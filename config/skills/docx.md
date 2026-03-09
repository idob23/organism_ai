# Word документы — как создавать через Node.js

Используй Node.js библиотеку `docx` (npm install -g docx).
Создавай .js файл, запускай через node, сохраняй в /output/.

## Базовый шаблон
```javascript
const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
        AlignmentType, HeadingLevel, BorderStyle, WidthType, ShadingType } = require('docx');
const fs = require('fs');

const doc = new Document({
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 }, // A4
        margin: { top: 1200, right: 1200, bottom: 1200, left: 1440 }
      }
    },
    children: [
      // Заголовок документа
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 0, after: 300 },
        children: [new TextRun({ text: "НАЗВАНИЕ", bold: true, size: 32, font: "Arial" })]
      }),
      // Параграф
      new Paragraph({
        spacing: { before: 100, after: 100 },
        children: [new TextRun({ text: "Текст параграфа", size: 22, font: "Arial" })]
      }),
    ]
  }]
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync('/output/document.docx', buf);
  console.log('Saved files: document.docx');
});
```

## Как запустить из code_executor
```python
import subprocess, os, tempfile

js_code = """
const { Document, Packer, Paragraph, TextRun } = require('docx');
// ... твой код ...
"""

with tempfile.NamedTemporaryFile(mode='w', suffix='.js',
                                  dir='/sandbox', delete=False) as f:
    f.write(js_code)
    js_path = f.name

result = subprocess.run(['node', js_path], capture_output=True, text=True)
print(result.stdout)
if result.returncode != 0:
    print("Error:", result.stderr)
```

## Правила
- Шрифт всегда Arial, размер обычного текста 22 (11pt в Word)
- Поля: top/right/bottom 1200 DXA (≈2.1 см), left 1440 DXA (≈2.5 см)
- Заголовки: bold, size 28-36
- Таблицы: ширина 100% страницы (WidthType.PERCENTAGE)
- Файл сохранять в /output/, print "Saved files: filename.docx"
