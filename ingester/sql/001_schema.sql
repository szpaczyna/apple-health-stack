-- Apple Health PostgreSQL Schema
-- Run this against the health database to create the tables in the public schema.

-- Main table for all health records (replaces all InfluxDB measurements)
-- Each row corresponds to one Apple Health record or workout entry.
CREATE TABLE IF NOT EXISTS records (
    id          BIGSERIAL PRIMARY KEY,
    measurement VARCHAR(128) NOT NULL,   -- e.g. "BodyMass", "StepCount", "HeartRate", "Walking"
    time        TIMESTAMPTZ NOT NULL,
    value       DOUBLE PRECISION,
    unit        VARCHAR(64),
    device      VARCHAR(256)
);

-- Sleep analysis needs start/stop times and state info
CREATE TABLE IF NOT EXISTS sleep_analysis (
    id          BIGSERIAL PRIMARY KEY,
    time        TIMESTAMPTZ NOT NULL,
    device      VARCHAR(256),
    state       VARCHAR(32),             -- Deep, Core, REM, Asleep, Awake
    state_value INTEGER NOT NULL,        -- numeric representation (0=Deep, 1=Core, 2=REM, 3=Asleep, 4=Awake)
    start_time  TIMESTAMPTZ NOT NULL,
    end_time    TIMESTAMPTZ NOT NULL
);

-- Workout routes (GPX track points)
CREATE TABLE IF NOT EXISTS workout_routes (
    id          BIGSERIAL PRIMARY KEY,
    workout     VARCHAR(256) NOT NULL,
    time        TIMESTAMPTZ NOT NULL,
    latitude    DOUBLE PRECISION,
    longitude   DOUBLE PRECISION,
    elevation   DOUBLE PRECISION,
    speed       DOUBLE PRECISION,
    distance    DOUBLE PRECISION
);

-- Track which data sources have been ingested
CREATE TABLE IF NOT EXISTS data_sources (
    device      VARCHAR(256) PRIMARY KEY
);

-- Indexes optimized for Grafana time-series queries
-- The ingester drops every "CREATE INDEX IF NOT EXISTS" index below before the
-- bulk load and re-runs this file afterwards to rebuild them. Indexes needed
-- during the load (e.g. for ON CONFLICT) must be UNIQUE / PRIMARY KEY.
CREATE INDEX IF NOT EXISTS idx_records_measurement_time
    ON records (measurement, time DESC);

CREATE INDEX IF NOT EXISTS idx_records_time
    ON records (time DESC);

CREATE INDEX IF NOT EXISTS idx_records_device
    ON records (device);

CREATE INDEX IF NOT EXISTS idx_records_measurement_device_time
    ON records (measurement, device, time DESC);

CREATE INDEX IF NOT EXISTS idx_sleep_time
    ON sleep_analysis (time DESC);

CREATE INDEX IF NOT EXISTS idx_sleep_device_time
    ON sleep_analysis (device, time DESC);

CREATE INDEX IF NOT EXISTS idx_routes_workout_time
    ON workout_routes (workout, time DESC);
