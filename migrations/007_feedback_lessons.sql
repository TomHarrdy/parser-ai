-- Migration 007: Agent self-learning from human feedback
-- Stores lessons extracted from dismissed mentions;
-- injected into LLM system prompt to prevent repeat false positives.

CREATE TABLE IF NOT EXISTS feedback_lessons (
    id               SERIAL PRIMARY KEY,
    mention_id       VARCHAR(36),
    url              VARCHAR(2048),
    source_name      VARCHAR(64),
    mention_snippet  TEXT,
    error_category   VARCHAR(64),
    lesson_text      TEXT        NOT NULL,
    is_active        BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_feedback_lessons_is_active
    ON feedback_lessons (is_active);
