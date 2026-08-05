#!/usr/bin/env python3
"""Project-owned configuration for human-tunable repository policies."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from project_policy_contract_v2 import (
    INSTANCE_SCHEMA,
    SCHEMA_VERSION as PROJECT_POLICY_SCHEMA_VERSION,
    legacy_cost_policy_from_v2,
    owner_defaults,
    output_profiles,
    project_policy_schema,
    v2_validation_issue_from_legacy,
    v2_cost_policy_from_v1,
    validate_semantics,
)

PROJECT_POLICY_PATH = ".agents/project-policy.json"
LOCAL_AI_PATH = ".agents/local-ai.json"
SCHEMA_VERSION = PROJECT_POLICY_SCHEMA_VERSION
REPORT_SCHEMA_VERSION = 1
WARNING_ACTIONS = {"off", "warning", "error"}
WARNING_MARKER = "[policy:"


def _spec(
    default: object,
    description: str,
    *,
    unit: str,
    minimum: int | None = None,
    maximum: int | None = None,
    choices: tuple[str, ...] | None = None,
) -> dict[str, object]:
    return {
        "default": default,
        "description": description,
        "unit": unit,
        "minimum": minimum,
        "maximum": maximum,
        "choices": choices,
    }


POLICY_SPECS: dict[str, dict[str, object]] = {
    "limits.agents.warn_chars": _spec(3500, "Warn when AGENTS.md exceeds this normalized character count.", unit="characters", minimum=1),
    "limits.agents.fail_chars": _spec(4000, "Fail when AGENTS.md exceeds this normalized character count.", unit="characters", minimum=1),
    "limits.agents.warn_tokens": _spec(1200, "Warn when AGENTS.md exceeds this estimated token count.", unit="tokens", minimum=1),
    "limits.skill.warn_words": _spec(1200, "Warn when SKILL.md exceeds this word count.", unit="words", minimum=1),
    "limits.skill.fail_words": _spec(2000, "Fail when SKILL.md exceeds this word count unless a size exception is declared.", unit="words", minimum=1),
    "limits.skill.warn_tokens": _spec(2400, "Warn when trigger-loaded SKILL.md guidance exceeds this estimated token count.", unit="tokens", minimum=1),
    "limits.skill.name_max_chars": _spec(63, "Maximum portable skill name length.", unit="characters", minimum=1, maximum=63),
    "limits.skill.name_min_terms": _spec(2, "Minimum recommended kebab-case terms in a skill name.", unit="terms", minimum=1),
    "limits.skill.name_max_terms": _spec(5, "Maximum recommended kebab-case terms in a skill name.", unit="terms", minimum=1),
    "limits.skill.candidate_description_min_chars": _spec(80, "Minimum description length that receives the full candidate-quality score.", unit="characters", minimum=1),
    "limits.skill.candidate_description_max_chars": _spec(700, "Maximum description length that receives the full candidate-quality score.", unit="characters", minimum=1),
    "limits.skill.description_process_warn_words": _spec(28, "Minimum description word count before process-summary heuristics apply.", unit="words", minimum=1),
    "limits.skill.description_process_warn_terms": _spec(2, "Process-term count that contributes to a routing-description warning.", unit="terms", minimum=1),
    "limits.skill.description_process_warn_actions": _spec(5, "Action-term count that triggers a routing-description warning.", unit="terms", minimum=1),
    "limits.skill.description_process_warn_punctuation": _spec(4, "Comma/semicolon count that triggers a routing-description warning.", unit="characters", minimum=1),
    "limits.skill.asset_max_bytes": _spec(5 * 1024 * 1024, "Maximum accepted size for one skill asset.", unit="bytes", minimum=1, maximum=5 * 1024 * 1024),
    "limits.workflow.warn_tokens": _spec(500, "Warn when a workflow entry point exceeds this estimated token count.", unit="tokens", minimum=1),
    "limits.workflow.mermaid_warn_tokens": _spec(700, "Warn threshold for workflow entry points containing Mermaid diagrams.", unit="tokens", minimum=1),
    "limits.workflow.entry_warn_words": _spec(260, "Warn when a workflow entry point exceeds this word count.", unit="words", minimum=1),
    "limits.workflow.contract_warn_words": _spec(750, "Warn when a workflow module contract exceeds this word count.", unit="words", minimum=1),
    "limits.workflow.instructions_warn_words": _spec(1000, "Warn when workflow instructions exceed this word count.", unit="words", minimum=1),
    "limits.workflow.aggregate_warn_words": _spec(2200, "Warn when the core files of one workflow exceed this aggregate word count.", unit="words", minimum=1),
    "limits.workflow.profile_text_chars": _spec(220, "Maximum worker-profile expected-output text length.", unit="characters", minimum=40, maximum=2000),
    "limits.workflow.context_packet_command_chars": _spec(180, "Maximum command text retained in workflow context packets.", unit="characters", minimum=40, maximum=2000),
    "limits.workflow.context_packet_token_limit": _spec(2500, "Maximum estimated tokens retained in one workflow context packet.", unit="tokens", minimum=500, maximum=20000),
    "limits.workflow.context_packet_command_limit": _spec(3, "Maximum command rows retained in one workflow context packet.", unit="commands", minimum=1, maximum=20),
    "limits.workflow.context_packet_evidence_handle_limit": _spec(8, "Maximum evidence handles retained in one workflow context packet.", unit="paths", minimum=1, maximum=50),
    "limits.workflow.context_packet_raw_file_limit": _spec(10, "Maximum raw source-file estimates retained in workflow context accounting.", unit="files", minimum=1, maximum=100),
    "limits.workflow.context_packet_validation_file_limit": _spec(3, "Maximum validation-file estimates retained in one workflow context packet.", unit="files", minimum=1, maximum=50),
    "limits.workflow.context_packet_min_savings_percent": _spec(60, "Minimum context reduction required when the context packet savings gate applies.", unit="percent", minimum=0, maximum=100),
    "limits.workflow.context_packet_min_savings_raw_tokens": _spec(3125, "Minimum raw-context size before the context packet savings gate applies.", unit="tokens", minimum=1),
    "limits.workflow.context_reason_chars": _spec(240, "Maximum fallback reason or delivery directive retained in compact workflow context.", unit="characters", minimum=40, maximum=2000),
    "limits.workflow.runtime_identity_chars": _spec(160, "Maximum workflow, run, or phase identifier length in runtime observations.", unit="characters", minimum=40, maximum=500),
    "limits.workflow.runtime_evidence_path_chars": _spec(500, "Maximum evidence-path text length in runtime observations.", unit="characters", minimum=80, maximum=4000),
    "limits.workflow.runtime_model_id_chars": _spec(160, "Maximum model identifier length in runtime observations.", unit="characters", minimum=40, maximum=500),
    "limits.workflow.runtime_deliberation_chars": _spec(80, "Maximum observed-deliberation text length in runtime observations.", unit="characters", minimum=20, maximum=200),
    "limits.workflow_module.warn_tokens": _spec(1700, "Warn when a workflow module contract exceeds this estimated token count.", unit="tokens", minimum=1),
    "limits.navigation.warn_tokens": _spec(1200, "Warn when the route-first navigation map exceeds this estimated token count.", unit="tokens", minimum=1),
    "limits.navigation.map_warn_words": _spec(1400, "Warn when the generated navigation map exceeds this word count.", unit="words", minimum=1),
    "limits.navigation.scan_warn_entries": _spec(2500, "Warn when a navigation scan exceeds this entry count.", unit="entries", minimum=1),
    "limits.navigation.relationship_max_entries": _spec(6000, "Maximum relationships retained in generated navigation data.", unit="entries", minimum=100, maximum=20000),
    "limits.navigation.source_snippet_chars": _spec(180, "Maximum source-focus evidence snippet length.", unit="characters", minimum=40, maximum=2000),
    "limits.navigation.relationship_evidence_chars": _spec(160, "Maximum relationship evidence text retained per navigation row.", unit="characters", minimum=40, maximum=2000),
    "limits.navigation.project_context_placeholder_chars": _spec(120, "Maximum placeholder line excerpt in generated project context.", unit="characters", minimum=40, maximum=1000),
    "limits.routing.warn_chars": _spec(10_000, "Warn when a generated routing index exceeds this character count.", unit="characters", minimum=1),
    "limits.routing.warn_rows": _spec(75, "Warn when a generated routing index exceeds this row count.", unit="rows", minimum=1),
    "limits.routing.entry_summary_chars": _spec(120, "Maximum generated routing-table summary length.", unit="characters", minimum=20),
    "limits.script.warn_lines": _spec(1200, "Warn when a Python implementation file exceeds this line count.", unit="lines", minimum=1),
    "limits.script.public_command_warn_lines": _spec(1200, "Warn when a public command file exceeds this line count.", unit="lines", minimum=1),
    "limits.script.warn_functions": _spec(40, "Warn when a Python file exceeds this top-level function count.", unit="functions", minimum=1),
    "limits.script.warn_bytes": _spec(70_000, "Warn when a Python file exceeds this UTF-8 byte count.", unit="bytes", minimum=1),
    "limits.script.warn_top_level_files": _spec(16, "Warn when a skill script folder exceeds this direct file count.", unit="files", minimum=1),
    "limits.documentation.warn_words": _spec(1800, "Warn when an active Markdown guide exceeds this word count.", unit="words", minimum=1),
    "limits.compatibility.skill_warn_words": _spec(1000, "Warn when a portable SKILL.md exceeds this word count.", unit="words", minimum=1),
    "limits.compatibility.skill_warn_lines": _spec(180, "Warn when a portable SKILL.md exceeds this line count.", unit="lines", minimum=1),
    "limits.feedback.text_chars": _spec(1200, "Maximum normalized feedback or correction text length.", unit="characters", minimum=80),
    "limits.feedback.actor_chars": _spec(120, "Maximum caller or failure-type text length in feedback records.", unit="characters", minimum=20),
    "limits.feedback.fact_chars": _spec(500, "Maximum compact failure-fact text length.", unit="characters", minimum=80),
    "limits.feedback.target_chars": _spec(160, "Maximum feedback target or source-tool text length.", unit="characters", minimum=20),
    "limits.feedback.command_chars": _spec(400, "Maximum command text length stored in feedback records.", unit="characters", minimum=40),
    "limits.feedback.digest_chars": _spec(80, "Maximum external digest text length stored in feedback records.", unit="characters", minimum=16),
    "limits.diagnostics.callback_output_chars": _spec(400, "Maximum unstructured callback output retained in health errors.", unit="characters", minimum=80),
    "limits.context.validation_command_chars": _spec(180, "Maximum validation command length retained in compact context packets.", unit="characters", minimum=40),
    "limits.review.validation_command_chars": _spec(360, "Maximum validation command display length in review packets.", unit="characters", minimum=80),
    "limits.review.syntax_command_chars": _spec(260, "Length above which syntax-check command displays use a compact canonical form.", unit="characters", minimum=80),
    "limits.dashboard.path_chars": _spec(160, "Maximum path length retained inline in compact dashboard rows.", unit="characters", minimum=40),
    "limits.output.evidence_snippet_chars": _spec(240, "Maximum source/evidence snippet length in compact reports.", unit="characters", minimum=40),
    "limits.output.finding_snippet_chars": _spec(160, "Maximum proof and optimization finding snippet length.", unit="characters", minimum=40),
    "limits.output.failure_excerpt_chars": _spec(500, "Maximum failed-command excerpt length in compact reports.", unit="characters", minimum=80),
    "limits.output.capture_line_chars": _spec(260, "Maximum captured diagnostic line length.", unit="characters", minimum=80),
    "limits.output.command_label_chars": _spec(60, "Maximum filesystem-safe captured command label length.", unit="characters", minimum=20),
    "limits.output.distilled_chars": _spec(1600, "Maximum distilled command-output text length.", unit="characters", minimum=160),
    "limits.skill.duplicate_description_chars": _spec(160, "Description prefix length used for deterministic candidate duplicate keys.", unit="characters", minimum=40),
    "limits.organization.review_direct_files": _spec(10, "Direct-file count that triggers an organization review advisory.", unit="files", minimum=1),
    "limits.optimization.skill_detail_words": _spec(2500, "Skill word count above which optimization guidance recommends moving detail into docs.", unit="words", minimum=1),
    "limits.import.large_file_warn_bytes": _spec(5 * 1024 * 1024, "Warn when an imported candidate file exceeds this size.", unit="bytes", minimum=1),
    "warnings.default_action": _spec("warning", "Action for end-user advisories without a more specific warning policy ID.", unit="action"),
    "warnings.health.agents.characters": _spec("warning", "Action for the AGENTS.md character advisory.", unit="action"),
    "warnings.health.agents.tokens": _spec("warning", "Action for the AGENTS.md token advisory.", unit="action"),
    "warnings.health.skill.words": _spec("warning", "Action for the SKILL.md word advisory.", unit="action"),
    "warnings.health.skill.tokens": _spec("warning", "Action for the SKILL.md token advisory.", unit="action"),
    "warnings.health.workflow.tokens": _spec("warning", "Action for workflow entry-point token advisories.", unit="action"),
    "warnings.workflow.context-budget": _spec("warning", "Action for workflow word-budget advisories.", unit="action"),
    "warnings.health.workflow-module.tokens": _spec("warning", "Action for workflow module token advisories.", unit="action"),
    "warnings.health.navigation.tokens": _spec("warning", "Action for navigation token advisories.", unit="action"),
    "warnings.navigation.map-size": _spec("warning", "Action for generated navigation map-size advisories.", unit="action"),
    "warnings.health.routing.characters": _spec("warning", "Action for routing character advisories.", unit="action"),
    "warnings.health.routing.rows": _spec("warning", "Action for routing row advisories.", unit="action"),
    "warnings.health.script.lines": _spec("warning", "Action for script line-count advisories.", unit="action"),
    "warnings.health.script.functions": _spec("warning", "Action for script function-count advisories.", unit="action"),
    "warnings.health.script.bytes": _spec("warning", "Action for script byte-count advisories.", unit="action"),
    "warnings.health.script.top-level-files": _spec("warning", "Action for script-folder file-count advisories.", unit="action"),
    "warnings.health.documentation.words": _spec("warning", "Action for active documentation word-count advisories.", unit="action"),
    "warnings.compatibility.skill.words": _spec("warning", "Action for portable skill word-count advisories.", unit="action"),
    "warnings.compatibility.skill.lines": _spec("warning", "Action for portable skill line-count advisories.", unit="action"),
}

_OWNER_DEFAULT_DESCRIPTIONS = {
    "owner_defaults.skill_manager.claude_adapter.name_only_saved_tokens": ("Minimum estimated token saving before compact Claude adapters switch to name-only mode.", "tokens", 1, None),
    "owner_defaults.skill_manager.claude_adapter.name_only_skill_count": ("Minimum skill count before compact Claude adapters switch to name-only mode.", "skills", 1, None),
    "owner_defaults.skill_manager.claude_adapter.context_window_tokens": ("Portable context-window estimate used in generated adapter savings reports.", "tokens", 1, None),
    "owner_defaults.skill_manager.capability_audit.low_context_token_target": ("Target maximum for low-context startup guidance.", "tokens", 1, None),
    "owner_defaults.skill_manager.capability_audit.fast_daily_target_ms": ("Target maximum for the fast daily path.", "milliseconds", 1, None),
    "owner_defaults.skill_manager.optimization.lesson_promotion_min_count": ("Repeated lesson count required before promotion guidance.", "occurrences", 1, None),
    "owner_defaults.skill_manager.review_cost.extra_output_tokens": ("Assumed extra output tokens for a review pass.", "tokens", 0, None),
    "owner_defaults.skill_manager.review_cost.output_price_multiplier": ("Assumed output-to-input token price multiplier.", "multiplier", 1, None),
    "owner_defaults.skill_manager.review_cost.visible_history_entries": ("Review-cost history rows retained in user-visible trends.", "entries", 1, 200),
    "owner_defaults.workflow_manager.context_evidence.top_k": ("Default bounded workflow context-evidence result count.", "results", 1, 100),
}
for _path, (_description, _unit, _minimum, _maximum) in _OWNER_DEFAULT_DESCRIPTIONS.items():
    _parts = _path.split(".")[1:]
    _current: object = owner_defaults()
    for _part in _parts:
        _current = _current[_part]  # type: ignore[index]
    POLICY_SPECS[_path] = _spec(_current, _description, unit=_unit, minimum=_minimum, maximum=_maximum)

for _profile, _profile_values in output_profiles().items():
    for _field, _default in _profile_values.items():
        POLICY_SPECS[f"output_profiles.{_profile}.{_field}"] = _spec(
            _default,
            f"Reusable {_profile.replace('_', ' ')} output {_field.replace('_', ' ')}.",
            unit="characters" if _field.endswith("chars") else "lines",
            minimum=1,
        )

for _profile, _values in owner_defaults()["repo_navigation"]["briefing"]["profiles"].items():
    for _field, _default in _values.items():
        POLICY_SPECS[f"owner_defaults.repo_navigation.briefing.profiles.{_profile}.{_field}"] = _spec(
            _default, f"Repo-navigation {_profile} briefing {_field.replace('_', ' ')}.", unit="count", minimum=1
        )
POLICY_SPECS["owner_defaults.repo_navigation.briefing.default_profile"] = _spec(
    "normal", "Default repo-navigation briefing profile.", unit="choice", choices=("short", "normal", "deep")
)

DEFAULT_LATENCY_BUDGETS_MS = {
    "status-fast": 4000,
    "status-full": 15000,
    "startup-context": 2500,
    "check-changed": 75000,
    "changed-context": 3000,
    "changed-evidence": 3000,
    "next-action": 25000,
    "review-loop": 180000,
    "review-next": 12000,
    "review-autopilot": 180000,
    "smoke-workflows": 45000,
    "context-use-check": 40000,
    "finish": 180000,
    "command-budget-check": 180000,
}
DEFAULT_COMPONENT_LATENCY_BUDGETS_MS = {"startup-context": 1500}
DEFAULT_OUTPUT_BUDGETS_TOKENS = {
    "status-fast": 2000,
    "status-full": 3500,
    "startup-context": 1600,
    "check-changed": 2400,
    "changed-context": 1600,
    "changed-evidence": 1400,
    "next-action": 1400,
    "review-loop": 1400,
    "review-next": 900,
    "review-autopilot": 1400,
    "smoke-workflows": 1800,
    "context-use-check": 1200,
    "finish": 1800,
    "command-budget-check": 2200,
}

for _command, _value in DEFAULT_LATENCY_BUDGETS_MS.items():
    POLICY_SPECS[f"commands.latency_ms.{_command}"] = _spec(
        _value, f"Latency advisory budget for `{_command}`.", unit="milliseconds", minimum=1
    )
for _command, _value in DEFAULT_COMPONENT_LATENCY_BUDGETS_MS.items():
    POLICY_SPECS[f"commands.component_latency_ms.{_command}"] = _spec(
        _value, f"Slow-component latency budget for `{_command}`.", unit="milliseconds", minimum=1
    )
for _command, _value in DEFAULT_OUTPUT_BUDGETS_TOKENS.items():
    POLICY_SPECS[f"commands.output_tokens.{_command}"] = _spec(
        _value, f"Compact output advisory budget for `{_command}`.", unit="tokens", minimum=1
    )


def project_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for current in (candidate, *candidate.parents):
        if (current / ".agents" / "manage.py").is_file():
            return current
    return Path.cwd().resolve()


def default_value(path: str) -> object:
    return copy.deepcopy(POLICY_SPECS[path]["default"])


def default_policy_document() -> dict[str, object]:
    from repo_support import repo_cost_policy

    document: dict[str, Any] = {"$schema": INSTANCE_SCHEMA, "schema_version": SCHEMA_VERSION}
    for path, spec in sorted(POLICY_SPECS.items()):
        _nested_set(document, path.split("."), copy.deepcopy(spec["default"]))
    document["cost_policy"] = v2_cost_policy_from_v1(repo_cost_policy.default_cost_policy())
    return document


def generated_schema() -> dict[str, Any]:
    return project_policy_schema(default_policy_document(), POLICY_SPECS)


def _read_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}, ""
    except OSError as exc:
        return {}, str(exc)
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON: {exc}"
    if not isinstance(value, dict):
        return {}, "root must be an object"
    return value, ""


def _flatten(document: object, prefix: str = "") -> dict[str, object]:
    if not isinstance(document, dict):
        return {prefix: document} if prefix else {}
    flattened: dict[str, object] = {}
    for key, value in document.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flattened.update(_flatten(value, path))
        else:
            flattened[path] = value
    return flattened


def _policy_values(document: dict[str, Any]) -> dict[str, object]:
    flattened = _flatten(document)
    return {path: flattened[path] for path in POLICY_SPECS if path in flattened}


def validate_values(values: object, *, require_complete: bool = True) -> list[str]:
    if not isinstance(values, dict):
        return ["project policy values must be an object keyed by policy path."]
    issues: list[str] = []
    if require_complete:
        missing = sorted(set(POLICY_SPECS) - set(values))
        if missing:
            issues.append("missing policy paths: " + ", ".join(missing))
    for raw_path, value in sorted(values.items(), key=lambda item: str(item[0])):
        path = str(raw_path)
        spec = POLICY_SPECS.get(path)
        if spec is None:
            issues.append(f"unknown policy path: {path}")
            continue
        unit = str(spec.get("unit", ""))
        if unit == "action":
            if value not in WARNING_ACTIONS:
                issues.append(f"{path} must be one of: {', '.join(sorted(WARNING_ACTIONS))}.")
            continue
        if unit == "choice":
            choices = tuple(str(item) for item in (spec.get("choices") or ()))
            if not isinstance(value, str) or value not in choices:
                issues.append(f"{path} must be one of: {', '.join(choices)}.")
            continue
        minimum_raw = spec.get("minimum")
        minimum = int(minimum_raw) if isinstance(minimum_raw, int) else 1
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            issues.append(f"{path} must be an integer at or above {minimum}.")
            continue
        maximum = spec.get("maximum")
        if isinstance(maximum, int) and value > maximum:
            issues.append(f"{path} must be an integer at or below {maximum}.")
    if not issues:
        if int(values["limits.agents.warn_chars"]) >= int(values["limits.agents.fail_chars"]):
            issues.append("limits.agents.warn_chars must be lower than limits.agents.fail_chars.")
        if int(values["limits.skill.warn_words"]) >= int(values["limits.skill.fail_words"]):
            issues.append("limits.skill.warn_words must be lower than limits.skill.fail_words.")
        if int(values["limits.skill.candidate_description_min_chars"]) > int(
            values["limits.skill.candidate_description_max_chars"]
        ):
            issues.append(
                "limits.skill.candidate_description_min_chars must be at or below "
                "limits.skill.candidate_description_max_chars."
            )
        if int(values["limits.skill.name_min_terms"]) > int(values["limits.skill.name_max_terms"]):
            issues.append("limits.skill.name_min_terms must be at or below limits.skill.name_max_terms.")
    return issues


def _validate_cost_shape(policy: object) -> list[str]:
    from repo_support import repo_cost_policy

    if not isinstance(policy, dict):
        return ["cost_policy must be an object."]
    expected = _flatten(v2_cost_policy_from_v1(repo_cost_policy.default_cost_policy()))
    actual = _flatten(policy)
    issues: list[str] = []
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing:
        issues.append("missing cost_policy paths: " + ", ".join(f"cost_policy.{item}" for item in missing))
    if extra:
        issues.append("unknown cost_policy paths: " + ", ".join(f"cost_policy.{item}" for item in extra))
    for path in sorted(set(expected) & set(actual)):
        default = expected[path]
        value = actual[path]
        if isinstance(default, bool):
            valid = isinstance(value, bool)
        elif isinstance(default, int):
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif isinstance(default, float):
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif isinstance(default, str):
            valid = isinstance(value, str) and bool(value)
        elif isinstance(default, list):
            valid = (
                isinstance(value, list)
                and bool(value)
                and all(isinstance(item, str) and bool(item) for item in value)
                and len(value) == len(set(value))
            )
        else:
            valid = isinstance(value, type(default))
        if not valid:
            if isinstance(default, list):
                issues.append(f"cost_policy.{path} must be a non-empty list of unique non-empty strings.")
            elif isinstance(default, str):
                issues.append(f"cost_policy.{path} must be a non-empty string.")
            else:
                issues.append(f"cost_policy.{path} must have the same JSON type as its default value.")
    return issues


def load_project_policy(root: Path) -> tuple[dict[str, Any], list[str], bool]:
    path = root / PROJECT_POLICY_PATH
    document, read_error = _read_json(path)
    if read_error:
        return default_policy_document(), [f"{PROJECT_POLICY_PATH}: {read_error}"], path.exists()
    if not document:
        return default_policy_document(), [], False
    return load_project_policy_from_document(document, root)


def effective_values(root: Path) -> tuple[dict[str, object], list[str], set[str]]:
    document, issues, exists = load_project_policy(root)
    values = {path: copy.deepcopy(spec["default"]) for path, spec in POLICY_SPECS.items()}
    configured: set[str] = set(POLICY_SPECS) if exists and not issues else set()
    configured_values = _policy_values(document)
    if not issues:
        values.update(copy.deepcopy(configured_values))
    return values, issues, configured


def value(root: Path, path: str) -> object:
    values, issues, _configured = effective_values(root)
    if issues:
        raise ValueError("invalid project policy: " + "; ".join(issues))
    return copy.deepcopy(values.get(path, default_value(path)))


def int_value(root: Path, path: str) -> int:
    return int(value(root, path))


def warning_action(root: Path, warning_id: str) -> str:
    path = f"warnings.{warning_id}"
    if path not in POLICY_SPECS:
        return str(value(root, "warnings.default_action"))
    return str(value(root, path))


def skill_word_status(root: Path, words: int, *, size_exception: bool = False) -> str:
    if words > int_value(root, "limits.skill.fail_words") and not size_exception:
        return "fail"
    if words <= int_value(root, "limits.skill.warn_words"):
        return "ok"
    action = warning_action(root, "health.skill.words")
    return "ok" if action == "off" else "fail" if action == "error" else "warn"


def tagged_warning(warning_id: str, message: str) -> str:
    return f"[policy:{warning_id}] {message}"


def warning_id(message: str) -> str:
    if message.startswith(WARNING_MARKER) and "] " in message:
        return message[len(WARNING_MARKER): message.index("] ")]
    return ""


def classify_warnings(root: Path, messages: list[str]) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    for message in messages:
        identifier = warning_id(message)
        action = warning_action(root, identifier)
        if action == "off":
            continue
        if action == "error":
            errors.append(message)
        else:
            warnings.append(message)
    return sorted(set(warnings)), sorted(set(errors))


def _atomic_write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    text = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _parse_cli_value(raw: str) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _cost_catalog(root: Path) -> tuple[dict[str, dict[str, object]], list[str]]:
    defaults = default_policy_document()["cost_policy"]
    document, project_issues, project_exists = load_project_policy(root)
    configured = document.get("cost_policy", defaults)
    catalog: dict[str, dict[str, object]] = {}

    def walk(prefix: str, default: object, actual: object) -> None:
        if isinstance(default, dict):
            actual_dict = actual if isinstance(actual, dict) else {}
            for key, item in sorted(default.items()):
                walk(f"{prefix}.{key}" if prefix else key, item, actual_dict.get(key))
            return
        catalog[f"cost_policy.{prefix}"] = {
            "default": copy.deepcopy(default),
            "effective": copy.deepcopy(actual if actual is not None else default),
            "source": PROJECT_POLICY_PATH if project_exists and not project_issues else "built-in",
            "description": "Portable local-first context and task-routing policy.",
            "unit": "cost-policy",
        }

    walk("", defaults, configured)
    return catalog, project_issues if project_exists else []


def policy_catalog(root: Path) -> tuple[dict[str, dict[str, object]], list[str]]:
    values, issues, configured = effective_values(root)
    catalog = {
        path: {
            **copy.deepcopy(spec),
            "effective": copy.deepcopy(values[path]),
            "source": PROJECT_POLICY_PATH if path in configured else "built-in",
        }
        for path, spec in sorted(POLICY_SPECS.items())
    }
    cost, cost_issues = _cost_catalog(root)
    catalog.update(cost)
    return catalog, [*issues, *cost_issues]


def _nested_set(document: dict[str, Any], path: list[str], value: object) -> None:
    current = document
    for part in path[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[path[-1]] = value


def _nested_delete(document: dict[str, Any], path: list[str]) -> bool:
    current = document
    parents: list[tuple[dict[str, Any], str]] = []
    for part in path[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            return False
        parents.append((current, part))
        current = child
    if path[-1] not in current:
        return False
    del current[path[-1]]
    for parent, key in reversed(parents):
        child = parent.get(key)
        if isinstance(child, dict) and not child:
            del parent[key]
    return True


def refresh_project_policy(root: Path) -> tuple[bool, str]:
    """Add newly registered defaults without changing existing project choices."""

    target = root / PROJECT_POLICY_PATH
    current, read_error = _read_json(target)
    if read_error:
        return False, f"{PROJECT_POLICY_PATH}: {read_error}"
    if not current:
        _atomic_write_json(target, default_policy_document())
        return True, "created complete project policy"
    if current.get("schema_version") == 1:
        return migrate_project_policy(root)
    if current.get("schema_version") != SCHEMA_VERSION:
        return False, f"schema_version must be {SCHEMA_VERSION}; run `policy migrate` for a v1 document."
    if "overrides" in current:
        return False, "obsolete sparse overrides are not supported; initialize the complete v2 policy document."
    defaults = default_policy_document()
    merged = 0
    for path, default in _flatten(defaults).items():
        if path in {"$schema", "schema_version"}:
            continue
        if path not in _flatten(current):
            _nested_set(current, path.split("."), copy.deepcopy(default))
            merged += 1
    _document, issues, _exists = load_project_policy_from_document(current, root)
    if issues:
        return False, "; ".join(issues)
    _atomic_write_json(target, current)
    return True, f"added {merged} newly registered policy values"


def load_project_policy_from_document(
    document: dict[str, Any], root: Path | None = None,
) -> tuple[dict[str, Any], list[str], bool]:
    issues: list[str] = []
    if document.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"schema_version must be {SCHEMA_VERSION}.")
    if document.get("$schema") != INSTANCE_SCHEMA:
        issues.append(f"$schema must be {INSTANCE_SCHEMA!r}.")
    expected_top_level = set(default_policy_document())
    extra = sorted(set(document) - expected_top_level)
    if extra:
        issues.append("unsupported top-level keys: " + ", ".join(extra))
    missing_top_level = sorted(expected_top_level - set(document))
    if missing_top_level:
        issues.append("missing top-level keys: " + ", ".join(missing_top_level))
    issues.extend(validate_values(_policy_values(document)))
    issues.extend(_validate_cost_shape(document.get("cost_policy")))
    issues.extend(validate_semantics(document))
    if not issues and isinstance(document.get("cost_policy"), dict):
        from repo_support import repo_cost_policy

        legacy = legacy_cost_policy_from_v2(document["cost_policy"], repo_cost_policy.default_cost_policy())
        local_ai: dict[str, Any] = {}
        if root is not None:
            local_ai, local_ai_error = _read_json(root / LOCAL_AI_PATH)
            if local_ai_error:
                issues.append(f"{LOCAL_AI_PATH}: {local_ai_error}")
        if not issues:
            validation_root = root or project_root()
            low_context = repo_cost_policy.low_context_report(
                validation_root,
                int(legacy["always_loaded_budget_tokens"]),
                list(legacy["always_loaded_files"]),
            )
            beginner = repo_cost_policy.low_context_report(
                validation_root,
                int(legacy["beginner_loaded_budget_tokens"]),
                list(legacy["beginner_loaded_files"]),
            )
            issues.extend(
                v2_validation_issue_from_legacy(issue)
                for issue in repo_cost_policy.validate_policy(legacy, local_ai, low_context, beginner)
            )
    return document, issues, True


def migrate_v1_document(document: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Translate exactly one complete v1 document; normal readers remain v2-only."""

    if document.get("schema_version") != 1:
        return {}, ["policy migrate requires schema_version 1."]
    required = {"limits", "warnings", "commands", "cost_policy"}
    missing = sorted(required - set(document))
    extra = sorted(set(document) - (required | {"schema_version"}))
    if missing:
        return {}, ["v1 policy is missing top-level keys: " + ", ".join(missing)]
    if extra:
        return {}, ["v1 policy has unsupported top-level keys: " + ", ".join(extra)]
    old_flat = _flatten(document)
    allowed_general = {
        path
        for path in POLICY_SPECS
        if path.startswith(("limits.", "warnings.", "commands."))
        and path != "limits.import.large_file_warn_bytes"
    }
    actual_general = {
        path for path in old_flat if path.startswith(("limits.", "warnings.", "commands."))
    }
    unknown_general = sorted(actual_general - allowed_general)
    missing_general = sorted(allowed_general - actual_general)
    if unknown_general:
        return {}, ["v1 policy has unknown paths: " + ", ".join(unknown_general)]
    if missing_general:
        return {}, ["v1 policy is incomplete: " + ", ".join(missing_general)]
    from repo_support import repo_cost_policy

    expected_cost = set(_flatten(repo_cost_policy.default_cost_policy()))
    actual_cost = {
        path.removeprefix("cost_policy.")
        for path in old_flat
        if path.startswith("cost_policy.")
    }
    unknown_cost = sorted(actual_cost - expected_cost)
    missing_cost = sorted(expected_cost - actual_cost)
    if unknown_cost:
        return {}, ["v1 cost_policy has unknown paths: " + ", ".join(unknown_cost)]
    if missing_cost:
        return {}, ["v1 cost_policy is incomplete: " + ", ".join(missing_cost)]
    defaults = default_policy_document()
    migrated = copy.deepcopy(defaults)
    for path in POLICY_SPECS:
        if path in old_flat:
            _nested_set(migrated, path.split("."), copy.deepcopy(old_flat[path]))
    if not isinstance(document.get("cost_policy"), dict):
        return {}, ["v1 cost_policy must be an object."]
    migrated["cost_policy"] = v2_cost_policy_from_v1(document["cost_policy"])
    # Newly introduced v2 leaves keep their v2 defaults; every v1 choice is preserved above.
    return migrated, []


