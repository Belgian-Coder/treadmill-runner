#!/usr/bin/env python3
"""Dependency and purpose parsing for skill-manager location analysis."""

from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True

from analysis_support import analysis_common as common
from repo_support import repo_policy

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.12+ is required.
    tomllib = None

def summarize_purpose(root: Path, files: list[Path]) -> list[str]:
    bullets: list[str] = []
    skill_file = root / "SKILL.md" if root.is_dir() else None
    if skill_file and skill_file.exists():
        name, description = common.extract_frontmatter_description(skill_file)
        if name:
            bullets.append(f"Skill name: `{name}`.")
        if description:
            bullets.append(f"Skill description: {description}")

    readme = common.first_existing(root, ["README.md", "readme.md", "AGENTS.md", "CLAUDE.md"])
    if readme and readme.exists():
        text = common.read_text(readme, limit=20_000)
        heading = next((line.strip("# ").strip() for line in text.splitlines() if line.startswith("#")), "")
        paragraph = next(
            (
                line.strip()
                for line in text.splitlines()
                if line.strip() and not line.startswith("#") and not line.startswith("!")
            ),
            "",
        )
        if heading:
            bullets.append(f"Primary documentation heading: {heading}.")
        if paragraph:
            snippet_chars = repo_policy.int_value(
                repo_policy.project_root(path), "limits.output.evidence_snippet_chars"
            )
            bullets.append(f"First useful documentation line: {paragraph[:snippet_chars]}")

    package = root / "package.json" if root.is_dir() else None
    if package and package.exists():
        try:
            data = json.loads(common.read_text(package))
            if data.get("description"):
                bullets.append(f"package.json description: {data['description']}")
        except json.JSONDecodeError:
            pass

    pyproject = root / "pyproject.toml" if root.is_dir() else None
    if pyproject and pyproject.exists() and tomllib is not None:
        try:
            data = tomllib.loads(common.read_text(pyproject))
            project = data.get("project", {})
            poetry = data.get("tool", {}).get("poetry", {})
            description = project.get("description") or poetry.get("description")
            if description:
                bullets.append(f"pyproject.toml description: {description}")
        except tomllib.TOMLDecodeError:
            pass

    if not bullets:
        suffix_counts = Counter(path.suffix.lower() or "<none>" for path in files)
        common_suffixes = ", ".join(
            f"{suffix} ({count})" for suffix, count in suffix_counts.most_common(5)
        )
        bullets.append(
            "No explicit skill or package description found. Infer purpose manually "
            f"from files; most common file types: {common_suffixes or 'none'}."
        )
    return bullets


def parse_package_json(path: Path) -> list[str]:
    try:
        data = json.loads(common.read_text(path))
    except json.JSONDecodeError:
        return ["package.json is present but could not be parsed."]
    deps: list[str] = []
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        values = data.get(section, {})
        if isinstance(values, dict):
            for name, version in sorted(values.items()):
                deps.append(f"npm {section}: `{name}` {version}")
    scripts = data.get("scripts", {})
    if isinstance(scripts, dict) and scripts:
        deps.append(f"npm scripts: {', '.join(sorted(scripts.keys()))}")
    return deps


def parse_pyproject(path: Path) -> list[str]:
    if tomllib is None:
        return ["pyproject.toml is present but TOML parsing is unavailable."]
    try:
        data = tomllib.loads(common.read_text(path))
    except tomllib.TOMLDecodeError:
        return ["pyproject.toml is present but could not be parsed."]

    deps: list[str] = []
    project = data.get("project", {})
    for dep in project.get("dependencies", []) or []:
        deps.append(f"Python dependency: `{dep}`")
    optional = project.get("optional-dependencies", {}) or {}
    if isinstance(optional, dict):
        for group, values in sorted(optional.items()):
            for dep in values or []:
                deps.append(f"Python optional dependency `{group}`: `{dep}`")

    poetry = data.get("tool", {}).get("poetry", {})
    poetry_deps = poetry.get("dependencies", {}) if isinstance(poetry, dict) else {}
    if isinstance(poetry_deps, dict):
        for name, value in sorted(poetry_deps.items()):
            if name.lower() != "python":
                deps.append(f"Poetry dependency: `{name}` {value}")
    return deps


