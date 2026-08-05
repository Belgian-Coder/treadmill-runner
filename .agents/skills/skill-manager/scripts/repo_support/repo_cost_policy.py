"""Local-first cost and context policy reporting."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from repo_support import repo_common as repo

CONFIG_PATH = ".agents/project-policy.json"
LOCAL_AI_CONFIG_PATH = ".agents/local-ai.json"
DEFAULT_POLICY_ID = "local-first-context-savings"
REQUIRED_TASK_ROUTES = {
    "routing",
    "context-discovery",
    "planning",
    "implementation",
    "test-authoring",
    "validation",
    "review",
    "handoff",
}
REQUIRED_PHASE_BUDGETS = {
    "routing",
    "planning",
    "implementation",
    "test-authoring",
    "validation",
    "evidence",
    "handoff",
}
LOCAL_AI_TASKS = {
    "skill-routing",
    "workflow-routing",
    "validation-triage",
    "inventory-summary",
    "changed-files-summary",
    "failure-cluster",
    "test-gap-summary",
    "handoff-draft",
    "duplicate-overlap-detection",
}
LOW_CONTEXT_FILES = [
    "AGENTS.md",
    ".agents/routing.md",
    "automations/routing.md",
]
BEGINNER_CONTEXT_FILES = [
    "README.md",
    "docs/start-here.md",
]
DEFAULT_GUIDANCE_FILES = LOW_CONTEXT_FILES + [
    "automations/navigation/artifacts/maps/HANDOFF.md",
]
BROAD_GUIDANCE_BASELINE_FILES = LOW_CONTEXT_FILES + BEGINNER_CONTEXT_FILES + [
    "automations/navigation/artifacts/maps/HANDOFF.md",
    "automations/navigation/artifacts/maps/NAVIGATION.md",
    "automations/navigation/artifacts/maps/TECHNICAL_CONTEXT.md",
    "automations/navigation/artifacts/maps/CONVENTIONS.md",
]
DEFAULT_GUIDANCE_MIN_SAVED_PERCENT = 25
DEFAULT_STARTUP_CONTEXT_MAX_ADDED_TOKENS = 250
DEFAULT_STARTUP_CONTEXT_MAX_ADDED_PERCENT = 10
DEFAULT_REVIEW_LOOP_POLICY = {
    "max_units": 20,
    "max_estimated_tokens": 8000,
    "max_elapsed_ms": 180000,
    "max_hunks_per_batch": 12,
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


def estimate_tokens_from_bytes(byte_count: int) -> int:
    return max(1, (max(byte_count, 0) + 3) // 4) if byte_count else 0


def default_cost_policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": DEFAULT_POLICY_ID,
        "mode": "local-first",
        "prefer_local_ai_over_paid_small_models": True,
        "paid_model_fallback": "explicit-after-local-ai-or-deterministic-summary",
        "compact_outputs_default": True,
        "deterministic_checks_first": True,
        "find_first_read_second": True,
        "delta_only_review": True,
        "stable_context_cache": True,
        "token_savings_report": True,
        "default_guidance_required": True,
        "default_guidance_budget_tokens": 5000,
        "default_guidance_files": DEFAULT_GUIDANCE_FILES,
        "broad_guidance_baseline_files": BROAD_GUIDANCE_BASELINE_FILES,
        "min_guidance_saved_percent": DEFAULT_GUIDANCE_MIN_SAVED_PERCENT,
        "startup_context_max_added_tokens": DEFAULT_STARTUP_CONTEXT_MAX_ADDED_TOKENS,
        "startup_context_max_added_percent": DEFAULT_STARTUP_CONTEXT_MAX_ADDED_PERCENT,
        "review_loop": dict(DEFAULT_REVIEW_LOOP_POLICY),
        "delegation_gates": {
            "delegation-balanced-v1": dict(DEFAULT_DELEGATION_GATE),
        },
        "warm_server_batch": {
            "enabled": True,
            "min_items": 2,
            "auto_shutdown": True,
            "schema_validation_required": True,
            "prefer_for_tasks": [
                "changed-files-summary",
                "failure-cluster",
                "test-gap-summary",
                "handoff-draft",
            ],
        },
        "always_loaded_budget_tokens": 3500,
        "always_loaded_files": LOW_CONTEXT_FILES,
        "beginner_loaded_budget_tokens": 5000,
        "beginner_loaded_files": BEGINNER_CONTEXT_FILES,
        "default_phase_budget_tokens": 6000,
        "phase_budgets": {
            "routing": 1500,
            "planning": 6000,
            "implementation": 8000,
            "test-authoring": 6000,
            "validation": 3000,
            "evidence": 12000,
            "handoff": 2500,
        },
        "task_routes": {
            "routing": {
                "prefer": "local-ai",
                "local_ai_use_cases": ["skill-routing", "workflow-routing"],
                "max_context_tokens": 1500,
                "fallback": "deterministic-routing",
                "paid_model_fallback": "disabled-by-default",
                "authoritative_evidence": "routing files and deterministic route scores",
            },
            "context-discovery": {
                "prefer": "exact-search-then-read",
                "local_ai_use_cases": ["inventory-summary"],
                "max_context_tokens": 4000,
                "fallback": "direct file reads from cited paths",
                "paid_model_fallback": "allowed-after-bounded-evidence",
                "authoritative_evidence": "repo files and exact search output",
            },
            "planning": {
                "prefer": "orchestrator-with-local-evidence",
                "local_ai_use_cases": ["inventory-summary", "test-gap-summary"],
                "max_context_tokens": 6000,
                "fallback": "paid planning model after local evidence packet",
                "paid_model_fallback": "allowed-for-decisions",
                "authoritative_evidence": "plan, project context, and deterministic checks",
            },
            "implementation": {
                "prefer": "orchestrator",
                "local_ai_use_cases": ["changed-files-summary"],
                "max_context_tokens": 8000,
                "fallback": "paid implementation model",
                "paid_model_fallback": "allowed-for-code",
                "authoritative_evidence": "source files and tests",
            },
            "test-authoring": {
                "prefer": "orchestrator-with-local-gap-summary",
                "local_ai_use_cases": ["test-gap-summary"],
                "max_context_tokens": 6000,
                "fallback": "paid implementation or test-authoring model",
                "paid_model_fallback": "allowed-for-tests",
                "authoritative_evidence": "test files, coverage, and acceptance criteria",
            },
            "validation": {
                "prefer": "deterministic",
                "local_ai_use_cases": ["failure-cluster"],
                "max_context_tokens": 3000,
                "fallback": "deterministic failed-output packet",
                "paid_model_fallback": "allowed-after-deterministic-evidence",
                "authoritative_evidence": "command exit codes and raw validation output",
            },
            "review": {
                "prefer": "delta-only",
                "local_ai_use_cases": ["changed-files-summary", "duplicate-overlap-detection"],
                "max_context_tokens": 5000,
                "fallback": "paid review model after diff summary",
                "paid_model_fallback": "allowed-after-delta-summary",
                "authoritative_evidence": "git diff, changed files, and deterministic reports",
            },
            "handoff": {
                "prefer": "local-ai",
                "local_ai_use_cases": ["handoff-draft", "changed-files-summary"],
                "max_context_tokens": 2500,
                "fallback": "orchestrator verifies local draft",
                "paid_model_fallback": "disabled-by-default",
                "authoritative_evidence": "run report, validation, and skipped checks",
            },
        },
    }


def read_json(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        return {}, str(exc)
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON: {exc}"
    return data if isinstance(data, dict) else {}, None


def load_local_ai_config(root: Path) -> tuple[dict[str, Any], str | None]:
    return read_json(root / LOCAL_AI_CONFIG_PATH)


def load_cost_policy(root: Path) -> tuple[dict[str, Any], str | None]:
    from project_policy_contract_v2 import legacy_cost_policy_from_v2
    from repo_support import repo_policy

    config, issues, exists = repo_policy.load_project_policy(root)
    if issues:
        return {}, "; ".join(issues)
    if not exists:
        return default_cost_policy(), None
    policy = config.get("cost_policy")
    if not isinstance(policy, dict):
        return {}, f"missing cost_policy object in {CONFIG_PATH}"
    return legacy_cost_policy_from_v2(policy, default_cost_policy()), None


def bool_field(policy: dict[str, Any], key: str) -> bool:
    return bool(policy.get(key))


def int_field(value: object, default: int) -> int:
    return value if isinstance(value, int) and value >= 0 else default


def positive_int_field(value: object, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default


def positive_budget_resolution(
    values: dict[str, Any],
    key: str,
    default: int,
    *,
    path: str | None = None,
) -> dict[str, Any]:
    label = path or f"cost_policy.{key}"
    if key not in values:
        return {"value": default, "source": "default-missing", "issue": ""}
    value = values.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return {"value": value, "source": "configured", "issue": ""}
    return {
        "value": default,
        "source": "fallback-invalid",
        "issue": f"{label} must be a positive integer (boolean is not allowed).",
    }


def phase_budget_resolution(policy: dict[str, Any], budget_ref: str) -> dict[str, Any]:
    default = positive_budget_resolution(
        policy,
        "default_phase_budget_tokens",
        6000,
    )
    raw = policy.get("phase_budgets")
    if "phase_budgets" not in policy:
        return {"value": default["value"], "source": "default-missing", "issue": default["issue"]}
    if not isinstance(raw, dict):
        return {
            "value": default["value"],
            "source": "fallback-invalid",
            "issue": "cost_policy.phase_budgets must be an object.",
        }
    resolved = positive_budget_resolution(
        raw,
        budget_ref,
        int(default["value"]),
        path=f"cost_policy.phase_budgets.{budget_ref}",
    )
    if default["issue"] and not resolved["issue"]:
        resolved["issue"] = default["issue"]
        resolved["source"] = "fallback-invalid"
    return resolved


def review_loop_policy(policy: dict[str, Any]) -> dict[str, int]:
    raw = policy.get("review_loop")
    raw = raw if isinstance(raw, dict) else {}
    return {
        "max_units": positive_int_field(raw.get("max_units"), DEFAULT_REVIEW_LOOP_POLICY["max_units"]),
        "max_estimated_tokens": positive_int_field(
            raw.get("max_estimated_tokens"),
            DEFAULT_REVIEW_LOOP_POLICY["max_estimated_tokens"],
        ),
        "max_elapsed_ms": positive_int_field(raw.get("max_elapsed_ms"), DEFAULT_REVIEW_LOOP_POLICY["max_elapsed_ms"]),
        "max_hunks_per_batch": positive_int_field(
            raw.get("max_hunks_per_batch"),
            DEFAULT_REVIEW_LOOP_POLICY["max_hunks_per_batch"],
        ),
    }


def task_routes(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = policy.get("task_routes")
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}


def phase_budgets(policy: dict[str, Any]) -> dict[str, int]:
    raw = policy.get("phase_budgets")
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): int(value)
        for key, value in raw.items()
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    }


def local_ai_tasks(config: dict[str, Any]) -> set[str]:
    tasks = config.get("tasks")
    if isinstance(tasks, list):
        return {str(item) for item in tasks if str(item).strip()}
    return set(LOCAL_AI_TASKS)


def changed_files(root: Path) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return []
    return [line.strip().replace("\\", "/") for line in completed.stdout.splitlines() if line.strip()]


def changed_diff_estimate(root: Path) -> dict[str, Any]:
    added = 0
    deleted = 0
    tracked_paths: set[str] = set()
    for args in (["git", "diff", "--numstat"], ["git", "diff", "--cached", "--numstat"]):
        try:
            completed = subprocess.run(
                args,
                cwd=root,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            continue
        for line in completed.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            path = parts[-1].strip().replace("\\", "/")
            if not path or path.startswith(repo.DEFAULT_CHANGED_IGNORE_PREFIXES):
                continue
            tracked_paths.add(path)
            if parts[0].isdigit():
                added += int(parts[0])
            if parts[1].isdigit():
                deleted += int(parts[1])
    untracked_paths: list[str] = []
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        completed = None
    if completed is not None:
        untracked_paths = [
            line.strip().replace("\\", "/")
            for line in completed.stdout.splitlines()
            if line.strip() and not line.strip().replace("\\", "/").startswith(repo.DEFAULT_CHANGED_IGNORE_PREFIXES)
        ]
    untracked_tokens = 0
    for rel in untracked_paths:
        path = root / rel
        if not path.is_file():
            continue
        try:
            untracked_tokens += estimate_tokens_from_bytes(path.stat().st_size)
        except OSError:
            continue
    tracked_tokens = (added + deleted) * 12
    return {
        "files": len(tracked_paths) + len(untracked_paths),
        "tracked_files": len(tracked_paths),
        "untracked_files": len(untracked_paths),
        "added": added,
        "deleted": deleted,
        "tracked_estimated_tokens": tracked_tokens,
        "untracked_estimated_tokens": untracked_tokens,
        "estimated_tokens": tracked_tokens + untracked_tokens,
    }


def configured_paths(policy: dict[str, Any], key: str, default: list[str]) -> list[str]:
    raw = policy.get(key)
    if not isinstance(raw, list):
        return list(default)
    paths = [str(item).replace("\\", "/") for item in raw if str(item).strip()]
    return paths


def low_context_report(root: Path, budget_tokens: int, paths: list[str]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    total = 0
    for rel in paths:
        path = root / rel
        if not path.is_file():
            missing.append(rel)
            continue
        size = path.stat().st_size
        tokens = estimate_tokens_from_bytes(size)
        rows.append({"path": rel, "size_bytes": size, "estimated_tokens": tokens})
        total += tokens
    return {
        "budget_tokens": budget_tokens,
        "estimated_tokens": total,
        "within_budget": total <= budget_tokens,
        "complete": not missing and len(rows) == len(paths),
        "expected_file_count": len(paths),
        "present_file_count": len(rows),
        "files": rows,
        "missing": missing,
    }


def numeric_percent(value: object, default: int) -> int:
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    return default


def guidance_savings_report(root: Path, policy: dict[str, Any]) -> dict[str, Any]:
    default_paths = configured_paths(policy, "default_guidance_files", DEFAULT_GUIDANCE_FILES)
    baseline_paths = configured_paths(policy, "broad_guidance_baseline_files", BROAD_GUIDANCE_BASELINE_FILES)
    budget_resolution = positive_budget_resolution(
        policy,
        "default_guidance_budget_tokens",
        5000,
    )
    budget = int(budget_resolution["value"])
    minimum_percent = numeric_percent(policy.get("min_guidance_saved_percent"), DEFAULT_GUIDANCE_MIN_SAVED_PERCENT)
    default_context = low_context_report(root, budget, default_paths)
    broad_baseline = low_context_report(root, max(budget, 1_000_000), baseline_paths)
    default_tokens = int(default_context.get("estimated_tokens", 0) or 0)
    baseline_tokens = int(broad_baseline.get("estimated_tokens", 0) or 0)
    raw_saved = baseline_tokens - default_tokens
    saved = max(0, raw_saved)
    saved_percent = round((raw_saved / baseline_tokens) * 100, 2) if baseline_tokens else 0.0
    complete = bool(default_context.get("complete")) and bool(broad_baseline.get("complete"))
    within_absolute_budget = bool(default_context.get("within_budget"))
    measurable = complete and bool(default_context.get("files")) and bool(broad_baseline.get("files")) and baseline_tokens > 0
    better = measurable and raw_saved > 0
    meets_minimum = better and saved_percent >= minimum_percent
    status = "unavailable"
    if not complete:
        status = "incomplete"
    elif not within_absolute_budget:
        status = "over-budget"
    elif measurable:
        if meets_minimum:
            status = "measurably-better"
        elif better:
            status = "better-below-threshold"
        else:
            status = "not-better"
    return {
        "use_by_default": bool_field(policy, "default_guidance_required"),
        "status": status,
        "measurable": measurable,
        "complete": complete,
        "budget_tokens": budget,
        "budget_source": budget_resolution["source"],
        "budget_issue": budget_resolution["issue"],
        "within_absolute_budget": within_absolute_budget,
        "better_than_baseline": better,
        "meets_minimum": meets_minimum,
        "min_saved_percent": minimum_percent,
        "token_counter": "estimated_utf8_bytes_div_4",
        "provenance": "heuristic_estimate",
        "scope": "artifact",
        "default_context": default_context,
        "broad_baseline": broad_baseline,
        "default_guidance_tokens": default_tokens,
        "broad_baseline_tokens": baseline_tokens,
        "saved_tokens_estimated": saved,
        "saved_percent_estimated": saved_percent,
        "boundary": (
            "Estimated input-context tokens for routing/orientation files only. "
            "This is not provider billing telemetry and excludes hidden prompts, tool payloads, output tokens, and reasoning tokens."
        ),
    }


def workflow_phase_rows(root: Path, policy: dict[str, Any], workflow_name: str | None) -> list[dict[str, Any]]:
    automations = root / "automations"
    if not automations.exists():
        return []
    rows: list[dict[str, Any]] = []
    for module_path in sorted(automations.glob("*/module.json"), key=lambda item: item.as_posix()):
        if workflow_name and module_path.parent.name != workflow_name:
            continue
        manifest, error = read_json(module_path)
        if error:
            rows.append({"workflow": module_path.parent.name, "ok": False, "issues": [error]})
            continue
        phases = manifest.get("phases")
        if not isinstance(phases, list):
            continue
        for phase in phases:
            phase_id = str(phase.get("id", "") if isinstance(phase, dict) else phase).strip()
            if not phase_id:
                continue
            category = phase_category(phase_id)
            budget = phase_budget_resolution(policy, category)
            rows.append(
                {
                    "workflow": module_path.parent.name,
                    "phase": phase_id,
                    "category": category,
                    "budget_tokens": budget["value"],
                    "budget_source": budget["source"],
                    "budget_issue": budget["issue"],
                }
            )
    return rows


def phase_category(phase_id: str) -> str:
    lowered = phase_id.lower()
    if "routing" in lowered:
        return "routing"
    if "test" in lowered:
        return "test-authoring"
    if "valid" in lowered or "hardening" in lowered or "quality" in lowered:
        return "validation"
    if "implement" in lowered or "migration" in lowered or "modernization" in lowered:
        return "implementation"
    if "handoff" in lowered or "closeout" in lowered or "finish" in lowered:
        return "handoff"
    if "plan" in lowered or "approval" in lowered or "assessment" in lowered or "review" in lowered:
        return "planning"
    return "evidence"


def warm_server_batch_policy(policy: dict[str, Any]) -> dict[str, Any]:
    raw = policy.get("warm_server_batch")
    if not isinstance(raw, dict):
        raw = default_cost_policy()["warm_server_batch"]
    min_items = raw.get("min_items", 2)
    if not isinstance(min_items, int) or min_items < 2:
        min_items = 2
    prefer_for_tasks = raw.get("prefer_for_tasks")
    if not isinstance(prefer_for_tasks, list):
        prefer_for_tasks = default_cost_policy()["warm_server_batch"]["prefer_for_tasks"]
    return {
        "enabled": bool(raw.get("enabled", True)),
        "min_items": min_items,
        "auto_shutdown": bool(raw.get("auto_shutdown", True)),
        "schema_validation_required": bool(raw.get("schema_validation_required", True)),
        "prefer_for_tasks": [str(item) for item in prefer_for_tasks if str(item).strip()],
    }


def token_savings_report(
    policy: dict[str, Any],
    low_context: dict[str, Any],
    beginner_context: dict[str, Any],
    diff_estimate: dict[str, Any],
    guidance_savings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    routes = task_routes(policy)
    review_budget = int(routes.get("review", {}).get("max_context_tokens", 5000) or 5000)
    changed_tokens = int(diff_estimate.get("estimated_tokens", 0) or 0)
    beginner_tokens = int(beginner_context.get("estimated_tokens", 0) or 0)
    always_tokens = int(low_context.get("estimated_tokens", 0) or 0)
    return {
        "routine_skips_beginner_tokens": beginner_tokens,
        "always_loaded_tokens": always_tokens,
        "beginner_loaded_tokens": beginner_tokens,
        "review_budget_tokens": review_budget,
        "changed_diff_estimated_tokens": changed_tokens,
        "tracked_diff_estimated_tokens": int(diff_estimate.get("tracked_estimated_tokens", 0) or 0),
        "untracked_file_estimated_tokens": int(diff_estimate.get("untracked_estimated_tokens", 0) or 0),
        "tracked_changed_file_count": int(diff_estimate.get("tracked_files", 0) or 0),
        "untracked_changed_file_count": int(diff_estimate.get("untracked_files", 0) or 0),
        "changed_diff_tokens_over_review_budget": max(0, changed_tokens - review_budget),
        "guidance_saved_tokens_estimated": (guidance_savings or {}).get("saved_tokens_estimated", 0),
        "guidance_saved_percent_estimated": (guidance_savings or {}).get("saved_percent_estimated", 0.0),
        "guidance_status": (guidance_savings or {}).get("status", "unknown"),
        "savings_controls": {
            "owner_first_retrieval": bool_field(policy, "find_first_read_second"),
            "compact_outputs": bool_field(policy, "compact_outputs_default"),
            "search_before_read": bool_field(policy, "find_first_read_second"),
            "delta_review": bool_field(policy, "delta_only_review"),
        },
        "plain_language": [
            "Normal workflow starts do not load beginner docs unless the user asks for beginner help.",
            "Normal workflow starts use the default guidance packet instead of loading broad orientation files.",
            "Exact search narrows named skill/workflow owners before broader repository reads.",
            "Changed diffs above the review budget should get a local changed-files summary before paid review.",
        ],
    }


def nonnegative_int(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) and value >= 0 else default


def percent_saved(baseline: int, candidate: int) -> float:
    if baseline <= 0:
        return 0.0
    return round((max(0, baseline - candidate) / baseline) * 100, 2)


def review_cost_ledger(packet: dict[str, Any]) -> dict[str, Any]:
    """Build a provider-neutral input-token ledger for review routing."""
    review_budget = nonnegative_int(packet.get("review_budget_tokens"), 0)
    raw_tokens = nonnegative_int(
        packet.get("raw_changed_diff_estimated_tokens"),
        nonnegative_int(
            packet.get("changed_diff_estimated_tokens"),
            nonnegative_int(packet.get("estimated_changed_tokens"), 0),
        ),
    )
    owner_packets = packet.get("owner_review_packets")
    has_owner_packet_list = isinstance(owner_packets, list)
    owner_tokens: list[int] = []
    subpacket_tokens: list[int] = []
    hunk_tokens: list[int] = []
    review_unit_tokens: list[int] = []
    if has_owner_packet_list:
        owner_tokens = [
            nonnegative_int(item.get("estimated_changed_tokens"), 0)
            for item in owner_packets
            if isinstance(item, dict)
        ]
        for item in owner_packets:
            if not isinstance(item, dict):
                continue
            subpackets = item.get("owner_review_subpackets")
            if not isinstance(subpackets, list):
                continue
            subpacket_tokens.extend(
                nonnegative_int(subpacket.get("estimated_changed_tokens"), 0)
                for subpacket in subpackets
                if isinstance(subpacket, dict)
            )
            for subpacket in subpackets:
                if not isinstance(subpacket, dict):
                    continue
                hunks = subpacket.get("path_review_hunks")
                if isinstance(hunks, list) and hunks:
                    hunk_tokens.extend(
                        nonnegative_int(hunk.get("estimated_changed_tokens"), 0)
                        for hunk in hunks
                        if isinstance(hunk, dict)
                    )
                    review_unit_tokens.extend(
                        nonnegative_int(hunk.get("estimated_changed_tokens"), 0)
                        for hunk in hunks
                        if isinstance(hunk, dict)
                    )
                else:
                    review_unit_tokens.append(nonnegative_int(subpacket.get("estimated_changed_tokens"), 0))
    elif packet.get("tool") == "skill-manager.owner-review-packet":
        owner_tokens = [nonnegative_int(packet.get("estimated_changed_tokens"), 0)]
        if packet.get("scope") == "hunk-slice":
            hunk_tokens = owner_tokens
            review_unit_tokens = owner_tokens
        elif packet.get("scope") == "path-slice":
            subpacket_tokens = owner_tokens
            hunks = packet.get("path_review_hunks")
            if isinstance(hunks, list) and hunks:
                hunk_tokens = [
                    nonnegative_int(hunk.get("estimated_changed_tokens"), 0)
                    for hunk in hunks
                    if isinstance(hunk, dict)
                ]
                review_unit_tokens = hunk_tokens
            else:
                review_unit_tokens = owner_tokens
        else:
            subpackets = packet.get("owner_review_subpackets")
            if isinstance(subpackets, list):
                subpacket_tokens = [
                    nonnegative_int(subpacket.get("estimated_changed_tokens"), 0)
                    for subpacket in subpackets
                    if isinstance(subpacket, dict)
                ]
                for subpacket in subpackets:
                    if not isinstance(subpacket, dict):
                        continue
                    hunks = subpacket.get("path_review_hunks")
                    if isinstance(hunks, list) and hunks:
                        hunk_tokens.extend(
                            nonnegative_int(hunk.get("estimated_changed_tokens"), 0)
                            for hunk in hunks
                            if isinstance(hunk, dict)
                        )
                        review_unit_tokens.extend(
                            nonnegative_int(hunk.get("estimated_changed_tokens"), 0)
                            for hunk in hunks
                            if isinstance(hunk, dict)
                        )
                    else:
                        review_unit_tokens.append(nonnegative_int(subpacket.get("estimated_changed_tokens"), 0))
    owner_count = len(owner_tokens)
    subpacket_count = len(subpacket_tokens)
    hunk_count = len(hunk_tokens)
    total_owner_tokens = sum(owner_tokens)
    largest_owner_tokens = max(owner_tokens, default=0)
    first_owner_tokens = owner_tokens[0] if owner_tokens else 0
    largest_subpacket_tokens = max(subpacket_tokens, default=0)
    first_subpacket_tokens = subpacket_tokens[0] if subpacket_tokens else 0
    largest_hunk_tokens = max(hunk_tokens, default=0)
    first_hunk_tokens = hunk_tokens[0] if hunk_tokens else 0
    comparison_tokens = largest_owner_tokens or first_owner_tokens or raw_tokens
    if not review_unit_tokens:
        review_unit_tokens = subpacket_tokens or owner_tokens
    next_review_unit_tokens = review_unit_tokens[0] if review_unit_tokens else raw_tokens
    largest_review_unit_tokens = max(review_unit_tokens, default=0)
    review_unit_count = len(review_unit_tokens)
    total_review_unit_tokens = sum(review_unit_tokens)
    status = "measured" if raw_tokens or owner_tokens else "unavailable"
    over_budget = raw_tokens > review_budget if review_budget else False
    if has_owner_packet_list:
        comparison_scope = "all-owner-packets"
    elif packet.get("scope") == "hunk-slice":
        comparison_scope = "selected-owner-hunk"
    elif packet.get("scope") == "path-slice":
        comparison_scope = "selected-owner-subpacket"
    else:
        comparison_scope = "selected-owner-packet"
    return {
        "schema_version": 1,
        "tool": "skill-manager.review-cost-ledger",
        "status": status,
        "token_counter": "git_numstat_lines_x12_plus_untracked_bytes_div_4",
        "billing_scope": "input-context-estimate-only",
        "billing_boundary": (
            "Excludes output tokens, reasoning tokens, hidden prompts, cache discounts, "
            "provider prices, and rework. Use provider usage telemetry for money claims."
        ),
        "review_budget_tokens": review_budget,
        "raw_changed_diff_estimated_tokens": raw_tokens,
        "review_budget_exceeded": over_budget,
        "tokens_over_review_budget": max(0, raw_tokens - review_budget),
        "owner_packet_count": owner_count,
        "owner_subpacket_count": subpacket_count,
        "owner_hunk_count": hunk_count,
        "comparison_scope": comparison_scope,
        "first_owner_packet_estimated_tokens": first_owner_tokens,
        "largest_owner_packet_estimated_tokens": largest_owner_tokens,
        "first_owner_subpacket_estimated_tokens": first_subpacket_tokens,
        "largest_owner_subpacket_estimated_tokens": largest_subpacket_tokens,
        "first_owner_hunk_estimated_tokens": first_hunk_tokens,
        "largest_owner_hunk_estimated_tokens": largest_hunk_tokens,
        "review_unit_count": review_unit_count,
        "next_review_unit_estimated_tokens": next_review_unit_tokens,
        "largest_review_unit_estimated_tokens": largest_review_unit_tokens,
        "review_units_estimated_tokens_total": total_review_unit_tokens,
        "owner_packets_estimated_tokens_total": total_owner_tokens,
        "single_agent_saved_tokens_vs_raw_estimated": max(0, raw_tokens - comparison_tokens),
        "single_agent_saved_percent_vs_raw_estimated": percent_saved(raw_tokens, comparison_tokens),
        "next_review_unit_saved_tokens_vs_raw_estimated": max(0, raw_tokens - next_review_unit_tokens),
        "next_review_unit_saved_percent_vs_raw_estimated": percent_saved(raw_tokens, next_review_unit_tokens),
        "all_review_units_saved_tokens_vs_raw_estimated": max(0, raw_tokens - total_review_unit_tokens) if review_unit_tokens else 0,
        "all_review_units_saved_percent_vs_raw_estimated": percent_saved(raw_tokens, total_review_unit_tokens) if review_unit_tokens else 0.0,
        "first_owner_saved_tokens_vs_raw_estimated": max(0, raw_tokens - first_owner_tokens) if first_owner_tokens else 0,
        "first_owner_saved_percent_vs_raw_estimated": percent_saved(raw_tokens, first_owner_tokens) if first_owner_tokens else 0.0,
        "selected_owner_delta_tokens_vs_raw_estimated": total_owner_tokens - raw_tokens if owner_tokens else 0,
        "all_owner_packets_delta_tokens_vs_raw_estimated": total_owner_tokens - raw_tokens if has_owner_packet_list else 0,
        "all_review_units_delta_tokens_vs_raw_estimated": total_review_unit_tokens - raw_tokens if review_unit_tokens else 0,
        "release_gate": "needs-owner-review" if over_budget and owner_count else "within-budget",
        "savings_claim": (
            "Owner and subpacket review routes reduce the maximum prompt size for any one review agent. "
            "Total review-unit tokens estimate the all-slices path and can be higher or lower than raw diff."
        ),
    }


def compact_review_cost_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(ledger, dict) or not ledger:
        return {}
    compact = {
        "status": ledger.get("status", "unknown"),
        "billing_scope": ledger.get("billing_scope", "input-context-estimate-only"),
        "comparison_scope": ledger.get("comparison_scope", ""),
        "raw_changed_diff_estimated_tokens": ledger.get("raw_changed_diff_estimated_tokens", 0),
        "review_budget_tokens": ledger.get("review_budget_tokens", 0),
        "review_budget_exceeded": bool(ledger.get("review_budget_exceeded", False)),
        "release_gate": ledger.get("release_gate", ""),
        "owner_packet_count": ledger.get("owner_packet_count", 0),
        "owner_subpacket_count": ledger.get("owner_subpacket_count", 0),
        "owner_hunk_count": ledger.get("owner_hunk_count", 0),
        "largest_owner_packet_estimated_tokens": ledger.get("largest_owner_packet_estimated_tokens", 0),
        "largest_owner_subpacket_estimated_tokens": ledger.get("largest_owner_subpacket_estimated_tokens", 0),
        "largest_owner_hunk_estimated_tokens": ledger.get("largest_owner_hunk_estimated_tokens", 0),
        "review_unit_count": ledger.get("review_unit_count", 0),
        "next_review_unit_estimated_tokens": ledger.get("next_review_unit_estimated_tokens", 0),
        "largest_review_unit_estimated_tokens": ledger.get("largest_review_unit_estimated_tokens", 0),
        "review_units_estimated_tokens_total": ledger.get("review_units_estimated_tokens_total", 0),
        "single_agent_saved_tokens_vs_raw_estimated": ledger.get("single_agent_saved_tokens_vs_raw_estimated", 0),
        "single_agent_saved_percent_vs_raw_estimated": ledger.get("single_agent_saved_percent_vs_raw_estimated", 0.0),
        "next_review_unit_saved_tokens_vs_raw_estimated": ledger.get("next_review_unit_saved_tokens_vs_raw_estimated", 0),
        "next_review_unit_saved_percent_vs_raw_estimated": ledger.get("next_review_unit_saved_percent_vs_raw_estimated", 0.0),
        "all_review_units_delta_tokens_vs_raw_estimated": ledger.get("all_review_units_delta_tokens_vs_raw_estimated", 0),
        "all_owner_packets_delta_tokens_vs_raw_estimated": ledger.get("all_owner_packets_delta_tokens_vs_raw_estimated", 0),
    }
    for key in (
        "source_review_unit_count",
        "batched_review_unit_count",
        "saved_batched_review_unit_count",
        "max_hunks_per_batch_limit",
    ):
        if key in ledger:
            compact[key] = ledger.get(key, 0)
    return compact


def validate_policy(
    policy: dict[str, Any],
    config: dict[str, Any],
    low_context: dict[str, Any],
    beginner_context: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    if policy.get("schema_version") != 1:
        issues.append("cost_policy.schema_version must be 1.")
    if policy.get("mode") != "local-first":
        issues.append("cost_policy.mode must be 'local-first'.")
    for key, default in (
        ("default_guidance_budget_tokens", 5000),
        ("default_phase_budget_tokens", 6000),
    ):
        issue = str(positive_budget_resolution(policy, key, default)["issue"])
        if issue and issue not in issues:
            issues.append(issue)
    if not bool_field(policy, "prefer_local_ai_over_paid_small_models"):
        issues.append("cost_policy must prefer local AI over paid small models.")
    for key in (
        "compact_outputs_default",
        "deterministic_checks_first",
        "find_first_read_second",
        "delta_only_review",
        "stable_context_cache",
        "token_savings_report",
        "default_guidance_required",
    ):
        if not bool_field(policy, key):
            issues.append(f"cost_policy.{key} must be true.")
    if not configured_paths(policy, "default_guidance_files", DEFAULT_GUIDANCE_FILES):
        issues.append("cost_policy.default_guidance_files must name at least one path.")
    if not configured_paths(policy, "broad_guidance_baseline_files", BROAD_GUIDANCE_BASELINE_FILES):
        issues.append("cost_policy.broad_guidance_baseline_files must name at least one path.")
    if numeric_percent(policy.get("min_guidance_saved_percent"), -1) < 0:
        issues.append("cost_policy.min_guidance_saved_percent must be a non-negative number.")
    if int_field(policy.get("startup_context_max_added_tokens"), DEFAULT_STARTUP_CONTEXT_MAX_ADDED_TOKENS) < 0:
        issues.append("cost_policy.startup_context_max_added_tokens must be non-negative.")
    if numeric_percent(
        policy.get("startup_context_max_added_percent"),
        DEFAULT_STARTUP_CONTEXT_MAX_ADDED_PERCENT,
    ) < 0:
        issues.append("cost_policy.startup_context_max_added_percent must be non-negative.")
    review_loop = policy.get("review_loop")
    if not isinstance(review_loop, dict):
        issues.append("cost_policy.review_loop must be configured.")
        review_loop = {}
    for key in ("max_units", "max_estimated_tokens", "max_elapsed_ms", "max_hunks_per_batch"):
        if not isinstance(review_loop.get(key), int) or int(review_loop.get(key, 0)) <= 0:
            issues.append(f"cost_policy.review_loop.{key} must be a positive integer.")
    delegation_gates = policy.get("delegation_gates")
    gate = (
        delegation_gates.get("delegation-balanced-v1")
        if isinstance(delegation_gates, dict)
        else None
    )
    gate_path = "cost_policy.delegation_gates.delegation-balanced-v1"
    if not isinstance(gate, dict):
        issues.append(f"{gate_path} must be configured.")
    else:
        if gate.get("quality_noninferior") is not True:
            issues.append(f"{gate_path}.quality_noninferior must be true.")
        wall_time = gate.get("minimum_median_wall_time_improvement_percent")
        if not isinstance(wall_time, (int, float)) or isinstance(wall_time, bool) or wall_time < 20:
            issues.append(
                f"{gate_path}.minimum_median_wall_time_improvement_percent must be at least 20."
            )
        token_increase = gate.get("maximum_median_provider_token_increase_percent")
        if (
            not isinstance(token_increase, (int, float))
            or isinstance(token_increase, bool)
            or token_increase < 0
            or token_increase > 25
        ):
            issues.append(
                f"{gate_path}.maximum_median_provider_token_increase_percent must be from 0 to 25."
            )
        trials = gate.get("minimum_trials_per_arm")
        if not isinstance(trials, int) or isinstance(trials, bool) or trials < 3:
            issues.append(f"{gate_path}.minimum_trials_per_arm must be at least 3.")
        maximum_tokens = gate.get("maximum_tokens_per_trial")
        if (
            not isinstance(maximum_tokens, int)
            or isinstance(maximum_tokens, bool)
            or maximum_tokens <= 0
            or maximum_tokens > 80000
        ):
            issues.append(f"{gate_path}.maximum_tokens_per_trial must be from 1 to 80000.")
        maximum_seconds = gate.get("maximum_seconds_per_trial")
        if (
            not isinstance(maximum_seconds, int)
            or isinstance(maximum_seconds, bool)
            or maximum_seconds <= 0
            or maximum_seconds > 600
        ):
            issues.append(f"{gate_path}.maximum_seconds_per_trial must be from 1 to 600.")
        if gate.get("required_token_provenance") != "provider_telemetry":
            issues.append(
                f"{gate_path}.required_token_provenance must be 'provider_telemetry'."
            )
        if gate.get("fallback") != "single-agent":
            issues.append(f"{gate_path}.fallback must be 'single-agent'.")
    warm_batch = policy.get("warm_server_batch")
    if not isinstance(warm_batch, dict):
        issues.append("cost_policy.warm_server_batch must be configured.")
    else:
        if not warm_batch.get("enabled"):
            issues.append("cost_policy.warm_server_batch.enabled must be true.")
        if not isinstance(warm_batch.get("min_items"), int) or int(warm_batch.get("min_items", 0)) < 2:
            issues.append("cost_policy.warm_server_batch.min_items must be at least 2.")
        if not warm_batch.get("auto_shutdown"):
            issues.append("cost_policy.warm_server_batch.auto_shutdown must be true.")
        if not warm_batch.get("schema_validation_required"):
            issues.append("cost_policy.warm_server_batch.schema_validation_required must be true.")
    if not configured_paths(policy, "always_loaded_files", LOW_CONTEXT_FILES):
        issues.append("cost_policy.always_loaded_files must name at least one low-context path.")
    routes = task_routes(policy)
    missing_routes = sorted(REQUIRED_TASK_ROUTES - set(routes))
    if missing_routes:
        issues.append("cost_policy.task_routes missing: " + ", ".join(missing_routes))
    known_tasks = local_ai_tasks(config)
    for route_id, route in sorted(routes.items()):
        use_cases = route.get("local_ai_use_cases")
        if not isinstance(use_cases, list) or not use_cases:
            issues.append(f"cost_policy.task_routes.{route_id}.local_ai_use_cases must be a non-empty list.")
            continue
        unknown = sorted(str(item) for item in use_cases if str(item) not in known_tasks)
        if unknown:
            issues.append(f"cost_policy.task_routes.{route_id} uses unknown local AI tasks: {', '.join(unknown)}")
        fallback = str(route.get("paid_model_fallback", "")).lower()
        if route.get("prefer") == "paid":
            issues.append(f"cost_policy.task_routes.{route_id}.prefer must not be paid.")
        if "primary" in fallback:
            issues.append(f"cost_policy.task_routes.{route_id}.paid_model_fallback must not be primary/default.")
    for budget_ref in sorted(REQUIRED_PHASE_BUDGETS):
        issue = str(phase_budget_resolution(policy, budget_ref)["issue"])
        if issue and issue not in issues:
            issues.append(issue)
    if not low_context.get("within_budget", True):
        issues.append(
            "always-loaded context exceeds cost_policy.context.always_loaded.budget_tokens "
            f"({low_context.get('estimated_tokens')} > {low_context.get('budget_tokens')})."
        )
    if not beginner_context.get("within_budget", True):
        issues.append(
            "beginner-loaded context exceeds cost_policy.context.beginner.budget_tokens "
            f"({beginner_context.get('estimated_tokens')} > {beginner_context.get('budget_tokens')})."
        )
    return issues


def cost_policy_report(
    root: Path,
    *,
    workflow_name: str | None = None,
    phase: str | None = None,
    task: str | None = None,
    compact: bool = False,
) -> dict[str, Any]:
    config, config_error = load_local_ai_config(root)
    policy, policy_error = load_cost_policy(root)
    policy_valid = not policy_error
    if not policy_valid:
        # Reports remain renderable for diagnosis, but invalid configured
        # policy is never run through the private flat projection validator.
        # That would leak obsolete internal labels and obscure the canonical
        # v2 validation error.
        policy = default_cost_policy()
    low_context = low_context_report(
        root,
        int_field(policy.get("always_loaded_budget_tokens"), 3500),
        configured_paths(policy, "always_loaded_files", LOW_CONTEXT_FILES),
    )
    beginner_context = low_context_report(
        root,
        int_field(policy.get("beginner_loaded_budget_tokens"), 5000),
        configured_paths(policy, "beginner_loaded_files", BEGINNER_CONTEXT_FILES),
    )
    issues = validate_policy(policy, config, low_context, beginner_context) if policy_valid else []
    if config_error:
        issues.append(f"{LOCAL_AI_CONFIG_PATH} could not be loaded: {config_error}")
    if policy_error and "missing cost_policy" not in policy_error:
        issues.append(policy_error)

    routes = task_routes(policy)
    if task:
        routes = {key: value for key, value in routes.items() if key == task}
        if not routes:
            issues.append(f"cost policy task route not found: {task}")
    phase_rows = workflow_phase_rows(root, policy, workflow_name)
    if phase:
        phase_rows = [row for row in phase_rows if row.get("phase") == phase or row.get("category") == phase]
        if not phase_rows:
            issues.append(f"phase or phase category not found: {phase}")

    diff_estimate = changed_diff_estimate(root)
    guidance_savings = guidance_savings_report(root, policy)
    if policy_error:
        guidance_savings["budget_source"] = "fallback-invalid"
        guidance_savings["budget_issue"] = policy_error
    elif not (root / CONFIG_PATH).is_file():
        guidance_savings["budget_source"] = "default-missing"
    token_savings = token_savings_report(policy, low_context, beginner_context, diff_estimate, guidance_savings)
    default_guidance = guidance_savings.get("default_context") if isinstance(guidance_savings.get("default_context"), dict) else {}
    broad_guidance = guidance_savings.get("broad_baseline") if isinstance(guidance_savings.get("broad_baseline"), dict) else {}
    if default_guidance.get("missing"):
        issues.append(
            "default guidance packet is missing required files: "
            + ", ".join(str(item) for item in default_guidance.get("missing", []))
        )
    if broad_guidance.get("missing"):
        issues.append(
            "broad guidance baseline is missing required files: "
            + ", ".join(str(item) for item in broad_guidance.get("missing", []))
        )
    if guidance_savings.get("within_absolute_budget") is not True:
        issues.append(
            "default guidance packet exceeds cost_policy.guidance.default.budget_tokens "
            f"({guidance_savings.get('default_guidance_tokens')} > {guidance_savings.get('budget_tokens')})."
        )
    if guidance_savings.get("measurable") and not guidance_savings.get("meets_minimum"):
        issues.append(
            "default guidance packet does not meet cost_policy.guidance.minimum_saved_percent "
            f"({guidance_savings.get('saved_percent_estimated')}% < {guidance_savings.get('min_saved_percent')}%)."
        )
    warm_batch = warm_server_batch_policy(policy)
    review_loop = review_loop_policy(policy)
    recommendations = recommendation_rows(policy, diff_estimate)
    ok = not issues
    report: dict[str, Any] = {
        "schema_version": 1,
        "tool": "skill-manager.cost-policy",
        "ok": ok,
        "status": "passed" if ok else "failed",
        "policy": {
            "configuration_schema_version": 2,
            "source": (
                "fallback-invalid"
                if policy_error
                else "project-policy-v2"
                if (root / CONFIG_PATH).is_file()
                else "built-in-default"
            ),
            "routing": {
                "default_paid_model_fallback": policy.get("paid_model_fallback", ""),
            },
            "review": {"loop": review_loop},
            "runtime_profile": {
                "id": policy.get("id", DEFAULT_POLICY_ID),
                "mode": policy.get("mode", ""),
                "local_ai_preferred": bool_field(policy, "prefer_local_ai_over_paid_small_models"),
                "compact_outputs": bool_field(policy, "compact_outputs_default"),
                "deterministic_checks_first": bool_field(policy, "deterministic_checks_first"),
                "search_before_read": bool_field(policy, "find_first_read_second"),
                "delta_review": bool_field(policy, "delta_only_review"),
                "stable_context_cache": bool_field(policy, "stable_context_cache"),
                "owner_first_retrieval": bool_field(policy, "find_first_read_second"),
                "token_savings_report": bool_field(policy, "token_savings_report"),
            },
        },
        "summary": {
            "issue_count": len(issues),
            "task_route_count": len(routes),
            "phase_assignment_count": len(phase_rows),
            "changed_file_count": diff_estimate.get("files", 0),
            "estimated_changed_tokens": diff_estimate.get("estimated_tokens", 0),
            "tracked_changed_file_count": diff_estimate.get("tracked_files", 0),
            "untracked_changed_file_count": diff_estimate.get("untracked_files", 0),
            "tracked_changed_tokens": diff_estimate.get("tracked_estimated_tokens", 0),
            "untracked_changed_tokens": diff_estimate.get("untracked_estimated_tokens", 0),
            "always_loaded_tokens": low_context.get("estimated_tokens", 0),
            "beginner_loaded_tokens": beginner_context.get("estimated_tokens", 0),
            "routine_skips_beginner_tokens": token_savings.get("routine_skips_beginner_tokens", 0),
            "changed_diff_tokens_over_review_budget": token_savings.get("changed_diff_tokens_over_review_budget", 0),
            "guidance_saved_tokens_estimated": guidance_savings.get("saved_tokens_estimated", 0),
            "guidance_saved_percent_estimated": guidance_savings.get("saved_percent_estimated", 0.0),
            "guidance_status": guidance_savings.get("status", "unknown"),
        },
        "issues": issues,
        "low_context": low_context,
        "beginner_context": beginner_context,
        "token_savings": token_savings,
        "guidance_savings": guidance_savings,
        "local_ai_warm_batch": warm_batch,
        "changed_diff": diff_estimate,
        "task_routes": [
            route_summary(route_id, route, compact=compact)
            for route_id, route in sorted(routes.items())
        ],
        "phase_budgets": [] if compact else phase_rows,
        "recommendations": recommendations,
        "next_command": "python -B .agents/manage.py cost-policy --check --summary --compact --format json",
    }
    if compact:
        report["low_context"].pop("files", None)
        report["beginner_context"].pop("files", None)
        if isinstance(report.get("guidance_savings"), dict):
            report["guidance_savings"].get("default_context", {}).pop("files", None)
            report["guidance_savings"].get("broad_baseline", {}).pop("files", None)
        report["phase_budgets"] = []
    return report


def route_summary(route_id: str, route: dict[str, Any], *, compact: bool) -> dict[str, Any]:
    row = {
        "id": route_id,
        "prefer": route.get("prefer", ""),
        "local_ai_use_cases": route.get("local_ai_use_cases", []),
        "max_context_tokens": route.get("max_context_tokens", 0),
        "paid_model_fallback": route.get("paid_model_fallback", ""),
        "authoritative_evidence": route.get("authoritative_evidence", ""),
    }
    if not compact:
        row["fallback"] = route.get("fallback", "")
    return row


def recommendation_rows(policy: dict[str, Any], diff_estimate: dict[str, Any]) -> list[dict[str, Any]]:
    routes = task_routes(policy)
    rows = [
        {
            "id": "phase-local-context",
            "action": "Load phase packet, touched files, latest validation, and blockers before broader docs.",
            "local_ai": "handoff-draft only for evidence shaping",
            "commands": ["python -B .agents/manage.py workflow context --name <workflow> --run-id <run-id> --write"],
        },
        {
            "id": "find-first-read-second",
            "action": "Run exact search first and read only matching files or snippets.",
            "local_ai": "inventory-summary after deterministic evidence exists",
            "commands": ["rg -n \"<pattern>\" <scoped-paths>"],
        },
        {
            "id": "deterministic-first",
            "action": "Run format/build/test/lint before asking any model to explain failures.",
            "local_ai": "failure-cluster after command output exists",
            "commands": ["python -B .agents/manage.py what-now --from-command \"python -B .agents/manage.py check\""],
        },
    ]
    warm_batch = warm_server_batch_policy(policy)
    if warm_batch.get("enabled"):
        rows.append(
            {
                "id": "warm-server-batch",
                "action": (
                    f"When {warm_batch['min_items']} or more local text items need model work, "
                    "send them in one local-ai task command so the existing llama-server batch path stays warm and then shuts down."
                ),
                "local_ai": ", ".join(warm_batch.get("prefer_for_tasks", [])),
                "commands": [
                    "python -B .agents/manage.py local-ai task --task changed-files-summary --input <file-1> --input <file-2> --json",
                ],
            }
        )
    if int(diff_estimate.get("estimated_tokens", 0) or 0) > int(routes.get("review", {}).get("max_context_tokens", 5000) or 5000):
        rows.append(
            {
                "id": "delta-summary-required",
                "action": "Changed diff estimate exceeds review route budget; summarize changed files before paid review.",
                "local_ai": "changed-files-summary",
                "commands": [
                    "python -B .agents/manage.py changed-evidence --write evidence/changed",
                    "python -B .agents/manage.py local-ai task --task changed-files-summary --input evidence/changed/changed-files.md --json",
                ],
            }
        )
    return rows


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cost Policy",
        "",
        f"- Status: {report.get('status')}",
        f"- Local first: {str(report.get('policy', {}).get('runtime_profile', {}).get('local_ai_preferred')).lower()}",
        f"- Paid fallback: {report.get('policy', {}).get('routing', {}).get('default_paid_model_fallback', '')}",
        f"- Always-loaded estimate: {report.get('summary', {}).get('always_loaded_tokens', 0)} tokens",
        f"- Beginner-loaded estimate: {report.get('summary', {}).get('beginner_loaded_tokens', 0)} tokens",
        f"- Routine skips beginner docs: {report.get('summary', {}).get('routine_skips_beginner_tokens', 0)} tokens per normal start",
        f"- Guidance savings estimate: {report.get('summary', {}).get('guidance_saved_tokens_estimated', 0)} tokens "
        f"({report.get('summary', {}).get('guidance_saved_percent_estimated', 0.0)}%)",
        f"- Changed diff estimate: {report.get('summary', {}).get('estimated_changed_tokens', 0)} tokens",
        f"- Changed diff over review budget: {report.get('summary', {}).get('changed_diff_tokens_over_review_budget', 0)} tokens",
    ]
    token_savings = report.get("token_savings", {}) if isinstance(report.get("token_savings"), dict) else {}
    controls = token_savings.get("savings_controls", {}) if isinstance(token_savings.get("savings_controls"), dict) else {}
    if controls:
        lines.extend(["", "## Savings Controls", ""])
        for key, value in controls.items():
            lines.append(f"- `{key}`: {str(bool(value)).lower()}")
    warm_batch = report.get("local_ai_warm_batch", {}) if isinstance(report.get("local_ai_warm_batch"), dict) else {}
    if warm_batch:
        lines.extend(["", "## Warm Server Batch", ""])
        lines.append(
            f"- Enabled: {str(bool(warm_batch.get('enabled'))).lower()}, "
            f"minimum items: {warm_batch.get('min_items', 2)}, "
            f"auto-shutdown: {str(bool(warm_batch.get('auto_shutdown'))).lower()}."
        )
    issues = report.get("issues", []) if isinstance(report.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in issues)
    lines.extend(["", "## Task Routes", ""])
    for row in report.get("task_routes", []):
        if not isinstance(row, dict):
            continue
        local_ai = ", ".join(str(item) for item in row.get("local_ai_use_cases", []))
        lines.append(
            f"- `{row.get('id')}`: prefer `{row.get('prefer')}`, local AI `{local_ai}`, "
            f"paid fallback `{row.get('paid_model_fallback')}`."
        )
    lines.extend(["", "## Recommendations", ""])
    for row in report.get("recommendations", []):
        if isinstance(row, dict):
            commands = row.get("commands", [])
            if isinstance(commands, list):
                command_text = " ".join(f"`{command}`" for command in commands if str(command).strip())
            else:
                command_text = ""
            suffix = f" Commands: {command_text}." if command_text else ""
            lines.append(f"- {row.get('action')} Local AI: `{row.get('local_ai')}`.{suffix}")
    return "\n".join(lines) + "\n"


def cost_policy_command(args: Any, root: Path) -> int:
    report = cost_policy_report(
        root,
        workflow_name=getattr(args, "workflow_name", None),
        phase=getattr(args, "phase", None),
        task=getattr(args, "task", None),
        compact=bool(getattr(args, "compact", False)),
    )
    if getattr(args, "summary", False) or getattr(args, "compact", False):
        report = {
            "schema_version": report["schema_version"],
            "tool": report["tool"],
            "ok": report["ok"],
            "status": report["status"],
            "summary": report["summary"],
            "issues": report["issues"],
            "policy": report["policy"],
            "token_savings": report["token_savings"],
            "guidance_savings": report["guidance_savings"],
            "local_ai_warm_batch": report["local_ai_warm_batch"],
            "recommendations": report["recommendations"],
            "next_command": report["next_command"],
        }
    if getattr(args, "output_format", "markdown") == "json":
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        print(render_markdown(report))
    if getattr(args, "check", False) and not report.get("ok"):
        return 1
    return 0
