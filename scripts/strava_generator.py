"""
Strava-like activity generator — generates synthetic sports activities for the last 12 months.
"""

import os
import logging
import random
import csv
from datetime import datetime, timedelta
from pathlib import Path

import psycopg2
from psycopg2.extensions import connection as Connection
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

OUTPUT_FILE = Path(__file__).parent.parent / "data" / "activites_init.csv"

ELIGIBLE_RATIO = 0.75

ELIGIBLE_ACTIVITY_COUNT   = (15, 80)
INELIGIBLE_ACTIVITY_COUNT = (1, 14)

COMMENTS = [
    "Belle séance !",
    "Reprise du sport :)",
    "Fatigant mais satisfaisant",
    "Super conditions aujourd'hui",
    "Nouveau record personnel !",
    "Séance de récupération",
    "Objectif atteint !",
    "Bonne sortie",
    None,
    None,
    None,
]


def load_sport_config(conn: Connection) -> dict:
    """
    Load sport configuration from config.sports.

    Args:
        conn: Active psycopg2 database connection.

    Returns:
        Dict mapping sport name to its configuration dict with keys:
        max_speed_kmh, min_duration_min, has_distance.
    """

    with conn.cursor() as cur:
        cur.execute("""
            SELECT sport, max_speed_kmh, min_duration_min, has_distance
            FROM config.sports
        """)
        return {
            row[0]: {
                "max_speed_kmh":    float(row[1]) if row[1] is not None else None,
                "min_duration_min": int(row[2]),
                "has_distance":     bool(row[3]),
            }
            for row in cur.fetchall()
        }


def generate_activity(
    activity_id: int,
    employee_id: int,
    sport: str,
    date: datetime,
    sport_config: dict,
) -> dict:
    """
    Generate a single realistic activity for a given sport and date.

    Args:
        activity_id: Unique activity identifier.
        employee_id: Employee identifier.
        sport: Sport type.
        date: Activity start date.
        sport_config: Sport configuration dict loaded from config.sports.

    Returns:
        Dictionary representing one activity row.
    """

    config = sport_config.get(sport, sport_config.get("Running"))

    min_duration = config["min_duration_min"]
    duration_min = random.randint(min_duration, min_duration * 3)
    end_date = date + timedelta(minutes=duration_min)

    if config["has_distance"] and config["max_speed_kmh"]:
        max_distance_m = int(config["max_speed_kmh"] * duration_min / 60 * 1000 * 0.85)
        min_distance_m = int(config["max_speed_kmh"] * duration_min / 60 * 1000 * 0.3)
        distance_m = random.randint(min_distance_m, max_distance_m)
    else:
        distance_m = None

    return {
        "activity_id":  activity_id,
        "employee_id":  employee_id,
        "start_date":   date.strftime("%Y-%m-%d %H:%M:%S"),
        "sport_type":   sport,
        "distance_m":   distance_m if distance_m is not None else "",
        "end_date":     end_date.strftime("%Y-%m-%d %H:%M:%S"),
        "comment":      random.choice(COMMENTS) or "",
    }


def get_employees_with_sport(conn: Connection) -> list[tuple]:
    """
    Read employees with a declared sport from clean.sports.

    Args:
        conn: Active psycopg2 database connection.

    Returns:
        List of (employee_id, sport) tuples.
    """

    with conn.cursor() as cur:
        cur.execute("""
            SELECT employee_id, sport
            FROM clean.sports
            WHERE sport IS NOT NULL
        """)
        return cur.fetchall()


def generate_activities(conn: Connection) -> list[dict]:
    """
    Generate synthetic activities for all employees with a declared sport.

    Args:
        conn: Active psycopg2 database connection.

    Returns:
        List of activity dictionaries.
    """

    employees = get_employees_with_sport(conn)
    sport_config = load_sport_config(conn)
    logger.info("Found %s employees with a declared sport", len(employees))

    now = datetime.now()
    one_year_ago = now - timedelta(days=365)

    random.shuffle(employees)
    eligible_count = int(len(employees) * ELIGIBLE_RATIO)

    activities = []
    activity_id = 1

    for i, (employee_id, sport) in enumerate(employees):

        if i < eligible_count:
            activity_count = random.randint(*ELIGIBLE_ACTIVITY_COUNT)
        else:
            activity_count = random.randint(*INELIGIBLE_ACTIVITY_COUNT)

        dates = sorted([
            one_year_ago + timedelta(
                days=random.randint(0, 364),
                hours=random.randint(6, 20),
                minutes=random.randint(0, 59)
            )
            for _ in range(activity_count)
        ])

        for date in dates:
            activities.append(
                generate_activity(activity_id, employee_id, sport, date, sport_config)
            )
            activity_id += 1

    logger.info("Generated %s activities", len(activities))
    return activities


def save_to_csv(activities: list[dict]) -> None:
    """
    Save generated activities to CSV file.

    Args:
        activities: List of activity dictionaries.
    """

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "activity_id", "employee_id", "start_date", "sport_type",
            "distance_m", "end_date", "comment"
        ])
        writer.writeheader()
        writer.writerows(activities)

    logger.info("Saved to %s", OUTPUT_FILE)


if __name__ == "__main__":
    db_conn = psycopg2.connect(**DB_CONFIG)
    try:
        gen_activities = generate_activities(db_conn)
        save_to_csv(gen_activities)
    except Exception as e:
        logger.error("Generation failed: %s", e)
        raise
    finally:
        db_conn.close()
