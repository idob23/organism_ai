import time
from .base import BaseAgent, AgentResult
from src.organism.llm.base import Message


class WriterAgent(BaseAgent):

    @property
    def name(self) -> str:
        return "writer"

    @property
    def description(self) -> str:
        return "Generates texts, articles, emails, social media content, summaries. Use for any writing tasks."

    @property
    def tools(self) -> list[str]:
        return ["file_manager"]

    async def run(self, task: str) -> AgentResult:
        import time
        start = time.time()
        try:
            response = await self.llm.complete(
                messages=[Message(role="user", content=task)],
                system=(
                    "You are a professional writer. Create high-quality, engaging content. "
                    "Write in the same language as the task. Be concise and impactful."
                ),
                model_tier="balanced",
            )
            return AgentResult(
                agent=self.name,
                task=task,
                output=response.content,
                success=True,
                duration=time.time() - start,
            )
        except Exception as e:
            return AgentResult(
                agent=self.name, task=task, output="",
                success=False, duration=time.time() - start, error=str(e),
            )
