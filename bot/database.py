from __future__ import annotations
import os
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy import String, Integer, Boolean, DateTime, select, delete, text, func
from pathlib import Path

from config import DATA_DIR

# Ensure DATA_DIR exists
Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
db_path = os.path.join(DATA_DIR, "bot.db")
DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
)

Base = declarative_base()

class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(50), nullable=False)
    up_name: Mapped[str] = mapped_column(String(100), nullable=True)
    keyword: Mapped[str] = mapped_column(String(100), nullable=True)
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False)

class DownloadHistory(Base):
    __tablename__ = "download_history"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(50), nullable=False)
    bvid: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)

class UpVideo(Base):
    """Stores all video URLs fetched via BBDown for a given UP master."""
    __tablename__ = "up_videos"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    bvid: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    url: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=True)
    parsed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Safe migration: add up_name column to existing subscriptions table
        try:
            await conn.execute(text("ALTER TABLE subscriptions ADD COLUMN up_name VARCHAR(100)"))
        except Exception:
            pass

# ─────────────────────────── Subscriptions ────────────────────────────────

async def add_subscription(uid: str, chat_id: int, keyword: str = None, up_name: str = None) -> bool:
    """
    添加或更新订阅。
    Returns True if newly created, False if updated existing subscription.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.uid == uid, Subscription.chat_id == chat_id)
        )
        sub = result.scalar_one_or_none()
        if sub:
            # 更新已有订阅
            sub.keyword = keyword
            if up_name:
                sub.up_name = up_name
            await session.commit()
            return False  # 已存在，本次为更新
        new_sub = Subscription(uid=uid, chat_id=chat_id, keyword=keyword, up_name=up_name)
        session.add(new_sub)
        await session.commit()
        return True  # 新建成功

async def remove_subscription(uid: str, chat_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(Subscription).where(Subscription.uid == uid, Subscription.chat_id == chat_id)
        )
        await session.commit()
        return result.rowcount > 0

async def get_all_subscriptions() -> list[Subscription]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Subscription))
        return list(result.scalars().all())

async def get_user_subscriptions(chat_id: int) -> list[Subscription]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Subscription).where(Subscription.chat_id == chat_id))
        return list(result.scalars().all())

# ─────────────────────────── Download History ─────────────────────────────

async def is_bvid_downloaded(bvid: str) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DownloadHistory).where(DownloadHistory.bvid == bvid)
        )
        return result.scalar_one_or_none() is not None

async def mark_bvid_downloaded(uid: str, bvid: str):
    async with AsyncSessionLocal() as session:
        try:
            history = DownloadHistory(uid=uid, bvid=bvid)
            session.add(history)
            await session.commit()
        except Exception:
            await session.rollback()

# ─────────────────────────── UP Video Cache ───────────────────────────────

async def upsert_up_video_url(uid: str, bvid: str, url: str) -> bool:
    """Insert a video URL for a UP master. Does nothing if bvid already exists. Returns True if newly inserted."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(UpVideo).where(UpVideo.bvid == bvid))
        existing = result.scalar_one_or_none()
        if existing:
            return False
        session.add(UpVideo(uid=uid, bvid=bvid, url=url, parsed=False))
        await session.commit()
        return True

async def get_unparsed_videos(uid: str, limit: int = 50) -> list[UpVideo]:
    """Get videos that haven't been parsed for title yet."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(UpVideo)
            .where(UpVideo.uid == uid, UpVideo.parsed == False)
            .limit(limit)
        )
        return list(result.scalars().all())

async def update_video_title(bvid: str, title: str):
    """Set the title for a video and mark it as parsed."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(UpVideo).where(UpVideo.bvid == bvid))
        video = result.scalar_one_or_none()
        if video:
            video.title = title
            video.parsed = True
            await session.commit()

async def get_videos_by_uid(uid: str, page: int = 1, page_size: int = 8) -> list[UpVideo]:
    """Paginated video list for a UP master (most recently fetched first)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(UpVideo)
            .where(UpVideo.uid == uid)
            .order_by(UpVideo.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all())

async def count_videos_by_uid(uid: str) -> int:
    """Total number of cached videos for a UP master."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count()).select_from(UpVideo).where(UpVideo.uid == uid)
        )
        return result.scalar_one()

async def get_recent_videos_by_uid(uid: str, limit: int = 10) -> list[UpVideo]:
    """Get most recently fetched (highest id) parsed videos for subscription matching."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(UpVideo)
            .where(UpVideo.uid == uid, UpVideo.parsed == True)
            .order_by(UpVideo.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
