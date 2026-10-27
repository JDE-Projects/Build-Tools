"""Tests for scripts/checks/verify_release_unpublished.sh, run as a
subprocess with a stand-in `gh` placed first on PATH."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "checks" / "verify_release_unpublished.sh"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not found on PATH")


def run_script(tmp_path: Path, gh_output: str, gh_exit: int) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh = bin_dir / "gh"
    fake_gh.write_bytes(
        f"#!/usr/bin/env bash\necho '{gh_output}'\nexit {gh_exit}\n".encode("utf-8")
    )
    fake_gh.chmod(0o755)
    env = dict(os.environ, GH_TOKEN="x", GH_REPO="JDE-Projects/Example")
    # Converted by bash itself so the stand-in wins on Windows and Linux alike.
    return subprocess.run(
        [BASH, "-c", 'PATH="$(cd "$1" && pwd):$PATH" bash "$2" v1.0.0', "_", str(bin_dir), str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )


def test_published_release_fails_with_clear_message(tmp_path):
    result = run_script(tmp_path, "false", 0)
    assert result.returncode == 1
    assert "already published" in result.stdout


def test_draft_release_passes(tmp_path):
    result = run_script(tmp_path, "true", 0)
    assert result.returncode == 0, result.stdout
    assert "draft" in result.stdout


def test_missing_release_passes(tmp_path):
    result = run_script(tmp_path, "release not found", 1)
    assert result.returncode == 0, result.stdout
    assert "no release" in result.stdout


def test_other_gh_failure_warns_and_passes(tmp_path):
    result = run_script(tmp_path, "HTTP 502: Bad Gateway", 1)
    assert result.returncode == 0, result.stdout
    assert "::warning::" in result.stdout
