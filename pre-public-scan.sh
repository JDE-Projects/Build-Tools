#!/usr/bin/env bash
# pre-public-scan.sh — JDE-Projects pre-public safety scan.
#
# Run this on ANY repo BEFORE switching it from private to public. Going public
# exposes the ENTIRE git history and is effectively irreversible, so this scans
# all history (not just current files) for:
#   1. Secrets  — via gitleaks (keys, tokens, passwords, connection strings)
#   2. PII/infra — internal IPs, hostnames, AD/onmicrosoft domains, emails,
#                  local user paths (these are NOT to appear in public repos)
#   3. Commit author/committer emails — must be GitHub no-reply addresses
#   4. Local-only files — roadmaps, drafts, CLAUDE.md, .env, IDE dirs, etc.
#   5. License sanity — LICENSE type, copyleft red flags, PyQt vs PySide6
#
# Usage:  bash pre-public-scan.sh [path-to-repo]   (defaults to current dir)
# Exit:   0 = all checks clean (still review the PII list); 1 = attention needed.
set -uo pipefail

REPO="${1:-.}"
cd "$REPO" 2>/dev/null || { echo "Not a directory: $REPO"; exit 2; }
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "Not a git repo: $REPO"; exit 2; }
NAME="$(basename "$(pwd)")"
FAIL=0

echo "===== PRE-PUBLIC SCAN: $NAME ($(git rev-list --all --count) commits) ====="

# gitleaks PATH fallback — check winget install locations on Windows
if ! command -v gitleaks >/dev/null 2>&1 && [ -n "${LOCALAPPDATA:-}" ]; then
  GL_FOUND=""
  if [ -x "$LOCALAPPDATA/Microsoft/WinGet/Links/gitleaks.exe" ]; then
    GL_FOUND="$LOCALAPPDATA/Microsoft/WinGet/Links"
  else
    GL_MATCH="$(find "$LOCALAPPDATA/Microsoft/WinGet/Packages/" \
                -name 'gitleaks.exe' -print -quit 2>/dev/null || true)"
    if [ -n "$GL_MATCH" ]; then
      GL_FOUND="$(dirname "$GL_MATCH")"
    fi
  fi
  if [ -n "$GL_FOUND" ]; then
    export PATH="$GL_FOUND:$PATH"
    echo "  (using gitleaks from $GL_FOUND)"
  fi
fi

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

# 3) Commit author/committer email metadata -------------------------------
# These live in commit headers, NOT in files — gitleaks and content greps miss
# them. Public repos expose every author email. Want only GitHub no-reply.
echo "--- commit author/committer emails ---"
BADEMAIL="$(git log --all --format='%ae%n%ce' | sort -u | grep -viE '(users\.noreply\.github\.com|^noreply@github\.com)$')"
if [ -n "$BADEMAIL" ]; then
  echo "  REVIEW — real emails in commit metadata (rewrite history before public):"
  echo "$BADEMAIL" | sed 's/^/    /'
  FAIL=1
else
  echo "  clean — all commits use a github no-reply address"
fi

