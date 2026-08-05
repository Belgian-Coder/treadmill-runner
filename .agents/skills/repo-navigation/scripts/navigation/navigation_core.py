#!/usr/bin/env python3
"""Deterministic project navigation map generation."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import copy
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

for _ancestor in Path(__file__).resolve().parents:
    _contract_scripts = _ancestor / ".agents" / "skills" / "skill-manager" / "scripts"
    if _contract_scripts.is_dir():
        sys.path.insert(0, str(_contract_scripts))
        break

import module_command

try:
    from repo_support import repo_policy
except ModuleNotFoundError:
    # The generated navigation workflow is also installable as a standalone
    # updater. It may use built-in defaults only when no project-policy file
    # exists; configured policy always requires the canonical validator.
    repo_policy = None

SCHEMA_VERSION = 1
TOOL_NAME = "repo-navigation"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_SCAN_FILES = 5000
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
    "out",
    "temp",
    "tmp",
    "venv",
}
GENERATED_NAVIGATION_PREFIXES = {
    ".superpowers",
    ".agents/.deps",
    ".agents/local-ai/bundle",
    ".agents/local-ai/cache",
    ".agents/local-ai/downloads",
    ".agents/tools/cache",
    "automations/navigation/artifacts/maps",
    "automations/navigation/runs",
    "docs/project/validation/evidence",
}
GENERATED_NAVIGATION_FILES = {
    ".agents/local-ai/local.settings.json",
    ".agents/local-ai/secrets.local.json",
}
TOOL_ONLY_NAVIGATION_JSON = [
    "automations/navigation/artifacts/maps/handoff.json",
    "automations/navigation/artifacts/maps/staleness.json",
]
TEXT_SUFFIXES = {
    ".cs",
    ".css",
    ".editorconfig",
    ".html",
    ".js",
    ".json",
    ".md",
    ".props",
    ".py",
    ".targets",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
SOURCE_HASH_KIND = "sha256-text-lf-or-raw-v2"
SOURCE_GIT_TREE_KIND = "git-filtered-working-sources-v3"
COMMAND_PATTERN = re.compile(
    r"\b("
    r"python(?![\w.-])\s+-B(?:\s+(?!or\b)[A-Za-z0-9_./:=\\-]+)*|"
    r"pytest(?![\w.-])(?:\s+(?!or\b)[A-Za-z0-9_./:=\\-]+)*|"
    r"npm(?![\w.-])\s+(?:test|run\s+[A-Za-z0-9:_-]+)(?:\s+(?!or\b)[A-Za-z0-9_./:=\\-]+)*|"
    r"pnpm(?![\w.-])\s+(?:test|run\s+[A-Za-z0-9:_-]+)(?:\s+(?!or\b)[A-Za-z0-9_./:=\\-]+)*|"
    r"yarn(?![\w.-])\s+(?:test|run\s+[A-Za-z0-9:_-]+)(?:\s+(?!or\b)[A-Za-z0-9_./:=\\-]+)*|"
    r"dotnet(?![\w.-])\s+(?:test|build|run)(?:\s+(?!or\b)[A-Za-z0-9_./:=\\-]+)*"
    r")",
    re.IGNORECASE,
)
PYTHON_SYMBOL_PATTERN = re.compile(r"^\s*(class|def|async\s+def)\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.MULTILINE)
DOTNET_SYMBOL_PATTERN = re.compile(
    r"\b(class|interface|record|struct|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\b"
)
JSTS_SYMBOL_PATTERN = re.compile(
    r"\b(?:export\s+)?(class|function|interface|type)\s+([A-Za-z_$][A-Za-z0-9_$]*)\b"
    r"|\b(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=",
    re.MULTILINE,
)
PYTHON_IMPORT_PATTERN = re.compile(
    r"^\s*(?:from\s+((?:\.*[A-Za-z_][A-Za-z0-9_.]*)|\.+)\s+import|import\s+([A-Za-z_][A-Za-z0-9_.]*))",
    re.MULTILINE,
)
JSTS_IMPORT_PATTERN = re.compile(
    r"\b(?:import|export)\s+(?:[^'\"]+\s+from\s+)?['\"]([^'\"]+)['\"]|"
    r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)",
    re.MULTILINE,
)
DOTNET_USING_PATTERN = re.compile(r"^\s*using\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;", re.MULTILINE)
DOTNET_ROUTE_PATTERN = re.compile(r"\bMap(?:Get|Post|Put|Delete|Patch|Methods)\s*\(\s*\"([^\"]+)\"", re.MULTILINE)
JSTS_ROUTE_PATTERN = re.compile(r"\b(?:router|app)\.(?:get|post|put|delete|patch|use)\s*\(\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
RELATIONSHIP_CONTENT_LIMIT_BYTES = 1024 * 1024


def _project_policy_document(root: Path) -> dict[str, object] | None:
    if repo_policy is None:
        if (root / ".agents" / "project-policy.json").exists():
            raise ValueError("invalid project policy: canonical v2 validator is unavailable")
        return None
    document, issues, exists = repo_policy.load_project_policy(root)
    if issues:
        raise ValueError("invalid project policy: " + "; ".join(issues))
    if not exists:
        return None
    return document


def project_policy_int(path: str, *, start: Path | None = None) -> int:
    root = project_root(start)
    if repo_policy is not None:
        return repo_policy.int_value(root, path)
    defaults = {
        "limits.navigation.map_warn_words": 1400,
        "limits.navigation.scan_warn_entries": 2500,
        "limits.navigation.relationship_max_entries": 6000,
        "limits.navigation.source_snippet_chars": 180,
        "limits.navigation.relationship_evidence_chars": 160,
        "limits.navigation.project_context_placeholder_chars": 120,
    }
    return defaults[path]


def project_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for current in (candidate, *candidate.parents):
        if (current / ".agents" / "project-policy.json").is_file() or (current / ".agents" / "manage.py").is_file():
            return current
    return Path.cwd().resolve()


def project_warning_action(warning_id: str, *, start: Path | None = None) -> str:
    root = project_root(start)
    if repo_policy is not None:
        return repo_policy.warning_action(root, warning_id)
    return "warning"


def require_supported_python() -> None:
    if sys.version_info >= (3, 12):
        return
    current = ".".join(str(part) for part in sys.version_info[:3])
    raise SystemExit(f"Python 3.12+ is required; current interpreter is Python {current}.")


def relpath(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def read_text(path: Path, limit: int = 200_000) -> str:
    try:
        with path.open("rb") as stream:
            data = stream.read(max(0, limit))
    except OSError:
        return ""
    return data.decode("utf-8-sig", errors="replace")


def looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            chunk = stream.read(4096)
    except OSError:
        return False
    return b"\0" in chunk


def utf8_text_content(content: bytes) -> bool:
    if b"\0" in content:
        return False
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return not any(
        (ord(character) < 32 and character not in "\t\n\r\f")
        or 127 <= ord(character) < 160
        for character in text
    )


def sha256_file(path: Path, *, text_attribute: str | None = None) -> str:
    content = path.read_bytes()
    if text_attribute not in {"unset", "unknown"} and utf8_text_content(content):
        content = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(content).hexdigest()


def git_path_attributes(
    target: Path,
    paths: list[str],
    attributes: tuple[str, ...],
) -> dict[str, dict[str, str]] | None:
    if not paths:
        return {}
    if any("\0" in path or "\n" in path or "\r" in path for path in paths):
        return None
    try:
        completed = subprocess.run(
            ["git", "--no-optional-locks", "check-attr", "-z", "--stdin", *attributes],
            cwd=target,
            check=False,
            input=b"\0".join(
                path.encode("utf-8", errors="surrogateescape") for path in paths
            )
            + b"\0",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    fields = completed.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 3 != 0:
        return None
    expected_paths = set(paths)
    expected_attributes = set(attributes)
    result: dict[str, dict[str, str]] = {path: {} for path in paths}
    for index in range(0, len(fields), 3):
        raw_path, raw_attribute, raw_value = fields[index : index + 3]
        normalized = raw_path.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        attribute = raw_attribute.decode("utf-8", errors="surrogateescape")
        value = raw_value.decode("utf-8", errors="surrogateescape")
        if normalized not in expected_paths or attribute not in expected_attributes:
            return None
        result[normalized][attribute] = value
    if any(set(values) != expected_attributes for values in result.values()):
        return None
    return result


def skip_generated_navigation(relative_path: str) -> bool:
    parts = Path(relative_path).parts
    if len(parts) >= 3 and parts[0] == "automations" and parts[2] == "runs":
        return True
    normalized = relative_path.replace("\\", "/")
    if normalized in GENERATED_NAVIGATION_FILES:
        return True
    if normalized.startswith(".agents/skills/") and "/fixtures/" in normalized:
        return True
    return any(
        relative_path == prefix or relative_path.startswith(prefix + "/")
        for prefix in GENERATED_NAVIGATION_PREFIXES
    )


def iter_project_files(
    target: Path,
    max_files: int = 5000,
    *,
    visible_paths: set[str] | None = None,
) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    skipped: list[str] = []
    requested_max_files = max_files
    max_files = max(1, min(max_files, MAX_SCAN_FILES))
    if max_files != requested_max_files:
        skipped.append(
            f"file scan limit clamped from {requested_max_files} to {max_files} files"
        )
    for current_root, dirnames, filenames in os.walk(target):
        current = Path(current_root)
        rel_current = relpath(target, current)
        normalized_current = "" if rel_current == "." else rel_current
        ignored_dirs = sorted(name for name in dirnames if name in IGNORED_DIRS)
        for name in ignored_dirs:
            if name != ".git":
                skipped.append(f"ignored directory `{relpath(target, current / name)}`")
        dirnames[:] = [
            name
            for name in sorted(dirnames, key=str.lower)
            if name not in IGNORED_DIRS
            and not name.endswith(".egg-info")
            and not skip_generated_navigation(
                f"{normalized_current}/{name}".strip("/")
            )
        ]
        for filename in sorted(filenames, key=str.lower):
            path = current / filename
            relative = relpath(target, path)
            if skip_generated_navigation(relative):
                continue
            if visible_paths is not None and relative not in visible_paths:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                skipped.append(f"could not stat `{relative}`")
                continue
            if size > MAX_FILE_BYTES:
                skipped.append(f"skipped large file `{relative}`")
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES and looks_binary(path):
                skipped.append(f"skipped binary file `{relative}`")
                continue
            files.append(path)
            if len(files) >= max_files:
                skipped.append(f"file scan capped at {max_files} files")
                return files, skipped
    return files, skipped


def durable_skipped(scan: dict[str, Any]) -> list[str]:
    """Keep generated maps independent from ignored or unreadable local state."""
    skipped = scan.get("skipped", [])
    if not isinstance(skipped, list):
        return []
    return sorted(
        str(item)
        for item in skipped
        if str(item).startswith(
            (
                "file scan capped at ",
                "file scan limit clamped from ",
                "skipped large file `",
                "skipped binary file `",
            )
        )
    )


def git_visible_paths(target: Path) -> set[str] | None:
    try:
        completed = subprocess.run(
            ["git", "--no-optional-locks", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=target,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return {
        raw.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for raw in completed.stdout.split(b"\0")
        if raw
    }


def responsibility(relative_path: str) -> str:
    lower = relative_path.lower()
    name = Path(relative_path).name.lower()
    parts = lower.split("/")
    if name in {"agents.md", "claude.md", "copilot-instructions.md"}:
        return "agent instructions and repository guidance"
    if name.startswith("readme") or "/docs/" in lower or lower.startswith("docs/"):
        return "human documentation"
    if name.endswith(".sln") or name.endswith(".csproj") or name in {"directory.build.props", "directory.build.targets", "global.json"}:
        return ".NET build and project configuration"
    if name in {"pyproject.toml", "requirements.txt", "requirements-dev.txt"} or name.endswith(".py"):
        return "Python project code or configuration"
    if name in {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "tsconfig.json"} or name.endswith((".ts", ".tsx", ".js", ".jsx")):
        return "JavaScript or TypeScript project code or configuration"
    if "test" in parts or "tests" in parts or name.startswith("test_") or name.endswith("test.cs"):
        return "test code"
    if lower.startswith("automations/"):
        return "workflow automation module"
    if lower.startswith("src/"):
        return "source code"
    return "project file"


def manifest_kind(path: Path) -> str | None:
    name = path.name
    lower = name.lower()
    if lower.endswith(".sln") or lower.endswith(".csproj"):
        return ".NET"
    if lower in {"pyproject.toml", "requirements.txt", "requirements-dev.txt"}:
        return "Python"
    if lower in {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "tsconfig.json"}:
        return "JavaScript/TypeScript"
    if lower in {"readme.md", "agents.md"}:
        return "Guidance"
    return None


def detect_frameworks(target: Path, files: list[Path]) -> list[str]:
    rels = {relpath(target, path).lower(): path for path in files}
    frameworks: set[str] = set()
    if any(path.endswith(".sln") or path.endswith(".csproj") for path in rels):
        frameworks.add(".NET")
    if any(path.endswith(".csproj") and "microsoft.net.sdk.web" in read_text(file).lower() for path, file in rels.items()):
        frameworks.add("ASP.NET Core signal")
    if any(path in {"pyproject.toml", "requirements.txt", "requirements-dev.txt"} or path.endswith(".py") for path in rels):
        frameworks.add("Python")
    if any(path in {"package.json", "tsconfig.json"} or path.endswith((".ts", ".tsx", ".js", ".jsx")) for path in rels):
        frameworks.add("JavaScript/TypeScript")
    if any(path.endswith(".md") for path in rels):
        frameworks.add("Markdown documentation")
    return sorted(frameworks)


def codebase_categories(target: Path, files: list[Path]) -> list[dict[str, Any]]:
    categories: dict[str, list[str]] = {
        "stack": [],
        "structure": [],
        "entrypoints": [],
        "conventions": [],
        "integrations": [],
        "testing": [],
        "risks": [],
    }
    for path in files:
        relative = relpath(target, path)
        lower = relative.lower()
        name = path.name.lower()
        kind = manifest_kind(path)
        if kind:
            categories["stack"].append(relative)
        if path.parent == target:
            categories["structure"].append(relative)
        if is_code_entrypoint(relative):
            categories["entrypoints"].append(relative)
        if name in {".editorconfig", "directory.build.props", "eslint.config.js", "pyproject.toml", "tsconfig.json"}:
            categories["conventions"].append(relative)
        if name in {".env.example", ".env.template", "docker-compose.yml", "dockerfile"} or ".github/workflows/" in lower:
            categories["integrations"].append(relative)
        if "test" in lower or name in {"pytest.ini", "jest.config.js", "playwright.config.ts", "coverlet.runsettings"}:
            categories["testing"].append(relative)
        if "security" in lower or name in {".env", "credentials.json", "secrets.json"}:
            categories["risks"].append(relative)
    return [
        {"category": name, "count": len(set(paths)), "paths": sorted(set(paths))[:30]}
        for name, paths in categories.items()
    ]


def entrypoint_quality(scan: dict[str, Any]) -> list[str]:
    entries = {str(item.get("path", "")) for item in scan.get("entries", []) if isinstance(item, dict)}
    manifests = {str(item.get("path", "")) for item in scan.get("manifests", []) if isinstance(item, dict)}
    warnings: list[str] = []
    if not any(path in entries for path in ("README.md", "AGENTS.md")):
        warnings.append("missing README.md or AGENTS.md guidance entrypoint")
    if scan.get("frameworks") and not manifests:
        warnings.append("framework signals exist but no build/package manifest was captured")
    if not any(is_code_entrypoint(path) for path in entries):
        warnings.append("no common code entrypoint detected")
    skipped = "\n".join(str(item) for item in scan.get("skipped", []))
    if any(name in skipped for name in ("bin", "obj", "node_modules", "dist", "build")):
        warnings.append("ignored build/dependency output was skipped as expected")
    return warnings


def is_code_entrypoint(path: str) -> bool:
    lower = path.replace("\\", "/").lower()
    name = lower.rsplit("/", 1)[-1]
    if name in {"program.cs", "startup.cs", "manage.py", "main.py", "app.py", "server.py", "index.js", "index.ts"}:
        return True
    if lower == ".agents/manage.py":
        return True
    if lower.startswith(".agents/skills/") and lower.endswith(("/skill.md", "/module.json")):
        return True
    if lower.startswith("automations/") and lower.endswith(("/workflow.md", "/module.json")):
        return True
    return False


def map_size_budget(outputs: dict[str, str], scan: dict[str, Any]) -> dict[str, Any]:
    root = project_root()
    navigation_word_budget = project_policy_int("limits.navigation.map_warn_words", start=root)
    project_map_entry_budget = project_policy_int("limits.navigation.scan_warn_entries", start=root)
    warning_action = project_warning_action("navigation.map-size", start=root)
    navigation_words = len(outputs.get("automations/navigation/artifacts/maps/NAVIGATION.md", "").split())
    entries = len(scan.get("entries", []))
    warnings: list[str] = []
    if navigation_words > navigation_word_budget:
        warnings.append(f"NAVIGATION.md has {navigation_words} words; budget is {navigation_word_budget}.")
    if entries > project_map_entry_budget:
        warnings.append(f"navigation scan has {entries} entries; budget is {project_map_entry_budget}.")
    if warning_action == "off":
        warnings = []
    return {
        "navigation_words": navigation_words,
        "navigation_budget": navigation_word_budget,
        "scan_entries": entries,
        "scan_entry_budget": project_map_entry_budget,
        "warning_action": warning_action,
        "status": "error" if warnings and warning_action == "error" else "warn" if warnings else "ok",
        "warnings": warnings,
    }


def stale_source_changes(target: Path, current_hashes: dict[str, str]) -> dict[str, list[str]]:
    staleness_path = target / "automations" / "navigation" / "artifacts" / "maps" / "staleness.json"
    if not staleness_path.exists():
        return {"added": sorted(current_hashes), "modified": [], "deleted": []}
    try:
        previous = json.loads(staleness_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"added": sorted(current_hashes), "modified": [], "deleted": []}
    old_hashes = previous.get("source_hashes", {})
    if not isinstance(old_hashes, dict):
        old_hashes = {}
    old = {str(key): str(value) for key, value in old_hashes.items()}
    current = {str(key): str(value) for key, value in current_hashes.items()}
    return {
        "added": sorted(set(current) - set(old)),
        "modified": sorted(path for path in set(current) & set(old) if current[path] != old[path]),
        "deleted": sorted(set(old) - set(current)),
    }


def extract_package_commands(target: Path, package_file: Path) -> list[dict[str, str]]:
    try:
        data = json.loads(package_file.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return []
    commands: list[dict[str, str]] = []
    for name, value in sorted(scripts.items()):
        command = "npm test" if name == "test" else f"npm run {name}"
        commands.append(
            {
                "path": relpath(target, package_file),
                "command": command,
                "source": f"package.json script `{name}`",
                "detail": str(value),
            }
        )
    return commands[:80]


def extract_commands(target: Path, files: list[Path]) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for path in files:
        if path.name == "package.json":
            for item in extract_package_commands(target, path):
                key = (item["path"], item["command"])
                if key not in seen:
                    seen.add(key)
                    commands.append(item)
        if path.suffix.lower() not in {".md", ".txt", ".toml", ".json", ".yaml", ".yml"}:
            continue
        for line_number, line in enumerate(read_text(path).splitlines(), start=1):
            match = COMMAND_PATTERN.search(line)
            if not match:
                continue
            command = match.group(1).strip()
            key = (relpath(target, path), command)
            if key in seen:
                continue
            seen.add(key)
            commands.append(
                {
                    "path": relpath(target, path),
                    "line": str(line_number),
                    "command": command,
                    "source": "documented command",
                }
            )
    return commands[:80]


def extract_strict_read_only_commands(target: Path, files: list[Path]) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for path in files:
        if path.name != "module.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        values = data.get("strict_read_only_commands") if isinstance(data, dict) else None
        if not isinstance(values, list):
            continue
        typed_commands: dict[str, dict[str, Any]] = {}
        duplicates: set[str] = set()
        command_specs = data.get("commands") if isinstance(data, dict) else None
        if isinstance(command_specs, list):
            for command_spec in command_specs:
                if not isinstance(command_spec, dict):
                    continue
                command_id = command_spec.get("id")
                argv = command_spec.get("argv")
                if not (
                    isinstance(command_id, str)
                    and isinstance(argv, list)
                    and argv
                    and all(isinstance(item, str) and item for item in argv)
                ):
                    continue
                if command_id in typed_commands:
                    duplicates.add(command_id)
                typed_commands[command_id] = copy.deepcopy(command_spec)
        for value in values:
            value_text = str(value).strip()
            command_spec = typed_commands.get(value_text) if value_text not in duplicates else None
            if command_spec:
                commands.append(
                    {
                        "path": relpath(target, path),
                        "command": command_spec,
                        "argv": module_command.command_argv(command_spec),
                        "source": "module strict_read_only_commands",
                    }
                )
    return commands


def extract_symbols_for_file(target: Path, path: Path) -> list[dict[str, str]]:
    suffix = path.suffix.lower()
    if suffix not in {".py", ".cs", ".js", ".jsx", ".ts", ".tsx"}:
        return []
    text = read_text(path, limit=120_000)
    symbols: list[dict[str, str]] = []
    if suffix == ".py":
        for match in PYTHON_SYMBOL_PATTERN.finditer(text):
            kind = "function" if "def" in match.group(1) else "class"
            symbols.append({"path": relpath(target, path), "kind": kind, "name": match.group(2)})
    elif suffix == ".cs":
        for match in DOTNET_SYMBOL_PATTERN.finditer(text):
            symbols.append({"path": relpath(target, path), "kind": match.group(1), "name": match.group(2)})
    else:
        for match in JSTS_SYMBOL_PATTERN.finditer(text):
            if match.group(3):
                symbols.append({"path": relpath(target, path), "kind": "constant", "name": match.group(3)})
            else:
                symbols.append({"path": relpath(target, path), "kind": match.group(1), "name": match.group(2)})
    return symbols[:40]


def extract_symbols(target: Path, files: list[Path]) -> list[dict[str, str]]:
    symbols: list[dict[str, str]] = []
    for path in files:
        symbols.extend(extract_symbols_for_file(target, path))
        if len(symbols) >= 400:
            return symbols[:400]
    return symbols


def python_import_targets(text: str) -> list[dict[str, str]]:
    evidence_chars = project_policy_int("limits.navigation.relationship_evidence_chars")
    rows: list[dict[str, str]] = []
    source_lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        for match in PYTHON_IMPORT_PATTERN.finditer(text):
            target = (match.group(1) or match.group(2) or "").strip()
            if target:
                rows.append(
                    {
                        "kind": "module",
                        "target": target,
                        "evidence": match.group(0).strip(),
                        "confidence_hint": "inferred",
                        "provenance": "python-import-regex",
                    }
                )
        return rows[:80]
    for node in ast.walk(tree):
        start = max(0, int(getattr(node, "lineno", 1)) - 1)
        end = max(start + 1, int(getattr(node, "end_lineno", start + 1)))
        evidence = " ".join(line.strip() for line in source_lines[start:end]).strip()[:evidence_chars]
        if isinstance(node, ast.Import):
            for alias in node.names:
                rows.append(
                    {
                        "kind": "module",
                        "target": alias.name,
                        "evidence": evidence,
                        "confidence_hint": "high",
                        "provenance": "python-ast",
                    }
                )
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            module = f"{prefix}{node.module or ''}"
            if module:
                rows.append(
                    {
                        "kind": "module",
                        "target": module,
                        "evidence": evidence,
                        "confidence_hint": "high",
                        "provenance": "python-ast",
                    }
                )
            for alias in node.names:
                if alias.name == "*":
                    continue
                separator = "" if not module or module.endswith(".") else "."
                candidate = f"{module}{separator}{alias.name}"
                rows.append(
                    {
                        "kind": "module",
                        "target": candidate,
                        "evidence": evidence,
                        "confidence_hint": "inferred",
                        "provenance": "python-ast",
                    }
                )
    return rows[:80]


def javascript_code_positions(text: str) -> bytearray:
    """Mark positions outside JavaScript/TypeScript comments and string literals."""
    positions = bytearray(b"\x01") * len(text)
    index = 0
    state = "code"
    quote = ""
    while index < len(text):
        character = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "code":
            if character == "/" and following == "/":
                positions[index : index + 2] = b"\x00\x00"
                index += 2
                state = "line-comment"
                continue
            if character == "/" and following == "*":
                positions[index : index + 2] = b"\x00\x00"
                index += 2
                state = "block-comment"
                continue
            if character in {"'", '"', "`"}:
                positions[index] = 0
                quote = character
                state = "string"
        elif state == "line-comment":
            if character == "\n":
                state = "code"
            else:
                positions[index] = 0
        elif state == "block-comment":
            positions[index] = 0
            if character == "*" and following == "/":
                positions[index + 1] = 0
                index += 2
                state = "code"
                continue
        else:
            positions[index] = 0
            if character == "\\" and following:
                positions[index + 1] = 0
                index += 2
                continue
            if character == quote:
                state = "code"
        index += 1
    return positions


def imported_targets_for_file(path: Path, text: str) -> list[dict[str, str]]:
    evidence_chars = project_policy_int("limits.navigation.relationship_evidence_chars", start=path)
    suffix = path.suffix.lower()
    rows: list[dict[str, str]] = []
    if suffix == ".py":
        return python_import_targets(text)
    elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
        code_positions = javascript_code_positions(text)
        for match in JSTS_IMPORT_PATTERN.finditer(text):
            if not code_positions or not code_positions[match.start()]:
                continue
            target = (match.group(1) or match.group(2) or "").strip()
            if target:
                rows.append(
                    {
                        "kind": "module",
                        "target": target,
                        "evidence": match.group(0).strip()[:evidence_chars],
                        "confidence_hint": "inferred",
                        "provenance": "js-ts-import-regex",
                    }
                )
    elif suffix == ".cs":
        for match in DOTNET_USING_PATTERN.finditer(text):
            rows.append({"kind": "namespace", "target": match.group(1), "evidence": match.group(0).strip()})
    return rows[:80]


def route_targets_for_file(path: Path, text: str) -> list[dict[str, str]]:
    evidence_chars = project_policy_int("limits.navigation.relationship_evidence_chars", start=path)
    suffix = path.suffix.lower()
    pattern = DOTNET_ROUTE_PATTERN if suffix == ".cs" else JSTS_ROUTE_PATTERN if suffix in {".js", ".jsx", ".ts", ".tsx"} else None
    if pattern is None:
        return []
    return [
        {"kind": "route", "target": match.group(1), "evidence": match.group(0).strip()[:evidence_chars]}
        for match in pattern.finditer(text)
    ][:80]


def likely_test_target(test_path: str, source_paths: set[str]) -> str:
    name = Path(test_path).stem.lower()
    for prefix in ("test_", "tests_", "spec_", "should_"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
    for suffix in ("test", "tests", "spec", "specs"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    if not name:
        return ""
    for source in sorted(source_paths):
        stem = Path(source).stem.lower()
        if name == stem or name in stem or stem in name:
            return source
    return ""


def extract_relationships(
    target: Path,
    files: list[Path],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    relationship_budget = project_policy_int("limits.navigation.relationship_max_entries", start=target)
    source_paths = {
        relpath(target, path)
        for path in files
        if path.suffix.lower() in {".py", ".cs", ".js", ".jsx", ".ts", ".tsx"}
        and "test" not in relpath(target, path).lower()
    }
    relationships: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    content_capped_files: list[str] = []
    relationship_cap_reached = False
    for path in files:
        relative = relpath(target, path)
        suffix = path.suffix.lower()
        if suffix not in {".py", ".cs", ".js", ".jsx", ".ts", ".tsx"}:
            continue
        try:
            if path.stat().st_size > RELATIONSHIP_CONTENT_LIMIT_BYTES:
                content_capped_files.append(relative)
        except OSError:
            content_capped_files.append(relative)
        text = read_text(path, limit=RELATIONSHIP_CONTENT_LIMIT_BYTES)
        for item in imported_targets_for_file(path, text):
            key = ("imports", relative, item["target"])
            if key in seen:
                continue
            seen.add(key)
            relationships.append(
                {
                    "type": "imports",
                    "source": relative,
                    "target": f"{item['kind']}:{item['target']}",
                    "evidence": item["evidence"],
                    "confidence_hint": item.get("confidence_hint", "high"),
                    "provenance_hint": item.get("provenance", ""),
                }
            )
        for item in route_targets_for_file(path, text):
            key = ("routes_to", relative, item["target"])
            if key in seen:
                continue
            seen.add(key)
            relationships.append(
                {
                    "type": "routes_to",
                    "source": relative,
                    "target": f"route:{item['target']}",
                    "evidence": item["evidence"],
                }
            )
        if "test" in relative.lower() or Path(relative).name.lower().endswith((".spec.ts", ".spec.js")):
            test_target = likely_test_target(relative, source_paths)
            if test_target:
                key = ("tests", relative, test_target)
                if key not in seen:
                    seen.add(key)
                    relationships.append(
                        {
                            "type": "tests",
                            "source": relative,
                            "target": test_target,
                            "evidence": "filename heuristic",
                        }
                    )
        if len(relationships) >= relationship_budget:
            relationship_cap_reached = True
            break
    return relationships[:relationship_budget], {
        "relationship_budget": relationship_budget,
        "relationship_cap_reached": relationship_cap_reached,
        "content_read_limit_bytes": RELATIONSHIP_CONTENT_LIMIT_BYTES,
        "content_capped_files": sorted(set(content_capped_files)),
    }


def top_folder_summary(target: Path, files: list[Path]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        relative = relpath(target, path)
        first = relative.split("/", 1)[0]
        grouped[first].append(path)
    rows: list[dict[str, Any]] = []
    for folder, values in sorted(grouped.items()):
        if len(values) == 1 and "/" not in relpath(target, values[0]):
            continue
        purposes = Counter(responsibility(relpath(target, value)) for value in values)
        rows.append(
            {
                "path": folder if folder in {Path(relpath(target, value)).parts[0] for value in values} else folder,
                "file_count": len(values),
                "responsibility": purposes.most_common(1)[0][0],
            }
        )
    return rows


def build_scan(target: Path, max_files: int = 5000) -> dict[str, Any]:
    target = target.expanduser().resolve()
    if not target.exists() or not target.is_dir():
        return {
            "schema_version": SCHEMA_VERSION,
            "tool": TOOL_NAME,
            "ok": False,
            "status": "target not found",
            "target": str(target),
            "files_scanned": 0,
            "entries": [],
            "manifests": [],
            "commands": [],
            "frameworks": [],
            "checks": [],
            "skipped": [],
            "hashes": {},
        }
    visible_paths = git_visible_paths(target)
    files, skipped = iter_project_files(target, max_files=max_files, visible_paths=visible_paths)
    manifests = [
        {
            "path": relpath(target, path),
            "kind": manifest_kind(path),
            "responsibility": responsibility(relpath(target, path)),
        }
        for path in files
        if manifest_kind(path)
    ]
    symbol_rows = extract_symbols(target, files)
    relationships, relationship_extraction = extract_relationships(target, files)
    symbols_by_path: dict[str, list[dict[str, str]]] = defaultdict(list)
    for symbol in symbol_rows:
        symbols_by_path[symbol["path"]].append({"kind": symbol["kind"], "name": symbol["name"]})
    entries = []
    for path in files:
        relative = relpath(target, path)
        row: dict[str, Any] = {
            "path": relative,
            "type": "file",
            "responsibility": responsibility(relative),
            "extension": path.suffix.lower(),
        }
        if symbols_by_path.get(relative):
            row["symbols"] = symbols_by_path[relative][:20]
        entries.append(row)
    folder_entries = [
        {"path": item["path"], "type": "folder", "responsibility": item["responsibility"], "file_count": item["file_count"]}
        for item in top_folder_summary(target, files)
    ]
    relative_paths = [relpath(target, path) for path in files]
    text_attributes = git_path_attributes(target, relative_paths, ("text",))
    hashes = {
        relative: sha256_file(
            path,
            text_attribute=(
                (text_attributes or {}).get(relative, {}).get("text")
                if text_attributes is not None
                else ("unknown" if visible_paths is not None else None)
            ),
        )
        for path, relative in zip(files, relative_paths)
    }
    extension_counts = Counter(path.suffix.lower() or "[no extension]" for path in files)
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "ok": True,
        "status": "ok",
        "target": ".",
        "files_scanned": len(files),
        "entries": folder_entries + entries,
        "manifests": manifests,
        "commands": extract_commands(target, files),
        "strict_read_only_commands": extract_strict_read_only_commands(target, files),
        "frameworks": detect_frameworks(target, files),
        "codebase_categories": codebase_categories(target, files),
        "symbols": symbol_rows,
        "relationships": relationships,
        "relationship_extraction": relationship_extraction,
        "extension_counts": dict(sorted(extension_counts.items())),
        "checks": ["project files scanned", "ignored folders skipped", "deterministic conventions inferred"],
        "skipped": sorted(set(skipped)),
        "hashes": hashes,
    }


def recommended_read_order(scan: dict[str, Any]) -> list[str]:
    entries = {str(item.get("path")) for item in scan.get("entries", []) if isinstance(item, dict)}
    order: list[str] = []
    for candidate in ("AGENTS.md", "README.md", "automations/navigation/WORKFLOW.md"):
        if candidate in entries:
            order.append(candidate)
    for manifest in scan.get("manifests", [])[:10]:
        if isinstance(manifest, dict):
            path = str(manifest.get("path", ""))
            if path and path not in order:
                order.append(path)
    for entry in scan.get("entries", []):
        if isinstance(entry, dict) and str(entry.get("responsibility")) in {"source code", "test code"}:
            path = str(entry.get("path", ""))
            if path and path not in order:
                order.append(path)
            if len(order) >= 14:
                break
    return order


def render_navigation(scan: dict[str, Any]) -> str:
    lines = [
        "# Navigation Map",
        "",
        "Generated by repo-navigation from deterministic file facts. This file is route-first; use `repo_navigation.py focus` or direct source reads instead of broad file opens.",
        "",
        "## Context Guardrails",
        "",
        "- Raw navigation JSON is tool-only; use `HANDOFF.md`, this map, `repo_navigation.py focus`, or status/check commands for orientation.",
        "- Avoid `.agents/local-ai/cache/command-output/` unless debugging command-output artifacts directly.",
        "",
        "## Read First",
        "",
    ]
    order = recommended_read_order(scan)
    lines.extend(f"- `{path}`" for path in order) if order else lines.append("- No read order could be inferred.")
    lines.extend(["", "## Framework Signals", ""])
    frameworks = scan.get("frameworks", [])
    lines.extend(f"- {item}" for item in frameworks) if frameworks else lines.append("- None detected.")
    lines.extend(["", "## Folder Responsibilities", ""])
    folder_entries = [item for item in scan.get("entries", []) if isinstance(item, dict) and item.get("type") == "folder"]
    if folder_entries:
        lines.extend(["| Path | Files | Responsibility |", "|---|---:|---|"])
        for item in folder_entries[:20]:
            lines.append(f"| `{item['path']}` | {item.get('file_count', '')} | {item['responsibility']} |")
        if len(folder_entries) > 20:
            lines.append(f"| more | {len(folder_entries) - 20} | Run `repo_navigation.py focus --query \"<task>\"`. |")
    else:
        lines.append("- No folders detected.")
    lines.extend(["", "## Manifests", ""])
    manifests = scan.get("manifests", [])
    if manifests:
        for item in manifests[:20]:
            lines.append(f"- `{item['path']}` - {item['kind']}")
        if len(manifests) > 20:
            lines.append(f"- {len(manifests) - 20} more; use a focused navigation query before opening more files.")
    else:
        lines.append("- None detected.")
    lines.extend(["", "## Codebase Knowledge Categories", ""])
    categories = scan.get("codebase_categories", [])
    if categories:
        lines.extend(["| Category | Count | Example paths |", "|---|---:|---|"])
        for item in categories:
            examples = ", ".join(f"`{path}`" for path in item.get("paths", [])[:5])
            lines.append(f"| {item['category']} | {item['count']} | {examples or 'None'} |")
    else:
        lines.append("- None detected.")
    graph_edges = len(scan.get("relationships", []))
    lines.extend(["", "## Relationships", ""])
    if graph_edges:
        lines.append(f"- Scan inferred {graph_edges} conservative relationship edge(s).")
    else:
        lines.append("- No import, route, or test relationship edges were inferred.")
    lines.extend(["", "## Skipped", ""])
    skipped = durable_skipped(scan)
    lines.extend(f"- {item}" for item in skipped[:20]) if skipped else lines.append("- None.")
    if len(skipped) > 20:
        lines.append(f"- {len(skipped) - 20} more; use `repo_navigation.py check --format json` for machine-readable status.")
    return "\n".join(lines) + "\n"


def render_technical_context(scan: dict[str, Any]) -> str:
    lines = [
        "# Technical Context",
        "",
        "Facts are deterministic review-layer signals from repository files and commands. Confirm against the referenced files before relying on them.",
        "",
        "## Frameworks And Languages",
        "",
    ]
    frameworks = scan.get("frameworks", [])
    lines.extend(f"- {item}" for item in frameworks) if frameworks else lines.append("- None detected.")
    lines.extend(["", "## Manifests", ""])
    manifests = scan.get("manifests", [])
    if manifests:
        lines.extend(f"- `{item['path']}` - {item['kind']}" for item in manifests)
    else:
        lines.append("- None detected.")
    lines.extend(["", "## Commands", ""])
    commands = scan.get("commands", [])
    if commands:
        for item in commands:
            location = f"`{item['path']}`"
            if item.get("line"):
                location += f":{item['line']}"
            lines.append(f"- {location} - `{item['command']}` ({item['source']})")
    else:
        lines.append("- None detected.")
    lines.extend(["", "## Test And Build Signals", ""])
    test_signals = [
        item
        for item in scan.get("entries", [])
        if isinstance(item, dict) and ("test" in str(item.get("path", "")).lower() or "test" in str(item.get("responsibility", "")).lower())
    ]
    if test_signals:
        for item in test_signals[:40]:
            lines.append(f"- `{item['path']}` - {item['responsibility']}")
    else:
        lines.append("- None detected.")
    return "\n".join(lines) + "\n"


def render_conventions(scan: dict[str, Any]) -> str:
    lines = [
        "# Conventions",
        "",
        "Inferred from deterministic file facts. This file intentionally reports observed facts only, not AI guesses.",
        "",
        "## Observed file extensions",
        "",
        "| Extension | Count |",
        "|---|---:|",
    ]
    for extension, count in scan.get("extension_counts", {}).items():
        lines.append(f"| `{extension}` | {count} |")
    lines.extend(["", "## Observed project folders", ""])
    folders = [item for item in scan.get("entries", []) if isinstance(item, dict) and item.get("type") == "folder"]
    if folders:
        for item in folders:
            lines.append(f"- `{item['path']}` - {item['responsibility']}")
    else:
        lines.append("- None detected.")
    lines.extend(["", "## Observed package and command facts", ""])
    commands = scan.get("commands", [])
    if commands:
        for item in commands[:40]:
            lines.append(f"- `{item['command']}` from `{item['path']}`")
    else:
        lines.append("- None detected.")
    lines.extend(["", "## Observed symbols", ""])
    symbols = scan.get("symbols", [])
    if symbols:
        for item in symbols[:80]:
            lines.append(f"- `{item['path']}` - {item['kind']} `{item['name']}`")
    else:
        lines.append("- None detected.")
    return "\n".join(lines) + "\n"


def handoff_payload(scan: dict[str, Any]) -> dict[str, Any]:
    read_first = recommended_read_order(scan)
    commands = scan.get("commands", []) if isinstance(scan.get("commands"), list) else []
    validation_commands = [
        str(item.get("command", ""))
        for item in commands
        if isinstance(item, dict)
        and any(term in str(item.get("command", "")).lower() for term in ("test", "validate", "lint", "build"))
    ][:12]
    entries = scan.get("entries", []) if isinstance(scan.get("entries"), list) else []
    active_roots = [
        str(item.get("path"))
        for item in entries
        if isinstance(item, dict) and item.get("type") == "folder"
    ][:20]
    purpose = "Project navigation handoff generated from deterministic file, manifest, command, and symbol facts."
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "ok": bool(scan.get("ok")),
        "target": scan.get("target", "."),
        "purpose": purpose,
        "load_first": [
            "docs/project/project-context.md",
            "automations/navigation/WORKFLOW.md",
            "automations/navigation/module.json",
            "automations/navigation/artifacts/maps/HANDOFF.md",
            "automations/navigation/artifacts/maps/NAVIGATION.md",
        ],
        "read_first_files": read_first,
        "active_roots": active_roots,
        "validation_commands": validation_commands,
        "evidence_folders": [
            "automations/*/runs/",
            "evidence/",
            "artifacts/",
        ],
        "generated_maps": [
            "automations/navigation/artifacts/maps/NAVIGATION.md",
            "automations/navigation/artifacts/maps/TECHNICAL_CONTEXT.md",
            "automations/navigation/artifacts/maps/CONVENTIONS.md",
            "automations/navigation/artifacts/maps/PROJECT_CONTEXT_DRAFT.md",
        ],
        "owner_capsules": sorted(owner_capsule_outputs(scan)),
        "tool_only_maps": TOOL_ONLY_NAVIGATION_JSON,
        "avoid_unless_needed": [
            ".git/",
            ".agents/local-ai/cache/",
            ".agents/local-ai/cache/command-output/",
            "node_modules/",
            "bin/",
            "obj/",
            "dist/",
            "build/",
            ".venv/",
            *TOOL_ONLY_NAVIGATION_JSON,
        ],
        "staleness": {
            "status": "fresh",
            "source_file_count": len(scan.get("hashes", {})) if isinstance(scan.get("hashes"), dict) else 0,
            "check_command": "python -B automations/navigation/scripts/update_navigation.py --target . --check",
        },
        "next_command": "python -B automations/navigation/scripts/update_navigation.py --target . --check",
    }


def render_handoff(scan: dict[str, Any]) -> str:
    payload = handoff_payload(scan)
    lines = [
        "# Project Context Handoff",
        "",
        str(payload["purpose"]),
        "",
        "## Load First",
        "",
    ]
    lines.extend(f"- `{item}`" for item in payload["load_first"])
    lines.extend(["", "## Read First Files", ""])
    read_first = payload.get("read_first_files", [])
    lines.extend(f"- `{item}`" for item in read_first) if read_first else lines.append("- No deterministic read order inferred.")
    lines.extend(["", "## Active Roots", ""])
    active_roots = payload.get("active_roots", [])
    lines.extend(f"- `{item}`" for item in active_roots) if active_roots else lines.append("- No folder roots inferred.")
    lines.extend(["", "## Validation Commands", ""])
    validation_commands = payload.get("validation_commands", [])
    lines.extend(f"- `{item}`" for item in validation_commands) if validation_commands else lines.append("- No validation command inferred.")
    lines.extend(["", "## Tool-Only Indexes", "", "Do not load raw JSON into model context; use `repo_navigation.py focus`, `check`, or status commands."])
    lines.extend(f"- `{item}`" for item in payload["tool_only_maps"])
    owner_capsules = payload.get("owner_capsules", [])
    if owner_capsules:
        lines.extend(["", "## Owner Capsules", "", "Read one matching capsule after routing to an owner; do not load all capsules."])
        lines.extend(f"- `{item}`" for item in owner_capsules[:20])
        if len(owner_capsules) > 20:
            lines.append(f"- {len(owner_capsules) - 20} more; use `repo_navigation.py focus --query \"<task>\"`.")
    lines.extend(["", "## Avoid Unless Needed", ""])
    lines.extend(f"- `{item}`" for item in payload["avoid_unless_needed"])
    lines.extend(["", f"Next command: `{payload['next_command']}`", ""])
    return "\n".join(lines)


def owner_slug(owner: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in owner).strip("-") or "owner"


def owner_from_path(path: str) -> tuple[str, str]:
    value = path.replace("\\", "/")
    parts = value.split("/")
    if len(parts) >= 3 and parts[0] == ".agents" and parts[1] == "skills":
        return f"skill:{parts[2]}", f".agents/skills/{parts[2]}"
    if len(parts) >= 3 and parts[0] == "automations" and parts[1] not in {"routing.md", "registry.json"}:
        return f"workflow:{parts[1]}", f"automations/{parts[1]}"
    if value.startswith("docs/"):
        return "docs", "docs"
    if value.startswith(".agents/"):
        return "agent-harness", ".agents"
    return "repo", "."


def owner_capsule_groups(scan: dict[str, Any]) -> list[dict[str, Any]]:
    def owner_validation_command(command: str) -> str:
        text = command.strip()
        if not text:
            return ""
        tokens = text.split()
        lowered = text.lower()
        unsafe_terms = (
            " local-ai ",
            " local_ai",
            "multimodal_benchmark",
            "mtp_benchmark",
            "workflow start",
            "workflow resume",
            "workflow finish",
            "context-evidence",
            "checkpoint",
            "context --name",
        )
        padded = f" {lowered} "
        if any(term in padded for term in unsafe_terms):
            return ""
        if "--write" in tokens or "--output" in tokens or "--run-tests" in tokens:
            return ""
        safe_endings = {"--help", "--check", "--compact", "--json", "--summary"}
        if tokens[-1].startswith("--") and tokens[-1] not in safe_endings:
            return " ".join([*tokens[:-1], "--help"])
        return text

    entries = [item for item in scan.get("entries", []) if isinstance(item, dict) and item.get("type") == "file"]
    grouped: dict[str, dict[str, Any]] = {}
    priority_names = {"SKILL.md", "WORKFLOW.md", "module.json", "instructions.md", "README.md", "AGENTS.md"}
    for entry in entries:
        path = str(entry.get("path", ""))
        if not path:
            continue
        owner, root = owner_from_path(path)
        if owner in {"repo"}:
            continue
        group = grouped.setdefault(
            owner,
            {
                "owner": owner,
                "root": root,
                "paths": [],
                "entry_files": [],
                "validation_commands": [],
            },
        )
        group["paths"].append(path)
        if Path(path).name in priority_names:
            group["entry_files"].append(path)
    commands = scan.get("commands", []) if isinstance(scan.get("commands"), list) else []
    strict_commands = scan.get("strict_read_only_commands", []) if isinstance(scan.get("strict_read_only_commands"), list) else []
    strict_by_owner: dict[str, list[object]] = defaultdict(list)
    for command in strict_commands:
        if not isinstance(command, dict):
            continue
        source = str(command.get("path", ""))
        owner, _root = owner_from_path(source)
        argv = command.get("argv")
        if not (isinstance(argv, list) and argv and all(isinstance(item, str) for item in argv)):
            continue
        safe_command: object = list(argv)
        if owner != "repo" and safe_command and safe_command not in strict_by_owner[owner]:
            strict_by_owner[owner].append(safe_command)
    for group in grouped.values():
        owner = str(group["owner"])
        root = str(group["root"])
        validation = []
        owner_strict_commands = strict_by_owner.get(owner, [])
        if owner_strict_commands:
            for safe_command in owner_strict_commands:
                if safe_command not in validation:
                    validation.append(safe_command)
        else:
            if owner.startswith("skill:"):
                name = owner.split(":", 1)[1]
                validation.append(f"python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/{name} --summary --compact --format json")
            elif owner.startswith("workflow:"):
                name = owner.split(":", 1)[1]
                validation.append(f"python -B .agents/manage.py validate-automations --name {name} --summary --compact --format json")
                validation.append(f"python -B .agents/manage.py workflow smoke --name {name} --dry-run --summary --compact --format json")
            for command in commands:
                if not isinstance(command, dict):
                    continue
                text = str(command.get("command", ""))
                source = str(command.get("path", ""))
                safe_text = owner_validation_command(text)
                if safe_text and (root in source or root in text) and safe_text not in validation:
                    validation.append(safe_text)
        group["entry_files"] = sorted(set(group["entry_files"]))[:8]
        group["paths"] = sorted(set(group["paths"]))
        group["validation_commands"] = validation[:6]
    return sorted(grouped.values(), key=lambda item: str(item.get("owner", "")))


def render_owner_capsule(group: dict[str, Any]) -> str:
    owner = str(group.get("owner", ""))
    root = str(group.get("root", ""))
    entry_files = [str(item) for item in group.get("entry_files", []) if str(item)]
    paths = [str(item) for item in group.get("paths", []) if str(item)]
    validation = [item for item in group.get("validation_commands", []) if item]
    lines = [
        f"# Owner Capsule: {owner}",
        "",
        "Generated from deterministic navigation scan facts. Use this capsule only after routing to this owner.",
        "",
        f"- Owner root: `{root}`",
        f"- File count: {len(paths)}",
        "",
        "## Read First",
        "",
    ]
    lines.extend(f"- `{item}`" for item in entry_files[:8]) if entry_files else lines.append("- No entry files inferred.")
    lines.extend(
        [
            "",
            "## Validation",
            "",
            "Typed entries use canonical JSON argv display for review; JSON argv display is not shell-executable command text.",
            "",
        ]
    )
    lines.extend(
        f"- `{module_command.command_display(item)}`"
        if isinstance(item, list)
        else f"- `{item}`"
        for item in validation[:6]
    ) if validation else lines.append("- No owner validation command inferred.")
    display_paths = [item for item in paths if not item.replace("\\", "/").endswith("/run_self_tests.py")]
    lines.extend(["", "## Path Samples", ""])
    for item in display_paths[:12]:
        lines.append(f"- `{item}`")
    if len(display_paths) > 12:
        lines.append(f"- {len(display_paths) - 12} more; use `repo_navigation.py focus --query \"{owner}\"`.")
    lines.extend(["", "## Rules", "", "- Reopen source files before edits or claims.", "- Raw navigation JSON is tool-only.", ""])
    return "\n".join(lines)


def owner_capsule_outputs(scan: dict[str, Any]) -> dict[str, str]:
    outputs: dict[str, str] = {}
    for group in owner_capsule_groups(scan):
        owner = str(group.get("owner", ""))
        outputs[f"automations/navigation/artifacts/maps/owners/{owner_slug(owner)}.md"] = render_owner_capsule(group)
    return outputs


def build_staleness(scan: dict[str, Any], map_files: list[str] | None = None) -> dict[str, Any]:
    maps = map_files or [
        "automations/navigation/artifacts/maps/NAVIGATION.md",
        "automations/navigation/artifacts/maps/TECHNICAL_CONTEXT.md",
        "automations/navigation/artifacts/maps/CONVENTIONS.md",
        "automations/navigation/artifacts/maps/HANDOFF.md",
        "automations/navigation/artifacts/maps/handoff.json",
        "automations/navigation/artifacts/maps/staleness.json",
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "ok": True,
        "status": "fresh",
        "source_hash_kind": SOURCE_HASH_KIND,
        "source_hashes": scan.get("hashes", {}),
        "map_files": sorted(maps),
        "skipped": durable_skipped(scan),
    }


def source_git_tree_hash(target: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "--no-optional-locks", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=target,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    digest = hashlib.sha256()
    raw_paths = sorted({item for item in completed.stdout.split(b"\0") if item})
    paths: list[str] = []
    for raw_path in raw_paths:
        path = raw_path.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        source = target / path
        if skip_generated_navigation(path) or any(part in IGNORED_DIRS for part in Path(path).parts):
            continue
        try:
            size = source.stat().st_size
        except FileNotFoundError:
            # The index still lists an unstaged deletion. Hash the current
            # working source set so staging the same deletion is cache-stable.
            continue
        except OSError:
            return ""
        if size > MAX_FILE_BYTES or (source.suffix.lower() not in TEXT_SUFFIXES and looks_binary(source)):
            continue
        if "\n" in path or not source.is_file():
            return ""
        paths.append(path)
    filter_attributes = git_path_attributes(target, paths, ("filter",))
    if filter_attributes is None or any(
        values.get("filter") not in {"", "unset", "unspecified"}
        for values in filter_attributes.values()
    ):
        return ""
    try:
        hashed = subprocess.run(
            ["git", "--no-optional-locks", "hash-object", "--stdin-paths"],
            cwd=target,
            check=False,
            input=b"".join(
                path.encode("utf-8", errors="surrogateescape") + b"\n"
                for path in paths
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    object_ids = hashed.stdout.splitlines() if hashed.returncode == 0 else []
    if len(object_ids) != len(paths):
        return ""
    for path, object_id in zip(paths, object_ids):
        digest.update(path.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(object_id)
        digest.update(b"\0")
    return digest.hexdigest()


def build_outputs(target: Path, max_files: int = 5000) -> tuple[dict[str, str], dict[str, Any]]:
    scan = build_scan(target, max_files=max_files)
    outputs = {
        "automations/navigation/artifacts/maps/NAVIGATION.md": render_navigation(scan),
        "automations/navigation/artifacts/maps/TECHNICAL_CONTEXT.md": render_technical_context(scan),
        "automations/navigation/artifacts/maps/CONVENTIONS.md": render_conventions(scan),
        "automations/navigation/artifacts/maps/HANDOFF.md": render_handoff(scan),
        "automations/navigation/artifacts/maps/handoff.json": json.dumps(handoff_payload(scan), indent=2, sort_keys=True) + "\n",
    }
    outputs.update(owner_capsule_outputs(scan))
    staleness_path = "automations/navigation/artifacts/maps/staleness.json"
    staleness = build_staleness(scan, sorted([*outputs, staleness_path]))
    staleness["map_hashes"] = {
        path: hashlib.sha256(content.encode("utf-8")).hexdigest()
        for path, content in sorted(outputs.items())
    }
    git_tree_hash = source_git_tree_hash(target)
    if git_tree_hash:
        staleness["source_git_tree_hash"] = git_tree_hash
        staleness["source_git_tree_kind"] = SOURCE_GIT_TREE_KIND
    outputs["automations/navigation/artifacts/maps/staleness.json"] = json.dumps(
        staleness,
        indent=2,
        sort_keys=True,
    ) + "\n"
    scan["route_quality_warnings"] = entrypoint_quality(scan)
    scan["map_size_budget"] = map_size_budget(outputs, scan)
    return outputs, scan


def write_outputs(target: Path, outputs: dict[str, str]) -> list[str]:
    written: list[str] = []
    expected_owner_files = {
        target / relative
        for relative in outputs
        if relative.startswith("automations/navigation/artifacts/maps/owners/")
    }
    owners_dir = target / "automations/navigation/artifacts/maps/owners"
    if owners_dir.exists():
        for path in owners_dir.glob("*.md"):
            if path not in expected_owner_files:
                path.unlink()
    for relative, text in outputs.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text(encoding="utf-8").replace("\r\n", "\n") == text:
            continue
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(relative)
    return written


def stale_outputs(target: Path, outputs: dict[str, str]) -> list[str]:
    stale: list[str] = []
    expected_owner_files = {
        target / relative
        for relative in outputs
        if relative.startswith("automations/navigation/artifacts/maps/owners/")
    }
    owners_dir = target / "automations/navigation/artifacts/maps/owners"
    if owners_dir.exists():
        for path in owners_dir.glob("*.md"):
            if path not in expected_owner_files:
                stale.append(relpath(target, path))
    for relative, expected in outputs.items():
        path = target / relative
        if not path.exists():
            stale.append(relative)
            continue
        actual = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        if actual != expected:
            stale.append(relative)
    return stale
