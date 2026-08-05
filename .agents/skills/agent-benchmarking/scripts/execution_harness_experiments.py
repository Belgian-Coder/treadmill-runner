#!/usr/bin/env python3
"""Validate and expand offline V1 execution-harness experiments without launching them."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import Any

from support import provider_evidence_adapters


SCHEMA_VERSION = 1
SUITE_TOOL_ID = "agent-benchmarking.execution-harness-experiment-suite"
PLAN_TOOL_ID = "agent-benchmarking.execution-harness-experiment-plan"
MAX_SUITE_BYTES = 256 * 1024
EXPECTED_HOSTS = {
    "codex": ("openai", "codex-rollout-v1"),
    "github-copilot": ("other", "github-copilot-otel-v1"),
    "claude-code": ("anthropic", "claude-code-result-v1"),
}
BASE_EVIDENCE = (
    "runtime-observation-v1",
    "token-measurement-v1",
    "quality-result-v1",
    "wall-time-v1",
    "rework-v1",
    "deterministic-validation-v1",
    "isolated-workspace-v1",
    "exact-prompt-v1",
    "host-tool-vocabulary-v1",
    "route-resolution-v1",
    "host-adapter-receipt-v1",
)
ADAPTER_CAPABILITIES = {
    "codex-rollout-v1": {
        "complete-serial-usage",
        "observed-deliberation",
        "observed-model",
    },
    "github-copilot-otel-v1": {
        "complete-serial-usage",
        "observed-model",
    },
    "claude-code-result-v1": {
        "complete-serial-usage",
        "observed-model",
    },
}
EXPERIMENT_CONTRACTS = {
    "simple-bounded-efficiency": {
        "arms": (
            "default-execution",
            "bounded-efficient-execution",
        ),
        "roles": ("control", "candidate"),
        "adapter_arms": ("serial-active-model", "serial-active-model"),
        "promotion_candidates": (False, True),
        "capabilities": (
            ("complete-serial-usage", "observed-model"),
            (
                "complete-serial-usage",
                "observed-model",
            ),
        ),
        "extra_evidence": (
            ("task-boundary-v1",),
            (
                "bounded-task-contract-v1",
                "trajectory-signals-v1",
            ),
        ),
    },
    "guided-continuation": {
        "arms": (
            "frontier",
            "executor",
        ),
        "roles": ("control", "control"),
        "adapter_arms": (
            "serial-active-model",
            "serial-active-model",
        ),
        "promotion_candidates": (False, False),
        "capabilities": (
            ("complete-serial-usage", "observed-model"),
            ("complete-serial-usage", "observed-model"),
        ),
        "extra_evidence": (
            ("frontier-role-config-v1",),
            ("executor-role-config-v1",),
        ),
    },
}
EXECUTION_POLICY = {
    "launches_agents": False,
    "launches_models": False,
    "uses_network": False,
    "uses_subprocesses": False,
    "writes_files": False,
}
PROMOTION_POLICY = {
    "mode": "results-required",
    "minimum_repetitions_per_arm": 3,
    "quality_no_regression": True,
    "no_new_failures": True,
    "no_new_skipped_checks": True,
    "rework_no_regression": True,
    "provider_tokens_required": True,
    "wall_time_recorded": True,
    "host_scoped_only": True,
    "task_scoped_only": True,
    "cross_host_inference_allowed": False,
    "global_default_promotion_allowed": False,
    "estimated_cost_can_promote": False,
    "unready_cells_can_promote": False,
}


def _exact_v1(value: object) -> bool:
    return type(value) is int and value == SCHEMA_VERSION


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def _read_suite(path: Path) -> dict[str, Any]:
    lexical = Path(os.path.abspath(path))
    try:
        metadata = os.lstat(lexical)
    except OSError as exc:
        raise SystemExit(f"execution-harness suite is unavailable: {exc}") from exc
    reparse = bool(int(getattr(metadata, "st_file_attributes", 0)) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or reparse or not stat.S_ISREG(metadata.st_mode):
        raise SystemExit("execution-harness suite must be a no-follow regular file")
    if metadata.st_size > MAX_SUITE_BYTES:
        raise SystemExit(f"execution-harness suite exceeds {MAX_SUITE_BYTES} bytes")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lexical, flags)
    except OSError as exc:
        raise SystemExit(f"execution-harness suite cannot be opened safely: {exc}") from exc
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino):
            raise SystemExit("execution-harness suite changed while opening")
        data = handle.read(MAX_SUITE_BYTES + 1)
    if len(data) > MAX_SUITE_BYTES:
        raise SystemExit(f"execution-harness suite exceeds {MAX_SUITE_BYTES} bytes")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"execution-harness suite is invalid strict JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit("execution-harness suite must be an object")
    return value


def _string_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(_non_empty(item) for item in value)
        and len(value) == len(set(value))
    )


def _expected_arm_contract(experiment_id: str, position: int) -> dict[str, Any]:
    contract = EXPERIMENT_CONTRACTS[experiment_id]
    return {
        "id": contract["arms"][position],
        "comparison_role": contract["roles"][position],
        "evidence_adapter_arm": contract["adapter_arms"][position],
        "promotion_candidate": contract["promotion_candidates"][position],
        "required_capabilities": list(contract["capabilities"][position]),
        "required_evidence": [
            *BASE_EVIDENCE,
            *contract["extra_evidence"][position],
        ],
    }


def validate_suite(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["execution-harness suite must be an object"]
    issues: list[str] = []
    allowed = {
        "schema_version",
        "tool",
        "suite",
        "version",
        "description",
        "launch_policy",
        "repetitions",
        "execution_policy",
        "promotion_policy",
        "hosts",
        "experiments",
    }
    issues.extend(
        f"execution-harness suite field is not allowed: {field}"
        for field in sorted(set(value) - allowed)
    )
    if not _exact_v1(value.get("schema_version")):
        issues.append("execution-harness suite schema_version must be the integer 1")
    if value.get("tool") != SUITE_TOOL_ID:
        issues.append(f"execution-harness suite tool must be {SUITE_TOOL_ID}")
    for field in ("suite", "version", "description"):
        if not _non_empty(value.get(field)):
            issues.append(f"execution-harness suite {field} must be a non-empty string")
    if value.get("launch_policy") != "external-manual-only":
        issues.append("execution-harness suite launch_policy must be external-manual-only")
    repetitions = value.get("repetitions")
    if type(repetitions) is not int or repetitions != 3:
        issues.append("execution-harness suite repetitions must be exactly 3 in V1")
    if value.get("execution_policy") != EXECUTION_POLICY:
        issues.append("execution-harness suite execution_policy must prohibit all planner side effects")
    if value.get("promotion_policy") != PROMOTION_POLICY:
        issues.append("execution-harness suite promotion_policy is unsafe or not canonical V1")
    if isinstance(repetitions, int) and repetitions < PROMOTION_POLICY["minimum_repetitions_per_arm"]:
        issues.append("execution-harness suite repetitions are below the promotion minimum")

    hosts = value.get("hosts")
    observed_surfaces: set[str] = set()
    observed_ids: set[str] = set()
    if not isinstance(hosts, list) or len(hosts) != len(EXPECTED_HOSTS):
        issues.append("execution-harness suite must declare exactly Codex, Copilot, and Claude Code hosts")
    else:
        for index, host in enumerate(hosts):
            if not isinstance(host, dict):
                issues.append(f"execution-harness suite hosts[{index}] must be an object")
                continue
            if set(host) != {"id", "host_surface", "model_provider", "evidence_adapter_id"}:
                issues.append(f"execution-harness suite hosts[{index}] has an invalid shape")
                continue
            host_id = str(host.get("id", "")).strip()
            surface = str(host.get("host_surface", "")).strip()
            provider = str(host.get("model_provider", "")).strip()
            adapter_id = str(host.get("evidence_adapter_id", "")).strip()
            if not host_id or host_id in observed_ids:
                issues.append(f"execution-harness suite hosts[{index}].id must be unique and non-empty")
            observed_ids.add(host_id)
            if surface not in EXPECTED_HOSTS or surface in observed_surfaces:
                issues.append(f"execution-harness suite hosts[{index}].host_surface is unsupported or duplicated")
            else:
                expected_provider, expected_adapter = EXPECTED_HOSTS[surface]
                if (provider, adapter_id) != (expected_provider, expected_adapter):
                    issues.append(f"execution-harness suite hosts[{index}] does not use the canonical host adapter")
            observed_surfaces.add(surface)
            declaration = provider_evidence_adapters.ADAPTERS.get(adapter_id)
            if not isinstance(declaration, dict) or declaration.get("status") != "implemented":
                issues.append(f"execution-harness suite hosts[{index}] lacks an implemented evidence adapter")
            elif (
                surface not in declaration.get("host_surfaces", [])
                or provider not in declaration.get("model_providers", [])
            ):
                issues.append(f"execution-harness suite hosts[{index}] adapter identity does not match its host")
        if observed_surfaces != set(EXPECTED_HOSTS):
            issues.append("execution-harness suite host surfaces do not match canonical V1")

    experiments = value.get("experiments")
    expected_ids = tuple(EXPERIMENT_CONTRACTS)
    if not isinstance(experiments, list) or len(experiments) != len(expected_ids):
        issues.append("execution-harness suite must declare the two executable canonical V1 experiments")
    else:
        observed_experiment_ids: list[str] = []
        for index, experiment in enumerate(experiments):
            if not isinstance(experiment, dict):
                issues.append(f"execution-harness suite experiments[{index}] must be an object")
                continue
            if set(experiment) != {"id", "description", "task_class", "prompt", "expected_checks", "arms"}:
                issues.append(f"execution-harness suite experiments[{index}] has an invalid shape")
                continue
            experiment_id = str(experiment.get("id", "")).strip()
            observed_experiment_ids.append(experiment_id)
            if experiment_id not in EXPERIMENT_CONTRACTS:
                issues.append(f"execution-harness suite experiments[{index}].id is unsupported")
                continue
            if experiment.get("task_class") != "implementation":
                issues.append(f"execution-harness suite {experiment_id} must remain implementation-scoped")
            if not _non_empty(experiment.get("description")) or not _non_empty(experiment.get("prompt")):
                issues.append(f"execution-harness suite {experiment_id} description and prompt must be non-empty")
            if not _string_list(experiment.get("expected_checks")):
                issues.append(f"execution-harness suite {experiment_id} expected_checks must be unique strings")
            arms = experiment.get("arms")
            expected_arms = EXPERIMENT_CONTRACTS[experiment_id]["arms"]
            if not isinstance(arms, list) or len(arms) != len(expected_arms):
                issues.append(f"execution-harness suite {experiment_id} must declare its canonical arms")
                continue
            for arm_index, arm in enumerate(arms):
                expected = _expected_arm_contract(experiment_id, arm_index)
                if arm != expected:
                    issues.append(
                        f"execution-harness suite {experiment_id} arm {arm_index} weakens or changes the canonical V1 contract"
                    )
        if tuple(observed_experiment_ids) != expected_ids:
            issues.append("execution-harness suite experiments must use canonical V1 order and unique ids")
    return issues


def build_plan(suite: dict[str, Any]) -> dict[str, Any]:
    issues = validate_suite(suite)
    if issues:
        return {
            "schema_version": SCHEMA_VERSION,
            "tool": PLAN_TOOL_ID,
            "ok": False,
            "status": "invalid",
            "unsafe_promotion_rejected": True,
            "issues": issues,
            "cells": [],
        }

    repetitions = int(suite["repetitions"])
    cells: list[dict[str, Any]] = []
    blocked_hosts: set[str] = set()
    blocked_arms: set[str] = set()
    experiment_summaries: list[dict[str, Any]] = []
    for experiment in suite["experiments"]:
        experiment_cells: list[dict[str, Any]] = []
        for host in suite["hosts"]:
            adapter_id = str(host["evidence_adapter_id"])
            declaration = provider_evidence_adapters.ADAPTERS[adapter_id]
            adapter_status = str(declaration.get("status", "unavailable"))
            supported_arms = {str(item) for item in declaration.get("supported_arms", [])}
            adapter_capabilities = ADAPTER_CAPABILITIES.get(adapter_id, set())
            for arm in experiment["arms"]:
                required_capabilities = {str(item) for item in arm["required_capabilities"]}
                missing_capabilities = sorted(required_capabilities - adapter_capabilities)
                for replicate in range(1, repetitions + 1):
                    blocked_reasons: list[str] = []
                    if adapter_status != "implemented":
                        blocked_reasons.append(f"evidence adapter status is {adapter_status}")
                    if arm["evidence_adapter_arm"] not in supported_arms:
                        blocked_reasons.append(
                            "adapter does not attest the complete execution mode required for this arm"
                        )
                    if missing_capabilities:
                        blocked_reasons.append(
                            "adapter lacks required evidence capabilities: " + ", ".join(missing_capabilities)
                        )
                    execution_ready = not blocked_reasons
                    if not execution_ready:
                        blocked_hosts.add(str(host["id"]))
                        blocked_arms.add(f"{experiment['id']}:{arm['id']}")
                    promotion_blockers = [
                        "planner does not execute trials or evaluate external results",
                        *blocked_reasons,
                    ]
                    cell = {
                        "id": (
                            f"{host['id']}:{experiment['id']}:{arm['id']}:"
                            f"r{replicate:02d}"
                        ),
                        "host_id": host["id"],
                        "host_surface": host["host_surface"],
                        "model_provider": host["model_provider"],
                        "evidence_adapter_id": adapter_id,
                        "evidence_adapter_status": adapter_status,
                        "experiment_id": experiment["id"],
                        "task_class": experiment["task_class"],
                        "arm": arm["id"],
                        "comparison_role": arm["comparison_role"],
                        "promotion_candidate": arm["promotion_candidate"],
                        "replicate": replicate,
                        "execution_ready": execution_ready,
                        "blocked_reasons": blocked_reasons,
                        "missing_capabilities": missing_capabilities,
                        "required_capabilities": list(arm["required_capabilities"]),
                        "required_evidence": list(arm["required_evidence"]),
                        "expected_checks": list(experiment["expected_checks"]),
                        "promotion_ready": False,
                        "promotion_blockers": promotion_blockers,
                        "unsafe_promotion_rejected": True,
                    }
                    cells.append(cell)
                    experiment_cells.append(cell)
        ready_count = sum(1 for cell in experiment_cells if cell["execution_ready"])
        experiment_summaries.append(
            {
                "id": experiment["id"],
                "arm_count": len(experiment["arms"]),
                "cell_count": len(experiment_cells),
                "ready_cell_count": ready_count,
                "blocked_cell_count": len(experiment_cells) - ready_count,
            }
        )

    ready_cell_count = sum(1 for cell in cells if cell["execution_ready"])
    blocked_cell_count = len(cells) - ready_cell_count
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": PLAN_TOOL_ID,
        "ok": blocked_cell_count == 0,
        "status": "ready" if blocked_cell_count == 0 else "partially-runnable",
        "suite": suite["suite"],
        "version": suite["version"],
        "launch_policy": suite["launch_policy"],
        "execution_policy": dict(suite["execution_policy"]),
        "promotion_policy": dict(suite["promotion_policy"]),
        "does_not_launch_agents_or_models": True,
        "unsafe_promotion_rejected": True,
        "promotion_status": "blocked-until-external-results",
        "promotion_blockers": [
            "no external trials were executed",
            "no result packets were evaluated",
            "planner never promotes an arm",
        ],
        "repetitions": repetitions,
        "host_count": len(suite["hosts"]),
        "experiment_count": len(suite["experiments"]),
        "cell_count": len(cells),
        "ready_cell_count": ready_cell_count,
        "blocked_cell_count": blocked_cell_count,
        "blocked_hosts": sorted(blocked_hosts),
        "blocked_arms": sorted(blocked_arms),
        "experiments": experiment_summaries,
        "cells": cells,
        "issues": [],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Execution Harness Experiment Plan",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Hosts: {report.get('host_count', 0)}",
        f"- Experiments: {report.get('experiment_count', 0)}",
        f"- Cells: {report.get('cell_count', 0)}",
        f"- Ready cells: {report.get('ready_cell_count', 0)}",
        f"- Blocked cells: {report.get('blocked_cell_count', 0)}",
        f"- Blocked hosts: {', '.join(report.get('blocked_hosts', [])) or 'none'}",
        f"- Blocked arms: {', '.join(report.get('blocked_arms', [])) or 'none'}",
        "- Launch policy: external manual execution only",
        "- Promotion: blocked until external results; this planner never promotes an arm",
        "- Unsafe promotion rejected: true",
    ]
    experiments = report.get("experiments")
    if isinstance(experiments, list) and experiments:
        lines.extend(["", "## Experiments", ""])
        for experiment in experiments:
            if not isinstance(experiment, dict):
                continue
            lines.append(
                f"- `{experiment.get('id', '')}`: {experiment.get('ready_cell_count', 0)} ready, "
                f"{experiment.get('blocked_cell_count', 0)} blocked, "
                f"{experiment.get('cell_count', 0)} total"
            )
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", "", *[f"- {item}" for item in issues]])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True)
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        dest="output_format",
    )
    args = parser.parse_args()
    suite = _read_suite(Path(args.suite))
    report = build_plan(suite)
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if not report.get("issues") else 1


if __name__ == "__main__":
    raise SystemExit(main())
