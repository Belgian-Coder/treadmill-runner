#!/usr/bin/env python3
"""Resolve project-owned task model priorities without invoking a model."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


CONFIG_REL = ".agents/orchestration.json"
DEFAULTS_REL = ".agents/skills/workflow-manager/assets/orchestration-defaults.json"
SCHEMA_REL = ".agents/skills/workflow-manager/assets/schemas/orchestration.schema.json"
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SPECIAL_FALLBACKS = {"active", "inherit"}
REASONING_LEVELS = {"inherit", "low", "medium", "high", "xhigh"}
EXECUTION_MODES = {"deterministic", "orchestrator-decides", "primary"}


def _read_object(path: Path) -> tuple[dict[str, Any], list[str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}, [f"missing orchestration configuration: {path.as_posix()}"]
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"cannot read orchestration configuration {path.as_posix()}: {exc}"]
    if not isinstance(value, dict):
        return {}, [f"orchestration configuration must be a JSON object: {path.as_posix()}"]
    return value, []


def load_orchestration(root: Path) -> tuple[dict[str, Any], str, list[str]]:
    project_path = root / CONFIG_REL
    source = project_path if project_path.is_file() else root / DEFAULTS_REL
    document, issues = _read_object(source)
    issues.extend(validate_orchestration(document))
    return document, source.relative_to(root).as_posix(), sorted(dict.fromkeys(issues))


def validate_orchestration(document: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    allowed_root = {"$schema", "schema_version", "default_task_set", "chains", "task_sets", "tasks"}
    for key in sorted(set(document) - allowed_root):
        issues.append(f"unsupported root field: {key}")
    if document.get("schema_version") != 1:
        issues.append("schema_version must equal 1")
    for field in ("chains", "task_sets", "tasks"):
        if not isinstance(document.get(field), dict):
            issues.append(f"{field} must be an object")
    chains = document.get("chains") if isinstance(document.get("chains"), dict) else {}
    task_sets = document.get("task_sets") if isinstance(document.get("task_sets"), dict) else {}
    tasks = document.get("tasks") if isinstance(document.get("tasks"), dict) else {}
    default_task_set = document.get("default_task_set")
    if not isinstance(default_task_set, str) or default_task_set not in task_sets:
        issues.append("default_task_set must reference a declared task set")

    for chain_id, raw_chain in chains.items():
        if not isinstance(chain_id, str) or not ID_RE.fullmatch(chain_id):
            issues.append(f"invalid chain id: {chain_id!r}")
            continue
        if not isinstance(raw_chain, dict) or not isinstance(raw_chain.get("hosts"), dict):
            issues.append(f"chain {chain_id!r} must contain a hosts object")
            continue
        for key in sorted(set(raw_chain) - {"hosts"}):
            issues.append(f"chain {chain_id!r} has unsupported field: {key}")
        hosts = raw_chain["hosts"]
        if "default" not in hosts:
            issues.append(f"chain {chain_id!r} must declare a default host route")
        for host, raw_candidates in hosts.items():
            if not isinstance(host, str) or not ID_RE.fullmatch(host):
                issues.append(f"chain {chain_id!r} has invalid host id: {host!r}")
                continue
            if not isinstance(raw_candidates, list) or not raw_candidates:
                issues.append(f"chain {chain_id!r} host {host!r} must have candidates")
                continue
            seen: set[str] = set()
            for index, candidate in enumerate(raw_candidates):
                label = f"chain {chain_id!r} host {host!r} candidate {index + 1}"
                if not isinstance(candidate, dict):
                    issues.append(f"{label} must be an object")
                    continue
                for key in sorted(set(candidate) - {"model", "reasoning", "note"}):
                    issues.append(f"{label} has unsupported field: {key}")
                model = candidate.get("model")
                reasoning = candidate.get("reasoning", "inherit")
                if not isinstance(model, str) or not model.strip():
                    issues.append(f"{label} requires a non-empty model")
                elif model.casefold() in seen:
                    issues.append(f"{label} duplicates model {model!r}")
                else:
                    seen.add(model.casefold())
                if reasoning not in REASONING_LEVELS:
                    issues.append(f"{label} has unsupported reasoning {reasoning!r}")
            last = raw_candidates[-1]
            last_model = last.get("model", "") if isinstance(last, dict) else ""
            if str(last_model).casefold() not in SPECIAL_FALLBACKS:
                issues.append(
                    f"chain {chain_id!r} host {host!r} must end with active or inherit fallback"
                )

    for task_set_id, raw_task_set in task_sets.items():
        label = f"task set {task_set_id!r}"
        if not isinstance(task_set_id, str) or not ID_RE.fullmatch(task_set_id):
            issues.append(f"invalid task set id: {task_set_id!r}")
        if not isinstance(raw_task_set, dict):
            issues.append(f"{label} must be an object")
            continue
        for key in sorted(set(raw_task_set) - {"responsibility", "execution", "chain"}):
            issues.append(f"{label} has unsupported field: {key}")
        if not str(raw_task_set.get("responsibility", "")).strip():
            issues.append(f"{label} requires a responsibility")
        execution = raw_task_set.get("execution")
        if execution not in EXECUTION_MODES:
            issues.append(f"{label} has unsupported execution mode {execution!r}")
        chain = raw_task_set.get("chain")
        if execution == "deterministic":
            if chain is not None:
                issues.append(f"{label} is deterministic and must not declare a chain")
        elif not isinstance(chain, str) or chain not in chains:
            issues.append(f"{label} must reference a declared chain")

    for task_id, raw_task in tasks.items():
        label = f"task {task_id!r}"
        if not isinstance(task_id, str) or not ID_RE.fullmatch(task_id):
            issues.append(f"invalid task id: {task_id!r}")
        if not isinstance(raw_task, dict):
            issues.append(f"{label} must be an object")
            continue
        for key in sorted(set(raw_task) - {"responsibility", "task_set", "chain"}):
            issues.append(f"{label} has unsupported field: {key}")
        if not str(raw_task.get("responsibility", "")).strip():
            issues.append(f"{label} requires a responsibility")
        task_set = raw_task.get("task_set")
        if not isinstance(task_set, str) or task_set not in task_sets:
            issues.append(f"{label} must reference a declared task set")
        chain = raw_task.get("chain")
        if chain is not None and (not isinstance(chain, str) or chain not in chains):
            issues.append(f"{label} chain override must reference a declared chain")
        if (
            chain is not None
            and isinstance(task_set, str)
            and isinstance(task_sets.get(task_set), dict)
            and task_sets[task_set].get("execution") == "deterministic"
        ):
            issues.append(f"{label} belongs to a deterministic task set and must not override a chain")
    return sorted(dict.fromkeys(issues))


def resolve_orchestration(
    root: Path,
    *,
    task: str | None,
    task_set: str | None,
    host: str,
    available_models: list[str] | None = None,
    failed_models: list[str] | None = None,
    validate_only: bool = False,
) -> dict[str, Any]:
    document, source, issues = load_orchestration(root)
    report: dict[str, Any] = {
        "schema_version": 1,
        "tool": "workflow-manager.orchestration-route",
        "ok": not issues,
        "status": "valid" if not issues else "invalid",
        "source": source,
        "schema": SCHEMA_REL,
        "issues": issues,
    }
    if validate_only or issues:
        return report

    task_sets = document["task_sets"]
    tasks = document["tasks"]
    selected_task: dict[str, Any] | None = None
    if task:
        raw_task = tasks.get(task)
        if isinstance(raw_task, dict):
            selected_task = raw_task
            selected_task_set_id = str(raw_task["task_set"])
        else:
            selected_task_set_id = str(document["default_task_set"])
    elif task_set:
        selected_task_set_id = task_set
    else:
        report.update(ok=False, status="invalid-request", issues=["--task or --task-set is required"])
        return report
    if selected_task_set_id not in task_sets:
        report.update(
            ok=False,
            status="invalid-request",
            issues=[f"unknown task set: {selected_task_set_id}"],
        )
        return report

    selected_task_set = task_sets[selected_task_set_id]
    execution = str(selected_task_set["execution"])
    responsibility = (
        str(selected_task["responsibility"])
        if selected_task is not None
        else str(selected_task_set["responsibility"])
    )
    report.update(
        status="deterministic" if execution == "deterministic" else "preference-only",
        task=task or "",
        task_known=selected_task is not None if task else None,
        task_set=selected_task_set_id,
        responsibility=responsibility,
        execution=execution,
        host=host,
        chain=None,
        host_route=None,
        candidates=[],
        selected=None,
    )
    if execution == "deterministic":
        return report

    chain_id = str(
        selected_task.get("chain")
        if selected_task is not None and selected_task.get("chain") is not None
        else selected_task_set["chain"]
    )
    hosts = document["chains"][chain_id]["hosts"]
    host_route = host if host in hosts else "default"
    raw_candidates = hosts[host_route]
    failed = {value.casefold() for value in failed_models or []}
    available = None if available_models is None else {value.casefold() for value in available_models}
    candidates: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    for index, raw_candidate in enumerate(raw_candidates, start=1):
        candidate = dict(raw_candidate)
        model = str(candidate["model"])
        normalized = model.casefold()
        if normalized in failed:
            candidate_status = "failed"
        elif available is None:
            candidate_status = "preferred"
        elif normalized in available or normalized in SPECIAL_FALLBACKS:
            candidate_status = "selected" if selected is None else "available-fallback"
            if selected is None:
                selected = {**candidate, "priority": index}
        else:
            candidate_status = "unavailable"
        candidates.append({**candidate, "priority": index, "status": candidate_status})

    report.update(chain=chain_id, host_route=host_route, candidates=candidates, selected=selected)
    if available is not None:
        if selected is None:
            report.update(ok=False, status="blocked", issues=["no available model or active-model fallback remains"])
        else:
            report["status"] = "selected"
    return report


def render_orchestration_markdown(report: dict[str, Any]) -> str:
    lines = ["# Orchestration route", ""]
    for key in ("status", "source", "task", "task_set", "responsibility", "execution", "host", "chain", "host_route"):
        if report.get(key) not in (None, ""):
            lines.append(f"- {key.replace('_', ' ').title()}: `{report[key]}`")
    selected = report.get("selected")
    if isinstance(selected, dict):
        lines.append(
            f"- Selected: `{selected.get('model')}` (reasoning `{selected.get('reasoning', 'inherit')}`, priority {selected.get('priority')})"
        )
    candidates = report.get("candidates")
    if isinstance(candidates, list) and candidates:
        lines.extend(["", "## Priority order", ""])
        for candidate in candidates:
            lines.append(
                f"{candidate.get('priority')}. `{candidate.get('model')}` / `{candidate.get('reasoning', 'inherit')}` — {candidate.get('status')}"
            )
    issues = report.get("issues")
    if isinstance(issues, list) and issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in issues)
    return "\n".join(lines) + "\n"
