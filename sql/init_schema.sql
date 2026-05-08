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
    commute_mode  TEXT
);

CREATE TABLE IF NOT EXISTS raw.sports (
    employee_id INTEGER PRIMARY KEY,
    sport       TEXT
);

CREATE TABLE IF NOT EXISTS raw.activities (
    activity_id     INTEGER PRIMARY KEY,
    employee_id     INTEGER,
    start_date      TIMESTAMP,
    sport_type      TEXT,
    distance_m      INTEGER,
    end_date        TIMESTAMP,
    comment         TEXT
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
    commute_validated   BOOLEAN 
);

CREATE TABLE IF NOT EXISTS clean.sports (
    employee_id INTEGER PRIMARY KEY,
    sport       TEXT
);

CREATE TABLE IF NOT EXISTS clean.activities (
    activity_id     INTEGER PRIMARY KEY,
    employee_id     INTEGER,
    start_date      TIMESTAMP,
    sport_type      TEXT,
    distance_m      INTEGER,
    end_date        TIMESTAMP,
    comment         TEXT,
    slack_notified  BOOLEAN DEFAULT FALSE
);


-- Config table
CREATE TABLE IF NOT EXISTS config.parameters (
    key         VARCHAR(100) PRIMARY KEY,
    value       VARCHAR(255) NOT NULL,
    description TEXT
);


-- Quality report table
CREATE TABLE IF NOT EXISTS quality_report.anomalies (
    id          SERIAL PRIMARY KEY,
    checked_at  TIMESTAMP DEFAULT NOW(),
    table_name  TEXT,
    test_name   TEXT,
    detail      TEXT
);