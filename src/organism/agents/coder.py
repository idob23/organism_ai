import hashlib
import time
from .base import BaseAgent, AgentResult, TemperatureLocked
from src.organism.core.loop import CoreLoop


class CoderAgent(BaseAgent):

    temperature = 0.0      # deterministic — same input must yield same code
    max_iterations = 5     # code tasks may need more retry cycles

    # Session-scoped snippet cache: task_hash → output (avoids re-running identical code)
    _snippet_cache: dict[str, str] = {}

    @property
    def name(self) -> str:
        return "coder"

    @property
    def description(self) -> str:
        return "Writes, debugs and runs Python code. Use for algorithms, data processing, calculations, scripts."

    @property
    def tools(self) -> list[str]:
        return ["code_executor", "file_manager"]

    def _cache_key(self, task: str) -> str:
        return hashlib.sha256(task.strip().lower().encode()).hexdigest()[:16]

    async def run(self, task: str) -> AgentResult:
        start = time.time()

        key = self._cache_key(task)
        if key in self._snippet_cache:
            return AgentResult(
                agent=self.name, task=task,
                output=self._snippet_cache[key],
                success=True, duration=time.time() - start,
            )

        # Q-7.5: cross-agent knowledge sharing
        effective_task = await self._enrich_with_cross_insights(task)

        llm = TemperatureLocked(self.llm, self.temperature)
        loop = CoreLoop(llm, self.registry)
        loop.MAX_RETRIES = self.max_iterations
        loop_result = await loop.run(effective_task, verbose=False)

        result = AgentResult(
            agent=self.name, task=task,
            output=loop_result.output, success=loop_result.success,
            duration=time.time() - start, error=loop_result.error,
        )

        if result.success:
            self._snippet_cache[key] = result.output

        await self._save_reflection(task, result)
        return result
