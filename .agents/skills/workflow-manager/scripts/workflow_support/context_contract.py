"""Authoritative context-packet v3 schema."""

from __future__ import annotations

import copy
import re
from typing import Any


def _array(items: dict[str, Any] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "array"}
    if items is not None:
        schema["items"] = items
    return schema


def _object(
    properties: dict[str, Any],
    *,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


STRING = {"type": "string"}
BOOLEAN = {"type": "boolean"}
INTEGER = {"type": "integer"}
NUMBER = {"type": "number"}
STRING_ARRAY = _array(STRING)


FILE_ESTIMATE = _object(
    {
        "path": STRING,
        "tokens_estimated": INTEGER,
        "exists": BOOLEAN,
        "bytes": INTEGER,
        "chars": INTEGER,
    },
    required=("path", "tokens_estimated"),
)

CHECK = _object(
    {
        "name": STRING,
        "ok": BOOLEAN,
        "limit": NUMBER,
        "actual": NUMBER,
        "minimum": NUMBER,
        "minimum_raw_tokens": INTEGER,
        "applies": BOOLEAN,
        "file_count": INTEGER,
        "packet_tokens": INTEGER,
        "must_open_tokens": INTEGER,
        "budget_ref_valid": BOOLEAN,
        "issue": STRING,
        "remaining_margin_tokens": INTEGER,
    },
    required=("name", "ok"),
)

COMMAND = _object(
    {
        "command": STRING,
        "ok": BOOLEAN,
        "status": STRING,
        "returncode": {},
        "elapsed_seconds": {},
        "evidence_path": STRING,
    },
    required=("command", "ok", "status"),
)

CONTEXT_SOURCE = _object(
    {
        "id": STRING,
        "artifact_role": STRING,
        "load_policy": {"type": "string", "enum": ["must_open", "handle_only"]},
        "critical_category": STRING,
        "budget_ref": STRING,
        "preserve_coordinates": BOOLEAN,
        "declared": STRING,
        "files": _array(FILE_ESTIMATE),
        "tokens_estimated": INTEGER,
    },
    required=(
        "id",
        "artifact_role",
        "load_policy",
        "critical_category",
        "budget_ref",
        "preserve_coordinates",
        "declared",
        "files",
        "tokens_estimated",
    ),
)


def _execution_profile_schema() -> dict[str, Any]:
    declared_profile_properties = {
        "profile_id": STRING,
        "prompt_adapter": STRING,
        "context_budget": STRING,
        "context_budget_ref": STRING,
        "budget_tokens": INTEGER,
        "budget_source": STRING,
        "budget_issue": STRING,
        "effective_context_tokens": INTEGER,
        "remaining_margin_tokens": INTEGER,
        "within_budget": BOOLEAN,
        "context_measurement": STRING,
        "tool_policy": STRING,
        "expected_output": STRING,
        "validation_gate": STRING,
        "route_set": STRING,
        "model_target": STRING,
        "deliberation_tier": STRING,
        "declared_host_surface": STRING,
        "declared_model_provider": STRING,
        "declared_model": STRING,
        "declared_deliberation_tier": STRING,
        "profile_purpose": STRING,
        "instruction_header": STRING_ARRAY,
        "endpoint_status": {
            "type": "string",
            "enum": [
                "attested-primary",
                "attested-alternate",
                "active-model-fallback",
                "attested-host-only",
                "attested-model-only",
                "unattested-active",
            ],
        },
        "capability_status": {
            "type": "string",
            "enum": ["attested", "partial", "unavailable"],
        },
        "effective_execution_mode": {
            "type": "string",
            "enum": ["declared-endpoint", "serial-active-model"],
        },
        "observed_host_surface": STRING,
        "observed_model_provider": STRING,
        "observed_model": STRING,
        "observed_deliberation": STRING,
        "observed_capabilities": STRING_ARRAY,
        "host_observation_source": STRING,
        "model_observation_source": STRING,
        "observation_evidence_path": STRING,
        "fallback_reason": STRING,
        "prompt_overlay": _object(
            {
                "id": STRING,
                "version": INTEGER,
                "generation": STRING,
                "promotion_state": STRING,
                "instructions": STRING_ARRAY,
                "source_refs": STRING_ARRAY,
                "delivery_directive": STRING,
            },
            required=(
                "id",
                "version",
            ),
        ),
        "surface_adapter": _object(
            {
                "id": STRING,
                "host_surface": STRING,
                "instruction_surfaces": STRING_ARRAY,
                "delivery_directive": STRING,
                "available_orchestration_mode": STRING,
                "orchestration_mode": STRING,
                "effective_orchestration_mode": STRING,
                "continuation_mode": STRING,
                "cache_mode": STRING,
                "enabled_optimizations": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "blocked_optimizations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"id": STRING, "reason": STRING},
                        "required": ["id", "reason"],
                    },
                },
            },
            required=(
                "id",
                "host_surface",
                "available_orchestration_mode",
                "orchestration_mode",
                "effective_orchestration_mode",
                "continuation_mode",
                "cache_mode",
            ),
        ),
    }
    observed_runtime_fields = {
        "observed_host_surface",
        "observed_model_provider",
        "observed_model",
        "observed_deliberation",
        "observed_capabilities",
        "host_observation_source",
        "model_observation_source",
        "observation_evidence_path",
    }
    declared_profile_required = (
        "profile_id",
        "route_set",
        "prompt_adapter",
        "context_budget",
        "context_budget_ref",
        "budget_tokens",
        "budget_source",
        "effective_context_tokens",
        "remaining_margin_tokens",
        "within_budget",
        "tool_policy",
        "expected_output",
        "validation_gate",
        "instruction_header",
        "endpoint_status",
        "capability_status",
        "effective_execution_mode",
        "fallback_reason",
        "prompt_overlay",
        "surface_adapter",
    )
    return {
        "oneOf": [
            _object(
                {
                    "status": STRING,
                    "phase": STRING,
                    "profile_id": STRING,
                },
                required=("status", "phase"),
            ),
            _object(
                declared_profile_properties,
                required=declared_profile_required,
            ),
        ]
    }


