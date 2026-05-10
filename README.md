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

Before running the pipeline, load the reference data by dropping both files into `/params/`:

- `data/params.csv` — business parameters (bonus rate, activity threshold)
- `data/sports.csv` — sports reference data with physical constraints

## How it works

Drop a file into the `/inbox/` folder. Kestra detects it automatically and triggers
the appropriate treatment based on the filename. Processed files are moved to `/archive/inbox/`
with a timestamp prefix (e.g. `20260508101950_donnees_rh.xlsx`).

| File                     | Treatment                                                                       |
| ------------------------ | ------------------------------------------------------------------------------- |
| `donnees_rh.xlsx`        | HR data ingestion + raw quality tests + cleaning + Google Maps validation       |
| `donnees_sportives.xlsx` | Sports declarations ingestion + raw quality tests + cleaning                    |
| `activites_init.csv`     | Activities ingestion + raw quality tests + cleaning, no Slack notification      |
| `activites.csv`          | Activities ingestion + raw quality tests + cleaning, Slack notification pending |

## Pipeline steps

For each file dropped in `/inbox/`, Kestra executes the following sequence:

| Step | Script                   | Description                                             |
| ---- | ------------------------ | ------------------------------------------------------- |
| 1    | `ingestion.py`           | Create batch, load raw data into `raw.*` schema         |
| 2    | `quality_tests_raw.py`   | Run Great Expectations checks on the ingested raw table |
| 3    | `cleaning.py`            | Normalize and load into `clean.*` schema                |
| 2    | `quality_tests_clean.py` | Run Great Expectations checks on the cleaned data       |
| 4    | `google_maps.py`         | Validate commute distances (HR file only)               |

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

| File                                   | Table tested     | Checks                                                                                        |
| -------------------------------------- | ---------------- | --------------------------------------------------------------------------------------------- |
| `donnees_rh.xlsx`                      | `raw.employees`  | Mandatory fields not null, `gross_salary > 0`, `employee_id` unique                           |
| `donnees_sportives.xlsx`               | `raw.sports`     | `employee_id` not null, `employee_id` unique                                                  |
| `activites.csv` / `activites_init.csv` | `raw.activities` | Mandatory fields not null, `distance_m >= 0`, `activity_id` unique, `sport_type` in valid set |

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

Business parameters and sports reference data are managed separately via the `/params/` folder.
Dropping a file into `/params/` triggers `flow_params`, which updates the config tables
and will trigger a full recalculation once `calculs.py` is implemented.

| File         | Table updated       | Description                            |
| ------------ | ------------------- | -------------------------------------- |
| `params.csv` | `config.parameters` | Business parameters (bonus rate, etc.) |
| `sports.csv` | `config.sports`     | Sports reference data with constraints |

Processed files are archived to `/archive/params/`.

To update a parameter (e.g. change the bonus rate), edit `data/params.csv` and drop it
into `/params/`. No code change or Docker rebuild required.

## Google Maps commute validation

When `donnees_rh.xlsx` is processed, the pipeline validates each employee's commute
declaration against their home address using the Google Maps Routes API.
Only employees from the current batch are processed.

| Commute mode              | Travel mode | Max distance |
| ------------------------- | ----------- | ------------ |
| `marche/running`          | WALK        | 15 km        |
| `vélo/trottinette/autres` | BICYCLE     | 25 km        |

Non-eligible modes (`véhicule thermique/électrique`, `transports en commun`) are
automatically excluded from the sports bonus. Anomalies are logged in
`quality_report.anomalies`.

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

The Python image `avantages_sportifs_python` must be built locally before running the pipeline:

```bash
docker build -t avantages_sportifs_python -f docker/Dockerfile .
```

Rebuild the image whenever scripts are modified.

## Environment variables

The following variables must be defined in `.env`:

```env
POSTGRES_USER=...
POSTGRES_PASSWORD=...
POSTGRES_DB=avantages_sportifs
POSTGRES_PORT=5432
POSTGRES_HOST=localhost
GOOGLE_MAPS_API_KEY=...
```

## Architecture notes

Kestra and business data share the same PostgreSQL database `avantages_sportifs`,
separated by schemas (`raw`, `clean`, `resultats`, `config`, `quality_report`).
In production, using two separate databases would be recommended.

Sport names and business parameters are managed in `config.sports` and `config.parameters`.
Adding a new sport or changing a parameter requires no code change — simply update the
relevant CSV file and drop it into `/params/`.
