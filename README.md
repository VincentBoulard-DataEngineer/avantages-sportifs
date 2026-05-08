# Avantages Sportifs — POC

Data pipeline for managing employee sports benefits.

## Prerequisites

Create the runtime directories before the first launch:

```bash
mkdir inbox archive
```

## Start

```bash
docker-compose up -d
```

## Stack

- PostgreSQL 15
- Kestra (latest)
- Python / Pandas / Great Expectations
- Power BI

## How it works

Drop a file into the `/inbox/` folder. Kestra detects it automatically and triggers
the appropriate treatment based on the filename. Processed files are moved to `/archive/`
with a timestamp prefix (e.g. `20260508101950_donnees_rh.xlsx`).

| File                     | Treatment                                |
| ------------------------ | ---------------------------------------- |
| `donnees_rh.xlsx`        | HR data ingestion + cleaning             |
| `donnees_sportives.xlsx` | Sports declarations ingestion + cleaning |

## Google Maps commute validation

When `donnees_rh.xlsx` is processed, the pipeline validates each employee's commute
declaration against their home address using the Google Maps Routes API.

| Commute mode              | Travel mode | Max distance |
| ------------------------- | ----------- | ------------ |
| `marche/running`          | WALK        | 15 km        |
| `vélo/trottinette/autres` | BICYCLE     | 25 km        |

Non-eligible modes (`véhicule thermique/électrique`, `transports en commun`) are
automatically excluded from the sports bonus. Anomalies are logged in
`quality_report.anomalies`.

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
