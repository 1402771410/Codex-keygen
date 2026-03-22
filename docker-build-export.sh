#!/usr/bin/env bash

set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-codex-register}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
OUTPUT_DIR="${OUTPUT_DIR:-dist}"
CONTAINER_CLI="${CONTAINER_CLI:-}"

if [[ -z "${CONTAINER_CLI}" ]]; then
  if command -v docker >/dev/null 2>&1; then
    CONTAINER_CLI="docker"
  elif command -v podman >/dev/null 2>&1; then
    CONTAINER_CLI="podman"
  else
    echo "Error: docker/podman command not found. Install a container runtime first." >&2
    exit 1
  fi
fi

safe_image_name="${IMAGE_NAME//\//_}"
safe_tag="${IMAGE_TAG//:/_}"
OUTPUT_FILE="${OUTPUT_FILE:-${OUTPUT_DIR}/${safe_image_name}-${safe_tag}.tar}"

mkdir -p "${OUTPUT_DIR}"

echo "[1/2] Build image with ${CONTAINER_CLI}: ${IMAGE_NAME}:${IMAGE_TAG}"
"${CONTAINER_CLI}" build -t "${IMAGE_NAME}:${IMAGE_TAG}" .

echo "[2/2] Export image tar: ${OUTPUT_FILE}"
"${CONTAINER_CLI}" save -o "${OUTPUT_FILE}" "${IMAGE_NAME}:${IMAGE_TAG}"

echo "Done: ${OUTPUT_FILE}"
