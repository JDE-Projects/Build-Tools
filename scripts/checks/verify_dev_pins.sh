#!/usr/bin/env bash
# verify_dev_pins.sh — confirm a caller repo's requirements-dev.txt matches
# Build-Tools' canonical templates/dev/requirements.txt pins.
#
# Usage: verify_dev_pins.sh [caller_file] [canonical_file]
#   caller_file    defaults to ./requirements-dev.txt (the caller repo's copy)
#   canonical_file defaults to _buildtools/templates/dev/requirements.txt
#
# Only opt-in via the verify_dev_pins workflow input. Compares the set of pin
# lines only: comment lines (#...), blank lines, and CRLF/LF differences are
# ignored, so a cosmetic edit never fails the build.
set -uo pipefail

CALLER_FILE="${1:-requirements-dev.txt}"
CANONICAL_FILE="${2:-_buildtools/templates/dev/requirements.txt}"

if [ ! -f "$CALLER_FILE" ]; then
  echo "verify_dev_pins: missing $CALLER_FILE" >&2
  echo "Every repo with verify_dev_pins enabled must ship a requirements-dev.txt" >&2
  echo "that is a copy of _buildtools/templates/dev/requirements.txt." >&2
  exit 1
fi

if [ ! -f "$CANONICAL_FILE" ]; then
  echo "verify_dev_pins: missing canonical file $CANONICAL_FILE" >&2
  exit 1
fi

# Strip CR (CRLF -> LF), drop comment lines and blank lines, sort for a set
# comparison.
normalize() {
  tr -d '\r' < "$1" | grep -v '^[[:space:]]*#' | grep -v '^[[:space:]]*$' | sort -u
}

CALLER_PINS="$(normalize "$CALLER_FILE")"
CANONICAL_PINS="$(normalize "$CANONICAL_FILE")"

if [ "$CALLER_PINS" = "$CANONICAL_PINS" ]; then
  echo "verify_dev_pins: $CALLER_FILE matches the canonical pins."
  exit 0
fi

echo "verify_dev_pins: $CALLER_FILE does not match $CANONICAL_FILE" >&2
echo >&2
MISSING="$(comm -23 <(echo "$CANONICAL_PINS") <(echo "$CALLER_PINS"))"
EXTRA="$(comm -13 <(echo "$CANONICAL_PINS") <(echo "$CALLER_PINS"))"
if [ -n "$MISSING" ]; then
  echo "Missing pins (present in canonical, absent from $CALLER_FILE):" >&2
  echo "$MISSING" | sed 's/^/  /' >&2
fi
if [ -n "$EXTRA" ]; then
  echo "Extra/different pins (present in $CALLER_FILE, not in canonical):" >&2
  echo "$EXTRA" | sed 's/^/  /' >&2
fi
echo >&2
echo "Copy _buildtools/templates/dev/requirements.txt over $CALLER_FILE and commit it." >&2
exit 1
