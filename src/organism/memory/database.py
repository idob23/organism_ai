from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, String, Float, Integer, Text, DateTime, Boolean
from sqlalchemy import func
from pgvector.sqlalchemy import Vector
from config.settings import settings


class Base(DeclarativeBase):
    pass


class TaskMemory(Base):
    __tablename__ = "task_memories"

    id = Column(String, primary_key=True)
    task = Column(Text, nullable=False)
    result = Column(Text, nullable=False)
    success = Column(Boolean, default=True)
    duration = Column(Float, default=0.0)
    steps_count = Column(Integer, default=0)
    tools_used = Column(Text, default="")
    quality_score = Column(Float, default=0.0)  # NEW: 0.0 - 1.0
    embedding = Column(Vector(1536), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class UserProfile(Base):
    __tablename__ = "user_profile"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)