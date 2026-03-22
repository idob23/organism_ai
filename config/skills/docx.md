# Word documents — professional quality via Node.js

Use the Node.js `docx` library. Create a .js file, run via node.

## Important: sandbox paths
- Read existing files: /data/outputs/filename.docx
- Save to: /output/filename.docx (always here)
- After saving: print("Saved files: filename.docx")

## Professional document template
```javascript
const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
        AlignmentType, HeadingLevel, BorderStyle, WidthType, ShadingType,
        Header, Footer, PageNumber, NumberFormat,
        LevelFormat, convertInchesToTwip } = require('docx');
const fs = require('fs');

// Color scheme
const COLORS = {
  primary: "1E3A5F",    // dark blue — headings, table headers
  accent: "2E5C8A",     // medium blue — accents
  text: "333333",        // dark gray — body text
  muted: "666666",       // gray — captions, headers/footers
  tableAlt: "F0F4F8",   // light blue — alternating rows
  white: "FFFFFF",
};

const doc = new Document({
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 }, // A4
        margin: { top: 1440, right: 1200, bottom: 1440, left: 1440 }
      },
      pageNumberStart: 1,
    },
    // Headers and footers
    headers: {
      default: new Header({
        children: [new Paragraph({
          alignment: AlignmentType.RIGHT,
          children: [new TextRun({ text: "DOCUMENT TITLE", size: 16, color: COLORS.muted, font: "Arial" })]
        })]
      })
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [
            new TextRun({ children: [PageNumber.CURRENT], size: 16, color: COLORS.muted, font: "Arial" }),
            new TextRun({ text: " / ", size: 16, color: COLORS.muted, font: "Arial" }),
            new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 16, color: COLORS.muted, font: "Arial" }),
          ]
        })]
      })
    },
    children: [
      // === DOCUMENT TITLE ===
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 200, after: 400 },
        children: [new TextRun({ text: "TITLE", bold: true, size: 36, font: "Arial", color: COLORS.primary })]
      }),

      // === SECTION HEADING (H2) ===
      new Paragraph({
        heading: HeadingLevel.HEADING_2,
        spacing: { before: 300, after: 120 },
        children: [new TextRun({ text: "Section name", bold: true, size: 26, font: "Arial", color: COLORS.primary })]
      }),

      // === REGULAR PARAGRAPH ===
      new Paragraph({
        spacing: { before: 60, after: 60 },
        children: [new TextRun({ text: "Paragraph text.", size: 22, font: "Arial", color: COLORS.text })]
      }),

      // === BULLET LIST ===
      new Paragraph({
        bullet: { level: 0 },
        spacing: { before: 40, after: 40 },
        children: [new TextRun({ text: "List item", size: 22, font: "Arial", color: COLORS.text })]
      }),

      // === STYLED TABLE ===
      new Table({
        width: { size: 100, type: WidthType.PERCENTAGE },
        rows: [
          // Header row
          new TableRow({
            children: ["Column 1", "Column 2", "Column 3"].map(h =>
              new TableCell({
                shading: { fill: COLORS.primary, type: ShadingType.CLEAR },
                children: [new Paragraph({
                  alignment: AlignmentType.CENTER,
                  children: [new TextRun({ text: h, bold: true, size: 20, font: "Arial", color: COLORS.white })]
                })]
              })
            )
          }),
          // Data row (alternate via index % 2)
          new TableRow({
            children: ["Data 1", "Data 2", "Data 3"].map(d =>
              new TableCell({
                shading: { fill: COLORS.tableAlt, type: ShadingType.CLEAR },
                children: [new Paragraph({
                  children: [new TextRun({ text: d, size: 20, font: "Arial", color: COLORS.text })]
                })]
              })
            )
          }),
        ]
      }),

      // === HORIZONTAL LINE ===
      new Paragraph({
        spacing: { before: 200, after: 200 },
        border: { bottom: { style: BorderStyle.SINGLE, size: 1, color: COLORS.muted } },
        children: []
      }),
    ]
  }]
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync('/output/document.docx', buf);
  console.log('Saved files: document.docx');
});
```

## How to run from code_executor
```python
import subprocess, tempfile

js_code = """
const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
        AlignmentType, HeadingLevel, BorderStyle, WidthType, ShadingType,
        Header, Footer, PageNumber } = require('docx');
const fs = require('fs');
// ... build document ...
Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync('/output/filename.docx', buf);
  console.log('Saved files: filename.docx');
});
"""

with tempfile.NamedTemporaryFile(mode='w', suffix='.js',
                                  dir='/sandbox', delete=False) as f:
    f.write(js_code)
    js_path = f.name

result = subprocess.run(['node', js_path], capture_output=True, text=True, timeout=30)
print(result.stdout)
if result.returncode != 0:
    print("Error:", result.stderr)
```

## Rules
- Font: Arial, body text size 22 (11pt in Word)
- Margins: top/bottom 1440 DXA (~2.5 cm), right 1200, left 1440
- Headings: bold, color 1E3A5F, size 26-36
- Tables: width 100% (WidthType.PERCENTAGE), dark header, alternating rows
- Headers/footers: document title top-right, page number "N / Total" bottom-center
- Bullets: via { bullet: { level: 0 } }, not with symbols
- Save to /output/, print "Saved files: filename.docx"
