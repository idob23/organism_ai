import time
from .base import BaseAgent, AgentResult
from src.organism.core.loop import CoreLoop


class ResearcherAgent(BaseAgent):

    @property
    def name(self) -> str:
        return "researcher"

    @property
    def description(self) -> str:
        return "Searches and analyzes information from the internet. Use for news, facts, current events, market data."

    @property
    def tools(self) -> list[str]:
        return ["web_search", "web_fetch", "file_manager"]

    async def run(self, task: str) -> AgentResult:
        start = time.time()
        loop = CoreLoop(self.llm, self.registry)
        result = await loop.run(task, verbose=False)
        return AgentResult(
            agent=self.name,
            task=task,
            output=result.output,
            success=result.success,
            duration=time.time() - start,
            error=result.error,
        )
