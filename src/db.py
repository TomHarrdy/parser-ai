import hashlib
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.models import (
    Base,
    CommentWatchlist,
    ContentSnapshot,
    FeedbackLesson,
    IgnoredUrl,
    Mention,
    RunCheckpoint,
    StaleUrl,
    TelegramSubscriber,
)
from src.settings import Settings


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


engine = None
session_factory: async_sessionmaker[AsyncSession] | None = None


async def init_db(settings: Settings) -> None:
    global engine, session_factory
    engine = create_async_engine(settings.database_url, echo=False, pool_size=5)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if session_factory is None:
        raise RuntimeError("DB not initialized. Call init_db() first.")
    async with session_factory() as session:
        yield session


async def mention_exists_by_url(session: AsyncSession, url: str) -> bool:
    """Check if a mention with this URL hash already exists."""
    h = _hash(url)
    result = await session.execute(
        text("SELECT 1 FROM mentions WHERE url_hash = :h LIMIT 1"), {"h": h}
    )
    return result.scalar() is not None


async def mention_exists_by_text_hash(session: AsyncSession, raw_text: str) -> bool:
    """Check if a mention with identical text hash already exists (catches reprints)."""
    h = _hash(raw_text)
    result = await session.execute(
        text("SELECT 1 FROM mentions WHERE raw_text_hash = :h LIMIT 1"), {"h": h}
    )
    return result.scalar() is not None


def url_hash(url: str) -> str:
    return _hash(url)


def text_hash(text: str) -> str:
    return _hash(text)


# ── Agent self-learning: feedback lessons ────────────────

async def save_feedback_lesson(
    session: AsyncSession,
    mention_id: str,
    url: Optional[str],
    source_name: Optional[str],
    mention_snippet: Optional[str],
    error_category: str,
    lesson_text: str,
    user_explanation: Optional[str] = None,
) -> FeedbackLesson:
    """Persist a lesson learned from a human-dismissed mention."""
    lesson = FeedbackLesson(
        mention_id=mention_id,
        url=url,
        source_name=source_name,
        mention_snippet=mention_snippet,
        error_category=error_category,
        lesson_text=lesson_text,
        user_explanation=user_explanation,
    )
    session.add(lesson)
    await session.commit()
    return lesson


