"""Q-6.5: CLI channel using Gateway abstraction."""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.organism.channels.base import BaseChannel, IncomingMessage, OutgoingMessage

if TYPE_CHECKING:
    from src.organism.channels.gateway import Gateway


class CLIChannel(BaseChannel):
    """Simple CLI channel for interactive mode."""

    def __init__(self, gateway: "Gateway") -> None:
        self.gateway = gateway

    async def start(self) -> None:
        print("Organism AI interactive mode. Type 'exit' to quit.\n")
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
            incoming = IncomingMessage(
                text=task, user_id="local", channel="cli",
            )
            response = await self.gateway.handle_message(incoming)
            if response.is_file:
                try:
                    with open(response.text, "r", encoding="utf-8") as f:
                        print(f.read())
                except Exception as exc:
                    print(f"Error reading result file: {exc}")
            else:
                print(response.text)

    async def stop(self) -> None:
        pass

    async def send(self, message: OutgoingMessage) -> None:
        print(message.text)
