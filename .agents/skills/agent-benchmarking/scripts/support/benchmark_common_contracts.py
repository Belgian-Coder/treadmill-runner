"""Shared benchmark constants and taxonomies."""

from __future__ import annotations

import re

SCHEMA_VERSION = 1
TOOL_NAME = "agent-benchmarking"
TOKEN_ESTIMATION_METHOD = "tiktoken_o200k_base_exact_if_available_else_estimated_chars_div_4"
TOKEN_ENCODING_NAME = "o200k_base"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

EXECUTION_TRACE_V1_TOOL = "agent-benchmarking.execution-trace"
EXECUTION_TRACE_V1_SUMMARY_TOOL = "agent-benchmarking.execution-trace-summary"
EXECUTION_TRACE_V1_MAX_EVENTS = 10_000
EXECUTION_TRACE_V1_ACTOR_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
)
EXECUTION_TRACE_V1_OPERATION_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
)
EXECUTION_TRACE_V1_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EXECUTION_TRACE_V1_FIELDS = {
    "schema_version",
    "tool",
    "root_actor_id",
    "events",
}
EXECUTION_TRACE_V1_EVENT_FIELDS = {
    "sequence",
    "elapsed_ms",
    "round",
    "kind",
    "actor_id",
    "target_actor_id",
    "operation",
    "input_fingerprint",
    "result_fingerprint",
    "authorized",
    "context_inheritance",
    "scope",
    "material",
}
EXECUTION_TRACE_V1_EVENT_KINDS = {
    "action",
    "command",
    "read",
    "validation",
    "spawn",
    "compaction",
    "observation",
}
EXECUTION_TRACE_V1_CONTEXT_INHERITANCE = {
    "not-applicable",
    "fresh",
    "full",
    "selected-turns",
    "unknown",
}
EXECUTION_TRACE_V1_SCOPE_STATES = {"within", "excess", "unknown"}
EXECUTION_TRACE_V1_NEGATIVE_COUNT_KEYS = {
    "duplicate_command_count",
    "unchanged_read_count",
    "unchanged_validation_count",
    "unauthorized_spawn_count",
    "recursive_spawn_count",
    "unknown_context_inheritance_count",
    "scope_excess_count",
}
EXECUTION_TRACE_V1_NEUTRAL_COUNT_KEYS = {
    "event_count",
    "action_count",
    "command_count",
    "read_count",
    "validation_count",
    "spawn_count",
    "compaction_count",
    "observation_count",
    "material_action_count",
    "round_count",
    "max_depth",
}
EXECUTION_TRACE_V1_SUMMARY_FIELDS = {
    "schema_version",
    "tool",
    "method",
    *EXECUTION_TRACE_V1_NEGATIVE_COUNT_KEYS,
    *EXECUTION_TRACE_V1_NEUTRAL_COUNT_KEYS,
    "time_to_first_material_action_ms",
}

QUALITY_RUBRICS: dict[str, dict[str, list[str]]] = {
    "code-review": {
        "model_quality": [
            "Finds concrete bugs, regressions, and missing tests.",
            "Avoids style-only feedback unless it affects maintainability or behavior.",
        ],
        "agent_behavior": [
            "Prioritizes findings by severity and cites file/line evidence.",
            "Keeps summaries secondary to actionable findings.",
        ],
        "tool_behavior": [
            "Uses local evidence and validation output instead of unsupported claims.",
            "Records skipped validation honestly.",
        ],
        "workflow_quality": [
            "Produces review evidence that can be attached to a workflow run.",
        ],
    },
    "planning": {
        "model_quality": [
            "Breaks work into feasible, ordered steps with clear dependencies.",
            "Names risks, unknowns, and validation gates.",
        ],
        "agent_behavior": [
            "Keeps implementation scope bounded to the request.",
            "Does not invent project conventions when evidence is missing.",
        ],
        "tool_behavior": [
            "References known commands and files from local context.",
        ],
        "workflow_quality": [
            "Leaves a resumable plan with expected outputs and checks.",
        ],
    },
    "tool-use": {
        "model_quality": [
            "Selects tools that fit the requested evidence or mutation boundary.",
        ],
        "agent_behavior": [
            "Runs read-only inspection before writing and preserves unrelated changes.",
        ],
        "tool_behavior": [
            "Commands are reproducible, scoped, and have captured status.",
        ],
        "workflow_quality": [
            "Tool evidence is structured enough for later comparison.",
        ],
    },
    "repository-search": {
        "model_quality": [
            "Answers from cited source evidence and abstains when exact evidence is absent.",
        ],
        "agent_behavior": [
            "Reads cited files directly before making claims.",
        ],
        "tool_behavior": [
            "Reports search terms, exclusions, cited paths, and bounded evidence.",
        ],
        "workflow_quality": [
            "Keeps search evidence under workflow-owned evidence paths.",
        ],
    },
    "vision": {
        "model_quality": [
            "Describes visible pixels, layout, charts, and raster text without metadata-only shortcuts.",
        ],
        "agent_behavior": [
            "States uncertainty for unreadable visual text.",
        ],
        "tool_behavior": [
            "Records image/PDF page inputs and render status.",
        ],
        "workflow_quality": [
            "Produces page-level evidence suitable for document review workflows.",
        ],
    },
    "workflow-execution": {
        "model_quality": [
            "Follows workflow phase contracts and completion requirements.",
        ],
        "agent_behavior": [
            "Updates run state, decisions, skipped checks, blocked items, and next actions.",
        ],
        "tool_behavior": [
            "Uses declared scripts and validates outputs.",
        ],
        "workflow_quality": [
            "Leaves workflow-owned evidence and resumable handoff notes.",
        ],
    },
}

