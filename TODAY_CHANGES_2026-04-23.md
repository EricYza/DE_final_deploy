# Work Summary - 2026-04-23

This file summarizes the code changes, deployment adjustments, and testing completed on April 23, 2026.

## 1. Naming Cleanup

The original stage naming based on `bronze / silver / gold` was changed to clearer pipeline-stage names.

### DAG file renames

- `dags/climate_ingest_bronze.py` -> `dags/climate_collect_weather.py`
- `dags/climate_transform_silver.py` -> `dags/climate_prepare_weather.py`
- `dags/climate_load_gold.py` -> `dags/climate_publish_analytics.py`

### DAG ID renames

- `climate_collect_weather`
- `climate_prepare_weather`
- `climate_publish_analytics`

This makes the pipeline easier to explain in demos and reports.

## 2. Local Deployment Configuration

The project was adjusted from the lab registry to a local registry for local-kind deployment and testing.

### Repository file changes

- `scripts/build_and_push.sh`
  - Default registry changed to `localhost:32000`
- `k8s/airflow.yaml`
  - Airflow image updated to `localhost:32000/climate-airflow-stable:latest`
- `k8s/postgres.yaml`
  - PostgreSQL image updated to `localhost:32000/postgres:16-bookworm`

### Local environment actions performed

These were operational setup changes, not repository-file changes:

- Created/started local Docker registry on `localhost:32000`
- Connected the registry to the `kind` Docker network
- Configured the kind node container runtime to resolve `localhost:32000`
- Added node labels required by `nodeSelector`
- Created persistent storage directories for PostgreSQL and MinIO

## 3. Main Pipeline Orchestration Fix

### Problem found

Originally, the three DAGs existed as separate stages, but they were not reliably chained together.

That caused this failure mode:

1. `collect` wrote `bronze` data
2. `prepare` started too early and skipped missing upstream files
3. `publish` started too early and skipped missing parquet files
4. Airflow could still show successful runs even though downstream data was not actually produced

### Fix implemented

The pipeline was changed so that downstream stages only run after upstream data is both:

- completed
- explicitly verified as available

### Updated behavior

- `climate_collect_weather` remains the scheduled entrypoint
- after `collect` finishes, it verifies all expected `bronze` objects exist
- then it automatically triggers `climate_prepare_weather`
- `climate_prepare_weather` first verifies all required `bronze` objects exist
- after transformation, it verifies all expected `silver` parquet files exist
- then it automatically triggers `climate_publish_analytics`
- `climate_publish_analytics` first verifies all expected `silver` files exist before loading PostgreSQL

### DAG scheduling change

- `climate_collect_weather` keeps its daily schedule
- `climate_prepare_weather` was changed to `schedule=None`
- `climate_publish_analytics` was changed to `schedule=None`

This makes `collect` the only scheduled entrypoint, which then orchestrates the other two stages automatically.

## 4. Files Modified Today

### Pipeline code

- `dags/climate_collect_weather.py`
- `dags/climate_prepare_weather.py`
- `dags/climate_publish_analytics.py`

### Deployment/configuration

- `scripts/build_and_push.sh`
- `k8s/airflow.yaml`
- `k8s/postgres.yaml`

### Documentation

- `README.md`
- `ACCEPTANCE_TESTS.md`

## 5. Image Rebuild and Rollout

After DAG orchestration changes, the Airflow image was:

1. rebuilt locally
2. pushed to local registry
3. rolled out to Kubernetes

This ensured the cluster used the new DAG behavior.

## 6. Testing Completed

## 6.1 Basic end-to-end validation

Verified that the pipeline can successfully move data through:

- Open-Meteo -> MinIO bronze JSON
- MinIO bronze -> MinIO silver parquet
- MinIO silver -> PostgreSQL analytics tables

Confirmed with actual data:

- JSON files visible in `bronze`
- Parquet files visible in `silver`
- rows visible in PostgreSQL

## 6.2 Automatic chained execution validation

Verified that after the orchestration fix, triggering only:

- `climate_collect_weather`

automatically leads to:

- `climate_prepare_weather`
- `climate_publish_analytics`

Successful proof case:

- Business date `2026-03-16`
- `bronze_files_20260316 = 5`
- `silver_files_20260316 = 5`
- PostgreSQL `rows_2026_03_16 = 120`

Additional proof case:

- Business date `2026-03-15`
- `bronze_files_20260315 = 5`
- `silver_files_20260315 = 5`
- PostgreSQL `rows_2026_03_15 = 120`

Interpretation:

- only one collect trigger is now needed
- downstream stages run automatically and in order

## 6.3 Multi-location validation

Confirmed that all 5 configured locations were processed correctly.

Expected and observed:

- 5 cities
- 24 hourly records per city per day
- total rows per day = `120`

## 6.4 Repeated collection over multiple days

Validated that the pipeline can ingest multiple business dates, not just a single day.

This demonstrates support for repeated historical collection over longer time windows.

## 6.5 Missing-value handling

Custom bronze data was created with the `pressure_msl` field removed for Cairo.

Observed behavior:

- bronze JSON no longer contained `pressure_msl`
- silver parquet contained `pressure_hpa` column but all values were missing
- PostgreSQL stored the missing numeric values as `NaN`

Important interpretation:

- missing values are preserved through the pipeline
- the pipeline does not fail on incomplete numeric input
- current database representation uses `NaN` rather than SQL `NULL`

For the course project, this is acceptable as a missing-value handling implementation.

## 6.6 Higher-resolution data test

Created a custom Cairo test case with 48 half-hour records for one day.

Observed result:

- PostgreSQL stored `48` Cairo records for that date

Interpretation:

- the current data model can accommodate sub-hourly data

## 7. Acceptance Test Document Added

A reusable test guide was created:

- `ACCEPTANCE_TESTS.md`

This file contains:

- copy-pasteable commands
- expected outputs
- notes about missing values
- extension tests for new locations and new variables

This document can be reused on the lab cluster.

## 8. Current Status Against Project Goals

### Clearly satisfied

- end-to-end ingest / process / serve pipeline
- multi-location support
- repeated data collection across multiple dates
- automatic ordered execution from collect to prepare to publish
- missing-value robustness
- support for higher-resolution data at the data-model level

### Partially satisfied

- add new variables without redesign

Reason:

- new variables can be collected into `bronze`
- but they do not automatically flow through `silver` and PostgreSQL without further transform/schema changes

## 9. Known Limitation

The current PostgreSQL layer stores missing numeric values as `NaN` instead of SQL `NULL`.

This is acceptable for the current project scope, but could be improved later for cleaner SQL semantics.

## 10. Recommended Next Step

Focus can now shift from implementation/testing to:

- report writing
- architecture explanation
- results summary
- limitations / future work section

