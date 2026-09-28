"""Drive a real pywebview (Qt) app window from source and smoke test it.

Launches the app the same way a user does (from its own virtual environment,
in its own repo), turns on QtWebEngine's remote debugging port, and drives the
page over the Chrome DevTools Protocol (CDP) through a small Node helper
(cdp.mjs). The app's real Python bridge answers every call, because this is
the real window, not a headless stand-in.

Usage:
    <app repo>\\.venv\\Scripts\\python.exe drive.py <app repo> <scenario name>

See README.md for the manifest format, the scenario format, and what this
does not cover. Windows only.
"""

from __future__ import annotations

import ctypes
import json
import msvcrt
import os
import random
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
RUN_MARKER_NAME = ".ui-drive-run"
TEARDOWN_MARKER_NAME = ".teardown-complete"
LOCK_FILE_NAME = ".ui-drive.lock"

HERE = Path(__file__).resolve().parent

PORT_MIN = 20000
PORT_MAX = 60000

LAUNCH_TIMEOUT_S = 30       # waiting for the debug port, the page target, and the fixture's JSON line
CLOSE_TIMEOUT_S = 15        # waiting for the app to exit after WM_CLOSE
SURVIVOR_TIMEOUT_S = 5      # waiting, after the job is closed, for its processes to actually be gone

DEFAULT_SCENARIO_TIMEOUT_S = 60
MIN_SCENARIO_TIMEOUT_S = 10
MAX_SCENARIO_TIMEOUT_S = 600


class ManifestError(Exception):
    """A problem with ui_drive.json or how it was invoked. Always a plain,
    one-line message: this is shown straight to the person running the
    command, not just logged."""


class SetupError(Exception):
    """A problem launching, verifying, or tearing down the app. Same
    plain-message rule as ManifestError."""


# --------------------------------------------------------------------------
# Manifest loading and validation (no I/O beyond reading the manifest file;
# fully testable without launching anything)
# --------------------------------------------------------------------------

REQUIRED_MANIFEST_KEYS = ("entry", "mutex", "window_title", "scenarios")


def _reject_unsafe_relative_path(rel: str, what: str) -> None:
    """Raises if rel looks like it could escape the repo: absolute paths and
    any ".." segment are rejected outright, before we even try to resolve
    them against the repo root."""
    if not isinstance(rel, str) or not rel:
        raise ManifestError(f"{what} must be a non-empty string")
    if os.path.isabs(rel) or re.match(r"^[A-Za-z]:[\\/]", rel) or rel.startswith(("\\\\", "//")):
        raise ManifestError(f"{what} must be a repo-relative path, not absolute: {rel!r}")
    parts = re.split(r"[\\/]+", rel)
    if any(p == ".." for p in parts):
        raise ManifestError(f"{what} must not contain '..': {rel!r}")


def resolve_repo_relative(repo: Path, rel: str, what: str) -> Path:
    """Turns a manifest path into an absolute path, guaranteed to sit inside
    repo. Raises ManifestError (plain message) on anything else: absolute
    input, a ".." segment, or a path that resolves outside the repo (e.g. via
    a symlink)."""
    _reject_unsafe_relative_path(rel, what)
    repo_resolved = repo.resolve()
    candidate = (repo_resolved / rel).resolve()
    try:
        candidate.relative_to(repo_resolved)
    except ValueError:
        raise ManifestError(f"{what} resolves outside the app repo: {rel!r}") from None
    return candidate


def load_manifest(repo: Path) -> dict:
    manifest_path = repo / "tools" / "ui_check" / "ui_drive.json"
    if not manifest_path.is_file():
        raise ManifestError(f"no manifest at {manifest_path}")
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"could not read {manifest_path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{manifest_path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"{manifest_path} must contain a JSON object")
    return data