FAILURE_TAXONOMY_CATEGORIES = {
    "unsupported-claim",
    "invented-path",
    "invented-command",
    "false-validation-claim",
    "missing-evidence",
    "missing-artifact",
    "missing-control",
    "missing-treatment",
    "skipped-validation",
    "tool-failure",
    "tool-discovery",
    "wrong-tool",
    "bad-parameters",
    "sequencing",
    "recovery-failure",
    "premature-stop",
    "unsupported-final-claim",
    "unsafe-path",
    "module-contract-miss",
    "overeager-action",
    "unauthorized-install",
    "scope-expansion",
    "redundant-verification",
    "unchanged-evidence-cycle",
    "overbuild",
    "non-material-review",
    "invalid-comparison",
    "skill-interference",
    "token-overhead-without-quality-gain",
    "external-download-triggered",
    "compute-budget-exceeded",
    "setup-blocker",
    "output-schema-error",
    "context-overload",
    "timeout",
    "config-error",
    "assertion-mismatch",
    "transient-error",
    "permanent-error",
    "other",
}

STANDARD_NUMERIC_METRICS = {
    "ttft_ms",
    "tpot_ms",
    "itl_ms",
    "e2e_latency_ms",
    "model_load_ms",
    "prompt_eval_ms",
    "decode_ms",
    "request_throughput_rps",
    "output_throughput_tps",
    "peak_memory_mib",
    "cpu_utilization_percent",
}
STANDARD_BOOL_METRICS = {"cold_start", "warm_cache"}
STANDARD_AGENT_BOOL_METRICS = {"verifier_passed", "trajectory_complete"}
STANDARD_AGENT_NUMERIC_METRICS = {
    "pass_at_1",
    "attempts",
    "tool_call_count",
    "tool_retry_count",
    "unsupported_claim_count",
    "evidence_coverage_percent",
}
TRAJECTORY_SIGNAL_COUNT_KEYS = {
    "misalignment_count",
    "stagnation_count",
    "redundant_verification_count",
    "unchanged_evidence_cycle_count",
    "scope_expansion_count",
    "overbuild_count",
    "non_material_review_count",
    "disengagement_count",
    "satisfaction_count",
    "execution_failure_count",
    "loop_count",
    "environment_exhaustion_count",
    "timeout_count",
    "tool_error_count",
    *EXECUTION_TRACE_V1_NEGATIVE_COUNT_KEYS,
}
RUN_CONFIG_COMPARE_KEYS = {
    "model_hash",
    "runtime_hash",
    "quantization",
    "backend",
    "threads",
    "context_size",
    "batch_size",
    "kv_cache",
    "prompt_version",
    "suite_version",
    "verifier_version",
    "embedding_profile",
    "retrieval_backend",
    "vector_state",
    "hybrid_weight_preset",
    "chunking_version",
    "query_scope",
    "temperature",
    "seed",
    "output_cap",
}
