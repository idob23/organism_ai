import asyncio
import os
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import CommandStart, Command

from src.organism.channels.base import BaseChannel, IncomingMessage, OutgoingMessage
from config.settings import settings


class TelegramChannel(BaseChannel):

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self.bot = Bot(token=settings.telegram_bot_token)
        self.dp = Dispatcher()
        self._setup_handlers()

    @staticmethod
    async def _tick_progress(msg: Message, preview: str) -> None:
        """Edit status message every 5s with elapsed time while task runs."""
        icons = ["\u23f3", "\U0001f504"]
        elapsed = 0
        while True:
            await asyncio.sleep(5)
            elapsed += 5
            icon = icons[(elapsed // 5) % 2]
            try:
                # "\u0412\u044b\u043f\u043e\u043b\u043d\u044f\u044e"
                await msg.edit_text(
                    f"{icon} \u0412\u044b\u043f\u043e\u043b\u043d\u044f\u044e... {elapsed}\u0441\n{preview}"
                )
            except Exception:
                pass  # ignore FloodWait / MessageNotModified

    def _setup_handlers(self) -> None:
        allowed = settings.allowed_user_ids

        @self.dp.message(CommandStart())
        async def cmd_start(message: Message) -> None:
            if allowed and message.from_user.id not in allowed:
                await message.answer("Access denied.")
                return
            await message.answer(
                "Organism AI \u0433\u043e\u0442\u043e\u0432 \u043a \u0440\u0430\u0431\u043e\u0442\u0435.\n"
                "\u041e\u0442\u043f\u0440\u0430\u0432\u044c \u043c\u043d\u0435 "
                "\u0437\u0430\u0434\u0430\u0447\u0443 \u043d\u0430 "
                "\u0435\u0441\u0442\u0435\u0441\u0442\u0432\u0435\u043d\u043d\u043e\u043c "
                "\u044f\u0437\u044b\u043a\u0435."
            )

        @self.dp.message(Command("status"))
        async def cmd_status(message: Message) -> None:
            if allowed and message.from_user.id not in allowed:
                return
            await message.answer("Organism AI running.")

        @self.dp.message(F.text)
        async def handle_task(message: Message) -> None:
            if allowed and message.from_user.id not in allowed:
                await message.answer("Access denied.")
                return

            task = message.text.strip()
            if not task:
                return

            incoming = IncomingMessage(
                text=task,
                user_id=str(message.from_user.id),
                channel="telegram",
                metadata={"chat_id": message.chat.id},
            )

            # Commands — no progress ticker needed
            if self.gateway.cmd_handler.is_command(task):
                response = await self.gateway.handle_message(incoming)
                await message.answer(response.text)
                return

            # Regular task — with progress ticker
            preview = task[:60] + ("..." if len(task) > 60 else "")
            # "\u23f3 \u041f\u0440\u0438\u043d\u044f\u043b \u0437\u0430\u0434\u0430\u0447\u0443"
            status_msg = await message.answer(
                f"\u23f3 \u041f\u0440\u0438\u043d\u044f\u043b "
                f"\u0437\u0430\u0434\u0430\u0447\u0443:\n{preview}\n\n"
                f"\u0412\u044b\u043f\u043e\u043b\u043d\u044f\u044e..."
            )
            ticker = asyncio.create_task(self._tick_progress(status_msg, preview))

            try:
                try:
                    response = await self.gateway.handle_message(incoming)
                finally:
                    ticker.cancel()

                duration = response.metadata.get("duration", 0)
                steps = response.metadata.get("steps", 0)
                # "\u0428\u0430\u0433\u043e\u0432" / "\u0412\u0440\u0435\u043c\u044f"
                steps_info = (
                    f"\u0428\u0430\u0433\u043e\u0432: {steps} | "
                    f"\u0412\u0440\u0435\u043c\u044f: {duration:.1f}s"
                )

                if response.is_file:
                    # FIX-3: File response — send as .txt (not .md)
                    file_path = response.text
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            full_text = f.read()
                        short_preview = full_text[:500] + "..."
                        await status_msg.edit_text(
                            f"\u2705 \u0413\u043e\u0442\u043e\u0432\u043e\n{steps_info}\n\n"
                            f"{short_preview}\n\n"
                            f"\U0001f4ce \u041f\u043e\u043b\u043d\u044b\u0439 "
                            f"\u0442\u0435\u043a\u0441\u0442 \u0432\u043e "
                            f"\u0432\u043b\u043e\u0436\u0435\u043d\u0438\u0438:"
                        )
                        fname = os.path.basename(file_path)
                        try:
                            await message.answer_document(
                                FSInputFile(file_path, filename=fname),
                            )
                        finally:
                            try:
                                os.unlink(file_path)
                            except Exception:
                                pass
                    except Exception:
                        await status_msg.edit_text(
                            f"\u2705 \u0413\u043e\u0442\u043e\u0432\u043e\n{steps_info}"
                        )
                elif response.text.startswith("Error:"):
                    err = response.text[7:]  # strip "Error: "
                    if "Traceback" in err or "File \"/" in err:
                        err = err.splitlines()[-1]
                    await status_msg.edit_text(
                        f"\u274c \u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c "
                        f"\u0432\u044b\u043f\u043e\u043b\u043d\u0438\u0442\u044c\n\n"
                        f"{err[:300]}\n\n"
                        f"\u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 "
                        f"\u043f\u0435\u0440\u0435\u0444\u043e\u0440\u043c\u0443\u043b\u0438\u0440\u043e\u0432\u0430\u0442\u044c "
                        f"\u0437\u0430\u0434\u0430\u0447\u0443."
                    )
                else:
                    # FIX-3: Try Markdown formatting, fallback to plain text
                    full = (
                        f"\u2705 \u0413\u043e\u0442\u043e\u0432\u043e\n{steps_info}\n\n"
                        f"{response.text}"
                    )
                    try:
                        await status_msg.edit_text(full, parse_mode="Markdown")
                    except Exception:
                        try:
                            await status_msg.edit_text(full)
                        except Exception:
                            pass

            except Exception:
                await status_msg.edit_text(
                    "\u26a0\ufe0f \u0412\u043d\u0443\u0442\u0440\u0435\u043d\u043d\u044f\u044f "
                    "\u043e\u0448\u0438\u0431\u043a\u0430. "
                    "\u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 "
                    "\u0435\u0449\u0451 \u0440\u0430\u0437."
                )

    async def start(self) -> None:
        await self.dp.start_polling(self.bot)

    async def stop(self) -> None:
        await self.bot.session.close()

    async def send(self, message: OutgoingMessage) -> None:
        """Send outgoing message via Telegram bot."""
        chat_id = message.metadata.get("chat_id")
        if not chat_id:
            # Fallback: send to first allowed user
            uids = settings.allowed_user_ids
            if uids:
                chat_id = uids[0]
            else:
                return
        try:
            if message.is_file:
                fname = os.path.basename(message.text)
                await self.bot.send_document(
                    chat_id, FSInputFile(message.text, filename=fname),
                )
            else:
                # FIX-3: Try Markdown, fallback to plain text
                try:
                    await self.bot.send_message(
                        chat_id, message.text, parse_mode="Markdown",
                    )
                except Exception:
                    await self.bot.send_message(chat_id, message.text)
        except Exception:
            pass
