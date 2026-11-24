#!/usr/bin/env python3
"""verify_notices.py: confirm the license notices name the same versions of
Python and its bundled libraries that the build actually uses.

Usage:
    python verify_notices.py [notices_file] [--dist <folder>]

notices_file defaults to THIRD-PARTY-LICENSES.txt in the current folder. If it
does not exist the check prints a skip line and exits 0 (some tools ship no
notices).

The expected versions come from the running interpreter: CPython, OpenSSL,
SQLite, expat and mpdecimal. Every notices line that starts (after optional
leading whitespace) with one of those names, a space and a version number is
compared with the expected version. A component with no such line, or with a
line naming another version, fails the check. All failures are reported.

With --dist <folder>, the built app folder is also checked: every .dll or .pyd
in it that has the same file name as one in the interpreter's DLLs folder, and
the interpreter's own python3*.dll, must be byte-identical (SHA-256) to the
interpreter's copy. This shows the bundle carries the libraries the version
probe read.

No network access and nothing is written.
"""

from __future__ import annotations

import argparse
import decimal
import hashlib
import os
import platform
import re
import sqlite3
import ssl
import sys

import pyexpat

DEFAULT_NOTICES = "THIRD-PARTY-LICENSES.txt"
COMPONENTS = ("CPython", "OpenSSL", "SQLite", "expat", "mpdecimal")
LINE_RE = re.compile(r"^\s*(" + "|".join(COMPONENTS) + r") (\d+(?:\.\d+)*)")


def fail(message: str) -> None:
    print(f"verify_notices: {message}", file=sys.stderr)
    sys.exit(1)


def expected_versions() -> dict[str, str]:
    match = re.match(r"OpenSSL (\d+\.\d+\.\d+)", ssl.OPENSSL_VERSION)
    if match is None:
        fail(f"cannot read an OpenSSL x.y.z version from {ssl.OPENSSL_VERSION!r}")
        raise SystemExit(1)  # unreachable, keeps type checkers happy
    expat = pyexpat.EXPAT_VERSION
    if expat.startswith("expat_"):
        expat = expat[len("expat_"):]
    return {
        "CPython": platform.python_version(),
        "OpenSSL": match.group(1),
        "SQLite": sqlite3.sqlite_version,
        "expat": expat,
        "mpdecimal": decimal.__libmpdec_version__,
    }


def check_notices(path: str, expected: dict[str, str]) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError as exc:
        return [f"could not read notices file {path!r}: {exc}"]

    found: dict[str, int] = {name: 0 for name in COMPONENTS}
    problems: list[str] = []
    for number, line in enumerate(lines, start=1):
        match = LINE_RE.match(line)
        if match is None:
            continue
        name, version = match.group(1), match.group(2)
        found[name] += 1
        if version != expected[name]:
            problems.append(
                f"{name} {version} on line {number} of {path!r}; "
                f"the build uses {name} {expected[name]}"
            )
    for name in COMPONENTS:
        if found[name] == 0:
            problems.append(
                f"no line naming {name} in {path!r}; "
                f"expected a line starting '{name} {expected[name]}'"
            )
    return problems


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_dist(dist: str) -> tuple[list[str], int]:
    if not os.path.isdir(dist):
        return [f"dist folder {dist!r} does not exist"], 0

    base = sys.base_prefix
    dlls_dir = os.path.join(base, "DLLs")
    reference: dict[str, str] = {}  # lowercase file name -> interpreter's path
    if os.path.isdir(dlls_dir):
        for name in os.listdir(dlls_dir):
            if name.lower().endswith((".dll", ".pyd")):
                reference[name.lower()] = os.path.join(dlls_dir, name)
    core: set[str] = set()  # the interpreter's own python3*.dll files
    for name in os.listdir(base):
        low = name.lower()
        if low.startswith("python3") and low.endswith(".dll"):
            reference[low] = os.path.join(base, name)
            if low != "python3.dll":
                core.add(low)
    if not core:
        return [f"no python3*.dll found in the interpreter folder {base!r}"], 0

    problems: list[str] = []
    compared = 0
    core_seen = False
    for root, _dirs, files in os.walk(dist):
        for name in files:
            low = name.lower()
            if low not in reference:
                continue
            if low in core:
                core_seen = True
            actual = os.path.join(root, name)
            compared += 1
            if sha256_of(actual) != sha256_of(reference[low]):
                problems.append(
                    f"{actual!r} differs from the interpreter's {reference[low]!r}"
                )
    if not core_seen:
        problems.append(
            f"no interpreter python3*.dll ({', '.join(sorted(core))}) found in "
            f"{dist!r}"
        )
    return problems, compared


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("notices", nargs="?", default=DEFAULT_NOTICES)
    parser.add_argument("--dist", default=None, help="built app folder to compare")
    args = parser.parse_args()

    problems: list[str] = []

    if not os.path.exists(args.notices):
        print(f"verify_notices: no notices file at {args.notices!r}, skipping.")
    else:
        expected = expected_versions()
        problems.extend(check_notices(args.notices, expected))
        if not problems:
            print(
                f"verify_notices: {args.notices!r} matches the build ("
                + ", ".join(f"{n} {expected[n]}" for n in COMPONENTS)
                + ")."
            )

    if args.dist is not None:
        dist_problems, compared = check_dist(args.dist)
        problems.extend(dist_problems)
        if not dist_problems:
            print(
                f"verify_notices: {compared} file(s) in {args.dist!r} match "
                "the interpreter's copies."
            )

    if problems:
        for problem in problems:
            print(f"verify_notices: {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
