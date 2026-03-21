"""Q-6.5: Channel-agnostic gateway abstraction.

Sits between channels (Telegram, CLI, web) and CoreLoop.
Routes messages, handles commands, formats responses.
"""
from __future__ import annotations

import os
import re
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
        bot_sender=None,
    ) -> None:
        self.loop = loop
        self.scheduler = scheduler
        self.approval = approval
        from src.organism.agents.factory import AgentFactory
        self.factory = getattr(loop, 'factory', None) or AgentFactory()
        self.cmd_handler = CommandHandler(
            scheduler=scheduler,
            approval=approval,
            personality=getattr(loop, "personality", None),
            factory=self.factory,
            loop=loop,
            bot_sender=bot_sender,
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
                    msg.text, self.loop.memory, user_id=msg.user_id,
                )
            except Exception as exc:
                _log.error("gateway.cmd_error: %s: %s", type(exc).__name__, exc)
                result_text = f"Command error: {exc}"
            # FIX-71: Save /assign results to chat history (agent responses are conversational)
            if msg.text.strip().lower().startswith("/assign") and self.loop.memory and msg.user_id:
                try:
                    await self.loop.memory.chat_history.save_message(msg.user_id, "user", msg.text)
                    await self.loop.memory.chat_history.save_message(msg.user_id, "assistant", result_text[:5000])
                except Exception:
                    pass
            return OutgoingMessage(
                text=result_text, user_id=msg.user_id, channel=msg.channel,
            )

        # 2. Regular task -> CoreLoop
        try:
            progress_cb = msg.metadata.get("progress_callback") if msg.metadata else None
            tool_progress_cb = msg.metadata.get("tool_progress_callback") if msg.metadata else None
            result = await self.loop.run(
                msg.text, verbose=False, user_id=msg.user_id,
                media=getattr(msg, "media", None) or [],
                progress_callback=progress_cb,
                tool_progress_callback=tool_progress_cb,
            )
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

        # HIST-1: Save chat history (user message + assistant response)
        if self.loop.memory and msg.user_id:
            try:
                await self.loop.memory.chat_history.save_message(msg.user_id, "user", msg.text)
                await self.loop.memory.chat_history.save_message(msg.user_id, "assistant", response_text[:5000])
            except Exception:
                pass

        # FIX-78: Structural file delivery — no regex parsing
        files_to_send = []
        for fname in getattr(result, 'created_files', []):
            candidate = os.path.join("data", "outputs", fname)
            if os.path.exists(candidate):
                files_to_send.append(candidate)

        meta = {
            "duration": getattr(result, "duration", 0),
            "steps": len(getattr(result, "steps", [])),
            "files": files_to_send,
        }
        resp = self._prepare_response(response_text, msg.user_id, msg.channel, meta)
        return resp

    # FIX-3: Telegram-friendly response threshold (4096 limit, 3500 with margin)
    _TEXT_LIMIT = 3500

    def _prepare_response(
        self,
        output: str,
        user_id: str,
        channel: str,
        metadata: dict,
    ) -> OutgoingMessage:
        """Convert task output to a Telegram-friendly response.

        - FIX-78: Structural file delivery via metadata["files"] (no regex).
        - If output is a file path: read content, send inline if short, .txt file if long.
        - If output is plain text: send inline if short, save to .txt if long.
        """
        # FIX-78: Structural file delivery from TaskResult.created_files
        files = metadata.get("files", [])
        if files:
            # Clean "Saved files: ..." markers from text output
            clean_output = re.sub(r'\n?Saved files:\s*\S+', '', output).strip()
            caption = clean_output[:1000] if clean_output else ""

            if len(files) == 1:
                return self._prepare_file_response(
                    files[0], user_id, channel, metadata, caption=caption,
                )
            else:
                return OutgoingMessage(
                    text=files[0],
                    user_id=user_id,
                    channel=channel,
                    is_file=True,
                    metadata={**metadata, "extra_files": files[1:]},
                    caption=caption,
                )

        stripped = output.strip()

        # Detect file paths in output
        _file_exts = (".md", ".txt", ".csv", ".xlsx", ".pptx", ".py", ".pdf")
        is_file_path = (
            stripped.endswith(_file_exts)
            and "\n" not in stripped
            and len(stripped) < 300
            and os.path.exists(stripped)
        )

        if is_file_path:
            return self._prepare_file_response(stripped, user_id, channel, metadata)

        # Plain text output
        if len(output) <= self._TEXT_LIMIT:
            return OutgoingMessage(
                text=output, user_id=user_id, channel=channel,
                is_file=False, metadata=metadata,
            )

        # Too long for inline — save to .txt
        try:
            fd, path = tempfile.mkstemp(
                suffix=".txt", prefix="result_", dir="data/outputs",
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(output)
            return OutgoingMessage(
                text=path, user_id=user_id, channel=channel,
                is_file=True, metadata=metadata,
            )
        except Exception:
            # Fallback: send truncated inline
            return OutgoingMessage(
                text=output[:self._TEXT_LIMIT], user_id=user_id, channel=channel,
                is_file=False, metadata=metadata,
            )

    def _prepare_file_response(
        self,
        file_path: str,
        user_id: str,
        channel: str,
        metadata: dict,
        caption: str = "",
    ) -> OutgoingMessage:
        """Read a file and decide: inline text or .txt attachment."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return OutgoingMessage(
                text=file_path, user_id=user_id, channel=channel,
                is_file=True, metadata=metadata, caption=caption,
            )

        if len(content) <= self._TEXT_LIMIT:
            # Short enough — send as inline text, remove the file
            try:
                os.unlink(file_path)
            except Exception:
                pass
            return OutgoingMessage(
                text=content, user_id=user_id, channel=channel,
                is_file=False, metadata=metadata,
            )

        # Long content — send as .txt (not .md)
        if file_path.endswith(".md"):
            txt_path = file_path.rsplit(".", 1)[0] + ".txt"
            try:
                os.rename(file_path, txt_path)
                file_path = txt_path
            except Exception:
                pass  # keep .md if rename fails
        return OutgoingMessage(
            text=file_path, user_id=user_id, channel=channel,
            is_file=True, metadata=metadata, caption=caption,
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
