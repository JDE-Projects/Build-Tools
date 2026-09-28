"""Tests for ui_drive/drive.py's non-UI logic: manifest validation, listener
verification, and free-port selection. None of this launches a process or a
browser, but drive.py itself is Windows-only (it loads kernel32/ntdll/user32
at import time), so these tests are skipped anywhere else.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="ui_drive is Windows-only")

REPO_ROOT = Path(__file__).resolve().parent.parent
DRIVE_PY = REPO_ROOT / "ui_drive" / "drive.py"


def _load_drive():
    spec = importlib.util.spec_from_file_location("ui_drive_drive", DRIVE_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


drive = _load_drive() if sys.platform == "win32" else None


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "app_repo"
    (repo / "tools" / "ui_check").mkdir(parents=True)
    (repo / "entry.py").write_text("print('hi')\n", encoding="utf-8")
    (repo / "tools" / "ui_check" / "scenario.js").write_text("export default async () => {};\n", encoding="utf-8")
    return repo


def good_manifest() -> dict:
    return {
        "entry": "entry.py",
        "mutex": "Test_Mutex",
        "window_title": "Test Window",
        "scenarios": {
            "smoke": {"script": "tools/ui_check/scenario.js"},
        },
    }


# --------------------------------------------------------------------------
# Manifest validation
# --------------------------------------------------------------------------

def test_good_manifest_is_accepted(tmp_path):
    repo = make_repo(tmp_path)
    plan = drive.validate_manifest(repo, good_manifest(), "smoke")
    assert plan["entry_path"] == (repo / "entry.py").resolve()
    assert plan["mutex"] == "Test_Mutex"
    assert plan["window_title"] == "Test Window"
    assert plan["script_path"] == (repo / "tools" / "ui_check" / "scenario.js").resolve()
    assert plan["fixture_path"] is None


def test_missing_required_key_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    manifest = good_manifest()
    del manifest["mutex"]
    with pytest.raises(drive.ManifestError, match="mutex"):
        drive.validate_manifest(repo, manifest, "smoke")


def test_unknown_scenario_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(drive.ManifestError, match="unknown scenario"):
        drive.validate_manifest(repo, good_manifest(), "does-not-exist")


def test_absolute_entry_path_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    manifest = good_manifest()
    manifest["entry"] = str(tmp_path / "entry.py")
    with pytest.raises(drive.ManifestError, match="absolute"):
        drive.validate_manifest(repo, manifest, "smoke")


def test_dotdot_in_script_path_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    manifest = good_manifest()
    manifest["scenarios"]["smoke"]["script"] = "../outside.js"
    with pytest.raises(drive.ManifestError, match=r"\.\."):
        drive.validate_manifest(repo, manifest, "smoke")


def test_path_resolving_outside_repo_is_rejected(tmp_path):
    # A symlink that looks repo-relative but actually points outside the
    # repo root. Falls back to a plain "outside the repo" style path if
    # symlinks aren't supported in this environment.
    repo = make_repo(tmp_path)
    outside = tmp_path / "outside.js"
    outside.write_text("export default async () => {};\n", encoding="utf-8")
    link = repo / "tools" / "ui_check" / "linked.js"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available in this environment")
    manifest = good_manifest()
    manifest["scenarios"]["smoke"]["script"] = "tools/ui_check/linked.js"
    with pytest.raises(drive.ManifestError, match="outside"):
        drive.validate_manifest(repo, manifest, "smoke")


def test_scenario_missing_script_key_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    manifest = good_manifest()
    manifest["scenarios"]["smoke"] = {}
    with pytest.raises(drive.ManifestError, match="script"):
        drive.validate_manifest(repo, manifest, "smoke")


def test_load_manifest_missing_file_is_rejected(tmp_path):
    repo = tmp_path / "no_manifest_repo"
    repo.mkdir()
    with pytest.raises(drive.ManifestError, match="no manifest"):
        drive.load_manifest(repo)


def test_load_manifest_invalid_json_is_rejected(tmp_path):
    repo = tmp_path / "bad_json_repo"
    (repo / "tools" / "ui_check").mkdir(parents=True)
    (repo / "tools" / "ui_check" / "ui_drive.json").write_text("not json", encoding="utf-8")
    with pytest.raises(drive.ManifestError, match="not valid JSON"):
        drive.load_manifest(repo)


def test_load_manifest_reads_a_real_file(tmp_path):
    repo = make_repo(tmp_path)
    manifest_path = repo / "tools" / "ui_check" / "ui_drive.json"
    manifest_path.write_text(json.dumps(good_manifest()), encoding="utf-8")
    loaded = drive.load_manifest(repo)
    assert loaded["mutex"] == "Test_Mutex"


# --------------------------------------------------------------------------
# Listener verification
# --------------------------------------------------------------------------

def netstat_line(local_addr: str, port: int, pid: int, state: str = "LISTENING") -> str:
    return f"  TCP    {local_addr}:{port}         0.0.0.0:0              {state}       {pid}"


def test_verify_listener_accepts_loopback_owned_by_job(tmp_path):
    output = netstat_line("127.0.0.1", 9333, 4242)
    ok, reason = drive.verify_listener(9333, output, {4242})
    assert ok, reason


def test_verify_listener_rejects_all_interfaces(tmp_path):
    output = netstat_line("0.0.0.0", 9333, 4242)
    ok, reason = drive.verify_listener(9333, output, {4242})
    assert not ok
    assert "loopback" in reason


def test_verify_listener_rejects_ipv6_any(tmp_path):
    output = "  TCPv6  [::]:9333              [::]:0                 LISTENING       4242"
    ok, reason = drive.verify_listener(9333, output, {4242})
    assert not ok
    assert "loopback" in reason


def test_verify_listener_rejects_unknown_pid(tmp_path):
    output = netstat_line("127.0.0.1", 9333, 9999)
    ok, reason = drive.verify_listener(9333, output, {4242})
    assert not ok
    assert "9999" in reason


def test_verify_listener_rejects_nothing_listening(tmp_path):
    output = netstat_line("127.0.0.1", 1234, 4242)
    ok, reason = drive.verify_listener(9333, output, {4242})
    assert not ok


def test_parse_netstat_lines_skips_header_and_blank_lines():
    output = (
        "\n"
        "Active Connections\n"
        "\n"
        "  Proto  Local Address          Foreign Address        State           PID\n"
        + netstat_line("127.0.0.1", 9333, 4242)
        + "\n"
    )
    rows = drive.parse_netstat_lines(output)
    assert rows == [
        {"proto": "TCP", "local_addr": "127.0.0.1", "local_port": 9333, "state": "LISTENING", "pid": 4242}
    ]


# --------------------------------------------------------------------------
# Free port selection
# --------------------------------------------------------------------------

def test_pick_port_stays_in_range():
    import random

    rng = random.Random(0)
    for _ in range(500):
        port = drive.pick_port(rng)
        assert drive.PORT_MIN <= port <= drive.PORT_MAX


def test_find_free_port_returns_a_free_port():
    port = drive.find_free_port()
    assert drive.PORT_MIN <= port <= drive.PORT_MAX
    assert drive.is_port_free(port)


# --------------------------------------------------------------------------
# timeout_s validation
# --------------------------------------------------------------------------

def test_timeout_s_defaults_when_absent(tmp_path):
    repo = make_repo(tmp_path)
    plan = drive.validate_manifest(repo, good_manifest(), "smoke")
    assert plan["timeout_s"] == drive.DEFAULT_SCENARIO_TIMEOUT_S


def test_timeout_s_valid_value_is_accepted(tmp_path):
    repo = make_repo(tmp_path)
    manifest = good_manifest()
    manifest["scenarios"]["smoke"]["timeout_s"] = 120
    plan = drive.validate_manifest(repo, manifest, "smoke")
    assert plan["timeout_s"] == 120


def test_timeout_s_too_low_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    manifest = good_manifest()
    manifest["scenarios"]["smoke"]["timeout_s"] = 5
    with pytest.raises(drive.ManifestError, match="timeout_s"):
        drive.validate_manifest(repo, manifest, "smoke")


def test_timeout_s_too_high_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    manifest = good_manifest()
    manifest["scenarios"]["smoke"]["timeout_s"] = 601
    with pytest.raises(drive.ManifestError, match="timeout_s"):
        drive.validate_manifest(repo, manifest, "smoke")


def test_timeout_s_non_integer_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    manifest = good_manifest()
    manifest["scenarios"]["smoke"]["timeout_s"] = "30"
    with pytest.raises(drive.ManifestError, match="timeout_s"):
        drive.validate_manifest(repo, manifest, "smoke")


def test_timeout_s_float_is_rejected(tmp_path):
    repo = make_repo(tmp_path)
    manifest = good_manifest()
    manifest["scenarios"]["smoke"]["timeout_s"] = 30.5
    with pytest.raises(drive.ManifestError, match="timeout_s"):
        drive.validate_manifest(repo, manifest, "smoke")


def test_timeout_s_bool_is_rejected(tmp_path):
    # bool is a subclass of int in Python; True/False are not sensible
    # timeouts and should be rejected the same as any other non-integer.
    repo = make_repo(tmp_path)
    manifest = good_manifest()
    manifest["scenarios"]["smoke"]["timeout_s"] = True
    with pytest.raises(drive.ManifestError, match="timeout_s"):
        drive.validate_manifest(repo, manifest, "smoke")


def test_timeout_s_bounds_are_accepted(tmp_path):
    repo = make_repo(tmp_path)
    manifest = good_manifest()
    manifest["scenarios"]["smoke"]["timeout_s"] = drive.MIN_SCENARIO_TIMEOUT_S
    plan = drive.validate_manifest(repo, manifest, "smoke")
    assert plan["timeout_s"] == drive.MIN_SCENARIO_TIMEOUT_S

    manifest["scenarios"]["smoke"]["timeout_s"] = drive.MAX_SCENARIO_TIMEOUT_S
    plan = drive.validate_manifest(repo, manifest, "smoke")
    assert plan["timeout_s"] == drive.MAX_SCENARIO_TIMEOUT_S


# --------------------------------------------------------------------------
# Fixture first-line reader (polls a file directly, nothing launched)
# --------------------------------------------------------------------------

def test_read_first_json_line_reads_existing_file(tmp_path):
    path = tmp_path / "fixture_stdout.log"
    path.write_text('{"port": 4242, "user": "demo"}\n', encoding="utf-8")
    data = drive.read_first_json_line(path, timeout_s=2)
    assert data == {"port": 4242, "user": "demo"}


def test_read_first_json_line_waits_for_the_file_to_appear(tmp_path):
    import threading
    import time

    path = tmp_path / "fixture_stdout.log"

    def write_later():
        time.sleep(0.3)
        path.write_text('{"ready": true}\n', encoding="utf-8")

    threading.Thread(target=write_later, daemon=True).start()
    data = drive.read_first_json_line(path, timeout_s=3)
    assert data == {"ready": True}


def test_read_first_json_line_times_out_when_nothing_written(tmp_path):
    path = tmp_path / "fixture_stdout.log"
    with pytest.raises(drive.SetupError, match="did not print"):
        drive.read_first_json_line(path, timeout_s=0.3, poll_interval_s=0.05)


def test_read_first_json_line_rejects_invalid_json(tmp_path):
    path = tmp_path / "fixture_stdout.log"
    path.write_text("not json at all\n", encoding="utf-8")
    with pytest.raises(drive.SetupError, match="not valid JSON"):
        drive.read_first_json_line(path, timeout_s=2)


def test_read_first_json_line_rejects_empty_first_line(tmp_path):
    path = tmp_path / "fixture_stdout.log"
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(drive.SetupError, match="empty"):
        drive.read_first_json_line(path, timeout_s=2)
