-- Migration 010: Freshness archives and comment watchlist
-- Separates old/stale publication blocking from fresh comment monitoring.

ALTER TABLE mentions
    ADD COLUMN IF NOT EXISTS activity_published_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS activity_type VARCHAR(32) DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS freshness_status VARCHAR(32) DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_mentions_activity_published_at
    ON mentions (activity_published_at)
    WHERE activity_published_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_mentions_freshness_status
    ON mentions (freshness_status)
    WHERE freshness_status IS NOT NULL;

CREATE TABLE IF NOT EXISTS stale_urls (
    id                    SERIAL PRIMARY KEY,
    url_hash              VARCHAR(64)   NOT NULL,
    url                   VARCHAR(2048),
    mention_id            VARCHAR(36),
    reason                VARCHAR(64)   NOT NULL DEFAULT 'old_publication',
    source_published_at   TIMESTAMPTZ,
    first_seen_at         TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    marked_stale_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    last_checked_at       TIMESTAMPTZ,
    can_recheck_comments  BOOLEAN       NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_stale_urls_url_hash UNIQUE (url_hash)
);

CREATE INDEX IF NOT EXISTS ix_stale_urls_url_hash
    ON stale_urls (url_hash);

CREATE INDEX IF NOT EXISTS ix_stale_urls_reason
    ON stale_urls (reason);

CREATE TABLE IF NOT EXISTS comment_watchlist (
    id                       SERIAL PRIMARY KEY,
    url_hash                 VARCHAR(64)   NOT NULL,
    url                      VARCHAR(2048) NOT NULL,
    source_name              VARCHAR(64)   NOT NULL DEFAULT 'comment_watchlist',
    page_title               VARCHAR(255),
    main_published_at        TIMESTAMPTZ,
    last_comment_at          TIMESTAMPTZ,
    last_comment_hash        VARCHAR(64),
    comments_snapshot_hash   VARCHAR(64),
    check_interval_minutes   INTEGER       NOT NULL DEFAULT 60,
    is_active                BOOLEAN       NOT NULL DEFAULT TRUE,
    failure_count            INTEGER       NOT NULL DEFAULT 0,
    first_seen_at            TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    last_checked_at          TIMESTAMPTZ,
    last_changed_at          TIMESTAMPTZ,
    CONSTRAINT uq_comment_watchlist_url_hash UNIQUE (url_hash)
);

CREATE INDEX IF NOT EXISTS ix_comment_watchlist_url_hash
    ON comment_watchlist (url_hash);

CREATE INDEX IF NOT EXISTS ix_comment_watchlist_is_active
    ON comment_watchlist (is_active);

CREATE INDEX IF NOT EXISTS ix_comment_watchlist_last_checked_at
    ON comment_watchlist (last_checked_at);
