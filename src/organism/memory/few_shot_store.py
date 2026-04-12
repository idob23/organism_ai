"""Q-7.3: Few-shot example curation.

Stores high-quality task-plan pairs and retrieves top-3 most relevant
as demonstrations for the planner.
"""
import json
import uuid

from sqlalchemy import select, text, func

from config.settings import settings
from .database import FewShotExample, AsyncSessionLocal
from src.organism.logging.error_handler import get_logger

_log = get_logger("memory.few_shot_store")

MIN_QUALITY = 0.75      # minimum quality_score for saving
MAX_EXAMPLES = 100       # maximum rows in table (FIFO, delete oldest)
TOP_K = 3                # how many to inject into planner prompt


class FewShotStore:

    @staticmethod
    def infer_task_type(tools_used: list[str]) -> str:
        """Infer task_type from tools list (no LLM call).

        Returns one of: code | writing | research | data |
        presentation | mixed | conversation.
        """
        if not tools_used:
            return "conversation"
        s = set(tools_used)
        if "pptx_creator" in s:
            return "presentation"
        if "duplicate_finder" in s:
            return "data"
        if "code_executor" in s and "file_manager" in s and "web_search" not in s:
            return "data"
        if "code_executor" in s:
            return "code"
        if ("web_search" in s or "web_fetch" in s) and "code_executor" not in s:
            return "research"
        if ("text_writer" in s or "pdf_tool" in s) and "code_executor" not in s:
            return "writing"
        if s <= {"manage_agents", "manage_schedule", "memory_search"}:
            return "conversation"
        return "mixed"

    async def save_example(
        self,
        task_text: str,
        task_type: str,
        plan_steps: list[dict],   # [{"tool": "...", "description": "..."}]
        quality_score: float,
        tools_used: list[str],
    ) -> bool:
        """Save a successful task-plan pair as a few-shot example.

        Returns True if saved, False if skipped (low quality or duplicate).
        Maintains MAX_EXAMPLES limit by deleting oldest entries.
        """
        if quality_score < MIN_QUALITY:
            return False

        # Compact plan representation (only tool + description, no full input)
        compact_plan = [
            {"tool": s.get("tool", ""), "description": s.get("description", "")[:100]}
            for s in plan_steps[:5]  # max 5 steps
        ]
        plan_json = json.dumps(compact_plan, ensure_ascii=False)

        # Generate embedding for similarity search
        embedding = None
        try:
            from .embeddings import get_embedding
            emb = await get_embedding(task_text[:300])
            if emb:  # get_embedding returns [] on failure
                embedding = emb
        except Exception:
            pass  # save without embedding, text-based fallback

        async with AsyncSessionLocal() as session:
            # Dedup: skip if very similar task already exists (same task_type + prefix)
            prefix = task_text[:100].replace("%", "").replace("_", "")
            existing = await session.execute(
                select(FewShotExample.id).where(
                    FewShotExample.task_type == task_type,
                    FewShotExample.task_text.like(prefix + "%"),
                ).limit(1)
            )
            if existing.scalar_one_or_none():
                return False

            session.add(FewShotExample(
                id=uuid.uuid4().hex,
                task_type=task_type,
                task_text=task_text[:300],
                plan_json=plan_json,
                quality_score=quality_score,
                tools_used=",".join(tools_used),
                embedding=embedding,
                artel_id=settings.artel_id,
            ))
            await session.commit()

        # Enforce MAX_EXAMPLES limit
        try:
            async with AsyncSessionLocal() as session:
                count_result = await session.execute(
                    select(func.count()).select_from(FewShotExample)
                )
                total = count_result.scalar() or 0
                if total > MAX_EXAMPLES:
                    delete_count = total - MAX_EXAMPLES
                    oldest = await session.execute(
                        select(FewShotExample.id)
                        .order_by(FewShotExample.created_at.asc())
                        .limit(delete_count)
                    )
                    old_ids = [row[0] for row in oldest.fetchall()]
                    if old_ids:
                        for old_id in old_ids:
                            await session.execute(
                                text("DELETE FROM few_shot_examples WHERE id = :id"),
                                {"id": old_id},
                            )
                        await session.commit()
        except Exception:
            pass

        _log.info(
            f"Few-shot saved: type={task_type}, quality={quality_score:.2f}, "
            f"tools={tools_used}"
        )
        return True

    async def get_examples(
        self, task_text: str, task_type: str | None = None, limit: int = TOP_K,
    ) -> list[dict]:
        """Retrieve top-K most relevant few-shot examples.

        Strategy:
        1. If embedding available: vector cosine distance search
        2. Fallback: filter by task_type, order by quality_score desc

        Returns list of {"task": str, "plan": list, "tools": str, "quality": float}
        """
        # Try vector search first
        try:
            from .embeddings import get_embedding
            query_embedding = await get_embedding(task_text[:300])
            if query_embedding:
                async with AsyncSessionLocal() as session:
                    type_clause = ""
                    params: dict = {"emb": str(query_embedding), "lim": limit}
                    if task_type:
                        type_clause = "AND task_type = :ttype"
                        params["ttype"] = task_type

                    params["artel_id"] = settings.artel_id
                    rows = await session.execute(
                        text(f"""
                            SELECT id, task_text, plan_json, tools_used, quality_score
                            FROM few_shot_examples
                            WHERE embedding IS NOT NULL
                              AND artel_id = :artel_id {type_clause}
                            ORDER BY embedding <=> :emb
                            LIMIT :lim
                        """),
                        params,
                    )
                    results: list[dict] = []
                    for row in rows.fetchall():
                        results.append({
                            "task": row[1],
                            "plan": json.loads(row[2]),
                            "tools": row[3],
                            "quality": row[4],
                        })
                        # Bump usage_count
                        await session.execute(
                            text(
                                "UPDATE few_shot_examples "
                                "SET usage_count = usage_count + 1 WHERE id = :id"
                            ),
                            {"id": row[0]},
                        )
                    await session.commit()
                    if results:
                        return results
        except Exception:
            pass

        # Fallback: filter by task_type, order by quality
        try:
            async with AsyncSessionLocal() as session:
                stmt = (
                    select(FewShotExample)
                    .where(text("artel_id = :artel_id"))
                    .params(artel_id=settings.artel_id)
                    .order_by(FewShotExample.quality_score.desc())
                    .limit(limit)
                )
                if task_type:
                    stmt = stmt.where(FewShotExample.task_type == task_type)
                result = await session.execute(stmt)
                examples: list[dict] = []
                for row in result.scalars().all():
                    examples.append({
                        "task": row.task_text,
                        "plan": json.loads(row.plan_json),
                        "tools": row.tools_used,
                        "quality": row.quality_score,
                    })
                    row.usage_count += 1
                await session.commit()
                return examples
        except Exception:
            return []

    def format_for_prompt(self, examples: list[dict]) -> str:
        """Format few-shot examples as a prompt section.

        Returns empty string if no examples.
        """
        if not examples:
            return ""

        lines = ["[Successful task examples for reference:]"]
        for i, ex in enumerate(examples, 1):
            plan_desc = " -> ".join(
                s.get("tool", "") for s in ex.get("plan", [])
            )
            lines.append(
                f"  Example {i}: \"{ex['task'][:120]}\" "
                f"-> {plan_desc} (quality={ex['quality']:.2f})"
            )
        return "\n".join(lines)
