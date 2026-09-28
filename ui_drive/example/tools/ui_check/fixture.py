"""Fixture for the ui_drive example's smoke scenario.

Prints one line of JSON, then keeps running until drive.py tears it down
along with the app. Exists to exercise the fixture path itself: launched
suspended and assigned to the same job as the app, its stdout read from a
log file rather than a pipe.
"""

import json
import time

print(json.dumps({"greeting": "hello from fixture"}), flush=True)

while True:
    time.sleep(1)
