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
    if settings.tavily_api_key:
        registry.register(WebSearchTool())
    if settings.telegram_bot_token:
        registry.register(TelegramSenderTool(settings.telegram_bot_token))
    return registry


def build_loop(registry: ToolRegistry | None = None) -> CoreLoop:
    llm = ClaudeProvider()
    reg = registry or build_registry()
    memory = MemoryManager() if settings.database_url else None
    return CoreLoop(llm, reg, memory=memory)


async def run_single(task: str, use_orchestrator: bool = False) -> None:
    from src.organism.commands.handler import CommandHandler
    handler = CommandHandler()
    if handler.is_command(task):
        memory = MemoryManager() if settings.database_url else None
        print(await handler.handle(task, memory))
        return

    if use_orchestrator:
        from src.organism.agents.orchestrator import Orchestrator
        llm = ClaudeProvider()
        registry = build_registry()
        memory = MemoryManager() if settings.database_url else None
        if memory:
            await memory.initialize()
        orch = Orchestrator(llm, registry, memory=memory)
        result = await orch.run(task)
    else:
        loop = build_loop()
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
    from src.organism.commands.handler import CommandHandler
    handler = CommandHandler()

    if use_orchestrator:
        from src.organism.agents.orchestrator import Orchestrator
        llm = ClaudeProvider()
        registry = build_registry()
        memory = MemoryManager() if settings.database_url else None
        if memory:
            await memory.initialize()
        orch = Orchestrator(llm, registry, memory=memory)
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
        print("Organism AI  interactive mode. Type 'exit' to quit.\n")
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
                print(await handler.handle(task, loop.memory))
                continue
            try:
                result = await loop.run(task)
                if not result.success:
                    print(f"Failed: {result.error}")
            except KeyboardInterrupt:
                print("\nInterrupted.")


async def run_telegram() -> None:
    from src.organism.channels.telegram import TelegramChannel
    if not settings.telegram_bot_token:
        print("Error: TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)
    registry = build_registry()
    loop = build_loop(registry)
    channel = TelegramChannel(loop)
    print("Organism AI Telegram bot starting...")
    await channel.start()


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
    summary = await AutoImprover().run_cycle(memory, llm, kb, days=days)
    print(f"Done:")
    print(f"  Failed tasks found:   {summary['failures_found']}")
    print(f"  Patterns analyzed:    {summary['patterns_analyzed']}")
    print(f"  Rules saved:          {summary['rules_saved']}")
    if summary["rules_saved"] == 0:
        print("  (not enough repeating failure patterns yet)")


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
    args = parser.parse_args()

    try:
        if args.stats:
            asyncio.run(run_stats())
        elif args.analyze:
            asyncio.run(run_analyze())
        elif args.cache:
            asyncio.run(run_cache_stats())
        elif args.improve:
            asyncio.run(run_improve(days=args.days))
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




