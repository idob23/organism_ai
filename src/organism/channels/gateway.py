"""Q-6.5: Channel-agnostic gateway abstraction.

Sits between channels (Telegram, CLI, web) and CoreLoop.
Routes messages, handles commands, formats responses.
"""
from __future__ import annotations

import os
import tempfile
from typing import TYPE_CHECKING

from src.organism.channels.base import BaseChannel, IncomingMessage, OutgoingMessage
from src.organism.commands.handler import CommandHandler
from src.organism.logging.error_handler import get_logger

if TYPE_CHECKING:
    from src.organism.core.loop import CoreLoop
    from src.organism.core.scheduler import ProactiveScheduler
    from src.organism.core.human_approval import HumanApproval

_log = get_logger("channels.gateway")


class Gateway:
    """Channel-agnostic message router between channels and CoreLoop."""

    def __init__(
        self,
        loop: "CoreLoop",
        scheduler: "ProactiveScheduler | None" = None,
        approval: "HumanApproval | None" = None,
    ) -> None:
        self.loop = loop
        self.scheduler = scheduler
        self.approval = approval
        self.cmd_handler = CommandHandler(
            scheduler=scheduler,
            approval=approval,
            personality=getattr(loop, "personality", None),
        )
        self._channels: dict[str, BaseChannel] = {}

    def register_channel(self, name: str, channel: BaseChannel) -> None:
        self._channels[name] = channel

    async def handle_message(self, msg: IncomingMessage) -> OutgoingMessage:
        """Main entry point. Process incoming message, return response."""
        # 1. Commands
        if self.cmd_handler.is_command(msg.text):
            try:
                result_text = await self.cmd_handler.handle(
                    msg.text, self.loop.memory,
                )
            except Exception as exc:
                _log.error("gateway.cmd_error: %s: %s", type(exc).__name__, exc)
                result_text = f"Command error: {exc}"
            return OutgoingMessage(
                text=result_text, user_id=msg.user_id, channel=msg.channel,
            )

        # 2. Regular task -> CoreLoop
        try:
            result = await self.loop.run(msg.text, verbose=False)
        except Exception as exc:
            _log.error("gateway.task_error: %s: %s", type(exc).__name__, exc)
            # MON-1: Capture to ErrorLog for Telegram monitoring
            try:
                from src.organism.monitoring.error_notifier import capture_error
                import asyncio
                asyncio.ensure_future(capture_error(
                    component="channels.gateway",
                    message=f"Task execution error: {type(exc).__name__}: {exc}",
                    exception=exc,
                    task_text=msg.text[:500] if msg.text else "",
                ))
            except Exception:
                pass
            return OutgoingMessage(
                text=f"Error: {type(exc).__name__}: {exc}",
                user_id=msg.user_id,
                channel=msg.channel,
            )

        if result.success:
            raw = (
                result.answer
                if result.answer and not result.answer.startswith("Saved to")
                else result.output
            )
            lines = [ln for ln in raw.splitlines() if not ln.startswith("Saved to")]
            response_text = "\n".join(lines).strip() or "Done"
        else:
            response_text = f"Error: {result.error}" if result.error else "Task failed"

        # 3. Long responses -> temp file
        is_file = False
        if len(response_text) > 800:
            try:
                fd, path = tempfile.mkstemp(suffix=".md", prefix="result_")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(response_text)
                response_text = path
                is_file = True
            except Exception:
                pass  # keep inline text on failure

        return OutgoingMessage(
            text=response_text,
            user_id=msg.user_id,
            channel=msg.channel,
            is_file=is_file,
            metadata={"duration": getattr(result, "duration", 0),
                       "steps": len(getattr(result, "steps", []))},
        )

    async def send_to_channel(self, msg: OutgoingMessage) -> None:
        """Route outgoing message to the appropriate channel."""
        channel = self._channels.get(msg.channel)
        if channel:
            await channel.send(msg)

    async def broadcast(self, text: str) -> None:
        """Send message to all registered channels (for scheduler notifications)."""
        for name, channel in self._channels.items():
            try:
                await channel.send(
                    OutgoingMessage(text=text, user_id="", channel=name),
                )
            except Exception:
                pass
