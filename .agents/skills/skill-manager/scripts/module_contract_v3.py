#!/usr/bin/env python3
"""Authoritative ModuleContractV3 field model, runtime validator, and schema generator."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from module_command import command_argv, command_display


_UNSET = object()


@dataclass(frozen=True)
class FieldSpec:
    """One field definition used by both runtime and JSON Schema validation."""

    type_name: str
    required: bool = False
    properties: tuple[tuple[str, "FieldSpec"], ...] = ()
    items: "FieldSpec | None" = None
    additional_properties: bool | "FieldSpec" = True
    const: Any = _UNSET
    enum: tuple[Any, ...] = ()
    pattern: str = ""
    property_name_pattern: str = ""
    min_length: int | None = None
    min_items: int | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    unique_items: bool = False
    variants: tuple["FieldSpec", ...] = ()
    semantic_rules: tuple[str, ...] = ()


def string_spec(*, required: bool = False, min_length: int | None = None) -> FieldSpec:
    return FieldSpec("string", required=required, min_length=min_length)


def string_list_spec(*, required: bool = False) -> FieldSpec:
    return FieldSpec(
        "array",
        required=required,
        items=string_spec(min_length=1),
    )


def object_spec(
    *,
    required: bool = False,
    properties: tuple[tuple[str, FieldSpec], ...] = (),
    additional_properties: bool | FieldSpec = True,
    property_name_pattern: str = "",
    semantic_rules: tuple[str, ...] = (),
) -> FieldSpec:
    return FieldSpec(
        "object",
        required=required,
        properties=properties,
        additional_properties=additional_properties,
        property_name_pattern=property_name_pattern,
        semantic_rules=semantic_rules,
    )


def union_spec(*variants: FieldSpec, required: bool = False) -> FieldSpec:
    return FieldSpec("any", required=required, variants=tuple(variants))


COMMAND_EFFECT_KEYS = (
    "repository_write",
    "temporary_write",
    "network",
    "credentials",
    "install",
    "upload",
    "external_write",
)

COMMAND_EFFECTS_SPEC = FieldSpec(
    "array",
    required=True,
    items=FieldSpec("string", enum=COMMAND_EFFECT_KEYS),
    unique_items=True,
)

COMMAND_SPEC = object_spec(
    properties=(
        (
            "id",
            FieldSpec(
                "string",
                required=True,
                pattern=r"[a-z][a-z0-9-]{0,63}",
            ),
        ),
        (
            "argv",
            FieldSpec(
                "array",
                required=True,
                items=string_spec(min_length=1),
                min_items=1,
            ),
        ),
        (
            "timeout_seconds",
            FieldSpec("integer", required=True, minimum=1, maximum=3600),
        ),
        (
            "working_directory",
            FieldSpec(
                "string",
                required=True,
                enum=("repository", "module", "temporary"),
            ),
        ),
        ("effects", COMMAND_EFFECTS_SPEC),
    ),
    additional_properties=False,
)

ROUTING_SPEC = object_spec(
    properties=(
        ("terms", string_list_spec(required=True)),
        ("activation_terms", string_list_spec(required=True)),
        ("threshold", FieldSpec("integer", required=True, minimum=1)),
        ("winner_margin", FieldSpec("integer", required=True, minimum=0)),
    ),
    additional_properties=False,
)

TEMPLATE_PROFILE_SPEC = object_spec(
    properties=(
        ("template_roots", string_list_spec(required=True)),
        ("template", string_spec(min_length=1)),
    ),
    additional_properties=False,
)

TEMPLATE_LAYERS_SPEC = object_spec(
    properties=(
        ("default_template", string_spec(required=True, min_length=1)),
        ("override_roots", string_list_spec(required=True)),
        ("preset_roots", string_list_spec(required=True)),
        (
            "profiles",
            object_spec(
                required=True,
                additional_properties=TEMPLATE_PROFILE_SPEC,
                property_name_pattern=r"[a-z][a-z0-9-]{0,63}",
            ),
        ),
        (
            "priorities",
            object_spec(
                required=True,
                additional_properties=FieldSpec("integer", minimum=0),
                property_name_pattern=r"[a-z][a-z0-9-]{0,63}",
            ),
        ),
        ("conflict_policy", FieldSpec("string", required=True, const="error")),
    ),
    additional_properties=False,
)

CONTEXT_SOURCE_SPEC = object_spec(
    properties=(
        (
            "id",
            FieldSpec(
                "string",
                required=True,
                pattern=r"[a-z][a-z0-9-]{0,63}",
            ),
        ),
        ("artifact_role", string_spec(required=True, min_length=1)),
        ("path", string_spec(min_length=1)),
        ("pattern", string_spec(min_length=1)),
        (
            "load_policy",
            FieldSpec(
                "string",
                required=True,
                enum=("must_open", "handle_only"),
            ),
        ),
        ("critical_category", string_spec(required=True, min_length=1)),
        ("budget_ref", string_spec(required=True, min_length=1)),
        ("preserve_coordinates", FieldSpec("boolean", required=True)),
    ),
    additional_properties=False,
)

CONTEXT_SPEC = object_spec(
    properties=(
        (
            "budgets",
            object_spec(
                required=True,
                additional_properties=FieldSpec("integer", minimum=1),
                property_name_pattern=r"[a-z][a-z0-9-]{0,63}",
            ),
        ),
        (
            "sources",
            FieldSpec(
                "array",
                required=True,
                items=CONTEXT_SOURCE_SPEC,
                min_items=1,
            ),
        ),
    ),
    additional_properties=False,
)

DETERMINISM_SPEC = object_spec(
    properties=(
        (
            "replay_commands",
            FieldSpec(
                "array",
                required=True,
                items=FieldSpec("string", pattern=r"[a-z][a-z0-9-]{0,63}"),
                unique_items=True,
            ),
        ),
        (
            "allowed_temporary_effects",
            FieldSpec(
                "array",
                required=True,
                items=object_spec(
                    properties=(
                        ("path", string_spec(required=True, min_length=1)),
                        ("recursive", FieldSpec("boolean", required=True)),
                        (
                            "operations",
                            FieldSpec(
                                "array",
                                required=True,
                                items=FieldSpec(
                                    "string",
                                    enum=("create", "modify", "delete"),
                                ),
                                min_items=1,
                                unique_items=True,
                            ),
                        ),
                    ),
                    additional_properties=False,
                ),
            ),
        ),
        (
            "volatile_json_pointers",
            FieldSpec(
                "array",
                required=True,
                items=FieldSpec(
                    "string",
                    pattern=r"/(?:[^~/]|~[01])*(?:/(?:[^~/]|~[01])*)*",
                ),
                unique_items=True,
            ),
        ),
        (
            "environment_requirements",
            object_spec(
                required=True,
                properties=(
                    (
                        "minimum_python",
                        FieldSpec(
                            "string",
                            required=True,
                            pattern=r"[0-9]+\.[0-9]+",
                        ),
                    ),
                    (
                        "executables",
                        FieldSpec(
                            "array",
                            required=True,
                            items=FieldSpec(
                                "string",
                                pattern=r"[A-Za-z0-9][A-Za-z0-9._+-]*",
                            ),
                            unique_items=True,
                        ),
                    ),
                    (
                        "platforms",
                        FieldSpec(
                            "array",
                            required=True,
                            items=FieldSpec(
                                "string",
                                enum=("windows", "linux", "macos"),
                            ),
                            min_items=1,
                            unique_items=True,
                        ),
                    ),
                ),
                additional_properties=False,
            ),
        ),
    ),
    additional_properties=False,
)

IDENTIFIER_PATTERN = r"[a-z][a-z0-9-]{0,63}"
SNAKE_CASE_PATTERN = r"[a-z][a-z0-9_]*"
DATE_PATTERN = r"\d{4}-\d{2}-\d{2}"

COMPATIBILITY_SPEC = object_spec(
    properties=tuple(
        (
            name,
            FieldSpec("string", enum=("required", "optional", "unsupported")),
        )
        for name in ("codex", "github_copilot", "claude_code")
    ),
    additional_properties=False,
)

DEPENDENCY_SPEC = object_spec(
    properties=(
        ("name", string_spec(required=True, min_length=1)),
        ("purpose", string_spec(required=True, min_length=1)),
        ("version", string_spec(min_length=1)),
        ("optional", FieldSpec("boolean")),
    ),
    additional_properties=False,
)

RISK_SPEC = object_spec(
    required=True,
    properties=(
        ("credentials", FieldSpec("boolean", required=True)),
        ("destructive", FieldSpec("boolean", required=True)),
        ("generated_settings", FieldSpec("boolean", required=True)),
        ("installs", FieldSpec("boolean", required=True)),
        ("network", FieldSpec("boolean", required=True)),
        ("production_writes", FieldSpec("boolean", required=True)),
        ("uploads", FieldSpec("boolean", required=True)),
        (
            "profile",
            FieldSpec(
                "string",
                required=True,
                enum=(
                    "read-only",
                    "local-write",
                    "local-destructive",
                    "networked",
                    "credentialed",
                    "production-write",
                ),
            ),
        ),
    ),
    additional_properties=False,
)

EXTERNAL_ACCESS_SPEC = object_spec(
    required=True,
    properties=(
        ("source_systems", string_list_spec(required=True)),
        ("credential_expectations", string_spec(required=True, min_length=1)),
        ("data_copied_locally", string_list_spec(required=True)),
        ("attachments_retrieved", FieldSpec("boolean", required=True)),
    ),
    additional_properties=False,
)

LOCAL_AI_USE_CASE_OBJECT_SPEC = object_spec(
    properties=tuple(
        (name, string_spec(required=True, min_length=1))
        for name in (
            "id",
            "command",
            "applies_when",
            "guardrail",
            "evidence_input",
            "owner",
        )
    ),
    additional_properties=False,
)
LOCAL_AI_USE_CASE_SPEC = union_spec(
    string_spec(min_length=1),
    LOCAL_AI_USE_CASE_OBJECT_SPEC,
)
LOCAL_AI_SPEC = object_spec(
    required=True,
    properties=(
        (
            "use_cases",
            FieldSpec(
                "array",
                required=True,
                items=LOCAL_AI_USE_CASE_SPEC,
                unique_items=True,
            ),
        ),
    ),
    additional_properties=False,
)

PHASE_SPEC = object_spec(
    properties=(
        ("id", FieldSpec("string", required=True, pattern=IDENTIFIER_PATTERN)),
        ("summary", string_spec(min_length=1)),
        ("entry_checks", string_list_spec()),
        ("exit_checks", string_list_spec()),
        ("evidence", string_list_spec()),
        ("hooks", string_list_spec()),
    ),
    additional_properties=False,
)
PHASE_LIFECYCLE_SPEC = object_spec(
    properties=(
        ("events", string_list_spec(required=True)),
        ("state_fields", string_list_spec(required=True)),
        ("required_handoff_fields", string_list_spec(required=True)),
    ),
    additional_properties=False,
)

WORKER_PROFILE_SPEC = object_spec(
    properties=(
        ("purpose", string_spec(required=True, min_length=1)),
        (
            "prompt_adapter",
            FieldSpec(
                "string",
                required=True,
                enum=(
                    "evidence",
                    "planning",
                    "implementation",
                    "test-authoring",
                    "validation",
                    "handoff",
                    "general",
                ),
            ),
        ),
        (
            "context_budget",
            FieldSpec("string", required=True, enum=("lean", "standard", "expanded")),
        ),
        (
            "tool_policy",
            FieldSpec(
                "string",
                required=True,
                enum=(
                    "read-only",
                    "bounded-write",
                    "deterministic-validation",
                    "evidence-only",
                    "handoff-only",
                ),
            ),
        ),
        ("expected_output", string_spec(required=True, min_length=1)),
        (
            "validation_gate",
            FieldSpec(
                "string",
                required=True,
                enum=(
                    "record-evidence",
                    "approval-required",
                    "deterministic-checks",
                    "fresh-validation",
                    "handoff-contract",
                ),
            ),
        ),
        ("route_set", FieldSpec("string", required=True, pattern=IDENTIFIER_PATTERN)),
    ),
    additional_properties=False,
)
DELEGATION_SPEC = object_spec(
    required=True,
    properties=(
        ("schema_version", FieldSpec("integer", required=True, const=1)),
        (
            "trigger",
            FieldSpec(
                "string",
                required=True,
                const="explicit-request-or-owner-instruction",
            ),
        ),
        ("max_depth", FieldSpec("integer", required=True, const=1)),
        (
            "eligible_task_classes",
            FieldSpec(
                "array",
                required=True,
                items=FieldSpec("string", enum=("independent-read-heavy",)),
                min_items=1,
                unique_items=True,
            ),
        ),
        (
            "require_model_attestation",
            FieldSpec("boolean", required=True, const=True),
        ),
        (
            "require_complete_thread_tree",
            FieldSpec("boolean", required=True, const=True),
        ),
        (
            "economics_gate_ref",
            FieldSpec("string", required=True, pattern=IDENTIFIER_PATTERN),
        ),
        ("fallback", FieldSpec("string", required=True, const="sequential")),
    ),
    additional_properties=False,
)
WORKER_PROFILES_SPEC = object_spec(
    properties=(
        ("schema_version", FieldSpec("integer", required=True, const=1)),
        ("extends", FieldSpec("string", required=True, pattern=IDENTIFIER_PATTERN)),
        (
            "mode",
            FieldSpec(
                "string",
                required=True,
                enum=("advisory", "auto-when-supported", "manual"),
            ),
        ),
        (
            "max_parallel_workers",
            FieldSpec("integer", required=True, minimum=1, maximum=4),
        ),
        (
            "phase_assignments",
            object_spec(
                required=True,
                additional_properties=FieldSpec("string", pattern=IDENTIFIER_PATTERN),
                property_name_pattern=IDENTIFIER_PATTERN,
            ),
        ),
        (
            "task_assignments",
            object_spec(
                required=True,
                additional_properties=FieldSpec("string", pattern=IDENTIFIER_PATTERN),
                property_name_pattern=IDENTIFIER_PATTERN,
            ),
        ),
        (
            "profiles",
            object_spec(
                additional_properties=WORKER_PROFILE_SPEC,
                property_name_pattern=IDENTIFIER_PATTERN,
            ),
        ),
        ("delegation", DELEGATION_SPEC),
    ),
    additional_properties=False,
)

PARALLEL_RUNTIME_SPEC = object_spec(
    required=True,
    properties=(
        (
            "environment",
            FieldSpec(
                "string",
                required=True,
                enum=("none", "inherited-read-only", "per-worker"),
            ),
        ),
        (
            "ports",
            FieldSpec("string", required=True, enum=("none", "per-worker")),
        ),
        (
            "state_stores",
            FieldSpec("string", required=True, enum=("none", "per-worker")),
        ),
        (
            "services",
            FieldSpec("string", required=True, enum=("none", "per-worker")),
        ),
    ),
    additional_properties=False,
)
PARALLEL_PHASE_POLICY_SPEC = object_spec(
    properties=(
        (
            "mode",
            FieldSpec(
                "string",
                required=True,
                enum=("serial", "parallel-read-only", "parallel-isolated"),
            ),
        ),
        ("max_workers", FieldSpec("integer", required=True, minimum=1, maximum=4)),
        (
            "write_scopes",
            FieldSpec(
                "array",
                required=True,
                items=string_spec(min_length=1),
                unique_items=True,
            ),
        ),
        ("runtime", PARALLEL_RUNTIME_SPEC),
        (
            "provider",
            FieldSpec("string", required=True, enum=("none", "external")),
        ),
        (
            "provision_command_id",
            FieldSpec("string", pattern=IDENTIFIER_PATTERN),
        ),
        (
            "cleanup_command_id",
            FieldSpec("string", pattern=IDENTIFIER_PATTERN),
        ),
    ),
    additional_properties=False,
)
PARALLEL_SAFETY_SPEC = object_spec(
    properties=(
        ("schema_version", FieldSpec("integer", required=True, const=1)),
        ("default_mode", FieldSpec("string", required=True, const="serial")),
        (
            "phase_policies",
            object_spec(
                required=True,
                additional_properties=PARALLEL_PHASE_POLICY_SPEC,
                property_name_pattern=IDENTIFIER_PATTERN,
            ),
        ),
    ),
    additional_properties=False,
)

PROVENANCE_SPEC = object_spec(
    properties=(
        ("source", string_spec(min_length=1)),
        ("license", string_spec(min_length=1)),
        ("introduced", FieldSpec("string", pattern=DATE_PATTERN)),
        ("updated", FieldSpec("string", pattern=DATE_PATTERN)),
        ("reviewed_at", FieldSpec("string", pattern=DATE_PATTERN)),
        ("attestations", string_list_spec()),
        (
            "source_hashes",
            object_spec(additional_properties=string_spec(min_length=1)),
        ),
    ),
    additional_properties=False,
)

QUALITY_ENTRY_OBJECT_SPEC = object_spec(
    properties=(
        ("path", string_spec(required=True, min_length=1)),
        ("purpose", string_spec(min_length=1)),
    ),
    additional_properties=False,
)
QUALITY_ENTRY_SPEC = union_spec(string_spec(min_length=1), QUALITY_ENTRY_OBJECT_SPEC)
QUALITY_SPEC = object_spec(
    properties=(
        ("eval_suites", FieldSpec("array", items=QUALITY_ENTRY_SPEC)),
        ("self_tests", FieldSpec("array", items=QUALITY_ENTRY_SPEC)),
        ("eval_gap_rationale", string_spec(min_length=1)),
    ),
    additional_properties=False,
)

CONTEXT_EVIDENCE_QUERY_SPEC = object_spec(
    properties=(
        ("id", FieldSpec("string", required=True, pattern=IDENTIFIER_PATTERN)),
        ("question", string_spec(required=True, min_length=1)),
        (
            "scope",
            FieldSpec(
                "string",
                enum=("repo", "documents", "workflow-runs", "all"),
            ),
        ),
        ("required", FieldSpec("boolean")),
        ("fallback_paths", string_list_spec(required=True)),
    ),
    additional_properties=False,
)
CONTEXT_EVIDENCE_SPEC = object_spec(
    properties=(
        ("required", FieldSpec("boolean", required=True)),
        ("start_queries", FieldSpec("array", required=True, items=CONTEXT_EVIDENCE_QUERY_SPEC)),
        ("resume_queries", FieldSpec("array", required=True, items=CONTEXT_EVIDENCE_QUERY_SPEC)),
        ("finish_queries", FieldSpec("array", required=True, items=CONTEXT_EVIDENCE_QUERY_SPEC)),
    ),
    additional_properties=False,
)

TASK_SPEC = object_spec(
    properties=(
        ("id", FieldSpec("string", required=True, pattern=IDENTIFIER_PATTERN)),
        ("summary", string_spec(required=True, min_length=1)),
        ("phase", FieldSpec("string", pattern=IDENTIFIER_PATTERN)),
        ("depends_on", string_list_spec()),
    ),
    additional_properties=False,
)

HOOK_EVENTS = (
    "phase-pre",
    "phase-started",
    "phase-between",
    "phase-completed",
    "phase-blocked",
    "phase-post",
    "phase-handoff",
    "workflow-pre",
    "workflow-post",
    "run-started",
    "run-finished",
)
HOOK_SPEC = object_spec(
    properties=(
        ("id", FieldSpec("string", required=True, pattern=IDENTIFIER_PATTERN)),
        ("event", FieldSpec("string", required=True, enum=HOOK_EVENTS)),
        ("command", string_spec(required=True, min_length=1)),
        ("required", FieldSpec("boolean")),
        ("timeout_seconds", FieldSpec("integer", minimum=1, maximum=3600)),
        ("evidence_path", string_spec(min_length=1)),
    ),
    additional_properties=False,
)

INPUT_PROPERTY_SPEC = object_spec(
    properties=(
        (
            "type",
            FieldSpec(
                "string",
                required=True,
                enum=("string", "number", "boolean", "enum", "path", "list", "object"),
            ),
        ),
        ("description", string_spec(min_length=1)),
        ("values", string_list_spec()),
    ),
    additional_properties=False,
)
INPUT_SCHEMA_SPEC = object_spec(
    properties=(
        (
            "properties",
            object_spec(
                required=True,
                additional_properties=INPUT_PROPERTY_SPEC,
                property_name_pattern=SNAKE_CASE_PATTERN,
            ),
        ),
        ("required", string_list_spec(required=True)),
    ),
    additional_properties=False,
)

GATE_SPEC = object_spec(
    properties=(
        ("id", FieldSpec("string", required=True, pattern=IDENTIFIER_PATTERN)),
        (
            "type",
            FieldSpec(
                "string",
                required=True,
                enum=("approval", "clarification", "quality", "validation", "human", "policy"),
            ),
        ),
        ("summary", string_spec(required=True, min_length=1)),
        ("evidence", string_spec(required=True, min_length=1)),
        ("required", FieldSpec("boolean")),
    ),
    additional_properties=False,
)
BRANCH_POLICY_SPEC = object_spec(
    properties=(("pattern", string_spec(required=True, min_length=1)),),
    additional_properties=False,
)
INTEGRATION_OBJECT_SPEC = object_spec(
    properties=(
        ("id", FieldSpec("string", required=True, pattern=IDENTIFIER_PATTERN)),
        ("descriptor", string_spec(min_length=1)),
    ),
    additional_properties=False,
)
INTEGRATION_SPEC = union_spec(
    FieldSpec("string", pattern=IDENTIFIER_PATTERN),
    INTEGRATION_OBJECT_SPEC,
)


MODULE_CONTRACT_V3 = object_spec(
    properties=(
        ("schema_version", FieldSpec("integer", required=True, const=3)),
        ("kind", FieldSpec("string", required=True, enum=("skill", "workflow"))),
        ("id", string_spec(required=True, min_length=1)),
        ("name", string_spec(min_length=1)),
        ("version", string_spec(required=True, min_length=1)),
        (
            "status",
            FieldSpec(
                "string",
                enum=("accepted", "staged", "deprecated", "retired"),
            ),
        ),
        ("summary", string_spec(required=True, min_length=1)),
        ("owners", string_list_spec(required=True)),
        ("compatibility", COMPATIBILITY_SPEC),
        (
            "dependencies",
            FieldSpec("array", items=DEPENDENCY_SPEC),
        ),
        ("inputs", string_list_spec(required=True)),
        ("outputs", string_list_spec(required=True)),
        (
            "commands",
            FieldSpec("array", required=True, items=COMMAND_SPEC),
        ),
        ("related_modules", string_list_spec(required=True)),
        ("validation", string_list_spec(required=True)),
        ("risk", RISK_SPEC),
        ("external_access", EXTERNAL_ACCESS_SPEC),
        ("local_ai", LOCAL_AI_SPEC),
        ("strict_read_only_commands", string_list_spec(required=True)),
        (
            "phases",
            FieldSpec("array", items=PHASE_SPEC),
        ),
        ("phase_lifecycle", PHASE_LIFECYCLE_SPEC),
        ("worker_profiles", WORKER_PROFILES_SPEC),
        ("parallel_safety", PARALLEL_SAFETY_SPEC),
        ("metadata_path", string_spec(min_length=1)),
        ("provenance", PROVENANCE_SPEC),
        ("quality", QUALITY_SPEC),
        ("context_evidence", CONTEXT_EVIDENCE_SPEC),
        ("tasks", FieldSpec("array", items=TASK_SPEC)),
        ("hooks", FieldSpec("array", items=HOOK_SPEC)),
        ("input_schema", INPUT_SCHEMA_SPEC),
        ("gates", FieldSpec("array", items=GATE_SPEC)),
        ("branch_policy", BRANCH_POLICY_SPEC),
        ("integrations", FieldSpec("array", items=INTEGRATION_SPEC)),
        (
            "updated",
            FieldSpec("string", pattern=DATE_PATTERN),
        ),
        ("routing", ROUTING_SPEC),
        ("template_layers", TEMPLATE_LAYERS_SPEC),
        ("context", CONTEXT_SPEC),
        ("determinism", DETERMINISM_SPEC),
        (
            "extensions",
            object_spec(
                required=True,
                additional_properties=object_spec(),
                property_name_pattern=(
                    r"[a-z0-9][a-z0-9.-]*/[a-z0-9][a-z0-9._-]*"
                ),
            ),
        ),
    ),
    additional_properties=False,
    semantic_rules=(
        "unique-command-ids",
        "strict-read-only-command-ids",
        "context-source-contract",
        "determinism-command-contract",
        "parallel-safety-contract",
    ),
)


SEMANTIC_VOCABULARY_URI = (
    "https://schemas.skills-harness.invalid/vocab/module-contract-semantics/v1"
)
SEMANTIC_KEYWORD = "x-moduleContractSemantics"


def _anchored_pattern(pattern: str) -> str:
    return rf"^(?:{pattern})(?![\s\S])"


def _schema_for(spec: FieldSpec) -> dict[str, Any]:
    if spec.variants:
        return {"oneOf": [_schema_for(variant) for variant in spec.variants]}
    schema: dict[str, Any] = {}
    if spec.type_name != "any":
        schema["type"] = spec.type_name
    if spec.const is not _UNSET:
        schema["const"] = spec.const
    if spec.enum:
        schema["enum"] = list(spec.enum)
    if spec.pattern:
        schema["pattern"] = _anchored_pattern(spec.pattern)
    if spec.min_length is not None:
        schema["minLength"] = spec.min_length
    if spec.min_items is not None:
        schema["minItems"] = spec.min_items
    if spec.minimum is not None:
        schema["minimum"] = spec.minimum
    if spec.maximum is not None:
        schema["maximum"] = spec.maximum
    if spec.unique_items:
        schema["uniqueItems"] = True
    if spec.type_name == "array" and spec.items is not None:
        schema["items"] = _schema_for(spec.items)
    if spec.type_name == "object":
        schema["properties"] = {
            name: _schema_for(field) for name, field in spec.properties
        }
        required = [name for name, field in spec.properties if field.required]
        if required:
            schema["required"] = required
        additional = spec.additional_properties
        schema["additionalProperties"] = (
            _schema_for(additional) if isinstance(additional, FieldSpec) else additional
        )
        if spec.property_name_pattern:
            schema["propertyNames"] = {
                "pattern": _anchored_pattern(spec.property_name_pattern)
            }
    return schema


def module_contract_schema() -> dict[str, Any]:
    """Generate the public JSON Schema from the authoritative field model."""

    schema = _schema_for(MODULE_CONTRACT_V3)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://example.invalid/skills-harness/module.schema.json",
        "$vocabulary": {SEMANTIC_VOCABULARY_URI: True},
        "$comment": (
            "Cross-field ModuleContractV3 rules use the required semantic vocabulary; "
            "use validate_schema_instance, not a structural-only JSON Schema validator."
        ),
        "title": "Skills Harness ModuleContractV3",
        SEMANTIC_KEYWORD: {
            "required": True,
            "rules": list(MODULE_CONTRACT_V3.semantic_rules),
        },
        **schema,
    }


def _matches_type(value: Any, type_name: str) -> bool:
    if type_name == "any":
        return True
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    raise ValueError(f"unsupported field type: {type_name}")


def _validate(spec: FieldSpec, value: Any, path: str, errors: list[str]) -> None:
    if spec.variants:
        for variant in spec.variants:
            variant_errors: list[str] = []
            _validate(variant, value, path, variant_errors)
            if not variant_errors:
                return
        errors.append(f"{path} does not match any allowed shape.")
        return
    if not _matches_type(value, spec.type_name):
        errors.append(f"{path} must be a {spec.type_name}.")
        return
    if spec.const is not _UNSET and value != spec.const:
        errors.append(f"{path} must equal {spec.const!r}.")
    if spec.enum and value not in spec.enum:
        errors.append(f"{path} must be one of: {', '.join(map(str, spec.enum))}.")
    if isinstance(value, str):
        if spec.min_length is not None and len(value) < spec.min_length:
            errors.append(f"{path} must contain at least {spec.min_length} character(s).")
        if spec.pattern and re.fullmatch(spec.pattern, value) is None:
            errors.append(f"{path} must match {spec.pattern!r}.")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if spec.minimum is not None and value < spec.minimum:
            errors.append(f"{path} must be at least {spec.minimum}.")
        if spec.maximum is not None and value > spec.maximum:
            errors.append(f"{path} must be at most {spec.maximum}.")
    if isinstance(value, list):
        if spec.min_items is not None and len(value) < spec.min_items:
            errors.append(f"{path} must contain at least {spec.min_items} item(s).")
        if spec.items is not None:
            for index, item in enumerate(value):
                _validate(spec.items, item, f"{path}[{index}]", errors)
        if spec.unique_items:
            seen: set[str] = set()
            for index, item in enumerate(value):
                marker = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if marker in seen:
                    errors.append(f"{path}[{index}] duplicates an earlier item.")
                seen.add(marker)
    if isinstance(value, dict):
        properties = dict(spec.properties)
        for name, field in spec.properties:
            if field.required and name not in value:
                errors.append(f"{path}.{name} is required.")
        for name in sorted(value):
            item_path = f"{path}.{name}"
            if spec.property_name_pattern and re.fullmatch(
                spec.property_name_pattern, name
            ) is None:
                errors.append(
                    f"{item_path} must match property-name pattern "
                    f"{spec.property_name_pattern!r}."
                )
            if name in properties:
                _validate(properties[name], value[name], item_path, errors)
            elif isinstance(spec.additional_properties, FieldSpec):
                _validate(spec.additional_properties, value[name], item_path, errors)
            elif spec.additional_properties is False:
                errors.append(f"{item_path} is not an allowed field.")


_WINDOWS_RESERVED_COMPONENTS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CLOCK$",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _safe_determinism_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if "\0" in value or "\\" in value or ":" in value or value.startswith("/"):
        return False
    components = value.split("/")
    if any(
        not component
        or component in {".", ".."}
        or component != component.strip()
        or component.endswith((".", " "))
        or component.split(".", 1)[0].upper() in _WINDOWS_RESERVED_COMPONENTS
        for component in components
    ):
        return False
    return True


def _json_pointer_tokens(pointer: str) -> tuple[str, ...]:
    return tuple(
        token.replace("~1", "/").replace("~0", "~")
        for token in pointer[1:].split("/")
    )


def _semantic_errors(
    manifest: object,
    rules: tuple[str, ...] | list[str],
    *,
    path: str,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return errors
    commands_value = manifest.get("commands")
    strict_value = manifest.get("strict_read_only_commands")
    if not isinstance(commands_value, list):
        return errors

    commands_by_id: dict[str, list[dict[str, Any]]] = {}
    for command in commands_value:
        if not isinstance(command, dict) or not isinstance(command.get("id"), str):
            continue
        commands_by_id.setdefault(command["id"], []).append(command)

    if "unique-command-ids" in rules:
        for command_id, commands in sorted(commands_by_id.items()):
            if len(commands) > 1:
                errors.append(f"{path} commands has duplicate command id {command_id!r}.")

    if (
        "strict-read-only-command-ids" in rules
        and isinstance(strict_value, list)
    ):
        for command_id in strict_value:
            if not isinstance(command_id, str):
                continue
            matching = commands_by_id.get(command_id, [])
            if not matching:
                errors.append(
                    f"{path} strict_read_only_commands references unknown command id "
                    f"{command_id!r}."
                )
                continue
            unsafe = sorted(
                {
                    effect
                    for command in matching
                    for effect in command.get("effects", [])
                    if isinstance(effect, str)
                }
            )
            if unsafe:
                errors.append(
                    f"{path} strict_read_only_commands references command "
                    f"{command_id!r} with unsafe effects: {', '.join(unsafe)}."
                )
    if "context-source-contract" in rules:
        context = manifest.get("context")
        if isinstance(context, dict):
            budgets = context.get("budgets")
            known_budgets = set(budgets) if isinstance(budgets, dict) else set()
            sources = context.get("sources")
            seen_source_ids: set[str] = set()
            if isinstance(sources, list):
                for index, source in enumerate(sources):
                    if not isinstance(source, dict):
                        continue
                    source_path = f"{path} context.sources[{index}]"
                    has_path = isinstance(source.get("path"), str)
                    has_pattern = isinstance(source.get("pattern"), str)
                    if has_path == has_pattern:
                        errors.append(
                            f"{source_path} must declare exactly one of path or pattern."
                        )
                    source_id = source.get("id")
                    if isinstance(source_id, str):
                        if source_id in seen_source_ids:
                            errors.append(
                                f"{source_path}.id duplicates context source id {source_id!r}."
                            )
                        seen_source_ids.add(source_id)
                    budget_ref = source.get("budget_ref")
                    if isinstance(budget_ref, str) and budget_ref not in known_budgets:
                        errors.append(
                            f"{source_path}.budget_ref references unknown context budget "
                            f"{budget_ref!r}."
                        )
    if "determinism-command-contract" in rules:
        determinism = manifest.get("determinism")
        if isinstance(determinism, dict):
            strict_ids = {
                command_id
                for command_id in strict_value or []
                if isinstance(command_id, str)
            }
            replay_commands = determinism.get("replay_commands")
            if isinstance(replay_commands, list):
                for command_id in replay_commands:
                    if not isinstance(command_id, str):
                        continue
                    if command_id not in commands_by_id:
                        errors.append(
                            f"{path} determinism.replay_commands references unknown command id "
                            f"{command_id!r}."
                        )
                    elif command_id not in strict_ids:
                        errors.append(
                            f"{path} determinism.replay_commands references non-strict command id "
                            f"{command_id!r}."
                        )
            temporary_effects = determinism.get("allowed_temporary_effects")
            if isinstance(temporary_effects, list):
                for index, effect in enumerate(temporary_effects):
                    if not isinstance(effect, dict):
                        continue
                    effect_path = effect.get("path")
                    if not _safe_determinism_relative_path(effect_path):
                        errors.append(
                            f"{path} determinism.allowed_temporary_effects[{index}].path "
                            "must be a safe portable relative path."
                        )
            pointers = determinism.get("volatile_json_pointers")
            if isinstance(pointers, list):
                valid_pointers = [
                    pointer
                    for pointer in pointers
                    if isinstance(pointer, str)
                    and re.fullmatch(
                        r"/(?:[^~/]|~[01])*(?:/(?:[^~/]|~[01])*)*",
                        pointer,
                    )
                ]
                token_rows = [(_json_pointer_tokens(pointer), pointer) for pointer in valid_pointers]
                for index, (left_tokens, left) in enumerate(token_rows):
                    for right_tokens, right in token_rows[index + 1 :]:
                        shorter = min(len(left_tokens), len(right_tokens))
                        if left_tokens[:shorter] == right_tokens[:shorter]:
                            errors.append(
                                f"{path} determinism.volatile_json_pointers overlap: "
                                f"{left!r} and {right!r}."
                            )
    if "parallel-safety-contract" in rules:
        workers = manifest.get("worker_profiles")
        parallel_safety = manifest.get("parallel_safety")
        declared_max = (
            workers.get("max_parallel_workers")
            if isinstance(workers, dict)
            else 1
        )
        if (
            isinstance(declared_max, int)
            and not isinstance(declared_max, bool)
            and declared_max > 1
            and not isinstance(parallel_safety, dict)
        ):
            errors.append(
                f"{path} parallel_safety is required when "
                "worker_profiles.max_parallel_workers is greater than 1."
            )
        if isinstance(parallel_safety, dict):
            known_phases = {
                phase.get("id")
                for phase in manifest.get("phases", [])
                if isinstance(phase, dict) and isinstance(phase.get("id"), str)
            }
            phase_policies = parallel_safety.get("phase_policies")
            if isinstance(phase_policies, dict):
                for phase_id, policy in phase_policies.items():
                    policy_path = f"{path} parallel_safety.phase_policies.{phase_id}"
                    if phase_id not in known_phases:
                        errors.append(
                            f"{policy_path} references unknown phase {phase_id!r}."
                        )
                    if not isinstance(policy, dict):
                        continue
                    mode = policy.get("mode")
                    max_workers = policy.get("max_workers")
                    write_scopes = policy.get("write_scopes")
                    runtime = policy.get("runtime")
                    runtime = runtime if isinstance(runtime, dict) else {}
                    provider = policy.get("provider")
                    if (
                        isinstance(max_workers, int)
                        and not isinstance(max_workers, bool)
                        and isinstance(declared_max, int)
                        and not isinstance(declared_max, bool)
                        and max_workers > declared_max
                    ):
                        errors.append(
                            f"{policy_path}.max_workers cannot exceed "
                            "worker_profiles.max_parallel_workers."
                        )
                    if mode == "serial":
                        if max_workers != 1:
                            errors.append(
                                f"{policy_path}.max_workers must be 1 in serial mode."
                            )
                    elif mode == "parallel-read-only":
                        if not isinstance(max_workers, int) or max_workers < 2:
                            errors.append(
                                f"{policy_path}.max_workers must be at least 2 in "
                                "parallel-read-only mode."
                            )
                        if write_scopes != []:
                            errors.append(
                                f"{policy_path}.write_scopes must be empty in "
                                "parallel-read-only mode."
                            )
                        expected_runtime = {
                            "environment": "inherited-read-only",
                            "ports": "none",
                            "state_stores": "none",
                            "services": "none",
                        }
                        if runtime != expected_runtime:
                            errors.append(
                                f"{policy_path}.runtime must declare inherited-read-only "
                                "environment and no ports, state stores, or services."
                            )
                        if provider != "none":
                            errors.append(
                                f"{policy_path}.provider must be 'none' in "
                                "parallel-read-only mode."
                            )
                        if "provision_command_id" in policy or "cleanup_command_id" in policy:
                            errors.append(
                                f"{policy_path} must not declare provision or cleanup commands "
                                "in parallel-read-only mode."
                            )
                    elif mode == "parallel-isolated":
                        if not isinstance(max_workers, int) or max_workers < 2:
                            errors.append(
                                f"{policy_path}.max_workers must be at least 2 in "
                                "parallel-isolated mode."
                            )
                        if not isinstance(write_scopes, list) or not write_scopes:
                            errors.append(
                                f"{policy_path}.write_scopes must be non-empty in "
                                "parallel-isolated mode."
                            )
                        elif any(
                            not isinstance(scope, str) or "{worker_id}" not in scope
                            for scope in write_scopes
                        ):
                            errors.append(
                                f"{policy_path}.write_scopes entries must contain "
                                "'{worker_id}' in parallel-isolated mode."
                            )
                        expected_runtime = {
                            "environment": "per-worker",
                            "ports": "per-worker",
                            "state_stores": "per-worker",
                            "services": "per-worker",
                        }
                        if runtime != expected_runtime:
                            errors.append(
                                f"{policy_path}.runtime must declare per-worker environment, "
                                "ports, state stores, and services."
                            )
                        if provider != "external":
                            errors.append(
                                f"{policy_path}.provider must be 'external' in "
                                "parallel-isolated mode."
                            )
                        for field in ("provision_command_id", "cleanup_command_id"):
                            command_id = policy.get(field)
                            matching = commands_by_id.get(command_id, []) if isinstance(command_id, str) else []
                            if not matching:
                                errors.append(
                                    f"{policy_path}.{field} must reference a declared command id."
                                )
                                continue
                            unsafe_effects = sorted(
                                {
                                    effect
                                    for command in matching
                                    for effect in command.get("effects", [])
                                    if effect in {"repository_write", "upload", "external_write"}
                                }
                            )
                            if unsafe_effects:
                                errors.append(
                                    f"{policy_path}.{field} references a command with "
                                    f"unsafe isolation effects: {', '.join(unsafe_effects)}."
                                )
    return errors


def validate_v3(manifest: object) -> tuple[list[str], list[str]]:
    """Validate a v3 manifest directly against ModuleContractV3."""

    errors: list[str] = []
    _validate(MODULE_CONTRACT_V3, manifest, "module.json", errors)
    errors.extend(
        _semantic_errors(
            manifest,
            MODULE_CONTRACT_V3.semantic_rules,
            path="module.json",
        )
    )
    return errors, []


def _schema_type_matches(value: object, type_name: object) -> bool:
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    return True


def _validate_schema_node(
    node: dict[str, Any],
    value: object,
    path: str,
    errors: list[str],
) -> None:
    variants = node.get("oneOf")
    if isinstance(variants, list):
        accepted = 0
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            variant_errors: list[str] = []
            _validate_schema_node(variant, value, path, variant_errors)
            if not variant_errors:
                accepted += 1
        if accepted != 1:
            errors.append(f"{path} must match exactly one allowed schema shape.")
        return

    expected = node.get("type")
    if expected is not None and not _schema_type_matches(value, expected):
        errors.append(f"{path} must be a {expected}.")
        return
    if "const" in node and value != node["const"]:
        errors.append(f"{path} must match const.")
    enum = node.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path} must be one of the declared enum values.")
    if isinstance(value, str):
        minimum_length = node.get("minLength")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            errors.append(f"{path} is shorter than minLength.")
        pattern = node.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            errors.append(f"{path} does not match pattern.")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = node.get("minimum")
        maximum = node.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(f"{path} is below minimum.")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(f"{path} is above maximum.")
    if isinstance(value, list):
        minimum_items = node.get("minItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            errors.append(f"{path} has too few items.")
        if node.get("uniqueItems") is True:
            markers = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            if len(markers) != len(set(markers)):
                errors.append(f"{path} must contain unique items.")
        item_schema = node.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema_node(item_schema, item, f"{path}[{index}]", errors)
    if isinstance(value, dict):
        required = node.get("required")
        if isinstance(required, list):
            for name in required:
                if name not in value:
                    errors.append(f"{path}.{name} is required.")
        properties = node.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        additional = node.get("additionalProperties", True)
        property_names = node.get("propertyNames")
        property_pattern = (
            property_names.get("pattern")
            if isinstance(property_names, dict)
            else None
        )
        for name, item in value.items():
            item_path = f"{path}.{name}"
            if isinstance(property_pattern, str) and re.search(property_pattern, name) is None:
                errors.append(f"{item_path} is not a valid property name.")
            if name in properties and isinstance(properties[name], dict):
                _validate_schema_node(properties[name], item, item_path, errors)
            elif additional is False:
                errors.append(f"{item_path} is not an allowed field.")
            elif isinstance(additional, dict):
                _validate_schema_node(additional, item, item_path, errors)


def validate_schema_instance(
    manifest: object,
    *,
    schema: dict[str, Any] | None = None,
) -> list[str]:
    """Validate with the generated schema and its required semantic vocabulary."""

    generated = module_contract_schema() if schema is None else schema
    errors: list[str] = []
    vocabulary = generated.get("$vocabulary")
    semantics = generated.get(SEMANTIC_KEYWORD)
    if not (
        isinstance(vocabulary, dict)
        and vocabulary.get(SEMANTIC_VOCABULARY_URI) is True
        and isinstance(semantics, dict)
        and semantics.get("required") is True
        and isinstance(semantics.get("rules"), list)
    ):
        return ["generated schema is missing the required ModuleContractV3 semantic vocabulary."]
    _validate_schema_node(generated, manifest, "module.json", errors)
    errors.extend(
        _semantic_errors(
            manifest,
            semantics["rules"],
            path="module.json",
        )
    )
    return errors


def normalize_v3(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a detached v3 runtime view without interpreting argv as shell text."""

    return copy.deepcopy(manifest)


