"""Tests for scripts/checks/verify_runtime_lock.sh, run as a subprocess.

These use the RUNTIME_LOCK_REGENERATED_FILE test-only seam so the
regenerated-vs-committed comparison can be exercised without uv installed.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "checks" / "verify_runtime_lock.sh"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "runtime_lock"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not found on PATH")


def run_script(
    requirements_in: Path, requirements_txt: Path, regenerated_file: Path
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["RUNTIME_LOCK_REGENERATED_FILE"] = str(regenerated_file)
    return subprocess.run(
        [BASH, str(SCRIPT), str(requirements_in), str(requirements_txt)],
        capture_output=True,
        text=True,
        env=env,
    )


def test_matching_lock_passes():
    result = run_script(
        FIXTURES / "requirements.in",
        FIXTURES / "requirements_committed_matching.txt",
        FIXTURES / "requirements_matching.txt",
    )
    assert result.returncode == 0, result.stderr


def test_mismatching_lock_fails():
    result = run_script(
        FIXTURES / "requirements.in",
        FIXTURES / "requirements_committed_stale.txt",
        FIXTURES / "requirements_matching.txt",
    )
    assert result.returncode != 0
    assert "out of date" in result.stderr


def test_missing_requirements_in_fails():
    result = run_script(
        FIXTURES / "does_not_exist.in",
        FIXTURES / "requirements_committed_matching.txt",
        FIXTURES / "requirements_matching.txt",
    )
    assert result.returncode != 0
    assert "requirements.in" in result.stderr or "does_not_exist.in" in result.stderr


def test_missing_requirements_txt_fails():
    result = run_script(
        FIXTURES / "requirements.in",
        FIXTURES / "does_not_exist.txt",
        FIXTURES / "requirements_matching.txt",
    )
    assert result.returncode != 0
