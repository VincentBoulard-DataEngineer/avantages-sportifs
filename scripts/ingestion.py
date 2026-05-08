"""
Ingestion script — detects file type from filename and loads raw data into PostgreSQL.
"""

import os
import sys
import logging
from functools import partial
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extensions import connection as Connection
from psycopg2.extras import execute_values
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Logger configuration
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


def load_employees(filepath: str, conn: Connection) -> None:
    """
    Load raw HR data into raw.employees — UPSERT on employee_id.

    Args:
        filepath: Path to the HR Excel file.
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
        )
        for row in df.itertuples(index=False)
    ]

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO raw.employees (
                employee_id, last_name, first_name, birth_date, bu,
                hire_date, gross_salary, contract_type, vacation_days,
                home_address, commute_mode
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
                commute_mode    = EXCLUDED.commute_mode
        """, rows)
    conn.commit()
    logger.info("raw.employees upserted: %s rows", len(df))


def load_sports(filepath: str, conn: Connection) -> None:
    """Load raw sports declarations into raw.sports — UPSERT on employee_id.

    Args:
        filepath: Path to the sports Excel file.
        conn: Active psycopg2 database connection.
    """

    df = pd.read_excel(filepath)
    df.columns = ["employee_id", "sport"]
    df["employee_id"] = df["employee_id"].astype(int)

    rows = [
        (
            int(row.employee_id),
            None if pd.isna(row.sport) else row.sport,
        )
        for row in df.itertuples(index=False)
    ]

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO raw.sports (employee_id, sport)
            VALUES %s
            ON CONFLICT (employee_id) DO UPDATE SET
                sport = EXCLUDED.sport
        """, rows)
    conn.commit()
    logger.info("raw.sports upserted: %s rows", len(df))


def load_activities(filepath: str, conn: Connection, slack_notified: bool) -> None:
    """
    Load raw activities into raw.activities — UPSERT on activity_id.

    Args:
        filepath: Path to the activities CSV file.
        conn: Active psycopg2 database connection.
        slack_notified: False for activites.csv (notification pending),
                        True for activites_init.csv (no notification).
    """
    logger.info("load_activities — not yet implemented (slack_notified=%s)", slack_notified)


FILE_TYPE_MAP = {
    "donnees_rh.xlsx":        load_employees,
    "donnees_sportives.xlsx": load_sports,
    "activites.csv":          partial(load_activities, slack_notified=False),
    "activites_init.csv":     partial(load_activities, slack_notified=True),
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
    try:
        FILE_TYPE_MAP[filename](path_to_file, db_conn)
    except Exception as e:
        logger.error("Ingestion failed: %s", e)
        raise
    finally:
        db_conn.close()
