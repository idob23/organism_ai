"""Q-8.1: MCP client for connecting to external MCP servers.

Implements a lightweight MCP client that can:
1. Connect to an MCP server via HTTP
2. Discover available tools (tools/list)
3. Invoke tools (tools/call)
4. Wrap MCP tools as BaseTool instances for ToolRegistry
"""
import json
from dataclasses import dataclass
from typing import Any

import httpx

from .base import BaseTool, ToolResult
from src.organism.logging.error_handler import get_logger, log_exception

_log = get_logger("mcp.client")

DEFAULT_TIMEOUT = 30   # seconds for MCP tool calls
DISCOVERY_TIMEOUT = 10  # seconds for tools/list


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server connection."""

    name: str           # human-readable name, e.g. "1c-artel"
    url: str            # base URL, e.g. "http://192.168.1.100:8080"
    api_key: str = ""   # optional auth token
    enabled: bool = True


class MCPClient:
    """Lightweight MCP client for HTTP-based servers."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._tools_cache: list[dict] | None = None

    async def discover_tools(self) -> list[dict]:
        """Call tools/list on the MCP server. Returns list of tool descriptors.

        Each descriptor: {"name": str, "description": str, "inputSchema": dict}
        Caches result after first call. Returns [] on error.
        """
        if self._tools_cache is not None:
            return self._tools_cache

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        try:
            async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.config.url.rstrip('/')}/tools/list",
                    headers=headers,
                    json={},
                )
                resp.raise_for_status()
                data = resp.json()
                tools = data.get("tools", [])
                self._tools_cache = tools
                _log.info(f"MCP '{self.config.name}': discovered {len(tools)} tools")
                return tools
        except Exception as e:
            log_exception(_log, f"MCP '{self.config.name}' discovery failed", e)
            return []

    async def call_tool(self, tool_name: str, arguments: dict) -> ToolResult:
        """Invoke a tool on the MCP server.

        Returns ToolResult with output from server or error.
        """
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.config.url.rstrip('/')}/tools/call",
                    headers=headers,
                    json={"name": tool_name, "arguments": arguments},
                )
                resp.raise_for_status()
                data = resp.json()

                # MCP response format: {"content": [{"type": "text", "text": "..."}]}
                content = data.get("content", [])
                text_parts: list[str] = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(item.get("text", ""))

                output = "\n".join(text_parts) if text_parts else json.dumps(data)

                is_error = data.get("isError", False)
                return ToolResult(
                    output=output,
                    error="" if not is_error else output,
                    exit_code=1 if is_error else 0,
                )
        except httpx.TimeoutException:
            return ToolResult(
                output="", error=f"MCP timeout ({DEFAULT_TIMEOUT}s)", exit_code=-1,
            )
        except Exception as e:
            return ToolResult(output="", error=f"MCP call failed: {e}", exit_code=1)

    def invalidate_cache(self) -> None:
        """Force re-discovery on next call."""
        self._tools_cache = None


class MCPTool(BaseTool):
    """Wraps a single MCP server tool as a BaseTool for ToolRegistry.

    Makes MCP tools indistinguishable from local tools to the rest of the system.
    """

    def __init__(self, mcp_client: MCPClient, tool_descriptor: dict) -> None:
        self._client = mcp_client
        self._descriptor = tool_descriptor
        self._name = f"mcp_{mcp_client.config.name}_{tool_descriptor['name']}"
        self._remote_name = tool_descriptor["name"]

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        desc = self._descriptor.get("description", "")
        server = self._client.config.name
        return f"[MCP:{server}] {desc}"

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._descriptor.get(
            "inputSchema", {"type": "object", "properties": {}},
        )

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        return await self._client.call_tool(self._remote_name, input)
