# ui_drive

Smoke test a JDE pywebview app in its real window, launched from source, the
same way a person would run it. No headless stand-in and no fake browser: the
real Python code answers every button click, because the app's own
QtWebEngine window is what gets driven.

This works because pywebview's Qt backend is built on QtWebEngine, which is
Chromium under the hood. Setting one environment variable before launch turns
on Chromium's DevTools protocol (CDP), the same protocol behind headless
Chrome automation. `drive.py` launches the app with that variable set, then a
small Node script drives the page over CDP: clicking real buttons, typing
real text, and reading the page back through the app's real bridge
(`window.pywebview.api`).

## Requirements

- Windows (this tool uses Windows-only APIs: job objects, window handles,
  named mutexes)
- Python 3.12 or later, the app's own virtual environment (stdlib only, no
  extra packages to install)
- Node 22 or later on PATH (built-in `fetch` and `WebSocket`, no npm
  packages)
- `git` on PATH, to build the throwaway copy of the app repo a run works from
- PowerShell (already on Windows), for the window screenshot step

## Usage

```
<app repo>\.venv\Scripts\python.exe <build-tools>\ui_drive\drive.py <app repo> <scenario name>
```

Example, using this repo's worked example:

```
"path\to\Simple SFTP Client\.venv\Scripts\python.exe" ui_drive\drive.py ui_drive\example smoke
```

To remove a run folder once you're done looking at it:

```
<app repo>\.venv\Scripts\python.exe <build-tools>\ui_drive\drive.py cleanup <run folder>
```

The app must not already be running: if its single-instance mutex is held,
`drive.py` refuses to start (exit code 2) rather than touch the user's open
copy.

The app repo is only ever read. Every run starts by copying it into a fresh
folder directly under the system temp folder, named `ui-drive-` followed by
32 random hex characters (printed at the start of the run), and everything
the run does from then on, launching the app, running the fixture, and
writing settings, databases, sample data, logs, and screenshots, happens
inside that one folder. The folder is created outright (it fails if a name
is somehow already taken, in which case a new random name is tried) and then
held open for the whole run in a way that stops it being renamed, moved, or
replaced by anything else running as the same Windows user; every file
`drive.py` itself writes inside it, and every subfolder it creates, is
checked the same way and created fresh, never overwriting something already
there. This closes the door on another program racing `drive.py` to swap in
a symlink or a different folder while a run is going. It does not, and
cannot, stop the app or the fixture from writing wherever their own code
tells them to: that is the same as running them by hand, not something this
tool sits between. The run folder holds:

