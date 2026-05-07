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

## Architecture notes

Kestra and business data share the same PostgreSQL database `avantages_sportifs`,
separated by schemas (`raw`, `clean`, `resultats`, `config`, `quality_report`).
Each schema contains multiple tables and represents a distinct stage of the pipeline.
In production, using two separate databases would be recommended.
