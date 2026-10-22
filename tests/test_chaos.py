import subprocess
import sys

from services.core import config


def test_the_full_chaos_matrix_recovers_or_degrades_as_documented(tmp_path):
    report = tmp_path / "chaos.md"
    out = subprocess.run([sys.executable, "tools/chaos.py", "--fast", "--out", str(report)],
                         capture_output=True, text=True, cwd=config.ROOT, timeout=600)
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-3000:]
    text = report.read_text(encoding="utf-8")
    assert "**FAIL**" not in text and "Unhandled exceptions in the core log: none." in text
    assert text.count("| pass |") == 12