- `app\`, the throwaway copy of the app repo the app and fixture actually run
  from
- `tmp\`, the private `TEMP`/`TMP` folder handed to the app and fixture, so
  anything either writes to "the temp folder" lands here instead of the
  user's real one
- `results.json`, the scenario's checks in machine-readable form
- any screenshots the scenario or the window capture step took
- on any failure, the app's own stdout/stderr and the Node log, so a broken
  run can be diagnosed without repeating it
- `.ui-drive-run`, a marker written at creation holding the run's ID and its
  own canonical path
- `.ui-drive.lock`, held exclusively by `drive.py` for the whole run
- `.teardown-complete`, written only once the app's job has been closed and
  cleanup (no leftover process, the debug port closed) has been verified

### The copy

The file list is `git`'s: tracked files plus untracked-but-not-ignored ones,
taken from the working tree, not from a commit. A listed path that no longer
exists on disk (a tracked file since deleted) is skipped. Any entry that is a
symlink or other reparse point, or whose resolved path falls outside the
repo, makes `drive.py` refuse to start at all (exit code 2), before it
launches anything. The app repo can itself be a subfolder of a larger git
repo (`ui_drive/example` in this repo is); the copy still works, since the
file list is taken relative to the app repo, not the git repo's top level.
If the app repo is not inside a git work tree at all, `drive.py` refuses with
a clear message.

The manifest (`tools/ui_check/ui_drive.json`) is read and validated
against the real repo. The entry script and the
scenario's `script` and `fixture` paths, once validated, are then resolved
against the copy instead, so the app, the scenario, and the fixture all run
from inside the run folder.

### Child process environment

The app and its fixture both get `PYTHONDONTWRITEBYTECODE=1` and a private
`TEMP`/`TMP` pointing at `<run folder>\tmp`. The fixture additionally gets
`UI_DRIVE_OUT_DIR` (the run folder) and
`UI_DRIVE_APP_DIR` (the copy), so a fixture that needs to can pre-place
sample data, a database file, for example, next to the copied app before it
launches; the fixture already runs before the app does.

### Cleanup

`drive.py cleanup <run folder>` deletes one run folder. It never kills a
process: only `drive.py`'s own run does that, through the job object. The
folder is held open (the same way a run holds its own folder open) for the
whole cleanup, so nothing else can rename, move, or replace it out from
under the delete. Cleanup refuses (exit code 2, with a plain message) unless
all of these hold: the path is a plain directory, not a reparse point; its
canonical parent is the system temp folder; its name matches
`ui-drive-` followed by 32 hex characters; `.ui-drive-run` exists and its
recorded path matches the folder; `.teardown-complete` exists; and the lock
file can be locked exclusively (nothing, meaning no live `drive.py`, is
still holding it). Deleting the contents walks the folder top-down, holding
each real subdirectory open the same way as the root while it is being
emptied; a symlink or junction found anywhere inside, file or directory, is
deleted as the link itself and never entered, so its target is left
untouched. Every one of these checks is read from the thing actually being
acted on (an open handle), never just trusted from a path string, and any
unexpected result refuses rather than falling back to an ordinary
path-based delete. Exit code 0 means the folder was deleted and verified
gone.

Exit code for a run is 0 only when every check passed and cleanup was
verified, 1 when cleanup was verified but a check failed, and 2 whenever
cleanup could not be verified (this always wins over a check result) or any
setup problem came up first (bad manifest, app already running, `git`
missing or the repo not a work tree, the debug port never came up, a Node or
process-launch failure, or anything else unexpected). Every exception
`drive.py` can raise is caught, reported, and run through the same verified
teardown before the process exits, so a crash never skips cleanup.

## The manifest: `tools/ui_check/ui_drive.json`

Every app that wants to use this lives up to a small contract, one JSON file
in its own repo:

| Key | Required | Meaning |
|---|---|---|
| `entry` | yes | Repo-relative path to the script that starts the app (e.g. `app.py`) |
| `mutex` | yes | The name of the app's single-instance mutex, so a running copy is never touched |
| `window_title` | yes | The exact title of the app's main window, used to find and close it |
| `page_url_contains` | no | A substring the page's URL must contain, to pick the right CDP target when more than one could match |
| `scenarios` | yes | Object mapping a scenario name to `{ "script": "...", "fixture": "..." }` |

Inside `scenarios`, each entry needs:

| Key | Required | Meaning |
|---|---|---|
| `script` | yes | Repo-relative path to the scenario's `.js` file |
| `fixture` | no | Repo-relative path to a Python script that stands up test data (a throwaway server, sample files) before the app launches |
| `timeout_s` | no | How many seconds the scenario gets to finish, from connecting to the page onward. Whole number, 10 to 600. Default 60 |

All paths in the manifest are repo-relative. Absolute paths, `..` segments,
and anything that resolves outside the repo (including through a symlink)
are rejected before anything is launched. Asking for a scenario name that
is not in the manifest is rejected the same way.

### Fixtures

A fixture is a plain Python script, run with the same interpreter as the
app, with the run folder's copy of the app (`app\`) as its working
directory. It prints exactly one line of JSON to stdout (for example, a
throwaway test server's port and a login the scenario should use), then
keeps running until `drive.py` shuts it down.
That JSON is handed to the scenario as `fixture`. The fixture runs inside the
same job as the app, so it is torn down at the same time, even if it forgot
to clean up after itself.

The fixture's stdout and stderr go straight to `fixture_stdout.log` and
`fixture_stderr.log` in the output folder, not to a pipe: a fixture that
prints more than a pipe buffer holds, with nothing reading the other end,
would otherwise stall. Because stdout is a file, Python holds printed text
back until its buffer fills, so print the JSON line with `flush=True`:
without it, `drive.py` waits the full launch timeout and gives up.

A fixture is killed along with the job when the run ends, so it never gets a
chance to clean up its own files. Write a fixture to use only the two
folders `drive.py` hands it: `UI_DRIVE_OUT_DIR`, the run's output folder
(the same folder `results.json` and any screenshots land in), for its own
throwaway files such as a test server's working folder, and
`UI_DRIVE_APP_DIR`, the copied app, for sample data the app must find next
to itself. Both sit inside the run folder, so what the fixture writes there
is deleted along with the rest of the run. `drive.py` passes these folders
but cannot enforce them: a fixture that writes anywhere else leaves those
files behind.

## Writing a scenario

A scenario file is a JS module whose default export is an async function.
It receives one object with these helpers:

| Helper | What it does |
|---|---|
| `click(selector)` | Scrolls the element into view, then sends a real mouse click at its on-screen centre. Fails the check if the element is missing, hidden, disabled, or covered by something else at that point |
| `type(selector, text)` | Clicks the element to focus it (same checks as `click`), then **replaces its entire current contents** with `text`, the way a person clearing a field before typing over it would: a real Ctrl+A (the browser's own "select all" editing command, not just the key's default handling) followed by the new text. For a native segmented input (`<input type="date">`, `time`, `month`, `week`, `datetime-local`), where `Input.insertText` never reaches the control's sub-fields at all, it instead presses Home and sends `text` as real per-character key events, the same way a person fills one of these in one segment at a time |
| `keys(selector, text)` | Clicks the element, then sends `text` as real per-character key events (keydown/keyup per character, not one bulk text-insertion event). `type()` already does this automatically for a native segmented input; use this directly on a plain field when a scenario needs to watch something react to individual keystrokes rather than one paste-like insertion |
| `press(key)` | Sends a real key press. Supported keys: `Enter`, `Tab`, `Escape`, `Backspace`, `ArrowUp`, `ArrowDown`, `Home`. Add more to `KEY_TABLE` in `cdp.mjs` if a scenario needs one |
| `evaluate(js)` | Runs JavaScript in the page and returns its value. For reading state and setting up test conditions, not for simulating input |
| `waitFor(js, timeoutMs)` | Polls `js` until it is truthy, or throws after `timeoutMs` (default 5000) |
| `check(name, pass, detail)` | Records one pass/fail result. `detail` is shown only when `pass` is false |
| `screenshot(name, settleMs)` | Waits `settleMs` (default 600) so any fade or transition finishes, then saves a PNG of the page (not the window chrome) to the output folder as `<name>.png`. A shot taken mid-fade can look like a styling bug that isn't there |
| `fixture` | The JSON the fixture printed, or `null` if the scenario has no fixture |

A scenario that throws, or that times out, is recorded as a scenario error
rather than left to hang; the run always finishes.

## Window screenshot

After the scenario finishes, `drive.py` takes one more screenshot on its
own: the whole window, title bar included, saved as `window.png` in the
output folder. This uses `capture_window.ps1`, which calls Windows'
`PrintWindow` with the flag that captures GPU-accelerated content, so it
works even for a window that is not on top. `screenshot()` inside a scenario
only captures the page itself; use this one when the window frame matters.

## The example

`ui_drive/example/` is a minimal pywebview app: one window with a counter
button wired to a real `js_api` call, an empty text box, a pre-filled text
box, a date input, and a light/dark theme toggle. Its manifest and `smoke`
scenario exercise every helper above, including one check that goes through
the real Python bridge, `type()` replacing a pre-filled field's contents
instead of appending to it, and `type()`'s native-date-input fallback. Run
it with any interpreter that has `pywebview` and `PySide6` installed:

```
<python with pywebview and PySide6>.exe ui_drive\drive.py ui_drive\example smoke
```

## What this does not cover

- Native file pickers, print dialogs, or any other OS-level window: CDP
  only reaches the web page inside the app, not dialogs Windows itself draws
- OS-level input (this sends input through CDP, not through the real mouse
  and keyboard driver, so it will not catch a bug that only shows up with
  real hardware input)
- Multi-monitor behaviour or window placement
- Look and feel: pixel-perfect rendering, animations, fonts. The screenshots
  are for a human to look at, not for automated pixel comparison
- The built `.exe`: this always launches the app from source, in its own
  virtual environment, the same way `python app.py` would

## Security

While a run is going, the app's debug port is open on `127.0.0.1` and
accepts connections from any program running on the same PC under the same
Windows session, with no password. This is a local test door on a trusted
development machine, not a security boundary: never leave a run going
unattended on a shared or untrusted machine, and never point this at a
built, installed, or production copy of an app. Runs are from source only,
for exactly this reason.

The app runs from the copy as the same Windows user running `drive.py`, with
the same permissions that user has everywhere else: the copy is a fresh
folder the app cannot tell apart from a real install, not a sandbox. Nothing
here stops the app from reading or writing outside its own folder if its own
code does that. The same is true of a scenario's `.js` file and a fixture's
`.py` file: they run with the same permissions as everything else and are
expected to already be reviewed code, the same trust as the app itself.
Before pointing this at a new app, check what the app writes and where, so a
run's list of side effects is known ahead of time rather than found by
surprise.

`drive.py` itself is held to a tighter rule than the app, the scenario, or
the fixture: it has no path of its own to delete, overwrite, or create
anything outside the one run folder it made for itself, even if another
program running as the same user is actively racing it. Every folder it
creates is opened in a way that stops it being renamed or replaced while
`drive.py` is using it, and every file it writes is created fresh and
refuses if anything, including a symlink, already sits at that name. Any
check along the way that cannot be verified refuses outright (exit code 2)
rather than falling back to a plain path-based read, write, or delete. This
protects `drive.py`'s own bookkeeping, not the app: what the app, the
scenario, and the fixture do with their own files is outside this
boundary, exactly as described above.
