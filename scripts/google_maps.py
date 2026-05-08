"""
Google Maps validation script — validates employee commute declarations.
"""

import os
import sys
import logging
import requests

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

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
OFFICE_ADDRESS = "1362 Av. des Platanes, 34970 Lattes"

COMMUTE_CONFIG = {
    "marche/running": {
        "travel_mode": "WALK",
        "threshold_m": 15000,
    },
    "vélo/trottinette/autres": {
        "travel_mode": "BICYCLE",
        "threshold_m": 25000,
    },
}

ELIGIBLE_MODES = set(COMMUTE_CONFIG.keys())


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


def validate_commutes(conn: Connection) -> None:
    """
    Validate commute declarations for eligible employees and update clean.employees.

    Args:
        conn: Active psycopg2 database connection.
    """

    with conn.cursor() as cur:
        cur.execute("""
            SELECT employee_id, first_name, last_name, home_address, commute_mode
            FROM clean.employees
        """)
        employees = cur.fetchall()

    logger.info("Processing %s employees", len(employees))

    updates = []
    anomalies = []

    for employee_id, first_name, last_name, home_address, commute_mode in employees:

        # Non-eligible modes — no API call
        if commute_mode not in ELIGIBLE_MODES:
            updates.append((None, False, employee_id))
            continue

        config = COMMUTE_CONFIG[commute_mode]
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
                INSERT INTO quality_report.anomalies (table_name, test_name, detail)
                VALUES %s
            """, anomalies)

    conn.commit()
    logger.info("Commute validation done: %s updates, %s anomalies", len(updates), len(anomalies))


if __name__ == "__main__":
    if not GOOGLE_MAPS_API_KEY:
        logger.error("GOOGLE_MAPS_API_KEY is not set")
        sys.exit(1)

    db_conn = psycopg2.connect(**DB_CONFIG)
    try:
        validate_commutes(db_conn)
    except Exception as e:
        logger.error("Google Maps validation failed: %s", e)
        raise
    finally:
        db_conn.close()
