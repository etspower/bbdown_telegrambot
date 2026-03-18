import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy import String, Integer, select, delete
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
    keyword: Mapped[str] = mapped_column(String(100), nullable=True)
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False)

class DownloadHistory(Base):
    __tablename__ = "download_history"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(50), nullable=False)
    bvid: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def add_subscription(uid: str, chat_id: int, keyword: str = None) -> bool:
    async with AsyncSessionLocal() as session:
        # Check if already exists
        result = await session.execute(
            select(Subscription).where(Subscription.uid == uid, Subscription.chat_id == chat_id)
        )
        if result.scalar_one_or_none():
            return False # Already subscribed
        
        new_sub = Subscription(uid=uid, chat_id=chat_id, keyword=keyword)
        session.add(new_sub)
        await session.commit()
        return True

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

async def is_bvid_downloaded(bvid: str) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DownloadHistory).where(DownloadHistory.bvid == bvid)
        )
        return result.scalar_one_or_none() is not None

async def mark_bvid_downloaded(uid: str, bvid: str):
    async with AsyncSessionLocal() as session:
        history = DownloadHistory(uid=uid, bvid=bvid)
        session.add(history)
        await session.commit()
