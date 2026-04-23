"""Centralized configuration for the climate pipeline."""

from __future__ import annotations

import os


def _parse_csv_env(value: str | None, default: str) -> list[str]:
    raw = value if value is not None else default
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_int_env(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float_env(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


OPEN_METEO_API_URL = "https://archive-api.open-meteo.com/v1/archive"
API_MAX_RETRIES = _parse_int_env(os.getenv("API_MAX_RETRIES"), 5)
API_RETRY_BASE_DELAY = _parse_int_env(os.getenv("API_RETRY_BASE_DELAY"), 5)
API_REQUEST_TIMEOUT = _parse_int_env(os.getenv("API_REQUEST_TIMEOUT"), 60)
API_RATE_LIMIT_DELAY = _parse_float_env(os.getenv("API_RATE_LIMIT_DELAY"), 1.5)

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "climate-minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_USE_SSL = str(os.getenv("MINIO_USE_SSL", "false")).strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
    "on",
}

BRONZE_BUCKET = "bronze"
SILVER_BUCKET = "silver"
BRONZE_HOURLY_PREFIX = "climate_hourly_bronze"
SILVER_HOURLY_PREFIX = "climate_hourly_silver"

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "climate-postgres")
POSTGRES_PORT = _parse_int_env(os.getenv("POSTGRES_PORT"), 5432)
POSTGRES_DB = os.getenv("POSTGRES_DB", "climate_analytics")
POSTGRES_USER = os.getenv("POSTGRES_USER", "climate_user")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "climate_password")

LOCATION_CATALOG: dict[str, dict] = {
    "durham_nc": {
        "id": "durham_nc",
        "name": "Durham, NC",
        "latitude": 35.9940,
        "longitude": -78.8986,
        "timezone": "America/New_York",
        "country": "USA",
        "elevation": 123.0,
    },
    "london_uk": {
        "id": "london_uk",
        "name": "London",
        "latitude": 51.5074,
        "longitude": -0.1278,
        "timezone": "Europe/London",
        "country": "UK",
        "elevation": 11.0,
    },
    "tokyo_jp": {
        "id": "tokyo_jp",
        "name": "Tokyo",
        "latitude": 35.6762,
        "longitude": 139.6503,
        "timezone": "Asia/Tokyo",
        "country": "Japan",
        "elevation": 40.0,
    },
    "sydney_au": {
        "id": "sydney_au",
        "name": "Sydney",
        "latitude": -33.8688,
        "longitude": 151.2093,
        "timezone": "Australia/Sydney",
        "country": "Australia",
        "elevation": 39.0,
    },
    "cairo_eg": {
        "id": "cairo_eg",
        "name": "Cairo",
        "latitude": 30.0444,
        "longitude": 31.2357,
        "timezone": "Africa/Cairo",
        "country": "Egypt",
        "elevation": 23.0,
    },
}

TARGET_LOCATION_IDS = _parse_csv_env(
    os.getenv("TARGET_LOCATION_IDS"),
    ",".join(LOCATION_CATALOG.keys()),
)

HOURLY_VARIABLES: list[str] = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "rain",
    "snowfall",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "pressure_msl",
    "cloud_cover",
    "weather_code",
    "et0_fao_evapotranspiration",
]


def get_target_locations() -> list[dict]:
    return [
        LOCATION_CATALOG[location_id]
        for location_id in TARGET_LOCATION_IDS
        if location_id in LOCATION_CATALOG
    ]
