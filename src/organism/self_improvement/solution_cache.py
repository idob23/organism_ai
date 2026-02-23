import json
import hashlib
from sqlalchemy import Column, String, Text, Integer
from sqlalchemy import select
from src.organism.memory.database import Base, AsyncSessionLocal, engine


class SolutionCache(Base):
    __tablename__ = "solution_cache"

    key = Column(String, primary_key=True)
    task_pattern = Column(Text, nullable=False)
    solution = Column(Text, nullable=False)
    hits = Column(Integer, default=0)
    success_count = Column(Integer, default=0)


async def init_cache_table() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SolutionCache.__table__.create, checkfirst=True)


class SolutionCacheManager:

    def __init__(self) -> None:
        self._initialized = False

    async def _ensure_init(self) -> None:
        if not self._initialized:
            await init_cache_table()
            self._initialized = True

    def _make_key(self, task: str) -> str:
        # Normalize task for caching
        normalized = task.lower().strip()
        return hashlib.md5(normalized.encode()).hexdigest()

    async def get(self, task: str) -> dict | None:
        await self._ensure_init()
        key = self._make_key(task)
        async with AsyncSessionLocal() as session:
            obj = await session.get(SolutionCache, key)
            if obj:
                # Increment hits
                obj.hits += 1
                await session.commit()
                return json.loads(obj.solution)
        return None

    async def save(self, task: str, solution: dict, success: bool) -> None:
        await self._ensure_init()
        key = self._make_key(task)
        async with AsyncSessionLocal() as session:
            obj = await session.get(SolutionCache, key)
            if obj:
                if success:
                    obj.success_count += 1
                    obj.solution = json.dumps(solution)
            else:
                session.add(SolutionCache(
                    key=key,
                    task_pattern=task[:500],
                    solution=json.dumps(solution),
                    hits=0,
                    success_count=1 if success else 0,
                ))
            await session.commit()

    async def get_top_patterns(self, limit: int = 10) -> list[dict]:
        await self._ensure_init()
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SolutionCache)
                .order_by(SolutionCache.hits.desc())
                .limit(limit)
            )
            items = result.scalars().all()
        return [
            {
                "pattern": i.task_pattern,
                "hits": i.hits,
                "success_count": i.success_count,
            }
            for i in items
        ]
