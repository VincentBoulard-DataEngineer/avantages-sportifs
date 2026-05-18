"""
Cleaning script — reads raw tables and writes normalized data to clean tables.
"""

import os
import sys
import logging
from pathlib import Path
from functools import partial

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


def get_current_batch_id(filename: str, conn: Connection) -> int:
    """
    Retrieve the batch_id of the latest completed batch for a given file.

    Args:
        filename: Name of the ingested file.
        conn: Active psycopg2 database connection.

    Returns:
        The batch_id of the latest completed batch.
    """

    with conn.cursor() as cur:
        cur.execute("""
            SELECT batch_id FROM config.batches
            WHERE filename = %s AND status = 'done'
            ORDER BY started_at DESC
            LIMIT 1
        """, (filename,))
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"No completed batch found for filename: {filename}")
    return row[0]


def read_table(table: str, batch_id: int, conn: Connection) -> pd.DataFrame:
    """
    Read rows from a raw table filtered by batch_id.

    Args:
        table: Fully qualified table name (e.g. 'raw.employees').
        batch_id: Current batch identifier.
        conn: Active psycopg2 database connection.

    Returns:
        DataFrame containing only rows from the current batch.
    """

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM {table} WHERE batch_id = %s",
            (batch_id,)
        )
        columns = [desc[0] for desc in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columns)


def load_valid_sports(conn: Connection) -> dict:
    """
    Load sport names from config.sports as a lowercase-to-exact mapping.

    Args:
        conn: Active psycopg2 database connection.

    Returns:
        Dict mapping lowercase sport name to exact name from config.sports.
    """

    with conn.cursor() as cur:
        cur.execute("SELECT sport FROM config.sports")
        return {row[0].lower(): row[0] for row in cur.fetchall()}


def normalize_commute_mode(value: str) -> str | None:
    """
    Normalize commute mode to lowercase stripped string.

    Args:
        value: Raw commute mode string.

    Returns:
        Normalized string or None if value is missing.
    """

    if pd.isna(value):
        return None
    return str(value).strip().lower()


def normalize_sport(value: str, valid_sports: dict) -> str | None:
    """
    Normalize sport name against config.sports reference data.

    Args:
        value: Raw sport name string.
        valid_sports: Dict mapping lowercase sport name to exact name from config.sports.

    Returns:
        Exact sport name from config.sports, or None if missing or unknown.
    """

    if value is None or pd.isna(value):
        return None
    return valid_sports.get(str(value).strip().lower())


def clean_employees(filename: str, conn: Connection) -> None:
    """
    Read raw.employees batch, normalize and write to clean.employees — UPSERT on employee_id.

    Args:
        filename: Name of the ingested file, used to retrieve batch_id.
        conn: Active psycopg2 database connection.
    """

    batch_id = get_current_batch_id(filename, conn)
    df = read_table("raw.employees", batch_id, conn)

    df["employee_id"]   = df["employee_id"].astype(int)
    df["last_name"]     = df["last_name"].str.strip().str.title()
    df["first_name"]    = df["first_name"].str.strip().str.title()
    df["birth_date"]    = pd.to_datetime(df["birth_date"], unit="us").dt.date
    df["bu"]            = df["bu"].str.strip()
    df["hire_date"]     = pd.to_datetime(df["hire_date"], unit="us").dt.date
    df["gross_salary"]  = df["gross_salary"].astype(float)
    df["contract_type"] = df["contract_type"].str.strip()
    df["vacation_days"] = df["vacation_days"].astype(int)
    df["home_address"]  = df["home_address"].str.strip()
    df["commute_mode"]  = df["commute_mode"].apply(normalize_commute_mode)

    rows = [
        (
            row.employee_id,
            row.last_name,
            row.first_name,
            row.birth_date,
            row.bu,
            row.hire_date,
            row.gross_salary,
            row.contract_type,
            row.vacation_days,
            row.home_address,
            row.commute_mode,
            None,
            None,
            batch_id,
        )
        for row in df.itertuples(index=False)
    ]

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO clean.employees (
                employee_id, last_name, first_name, birth_date, bu,
                hire_date, gross_salary, contract_type, vacation_days,
                home_address, commute_mode, commute_distance_m,
                commute_validated, batch_id
            ) VALUES %s
            ON CONFLICT (employee_id) DO UPDATE SET
                last_name     = EXCLUDED.last_name,
                first_name    = EXCLUDED.first_name,
                birth_date    = EXCLUDED.birth_date,
                bu            = EXCLUDED.bu,
                hire_date     = EXCLUDED.hire_date,
                gross_salary  = EXCLUDED.gross_salary,
                contract_type = EXCLUDED.contract_type,
                vacation_days = EXCLUDED.vacation_days,
                home_address  = EXCLUDED.home_address,
                commute_mode  = EXCLUDED.commute_mode,
                cleaned_at    = NOW(),
                batch_id      = EXCLUDED.batch_id
                -- commute_distance_m and commute_validated intentionally excluded:
                -- updated by google_maps.py
        """, rows)
    conn.commit()
    logger.info("clean.employees upserted: %s rows", len(df))


def clean_sports(filename: str, conn: Connection) -> None:
    """
    Read raw.sports batch, normalize and write to clean.sports — UPSERT on employee_id.

    Args:
        filename: Name of the ingested file, used to retrieve batch_id.
        conn: Active psycopg2 database connection.
    """

    batch_id = get_current_batch_id(filename, conn)
    df = read_table("raw.sports", batch_id, conn)
    valid_sports = load_valid_sports(conn)
    df["employee_id"] = df["employee_id"].astype(int)

    rows = [
        (
            int(row.employee_id),
            normalize_sport(row.sport, valid_sports),
            batch_id,
        )
        for row in df.itertuples(index=False)
    ]

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO clean.sports (employee_id, sport, batch_id)
            VALUES %s
            ON CONFLICT (employee_id) DO UPDATE SET
                sport      = EXCLUDED.sport,
                cleaned_at = NOW(),
                batch_id   = EXCLUDED.batch_id
        """, rows)
    conn.commit()
    logger.info("clean.sports upserted: %s rows", len(df))


