import httpx
from bs4 import BeautifulSoup
from typing import Any
from .base import BaseTool, ToolResult


# Sites that consistently block or misbehave
BLOCKED_DOMAINS = [
    "statista.com", "g2.com", "forbes.com",
    "gartner.com", "capterra.com", "trustradius.com",
]


class WebFetchTool(BaseTool):

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "Fetch and parse content from a specific URL. "
            "Use when you have an exact URL to retrieve. "
            "Returns cleaned text content. "
            "Note: some sites (g2.com, statista.com, forbes.com) block bots  use web_search instead."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "max_chars": {"type": "integer", "default": 3000},
            },
            "required": ["url"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        url: str = input["url"]
        max_chars: int = input.get("max_chars", 3000)

        # Check blocked domains
        for blocked in BLOCKED_DOMAINS:
            if blocked in url:
                return ToolResult(
                    output=f"Domain blocked (known bot protection): {blocked}. Use web_search instead.",
                    error="",
                    exit_code=0,
                )

        try:
            async with httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                max_redirects=3,
                headers={"User-Agent": "Mozilla/5.0 (compatible; OrganismAI/1.0)"},
            ) as client:
                response = await client.get(url)

                if response.status_code in (403, 404, 410, 429, 451):
                    return ToolResult(
                        output=f"Page not accessible (HTTP {response.status_code}): {url}. Use web_search instead.",
                        error="",
                        exit_code=0,
                    )

                response.raise_for_status()

        except httpx.TooManyRedirects:
            return ToolResult(
                output=f"Too many redirects for {url}. Site likely requires login. Use web_search instead.",
                error="",
                exit_code=0,
            )
        except httpx.TimeoutException:
            return ToolResult(output="", error=f"Timeout fetching {url}", exit_code=1)
        except Exception as e:
            return ToolResult(output="", error=str(e), exit_code=1)

        try:
            soup = BeautifulSoup(response.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
                tag.decompose()

            text = soup.get_text(separator="\n", strip=True)
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            content = "\n".join(lines)[:max_chars]

            title = soup.title.string.strip() if soup.title else ""
            if title:
                content = f"{title}\n\n{content}"

        except Exception as e:
            return ToolResult(output="", error=f"Parse error: {e}", exit_code=1)

        return ToolResult(output=content, error="", exit_code=0)
