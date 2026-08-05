"""Focused contract tests for the authoritative ModuleContractV3 field model."""

from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch


def contract_module():
    try:
        return importlib.import_module("module_contract_v3")
    except ModuleNotFoundError:
        raise AssertionError("ModuleContractV3 implementation is missing") from None


def minimal_v3_manifest() -> dict[str, Any]:
    return {
        "schema_version": 3,
        "kind": "skill",
        "id": "demo-skill",
        "version": "1.0.0",
        "summary": "Demonstrates the v3 contract.",
        "owners": ["engineering"],
        "inputs": ["SKILL.md", "module.json"],
        "outputs": [],
        "commands": [],
        "related_modules": [],
        "validation": [],
        "risk": {
            "credentials": False,
            "destructive": False,
            "generated_settings": False,
            "installs": False,
            "network": False,
            "production_writes": False,
            "uploads": False,
            "profile": "read-only",
        },
        "external_access": {
            "source_systems": [],
            "credential_expectations": "none",
            "data_copied_locally": [],
            "attachments_retrieved": False,
        },
        "local_ai": {"use_cases": []},
        "strict_read_only_commands": [],
        "extensions": {},
    }


def command_spec(
    command_id: str = "inspect",
    *,
    argv: list[str] | None = None,
    effects: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": command_id,
        "argv": argv or ["python", "-B", ".agents/manage.py", "status", "--fast"],
        "timeout_seconds": 30,
        "working_directory": "repository",
        "effects": list(effects or []),
    }


def delegation_spec() -> dict[str, Any]:
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


def parallel_safety_spec() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "default_mode": "serial",
        "phase_policies": {
            "review": {
                "mode": "parallel-read-only",
                "max_workers": 2,
                "write_scopes": [],
                "runtime": {
                    "environment": "inherited-read-only",
                    "ports": "none",
                    "state_stores": "none",
                    "services": "none",
                },
                "provider": "none",
            }
        },
    }


def v3_parallel_workflow() -> dict[str, Any]:
    manifest = minimal_v3_manifest()
    manifest.update(
        {
            "kind": "workflow",
            "id": "parallel-workflow",
            "phases": [{"id": "review", "summary": "Review evidence."}],
            "worker_profiles": {
                "schema_version": 1,
                "extends": "portable-default",
                "mode": "auto-when-supported",
                "max_parallel_workers": 2,
                "phase_assignments": {"review": "evidence-medium"},
                "task_assignments": {},
                "delegation": delegation_spec(),
            },
            "parallel_safety": parallel_safety_spec(),
        }
    )
    return manifest


def test_delegation_contract_runtime_and_generated_schema_mutation_parity(_tmp):
    contract = contract_module()
    schema = contract.module_contract_schema()
    base = v3_parallel_workflow()

    cases: dict[str, tuple[dict[str, Any], bool]] = {"valid": (base, True)}
    for label, mutate in {
        "missing-delegation": lambda value: value["worker_profiles"].pop("delegation"),
        "undocumented-trigger": lambda value: value["worker_profiles"]["delegation"].__setitem__("trigger", "automatic"),
        "recursive-depth": lambda value: value["worker_profiles"]["delegation"].__setitem__("max_depth", 2),
        "unsafe-task-class": lambda value: value["worker_profiles"]["delegation"].__setitem__("eligible_task_classes", ["write-heavy"]),
        "missing-attestation": lambda value: value["worker_profiles"]["delegation"].__setitem__("require_model_attestation", False),
        "unknown-field": lambda value: value["worker_profiles"]["delegation"].__setitem__("multi_agent_v2", True),
    }.items():
        candidate = copy.deepcopy(base)
        mutate(candidate)
        cases[label] = (candidate, False)

    for label, (manifest, expected) in cases.items():
        runtime_errors, _warnings = contract.validate_v3(manifest)
        schema_validation_errors = contract.validate_schema_instance(manifest, schema=schema)
        assert (not runtime_errors) == (not schema_validation_errors), (
            label,
            runtime_errors,
            schema_validation_errors,
        )
        assert (not runtime_errors) is expected, (label, runtime_errors)


def test_worker_profile_contract_accepts_route_set_and_rejects_legacy_endpoint_axes(_tmp):
    contract = contract_module()
    schema = contract.module_contract_schema()
    valid = v3_parallel_workflow()
    valid["worker_profiles"]["profiles"] = {
        "custom-review": {
            "purpose": "Review bounded evidence.",
            "prompt_adapter": "validation",
            "context_budget": "lean",
            "tool_policy": "read-only",
            "expected_output": "Risk-ranked findings.",
            "validation_gate": "fresh-validation",
            "route_set": "review-high",
        }
    }
    runtime_errors, _warnings = contract.validate_v3(valid)
    schema_errors = contract.validate_schema_instance(valid, schema=schema)
    assert runtime_errors == []
    assert schema_errors == []

    for field, value in {
        "primary": {"provider": "codex", "model": "gpt", "reasoning_effort": "high"},
        "fallbacks": [],
        "provider": "openai",
        "reasoning_effort": "high",
    }.items():
        legacy = copy.deepcopy(valid)
        legacy["worker_profiles"]["profiles"]["custom-review"][field] = value
        runtime_errors, _warnings = contract.validate_v3(legacy)
        schema_errors = contract.validate_schema_instance(legacy, schema=schema)
        assert runtime_errors, field
        assert schema_errors, field