def validate_manifest(repo: Path, manifest: dict, scenario_name: str) -> dict:
    """Checks the manifest is well formed and the requested scenario exists,
    then returns a plan with every path resolved to an absolute Path inside
    the repo. Raises ManifestError with a plain message on the first problem
    found."""
    for key in REQUIRED_MANIFEST_KEYS:
        if key not in manifest:
            raise ManifestError(f"manifest is missing required key {key!r}")

    entry_path = resolve_repo_relative(repo, manifest["entry"], "entry")

    mutex = manifest["mutex"]
    if not isinstance(mutex, str) or not mutex:
        raise ManifestError("mutex must be a non-empty string")

    window_title = manifest["window_title"]
    if not isinstance(window_title, str) or not window_title:
        raise ManifestError("window_title must be a non-empty string")

    page_url_contains = manifest.get("page_url_contains")
    if page_url_contains is not None and not isinstance(page_url_contains, str):
        raise ManifestError("page_url_contains must be a string when present")

    scenarios = manifest["scenarios"]
    if not isinstance(scenarios, dict) or not scenarios:
        raise ManifestError("scenarios must be a non-empty object")
    if scenario_name not in scenarios:
        known = ", ".join(sorted(scenarios)) or "(none)"
        raise ManifestError(f"unknown scenario {scenario_name!r}; known scenarios: {known}")

    entry_scenario = scenarios[scenario_name]
    if not isinstance(entry_scenario, dict) or "script" not in entry_scenario:
        raise ManifestError(f"scenario {scenario_name!r} must be an object with a 'script' key")

    script_path = resolve_repo_relative(repo, entry_scenario["script"], f"scenarios.{scenario_name}.script")

    fixture_path = None
    if entry_scenario.get("fixture") is not None:
        fixture_path = resolve_repo_relative(
            repo, entry_scenario["fixture"], f"scenarios.{scenario_name}.fixture"
        )

    timeout_s = entry_scenario.get("timeout_s", DEFAULT_SCENARIO_TIMEOUT_S)
    if not isinstance(timeout_s, int) or isinstance(timeout_s, bool):
        raise ManifestError(f"scenarios.{scenario_name}.timeout_s must be a whole number of seconds")
    if not (MIN_SCENARIO_TIMEOUT_S <= timeout_s <= MAX_SCENARIO_TIMEOUT_S):
        raise ManifestError(
            f"scenarios.{scenario_name}.timeout_s must be between {MIN_SCENARIO_TIMEOUT_S} and "
            f"{MAX_SCENARIO_TIMEOUT_S}, got {timeout_s}"
        )

    return {
        "entry_path": entry_path,
        "mutex": mutex,
        "window_title": window_title,
        "page_url_contains": page_url_contains,
        "script_path": script_path,
        "fixture_path": fixture_path,
        "timeout_s": timeout_s,
    }


# --------------------------------------------------------------------------
# Throwaway copy of the app repo. The app is only ever read from its real
# repo; everything a run touches (settings, databases, sample data, logs,
# screenshots) lives under the run folder instead.
# --------------------------------------------------------------------------

def is_reparse_point(path: Path) -> bool:
    """True for a symlink, junction, or any other NTFS reparse point. Uses
    lstat (does not follow the link) so this also catches a broken link."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def list_repo_files(repo: Path) -> list[str]:
    """Returns every file `git` considers part of the working tree: tracked
    files plus untracked-but-not-ignored ones, repo-relative. Raises
    SetupError if repo is not inside a git work tree at all."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise SetupError(f"could not run git to list {repo}: {exc}") from exc
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise SetupError(f"{repo} is not inside a git work tree (git ls-files failed: {stderr})")
    raw = result.stdout.decode("utf-8", errors="surrogateescape")
    return [p for p in raw.split("\0") if p]


def copy_repo_to(repo: Path, app_dir: Path) -> None:
    """Copies the repo's working-tree files into app_dir, preserving relative
    layout. Skips a listed path that no longer exists on disk (a tracked file
    that was since deleted). Refuses, before copying anything, any entry that
    is a symlink/reparse point or whose resolved path falls outside the repo."""
    repo_resolved = repo.resolve()
    files = list_repo_files(repo)
    for rel in files:
        src = repo / rel
        if is_reparse_point(src):
            raise SetupError(f"refusing to copy a symlink/reparse point from the repo: {rel}")
        if not src.exists():
            continue
        if not src.is_file():
            continue
        resolved = src.resolve()
        try:
            resolved.relative_to(repo_resolved)
        except ValueError:
            raise SetupError(f"refusing to copy a path that resolves outside the repo: {rel}")
        dest = app_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def repo_path_to_copy(repo: Path, app_dir: Path, absolute_path: Path) -> Path:
    """Maps a path already resolved inside repo (as validate_manifest
    returns) to the matching path inside the copy at app_dir."""
    rel = absolute_path.relative_to(repo.resolve())
    return app_dir / rel


# --------------------------------------------------------------------------
# Run folder: creation, the run marker, the lifetime lock, and the teardown
# marker that only appears once cleanup is verified.
# --------------------------------------------------------------------------

def create_run_dir() -> Path:
    run_dir = Path(tempfile.mkdtemp(prefix="ui-drive-", dir=tempfile.gettempdir())).resolve()
    marker = {"run_id": secrets.token_hex(16), "path": str(run_dir)}
    (run_dir / RUN_MARKER_NAME).write_text(json.dumps(marker), encoding="utf-8")
    return run_dir


def acquire_run_lock(run_dir: Path) -> int:
    """Opens and exclusively locks <run_dir>\\.ui-drive.lock for the life of
    this process. The handle is kept open (and so the lock held) until
    release_run_lock is called; `cleanup` later uses the same lock to refuse
    to delete a run folder a live drive.py is still using."""
    lock_path = run_dir / LOCK_FILE_NAME
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT | os.O_BINARY)
    try:
        os.write(fd, b"0")
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        os.close(fd)
        raise SetupError(f"could not lock {lock_path}: {exc}") from exc
    return fd


