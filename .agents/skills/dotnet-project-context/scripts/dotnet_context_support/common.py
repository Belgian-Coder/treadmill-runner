"""Shared filesystem and parsing helpers for dotnet-project-context."""

from __future__ import annotations

import fnmatch
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

PROJECT_SUFFIXES = {".csproj", ".fsproj", ".vbproj"}
SOLUTION_SUFFIXES = {".sln", ".slnx"}
IGNORED_DIRS = {
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".vs",
    ".vscode-test",
    "__pycache__",
    "bin",
    "coverage",
    "dist",
    "node_modules",
    "obj",
    "temp",
    "tmp",
    "TestResults",
}
IGNORED_PATH_SEGMENTS = {"fixtures", "samples"}
IGNORED_RELATIVE_PREFIXES = (
    (".agents", ".deps"),
    (".agents", "local-ai"),
    (".agents", "tools", "cache"),
    ("automations", "*", "runs"),
)

def rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)

def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""

def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}

def parse_xml(path: Path) -> ET.Element | None:
    try:
        return ET.fromstring(read_text(path))
    except ET.ParseError:
        return None

def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

def relative_parts(root: Path, path: Path) -> tuple[str, ...]:
    try:
        return path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        return path.parts

def matches_prefix(parts: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    if len(parts) < len(prefix):
        return False
    return all(pattern == "*" or fnmatch.fnmatch(part, pattern) for part, pattern in zip(parts, prefix))

def is_ignored_path(root: Path, path: Path) -> bool:
    parts = relative_parts(root, path)
    lowered = tuple(part.lower() for part in parts)
    if any(part in {name.lower() for name in IGNORED_DIRS} or part.startswith(".cache") for part in lowered):
        return True
    if any(part in IGNORED_PATH_SEGMENTS for part in lowered):
        return True
    return any(matches_prefix(lowered, prefix) for prefix in IGNORED_RELATIVE_PREFIXES)

def iter_files(root: Path, max_files: int = 8000) -> list[Path]:
    files: list[Path] = []
    if not root.exists() or not root.is_dir():
        return files
    for current_root, dirnames, filenames in os.walk(root):
        current = Path(current_root)
        dirnames[:] = [name for name in dirnames if not is_ignored_path(root, current / name)]
        for filename in sorted(filenames):
            path = current / filename
            if is_ignored_path(root, path):
                continue
            files.append(path)
            if len(files) >= max_files:
                return files
    return files
