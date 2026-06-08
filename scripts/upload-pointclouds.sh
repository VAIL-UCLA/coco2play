#!/usr/bin/env bash
#
# upload-pointclouds.sh
#
# Uploads every data/scene*/point_cloud.ply to a GitHub Release as a flat asset.
# Point clouds are too large to commit (~200–800 MB each). Use this script to
# share scene data with collaborators who can download from the release and place
# files under data/scene*/point_cloud.ply locally.
#
# Usage:
#   scripts/upload-pointclouds.sh [tag]
#
# Default tag: data-v1
#
# Requires the GitHub CLI (`gh`) to be authenticated for the current repo.

set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v gh >/dev/null 2>&1; then
  echo "error: gh (GitHub CLI) is not installed. Install from https://cli.github.com/" >&2
  exit 1
fi

REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
if [[ -z "${REPO}" ]]; then
  echo "error: could not detect repo. Run 'gh auth login' or set a git remote." >&2
  exit 1
fi

TAG="${1:-data-v1}"

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
    --notes "Point cloud assets for Coco2Play. Managed by scripts/upload-pointclouds.sh."
fi

STAGE_DIR="$(mktemp -d -t coco2play-pcs.XXXXXX)"
trap 'rm -rf "${STAGE_DIR}"' EXIT

for ply in "${PLYS[@]}"; do
  scene_id="$(basename "$(dirname "$ply")")"
  asset_name="${scene_id}_point_cloud.ply"
  size_mb="$(du -m "$ply" | awk '{print $1}')"
  echo "  ${ply}  ->  ${asset_name}  (${size_mb} MB)"
  ln -sf "$(realpath "$ply")" "${STAGE_DIR}/${asset_name}"
  gh release upload "${TAG}" "${STAGE_DIR}/${asset_name}" \
    --repo "${REPO}" \
    --clobber
done

echo
echo "Done. Collaborators can download with:"
echo "  gh release download ${TAG} --repo ${REPO} -D ./downloads"
echo "  # then move sceneN_point_cloud.ply -> data/sceneN/point_cloud.ply"
