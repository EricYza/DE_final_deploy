"""
Author: Ziang Yang
Description: Airflow DAG for the Bronze-stage collection workflow in the climate data pipeline.
Fetches the previous business day's hourly weather data from Open-Meteo for each configured
location, stores raw JSON payloads in MinIO Bronze, verifies expected outputs, and triggers
the downstream preparation stage.

"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.utils.task_group import TaskGroup

from common.config import BRONZE_BUCKET, BRONZE_HOURLY_PREFIX, get_target_locations
from common.minio_utils import ensure_buckets, get_s3_client, object_exists, upload_json
from common.open_meteo_utils import api_is_available, fetch_location_day

logger = logging.getLogger(__name__)

default_args = {
    "owner": "climate-pipeline",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=2),
}


def check_api(**kwargs) -> None:
    if not api_is_available():
        raise RuntimeError("Open-Meteo API is not available")
    logger.info("Open-Meteo API is available")


def init_storage(**kwargs) -> None:
    ensure_buckets()
    logger.info("MinIO buckets verified")


# We derive expected Bronze object keys from the same logical date that drives the DAG,
# so collection and verification always agree on one business partition.
def _expected_raw_objects(logical_date):
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


# We collect one location per task and attach ingestion metadata before writing Bronze.
# This keeps each raw file traceable back to the Airflow run that produced it.
def fetch_daily_weather(location_id: str, **kwargs) -> None:
    logical_date = kwargs["logical_date"]
    target_date = (logical_date - timedelta(days=1)).date()
    business_date = target_date.strftime("%Y-%m-%d")
    date_nodash = target_date.strftime("%Y%m%d")
    year = target_date.strftime("%Y")

    dag_run = kwargs.get("dag_run")
    run_id = (
        str(dag_run.run_id)
        if dag_run and getattr(dag_run, "run_id", None)
        else f"airflow__{logical_date.isoformat()}"
    )

    location = next(
        location for location in get_target_locations() if location["id"] == location_id
    )
    payload = fetch_location_day(location, business_date)
    payload["_ingest_metadata"] = {
        "run_id": run_id,
        "dataset": "climate_hourly_weather",
        "location_id": location_id,
        "business_date": business_date,
        "fetched_at": datetime.utcnow().isoformat(),
    }

    raw_key = f"{BRONZE_HOURLY_PREFIX}/location={location_id}/{year}/{date_nodash}.json"
    upload_json(BRONZE_BUCKET, raw_key, payload, client=get_s3_client())
    logger.info("Weather collection complete for %s: %s", location_id, raw_key)


# We verify the full Bronze partition before triggering the next DAG so downstream work
# never starts from a partially collected date.
def verify_bronze_ready(**kwargs) -> None:
    logical_date = kwargs["logical_date"]
    s3 = get_s3_client()
    expected_objects = _expected_raw_objects(logical_date)
    missing = []

    for _location, raw_key in expected_objects:
        if not object_exists(BRONZE_BUCKET, raw_key, client=s3):
            missing.append(raw_key)

    if missing:
        raise RuntimeError(f"Bronze verification failed; missing files: {missing}")

    logger.info("Bronze verification complete: files=%d", len(expected_objects))


with DAG(
    dag_id="climate_collect_weather",
    default_args=default_args,
    description="Fetch previous-day Open-Meteo data into raw JSON storage",
    schedule="30 5 * * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_tasks=3,
    tags=["climate", "collect"],
) as dag:
    task_check_api = PythonOperator(
        task_id="check_api_available",
        python_callable=check_api,
    )

    task_init_storage = PythonOperator(
        task_id="init_storage",
        python_callable=init_storage,
    )

    with TaskGroup("fetch_daily_weather") as tg_fetch:
        for location in get_target_locations():
            PythonOperator(
                task_id=f"fetch_{location['id']}",
                python_callable=fetch_daily_weather,
                op_kwargs={"location_id": location["id"]},
            )

    task_verify_bronze = PythonOperator(
        task_id="verify_bronze_ready",
        python_callable=verify_bronze_ready,
    )

    task_trigger_prepare = TriggerDagRunOperator(
        task_id="trigger_prepare_stage",
        trigger_dag_id="climate_prepare_weather",
        logical_date="{{ ts }}",
        trigger_run_id="prepare__{{ ds_nodash }}",
        reset_dag_run=True,
    )

    task_check_api >> task_init_storage >> tg_fetch >> task_verify_bronze >> task_trigger_prepare