def release_run_lock(fd: int | None) -> None:
    if fd is None:
        return
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_teardown_marker(run_dir: Path) -> None:
    (run_dir / TEARDOWN_MARKER_NAME).write_text("", encoding="utf-8")


def base_child_env(base_env: dict, run_dir: Path) -> dict:
    """Environment additions common to the app and its fixture: no .pyc
    files left behind, and a private TEMP/TMP under the run folder so
    anything either process writes to "the temp folder" lands inside the
    run rather than the user's real one. Pure and testable without
    launching anything."""
    env = dict(base_env)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    tmp_dir = str(run_dir / "tmp")
    env["TEMP"] = tmp_dir
    env["TMP"] = tmp_dir
    return env


def fixture_env(base_env: dict, run_dir: Path, app_dir: Path) -> dict:
    """Returns base_child_env(base_env, run_dir) plus UI_DRIVE_OUT_DIR
    pointing at the run folder, so a fixture has somewhere to put its own
    throwaway files, and UI_DRIVE_APP_DIR pointing at the copied app, so a
    fixture can pre-place sample data next to it before the app launches. A
    fixture is killed with the job and never gets a chance to clean up after
    itself, so anything it writes under the run folder is deleted along with
    the rest of the run's output. Pure and testable without launching
    anything."""
    env = base_child_env(base_env, run_dir)
    env["UI_DRIVE_OUT_DIR"] = str(run_dir)
    env["UI_DRIVE_APP_DIR"] = str(app_dir)
    return env


# --------------------------------------------------------------------------
# Free port selection
# --------------------------------------------------------------------------

def pick_port(rng: random.Random | None = None) -> int:
    """Returns a random port in the reserved scan range. Pure and testable;
    does not check availability (see find_free_port)."""
    rng = rng or random
    return rng.randint(PORT_MIN, PORT_MAX)


def is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def find_free_port(attempts: int = 50, rng: random.Random | None = None) -> int:
    for _ in range(attempts):
        port = pick_port(rng)
        if is_port_free(port):
            return port
    raise SetupError(f"could not find a free port in {PORT_MIN}-{PORT_MAX} after {attempts} attempts")


# --------------------------------------------------------------------------
# netstat parsing and listener verification (pure string processing, fully
# testable without a real listener)
# --------------------------------------------------------------------------

_NETSTAT_LINE = re.compile(
    r"^\s*(?P<proto>TCP|TCPv6)\s+(?P<local>\S+)\s+(?P<remote>\S+)\s+(?P<state>\S+)\s+(?P<pid>\d+)\s*$"
)


def parse_netstat_lines(output: str) -> list[dict]:
    """Parses the table `netstat -ano` prints. Only TCP/TCPv6 rows with a
    trailing PID are kept; header and blank lines are skipped silently."""
    rows = []
    for line in output.splitlines():
        m = _NETSTAT_LINE.match(line)
        if not m:
            continue
        local = m.group("local")
        if local.count(":") > 1:
            # IPv6, e.g. [::1]:9333 or [::]:9333
            addr, _, port_str = local.rpartition(":")
            addr = addr.strip("[]")
        else:
            addr, _, port_str = local.rpartition(":")
        try:
            port = int(port_str)
        except ValueError:
            continue
        rows.append(
            {
                "proto": m.group("proto"),
                "local_addr": addr,
                "local_port": port,
                "state": m.group("state"),
                "pid": int(m.group("pid")),
            }
        )
    return rows


LOOPBACK_ADDRS = {"127.0.0.1"}


def verify_listener(port: int, netstat_output: str, allowed_pids: set[int]) -> tuple[bool, str]:
    """Checks that the only LISTENING entry on `port` is loopback-only
    (127.0.0.1) and owned by one of allowed_pids. Any non-loopback address on
    that port (0.0.0.0, ::, [::]) or a listener owned by an unknown PID fails
    the check. Returns (ok, reason)."""
    rows = [r for r in parse_netstat_lines(netstat_output) if r["local_port"] == port and r["state"] == "LISTENING"]
    if not rows:
        return False, f"nothing is listening on port {port} yet"
    for row in rows:
        if row["local_addr"] not in LOOPBACK_ADDRS:
            return False, f"port {port} is bound on {row['local_addr']}, not loopback-only"
        if row["pid"] not in allowed_pids:
            return False, f"port {port} is owned by PID {row['pid']}, not by this run"
    return True, "ok"


# --------------------------------------------------------------------------
# Windows primitives: mutex check, job objects, suspended launch, window
# lookup, WM_CLOSE. All via ctypes; none of this is unit-testable without a
# real Windows session, so it stays out of the pure-logic functions above.
# --------------------------------------------------------------------------

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

ERROR_ALREADY_EXISTS = 183
STILL_ACTIVE = 259

CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400

STARTF_USESTDHANDLES = 0x00000100
HANDLE_FLAG_INHERIT = 0x00000001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

JobObjectExtendedLimitInformation = 9
JobObjectBasicProcessIdList = 3
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

WM_CLOSE = 0x0010


