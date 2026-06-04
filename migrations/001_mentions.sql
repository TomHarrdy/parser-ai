-- migrations/001_mentions.sql
-- Auto-run by PostgreSQL Docker entrypoint on first init.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TYPE source_type AS ENUM ('web', 'news', 'vk', 'yandex_maps', 'telegram', 'other');
CREATE TYPE sentiment AS ENUM ('positive', 'negative', 'neutral');

CREATE TABLE IF NOT EXISTS mentions (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    url           VARCHAR(2048),
    url_hash      VARCHAR(64) NOT NULL UNIQUE,
    source_type   source_type NOT NULL,
    raw_text      TEXT NOT NULL,
    raw_text_hash VARCHAR(64) NOT NULL,
    ai_summary    TEXT,
    sentiment     sentiment,
    is_alert_sent BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_mentions_url_hash ON mentions (url_hash);
CREATE INDEX idx_mentions_text_hash ON mentions (raw_text_hash);
CREATE INDEX idx_mentions_created_at ON mentions (created_at);
CREATE INDEX idx_mentions_sentiment ON mentions (sentiment);
