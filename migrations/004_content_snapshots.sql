-- Migration 004: Content hash snapshots for dynamic sources
-- Tracks the last known content hash per URL to detect new comments/reviews
-- on pages that were published long ago (VK posts, Yandex Maps, forums).

CREATE TABLE IF NOT EXISTS content_snapshots (
    id              SERIAL PRIMARY KEY,
    url_hash        VARCHAR(64)   NOT NULL,
    url             VARCHAR(2048),
    source_name     VARCHAR(64)   NOT NULL,
    content_hash    VARCHAR(64)   NOT NULL,
    first_seen_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    last_checked_at TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    last_changed_at TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_content_snapshots_url_hash UNIQUE (url_hash)
);

CREATE INDEX IF NOT EXISTS ix_content_snapshots_url_hash
    ON content_snapshots (url_hash);

CREATE INDEX IF NOT EXISTS ix_content_snapshots_source_name
    ON content_snapshots (source_name);
