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
    company_description: str = Field(default="", alias="COMPANY_DESCRIPTION")
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
    enable_firecrawl_fallback: bool = Field(default=False, alias="ENABLE_FIRECRAWL_FALLBACK")
    apify_api_key: str = Field(default="", alias="APIFY_API_KEY")
    jina_reader_base_url: str = Field(default="https://r.jina.ai", alias="JINA_READER_BASE_URL")
    enable_searxng: bool = Field(default=False, alias="ENABLE_SEARXNG")
    searxng_base_url: str = Field(default="http://searxng:8080", alias="SEARXNG_BASE_URL")

    # ── VK Direct (альтернатива Apify) ────────────────
    vk_access_token: str = Field(default="", alias="VK_ACCESS_TOKEN")

    # ── Instagram Direct (Instaloader) ────────────────
    enable_instagram: bool = Field(default=False, alias="ENABLE_INSTAGRAM")
    instagram_targets: str = Field(default="", alias="INSTAGRAM_TARGETS")
    instagram_max_posts: int = Field(default=5, alias="INSTAGRAM_MAX_POSTS")
    instagram_max_comments: int = Field(default=20, alias="INSTAGRAM_MAX_COMMENTS")
    instagram_session_username: str = Field(default="", alias="INSTAGRAM_SESSION_USERNAME")
    instagram_session_file: str = Field(default="", alias="INSTAGRAM_SESSION_FILE")

    # ── Apify Actors ───────────────────────────
    # Comma-separated VK handles or URLs to monitor via VK posts scraper.
    # Example: "pogruzhenye.official,https://vk.com/company_page"
    vk_targets: str = Field(default="", alias="VK_TARGETS")

    # Yandex Maps organization ID (e.g. "1076439570" from the maps URL)
    yandex_maps_org_id: str = Field(default="", alias="YANDEX_MAPS_ORG_ID")

    # ── Filtering ──────────────────────────────
    # Discard articles published more than N days ago (prevents evergreen
    # articles from flooding alerts on every pipeline run).
    max_article_age_days: int = Field(default=7, alias="MAX_ARTICLE_AGE_DAYS")

    # How far back Tavily should search (days). Should be <= max_article_age_days.
    tavily_search_days: int = Field(default=1, alias="TAVILY_SEARCH_DAYS")

    # Which sentiments trigger a Telegram alert.
    # Allowed values: "all" | "negative_only" | "negative_neutral"
    alert_on_sentiment: str = Field(default="all", alias="ALERT_ON_SENTIMENT")

    # High-water mark: how many minutes before the previous run's timestamp
    # to re-check (overlap buffer). Protects against indexing delays.
    # Set to 0 to disable overlap (strict watermark).
    watermark_overlap_minutes: int = Field(default=60, alias="WATERMARK_OVERLAP_MINUTES")

    # Comma-separated list of source_name values that use content-hash change
    # detection instead of (or in addition to) watermark date filtering.
    # These sources have pages whose published_at never changes but content
    # grows over time (new comments, new reviews, discussion threads).
    dynamic_sources: str = Field(
        default="vk,yandex_maps",
        alias="DYNAMIC_SOURCES",
    )

    @property
    def dynamic_source_list(self) -> List[str]:
        return [s.strip() for s in self.dynamic_sources.split(",") if s.strip()]

    # ── Derived ────────────────────────────────
    @property
    def vk_target_list(self) -> List[str]:
        return [target.strip() for target in self.vk_targets.split(",") if target.strip()]

    @property
    def instagram_target_list(self) -> List[str]:
        return [target.strip() for target in self.instagram_targets.split(",") if target.strip()]

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
