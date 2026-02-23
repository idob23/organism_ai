import asyncio
import argparse
import sys

from src.organism.llm.claude import ClaudeProvider
from src.organism.tools.code_executor import CodeExecutorTool
from src.organism.tools.web_search import WebSearchTool
from src.organism.tools.web_fetch import WebFetchTool
from src.organism.tools.file_manager import FileManagerTool
from src.organism.tools.telegram_sender import TelegramSenderTool
from src.organism.tools.registry import ToolRegistry
from src.organism.core.loop import CoreLoop
from src.organism.memory.manager import MemoryManager
from config.settings import settings


def build_loop() -> CoreLoop:
    llm = ClaudeProvider()
    registry = ToolRegistry()

    registry.register(CodeExecutorTool())
    registry.register(WebFetchTool())
    registry.register(FileManagerTool())

    if settings.tavily_api_key:
        registry.register(WebSearchTool())

    if settings.telegram_bot_token:
        registry.register(TelegramSenderTool(settings.telegram_bot_token))

    memory = MemoryManager() if settings.database_url else None

    return CoreLoop(llm, registry, memory=memory)


async def run_single(task: str) -> None:
    loop = build_loop()
    result = await loop.run(task)
    if not result.success:
        print(f"\nFailed: {result.error}")
        sys.exit(1)


async def run_interactive() -> None:
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
    loop = build_loop()
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Organism AI")
    parser.add_argument("--task", "-t", type=str, help="Task to execute")
    parser.add_argument("--telegram", action="store_true", help="Run Telegram bot")
    parser.add_argument("--stats", action="store_true", help="Show memory stats")
    args = parser.parse_args()

    try:
        if args.stats:
            asyncio.run(run_stats())
        elif args.telegram:
            asyncio.run(run_telegram())
        elif args.task:
            asyncio.run(run_single(args.task))
        else:
            asyncio.run(run_interactive())
    except KeyboardInterrupt:
        print("\nBye.")


if __name__ == "__main__":
    main()
