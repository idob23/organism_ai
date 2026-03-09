import hashlib
import re
from datetime import datetime, timedelta

from sqlalchemy import select, func, text

from src.organism.llm.base import LLMProvider, Message
from src.organism.memory.database import SolutionCacheEntry, AsyncSessionLocal
from config.settings import settings


NORMALIZE_PROMPT = """You are a task normalizer. Convert the user task to a canonical form.

Rules:
- Normalize action synonyms in Russian: \u043d\u0430\u043f\u0438\u0448\u0438/\u0441\u043e\u0441\u0442\u0430\u0432\u044c/\u043f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u044c/\u0441\u0434\u0435\u043b\u0430\u0439 \u2192 \u043d\u0430\u043f\u0438\u0441\u0430\u0442\u044c; \u043d\u0430\u0439\u0434\u0438/\u043f\u043e\u0438\u0449\u0438/\u0443\u0437\u043d\u0430\u0439/\u043f\u0440\u043e\u0432\u0435\u0440\u044c \u2192 \u043d\u0430\u0439\u0442\u0438; \u0441\u043e\u0437\u0434\u0430\u0439/\u0441\u0433\u0435\u043d\u0435\u0440\u0438\u0440\u0443\u0439 \u2192 \u0441\u043e\u0437\u0434\u0430\u0442\u044c
- Normalize in English: write/draft/compose/prepare -> write; find/search/look up/check -> find; create/generate/build -> create
- Remove polite filler: \u043f\u043e\u0436\u0430\u043b\u0443\u0439\u0441\u0442\u0430, \u043c\u043e\u0436\u0435\u0448\u044c, \u043f\u043e\u043c\u043e\u0433\u0438 \u043c\u043d\u0435, please, could you, help me
- Keep ALL specific requirements: topic, format, length, names, dates, numbers
- Lowercase the result
- Return ONLY the normalized task text, one line, no explanation, no JSON, no quotes"""


class SolutionCache:
    """L1 solution cache: hash-based lookup for high-quality past results.

    Flow:
      1. normalize_task(task, llm)  → canonical form via Haiku
      2. hash_task(canonical)       → SHA-256 hex string
      3. get(hash)                  → cached result or None (respects TTL)
      4. put(hash, ...)             → stores if quality_score >= MIN_QUALITY
    """

    TTL_DAYS = 30
    MIN_QUALITY = 0.8

    async def normalize_task(self, task: str, llm: LLMProvider) -> str:
        """Call Haiku to reduce task to a canonical, cache-friendly form."""
        try:
            response = await llm.complete(
                messages=[Message(role="user", content=task)],
                system=NORMALIZE_PROMPT,
                model_tier="fast",
                max_tokens=200,
            )
            normalized = response.content.strip().lower()
            # Strip any accidental markdown/quote wrappers from LLM response
            normalized = re.sub(r'^[`"\'\u00ab\u00bb]+|[`"\'\u00ab\u00bb]+$', "", normalized).strip()
            return normalized if normalized else task.lower().strip()
        except Exception:
            return task.lower().strip()

    def hash_task(self, canonical: str) -> str:
        """Return SHA-256 hex digest of the canonical task string."""
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def get(self, task_hash: str) -> dict | None:
        """Return cached entry if it exists and has not expired, else None.

        On a cache hit the hit counter is incremented.
        On an expired entry the row is deleted.
        Q-9.6: filtered by artel_id for multi-tenancy.
        """
        async with AsyncSessionLocal() as session:
            # Q-9.6: filter by artel_id
            stmt = (
                select(SolutionCacheEntry)
                .where(
                    SolutionCacheEntry.task_hash == task_hash,
                    text("artel_id = :artel_id"),
                )
                .params(artel_id=settings.artel_id)
            )
            result = await session.execute(stmt)
            entry = result.scalar_one_or_none()
            if entry is None:
                return None

            if entry.expires_at < datetime.utcnow():
                await session.delete(entry)
                await session.commit()
                return None

            entry.hits += 1
            await session.commit()

            return {
                "canonical_task": entry.canonical_task,
                "original_task": entry.original_task,
                "result": entry.result,
                "quality_score": entry.quality_score,
                "hits": entry.hits,
            }

    async def put(
        self,
        task_hash: str,
        canonical_task: str,
        original_task: str,
        result: str,
        quality_score: float,
    ) -> None:
        """Persist a result to the cache.

        Only stored when quality_score >= MIN_QUALITY.
        If the hash already exists, the entry is updated only when the new
        quality_score is strictly higher than the stored one.
        """
        if quality_score < self.MIN_QUALITY:
            return

        expires_at = datetime.utcnow() + timedelta(days=self.TTL_DAYS)

        async with AsyncSessionLocal() as session:
            existing = await session.get(SolutionCacheEntry, task_hash)
            if existing:
                if quality_score > existing.quality_score:
                    existing.result = result[:4000]
                    existing.quality_score = quality_score
                    existing.expires_at = expires_at
                    await session.commit()
            else:
                session.add(SolutionCacheEntry(
                    task_hash=task_hash,
                    canonical_task=canonical_task,
                    original_task=original_task[:500],
                    result=result[:4000],
                    quality_score=quality_score,
                    hits=0,
                    expires_at=expires_at,
                ))
                await session.commit()
                # Q-9.6: set artel_id on newly created row
                try:
                    await session.execute(
                        text("UPDATE solution_cache SET artel_id = :aid WHERE task_hash = :th"),
                        {"aid": settings.artel_id, "th": task_hash},
                    )
                    await session.commit()
                except Exception:
                    pass

    async def get_stats(self) -> dict:
        """Return live stats for non-expired cache entries.
        Q-9.6: filtered by artel_id."""
        async with AsyncSessionLocal() as session:
            row = (await session.execute(
                text("""
                    SELECT COUNT(task_hash),
                           SUM(hits),
                           AVG(quality_score)
                    FROM solution_cache
                    WHERE expires_at > :now
                      AND artel_id = :artel_id
                """),
                {"now": datetime.utcnow(), "artel_id": settings.artel_id},
            )).fetchone()

        return {
            "cache_entries": row[0] or 0,
            "total_cache_hits": int(row[1] or 0),
            "avg_cached_quality": round(float(row[2] or 0), 2),
        }
