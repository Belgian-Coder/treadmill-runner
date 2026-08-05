---
title: PDF Evidence Usage
type: reference
status: active
owner: document-artifacts
audience: agent
updated: 2026-07-17
---

# PDF Evidence Usage

Use deterministic reports before local AI, OCR, vision, or write operations.

Strict read-only use omits `--output-json`, `--output-md`, `--output`, `--output-dir`, `--write`, `--force`, and install flags. The commands below are evidence-writing workflow examples unless those output/write flags are removed; `to-markdown --output` writes a file even without `--write`. Without `--output`, the report contains a compact excerpt unless `--include-content --json` explicitly embeds the complete Markdown in `evidence[kind=markdown].content`; that JSON can be large or sensitive.

## Commands

```shell
python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py doctor --json
python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py to-markdown --file docs/input.pdf --include-content --json
python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py inspect --file docs/input.pdf --json --output-json evidence/pdf-inspect.json --output-md evidence/pdf-inspect.md
python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py extract-text --file docs/input.pdf --json --output-json evidence/pdf-text.json
python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py to-markdown --file docs/input.pdf --output evidence/input.md --include-metadata --include-links --include-outline --json
python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py metadata --file docs/input.pdf --json --output-json evidence/pdf-metadata.json
python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py links --file docs/input.pdf --json --output-json evidence/pdf-links.json
python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py outline --file docs/input.pdf --json --output-json evidence/pdf-outline.json
python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py accessibility --file docs/input.pdf --json --output-json evidence/pdf-accessibility.json
python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py extract-assets --file docs/input.pdf --output-dir evidence/pdf-assets --json
python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py compare --before old.pdf --after new.pdf --json --output-json evidence/pdf-compare.json
python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py bundle-evidence --file docs/input.pdf --output-dir evidence/pdf-bundle --write --json
python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py batch --paths docs/input.pdf docs/other.pdf --output-dir evidence/pdf-batch --write --json
```

`extract-assets` is dry-run inventory unless `--write` is passed, but strict dogfood skips it because asset extraction is adjacent to output generation. Use `--strict` only when skipped renderer/fallback evidence must fail the workflow.

## Boundaries

- `doctor --install-python-deps --json` is the only package bootstrap and writes to ignored `.agents/.deps/document-artifacts`; see `dependency-policy.json`.
- Use `--force` only for intentional overwrites.
- Use `--verify-output` on write-capable form commands when before/after proof matters.
- For workflow attachments, feed deterministic JSON/Markdown into summaries, local-AI triage, or review packets. Without local AI, use report `summary`, `findings`, `warnings`, `skipped`, `blocked`, and `evidence`.
