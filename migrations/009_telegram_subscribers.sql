-- Migration 009: Telegram /start subscriptions
-- Stores chats that pressed /start so alerts can be delivered without manual chat_id setup.

CREATE TABLE IF NOT EXISTS telegram_subscribers (
    id          SERIAL PRIMARY KEY,
    chat_id     VARCHAR(64)  NOT NULL,
    chat_type   VARCHAR(32),
    title       VARCHAR(255),
    username    VARCHAR(255),
    first_name  VARCHAR(255),
    last_name   VARCHAR(255),
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_telegram_subscribers_chat_id UNIQUE (chat_id)
);

CREATE INDEX IF NOT EXISTS ix_telegram_subscribers_chat_id
    ON telegram_subscribers (chat_id);

CREATE INDEX IF NOT EXISTS ix_telegram_subscribers_is_active
    ON telegram_subscribers (is_active);
