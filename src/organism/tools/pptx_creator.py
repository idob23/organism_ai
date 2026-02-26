
from pathlib import Path
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from typing import Any
from .base import BaseTool, ToolResult, OUTPUTS_DIR


class PptxCreatorTool(BaseTool):

    @property
    def name(self) -> str:
        return "pptx_creator"

    @property
    def description(self) -> str:
        return (
            "Create a PowerPoint presentation (.pptx file). "
            "Provide slides with title and content. "
            "Content can be brief — it will be expanded automatically."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "topic": {"type": "string", "description": "Presentation topic for context"},
                "slides": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "content": {"type": "string"},
                        }
                    }
                }
            },
            "required": ["filename", "slides"],
        }

    async def _expand_content(self, topic: str, slide_title: str, brief: str) -> str:
        if len(brief) > 200:
            return brief
        from src.organism.llm.claude import ClaudeProvider
        from src.organism.llm.base import Message
        llm = ClaudeProvider()
        response = await llm.complete(
            messages=[Message(role="user", content=(
                f"Тема презентации: {topic}\n"
                f"Заголовок слайда: {slide_title}\n"
                f"Краткое описание: {brief}\n\n"
                f"Напиши содержимое слайда: 4-6 тезисов через bullet points (символ •). "
                f"Язык: русский. Максимум 500 символов."
            ))],
            system="Пиши только текст слайда. Никаких вступлений. Начинай сразу с содержания.",
            model_tier="fast",
            max_tokens=600,
        )
        return response.content.strip()

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        filename: str = input["filename"]
        slides_data: list[dict] = input["slides"]
        topic: str = input.get("topic", slides_data[0].get("title", "") if slides_data else "")

        if not filename.endswith(".pptx"):
            filename += ".pptx"

        expanded = []
        for slide in slides_data:
            content = slide.get("content", "")
            title = slide.get("title", "")
            if len(content) < 200 and title and not title == slides_data[0].get("title"):
                content = await self._expand_content(topic, title, content or title)
            expanded.append({"title": title, "content": content})

        try:
            prs = Presentation()
            prs.slide_width = Inches(13.33)
            prs.slide_height = Inches(7.5)

            BG = RGBColor(0x1A, 0x1A, 0x2E)
            ACCENT = RGBColor(0xE9, 0x4F, 0x37)
            WHITE = RGBColor(0xFF, 0xFF, 0xFF)
            GRAY = RGBColor(0xB0, 0xB8, 0xC8)

            def bg(slide):
                f = slide.background.fill
                f.solid()
                f.fore_color.rgb = BG

            def text(slide, t, l, tp, w, h, size=18, bold=False, color=WHITE, align=PP_ALIGN.LEFT):
                tb = slide.shapes.add_textbox(Inches(l), Inches(tp), Inches(w), Inches(h))
                tf = tb.text_frame
                tf.word_wrap = True
                p = tf.paragraphs[0]
                p.alignment = align
                r = p.add_run()
                r.text = t
                r.font.size = Pt(size)
                r.font.bold = bold
                r.font.color.rgb = color

            def rect(slide, l, tp, w, h, color):
                s = slide.shapes.add_shape(1, Inches(l), Inches(tp), Inches(w), Inches(h))
                s.fill.solid()
                s.fill.fore_color.rgb = color
                s.line.fill.background()

            for i, s in enumerate(expanded):
                layout = prs.slide_layouts[6]
                slide = prs.slides.add_slide(layout)
                bg(slide)

                if i == 0:
                    rect(slide, 0.4, 2.6, 0.1, 2.0, ACCENT)
                    text(slide, s["title"], 0.8, 2.5, 11.5, 1.5, size=36, bold=True)
                    if s["content"]:
                        text(slide, s["content"], 0.8, 4.3, 11.5, 1.8, size=20, color=GRAY)
                    rect(slide, 0.5, 6.85, 12.3, 0.05, ACCENT)
                else:
                    rect(slide, 0, 0, 13.33, 0.07, ACCENT)
                    text(slide, s["title"], 0.5, 0.2, 12.3, 0.9, size=28, bold=True)
                    rect(slide, 0.5, 1.15, 12.3, 0.02, GRAY)
                    if s["content"]:
                        text(slide, s["content"], 0.5, 1.3, 12.3, 5.7, size=18)
                    text(slide, str(i + 1), 12.4, 6.9, 0.7, 0.4, size=12, color=GRAY, align=PP_ALIGN.RIGHT)

            filepath = OUTPUTS_DIR / Path(filename).name
            prs.save(str(filepath))
            return ToolResult(output=f"Created: {filepath} ({len(expanded)} slides)", exit_code=0)

        except Exception as e:
            return ToolResult(output="", error=str(e), exit_code=1)
