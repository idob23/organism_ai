# PDF — how to create via code_executor + fpdf2

For PDF documents use code_executor with fpdf2 library.
This is the ONLY way to create long professional PDFs (reports, business plans, memos).
pdf_tool is NOT suitable for documents longer than 2-3 pages.

## Important: sandbox paths
- Fonts: /sandbox/fonts/DejaVuSans.ttf and DejaVuSans-Bold.ttf (always available)
- Save files to: /output/filename.pdf
- After saving: print("Saved files: filename.pdf")

## Base template
```python
from fpdf import FPDF

pdf = FPDF()
pdf.set_margin(20)
pdf.set_auto_page_break(auto=True, margin=20)

# Fonts — always load DejaVu for Cyrillic
pdf.add_font("DejaVu", fname="/sandbox/fonts/DejaVuSans.ttf")
pdf.add_font("DejaVu", style="B", fname="/sandbox/fonts/DejaVuSans-Bold.ttf")
FONT = "DejaVu"

def add_title(text):
    pdf.add_page()
    pdf.set_font(FONT, style="B", size=20)
    pdf.set_text_color(30, 58, 95)
    pdf.cell(w=0, h=15, text=text, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)

def add_heading(text, level=2):
    sizes = {1: 16, 2: 14, 3: 12}
    pdf.set_font(FONT, style="B", size=sizes.get(level, 12))
    pdf.set_text_color(30, 58, 95)
    pdf.cell(w=0, h=10, text=text, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(FONT, size=11)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

def add_text(text):
    pdf.set_font(FONT, size=11)
    pdf.multi_cell(w=0, h=7, text=text, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

def add_bullet(text):
    pdf.set_font(FONT, size=11)
    pdf.multi_cell(w=0, h=7, text=f"\u2022 {text}", new_x="LMARGIN", new_y="NEXT")

def add_hr():
    y = pdf.get_y()
    pdf.set_draw_color(180, 180, 180)
    pdf.line(20, y, 190, y)
    pdf.ln(5)

def add_table(headers, rows):
    """Table with dark header and alternating row colors."""
    col_width = (pdf.w - 40) / len(headers)

    # Header
    pdf.set_font(FONT, style="B", size=10)
    pdf.set_fill_color(30, 58, 95)
    pdf.set_text_color(255, 255, 255)
    for h in headers:
        pdf.cell(col_width, 8, str(h)[:40], border=1, fill=True, align="C")
    pdf.ln()

    # Data rows
    pdf.set_font(FONT, size=10)
    pdf.set_text_color(0, 0, 0)
    for i, row in enumerate(rows):
        if i % 2 == 0:
            pdf.set_fill_color(245, 245, 245)
        else:
            pdf.set_fill_color(255, 255, 255)
        for cell in row:
            pdf.cell(col_width, 8, str(cell)[:40], border=1, fill=True)
        pdf.ln()

    pdf.set_text_color(0, 0, 0)
    pdf.ln(5)

# === DOCUMENT CONTENT ===

add_title("DOCUMENT TITLE")

add_heading("1. First Section")
add_text("Section text. Description, analysis, conclusions.")
add_text("Second paragraph with additional data.")

add_heading("2. Data Table")
add_table(
    ["Indicator", "Value", "Unit"],
    [
        ["Revenue", "18 000 000", "RUB"],
        ["Expenses", "11 600 000", "RUB"],
        ["Profit", "6 400 000", "RUB"],
    ]
)

add_heading("3. Recommendations")
add_bullet("First recommendation with justification")
add_bullet("Second recommendation")
add_bullet("Third recommendation")

add_hr()
add_text("Date: March 16, 2026")
add_text("Signature: ________________________")

pdf.output("/output/document.pdf")
print("Saved files: document.pdf")
```

## Formatting rules
- Font: always DejaVu (Cyrillic support), load from /sandbox/fonts/
- Document title: bold, size=20, color #1E3A5F, centered
- Section headings: bold, size=14, color #1E3A5F
- Body text: regular, size=11, black
- Tables: dark header (#1E3A5F white text), alternating row colors
- Margins: 20mm all sides
- Auto page break: enabled (set_auto_page_break)
- For long documents: describe content directly in code via add_text/add_heading/add_table calls
- Save file to /output/, print "Saved files: filename.pdf"

## When to use
- Any PDF longer than 1-2 pages
- Business plans, reports, memos, instructions
- Documents with tables and structured formatting