def test_parallel_safety_contract_runtime_and_generated_schema_mutation_parity(_tmp):
    contract = contract_module()
    schema = contract.module_contract_schema()
    base = v3_parallel_workflow()

    cases: dict[str, tuple[dict[str, Any], bool]] = {"read-only-valid": (base, True)}
    for label, mutate in {
        "missing-for-parallel-workflow": lambda value: value.pop("parallel_safety"),
        "read-only-write-scope": lambda value: value["parallel_safety"]["phase_policies"]["review"].__setitem__("write_scopes", ["runs/shared"]),
        "read-only-shared-port": lambda value: value["parallel_safety"]["phase_policies"]["review"]["runtime"].__setitem__("ports", "shared"),
        "read-only-service": lambda value: value["parallel_safety"]["phase_policies"]["review"]["runtime"].__setitem__("services", "per-worker"),
        "unknown-phase": lambda value: value["parallel_safety"]["phase_policies"].__setitem__("missing", copy.deepcopy(value["parallel_safety"]["phase_policies"]["review"])),
        "unknown-field": lambda value: value["parallel_safety"].__setitem__("container_runtime", "moo"),
    }.items():
        candidate = copy.deepcopy(base)
        mutate(candidate)
        cases[label] = (candidate, False)

    isolated = copy.deepcopy(base)
    isolated["commands"] = [
        command_spec("provision-worker", effects=["temporary_write"]),
        command_spec("cleanup-worker", effects=["temporary_write"]),
    ]
    isolated_policy = isolated["parallel_safety"]["phase_policies"]["review"]
    isolated_policy.update(
        {
            "mode": "parallel-isolated",
            "write_scopes": [".agents/tmp/{worker_id}"],
            "runtime": {
                "environment": "per-worker",
                "ports": "per-worker",
                "state_stores": "per-worker",
                "services": "per-worker",
            },
            "provider": "external",
            "provision_command_id": "provision-worker",
            "cleanup_command_id": "cleanup-worker",
        }
    )
    cases["isolated-valid"] = (isolated, True)
    missing_placeholder = copy.deepcopy(isolated)
    missing_placeholder["parallel_safety"]["phase_policies"]["review"]["write_scopes"] = [".agents/tmp/shared"]
    cases["isolated-shared-write-scope"] = (missing_placeholder, False)
    missing_command = copy.deepcopy(isolated)
    missing_command["parallel_safety"]["phase_policies"]["review"]["cleanup_command_id"] = "missing"
    cases["isolated-missing-command"] = (missing_command, False)

    for label, (manifest, expected) in cases.items():
        runtime_errors, _warnings = contract.validate_v3(manifest)
        schema_validation_errors = contract.validate_schema_instance(manifest, schema=schema)
        assert (not runtime_errors) == (not schema_validation_errors), (
            label,
            runtime_errors,
            schema_validation_errors,
        )
        assert (not runtime_errors) is expected, (label, runtime_errors)


def schema_errors(schema: dict[str, Any], value: Any) -> list[str]:
    """Use the official stdlib schema-plus-semantics validator."""

    contract = contract_module()
    return contract.validate_schema_instance(value, schema=schema)


def test_v3_schema_runtime_accept_minimal_contract(_tmp):
    contract = contract_module()
    manifest = minimal_v3_manifest()

    runtime_errors, warnings = contract.validate_v3(manifest)
    generated_schema = contract.module_contract_schema()

    assert runtime_errors == []
    assert warnings == []
    assert schema_errors(generated_schema, manifest) == []


def test_v3_schema_runtime_mutation_parity_for_strict_core_and_extensions(_tmp):
    contract = contract_module()
    generated_schema = contract.module_contract_schema()
    base = minimal_v3_manifest()

    def mutate(callback):
        value = copy.deepcopy(base)
        callback(value)
        return value

    cases = {
        "valid": (base, True),
        "missing-required": (mutate(lambda value: value.pop("summary")), False),
        "wrong-type": (mutate(lambda value: value.__setitem__("owners", "engineering")), False),
        "unknown-core": (mutate(lambda value: value.__setitem__("owner_hint", "engineering")), False),
        "namespaced-extension": (
            mutate(
                lambda value: value.__setitem__(
                    "extensions", {"example.com/demo": {"enabled": True}}
                )
            ),
            True,
        ),
        "unnamespaced-extension": (
            mutate(lambda value: value.__setitem__("extensions", {"demo": {}})),
            False,
        ),
    }

    for label, (manifest, expected) in cases.items():
        runtime_errors, _warnings = contract.validate_v3(manifest)
        generated_schema_errors = schema_errors(generated_schema, manifest)
        runtime_accepted = not runtime_errors
        schema_accepted = not generated_schema_errors
        assert runtime_accepted == schema_accepted, (
            label,
            runtime_errors,
            generated_schema_errors,
        )
        assert runtime_accepted is expected, (label, runtime_errors)


