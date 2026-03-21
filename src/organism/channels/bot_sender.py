"""FIX-93: Single Telegram Bot sender — replaces 3 duplicate Bot() creation points."""
from __future__ import annotations

from aiogram import Bot
from src.organism.logging.error_handler import get_logger

_log = get_logger("channels.bot_sender")


class BotSender:
    """Centralized Telegram message sender. One instance per process."""

    def __init__(self, token: str) -> None:
        self._token = token

    async def send(self, chat_id: int | str, text: str) -> bool:
        """Send message to a single chat. Returns True on success."""
        bot = Bot(token=self._token)
        try:
            await bot.send_message(chat_id, text)
            return True
        except Exception as exc:
            _log.warning("bot_sender.send failed (%s): %s", chat_id, exc)
            return False
        finally:
            await bot.session.close()

    async def send_many(self, chat_ids: list[int | str], text: str) -> int:
        """Send message to multiple chats. Returns count of successful sends."""
        if not chat_ids:
            return 0
        bot = Bot(token=self._token)
        sent = 0
        try:
            for cid in chat_ids:
                try:
                    await bot.send_message(cid, text)
                    sent += 1
                except Exception as exc:
                    _log.warning("bot_sender.send_many failed (%s): %s", cid, exc)
        finally:
            await bot.session.close()
        return sent
