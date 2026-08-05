#!/usr/bin/env python3
"""Prepare, preflight, and aggregate isolated repeated three-arm benchmarks."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import statistics
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import benchmark_common as common
from support import execution_prompt_marker
from support import token_measurement_v1 as token_v1


ARM_IDS = ("direct", "harness_no_local_ai", "harness_local_ai")
ARM_CONTRACTS = {
    "direct": {
        "harness_enabled": False,
        "local_ai_enabled": False,
        "allowed_context_roles": ["ordinary-task", "target-fixture"],
    },
    "harness_no_local_ai": {
        "harness_enabled": True,
        "local_ai_enabled": False,
        "allowed_context_roles": ["ordinary-task", "target-fixture", "installed-harness"],
    },
    "harness_local_ai": {
        "harness_enabled": True,
        "local_ai_enabled": True,
        "allowed_context_roles": ["ordinary-task", "target-fixture", "installed-harness", "local-ai-advisory"],
    },
}
DELEGATION_ARM_CONTRACTS = {
    "direct": {
        "harness_enabled": False,
        "local_ai_enabled": False,
        "delegation_enabled": False,
        "task_class": "independent-read-heavy",
        "allowed_context_roles": ["ordinary-task", "target-fixture"],
    },
    "harness_no_local_ai": {
        "harness_enabled": True,
        "local_ai_enabled": False,
        "delegation_enabled": True,
        "task_class": "independent-read-heavy",
        "allowed_context_roles": ["ordinary-task", "target-fixture", "installed-harness"],
    },
    "harness_local_ai": {
        "harness_enabled": True,
        "local_ai_enabled": False,
        "delegation_enabled": True,
        "task_class": "independent-read-heavy",
        "allowed_context_roles": ["ordinary-task", "target-fixture", "installed-harness"],
    },
}
DELEGATION_ARM_ALIASES = {
    "direct": "single-gpt-5.6-sol-medium",
    "harness_no_local_ai": "root-plus-two-gpt-5.6-sol-medium",
    "harness_local_ai": "root-gpt-5.6-sol-plus-two-gpt-5.6-terra-medium",
}
DELEGATION_REQUESTED_THREAD_MODELS = {
    "direct": {
        "root": {"provider": "openai", "model": "gpt-5.6-sol", "reasoning_effort": "medium"},
    },
    "harness_no_local_ai": {
        "root": {"provider": "openai", "model": "gpt-5.6-sol", "reasoning_effort": "medium"},
        "worker": {"provider": "openai", "model": "gpt-5.6-sol", "reasoning_effort": "medium"},
    },
    "harness_local_ai": {
        "root": {"provider": "openai", "model": "gpt-5.6-sol", "reasoning_effort": "medium"},
        "worker": {"provider": "openai", "model": "gpt-5.6-terra", "reasoning_effort": "medium"},
    },
}
DELEGATION_THREAD_COUNTS = {
    "direct": 1,
    "harness_no_local_ai": 3,
    "harness_local_ai": 3,
}
DEFAULT_DELEGATION_GATE = {
    "quality_noninferior": True,
    "minimum_median_wall_time_improvement_percent": 20,
    "maximum_median_provider_token_increase_percent": 25,
    "minimum_trials_per_arm": 3,
    "maximum_tokens_per_trial": 80000,
    "maximum_seconds_per_trial": 600,
    "required_token_provenance": "provider_telemetry",
    "fallback": "single-agent",
}
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REWORK_FIELDS = ("human_steering_turns", "repair_turns", "acceptance_retries", "total")
MAX_JSON_INPUT_BYTES = 64 * 1024 * 1024
MAX_CONTEXT_PACKET_BYTES = 64 * 1024
CONTEXT_INHERITANCE_MODES = ("fresh", "selected-turns", "full")


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        data = read_no_follow_bytes(
            path,
            "JSON input",
            max_bytes=MAX_JSON_INPUT_BYTES,
        )
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid JSON in {path}: {exc}") from None
    if not isinstance(value, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return value


def stable_json_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    path = path.expanduser()
    _assert_no_link_chain(path, "evidence file")
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        raise SystemExit(f"evidence file not found: {path}") from None
    if (
        stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or not stat.S_ISREG(metadata.st_mode)
        or int(getattr(metadata, "st_nlink", 1)) != 1
    ):
        raise SystemExit(f"evidence path must be a no-follow regular file: {path}")
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or int(getattr(opened, "st_nlink", 1)) != 1
            or (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise SystemExit(f"evidence file changed or resolved through an alias: {path}")
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_no_follow_bytes(path: Path, field: str, *, max_bytes: int = 64 * 1024 * 1024) -> bytes:
    path = path.expanduser()
    _assert_no_link_chain(path, field)
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        raise SystemExit(f"{field} not found: {path}") from None
    if (
        stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or not stat.S_ISREG(metadata.st_mode)
        or int(getattr(metadata, "st_nlink", 1)) != 1
    ):
        raise SystemExit(f"{field} must be a no-follow regular file: {path}")
    if metadata.st_size > max_bytes:
        raise SystemExit(f"{field} exceeds the {max_bytes}-byte evidence limit: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or int(getattr(opened, "st_nlink", 1)) != 1
            or (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise SystemExit(f"{field} changed or resolved through an alias: {path}")
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise SystemExit(f"{field} exceeds the {max_bytes}-byte evidence limit: {path}")
    return data


def read_no_follow_json(path: Path, field: str) -> tuple[dict[str, Any], str]:
    data = read_no_follow_bytes(path, field)
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid JSON in {field} {path}: {exc}") from None
    if not isinstance(value, dict):
        raise SystemExit(f"{field} must contain a JSON object: {path}")
    return value, hashlib.sha256(data).hexdigest()


def _is_reparse(entry_stat: os.stat_result) -> bool:
    return bool(int(getattr(entry_stat, "st_file_attributes", 0)) & REPARSE_POINT)


def _assert_no_link_chain(path: Path, field: str) -> None:
    current = Path(os.path.abspath(path.expanduser()))
    while True:
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            metadata = None
        except OSError as exc:
            raise SystemExit(f"unable to inspect {field} path {current}: {exc}") from None
        if metadata is not None and (stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata)):
            raise SystemExit(f"{field} must not use a link or reparse-point alias: {current}")
        parent = current.parent
        if parent == current:
            break
        current = parent


def tree_manifest(root: Path) -> list[dict[str, object]]:
    _assert_no_link_chain(root, "tree root")
    root = root.resolve(strict=True)
    root_metadata = os.lstat(root)
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or stat.S_ISLNK(root_metadata.st_mode)
        or _is_reparse(root_metadata)
    ):
        raise SystemExit(f"tree root must be a directory: {root}")
    rows: list[dict[str, object]] = []

    def visit(directory: Path, relative: Path) -> None:
        with os.scandir(directory) as entries:
            ordered = sorted(entries, key=lambda item: item.name)
        for entry in ordered:
            child_relative = relative / entry.name
            child_path = directory / entry.name
            metadata = entry.stat(follow_symlinks=False)
            portable = child_relative.as_posix()
            if entry.is_symlink() or _is_reparse(metadata):
                raise SystemExit(f"tree inputs must not contain links or reparse points: {portable}")
            if stat.S_ISDIR(metadata.st_mode):
                rows.append({"path": portable, "kind": "directory"})
                visit(child_path, child_relative)
            elif stat.S_ISREG(metadata.st_mode):
                rows.append(
                    {
                        "path": portable,
                        "kind": "file",
                        "bytes": metadata.st_size,
                        "sha256": file_sha256(child_path),
                    }
                )
            else:
                raise SystemExit(f"tree inputs must contain only regular files and directories: {portable}")

    visit(root, Path())
    return rows


def tree_sha256(root: Path) -> tuple[str, list[dict[str, object]]]:
    manifest = tree_manifest(root)
    return stable_json_hash(manifest), manifest


def resolved_path(base: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"{field} must be a non-empty path string")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    lexical = Path(os.path.abspath(path))
    _assert_no_link_chain(lexical, field)
    return lexical.resolve(strict=False)


def overlaps(left: Path, right: Path) -> bool:
    left = left.resolve(strict=False)
    right = right.resolve(strict=False)
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _required_string(mapping: dict[str, Any], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"{field} must be a non-empty string")
    return value.strip()


def requested_model_from_definition(definition: dict[str, Any]) -> dict[str, str]:
    field = "requested_model"
    value = definition.get(field)
    if not isinstance(value, dict):
        raise SystemExit("requested_model must be an object")
    normalized: dict[str, str] = {}
    for model_field in ("provider", "model", "reasoning_effort"):
        model_value = value.get(model_field)
        if not isinstance(model_value, str) or not model_value.strip():
            raise SystemExit(f"{field}.{model_field} must be a non-empty string")
        normalized[model_field] = model_value.strip()
    return normalized


def requested_model_from_protocol(protocol: dict[str, Any]) -> tuple[dict[str, Any], str]:
    return _object(protocol.get("requested_model")), "requested_model"


def arm_contracts_for_mode(mode: str) -> dict[str, dict[str, Any]]:
    return DELEGATION_ARM_CONTRACTS if mode == "delegation-economics" else ARM_CONTRACTS


def build_protocol(definition_path: Path, output_root: Path) -> dict[str, Any]:
    definition_path = resolved_path(Path.cwd(), str(definition_path), "definition")
    if not definition_path.is_file():
        raise SystemExit(f"definition must be an existing regular file: {definition_path}")
    definition = load_json_object(definition_path)
    if not _schema_version_one(definition.get("schema_version")):
        raise SystemExit("three-arm definition schema_version must be 1")
    benchmark_id = _required_string(definition, "benchmark_id")
    repetitions = definition.get("repetitions")
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 3:
        raise SystemExit("three-arm repetitions must be an integer of at least 3")
    benchmark_mode = str(definition.get("benchmark_mode", "harness-economics")).strip()
    if benchmark_mode not in {"harness-economics", "delegation-economics"}:
        raise SystemExit("benchmark_mode must be harness-economics or delegation-economics")
    arm_contracts = arm_contracts_for_mode(benchmark_mode)

    base = definition_path.parent
    task_prompt = resolved_path(base, definition.get("task_prompt"), "task_prompt")
    fixture_root = resolved_path(base, definition.get("fixture_root"), "fixture_root")
    coordinator_root = resolved_path(base, definition.get("coordinator_root"), "coordinator_root")
    harness_root = resolved_path(base, definition.get("harness_root"), "harness_root")
    if not task_prompt.is_file():
        raise SystemExit(f"task_prompt must be an existing regular file: {task_prompt}")
    if task_prompt.is_symlink() or _is_reparse(task_prompt.stat(follow_symlinks=False)):
        raise SystemExit("task_prompt must not be a link or reparse point")
    task_prompt_bytes = read_no_follow_bytes(task_prompt, "task prompt")
    try:
        task_prompt_text = task_prompt_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SystemExit(f"task_prompt must be valid UTF-8: {task_prompt}") from None
    if not fixture_root.is_dir():
        raise SystemExit(f"fixture_root must be an existing directory: {fixture_root}")
    if not harness_root.is_dir():
        raise SystemExit(f"harness_root must be an existing directory: {harness_root}")

    evaluator = definition.get("evaluator")
    if not isinstance(evaluator, dict):
        raise SystemExit("evaluator must be an object")
    evaluator_root = resolved_path(base, evaluator.get("root"), "evaluator.root")
    evaluator_argv = evaluator.get("argv")
    if (
        not evaluator_root.is_dir()
        or not isinstance(evaluator_argv, list)
        or not evaluator_argv
        or not all(isinstance(item, str) and item.strip() for item in evaluator_argv)
    ):
        raise SystemExit("evaluator requires an existing root and non-empty string argv array")

    output_root = resolved_path(Path.cwd(), str(output_root), "coordinator output root")
    if output_root == coordinator_root or not output_root.is_relative_to(coordinator_root):
        raise SystemExit(
            "coordinator output root must be strictly contained in the declared coordinator_root"
        )

    isolated_roots = {
        "fixture": fixture_root,
        "evaluator": evaluator_root,
        "coordinator": coordinator_root,
        "harness": harness_root,
    }
    root_items = list(isolated_roots.items())
    for index, (left_name, left_path) in enumerate(root_items):
        for right_name, right_path in root_items[index + 1 :]:
            if overlaps(left_path, right_path):
                raise SystemExit(
                    f"{left_name} root overlaps {right_name} root; benchmark roots must be isolated"
                )
    task_source_root = task_prompt.parent
    for name in ("evaluator", "coordinator", "harness"):
        if overlaps(task_prompt, isolated_roots[name]):
            raise SystemExit(f"task prompt overlaps {name} root; execution inputs must stay isolated")

    requested_model = requested_model_from_definition(definition)
    if benchmark_mode == "delegation-economics" and requested_model != DELEGATION_REQUESTED_THREAD_MODELS["direct"]["root"]:
        raise SystemExit(
            "delegation-economics requires requested root model openai gpt-5.6-sol with medium reasoning"
        )
    delegation_gate = definition.get("delegation_gate", DEFAULT_DELEGATION_GATE)
    if benchmark_mode == "delegation-economics" and delegation_gate != DEFAULT_DELEGATION_GATE:
        raise SystemExit("delegation_gate must match delegation-balanced-v1")

    workspaces = definition.get("workspaces")
    if not isinstance(workspaces, dict) or set(workspaces) != set(ARM_IDS):
        raise SystemExit("workspaces must declare exactly direct, harness_no_local_ai, and harness_local_ai")
    resolved_workspaces: dict[str, list[Path]] = {}
    all_workspace_paths: list[Path] = []
    for arm in ARM_IDS:
        values = workspaces.get(arm)
        if not isinstance(values, list) or len(values) != repetitions:
            raise SystemExit(f"workspaces.{arm} must contain exactly {repetitions} paths")
        arm_paths: list[Path] = []
        for index, value in enumerate(values, start=1):
            workspace = resolved_path(base, value, f"workspaces.{arm}[{index - 1}]")
            for existing in all_workspace_paths:
                if overlaps(workspace, existing):
                    raise SystemExit(
                        f"workspace paths must not overlap by equality or containment: {existing} and {workspace}"
                    )
            protected_paths = {
                "task source": task_source_root,
                "fixture root": fixture_root,
                "external evaluator root": evaluator_root,
                "coordinator root": coordinator_root,
                "coordinator output root": output_root,
                "source harness root": harness_root,
            }
            for protected_name, protected_path in protected_paths.items():
                if overlaps(workspace, protected_path):
                    raise SystemExit(f"workspace overlaps {protected_name}: {workspace}")
            all_workspace_paths.append(workspace)
            arm_paths.append(workspace)
        resolved_workspaces[arm] = arm_paths

    fixture_hash, fixture_manifest = tree_sha256(fixture_root)
    evaluator_hash, evaluator_manifest = tree_sha256(evaluator_root)
    harness_hash, _harness_manifest = tree_sha256(harness_root)
    identity = {
        "task_sha256": hashlib.sha256(task_prompt_bytes).hexdigest(),
        "fixture_sha256": fixture_hash,
        "evaluator_sha256": evaluator_hash,
        "harness_sha256": harness_hash,
    }
    protocol_seed_sha256 = stable_json_hash(
        {
            "benchmark_id": benchmark_id,
            "repetitions": repetitions,
            "identity": identity,
            "requested_model": requested_model,
            "evaluator_argv": list(evaluator_argv),
            "benchmark_mode": benchmark_mode,
            "arm_contracts": arm_contracts,
            "thread_models": DELEGATION_REQUESTED_THREAD_MODELS if benchmark_mode == "delegation-economics" else {},
            "thread_counts": DELEGATION_THREAD_COUNTS if benchmark_mode == "delegation-economics" else {},
            "delegation_gate": delegation_gate if benchmark_mode == "delegation-economics" else {},
        }
    )
    trials: list[dict[str, Any]] = []
    for arm in ARM_IDS:
        for index, workspace in enumerate(resolved_workspaces[arm], start=1):
            replicate_id = f"r{index:02d}"
            execution_nonce = stable_json_hash(
                {
                    "protocol_seed_sha256": protocol_seed_sha256,
                    "arm": arm,
                    "replicate_id": replicate_id,
                    "task_sha256": identity["task_sha256"],
                    "fixture_sha256": identity["fixture_sha256"],
                    "harness_sha256": identity["harness_sha256"],
                    "workspace": str(workspace),
                }
            )
            prompt_marker = execution_prompt_marker.build_marker(execution_nonce)
            submitted_prompt = execution_prompt_marker.build_prompt(task_prompt_text, prompt_marker)
            submitted_prompt_path = (
                output_root / "execution-prompts" / f"{arm}-{replicate_id}.txt"
            )
            submitted_prompt_sha256 = execution_prompt_marker.prompt_sha256(submitted_prompt)
            execution_input = {
                "benchmark_id": benchmark_id,
                "arm": arm,
                "replicate_id": replicate_id,
                "task_sha256": identity["task_sha256"],
                "fixture_sha256": identity["fixture_sha256"],
                "pre_state_sha256": identity["fixture_sha256"],
                "execution_nonce": execution_nonce,
                "execution_prompt_marker": prompt_marker,
                "execution_prompt_path": str(submitted_prompt_path),
                "execution_prompt_sha256": submitted_prompt_sha256,
                "workspace": str(workspace),
                "allowed_context_roles": arm_contracts[arm]["allowed_context_roles"],
            }
            if arm_contracts[arm]["harness_enabled"] is True:
                execution_input["harness_sha256"] = identity["harness_sha256"]
            trials.append(
                {
                    "arm": arm,
                    "replicate_id": replicate_id,
                    "workspace": str(workspace),
                    "execution_nonce": execution_nonce,
                    "execution_prompt_marker": prompt_marker,
                    "execution_prompt_path": str(submitted_prompt_path),
                    "execution_prompt_sha256": submitted_prompt_sha256,
                    "pre_state_sha256": identity["fixture_sha256"],
                    "execution_input_sha256": stable_json_hash(execution_input),
                }
            )

    core: dict[str, Any] = {
        "schema_version": 1,
        "tool": "agent-benchmarking.three-arm-full-run-protocol",
        "benchmark_id": benchmark_id,
        "benchmark_mode": benchmark_mode,
        "repetitions": repetitions,
        "protocol_seed_sha256": protocol_seed_sha256,
        "arms": list(ARM_IDS),
        "arm_contracts": arm_contracts,
        "arm_aliases": DELEGATION_ARM_ALIASES if benchmark_mode == "delegation-economics" else {},
        "thread_models": DELEGATION_REQUESTED_THREAD_MODELS if benchmark_mode == "delegation-economics" else {},
        "thread_counts": DELEGATION_THREAD_COUNTS if benchmark_mode == "delegation-economics" else {},
        "delegation_gate": delegation_gate if benchmark_mode == "delegation-economics" else {},
        "paths": {
            "definition": str(definition_path),
            "task_prompt": str(task_prompt),
            "fixture_root": str(fixture_root),
            "evaluator_root": str(evaluator_root),
            "coordinator_root": str(coordinator_root),
            "coordinator_output_root": str(output_root),
            "harness_root": str(harness_root),
        },
        "evaluator": {"argv": list(evaluator_argv)},
        "requested_model": requested_model,
        "identity": identity,
        "source_manifests": {
            "fixture": fixture_manifest,
            "evaluator": evaluator_manifest,
        },
        "trials": trials,
        "claim_policy": {
            "minimum_repetitions": 3,
            "require_all_acceptance_passed": True,
            "maximum_median_quality_drop": 0,
            "require_no_rework_regression": True,
            "require_no_paired_quality_regression": True,
            "require_every_paired_total_token_improvement": True,
        },
    }
    return {**core, "protocol_sha256": stable_json_hash(core)}


def trial_template(protocol: dict[str, Any], trial: dict[str, Any]) -> dict[str, Any]:
    arm = str(trial["arm"])
    delegated = (
        protocol.get("benchmark_mode") == "delegation-economics"
        and int(_object(protocol.get("thread_counts")).get(arm, 1) or 1) > 1
    )
    required_evidence = [
        "complete codex_usage_ledger provider telemetry row",
        "output manifest matching the isolated workspace tree",
        "isolation proof matching packet assertions",
        "post-run evaluator result matching packet acceptance fields",
        "local AI advisory proof when enabled",
        "provider invoice proof when measured cost is declared",
    ]
    if delegated:
        required_evidence.insert(
            1,
            "complete root/direct-child thread_tree with one recorded spawn event and provider telemetry per child",
        )
    template = {
        "schema_version": 1,
        "tool": "agent-benchmarking.three-arm-full-run-trial-template",
        "benchmark_id": protocol["benchmark_id"],
        "protocol_sha256": protocol["protocol_sha256"],
        "arm": arm,
        "replicate_id": trial["replicate_id"],
        "workspace": trial["workspace"],
        "expected_treatment": protocol["arm_contracts"][arm],
        "identity": {
            **protocol["identity"],
            "execution_input_sha256": trial["execution_input_sha256"],
        },
        "evidence_contract": {
            "boundary": "no-follow JSON files strictly inside coordinator_output_root",
            "required": required_evidence,
            "hash_policy": "recompute every declared SHA-256 from the durable evidence file",
        },
        "user_prompt_contract": {
            "task_prompt_path": protocol["paths"]["task_prompt"],
            "prepared_prompt_path": trial["execution_prompt_path"],
            "prepared_prompt_sha256": trial["execution_prompt_sha256"],
            "append_exact_final_line": trial["execution_prompt_marker"],
            "construction": "exact decoded task text + one newline + execution marker",
            "submit_as": "one user message",
        },
        "execution": "external-only; this template never launches an agent or model",
    }
    if protocol.get("benchmark_mode") == "delegation-economics":
        template["thread_tree_contract"] = {
            "required": delegated,
            "expected_thread_count": _object(protocol.get("thread_counts")).get(arm, 1),
            "requested_models": _object(_object(protocol.get("thread_models")).get(arm)),
            "max_depth": 1,
            "allowed_child_role": "worker",
            "require_spawn_event_sha256": True,
            "require_spawn_event_evidence_path": True,
            "context_inheritance_modes": list(CONTEXT_INHERITANCE_MODES),
            "require_prompt_hashes": True,
            "require_durable_exact_child_prompt": True,
            "require_child_prompt_path": True,
            "require_child_prompt_sha256": True,
            "require_child_prompt_bytes": True,
            "require_bounded_evidence_packet_for": ["fresh", "selected-turns"],
            "maximum_evidence_packet_bytes": MAX_CONTEXT_PACKET_BYTES,
            "require_complete_provider_telemetry_per_thread": True,
        }
    return template


def prepare_protocol(definition_path: Path, output_root: Path, *, write: bool = False) -> dict[str, Any]:
    output_root = resolved_path(Path.cwd(), str(output_root), "coordinator output root")
    protocol = build_protocol(definition_path, output_root)
    protocol_path = output_root / "protocol.json"
    template_paths = [
        output_root / "trial-templates" / f"{trial['arm']}-{trial['replicate_id']}.json"
        for trial in protocol["trials"]
    ]
    execution_prompt_paths = [
        Path(str(trial["execution_prompt_path"]))
        for trial in protocol["trials"]
    ]
    trial_packet_paths = [
        output_root / "trial-packets" / f"{trial['arm']}-{trial['replicate_id']}.json"
        for trial in protocol["trials"]
    ]
    trial_index_path = output_root / "trial-index.json"
    trial_index = {
        "schema_version": 1,
        "tool": "agent-benchmarking.three-arm-full-run-trial-index",
        "benchmark_id": protocol["benchmark_id"],
        "protocol_sha256": protocol["protocol_sha256"],
        "trial_paths": [str(path) for path in trial_packet_paths],
    }
    task_prompt_path = Path(str(protocol["paths"]["task_prompt"]))
    task_prompt_bytes = read_no_follow_bytes(task_prompt_path, "task prompt")
    if hashlib.sha256(task_prompt_bytes).hexdigest() != protocol["identity"]["task_sha256"]:
        raise SystemExit("task prompt changed while preparing execution prompts")
    try:
        task_prompt_text = task_prompt_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SystemExit(f"task prompt must be valid UTF-8: {task_prompt_path}") from None
    execution_prompts = [
        execution_prompt_marker.build_prompt(
            task_prompt_text,
            str(trial["execution_prompt_marker"]),
        )
        for trial in protocol["trials"]
    ]
    for trial, prompt in zip(protocol["trials"], execution_prompts):
        if execution_prompt_marker.prompt_sha256(prompt) != trial["execution_prompt_sha256"]:
            raise SystemExit("execution prompt hash changed while preparing trial artifacts")
    if write:
        _assert_no_link_chain(output_root, "coordinator output root")
        if output_root.exists() and (not output_root.is_dir() or any(output_root.iterdir())):
            raise SystemExit(f"coordinator output root must be absent or empty: {output_root}")
        output_root.mkdir(parents=True, exist_ok=True)
        common.write_json(protocol_path, protocol)
        common.write_json(trial_index_path, trial_index)
        for trial, path in zip(protocol["trials"], template_paths):
            common.write_json(path, trial_template(protocol, trial))
        for path, prompt in zip(execution_prompt_paths, execution_prompts):
            common.write_text(path, prompt)
    return {
        "schema_version": 1,
        "tool": "agent-benchmarking.three-arm-full-run",
        "mode": "prepare",
        "ok": True,
        "write_performed": write,
        "protocol_path": str(protocol_path),
        "trial_template_paths": [str(path) for path in template_paths],
        "execution_prompt_paths": [str(path) for path in execution_prompt_paths],
        "trial_index_path": str(trial_index_path),
        "trial_index": trial_index,
        "protocol": protocol,
        "execution_started": False,
        "network_used": False,
    }


def protocol_hash_issues(protocol: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    recorded = protocol.get("protocol_sha256")
    core = dict(protocol)
    core.pop("protocol_sha256", None)
    if not isinstance(recorded, str) or recorded != stable_json_hash(core):
        issues.append("protocol_sha256 does not match the canonical protocol")
    if not _schema_version_one(protocol.get("schema_version")):
        issues.append("protocol schema_version must be 1")
    if protocol.get("tool") != "agent-benchmarking.three-arm-full-run-protocol":
        issues.append("protocol tool is invalid")
    if not isinstance(protocol.get("benchmark_id"), str) or not str(protocol.get("benchmark_id", "")).strip():
        issues.append("protocol benchmark_id must be a non-empty string")
    if protocol.get("arms") != list(ARM_IDS):
        issues.append("protocol arms must be the fixed three-arm order")
    benchmark_mode = str(protocol.get("benchmark_mode", "harness-economics"))
    if benchmark_mode not in {"harness-economics", "delegation-economics"}:
        issues.append("protocol benchmark_mode is invalid")
    expected_arm_contracts = arm_contracts_for_mode(benchmark_mode)
    if protocol.get("arm_contracts") != expected_arm_contracts:
        issues.append("protocol arm_contracts must match the fixed treatment contracts")
    if benchmark_mode == "delegation-economics":
        if protocol.get("arm_aliases") != DELEGATION_ARM_ALIASES:
            issues.append("protocol delegation arm aliases are invalid")
        if protocol.get("thread_models") != DELEGATION_REQUESTED_THREAD_MODELS:
            issues.append("protocol delegation thread models are invalid")
        if protocol.get("thread_counts") != DELEGATION_THREAD_COUNTS:
            issues.append("protocol delegation thread counts are invalid")
        if protocol.get("delegation_gate") != DEFAULT_DELEGATION_GATE:
            issues.append("protocol delegation gate is invalid")
    repetitions = protocol.get("repetitions")
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 3:
        issues.append("protocol repetitions must be at least 3")
    requested_model, requested_model_field = requested_model_from_protocol(protocol)
    for field in ("provider", "model", "reasoning_effort"):
        if not isinstance(requested_model.get(field), str) or not str(requested_model.get(field, "")).strip():
            issues.append(f"protocol {requested_model_field}.{field} must be a non-empty string")
    identity = _object(protocol.get("identity"))
    for field in ("task_sha256", "fixture_sha256", "evaluator_sha256", "harness_sha256"):
        if not _sha256(identity.get(field)):
            issues.append(f"protocol identity.{field} must be a lowercase SHA-256")
    evaluator = _object(protocol.get("evaluator"))
    evaluator_argv = evaluator.get("argv")
    if (
        not isinstance(evaluator_argv, list)
        or not evaluator_argv
        or not all(isinstance(value, str) and value for value in evaluator_argv)
    ):
        issues.append("protocol evaluator.argv must be a non-empty string array")
        evaluator_argv = []
    paths = _object(protocol.get("paths"))
    for field in (
        "definition",
        "task_prompt",
        "fixture_root",
        "evaluator_root",
        "coordinator_root",
        "coordinator_output_root",
        "harness_root",
    ):
        value = paths.get(field)
        if not isinstance(value, str) or not value.strip() or not Path(value).is_absolute():
            issues.append(f"protocol paths.{field} must be a non-empty absolute path")
    expected_claim_policy = {
        "minimum_repetitions": 3,
        "require_all_acceptance_passed": True,
        "maximum_median_quality_drop": 0,
        "require_no_rework_regression": True,
        "require_no_paired_quality_regression": True,
        "require_every_paired_total_token_improvement": True,
    }
    if protocol.get("claim_policy") != expected_claim_policy:
        issues.append("protocol claim_policy must match the fixed conservative claim policy")
    source_manifests = _object(protocol.get("source_manifests"))
    if not isinstance(source_manifests.get("fixture"), list):
        issues.append("protocol source_manifests.fixture must be an array")
    if not isinstance(source_manifests.get("evaluator"), list):
        issues.append("protocol source_manifests.evaluator must be an array")

    expected_seed_fields = {
        "benchmark_id": protocol.get("benchmark_id"),
        "repetitions": repetitions,
        "identity": identity,
        requested_model_field: requested_model,
        "evaluator_argv": evaluator_argv,
        "benchmark_mode": benchmark_mode,
        "arm_contracts": expected_arm_contracts,
        "thread_models": DELEGATION_REQUESTED_THREAD_MODELS if benchmark_mode == "delegation-economics" else {},
        "thread_counts": DELEGATION_THREAD_COUNTS if benchmark_mode == "delegation-economics" else {},
        "delegation_gate": DEFAULT_DELEGATION_GATE if benchmark_mode == "delegation-economics" else {},
    }
    expected_seed = stable_json_hash(expected_seed_fields)
    protocol_seed = protocol.get("protocol_seed_sha256")
    if protocol_seed != expected_seed:
        issues.append("protocol_seed_sha256 does not match the claim-bearing protocol fields")
    trials = protocol.get("trials") if isinstance(protocol.get("trials"), list) else []
    if not isinstance(protocol.get("trials"), list):
        issues.append("protocol trials must be an array")
    if isinstance(repetitions, int) and len(trials) != len(ARM_IDS) * repetitions:
        issues.append("protocol trial count does not match arms times repetitions")
    seen_keys: set[tuple[str, str]] = set()
    seen_workspaces: list[Path] = []
    for index, trial_value in enumerate(trials):
        if not isinstance(trial_value, dict):
            issues.append(f"protocol trial[{index}] must be an object")
            continue
        arm = trial_value.get("arm")
        replicate_id = trial_value.get("replicate_id")
        if arm not in ARM_IDS:
            issues.append(f"protocol trial[{index}].arm is invalid")
        if not isinstance(replicate_id, str) or not re.fullmatch(r"r[0-9]{2,}", replicate_id):
            issues.append(f"protocol trial[{index}].replicate_id is invalid")
        key = (str(arm), str(replicate_id))
        if key in seen_keys:
            issues.append(f"duplicate protocol trial: {key[0]}/{key[1]}")
        seen_keys.add(key)
        workspace_value = trial_value.get("workspace")
        if not isinstance(workspace_value, str) or not workspace_value or not Path(workspace_value).is_absolute():
            issues.append(f"protocol trial[{index}].workspace must be an absolute path")
            workspace_value = ""
        elif "\x00" in workspace_value:
            issues.append(f"protocol trial[{index}].workspace contains a NUL byte")
        else:
            workspace_path = Path(workspace_value)
            if any(overlaps(workspace_path, existing) for existing in seen_workspaces):
                issues.append(f"protocol trial[{index}].workspace overlaps another trial workspace")
            seen_workspaces.append(workspace_path)
        nonce = trial_value.get("execution_nonce")
        prompt_marker = trial_value.get("execution_prompt_marker")
        prompt_path_value = trial_value.get("execution_prompt_path")
        prompt_sha256 = trial_value.get("execution_prompt_sha256")
        pre_state = trial_value.get("pre_state_sha256")
        execution_input_sha256 = trial_value.get("execution_input_sha256")
        for field, value in (
            ("execution_nonce", nonce),
            ("execution_prompt_sha256", prompt_sha256),
            ("pre_state_sha256", pre_state),
            ("execution_input_sha256", execution_input_sha256),
        ):
            if not _sha256(value):
                issues.append(f"protocol trial[{index}].{field} must be a lowercase SHA-256")
        if pre_state != identity.get("fixture_sha256"):
            issues.append(f"protocol trial[{index}].pre_state_sha256 must equal fixture_sha256")
        expected_prompt_path = ""
        coordinator_output_value = paths.get("coordinator_output_root")
        if (
            isinstance(prompt_path_value, str)
            and prompt_path_value
            and Path(prompt_path_value).is_absolute()
            and isinstance(coordinator_output_value, str)
            and coordinator_output_value
            and isinstance(arm, str)
            and isinstance(replicate_id, str)
        ):
            expected_prompt_path = str(
                Path(coordinator_output_value)
                / "execution-prompts"
                / f"{arm}-{replicate_id}.txt"
            )
            if prompt_path_value != expected_prompt_path:
                issues.append(f"protocol trial[{index}].execution_prompt_path is not canonical")
        else:
            issues.append(f"protocol trial[{index}].execution_prompt_path must be an absolute path")
        if arm in ARM_IDS and isinstance(replicate_id, str) and workspace_value:
            expected_nonce = stable_json_hash(
                {
                    "protocol_seed_sha256": protocol_seed,
                    "arm": arm,
                    "replicate_id": replicate_id,
                    "task_sha256": identity.get("task_sha256"),
                    "fixture_sha256": identity.get("fixture_sha256"),
                    "harness_sha256": identity.get("harness_sha256"),
                    "workspace": workspace_value,
                }
            )
            if nonce != expected_nonce:
                issues.append(f"protocol trial[{index}].execution_nonce does not match its inputs")
            expected_prompt_marker = execution_prompt_marker.build_marker(expected_nonce)
            if prompt_marker != expected_prompt_marker:
                issues.append(
                    f"protocol trial[{index}].execution_prompt_marker does not match execution_nonce"
                )
            execution_input = {
                "benchmark_id": protocol.get("benchmark_id"),
                "arm": arm,
                "replicate_id": replicate_id,
                "task_sha256": identity.get("task_sha256"),
                "fixture_sha256": identity.get("fixture_sha256"),
                "pre_state_sha256": identity.get("fixture_sha256"),
                "execution_nonce": nonce,
                "execution_prompt_marker": prompt_marker,
                "execution_prompt_path": prompt_path_value,
                "execution_prompt_sha256": prompt_sha256,
                "workspace": workspace_value,
                "allowed_context_roles": expected_arm_contracts[arm]["allowed_context_roles"],
            }
            if expected_arm_contracts[arm]["harness_enabled"] is True:
                execution_input["harness_sha256"] = identity.get("harness_sha256")
            if execution_input_sha256 != stable_json_hash(execution_input):
                issues.append(f"protocol trial[{index}].execution_input_sha256 does not match its inputs")
    if isinstance(repetitions, int) and repetitions >= 3:
        expected_keys = {
            (arm, f"r{index:02d}")
            for arm in ARM_IDS
            for index in range(1, repetitions + 1)
        }
        if seen_keys != expected_keys:
            issues.append("protocol trial keys do not exactly cover every arm and replicate")
    return issues


def protocol_source_identity_issues(protocol: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    paths = _object(protocol.get("paths"))
    identity = _object(protocol.get("identity"))
    checks = (
        ("task_prompt", "task_sha256", "file", "task prompt source hash"),
        ("fixture_root", "fixture_sha256", "tree", "fixture source hash"),
        ("evaluator_root", "evaluator_sha256", "tree", "external evaluator source hash"),
        ("harness_root", "harness_sha256", "tree", "source harness hash"),
    )
    for path_field, hash_field, kind, label in checks:
        try:
            source = resolved_path(Path.cwd(), paths.get(path_field), f"protocol paths.{path_field}")
            actual = file_sha256(source) if kind == "file" else tree_sha256(source)[0]
        except (OSError, SystemExit, ValueError) as exc:
            issues.append(f"{label} cannot be verified: {exc}")
            continue
        if actual != identity.get(hash_field):
            issues.append(f"{label} changed after protocol preparation")
    try:
        task_path = resolved_path(Path.cwd(), paths.get("task_prompt"), "protocol paths.task_prompt")
        task_bytes = read_no_follow_bytes(task_path, "task prompt")
        task_text = task_bytes.decode("utf-8-sig")
    except (OSError, SystemExit, UnicodeDecodeError, ValueError) as exc:
        issues.append(f"execution prompt source cannot be verified: {exc}")
        return issues
    trials = protocol.get("trials") if isinstance(protocol.get("trials"), list) else []
    for index, trial in enumerate(trials):
        if not isinstance(trial, dict):
            continue
        try:
            expected_prompt = execution_prompt_marker.build_prompt(
                task_text,
                str(trial.get("execution_prompt_marker", "")),
            )
        except ValueError as exc:
            issues.append(f"protocol trial[{index}] execution prompt cannot be built: {exc}")
            continue
        expected_hash = execution_prompt_marker.prompt_sha256(expected_prompt)
        if trial.get("execution_prompt_sha256") != expected_hash:
            issues.append(f"protocol trial[{index}].execution_prompt_sha256 does not match task text")
        try:
            prompt_path = resolved_path(
                Path.cwd(),
                trial.get("execution_prompt_path"),
                f"protocol trial[{index}].execution_prompt_path",
            )
            prompt_bytes = read_no_follow_bytes(prompt_path, "prepared execution prompt")
        except (OSError, SystemExit, ValueError) as exc:
            issues.append(f"protocol trial[{index}] prepared execution prompt cannot be verified: {exc}")
            continue
        if prompt_bytes != expected_prompt.encode("utf-8"):
            issues.append(f"protocol trial[{index}] prepared execution prompt bytes do not match task text")
    return issues


def preflight_protocol(protocol_path: Path, *, live: bool = False) -> dict[str, Any]:
    protocol_path = resolved_path(Path.cwd(), str(protocol_path), "protocol")
    protocol = load_json_object(protocol_path)
    issues = protocol_hash_issues(protocol)
    issues.extend(protocol_source_identity_issues(protocol))
    paths = protocol.get("paths") if isinstance(protocol.get("paths"), dict) else {}
    identity = protocol.get("identity") if isinstance(protocol.get("identity"), dict) else {}

    def checked_path(name: str, *, kind: str, required: bool = True) -> Path:
        try:
            candidate = resolved_path(Path.cwd(), paths.get(name), f"protocol paths.{name}")
        except (OSError, SystemExit, ValueError) as exc:
            issues.append(str(exc))
            return protocol_path.parent
        if required:
            if kind == "file" and not candidate.is_file():
                issues.append(f"protocol paths.{name} is not an existing regular file: {candidate}")
            elif kind == "directory" and not candidate.is_dir():
                issues.append(f"protocol paths.{name} is not an existing directory: {candidate}")
        return candidate

    task_prompt = checked_path("task_prompt", kind="file")
    fixture_root = checked_path("fixture_root", kind="directory")
    evaluator_root = checked_path("evaluator_root", kind="directory")
    coordinator_root = checked_path("coordinator_root", kind="directory", required=False)
    coordinator_output_root = checked_path(
        "coordinator_output_root", kind="directory", required=False
    )
    harness_root = checked_path("harness_root", kind="directory")

    if (
        coordinator_output_root == coordinator_root
        or not coordinator_output_root.is_relative_to(coordinator_root)
    ):
        issues.append("coordinator output root is not strictly contained in coordinator root")
    isolated_roots = {
        "fixture": fixture_root,
        "evaluator": evaluator_root,
        "coordinator": coordinator_root,
        "harness": harness_root,
    }
    root_items = list(isolated_roots.items())
    for index, (left_name, left_path) in enumerate(root_items):
        for right_name, right_path in root_items[index + 1 :]:
            if overlaps(left_path, right_path):
                issues.append(f"{left_name} root overlaps {right_name} root")

    trials = protocol.get("trials") if isinstance(protocol.get("trials"), list) else []
    expected_contracts = arm_contracts_for_mode(
        str(protocol.get("benchmark_mode", "harness-economics"))
    )
    workspace_paths: list[Path] = []
    for trial in trials:
        if not isinstance(trial, dict):
            issues.append("protocol trial must be an object")
            continue
        arm = str(trial.get("arm", ""))
        try:
            workspace = resolved_path(
                Path.cwd(), trial.get("workspace"), f"{arm} execution workspace"
            )
        except (OSError, SystemExit, ValueError) as exc:
            issues.append(str(exc))
            continue
        workspace_paths.append(workspace)
        if overlaps(workspace, task_prompt.parent):
            issues.append(f"{arm} workspace overlaps task source: {workspace}")
        if overlaps(workspace, fixture_root):
            issues.append(f"{arm} workspace overlaps target fixture root: {workspace}")
        if overlaps(workspace, coordinator_root):
            issues.append(f"{arm} workspace overlaps coordinator root: {workspace}")
        if overlaps(workspace, evaluator_root):
            issues.append(f"{arm} workspace overlaps external evaluator root: {workspace}")
        if overlaps(workspace, harness_root):
            issues.append(f"{arm} workspace overlaps source harness root: {workspace}")
        if overlaps(workspace, coordinator_output_root):
            issues.append(f"{arm} workspace overlaps coordinator output root: {workspace}")
        if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
            issues.append(f"execution workspace must be absent or empty before launch: {workspace}")
        contract = protocol.get("arm_contracts", {}).get(arm, {}) if isinstance(protocol.get("arm_contracts"), dict) else {}
        if contract != expected_contracts.get(arm):
            issues.append(f"{arm} treatment contract differs from the fixed contract")
        roles = contract.get("allowed_context_roles") if isinstance(contract, dict) else []
        if arm == "direct" and roles != ["ordinary-task", "target-fixture"]:
            issues.append("direct evaluator/harness context was not withheld")

    for index, left in enumerate(workspace_paths):
        for right in workspace_paths[index + 1 :]:
            if overlaps(left, right):
                issues.append(
                    f"execution workspace paths overlap by equality or containment: {left} and {right}"
                )

    return {
        "schema_version": 1,
        "tool": "agent-benchmarking.three-arm-full-run",
        "mode": "preflight",
        "ok": not issues,
        "status": "ready-for-external-execution" if not issues else "blocked",
        "benchmark_id": protocol.get("benchmark_id", ""),
        "protocol_sha256": protocol.get("protocol_sha256", ""),
        "trial_count": len(trials),
        "issues": sorted(set(issues)),
        "live_prerequisites_checked": live,
        "execution_started": False,
        "network_used": False,
        "model_invoked": False,
        "subprocess_invoked": False,
        "boundary": (
            "Preflight validates local files, hashes, roots, isolation, and fixed treatments only. "
            "Even with --live it never launches an agent, provider request, model, local AI, network call, or subprocess."
        ),
    }


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _schema_version_one(value: object) -> bool:
    return type(value) is int and value == 1


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _sha256(value: object) -> bool:
    return isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None


def _inside(parent: Path, child: Path) -> bool:
    parent = parent.resolve(strict=False)
    child = child.resolve(strict=False)
    return child != parent and child.is_relative_to(parent)


def durable_json_evidence(
    mapping: dict[str, Any],
    *,
    path_field: str,
    sha_field: str,
    label: str,
    coordinator_output_root: Path,
    issue,
) -> dict[str, Any] | None:
    raw_path = mapping.get(path_field)
    recorded_hash = mapping.get(sha_field)
    if not isinstance(raw_path, str) or not raw_path.strip():
        issue(f"{label} {path_field} must name a durable evidence file")
        return None
    if not _sha256(recorded_hash):
        issue(f"{label} {sha_field} must be a lowercase SHA-256")
        return None
    try:
        path = resolved_path(Path.cwd(), raw_path, f"{label} evidence")
        if not _inside(coordinator_output_root, path):
            issue(f"{label} evidence must be contained in coordinator_output_root: {path}")
            return None
        evidence, actual_hash = read_no_follow_json(path, f"{label} evidence")
        if actual_hash != recorded_hash:
            issue(f"{label} evidence SHA-256 does not match the durable file")
            return None
        return evidence
    except (OSError, SystemExit, ValueError) as exc:
        issue(f"{label} evidence is unavailable or unsafe: {exc}")
        return None


def durable_file_evidence(
    mapping: dict[str, Any],
    *,
    path_field: str,
    sha_field: str,
    bytes_field: str,
    label: str,
    coordinator_output_root: Path,
    issue,
    max_bytes: int,
) -> bytes | None:
    raw_path = mapping.get(path_field)
    recorded_hash = mapping.get(sha_field)
    recorded_bytes = mapping.get(bytes_field)
    if not isinstance(raw_path, str) or not raw_path.strip():
        issue(f"{label} {path_field} must name a durable evidence file")
        return None
    if not _sha256(recorded_hash):
        issue(f"{label} {sha_field} must be a lowercase SHA-256")
        return None
    if not _nonnegative_int(recorded_bytes) or int(recorded_bytes) <= 0:
        issue(f"{label} {bytes_field} must be a positive integer")
        return None
    try:
        path = resolved_path(Path.cwd(), raw_path, f"{label} evidence")
        if not _inside(coordinator_output_root, path):
            issue(f"{label} evidence must be contained in coordinator_output_root: {path}")
            return None
        data = read_no_follow_bytes(path, f"{label} evidence", max_bytes=max_bytes)
        if hashlib.sha256(data).hexdigest() != recorded_hash:
            issue(f"{label} evidence SHA-256 does not match the durable file")
            return None
        if len(data) != recorded_bytes:
            issue(f"{label} evidence byte count does not match the durable file")
            return None
        return data
    except (OSError, SystemExit, ValueError) as exc:
        issue(f"{label} evidence is unavailable or unsafe: {exc}")
        return None


def rollout_trace_observation(
    data: bytes,
    execution_nonce: str,
    expected_prompt: str,
) -> dict[str, Any]:
    core_fields = ("input_tokens", "output_tokens", "total_tokens")
    detail_fields = (
        "cached_input_tokens",
        "cache_write_input_tokens",
        "reasoning_output_tokens",
    )
    totals: dict[str, int | None] = {field: 0 for field in token_v1.USAGE_TOKEN_FIELDS}
    detail_available = {field: True for field in detail_fields}
    event_count = 0
    malformed_line_count = 0
    timestamps: list[str] = []
    prompt_scope = execution_prompt_marker.scope_observation(
        data,
        expected_prompt if _sha256(execution_nonce) else "",
    )
    nonce_occurrences = int(prompt_scope["occurrence_count"])
    model_observations: set[tuple[str, str, str]] = set()
    for raw_line in data.splitlines():
        try:
            value = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            malformed_line_count += 1
            continue
        if not isinstance(value, dict):
            malformed_line_count += 1
            continue
        payload = value.get("payload")
        if value.get("type") == "turn_context" and isinstance(payload, dict):
            provider = str(payload.get("model_provider") or payload.get("provider") or "").strip()
            model = str(payload.get("model") or "").strip()
            reasoning = str(payload.get("reasoning_effort") or "").strip()
            if provider and model:
                model_observations.add((provider, model, reasoning))
        info = payload.get("info") if isinstance(payload, dict) else None
        usage = info.get("last_token_usage") if isinstance(info, dict) else None
        if not isinstance(usage, dict):
            continue
        if not all(_nonnegative_int(usage.get(field)) for field in core_fields):
            malformed_line_count += 1
            continue
        input_tokens = int(usage["input_tokens"])
        output_tokens = int(usage["output_tokens"])
        if int(usage["total_tokens"]) != input_tokens + output_tokens:
            malformed_line_count += 1
            continue
        details: dict[str, int | None] = {}
        invalid_detail = False
        for field in detail_fields:
            raw = usage.get(field)
            if raw is None:
                details[field] = None
            elif not _nonnegative_int(raw):
                invalid_detail = True
                break
            else:
                details[field] = int(raw)
        if invalid_detail:
            malformed_line_count += 1
            continue
        cache_read = details["cached_input_tokens"] or 0
        cache_write = details["cache_write_input_tokens"] or 0
        reasoning = details["reasoning_output_tokens"]
        if (
            cache_read + cache_write > input_tokens
            or (reasoning is not None and reasoning > output_tokens)
        ):
            malformed_line_count += 1
            continue
        event_count += 1
        for field in core_fields:
            totals[field] = int(totals[field] or 0) + int(usage[field])
        for field in detail_fields:
            raw = details[field]
            if raw is None:
                detail_available[field] = False
                totals[field] = None
            elif detail_available[field]:
                totals[field] = int(totals[field] or 0) + int(raw)
        timestamp = str(value.get("timestamp", "")).strip()
        if timestamp:
            timestamps.append(timestamp)
    model_observation = {}
    if len(model_observations) == 1:
        provider, model, reasoning = next(iter(model_observations))
        model_observation = {
            "provider": provider,
            "model": model,
            "reasoning_effort": reasoning,
        }
    return {
        "event_count": event_count,
        "malformed_line_count": malformed_line_count,
        "totals": totals,
        "nonce_occurrence_count": nonce_occurrences,
        "execution_prompt_scope": prompt_scope,
        "first_usage_timestamp": timestamps[0] if timestamps else "",
        "last_usage_timestamp": timestamps[-1] if timestamps else "",
        "model_observation": model_observation,
    }


def packet_thread_rows(packet: dict[str, Any]) -> list[dict[str, Any]]:
    tree = _object(packet.get("thread_tree"))
    rows = tree.get("threads")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    thread = packet.get("thread")
    return [thread] if isinstance(thread, dict) else []


def thread_evidence_identities(
    rows: list[dict[str, Any]],
    *,
    issue: Any,
) -> list[dict[str, object]]:
    identities: list[dict[str, object]] = []
    for row in rows:
        thread_id = str(row.get("id", "")).strip() or "<missing>"
        measurement = _object(row.get("token_measurement"))
        evidence = _object(measurement.get("evidence"))
        identity: dict[str, object] = {"thread_id": thread_id}
        try:
            rollout_path = resolved_path(
                Path.cwd(),
                evidence.get("source_path"),
                f"thread {thread_id} token measurement rollout",
            )
            opened = os.lstat(rollout_path)
        except (OSError, SystemExit, ValueError) as exc:
            issue(f"thread {thread_id} rollout identity is unavailable or unsafe: {exc}")
        else:
            identity["rollout_path"] = os.path.normcase(str(rollout_path))
            identity["rollout_file"] = (int(opened.st_dev), int(opened.st_ino))
            identity["rollout_sha256"] = str(evidence.get("source_sha256", ""))
        try:
            ledger_path = resolved_path(
                Path.cwd(),
                row.get("telemetry_evidence_path"),
                f"thread {thread_id} provider telemetry",
            )
        except (OSError, SystemExit, ValueError) as exc:
            issue(f"thread {thread_id} provider telemetry selector is unavailable or unsafe: {exc}")
        else:
            identity["telemetry_selector"] = (
                os.path.normcase(str(ledger_path)),
                str(row.get("telemetry_evidence_label", "")),
            )
        identities.append(identity)
    return identities


def reject_reused_thread_evidence(
    identities: list[dict[str, object]],
    *,
    issue: Any,
    scope: str,
) -> None:
    for field, label in (
        ("rollout_path", "rollout path"),
        ("rollout_file", "rollout file identity"),
        ("rollout_sha256", "rollout SHA-256"),
        ("telemetry_selector", "telemetry ledger selector"),
    ):
        seen: dict[object, str] = {}
        for identity in identities:
            value = identity.get(field)
            if value in {None, "", ("", "")}:
                continue
            thread_id = str(identity.get("thread_id", "<missing>"))
            previous = seen.get(value)
            if previous is not None:
                issue(f"{scope} reuses {label} for threads {previous} and {thread_id}")
            else:
                seen[value] = thread_id


def requested_thread_model(
    protocol: dict[str, Any],
    arm: str,
    role: str,
) -> dict[str, Any]:
    configured = _object(_object(protocol.get("thread_models")).get(arm))
    selected = configured.get(role)
    if isinstance(selected, dict):
        return _object(selected)
    requested, _field = requested_model_from_protocol(protocol)
    return requested


def thread_tree_rows(
    packet: dict[str, Any],
    issue: Any,
    *,
    coordinator_output_root: Path,
) -> tuple[
    dict[str, Any] | None,
    list[dict[str, Any]],
    dict[str, tuple[str, str]],
]:
    tree = packet.get("thread_tree")
    if tree is None:
        return None, [], {}
    if not isinstance(tree, dict):
        issue("thread_tree must be an object")
        return {}, [], {}
    values = tree.get("threads")
    edges = tree.get("spawn_edges")
    if not isinstance(values, list) or not values:
        issue("thread_tree.threads must be a non-empty list")
        values = []
    if not isinstance(edges, list):
        issue("thread_tree.spawn_edges must be a list")
        edges = []
    threads = [value for value in values if isinstance(value, dict)]
    if len(threads) != len(values):
        issue("thread_tree.threads entries must be objects")
    ids = [str(thread.get("id", "")).strip() for thread in threads]
    if any(not thread_id for thread_id in ids):
        issue("thread_tree thread id must be non-empty")
    duplicate_ids = sorted({thread_id for thread_id in ids if thread_id and ids.count(thread_id) > 1})
    for thread_id in duplicate_ids:
        issue(f"duplicate thread id {thread_id} inside thread_tree")
    root_id = str(tree.get("root_thread_id", "")).strip()
    roots = [
        thread
        for thread in threads
        if thread.get("parent_id") is None and thread.get("role") == "root"
    ]
    root = next((thread for thread in roots if thread.get("id") == root_id), None)
    if len(roots) != 1 or root is None:
        issue("thread_tree must contain exactly one declared root thread")
    child_ids = {
        str(thread.get("id", ""))
        for thread in threads
        if thread is not root and str(thread.get("id", ""))
    }
    expected_edges: set[tuple[str, str]] = set()
    child_prompts: dict[str, tuple[str, str]] = {}
    for thread in threads:
        if thread is root:
            continue
        child_id = str(thread.get("id", ""))
        parent_id = thread.get("parent_id")
        if thread.get("role") != "worker":
            issue(f"thread_tree child {child_id or '<missing>'} role must be worker")
        if parent_id in child_ids:
            issue(f"thread_tree child {child_id or '<missing>'} is recursively spawned")
        elif parent_id != root_id:
            issue(f"thread_tree child {child_id or '<missing>'} parent must be the root thread")
        if child_id:
            expected_edges.add((root_id, child_id))
        if not isinstance(thread.get("agent_name"), str) or not str(thread.get("agent_name", "")).strip():
            issue(f"thread_tree child {child_id or '<missing>'} agent_name must be non-empty")
    observed_edges: list[tuple[str, str]] = []
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            issue(f"thread_tree.spawn_edges[{index}] must be an object")
            continue
        parent_id = str(edge.get("parent_id", ""))
        child_id = str(edge.get("child_id", ""))
        pair = (parent_id, child_id)
        observed_edges.append(pair)
        if parent_id in child_ids:
            issue(f"thread_tree spawn edge for {child_id or '<missing>'} is recursively spawned")
        if not _sha256(edge.get("spawn_event_sha256")):
            issue(f"thread_tree spawn edge for {child_id or '<missing>'} lacks a valid spawn event SHA-256")
        spawn_event = durable_json_evidence(
            edge,
            path_field="spawn_event_path",
            sha_field="spawn_event_sha256",
            label=f"thread_tree spawn event for {child_id or '<missing>'}",
            coordinator_output_root=coordinator_output_root,
            issue=issue,
        )
        if spawn_event is not None:
            label = f"thread_tree spawn event for {child_id or '<missing>'}"
            context_inheritance = spawn_event.get("context_inheritance")
            expected_fields = {
                "schema_version",
                "tool",
                "event_type",
                "parent_id",
                "child_id",
                "source_rollout_sha256",
                "context_inheritance",
                "parent_prompt_sha256",
                "child_prompt_sha256",
                "child_prompt_path",
                "child_prompt_bytes",
            }
            if context_inheritance in {"fresh", "selected-turns"}:
                expected_fields.update(
                    {
                        "evidence_packet_path",
                        "evidence_packet_sha256",
                        "evidence_packet_bytes",
                    }
                )
            if set(spawn_event) != expected_fields:
                issue(f"{label} fields must match the V1 context-provenance contract")
            if (
                not _schema_version_one(spawn_event.get("schema_version"))
                or spawn_event.get("tool") != "agent-benchmarking.spawn-event"
                or spawn_event.get("event_type") != "subagent-spawn"
                or spawn_event.get("parent_id") != parent_id
                or spawn_event.get("child_id") != child_id
                or spawn_event.get("source_rollout_sha256") != _object(root).get("rollout_sha256")
            ):
                issue(f"{label} is not bound to the root rollout")
            if context_inheritance not in CONTEXT_INHERITANCE_MODES:
                issue(f"{label} context_inheritance must be fresh, selected-turns, or full")
            if spawn_event.get("parent_prompt_sha256") != _object(root).get(
                "execution_prompt_sha256"
            ):
                issue(f"{label} parent prompt hash is not bound to the root thread")
            if not _sha256(spawn_event.get("child_prompt_sha256")):
                issue(f"{label} child_prompt_sha256 must be a lowercase SHA-256")
            child_prompt_data = durable_file_evidence(
                spawn_event,
                path_field="child_prompt_path",
                sha_field="child_prompt_sha256",
                bytes_field="child_prompt_bytes",
                label=f"{label} exact child prompt",
                coordinator_output_root=coordinator_output_root,
                issue=issue,
                max_bytes=MAX_CONTEXT_PACKET_BYTES,
            )
            if child_prompt_data is not None:
                try:
                    child_prompt_text = child_prompt_data.decode("utf-8-sig")
                except UnicodeDecodeError:
                    issue(f"{label} exact child prompt must be valid UTF-8")
                else:
                    if not child_prompt_text:
                        issue(f"{label} exact child prompt must not be empty")
                    elif child_id:
                        child_prompts[child_id] = (
                            str(spawn_event["child_prompt_sha256"]),
                            child_prompt_text,
                        )
            if context_inheritance in {"fresh", "selected-turns"}:
                durable_file_evidence(
                    spawn_event,
                    path_field="evidence_packet_path",
                    sha_field="evidence_packet_sha256",
                    bytes_field="evidence_packet_bytes",
                    label=f"{label} bounded context packet",
                    coordinator_output_root=coordinator_output_root,
                    issue=issue,
                    max_bytes=MAX_CONTEXT_PACKET_BYTES,
                )
    for pair in sorted(expected_edges - set(observed_edges)):
        issue(f"thread_tree child {pair[1]} has an unproven spawn edge")
    for pair in sorted(set(observed_edges) - expected_edges):
        issue(f"thread_tree has an unexpected spawn edge {pair[0]} -> {pair[1]}")
    for pair in sorted({pair for pair in observed_edges if observed_edges.count(pair) > 1}):
        issue(f"thread_tree has duplicate spawn edge {pair[0]} -> {pair[1]}")
    return root or {}, threads, child_prompts


def validate_child_thread_evidence(
    thread: dict[str, Any],
    *,
    protocol: dict[str, Any],
    arm: str,
    expected_nonce: str,
    expected_prompt_sha256: str,
    expected_prompt: str,
    workspace: Path,
    coordinator_output_root: Path,
    issue: Any,
) -> None:
    thread_id = str(thread.get("id", "")).strip()
    prefix = f"thread_tree child {thread_id or '<missing>'}"
    if not _nonnegative_int(thread.get("usage_event_count")) or int(thread.get("usage_event_count") or 0) <= 0:
        issue(f"{prefix} usage_event_count must be greater than zero")
    if thread.get("execution_nonce") != expected_nonce:
        issue(f"{prefix} execution_nonce does not match the protocol trial")
    if thread.get("execution_prompt_sha256") != expected_prompt_sha256:
        issue(f"{prefix} execution prompt does not match the exact submitted prompt")
    try:
        child_cwd = resolved_path(Path.cwd(), thread.get("cwd"), f"{prefix} cwd")
    except (OSError, SystemExit, ValueError):
        child_cwd = Path()
    if child_cwd != workspace:
        issue(f"{prefix} cwd must match the isolated trial workspace")
    requested_model = requested_thread_model(protocol, arm, "worker")
    for field, expected_field, label_name in (
        ("observed_provider", "provider", "provider"),
        ("observed_model", "model", "observed model"),
        ("observed_reasoning_effort", "reasoning_effort", "observed reasoning effort"),
    ):
        if thread.get(field) != requested_model.get(expected_field):
            issue(f"{prefix} {label_name} does not match the protocol")
    measurement = thread.get("token_measurement")
    measurement_gate = token_v1.gate_eligibility(
        measurement,
        gate_scope="full_run",
        evidence_already_verified=True,
    )
    if measurement_gate.get("eligible") is not True:
        reasons = measurement_gate.get("reasons") or ["ineligible measurement"]
        issue(f"{prefix} lacks verifier-bound provider telemetry: {reasons[0]}")
    telemetry = durable_json_evidence(
        thread,
        path_field="telemetry_evidence_path",
        sha_field="model_evidence_sha256",
        label=f"{prefix} provider telemetry evidence",
        coordinator_output_root=coordinator_output_root,
        issue=issue,
    )
    if telemetry is None:
        return
    telemetry_label = thread.get("telemetry_evidence_label")
    row = _object(_object(telemetry.get("arms")).get(telemetry_label))
    observation = _object(row.get("model_observation"))
    if (
        telemetry.get("tool") != "agent-benchmarking.codex-usage-ledger"
        or telemetry.get("ok") is not True
        or _object(telemetry.get("measurement_scope")).get("complete_for_full_run_trials") is not True
        or not row
    ):
        issue(f"{prefix} provider telemetry is incomplete")
    if row.get("thread_id") != thread_id:
        issue(f"{prefix} provider telemetry thread id does not match")
    if row.get("event_count") != thread.get("usage_event_count"):
        issue(f"{prefix} provider telemetry usage count does not match")
    if row.get("source") != "state-sqlite":
        issue(f"{prefix} provider telemetry source must be state-sqlite")
    try:
        telemetry_cwd = resolved_path(
            Path.cwd(),
            row.get("cwd"),
            f"{prefix} provider telemetry cwd",
        )
    except (OSError, SystemExit, ValueError) as exc:
        issue(f"{prefix} provider telemetry cwd is unavailable or unsafe: {exc}")
    else:
        if telemetry_cwd != workspace:
            issue(f"{prefix} provider telemetry cwd does not match the isolated workspace")
    try:
        rollout_path = resolved_path(Path.cwd(), row.get("rollout_path"), f"{prefix} rollout")
        rollout_data = read_no_follow_bytes(rollout_path, f"{prefix} rollout")
    except (OSError, SystemExit, ValueError) as exc:
        issue(f"{prefix} rollout is unavailable or unsafe: {exc}")
        return
    rollout_sha256 = hashlib.sha256(rollout_data).hexdigest()
    if row.get("rollout_sha256") != rollout_sha256 or thread.get("rollout_sha256") != rollout_sha256:
        issue(f"{prefix} rollout SHA-256 does not match")
    measurement_evidence = _object(_object(measurement).get("evidence"))
    try:
        measurement_source = resolved_path(
            Path.cwd(), measurement_evidence.get("source_path"), f"{prefix} measurement evidence"
        )
    except (OSError, SystemExit, ValueError) as exc:
        issue(f"{prefix} measurement evidence path is unavailable or unsafe: {exc}")
    else:
        if measurement_source != rollout_path:
            issue(f"{prefix} measurement evidence path does not match the verified rollout")
    if measurement_evidence.get("source_sha256") != rollout_sha256:
        issue(f"{prefix} measurement evidence SHA-256 does not match the verified rollout")
    trace = rollout_trace_observation(rollout_data, expected_nonce, expected_prompt)
    if trace["event_count"] != thread.get("usage_event_count") or trace["malformed_line_count"] != 0:
        issue(f"{prefix} rollout usage evidence is incomplete")
    if trace["nonce_occurrence_count"] <= 0:
        issue(f"{prefix} rollout does not contain the exact submitted prompt")
    if isinstance(measurement, dict) and trace["totals"] != token_v1.usage_counts(measurement):
        issue(f"{prefix} rollout token totals do not match TokenMeasurementV1")
    raw_observation = _object(trace.get("model_observation"))
    for raw_field, packet_field in (
        ("provider", "observed_provider"),
        ("model", "observed_model"),
        ("reasoning_effort", "observed_reasoning_effort"),
    ):
        if raw_observation.get(raw_field) != thread.get(packet_field):
            issue(f"{prefix} raw rollout {raw_field} does not match the packet")
    if row.get("token_measurement") != measurement or row.get("state_tokens_used") != _object(measurement).get("total_tokens"):
        issue(f"{prefix} provider telemetry token measurement does not match")
    for evidence_field, packet_field in (
        ("provider", "observed_provider"),
        ("model", "observed_model"),
        ("reasoning_effort", "observed_reasoning_effort"),
    ):
        if observation.get(evidence_field) != thread.get(packet_field):
            issue(f"{prefix} provider telemetry {evidence_field} does not match")
    if (
        observation.get("complete") is not True
        or observation.get("source") != "codex-rollout-turn-context"
        or observation.get("missing") != []
    ):
        issue(f"{prefix} provider telemetry model observation is incomplete")


def _trial_label(packet: dict[str, Any], path: Path) -> str:
    arm = str(packet.get("arm", "missing-arm"))
    replicate = str(packet.get("replicate_id", "missing-replicate"))
    return f"{arm}/{replicate} ({path})"


def validate_trial_packet(
    packet: dict[str, Any],
    path: Path,
    protocol: dict[str, Any],
    expected_trial: dict[str, Any] | None,
) -> list[str]:
    label = _trial_label(packet, path)
    issues: list[str] = []

    def issue(message: str) -> None:
        issues.append(f"{label}: {message}")

    try:
        coordinator_output_root = resolved_path(
            Path.cwd(),
            _object(protocol.get("paths")).get("coordinator_output_root"),
            "protocol coordinator_output_root",
        )
    except (OSError, SystemExit, ValueError) as exc:
        issue(f"protocol coordinator_output_root is unavailable or unsafe: {exc}")
        coordinator_output_root = path.parent
    protocol_paths = _object(protocol.get("paths"))

    if not _schema_version_one(packet.get("schema_version")):
        issue("schema_version must be 1")
    if packet.get("tool") != "agent-benchmarking.three-arm-full-run-trial":
        issue("tool must be agent-benchmarking.three-arm-full-run-trial")
    if packet.get("benchmark_id") != protocol.get("benchmark_id"):
        issue("benchmark_id does not match protocol")
    if packet.get("protocol_sha256") != protocol.get("protocol_sha256"):
        issue("protocol_sha256 does not match protocol")
    arm = str(packet.get("arm", ""))
    if arm not in ARM_IDS:
        issue("arm is not one of the fixed three arms")
    expected_prompt = ""
    expected_prompt_marker = ""
    expected_prompt_sha256 = ""
    if expected_trial is None:
        issue("arm/replicate key is not declared by the protocol")
        expected_nonce = ""
    else:
        expected_nonce = str(expected_trial.get("execution_nonce", ""))
        expected_prompt_marker = str(expected_trial.get("execution_prompt_marker", ""))
        expected_prompt_sha256 = str(expected_trial.get("execution_prompt_sha256", ""))
        if str(packet.get("workspace", "")) != str(expected_trial.get("workspace", "")):
            issue("workspace does not match protocol trial workspace")
        if not _sha256(expected_nonce):
            issue("protocol trial execution_nonce is not a lowercase SHA-256")
        try:
            task_path = resolved_path(
                Path.cwd(),
                protocol_paths.get("task_prompt"),
                "protocol task prompt",
            )
            task_text = read_no_follow_bytes(task_path, "task prompt").decode("utf-8-sig")
            expected_prompt = execution_prompt_marker.build_prompt(
                task_text,
                expected_prompt_marker,
            )
        except (OSError, SystemExit, UnicodeDecodeError, ValueError) as exc:
            issue(f"protocol execution prompt cannot be reconstructed: {exc}")
        else:
            if execution_prompt_marker.prompt_sha256(expected_prompt) != expected_prompt_sha256:
                issue("protocol execution_prompt_sha256 does not match the exact submitted prompt")

    workspace_valid = True
    try:
        workspace = resolved_path(Path.cwd(), packet.get("workspace"), "trial workspace")
    except (OSError, SystemExit, ValueError) as exc:
        issue(f"trial workspace is unavailable or unsafe: {exc}")
        workspace = path.parent
        workspace_valid = False
    if workspace_valid:
        protected = (
            ("task source", "task_prompt", True),
            ("fixture root", "fixture_root", False),
            ("external evaluator root", "evaluator_root", False),
            ("coordinator root", "coordinator_root", False),
            ("coordinator output root", "coordinator_output_root", False),
            ("source harness root", "harness_root", False),
        )
        for protected_name, field, use_parent in protected:
            try:
                protected_path = resolved_path(
                    Path.cwd(), protocol_paths.get(field), f"protocol paths.{field}"
                )
            except (OSError, SystemExit, ValueError) as exc:
                issue(f"{protected_name} cannot be verified: {exc}")
                continue
            if use_parent:
                protected_path = protected_path.parent
            if overlaps(workspace, protected_path):
                issue(f"trial workspace overlaps {protected_name}: {workspace}")
    tree_root, tree_threads, child_prompts = thread_tree_rows(
        packet,
        issue,
        coordinator_output_root=coordinator_output_root,
    )
    if tree_root is not None:
        trial_identities = thread_evidence_identities(tree_threads, issue=issue)
        reject_reused_thread_evidence(
            trial_identities,
            issue=issue,
            scope="thread_tree",
        )
    single_thread = _object(packet.get("thread"))
    configured_thread_counts = _object(protocol.get("thread_counts"))
    if arm in configured_thread_counts:
        expected_thread_count = configured_thread_counts.get(arm)
        actual_thread_count = len(tree_threads) if tree_root is not None else 1
        if (
            not isinstance(expected_thread_count, int)
            or isinstance(expected_thread_count, bool)
            or expected_thread_count < 1
        ):
            issue("protocol expected thread count is invalid")
        elif actual_thread_count != expected_thread_count:
            issue(
                f"thread tree count {actual_thread_count} does not match the protocol count "
                f"{expected_thread_count}"
            )
    if tree_root is not None:
        thread = dict(tree_root)
        if single_thread and single_thread.get("id") != thread.get("id"):
            issue("thread.id does not match thread_tree.root_thread_id")
        if "provider" not in thread:
            thread["provider"] = thread.get("observed_provider")
        thread_measurement = thread.get("token_measurement")
    else:
        thread = single_thread
        thread_measurement = packet.get("token_measurement")
    for child in tree_threads:
        if child is tree_root:
            continue
        child_prompt_sha256, child_prompt = child_prompts.get(
            str(child.get("id", "")),
            ("", ""),
        )
        validate_child_thread_evidence(
            child,
            protocol=protocol,
            arm=arm,
            expected_nonce=expected_nonce,
            expected_prompt_sha256=child_prompt_sha256,
            expected_prompt=child_prompt,
            workspace=workspace,
            coordinator_output_root=coordinator_output_root,
            issue=issue,
        )
    if tree_root is not None:
        measurements = [
            thread_row.get("token_measurement")
            for thread_row in tree_threads
            if isinstance(thread_row, dict)
        ]
        complete_measurements = [
            measurement
            for measurement in measurements
            if isinstance(measurement, dict)
            and token_v1.gate_eligibility(
                measurement,
                gate_scope="full_run",
                evidence_already_verified=True,
            ).get("eligible")
            is True
            and measurement.get("provenance") == "provider_telemetry"
        ]
        if len(complete_measurements) != len(tree_threads):
            issue("thread_tree does not contain complete provider telemetry for every thread")
        aggregate_measurement = _object(packet.get("token_measurement"))
        aggregate_counts = token_v1.usage_counts(aggregate_measurement)
        measurement_counts = [token_v1.usage_counts(item) for item in measurements]
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            expected_total = sum(
                int(counts.get(field, 0) or 0)
                for counts in measurement_counts
                if _nonnegative_int(counts.get(field))
            )
            if aggregate_counts.get(field) != expected_total:
                issue(f"thread_tree aggregate {field} does not equal the root and child sum")
        for usage_field, detail_field in (
            ("cached_input_tokens", "cache_read_input_tokens"),
            ("cache_write_input_tokens", "cache_write_input_tokens"),
            ("reasoning_output_tokens", "reasoning_output_tokens"),
        ):
            expected_detail = token_v1.aggregate_detail(measurements, detail_field)
            observed_detail = _object(aggregate_measurement.get("details")).get(detail_field)
            if observed_detail != expected_detail:
                issue(
                    f"thread_tree aggregate {usage_field} does not preserve the summed availability lattice"
                )
    thread_id = thread.get("id")
    if not isinstance(thread_id, str) or not thread_id.strip():
        issue("thread.id must be a non-empty telemetry-visible id")
    event_count = thread.get("usage_event_count")
    if not _nonnegative_int(event_count) or int(event_count) <= 0:
        issue("thread.usage_event_count must be greater than zero")
    if thread.get("execution_nonce") != expected_nonce:
        issue("thread.execution_nonce does not match the protocol trial")
    if thread.get("execution_prompt_sha256") != expected_prompt_sha256:
        issue("thread.execution_prompt_sha256 does not match the exact submitted prompt")
    try:
        thread_cwd = resolved_path(Path.cwd(), thread.get("cwd"), "thread.cwd")
    except (OSError, SystemExit, ValueError):
        thread_cwd = Path()
    if thread_cwd != workspace:
        issue("thread.cwd must match the isolated trial workspace")
    requested_model = requested_thread_model(protocol, arm, "root")
    for field, label_name in (
        ("provider", "provider"),
        ("observed_model", "observed model"),
        ("observed_reasoning_effort", "observed reasoning effort"),
    ):
        expected_field = "model" if field == "observed_model" else (
            "reasoning_effort" if field == "observed_reasoning_effort" else "provider"
        )
        if thread.get(field) != requested_model.get(expected_field):
            issue(f"thread {label_name} does not match the protocol's observed exact model configuration")
    if not _sha256(thread.get("model_evidence_sha256")):
        issue("thread.model_evidence_sha256 must be a lowercase SHA-256")
    observed_rollout_sha256 = ""
    telemetry = durable_json_evidence(
        thread,
        path_field="telemetry_evidence_path",
        sha_field="model_evidence_sha256",
        label="provider telemetry evidence",
        coordinator_output_root=coordinator_output_root,
        issue=issue,
    )
    if telemetry is not None:
        telemetry_label = thread.get("telemetry_evidence_label")
        telemetry_scope = _object(telemetry.get("measurement_scope"))
        telemetry_arms = _object(telemetry.get("arms"))
        if not isinstance(telemetry_label, str) or not telemetry_label:
            issue("provider telemetry evidence label must be a non-empty string")
            telemetry_row = {}
        else:
            telemetry_row = _object(telemetry_arms.get(telemetry_label))
        observation = _object(telemetry_row.get("model_observation"))
        if (
            telemetry.get("tool") != "agent-benchmarking.codex-usage-ledger"
            or telemetry.get("ok") is not True
            or telemetry_scope.get("complete_for_full_run_trials") is not True
            or not telemetry_row
        ):
            issue("provider telemetry evidence is not a complete Codex usage ledger row")
        if telemetry_row.get("thread_id") != thread_id:
            issue("provider telemetry evidence thread_id does not match the packet")
        if telemetry_row.get("event_count") != event_count:
            issue("provider telemetry evidence event_count does not match the packet")
        if telemetry_row.get("source") != "state-sqlite":
            issue("provider telemetry evidence must come from the Codex state database")
        try:
            telemetry_cwd = resolved_path(
                Path.cwd(), telemetry_row.get("cwd"), "provider telemetry cwd"
            )
        except (OSError, SystemExit, ValueError) as exc:
            issue(f"provider telemetry cwd is unavailable or unsafe: {exc}")
        else:
            if telemetry_cwd != workspace:
                issue("provider telemetry cwd does not match the isolated workspace")
        try:
            rollout_path = resolved_path(
                Path.cwd(), telemetry_row.get("rollout_path"), "provider rollout"
            )
            rollout_data = read_no_follow_bytes(rollout_path, "provider rollout")
        except (OSError, SystemExit, ValueError) as exc:
            issue(f"provider rollout is unavailable or unsafe: {exc}")
        else:
            rollout_sha256 = hashlib.sha256(rollout_data).hexdigest()
            observed_rollout_sha256 = rollout_sha256
            if telemetry_row.get("rollout_sha256") != rollout_sha256:
                issue("provider telemetry rollout_sha256 does not match the rollout bytes")
            measurement_evidence = _object(_object(thread_measurement).get("evidence"))
            try:
                measurement_source = resolved_path(
                    Path.cwd(),
                    measurement_evidence.get("source_path"),
                    "token measurement evidence",
                )
            except (OSError, SystemExit, ValueError) as exc:
                issue(f"token measurement evidence path is unavailable or unsafe: {exc}")
            else:
                if measurement_source != rollout_path:
                    issue("token measurement evidence path does not match the verified rollout")
            if measurement_evidence.get("source_sha256") != rollout_sha256:
                issue("token measurement evidence SHA-256 does not match the verified rollout")
            trace = rollout_trace_observation(rollout_data, expected_nonce, expected_prompt)
            if trace["event_count"] != event_count:
                issue("provider rollout usage event count does not match the packet")
            if trace["malformed_line_count"] != 0:
                issue("provider rollout contains malformed or incomplete JSONL evidence")
            if trace["nonce_occurrence_count"] <= 0:
                issue("provider rollout does not contain the exact complete trial user prompt")
            raw_observation = _object(trace.get("model_observation"))
            for raw_field, packet_field in (
                ("provider", "provider"),
                ("model", "observed_model"),
                ("reasoning_effort", "observed_reasoning_effort"),
            ):
                if raw_observation.get(raw_field) != thread.get(packet_field):
                    issue(f"provider rollout raw {raw_field} does not match the packet")
            prompt_scope = _object(trace.get("execution_prompt_scope"))
            if prompt_scope.get("fresh_thread_scope") is not True:
                issue(
                    "provider rollout must begin with the exact benchmark user prompt and no prior usage"
                )
            prompt_evidence = _object(telemetry_row.get("execution_prompt"))
            if (
                prompt_evidence.get("observed") is not True
                or prompt_evidence.get("source") != "structured-user-prompt-events"
                or prompt_evidence.get("binding") != "exact-complete-user-prompt"
                or prompt_evidence.get("prompt_sha256") != expected_prompt_sha256
                or prompt_evidence.get("occurrence_count") != trace["nonce_occurrence_count"]
                or prompt_evidence.get("first_structured_user_message_observed")
                != prompt_scope.get("first_structured_user_message_observed")
                or prompt_evidence.get("first_structured_user_message_matches")
                != prompt_scope.get("first_structured_user_message_matches")
                or prompt_evidence.get("usage_events_before_first_prompt")
                != prompt_scope.get("usage_events_before_first_prompt")
                or prompt_evidence.get("unsupported_user_context_before_or_with_prompt")
                != prompt_scope.get("unsupported_user_context_before_or_with_prompt")
                or prompt_evidence.get("fresh_thread_scope")
                != prompt_scope.get("fresh_thread_scope")
            ):
                issue("provider telemetry execution prompt does not match the rollout")
            measurement = _object(thread_measurement)
            if trace["totals"] != token_v1.usage_counts(measurement):
                issue("provider rollout token totals do not match TokenMeasurementV1")
            if telemetry_row.get("first_usage_timestamp") != trace["first_usage_timestamp"]:
                issue("provider telemetry first usage timestamp does not match the rollout")
            if telemetry_row.get("last_usage_timestamp") != trace["last_usage_timestamp"]:
                issue("provider telemetry last usage timestamp does not match the rollout")
        if telemetry_row.get("malformed_line_count") != 0 or telemetry_row.get("read_errors") != []:
            issue("provider telemetry evidence reports malformed lines or read errors")
        measurement = _object(thread_measurement)
        if telemetry_row.get("state_tokens_used") != measurement.get("total_tokens"):
            issue("provider telemetry state token total does not match TokenMeasurementV1")
        for evidence_field, packet_field in (
            ("provider", "provider"),
            ("model", "observed_model"),
            ("reasoning_effort", "observed_reasoning_effort"),
        ):
            if observation.get(evidence_field) != thread.get(packet_field):
                issue(f"provider telemetry evidence {evidence_field} does not match the packet")
        if (
            observation.get("complete") is not True
            or observation.get("source") != "codex-rollout-turn-context"
            or observation.get("missing") != []
        ):
            issue("provider telemetry model observation is incomplete, ambiguous, or not rollout-observed")
        if telemetry_row.get("token_measurement") != thread_measurement:
            issue("provider telemetry evidence token measurement does not match the packet")

    identity = _object(packet.get("identity"))
    protocol_identity = _object(protocol.get("identity"))
    for field in ("task_sha256", "fixture_sha256", "evaluator_sha256", "harness_sha256"):
        if identity.get(field) != protocol_identity.get(field):
            issue(f"identity.{field} does not match protocol {field}")
    if expected_trial is not None and identity.get("execution_input_sha256") != expected_trial.get("execution_input_sha256"):
        issue("identity.execution_input_sha256 does not match protocol trial input")
    if identity.get("execution_nonce") != expected_nonce:
        issue("identity.execution_nonce does not match the protocol trial")
    if identity.get("execution_prompt_sha256") != expected_prompt_sha256:
        issue("identity.execution_prompt_sha256 does not match the exact submitted prompt")
    if expected_trial is not None and identity.get("pre_state_sha256") != expected_trial.get("pre_state_sha256"):
        issue("identity.pre_state_sha256 does not match the protocol trial")
    if not _sha256(identity.get("output_manifest_sha256")):
        issue("identity.output_manifest_sha256 must be a lowercase SHA-256")
    output_manifest = durable_json_evidence(
        identity,
        path_field="output_manifest_path",
        sha_field="output_manifest_sha256",
        label="output manifest",
        coordinator_output_root=coordinator_output_root,
        issue=issue,
    )
    if output_manifest is not None and (
        not _schema_version_one(output_manifest.get("schema_version"))
        or output_manifest.get("arm") != arm
        or output_manifest.get("replicate_id") != packet.get("replicate_id")
        or not isinstance(output_manifest.get("entries"), list)
        or output_manifest.get("execution_nonce") != expected_nonce
        or output_manifest.get("execution_prompt_sha256") != expected_prompt_sha256
        or output_manifest.get("workspace") != str(packet.get("workspace", ""))
        or output_manifest.get("pre_state_sha256") != identity.get("pre_state_sha256")
        or output_manifest.get("execution_input_sha256") != identity.get("execution_input_sha256")
        or output_manifest.get("thread_id") != thread_id
        or output_manifest.get("rollout_sha256") != observed_rollout_sha256
    ):
        issue("output manifest evidence does not cross-link this trial execution")
    elif output_manifest is not None:
        try:
            actual_output_entries = tree_manifest(workspace)
        except (OSError, SystemExit, ValueError) as exc:
            issue(f"output manifest entries cannot be verified against the workspace: {exc}")
        else:
            if output_manifest.get("entries") != actual_output_entries:
                issue("output manifest entries do not match the isolated workspace tree")
            if output_manifest.get("post_state_sha256") != stable_json_hash(actual_output_entries):
                issue("output manifest post_state_sha256 does not match the workspace tree")

    isolation = _object(packet.get("isolation"))
    if isolation.get("execution_nonce") != expected_nonce:
        issue("isolation.execution_nonce does not match the protocol trial")
    if isolation.get("workspace") != packet.get("workspace"):
        issue("isolation.workspace does not match the protocol trial")
    preflight_receipt = durable_json_evidence(
        isolation,
        path_field="preflight_receipt_path",
        sha_field="preflight_receipt_sha256",
        label="preflight receipt",
        coordinator_output_root=coordinator_output_root,
        issue=issue,
    )
    expected_relationships = {
        "task_source": "distinct",
        "fixture_root": "distinct",
        "evaluator_root": "distinct",
        "coordinator_root": "distinct",
        "coordinator_output_root": "distinct",
        "harness_root": "distinct",
    }
    if preflight_receipt is not None and (
        not _schema_version_one(preflight_receipt.get("schema_version"))
        or preflight_receipt.get("execution_nonce") != expected_nonce
        or preflight_receipt.get("arm") != arm
        or preflight_receipt.get("replicate_id") != packet.get("replicate_id")
        or preflight_receipt.get("workspace") != packet.get("workspace")
        or preflight_receipt.get("workspace_no_links") is not True
        or preflight_receipt.get("execution_input_sha256")
        != (expected_trial or {}).get("execution_input_sha256")
        or preflight_receipt.get("task_sha256") != protocol_identity.get("task_sha256")
        or preflight_receipt.get("execution_prompt_sha256") != expected_prompt_sha256
        or preflight_receipt.get("pre_state_sha256")
        != (expected_trial or {}).get("pre_state_sha256")
        or preflight_receipt.get("protected_root_relationships") != expected_relationships
    ):
        issue("preflight receipt does not bind the pristine isolated trial input")
    if isolation.get("thread_cwd_matches") is not True:
        issue("isolation.thread_cwd_matches must be true")
    if not _sha256(isolation.get("proof_sha256")):
        issue("isolation.proof_sha256 must be a lowercase SHA-256")
    isolation_proof = durable_json_evidence(
        isolation,
        path_field="proof_path",
        sha_field="proof_sha256",
        label="isolation proof",
        coordinator_output_root=coordinator_output_root,
        issue=issue,
    )
    expected_isolation_proof = {
        key: value
        for key, value in isolation.items()
        if key not in {"proof_path", "proof_sha256"}
    }
    if isolation_proof is not None and isolation_proof != expected_isolation_proof:
        issue("isolation proof evidence does not match the packet's isolation assertions")
    if isolation.get("prompt_sha256") != expected_prompt_sha256:
        issue("isolation.prompt_sha256 must equal the exact submitted prompt hash")
    if arm == "direct":
        if isolation.get("workspace_outside_harness") is not True:
            issue("direct workspace_outside_harness must be true")
        for field in (
            "workflow_context_paths",
            "skill_context_paths",
            "routing_context_paths",
            "context_evidence_paths",
            "procedure_context_paths",
            "trace_accessed_harness_paths",
        ):
            if isolation.get(field) != []:
                issue(f"direct {field} must be empty")
        if isolation.get("evaluator_disclosed") is not False:
            issue("direct evaluator must be withheld during execution")

    treatment = _object(packet.get("treatment"))
    if treatment.get("execution_nonce") != expected_nonce:
        issue("treatment.execution_nonce does not match the protocol trial")
    expected_treatment = _object(_object(protocol.get("arm_contracts")).get(arm))
    for field in ("harness_enabled", "local_ai_enabled"):
        if treatment.get(field) is not expected_treatment.get(field):
            issue(f"treatment.{field} does not match the fixed {arm} treatment")
    invocation_count = treatment.get("local_ai_invocation_count")
    invocation_ids = treatment.get("local_ai_invocation_ids")
    if (
        not isinstance(invocation_ids, list)
        or not all(isinstance(value, str) and value for value in invocation_ids)
        or len(invocation_ids) != len(set(invocation_ids))
    ):
        issue("treatment.local_ai_invocation_ids must be unique non-empty strings")
        invocation_ids = []
    if not _nonnegative_int(invocation_count):
        issue("treatment.local_ai_invocation_count must be a non-negative integer")
    elif expected_treatment.get("local_ai_enabled") is True:
        if int(invocation_count) <= 0:
            issue(f"{arm} must record at least one local AI advisory invocation")
        if len(invocation_ids) != int(invocation_count):
            issue(f"{arm} invocation ids must match the invocation count")
        if not _sha256(treatment.get("local_ai_evidence_sha256")):
            issue(f"{arm} local_ai_evidence_sha256 must be a lowercase SHA-256")
        local_ai_evidence = durable_json_evidence(
            treatment,
            path_field="local_ai_evidence_path",
            sha_field="local_ai_evidence_sha256",
            label="local AI evidence",
            coordinator_output_root=coordinator_output_root,
            issue=issue,
        )
        if local_ai_evidence is not None and (
            local_ai_evidence.get("arm") != arm
            or local_ai_evidence.get("replicate_id") != packet.get("replicate_id")
            or local_ai_evidence.get("execution_nonce") != expected_nonce
            or local_ai_evidence.get("thread_id") != thread_id
            or local_ai_evidence.get("rollout_sha256") != observed_rollout_sha256
            or local_ai_evidence.get("harness_sha256") != protocol_identity.get("harness_sha256")
            or local_ai_evidence.get("invocation_count") != invocation_count
            or local_ai_evidence.get("invocation_ids") != invocation_ids
            or local_ai_evidence.get("advisory_only") is not True
        ):
            issue("local AI evidence does not match the advisory treatment")
    elif int(invocation_count) != 0 or invocation_ids != []:
        issue(f"{arm} must record zero local AI invocations and ids")

    evaluator = _object(packet.get("evaluator"))
    evaluator_links = {
        "execution_nonce": expected_nonce,
        "arm": arm,
        "replicate_id": packet.get("replicate_id"),
        "thread_id": thread_id,
        "rollout_sha256": observed_rollout_sha256,
        "output_manifest_sha256": identity.get("output_manifest_sha256"),
        "evaluator_source_sha256": protocol_identity.get("evaluator_sha256"),
        "evaluator_argv": _object(protocol.get("evaluator")).get("argv"),
    }
    for field, expected_value in evaluator_links.items():
        if evaluator.get(field) != expected_value:
            issue(f"evaluator.{field} does not cross-link the trial execution")
    if evaluator.get("sha256") != protocol_identity.get("evaluator_sha256"):
        issue("evaluator sha256 does not match protocol")
    if not _sha256(evaluator.get("result_sha256")):
        issue("evaluator.result_sha256 must be a lowercase SHA-256")
    evaluator_result = durable_json_evidence(
        evaluator,
        path_field="result_path",
        sha_field="result_sha256",
        label="evaluator result",
        coordinator_output_root=coordinator_output_root,
        issue=issue,
    )
    expected_evaluator_result = {
        key: value
        for key, value in evaluator.items()
        if key not in {"result_path", "result_sha256"}
    }
    if evaluator_result is not None and evaluator_result != expected_evaluator_result:
        issue("evaluator result evidence does not match the packet's acceptance result")
    if not isinstance(evaluator.get("passed"), bool):
        issue("evaluator.passed must be boolean")
    score = evaluator.get("score")
    if not _finite_number(score) or not 0 <= float(score) <= 1:
        issue("evaluator.score must be a finite number between 0 and 1")
    checks_passed = evaluator.get("checks_passed")
    checks_total = evaluator.get("checks_total")
    if (
        not _nonnegative_int(checks_passed)
        or not _nonnegative_int(checks_total)
        or int(checks_total or 0) <= 0
        or int(checks_passed or 0) > int(checks_total or 0)
    ):
        issue("evaluator check counts are invalid")
    if evaluator.get("evaluated_after_execution") is not True:
        issue("external evaluator must run after execution")

    measurement = packet.get("token_measurement")
    measurement_gate = token_v1.gate_eligibility(
        measurement,
        gate_scope="full_run",
        evidence_already_verified=True,
    )
    for measurement_issue in measurement_gate.get("reasons", []):
        issue(f"token_measurement is not eligible for the full-run gate: {measurement_issue}")

    elapsed = packet.get("elapsed_seconds")
    if not _finite_number(elapsed) or float(elapsed) < 0:
        issue("elapsed_seconds must be a finite non-negative number")
    rework = _object(packet.get("rework"))
    for field in REWORK_FIELDS:
        if not _nonnegative_int(rework.get(field)):
            issue(f"rework.{field} must be a non-negative integer")
    if all(_nonnegative_int(rework.get(field)) for field in REWORK_FIELDS):
        expected_total = sum(int(rework[field]) for field in REWORK_FIELDS[:-1])
        if int(rework["total"]) != expected_total:
            issue("rework.total must equal steering, repair, and acceptance retry counts")
    cost = packet.get("cost_estimates")
    if cost is not None:
        if not isinstance(cost, dict):
            issue("cost_estimates must be an object when present")
        else:
            available = cost.get("available")
            measured = cost.get("measured")
            if not isinstance(available, bool):
                issue("cost_estimates.available must be boolean")
            if not isinstance(measured, bool):
                issue("cost_estimates.measured must be boolean")
            if available is True and (
                not _finite_number(cost.get("total_estimated"))
                or float(cost.get("total_estimated", -1)) < 0
            ):
                issue("cost_estimates.total_estimated must be a finite non-negative number when available")
            if measured is True:
                issue(
                    "measured provider invoice cost is unavailable because no trusted invoice adapter is implemented"
                )
                completeness = _object(cost.get("completeness"))
                if cost.get("provenance") != "provider_invoice":
                    issue("measured cost requires provider_invoice provenance")
                if available is not True:
                    issue("measured provider invoice cost must be available")
                if completeness.get("complete") is not True or completeness.get("missing") != []:
                    issue("measured provider invoice cost must be complete")
                if not isinstance(cost.get("currency"), str) or not str(cost.get("currency", "")).strip():
                    issue("measured provider invoice cost requires a currency")
                invoice = durable_json_evidence(
                    cost,
                    path_field="evidence_path",
                    sha_field="evidence_sha256",
                    label="provider invoice evidence",
                    coordinator_output_root=coordinator_output_root,
                    issue=issue,
                )
                line_item_ids = cost.get("line_item_ids")
                if (
                    not isinstance(line_item_ids, list)
                    or not line_item_ids
                    or not all(isinstance(value, str) and value for value in line_item_ids)
                    or len(line_item_ids) != len(set(line_item_ids))
                ):
                    issue("provider invoice line_item_ids must be unique non-empty strings")
                    line_item_ids = []
                if invoice is not None:
                    line_items = invoice.get("line_items")
                    selected = [
                        row
                        for row in line_items
                        if isinstance(line_items, list)
                        and isinstance(row, dict)
                        and row.get("id") in line_item_ids
                    ] if isinstance(line_items, list) else []
                    selected_totals = [
                        float(row.get("total"))
                        for row in selected
                        if _finite_number(row.get("total"))
                    ]
                    if (
                        not _schema_version_one(invoice.get("schema_version"))
                        or invoice.get("tool") != "provider-invoice-export"
                        or invoice.get("currency") != cost.get("currency")
                        or len(selected) != len(line_item_ids)
                        or any(row.get("thread_id") != thread_id for row in selected)
                        or len(selected_totals) != len(selected)
                        or not _finite_number(cost.get("total_estimated"))
                        or not math.isclose(
                            sum(selected_totals),
                            float(cost.get("total_estimated")),
                            rel_tol=0,
                            abs_tol=1e-12,
                        )
                    ):
                        issue("provider invoice export does not bind the thread line items and measured cost")
    return issues


def distribution(values: list[int | float]) -> dict[str, int | float]:
    if not values:
        return {"count": 0, "median": 0, "min": 0, "max": 0, "range": 0, "spread": 0}
    minimum = min(values)
    maximum = max(values)
    return {
        "count": len(values),
        "median": statistics.median(values),
        "min": minimum,
        "max": maximum,
        "range": maximum - minimum,
        "spread": maximum - minimum,
    }


def arm_statistics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    quality_scores = [
        float(value)
        for row in rows
        for value in [_object(row.get("evaluator")).get("score")]
        if _finite_number(value)
    ]
    passed_values = [1 if _object(row.get("evaluator")).get("passed") is True else 0 for row in rows]
    rework_rows = [_object(row.get("rework")) for row in rows]
    measurements = [_object(row.get("token_measurement")) for row in rows]
    costs = [_object(row.get("cost_estimates")) for row in rows]
    locally_consistent_measured_cost = bool(costs) and all(
        cost.get("available") is True
        and cost.get("measured") is True
        and cost.get("provenance") == "provider_invoice"
        and _object(cost.get("completeness")).get("complete") is True
        and _object(cost.get("completeness")).get("missing") == []
        and isinstance(cost.get("currency"), str)
        and bool(str(cost.get("currency", "")).strip())
        and _finite_number(cost.get("total_estimated"))
        for cost in costs
    )
    currencies = {str(cost.get("currency", "")).strip() for cost in costs if str(cost.get("currency", "")).strip()}
    locally_consistent_measured_cost = locally_consistent_measured_cost and len(currencies) == 1
    measured_cost = False
    available_costs = [
        float(cost["total_estimated"])
        for cost in costs
        if cost.get("available") is True and _finite_number(cost.get("total_estimated"))
    ]
    cost_summary: dict[str, Any] = {
        "measured": measured_cost,
        "provenance": "provider_invoice" if measured_cost else "not-measured",
        "currency": next(iter(currencies)) if len(currencies) == 1 else "",
        "total": distribution(available_costs),
    }
    if not measured_cost:
        cost_summary["estimate_only"] = bool(available_costs)
        cost_summary["locally_consistent_provider_invoice"] = locally_consistent_measured_cost
        cost_summary["provider_invoice_adapter_status"] = "unavailable"
        cost_summary["provenances"] = sorted(
            {str(cost.get("provenance", "")) for cost in costs if str(cost.get("provenance", ""))}
        )
    return {
        "count": len(rows),
        "quality": {
            "score": distribution(quality_scores),
            "passed": distribution(passed_values),
            "pass_rate": (sum(passed_values) / len(passed_values)) if passed_values else 0,
        },
        "elapsed_seconds": distribution(
            [float(row["elapsed_seconds"]) for row in rows if _finite_number(row.get("elapsed_seconds"))]
        ),
        "rework": {
            field: distribution(
                [int(rework[field]) for rework in rework_rows if _nonnegative_int(rework.get(field))]
            )
            for field in REWORK_FIELDS
        },
        "tokens": {
            field: distribution(
                [
                    int(measurement[field])
                    for measurement in measurements
                    if _nonnegative_int(measurement.get(field))
                ]
            )
            for field in token_v1.TOKEN_FIELDS
        },
        "cost": cost_summary,
    }


def comparison_report(
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    *,
    valid_benchmark: bool,
) -> dict[str, Any]:
    baseline_by_rep = {str(row.get("replicate_id", "")): row for row in baseline_rows}
    candidate_by_rep = {str(row.get("replicate_id", "")): row for row in candidate_rows}
    replicate_ids = sorted(set(baseline_by_rep) & set(candidate_by_rep))
    paired_complete = len(replicate_ids) == len(baseline_rows) == len(candidate_rows)
    baseline_passed = [
        _object(row.get("evaluator")).get("passed") is True
        for row in baseline_rows
    ]
    candidate_passed = [
        _object(row.get("evaluator")).get("passed") is True
        for row in candidate_rows
    ]
    baseline_scores = [
        float(value)
        for row in baseline_rows
        for value in [_object(row.get("evaluator")).get("score")]
        if _finite_number(value)
    ]
    candidate_scores = [
        float(value)
        for row in candidate_rows
        for value in [_object(row.get("evaluator")).get("score")]
        if _finite_number(value)
    ]
    baseline_score_median = statistics.median(baseline_scores) if baseline_scores else 0
    candidate_score_median = statistics.median(candidate_scores) if candidate_scores else 0

    paired_quality_deltas: list[float] = []
    paired_rework_deltas: list[int] = []
    paired_token_deltas: list[int] = []
    paired_quality_complete = True
    paired_rework_complete = True
    paired_tokens_complete = True
    for replicate_id in replicate_ids:
        baseline = baseline_by_rep[replicate_id]
        candidate = candidate_by_rep[replicate_id]
        baseline_score = _object(baseline.get("evaluator")).get("score")
        candidate_score = _object(candidate.get("evaluator")).get("score")
        if _finite_number(baseline_score) and _finite_number(candidate_score):
            paired_quality_deltas.append(float(candidate_score) - float(baseline_score))
        else:
            paired_quality_complete = False
        baseline_rework = _object(baseline.get("rework")).get("total")
        candidate_rework = _object(candidate.get("rework")).get("total")
        if _nonnegative_int(baseline_rework) and _nonnegative_int(candidate_rework):
            paired_rework_deltas.append(int(candidate_rework) - int(baseline_rework))
        else:
            paired_rework_complete = False
        baseline_total = _object(baseline.get("token_measurement")).get("total_tokens")
        candidate_total = _object(candidate.get("token_measurement")).get("total_tokens")
        if _nonnegative_int(baseline_total) and _nonnegative_int(candidate_total):
            paired_token_deltas.append(int(candidate_total) - int(baseline_total))
        else:
            paired_tokens_complete = False
    no_paired_quality_regression = (
        paired_complete
        and paired_quality_complete
        and len(paired_quality_deltas) == len(replicate_ids)
        and all(delta >= 0 for delta in paired_quality_deltas)
    )
    quality_equivalent = (
        paired_complete
        and bool(baseline_passed)
        and all(baseline_passed)
        and all(candidate_passed)
        and len(baseline_scores) == len(baseline_rows)
        and len(candidate_scores) == len(candidate_rows)
        and candidate_score_median >= baseline_score_median
        and no_paired_quality_regression
    )
    no_rework_regression = (
        paired_complete
        and paired_rework_complete
        and len(paired_rework_deltas) == len(replicate_ids)
        and all(delta <= 0 for delta in paired_rework_deltas)
    )

    measurements = [
        _object(row.get("token_measurement"))
        for row in [*baseline_rows, *candidate_rows]
    ]
    boundaries = {
        (
            str(measurement.get("provenance", "")),
            str(measurement.get("scope", "")),
            str(measurement.get("accounting_unit", "")),
            str(measurement.get("tokenizer_or_estimator", "")),
            str(measurement.get("host_surface", "")),
            str(measurement.get("model_provider", "")),
        )
        for measurement in measurements
    }
    locally_consistent_provider_telemetry = bool(measurements) and len(boundaries) == 1 and all(
        token_v1.gate_eligibility(
            measurement,
            gate_scope="full_run",
            evidence_already_verified=True,
        ).get("eligible") is True
        for measurement in measurements
    )
    # Coordinator-authored rollout/ledger files are useful diagnostics, but they
    # are not an out-of-band provider trust root. Promotion remains fail-closed
    # until aggregate accepts an implemented host-state adapter.
    complete_matching_provider_telemetry = False
    baseline_totals = [
        int(value)
        for row in baseline_rows
        for value in [_object(row.get("token_measurement")).get("total_tokens")]
        if _nonnegative_int(value)
    ]
    candidate_totals = [
        int(value)
        for row in candidate_rows
        for value in [_object(row.get("token_measurement")).get("total_tokens")]
        if _nonnegative_int(value)
    ]
    baseline_median = statistics.median(baseline_totals) if baseline_totals else 0
    candidate_median = statistics.median(candidate_totals) if candidate_totals else 0
    paired_improvement_count = sum(1 for delta in paired_token_deltas if delta < 0)
    repeatable_improvement = (
        complete_matching_provider_telemetry
        and paired_complete
        and paired_tokens_complete
        and len(paired_token_deltas) == len(replicate_ids)
        and len(replicate_ids) >= 3
        and candidate_median < baseline_median
        and bool(paired_token_deltas)
        and all(delta < 0 for delta in paired_token_deltas)
    )
    claim_eligible = (
        valid_benchmark
        and quality_equivalent
        and no_rework_regression
        and repeatable_improvement
    )
    return {
        "quality_equivalent": quality_equivalent,
        "no_paired_quality_regression": no_paired_quality_regression,
        "paired_quality_deltas": paired_quality_deltas,
        "no_rework_regression": no_rework_regression,
        "complete_matching_provider_telemetry": complete_matching_provider_telemetry,
        "locally_consistent_provider_telemetry": locally_consistent_provider_telemetry,
        "provider_adapter_status": "unavailable",
        "baseline_total_tokens_median": baseline_median,
        "candidate_total_tokens_median": candidate_median,
        "median_total_token_delta": candidate_median - baseline_median,
        "paired_total_token_deltas": paired_token_deltas,
        "paired_improvement_count": paired_improvement_count,
        "repeatable_provider_token_improvement": repeatable_improvement,
        "claim_eligible": claim_eligible,
    }


def delegation_economics_report(
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    *,
    gate: dict[str, Any],
    valid_benchmark: bool,
) -> dict[str, Any]:
    gate_policy_valid = gate == DEFAULT_DELEGATION_GATE
    effective_gate = gate if gate_policy_valid else DEFAULT_DELEGATION_GATE
    baseline_by_rep = {str(row.get("replicate_id", "")): row for row in baseline_rows}
    candidate_by_rep = {str(row.get("replicate_id", "")): row for row in candidate_rows}
    paired_ids = sorted(set(baseline_by_rep) & set(candidate_by_rep))
    minimum_trials = int(effective_gate["minimum_trials_per_arm"])
    paired_complete = (
        len(paired_ids) == len(baseline_rows) == len(candidate_rows)
        and len(paired_ids) >= minimum_trials
    )

    quality_noninferior = paired_complete
    no_rework_regression = paired_complete
    for replicate_id in paired_ids:
        baseline = baseline_by_rep[replicate_id]
        candidate = candidate_by_rep[replicate_id]
        baseline_evaluator = _object(baseline.get("evaluator"))
        candidate_evaluator = _object(candidate.get("evaluator"))
        baseline_score = baseline_evaluator.get("score")
        candidate_score = candidate_evaluator.get("score")
        if (
            baseline_evaluator.get("passed") is not True
            or candidate_evaluator.get("passed") is not True
            or not _finite_number(baseline_score)
            or not _finite_number(candidate_score)
            or float(candidate_score) < float(baseline_score)
        ):
            quality_noninferior = False
        baseline_rework = _object(baseline.get("rework")).get("total")
        candidate_rework = _object(candidate.get("rework")).get("total")
        if (
            not _nonnegative_int(baseline_rework)
            or not _nonnegative_int(candidate_rework)
            or int(candidate_rework) > int(baseline_rework)
        ):
            no_rework_regression = False

    measurements = [
        _object(row.get("token_measurement"))
        for row in [*baseline_rows, *candidate_rows]
    ]
    required_provenance = str(effective_gate["required_token_provenance"])
    locally_consistent_provider_telemetry = bool(measurements) and all(
        token_v1.gate_eligibility(
            measurement,
            gate_scope="full_run",
            evidence_already_verified=True,
        ).get("eligible") is True
        and measurement.get("provenance") == required_provenance == "provider_telemetry"
        for measurement in measurements
    )
    provider_telemetry_complete = False
    thread_tree_complete = bool(candidate_rows) and all(
        isinstance(row.get("thread_tree"), dict)
        and isinstance(_object(row.get("thread_tree")).get("threads"), list)
        and len(_object(row.get("thread_tree"))["threads"]) == 3
        for row in candidate_rows
    )
    baseline_tokens = [
        int(_object(row.get("token_measurement")).get("total_tokens", 0))
        for row in baseline_rows
        if _nonnegative_int(_object(row.get("token_measurement")).get("total_tokens"))
    ]
    candidate_tokens = [
        int(_object(row.get("token_measurement")).get("total_tokens", 0))
        for row in candidate_rows
        if _nonnegative_int(_object(row.get("token_measurement")).get("total_tokens"))
    ]
    baseline_elapsed = [
        float(row["elapsed_seconds"])
        for row in baseline_rows
        if _finite_number(row.get("elapsed_seconds"))
    ]
    candidate_elapsed = [
        float(row["elapsed_seconds"])
        for row in candidate_rows
        if _finite_number(row.get("elapsed_seconds"))
    ]
    baseline_token_median = statistics.median(baseline_tokens) if baseline_tokens else 0
    candidate_token_median = statistics.median(candidate_tokens) if candidate_tokens else 0
    baseline_elapsed_median = statistics.median(baseline_elapsed) if baseline_elapsed else 0
    candidate_elapsed_median = statistics.median(candidate_elapsed) if candidate_elapsed else 0
    token_increase_percent = (
        ((candidate_token_median - baseline_token_median) / baseline_token_median) * 100
        if baseline_token_median > 0
        else math.inf
    )
    wall_time_improvement_percent = (
        ((baseline_elapsed_median - candidate_elapsed_median) / baseline_elapsed_median) * 100
        if baseline_elapsed_median > 0
        else -math.inf
    )
    token_limit = int(effective_gate["maximum_tokens_per_trial"])
    seconds_limit = int(effective_gate["maximum_seconds_per_trial"])
    within_trial_limits = (
        len(baseline_tokens) == len(baseline_rows)
        and len(candidate_tokens) == len(candidate_rows)
        and len(baseline_elapsed) == len(baseline_rows)
        and len(candidate_elapsed) == len(candidate_rows)
        and all(value <= token_limit for value in [*baseline_tokens, *candidate_tokens])
        and all(value <= seconds_limit for value in [*baseline_elapsed, *candidate_elapsed])
    )
    wall_time_gate_passed = (
        wall_time_improvement_percent
        >= float(effective_gate["minimum_median_wall_time_improvement_percent"])
    )
    provider_token_gate_passed = (
        token_increase_percent
        <= float(effective_gate["maximum_median_provider_token_increase_percent"])
    )
    passed = (
        valid_benchmark
        and gate_policy_valid
        and paired_complete
        and quality_noninferior
        and no_rework_regression
        and provider_telemetry_complete
        and thread_tree_complete
        and within_trial_limits
        and wall_time_gate_passed
        and provider_token_gate_passed
    )
    return {
        "gate_policy_valid": gate_policy_valid,
        "paired_complete": paired_complete,
        "trial_count": len(candidate_rows),
        "quality_noninferior": quality_noninferior,
        "no_rework_regression": no_rework_regression,
        "provider_telemetry_complete": provider_telemetry_complete,
        "locally_consistent_provider_telemetry": locally_consistent_provider_telemetry,
        "provider_adapter_status": "unavailable",
        "thread_tree_complete": thread_tree_complete,
        "model_attested": bool(valid_benchmark),
        "within_trial_limits": within_trial_limits,
        "baseline_median_total_tokens": baseline_token_median,
        "candidate_median_total_tokens": candidate_token_median,
        "median_provider_token_increase_percent": round(token_increase_percent, 4),
        "baseline_median_wall_seconds": baseline_elapsed_median,
        "candidate_median_wall_seconds": candidate_elapsed_median,
        "median_wall_time_improvement_percent": round(wall_time_improvement_percent, 4),
        "wall_time_gate_passed": wall_time_gate_passed,
        "provider_token_gate_passed": provider_token_gate_passed,
        "passed": passed,
    }


def aggregate_trials(protocol_path: Path, trial_paths: list[Path]) -> dict[str, Any]:
    protocol_path = resolved_path(Path.cwd(), str(protocol_path), "protocol")
    protocol = load_json_object(protocol_path)
    issues = protocol_hash_issues(protocol)
    issues.extend(protocol_source_identity_issues(protocol))
    try:
        coordinator_output_root = resolved_path(
            Path.cwd(),
            _object(protocol.get("paths")).get("coordinator_output_root"),
            "protocol coordinator_output_root",
        )
    except (OSError, SystemExit, ValueError) as exc:
        issues.append(f"protocol coordinator_output_root is unavailable or unsafe: {exc}")
        coordinator_output_root = protocol_path.parent
    if not _inside(coordinator_output_root, protocol_path):
        issues.append("protocol file must be contained in coordinator_output_root")
    declared_trials = protocol.get("trials") if isinstance(protocol.get("trials"), list) else []
    expected_trials = {
        (str(item.get("arm", "")), str(item.get("replicate_id", ""))): item
        for item in declared_trials
        if isinstance(item, dict)
    }
    packets: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    thread_ids: dict[str, str] = {}
    workspaces: dict[str, str] = {}
    all_evidence_identities: list[dict[str, object]] = []
    for raw_path in trial_paths:
        try:
            path = resolved_path(Path.cwd(), str(raw_path), "trial packet")
        except (OSError, SystemExit, ValueError) as exc:
            issues.append(f"trial packet is unavailable or unsafe: {exc}")
            continue
        if not _inside(coordinator_output_root, path):
            issues.append(f"trial packet must be contained in coordinator_output_root: {path}")
        try:
            packet = load_json_object(path)
        except SystemExit as exc:
            issues.append(f"invalid trial packet {path}: {exc}")
            continue
        packets.append(packet)
        key = (str(packet.get("arm", "")), str(packet.get("replicate_id", "")))
        if key in seen_keys:
            issues.append(f"duplicate arm/replicate key: {key[0]}/{key[1]}")
        seen_keys.add(key)
        issues.extend(validate_trial_packet(packet, path, protocol, expected_trials.get(key)))
        label = f"{key[0]}/{key[1]}"
        evidence_rows = packet_thread_rows(packet)
        if len(evidence_rows) == 1 and "token_measurement" not in evidence_rows[0]:
            evidence_rows = [{**evidence_rows[0], "token_measurement": packet.get("token_measurement")}]
        packet_identities = thread_evidence_identities(
            evidence_rows,
            issue=lambda message: issues.append(f"{label}: {message}"),
        )
        for identity in packet_identities:
            identity["thread_id"] = f"{label}:{identity.get('thread_id', '<missing>')}"
        all_evidence_identities.extend(packet_identities)
        packet_tree = _object(packet.get("thread_tree"))
        packet_tree_rows = packet_tree.get("threads")
        if isinstance(packet_tree_rows, list):
            packet_thread_ids = [
                str(row.get("id", "")).strip()
                for row in packet_tree_rows
                if isinstance(row, dict) and str(row.get("id", "")).strip()
            ]
        else:
            packet_thread_ids = [str(_object(packet.get("thread")).get("id", "")).strip()]
        for thread_id in packet_thread_ids:
            if not thread_id:
                continue
            if thread_id in thread_ids:
                issues.append(
                    f"duplicate thread id {thread_id} shared across trials: "
                    f"{thread_ids[thread_id]} and {label}"
                )
            else:
                thread_ids[thread_id] = label
        try:
            workspace_path = resolved_path(Path.cwd(), packet.get("workspace"), "trial workspace")
        except (OSError, SystemExit, ValueError) as exc:
            issues.append(f"{label}: trial workspace cannot be normalized: {exc}")
        else:
            workspace = os.path.normcase(str(workspace_path))
            if workspace in workspaces:
                issues.append(f"duplicate workspace {workspace}: {workspaces[workspace]} and {label}")
            else:
                workspaces[workspace] = label

    reject_reused_thread_evidence(
        all_evidence_identities,
        issue=issues.append,
        scope="benchmark trials",
    )

    missing_keys = sorted(set(expected_trials) - seen_keys)
    unexpected_keys = sorted(seen_keys - set(expected_trials))
    issues.extend(f"missing trial packet: {arm}/{replicate}" for arm, replicate in missing_keys)
    issues.extend(f"unexpected trial packet: {arm}/{replicate}" for arm, replicate in unexpected_keys)
    repetitions_value = protocol.get("repetitions")
    repetitions = (
        repetitions_value
        if isinstance(repetitions_value, int) and not isinstance(repetitions_value, bool)
        else 0
    )
    for arm in ARM_IDS:
        count = sum(1 for packet in packets if packet.get("arm") == arm)
        if count < 3 or count != repetitions:
            issues.append(f"{arm} requires exactly {repetitions} packets and at least 3; found {count}")

    rows_by_arm = {
        arm: sorted(
            [packet for packet in packets if packet.get("arm") == arm],
            key=lambda packet: str(packet.get("replicate_id", "")),
        )
        for arm in ARM_IDS
    }
    summaries = {arm: arm_statistics(rows) for arm, rows in rows_by_arm.items()}
    valid = not issues
    comparisons = {
        "harness_no_local_ai_vs_direct": comparison_report(
            rows_by_arm["direct"],
            rows_by_arm["harness_no_local_ai"],
            valid_benchmark=valid,
        ),
        "harness_local_ai_vs_direct": comparison_report(
            rows_by_arm["direct"],
            rows_by_arm["harness_local_ai"],
            valid_benchmark=valid,
        ),
        "harness_local_ai_vs_harness_no_local_ai": comparison_report(
            rows_by_arm["harness_no_local_ai"],
            rows_by_arm["harness_local_ai"],
            valid_benchmark=valid,
        ),
    }
    benchmark_mode = str(protocol.get("benchmark_mode", "harness-economics"))
    delegation_gate: dict[str, Any] = {}
    if benchmark_mode == "delegation-economics":
        gate_policy = _object(protocol.get("delegation_gate"))
        effective_gate_policy = (
            gate_policy
            if gate_policy == DEFAULT_DELEGATION_GATE
            else DEFAULT_DELEGATION_GATE
        )
        delegation_comparisons = {
            arm: delegation_economics_report(
                rows_by_arm["direct"],
                rows_by_arm[arm],
                gate=gate_policy,
                valid_benchmark=valid,
            )
            for arm in ("harness_no_local_ai", "harness_local_ai")
        }
        passing_arms = [
            arm
            for arm, comparison in delegation_comparisons.items()
            if comparison.get("passed") is True
        ]
        selected_protocol_arm = min(
            passing_arms,
            key=lambda arm: (
                delegation_comparisons[arm]["candidate_median_total_tokens"],
                delegation_comparisons[arm]["candidate_median_wall_seconds"],
            ),
        ) if passing_arms else ""
        aliases = _object(protocol.get("arm_aliases"))
        selected_report = delegation_comparisons.get(selected_protocol_arm, {})
        gate_status = "passed" if selected_protocol_arm else ("failed" if valid else "invalid")
        delegation_gate = {
            "schema_version": 2,
            "tool": "agent-benchmarking.delegation-gate",
            "gate_ref": "delegation-balanced-v1",
            "status": gate_status,
            "task_class": "independent-read-heavy",
            "host_surface": "codex",
            "model_provider": str(protocol.get("model_provider", "")),
            "model": str(protocol.get("model", "")),
            "execution_mode": "native-subagents",
            "provider_adapter_id": "unavailable",
            "provider_adapter_status": "unavailable",
            "selected_protocol_arm": selected_protocol_arm,
            "selected_arm": aliases.get(selected_protocol_arm, "") if selected_protocol_arm else "",
            "token_provenance": "provider_telemetry"
            if selected_report.get("provider_telemetry_complete") is True
            else "incomplete",
            "model_attested": selected_report.get("model_attested") is True,
            "thread_tree_complete": selected_report.get("thread_tree_complete") is True,
            "minimum_trials_per_arm": effective_gate_policy["minimum_trials_per_arm"],
            "fallback": effective_gate_policy["fallback"],
            "comparisons": delegation_comparisons,
        }
        general_claim = False
        internal_optimization = bool(selected_protocol_arm)
    else:
        general_claim = valid and any(
            comparisons[key]["claim_eligible"] is True
            for key in ("harness_no_local_ai_vs_direct", "harness_local_ai_vs_direct")
        )
        internal_optimization = (
            valid
            and comparisons["harness_local_ai_vs_harness_no_local_ai"]["claim_eligible"] is True
        )
    currencies = {
        str(summary.get("cost", {}).get("currency", ""))
        for summary in summaries.values()
        if str(summary.get("cost", {}).get("currency", ""))
    }
    measured_cost_comparable = (
        valid
        and len(currencies) == 1
        and all(summary.get("cost", {}).get("measured") is True for summary in summaries.values())
    )
    if benchmark_mode == "delegation-economics":
        status = (
            "invalid"
            if not valid
            else (
                "valid-delegation-arm-promoted"
                if delegation_gate.get("status") == "passed"
                else "valid-delegation-negative-result"
            )
        )
    else:
        status = (
            "invalid"
            if not valid
            else ("valid-general-savings-supported" if general_claim else "valid-no-general-savings")
        )
    return {
        "schema_version": 1,
        "tool": "agent-benchmarking.three-arm-full-run",
        "mode": "aggregate",
        "ok": valid,
        "status": status,
        "valid_benchmark": valid,
        "benchmark_id": protocol.get("benchmark_id", ""),
        "protocol_sha256": protocol.get("protocol_sha256", ""),
        "trial_count": len(packets),
        "issues": sorted(set(issues)),
        "arms": summaries,
        "comparisons": comparisons,
        "delegation_gate": delegation_gate,
        "quality_equivalent": {
            key: value["quality_equivalent"] for key, value in comparisons.items()
        },
        "repeatable_provider_token_improvement": {
            key: value["repeatable_provider_token_improvement"]
            for key, value in comparisons.items()
        },
        "general_savings_claim_eligible": general_claim,
        "harness_internal_optimization_eligible": internal_optimization,
        "measured_cost_comparable": measured_cost_comparable,
        "conclusion": {
            "status": status,
            "statement": (
                "A repeatable provider-backed token improvement is supported at equivalent quality."
                if general_claim
                else (
                    "A delegation arm passed the balanced quality, wall-time, and provider-token gate."
                    if valid and delegation_gate.get("status") == "passed"
                    else (
                        "The repeated delegation benchmark is valid; no arm passed, so single-agent remains the default."
                        if valid and benchmark_mode == "delegation-economics"
                        else "The repeated benchmark is valid. No general savings claim is supported."
                    )
                    if valid
                    else "The evidence packet is invalid. No economic conclusion is supported."
                )
            ),
        },
        "execution_started": False,
        "network_used": False,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Three-Arm Full-Run Benchmark",
        "",
        f"- Mode: `{report.get('mode', '')}`",
        f"- OK: {str(report.get('ok') is True).lower()}",
        f"- Execution started: {str(report.get('execution_started') is True).lower()}",
        f"- Network used: {str(report.get('network_used') is True).lower()}",
    ]
    if report.get("mode") == "aggregate":
        conclusion = _object(report.get("conclusion"))
        lines.extend(
            [
                f"- Valid benchmark: {str(report.get('valid_benchmark') is True).lower()}",
                f"- General savings claim eligible: {str(report.get('general_savings_claim_eligible') is True).lower()}",
                f"- Conclusion: {conclusion.get('statement', '')}",
                "",
                "| Arm | Trials | Quality median | Pass rate | Rework median | Total-token median | Elapsed median | Measured cost |",
                "|---|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        arms = report.get("arms") if isinstance(report.get("arms"), dict) else {}
        for arm in ARM_IDS:
            summary = _object(arms.get(arm))
            quality = _object(summary.get("quality"))
            rework = _object(summary.get("rework"))
            tokens = _object(summary.get("tokens"))
            elapsed = _object(summary.get("elapsed_seconds"))
            cost = _object(summary.get("cost"))
            lines.append(
                f"| `{arm}` | {summary.get('count', 0)} | "
                f"{_object(quality.get('score')).get('median', 0)} | {quality.get('pass_rate', 0)} | "
                f"{_object(rework.get('total')).get('median', 0)} | "
                f"{_object(tokens.get('total_tokens')).get('median', 0)} | {elapsed.get('median', 0)} | "
                f"{str(cost.get('measured') is True).lower()} |"
            )
        comparisons = report.get("comparisons") if isinstance(report.get("comparisons"), dict) else {}
        if comparisons:
            lines.extend(["", "## Comparisons", ""])
            for name in (
                "harness_no_local_ai_vs_direct",
                "harness_local_ai_vs_direct",
                "harness_local_ai_vs_harness_no_local_ai",
            ):
                comparison = _object(comparisons.get(name))
                lines.append(
                    f"- `{name}`: quality equivalent `{str(comparison.get('quality_equivalent') is True).lower()}`, "
                    f"repeatable provider improvement `{str(comparison.get('repeatable_provider_token_improvement') is True).lower()}`, "
                    f"claim eligible `{str(comparison.get('claim_eligible') is True).lower()}`."
                )
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in issues)
    return "\n".join(lines).rstrip() + "\n"


def load_trial_index(protocol_path: Path, index_path: Path) -> list[Path]:
    protocol_path = resolved_path(Path.cwd(), str(protocol_path), "protocol")
    protocol = load_json_object(protocol_path)
    output_root = resolved_path(
        Path.cwd(),
        _object(protocol.get("paths")).get("coordinator_output_root"),
        "protocol coordinator_output_root",
    )
    index_path = resolved_path(Path.cwd(), str(index_path), "trial index")
    if not _inside(output_root, index_path):
        raise SystemExit("trial index must be contained in coordinator_output_root")
    index, _index_sha256 = read_no_follow_json(index_path, "trial index")
    if not _schema_version_one(index.get("schema_version")):
        raise SystemExit("trial index schema_version must be 1")
    if index.get("tool") != "agent-benchmarking.three-arm-full-run-trial-index":
        raise SystemExit("trial index tool is invalid")
    if index.get("benchmark_id") != protocol.get("benchmark_id"):
        raise SystemExit("trial index benchmark_id does not match the protocol")
    if index.get("protocol_sha256") != protocol.get("protocol_sha256"):
        raise SystemExit("trial index protocol_sha256 does not match the protocol")
    values = index.get("trial_paths")
    if not isinstance(values, list) or not values:
        raise SystemExit("trial index trial_paths must be a non-empty path array")
    expected_trials = protocol.get("trials") if isinstance(protocol.get("trials"), list) else []
    if len(values) != len(expected_trials) or len(values) < len(ARM_IDS) * 3:
        raise SystemExit("trial index must contain exactly every protocol trial and at least nine paths")
    paths: list[Path] = []
    normalized: set[str] = set()
    for position, value in enumerate(values):
        try:
            path = resolved_path(index_path.parent, value, f"trial index trials[{position}]")
        except (OSError, SystemExit, ValueError) as exc:
            raise SystemExit(f"invalid trial index entry {position}: {exc}") from None
        if not _inside(output_root, path):
            raise SystemExit(f"trial index entry {position} must be contained in coordinator_output_root")
        key = os.path.normcase(str(path))
        if key in normalized:
            raise SystemExit(f"trial index contains a duplicate path at position {position}")
        normalized.add(key)
        packet, _packet_sha256 = read_no_follow_json(path, f"trial index packet {position}")
        expected_trial = expected_trials[position] if isinstance(expected_trials[position], dict) else {}
        if (
            packet.get("arm") != expected_trial.get("arm")
            or packet.get("replicate_id") != expected_trial.get("replicate_id")
        ):
            raise SystemExit(
                f"trial index entry {position} is not in deterministic protocol arm/replicate order"
            )
        paths.append(path)
    return paths


def aggregate_output_path(protocol_path: Path, value: str) -> Path:
    protocol_path = resolved_path(Path.cwd(), str(protocol_path), "protocol")
    protocol = load_json_object(protocol_path)
    output_root = resolved_path(
        Path.cwd(),
        _object(protocol.get("paths")).get("coordinator_output_root"),
        "protocol coordinator_output_root",
    )
    try:
        output_path = resolved_path(Path.cwd(), value, "aggregate output")
    except (OSError, SystemExit, ValueError) as exc:
        raise SystemExit(f"aggregate output is unavailable or unsafe: {exc}") from None
    if not _inside(output_root, output_path):
        raise SystemExit("aggregate output must be strictly contained in coordinator_output_root")
    if output_path.exists():
        raise SystemExit(f"aggregate output already exists: {output_path}")
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="prepare a deterministic protocol and optional templates")
    prepare.add_argument("--definition", required=True)
    prepare.add_argument("--output-root", required=True)
    prepare.add_argument("--write", action="store_true")
    prepare.add_argument("--format", choices=("json", "markdown"), default="json", dest="output_format")
    preflight = subparsers.add_parser("preflight", help="validate protocol prerequisites without execution")
    preflight.add_argument("--protocol", required=True)
    preflight.add_argument(
        "--live",
        action="store_true",
        help="check external-execution prerequisites only; never launch an agent, model, network call, or subprocess",
    )
    preflight.add_argument("--format", choices=("json", "markdown"), default="json", dest="output_format")
    aggregate = subparsers.add_parser("aggregate", help="aggregate explicit completed trial packets")
    aggregate.add_argument("--protocol", required=True)
    trial_source = aggregate.add_mutually_exclusive_group(required=True)
    trial_source.add_argument("--trial", action="append", default=[], help="explicit packet path; repeatable")
    trial_source.add_argument("--trial-index", default="", help="explicit JSON index containing every packet path")
    aggregate.add_argument("--output", default="", help="optional fresh JSON report path")
    aggregate.add_argument("--format", choices=("json", "markdown"), default="json", dest="output_format")
    return parser


def main(argv: list[str] | None = None) -> int:
    common.require_supported_python()
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        report = prepare_protocol(Path(args.definition), Path(args.output_root), write=args.write)
    elif args.command == "preflight":
        report = preflight_protocol(Path(args.protocol), live=args.live)
    else:
        trial_paths = (
            load_trial_index(Path(args.protocol), Path(args.trial_index))
            if args.trial_index
            else [Path(path) for path in args.trial]
        )
        report = aggregate_trials(Path(args.protocol), trial_paths)
        if args.output:
            output_path = aggregate_output_path(Path(args.protocol), args.output)
            common.write_json(output_path, report)
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
