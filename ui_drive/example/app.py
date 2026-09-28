"""Minimal pywebview (Qt) app used as the worked example for ui_drive.

One window, one page: a button that increments a counter, a text box, and a
theme toggle. The counter goes up through a real call into js_api, the same
path drive.py's example scenario drives over CDP.

    <this folder>\\.venv\\Scripts\\python.exe app.py
"""

import ctypes
import os
import sys

import webview

HERE = os.path.dirname(os.path.abspath(__file__))

MUTEX_NAME = "UiDrive_Example_SingleInstance"

PAGE_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>ui_drive example</title>
<style>
  body { font-family: sans-serif; padding: 24px; }
  body.dark { background: #1e1e1e; color: #eee; }
  body.light { background: #ffffff; color: #111; }
</style>
</head>
<body class="light">
  <h1>ui_drive example</h1>
  <p>Count: <span id="count">0</span></p>
  <button id="incrementBtn">Increment</button>
  <br><br>
  <label for="nameBox">Name:</label>
  <input id="nameBox" type="text">
  <br><br>
  <button id="themeBtn">Toggle theme</button>
  <p id="themeLabel">light</p>
<script>
  let count = 0;
  document.getElementById("incrementBtn").addEventListener("click", async () => {
    count = await window.pywebview.api.increment(count);
    document.getElementById("count").textContent = String(count);
  });
  document.getElementById("themeBtn").addEventListener("click", () => {
    const body = document.body;
    const next = body.classList.contains("light") ? "dark" : "light";
    body.classList.remove("light", "dark");
    body.classList.add(next);
    document.getElementById("themeLabel").textContent = next;
  });
</script>
</body>
</html>
"""


class Api:
    def increment(self, current):
        return int(current) + 1


def _acquire_single_instance(mutex_name: str) -> bool:
    global _mutex_handle
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _mutex_handle = kernel32.CreateMutexW(None, False, mutex_name)
        return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


_mutex_handle = None


def main():
    if not _acquire_single_instance(MUTEX_NAME):
        sys.exit(0)

    api = Api()
    webview.create_window("ui_drive example", html=PAGE_HTML, js_api=api, width=640, height=480)
    webview.start(gui="qt")


if __name__ == "__main__":
    main()
