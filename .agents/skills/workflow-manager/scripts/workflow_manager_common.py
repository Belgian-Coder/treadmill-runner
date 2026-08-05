#!/usr/bin/env python3
"""Shared helpers for workflow-manager scripts."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

for _ancestor in Path(__file__).resolve().parents:
    _policy_scripts = _ancestor / ".agents" / "skills" / "skill-manager" / "scripts"
    if _policy_scripts.is_dir():
        sys.path.insert(0, str(_policy_scripts))
        break

from repo_support import repo_policy

MIN_PYTHON = (3, 12)
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
DISALLOWED_SCRIPT_SUFFIXES = {
    ".bash",
    ".bat",
    ".cmd",
    ".fish",
    ".ps1",
    ".psd1",
    ".psm1",
    ".sh",
    ".zsh",
}
IGNORED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "bin",
    "dist",
    "node_modules",
    "obj",
    "venv",
}
RISK_KEYS = {
    "credentials",
    "destructive",
    "generated_settings",
    "installs",
    "network",
    "production_writes",
    "uploads",
}
RISK_PROFILES = {
    "read-only",
    "local-write",
    "local-destructive",
    "networked",
    "credentialed",
    "production-write",
}
RISK_PROFILE_RANK = {
    "read-only": 0,
    "local-write": 1,
    "local-destructive": 2,
    "networked": 3,
    "credentialed": 4,
    "production-write": 5,
}


def project_policy_int(path: str, *, start: Path | None = None) -> int:
    """Return one validated project policy integer for workflow-manager."""
    return repo_policy.int_value(repo_policy.project_root(start), path)


def project_warning_action(warning_id: str, *, start: Path | None = None) -> str:
    """Return the configured project action for one workflow warning."""
    return repo_policy.warning_action(repo_policy.project_root(start), warning_id)


def require_supported_python() -> None:
    if sys.version_info >= MIN_PYTHON:
        return
    current = ".".join(str(part) for part in sys.version_info[:3])
    required = ".".join(str(part) for part in MIN_PYTHON)
    raise SystemExit(
        f"Python {required}+ is required; current interpreter is Python {current}."
    )


def child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def read_text(path: Path, limit: int | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    if limit is not None:
        return text[:limit]
    return text


def workflow_start_name(workflow_name: str) -> str:
    return "WORKFLOW.md"


def workflow_start_path(module_dir: Path) -> Path:
    return module_dir / "WORKFLOW.md"


def workflow_start_relative(module_dir: Path) -> str:
    return workflow_start_path(module_dir).name


def read_json_file(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None, f"{path} not found"
    except json.JSONDecodeError as exc:
        return None, f"{path} has invalid JSON: {exc}"
    if not isinstance(data, dict):
        return None, f"{path} must contain a JSON object"
    return data, None


def semver_tuple(value: str) -> tuple[int, int, int] | None:
    match = SEMVER_PATTERN.match(value)
    if not match:
        return None
    return tuple(int(part) for part in match.groups()[:3])


def iter_files(root: Path, max_files: int = 5000) -> list[Path]:
    files: list[Path] = []
    if not root.exists():
        return files
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in IGNORED_DIRS]
        current = Path(current_root)
        for filename in filenames:
            files.append(current / filename)
            if len(files) >= max_files:
                return files
    return files


def discover_skill_dirs(root: Path, source_root: str = ".agents/skills") -> list[Path]:
    skill_root = root / source_root
    if not skill_root.exists():
        return []
    return [
        child
        for child in sorted(skill_root.iterdir(), key=lambda item: item.name.lower())
        if child.is_dir() and (child / "SKILL.md").exists()
    ]


def compact_snippet(text: str, limit: int = 160) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def required_risk_profile(risk: dict[str, object]) -> str:
    if risk.get("production_writes"):
        return "production-write"
    if risk.get("credentials"):
        return "credentialed"
    if risk.get("network") or risk.get("uploads"):
        return "networked"
    if risk.get("destructive"):
        return "local-destructive"
    if risk.get("generated_settings") or risk.get("installs"):
        return "local-write"
    return "read-only"


def risk_profile_covers(profile: str, required: str) -> bool:
    return RISK_PROFILE_RANK.get(profile, -1) >= RISK_PROFILE_RANK.get(required, 99)
