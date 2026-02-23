from typing import Any
from tavily import TavilyClient
from .base import BaseTool, ToolResult
from config.settings import settings


class WebSearchTool(BaseTool):

    def __init__(self) -> None:
        self._client = TavilyClient(api_key=settings.tavily_api_key)

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the internet for current information. "
            "Use for news, facts, prices, events  anything that requires up-to-date data. "
            "Returns a list of relevant results with titles, URLs and content snippets."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        query = input["query"]
        max_results = input.get("max_results", 5)

        try:
            response = self._client.search(
                query=query,
                max_results=max_results,
                include_answer=True,
            )

            output_lines = []

            if response.get("answer"):
                output_lines.append(f"Answer: {response['answer']}\n")

            for i, result in enumerate(response.get("results", []), 1):
                output_lines.append(f"{i}. {result['title']}")
                output_lines.append(f"   URL: {result['url']}")
                if result.get("content"):
                    output_lines.append(f"   {result['content'][:200]}")
                output_lines.append("")

            return ToolResult(output="\n".join(output_lines))

        except Exception as e:
            return ToolResult(output="", error=str(e), exit_code=1)
