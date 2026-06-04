from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Runtime ────────────────────────────────
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    schedule_interval_minutes: int = Field(default=60, alias="SCHEDULE_INTERVAL_MINUTES")

    # ── Database ───────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://brandmon:brandmon@localhost:5432/brandmon",
        alias="DATABASE_URL",
    )

    # ── Telegram ───────────────────────────────
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_target_chat_id: str = Field(default="", alias="TELEGRAM_TARGET_CHAT_ID")

    # ── Brand ──────────────────────────────────
    company_name: str = Field(default="", alias="COMPANY_NAME")
    keywords: str = Field(default="", alias="KEYWORDS")
    search_phrases: str = Field(default="", alias="SEARCH_PHRASES")
    monitoring_location: str = Field(default="Москва", alias="MONITORING_LOCATION")
    monitoring_languages: str = Field(default="ru", alias="MONITORING_LANGUAGES")

    # ── LLM ────────────────────────────────────
    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_endpoint: str = Field(default="https://api.openai.com/v1", alias="LLM_ENDPOINT")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")

    # ── Tools ──────────────────────────────────
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")
    firecrawl_api_key: str = Field(default="", alias="FIRECRAWL_API_KEY")
    apify_api_key: str = Field(default="", alias="APIFY_API_KEY")

    # ── Apify Actors ───────────────────────────
    # Yandex Maps organization ID (e.g. "1076439570" from the maps URL)
    yandex_maps_org_id: str = Field(default="", alias="YANDEX_MAPS_ORG_ID")

    # ── Derived ────────────────────────────────
    @property
    def keyword_list(self) -> List[str]:
        if not self.keywords.strip():
            return []
        return [k.strip() for k in self.keywords.split(",") if k.strip()]

    @property
    def search_phrase_list(self) -> List[str]:
        if not self.search_phrases.strip():
            return []
        return [p.strip() for p in self.search_phrases.split(",") if p.strip()]

    @property
    def language_list(self) -> List[str]:
        return [lang.strip() for lang in self.monitoring_languages.split(",") if lang.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
