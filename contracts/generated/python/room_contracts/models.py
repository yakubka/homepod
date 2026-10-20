# Generated from contracts/schemas, contract 1.0.1. Do not edit.
# Regenerate with: python contracts/generate.py

from __future__ import annotations

from enum import Enum, IntEnum
from typing import Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    confloat,
    conint,
    constr,
)



class AnnouncementCategory(Enum):
    """
    Announcement category.
    """

    info = "info"
    reminder = "reminder"
    schedule = "schedule"
    emergency = "emergency"


class Announcement(BaseModel):
    """
    A prepared announcement the voice stack can speak through the room speaker. The set is managed by the API, the app only lists and plays them in this sprint.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    id: constr(pattern=r"^ann_[a-z0-9]{3,32}$") = Field(
        ..., description="Announcement identifier."
    )
    title: constr(min_length=1, max_length=80) = Field(
        ..., description="Short title shown in the app, 1 to 80 characters."
    )
    text: constr(min_length=1, max_length=600) = Field(
        ..., description="Text that is spoken, 1 to 600 characters."
    )
    category: AnnouncementCategory = Field(
        ..., description="Category, used for grouping and icon."
    )
    language: constr(pattern=r"^[a-z]{2}(-[A-Z]{2})?$") = Field(
        ..., description="Spoken language as a BCP 47 tag, for example en-US or uz-UZ."
    )
    duration_s: conint(ge=1, le=120) = Field(
        ..., description="Expected playback length, seconds, integer 1 to 120."
    )
    last_played_at: AwareDatetime | None = Field(
        ..., description="Last playback start, ISO 8601 UTC, null when never played."
    )


class AnnouncementPlayback(BaseModel):
    """
    A started announcement playback, returned by POST /announcements/{id}/play. Only one playback runs at a time.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    playback_id: constr(pattern=r"^pb_[a-z0-9]{6,32}$") = Field(
        ..., description="Playback identifier."
    )
    announcement_id: constr(pattern=r"^ann_[a-z0-9]{3,32}$") = Field(
        ..., description="Announcement being played."
    )
    decision_id: constr(pattern=r"^dec_[a-z0-9]{6,32}$") = Field(
        ..., description="Engine decision that started the playback."
    )
    started_at: AwareDatetime = Field(..., description="Playback start, ISO 8601 UTC.")
    expected_end_at: AwareDatetime = Field(
        ..., description="started_at plus the announcement duration, ISO 8601 UTC."
    )
    volume: conint(ge=0, le=100) = Field(
        ..., description="Effective volume, percent 0 to 100."
    )


class ClimateTargetRequest(BaseModel):
    """
    Request body of POST /climate/target. Sets the target and switches the climate mode to auto.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    target_c: confloat(ge=16.0, le=30.0) = Field(
        ...,
        description="Target temperature, degrees Celsius, one decimal place, 16.0 to 30.0.",
    )


class Zone(Enum):
    """
    Room zone. Canonical order is left, center, right, professor. Left and right are as seen by the audience facing the board.
    """

    left = "left"
    center = "center"
    right = "right"
    professor = "professor"


class LightingMode(Enum):
    """
    Lighting mode. auto follows occupancy and daylight, lecture and presentation apply the presets from Settings, manual holds per-zone values set by a person, off forces every zone to 0.
    """

    auto = "auto"
    lecture = "lecture"
    presentation = "presentation"
    manual = "manual"
    off = "off"


class ClimateMode(Enum):
    """
    Climate mode. auto drives the fan toward the target temperature, manual holds the last fan speed, off stops the fan.
    """

    auto = "auto"
    manual = "manual"
    off = "off"


class RingPattern(Enum):
    """
    LED ring animation. idle is a dim breathe, listening follows the wake word, thinking spins while the request is processed, speaking pulses during a spoken reply, success and error flash once and fall back to idle.
    """

    off = "off"
    idle = "idle"
    listening = "listening"
    thinking = "thinking"
    speaking = "speaking"
    success = "success"
    error = "error"


class ComponentStatus(Enum):
    """
    Health of a component or of the whole system. ok works as specified, degraded works with reduced function or stale data, down does not work.
    """

    ok = "ok"
    degraded = "degraded"
    down = "down"


class Component(Enum):
    """
    Process on the Pi that publishes to the event bus. bridge owns the serial link to the ESP32, recorder writes history.
    """

    api = "api"
    engine = "engine"
    voice = "voice"
    vision = "vision"
    bridge = "bridge"
    recorder = "recorder"


class BusTopic(Enum):
    """
    Logical event bus topic. The full list with publishers, subscribers and rates is in bus.md.
    """

    sensor_environment = "sensor.environment"
    sensor_presence = "sensor.presence"
    perception_occupancy = "perception.occupancy"
    voice_wake = "voice.wake"
    voice_transcript = "voice.transcript"
    voice_intent = "voice.intent"
    control_request = "control.request"
    decision_action = "decision.action"
    device_command = "device.command"
    device_ack = "device.ack"
    device_status = "device.status"
    schedule_lecture = "schedule.lecture"
    settings_changed = "settings.changed"
    system_heartbeat = "system.heartbeat"
    system_state = "system.state"


class ZoneLevels(BaseModel):
    """
    One brightness value per zone, in percent 0 to 100.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    left: conint(ge=0, le=100) = Field(
        ..., description="Left zone brightness, percent 0 to 100."
    )
    center: conint(ge=0, le=100) = Field(
        ..., description="Center zone brightness, percent 0 to 100."
    )
    right: conint(ge=0, le=100) = Field(
        ..., description="Right zone brightness, percent 0 to 100."
    )
    professor: conint(ge=0, le=100) = Field(
        ..., description="Professor zone brightness, percent 0 to 100."
    )


class ActionType(Enum):
    """
    What the engine does as the result of a decision. lighting.set_zone with a null zone applies to every zone. answer only produces reply_text. none means nothing was done.
    """

    lighting_set_mode = "lighting.set_mode"
    lighting_set_zone = "lighting.set_zone"
    climate_set_target = "climate.set_target"
    climate_set_fan = "climate.set_fan"
    presentation_start = "presentation.start"
    presentation_stop = "presentation.stop"
    announcement_play = "announcement.play"
    ring_set = "ring.set"
    answer = "answer"
    none = "none"


class ActionParams(BaseModel):
    """
    Parameters of an Action. Which keys are required depends on the action type, see the table in bus.md under control.request.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    mode: LightingMode | None = Field(
        None, description="Lighting mode for lighting.set_mode."
    )
    brightness: conint(ge=0, le=100) | None = Field(
        None, description="Brightness for lighting.set_zone, percent 0 to 100."
    )
    fade_ms: conint(ge=0, le=5000) | None = Field(
        None, description="Fade for lighting.set_zone, milliseconds 0 to 5000."
    )
    target_c: confloat(ge=16.0, le=30.0) | None = Field(
        None, description="Target for climate.set_target, degrees Celsius 16.0 to 30.0."
    )
    speed: conint(ge=0, le=100) | None = Field(
        None, description="Fan speed for climate.set_fan, percent 0 to 100."
    )
    announcement_id: constr(pattern=r"^ann_[a-z0-9]{3,32}$") | None = Field(
        None, description="Announcement for announcement.play."
    )
    volume: conint(ge=0, le=100) | None = Field(
        None, description="Volume for announcement.play, percent 0 to 100."
    )
    pattern: RingPattern | None = Field(None, description="Ring pattern for ring.set.")
    lecture_id: constr(pattern=r"^lec_[a-z0-9]{6,32}$") | None = Field(
        None, description="Lecture for presentation.start."
    )


class Action(BaseModel):
    """
    A single change the engine is asked to make or has decided to make.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    type: ActionType = Field(..., description="Action type.")
    zone: Zone | None = Field(
        ...,
        description="Zone the action applies to, null when it applies to the whole room.",
    )
    params: ActionParams = Field(
        ..., description="Action parameters, an empty object when the action has none."
    )