def migrate_project_policy(root: Path) -> tuple[bool, str]:
    target = root / PROJECT_POLICY_PATH
    current, error = _read_json(target)
    if error:
        return False, f"{PROJECT_POLICY_PATH}: {error}"
    candidate, issues = migrate_v1_document(current)
    if issues:
        return False, "; ".join(issues)
    _document, validation, _exists = load_project_policy_from_document(candidate, root)
    if validation:
        return False, "; ".join(validation)
    _atomic_write_json(target, candidate)
    return True, "migrated project policy from schema v1 to v2"


def configure_project_value(root: Path, path: str, raw_value: object | None, *, reset: bool = False) -> tuple[bool, str]:
    defaults = default_policy_document()
    default_flat = _flatten(defaults)
    is_cost_path = path.startswith("cost_policy.") and path in default_flat
    if path not in POLICY_SPECS and not is_cost_path:
        return False, f"unknown policy path: {path}"
    document, issues, exists = load_project_policy(root)
    if issues:
        return False, "; ".join(issues)
    if not exists:
        document = defaults
    if reset:
        _nested_set(document, path.split("."), copy.deepcopy(default_flat[path]))
    else:
        _nested_set(document, path.split("."), raw_value)
    _validated, validation, _exists = load_project_policy_from_document(document, root)
    if validation:
        return False, "; ".join(validation)
    document["schema_version"] = SCHEMA_VERSION
    _atomic_write_json(root / PROJECT_POLICY_PATH, document)
    return True, "reset" if reset else "configured"


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# Project Policy", "", f"- Status: {report.get('status')}", f"- Project policy: `{PROJECT_POLICY_PATH}`"]
    if report.get("path"):
        lines.append(f"- Path: `{report['path']}`")
    if "effective" in report:
        lines.extend([f"- Effective: `{json.dumps(report['effective'], ensure_ascii=False)}`", f"- Source: `{report.get('source')}`"])
    rows = report.get("policies") if isinstance(report.get("policies"), list) else []
    if rows:
        lines.extend(["", "| Path | Effective | Source | Description |", "|---|---:|---|---|"])
        for item in rows:
            lines.append(
                f"| `{item['path']}` | `{json.dumps(item['effective'], ensure_ascii=False)}` | "
                f"`{item['source']}` | {item['description']} |"
            )
    for issue in report.get("issues", []):
        lines.append(f"- Issue: {issue}")
    return "\n".join(lines) + "\n"


