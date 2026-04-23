"""Weather preparation DAG for the climate pipeline."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

from common.config import (
    BRONZE_BUCKET,
    BRONZE_HOURLY_PREFIX,
    SILVER_BUCKET,
    SILVER_HOURLY_PREFIX,
    get_target_locations,
)
from common.minio_utils import download_json, get_s3_client, object_exists, upload_parquet
from common.open_meteo_utils import payload_to_dataframe
from common.transformations import (
    dataframe_to_parquet_bytes,
    transform_weather_dataframe,
    validate_raw_dataframe,
)

logger = logging.getLogger(__name__)

default_args = {
    "owner": "climate-pipeline",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=3),
}


def _expected_source_objects(logical_date):
    target_date = (logical_date - timedelta(days=1)).date()
    date_nodash = target_date.strftime("%Y%m%d")
    year = target_date.strftime("%Y")
    objects = []
    for location in get_target_locations():
        objects.append(
            (
                location,
                f"{BRONZE_HOURLY_PREFIX}/location={location['id']}/{year}/{date_nodash}.json",
            )
        )
    return objects


def _prepared_key(source_key: str) -> str:
    return source_key.replace(BRONZE_HOURLY_PREFIX, SILVER_HOURLY_PREFIX, 1).replace(
        ".json",
        ".parquet",
    )


def verify_bronze_ready(**kwargs) -> None:
    logical_date = kwargs["logical_date"]
    s3 = get_s3_client()
    expected_objects = _expected_source_objects(logical_date)
    missing = []

    for _location, source_key in expected_objects:
        if not object_exists(BRONZE_BUCKET, source_key, client=s3):
            missing.append(source_key)

    if missing:
        raise RuntimeError(f"Bronze inputs are not ready: {missing}")

    logger.info("Bronze inputs verified: files=%d", len(expected_objects))


def prepare_previous_day(**kwargs) -> None:
    logical_date = kwargs["logical_date"]
    s3 = get_s3_client()
    prepared_files = 0
    rows_total = 0

    for location, source_key in _expected_source_objects(logical_date):
        prepared_key = _prepared_key(source_key)

        if object_exists(SILVER_BUCKET, prepared_key, client=s3):
            logger.info("Prepared parquet already exists, skip: %s", prepared_key)
            continue

        try:
            payload = download_json(BRONZE_BUCKET, source_key, client=s3)
        except Exception:
            logger.info("Source JSON not found, skip: %s", source_key)
            continue

        raw_df = payload_to_dataframe(payload)
        if raw_df.empty:
            logger.info("No rows in source payload: %s", source_key)
            continue

        validated = validate_raw_dataframe(raw_df, location["id"])
        transformed = transform_weather_dataframe(validated)
        if transformed.empty:
            continue

        upload_parquet(
            SILVER_BUCKET,
            prepared_key,
            dataframe_to_parquet_bytes(transformed),
            client=s3,
        )

        prepared_files += 1
        rows_total += len(transformed)

    logger.info(
        "Weather preparation complete: files=%d rows=%d",
        prepared_files,
        rows_total,
    )


def verify_silver_ready(**kwargs) -> None:
    logical_date = kwargs["logical_date"]
    s3 = get_s3_client()
    missing = []

    for _location, source_key in _expected_source_objects(logical_date):
        prepared_key = _prepared_key(source_key)
        if not object_exists(SILVER_BUCKET, prepared_key, client=s3):
            missing.append(prepared_key)

    if missing:
        raise RuntimeError(f"Silver verification failed; missing files: {missing}")

    logger.info("Silver verification complete: files=%d", len(_expected_source_objects(logical_date)))


with DAG(
    dag_id="climate_prepare_weather",
    default_args=default_args,
    description="Validate and convert raw weather JSON into Parquet files",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["climate", "prepare"],
) as dag:
    task_verify_bronze = PythonOperator(
        task_id="verify_bronze_ready",
        python_callable=verify_bronze_ready,
    )

    task_prepare = PythonOperator(
        task_id="prepare_previous_day",
        python_callable=prepare_previous_day,
    )

    task_verify_silver = PythonOperator(
        task_id="verify_silver_ready",
        python_callable=verify_silver_ready,
    )

    task_trigger_publish = TriggerDagRunOperator(
        task_id="trigger_publish_stage",
        trigger_dag_id="climate_publish_analytics",
        logical_date="{{ ts }}",
        trigger_run_id="publish__{{ ds_nodash }}",
        reset_dag_run=True,
    )

    task_verify_bronze >> task_prepare >> task_verify_silver >> task_trigger_publish
