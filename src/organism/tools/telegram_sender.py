from typing import Any
import httpx
from .base import BaseTool, ToolResult


class TelegramSenderTool(BaseTool):

    def __init__(self, bot_token: str) -> None:
        self._token = bot_token
        self._base = f"https://api.telegram.org/bot{bot_token}"

    @property
    def name(self) -> str:
        return "telegram_sender"

    @property
    def description(self) -> str:
        return (
            "Send messages to a user's personal Telegram chat. "
            "Use to deliver results, reports, or notifications to the user. "
            "Cannot send to channels \u2014 for channel publishing use "
            "manage_schedule tool with action=publish."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": "Telegram chat ID to send to",
                },
                "text": {
                    "type": "string",
                    "description": "Message text to send",
                },
            },
            "required": ["chat_id", "text"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        chat_id = str(input["chat_id"])
        text = input["text"]

        if chat_id.startswith("@") or chat_id.startswith("-100"):
            return ToolResult(
                output="",
                error=(
                    "\u041e\u0442\u043f\u0440\u0430\u0432\u043a\u0430 \u0432 \u043a\u0430\u043d\u0430\u043b\u044b "
                    "\u0447\u0435\u0440\u0435\u0437 telegram_sender \u0437\u0430\u043f\u0440\u0435\u0449\u0435\u043d\u0430. "
                    "\u0414\u043b\u044f \u043f\u0443\u0431\u043b\u0438\u043a\u0430\u0446\u0438\u0438 \u043f\u043e\u0441\u0442\u043e\u0432 "
                    "\u0438\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439 manage_schedule action=publish."
                ),
                exit_code=1,
            )

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"{self._base}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "Markdown",
                    }
                )
                data = response.json()

            if data.get("ok"):
                return ToolResult(output="Message sent successfully")
            return ToolResult(output="", error=data.get("description", "Unknown error"), exit_code=1)

        except Exception as e:
            return ToolResult(output="", error=str(e), exit_code=1)
