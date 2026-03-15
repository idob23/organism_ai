"""TOOL-1: PDF creation and reading tool (FIX-57c: fpdf2 for Unicode)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult, OUTPUTS_DIR

_FONTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config" / "fonts"


def _create_pdf_sync(filename: str, content: str, title: str, out_path: Path) -> None:
    """Synchronous PDF creation via fpdf2."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_margin(20)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # FIX-57c: DejaVuSans for Cyrillic, Helvetica fallback
    font_name = "Helvetica"
    try:
        regular = _FONTS_DIR / "DejaVuSans.ttf"
        bold = _FONTS_DIR / "DejaVuSans-Bold.ttf"
        if regular.exists() and bold.exists():
            pdf.add_font("DejaVu", fname=str(regular))
            pdf.add_font("DejaVu", style="B", fname=str(bold))
            font_name = "DejaVu"
    except Exception:
        font_name = "Helvetica"

    _mc = dict(w=0, new_x="LMARGIN", new_y="NEXT")

    # Title
    if title:
        pdf.set_font(font_name, style="B", size=16)
        pdf.set_text_color(30, 58, 95)  # #1E3A5F
        pdf.multi_cell(**_mc, h=10, text=title, align="C")
        pdf.ln(5)

    # Body
    pdf.set_font(font_name, size=11)
    pdf.set_text_color(0, 0, 0)

    for line in content.split("\n"):
        line = line.strip()
        if not line:
            pdf.ln(4)
            continue
        if line.startswith("## "):
            pdf.set_font(font_name, style="B", size=13)
            pdf.multi_cell(**_mc, h=8, text=line[3:])
            pdf.set_font(font_name, size=11)
        elif line.startswith("# "):
            pdf.set_font(font_name, style="B", size=15)
            pdf.multi_cell(**_mc, h=10, text=line[2:])
            pdf.set_font(font_name, size=11)
        elif line.startswith("- ") or line.startswith("* "):
            pdf.multi_cell(**_mc, h=7, text=f"\u2022 {line[2:]}")
        else:
            pdf.multi_cell(**_mc, h=7, text=line)

    pdf.output(str(out_path))


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
        if not filename.endswith(".pdf"):
            filename += ".pdf"

        out_path = OUTPUTS_DIR / filename

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, _create_pdf_sync, filename, content, title, out_path,
            )
            return ToolResult(output=f"Saved files: {filename}",
                              created_files=[filename])
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
