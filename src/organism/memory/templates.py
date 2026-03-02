"""Q-5.4: TemplateExtractor — extracts and matches procedural templates from successful tasks.

After each high-quality task (quality >= 0.8), Haiku is asked whether the task
represents a repeatable pattern.  If yes, the pattern is saved/updated in
procedural_templates.  Before planning, loop.py looks up the best matching
template and injects it as a hint into the planner context.
"""
import json
import re
import uuid
from pathlib import Path

from sqlalchemy import select

from src.organism.llm.base import LLMProvider, Message
from src.organism.memory.database import ProceduralTemplate, AsyncSessionLocal

_PROMPT_TEMPLATE = Path("config/prompts/template_extractor.txt").read_text(encoding="utf-8")


def _parse_extraction(text: str) -> dict | None:
    """Extract the first JSON object from a Haiku response. Returns None on failure."""
    try:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        return json.loads(match.group(0))
    except Exception:
        return None


class TemplateExtractor:

    async def extract_template(
        self,
        task: str,
        tools_used: list[str],
        result: str,
        quality_score: float,
        llm: LLMProvider,
    ) -> None:
        """Ask Haiku if this task matches a reusable pattern. Save/update if yes."""
        prompt = (
            _PROMPT_TEMPLATE
            .replace("{task}", task[:400])
            .replace("{tools}", ", ".join(tools_used) or "none")
            .replace("{result}", result[:500])
        )
        try:
            resp = await llm.complete(
                messages=[Message(role="user", content=prompt)],
                model_tier="fast",
                max_tokens=300,
            )
            parsed = _parse_extraction(resp.content)
        except Exception:
            return

        if parsed is None:
            return

        pattern_name: str | None = parsed.get("pattern_name")
        if not pattern_name:
            return  # one-off task, nothing to save

        task_pattern: str = parsed.get("task_pattern") or task[:200]
        code_template: str | None = parsed.get("code_template") or None
        tools_json = json.dumps(tools_used)

        await self._save_template(pattern_name, task_pattern, tools_json, code_template, quality_score)

    async def _save_template(
        self,
        pattern_name: str,
        task_pattern: str,
        tools_json: str,
        code_template: str | None,
        quality_score: float,
    ) -> None:
        """Upsert template: update running average and success_count if exists, else insert."""
        async with AsyncSessionLocal() as session:
            stmt = select(ProceduralTemplate).where(ProceduralTemplate.pattern_name == pattern_name)
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                n = existing.success_count
                new_avg = (existing.avg_quality * n + quality_score) / (n + 1)
                existing.avg_quality = round(new_avg, 4)
                existing.success_count = n + 1
                # Replace code_template only when the new result is better
                if code_template and quality_score > existing.avg_quality:
                    existing.code_template = code_template
            else:
                session.add(ProceduralTemplate(
                    id=uuid.uuid4().hex,
                    pattern_name=pattern_name,
                    tools_sequence=tools_json,
                    code_template=code_template,
                    task_pattern=task_pattern,
                    success_count=1,
                    avg_quality=round(quality_score, 4),
                ))
            await session.commit()

    async def find_template(self, task: str) -> dict | None:
        """Find the best matching template by word-overlap with task_pattern.

        Splits both the query and each template's task_pattern into words and
        computes overlap / pattern_size.  Returns the best match if its score
        is >= 0.3, otherwise None.
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(ProceduralTemplate))
            templates = result.scalars().all()

        if not templates:
            return None

        task_words = set(task.lower().split())
        best_score = 0.0
        best: ProceduralTemplate | None = None

        for tmpl in templates:
            pattern_words = set(tmpl.task_pattern.lower().split())
            if not pattern_words:
                continue
            overlap = len(task_words & pattern_words) / len(pattern_words)
            if overlap > best_score:
                best_score = overlap
                best = tmpl

        if best is None or best_score < 0.3:
            return None

        try:
            tools_seq = json.loads(best.tools_sequence) if best.tools_sequence else []
        except Exception:
            tools_seq = []

        return {
            "pattern_name": best.pattern_name,
            "task_pattern": best.task_pattern,
            "tools_sequence": tools_seq,
            "code_template": best.code_template,
            "avg_quality": best.avg_quality,
            "success_count": best.success_count,
        }
