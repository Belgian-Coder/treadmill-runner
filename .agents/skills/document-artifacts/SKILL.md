---
name: document-artifacts
description: Use when inspecting, extracting, validating, rendering, or safely editing Excel, Word, PowerPoint, or PDF files, or scanning converted Markdown, with deterministic evidence and guarded write behavior.
---

# Document Artifacts

## Goal

Produce deterministic XLSX, DOCX, PPTX, PDF, and converted-Markdown evidence before summaries, layout claims, local AI, rendering, or writes.

## Read-Only Dogfood

Strict read-only/offline review uses `--help`, `formats --json`, `doctor --json` without install flags, inspect/validate/extract commands without output paths, Markdown `scan`, and `to-markdown` only without `--output`. No-output Markdown reports stay compact by default; add `--include-content --json` only when the complete conversion is required in `evidence[kind=markdown].content`. Skip `--output-json`, `--output-md`, `--output`, `--write`, `--force`, `extract-assets`, bundle/batch writes, local AI/OCR, rendering, and `doctor --install-python-deps` unless explicitly allowed. Self-tests and evals can create temp fixtures; skip them in strict no-write dogfood.
`formats --json` is a static portable-dispatcher inventory, not proof that optional runtime dependencies are available. For formats whose advertised operations include `doctor`, use that format's read-only `doctor --json` result for runtime evidence. Markdown `scan` is stdlib-only and does not expose `doctor`.

## Workflow

1. Identify the artifact type and inspect before editing:

```shell
python -B .agents/skills/document-artifacts/scripts/document_artifacts.py formats --json
python -B .agents/skills/document-artifacts/scripts/document_artifacts.py excel doctor --json
python -B .agents/skills/document-artifacts/scripts/document_artifacts.py word inspect --file <file.docx> --json
python -B .agents/skills/document-artifacts/scripts/document_artifacts.py powerpoint inventory --file <file.pptx> --json
python -B .agents/skills/document-artifacts/scripts/document_artifacts.py pdf validate --file <file.pdf> --json
python -B .agents/skills/document-artifacts/scripts/document_artifacts.py markdown scan --file <file.md> --json
```

2. Add only the evidence needed: metadata, links, outline, accessibility, text or Markdown extraction, tables, formulas, external links, comments, tracked changes, slide inventory, forms, assets, comparisons, or render output.
3. Use guarded write modes explicitly: `--write`, `--dry-run`, explicit output paths, `--force` for intentional overwrites, and `--verify-output` when before/after proof matters. `to-markdown --output` and report `--output-*` paths write files even without `--write`.
4. Use format-specific scripts under `scripts/excel`, `scripts/word`, `scripts/powerpoint`, and `scripts/pdf` only when the dispatcher does not expose the needed command directly.

Workflow report commands support `--output-json <path>`, `--output-md <path>`, and `--strict`. Fallback without local AI: use report `summary`, `findings`, `evidence`, `skipped`, and `blocked`.

## Rules

- Do not upload documents or call external services; network risk covers only explicit package bootstrap such as `doctor --install-python-deps`.
- Do not install Office, LibreOffice, Poppler, OCR, converters, or Python packages automatically; `doctor --install-python-deps` is the only explicit Python package bootstrap and writes under `.agents/.deps/document-artifacts`.
- Report optional setup as skipped or failed and continue non-blocking unless the workflow made it required.
- Do not write changed artifacts unless a write flag and explicit output path are passed.
- Do not overwrite inputs or outputs unless the command supports it and `--force` is explicit.
- Do not extract media, charts, embedded objects, or PDF assets unless `extract-assets --write` and an explicit output directory are passed.
- Treat OOXML/PDF structure and Markdown extraction as content evidence, not visual layout proof.
- Treat `--include-content` as explicit full-text disclosure: it does not write a file, but its JSON can be large or sensitive.
- Markdown security scans use a bounded CommonMark-aware lexical pass for inline link/image, raw-HTML, autolink, and reference-definition JavaScript destinations after context-appropriate entity and escape normalization. The pass excludes fenced/inline code, supports LF, CRLF, and CR line endings, escaped label punctuation, multiline labels, common block containers, balanced inline labels up to 32 levels, and standard quoted/parenthesized titles. Its deterministic link-work budget prevents malformed nested destinations from causing unbounded rescans; exhaustion emits `MARKDOWN_LINK_SCAN_LIMIT`, sets `processing_truncated` and `detected_issue_count_is_lower_bound`, and fails the scan closed. Raw HTML contexts are scanned once; internal backticks cannot alter code-span state or expose attribute text to Markdown parsing. Reference context is evaluated incrementally without copying the prefix. Reports expose `link_scan_work`, `link_scan_budget`, `html_scan_work`, `code_scan_work`, and `reference_context_work`. It reports secret-like tokens plus dangerous control/format characters without echoing matched values. It is advisory evidence, not a complete CommonMark parser, sanitization, or prompt-injection detection; use `--strict` when findings must fail a gate.
- Hidden-format warnings intentionally include soft hyphen (U+00AD), Arabic letter mark (U+061C), Mongolian vowel separator (U+180E), and word joiner (U+2060); visible ordinary Unicode punctuation remains allowed.
- `--strict` also fails on warned ZWJ/ZWNJ characters, including legitimate emoji or multilingual shaping uses; review those findings rather than treating every warning as malicious.
- Treat missing render, recalc, OCR, or Office tools as skipped capability unless required.
- Use direct OOXML and ZIP handling safely; reject unsafe package paths.

See `docs/*-evidence-usage.md` for format-specific examples.

## Validation

Strict validation uses `module.json.strict_read_only_commands` only. Do not add artifact-processing commands that need user files unless fixture creation and output paths are explicitly allowed.

```shell
python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/document-artifacts
python -B .agents/skills/document-artifacts/scripts/run_self_tests.py
python -B .agents/manage.py eval-skill --skill .agents/skills/document-artifacts --suite .agents/skills/document-artifacts/suites/document-artifacts-evals.json
```

Run self-tests/evals only when temporary fixtures and suite command execution are allowed.

## Stop Rules

Stop before overwriting source artifacts, filling PDF forms whose fields cannot be inspected, accepting tracked changes or deleting comments, claiming formula recalculation or layout correctness without evidence, OCR/local AI use without approval, or processing unsafe OOXML package paths.

## Completion Contract

Report input files, artifact types, command mode, write flags, output paths, metadata/link/outline/accessibility findings, Markdown security score/issues, asset manifest, tables/formulas/comments/revisions/slides/forms/text/render status, skipped or blocked dependencies, failed commands, validation result, and remaining document risk.
