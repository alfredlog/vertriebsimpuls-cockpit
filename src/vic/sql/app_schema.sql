-- Application data that must survive the daily full refresh of schema vic
-- (which is dropped and recreated). Never dropped by load_all.
-- No foreign keys into vic: those rows disappear on every reload.
CREATE SCHEMA IF NOT EXISTS vic_app;

-- impulse_id is derived from run_date, client and product, so a rerun of the
-- same day produces the same ids and existing feedback stays linked.
CREATE TABLE IF NOT EXISTS vic_app.daily_impulses (
    impulse_id VARCHAR PRIMARY KEY,
    run_date DATE NOT NULL,
    sales_rep_id VARCHAR NOT NULL,
    client_id VARCHAR NOT NULL,
    product_id VARCHAR,
    signal_type VARCHAR NOT NULL,
    score DOUBLE NOT NULL,
    rank INTEGER NOT NULL CHECK (rank >= 1),
    reason_de VARCHAR NOT NULL,
    UNIQUE (run_date, sales_rep_id, rank)
);

-- Several entries per impulse are allowed; the latest ts is the current status.
CREATE TABLE IF NOT EXISTS vic_app.impulse_feedback (
    impulse_id VARCHAR NOT NULL,
    run_date DATE NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('angenommen', 'abgelehnt')),
    ts TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS vic_app.quality_results (
    run_ts TIMESTAMP NOT NULL,
    run_date DATE NOT NULL,
    dataset VARCHAR NOT NULL,
    check_name VARCHAR NOT NULL,
    passed BOOLEAN NOT NULL,
    failures INTEGER NOT NULL
);
