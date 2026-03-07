"""MON-1: Error monitoring -- capture errors, save to DB, notify via Telegram.

Two components:
1. capture_error() -- call from anywhere to log an error to ErrorLog table
2. ErrorNotifier -- background asyncio task that polls unnotified errors
   and sends them to a dedicated Telegram chat
"""
import asyncio
import traceback as tb_module

import httpx
from sqlalchemy import select

from src.organism.memory.database import ErrorLog, AsyncSessionLocal
from src.organism.logging.error_handler import get_logger
from config.settings import settings

_log = get_logger("monitoring")

MAX_MESSAGE_LENGTH = 4000  # Telegram limit is 4096, leave margin


async def capture_error(
    component: str,
    message: str,
    exception: Exception | None = None,
    task_id: str = "",
    task_text: str = "",
    level: str = "ERROR",
) -> None:
    """Save error to ErrorLog table. Call from anywhere in the system.

    Args:
        component: module path, e.g. "core.loop", "agents.coder", "tools.web_fetch"
        message: human-readable error description
        exception: original exception (traceback will be extracted)
        task_id: related task ID if available
        task_text: task text for context (truncated to 500 chars)
        level: ERROR, WARNING, or CRITICAL
    """
    tb_text = ""
    if exception:
        tb_text = tb_module.format_exception(type(exception), exception, exception.__traceback__)
        tb_text = "".join(tb_text)[-2000:]  # last 2000 chars of traceback

    try:
        async with AsyncSessionLocal() as session:
            entry = ErrorLog(
                level=level,
                component=component,
                message=message[:1000],
                traceback=tb_text if tb_text else None,
                task_id=task_id if task_id else None,
                task_text=task_text[:500] if task_text else None,
                artel_id=getattr(settings, "artel_id", "default"),
                notified=False,
            )
            session.add(entry)
            await session.commit()
    except Exception as e:
        # Last resort: log to file if DB is unavailable
        _log.error("Failed to save error to DB: %s. Original: %s: %s", e, component, message)


def _format_error_message(error: ErrorLog) -> str:
    """Format error for Telegram notification."""
    # \u2757\u2757 = !!, \u274c = X, \u26a0\ufe0f = warning
    emoji = {
        "CRITICAL": "\u2757\u2757",
        "ERROR": "\u274c",
        "WARNING": "\u26a0\ufe0f",
    }.get(error.level, "\u274c")

    lines = [
        f"{emoji} *{error.level}* | `{error.component}`",
        "",
        f"{error.message[:500]}",
    ]

    if error.task_text:
        # \u0417\u0430\u0434\u0430\u0447\u0430 = "Zadacha" (Task)
        lines.append("")
        lines.append(f"\u0417\u0430\u0434\u0430\u0447\u0430: _{error.task_text[:200]}_")

    if error.task_id:
        lines.append(f"Task ID: `{error.task_id[:16]}`")

    if error.traceback:
        # Show last 3 lines of traceback
        tb_lines = error.traceback.strip().splitlines()
        short_tb = "\n".join(tb_lines[-3:])
        lines.append("")
        lines.append(f"```\n{short_tb[:500]}\n```")

    ts = error.created_at.strftime("%H:%M:%S") if error.created_at else ""
    lines.append(f"\n_{ts}_")

    text = "\n".join(lines)
    return text[:MAX_MESSAGE_LENGTH]


class ErrorNotifier:
    """Background task that polls ErrorLog for unnotified errors and sends to Telegram."""

    def __init__(self) -> None:
        # Prefer dedicated error bot, fallback to main bot
        self._bot_token = settings.error_bot_token or settings.telegram_bot_token
        self._chat_id = settings.error_monitor_chat_id
        self._interval = settings.error_monitor_interval
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self._bot_token and self._chat_id)

    async def start(self) -> None:
        """Start the background polling loop."""
        if not self.is_configured:
            _log.warning("ErrorNotifier not configured (TELEGRAM_ERROR_CHAT_ID not set)")
            return

        self._running = True
        self._task = asyncio.create_task(self._loop())
        _log.info("ErrorNotifier started (interval=%ds, chat=%s)", self._interval, self._chat_id)

    async def stop(self) -> None:
        """Stop the background loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        _log.info("ErrorNotifier stopped")

    async def _loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self._process_unnotified()
            except Exception as e:
                # Don't let monitoring crash itself
                _log.error("ErrorNotifier loop error: %s", e)

            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break

    async def _process_unnotified(self) -> None:
        """Fetch unnotified errors, send to Telegram, mark as notified."""
        try:
            async with AsyncSessionLocal() as session:
                stmt = (
                    select(ErrorLog)
                    .where(ErrorLog.notified == False)  # noqa: E712
                    .order_by(ErrorLog.created_at.asc())
                    .limit(10)  # batch size -- don't spam
                )
                result = await session.execute(stmt)
                errors = result.scalars().all()

                if not errors:
                    return

                sent = 0
                for error in errors:
                    success = await self._send_telegram(error)
                    if success:
                        error.notified = True
                        sent += 1
                    # If send fails, leave notified=false -> retry next cycle

                await session.commit()

                if sent:
                    _log.info("Notified %d errors to Telegram", sent)
        except Exception as e:
            _log.error("Error processing unnotified: %s", e)

    async def _send_telegram(self, error: ErrorLog) -> bool:
        """Send single error to Telegram. Returns True on success."""
        text = _format_error_message(error)

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # Try Markdown first
                resp = await client.post(
                    f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                    json={
                        "chat_id": self._chat_id,
                        "text": text,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    },
                )
                data = resp.json()
                if data.get("ok"):
                    return True

                # Markdown failed — retry as plain text
                resp2 = await client.post(
                    f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                    json={
                        "chat_id": self._chat_id,
                        "text": text,
                        "disable_web_page_preview": True,
                    },
                )
                data2 = resp2.json()
                if data2.get("ok"):
                    return True

                _log.warning("Telegram send failed: %s", data2.get("description", "unknown"))
                return False
        except Exception as e:
            _log.warning("Telegram send error: %s", e)
            return False

    async def send_test(self) -> bool:
        """Send a test message to verify configuration."""
        if not self.is_configured:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                    json={
                        "chat_id": self._chat_id,
                        # \u2705 = checkmark, "Organism AI Error Monitor aktiven"
                        "text": "\u2705 Organism AI Error Monitor \u0430\u043a\u0442\u0438\u0432\u0435\u043d",
                        "parse_mode": "Markdown",
                    },
                )
                return resp.json().get("ok", False)
        except Exception:
            return False
