#!/usr/bin/env python3
"""Review-packet helpers for skill-manager location analysis."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from analysis_support import analysis_common as common

READ_FIRST_NAMES = {
    "AGENTS.md": "repository or folder operating instructions",
    "SKILL.md": "skill entry point",
    "WORKFLOW.md": "workflow entry point",
    "README.md": "primary overview",
    "CLAUDE.md": "assistant guidance",
    "package.json": "Node manifest",
    "pyproject.toml": "Python project manifest",
    "module.json": "canonical module contract",
}

ENTRYPOINT_NAMES = {
    "main.py",
    "cli.py",
    "manage.py",
    "__main__.py",
    "index.js",
    "index.ts",
    "main.rs",
    "Program.cs",
}


def build_review_packet(
    root: Path,
    files: list[Path],
    manifests: list[str],
    dependencies: list[str],
    max_files: int,
) -> dict[str, object]:
    base = root if root.is_dir() else root.parent
    return {
        "read_these_first": read_these_first(base, files),
        "likely_entry_points": likely_entry_points(base, files),
        "active_work": active_work(base),
        "dependency_summary": dependencies[:12],
        "manifest_summary": manifests[:12],
        "caveats": caveats(base, files, max_files),
    }


def read_these_first(base: Path, files: list[Path]) -> list[dict[str, str]]:
    candidates: list[tuple[int, str, str]] = []
    for path in files:
        rel = common.relative(base, path)
        name = path.name
        reason = READ_FIRST_NAMES.get(name)
        if reason is None:
            continue
        depth = len(Path(rel).parts)
        score = {
            "AGENTS.md": 0,
            "SKILL.md": 1,
            "WORKFLOW.md": 1,
            "README.md": 4,
            "module.json": 5,
        }.get(name, 8)
        candidates.append((score + depth, rel, reason))
    return [
        {"path": rel, "reason": reason}
        for _score, rel, reason in sorted(candidates, key=lambda item: (item[0], item[1]))[:8]
    ]


def likely_entry_points(base: Path, files: list[Path]) -> list[dict[str, str]]:
    candidates: list[tuple[int, str, str]] = []
    for path in files:
        rel = common.relative(base, path)
        reason = ""
        if path.name in ENTRYPOINT_NAMES:
            reason = "common executable entry point"
        elif "/scripts/" in f"/{rel.lower()}" and path.suffix.lower() == ".py":
            reason = "Python helper script"
        elif rel.startswith("scripts/") and path.suffix.lower() == ".py":
            reason = "workflow-local Python helper"
        if reason:
            candidates.append((len(Path(rel).parts), rel, reason))
    return [
        {"path": rel, "reason": reason}
        for _depth, rel, reason in sorted(candidates, key=lambda item: (item[0], item[1]))[:8]
    ]


def active_work(base: Path) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(base), "status", "--short"],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ["Git status unavailable."]
    if completed.returncode != 0:
        return ["Not a Git worktree or Git status unavailable."]
    raw_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    lines = [line for line in raw_lines if not status_path(line).startswith("../")]
    if not raw_lines:
        return ["No active Git changes detected."]
    if not lines:
        return ["Git changes exist outside the analyzed folder."]
    omitted = len(raw_lines) - len(lines)
    if omitted:
        lines = [*lines[:7], f"... {omitted} change(s) outside analyzed folder omitted."]
    return lines[:8]


def caveats(base: Path, files: list[Path], max_files: int) -> list[str]:
    rels = {common.relative(base, path) for path in files}
    names = {path.name for path in files}
    notes: list[str] = []
    if len(files) >= max_files:
        notes.append(f"File scan reached the configured max-files limit ({max_files}).")
    if "AGENTS.md" not in names:
        notes.append("No AGENTS.md found in scanned files.")
    if "README.md" not in names:
        notes.append("No README.md found in scanned files.")
    if not any(name.lower().startswith("license") for name in names):
        notes.append("No obvious license file found in scanned files.")
    if "SKILL.md" in names and "module.json" not in names:
        notes.append("Skill entry point found without accepted module.json manifest.")
    if "WORKFLOW.md" in names and "module.json" not in names:
        notes.append("Workflow entry point found without module.json.")
    return notes[:6]


def status_path(line: str) -> str:
    parts = line.split(maxsplit=1)
    text = parts[1] if len(parts) == 2 else line
    if " -> " in text:
        text = text.rsplit(" -> ", 1)[1]
    return text.strip().replace("\\", "/")
