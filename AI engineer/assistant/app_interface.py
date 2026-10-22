"""Mobile-app interface: the AI side's READ / WRITE contract. PROVISIONAL - NO NETWORK SERVICE - NOT AGREED WITH THE APP DEVELOPER.

No app or backend code exists in this repository and no protocol (HTTP, WebSocket, MQTT, BLE) has been agreed, so this module is
TRANSPORT-FREE: plain JSON-serialisable dicts in and out. A future network adapter (owner and protocol OPEN) calls these four methods
and nothing else; it must add transport security itself. Nothing here opens a socket.
(Conceptual operations: docs/handoff/MIDTERM_HARDWARE_VALIDATION.md section 14.)

READ   read_status()   facts from the ONE ClassroomStateStore (the same ClassroomState the voice answers use), with usability,
                       age and origin per value. A value is present ONLY when its source is FRESH; otherwise it is None and the
                       reason says why. Every value carries its origin; SIMULATED values say SIMULATED. No transcript, audio or video.
WRITE  request_light() validated CLASSROOM_LIGHTS TURN_ON / TURN_OFF only, for an AUTHORIZED caller only (the injected authorizer;
                       the default denies everyone), through intents.contracts.validate_command and the SAME action loop and response
                       policy the voice runtime uses. The app gets the execution result and correlation ids; it never assumes success.
       get_command_result()  the stored RESULT for one of the CALLER'S OWN correlation ids.
READ   assistant_state()     IDLE / LISTENING / CHECKING / SPEAKING and its colour semantic.

CALLER IDENTITY. The authorizer maps a token to a TRUSTED caller id (or None = denied); the caller never names itself. Correlation
ids are scoped per caller, so two callers can never collide or read each other's results.

REPLAY GUARANTEE (what holds, and what does not):
  * within ONE process, a (caller, correlation id) pair that was SUBMITTED to the controller executes AT MOST ONCE. A retry gets
    the stored result (replayed=True) while it is in the 64-result history; after that it is REFUSED as CORRELATION_ID_EXPIRED,
    never executed again. A request that was never submitted (BUSY, NOT_AVAILABLE, capability unavailable / unknown, known not
    delivered) is not recorded: retrying the same id later really runs. Each CALLER has its own bounded record of used ids; when
    one caller's record is full, only that caller's new requests are refused (fail closed) until VESTA restarts;
  * ACROSS A RESTART nothing is remembered (in memory only; no durable store, no device-side idempotency). Every response carries
    `server_session`; a client that sees it change must treat an unconfirmed earlier request as UNKNOWN and check the light state
    instead of retrying blindly. Exactly-once execution is NOT claimed.

ExecutionResult refuses origin REAL today (actions/contracts.py), so every confirmation this can return is SIMULATED.
"""

import hashlib
import hmac
import os
import re
import threading
from collections import OrderedDict
from typing import Callable, Optional

from actions.contracts import VALIDATION_LABEL, CapabilityState, RequestIdGenerator
from actions.loop import ActionLoop, ActionOutcome
from actions.response_policy import respond
from assistant.state import AssistantStateManager
from fusion.live_state import ClassroomStateStore
from intents.contracts import CommandAction, CommandTarget, IntentCategory, validate_command

APP_API_VERSION = "vesta-app-contract-v0"
APP_API_STATUS = "PROVISIONAL - TRANSPORT-FREE - NOT AGREED WITH THE MOBILE APP DEVELOPER"
APP_LIGHT_ACTIONS = ("TURN_ON", "TURN_OFF")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")          # correlation ids and caller ids
RESULT_HISTORY = 64        # full results kept for get_command_result / replay
USED_ID_LIMIT = 20_000     # per caller. ponytail: in-memory record of used ids; a durable store if sessions outgrow it
COUNT_NOTE = "detected persons inside the camera ROI in one frame; undercounts dense rooms; not a verified student count"


class SerializedActionLoop(ActionLoop):
    """The ONE action loop shared by voice and app: at most one execute() at a time.

    Voice (runtime) calls execute() and waits for an in-flight app command (bounded by the loop timeout). The app calls
    try_execute(), which never waits: a busy loop answers BUSY. ponytail: one global lock, a per-target lock if more targets appear.
    """

    def __init__(self, adapter, capabilities, *, clock, timeout_s) -> None:
        super().__init__(adapter, capabilities, clock=clock, timeout_s=timeout_s)
        self.adapter, self.capabilities = adapter, capabilities
        self._busy = threading.Lock()

    def execute(self, command) -> ActionOutcome:
        with self._busy:
            return super().execute(command)

    def try_execute(self, command) -> Optional[ActionOutcome]:
        if not self._busy.acquire(blocking=False):
            return None
        try:
            return super().execute(command)
        finally:
            self._busy.release()


