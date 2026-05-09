"""
Raw quality tests — runs Great Expectations validation checks on the raw table
matching the ingested file and writes anomalies to quality_report.anomalies (stage='raw').
"""

import os
import sys
import logging
from pathlib import Path

import pandas as pd
import great_expectations as ge
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
    "port":     int(os.getenv("POSTGRES_PORT") or "5432"),
    "dbname":   os.getenv("POSTGRES_DB"),
    "user":     os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD"),
}


def read_table(table: str, conn: Connection) -> pd.DataFrame:
    """
    Read a PostgreSQL table into a DataFrame.

    Args:
        table: Fully qualified table name (e.g. 'raw.employees').
        conn: Active psycopg2 database connection.

    Returns:
        DataFrame containing all rows and columns from the table,
        with NaN values replaced by None.
    """

    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {table}")
        columns = [desc[0] for desc in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=columns)
        return df.where(pd.notnull(df), None)


def run_expectation(
    result: dict,
    df: pd.DataFrame,
    table_name: str,
    test_name: str,
    id_col: str,
) -> list[dict]:
    """
    Extract anomalies from a single GE expectation result.

    Args:
        result: GE expectation result dict from to_json_dict().
        df: Source DataFrame to look up the identifier of each failing row.
        table_name: Fully qualified table name (e.g. 'raw.employees').
        test_name: Short snake_case test identifier.
        id_col: Row identifier column, either 'employee_id' or 'activity_id'.

    Returns:
        List of anomaly dicts. Empty if the expectation passed.
    """
    anomalies = []

    if not result["success"]:
        for idx in result["result"].get("unexpected_index_list", []):
            row_id = df.iloc[idx][id_col]
            anomalies.append({
                "table_name": table_name,
                "test_name":  test_name,
                "detail":     f"{id_col}={row_id}",
                id_col:       int(row_id) if row_id is not None else None,
            })
    return anomalies


def write_anomalies(anomalies: list[dict], conn: Connection) -> None:
    """
    Write detected anomalies to quality_report.anomalies with stage='raw'.

    Args:
        anomalies: List of anomaly dicts from run_expectation().
        conn: Active psycopg2 database connection.
    """

    if not anomalies:
        logger.info("No raw anomalies detected")
        return
    rows = [
        (
            "raw",
            a["table_name"],
            a["test_name"],
            a.get("detail"),
            a.get("employee_id"),
            a.get("activity_id"),
        )
        for a in anomalies
    ]
    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO quality_report.anomalies
                (stage, table_name, test_name, detail, employee_id, activity_id)
            VALUES %s
        """, rows)
    conn.commit()
    logger.info("Raw anomalies written: %s", len(rows))


def test_raw_employees(conn: Connection) -> list[dict]:
    """
    Run GE quality checks on raw.employees.

    Checks: mandatory fields not null (employee_id, last_name, first_name,
    home_address, commute_mode, birth_date, hire_date), gross_salary > 0,
    employee_id unique.

    Note:
        birth_date and hire_date are Excel serial floats in raw — date
        coherence is validated in the clean quality pass.

    Args:
        conn: Active psycopg2 database connection.

    Returns:
        List of anomaly dicts. Empty if all checks pass.
    """

    df = read_table("raw.employees", conn)
    ge_df = ge.from_pandas(df)
    ge_df.set_default_expectation_argument("result_format", "COMPLETE")
    anomalies = []
    table = "raw.employees"

    for col in ("employee_id", "last_name", "first_name", "home_address",
                "commute_mode", "birth_date", "hire_date"):
        anomalies += run_expectation(
            ge_df.expect_column_values_to_not_be_null(col).to_json_dict(),
            df, table, f"{col}_not_null", "employee_id"
        )

    anomalies += run_expectation(
        ge_df.expect_column_values_to_be_between(
            "gross_salary", min_value=0, strict_min=True
        ).to_json_dict(),
        df, table, "gross_salary_positive", "employee_id"
    )

    anomalies += run_expectation(
        ge_df.expect_column_values_to_be_unique("employee_id").to_json_dict(),
        df, table, "employee_id_unique", "employee_id"
    )

    logger.info("test_raw_employees — %s anomalies", len(anomalies))
    return anomalies


def test_raw_sports(conn: Connection) -> list[dict]:
    """
    Run GE quality checks on raw.sports.

    Checks: employee_id not null, employee_id unique.

    Note:
        Sport value validation is deferred to the clean quality pass,
        where values have been normalized by cleaning.py.

    Args:
        conn: Active psycopg2 database connection.

    Returns:
        List of anomaly dicts. Empty if all checks pass.
    """

    df = read_table("raw.sports", conn)
    ge_df = ge.from_pandas(df)
    ge_df.set_default_expectation_argument("result_format", "COMPLETE")
    anomalies = []
    table = "raw.sports"

    anomalies += run_expectation(
        ge_df.expect_column_values_to_not_be_null("employee_id").to_json_dict(),
        df, table, "employee_id_not_null", "employee_id"
    )

    anomalies += run_expectation(
        ge_df.expect_column_values_to_be_unique("employee_id").to_json_dict(),
        df, table, "employee_id_unique", "employee_id"
    )

    logger.info("test_raw_sports — %s anomalies", len(anomalies))
    return anomalies


def test_raw_activities(conn: Connection) -> list[dict]:
    """
    Run GE quality checks on raw.activities.

    Args:
        conn: Active psycopg2 database connection.

    Returns:
        List of anomaly dicts. Empty if all checks pass.
    """

    df = read_table("raw.activities", conn)
    ge_df = ge.from_pandas(df)
    ge_df.set_default_expectation_argument("result_format", "COMPLETE")
    anomalies = []
    table = "raw.activities"

    for col in ("activity_id", "employee_id", "start_date", "end_date", "sport_type"):
        anomalies += run_expectation(
            ge_df.expect_column_values_to_not_be_null(col).to_json_dict(),
            df, table, f"{col}_not_null", "activity_id"
        )

    anomalies += run_expectation(
        ge_df.expect_column_values_to_be_between(
            "distance_m", min_value=0
        ).to_json_dict(),
        df, table, "distance_non_negative", "activity_id"
    )

    anomalies += run_expectation(
        ge_df.expect_column_values_to_be_unique("activity_id").to_json_dict(),
        df, table, "activity_id_unique", "activity_id"
    )

    logger.info("test_raw_activities — %s anomalies", len(anomalies))
    return anomalies


FILE_TEST_MAP = {
    "donnees_rh.xlsx":        test_raw_employees,
    "donnees_sportives.xlsx": test_raw_sports,
    "activites.csv":          test_raw_activities,
    "activites_init.csv":     test_raw_activities,
}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.error("Usage: python quality_tests_raw.py <filename>")
        logger.error("Supported files: %s", ", ".join(FILE_TEST_MAP.keys()))
        sys.exit(1)

    filename = Path(sys.argv[1]).name

    if filename not in FILE_TEST_MAP:
        logger.error("Unknown file: %s. Supported files: %s", filename, ", ".join(FILE_TEST_MAP.keys()))
        sys.exit(1)

    logger.info("Starting raw quality tests for: %s", filename)

    db_conn = psycopg2.connect(**DB_CONFIG)
    try:
        anomalies = FILE_TEST_MAP[filename](db_conn)
        write_anomalies(anomalies, db_conn)
        logger.info("Raw quality tests done — total anomalies: %s", len(anomalies))
    except Exception as e:
        logger.error("Raw quality tests failed: %s", e)
        raise
    finally:
        db_conn.close()