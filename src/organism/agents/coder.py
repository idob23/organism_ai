import time
from .base import BaseAgent, AgentResult
from src.organism.core.loop import CoreLoop
from src.organism.memory.manager import MemoryManager


class CoderAgent(BaseAgent):

    @property
    def name(self) -> str:
        return "coder"

    @property
    def description(self) -> str:
        return "Writes, debugs and runs Python code. Use for algorithms, data processing, calculations, scripts."

    @property
    def tools(self) -> list[str]:
        return ["code_executor", "file_manager"]

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
