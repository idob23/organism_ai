import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.organism.llm.base import LLMProvider, TemperatureLocked, Message
from src.organism.tools.registry import ToolRegistry
from src.organism.logging.error_handler import get_logger

if TYPE_CHECKING:
    from src.organism.memory.manager import MemoryManager

_log = get_logger("agent.base")


@dataclass
class AgentResult:
    agent: str
    task: str
    output: str
    success: bool
    duration: float
    error: str = ""


class BaseAgent(ABC):

    # Subclasses override these to specialize behavior
    temperature: float = 0.5
    max_iterations: int = 3

    def __init__(
        self,
        llm: LLMProvider,
        registry: ToolRegistry,
        memory: "MemoryManager | None" = None,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.memory = memory

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent name."""

    @property
    @abstractmethod
    def description(self) -> str:
        """What this agent specializes in."""

    @property
    @abstractmethod
    def tools(self) -> list[str]:
        """Tool names this agent uses."""

    @abstractmethod
    async def run(self, task: str) -> AgentResult:
        """Execute a task."""

    async def _reflect(self, task: str, result: AgentResult) -> dict | None:
        """Call Haiku to self-evaluate the completed task.

        Returns {"score": int(1-5), "insight": str} or None on failure.
        """
        prompt = (
            f"Task: {task[:300]}\n"
            f"Status: {'SUCCESS' if result.success else 'FAILURE'}\n"
            f"Output: {result.output[:300]}\n\n"
            "Rate 1-5 how well you completed this task. "
            "What could be improved? "
            'Respond JSON: {"score": 1-5, "insight": "one sentence"}'
        )
        try:
            resp = await self.llm.complete(
                messages=[Message(role="user", content=prompt)],
                model_tier="fast",
                max_tokens=80,
            )
            raw = resp.content.strip()
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                data = json.loads(match.group(0))
                score = int(data.get("score", 0))
                insight = str(data.get("insight", "")).strip()
                if 1 <= score <= 5 and insight:
                    return {"score": score, "insight": insight}
        except Exception:
            pass
        return None

    async def _save_reflection(self, task: str, result: AgentResult) -> None:
        """Reflect on result and persist to memory if available."""
        reflection = await self._reflect(task, result)
        if not reflection:
            return
        _log.info(
            f"[{result.agent}] Reflection score={reflection['score']}: {reflection['insight']}"
        )
        if self.memory:
            try:
                await self.memory.save_reflection(
                    self.name, task, reflection["score"], reflection["insight"]
                )
            except Exception:
                pass
