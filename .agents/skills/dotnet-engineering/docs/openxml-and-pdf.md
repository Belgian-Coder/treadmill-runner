# OpenXML And PDF

Use for modern .NET code that creates, reads, modifies, or validates Office and PDF files.

## Choose The Library

- Open XML SDK is the fidelity-first default for `.xlsx`, `.docx`, and `.pptx` when preserving OOXML parts, formulas, charts, pivots, styles, themes, media, comments, or package structure matters.
- `DocumentFormat.OpenXml` is verbose but explicit; prefer it for templates, large files, streaming, package edits, and unsupported wrapper-library features.
- ClosedXML fits straightforward Excel read/write/format tasks, but do not assume chart, pivot, macro, external-link, or theme fidelity.
- EPPlus has a non-MIT commercial model; confirm project license policy before adding it.
- PDFsharp fits create/merge/split/stamp/basic forms; MigraDoc fits flow documents with sections, tables, headers, and pagination.
- PdfPig is better for existing-PDF text extraction than PDFsharp.

## Office Rules

- Dispose packages so files flush and close deterministically.
- For Excel reads, handle SharedString tables and inline strings; missing lookup gives wrong text.
- Set `CellReference` when precise placement matters; append-only cells are fragile with sparse rows or reused styles.
- Treat stylesheet indices as positional contracts.
- Start PowerPoint from templates when practical; masters, layouts, placeholders, notes, and themes are easy to corrupt from scratch.
- For Word, preserve spacing, section properties, numbering, styles, relationships, comments, and tracked changes unless owned by the task.
- Use file-based apps only when target SDK and repo policy support that C# script model; otherwise create normal projects/tests.

## PDF And Evidence

- PDF coordinates are layout code; use MigraDoc or templates when flow, pagination, tables, or headers matter.
- Embed/configure Unicode fonts for CJK, RTL, emoji, and mixed scripts.
- Do not use text extraction as layout proof; render pages when placement, pagination, or watermarks matter.
- Keep passwords, form values, and generated private PDFs out of committed fixtures unless sanitized.
- Use `document-artifacts` for workbook formulas/tables/recalc/rendering, DOCX comments/tracked changes/links/accessibility/rendering, slide notes/media/charts/rendering, and PDF metadata/text/forms/links/page rendering/comparison.

## Review Checklist

- Library choice matches fidelity, file size, license, and team support.
- Inputs/outputs are explicit and source artifacts are not overwritten.
- Templates and generated files have open/inspect/render evidence.
- Formulas, styles, charts, pivots, masters, tracked changes, comments, metadata, relationships, fonts, and page layout are preserved or intentionally changed.
