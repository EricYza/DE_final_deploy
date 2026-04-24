# Pipeline Design, DAG Structure, and Technology Choices

## 1. Project Overview

Our team designed this project as a staged climate-data pipeline built on top of a stable deployment framework. The end-to-end flow is:

`Open-Meteo Historical Weather API -> Airflow -> MinIO Bronze -> MinIO Silver -> PostgreSQL Gold`

The pipeline collects historical hourly weather observations for a fixed set of cities, validates and standardizes the records, and then publishes analytical tables and serve-layer summaries for downstream queries. The current configuration targets five locations:

- Durham, NC
- London
- Tokyo
- Sydney
- Cairo

At the API level, the pipeline collects hourly weather variables such as temperature, humidity, precipitation, wind speed, pressure, cloud cover, weather code, and evapotranspiration. These variables are configured centrally so that the workflow remains consistent across all three pipeline stages.

Our design goal was not only to produce correct data, but also to make the workflow easy to explain, easy to rerun, and easy to validate in a cluster environment. For that reason, we separated the work into three Airflow DAGs that mirror the Bronze, Silver, and Gold stages.

## 2. Pipeline Design

We organized the pipeline as three explicit stages rather than one large monolithic DAG:

1. `climate_collect_weather`
2. `climate_prepare_weather`
3. `climate_publish_analytics`

This separation gives the pipeline a clear boundary between raw ingestion, structured transformation, and analytical publication.

The storage layout also follows that same logic:

- **Bronze** stores raw JSON payloads from the API.
- **Silver** stores validated and transformed parquet files.
- **Gold** stores final analytical tables and views in PostgreSQL.

This design helped our team in three ways:

- It made debugging easier because each stage has a clear input and output.
- It made acceptance testing easier because we could verify Bronze, Silver, and Gold independently.
- It made special tests possible, such as missing-value tests and higher-time-resolution tests, without redesigning the whole system.

## 3. DAG Design

### 3.1 `climate_collect_weather`

This DAG is the scheduled entry point of the entire pipeline. Its responsibility is to fetch raw weather data for the previous business day and store it in the Bronze layer.

Its task flow is:

1. Check whether the Open-Meteo API is available.
2. Ensure that the Bronze and Silver MinIO buckets exist.
3. Fetch one raw JSON payload per configured location.
4. Verify that all expected Bronze objects were created.
5. Trigger the preparation DAG.

The fetch step runs once per location, so the DAG naturally supports multi-location collection while keeping each task small and traceable.

Each raw object is written to a deterministic key pattern:

`climate_hourly_bronze/location=<location_id>/<year>/<yyyymmdd>.json`

The DAG also writes ingestion metadata such as:

- Airflow run ID
- dataset name
- location ID
- business date
- fetch timestamp

That metadata makes the raw layer more auditable and easier to inspect during demonstrations or debugging.

### 3.2 `climate_prepare_weather`

This DAG is responsible for converting Bronze JSON into structured Silver parquet files.

Its task flow is:

1. Verify that all expected Bronze files exist.
2. Read each Bronze JSON object.
3. Convert the payload into a dataframe.
4. Validate required columns and expected ranges.
5. Standardize the data model and create derived columns.
6. Write parquet outputs to Silver.
7. Verify that all expected Silver files exist.
8. Trigger the publish DAG.

The output key pattern is:

`climate_hourly_silver/location=<location_id>/<year>/<yyyymmdd>.parquet`

This stage is where most of the data-standardization logic lives. For example, it converts:

- Celsius to Fahrenheit
- millimeters to inches
- kilometers per hour to miles per hour and meters per second
- pressure values into both hectopascals and inches of mercury

It also preserves missing values instead of failing immediately on incomplete numeric input. This made the pipeline more robust during testing.

Another important design property is that this DAG can be triggered independently. That is useful for controlled experiments where Bronze data is intentionally modified before downstream processing.

### 3.3 `climate_publish_analytics`

This DAG is responsible for loading Silver parquet files into the final analytical database.

Its task flow is:

1. Verify that all expected Silver objects exist.
2. Initialize the PostgreSQL schema if needed.
3. Read each parquet file and load it into PostgreSQL.
4. Refresh the serve layer.
5. Run quality checks.

The publication step writes into relational tables such as:

- `locations`
- `hourly_observations`

It then refreshes analytical objects such as:

- `mv_daily_summary`
- `mv_monthly_summary`
- `vw_extreme_events`
- `vw_location_stats`
- derived tables for heatwaves, heavy rain, volatility, and city comparison

This stage turns processed records into a queryable analytical layer suitable for SQL-based reporting and demonstration queries.

## 4. Trigger Properties and Execution Semantics

The three DAGs were intentionally designed with different trigger behaviors.

| DAG | Schedule | Trigger Type | Downstream Behavior |
| --- | --- | --- | --- |
| `climate_collect_weather` | Daily at `05:30 UTC` | Scheduled or manual | Triggers `climate_prepare_weather` |
| `climate_prepare_weather` | `None` | Triggered by collect or manual | Triggers `climate_publish_analytics` |
| `climate_publish_analytics` | `None` | Triggered by prepare or manual | Final stage |

Additional trigger-related properties are consistent across the DAGs:

- `catchup=False` to avoid creating a backlog of historical runs automatically
- `depends_on_past=False` so each run is independent
- retries enabled for resilience
- explicit verification steps before any downstream trigger

The most important execution rule is that the pipeline uses **logical date minus one day** as the business date. In practice, this means a run executed at `2026-03-17 05:30 UTC` collects and processes data for `2026-03-16`.

This design made the schedule easier to explain and ensured that each run operates on a complete previous-day dataset instead of partially available same-day data.

The downstream triggers use the same logical timestamp as the upstream DAG. As a result, the three stages stay aligned around the same business date.