class ControlRequest(BaseModel):
    """
    A state change requested through the HTTP API, published by the API on control.request. The engine answers with a decision.action carrying the same request_id. The API fills every default, for example fade_ms and volume, before publishing.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    request_id: constr(pattern=r"^req_[a-z0-9]{6,32}$") = Field(
        ..., description="Correlation id created by the API for this HTTP request."
    )
    ts: AwareDatetime = Field(
        ..., description="Receipt time of the HTTP request, ISO 8601 UTC."
    )
    action: Action = Field(..., description="Requested change.")


class DecisionTriggerType(Enum):
    """
    Kind of trigger. presence, occupancy and environment come from sensor topics, voice from voice.intent, api from control.request, schedule from schedule.lecture, timer from an engine timer such as the vacancy timeout, device from device.ack or a reconnect.
    """

    presence = "presence"
    occupancy = "occupancy"
    environment = "environment"
    voice = "voice"
    api = "api"
    schedule = "schedule"
    timer = "timer"
    device = "device"


class DecisionTrigger(BaseModel):
    """
    Cause of a decision.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    type: DecisionTriggerType = Field(..., description="Kind of trigger.")
    topic: BusTopic | None = Field(
        ..., description="Bus topic of the triggering event, null for timer triggers."
    )
    event_id: constr(pattern=r"^evt_[a-z0-9]{6,32}$") | None = Field(
        ...,
        description="Envelope id of the triggering bus event, null for timer triggers.",
    )


class DecisionOutcome(Enum):
    """
    issued means commands were sent, answered means a query was answered without commands, skipped means the rule fired but the room was already in the wanted state, rejected means the request could not be carried out, see summary.
    """

    issued = "issued"
    answered = "answered"
    skipped = "skipped"
    rejected = "rejected"


class CommandStatus(Enum):
    """
    Delivery status of a device command. pending until the matching device.ack arrives.
    """

    pending = "pending"
    applied = "applied"
    rejected = "rejected"
    timeout = "timeout"


class AckStatus(Enum):
    """
    applied means the command was accepted and is being executed, a fade may still be running. rejected means it was not executed, see reason. timeout is synthetic, produced by the bridge only.
    """

    applied = "applied"
    rejected = "rejected"
    timeout = "timeout"


class AckReason(Enum):
    """
    invalid_field means a field is missing or has the wrong type, out_of_range means a value is outside its range, unknown_type means the message type is not known, unsupported means the hardware is not fitted, busy means the device or bridge queue is full, hardware_fault means the actuator failed. no_response and link_down are bridge only, for a command that timed out and for one that could not be sent because the link is down.
    """

    invalid_field = "invalid_field"
    out_of_range = "out_of_range"
    unknown_type = "unknown_type"
    unsupported = "unsupported"
    busy = "busy"
    hardware_fault = "hardware_fault"
    no_response = "no_response"
    link_down = "link_down"


class ActuatorState(BaseModel):
    """
    Actuator targets held by the device.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    lights: ZoneLevels = Field(
        ..., description="Target brightness per zone, percent 0 to 100."
    )
    fan: conint(ge=0, le=100) = Field(..., description="Fan speed, percent 0 to 100.")
    ring: RingPattern = Field(..., description="Current ring pattern.")


class DeviceAck(BaseModel):
    """
    Acknowledgement of one downlink command. Sent by the ESP32 with status applied or rejected within 200 ms of receiving the line. The bridge republishes it unchanged on device.ack. The bridge also publishes synthetic acks, recognisable by the missing seq and a null state: status timeout with reason no_response when no ack arrived after the last retry, and status rejected with reason busy or link_down when the command never reached the wire. The device never sends timeout, link_down or no_response.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    type: Literal["ack"] = Field(..., description="Message type.")
    seq: conint(ge=1, le=4294967295) | None = Field(
        None,
        description="Uplink frame counter, present on every ack sent by the device, absent on a synthetic ack.",
    )
    ts: AwareDatetime = Field(
        ...,
        description="Device time when the ack was sent, or bridge time for a synthetic ack, ISO 8601 UTC.",
    )
    cmd_id: constr(pattern=r"^[A-Za-z0-9_-]{1,16}$") = Field(
        ..., description="cmd_id of the acknowledged command, unchanged."
    )
    status: AckStatus = Field(..., description="Result.")
    reason: AckReason | None = Field(
        ..., description="Why the command was not applied, null when status is applied."
    )
    state: ActuatorState | None = Field(
        ...,
        description="Actuator state after handling the command, null on a synthetic ack.",
    )


class DeviceCommandType(Enum):
    """
    Downlink message type.
    """

    set_zone_light = "set_zone_light"
    set_ring = "set_ring"
    set_fan = "set_fan"
    query = "query"
    ping = "ping"


class QueryWhat(Enum):
    """
    sensors asks for an immediate sensor_report, state asks for the actuator state in the ack, info asks for a new hello.
    """

    sensors = "sensors"
    state = "state"
    info = "info"


class SetZoneLight(BaseModel):
    """
    Fade one zone to a brightness.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    type: Literal["set_zone_light"] = Field(..., description="Message type.")
    seq: conint(ge=1, le=4294967295) | None = Field(
        None, description="Frame counter, stamped by the bridge, mandatory on the wire."
    )
    ts: AwareDatetime = Field(
        ..., description="Send time, ISO 8601 UTC. The device sets its clock from it."
    )
    cmd_id: constr(pattern=r"^[A-Za-z0-9_-]{1,16}$") = Field(
        ..., description="Command id echoed by the ack."
    )
    zone: Zone = Field(..., description="Zone.")
    brightness: conint(ge=0, le=100) = Field(
        ..., description="Target brightness, percent 0 to 100."
    )
    fade_ms: conint(ge=0, le=5000) = Field(
        ..., description="Fade duration, milliseconds 0 to 5000."
    )


class SetRing(BaseModel):
    """
    Set the LED ring animation used as voice feedback.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    type: Literal["set_ring"] = Field(..., description="Message type.")
    seq: conint(ge=1, le=4294967295) | None = Field(
        None, description="Frame counter, stamped by the bridge, mandatory on the wire."
    )
    ts: AwareDatetime = Field(..., description="Send time, ISO 8601 UTC.")
    cmd_id: constr(pattern=r"^[A-Za-z0-9_-]{1,16}$") = Field(
        ..., description="Command id echoed by the ack."
    )
    pattern: RingPattern = Field(..., description="Animation.")
    color: constr(pattern=r"^#[0-9A-F]{6}$") | None = Field(
        ...,
        description="Colour as #RRGGBB uppercase hex, null for the pattern default colour.",
    )
    brightness: conint(ge=0, le=100) = Field(
        ..., description="Ring brightness, percent 0 to 100."
    )


class SetFan(BaseModel):
    """
    Set the fan speed.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    type: Literal["set_fan"] = Field(..., description="Message type.")
    seq: conint(ge=1, le=4294967295) | None = Field(
        None, description="Frame counter, stamped by the bridge, mandatory on the wire."
    )
    ts: AwareDatetime = Field(..., description="Send time, ISO 8601 UTC.")
    cmd_id: constr(pattern=r"^[A-Za-z0-9_-]{1,16}$") = Field(
        ..., description="Command id echoed by the ack."
    )
    speed: conint(ge=0, le=100) = Field(..., description="Fan speed, percent 0 to 100.")


class Query(BaseModel):
    """
    Ask the device for data. The ack always comes first, a requested sensor_report or hello follows it.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    type: Literal["query"] = Field(..., description="Message type.")
    seq: conint(ge=1, le=4294967295) | None = Field(
        None, description="Frame counter, stamped by the bridge, mandatory on the wire."
    )
    ts: AwareDatetime = Field(..., description="Send time, ISO 8601 UTC.")
    cmd_id: constr(pattern=r"^[A-Za-z0-9_-]{1,16}$") = Field(
        ..., description="Command id echoed by the ack."
    )
    what: QueryWhat = Field(..., description="What to report.")


