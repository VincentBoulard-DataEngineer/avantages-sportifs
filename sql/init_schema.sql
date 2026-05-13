-- Raw data: imported as-is from source files
CREATE SCHEMA IF NOT EXISTS raw;

-- Clean data: normalized and validated
CREATE SCHEMA IF NOT EXISTS clean;

-- Results: eligibility and financial calculations
CREATE SCHEMA IF NOT EXISTS results;

-- Config: business parameters
CREATE SCHEMA IF NOT EXISTS config;

-- Quality report: anomalies detected by Great Expectations
CREATE SCHEMA IF NOT EXISTS quality_report;


-- Config tables first (referenced by raw and clean tables)
CREATE TABLE IF NOT EXISTS config.parameters (
    key         VARCHAR(100) PRIMARY KEY,
    value       VARCHAR(255) NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS config.batches (
    batch_id   SERIAL PRIMARY KEY,
    filename   TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT NOW(),
    status     TEXT DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS config.sports (
    sport            TEXT PRIMARY KEY,
    max_speed_kmh    NUMERIC,
    min_duration_min INTEGER NOT NULL,
    has_distance     BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS config.commute_modes (
    mode        TEXT PRIMARY KEY,
    threshold_m INTEGER NOT NULL,
    travel_mode TEXT NOT NULL
);


-- Raw tables
CREATE TABLE IF NOT EXISTS raw.employees (
    employee_id   INTEGER PRIMARY KEY,
    last_name     TEXT,
    first_name    TEXT,
    birth_date    FLOAT,
    bu            TEXT,
    hire_date     FLOAT,
    gross_salary  FLOAT,
    contract_type TEXT,
    vacation_days FLOAT,
    home_address  TEXT,
    commute_mode  TEXT,
    ingested_at   TIMESTAMP DEFAULT NOW(),
    batch_id      INTEGER REFERENCES config.batches(batch_id)
);

CREATE TABLE IF NOT EXISTS raw.sports (
    employee_id INTEGER PRIMARY KEY,
    sport       TEXT,
    ingested_at TIMESTAMP DEFAULT NOW(),
    batch_id    INTEGER REFERENCES config.batches(batch_id)
);

CREATE TABLE IF NOT EXISTS raw.activities (
    activity_id     BIGINT PRIMARY KEY,
    employee_id     INTEGER,
    start_date      TIMESTAMP,
    sport_type      TEXT,
    distance_m      INTEGER,
    end_date        TIMESTAMP,
    comment         TEXT,
    ingested_at     TIMESTAMP DEFAULT NOW(),
    batch_id        INTEGER REFERENCES config.batches(batch_id)
);


-- Clean tables
CREATE TABLE IF NOT EXISTS clean.employees (
    employee_id         INTEGER PRIMARY KEY,
    last_name           TEXT,
    first_name          TEXT,
    birth_date          DATE,
    bu                  TEXT,
    hire_date           DATE,
    gross_salary        NUMERIC,
    contract_type       TEXT,
    vacation_days       INTEGER,
    home_address        TEXT,
    commute_mode        TEXT,
    commute_distance_m  INTEGER,
    commute_validated   BOOLEAN,
    cleaned_at          TIMESTAMP DEFAULT NOW(),
    batch_id            INTEGER REFERENCES config.batches(batch_id)
);

CREATE TABLE IF NOT EXISTS clean.sports (
    employee_id INTEGER PRIMARY KEY,
    sport       TEXT,
    cleaned_at  TIMESTAMP DEFAULT NOW(),
    batch_id    INTEGER REFERENCES config.batches(batch_id)
);

CREATE TABLE IF NOT EXISTS clean.activities (
    activity_id     BIGINT PRIMARY KEY,
    employee_id     INTEGER,
    start_date      TIMESTAMP,
    sport_type      TEXT,
    distance_m      INTEGER,
    end_date        TIMESTAMP,
    comment         TEXT,
    slack_notified  BOOLEAN DEFAULT FALSE,
    cleaned_at      TIMESTAMP DEFAULT NOW(),
    batch_id        INTEGER REFERENCES config.batches(batch_id)
);


-- Quality report table
CREATE TABLE IF NOT EXISTS quality_report.anomalies (
    id          SERIAL PRIMARY KEY,
    checked_at  TIMESTAMP DEFAULT NOW(),
    stage       TEXT,
    table_name  TEXT,
    test_name   TEXT,
    detail      TEXT,
    employee_id INTEGER,
    activity_id BIGINT
);


-- Results table
CREATE TABLE IF NOT EXISTS results.eligibility (
    employee_id       INTEGER PRIMARY KEY,
    last_name         TEXT,
    first_name        TEXT,
    bu                TEXT,
    gross_salary      NUMERIC,
    commute_mode      TEXT,
    commute_validated BOOLEAN,
    eligible_prime    BOOLEAN,
    prime_amount      NUMERIC,
    activity_count    INTEGER,
    eligible_wellness BOOLEAN,
    calculated_at     TIMESTAMP DEFAULT NOW()
);