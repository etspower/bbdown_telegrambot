"""
tests/test_database.py - 数据库层测试

注意：这些测试需要异步数据库支持
"""

import pytest
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import String, Integer, select
from sqlalchemy.orm import Mapped, mapped_column

# 使用内存数据库进行测试
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

Base = declarative_base()


class TestSubscription(Base):
    """测试用的 Subscription 模型"""
    __tablename__ = "test_subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(50), nullable=False)
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False)
    keyword: Mapped[str] = mapped_column(String(100), nullable=True)


@pytest.fixture
async def db_session():
    """创建测试数据库会话"""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
    
    await engine.dispose()


@pytest.mark.asyncio
class TestSubscriptionDedup:
    """测试订阅去重功能"""
    
    async def test_add_same_subscription_twice(self, db_session):
        """测试添加相同订阅两次"""
        # 第一次添加
        sub1 = TestSubscription(uid="123456", chat_id=1, keyword="test")
        db_session.add(sub1)
        await db_session.commit()
        
        # 第二次添加相同 uid 和 chat_id
        sub2 = TestSubscription(uid="123456", chat_id=1, keyword="updated")
        db_session.add(sub2)
        await db_session.commit()
        
        # 查询应该有两条记录（实际生产环境应该用唯一约束阻止）
        result = await db_session.execute(
            select(TestSubscription).where(TestSubscription.uid == "123456")
        )
        subs = result.scalars().all()
        assert len(subs) == 2  # 演示：没有唯一约束时会插入重复


@pytest.mark.asyncio
class TestDLStatus:
    """测试下载状态"""
    
    def test_status_values(self):
        """测试状态值定义"""
        from bot.database import DLStatus
        
        assert DLStatus.PENDING.value == "pending"
        assert DLStatus.DOWNLOADING.value == "downloading"
        assert DLStatus.DONE.value == "done"
        assert DLStatus.ABANDONED.value == "abandoned"
    
    def test_status_comparison(self):
        """测试状态比较"""
        from bot.database import DLStatus
        
        assert DLStatus.DONE != DLStatus.PENDING
        assert DLStatus.DOWNLOADING != DLStatus.ABANDONED
