"""PostgreSQL helpers for the climate pipeline."""

from __future__ import annotations

from typing import Any

import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

from common.config import POSTGRES_DB, POSTGRES_HOST, POSTGRES_PASSWORD, POSTGRES_PORT, POSTGRES_USER
from common.transformations import OBSERVATION_COLUMNS


def get_connection() -> Any:
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    )


_DDL_LOCATIONS = """
CREATE TABLE IF NOT EXISTS locations (
    location_id  VARCHAR PRIMARY KEY,
    name         VARCHAR NOT NULL,
    latitude     DOUBLE PRECISION NOT NULL,
    longitude    DOUBLE PRECISION NOT NULL,
    timezone     VARCHAR NOT NULL,
    country      VARCHAR,
    elevation    DOUBLE PRECISION
);
"""

_DDL_OBSERVATIONS = """
CREATE TABLE IF NOT EXISTS hourly_observations (
    location_id        VARCHAR NOT NULL REFERENCES locations(location_id),
    timestamp          TIMESTAMPTZ NOT NULL,
    temperature_c      DOUBLE PRECISION,
    temperature_f      DOUBLE PRECISION,
    humidity_pct       DOUBLE PRECISION,
    precipitation_mm   DOUBLE PRECISION,
    precipitation_in   DOUBLE PRECISION,
    rain_mm            DOUBLE PRECISION,
    rain_in            DOUBLE PRECISION,
    snowfall_cm        DOUBLE PRECISION,
    snowfall_in        DOUBLE PRECISION,
    wind_speed_kmh     DOUBLE PRECISION,
    wind_speed_mph     DOUBLE PRECISION,
    wind_speed_ms      DOUBLE PRECISION,
    wind_direction_deg DOUBLE PRECISION,
    wind_gusts_kmh     DOUBLE PRECISION,
    wind_gusts_mph     DOUBLE PRECISION,
    pressure_hpa       DOUBLE PRECISION,
    pressure_inhg      DOUBLE PRECISION,
    cloud_cover_pct    DOUBLE PRECISION,
    weather_code       INTEGER,
    et0_mm             DOUBLE PRECISION,
    et0_in             DOUBLE PRECISION,
    ingested_at        TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (location_id, timestamp)
);
"""

_DDL_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_obs_ts ON hourly_observations (timestamp);
CREATE INDEX IF NOT EXISTS idx_obs_loc ON hourly_observations (location_id);
CREATE INDEX IF NOT EXISTS idx_obs_temp ON hourly_observations (location_id, temperature_c);
CREATE INDEX IF NOT EXISTS idx_obs_precip ON hourly_observations (location_id, precipitation_mm);
"""

_DDL_MV_DAILY = """
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_daily_summary AS
SELECT
    o.location_id,
    l.name AS location_name,
    l.country,
    l.latitude,
    l.longitude,
    DATE_TRUNC('day', o.timestamp)::DATE AS date,
    AVG(o.temperature_c) AS avg_temp_c,
    MIN(o.temperature_c) AS min_temp_c,
    MAX(o.temperature_c) AS max_temp_c,
    AVG(o.temperature_f) AS avg_temp_f,
    STDDEV(o.temperature_c) AS stddev_temp_c,
    SUM(o.precipitation_mm) AS total_precip_mm,
    MAX(o.precipitation_mm) AS max_hourly_precip_mm,
    SUM(o.rain_mm) AS total_rain_mm,
    SUM(o.snowfall_cm) AS total_snowfall_cm,
    AVG(o.humidity_pct) AS avg_humidity_pct,
    MAX(o.wind_speed_kmh) AS max_wind_kmh,
    AVG(o.wind_speed_kmh) AS avg_wind_kmh,
    MAX(o.wind_gusts_kmh) AS max_gusts_kmh,
    AVG(o.pressure_hpa) AS avg_pressure_hpa,
    AVG(o.cloud_cover_pct) AS avg_cloud_cover_pct,
    COUNT(*) AS hourly_records,
    COUNT(o.temperature_c) AS valid_temp_records
