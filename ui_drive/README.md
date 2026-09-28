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
- PowerShell (already on Windows), for the window screenshot step

## Usage

```
<app repo>\.venv\Scripts\python.exe <build-tools>\ui_drive\drive.py <app repo> <scenario name>
```

Example, using this repo's worked example:

```
"path\to\Simple SFTP Client\.venv\Scripts\python.exe" ui_drive\drive.py ui_drive\example smoke
```

The app must not already be running: if its single-instance mutex is held,
`drive.py` refuses to start (exit code 2) rather than touch the user's open
copy.

Everything the run produces goes into a fresh folder under `%TEMP%`
(`ui-drive-<random>`), printed at the start of the run. It holds:

- `results.json`, the scenario's checks in machine-readable form
- any screenshots the scenario or the window capture step took
- on any failure, the app's own stdout/stderr and the Node log, so a broken
  run can be diagnosed without repeating it

Exit code is 0 only when every check passed and cleanup was verified, 1 if
any check failed, and 2 on a setup or cleanup problem (bad manifest, app
already running, the debug port never came up, cleanup left something
behind).

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
app, with the repo as its working directory. It prints exactly one line of
JSON to stdout (for example, a throwaway test server's port and a login the
scenario should use), then keeps running until `drive.py` shuts it down.
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
chance to clean up its own files. `drive.py` sets the environment variable
`UI_DRIVE_OUT_DIR` on the fixture's process to the run's output folder (the
same folder `results.json` and any screenshots land in); a fixture should
write its own throwaway files (a test server's working folder, sample files
it builds) under that path instead of the app's repo, so they are deleted
along with the rest of the run's output rather than left behind.

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
