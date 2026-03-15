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
            "Three search modes:\n"
            "(1) Semantic: provide query to find similar tasks by meaning.\n"
            "(2) Date: provide date_from (and optionally date_to) to get ALL tasks for a period. "
            "Date search returns all tasks (no need to specify limit).\n"
            "(3) Combined: provide both query AND date_from to find specific tasks "
            "within a date range.\n"
            "Use proactively when creating reports that need previously calculated numbers, "
            "when user references past work, or when task mentions quantities "
            "that may have been calculated before."
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
                "date_from": {
                    "type": "string",
                    "description": (
                        "Start date filter (YYYY-MM-DD). "
                        "Use when user asks about specific date or period."
                    ),
                },
                "date_to": {
                    "type": "string",
                    "description": (
                        "End date filter (YYYY-MM-DD). "
                        "Defaults to date_from if only one date mentioned."
                    ),
                },
            },
            "required": [],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        query = input.get("query", "")
        limit = input.get("limit", 5)
        date_from = input.get("date_from", "")
        date_to = input.get("date_to", "")

        if not query and not date_from:
            return ToolResult(output="", error="Provide query or date_from", exit_code=1)

        if not self._memory:
            return ToolResult(
                output="Memory unavailable. Cannot search past tasks.",
                error="",
                exit_code=0,
            )

        try:
            if date_from and query:
                # Combined: semantic search within date range
                if not date_to:
                    date_to = date_from
                results = await self._memory.longterm.search_similar_in_date_range(
                    query, date_from, date_to, limit=limit,
                )
            elif date_from:
                # Pure date: all tasks for the period
                if not date_to:
                    date_to = date_from
                results = await self._memory.longterm.get_tasks_by_date_range(
                    date_from, date_to, limit=50,
                )
            else:
                # Pure semantic (existing behavior)
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

        # Compact format for date queries, full format for semantic
        if date_from and not query:
            lines = []
            for i, item in enumerate(results, 1):
                task_text = item.get("task", "")[:100]
                quality = item.get("quality_score", 0)
                created = item.get("created_at", "")[:16]
                lines.append(f"{i}. [{created}] {task_text} (q={quality:.2f})")
            output = (
                f"Found {len(results)} task(s) for {date_from}"
                + (f" to {date_to}" if date_to != date_from else "")
                + ":\n" + "\n".join(lines)
            )
        else:
            # Full format (semantic or combined)
            lines = []
            for i, item in enumerate(results, 1):
                task_text = item.get("task", "")[:200]
                result_text = (item.get("result") or "")[:300].replace("\n", " ")
                tools = item.get("tools_used") or []
                tool_str = ", ".join(tools) if tools else "none"
                quality = item.get("quality_score", 0)
                date_str = ""
                if item.get("created_at"):
                    date_str = f" | Date: {item['created_at'][:10]}"
                lines.append(
                    f"{i}. Task: {task_text}\n"
                    f"   Result: {result_text}\n"
                    f"   Tools: {tool_str} | Quality: {quality:.2f}{date_str}"
                )
            output = f"Found {len(results)} result(s):\n\n" + "\n\n".join(lines)

        return ToolResult(output=output, error="", exit_code=0)
