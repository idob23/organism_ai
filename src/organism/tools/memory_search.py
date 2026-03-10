from typing import Any

from src.organism.logging.error_handler import get_logger
from .base import BaseTool, ToolResult

_log = get_logger("tools.memory_search")


class MemorySearchTool(BaseTool):

    def __init__(self, memory=None) -> None:
        self._memory = memory

    def set_memory(self, memory) -> None:
        self._memory = memory

    @property
    def name(self) -> str:
        return "memory_search"

    @property
    def description(self) -> str:
        return (
            "Search long-term memory for past tasks, calculations, and results. "
            "Use proactively when: "
            "(1) creating reports/summaries that need previously calculated numbers "
            "\u2014 search BEFORE generating content to use real data instead of estimates; "
            "(2) user references past work: 'remember we agreed', 'that file', 'last time'; "
            "(3) task mentions specific quantities (fuel, payroll, costs) that may have been "
            "calculated before. "
            "Always try memory first when building documents with financial or operational data."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query describing what to look for in memory",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        query = input.get("query", "")
        limit = input.get("limit", 5)

        if not query:
            return ToolResult(output="", error="Query is required", exit_code=1)

        if not self._memory:
            return ToolResult(
                output="Memory unavailable. Cannot search past tasks.",
                error="",
                exit_code=0,
            )

        try:
            results = await self._memory.longterm.search_similar(
                query, limit=limit, llm=self._memory.llm,
            )
        except Exception as e:
            _log.warning("memory_search failed: %s", e)
            return ToolResult(
                output="Memory search failed. Try rephrasing your query.",
                error=str(e),
                exit_code=1,
            )

        if not results:
            return ToolResult(
                output="No matching tasks found in memory. Try a different query phrasing.",
                error="",
                exit_code=0,
            )

        lines = []
        for i, item in enumerate(results, 1):
            task_text = item.get("task", "")[:200]
            result_text = (item.get("result") or "")[:300].replace("\n", " ")
            tools = item.get("tools_used") or []
            tool_str = ", ".join(tools) if tools else "none"
            quality = item.get("quality_score", 0)
            lines.append(
                f"{i}. Task: {task_text}\n"
                f"   Result: {result_text}\n"
                f"   Tools: {tool_str} | Quality: {quality:.2f}"
            )

        return ToolResult(
            output=f"Found {len(results)} result(s):\n\n" + "\n\n".join(lines),
            error="",
            exit_code=0,
        )
