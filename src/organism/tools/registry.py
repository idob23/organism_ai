from typing import Any
from .base import BaseTool
from .mcp_client import MCPClient, MCPServerConfig, MCPTool


class ToolRegistry:

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._mcp_clients: dict[str, MCPClient] = {}  # name -> client

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

    # ── MCP server management (Q-8.1) ────────────────────────────────────

    async def register_mcp_server(self, config: MCPServerConfig) -> int:
        """Connect to an MCP server, discover tools, register them.

        Returns number of tools registered. Returns 0 on failure.
        Tool names are prefixed: mcp_{server_name}_{tool_name}
        """
        if not config.enabled:
            return 0

        client = MCPClient(config)
        tools = await client.discover_tools()

        if not tools:
            return 0

        self._mcp_clients[config.name] = client
        registered = 0

        for descriptor in tools:
            tool_name = descriptor.get("name", "")
            if not tool_name:
                continue
            mcp_tool = MCPTool(client, descriptor)
            try:
                self._tools[mcp_tool.name] = mcp_tool  # direct set, skip duplicate check
                registered += 1
            except Exception:
                pass

        return registered

    def unregister_mcp_server(self, server_name: str) -> int:
        """Remove all tools from a specific MCP server.

        Returns number of tools removed.
        """
        prefix = f"mcp_{server_name}_"
        to_remove = [name for name in self._tools if name.startswith(prefix)]
        for name in to_remove:
            del self._tools[name]
        self._mcp_clients.pop(server_name, None)
        return len(to_remove)

    def list_mcp_servers(self) -> list[str]:
        """Return names of connected MCP servers."""
        return list(self._mcp_clients.keys())
