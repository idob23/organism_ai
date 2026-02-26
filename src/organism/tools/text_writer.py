from typing import Any
from pathlib import Path
from .base import BaseTool, ToolResult, OUTPUTS_DIR


class TextWriterTool(BaseTool):

    @property
    def name(self) -> str:
        return "text_writer"

    @property
    def description(self) -> str:
        return (
            "Write long-form text content (articles, proposals, reports, letters) and save to file. "
            "Use this for any writing task that needs to be saved. "
            "Generates content via AI and saves directly  no JSON size limits."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What to write  full instructions"},
                "filename": {"type": "string", "description": "File to save to (e.g. report.md)"},
                "language": {"type": "string", "default": "ru", "description": "Language: ru or en"},
            },
            "required": ["prompt", "filename"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        prompt: str = input["prompt"]
        filename: str = input["filename"]
        language: str = input.get("language", "ru")

        from src.organism.llm.claude import ClaudeProvider
        from src.organism.llm.base import Message

        llm = ClaudeProvider()

        system = (
            "Ты профессиональный копирайтер и бизнес-консультант. "
            "Пиши структурированно, убедительно, профессионально. "
            "Используй markdown-форматирование. "
            "Отвечай только текстом документа, без вступлений типа 'Вот текст:'."
        ) if language == "ru" else (
            "You are a professional copywriter and business consultant. "
            "Write structured, persuasive, professional content in Markdown."
        )

        response = await llm.complete(
            messages=[Message(role="user", content=prompt)],
            system=system,
            model_tier="balanced",
            max_tokens=4000,
        )

        content = response.content.strip()

        try:
            filepath = OUTPUTS_DIR / Path(filename).name
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            return ToolResult(
                output=f"Saved to {filepath} ({len(content)} chars)\n\nPreview:\n{content[:300]}...",
                exit_code=0,
            )
        except Exception as e:
            return ToolResult(output="", error=str(e), exit_code=1)
