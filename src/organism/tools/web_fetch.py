from typing import Any
import httpx
from bs4 import BeautifulSoup
from .base import BaseTool, ToolResult


class WebFetchTool(BaseTool):

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "Fetch and parse content from a specific URL. "
            "Use when you have a direct URL and need its full content. "
            "Returns cleaned text content of the page."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to fetch",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return (default: 3000)",
                    "default": 3000,
                },
            },
            "required": ["url"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        url = input["url"]
        max_chars = input.get("max_chars", 3000)

        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                response = await client.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; OrganismAI/1.0)"}
                )
                response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # Remove noise
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()

            text = soup.get_text(separator="\n", strip=True)

            # Clean up blank lines
            lines = [l for l in text.splitlines() if l.strip()]
            clean = "\n".join(lines)[:max_chars]

            return ToolResult(output=clean)

        except Exception as e:
            return ToolResult(output="", error=str(e), exit_code=1)
