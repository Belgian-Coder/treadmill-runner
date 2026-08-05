"""Workflow module discovery helpers."""

from __future__ import annotations

from pathlib import Path

import workflow_manager_common as common


def discover_automation_dirs(root: Path) -> list[Path]:
    automations_root = root / "automations"
    if not automations_root.exists():
        return []
    return [
        child
        for child in sorted(automations_root.iterdir(), key=lambda item: item.name.lower())
        if child.is_dir() and child.name != "__pycache__"
    ]


def known_skill_names(root: Path) -> set[str]:
    return {path.name for path in common.discover_skill_dirs(root)}


def detect_scripts(module_dir: Path) -> list[str]:
    scripts_root = module_dir / "scripts"
    if not scripts_root.exists():
        return []
    return [
        common.relative(module_dir, path)
        for path in common.iter_files(scripts_root, max_files=500)
        if path.is_file()
    ]
