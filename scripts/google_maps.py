"""
Google Maps validation script — validates employee commute declarations.
"""

import os
import sys
import logging
from pathlib import Path
import requests

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

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
OFFICE_ADDRESS = "1362 Av. des Platanes, 34970 Lattes"


def load_commute_config(conn: Connection) -> dict:
    """
    Load commute modes configuration from config.commute_modes.

    Args:
        conn: Active psycopg2 database connection.

    Returns:
        Dict mapping mode -> {travel_mode, threshold_m} or
        mode -> {travel_mode: None, threshold_m: None} for non-eligible modes.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT mode, threshold_m, travel_mode FROM config.commute_modes")
        rows = cur.fetchall()

    if not rows:
        raise ValueError("config.commute_modes is empty — load commute_modes.csv first")

    return {
        mode: {"threshold_m": threshold_m, "travel_mode": travel_mode}
        for mode, threshold_m, travel_mode in rows
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


def get_distance_m(origin: str, travel_mode: str, api_key: str) -> int | None:
    """
    Call Google Maps Routes API to get distance between origin and office.

    Args:
        origin: Employee home address.
        travel_mode: Google Maps travel mode (WALK, BICYCLE).
        api_key: Google Maps API key.

    Returns:
        Distance in meters or None if API call fails.
    """

    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {
        "Content-Type":      "application/json",
        "X-Goog-Api-Key":    api_key,
        "X-Goog-FieldMask":  "routes.distanceMeters",
    }
    body = {
        "origin":      {"address": origin},
        "destination": {"address": OFFICE_ADDRESS},
        "travelMode":  travel_mode,
    }

    try:
        response = requests.post(url, headers=headers, json=body, timeout=10)
        data = response.json()
        return data["routes"][0]["distanceMeters"]
    except Exception as e:
        logger.error("API call failed for address %s: %s", origin, e)
        return None


def validate_commutes(filename: str, conn: Connection) -> None:
    """
    Validate commute declarations for employees in the current batch.

    Args:
        filename: Name of the ingested file, used to retrieve batch_id.
        conn: Active psycopg2 database connection.
    """

    commute_config = load_commute_config(conn)
    batch_id = get_current_batch_id(filename, conn)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT employee_id, first_name, last_name, home_address, commute_mode
            FROM clean.employees
            WHERE batch_id = %s
        """, (batch_id,))
        employees = cur.fetchall()

    logger.info("Processing %s employees", len(employees))

    updates = []
    anomalies = []

    for employee_id, first_name, last_name, home_address, commute_mode in employees:

        config = commute_config.get(commute_mode)

        # Unknown or non-eligible mode — no API call
        if config is None or config["travel_mode"] is None:
            updates.append((None, False, employee_id))
            continue

        distance_m = get_distance_m(home_address, config["travel_mode"], GOOGLE_MAPS_API_KEY)

        if distance_m is None:
            updates.append((None, False, employee_id))
            continue

        validated = distance_m <= config["threshold_m"]
        updates.append((distance_m, validated, employee_id))

        if not validated:
            anomalies.append((
                "clean.employees",
                "commute_distance_check",
                f"Employee {employee_id} ({first_name} {last_name}): "
                f"declared {commute_mode}, distance {distance_m}m "
                f"exceeds threshold {config['threshold_m']}m"
            ))
            logger.warning(
                "Anomaly — employee %s (%s %s): %sm > %sm threshold",
                employee_id, first_name, last_name, distance_m, config["threshold_m"]
            )

    # Update clean.employees
    with conn.cursor() as cur:
        for distance_m, validated, employee_id in updates:
            cur.execute("""
                UPDATE clean.employees
                SET commute_distance_m = %s,
                    commute_validated  = %s
                WHERE employee_id = %s
            """, (distance_m, validated, employee_id))

    # Log anomalies to quality_report
    if anomalies:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO quality_report.anomalies (stage, table_name, test_name, detail)
                VALUES %s
            """, [("clean", a[0], a[1], a[2]) for a in anomalies])

    conn.commit()
    logger.info("Commute validation done: %s updates, %s anomalies", len(updates), len(anomalies))


if __name__ == "__main__":
    if not GOOGLE_MAPS_API_KEY:
        logger.error("GOOGLE_MAPS_API_KEY is not set")
        sys.exit(1)

    if len(sys.argv) < 2:
        logger.error("Usage: python google_maps.py <filename>")
        sys.exit(1)

    filename = Path(sys.argv[1]).name

    db_conn = psycopg2.connect(**DB_CONFIG)
    try:
        validate_commutes(filename, db_conn)
    except Exception as e:
        logger.error("Google Maps validation failed: %s", e)
        raise
    finally:
        db_conn.close()