async def get_active_lessons(session: AsyncSession, limit: int = 30) -> list[FeedbackLesson]:
    """Fetch the most recent active lessons to inject into the system prompt."""
    result = await session.execute(
        select(FeedbackLesson)
        .where(FeedbackLesson.is_active.is_(True))
        .order_by(FeedbackLesson.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


# ── Human feedback: ignored URLs blocklist ───────────────

async def is_url_ignored(session: AsyncSession, url: str) -> bool:
    """Return True if the URL was previously dismissed by a human operator."""
    uh = _hash(url)
    result = await session.execute(
        select(IgnoredUrl).where(IgnoredUrl.url_hash == uh)
    )
    return result.scalar_one_or_none() is not None


async def mark_mention_ignored(
    session: AsyncSession,
    mention_id: str,
) -> Optional[Mention]:
    """Mark a Mention as ignored and add its URL to the permanent blocklist.

    Returns the updated Mention object, or None if not found.
    Called from the Telegram callback handler when a user clicks 'Not relevant'.
    """
    from uuid import UUID
    try:
        uid = UUID(mention_id)
    except ValueError:
        return None

    result = await session.execute(
        select(Mention).where(Mention.id == uid)
    )
    mention = result.scalar_one_or_none()
    if mention is None:
        return None

    # Mark the mention itself
    mention.is_ignored = True
    session.add(mention)

    # Add URL to permanent blocklist (ignore duplicate constraint)
    if mention.url:
        uh = _hash(mention.url)
        stmt = (
            pg_insert(IgnoredUrl)
            .values(
                url_hash=uh,
                url=mention.url,
                mention_id=str(mention.id),
                ignored_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(index_elements=["url_hash"])
        )
        await session.execute(stmt)

    await session.commit()
    return mention


async def remove_url_ignore(session: AsyncSession, url: str) -> None:
    """Remove a URL from the permanent ignore blocklist."""
    await session.execute(delete(IgnoredUrl).where(IgnoredUrl.url_hash == _hash(url)))
    await session.commit()


# ── Freshness archive: stale URLs and comment watchlist ─────────────────────

async def is_url_stale(session: AsyncSession, url: str) -> bool:
    """Return True if this URL is archived as an old/stale publication."""
    uh = _hash(url)
    result = await session.execute(select(StaleUrl).where(StaleUrl.url_hash == uh))
    return result.scalar_one_or_none() is not None


async def save_stale_url(
    session: AsyncSession,
    url: str,
    mention_id: Optional[str] = None,
    reason: str = "old_publication",
    source_published_at: Optional[datetime] = None,
    can_recheck_comments: bool = True,
) -> None:
    """Archive a URL as stale without blocking future comment monitoring."""
    now = datetime.now(timezone.utc)
    stmt = (
        pg_insert(StaleUrl)
        .values(
            url_hash=_hash(url),
            url=url,
            mention_id=mention_id,
            reason=reason,
            source_published_at=source_published_at,
            first_seen_at=now,
            marked_stale_at=now,
            can_recheck_comments=can_recheck_comments,
        )
        .on_conflict_do_update(
            index_elements=["url_hash"],
            set_={
                "mention_id": mention_id,
                "reason": reason,
                "source_published_at": source_published_at,
                "marked_stale_at": now,
                "can_recheck_comments": can_recheck_comments,
            },
        )
    )
    await session.execute(stmt)
    await session.commit()


async def upsert_comment_watchlist(
    session: AsyncSession,
    url: str,
    source_name: str = "comment_watchlist",
    main_published_at: Optional[datetime] = None,
    last_comment_at: Optional[datetime] = None,
    comments_snapshot: Optional[str] = None,
    check_interval_minutes: int = 60,
) -> None:
    """Ensure a page is monitored for future fresh comments/reviews."""
    now = datetime.now(timezone.utc)
    snapshot_hash = _hash(comments_snapshot) if comments_snapshot else None
    stmt = (
        pg_insert(CommentWatchlist)
        .values(
            url_hash=_hash(url),
            url=url,
            source_name=source_name,
            main_published_at=main_published_at,
            last_comment_at=last_comment_at,
            comments_snapshot_hash=snapshot_hash,
            check_interval_minutes=check_interval_minutes,
            is_active=True,
            failure_count=0,
            first_seen_at=now,
            last_checked_at=now,
            last_changed_at=now if snapshot_hash else None,
        )
        .on_conflict_do_update(
            index_elements=["url_hash"],
            set_={
                "source_name": source_name,
                "main_published_at": main_published_at,
                "last_comment_at": last_comment_at,
                "comments_snapshot_hash": snapshot_hash,
                "check_interval_minutes": check_interval_minutes,
                "is_active": True,
                "last_checked_at": now,
            },
        )
    )
    await session.execute(stmt)
    await session.commit()


async def get_due_comment_watchlist(
    session: AsyncSession,
    limit: int = 20,
) -> list[CommentWatchlist]:
    """Return active pages whose comment blocks should be checked now."""
    result = await session.execute(
        select(CommentWatchlist)
        .where(CommentWatchlist.is_active.is_(True))
        .where(
            (CommentWatchlist.last_checked_at.is_(None))
            | (
                CommentWatchlist.last_checked_at
                <= text("NOW() - (check_interval_minutes || ' minutes')::interval")
            )
        )
        .order_by(CommentWatchlist.last_checked_at.asc().nullsfirst())
        .limit(limit)
    )
    return list(result.scalars().all())


async def update_comment_watchlist_check(
    session: AsyncSession,
    item: CommentWatchlist,
    comments_snapshot: Optional[str],
    last_comment_at: Optional[datetime],
    changed: bool,
    failed: bool = False,
) -> None:
    """Persist one watchlist check result."""
    now = datetime.now(timezone.utc)
    item.last_checked_at = now
    if failed:
        item.failure_count += 1
        if item.failure_count >= 5:
            item.is_active = False
    else:
        item.failure_count = 0
    if comments_snapshot is not None:
        item.comments_snapshot_hash = _hash(comments_snapshot)
    if last_comment_at is not None:
        item.last_comment_at = last_comment_at
        item.last_comment_hash = _hash(f"{last_comment_at.isoformat()}:{comments_snapshot or ''}")
    if changed:
        item.last_changed_at = now
    session.add(item)
    await session.commit()


# ── Telegram subscribers ─────────────────────────────────

async def save_telegram_subscriber(
    session: AsyncSession,
    chat_id: int | str,
    chat_type: Optional[str] = None,
    title: Optional[str] = None,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
) -> TelegramSubscriber:
    """Create or reactivate a Telegram chat subscribed to bot alerts."""
    chat_id_str = str(chat_id)
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(TelegramSubscriber).where(TelegramSubscriber.chat_id == chat_id_str)
    )
    subscriber = result.scalar_one_or_none()

    if subscriber is None:
        subscriber = TelegramSubscriber(
            chat_id=chat_id_str,
            chat_type=chat_type,
            title=title,
            username=username,
            first_name=first_name,
            last_name=last_name,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    else:
        subscriber.chat_type = chat_type
        subscriber.title = title
        subscriber.username = username
        subscriber.first_name = first_name
        subscriber.last_name = last_name
        subscriber.is_active = True
        subscriber.updated_at = now

    session.add(subscriber)
    await session.commit()
    return subscriber


async def get_active_telegram_chat_ids(session: AsyncSession) -> list[str]:
    """Return active Telegram chats subscribed through /start."""
    result = await session.execute(
        select(TelegramSubscriber.chat_id)
        .where(TelegramSubscriber.is_active.is_(True))
        .order_by(TelegramSubscriber.created_at.asc())
    )
    return [str(chat_id) for chat_id in result.scalars().all()]


# ── Content hash snapshots (dynamic source dedup) ───────

class ContentChangeResult:
    """Result of a content snapshot check."""
    __slots__ = ("is_new", "has_changed", "old_hash")

    def __init__(self, is_new: bool, has_changed: bool, old_hash: Optional[str] = None):
        self.is_new = is_new          # True: URL never seen before
        self.has_changed = has_changed  # True: URL seen before but content changed
        self.old_hash = old_hash      # Previous content hash (if any)

    @property
    def should_process(self) -> bool:
        """True if the mention should be processed (new or updated content)."""
        return self.is_new or self.has_changed


async def check_and_update_content_snapshot(
    session: AsyncSession,
    url: str,
    source_name: str,
    current_text: str,
) -> ContentChangeResult:
    """Check whether a URL's content has changed since last visit.

    - First visit (no snapshot): saves snapshot, returns is_new=True
    - Same content: updates last_checked_at only, returns has_changed=False
    - Changed content: updates snapshot, returns has_changed=True

    The caller decides whether to process the mention based on .should_process.
    """
    uh = _hash(url)
    ch = _hash(current_text)
    now = datetime.now(timezone.utc)

    result = await session.execute(
        select(ContentSnapshot).where(ContentSnapshot.url_hash == uh)
    )
    snapshot = result.scalar_one_or_none()

    if snapshot is None:
        # First time seeing this URL
        session.add(ContentSnapshot(
            url_hash=uh,
            url=url,
            source_name=source_name,
            content_hash=ch,
            first_seen_at=now,
            last_checked_at=now,
            last_changed_at=now,
        ))
        await session.commit()
        return ContentChangeResult(is_new=True, has_changed=False)

    old_hash = snapshot.content_hash
    snapshot.last_checked_at = now

    if snapshot.content_hash != ch:
        # Content changed (new comments, updated reviews, etc.)
        snapshot.content_hash = ch
        snapshot.last_changed_at = now
        await session.commit()
        return ContentChangeResult(is_new=False, has_changed=True, old_hash=old_hash)

    # No change
    await session.commit()
    return ContentChangeResult(is_new=False, has_changed=False, old_hash=old_hash)


# ── High-water mark (run checkpoints) ───────────────────

async def get_watermark(session: AsyncSession, source_name: str) -> Optional[datetime]:
    """Return the last successful run timestamp for a given source, or None (first run)."""
    result = await session.execute(
        select(RunCheckpoint).where(RunCheckpoint.source_name == source_name)
    )
    checkpoint = result.scalar_one_or_none()
    return checkpoint.last_run_at if checkpoint else None


async def set_watermark(session: AsyncSession, source_name: str, run_at: datetime) -> None:
    """Upsert the run checkpoint for a source (INSERT … ON CONFLICT DO UPDATE)."""
    stmt = (
        pg_insert(RunCheckpoint)
        .values(source_name=source_name, last_run_at=run_at, updated_at=run_at)
        .on_conflict_do_update(
            constraint="uq_run_checkpoints_source",
            set_={"last_run_at": run_at, "updated_at": run_at},
        )
    )
    await session.execute(stmt)
    await session.commit()
