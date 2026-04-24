#!/usr/bin/env bash

# Author: Ziang Yang
# Description: Helper runbook script that groups common lab commands for copying files, building
# images, and checking deployment state.
# Note: This script is a helper/reference for local-PC testing and command rehearsal only.
# It should not be treated as the formal remote-node deployment procedure.
# For the actual deployment process, follow the setup instructions in README.md step by step.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_NAME="$(basename "${PROJECT_DIR}")"

SSH_USER="${SSH_USER:-student}"
NODE1_IP="${NODE1_IP:-10.10.5.21}"
NODE2_IP="${NODE2_IP:-10.10.5.22}"
NODE1_NAME="${NODE1_NAME:-node1}"
REMOTE_ROOT_NAME="${REMOTE_ROOT_NAME:-DE_final_f}"
REMOTE_PROJECT_DIR="~/${REMOTE_ROOT_NAME}/${PROJECT_NAME}"

REGISTRY="${REGISTRY:-10.10.5.21:32000}"
IMAGE_NAME="${IMAGE_NAME:-climate-airflow-stable}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
IMAGE_REF="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"

BUILDER_NAME="${BUILDER_NAME:-armbuilder}"
BUILDKIT_CONFIG="${BUILDKIT_CONFIG:-${PROJECT_DIR}/buildkitd.lab.toml}"

TEST_DAY="${TEST_DAY:-2026-03-16}"
TEST_DAY_NODASH="${TEST_DAY_NODASH:-20260316}"
TEST_EXECUTION_TS="${TEST_EXECUTION_TS:-2026-03-17T05:30:00+00:00}"
TEST_RUN_ID="${TEST_RUN_ID:-collect_auto_20260316}"

node1() {
  ssh "${SSH_USER}@${NODE1_IP}" "$1"
}

node2() {
  ssh "${SSH_USER}@${NODE2_IP}" "$1"
}

print_help() {
  cat <<EOF
Usage:
  $(basename "$0") <action>

Actions:
  summary               Show the deployment order we used today
  copy-project          Copy the project folder to node1
  local-buildx-once     One-time local Docker buildx setup for linux/arm64 + HTTP registry
  local-build-push      Build and push the Airflow image for linux/arm64
  node1-bootstrap       Check node1 MicroK8s and enable dns/storage/registry
  show-join             Print the microk8s add-node join command on node1
  join-node2            Join node2 using JOIN_CMD='sudo microk8s join ... --worker'
  node1-registry        Configure node1 to trust ${REGISTRY}
  node2-registry        Configure node2 to trust ${REGISTRY}
  label-node1           Create host directories and label node1 as storage+compute
  deploy-storage        Apply namespace, secrets, postgres, and minio
  deploy-airflow        Apply airflow.yaml and wait for the deployment
  verify                Check pods and Airflow DAG discovery
  unpause-dags          Unpause the 3 climate DAGs
  trigger-test          Trigger the normal end-to-end test for ${TEST_DAY}
  check-test            Check bronze/silver counts and PostgreSQL rows for ${TEST_DAY}
  port-forward          Forward local ports for Airflow and MinIO

Examples:
  $(basename "$0") summary
  $(basename "$0") local-buildx-once
  $(basename "$0") local-build-push
  JOIN_CMD='sudo microk8s join ${NODE1_IP}:25000/<token>/<hash> --worker' $(basename "$0") join-node2

Notes:
  - Run local-* actions on your local machine where Docker Desktop is installed.
  - Run node*/deploy*/verify/unpause*/trigger*/check* actions on any machine that can SSH to the lab nodes.
  - All kubectl commands use 'sudo microk8s kubectl' on purpose.
EOF
}

summary() {
  cat <<EOF
1. Copy project to node1
2. node1-bootstrap
3. show-join
4. join-node2
5. node1-registry
6. node2-registry
7. label-node1
8. local-buildx-once
9. local-build-push
10. deploy-storage
11. deploy-airflow
12. verify
13. unpause-dags
14. trigger-test
15. check-test
EOF
}