def test_v3_command_spec_schema_runtime_parity_and_argv_round_trip(_tmp):
    contract = contract_module()
    generated_schema = contract.module_contract_schema()
    argv = [
        "python",
        "-c",
        "print('two words')",
        "D:\\path with spaces\\fixture.json",
        "literal;not-a-shell-separator",
    ]
    base = minimal_v3_manifest()
    base["commands"] = [command_spec(argv=argv)]

    def mutate(callback):
        value = copy.deepcopy(base)
        callback(value["commands"][0])
        return value

    cases = {
        "valid": (base, True),
        "argv-is-string": (
            mutate(lambda command: command.__setitem__("argv", "python --version")),
            False,
        ),
        "empty-argv": (
            mutate(lambda command: command.__setitem__("argv", [])),
            False,
        ),
        "invalid-timeout": (
            mutate(lambda command: command.__setitem__("timeout_seconds", 0)),
            False,
        ),
        "unknown-working-directory": (
            mutate(lambda command: command.__setitem__("working_directory", "current")),
            False,
        ),
        "unknown-effect": (
            mutate(lambda command: command["effects"].append("process")),
            False,
        ),
        "wrong-effect-type": (
            mutate(lambda command: command["effects"].append(False)),
            False,
        ),
        "duplicate-effect": (
            mutate(lambda command: command.__setitem__("effects", ["network", "network"])),
            False,
        ),
    }

    for label, (manifest, expected) in cases.items():
        runtime_errors, _warnings = contract.validate_v3(manifest)
        generated_schema_errors = schema_errors(generated_schema, manifest)
        runtime_accepted = not runtime_errors
        schema_accepted = not generated_schema_errors
        assert runtime_accepted == schema_accepted, (
            label,
            runtime_errors,
            generated_schema_errors,
        )
        assert runtime_accepted is expected, (label, runtime_errors)

    normalized = contract.normalize_v3(copy.deepcopy(base))
    assert normalized["commands"][0]["argv"] == argv
    spaced = {"argv": ["tool", "arg with spaces"]}
    assert contract.command_argv(spaced) == ["tool", "arg with spaces"]
    assert contract.command_argv(["tool", "arg with spaces"]) == [
        "tool",
        "arg with spaces",
    ]
    assert contract.command_display(spaced) == '["tool","arg with spaces"]'


def test_v3_strict_read_only_ids_reject_unknown_and_every_declared_effect(_tmp):
    contract = contract_module()
    generated_schema = contract.module_contract_schema()
    base = minimal_v3_manifest()
    base["commands"] = [command_spec()]
    base["strict_read_only_commands"] = ["inspect"]

    cases: dict[str, tuple[dict[str, Any], bool]] = {"safe": (base, True)}
    unknown = copy.deepcopy(base)
    unknown["strict_read_only_commands"] = ["missing"]
    cases["unknown-id"] = (unknown, False)
    for effect_name in (
        "repository_write",
        "temporary_write",
        "network",
        "credentials",
        "install",
        "upload",
        "external_write",
    ):
        unsafe = copy.deepcopy(base)
        unsafe["commands"][0]["effects"].append(effect_name)
        cases[f"unsafe-{effect_name}"] = (unsafe, False)

    for label, (manifest, expected) in cases.items():
        runtime_errors, _warnings = contract.validate_v3(manifest)
        generated_schema_errors = schema_errors(generated_schema, manifest)
        runtime_accepted = not runtime_errors
        schema_accepted = not generated_schema_errors
        assert runtime_accepted == schema_accepted, (
            label,
            runtime_errors,
            generated_schema_errors,
        )
        assert runtime_accepted is expected, (label, runtime_errors)


def test_v3_generated_patterns_are_anchored_and_semantic_vocabulary_is_required(_tmp):
    contract = contract_module()
    schema = contract.module_contract_schema()
    vocabulary = schema.get("$vocabulary", {})
    semantics = schema.get(contract.SEMANTIC_KEYWORD)

    assert vocabulary.get(contract.SEMANTIC_VOCABULARY_URI) is True
    assert semantics == {
        "required": True,
        "rules": [
            "unique-command-ids",
            "strict-read-only-command-ids",
            "context-source-contract",
            "determinism-command-contract",
            "parallel-safety-contract",
        ],
    }
    command_id_pattern = schema["properties"]["commands"]["items"]["properties"]["id"]["pattern"]
    extension_pattern = schema["properties"]["extensions"]["propertyNames"]["pattern"]

    partial_id = minimal_v3_manifest()
    partial_id["commands"] = [command_spec(command_id="inspect!")]
    partial_extension = minimal_v3_manifest()
    partial_extension["extensions"] = {"example.com/demo!": {}}
    final_newline_id = minimal_v3_manifest()
    final_newline_id["commands"] = [command_spec(command_id="inspect\n")]
    final_newline_extension = minimal_v3_manifest()
    final_newline_extension["extensions"] = {"example.com/demo\n": {}}
    for manifest in (
        partial_id,
        partial_extension,
        final_newline_id,
        final_newline_extension,
    ):
        runtime_errors, _warnings = contract.validate_v3(manifest)
        official_errors = contract.validate_schema_instance(manifest, schema=schema)
        assert runtime_errors
        assert official_errors

    true_end = r")(?![\s\S])"
    assert command_id_pattern.startswith("^(?:") and command_id_pattern.endswith(true_end)
    assert extension_pattern.startswith("^(?:") and extension_pattern.endswith(true_end)


