"""Tests for screenshot/compose.py, run as a subprocess."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "screenshot" / "compose.py"
REQUIREMENTS = REPO_ROOT / "screenshot" / "requirements.txt"

# Runs compose.py with Pillow hidden, so the test does not depend on whether
# the test machine has it installed.
WITHOUT_PILLOW = (
    "import runpy, sys\n"
    "sys.modules['PIL'] = None\n"
    "sys.argv = [sys.argv[1]]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)


def test_missing_pillow_names_the_install_command():
    result = subprocess.run(
        [sys.executable, "-c", WITHOUT_PILLOW, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "needs Pillow" in result.stderr
    assert f'"{sys.executable}" -m pip install -r "{REQUIREMENTS}"' in result.stderr
    assert "Traceback" not in result.stderr


def test_requirements_pin_pillow():
    lines = [
        line.strip()
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(lines) == 1
    assert lines[0].startswith("Pillow==")
