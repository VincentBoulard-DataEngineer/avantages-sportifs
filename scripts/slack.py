"""
slack.py — Send Slack notifications for new activities.
"""

import logging
import os
import random

import psycopg2
import requests

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

SLACK_BOT_TOKEN  = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID")
SLACK_API_URL    = os.getenv("SLACK_API_URL", "https://slack.com/api/chat.postMessage")

INTROS = [
    "Bravo {name} !",
    "Magnifique {name} !",
    "Chapeau {name} !",
    "Respect {name} !",
    "Belle performance {name} !",
]

BODY_WITH_DISTANCE = [
    "{sport} de {distance} en {duration} !",
    "{distance} de {sport} bouclés en {duration} !",
    "{duration} de {sport} et {distance} au compteur !",
]

BODY_WITHOUT_DISTANCE = [
    "{sport} de {duration} !",
    "{duration} de {sport} au compteur !",
    "Une belle séance de {sport} de {duration} !",
]

EMOJIS = ["🔥", "💪", "🏅", "🎉", "⚡", "🙌", "👏"]


def format_duration(start_date, end_date) -> str:
    """
    Format duration between two timestamps as a human-readable string.

    Args:
        start_date: Activity start datetime.
        end_date: Activity end datetime.

    Returns:
        Duration string, e.g. '46 min' or '1h 15min'.
    """

    total_minutes = int((end_date - start_date).total_seconds() / 60)
    if total_minutes < 60:
        return f"{total_minutes} min"
    hours   = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours}h {minutes}min" if minutes else f"{hours}h"


def format_distance(distance_m) -> str:
    """
    Format distance in meters as a human-readable string.

    Args:
        distance_m: Distance in meters (integer or None).

    Returns:
        Distance string, e.g. '10,8 km'.
    """

    km = distance_m / 1000
    return f"{km:,.1f} km".replace(".", ",")


def build_message(first_name: str, last_name: str, sport_type: str,
                  distance_m, start_date, end_date, comment: str) -> str:
    """
    Build a randomized Slack notification message for one activity.

    Args:
        first_name: Employee first name.
        last_name: Employee last name.
        sport_type: Type of sport (e.g. 'Running', 'Randonnée').
        distance_m: Distance in meters, or None if not applicable.
        start_date: Activity start datetime.
        end_date: Activity end datetime.
        comment: Optional free-text comment from the employee.

    Returns:
        Formatted Slack message string.
    """

    name     = f"{first_name} {last_name}"
    duration = format_duration(start_date, end_date)
    emoji    = random.choice(EMOJIS)

    intro = random.choice(INTROS).format(name=name)

    if distance_m:
        distance = format_distance(distance_m)
        body = random.choice(BODY_WITH_DISTANCE).format(
            sport=sport_type, distance=distance, duration=duration
        )
    else:
        body = random.choice(BODY_WITHOUT_DISTANCE).format(
            sport=sport_type, duration=duration
        )

    message = f"{intro} {body} {emoji}"

    if comment:
        message += f'\n"{comment}"'

    return message


def send_slack_message(message: str) -> bool:
    """
    Send a message to the configured Slack channel.

    Args:
        message: Text to send.

    Returns:
        True if the message was sent successfully, False otherwise.
    """

    response = requests.post(
        SLACK_API_URL,
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json={"channel": SLACK_CHANNEL_ID, "text": message},
        timeout=10,
    )
    data = response.json()
    if not data.get("ok"):
        logger.error("Slack API error: %s", data.get("error"))
        return False
    return True


def fetch_pending_activities(conn, batch_id: int) -> list:
    """
    Fetch activities where slack_notified = FALSE for the current batch.

    Scoped to the current batch_id to avoid notifying activities from
    a previous run that may not have been marked yet.

    Args:
        conn: Active psycopg2 database connection.
        batch_id: Current batch ID to scope the query.

    Returns:
        List of tuples: (activity_id, first_name, last_name, sport_type,
                         distance_m, start_date, end_date, comment).
    """

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                a.activity_id,
                e.first_name,
                e.last_name,
                a.sport_type,
                a.distance_m,
                a.start_date,
                a.end_date,
                a.comment
            FROM clean.activities a
            JOIN clean.employees e ON a.employee_id = e.employee_id
            WHERE a.slack_notified = FALSE
            AND a.batch_id = %s;
        """, (batch_id,))
        return cur.fetchall()


def mark_notified(conn, activity_id: int) -> None:
    """
    Mark a single activity as notified in clean.activities.

    Args:
        conn: Active psycopg2 database connection.
        activity_id: ID of the activity to mark.
    """

    with conn.cursor() as cur:
        cur.execute("""
            UPDATE clean.activities
            SET slack_notified = TRUE
            WHERE activity_id = %s;
        """, (activity_id,))
    conn.commit()


def get_current_batch_id(filename: str, conn) -> int:
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
            LIMIT 1;
        """, (filename,))
        row = cur.fetchone()
    if not row:
        raise ValueError(f"No completed batch found for filename: {filename}")
    return row[0]


def run(conn) -> None:
    """
    Poll pending activities for the current batch and send Slack notifications.

    Args:
        conn: Active psycopg2 database connection.
    """

    if not SLACK_BOT_TOKEN or not SLACK_CHANNEL_ID:
        logger.error("SLACK_BOT_TOKEN or SLACK_CHANNEL_ID is not set")
        raise ValueError("Missing Slack configuration")

    batch_id = get_current_batch_id("activites.csv", conn)
    logger.info("Processing batch_id=%d", batch_id)

    activities = fetch_pending_activities(conn, batch_id)
    logger.info("Found %d activities to notify", len(activities))

    sent = 0
    for (activity_id, first_name, last_name, sport_type,
         distance_m, start_date, end_date, comment) in activities:

        message = build_message(
            first_name, last_name, sport_type,
            distance_m, start_date, end_date, comment
        )

        if send_slack_message(message):
            mark_notified(conn, activity_id)
            sent += 1
            logger.info("Notified activity %s — %s %s", activity_id, first_name, last_name)
        else:
            logger.warning("Failed to notify activity %s", activity_id)

    logger.info("slack.py done — %d/%d notifications sent", sent, len(activities))


if __name__ == "__main__":
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        run(conn)
    except Exception as e:
        logger.error("slack.py failed: %s", e)
        raise
    finally:
        conn.close()