def test_v3_duplicate_command_ids_rejected_by_runtime_and_official_schema_semantics(_tmp):
    contract = contract_module()
    manifest = minimal_v3_manifest()
    manifest["commands"] = [
        command_spec(command_id="duplicate", argv=["python", "first.py"]),
        command_spec(command_id="duplicate", argv=["python", "second.py"]),
    ]

    runtime_errors, _warnings = contract.validate_v3(manifest)
    official_errors = contract.validate_schema_instance(
        manifest,
        schema=contract.module_contract_schema(),
    )

    assert any("duplicate command id" in error for error in runtime_errors)
    assert any("duplicate command id" in error for error in official_errors)


def test_conventional_context_uses_compact_packet_as_resume_boundary(_tmp):
    contract = contract_module()
    context = contract.conventional_context("user-story-workflow")

    assert context["sources"]
    assert {
        source["load_policy"] for source in context["sources"]
    } == {"handle_only"}


def test_v3_typed_routing_template_context_and_determinism_shapes(_tmp):
    contract = contract_module()
    generated_schema = contract.module_contract_schema()
    base = minimal_v3_manifest()
    base.update(
        {
            "routing": {
                "terms": ["demo", "skill"],
                "activation_terms": ["demo skill"],
                "threshold": 2,
                "winner_margin": 1,
            },
            "template_layers": {
                "default_template": "plan.md",
                "override_roots": ["docs/project/workflow-overrides/demo"],
                "preset_roots": ["automations/demo/presets"],
                "profiles": {
                    "lean": {"template_roots": ["templates/lean"]},
                },
                "priorities": {
                    "project-override": 0,
                    "workflow-preset": 50,
                    "workflow-default": 100,
                },
                "conflict_policy": "error",
            },
            "context": {
                "budgets": {"lean": 2500, "standard": 8000},
                "sources": [
                    {
                        "id": "project-context",
                        "artifact_role": "project-context",
                        "path": "docs/project/project-context.md",
                        "load_policy": "must_open",
                        "critical_category": "project",
                        "budget_ref": "standard",
                        "preserve_coordinates": True,
                    }
                ],
            },
            "determinism": {
                "replay_commands": ["inspect"],
                "allowed_temporary_effects": [
                    {
                        "path": "scratch/result.json",
                        "recursive": False,
                        "operations": ["create", "modify", "delete"],
                    }
                ],
                "volatile_json_pointers": ["/timings/elapsed_seconds"],
                "environment_requirements": {
                    "minimum_python": "3.12",
                    "executables": ["git"],
                    "platforms": ["windows", "linux", "macos"],
                },
            },
        }
    )
    base["commands"] = [command_spec()]
    base["strict_read_only_commands"] = ["inspect"]

    cases: dict[str, tuple[dict[str, Any], bool]] = {"valid": (base, True)}
    bad_routing = copy.deepcopy(base)
    bad_routing["routing"]["threshold"] = "two"
    cases["routing-threshold"] = (bad_routing, False)
    bad_profile = copy.deepcopy(base)
    bad_profile["template_layers"]["profiles"]["lean"] = ["templates/lean"]
    cases["template-profile"] = (bad_profile, False)
    bad_context = copy.deepcopy(base)
    bad_context["context"]["sources"][0]["load_policy"] = "sometimes"
    cases["context-load-policy"] = (bad_context, False)
    bad_determinism = copy.deepcopy(base)
    bad_determinism["determinism"]["volatile_json_pointers"] = [1]
    cases["determinism-pointer"] = (bad_determinism, False)
    unknown_replay = copy.deepcopy(base)
    unknown_replay["determinism"]["replay_commands"] = ["missing"]
    cases["determinism-unknown-replay"] = (unknown_replay, False)
    non_strict_replay = copy.deepcopy(base)
    non_strict_replay["commands"].append(command_spec("other"))
    non_strict_replay["determinism"]["replay_commands"] = ["other"]
    cases["determinism-non-strict-replay"] = (non_strict_replay, False)
    unsafe_temp_path = copy.deepcopy(base)
    unsafe_temp_path["determinism"]["allowed_temporary_effects"][0]["path"] = "../escape"
    cases["determinism-unsafe-temporary-path"] = (unsafe_temp_path, False)
    overlapping_pointer = copy.deepcopy(base)
    overlapping_pointer["determinism"]["volatile_json_pointers"] = ["/timings", "/timings/elapsed_seconds"]
    cases["determinism-overlapping-pointers"] = (overlapping_pointer, False)
    invalid_pointer_escape = copy.deepcopy(base)
    invalid_pointer_escape["determinism"]["volatile_json_pointers"] = ["/timings/~2bad"]
    cases["determinism-invalid-pointer-escape"] = (invalid_pointer_escape, False)
    invalid_environment = copy.deepcopy(base)
    invalid_environment["determinism"]["environment_requirements"]["minimum_python"] = "3.x"
    cases["determinism-invalid-environment"] = (invalid_environment, False)
    unknown_nested = copy.deepcopy(base)
    unknown_nested["routing"]["fallback"] = True
    cases["unknown-routing-field"] = (unknown_nested, False)

    for label, (manifest, expected) in cases.items():
        runtime_errors, _warnings = contract.validate_v3(manifest)
        generated_schema_errors = schema_errors(generated_schema, manifest)
        runtime_accepted = not runtime_errors
        schema_accepted = not generated_schema_errors
        assert runtime_accepted == schema_accepted, (
            label,
            runtime_errors,
            generated_schema_errors,
        )
        assert runtime_accepted is expected, (label, runtime_errors)


