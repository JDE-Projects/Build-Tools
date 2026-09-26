"""Tests for scripts/checks/verify_runtime_lock.sh, run for real against uv
0.12.17 and the live package index (PyPI), instead of the
RUNTIME_LOCK_REGENERATED_FILE test-only seam used by test_verify_runtime_lock.py.

Each test needs network access to PyPI and takes real time to resolve and
download packages. A module-scoped fixture builds one throwaway virtual
environment, installs the pinned uv version into it, and every test runs the
script with that environment's script directory placed first on PATH so
`python` and `uv` both resolve there. If the venv or the uv install cannot be
created (for example when offline), the whole module is skipped rather than
failed.
"""

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "checks" / "verify_runtime_lock.sh"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "runtime_lock"
BASE_IN = FIXTURES / "requirements_uv_real.in"
BASE_TXT = FIXTURES / "requirements_uv_real.txt"
UV_VERSION = "0.12.17"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not found on PATH")


def _scripts_dir(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts"
    return venv_dir / "bin"


@pytest.fixture(scope="module")
def uv_env(tmp_path_factory):
    """Build a throwaway venv with uv 0.12.17 installed, or skip the module."""
    venv_dir = tmp_path_factory.mktemp("verify_runtime_lock_uv_venv")
    try:
        venv.create(venv_dir, with_pip=True)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"could not create a venv for uv tests: {exc}")

    scripts_dir = _scripts_dir(venv_dir)
    python = scripts_dir / ("python.exe" if sys.platform == "win32" else "python")
    if not python.exists():
        pytest.skip(f"venv python not found at {python}")

    install = subprocess.run(
        [str(python), "-m", "pip", "install", f"uv=={UV_VERSION}"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if install.returncode != 0:
        pytest.skip(
            f"could not install uv=={UV_VERSION} (offline?): {install.stderr.strip()}"
        )

    cache_dir = tmp_path_factory.mktemp("verify_runtime_lock_uv_cache")
    return {"scripts_dir": scripts_dir, "cache_dir": cache_dir}


def _run_script(uv_env, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PATH"] = str(uv_env["scripts_dir"]) + os.pathsep + env.get("PATH", "")
    env["UV_CACHE_DIR"] = str(uv_env["cache_dir"])
    env.pop("RUNTIME_LOCK_REGENERATED_FILE", None)
    return subprocess.run(
        [BASH, str(SCRIPT)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


def _fresh_project(tmp_path: Path) -> Path:
    """Copy the base fixture pair into tmp_path as requirements.in/.txt.

    uv writes the "# via -r requirements.in" annotation using the real
    filename of the input, so the files must be copied under those exact
    names rather than passed by their fixture paths.
    """
    shutil.copyfile(BASE_IN, tmp_path / "requirements.in")
    shutil.copyfile(BASE_TXT, tmp_path / "requirements.txt")
    return tmp_path


def test_unchanged_lock_passes_despite_newer_indirect_releases(uv_env, tmp_path):
    project = _fresh_project(tmp_path)
    result = _run_script(uv_env, project)
    assert result.returncode == 0, result.stderr


def test_bumped_direct_pin_without_regenerating_fails(uv_env, tmp_path):
    project = _fresh_project(tmp_path)
    requirements_in = project / "requirements.in"
    requirements_in.write_text(
        requirements_in.read_text().replace("requests==2.32.3", "requests==2.31.0"),
        encoding="utf-8",
    )
    result = _run_script(uv_env, project)
    assert result.returncode != 0
    assert "out of date" in result.stderr


def test_fake_hash_fails(uv_env, tmp_path):
    project = _fresh_project(tmp_path)
    requirements_txt = project / "requirements.txt"
    text = requirements_txt.read_text()
    real_hash = "--hash=sha256:55365417734eb18255590a9ff9eb97e9e1da868d4ccd6402399eaf68af20a760"
    assert real_hash in text
    fake_hash = "--hash=sha256:" + "0" * 64
    requirements_txt.write_text(text.replace(real_hash, fake_hash), encoding="utf-8")
    result = _run_script(uv_env, project)
    assert result.returncode != 0
    assert "out of date" in result.stderr


def test_hand_edited_indirect_version_fails(uv_env, tmp_path):
    project = _fresh_project(tmp_path)
    requirements_txt = project / "requirements.txt"
    text = requirements_txt.read_text()
    assert "idna==3.7 \\" in text
    edited = text.replace("idna==3.7 \\", "idna==3.6 \\")
    assert edited != text
    requirements_txt.write_text(edited, encoding="utf-8")
    result = _run_script(uv_env, project)
    assert result.returncode != 0
    assert "out of date" in result.stderr
