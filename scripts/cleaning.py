"""
Cleaning script — reads raw tables and writes normalized data to clean tables.
"""

import os
import sys
import logging
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extensions import connection as Connection
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     5432,
    "dbname":   os.getenv("POSTGRES_DB"),
    "user":     os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD"),
}

SPORT_CORRECTIONS = {
    "runing": "Running"
}


def read_table(table: str, conn: Connection) -> pd.DataFrame:
    """
    Read a PostgreSQL table into a DataFrame using psycopg2 cursor.

    Args:
        table: Fully qualified table name (e.g. 'raw.employees').
        conn: Active psycopg2 database connection.

    Returns:
        DataFrame with table contents.
    """

    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {table}")
        columns = [desc[0] for desc in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columns)


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


def normalize_sport(value: str) -> str | None:
    """
    Normalize sport name using known corrections dictionary.

    Args:
        value: Raw sport name string.

    Returns:
        Normalized sport name or None if value is missing.
    """

    if value is None:
        return None
    normalized = str(value).strip().lower()
    return SPORT_CORRECTIONS.get(normalized, str(value).strip().title())


def clean_employees(conn: Connection) -> None:
    """
    Read raw.employees, normalize and write to clean.employees — UPSERT on employee_id.

    Args:
        conn: Active psycopg2 database connection.
    """

    df = read_table("raw.employees", conn)

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
        )
        for row in df.itertuples(index=False)
    ]

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO clean.employees (
                employee_id, last_name, first_name, birth_date, bu,
                hire_date, gross_salary, contract_type, vacation_days,
                home_address, commute_mode, commute_distance_m, commute_validated
            ) VALUES %s
            ON CONFLICT (employee_id) DO UPDATE SET
                last_name           = EXCLUDED.last_name,
                first_name          = EXCLUDED.first_name,
                birth_date          = EXCLUDED.birth_date,
                bu                  = EXCLUDED.bu,
                hire_date           = EXCLUDED.hire_date,
                gross_salary        = EXCLUDED.gross_salary,
                contract_type       = EXCLUDED.contract_type,
                vacation_days       = EXCLUDED.vacation_days,
                home_address        = EXCLUDED.home_address,
                commute_mode        = EXCLUDED.commute_mode
        """, rows)
    conn.commit()
    logger.info("clean.employees upserted: %s rows", len(df))


def clean_sports(conn: Connection) -> None:
    """
    Read raw.sports, normalize and write to clean.sports — UPSERT on employee_id.

    Args:
        conn: Active psycopg2 database connection.
    """

    df = read_table("raw.sports", conn)
    df["employee_id"] = df["employee_id"].astype(int)

    rows = [
        (int(row.employee_id), None if pd.isna(row.sport) else normalize_sport(row.sport))
        for row in df.itertuples(index=False)
    ]

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO clean.sports (employee_id, sport)
            VALUES %s
            ON CONFLICT (employee_id) DO UPDATE SET
                sport = EXCLUDED.sport
        """, rows)
    conn.commit()
    logger.info("clean.sports upserted: %s rows", len(df))


def clean_activities(conn: Connection) -> None:
    """
    Clean activities — not yet implemented.
    """

    logger.info("clean_activities — not yet implemented")


FILE_TYPE_MAP = {
    "donnees_rh.xlsx":        clean_employees,
    "donnees_sportives.xlsx": clean_sports,
    "activites.csv":          clean_activities,
    "activites_init.csv":     clean_activities,
}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.error("Usage: python cleaning.py <filename>")
        logger.error("Supported files: %s", ", ".join(FILE_TYPE_MAP.keys()))
        sys.exit(1)

    filename = Path(sys.argv[1]).name

    if filename not in FILE_TYPE_MAP:
        logger.error("Unknown file: %s. Supported files: %s", filename, ", ".join(FILE_TYPE_MAP.keys()))
        sys.exit(1)

    logger.info("Starting cleaning for: %s", filename)

    db_conn = psycopg2.connect(**DB_CONFIG)
    try:
        FILE_TYPE_MAP[filename](db_conn)
    except Exception as e:
        logger.error("Cleaning failed: %s", e)
        raise
    finally:
        db_conn.close()
