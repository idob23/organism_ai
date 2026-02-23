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
            "Send messages or files to a Telegram chat. "
            "Use to deliver results, reports, or notifications to the user."
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
        chat_id = input["chat_id"]
        text = input["text"]

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
