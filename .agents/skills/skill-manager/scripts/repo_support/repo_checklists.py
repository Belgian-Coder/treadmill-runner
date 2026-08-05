"""Checklist renderers for repository manager commands."""

from __future__ import annotations

import argparse
from pathlib import Path


def new_skill_checklist(args: argparse.Namespace, root: Path) -> int:
    name = args.name
    skill_path = f".agents/skills/{name}"
    lines = [
        "# New Skill Checklist",
        "",
        f"- Skill name: `{name}`",
        f"- Target folder: `{skill_path}`",
        "",
        "## Required Files",
        "",
        f"- [ ] `{skill_path}/SKILL.md` with frontmatter `name` and `description` only.",
        f"- [ ] `{skill_path}/module.json` with SemVer, compatibility, dependencies, risk, and provenance.",
        "- [ ] Optional `docs/`, `scripts/`, and `assets/` only when they remove real complexity.",
        "",
        "## Behavior Q&A",
        "",
        "- [ ] Trigger scope and non-goals are clear. Example answer: use for Azure DevOps Mermaid diagrams, not general GitHub diagrams.",
        "- [ ] Allowed reads and writes are clear. Example answer: read Markdown files and write only normalized diagram blocks.",
        "- [ ] Guardrails and stop rules are clear. Example answer: continue when optional render validation is unavailable; stop before unapproved installs.",
        "- [ ] Validation evidence is clear. Example answer: static validation is required; render validation is best-effort.",
        "- [ ] Parallel-safe checks are identified or explicitly absent. Example answer: static checks on separate files may run in parallel when supported.",
        "- [ ] Repeatable behavior that belongs in Python scripts is identified. Example answer: extract blocks, validate wrappers, and render reports.",
        "",
        "## Transform Shape",
        "",
        "- [ ] Imported or new material is rewritten into goal, workflow, guardrails, validation, completion contract, and stop rules.",
        "- [ ] Domain detail is preserved in `docs/` or `assets/`; trigger-loaded `SKILL.md` stays procedural.",
        "- [ ] Scripts have clear `--help`, explicit write flags when mutating, and Markdown or JSON output when reports are useful.",
        "",
        "## Design Checks",
        "",
        "- [ ] One clear job and a specific routing description.",
        "- [ ] No broad persona, always-on trigger, hidden network call, upload, install, destructive action, or generated setting.",
        "- [ ] Python helpers are Python 3.12+ stdlib and owned by this skill.",
        "- [ ] Markdown is used unless JSON materially improves validation or tooling.",
        "- [ ] Small-model fit: `SKILL.md` is concise, optional docs are routed, and repeatable checks are scripted.",
        "",
        "## Commands",
        "",
        "```shell",
        f"python -B .agents/skills/skill-manager/scripts/validate_skill.py {skill_path}",
        "```",
        "",
        "```shell",
        f"python -B .agents/manage.py inspect-skill --skill {skill_path}",
        "```",
        "",
        "```shell",
        "python -B .agents/manage.py sync",
        "```",
        "",
        "```shell",
        "python -B .agents/manage.py validate",
        "```",
    ]
    print("\n".join(lines))
    return 0
