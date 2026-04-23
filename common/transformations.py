"""Validation and transform logic for climate observations."""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

REQUIRED_COLUMNS: list[str] = ["time", "location_id"]

EXPECTED_COLUMNS: list[str] = [
    "time",
    "location_id",
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

RANGE_CHECKS: dict[str, tuple[float, float]] = {
    "temperature_2m": (-90.0, 60.0),
    "relative_humidity_2m": (0.0, 100.0),
    "precipitation": (0.0, 500.0),
    "wind_speed_10m": (0.0, 400.0),
    "pressure_msl": (870.0, 1085.0),
}

OBSERVATION_COLUMNS: list[str] = [
    "location_id",
    "timestamp",
    "temperature_c",
    "temperature_f",
    "humidity_pct",
    "precipitation_mm",
    "precipitation_in",
    "rain_mm",
    "rain_in",
    "snowfall_cm",
    "snowfall_in",
    "wind_speed_kmh",
    "wind_speed_mph",
    "wind_speed_ms",
    "wind_direction_deg",
    "wind_gusts_kmh",
    "wind_gusts_mph",
    "pressure_hpa",
    "pressure_inhg",
    "cloud_cover_pct",
    "weather_code",
    "et0_mm",
    "et0_in",
]


def validate_raw_dataframe(df: pd.DataFrame, location_id: str) -> pd.DataFrame:
    missing_required = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing_required:
        raise ValueError(f"{location_id}: missing required columns: {missing_required}")

    out = df.copy()

    for column in EXPECTED_COLUMNS:
        if column not in out.columns:
            out[column] = float("nan")

    out = out.drop_duplicates(subset=["location_id", "time"])
    out = out.sort_values("time").reset_index(drop=True)

    if "temperature_2m" in out.columns:
        null_ratio = out["temperature_2m"].isna().mean()
        if null_ratio > 0.5:
            raise ValueError(
                f"{location_id}: temperature_2m has {null_ratio * 100:.1f}% nulls"
            )

    for column, (lower, upper) in RANGE_CHECKS.items():
        if column not in out.columns:
            continue
        bad = out[column].notna() & ((out[column] < lower) | (out[column] > upper))
        if bad.any():
            print(
                f"[validate] {location_id}: {column} has {int(bad.sum())} out-of-range values"
            )

    return out


def _add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    if "temperature_2m" in out.columns:
        out["temperature_c"] = out["temperature_2m"]
        out["temperature_f"] = out["temperature_2m"] * 9.0 / 5.0 + 32.0

    if "relative_humidity_2m" in out.columns:
        out["humidity_pct"] = out["relative_humidity_2m"]

    if "precipitation" in out.columns:
        out["precipitation_mm"] = out["precipitation"]
        out["precipitation_in"] = out["precipitation"] / 25.4

    if "rain" in out.columns:
        out["rain_mm"] = out["rain"]
        out["rain_in"] = out["rain"] / 25.4

    if "snowfall" in out.columns:
        out["snowfall_cm"] = out["snowfall"]
        out["snowfall_in"] = out["snowfall"] / 2.54

    if "wind_speed_10m" in out.columns:
        out["wind_speed_kmh"] = out["wind_speed_10m"]
        out["wind_speed_mph"] = out["wind_speed_10m"] * 0.621371
        out["wind_speed_ms"] = out["wind_speed_10m"] / 3.6

    if "wind_direction_10m" in out.columns:
        out["wind_direction_deg"] = out["wind_direction_10m"]

    if "wind_gusts_10m" in out.columns:
        out["wind_gusts_kmh"] = out["wind_gusts_10m"]
        out["wind_gusts_mph"] = out["wind_gusts_10m"] * 0.621371

    if "pressure_msl" in out.columns:
        out["pressure_hpa"] = out["pressure_msl"]
        out["pressure_inhg"] = out["pressure_msl"] * 0.02953

    if "cloud_cover" in out.columns:
        out["cloud_cover_pct"] = out["cloud_cover"]

    if "weather_code" in out.columns:
        out["weather_code"] = out["weather_code"].astype("Int64")

    if "et0_fao_evapotranspiration" in out.columns:
        out["et0_mm"] = out["et0_fao_evapotranspiration"]
        out["et0_in"] = out["et0_fao_evapotranspiration"] / 25.4

    return out


def transform_weather_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    out = _add_derived_columns(df)
    out = out.rename(columns={"time": "timestamp"})
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    keep = [column for column in OBSERVATION_COLUMNS if column in out.columns]
    return out[keep].reset_index(drop=True)


def dataframe_to_parquet_bytes(df: pd.DataFrame) -> bytes:
    table = pa.Table.from_pandas(df, preserve_index=False)
    buffer = io.BytesIO()
    pq.write_table(table, buffer)
    return buffer.getvalue()


def save_transformed_parquet(df: pd.DataFrame, output_path: str) -> str:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    return output_path