def test_v3_authoritative_core_objects_are_recursively_strict(_tmp):
    contract = contract_module()
    schema = contract.module_contract_schema()
    manifest = minimal_v3_manifest()
    manifest.update(
        {
            "compatibility": {
                "codex": "required",
                "github_copilot": "required",
                "claude_code": "required",
            },
            "dependencies": [
                {
                    "name": "Python",
                    "purpose": "Runs deterministic tools.",
                    "version": "3.12+",
                    "optional": False,
                }
            ],
            "phases": [
                {
                    "id": "validate",
                    "summary": "Validate the result.",
                    "entry_checks": ["inputs ready"],
                    "exit_checks": ["tests pass"],
                    "evidence": ["test output"],
                    "hooks": ["phase-pre"],
                }
            ],
            "phase_lifecycle": {
                "events": ["phase-pre"],
                "state_fields": ["status"],
                "required_handoff_fields": ["next_action"],
            },
            "worker_profiles": {
                "schema_version": 1,
                "extends": "portable-default",
                "mode": "auto-when-supported",
                "max_parallel_workers": 1,
                "phase_assignments": {"validate": "validation-local"},
                "task_assignments": {},
                "delegation": delegation_spec(),
            },
            "provenance": {
                "source": "repo-owned",
                "license": "repository",
                "introduced": "2026-01-01",
                "updated": "2026-07-11",
                "reviewed_at": "2026-07-11",
                "attestations": ["python -B check.py"],
                "source_hashes": {"source": "abc123"},
            },
            "quality": {
                "eval_suites": [
                    {"path": "suites/evals.json", "purpose": "Contract checks."},
                    "suites/compat.json",
                ],
                "self_tests": [
                    {"path": "scripts/run_self_tests.py", "purpose": "Unit checks."}
                ],
                "eval_gap_rationale": "No live model execution is required.",
            },
            "context_evidence": {
                "required": True,
                "start_queries": [
                    {
                        "id": "contract",
                        "question": "What defines this module?",
                        "scope": "repo",
                        "required": True,
                        "fallback_paths": ["module.json"],
                    }
                ],
                "resume_queries": [],
                "finish_queries": [],
            },
            "tasks": [
                {
                    "id": "validate",
                    "summary": "Run validation.",
                    "phase": "validate",
                    "depends_on": [],
                }
            ],
            "hooks": [
                {
                    "id": "preflight",
                    "event": "phase-pre",
                    "command": "python -B .agents/manage.py check",
                    "required": True,
                    "timeout_seconds": 30,
                    "evidence_path": "validation/preflight.json",
                }
            ],
            "input_schema": {
                "properties": {
                    "request": {
                        "type": "string",
                        "description": "Requested outcome.",
                    },
                    "mode": {
                        "type": "enum",
                        "description": "Execution mode.",
                        "values": ["check", "write"],
                    },
                },
                "required": ["request"],
            },
            "gates": [
                {
                    "id": "validation",
                    "type": "validation",
                    "summary": "Validation passes.",
                    "evidence": "test output",
                    "required": True,
                }
            ],
            "branch_policy": {
                "pattern": "^(feature|fix)/[a-z0-9-]+$",
            },
            "integrations": [
                "workflow-manager",
                {"id": "local-ai-helper", "descriptor": "integrations/local-ai.json"},
            ],
            "updated": "2026-07-11",
        }
    )
    runtime_errors, _warnings = contract.validate_v3(manifest)
    official_errors = contract.validate_schema_instance(manifest, schema=schema)
    assert runtime_errors == [], runtime_errors
    assert official_errors == [], official_errors

    mutations = {
        "compatibility": lambda value: value["compatibility"].__setitem__("other", "required"),
        "dependency": lambda value: value["dependencies"][0].__setitem__("url", "https://example.invalid"),
        "risk": lambda value: value["risk"].__setitem__("filesystem", True),
        "external-access": lambda value: value["external_access"].__setitem__("endpoint", "prod"),
        "local-ai": lambda value: value["local_ai"].__setitem__("model", "default"),
        "phase": lambda value: value["phases"][0].__setitem__("owner", "agent"),
        "phase-lifecycle": lambda value: value["phase_lifecycle"].__setitem__("mode", "auto"),
        "worker-profiles": lambda value: value["worker_profiles"].__setitem__("provider", "openai"),
        "provenance": lambda value: value["provenance"].__setitem__("repository", "demo"),
        "quality": lambda value: value["quality"].__setitem__("score", 1),
        "quality-item": lambda value: value["quality"]["eval_suites"][0].__setitem__("owner", "qa"),
        "context_evidence": lambda value: value["context_evidence"].__setitem__("provider", "local"),
        "context-evidence-query": lambda value: value["context_evidence"]["start_queries"][0].__setitem__("limit", 3),
        "task": lambda value: value["tasks"][0].__setitem__("status", "pending"),
        "hook": lambda value: value["hooks"][0].__setitem__("shell", True),
        "input-schema": lambda value: value["input_schema"].__setitem__("additionalProperties", False),
        "input-property": lambda value: value["input_schema"]["properties"]["request"].__setitem__("default", "demo"),
        "gate": lambda value: value["gates"][0].__setitem__("owner", "qa"),
        "branch-policy": lambda value: value["branch_policy"].__setitem__("prefix", "feature/"),
        "integration": lambda value: value["integrations"][1].__setitem__("version", "1"),
    }
    for label, mutate in mutations.items():
        candidate = copy.deepcopy(manifest)
        mutate(candidate)
        runtime_errors, _warnings = contract.validate_v3(candidate)
        official_errors = contract.validate_schema_instance(candidate, schema=schema)
        assert runtime_errors, label
        assert official_errors, label

    extension = copy.deepcopy(manifest)
    extension["extensions"] = {
        "skills-harness/demo": {
            "arbitrary": {"module_specific": [1, "two", {"three": True}]}
        }
    }
    assert contract.validate_v3(extension)[0] == []
    assert contract.validate_schema_instance(extension, schema=schema) == []


