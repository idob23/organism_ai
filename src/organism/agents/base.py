from abc import ABC, abstractmethod
from dataclasses import dataclass
from src.organism.llm.base import LLMProvider
from src.organism.tools.registry import ToolRegistry


@dataclass
class AgentResult:
    agent: str
    task: str
    output: str
    success: bool
    duration: float
    error: str = ""


class BaseAgent(ABC):

    def __init__(self, llm: LLMProvider, registry: ToolRegistry) -> None:
        self.llm = llm
        self.registry = registry

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
