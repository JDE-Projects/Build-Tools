"""Tests for scripts/checks/run_shellcheck.sh, run as a subprocess.

Path-validation logic (missing listed files) does not need the ShellCheck
binary and always runs. Tests that actually invoke ShellCheck are skipped
when it is not installed locally, since Build-Tools pins and downloads its
own copy only inside CI.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "checks" / "run_shellcheck.sh"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "shellcheck"

BASH = shutil.which("bash")
SHELLCHECK = shutil.which("shellcheck")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not found on PATH")


def run_script(paths_arg: str, use_local_shellcheck: bool = False) -> subprocess.CompletedProcess:
    import os

    env = dict(os.environ)
    if use_local_shellcheck and SHELLCHECK:
        env["SHELLCHECK_BIN"] = SHELLCHECK
    return subprocess.run(
        [BASH, str(SCRIPT), paths_arg],
        capture_output=True,
        text=True,
        cwd=FIXTURES,
        env=env,
    )


def test_missing_listed_path_fails():
    # Does not need shellcheck installed: the missing-file check runs first.
    result = run_script("clean.sh does_not_exist.sh")
    assert result.returncode != 0
    assert "does_not_exist.sh" in result.stderr


@pytest.mark.skipif(SHELLCHECK is None, reason="shellcheck not installed locally")
def test_clean_script_passes():
    result = run_script("clean.sh", use_local_shellcheck=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(SHELLCHECK is None, reason="shellcheck not installed locally")
def test_broken_script_fails():
    result = run_script("broken.sh", use_local_shellcheck=True)
    assert result.returncode != 0
