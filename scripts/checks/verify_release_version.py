#!/usr/bin/env python3
"""verify_release_version.py — confirm a pushed release tag matches the
version literal in the app's entry script, before any packaging happens.

Usage:
    python verify_release_version.py <entry_script_path>

The entry script path comes from the trusted workflow input (entry_script)
and is passed as a CLI argument. The pushed tag is read from the
GITHUB_REF_NAME environment variable, never interpolated into a shell
command or into Python source, so a crafted tag cannot run as code.

Reads APP_VERSION by parsing the entry script with the ast module only. The
file is never imported or executed.
"""

from __future__ import annotations

import ast
import os
import re
import sys

TAG_RE = re.compile(
    r"^v(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-rc(?P<rc>[1-9]\d*))?$"
)


def fail(message: str) -> None:
    print(f"verify_release_version: {message}", file=sys.stderr)
    sys.exit(1)


def find_app_version(entry_script: str) -> str:
    try:
        with open(entry_script, "r", encoding="utf-8") as fh:
            source = fh.read()
    except OSError as exc:
        fail(f"could not read entry script {entry_script!r}: {exc}")
        raise  # unreachable, keeps type checkers happy

    try:
        tree = ast.parse(source, filename=entry_script)
    except SyntaxError as exc:
        fail(f"could not parse entry script {entry_script!r}: {exc}")
        raise

    matches: list[str] = []
    for node in tree.body:  # top-level only
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue

        for target in targets:
            if isinstance(target, ast.Name) and target.id == "APP_VERSION":
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    matches.append(value.value)
                else:
                    fail(
                        "APP_VERSION is assigned but is not a plain string "
                        "literal"
                    )

    if not matches:
        fail(f"no top-level APP_VERSION string assignment found in {entry_script!r}")
    if len(matches) > 1:
        fail(
            f"found {len(matches)} top-level APP_VERSION assignments in "
            f"{entry_script!r}; there must be exactly one"
        )
    return matches[0]


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        fail("entry_script argument is required and must not be empty")
        return 1

    entry_script = sys.argv[1]
    app_version = find_app_version(entry_script)

    tag = os.environ.get("GITHUB_REF_NAME", "")
    if not tag:
        fail("GITHUB_REF_NAME is not set")
        return 1

    match = TAG_RE.match(tag)
    if match is None:
        fail(
            f"tag {tag!r} is malformed; expected vMAJOR.MINOR.PATCH or "
            "vMAJOR.MINOR.PATCH-rcN (no leading zeros, N >= 1)"
        )
        return 1

    tag_version = f"{match.group('major')}.{match.group('minor')}.{match.group('patch')}"
    if tag_version != app_version:
        fail(
            f"tag {tag!r} (version {tag_version}) does not match "
            f"APP_VERSION {app_version!r} in {entry_script!r}"
        )
        return 1

    print(f"verify_release_version: tag {tag!r} matches APP_VERSION {app_version!r}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
