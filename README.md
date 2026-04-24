# Climate Pipeline on Stable Framework

| Group Number | Cluster Number | Member | NetID |
| --- | --- | --- | --- |
| 15 | 5 | Yuyao Tang | yt237 |
| 15 | 5 | Ziang Yang | zy234 |
| 15 | 5 | Yanhe Zhu | yz1054 |
| 15 | 5 | Xianghao Zheng | xz473 |

We built this project as a staged climate-data pipeline on top of a stable Kubernetes deployment pattern. The system collects historical hourly weather data from Open-Meteo, stores raw files in MinIO, transforms them into structured parquet datasets, and publishes the final analytical layer into PostgreSQL.

Our end-to-end flow is:

`Open-Meteo API -> Airflow -> MinIO Bronze -> MinIO Silver -> PostgreSQL Gold`

## Repository Layout

```text
PRJ_STABLE_FRAMEWORK/
├── scripts/              Optional helper scripts mainly used for local PC testing; the actual lab deployment flow is documented below
├── k8s/                  Kubernetes manifests for Airflow, MinIO, PostgreSQL, secrets, and namespace
├── docker/               Custom Airflow image definition
├── dags/                 The three Airflow DAGs that orchestrate the pipeline
├── common/               Shared pipeline logic: config, API fetch, transforms, MinIO, PostgreSQL
├── pyproject.toml        Python project metadata and dependencies
├── main.py               Small local entry point for quick code-level checks
└── buildkitd.toml        Local BuildKit registry config for pushing the arm64 Airflow image
```

## Pipeline Design

We separated the pipeline into three DAGs so that each stage has a clear responsibility and a clear output boundary.

### DAG Responsibilities

- `dags/climate_collect_weather.py`
  Fetches the previous business day's raw weather data for each configured city and writes one JSON object per city into the Bronze layer.

- `dags/climate_prepare_weather.py`
  Reads Bronze JSON, validates required fields and ranges, creates derived variables and standardized columns, and writes parquet files into the Silver layer.

- `dags/climate_publish_analytics.py`
  Reads Silver parquet files, loads the analytical schema in PostgreSQL, refreshes serve-layer summaries, and records quality-check metrics.

### DAG Relationship

```text
Scheduled entry point
        |
        v
+---------------------------+
| climate_collect_weather   |
| schedule: 30 5 * * * UTC  |
+---------------------------+
        |
        | verify Bronze
        | trigger downstream
        v
+---------------------------+
| climate_prepare_weather   |
| schedule: None            |
+---------------------------+
        |
        | verify Silver
        | trigger downstream
        v
+---------------------------+
| climate_publish_analytics |
| schedule: None            |
+---------------------------+
        |
        v
PostgreSQL analytics + serve layer
```

### Trigger Properties

- `climate_collect_weather` is the only scheduled DAG.
- `climate_prepare_weather` is triggered by `collect`, but can also be run manually for controlled tests.
- `climate_publish_analytics` is triggered by `prepare`, but can also be run manually if Silver files already exist.
- All three DAGs use `catchup=False`, `depends_on_past=False`, and retry policies.
- Each downstream stage starts only after an explicit file-existence check on the upstream outputs.

### Business-Date Rule

The pipeline processes the **previous day** relative to the Airflow logical run time. For example, a run executed at `2025-03-17 05:30 UTC` collects and publishes data for `2025-03-16`.

### Date Reuse Policy

For normal operation and demonstration, we treat each business date as a single canonical partition rather than a date that should be repeatedly re-collected. The Bronze and Silver object keys are deterministic by `location + date`, and the publication step rewrites the same analytical time window instead of appending duplicate final rows. In practice, this means we avoid reusing dates that have already been loaded in the cluster and choose a new historical date for each fresh end-to-end test.

### Data Layers

- **Bronze**
  Raw JSON payloads stored in MinIO under `climate_hourly_bronze/location=<location_id>/<year>/<yyyymmdd>.json`

- **Silver**
  Validated and transformed parquet files stored in MinIO under `climate_hourly_silver/location=<location_id>/<year>/<yyyymmdd>.parquet`

- **Gold**
  Analytical tables and views in PostgreSQL, including `hourly_observations`, `mv_daily_summary`, `mv_monthly_summary`, and serve-layer derived tables

## Setup Instructions

These instructions follow the lab deployment flow we used on a two-node MicroK8s cluster:

- `node1 = 10.10.5.21` as the control plane
- `node2 = 10.10.5.22` as the worker
- `student` as the SSH user
- first-pass deployment with both `compute` and `storage` pinned to `node1`

The Airflow image is pushed to:

```text
10.10.5.21:32000/climate-airflow-stable:latest
```

PostgreSQL is pulled directly from:

```text
postgres:16-bookworm
```

### 1. Copy the project to `node1`

**Run on Local PC**

```powershell
cd F:\DE_final_f
ssh student@10.10.5.21 "mkdir -p ~/DE_final_f"
scp -r .\PRJ_STABLE_FRAMEWORK student@10.10.5.21:~/DE_final_f/
```

### 2. Check MicroK8s and enable the built-in registry

**Run on `node1`**

```bash
ssh student@10.10.5.21
sudo microk8s status --wait-ready
sudo microk8s kubectl get nodes -o wide
sudo microk8s enable registry
sudo microk8s kubectl get svc -A | grep 32000
```

If `node2` is not yet part of the cluster:

```bash
sudo microk8s add-node
```

Then run the printed `--worker` join command on `node2`.

### 3. Configure both nodes to trust `10.10.5.21:32000`

**Run on `node1`**

