-- Migration 003: High-water mark checkpoints per source
-- Stores the last successful run timestamp for each data source.
-- Used to filter out already-seen content in subsequent pipeline runs.

CREATE TABLE IF NOT EXISTS run_checkpoints (
    id           SERIAL PRIMARY KEY,
    source_name  VARCHAR(64)  NOT NULL,
    last_run_at  TIMESTAMPTZ  NOT NULL,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_run_checkpoints_source UNIQUE (source_name)
);

CREATE INDEX IF NOT EXISTS ix_run_checkpoints_source_name
    ON run_checkpoints (source_name);
