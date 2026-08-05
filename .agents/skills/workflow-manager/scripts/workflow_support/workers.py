"""Workflow phase worker profile reporting and validation."""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import workflow_manager_common as common
from validation_support.manifests import module_contract_v3

WORKER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
OVERLAY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9.-]{0,63}$")
HOST_SURFACES = {
    "codex",
    "github-copilot",
    "claude-code",
    "openai-responses-api",
    "anthropic-messages-api",
    "local-ai",
    "unknown",
}
MODEL_PROVIDERS = {"openai", "anthropic", "local", "other", "unknown"}
PROFILE_MODES = {"advisory", "auto-when-supported", "manual"}
EXECUTION_MODE_IDS = {"serial", "direct-child-agent", "independent-thread"}
DELIBERATION_TIERS = {"low", "medium", "high", "xhigh"}
CATALOG_SCHEMA_VERSIONS = {3}
MODEL_SELECTION_MODES = {"attestation-required"}
UNKNOWN_MODEL_POLICIES = {"generic-overlay-serial-active-model"}
MODEL_PROMOTION_STATES = {"portable-default", "existing-default", "experimental", "benchmark-approved"}
OVERLAY_GENERATIONS = {"generic", "gpt-5.5", "gpt-5.6", "claude"}
TRUSTED_OBSERVATION_SOURCES = {"host-runtime", "provider-response"}
RUNTIME_OBSERVATION_SCHEMA_VERSION = 1
RUNTIME_OBSERVATION_TOOL = "workflow-manager.runtime-observation"
RUNTIME_OBSERVATION_MAX_BYTES = 16_384
RUNTIME_OBSERVATION_FIELDS = {
    "schema_version",
    "tool",
    "workflow",
    "run_id",
    "phase",
    "host",
    "model",
    "evidence_path",
}
HOST_OBSERVATION_FIELDS = {"attested", "source", "surface", "capabilities"}
MODEL_OBSERVATION_FIELDS = {"attested", "source", "provider", "model", "observed_deliberation"}
CAPABILITY_IDS = {
    "model-selection",
    "deliberation-control",
    "complete-usage-telemetry",
    "per-call-usage",
    "complete-thread-tree",
    "isolated-worker-runtime",
    "context-inheritance-control",
    "native-subagents",
    "deterministic-hooks",
    "session-resume",
    "prompt-cache-control",
    "prompt-cache-telemetry",
    "reasoning-continuation",
    "hosted-program-orchestration",
}
PROFILE_FIELDS = {
    "purpose",
    "prompt_adapter",
    "context_budget",
    "tool_policy",
    "expected_output",
    "validation_gate",
    "route_set",
}
PROVIDER_RESPONSE_SURFACES = {
    "openai-responses-api": "openai",
    "anthropic-messages-api": "anthropic",
    "local-ai": "local",
}
PROVIDER_RESPONSE_FORBIDDEN_CAPABILITIES = {
    "complete-thread-tree",
    "isolated-worker-runtime",
    "context-inheritance-control",
    "native-subagents",
    "deterministic-hooks",
    "session-resume",
}
RISK_ROUTING_STATUSES = {"benchmark-gated"}
RISK_SELECTION_MODES = {"declarative-manual"}
APPROVED_MODEL_SOURCE_HOSTS = {
    "developers.openai.com",
    "platform.openai.com",
    "platform.claude.com",
    "docs.anthropic.com",
    "code.claude.com",
    "docs.github.com",
}
OVERLAY_ALLOWED_FIELDS = {
    "generation",
    "instructions",
    "promotion_state",
    "source_refs",
    "version",
}
PROMPT_ADAPTERS = {"evidence", "planning", "implementation", "test-authoring", "validation", "handoff", "general"}
CONTEXT_BUDGETS = {"lean", "standard", "expanded"}
PHASE_BUDGET_REFS = {
    "routing",
    "planning",
    "implementation",
    "test-authoring",
    "validation",
    "evidence",
    "handoff",
}
TOOL_POLICIES = {"read-only", "bounded-write", "deterministic-validation", "evidence-only", "handoff-only"}
VALIDATION_GATES = {"record-evidence", "approval-required", "deterministic-checks", "fresh-validation", "handoff-contract"}
PROFILE_CONFIG_PATH = Path(__file__).with_name("worker_profiles.json")
DELEGATION_EVIDENCE_PATH = Path(".agents/benchmarks/delegation-gates")
DELEGATION_GATE_SCHEMA_VERSION = 2
DELEGATION_GATE_TOOL = "agent-benchmarking.delegation-gate"
DELEGATION_GATE_FIELDS = {
    "schema_version",
    "tool",
    "gate_ref",
    "status",
    "task_class",
    "selected_protocol_arm",
    "selected_arm",
    "token_provenance",
    "model_attested",
    "thread_tree_complete",
    "minimum_trials_per_arm",
    "fallback",
    "comparisons",
    "host_surface",
    "model_provider",
    "model",
    "execution_mode",
    "provider_adapter_id",
    "provider_adapter_status",
}
IMPLEMENTED_DELEGATION_ECONOMICS_ADAPTERS = {
    ("codex", "openai"): "codex-rollout-v1",
}


def load_worker_profile_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or PROFILE_CONFIG_PATH
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"schema_version": None, "profile_sets": {}, "host_guidance": [], "_error": str(exc)}
    return data if isinstance(data, dict) else {"schema_version": None, "profile_sets": {}, "host_guidance": [], "_error": "catalog must be an object"}


def built_in_profile_sets() -> dict[str, dict[str, Any]]:
    sets = load_worker_profile_config().get("profile_sets", {})
    return sets if isinstance(sets, dict) else {}


def host_guidance() -> list[str]:
    guidance = load_worker_profile_config().get("host_guidance", [])
    return [str(item) for item in guidance if str(item).strip()] if isinstance(guidance, list) else []


def host_support() -> list[dict[str, str]]:
    rows = load_worker_profile_config().get("host_support", [])
    if not isinstance(rows, list):
        return []
    result: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        result.append(
            {
                "host": str(row.get("host", "")).strip(),
                "worker_selection": str(row.get("worker_selection", "")).strip(),
                "model_selection": str(row.get("model_selection", "")).strip(),
                "fallback": str(row.get("fallback", "")).strip(),
                "mitigation": str(row.get("mitigation", "")).strip(),
            }
        )
    return [row for row in result if row["host"]]


def validation_authority() -> dict[str, Any]:
    authority = load_worker_profile_config().get("validation_authority", {})
    return authority if isinstance(authority, dict) else {}


def execution_modes() -> dict[str, Any]:
    value = load_worker_profile_config().get("execution_modes", {})
    return value if isinstance(value, dict) else {}


def surface_adapters() -> dict[str, Any]:
    value = load_worker_profile_config().get("surface_adapters", {})
    return value if isinstance(value, dict) else {}


def surface_route_sets() -> dict[str, Any]:
    value = load_worker_profile_config().get("surface_route_sets", {})
    return value if isinstance(value, dict) else {}


def risk_routing() -> dict[str, Any]:
    value = load_worker_profile_config().get("risk_routing", {})
    return value if isinstance(value, dict) else {}


def synthetic_generic_overlay() -> dict[str, Any]:
    return {
        "id": "generic-v1",
        "version": 1,
        "generation": "generic",
        "promotion_state": "portable-default",
        "instructions": [
            "Follow the semantic task profile exactly.",
            "Treat the declared model target as advisory until trusted runtime observation is available.",
            "Do not change tools, authority, output contract, delegation, or validation requirements.",
        ],
        "source_refs": [],
    }


def prompt_overlay_delivery_directive(overlay: object) -> str:
    """Return one bounded behavior instruction without copying the full overlay."""

    if not isinstance(overlay, dict):
        return ""
    instructions = overlay.get("instructions")
    if not isinstance(instructions, list):
        return ""
    first = next(
        (" ".join(str(item).split()) for item in instructions if isinstance(item, str) and item.strip()),
        "",
    )
    return first[:217].rstrip() + "..." if len(first) > 220 else first


def effective_model_prompt_overlays(catalog: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    selected = catalog if isinstance(catalog, dict) else load_worker_profile_config()
    raw = selected.get("model_prompt_overlays")
    if not isinstance(raw, dict):
        fallback = synthetic_generic_overlay()
        fallback["delivery_directive"] = prompt_overlay_delivery_directive(fallback)
        return {fallback["id"]: fallback}
    overlays: dict[str, dict[str, Any]] = {}
    for overlay_id, value in raw.items():
        if not isinstance(value, dict):
            continue
        version = value.get("version")
        generation = value.get("generation")
        promotion_state = value.get("promotion_state")
        instructions = value.get("instructions")
        source_refs = value.get("source_refs")
        if not (
            isinstance(version, int)
            and not isinstance(version, bool)
            and version > 0
            and isinstance(generation, str)
            and generation.strip() in OVERLAY_GENERATIONS
            and isinstance(promotion_state, str)
            and promotion_state.strip() in MODEL_PROMOTION_STATES
            and isinstance(instructions, list)
            and instructions
            and all(isinstance(item, str) and item.strip() for item in instructions)
            and isinstance(source_refs, list)
            and all(isinstance(item, str) and item.strip() for item in source_refs)
        ):
            continue
        normalized = {
            "version": version,
            "generation": generation.strip(),
            "promotion_state": promotion_state.strip(),
            "instructions": list(instructions),
            "source_refs": list(source_refs),
            "id": str(overlay_id),
        }
        normalized["delivery_directive"] = prompt_overlay_delivery_directive(normalized)
        overlays[str(overlay_id)] = normalized
    if "generic-v1" not in overlays:
        fallback = synthetic_generic_overlay()
        fallback["delivery_directive"] = prompt_overlay_delivery_directive(fallback)
        overlays[fallback["id"]] = fallback
    return overlays


def model_compatibility() -> dict[str, Any]:
    value = load_worker_profile_config().get("model_compatibility", {})
    return value if isinstance(value, dict) else {}


def model_prompt_overlays() -> dict[str, dict[str, Any]]:
    return effective_model_prompt_overlays(load_worker_profile_config())


def _is_approved_https_source(value: object) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme == "https" and parsed.hostname in APPROVED_MODEL_SOURCE_HOSTS


def _iso_date(value: object) -> date | None:
    parsed_date: date | None = None
    try:
        parsed_date = date.fromisoformat(str(value or "").strip())
    except ValueError:
        # An invalid calendar date is expected validator input, represented by
        # the explicit sentinel so the caller can report the catalog field.
        parsed_date = None
    return parsed_date


def catalog_warnings(
    catalog: dict[str, Any],
    *,
    today: date | None = None,
) -> list[str]:
    compatibility = catalog.get("model_compatibility")
    if not isinstance(compatibility, dict):
        return []
    verified_at = _iso_date(compatibility.get("verified_at"))
    stale_after_days = compatibility.get("stale_after_days")
    if verified_at is None or not isinstance(stale_after_days, int) or isinstance(stale_after_days, bool):
        return []
    age = ((today or date.today()) - verified_at).days
    if age > stale_after_days:
        return [
            f"Model compatibility sources are stale ({age} days old; refresh after {stale_after_days} days)."
        ]
    return []


def load_cost_policy(root: Path) -> dict[str, Any]:
    document, issues, _exists = common.repo_policy.load_project_policy(root)
    if issues:
        raise ValueError("invalid project policy: " + "; ".join(issues))
    policy = document.get("cost_policy")
    if not isinstance(policy, dict):
        raise ValueError("invalid project policy: cost_policy must be an object")
    return policy


def load_delegation_gate_evidence(root: Path, gate_ref: str) -> dict[str, Any]:
    if not WORKER_ID_PATTERN.fullmatch(gate_ref):
        return {}
    path = root / DELEGATION_EVIDENCE_PATH / f"{gate_ref}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def delegation_gate_passed(
    *,
    delegation: dict[str, Any],
    cost_policy: dict[str, Any],
    evidence: dict[str, Any],
    host_surface: str,
    model_provider: str,
    model: str,
    execution_mode: str,
    runtime_model_trusted: bool,
) -> tuple[bool, str]:
    gate_ref = str(delegation.get("economics_gate_ref", "")).strip()
    delegation_policy = cost_policy.get("delegation")
    gates = delegation_policy.get("gates") if isinstance(delegation_policy, dict) else None
    gate = gates.get(gate_ref) if isinstance(gates, dict) else None
    if not isinstance(gate, dict):
        return False, f"delegation economics gate {gate_ref or '<missing>'} is not configured"
    if not evidence:
        return (
            False,
            f"delegation economics gate {gate_ref} has no passing provider-backed evidence",
        )
    unknown_fields = sorted(str(field) for field in set(evidence) - DELEGATION_GATE_FIELDS)
    missing_fields = sorted(DELEGATION_GATE_FIELDS - set(evidence))
    if unknown_fields:
        return (
            False,
            f"delegation economics gate {gate_ref} evidence invalid: unsupported fields: "
            + ", ".join(unknown_fields),
        )
    if missing_fields:
        return (
            False,
            f"delegation economics gate {gate_ref} evidence invalid: missing fields: "
            + ", ".join(missing_fields),
        )
    if evidence.get("schema_version") != DELEGATION_GATE_SCHEMA_VERSION:
        return (
            False,
            f"delegation economics gate {gate_ref} evidence invalid: schema_version must be "
            f"{DELEGATION_GATE_SCHEMA_VERSION}",
        )
    if evidence.get("tool") != DELEGATION_GATE_TOOL:
        return (
            False,
            f"delegation economics gate {gate_ref} evidence invalid: tool must be {DELEGATION_GATE_TOOL}",
        )
    for field in (
        "gate_ref",
        "status",
        "task_class",
        "selected_protocol_arm",
        "selected_arm",
        "token_provenance",
        "fallback",
        "host_surface",
        "model_provider",
        "model",
        "execution_mode",
        "provider_adapter_id",
        "provider_adapter_status",
    ):
        if not isinstance(evidence.get(field), str) or not str(evidence.get(field)).strip():
            return (
                False,
                f"delegation economics gate {gate_ref} evidence invalid: {field} must be a non-empty string",
            )
    for field in ("model_attested", "thread_tree_complete"):
        if not isinstance(evidence.get(field), bool):
            return (
                False,
                f"delegation economics gate {gate_ref} evidence invalid: {field} must be boolean",
            )
    if not isinstance(evidence.get("comparisons"), dict) or not evidence.get("comparisons"):
        return (
            False,
            f"delegation economics gate {gate_ref} evidence invalid: comparisons must be a non-empty object",
        )
    if not runtime_model_trusted:
        return (
            False,
            f"delegation economics gate {gate_ref} requires a trusted current model observation",
        )
    expected_adapter_id = IMPLEMENTED_DELEGATION_ECONOMICS_ADAPTERS.get(
        (host_surface, model_provider)
    )
    if not expected_adapter_id:
        return (
            False,
            f"delegation economics gate {gate_ref} has no implemented evidence adapter for "
            f"{host_surface or 'unknown'}/{model_provider or 'unknown'}",
        )
    required_provenance = "provider_telemetry"
    required_trials = gate.get("minimum_trials_per_arm", 3)
    checks = {
        "gate reference mismatch": evidence.get("gate_ref") != gate_ref,
        "gate status is not passed": evidence.get("status") != "passed",
        "task class is not independent-read-heavy": evidence.get("task_class")
        not in delegation.get("eligible_task_classes", []),
        f"token provenance is not {required_provenance}": evidence.get("token_provenance")
        != required_provenance,
        "model attestation is incomplete": evidence.get("model_attested") is not True,
        "thread tree is incomplete": evidence.get("thread_tree_complete") is not True,
        "host surface does not match the current runtime": evidence.get("host_surface")
        != host_surface,
        "model provider does not match the current runtime": evidence.get("model_provider")
        != model_provider,
        "model does not match the current runtime": evidence.get("model") != model,
        "execution mode does not match the current runtime": evidence.get("execution_mode")
        != execution_mode,
        "provider evidence adapter status is unavailable": evidence.get("provider_adapter_status")
        != "implemented",
        "provider evidence adapter does not match the current runtime": evidence.get(
            "provider_adapter_id"
        )
        != expected_adapter_id,
        "trial count is below the configured minimum": not (
            isinstance(evidence.get("minimum_trials_per_arm"), int)
            and not isinstance(evidence.get("minimum_trials_per_arm"), bool)
            and isinstance(required_trials, int)
            and evidence["minimum_trials_per_arm"] >= required_trials
        ),
    }
    failed = [message for message, active in checks.items() if active]
    if failed:
        return False, f"delegation economics gate {gate_ref} evidence invalid: {failed[0]}"
    return True, ""


