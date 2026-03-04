"""Q-8.5: Agent-to-Agent protocol for multi-system collaboration.

Lightweight protocol for delegating tasks between Organism AI instances
(or any MCP-compatible system exposing execute_task).

Architecture:
- PeerRegistry: known peers (other Organism AI instances)
- A2AClient: send task to peer, get result
- DelegateToAgent tool: registered in ToolRegistry, usable by Planner

Protocol flow:
1. Planner decides task needs external data -> selects delegate_to_agent
2. DelegateToAgent sends task to specified peer via MCP
3. Peer executes task, returns result
4. Result injected back into current task flow
"""

import json
from dataclasses import dataclass, field
from typing import Any

from src.organism.tools.base import BaseTool, ToolResult
from src.organism.tools.mcp_client import MCPClient, MCPServerConfig
from src.organism.logging.error_handler import get_logger, log_exception

_log = get_logger("a2a")


@dataclass
class PeerAgent:
    """A known peer Organism AI instance."""
    name: str           # human-readable, e.g. "artel-south"
    url: str            # MCP endpoint, e.g. "http://192.168.2.100:8091"
    api_key: str = ""
    capabilities: list[str] = field(default_factory=list)  # cached from list_capabilities
    enabled: bool = True


class PeerRegistry:
    """Registry of known peer agents."""

    def __init__(self) -> None:
        self._peers: dict[str, PeerAgent] = {}

    def add_peer(self, peer: PeerAgent) -> None:
        self._peers[peer.name] = peer
        _log.info(f"Peer registered: {peer.name} ({peer.url})")

    def remove_peer(self, name: str) -> None:
        self._peers.pop(name, None)

    def get_peer(self, name: str) -> PeerAgent | None:
        return self._peers.get(name)

    def list_peers(self) -> list[PeerAgent]:
        return [p for p in self._peers.values() if p.enabled]

    def to_prompt_hint(self) -> str:
        """Format peer list for injection into planner prompts."""
        peers = self.list_peers()
        if not peers:
            return ""
        lines = ["[Available peer agents for delegation:]"]
        for p in peers:
            caps = f" ({', '.join(p.capabilities)})" if p.capabilities else ""
            lines.append(f"  - {p.name}: {p.url}{caps}")
        return "\n".join(lines)


class A2AClient:
    """Client for sending tasks to peer agents via MCP."""

    async def send_task(self, peer: PeerAgent, task: str, mode: str = "single") -> ToolResult:
        """Delegate a task to a peer agent and return the result."""
        config = MCPServerConfig(
            name=f"a2a_{peer.name}",
            url=peer.url,
            api_key=peer.api_key,
        )
        client = MCPClient(config)

        try:
            result = await client.call_tool("execute_task", {
                "task": task,
                "mode": mode,
            })
            return result
        except Exception as e:
            return ToolResult(
                output="",
                error=f"A2A delegation to '{peer.name}' failed: {e}",
                exit_code=1,
            )

    async def discover_capabilities(self, peer: PeerAgent) -> list[str]:
        """Query peer for its capabilities."""
        config = MCPServerConfig(
            name=f"a2a_{peer.name}",
            url=peer.url,
            api_key=peer.api_key,
        )
        client = MCPClient(config)

        try:
            result = await client.call_tool("list_capabilities", {})
            if result.exit_code == 0:
                data = json.loads(result.output)
                return data.get("tools", [])
        except Exception:
            pass
        return []


class DelegateToAgentTool(BaseTool):
    """Tool that allows Planner to delegate tasks to peer AI agents."""

    def __init__(self, peer_registry: PeerRegistry) -> None:
        self._registry = peer_registry
        self._client = A2AClient()

    @property
    def name(self) -> str:
        return "delegate_to_agent"

    @property
    def description(self) -> str:
        peers = self._registry.list_peers()
        if peers:
            peer_list = ", ".join(p.name for p in peers)
            return (
                f"Delegate a task to another AI agent system. "
                f"Available peers: {peer_list}. "
                f"Use when task requires data or capabilities from another system."
            )
        return (
            "Delegate a task to another AI agent system via A2A protocol. "
            "No peers currently configured."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "peer_name": {
                    "type": "string",
                    "description": "Name of the peer agent to delegate to",
                },
                "task": {
                    "type": "string",
                    "description": "Task to delegate (natural language)",
                },
                "mode": {
                    "type": "string",
                    "enum": ["single", "multi"],
                    "default": "single",
                    "description": "Execution mode on the peer",
                },
            },
            "required": ["peer_name", "task"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        peer_name = input.get("peer_name", "")
        task = input.get("task", "")
        mode = input.get("mode", "single")

        if not peer_name or not task:
            return ToolResult(output="", error="peer_name and task are required", exit_code=1)

        peer = self._registry.get_peer(peer_name)
        if not peer:
            available = [p.name for p in self._registry.list_peers()]
            return ToolResult(
                output="",
                error=f"Peer '{peer_name}' not found. Available: {available}",
                exit_code=1,
            )

        _log.info(f"A2A delegation: '{task[:80]}' -> {peer_name}")
        result = await self._client.send_task(peer, task, mode)

        if result.exit_code == 0:
            _log.info(f"A2A delegation to '{peer_name}' succeeded")
        else:
            _log.warning(f"A2A delegation to '{peer_name}' failed: {result.error}")

        return result
