"""Consistency checks for the Week 7 pre-handoff documents (docs/handoff/*.md).

DOCUMENTATION-FOCUSED TESTS ONLY: they read the documents and the repository and never run a model, open a device or touch hardware.
They guard against a handoff that drifts away from the code: an invented command or flag, a wrong model hash, a repo path that does not
exist, an acceptance status that disagrees with the Team Leader status file, a private path or e-mail in the text, and a GitHub
inclusion plan that no longer matches `.gitignore` or the files a first `git add -A` would pick up.
"""

import ast
import hashlib
import importlib
import importlib.util
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs" / "handoff"
HANDOFF = DOCS_DIR / "FATIMA_AI_HARDWARE_HANDOFF.md"
EVIDENCE = DOCS_DIR / "EVIDENCE_INDEX.md"
PR_DOC = DOCS_DIR / "AI_GITHUB_PR_READINESS.md"
MIDTERM = DOCS_DIR / "MIDTERM_HARDWARE_VALIDATION.md"        # midterm readiness protocol: same command / path / privacy checks
ALL_DOCS = (HANDOFF, EVIDENCE, PR_DOC, MIDTERM)

KNOWN_TOP = {"audio", "wakeword", "speech", "assistant", "fusion", "vision", "config", "scripts", "tests", "evaluation", "docs", "intents", "actions"}   # actions: Week 9 Task 1B simulated execution layer
ROOT_FILES = {"CLAUDE.md", "WEEK7_STATUS.md", "WEEK7_REPORT.md", "TEAM_LEADER_AI_STATUS.md", "TEAM_LEADER_AI_REPORT.md", "WEEK2_REPORT.md",
              "WEEK4_REPORT.md", "requirements.txt", "record_demo.py", ".gitignore", ".gitattributes"}
NAMED_BUT_NOT_CREATED = {"tests/fixtures/synthetic/"}       # named by .gitignore as the only place a tiny synthetic clip may be committed
LOCAL_ONLY = {"CLAUDE.md"}           # personal assistant instructions: present in the local folder, never part of the team upload (2026-10-18)
# the team repository's `AI engineer/` folder READMEs (2026-10-18): README.md is kept at this folder's root; models/README.md (where the
# model files go) exists only in the team copy. Documentation only: every model FILE under models/ stays forbidden.
TEAM_FOLDER_FILES = {"README.md", "models/README.md"}
SKIP_MODULES = {"pytest", "venv", "pip"}


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# =========================================================================================
# existence and shape
# =========================================================================================

@pytest.mark.parametrize("path", ALL_DOCS, ids=lambda p: p.name)
def test_the_handoff_documents_exist_and_are_not_empty(path):
    assert path.is_file() and len(text(path)) > 2000


def test_the_documents_state_that_this_is_not_a_hardware_demonstration():
    body = text(HANDOFF)
    assert "NOT a runnable hardware demonstration" in body
    # the Stage B harness and the software composition root exist (2026-10-16); the LIVE RUNNER exists since 2026-10-18 but never ran on a device
    assert "no live-microphone harness" not in body and "No composition root" not in body
    assert "no **live runner**" not in body and "SOFTWARE-VALIDATED WITH FAKE INPUT/OUTPUT" in body
    assert "`scripts/vesta_live_voice.py`" in body and "never run on a device" in body
    assert "until 2026-10-18 this document stated that no live runner existed" in body          # the historical record is kept
    for f in ("scripts/ai2_stage_b_live.py", "assistant/runtime.py", "scripts/vesta_live_voice.py"):
        assert (REPO_ROOT / f).is_file(), f
    assert "This project folder is **not a Git repository today**" in text(PR_DOC)


# =========================================================================================
# commands: every module, subcommand, flag and imported name must be real
# =========================================================================================