```bash
sudo mkdir -p /var/snap/microk8s/current/args/certs.d/10.10.5.21:32000
cat <<'EOF' | sudo tee /var/snap/microk8s/current/args/certs.d/10.10.5.21:32000/hosts.toml
server = "http://10.10.5.21:32000"

[host."http://10.10.5.21:32000"]
capabilities = ["pull", "resolve"]
EOF
sudo microk8s stop
sudo microk8s start
sudo microk8s status --wait-ready
```

**Run on `node2`**

```bash
ssh student@10.10.5.22
sudo mkdir -p /var/snap/microk8s/current/args/certs.d/10.10.5.21:32000
cat <<'EOF' | sudo tee /var/snap/microk8s/current/args/certs.d/10.10.5.21:32000/hosts.toml
server = "http://10.10.5.21:32000"

[host."http://10.10.5.21:32000"]
capabilities = ["pull", "resolve"]
EOF
sudo snap stop microk8s
sudo snap start microk8s
```

### 4. Prepare host storage and label `node1`

**Run on `node1`**

```bash
ssh student@10.10.5.21
sudo mkdir -p /var/data/climate-stable/postgres
sudo mkdir -p /var/data/climate-stable/minio
sudo microk8s kubectl label node node1 node-role.kubernetes.io/storage= --overwrite
sudo microk8s kubectl label node node1 node-role.kubernetes.io/compute= --overwrite
sudo microk8s kubectl get nodes --show-labels
```

### 5. Build and push the Airflow image for `linux/arm64`

**Run on Local PC**

Create the local BuildKit registry config:

```powershell
cd F:\DE_final_f\PRJ_STABLE_FRAMEWORK
@'
[registry."10.10.5.21:32000"]
  http = true
'@ | Set-Content .\buildkitd.toml
```

If this is the first build on this machine, enable multi-architecture support and create the builder:

```powershell
docker run --privileged --rm tonistiigi/binfmt --install all
docker buildx create --name armbuilder --driver docker-container --buildkitd-config .\buildkitd.toml --bootstrap --use
docker buildx inspect --bootstrap
```

Then build and push:

```powershell
docker buildx build --platform linux/arm64 -t 10.10.5.21:32000/climate-airflow-stable:latest -f .\docker\airflow\Dockerfile . --push
```

### 6. Deploy PostgreSQL and MinIO

**Run on `node1`**

```bash
ssh student@10.10.5.21
cd ~/DE_final_f/PRJ_STABLE_FRAMEWORK
sudo microk8s kubectl apply -f ./k8s/namespace.yaml -f ./k8s/secrets.yaml
sudo microk8s kubectl apply -f ./k8s/postgres.yaml -f ./k8s/minio.yaml
sudo microk8s kubectl -n climate-stable get pods -o wide
```

Wait until both of these are `Running`:

- `climate-postgres`
- `climate-minio`

### 7. Deploy Airflow

**Run on `node1`**

```bash
cd ~/DE_final_f/PRJ_STABLE_FRAMEWORK
sudo microk8s kubectl apply -f ./k8s/airflow.yaml
sudo microk8s kubectl -n climate-stable get pods -o wide
```

Wait until `climate-airflow` is fully running.

### 8. Verify the DAGs and unpause them

**Run on `node1`**

```bash
sudo microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags list
```

You should see:

- `climate_collect_weather`
- `climate_prepare_weather`
- `climate_publish_analytics`

Then unpause them:

```bash
sudo microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags unpause climate_collect_weather
sudo microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags unpause climate_prepare_weather
sudo microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags unpause climate_publish_analytics
```

### 9. Trigger a test run

Choose a business date that has not already been used in the cluster. In our current lab cluster, previously used examples include:

- `2026-03-11`
- `2026-03-12`
- `2026-03-13`
- `2026-03-16`
- `2026-03-18`
- `2026-04-21`
- `2026-04-22`

For a clean demonstration, the example below uses `2025-03-16`.

**Run on `node1`**

```bash
sudo microk8s kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags trigger climate_collect_weather -e 2025-03-17T05:30:00+00:00 -r collect_auto_20250316
```

### 10. Check the outputs

**Run on `node1`**

Check MinIO:

```bash
cat <<'PY' | sudo microk8s kubectl -n climate-stable exec -i deployment/climate-airflow -c airflow-scheduler -- python -
from common.minio_utils import get_s3_client
s3 = get_s3_client()
bronze = s3.list_objects_v2(Bucket="bronze")
silver = s3.list_objects_v2(Bucket="silver")
print("bronze_files_20250316 =", sum(1 for o in bronze.get("Contents", []) if o["Key"].endswith("20250316.json")))
print("silver_files_20250316 =", sum(1 for o in silver.get("Contents", []) if o["Key"].endswith("20250316.parquet")))
PY
```

Check PostgreSQL:

```bash
sudo microk8s kubectl -n climate-stable exec deployment/climate-postgres -- psql -U climate_user -d climate_analytics -c "select count(*) as rows_2025_03_16 from hourly_observations where timestamp >= '2025-03-16' and timestamp < '2025-03-17';"
```

## Service Access

To access the UIs from your local machine, create SSH port forwarding.

**Run on Local PC**

```powershell
ssh -N `
  -L 8080:10.10.5.21:31280 `
  -L 9001:10.10.5.21:30931 `
  -L 9000:10.10.5.21:30930 `
  student@10.10.5.21
```

Then open:

- Airflow UI: `http://localhost:8080`
- MinIO Console: `http://localhost:9001`

Default credentials if unchanged:

- Airflow: `admin` / `change-me-admin-password`
- MinIO: `minioadmin` / `change-me-minio-password`
