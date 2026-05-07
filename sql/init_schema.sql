-- Raw data: imported as-is from source files
CREATE SCHEMA IF NOT EXISTS raw;

-- Clean data: normalized and validated
CREATE SCHEMA IF NOT EXISTS clean;

-- Results: eligibility and financial calculations (read by Power BI)
CREATE SCHEMA IF NOT EXISTS results;

-- Config: business parameters (taux_prime, seuil_activites)
CREATE SCHEMA IF NOT EXISTS config;

-- Quality report: anomalies detected by Great Expectations
CREATE SCHEMA IF NOT EXISTS quality_report;