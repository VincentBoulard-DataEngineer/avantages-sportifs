"""
Ingestion script — reads HR and sports Excel files and loads raw data into PostgreSQL.
"""

import pandas as pd
import psycopg2
from psycopg2.extensions import connection as Connection
from psycopg2.extras import execute_values
import os
import sys
import logging
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
    "port":     5432,
    "dbname":   os.getenv("POSTGRES_DB"),
    "user":     os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD"),
}


def load_employees(filepath: str, conn: Connection) -> None:
    """Load raw HR data into raw.employees — UPSERT on employee_id.

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
        """, df.values.tolist())
    conn.commit()
    logger.info("raw.employees upserted: %s rows", len(df))


def load_sports(filepath: str, conn: Connection) -> None:
    """Load raw sports data into raw.sports — UPSERT on employee_id.

    Args:
        filepath: Path to the sports Excel file.
        conn: Active psycopg2 database connection.
    """
    df = pd.read_excel(filepath)
    df.columns = ["employee_id", "sport"]
    df["employee_id"] = df["employee_id"].astype(int)

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO raw.sports (employee_id, sport)
            VALUES %s
            ON CONFLICT (employee_id) DO UPDATE SET
                sport = EXCLUDED.sport
        """, df.values.tolist())
    conn.commit()
    logger.info("raw.sports upserted: %s rows", len(df))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        logger.error("Usage: python ingestion.py <rh_file> <sport_file>")
        sys.exit(1)

    rh_path    = sys.argv[1]
    sport_path = sys.argv[2]

    db_conn = psycopg2.connect(**DB_CONFIG)
    try:
        load_employees(rh_path, db_conn)
        load_sports(sport_path, db_conn)
    except Exception as e:
        logger.error("Ingestion failed: %s", e)
        raise
    finally:
        db_conn.close()