def phase_parallel_decision(
    manifest: dict[str, Any],
    phase: str,
    *,
    root: Path,
    cost_policy: dict[str, Any],
    delegation_requested: bool = False,
    task_class: str = "independent-read-heavy",
    runtime_observation: object = None,
    runtime_observation_verification_issues: list[str] | None = None,
    workflow: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    config = configured_worker_profiles(manifest)
    declared = config.get("max_parallel_workers", 1)
    declared = declared if isinstance(declared, int) and not isinstance(declared, bool) else 1
    safety = manifest.get("parallel_safety")
    policies = safety.get("phase_policies") if isinstance(safety, dict) else {}
    policy = policies.get(phase) if isinstance(policies, dict) else None
    if not isinstance(policy, dict):
        if isinstance(safety, dict) and safety.get("default_mode") == "serial":
            return {
                "declared_worker_count": 1,
                "effective_worker_count": 1,
                "eligible": False,
                "isolation_mode": "serial",
                "serial_fallback_reason": "parallel_safety.default_mode requires serial execution",
            }
        return {
            "declared_worker_count": declared,
            "effective_worker_count": 1,
            "eligible": False,
            "isolation_mode": "serial-fallback" if declared > 1 else "serial",
            "serial_fallback_reason": (
                f"phase {phase!r} has no declared parallel_safety policy"
                if declared > 1
                else ""
            ),
        }
    mode = str(policy.get("mode", "serial"))
    phase_declared = policy.get("max_workers", 1)
    phase_declared = (
        min(declared, phase_declared)
        if isinstance(phase_declared, int) and not isinstance(phase_declared, bool)
        else 1
    )
    if mode == "serial" or phase_declared <= 1:
        return {
            "declared_worker_count": 1,
            "effective_worker_count": 1,
            "eligible": False,
            "isolation_mode": "serial",
            "serial_fallback_reason": "phase policy requires serial execution",
        }
    delegation = config.get("delegation")
    delegation = delegation if isinstance(delegation, dict) else {}
    observation = normalized_runtime_observation(
        runtime_observation,
        expected_workflow=workflow,
        expected_run_id=run_id,
        expected_phase=phase,
        verification_issues=runtime_observation_verification_issues,
    )
    host_gate = delegation_host_capability_gate(
        runtime_observation,
        expected_workflow=workflow,
        expected_run_id=run_id,
        expected_phase=phase,
        verification_issues=runtime_observation_verification_issues,
    )
    gate_ref = str(delegation.get("economics_gate_ref", "")).strip()
    evidence = load_delegation_gate_evidence(root, gate_ref)
    passed, economics_reason = delegation_gate_passed(
        delegation=delegation,
        cost_policy=cost_policy,
        evidence=evidence,
        host_surface=str(observation["host_surface"]) if observation["host_trusted"] else "",
        model_provider=str(observation["model_provider"]) if observation["model_trusted"] else "",
        model=str(observation["model"]) if observation["model_trusted"] else "",
        execution_mode="native-subagents",
        runtime_model_trusted=bool(observation["model_trusted"]),
    )
    task_class_eligible = task_class in delegation.get("eligible_task_classes", [])
    blockers: list[str] = []
    if not host_gate["eligible"]:
        blockers.append(str(host_gate["reason"]))
    if not passed:
        blockers.append(economics_reason)
    if not task_class_eligible:
        blockers.append(f"task class {task_class!r} is not eligible for delegation")
    eligible = bool(host_gate["eligible"] and passed and task_class_eligible)
    if eligible and not delegation_requested:
        blockers.append("delegation trigger not present; explicit request or owner instruction is required")
    effective = phase_declared if eligible and delegation_requested else 1
    effective_mode = "native-subagents" if effective > 1 else "direct-tools"
    return {
        "declared_worker_count": phase_declared,
        "effective_worker_count": effective,
        "eligible": eligible,
        "isolation_mode": mode,
        "serial_fallback_reason": blockers[0] if blockers else "",
        "serial_fallback_reasons": blockers,
        "available_orchestration_mode": host_gate["available_orchestration_mode"],
        "effective_orchestration_mode": effective_mode,
        "host_capability_eligible": host_gate["eligible"],
        "host_capability_reason": host_gate["reason"],
        "host_surface": host_gate["host_surface"],
        "required_host_capabilities": host_gate["required_capabilities"],
        "missing_host_capabilities": host_gate["missing_capabilities"],
        "economics_gate_ref": gate_ref,
        "trigger": delegation.get("trigger", ""),
        "max_depth": delegation.get("max_depth", 1),
        "delegation_requested": bool(delegation_requested),
        "task_class": task_class,
    }


def phase_budget_ref(phase: str, prompt_adapter: str) -> str:
    adapter = str(prompt_adapter).strip()
    if adapter in PHASE_BUDGET_REFS:
        return adapter
    lowered = str(phase).lower()
    if "rout" in lowered or "orient" in lowered:
        return "routing"
    if "test" in lowered:
        return "test-authoring"
    if "valid" in lowered or "quality" in lowered or "hardening" in lowered:
        return "validation"
    if "implement" in lowered or "execute" in lowered or "migration" in lowered or "modern" in lowered:
        return "implementation"
    if "handoff" in lowered or "finish" in lowered or "close" in lowered:
        return "handoff"
    if "plan" in lowered or "review" in lowered or "approval" in lowered or "assess" in lowered:
        return "planning"
    return "evidence"


def numeric_phase_budget_details(
    policy: dict[str, Any] | None,
    budget_ref: str,
) -> dict[str, Any]:
    canonical_default = common.repo_policy.default_policy_document()["cost_policy"]["budgets"]["phases"]
    default_value = int(canonical_default["default_tokens"])
    if not isinstance(policy, dict) or not policy:
        return {
            "budget_tokens": default_value,
            "budget_source": "default-missing",
            "budget_issue": "",
        }
    budgets = policy.get("budgets")
    phases = budgets.get("phases") if isinstance(budgets, dict) else None
    if not isinstance(phases, dict):
        return {
            "budget_tokens": default_value,
            "budget_source": "fallback-invalid",
            "budget_issue": "cost_policy.budgets.phases must be an object.",
        }
    configured_default = phases.get("default_tokens")
    if not isinstance(configured_default, int) or isinstance(configured_default, bool) or configured_default <= 0:
        return {
            "budget_tokens": default_value,
            "budget_source": "fallback-invalid",
            "budget_issue": "cost_policy.budgets.phases.default_tokens must be a positive integer (boolean is not allowed).",
        }
    default_value = configured_default
    raw_budgets = phases.get("overrides")
    if not isinstance(raw_budgets, dict):
        return {
            "budget_tokens": default_value,
            "budget_source": "fallback-invalid",
            "budget_issue": "cost_policy.budgets.phases.overrides must be an object.",
        }
    if budget_ref not in raw_budgets:
        return {
            "budget_tokens": default_value,
            "budget_source": "default-missing",
            "budget_issue": "",
        }
    value = raw_budgets.get(budget_ref)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return {
            "budget_tokens": value,
            "budget_source": "configured",
            "budget_issue": "",
        }
    return {
        "budget_tokens": default_value,
        "budget_source": "fallback-invalid",
        "budget_issue": (
            f"cost_policy.budgets.phases.overrides.{budget_ref} must be a positive integer "
            "(boolean is not allowed)."
        ),
    }


def numeric_phase_budget(policy: dict[str, Any] | None, budget_ref: str) -> int:
    return int(numeric_phase_budget_details(policy, budget_ref)["budget_tokens"])


def execution_budget_fields(
    *,
    phase: str,
    prompt_adapter: str,
    cost_policy: dict[str, Any] | None,
    effective_context_tokens: int | None,
) -> dict[str, Any]:
    budget_ref = phase_budget_ref(phase, prompt_adapter)
    budget = numeric_phase_budget_details(cost_policy, budget_ref)
    budget_tokens = int(budget["budget_tokens"])
    measured = (
        effective_context_tokens
        if isinstance(effective_context_tokens, int) and not isinstance(effective_context_tokens, bool) and effective_context_tokens >= 0
        else None
    )
    return {
        "context_budget_ref": budget_ref,
        "budget_tokens": budget_tokens,
        "budget_source": budget["budget_source"],
        "budget_issue": budget["budget_issue"],
        "effective_context_tokens": measured,
        "remaining_margin_tokens": budget_tokens - measured if measured is not None else None,
        "within_budget": measured <= budget_tokens if measured is not None else None,
        "context_measurement": "measured" if measured is not None else "not-measured",
    }


def manifest_tasks(manifest: dict[str, Any]) -> list[str]:
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list):
        return []
    task_ids: list[str] = []
    for task in tasks:
        if isinstance(task, dict):
            task_id = str(task.get("id", "")).strip()
            if task_id:
                task_ids.append(task_id)
    return task_ids


def phase_id(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("id", "")).strip()
    return ""


def phase_ids(manifest: dict[str, Any]) -> list[str]:
    phases = manifest.get("phases")
    if not isinstance(phases, list):
        return []
    return [phase_id(item) for item in phases if phase_id(item)]


