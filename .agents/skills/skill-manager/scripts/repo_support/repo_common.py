#!/usr/bin/env python3
"""Common repository helpers owned by skill-manager."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from repo_support import repo_policy

sys.dont_write_bytecode = True

TOOLS = ("Codex", "Claude", "Copilot")
MIN_PYTHON = (3, 12)
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
IGNORED_SCAN_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}
WORKFLOW_COMMANDS = {
    "eval-workflow",
    "index-workflow-runs",
    "validate-automations",
    "sync-automation-routing",
    "create-workflow",
    "create-workflow-from-request",
    "propose-workflow",
    "workflow-recipes",
    "adjust-workflow",
    "review-workflow",
    "scorecard-workflows",
    "analytics-workflows",
    "smoke-workflows",
    "context-run",
    "context-audit-run",
    "checkpoint-run",
    "hooks-run",
    "hook-audit-run",
    "context-evidence-run",
}
MANAGER_SKILL_NAMES = ("skill-manager", "workflow-manager")
AGENTS_WARN_CHARS = int(repo_policy.default_value("limits.agents.warn_chars"))
AGENTS_FAIL_CHARS = int(repo_policy.default_value("limits.agents.fail_chars"))
DEFAULT_CHANGED_IGNORE_PREFIXES = ("_candidate-imports/",)
HARNESS_SMOKE_TARGET_MARKER_REL = ".agents/harness-smoke-target.json"
HARNESS_INSTALL_MANIFEST_REL = ".agents/harness.lock.json"
CONSUMER_GENERATED_PREFIXES = ("automations/navigation/", "docs/project/")


def require_supported_python() -> None:
    if sys.version_info >= MIN_PYTHON:
        return
    current = ".".join(str(part) for part in sys.version_info[:3])
    required = ".".join(str(part) for part in MIN_PYTHON)
    raise SystemExit(
        f"Python {required}+ is required; current interpreter is Python {current}. "
        "Run this command with a Python 3.12+ launcher, such as python3 or py -3."
    )


def repo_root(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    return Path(__file__).resolve().parents[5]


def relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def norm_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
        str(right.resolve(strict=False))
    )


def child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def python_command(script: Path, arguments: list[str] | tuple[str, ...] = ()) -> list[str]:
    return [sys.executable, "-B", str(script), *arguments]


def installed_harness_manifest_paths(root: Path) -> set[str]:
    path = root / HARNESS_INSTALL_MANIFEST_REL
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, dict):
        return set()
    rows = payload.get("files")
    if not isinstance(rows, list):
        return set()
    paths: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = str(row.get("path", "")).replace("\\", "/").strip()
        if value and not value.startswith("/") and ".." not in value.split("/"):
            paths.add(value)
    return paths


def installed_consumer_validation_paths(root: Path, paths: list[str]) -> list[str]:
    installed = installed_harness_manifest_paths(root)
    if not installed:
        return paths
    return [
        path
        for path in paths
        if path in installed
        or any(path.startswith(prefix) for prefix in CONSUMER_GENERATED_PREFIXES)
    ]


def is_installed_consumer_generated_path(root: Path, path: str) -> bool:
    if not installed_harness_manifest_paths(root):
        return False
    normalized = path.replace("\\", "/")
    return any(normalized.startswith(prefix) for prefix in CONSUMER_GENERATED_PREFIXES)


def is_installed_consumer_owned_path(root: Path, path: str) -> bool:
    installed = installed_harness_manifest_paths(root)
    if not installed:
        return False
    normalized = path.replace("\\", "/")
    if normalized in installed:
        return False
    if any(normalized.startswith(prefix) for prefix in CONSUMER_GENERATED_PREFIXES):
        return False
    return True


def run_python_script_quiet(script: Path, arguments: list[str]) -> tuple[int, str]:
    command = python_command(script, arguments)
    completed = subprocess.run(
        command,
        check=False,
        env=child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return completed.returncode, completed.stdout.strip()


def skill_script(root: Path, skill_name: str, script_name: str) -> Path:
    script = (
        root
        / ".agents"
        / "skills"
        / skill_name
        / "scripts"
        / script_name
    )
    if not script.exists():
        raise SystemExit(f"{skill_name} script not found: {script}")
    return script


def skill_manager_script(root: Path, script_name: str) -> Path:
    return skill_script(root, "skill-manager", script_name)


def workflow_manager_script(root: Path, script_name: str) -> Path:
    return skill_script(root, "workflow-manager", script_name)


def run_skill_script(root: Path, skill_name: str, script_name: str, arguments: list[str]) -> int:
    script = skill_script(root, skill_name, script_name)
    command = python_command(script, arguments)
    return subprocess.run(command, check=False, env=child_env()).returncode


def run_skill_script_quiet(
    root: Path, skill_name: str, script_name: str, arguments: list[str]
) -> tuple[int, str]:
    script = skill_script(root, skill_name, script_name)
    return run_python_script_quiet(script, arguments)


def run_skill_manager_script(root: Path, script_name: str, arguments: list[str]) -> int:
    return run_skill_script(root, "skill-manager", script_name, arguments)


def run_skill_manager_script_quiet(
    root: Path, script_name: str, arguments: list[str]
) -> tuple[int, str]:
    return run_skill_script_quiet(root, "skill-manager", script_name, arguments)


def run_workflow_repo_manager(root: Path, arguments: list[str]) -> int:
    script = workflow_manager_script(root, "workflow_repo_manager.py")
    command = python_command(script, arguments)
    return subprocess.run(command, check=False, env=child_env()).returncode


def validate_skill_with_manager(root: Path, skill_dir: Path) -> int:
    script = skill_manager_script(root, "validate_skill.py")
    command = python_command(script, [str(skill_dir)])
    return subprocess.run(command, check=False, env=child_env()).returncode


def validate_skill_with_manager_quiet(root: Path, skill_dir: Path) -> tuple[int, str]:
    script = skill_manager_script(root, "validate_skill.py")
    return run_python_script_quiet(script, [str(skill_dir)])


def skill_directories(source_root: Path) -> list[Path]:
    if not source_root.exists():
        return []
    return [
        child
        for child in sorted(source_root.iterdir(), key=lambda item: item.name.lower())
        if child.is_dir() and (child / "SKILL.md").exists()
    ]


def get_skill_directories(root: Path) -> list[Path]:
    skill_roots = [root / ".agents" / "skills", root / ".github" / "skills"]
    seen: set[str] = set()
    results: list[Path] = []

    for skill_root in skill_roots:
        if not skill_root.exists():
            continue
        for child in sorted(skill_root.iterdir(), key=lambda item: item.name.lower()):
            if child.is_dir() and (child / "SKILL.md").exists():
                key = norm_key(child)
                if key not in seen:
                    seen.add(key)
                    results.append(child)

    ignored_root_dirs = {
        ".git",
        ".github",
        ".agents",
        "automations",
        ".claude",
        "docs",
        "scripts",
        "templates",
    }
    for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if (
            child.is_dir()
            and child.name not in ignored_root_dirs
            and not child.name.startswith(".")
            and (child / "SKILL.md").exists()
        ):
            key = norm_key(child)
            if key not in seen:
                seen.add(key)
                results.append(child)

    return results


def git_output(root: Path, *args: str) -> tuple[int, list[str]]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return 1, []
    if completed.returncode != 0:
        return completed.returncode, []
    return (
        0,
        [
            line.strip().replace("\\", "/")
            for line in completed.stdout.splitlines()
            if line.strip()
        ],
    )
