#!/bin/bash

set -euo pipefail

REGISTRY="${REGISTRY:-10.10.5.21:32000}"
IMAGE_NAME="${IMAGE_NAME:-climate-airflow-stable}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
IMAGE_REF="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"

echo "=== Building Airflow image ==="
docker build -t "${IMAGE_REF}" -f "${PROJECT_DIR}/docker/airflow/Dockerfile" "${PROJECT_DIR}"

echo "=== Pushing image ==="
docker push "${IMAGE_REF}"

echo "=== Done ==="
echo "${IMAGE_REF}"
