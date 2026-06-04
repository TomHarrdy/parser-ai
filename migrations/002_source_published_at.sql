-- migrations/002_source_published_at.sql
-- Add publication date field to track when the original article was published.
-- This allows filtering out evergreen/old articles on each pipeline run.

ALTER TABLE mentions
    ADD COLUMN IF NOT EXISTS source_published_at TIMESTAMPTZ DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_mentions_source_published_at
    ON mentions (source_published_at)
    WHERE source_published_at IS NOT NULL;
