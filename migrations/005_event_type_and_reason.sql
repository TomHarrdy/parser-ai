-- Migration 005: Add event_type and ai_reason to mentions
-- event_type: LLM-classified content type (article, review, comment, post, forum, mention, unknown)
-- ai_reason:  short reason for sentiment (e.g. "slow service", "friendly staff")

DO $$ BEGIN
    CREATE TYPE eventtype AS ENUM (
        'article', 'review', 'comment', 'post', 'forum', 'mention', 'unknown'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE mentions
    ADD COLUMN IF NOT EXISTS event_type eventtype,
    ADD COLUMN IF NOT EXISTS ai_reason  TEXT;
