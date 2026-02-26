import asyncio
import os
import tempfile
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import CommandStart, Command

from src.organism.core.loop import CoreLoop
from src.organism.channels.base import BaseChannel
from config.settings import settings


class TelegramChannel(BaseChannel):

    def __init__(self, loop: CoreLoop) -> None:
        self.loop = loop
        self.bot = Bot(token=settings.telegram_bot_token)
        self.dp = Dispatcher()
        self._setup_handlers()

    def _setup_handlers(self) -> None:
        allowed = settings.allowed_user_ids

        @self.dp.message(CommandStart())
        async def cmd_start(message: Message) -> None:
            if allowed and message.from_user.id not in allowed:
                await message.answer("Access denied.")
                return
            await message.answer(
                "Organism AI готов к работе.\n"
                "Отправь мне задачу на естественном языке."
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

            # Notify user that work has started
            status_msg = await message.answer(f"Принял задачу: {task[:80]}\nВыполняю...")

            try:
                result = await self.loop.run(task, verbose=False)

                if result.success:
                    steps_info = f"Шагов: {len(result.steps)} | Время: {result.duration:.1f}s"
                    raw = result.answer if result.answer and not result.answer.startswith("Saved to") else result.output
                    lines = [line for line in raw.splitlines() if not line.startswith("Saved to")]
                    clean_output = "\n".join(lines).strip()

                    if len(clean_output) > 800:
                        short_preview = clean_output[:500] + "..."
                        await status_msg.edit_text(f"✅ Готово\n{steps_info}\n\n{short_preview}\n\n📎 Полный текст во вложении:")
                        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
                            f.write(clean_output)
                            tmp_path = f.name
                        try:
                            await message.answer_document(FSInputFile(tmp_path, filename="result.md"))
                        finally:
                            os.unlink(tmp_path)
                    else:
                        await status_msg.edit_text(f"✅ Готово\n{steps_info}\n\n{clean_output}")
                else:
                    await status_msg.edit_text(f"❌ Не удалось выполнить\n{result.error[:500]}")

            except Exception as e:
                await status_msg.edit_text(f" Ошибка: {str(e)[:300]}")

    async def start(self) -> None:
        await self.dp.start_polling(self.bot)

    async def stop(self) -> None:
        await self.bot.session.close()
