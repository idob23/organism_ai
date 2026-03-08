"""TOOL-1: PDF creation and reading tool."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult, OUTPUTS_DIR


class PdfTool(BaseTool):

    @property
    def name(self) -> str:
        return "pdf_tool"

    @property
    def description(self) -> str:
        return (
            "Create PDF files from text/markdown content, or extract text from existing PDFs. "
            "Use for: reports, proposals, grant applications, commercial offers, any document "
            "that needs to be saved as PDF."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "read"],
                    "description": "create: generate PDF from content. read: extract text from PDF file.",
                },
                "filename": {
                    "type": "string",
                    "description": "Output filename for create (e.g. report.pdf), or input path for read.",
                },
                "content": {
                    "type": "string",
                    "description": "Text or markdown content to put in PDF (for create action).",
                },
                "title": {
                    "type": "string",
                    "description": "Document title (for create action).",
                    "default": "",
                },
            },
            "required": ["action", "filename"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        action = input.get("action", "create")
        filename = input.get("filename", "output.pdf")
        content = input.get("content", "")
        title = input.get("title", "")

        if action == "create":
            return await self._create_pdf(filename, content, title)
        elif action == "read":
            return await self._read_pdf(filename)
        else:
            return ToolResult(output="", error=f"Unknown action: {action}", exit_code=1)

    async def _create_pdf(self, filename: str, content: str, title: str) -> ToolResult:
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import cm
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
            from reportlab.lib.enums import TA_LEFT
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
        except ImportError:
            return ToolResult(
                output="",
                error="Install reportlab: pip install reportlab",
                exit_code=1,
            )

        if not filename.endswith(".pdf"):
            filename += ".pdf"

        out_path = OUTPUTS_DIR / filename

        try:
            doc = SimpleDocTemplate(
                str(out_path),
                pagesize=A4,
                rightMargin=2 * cm,
                leftMargin=2 * cm,
                topMargin=2 * cm,
                bottomMargin=2 * cm,
            )

            styles = getSampleStyleSheet()
            story = []

            if title:
                title_style = ParagraphStyle(
                    "CustomTitle",
                    parent=styles["Title"],
                    fontSize=16,
                    spaceAfter=20,
                )
                story.append(Paragraph(title, title_style))
                story.append(Spacer(1, 0.5 * cm))

            body_style = ParagraphStyle(
                "CustomBody",
                parent=styles["Normal"],
                fontSize=11,
                leading=16,
                spaceAfter=8,
            )

            for line in content.split("\n"):
                line = line.strip()
                if not line:
                    story.append(Spacer(1, 0.3 * cm))
                    continue
                # Basic markdown: ## headers
                if line.startswith("## "):
                    h_style = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=13)
                    story.append(Paragraph(line[3:], h_style))
                elif line.startswith("# "):
                    h_style = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=15)
                    story.append(Paragraph(line[2:], h_style))
                elif line.startswith("- ") or line.startswith("* "):
                    bullet_style = ParagraphStyle(
                        "Bullet", parent=styles["Normal"],
                        fontSize=11, leftIndent=20, spaceAfter=4,
                    )
                    story.append(Paragraph(f"\u2022 {line[2:]}", bullet_style))
                else:
                    story.append(Paragraph(line, body_style))

            doc.build(story)
            return ToolResult(output=f"PDF created: {out_path}\nFile: {filename}")

        except Exception as e:
            return ToolResult(output="", error=f"PDF creation failed: {e}", exit_code=1)

    async def _read_pdf(self, filename: str) -> ToolResult:
        try:
            import PyPDF2
        except ImportError:
            return ToolResult(output="", error="Install pypdf2: pip install pypdf2", exit_code=1)

        try:
            path = Path(filename) if Path(filename).exists() else OUTPUTS_DIR / filename
            if not path.exists():
                return ToolResult(output="", error=f"File not found: {filename}", exit_code=1)

            text_parts = []
            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text_parts.append(page.extract_text())

            text = "\n".join(text_parts)
            return ToolResult(output=text[:5000])

        except Exception as e:
            return ToolResult(output="", error=f"PDF read failed: {e}", exit_code=1)
