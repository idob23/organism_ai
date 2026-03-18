import asyncio
import base64
import html
import io
import os
import subprocess
import tempfile

import openai
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from src.organism.channels.base import BaseChannel, IncomingMessage, OutgoingMessage
from config.settings import settings

# FIX-21: Binary file extensions that must not be read as text
BINARY_EXTENSIONS = (".xlsx", ".pptx", ".pdf", ".docx", ".zip", ".png", ".jpg", ".jpeg")

# TG-UX: Max stored task texts for retry (prevents unbounded growth)
_MAX_TASK_TEXTS = 100


def _escape_html(text: str) -> str:
    """Escape HTML special characters for Telegram HTML parse_mode."""
    return html.escape(text, quote=False)


def _stop_keyboard(msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="\u274c \u041e\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c",
            callback_data=f"cancel:{msg_id}",
        )]
    ])


def _retry_keyboard(msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="\U0001f504 \u041f\u043e\u0432\u0442\u043e\u0440\u0438\u0442\u044c",
            callback_data=f"retry:{msg_id}",
        )]
    ])


class TelegramChannel(BaseChannel):

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self.bot = Bot(token=settings.telegram_bot_token)
        self.dp = Dispatcher()
        # TG-UX: active tasks for cancel, task texts for retry
        self._active_tasks: dict[int, tuple[asyncio.Task, str]] = {}
        self._task_texts: dict[int, str] = {}
        self._setup_handlers()

    # \u2500\u2500 Progress ticker \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    async def _tick_progress(self, msg: Message, preview: str, stop_kb: InlineKeyboardMarkup | None = None) -> None:
        """Edit status message every 5s with elapsed time while task runs."""
        icons = ["\u23f3", "\U0001f504"]
        elapsed = 0
        self._current_tool: str = ""
        while True:
            await asyncio.sleep(5)
            elapsed += 5
            icon = icons[(elapsed // 5) % 2]
            tool_hint = ""
            if self._current_tool:
                tool_hint = f"\n\U0001f527 {_escape_html(self._current_tool)}"
            try:
                await msg.edit_text(
                    f"{icon} <b>\u0412\u044b\u043f\u043e\u043b\u043d\u044f\u044e...</b> {elapsed}\u0441"
                    f"\n<i>{_escape_html(preview)}</i>{tool_hint}",
                    parse_mode="HTML",
                    reply_markup=stop_kb,
                )
            except Exception:
                pass  # ignore FloodWait / MessageNotModified

    # \u2500\u2500 Result sender (DRY) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    async def _send_result(
        self, status_msg: Message, response: OutgoingMessage,
        message: Message, original_task: str,
    ) -> None:
        """Send task result to user with HTML formatting and retry button."""
        retry_kb = _retry_keyboard(status_msg.message_id)

        # Store for retry
        if len(self._task_texts) >= _MAX_TASK_TEXTS:
            oldest = next(iter(self._task_texts))
            self._task_texts.pop(oldest, None)
        self._task_texts[status_msg.message_id] = original_task

        duration = response.metadata.get("duration", 0)
        steps = response.metadata.get("steps", 0)
        meta_line = f"<i>\u23f1 {duration:.1f}\u0441 \u00b7 \U0001f527 {steps} \u0448\u0430\u0433\u043e\u0432</i>"

        if response.is_file:
            file_path = response.text
            fname = os.path.basename(file_path)
            is_binary = file_path.lower().endswith(BINARY_EXTENSIONS)
            _caption = (response.caption or "")[:1024] or None

            if is_binary:
                try:
                    await status_msg.edit_text(
                        f"<b>\u2705 \u0413\u043e\u0442\u043e\u0432\u043e</b>\n{meta_line}\n\n"
                        f"\U0001f4ce {_escape_html(fname)}",
                        parse_mode="HTML", reply_markup=retry_kb,
                    )
                except Exception:
                    try:
                        await status_msg.edit_text(
                            f"\u2705 \u0413\u043e\u0442\u043e\u0432\u043e\n\U0001f4ce {fname}",
                            reply_markup=retry_kb,
                        )
                    except Exception:
                        pass
                try:
                    await message.answer_document(
                        FSInputFile(file_path, filename=fname), caption=_caption,
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
                    short_preview = _escape_html(full_text[:500]) + "..."
                    try:
                        await status_msg.edit_text(
                            f"<b>\u2705 \u0413\u043e\u0442\u043e\u0432\u043e</b>\n{meta_line}\n\n"
                            f"{short_preview}\n\n"
                            f"\U0001f4ce \u041f\u043e\u043b\u043d\u044b\u0439 \u0442\u0435\u043a\u0441\u0442 \u0432\u043e \u0432\u043b\u043e\u0436\u0435\u043d\u0438\u0438:",
                            parse_mode="HTML", reply_markup=retry_kb,
                        )
                    except Exception:
                        await status_msg.edit_text(
                            f"\u2705 \u0413\u043e\u0442\u043e\u0432\u043e\n\U0001f4ce \u041f\u043e\u043b\u043d\u044b\u0439 \u0442\u0435\u043a\u0441\u0442 \u0432\u043e \u0432\u043b\u043e\u0436\u0435\u043d\u0438\u0438:",
                            reply_markup=retry_kb,
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
                    try:
                        await status_msg.edit_text(
                            f"<b>\u2705 \u0413\u043e\u0442\u043e\u0432\u043e</b>\n{meta_line}",
                            parse_mode="HTML", reply_markup=retry_kb,
                        )
                    except Exception:
                        pass

            # FIX-78: Send extra files (multi-file tasks)
            for extra_path in (response.metadata or {}).get("extra_files", []):
                try:
                    await message.answer_document(
                        FSInputFile(extra_path, filename=os.path.basename(extra_path)),
                    )
                except Exception:
                    pass
                finally:
                    try:
                        os.unlink(extra_path)
                    except Exception:
                        pass

        elif response.text.startswith("Error:"):
            err = response.text[7:]
            if "Traceback" in err or "File \"/" in err:
                err = err.splitlines()[-1]
            full = (
                "<b>\u274c \u041e\u0448\u0438\u0431\u043a\u0430</b>\n"
                f"<i>{_escape_html(err[:300])}</i>\n\n"
                "\u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u043f\u0435\u0440\u0435\u0444\u043e\u0440\u043c\u0443\u043b\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0437\u0430\u0434\u0430\u0447\u0443."
            )
            try:
                await status_msg.edit_text(full, parse_mode="HTML", reply_markup=retry_kb)
            except Exception:
                try:
                    await status_msg.edit_text(
                        f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430\n\n{err[:300]}",
                        reply_markup=retry_kb,
                    )
                except Exception:
                    pass
        else:
            body = _escape_html(response.text)
            full = (
                f"<b>\u2705 \u0413\u043e\u0442\u043e\u0432\u043e</b>\n{meta_line}\n\n"
                f"{body}"
            )
            try:
                await status_msg.edit_text(full, parse_mode="HTML", reply_markup=retry_kb)
            except Exception:
                # Fallback: plain text without parse_mode
                plain = f"\u2705 \u0413\u043e\u0442\u043e\u0432\u043e\n\n{response.text}"
                try:
                    await status_msg.edit_text(plain, reply_markup=retry_kb)
                except Exception:
                    pass

    # \u2500\u2500 Core task runner \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    async def _run_task(
        self, message: Message, task: str, media: list | None = None,
    ) -> None:
        """Run a task with progress ticker, cancel/retry buttons, HTML output."""
        preview = task[:60] + ("..." if len(task) > 60 else "")
        status_msg = await message.answer(
            f"<b>\U0001f4cb \u041f\u0440\u0438\u043d\u044f\u043b \u0437\u0430\u0434\u0430\u0447\u0443</b>\n"
            f"<i>{_escape_html(preview)}</i>\n\n\u23f3 \u0412\u044b\u043f\u043e\u043b\u043d\u044f\u044e...",
            parse_mode="HTML",
            reply_markup=_stop_keyboard(0),  # placeholder, updated below
        )

        # Update stop keyboard with real message_id
        stop_kb = _stop_keyboard(status_msg.message_id)
        try:
            await status_msg.edit_reply_markup(reply_markup=stop_kb)
        except Exception:
            pass

        # TG-UX: tool progress callback
        async def _tool_progress(tool_name: str, round_num: int, max_rounds: int) -> None:
            self._current_tool = f"{tool_name} (\u0448\u0430\u0433 {round_num}/{max_rounds})"

        # Q-9.9: Progress callback for task decomposition
        async def _subtask_progress(current: int, total: int, subtask_preview: str) -> None:
            try:
                await status_msg.edit_text(
                    f"<b>\u23f3 \u0412\u044b\u043f\u043e\u043b\u043d\u044f\u044e</b>\n"
                    f"<i>{_escape_html(preview)}</i>\n\n"
                    f"\u0427\u0430\u0441\u0442\u044c {current}/{total}: "
                    f"{_escape_html(subtask_preview[:60])}...",
                    parse_mode="HTML",
                    reply_markup=stop_kb,
                )
            except Exception:
                pass

        incoming = IncomingMessage(
            text=task,
            user_id=str(message.from_user.id),
            channel="telegram",
            metadata={
                "chat_id": message.chat.id,
                "progress_callback": _subtask_progress,
                "tool_progress_callback": _tool_progress,
            },
            media=media or [],
        )

        ticker = asyncio.create_task(self._tick_progress(status_msg, preview, stop_kb))
        gateway_task = asyncio.create_task(self.gateway.handle_message(incoming))
        self._active_tasks[status_msg.message_id] = (gateway_task, task)

        try:
            response = await gateway_task
        except asyncio.CancelledError:
            # FIX-86: Task.cancel() interrupts any await — show cancelled UI
            ticker.cancel()
            self._active_tasks.pop(status_msg.message_id, None)
            self._current_tool = ""
            retry_kb = _retry_keyboard(status_msg.message_id)
            if len(self._task_texts) >= _MAX_TASK_TEXTS:
                oldest = next(iter(self._task_texts))
                self._task_texts.pop(oldest, None)
            self._task_texts[status_msg.message_id] = task
            try:
                await status_msg.edit_text(
                    "<b>\u26d4 \u041e\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u043e</b>\n"
                    "<i>\u0417\u0430\u0434\u0430\u0447\u0430 \u043e\u0442\u043c\u0435\u043d\u0435\u043d\u0430 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0435\u043c</i>",
                    parse_mode="HTML", reply_markup=retry_kb,
                )
            except Exception:
                try:
                    await status_msg.edit_text(
                        "\u26d4 \u041e\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u043e",
                        reply_markup=retry_kb,
                    )
                except Exception:
                    pass
            return
        except Exception:
            ticker.cancel()
            self._active_tasks.pop(status_msg.message_id, None)
            self._current_tool = ""
            retry_kb = _retry_keyboard(status_msg.message_id)
            if len(self._task_texts) >= _MAX_TASK_TEXTS:
                oldest = next(iter(self._task_texts))
                self._task_texts.pop(oldest, None)
            self._task_texts[status_msg.message_id] = task
            try:
                await status_msg.edit_text(
                    "<b>\u26a0\ufe0f \u0412\u043d\u0443\u0442\u0440\u0435\u043d\u043d\u044f\u044f \u043e\u0448\u0438\u0431\u043a\u0430</b>\n"
                    "<i>\u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u0435\u0449\u0451 \u0440\u0430\u0437.</i>",
                    parse_mode="HTML", reply_markup=retry_kb,
                )
            except Exception:
                try:
                    await status_msg.edit_text(
                        "\u26a0\ufe0f \u0412\u043d\u0443\u0442\u0440\u0435\u043d\u043d\u044f\u044f \u043e\u0448\u0438\u0431\u043a\u0430. "
                        "\u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439\u0442\u0435 \u0435\u0449\u0451 \u0440\u0430\u0437.",
                        reply_markup=retry_kb,
                    )
                except Exception:
                    pass
            return
        finally:
            ticker.cancel()
            self._active_tasks.pop(status_msg.message_id, None)
            self._current_tool = ""

        await self._send_result(status_msg, response, message, task)

    # \u2500\u2500 Handlers \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

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

            # Commands \u2014 no progress ticker / buttons needed
            if self.gateway.cmd_handler.is_command(task):
                incoming = IncomingMessage(
                    text=task,
                    user_id=str(message.from_user.id),
                    channel="telegram",
                    metadata={"chat_id": message.chat.id},
                )
                response = await self.gateway.handle_message(incoming)
                await message.answer(response.text)
                return

            await self._run_task(message, task)

        @self.dp.message(F.voice)
        async def handle_voice(message: Message) -> None:
            if allowed and message.from_user.id not in allowed:
                await message.answer("Access denied.")
                return

            status_msg = await message.answer(
                "\U0001f3a4 \u0420\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u044e \u0433\u043e\u043b\u043e\u0441..."
            )

            try:
                file = await self.bot.get_file(message.voice.file_id)
                tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
                tmp_path = tmp.name
                tmp.close()
                await self.bot.download_file(file.file_path, tmp_path)

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

                preview = text[:100] + ("..." if len(text) > 100 else "")
                await status_msg.edit_text(
                    f"\U0001f4ac \u0420\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u043d\u043e: {preview}"
                )

                await self._run_task(message, text)

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
                        file = await self.bot.get_file(doc.file_id)
                        buf = io.BytesIO()
                        await self.bot.download_file(file.file_path, buf)
                        pages = await self._pdf_to_images(buf.getvalue())
                        if pages:
                            media_items.extend(pages)
                        else:
                            await message.answer(
                                "\u274c \u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u0442\u044c PDF: "
                                "poppler \u043d\u0435 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d. "
                                "\u0423\u0441\u0442\u0430\u043d\u043e\u0432\u0438: apt-get install poppler-utils "
                                "\u0438\u043b\u0438 \u043e\u0442\u043f\u0440\u0430\u0432\u044c \u0441\u043e\u0434\u0435\u0440\u0436\u0438\u043c\u043e\u0435 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430 \u043a\u0430\u043a \u0442\u0435\u043a\u0441\u0442."
                            )
                            return
                    else:
                        fname = doc.file_name or "document"
                        try:
                            file = await self.bot.get_file(doc.file_id)
                            buf = io.BytesIO()
                            await self.bot.download_file(file.file_path, buf)
                            raw = buf.getvalue()
                            text_content = raw.decode("utf-8", errors="replace")
                            if "\x00" not in text_content:
                                snippet = text_content[:8000]
                                task = (
                                    f"[\u0421\u043e\u0434\u0435\u0440\u0436\u0438\u043c\u043e\u0435"
                                    f" \u0444\u0430\u0439\u043b\u0430 {fname}]:\n"
                                    f"{snippet}\n\n{task}"
                                )
                            else:
                                task = f"[{fname}] {task}"
                        except Exception:
                            task = f"[{fname}] {task}"

                elif message.video:
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

            await self._run_task(message, task, media=media_items)

        # TG-UX: Callback handlers for cancel/retry buttons
        @self.dp.callback_query(lambda c: c.data and c.data.startswith("cancel:"))
        async def on_cancel(callback: CallbackQuery) -> None:
            msg_id = int(callback.data.split(":")[1])
            entry = self._active_tasks.get(msg_id)
            if entry:
                gateway_task, _ = entry
                gateway_task.cancel()
                await callback.answer("\u041e\u0441\u0442\u0430\u043d\u0430\u0432\u043b\u0438\u0432\u0430\u044e...")
            else:
                await callback.answer("\u0417\u0430\u0434\u0430\u0447\u0430 \u0443\u0436\u0435 \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0430")

        @self.dp.callback_query(lambda c: c.data and c.data.startswith("retry:"))
        async def on_retry(callback: CallbackQuery) -> None:
            msg_id = int(callback.data.split(":")[1])
            original_task = self._task_texts.get(msg_id)
            if original_task:
                await callback.answer("\u041f\u0435\u0440\u0435\u0437\u0430\u043f\u0443\u0441\u043a\u0430\u044e...")
                try:
                    await callback.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
                await self._run_task(callback.message, original_task)
            else:
                await callback.answer("\u0417\u0430\u0434\u0430\u0447\u0430 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430")

    # \u2500\u2500 Static helpers \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    @staticmethod
    async def _extract_video_frames(video_path: str, max_frames: int = 4) -> list[bytes]:
        """Extract up to max_frames from video using ffmpeg. Returns list of JPEG bytes."""
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                capture_output=True, text=True, timeout=10,
            )
            duration = float(probe.stdout.strip() or "0")
            if duration <= 0:
                return []

            frames = []
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
            return []
        except Exception:
            return []

    @staticmethod
    async def _pdf_to_images(pdf_bytes: bytes, max_pages: int = 10) -> list[dict]:
        """Convert PDF pages to Vision API image blocks via pymupdf (fitz)."""
        try:
            import fitz  # pymupdf
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            result = []
            for i in range(min(len(doc), max_pages)):
                page = doc[i]
                mat = fitz.Matrix(2.0, 2.0)
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
            uids = settings.allowed_user_ids
            if uids:
                chat_id = uids[0]
            else:
                return
        try:
            if message.is_file:
                fname = os.path.basename(message.text)
                _caption = (message.caption or "")[:1024] or None
                await self.bot.send_document(
                    chat_id, FSInputFile(message.text, filename=fname),
                    caption=_caption,
                )
            else:
                try:
                    await self.bot.send_message(
                        chat_id, message.text, parse_mode="HTML",
                    )
                except Exception:
                    await self.bot.send_message(chat_id, message.text)
        except Exception:
            pass
