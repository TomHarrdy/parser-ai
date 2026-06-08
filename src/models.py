import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SourceType(str, Enum):
    web = "web"
    news = "news"
    vk = "vk"
    yandex_maps = "yandex_maps"
    telegram = "telegram"
    other = "other"


class EventType(str, Enum):
    """Type of content as classified by the LLM."""
    article = "article"        # Статья / новость
    review = "review"          # Отзыв (Яндекс.Карты, 2ГИС, сайт-агрегатор)
    comment = "comment"        # Комментарий под статьёй / постом
    post = "post"              # Пост в соцсети (VK, Telegram-канал)
    forum = "forum"            # Форумное обсуждение / ветка
    mention = "mention"        # Общее упоминание (не подходит под другие типы)
    unknown = "unknown"        # LLM не смог определить


class Sentiment(str, Enum):
    positive = "positive"
    negative = "negative"
    neutral = "neutral"


class Mention(Base):
    __tablename__ = "mentions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=True)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    source_type: Mapped[SourceType] = mapped_column(SAEnum(SourceType), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_text_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Reason for the sentiment (e.g. "slow service", "friendly staff")
    ai_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Type of content as classified by LLM (article, review, comment, post, forum, mention)
    event_type: Mapped[EventType | None] = mapped_column(SAEnum(EventType), nullable=True)
    sentiment: Mapped[Sentiment | None] = mapped_column(SAEnum(Sentiment), nullable=True)
    is_alert_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Set to True when a human clicks "Not relevant" on the Telegram alert.
    # Ignored mentions are excluded from future dedup checks, digests, and alerts.
    # Their URL is also added to the permanent blocklist (ignored_urls table).
    is_ignored: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Publication date from the original source (e.g. Tavily's published_date).
    # NULL means the source didn't provide a date (we don't filter these out,
    # but do log a warning so the operator knows filtering wasn't applied).
    source_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Actual fresh activity date used for alerting. Usually equals
    # source_published_at, but for old pages with new comments it is the
    # latest comment/review date.
    activity_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    activity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    freshness_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Mention {self.id} [{self.sentiment}] {self.url}>"


class FeedbackLesson(Base):
    """A lesson learned from human feedback (operator clicked 'Not relevant').

    When an operator dismisses a mention, the agent asks the LLM:
    "Why was this a false positive? What rule should I follow to avoid this mistake?"

    The resulting lesson is stored here and injected into the system prompt
    of every future LLM analysis call — making the agent progressively smarter.

    Example lessons:
      - "Не считать релевантными упоминания компании 'Погружение' из Санкт-Петербурга"
      - "Слово 'погружение' в контексте дайвинга/медитации — не наша компания"
      - "Обзоры квестов-конкурентов не являются упоминаниями нашего бренда"
    """
    __tablename__ = "feedback_lessons"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # The dismissed mention
    mention_id: Mapped[str] = mapped_column(String(36), nullable=True, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=True)
    source_name: Mapped[str] = mapped_column(String(64), nullable=True)
    # Truncated original text sent to LLM (max 500 chars for context)
    mention_snippet: Mapped[str] = mapped_column(Text, nullable=True)
    # Error category classified by LLM
    # e.g. "wrong_location", "different_company", "keyword_coincidence", "unrelated_topic"
    error_category: Mapped[str] = mapped_column(String(64), nullable=True)
    # Short concrete rule in Russian (injected into system prompt)
    lesson_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional free-text explanation from the human operator (collected via Telegram dialog)
    # NULL means operator did not provide explanation (lesson extracted by LLM from text alone)
    user_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Whether this lesson is still active (can be disabled manually)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<FeedbackLesson id={self.id} category={self.error_category!r}>"


class IgnoredUrl(Base):
    """Permanent URL blocklist populated when a human clicks 'Not relevant'.

    On every future pipeline run, any mention whose URL hash is in this table
    is silently skipped before LLM analysis — saving API costs and preventing
    repeat false-positive alerts.
    """
    __tablename__ = "ignored_urls"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=True)
    # ID of the mention that triggered the ignore action
    mention_id: Mapped[str] = mapped_column(String(36), nullable=True)
    ignored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<IgnoredUrl url_hash={self.url_hash[:8]}…>"


class StaleUrl(Base):
    """Archive of URLs marked as old/outdated.

    Unlike ignored_urls, this does not mean the page is useless forever:
    an old article can still be monitored for fresh comments.
    """
    __tablename__ = "stale_urls"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=True)
    mention_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    reason: Mapped[str] = mapped_column(String(64), nullable=False, default="old_publication")
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    marked_stale_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    can_recheck_comments: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:
        return f"<StaleUrl url_hash={self.url_hash[:8]}… reason={self.reason!r}>"


class CommentWatchlist(Base):
    """Pages whose old publication can still receive fresh comments/reviews."""
    __tablename__ = "comment_watchlist"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False, default="comment_watchlist")
    page_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    main_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_comment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_comment_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comments_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    check_interval_minutes: Mapped[int] = mapped_column(default=60, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    failure_count: Mapped[int] = mapped_column(default=0, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<CommentWatchlist url_hash={self.url_hash[:8]}… active={self.is_active}>"


class TelegramSubscriber(Base):
    """Telegram chat subscribed to alerts for this single-company bot."""
    __tablename__ = "telegram_subscribers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    chat_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<TelegramSubscriber chat_id={self.chat_id!r} active={self.is_active}>"


class ContentSnapshot(Base):
    """Tracks the last known content hash for a URL.

    Used for dynamic sources (VK, Yandex Maps, forums) where the publication
    date never changes but new comments/reviews accumulate over time.

    Logic:
      - First visit: no snapshot → treat as new → save snapshot
      - Revisit, same hash: content unchanged → skip
      - Revisit, different hash: new comments detected → process as new mention → update snapshot
    """
    __tablename__ = "content_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=True)
    # Source that owns this URL (e.g. "vk", "yandex_maps")
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # SHA-256 of the full text seen on last successful processing
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<ContentSnapshot url_hash={self.url_hash[:8]}… source={self.source_name!r}>"


class RunCheckpoint(Base):
    """Stores the last successful run timestamp per source.

    Used as a "high-water mark": next pipeline run only processes
    mentions with published_at > (last_run_at - overlap_buffer).
    This prevents re-processing old content while tolerating indexing delays.
    """
    __tablename__ = "run_checkpoints"
    __table_args__ = (UniqueConstraint("source_name", name="uq_run_checkpoints_source"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # e.g. "tavily", "tavily_phrase", "vk", "yandex_maps"
    source_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # The wall-clock time when the last successful pipeline run completed for this source
    last_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<RunCheckpoint source={self.source_name!r} last_run_at={self.last_run_at}>"