class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_char_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
kernel32.SetInformationJobObject.restype = wintypes.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
kernel32.QueryInformationJobObject.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryInformationJobObject.restype = wintypes.BOOL
kernel32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.LPCWSTR,
    ctypes.POINTER(STARTUPINFO),
    ctypes.POINTER(PROCESS_INFORMATION),
]
kernel32.CreateProcessW.restype = wintypes.BOOL
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
kernel32.SetHandleInformation.restype = wintypes.BOOL
ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
ntdll.NtResumeProcess.restype = ctypes.c_long
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int


def _job_pid_list_type(max_pids: int):
    class JOBOBJECT_BASIC_PROCESS_ID_LIST(ctypes.Structure):
        _fields_ = [
            ("NumberOfAssignedProcesses", wintypes.DWORD),
            ("NumberOfProcessIdsInList", wintypes.DWORD),
            ("ProcessIdList", ctypes.c_size_t * max_pids),
        ]

    return JOBOBJECT_BASIC_PROCESS_ID_LIST


def get_job_pids(job: wintypes.HANDLE, max_pids: int = 64) -> set[int]:
    """Returns every process ID currently assigned to the job (the launched
    app plus any child it has spawned, e.g. QtWebEngineProcess.exe), which is
    what "owned by this run" actually means once a browser child is in the
    picture. Grows the buffer and retries if the job somehow holds more than
    max_pids processes."""
    list_type = _job_pid_list_type(max_pids)
    info = list_type()
    ok = kernel32.QueryInformationJobObject(
        job, JobObjectBasicProcessIdList, ctypes.byref(info), ctypes.sizeof(info), None
    )
    if not ok:
        if max_pids < 4096:
            return get_job_pids(job, max_pids * 4)
        raise SetupError(f"QueryInformationJobObject failed: {ctypes.get_last_error()}")
    count = min(info.NumberOfProcessIdsInList, max_pids)
    return {int(info.ProcessIdList[i]) for i in range(count)}


def mutex_exists(name: str) -> bool:
    """True if a named mutex with this name is already held by some process
    in this Windows session (i.e. the app is already running)."""
    SYNCHRONIZE = 0x00100000
    handle = kernel32.OpenMutexW(SYNCHRONIZE, False, name)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def create_job_with_kill_on_close() -> wintypes.HANDLE:
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise SetupError(f"CreateJobObjectW failed: {ctypes.get_last_error()}")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(
        job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    )
    if not ok:
        kernel32.CloseHandle(job)
        raise SetupError(f"SetInformationJobObject failed: {ctypes.get_last_error()}")
    return job


def launch_suspended_in_job(
    job: wintypes.HANDLE,
    argv: list[str],
    cwd: Path,
    env: dict,
    stdout_handle: wintypes.HANDLE | None = None,
    stderr_handle: wintypes.HANDLE | None = None,
) -> tuple[int, wintypes.HANDLE]:
    """Creates argv[0] argv[1:] suspended, assigns it to job before it can run
    a single instruction (so it can never spawn a child outside the job, and
    can never survive the job closing), then resumes it via NtResumeProcess.
    Returns (pid, hProcess); hProcess is kept open so the caller can wait on
    / query it later.

    When stdout_handle / stderr_handle are given (must be inheritable), the
    child's output is redirected straight to them instead of inheriting this
    process's console, which is how the fixture's log files are captured
    without a pipe that could fill up and stall it."""
    startup_info = STARTUPINFO()
    startup_info.cb = ctypes.sizeof(STARTUPINFO)
    process_info = PROCESS_INFORMATION()

    inherit_handles = False
    if stdout_handle is not None or stderr_handle is not None:
        startup_info.dwFlags |= STARTF_USESTDHANDLES
        startup_info.hStdInput = wintypes.HANDLE(0)
        startup_info.hStdOutput = stdout_handle if stdout_handle is not None else wintypes.HANDLE(0)
        startup_info.hStdError = stderr_handle if stderr_handle is not None else wintypes.HANDLE(0)
        inherit_handles = True

    cmdline = subprocess.list2cmdline(argv)
    cmdline_buf = ctypes.create_unicode_buffer(cmdline)
    env_block = "\0".join(f"{k}={v}" for k, v in env.items()) + "\0\0"
    env_buf = ctypes.create_unicode_buffer(env_block)

    ok = kernel32.CreateProcessW(
        None,
        cmdline_buf,
        None,
        None,
        inherit_handles,
        CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
        ctypes.cast(env_buf, ctypes.c_void_p),
        str(cwd),
        ctypes.byref(startup_info),
        ctypes.byref(process_info),
    )
    if not ok:
        raise SetupError(f"CreateProcessW failed for {argv[0]}: {ctypes.get_last_error()}")

    h_process = process_info.hProcess
    h_thread = process_info.hThread
    pid = process_info.dwProcessId

    try:
        if not kernel32.AssignProcessToJobObject(job, h_process):
            err = ctypes.get_last_error()
            kernel32.TerminateProcess(h_process, 1)
            kernel32.CloseHandle(h_process)
            raise SetupError(f"AssignProcessToJobObject failed: {err}")
        status = ntdll.NtResumeProcess(h_process)
        if status != 0:
            kernel32.TerminateProcess(h_process, 1)
            kernel32.CloseHandle(h_process)
            raise SetupError(f"NtResumeProcess failed: 0x{status:08x}")
    finally:
        kernel32.CloseHandle(h_thread)

    return pid, h_process


