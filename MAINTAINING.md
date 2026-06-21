# Maintaining this pipeline

Maintainer notes for Build-Tools. Consumer docs (how to call the pipeline, how to
verify a download) live in README.md.

## Why releases ship unsigned

The Simple X Tools are licensed PolyForm Noncommercial 1.0.0, which is not
OSI-approved (it forbids commercial use). That makes them ineligible for
SignPath Foundation free OSS signing. Azure code signing (about $10/month,
CI-native via OIDC, no hardware token) works but was declined on cost. So
releases ship unsigned and the SmartScreen "unknown publisher" warning stays.
installer/installer.iss keeps a commented SignTool hook so signing can drop in
later with no other change.

Signing facts worth not re-researching:
- EV certificates no longer bypass SmartScreen (Microsoft removed instant bypass
  in 2024). EV, OV, and Azure all build reputation per file hash over download
  volume.
- The only free paths to zero SmartScreen warnings are Microsoft Store or MSIX
  re-signing, both untested (a noncommercial license may not be Store-eligible).
- OV/EV private keys must live on FIPS hardware (HSM or USB token) since June
  2023, which is why a plain cert is painful in CI.

## Supply-chain hardening (every tool)

1. Pin GitHub Actions to full commit SHAs, not moving tags.
2. Pin Python deps to exact versions in requirements.txt (learn them from a green
   build first).
3. Pin the caller's `uses:` to a Build-Tools commit SHA, not @v1 or @main.
4. Enable Dependabot so pinned refs still get reviewed update PRs.

## Dependabot PR review protocol

Never auto-merge, never rubber-stamp. For each PR:
1. Read the embedded changelog and summarize it in plain language: what it
   updates and the size of the jump (patch, minor, or major).
2. Call out risk: major or breaking changes, anything touching the
   build/sign/publish chain (especially attest-build-provenance and the
   upload/download-artifact pair, which must move together), and whether it is a
   security fix (act) or a plain version bump (no urgency).
3. To accept a bump: test it on one tool via a throwaway RC tag, confirm the
   release still builds and attests, then roll it across the fleet.

Default when everything works and nothing is a security fix: leave them open.

## Pre-public scan (before flipping any repo public)

Run pre-public-scan.sh and show the results. Never just assert "clean". It checks
secrets (gitleaks over full history), PII and infra identifiers, personal local
paths, and commit author/committer email, which lives in git headers where
content greps miss it (it must be a GitHub no-reply address, not a real address).
A skipped gitleaks run counts as a FAIL, not a pass, so confirm the scan actually
ran. No scan is 100%; showing what was checked is part of the deal.

## Constraints

- Provenance attestation and the required-reviewer gate are public-repo-only on
  Free/Pro, so neither can be exercised while a repo is private.
