from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class ToolResult:
    tool_use_id: str
    content: str


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class LLMProvider(ABC):

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        system: str = "",
        model_tier: str = "balanced",
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Simple completion without tools."""

    @abstractmethod
    async def complete_with_tools(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        system: str = "",
        model_tier: str = "balanced",
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Completion with function calling."""


class TemperatureLocked(LLMProvider):
    """Wraps any LLMProvider and pins temperature for all calls.

    Agents pass this to CoreLoop so their temperature propagates through
    Planner and Evaluator without modifying those classes.
    """

    def __init__(self, provider: LLMProvider, temperature: float) -> None:
        self._provider = provider
        self._temperature = temperature

    async def complete(
        self,
        messages: list[Message],
        system: str = "",
        model_tier: str = "balanced",
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> LLMResponse:
        return await self._provider.complete(
            messages, system=system, model_tier=model_tier,
            max_tokens=max_tokens, temperature=self._temperature,
        )

    async def complete_with_tools(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        system: str = "",
        model_tier: str = "balanced",
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> LLMResponse:
        return await self._provider.complete_with_tools(
            messages, tools, system=system, model_tier=model_tier,
            max_tokens=max_tokens, temperature=self._temperature,
        )
