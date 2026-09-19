"""Tests for scripts/checks/verify_dev_pins.sh, run as a subprocess."""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "checks" / "verify_dev_pins.sh"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "dev_pins"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not found on PATH")


def run_script(caller_file: Path, canonical_file: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(SCRIPT), str(caller_file), str(canonical_file)],
        capture_output=True,
        text=True,
    )


def test_matching_pins_pass():
    result = run_script(FIXTURES / "matching.txt", FIXTURES / "canonical.txt")
    assert result.returncode == 0, result.stderr


def test_mismatching_pins_fail():
    result = run_script(FIXTURES / "mismatching.txt", FIXTURES / "canonical.txt")
    assert result.returncode != 0
    assert "ruff==0.16.4" in result.stderr  # missing (from canonical)
    assert "ruff==0.15.0" in result.stderr  # extra (from caller)


def test_missing_caller_file_fails():
    result = run_script(FIXTURES / "does_not_exist.txt", FIXTURES / "canonical.txt")
    assert result.returncode != 0
    assert "missing" in result.stderr.lower()


def test_comments_blanks_and_crlf_are_ignored():
    result = run_script(FIXTURES / "cosmetic_crlf.txt", FIXTURES / "canonical.txt")
    assert result.returncode == 0, result.stderr