FROM hourly_observations o
JOIN locations l USING (location_id)
GROUP BY o.location_id, l.name, l.country, l.latitude, l.longitude, DATE_TRUNC('day', o.timestamp)::DATE
WITH NO DATA;
"""

_DDL_MV_DAILY_IDX = """
CREATE UNIQUE INDEX IF NOT EXISTS uix_daily_summary ON mv_daily_summary (location_id, date);
"""

_DDL_MV_MONTHLY = """
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_monthly_summary AS
SELECT
    location_id,
    location_name,
    country,
    DATE_TRUNC('month', date)::DATE AS month,
    AVG(avg_temp_c) AS avg_temp_c,
    MIN(min_temp_c) AS min_temp_c,
    MAX(max_temp_c) AS max_temp_c,
    STDDEV(avg_temp_c) AS stddev_daily_temp_c,
    SUM(total_precip_mm) AS total_precip_mm,
    AVG(total_precip_mm) AS avg_daily_precip_mm,
    MAX(max_hourly_precip_mm) AS max_hourly_precip_mm,
    SUM(total_snowfall_cm) AS total_snowfall_cm,
    MAX(max_wind_kmh) AS max_wind_kmh,
    AVG(avg_wind_kmh) AS avg_wind_kmh,
    AVG(avg_humidity_pct) AS avg_humidity_pct,
    SUM(hourly_records) AS hourly_records,
    COUNT(*) AS days_with_data,
    SUM(CASE WHEN total_precip_mm >= 1.0 THEN 1 ELSE 0 END) AS rainy_days
FROM mv_daily_summary
GROUP BY location_id, location_name, country, DATE_TRUNC('month', date)::DATE
WITH NO DATA;
"""

_DDL_MV_MONTHLY_IDX = """
CREATE UNIQUE INDEX IF NOT EXISTS uix_monthly_summary ON mv_monthly_summary (location_id, month);
"""

_DDL_VW_EXTREME = """
CREATE OR REPLACE VIEW vw_extreme_events AS
SELECT
    o.location_id,
    l.name AS location_name,
    l.country,
    o.timestamp,
    o.temperature_c,
    o.precipitation_mm,
    o.wind_speed_kmh,
    o.wind_gusts_kmh,
    o.pressure_hpa,
    (o.temperature_c > 35.0) AS is_heat_extreme,
    (o.temperature_c < -10.0) AS is_cold_extreme,
    (o.precipitation_mm > 10.0) AS is_heavy_rain,
    (o.wind_speed_kmh > 89.0) AS is_strong_wind,
    (o.wind_gusts_kmh > 117.0) AS is_violent_gust
FROM hourly_observations o
JOIN locations l USING (location_id)
WHERE
    o.temperature_c > 35.0
    OR o.temperature_c < -10.0
    OR o.precipitation_mm > 10.0
    OR o.wind_speed_kmh > 89.0
    OR o.wind_gusts_kmh > 117.0;
"""

_DDL_VW_STATS = """
CREATE OR REPLACE VIEW vw_location_stats AS
SELECT
    o.location_id,
    l.name AS location_name,
    l.country,
    l.latitude,
    l.longitude,
    l.elevation,
    MIN(o.timestamp) AS first_record,
    MAX(o.timestamp) AS last_record,
    COUNT(*) AS total_hours,
    ROUND(AVG(o.temperature_c)::NUMERIC, 2) AS mean_temp_c,
    ROUND(STDDEV(o.temperature_c)::NUMERIC, 2) AS stddev_temp_c,
    ROUND(MIN(o.temperature_c)::NUMERIC, 2) AS record_low_c,
    ROUND(MAX(o.temperature_c)::NUMERIC, 2) AS record_high_c,
    ROUND(SUM(o.precipitation_mm)::NUMERIC, 1) AS total_precip_mm,
    ROUND(MAX(o.wind_gusts_kmh)::NUMERIC, 1) AS record_gust_kmh
FROM hourly_observations o
JOIN locations l USING (location_id)
GROUP BY o.location_id, l.name, l.country, l.latitude, l.longitude, l.elevation;
"""

_DDL_HEATWAVE = """
CREATE TABLE IF NOT EXISTS heatwave_periods (
    location_id VARCHAR NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    consecutive_days INTEGER NOT NULL,
    peak_temp_c DOUBLE PRECISION,
    mean_temp_c DOUBLE PRECISION,
    PRIMARY KEY (location_id, start_date)
);
"""

_DDL_HEAVY_RAIN = """
CREATE TABLE IF NOT EXISTS heavy_rain_events (
    location_id VARCHAR NOT NULL,
    date DATE NOT NULL,
    total_precip_mm DOUBLE PRECISION NOT NULL,
    max_hourly_mm DOUBLE PRECISION,
    PRIMARY KEY (location_id, date)
);
"""

_DDL_VOLATILITY = """
CREATE TABLE IF NOT EXISTS climate_volatility (
    location_id VARCHAR NOT NULL,
    location_name VARCHAR,
    month DATE NOT NULL,
    stddev_temp_c DOUBLE PRECISION,
    temp_range_c DOUBLE PRECISION,
    extreme_heat_days INTEGER,
    extreme_cold_days INTEGER,
    heavy_rain_days INTEGER,
    strong_wind_days INTEGER,
    PRIMARY KEY (location_id, month)
);
"""

_DDL_CITY_COMPARISON = """
CREATE TABLE IF NOT EXISTS city_comparison (
    location_id VARCHAR NOT NULL,
    location_name VARCHAR,
    country VARCHAR,
    year INTEGER NOT NULL,
    avg_temp_c DOUBLE PRECISION,
    total_precip_mm DOUBLE PRECISION,
    max_temp_c DOUBLE PRECISION,
    min_temp_c DOUBLE PRECISION,
    stddev_temp_c DOUBLE PRECISION,
    heatwave_days INTEGER,
    heavy_rain_days INTEGER,
    PRIMARY KEY (location_id, year)
);
"""

_SQL_HEATWAVE = """
INSERT INTO heatwave_periods
    (location_id, start_date, end_date, consecutive_days, peak_temp_c, mean_temp_c)
