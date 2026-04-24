"""
Author: Ziang Yang
Description: Airflow DAG for the Gold-stage publication workflow in the climate data pipeline.
Reads transformed Silver parquet files from MinIO, initializes and loads the PostgreSQL
analytical schema, refreshes serve-layer summaries and derived tables, and records pipeline
quality-check metrics for the published dataset.

"""

from __future__ import annotations

from datetime import datetime, timedelta
import io
import logging

from airflow import DAG
from airflow.operators.python import PythonOperator
import pyarrow.parquet as pq

from common.config import SILVER_BUCKET, SILVER_HOURLY_PREFIX, get_target_locations
from common.minio_utils import download_bytes, get_s3_client, object_exists
from common.pg_utils import (
    fetch_gold_quality_metrics as fetch_analytics_quality_metrics,
    init_schema,
    refresh_serve_layer,
    upsert_observation_dataframe,
)

logger = logging.getLogger(__name__)

default_args = {
    "owner": "climate-pipeline",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=3),
}


def _expected_prepared_objects(logical_date):
    target_date = (logical_date - timedelta(days=1)).date()
    date_nodash = target_date.strftime("%Y%m%d")
    year = target_date.strftime("%Y")
    objects = []
    for location in get_target_locations():
        objects.append(
            (
                location,
                f"{SILVER_HOURLY_PREFIX}/location={location['id']}/{year}/{date_nodash}.parquet",
            )
        )
    return objects


# We verify the full Silver partition before touching PostgreSQL so publication never
# produces a partial analytical day from missing parquet inputs.
def verify_silver_ready(**kwargs) -> None:
    logical_date = kwargs["logical_date"]
    s3 = get_s3_client()
    expected_objects = _expected_prepared_objects(logical_date)
    missing = []

    for _location, prepared_key in expected_objects:
        if not object_exists(SILVER_BUCKET, prepared_key, client=s3):
            missing.append(prepared_key)

    if missing:
        raise RuntimeError(f"Silver inputs are not ready: {missing}")

    logger.info("Silver inputs verified: files=%d", len(expected_objects))


# We initialize the schema on publish runs so a fresh cluster can bootstrap the final
# analytical layer without any separate manual database setup step.
def init_analytics_schema(**kwargs) -> None:
    init_schema()
    logger.info("Analytics schema initialized")


# We load one Silver partition at a time into PostgreSQL so the final layer stays aligned
# with the same location/date boundaries used in Bronze and Silver.
def publish_previous_day(**kwargs) -> None:
    logical_date = kwargs["logical_date"]
    s3 = get_s3_client()
    total_rows = 0

    for location, prepared_key in _expected_prepared_objects(logical_date):
        try:
            raw = download_bytes(SILVER_BUCKET, prepared_key, client=s3)
        except Exception:
            logger.info("Prepared parquet not found, skip: %s", prepared_key)
            continue

        df = pq.read_table(io.BytesIO(raw)).to_pandas()
        if df.empty:
            continue

        total_rows += upsert_observation_dataframe(df, location)

    logger.info("Analytics publish complete: rows=%d", total_rows)


# We refresh the serve layer only after base observations are loaded, so downstream SQL
# queries always see summaries derived from the latest published partition.
def refresh_views(**kwargs) -> None:
    results = refresh_serve_layer()
    logger.info("Serve layer refreshed: %s", results)


# We end the run with lightweight quality checks to confirm that the published layer
# contains the expected analytical footprint for monitoring and demo purposes.
def run_quality_checks(**kwargs) -> None:
    metrics = fetch_analytics_quality_metrics()
    logger.info("Quality summary: %s", metrics)


with DAG(
    dag_id="climate_publish_analytics",
    default_args=default_args,
    description="Load prepared weather data into PostgreSQL analytics tables",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["climate", "publish"],
) as dag:
    task_verify_silver = PythonOperator(
        task_id="verify_silver_ready",
        python_callable=verify_silver_ready,
    )

    task_init = PythonOperator(
        task_id="init_analytics_schema",
        python_callable=init_analytics_schema,
    )

    task_publish = PythonOperator(
        task_id="publish_previous_day",
        python_callable=publish_previous_day,
    )

    task_refresh = PythonOperator(
        task_id="refresh_serve_layer",
        python_callable=refresh_views,
    )

    task_quality = PythonOperator(
        task_id="run_quality_checks",
        python_callable=run_quality_checks,
    )

    task_verify_silver >> task_init >> task_publish >> task_refresh >> task_quality
