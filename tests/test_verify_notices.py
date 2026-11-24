"""Tests for scripts/checks/verify_notices.py, run as a subprocess.

Expected versions are read from the live interpreter, the same way the script
reads them, so the tests hold on any Python.
"""

import decimal
import glob
import os
import platform
import re
import shutil
import sqlite3
import ssl
import subprocess
import sys
from pathlib import Path

import pyexpat
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "checks" / "verify_notices.py"


def live_versions() -> dict:
    openssl = re.match(r"OpenSSL (\d+\.\d+\.\d+)", ssl.OPENSSL_VERSION).group(1)
    return {
        "CPython": platform.python_version(),
        "OpenSSL": openssl,
        "SQLite": sqlite3.sqlite_version,
        "expat": pyexpat.EXPAT_VERSION.removeprefix("expat_"),
        "mpdecimal": decimal.__libmpdec_version__,
    }


def good_lines(**overrides) -> list:
    v = {**live_versions(), **overrides}
    return [
        f"CPython {v['CPython']} and bundled native libraries: PSF-2.0",
        "",
        f"CPython {v['CPython']} - LICENSE.txt",
        f"  OpenSSL {v['OpenSSL']}",
        f"expat {v['expat']}",
        f"SQLite {v['SQLite']} - public domain",
        f"mpdecimal {v['mpdecimal']}",
    ]


def write_notices(tmp_path: Path, lines: list) -> Path:
    path = tmp_path / "THIRD-PARTY-LICENSES.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


def test_matching_notices_pass(tmp_path):
    path = write_notices(tmp_path, good_lines())
    result = run_script(str(path))
    assert result.returncode == 0, result.stderr


def test_stale_cpython_names_version_and_line(tmp_path):
    lines = good_lines()
    lines[2] = "CPython 3.0.1 - LICENSE.txt"
    path = write_notices(tmp_path, lines)
    result = run_script(str(path))
    assert result.returncode != 0
    assert "CPython 3.0.1" in result.stderr
    assert "line 3" in result.stderr
    assert platform.python_version() in result.stderr


def test_missing_component_fails(tmp_path):
    lines = [x for x in good_lines() if not x.startswith("SQLite")]
    path = write_notices(tmp_path, lines)
    result = run_script(str(path))
    assert result.returncode != 0
    assert "SQLite" in result.stderr


def test_mid_sentence_name_is_ignored(tmp_path):
    lines = good_lines() + [
        "The Fraunhofer FDK AAC and OpenSSL libraries are under licenses",
        "See also OpenSSL 0.9.8 history",
    ]
    path = write_notices(tmp_path, lines)
    result = run_script(str(path))
    assert result.returncode == 0, result.stderr


def test_several_stale_components_all_reported(tmp_path):
    lines = good_lines(CPython="1.0.0", OpenSSL="1.0.0", mpdecimal="1.0.0")
    path = write_notices(tmp_path, lines)
    result = run_script(str(path))
    assert result.returncode != 0
    for name in ("CPython", "OpenSSL", "mpdecimal"):
        assert name in result.stderr
    assert "SQLite 1.0.0" not in result.stderr


def test_absent_notices_file_is_skipped(tmp_path):
    result = run_script(str(tmp_path / "missing.txt"))
    assert result.returncode == 0, result.stderr
    assert "skipping" in result.stdout


def _core_dll():
    found = [
        p
        for p in glob.glob(os.path.join(sys.base_prefix, "python3*.dll"))
        if os.path.basename(p).lower() != "python3.dll"
    ]
    return Path(found[0]) if found else None


needs_dll = pytest.mark.skipif(
    _core_dll() is None,
    reason="the interpreter has no python3*.dll (not a Windows layout)",
)


@needs_dll
def test_dist_identical_dll_passes(tmp_path):
    dll = _core_dll()
    dist = tmp_path / "dist"
    (dist / "_internal").mkdir(parents=True)
    shutil.copyfile(dll, dist / "_internal" / dll.name)
    result = run_script(str(tmp_path / "none.txt"), "--dist", str(dist))
    assert result.returncode == 0, result.stderr
    assert "1 file(s)" in result.stdout


@needs_dll
def test_dist_altered_dll_fails(tmp_path):
    dll = _core_dll()
    dist = tmp_path / "dist"
    dist.mkdir()
    shutil.copyfile(dll, dist / dll.name)
    with open(dist / dll.name, "ab") as fh:
        fh.write(b"x")
    result = run_script(str(tmp_path / "none.txt"), "--dist", str(dist))
    assert result.returncode != 0
    assert dll.name in result.stderr


@needs_dll
def test_dist_without_core_dll_fails(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "other.txt").write_text("hi")
    result = run_script(str(tmp_path / "none.txt"), "--dist", str(dist))
    assert result.returncode != 0
    assert "python3" in result.stderr
