"""
calculs.py — Eligibility calculation for sports benefits.
"""

import logging
import os

import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     int(os.getenv("POSTGRES_PORT", 5432)),
    "dbname":   os.getenv("POSTGRES_DB", "avantages_sportifs"),
    "user":     os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD"),
}


def get_parameters(conn) -> dict:
    """
    Fetch business parameters from config.parameters.

    Args:
        conn: Active psycopg2 database connection.

    Returns:
        dict with keys 'bonus_rate' (float) and 'activity_threshold' (int).
    """

    with conn.cursor() as cur:
        cur.execute("SELECT key, value FROM config.parameters;")
        rows = cur.fetchall()

    params = {key: value for key, value in rows}
    bonus_rate = float(params.get("bonus_rate", 0.05))
    activity_threshold = int(float(params.get("activity_threshold", 15)))

    logger.info(
        "Parameters loaded — bonus_rate=%.2f, activity_threshold=%d",
        bonus_rate,
        activity_threshold,
    )
    return {"bonus_rate": bonus_rate, "activity_threshold": activity_threshold}


def get_eligible_commute_modes(conn) -> set:
    """
    Fetch eligible commute modes from config.commute_modes.

    Args:
        conn: Active psycopg2 database connection.

    Returns:
        Set of eligible commute mode strings.
    """

    with conn.cursor() as cur:
        cur.execute("SELECT mode FROM config.commute_modes;")
        all_modes = {row[0] for row in cur.fetchall()}

    non_eligible = {"véhicule thermique/électrique", "transports en commun"}
    eligible = all_modes - non_eligible

    logger.info("Eligible commute modes: %s", eligible)
    return eligible


def compute_activity_counts(conn) -> dict:
    """
    Count valid activities per employee, excluding anomalies.

    Args:
        conn: Active psycopg2 database connection.

    Returns:
        dict mapping employee_id (int) -> valid activity count (int).
    """

    with conn.cursor() as cur:
        cur.execute("""
            SELECT a.employee_id, COUNT(*) AS activity_count
            FROM clean.activities a
            WHERE a.activity_id NOT IN (
                SELECT activity_id
                FROM quality_report.anomalies
                WHERE activity_id IS NOT NULL
            )
            GROUP BY a.employee_id;
        """)
        rows = cur.fetchall()

    return {employee_id: count for employee_id, count in rows}


def compute_eligibilities(conn, params: dict) -> None:
    """
    Compute eligibilities and UPSERT into results.eligibility.

    Args:
        conn: Active psycopg2 connection.
        params: Dict with 'bonus_rate' and 'activity_threshold'.
    """

    bonus_rate = params["bonus_rate"]
    activity_threshold = params["activity_threshold"]

    eligible_modes = get_eligible_commute_modes(conn)

    # Fetch all employees from clean schema
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                employee_id,
                last_name,
                first_name,
                bu,
                gross_salary,
                commute_mode,
                commute_validated
            FROM clean.employees;
        """)
        employees = cur.fetchall()

    logger.info("Processing %d employees", len(employees))

    activity_counts = compute_activity_counts(conn)

    rows = []
    for (
        employee_id,
        last_name,
        first_name,
        bu,
        gross_salary,
        commute_mode,
        commute_validated,
    ) in employees:

        # --- Sports bonus ---
        eligible_prime = (
            commute_mode in eligible_modes
            and commute_validated is True
        )
        prime_amount = float(gross_salary or 0) * bonus_rate if eligible_prime else 0.0

        # --- Wellness days ---
        count = activity_counts.get(employee_id, 0)
        eligible_wellness = count >= activity_threshold

        rows.append((
            employee_id,
            last_name,
            first_name,
            bu,
            gross_salary,
            commute_mode,
            commute_validated,
            eligible_prime,
            round(prime_amount, 2),
            count,
            eligible_wellness,
        ))

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO results.eligibility (
                employee_id,
                last_name,
                first_name,
                bu,
                gross_salary,
                commute_mode,
                commute_validated,
                eligible_prime,
                prime_amount,
                activity_count,
                eligible_wellness
            ) VALUES %s
            ON CONFLICT (employee_id) DO UPDATE SET
                last_name         = EXCLUDED.last_name,
                first_name        = EXCLUDED.first_name,
                bu                = EXCLUDED.bu,
                gross_salary      = EXCLUDED.gross_salary,
                commute_mode      = EXCLUDED.commute_mode,
                commute_validated = EXCLUDED.commute_validated,
                eligible_prime    = EXCLUDED.eligible_prime,
                prime_amount      = EXCLUDED.prime_amount,
                activity_count    = EXCLUDED.activity_count,
                eligible_wellness = EXCLUDED.eligible_wellness,
                calculated_at     = NOW();
            """,
            rows,
        )

    conn.commit()

    prime_count    = sum(1 for r in rows if r[7])   # eligible_prime index
    wellness_count = sum(1 for r in rows if r[10])  # eligible_wellness index
    total_prime    = sum(r[8] for r in rows)

    logger.info(
        "Eligibility calculated — %d employees | prime: %d eligible (total €%.2f) "
        "| wellness: %d eligible",
        len(rows),
        prime_count,
        total_prime,
        wellness_count,
    )


if __name__ == "__main__":
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        params = get_parameters(conn)
        compute_eligibilities(conn, params)
        logger.info("calculs.py completed successfully")
    except Exception as e:
        logger.error("calculs.py failed: %s", e)
        raise
    finally:
        conn.close()
