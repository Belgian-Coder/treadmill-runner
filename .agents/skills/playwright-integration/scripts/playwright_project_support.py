#!/usr/bin/env python3
"""Shared Playwright project inspection helpers."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

CONFIG_NAMES = (
    "playwright.config.ts",
    "playwright.config.js",
    "playwright.config.mjs",
    "playwright.config.cjs",
)
DOTNET_PROJECT_SUFFIXES = (".csproj", ".fsproj", ".vbproj")

SPEC_SUFFIXES = (
    ".spec.ts",
    ".spec.tsx",
    ".spec.js",
    ".spec.jsx",
    ".spec.mjs",
    ".test.ts",
    ".test.tsx",
    ".test.js",
    ".test.jsx",
    ".test.mjs",
)

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "test-results",
    "playwright-report",
    "blob-report",
    ".cache",
}


def read_package_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, str(exc)
    except json.JSONDecodeError as exc:
        return None, f"package.json is not valid JSON: {exc}"
    if not isinstance(data, dict):
        return None, "package.json must contain a JSON object"
    return data, None


def dependency_names(package_json: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        deps = package_json.get(section)
        if isinstance(deps, dict):
            names.update(str(name) for name in deps)
    return names


def script_items(package_json: dict[str, Any]) -> dict[str, str]:
    scripts = package_json.get("scripts")
    if not isinstance(scripts, dict):
        return {}
    return {str(name): str(value) for name, value in scripts.items()}


def find_configs(root: Path) -> list[Path]:
    return [root / name for name in CONFIG_NAMES if (root / name).exists()]


def is_skipped(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return any(part in SKIP_DIRS for part in relative.parts)


def iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and not is_skipped(path, root):
            files.append(path)
    return sorted(files, key=lambda item: item.as_posix())


def list_spec_files(root: Path) -> list[Path]:
    return [path for path in iter_files(root) if path.name.endswith(SPEC_SUFFIXES)]


def dotnet_project_files(root: Path) -> list[Path]:
    return [path for path in iter_files(root) if path.suffix.lower() in DOTNET_PROJECT_SUFFIXES]


def dotnet_package_references(project: Path) -> list[str]:
    text = project.read_text(encoding="utf-8-sig", errors="replace")
    packages = re.findall(r"<PackageReference\b[^>]*(?:Include|Update)\s*=\s*['\"]([^'\"]+)['\"]", text, re.IGNORECASE)
    return sorted({package for package in packages if "playwright" in package.lower()})


def dotnet_target_frameworks(project: Path) -> list[str]:
    text = project.read_text(encoding="utf-8-sig", errors="replace")
    frameworks: set[str] = set()
    for match in re.findall(r"<TargetFrameworks?>\s*([^<]+)\s*</TargetFrameworks?>", text, re.IGNORECASE):
        for value in match.split(";"):
            value = value.strip()
            if value:
                frameworks.add(value)
    return sorted(frameworks)


def dotnet_playwright_projects(root: Path) -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    for project in dotnet_project_files(root):
        packages = dotnet_package_references(project)
        if packages:
            projects.append({"path": project.relative_to(root).as_posix(), "packages": packages, "target_frameworks": dotnet_target_frameworks(project)})
    return projects


def dotnet_playwright_signals(root: Path) -> list[str]:
    signals: list[str] = []
    for project in dotnet_playwright_projects(root):
        for package in project["packages"]:
            signals.append(f"csproj:{project['path']}:{package}")
    return sorted(signals)


def detect_framework(root: Path, package_json: dict[str, Any] | None) -> str:
    deps = dependency_names(package_json or {})
    if "next" in deps or (root / "next.config.js").exists() or (root / "next.config.mjs").exists():
        return "nextjs"
    if "@angular/core" in deps or (root / "angular.json").exists():
        return "angular"
    if "nuxt" in deps or (root / "nuxt.config.ts").exists() or (root / "nuxt.config.js").exists():
        return "nuxt"
    if "vue" in deps or (root / "vue.config.js").exists():
        return "vue"
    if "svelte" in deps or "@sveltejs/kit" in deps or (root / "svelte.config.js").exists():
        return "svelte"
    if "vite" in deps or (root / "vite.config.ts").exists() or (root / "vite.config.js").exists():
        return "vite"
    if "react" in deps:
        return "react"
    if dotnet_playwright_signals(root):
        return "dotnet"
    if python_playwright_signals(root):
        return "python"
    return "unknown"


def detect_language(root: Path, package_json: dict[str, Any] | None) -> str:
    deps = dependency_names(package_json or {})
    if (root / "tsconfig.json").exists() or "typescript" in deps or any(config.suffix == ".ts" for config in find_configs(root)):
        return "typescript"
    if dotnet_playwright_signals(root):
        return "csharp"
    if python_playwright_signals(root) or (root / "pyproject.toml").exists():
        return "python"
    return "javascript"


def detect_reporters(root: Path) -> list[str]:
    reporters: set[str] = set()
    for config in find_configs(root):
        text = config.read_text(encoding="utf-8", errors="replace").lower()
        for reporter in ("html", "json", "junit", "list", "line", "github", "blob"):
            if re.search(rf"['\"]{re.escape(reporter)}['\"]", text):
                reporters.add(reporter)
    return sorted(reporters)


def playwright_signals(package_json: dict[str, Any] | None, root: Path) -> list[str]:
    signals: list[str] = []
    if package_json:
        for section in ("dependencies", "devDependencies"):
            deps = package_json.get(section)
            if isinstance(deps, dict):
                for name in sorted(deps):
                    if "playwright" in name.lower():
                        signals.append(f"{section}:{name}")
        for name, value in sorted(script_items(package_json).items()):
            if "playwright" in value.lower():
                signals.append(f"script:{name}")
    for config in find_configs(root):
        signals.append(f"config:{config.name}")
    signals.extend(python_playwright_signals(root))
    signals.extend(dotnet_playwright_signals(root))
    return sorted(signals)


def normalize_python_package_name(value: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", value)
    if not match:
        return ""
    return match.group(1).replace("_", "-").lower()


def requirement_name(line: str) -> str:
    value = line.split("#", 1)[0].split(";", 1)[0].strip()
    if not value or value.startswith(("-", "--")):
        return ""
    return normalize_python_package_name(value)


def python_requirement_files(root: Path) -> list[Path]:
    return [
        path
        for path in iter_files(root)
        if path.name == "requirements.txt" or (path.name.startswith("requirements-") and path.suffix.lower() == ".txt")
    ]


def python_pyproject_files(root: Path) -> list[Path]:
    return [path for path in iter_files(root) if path.name == "pyproject.toml"]


def python_dependencies_from_requirements(path: Path) -> list[str]:
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        name = requirement_name(line)
        if name:
            names.add(name)
    return sorted(names)


def add_python_dependency_names(entries: list[tuple[str, str]], source: str, value: Any) -> None:
    if isinstance(value, str):
        name = normalize_python_package_name(value)
        if name:
            entries.append((source, name))
    elif isinstance(value, list):
        for item in value:
            add_python_dependency_names(entries, source, item)
    elif isinstance(value, dict):
        for name in value:
            normalized = normalize_python_package_name(str(name))
            if normalized and normalized != "python":
                entries.append((source, normalized))


def python_dependencies_from_pyproject(path: Path) -> list[tuple[str, str]]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    entries: list[tuple[str, str]] = []
    project = data.get("project")
    if isinstance(project, dict):
        add_python_dependency_names(entries, "project.dependencies", project.get("dependencies"))
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for group, values in optional.items():
                add_python_dependency_names(entries, f"project.optional-dependencies.{group}", values)
    dependency_groups = data.get("dependency-groups")
    if isinstance(dependency_groups, dict):
        for group, values in dependency_groups.items():
            add_python_dependency_names(entries, f"dependency-groups.{group}", values)
    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            add_python_dependency_names(entries, "tool.poetry.dependencies", poetry.get("dependencies"))
            groups = poetry.get("group")
            if isinstance(groups, dict):
                for group, group_config in groups.items():
                    if isinstance(group_config, dict):
                        add_python_dependency_names(entries, f"tool.poetry.group.{group}.dependencies", group_config.get("dependencies"))
        pdm = tool.get("pdm")
        if isinstance(pdm, dict):
            dev_deps = pdm.get("dev-dependencies")
            if isinstance(dev_deps, dict):
                for group, values in dev_deps.items():
                    add_python_dependency_names(entries, f"tool.pdm.dev-dependencies.{group}", values)
        uv = tool.get("uv")
        if isinstance(uv, dict):
            add_python_dependency_names(entries, "tool.uv.dev-dependencies", uv.get("dev-dependencies"))
    return sorted(set(entries), key=lambda item: (item[0], item[1]))


def python_playwright_manifests(root: Path) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for path in python_requirement_files(root):
        packages = [name for name in python_dependencies_from_requirements(path) if "playwright" in name]
        if packages:
            manifests.append({"path": path.relative_to(root).as_posix(), "kind": "requirements", "packages": packages})
    for path in python_pyproject_files(root):
        packages_by_source: dict[str, list[str]] = {}
        for source, name in python_dependencies_from_pyproject(path):
            if "playwright" in name:
                packages_by_source.setdefault(source, []).append(name)
        for source in packages_by_source:
            packages_by_source[source] = sorted(set(packages_by_source[source]))
        if packages_by_source:
            manifests.append({"path": path.relative_to(root).as_posix(), "kind": "pyproject", "packages_by_source": packages_by_source})
    return sorted(manifests, key=lambda item: item["path"])


def python_playwright_signals(root: Path) -> list[str]:
    signals: list[str] = []
    for manifest in python_playwright_manifests(root):
        path = manifest["path"]
        if manifest["kind"] == "requirements":
            for package in manifest["packages"]:
                signals.append(f"requirements:{path}:{package}")
        elif manifest["kind"] == "pyproject":
            for source, packages in manifest["packages_by_source"].items():
                for package in packages:
                    signals.append(f"pyproject:{path}:{source}:{package}")
    return sorted(signals)


def git_changed_files(root: Path) -> list[Path]:
    commands = [
        ["git", "-C", str(root), "diff", "--name-only", "--diff-filter=ACMRTUXB", "HEAD"],
        ["git", "-C", str(root), "diff", "--cached", "--name-only", "--diff-filter=ACMRTUXB"],
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
    ]
    changed: set[Path] = set()
    for command in commands:
        try:
            completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        except OSError:
            continue
        if completed.returncode != 0:
            continue
        for line in completed.stdout.splitlines():
            if line.strip():
                changed.add((root / line.strip()).resolve())
    return sorted(changed, key=lambda item: item.as_posix())


def route_candidates(root: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    route_roots = [
        root / "app",
        root / "pages",
        root / "src" / "app",
        root / "src" / "pages",
        root / "src" / "routes",
    ]
    for route_root in route_roots:
        if not route_root.exists():
            continue
        for path in route_root.rglob("*"):
            if not path.is_file() or is_skipped(path, root):
                continue
            if path.suffix.lower() not in {".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte"}:
                continue
            relative = path.relative_to(route_root).as_posix()
            if relative.startswith("_") or "/_" in relative:
                continue
            candidates.append({"path": str(path.relative_to(root)), "route_root": str(route_root.relative_to(root))})
    return sorted(candidates, key=lambda item: item["path"])


def config_suggestions(framework: str, language: str) -> list[str]:
    ext = "ts" if language == "typescript" else "js"
    suggestions = {
        "nextjs": ["baseURL http://localhost:3000", "webServer command npm run dev", f"prefer playwright.config.{ext}"],
        "vite": ["baseURL http://localhost:5173", "webServer command npm run dev", f"prefer playwright.config.{ext}"],
        "react": ["detect Vite or app command before choosing baseURL", f"prefer playwright.config.{ext}"],
        "angular": ["baseURL http://localhost:4200", "webServer command npm run start", f"prefer playwright.config.{ext}"],
        "vue": ["baseURL usually http://localhost:5173 for Vite or 3000 for Nuxt", f"prefer playwright.config.{ext}"],
        "nuxt": ["baseURL http://localhost:3000", "webServer command npm run dev", f"prefer playwright.config.{ext}"],
        "svelte": ["baseURL usually http://localhost:5173", "webServer command npm run dev", f"prefer playwright.config.{ext}"],
        "unknown": ["ask for app start command and baseURL before generating config", f"prefer playwright.config.{ext}"],
    }
    return suggestions.get(framework, suggestions["unknown"])


def gitignore_report(root: Path) -> dict[str, Any]:
    required = ["test-results/", "playwright-report/", "blob-report/", "playwright/.cache/"]
    gitignore = root / ".gitignore"
    text = gitignore.read_text(encoding="utf-8", errors="replace") if gitignore.exists() else ""
    missing = [item for item in required if item.rstrip("/") not in text]
    return {
        "path": str(gitignore),
        "exists": gitignore.exists(),
        "required": required,
        "missing": missing,
        "ok": not missing,
    }