class Ping(BaseModel):
    """
    Heartbeat and clock sync, sent by the bridge every 5 seconds while the link is idle.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    type: Literal["ping"] = Field(..., description="Message type.")
    seq: conint(ge=1, le=4294967295) | None = Field(
        None, description="Frame counter, stamped by the bridge, mandatory on the wire."
    )
    ts: AwareDatetime = Field(
        ..., description="Send time, ISO 8601 UTC. The device sets its clock from it."
    )
    cmd_id: constr(pattern=r"^[A-Za-z0-9_-]{1,16}$") = Field(
        ..., description="Command id echoed by the ack."
    )


class DeviceCommand(RootModel[SetZoneLight | SetRing | SetFan | Query | Ping]):
    root: SetZoneLight | SetRing | SetFan | Query | Ping = Field(
        ...,
        description="Downlink message from the Pi to the ESP32, one JSON object per line, see protocol.md. The engine publishes it on device.command without seq. The bridge stamps seq and overwrites ts with the write time immediately before the line goes on the wire, where seq is mandatory. Every field is explicit on the wire, there are no device side defaults.",
        title="DeviceCommand",
    )


class DeviceErrorCode(Enum):
    """
    parse_error means the line is not valid JSON, line_too_long means it exceeded 512 bytes, sensor_fault and actuator_fault mean hardware stopped responding, queue_full means a line was dropped, internal is anything else.
    """

    parse_error = "parse_error"
    line_too_long = "line_too_long"
    sensor_fault = "sensor_fault"
    actuator_fault = "actuator_fault"
    queue_full = "queue_full"
    internal = "internal"


class DeviceError(BaseModel):
    """
    Unsolicited uplink error. Used for faults that cannot be reported in an ack, such as a line that is not valid JSON or a sensor that stopped responding. A command that parsed but cannot be executed is answered with an ack with status rejected instead.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    type: Literal["error"] = Field(..., description="Message type.")
    seq: conint(ge=1, le=4294967295) = Field(..., description="Uplink frame counter.")
    ts: AwareDatetime = Field(..., description="Device time, ISO 8601 UTC.")
    code: DeviceErrorCode = Field(..., description="Error code.")
    message: constr(max_length=120) = Field(
        ..., description="Short English description, at most 120 characters."
    )
    ref_seq: conint(ge=1, le=4294967295) | None = Field(
        ..., description="Downlink seq the error refers to, null when unknown."
    )
    cmd_id: constr(pattern=r"^[A-Za-z0-9_-]{1,16}$") | None = Field(
        ..., description="Downlink cmd_id the error refers to, null when unknown."
    )


class ResetReason(Enum):
    """
    ESP32 reset cause.
    """

    power_on = "power_on"
    software = "software"
    watchdog = "watchdog"
    brownout = "brownout"
    panic = "panic"
    unknown = "unknown"


class SensorKind(Enum):
    """
    Environment sensor kind.
    """

    temperature = "temperature"
    humidity = "humidity"
    lux = "lux"


class DeviceCapabilities(BaseModel):
    """
    Hardware fitted to this device.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    light_zones: list[Zone] = Field(
        ..., description="Zones with a dimmable light channel.", max_length=4
    )
    presence_zones: list[Zone] = Field(
        ..., description="Zones with a PIR sensor.", max_length=4
    )
    fan: bool = Field(..., description="Fan channel fitted.")
    ring: bool = Field(..., description="LED ring fitted.")
    sensors: list[SensorKind] = Field(
        ..., description="Environment sensors fitted.", max_length=3
    )


class DeviceHello(BaseModel):
    """
    First uplink message after boot or after the serial port is opened, and the answer to a query for info. The bridge refuses to send commands to a device whose protocol_version major differs from the contract major.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    type: Literal["hello"] = Field(..., description="Message type.")
    seq: conint(ge=1, le=4294967295) = Field(
        ..., description="Uplink frame counter, 1 after boot."
    )
    ts: AwareDatetime = Field(
        ...,
        description="Device time, ISO 8601 UTC, epoch based until the clock is synced.",
    )
    device_id: constr(pattern=r"^esp32-[0-9a-f]{6}$") = Field(
        ..., description="Device identifier."
    )
    fw_version: constr(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$") = Field(
        ..., description="Firmware version."
    )
    protocol_version: constr(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$") = Field(
        ...,
        description="Contract version the firmware was built against, from contracts/VERSION.",
    )
    clock_synced: bool = Field(
        ..., description="False until the device has received a downlink ts since boot."
    )
    uptime_ms: conint(ge=0, le=9007199254740991) = Field(
        ..., description="Milliseconds since device boot."
    )
    reset_reason: ResetReason = Field(..., description="Cause of the last reset.")
    report_interval_ms: conint(ge=500, le=60000) = Field(
        ...,
        description="Period of interval sensor reports, milliseconds, integer 500 to 60000, 2000 by default.",
    )
    capabilities: DeviceCapabilities = Field(..., description="Fitted hardware.")


class LinkReason(Enum):
    """
    no_hello means frames arrive but no hello yet, ack_timeouts means 2 or more consecutive commands timed out, silent means no uplink frame for 15 seconds, port_closed means the serial port is missing or failed, protocol_mismatch means the device protocol major differs from the contract major.
    """

    no_hello = "no_hello"
    ack_timeouts = "ack_timeouts"
    silent = "silent"
    port_closed = "port_closed"
    protocol_mismatch = "protocol_mismatch"


class LinkError(BaseModel):
    """
    Last error frame received from the device.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    code: DeviceErrorCode = Field(..., description="Error code from the device.")
    message: constr(max_length=120) = Field(
        ..., description="Error message from the device."
    )
    received_at: AwareDatetime = Field(
        ..., description="Receipt time on the Pi clock, ISO 8601 UTC."
    )


class DeviceLink(BaseModel):
    """
    State of the serial link to the ESP32 as seen by the bridge. Published on device.status, retained, on every change and every 5 seconds, and returned inside GET /sensors. ok means frames and acks flow, degraded means acks are timing out, down means no frame for 15 seconds, the port is closed or the protocol major differs.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    device_id: constr(pattern=r"^esp32-[0-9a-f]{6}$") | None = Field(
        ...,
        description="Device identifier from the last hello, null before the first hello.",
    )
    status: ComponentStatus = Field(..., description="Link status.")
    reason: LinkReason | None = Field(
        ..., description="Why the link is not ok, null when ok."
    )
    fw_version: constr(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$") | None = Field(
        ...,
        description="Firmware version from the last hello, null before the first hello.",
    )
    protocol_version: constr(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$") | None = Field(
        ...,
        description="Protocol version from the last hello, null before the first hello.",
    )
    uptime_ms: conint(ge=0, le=9007199254740991) | None = Field(
        ...,
        description="Device uptime from the latest hello or sensor_report, milliseconds, null before the first frame. A decrease means the device rebooted.",
    )
    last_seen_at: AwareDatetime | None = Field(
        ...,
        description="Receipt time of the last uplink frame on the Pi clock, ISO 8601 UTC, null when none since bridge start.",
    )
    last_error: LinkError | None = Field(
        ...,
        description="Most recent error message from the device, null when none since the last hello.",
    )