def process_is_running(h_process: wintypes.HANDLE) -> bool:
    exit_code = wintypes.DWORD()
    if not kernel32.GetExitCodeProcess(h_process, ctypes.byref(exit_code)):
        return False
    return exit_code.value == STILL_ACTIVE


def pid_is_gone(pid: int) -> bool:
    """True if pid is no longer a live process. Opening it by ID is the only
    option once we only have a bare PID (e.g. from a job's PID list) rather
    than a handle we created ourselves; a PID that cannot be opened at all is
    treated as gone rather than as unknown, since on this machine that always
    means it has already exited and been recycled out of the process table."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return True
    try:
        return not process_is_running(handle)
    finally:
        kernel32.CloseHandle(handle)


def find_window_for_pids(pids: set[int], title: str) -> int | None:
    """Finds the top-level, visible window whose title matches exactly and
    whose owning process is one of pids. Mirrors the enumerate-and-match
    pattern apps use to focus their own already-running window."""
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    found = {"hwnd": None}

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value != title:
            return True
        found["hwnd"] = hwnd
        return False

    proc = WNDENUMPROC(callback)
    user32.EnumWindows(proc, 0)
    return found["hwnd"]


def close_window(hwnd: int) -> None:
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def capture_whole_window(hwnd: int, out_path: Path) -> bool:
    """Shells out to capture_window.ps1 to save the whole window, title bar
    included, via PrintWindow. Returns True on success; failures are
    reported by the caller rather than raised, since a failed screenshot
    should not stop cleanup."""
    script = HERE / "capture_window.ps1"
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Hwnd",
                str(int(hwnd)),
                "-OutPath",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        print(f"FAIL window capture failed: {exc}")
        return False
    if result.returncode != 0 or not out_path.is_file():
        print(f"FAIL window capture failed: {result.stderr.strip() or result.stdout.strip()}")
        return False
    print(f"PASS captured whole window to {out_path}")
    return True


def run_netstat() -> str:
    result = subprocess.run(
        ["netstat", "-ano", "-p", "TCP"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def _wait_for(predicate, timeout_s: float, interval_s: float = 0.2):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval_s)
    return None


def read_first_json_line(path: Path, timeout_s: float, poll_interval_s: float = 0.1) -> dict:
    """Polls a file for its first complete line and parses it as JSON. Used
    to read a fixture's one line of startup JSON from its stdout log, since
    the fixture's stdout is redirected straight to a file rather than a pipe
    (a pipe nobody reads can fill its buffer and stall the fixture). Purely
    filesystem-based, so it is testable by writing to a temp file directly,
    without launching anything."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                text = ""
            newline_at = text.find("\n")
            if newline_at != -1:
                line = text[:newline_at].strip()
                if not line:
                    raise SetupError(f"fixture's first line in {path.name} was empty")
                try:
                    return json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SetupError(f"fixture's first line in {path.name} was not valid JSON: {exc}") from exc
        time.sleep(poll_interval_s)
    raise SetupError(f"fixture did not print its JSON line to {path.name} within {timeout_s}s")


