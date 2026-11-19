#!/usr/bin/env bash
# Publish <dir>/* to a GitHub Release: the pushed v* tag, or the rolling "nightly" prerelease.
# Expects GH_TOKEN, GITHUB_REF, GITHUB_REF_NAME, GITHUB_SHA and GITHUB_REPOSITORY.
set -euo pipefail

dir="$1"
mapfile -t files < <(find "$dir" -maxdepth 1 -type f | sort)

publish_tag() {
  local tag="$GITHUB_REF_NAME" flags=()
  if [[ "$tag" == *-* ]]; then flags+=(--prerelease); fi
  gh release create "$tag" "${files[@]}" --title "LobsterVM ${tag#v}" \
    --generate-notes --verify-tag "${flags[@]}"
}

publish_nightly() {
  local tag=nightly notes
  notes="Rolling build of master (${GITHUB_SHA::7}). Inputs:"$'\n\n```\n'"$(cat "$dir/VERSIONS.txt")"$'\n```'
  gh release delete "$tag" --cleanup-tag --yes --repo "$GITHUB_REPOSITORY" || true
  gh release create "$tag" "${files[@]}" --title "LobsterVM nightly" --notes "$notes" \
    --prerelease --target "$GITHUB_SHA"
}

if [[ "$GITHUB_REF" == refs/tags/v* ]]; then
  publish_tag
else
  publish_nightly
fi
