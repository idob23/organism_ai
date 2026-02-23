from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    output: str
    error: str = ""
    exit_code: int = 0

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.error


class BaseTool(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool name."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description for LLM."""

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]:
        """JSON schema for tool input."""

    @abstractmethod
    async def execute(self, input: dict[str, Any]) -> ToolResult:
        """Execute the tool and return result."""

    def to_json_schema(self) -> dict[str, Any]:
        """Format for Claude API function calling."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
