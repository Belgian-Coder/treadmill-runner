#!/usr/bin/env python3
"""Prepare a local current-agent benchmark run folder."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import benchmark_common as common


def resource_metadata(repo_root: Path) -> dict[str, Any]:
    script = repo_root / ".agents" / "manage.py"
    if script.exists():
        completed = subprocess.run(
            [sys.executable, "-B", str(script), "local-ai", "resources", "--json"],
            cwd=str(repo_root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
        if completed.returncode == 0:
            try:
                data = json.loads(completed.stdout)
                if isinstance(data, dict):
                    return {"source": "local-ai-helper.resources", "data": data}
            except json.JSONDecodeError:
                pass
    disk = shutil.disk_usage(repo_root)
    return {
        "source": "agent-benchmarking.fallback",
        "data": {
            "cpu": {"logical_cores": os.cpu_count() or 1, "platform": platform.platform()},
            "disk": {"free_gb": round(disk.free / (1024**3), 2), "total_gb": round(disk.total / (1024**3), 2)},
        },
    }


def find_repo_root(start: Path) -> Path:
    current = start if start.is_dir() else start.parent
    for candidate in [current, *current.parents]:
        if (candidate / ".agents" / "manage.py").exists():
            return candidate
    return Path.cwd()


def git_dirty_state(root: Path) -> str:
    completed = subprocess.run(
        ["git", "status", "--short"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return "unknown"
    return "dirty" if completed.stdout.strip() else "clean"


def require_supported_python() -> None:
    common.require_supported_python()


def normalized_context_path_identities(suite_base: Path, paths: list[str]) -> list[str]:
    identities: list[str] = []
    for value in paths:
        candidate, _issue = common.resolve_context_path(suite_base, value)
        if candidate is None:
            continue
        identities.append(os.path.normcase(os.path.abspath(candidate)))
    return identities


def prompt_markdown(task: dict[str, Any], static_context: str, task_context: str) -> str:
    lines = [
        "# Agent Benchmark Task",
        "",
        f"## Task: {task.get('title') or task.get('id')}",
        "",
        str(task.get("prompt", "")).strip(),
        "",
        "## Static Navigation Context",
        "",
        static_context.strip() or "No static navigation context was supplied.",
        "",
        "## Task-Specific Context",
        "",
        task_context.strip() or "No task-specific context was supplied.",
        "",
        "## Required Report",
        "",
        "Save a normalized result report with quality, commands, files_changed, checks, skipped, failures, notes, elapsed_seconds, output_text, unsupported_claims, invented_paths, invented_commands, false_validation_claims, abstentions, loaded_context, and evidence.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def context_packet_savings(suite_base: Path, paths: list[str]) -> dict[str, Any]:
    packets: list[dict[str, Any]] = []
    total_saved = 0
    for value in paths:
        candidate, issue = common.resolve_context_path(suite_base, value)
        if candidate is None:
            continue
        candidate = candidate.resolve(strict=False)
        if not candidate.exists() or candidate.suffix.lower() != ".json":
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or data.get("tool") != "workflow-manager.context-packet":
            continue
        estimates = data.get("token_estimates") if isinstance(data.get("token_estimates"), dict) else {}
        try:
            saved = int(estimates.get("estimated_tokens_saved", 0) or 0)
        except (TypeError, ValueError):
            saved = 0
        total_saved += max(saved, 0)
        packets.append(
            {
                "path": issue,
                "workflow": data.get("workflow", ""),
                "run_id": data.get("run_id", ""),
                "estimated_tokens_saved": max(saved, 0),
                "method": estimates.get("method", ""),
            }
        )
    return {
        "estimated_tokens_saved": total_saved,
        "packets": packets,
    }


def build_task_packet(
    *,
    suite_path: Path,
    task_id: str,
    run_id: str,
    agent_tool: str,
    model_label: str,
    workflow_name: str | None,
    workflow_version: str | None,
    git_ref: str | None,
) -> tuple[dict[str, Any], str]:
    if not common.RUN_ID_PATTERN.match(run_id):
        raise SystemExit("run-id must start with a letter or digit and use only letters, digits, underscore, dot, or hyphen.")
    suite = common.load_suite(suite_path)
    task = common.find_task(suite, task_id)
    repo_root = find_repo_root(suite_path)
    suite_base = suite_path.parent
    static_paths = common.as_string_list(task.get("static_context"), "static_context")
    task_paths = common.as_string_list(task.get("task_context"), "task_context")
    context_paths = [*static_paths, *task_paths]
    context_identities = normalized_context_path_identities(suite_base, context_paths)
    if len(context_identities) != len(set(context_identities)):
        raise SystemExit("static_context and task_context paths must be unique")
    static_text, included_static, skipped_static = common.collect_context(suite_base, static_paths)
    task_text, included_task, skipped_task = common.collect_context(suite_base, task_paths)
    savings = context_packet_savings(suite_base, context_paths)
    prompt_text = prompt_markdown(task, static_text, task_text)
    prompt_tokens = common.estimate_tokens(str(task.get("prompt", "")))
    static_tokens = common.estimate_tokens(static_text)
    task_tokens = common.estimate_tokens(task_text)
    token_counter = common.token_count_metadata()
    packet = {
        "schema_version": common.SCHEMA_VERSION,
        "tool": common.TOOL_NAME,
        "ok": True,
        "status": "prepared",
        "run_id": run_id,
        "suite": common.suite_name(suite, suite_path),
        "suite_path": str(suite_path.resolve()),
        "task_id": task_id,
        "subject": common.subject_line(agent_tool, model_label, workflow_name, workflow_version),
        "agent_tool": agent_tool,
        "model_label": model_label,
        "workflow_name": workflow_name or "",
        "workflow_version": workflow_version or "",
        "git_ref": git_ref or "",
        "run_config": common.normalize_run_config(
            {
                "suite_version": str(suite.get("version", suite.get("schema_version", ""))),
                "prompt_version": str(task.get("prompt_version", suite.get("prompt_version", "v1"))),
                "git_ref": git_ref or "",
                "dirty_state": git_dirty_state(repo_root),
            }
        ),
        "determinism": common.deterministic_metadata(
            run_id=run_id,
            task_id=task_id,
            artifact_dir=run_id,
        ),
        "resource_metadata": resource_metadata(repo_root),
        "task": {
            "id": task_id,
            "title": str(task.get("title", task_id)),
            "prompt": str(task.get("prompt", "")),
            "expected_checks": common.as_string_list(task.get("expected_checks"), "expected_checks"),
            "static_context": included_static,
            "task_context": included_task,
        },
        "advisory_token_estimates": {
            "estimates": not token_counter["exact"],
            "exact": token_counter["exact"],
            "method": common.TOKEN_ESTIMATION_METHOD,
            "token_counter": token_counter,
            "prompt_tokens_estimated": prompt_tokens,
            "static_navigation_context": static_tokens,
            "task_specific_context": task_tokens,
            "input_tokens_estimated": prompt_tokens + static_tokens + task_tokens,
            "cacheable_static_tokens_estimated": static_tokens,
            "context_saved_tokens_estimated": savings["estimated_tokens_saved"],
        },
        "context_savings": savings,
        "checks": ["suite loaded", "task selected", "local context estimated"],
        "skipped": sorted(set(skipped_static + skipped_task)),
    }
    return packet, prompt_text


def prepare_run(
    *,
    suite_path: Path,
    task_id: str,
    output_root: Path,
    run_id: str,
    agent_tool: str,
    model_label: str,
    workflow_name: str | None = None,
    workflow_version: str | None = None,
    git_ref: str | None = None,
    write: bool = False,
) -> Path:
    suite_path = suite_path.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    packet, prompt_text = build_task_packet(
        suite_path=suite_path,
        task_id=task_id,
        run_id=run_id,
        agent_tool=agent_tool,
        model_label=model_label,
        workflow_name=workflow_name,
        workflow_version=workflow_version,
        git_ref=git_ref,
    )
    run_dir = output_root / run_id
    if write:
        common.write_json(run_dir / "benchmark-task.json", packet)
        common.write_text(run_dir / "PROMPT.md", prompt_text)
        common.write_json(
            run_dir / "result-template.json",
            {
                "quality": {"passed": False, "score": 0.0},
                "commands": [],
                "files_changed": [],
                "checks": [],
                "skipped": [],
                "failures": [],
                "notes": [],
                "unsupported_claims": [],
                "invented_paths": [],
                "invented_commands": [],
                "false_validation_claims": [],
                "abstentions": [],
                "loaded_context": [],
                "evidence": [],
                "elapsed_seconds": 0,
                "output_text": "",
            },
        )
    return run_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, help="benchmark suite JSON")
    parser.add_argument("--task-id", required=True, help="task id from the suite")
    parser.add_argument("--output-root", required=True, help="root folder for benchmark runs")
    parser.add_argument("--run-id", required=True, help="run folder id")
    parser.add_argument("--agent-tool", default="codex", help="agent tool label")
    parser.add_argument("--model-label", default="unlabeled-model", help="model label")
    parser.add_argument("--workflow-name", default="", help="workflow name under test")
    parser.add_argument("--workflow-version", default="", help="workflow version or label under test")
    parser.add_argument("--git-ref", default="", help="manual git ref label")
    parser.add_argument("--write", action="store_true", help="write the run folder")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    return parser


def main() -> int:
    common.require_supported_python()
    args = build_parser().parse_args()
    run_dir = prepare_run(
        suite_path=Path(args.suite),
        task_id=args.task_id,
        output_root=Path(args.output_root),
        run_id=args.run_id,
        agent_tool=args.agent_tool,
        model_label=args.model_label,
        workflow_name=args.workflow_name or None,
        workflow_version=args.workflow_version or None,
        git_ref=args.git_ref or None,
        write=args.write,
    )
    packet = common.read_json(run_dir / "benchmark-task.json") if args.write else build_task_packet(
        suite_path=Path(args.suite).expanduser().resolve(),
        task_id=args.task_id,
        run_id=args.run_id,
        agent_tool=args.agent_tool,
        model_label=args.model_label,
        workflow_name=args.workflow_name or None,
        workflow_version=args.workflow_version or None,
        git_ref=args.git_ref or None,
    )[0]
    if args.output_format == "json":
        print(json.dumps({"ok": True, "run_dir": str(run_dir), "task": packet}, indent=2, sort_keys=True))
    else:
        print(f"Prepared benchmark run: {run_dir}")
        print(f"Subject: {packet['subject']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
