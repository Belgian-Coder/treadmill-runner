#!/usr/bin/env python3
"""Build a compact repository orientation brief."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

for _ancestor in Path(__file__).resolve().parents:
    _policy_scripts = _ancestor / ".agents" / "skills" / "skill-manager" / "scripts"
    if _policy_scripts.is_dir():
        sys.path.insert(0, str(_policy_scripts))
        break

from repo_support import repo_policy

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
def configured_output_budgets(root: Path) -> tuple[dict[str, dict[str, int]], str]:
    """Load canonical v2 briefing preferences; explicit CLI arguments still win."""

    policy_root = next(
        (candidate for candidate in (root, *root.parents) if (candidate / ".agents" / "project-policy.json").is_file()),
        root,
    )
    document, issues, _exists = repo_policy.load_project_policy(policy_root)
    if issues:
        raise ValueError("invalid project policy: " + "; ".join(issues))
    briefing = document.get("owner_defaults", {}).get("repo_navigation", {}).get("briefing", {})
    profiles = briefing.get("profiles") if isinstance(briefing, dict) else None
    default_profile = briefing.get("default_profile") if isinstance(briefing, dict) else None
    expected_profiles = {"short", "normal", "deep"}
    if not isinstance(profiles, dict) or set(profiles) != expected_profiles:
        raise ValueError("invalid project policy: repo-navigation briefing profiles are incomplete")
    normalized: dict[str, dict[str, int]] = {}
    for profile in sorted(expected_profiles):
        configured = profiles.get(profile)
        expected_fields = {"max_files", "max_text_files", "item_limit", "read_order_limit", "do_not_open_limit"}
        if not isinstance(configured, dict) or set(configured) != expected_fields:
            raise ValueError(f"invalid project policy: briefing profile {profile} is incomplete")
        if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in configured.values()):
            raise ValueError(f"invalid project policy: briefing profile {profile} values must be positive integers")
        normalized[profile] = {key: int(configured[key]) for key in sorted(expected_fields)}
    if default_profile not in normalized:
        raise ValueError("invalid project policy: briefing default_profile is unsupported")
    return normalized, str(default_profile)
GUIDANCE_PATHS = {
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "README.md",
    "CONTRIBUTING.md",
    "llms.txt",
    "REPO_MEMORY.md",
    ".context-pack/memory.md",
    ".clio/instructions.md",
    ".agents/routing.md",
    "automations/routing.md",
    ".github/copilot-instructions.md",
    ".claude/CLAUDE.md",
}
MANIFEST_NAMES = {
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "compose.yml",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "uv.lock",
    "poetry.lock",
    "Pipfile",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "global.json",
    "Directory.Build.props",
    "Directory.Build.targets",
    "module.json",
}
ENTRYPOINT_NAMES = {
    "manage.py",
    "main.py",
    "app.py",
    "server.py",
    "Program.cs",
    "Startup.cs",
    "index.js",
    "index.ts",
    "vite.config.ts",
    "next.config.js",
}
COMMAND_PATTERN = re.compile(
    r"\b("
    r"python\s+-B\s+\.agents/manage\.py[^\n`]*|"
    r"python\s+-B\s+[^\n`]*|"
    r"npm\s+(?:test|run\s+[A-Za-z0-9:_-]+)|"
    r"pnpm\s+(?:test|run\s+[A-Za-z0-9:_-]+)|"
    r"yarn\s+(?:test|run\s+[A-Za-z0-9:_-]+)|"
    r"dotnet\s+(?:test|build|run)[^\n`]*|"
    r"cargo\s+(?:test|build|check)[^\n`]*|"
    r"go\s+test[^\n`]*"
    r")",
    re.IGNORECASE,
)
SECRET_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "secrets.json",
}
TOOL_SETTING_PATHS = {
    "." + "codex" + "/config.toml",
    "." + "mcp" + ".json",
    "." + "vscode" + "/settings.json",
    "." + "vscode" + "/mcp.json",
    "." + "claude" + "/settings.json",
    "." + "github" + "/copilot/settings.json",
}
TEXT_SUFFIXES = {
    ".cs",
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".rs",
    ".ts",
    ".txt",
    ".toml",
    ".xml",
    ".yaml",
    ".yml",
}


def estimate_tokens(text: str) -> int:
    """Return a coarse prompt-token estimate for bounded reporting."""
    return max(1, (len(text) + 3) // 4)


def require_supported_python() -> None:
    if sys.version_info >= (3, 12):
        return
    current = ".".join(str(part) for part in sys.version_info[:3])
    raise SystemExit(f"Python 3.12+ is required; current interpreter is Python {current}.")


def relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def read_text(path: Path, limit: int = 80_000) -> str:
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


def iter_files(target: Path, max_files: int) -> tuple[list[Path], list[str]]:
    skipped: list[str] = []
    requested_max_files = max_files
    max_files = max(1, min(max_files, MAX_SCAN_FILES))
    if max_files != requested_max_files:
        skipped.append(
            f"file scan limit clamped from {requested_max_files} to {max_files} files"
        )
    if target.is_file():
        return [target], skipped

    files: list[Path] = []
    for current_root, dirnames, filenames in os.walk(target):
        ignored = sorted(name for name in dirnames if name in IGNORED_DIRS)
        for name in ignored:
            skipped.append(f"ignored directory `{relative(target, Path(current_root) / name)}`")
        dirnames[:] = [
            name
            for name in sorted(dirnames, key=str.lower)
            if name not in IGNORED_DIRS and not name.endswith(".egg-info")
        ]
        current = Path(current_root)
        for filename in sorted(filenames, key=str.lower):
            files.append(current / filename)
            if len(files) >= max_files:
                skipped.append(f"file scan capped at {max_files} files")
                return files, skipped
    return files, skipped


def section(identifier: str, title: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": identifier, "title": title, "items": items}


def file_item(root: Path, path: Path, reason: str, **extra: Any) -> dict[str, Any]:
    item = {"path": relative(root, path), "reason": reason}
    item.update(extra)
    return item


def guidance_files(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in files:
        rel = relative(root, path)
        if rel in GUIDANCE_PATHS or path.name in {"AGENTS.md", "README.md", "CONTRIBUTING.md"}:
            items.append(file_item(root, path, "repository guidance or low-context routing"))
    return items[:40]


def manifests(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in files:
        if path.name in MANIFEST_NAMES or path.suffix.lower() in {".csproj", ".sln"}:
            items.append(file_item(root, path, "dependency, build, skill, or workflow manifest"))
    return items[:80]


def entrypoints(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in files:
        rel = relative(root, path)
        if path.name in ENTRYPOINT_NAMES:
            items.append(file_item(root, path, "common application or repository entrypoint"))
        elif rel == ".agents/manage.py":
            items.append(file_item(root, path, "repository command launcher"))
        elif rel.endswith("/START.md") and rel.startswith("automations/"):
            items.append(file_item(root, path, "workflow entrypoint"))
        elif rel.endswith("/SKILL.md") and "/skills/" in rel:
            items.append(file_item(root, path, "skill entrypoint"))
        elif rel.startswith("scripts/") and path.suffix.lower() == ".py":
            items.append(file_item(root, path, "root script entrypoint"))
    return items[:80]


def extract_commands(root: Path, files: list[Path], max_text_files: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    checked = 0
    for path in files:
        if checked >= max_text_files:
            break
        if path.suffix.lower() not in {".md", ".txt", ".toml", ".json", ".yaml", ".yml"}:
            continue
        checked += 1
        for line_number, line in enumerate(read_text(path).splitlines(), start=1):
            match = COMMAND_PATTERN.search(line)
            if not match:
                continue
            command = match.group(1).strip()
            key = (relative(root, path), command)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "path": relative(root, path),
                    "line": line_number,
                    "command": command,
                    "reason": "documented command signal",
                }
            )
            if len(items) >= 60:
                return items
    return items


def dependency_signals(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in manifests(root, files):
        name = Path(str(item["path"])).name
        reason = "manifest signal"
        if name == "package.json":
            reason = "Node package manifest"
        elif name == "pyproject.toml":
            reason = "Python project manifest"
        elif name.startswith("requirements"):
            reason = "Python requirements file"
        elif name in {"Cargo.toml", "go.mod", "pom.xml"}:
            reason = "language package manifest"
        elif name.endswith(".csproj") or name.endswith(".sln"):
            reason = ".NET project manifest"
        items.append({"path": item["path"], "reason": reason})
    if not items:
        items.append({"reason": "no common dependency manifests detected"})
    return items[:80]


def git_state(target: Path) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    skipped: list[str] = []
    checks: list[str] = []
    status_cwd = target if target.is_dir() else target.parent
    pathspec = "." if target.is_dir() else target.name
    try:
        completed = subprocess.run(
            ["git", "-C", str(status_cwd), "status", "--short", "--branch", "--", pathspec],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return [], [f"git status skipped: {exc}"], ["git unavailable"]
    if completed.returncode != 0:
        skipped.append("git status skipped: target is not inside a git worktree or git failed")
        return [], skipped, ["git status failed"]

    checks.append("git status collected")
    items: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        if line.startswith("##"):
            items.append({"branch": line[3:].strip(), "reason": "active branch"})
            continue
        status = line[:2].strip() or "changed"
        path_text = line[3:].strip().replace("\\", "/")
        if " -> " in path_text:
            path_text = path_text.split(" -> ", 1)[1]
        items.append({"path": path_text, "status": status, "reason": "active git change"})
    return items, skipped, checks


def run_git_lines(root: Path, command: list[str], timeout_seconds: int = 10) -> tuple[list[str], str | None]:
    try:
        completed = subprocess.run(
            ["git", *command],
            cwd=str(root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], str(exc)
    if completed.returncode != 0:
        return [], completed.stderr.strip() or "git command failed"
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()], None


def git_history_facts(target: Path, limit: int = 12, since: str = "180 days ago") -> tuple[list[dict[str, Any]], list[str], list[str]]:
    skipped: list[str] = []
    history_cwd = target if target.is_dir() else target.parent
    top_level, error = run_git_lines(history_cwd, ["rev-parse", "--show-toplevel"], timeout_seconds=5)
    if error or not top_level:
        return [], ["git history skipped: target is not inside a git worktree or git failed"], ["git history unavailable"]
    git_root = Path(top_level[0]).resolve()
    try:
        pathspec = target.resolve().relative_to(git_root).as_posix()
    except ValueError:
        pathspec = "."
    if not pathspec:
        pathspec = "."
    first_commit, _ = run_git_lines(git_root, ["log", "--reverse", "--format=%ad", "--date=short", "-1", "--", pathspec])
    latest_commit, _ = run_git_lines(git_root, ["log", "--format=%ad", "--date=short", "-1", "--", pathspec])
    commit_count, _ = run_git_lines(git_root, ["rev-list", "--count", "HEAD", "--", pathspec])
    authors, _ = run_git_lines(git_root, ["shortlog", "-sne", "HEAD", "--", pathspec])
    churn_lines, churn_error = run_git_lines(git_root, ["log", "--format=", "--name-only", f"--since={since}", "--", pathspec])
    fix_lines, fix_error = run_git_lines(
        git_root,
        ["log", "--format=", "--name-only", f"--since={since}", "--grep=fix|bug|hotfix|regression", "-E", "-i", "--", pathspec],
    )
    if churn_error:
        skipped.append(f"git churn skipped: {churn_error}")
        churn_lines = []
    if fix_error:
        skipped.append(f"git bug-magnet scan skipped: {fix_error}")
        fix_lines = []
    churn = Counter(line.replace("\\", "/") for line in churn_lines if line)
    fixes = Counter(line.replace("\\", "/") for line in fix_lines if line)
    high_risk = sorted(
        set(churn).intersection(fixes),
        key=lambda item: (churn[item] + fixes[item], churn[item], fixes[item], item),
        reverse=True,
    )
    items: list[dict[str, Any]] = [
        {
            "kind": "repo-history",
            "first_commit": first_commit[0] if first_commit else "",
            "latest_commit": latest_commit[0] if latest_commit else "",
            "commit_count": commit_count[0] if commit_count else "",
            "reason": "git history span",
        }
    ]
    for path_text, count in churn.most_common(limit):
        items.append({"path": path_text, "kind": "churn-hotspot", "count": count, "reason": f"changed in last {since}"})
    for path_text, count in fixes.most_common(limit):
        items.append({"path": path_text, "kind": "bug-magnet", "count": count, "reason": f"changed in fix/bug commits in last {since}"})
    for path_text in high_risk[:limit]:
        items.append(
            {
                "path": path_text,
                "kind": "high-risk-file",
                "count": churn[path_text] + fixes[path_text],
                "reason": "appears in both churn hotspots and bug/fix history",
            }
        )
    for author in authors[:limit]:
        items.append({"kind": "contributor", "reason": "git shortlog contributor signal", "detail": author})
    return items[: limit * 4], skipped, ["git history collected"]


def codebase_knowledge_categories(root: Path, files: list[Path]) -> list[dict[str, Any]]:
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
        rel = relative(root, path)
        lower = rel.lower()
        name = path.name.lower()
        if name in MANIFEST_NAMES or path.suffix.lower() in {".csproj", ".sln"}:
            categories["stack"].append(rel)
        if "/" not in rel and path.is_file():
            categories["structure"].append(rel)
        if path.name in ENTRYPOINT_NAMES or lower.endswith("/start.md") or lower.endswith("/skill.md"):
            categories["entrypoints"].append(rel)
        if name in {".editorconfig", "eslint.config.js", ".eslintrc", "prettier.config.js", "pyproject.toml"}:
            categories["conventions"].append(rel)
        if name in {".env.example", ".env.template", "docker-compose.yml", "dockerfile"} or ".github/workflows/" in lower:
            categories["integrations"].append(rel)
        if "test" in lower or name in {"pytest.ini", "jest.config.js", "playwright.config.ts", "coverlet.runsettings"}:
            categories["testing"].append(rel)
        if name in SECRET_FILENAMES or lower.endswith((".pem", ".key")) or "security" in lower:
            categories["risks"].append(rel)
    return [
        {"category": key, "paths": sorted(set(values))[:20], "count": len(set(values))}
        for key, values in categories.items()
    ]


def risk_hints(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in files:
        rel = relative(root, path)
        rel_lower = rel.lower()
        if path.name in SECRET_FILENAMES or rel_lower.endswith(".pem") or rel_lower.endswith(".key"):
            items.append(file_item(root, path, "credential-like filename"))
        if rel_lower.startswith((".agents/local-ai/bundle/", ".agents/local-ai/downloads/", ".agents/local-ai/cache/")):
            items.append(file_item(root, path, "ignored local AI runtime, model, download, or cache payload"))
        if rel_lower.endswith((".min.js", ".generated.cs")) or "/generated/" in rel_lower:
            items.append(file_item(root, path, "generated file signal"))
        if rel in TOOL_SETTING_PATHS:
            items.append(file_item(root, path, "committed tool settings path"))
        if path.suffix.lower() in {".ps1", ".psm1", ".psd1", ".sh", ".bash", ".bat", ".cmd", ".zsh", ".fish"}:
            items.append(file_item(root, path, "shell, batch, or PowerShell script requires review"))
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size > MAX_FILE_BYTES:
            items.append(file_item(root, path, "large file", bytes=size))
        elif path.suffix.lower() not in TEXT_SUFFIXES and looks_binary(path):
            items.append(file_item(root, path, "binary file"))
    return items[:80]


def recommended_read_order(
    root: Path,
    mode: str,
    sections: dict[str, list[dict[str, Any]]],
    git_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(path_text: str, reason: str) -> None:
        if not path_text or path_text in seen:
            return
        seen.add(path_text)
        ordered.append({"path": path_text, "reason": reason})

    priority = [
        "AGENTS.md",
        ".agents/routing.md",
        "automations/routing.md",
        "README.md",
    ]
    for path_text in priority:
        if (root / path_text).exists():
            add(path_text, "low-context repository guidance")

    if mode == "changed":
        for item in git_items:
            path_text = str(item.get("path", ""))
            if path_text and path_text != "." and (root / path_text).exists():
                add(path_text, "active git change")

    for item in sections.get("entrypoints", [])[:10]:
        add(str(item.get("path", "")), str(item.get("reason", "entrypoint")))
    for item in sections.get("manifests", [])[:10]:
        add(str(item.get("path", "")), str(item.get("reason", "manifest")))
    return ordered[:30]


def apply_section_budget(sections: dict[str, list[dict[str, Any]]], budget: str, budgets: dict[str, dict[str, int]]) -> dict[str, list[dict[str, Any]]]:
    limits = budgets.get(budget, budgets["normal"])
    item_limit = limits["item_limit"]
    capped: dict[str, list[dict[str, Any]]] = {}
    for section_id, items in sections.items():
        if section_id == "recommended_read_order":
            capped[section_id] = items[: limits.get("read_order_limit", 30)]
        else:
            capped[section_id] = items[:item_limit]
    return capped


def do_not_open_hints(root: Path, skipped: list[str], risk_items: list[dict[str, Any]], *, limit: int = 40) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(path_text: str, reason: str) -> None:
        normalized = path_text.replace("\\", "/").strip("`")
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        hints.append({"path": normalized, "reason": reason})

    for skipped_item in skipped:
        match = re.search(r"`([^`]+)`", skipped_item)
        if match:
            add(match.group(1), "ignored directory skipped by briefing scan")

    for item in risk_items:
        path_text = str(item.get("path", ""))
        reason = str(item.get("reason", "review before opening"))
        if path_text and any(
            marker in reason
            for marker in (
                "credential-like",
                "ignored local AI runtime",
                "large file",
                "binary file",
            )
        ):
            add(path_text, reason)

    return hints[:limit]


def build_report(
    target: Path,
    mode: str,
    max_files: int = 5000,
    max_text_files: int = 250,
    budget: str = "normal",
) -> dict[str, Any]:
    target = target.expanduser().resolve()
    policy_root = target if target.is_dir() else target.parent
    budgets, default_budget = configured_output_budgets(policy_root)
    if budget not in budgets:
        budget = default_budget
    checks: list[str] = []
    skipped: list[str] = []
    if not target.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "tool": TOOL_NAME,
            "ok": False,
            "status": "target not found",
            "summary": "Target path does not exist.",
            "target": str(target),
            "mode": mode,
            "next_file_to_open": None,
            "do_not_open": [],
            "risk_hints": [],
            "sections": [],
            "checks": checks,
            "skipped": skipped,
        }

    requested_max_files = max_files
    max_files = max(1, min(max_files, MAX_SCAN_FILES))
    if max_files != requested_max_files:
        skipped.append(
            f"file scan limit clamped from {requested_max_files} to {max_files} files"
        )
    if budget in budgets:
        max_files = min(max_files, budgets[budget]["max_files"])
        max_text_files = min(max_text_files, budgets[budget]["max_text_files"])
    files, file_skips = iter_files(target, max_files=max_files)
    skipped.extend(file_skips)
    checks.append(f"scanned {len(files)} file(s)")
    git_items, git_skips, git_checks = git_state(target if target.is_dir() else target.parent)
    skipped.extend(git_skips)
    checks.extend(git_checks)
    history_items, history_skips, history_checks = git_history_facts(target if target.is_dir() else target.parent)
    skipped.extend(history_skips)
    checks.extend(history_checks)

    by_id = {
        "guidance": guidance_files(target if target.is_dir() else target.parent, files),
        "entrypoints": entrypoints(target if target.is_dir() else target.parent, files),
        "manifests": manifests(target if target.is_dir() else target.parent, files),
        "commands": extract_commands(target if target.is_dir() else target.parent, files, max_text_files),
        "dependency_signals": dependency_signals(target if target.is_dir() else target.parent, files),
        "knowledge_categories": codebase_knowledge_categories(target if target.is_dir() else target.parent, files),
        "git_history": history_items,
        "active_git_state": git_items,
        "risk_hints": risk_hints(target if target.is_dir() else target.parent, files),
    }
    effective_mode = "changed" if mode == "resume" else "brief" if mode == "new-repo" else mode
    by_id["recommended_read_order"] = recommended_read_order(
        target if target.is_dir() else target.parent,
        effective_mode,
        by_id,
        git_items,
    )
    raw_by_id = {section_id: list(items) for section_id, items in by_id.items()}
    raw_counts = {section_id: len(items) for section_id, items in raw_by_id.items()}
    by_id = apply_section_budget(by_id, budget, budgets)
    next_file_to_open = by_id["recommended_read_order"][0] if by_id["recommended_read_order"] else None
    do_not_open = do_not_open_hints(
        target if target.is_dir() else target.parent,
        skipped,
        raw_by_id["risk_hints"],
        limit=budgets.get(budget, budgets["normal"]).get("do_not_open_limit", 40),
    )
    high_context_count = raw_counts["guidance"] + raw_counts["manifests"] + raw_counts["entrypoints"]
    warnings: list[str] = []
    if high_context_count > budgets.get(budget, budgets["normal"])["item_limit"] * 3:
        warnings.append(
            f"brief found {high_context_count} high-context candidates; use recommended read order before opening broad folders"
        )
    capped_sections = [
        f"{section_id}: {raw_counts[section_id]}->{len(items)}"
        for section_id, items in by_id.items()
        if raw_counts.get(section_id, 0) > len(items)
    ]
    if capped_sections:
        warnings.append(f"{budget} budget capped section output ({', '.join(capped_sections)})")
    sections = [
        section("guidance", "Guidance Files", by_id["guidance"]),
        section("entrypoints", "Likely Entrypoints", by_id["entrypoints"]),
        section("manifests", "Manifests", by_id["manifests"]),
        section("commands", "Commands", by_id["commands"]),
        section("dependency_signals", "Dependency Signals", by_id["dependency_signals"]),
        section("knowledge_categories", "Codebase Knowledge Categories", by_id["knowledge_categories"]),
        section("git_history", "Git History Signals", by_id["git_history"]),
        section("active_git_state", "Active Git State", by_id["active_git_state"]),
        section("risk_hints", "Risk Hints", by_id["risk_hints"]),
        section("recommended_read_order", "Recommended Read Order", by_id["recommended_read_order"]),
    ]
    total_items = sum(len(value) for value in by_id.values())
    status = "ok" if total_items else "empty"
    summary = (
        f"Found {len(files)} scanned file(s), {raw_counts['guidance']} guidance file(s), "
        f"{raw_counts['entrypoints']} entrypoint(s), {raw_counts['manifests']} manifest(s), "
        f"and {raw_counts['risk_hints']} risk hint(s)."
    )
    if raw_counts["git_history"]:
        summary += f" Git history signal(s): {raw_counts['git_history']}."
    if mode == "changed":
        changed = [item for item in git_items if item.get("path")]
        summary += f" Active git changed path(s): {len(changed)}."
    if mode == "new-repo":
        summary += " New-repo mode emphasizes setup, commands, risks, and read order."
    if mode == "resume":
        changed = [item for item in git_items if item.get("path")]
        summary += f" Resume mode emphasizes branch, dirty files, failed checks, and next actions; dirty path(s): {len(changed)}."

    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "ok": True,
        "status": status,
        "summary": summary,
        "target": str(target),
        "mode": mode,
        "budget": budget,
        "budget_limits": budgets.get(budget, budgets["normal"]),
        "next_file_to_open": next_file_to_open,
        "do_not_open": do_not_open,
        "risk_hints": by_id["risk_hints"],
        "sections": sections,
        "warnings": warnings,
        "checks": checks,
        "skipped": sorted(set(skipped)),
    }
    report["estimated_prompt_tokens"] = estimate_tokens(json.dumps(report, sort_keys=True))
    return report


def render_item(item: dict[str, Any]) -> str:
    if "path" in item:
        location = f"`{item['path']}`"
        if item.get("line"):
            location = f"{location}:{item['line']}"
        if item.get("command"):
            return f"- {location} - `{item['command']}` ({item.get('reason', 'command')})"
        extras = []
        if item.get("status"):
            extras.append(f"status: {item['status']}")
        if item.get("bytes"):
            extras.append(f"bytes: {item['bytes']}")
        suffix = f" ({'; '.join(extras)})" if extras else ""
        return f"- {location} - {item.get('reason', 'signal')}{suffix}"
    if "branch" in item:
        return f"- Branch: `{item['branch']}` - {item.get('reason', 'active branch')}"
    return "- " + "; ".join(f"{key}: {value}" for key, value in item.items())


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Repository Context Brief",
        "",
        f"- Tool: `{report['tool']}`",
        f"- Target: `{report['target']}`",
        f"- Mode: `{report['mode']}`",
        f"- Budget: `{report.get('budget', 'normal')}`",
        f"- Estimated prompt tokens: `{report.get('estimated_prompt_tokens', 'unknown')}`",
        f"- Status: {report['status']}",
        f"- Summary: {report['summary']}",
    ]
    warnings = report.get("warnings", [])
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in warnings)
    lines.extend(["", "## Sections", ""])
    for section_data in report["sections"]:
        lines.extend(["", f"### {section_data['title']}", ""])
        items = section_data.get("items", [])
        if items:
            lines.extend(render_item(item) for item in items)
        else:
            lines.append("- None detected.")
    lines.extend(["", "## Checks", ""])
    checks = report.get("checks", [])
    lines.extend(f"- {item}" for item in checks) if checks else lines.append("- None.")
    lines.extend(["", "## Skipped", ""])
    skipped = report.get("skipped", [])
    lines.extend(f"- {item}" for item in skipped) if skipped else lines.append("- None.")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="read repository or subfolder to brief")
    parser.add_argument("--mode", choices=("brief", "changed", "new-repo", "resume"), default="brief")
    parser.add_argument("--budget", choices=("short", "normal", "deep"), default="normal")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    parser.add_argument("--output", help="write optional report output path; omit for stdout-only read-only reporting")
    parser.add_argument("--max-files", type=int, default=5000)
    parser.add_argument("--max-text-files", type=int, default=250)
    return parser


def main() -> int:
    require_supported_python()
    args = build_parser().parse_args()
    report = build_report(
        Path(args.target),
        mode=args.mode,
        max_files=args.max_files,
        max_text_files=args.max_text_files,
        budget=args.budget,
    )
    if args.output_format == "json":
        output = json.dumps(report, indent=2, sort_keys=True)
    else:
        output = render_markdown(report)

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output + "\n", encoding="utf-8", newline="\n")
        print(f"Wrote {args.output_format} repository brief: {output_path}")
    else:
        print(output)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
