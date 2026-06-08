-- Migration 006: Human feedback — ignore button support
-- is_ignored: marks a mention as manually dismissed by operator
-- ignored_urls: permanent URL blocklist (never process again)

ALTER TABLE mentions
    ADD COLUMN IF NOT EXISTS is_ignored BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS ignored_urls (
    id          SERIAL PRIMARY KEY,
    url_hash    VARCHAR(64)   NOT NULL,
    url         VARCHAR(2048),
    mention_id  VARCHAR(36),
    ignored_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ignored_urls_url_hash UNIQUE (url_hash)
);

CREATE INDEX IF NOT EXISTS ix_ignored_urls_url_hash
    ON ignored_urls (url_hash);