def test_context_spec_cross_field_rules_match_runtime_and_generated_schema(_tmp):
    contract = contract_module()
    schema = contract.module_contract_schema()
    manifest = minimal_v3_manifest()
    manifest["kind"] = "workflow"
    manifest["context"] = {
        "budgets": {"critical": 2_000, "evidence": 4_000},
        "sources": [
            {
                "id": "run-plan",
                "artifact_role": "implementation-plan",
                "path": "automations/demo/runs/<run-id>/plan.md",
                "load_policy": "must_open",
                "critical_category": "resume-critical",
                "budget_ref": "critical",
                "preserve_coordinates": True,
            },
            {
                "id": "validation-evidence",
                "artifact_role": "validation-evidence",
                "pattern": "automations/demo/runs/<run-id>/validation/**/*.json",
                "load_policy": "handle_only",
                "critical_category": "evidence",
                "budget_ref": "evidence",
                "preserve_coordinates": False,
            },
        ],
    }
    assert contract.validate_v3(manifest)[0] == []
    assert contract.validate_schema_instance(manifest, schema=schema) == []

    mutations = {
        "path-and-pattern": lambda value: value["context"]["sources"][0].__setitem__(
            "pattern", "**/*.md"
        ),
        "missing-path-and-pattern": lambda value: value["context"]["sources"][0].pop(
            "path"
        ),
        "duplicate-source-id": lambda value: value["context"]["sources"][1].__setitem__(
            "id", "run-plan"
        ),
        "unknown-budget-reference": lambda value: value["context"]["sources"][0].__setitem__(
            "budget_ref", "missing"
        ),
    }
    for label, mutate in mutations.items():
        candidate = copy.deepcopy(manifest)
        mutate(candidate)
        runtime_errors, _warnings = contract.validate_v3(candidate)
        generated_errors = contract.validate_schema_instance(candidate, schema=schema)
        assert runtime_errors, label
        assert generated_errors, label


def test_non_v3_module_contracts_are_rejected_without_adaptation(_tmp):
    contract = contract_module()
    manifest = minimal_v3_manifest()
    manifest["schema_version"] = 2

    normalized, errors, warnings = contract.normalize_module_contract(manifest)

    assert normalized == {}
    assert errors == ["module.json schema_version must be 3."]
    assert warnings == []


