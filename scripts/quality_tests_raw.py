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
        DataFrame containing only rows from the current batch,
        with NaN values replaced by None.
    """

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM {table} WHERE batch_id = %s",
            (batch_id,)
        )
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


def test_raw_employees(filename: str, conn: Connection) -> list[dict]:
    """
    Run GE quality checks on raw.employees for the current batch.

    Args:
        filename: Name of the ingested file, used to retrieve batch_id.
        conn: Active psycopg2 database connection.

    Returns:
        List of anomaly dicts. Empty if all checks pass.
    """

    batch_id = get_current_batch_id(filename, conn)
    df    = read_table("raw.employees", batch_id, conn)
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


def test_raw_sports(filename: str, conn: Connection) -> list[dict]:
    """
    Run GE quality checks on raw.sports for the current batch.

    Checks: employee_id not null, employee_id unique.
    Sport value validation is deferred to the clean quality pass,
    where values have been normalized by cleaning.py.

    Args:
        filename: Name of the ingested file, used to retrieve batch_id.
        conn: Active psycopg2 database connection.

    Returns:
        List of anomaly dicts. Empty if all checks pass.
    """

    batch_id = get_current_batch_id(filename, conn)
    df    = read_table("raw.sports", batch_id, conn)
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


def test_raw_activities(filename: str, conn: Connection) -> list[dict]:
    """
    Run GE quality checks on raw.activities for the current batch.

    Checks: mandatory fields not null, distance_m >= 0, activity_id unique.
    Sport type validation is deferred to the clean quality pass.

    Args:
        filename: Name of the ingested file, used to retrieve batch_id.
        conn: Active psycopg2 database connection.

    Returns:
        List of anomaly dicts. Empty if all checks pass.
    """

    batch_id = get_current_batch_id(filename, conn)
    df    = read_table("raw.activities", batch_id, conn)
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
        anomalies = FILE_TEST_MAP[filename](filename, db_conn)
        write_anomalies(anomalies, db_conn)
        logger.info("Raw quality tests done — total anomalies: %s", len(anomalies))
    except Exception as e:
        logger.error("Raw quality tests failed: %s", e)
        raise
    finally:
        db_conn.close()
