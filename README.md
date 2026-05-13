# Avantages Sportifs — POC

Data pipeline for managing employee sports benefits.

## Prerequisites

Create the runtime directories before the first launch:

```bash
mkdir inbox archive/inbox archive/params params
```

## Stack

- PostgreSQL 15
- Kestra (latest)
- Python / Pandas / Great Expectations
- Power BI

## Start

```bash
docker-compose up -d
```

## Initial setup

Before running the pipeline, load the reference data by dropping the files into `/params/`:

- `data/params.csv` — business parameters (bonus rate, activity threshold)
- `data/sports.csv` — sports reference data with physical constraints
- `data/commute_modes.csv` — commute modes with distance thresholds and travel modes

## How it works

Drop a file into the `/inbox/` folder. Kestra detects it automatically and triggers
the appropriate treatment based on the filename. Processed files are moved to `/archive/inbox/`
with a timestamp prefix (e.g. `20260508101950_donnees_rh.xlsx`).

| File                     | Treatment                                                                  |
| ------------------------ | -------------------------------------------------------------------------- |
| `donnees_rh.xlsx`        | HR data ingestion + raw quality tests + cleaning + Google Maps validation  |
| `donnees_sportives.xlsx` | Sports declarations ingestion + raw quality tests + cleaning               |
| `activites_init.csv`     | Activities ingestion + raw quality tests + cleaning, no Slack notification |
| `activites.csv`          | Activities ingestion + raw quality tests + cleaning + Slack notification   |

## Pipeline steps

For each file dropped in `/inbox/`, Kestra executes the following sequence:

| Step | Script                   | Description                                                         |
| ---- | ------------------------ | ------------------------------------------------------------------- |
| 1    | `ingestion.py`           | Create batch, load raw data into `raw.*` schema                     |
| 2    | `quality_tests_raw.py`   | Run Great Expectations checks on the ingested raw table             |
| 3    | `cleaning.py`            | Normalize and load into `clean.*` schema                            |
| 4    | `quality_tests_clean.py` | Run Great Expectations checks on the cleaned data                   |
| 5    | `google_maps.py`         | Validate commute distances (HR file only)                           |
| 6    | `slack.py`               | Send Slack notifications for new activities (`activites.csv` only)  |
| 7    | `calculs.py`             | Compute eligibilities and UPSERT into `results.eligibility`         |

## Batch tracking

Each file ingestion creates a batch entry in `config.batches` with a unique `batch_id`.
This `batch_id` is propagated to all `raw.*` and `clean.*` tables, allowing downstream
scripts (`quality_tests_raw.py`, `cleaning.py`, `google_maps.py`) to process only the
rows from the current batch — not the entire table.

| Status    | Meaning                                          |
| --------- | ------------------------------------------------ |
| `running` | Ingestion in progress                            |
| `done`    | Ingestion completed — batch ready for processing |
| `failed`  | Ingestion failed                                 |

## Raw quality tests (Great Expectations)

Quality checks run on `raw.*` tables after each ingestion, targeting only the rows
from the current batch. Anomalies are written to `quality_report.anomalies`
with `stage='raw'`.

| File                                   | Table tested     | Checks                                                              |
| -------------------------------------- | ---------------- | ------------------------------------------------------------------- |
| `donnees_rh.xlsx`                      | `raw.employees`  | Mandatory fields not null, `gross_salary > 0`, `employee_id` unique |
| `donnees_sportives.xlsx`               | `raw.sports`     | `employee_id` not null, `employee_id` unique                        |
| `activites.csv` / `activites_init.csv` | `raw.activities` | Mandatory fields not null, `distance_m >= 0`, `activity_id` unique  |

## Clean quality tests (Great Expectations)

Quality checks run on `clean.*` tables after each cleaning pass, targeting only the rows
from the current batch. Anomalies are written to `quality_report.anomalies`
with `stage='clean'`.

GE native checks are used where possible; pandas fallback is used for cross-table
referential integrity and per-sport physical computations.

| File                                   | Table tested       | Checks                                                                                                                                                                      |
| -------------------------------------- | ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `donnees_rh.xlsx`                      | `clean.employees`  | `commute_mode` in valid set, `hire_date > birth_date`, `hire_date` in the past                                                                                              |
| `donnees_sportives.xlsx`               | `clean.sports`     | `employee_id` exists in `clean.employees`                                                                                                                                   |
| `activites.csv` / `activites_init.csv` | `clean.activities` | `sport_type` in valid set, `end_date > start_date`, duration > 2 min, max speed per sport, min duration per sport, no duplicates, `employee_id` exists in `clean.employees` |

## Parameters pipeline

Business parameters, sports reference data and commute modes are managed separately
via the `/params/` folder. Dropping a file into `/params/` triggers `flow_params`,
which updates the config tables and triggers a full recalculation of eligibilities.

