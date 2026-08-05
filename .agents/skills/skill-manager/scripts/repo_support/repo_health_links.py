#!/usr/bin/env python3
"""Repository link and command-reference checks for repo health."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from repo_support import repo_common as repo

MANAGE_COMMAND_RE = re.compile(r"(?:python\s+-B\s+)?\.agents[\\/]+manage\.py\s+(?P<command>[a-z0-9-]+)\b")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\((?P<target>[^)#]+)(?:#[^)]+)?\)")
DOC_LINK_SCAN_ROOTS = ("docs",)
DOC_LINK_ENTRYPOINTS = ("docs/start-here.md",)
DOC_MAP_PATH = "docs/reference/documentation-map.md"
WORKFLOW_PROMPT_DOC = "docs/workflow/using-workflows.md"
MANAGE_COMMAND_FALLBACKS = {
    "analyze-location",
    "attachment-route",
    "attest-skill",
    "audit-candidate-source",
    "audit-skill-determinism",
    "benchmark",
    "changed-evidence",
    "check",
    "check-additions",
    "check-changed",
    "check-repo-health",
    "claude-adapter-budget",
    "commands",
    "commit-readiness",
    "compare-skill",
    "credential-doctor",
    "dashboard",
    "eval-skill",
    "eval-workflow",
    "evidence-index",
    "explain-route",
    "finish",
    "finish",
    "format-json",
    "fresh-clone-smoke",
    "index-workflow-runs",
    "install-harness",
    "link-skills",
    "local-ai",
    "measure-skill-budget",
    "new",
    "new-skill-checklist",
    "release-evidence",
    "resume-work",
    "review",
    "review-skill",
    "route",
    "scorecard-workflows",
    "setup",
    "status",
    "skill",
    "skill-inventory",
    "smoke-workflows",
    "sync",
    "sync-automation-routing",
    "sync-claude-skills",
    "sync-instructions",
    "sync-skill-routing",
    "triage-candidates",
    "upgrade-skill",
    "validate",
    "validate-agent-compatibility",
    "validate-automations",
    "what-now",
    "which-skill",
    "workflow",
}


def available_manage_commands(root: Path) -> set[str]:
    launcher = root / ".agents" / "manage.py"
    if not launcher.exists():
        return set(MANAGE_COMMAND_FALLBACKS)
    try:
        completed = subprocess.run(
            ["python", "-B", str(launcher), "--help"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set(MANAGE_COMMAND_FALLBACKS)
    if completed.returncode != 0:
        return set(MANAGE_COMMAND_FALLBACKS)
    match = re.search(r"\{([^}]+)\}", completed.stdout)
    if not match:
        return set(MANAGE_COMMAND_FALLBACKS)
    commands = {item.strip() for item in match.group(1).split(",") if item.strip()}
    return commands | set(MANAGE_COMMAND_FALLBACKS)


def active_command_reference_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for rel in ("AGENTS.md", "README.md"):
        path = root / rel
        if path.exists():
            paths.append(path)
    for scan_root in DOC_LINK_SCAN_ROOTS:
        folder = root / scan_root
        if folder.exists():
            paths.extend(
                path
                for path in folder.rglob("*.md")
                if path.is_file()
                and not repo.is_installed_consumer_generated_path(root, repo.relative(root, path))
                and not repo.is_installed_consumer_owned_path(root, repo.relative(root, path))
            )
    automations = root / "automations"
    if automations.exists():
        for folder in sorted(automations.iterdir(), key=lambda item: item.name.lower()):
            if not folder.is_dir():
                continue
            for name in ("WORKFLOW.md", "instructions.md", "module.json"):
                path = folder / name
                if path.exists():
                    paths.append(path)
    return sorted(set(paths), key=lambda item: repo.relative(root, item))


def manage_command_reference_errors(root: Path) -> list[str]:
    commands = available_manage_commands(root)
    errors: list[str] = []
    for path in active_command_reference_files(root):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for match in MANAGE_COMMAND_RE.finditer(text):
            command = match.group("command")
            if command not in commands:
                errors.append(
                    f"{repo.relative(root, path)} references unsupported manage.py command `{command}`."
                )
    return sorted(set(errors))


def markdown_link_targets(root: Path, path: Path) -> list[Path]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    targets: list[Path] = []
    for match in MARKDOWN_LINK_RE.finditer(text):
        raw = match.group("target").strip()
        if not raw or re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.IGNORECASE):
            continue
        if raw.startswith("#"):
            continue
        candidate = (path.parent / raw).resolve(strict=False)
        if candidate.is_dir():
            candidate = candidate / "README.md"
        if candidate.suffix.lower() != ".md":
            continue
        try:
            candidate.relative_to(root.resolve(strict=False))
        except ValueError:
            continue
        targets.append(candidate)
    return targets


def root_docs_link_errors(root: Path) -> list[str]:
    docs_root = root / "docs"
    if not docs_root.exists():
        return []
    docs = {
        path.resolve(strict=False)
        for path in docs_root.rglob("*.md")
        if path.is_file()
        and not repo.is_installed_consumer_generated_path(root, repo.relative(root, path))
        and not repo.is_installed_consumer_owned_path(root, repo.relative(root, path))
    }
    if not docs:
        return []
    reachable: set[Path] = set()
    pending = [
        (root / rel).resolve(strict=False)
        for rel in DOC_LINK_ENTRYPOINTS
        if (root / rel).exists()
    ]
    while pending:
        current = pending.pop()
        if current in reachable or current not in docs:
            continue
        reachable.add(current)
        for target in markdown_link_targets(root, current):
            if target in docs and target not in reachable:
                pending.append(target)
    missing = sorted(docs - reachable, key=lambda item: repo.relative(root, item))
    return [
        f"{repo.relative(root, path)} is not reachable from docs/start-here.md links."
        for path in missing
    ]


def documentation_map_errors(root: Path) -> list[str]:
    docs_root = root / "docs"
    if not docs_root.exists():
        return []
    docs = {
        path.resolve(strict=False)
        for path in docs_root.rglob("*.md")
        if path.is_file()
        and not repo.is_installed_consumer_generated_path(root, repo.relative(root, path))
        and not repo.is_installed_consumer_owned_path(root, repo.relative(root, path))
    }
    if not docs:
        return []
    map_path = root / DOC_MAP_PATH
    if not map_path.exists():
        return [f"{DOC_MAP_PATH} is missing; root docs must stay mapped."]
    linked = {
        target.resolve(strict=False)
        for target in markdown_link_targets(root, map_path)
        if target.exists()
    }
    covered = linked | {map_path.resolve(strict=False)}
    missing = sorted(docs - covered, key=lambda item: repo.relative(root, item))
    return [
        f"{repo.relative(root, path)} is not listed in {DOC_MAP_PATH}."
        for path in missing
    ]


def workflow_prompt_doc_errors(root: Path) -> list[str]:
    path = root / WORKFLOW_PROMPT_DOC
    if not path.exists():
        return [f"{WORKFLOW_PROMPT_DOC} is missing; workflow users need copyable start/resume prompts."]
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    errors: list[str] = []
    if "## Copyable Prompts" not in text:
        errors.append(f"{WORKFLOW_PROMPT_DOC} is missing ## Copyable Prompts.")
    for label in (
        "Start a user story",
        "Start a bug investigation",
        "Resume in a new chat",
        "Recover after interruption",
        "Review a plan",
        "Finish a run",
    ):
        pattern = rf"{re.escape(label)}:\s*\n\s*```text\s+.+?\s*```"
        if not re.search(pattern, text, flags=re.DOTALL):
            errors.append(f"{WORKFLOW_PROMPT_DOC} is missing a copyable `{label}` text prompt.")
    for command in ("workflow start", "workflow resume", "workflow recover", "workflow finish"):
        if command not in text:
            errors.append(f"{WORKFLOW_PROMPT_DOC} does not reference `{command}`.")
    start_here = root / "docs" / "start-here.md"
    if start_here.exists():
        start_text = start_here.read_text(encoding="utf-8-sig", errors="replace")
        if "workflow/using-workflows.md" not in start_text and "workflow/using-workflows" not in start_text:
            errors.append("docs/start-here.md must link to docs/workflow/using-workflows.md.")
    return sorted(set(errors))
    "portable-tools",