class EnvironmentReading(BaseModel):
    """
    One environment sample from the ESP32, normalised by the bridge from a sensor_report and published on sensor.environment. A value is null when that sensor failed in this cycle.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    ts: AwareDatetime = Field(
        ..., description="Sample time, ISO 8601 UTC, as stamped by the Pi on receipt."
    )
    device_id: constr(pattern=r"^esp32-[0-9a-f]{6}$") = Field(
        ..., description="Reporting device."
    )
    temperature_c: confloat(ge=-40.0, le=85.0) | None = Field(
        ...,
        description="Air temperature, degrees Celsius, one decimal place, -40.0 to 85.0, null on sensor fault.",
    )
    humidity_pct: conint(ge=0, le=100) | None = Field(
        ...,
        description="Relative humidity, percent, integer 0 to 100, null on sensor fault.",
    )
    lux: conint(ge=0, le=100000) | None = Field(
        ...,
        description="Ambient illuminance at the light sensor, lux, integer 0 to 100000, null on sensor fault.",
    )


class ErrorCode(Enum):
    """
    Fixed set of failure codes. HTTP status per code: invalid_input 400, unauthorized 401, not_found 404, conflict 409, internal 500, device_unavailable 503, degraded 503, device_timeout 504. Clients branch on the code, never on the message.
    """

    invalid_input = "invalid_input"
    unauthorized = "unauthorized"
    not_found = "not_found"
    conflict = "conflict"
    device_unavailable = "device_unavailable"
    device_timeout = "device_timeout"
    degraded = "degraded"
    internal = "internal"


class ApiError(BaseModel):
    """
    Failure details.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    code: ErrorCode = Field(..., description="Machine readable failure code.")
    message: constr(min_length=1, max_length=300) = Field(
        ...,
        description="One English sentence for logs and toasts. Never parsed by clients.",
    )
    detail: dict[str, Any] = Field(
        ...,
        description="Machine readable context, an empty object when there is none. Conventional keys: invalid_input uses field and reason, not_found uses resource and id, conflict uses resource and conflicting_id, device_unavailable uses device, reason and last_seen_at, device_timeout uses device, cmd_id and attempts, degraded uses components.",
    )


class ErrorResponse(BaseModel):
    """
    The only failure shape in the contract. Every non-2xx HTTP response body is an ErrorResponse. No endpoint defines its own error shape.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    ok: Literal[False] = Field(..., description="Always false on failure.")
    error: ApiError = Field(..., description="What went wrong.")


class HealthComponent(Enum):
    """
    Component reported in health. esp32 is the microcontroller as seen through the bridge, database is the API store.
    """

    api = "api"
    bus = "bus"
    engine = "engine"
    bridge = "bridge"
    esp32 = "esp32"
    vision = "vision"
    voice = "voice"
    recorder = "recorder"
    database = "database"


class ComponentHealth(BaseModel):
    """
    Health of one component.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    name: HealthComponent = Field(..., description="Component.")
    status: ComponentStatus = Field(..., description="Component status.")
    last_seen_at: AwareDatetime | None = Field(
        ...,
        description="Last bus message or heartbeat from the component, ISO 8601 UTC, null when never seen.",
    )
    detail: constr(max_length=200) | None = Field(
        ..., description="Short explanation when not ok, otherwise null."
    )


class HealthStatus(BaseModel):
    """
    Liveness and readiness of the Pi services, returned by GET /health. status is the worst status of the critical components api, bus, engine and bridge.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    status: ComponentStatus = Field(..., description="Overall status.")
    ts: AwareDatetime = Field(
        ...,
        description="Server time, ISO 8601 UTC. Clients may use it to detect clock skew.",
    )
    service_version: constr(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$") = Field(
        ..., description="API service version."
    )
    contract_version: constr(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$") = Field(
        ..., description="Contract version the API was built against."
    )
    uptime_s: conint(ge=0) = Field(..., description="Seconds since API start.")
    components: list[ComponentHealth] = Field(
        ..., description="One entry per component, at most one per name.", min_length=1
    )


class Heartbeat(BaseModel):
    """
    Liveness signal every Pi component publishes on system.heartbeat every 5 seconds. A component without a heartbeat for 15 seconds is reported down by GET /health. component must equal the envelope source.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    component: Component = Field(..., description="Publishing component.")
    status: ComponentStatus = Field(..., description="Self-reported status.")
    version: constr(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$") = Field(
        ..., description="Component version."
    )
    uptime_s: conint(ge=0) = Field(
        ..., description="Seconds since the component started."
    )
    detail: constr(max_length=200) | None = Field(
        ..., description="Short explanation when not ok, otherwise null."
    )


class HistoryMetric(Enum):
    """
    temperature_c in celsius, humidity_pct in percent, lux in lux, occupancy in people.
    """

    temperature_c = "temperature_c"
    humidity_pct = "humidity_pct"
    lux = "lux"
    occupancy = "occupancy"


class HistoryUnit(Enum):
    """
    Unit of a history value.
    """

    celsius = "celsius"
    percent = "percent"
    lux = "lux"
    people = "people"


class HistoryBucket(IntEnum):
    """
    Allowed bucket widths in seconds.
    """

    integer_60 = 60
    integer_300 = 300
    integer_900 = 900
    integer_3600 = 3600


class HistoryPoint(BaseModel):
    """
    One bucket.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    ts: AwareDatetime = Field(..., description="Bucket start, ISO 8601 UTC.")
    value: float | None = Field(
        ...,
        description="Bucket mean in the series unit, rounded to one decimal place, null when the bucket is empty.",
    )


class HistorySeries(BaseModel):
    """
    Time series of one metric, bucketed by the recorder and returned by GET /history. Each point is the mean over its bucket, ts is the bucket start. A bucket without samples has value null. Points are in ascending ts order with no gaps.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    metric: HistoryMetric = Field(..., description="Metric.")
    unit: HistoryUnit = Field(..., description="Unit of every value.")
    zone: Zone | None = Field(
        ...,
        description="Zone for occupancy, null for the whole room and for other metrics.",
    )
    from_: AwareDatetime = Field(
        ...,
        alias="from",
        description="Inclusive start, ISO 8601 UTC, aligned to bucket_s.",
    )
    to: AwareDatetime = Field(
        ..., description="Exclusive end, ISO 8601 UTC, aligned to bucket_s."
    )
    bucket_s: HistoryBucket = Field(..., description="Bucket width, seconds.")
    points: list[HistoryPoint] = Field(
        ..., description="Data points, at most 1440.", max_length=1440
    )


class LectureStatus(Enum):
    """
    scheduled before starts_at, in_progress between starts_at and ends_at, finished after ends_at, cancelled when cancelled by a person. Finished and cancelled lectures are read-only.
    """

    scheduled = "scheduled"
    in_progress = "in_progress"
    finished = "finished"
    cancelled = "cancelled"


class LectureLightingMode(Enum):
    """
    Lighting mode a lecture starts in.
    """

    lecture = "lecture"
    presentation = "presentation"


class Lecture(BaseModel):
    """
    A scheduled lecture in the room. Owned and stored by the API. status is derived by the API from the clock and is read-only. ends_at is strictly after starts_at and two lectures that are not cancelled never overlap.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    id: constr(pattern=r"^lec_[a-z0-9]{6,32}$") = Field(
        ..., description="Lecture identifier."
    )
    title: constr(min_length=1, max_length=120) = Field(
        ..., description="Lecture title, 1 to 120 characters."
    )
    course_code: constr(max_length=20) | None = Field(
        ..., description="Course code such as CS301, null when not set."
    )
    lecturer: constr(min_length=1, max_length=80) = Field(
        ..., description="Lecturer display name, 1 to 80 characters."
    )
    starts_at: AwareDatetime = Field(..., description="Start, ISO 8601 UTC.")
    ends_at: AwareDatetime = Field(
        ..., description="End, ISO 8601 UTC, strictly after starts_at."
    )
    status: LectureStatus = Field(
        ..., description="Lifecycle status, derived by the API."
    )
    lighting_mode: LectureLightingMode = Field(
        ..., description="Lighting mode the engine applies when the lecture starts."
    )
    notes: constr(max_length=500) | None = Field(
        ..., description="Free text, at most 500 characters, null when empty."
    )
    created_at: AwareDatetime = Field(..., description="Creation time, ISO 8601 UTC.")
    updated_at: AwareDatetime = Field(
        ..., description="Last modification time, ISO 8601 UTC."
    )


class LectureCreate(BaseModel):
    """
    Request body of POST /lectures. The API assigns id, status, created_at and updated_at. Omitted optional fields take the defaults stated below.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    title: constr(min_length=1, max_length=120) = Field(
        ..., description="Lecture title, 1 to 120 characters."
    )
    course_code: constr(max_length=20) | None = Field(
        None, description="Course code, default null."
    )
    lecturer: constr(min_length=1, max_length=80) = Field(
        ..., description="Lecturer display name, 1 to 80 characters."
    )
    starts_at: AwareDatetime = Field(..., description="Start, ISO 8601 UTC.")
    ends_at: AwareDatetime = Field(
        ...,
        description="End, ISO 8601 UTC, strictly after starts_at, at most 6 hours after it.",
    )
    lighting_mode: LectureLightingMode | None = Field(
        None, description="Lighting mode applied at start, default lecture."
    )
    notes: constr(max_length=500) | None = Field(
        None, description="Free text, at most 500 characters, default null."
    )