WITH hot_days AS (
    SELECT
        location_id,
        date,
        avg_temp_c,
        (date - ROW_NUMBER() OVER (PARTITION BY location_id ORDER BY date)::INT) AS grp
    FROM mv_daily_summary
    WHERE avg_temp_c > 35
),
periods AS (
    SELECT
        location_id,
        MIN(date) AS start_date,
        MAX(date) AS end_date,
        COUNT(*) AS consecutive_days,
        MAX(avg_temp_c) AS peak_temp_c,
        ROUND(AVG(avg_temp_c)::NUMERIC, 2) AS mean_temp_c
    FROM hot_days
    GROUP BY location_id, grp
    HAVING COUNT(*) >= 3
)
SELECT location_id, start_date, end_date, consecutive_days, peak_temp_c, mean_temp_c
FROM periods
ON CONFLICT (location_id, start_date) DO UPDATE SET
    end_date = EXCLUDED.end_date,
    consecutive_days = EXCLUDED.consecutive_days,
    peak_temp_c = EXCLUDED.peak_temp_c,
    mean_temp_c = EXCLUDED.mean_temp_c;
"""

_SQL_HEAVY_RAIN = """
INSERT INTO heavy_rain_events (location_id, date, total_precip_mm, max_hourly_mm)
SELECT location_id, date, total_precip_mm, max_hourly_precip_mm
FROM mv_daily_summary
WHERE total_precip_mm >= 50.0
ON CONFLICT (location_id, date) DO UPDATE SET
    total_precip_mm = EXCLUDED.total_precip_mm,
    max_hourly_mm = EXCLUDED.max_hourly_mm;
"""

_SQL_VOLATILITY = """
INSERT INTO climate_volatility
    (location_id, location_name, month, stddev_temp_c, temp_range_c,
     extreme_heat_days, extreme_cold_days, heavy_rain_days, strong_wind_days)
SELECT
    d.location_id,
    d.location_name,
    DATE_TRUNC('month', d.date)::DATE AS month,
    ROUND(STDDEV(d.avg_temp_c)::NUMERIC, 3) AS stddev_temp_c,
    ROUND((MAX(d.max_temp_c) - MIN(d.min_temp_c))::NUMERIC, 2) AS temp_range_c,
    COUNT(*) FILTER (WHERE d.max_temp_c > 35) AS extreme_heat_days,
    COUNT(*) FILTER (WHERE d.min_temp_c < -10) AS extreme_cold_days,
    COUNT(*) FILTER (WHERE d.total_precip_mm >= 50) AS heavy_rain_days,
    COUNT(*) FILTER (WHERE d.max_wind_kmh > 89) AS strong_wind_days
FROM mv_daily_summary d
GROUP BY d.location_id, d.location_name, DATE_TRUNC('month', d.date)::DATE
ON CONFLICT (location_id, month) DO UPDATE SET
    stddev_temp_c = EXCLUDED.stddev_temp_c,
    temp_range_c = EXCLUDED.temp_range_c,
    extreme_heat_days = EXCLUDED.extreme_heat_days,
    extreme_cold_days = EXCLUDED.extreme_cold_days,
    heavy_rain_days = EXCLUDED.heavy_rain_days,
    strong_wind_days = EXCLUDED.strong_wind_days;
"""

_SQL_CITY_COMPARISON = """
INSERT INTO city_comparison
    (location_id, location_name, country, year, avg_temp_c, total_precip_mm,
     max_temp_c, min_temp_c, stddev_temp_c, heatwave_days, heavy_rain_days)
SELECT
    d.location_id,
    d.location_name,
    d.country,
    EXTRACT(YEAR FROM d.date)::INT AS year,
    ROUND(AVG(d.avg_temp_c)::NUMERIC, 2) AS avg_temp_c,
    ROUND(SUM(d.total_precip_mm)::NUMERIC, 1) AS total_precip_mm,
    ROUND(MAX(d.max_temp_c)::NUMERIC, 2) AS max_temp_c,
    ROUND(MIN(d.min_temp_c)::NUMERIC, 2) AS min_temp_c,
    ROUND(STDDEV(d.avg_temp_c)::NUMERIC, 3) AS stddev_temp_c,
    COUNT(*) FILTER (WHERE d.max_temp_c > 35) AS heatwave_days,
    COUNT(*) FILTER (WHERE d.total_precip_mm >= 50) AS heavy_rain_days
