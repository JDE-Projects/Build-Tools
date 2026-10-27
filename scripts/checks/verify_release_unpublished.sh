#!/usr/bin/env bash
# verify_release_unpublished.sh: stop a release run early when the tag's
# release is already published.
#
# Usage: verify_release_unpublished.sh <tag>
#   Needs GH_TOKEN and GH_REPO in the environment.
#
# With immutable releases on, a published release can never take new files,
# so a re-run would build everything and then fail at the upload with
# GitHub's own refusal. A missing release or a draft passes. Any other gh
# failure is reported as a warning and the run continues, so a GitHub hiccup
# here never blocks a release.
set -uo pipefail

TAG="${1:?usage: verify_release_unpublished.sh <tag>}"

if out="$(gh release view "$TAG" --json isDraft --jq .isDraft 2>&1)"; then
  if [ "$out" = "false" ]; then
    echo "::error::Release $TAG is already published on $GH_REPO and cannot be changed. To ship a fix, push a new version tag."
    exit 1
  fi
  echo "verify_release_unpublished: $TAG exists as a draft; continuing."
elif [ "$out" = "release not found" ]; then
  echo "verify_release_unpublished: no release for $TAG yet; continuing."
else
  echo "::warning::Could not check whether $TAG is already published: $out"
fi
exit 0
