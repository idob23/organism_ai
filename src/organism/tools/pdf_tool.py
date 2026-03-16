"""TOOL-1: PDF creation and reading tool (FIX-57c: fpdf2 for Unicode, FIX-77: full markdown)."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from .base import BaseTool, ToolResult, OUTPUTS_DIR

_FONTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config" / "fonts"


def _clean_markdown(text: str) -> str:
    """Strip markdown inline formatting, keep readable text."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)
    return text


def _draw_hr(pdf):
    """Horizontal rule — thin gray line."""
    y = pdf.get_y()
    pdf.set_draw_color(180, 180, 180)
    pdf.line(20, y, 190, y)
    pdf.ln(5)


def _draw_heading(pdf, text, font_name, size, color):
    pdf.set_font(font_name, style="B", size=size)
    pdf.set_text_color(*color)
    pdf.multi_cell(w=0, h=size * 0.7, text=_clean_markdown(text),
                   new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font_name, size=11)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)


def _draw_text(pdf, text, font_name):
    pdf.set_font(font_name, size=11)
    pdf.set_text_color(0, 0, 0)
    pdf.multi_cell(w=0, h=7, text=text, new_x="LMARGIN", new_y="NEXT")


def _draw_table(pdf, table_lines, font_name):
    """Parse markdown table lines and render with fpdf2."""
    rows = []
    for tl in table_lines:
        cells = [c.strip() for c in tl.strip('|').split('|')]
        if all(re.match(r'^[-:]+$', c) for c in cells if c):
            continue
        rows.append(cells)

    if not rows:
        return

    num_cols = max(len(r) for r in rows)
    col_width = (pdf.w - 40) / num_cols

    for row_idx, row in enumerate(rows):
        while len(row) < num_cols:
            row.append("")

        is_header = (row_idx == 0)

        if is_header:
            pdf.set_font(font_name, style="B", size=10)
            pdf.set_fill_color(30, 58, 95)
            pdf.set_text_color(255, 255, 255)
        else:
            pdf.set_font(font_name, size=10)
            pdf.set_text_color(0, 0, 0)
            if row_idx % 2 == 0:
                pdf.set_fill_color(245, 245, 245)
            else:
                pdf.set_fill_color(255, 255, 255)

        for cell in row:
            pdf.cell(col_width, 8, _clean_markdown(cell)[:50], border=1, fill=True)
        pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.ln(5)


def _create_pdf_sync(filename: str, content: str, title: str, out_path: Path) -> None:
    """Synchronous PDF creation via fpdf2 (FIX-77: full markdown support)."""
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

    # Title
    if title:
        pdf.set_font(font_name, style="B", size=16)
        pdf.set_text_color(30, 58, 95)
        pdf.multi_cell(w=0, h=10, text=title, align="C",
                       new_x="LMARGIN", new_y="NEXT")
        pdf.ln(5)

    # Body — FIX-77: full markdown parser
    pdf.set_font(font_name, size=11)
    pdf.set_text_color(0, 0, 0)

    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Empty line
        if not line:
            pdf.ln(4)
            i += 1
            continue

        # Horizontal rule
        if re.match(r'^[-*_]{3,}$', line):
            _draw_hr(pdf)
            i += 1
            continue

        # Table block (lines containing |, not headings)
        if '|' in line and not line.startswith('#'):
            table_lines = []
            while i < len(lines) and '|' in lines[i].strip():
                table_lines.append(lines[i].strip())
                i += 1
            try:
                _draw_table(pdf, table_lines, font_name)
            except Exception:
                for tl in table_lines:
                    _draw_text(pdf, _clean_markdown(tl), font_name)
            continue

        # Headings (check ### before ## before #)
        if line.startswith('### '):
            _draw_heading(pdf, line[4:], font_name, size=12, color=(51, 51, 51))
        elif line.startswith('## '):
            _draw_heading(pdf, line[3:], font_name, size=13, color=(30, 58, 95))
        elif line.startswith('# '):
            _draw_heading(pdf, line[2:], font_name, size=15, color=(30, 58, 95))

        # Bullet lists
        elif line.startswith('- ') or line.startswith('* '):
            _draw_text(pdf, f"\u2022 {_clean_markdown(line[2:])}", font_name)

        # Numbered lists
        elif re.match(r'^\d+\.\s', line):
            _draw_text(pdf, _clean_markdown(line), font_name)

        # Regular text
        else:
            _draw_text(pdf, _clean_markdown(line), font_name)

        i += 1

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
