"""
Ingestion script — detects file type from filename and loads raw data into PostgreSQL.
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


def create_batch(filename: str, conn: Connection) -> int:
    """
    Create a new batch entry in config.batches and return its batch_id.

    Args:
        filename: Name of the file being ingested.
        conn: Active psycopg2 database connection.

    Returns:
        The batch_id of the created batch.
    """

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO config.batches (filename) VALUES (%s) RETURNING batch_id",
            (filename,)
        )
        batch_id = cur.fetchone()[0]
    conn.commit()
    logger.info("Batch created: batch_id=%s", batch_id)
    return batch_id


def close_batch(batch_id: int, status: str, conn: Connection) -> None:
    """
    Update batch status to 'done' or 'failed'.

    Args:
        batch_id: The batch to close.
        status: Final status — 'done' or 'failed'.
        conn: Active psycopg2 database connection.
    """

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE config.batches SET status = %s WHERE batch_id = %s",
            (status, batch_id)
        )
    conn.commit()
    logger.info("Batch closed: batch_id=%s status=%s", batch_id, status)


def load_employees(filepath: str, batch_id: int, conn: Connection) -> None:
    """
    Load raw HR data into raw.employees — UPSERT on employee_id.

    Args:
        filepath: Path to the HR Excel file.
        batch_id: Current batch identifier.
        conn: Active psycopg2 database connection.
    """

    df = pd.read_excel(filepath)
    df.columns = [
        "employee_id", "last_name", "first_name", "birth_date", "bu",
        "hire_date", "gross_salary", "contract_type", "vacation_days",
        "home_address", "commute_mode"
    ]
    df["employee_id"] = df["employee_id"].astype(int)
    df["birth_date"]  = pd.to_numeric(df["birth_date"], errors="coerce")
    df["hire_date"]   = pd.to_numeric(df["hire_date"], errors="coerce")

    def val(v):
        return None if pd.isna(v) else v

    rows = [
        (
            int(row.employee_id),
            val(row.last_name),
            val(row.first_name),
            val(row.birth_date),
            val(row.bu),
            val(row.hire_date),
            val(row.gross_salary),
            val(row.contract_type),
            val(row.vacation_days),
            val(row.home_address),
            val(row.commute_mode),
            batch_id,
        )
        for row in df.itertuples(index=False)
    ]

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO raw.employees (
                employee_id, last_name, first_name, birth_date, bu,
                hire_date, gross_salary, contract_type, vacation_days,
                home_address, commute_mode, batch_id
            ) VALUES %s
            ON CONFLICT (employee_id) DO UPDATE SET
                last_name       = EXCLUDED.last_name,
                first_name      = EXCLUDED.first_name,
                birth_date      = EXCLUDED.birth_date,
                bu              = EXCLUDED.bu,
                hire_date       = EXCLUDED.hire_date,
                gross_salary    = EXCLUDED.gross_salary,
                contract_type   = EXCLUDED.contract_type,
                vacation_days   = EXCLUDED.vacation_days,
                home_address    = EXCLUDED.home_address,
                commute_mode    = EXCLUDED.commute_mode,
                ingested_at     = NOW(),
                batch_id        = EXCLUDED.batch_id
        """, rows)
    conn.commit()
    logger.info("raw.employees upserted: %s rows", len(df))


def load_sports(filepath: str, batch_id: int, conn: Connection) -> None:
    """
    Load raw sports declarations into raw.sports — UPSERT on employee_id.

    Args:
        filepath: Path to the sports Excel file.
        batch_id: Current batch identifier.
        conn: Active psycopg2 database connection.
    """

    df = pd.read_excel(filepath)
    df.columns = ["employee_id", "sport"]
    df["employee_id"] = df["employee_id"].astype(int)

    rows = [
        (
            int(row.employee_id),
            None if pd.isna(row.sport) else row.sport,
            batch_id,
        )
        for row in df.itertuples(index=False)
    ]

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO raw.sports (employee_id, sport, batch_id)
            VALUES %s
            ON CONFLICT (employee_id) DO UPDATE SET
                sport       = EXCLUDED.sport,
                ingested_at = NOW(),
                batch_id    = EXCLUDED.batch_id
        """, rows)
    conn.commit()
    logger.info("raw.sports upserted: %s rows", len(df))


def load_activities(filepath: str, batch_id: int, conn: Connection) -> None:
    """
    Load raw activity data into raw.activities — UPSERT on activity_id.

    Args:
        filepath: Path to the activities CSV file.
        batch_id: Current batch identifier.
        conn: Active psycopg2 database connection.
    """

    df = pd.read_csv(filepath)

    df["activity_id"]  = df["activity_id"].astype(int)
    df["employee_id"]  = df["employee_id"].astype(int)
    df["start_date"]   = pd.to_datetime(df["start_date"])
    df["end_date"]     = pd.to_datetime(df["end_date"])
    df["distance_m"]   = pd.to_numeric(df["distance_m"], errors="coerce").astype("Int64")

    rows = [
        (
            int(row.activity_id),
            int(row.employee_id),
            row.start_date.to_pydatetime(),
            row.sport_type if row.sport_type and str(row.sport_type) != 'nan' else None,
            None if pd.isna(row.distance_m) else int(row.distance_m),
            row.end_date.to_pydatetime(),
            row.comment if row.comment and str(row.comment) != "nan" else None,
            batch_id,
        )
        for row in df.itertuples(index=False)
    ]

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO raw.activities (
                activity_id, employee_id, start_date, sport_type,
                distance_m, end_date, comment, batch_id
            ) VALUES %s
            ON CONFLICT (activity_id) DO UPDATE SET
                employee_id = EXCLUDED.employee_id,
                start_date  = EXCLUDED.start_date,
                sport_type  = EXCLUDED.sport_type,
                distance_m  = EXCLUDED.distance_m,
                end_date    = EXCLUDED.end_date,
                comment     = EXCLUDED.comment,
                ingested_at = NOW(),
                batch_id    = EXCLUDED.batch_id
        """, rows)
    conn.commit()
    logger.info("raw.activities upserted: %s rows", len(df))


FILE_TYPE_MAP = {
    "donnees_rh.xlsx":        load_employees,
    "donnees_sportives.xlsx": load_sports,
    "activites.csv":          load_activities,
    "activites_init.csv":     load_activities
}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.error("Usage: python ingestion.py <filepath>")
        logger.error("Supported files: %s", ", ".join(FILE_TYPE_MAP.keys()))
        sys.exit(1)

    path_to_file = sys.argv[1]
    filename = Path(path_to_file).name

    if filename not in FILE_TYPE_MAP:
        logger.error("Unknown file: %s. Supported files: %s", filename, ", ".join(FILE_TYPE_MAP.keys()))
        sys.exit(1)

    logger.info("Detected file type: %s", filename)

    db_conn = psycopg2.connect(**DB_CONFIG)
    batch_id = create_batch(filename, db_conn)
    try:
        FILE_TYPE_MAP[filename](path_to_file, batch_id, db_conn)
        close_batch(batch_id, "done", db_conn)
    except Exception as e:
        logger.error("Ingestion failed: %s", e)
        close_batch(batch_id, "failed", db_conn)
        raise
    finally:
        db_conn.close()