def deny_all(_token: str) -> Optional[str]:
    return None


def env_token_authorizer(env_var: str = "VESTA_APP_TOKEN_SHA256", caller_id: str = "app-client") -> Callable[[str], Optional[str]]:
    """Map a token whose SHA-256 hex equals the value of `env_var` to `caller_id`. Unset or empty variable = deny everyone.
    One variable = one caller; give each phone its own variable and caller id. No secret lives in the code."""
    if not isinstance(caller_id, str) or not _ID.match(caller_id):
        raise ValueError("caller_id must be a short id")

    def authorize(token: str) -> Optional[str]:
        expected = os.environ.get(env_var, "").strip().lower()
        if not expected or not isinstance(token, str) or not token:
            return None
        return caller_id if hmac.compare_digest(hashlib.sha256(token.encode("utf-8")).hexdigest(), expected) else None
    return authorize


def _source(state) -> dict:
    return {"usability": state.usability.value, "origin": state.origin.value if state.origin else None,
            "age_s": state.age_s, "reason": state.reason}


def _env(ev) -> dict:
    return {"value": ev.value, "unit": ev.unit.value, "sensor_id": ev.sensor_id, **_source(ev.state)}


class AppInterface:
    def __init__(self, store: ClassroomStateStore, *, assistant_state: Optional[AssistantStateManager] = None,
                 action_loop: Optional[SerializedActionLoop] = None, authorize: Callable[[str], Optional[str]] = deny_all,
                 request_ids: Optional[RequestIdGenerator] = None) -> None:
        if not isinstance(store, ClassroomStateStore):
            raise TypeError("store must be the ClassroomStateStore the voice runtime also reads")
        if action_loop is not None and not isinstance(action_loop, SerializedActionLoop):
            raise TypeError("action_loop must be the shared SerializedActionLoop (or None = light control not available)")
        if not callable(authorize):
            raise TypeError("authorize must be callable")
        self._store, self._assistant_state, self._loop, self._authorize = store, assistant_state, action_loop, authorize
        self._ids = request_ids or RequestIdGenerator(prefix="app")
        self._last_light: Optional[dict] = None
        self._results: "OrderedDict[tuple, dict]" = OrderedDict()     # (caller, correlation id) -> RESULT (bounded)
        self._used: set = set()                                        # every SUBMITTED (caller, correlation id)
        self._used_count: dict = {}                                     # caller -> how many ids it has used (per-caller budget)
        self._write = threading.Lock()                                  # one app write at a time: the replay check cannot race

    @property
    def server_session(self) -> str:
        return self._ids.session_id

    def _caller(self, token) -> Optional[str]:
        """The TRUSTED caller id from the authorizer; anything that is not a valid id string means denied."""
        caller = self._authorize(token)
        return caller if isinstance(caller, str) and _ID.match(caller) else None

    # -- READ ------------------------------------------------------------------------------------

    def read_status(self) -> dict:
        s = self._store.snapshot()
        env, occ = s.environment, s.occupancy
        if self._loop is None:
            light = {"capability": "NOT_CONFIGURED", "adapter": "NOT_CONFIGURED", "last_result": None}
        else:
            caps = {a: self._loop.capabilities.state_of(CommandTarget.CLASSROOM_LIGHTS, CommandAction[a]).value for a in APP_LIGHT_ACTIONS}
            light = {"capability": caps, "adapter": "CONNECTED" if self._loop.adapter.is_connected() else "DISCONNECTED",
                     "adapter_origin": self._loop.adapter.origin.value,
                     "last_result": dict(self._last_light) if self._last_light else None}
        return {
            "api_version": APP_API_VERSION, "api_status": APP_API_STATUS, "server_session": self.server_session,
            "evaluated_at_monotonic_s": s.evaluated_at_monotonic_s, "aggregate_origin": s.aggregate_origin.value,
            "temperatures": [_env(t) for t in env.temperatures], "humidity": _env(env.humidity), "lux": _env(env.lux),
            "detected_person_count": {"value": occ.vision_count, "note": COUNT_NOTE, **_source(s.vision_state)},
            "presence": {"value": occ.sensor_present, **_source(s.presence_state)},
            "light": light,
            "assistant_state": self.assistant_state()["state"],
        }

    def assistant_state(self) -> dict:
        """IDLE / LISTENING / CHECKING / SPEAKING with its colour semantic, or NOT_CONFIGURED. No text, no audio."""
        if self._assistant_state is None:
            return {"state": "NOT_CONFIGURED", "color": None}
        return {"state": self._assistant_state.current_state.value, "color": self._assistant_state.current_color.value}

    def get_command_result(self, *, client_token: str, correlation_id: str) -> dict:
        """The stored RESULT of one of THIS caller's earlier requests. NOT_FOUND = never received, or aged out of the history."""
        cid = correlation_id if isinstance(correlation_id, str) else None
        caller = self._caller(client_token)
        if caller is None:
            return {"status": "UNAUTHORIZED", "correlation_id": cid, "server_session": self.server_session}
        stored = self._results.get((caller, cid))
        if stored is not None:
            return dict(stored)
        status = "EXPIRED" if (caller, cid) in self._used else "NOT_FOUND"
        return {"status": status, "correlation_id": cid, "server_session": self.server_session}

    # -- WRITE -----------------------------------------------------------------------------------

    def request_light(self, *, client_token: str, action: str, correlation_id: str) -> dict:
        """One light request. status: UNAUTHORIZED, INVALID_REQUEST, NOT_AVAILABLE, BUSY, or RESULT (then read result_state and confirmed).
        A (caller, correlation id) that already produced a RESULT is NEVER executed again (see REPLAY GUARANTEE in the module
        docstring). A request that was never submitted is not recorded and may be retried with the same id."""
        session = self.server_session
        if not isinstance(correlation_id, str) or not _ID.match(correlation_id):
            return {"status": "INVALID_REQUEST", "reason": "BAD_CORRELATION_ID", "correlation_id": None, "server_session": session}
        base = {"correlation_id": correlation_id, "server_session": session}
        caller = self._caller(client_token)
        if caller is None:
            return {"status": "UNAUTHORIZED", **base}
        if not self._write.acquire(blocking=False):
            return {"status": "BUSY", **base}
        try:
            return self._request_light((caller, correlation_id), action, base)
        finally:
            self._write.release()

    def _request_light(self, key, action, base) -> dict:
        if action not in APP_LIGHT_ACTIONS:           # an invalid action is reported as such, whatever the id was used for
            return {"status": "INVALID_REQUEST", "reason": "UNSUPPORTED_ACTION", **base}
        stored = self._results.get(key)
        if stored is not None:
            if stored["action"] != action:
                return {"status": "INVALID_REQUEST", "reason": "CORRELATION_ID_REUSED", **base}
            return {**stored, "replayed": True}
        if key in self._used:                         # executed earlier, aged out of the history: refuse, never re-run
            return {"status": "INVALID_REQUEST", "reason": "CORRELATION_ID_EXPIRED", **base}
        if self._used_count.get(key[0], 0) >= USED_ID_LIMIT:
            return {"status": "INVALID_REQUEST", "reason": "REPLAY_RECORD_FULL", **base}
        checked = validate_command({"intent_category": IntentCategory.CLASSROOM_CONTROL.name, "action": action,
                                    "target": CommandTarget.CLASSROOM_LIGHTS.name, "request_id": self._ids.next_id()})
        if not checked.valid:
            return {"status": "INVALID_REQUEST", "reason": checked.status.value, **base}
        command = checked.command
        base["request_id"] = command.request_id
        if self._loop is None:
            return {"status": "NOT_AVAILABLE", "reason": "NO_ACTION_ADAPTER_CONFIGURED", **base}
        outcome = self._loop.try_execute(command)
        if outcome is None:
            return {"status": "BUSY", **base}
        decision = respond(outcome)
        r = outcome.result
        result = {"status": "RESULT", **base, "action": action, "capability": outcome.capability.value, "submitted": outcome.submitted,
                  "result_state": r.state.value if r else None, "reason": r.reason if r else None, "origin": r.origin.value if r else None,
                  "confirmed": decision.confirms_simulated_completion, "message": decision.text, "validation_label": VALIDATION_LABEL}
        if outcome.capability is CapabilityState.SUPPORTED_AND_AVAILABLE:
            self._last_light = {k: result[k] for k in ("request_id", "action", "result_state", "origin", "confirmed", "validation_label")}
        if not outcome.submitted:                    # nothing reached the controller: not recorded, the same id may be retried
            return dict(result)
        self._used.add(key)
        self._used_count[key[0]] = self._used_count.get(key[0], 0) + 1
        self._results[key] = result
        while len(self._results) > RESULT_HISTORY:
            self._results.popitem(last=False)
        return dict(result)