The trigger operators also use `reset_dag_run=True`. This makes repeated testing more predictable because rerunning the same logical date replaces the old downstream run instead of leaving stale overlapping runs in Airflow.

## 5. Reliability and Data Integrity Design

Our team added explicit verification steps between stages to avoid a common orchestration problem: downstream tasks starting before upstream outputs are fully available.

That design choice is visible throughout the pipeline:

- `collect` verifies Bronze before triggering `prepare`
- `prepare` verifies Silver before triggering `publish`
- `publish` verifies Silver again before loading PostgreSQL

This is important because a DAG can appear to complete successfully even if a downstream stage starts too early and processes incomplete data. By checking for the existence of every expected object, we reduced the chance of silent partial success.

We also built idempotent behavior into the later stages:

- `prepare` skips parquet files that already exist
- `publish` removes existing rows for the same location and time window before inserting new rows

That made repeated test runs safer and reduced the chance of duplicate analytical records.

## 6. Why We Used These Technologies

During the course, we worked with several data and systems technologies, including PostgreSQL, MongoDB, Kubernetes, and MinIO/S3-style object storage. For this project, we did not treat them as interchangeable. Instead, we assigned each technology the role where it was strongest.

### 6.1 Airflow for orchestration

We used Airflow because this project is fundamentally a workflow problem, not just a storage problem.

Airflow gave us several advantages:

- explicit task dependencies
- retry policies
- manual and scheduled triggering
- visibility into each stage of execution
- easy demonstration of ordered stage progression

A simpler script-based approach would have been possible for a single run, but it would have been much harder to explain reruns, failures, partial success, or stage-by-stage validation. Airflow made the pipeline behavior transparent.

### 6.2 MinIO for Bronze and Silver object storage

We used MinIO as our S3-compatible object store for the Bronze and Silver layers.

This was a better fit than storing raw and intermediate data directly in PostgreSQL or MongoDB for several reasons:

- Raw API payloads are naturally file-oriented documents.
- Intermediate parquet outputs are analytical files, not transactional records.
- Object storage is a natural boundary between ingestion and publication.
- S3 compatibility makes the design portable and easy to reason about.

MinIO also allowed us to keep the raw source payloads exactly as collected while using parquet for compact, typed Silver outputs.

Compared with storing everything in a relational database, this reduced unnecessary schema pressure in the early pipeline stages. Compared with MongoDB, MinIO was a better match for immutable stage artifacts and file-based processing.

### 6.3 PostgreSQL for the Gold and serve layers

We used PostgreSQL as the final analytical store because the published layer is strongly structured and query-oriented.

PostgreSQL was the right choice for this stage because we needed:

- a stable relational schema
- joins between dimension and fact data
- SQL aggregations
- materialized views
- consistent tabular outputs for reporting
- predictable query behavior during demonstrations

This was a stronger fit than MongoDB for our final layer. MongoDB is flexible for document storage, but our project depends on structured analytical queries such as:

- daily summaries by location
- monthly rollups
- extreme-event filtering
- serve-layer views with relational attributes

These are naturally expressed in SQL and benefit from relational integrity. Using PostgreSQL let us model the analytical layer cleanly and explain it clearly.

### 6.4 Why we did not use MongoDB as the primary analytical store

MongoDB is useful when the data model is highly variable, document-heavy, or read primarily as nested JSON. Our workflow did include JSON at the Bronze stage, but our final analytical outputs were not document-centric.

Once the pipeline moved beyond raw ingestion, our data had clear structure:

- fixed observation timestamps
- one location dimension
- numerical weather measurements
- derived metrics
- summary views

At that point, PostgreSQL provided a better balance of structure, query power, and explainability. We therefore treated flexible JSON storage as an ingestion concern and relational analytics as the final publication concern.

### 6.5 Kubernetes for deployment

We deployed the system on Kubernetes because the project is a multi-service data platform rather than a single application.

Our deployment includes:

- Airflow webserver and scheduler
- PostgreSQL
- MinIO
- persistent volumes
- service exposure for demonstration and testing

Kubernetes gave us several practical advantages:

- consistent deployment manifests
- service discovery inside the cluster
- restart and self-healing behavior
- separation of storage and compute roles by node label
- a realistic environment for demonstrating distributed deployment

Compared with running each container manually, Kubernetes made the architecture more reproducible and easier to manage across the two-node MicroK8s cluster.

## 7. Design Tradeoffs

Our design intentionally favors clarity and robustness over maximum complexity.

Some of the main tradeoffs are:

- We use three DAGs instead of one large DAG because the stages are easier to test and explain separately.
- We use object storage plus PostgreSQL instead of one storage engine for everything because raw data, intermediate data, and analytical data have different access patterns.
- We use scheduled collection but triggered downstream stages so that only one DAG acts as the operational entry point.
- We accept some operational overhead from Kubernetes in exchange for a deployment model that is closer to a real distributed data platform.

These tradeoffs were reasonable for our course goals because the project needed to demonstrate both end-to-end correctness and system design choices, not just code execution.

## 8. Summary

Our team designed the pipeline as a staged, verifiable workflow with explicit transitions between raw collection, structured transformation, and analytical publication.

The three DAGs are not independent scripts. They are coordinated stages with clearly defined trigger rules and boundary checks:

- `climate_collect_weather` creates Bronze
- `climate_prepare_weather` creates Silver
- `climate_publish_analytics` creates Gold and refreshes the serve layer

The technology choices follow the same staged logic:

- Airflow for orchestration
- MinIO for Bronze and Silver object storage
- PostgreSQL for Gold analytics and serve-layer queries
- Kubernetes for deployment and service coordination

Together, these choices gave us a pipeline that is modular, testable, and appropriate for both technical evaluation and project demonstration.
