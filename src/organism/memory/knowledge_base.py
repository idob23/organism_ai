from sqlalchemy import select
from .database import KnowledgeRule, AsyncSessionLocal


class KnowledgeBase:

    async def get_rules(self, top_k: int = 5, min_confidence: float = 0.7) -> list[str]:
        """Return top rules ordered by confidence * usage_count descending."""
        async with AsyncSessionLocal() as session:
            stmt = (
                select(KnowledgeRule)
                .where(KnowledgeRule.confidence >= min_confidence)
                .order_by((KnowledgeRule.confidence * KnowledgeRule.usage_count).desc())
                .limit(top_k)
            )
            result = await session.execute(stmt)
            return [r.rule_text for r in result.scalars().all()]

    async def add_rule(
        self, rule_text: str, confidence: float, source_task_hash: str
    ) -> None:
        """Add a new rule or update an existing one if exact text matches."""
        async with AsyncSessionLocal() as session:
            stmt = select(KnowledgeRule).where(KnowledgeRule.rule_text == rule_text)
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                if confidence > existing.confidence:
                    existing.confidence = confidence
                hashes = set(existing.source_tasks.split(",")) if existing.source_tasks else set()
                hashes.discard("")
                hashes.add(source_task_hash)
                existing.source_tasks = ",".join(hashes)
                existing.usage_count += 1
            else:
                session.add(KnowledgeRule(
                    rule_text=rule_text,
                    confidence=confidence,
                    source_tasks=source_task_hash,
                    usage_count=1,
                ))

            await session.commit()
