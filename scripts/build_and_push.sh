#!/bin/bash

# Author: Ziang Yang
# Description: Helper script for building and pushing the custom Airflow image.
# Note: This script is intended as a local-PC helper for testing or ad hoc image operations.
# It is not the formal remote-node deployment procedure for the lab cluster.
# For actual remote deployment, follow the setup instructions in README.md.

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