def parse_requirements(path: Path) -> list[str]:
    deps: list[str] = []
    for line in common.read_text(path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        deps.append(f"Python requirement: `{stripped}`")
    return deps


def parse_go_mod(path: Path) -> list[str]:
    deps: list[str] = []
    for line in common.read_text(path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        if stripped.startswith("module "):
            deps.append(f"Go module: `{stripped.split(maxsplit=1)[1]}`")
        elif re.match(r"^[\w./-]+\s+v\d", stripped):
            deps.append(f"Go dependency: `{stripped}`")
    return deps


def parse_cargo(path: Path) -> list[str]:
    if tomllib is None:
        return ["Cargo.toml is present but TOML parsing is unavailable."]
    try:
        data = tomllib.loads(common.read_text(path))
    except tomllib.TOMLDecodeError:
        return ["Cargo.toml is present but could not be parsed."]
    deps: list[str] = []
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        values = data.get(section, {})
        if isinstance(values, dict):
            for name, value in sorted(values.items()):
                deps.append(f"Cargo {section}: `{name}` {value}")
    return deps


def parse_dotnet(path: Path) -> list[str]:
    text = common.read_text(path)
    deps = []
    for match in re.finditer(
        r"<PackageReference\s+Include=\"([^\"]+)\"\s+Version=\"([^\"]+)\"", text
    ):
        deps.append(f".NET package: `{match.group(1)}` {match.group(2)}")
    return deps


def parse_python_imports(files: list[Path], root: Path) -> tuple[bool, list[str]]:
    python_files = [path for path in files if path.suffix.lower() == ".py"]
    if not python_files:
        return False, []

    local_names = {
        path.stem
        for path in files
        if path.suffix.lower() == ".py" and path.name != "__init__.py"
    }
    if root.is_dir():
        local_names.update(
            child.name for child in root.iterdir() if child.is_dir() and child.name.isidentifier()
        )
    local_names.update(
        path.parent.name
        for path in files
        if path.name == "__init__.py" and path.parent.name.isidentifier()
    )

    external: set[str] = set()
    stdlib_names = getattr(sys, "stdlib_module_names", set())

    for path in python_files[:200]:
        try:
            tree = ast.parse(common.read_text(path, limit=200_000))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name.split(".", 1)[0]
                    if name not in stdlib_names and name not in local_names:
                        external.add(name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                name = node.module.split(".", 1)[0]
                if node.level == 0 and name not in stdlib_names and name not in local_names:
                    external.add(name)

    return True, sorted(external)


def dependency_report(root: Path, files: list[Path]) -> tuple[list[str], list[str]]:
    dependencies: list[str] = []
    manifests: list[str] = []

    for path in files:
        name = path.name
        rel = common.relative(root if root.is_dir() else path.parent, path)
        if name in common.MANIFEST_NAMES or path.suffix.lower() == ".csproj":
            manifests.append(rel)

        lower = name.lower()
        if lower == "package.json":
            dependencies.extend(parse_package_json(path))
        elif lower == "pyproject.toml":
            dependencies.extend(parse_pyproject(path))
        elif lower.startswith("requirements") and lower.endswith(".txt"):
            dependencies.extend(parse_requirements(path))
        elif lower == "go.mod":
            dependencies.extend(parse_go_mod(path))
        elif lower == "cargo.toml":
            dependencies.extend(parse_cargo(path))
        elif path.suffix.lower() == ".csproj":
            dependencies.extend(parse_dotnet(path))
        elif lower in {"dockerfile", "docker-compose.yml", "docker-compose.yaml"}:
            dependencies.append(f"Container tooling manifest: `{rel}`")

    has_python, external_python = parse_python_imports(files, root)
    if external_python:
        dependencies.extend(f"Python import to verify: `{name}`" for name in external_python)
    elif has_python:
        dependencies.append("Python 3.12+ stdlib only (inferred from Python scripts).")

    if not dependencies:
        dependencies.append("No package manifest dependencies detected.")

    return manifests, sorted(set(dependencies))
