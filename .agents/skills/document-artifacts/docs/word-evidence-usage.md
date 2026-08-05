---
title: Word Evidence Usage
type: reference
status: active
owner: document-artifacts
audience: agent
updated: 2026-07-17
---

# Word Evidence Usage

Use deterministic DOCX reports before summarizing, reviewing, rendering, or editing.

Strict read-only use omits `--output-json`, `--output-md`, `--output`, `--output-dir`, `--write`, `--force`, and install flags. The commands below are evidence-writing workflow examples unless those output/write flags are removed; `to-markdown --output` writes a file even without `--write`. Without `--output`, the report contains a compact excerpt unless `--include-content --json` explicitly embeds the complete Markdown in `evidence[kind=markdown].content`; that JSON can be large or sensitive.

## Commands

```shell
python -B .agents/skills/document-artifacts/scripts/word/word_tools.py doctor --json
python -B .agents/skills/document-artifacts/scripts/word/word_tools.py to-markdown --file docs/input.docx --include-content --json
python -B .agents/skills/document-artifacts/scripts/word/word_tools.py inspect --file docs/input.docx --json --output-json evidence/docx-inspect.json --output-md evidence/docx-inspect.md
python -B .agents/skills/document-artifacts/scripts/word/word_tools.py extract-markdown --file docs/input.docx --json --output-json evidence/docx-text.json
python -B .agents/skills/document-artifacts/scripts/word/word_tools.py to-markdown --file docs/input.docx --output evidence/input.md --include-metadata --include-links --include-outline --json
python -B .agents/skills/document-artifacts/scripts/word/word_tools.py comments --file docs/input.docx --json --output-json evidence/docx-comments.json
python -B .agents/skills/document-artifacts/scripts/word/word_tools.py tracked-changes --file docs/input.docx --json --output-json evidence/docx-revisions.json
python -B .agents/skills/document-artifacts/scripts/word/word_tools.py metadata --file docs/input.docx --json --output-json evidence/docx-metadata.json
python -B .agents/skills/document-artifacts/scripts/word/word_tools.py links --file docs/input.docx --json --output-json evidence/docx-links.json
python -B .agents/skills/document-artifacts/scripts/word/word_tools.py outline --file docs/input.docx --json --output-json evidence/docx-outline.json
python -B .agents/skills/document-artifacts/scripts/word/word_tools.py accessibility --file docs/input.docx --json --output-json evidence/docx-accessibility.json
python -B .agents/skills/document-artifacts/scripts/word/word_tools.py extract-assets --file docs/input.docx --output-dir evidence/docx-assets --json
python -B .agents/skills/document-artifacts/scripts/word/word_tools.py compare --before old.docx --after new.docx --json --output-json evidence/docx-compare.json
python -B .agents/skills/document-artifacts/scripts/word/word_tools.py bundle-evidence --file docs/input.docx --output-dir evidence/docx-bundle --write --json
python -B .agents/skills/document-artifacts/scripts/word/word_tools.py batch --paths docs/input.docx docs/other.docx --output-dir evidence/docx-batch --write --json
```

`extract-assets` is dry-run inventory unless `--write` is passed, but strict dogfood skips it because asset extraction is adjacent to output generation. Use `--strict` only when skipped rendering/fallback evidence must fail the workflow.

## Boundaries

- `doctor --install-python-deps --json` is the only package bootstrap and writes to ignored `.agents/.deps/document-artifacts`; see `dependency-policy.json`.
- Use `--force` only for intentional overwrites.
- Use `--verify-output` with `replace-text --write` when before/after proof matters.
- For workflow attachments, feed deterministic JSON/Markdown into summaries, local-AI triage, or review packets. Without local AI, the deterministic report remains fallback evidence.
