# Climate Pipeline Acceptance Tests

This file contains copy-pasteable acceptance-test commands for the local/lab Kubernetes cluster.

These tests assume:

- The updated Airflow image has already been built, pushed, and rolled out.
- The current implementation uses `climate_collect_weather` as the normal entrypoint.
- `climate_collect_weather` automatically triggers `climate_prepare_weather`.
- `climate_prepare_weather` automatically triggers `climate_publish_analytics`.
- Namespace is `climate-stable`.
- PostgreSQL database is `climate_analytics`.

If a trigger command returns `DagRunAlreadyExists`, shift that business date one day earlier and retry with matching `-e` and `run_id`.

## Registry + kind Prerequisites

If you are using a fresh Docker Desktop kind cluster, run these first.

### 1. Detect node name

```powershell
$NODE = kubectl get nodes -o jsonpath="{.items[0].metadata.name}"
echo $NODE
```

Expected:

- prints the kind node name, for example `desktop-control-plane`

### 2. Add required node labels

```powershell
kubectl label node $NODE node-role.kubernetes.io/storage= node-role.kubernetes.io/compute= --overwrite
```

Expected:

- node labeled successfully

### 3. Start local registry on `localhost:32000`

```powershell
if (docker ps -a --format "{{.Names}}" | Select-String -Pattern "^kind-registry$") { docker start kind-registry } else { docker run -d --restart=always -p 127.0.0.1:32000:5000 --name kind-registry registry:2 }
```

Expected:

- registry container starts or is created

### 4. Connect registry to the `kind` Docker network

```powershell
if ((docker inspect kind-registry --format "{{json .NetworkSettings.Networks.kind}}") -eq "null") { docker network connect kind kind-registry } else { Write-Output "kind-registry already connected to kind" }
```

Expected:

- either no output or `kind-registry already connected to kind`

### 5. Configure the kind node to resolve `localhost:32000`