def _definitions() -> dict[str, Any]:
    return {
        "executionProfile": _execution_profile_schema(),
        "instructionContext": _object(
            {
                "status": STRING,
                "path": STRING,
                "instructions_sha256": STRING,
                "always_load": STRING,
                "stop_rules": STRING,
                "completion_contract": STRING,
                "current_phase": STRING,
                "current_phase_instructions": STRING,
                "requires_full_instructions": BOOLEAN,
                "issues": STRING_ARRAY,
            },
            required=(
                "status",
                "path",
                "instructions_sha256",
                "always_load",
                "stop_rules",
                "completion_contract",
                "current_phase",
                "current_phase_instructions",
                "requires_full_instructions",
                "issues",
            ),
        ),
        "scope": _object(
            {
                "in_scope": STRING_ARRAY,
                "out_of_scope": STRING_ARRAY,
                "assumptions": STRING_ARRAY,
                "ticket_scope_recorded": BOOLEAN,
                "run_status": STRING,
            },
            required=(
                "in_scope",
                "out_of_scope",
                "assumptions",
                "ticket_scope_recorded",
                "run_status",
            ),
        ),
        "workItemSummary": _object(
            {
                "type": STRING,
                "observed_behavior": STRING,
                "expected_behavior": STRING,
                "reproduction": STRING,
                "regression_proof": STRING,
                "execution_evidence": STRING,
                "acceptance_criteria": STRING_ARRAY,
                "acceptance_mapping": STRING,
            },
            required=("type", "execution_evidence"),
        ),
        "documentationDelta": _object(
            {
                "schema_version": INTEGER,
                "tool": STRING,
                "status": STRING,
                "changed_docs": STRING_ARRAY,
                "required_updates": STRING_ARRAY,
                "no_doc_impact_reason": STRING,
                "frontmatter_checked": BOOLEAN,
                "map_checked": BOOLEAN,
                "evidence_paths": STRING_ARRAY,
                "issues": STRING_ARRAY,
                "paths": _object(
                    {"json": STRING, "markdown": STRING},
                    required=("json", "markdown"),
                ),
            },
            required=(
                "schema_version",
                "tool",
                "status",
                "changed_docs",
                "required_updates",
                "no_doc_impact_reason",
                "frontmatter_checked",
                "map_checked",
                "evidence_paths",
                "issues",
                "paths",
            ),
        ),
        "validationSummary": _object(
            {
                "external_validation_status": STRING,
                "commands": _array(COMMAND),
                "skipped": _array(),
                "blocked": _array(),
                "failed": _array(),
                "validation_file_count": INTEGER,
                "validation_files": _array(FILE_ESTIMATE),
            },
            required=(
                "external_validation_status",
                "commands",
                "skipped",
                "blocked",
                "failed",
                "validation_file_count",
                "validation_files",
            ),
        ),
        "guidanceSavings": _object(
            {
                "use_by_default": BOOLEAN,
                "status": STRING,
                "measurable": BOOLEAN,
                "complete": BOOLEAN,
                "budget_tokens": INTEGER,
                "budget_source": STRING,
                "budget_issue": STRING,
                "within_absolute_budget": BOOLEAN,
                "meets_minimum": BOOLEAN,
                "min_saved_percent": NUMBER,
                "token_counter": STRING,
                "provenance": STRING,
                "scope": STRING,
                "default_guidance_tokens": INTEGER,
                "broad_baseline_tokens": INTEGER,
                "saved_tokens_estimated": INTEGER,
                "saved_percent_estimated": NUMBER,
                "default_missing_count": INTEGER,
                "baseline_missing_count": INTEGER,
                "policy_note": STRING,
            },
            required=(
                "use_by_default",
                "status",
                "measurable",
                "complete",
                "budget_tokens",
                "budget_source",
                "budget_issue",
                "within_absolute_budget",
                "meets_minimum",
                "min_saved_percent",
                "token_counter",
                "provenance",
                "scope",
                "default_guidance_tokens",
                "broad_baseline_tokens",
                "saved_tokens_estimated",
                "saved_percent_estimated",
                "default_missing_count",
                "baseline_missing_count",
            ),
        ),
        "coordinateCloset": _object(
            {
                "status": STRING,
                "paths": STRING_ARRAY,
                "hashes": STRING_ARRAY,
                "ids": STRING_ARRAY,
                "ports": STRING_ARRAY,
                "env": STRING_ARRAY,
                "source_count": INTEGER,
            },
            required=(
                "status",
                "paths",
                "hashes",
                "ids",
                "ports",
                "env",
                "source_count",
            ),
        ),
        "tokenEstimates": _object(
            {
                "method": STRING,
                "serialization_alignment": STRING,
                "raw_context_tokens_estimated": INTEGER,
                "packet_tokens_estimated": INTEGER,
                "compact_packet_tokens_estimated": INTEGER,
                "estimated_tokens_saved": INTEGER,
                "raw_context_file_count": INTEGER,
                "raw_context_files": _array(FILE_ESTIMATE),
                "validation_tokens_estimated": INTEGER,
                "must_open_file_count": INTEGER,
                "must_open_files": _array(FILE_ESTIMATE),
                "must_open_tokens_estimated": INTEGER,
                "effective_load_tokens_estimated": INTEGER,
            },
            required=(
                "method",
                "serialization_alignment",
                "raw_context_tokens_estimated",
                "packet_tokens_estimated",
                "compact_packet_tokens_estimated",
                "estimated_tokens_saved",
                "raw_context_file_count",
                "validation_tokens_estimated",
                "must_open_file_count",
                "must_open_tokens_estimated",
                "effective_load_tokens_estimated",
            ),
        ),
        "contextBudget": _object(
            {
                "status": STRING,
                "packet_token_limit": INTEGER,
                "minimum_savings_raw_tokens": INTEGER,
                "minimum_savings_ratio": NUMBER,
                "packet_only_ratio": NUMBER,
                "effective_load_ratio": NUMBER,
                "savings_ratio": NUMBER,
                "effective_load_tokens_estimated": INTEGER,
                "effective_load_limit": INTEGER,
                "issues": _array(STRING),
                "checks": _array(CHECK),
            },
            required=(
                "status",
                "packet_token_limit",
                "minimum_savings_raw_tokens",
                "minimum_savings_ratio",
                "packet_only_ratio",
                "effective_load_ratio",
                "savings_ratio",
                "effective_load_tokens_estimated",
                "effective_load_limit",
                "issues",
                "checks",
            ),
        ),
        "qualityGate": _object(
            {
                "schema_version": INTEGER,
                "tool": STRING,
                "ok": BOOLEAN,
                "status": STRING,
                "check_count": INTEGER,
                "failed_count": INTEGER,
                "failed_checks": _array(),
                "checks": _array(),
            },
            required=(
                "schema_version",
                "tool",
                "ok",
                "status",
                "check_count",
                "failed_count",
                "failed_checks",
                "checks",
            ),
        ),
        "checkState": _object(
            {
                "existing": BOOLEAN,
                "fresh": BOOLEAN,
                "markdown_exists": BOOLEAN,
            },
            required=("existing", "fresh", "markdown_exists"),
        ),
    }