| File                | Table updated          | Description                            |
| ------------------- | ---------------------- | -------------------------------------- |
| `params.csv`        | `config.parameters`    | Business parameters (bonus rate, etc.) |
| `sports.csv`        | `config.sports`        | Sports reference data with constraints |
| `commute_modes.csv` | `config.commute_modes` | Commute modes with distance thresholds |

Processed files are archived to `/archive/params/`.
²
To update a parameter, edit the relevant file in `data/` and drop it into `/params/`.

## Google Maps commute validation

When `donnees_rh.xlsx` is processed, the pipeline validates each employee's commute
declaration against their home address using the Google Maps Routes API.
Only employees from the current batch are processed.

Commute mode configuration is loaded from `config.commute_modes` — no hardcoded values.
Non-eligible modes (null `travel_mode`) are automatically excluded from the sports bonus.
Anomalies are logged in `quality_report.anomalies`.

## Slack notifications

When `activites.csv` is processed, `slack.py` sends one Slack message per activity
to the `#activites-sportives` channel. Activities from `activites_init.csv` are never
notified — the filename is the signal.

Each message includes the employee's name, sport type, duration, distance (if applicable),
and optional comment. Intros, message bodies, and emojis are randomized for variety.
Only activities from the current batch are processed (`batch_id` scoped query).
Once notified, each activity is flagged `slack_notified = TRUE` to prevent duplicates.
Activities flagged as anomalies in `quality_report.anomalies` are excluded from notifications — only valid activities trigger a Slack message.

## Eligibility calculation

After all files in `/inbox/` are processed, `calculs.py` runs once and computes
eligibility for both benefits for all employees.

| Benefit        | Condition                                                              | Amount                        |
| -------------- | ---------------------------------------------------------------------- | ----------------------------- |
| Sports bonus   | Eligible commute mode + `commute_validated = TRUE`                     | `gross_salary × bonus_rate`   |
| Wellness days  | `activity_count >= activity_threshold` (anomalous activities excluded) | 5 days                        |

Results are written to `results.eligibility` via UPSERT. Power BI reads this table directly.
`calculs.py` is also triggered by `flow_params` whenever business parameters are updated,
ensuring results are always consistent with the current configuration.

## Activity data generation

Synthetic activities for the last 12 months are generated by `strava_generator.py`.
Sport configurations are loaded from `config.sports` — no hardcoded values.
The output file `data/activites_init.csv` is included in the repository as the demo dataset.

To regenerate (requires `config.sports` and `clean.sports` to be populated first):

```bash
uv run python scripts/strava_generator.py
```

Distribution: 75% of employees with a declared sport have ≥15 activities (eligible for
wellness days), 25% have <15 activities (not eligible).

## Docker image

The Python image is automatically built and pushed to `ghcr.io` via GitHub Actions
whenever `docker/Dockerfile` or `pyproject.toml` is modified.

To trigger a manual rebuild: GitHub → Actions → **Build and push Docker image** → **Run workflow**.

The image contains only Python dependencies — scripts are loaded at runtime from
the GitHub repository via Kestra's namespace file sync.

At the start of each flow,scripts are synchronized from GitHub (`SyncNamespaceFiles`),
the image is pulled once via a dedicated `pull_image` task (`pullPolicy: ALWAYS`),
then reused from local cache for all subsequent tasks (`pullPolicy: IF_NOT_PRESENT`).

The image is built and pushed to `ghcr.io/<github-username>/avantages-sportifs:latest`.
Update the image tag in Kestra flows to match your GitHub username.

## Environment variables

`.env` and `docker-compose.yml` both contain sensitive credentials and are excluded
from version control. A `docker-compose.yml.example` with placeholder values is
provided as a template — copy it, fill in the secrets, and rename it to `docker-compose.yml`.

The following variables must be defined in `.env`:

```env
POSTGRES_USER=...
POSTGRES_PASSWORD=...
POSTGRES_DB=avantages_sportifs
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
```

These variables are used by Docker Compose to initialize PostgreSQL, and by
`strava_generator.py` which runs locally and connects directly to the database.

All other secrets (Google Maps API key, Slack bot token, channel ID, and API URL)
are managed as Kestra secrets in `docker-compose.yml` and passed to Python scripts
via the flow `env:` configuration. Each secret value must be base64-encoded:

```bash
echo -n "your_value" | base64
```

## Architecture notes

Kestra and business data share the same PostgreSQL database `avantages_sportifs`,
separated by schemas (`raw`, `clean`, `results`, `config`, `quality_report`).
In production, using two separate databases would be recommended.

Sport names and business parameters are managed in `config.sports` and `config.parameters`.
Adding a new sport or changing a parameter requires no code change — simply update the
relevant CSV file and drop it into `/params/`.
