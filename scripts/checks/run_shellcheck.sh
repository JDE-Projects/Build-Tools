#!/usr/bin/env bash
# run_shellcheck.sh — lint the caller's own shell scripts with a pinned
# ShellCheck binary (never the runner's preinstalled copy, which drifts).
#
# Usage: run_shellcheck.sh "path1 path2 ..."
#   A single space-separated argument, matching the shellcheck_paths workflow
#   input (default: "install.sh uninstall.sh"). Only these caller-owned files
#   are linted; _buildtools is never touched.
#
# Pinned version: ShellCheck v0.10.0
# Release asset: shellcheck-v0.10.0.linux.x86_64.tar.xz
#   https://github.com/koalaman/shellcheck/releases/tag/v0.10.0
# The download is verified against a pinned SHA-256 before use, so a tampered
# or swapped release binary is rejected rather than run.
#
# Test-only seam: set SHELLCHECK_BIN to an already-installed shellcheck
# binary to skip the pinned download (used so the path-validation logic can
# be tested without network access). Do not set this in CI.
set -uo pipefail

SHELLCHECK_VERSION="0.10.0"
# SHA-256 of shellcheck-v0.10.0.linux.x86_64.tar.xz from the pinned release.
SHELLCHECK_SHA256="6c881ab0698e4e6ea235245f22832860544f17ba386442fe7e9d629f8cbedf87"
PATHS_ARG="${1:-install.sh uninstall.sh}"

# Split on whitespace into an array of paths.
read -r -a TARGET_PATHS <<< "$PATHS_ARG"

if [ "${#TARGET_PATHS[@]}" -eq 0 ]; then
  echo "run_shellcheck: no paths given" >&2
  exit 1
fi

MISSING=0
for p in "${TARGET_PATHS[@]}"; do
  if [ ! -f "$p" ]; then
    echo "run_shellcheck: listed path not found: $p" >&2
    MISSING=1
  fi
done
if [ "$MISSING" -ne 0 ]; then
  echo "run_shellcheck: fix shellcheck_paths, or add the missing file(s)." >&2
  exit 1
fi

if [ -n "${SHELLCHECK_BIN:-}" ]; then
  SHELLCHECK="$SHELLCHECK_BIN"
else
  TMP_DIR="$(mktemp -d)"
  trap 'rm -rf "$TMP_DIR"' EXIT
  ARCHIVE="shellcheck-v${SHELLCHECK_VERSION}.linux.x86_64.tar.xz"
  URL="https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/${ARCHIVE}"
  echo "run_shellcheck: downloading pinned ShellCheck v${SHELLCHECK_VERSION}"
  curl -fsSL -o "$TMP_DIR/$ARCHIVE" "$URL" || {
    echo "run_shellcheck: failed to download $URL" >&2
    exit 1
  }
  echo "${SHELLCHECK_SHA256}  $TMP_DIR/$ARCHIVE" | sha256sum -c - || {
    echo "run_shellcheck: SHA-256 mismatch on downloaded ShellCheck; refusing to run it." >&2
    exit 1
  }
  tar -xJf "$TMP_DIR/$ARCHIVE" -C "$TMP_DIR" || {
    echo "run_shellcheck: failed to extract $ARCHIVE" >&2
    exit 1
  }
  SHELLCHECK="$TMP_DIR/shellcheck-v${SHELLCHECK_VERSION}/shellcheck"
  chmod +x "$SHELLCHECK"
fi

echo "run_shellcheck: linting: ${TARGET_PATHS[*]}"
"$SHELLCHECK" "${TARGET_PATHS[@]}"
