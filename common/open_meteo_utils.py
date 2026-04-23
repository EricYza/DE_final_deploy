"""Open-Meteo fetch utilities."""

from __future__ import annotations

import time

import pandas as pd
import requests

from common.config import (
    API_MAX_RETRIES,
    API_RATE_LIMIT_DELAY,
    API_REQUEST_TIMEOUT,
    API_RETRY_BASE_DELAY,
    HOURLY_VARIABLES,
    OPEN_METEO_API_URL,
)


def api_is_available() -> bool:
    try:
        response = requests.get(
            OPEN_METEO_API_URL,
            params={
                "latitude": 35.9940,
                "longitude": -78.8986,
                "start_date": "2024-01-15",
                "end_date": "2024-01-15",
                "hourly": "temperature_2m",
                "timezone": "GMT",
            },
            timeout=API_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return True
    except Exception:
        return False


def _fetch_api(location: dict, start_date: str, end_date: str) -> dict:
    params = {
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ",".join(HOURLY_VARIABLES),
        "timezone": "GMT",
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
    }

    last_exc: Exception = RuntimeError("No attempts made")
    for attempt in range(API_MAX_RETRIES):
        try:
            response = requests.get(
                OPEN_METEO_API_URL,
                params=params,
                timeout=API_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            time.sleep(API_RATE_LIMIT_DELAY)
            return payload
        except Exception as exc:
            last_exc = exc
            if attempt < API_MAX_RETRIES - 1:
                delay = API_RETRY_BASE_DELAY * (2**attempt)
                time.sleep(delay)
    raise last_exc


def fetch_location_day(location: dict, business_date: str) -> dict:
    payload = _fetch_api(location, business_date, business_date)
    return {
        "location": location,
        "business_date": business_date,
        "source": "open-meteo",
        "response": payload,
    }


def payload_to_dataframe(raw_payload: dict) -> pd.DataFrame:
    location = raw_payload["location"]
    response = raw_payload["response"]
    hourly = response.get("hourly", {})

    df = pd.DataFrame(hourly)
    if df.empty:
        return pd.DataFrame(columns=["time", "location_id"])

    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    df["location_id"] = location["id"]
    df["api_elevation"] = response.get("elevation")
    return df
