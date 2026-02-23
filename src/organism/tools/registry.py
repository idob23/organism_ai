from typing import Any
from .base import BaseTool


class ToolRegistry:

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found in registry")
        return self._tools[name]

    def list_all(self) -> list[str]:
        return list(self._tools.keys())

    def to_json_schema(self) -> list[dict[str, Any]]:
        """Return all tools formatted for Claude API."""
        return [tool.to_json_schema() for tool in self._tools.values()]