def clean_activities(filename: str, conn: Connection, slack_notified: bool = False) -> None:
    """
    Read raw.activities batch, clean and write to clean.activities — UPSERT on activity_id.

    Args:
        filename: Name of the ingested file, used to retrieve batch_id.
        conn: Active psycopg2 database connection.
        slack_notified: False for activites.csv (notification pending),
                        True for activites_init.csv (no notification).
    """

    batch_id = get_current_batch_id(filename, conn)
    df = read_table("raw.activities", batch_id, conn)

    df["activity_id"] = df["activity_id"].astype(int)
    df["employee_id"] = df["employee_id"].astype(int)
    df["distance_m"]  = pd.to_numeric(df["distance_m"], errors="coerce").astype("Int64")

    rows = [
        (
            int(row.activity_id),
            int(row.employee_id),
            row.start_date,
            row.sport_type if row.sport_type and str(row.sport_type) != "nan" else None,
            None if pd.isna(row.distance_m) else int(row.distance_m),
            row.end_date,
            row.comment if row.comment and str(row.comment) != "nan" else None,
            slack_notified,
            batch_id,
        )
        for row in df.itertuples(index=False)
    ]

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO clean.activities (
                activity_id, employee_id, start_date, sport_type,
                distance_m, end_date, comment, slack_notified, batch_id
            ) VALUES %s
            ON CONFLICT (activity_id) DO UPDATE SET
                employee_id = EXCLUDED.employee_id,
                start_date  = EXCLUDED.start_date,
                sport_type  = EXCLUDED.sport_type,
                distance_m  = EXCLUDED.distance_m,
                end_date    = EXCLUDED.end_date,
                comment     = EXCLUDED.comment,
                cleaned_at  = NOW(),
                batch_id    = EXCLUDED.batch_id
                -- slack_notified intentionally excluded: preserved from initial INSERT
        """, rows)
    conn.commit()
    logger.info("clean.activities upserted: %s rows", len(df))


FILE_TYPE_MAP = {
    "donnees_rh.xlsx":        clean_employees,
    "donnees_sportives.xlsx": clean_sports,
    "activites.csv":          partial(clean_activities, slack_notified=False),
    "activites_init.csv":     partial(clean_activities, slack_notified=True),
}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.error("Usage: python cleaning.py <filename>")
        logger.error("Supported files: %s", ", ".join(FILE_TYPE_MAP.keys()))
        sys.exit(1)

    filename = Path(sys.argv[1]).name

    if filename not in FILE_TYPE_MAP:
        sys.exit(0)

    logger.info("Starting cleaning for: %s", filename)

    db_conn = psycopg2.connect(**DB_CONFIG)
    try:
        FILE_TYPE_MAP[filename](filename, db_conn)
    except Exception as e:
        logger.error("Cleaning failed: %s", e)
        raise
    finally:
        db_conn.close()