def read_workflow_manifest(module_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    return common.read_json_file(module_dir / "module.json")


def configured_worker_profiles(manifest: dict[str, Any]) -> dict[str, Any]:
    value = manifest.get("worker_profiles")
    return value if isinstance(value, dict) else {}


def config_schema_version(config: dict[str, Any]) -> int:
    value = config.get("schema_version", 1)
    return int(value) if isinstance(value, int) else -1


def config_mode(config: dict[str, Any]) -> str:
    return str(config.get("mode", "auto-when-supported")).strip()


def config_extends(config: dict[str, Any]) -> str:
    return str(config.get("extends", "portable-default")).strip()


def merged_profiles(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    inherited: dict[str, dict[str, Any]] = {}
    extends = config_extends(config)
    profile_sets = built_in_profile_sets()
    if isinstance(extends, str) and extends in profile_sets:
        inherited = {key: dict(value) for key, value in profile_sets[extends].items()}
    raw_profiles = config.get("profiles")
    if isinstance(raw_profiles, dict):
        for key, value in raw_profiles.items():
            if isinstance(value, dict):
                inherited[str(key)] = dict(value)
    return inherited


def validate_route_endpoint(value: object, label: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return [f"{label} must be an object."]
    allowed_fields = {"model_provider", "agent_type", "model", "deliberation_tier"}
    unknown_fields = sorted(set(value) - allowed_fields)
    if unknown_fields:
        errors.append(f"{label} has unsupported fields: {', '.join(unknown_fields)}.")
    model_provider = str(value.get("model_provider", "")).strip()
    if model_provider not in MODEL_PROVIDERS:
        errors.append(
            f"{label}.model_provider must be one of: {', '.join(sorted(MODEL_PROVIDERS))}."
        )
    model = str(value.get("model", "")).strip()
    if not model:
        errors.append(f"{label}.model must be a non-empty string.")
    tier = str(value.get("deliberation_tier", "")).strip()
    if tier not in DELIBERATION_TIERS:
        errors.append(
            f"{label}.deliberation_tier must be one of: {', '.join(sorted(DELIBERATION_TIERS))}."
        )
    if "agent_type" in value:
        agent_type = str(value.get("agent_type", "")).strip()
        if agent_type and not WORKER_ID_PATTERN.match(agent_type):
            errors.append(f"{label}.agent_type must use lowercase letters, digits, and hyphens.")
    return errors


def validate_profile_metadata(profile: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    profile_text_limit = common.project_policy_int("limits.workflow.profile_text_chars")
    for field in sorted(set(profile) - PROFILE_FIELDS):
        errors.append(f"{label}.{field} is not allowed; semantic profiles cannot carry model or host axes.")
    for field, allowed in (
        ("prompt_adapter", PROMPT_ADAPTERS),
        ("context_budget", CONTEXT_BUDGETS),
        ("tool_policy", TOOL_POLICIES),
        ("validation_gate", VALIDATION_GATES),
    ):
        value = str(profile.get(field, "")).strip()
        if not value:
            errors.append(f"{label}.{field} must be a non-empty string.")
        elif value not in allowed:
            errors.append(f"{label}.{field} must be one of: {', '.join(sorted(allowed))}.")
    output = str(profile.get("expected_output", "")).strip()
    if not output:
        errors.append(f"{label}.expected_output must be a non-empty string.")
    elif len(output) > profile_text_limit:
        errors.append(f"{label}.expected_output must be {profile_text_limit} characters or fewer.")
    return errors


def normalized_phase_assignments(manifest: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, str]:
    config = config if isinstance(config, dict) else configured_worker_profiles(manifest)
    phases = phase_ids(manifest)
    mapping = config.get("phase_assignments")
    if isinstance(mapping, dict):
        return {str(phase).strip(): str(profile).strip() for phase, profile in mapping.items()}
    return {}


def normalized_task_assignments(manifest: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, str]:
    config = config if isinstance(config, dict) else configured_worker_profiles(manifest)
    tasks = manifest_tasks(manifest)
    mapping = config.get("task_assignments")
    if isinstance(mapping, dict):
        return {str(task).strip(): str(profile).strip() for task, profile in mapping.items()}
    sequence = config.get("task_profiles")
    if isinstance(sequence, list) and len(sequence) == len(tasks):
        return {
            task: str(profile).strip()
            for task, profile in zip(tasks, sequence)
        }
    return {}


def validate_model_compatibility(catalog: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    compatibility = catalog.get("model_compatibility")
    overlays = catalog.get("model_prompt_overlays")
    if not isinstance(compatibility, dict):
        return ["model_compatibility must be an object for catalog schema_version 3."]
    if not isinstance(overlays, dict) or not overlays:
        errors.append("model_prompt_overlays must be a non-empty object for catalog schema_version 3.")
        overlays = {}

    selection_mode = str(compatibility.get("selection_mode", "")).strip()
    if selection_mode not in MODEL_SELECTION_MODES:
        errors.append(
            "model_compatibility.selection_mode must be one of: "
            f"{', '.join(sorted(MODEL_SELECTION_MODES))}."
        )
    unknown_policy = str(compatibility.get("unknown_model_policy", "")).strip()
    if unknown_policy not in UNKNOWN_MODEL_POLICIES:
        errors.append(
            "model_compatibility.unknown_model_policy must be one of: "
            f"{', '.join(sorted(UNKNOWN_MODEL_POLICIES))}."
        )
    tiers = compatibility.get("portable_deliberation_tiers")
    if not isinstance(tiers, list) or set(tiers) != DELIBERATION_TIERS or len(tiers) != len(DELIBERATION_TIERS):
        errors.append(
            "model_compatibility.portable_deliberation_tiers must contain exactly: "
            f"{', '.join(sorted(DELIBERATION_TIERS))}."
        )
    if _iso_date(compatibility.get("verified_at")) is None:
        errors.append("model_compatibility.verified_at must be an ISO date.")
    stale_after_days = compatibility.get("stale_after_days")
    if (
        not isinstance(stale_after_days, int)
        or isinstance(stale_after_days, bool)
        or stale_after_days < 1
        or stale_after_days > 365
    ):
        errors.append("model_compatibility.stale_after_days must be an integer from 1 to 365.")

    sources = compatibility.get("sources")
    source_ids: set[str] = set()
    if not isinstance(sources, list) or not sources:
        errors.append("model_compatibility.sources must be a non-empty list.")
    else:
        for index, source in enumerate(sources):
            label = f"model_compatibility.sources[{index}]"
            if not isinstance(source, dict):
                errors.append(f"{label} must be an object.")
                continue
            source_id = str(source.get("id", "")).strip()
            if not WORKER_ID_PATTERN.fullmatch(source_id):
                errors.append(f"{label}.id must use lowercase letters, digits, and hyphens.")
            elif source_id in source_ids:
                errors.append(f"{label}.id duplicates '{source_id}'.")
            source_ids.add(source_id)
            if not _is_approved_https_source(source.get("url")):
                errors.append(f"{label}.url must use HTTPS on an approved provider documentation domain.")
            for field in ("proves", "does_not_prove"):
                if not isinstance(source.get(field), str) or not source.get(field, "").strip():
                    errors.append(f"{label}.{field} must be a non-empty string.")

    overlay_ids = {str(item) for item in overlays}
    if "generic-v1" not in overlay_ids:
        errors.append("model_prompt_overlays must declare generic-v1.")
    for overlay_id, overlay in overlays.items():
        label = f"model_prompt_overlays.{overlay_id}"
        if not OVERLAY_ID_PATTERN.fullmatch(str(overlay_id)):
            errors.append(f"{label} id must use lowercase letters, digits, dots, and hyphens.")
        if not isinstance(overlay, dict):
            errors.append(f"{label} must be an object.")
            continue
        unsupported = sorted(set(overlay).difference(OVERLAY_ALLOWED_FIELDS))
        if unsupported:
            errors.append(
                f"{label} has unsupported fields: {', '.join(unsupported)}; "
                f"allowed fields are: {', '.join(sorted(OVERLAY_ALLOWED_FIELDS))}."
            )
        version = overlay.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            errors.append(f"{label}.version must be a positive integer.")
        generation = str(overlay.get("generation", "")).strip()
        if generation not in OVERLAY_GENERATIONS:
            errors.append(f"{label}.generation must be one of: {', '.join(sorted(OVERLAY_GENERATIONS))}.")
        promotion_state = str(overlay.get("promotion_state", "")).strip()
        if promotion_state not in MODEL_PROMOTION_STATES:
            errors.append(
                f"{label}.promotion_state must be one of: {', '.join(sorted(MODEL_PROMOTION_STATES))}."
            )
        instructions = overlay.get("instructions")
        if not isinstance(instructions, list) or not instructions or not all(
            isinstance(item, str) and item.strip() for item in instructions
        ):
            errors.append(f"{label}.instructions must be a non-empty list of strings.")
        refs = overlay.get("source_refs")
        if not isinstance(refs, list) or not all(isinstance(item, str) and item.strip() for item in refs):
            errors.append(f"{label}.source_refs must be a list of non-empty strings.")
        else:
            for ref in refs:
                if ref not in source_ids:
                    errors.append(f"{label}.source_refs references unknown source '{ref}'.")

    models = compatibility.get("models")
    model_keys: set[tuple[str, str]] = set()
    if not isinstance(models, list) or not models:
        errors.append("model_compatibility.models must be a non-empty list.")
        models = []
    for index, model in enumerate(models):
        label = f"model_compatibility.models[{index}]"
        if not isinstance(model, dict):
            errors.append(f"{label} must be an object.")
            continue
        model_provider = str(model.get("model_provider", "")).strip()
        model_id = str(model.get("model", "")).strip()
        key = (model_provider, model_id)
        if model_provider not in MODEL_PROVIDERS:
            errors.append(
                f"{label}.model_provider must be one of: {', '.join(sorted(MODEL_PROVIDERS))}."
            )
        if not model_id:
            errors.append(f"{label}.model must be a non-empty string.")
        if key in model_keys:
            errors.append(f"{label} duplicates model-provider/model '{model_provider}/{model_id}'.")
        model_keys.add(key)
        overlay_id = str(model.get("overlay_id", "")).strip()
        if overlay_id not in overlay_ids:
            errors.append(f"{label}.overlay_id references unknown overlay '{overlay_id}'.")
        promotion_state = str(model.get("promotion_state", "")).strip()
        if promotion_state not in MODEL_PROMOTION_STATES:
            errors.append(
                f"{label}.promotion_state must be one of: {', '.join(sorted(MODEL_PROMOTION_STATES))}."
            )

    referenced_overlay_ids = {
        str(model.get("overlay_id", "")).strip()
        for model in models
        if isinstance(model, dict)
    }
    for overlay_id in sorted(overlay_ids - {"generic-v1"} - referenced_overlay_ids):
        errors.append(f"model_prompt_overlays.{overlay_id} is not referenced by any exact model mapping.")

    aliases = compatibility.get("aliases")
    alias_keys: set[tuple[str, str]] = set()
    if not isinstance(aliases, list):
        errors.append("model_compatibility.aliases must be a list.")
        aliases = []
    for index, alias in enumerate(aliases):
        label = f"model_compatibility.aliases[{index}]"
        if not isinstance(alias, dict):
            errors.append(f"{label} must be an object.")
            continue
        model_provider = str(alias.get("model_provider", "")).strip()
        alias_id = str(alias.get("alias", "")).strip()
        canonical = str(alias.get("canonical_model", "")).strip()
        key = (model_provider, alias_id)
        if model_provider not in MODEL_PROVIDERS:
            errors.append(
                f"{label}.model_provider must be one of: {', '.join(sorted(MODEL_PROVIDERS))}."
            )
        if not alias_id or not canonical or alias_id == canonical:
            errors.append(f"{label} must declare different non-empty alias and canonical_model values.")
        if key in alias_keys:
            errors.append(f"{label} duplicates model-provider/alias '{model_provider}/{alias_id}'.")
        alias_keys.add(key)
        if key in model_keys:
            errors.append(
                f"{label}.alias collides with exact model-provider/model '{model_provider}/{alias_id}'."
            )
        if (model_provider, canonical) not in model_keys:
            errors.append(
                f"{label}.canonical_model references unknown model '{model_provider}/{canonical}'."
            )
    return errors


def validate_surface_contracts(catalog: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    adapters = catalog.get("surface_adapters")
    if not isinstance(adapters, dict) or not adapters:
        return ["surface_adapters must be a non-empty object."]
    observed_surfaces: set[str] = set()
    for adapter_id, adapter in adapters.items():
        label = f"surface_adapters.{adapter_id}"
        if not OVERLAY_ID_PATTERN.fullmatch(str(adapter_id)):
            errors.append(f"{label} id must use lowercase letters, digits, dots, and hyphens.")
        if not isinstance(adapter, dict):
            errors.append(f"{label} must be an object.")
            continue
        unknown = sorted(
            set(adapter)
            - {
                "host_surfaces",
                "instruction_surfaces",
                "base_orchestration_mode",
                "capability_modes",
                "capability_requirements",
                "delivery_directive",
            }
        )
        if unknown:
            errors.append(f"{label} has unsupported fields: {', '.join(unknown)}.")
        surfaces = adapter.get("host_surfaces")
        if not isinstance(surfaces, list) or not surfaces:
            errors.append(f"{label}.host_surfaces must be a non-empty list.")
        else:
            for surface in surfaces:
                surface_id = str(surface).strip()
                if surface_id not in HOST_SURFACES:
                    errors.append(
                        f"{label}.host_surfaces contains unsupported surface '{surface_id}'."
                    )
                elif surface_id in observed_surfaces:
                    errors.append(f"host surface '{surface_id}' is assigned to multiple adapters.")
                observed_surfaces.add(surface_id)
        instructions = adapter.get("instruction_surfaces")
        if not isinstance(instructions, list) or not instructions or not all(
            isinstance(item, str) and item.strip() for item in instructions
        ):
            errors.append(f"{label}.instruction_surfaces must be a non-empty list of strings.")
        base_mode = str(adapter.get("base_orchestration_mode", "")).strip()
        if not base_mode:
            errors.append(f"{label}.base_orchestration_mode must be a non-empty string.")
        if "delivery_directive" in adapter and (
            not isinstance(adapter.get("delivery_directive"), str)
            or not str(adapter.get("delivery_directive", "")).strip()
        ):
            errors.append(f"{label}.delivery_directive must be a non-empty string when provided.")
        modes = adapter.get("capability_modes")
        if not isinstance(modes, dict):
            errors.append(f"{label}.capability_modes must be an object.")
        else:
            for capability, mode in modes.items():
                if capability not in CAPABILITY_IDS:
                    errors.append(f"{label}.capability_modes has unsupported capability '{capability}'.")
                if not isinstance(mode, str) or not mode.strip():
                    errors.append(f"{label}.capability_modes.{capability} must be a non-empty string.")
        requirements = adapter.get("capability_requirements")
        if not isinstance(requirements, dict):
            errors.append(f"{label}.capability_requirements must be an object.")
        else:
            for capability, required_capabilities in requirements.items():
                requirement_label = f"{label}.capability_requirements.{capability}"
                if not isinstance(modes, dict) or capability not in modes:
                    errors.append(f"{requirement_label} must target a declared capability mode.")
                if (
                    not isinstance(required_capabilities, list)
                    or not required_capabilities
                    or len(required_capabilities) != len(set(required_capabilities))
                    or not all(item in CAPABILITY_IDS for item in required_capabilities)
                ):
                    errors.append(
                        f"{requirement_label} must be a unique non-empty list of supported capability ids."
                    )
    missing_surfaces = sorted(HOST_SURFACES - observed_surfaces)
    if missing_surfaces:
        errors.append("surface_adapters is missing host surfaces: " + ", ".join(missing_surfaces) + ".")

    route_sets = catalog.get("surface_route_sets")
    if not isinstance(route_sets, dict) or not route_sets:
        return errors + ["surface_route_sets must be a non-empty object."]
    for route_set_id, routes in route_sets.items():
        label = f"surface_route_sets.{route_set_id}"
        if not WORKER_ID_PATTERN.fullmatch(str(route_set_id)):
            errors.append(f"{label} id must use lowercase letters, digits, and hyphens.")
        if not isinstance(routes, dict) or not routes:
            errors.append(f"{label} must be a non-empty object.")
            continue
        for host_surface, endpoints in routes.items():
            route_label = f"{label}.{host_surface}"
            if host_surface not in HOST_SURFACES - {"unknown"}:
                errors.append(f"{route_label} uses unsupported host surface '{host_surface}'.")
            if not isinstance(endpoints, list) or not endpoints:
                errors.append(f"{route_label} must be a non-empty list of endpoints.")
                continue
            seen: set[tuple[str, str]] = set()
            for index, endpoint in enumerate(endpoints):
                endpoint_label_text = f"{route_label}[{index}]"
                errors.extend(validate_route_endpoint(endpoint, endpoint_label_text))
                if isinstance(endpoint, dict):
                    expected_provider = PROVIDER_RESPONSE_SURFACES.get(str(host_surface))
                    observed_provider = str(endpoint.get("model_provider", "")).strip()
                    if expected_provider and observed_provider != expected_provider:
                        errors.append(
                            f"{endpoint_label_text}.model_provider must be {expected_provider} "
                            f"for direct API surface {host_surface}."
                        )
                    key = (
                        str(endpoint.get("model_provider", "")).strip(),
                        str(endpoint.get("model", "")).strip(),
                    )
                    if key in seen:
                        errors.append(
                            f"{endpoint_label_text} duplicates model-provider/model '{key[0]}/{key[1]}'."
                        )
                    seen.add(key)
    return errors


def validate_profile_catalog(catalog: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if catalog.get("_error"):
        return [str(catalog["_error"])]
    catalog_version = catalog.get("schema_version")
    if catalog_version not in CATALOG_SCHEMA_VERSIONS:
        errors.append(f"schema_version must be one of: {', '.join(str(item) for item in sorted(CATALOG_SCHEMA_VERSIONS))}.")
    if catalog_version in CATALOG_SCHEMA_VERSIONS:
        errors.extend(validate_model_compatibility(catalog))
        errors.extend(validate_surface_contracts(catalog))
    profile_sets = catalog.get("profile_sets")
    if not isinstance(profile_sets, dict) or not profile_sets:
        errors.append("profile_sets must be a non-empty object.")
        return errors
    guidance = catalog.get("host_guidance", [])
    if not isinstance(guidance, list) or not all(isinstance(item, str) and item.strip() for item in guidance):
        errors.append("host_guidance must be a list of non-empty strings.")
    host_rows = catalog.get("host_support", [])
    if not isinstance(host_rows, list) or not host_rows:
        errors.append("host_support must be a non-empty list.")
    else:
        required_host_fields = ("host", "worker_selection", "model_selection", "fallback", "mitigation")
        for index, row in enumerate(host_rows):
            row_label = f"host_support[{index}]"
            if not isinstance(row, dict):
                errors.append(f"{row_label} must be an object.")
                continue
            for field in required_host_fields:
                if not isinstance(row.get(field), str) or not row.get(field, "").strip():
                    errors.append(f"{row_label}.{field} must be a non-empty string.")
            host_id = str(row.get("host", "")).strip()
            if host_id and not WORKER_ID_PATTERN.match(host_id):
                errors.append(f"{row_label}.host must use lowercase letters, digits, and hyphens.")
    authority = catalog.get("validation_authority")
    if not isinstance(authority, dict):
        errors.append("validation_authority must be an object.")
    else:
        for field in ("authoritative", "advisory"):
            values = authority.get(field)
            valid_values = isinstance(values, list) and values and all(
                isinstance(item, str) and item.strip()
                for item in values
            )
            if not valid_values:
                errors.append(f"validation_authority.{field} must be a non-empty list of strings.")
        if not isinstance(authority.get("required_record"), str) or not authority.get("required_record", "").strip():
            errors.append("validation_authority.required_record must be a non-empty string.")
    execution_mode_rows = catalog.get("execution_modes")
    if not isinstance(execution_mode_rows, dict):
        errors.append("execution_modes must be an object.")
    else:
        missing_modes = sorted(EXECUTION_MODE_IDS - set(execution_mode_rows))
        unknown_modes = sorted(set(execution_mode_rows) - EXECUTION_MODE_IDS)
        if missing_modes:
            errors.append(f"execution_modes is missing: {', '.join(missing_modes)}.")
        if unknown_modes:
            errors.append(f"execution_modes has unknown modes: {', '.join(unknown_modes)}.")
        for mode_id, row in execution_mode_rows.items():
            label = f"execution_modes.{mode_id}"
            if not isinstance(row, dict):
                errors.append(f"{label} must be an object.")
                continue
            for field in ("authority", "lifecycle_owner", "write_policy", "fallback"):
                if not isinstance(row.get(field), str) or not row.get(field, "").strip():
                    errors.append(f"{label}.{field} must be a non-empty string.")
            evidence = row.get("required_evidence")
            if not isinstance(evidence, list) or not evidence or not all(
                isinstance(item, str) and item.strip() for item in evidence
            ):
                errors.append(f"{label}.required_evidence must be a non-empty list of strings.")
    routing = catalog.get("risk_routing")
    if not isinstance(routing, dict):
        errors.append("risk_routing must be an object.")
    else:
        for field in (
            "status",
            "selection_mode",
            "verified_at",
            "availability_boundary",
            "promotion_gate",
        ):
            if not isinstance(routing.get(field), str) or not routing.get(field, "").strip():
                errors.append(f"risk_routing.{field} must be a non-empty string.")
        status = str(routing.get("status", "")).strip()
        if routing and status not in RISK_ROUTING_STATUSES:
            errors.append(f"risk_routing.status must be one of: {', '.join(sorted(RISK_ROUTING_STATUSES))}.")
        selection_mode = str(routing.get("selection_mode", "")).strip()
        if routing and selection_mode not in RISK_SELECTION_MODES:
            errors.append(
                f"risk_routing.selection_mode must be one of: {', '.join(sorted(RISK_SELECTION_MODES))}."
            )
        source_refs = routing.get("source_refs")
        compatibility = catalog.get("model_compatibility")
        known_source_ids = {
            str(item.get("id", "")).strip()
            for item in compatibility.get("sources", [])
            if isinstance(item, dict)
        } if isinstance(compatibility, dict) else set()
        if (
            not isinstance(source_refs, list)
            or not source_refs
            or len(source_refs) != len(set(source_refs))
            or not all(isinstance(item, str) and item in known_source_ids for item in source_refs)
        ):
            errors.append("risk_routing.source_refs must be a unique non-empty list of model_compatibility source ids.")
        if routing and _iso_date(routing.get("verified_at")) is None:
            errors.append("risk_routing.verified_at must be an ISO date.")
        for field in ("selection_basis", "rules"):
            values = routing.get(field)
            if not isinstance(values, list) or not values or not all(
                isinstance(item, str) and item.strip() for item in values
            ):
                errors.append(f"risk_routing.{field} must be a non-empty list of strings.")
        routes = routing.get("routes")
        known_profiles = {
            str(profile_id)
            for profiles in profile_sets.values()
            if isinstance(profiles, dict)
            for profile_id in profiles
        }
        if not isinstance(routes, list) or not routes:
            errors.append("risk_routing.routes must be a non-empty list.")
        else:
            seen_route_ids: set[str] = set()
            for index, route in enumerate(routes):
                label = f"risk_routing.routes[{index}]"
                if not isinstance(route, dict):
                    errors.append(f"{label} must be an object.")
                    continue
                route_id = str(route.get("id", "")).strip()
                if not WORKER_ID_PATTERN.match(route_id):
                    errors.append(f"{label}.id must use lowercase letters, digits, and hyphens.")
                elif route_id in seen_route_ids:
                    errors.append(f"{label}.id duplicates '{route_id}'.")
                seen_route_ids.add(route_id)
                condition_keys = [key for key in ("when_any", "when_all") if key in route]
                if len(condition_keys) != 1:
                    errors.append(f"{label} must declare exactly one of when_any or when_all.")
                conditions = route.get(condition_keys[0]) if len(condition_keys) == 1 else None
                if not isinstance(conditions, list) or not conditions or not all(
                    isinstance(item, str) and item.strip() for item in conditions
                ):
                    errors.append(f"{label} must declare non-empty condition strings.")
                profiles = route.get("profiles")
                if not isinstance(profiles, list) or not profiles:
                    errors.append(f"{label}.profiles must be a non-empty list.")
                else:
                    for profile_id in profiles:
                        if str(profile_id) not in known_profiles:
                            errors.append(f"{label}.profiles references unknown profile '{profile_id}'.")
    for set_id, profiles in profile_sets.items():
        set_label = f"profile_sets.{set_id}"
        if not WORKER_ID_PATTERN.match(str(set_id)):
            errors.append(f"{set_label} id must use lowercase letters, digits, and hyphens.")
        if not isinstance(profiles, dict) or not profiles:
            errors.append(f"{set_label} must be a non-empty object.")
            continue
        for profile_id, profile in profiles.items():
            profile_label = f"{set_label}.{profile_id}"
            if not WORKER_ID_PATTERN.match(str(profile_id)):
                errors.append(f"{profile_label} id must use lowercase letters, digits, and hyphens.")
            if not isinstance(profile, dict):
                errors.append(f"{profile_label} must be an object.")
                continue
            purpose = str(profile.get("purpose", "")).strip()
            if not purpose:
                errors.append(f"{profile_label}.purpose must be a non-empty string.")
            errors.extend(validate_profile_metadata(profile, profile_label))
            route_set = str(profile.get("route_set", "")).strip()
            catalog_route_sets = catalog.get("surface_route_sets", {})
            if not isinstance(catalog_route_sets, dict) or route_set not in catalog_route_sets:
                errors.append(f"{profile_label}.route_set references unknown route set '{route_set}'.")
    return errors


def validate_worker_profiles(manifest: dict[str, Any]) -> list[str]:
    config = manifest.get("worker_profiles")
    if config is None:
        return []
    if not isinstance(config, dict):
        return ["module.json worker_profiles must be an object when provided."]

    errors: list[str] = []
    label = "module.json worker_profiles"
    catalog_errors = validate_profile_catalog(load_worker_profile_config())
    errors.extend(f"worker profile catalog: {error}" for error in catalog_errors)
    if "schema_version" in config and config_schema_version(config) != 1:
        errors.append(f"{label}.schema_version must be 1.")
    extends = config_extends(config)
    profile_sets = built_in_profile_sets()
    if not isinstance(config.get("extends", extends), str) or extends not in profile_sets:
        errors.append(f"{label}.extends must be one of: {', '.join(sorted(profile_sets))}.")
    mode = config_mode(config)
    if mode not in PROFILE_MODES:
        errors.append(f"{label}.mode must be one of: {', '.join(sorted(PROFILE_MODES))}.")
    max_workers = config.get("max_parallel_workers", 1)
    if not isinstance(max_workers, int) or max_workers < 1 or max_workers > 4:
        errors.append(f"{label}.max_parallel_workers must be an integer from 1 to 4.")

    profiles = merged_profiles(config)
    if not profiles:
        errors.append(f"{label} must define or extend at least one profile.")
    for profile_id, profile in profiles.items():
        profile_label = f"{label}.profiles.{profile_id}"
        if not WORKER_ID_PATTERN.match(profile_id):
            errors.append(f"{profile_label} id must use lowercase letters, digits, and hyphens.")
        purpose = str(profile.get("purpose", "")).strip()
        if not purpose:
            errors.append(f"{profile_label}.purpose must be a non-empty string.")
        errors.extend(validate_profile_metadata(profile, profile_label))
        route_set = str(profile.get("route_set", "")).strip()
        if route_set not in surface_route_sets():
            errors.append(f"{profile_label}.route_set references unknown route set '{route_set}'.")

    known_profiles = set(profiles)
    known_phases = set(phase_ids(manifest))
    phase_mapping = config.get("phase_assignments")
    phase_assignments = normalized_phase_assignments(manifest, config)
    if "phase_profiles" in config:
        errors.append(f"{label}.phase_profiles is not supported; use phase_assignments keyed by phase ID.")
    if phase_mapping is not None and (not isinstance(phase_mapping, dict) or not phase_mapping):
        errors.append(f"{label}.phase_assignments must be a non-empty object when provided.")
    if not phase_assignments:
        errors.append(f"{label} must define phase_assignments.")
    elif known_phases:
        for phase in known_phases:
            if phase not in phase_assignments:
                errors.append(f"{label} is missing phase '{phase}'.")
        for phase, profile_id in phase_assignments.items():
            phase_text = str(phase).strip()
            profile_text = str(profile_id).strip()
            if phase_text not in known_phases:
                errors.append(f"{label} references unknown phase '{phase_text}'.")
            if profile_text not in known_profiles:
                errors.append(f"{label}.{phase_text} references unknown profile '{profile_text}'.")

    known_tasks = set(manifest_tasks(manifest))
    task_mapping = config.get("task_assignments")
    task_sequence = config.get("task_profiles")
    task_assignments = normalized_task_assignments(manifest, config)
    if task_mapping is not None and task_sequence is not None:
        errors.append(f"{label} must use either task_assignments or task_profiles, not both.")
    if task_sequence is not None:
        if not isinstance(task_sequence, list):
            errors.append(f"{label}.task_profiles must be a list when provided.")
        elif len(task_sequence) != len(known_tasks):
            errors.append(f"{label}.task_profiles must contain one profile for each declared task.")
    if task_mapping is not None:
        if not isinstance(task_mapping, dict):
            errors.append(f"{label}.task_assignments must be an object when provided.")
        elif task_mapping:
            for task, profile_id in task_assignments.items():
                task_text = str(task).strip()
                profile_text = str(profile_id).strip()
                if task_text not in known_tasks:
                    errors.append(f"{label}.task_assignments references unknown task '{task_text}'.")
                if profile_text not in known_profiles:
                    errors.append(f"{label}.task_assignments.{task_text} references unknown profile '{profile_text}'.")
    if manifest.get("schema_version") == 3:
        contract_errors, _warnings = module_contract_v3.validate_v3(manifest)
        errors.extend(
            error
            for error in contract_errors
            if "worker_profiles.delegation" in error or "parallel_safety" in error
        )
    return errors


def endpoint_summary(endpoint: dict[str, Any] | None) -> dict[str, str]:
    endpoint = endpoint if isinstance(endpoint, dict) else {}
    return {
        "model_provider": str(endpoint.get("model_provider", "")),
        "agent_type": str(endpoint.get("agent_type", "")),
        "model": str(endpoint.get("model", "")),
        "deliberation_tier": str(endpoint.get("deliberation_tier", "")),
    }


def profile_execution_metadata(profile: dict[str, Any]) -> dict[str, str]:
    return {
        "prompt_adapter": str(profile.get("prompt_adapter", "general")).strip() or "general",
        "context_budget": str(profile.get("context_budget", "standard")).strip() or "standard",
        "tool_policy": str(profile.get("tool_policy", "read-only")).strip() or "read-only",
        "expected_output": str(profile.get("expected_output", "Current phase output with evidence.")).strip(),
        "validation_gate": str(profile.get("validation_gate", "record-evidence")).strip() or "record-evidence",
    }


def execution_instruction_header(profile_id: str, profile: dict[str, Any]) -> list[str]:
    execution = profile_execution_metadata(profile)
    purpose = str(profile.get("purpose", "")).strip() or "Complete the assigned workflow phase."
    return [
        f"Use semantic profile `{profile_id}`: {purpose}",
        f"Prompt adapter={execution['prompt_adapter']}; surface routes are advisory until runtime attestation.",
        f"Budget={execution['context_budget']}; tools={execution['tool_policy']}; gate={execution['validation_gate']}.",
        f"Output: {execution['expected_output']}",
        "Do not expose hidden reasoning; record deterministic evidence, blockers, validation status, and any active-model fallback.",
    ]


def profile_summary(
    profile_id: str,
    profiles: dict[str, dict[str, Any]],
    *,
    phase: str = "",
    cost_policy: dict[str, Any] | None = None,
    effective_context_tokens: int | None = None,
) -> dict[str, Any]:
    profile = profiles.get(profile_id, {})
    route_set_id = str(profile.get("route_set", "")).strip()
    route_sets = surface_route_sets()
    raw_routes = route_sets.get(route_set_id, {}) if isinstance(route_sets, dict) else {}
    routes: dict[str, list[dict[str, str]]] = {}
    if isinstance(raw_routes, dict):
        for host_surface, endpoints in raw_routes.items():
            if isinstance(endpoints, list):
                routes[str(host_surface)] = [
                    endpoint_summary(item) for item in endpoints if isinstance(item, dict)
                ]
    execution = profile_execution_metadata(profile if isinstance(profile, dict) else {})
    if phase:
        execution.update(
            execution_budget_fields(
                phase=phase,
                prompt_adapter=execution["prompt_adapter"],
                cost_policy=cost_policy,
                effective_context_tokens=effective_context_tokens,
            )
        )
    return {
        "id": profile_id,
        "purpose": str(profile.get("purpose", "")),
        "route_set": route_set_id,
        "surface_routes": routes,
        "execution": {
            **execution,
            "instruction_header": execution_instruction_header(profile_id, profile if isinstance(profile, dict) else {}),
        },
    }


def endpoint_label(endpoint: dict[str, Any]) -> str:
    return (
        f"{endpoint.get('model_provider', '')} {endpoint.get('model', '')} "
        f"{endpoint.get('deliberation_tier', '')}"
    ).strip()


def profile_catalog_report(*, compact: bool = False) -> dict[str, Any]:
    catalog = load_worker_profile_config()
    issues = validate_profile_catalog(catalog)
    warnings = catalog_warnings(catalog)
    profile_sets = built_in_profile_sets()
    sets: list[dict[str, Any]] = []
    for set_id, profiles in sorted(profile_sets.items()):
        profile_rows = []
        if isinstance(profiles, dict):
            for profile_id, profile in sorted(profiles.items()):
                if not isinstance(profile, dict):
                    continue
                row = profile_summary(str(profile_id), {str(profile_id): profile})
                if compact:
                    row.pop("purpose", None)
                profile_rows.append(row)
        sets.append({"id": set_id, "profile_count": len(profile_rows), "profiles": profile_rows})
    return {
        "schema_version": 1,
        "tool": "workflow-manager.worker-profiles",
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "summary": {
            "profile_set_count": len(sets),
            "profile_count": sum(int(item.get("profile_count", 0) or 0) for item in sets),
            "issue_count": len(issues),
            "warning_count": len(warnings),
        },
        "issues": issues,
        "warnings": warnings,
        "profile_sets": sets,
        "host_guidance": host_guidance(),
        "host_support": host_support(),
        "model_compatibility": model_compatibility(),
        "model_prompt_overlays": model_prompt_overlays(),
        "surface_adapters": surface_adapters(),
        "surface_route_sets": surface_route_sets(),
        "execution_modes": execution_modes(),
        "risk_routing": risk_routing(),
        "validation_authority": validation_authority(),
        "cost_guidance": [
            "Execution modes and risk routes are declarative/manual catalog guidance, not automatic host selection or enforcement.",
            "Treat specialized reviewer, implementer, coordinator, and local-validation roles as opt-in catalog guidance until measured trials justify promotion.",
            "Choose a host-surface route only after runtime attestation; otherwise use the active model with the generic overlay.",
            "Use validation-local for deterministic command runs, local-AI triage, and evidence review before paying for hosted fallbacks.",
            "A local-AI validation result is advisory; deterministic command exit codes and recorded evidence remain authoritative.",
        ],
    }


def parallel_decision_summary(
    rows: list[dict[str, Any]],
    *,
    declared_worker_count: int,
) -> dict[str, Any]:
    decisions = [
        row["parallel_safety"]
        for row in rows
        if isinstance(row.get("parallel_safety"), dict)
    ]
    parallel_rows = [
        decision
        for decision in decisions
        if decision.get("declared_worker_count", 1) > 1
    ]
    effective = max(
        [int(row.get("effective_worker_count", 1) or 1) for row in parallel_rows]
        or [1]
    )
    eligible = any(row.get("eligible") is True for row in parallel_rows)
    active_mode = next(
        (
            str(row.get("isolation_mode", ""))
            for row in parallel_rows
            if row.get("effective_worker_count", 1) > 1
        ),
        "serial-fallback" if declared_worker_count > 1 else "serial",
    )
    fallback_reasons: list[str] = []
    for row in decisions:
        reasons = row.get("serial_fallback_reasons")
        values = (
            [str(item) for item in reasons if str(item).strip()]
            if isinstance(reasons, list)
            else []
        )
        if not values and str(row.get("serial_fallback_reason", "")).strip():
            values = [str(row["serial_fallback_reason"])]
        for value in values:
            if value not in fallback_reasons:
                fallback_reasons.append(value)
    available_mode = (
        "native-subagents"
        if any(
            row.get("available_orchestration_mode") == "native-subagents"
            for row in decisions
        )
        else "direct-tools"
    )
    return {
        "declared_worker_count": declared_worker_count,
        "effective_worker_count": effective,
        "eligible": eligible,
        "isolation_mode": active_mode,
        "serial_fallback_reason": fallback_reasons[0] if fallback_reasons else "",
        "serial_fallback_reasons": fallback_reasons,
        "available_orchestration_mode": available_mode,
        "effective_orchestration_mode": "native-subagents" if effective > 1 else "direct-tools",
    }


def worker_summary(
    manifest: dict[str, Any],
    *,
    root: Path,
    cost_policy: dict[str, Any] | None = None,
    delegation_requested: bool = False,
    task_class: str = "independent-read-heavy",
    runtime_observation: object = None,
    runtime_observation_verification_issues: list[str] | None = None,
    workflow: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    config = configured_worker_profiles(manifest)
    profiles = merged_profiles(config)
    phase_assignments = normalized_phase_assignments(manifest, config)
    policy = cost_policy if isinstance(cost_policy, dict) else {}
    rows = []
    for phase in phase_ids(manifest):
        profile_id = str(phase_assignments.get(phase, "")).strip()
        parallel = phase_parallel_decision(
            manifest,
            phase,
            root=root,
            cost_policy=policy,
            delegation_requested=delegation_requested,
            task_class=task_class,
            runtime_observation=runtime_observation,
            runtime_observation_verification_issues=runtime_observation_verification_issues,
            workflow=workflow,
            run_id=run_id,
        )
        rows.append(
            {
                "phase": phase,
                "profile": profile_summary(
                    profile_id,
                    profiles,
                    phase=phase,
                    cost_policy=policy,
                )
                if profile_id
                else {},
                "parallel_safety": parallel,
            }
        )
    task_assignments = normalized_task_assignments(manifest, config)
    task_rows = []
    for task in manifest_tasks(manifest):
        profile_id = str(task_assignments.get(task, "")).strip()
        if profile_id:
            task_rows.append({"task": task, "profile": profile_summary(profile_id, profiles)})
    declared = config.get("max_parallel_workers", 1) if config else 1
    declared = declared if isinstance(declared, int) and not isinstance(declared, bool) else 1
    parallel_summary = parallel_decision_summary(
        rows,
        declared_worker_count=declared,
    )
    return {
        "schema_version": config_schema_version(config) if config else 1,
        "mode": config_mode(config) if config else "auto-when-supported",
        "extends": config_extends(config) if config else "portable-default",
        "max_parallel_workers": declared,
        **parallel_summary,
        "delegation_requested": bool(delegation_requested),
        "task_class": task_class,
        "phase_count": len(rows),
        "profile_count": len(profiles),
        "profiles": [profile_summary(profile_id, profiles) for profile_id in sorted(profiles)],
        "phase_assignments": rows,
        "task_assignments": task_rows,
    }


def _runtime_observation_issue_groups(
    value: object,
    *,
    expected_workflow: str = "",
    expected_run_id: str = "",
    expected_phase: str = "",
) -> tuple[list[str], list[str], list[str]]:
    """Validate packet, host, and model axes independently."""

    if not isinstance(value, dict):
        return ["runtime observation packet must be a JSON object"], [], []
    packet_issues: list[str] = []
    host_issues: list[str] = []
    model_issues: list[str] = []
    identity_chars = common.project_policy_int("limits.workflow.runtime_identity_chars")
    evidence_path_chars = common.project_policy_int("limits.workflow.runtime_evidence_path_chars")
    model_id_chars = common.project_policy_int("limits.workflow.runtime_model_id_chars")
    deliberation_chars = common.project_policy_int("limits.workflow.runtime_deliberation_chars")
    unknown = sorted(str(key) for key in value if key not in RUNTIME_OBSERVATION_FIELDS)
    if unknown:
        packet_issues.append("runtime observation packet has unsupported fields: " + ", ".join(unknown))
    if (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != RUNTIME_OBSERVATION_SCHEMA_VERSION
    ):
        packet_issues.append(f"runtime observation schema_version must be {RUNTIME_OBSERVATION_SCHEMA_VERSION}")
    if value.get("tool") != RUNTIME_OBSERVATION_TOOL:
        packet_issues.append(f"runtime observation tool must be {RUNTIME_OBSERVATION_TOOL}")
    for field, limit in (("workflow", identity_chars), ("run_id", identity_chars), ("phase", identity_chars)):
        raw = value.get(field)
        if not isinstance(raw, str) or not raw.strip():
            packet_issues.append(f"runtime observation {field} must be a non-empty string")
        elif len(raw) > limit:
            packet_issues.append(f"runtime observation {field} exceeds {limit} characters")
    for field, expected in (
        ("workflow", expected_workflow),
        ("run_id", expected_run_id),
        ("phase", expected_phase),
    ):
        expected_text = str(expected or "").strip()
        actual_text = str(value.get(field, "")).strip()
        if expected_text and actual_text and actual_text != expected_text:
            packet_issues.append(
                f"runtime observation {field} '{actual_text}' does not match current {field} '{expected_text}'"
            )
    evidence_path = value.get("evidence_path", "")
    if not isinstance(evidence_path, str) or not evidence_path.strip():
        packet_issues.append("runtime observation evidence_path must be a non-empty string")
    elif len(evidence_path) > evidence_path_chars:
        packet_issues.append(f"runtime observation evidence_path exceeds {evidence_path_chars} characters")

    host = value.get("host")
    model = value.get("model")
    if host is None and model is None:
        packet_issues.append("runtime observation must contain at least one of host or model")
    if host is not None:
        if not isinstance(host, dict):
            host_issues.append("runtime observation host must be an object")
        else:
            unknown_host = sorted(str(key) for key in host if key not in HOST_OBSERVATION_FIELDS)
            if unknown_host:
                host_issues.append("runtime observation host has unsupported fields: " + ", ".join(unknown_host))
            if host.get("attested") is not True:
                host_issues.append("runtime observation host.attested must be true")
            source = host.get("source")
            if not isinstance(source, str) or source not in TRUSTED_OBSERVATION_SOURCES:
                host_issues.append(
                    "runtime observation host.source must be one of: "
                    + ", ".join(sorted(TRUSTED_OBSERVATION_SOURCES))
                )
            surface = str(host.get("surface", "")).strip()
            if surface not in HOST_SURFACES:
                host_issues.append(
                    "runtime observation host.surface must be one of: "
                    + ", ".join(sorted(HOST_SURFACES))
                )
            raw_capabilities = host.get("capabilities", [])
            if not isinstance(raw_capabilities, list):
                host_issues.append("runtime observation host.capabilities must be an array")
            else:
                invalid = sorted(
                    {str(item) for item in raw_capabilities if not isinstance(item, str) or item not in CAPABILITY_IDS}
                )
                if invalid:
                    host_issues.append(
                        "runtime observation host has unsupported capabilities: " + ", ".join(invalid)
                    )
                strings = [item for item in raw_capabilities if isinstance(item, str)]
                if len(strings) != len(set(strings)):
                    host_issues.append("runtime observation host.capabilities must contain unique values")
                if source == "provider-response":
                    forbidden = sorted(set(strings) & PROVIDER_RESPONSE_FORBIDDEN_CAPABILITIES)
                    if forbidden:
                        host_issues.append(
                            "provider-response observations cannot attest host-runtime capabilities: "
                            + ", ".join(forbidden)
                        )
                    if surface not in PROVIDER_RESPONSE_SURFACES:
                        host_issues.append(
                            "provider-response host observations must use an API surface: "
                            + ", ".join(sorted(PROVIDER_RESPONSE_SURFACES))
                        )

    if model is not None:
        if not isinstance(model, dict):
            model_issues.append("runtime observation model must be an object")
        else:
            unknown_model = sorted(str(key) for key in model if key not in MODEL_OBSERVATION_FIELDS)
            if unknown_model:
                model_issues.append("runtime observation model has unsupported fields: " + ", ".join(unknown_model))
            if model.get("attested") is not True:
                model_issues.append("runtime observation model.attested must be true")
            source = model.get("source")
            if not isinstance(source, str) or source not in TRUSTED_OBSERVATION_SOURCES:
                model_issues.append(
                    "runtime observation model.source must be one of: "
                    + ", ".join(sorted(TRUSTED_OBSERVATION_SOURCES))
                )
            provider = str(model.get("provider", "")).strip()
            if provider not in MODEL_PROVIDERS:
                model_issues.append(
                    "runtime observation model.provider must be one of: "
                    + ", ".join(sorted(MODEL_PROVIDERS))
                )
            model_id = model.get("model")
            if not isinstance(model_id, str) or not model_id.strip():
                model_issues.append("runtime observation model.model must be a non-empty string")
            elif len(model_id) > model_id_chars:
                model_issues.append(f"runtime observation model.model exceeds {model_id_chars} characters")
            deliberation = model.get("observed_deliberation", "")
            if not isinstance(deliberation, str):
                model_issues.append("runtime observation model.observed_deliberation must be a string")
            elif len(deliberation) > deliberation_chars:
                model_issues.append(
                    f"runtime observation model.observed_deliberation exceeds {deliberation_chars} characters"
                )

    if isinstance(host, dict) and isinstance(model, dict):
        surface = str(host.get("surface", "")).strip()
        provider = str(model.get("provider", "")).strip()
        expected_provider = PROVIDER_RESPONSE_SURFACES.get(surface)
        if expected_provider and provider and provider != expected_provider:
            conflict = f"runtime observation host surface {surface} requires model provider {expected_provider}"
            host_issues.append(conflict)
            model_issues.append(conflict)
    return packet_issues, host_issues, model_issues


def runtime_observation_issues(
    value: object,
    *,
    expected_workflow: str = "",
    expected_run_id: str = "",
    expected_phase: str = "",
) -> list[str]:
    groups = _runtime_observation_issue_groups(
        value,
        expected_workflow=expected_workflow,
        expected_run_id=expected_run_id,
        expected_phase=expected_phase,
    )
    return [item for group in groups for item in group]


def _runtime_observation_comparable(value: dict[str, Any], evidence_path: str) -> dict[str, Any]:
    comparable: dict[str, Any] = {
        "schema_version": value.get("schema_version"),
        "tool": str(value.get("tool", "")).strip(),
        "workflow": str(value.get("workflow", "")).strip(),
        "run_id": str(value.get("run_id", "")).strip(),
        "phase": str(value.get("phase", "")).strip(),
        "evidence_path": evidence_path,
    }
    host = value.get("host")
    if isinstance(host, dict):
        comparable["host"] = {
            "attested": host.get("attested"),
            "source": str(host.get("source", "")).strip(),
            "surface": str(host.get("surface", "")).strip(),
            "capabilities": sorted(
                {str(item).strip() for item in host.get("capabilities", []) if isinstance(item, str) and str(item).strip()}
            ),
        }
    model = value.get("model")
    if isinstance(model, dict):
        comparable["model"] = {
            "attested": model.get("attested"),
            "source": str(model.get("source", "")).strip(),
            "provider": str(model.get("provider", "")).strip(),
            "model": str(model.get("model", "")).strip(),
            "observed_deliberation": str(model.get("observed_deliberation", "")).strip(),
        }
    return comparable


def _runtime_observation_path_metadata(
    path: Path,
    *,
    regular_file: bool,
) -> os.stat_result:
    metadata = os.lstat(path)
    is_reparse = bool(int(getattr(metadata, "st_file_attributes", 0)) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or is_reparse:
        raise OSError("persisted runtime observation evidence must not use a symlink or reparse alias")
    if regular_file:
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("persisted runtime observation evidence must be a no-follow regular file")
        if int(getattr(metadata, "st_nlink", 1)) != 1:
            raise OSError("persisted runtime observation evidence must not use a hard-link alias")
    elif not stat.S_ISDIR(metadata.st_mode):
        raise OSError("persisted runtime observation evidence parent must be a directory")
    return metadata


def _runtime_observation_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _runtime_observation_snapshot(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _read_persisted_runtime_observation(
    path: Path,
    *,
    validation_dir: Path,
) -> bytes:
    relative = path.relative_to(validation_dir)
    parents = [validation_dir]
    current = validation_dir
    for part in relative.parts[:-1]:
        current /= part
        parents.append(current)
    parent_identities = {
        parent: _runtime_observation_identity(
            _runtime_observation_path_metadata(parent, regular_file=False)
        )
        for parent in parents
    }
    metadata = _runtime_observation_path_metadata(path, regular_file=True)
    if metadata.st_size > RUNTIME_OBSERVATION_MAX_BYTES:
        raise OSError(
            f"persisted runtime observation evidence exceeds {RUNTIME_OBSERVATION_MAX_BYTES} bytes"
        )
    expected_identity = _runtime_observation_identity(metadata)
    expected_snapshot = _runtime_observation_snapshot(metadata)

    resolved = path.resolve(strict=True)
    if os.path.normcase(str(path)) != os.path.normcase(str(resolved)):
        raise OSError("persisted runtime observation evidence must not use a symlink or reparse alias")
    for parent, identity in parent_identities.items():
        current_metadata = _runtime_observation_path_metadata(parent, regular_file=False)
        if _runtime_observation_identity(current_metadata) != identity:
            raise OSError("persisted runtime observation evidence parent identity changed before opening")
    if _runtime_observation_identity(
        _runtime_observation_path_metadata(path, regular_file=True)
    ) != expected_identity:
        raise OSError("persisted runtime observation evidence file identity changed before opening")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode) or _runtime_observation_identity(opened) != expected_identity:
            raise OSError("persisted runtime observation evidence file identity changed while opening")
        opened_snapshot = _runtime_observation_snapshot(opened)
        if opened_snapshot != expected_snapshot:
            raise OSError("persisted runtime observation evidence changed while opening")
        data = handle.read(RUNTIME_OBSERVATION_MAX_BYTES + 1)
        if len(data) > RUNTIME_OBSERVATION_MAX_BYTES:
            raise OSError(
                f"persisted runtime observation evidence exceeds {RUNTIME_OBSERVATION_MAX_BYTES} bytes"
            )
        if _runtime_observation_snapshot(os.fstat(handle.fileno())) != opened_snapshot:
            raise OSError("persisted runtime observation evidence changed while reading")

    if _runtime_observation_snapshot(
        _runtime_observation_path_metadata(path, regular_file=True)
    ) != expected_snapshot:
        raise OSError("persisted runtime observation evidence changed after reading")
    for parent, identity in parent_identities.items():
        current_metadata = _runtime_observation_path_metadata(parent, regular_file=False)
        if _runtime_observation_identity(current_metadata) != identity:
            raise OSError("persisted runtime observation evidence parent identity changed while reading")
    return data


def verify_persisted_runtime_observation(
    root: Path,
    workflow: str,
    run_id: str,
    phase: str,
    value: object,
) -> tuple[object, list[str]]:
    """Fail closed when durable runtime evidence no longer matches run.json."""

    if not isinstance(value, dict) or not value:
        return value, []
    evidence_path = str(value.get("evidence_path", "")).strip()
    if not evidence_path:
        return value, ["persisted runtime observation has no evidence_path"]
    candidate = Path(evidence_path)
    if any(part in {".", ".."} for part in candidate.parts):
        return value, ["persisted runtime observation evidence path must not contain lexical aliases"]
    if not candidate.is_absolute():
        candidate = root / candidate
    path = Path(os.path.abspath(candidate))
    validation_dir = Path(
        os.path.abspath(root / "automations" / workflow / "runs" / run_id / "validation")
    )
    try:
        path.relative_to(validation_dir)
    except ValueError:
        return value, ["persisted runtime observation evidence is outside the selected run validation directory"]
    try:
        raw = _read_persisted_runtime_observation(path, validation_dir=validation_dir)
        loaded = json.loads(raw.decode("utf-8-sig"))
    except FileNotFoundError:
        return value, ["persisted runtime observation evidence file is missing"]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return value, [f"persisted runtime observation evidence is unreadable: {exc}"]
    if not isinstance(loaded, dict):
        return value, ["persisted runtime observation evidence must be a JSON object"]
    canonical_path = common.relative(root, path)
    loaded["evidence_path"] = canonical_path
    loaded_issues = runtime_observation_issues(
        loaded,
        expected_workflow=workflow,
        expected_run_id=run_id,
        expected_phase=phase,
    )
    if loaded_issues:
        return value, [f"persisted runtime observation evidence: {item}" for item in loaded_issues]
    stored_issues = runtime_observation_issues(
        value,
        expected_workflow=workflow,
        expected_run_id=run_id,
        expected_phase=phase,
    )
    if stored_issues:
        return value, [f"persisted runtime observation record: {item}" for item in stored_issues]
    if _runtime_observation_comparable(loaded, canonical_path) != _runtime_observation_comparable(value, canonical_path):
        return value, ["persisted runtime observation evidence does not match the run record"]
    return loaded, []


def normalized_runtime_observation(
    value: object,
    *,
    expected_workflow: str = "",
    expected_run_id: str = "",
    expected_phase: str = "",
    verification_issues: list[str] | None = None,
) -> dict[str, Any]:
    observation = value if isinstance(value, dict) else {}
    supplied = bool(observation)
    packet_issues, host_issues, model_issues = (
        _runtime_observation_issue_groups(
            observation,
            expected_workflow=expected_workflow,
            expected_run_id=expected_run_id,
            expected_phase=expected_phase,
        )
        if supplied
        else ([], [], [])
    )
    verification = [str(item) for item in (verification_issues or []) if str(item).strip()]
    packet_issues.extend(verification)
    host = observation.get("host") if isinstance(observation.get("host"), dict) else {}
    model_observation = observation.get("model") if isinstance(observation.get("model"), dict) else {}
    raw_capabilities = host.get("capabilities", [])
    capabilities = sorted(
        {
            str(item).strip()
            for item in raw_capabilities
            if isinstance(item, str) and str(item).strip() in CAPABILITY_IDS
        }
    ) if isinstance(raw_capabilities, list) else []
    host_trusted = supplied and bool(host) and not packet_issues and not host_issues
    model_trusted = supplied and bool(model_observation) and not packet_issues and not model_issues
    return {
        "host_trusted": host_trusted,
        "model_trusted": model_trusted,
        "host_surface": str(host.get("surface", "")).strip(),
        "model_provider": str(model_observation.get("provider", "")).strip(),
        "model": str(model_observation.get("model", "")).strip(),
        "observed_deliberation": str(model_observation.get("observed_deliberation", "")).strip(),
        "host_observation_source": str(host.get("source", "")).strip(),
        "model_observation_source": str(model_observation.get("source", "")).strip(),
        "capabilities": capabilities,
        "observation_evidence_path": str(observation.get("evidence_path", "")).strip(),
        "validation_issues": [*packet_issues, *host_issues, *model_issues],
        "host_validation_issues": host_issues,
        "model_validation_issues": model_issues,
    }


def delegation_host_capability_gate(
    runtime_observation: object,
    *,
    expected_workflow: str = "",
    expected_run_id: str = "",
    expected_phase: str = "",
    verification_issues: list[str] | None = None,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Require a trusted current-host observation and native-worker prerequisites."""

    observation = normalized_runtime_observation(
        runtime_observation,
        expected_workflow=expected_workflow,
        expected_run_id=expected_run_id,
        expected_phase=expected_phase,
        verification_issues=verification_issues,
    )
    if not observation["host_trusted"]:
        issues = [str(item) for item in observation["validation_issues"] if str(item).strip()]
        reason = (
            "current host observation is not trusted: " + "; ".join(issues[:2])
            if issues
            else "trusted current host observation is required for native delegation"
        )
        return {
            "eligible": False,
            "reason": reason,
            "host_surface": "",
            "available_orchestration_mode": "direct-tools",
            "required_capabilities": ["native-subagents"],
            "missing_capabilities": ["native-subagents"],
        }

    selected_catalog = catalog if isinstance(catalog, dict) else load_worker_profile_config()
    adapters = selected_catalog.get("surface_adapters")
    adapters = adapters if isinstance(adapters, dict) else {}
    host_surface = str(observation["host_surface"])
    selected: dict[str, Any] = {}
    for adapter in adapters.values():
        if not isinstance(adapter, dict):
            continue
        surfaces = adapter.get("host_surfaces")
        if isinstance(surfaces, list) and host_surface in surfaces:
            selected = adapter
            break
    modes = selected.get("capability_modes") if isinstance(selected, dict) else {}
    modes = modes if isinstance(modes, dict) else {}
    requirements = selected.get("capability_requirements") if isinstance(selected, dict) else {}
    requirements = requirements if isinstance(requirements, dict) else {}
    prerequisites = requirements.get("native-subagents", [])
    prerequisites = [str(item) for item in prerequisites if isinstance(item, str)] if isinstance(prerequisites, list) else []
    required = sorted({"native-subagents", *prerequisites})
    observed = set(str(item) for item in observation["capabilities"])
    missing = sorted(set(required) - observed)
    supports_mode = modes.get("native-subagents") == "native-subagents"
    if not supports_mode:
        return {
            "eligible": False,
            "reason": f"host surface {host_surface!r} has no native-subagents adapter mode",
            "host_surface": host_surface,
            "available_orchestration_mode": "direct-tools",
            "required_capabilities": required,
            "missing_capabilities": required,
        }
    if missing:
        return {
            "eligible": False,
            "reason": "trusted host observation is missing native delegation capabilities: " + ", ".join(missing),
            "host_surface": host_surface,
            "available_orchestration_mode": "direct-tools",
            "required_capabilities": required,
            "missing_capabilities": missing,
        }
    return {
        "eligible": True,
        "reason": "",
        "host_surface": host_surface,
        "available_orchestration_mode": "native-subagents",
        "required_capabilities": required,
        "missing_capabilities": [],
    }


def _model_registry_match(
    catalog: dict[str, Any],
    model_provider: str,
    observed_model: str,
) -> tuple[dict[str, Any], str]:
    compatibility = catalog.get("model_compatibility")
    if not isinstance(compatibility, dict):
        return {}, ""
    models = compatibility.get("models")
    if isinstance(models, list):
        for row in models:
            if not isinstance(row, dict):
                continue
            if (
                str(row.get("model_provider", "")).strip() == model_provider
                and str(row.get("model", "")).strip() == observed_model
            ):
                return row, observed_model
    canonical_model = observed_model
    aliases = compatibility.get("aliases")
    if isinstance(aliases, list):
        for alias in aliases:
            if not isinstance(alias, dict):
                continue
            if (
                str(alias.get("model_provider", "")).strip() == model_provider
                and str(alias.get("alias", "")).strip() == observed_model
            ):
                canonical_model = str(alias.get("canonical_model", "")).strip()
                break
    if isinstance(models, list):
        for row in models:
            if not isinstance(row, dict):
                continue
            if (
                str(row.get("model_provider", "")).strip() == model_provider
                and str(row.get("model", "")).strip() == canonical_model
            ):
                return row, canonical_model
    return {}, canonical_model


def resolve_surface_adapter(
    catalog: dict[str, Any],
    host_surface: str,
    capabilities: list[str],
    *,
    trusted: bool,
    delegation_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    adapters = catalog.get("surface_adapters")
    adapters = adapters if isinstance(adapters, dict) else {}
    selected_id = "generic-v1"
    selected = adapters.get(selected_id, {})
    if trusted:
        for adapter_id, adapter in adapters.items():
            if not isinstance(adapter, dict):
                continue
            surfaces = adapter.get("host_surfaces")
            if isinstance(surfaces, list) and host_surface in surfaces:
                selected_id = str(adapter_id)
                selected = adapter
                break
    selected = selected if isinstance(selected, dict) else {}
    modes = selected.get("capability_modes")
    modes = modes if isinstance(modes, dict) else {}
    requirements = selected.get("capability_requirements")
    requirements = requirements if isinstance(requirements, dict) else {}
    observed = set(capabilities)
    enabled = {
        capability: str(modes[capability])
        for capability in sorted(set(capabilities))
        if capability in modes and isinstance(modes[capability], str)
        and set(requirements.get(capability, [])) <= observed
    }
    base_mode = str(selected.get("base_orchestration_mode", "direct-tools")).strip() or "direct-tools"
    available_orchestration_mode = enabled.get(
        "hosted-program-orchestration",
        enabled.get("native-subagents", base_mode),
    )
    orchestration_mode = available_orchestration_mode
    blocked_optimizations: list[dict[str, str]] = []
    if available_orchestration_mode == "native-subagents":
        decision = delegation_decision if isinstance(delegation_decision, dict) else {}
        authorized = (
            decision.get("effective_orchestration_mode") == "native-subagents"
            and isinstance(decision.get("effective_worker_count"), int)
            and not isinstance(decision.get("effective_worker_count"), bool)
            and decision["effective_worker_count"] > 1
        )
        if not authorized:
            orchestration_mode = base_mode
            reasons = decision.get("serial_fallback_reasons")
            reason = (
                "; ".join(str(item) for item in reasons if str(item).strip())
                if isinstance(reasons, list)
                else ""
            )
            blocked_optimizations.append(
                {
                    "id": "native-subagents",
                    "reason": reason
                    or "Host availability does not grant delegation authority; workflow parallel-safety, economics, task-class, and request gates remain required.",
                }
            )
    continuation_mode = enabled.get(
        "reasoning-continuation",
        enabled.get("session-resume", "durable-workflow-checkpoint"),
    )
    cache_mode = enabled.get("prompt-cache-control", "unavailable")
    return {
        "id": selected_id,
        "host_surface": host_surface if trusted else "unknown",
        "instruction_surfaces": list(selected.get("instruction_surfaces", []))
        if isinstance(selected.get("instruction_surfaces"), list)
        else [],
        "delivery_directive": str(selected.get("delivery_directive", "")).strip(),
        "available_orchestration_mode": available_orchestration_mode,
        "orchestration_mode": orchestration_mode,
        "effective_orchestration_mode": orchestration_mode,
        "continuation_mode": continuation_mode,
        "cache_mode": cache_mode,
        "enabled_optimizations": enabled,
        "blocked_optimizations": blocked_optimizations,
    }


def resolve_model_delivery(
    surface_routes: dict[str, list[dict[str, str]]],
    runtime_observation: object = None,
    *,
    catalog: dict[str, Any] | None = None,
    expected_workflow: str = "",
    expected_run_id: str = "",
    expected_phase: str = "",
    runtime_observation_verification_issues: list[str] | None = None,
    delegation_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected_catalog = catalog if isinstance(catalog, dict) else load_worker_profile_config()
    overlays = effective_model_prompt_overlays(selected_catalog)
    generic_overlay = dict(overlays.get("generic-v1", synthetic_generic_overlay()))
    observation = normalized_runtime_observation(
        runtime_observation,
        expected_workflow=expected_workflow,
        expected_run_id=expected_run_id,
        expected_phase=expected_phase,
        verification_issues=runtime_observation_verification_issues,
    )
    observed_surface = str(observation["host_surface"])
    observed_model_provider = str(observation["model_provider"])
    observed_model = str(observation["model"])
    observed_deliberation = str(observation["observed_deliberation"])
    capabilities = list(observation["capabilities"])
    host_trusted = bool(observation["host_trusted"])
    model_trusted = bool(observation["model_trusted"])
    observation_issues = list(observation["validation_issues"])

    declared_routes = surface_routes.get(observed_surface, []) if host_trusted else []
    primary_summary = declared_routes[0] if declared_routes else {}
    route_match_index = next(
        (
            index
            for index, endpoint in enumerate(declared_routes)
            if observed_model_provider == endpoint.get("model_provider", "")
            and observed_model == endpoint.get("model", "")
        ),
        None,
    ) if model_trusted else None
    route_match = declared_routes[route_match_index] if route_match_index is not None else None

    registry_row, canonical_model = _model_registry_match(
        selected_catalog,
        observed_model_provider,
        observed_model,
    ) if model_trusted else ({}, "")
    overlay_id = str(registry_row.get("overlay_id", "")).strip() if registry_row else ""
    prompt_overlay = dict(overlays.get(overlay_id, generic_overlay))
    prompt_overlay.setdefault("id", overlay_id or "generic-v1")

    if host_trusted and model_trusted and route_match_index == 0:
        endpoint_status = "attested-primary"
    elif host_trusted and model_trusted and route_match_index is not None:
        endpoint_status = "attested-alternate"
    elif host_trusted and model_trusted:
        endpoint_status = "active-model-fallback"
    elif host_trusted:
        endpoint_status = "attested-host-only"
    elif model_trusted:
        endpoint_status = "attested-model-only"
    else:
        endpoint_status = "unattested-active"
    expected_tier = str(route_match.get("deliberation_tier", "")) if route_match else ""
    deliberation_mismatch = bool(
        observed_deliberation
        and expected_tier
        and observed_deliberation != expected_tier
    )
    if host_trusted and capabilities:
        capability_status = "attested"
    elif host_trusted:
        capability_status = "partial"
    else:
        capability_status = "unavailable"

    reasons: list[str] = []
    if observation_issues:
        reasons.append(
            "runtime observation failed contract validation: "
            + "; ".join(str(item) for item in observation_issues[:2])
        )
    elif not host_trusted and not model_trusted:
        reasons.append("trusted runtime observation is unavailable; using the generic overlay and serial active-model fallback")
    elif host_trusted and not model_trusted:
        reasons.append("host surface is attested but model identity is unavailable; using the host adapter with the generic model overlay")
    elif model_trusted and not host_trusted:
        reasons.append("model identity is attested but host surface is unavailable; using the model overlay with the generic surface adapter")
    elif endpoint_status == "attested-alternate":
        reasons.append("the first surface route was not observed; using an attested alternate route")
    elif endpoint_status == "active-model-fallback":
        reasons.append("the observed host/model pair is outside the profile's surface routes; using serial active-model fallback")
    if host_trusted and capability_status == "partial":
        reasons.append("host identity is attested but capability evidence is incomplete")
    if deliberation_mismatch:
        reasons.append(
            f"observed deliberation '{observed_deliberation}' differs from declared route tier '{expected_tier}'"
        )
    if model_trusted and canonical_model and canonical_model != observed_model:
        reasons.append(
            f"alias '{observed_model}' maps to '{canonical_model}' for overlay selection only; exact observation is preserved"
        )
    if model_trusted and not registry_row:
        reasons.append("no exact compatibility mapping exists; using the generic overlay")

    declared_endpoint_attested = endpoint_status in {"attested-primary", "attested-alternate"}
    effective_execution_mode = (
        "declared-endpoint"
        if declared_endpoint_attested and not deliberation_mismatch
        else "serial-active-model"
    )
    surface_adapter = resolve_surface_adapter(
        selected_catalog,
        observed_surface,
        capabilities,
        trusted=host_trusted,
        delegation_decision=delegation_decision,
    )
    return {
        "endpoint_status": endpoint_status,
        "capability_status": capability_status,
        "effective_execution_mode": effective_execution_mode,
        "declared_host_surface": observed_surface if declared_routes else "",
        "declared_model_provider": str(primary_summary.get("model_provider", "")),
        "declared_model": str(primary_summary.get("model", "")),
        "declared_deliberation_tier": str(primary_summary.get("deliberation_tier", "")),
        "observed_host_surface": observed_surface if host_trusted else "",
        "observed_model_provider": observed_model_provider if model_trusted else "",
        "observed_model": observed_model if model_trusted else "",
        "observed_deliberation": observed_deliberation if model_trusted else "",
        "observed_capabilities": capabilities if host_trusted else [],
        "host_observation_source": str(observation["host_observation_source"]) if host_trusted else "",
        "model_observation_source": str(observation["model_observation_source"]) if model_trusted else "",
        "observation_evidence_path": str(observation["observation_evidence_path"])
        if host_trusted or model_trusted
        else "",
        "fallback_reason": "; ".join(reasons),
        "prompt_overlay": prompt_overlay,
        "surface_adapter": surface_adapter,
    }


def workflow_execution_profile(
    manifest: dict[str, Any],
    phase: str,
    *,
    cost_policy: dict[str, Any] | None = None,
    effective_context_tokens: int | None = None,
    runtime_observation: dict[str, Any] | None = None,
    runtime_observation_verification_issues: list[str] | None = None,
    workflow: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    config = configured_worker_profiles(manifest)
    if not config:
        return {"status": "not-declared", "phase": phase}
    profiles = merged_profiles(config)
    phase_assignments = normalized_phase_assignments(manifest, config)
    phase_text = str(phase or "").strip()
    profile_id = str(phase_assignments.get(phase_text, "")).strip()
    if not profile_id or profile_id not in profiles:
        return {
            "status": "missing-profile",
            "phase": phase_text,
            "profile_id": profile_id,
        }
    profile = profile_summary(
        profile_id,
        profiles,
        phase=phase_text,
        cost_policy=cost_policy,
        effective_context_tokens=effective_context_tokens,
    )
    execution = profile.get("execution") if isinstance(profile.get("execution"), dict) else {}
    surface_routes = profile.get("surface_routes") if isinstance(profile.get("surface_routes"), dict) else {}
    delivery = resolve_model_delivery(
        {
            str(surface): [item for item in endpoints if isinstance(item, dict)]
            for surface, endpoints in surface_routes.items()
            if isinstance(endpoints, list)
        },
        runtime_observation,
        catalog=load_worker_profile_config(),
        expected_workflow=workflow,
        expected_run_id=run_id,
        expected_phase=phase_text,
        runtime_observation_verification_issues=runtime_observation_verification_issues,
    )
    return {
        "profile_id": profile_id,
        "route_set": profile.get("route_set", ""),
        "model_target": " ".join(
            value
            for value in (
                str(delivery.get("declared_model_provider", "")),
                str(delivery.get("declared_model", "")),
            )
            if value
        ),
        "deliberation_tier": delivery.get("declared_deliberation_tier", ""),
        "profile_purpose": profile.get("purpose", ""),
        "instruction_header": execution.get("instruction_header", []),
        **delivery,
        "prompt_adapter": execution.get("prompt_adapter", ""),
        "context_budget": execution.get("context_budget", ""),
        "tool_policy": execution.get("tool_policy", ""),
        "expected_output": execution.get("expected_output", ""),
        "validation_gate": execution.get("validation_gate", ""),
        "context_budget_ref": execution.get("context_budget_ref", ""),
        "budget_tokens": execution.get("budget_tokens", 0),
        "budget_source": execution.get("budget_source", "default-missing"),
        "budget_issue": execution.get("budget_issue", ""),
        "effective_context_tokens": execution.get("effective_context_tokens"),
        "remaining_margin_tokens": execution.get("remaining_margin_tokens"),
        "within_budget": execution.get("within_budget"),
        "context_measurement": execution.get("context_measurement", "not-measured"),
    }


def workflow_workers_report(
    root: Path,
    *,
    workflow_names: list[str] | None = None,
    phase: str | None = None,
    summary: bool = False,
    compact: bool = False,
    delegation_requested: bool = False,
    task_class: str = "independent-read-heavy",
    runtime_observation: object = None,
    runtime_observation_verification_issues: list[str] | None = None,
    observation_run_id: str = "",
) -> dict[str, Any]:
    names = workflow_names or accepted_workflow_names(root)
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    try:
        cost_policy = load_cost_policy(root)
    except ValueError as exc:
        issue = str(exc)
        return {
            "schema_version": 1,
            "tool": "workflow-manager.workers",
            "ok": False,
            "status": "failed",
            "summary": {
                "workflow_count": 0,
                "issue_count": 1,
                "phase_assignment_count": 0,
                "task_assignment_count": 0,
            },
            "issues": [issue],
            "workflows": [],
            "host_guidance": host_guidance(),
        }
    catalog = load_worker_profile_config()
    experimental_models = {
        (str(row.get("model_provider", "")).strip(), str(row.get("model", "")).strip())
        for row in (
            catalog.get("model_compatibility", {}).get("models", [])
            if isinstance(catalog.get("model_compatibility"), dict)
            and isinstance(catalog.get("model_compatibility", {}).get("models"), list)
            else []
        )
        if isinstance(row, dict) and row.get("promotion_state") == "experimental"
    }
    route_sets = catalog.get("surface_route_sets") if isinstance(catalog.get("surface_route_sets"), dict) else {}
    experimental_route_sets = {
        str(route_set_id)
        for route_set_id, surface_map in route_sets.items()
        if isinstance(surface_map, dict)
        and any(
            (
                str(endpoint.get("model_provider", "")).strip(),
                str(endpoint.get("model", "")).strip(),
            ) in experimental_models
            for endpoints in surface_map.values()
            if isinstance(endpoints, list)
            for endpoint in endpoints
            if isinstance(endpoint, dict)
        )
    }
    experimental_profiles = {
        str(profile_id)
        for profiles in (catalog.get("profile_sets", {}) or {}).values()
        if isinstance(profiles, dict)
        for profile_id, profile in profiles.items()
        if isinstance(profile, dict)
        and str(profile.get("route_set", "")).strip() in experimental_route_sets
    } if isinstance(catalog.get("profile_sets"), dict) else set()
    for workflow_name in names:
        module_dir = root / "automations" / workflow_name
        manifest, error = read_workflow_manifest(module_dir)
        if error or not isinstance(manifest, dict):
            issues.append(f"automations/{workflow_name}/module.json could not be loaded: {error or 'missing'}")
            continue
        validation_issues = validate_worker_profiles(manifest)
        compatibility_warnings = catalog_warnings(catalog)
        config = configured_worker_profiles(manifest)
        assignments = {
            **normalized_phase_assignments(manifest, config),
            **normalized_task_assignments(manifest, config),
        }
        for assignment_id, profile_id in assignments.items():
            if profile_id in experimental_profiles:
                validation_issues.append(
                    f"workflow '{workflow_name}' assignment '{assignment_id}' uses experimental profile "
                    f"'{profile_id}'; provider-backed promotion evidence is required before default assignment."
                )
        worker_data = worker_summary(
            manifest,
            root=root,
            cost_policy=cost_policy,
            delegation_requested=delegation_requested,
            task_class=task_class,
            runtime_observation=runtime_observation,
            runtime_observation_verification_issues=runtime_observation_verification_issues,
            workflow=workflow_name,
            run_id=observation_run_id,
        )
        phase_rows = worker_data.get("phase_assignments", [])
        if phase:
            phase_rows = [row for row in phase_rows if isinstance(row, dict) and row.get("phase") == phase]
            if not phase_rows:
                validation_issues.append(f"workflow '{workflow_name}' does not declare phase '{phase}'.")
            else:
                phase_declared = max(
                    int(row.get("parallel_safety", {}).get("declared_worker_count", 1) or 1)
                    for row in phase_rows
                    if isinstance(row.get("parallel_safety"), dict)
                )
                worker_data.update(
                    parallel_decision_summary(
                        phase_rows,
                        declared_worker_count=phase_declared,
                    )
                )
        rows.append(
            {
                "workflow": workflow_name,
                "ok": not validation_issues,
                "issues": validation_issues,
                "warnings": compatibility_warnings,
                "worker_profiles": {
                    **worker_data,
                    "phase_assignments": phase_rows,
                    "profiles": [] if compact else worker_data.get("profiles", []),
                },
            }
        )
    issue_count = len(issues) + sum(len(row.get("issues", [])) for row in rows if isinstance(row, dict))
    report: dict[str, Any] = {
        "schema_version": 1,
        "tool": "workflow-manager.workers",
        "ok": issue_count == 0,
        "status": "passed" if issue_count == 0 else "failed",
        "summary": {
            "workflow_count": len(rows),
            "issue_count": issue_count,
            "phase_assignment_count": sum(
                len(row.get("worker_profiles", {}).get("phase_assignments", []))
                for row in rows
                if isinstance(row.get("worker_profiles"), dict)
            ),
            "task_assignment_count": sum(
                len(row.get("worker_profiles", {}).get("task_assignments", []))
                for row in rows
                if isinstance(row.get("worker_profiles"), dict)
            ),
        },
        "issues": issues,
        "workflows": rows,
        "host_guidance": host_guidance(),
    }
    if summary or compact:
        report["workflows"] = [
            {
                "workflow": row.get("workflow"),
                "ok": row.get("ok"),
                "issues": row.get("issues", []),
                "warnings": row.get("warnings", []),
                "phase_count": row.get("worker_profiles", {}).get("phase_count", 0)
                if isinstance(row.get("worker_profiles"), dict)
                else 0,
                "max_parallel_workers": row.get("worker_profiles", {}).get("max_parallel_workers", 1)
                if isinstance(row.get("worker_profiles"), dict)
                else 1,
                "declared_worker_count": row.get("worker_profiles", {}).get("declared_worker_count", 1)
                if isinstance(row.get("worker_profiles"), dict)
                else 1,
                "effective_worker_count": row.get("worker_profiles", {}).get("effective_worker_count", 1)
                if isinstance(row.get("worker_profiles"), dict)
                else 1,
                "eligible": row.get("worker_profiles", {}).get("eligible", False)
                if isinstance(row.get("worker_profiles"), dict)
                else False,
                "isolation_mode": row.get("worker_profiles", {}).get("isolation_mode", "serial")
                if isinstance(row.get("worker_profiles"), dict)
                else "serial",
                "serial_fallback_reason": row.get("worker_profiles", {}).get("serial_fallback_reason", "")
                if isinstance(row.get("worker_profiles"), dict)
                else "",
                "serial_fallback_reasons": row.get("worker_profiles", {}).get("serial_fallback_reasons", [])
                if isinstance(row.get("worker_profiles"), dict)
                else [],
                "available_orchestration_mode": row.get("worker_profiles", {}).get(
                    "available_orchestration_mode", "direct-tools"
                )
                if isinstance(row.get("worker_profiles"), dict)
                else "direct-tools",
                "effective_orchestration_mode": row.get("worker_profiles", {}).get(
                    "effective_orchestration_mode", "direct-tools"
                )
                if isinstance(row.get("worker_profiles"), dict)
                else "direct-tools",
                "delegation_requested": row.get("worker_profiles", {}).get("delegation_requested", False)
                if isinstance(row.get("worker_profiles"), dict)
                else False,
                "task_class": row.get("worker_profiles", {}).get("task_class", "independent-read-heavy")
                if isinstance(row.get("worker_profiles"), dict)
                else "independent-read-heavy",
            }
            for row in rows
            if (
                not compact
                or row.get("ok") is not True
                or (
                    isinstance(row.get("worker_profiles"), dict)
                    and int(row["worker_profiles"].get("declared_worker_count", 1) or 1) > 1
                )
            )
        ]
        if compact and not report["issues"]:
            report.pop("issues", None)
    return report


def accepted_workflow_names(root: Path) -> list[str]:
    automations = root / "automations"
    if not automations.exists():
        return []
    return [
        item.name
        for item in sorted(automations.iterdir(), key=lambda child: child.name)
        if item.is_dir() and (item / "WORKFLOW.md").exists()
    ]


def render_workers_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Workflow Workers",
        "",
        f"- Status: {report.get('status')}",
        f"- Workflows: {summary.get('workflow_count', 0)}",
        f"- Phase assignments: {summary.get('phase_assignment_count', 0)}",
        f"- Task assignments: {summary.get('task_assignment_count', 0)}",
    ]
    issues = report.get("issues", []) if isinstance(report.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in issues)
    warnings = report.get("warnings", []) if isinstance(report.get("warnings"), list) else []
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    workflows = report.get("workflows", []) if isinstance(report.get("workflows"), list) else []
    for workflow in workflows:
        if not isinstance(workflow, dict):
            continue
        lines.extend(["", f"## {workflow.get('workflow')}", ""])
        row_issues = workflow.get("issues", []) if isinstance(workflow.get("issues"), list) else []
        if row_issues:
            lines.extend(f"- Issue: {issue}" for issue in row_issues)
        row_warnings = workflow.get("warnings", []) if isinstance(workflow.get("warnings"), list) else []
        if row_warnings:
            lines.extend(f"- Warning: {warning}" for warning in row_warnings)
        workers = workflow.get("worker_profiles") if isinstance(workflow.get("worker_profiles"), dict) else {}
        if workers:
            lines.append(f"- Mode: {workers.get('mode')}")
            lines.append(
                f"- Workers: declared {workers.get('declared_worker_count', 1)}; "
                f"effective {workers.get('effective_worker_count', 1)}; "
                f"eligible {str(bool(workers.get('eligible'))).lower()}"
            )
            lines.append(f"- Isolation: {workers.get('isolation_mode', 'serial')}")
            lines.append(
                f"- Orchestration: available {workers.get('available_orchestration_mode', 'direct-tools')}; "
                f"effective {workers.get('effective_orchestration_mode', 'direct-tools')}"
            )
            if workers.get("serial_fallback_reason"):
                lines.append(f"- Serial fallback: {workers.get('serial_fallback_reason')}")
            extra_blockers = workers.get("serial_fallback_reasons")
            if isinstance(extra_blockers, list) and len(extra_blockers) > 1:
                lines.extend(f"- Additional blocker: {reason}" for reason in extra_blockers[1:])
            phase_rows = workers.get("phase_assignments", []) if isinstance(workers.get("phase_assignments"), list) else []
            if phase_rows:
                lines.extend(
                    [
                        "",
                        "| Phase | Surface routes | Workers | Isolation | Budget | Effective context | Remaining margin |",
                        "|---|---|---:|---|---:|---:|---:|",
                    ]
                )
                for row in phase_rows:
                    if not isinstance(row, dict):
                        continue
                    profile = row.get("profile") if isinstance(row.get("profile"), dict) else {}
                    routes = profile.get("surface_routes") if isinstance(profile.get("surface_routes"), dict) else {}
                    route_text = "<br>".join(
                        f"`{surface}`: {endpoint_label(endpoints[0])}"
                        + (f" (+{len(endpoints) - 1})" if len(endpoints) > 1 else "")
                        for surface, endpoints in sorted(routes.items())
                        if isinstance(endpoints, list) and endpoints and isinstance(endpoints[0], dict)
                    )
                    execution = profile.get("execution") if isinstance(profile.get("execution"), dict) else {}
                    parallel = row.get("parallel_safety") if isinstance(row.get("parallel_safety"), dict) else {}
                    effective = execution.get("effective_context_tokens")
                    margin = execution.get("remaining_margin_tokens")
                    lines.append(
                        f"| `{row.get('phase')}` | {route_text or 'active model fallback'} | "
                        f"{parallel.get('declared_worker_count', 1)} → {parallel.get('effective_worker_count', 1)} | "
                        f"{parallel.get('isolation_mode', 'serial')} | {execution.get('budget_tokens', 0)} "
                        f"(`{execution.get('context_budget_ref', '')}`) | "
                        f"{effective if effective is not None else 'not measured'} | "
                        f"{margin if margin is not None else 'not measured'} |"
                    )
            task_rows = workers.get("task_assignments", []) if isinstance(workers.get("task_assignments"), list) else []
            if task_rows:
                lines.extend(["", "| Task | Profile | Route set |", "|---|---|---|"])
                for row in task_rows:
                    if not isinstance(row, dict):
                        continue
                    profile = row.get("profile") if isinstance(row.get("profile"), dict) else {}
                    lines.append(
                        f"| `{row.get('task')}` | `{profile.get('id', '')}` | `{profile.get('route_set', '')}` |"
                    )
    profile_sets = report.get("profile_sets", []) if isinstance(report.get("profile_sets"), list) else []
    if profile_sets:
        lines.extend(["", "## Profile Sets", ""])
        for profile_set in profile_sets:
            if not isinstance(profile_set, dict):
                continue
            lines.extend(["", f"### {profile_set.get('id')}", ""])
            profiles = profile_set.get("profiles", []) if isinstance(profile_set.get("profiles"), list) else []
            if profiles:
                lines.extend(["| Profile | Route set | Purpose |", "|---|---|---|"])
                for profile in profiles:
                    if not isinstance(profile, dict):
                        continue
                    lines.append(
                        f"| `{profile.get('id')}` | `{profile.get('route_set', '')}` | {profile.get('purpose', '')} |"
                    )
    compatibility = report.get("model_compatibility") if isinstance(report.get("model_compatibility"), dict) else {}
    overlays = report.get("model_prompt_overlays") if isinstance(report.get("model_prompt_overlays"), dict) else {}
    if compatibility:
        lines.extend(["", "## Model Compatibility", ""])
        lines.append(f"- Selection mode: {compatibility.get('selection_mode', '')}")
        lines.append(f"- Unknown model policy: {compatibility.get('unknown_model_policy', '')}")
        lines.append(
            "- Portable deliberation tiers: "
            + ", ".join(str(item) for item in compatibility.get("portable_deliberation_tiers", []))
        )
        lines.append(f"- Verified: {compatibility.get('verified_at', '')}")
        lines.append("- Declared endpoints and aliases are guidance, not runtime attestation.")
        models = compatibility.get("models") if isinstance(compatibility.get("models"), list) else []
        if models:
            lines.extend(
                [
                    "",
                    "| Provider | Model | Prompt overlay | Promotion state |",
                    "|---|---|---|---|",
                ]
            )
            for row in models:
                if isinstance(row, dict):
                    lines.append(
                        f"| `{row.get('model_provider', '')}` | `{row.get('model', '')}` | "
                        f"`{row.get('overlay_id', '')}` | {row.get('promotion_state', '')} |"
                    )
    if overlays:
        lines.extend(["", "### Prompt Delivery Overlays", ""])
        for overlay_id, overlay in sorted(overlays.items()):
            if not isinstance(overlay, dict):
                continue
            lines.append(
                f"- `{overlay_id}` ({overlay.get('promotion_state', '')}): "
                + " ".join(str(item) for item in overlay.get("instructions", []))
            )
    mode_rows = report.get("execution_modes") if isinstance(report.get("execution_modes"), dict) else {}
    if mode_rows:
        lines.extend(
            [
                "",
                "## Execution Modes",
                "",
                "| Mode | Authority | Lifecycle owner | Write policy | Fallback |",
                "|---|---|---|---|---|",
            ]
        )
        for mode_id, row in sorted(mode_rows.items()):
            if not isinstance(row, dict):
                continue
            lines.append(
                f"| `{mode_id}` | {row.get('authority', '')} | {row.get('lifecycle_owner', '')} | "
                f"{row.get('write_policy', '')} | {row.get('fallback', '')} |"
            )
    routing = report.get("risk_routing") if isinstance(report.get("risk_routing"), dict) else {}
    if routing:
        lines.extend(["", "## Risk Routing", ""])
        lines.append(f"- Status: {routing.get('status', '')}")
        lines.append(f"- Selection: {routing.get('selection_mode', '')}")
        lines.append(f"- Official model source: {routing.get('official_model_source', '')}")
        lines.append(f"- Verified: {routing.get('verified_at', '')}")
        lines.append(f"- Availability: {routing.get('availability_boundary', '')}")
        for rule in routing.get("rules", []):
            lines.append(f"- {rule}")
        lines.append(f"- Promotion gate: {routing.get('promotion_gate', '')}")
    guidance = report.get("host_guidance", []) if isinstance(report.get("host_guidance"), list) else []
    if guidance:
        lines.extend(["", "## Host Guidance", ""])
        lines.extend(f"- {item}" for item in guidance)
    support = report.get("host_support", []) if isinstance(report.get("host_support"), list) else []
    if support:
        lines.extend(
            [
                "",
                "## Host Compatibility",
                "",
                "| Host | Worker Selection | Model Selection | Fallback | Mitigation |",
                "|---|---|---|---|---|",
            ]
        )
        for row in support:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"| `{row.get('host', '')}` | {row.get('worker_selection', '')} | "
                f"{row.get('model_selection', '')} | {row.get('fallback', '')} | {row.get('mitigation', '')} |"
            )
    authority = report.get("validation_authority") if isinstance(report.get("validation_authority"), dict) else {}
    if authority:
        authoritative = authority.get("authoritative", []) if isinstance(authority.get("authoritative"), list) else []
        advisory = authority.get("advisory", []) if isinstance(authority.get("advisory"), list) else []
        lines.extend(["", "## Validation Authority", ""])
        if authoritative:
            lines.append(f"- Authoritative: {', '.join(str(item) for item in authoritative)}")
        if advisory:
            lines.append(f"- Advisory: {', '.join(str(item) for item in advisory)}")
        required_record = str(authority.get("required_record", "")).strip()
        if required_record:
            lines.append(f"- Required record: {required_record}")
    cost_guidance = report.get("cost_guidance", []) if isinstance(report.get("cost_guidance"), list) else []
    if cost_guidance:
        lines.extend(["", "## Cost Guidance", ""])
        lines.extend(f"- {item}" for item in cost_guidance)
    return "\n".join(lines) + "\n"
