---
title: Excel Evidence Usage
type: reference
status: active
owner: document-artifacts
audience: agent
updated: 2026-07-17
---

# Excel Evidence Usage

Use deterministic XLSX reports before summarizing, reviewing, recalculating, rendering, or editing.

Strict read-only use omits `--output-json`, `--output-md`, `--output`, `--output-dir`, `--write`, `--force`, and install flags. The commands below are evidence-writing workflow examples unless those output/write flags are removed; `to-markdown --output` writes a file even without `--write`. Without `--output`, the report contains a compact excerpt unless `--include-content --json` explicitly embeds the complete Markdown in `evidence[kind=markdown].content`; that JSON can be large or sensitive.

For strict dogfood, copy only the command shape and keep stdout JSON.

## Commands

```shell
python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py doctor --json
python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py to-markdown --file data/input.xlsx --include-content --json
python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py inspect --file data/input.xlsx --json --output-json evidence/xlsx-inspect.json --output-md evidence/xlsx-inspect.md
python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py to-markdown --file data/input.xlsx --output evidence/input.md --include-metadata --include-links --include-outline --max-rows 100 --json
python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py formulas --file data/input.xlsx --json --output-json evidence/xlsx-formulas.json
python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py external-links --file data/input.xlsx --json --output-json evidence/xlsx-links.json
python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py recalc-check --file data/input.xlsx --json --output-json evidence/xlsx-recalc.json
python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py metadata --file data/input.xlsx --json --output-json evidence/xlsx-metadata.json
python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py links --file data/input.xlsx --json --output-json evidence/xlsx-links.json
python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py outline --file data/input.xlsx --json --output-json evidence/xlsx-outline.json
python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py accessibility --file data/input.xlsx --json --output-json evidence/xlsx-accessibility.json
python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py extract-assets --file data/input.xlsx --output-dir evidence/xlsx-assets --json
python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py compare --before old.xlsx --after new.xlsx --json --output-json evidence/xlsx-compare.json
python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py bundle-evidence --file data/input.xlsx --output-dir evidence/xlsx-bundle --write --json
python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py batch --paths data/input.xlsx data/other.xlsx --output-dir evidence/xlsx-batch --write --json
```

`extract-assets` is dry-run inventory unless `--write` is passed, but strict dogfood skips it because asset extraction is adjacent to output generation. Use `--strict` only when skipped recalculation/rendering/fallback evidence must fail the workflow.

## Boundaries

- `doctor --install-python-deps --json` is the only package bootstrap and writes to ignored `.agents/.deps/document-artifacts`; see `dependency-policy.json`.
- Use `--force` only for intentional overwrites.
- Use `--verify-output` with `write-cells --write` when before/after proof matters.
- For workflow attachments, feed deterministic JSON/Markdown into summaries, local-AI triage, or review packets. Without local AI, use JSON fields directly.
