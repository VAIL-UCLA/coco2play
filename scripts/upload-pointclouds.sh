#!/usr/bin/env bash
#
# upload-pointclouds.sh
#
# Uploads every data/scene*/point_cloud.ply to a GitHub Release as a flat asset.
# These files are too large for GitHub Pages (>100 MB per file) and too costly
# for Git LFS bandwidth (~1.5 GB pulled on every CI deploy), so they live
# outside the repo and are referenced via meta.json (_data_sources).
#
# Usage:
#   scripts/upload-pointclouds.sh [tag]
#
# Defaults to the tag in data/meta.json (_data_sources.tag), or `data-v1`.
#
# Requires the GitHub CLI (`gh`) to be authenticated for the current repo.

set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v gh >/dev/null 2>&1; then
  echo "error: gh (GitHub CLI) is not installed. Install from https://cli.github.com/" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is required (brew install jq)" >&2
  exit 1
fi

REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
if [[ -z "${REPO}" ]]; then
  echo "error: could not detect repo. Run 'gh auth login' or set a git remote." >&2
  exit 1
fi

DEFAULT_TAG="$(jq -r '._data_sources.tag // "data-v1"' data/meta.json)"
TAG="${1:-$DEFAULT_TAG}"

shopt -s nullglob
PLYS=(data/scene*/point_cloud.ply)
shopt -u nullglob

if [[ ${#PLYS[@]} -eq 0 ]]; then
  echo "error: no data/scene*/point_cloud.ply files found." >&2
  exit 1
fi

echo "Repo:  ${REPO}"
echo "Tag:   ${TAG}"
echo "Files: ${#PLYS[@]} point cloud(s)"
echo

if ! gh release view "${TAG}" --repo "${REPO}" >/dev/null 2>&1; then
  echo "Creating release ${TAG}..."
  gh release create "${TAG}" \
    --repo "${REPO}" \
    --title "Data ${TAG}" \
    --notes "Point cloud assets for the web viewer. Managed by scripts/upload-pointclouds.sh."
fi

for ply in "${PLYS[@]}"; do
  scene_id="$(basename "$(dirname "$ply")")"
  asset_name="${scene_id}_point_cloud.ply"
  size_mb="$(du -m "$ply" | awk '{print $1}')"
  echo "  ${ply}  ->  ${asset_name}  (${size_mb} MB)"
  gh release upload "${TAG}" "${ply}#${asset_name}" \
    --repo "${REPO}" \
    --clobber
done

echo
echo "Done. Verify with:"
echo "  gh release view ${TAG} --repo ${REPO}"
echo
echo "Web client will fetch from:"
echo "  https://github.com/${REPO}/releases/download/${TAG}/<scene_id>_point_cloud.ply"
