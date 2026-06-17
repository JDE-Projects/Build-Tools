#!/usr/bin/env bash
# pre-public-scan.sh — JDE-Projects pre-public safety scan.
#
# Run this on ANY repo BEFORE switching it from private to public. Going public
# exposes the ENTIRE git history and is effectively irreversible, so this scans
# all history (not just current files) for two things:
#   1. Secrets  — via gitleaks (keys, tokens, passwords, connection strings)
#   2. PII/infra — internal IPs, hostnames, AD/onmicrosoft domains, emails,
#                  local user paths (these are NOT to appear in public repos)
#
# Usage:  bash pre-public-scan.sh [path-to-repo]   (defaults to current dir)
# Exit:   0 = secrets clean (still review the PII list); 1 = attention needed.
set -uo pipefail

REPO="${1:-.}"
cd "$REPO" 2>/dev/null || { echo "Not a directory: $REPO"; exit 2; }
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "Not a git repo: $REPO"; exit 2; }
NAME="$(basename "$(pwd)")"
FAIL=0

echo "===== PRE-PUBLIC SCAN: $NAME ($(git rev-list --all --count) commits) ====="

# 1) Secrets ---------------------------------------------------------------
echo "--- gitleaks (secrets, full history) ---"
if command -v gitleaks >/dev/null 2>&1; then
  if gitleaks detect --source . --no-banner --redact >/tmp/gl_$$.txt 2>/dev/null; then
    echo "  CLEAN — no secrets in history"
  else
    echo "  LEAKS FOUND:"; sed 's/^/    /' /tmp/gl_$$.txt; FAIL=1
  fi
  rm -f /tmp/gl_$$.txt
else
  echo "  WARNING: gitleaks not installed — secret scan SKIPPED"; FAIL=1
fi

# 2) PII / infra identifiers ----------------------------------------------
echo "--- PII / infra identifiers (full history) ---"
PAT='\b(10\.[0-9]+\.[0-9]+\.[0-9]+|192\.168\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]+\.[0-9]+)\b|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\.onmicrosoft\.com|[A-Za-z0-9-]+\.local\b|C:\\\\Users\\\\[A-Za-z]+|/home/[a-z]+'
# Known-safe noise: loopback, RFC example domains, SSH algorithm strings.
NOISE='127\.0\.0\.1|0\.0\.0\.0|example\.(com|org|net)|@example|localhost|@openssh\.com|@libssh\.org'
HITS="$(git grep -hIoE -i "$PAT" $(git rev-list --all) \
        -- . ':(exclude)*.ttf' ':(exclude)fonts/*' ':(exclude)*.png' ':(exclude)*.ico' 2>/dev/null \
        | grep -vE "$NOISE" | sort | uniq -c | sort -rn)"
if [ -n "$HITS" ]; then
  echo "  REVIEW — confirm each is a placeholder/example, not real PII:"
  echo "$HITS" | sed 's/^/    /'
  echo "  (placeholders are fine; real names, IPs, hostnames, emails, or paths"
  echo "   must be scrubbed from history before going public)"
else
  echo "  none"
fi

echo "===== END: $NAME ====="
if [ "$FAIL" -eq 0 ]; then
  echo "RESULT: secrets CLEAN. Review the PII list above before going public."
else
  echo "RESULT: ATTENTION NEEDED (secrets found or scanner missing)."
fi
exit "$FAIL"
