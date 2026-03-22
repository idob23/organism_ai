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


def _draw_heading(pdf, text, font_name, size, color, is_first_on_page=False):
    if not is_first_on_page:
        spacing = 8 if size >= 14 else 6
        pdf.ln(spacing)
    pdf.set_font(font_name, style="B", size=size)
    pdf.set_text_color(*color)
    pdf.multi_cell(w=0, h=size * 0.7, text=_clean_markdown(text),
                   new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font_name, size=11)
    pdf.set_text_color(0, 0, 0)
    after = 6 if size >= 14 else 4
    pdf.ln(after)


def _draw_text(pdf, text, font_name):
    pdf.set_font(font_name, size=11)
    pdf.set_text_color(0, 0, 0)
    pdf.multi_cell(w=0, h=7, text=text, new_x="LMARGIN", new_y="NEXT")


def _calc_col_widths(rows, num_cols, available_width):
    """Column widths proportional to max content length."""
    max_lengths = [0] * num_cols
    for row in rows:
        for i, cell in enumerate(row):
            if i < num_cols:
                max_lengths[i] = max(max_lengths[i], len(str(cell).strip()))

    MIN_COL = 20
    total = sum(max(ml, 3) for ml in max_lengths)
    if total == 0:
        return [available_width / num_cols] * num_cols

    widths = []
    for ml in max_lengths:
        w = max(available_width * max(ml, 3) / total, MIN_COL)
        widths.append(w)

    scale = available_width / sum(widths)
    return [w * scale for w in widths]


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
    available_width = pdf.w - 40
    col_widths = _calc_col_widths(rows, num_cols, available_width)

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

        for i, cell in enumerate(row):
            cw = col_widths[i] if i < len(col_widths) else col_widths[-1]
            cleaned = _clean_markdown(cell)
            # Truncate proportionally to column width, with ellipsis
            max_chars = max(int(cw / 2.2), 8)
            if len(cleaned) > max_chars:
                cleaned = cleaned[:max_chars - 1] + "\u2026"
            pdf.cell(cw, 8, cleaned, border=1, fill=True)
        pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.ln(5)


def _create_pdf_sync(filename: str, content: str, title: str, out_path: Path) -> None:
    """Synchronous PDF creation via fpdf2 (FIX-77: full markdown support)."""
    from fpdf import FPDF

    class OrganismPDF(FPDF):
        _font_name = "Helvetica"

        def footer(self):
            self.set_y(-15)
            self.set_font(self._font_name, size=9)
            self.set_text_color(150, 150, 150)
            self.cell(0, 10, f"{self.page_no()} / {{nb}}", align="C")

    pdf = OrganismPDF()
    pdf.alias_nb_pages()
    pdf.set_margin(20)
    pdf.set_auto_page_break(auto=True, margin=25)
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

    pdf._font_name = font_name

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
    is_first_content = True
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
            is_first_content = False
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
            is_first_content = False
            continue

        # Headings (check ### before ## before #)
        if line.startswith('### '):
            _draw_heading(pdf, line[4:], font_name, size=12, color=(51, 51, 51),
                         is_first_on_page=is_first_content)
        elif line.startswith('## '):
            _draw_heading(pdf, line[3:], font_name, size=13, color=(30, 58, 95),
                         is_first_on_page=is_first_content)
        elif line.startswith('# '):
            _draw_heading(pdf, line[2:], font_name, size=15, color=(30, 58, 95),
                         is_first_on_page=is_first_content)

        # Bullet lists
        elif line.startswith('- ') or line.startswith('* '):
            _draw_text(pdf, f"\u2022 {_clean_markdown(line[2:])}", font_name)

        # Numbered lists
        elif re.match(r'^\d+\.\s', line):
            _draw_text(pdf, _clean_markdown(line), font_name)

        # Regular text
        else:
            _draw_text(pdf, _clean_markdown(line), font_name)

        is_first_content = False
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
            "that needs to be saved as PDF. "
            "For long documents (5+ pages): use text_writer to generate .md first, "
            "then pdf_tool with source_file parameter."
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
                "source_file": {
                    "type": "string",
                    "description": "Read content from this file instead of 'content' parameter. "
                                   "Path relative to data/outputs/ (e.g. 'report.md'). "
                                   "Use for long documents: first generate .md via text_writer, then convert to PDF.",
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
        source_file = input.get("source_file", "")

        if action == "create":
            return await self._create_pdf(filename, content, title, source_file)
        elif action == "read":
            return await self._read_pdf(filename)
        else:
            return ToolResult(output="", error=f"Unknown action: {action}", exit_code=1)

    async def _create_pdf(self, filename: str, content: str, title: str,
                          source_file: str = "") -> ToolResult:
        # FIX-80: read content from file for long documents
        if source_file and not content:
            source_path = OUTPUTS_DIR / Path(source_file).name
            if source_path.exists():
                try:
                    content = source_path.read_text(encoding="utf-8")
                except Exception as e:
                    return ToolResult(output="", error=f"Cannot read source file: {e}", exit_code=1)
            else:
                return ToolResult(output="", error=f"Source file not found: {source_file}", exit_code=1)

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
