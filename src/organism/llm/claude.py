import anthropic
from typing import Any
from .base import LLMProvider, LLMResponse, Message
from config.settings import settings


class ClaudeProvider(LLMProvider):

    def __init__(self) -> None:
        self.client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key
        )
        self._models = {
            "fast":     settings.llm_fast_model,
            "balanced": settings.llm_balanced_model,
            "powerful": settings.llm_powerful_model,
        }

    def _get_model(self, tier: str) -> str:
        return self._models.get(tier, self._models["balanced"])

    def _to_anthropic_messages(
        self, messages: list[Message]
    ) -> list[dict[str, Any]]:
        return [{"role": m.role, "content": m.content} for m in messages]

    async def complete(
        self,
        messages: list[Message],
        system: str = "",
        model_tier: str = "balanced",
        max_tokens: int = 4096,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model":      self._get_model(model_tier),
            "max_tokens": max_tokens,
            "messages":   self._to_anthropic_messages(messages),
        }
        if system:
            kwargs["system"] = system

        response = await self.client.messages.create(**kwargs)

        content = ""
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text

        return LLMResponse(
            content=content,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    async def complete_with_tools(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        system: str = "",
        model_tier: str = "balanced",
        max_tokens: int = 4096,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model":      self._get_model(model_tier),
            "max_tokens": max_tokens,
            "messages":   self._to_anthropic_messages(messages),
            "tools":      tools,
        }
        if system:
            kwargs["system"] = system

        response = await self.client.messages.create(**kwargs)

        content = ""
        tool_calls: list[dict[str, Any]] = []

        for block in response.content:
            if hasattr(block, "text"):
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id":    block.id,
                    "name":  block.name,
                    "input": block.input,
                })

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
