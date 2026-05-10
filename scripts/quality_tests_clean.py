"""
Clean quality tests — runs Great Expectations validation checks on clean tables
and writes anomalies to quality_report.anomalies (stage='clean').
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
    Read rows from a clean table filtered by batch_id.

    Args:
        table: Fully qualified table name (e.g. 'clean.employees').
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


def read_full_table(table: str, conn: Connection) -> pd.DataFrame:
    """
    Read all rows from a table — used for referential integrity checks.

    Args:
        table: Fully qualified table name (e.g. 'clean.employees').
        conn: Active psycopg2 database connection.

    Returns:
        DataFrame containing all rows, with NaN values replaced by None.
    """

    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {table}")
        columns = [desc[0] for desc in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=columns)
        return df.where(pd.notnull(df), None)


def load_valid_commute_modes(conn: Connection) -> list:
    """
    Load valid commute modes from config.commute_modes.

    Args:
        conn: Active psycopg2 database connection.

    Returns:
        List of valid commute mode strings.
    """

    with conn.cursor() as cur:
        cur.execute("SELECT mode FROM config.commute_modes")
        return [row[0] for row in cur.fetchall()]


def load_sport_rules(conn: Connection) -> dict:
    """
    Load sport physical constraints from config.sports.

    Args:
        conn: Active psycopg2 database connection.

    Returns:
        Dict mapping sport name to its constraints (max_speed_kmh, min_duration_min).
    """

    with conn.cursor() as cur:
        cur.execute("SELECT sport, max_speed_kmh, min_duration_min FROM config.sports")
        return {
            row[0]: {
                "max_speed_kmh":    float(row[1]) if row[1] is not None else None,
                "min_duration_min": int(row[2]),
            }
            for row in cur.fetchall()
        }


def load_valid_sports(conn: Connection) -> list:
    """
    Load valid sport names from config.sports.

    Args:
        conn: Active psycopg2 database connection.

    Returns:
        List of valid sport name strings.
    """

    with conn.cursor() as cur:
        cur.execute("SELECT sport FROM config.sports")
        return [row[0] for row in cur.fetchall()]


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
        table_name: Fully qualified table name (e.g. 'clean.employees').
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
    Write detected anomalies to quality_report.anomalies with stage='clean'.

    Args:
        anomalies: List of anomaly dicts from run_expectation() or pandas checks.
        conn: Active psycopg2 database connection.
    """

    if not anomalies:
        logger.info("No clean anomalies detected")
        return
    rows = [
        (
            "clean",
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
    logger.info("Clean anomalies written: %s", len(rows))


def test_clean_employees(filename: str, conn: Connection) -> list[dict]:
    """
    Run GE quality checks on clean.employees for the current batch.

    Args:
        filename: Name of the ingested file, used to retrieve batch_id.
        conn: Active psycopg2 database connection.

    Returns:
        List of anomaly dicts. Empty if all checks pass.
    """

    batch_id    = get_current_batch_id(filename, conn)
    df          = read_table("clean.employees", batch_id, conn)
    valid_modes = load_valid_commute_modes(conn)
    anomalies   = []
    table       = "clean.employees"

    df["birth_date"] = pd.to_datetime(df["birth_date"])
    df["hire_date"]  = pd.to_datetime(df["hire_date"])

    ge_df = ge.from_pandas(df)
    ge_df.set_default_expectation_argument("result_format", "COMPLETE")

    # commute_mode in valid set — GE native
    anomalies += run_expectation(
        ge_df.expect_column_values_to_be_in_set(
            "commute_mode", valid_modes
        ).to_json_dict(),
        df, table, "commute_mode_valid", "employee_id"
    )

    # hire_date > birth_date — GE column pair
    anomalies += run_expectation(
        ge_df.expect_column_pair_values_A_to_be_greater_than_B(
            "hire_date", "birth_date"
        ).to_json_dict(),
        df, table, "hire_date_after_birth_date", "employee_id"
    )

    # hire_date in the past — pandas fallback (comparison with dynamic value NOW())
    df_dates = df[df["hire_date"].notna()]
    bad = df_dates[df_dates["hire_date"] > pd.Timestamp.today()]
    for row in bad.itertuples():
        anomalies.append({
            "table_name":  table,
            "test_name":   "hire_date_in_past",
            "detail":      f"employee_id={row.employee_id} — hire_date={row.hire_date.date()}",
            "employee_id": int(row.employee_id),
        })

    logger.info("test_clean_employees — %s anomalies", len(anomalies))
    return anomalies


def test_clean_sports(filename: str, conn: Connection) -> list[dict]:
    """
    Run GE quality checks on clean.sports for the current batch.

    Args:
        filename: Name of the ingested file, used to retrieve batch_id.
        conn: Active psycopg2 database connection.

    Returns:
        List of anomaly dicts. Empty if all checks pass.
    """

    batch_id     = get_current_batch_id(filename, conn)
    df           = read_table("clean.sports", batch_id, conn)
    df_employees = read_full_table("clean.employees", conn)
    anomalies    = []
    table        = "clean.sports"

    # employee_id referential integrity — pandas fallback (no native GE cross-table check)
    valid_ids = set(df_employees["employee_id"])
    bad = df[~df["employee_id"].isin(valid_ids)]
    for row in bad.itertuples():
        anomalies.append({
            "table_name":  table,
            "test_name":   "employee_id_exists",
            "detail":      f"employee_id={row.employee_id} not found in clean.employees",
            "employee_id": int(row.employee_id),
        })

    logger.info("test_clean_sports — %s anomalies", len(anomalies))
    return anomalies


def test_clean_activities(filename: str, conn: Connection) -> list[dict]:
    """
    Run GE and pandas quality checks on clean.activities for the current batch.

    Args:
        filename: Name of the ingested file, used to retrieve batch_id.
        conn: Active psycopg2 database connection.

    Returns:
        List of anomaly dicts. Empty if all checks pass.
    """

    batch_id     = get_current_batch_id(filename, conn)
    df           = read_table("clean.activities", batch_id, conn)
    df_employees = read_full_table("clean.employees", conn)
    valid_sports = load_valid_sports(conn)
    sport_rules  = load_sport_rules(conn)
    anomalies    = []
    table        = "clean.activities"

    df["start_date"]   = pd.to_datetime(df["start_date"])
    df["end_date"]     = pd.to_datetime(df["end_date"])
    df["duration_min"] = (df["end_date"] - df["start_date"]).dt.total_seconds() / 60

    ge_df = ge.from_pandas(df)
    ge_df.set_default_expectation_argument("result_format", "COMPLETE")

    # sport_type in valid set — GE native
    anomalies += run_expectation(
        ge_df.expect_column_values_to_be_in_set(
            "sport_type", valid_sports
        ).to_json_dict(),
        df, table, "sport_type_valid", "activity_id"
    )

    # end_date > start_date — GE column pair
    anomalies += run_expectation(
        ge_df.expect_column_pair_values_A_to_be_greater_than_B(
            "end_date", "start_date"
        ).to_json_dict(),
        df, table, "end_date_after_start_date", "activity_id"
    )

    # duration > 2 minutes — GE native on pre-computed column
    anomalies += run_expectation(
        ge_df.expect_column_values_to_be_between(
            "duration_min", min_value=2, strict_min=True
        ).to_json_dict(),
        df, table, "duration_above_2min", "activity_id"
    )

    # no duplicates (employee_id + start_date) — GE compound unique
    anomalies += run_expectation(
        ge_df.expect_compound_columns_to_be_unique(
            ["employee_id", "start_date"]
        ).to_json_dict(),
        df, table, "no_duplicate_activity", "activity_id"
    )

    # physical consistency per sport — pandas fallback
    # (requires per-sport iteration, not supported natively by GE)
    for sport, rules in sport_rules.items():
        df_sport = df[df["sport_type"] == sport].copy()
        if df_sport.empty:
            continue

        # min duration per sport
        bad = df_sport[df_sport["duration_min"] < rules["min_duration_min"]]
        for row in bad.itertuples():
            anomalies.append({
                "table_name":  table,
                "test_name":   "min_duration_per_sport",
                "detail":      f"activity_id={row.activity_id} — {sport}: {row.duration_min:.1f} min < {rules['min_duration_min']} min",
                "activity_id": int(row.activity_id),
            })

        # max speed per sport
        if rules["max_speed_kmh"] is not None:
            df_dist = df_sport[df_sport["distance_m"].notna() & (df_sport["duration_min"] > 0)].copy()
            df_dist["speed_kmh"] = (df_dist["distance_m"] / 1000) / (df_dist["duration_min"] / 60)
            bad = df_dist[df_dist["speed_kmh"] > rules["max_speed_kmh"]]
            for row in bad.itertuples():
                anomalies.append({
                    "table_name":  table,
                    "test_name":   "max_speed_per_sport",
                    "detail":      f"activity_id={row.activity_id} — {sport}: {row.speed_kmh:.1f} km/h > {rules['max_speed_kmh']} km/h",
                    "activity_id": int(row.activity_id),
                })

    # employee_id referential integrity — pandas fallback (no native GE cross-table check)
    valid_ids = set(df_employees["employee_id"])
    bad = df[~df["employee_id"].isin(valid_ids)]
    for row in bad.itertuples():
        anomalies.append({
            "table_name":  table,
            "test_name":   "employee_id_exists",
            "detail":      f"activity_id={row.activity_id} — employee_id={row.employee_id} not found in clean.employees",
            "activity_id": int(row.activity_id),
        })

    logger.info("test_clean_activities — %s anomalies", len(anomalies))
    return anomalies


FILE_TEST_MAP = {
    "donnees_rh.xlsx":        test_clean_employees,
    "donnees_sportives.xlsx": test_clean_sports,
    "activites.csv":          test_clean_activities,
    "activites_init.csv":     test_clean_activities,
}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.error("Usage: python quality_tests_clean.py <filename>")
        logger.error("Supported files: %s", ", ".join(FILE_TEST_MAP.keys()))
        sys.exit(1)

    filename = Path(sys.argv[1]).name

    if filename not in FILE_TEST_MAP:
        logger.error("Unknown file: %s. Supported files: %s", filename, ", ".join(FILE_TEST_MAP.keys()))
        sys.exit(1)

    logger.info("Starting clean quality tests for: %s", filename)

    db_conn = psycopg2.connect(**DB_CONFIG)
    try:
        anomalies = FILE_TEST_MAP[filename](filename, db_conn)
        write_anomalies(anomalies, db_conn)
        logger.info("Clean quality tests done — total anomalies: %s", len(anomalies))
    except Exception as e:
        logger.error("Clean quality tests failed: %s", e)
        raise
    finally:
        db_conn.close()
