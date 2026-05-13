"""
Parameters ingestion script — loads business parameters and sports reference data
from CSV files into config.parameters and config.sports tables.
"""

import os
import sys
import logging
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extensions import connection as Connection
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     int(os.getenv("POSTGRES_PORT") or "5432"),
    "dbname":   os.getenv("POSTGRES_DB"),
    "user":     os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD"),
}


def load_params(filepath: str, conn: Connection) -> None:
    """
    Load business parameters into config.parameters — UPSERT on key.

    Args:
        filepath: Path to the params CSV file.
        conn: Active psycopg2 database connection.
    """

    df = pd.read_csv(filepath)

    if "description" not in df.columns:
        df["description"] = None

    rows = [
        (
            str(row.key).strip(),
            str(row.value).strip(),
            None if pd.isna(row.description) else str(row.description).strip(),
        )
        for row in df.itertuples(index=False)
    ]

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO config.parameters (key, value, description)
            VALUES %s
            ON CONFLICT (key) DO UPDATE SET
                value       = EXCLUDED.value,
                description = EXCLUDED.description
        """, rows)
    conn.commit()
    logger.info("config.parameters upserted: %s rows", len(df))


def load_sports(filepath: str, conn: Connection) -> None:
    """
    Load sports reference data into config.sports — UPSERT on sport.

    Args:
        filepath: Path to the sports CSV file.
        conn: Active psycopg2 database connection.
    """

    df = pd.read_csv(filepath)

    rows = [
        (
            str(row.sport).strip(),
            None if pd.isna(row.max_speed_kmh) else float(row.max_speed_kmh),
            int(row.min_duration_min),
            bool(row.has_distance),
        )
        for row in df.itertuples(index=False)
    ]

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO config.sports (sport, max_speed_kmh, min_duration_min, has_distance)
            VALUES %s
            ON CONFLICT (sport) DO UPDATE SET
                max_speed_kmh    = EXCLUDED.max_speed_kmh,
                min_duration_min = EXCLUDED.min_duration_min,
                has_distance     = EXCLUDED.has_distance
        """, rows)
    conn.commit()
    logger.info("config.sports upserted: %s rows", len(df))


def load_commute_modes(filepath: str, conn: Connection) -> None:
    """
    Load commute modes reference data into config.commute_modes — UPSERT on mode.

    Args:
        filepath: Path to the commute_modes CSV file.
        conn: Active psycopg2 database connection.
    """

    df = pd.read_csv(filepath)

    rows = [
        (
            str(row.mode).strip(),
            None if pd.isna(row.threshold_m) else int(row.threshold_m),
            None if pd.isna(row.travel_mode) else str(row.travel_mode).strip(),
        )
        for row in df.itertuples(index=False)
    ]

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO config.commute_modes (mode, threshold_m, travel_mode)
            VALUES %s
            ON CONFLICT (mode) DO UPDATE SET
                threshold_m = EXCLUDED.threshold_m,
                travel_mode    = EXCLUDED.travel_mode
        """, rows)
    conn.commit()
    logger.info("config.commute_modes upserted: %s rows", len(df))


FILE_TYPE_MAP = {
    "params.csv":        load_params,
    "sports.csv":        load_sports,
    "commute_modes.csv": load_commute_modes,
}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.error("Usage: python params_ingestion.py <filepath>")
        logger.error("Supported files: %s", ", ".join(FILE_TYPE_MAP.keys()))
        sys.exit(1)

    path_to_file = sys.argv[1]
    filename = Path(path_to_file).name

    if filename not in FILE_TYPE_MAP:
        logger.error("Unknown file: %s. Supported files: %s", filename, ", ".join(FILE_TYPE_MAP.keys()))
        sys.exit(1)

    logger.info("Detected file type: %s", filename)

    db_conn = psycopg2.connect(**DB_CONFIG)
    try:
        FILE_TYPE_MAP[filename](path_to_file, db_conn)
    except Exception as e:
        logger.error("Params ingestion failed: %s", e)
        raise
    finally:
        db_conn.close()