class LectureEventType(Enum):
    """
    Schedule event type.
    """

    created = "created"
    updated = "updated"
    deleted = "deleted"
    started = "started"
    ended = "ended"


class LectureEvent(BaseModel):
    """
    Change in the lecture schedule, published by the API on schedule.lecture. created, updated and deleted follow the HTTP calls. started and ended are emitted by the API scheduler within 1 second of starts_at and ends_at. The engine applies lighting_mode and the default climate target on started.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    event: LectureEventType = Field(..., description="What happened.")
    ts: AwareDatetime = Field(..., description="Event time, ISO 8601 UTC.")
    lecture: Lecture = Field(
        ..., description="Lecture after the change, or the last state for deleted."
    )


class LecturePatchStatus(Enum):
    """
    Status a client may set on a lecture.
    """

    cancelled = "cancelled"


class LecturePatch(BaseModel):
    """
    Request body of PATCH /lectures/{id}. Merge semantics, only the keys present are changed, null is never sent. An empty string clears course_code or notes. Setting status to cancelled cancels the lecture, no other status value is accepted. Finished and cancelled lectures cannot be patched.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    title: constr(min_length=1, max_length=120) | None = Field(
        None, description="Lecture title, 1 to 120 characters."
    )
    course_code: constr(max_length=20) | None = Field(
        None, description="Course code, an empty string clears it."
    )
    lecturer: constr(min_length=1, max_length=80) | None = Field(
        None, description="Lecturer display name, 1 to 80 characters."
    )
    starts_at: AwareDatetime | None = Field(None, description="Start, ISO 8601 UTC.")
    ends_at: AwareDatetime | None = Field(
        None, description="End, ISO 8601 UTC, strictly after the resulting starts_at."
    )
    lighting_mode: LectureLightingMode | None = Field(
        None, description="Lighting mode applied at start."
    )
    notes: constr(max_length=500) | None = Field(
        None, description="Free text, an empty string clears it."
    )
    status: LecturePatchStatus | None = Field(
        None, description="Only cancelled is accepted."
    )


class SelectableLightingMode(Enum):
    """
    LightingMode values a client may select directly, every mode except manual.
    """

    auto = "auto"
    lecture = "lecture"
    presentation = "presentation"
    off = "off"


class LightingModeRequest(BaseModel):
    """
    Request body of POST /lighting/mode. manual is not accepted here, it is entered by POST /lighting/zone.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    mode: SelectableLightingMode = Field(..., description="Mode to switch to.")
    fade_ms: conint(ge=0, le=5000) | None = Field(
        None, description="Fade for every zone, milliseconds 0 to 5000, default 800."
    )


class ZoneLight(BaseModel):
    """
    Light of one zone.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    zone: Zone = Field(..., description="Zone.")
    brightness: conint(ge=0, le=100) = Field(
        ..., description="Commanded brightness, percent 0 to 100."
    )
    confirmed: bool = Field(
        ...,
        description="True when the device acknowledged this brightness, false while pending or after an ack timeout.",
    )
    updated_at: AwareDatetime = Field(
        ..., description="Time brightness was last commanded, ISO 8601 UTC."
    )


class LightingState(BaseModel):
    """
    Lighting as known by the engine. brightness is the last value the engine commanded. confirmed is true once the ESP32 acknowledged that value. zones holds exactly one entry per zone in canonical order.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    mode: LightingMode = Field(..., description="Active lighting mode.")
    zones: list[ZoneLight] = Field(
        ...,
        description="Per-zone lighting, one entry per zone, order left, center, right, professor.",
        max_length=4,
        min_length=4,
    )
    ambient_lux: conint(ge=0, le=100000) | None = Field(
        ...,
        description="Latest ambient illuminance used by auto mode, lux, integer 0 to 100000, null when unknown.",
    )
    updated_at: AwareDatetime = Field(
        ..., description="Last change of any field, ISO 8601 UTC."
    )


class OccupancySource(Enum):
    """
    vision is the camera people counter, presence is the PIR fallback.
    """

    vision = "vision"
    presence = "presence"


class PlayAnnouncementRequest(BaseModel):
    """
    Optional request body of POST /announcements/{id}/play. An empty object or no body plays at the volume from Settings.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    volume: conint(ge=0, le=100) | None = Field(
        None, description="Volume for this playback only, percent 0 to 100."
    )


class ZonePresence(BaseModel):
    """
    PIR state of one zone.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    zone: Zone = Field(..., description="Zone.")
    present: bool = Field(
        ...,
        description="True while the PIR reports motion, held for the sensor hold time of about 3 seconds.",
    )


class PresenceReading(BaseModel):
    """
    PIR motion state per zone, normalised by the bridge from a sensor_report and published on sensor.presence. Contains one entry for each zone that has a PIR sensor, as declared in the device hello.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    ts: AwareDatetime = Field(
        ..., description="Sample time, ISO 8601 UTC, as stamped by the Pi on receipt."
    )
    device_id: constr(pattern=r"^esp32-[0-9a-f]{6}$") = Field(
        ..., description="Reporting device."
    )
    zones: list[ZonePresence] = Field(
        ...,
        description="Presence per zone with a PIR sensor, at most one entry per zone, canonical order.",
        max_length=4,
        min_length=1,
    )


class PresentationAction(Enum):
    """
    Presentation request action.
    """

    start = "start"
    stop = "stop"


class PresentationRequest(BaseModel):
    """
    Request body of POST /presentation. Idempotent, starting an active presentation or stopping an inactive one returns the current state unchanged.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    action: PresentationAction = Field(..., description="Start or stop.")
    lecture_id: constr(pattern=r"^lec_[a-z0-9]{6,32}$") | None = Field(
        None,
        description="Lecture to attach the presentation to, start only. Defaults to the lecture in progress.",
    )


class PresentationStarter(Enum):
    """
    app is POST /presentation, voice is a start_presentation intent, schedule is a lecture with lighting_mode presentation.
    """

    app = "app"
    voice = "voice"
    schedule = "schedule"


class PresentationState(BaseModel):
    """
    Presentation mode. While active the lighting mode is presentation unless a person overrides a zone.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    active: bool = Field(..., description="True while presentation mode is on.")
    started_at: AwareDatetime | None = Field(
        ...,
        description="Start of the current presentation, ISO 8601 UTC, null when inactive.",
    )
    started_by: PresentationStarter | None = Field(
        ..., description="Who started it, null when inactive."
    )
    lecture_id: constr(pattern=r"^lec_[a-z0-9]{6,32}$") | None = Field(
        ..., description="Lecture the presentation belongs to, null when none."
    )


class ReportTrigger(Enum):
    """
    interval is the periodic report, presence_change follows a PIR edge, query answers a query for sensors.
    """

    interval = "interval"
    presence_change = "presence_change"
    query = "query"