def context_packet_schema() -> dict[str, Any]:
    """Generate the strict public schema from the one owner model in this module."""

    properties = {
        "schema_version": {"type": "integer", "const": 3},
        "tool": {"type": "string", "const": "workflow-manager.context-packet"},
        "ok": BOOLEAN,
        "status": STRING,
        "workflow": STRING,
        "run_id": STRING,
        "run_path": STRING,
        "current_phase": STRING,
        "phase_status": STRING,
        "next_action": STRING,
        "execution_profile": {"$ref": "#/$defs/executionProfile"},
        "instruction_context": {"$ref": "#/$defs/instructionContext"},
        "scope": {"$ref": "#/$defs/scope"},
        "work_item_summary": {"$ref": "#/$defs/workItemSummary"},
        "documentation_delta": {"$ref": "#/$defs/documentationDelta"},
        "validation_summary": {"$ref": "#/$defs/validationSummary"},
        "guidance_savings": {"$ref": "#/$defs/guidanceSavings"},
        "decisions": _array(),
        "evidence_handles": STRING_ARRAY,
        "context_sources": _array(CONTEXT_SOURCE),
        "required_next_context": STRING_ARRAY,
        "context_packet_paths": _object(
            {"json": STRING, "markdown": STRING},
            required=("json", "markdown"),
        ),
        "unsupported_claims": _array(),
        "advisories": STRING_ARRAY,
        "issues": STRING_ARRAY,
        "next_command": STRING,
        "coordinate_closet": {"$ref": "#/$defs/coordinateCloset"},
        "token_estimates": {"$ref": "#/$defs/tokenEstimates"},
        "context_budget": {"$ref": "#/$defs/contextBudget"},
        "written": STRING_ARRAY,
        "quality_gate": {"$ref": "#/$defs/qualityGate"},
        "check": {"$ref": "#/$defs/checkState"},
        "existing_packet_path": STRING,
        "existing_markdown_path": STRING,
    }
    required = (
        "schema_version",
        "tool",
        "ok",
        "status",
        "workflow",
        "run_id",
        "run_path",
        "current_phase",
        "phase_status",
        "next_action",
        "execution_profile",
        "instruction_context",
        "scope",
        "work_item_summary",
        "documentation_delta",
        "validation_summary",
        "guidance_savings",
        "decisions",
        "evidence_handles",
        "required_next_context",
        "context_packet_paths",
        "unsupported_claims",
        "issues",
        "next_command",
        "token_estimates",
        "context_budget",
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://schemas.skills-harness.invalid/context-packet-v3.schema.json",
        "title": "Workflow Context Packet v3",
        "$defs": _definitions(),
        **_object(properties, required=required),
    }


