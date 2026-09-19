#!/usr/bin/env bash
# verify_runtime_lock.sh — confirm a caller repo's committed requirements.txt
# is exactly what regenerating from requirements.in produces, so the hashed
# install (--require-hashes) is installing what the source file actually
# resolves to and not a stale or hand-edited lock.
#
# Usage: verify_runtime_lock.sh [requirements_in] [requirements_txt]
#   requirements_in  defaults to ./requirements.in
#   requirements_txt defaults to ./requirements.txt
#
# Regeneration is pinned to a single reproducible command, run with a pinned
# uv version:
#   uv version:      0.12.17
#   install command: pip install uv==0.12.17
#   compile command: uv pip compile --universal --generate-hashes \
#                       --python-version 3.10 requirements.in -o <tempfile>
# A maintainer regenerating the lock by hand should install that exact uv
# version and run that exact command from the repo root.
#
# Test-only seam: set RUNTIME_LOCK_REGENERATED_FILE to a path and this script
# skips installing uv and compiling, using that file as the "regenerated"
# side of the comparison instead. This lets the comparison logic be exercised
# in tests on machines without uv installed. Do not set this in CI.
set -uo pipefail

REQUIREMENTS_IN="${1:-requirements.in}"
REQUIREMENTS_TXT="${2:-requirements.txt}"
UV_VERSION="0.12.17"

if [ ! -f "$REQUIREMENTS_IN" ]; then
  echo "verify_runtime_lock: missing $REQUIREMENTS_IN" >&2
  exit 1
fi

if [ ! -f "$REQUIREMENTS_TXT" ]; then
  echo "verify_runtime_lock: missing $REQUIREMENTS_TXT" >&2
  exit 1
fi

CLEANUP_TMP=""
cleanup() {
  [ -n "$CLEANUP_TMP" ] && rm -f "$CLEANUP_TMP"
}
trap cleanup EXIT

if [ -n "${RUNTIME_LOCK_REGENERATED_FILE:-}" ]; then
  # Test-only: skip uv entirely and compare against a pre-built file.
  REGENERATED_FILE="$RUNTIME_LOCK_REGENERATED_FILE"
  if [ ! -f "$REGENERATED_FILE" ]; then
    echo "verify_runtime_lock: RUNTIME_LOCK_REGENERATED_FILE set but not found: $REGENERATED_FILE" >&2
    exit 1
  fi
else
  echo "verify_runtime_lock: installing pinned uv==$UV_VERSION"
  python -m pip install "uv==$UV_VERSION" || {
    echo "verify_runtime_lock: failed to install uv==$UV_VERSION" >&2
    exit 1
  }
  REGENERATED_FILE="$(mktemp)"
  CLEANUP_TMP="$REGENERATED_FILE"
  uv pip compile --universal --generate-hashes --python-version 3.10 \
    "$REQUIREMENTS_IN" -o "$REGENERATED_FILE" || {
    echo "verify_runtime_lock: uv pip compile failed" >&2
    exit 1
  }
fi

# Ignore uv's leading comment header (lines starting with # at column 0;
# per-package "# via ..." annotation lines are indented and are kept) and
# normalize CRLF/LF.
normalize() {
  tr -d '\r' < "$1" | grep -v '^#'
}

REGENERATED_BODY="$(normalize "$REGENERATED_FILE")"
COMMITTED_BODY="$(normalize "$REQUIREMENTS_TXT")"

if [ "$REGENERATED_BODY" = "$COMMITTED_BODY" ]; then
  echo "verify_runtime_lock: $REQUIREMENTS_TXT matches a fresh regeneration from $REQUIREMENTS_IN."
  exit 0
fi

echo "verify_runtime_lock: $REQUIREMENTS_TXT is out of date with $REQUIREMENTS_IN" >&2
echo >&2
diff -u <(echo "$COMMITTED_BODY") <(echo "$REGENERATED_BODY") >&2 || true
echo >&2
echo "Regenerate the lock and commit it in the same PR:" >&2
echo "  pip install uv==$UV_VERSION" >&2
echo "  uv pip compile --universal --generate-hashes --python-version 3.10 requirements.in -o requirements.txt" >&2
exit 1