FROM mv_daily_summary d
GROUP BY d.location_id, d.location_name, d.country, EXTRACT(YEAR FROM d.date)::INT
ON CONFLICT (location_id, year) DO UPDATE SET
    avg_temp_c = EXCLUDED.avg_temp_c,
    total_precip_mm = EXCLUDED.total_precip_mm,
    max_temp_c = EXCLUDED.max_temp_c,
    min_temp_c = EXCLUDED.min_temp_c,
    stddev_temp_c = EXCLUDED.stddev_temp_c,
    heatwave_days = EXCLUDED.heatwave_days,
    heavy_rain_days = EXCLUDED.heavy_rain_days;
"""


def init_schema() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for ddl in [
                _DDL_LOCATIONS,
                _DDL_OBSERVATIONS,
                _DDL_INDEXES,
                _DDL_MV_DAILY,
                _DDL_MV_DAILY_IDX,
                _DDL_MV_MONTHLY,
                _DDL_MV_MONTHLY_IDX,
                _DDL_VW_EXTREME,
                _DDL_VW_STATS,
                _DDL_HEATWAVE,
                _DDL_HEAVY_RAIN,
                _DDL_VOLATILITY,
                _DDL_CITY_COMPARISON,
            ]:
                cur.execute(ddl)
        conn.commit()
    finally:
        conn.close()


def upsert_location(location: dict) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO locations
                    (location_id, name, latitude, longitude, timezone, country, elevation)
                VALUES (%(id)s, %(name)s, %(latitude)s, %(longitude)s,
                        %(timezone)s, %(country)s, %(elevation)s)
                ON CONFLICT (location_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    latitude = EXCLUDED.latitude,
                    longitude = EXCLUDED.longitude,
                    timezone = EXCLUDED.timezone,
                    country = EXCLUDED.country,
                    elevation = EXCLUDED.elevation
                """,
                location,
            )
        conn.commit()
    finally:
        conn.close()


def upsert_observation_dataframe(df: pd.DataFrame, location: dict) -> int:
    if df.empty:
        return 0

    upsert_location(location)

    working = df.copy()
    working["timestamp"] = pd.to_datetime(working["timestamp"], utc=True, errors="coerce")
    cols = [column for column in OBSERVATION_COLUMNS if column in working.columns]
    min_ts = working["timestamp"].min()
    max_ts = working["timestamp"].max()

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM hourly_observations "
                "WHERE location_id = %s AND timestamp BETWEEN %s AND %s",
                [location["id"], min_ts, max_ts],
            )

            records = working[cols].where(pd.notnull(working[cols]), other=None).to_dict("records")
            rows = [tuple(record[column] for column in cols) for record in records]
            execute_values(
                cur,
                f"INSERT INTO hourly_observations ({', '.join(cols)}) VALUES %s",
                rows,
                page_size=500,
            )
        conn.commit()
        return len(working)
    finally:
        conn.close()


def refresh_serve_layer() -> dict:
    conn = get_connection()
    conn.autocommit = True
    results: dict = {}
    try:
        with conn.cursor() as cur:
            for mv in ("mv_daily_summary", "mv_monthly_summary"):
                cur.execute("SELECT relispopulated FROM pg_class WHERE relname = %s", (mv,))
                row = cur.fetchone()
                is_populated = row and row[0]
                if is_populated:
                    cur.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv}")
                else:
                    cur.execute(f"REFRESH MATERIALIZED VIEW {mv}")
                results[mv] = "refreshed"

        conn.autocommit = False
        with conn.cursor() as cur:
            for name, sql in [
                ("heatwave_periods", _SQL_HEATWAVE),
                ("heavy_rain_events", _SQL_HEAVY_RAIN),
                ("climate_volatility", _SQL_VOLATILITY),
                ("city_comparison", _SQL_CITY_COMPARISON),
            ]:
                cur.execute(sql)
                conn.commit()
                cur.execute(f"SELECT COUNT(*) FROM {name}")
                (count,) = cur.fetchone()
                results[name] = count
        return results
    finally:
        conn.close()


def fetch_gold_quality_metrics() -> dict:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total_rows,
                    COUNT(DISTINCT location_id) AS locations_loaded,
                    MAX(timestamp) AS latest_timestamp,
                    CASE
                        WHEN MAX(timestamp) IS NULL THEN NULL
                        ELSE CURRENT_DATE - MAX(timestamp AT TIME ZONE 'UTC')::DATE
                    END AS days_behind
                FROM hourly_observations
                """
            )
            return dict(cur.fetchone() or {})
    finally:
        conn.close()
