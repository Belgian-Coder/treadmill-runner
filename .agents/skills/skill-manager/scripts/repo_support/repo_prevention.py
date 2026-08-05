"""Preventive validation helpers for clean-room and command-surface checks."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from repo_support import repo_common as repo
from repo_support import repo_cli_parser
from repo_support import repo_public_commands


MANAGE_COMMAND_RE = re.compile(
    r"(?:python(?:\.exe)?\s+-B\s+)?\.agents[\\/]+manage\.py\s+(?P<args>[^\n`]+)"
)
WORKFLOW_PLAN_CHECK_ALLOWED_FLAGS = {"--name", "--run-id", "--template", "--plan", "--format"}
DOC_COMMAND_PARSE_SKIP_MARKERS = ("[", "]", "<", ">", "|", "&&", "$(", "%", "...")
RAW_CONTEXT_JSON_NAMES = (
    "automations/navigation/artifacts/maps/project-map.json",
    "automations/navigation/artifacts/maps/code-graph.json",
    "automations/navigation/artifacts/maps/handoff.json",
    "automations/navigation/artifacts/maps/staleness.json",
    "project-map.json",
    "code-graph.json",
    "handoff.json",
    "staleness.json",
)
RAW_CONTEXT_READ_RE = re.compile(
    r"\b(read|load|open|inspect|summari[sz]e|use|review)\b.*\b(project-map|code-graph|handoff|staleness)\.json\b",
    re.IGNORECASE,
)
RAW_CONTEXT_SAFE_MARKERS = (
    "tool-only",
    "do not load",
    "do not read",
    "do not open",
    "never load",
    "never read",
    "never open",
    "skip generated",
    "generated from",
    "freshness index",
    "inside deterministic commands",
    "inside the tool",
)


def _timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")


def _completed_to_row(
    name: str,
    command: list[str],
    completed: subprocess.CompletedProcess[str],
    log_path: Path | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": name,
        "ok": completed.returncode == 0,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "command": command,
        "output_tail": (completed.stdout or "")[-4000:],
    }
    if log_path is not None:
        row["log_path"] = str(log_path)
    return row


def _run_command(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    logs_dir: Path | None = None,
    timeout_seconds: int = 900,
    runner=subprocess.run,
) -> dict[str, Any]:
    try:
        completed = runner(
            command,
            cwd=cwd,
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if not isinstance(output, str):
            output = ""
        log_path = None
        if logs_dir is not None:
            logs_dir.mkdir(parents=True, exist_ok=True)
            log_path = logs_dir / f"{name}.log"
            log_path.write_text(output, encoding="utf-8", newline="\n")
        return {
            "name": name,
            "ok": False,
            "status": "failed",
            "returncode": None,
            "command": command,
            "output_tail": output[-4000:],
            "issue": f"timed out after {timeout_seconds}s",
            **({"log_path": str(log_path)} if log_path else {}),
        }
    except OSError as exc:
        return {
            "name": name,
            "ok": False,
            "status": "failed",
            "returncode": None,
            "command": command,
            "output_tail": "",
            "issue": str(exc),
        }

    log_path = None
    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{name}.log"
        log_path.write_text(completed.stdout or "", encoding="utf-8", newline="\n")
    return _completed_to_row(name, command, completed, log_path=log_path)


def _isolated_env(base_dir: Path) -> dict[str, str]:
    user_home = base_dir / "user-profile"
    temp_dir = base_dir / "tmp"
    npm_cache = base_dir / "npm-cache"
    playwright_cache = base_dir / "ms-playwright"
    for path in (user_home, temp_dir, npm_cache, playwright_cache):
        path.mkdir(parents=True, exist_ok=True)
    env = repo.child_env()
    env.update(
        {
            "HOME": str(user_home),
            "USERPROFILE": str(user_home),
            "TMP": str(temp_dir),
            "TEMP": str(temp_dir),
            "npm_config_cache": str(npm_cache),
            "PLAYWRIGHT_BROWSERS_PATH": str(playwright_cache),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return env


def _default_clean_room_root() -> Path:
    d_root = Path("D:/AgentValidation/skills-repo")
    if d_root.drive:
        return d_root
    return Path.cwd() / "validation-clean-room"


def _origin_url(root: Path, runner=subprocess.run) -> str:
    try:
        completed = runner(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _clone_sources(root: Path, source: str, runner=subprocess.run) -> list[tuple[str, str]]:
    if source == "local":
        return [("local", str(root))]
    if source == "origin":
        origin = _origin_url(root, runner=runner)
        return [("origin", origin or str(root))]
    origin = _origin_url(root, runner=runner)
    result: list[tuple[str, str]] = []
    if origin:
        result.append(("origin", origin))
    result.append(("local", str(root)))
    return result


def _clean_room_commands(quick: bool) -> list[tuple[str, list[str], int]]:
    manage = [sys.executable, "-B", ".agents/manage.py"]
    commands: list[tuple[str, list[str], int]] = [
        ("setup-check", [*manage, "setup", "--check", "--no-link-skills"], 300),
        ("environment-preflight", [*manage, "environment-preflight", "--summary", "--compact", "--format", "json"], 120),
        ("command-docs-smoke", [*manage, "command-docs-smoke", "--summary", "--compact", "--format", "json"], 180),
        ("format-json-check", [*manage, "format-json", "--check"], 180),
        ("check-additions", [*manage, "check-additions"], 300),
        ("sync-check", [*manage, "sync", "--check"], 300),
        ("workflow-template-gate-check", [*manage, "workflow", "template", "gate-check", "--all", "--format", "json"], 240),
        ("validate-agent-compatibility", [*manage, "validate-agent-compatibility", "--format", "json"], 300),
    ]
    if quick:
        commands.append(("check", [*manage, "check"], 900))
    else:
        commands.extend(
            [
                ("validate-deep", [*manage, "validate", "--deep"], 1800),
                ("workflow-scorecard", [*manage, "workflow", "scorecard", "--all", "--summary", "--compact", "--format", "json"], 900),
                ("workflow-smoke", [*manage, "workflow", "smoke", "--all", "--summary", "--compact", "--format", "json"], 900),
                ("workflow-integration-check", [*manage, "workflow", "integration-check", "--format", "json"], 300),
                ("check", [*manage, "check"], 900),
                ("finish", [*manage, "finish", "--summary", "--compact", "--format", "json"], 900),
            ]
        )
    return commands


def clean_room_validate_report(
    root: Path,
    *,
    work_dir: Path | None = None,
    source: str = "auto",
    keep: bool = True,
    quick: bool = False,
    runner=subprocess.run,
) -> dict[str, Any]:
    git = shutil.which("git")
    if not git:
        return {
            "schema_version": 1,
            "tool": "skill-manager.clean-room-validate",
            "ok": False,
            "status": "failed",
            "issues": ["git was not found on PATH"],
            "checks": [],
        }
    base = (work_dir.expanduser().resolve() if work_dir else _default_clean_room_root() / f"clean-room-{_timestamp()}")
    repo_dir = base / "repo"
    evidence_dir = base / "evidence"
    logs_dir = evidence_dir / "logs"
    for path in (base, evidence_dir, logs_dir):
        path.mkdir(parents=True, exist_ok=True)
    env = _isolated_env(base)
    checks: list[dict[str, Any]] = []
    issues: list[str] = []
    clone_source = ""
    clone_kind = ""
    if repo_dir.exists():
        issues.append(f"clean-room repo target already exists: {repo_dir}")
    else:
        for kind, source_value in _clone_sources(root, source, runner=runner):
            if not source_value:
                continue
            command = [git, "-c", "core.longpaths=true", "clone", "--no-local", source_value, str(repo_dir)]
            if kind == "origin":
                command = [git, "-c", "core.longpaths=true", "clone", "--depth", "1", source_value, str(repo_dir)]
            clone_check = _run_command(
                f"git-clone-{kind}",
                command,
                cwd=root,
                env=env,
                logs_dir=logs_dir,
                timeout_seconds=300,
                runner=runner,
            )
            checks.append(clone_check)
            if clone_check.get("ok"):
                clone_source = source_value
                clone_kind = kind
                break
            if repo_dir.exists():
                shutil.rmtree(repo_dir, ignore_errors=True)
        if not clone_source:
            issues.append("clean-room clone failed for every configured source")
    if not issues:
        checks.append(
            _run_command(
                "git-longpaths",
                [git, "config", "core.longpaths", "true"],
                cwd=repo_dir,
                env=env,
                logs_dir=logs_dir,
                timeout_seconds=30,
                runner=runner,
            )
        )
    command_results: list[dict[str, Any]] = []
    if not issues:
        for name, command, timeout_seconds in _clean_room_commands(quick):
            row = _run_command(
                name,
                command,
                cwd=repo_dir,
                env=env,
                logs_dir=logs_dir,
                timeout_seconds=timeout_seconds,
                runner=runner,
            )
            command_results.append(row)
            checks.append(row)
            if not row.get("ok"):
                issues.append(f"clean-room command failed: {name}")
                break
    head = ""
    branch = ""
    status = ""
    if repo_dir.exists():
        for label, command in (
            ("head", [git, "rev-parse", "HEAD"]),
            ("branch", [git, "branch", "--show-current"]),
            ("status", [git, "status", "--short", "--branch"]),
        ):
            row = _run_command(
                f"git-{label}",
                command,
                cwd=repo_dir,
                env=env,
                logs_dir=logs_dir,
                timeout_seconds=30,
                runner=runner,
            )
            output = str(row.get("output_tail") or "").strip()
            if label == "head":
                head = output.splitlines()[-1] if output else ""
            elif label == "branch":
                branch = output.splitlines()[-1] if output else ""
            else:
                status = output
    clone_state = {
        "schema_version": 1,
        "tool": "skill-manager.clean-room-state",
        "source": source,
        "clone_kind": clone_kind,
        "clone_source": clone_source,
        "repo_dir": str(repo_dir),
        "evidence_dir": str(evidence_dir),
        "head": head,
        "branch": branch,
        "status": status,
        "environment": {
            "HOME": env.get("HOME", ""),
            "USERPROFILE": env.get("USERPROFILE", ""),
            "TMP": env.get("TMP", ""),
            "TEMP": env.get("TEMP", ""),
            "npm_config_cache": env.get("npm_config_cache", ""),
            "PLAYWRIGHT_BROWSERS_PATH": env.get("PLAYWRIGHT_BROWSERS_PATH", ""),
        },
    }
    (evidence_dir / "clone-state.json").write_text(
        json.dumps(clone_state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (evidence_dir / "command-results.json").write_text(
        json.dumps(command_results, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if not keep and base.exists():
        shutil.rmtree(base, ignore_errors=True)
    return {
        "schema_version": 1,
        "tool": "skill-manager.clean-room-validate",
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "mode": "quick" if quick else "full",
        "source": source,
        "clone_kind": clone_kind,
        "root": str(root),
        "work_dir": str(base),
        "repo_dir": str(repo_dir),
        "evidence_dir": str(evidence_dir),
        "checks": checks,
        "issues": issues,
        "skipped": [] if keep else ["clean-room work directory was removed"],
        "next_command": "inspect evidence/command-results.json" if issues else "none",
    }


def tool_status(name: str, command: str, *, required: bool = False) -> dict[str, Any]:
    path = shutil.which(command)
    return {
        "name": name,
        "command": command,
        "required": required,
        "available": bool(path),
        "status": "available" if path else ("missing-required" if required else "missing-optional"),
        "path": path or "",
    }


def environment_preflight_report(root: Path) -> dict[str, Any]:
    tools = [
        tool_status("Python", Path(sys.executable).name, required=True),
        tool_status("git", "git", required=True),
        tool_status("ripgrep", "rg"),
        tool_status("Node.js", "node"),
        tool_status("npm", "npm"),
        tool_status("npx", "npx"),
        tool_status(".NET SDK", "dotnet"),
        tool_status("Mermaid CLI", "mmdc"),
    ]
    credentials = [
        {"name": "Azure DevOps PAT", "env": "AZURE_DEVOPS_PAT", "configured": bool(os.environ.get("AZURE_DEVOPS_PAT"))},
        {"name": "SonarQube token", "env": "SONAR_TOKEN", "configured": bool(os.environ.get("SONAR_TOKEN"))},
    ]
    paths = {
        "root": str(root),
        "default_clean_room_root": str(_default_clean_room_root()),
        "default_clean_room_root_exists": _default_clean_room_root().exists(),
        "is_d_drive_default": str(_default_clean_room_root()).replace("\\", "/").lower().startswith("d:/"),
    }
    required_missing = [row["name"] for row in tools if row["required"] and not row["available"]]
    optional_missing = [row["name"] for row in tools if not row["required"] and not row["available"]]
    return {
        "schema_version": 1,
        "tool": "skill-manager.environment-preflight",
        "ok": not required_missing,
        "status": "passed" if not required_missing else "failed",
        "tools": tools,
        "credentials": credentials,
        "paths": paths,
        "summary": {
            "required_missing": len(required_missing),
            "optional_missing": len(optional_missing),
            "credential_missing": sum(1 for row in credentials if not row["configured"]),
        },
        "issues": [f"required tool missing: {name}" for name in required_missing],
        "skipped": [f"optional tool missing: {name}" for name in optional_missing],
    }


def _active_docs(root: Path) -> list[Path]:
    paths = [root / "AGENTS.md", root / "README.md"]
    for folder in (root / "docs", root / "automations", root / ".agents" / "skills"):
        if folder.exists():
            paths.extend(path for path in folder.rglob("*.md") if path.is_file())
            paths.extend(path for path in folder.rglob("module.json") if path.is_file())
    return sorted({path for path in paths if path.exists()}, key=lambda item: repo.relative(root, item))


def _strip_shell_prefix(line: str) -> str:
    text = line.strip()
    if text.startswith(("$ ", "> ")):
        text = text[2:].strip()
    return text


def _extract_manage_commands(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _active_docs(root):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for index, line in enumerate(text.splitlines(), start=1):
            if ".agents/manage.py" not in line and ".agents\\manage.py" not in line:
                continue
            cleaned = _strip_shell_prefix(line.strip().strip("`"))
            match = MANAGE_COMMAND_RE.search(cleaned)
            if not match:
                continue
            args = match.group("args").strip()
            rows.append(
                {
                    "path": repo.relative(root, path),
                    "line": index,
                    "args": args,
                    "text": cleaned,
                }
            )
    return rows


def _raw_context_json_issues(root: Path) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for path in _active_docs(root):
        if path.suffix.lower() != ".md":
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for index, line in enumerate(text.splitlines(), start=1):
            normalized = line.replace("\\", "/")
            lowered = normalized.lower()
            if not any(name in lowered for name in RAW_CONTEXT_JSON_NAMES):
                continue
            if any(marker in lowered for marker in RAW_CONTEXT_SAFE_MARKERS):
                continue
            if not RAW_CONTEXT_READ_RE.search(normalized):
                continue
            issues.append(
                {
                    "path": repo.relative(root, path),
                    "line": index,
                    "command": normalized.strip(),
                    "issue": "raw generated navigation JSON is tool-only and must not be loaded as agent context",
                    "fix": "route agents to HANDOFF.md, NAVIGATION.md, or a compact manage.py packet instead",
                }
            )
    return issues


def _flags(args: str) -> set[str]:
    return {part for part in re.split(r"\s+", args) if part.startswith("--")}


def _parseable_documented_command(args: str) -> bool:
    return not any(marker in args for marker in DOC_COMMAND_PARSE_SKIP_MARKERS)


def _parse_checked_source(path: object) -> bool:
    normalized = str(path).replace("\\", "/")
    parts = normalized.split("/")
    return (
        normalized.endswith(".md")
        and not normalized.startswith("automations/navigation/artifacts/maps/")
        and not ("automations" in parts and "runs" in parts)
    )


def _parse_manage_command_args(parser: argparse.ArgumentParser, args: str) -> str:
    try:
        parts = shlex.split(args, posix=True)
    except ValueError as exc:
        return f"could not tokenize documented manage.py example: {exc}"
    if not parts:
        return "documented manage.py example has no command arguments"
    normalized = repo_public_commands.normalize_public_commands(parts)
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            parser.parse_args(normalized)
    except SystemExit as exc:
        if exc.code == 0:
            return ""
        detail = (stderr.getvalue() or stdout.getvalue()).strip().splitlines()
        tail = detail[-1] if detail else f"argparse exited with {exc.code}"
        return f"documented manage.py example does not parse: {tail}"
    return ""


def command_docs_smoke_report(root: Path) -> dict[str, Any]:
    rows = _extract_manage_commands(root)
    issues: list[dict[str, Any]] = _raw_context_json_issues(root)
    parser = repo_cli_parser.build_parser()
    parse_checked_count = 0
    parse_skipped_count = 0
    for row in rows:
        args = str(row["args"])
        if _parse_checked_source(row.get("path")) and _parseable_documented_command(args):
            parse_checked_count += 1
            parse_issue = _parse_manage_command_args(parser, args)
            if parse_issue:
                issues.append(
                    {
                        "path": row["path"],
                        "line": row["line"],
                        "command": row["text"],
                        "issue": parse_issue,
                        "fix": "update the documented example or the launcher parser so the public command shape is valid",
                    }
                )
        elif _parse_checked_source(row.get("path")):
            parse_skipped_count += 1
        parts = re.split(r"\s+", args)
        if len(parts) >= 2 and parts[0] == "workflow" and parts[1] == "plan-check":
            unsupported = sorted(_flags(args) - WORKFLOW_PLAN_CHECK_ALLOWED_FLAGS)
            if unsupported:
                issues.append(
                    {
                        "path": row["path"],
                        "line": row["line"],
                        "command": row["text"],
                        "issue": f"workflow plan-check example uses unsupported flag(s): {', '.join(unsupported)}",
                        "fix": "use workflow template resolve --profile lean for lean templates; plan-check does not select profiles",
                    }
                )
    return {
        "schema_version": 1,
        "tool": "skill-manager.command-docs-smoke",
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "checked_command_count": len(rows),
        "parse_checked_count": parse_checked_count,
        "parse_skipped_count": parse_skipped_count,
        "issues": issues,
        "next_command": "fix unsupported documented manage.py examples" if issues else "none",
    }


def summarize_prevention_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    failed = [row for row in checks if isinstance(row, dict) and row.get("ok") is not True]
    summary = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", ""),
        "ok": bool(report.get("ok")),
        "status": report.get("status", ""),
        "summary": {
            "check_count": len(checks),
            "failed_check_count": len(failed),
            "issue_count": len(report.get("issues", []) if isinstance(report.get("issues"), list) else []),
            "skipped_count": len(report.get("skipped", []) if isinstance(report.get("skipped"), list) else []),
        },
        "issues": report.get("issues", []),
        "skipped": report.get("skipped", []),
    }
    if not compact:
        summary["checks"] = checks
    elif not summary["issues"]:
        summary.pop("issues", None)
    return summary