def test_current_repository_v3_manifests_validate(_tmp):
    contract = contract_module()
    root = Path(__file__).resolve().parents[5]
    paths = sorted(root.glob(".agents/skills/*/module.json"))
    paths.extend(sorted(root.glob("automations/*/module.json")))
    assert paths

    for path in paths:
        text = path.read_text(encoding="utf-8")
        manifest = json.loads(text)
        assert manifest["schema_version"] == 3, path
        normalized, errors, warnings = contract.normalize_module_contract(manifest)
        assert errors == [], (path, errors)
        assert normalized["schema_version"] == 3
        assert warnings == [], (path, warnings)
        assert text == contract.canonical_module_json(normalized), path


def test_current_repository_commands_have_executable_argv_and_known_temp_effects(_tmp):
    root = Path(__file__).resolve().parents[5]
    manifests = {
        path.relative_to(root).as_posix(): json.loads(path.read_text(encoding="utf-8"))
        for path in (
            root / ".agents/skills/skill-manager/module.json",
            root / ".agents/skills/workflow-manager/module.json",
            root / "automations/feedback-improvement-workflow/module.json",
            root / "automations/diagram-review-workflow/module.json",
            root / "automations/local-ai-benchmark-workflow/module.json",
        )
    }

    for path, manifest in manifests.items():
        for command in manifest["commands"]:
            assert all("[" not in token and "]" not in token for token in command["argv"]), (
                path,
                command,
            )

    expected_temp_commands = {
        ".agents/skills/skill-manager/module.json": [
            ["python", "-B", "scripts/run_self_tests.py"],
        ],
        ".agents/skills/workflow-manager/module.json": [
            ["python", "-B", "scripts/run_self_tests.py"],
        ],
        "automations/feedback-improvement-workflow/module.json": [
            [
                "python",
                "-B",
                ".agents/manage.py",
                "eval-workflow",
                "--name",
                "feedback-improvement-workflow",
                "--suite",
                "automations/feedback-improvement-workflow/suites/workflow-evals.json",
            ],
            [
                "python",
                "-B",
                ".agents/manage.py",
                "workflow",
                "scorecard",
                "--name",
                "feedback-improvement-workflow",
                "--format",
                "json",
            ],
        ],
        "automations/diagram-review-workflow/module.json": [
            [
                "python",
                "-B",
                ".agents/manage.py",
                "eval-workflow",
                "--name",
                "diagram-review-workflow",
                "--suite",
                "automations/diagram-review-workflow/suites/workflow-evals.json",
            ],
        ],
    }
    for path, expected_commands in expected_temp_commands.items():
        by_argv = {
            tuple(command["argv"]): command for command in manifests[path]["commands"]
        }
        for argv in expected_commands:
            assert by_argv[tuple(argv)]["effects"] == ["temporary_write"], (path, argv)


