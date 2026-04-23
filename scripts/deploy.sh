#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
K8S_DIR="$(dirname "$SCRIPT_DIR")/k8s"
NAMESPACE="climate-stable"

echo "=== Creating namespace and secrets ==="
kubectl apply -f "${K8S_DIR}/namespace.yaml"
kubectl apply -f "${K8S_DIR}/secrets.yaml"

echo "=== Deploying PostgreSQL and MinIO ==="
kubectl apply -f "${K8S_DIR}/postgres.yaml"
kubectl apply -f "${K8S_DIR}/minio.yaml"

echo "=== Waiting for storage services ==="
kubectl -n "${NAMESPACE}" wait --for=condition=available --timeout=180s deployment/climate-postgres
kubectl -n "${NAMESPACE}" wait --for=condition=available --timeout=180s deployment/climate-minio

echo "=== Deploying Airflow ==="
kubectl apply -f "${K8S_DIR}/airflow.yaml"
kubectl -n "${NAMESPACE}" wait --for=condition=available --timeout=240s deployment/climate-airflow

echo ""
echo "=== Deployment complete ==="
echo "Airflow UI:    http://<node-ip>:31280"
echo "MinIO API:     http://<node-ip>:30930"
echo "MinIO Console: http://<node-ip>:30931"
echo "PostgreSQL:    <node-ip>:31462"
echo ""
echo "kubectl -n ${NAMESPACE} get pods -o wide"