copy_project() {
  ssh "${SSH_USER}@${NODE1_IP}" "mkdir -p ~/${REMOTE_ROOT_NAME}"
  scp -r "${PROJECT_DIR}" "${SSH_USER}@${NODE1_IP}:~/${REMOTE_ROOT_NAME}/"
}

local_buildx_once() {
  cat > "${BUILDKIT_CONFIG}" <<EOF
[registry."${REGISTRY}"]
  http = true
EOF

  docker run --privileged --rm tonistiigi/binfmt --install all

  if docker buildx ls --format '{{.Name}}' | grep -qx "${BUILDER_NAME}"; then
    docker buildx rm "${BUILDER_NAME}"
  fi

  docker buildx create \
    --name "${BUILDER_NAME}" \
    --driver docker-container \
    --buildkitd-config "${BUILDKIT_CONFIG}" \
    --bootstrap \
    --use

  docker buildx inspect --bootstrap
}

local_build_push() {
  cd "${PROJECT_DIR}"

  docker buildx build \
    --platform linux/arm64 \
    -t "${IMAGE_REF}" \
    -f "${PROJECT_DIR}/docker/airflow/Dockerfile" \
    "${PROJECT_DIR}" \
    --push
}

node1_bootstrap() {
  node1 "sudo microk8s status --wait-ready"
  node1 "sudo microk8s kubectl get nodes -o wide"
  node1 "sudo microk8s enable dns storage registry"
  node1 "sudo microk8s kubectl get svc -A | grep 32000 || true"
  node1 "sudo microk8s kubectl get pods -A | grep registry || true"
}

show_join() {
  node1 "sudo microk8s add-node"
}

join_node2() {
  : "${JOIN_CMD:?Set JOIN_CMD to the full 'sudo microk8s join ... --worker' command first.}"
  node2 "${JOIN_CMD}"
}

