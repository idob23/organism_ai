import uuid
import json
from datetime import datetime, timedelta
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from .database import TaskMemory, UserProfile, AsyncSessionLocal
from .embeddings import get_embedding
from src.organism.llm.base import LLMProvider, Message


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


def _to_dict(m: TaskMemory) -> dict:
    return {
        "id": m.id,  # Q-5.3: included for CausalAnalyzer edge creation
        "task": m.task,
        "result": m.result,
        "tools_used": m.tools_used.split(",") if m.tools_used else [],
        "duration": m.duration,
        "steps_count": m.steps_count,
        "quality_score": m.quality_score,
    }


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

    @staticmethod
    async def _rerank(
        task: str, candidates: list[dict], llm: LLMProvider, top_k: int = 3
    ) -> list[dict]:
        """Use Haiku to rerank candidates by relevance to task. Falls back to original order on error."""
        numbered = "\n".join(
            f"{i}. {c['task'][:120]}" for i, c in enumerate(candidates)
        )
        prompt = (
            f"Task: {task}\n\n"
            f"Candidates:\n{numbered}\n\n"
            f"Return the indices (0-based) of the {top_k} most relevant candidates, "
            f"comma-separated, most relevant first. Only indices, no explanation."
        )
        try:
            resp = await llm.complete(
                messages=[Message(role="user", content=prompt)],
                model_tier="fast",
                max_tokens=32,
            )
            raw = resp.content.strip()
            indices = [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
            indices = [i for i in indices if 0 <= i < len(candidates)][:top_k]
            if indices:
                return [candidates[i] for i in indices]
        except Exception:
            pass
        return candidates[:top_k]

    async def search_similar(
        self, task: str, limit: int = 3, min_quality: float = 0.6,
        llm: LLMProvider | None = None,
    ) -> list[dict]:
        """Hybrid search: 0.7 * vector_score + 0.3 * bm25_score.

        Vector search uses pgvector L2 distance (semantic similarity).
        BM25 search uses PostgreSQL ts_vector with Russian stemming (keyword overlap).
        Combining both improves recall — e.g. "расход ГСМ" finds fuel tasks even
        when phrased differently or using abbreviations.

        Metadata filtering: success=True, quality_score >= min_quality, last 90 days.

        Adaptive K:
          - best score > 0.9 → return 1 (near-exact match, no more needed)
          - best score < 0.6 → return [] (nothing relevant)
          - otherwise → return up to limit
        """
        cutoff = datetime.utcnow() - timedelta(days=90)

        # Search with just [TASK] tag — we don't know tools/outcome yet
        search_text = _enrich_for_embedding(task=task)
        embedding = await get_embedding(search_text)

        async with AsyncSessionLocal() as session:
            if not embedding:
                # Fallback: recent high-quality tasks
                stmt = (
                    select(TaskMemory)
                    .where(
                        TaskMemory.success == True,
                        TaskMemory.quality_score >= min_quality,
                        TaskMemory.created_at >= cutoff,
                    )
                    .order_by(TaskMemory.created_at.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                return [_to_dict(m) for m in result.scalars().all()]

            fetch = limit * 2

            # --- Vector search (pgvector L2 distance) ---
            dist_expr = TaskMemory.embedding.l2_distance(embedding)
            vec_stmt = (
                select(TaskMemory, dist_expr.label("l2_dist"))
                .where(
                    TaskMemory.success == True,
                    TaskMemory.quality_score >= min_quality,
                    TaskMemory.created_at >= cutoff,
                    TaskMemory.embedding.isnot(None),
                    dist_expr < self.SIMILARITY_THRESHOLD,
                )
                .order_by(dist_expr)
                .limit(fetch)
            )
            vec_result = await session.execute(vec_stmt)
            vec_pairs = vec_result.all()  # [(TaskMemory, l2_dist), ...]

            # --- BM25 keyword search (ts_vector Russian config) ---
            bm25_rows: list = []
            try:
                bm25_result = await session.execute(
                    text("""
                        SELECT id,
                               ts_rank(to_tsvector('russian', task),
                                       plainto_tsquery('russian', :q)) AS bm25_rank
                        FROM task_memories
                        WHERE success = true
                          AND quality_score >= :min_quality
                          AND created_at >= :cutoff
                          AND to_tsvector('russian', task)
                              @@ plainto_tsquery('russian', :q)
                        ORDER BY bm25_rank DESC
                        LIMIT :lim
                    """),
                    {"q": task, "lim": fetch, "min_quality": min_quality, "cutoff": cutoff},
                )
                bm25_rows = bm25_result.all()
            except Exception:
                pass  # BM25 unavailable — fall back to vector-only

            # --- Build id → (TaskMemory, vec_score) from vector results ---
            id_to_memory: dict[str, TaskMemory] = {}
            vec_scores: dict[str, float] = {}
            for m, dist in vec_pairs:
                id_to_memory[m.id] = m
                vec_scores[m.id] = max(0.0, 1.0 - dist / self.SIMILARITY_THRESHOLD)

            # --- Normalize BM25 ranks to [0, 1] ---
            bm25_scores: dict[str, float] = {}
            if bm25_rows:
                raw = {row[0]: float(row[1]) for row in bm25_rows}
                max_rank = max(raw.values())
                bm25_scores = {
                    rid: (s / max_rank if max_rank > 0 else 0.0)
                    for rid, s in raw.items()
                }

            # --- Fetch full ORM objects for BM25-only results ---
            bm25_only_ids = set(bm25_scores) - set(id_to_memory)
            if bm25_only_ids:
                extra = await session.execute(
                    select(TaskMemory).where(TaskMemory.id.in_(bm25_only_ids))
                )
                for m in extra.scalars().all():
                    id_to_memory[m.id] = m

            # --- Combine: hybrid_score = 0.7 * vec_score + 0.3 * bm25_score ---
            all_ids = set(vec_scores) | set(bm25_scores)
            scored = [
                (
                    0.7 * vec_scores.get(rid, 0.0) + 0.3 * bm25_scores.get(rid, 0.0),
                    id_to_memory[rid],
                )
                for rid in all_ids
                if rid in id_to_memory
            ]
            scored.sort(key=lambda x: x[0], reverse=True)

        if not scored:
            return []

        best_score = scored[0][0]

        # Adaptive K: near-exact match → only 1 result
        if best_score > 0.9:
            return [_to_dict(scored[0][1])]

        # Adaptive K: nothing relevant → skip memory entirely
        if best_score < 0.6:
            return []

        candidates = [_to_dict(m) for _, m in scored[:limit * 2]]
        if llm and len(candidates) > 3:
            return await self._rerank(task, candidates, llm, top_k=limit)
        return candidates[:limit]

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