# 4) Local-only / non-distributable files ----------------------------------
echo "--- local-only / non-distributable files ---"
echo "  tracked files (eyeball for anything unexpected):"
git ls-files | sed 's/^/    /'
LOCAL_FLAGS="$(git ls-files | while IFS= read -r f; do
  base="$(basename "$f")"
  lbase="$(echo "$base" | tr '[:upper:]' '[:lower:]')"
  lf="$(echo "$f" | tr '[:upper:]' '[:lower:]')"

  # directory prefixes
  case "$lf" in
    .vscode/*|.idea/*|.claude/*) echo "$f"; continue ;;
  esac

  # keyword matches (case-insensitive, anywhere in path)
  if echo "$lf" | grep -qiE '(roadmap|status|todo|notes|scratch|draft|internal)'; then
    echo "$f"; continue
  fi

  # basename equals CLAUDE.md
  if [ "$lbase" = "claude.md" ]; then
    echo "$f"; continue
  fi

  # preview anywhere in path
  if echo "$lf" | grep -qi 'preview'; then
    echo "$f"; continue
  fi

  # Debug_Log_*
  case "$base" in
    Debug_Log_*) echo "$f"; continue ;;
  esac

  # Launch_*.bat
  case "$lbase" in
    launch_*.bat) echo "$f"; continue ;;
  esac

  # .spec extension
  case "$lbase" in
    *.spec) echo "$f"; continue ;;
  esac

  # *.local.* pattern
  if echo "$lbase" | grep -qE '\.local\.'; then
    echo "$f"; continue
  fi

  # .env or .env.*
  if [ "$lbase" = ".env" ] || echo "$lbase" | grep -qE '^\.env\.'; then
    echo "$f"; continue
  fi

  # .log extension
  case "$lbase" in
    *.log) echo "$f"; continue ;;
  esac

  # *.md files that are NOT README.md
  case "$lbase" in
    *.md)
      if [ "$lbase" != "readme.md" ]; then
        echo "$f"; continue
      fi
      ;;
  esac
done)"
if [ -n "$LOCAL_FLAGS" ]; then
  echo "  MUST NOT be public — remove from the repo (and history) before going public:"
  echo "$LOCAL_FLAGS" | sed 's/^/    /'
  FAIL=1
else
  echo "  none"
fi

# 5) License sanity --------------------------------------------------------
echo "--- license sanity ---"
# LICENSE file
if ! git ls-files --error-unmatch LICENSE >/dev/null 2>&1; then
  echo "  WARNING: no LICENSE file"; FAIL=1
else
  if grep -q 'PolyForm Noncommercial' LICENSE 2>/dev/null; then
    echo "  LICENSE: PolyForm NC"
  elif grep -q 'MIT License' LICENSE 2>/dev/null; then
    echo "  LICENSE: MIT"
  elif grep -q 'Apache License' LICENSE 2>/dev/null; then
    echo "  LICENSE: Apache-2.0"
  elif grep -qiE 'GNU GENERAL PUBLIC|GNU AFFERO' LICENSE 2>/dev/null; then
    echo "  WARNING: copyleft license detected in LICENSE — confirm this is intentional"
    FAIL=1
  else
    echo "  LICENSE present (type unrecognized — review manually)"
  fi
fi

# THIRD-PARTY-LICENSES
if ! git ls-files | grep -qi '^THIRD-PARTY-LICENSES'; then
  echo "  REVIEW: no THIRD-PARTY-LICENSES file (bundled deps' notices may be missing)"
fi

# PyQt red flag (GPL Qt binding) — real imports / deps only, not "never PyQt" comments
PYQT_HITS="$(git ls-files | grep -iE '\.(py)$|^requirements.*\.txt$' \
             | xargs -r grep -liE '^[[:space:]]*(import[[:space:]]+PyQt|from[[:space:]]+PyQt|PyQt[56]?[[:space:]]*(==|>=|~=|$))' 2>/dev/null || true)"
if [ -n "$PYQT_HITS" ]; then
  echo "  FAIL: PyQt (GPL Qt binding) referenced — conflicts with a noncommercial/permissive license; the bundle must use PySide6 (LGPL)"
  echo "$PYQT_HITS" | sed 's/^/    /'
  FAIL=1
fi

# PySide6 confirmation
PYSIDE_HITS="$(git ls-files | grep -iE '\.(py)$|^requirements.*\.txt$' \
               | xargs -r grep -li 'PySide6' 2>/dev/null || true)"
if [ -z "$PYSIDE_HITS" ]; then
  echo "  REVIEW: no PySide6 reference found — confirm which Qt binding is bundled"
fi

# requirements.txt human review
if [ -f requirements.txt ]; then
  echo "  deps to eyeball (confirm none are GPL/AGPL):"
  sed 's/^/    /' requirements.txt
fi

echo "===== END: $NAME ====="
if [ "$FAIL" -eq 0 ]; then
  echo "RESULT: all checks CLEAN. Review PII and dep-license lists above before going public."
else
  echo "RESULT: ATTENTION NEEDED — review failures above before going public."
fi
exit "$FAIL"