def test_generated_module_schema_matches_authoritative_field_spec(_tmp):
    contract = contract_module()
    root = Path(__file__).resolve().parents[5]
    generated = json.loads(
        (root / ".agents/skills/skill-manager/assets/schemas/module.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert generated == contract.module_contract_schema()


def test_conventional_template_materializer_rejects_resolved_child_escape(tmp):
    contract = contract_module()
    workflow_dir = tmp / "automations/demo-workflow"
    candidate = workflow_dir / "templates/plan.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("# Candidate\n", encoding="utf-8")
    outside = tmp / "outside/plan.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("# Outside\n", encoding="utf-8")
    manifest = {
        "kind": "workflow",
        "id": "demo-workflow",
        "template_layers": contract.conventional_template_layers("demo-workflow"),
    }
    original_resolve = Path.resolve
    outside_resolved = original_resolve(outside, strict=False)

    def resolve_with_escape(path, strict=False):
        if path == candidate:
            return outside_resolved
        return original_resolve(path, strict=strict)

    with patch.object(Path, "resolve", resolve_with_escape):
        contract.materialize_conventional_template_availability(
            manifest,
            repository_root=tmp,
            workflow_dir=workflow_dir,
        )

    assert manifest["template_layers"]["profiles"] == {}


def test_conventional_template_materializer_rejects_real_symlink_escape_when_supported(tmp):
    contract = contract_module()
    workflow_dir = tmp / "automations/demo-workflow"
    candidate = workflow_dir / "templates/plan.md"
    candidate.parent.mkdir(parents=True)
    outside = tmp / "outside/plan.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("# Outside\n", encoding="utf-8")
    try:
        candidate.symlink_to(outside)
    except OSError:
        return
    manifest = {
        "kind": "workflow",
        "id": "demo-workflow",
        "template_layers": contract.conventional_template_layers("demo-workflow"),
    }

    contract.materialize_conventional_template_availability(
        manifest,
        repository_root=tmp,
        workflow_dir=workflow_dir,
    )

    assert manifest["template_layers"]["profiles"] == {}


def test_shared_effect_inference_uses_command_and_suite_semantics(_tmp):
    contract = contract_module()
    root = Path(__file__).resolve().parents[5]
    cases = [
        (
            "python -B scripts/analyze.py",
            {"id": "credentialed-module"},
            [],
            None,
        ),
        (
            "python -B scripts/setup_local_ai.py",
            {"id": "local-ai-helper"},
            ["temporary_write", "network", "credentials", "install"],
            None,
        ),
        (
            "python -B scripts/navigation/install_navigation_workflow.py",
            {"id": "repo-navigation"},
            ["repository_write", "install"],
            None,
        ),
        (
            "python -B scripts/setup_vscode_mermaid_preview.py",
            {"id": "mermaid-diagrams-azure-devops"},
            ["repository_write", "install"],
            None,
        ),
        (
            "python -B scripts/update_navigation.py --target . --write",
            {"id": "navigation"},
            ["repository_write"],
            None,
        ),
        (
            "python -B scripts/generate.py --target D:/consumer --write",
            {"id": "generator"},
            ["external_write"],
            None,
        ),
        (
            "python -B scripts/run_self_tests.py",
            {"id": "skill-manager"},
            ["temporary_write"],
            None,
        ),
        (
            "python -B scripts/run_self_tests.py",
            {"id": "workflow-manager"},
            ["temporary_write"],
            None,
        ),
        (
            "python -B .agents/manage.py determinism-check --changed --summary --compact --format json",
            {"id": "skill-manager"},
            ["temporary_write"],
            None,
        ),
        (
            "python -B .agents/manage.py workflow scorecard --name feedback-improvement-workflow --format json",
            {"id": "feedback-improvement-workflow"},
            ["temporary_write"],
            None,
        ),
        (
            "python -B .agents/manage.py workflow scorecard --name feedback-improvement-workflow --no-lifecycle --format json",
            {"id": "feedback-improvement-workflow"},
            [],
            None,
        ),
        (
            "python -B .agents/manage.py eval-workflow --name feedback-improvement-workflow --suite automations/feedback-improvement-workflow/suites/workflow-evals.json",
            {"id": "feedback-improvement-workflow"},
            ["temporary_write"],
            root,
        ),
        (
            "python -B .agents/manage.py eval-workflow --name diagram-review-workflow --suite automations/diagram-review-workflow/suites/workflow-evals.json",
            {"id": "diagram-review-workflow"},
            ["temporary_write"],
            root,
        ),
        (
            "python -B scripts/sync_skill_routing.py",
            {"id": "skill-manager"},
            ["repository_write"],
            None,
        ),
        (
            "python -B scripts/sync_skill_routing.py --check",
            {"id": "skill-manager"},
            [],
            None,
        ),
        (
            "python -B scripts/sync_automation_routing.py",
            {"id": "workflow-manager"},
            ["repository_write"],
            None,
        ),
        (
            "python -B scripts/sync_automation_routing.py --check",
            {"id": "workflow-manager"},
            [],
            None,
        ),
        (
            "python -B .agents/manage.py benchmark routing-eval --suite automations/agent-benchmarking/suites/routing-evidence-real-use.json --check-suite --format json",
            {"id": "agent-benchmarking"},
            [],
            None,
        ),
        (
            "python -B .agents/skills/agent-benchmarking/scripts/three_arm_full_run.py aggregate --protocol protocol.json --trial-index trials.json --output summary.json --format json",
            {"id": "agent-benchmarking"},
            ["repository_write"],
            None,
        ),
        (
            "python -B scripts/create_workflow.py",
            {"id": "workflow-manager"},
            [],
            None,
        ),
        (
            "python -B scripts/workflow_support/start_checklist.py",
            {"id": "workflow-manager"},
            [],
            None,
        ),
        (
            "python -B scripts/sync_references.py",
            {"id": "external-reference-manager"},
            ["network"],
            None,
        ),
        (
            "python -B scripts/sync_references.py --manifest references.json --output-root references --no-fetch --format json",
            {"id": "external-reference-manager"},
            [],
            None,
        ),
        (
            "python -B scripts/analyze_path.py",
            {"id": "credentialed-module"},
            [],
            None,
        ),
    ]
    for command_text, manifest, expected, command_root in cases:
        actual = contract.infer_command_effects(
            {"argv": contract.lexical_argv_from_text(command_text)},
            manifest,
            root=command_root,
        )
        assert actual == expected, (command_text, expected, actual)

    mixed_case_suite = _tmp / "MixedCase" / "Lifecycle.json"
    mixed_case_suite.parent.mkdir(parents=True)
    mixed_case_suite.write_text(
        json.dumps({"assertions": [{"type": "workflow_lifecycle_smoke_ok"}]}),
        encoding="utf-8",
    )
    assert contract.infer_command_effects(
        {
            "argv": [
                "python",
                "-B",
                ".agents/manage.py",
                "eval-workflow",
                "--suite",
                "MixedCase/Lifecycle.json",
            ]
        },
        {"id": "mixed-case-workflow"},
        root=_tmp,
    ) == ["temporary_write"]