class PresenceMap(BaseModel):
    """
    PIR motion per zone, true while motion is detected. Only zones listed in hello.capabilities.presence_zones appear.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    left: bool | None = Field(None, description="Left zone PIR.")
    center: bool | None = Field(None, description="Center zone PIR.")
    right: bool | None = Field(None, description="Right zone PIR.")
    professor: bool | None = Field(None, description="Professor zone PIR.")


class SensorReport(BaseModel):
    """
    Uplink sensor sample from the ESP32. Sent every report_interval_ms from the hello, immediately on any PIR change but at most 5 per second, and after a query for sensors. The bridge splits it into sensor.environment and sensor.presence.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    type: Literal["sensor_report"] = Field(..., description="Message type.")
    seq: conint(ge=1, le=4294967295) = Field(..., description="Uplink frame counter.")
    ts: AwareDatetime = Field(
        ...,
        description="Device time of the sample, ISO 8601 UTC, epoch based until the clock is synced.",
    )
    uptime_ms: conint(ge=0, le=9007199254740991) = Field(
        ..., description="Milliseconds since device boot."
    )
    trigger: ReportTrigger = Field(..., description="Why this report was sent.")
    temperature_c: confloat(ge=-40.0, le=85.0) | None = Field(
        ...,
        description="Air temperature, degrees Celsius, one decimal place, null on sensor fault.",
    )
    humidity_pct: conint(ge=0, le=100) | None = Field(
        ...,
        description="Relative humidity, percent, integer 0 to 100, null on sensor fault.",
    )
    lux: conint(ge=0, le=100000) | None = Field(
        ...,
        description="Ambient illuminance, lux, integer 0 to 100000, null on sensor fault.",
    )
    presence: PresenceMap = Field(..., description="PIR state per fitted zone.")


class SensorsSnapshot(BaseModel):
    """
    Latest raw sensor data and ESP32 link status, returned by GET /sensors.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    environment: EnvironmentReading | None = Field(
        ..., description="Latest environment reading, null before the first report."
    )
    presence: PresenceReading | None = Field(
        ..., description="Latest PIR state, null before the first report."
    )
    device: DeviceLink = Field(..., description="ESP32 link status.")


class LightingPresets(BaseModel):
    """
    Brightness per zone for the lecture and presentation modes.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    lecture: ZoneLevels = Field(
        ..., description="Applied in lecture mode, percent 0 to 100 per zone."
    )
    presentation: ZoneLevels = Field(
        ..., description="Applied in presentation mode, percent 0 to 100 per zone."
    )


class ClimateSettings(BaseModel):
    """
    Climate configuration.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    default_target_c: confloat(ge=16.0, le=30.0) = Field(
        ...,
        description="Target restored at the start of each lecture, degrees Celsius, one decimal place.",
    )
    hysteresis_c: confloat(ge=0.1, le=3.0) = Field(
        ...,
        description="Dead band around the target, degrees Celsius, one decimal place, 0.1 to 3.0.",
    )


class VoiceSettings(BaseModel):
    """
    Voice configuration.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    enabled: bool = Field(
        ...,
        description="When false the voice stack ignores the wake word. POST /voice/ask keeps working.",
    )
    wake_word: constr(min_length=2, max_length=32) = Field(
        ..., description="Wake word model name."
    )
    language: constr(pattern=r"^[a-z]{2}(-[A-Z]{2})?$") = Field(
        ..., description="Recognition language as a BCP 47 tag."
    )
    min_confidence: confloat(ge=0.0, le=1.0) = Field(
        ...,
        description="Intents below this confidence are turned into unknown, 0.0 to 1.0.",
    )


class AnnouncementSettings(BaseModel):
    """
    Announcement configuration.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    volume: conint(ge=0, le=100) = Field(
        ..., description="Default playback volume, percent 0 to 100."
    )


class ZoneLevelsPatch(BaseModel):
    """
    Partial ZoneLevels, percent 0 to 100 per zone.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    left: conint(ge=0, le=100) | None = Field(None, description="Percent 0 to 100.")
    center: conint(ge=0, le=100) | None = Field(None, description="Percent 0 to 100.")
    right: conint(ge=0, le=100) | None = Field(None, description="Percent 0 to 100.")
    professor: conint(ge=0, le=100) | None = Field(
        None, description="Percent 0 to 100."
    )


class ClimateSettingsPatch(BaseModel):
    """
    Partial ClimateSettings.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    default_target_c: confloat(ge=16.0, le=30.0) | None = Field(
        None, description="Degrees Celsius, one decimal place, 16.0 to 30.0."
    )
    hysteresis_c: confloat(ge=0.1, le=3.0) | None = Field(
        None, description="Degrees Celsius, one decimal place, 0.1 to 3.0."
    )


class VoiceSettingsPatch(BaseModel):
    """
    Partial VoiceSettings.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    enabled: bool | None = Field(None, description="Wake word on or off.")
    wake_word: constr(min_length=2, max_length=32) | None = Field(
        None, description="Wake word model name."
    )
    language: constr(pattern=r"^[a-z]{2}(-[A-Z]{2})?$") | None = Field(
        None, description="BCP 47 tag."
    )
    min_confidence: confloat(ge=0.0, le=1.0) | None = Field(
        None, description="0.0 to 1.0."
    )


class AnnouncementSettingsPatch(BaseModel):
    """
    Partial AnnouncementSettings.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    volume: conint(ge=0, le=100) | None = Field(None, description="Percent 0 to 100.")


class DeviceStatuses(BaseModel):
    """
    Status of the physical devices.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    esp32: ComponentStatus = Field(..., description="Microcontroller link.")
    camera: ComponentStatus = Field(..., description="Vision camera.")
    microphone: ComponentStatus = Field(..., description="Voice microphone.")
    speaker: ComponentStatus = Field(..., description="Room speaker.")


class VoiceAskRequest(BaseModel):
    """
    Request body of POST /voice/ask. Text typed or dictated in the app, handled exactly like a spoken request.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    text: constr(min_length=1, max_length=200) = Field(
        ..., description="Request text, 1 to 200 characters."
    )
    request_id: constr(pattern=r"^req_[a-z0-9]{6,32}$") | None = Field(
        None,
        description="Optional client supplied correlation id. The API generates one when absent.",
    )


class VoiceAskOutcome(Enum):
    """
    executed means device commands were applied, answered means a query was answered, rejected means the engine could not carry it out, for example with the device offline, not_understood means the intent was unknown.
    """

    executed = "executed"
    answered = "answered"
    rejected = "rejected"
    not_understood = "not_understood"


class VoiceIntent(Enum):
    """
    Intents supported in this sprint, nothing more.
    """

    lights_on = "lights_on"
    lights_off = "lights_off"
    set_brightness = "set_brightness"
    query_temperature = "query_temperature"
    query_humidity = "query_humidity"
    query_occupancy = "query_occupancy"
    query_lecture = "query_lecture"
    start_presentation = "start_presentation"
    stop_presentation = "stop_presentation"
    play_announcement = "play_announcement"
    unknown = "unknown"


class VoiceAction(Enum):
    """
    Verb of an intent.
    """

    set = "set"
    query = "query"
    start = "start"
    stop = "stop"
    play = "play"
    none = "none"


class VoiceTargetType(Enum):
    """
    Subsystem an intent acts on.
    """

    lighting = "lighting"
    climate = "climate"
    occupancy = "occupancy"
    lecture = "lecture"
    presentation = "presentation"
    announcement = "announcement"
    none = "none"


class VoiceTarget(BaseModel):
    """
    Target of an intent.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    type: VoiceTargetType = Field(..., description="Subsystem.")
    zone: Zone | None = Field(
        ..., description="Zone named in the utterance, null for the whole room."
    )


