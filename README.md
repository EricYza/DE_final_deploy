# Climate Pipeline On Stable Framework

This folder is a clean, separate project that keeps the deployment shape of the
stable deployment framework while replacing the EPA business logic with our group's
climate-data business logic.

In other words:

- same base stack: `Kubernetes + Airflow + MinIO + PostgreSQL`
- same deployment flow: `build -> push -> kubectl apply`
- different business logic:
  - data source: Open-Meteo Historical Weather API
  - transformation logic: our climate validation + unit conversion code
  - analytics schema: our climate tables/views

## Architecture

`Open-Meteo API -> Airflow -> MinIO Bronze -> MinIO Silver -> PostgreSQL Gold`

The pipeline is split into the same Bronze/Silver/Gold stages used in the
stable framework:

1. `dags/climate_collect_weather.py`
   Fetch the previous day's hourly weather data for each configured city and
   store the raw API payload in MinIO Bronze as JSON.

2. `dags/climate_prepare_weather.py`
   Read Bronze JSON, validate the records, apply the climate conversions, and
   write transformed Parquet files to MinIO Silver.

3. `dags/climate_publish_analytics.py`
   Read Silver Parquet, load the analytical tables in PostgreSQL, and refresh
   downstream views / serve-layer tables.

## Folder Layout

- `common/`: shared config, MinIO helpers, Open-Meteo fetch logic, transforms, PostgreSQL helpers
- `dags/`: Airflow DAGs
- `docker/airflow/`: Airflow image definition
- `k8s/`: Kubernetes manifests
- `scripts/`: build / deploy scripts

## Why This Folder Exists

`PRJ/` still contains your original group project layout.

`PRJ_STABLE_FRAMEWORK/` is the version where the bottom framework is aligned
to the stable deployment model:

- plain manifests instead of Helm
- explicit `build_and_push.sh`
- explicit `deploy.sh`
- Airflow webserver + scheduler deployment
- MinIO + PostgreSQL services managed by Kubernetes manifests

## Deployment Flow

Run these commands on the node where you normally use `docker` and `kubectl`.

### 1. Prepare storage directories

```bash
mkdir -p /var/data/climate-stable/postgres
mkdir -p /var/data/climate-stable/minio
```

### 2. Label cluster nodes

If you use separate storage and compute nodes:

```bash
kubectl label node <storage-node> node-role.kubernetes.io/storage= --overwrite
kubectl label node <compute-node> node-role.kubernetes.io/compute= --overwrite
```

If one node must do both jobs, it can carry both labels.

### 3. Edit secrets

Update [k8s/secrets.yaml](/Users/13681080795163.com/Desktop/Duke/data_engineering/ECE590-DE/PRJ_STABLE_FRAMEWORK/k8s/secrets.yaml:1) before deploying.

### 4. Build and push

```bash
cd PRJ_STABLE_FRAMEWORK
./scripts/build_and_push.sh
```

### 5. Deploy

```bash
./scripts/deploy.sh
```

### 6. Access services

- Airflow UI: `http://<node-ip>:31280`
- MinIO API: `http://<node-ip>:30930`
- MinIO Console: `http://<node-ip>:30931`
- PostgreSQL: `<node-ip>:31462`

## Key Difference From EPA Version

Only the task logic changes:

- EPA version: EPA air-quality API + air-quality transforms
- this folder: Open-Meteo climate API + climate transforms / climate analytics

The deployment skeleton stays intentionally simple and stable.
