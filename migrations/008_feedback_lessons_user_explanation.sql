-- Migration 008: Add user_explanation column to feedback_lessons
-- Stores the free-text reason provided by the human operator via Telegram dialog.
-- NULL means operator did not respond within timeout (lesson extracted by LLM alone).

ALTER TABLE feedback_lessons
    ADD COLUMN IF NOT EXISTS user_explanation TEXT;

COMMENT ON COLUMN feedback_lessons.user_explanation IS
    'Free-text explanation from human operator (via Telegram feedback dialog). '
    'NULL = operator did not respond; lesson was auto-extracted by LLM.';