class VoiceParams(BaseModel):
    """
    Intent parameters. brightness is required for set_brightness, announcement_id for play_announcement.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    brightness: conint(ge=0, le=100) | None = Field(
        None, description="Requested brightness, percent 0 to 100."
    )
    announcement_id: constr(pattern=r"^ann_[a-z0-9]{3,32}$") | None = Field(
        None, description="Announcement matched from the utterance."
    )


class VoiceSource(Enum):
    """
    microphone is the room microphone, app is text sent through POST /voice/ask.
    """

    microphone = "microphone"
    app = "app"


class VoiceCommand(BaseModel):
    """
    Parsed user request handed from the voice stack to the engine on voice.intent. The intent set is closed for this sprint. Each intent fixes action and target.type as in the table in bus.md, enforced by the allOf rules below. Intents below Settings.voice.min_confidence are emitted as unknown.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    request_id: constr(pattern=r"^req_[a-z0-9]{6,32}$") = Field(
        ..., description="Correlation id, same as on voice.wake and voice.transcript."
    )
    intent: VoiceIntent = Field(..., description="Recognised intent.")
    action: VoiceAction = Field(..., description="Verb implied by the intent.")
    target: VoiceTarget = Field(..., description="What the intent acts on.")
    params: VoiceParams = Field(
        ..., description="Intent parameters, an empty object when the intent has none."
    )
    confidence: confloat(ge=0.0, le=1.0) = Field(
        ..., description="Intent confidence, 0.0 to 1.0."
    )
    raw_text: constr(max_length=200) = Field(
        ...,
        description="Transcript the intent was parsed from, at most 200 characters.",
    )
    source: VoiceSource = Field(..., description="Where the text came from.")


class VoiceTranscript(BaseModel):
    """
    Final transcript of one utterance, published on voice.transcript. Partial transcripts are never published. The API publishes one with source app for every POST /voice/ask so both paths reach the same parser.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    request_id: constr(pattern=r"^req_[a-z0-9]{6,32}$") = Field(
        ..., description="Correlation id of the request."
    )
    ts: AwareDatetime = Field(
        ...,
        description="End of utterance, or API receipt time for source app, ISO 8601 UTC.",
    )
    text: constr(max_length=200) = Field(
        ...,
        description="Recognised text, at most 200 characters, empty when nothing was understood.",
    )
    confidence: confloat(ge=0.0, le=1.0) = Field(
        ..., description="Recogniser confidence, 0.0 to 1.0, 1.0 for source app."
    )
    language: constr(pattern=r"^[a-z]{2}(-[A-Z]{2})?$") = Field(
        ..., description="Recognition language as a BCP 47 tag."
    )
    source: VoiceSource = Field(..., description="microphone or app.")
    duration_ms: conint(ge=0, le=30000) | None = Field(
        ...,
        description="Audio length, milliseconds, integer 0 to 30000, null for source app.",
    )


class VoiceWake(BaseModel):
    """
    Wake word detected by the voice stack, published on voice.wake. Opens a request and assigns the request_id that follows it to the reply.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    request_id: constr(pattern=r"^req_[a-z0-9]{6,32}$") = Field(
        ..., description="New correlation id for this request."
    )
    ts: AwareDatetime = Field(..., description="Detection time, ISO 8601 UTC.")
    wake_word: constr(min_length=2, max_length=32) = Field(
        ..., description="Wake word model that fired."
    )
    confidence: confloat(ge=0.0, le=1.0) = Field(
        ..., description="Detector score, 0.0 to 1.0."
    )


class ZoneLightRequest(BaseModel):
    """
    Request body of POST /lighting/zone. Sets one zone and switches the lighting mode to manual. The other zones keep their current brightness.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    zone: Zone = Field(..., description="Zone.")
    brightness: conint(ge=0, le=100) = Field(
        ..., description="Brightness, percent 0 to 100."
    )
    fade_ms: conint(ge=0, le=5000) | None = Field(
        None, description="Fade, milliseconds 0 to 5000, default 400."
    )


class ZoneOccupancy(BaseModel):
    """
    Occupancy of one zone.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    zone: Zone = Field(..., description="Zone.")
    count: conint(ge=0, le=500) | None = Field(
        ...,
        description="People in the zone, integer 0 to 500, null when only presence is known, for example when the camera is down.",
    )
    occupied: bool = Field(
        ..., description="True when at least one person is in the zone."
    )
    confidence: confloat(ge=0.0, le=1.0) = Field(
        ..., description="Confidence of this zone estimate, 0.0 to 1.0."
    )


class BusEnvelope(BaseModel):
    """
    Wrapper of every event bus message and of every Server-Sent Event on GET /events. The topic fixes the payload schema and the only allowed publisher, enforced by the allOf rules below and listed in bus.md.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    id: constr(pattern=r"^evt_[a-z0-9]{6,32}$") = Field(
        ..., description="Unique message id. Consumers drop duplicates by id."
    )
    topic: BusTopic = Field(..., description="Logical topic.")
    ts: AwareDatetime = Field(
        ..., description="Publish time on the Pi clock, ISO 8601 UTC."
    )
    source: Component = Field(..., description="Publishing component.")
    contract_version: constr(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$") = Field(
        ..., description="Contract version of the publisher."
    )
    correlation_id: constr(pattern=r"^(req|dec)_[a-z0-9]{6,32}$") | None = Field(
        ...,
        description="Chain id. The request_id for anything caused by a voice or app request, otherwise the decision id from decision.action onward, null for raw sensor and perception events.",
    )
    payload: dict[str, Any] = Field(
        ..., description="Topic payload, schema given by the topic."
    )


class ClimateState(BaseModel):
    """
    Climate as known by the engine. The only actuator is the ESP32 fan. In auto mode the engine raises fan speed while temperature_c is above target_c plus the hysteresis from Settings and lowers it below target_c minus the hysteresis.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    mode: ClimateMode = Field(..., description="Active climate mode.")
    temperature_c: confloat(ge=-40.0, le=85.0) | None = Field(
        ...,
        description="Latest air temperature, degrees Celsius, one decimal place, null when unknown.",
    )
    humidity_pct: conint(ge=0, le=100) | None = Field(
        ...,
        description="Latest relative humidity, percent, integer 0 to 100, null when unknown.",
    )
    target_c: confloat(ge=16.0, le=30.0) = Field(
        ...,
        description="Target temperature, degrees Celsius, one decimal place, 16.0 to 30.0.",
    )
    fan_speed: conint(ge=0, le=100) = Field(
        ..., description="Commanded fan speed, percent 0 to 100."
    )
    fan_confirmed: bool = Field(
        ..., description="True when the device acknowledged fan_speed."
    )
    updated_at: AwareDatetime = Field(
        ..., description="Last change of any field, ISO 8601 UTC."
    )


class CurrentLecture(BaseModel):
    """
    The lecture in progress at a given instant and the next one after it. Returned by GET /lectures/current.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    at: AwareDatetime = Field(
        ..., description="Instant the answer refers to, ISO 8601 UTC."
    )
    current: Lecture | None = Field(
        ...,
        description="Lecture with status in_progress at that instant, null when none.",
    )
    next: Lecture | None = Field(
        ...,
        description="First scheduled lecture starting after that instant, null when none in the next 7 days.",
    )


class DecisionCommand(BaseModel):
    """
    Reference to one device command of a decision.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    cmd_id: constr(pattern=r"^[A-Za-z0-9_-]{1,16}$") = Field(
        ..., description="Command id, matches device.command and device.ack."
    )
    type: DeviceCommandType = Field(..., description="Command type.")
    status: CommandStatus = Field(..., description="Delivery status.")