def _matches_type(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return True


def _resolve_ref(schema: dict[str, Any], root_schema: dict[str, Any]) -> dict[str, Any]:
    reference = schema.get("$ref")
    if not isinstance(reference, str):
        return schema
    prefix = "#/$defs/"
    if not reference.startswith(prefix):
        return {}
    value = root_schema.get("$defs", {}).get(reference[len(prefix) :])
    return value if isinstance(value, dict) else {}


def _validate(
    schema: dict[str, Any],
    value: object,
    path: str,
    errors: list[str],
    root_schema: dict[str, Any],
) -> None:
    schema = _resolve_ref(schema, root_schema)
    all_variants = schema.get("allOf")
    if isinstance(all_variants, list):
        for variant in all_variants:
            if isinstance(variant, dict):
                _validate(variant, value, path, errors, root_schema)
    conditional = schema.get("if")
    if isinstance(conditional, dict):
        conditional_errors: list[str] = []
        _validate(conditional, value, path, conditional_errors, root_schema)
        branch = schema.get("then") if not conditional_errors else schema.get("else")
        if isinstance(branch, dict):
            _validate(branch, value, path, errors, root_schema)
    any_variants = schema.get("anyOf")
    if isinstance(any_variants, list) and any_variants:
        variant_errors: list[list[str]] = []
        for variant in any_variants:
            if not isinstance(variant, dict):
                continue
            candidate_errors: list[str] = []
            _validate(variant, value, path, candidate_errors, root_schema)
            variant_errors.append(candidate_errors)
        if variant_errors and not any(not item for item in variant_errors):
            errors.extend(min(variant_errors, key=len))
    variants = schema.get("oneOf")
    if isinstance(variants, list) and variants:
        variant_errors: list[list[str]] = []
        matched = 0
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            candidate_errors: list[str] = []
            _validate(variant, value, path, candidate_errors, root_schema)
            variant_errors.append(candidate_errors)
            if not candidate_errors:
                matched += 1
        if matched == 1:
            return
        if matched > 1:
            errors.append(f"{path} matches more than one declared shape.")
            return
        if variant_errors:
            errors.extend(min(variant_errors, key=len))
        else:
            errors.append(f"{path} does not match a declared shape.")
        return
    expected = schema.get("type")
    if isinstance(expected, str) and not _matches_type(value, expected):
        errors.append(f"{path} must be a {expected}.")
        return
    if "const" in schema and (
        value != schema["const"]
        or (isinstance(value, bool) != isinstance(schema["const"], bool))
    ):
        errors.append(f"{path} must equal {schema['const']!r}.")
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path} must be one of the declared values.")
    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            errors.append(f"{path} must contain at least {minimum_length} characters.")
        maximum_length = schema.get("maxLength")
        if isinstance(maximum_length, int) and len(value) > maximum_length:
            errors.append(f"{path} must contain at most {maximum_length} characters.")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            errors.append(f"{path} must match the declared pattern.")
    if isinstance(value, list):
        if schema.get("uniqueItems") is True:
            for index, item in enumerate(value):
                if any(item == previous for previous in value[:index]):
                    errors.append(f"{path} must contain unique items.")
                    break
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate(items, item, f"{path}[{index}]", errors, root_schema)
    if isinstance(value, dict):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        required = schema.get("required")
        if isinstance(required, list):
            for name in required:
                if name not in value:
                    errors.append(f"{path}.{name} is required.")
        for name, item in value.items():
            child = properties.get(name)
            if isinstance(child, dict):
                _validate(child, item, f"{path}.{name}", errors, root_schema)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{name} is not an allowed field.")


def validate_context_packet(
    packet: object,
    *,
    schema: dict[str, Any] | None = None,
) -> list[str]:
    """Validate a context packet with the same owner model used for generation."""

    generated = context_packet_schema() if schema is None else copy.deepcopy(schema)
    errors: list[str] = []
    _validate(generated, packet, "context-packet", errors, generated)
    return errors