@lru_cache(maxsize=None)
def help_text(module: str, sub: str = "") -> str:
    argv = [sys.executable, "-m", module] + ([sub] if sub else []) + ["--help"]
    done = subprocess.run(argv, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, f"{' '.join(argv)} failed: {done.stderr[-300:]}"
    return done.stdout


def documented_module_commands():
    found = []
    for doc in ALL_DOCS:
        for match in re.finditer(r"python -m ([A-Za-z0-9_.]+)([^`\n|]*)", text(doc)):
            found.append((doc.name, match.group(1), match.group(2)))
    return found


def test_there_are_documented_module_commands_to_check():
    modules = {m for _, m, _ in documented_module_commands()}
    assert {"scripts.ai2_stage_a_transcribe", "evaluation.week7_voice_eval", "evaluation.week7_calibration", "wakeword.openwakeword_backend",
            "evaluation.ingest"} <= modules


@pytest.mark.parametrize("doc,module,args", documented_module_commands(), ids=lambda v: str(v)[:40])
def test_every_documented_module_command_uses_a_real_module_and_real_flags(doc, module, args):
    if module in SKIP_MODULES:
        return
    assert importlib.util.find_spec(module) is not None, f"{doc}: module {module} does not exist"
    first = help_text(module)
    subcommands = set(re.findall(r"[{,]([a-z][a-z0-9_]*)(?=[,}])", "".join(re.findall(r"\{[a-z0-9_,]+\}", first))))
    tokens = args.split()
    sub = next((t for t in tokens if t in subcommands), "")
    help_for = help_text(module, sub) if sub else first
    for flag in re.findall(r"--[A-Za-z][A-Za-z-]*", args):
        assert flag in help_for or flag in first, f"{doc}: `{module} {sub}` has no flag {flag}"


def test_the_python_script_entry_point_named_in_the_docs_exists():
    body = "\n".join(text(d) for d in ALL_DOCS)
    assert "python record_demo.py" in body
    record = (REPO_ROOT / "record_demo.py").read_text(encoding="utf-8")
    assert "python record_demo.py [duration_seconds] [output_path]" in record


def documented_python_c_commands():
    out = []
    for doc in ALL_DOCS:
        for line in text(doc).splitlines():
            m = re.match(r"\s*python -c \"(.*)\"\s*$", line)
            if m:
                out.append((doc.name, m.group(1)))
    return out


def test_there_are_python_c_commands_to_check():
    assert len(documented_python_c_commands()) >= 5


@pytest.mark.parametrize("doc,code", documented_python_c_commands(), ids=lambda v: str(v)[:50])
def test_every_python_c_command_parses_and_imports_only_names_that_exist(doc, code):
    tree = ast.parse(code)
    checked = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            try:
                module = importlib.import_module(node.module)
            except Exception:                                         # a package not importable on this machine: not a documentation error
                continue
            for alias in node.names:
                assert hasattr(module, alias.name), f"{doc}: {node.module} has no name {alias.name}"
                checked += 1
    assert checked >= 0


# =========================================================================================
# repository paths named in the documents exist
# =========================================================================================

def repo_paths_in(doc: Path):
    for token in re.findall(r"`([^`\s]+)`", text(doc)):
        if any(ch in token for ch in "<>*|{}") or "..." in token or token.startswith("models/") or token.startswith("<"):
            continue
        token = token.rstrip(",.;:")
        top = token.split("/")[0]
        if token in ROOT_FILES or (("/" in token) and top in KNOWN_TOP and re.search(r"(\.[a-z0-9]+|/)$", token)):
            yield token


@pytest.mark.parametrize("doc", ALL_DOCS, ids=lambda p: p.name)
def test_repository_paths_named_in_the_documents_exist(doc):
    paths = sorted(set(repo_paths_in(doc)))
    assert paths
    # a LOCAL_ONLY file is named in the documents but is absent from the team copy of this folder: its absence is correct there
    missing = [p for p in paths if p not in NAMED_BUT_NOT_CREATED and p not in LOCAL_ONLY and not (REPO_ROOT / p).exists()]
    assert missing == []


def test_the_pr_plan_keeps_claude_md_local_only_and_names_the_team_folder_readme():
    body = text(PR_DOC)
    row = next(l for l in body.splitlines() if l.startswith("|") and "`CLAUDE.md`" in l and "personal" in l)
    assert "LOCAL-ONLY — NOT UPLOADED" in row and "never part of the team upload" in row
    assert "Leave `CLAUDE.md` out of the upload" in body and "`CLAUDE.md` (local-only)" in body
    assert "## 9. Team repository target" in body and "`AI engineer/`" in body and "`README.md`" in body


# =========================================================================================
# model pins and hashes
# =========================================================================================

def test_model_pins_and_hashes_in_the_handoff_match_the_code():
    from audio.silero_vad import SILERO_MODEL
    from speech.faster_whisper_backend import FASTER_WHISPER_TINY_EN as WHISPER
    from speech.sherpa_onnx_tts import load_manifest

    body = text(HANDOFF)
    assert SILERO_MODEL.sha256 in body and SILERO_MODEL.commit in body and f"{SILERO_MODEL.size_bytes:,}" in body
    assert WHISPER.revision in body and WHISPER.repo in body and f"{WHISPER.total_size_bytes:,}" in body and WHISPER.directory_name in body
    for entry in WHISPER.files:
        assert entry.sha256 in body and f"{entry.size_bytes:,}" in body and f"`{entry.path}`" in body
    voice = load_manifest()
    assert voice.asset_sha256 in body and voice.directory_name in body and str(voice.total_size_bytes) in body and str(len(voice.files)) in body
    assert voice.release_tag in body


def test_every_sha256_in_the_documents_is_a_known_pin():
    from audio.silero_vad import SILERO_MODEL
    from speech.faster_whisper_backend import FASTER_WHISPER_TINY_EN as WHISPER
    from speech.sherpa_onnx_tts import load_manifest

    known = {SILERO_MODEL.sha256, load_manifest().asset_sha256} | {f.sha256 for f in WHISPER.files}
    body = text(HANDOFF)
    observed_paragraph = next(p for p in body.split("\n\n") if p.startswith("**openWakeWord files as installed"))
    main = body.replace(observed_paragraph, "")
    for doc_text in (main, text(EVIDENCE), text(PR_DOC)):
        for digest in re.findall(r"\b[0-9a-f]{64}\b", doc_text):
            assert digest in known, f"unrecognised hash in the documents: {digest}"


def test_the_observed_openwakeword_hashes_match_the_installed_files_when_they_are_present():
    spec = importlib.util.find_spec("openwakeword")
    if spec is None or not spec.submodule_search_locations:
        pytest.skip("openwakeword is not installed here")
    models = Path(list(spec.submodule_search_locations)[0]) / "resources" / "models"
    body = text(HANDOFF)
    checked = 0
    for name in ("embedding_model.onnx", "hey_jarvis_v0.1.onnx", "melspectrogram.onnx"):
        path = models / name
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert f"`{name}` `{digest}`" in body or f"{name}` `{digest}" in body, f"{name}: the documented hash differs from the installed file"
        checked += 1
    if not checked:
        pytest.skip("the openWakeWord model files are not downloaded here")


def test_models_stay_out_of_git_the_doc_says_so_and_gitignore_agrees():
    ignore = text(REPO_ROOT / ".gitignore").splitlines()
    for rule in ("*.onnx", "*.wav", "models/faster-whisper-*/", "models/vits-piper-*/"):
        assert rule in ignore
    assert "Model binaries are git-ignored and are never committed" in text(HANDOFF)


# =========================================================================================
# statuses
# =========================================================================================

def bold_status_by_task(path: Path, status_cell_index: int):
    out = {}
    for line in text(path).splitlines():
        m = re.match(r"\|\s*(AI-\d)\s*\|", line)
        if not m:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        bold = re.search(r"\*\*(.+?)\*\*", cells[status_cell_index])
        out[m.group(1)] = bold.group(1) if bold else None
    return out


def test_the_acceptance_matrix_uses_the_exact_team_leader_statuses():
    leader = bold_status_by_task(REPO_ROOT / "TEAM_LEADER_AI_STATUS.md", 3)
    mine = bold_status_by_task(HANDOFF, 6)
    assert sorted(leader) == sorted(mine) == [f"AI-{n}" for n in range(1, 10)]
    assert mine == leader
    assert "NOT COMPLETE (1 of 9)" in text(HANDOFF)


def test_no_leader_task_is_upgraded_by_software_readiness():
    mine = bold_status_by_task(HANDOFF, 6)
    for task, status in mine.items():
        if task != "AI-1":
            assert not status.startswith("COMPLETE"), task
    body = text(PR_DOC)
    assert "does not complete the AI leader tasks" in body and "AI-1 is complete; AI-2 to AI-9 are not" in body


def test_week7_status_in_the_documents_matches_the_tracking_file():
    tracking = text(REPO_ROOT / "WEEK7_STATUS.md")
    assert re.search(r"WEEK 7 \(VAD \+ STT\) \| \*\*IN PROGRESS\*\*", tracking)
    assert re.search(r"TASK 5 \| \*\*NOT COMPLETE\*\*|TASK 5 — RECORDED DEVELOPMENT EVALUATION[^|]*\| \*\*NOT COMPLETE\*\*", tracking)
    handoff, pr = text(HANDOFF), text(PR_DOC)
    for body in (handoff, pr):
        assert "Week 7 is IN PROGRESS" in body or "**Week 7 IN PROGRESS**" in body
        assert not re.search(r"Week 7 (?:is )?COMPLETE", body)
    for phrase in ("Task 3 COMPLETE — SOFTWARE-VALIDATED", "Task 4 COMPLETE — SOFTWARE-VALIDATED, HARDENED", "Task 5 evaluation harness READY"):
        assert phrase in handoff
    assert "real evaluation PENDING" in handoff


def test_blocked_tests_say_so_and_the_runnable_ones_name_a_real_command():
    rows = {}
    for line in text(HANDOFF).splitlines():
        m = re.match(r"\|\s*\*\*([A-J])\*\*", line)
        if m:
            rows[m.group(1)] = line
    assert sorted(rows) == list("ABCDEFGHIJ")
    assert "python -m scripts.ai2_stage_a_transcribe run" in rows["A"] and "BLOCKED" not in rows["A"].split("|")[4]
    for key in "DFGHIJ":
        assert "BLOCKED — SOFTWARE INTEGRATION REQUIRED" in rows[key], key
    # Test E is runnable on the device with the live runner (2026-10-18); it is not validated live
    assert "python -m scripts.vesta_live_voice" in rows["E"] and "BLOCKED" not in rows["E"] and "live NOT VALIDATED" in rows["E"] and "UNREVIEWED" in rows["E"]
    # Test B is runnable on the device with the Stage B harness; it is not validated live
    assert "python -m scripts.ai2_stage_b_live" in rows["B"] and "BLOCKED" not in rows["B"] and "live NOT VALIDATED" in rows["B"]
    assert "UNREVIEWED" in rows["B"]
    assert "RECORDED-HARDWARE AUDIO" in rows["A"]


def test_hardware_blockers_are_all_classified():
    section = text(HANDOFF).split("## 4. Hardware blockers")[1].split("## 5.")[0]
    classes = ("BLOCKING LIVE TEST", "NONBLOCKING DEVELOPMENT LIMITATION", "HARDWARE-ONLY VALIDATION", "TEAM DEPENDENCY")
    rows = [l for l in section.splitlines() if re.match(r"\|\s*\d+\s*\|", l)]
    assert len(rows) >= 10
    for row in rows:
        assert any(c in row for c in classes), row[:60]
    for needle in ("drops the OLDEST block silently", "STT is synchronous", "sample continuity", "channel layout", "thresholds", "Pi 5",
                   "speaker playback", "Face/LED"):
        assert needle in section


def test_validated_labels_are_never_claimed_without_a_qualifier():
    qualifiers = ("not", "NOT", "never", "only", "until", "pending", "PENDING", "none", "None", "nothing", "reached by", "after", "label")
    for doc in ALL_DOCS:
        for line in text(doc).splitlines():
            if "HARDWARE-VALIDATED" in line:
                assert any(q in line for q in qualifiers), f"{doc.name}: {line[:100]}"


# =========================================================================================
# privacy
# =========================================================================================

@pytest.mark.parametrize("doc", ALL_DOCS, ids=lambda p: p.name)
def test_documents_contain_no_private_paths_addresses_or_recordings(doc):
    body = text(doc)
    assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", body)          # no e-mail address
    assert not re.search(r"\b[A-Za-z]:[\\/]", body)                                         # no drive-letter path
    assert not re.search(r"/(?:home|Users)/\w+", body)
    assert "Hijab" not in body
    assert not re.search(r"\b\w+\.wav\b", body.replace("REC-0001.wav", "").replace("HW-001.wav", "").replace("your_recording.wav", "").replace("<wav>", ""))


# =========================================================================================
# GitHub inclusion plan
# =========================================================================================

def test_every_byte_pinned_file_is_protected_from_line_ending_conversion():
    """The SHA-256 pins over raw bytes (tests/test_week8_task4_routing.py HASH, test_runtime_routing, test_week8_task5_audit and
    evaluation/week8_router_history.py) only hold if Git never rewrites those files' line endings."""
    import tests.test_week8_task4_routing as t4
    pinned = set(t4.HASH) | {"intents/router.py"}
    rules = {}
    for line in text(REPO_ROOT / ".gitattributes").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            path, *attrs = line.split()
            rules[path] = attrs
    for rel in sorted(pinned):
        assert (REPO_ROOT / rel).is_file(), rel
        assert rules.get(rel) == ["-text"], f"{rel} must be listed as '-text' in .gitattributes"
    assert set(rules) == pinned                     # nothing else: the policy stays the smallest one that protects the pins


def test_the_ignore_patterns_listed_in_the_pr_plan_exist_in_gitignore():
    body = text(PR_DOC)
    block = body.split("Ignore patterns verified present in `.gitignore` (checked by the test):")[1].split("```")[1]
    patterns = [l.strip() for l in block.splitlines() if l.strip()]
    ignore = text(REPO_ROOT / ".gitignore").splitlines()
    assert len(patterns) >= 15
    for pattern in patterns:
        assert pattern in ignore, pattern


def test_a_first_git_add_would_pick_up_no_recording_model_or_result(tmp_path):
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    gitdir = tmp_path / "gd"
    subprocess.run([git, "init", "-q", "--bare", str(gitdir)], check=True)
    done = subprocess.run([git, f"--git-dir={gitdir}", f"--work-tree={REPO_ROOT}", "ls-files", "--others", "--exclude-standard"],
                          cwd=str(REPO_ROOT), capture_output=True, text=True, check=True)
    files = [f for f in done.stdout.splitlines() if f.strip()]
    assert len(files) > 100
    forbidden_suffixes = (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".onnx", ".bin", ".tflite", ".partial", ".pyc", ".log")
    forbidden_names = ("week7_eval_items.jsonl", "week7_eval_report.json", "week7_calibration_sweep.json", "your_recording.wav")
    # the ACTUAL file selection must exclude the local-only files (enforced by .gitignore, not only by the plan's wording)
    assert not LOCAL_ONLY & set(files), f"local-only files would be uploaded: {LOCAL_ONLY & set(files)}"
    allowed_root = (ROOT_FILES - LOCAL_ONLY) | TEAM_FOLDER_FILES | {"WEEK3_REPORT.md", "WEEK3_STATUS.md", "WEEK4_STATUS.md", "WEEK5_REPORT.md", "WEEK5_STATUS.md", "WEEK6_REPORT.md",
                                 "WEEK6_STATUS.md", "WEEK2_STATUS.md", "WEEK8_STATUS.md", "WEEK8_REPORT.md",
                                 "WEEK9_STATUS.md", "WEEK9_REPORT.md"}
    for f in files:
        name = f.rsplit("/", 1)[-1]
        assert not f.endswith(forbidden_suffixes), f
        assert name not in forbidden_names and (f in TEAM_FOLDER_FILES or not f.startswith(("models/", ".venv/", ".claude/", "skill-observations/", "vision_assets/"))), f
        top = f.split("/")[0]
        assert top in KNOWN_TOP or f in allowed_root, f"unplanned top-level item in the upload: {f}"
        assert "week7" not in name or not name.endswith("manifest.json") or f == "evaluation/week7_corpus_manifest.template.json", f
    assert "evaluation/week7_corpus_manifest.template.json" in files          # the committed template is NOT swallowed by the manifest rule
