"""Q-5.3: CausalAnalyzer — async worker that infers graph edges after task completion.

Runs as a fire-and-forget asyncio task (via asyncio.create_task in MemoryManager).
For each completed task it finds semantically similar past tasks (top-3 via vector
search) and uses Haiku to classify their relationship:

  entity     — overlapping entities (gold, equipment, region, etc.)
  causal     — one task caused or motivated the other
  procedural — same tools used (no LLM call needed, detected locally)

Each Haiku call is independently wrapped in try/except so a single failure never
stops the rest of the analysis.  All graph writes are also try/except-guarded.
"""
import json
import re
from pathlib import Path

from src.organism.llm.base import LLMProvider, Message
from src.organism.memory.graph import MemoryGraph
from src.organism.memory.longterm import LongTermMemory

_PROMPT_TEMPLATE = Path("config/prompts/causal_analyzer.txt").read_text(encoding="utf-8")


def _parse_analysis(text: str) -> dict | None:
    """Extract the first JSON object from a Haiku response. Returns None on failure."""
    try:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        return json.loads(match.group(0))
    except Exception:
        return None


class CausalAnalyzer:

    def __init__(self, graph: MemoryGraph, longterm: LongTermMemory) -> None:
        self.graph = graph
        self.longterm = longterm

    async def analyze_task(
        self,
        task_id: str,
        task: str,
        tools_used: list[str],
        llm: LLMProvider,
    ) -> None:
        """Infer causal, entity, and procedural edges for a newly completed task.

        Strategy:
        1. Vector search → top-3 semantically similar past tasks (cheap, avoids
           running Haiku on unrelated tasks).
        2. Procedural edges: detected locally from tool overlap — no LLM call.
        3. Entity + causal edges: one Haiku call per candidate (max 3 calls total).
        """
        # Step 1: candidate retrieval via vector similarity
        try:
            similar = await self.longterm.search_similar(task, limit=3)
        except Exception:
            return

        if not similar:
            return

        tools_set = {t for t in tools_used if t}

        for past in similar:
            past_id: str | None = past.get("id")
            if not past_id or past_id == task_id:
                continue

            past_task: str = past.get("task", "")
            past_tools: list[str] = past.get("tools_used") or []
            past_tools_set = {t for t in past_tools if t}

            # Step 2: procedural edge — local check, no LLM
            shared_tools = tools_set & past_tools_set
            if shared_tools:
                try:
                    weight = len(shared_tools) / max(len(tools_set | past_tools_set), 1)
                    await self.graph.add_edge(
                        task_id, past_id, "procedural",
                        weight=round(weight, 3),
                        metadata={"shared_tools": sorted(shared_tools)},
                    )
                except Exception:
                    pass

            # Step 3: entity + causal — one Haiku call per candidate
            analysis: dict | None = None
            try:
                prompt = (
                    _PROMPT_TEMPLATE
                    .replace("{task_a}", task[:300])
                    .replace("{tools_a}", ", ".join(tools_used) or "none")
                    .replace("{task_b}", past_task[:300])
                    .replace("{tools_b}", ", ".join(past_tools) or "none")
                )
                resp = await llm.complete(
                    messages=[Message(role="user", content=prompt)],
                    model_tier="fast",
                    max_tokens=200,
                )
                analysis = _parse_analysis(resp.content)
            except Exception:
                continue  # skip this candidate, try the next

            if analysis is None:
                continue

            # Entity edge
            entities: list = analysis.get("entity_overlap") or []
            if entities:
                try:
                    # weight scales with number of shared entities, capped at 1.0
                    weight = min(1.0, len(entities) * 0.25)
                    await self.graph.add_edge(
                        task_id, past_id, "entity",
                        weight=round(weight, 3),
                        metadata={"entities": entities},
                    )
                except Exception:
                    pass

            # Causal edge
            if analysis.get("causal"):
                direction = analysis.get("causal_direction", "none")
                try:
                    if direction == "a_to_b":
                        await self.graph.add_edge(task_id, past_id, "causal", weight=0.9)
                    elif direction == "b_to_a":
                        await self.graph.add_edge(past_id, task_id, "causal", weight=0.9)
                except Exception:
                    pass
