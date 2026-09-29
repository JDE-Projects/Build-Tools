"""Tests for ui_drive/drive.py's non-UI logic: manifest validation, listener
verification, and free-port selection. None of this launches a process or a
browser, but drive.py itself is Windows-only (it loads kernel32/ntdll/user32
at import time), so these tests are skipped anywhere else.
"""

import importlib.util
import json
import os
import shutil
import subprocess
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


# --------------------------------------------------------------------------
# fixture_env
# --------------------------------------------------------------------------

def test_fixture_env_adds_out_dir_without_mutating_base(tmp_path):
    base = {"PATH": "C:/somewhere"}
    out_dir = tmp_path / "out"
    app_dir = out_dir / "app"
    env = drive.fixture_env(base, out_dir, app_dir)
    assert env["UI_DRIVE_OUT_DIR"] == str(out_dir)
    assert env["UI_DRIVE_APP_DIR"] == str(app_dir)
    assert env["PATH"] == "C:/somewhere"
    assert "UI_DRIVE_OUT_DIR" not in base


def test_fixture_env_overrides_existing_var(tmp_path):
    base = {"UI_DRIVE_OUT_DIR": "stale"}
    out_dir = tmp_path / "out"
    app_dir = out_dir / "app"
    env = drive.fixture_env(base, out_dir, app_dir)
    assert env["UI_DRIVE_OUT_DIR"] == str(out_dir)


def test_fixture_env_includes_base_child_env(tmp_path):
    out_dir = tmp_path / "out"
    app_dir = out_dir / "app"
    env = drive.fixture_env({}, out_dir, app_dir)
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["TEMP"] == str(out_dir / "tmp")
    assert env["TMP"] == str(out_dir / "tmp")


def test_base_child_env_adds_expected_vars_without_mutating_base(tmp_path):
    base = {"PATH": "C:/somewhere"}
    run_dir = tmp_path / "run"
    env = drive.base_child_env(base, run_dir)
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["TEMP"] == str(run_dir / "tmp")
    assert env["TMP"] == str(run_dir / "tmp")
    assert env["PATH"] == "C:/somewhere"
    assert "PYTHONDONTWRITEBYTECODE" not in base


# --------------------------------------------------------------------------
# Exit code mapping
# --------------------------------------------------------------------------

def test_compute_exit_code_all_pass():
    assert drive.compute_exit_code(True, True, True, True) == 0


def test_compute_exit_code_check_failure_with_verified_cleanup():
    assert drive.compute_exit_code(True, False, True, True) == 1
    assert drive.compute_exit_code(True, True, False, True) == 1
    assert drive.compute_exit_code(True, True, True, False) == 1


def test_compute_exit_code_unverified_cleanup_outranks_check_failure():
    # Even when every check passed, an unverified cleanup always wins.
    assert drive.compute_exit_code(False, True, True, True) == 2
    # And it wins over a check failure too, not just alongside a pass.
    assert drive.compute_exit_code(False, False, False, False) == 2


def test_collect_scenario_results_rejects_node_failure_before_reading_results(tmp_path):
    results_path = tmp_path / "results.json"
    results_path.write_text(
        json.dumps({"checks": [{"name": "planted pass", "pass": True}], "error": None}),
        encoding="utf-8",
    )

    results = drive.collect_scenario_results(results_path, node_returncode=1)

    assert results["checks"] == []
    assert results["error"] == "cdp.mjs exited 1 before writing results"


@pytest.mark.parametrize("name", ["tmp", "app"])
def test_prepare_run_subdirectories_refuses_existing_file(tmp_path, name):
    run_dir = tmp_path / VALID_RUN_NAME
    run_dir.mkdir()
    (run_dir / name).write_text("planted", encoding="utf-8")

    with pytest.raises(drive.SetupError, match=f"could not create.*{name}"):
        drive.prepare_run_subdirectories(run_dir)


@pytest.mark.parametrize("name", ["tmp", "app"])
def test_main_returns_clean_refusal_when_setup_subdirectory_exists(tmp_path, monkeypatch, name):
    repo = make_repo(tmp_path)
    manifest_path = repo / "tools" / "ui_check" / "ui_drive.json"
    manifest_path.write_text(json.dumps(good_manifest()), encoding="utf-8")
    run_dir = tmp_path / VALID_RUN_NAME
    run_dir.mkdir()
    (run_dir / name).write_text("planted", encoding="utf-8")
    run_pin = drive.pin_directory(run_dir)

    monkeypatch.setattr(drive, "mutex_exists", lambda mutex: False)
    monkeypatch.setattr(drive, "create_run_dir", lambda: (run_dir, run_pin))

    assert drive.main([str(repo), "smoke"]) == 2