def policy_command(args: Any, root: Path) -> int:
    action = str(getattr(args, "policy_action", "show") or "show")
    path = str(getattr(args, "path", "") or "").strip()
    output_format = str(getattr(args, "output_format", "markdown"))
    catalog, issues = policy_catalog(root)
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "project_policy_schema_version": SCHEMA_VERSION,
        "tool": "skill-manager.project-policy",
        "action": action,
        "status": "passed",
        "ok": not issues,
        "issues": issues,
        "project_policy_path": PROJECT_POLICY_PATH,
    }
    if action == "refresh":
        ok, message = refresh_project_policy(root)
        report.update(
            ok=ok,
            status="written" if ok else "failed",
            message=message,
            issues=[] if ok else [message],
        )
        if ok:
            catalog, issues = policy_catalog(root)
    elif action == "migrate":
        ok, message = migrate_project_policy(root)
        report.update(ok=ok, status="written" if ok else "failed", message=message, issues=[] if ok else [message])
        if ok:
            catalog, issues = policy_catalog(root)
    elif action in {"set", "reset"}:
        if not path:
            report.update(ok=False, status="failed", issues=["a policy path is required."])
        elif action == "set" and getattr(args, "value", None) is None:
            report.update(ok=False, status="failed", issues=["a JSON value is required for set."])
        else:
            parsed = _parse_cli_value(str(getattr(args, "value", ""))) if action == "set" else None
            ok, message = configure_project_value(root, path, parsed, reset=action == "reset")
            report.update(ok=ok, status="written" if ok else "failed", path=path, message=message, issues=[] if ok else [message])
            if ok:
                catalog, issues = policy_catalog(root)
    elif action == "init":
        target = root / PROJECT_POLICY_PATH
        if target.exists():
            report.update(status="unchanged", message="project policy already exists")
        else:
            _atomic_write_json(target, default_policy_document())
            report.update(status="written", message="created complete project policy")
    elif action in {"get", "explain"}:
        item = catalog.get(path)
        if not path or item is None:
            report.update(ok=False, status="failed", issues=[f"unknown policy path: {path or '<missing>'}"])
        else:
            report.update(path=path, **copy.deepcopy(item))
    elif action in {"show", "list", "validate"}:
        prefix = path or str(getattr(args, "section", "") or "")
        selected = [
            {"path": key, **copy.deepcopy(item)}
            for key, item in sorted(catalog.items())
            if not prefix or key == prefix or key.startswith(prefix + ".")
        ]
        if action != "validate":
            report["policies"] = selected
        report["summary"] = {
            "policy_count": len(catalog),
            "selected_count": len(selected),
            "issue_count": len(issues),
        }
        if prefix and not selected:
            report.update(ok=False, status="failed", issues=[f"unknown policy section or path: {prefix}"])
        elif issues:
            report.update(ok=False, status="failed")
    else:
        report.update(ok=False, status="failed", issues=[f"unsupported policy action: {action}"])
    if output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(render_markdown(report), end="")
    return 0 if report.get("ok") else 1
