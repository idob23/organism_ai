import time
from .base import BaseAgent, AgentResult
from src.organism.core.loop import CoreLoop
from src.organism.core.planner import Planner
from src.organism.llm.base import Message


class WriterAgent(BaseAgent):

    @property
    def name(self) -> str:
        return "writer"

    @property
    def description(self) -> str:
        return "Generates texts, articles, reports, commercial proposals. Saves to files when asked."

    @property
    def tools(self) -> list[str]:
        return ["file_manager", "pptx_creator"]

    async def run(self, task: str) -> AgentResult:
        start = time.time()
        try:
            # Use CoreLoop so Writer can actually use tools (file_manager, pptx_creator)
            loop = CoreLoop(self.llm, self.registry)
            result = await loop.run(task)
            return AgentResult(
                agent=self.name,
                task=task,
                output=result.answer or result.error or "",
                success=result.success,
                duration=time.time() - start,
                error=result.error or "",
            )
        except Exception as e:
            return AgentResult(
                agent=self.name, task=task, output="",
                success=False, duration=time.time() - start, error=str(e),
            )
