#!/usr/bin/env python3
"""Validate and expand an executable serial provider/host matrix without launching agents."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import Any

from support import provider_evidence_adapters


SCHEMA_VERSION = 1
TOOL_ID = "agent-benchmarking.provider-host-matrix-suite"
PLAN_TOOL_ID = "agent-benchmarking.provider-host-matrix-plan"
MAX_SUITE_BYTES = 1024 * 1024
ALLOWED_HOSTS = {
    "codex",
    "github-copilot",
    "claude-code",
    "openai-responses-api",
}
ARM_IDS = (
    "serial-active-model",
)
TASK_CLASSES = {"implementation", "validation", "review"}


def _exact_v1(value: object) -> bool:
    return type(value) is int and value == SCHEMA_VERSION


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _read_suite(path: Path) -> dict[str, Any]:
    lexical = Path(os.path.abspath(path))
    metadata = os.lstat(lexical)
    reparse = bool(int(getattr(metadata, "st_file_attributes", 0)) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or reparse or not stat.S_ISREG(metadata.st_mode):
        raise SystemExit("provider-host matrix suite must be a no-follow regular file")
    if metadata.st_size > MAX_SUITE_BYTES:
        raise SystemExit(f"provider-host matrix suite exceeds {MAX_SUITE_BYTES} bytes")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lexical, flags)
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino):
            raise SystemExit("provider-host matrix suite changed while opening")
        data = handle.read(MAX_SUITE_BYTES + 1)
    if len(data) > MAX_SUITE_BYTES:
        raise SystemExit(f"provider-host matrix suite exceeds {MAX_SUITE_BYTES} bytes")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"provider-host matrix suite is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit("provider-host matrix suite must be an object")
    return value


def validate_suite(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["provider-host matrix suite must be an object"]
    issues: list[str] = []
    allowed = {
        "schema_version",
        "tool",
        "suite",
        "version",
        "description",
        "launch_policy",
        "repetitions",
        "hosts",
        "arms",
        "tasks",
        "acceptance_gate",
    }
    unknown = sorted(set(value) - allowed)
    issues.extend(f"provider-host matrix field is not allowed: {field}" for field in unknown)
    if not _exact_v1(value.get("schema_version")):
        issues.append("provider-host matrix schema_version must be the integer 1")
    if value.get("tool") != TOOL_ID:
        issues.append(f"provider-host matrix tool must be {TOOL_ID}")
    for field in ("suite", "version", "description"):
        if not _non_empty(value.get(field)):
            issues.append(f"provider-host matrix {field} must be a non-empty string")
    if value.get("launch_policy") != "external-manual-only":
        issues.append("provider-host matrix launch_policy must be external-manual-only")
    repetitions = value.get("repetitions")
    if type(repetitions) is not int or repetitions < 3:
        issues.append("provider-host matrix repetitions must be an integer of at least 3")

    hosts = value.get("hosts")
    host_ids: list[str] = []
    if not isinstance(hosts, list) or not hosts:
        issues.append("provider-host matrix hosts must be a non-empty array")
    else:
        for index, host in enumerate(hosts):
            if not isinstance(host, dict):
                issues.append(f"provider-host matrix hosts[{index}] must be an object")
                continue
            if set(host) != {"id", "host_surface", "model_provider", "evidence_adapter_id"}:
                issues.append(f"provider-host matrix hosts[{index}] has an invalid shape")
                continue
            host_id = str(host.get("id", "")).strip()
            surface = str(host.get("host_surface", "")).strip()
            provider = str(host.get("model_provider", "")).strip()
            adapter_id = str(host.get("evidence_adapter_id", "")).strip()
            if not host_id:
                issues.append(f"provider-host matrix hosts[{index}].id must be non-empty")
            if surface not in ALLOWED_HOSTS:
                issues.append(f"provider-host matrix hosts[{index}].host_surface is unsupported")
            declaration = provider_evidence_adapters.ADAPTERS.get(adapter_id)
            if not isinstance(declaration, dict):
                issues.append(f"provider-host matrix hosts[{index}] uses an unknown evidence adapter")
            else:
                if surface not in declaration.get("host_surfaces", []):
                    issues.append(f"provider-host matrix hosts[{index}] adapter does not support its host")
                if provider not in declaration.get("model_providers", []):
                    issues.append(f"provider-host matrix hosts[{index}] adapter does not support its provider")
            host_ids.append(host_id)
    if len(host_ids) != len(set(host_ids)):
        issues.append("provider-host matrix host ids must be unique")

    arms = value.get("arms")
    if not isinstance(arms, list) or tuple(arms) != ARM_IDS:
        issues.append("provider-host matrix arms must declare only the executable serial arm")

    tasks = value.get("tasks")
    task_ids: list[str] = []
    if not isinstance(tasks, list) or not tasks:
        issues.append("provider-host matrix tasks must be a non-empty array")
    else:
        for index, task in enumerate(tasks):
            if not isinstance(task, dict):
                issues.append(f"provider-host matrix tasks[{index}] must be an object")
                continue
            if set(task) != {"id", "task_class", "prompt", "expected_checks"}:
                issues.append(f"provider-host matrix tasks[{index}] has an invalid shape")
                continue
            task_id = str(task.get("id", "")).strip()
            if not task_id:
                issues.append(f"provider-host matrix tasks[{index}].id must be non-empty")
            if task.get("task_class") not in TASK_CLASSES:
                issues.append(f"provider-host matrix tasks[{index}].task_class is unsupported")
            if not _non_empty(task.get("prompt")):
                issues.append(f"provider-host matrix tasks[{index}].prompt must be non-empty")
            checks = task.get("expected_checks")
            if not isinstance(checks, list) or not checks or not all(_non_empty(item) for item in checks):
                issues.append(f"provider-host matrix tasks[{index}].expected_checks must be non-empty strings")
            task_ids.append(task_id)
    if len(task_ids) != len(set(task_ids)):
        issues.append("provider-host matrix task ids must be unique")

    gate = value.get("acceptance_gate")
    required_gate = {
        "quality_no_regression": True,
        "no_new_failures": True,
        "no_new_skipped_checks": True,
        "provider_tokens_required": True,
        "wall_time_recorded": True,
        "promote_only_measured_task_class": True,
    }
    if gate != required_gate:
        issues.append("provider-host matrix acceptance_gate must match the fail-closed canonical gate")
    return issues


def build_plan(suite: dict[str, Any]) -> dict[str, Any]:
    issues = validate_suite(suite)
    if issues:
        return {
            "schema_version": SCHEMA_VERSION,
            "tool": PLAN_TOOL_ID,
            "ok": False,
            "status": "invalid",
            "issues": issues,
            "cells": [],
        }
    repetitions = int(suite["repetitions"])
    cells: list[dict[str, Any]] = []
    blocked_hosts: set[str] = set()
    blocked_arms: set[str] = set()
    for host in suite["hosts"]:
        declaration = provider_evidence_adapters.ADAPTERS[host["evidence_adapter_id"]]
        adapter_status = str(declaration.get("status", "unavailable"))
        supported_arms = {
            str(value) for value in declaration.get("supported_arms", [])
        }
        if adapter_status != "implemented":
            blocked_hosts.add(str(host["id"]))
        for task in suite["tasks"]:
            for arm in ARM_IDS:
                for replicate in range(1, repetitions + 1):
                    required_evidence = [
                        "runtime-observation-v1",
                        "token-measurement-v1",
                        "quality-result-v1",
                        "wall-time-v1",
                    ]
                    blocked_reasons: list[str] = []
                    if adapter_status != "implemented":
                        blocked_reasons.append(
                            f"evidence adapter status is {adapter_status}"
                        )
                    if arm not in supported_arms:
                        blocked_reasons.append(
                            "adapter does not attest the complete evidence required for this arm"
                        )
                        blocked_arms.add(arm)
                    cells.append(
                        {
                            "id": f"{host['id']}:{task['id']}:{arm}:r{replicate:02d}",
                            "host_id": host["id"],
                            "host_surface": host["host_surface"],
                            "model_provider": host["model_provider"],
                            "evidence_adapter_id": host["evidence_adapter_id"],
                            "evidence_adapter_status": adapter_status,
                            "task_id": task["id"],
                            "task_class": task["task_class"],
                            "arm": arm,
                            "replicate": replicate,
                            "execution_ready": not blocked_reasons,
                            "blocked_reasons": blocked_reasons,
                            "required_evidence": required_evidence,
                            "expected_checks": list(task["expected_checks"]),
                        }
                    )
    ready_cell_count = sum(
        1 for cell in cells if cell.get("execution_ready") is True
    )
    blocked_cell_count = len(cells) - ready_cell_count
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": PLAN_TOOL_ID,
        "ok": blocked_cell_count == 0,
        "status": "ready" if blocked_cell_count == 0 else "partially-runnable",
        "suite": suite["suite"],
        "version": suite["version"],
        "launch_policy": suite["launch_policy"],
        "does_not_launch_agents": True,
        "repetitions": repetitions,
        "host_count": len(suite["hosts"]),
        "task_count": len(suite["tasks"]),
        "arm_count": len(ARM_IDS),
        "cell_count": len(cells),
        "ready_cell_count": ready_cell_count,
        "blocked_cell_count": blocked_cell_count,
        "blocked_hosts": sorted(blocked_hosts),
        "blocked_arms": sorted(blocked_arms),
        "acceptance_gate": dict(suite["acceptance_gate"]),
        "cells": cells,
        "issues": [],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Provider/Host Benchmark Matrix",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Cells: {report.get('cell_count', 0)}",
        f"- Ready cells: {report.get('ready_cell_count', 0)}",
        f"- Blocked cells: {report.get('blocked_cell_count', 0)}",
        f"- Blocked hosts: {', '.join(report.get('blocked_hosts', [])) or 'none'}",
        f"- Blocked arms: {', '.join(report.get('blocked_arms', [])) or 'none'}",
        "- Launch policy: external manual execution only",
    ]
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", "", *[f"- {item}" for item in issues]])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--format", choices=("json", "markdown"), default="json", dest="output_format")
    args = parser.parse_args()
    suite = _read_suite(Path(args.suite))
    report = build_plan(suite)
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
