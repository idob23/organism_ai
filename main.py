import asyncio
import argparse
import sys

from src.organism.llm.claude import ClaudeProvider
from src.organism.tools.code_executor import CodeExecutorTool
from src.organism.tools.registry import ToolRegistry
from src.organism.core.loop import CoreLoop


def build_loop() -> CoreLoop:
    llm = ClaudeProvider()
    registry = ToolRegistry()
    registry.register(CodeExecutorTool())
    return CoreLoop(llm, registry)


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Organism AI")
    parser.add_argument("--task", "-t", type=str, help="Task to execute")
    args = parser.parse_args()

    try:
        if args.task:
            asyncio.run(run_single(args.task))
        else:
            asyncio.run(run_interactive())
    except KeyboardInterrupt:
        print("\nBye.")


if __name__ == "__main__":
    main()
