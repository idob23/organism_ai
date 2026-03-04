"""Q-8.4: Organism AI as MCP server.

Exposes Organism AI capabilities via MCP protocol so other AI systems
can delegate tasks through a standard interface.

Usage:
    python main.py --serve-mcp --mcp-port 8091
"""

import json
import time
from typing import Any

import aiohttp.web

from src.organism.core.loop import CoreLoop
from src.organism.memory.manager import MemoryManager
from src.organism.tools.registry import ToolRegistry
from src.organism.logging.error_handler import get_logger, log_exception

_log = get_logger("mcp_serve")

# Tool definitions exposed via MCP
ORGANISM_TOOLS = [
    {
        "name": "execute_task",
        "description": "Execute a task using Organism AI autonomous executor. Supports: calculations, document generation, web research, data analysis, presentations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task description in natural language (Russian or English)"},
                "mode": {"type": "string", "enum": ["single", "multi"], "default": "single", "description": "single = CoreLoop, multi = Orchestrator (multi-agent)"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "get_stats",
        "description": "Get Organism AI system statistics: success rate, task count, average quality, tool usage",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "search_knowledge",
        "description": "Search Organism AI memory for past tasks, results, and knowledge rules",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "default": 5, "description": "Max results"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_capabilities",
        "description": "List all available tools and agents in this Organism AI instance",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


class OrganismMCPServer:

    def __init__(self, loop: CoreLoop, memory: MemoryManager | None, registry: ToolRegistry) -> None:
        self.loop = loop
        self.memory = memory
        self.registry = registry
        self._handlers: dict[str, Any] = {
            "execute_task": self._h_execute_task,
            "get_stats": self._h_get_stats,
            "search_knowledge": self._h_search_knowledge,
            "list_capabilities": self._h_list_capabilities,
        }

    async def handle_tools_list(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        return aiohttp.web.json_response({"tools": ORGANISM_TOOLS})

    async def handle_tools_call(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        try:
            body = await request.json()
        except Exception:
            return aiohttp.web.json_response(
                {"isError": True, "content": [{"type": "text", "text": "Invalid JSON"}]},
                status=400,
            )

        tool_name = body.get("name", "")
        arguments = body.get("arguments", {})

        handler = self._handlers.get(tool_name)
        if not handler:
            return aiohttp.web.json_response(
                {"isError": True, "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}]},
                status=404,
            )

        try:
            result = await handler(arguments)
            return aiohttp.web.json_response(
                {"content": [{"type": "text", "text": result}]}
            )
        except Exception as e:
            log_exception(_log, f"MCP serve error: {tool_name}", e)
            return aiohttp.web.json_response(
                {"isError": True, "content": [{"type": "text", "text": f"Error: {e}"}]},
                status=500,
            )

    async def _h_execute_task(self, args: dict) -> str:
        task = args.get("task", "")
        mode = args.get("mode", "single")

        if not task:
            return "Error: 'task' is required"

        t0 = time.time()

        if mode == "multi":
            from src.organism.agents.orchestrator import Orchestrator
            orch = Orchestrator(self.loop.llm, self.registry, memory=self.memory)
            result = await orch.run(task, verbose=False)
            duration = time.time() - t0
            return json.dumps({
                "success": result.success,
                "output": result.output[:3000],
                "duration": round(duration, 1),
                "agents": [r.agent for r in result.agent_results],
            }, ensure_ascii=False)
        else:
            result = await self.loop.run(task, verbose=False)
            duration = time.time() - t0
            # Extract tools from step logs
            tools = sorted({s.tool for s in result.steps if s.success})
            return json.dumps({
                "success": result.success,
                "output": result.output[:3000],
                "quality_score": round(result.quality_score, 2),
                "duration": round(duration, 1),
                "tools_used": tools,
            }, ensure_ascii=False)

    async def _h_get_stats(self, args: dict) -> str:
        from src.organism.commands.handler import CommandHandler
        handler = CommandHandler()
        return await handler.handle("/stats", self.memory)

    async def _h_search_knowledge(self, args: dict) -> str:
        query = args.get("query", "")
        limit = args.get("limit", 5)

        if not query:
            return "Error: 'query' is required"

        if not self.memory:
            return "Memory not available (no database connection)"

        try:
            results = await self.memory.longterm.search_similar(query, limit=limit)
            if not results:
                return "No matching results found."

            items = []
            for r in results:
                items.append({
                    "task": r.get("task", "")[:200],
                    "result": r.get("result", "")[:200],
                    "quality": r.get("quality_score", 0),
                    "tools": r.get("tools_used", []),
                })
            return json.dumps(items, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"Search error: {e}"

    async def _h_list_capabilities(self, args: dict) -> str:
        tools = self.registry.list_all()
        agents = ["coder", "researcher", "writer", "analyst"]
        mcp_servers = self.registry.list_mcp_servers() if hasattr(self.registry, "list_mcp_servers") else []

        return json.dumps({
            "tools": tools,
            "agents": agents,
            "mcp_servers": mcp_servers,
            "version": "Organism AI v8.4",
        }, ensure_ascii=False, indent=2)


def create_organism_app(
    loop: CoreLoop, memory: MemoryManager | None, registry: ToolRegistry
) -> aiohttp.web.Application:
    server = OrganismMCPServer(loop, memory, registry)
    app = aiohttp.web.Application()
    app.router.add_post("/tools/list", server.handle_tools_list)
    app.router.add_post("/tools/call", server.handle_tools_call)
    return app