def open_inheritable_log_handle(path: Path) -> tuple[int, wintypes.HANDLE]:
    """Opens path for writing with an OS handle that a child process can
    inherit, for redirecting a suspended-launched process's stdout/stderr
    straight to a file. Returns (fd, handle); the caller closes fd once
    CreateProcessW has run (the child gets its own reference through handle
    inheritance, so the parent's fd is not needed afterward)."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_BINARY)
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(fd))
    if not kernel32.SetHandleInformation(handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT):
        os.close(fd)
        raise SetupError(f"SetHandleInformation failed for {path}: {ctypes.get_last_error()}")
    return fd, handle


def compute_exit_code(cleanup_verified: bool, checks_ok: bool, capture_ok: bool, window_ok: bool) -> int:
    """Turns a run's outcome into drive.py's exit code. Cleanup being
    unverified outranks everything else, so it always wins over a check
    failure: 0 only when cleanup was verified and every check, the window
    capture, and the close-and-verify step all passed; 1 when cleanup was
    verified but something failed; 2 when cleanup itself could not be
    verified. Pure and testable without launching anything."""
    if not cleanup_verified:
        return 2
    if checks_ok and capture_ok and window_ok:
        return 0
    return 1


def main(argv: list[str]) -> int:
    if argv and argv[0] == "cleanup":
        if len(argv) != 2:
            print("usage: drive.py cleanup <run folder>", file=sys.stderr)
            return 2
        return cmd_cleanup(argv[1])

    if len(argv) != 2:
        print("usage: drive.py <app repo> <scenario name>", file=sys.stderr)
        return 2

    repo = Path(argv[0])
    scenario_name = argv[1]

    if not repo.is_dir():
        print(f"ERROR: app repo not found: {repo}", file=sys.stderr)
        return 2

    try:
        manifest = load_manifest(repo)
        plan = validate_manifest(repo, manifest, scenario_name)
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if mutex_exists(plan["mutex"]):
        print(
            "ERROR: the app is already running (its single-instance mutex is held). "
            "Close it before running this check.",
            file=sys.stderr,
        )
        return 2

    run_dir = create_run_dir()
    print(f"run folder: {run_dir}")

    try:
        lock_fd = acquire_run_lock(run_dir)
    except SetupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        (run_dir / "tmp").mkdir(exist_ok=True)
        app_dir = run_dir / "app"
        app_dir.mkdir(exist_ok=True)

        try:
            copy_repo_to(repo, app_dir)
        except SetupError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

        interpreter = sys.executable
        job = None
        # entry_h_procs: only the app's own process, used to detect it exiting
        # after WM_CLOSE. The fixture never responds to WM_CLOSE (it is meant
        # to keep running until the job is torn down), so it does not belong
        # in this wait list; all_h_procs is every handle opened, entry and
        # fixture, kept only so their handles get closed at the end.
        entry_h_procs: list[wintypes.HANDLE] = []
        all_h_procs: list[wintypes.HANDLE] = []
        port = None

        try:
            port = find_free_port()
            job = create_job_with_kill_on_close()

            entry_path = repo_path_to_copy(repo, app_dir, plan["entry_path"])
            script_path = repo_path_to_copy(repo, app_dir, plan["script_path"])
            fixture_path = (
                repo_path_to_copy(repo, app_dir, plan["fixture_path"])
                if plan["fixture_path"] is not None
                else None
            )

            fixture_json = None
            if fixture_path is not None:
                # Launched the same way as the app itself: suspended,
                # assigned to the job, then resumed, so the fixture can never
                # run a single instruction outside the job and can never
                # outlive it. Its stdout/stderr go straight to files rather
                # than pipes, since a pipe nobody is reading from can fill up
                # and stall the fixture.
                stdout_log = run_dir / "fixture_stdout.log"
                stderr_log = run_dir / "fixture_stderr.log"
                out_fd, out_handle = open_inheritable_log_handle(stdout_log)
                err_fd, err_handle = open_inheritable_log_handle(stderr_log)
                try:
                    _fixture_pid, fixture_h_process = launch_suspended_in_job(
                        job,
                        [interpreter, str(fixture_path)],
                        app_dir,
                        fixture_env(os.environ, run_dir, app_dir),
                        stdout_handle=out_handle,
                        stderr_handle=err_handle,
                    )
                finally:
                    os.close(out_fd)
                    os.close(err_fd)
                all_h_procs.append(fixture_h_process)
                fixture_json = read_first_json_line(stdout_log, LAUNCH_TIMEOUT_S)

            env = base_child_env(os.environ, run_dir)
            env["QTWEBENGINE_REMOTE_DEBUGGING"] = str(port)

            pid, h_process = launch_suspended_in_job(job, [interpreter, str(entry_path)], app_dir, env)
            entry_h_procs.append(h_process)
            all_h_procs.append(h_process)

            # The debug port is opened by QtWebEngineProcess.exe, a child the
            # app spawns after launch, not by the entry script's own PID. A
            # child of a job-assigned process joins the same job
            # automatically, so "owned by this run" means "currently a
            # member of the job", read fresh each time: get_job_pids(job),
            # not a fixed set collected at launch.
            def listener_ready():
                output = run_netstat()
                ok, _reason = verify_listener(port, output, get_job_pids(job))
                return output if ok else None

            netstat_output = _wait_for(listener_ready, LAUNCH_TIMEOUT_S)
            if netstat_output is None:
                output = run_netstat()
                ok, reason = verify_listener(port, output, get_job_pids(job))
                raise SetupError(f"debug port never came up cleanly: {reason}")

            target = _wait_for_page_target(port, plan["page_url_contains"], LAUNCH_TIMEOUT_S)
            if target is None:
                raise SetupError("no matching CDP page target appeared in time")

            timeout_ms = plan["timeout_s"] * 1000

            results_path = run_dir / "results.json"
            job_request = {
                "webSocketDebuggerUrl": target["webSocketDebuggerUrl"],
                "scenarioPath": str(script_path),
                "outDir": str(run_dir),
                "fixture": fixture_json,
                "timeoutMs": timeout_ms,
            }
            request_path = run_dir / "cdp_request.json"
            request_path.write_text(json.dumps(job_request), encoding="utf-8")

            node_log_path = run_dir / "node.log"
            node_result = subprocess.run(
                ["node", str(HERE / "cdp.mjs"), str(request_path)],
                capture_output=True,
                text=True,
                timeout=plan["timeout_s"] + 30,
            )
            node_log_path.write_text(node_result.stdout + "\n" + node_result.stderr, encoding="utf-8")

            results = {"checks": [], "error": None}
            if results_path.is_file():
                try:
                    results = json.loads(results_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    results["error"] = "results.json was not valid JSON"
            elif node_result.returncode != 0:
                results["error"] = f"cdp.mjs exited {node_result.returncode} before writing results"

            checks = results.get("checks", [])
            for c in checks:
                status = "PASS" if c.get("pass") else "FAIL"
                line = f"{status} {c.get('name')}"
                if not c.get("pass") and c.get("detail"):
                    line += f"  [{c['detail']}]"
                print(line)
            if results.get("error"):
                print(f"SCENARIO ERROR: {results['error']}")

            passed = sum(1 for c in checks if c.get("pass"))
            print(f"{passed}/{len(checks)} checks passed")

            job_pids = get_job_pids(job)
            hwnd = find_window_for_pids(job_pids, plan["window_title"])
            if hwnd is not None:
                capture_ok = capture_whole_window(hwnd, run_dir / "window.png")
            else:
                print("FAIL could not find the app window to capture")
                capture_ok = False

            teardown = _close_app_and_verify(job, job_pids, entry_h_procs, plan["window_title"], port, hwnd)
            job = None  # _close_app_and_verify always closes the job before returning
            if teardown["cleanup_verified"]:
                write_teardown_marker(run_dir)

            checks_ok = bool(checks) and passed == len(checks) and not results.get("error")
            return compute_exit_code(teardown["cleanup_verified"], checks_ok, capture_ok, teardown["ok"])

        except Exception as exc:
            if isinstance(exc, SetupError):
                print(f"ERROR: {exc}", file=sys.stderr)
            else:
                print(f"ERROR: {exc!r}", file=sys.stderr)
            job, cleanup_verified = _report_cleanup_after_error(job, port)
            if cleanup_verified:
                write_teardown_marker(run_dir)
            return 2
        finally:
            _cleanup(job, all_h_procs)
    finally:
        release_run_lock(lock_fd)


def _wait_for_page_target(port: int, page_url_contains: str | None, timeout_s: float) -> dict | None:
    import urllib.request

    def check():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as resp:
                targets = json.loads(resp.read())
        except OSError:
            return None
        for t in targets:
            if t.get("type") != "page" or not t.get("webSocketDebuggerUrl"):
                continue
            if page_url_contains and page_url_contains not in (t.get("url") or ""):
                continue
            return t
        return None

    return _wait_for(check, timeout_s)


def port_is_listening(port: int, netstat_output: str) -> bool:
    return any(
        row["local_port"] == port and row["state"] == "LISTENING" for row in parse_netstat_lines(netstat_output)
    )


def _survivors(pids: set[int], timeout_s: float) -> list[int]:
    """Waits up to timeout_s for every pid to be gone, then returns whichever
    ones are still alive (empty means cleanup is fully verified)."""
    if not pids:
        return []
    _wait_for(lambda: all(pid_is_gone(p) for p in pids), timeout_s)
    return sorted(p for p in pids if not pid_is_gone(p))


def _close_app_and_verify(
    job, job_pids: set[int], entry_h_procs: list, window_title: str, port: int, hwnd: int | None
) -> dict:
    """Closes the app window, then closes the job (which kills anything the
    job still holds, including a fixture that never responds to WM_CLOSE),
    then verifies every PID the job ever held is actually gone and the port
    is no longer listening. Closes the job handle exactly once, here; the
    caller must not close it again.

    Returns a dict with two separate flags: "cleanup_verified" (the job is
    closed, no survivor PID, port no longer listening: this is what gates
    writing the teardown marker) and "ok" (cleanup_verified plus the app
    window was actually found, the overall pass/fail signal)."""
    found_window = hwnd is not None or find_window_for_pids(job_pids, window_title) is not None
    if hwnd is None:
        hwnd = find_window_for_pids(job_pids, window_title)

    try:
        if hwnd is not None:
            close_window(hwnd)
            exited = _wait_for(lambda: all(not process_is_running(h) for h in entry_h_procs), CLOSE_TIMEOUT_S)
            if not exited:
                print("FAIL app did not exit within the close timeout")
        else:
            print("FAIL could not find the app window to close")
    finally:
        kernel32.CloseHandle(job)  # KILL_ON_JOB_CLOSE: anything left in the job dies now

    survivors = _survivors(job_pids, SURVIVOR_TIMEOUT_S)
    port_output = run_netstat()
    still_listening = port_is_listening(port, port_output)

    cleanup_verified = not survivors and not still_listening
    ok = found_window and cleanup_verified
    if ok:
        print("PASS cleanup verified (no leftover process, port closed)")
    else:
        detail = []
        if survivors:
            detail.append(f"PIDs still alive: {survivors}")
        if still_listening:
            detail.append("port still listening")
        if not found_window:
            detail.append("window was never found")
        print(f"FAIL cleanup left something behind ({'; '.join(detail)})")
    return {"ok": ok, "cleanup_verified": cleanup_verified}


def _report_cleanup_after_error(job, port: int | None):
    """Runs after main's setup/run code raised, once the run is already
    being abandoned: closes the job if one exists (killing anything it still
    holds) and reports whether that actually cleaned everything up, the same
    way a successful run does. Returns (None, cleanup_verified): the caller
    unconditionally overwrites its `job` variable with the first element, so
    _cleanup does not close the job a second time, and uses the second
    element to decide whether the teardown marker may be written."""
    if job is None:
        return None, True
    try:
        pids = get_job_pids(job)
    except SetupError:
        pids = set()
    kernel32.CloseHandle(job)

    survivors = _survivors(pids, SURVIVOR_TIMEOUT_S)
    still_listening = port_is_listening(port, run_netstat()) if port is not None else False
    cleanup_verified = not survivors and not still_listening
    if not cleanup_verified:
        detail = []
        if survivors:
            detail.append(f"PIDs still alive: {survivors}")
        if still_listening:
            detail.append("port still listening")
        print(f"FAIL cleanup left something behind ({'; '.join(detail)})", file=sys.stderr)
    else:
        print("cleanup verified: no leftover process, port closed", file=sys.stderr)
    return None, cleanup_verified


def _cleanup(job, h_procs: list) -> None:
    if job:
        try:
            kernel32.CloseHandle(job)
        except Exception:
            pass
    for h in h_procs:
        try:
            kernel32.CloseHandle(h)
        except Exception:
            pass


# --------------------------------------------------------------------------
# `drive.py cleanup <run folder>`: deletes one run folder, refusing unless
# every safety check below holds.
# --------------------------------------------------------------------------

def _delete_dir_tree_no_follow(root: Path) -> None:
    """Removes root and everything under it, bottom-up, never following a
    symlink or junction found inside: such an entry is removed as the link
    itself (os.rmdir on a reparse point removes only the link, not its
    target's contents), not recursed into."""

    def _remove_contents(d: Path) -> None:
        for entry in os.scandir(d):
            p = Path(entry.path)
            if entry.is_dir(follow_symlinks=False) and not is_reparse_point(p):
                _remove_contents(p)
                os.rmdir(p)
            elif entry.is_dir(follow_symlinks=False):
                os.rmdir(p)  # reparse point (symlink/junction): remove the link only
            else:
                # chmod follows a link, so a file symlink is removed as-is
                # rather than clearing read-only on its target.
                if not is_reparse_point(p):
                    try:
                        os.chmod(p, 0o666)
                    except OSError:
                        pass
                os.remove(p)

    _remove_contents(root)
    os.rmdir(root)


def cmd_cleanup(run_dir_arg: str) -> int:
    path = Path(run_dir_arg)

    def refuse(msg: str) -> int:
        print(f"ERROR: refusing to clean up {path}: {msg}", file=sys.stderr)
        return 2

    if not path.exists():
        return refuse("path does not exist")
    if is_reparse_point(path):
        return refuse("path is a symlink/reparse point")

    temp_root = Path(tempfile.gettempdir()).resolve()

    def identity_ok() -> str | None:
        """Returns None if every identity check passes, else a reason."""
        if is_reparse_point(path):
            return "path is a symlink/reparse point"
        canonical = path.resolve()
        if canonical.parent != temp_root:
            return f"parent is not {temp_root}"
        if not canonical.name.startswith("ui-drive-"):
            return "folder name does not look like a ui_drive run"
        marker_path = canonical / RUN_MARKER_NAME
        if not marker_path.is_file():
            return f"missing {RUN_MARKER_NAME}"
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return f"could not read {RUN_MARKER_NAME}: {exc}"
        if marker.get("path") != str(canonical):
            return f"{RUN_MARKER_NAME} does not match this folder"
        if not (canonical / TEARDOWN_MARKER_NAME).is_file():
            return f"missing {TEARDOWN_MARKER_NAME} (teardown was not verified)"
        return None

    reason = identity_ok()
    if reason:
        return refuse(reason)

    canonical = path.resolve()
    try:
        lock_fd = acquire_run_lock(canonical)
    except SetupError as exc:
        return refuse(f"could not lock run folder (a run may still be using it): {exc}")
    release_run_lock(lock_fd)

    # Re-check immediately before deleting: the checks above, the lock
    # attempt, and the delete itself are not one atomic operation.
    reason = identity_ok()
    if reason:
        return refuse(reason)

    try:
        _delete_dir_tree_no_follow(canonical)
    except OSError as exc:
        return refuse(f"delete failed: {exc}")

    if canonical.exists():
        return refuse("folder still exists after deletion")

    print(f"removed {canonical}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
