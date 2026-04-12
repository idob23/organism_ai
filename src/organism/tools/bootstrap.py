"""CAPABILITY-1: Unified tool registry builder.

Single source of truth for tool registration. Used by main.py and benchmark.py.
Supports optional personality-based filtering: tools not allowed by the
active personality are silently skipped at registration time.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.organism.logging.error_handler import get_logger
from src.organism.tools.registry import ToolRegistry
from config.settings import settings

if TYPE_CHECKING:
    from src.organism.core.personality import PersonalityConfig

_log = get_logger("tools.bootstrap")


def build_registry(
    personality: "PersonalityConfig | None" = None,
    dev_mode: bool | None = None,
) -> ToolRegistry:
    """Build a ToolRegistry with all available tools.

    Args:
        personality: If provided, tools are filtered via
            personality.is_tool_allowed(). None = all tools pass.
        dev_mode: Override for settings.dev_mode. None = use settings.
    """
    if dev_mode is None:
        dev_mode = settings.dev_mode

    registry = ToolRegistry()

    def _register(tool: "BaseTool") -> None:  # noqa: F821
        name = tool.name
        if personality and not personality.is_tool_allowed(name):
            _log.info("tool %s filtered out by personality %s", name, personality.artel_id)
            return
        registry.register(tool)

    # --- Always-on tools (order matches main.py production) ---
    from src.organism.tools.code_executor import CodeExecutorTool
    try:
        _register(CodeExecutorTool())
    except Exception:
        _log.warning("Docker unavailable \u2014 code_executor skipped")

    from src.organism.tools.pptx_creator import PptxCreatorTool
    _register(PptxCreatorTool())

    from src.organism.tools.text_writer import TextWriterTool
    _register(TextWriterTool())

    from src.organism.tools.web_fetch import WebFetchTool
    _register(WebFetchTool())

    from src.organism.tools.file_manager import FileManagerTool
    _register(FileManagerTool())

    from src.organism.tools.duplicate_finder import DuplicateFinderTool
    _register(DuplicateFinderTool())

    from src.organism.tools.pdf_tool import PdfTool
    _register(PdfTool())

    from src.organism.tools.memory_search import MemorySearchTool
    _register(MemorySearchTool())

    from src.organism.tools.manage_agents import ManageAgentsTool
    _register(ManageAgentsTool())

    from src.organism.tools.manage_schedule import ManageScheduleTool
    _register(ManageScheduleTool())

    # --- Conditional: web_search (tavily) ---
    if settings.tavily_api_key:
        from src.organism.tools.web_search import WebSearchTool
        _register(WebSearchTool())

    # --- Conditional: telegram_sender (main.py only, needs token) ---
    if settings.telegram_bot_token:
        from src.organism.tools.telegram_sender import TelegramSenderTool
        _register(TelegramSenderTool(settings.telegram_bot_token))

    # --- Conditional: MCP servers (async connection queued) ---
    if settings.mcp_servers:
        try:
            import json as _json
            from src.organism.tools.mcp_client import MCPServerConfig
            servers = _json.loads(settings.mcp_servers)
            registry._pending_mcp = [
                MCPServerConfig(
                    name=srv.get("name", "unknown"),
                    url=srv.get("url", ""),
                    api_key=srv.get("api_key", ""),
                    enabled=srv.get("enabled", True),
                    artel_id=srv.get("artel_id", ""),
                    timeout=srv.get("timeout", 30),
                )
                for srv in servers
            ]
        except Exception:
            pass

    # --- Conditional: dev_review (DEV_MODE only) ---
    if dev_mode:
        from src.organism.tools.dev_review import DevReviewTool
        _register(DevReviewTool())

    # --- Conditional: A2A peers ---
    if settings.a2a_peers:
        try:
            import json as _json2
            from src.organism.a2a.protocol import PeerAgent, PeerRegistry, DelegateToAgentTool
            peers_data = _json2.loads(settings.a2a_peers)
            peer_reg = PeerRegistry()
            for p in peers_data:
                peer_reg.add_peer(PeerAgent(
                    name=p.get("name", "unknown"),
                    url=p.get("url", ""),
                    api_key=p.get("api_key", ""),
                ))
            if peer_reg.list_peers():
                _register(DelegateToAgentTool(peer_reg))
        except Exception:
            pass

    return registry