class FakeKernel32:
    def __init__(self):
        self.closed = []

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return True

    def GetProcessId(self, handle):
        return 101 if handle == "app-process" else 0


def prepare_main_teardown_test(tmp_path, monkeypatch, launch):
    repo = make_repo(tmp_path)
    run_dir = tmp_path / VALID_RUN_NAME
    run_dir.mkdir()
    kernel32 = FakeKernel32()
    markers = []
    plan = {
        "mutex": "Test_Mutex",
        "entry_path": repo / "entry.py",
        "script_path": repo / "tools" / "ui_check" / "scenario.js",
        "fixture_path": None,
        "window_title": "Test Window",
        "page_url_contains": None,
        "timeout_s": 10,
    }

    monkeypatch.setattr(drive, "kernel32", kernel32)
    monkeypatch.setattr(drive, "mutex_exists", lambda mutex: False)
    monkeypatch.setattr(drive, "load_manifest", lambda path: {})
    monkeypatch.setattr(drive, "validate_manifest", lambda *args: plan)
    monkeypatch.setattr(drive, "create_run_dir", lambda: (run_dir, "run-pin"))
    monkeypatch.setattr(drive, "acquire_run_lock", lambda path: "run-lock")
    monkeypatch.setattr(drive, "release_run_lock", lambda fd: None)
    monkeypatch.setattr(drive, "prepare_run_subdirectories", lambda path: (path / "tmp", path / "app"))
    monkeypatch.setattr(drive, "copy_repo_to", lambda source, dest: None)
    monkeypatch.setattr(drive, "find_free_port", lambda: 41000)
    monkeypatch.setattr(drive, "create_job_with_kill_on_close", lambda: "job")
    monkeypatch.setattr(drive, "launch_suspended_in_job", launch)
    monkeypatch.setattr(drive, "write_teardown_marker", lambda path: markers.append(path))
    return repo, kernel32, markers


