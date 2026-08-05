#!/usr/bin/env python3
"""Common helpers for skill-manager location analysis."""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

sys.dont_write_bytecode = True

import skill_manager_common as skill_common

MIN_PYTHON = (3, 12)
TEXT_SUFFIXES = {
    ".adoc",
    ".cfg",
    ".csproj",
    ".css",
    ".csv",
    ".env",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
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
SCRIPT_SUFFIXES = DISALLOWED_SCRIPT_SUFFIXES | {".py"}
MANIFEST_NAMES = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "uv.lock",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "global.json",
    "Directory.Packages.props",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Cargo.toml",
    "go.mod",
}
def require_supported_python() -> None:
    if sys.version_info >= MIN_PYTHON:
        return
    current = ".".join(str(part) for part in sys.version_info[:3])
    required = ".".join(str(part) for part in MIN_PYTHON)
    raise SystemExit(
        f"Python {required}+ is required; current interpreter is Python {current}."
    )


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def read_text(path: Path, limit: int = 200_000) -> str:
    data = path.read_bytes()[:limit]
    return data.decode("utf-8-sig", errors="replace")


def iter_files(root: Path, max_files: int) -> list[Path]:
    if root.is_file():
        return [root]

    files: list[Path] = []
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in IGNORED_DIRS and not name.endswith(".egg-info")
        ]
        current = Path(current_root)
        for filename in sorted(filenames, key=str.lower):
            files.append(current / filename)
            if len(files) >= max_files:
                return files
    return files


def first_existing(root: Path, names: list[str]) -> Path | None:
    if root.is_file():
        return root if root.name in names else None
    for name in names:
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def extract_frontmatter_description(skill_path: Path) -> tuple[str | None, str | None]:
    return skill_common.extract_frontmatter_description(skill_path)
def classify_location(value: str, path: Path | None) -> str:
    if is_url(value):
        parsed = urlparse(value)
        if parsed.netloc.lower() == "github.com":
            return "remote GitHub URL"
        return "remote URL"
    if path is None:
        return "missing local path"
    if path.is_file():
        suffix = path.suffix.lower()
        if path.name == "SKILL.md":
            return "skill file"
        if suffix in {".pdf"}:
            return "PDF file"
        if suffix in {".docx", ".pptx", ".xlsx", ".csv"}:
            return "Office/data file"
        if suffix in {".yaml", ".yml", ".json"}:
            return "structured config/spec file"
        return "local file"
    if (path / "SKILL.md").exists():
        return "skill folder"
    if (path / ".git").exists():
        return "local Git repository"
    return "local folder"


def counter_items(counter: Counter[str], limit: int) -> list[dict[str, int | str]]:
    return [
        {"name": name, "count": count}
        for name, count in counter.most_common(limit)
    ]
