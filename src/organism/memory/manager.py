from .longterm import LongTermMemory
from .working import WorkingMemory
from .database import init_db


class MemoryManager:

    def __init__(self) -> None:
        self.longterm = LongTermMemory()
        self.working = WorkingMemory()
        self._initialized = False

    async def initialize(self) -> None:
        if not self._initialized:
            await init_db()
            self._initialized = True

    async def on_task_start(self, task: str) -> list[dict]:
        self.working.clear()
        self.working.task = task

        # Search for similar past tasks
        similar = await self.longterm.search_similar(task, limit=3)
        return similar

    async def on_task_end(
        self,
        task: str,
        result: str,
        success: bool,
        duration: float,
        steps_count: int,
        tools_used: list[str],
    ) -> None:
        await self.longterm.save_task(
            task=task,
            result=result,
            success=success,
            duration=duration,
            steps_count=steps_count,
            tools_used=tools_used,
        )

    async def get_stats(self) -> dict:
        return await self.longterm.get_stats()
