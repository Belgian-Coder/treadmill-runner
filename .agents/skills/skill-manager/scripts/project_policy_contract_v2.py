#!/usr/bin/env python3
"""Authoritative project-policy v2 shape, migration, schema, and semantics."""

from __future__ import annotations

import copy
from typing import Any

SCHEMA_VERSION = 2
SCHEMA_ID = "https://schemas.skills-harness.invalid/project-policy/v2"
INSTANCE_SCHEMA = "skills/skill-manager/assets/schemas/project-policy.schema.json"

V1_COST_PATH_TO_V2 = {
    "always_loaded_budget_tokens": "context.always_loaded.budget_tokens",
    "always_loaded_files": "context.always_loaded.files",
    "beginner_loaded_budget_tokens": "context.beginner.budget_tokens",
    "beginner_loaded_files": "context.beginner.files",
    "default_guidance_budget_tokens": "guidance.default.budget_tokens",
    "default_guidance_files": "guidance.default.files",
    "broad_guidance_baseline_files": "guidance.baseline.files",
    "min_guidance_saved_percent": "guidance.minimum_saved_percent",
    "startup_context_max_added_tokens": "guidance.startup.max_added_tokens",
    "startup_context_max_added_percent": "guidance.startup.max_added_percent",
    "default_phase_budget_tokens": "budgets.phases.default_tokens",
    "phase_budgets": "budgets.phases.overrides",
    "review_loop": "review.loop",
    "paid_model_fallback": "routing.default_paid_model_fallback",
    "task_routes": "routing.tasks",
    "delegation_gates": "delegation.gates",
    "warm_server_batch": "local_ai.warm_batch",
}

REMOVED_V1_INVARIANTS = {
    "schema_version",
    "id",
    "mode",
    "prefer_local_ai_over_paid_small_models",
    "compact_outputs_default",
    "deterministic_checks_first",
    "find_first_read_second",
    "delta_only_review",
    "stable_context_cache",
    "token_savings_report",
    "default_guidance_required",
}


def v2_cost_policy_from_v1(value: dict[str, Any]) -> dict[str, Any]:
    """Map the complete v1 cost policy to grouped v2 project choices."""

    delegation = copy.deepcopy(value.get("delegation_gates", {}))
    for gate in delegation.values():
        if isinstance(gate, dict):
            for key in ("quality_noninferior", "required_token_provenance", "fallback"):
                gate.pop(key, None)
    warm = copy.deepcopy(value.get("warm_server_batch", {}))
    for key in ("enabled", "auto_shutdown", "schema_validation_required"):
        warm.pop(key, None)
    return {
        "context": {
            "always_loaded": {
                "budget_tokens": value.get("always_loaded_budget_tokens"),
                "files": copy.deepcopy(value.get("always_loaded_files")),
            },
            "beginner": {
                "budget_tokens": value.get("beginner_loaded_budget_tokens"),
                "files": copy.deepcopy(value.get("beginner_loaded_files")),
            },
        },
        "guidance": {
            "default": {
                "budget_tokens": value.get("default_guidance_budget_tokens"),
                "files": copy.deepcopy(value.get("default_guidance_files")),
            },
            "baseline": {"files": copy.deepcopy(value.get("broad_guidance_baseline_files"))},
            "minimum_saved_percent": value.get("min_guidance_saved_percent"),
            "startup": {
                "max_added_tokens": value.get("startup_context_max_added_tokens"),
                "max_added_percent": value.get("startup_context_max_added_percent"),
            },
        },
        "budgets": {
            "phases": {
                "default_tokens": value.get("default_phase_budget_tokens"),
                "overrides": copy.deepcopy(value.get("phase_budgets")),
            }
        },
        "review": {"loop": copy.deepcopy(value.get("review_loop"))},
        "routing": {
            "default_paid_model_fallback": value.get("paid_model_fallback"),
            "tasks": copy.deepcopy(value.get("task_routes")),
        },
        "delegation": {"gates": delegation},
        "local_ai": {"warm_batch": warm},
    }


