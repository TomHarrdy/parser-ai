import hashlib
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.models import Base
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


async def mention_exists_by_text_hash(session: AsyncSession, text: str) -> bool:
    """Check if a mention with identical text hash already exists (catches reprints)."""
    h = _hash(text)
    result = await session.execute(
        text("SELECT 1 FROM mentions WHERE raw_text_hash = :h LIMIT 1"), {"h": h}
    )
    return result.scalar() is not None


def url_hash(url: str) -> str:
    return _hash(url)


def text_hash(text: str) -> str:
    return _hash(text)