def canonical_module_json(manifest: dict[str, Any]) -> str:
    """Serialize a normalized module deterministically for tracked JSON files."""

    return json.dumps(
        normalize_v3(manifest),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def command_id_for_argv(argv: list[str], *, prefix: str = "command") -> str:
    """Return a stable ID for an already-tokenized command without shell parsing."""

    digest = hashlib.sha256("\0".join(argv).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def lexical_argv_from_text(command_text: str) -> list[str]:
    """Tokenize authored command notation without shell expansion or OS rules."""

    tokens: list[str] = []
    current: list[str] = []
    quote = ""
    for character in command_text:
        if quote:
            if character == quote:
                quote = ""
            else:
                current.append(character)
            continue
        if character in {"'", '"'}:
            quote = character
        elif character.isspace():
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(character)
    if quote:
        raise ValueError(f"command has an unterminated quote: {command_text!r}")
    if current:
        tokens.append("".join(current))
    if not tokens:
        raise ValueError("command must not be empty")
    executable = tokens[0].replace("\\", "/")
    if executable.endswith(".py") and executable != "python":
        tokens = ["python", "-B", *tokens]
    return tokens


def _suite_declares_lifecycle_smoke(root: Path | None, argv: list[str]) -> bool:
    if root is None or "eval-workflow" not in argv or "--suite" not in argv:
        return False
    suite_index = argv.index("--suite") + 1
    if suite_index >= len(argv):
        return False
    resolved_root = root.resolve()
    suite_path = (resolved_root / argv[suite_index]).resolve()
    try:
        suite_path.relative_to(resolved_root)
    except ValueError:
        return False
    try:
        suite = json.loads(suite_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    pending: list[Any] = [suite]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if value.get("type") == "workflow_lifecycle_smoke_ok":
                return True
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return False


def infer_command_effects(
    command: object,
    manifest: dict[str, Any],
    *,
    root: Path | None = None,
) -> list[str]:
    """Infer declared effects from exact argv tokens and module semantics."""

    argv = command_argv(command)
    lowered = [token.lower().replace("\\", "/") for token in argv]
    module_id = str(manifest.get("id", "")).lower()

    def contains(fragment: str) -> bool:
        return any(fragment in token for token in lowered)

    def adjacent(*tokens: str) -> bool:
        width = len(tokens)
        expected = list(tokens)
        return any(
            lowered[index : index + width] == expected
            for index in range(max(0, len(lowered) - width + 1))
        )

    script_basenames = {
        token.rsplit("/", 1)[-1]
        for token in lowered
        if token.endswith(".py")
    }
    safe_check_suite = adjacent("benchmark", "routing-eval") and "--check-suite" in lowered
    dry_run = any(flag in lowered for flag in ("--dry-run", "--check", "--help")) or safe_check_suite
    feedback_export = adjacent("feedback", "export")
    feedback_clear = adjacent("feedback", "clear")
    target_values = [
        lowered[index + 1]
        for index, token in enumerate(lowered[:-1])
        if token in {"--target", "--project-root", "--source-root", "--work-dir"}
    ]
    external_target = any(value.rstrip("/") not in {"", "."} for value in target_values)
    target_write = external_target and any(
        flag in lowered for flag in ("--write", "--apply", "--install")
    )
    explicit_write_verb = any(
        token in {"install", "migrate", "sync", "create", "start", "resume", "finish", "promote"}
        or token.startswith("install-")
        or token.startswith("migrate-")
        for token in lowered
    )
    default_routing_sync = bool(
        script_basenames & {"sync_skill_routing.py", "sync_automation_routing.py"}
    )
    benchmark_aggregate_output = (
        module_id == "agent-benchmarking"
        and "three_arm_full_run.py" in script_basenames
        and "aggregate" in lowered
        and "--output" in lowered
    )
    write_requested = not dry_run and (
        any(
            flag in lowered
            for flag in ("--write", "--apply", "--configure", "--confirm-truncate")
        )
        or explicit_write_verb
        or default_routing_sync
    )
    repository_write = (write_requested or feedback_export or benchmark_aggregate_output) and not (
        target_write or feedback_clear
    )

    script_temp_work = any(
        token.endswith(".py")
        and (
            token.rsplit("/", 1)[-1] == "run_self_tests.py"
            or "benchmark" in token.rsplit("/", 1)[-1]
            or "smoke" in token.rsplit("/", 1)[-1]
        )
        for token in lowered
    )
    cli_temp_work = any(token in {"benchmark", "smoke", "determinism-check"} for token in lowered)
    default_lifecycle_scorecard = adjacent("workflow", "scorecard") and (
        "--no-lifecycle" not in lowered
    )
    eval_workflow = "eval-workflow" in lowered and (
        root is None or _suite_declares_lifecycle_smoke(root, argv)
    )
    temporary_write = (
        not dry_run
        and (
            script_temp_work
            or cli_temp_work
            or default_lifecycle_scorecard
            or eval_workflow
        )
    ) or feedback_clear

    network = not dry_run and (
        adjacent("git", "fetch")
        or adjacent("git", "pull")
        or adjacent("git", "clone")
        or contains("download")
        or contains("fetch_reference")
        or contains("refresh_reference")
        or (module_id == "azure-devops-ticket-intake" and contains("import_azure"))
        or (
            module_id == "external-reference-manager"
            and contains("sync_references.py")
            and "--no-fetch" not in lowered
        )
        or (
            module_id == "sonarqube-diagnostics"
            and any(contains(marker) for marker in ("export", "publish", "scanner"))
        )
        or (
            module_id == "local-ai-helper"
            and any(contains(marker) for marker in ("download", "install", "bootstrap"))
        )
    )
    credential_parts = {
        part
        for token in lowered
        for part in re.split(r"[/_.-]+", token)
        if part
    }
    credentials = network and (
        module_id in {
            "azure-devops-ticket-intake",
            "sonarqube-diagnostics",
        }
        or bool(
            credential_parts
            & {"devops", "tfs", "github", "credential", "credentials", "private", "token", "pat"}
        )
    )
    install = not dry_run and (
        "--install" in lowered
        or any(token == "install" or token.startswith("install-") for token in lowered)
    )
    transfers_out = not dry_run and (
        "upload" in lowered or "publish" in lowered or adjacent("git", "push")
    )
    external_write = not dry_run and (
        target_write
        or transfers_out
        or "send" in lowered
        or "create-work-item" in lowered
    )

    enabled = {
        "repository_write": repository_write,
        "temporary_write": temporary_write,
        "network": network,
        "credentials": credentials,
        "install": install,
        "upload": transfers_out,
        "external_write": external_write,
    }
    if not dry_run and module_id == "local-ai-helper" and contains("setup_local_ai.py"):
        enabled.update(
            {
                "temporary_write": True,
                "network": True,
                "credentials": True,
                "install": True,
            }
        )
    if not dry_run and module_id == "repo-navigation" and contains(
        "install_navigation_workflow.py"
    ):
        enabled.update({"repository_write": True, "install": True})
    if (
        not dry_run
        and module_id == "mermaid-diagrams-azure-devops"
        and contains("setup_vscode_mermaid_preview.py")
    ):
        enabled.update({"repository_write": True, "install": True})
    return [name for name in COMMAND_EFFECT_KEYS if enabled[name]]


def default_delegation_contract() -> dict[str, Any]:
    """Return the conservative portable delegation policy used by owned workflows."""

    return {
        "schema_version": 1,
        "trigger": "explicit-request-or-owner-instruction",
        "max_depth": 1,
        "eligible_task_classes": ["independent-read-heavy"],
        "require_model_attestation": True,
        "require_complete_thread_tree": True,
        "economics_gate_ref": "delegation-balanced-v1",
        "fallback": "sequential",
    }


def conventional_template_layers(workflow_id: str) -> dict[str, Any]:
    """Return the explicit v3 template contract for a conventional workflow."""

    return {
        "default_template": "plan.md",
        "override_roots": [f"docs/project/workflow-overrides/{workflow_id}"],
        "preset_roots": [f"automations/{workflow_id}/presets"],
        "profiles": {
            "default": {"template_roots": ["templates"]},
            "lean": {
                "template_roots": ["templates"],
                "template": "lean-plan.md",
            },
        },
        "priorities": {
            "project-override": 0,
            "workflow-preset": 50,
            "workflow-lean": 90,
            "workflow-default": 100,
        },
        "conflict_policy": "error",
    }


def _safe_template_root(repository_root: Path, base: Path, value: object) -> Path | None:
    text = str(value or "").strip().replace("\\", "/")
    relative = Path(text)
    if not text or relative.is_absolute() or ".." in relative.parts:
        return None
    candidate = base / relative
    try:
        candidate.resolve(strict=False).relative_to(repository_root.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def _safe_template_child_file(boundary: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(boundary.resolve())
    except (OSError, ValueError):
        return False
    return candidate.is_file()


def _safe_template_child_dir(boundary: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(boundary.resolve())
    except (OSError, ValueError):
        return False
    return candidate.is_dir()


def _conventional_template_profiles(
    repository_root: Path,
    workflow_dir: Path,
    layers: dict[str, Any],
) -> dict[str, Any]:
    template_name = str(layers.get("default_template", "")).strip().replace("\\", "/")
    template_path = Path(template_name)
    if not template_name or template_path.is_absolute() or ".." in template_path.parts:
        return {}
    shared_provider = False
    for value in layers.get("override_roots", []):
        declared = _safe_template_root(repository_root, repository_root, value)
        if declared is not None and _safe_template_child_file(
            declared,
            declared / template_path,
        ):
            shared_provider = True
            break
    for value in layers.get("preset_roots", []):
        declared = _safe_template_root(repository_root, repository_root, value)
        if declared is None or not declared.is_dir():
            continue
        if any(
            _safe_template_child_dir(declared, preset)
            and _safe_template_child_file(
                preset,
                preset / "templates" / template_path,
            )
            for preset in declared.iterdir()
        ):
            shared_provider = True
            break
    profiles = layers.get("profiles") if isinstance(layers.get("profiles"), dict) else {}
    available: dict[str, Any] = {}
    for profile_name, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        profile_available = shared_provider
        candidate_name = str(profile.get("template") or template_name)
        for value in profile.get("template_roots", []):
            declared = _safe_template_root(repository_root, workflow_dir, value)
            if declared is not None and _safe_template_child_file(
                declared,
                declared / candidate_name,
            ):
                profile_available = True
                break
        if profile_available:
            available[str(profile_name)] = copy.deepcopy(profile)
    return available


def materialize_conventional_template_availability(
    manifest: dict[str, Any],
    *,
    repository_root: Path,
    workflow_dir: Path,
) -> None:
    """Make conventional profiles unavailable when the workflow has no template provider."""

    if manifest.get("kind") != "workflow":
        return
    layers = manifest.get("template_layers")
    if not isinstance(layers, dict):
        return
    defaults = conventional_template_layers(str(manifest.get("id", "")).strip())
    if any(
        layers.get(key) != value
        for key, value in defaults.items()
        if key != "profiles"
    ):
        return
    profiles = layers.get("profiles")
    if not isinstance(profiles, dict) or any(
        name not in defaults["profiles"] or profile != defaults["profiles"][name]
        for name, profile in profiles.items()
    ):
        return
    layers["profiles"] = _conventional_template_profiles(
        repository_root,
        workflow_dir,
        defaults,
    )


def _context_source(
    source_id: str,
    artifact_role: str,
    *,
    path: str | None = None,
    pattern: str | None = None,
    load_policy: str,
    critical_category: str,
    budget_ref: str,
    preserve_coordinates: bool,
) -> dict[str, Any]:
    source: dict[str, Any] = {
        "id": source_id,
        "artifact_role": artifact_role,
        "load_policy": load_policy,
        "critical_category": critical_category,
        "budget_ref": budget_ref,
        "preserve_coordinates": preserve_coordinates,
    }
    if path is not None:
        source["path"] = path
    if pattern is not None:
        source["pattern"] = pattern
    return source


def conventional_context(workflow_id: str) -> dict[str, Any]:
    """Return declarative sources for a conventional workflow context."""

    module_root = f"automations/{workflow_id}"
    run_root = f"{module_root}/runs/<run-id>"
    scope_category = (
        "scope-required"
        if workflow_id in {"user-story-workflow", "bug-ticket-workflow"}
        else "work-item"
    )
    ticket_role = (
        "bug-ticket"
        if workflow_id == "bug-ticket-workflow"
        else "user-story"
        if workflow_id == "user-story-workflow"
        else "ticket-info"
    )
    sources = [
        _context_source(
            "workflow-entry",
            "workflow-entry",
            path=f"{module_root}/WORKFLOW.md",
            load_policy="handle_only",
            critical_category="workflow-contract",
            budget_ref="core",
            preserve_coordinates=False,
        ),
        _context_source(
            "module-contract",
            "module-contract",
            path=f"{module_root}/module.json",
            load_policy="handle_only",
            critical_category="workflow-contract",
            budget_ref="core",
            preserve_coordinates=True,
        ),
        _context_source(
            "instructions",
            "instructions",
            path=f"{module_root}/instructions.md",
            load_policy="handle_only",
            critical_category="workflow-contract",
            budget_ref="core",
            preserve_coordinates=False,
        ),
        _context_source(
            "workflow-templates",
            "workflow-template",
            pattern=f"{module_root}/templates/*.md",
            load_policy="handle_only",
            critical_category="workflow-contract",
            budget_ref="core",
            preserve_coordinates=False,
        ),
        _context_source(
            "plan-template",
            "implementation-plan-template",
            path=f"{module_root}/templates/plan.md",
            load_policy="handle_only",
            critical_category="workflow-contract",
            budget_ref="core",
            preserve_coordinates=False,
        ),
        _context_source(
            "execution-template",
            "execution-log-template",
            path=f"{module_root}/templates/execution-log.md",
            load_policy="handle_only",
            critical_category="workflow-contract",
            budget_ref="core",
            preserve_coordinates=False,
        ),
        _context_source(
            "pr-template",
            "pr-handoff-template",
            path=f"{module_root}/templates/pr-description.md",
            load_policy="handle_only",
            critical_category="workflow-contract",
            budget_ref="core",
            preserve_coordinates=False,
        ),
        _context_source(
            "run-state",
            "run-state",
            path=f"{run_root}/run.json",
            load_policy="handle_only",
            critical_category="resume-critical",
            budget_ref="run-critical",
            preserve_coordinates=True,
        ),
        _context_source(
            "run-report",
            "run-report",
            path=f"{run_root}/REPORT.md",
            load_policy="handle_only",
            critical_category="resume-critical",
            budget_ref="run-critical",
            preserve_coordinates=True,
        ),
        _context_source(
            "execution-log",
            "execution-log",
            path=f"{run_root}/execution-log.md",
            load_policy="handle_only",
            critical_category="resume-critical",
            budget_ref="run-critical",
            preserve_coordinates=True,
        ),
        _context_source(
            "ticket-info",
            ticket_role,
            path=f"{run_root}/ticket-info.md",
            load_policy="handle_only",
            critical_category=scope_category,
            budget_ref="run-critical",
            preserve_coordinates=True,
        ),
        _context_source(
            "ticket-template",
            ticket_role,
            path=f"{module_root}/templates/ticket-info.md",
            load_policy="handle_only",
            critical_category=scope_category,
            budget_ref="core",
            preserve_coordinates=True,
        ),
        _context_source(
            "actual-plan",
            "implementation-plan",
            path=f"{run_root}/plan.md",
            load_policy="handle_only",
            critical_category="resume-critical",
            budget_ref="run-critical",
            preserve_coordinates=True,
        ),
        _context_source(
            "pr-description",
            "pr-handoff",
            path=f"{run_root}/pr-description.md",
            load_policy="handle_only",
            critical_category="handoff",
            budget_ref="run-critical",
            preserve_coordinates=True,
        ),
        _context_source(
            "validation-evidence",
            "validation-evidence",
            pattern=f"{run_root}/validation/**/*",
            load_policy="handle_only",
            critical_category="evidence",
            budget_ref="evidence",
            preserve_coordinates=False,
        ),
    ]
    if workflow_id in {"user-story-workflow", "bug-ticket-workflow"}:
        sources.extend(
            [
                _context_source(
                    "project-context",
                    "project-context",
                    pattern="docs/**/project-context.md",
                    load_policy="handle_only",
                    critical_category="project-context",
                    budget_ref="project-context",
                    preserve_coordinates=True,
                ),
                _context_source(
                    "root-project-context",
                    "project-context",
                    path="PROJECT_CONTEXT.md",
                    load_policy="handle_only",
                    critical_category="project-context",
                    budget_ref="project-context",
                    preserve_coordinates=True,
                ),
            ]
        )
    return {
        "budgets": {
            "core": 32_000,
            "evidence": 64_000,
            "project-context": 32_000,
            "run-critical": 64_000,
        },
        "sources": sources,
    }


def normalize_module_contract(
    manifest: object,
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Validate and return a detached ModuleContractV3 runtime view."""

    if not isinstance(manifest, dict):
        return {}, ["module.json must be an object."], []
    if manifest.get("schema_version") != 3:
        return (
            {},
            ["module.json schema_version must be 3."],
            [],
        )
    normalized = normalize_v3(manifest)
    errors, warnings = validate_v3(normalized)
    return normalized, errors, warnings
