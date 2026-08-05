#!/usr/bin/env python3
"""Generate workflow-ready project context files from an existing project."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
SKILLS_DIR = SCRIPT_DIR.parent.parent
DOTNET_CONTEXT_SCRIPT_DIR = SKILLS_DIR / "dotnet-project-context" / "scripts"
if DOTNET_CONTEXT_SCRIPT_DIR.exists() and str(DOTNET_CONTEXT_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(DOTNET_CONTEXT_SCRIPT_DIR))

import run_project_validation
try:
    import dotnet_project_context
except ImportError:  # pragma: no cover - exercised by copied generator without the skill installed
    dotnet_project_context = None

IGNORED_DIRS = {
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".venv",
    "__pycache__",
    "bin",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "obj",
    "venv",
}
IGNORED_PATH_SEGMENTS = {"fixtures", "samples"}
IGNORED_RELATIVE_PREFIXES = (
    (".agents", ".deps"),
    (".agents", "local-ai"),
    (".agents", "tools", "cache"),
    ("automations", "*", "runs"),
)
RESPONSIBILITY_HINTS = {
    "src": "application and library source",
    "source": "application and library source",
    "tests": "automated tests",
    "test": "automated tests",
    "docs": "project documentation",
    "scripts": "developer and automation scripts",
    "config": "configuration",
    "infra": "infrastructure",
    "migrations": "database migrations",
    "public": "static web assets",
    "components": "UI components",
    "pages": "routed UI pages",
    "api": "API surface",
}


def rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def require_under_root(root: Path, path: Path, *, label: str) -> Path:
    root = root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"{label} must resolve under target project: {resolved}") from exc
    return resolved


def resolve_output_dir(root: Path, output_dir: Path) -> Path:
    candidate = output_dir if output_dir.is_absolute() else root / output_dir
    return require_under_root(root, candidate, label="--output-dir")


def generated_package_markers(output_dir: Path) -> list[Path]:
    return [
        output_dir / "project-context.md",
        output_dir / "project-context.json",
        output_dir / "validation" / "validation-manifest.json",
        output_dir / "validation" / "run_project_validation.py",
        output_dir / "diagrams" / "project-context-structure.mmd",
        output_dir / "diagrams" / "project-context-structure.svg",
        output_dir / "diagrams" / "project-context-architecture.mmd",
        output_dir / "diagrams" / "project-context-architecture.svg",
    ]


def generated_package_exists(output_dir: Path) -> bool:
    return any(path.exists() for path in generated_package_markers(output_dir))


def sidecar_output_dir(output_dir: Path) -> Path:
    candidate = output_dir / "generated"
    if not generated_package_exists(candidate):
        return candidate
    suffix = 2
    while generated_package_exists(output_dir / f"generated-{suffix}"):
        suffix += 1
    return output_dir / f"generated-{suffix}"


def relative_parts(root: Path, path: Path) -> tuple[str, ...]:
    try:
        return path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        return path.parts


def matches_prefix(parts: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    if len(parts) < len(prefix):
        return False
    return all(pattern == "*" or fnmatch.fnmatch(part, pattern) for part, pattern in zip(parts, prefix))


def is_ignored_project_path(root: Path, path: Path) -> bool:
    parts = relative_parts(root, path)
    lowered = tuple(part.lower() for part in parts)
    if any(part in IGNORED_DIRS or part.startswith(".cache") for part in lowered):
        return True
    if any(part in IGNORED_PATH_SEGMENTS for part in lowered):
        return True
    return any(matches_prefix(lowered, prefix) for prefix in IGNORED_RELATIVE_PREFIXES)


def mermaid_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', "'")


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def iter_project_files(root: Path, max_files: int = 5000) -> list[Path]:
    files: list[Path] = []
    for current_root, dirnames, filenames in os.walk(root):
        current = Path(current_root)
        dirnames[:] = [
            name
            for name in dirnames
            if not is_ignored_project_path(root, current / name)
        ]
        for filename in sorted(filenames):
            path = current / filename
            if is_ignored_project_path(root, path):
                continue
            files.append(path)
            if len(files) >= max_files:
                return files
    return files


def top_folders(root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir() or child.name in IGNORED_DIRS:
            continue
        key = child.name.lower()
        responsibility = RESPONSIBILITY_HINTS.get(key, "project-owned folder; inspect before editing")
        rows.append({"path": child.name, "responsibility": responsibility})
        if len(rows) >= 16:
            break
    return rows


def csproj_info(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in files:
        if path.suffix.lower() != ".csproj":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        frameworks = re.findall(r"<TargetFrameworks?>([^<]+)</TargetFrameworks?>", text)
        packages = re.findall(r'<PackageReference\s+Include="([^"]+)"', text)
        rows.append({"path": rel(root, path), "target_frameworks": frameworks, "packages": packages[:20]})
    return rows


def dotnet_context_report(root: Path, files: list[Path]) -> dict[str, Any]:
    has_dotnet_signal = any(path.suffix.lower() in {".sln", ".slnx", ".csproj", ".fsproj", ".vbproj"} for path in files)
    if not has_dotnet_signal:
        return {}
    if dotnet_project_context is None:
        return {
            "schema_version": 1,
            "tool": "dotnet-project-context",
            "ok": True,
            "status": "partial",
            "target": str(root.resolve(strict=False)),
            "dotnet_cli": {"available": False},
            "solutions": [],
            "projects": [],
            "nuget": {},
            "validation_candidates": [],
            "context_facts": [],
            "advisories": [{"id": "dotnet-context-skill-missing", "message": "dotnet-project-context skill is not available in this copy."}],
            "skipped": [{"id": "dotnet-context-skill", "reason": "dotnet-project-context import failed"}],
        }
    return dotnet_project_context.build_report(root, probe_cli=False)


def package_info(root: Path) -> dict[str, Any]:
    package = read_json(root / "package.json")
    if not package:
        return {}
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    dependencies: list[str] = []
    for key in ("dependencies", "devDependencies"):
        deps = package.get(key)
        if isinstance(deps, dict):
            dependencies.extend(str(name) for name in deps)
    return {
        "path": "package.json",
        "name": package.get("name", ""),
        "scripts": {str(key): str(value) for key, value in scripts.items()},
        "dependencies": sorted(set(dependencies))[:40],
    }


def detect_technologies(root: Path, files: list[Path]) -> list[str]:
    found: list[str] = []
    suffixes = {path.suffix.lower() for path in files}
    names = {path.name.lower() for path in files}
    if ".sln" in suffixes or ".slnx" in suffixes or {".csproj", ".fsproj", ".vbproj"}.intersection(suffixes):
        found.append(".NET")
    if "package.json" in names:
        found.append("Node.js/npm")
    if "pyproject.toml" in names or "requirements.txt" in names or ".py" in suffixes:
        found.append("Python")
    if any(name.startswith("playwright.config.") for name in names) or run_project_validation.has_playwright(root):
        found.append("Playwright")
    if "dockerfile" in names or "docker-compose.yml" in names:
        found.append("Docker")
    if ".github" in {relative_parts(root, path)[0].lower() for path in files if relative_parts(root, path)}:
        found.append("GitHub Actions")
    return sorted(set(found))


def security_notes(root: Path, files: list[Path]) -> list[str]:
    notes = ["Secret values are not emitted; only file names and configuration key names are reported."]
    names = {path.name.lower() for path in files}
    if any(name.startswith(".env") for name in names):
        notes.append("Environment files are present; keep real values local and document only variable names.")
    if any(name in {"appsettings.json", "appsettings.development.json"} for name in names):
        notes.append(".NET appsettings files are present; inspect provider binding and secret storage before config changes.")
    if root.joinpath(".github", "workflows").exists():
        notes.append("CI workflow files are present; compare local validation with CI before handoff.")
    if len(notes) == 1:
        notes.append("No common secret/config file names were detected.")
    return notes


def write_dark_svg(path: Path, title: str, labels: list[str]) -> None:
    width = 920
    height = 188
    y = 58
    box_width = 145
    gap = 20
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="920" height="188" viewBox="0 -24 920 188" '
        'style="max-width: 920px; background-color: transparent;" preserveAspectRatio="xMidYMid meet" '
        'data-mermaid-vertical-padding="24" role="img">',
        f"<title>{escape(title)}</title>",
    ]
    for index, label in enumerate(labels[:6]):
        x = 24 + index * (box_width + gap)
        lines.append(f'<rect x="{x}" y="{y}" width="{box_width}" height="44" rx="6" fill="#1f2937" stroke="#d1d5db"/>')
        lines.append(
            f'<text x="{x + box_width / 2}" y="{y + 27}" text-anchor="middle" '
            'font-family="Arial, sans-serif" font-size="12" fill="#f9fafb">'
            f"{escape(label)}</text>"
        )
        if index < min(len(labels), 6) - 1:
            x1 = x + box_width
            x2 = x1 + gap - 6
            lines.append(f'<line x1="{x1}" y1="{y + 22}" x2="{x2}" y2="{y + 22}" stroke="#d1d5db"/>')
            lines.append(f'<polygon points="{x2},{y + 22} {x2 - 7},{y + 17} {x2 - 7},{y + 27}" fill="#d1d5db"/>')
    lines.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def write_diagrams(root: Path, output_dir: Path, folders: list[dict[str, str]]) -> list[dict[str, str]]:
    diagrams = output_dir / "diagrams"
    diagrams.mkdir(parents=True, exist_ok=True)
    structure_mmd = diagrams / "project-context-structure.mmd"
    architecture_mmd = diagrams / "project-context-architecture.mmd"
    structure_svg = diagrams / "project-context-structure.svg"
    architecture_svg = diagrams / "project-context-architecture.svg"
    structure_lines = ["graph TD;", '  root["Project root"];']
    labels = ["Project root"]
    for index, item in enumerate(folders[:5], start=1):
        node = f"f{index}"
        structure_lines.append(f'  root --> {node}["{mermaid_label(item["path"])}"];')
        labels.append(item["path"])
    architecture_lines = [
        "graph TD;",
        '  request["Work item"] --> context["Project context"];',
        '  context --> plan["Workflow plan"];',
        '  plan --> change["Implementation"];',
        '  change --> validation["Validation runner"];',
        '  validation --> evidence["Evidence package"];',
    ]
    structure_mmd.write_text("\n".join(structure_lines) + "\n", encoding="utf-8", newline="\n")
    architecture_mmd.write_text("\n".join(architecture_lines) + "\n", encoding="utf-8", newline="\n")
    write_dark_svg(structure_svg, "Project structure", labels)
    write_dark_svg(architecture_svg, "Project workflow architecture", ["Work item", "Context", "Plan", "Change", "Validation", "Evidence"])
    return [
        {"source": rel(root, structure_mmd), "image": rel(root, structure_svg)},
        {"source": rel(root, architecture_mmd), "image": rel(root, architecture_svg)},
    ]


def build_context(root: Path, output_dir: Path, data: dict[str, Any], diagrams: list[dict[str, str]]) -> str:
    updated = date.today().isoformat()
    lines = [
        "---",
        "title: Project Context",
        "type: project-context",
        "status: generated",
        "owner: project-context-generator",
        "audience: agent",
        f"updated: {updated}",
        "---",
        "",
        "# Project Context",
        "",
        "Generated project context for workflow planning and validation. Review assumptions before treating it as project policy.",
        "",
        "## Project Information",
        "",
        f"- Project root: `{data['target']}`",
        f"- Generated at: `{data['generated_at']}`",
        f"- Detected project name: `{data.get('project_name') or Path(data['target']).name}`",
        "- Context status: generated; ready for workflow use with recorded assumptions.",
        "",
        "## Technologies",
        "",
    ]
    lines.extend(f"- {item}" for item in data["technologies"]) if data["technologies"] else lines.append("- No major framework signals detected.")
    if data.get("package"):
        package = data["package"]
        lines.extend(["", "### Node Package", "", f"- Name: `{package.get('name') or 'not declared'}`"])
        if package.get("scripts"):
            lines.append("- Scripts:")
            lines.extend(f"  - `{key}`: `{value}`" for key, value in package["scripts"].items())
    if data.get("dotnet_projects"):
        lines.extend(["", "### .NET Projects", ""])
        for item in data["dotnet_projects"]:
            frameworks = ", ".join(item.get("target_frameworks", [])) or "not declared"
            lines.append(f"- `{item['path']}` targets {frameworks}")
    dotnet_context = data.get("dotnet_context") if isinstance(data.get("dotnet_context"), dict) else {}
    if dotnet_context:
        nuget = dotnet_context.get("nuget") if isinstance(dotnet_context.get("nuget"), dict) else {}
        dotnet_cli = dotnet_context.get("dotnet_cli") if isinstance(dotnet_context.get("dotnet_cli"), dict) else {}
        global_json = dotnet_context.get("global_json") if isinstance(dotnet_context.get("global_json"), dict) else {}
        validation_candidates = dotnet_context.get("validation_candidates") if isinstance(dotnet_context.get("validation_candidates"), list) else []
        build_policy = dotnet_context.get("build_policy") if isinstance(dotnet_context.get("build_policy"), dict) else {}
        ci = dotnet_context.get("ci") if isinstance(dotnet_context.get("ci"), dict) else {}
        configuration = dotnet_context.get("configuration") if isinstance(dotnet_context.get("configuration"), dict) else {}
        persistence = dotnet_context.get("persistence") if isinstance(dotnet_context.get("persistence"), dict) else {}
        features = dotnet_context.get("features") if isinstance(dotnet_context.get("features"), dict) else {}
        lines.extend(["", "### .NET Context", ""])
        lines.append(f"- .NET context status: {dotnet_context.get('status')}")
        lines.append(f"- dotnet CLI: {'available' if dotnet_cli.get('available') else 'not probed or missing'}")
        if global_json.get("sdk_version"):
            lines.append(f"- SDK pinned by `global.json`: `{global_json['sdk_version']}`")
        lines.append("- Safety: No restore/build/test/package commands were run while generating this context.")
        if nuget.get("private_feeds_detected"):
            lines.append("- NuGet/feed policy: Private/internal NuGet feeds detected; restore requires project-approved feed credentials and explicit approval.")
        else:
            lines.append("- NuGet/feed policy: no private/internal feeds detected from repo-local NuGet config.")
        if nuget.get("config_paths"):
            lines.append("- Repo-local NuGet config: " + ", ".join(f"`{item}`" for item in nuget.get("config_paths", []) if isinstance(item, str)))
        if nuget.get("central_package_management"):
            lines.append("- Central package management: `Directory.Packages.props` detected.")
        build_properties = build_policy.get("properties") if isinstance(build_policy.get("properties"), dict) else {}
        if build_properties:
            preferred = ["TreatWarningsAsErrors", "Nullable", "AnalysisLevel", "LangVersion", "ManagePackageVersionsCentrally"]
            ordered_keys = [key for key in preferred if key in build_properties]
            ordered_keys.extend(sorted(key for key in build_properties if key not in ordered_keys))
            policy_items = [f"{key}={build_properties[key]}" for key in ordered_keys[:8]]
            lines.append("- Build policy: " + ", ".join(f"`{item}`" for item in policy_items))
        ci_commands = ci.get("dotnet_commands") if isinstance(ci.get("dotnet_commands"), list) else []
        if ci_commands:
            lines.append("- CI dotnet candidates:")
            lines.extend(
                f"  - `{item.get('command')}` from `{item.get('path')}`"
                for item in ci_commands[:6]
                if isinstance(item, dict)
            )
        appsettings = configuration.get("appsettings_files") if isinstance(configuration.get("appsettings_files"), list) else []
        user_secrets = configuration.get("user_secrets_ids") if isinstance(configuration.get("user_secrets_ids"), list) else []
        if appsettings or user_secrets:
            config_bits: list[str] = []
            for item in appsettings[:4]:
                if isinstance(item, dict):
                    names = ", ".join(str(name) for name in item.get("connection_string_names", [])) or "no connection-string names"
                    config_bits.append(f"`{item.get('path')}` ({names})")
            if user_secrets:
                config_bits.append(f"{len(user_secrets)} UserSecretsId entr{'y' if len(user_secrets) == 1 else 'ies'}")
            lines.append("- Configuration inventory: " + "; ".join(config_bits))
        db_contexts = persistence.get("db_contexts") if isinstance(persistence.get("db_contexts"), list) else []
        provider_packages = persistence.get("provider_packages") if isinstance(persistence.get("provider_packages"), list) else []
        if db_contexts or provider_packages:
            persistence_bits: list[str] = []
            for item in db_contexts[:4]:
                if isinstance(item, dict):
                    persistence_bits.append(f"`{item.get('path')}` ({', '.join(str(name) for name in item.get('class_names', []))})")
            if provider_packages:
                persistence_bits.append("packages " + ", ".join(f"`{item}`" for item in provider_packages[:6]))
            lines.append("- Persistence signals: " + "; ".join(persistence_bits))
        feature_signals = features.get("signals") if isinstance(features.get("signals"), list) else []
        if feature_signals:
            lines.append("- Feature signals: " + ", ".join(f"`{item.get('id')}`" for item in feature_signals[:12] if isinstance(item, dict)))
        if validation_candidates:
            lines.append("- .NET validation candidates for later review:")
            lines.extend(f"  - `{item.get('command')}` ({item.get('kind')})" for item in validation_candidates if isinstance(item, dict))
    lines.extend(["", "## Structure And Responsibilities", ""])
    lines.append(f"[![Project structure](diagrams/project-context-structure.svg)](diagrams/project-context-structure.svg)")
    lines.extend(["", "Source: [Mermaid](diagrams/project-context-structure.mmd)", ""])
    lines.extend(["| Path | Responsibility |", "|---|---|"])
    for item in data["folders"]:
        lines.append(f"| `{item['path']}/` | {item['responsibility']} |")
    if not data["folders"]:
        lines.append("| `.` | Project root; inspect before editing |")
    lines.extend(["", "## Architecture And Workflow Use", ""])
    lines.append(f"[![Project workflow architecture](diagrams/project-context-architecture.svg)](diagrams/project-context-architecture.svg)")
    lines.extend(["", "Source: [Mermaid](diagrams/project-context-architecture.mmd)", ""])
    lines.extend(
        [
            "- User story and bug workflows should load this file before planning.",
            "- Navigation maps, when present, live under `automations/navigation/artifacts/maps/`; start with `HANDOFF.md` for the compact read order.",
            "- Plans should reference exact validation commands from `docs/project/validation/validation-manifest.json`.",
            "- Data-impacting work still needs story-specific impacted entities and ERD evidence in the workflow plan.",
            "",
            "## Security And Configuration Notes",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in data["security_notes"])
    lines.extend(["", "## Validation And Proof", ""])
    lines.append("- Validation runner: `python -B docs/project/validation/run_project_validation.py --target . --evidence-dir docs/project/validation/evidence`")
    lines.append("- Optional Playwright screenshot proof: add `--screenshot-url <local-url>` after starting the app.")
    lines.append("- Evidence output: `docs/project/validation/evidence/<run-id>/validation-report.json` and command logs.")
    lines.extend(["", "| Check | Command | Kind | Required |", "|---|---|---|---|"])
    for command in data["validation_commands"]:
        lines.append(f"| {command['label']} | `{command['command_text']}` | {command['kind']} | {str(command['required']).lower()} |")
    if not data["validation_commands"]:
        lines.append("| none detected | record project-specific commands | validation | true |")
    lines.extend(
        [
            "",
            "## Generated Files And Boundaries",
            "",
            "- Generated project context package: `docs/project/project-context.md`, `docs/project/project-context.json`, `docs/project/diagrams/`, and `docs/project/validation/`.",
            "- Generated navigation package: `automations/navigation/artifacts/maps/` when initialized by `setup` or `repo-navigation`.",
            "- Do not commit secrets, local caches, model payloads, browser traces, screenshots, or validation evidence unless the project policy explicitly asks for retained proof.",
            "",
            "## Agent Workflow Notes",
            "",
            "- Read `docs/project/project-context.md` before bug, story, migration, upgrade, or validation planning.",
            "- Read `automations/navigation/artifacts/maps/HANDOFF.md` when present for the compact project map and stale-source check command.",
            "- Refresh context and maps when manifests, source layout, validation commands, CI, security-sensitive config, or generated-file boundaries change.",
        ]
    )
    lines.extend(["", "## Freshness", ""])
    lines.extend(
        [
            f"- Last generated: {updated}",
            "- Last reviewed: not reviewed; generated automatically for workflow use with recorded assumptions.",
            "- Refresh when project files, dependencies, app startup, test commands, CI, Playwright config, migrations, or security-sensitive configuration changes.",
            "- Refresh command: `python -B .agents/skills/project-context-generator/scripts/generate_project_context.py --target . --write --overwrite`",
        ]
    )
    return "\n".join(lines) + "\n"


def project_context_data(root: Path) -> dict[str, Any]:
    files = iter_project_files(root)
    dotnet_context = dotnet_context_report(root, files)
    return {
        "schema_version": 1,
        "tool": "project-context-generator",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "target": str(root.resolve()),
        "project_name": root.name,
        "technologies": detect_technologies(root, files),
        "folders": top_folders(root),
        "dotnet_projects": csproj_info(root, files),
        "dotnet_context": dotnet_context,
        "package": package_info(root),
        "security_notes": security_notes(root, files),
        "validation_commands": run_project_validation.discover_commands(root),
    }


def write_outputs(root: Path, output_dir: Path, overwrite: bool) -> list[str]:
    output_dir = resolve_output_dir(root, output_dir)
    if generated_package_exists(output_dir) and not overwrite:
        output_dir = sidecar_output_dir(output_dir)
    context_path = output_dir / "project-context.md"
    data = project_context_data(root)
    diagrams = write_diagrams(root, output_dir, data["folders"])
    data["diagrams"] = diagrams
    context = build_context(root, output_dir, data, diagrams)
    validation_dir = output_dir / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    written = [
        context_path,
        output_dir / "project-context.json",
        validation_dir / "validation-manifest.json",
        validation_dir / "run_project_validation.py",
    ]
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(context, encoding="utf-8", newline="\n")
    (output_dir / "project-context.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    manifest = {
        "schema_version": 1,
        "tool": "project-context-generator.validation-manifest",
        "commands": data["validation_commands"],
        "evidence_dir": "docs/project/validation/evidence",
        "playwright_screenshot": {
            "supported": any(item["kind"] == "browser-test" for item in data["validation_commands"]),
            "argument": "--screenshot-url <local-url>",
        },
    }
    (validation_dir / "validation-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    runner_source = SCRIPT_DIR / "run_project_validation.py"
    (validation_dir / "run_project_validation.py").write_text(runner_source.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    written.extend(output_dir / "diagrams" / name for name in (
        "project-context-structure.mmd",
        "project-context-structure.svg",
        "project-context-architecture.mmd",
        "project-context-architecture.svg",
    ))
    return [rel(root, path) for path in written]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="read project root to inspect; prefer the narrow app/project root")
    parser.add_argument("--output-dir", default="docs/project", help="write destination under the target project when --write is passed")
    parser.add_argument("--write", action="store_true", help="write generated docs/project context files under the target project")
    parser.add_argument("--overwrite", action="store_true", help="write/overwrite existing reviewed project context; requires explicit approval")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown", dest="output_format", help="stdout report format")
    return parser


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Project Context Generation",
        "",
        f"- Target: `{report['target']}`",
        f"- Status: {report['status']}",
    ]
    if report["written"]:
        lines.extend(["", "## Written", ""])
        lines.extend(f"- `{path}`" for path in report["written"])
    lines.extend(["", "## Technologies", ""])
    lines.extend(f"- {item}" for item in report["detected"]["technologies"]) if report["detected"]["technologies"] else lines.append("- None detected.")
    lines.extend(["", "## Validation Commands", ""])
    lines.extend(f"- `{item['command_text']}`" for item in report["detected"]["validation_commands"]) if report["detected"]["validation_commands"] else lines.append("- None detected.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.target).expanduser().resolve()
    data = project_context_data(root)
    written: list[str] = []
    if args.write:
        written = write_outputs(root, Path(args.output_dir), args.overwrite)
    report = {
        "schema_version": 1,
        "tool": "project-context-generator",
        "ok": True,
        "status": "written" if written else "inspected",
        "target": str(root),
        "written": written,
        "detected": {
            "technologies": data["technologies"],
            "validation_commands": data["validation_commands"],
            "dotnet_context_status": data.get("dotnet_context", {}).get("status") if isinstance(data.get("dotnet_context"), dict) else "",
            "folder_count": len(data["folders"]),
        },
    }
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
