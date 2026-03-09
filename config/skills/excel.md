# Excel — как создавать таблицы правильно

Используй openpyxl. Всегда применяй форматирование.

## Структура кода
```python
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Данные"

# Заголовок файла (строка 1 — объединённые ячейки)
ws.merge_cells('A1:E1')
ws['A1'] = "НАЗВАНИЕ ОТЧЁТА"
ws['A1'].font = Font(bold=True, size=14, color="FFFFFF")
ws['A1'].fill = PatternFill("solid", fgColor="1E3A5F")
ws['A1'].alignment = Alignment(horizontal="center", vertical="center")
ws.row_dimensions[1].height = 30

# Заголовки столбцов (строка 2)
headers = ["Столбец 1", "Столбец 2", "Столбец 3"]
for col, header in enumerate(headers, 1):
    cell = ws.cell(row=2, column=col, value=header)
    cell.font = Font(bold=True, color="FFFFFF")
    cell.fill = PatternFill("solid", fgColor="2E4057")
    cell.alignment = Alignment(horizontal="center")

# Данные (строки 3+) — чередующийся фон
for row_idx, row_data in enumerate(data, 3):
    fill_color = "F0F4F8" if row_idx % 2 == 0 else "FFFFFF"
    for col_idx, value in enumerate(row_data, 1):
        cell = ws.cell(row=row_idx, column=col_idx, value=value)
        cell.fill = PatternFill("solid", fgColor=fill_color)

# Ширина столбцов — подогнать по содержимому
for col in ws.columns:
    max_len = max(len(str(cell.value or "")) for cell in col)
    ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 4, 40)

# Итоговая строка (если нужна)
total_row = len(data) + 3
ws.cell(row=total_row, column=1, value="ИТОГО").font = Font(bold=True)
ws.cell(row=total_row, column=1).fill = PatternFill("solid", fgColor="E8F0FE")

wb.save("/output/report.xlsx")
print("Saved files: report.xlsx")
```

## Правила
- Всегда тёмный заголовок (цвет 1E3A5F), белый текст
- Заголовки столбцов — контрастный фон
- Числа — выравнивание по правому краю
- Суммы — жирный шрифт, выделенный фон
- Файл всегда сохранять в /output/, print "Saved files: filename.xlsx"
- Для финансовых данных: формат ячейки '#,##0.00 ₽'
