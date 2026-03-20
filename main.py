import asyncio
import argparse
import sys

from src.organism.llm.claude import ClaudeProvider
from src.organism.tools.code_executor import CodeExecutorTool
from src.organism.tools.web_search import WebSearchTool
from src.organism.tools.web_fetch import WebFetchTool
from src.organism.tools.file_manager import FileManagerTool
from src.organism.tools.telegram_sender import TelegramSenderTool
from src.organism.tools.pptx_creator import PptxCreatorTool
from src.organism.tools.text_writer import TextWriterTool
from src.organism.tools.registry import ToolRegistry
from src.organism.core.loop import CoreLoop
from src.organism.memory.manager import MemoryManager
from config.settings import settings


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(CodeExecutorTool())
    registry.register(PptxCreatorTool())
    registry.register(TextWriterTool())
    registry.register(WebFetchTool())
    registry.register(FileManagerTool())
    from src.organism.tools.duplicate_finder import DuplicateFinderTool
    registry.register(DuplicateFinderTool())
    from src.organism.tools.pdf_tool import PdfTool
    registry.register(PdfTool())
    from src.organism.tools.memory_search import MemorySearchTool
    registry.register(MemorySearchTool())
    from src.organism.tools.manage_agents import ManageAgentsTool
    registry.register(ManageAgentsTool())
    from src.organism.tools.manage_schedule import ManageScheduleTool
    registry.register(ManageScheduleTool())
    if settings.tavily_api_key:
        registry.register(WebSearchTool())
    if settings.telegram_bot_token:
        registry.register(TelegramSenderTool(settings.telegram_bot_token))
    # Q-8.1: queue MCP servers for async connection (build_registry is sync)
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
                )
                for srv in servers
            ]
        except Exception:
            pass
    # Q-8.5: Register A2A peer agents
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
                registry.register(DelegateToAgentTool(peer_reg))
        except Exception:
            pass
    return registry


async def _connect_mcp(registry: ToolRegistry) -> None:
    """Connect pending MCP servers queued by build_registry()."""
    for config in getattr(registry, "_pending_mcp", []):
        try:
            count = await registry.register_mcp_server(config)
            if count > 0:
                print(f"  MCP '{config.name}': {count} tools registered")
        except Exception:
            pass


def _load_personality():
    from src.organism.core.personality import PersonalityConfig
    p = PersonalityConfig(artel_id=settings.artel_id)
    p.load()
    return p


def build_loop(registry: ToolRegistry | None = None, personality=None, with_orchestrator: bool = False) -> CoreLoop:
    llm = ClaudeProvider()
    reg = registry or build_registry()
    memory = MemoryManager() if settings.database_url else None
    p = personality if personality is not None else _load_personality()
    orch = None
    factory = None
    if with_orchestrator:
        from src.organism.agents.orchestrator import Orchestrator
        from src.organism.agents.factory import AgentFactory
        from src.organism.agents.meta_orchestrator import MetaOrchestrator
        base_orch = Orchestrator(llm, reg, memory=memory)
        factory = AgentFactory()
        orch = MetaOrchestrator(base_orch, llm, factory)
    loop = CoreLoop(llm, reg, memory=memory, personality=p, orchestrator=orch, factory=factory)
    if orch is not None and hasattr(orch, "set_loop"):
        orch.set_loop(loop)
    # AGENT-UX: inject dependencies into ManageAgentsTool
    try:
        mat = reg.get("manage_agents")
        if factory:
            mat.set_factory(factory)
        mat.set_llm(llm)
        if orch:
            mat.set_orchestrator(orch)
    except KeyError:
        pass
    return loop


async def run_single(task: str, use_orchestrator: bool = False) -> None:
    from src.organism.commands.handler import CommandHandler
    handler = CommandHandler()
    if handler.is_command(task):
        loop = build_loop(with_orchestrator=True)
        await _connect_mcp(loop.registry)
        from src.organism.agents.factory import AgentFactory
        factory = getattr(loop, 'factory', None) or AgentFactory()
        handler = CommandHandler(
            personality=loop.personality,
            factory=factory,
            loop=loop,
        )
        memory = MemoryManager() if settings.database_url else None
        print(await handler.handle(task, memory, user_id="local"))
        return

    if use_orchestrator:
        from src.organism.agents.orchestrator import Orchestrator
        llm = ClaudeProvider()
        registry = build_registry()
        await _connect_mcp(registry)
        memory = MemoryManager() if settings.database_url else None
        if memory:
            await memory.initialize()
        orch = Orchestrator(llm, registry, memory=memory)
        result = await orch.run(task)
    else:
        loop = build_loop()
        await _connect_mcp(loop.registry)
        result = await loop.run(task)

    # Drain background tasks (CausalAnalyzer, TemplateExtractor) before exit
    _pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if _pending:
        try:
            await asyncio.wait(_pending, timeout=5.0)
        except Exception:
            pass

    if not result.success:
        print(f"\nFailed: {result.error}")
        sys.exit(1)


