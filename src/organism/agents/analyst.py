import time
from .base import BaseAgent, AgentResult, TemperatureLocked
from src.organism.core.loop import CoreLoop


class AnalystAgent(BaseAgent):

    temperature = 0.0      # deterministic — calculations must be reproducible
    max_iterations = 3

    @property
    def name(self) -> str:
        return "analyst"

    @property
    def description(self) -> str:
        return "Analyzes data, builds statistics, creates visualizations. Use for data analysis, reports, charts."

    @property
    def tools(self) -> list[str]:
        return ["code_executor", "file_manager"]

    async def _run_impl(self, task: str, start: float) -> AgentResult:
        # Enforce code_executor for all calculations — never mental math
        enriched = (
            f"{task}\n\n"
            "IMPORTANT: All calculations MUST be done via code_executor (Python). "
            "Never compute numbers mentally. Use pandas/numpy for analysis. "
            "Print all results with clear labels and units."
        )
        llm = TemperatureLocked(self.llm, self.temperature)
        loop = CoreLoop(llm, self.registry)
        loop_result = await loop.run(enriched, verbose=False, skip_orchestrator=True)
        return AgentResult(
            agent=self.name, task=task,
            output=loop_result.output, success=loop_result.success,
            duration=time.time() - start, error=loop_result.error,
        )

    async def run(self, task: str) -> AgentResult:
        start = time.time()
        # Q-7.5: cross-agent knowledge sharing
        effective_task = await self._enrich_with_cross_insights(task)
        result = await self._run_impl(effective_task, start)
        await self._save_reflection(task, result)
        return result
