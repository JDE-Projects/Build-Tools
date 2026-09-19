"""Tests for scripts/checks/verify_release_version.py, run as a subprocess.

The tag is always passed via the GITHUB_REF_NAME environment variable, never
interpolated into a command line, matching how the real workflow step calls
this script.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "checks" / "verify_release_version.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "release_version"


def run_script(entry_script: str, tag: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GITHUB_REF_NAME"] = tag
    args = [sys.executable, str(SCRIPT)]
    if entry_script:
        args.append(entry_script)
    return subprocess.run(args, capture_output=True, text=True, env=env)


def test_good_stable_tag_passes():
    result = run_script(str(FIXTURES / "good_entry.py"), "v1.4.8")
    assert result.returncode == 0, result.stderr


def test_good_rc_tag_passes():
    result = run_script(str(FIXTURES / "good_entry.py"), "v1.4.8-rc3")
    assert result.returncode == 0, result.stderr


def test_malformed_tags_fail():
    bad_tags = [
        "1.4.8",  # missing leading v
        "v1.4.8.1",  # extra version part
        "v01.4.8",  # leading zero in major
        "v1.04.8",  # leading zero in minor
        "v1.4.08",  # leading zero in patch
        "v1.4.8-rc0",  # rc must be >= 1
        "v1.4.8-rc01",  # leading zero in rc
        "v1.4.8-beta1",  # wrong suffix
        "v1.4",  # incomplete
        "",  # empty
    ]
    for tag in bad_tags:
        result = run_script(str(FIXTURES / "good_entry.py"), tag)
        assert result.returncode != 0, f"expected failure for tag {tag!r}"


def test_tag_version_mismatch_fails():
    result = run_script(str(FIXTURES / "good_entry.py"), "v9.9.9")
    assert result.returncode != 0
    assert "9.9.9" in result.stderr
    assert "1.4.8" in result.stderr


def test_no_app_version_fails():
    result = run_script(str(FIXTURES / "no_version_entry.py"), "v1.4.8")
    assert result.returncode != 0
    assert "no top-level APP_VERSION" in result.stderr


def test_multiple_app_version_fails():
    result = run_script(str(FIXTURES / "multiple_version_entry.py"), "v1.0.1")
    assert result.returncode != 0
    assert "2 top-level APP_VERSION" in result.stderr


def test_non_literal_app_version_fails():
    result = run_script(str(FIXTURES / "non_literal_entry.py"), "v1.0.0")
    assert result.returncode != 0
    assert "not a plain string literal" in result.stderr


def test_missing_entry_script_argument_fails():
    result = run_script("", "v1.0.0")
    assert result.returncode != 0
    assert "entry_script" in result.stderr


def test_crafted_tag_is_handled_safely_not_executed():
    # A tag designed to break out of a naive string interpolation. Passed via
    # env var, so it must just fail validation, never run as shell/python code.
    crafted = "v1.0.0'; touch pwned; echo '"
    result = run_script(str(FIXTURES / "good_entry.py"), crafted)
    assert result.returncode != 0
    assert not (REPO_ROOT / "pwned").exists()
    (REPO_ROOT / "pwned").unlink(missing_ok=True)  # safety net; should not exist