async def run_interactive(use_orchestrator: bool = False) -> None:
    from src.organism.channels.gateway import Gateway
    from src.organism.channels.cli_channel import CLIChannel

    if use_orchestrator:
        from src.organism.agents.orchestrator import Orchestrator
        llm = ClaudeProvider()
        registry = build_registry()
        await _connect_mcp(registry)
        memory = MemoryManager() if settings.database_url else None
        if memory:
            await memory.initialize()
        orch = Orchestrator(llm, registry, memory=memory)
        # Orchestrator mode — still direct (no Gateway wrapper yet)
        from src.organism.commands.handler import CommandHandler
        handler = CommandHandler()
        print("Organism AI [Multi-Agent]  interactive mode. Type 'exit' to quit.\n")
        while True:
            try:
                task = input("Task> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break
            if not task:
                continue
            if task.lower() in ("exit", "quit", "q"):
                print("Bye.")
                break
            if handler.is_command(task):
                print(await handler.handle(task, memory))
                continue
            try:
                await orch.run(task)
            except KeyboardInterrupt:
                print("\nInterrupted.")
    else:
        loop = build_loop()
        await _connect_mcp(loop.registry)
        gateway = Gateway(loop)
        channel = CLIChannel(gateway)
        gateway.register_channel("cli", channel)
        await channel.start()


async def _heartbeat_writer() -> None:
    """Write current timestamp to data/heartbeat every 30 seconds."""
    import os
    import time
    heartbeat_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data", "heartbeat"
    )
    os.makedirs(os.path.dirname(heartbeat_path), exist_ok=True)
    while True:
        try:
            with open(heartbeat_path, "w") as f:
                f.write(str(time.time()))
        except Exception:
            pass
        await asyncio.sleep(30)


