import asyncio
import time
from .base import BaseAgent, AgentResult, TemperatureLocked
from src.organism.core.loop import CoreLoop
from src.organism.llm.base import Message


class ResearcherAgent(BaseAgent):

    temperature = 0.3      # slight variation helps with query diversity
    max_iterations = 3

    @property
    def name(self) -> str:
        return "researcher"

    @property
    def description(self) -> str:
        return "Searches and analyzes information from the internet. Use for news, facts, current events, market data."

    @property
    def tools(self) -> list[str]:
        return ["web_search", "web_fetch", "file_manager"]

    async def _parallel_search(self, task: str) -> str | None:
        """Generate 2-3 search queries via Haiku, run in parallel, deduplicate results."""
        try:
            resp = await self.llm.complete(
                messages=[Message(role="user", content=task)],
                system=(
                    "Generate 2-3 distinct search queries for the given task. "
                    "Return ONLY a comma-separated list of queries, no explanation."
                ),
                model_tier="fast",
                max_tokens=120,
                temperature=self.temperature,
            )
            queries = [q.strip() for q in resp.content.split(",") if q.strip()][:3]
            if len(queries) < 2:
                return None
        except Exception:
            return None

        tool = self.registry.get("web_search")
        raw_results = await asyncio.gather(
            *[tool.execute({"query": q, "max_results": 5}) for q in queries],
            return_exceptions=True,
        )

        # Combine and deduplicate by line (URL lines are unique per result)
        seen: set[str] = set()
        combined: list[str] = []
        for r in raw_results:
            if isinstance(r, Exception) or not getattr(r, "output", None):
                continue
            for line in r.output.splitlines():
                if line not in seen:
                    seen.add(line)
                    combined.append(line)

        return "\n".join(combined) if combined else None

    async def _run_impl(self, task: str, start: float) -> AgentResult:
        llm = TemperatureLocked(self.llm, self.temperature)
        search_context = await self._parallel_search(task)
        if search_context:
            enriched = (
                f"{task}\n\n"
                f"[Pre-fetched research data — use this to answer the task]:\n{search_context}"
            )
            loop = CoreLoop(llm, self.registry)
            loop_result = await loop.run(enriched, verbose=False)
        else:
            loop = CoreLoop(llm, self.registry)
            loop_result = await loop.run(task, verbose=False)

        return AgentResult(
            agent=self.name, task=task,
            output=loop_result.output, success=loop_result.success,
            duration=time.time() - start, error=loop_result.error,
        )

    async def run(self, task: str) -> AgentResult:
        start = time.time()
        result = await self._run_impl(task, start)
        await self._save_reflection(task, result)
        return result