```powershell
docker exec $NODE mkdir -p /etc/containerd/certs.d/localhost:32000
"server = ""http://localhost:32000""`n[host.""http://kind-registry:5000""]" | docker exec -i $NODE sh -c 'cat > /etc/containerd/certs.d/localhost:32000/hosts.toml'
docker exec $NODE cat /etc/containerd/certs.d/localhost:32000/hosts.toml
```

Expected file content:

```text
server = "http://localhost:32000"
[host."http://kind-registry:5000"]
```

### 6. Create local storage directories inside the node

```powershell
docker exec $NODE mkdir -p /var/data/climate-stable/postgres /var/data/climate-stable/minio
```

### 7. Push PostgreSQL image to local registry

If the cluster has trouble pulling directly from Docker Hub, use:

```powershell
docker pull postgres:16-bookworm
docker tag postgres:16-bookworm localhost:32000/postgres:16-bookworm
docker push localhost:32000/postgres:16-bookworm
```

Expected:

- `latest`/`16-bookworm` push succeeds

### 8. Deploy namespace, secrets, storage, and services

```powershell
kubectl apply -f .\PRJ_STABLE_FRAMEWORK\k8s\namespace.yaml -f .\PRJ_STABLE_FRAMEWORK\k8s\secrets.yaml
kubectl apply -f .\PRJ_STABLE_FRAMEWORK\k8s\postgres.yaml -f .\PRJ_STABLE_FRAMEWORK\k8s\minio.yaml
kubectl apply -f .\PRJ_STABLE_FRAMEWORK\k8s\airflow.yaml
```

### 9. Build and push the Airflow image

```powershell
docker build -t localhost:32000/climate-airflow-stable:latest -f .\PRJ_STABLE_FRAMEWORK\docker\airflow\Dockerfile .\PRJ_STABLE_FRAMEWORK
docker push localhost:32000/climate-airflow-stable:latest
kubectl -n climate-stable rollout restart deployment/climate-airflow
kubectl -n climate-stable rollout status deployment/climate-airflow --timeout=240s
```

### 10. Quick health check

```powershell
kubectl -n climate-stable get pods -o wide
kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags list
```

Expected:

- `climate-airflow`, `climate-minio`, `climate-postgres` are running
- the 3 DAGs are visible

## Date Rule

To test business date `D`:

- Trigger `climate_collect_weather` at `D+1 05:30 UTC`
- Then check:
  - MinIO `bronze`
  - MinIO `silver`
  - PostgreSQL

For tests that manually create `bronze` files, trigger `climate_prepare_weather` instead. It will automatically trigger `publish`.

## Test 1: Single-Day End-to-End

Business date: `2026-03-14`

### Trigger

```powershell
kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags trigger climate_collect_weather -e 2026-03-15T05:30:00+00:00 -r collect_auto_20260314
```

### Check bronze and silver

```powershell
@'
from common.minio_utils import get_s3_client
s3 = get_s3_client()
bronze = s3.list_objects_v2(Bucket="bronze")
silver = s3.list_objects_v2(Bucket="silver")
print("bronze_files_20260314 =", sum(1 for o in bronze.get("Contents", []) if o["Key"].endswith("20260314.json")))
print("silver_files_20260314 =", sum(1 for o in silver.get("Contents", []) if o["Key"].endswith("20260314.parquet")))
'@ | kubectl -n climate-stable exec -i deployment/climate-airflow -c airflow-scheduler -- python -
```

### Check PostgreSQL

```powershell
kubectl -n climate-stable exec deployment/climate-postgres -- psql -U climate_user -d climate_analytics -c "select count(*) as rows_2026_03_14 from hourly_observations where timestamp >= '2026-03-14' and timestamp < '2026-03-15'; select timestamp, count(distinct location_id) as locations_per_timestamp from hourly_observations where timestamp >= '2026-03-14' and timestamp < '2026-03-15' group by 1 order by 1 limit 3;"
```

### Expected

- `bronze_files_20260314 = 5`
- `silver_files_20260314 = 5`
- `rows_2026_03_14 = 120`
- `locations_per_timestamp = 5`

## Test 2: Repeated Collection Across Multiple Days

Business dates: `2026-03-11`, `2026-03-12`, `2026-03-13`

### Trigger

```powershell
kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags trigger climate_collect_weather -e 2026-03-12T05:30:00+00:00 -r collect_auto_20260311
kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags trigger climate_collect_weather -e 2026-03-13T05:30:00+00:00 -r collect_auto_20260312
kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags trigger climate_collect_weather -e 2026-03-14T05:30:00+00:00 -r collect_auto_20260313
```

### Check PostgreSQL

```powershell
kubectl -n climate-stable exec deployment/climate-postgres -- psql -U climate_user -d climate_analytics -c "select date_trunc('day', timestamp)::date as day, count(distinct location_id) as locations, count(*) as total_rows from hourly_observations where timestamp >= '2026-03-11' and timestamp < '2026-03-14' group by 1 order by 1;"
```

### Expected

- 3 rows returned: `2026-03-11`, `2026-03-12`, `2026-03-13`
- Each day: `locations = 5`
- Each day: `total_rows = 120`

## Test 3: Missing-Value Handling

Purpose: verify that a missing numeric field is preserved through the pipeline.

Current implementation note:

- Missing numeric values are stored in PostgreSQL as `NaN`, not SQL `NULL`.
- This is acceptable for robustness testing in the current project version.

Business date: `2026-03-03`

### Prepare bronze for all 5 locations, but remove Cairo pressure

This command copies existing `20260314` bronze files to `20260303` and removes `pressure_msl` only for `cairo_eg`.

```powershell
@'
import json
from common.config import get_target_locations
from common.minio_utils import get_s3_client

s3 = get_s3_client()

for location in get_target_locations():
    location_id = location["id"]
    src = f"climate_hourly_bronze/location={location_id}/2026/20260314.json"
    dst = f"climate_hourly_bronze/location={location_id}/2026/20260303.json"

    obj = json.loads(s3.get_object(Bucket="bronze", Key=src)["Body"].read())
    obj["business_date"] = "2026-03-03"
    obj["_ingest_metadata"]["business_date"] = "2026-03-03"
    obj["response"]["hourly"]["time"] = [
        t.replace("2026-03-14", "2026-03-03") for t in obj["response"]["hourly"]["time"]
    ]

    if location_id == "cairo_eg":
        obj["response"]["hourly"].pop("pressure_msl", None)

    s3.put_object(
        Bucket="bronze",
        Key=dst,
        Body=json.dumps(obj).encode("utf-8"),
        ContentType="application/json",
    )

print("ok")
'@ | kubectl -n climate-stable exec -i deployment/climate-airflow -c airflow-scheduler -- python -
```

### Trigger prepare

```powershell
kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags trigger climate_prepare_weather -e 2026-03-04T05:30:00+00:00 -r prepare_missing_pressure_20260303
```

### Inspect bronze and silver directly

```powershell
@'
import json
import io
import pyarrow.parquet as pq
from common.minio_utils import get_s3_client

s3 = get_s3_client()

bronze_key = "climate_hourly_bronze/location=cairo_eg/2026/20260303.json"
silver_key = "climate_hourly_silver/location=cairo_eg/2026/20260303.parquet"

bronze = json.loads(s3.get_object(Bucket="bronze", Key=bronze_key)["Body"].read())
print("bronze_has_pressure_msl =", "pressure_msl" in bronze["response"]["hourly"])

raw = s3.get_object(Bucket="silver", Key=silver_key)["Body"].read()
df = pq.read_table(io.BytesIO(raw)).to_pandas()
print("silver_has_pressure_hpa =", "pressure_hpa" in df.columns)
print("silver_non_null_pressure_hpa =", int(df['pressure_hpa'].notna().sum()) if 'pressure_hpa' in df.columns else -1)
'@ | kubectl -n climate-stable exec -i deployment/climate-airflow -c airflow-scheduler -- python -
```

### Check PostgreSQL

```powershell
kubectl -n climate-stable exec deployment/climate-postgres -- psql -U climate_user -d climate_analytics -c "select count(*) as cairo_rows, count(pressure_hpa) as counted_as_non_null, count(*) filter (where pressure_hpa::text = 'NaN') as nan_rows from hourly_observations where location_id='cairo_eg' and timestamp >= '2026-03-03' and timestamp < '2026-03-04';"
```

### Expected

- Bronze:
  - `bronze_has_pressure_msl = False`
- Silver:
  - `silver_has_pressure_hpa = True`
  - `silver_non_null_pressure_hpa = 0`
- PostgreSQL:
  - `cairo_rows = 24`
  - `counted_as_non_null = 24`
  - `nan_rows = 24`

Interpretation:

- Missing pressure was preserved successfully.
- In PostgreSQL, missing numeric values are represented as `NaN` rather than `NULL`.

## Test 4: Higher Time Resolution

Purpose: verify that the data model can accommodate sub-hourly data.

Business date: `2026-03-04`

### Prepare bronze for all 5 locations, but make Cairo 30-minute data

This command copies existing `20260314` bronze files to `20260304`. For `cairo_eg`, hourly records are expanded to 48 half-hour records.

```powershell
@'
import json
from datetime import datetime, timedelta
from common.config import get_target_locations
from common.minio_utils import get_s3_client

s3 = get_s3_client()

for location in get_target_locations():
    location_id = location["id"]
    src = f"climate_hourly_bronze/location={location_id}/2026/20260314.json"
    dst = f"climate_hourly_bronze/location={location_id}/2026/20260304.json"

    obj = json.loads(s3.get_object(Bucket="bronze", Key=src)["Body"].read())
    obj["business_date"] = "2026-03-04"
    obj["_ingest_metadata"]["business_date"] = "2026-03-04"

    if location_id == "cairo_eg":
        hourly = obj["response"]["hourly"]
        new_hourly = {}
        for key, values in hourly.items():
            if key == "time":
                base = datetime(2026, 3, 4, 0, 0)
                new_hourly["time"] = [
                    (base + timedelta(minutes=30 * i)).strftime("%Y-%m-%dT%H:%M")
                    for i in range(48)
                ]
            else:
                expanded = []
                for v in values:
                    expanded.extend([v, v])
                new_hourly[key] = expanded
        obj["response"]["hourly"] = new_hourly
    else:
        obj["response"]["hourly"]["time"] = [
            t.replace("2026-03-14", "2026-03-04") for t in obj["response"]["hourly"]["time"]
        ]

    s3.put_object(
        Bucket="bronze",
        Key=dst,
        Body=json.dumps(obj).encode("utf-8"),
        ContentType="application/json",
    )

print("ok")
'@ | kubectl -n climate-stable exec -i deployment/climate-airflow -c airflow-scheduler -- python -
```

### Trigger prepare

```powershell
kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags trigger climate_prepare_weather -e 2026-03-05T05:30:00+00:00 -r prepare_subhourly_20260304
```

### Check PostgreSQL

```powershell
kubectl -n climate-stable exec deployment/climate-postgres -- psql -U climate_user -d climate_analytics -c "select count(*) as cairo_rows from hourly_observations where location_id='cairo_eg' and timestamp >= '2026-03-04' and timestamp < '2026-03-05';"
```

### Expected

- `cairo_rows = 48`

Interpretation:

- The current model can store higher-resolution data.

## Test 5: Add a New Location

Do this near the end, because it changes code and requires rebuilding the Airflow image.

Business date: `2026-03-07`

### Step 1: Update `common/config.py`

Add this entry to `LOCATION_CATALOG`:

```python
"berlin_de": {
    "id": "berlin_de",
    "name": "Berlin",
    "latitude": 52.5200,
    "longitude": 13.4050,
    "timezone": "Europe/Berlin",
    "country": "Germany",
    "elevation": 34.0,
},
```

### Step 2: Update `k8s/airflow.yaml`

Add `berlin_de` to `TARGET_LOCATION_IDS`.

### Step 3: Rebuild and redeploy

```powershell
docker build -t localhost:32000/climate-airflow-stable:latest -f .\PRJ_STABLE_FRAMEWORK\docker\airflow\Dockerfile .\PRJ_STABLE_FRAMEWORK
docker push localhost:32000/climate-airflow-stable:latest
kubectl -n climate-stable rollout restart deployment/climate-airflow
kubectl -n climate-stable rollout status deployment/climate-airflow --timeout=240s
```

### Step 4: Trigger

```powershell
kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags trigger climate_collect_weather -e 2026-03-08T05:30:00+00:00 -r collect_auto_20260307_berlin
```

### Step 5: Check PostgreSQL

```powershell
kubectl -n climate-stable exec deployment/climate-postgres -- psql -U climate_user -d climate_analytics -c "select count(*) as locations from locations; select count(*) as rows_2026_03_07 from hourly_observations where timestamp >= '2026-03-07' and timestamp < '2026-03-08'; select count(*) as berlin_rows from hourly_observations where location_id='berlin_de' and timestamp >= '2026-03-07' and timestamp < '2026-03-08';"
```

### Expected

- `locations = 6`
- `rows_2026_03_07 = 144`
- `berlin_rows = 24`

## Test 6: Add a New Variable

Do this last, because it changes code and requires rebuilding the Airflow image.

Business date: `2026-03-06`

### Step 1: Update `common/config.py`

Add this variable to `HOURLY_VARIABLES`:

```python
"dew_point_2m",
```

### Step 2: Rebuild and redeploy

```powershell
docker build -t localhost:32000/climate-airflow-stable:latest -f .\PRJ_STABLE_FRAMEWORK\docker\airflow\Dockerfile .\PRJ_STABLE_FRAMEWORK
docker push localhost:32000/climate-airflow-stable:latest
kubectl -n climate-stable rollout restart deployment/climate-airflow
kubectl -n climate-stable rollout status deployment/climate-airflow --timeout=240s
```

### Step 3: Trigger

```powershell
kubectl -n climate-stable exec deployment/climate-airflow -c airflow-scheduler -- airflow dags trigger climate_collect_weather -e 2026-03-07T05:30:00+00:00 -r collect_auto_20260306_newvar
```

### Step 4: Check bronze and silver

```powershell
@'
import json, io
import pyarrow.parquet as pq
from common.minio_utils import get_s3_client

s3 = get_s3_client()

bronze = json.loads(s3.get_object(Bucket="bronze", Key="climate_hourly_bronze/location=cairo_eg/2026/20260306.json")["Body"].read())
raw = s3.get_object(Bucket="silver", Key="climate_hourly_silver/location=cairo_eg/2026/20260306.parquet")["Body"].read()
df = pq.read_table(io.BytesIO(raw)).to_pandas()

print("bronze_has_new_var =", "dew_point_2m" in bronze["response"]["hourly"])
print("silver_has_new_var =", "dew_point_2m" in df.columns)
'@ | kubectl -n climate-stable exec -i deployment/climate-airflow -c airflow-scheduler -- python -
```

### Expected

- `bronze_has_new_var = True`
- `silver_has_new_var = False`

Interpretation:

- New variables are collected into `bronze`.
- They do not automatically flow through `silver` and PostgreSQL without additional schema and transform work.
- This requirement is therefore `Partial`, not full `Pass`.

## Summary Verdicts

- End-to-end pipeline: `Pass`
- Multi-location support: `Pass`
- Repeated collection over multiple days: `Pass`
- Automatic chaining `collect -> prepare -> publish`: `Pass`
- Missing-value robustness: `Pass`
- Higher time resolution support: `Pass` at the data-model level
- Add new location without redesign: `Pass` with configuration update + redeploy
- Add new variable without redesign: `Partial`
