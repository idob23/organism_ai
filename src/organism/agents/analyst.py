import time
from .base import BaseAgent, AgentResult
from src.organism.core.loop import CoreLoop


class AnalystAgent(BaseAgent):

    @property
    def name(self) -> str:
        return "analyst"

    @property
    def description(self) -> str:
        return "Analyzes data, builds statistics, creates visualizations. Use for data analysis, reports, charts."

    @property
    def tools(self) -> list[str]:
        return ["code_executor", "file_manager"]

    async def run(self, task: str) -> AgentResult:
        start = time.time()
        enriched = (
            f"{task}\n\n"
            "Use pandas/numpy for analysis. Print clear formatted results with statistics."
        )
        loop = CoreLoop(self.llm, self.registry)
        result = await loop.run(enriched, verbose=False)
        return AgentResult(
            agent=self.name,
            task=task,
            output=result.output,
            success=result.success,
            duration=time.time() - start,
            error=result.error,
        )
