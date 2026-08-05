---
title: PowerPoint Evidence Usage
type: reference
status: active
owner: document-artifacts
audience: agent
updated: 2026-07-17
---

# PowerPoint Evidence Usage

Use deterministic PPTX reports before summarizing, reviewing, rendering, or editing.

Strict read-only use omits `--output-json`, `--output-md`, `--output`, `--output-dir`, `--write`, `--force`, and install flags. The commands below are evidence-writing workflow examples unless those output/write flags are removed; `to-markdown --output` writes a file even without `--write`. Without `--output`, the report contains a compact excerpt unless `--include-content --json` explicitly embeds the complete Markdown in `evidence[kind=markdown].content`; that JSON can be large or sensitive.

## Commands

```shell
python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py doctor --json
python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py to-markdown --file decks/input.pptx --include-content --json
python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py inspect --file decks/input.pptx --json --output-json evidence/pptx-inspect.json --output-md evidence/pptx-inspect.md
python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py inventory --file decks/input.pptx --json --output-json evidence/pptx-inventory.json
python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py extract-text --file decks/input.pptx --json --output-json evidence/pptx-text.json
python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py to-markdown --file decks/input.pptx --output evidence/input.md --include-metadata --include-links --include-outline --json
python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py metadata --file decks/input.pptx --json --output-json evidence/pptx-metadata.json
python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py links --file decks/input.pptx --json --output-json evidence/pptx-links.json
python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py outline --file decks/input.pptx --json --output-json evidence/pptx-outline.json
python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py accessibility --file decks/input.pptx --json --output-json evidence/pptx-accessibility.json
python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py extract-assets --file decks/input.pptx --output-dir evidence/pptx-assets --json
python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py compare --before old.pptx --after new.pptx --json --output-json evidence/pptx-compare.json
python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py bundle-evidence --file decks/input.pptx --output-dir evidence/pptx-bundle --write --json
python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py batch --paths decks/input.pptx decks/other.pptx --output-dir evidence/pptx-batch --write --json
```

`extract-assets` is dry-run inventory unless `--write` is passed, but strict dogfood skips it because asset extraction is adjacent to output generation. Use `--strict` only when skipped rendering/fallback evidence must fail the workflow.

## Boundaries

- `doctor --install-python-deps --json` is still install mode and unsafe for strict offline dogfood, even when no extra packages are currently required.
- Use `--force` only for intentional overwrites.
- Use `--verify-output` with `replace-text --write` or `rearrange --write` when before/after proof matters.
- For workflow attachments, feed deterministic JSON/Markdown into summaries, local-AI triage, or review packets. Without local AI, the deterministic report remains fallback evidence.