def legacy_cost_policy_from_v2(value: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Adapt validated v2 choices to the established internal cost-policy API."""

    result = copy.deepcopy(defaults)
    context = value["context"]
    guidance = value["guidance"]
    phases = value["budgets"]["phases"]
    result.update(
        {
            "always_loaded_budget_tokens": context["always_loaded"]["budget_tokens"],
            "always_loaded_files": copy.deepcopy(context["always_loaded"]["files"]),
            "beginner_loaded_budget_tokens": context["beginner"]["budget_tokens"],
            "beginner_loaded_files": copy.deepcopy(context["beginner"]["files"]),
            "default_guidance_budget_tokens": guidance["default"]["budget_tokens"],
            "default_guidance_files": copy.deepcopy(guidance["default"]["files"]),
            "broad_guidance_baseline_files": copy.deepcopy(guidance["baseline"]["files"]),
            "min_guidance_saved_percent": guidance["minimum_saved_percent"],
            "startup_context_max_added_tokens": guidance["startup"]["max_added_tokens"],
            "startup_context_max_added_percent": guidance["startup"]["max_added_percent"],
            "default_phase_budget_tokens": phases["default_tokens"],
            "phase_budgets": copy.deepcopy(phases["overrides"]),
            "review_loop": copy.deepcopy(value["review"]["loop"]),
            "paid_model_fallback": value["routing"]["default_paid_model_fallback"],
            "task_routes": copy.deepcopy(value["routing"]["tasks"]),
        }
    )
    gates = copy.deepcopy(value["delegation"]["gates"])
    for gate in gates.values():
        gate.update(
            {
                "quality_noninferior": True,
                "required_token_provenance": "provider_telemetry",
                "fallback": "single-agent",
            }
        )
    result["delegation_gates"] = gates
    warm = copy.deepcopy(value["local_ai"]["warm_batch"])
    warm.update({"enabled": True, "auto_shutdown": True, "schema_validation_required": True})
    result["warm_server_batch"] = warm
    return result


def v2_validation_issue_from_legacy(issue: str) -> str:
    """Translate internal-adapter validation labels back to public v2 paths."""

    translated = issue
    for old, new in sorted(V1_COST_PATH_TO_V2.items(), key=lambda item: len(item[0]), reverse=True):
        translated = translated.replace(f"cost_policy.{old}", f"cost_policy.{new}")
    return translated


def owner_defaults() -> dict[str, Any]:
    return {
        "skill_manager": {
            "claude_adapter": {
                "name_only_saved_tokens": 3000,
                "name_only_skill_count": 50,
                "context_window_tokens": 200000,
            },
            "capability_audit": {"low_context_token_target": 5000, "fast_daily_target_ms": 4000},
            "optimization": {"lesson_promotion_min_count": 2},
            "review_cost": {
                "extra_output_tokens": 1200,
                "output_price_multiplier": 4,
                "visible_history_entries": 20,
            },
        },
        "repo_navigation": {
            "briefing": {
                "default_profile": "normal",
                "profiles": {
                    "short": {"max_files": 1200, "max_text_files": 80, "item_limit": 8, "read_order_limit": 12, "do_not_open_limit": 12},
                    "normal": {"max_files": 5000, "max_text_files": 250, "item_limit": 20, "read_order_limit": 30, "do_not_open_limit": 40},
                    "deep": {"max_files": 5000, "max_text_files": 800, "item_limit": 60, "read_order_limit": 80, "do_not_open_limit": 80},
                },
            }
        },
        "workflow_manager": {
            "context_evidence": {
                "top_k": 5,
            }
        },
    }


def output_profiles() -> dict[str, Any]:
    return {
        "compact_command": {"max_chars": 1200, "max_lines": 16},
        "diagnostic": {"max_chars": 2000, "max_lines": 40},
        "evidence": {"max_chars": 4000, "max_lines": 80},
    }


def _schema_node(value: Any, path: str, specs: dict[str, dict[str, object]]) -> dict[str, Any]:
    if isinstance(value, dict):
        properties = {
            key: _schema_node(item, f"{path}.{key}" if path else key, specs)
            for key, item in value.items()
        }
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }
    spec = specs.get(path, {})
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        node: dict[str, Any] = {"type": "integer"}
        if isinstance(spec.get("minimum"), int):
            node["minimum"] = spec["minimum"]
        if isinstance(spec.get("maximum"), int):
            node["maximum"] = spec["maximum"]
        return node
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        node = {"type": "string", "minLength": 1}
        allowed = spec.get("choices")
        if isinstance(allowed, (list, tuple, set)):
            node["enum"] = sorted(str(item) for item in allowed)
        return node
    if isinstance(value, list):
        item_type = "string" if not value or all(isinstance(item, str) for item in value) else None
        node = {"type": "array", "minItems": 1, "uniqueItems": True}
        if item_type:
            node["items"] = {"type": item_type, "minLength": 1}
        return node
    raise TypeError(f"unsupported project-policy default at {path}: {type(value).__name__}")


def project_policy_schema(default_document: dict[str, Any], specs: dict[str, dict[str, object]]) -> dict[str, Any]:
    root = _schema_node(default_document, "", specs)
    root["properties"]["$schema"] = {"const": INSTANCE_SCHEMA}
    root["properties"]["schema_version"] = {"const": SCHEMA_VERSION}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "Skills Harness Project Policy v2",
        "description": "Strict portable project-owned policy. Runtime semantic validation remains authoritative for cross-field rules.",
        **root,
    }


def _safe_relative_path(raw: object) -> bool:
    from repo_support.repo_harness_paths import normalize_relative_path

    if not isinstance(raw, str) or "\\" in raw:
        return False
    try:
        return normalize_relative_path(raw) == raw
    except ValueError:
        return False


def validate_semantics(document: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    cost = document.get("cost_policy", {})
    try:
        contexts = [
            ("cost_policy.context.always_loaded.files", cost["context"]["always_loaded"]["files"]),
            ("cost_policy.context.beginner.files", cost["context"]["beginner"]["files"]),
            ("cost_policy.guidance.default.files", cost["guidance"]["default"]["files"]),
            ("cost_policy.guidance.baseline.files", cost["guidance"]["baseline"]["files"]),
        ]
    except (KeyError, TypeError):
        return issues
    safe_contexts: list[tuple[str, list[str]]] = []
    for path, values in contexts:
        if not isinstance(values, list) or not values:
            issues.append(f"{path} must be a non-empty list.")
            continue
        strings = [item for item in values if isinstance(item, str)]
        if len(strings) != len(values):
            issues.append(f"{path} must contain only strings.")
        if len(strings) != len(set(strings)):
            issues.append(f"{path} must contain unique paths.")
        for item in strings:
            if not _safe_relative_path(item):
                issues.append(f"{path} contains unsafe project-relative path: {item!r}.")
        safe_contexts.append((path, strings))
    baseline_values = cost["guidance"]["baseline"]["files"]
    baseline = set(item for item in baseline_values if isinstance(item, str)) if isinstance(baseline_values, list) else set()
    for path, values in safe_contexts[:3]:
        missing = sorted(set(values) - baseline)
        if missing:
            issues.append(f"{path} must be contained in cost_policy.guidance.baseline.files: {', '.join(missing)}.")
    return issues