async def run_telegram() -> None:
    from src.organism.channels.telegram import TelegramChannel
    from src.organism.channels.gateway import Gateway
    from src.organism.core.scheduler import ProactiveScheduler
    from src.organism.core.human_approval import HumanApproval
    from src.organism.tools.confirm_user import ConfirmUserTool
    if not settings.telegram_bot_token:
        print("Error: TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)
    registry = build_registry()
    await _connect_mcp(registry)

    # --- Human-in-the-loop approval ---
    async def _send_approval(message: str) -> None:
        """Send approval request to allowed Telegram users."""
        from aiogram import Bot
        bot = Bot(token=settings.telegram_bot_token)
        try:
            for uid in (settings.allowed_user_ids or []):
                try:
                    await bot.send_message(uid, message)
                except Exception:
                    pass
        finally:
            await bot.session.close()

    approval = HumanApproval(send_fn=_send_approval)
    registry.register(ConfirmUserTool(approval=approval))

    loop = build_loop(registry, with_orchestrator=True)

    # --- Proactive scheduler ---
    async def _notify(artel_id: str, message: str, channel_id: str = "", requires_approval: bool = False) -> None:
        """Send scheduled task result to allowed Telegram users and channel.

        FIX-90: if channel_id + requires_approval, send review request to personal chat only.
        Human approves via /publish <id> or rejects via /reject_post <id>.
        """
        import uuid as _uuid
        from aiogram import Bot
        bot = Bot(token=settings.telegram_bot_token)
        try:
            if channel_id and requires_approval:
                # Review mode: send to personal chat with approval buttons, NOT to channel
                short_id = _uuid.uuid4().hex[:8]
                scheduler.add_pending_publication(short_id, message, channel_id, "")
                review_msg = (
                    "\U0001f4dd \u041d\u0410 \u041f\u0420\u041e\u0412\u0415\u0420\u041a\u0423:\n\n"
                    f"{message}\n\n"
                    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
                    f"\u041e\u043f\u0443\u0431\u043b\u0438\u043a\u043e\u0432\u0430\u0442\u044c: /publish {short_id}\n"
                    f"\u041e\u0442\u043a\u043b\u043e\u043d\u0438\u0442\u044c: /reject_post {short_id}"
                )
                for uid in (settings.allowed_user_ids or []):
                    try:
                        await bot.send_message(uid, review_msg)
                    except Exception:
                        pass
            else:
                # Normal mode: personal chat + channel (if specified)
                for uid in (settings.allowed_user_ids or []):
                    try:
                        await bot.send_message(uid, message)
                    except Exception:
                        pass
                if channel_id:
                    try:
                        await bot.send_message(channel_id, message)
                    except Exception:
                        pass
        finally:
            await bot.session.close()

    scheduler = ProactiveScheduler(
        task_runner=loop.run,
        notify=_notify if settings.allowed_user_ids else None,
    )
    await scheduler.load_and_sync(settings.artel_id)
    loop.scheduler = scheduler
    # SCHED-1b: inject scheduler into ManageScheduleTool
    try:
        mst = loop.registry.get("manage_schedule")
        mst.set_scheduler(scheduler)
    except KeyError:
        pass
    scheduler.start()

    # MON-1: Start error monitoring
    error_notifier = None
    if settings.error_monitor_chat_id:
        from src.organism.monitoring.error_notifier import ErrorNotifier
        error_notifier = ErrorNotifier()
        if error_notifier.is_configured:
            await error_notifier.start()
            from src.organism.logging.error_handler import get_logger
            _bot_type = "dedicated" if settings.error_bot_token else "main"
            get_logger("main").info(
                "Error monitoring active (%s bot) -> chat %s", _bot_type, settings.error_monitor_chat_id
            )

    gateway = Gateway(loop, scheduler=scheduler, approval=approval)
    channel = TelegramChannel(gateway)
    gateway.register_channel("telegram", channel)

    # DOCKER-PROD: heartbeat for Docker health check
    heartbeat_task = asyncio.create_task(_heartbeat_writer())

    print("Organism AI Telegram bot starting...")
    try:
        await channel.start()
    finally:
        heartbeat_task.cancel()
        scheduler.stop()
        if error_notifier:
            await error_notifier.stop()


async def run_stats() -> None:
    memory = MemoryManager()
    await memory.initialize()
    stats = await memory.get_stats()
    print(f"Total tasks:     {stats['total_tasks']}")
    print(f"Successful:      {stats['successful_tasks']}")
    print(f"Success rate:    {stats['success_rate']}%")
    print(f"Avg duration:    {stats['avg_duration']}s")


async def run_analyze() -> None:
    from src.organism.self_improvement.optimizer import PromptOptimizer
    llm = ClaudeProvider()
    optimizer = PromptOptimizer(llm)
    print("Analyzing performance...\n")
    recommendations = await optimizer.analyze_and_recommend()
    print(recommendations)


async def run_improve(days: int = 7) -> None:
    from src.organism.self_improvement.auto_improver import AutoImprover
    from src.organism.memory.knowledge_base import KnowledgeBase
    if not settings.database_url:
        print("Error: DATABASE_URL not configured")
        return
    llm = ClaudeProvider()
    memory = MemoryManager()
    await memory.initialize()
    kb = KnowledgeBase()
    print(f"Running auto-improvement cycle (last {days} days)...")
    summary = await AutoImprover().run_cycle(memory, llm, kb, days=days, human_approval=None)
    print(f"Done:")
    print(f"  Failed tasks found:   {summary['failures_found']}")
    print(f"  Patterns analyzed:    {summary['patterns_analyzed']}")
    print(f"  Insights pending:     {summary.get('insights_pending', 0)}")
    print(f"  Sent for approval:    {summary.get('insights_sent', 0)}")
    print(f"  Rules saved:          {summary['rules_saved']}")
    if summary["rules_saved"] == 0 and summary.get("insights_pending", 0) == 0:
        print("  (not enough repeating failure patterns yet)")


async def run_optimize_prompts() -> None:
    from src.organism.self_improvement.prompt_versioning import PromptVersionControl
    from src.organism.self_improvement.benchmark_optimizer import BenchmarkPromptOptimizer

    llm = ClaudeProvider()
    pvc = PromptVersionControl()
    optimizer = BenchmarkPromptOptimizer(llm, pvc)

    print("\nOrganism AI \u2014 Prompt Optimization")
    print("=" * 50)
    results = await optimizer.optimize_all()

    for r in results:
        status = "DEPLOYED" if r.deployed else "NO CHANGE"
        print(f"\n  {r.prompt_name}:")
        print(f"    Baseline:     {r.baseline_score:.4f}")
        print(f"    Best variant: {r.best_variant_score:.4f}")
        print(f"    Improvement:  {r.improvement:+.4f}")
        print(f"    Status:       {status}")
        print(f"    Variants:     {r.variants_tested}")
        print(f"    Duration:     {r.duration:.1f}s")

    print("\nDone.\n")


async def run_evolve_prompts() -> None:
    from src.organism.self_improvement.prompt_versioning import PromptVersionControl
    from src.organism.self_improvement.evolutionary_search import EvolutionaryPromptSearch

    llm = ClaudeProvider()
    pvc = PromptVersionControl()
    evo = EvolutionaryPromptSearch(llm, pvc)

    print("\nOrganism AI \u2014 Evolutionary Prompt Search")
    print("=" * 50)
    results = await evo.evolve_all()

    for r in results:
        status = "DEPLOYED" if r.deployed else "NO CHANGE"
        print(f"\n  {r.prompt_name}:")
        print(f"    Generation:      {r.generation}")
        print(f"    Population:      {r.population_size}")
        print(f"    Best fitness:    {r.best_fitness:.4f}")
        print(f"    Status:          {status}")
        print(f"    Duration:        {r.duration:.1f}s")

    print("\nDone.\n")


async def run_cache_stats() -> None:
    from src.organism.self_improvement.solution_cache import SolutionCacheManager
    cache = SolutionCacheManager()
    patterns = await cache.get_top_patterns()
    if not patterns:
        print("Cache is empty.")
        return
    print("Top cached patterns:")
    for p in patterns:
        print(f"  [{p['hits']} hits] {p['pattern'][:80]}")


async def run_mcp_server(port: int) -> None:
    import aiohttp.web
    from src.organism.mcp_serve.server import create_organism_app

    registry = build_registry()
    await _connect_mcp(registry)

    memory = None
    if settings.database_url:
        try:
            memory = MemoryManager()
            await memory.initialize()
        except Exception:
            print("  [warn] Database unavailable \u2014 running without memory")

    personality = _load_personality()
    llm = ClaudeProvider()
    loop = CoreLoop(llm, registry, memory=memory, personality=personality)

    app = create_organism_app(loop, memory, registry)
    print(f"\nOrganism AI MCP Server starting on port {port}")
    print(f"  Tools exposed: execute_task, get_stats, search_knowledge, list_capabilities")
    print(f"  Connect via: MCP_SERVERS='[{{\"name\":\"organism\",\"url\":\"http://localhost:{port}\"}}]'\n")

    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    # Keep running until interrupted
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await runner.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description="Organism AI")
    parser.add_argument("--task", "-t", type=str, help="Task to execute")
    parser.add_argument("--telegram", action="store_true", help="Run Telegram bot")
    parser.add_argument("--stats", action="store_true", help="Show memory stats")
    parser.add_argument("--analyze", action="store_true", help="Analyze performance and get recommendations")
    parser.add_argument("--cache", action="store_true", help="Show solution cache stats")
    parser.add_argument("--improve", action="store_true", help="Run auto-improvement cycle (failures → rules)")
    parser.add_argument("--days", type=int, default=7, help="Days window for --improve (default: 7)")
    parser.add_argument("--multi", action="store_true", help="Use multi-agent orchestrator")
    parser.add_argument("--optimize-prompts", action="store_true",
                        help="Run benchmark-driven prompt optimization cycle")
    parser.add_argument("--evolve-prompts", action="store_true",
                        help="Run evolutionary prompt search cycle")
    parser.add_argument("--serve-mcp", action="store_true",
                        help="Start Organism AI as MCP server")
    parser.add_argument("--mcp-port", type=int, default=8091,
                        help="Port for MCP server (default: 8091)")
    parser.add_argument("--test-monitor", action="store_true",
                        help="Send test message to error monitoring Telegram channel")
    args = parser.parse_args()

    try:
        if args.test_monitor:
            from src.organism.monitoring.error_notifier import ErrorNotifier
            notifier = ErrorNotifier()
            if not notifier.is_configured:
                print("Error: TELEGRAM_BOT_TOKEN and TELEGRAM_ERROR_CHAT_ID must be set")
                sys.exit(1)
            success = asyncio.run(notifier.send_test())
            print("Test message sent!" if success else "Failed to send test message")
        elif args.stats:
            asyncio.run(run_stats())
        elif args.analyze:
            asyncio.run(run_analyze())
        elif args.cache:
            asyncio.run(run_cache_stats())
        elif args.improve:
            asyncio.run(run_improve(days=args.days))
        elif args.optimize_prompts:
            asyncio.run(run_optimize_prompts())
        elif args.evolve_prompts:
            asyncio.run(run_evolve_prompts())
        elif args.serve_mcp:
            asyncio.run(run_mcp_server(args.mcp_port))
        elif args.telegram:
            asyncio.run(run_telegram())
        elif args.task:
            asyncio.run(run_single(args.task, use_orchestrator=args.multi))
        else:
            asyncio.run(run_interactive(use_orchestrator=args.multi))
    except KeyboardInterrupt:
        print("\nBye.")


if __name__ == "__main__":
    main()