def test_main_refuses_when_mutex_is_held_without_creating_a_run(monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    monkeypatch.setattr(drive, "mutex_exists", lambda mutex: True)
    monkeypatch.setattr(
        drive, "create_run_dir", lambda: pytest.fail("create_run_dir must not run when the mutex is held")
    )
    monkeypatch.setattr(
        drive,
        "launch_suspended_in_job",
        lambda *args: pytest.fail("launch_suspended_in_job must not run when the mutex is held"),
    )

    assert drive.main([str(repo), "smoke"]) == 2


def test_main_exception_after_job_writes_marker_when_cleanup_is_verified(tmp_path, monkeypatch):
    def fail_launch(*args, **kwargs):
        raise drive.SetupError("app launch failed")

    repo, kernel32, markers = prepare_main_teardown_test(tmp_path, monkeypatch, fail_launch)
    monkeypatch.setattr(drive, "get_job_pids", lambda job: {101})
    monkeypatch.setattr(drive, "pid_is_gone", lambda pid: True)
    monkeypatch.setattr(drive, "run_netstat", lambda: "")

    assert drive.main([str(repo), "smoke"]) == 2
    assert kernel32.closed.count("job") == 1
    assert markers


def test_main_exception_after_job_skips_marker_when_a_survivor_remains(tmp_path, monkeypatch):
    def fail_launch(*args, **kwargs):
        raise drive.SetupError("app launch failed")

    repo, kernel32, markers = prepare_main_teardown_test(tmp_path, monkeypatch, fail_launch)
    monkeypatch.setattr(drive, "get_job_pids", lambda job: {101})
    monkeypatch.setattr(drive, "pid_is_gone", lambda pid: False)
    monkeypatch.setattr(drive, "run_netstat", lambda: "")

    assert drive.main([str(repo), "smoke"]) == 2
    assert kernel32.closed.count("job") == 1
    assert not markers


def test_main_keyboard_interrupt_after_job_writes_marker_when_cleanup_is_verified(tmp_path, monkeypatch):
    def interrupt_launch(*args, **kwargs):
        raise KeyboardInterrupt

    repo, kernel32, markers = prepare_main_teardown_test(tmp_path, monkeypatch, interrupt_launch)
    monkeypatch.setattr(drive, "get_job_pids", lambda job: {101})
    monkeypatch.setattr(drive, "pid_is_gone", lambda pid: True)
    monkeypatch.setattr(drive, "run_netstat", lambda: "")

    assert drive.main([str(repo), "smoke"]) == 2
    assert kernel32.closed.count("job") == 1
    assert markers


@pytest.mark.parametrize("interrupt_at", ["window lookup", "survivor wait", "netstat call"])
def test_main_keyboard_interrupt_in_close_verifies_known_processes(tmp_path, monkeypatch, interrupt_at):
    repo, kernel32, markers = prepare_main_teardown_test(
        tmp_path, monkeypatch, lambda *args, **kwargs: (101, "app-process")
    )
    monkeypatch.setattr(drive, "get_job_pids", lambda job: {101})
    monkeypatch.setattr(drive, "verify_listener", lambda *args: (True, None))
    monkeypatch.setattr(drive, "_wait_for_page_target", lambda *args: {"webSocketDebuggerUrl": "ws://test"})
    monkeypatch.setattr(drive, "collect_scenario_results", lambda *args: {"checks": [], "error": None})
    monkeypatch.setattr(drive, "capture_whole_window", lambda *args: True)
    monkeypatch.setattr(drive, "pid_is_gone", lambda pid: True)
    process_checks = []
    monkeypatch.setattr(drive, "process_is_running", lambda handle: process_checks.append(handle) and False)
    monkeypatch.setattr(drive.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"stdout": "", "stderr": "", "returncode": 0})())

    if interrupt_at == "window lookup":
        lookups = iter([None, KeyboardInterrupt()])

        def find_window(*args):
            value = next(lookups)
            if isinstance(value, BaseException):
                raise value
            return value

        monkeypatch.setattr(drive, "find_window_for_pids", find_window)
        monkeypatch.setattr(drive, "run_netstat", lambda: "")
    elif interrupt_at == "survivor wait":
        monkeypatch.setattr(drive, "find_window_for_pids", lambda *args: 1)
        waits = 0

        def wait_for(predicate, *args):
            nonlocal waits
            waits += 1
            if waits == 3:
                raise KeyboardInterrupt
            return predicate()

        monkeypatch.setattr(drive, "_wait_for", wait_for)
        monkeypatch.setattr(drive, "run_netstat", lambda: "")
    else:
        monkeypatch.setattr(drive, "find_window_for_pids", lambda *args: 1)
        netstat_calls = 0

        def run_netstat():
            nonlocal netstat_calls
            netstat_calls += 1
            if netstat_calls == 2:
                raise KeyboardInterrupt
            return ""

        monkeypatch.setattr(drive, "run_netstat", run_netstat)

    assert drive.main([str(repo), "smoke"]) == 2
    assert kernel32.closed.count("job") == 1
    assert process_checks
    assert markers


def test_report_cleanup_after_error_checks_known_pids_without_an_open_job(monkeypatch):
    checked_pids = []
    monkeypatch.setattr(drive, "pid_is_gone", lambda pid: checked_pids.append(pid) or True)

    assert drive._report_cleanup_after_error(None, None, {101}, []) == (None, True)
    assert checked_pids


@pytest.mark.parametrize("interrupt_at", ["job snapshot", "survivor wait"])
def test_report_cleanup_after_error_second_interrupt_closes_job_once(monkeypatch, interrupt_at):
    kernel32 = FakeKernel32()
    monkeypatch.setattr(drive, "kernel32", kernel32)

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    if interrupt_at == "job snapshot":
        monkeypatch.setattr(drive, "get_job_pids", interrupt)
    else:
        monkeypatch.setattr(drive, "get_job_pids", lambda job: {101})
        monkeypatch.setattr(drive, "_wait_for", interrupt)

    assert drive._report_cleanup_after_error("job", 41000, {101}, []) == (None, False)
    assert kernel32.closed == ["job"]


