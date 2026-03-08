import asyncio
import base64
import io
import os
import subprocess
import tempfile

import openai
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import CommandStart, Command

from src.organism.channels.base import BaseChannel, IncomingMessage, OutgoingMessage
from config.settings import settings

# FIX-21: Binary file extensions that must not be read as text
BINARY_EXTENSIONS = (".xlsx", ".pptx", ".pdf", ".docx", ".zip", ".png", ".jpg", ".jpeg")


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
                    # FIX-21: Handle binary and text files separately
                    file_path = response.text
                    fname = os.path.basename(file_path)
                    is_binary = file_path.lower().endswith(BINARY_EXTENSIONS)

                    if is_binary:
                        await status_msg.edit_text(
                            f"\u2705 \u0413\u043e\u0442\u043e\u0432\u043e\n{steps_info}\n\n"
                            f"\U0001f4ce {fname}"
                        )
                        try:
                            await message.answer_document(
                                FSInputFile(file_path, filename=fname),
                            )
                        finally:
                            try:
                                os.unlink(file_path)
                            except Exception:
                                pass
                    else:
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

        @self.dp.message(F.voice)
        async def handle_voice(message: Message) -> None:
            if allowed and message.from_user.id not in allowed:
                await message.answer("Access denied.")
                return

            # \U0001f3a4 = microphone, "\u0420\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u044e \u0433\u043e\u043b\u043e\u0441..."
            status_msg = await message.answer(
                "\U0001f3a4 \u0420\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u044e \u0433\u043e\u043b\u043e\u0441..."
            )

            try:
                # Download voice file
                file = await self.bot.get_file(message.voice.file_id)

                tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
                tmp_path = tmp.name
                tmp.close()

                await self.bot.download_file(file.file_path, tmp_path)

                # Transcribe with Whisper
                try:
                    text = await self._transcribe_voice(tmp_path)
                finally:
                    os.unlink(tmp_path)

                if not text or not text.strip():
                    await status_msg.edit_text(
                        "\u274c \u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c "
                        "\u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u0442\u044c \u0440\u0435\u0447\u044c. "
                        "\u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u0435\u0449\u0451 \u0440\u0430\u0437."
                    )
                    return

                # Show recognized text
                preview = text[:100] + ("..." if len(text) > 100 else "")
                # \U0001f4ac = speech bubble, "\u0420\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u043d\u043e"
                await status_msg.edit_text(
                    f"\U0001f4ac \u0420\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u043d\u043e: {preview}\n"
                    f"\u23f3 \u0412\u044b\u043f\u043e\u043b\u043d\u044f\u044e..."
                )

                # Process as regular task
                ticker = asyncio.create_task(self._tick_progress(status_msg, preview))
                try:
                    incoming = IncomingMessage(
                        text=text,
                        user_id=str(message.from_user.id),
                        channel="telegram",
                        metadata={"chat_id": message.chat.id},
                    )
                    response = await self.gateway.handle_message(incoming)
                finally:
                    ticker.cancel()

                # Send response (same logic as handle_task)
                duration = response.metadata.get("duration", 0)
                steps = response.metadata.get("steps", 0)
                steps_info = (
                    f"\u0428\u0430\u0433\u043e\u0432: {steps} | "
                    f"\u0412\u0440\u0435\u043c\u044f: {duration:.1f}s"
                )

                if response.is_file:
                    # FIX-21: Handle binary and text files separately
                    file_path = response.text
                    fname = os.path.basename(file_path)
                    is_binary = file_path.lower().endswith(BINARY_EXTENSIONS)

                    if is_binary:
                        await status_msg.edit_text(
                            f"\u2705 \u0413\u043e\u0442\u043e\u0432\u043e\n{steps_info}\n\n"
                            f"\U0001f4ce {fname}"
                        )
                        try:
                            await message.answer_document(
                                FSInputFile(file_path, filename=fname),
                            )
                        finally:
                            try:
                                os.unlink(file_path)
                            except Exception:
                                pass
                    else:
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
                    err = response.text[7:]
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
            except Exception as e:
                await status_msg.edit_text(
                    f"\u26a0\ufe0f \u041e\u0448\u0438\u0431\u043a\u0430 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0438 "
                    f"\u0433\u043e\u043b\u043e\u0441\u043e\u0432\u043e\u0433\u043e: {str(e)[:200]}"
                )

        # MEDIA-1: Handler for photos, videos, and image documents
        @self.dp.message(F.photo | F.video | F.document)
        async def handle_media(message: Message) -> None:
            if allowed and message.from_user.id not in allowed:
                await message.answer("Access denied.")
                return

            task = message.caption or "\u041f\u0440\u043e\u0430\u043d\u0430\u043b\u0438\u0437\u0438\u0440\u0443\u0439 \u043c\u0435\u0434\u0438\u0430\u0444\u0430\u0439\u043b \u0438 \u043e\u043f\u0438\u0448\u0438 \u0447\u0442\u043e \u043d\u0430 \u043d\u0451\u043c."
            media_items = []

            try:
                if message.photo:
                    # Take largest resolution photo
                    photo = message.photo[-1]
                    file = await self.bot.get_file(photo.file_id)
                    buf = io.BytesIO()
                    await self.bot.download_file(file.file_path, buf)
                    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                    media_items.append({
                        "type": "image",
                        "data": b64,
                        "media_type": "image/jpeg",
                    })

                elif message.document:
                    doc = message.document
                    mime = doc.mime_type or ""
                    if mime.startswith("image/"):
                        file = await self.bot.get_file(doc.file_id)
                        buf = io.BytesIO()
                        await self.bot.download_file(file.file_path, buf)
                        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                        media_items.append({
                            "type": "image",
                            "data": b64,
                            "media_type": mime,
                        })
                    elif mime == "application/pdf" or (doc.file_name or "").lower().endswith(".pdf"):
                        # MEDIA-3: PDF → convert pages to images via pdf2image → Vision API
                        file = await self.bot.get_file(doc.file_id)
                        buf = io.BytesIO()
                        await self.bot.download_file(file.file_path, buf)
                        pages = await self._pdf_to_images(buf.getvalue())
                        if pages:
                            media_items.extend(pages)
                        else:
                            # FIX-31: Honest error if poppler not installed
                            await message.answer(
                                "\u274c \u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u0442\u044c PDF: "
                                "poppler \u043d\u0435 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d. "
                                "\u0423\u0441\u0442\u0430\u043d\u043e\u0432\u0438: apt-get install poppler-utils "
                                "\u0438\u043b\u0438 \u043e\u0442\u043f\u0440\u0430\u0432\u044c \u0441\u043e\u0434\u0435\u0440\u0436\u0438\u043c\u043e\u0435 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430 \u043a\u0430\u043a \u0442\u0435\u043a\u0441\u0442."
                            )
                            return
                    else:
                        # Non-image document — just mention filename in task
                        fname = doc.file_name or "document"
                        task = f"[{fname}] {task}"

                elif message.video:
                    # Extract frames via ffmpeg
                    file = await self.bot.get_file(message.video.file_id)
                    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                    tmp_path = tmp.name
                    tmp.close()
                    try:
                        await self.bot.download_file(file.file_path, tmp_path)
                        frames = await self._extract_video_frames(tmp_path)
                        for frame_bytes in frames:
                            b64 = base64.b64encode(frame_bytes).decode("ascii")
                            media_items.append({
                                "type": "image",
                                "data": b64,
                                "media_type": "image/jpeg",
                            })
                    finally:
                        try:
                            os.unlink(tmp_path)
                        except Exception:
                            pass

                    if not media_items:
                        await message.answer(
                            "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u0442\u044c \u0432\u0438\u0434\u0435\u043e. "
                            "\u0422\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f ffmpeg \u0434\u043b\u044f \u0438\u0437\u0432\u043b\u0435\u0447\u0435\u043d\u0438\u044f \u043a\u0430\u0434\u0440\u043e\u0432. "
                            "\u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0441\u043a\u0440\u0438\u043d\u0448\u043e\u0442 \u0432\u043c\u0435\u0441\u0442\u043e \u0432\u0438\u0434\u0435\u043e."
                        )
                        return

            except Exception as e:
                await message.answer(
                    f"\u041e\u0448\u0438\u0431\u043a\u0430 \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438 \u043c\u0435\u0434\u0438\u0430: {str(e)[:200]}"
                )
                return

            incoming = IncomingMessage(
                text=task,
                user_id=str(message.from_user.id),
                channel="telegram",
                metadata={"chat_id": message.chat.id},
                media=media_items,
            )

            # Progress ticker + response — same logic as handle_task
            preview = task[:60] + ("..." if len(task) > 60 else "")
            status_msg = await message.answer(
                f"\u23f3 \u041f\u0440\u0438\u043d\u044f\u043b "
                f"\u043c\u0435\u0434\u0438\u0430:\n{preview}\n\n"
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
                steps_info = (
                    f"\u0428\u0430\u0433\u043e\u0432: {steps} | "
                    f"\u0412\u0440\u0435\u043c\u044f: {duration:.1f}s"
                )

                if response.is_file:
                    file_path = response.text
                    fname = os.path.basename(file_path)
                    is_binary = file_path.lower().endswith(BINARY_EXTENSIONS)
                    if is_binary:
                        await status_msg.edit_text(
                            f"\u2705 \u0413\u043e\u0442\u043e\u0432\u043e\n{steps_info}\n\n"
                            f"\U0001f4ce {fname}"
                        )
                        try:
                            await message.answer_document(
                                FSInputFile(file_path, filename=fname),
                            )
                        finally:
                            try:
                                os.unlink(file_path)
                            except Exception:
                                pass
                    else:
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
                    err = response.text[7:]
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

    @staticmethod
    async def _extract_video_frames(video_path: str, max_frames: int = 4) -> list[bytes]:
        """Extract up to max_frames from video using ffmpeg. Returns list of JPEG bytes."""
        try:
            # Get video duration
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                capture_output=True, text=True, timeout=10,
            )
            duration = float(probe.stdout.strip() or "0")
            if duration <= 0:
                return []

            frames = []
            # Extract frames at evenly spaced intervals
            for i in range(max_frames):
                t = duration * (i + 0.5) / max_frames
                tmp_frame = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                tmp_frame_path = tmp_frame.name
                tmp_frame.close()
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-ss", str(t), "-i", video_path,
                         "-frames:v", "1", "-q:v", "3", tmp_frame_path],
                        capture_output=True, timeout=10,
                    )
                    if os.path.exists(tmp_frame_path) and os.path.getsize(tmp_frame_path) > 0:
                        with open(tmp_frame_path, "rb") as f:
                            frames.append(f.read())
                finally:
                    try:
                        os.unlink(tmp_frame_path)
                    except Exception:
                        pass
            return frames
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # ffmpeg not available or timed out
            return []
        except Exception:
            return []

    @staticmethod
    async def _pdf_to_images(pdf_bytes: bytes, max_pages: int = 10) -> list[dict]:
        """Convert PDF pages to Vision API image blocks via pymupdf (fitz).

        Returns list of media items: [{"type": "image", "data": "<b64>", "media_type": "image/jpeg"}]
        Returns empty list if pymupdf unavailable.
        """
        try:
            import fitz  # pymupdf
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            result = []
            for i in range(min(len(doc), max_pages)):
                page = doc[i]
                mat = fitz.Matrix(2.0, 2.0)  # 2x zoom = ~144 DPI
                pix = page.get_pixmap(matrix=mat)
                img_bytes = pix.tobytes("jpeg")
                b64 = base64.b64encode(img_bytes).decode("ascii")
                result.append({"type": "image", "data": b64, "media_type": "image/jpeg"})
            doc.close()
            return result
        except Exception:
            return []

    @staticmethod
    async def _transcribe_voice(file_path: str) -> str:
        """Transcribe voice message using OpenAI Whisper API."""
        if not settings.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY not configured \u2014 voice transcription unavailable"
            )

        kwargs = {"api_key": settings.openai_api_key}
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url

        client = openai.AsyncOpenAI(**kwargs)

        with open(file_path, "rb") as audio_file:
            transcript = await client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="ru",
            )

        return transcript.text

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
