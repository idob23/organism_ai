# PDF — как создавать через reportlab

reportlab уже установлен в sandbox.

## Базовый шаблон
```python
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

doc = SimpleDocTemplate("/output/report.pdf",
    pagesize=A4,
    rightMargin=2*cm, leftMargin=2.5*cm,
    topMargin=2*cm, bottomMargin=2*cm)

styles = getSampleStyleSheet()
title_style = ParagraphStyle('Title',
    parent=styles['Title'],
    fontSize=16, textColor=colors.HexColor('#1E3A5F'),
    spaceAfter=20, alignment=TA_CENTER)
body_style = ParagraphStyle('Body',
    parent=styles['Normal'],
    fontSize=11, spaceAfter=8)

story = []
story.append(Paragraph("НАЗВАНИЕ ОТЧЁТА", title_style))
story.append(Spacer(1, 0.5*cm))
story.append(Paragraph("Текст отчёта...", body_style))

# Таблица
data = [["Параметр", "Значение"], ["Строка 1", "100"]]
table = Table(data, colWidths=[10*cm, 6*cm])
table.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E3A5F')),
    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F0F4F8')]),
    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CCCCCC')),
]))
story.append(table)

doc.build(story)
print("Saved files: report.pdf")
```

## Правила
- Всегда A4, поля 2-2.5 см
- Заголовок: цвет #1E3A5F, по центру
- Таблицы: тёмная шапка, чередующийся фон строк
- Кириллица: используй стандартные шрифты Helvetica (ASCII) или
  зарегистрируй TTF шрифт если нужна кириллица
- Файл сохранять в /output/, print "Saved files: filename.pdf"