def test_clear_readonly_via_handle_refuses_attribute_update_failure(monkeypatch):
    monkeypatch.setattr(drive, "_handle_attributes", lambda handle: (drive.FILE_ATTRIBUTE_READONLY, 0))
    monkeypatch.setattr(drive.kernel32, "SetFileInformationByHandle", lambda *args: False)
    monkeypatch.setattr(drive.ctypes, "get_last_error", lambda: 5)

    with pytest.raises(drive.SetupError, match="SetFileInformationByHandle failed"):
        drive._clear_readonly_via_handle(123)


# --------------------------------------------------------------------------
# copy_repo_to: the throwaway copy of the app repo
# --------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> None:
    import subprocess

    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_test_git_repo(tmp_path: Path):
    """A real git repo with a tracked file, a gitignored file, and an
    untracked-but-not-ignored file, all inside a subfolder ("app") of the
    repo root, so copy_repo_to can be exercised against a repo that is
    itself a subfolder of a larger git work tree (as ui_drive/example is)."""
    root = tmp_path / "gitroot"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    sub = root / "app"
    sub.mkdir()
    (sub / "tracked.txt").write_text("tracked", encoding="utf-8")
    (sub / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (sub / "ignored.txt").write_text("ignored", encoding="utf-8")
    (sub / "untracked.txt").write_text("untracked", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root, sub


def test_copy_repo_to_selects_tracked_and_untracked_not_ignored(tmp_path):
    _root, sub = make_test_git_repo(tmp_path)
    dest = tmp_path / "dest"
    dest.mkdir()
    drive.copy_repo_to(sub, dest)
    assert (dest / "tracked.txt").read_text(encoding="utf-8") == "tracked"
    assert (dest / "untracked.txt").read_text(encoding="utf-8") == "untracked"
    assert (dest / ".gitignore").is_file()
    assert not (dest / "ignored.txt").exists()


def test_copy_repo_to_skips_deleted_tracked_file(tmp_path):
    _root, sub = make_test_git_repo(tmp_path)
    (sub / "tracked.txt").unlink()
    dest = tmp_path / "dest"
    dest.mkdir()
    drive.copy_repo_to(sub, dest)  # must not raise
    assert not (dest / "tracked.txt").exists()
    assert (dest / "untracked.txt").exists()


def test_copy_repo_to_refuses_symlink(tmp_path):
    _root, sub = make_test_git_repo(tmp_path)
    outside = tmp_path / "outside_target.txt"
    outside.write_text("secret", encoding="utf-8")
    link = sub / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available in this environment")
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(drive.SetupError, match="symlink"):
        drive.copy_repo_to(sub, dest)


def test_copy_repo_to_refuses_path_resolving_outside_repo(tmp_path, monkeypatch):
    _root, sub = make_test_git_repo(tmp_path)
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("secret", encoding="utf-8")
    link_dir = sub / "linked_dir"
    try:
        link_dir.symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available in this environment")
    monkeypatch.setattr(drive, "list_repo_files", lambda repo: ["linked_dir/secret.txt"])
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(drive.SetupError, match="outside"):
        drive.copy_repo_to(sub, dest)


def test_copy_repo_to_works_for_a_subfolder_repo(tmp_path):
    # make_test_git_repo already puts the "repo" ui_drive is pointed at
    # (sub) inside a larger git work tree (root); this just asserts that
    # setup didn't secretly need root to be the repo argument.
    _root, sub = make_test_git_repo(tmp_path)
    dest = tmp_path / "dest"
    dest.mkdir()
    drive.copy_repo_to(sub, dest)
    assert (dest / "tracked.txt").is_file()


def test_copy_repo_to_rejects_non_git_repo(tmp_path):
    not_a_repo = tmp_path / "plain_folder"
    not_a_repo.mkdir()
    (not_a_repo / "file.txt").write_text("x", encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(drive.SetupError, match="not inside a git work tree"):
        drive.copy_repo_to(not_a_repo, dest)


# --------------------------------------------------------------------------
# drive.py cleanup <run folder>
# --------------------------------------------------------------------------

VALID_RUN_NAME = "ui-drive-" + "a" * 32


def make_fake_run_folder(temp_root: Path, name: str = VALID_RUN_NAME, verified: bool = True) -> Path:
    run_dir = temp_root / name
    run_dir.mkdir()
    marker = {"run_id": "abc123", "path": str(run_dir.resolve())}
    (run_dir / drive.RUN_MARKER_NAME).write_text(json.dumps(marker), encoding="utf-8")
    if verified:
        (run_dir / drive.TEARDOWN_MARKER_NAME).write_text("", encoding="utf-8")
    (run_dir / "results.json").write_text("{}", encoding="utf-8")
    return run_dir


def test_cleanup_happy_path_removes_the_folder(tmp_path, monkeypatch):
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(drive.tempfile, "gettempdir", lambda: str(temp_root))
    run_dir = make_fake_run_folder(temp_root)
    code = drive.main(["cleanup", str(run_dir)])
    assert code == 0
    assert not run_dir.exists()


def test_cleanup_refuses_when_teardown_not_verified(tmp_path, monkeypatch):
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(drive.tempfile, "gettempdir", lambda: str(temp_root))
    run_dir = make_fake_run_folder(temp_root, verified=False)
    code = drive.main(["cleanup", str(run_dir)])
    assert code == 2
    assert run_dir.exists()


def test_cleanup_refuses_when_outside_temp_dir(tmp_path, monkeypatch):
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(drive.tempfile, "gettempdir", lambda: str(temp_root))
    elsewhere = tmp_path / "elsewhere" / "ui-drive-xyz"
    elsewhere.mkdir(parents=True)
    marker = {"run_id": "xyz", "path": str(elsewhere.resolve())}
    (elsewhere / drive.RUN_MARKER_NAME).write_text(json.dumps(marker), encoding="utf-8")
    (elsewhere / drive.TEARDOWN_MARKER_NAME).write_text("", encoding="utf-8")
    code = drive.main(["cleanup", str(elsewhere)])
    assert code == 2
    assert elsewhere.exists()


def test_cleanup_refuses_when_name_does_not_match_pattern(tmp_path, monkeypatch):
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(drive.tempfile, "gettempdir", lambda: str(temp_root))
    run_dir = make_fake_run_folder(temp_root, name="not-a-ui-drive-folder")
    code = drive.main(["cleanup", str(run_dir)])
    assert code == 2
    assert run_dir.exists()


def test_cleanup_refuses_when_run_marker_missing(tmp_path, monkeypatch):
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(drive.tempfile, "gettempdir", lambda: str(temp_root))
    run_dir = make_fake_run_folder(temp_root)
    (run_dir / drive.RUN_MARKER_NAME).unlink()
    code = drive.main(["cleanup", str(run_dir)])
    assert code == 2
    assert run_dir.exists()


def test_cleanup_refuses_when_run_marker_path_mismatches(tmp_path, monkeypatch):
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(drive.tempfile, "gettempdir", lambda: str(temp_root))
    run_dir = make_fake_run_folder(temp_root)
    (run_dir / drive.RUN_MARKER_NAME).write_text(
        json.dumps({"run_id": "abc123", "path": "C:\\somewhere\\else"}), encoding="utf-8"
    )
    code = drive.main(["cleanup", str(run_dir)])
    assert code == 2
    assert run_dir.exists()


def test_cleanup_refuses_when_teardown_marker_missing(tmp_path, monkeypatch):
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(drive.tempfile, "gettempdir", lambda: str(temp_root))
    run_dir = make_fake_run_folder(temp_root, verified=False)
    code = drive.main(["cleanup", str(run_dir)])
    assert code == 2
    assert run_dir.exists()


def test_cleanup_refuses_when_path_does_not_exist(tmp_path, monkeypatch):
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(drive.tempfile, "gettempdir", lambda: str(temp_root))
    code = drive.main(["cleanup", str(temp_root / "ui-drive-does-not-exist")])
    assert code == 2


def test_cleanup_refuses_when_path_is_a_symlink(tmp_path, monkeypatch):
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(drive.tempfile, "gettempdir", lambda: str(temp_root))
    real_run_dir = make_fake_run_folder(temp_root, name="ui-drive-real")
    link = temp_root / "ui-drive-link"
    try:
        link.symlink_to(real_run_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available in this environment")
    code = drive.main(["cleanup", str(link)])
    assert code == 2
    assert real_run_dir.exists()


def test_cleanup_refuses_when_lock_is_held(tmp_path, monkeypatch):
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(drive.tempfile, "gettempdir", lambda: str(temp_root))
    run_dir = make_fake_run_folder(temp_root)
    lock_fd = drive.acquire_run_lock(run_dir)
    try:
        code = drive.main(["cleanup", str(run_dir)])
        assert code == 2
        assert run_dir.exists()
    finally:
        drive.release_run_lock(lock_fd)


def test_cleanup_lock_race_cannot_overwrite_swapped_symlink_target(tmp_path, monkeypatch):
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    run_dir = make_fake_run_folder(temp_root)
    lock_path = run_dir / drive.LOCK_FILE_NAME
    lock_path.write_bytes(b"lock")
    external_target = tmp_path / "external.txt"
    external_target.write_bytes(b"external contents")

    original_attributes = drive._handle_attributes
    swapped = False
    swap_blocked = False

    def swap_after_probe(handle):
        nonlocal swapped, swap_blocked
        attrs = original_attributes(handle)
        if not swapped:
            swapped = True
            try:
                lock_path.unlink()
            except PermissionError:
                swap_blocked = True
                return attrs
            try:
                lock_path.symlink_to(external_target)
            except OSError:
                pytest.skip("file symlinks are not available in this environment (needs a privilege this account lacks)")
        return attrs

    monkeypatch.setattr(drive, "_handle_attributes", swap_after_probe)
    try:
        fd = drive.acquire_run_lock(run_dir, create=False)
    finally:
        if "fd" in locals():
            drive.release_run_lock(fd)

    assert external_target.read_bytes() == b"external contents"
    assert swap_blocked


def test_cleanup_removes_junction_without_deleting_its_target(tmp_path, monkeypatch):
    import subprocess

    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(drive.tempfile, "gettempdir", lambda: str(temp_root))
    run_dir = make_fake_run_folder(temp_root)

    target = tmp_path / "junction_target"
    target.mkdir()
    (target / "keep.txt").write_text("keep me", encoding="utf-8")

    junction = run_dir / "linked"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"junction creation is not available in this environment: {result.stderr}")

    code = drive.main(["cleanup", str(run_dir)])
    assert code == 0
    assert not run_dir.exists()
    assert target.is_dir()
    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep me"


def test_cleanup_refuses_when_root_itself_is_a_junction(tmp_path, monkeypatch):
    import subprocess

    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(drive.tempfile, "gettempdir", lambda: str(temp_root))

    target = tmp_path / "junction_root_target"
    target.mkdir()
    marker = {"run_id": "x", "path": str((temp_root / VALID_RUN_NAME).resolve())}
    (target / drive.RUN_MARKER_NAME).write_text(json.dumps(marker), encoding="utf-8")
    (target / drive.TEARDOWN_MARKER_NAME).write_text("", encoding="utf-8")

    junction = temp_root / VALID_RUN_NAME
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"junction creation is not available in this environment: {result.stderr}")

    code = drive.main(["cleanup", str(junction)])
    assert code == 2
    assert target.exists()
    assert (target / drive.RUN_MARKER_NAME).is_file()


def test_cleanup_removes_file_symlink_without_deleting_its_target(tmp_path, monkeypatch):
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(drive.tempfile, "gettempdir", lambda: str(temp_root))
    run_dir = make_fake_run_folder(temp_root)

    target_dir = tmp_path / "symlink_target_dir"
    target_dir.mkdir()
    target_file = target_dir / "keep.txt"
    target_file.write_text("keep me too", encoding="utf-8")

    link = run_dir / "linked.txt"
    try:
        link.symlink_to(target_file)
    except OSError:
        pytest.skip("file symlinks are not available in this environment (needs a privilege this account lacks)")

    code = drive.main(["cleanup", str(run_dir)])
    assert code == 0
    assert not run_dir.exists()
    assert target_file.read_text(encoding="utf-8") == "keep me too"

    import shutil as _shutil

    _shutil.rmtree(target_dir)


# --------------------------------------------------------------------------
# exclusive_create: files the driver writes must never silently overwrite
# an existing file or a link planted at that name
# --------------------------------------------------------------------------

def test_exclusive_create_refuses_existing_file(tmp_path):
    path = tmp_path / "existing.txt"
    path.write_text("already here", encoding="utf-8")
    with pytest.raises(drive.SetupError, match="existing"):
        drive.exclusive_create(path)
    assert path.read_text(encoding="utf-8") == "already here"


def test_exclusive_create_refuses_existing_link(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("target contents", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are not available in this environment")
    with pytest.raises(drive.SetupError, match="existing"):
        drive.exclusive_create(link)
    assert target.read_text(encoding="utf-8") == "target contents"


def test_exclusive_create_succeeds_for_a_new_path(tmp_path):
    path = tmp_path / "new.txt"
    fd = drive.exclusive_create(path)
    try:
        import os as _os

        _os.write(fd, b"hello")
    finally:
        import os as _os

        _os.close(fd)
    assert path.read_text(encoding="utf-8") == "hello"


# --------------------------------------------------------------------------
# copy_repo_to: destination escape and a pre-planted junction in the
# destination are both refused
# --------------------------------------------------------------------------

def test_copy_repo_to_refuses_dotdot_relative_path(tmp_path, monkeypatch):
    _root, sub = make_test_git_repo(tmp_path)
    (sub.parent / "outside.txt").write_text("secret", encoding="utf-8")
    monkeypatch.setattr(drive, "list_repo_files", lambda repo: ["../outside.txt"])
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(drive.SetupError, match="outside"):
        drive.copy_repo_to(sub, dest)


def test_copy_repo_to_refuses_preplanted_junction_in_destination(tmp_path, monkeypatch):
    import subprocess

    _root, sub = make_test_git_repo(tmp_path)
    (sub / "sub").mkdir()
    (sub / "sub" / "tracked2.txt").write_text("tracked2", encoding="utf-8")
    _git(sub, "add", "-A")
    _git(sub, "commit", "-q", "-m", "add sub")

    dest = tmp_path / "dest"
    dest.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    junction = dest / "sub"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(elsewhere)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"junction creation is not available in this environment: {result.stderr}")

    # A junction resolves through to its target, so this is refused either
    # as a destination escape (the target sits outside dest) or, if the
    # target happened to sit inside dest, as an unexpected reparse point;
    # either way, nothing gets written through it.
    with pytest.raises(drive.SetupError, match="outside the destination|reparse point"):
        drive.copy_repo_to(sub, dest)
    assert not (elsewhere / "tracked2.txt").exists()


# --------------------------------------------------------------------------
# cdp.mjs screenshot-name validation (exercised through node, not pytest,
# since it is JS logic)
# --------------------------------------------------------------------------

def test_cdp_screenshot_name_validation():
    import shutil as _shutil
    import subprocess

    node = _shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")

    cdp_url = (REPO_ROOT / "ui_drive" / "cdp.mjs").as_uri()
    script = (
        f"import('{cdp_url}').then(m => {{"
        "const cases = [['good_name-1', true], ['bad/name', false], ['', false], "
        "['a'.repeat(64), true], ['a'.repeat(65), false]];"
        "const failed = cases.filter(([name, expected]) => m.isValidScreenshotName(name) !== expected);"
        "if (failed.length) { console.error(JSON.stringify(failed)); process.exit(1); }"
        "process.exit(0);"
        "});"
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("case", "out_dir_name", "temp_name", "error_text"),
    [
        ("mismatched-out-dir", "ui-drive-" + "b" * 32, "tmp", "private temp folder"),
        ("bad-name", "not-a-ui-drive-folder", "tmp", "does not look like"),
        ("wrong-temp-basename", "ui-drive-" + "c" * 32, "not-tmp", "private temp folder"),
    ],
)
def test_cdp_refuses_invalid_out_dir_before_connecting(tmp_path, case, out_dir_name, temp_name, error_text):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")

    run_dir = tmp_path / out_dir_name
    run_dir.mkdir()
    temp_parent = run_dir
    if case == "mismatched-out-dir":
        temp_parent = tmp_path / ("ui-drive-" + "d" * 32)
        temp_parent.mkdir()
    temp_dir = temp_parent / temp_name
    temp_dir.mkdir()
    request = {
        "webSocketDebuggerUrl": "ws://127.0.0.1:1/devtools/page/not-connected",
        "scenarioPath": str(tmp_path / "scenario.mjs"),
        "outDir": str(run_dir),
    }
    env = os.environ.copy()
    env["TEMP"] = str(temp_dir)
    env["TMP"] = str(temp_dir)

    result = subprocess.run(
        [node, str(REPO_ROOT / "ui_drive" / "cdp.mjs")],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )

    assert result.returncode != 0
    assert error_text in result.stderr
    assert not (run_dir / "results.json").exists()
