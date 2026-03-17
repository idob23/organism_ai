# PDF — how to create documents

## Routing by document length

**Short PDF (1-3 pages)**: use pdf_tool directly with content parameter.
**Long PDF (4+ pages, reports, business plans, memos)**: two-step pipeline:
1. text_writer — generate full content as markdown file
2. pdf_tool — convert markdown file to PDF

## Two-step pipeline for long documents

Step 1: Generate content via text_writer
- filename: use descriptive name ending in .md (e.g. "bizplan_content.md")
- prompt: detailed instructions for the document (structure, sections, data to include)
- The more detailed the prompt, the better the result

Step 2: Convert to PDF via pdf_tool
- action: "create"
- source_file: same filename from step 1 (e.g. "bizplan_content.md")
- filename: final PDF name (e.g. "bizplan.pdf")
- title: document title for the cover

## Example: business plan

Step 1:
```json
{"tool": "text_writer", "input": {"prompt": "Напиши бизнес-план компании X. Структура: 1. Резюме проекта 2. Описание продукта 3. Анализ рынка 4. Финансовый план (таблицы с выручкой, расходами, прибылью) 5. Команда 6. Риски. Используй markdown: заголовки ##, таблицы |col|col|, буллеты. 15-20 страниц.", "filename": "bizplan_content.md"}}
```

Step 2:
```json
{"tool": "pdf_tool", "input": {"action": "create", "source_file": "bizplan_content.md", "filename": "bizplan.pdf", "title": "Бизнес-план компании X"}}
```

## Markdown formatting tips for text_writer prompt

pdf_tool supports full markdown rendering:
- # H1, ## H2, ### H3 — headings with professional styling
- | col1 | col2 | — tables with dark headers and alternating rows
- - bullet or * bullet — bullet lists
- --- — horizontal rules
- **bold** and *italic* — cleaned to plain text in PDF
- 1. 2. 3. — numbered lists

For best results, instruct text_writer to use tables for financial data and structured comparisons.

## When NOT to use the two-step pipeline

- Quick 1-2 page PDFs — use pdf_tool with content directly
- PDF reading — use pdf_tool with action="read"
- Charts/graphs inside PDF — use code_executor with fpdf2 (charts need matplotlib)
