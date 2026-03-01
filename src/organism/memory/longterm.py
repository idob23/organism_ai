import uuid
import json
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from .database import TaskMemory, UserProfile, AsyncSessionLocal
from .embeddings import get_embedding


def _enrich_for_embedding(task: str, tools_used: list[str] = None, outcome: str = None) -> str:
    """Build enriched text for embedding: [TASK] + [TOOLS] + [OUTCOME].

    This improves semantic search by distinguishing tasks that have similar text
    but different tools/outcomes. E.g., "create GSM report" vs "create mining report"
    will have different TOOLS and OUTCOME sections, making embeddings more distinct.
    """
    parts = [f"[TASK] {task}"]
    if tools_used:
        parts.append(f"[TOOLS] {','.join(tools_used)}")
    if outcome:
        # Truncate outcome to keep embedding focused
        parts.append(f"[OUTCOME] {outcome[:200]}")
    return " ".join(parts)


class LongTermMemory:

    async def save_task(
        self,
        task: str,
        result: str,
        success: bool,
        duration: float,
        steps_count: int,
        tools_used: list[str],
        quality_score: float = 0.0,
    ) -> str:
        memory_id = uuid.uuid4().hex

        # Enriched embedding: task + tools + outcome summary
        enriched_text = _enrich_for_embedding(
            task=task,
            tools_used=tools_used,
            outcome=result[:200] if success else None,
        )
        embedding = await get_embedding(enriched_text)

        async with AsyncSessionLocal() as session:
            memory = TaskMemory(
                id=memory_id,
                task=task,
                result=result[:2000],
                success=success,
                duration=duration,
                steps_count=steps_count,
                tools_used=",".join(tools_used),
                quality_score=quality_score,
                embedding=embedding if embedding else None,
            )
            session.add(memory)
            await session.commit()

        return memory_id

    # text-embedding-3-small: L2=sqrt(2*(1-cosine)), threshold 1.0 ~ cosine>=0.5
    SIMILARITY_THRESHOLD = 1.0

    async def search_similar(self, task: str, limit: int = 3) -> list[dict]:
        # Search with just [TASK] tag — we don't know tools/outcome yet
        search_text = _enrich_for_embedding(task=task)
        embedding = await get_embedding(search_text)

        async with AsyncSessionLocal() as session:
            if embedding:
                # Vector similarity search with distance threshold
                stmt = select(TaskMemory).where(
                    TaskMemory.success == True,
                    TaskMemory.embedding.isnot(None),
                    TaskMemory.embedding.l2_distance(embedding) < self.SIMILARITY_THRESHOLD,
                ).order_by(
                    TaskMemory.embedding.l2_distance(embedding)
                ).limit(limit)
            else:
                # Fallback: just get recent successful tasks
                stmt = select(TaskMemory).where(
                    TaskMemory.success == True,
                ).order_by(
                    TaskMemory.created_at.desc()
                ).limit(limit)

            result = await session.execute(stmt)
            memories = result.scalars().all()

        return [
            {
                "task": m.task,
                "result": m.result,
                "tools_used": m.tools_used.split(",") if m.tools_used else [],
                "duration": m.duration,
                "steps_count": m.steps_count,
                "quality_score": m.quality_score,
            }
            for m in memories
        ]

    async def get_stats(self) -> dict:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("""
                    SELECT COUNT(*),
                           AVG(duration),
                           SUM(CASE WHEN success THEN 1 ELSE 0 END),
                           AVG(quality_score)
                    FROM task_memories
                """)
            )
            row = result.fetchone()
            total = row[0] or 0
            avg_duration = round(float(row[1] or 0), 2)
            successful = row[2] or 0
            avg_quality = round(float(row[3] or 0), 2)

        return {
            "total_tasks": total,
            "successful_tasks": successful,
            "success_rate": round(successful / total * 100, 1) if total > 0 else 0,
            "avg_duration": avg_duration,
            "avg_quality_score": avg_quality,
        }

    async def update_profile(self, key: str, value: str) -> None:
        async with AsyncSessionLocal() as session:
            existing = await session.get(UserProfile, key)
            if existing:
                existing.value = value
            else:
                session.add(UserProfile(key=key, value=value))
            await session.commit()

    async def get_profile(self, key: str) -> str | None:
        async with AsyncSessionLocal() as session:
            obj = await session.get(UserProfile, key)
            return obj.value if obj else None