class Decision(BaseModel):
    """
    One decision of the engine: what triggered it, which rule fired, what it did and why. Published on decision.action when made, with every command status pending. GET /decisions returns the same record with command statuses updated from device.ack. Every device.command is issued by exactly one decision.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    id: constr(pattern=r"^dec_[a-z0-9]{6,32}$") = Field(
        ..., description="Decision identifier."
    )
    ts: AwareDatetime = Field(..., description="Decision time, ISO 8601 UTC.")
    trigger: DecisionTrigger = Field(..., description="What caused the decision.")
    rule: constr(pattern=r"^[a-z][a-z0-9_]{2,63}$") = Field(
        ...,
        description="Identifier of the engine rule that fired, snake_case, for example occupancy_lights_on.",
    )
    action: Action = Field(..., description="What the engine decided to do.")
    outcome: DecisionOutcome = Field(..., description="Result at decision time.")
    summary: constr(min_length=1, max_length=200) = Field(
        ...,
        description="One English sentence explaining the decision, shown in the app.",
    )
    commands: list[DecisionCommand] = Field(
        ...,
        description="Device commands issued for this decision, empty when none.",
        max_length=8,
    )
    request_id: constr(pattern=r"^req_[a-z0-9]{6,32}$") | None = Field(
        ...,
        description="Correlation id when the trigger was a voice or app request, otherwise null.",
    )
    reply_text: constr(max_length=300) | None = Field(
        ...,
        description="Text the voice stack speaks and the app shows as the answer, null when the trigger was not a request.",
    )


class OccupancySnapshot(BaseModel):
    """
    Room occupancy at one instant. Published by vision on perception.occupancy with source vision. The engine serves its fused view on GET /occupancy, which falls back to PIR presence with source presence and null counts when vision is stale. zones holds exactly one entry per zone in canonical order. total is the sum of zone counts, null when any count is null.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    ts: AwareDatetime = Field(
        ..., description="Capture time of the frame or reading, ISO 8601 UTC."
    )
    total: conint(ge=0, le=500) | None = Field(
        ...,
        description="People in the room, integer 0 to 500, null when counts are unknown.",
    )
    occupied: bool = Field(..., description="True when any zone is occupied.")
    zones: list[ZoneOccupancy] = Field(
        ...,
        description="Per-zone occupancy, one entry per zone, order left, center, right, professor.",
        max_length=4,
        min_length=4,
    )
    source: OccupancySource = Field(..., description="Where the estimate comes from.")
    confidence: confloat(ge=0.0, le=1.0) = Field(
        ..., description="Overall confidence, 0.0 to 1.0."
    )
    stale: bool = Field(
        ...,
        description="True when the newest input is older than 5 seconds. Consumers must not switch lights off on stale data.",
    )
    frame_id: conint(ge=0) | None = Field(
        ...,
        description="Camera frame counter from vision, null when source is presence.",
    )


class LightingSettings(BaseModel):
    """
    Lighting configuration.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    target_lux: conint(ge=0, le=100000) = Field(
        ...,
        description="Auto mode lights occupied zones only while ambient light is below this, lux.",
    )
    vacancy_timeout_s: conint(ge=30, le=3600) = Field(
        ...,
        description="Seconds a zone must be unoccupied before auto mode dims it, integer 30 to 3600.",
    )
    vacant_brightness: conint(ge=0, le=100) = Field(
        ...,
        description="Brightness of unoccupied zones in auto mode while a lecture is in progress, percent 0 to 100. Outside lectures unoccupied zones are off.",
    )
    daylight_harvesting: bool = Field(
        ...,
        description="When true, auto mode lowers brightness as ambient lux rises above target_lux.",
    )
    presets: LightingPresets = Field(..., description="Per-mode brightness presets.")


class Settings(BaseModel):
    """
    Room configuration. Owned by the API, persisted on the Pi, published on settings.changed after every change. Every field is always present.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    room_id: constr(pattern=r"^[a-z0-9-]{2,32}$") = Field(
        ..., description="Room identifier, set at provisioning, read-only."
    )
    room_name: constr(min_length=1, max_length=60) = Field(
        ..., description="Display name, 1 to 60 characters."
    )
    timezone: constr(pattern=r"^[A-Za-z_]+(/[A-Za-z0-9_+-]+)*$") = Field(
        ...,
        description="IANA time zone used by the app to display times, for example Asia/Tashkent. Every timestamp on the wire stays UTC.",
    )
    lighting: LightingSettings = Field(..., description="Lighting configuration.")
    climate: ClimateSettings = Field(..., description="Climate configuration.")
    voice: VoiceSettings = Field(..., description="Voice configuration.")
    announcements: AnnouncementSettings = Field(
        ..., description="Announcement configuration."
    )
    updated_at: AwareDatetime = Field(..., description="Last change, ISO 8601 UTC.")


class LightingPresetsPatch(BaseModel):
    """
    Partial LightingPresets.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    lecture: ZoneLevelsPatch | None = Field(None, description="Lecture preset changes.")
    presentation: ZoneLevelsPatch | None = Field(
        None, description="Presentation preset changes."
    )


class SystemState(BaseModel):
    """
    Everything the app needs for its home screen in one object. Published by the engine on system.state, retained, and returned by GET /state.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    ts: AwareDatetime = Field(..., description="Snapshot time, ISO 8601 UTC.")
    status: ComponentStatus = Field(
        ..., description="Overall status, degraded when any device is not ok."
    )
    contract_version: constr(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$") = Field(
        ..., description="Contract version of the publisher."
    )
    devices: DeviceStatuses = Field(..., description="Per-device status.")
    environment: EnvironmentReading | None = Field(
        ..., description="Latest environment reading, null before the first report."
    )
    occupancy: OccupancySnapshot | None = Field(
        ..., description="Latest fused occupancy, null before the first estimate."
    )
    lighting: LightingState = Field(..., description="Lighting state.")
    climate: ClimateState = Field(..., description="Climate state.")
    presentation: PresentationState = Field(..., description="Presentation state.")
    lecture: CurrentLecture = Field(..., description="Current and next lecture.")


class VoiceAskResponse(BaseModel):
    """
    Result of POST /voice/ask, returned once the engine decided and, for device actions, once every command was acknowledged or timed out.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    request_id: constr(pattern=r"^req_[a-z0-9]{6,32}$") = Field(
        ..., description="Correlation id of the request."
    )
    command: VoiceCommand = Field(..., description="How the text was understood.")
    outcome: VoiceAskOutcome = Field(..., description="What happened.")
    reply_text: constr(min_length=1, max_length=300) = Field(
        ..., description="Answer to show to the user."
    )
    decision_id: constr(pattern=r"^dec_[a-z0-9]{6,32}$") | None = Field(
        ..., description="Engine decision, null when the intent was unknown."
    )


class LightingSettingsPatch(BaseModel):
    """
    Partial LightingSettings.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    target_lux: conint(ge=0, le=100000) | None = Field(None, description="Lux.")
    vacancy_timeout_s: conint(ge=30, le=3600) | None = Field(
        None, description="Seconds, integer 30 to 3600."
    )
    vacant_brightness: conint(ge=0, le=100) | None = Field(
        None, description="Percent 0 to 100."
    )
    daylight_harvesting: bool | None = Field(
        None, description="Daylight harvesting on or off."
    )
    presets: LightingPresetsPatch | None = Field(None, description="Preset changes.")


class SettingsPatch(BaseModel):
    """
    Request body of PATCH /settings. Deep merge semantics, only the keys present are changed, nested objects are merged key by key. room_id and updated_at are read-only and not accepted. The response is the full Settings after the merge.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )
    room_name: constr(min_length=1, max_length=60) | None = Field(
        None, description="Display name, 1 to 60 characters."
    )
    timezone: constr(pattern=r"^[A-Za-z_]+(/[A-Za-z0-9_+-]+)*$") | None = Field(
        None, description="IANA time zone."
    )
    lighting: LightingSettingsPatch | None = Field(
        None, description="Lighting changes."
    )
    climate: ClimateSettingsPatch | None = Field(None, description="Climate changes.")
    voice: VoiceSettingsPatch | None = Field(None, description="Voice changes.")
    announcements: AnnouncementSettingsPatch | None = Field(
        None, description="Announcement changes."
    )