node1_registry() {
  node1 "sudo mkdir -p /var/snap/microk8s/current/args/certs.d/${REGISTRY}"
  node1 "cat <<'EOF' | sudo tee /var/snap/microk8s/current/args/certs.d/${REGISTRY}/hosts.toml >/dev/null
server = \"http://${REGISTRY}\"

[host.\"http://${REGISTRY}\"]
capabilities = [\"pull\", \"resolve\"]
EOF"
  node1 "sudo microk8s stop && sudo microk8s start && sudo microk8s status --wait-ready"
}

node2_registry() {
  node2 "sudo mkdir -p /var/snap/microk8s/current/args/certs.d/${REGISTRY}"
  node2 "cat <<'EOF' | sudo tee /var/snap/microk8s/current/args/certs.d/${REGISTRY}/hosts.toml >/dev/null
server = \"http://${REGISTRY}\"

[host.\"http://${REGISTRY}\"]
capabilities = [\"pull\", \"resolve\"]
EOF"
  node2 "sudo snap stop microk8s && sudo snap start microk8s"
}

label_node1() {
  node1 "sudo mkdir -p /var/data/climate-stable/postgres /var/data/climate-stable/minio"
  node1 "sudo microk8s kubectl label node ${NODE1_NAME} node-role.kubernetes.io/storage= --overwrite"
  node1 "sudo microk8s kubectl label node ${NODE1_NAME} node-role.kubernetes.io/compute= --overwrite"
  node1 "sudo microk8s kubectl get nodes --show-labels"
}

deploy_storage() {
  node1 "cd ${REMOTE_PROJECT_DIR} && sudo microk8s kubectl apply -f ./k8s/namespace.yaml -f ./k8s/secrets.yaml"
  node1 "cd ${REMOTE_PROJECT_DIR} && sudo microk8s kubectl apply -f ./k8s/postgres.yaml -f ./k8s/minio.yaml"
  node1 "sudo microk8s kubectl -n climate-stable rollout status deployment/climate-postgres --timeout=300s"
  node1 "sudo microk8s kubectl -n climate-stable rollout status deployment/climate-minio --timeout=300s"
  node1 "sudo microk8s kubectl -n climate-stable get pods -o wide"
}

deploy_airflow() {
  node1 "cd ${REMOTE_PROJECT_DIR} && sudo microk8s kubectl apply -f ./k8s/airflow.yaml"
  node1 "sudo microk8s kubectl -n climate-stable rollout status deployment/climate-airflow --timeout=420s"
  node1 "sudo microk8s kubectl -n climate-stable get pods -o wide"
}

verify() {
  node1 "sudo microk8s kubectl get nodes -o wide"
  node1 "sudo microk8s kubectl -n climate-stable get pods -o wide"
  node1 "sudo microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags list"
}

unpause_dags() {
  node1 "sudo microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags unpause climate_collect_weather"
  node1 "sudo microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags unpause climate_prepare_weather"
  node1 "sudo microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags unpause climate_publish_analytics"
}

trigger_test() {
  node1 "sudo microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags trigger climate_collect_weather -e ${TEST_EXECUTION_TS} -r ${TEST_RUN_ID}"
}

check_test() {
  node1 "cat <<'PY' | sudo microk8s kubectl -n climate-stable exec -i deployment/climate-airflow -c airflow-scheduler -- python -
from common.minio_utils import get_s3_client
s3 = get_s3_client()
bronze = s3.list_objects_v2(Bucket='bronze')
silver = s3.list_objects_v2(Bucket='silver')
print('bronze_files_${TEST_DAY_NODASH} =', sum(1 for o in bronze.get('Contents', []) if o['Key'].endswith('${TEST_DAY_NODASH}.json')))
print('silver_files_${TEST_DAY_NODASH} =', sum(1 for o in silver.get('Contents', []) if o['Key'].endswith('${TEST_DAY_NODASH}.parquet')))
PY"

  node1 "sudo microk8s kubectl -n climate-stable exec deployment/climate-postgres -- psql -U climate_user -d climate_analytics -c \"select count(*) as rows_${TEST_DAY_NODASH} from hourly_observations where timestamp >= '${TEST_DAY}' and timestamp < '${TEST_DAY}'::date + interval '1 day'; select timestamp, count(distinct location_id) as locations_per_timestamp from hourly_observations where timestamp >= '${TEST_DAY}' and timestamp < '${TEST_DAY}'::date + interval '1 day' group by 1 order by 1 limit 3;\""
}

port_forward() {
  ssh -N \
    -L 8080:${NODE1_IP}:31280 \
    -L 9001:${NODE1_IP}:30931 \
    -L 9000:${NODE1_IP}:30930 \
    "${SSH_USER}@${NODE1_IP}"
}

ACTION="${1:-help}"

case "${ACTION}" in
  help|-h|--help)
    print_help
    ;;
  summary)
    summary
    ;;
  copy-project)
    copy_project
    ;;
  local-buildx-once)
    local_buildx_once
    ;;
  local-build-push)
    local_build_push
    ;;
  node1-bootstrap)
    node1_bootstrap
    ;;
  show-join)
    show_join
    ;;
  join-node2)
    join_node2
    ;;
  node1-registry)
    node1_registry
    ;;
  node2-registry)
    node2_registry
    ;;
  label-node1)
    label_node1
    ;;
  deploy-storage)
    deploy_storage
    ;;
  deploy-airflow)
    deploy_airflow
    ;;
  verify)
    verify
    ;;
  unpause-dags)
    unpause_dags
    ;;
  trigger-test)
    trigger_test
    ;;
  check-test)
    check_test
    ;;
  port-forward)
    port_forward
    ;;
  *)
    echo "Unknown action: ${ACTION}" >&2
    echo >&2
    print_help
    exit 1
    ;;
esac
