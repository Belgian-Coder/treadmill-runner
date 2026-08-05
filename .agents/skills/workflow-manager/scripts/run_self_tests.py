#!/usr/bin/env python3
"""Self-tests for workflow-manager helpers (write/temp fixtures)."""

import json
import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import threading
import time
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True

import create_workflow
import eval_workflow
import index_workflow_runs
import parallel_safety_fixture
import routing_contract
import sync_automation_routing
import validate_automations
import workflow_manager_common as common
import workflow_plan_check
import workflow_context_evidence
import workflow_repo_manager
import workflow_run_support
import workflow_context_packet
import workflow_checkpoint
from workflow_support import analytics as workflow_analytics
from validation_support import manifests, module_checks, reporting, scanning
from workflow_support import cli_parser
from workflow_support import context_budget as workflow_context_budget
from workflow_support import hooks as workflow_hooks
from workflow_support import intent_builder
from workflow_support import plan_smoke
from workflow_support import run_common
from workflow_support import scorecard as workflow_scorecard
from workflow_support import smoke_domain
from workflow_support import smoke as workflow_smoke
from workflow_support import story_bug_quality
from workflow_support import start_checklist
from workflow_support import template_layers
from workflow_support import validation_packets
from workflow_support import workers as workflow_workers


FIXTURE_NEXT_ACTION = "No next action."
FIXTURE_PR_SUMMARY = "Delivered fixture."
FIXTURE_SELF_TEST_RESULT = "workflow-manager self-tests passed."
FIXTURE_LESSON = "No reusable lesson: fixture."
WORKFLOW_EXECUTE_SUMMARY = "Execute."
APPROVAL_PENDING = "Approval status: pending"
APPROVAL_PLANNED = "Approval status: planned"
APPROVAL_APPROVED = "Approval status: approved"
AC_MAPPING = "Acceptance Criteria Mapping"
ADD_PLAN_CHECK = "Add plan check"
FIXTURE_PROOF_LESSON = "Prefer fixture-backed proof"
MISSING_OUT_OF_SCOPE_PLAN = "# Plan\n\n## Scope\n\n- missing"
VALIDATE_AUTOMATIONS_COMMAND = "python -B .agents/manage.py validate-automations"
VALIDATE_AUTOMATIONS_NAME = f"{VALIDATE_AUTOMATIONS_COMMAND} --name "
MISSING_ALWAYS_LOAD = "missing structured section ## Always Load"
CURRENT_PHASE_MISSING = "current phase section is missing"
HOOK_MARKER_ARGS = "--run-dir {run_dir} --event {event} --hook-id {hook_id} --phase {phase}"
HOOK_AUDIT_ARGS = "--name {workflow} --run-dir {run_dir} --event {event} --hook-id {hook_id} --format json"
PLAN_PHASE_INSTRUCTIONS = (
    "\n## Phase: plan\n\n"
    "- [ ] Read: `WORKFLOW.md`.\n"
    "  Do: shape the plan.\n"
    "  Write: update `run.json`.\n"
    "  Done when: plan evidence is recorded.\n"
    "  If blocked: record the blocker.\n"
)
STORY_AC_BASE_ROW = "| AC1 | Add shared check | workflow plan-check output"
STORY_AC_PROOF_ROW = f"{STORY_AC_BASE_ROW} | command help |"
STORY_AC_MISSING_DOC_ROW = f"{STORY_AC_BASE_ROW} | |"
STORY_SECURITY_PROOF_ROW = "| Roles, authorization, or tenant boundaries | No impact | planning only |"
STORY_WORK_ITEM_ROW = f"| WP2 | {ADD_PLAN_CHECK} | Preserve workflow behavior | WP1 | No unrelated changes | workflow-manager | self-test | command reports ok/fail | Record validation handoff |"
TICKET_INFO_FIXTURE = "# Ticket\n\n## Out Of Scope\n\n- fixture"
OUT_OF_SCOPE_FIXTURE_SECTION = "## Out Of Scope\n\n- fixture"
OUT_OF_SCOPE_NOT_IN_SCOPE_SECTION = "## Out Of Scope\n\n- not in scope"
OUT_OF_SCOPE_DEFERRED_SECTION = "## Out Of Scope\n\n- deferred"
STALE_CONTEXT_PREFIX = "context packet is stale"
STALE_CONTEXT_ISSUE = f"{STALE_CONTEXT_PREFIX}; run workflow context --write"
MERMAID_START_DONE = 'graph TD;\n  A["Start"] --> B["Done"];'
COMPACT_JSON = ["--summary", "--compact", "--format", "json"]
WORKFLOW_FIXTURE_NAME = "story-flow"
REPO_ROOT = Path(__file__).resolve().parents[4]
UNLISTED_INTERNAL_SELF_TESTS = {
    "test_validation_requires_copyable_workflow_prompts",
    "test_strict_phase_quality_promotes_step_warnings",
}


def runtime_observation(
    *,
    host_surface="codex",
    model_provider="openai",
    model="gpt-5.5",
    observed_deliberation="medium",
    capabilities=None,
    host_source="host-runtime",
    model_source="host-runtime",
    include_host=True,
    include_model=True,
    evidence_path="automations/story-flow/runs/run-a/validation/runtime-observation.json",
):
    packet = {
        "schema_version": 1,
        "tool": "workflow-manager.runtime-observation",
        "workflow": "story-flow",
        "run_id": "run-a",
        "phase": "execute",
        "evidence_path": evidence_path,
    }
    if include_host:
        packet["host"] = {
            "attested": True,
            "source": host_source,
            "surface": host_surface,
            "capabilities": list(capabilities or []),
        }
    if include_model:
        packet["model"] = {
            "attested": True,
            "source": model_source,
            "provider": model_provider,
            "model": model,
            "observed_deliberation": observed_deliberation,
        }
    return packet


def delegation_gate_evidence(
    *,
    host_surface="codex",
    model_provider="openai",
    model="gpt-5.5",
    execution_mode="native-subagents",
    provider_adapter_id="codex-rollout-v1",
    provider_adapter_status="implemented",
):
    return {
        "schema_version": 2,
        "tool": "agent-benchmarking.delegation-gate",
        "gate_ref": "delegation-balanced-v1",
        "status": "passed",
        "task_class": "independent-read-heavy",
        "selected_protocol_arm": "harness_no_local_ai",
        "selected_arm": "delegated",
        "token_provenance": "provider_telemetry",
        "model_attested": True,
        "thread_tree_complete": True,
        "minimum_trials_per_arm": 3,
        "fallback": "single-agent",
        "comparisons": {"harness_no_local_ai": {"passed": True}},
        "host_surface": host_surface,
        "model_provider": model_provider,
        "model": model,
        "execution_mode": execution_mode,
        "provider_adapter_id": provider_adapter_id,
        "provider_adapter_status": provider_adapter_status,
    }


def hook_marker_command(workflow="{workflow}"):
    return f"python -B automations/{workflow}/scripts/write_hook_marker.py {HOOK_MARKER_ARGS}"


def hook_audit_command():
    return f"python -B .agents/manage.py workflow hook-audit {HOOK_AUDIT_ARGS}"


def pr_description(summary=FIXTURE_PR_SUMMARY, validation=FIXTURE_SELF_TEST_RESULT, lesson=FIXTURE_LESSON):
    return f"""# Pull Request

## Summary

{summary}

## Validation

{validation}

## Plan Variance

| Package | Planned | Actual | Reason | Approval Impact | Validation Impact |
|---|---|---|---|---|---|
| No variance | fixture plan | fixture result | execution matched plan | none accepted by owner | validation unchanged |

## Independent Review Evidence

| Axis | Reviewer Or Method | Result | Evidence | Disposition |
|---|---|---|---|---|
| Spec and plan compliance | fixture review | passed | REPORT.md | accepted |
| Standards and maintainability | fixture review | passed | REPORT.md | accepted |
| Security and authority | fixture review | skipped: fixture has no security-sensitive changes | REPORT.md | accepted by owner |
| Validation and generated artifacts | fixture review | passed | REPORT.md | accepted |

## Reusable Lessons

{lesson}
"""


def hook_spec(hook_id, event, command=None):
    return {
        "id": hook_id,
        "event": event,
        "command": command or hook_marker_command(),
        "required": True,
        "timeout_seconds": 30,
    }


def command_spec(command, *, effects=None):
    argv = command.split()
    return {
        "id": manifests.module_contract_v3.command_id_for_argv(argv),
        "argv": argv,
        "timeout_seconds": 300,
        "working_directory": "repository",
        "effects": list(effects or []),
    }


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_guidance_savings_fixture(
    root,
    *,
    min_saved_percent=25,
    default_guidance_budget_tokens=5000,
    phase_budgets=None,
):
    large_orientation = "\n".join(f"- Generated orientation detail {index}" for index in range(240))
    default_guidance_files = [
        "AGENTS.md",
        ".agents/routing.md",
        "automations/routing.md",
        "docs/fixture-guidance/HANDOFF.md",
    ]
    broad_guidance_files = [
        *default_guidance_files,
        "README.md",
        "docs/agent-start.md",
        "docs/start-here.md",
        "docs/fixture-guidance/NAVIGATION.md",
        "docs/fixture-guidance/TECHNICAL_CONTEXT.md",
        "docs/fixture-guidance/CONVENTIONS.md",
    ]
    for rel_path, text in {
        "AGENTS.md": "# Agents\n\nRoute through compact files.\n",
        ".agents/routing.md": "# Skill Routing\n\n| Skill | Path |\n|---|---|\n| demo | .agents/skills/demo-skill/SKILL.md |\n",
        "automations/routing.md": "# Workflow Routing\n\n| Workflow | Path |\n|---|---|\n| story-flow | automations/story-flow/WORKFLOW.md |\n",
        "README.md": "# README\n\nBeginner repository overview.\n",
        "docs/agent-start.md": "# Agent Start\n\nBeginner setup notes.\n",
        "docs/start-here.md": "# Start Here\n\nBeginner repository notes.\n",
        "docs/fixture-guidance/HANDOFF.md": "# Handoff\n\nRead this compact file first.\n",
        "docs/fixture-guidance/NAVIGATION.md": "# Navigation\n\n" + large_orientation,
        "docs/fixture-guidance/TECHNICAL_CONTEXT.md": "# Technical Context\n\n" + large_orientation,
        "docs/fixture-guidance/CONVENTIONS.md": "# Conventions\n\n" + large_orientation,
    }.items():
        write_text(root / rel_path, text)
    project_policy = common.repo_policy.default_policy_document()
    cost = project_policy["cost_policy"]
    cost["guidance"]["default"]["budget_tokens"] = default_guidance_budget_tokens
    cost["guidance"]["default"]["files"] = default_guidance_files
    cost["guidance"]["baseline"]["files"] = broad_guidance_files
    cost["guidance"]["minimum_saved_percent"] = min_saved_percent
    cost["budgets"]["phases"]["overrides"] = phase_budgets or {
        "routing": 1500,
        "planning": 6000,
        "implementation": 8000,
        "test-authoring": 6000,
        "validation": 3000,
        "evidence": 12000,
        "handoff": 2500,
    }
    write_json(root / ".agents" / "project-policy.json", project_policy)


def write_feedback_manager(root):
    write_text(
        root / ".agents" / "manage.py",
        """
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("group")
parser.add_argument("action")
parser.add_argument("--target-kind", required=True)
parser.add_argument("--target", required=True)
parser.add_argument("--summary", required=True)
parser.add_argument("--bad", required=True)
parser.add_argument("--good", default="")
parser.add_argument("--context", action="append", default=[])
parser.add_argument("--trigger-command", default="")
parser.add_argument("--failure-type", default="")
parser.add_argument("--first-failing-fact", default="")
parser.add_argument("--suggested-next-command", default="")
parser.add_argument("--source-tool", default="")
parser.add_argument("--format", default="json")
args = parser.parse_args()
if args.group != "feedback" or args.action != "record":
    raise SystemExit(2)
entry = {
    "schema_version": 1,
    "target_kind": args.target_kind,
    "target": args.target,
    "summary": args.summary,
    "what_worked": args.good,
    "what_failed": args.bad,
    "context_paths": args.context,
    "trigger_command": args.trigger_command,
    "failure_type": args.failure_type,
    "first_failing_fact": args.first_failing_fact,
    "suggested_next_command": args.suggested_next_command,
    "source_tool": args.source_tool,
}
path = Path(".agents/local-ai/cache/feedback/failure-feedback.jsonl")
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("a", encoding="utf-8", newline="\\n") as handle:
    handle.write(json.dumps(entry, sort_keys=True) + "\\n")
print(json.dumps({"ok": True, "entry": entry}))
""",
    )


def module_file(module_dir, *parts):
    return module_dir.joinpath(*parts)


def template_path(module_dir, name="plan.md"):
    return module_file(module_dir, "templates", name)


def write_story_bug_templates(
    module_dir,
    section=OUT_OF_SCOPE_NOT_IN_SCOPE_SECTION,
    names=("ticket-info.md", "plan.md", "pr-description.md"),
):
    for name in names:
        write_text(template_path(module_dir, name), f"# {name}\n\n{section}")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_dir_for(module_dir, run_id="run-a"):
    return module_file(module_dir, "runs", run_id)


def run_file(run_dir, *parts):
    return run_dir.joinpath(*(parts or ("run.json",)))


def run_plan_path(module_dir, run_id="run-a"):
    return run_file(run_dir_for(module_dir, run_id), "plan.md")


def workflow_dir(root, workflow_name=WORKFLOW_FIXTURE_NAME):
    return root / "automations" / workflow_name


def workflow_runs_dir(root, workflow_name=WORKFLOW_FIXTURE_NAME):
    return workflow_dir(root, workflow_name) / "runs"


def workflow_run_dir(root, workflow_name=WORKFLOW_FIXTURE_NAME, run_id="run-a"):
    return workflow_runs_dir(root, workflow_name) / run_id


def workflow_suite_path(root, workflow_name=WORKFLOW_FIXTURE_NAME):
    return workflow_dir(root, workflow_name) / "suites" / "workflow-evals.json"


def workflow_hooks_path(root):
    return root / "automations" / "hooks.json"


def smoke_run_dirs(root, workflow_name):
    return list(workflow_runs_dir(root, workflow_name).glob("smoke-local-*"))


def update_run_packet(run_dir, **fields):
    packet = read_json(run_file(run_dir))
    packet.update(fields)
    write_json(run_file(run_dir), packet)
    return packet


def hook_result_map(packet, scoped=False):
    hook_results = packet.get("hook_results")
    assert isinstance(hook_results, list)
    if scoped:
        return {(item["event"], item["id"], item.get("scope")): item for item in hook_results}
    return {(item["event"], item["id"]): item for item in hook_results}


def assert_hook_markers(run_dir, *pairs):
    for event, hook_id in pairs:
        assert run_file(run_dir, "validation", "hooks", f"{event}-{hook_id}.marker").exists()


def assert_hook_marker(run_dir, event, hook_id, phase="orientation"):
    marker = run_file(run_dir, "validation", "hooks", f"{event}-{hook_id}.marker")
    assert marker.exists()
    assert marker.read_text(encoding="utf-8").strip() == f"{event}:{hook_id}:{phase}"


def assert_same_attrs(left, right, *names):
    for name in names:
        assert getattr(left, name) is getattr(right, name)


def assert_hook_ok(by_event, *keys):
    for key in keys:
        assert_ok(by_event[key])


def hook_phase(by_event, key):
    row = by_event[key]
    assert_ok(row)
    assert_field(row, "phase", "orientation")
    return row


def assert_parsed(parser, args, expected):
    parsed = parser.parse_args(args)
    for name, value in expected.items():
        assert getattr(parsed, name) == value


def assert_ok(report):
    assert report.get("ok") is True, report


def assert_not_ok(report):
    assert report.get("ok") is False, report


def assert_true(report, field):
    assert report[field] is True, report


def assert_false(report, field):
    assert report[field] is False, report


def assert_status(report, status):
    assert_field(report, "status", status)


def assert_tool(report, tool):
    assert_field(report, "tool", tool)


def assert_field(report, field, expected):
    assert report[field] == expected, report


def assert_fields(mapping, **expected):
    for field, value in expected.items():
        assert_field(mapping, field, value)


def assert_contains(items, text):
    assert any(text in str(item) for item in items), items


def assert_contains_all(items, *texts):
    assert any(all(text in str(item) for text in texts) for item in items), items


def assert_contains_each(items, *texts):
    for text in texts:
        assert_contains(items, text)


def assert_lacks(items, text):
    assert all(text not in str(item) for item in items), items


def assert_lacks_all(container, *items):
    for item in items:
        assert item not in container, container


def assert_has_all(container, *items):
    for item in items:
        assert item in container, container


def assert_empty(value):
    assert value == [], value


def estimated_compact_json_tokens(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return (len(data) + 3) // 4 if data else 0


def assert_field_set(rows, key, expected):
    assert {row[key] for row in rows} == expected, rows


def assert_single_eval_passed(report):
    expected = {"passed": 1, "failed": 0, "total": 1}
    assert_field(report, "summary", expected)


def assert_valid_workflow(root, name=WORKFLOW_FIXTURE_NAME):
    errors, warnings, _modules = validate_automations.validate_automations(root, workflow_name=name)
    assert_empty(errors)


def assert_strict_promotion(root, warning_text, error_texts=None):
    errors, warnings, _modules = validate_automations.validate_automations(
        root,
        workflow_name=WORKFLOW_FIXTURE_NAME,
        strict_phase_quality=False,
    )
    assert_empty(errors)
    assert_contains(warnings, warning_text)
    strict_errors, strict_warnings, _modules = validate_automations.validate_automations(
        root,
        workflow_name=WORKFLOW_FIXTURE_NAME,
        strict_phase_quality=True,
    )
    for text in error_texts or (warning_text,):
        assert_contains(strict_errors, text)


def write_skill(root, name="demo-skill"):
    skill_dir = root / ".agents" / "skills" / name
    write_text(
        skill_dir / "SKILL.md",
        f"""---
name: {name}
description: Demo skill.
---

# {name}

Fixture.
""",
    )
    write_json(
        skill_dir / "module.json",
        {
            "schema_version": 3,
            "kind": "skill",
            "id": name,
            "version": "1.0.0",
            "summary": "Fixture.",
            "owners": ["engineering"],
            "inputs": ["SKILL.md"],
            "outputs": [],
            "commands": [],
            "strict_read_only_commands": [],
            "extensions": {},
            "related_modules": [],
            "validation": [],
            "external_access": {
                "source_systems": [],
                "credential_expectations": "none",
                "data_copied_locally": [],
                "attachments_retrieved": False,
            },
            "local_ai": {"use_cases": []},
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
        },
    )


def workflow_manifest(name="story-flow"):
    command_texts = [
        f"{VALIDATE_AUTOMATIONS_NAME}{name}",
        f"python -B .agents/manage.py workflow context --name {name} --run-id <run-id> --write",
        f"python -B .agents/manage.py workflow checkpoint --name {name} --run-id <run-id> --write",
        f"python -B .agents/manage.py workflow context-evidence --name {name} --run-id <run-id> --event start --write",
    ]
    commands = [command_spec(command, effects=["repository_write"]) for command in command_texts]
    commands[0]["effects"] = []
    return {
        "schema_version": 3,
        "kind": "workflow",
        "id": name,
        "version": "1.0.0",
        "summary": "Fixture.",
        "routing": {
            "terms": [part for part in name.split("-") if part],
            "activation_terms": [name],
            "threshold": 2,
            "winner_margin": 1,
        },
        "owners": ["engineering"],
        "phases": [{"id": "execute", "summary": WORKFLOW_EXECUTE_SUMMARY}],
        "inputs": ["WORKFLOW.md", "module.json", "instructions.md"],
        "outputs": [
            "runs/<run-id>/run.json",
            "runs/<run-id>/REPORT.md",
            "runs/<run-id>/artifacts/context/context-packet.json",
            "runs/<run-id>/artifacts/documentation/documentation-delta.json",
            "runs/<run-id>/artifacts/documentation/documentation-delta.md",
            "runs/<run-id>/validation/context-evidence-start.json",
            "runs/<run-id>/validation/context-evidence-resume.json",
            "runs/<run-id>/validation/context-evidence-finish.json",
        ],
        "worker_profiles": {
            "schema_version": 1,
            "extends": "portable-default",
            "mode": "auto-when-supported",
            "max_parallel_workers": 1,
            "phase_assignments": {
                "execute": "general-medium",
            },
            "task_assignments": {},
            "delegation": manifests.module_contract_v3.default_delegation_contract(),
        },
        "commands": commands,
        "strict_read_only_commands": [commands[0]["id"]],
        "extensions": {},
        "context": manifests.module_contract_v3.conventional_context(name),
        "template_layers": manifests.module_contract_v3.conventional_template_layers(name),
        "related_modules": ["demo-skill"],
        "validation": [f"{VALIDATE_AUTOMATIONS_NAME}{name}"],
        "external_access": {
            "source_systems": [],
            "credential_expectations": "none",
            "data_copied_locally": [],
            "attachments_retrieved": False,
        },
        "local_ai": {"use_cases": []},
        "context_evidence": {
            "required": True,
            "start_queries": [
                {
                    "id": "workflow-contract",
                    "question": f"What defines {name}?",
                    "scope": "repo",
                    "required": True,
                    "fallback_paths": [
                        f"automations/{name}/WORKFLOW.md",
                        f"automations/{name}/module.json",
                    ],
                }
            ],
            "resume_queries": [
                {
                    "id": "run-state",
                    "question": f"{name} run state?",
                    "scope": "workflow-runs",
                    "required": True,
                    "fallback_paths": [
                        f"automations/{name}/runs/<run-id>/run.json",
                        f"automations/{name}/runs/<run-id>/REPORT.md",
                    ],
                }
            ],
            "finish_queries": [
                {
                    "id": "finish-evidence",
                    "question": f"Finish evidence for {name}?",
                    "scope": "repo",
                    "required": True,
                    "fallback_paths": [
                        f"automations/{name}/WORKFLOW.md",
                        f"automations/{name}/module.json",
                    ],
                }
            ],
        },
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
    }


def workflow_manifest_with_hooks(name="story-flow"):
    manifest = workflow_manifest(name)
    hook_command = hook_marker_command(name)
    manifest["commands"] = [
        *manifest["commands"],
        command_spec(hook_command, effects=["repository_write"]),
    ]
    manifest["hooks"] = [
        hook_spec(hook_id, event, hook_command)
        for hook_id, event in [
            ("write-workflow-pre-marker", "workflow-pre"),
            ("write-start-marker", "run-started"),
            ("write-phase-pre-marker", "phase-pre"),
            ("write-phase-start-marker", "phase-started"),
            ("write-phase-between-marker", "phase-between"),
            ("write-phase-handoff-marker", "phase-handoff"),
            ("write-phase-complete-marker", "phase-completed"),
            ("write-phase-post-marker", "phase-post"),
            ("write-phase-blocked-marker", "phase-blocked"),
            ("write-workflow-post-marker", "workflow-post"),
        ]
    ]
    return manifest


def workflow_manifest_with_tasks(name="story-flow"):
    manifest = workflow_manifest(name)
    manifest["phases"] = [
        {"id": "plan", "summary": "Plan."},
        {"id": "execute", "summary": WORKFLOW_EXECUTE_SUMMARY},
    ]
    manifest["tasks"] = [
        {"id": "shape-plan", "summary": "Plan.", "phase": "plan"},
        {
            "id": "apply-change",
            "summary": "Apply the approved change.",
            "phase": "execute",
            "depends_on": ["shape-plan"],
        },
        {
            "id": "validate-change",
            "summary": "Validate.",
            "phase": "execute",
            "depends_on": ["apply-change"],
        },
    ]
    manifest["worker_profiles"] = {
        "schema_version": 1,
        "extends": "portable-default",
        "mode": "auto-when-supported",
        "max_parallel_workers": 2,
        "phase_assignments": {
            "plan": "planning-high",
            "execute": "implementation-medium",
        },
        "task_assignments": {
            "shape-plan": "planning-high",
            "apply-change": "implementation-medium",
            "validate-change": "validation-local",
        },
        "delegation": manifests.module_contract_v3.default_delegation_contract(),
    }
    manifest["parallel_safety"] = {
        "schema_version": 1,
        "default_mode": "serial",
        "phase_policies": {},
    }
    return manifest


def run_packet(name="story-flow", run_id="run-a"):
    return {
        "schema_version": 2,
        "tool": "workflow-manager.run",
        "workflow": name,
        "run_id": run_id,
        "current_phase": "execute",
        "status": "completed",
        "decisions": [{"decision": "Use v2 run packet.", "why": "Canonical state."}],
        "checks": {"skipped": [], "blocked": [], "failed": []},
        "commands": [{"command": VALIDATE_AUTOMATIONS_COMMAND, "status": "ok"}],
        "evidence": [{"kind": "validation", "path": "runs/run-a/REPORT.md", "summary": "Fixture."}],
        "evidence_paths": ["runs/run-a/REPORT.md"],
        "skipped": [],
        "blocked": [],
        "failed": [],
        "handoff": {
            "loaded_context": ["automations/story-flow/WORKFLOW.md"],
            "required_next_context": ["automations/story-flow/runs/run-a/run.json"],
            "skipped_context": [],
            "blockers": [],
            "last_completed_step": "validated",
            "last_command": VALIDATE_AUTOMATIONS_COMMAND,
        },
        "next_action": FIXTURE_NEXT_ACTION,
        "unsupported_claims": [],
        "external_validation_status": "not-required",
    }


def progress_log_text(workflow_name, *, status="completed", phase="execute"):
    title = "Bug Execution Log" if workflow_name == "bug-ticket-workflow" else "User Story Execution Log"
    command_heading = "Commands And Validation" if workflow_name == "bug-ticket-workflow" else "Commands And Evidence"
    extra = "\n## Reproduction Evidence\n\n| Time | Command Or Action | Result | Evidence |\n|---|---|---|---|\n| t | repro | skipped | REPORT.md |\n" if workflow_name == "bug-ticket-workflow" else ""
    return f"""# {title}

## Progress Update Rules

- record evidence.

## Current State

- Status: {status}
- Current phase: {phase}
- Last updated: 2026-01-01T00:00:00Z

## Phase Handoffs

### Phase: {phase}

- Completed: done.
- Skipped: none.
- Blocked: none.
- Failed: none.
- Validation: passed.
- Decisions: fixture.
- Next step: {FIXTURE_NEXT_ACTION}
{extra}
## {command_heading}

| Time | Command Or Action | Result | Evidence |
|---|---|---|---|
| t | validate | passed | REPORT.md |

## Plan Item Progress

| Plan Item | Status | Evidence | Owner Or Decision |
|---|---|---|---|
| item | done | REPORT.md | fixture |

## Plan Variance

| Package | Planned | Actual | Reason | Approval Impact | Validation Impact |
|---|---|---|---|---|---|
| No variance | fixture plan | fixture result | execution matched plan | none accepted by owner | validation unchanged |

## Independent Review Evidence

| Axis | Reviewer Or Method | Result | Evidence | Disposition |
|---|---|---|---|---|
| Spec and plan compliance | fixture review | passed | REPORT.md | accepted |
| Standards and maintainability | fixture review | passed | REPORT.md | accepted |
| Security and authority | fixture review | skipped: fixture has no security-sensitive changes | REPORT.md | accepted by owner |
| Validation and generated artifacts | fixture review | passed | REPORT.md | accepted |

## Validation Evidence Map

| Planned Evidence | Final Evidence | Result |
|---|---|---|
| validation | REPORT.md | passed |

## Context And Claim Support

- Low-context files used: automations/{workflow_name}/WORKFLOW.md
- Detailed files opened: instructions.md
- Commands run: validate
- Evidence ledger path: run.json
- Remaining unsupported claims: none

## Reusable Lessons

{FIXTURE_LESSON}
"""


def write_workflow(root, name="story-flow", *, with_run=True):
    module_dir = root / "automations" / name
    write_text(
        module_file(module_dir, "WORKFLOW.md"),
        f"""# Story Flow

Read `module.json`, `WORKFLOW.md`, `instructions.md`.

## Example Prompts

- Start: "Start `{name}`. Read routing, `WORKFLOW.md`, and `module.json`; create the run packet."
- Resume: "Resume `{name}`. Load `run.json` and named context."
- Handoff: "Handoff `{name}` with context, blockers, evidence, and next action."
- Finish: "Finish `{name}` by checking run state, skipped/blocked/failed checks, claims, and validation."
""",
    )
    write_text(
        module_file(module_dir, "instructions.md"),
        """# Instructions

## Always Load

- Keep `run.json`.

## Stop Rules

- Stop on missing evidence.

## Completion Contract

- Report validation.

## Phase: execute

- [ ] Read: `WORKFLOW.md`, `module.json`.
  Do: execute.
  Write: `runs/<run-id>/run.json`.
  Done when: evidence is recorded.
  If blocked: record blocker.
""",
    )
    write_json(module_file(module_dir, "module.json"), workflow_manifest(name))
    if name in {"user-story-workflow", "bug-ticket-workflow"}:
        write_text(module_file(module_dir, "templates", "execution-log.md"), progress_log_text(name, status="not started", phase="orientation"))
    if with_run:
        run_dir = run_dir_for(module_dir)
        write_json(run_file(run_dir), run_packet(name))
        write_text(run_file(run_dir, "REPORT.md"), "# Report\n\nEvidence.")
        if name in {"user-story-workflow", "bug-ticket-workflow"}:
            write_text(run_file(run_dir, "execution-log.md"), progress_log_text(name))
    return module_dir


def write_workflow_with_hooks(root, name="story-flow"):
    module_dir = write_workflow(root, name, with_run=False)
    write_json(module_file(module_dir, "module.json"), workflow_manifest_with_hooks(name))
    write_text(module_file(module_dir, "templates", "plan.md"), "# Plan\n\n- [ ] Record hook evidence.\n")
    write_text(
        module_file(module_dir, "scripts", "write_hook_marker.py"),
        """#!/usr/bin/env python3
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--run-dir", required=True)
parser.add_argument("--event", required=True)
parser.add_argument("--hook-id", required=True)
parser.add_argument("--phase", required=True)
args = parser.parse_args()

run_dir = Path(args.run_dir)
marker = run_dir / "validation" / "hooks" / f"{args.event}-{args.hook_id}.marker"
marker.parent.mkdir(parents=True, exist_ok=True)
marker.write_text(f"{args.event}:{args.hook_id}:{args.phase}\\n", encoding="utf-8", newline="\\n")
print(marker.as_posix())
""",
    )
    return module_dir


def write_fixture(root, name="story-flow", *, with_run=True, hooks=False):
    write_skill(root)
    return write_workflow_with_hooks(root, name) if hooks else write_workflow(root, name, with_run=with_run)


def extend_fixture_workflow(module_dir, sentence, repetitions):
    workflow_path = module_file(module_dir, "WORKFLOW.md")
    write_text(
        workflow_path,
        workflow_path.read_text(encoding="utf-8")
        + "\n## Fixture Baseline\n\n"
        + (sentence + "\n") * repetitions,
    )


def filled_story_plan():
    plan = (
        plan_smoke.filled_user_story_plan()
        .replace("No security impact", "No impact")
        .replace(APPROVAL_PENDING, APPROVAL_PLANNED)
    )
    replacements = (
        ("| Plan exists | Fill plan.md and stop | workflow plan-check passes | WORKFLOW.md |", STORY_AC_PROOF_ROW),
        (
            "| WP1 | Request approval | No implementation before approval | none | No source changes | workflow run | plan-check | approval is recorded | Continue with approved implementation |",
            "| WP1 | Request approval | No implementation before approval | none | No source changes | workflow run | plan-check | approval is recorded | Continue with approved implementation |\n" + STORY_WORK_ITEM_ROW,
        ),
    )
    for old, new in replacements:
        plan = plan.replace(old, new)
    return plan


def scaffold_args(root, name="new-flow", *, force=False):
    return Namespace(
        root=str(root),
        workflow_name=name,
        summary="Coordinates a deterministic new flow.",
        uses_skill=["demo-skill"],
        uses_script=[],
        force=force,
    )


def test_valid_current_workflow(tmp):
    write_fixture(tmp)
    errors, warnings, modules = validate_automations.validate_automations(tmp)
    assert_empty(errors)
    assert [module.name for module in modules] == ["story-flow"]


def test_current_workflow_rejects_unknown_core_field(tmp):
    module_dir = write_fixture(tmp)
    manifest = workflow_manifest("story-flow")
    normalized, adapter_errors, _adapter_warnings = (
        manifests.module_contract_v3.normalize_module_contract(manifest)
    )
    assert_empty(adapter_errors)
    write_json(module_file(module_dir, "module.json"), normalized)

    errors, warnings, _modules = validate_automations.validate_automations(tmp)
    assert_empty(errors)

    normalized["owner_hint"] = "engineering"
    write_json(module_file(module_dir, "module.json"), normalized)
    strict_errors, _strict_warnings, _modules = validate_automations.validate_automations(tmp)
    assert_contains(strict_errors, "owner_hint")


def test_compact_local_ai_use_case_ids_validate_and_eval(tmp):
    module_dir = write_fixture(tmp)
    manifest = workflow_manifest("story-flow")
    manifest["local_ai"] = {"use_cases": ["validation-triage", "changed-files-summary"]}
    write_json(module_file(module_dir, "module.json"), manifest)
    assert_valid_workflow(tmp)

    suite = module_file(module_dir, "suites", "workflow-evals.json")
    write_json(
        suite,
        {
            "evals": [
                {
                    "id": "compact-local-ai",
                    "assertions": [
                        {
                            "type": "contract_local_ai_use_cases",
                            "use_cases": ["validation-triage", "changed-files-summary"],
                        }
                    ],
                }
            ]
        },
    )
    report = eval_workflow.run_eval(
        eval_workflow.Args(root=tmp, workflow_name="story-flow", suite=suite, output_format="json")
    )
    summary = manifests.local_ai_use_case_summary(manifest)
    assert_single_eval_passed(report)
    assert summary == {
        "use_case_count": 2,
        "use_cases": ["validation-triage", "changed-files-summary"],
    }


def test_eval_workflow_rejects_skill_suite_with_eval_skill_hint(tmp):
    write_fixture(tmp)
    suite = tmp / "skill-evals.json"
    write_json(
        suite,
        {
            "skill_name": "workflow-manager",
            "evals": [
                {
                    "id": "wrong-runner",
                    "assertions": [{"type": "manifest_field_equals", "path": "version", "value": "1.0.0"}],
                }
            ],
        },
    )

    try:
        eval_workflow.run_eval(
            eval_workflow.Args(root=tmp, workflow_name="story-flow", suite=suite, output_format="json")
        )
    except SystemExit as exc:
        message = str(exc)
    else:
        raise AssertionError("expected skill eval suite to be rejected by eval-workflow")

    assert "skill eval suite" in message, message
    assert "eval-skill" in message, message


def test_eval_workflow_executes_repeated_commands_against_current_state(tmp):
    module_dir = write_fixture(tmp)
    write_text(tmp / ".agents" / "manage.py", "# fixture launcher")
    suite = module_file(module_dir, "suites", "cached-command-evals.json")
    write_json(
        suite,
        {
            "evals": [
                {
                    "id": "cached-command",
                    "assertions": [
                        {
                            "type": "repo_command_succeeds",
                            "command": ["status", "--fast"],
                            "timeout_seconds": 5,
                        },
                        {
                            "type": "repo_command_succeeds",
                            "command": ["status", "--fast"],
                            "timeout_seconds": 5,
                        },
                        {
                            "type": "repo_command_succeeds",
                            "command": ["status", "--fast"],
                            "timeout_seconds": 6,
                        },
                    ],
                }
            ]
        },
    )
    calls = []

    def failed_run(command, **kwargs):
        calls.append((list(command), kwargs["timeout"]))
        return eval_workflow.subprocess.CompletedProcess(command, 0, stdout=f"execution-{len(calls)}\n")

    original_run = eval_workflow.execute_subprocess
    eval_workflow.execute_subprocess = failed_run
    try:
        report = eval_workflow.run_eval(
            eval_workflow.Args(root=tmp, workflow_name="story-flow", suite=suite, output_format="json")
        )
    finally:
        eval_workflow.execute_subprocess = original_run

    assert len(calls) == 3, calls
    assert {timeout for _command, timeout in calls} == {5, 6}, calls
    assert report["command_telemetry"] == {
        "command_assertions": 3,
        "command_executions": 3,
    }
    assertions = report["results"][0]["assertions"]
    assert assertions[0]["message"] != assertions[1]["message"]
    assert_has_all(assertions[0]["message"], "execution-1")
    assert_has_all(assertions[1]["message"], "execution-2")
    assert_has_all(assertions[2]["message"], "execution-3")


def test_eval_workflow_timeout_kills_child_process_tree(tmp):
    survived = tmp / "child-survived.txt"
    child_code = (
        "import time\n"
        "from pathlib import Path\n"
        "time.sleep(2)\n"
        f"Path({str(survived)!r}).write_text('survived', encoding='utf-8')\n"
    )
    parent = tmp / "spawn_child.py"
    write_text(
        parent,
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        "time.sleep(10)\n",
    )
    runner = eval_workflow.CommandRunner()

    returncode, output = runner.run(
        [sys.executable, "-B", str(parent)],
        cwd=tmp,
        timeout_seconds=1,
    )
    time.sleep(2)

    assert returncode == 124, (returncode, output)
    assert not survived.exists(), "timed-out workflow eval left its child process running"


def test_default_context_evidence_queries_use_compact_start_fallbacks(_tmp):
    queries = workflow_context_evidence.default_context_evidence_queries("user-story-workflow")
    start_paths = [
        path
        for query in queries["start"]
        for path in query.get("fallback_paths", [])
    ]
    finish_paths = [
        path
        for query in queries["finish"]
        for path in query.get("fallback_paths", [])
    ]

    assert_lacks_all(start_paths, "docs/start-here.md", "README.md", "docs/workflow/workflows.md")
    assert_has_all(start_paths, "docs/agent-start.md", "docs/workflow/workflow-quickstart.md")
    assert_has_all(finish_paths, "docs/workflow/quality-evidence-packets.md")


def test_start_context_evidence_uses_declared_compact_paths(tmp):
    for rel_path in (
        "AGENTS.md",
        "docs/agent-start.md",
        "docs/project/project-context.md",
        "docs/workflow/workflow-quickstart.md",
        "docs/workflow/quality-evidence-packets.md",
        "automations/routing.md",
        "automations/user-story-workflow/WORKFLOW.md",
        "automations/user-story-workflow/module.json",
        "automations/user-story-workflow/instructions.md",
        "automations/user-story-workflow/runs/run-a/run.json",
        "automations/user-story-workflow/runs/run-a/REPORT.md",
    ):
        write_text(tmp / rel_path, f"{rel_path} evidence\n")
    run_dir = workflow_run_dir(tmp, "user-story-workflow")
    query = workflow_context_evidence.default_context_evidence_queries("user-story-workflow")["start"][0]

    paths = [
        common.relative(tmp, path)
        for path in workflow_context_evidence.candidate_paths(tmp, "user-story-workflow", run_dir, query)
    ]

    assert_has_all(
        paths,
        "automations/user-story-workflow/WORKFLOW.md",
        "automations/user-story-workflow/module.json",
        "docs/workflow/workflow-quickstart.md",
    )
    assert_lacks_all(
        paths,
        "docs/project/project-context.md",
        "docs/workflow/quality-evidence-packets.md",
        "automations/user-story-workflow/runs/run-a/run.json",
    )


def test_resume_context_evidence_prioritizes_declared_paths(tmp):
    run_dir = workflow_run_dir(tmp, "user-story-workflow")
    declared = (
        run_dir / "run.json",
        run_dir / "REPORT.md",
        run_dir / "execution-log.md",
        run_dir / "artifacts" / "context" / "context-packet.json",
    )
    for path in declared:
        write_text(path, f"{path.name} declared resume source\n")
    for index in range(8):
        write_text(
            run_dir / "validation" / f"high-score-{index}.json",
            (
                "current run state latest report blockers validation evidence "
                "context packet next action "
            )
            * 100,
        )
    query = workflow_context_evidence.default_context_evidence_queries(
        "user-story-workflow"
    )["resume"][0]

    result = workflow_context_evidence.fallback_evidence(
        tmp,
        "user-story-workflow",
        run_dir,
        query,
        top_k=5,
    )

    assert result["ok"] is True
    assert result["evidence_paths"][:4] == [
        common.relative(tmp, path) for path in declared
    ]


def test_context_evidence_run_scan_enforces_file_and_byte_budgets(tmp):
    run_dir = workflow_run_dir(tmp, "user-story-workflow")
    for index in range(workflow_context_evidence.MAX_CANDIDATE_FILES + 20):
        write_text(
            run_dir / "bulk" / f"evidence-{index:03d}.md",
            ("bounded scan evidence " * 5000) + "\n",
        )
    query = {
        "id": "bounded-run-scan",
        "question": "Where is bounded scan evidence recorded?",
        "scope": "workflow-runs",
        "required": True,
        "fallback_paths": [],
    }

    result = workflow_context_evidence.fallback_evidence(
        tmp,
        "user-story-workflow",
        run_dir,
        query,
        top_k=3,
    )

    assert result["ok"] is True
    assert result["scan"]["candidate_file_count"] == workflow_context_evidence.MAX_CANDIDATE_FILES
    assert result["scan"]["scanned_file_count"] <= workflow_context_evidence.MAX_CANDIDATE_FILES
    assert result["scan"]["scanned_bytes"] <= workflow_context_evidence.MAX_SCAN_BYTES
    assert result["scan"]["truncated"] is True


def test_optional_task_graph_validation_and_eval(tmp):
    module_dir = write_fixture(tmp)
    write_json(module_file(module_dir, "module.json"), workflow_manifest_with_tasks("story-flow"))
    with module_file(module_dir, "instructions.md").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(PLAN_PHASE_INSTRUCTIONS)
    assert_valid_workflow(tmp)

    suite = module_file(module_dir, "suites", "workflow-evals.json")
    write_json(
        suite,
        {
            "evals": [
                {
                    "id": "task-graph",
                    "assertions": [
                        {"type": "validation_ok"},
                        {"type": "contract_declares_phase", "phase": "plan"},
                        {"type": "contract_declares_task", "task": "validate-change"},
                    ],
                }
            ]
        },
    )
    report = eval_workflow.run_eval(
        eval_workflow.Args(root=tmp, workflow_name="story-flow", suite=suite, output_format="json")
    )
    assert_single_eval_passed(report)
    worker_report = workflow_workers.workflow_workers_report(tmp, workflow_names=["story-flow"])
    worker_markdown = workflow_workers.render_workers_markdown(worker_report)
    assert_has_all(worker_markdown, "`validate-change`", "`validation-local`")


def test_worker_profiles_validate_and_report_phase_assignments(tmp):
    write_fixture(tmp)

    report = workflow_workers.workflow_workers_report(
        tmp,
        workflow_names=["story-flow"],
        phase="execute",
    )

    assert_ok(report)
    workflow = report["workflows"][0]
    phase_rows = workflow["worker_profiles"]["phase_assignments"]
    assert len(phase_rows) == 1
    assert_field(phase_rows[0], "phase", "execute")
    profile = phase_rows[0]["profile"]
    assert_fields(profile, id="general-medium", route_set="implementation-medium")
    assert_fields(
        profile["surface_routes"]["codex"][0],
        model_provider="openai",
        model="gpt-5.5",
    )
    assert_fields(
        profile["execution"],
        prompt_adapter="general",
        context_budget="standard",
        tool_policy="bounded-write",
        validation_gate="record-evidence",
    )
    assert_fields(
        profile["surface_routes"]["claude-code"][0],
        model_provider="anthropic",
        model="claude-sonnet-4.5",
    )
    header_text = " ".join(profile["execution"]["instruction_header"])
    assert_has_all(header_text, "surface routes are advisory", "Do not expose hidden reasoning")
    markdown = workflow_workers.render_workers_markdown(report)
    assert_has_all(markdown, "`codex`: openai gpt-5.5 medium", "`claude-code`: anthropic claude-sonnet-4.5 medium")


def test_worker_profile_resolves_numeric_phase_budget_and_margin(tmp):
    module_dir = write_fixture(tmp)
    manifest = read_json(module_file(module_dir, "module.json"))
    policy = common.repo_policy.default_policy_document()["cost_policy"]
    policy["budgets"]["phases"]["overrides"]["implementation"] = 8000

    within = workflow_workers.workflow_execution_profile(
        manifest,
        "execute",
        cost_policy=policy,
        effective_context_tokens=7600,
    )
    over = workflow_workers.workflow_execution_profile(
        manifest,
        "execute",
        cost_policy=policy,
        effective_context_tokens=8200,
    )

    assert_fields(
        within,
        context_budget_ref="implementation",
        budget_tokens=8000,
        effective_context_tokens=7600,
        remaining_margin_tokens=400,
        within_budget=True,
    )
    assert_fields(
        over,
        budget_tokens=8000,
        effective_context_tokens=8200,
        remaining_margin_tokens=-200,
        within_budget=False,
    )


def test_worker_phase_report_exposes_numeric_budget_with_unmeasured_context(tmp):
    write_fixture(tmp)
    write_guidance_savings_fixture(tmp)

    report = workflow_workers.workflow_workers_report(
        tmp,
        workflow_names=["story-flow"],
        phase="execute",
    )

    execution = report["workflows"][0]["worker_profiles"]["phase_assignments"][0]["profile"]["execution"]
    assert_fields(
        execution,
        context_budget_ref="implementation",
        budget_tokens=8000,
        effective_context_tokens=None,
        remaining_margin_tokens=None,
        within_budget=None,
        context_measurement="not-measured",
    )


def test_worker_report_returns_structured_v2_policy_errors(tmp):
    write_fixture(tmp)
    policy = common.repo_policy.default_policy_document()
    policy["cost_policy"]["budgets"]["phases"]["default_tokens"] = "bad"
    write_json(tmp / common.repo_policy.PROJECT_POLICY_PATH, policy)

    report = workflow_workers.workflow_workers_report(tmp, workflow_names=["story-flow"])

    assert_not_ok(report)
    assert_status(report, "failed")
    assert_field(report["summary"], "issue_count", 1)
    assert_contains(report["issues"], "cost_policy.budgets.phases.default_tokens")


def test_worker_numeric_phase_budget_fallback_metadata_is_strict(_tmp):
    for value in ("1500", True, 0, -1):
        policy = common.repo_policy.default_policy_document()["cost_policy"]
        policy["budgets"]["phases"]["overrides"]["routing"] = value
        details = workflow_workers.numeric_phase_budget_details(
            policy,
            "routing",
        )
        assert_fields(
            details,
            budget_tokens=6000,
            budget_source="fallback-invalid",
        )
        assert "budgets.phases.overrides.routing" in details["budget_issue"]

    missing = workflow_workers.numeric_phase_budget_details({}, "routing")
    valid_policy = common.repo_policy.default_policy_document()["cost_policy"]
    valid_policy["budgets"]["phases"]["overrides"]["routing"] = 1500
    valid = workflow_workers.numeric_phase_budget_details(
        valid_policy,
        "routing",
    )
    assert_fields(
        missing,
        budget_tokens=6000,
        budget_source="default-missing",
        budget_issue="",
    )
    assert_fields(
        valid,
        budget_tokens=1500,
        budget_source="configured",
        budget_issue="",
    )


def test_worker_parallelism_stays_serial_until_economics_evidence_passes(tmp):
    module_dir = write_fixture(tmp)
    manifest, errors, _warnings = manifests.module_contract_v3.normalize_module_contract(
        workflow_manifest_with_tasks("story-flow")
    )
    assert_empty(errors)
    manifest["worker_profiles"]["max_parallel_workers"] = 2
    manifest["parallel_safety"] = {
        "schema_version": 1,
        "default_mode": "serial",
        "phase_policies": {
            "plan": {
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
            },
            "execute": {
                "mode": "serial",
                "max_workers": 1,
                "write_scopes": ["automations/story-flow/runs/<run-id>"],
                "runtime": {
                    "environment": "none",
                    "ports": "none",
                    "state_stores": "none",
                    "services": "none",
                },
                "provider": "none",
            },
        },
    }
    write_json(module_file(module_dir, "module.json"), manifest)
    write_json(tmp / ".agents/project-policy.json", common.repo_policy.default_policy_document())

    report = workflow_workers.workflow_workers_report(tmp, workflow_names=["story-flow"])
    workers = report["workflows"][0]["worker_profiles"]
    plan_row = next(row for row in workers["phase_assignments"] if row["phase"] == "plan")
    execute_row = next(row for row in workers["phase_assignments"] if row["phase"] == "execute")

    assert_fields(
        workers,
        declared_worker_count=2,
        effective_worker_count=1,
        eligible=False,
        isolation_mode="serial-fallback",
    )
    assert any(
        "delegation-balanced-v1" in reason and "passing provider-backed evidence" in reason
        for reason in workers["serial_fallback_reasons"]
    )
    assert_fields(
        plan_row["parallel_safety"],
        declared_worker_count=2,
        effective_worker_count=1,
        eligible=False,
        isolation_mode="parallel-read-only",
    )
    assert_fields(
        execute_row["parallel_safety"],
        declared_worker_count=1,
        effective_worker_count=1,
        eligible=False,
        isolation_mode="serial",
    )

    write_json(
        tmp / ".agents/benchmarks/delegation-gates/delegation-balanced-v1.json",
        delegation_gate_evidence(),
    )
    promoted = workflow_workers.workflow_workers_report(tmp, workflow_names=["story-flow"])
    promoted_workers = promoted["workflows"][0]["worker_profiles"]
    promoted_plan = next(
        row for row in promoted_workers["phase_assignments"] if row["phase"] == "plan"
    )
    assert_fields(
        promoted_workers,
        declared_worker_count=2,
        effective_worker_count=1,
        eligible=False,
        available_orchestration_mode="direct-tools",
        effective_orchestration_mode="direct-tools",
    )
    assert_has_all(promoted_workers["serial_fallback_reason"], "trusted current host observation", "native delegation")
    assert_fields(
        promoted_plan["parallel_safety"],
        effective_worker_count=1,
        eligible=False,
        host_capability_eligible=False,
    )

    requested = workflow_workers.workflow_workers_report(
        tmp,
        workflow_names=["story-flow"],
        delegation_requested=True,
        task_class="independent-read-heavy",
    )
    requested_workers = requested["workflows"][0]["worker_profiles"]
    requested_plan = next(
        row for row in requested_workers["phase_assignments"] if row["phase"] == "plan"
    )
    assert_fields(
        requested_workers,
        declared_worker_count=2,
        effective_worker_count=1,
        eligible=False,
        available_orchestration_mode="direct-tools",
        effective_orchestration_mode="direct-tools",
        delegation_requested=True,
        task_class="independent-read-heavy",
    )
    assert_fields(
        requested_plan["parallel_safety"],
        effective_worker_count=1,
        eligible=False,
        delegation_requested=True,
    )

    observation = runtime_observation(
        capabilities=[
            "native-subagents",
            "complete-thread-tree",
            "complete-usage-telemetry",
            "isolated-worker-runtime",
            "context-inheritance-control",
        ]
    )
    observation["phase"] = "plan"
    approved = workflow_workers.workflow_workers_report(
        tmp,
        workflow_names=["story-flow"],
        phase="plan",
        delegation_requested=True,
        task_class="independent-read-heavy",
        runtime_observation=observation,
        observation_run_id="run-a",
    )
    approved_workers = approved["workflows"][0]["worker_profiles"]
    approved_plan = approved_workers["phase_assignments"][0]["parallel_safety"]
    assert_fields(
        approved_workers,
        declared_worker_count=2,
        effective_worker_count=2,
        eligible=True,
        isolation_mode="parallel-read-only",
        serial_fallback_reason="",
        serial_fallback_reasons=[],
        available_orchestration_mode="native-subagents",
        effective_orchestration_mode="native-subagents",
    )
    assert_fields(
        approved_plan,
        effective_worker_count=2,
        eligible=True,
        host_capability_eligible=True,
        available_orchestration_mode="native-subagents",
        effective_orchestration_mode="native-subagents",
        missing_host_capabilities=[],
    )

    adapter = workflow_workers.resolve_surface_adapter(
        workflow_workers.load_worker_profile_config(),
        "codex",
        observation["host"]["capabilities"],
        trusted=True,
        delegation_decision=approved_plan,
    )
    assert_fields(
        adapter,
        available_orchestration_mode="native-subagents",
        orchestration_mode="native-subagents",
        effective_orchestration_mode="native-subagents",
        blocked_optimizations=[],
    )


def test_delegation_economics_evidence_is_strict_and_runtime_scoped(tmp):
    manifest, errors, _warnings = manifests.module_contract_v3.normalize_module_contract(
        workflow_manifest_with_tasks("story-flow")
    )
    assert_empty(errors)
    manifest["worker_profiles"]["max_parallel_workers"] = 2
    manifest["parallel_safety"] = {
        "schema_version": 1,
        "default_mode": "serial",
        "phase_policies": {
            "plan": {
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
    cost_policy = common.repo_policy.default_policy_document()["cost_policy"]
    cost_policy["delegation"] = {
        "gates": {
            "delegation-balanced-v1": {
                "minimum_trials_per_arm": 3,
                "minimum_median_wall_time_improvement_percent": 20,
                "maximum_median_provider_token_increase_percent": 25,
                "maximum_tokens_per_trial": 80000,
                "maximum_seconds_per_trial": 600,
            }
        }
    }
    evidence_path = tmp / ".agents/benchmarks/delegation-gates/delegation-balanced-v1.json"
    capabilities = [
        "native-subagents",
        "complete-thread-tree",
        "complete-usage-telemetry",
        "isolated-worker-runtime",
        "context-inheritance-control",
    ]

    def decision(observation):
        return workflow_workers.phase_parallel_decision(
            manifest,
            "plan",
            root=tmp,
            cost_policy=cost_policy,
            delegation_requested=True,
            task_class="independent-read-heavy",
            runtime_observation=observation,
            workflow="story-flow",
            run_id="run-a",
        )

    codex_observation = runtime_observation(capabilities=capabilities)
    codex_observation["phase"] = "plan"

    schema_v1 = delegation_gate_evidence()
    schema_v1["schema_version"] = 1
    write_json(evidence_path, schema_v1)
    assert_has_all(
        decision(codex_observation)["serial_fallback_reason"],
        "schema_version must be 2",
    )

    wrong_tool = delegation_gate_evidence()
    wrong_tool["tool"] = "operator-authored-gate"
    write_json(evidence_path, wrong_tool)
    assert_has_all(
        decision(codex_observation)["serial_fallback_reason"],
        "tool must be agent-benchmarking.delegation-gate",
    )

    model_mismatch = delegation_gate_evidence(model="gpt-5.6-sol")
    write_json(evidence_path, model_mismatch)
    assert_has_all(
        decision(codex_observation)["serial_fallback_reason"],
        "model does not match the current runtime",
    )

    mode_mismatch = delegation_gate_evidence(execution_mode="serial")
    write_json(evidence_path, mode_mismatch)
    assert_has_all(
        decision(codex_observation)["serial_fallback_reason"],
        "execution mode does not match the current runtime",
    )

    unavailable_adapter = delegation_gate_evidence(
        provider_adapter_id="unavailable",
        provider_adapter_status="unavailable",
    )
    write_json(evidence_path, unavailable_adapter)
    assert_has_all(
        decision(codex_observation)["serial_fallback_reason"],
        "provider evidence adapter status is unavailable",
    )

    wrong_adapter = delegation_gate_evidence(provider_adapter_id="claude-code-usage-v1")
    write_json(evidence_path, wrong_adapter)
    assert_has_all(
        decision(codex_observation)["serial_fallback_reason"],
        "provider evidence adapter does not match the current runtime",
    )

    write_json(evidence_path, delegation_gate_evidence())
    claude_observation = runtime_observation(
        host_surface="claude-code",
        model_provider="anthropic",
        model="claude-opus-4.1",
        capabilities=capabilities,
    )
    claude_observation["phase"] = "plan"
    claude_decision = decision(claude_observation)
    assert_fields(
        claude_decision,
        effective_worker_count=1,
        effective_orchestration_mode="direct-tools",
        eligible=False,
    )
    assert_has_all(
        claude_decision["serial_fallback_reason"],
        "no implemented evidence adapter for claude-code/anthropic",
    )


def test_phase_filtered_worker_summary_excludes_other_phase_blockers(tmp):
    module_dir = write_fixture(tmp)
    manifest, errors, _warnings = manifests.module_contract_v3.normalize_module_contract(
        workflow_manifest_with_tasks("story-flow")
    )
    assert_empty(errors)
    manifest["worker_profiles"]["max_parallel_workers"] = 2
    policy = {
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
    manifest["parallel_safety"] = {
        "schema_version": 1,
        "default_mode": "serial",
        "phase_policies": {
            "plan": dict(policy),
            "execute": dict(policy),
        },
    }
    write_json(module_file(module_dir, "module.json"), manifest)
    write_json(tmp / ".agents/project-policy.json", common.repo_policy.default_policy_document())
    write_json(
        tmp / ".agents/benchmarks/delegation-gates/delegation-balanced-v1.json",
        delegation_gate_evidence(),
    )
    observation = runtime_observation(
        capabilities=[
            "native-subagents",
            "complete-thread-tree",
            "complete-usage-telemetry",
            "isolated-worker-runtime",
            "context-inheritance-control",
        ]
    )
    observation["phase"] = "plan"

    report = workflow_workers.workflow_workers_report(
        tmp,
        workflow_names=["story-flow"],
        phase="plan",
        delegation_requested=True,
        runtime_observation=observation,
        observation_run_id="run-a",
    )
    workers = report["workflows"][0]["worker_profiles"]

    assert_fields(
        workers,
        declared_worker_count=2,
        effective_worker_count=2,
        effective_orchestration_mode="native-subagents",
        serial_fallback_reason="",
        serial_fallback_reasons=[],
    )
    assert len(workers["phase_assignments"]) == 1
    assert_field(workers["phase_assignments"][0], "phase", "plan")


def test_v3_unsafe_parallel_declaration_fails_worker_report(tmp):
    module_dir = write_fixture(tmp)
    manifest, errors, _warnings = manifests.module_contract_v3.normalize_module_contract(
        workflow_manifest_with_tasks("story-flow")
    )
    assert_empty(errors)
    manifest["worker_profiles"]["max_parallel_workers"] = 2
    manifest["parallel_safety"] = {
        "schema_version": 1,
        "default_mode": "serial",
        "phase_policies": {
            "plan": {
                "mode": "parallel-read-only",
                "max_workers": 2,
                "write_scopes": ["shared.sqlite"],
                "runtime": {
                    "environment": "inherited-read-only",
                    "ports": "per-worker",
                    "state_stores": "per-worker",
                    "services": "none",
                },
                "provider": "none",
            }
        },
    }
    write_json(module_file(module_dir, "module.json"), manifest)

    report = workflow_workers.workflow_workers_report(tmp, workflow_names=["story-flow"])

    assert_not_ok(report)
    assert_contains_each(
        report["workflows"][0]["issues"],
        "write_scopes must be empty",
        "no ports, state stores, or services",
    )


def test_parallel_runtime_fixture_proves_shared_and_isolated_resources(tmp):
    report = parallel_safety_fixture.run_fixture(tmp)

    assert_ok(report)
    assert_fields(
        report["shared_runtime"],
        sqlite_collision=True,
        fixed_http_port_collision=True,
        environment_file_collision=True,
        worktrees_alone_are_insufficient=True,
    )
    assert_fields(
        report["isolated_runtime"],
        sqlite_paths_distinct=True,
        allocated_ports_distinct=True,
        environment_files_distinct=True,
        passed=True,
    )


def test_worker_profiles_reject_unknown_phase_or_profile(tmp):
    module_dir = write_fixture(tmp)
    manifest = workflow_manifest("story-flow")
    manifest["worker_profiles"] = {
        "schema_version": 1,
        "extends": "portable-default",
        "mode": "auto-when-supported",
        "max_parallel_workers": 1,
        "phase_assignments": {
            "missing": "unknown",
        },
        "task_assignments": {},
    }
    write_json(module_file(module_dir, "module.json"), manifest)

    errors, _warnings, _modules = validate_automations.validate_automations(tmp, workflow_name="story-flow")

    assert_contains_each(
        errors,
        "is missing phase 'execute'",
        "references unknown phase 'missing'",
        "missing references unknown profile 'unknown'",
    )


def test_worker_profiles_reject_invalid_execution_profile_metadata(tmp):
    module_dir = write_fixture(tmp)
    manifest = workflow_manifest("story-flow")
    manifest["worker_profiles"] = {
        "schema_version": 1,
        "extends": "portable-default",
        "mode": "auto-when-supported",
        "max_parallel_workers": 1,
        "profiles": {
            "bad-profile": {
                "purpose": "Invalid profile metadata.",
                "prompt_adapter": "verbose",
                "context_budget": "huge",
                "tool_policy": "network-write",
                "expected_output": "",
                "validation_gate": "trust-me",
                "route_set": "implementation-medium",
            }
        },
        "phase_assignments": {
            "execute": "bad-profile",
        },
        "task_assignments": {},
    }
    write_json(module_file(module_dir, "module.json"), manifest)

    errors, _warnings, _modules = validate_automations.validate_automations(tmp, workflow_name="story-flow")

    assert_contains_each(
        errors,
        "bad-profile.prompt_adapter must be one of",
        "bad-profile.context_budget must be one of",
        "bad-profile.tool_policy must be one of",
        "bad-profile.expected_output must be a non-empty string",
        "bad-profile.validation_gate must be one of",
    )


def test_worker_profiles_reject_positional_phase_profiles_at_runtime(_tmp):
    manifest = workflow_manifest_with_tasks("story-flow")
    manifest["schema_version"] = 3
    manifest["worker_profiles"] = {
        "phase_profiles": ["planning-high", "implementation-medium"],
        "max_parallel_workers": 2,
    }

    issues = workflow_workers.validate_worker_profiles(manifest)

    assert_contains(
        issues,
        "phase_profiles is not supported; use phase_assignments keyed by phase ID",
    )


def test_phase_assignment_mapping_is_stable_when_phases_reorder(_tmp):
    manifest, errors, _warnings = manifests.module_contract_v3.normalize_module_contract(
        workflow_manifest_with_tasks("story-flow")
    )
    assert_empty(errors)
    before = {
        phase: workflow_workers.workflow_execution_profile(manifest, phase)["profile_id"]
        for phase in ("plan", "execute")
    }

    manifest["phases"] = list(reversed(manifest["phases"]))
    after = {
        phase: workflow_workers.workflow_execution_profile(manifest, phase)["profile_id"]
        for phase in ("plan", "execute")
    }

    assert before == after == {
        "plan": "planning-high",
        "execute": "implementation-medium",
    }


def test_worker_profile_request_for_unknown_phase_never_falls_back(_tmp):
    manifest, errors, _warnings = manifests.module_contract_v3.normalize_module_contract(
        workflow_manifest_with_tasks("story-flow")
    )
    assert_empty(errors)

    report = workflow_workers.workflow_execution_profile(manifest, "missing-phase")

    assert_status(report, "missing-profile")
    assert_field(report, "phase", "missing-phase")
    assert_field(report, "profile_id", "")


def test_worker_profile_catalog_reports_local_validation_and_test_authoring(_tmp):
    report = workflow_workers.profile_catalog_report()
    markdown = workflow_workers.render_workers_markdown(report)

    assert_ok(report)
    assert report["summary"]["profile_count"] >= 8
    profiles = {
        profile["id"]: profile
        for profile_set in report["profile_sets"]
        for profile in profile_set["profiles"]
    }
    assert_field(profiles["validation-local"]["surface_routes"]["local-ai"][0], "model_provider", "local")
    assert_field(profiles["planning-high"]["surface_routes"]["codex"][0], "model", "gpt-5.5")
    assert_fields(
        profiles["implementation-low"]["surface_routes"]["codex"][0],
        model="gpt-5.6-luna",
        deliberation_tier="low",
    )
    assert_fields(
        profiles["implementation-low"]["surface_routes"]["github-copilot"][0],
        model="gpt-5.4-mini",
        deliberation_tier="low",
    )
    assert_fields(
        profiles["implementation-low"]["surface_routes"]["claude-code"][0],
        model="claude-haiku-4.5",
        deliberation_tier="medium",
    )
    assert_field(profiles["implementation-low"]["execution"], "context_budget", "lean")
    assert_field(profiles["implementation-low"]["execution"], "validation_gate", "deterministic-checks")
    assert_field(profiles["test-authoring-medium"]["surface_routes"]["codex"][0], "model", "gpt-5.5")
    assert_field(profiles["coordination-low-cost"]["surface_routes"]["codex"][0], "model", "gpt-5.6-luna")
    assert_field(profiles["review-high"]["surface_routes"]["codex"][0], "model", "gpt-5.6-sol")
    hosts = {row["host"]: row for row in report["host_support"]}
    assert_field(hosts["codex"], "fallback", "serial-active-model")
    assert_field(hosts["github-copilot"], "worker_selection", "capability-gated")
    assert_field(hosts["claude-code"], "model_selection", "attestation-required")
    assert_field(hosts["local-ai"], "worker_selection", "validation-triage-only")
    assert set(report["execution_modes"]) == {"serial", "direct-child-agent", "independent-thread"}
    assert "explicit user request" in report["execution_modes"]["independent-thread"]["authority"]
    for adapter_id in ("codex-v1", "github-copilot-v1", "claude-code-v1"):
        adapter = report["surface_adapters"][adapter_id]
        assert_field(
            adapter["capability_modes"],
            "context-inheritance-control",
            "controlled-worker-context",
        )
        assert "context-inheritance-control" in adapter["capability_requirements"]["native-subagents"]
    assert_field(report["risk_routing"], "status", "benchmark-gated")
    assert_field(report["risk_routing"], "selection_mode", "declarative-manual")
    assert_field(report["risk_routing"], "verified_at", "2026-07-19")
    risk_routes = {route["id"]: route for route in report["risk_routing"]["routes"]}
    assert risk_routes["simple-bounded"]["profiles"] == ["implementation-low"]
    assert_has_all(
        risk_routes["simple-bounded"]["when_all"],
        "one owning deterministic verifier is known",
    )
    root_policy = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert_has_all(
        root_policy,
        "Bounded governor",
        "use `implementation-low`",
        "verify once, then stop",
        "Do not delegate or repeat unchanged work without new evidence",
    )
    assert_has_all(report["validation_authority"]["authoritative"], "deterministic command exit codes")
    assert_has_all(
        markdown,
        "Cost Guidance",
        "Execution Modes",
        "Risk Routing",
        "declarative/manual catalog guidance",
        "Host Compatibility",
        "Validation Authority",
        "deterministic command exit codes",
    )


def test_worker_profile_catalog_rejects_unsafe_execution_and_risk_routes(_tmp):
    catalog = json.loads(json.dumps(workflow_workers.load_worker_profile_config()))
    catalog["execution_modes"].pop("direct-child-agent")
    catalog["risk_routing"]["routes"][0]["profiles"].append("unknown-profile")

    issues = workflow_workers.validate_profile_catalog(catalog)

    assert_contains_each(
        issues,
        "execution_modes is missing: direct-child-agent",
        "references unknown profile 'unknown-profile'",
    )


def test_semantic_profiles_reject_host_and_model_axis_fields(_tmp):
    catalog = json.loads(json.dumps(workflow_workers.load_worker_profile_config()))
    profile = catalog["profile_sets"]["portable-default"]["planning-high"]
    profile["host_surface"] = "codex"
    profile["model_provider"] = "openai"
    profile["reasoning_effort"] = "high"

    issues = workflow_workers.validate_profile_catalog(catalog)

    assert_contains_each(
        issues,
        "host_surface is not allowed",
        "model_provider is not allowed",
        "reasoning_effort is not allowed",
    )


def test_worker_profile_catalog_rejects_schema_v1(_tmp):
    catalog = json.loads(json.dumps(workflow_workers.load_worker_profile_config()))
    catalog["schema_version"] = 1
    catalog.pop("execution_modes")
    catalog.pop("risk_routing")

    assert_contains_each(
        workflow_workers.validate_profile_catalog(catalog),
        "schema_version must be one of: 3",
        "execution_modes must be an object",
        "risk_routing must be an object",
    )


def test_worker_prompt_overlay_key_is_authoritative(_tmp):
    catalog = json.loads(json.dumps(workflow_workers.load_worker_profile_config()))
    catalog["model_prompt_overlays"]["generic-v1"]["id"] = "spoofed-id"

    overlays = workflow_workers.effective_model_prompt_overlays(catalog)

    assert_field(overlays["generic-v1"], "id", "generic-v1")


def test_worker_profile_catalog_rejects_overlay_unknown_fields_alias_collisions_and_ambiguous_risk_route(_tmp):
    catalog = json.loads(json.dumps(workflow_workers.load_worker_profile_config()))
    catalog["model_prompt_overlays"]["openai-5.6-v1"]["tool_policy"] = "bounded-write"
    catalog["model_prompt_overlays"]["openai-5.6-v1"]["reasoning_effort"] = "max"
    catalog["model_prompt_overlays"]["openai-5.6-v1"]["primary"] = {"model": "gpt-5.6-sol"}
    catalog["model_compatibility"]["aliases"].append(
        {
            "model_provider": "openai",
            "alias": "gpt-5.5",
            "canonical_model": "gpt-5.6-sol",
        }
    )
    catalog["risk_routing"]["routes"][0]["when_all"] = ["also true"]
    catalog["model_compatibility"]["sources"][0]["url"] = "http://example.test/models"
    catalog["model_prompt_overlays"]["unreferenced-v1"] = {
        "version": 1,
        "generation": "generic",
        "promotion_state": "experimental",
        "instructions": ["Remain bounded."],
        "source_refs": [],
    }

    issues = workflow_workers.validate_profile_catalog(catalog)
    projected_overlay = workflow_workers.effective_model_prompt_overlays(catalog)["openai-5.6-v1"]

    assert_contains_each(
        issues,
        "has unsupported fields: primary, reasoning_effort, tool_policy",
        "alias collides with exact model-provider/model 'openai/gpt-5.5'",
        "must declare exactly one of when_any or when_all",
        "must use HTTPS on an approved provider documentation domain",
        "unreferenced-v1 is not referenced by any exact model mapping",
    )
    assert_lacks_all(projected_overlay, "primary", "reasoning_effort", "tool_policy")
    assert set(projected_overlay).issubset(
        workflow_workers.OVERLAY_ALLOWED_FIELDS | {"id", "delivery_directive"}
    )

    observation = runtime_observation(capabilities=["model-selection", "deliberation-control"])
    resolved = workflow_workers.resolve_model_delivery(
        {"codex": [{"model_provider": "openai", "model": "gpt-5.5", "deliberation_tier": "medium", "agent_type": "worker"}]},
        observation,
        catalog=catalog,
        expected_workflow="story-flow",
        expected_run_id="run-a",
        expected_phase="execute",
    )
    assert_field(resolved["prompt_overlay"], "id", "openai-5.5-v1")


def test_direct_api_routes_require_their_native_model_provider(_tmp):
    catalog = json.loads(json.dumps(workflow_workers.load_worker_profile_config()))
    catalog["surface_route_sets"]["planning-high"]["openai-responses-api"][0][
        "model_provider"
    ] = "anthropic"

    assert_contains(
        workflow_workers.validate_profile_catalog(catalog),
        "must be openai for direct API surface openai-responses-api",
    )


def test_forbidden_overlay_fields_never_reach_context_resume_or_handoff(tmp):
    module_dir = write_fixture(tmp)
    write_guidance_savings_fixture(tmp)
    with module_file(module_dir, "WORKFLOW.md").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "\n## Runtime Overlay Authority Baseline\n\n"
            + "Runtime projections remain behind a compact evidence handle.\n" * 500
        )
    run_dir = run_dir_for(module_dir)
    observation_path = run_file(run_dir, "validation", "runtime-observation.json")
    write_json(
        observation_path,
        runtime_observation(capabilities=["model-selection", "deliberation-control"]),
    )
    catalog = json.loads(json.dumps(workflow_workers.load_worker_profile_config()))
    overlay = catalog["model_prompt_overlays"]["openai-5.5-v1"]
    overlay["tool_policy"] = "network-write"
    overlay["reasoning_effort"] = "max"
    overlay["primary"] = {"provider": "other", "model": "authority-leak"}
    catalog_path = tmp / "worker-profiles-invalid-overlay.json"
    write_json(catalog_path, catalog)
    original_catalog_path = workflow_workers.PROFILE_CONFIG_PATH
    workflow_workers.PROFILE_CONFIG_PATH = catalog_path
    try:
        observation = workflow_run_support.load_runtime_observation_packet(
            tmp,
            "story-flow",
            "run-a",
            common.relative(tmp, observation_path),
        )
        context = workflow_run_support.context_workflow_run(
            tmp,
            "story-flow",
            run_id="run-a",
            write=True,
            runtime_observation=observation,
        )
        handoff = workflow_run_support.handoff_workflow_run(
            tmp,
            "story-flow",
            run_id="run-a",
        )
        resume = workflow_repo_manager.compact_resume_run_report(
            workflow_run_support.resume_workflow_run(tmp, "story-flow", run_id="run-a")
        )
        packet = read_json(
            run_file(run_dir, "artifacts", "context", "context-packet.json")
        )
    finally:
        workflow_workers.PROFILE_CONFIG_PATH = original_catalog_path

    projections = [
        context["execution_profile"]["prompt_overlay"],
        handoff["execution_profile"]["prompt_overlay"],
        resume["execution_profile"]["prompt_overlay"],
        packet["execution_profile"]["prompt_overlay"],
    ]
    for projection in projections:
        assert_lacks_all(projection, "primary", "reasoning_effort", "tool_policy")
    assert_field(context["execution_profile"]["prompt_overlay"], "id", "openai-5.5-v1")

    malformed_catalog = json.loads(json.dumps(catalog))
    malformed_overlay = malformed_catalog["model_prompt_overlays"]["openai-5.5-v1"]
    malformed_overlay["version"] = {"tool_policy": "network-write"}
    malformed_overlay["instructions"] = [
        {"primary": {"provider": "other", "model": "authority-leak"}}
    ]
    malformed_overlay["source_refs"] = [{"reasoning_effort": "max"}]
    malformed_catalog_path = tmp / "worker-profiles-malformed-overlay.json"
    write_json(malformed_catalog_path, malformed_catalog)
    workflow_workers.PROFILE_CONFIG_PATH = malformed_catalog_path
    try:
        effective = workflow_workers.effective_model_prompt_overlays(malformed_catalog)
        malformed_context = workflow_run_support.context_workflow_run(
            tmp,
            "story-flow",
            run_id="run-a",
            write=True,
            runtime_observation=observation,
        )
        malformed_handoff = workflow_run_support.handoff_workflow_run(
            tmp,
            "story-flow",
            run_id="run-a",
        )
        malformed_resume = workflow_repo_manager.compact_resume_run_report(
            workflow_run_support.resume_workflow_run(tmp, "story-flow", run_id="run-a")
        )
        malformed_packet = read_json(
            run_file(run_dir, "artifacts", "context", "context-packet.json")
        )
    finally:
        workflow_workers.PROFILE_CONFIG_PATH = original_catalog_path

    assert "openai-5.5-v1" not in effective
    malformed_projections = [
        malformed_context["execution_profile"]["prompt_overlay"],
        malformed_handoff["execution_profile"]["prompt_overlay"],
        malformed_resume["execution_profile"]["prompt_overlay"],
        malformed_packet["execution_profile"]["prompt_overlay"],
    ]
    for projection in malformed_projections:
        assert_field(projection, "id", "generic-v1")
        assert isinstance(projection["version"], int)
        assert_lacks_all(projection, "primary", "reasoning_effort", "tool_policy")


def test_worker_runtime_observation_selects_overlay_without_changing_semantic_profile(tmp):
    module_dir = write_fixture(tmp)
    manifest = read_json(module_file(module_dir, "module.json"))
    observation = runtime_observation(capabilities=["model-selection", "deliberation-control"])

    generic = workflow_workers.workflow_execution_profile(manifest, "execute")
    attested = workflow_workers.workflow_execution_profile(
        manifest,
        "execute",
        runtime_observation=observation,
    )

    assert_fields(
        generic,
        endpoint_status="unattested-active",
        capability_status="unavailable",
        effective_execution_mode="serial-active-model",
        declared_model="",
        observed_model="",
    )
    assert_field(generic["prompt_overlay"], "id", "generic-v1")
    assert_field(generic["surface_adapter"], "id", "generic-v1")
    assert_fields(
        attested,
        endpoint_status="attested-primary",
        capability_status="attested",
        effective_execution_mode="declared-endpoint",
        declared_model="gpt-5.5",
        observed_model="gpt-5.5",
        observed_deliberation="medium",
    )
    assert_field(attested["prompt_overlay"], "id", "openai-5.5-v1")
    assert_field(attested["surface_adapter"], "id", "codex-v1")
    assert_has_all(attested["prompt_overlay"]["delivery_directive"], "expected outcome", "stopping rule")
    for field in ("profile_id", "profile_purpose", "prompt_adapter", "tool_policy", "expected_output", "validation_gate"):
        assert generic[field] == attested[field]

    blank_effort = json.loads(json.dumps(observation))
    blank_effort["host"]["capabilities"] = ["unsupported-capability"]
    blank = workflow_workers.workflow_execution_profile(
        manifest,
        "execute",
        runtime_observation=blank_effort,
        workflow="story-flow",
        run_id="run-a",
    )
    assert_fields(
        blank,
        endpoint_status="attested-model-only",
        capability_status="unavailable",
        effective_execution_mode="serial-active-model",
        observed_model="gpt-5.5",
    )
    assert_field(blank["prompt_overlay"], "id", "openai-5.5-v1")
    assert_field(blank["surface_adapter"], "id", "generic-v1")
    assert_has_all(blank["fallback_reason"], "unsupported capabilities")

    sol_observation = json.loads(json.dumps(observation))
    sol_observation["model"].update(model="gpt-5.6-sol", observed_deliberation="high")
    sol = workflow_workers.resolve_model_delivery(
        {"codex": [{"model_provider": "openai", "model": "gpt-5.6-sol", "deliberation_tier": "high", "agent_type": "reviewer"}]},
        sol_observation,
        expected_workflow="story-flow",
        expected_run_id="run-a",
        expected_phase="execute",
    )
    compact_55 = workflow_repo_manager.compact_execution_profile_report(attested)
    compact_56 = workflow_repo_manager.compact_execution_profile_report(
        {
            "profile_id": "review-high",
            "instruction_header": ["Review evidence and preserve final authority."],
            **sol,
        }
    )
    assert_field(compact_55["prompt_overlay"], "id", "openai-5.5-v1")
    assert_field(compact_56["prompt_overlay"], "id", "openai-5.6-v1")
    assert_has_all(compact_56["prompt_overlay"]["delivery_directive"], "prompt lean", "instruction once")
    assert compact_55["prompt_overlay"]["delivery_directive"] != compact_56["prompt_overlay"]["delivery_directive"]


def test_worker_runtime_observation_handles_fallback_partial_alias_and_effort_mismatch(tmp):
    module_dir = write_fixture(tmp)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["worker_profiles"]["phase_assignments"]["execute"] = "review-high"
    fallback = workflow_workers.workflow_execution_profile(
        manifest,
        "execute",
        runtime_observation=runtime_observation(
            model="gpt-5.6-terra",
            observed_deliberation="high",
            capabilities=["model-selection", "deliberation-control"],
            model_source="provider-response",
        ),
    )
    assert_fields(
        fallback,
        endpoint_status="attested-alternate",
        capability_status="attested",
        effective_execution_mode="declared-endpoint",
        observed_model="gpt-5.6-terra",
    )
    assert_field(fallback["prompt_overlay"], "id", "openai-5.6-v1")

    manifest["worker_profiles"]["phase_assignments"]["execute"] = "coordination-low-cost"
    partial = workflow_workers.workflow_execution_profile(
        manifest,
        "execute",
        runtime_observation=runtime_observation(
            model="gpt-5.6-luna",
            observed_deliberation="medium",
        ),
    )
    assert_fields(
        partial,
        endpoint_status="attested-primary",
        capability_status="partial",
        effective_execution_mode="serial-active-model",
        observed_deliberation="medium",
    )
    assert_has_all(partial["fallback_reason"], "capability evidence is incomplete", "differs from declared route tier")

    alias = workflow_workers.workflow_execution_profile(
        manifest,
        "execute",
        runtime_observation=runtime_observation(
            model="gpt-5.6",
            observed_deliberation="low",
            capabilities=["model-selection", "deliberation-control"],
            model_source="provider-response",
        ),
    )
    assert_fields(
        alias,
        endpoint_status="active-model-fallback",
        effective_execution_mode="serial-active-model",
        observed_model="gpt-5.6",
    )
    assert_field(alias["prompt_overlay"], "id", "openai-5.6-v1")
    assert_has_all(alias["fallback_reason"], "overlay selection only", "exact observation is preserved")


def test_host_surface_adapters_do_not_infer_model_identity_or_cross_provider_capabilities(tmp):
    module_dir = write_fixture(tmp)
    manifest = read_json(module_file(module_dir, "module.json"))

    def observe(
        host_surface,
        model_provider,
        model,
        capabilities,
        *,
        include_host=True,
        include_model=True,
        host_source="host-runtime",
        model_source="host-runtime",
    ):
        return workflow_workers.workflow_execution_profile(
            manifest,
            "execute",
            workflow="story-flow",
            run_id="run-a",
            runtime_observation=runtime_observation(
                host_surface=host_surface,
                model_provider=model_provider,
                model=model,
                capabilities=capabilities,
                include_host=include_host,
                include_model=include_model,
                host_source=host_source,
                model_source=model_source,
            ),
        )

    copilot = observe(
        "github-copilot",
        "unknown",
        "unused",
        [
            "native-subagents",
            "complete-thread-tree",
            "complete-usage-telemetry",
            "isolated-worker-runtime",
            "context-inheritance-control",
            "session-resume",
        ],
        include_model=False,
    )
    assert_fields(
        copilot,
        endpoint_status="attested-host-only",
        observed_host_surface="github-copilot",
        observed_model_provider="",
    )
    assert_field(copilot["prompt_overlay"], "id", "generic-v1")
    assert_fields(
        copilot["surface_adapter"],
        id="github-copilot-v1",
        orchestration_mode="direct-tools",
        available_orchestration_mode="native-subagents",
        continuation_mode="host-session-resume",
    )
    assert_field(
        copilot["surface_adapter"]["enabled_optimizations"],
        "context-inheritance-control",
        "controlled-worker-context",
    )
    compact_copilot = workflow_repo_manager.compact_execution_profile_report(copilot)
    assert_field(compact_copilot, "observed_host_surface", "github-copilot")
    assert_field(compact_copilot["prompt_overlay"], "id", "generic-v1")
    assert_fields(
        compact_copilot["surface_adapter"],
        id="github-copilot-v1",
        orchestration_mode="direct-tools",
        available_orchestration_mode="native-subagents",
    )

    claude_on_copilot = observe(
        "github-copilot",
        "anthropic",
        "claude-sonnet-4.5",
        ["session-resume"],
    )
    assert_field(claude_on_copilot, "endpoint_status", "active-model-fallback")
    assert_field(claude_on_copilot["prompt_overlay"], "id", "anthropic-claude-v1")
    assert_field(claude_on_copilot["surface_adapter"], "id", "github-copilot-v1")

    claude = observe(
        "claude-code",
        "anthropic",
        "claude-sonnet-4.5",
        ["native-subagents", "deterministic-hooks", "session-resume"],
    )
    assert_field(claude, "endpoint_status", "attested-primary")
    assert_field(claude["prompt_overlay"], "id", "anthropic-claude-v1")
    assert_field(claude["surface_adapter"], "id", "claude-code-v1")

    responses = observe(
        "openai-responses-api",
        "openai",
        "gpt-5.5",
        [
            "prompt-cache-control",
            "prompt-cache-telemetry",
            "reasoning-continuation",
            "hosted-program-orchestration",
        ],
        host_source="provider-response",
        model_source="provider-response",
    )
    assert_field(responses, "endpoint_status", "attested-primary")
    assert_fields(
        responses["surface_adapter"],
        id="openai-responses-v1",
        orchestration_mode="hosted-program",
        continuation_mode="provider-reasoning-continuation",
        cache_mode="explicit-prompt-cache",
    )

    model_only = observe(
        "unknown",
        "anthropic",
        "claude-sonnet-4.5",
        [],
        include_host=False,
        model_source="provider-response",
    )
    assert_field(model_only, "endpoint_status", "attested-model-only")
    assert_field(model_only["prompt_overlay"], "id", "anthropic-claude-v1")
    assert_field(model_only["surface_adapter"], "id", "generic-v1")

    invalid_model = runtime_observation(
        host_surface="github-copilot",
        model_provider="unknown",
        model="",
        capabilities=["session-resume"],
    )
    host_preserved = workflow_workers.workflow_execution_profile(
        manifest,
        "execute",
        workflow="story-flow",
        run_id="run-a",
        runtime_observation=invalid_model,
    )
    assert_field(host_preserved, "endpoint_status", "attested-host-only")
    assert_field(host_preserved["surface_adapter"], "id", "github-copilot-v1")
    assert_field(host_preserved["prompt_overlay"], "id", "generic-v1")

    invalid_host = runtime_observation(
        host_surface="not-a-host",
        model_provider="anthropic",
        model="claude-sonnet-4.5",
    )
    model_preserved = workflow_workers.workflow_execution_profile(
        manifest,
        "execute",
        workflow="story-flow",
        run_id="run-a",
        runtime_observation=invalid_host,
    )
    assert_field(model_preserved, "endpoint_status", "attested-model-only")
    assert_field(model_preserved["surface_adapter"], "id", "generic-v1")
    assert_field(model_preserved["prompt_overlay"], "id", "anthropic-claude-v1")

    wrong_source = runtime_observation(
        host_surface="codex",
        capabilities=["context-inheritance-control"],
        host_source="provider-response",
    )
    wrong_source_issues = workflow_workers.runtime_observation_issues(wrong_source)
    assert_contains_each(
        wrong_source_issues,
        "cannot attest host-runtime capabilities",
        "must use an API surface",
    )

    incompatible_api = runtime_observation(
        host_surface="openai-responses-api",
        model_provider="anthropic",
        model="claude-sonnet-4.5",
        host_source="provider-response",
        model_source="provider-response",
    )
    assert_contains(
        workflow_workers.runtime_observation_issues(incompatible_api),
        "requires model provider openai",
    )


def test_worker_report_rejects_experimental_profile_as_accepted_default(tmp):
    module_dir = write_fixture(tmp)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["worker_profiles"]["phase_assignments"]["execute"] = "review-high"
    write_json(module_file(module_dir, "module.json"), manifest)

    report = workflow_workers.workflow_workers_report(tmp, workflow_names=["story-flow"])

    assert_not_ok(report)
    assert_contains(report["workflows"][0]["issues"], "uses experimental profile 'review-high'")


def test_task_graph_rejects_missing_deps_and_cycles(tmp):
    module_dir = write_fixture(tmp)
    manifest = workflow_manifest_with_tasks("story-flow")
    manifest["tasks"] = [
        {"id": "first", "summary": "First.", "phase": "execute", "depends_on": ["missing"]},
        {"id": "second", "summary": "Second.", "phase": "execute", "depends_on": ["third"]},
        {"id": "third", "summary": "Third.", "phase": "execute", "depends_on": ["second"]},
        {"id": "fourth", "summary": "Fourth.", "phase": "execute", "depends_on": [{"id": "first"}]},
    ]
    write_json(module_file(module_dir, "module.json"), manifest)

    errors, _warnings, _modules = validate_automations.validate_automations(tmp, workflow_name="story-flow")

    assert_contains_each(errors, "depends_on unknown task 'missing'", "dependency cycle", "depends_on[0] must be a non-empty string")


def test_v1_layout_rejected(tmp):
    old_dir = tmp / "automations" / "old-flow"
    write_text(old_dir / "README.md", "# Old Flow")
    errors, _warnings, _modules = validate_automations.validate_automations(tmp)
    assert_contains(errors, "module.json")


def test_registry_generated_from_module_json(tmp):
    module_dir = write_fixture(tmp)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["routing"] = {
        "terms": ["story", "feature", "acceptance"],
        "activation_terms": ["story", "feature"],
        "threshold": 3,
        "winner_margin": 1,
    }
    write_json(module_file(module_dir, "module.json"), manifest)
    registry = sync_automation_routing.build_registry_data_with_options(tmp, use_local_ai=False)
    entry = registry["automations"][0]
    assert_fields(
        entry,
        start_file="WORKFLOW.md",
        contract_file="module.json",
        outputs=[
            "runs/<run-id>/run.json",
            "runs/<run-id>/REPORT.md",
            "runs/<run-id>/artifacts/context/context-packet.json",
            "runs/<run-id>/artifacts/documentation/documentation-delta.json",
            "runs/<run-id>/artifacts/documentation/documentation-delta.md",
            "runs/<run-id>/validation/context-evidence-start.json",
            "runs/<run-id>/validation/context-evidence-resume.json",
            "runs/<run-id>/validation/context-evidence-finish.json",
        ],
    )
    assert_fields(
        entry["worker_profiles"],
        mode="auto-when-supported",
        max_parallel_workers=1,
        phase_assignments={"execute": "general-medium"},
    )
    assert_fields(
        entry["routing"],
        terms=["story", "feature", "acceptance"],
        activation_terms=["story", "feature"],
        threshold=3,
        winner_margin=1,
    )


def test_registry_renders_v3_command_argv_instead_of_opaque_ids(tmp):
    module_dir = write_fixture(tmp)
    current = read_json(module_file(module_dir, "module.json"))
    normalized, errors, _warnings = manifests.module_contract_v3.normalize_module_contract(current)
    assert_empty(errors)
    normalized["commands"] = [
        {
            "id": "spaced-argument",
            "argv": ["tool", "arg with spaces"],
            "timeout_seconds": 300,
            "working_directory": "repository",
            "effects": [],
        }
    ]
    expected_specs = normalized["commands"]
    write_json(module_file(module_dir, "module.json"), normalized)

    registry = sync_automation_routing.build_registry_data_with_options(
        tmp,
        use_local_ai=False,
    )
    entry = registry["automations"][0]

    assert_field(entry, "commands", expected_specs)
    assert_field(entry, "scripts", [["tool", "arg with spaces"]])
    assert all(set(("id", "argv", "effects")) <= set(command) for command in entry["commands"])
    assert entry["scripts"][0][1] == "arg with spaces"


def test_routing_metadata_is_required_and_rejects_unsafe_contracts(tmp):
    module_dir = write_fixture(tmp)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest.pop("routing")
    write_json(module_file(module_dir, "module.json"), manifest)

    errors, _warnings, _modules = validate_automations.validate_automations(tmp, workflow_name="story-flow")

    assert_contains(errors, "module.json routing is required")

    unsafe_contracts = [
        (
            {"terms": [{"id": "story"}], "activation_terms": ["story"], "threshold": 2, "winner_margin": 1},
            "routing.terms must be a list of non-empty strings",
        ),
        (
            {"terms": ["the"], "activation_terms": ["the"], "threshold": 2, "winner_margin": 1},
            "routing.terms must include a specific routing concept",
        ),
        (
            {"terms": ["---"], "activation_terms": ["---"], "threshold": 2, "winner_margin": 1},
            "routing.terms must include a specific routing concept",
        ),
        (
            {"terms": ["story"], "activation_terms": ["workflow"], "threshold": 2, "winner_margin": 1},
            "routing.activation_terms must include a non-generic routing concept",
        ),
        (
            {"terms": ["story"], "activation_terms": ["story"], "threshold": 2, "winner_margin": 1},
            "cannot reach threshold 2",
        ),
        (
            {
                "terms": ["workflow", "automation"],
                "activation_terms": ["story"],
                "threshold": 2,
                "winner_margin": 1,
            },
            "cannot reach threshold 2",
        ),
        (
            {"terms": ["story"], "activation_terms": ["story"], "threshold": 1, "winner_margin": 0},
            "routing.threshold must be an integer of at least 2",
        ),
    ]
    for routing, expected in unsafe_contracts:
        manifest["routing"] = routing
        write_json(module_file(module_dir, "module.json"), manifest)
        contract_errors, _warnings, _modules = validate_automations.validate_automations(
            tmp,
            workflow_name="story-flow",
        )
        assert_contains(contract_errors, expected)

    assert_contains(
        contract_errors,
        "routing.winner_margin must be an integer of at least 1",
    )


def test_workflow_routing_update_tool_is_dry_run_idempotent_and_versioned(tmp):
    script_path = Path(__file__).resolve().parent / "update_workflow_routing.py"
    assert script_path.is_file(), script_path
    spec = importlib.util.spec_from_file_location("update_workflow_routing_test", script_path)
    assert spec is not None and spec.loader is not None
    updater = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)
    module_dir = write_fixture(tmp)
    module_path = module_file(module_dir, "module.json")
    before = read_json(module_path)
    terms = ["story", "feature", "acceptance"]
    activation_terms = ["story", "feature"]

    planned = updater.routing_update_report(
        tmp,
        name="story-flow",
        terms=terms,
        activation_terms=activation_terms,
        threshold=3,
        winner_margin=1,
        write=False,
    )
    assert_ok(planned)
    assert_status(planned, "planned")
    assert read_json(module_path) == before, "dry-run must not edit module.json"

    written = updater.routing_update_report(
        tmp,
        name="story-flow",
        terms=terms,
        activation_terms=activation_terms,
        threshold=3,
        winner_margin=1,
        write=True,
    )
    updated = read_json(module_path)
    assert_ok(written)
    assert_status(written, "written")
    assert_field(
        updated,
        "routing",
        {
            "terms": terms,
            "activation_terms": activation_terms,
            "threshold": 3,
            "winner_margin": 1,
        },
    )
    assert updated["version"] != before["version"], updated

    unchanged = updater.routing_update_report(
        tmp,
        name="story-flow",
        terms=terms,
        activation_terms=activation_terms,
        threshold=3,
        winner_margin=1,
        write=True,
    )
    assert_status(unchanged, "unchanged")
    assert_field(read_json(module_path), "version", updated["version"])

    blocked = updater.routing_update_report(
        tmp,
        name="story-flow",
        terms=terms,
        activation_terms=activation_terms,
        threshold=1,
        winner_margin=1,
        write=True,
    )
    assert_not_ok(blocked)
    assert_status(blocked, "blocked")
    assert_contains(blocked["issues"], "threshold must be an integer of at least 2")
    assert read_json(module_path) == updated, "blocked updates must not edit module.json"

    unreachable = updater.routing_update_report(
        tmp,
        name="story-flow",
        terms=["story"],
        activation_terms=["story"],
        threshold=2,
        winner_margin=1,
        write=True,
    )
    assert_not_ok(unreachable)
    assert_status(unreachable, "blocked")
    assert_contains(unreachable["issues"], "cannot reach threshold 2")
    assert read_json(module_path) == updated, "unreachable updates must not edit or version module.json"

    generic_only = updater.routing_update_report(
        tmp,
        name="story-flow",
        terms=["workflow", "automation"],
        activation_terms=["story"],
        threshold=2,
        winner_margin=1,
        write=True,
    )
    assert_not_ok(generic_only)
    assert_status(generic_only, "blocked")
    assert_contains(generic_only["issues"], "cannot reach threshold 2")
    assert read_json(module_path) == updated, "generic-only updates must not edit or version module.json"


def test_create_workflow_scaffolds_v3(tmp):
    write_skill(tmp)
    write_guidance_savings_fixture(tmp)
    written = create_workflow.create_workflow(scaffold_args(tmp))
    relative = {path.relative_to(tmp).as_posix() for path in written}
    assert relative == {
        "automations/new-flow/WORKFLOW.md",
        "automations/new-flow/diagrams/workflow-connection-diagram.mmd",
        "automations/new-flow/diagrams/workflow-connection-diagram.svg",
        "automations/new-flow/diagrams/workflow-process-diagram.mmd",
        "automations/new-flow/diagrams/workflow-process-diagram.svg",
        "automations/new-flow/instructions.md",
        "automations/new-flow/metadata/workflow-metadata.json",
        "automations/new-flow/module.json",
        "automations/new-flow/suites/workflow-evals.json",
        "automations/new-flow/templates/lean-plan.md",
        "automations/new-flow/templates/plan.md",
    }
    start_text = (tmp / "automations" / "new-flow" / "WORKFLOW.md").read_text(encoding="utf-8")
    instructions_text = (tmp / "automations" / "new-flow" / "instructions.md").read_text(encoding="utf-8")
    manifest_text = read_json(tmp / "automations" / "new-flow" / "module.json")
    metadata_text = read_json(tmp / "automations" / "new-flow" / "metadata" / "workflow-metadata.json")
    eval_text = read_json(tmp / "automations" / "new-flow" / "suites" / "workflow-evals.json")
    assert_field(manifest_text, "schema_version", 3)
    assert_field(manifest_text, "extensions", {})
    assert all(isinstance(command, dict) for command in manifest_text["commands"])
    assert all(isinstance(command.get("argv"), list) for command in manifest_text["commands"])
    commands_by_argv = {
        tuple(command["argv"]): command for command in manifest_text["commands"]
    }
    eval_argv = (
        "python",
        "-B",
        ".agents/manage.py",
        "eval-workflow",
        "--name",
        "new-flow",
        "--suite",
        "automations/new-flow/suites/workflow-evals.json",
    )
    lifecycle_scorecard_argv = (
        "python",
        "-B",
        ".agents/manage.py",
        "workflow",
        "scorecard",
        "--name",
        "new-flow",
        "--format",
        "json",
    )
    safe_scorecard_argv = (
        "python",
        "-B",
        ".agents/manage.py",
        "workflow",
        "scorecard",
        "--name",
        "new-flow",
        "--no-lifecycle",
        "--summary",
        "--compact",
        "--format",
        "json",
    )
    assert commands_by_argv[eval_argv]["effects"] == ["temporary_write"]
    assert commands_by_argv[lifecycle_scorecard_argv]["effects"] == ["temporary_write"]
    assert commands_by_argv[safe_scorecard_argv]["effects"] == []
    assert commands_by_argv[safe_scorecard_argv]["id"] in manifest_text[
        "strict_read_only_commands"
    ]
    assert commands_by_argv[eval_argv]["id"] not in manifest_text[
        "strict_read_only_commands"
    ]
    assert commands_by_argv[lifecycle_scorecard_argv]["id"] not in manifest_text[
        "strict_read_only_commands"
    ]
    command_argvs = [command["argv"] for command in manifest_text["commands"]]

    def declares(*tokens):
        return any(
            argv[index : index + len(tokens)] == list(tokens)
            for argv in command_argvs
            for index in range(len(argv) - len(tokens) + 1)
        )
    assert_field(
        manifest_text,
        "routing",
        {
            "terms": ["new", "flow"],
            "activation_terms": ["new-flow", "new", "flow"],
            "threshold": 2,
            "winner_margin": 1,
        },
    )
    assert len(manifest_text["routing"]["terms"]) >= manifest_text["routing"]["threshold"]
    assert_has_all(
        start_text,
        "Decisions",
        "Evidence",
        "workflow hooks --name new-flow --format json",
        "workflow hooks --all --check --format json",
        "fresh-agent-packet",
        "finish --summary --compact --format json",
        "## Process Diagram",
        "diagrams/workflow-process-diagram.svg",
        "## Connection Diagram",
        "diagrams/workflow-connection-diagram.svg",
        "out-of-scope",
    )
    assert_lacks_all(
        start_text,
        "merge-readiness",
        "ready-packet",
        "can-i-finish",
        "completion-packet",
    )
    assert_has_all(
        instructions_text,
        "## Always Load",
        "## Stop Rules",
        "## Completion Contract",
        "fresh-agent-packet",
        "local AI output as advisory triage only",
        "Decision:",
        "Evidence:",
        "out-of-scope",
    )
    assert_has_all(start_text, "semantic profile", "model-provider prompt overlay", "host-surface adapter")
    assert_lacks_all(start_text, "reasoning_effort", "primary/fallback")
    assert_has_all(
        manifest_text["outputs"],
        "runs/<run-id>/artifacts/context/context-packet.json",
        "runs/<run-id>/artifacts/documentation/documentation-delta.json",
    )
    assert declares("workflow", "checkpoint", "--name", "new-flow")
    assert declares("fresh-agent-packet", "--summary", "--compact", "--format", "json")
    assert declares("startup-context", "--summary", "--compact", "--format", "json")
    assert declares("next-action", "--summary", "--compact", "--format", "json")
    assert declares("changed-context", "--summary", "--compact", "--format", "json")
    assert declares("review-loop", "--max-units", "20", "--max-estimated-tokens", "8000")
    assert declares("finish", "--summary", "--compact", "--format", "json")
    for removed_command in (
        "merge-readiness",
        "ready-packet",
        "can-i-finish",
        "completion-packet",
    ):
        assert not declares(removed_command), removed_command
    assert declares("check-changed", "--summary", "--compact", "--format", "json")
    assert declares("context-cost-benchmark", "--summary", "--compact", "--format", "json")
    assert declares("eval-workflow", "--name", "new-flow")
    assert declares("workflow", "scorecard", "--name", "new-flow")
    assert declares("workflow", "template", "resolve", "--name", "new-flow", "--template", "plan.md")
    assert declares("workflow", "template", "lint", "--name", "new-flow")
    assert declares("workflow", "metadata", "inspect", "--name", "new-flow")
    assert declares("workflow", "branch-policy")
    assert_field(manifest_text, "metadata_path", "metadata/workflow-metadata.json")
    assert len(str(metadata_text.get("updated", ""))) == 10
    assert_field(metadata_text["input_schema"], "required", ["request", "run_id"])
    assert_field(metadata_text["gates"][0], "id", "clarification")
    assert_field(metadata_text["template_layers"], "default_template", "plan.md")
    assert metadata_text["branch_policy"]["pattern"].startswith("^(feature|fix|docs")
    assert_field(manifest_text["local_ai"], "use_cases", [
        "validation-triage",
        "changed-files-summary",
        "handoff-draft",
    ])
    process_svg = (tmp / "automations" / "new-flow" / "diagrams" / "workflow-process-diagram.svg").read_text(encoding="utf-8")
    assert_has_all(process_svg, "background-color: transparent", "data-mermaid-vertical-padding", 'width="920"')
    assert_fields(
        manifest_text["worker_profiles"],
        extends="portable-default",
        phase_assignments={
            "execute": "general-medium",
            "intake": "evidence-mini",
            "record": "handoff-mini",
        },
    )
    assert_field(eval_text, "workflow_name", "new-flow")
    assert len(eval_text["evals"]) == 3
    assert "fresh-agent-packet" in json.dumps(eval_text)
    errors, warnings, _modules = validate_automations.validate_automations(tmp, workflow_name="new-flow")
    assert_empty(errors)
    process_diagram = (tmp / "automations" / "new-flow" / "diagrams" / "workflow-process-diagram.mmd").read_text(
        encoding="utf-8"
    )
    connection_diagram = (tmp / "automations" / "new-flow" / "diagrams" / "workflow-connection-diagram.mmd").read_text(
        encoding="utf-8"
    )
    assert_lacks_all(process_diagram + connection_diagram, "<")
    scorecard = workflow_scorecard.workflow_scorecard(tmp, "new-flow", run_lifecycle=False)
    diagrams = next(item for item in scorecard["checks"] if item["name"] == "mermaid-diagrams")
    assert_ok(diagrams)

    with (tmp / "automations" / "new-flow" / "WORKFLOW.md").open(
        "a",
        encoding="utf-8",
        newline="\n",
    ) as handle:
        handle.write(
            "\n## Deterministic Context Baseline\n\n"
            + "Scaffold fixture context proves effective-load savings.\n" * 500
        )
    start = workflow_run_support.start_workflow_run(tmp, "new-flow", run_id="guidance-run")
    assert_true(start, "context_packet_refreshed")
    context = read_json(tmp / "automations" / "new-flow" / "runs" / "guidance-run" / "artifacts" / "context" / "context-packet.json")
    assert_field(context["guidance_savings"], "status", "measurably-better")
    assert_true(context["guidance_savings"], "use_by_default")
    assert_true(context["guidance_savings"], "meets_minimum")
    check = workflow_run_support.context_workflow_run(tmp, "new-flow", run_id="guidance-run", check=True)
    assert_status(check["quality_gate"], "ok")


def test_create_workflow_scaffold_routing_threshold_is_reachable(_tmp):
    scaffold = create_workflow.manifest(
        "compliance-workflow",
        "Audits dependency evidence deterministically.",
        ["workflow-manager"],
        [],
    )
    routing = scaffold["routing"]

    assert routing["activation_terms"] == ["compliance-workflow", "compliance"]
    assert {"compliance", "workflow"} <= set(routing["terms"])
    assert routing_contract.routing_score_capacity(routing["terms"]) >= routing["threshold"]

    alias_scaffold = create_workflow.manifest(
        "azure-ticket",
        "Imports dependency evidence deterministically.",
        ["workflow-manager"],
        [],
    )
    alias_routing = alias_scaffold["routing"]
    assert routing_contract.routing_score_capacity(alias_routing["terms"]) >= alias_routing["threshold"]
    assert_has_all(alias_routing["terms"], "azure", "ticket", "imports")

    try:
        create_workflow.manifest("audit", "Audit", ["workflow-manager"], [])
    except ValueError as exc:
        assert_has_all(str(exc), "cannot reach threshold 2", "scaffold")
    else:
        raise AssertionError("expected an unreachable workflow routing scaffold to fail")

    generic_name_scaffold = create_workflow.manifest(
        "workflow-automation",
        "Coordinates release evidence deterministically.",
        ["workflow-manager"],
        [],
    )
    assert_has_all(generic_name_scaffold["routing"]["activation_terms"], "coordinates")
    assert routing_contract.has_non_generic_activation(generic_name_scaffold["routing"]["activation_terms"])


def test_intent_builder_recipes_list_canonical_shapes(tmp):
    _ = tmp
    report = intent_builder.recipes_report()
    recipe_ids = {recipe["id"] for recipe in report["recipes"]}

    assert_ok(report)
    assert_has_all(
        recipe_ids,
        "plan-gated-implementation",
        "evidence-only-read-only",
        "external-system-intake",
        "benchmark-evaluation",
        "documentation-diagram-review",
        "workflow-maintenance-repair",
    )
    for recipe in report["recipes"]:
        assert recipe["phases"], recipe
        assert recipe["outputs"], recipe
        assert recipe["eval_expectations"], recipe
        assert recipe["scorecard_expectations"], recipe
        assert recipe["do_not_create_when"], recipe


def test_intent_builder_propose_is_read_only_and_route_first(tmp):
    write_fixture(tmp, with_run=False)
    request = "I need a workflow to triage flaky CI failures with evidence."

    report = intent_builder.proposal_report(tmp, request, compact=False)

    assert_ok(report)
    assert_false(report, "writes")
    assert_field(report, "mode", "propose")
    assert_field(report, "action", "create-new")
    assert_field(report, "write_mode", "read-only")
    assert_field(report, "selected_workflow", report["proposed_workflow_name"])
    assert_field(report["decision"], "action", "create-new")
    assert_has_all(report, "user_only_answers", "system_derives", "validation_commands", "proposed_files")
    assert_contains(report["validation_commands"], "validate-automations --name")
    assert_contains(report["forbidden_direct_edits"], "automations/routing.md")
    assert not (tmp / "automations" / report["proposed_workflow_name"]).exists()


def test_intent_builder_propose_prefers_existing_workflow_when_overlap_is_high(tmp):
    write_fixture(tmp, "bug-ticket-workflow", with_run=False)

    report = intent_builder.proposal_report(
        tmp,
        "Fix a bug ticket with reproduction evidence and regression validation.",
        compact=False,
    )

    assert_ok(report)
    assert_field(report, "recommendation", "adjust-existing")
    assert_field(report, "target_workflow", "bug-ticket-workflow")
    assert_field(report, "action", "adjust-existing")
    assert_field(report, "selected_workflow", "bug-ticket-workflow")
    assert_field(report["decision"], "selected_workflow", "bug-ticket-workflow")
    assert "workflow adjust --name bug-ticket-workflow" in report["next_command"]


def test_intent_builder_generic_overlap_does_not_block_new_workflow(tmp):
    module_dir = write_fixture(tmp, "diagram-review-workflow", with_run=False)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["summary"] = "Coordinates Azure DevOps Mermaid diagram review, materialization checks, and validation evidence."
    write_json(module_file(module_dir, "module.json"), manifest)

    report = intent_builder.proposal_report(
        tmp,
        "Review release evidence and approval packets.",
        compact=False,
    )

    assert_ok(report)
    assert_field(report, "recommendation", "create-new")
    assert_field(report, "action", "create-new")
    assert report["target_workflow"] != "diagram-review-workflow"


def test_intent_builder_do_not_create_suppresses_fake_workflow_outputs(tmp):
    write_fixture(tmp, "story-flow", with_run=False)

    report = intent_builder.proposal_report(
        tmp,
        "single command to regenerate docs",
        compact=False,
    )

    assert_ok(report)
    assert_field(report, "recommendation", "do-not-create")
    assert_field(report, "action", "do-not-create")
    assert_field(report, "selected_workflow", "")
    assert_field(report, "proposed_workflow_name", "")
    assert_empty(report["proposed_files"])
    assert_empty(report["validation_commands"])
    assert "Keep this as a skill" in report["next_command"]


def test_intent_builder_do_not_create_wins_over_strong_overlap(tmp):
    write_fixture(tmp, "bug-ticket-workflow", with_run=False)

    report = intent_builder.proposal_report(
        tmp,
        "single command to fix a bug ticket with reproduction evidence",
        compact=False,
    )

    assert_ok(report)
    assert_field(report, "recommendation", "do-not-create")
    assert_field(report, "action", "do-not-create")
    assert_field(report, "selected_workflow", "")
    assert_field(report, "target_workflow", "")
    assert_empty(report["proposed_files"])
    assert_empty(report["validation_commands"])


def test_intent_builder_create_write_blocks_one_command_requests(tmp):
    write_fixture(tmp, "bug-ticket-workflow", with_run=False)

    report = intent_builder.create_from_request(
        tmp,
        "single command to fix a bug ticket with reproduction evidence",
        workflow_name="signal-bug-workflow",
        write=True,
        compact=False,
    )

    assert_field(report, "ok", False)
    assert_field(report, "status", "blocked")
    assert_field(report, "recommendation", "do-not-create")
    assert_field(report, "write_mode", "blocked")
    assert_field(report, "writes", False)
    assert_contains_all(report["issues"], "too small", "workflow")
    assert not (tmp / "automations" / "signal-bug-workflow").exists()


def test_intent_builder_adjust_emits_patch_plan_not_file_edits_by_default(tmp):
    write_fixture(tmp, "user-story-workflow", with_run=False)

    report = intent_builder.adjust_plan_report(
        tmp,
        "user-story-workflow",
        "Tighten implementation validation and model routing evidence.",
        compact=False,
    )

    assert_ok(report)
    assert_false(report, "writes")
    assert_field(report, "mode", "adjust")
    assert_field(report, "action", "adjust-existing")
    assert_field(report, "selected_workflow", "user-story-workflow")
    assert_field(report, "write_mode", "read-only")
    assert_contains(report["changed_paths"], "automations/user-story-workflow/WORKFLOW.md")
    assert_contains(report["changed_paths"], "automations/user-story-workflow/module.json")
    assert_contains(report["changed_paths"], "automations/user-story-workflow/suites/workflow-evals.json")
    assert_contains(report["validation_commands"], "validate-automations --name user-story-workflow --strict-phase-quality")
    assert_contains(report["validation_commands"], "eval-workflow --name user-story-workflow")
    assert_contains(report["validation_commands"], "workflow scorecard --name user-story-workflow")
    assert_contains(report["forbidden_direct_edits"], "automations/registry.json")


def test_intent_builder_create_dry_run_and_write_modes(tmp):
    write_skill(tmp, "workflow-manager")

    dry_run = intent_builder.create_from_request(
        tmp,
        "Create a workflow for partner launch evidence review.",
        workflow_name="partner-launch-workflow",
        uses_skill=["workflow-manager"],
        write=False,
        force_new=True,
        compact=False,
    )

    assert_ok(dry_run)
    assert_field(dry_run, "status", "dry-run")
    assert_false(dry_run, "writes")
    assert_field(dry_run, "write_mode", "dry-run")
    assert_field(dry_run, "selected_workflow", "partner-launch-workflow")
    assert not (tmp / "automations" / "partner-launch-workflow").exists()

    written = intent_builder.create_from_request(
        tmp,
        "Create a workflow for partner launch evidence review.",
        workflow_name="partner-launch-workflow",
        uses_skill=["workflow-manager"],
        write=True,
        force_new=True,
        compact=False,
    )

    assert_ok(written)
    assert_field(written, "status", "written")
    assert_true(written, "writes")
    assert_field(written, "write_mode", "written")
    assert_field(written, "selected_workflow", "partner-launch-workflow")
    assert_field(written["derived_contract"], "recipe", "evidence-only-read-only")
    assert_field(written["derived_contract"], "phases", ["intake", "collect-evidence", "analyze", "report"])
    assert_contains(written["written_paths"], "automations/partner-launch-workflow/module.json")
    assert (tmp / "automations" / "partner-launch-workflow" / "WORKFLOW.md").exists()
    manifest = read_json(tmp / "automations" / "partner-launch-workflow" / "module.json")
    assert_field(
        {phase["id"]: phase["summary"] for phase in manifest["phases"]},
        "collect-evidence",
        "Collect Evidence phase for the Evidence-Only Read-Only recipe.",
    )
    assert_field(
        manifest["worker_profiles"],
        "phase_assignments",
        {
            "analyze": "evidence-mini",
            "collect-evidence": "evidence-mini",
            "intake": "evidence-mini",
            "report": "handoff-mini",
        },
    )
    assert_field(
        manifest["worker_profiles"]["delegation"],
        "economics_gate_ref",
        "delegation-balanced-v1",
    )
    assert "## Builder Recipe" in (tmp / "automations" / "partner-launch-workflow" / "WORKFLOW.md").read_text(encoding="utf-8")
    assert "## Phase: collect-evidence" in (tmp / "automations" / "partner-launch-workflow" / "instructions.md").read_text(encoding="utf-8")
    eval_suite = read_json(tmp / "automations" / "partner-launch-workflow" / "suites" / "workflow-evals.json")
    assert_field(eval_suite, "workflow_name", "partner-launch-workflow")
    assert "collect-evidence" in json.dumps(eval_suite)
    errors, _warnings, _modules = validate_automations.validate_automations(
        tmp,
        workflow_name="partner-launch-workflow",
        strict_phase_quality=True,
    )
    assert_empty(errors)


def test_intent_builder_recipe_update_preserves_v3_command_specs(tmp):
    write_skill(tmp, "workflow-manager")
    write_skill(tmp)
    create_workflow.create_workflow(scaffold_args(tmp, name="typed-flow"))
    module_path = tmp / "automations" / "typed-flow" / "module.json"
    manifest = read_json(module_path)
    normalized, errors, _warnings = manifests.module_contract_v3.normalize_module_contract(
        manifest
    )
    assert_empty(errors)
    write_json(module_path, normalized)

    intent_builder.apply_recipe_to_scaffold(
        tmp,
        "typed-flow",
        "Collect typed workflow evidence.",
        intent_builder.recipe_by_id("evidence-only-read-only"),
        "simple",
    )

    updated = read_json(module_path)
    assert_field(updated, "schema_version", 3)
    assert all(isinstance(command, dict) for command in updated["commands"])
    validation_errors, warnings, _modules = validate_automations.validate_automations(
        tmp,
        workflow_name="typed-flow",
        strict_phase_quality=True,
    )
    assert_empty(validation_errors)


def test_v3_commands_render_in_start_checklist_and_template_lint(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    current = read_json(module_file(module_dir, "module.json"))
    normalized, errors, _warnings = manifests.module_contract_v3.normalize_module_contract(
        current
    )
    assert_empty(errors)
    plan_check_command = {
        "id": "plan-check",
        "argv": [
            "python",
            "-B",
            ".agents/manage.py",
            "workflow",
            "plan-check",
            "--name",
            "story-flow",
        ],
        "timeout_seconds": 300,
        "working_directory": "repository",
        "effects": [],
    }
    spaced_command = {
        "id": "spaced-argument",
        "argv": ["tool.py", "arg with spaces"],
        "timeout_seconds": 300,
        "working_directory": "repository",
        "effects": [],
    }
    normalized["commands"].extend([plan_check_command, spaced_command])

    checklist = start_checklist.build_start_checklist(
        tmp,
        "story-flow",
        module_dir / "runs" / "typed-run",
        manifest=normalized,
    )
    assert spaced_command in checklist["declared_scripts"]
    assert '["tool.py","arg with spaces"]' in checklist["declared_script_displays"]
    assert any(
        command["argv"][3:5] == ["workflow", "plan-check"]
        for command in checklist["declared_scripts"]
    )

    write_json(module_file(module_dir, "module.json"), normalized)
    metadata_path = module_file(module_dir, "metadata", "workflow-metadata.json")
    write_json(metadata_path, {})
    report = template_layers.lint_templates(tmp, "story-flow")
    assert_contains(report["issues"], "required template has no provider")


def test_intent_builder_force_new_bypasses_overlap_without_overwrite(tmp):
    write_fixture(tmp, "bug-ticket-workflow", with_run=False)
    write_skill(tmp, "workflow-manager")

    written = intent_builder.create_from_request(
        tmp,
        "Fix a bug ticket with reproduction evidence.",
        workflow_name="custom-bug-ticket-workflow",
        uses_skill=["workflow-manager"],
        write=True,
        force_new=True,
        compact=False,
    )

    assert_ok(written)
    assert_field(written, "status", "written")
    assert_contains(written["written_paths"], "automations/custom-bug-ticket-workflow/module.json")
    assert (tmp / "automations" / "bug-ticket-workflow" / "module.json").exists()
    assert (tmp / "automations" / "custom-bug-ticket-workflow" / "module.json").exists()


def test_template_layers_resolve_and_lint(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    write_text(template_path(module_dir), "# Default Plan")
    write_text(template_path(module_dir, "lean-plan.md"), "# Lean Plan")

    default_report = template_layers.resolve_template(tmp, "story-flow", "plan.md")
    lean_report = template_layers.resolve_template(tmp, "story-flow", "plan.md", profile="lean")

    assert_ok(default_report)
    assert_field(default_report["selected"], "path", "automations/story-flow/templates/plan.md")
    assert_ok(lean_report)
    assert_field(lean_report["selected"], "path", "automations/story-flow/templates/lean-plan.md")

    write_json(module_file(module_dir, "presets", "fast", "preset.json"), {"schema_version": 1, "priority": 100})
    write_text(module_file(module_dir, "presets", "fast", "templates", "plan.md"), "# Preset Plan")
    lint_report = template_layers.lint_templates(tmp, "story-flow")

    assert_not_ok(lint_report)
    assert_contains(lint_report["issues"], "same priority")


def declared_template_layers(*, profile_roots=None, override_roots=None, preset_roots=None):
    return {
        "default_template": "plan.md",
        "override_roots": list(override_roots or []),
        "preset_roots": list(preset_roots or []),
        "profiles": {
            "default": {"template_roots": ["templates"]},
            **(profile_roots or {}),
        },
        "priorities": {
            "project-override": 10,
            "workflow-preset": 20,
            "workflow-default": 100,
            "workflow-audit": 40,
        },
        "conflict_policy": "error",
    }


def test_template_layers_execute_declared_override_and_preset_roots(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    manifest = workflow_manifest("story-flow")
    manifest["template_layers"] = declared_template_layers(
        override_roots=["custom/template-overrides/story-flow"],
        preset_roots=["custom/template-presets/story-flow"],
    )
    write_json(module_file(module_dir, "module.json"), manifest)
    write_text(tmp / "custom" / "template-overrides" / "story-flow" / "plan.md", "# Custom Override")
    write_json(
        tmp / "custom" / "template-presets" / "story-flow" / "fast" / "preset.json",
        {"schema_version": 1},
    )
    write_text(
        tmp / "custom" / "template-presets" / "story-flow" / "fast" / "templates" / "plan.md",
        "# Custom Preset",
    )

    override = template_layers.resolve_template(tmp, "story-flow", "plan.md")
    assert_ok(override)
    assert_field(override["selected"], "path", "custom/template-overrides/story-flow/plan.md")

    (tmp / "custom" / "template-overrides" / "story-flow" / "plan.md").unlink()
    preset = template_layers.resolve_template(tmp, "story-flow", "plan.md")
    assert_ok(preset)
    assert_field(
        preset["selected"],
        "path",
        "custom/template-presets/story-flow/fast/templates/plan.md",
    )


def test_template_layers_reject_resolved_profile_file_escape(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    manifest = workflow_manifest("story-flow")
    manifest["template_layers"] = declared_template_layers()
    write_json(module_file(module_dir, "module.json"), manifest)
    candidate = template_path(module_dir)
    write_text(candidate, "# Escaped Profile Template")
    outside = Path(tmp.parent) / "outside-profile-template.md"
    original_resolve = Path.resolve
    outside_resolved = original_resolve(outside, strict=False)

    def resolve_with_escape(path, strict=False):
        if path == candidate:
            return outside_resolved
        return original_resolve(path, strict=strict)

    with patch.object(Path, "resolve", resolve_with_escape):
        report = template_layers.resolve_template(tmp, "story-flow", "plan.md")

    assert_not_ok(report)
    assert report["selected"] == {}
    assert_contains(report["issues"], "unsafe template provider")


def test_template_layers_reject_resolved_preset_child_escape(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    manifest = workflow_manifest("story-flow")
    manifest["template_layers"] = declared_template_layers(
        preset_roots=["custom/template-presets/story-flow"],
    )
    manifest["template_layers"]["profiles"]["default"]["template_roots"] = []
    write_json(module_file(module_dir, "module.json"), manifest)
    preset_dir = tmp / "custom" / "template-presets" / "story-flow" / "escaped"
    write_json(preset_dir / "preset.json", {"schema_version": 1})
    write_text(preset_dir / "templates" / "plan.md", "# Escaped Preset")
    outside = Path(tmp.parent) / "outside-preset"
    original_resolve = Path.resolve
    outside_resolved = original_resolve(outside, strict=False)

    def resolve_with_escape(path, strict=False):
        if path == preset_dir:
            return outside_resolved
        return original_resolve(path, strict=strict)

    with patch.object(Path, "resolve", resolve_with_escape):
        report = template_layers.resolve_template(tmp, "story-flow", "plan.md")

    assert_not_ok(report)
    assert report["selected"] == {}
    assert_contains(report["issues"], "unsafe template provider")


def test_template_layers_execute_named_profile_without_silent_fallback(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    manifest = workflow_manifest("story-flow")
    manifest["template_layers"] = declared_template_layers(
        profile_roots={"audit": {"template_roots": ["custom/profiles/audit"]}},
    )
    write_json(module_file(module_dir, "module.json"), manifest)
    write_text(module_file(module_dir, "custom", "profiles", "audit", "plan.md"), "# Audit Plan")
    write_text(template_path(module_dir), "# Default Plan")

    audit = template_layers.resolve_template(tmp, "story-flow", "plan.md", profile="audit")
    assert_ok(audit)
    assert_field(audit["selected"], "path", "automations/story-flow/custom/profiles/audit/plan.md")

    unavailable = template_layers.resolve_template(tmp, "story-flow", "plan.md", profile="missing")
    assert_not_ok(unavailable)
    assert_status(unavailable, "profile-unavailable")
    assert unavailable["selected"] == {}
    assert_contains(unavailable["issues"], "requested template profile 'missing' is unavailable")


def test_v3_lean_profile_uses_declared_root_and_default_template_filename(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    manifest, errors, _warnings = manifests.module_contract_v3.normalize_module_contract(
        workflow_manifest("story-flow")
    )
    assert_empty(errors)
    manifest["template_layers"] = declared_template_layers(
        profile_roots={"lean": {"template_roots": ["custom/lean"]}},
    )
    write_json(module_file(module_dir, "module.json"), manifest)
    write_text(
        module_file(module_dir, "custom", "lean", "plan.md"),
        "# Ordinary Lean Profile",
    )

    report = template_layers.resolve_template(
        tmp,
        "story-flow",
        "plan.md",
        profile="lean",
    )

    assert_ok(report)
    assert_field(
        report["selected"],
        "path",
        "automations/story-flow/custom/lean/plan.md",
    )


def test_profile_template_alias_applies_only_to_declared_default_request(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    manifest = workflow_manifest("story-flow")
    manifest["template_layers"] = declared_template_layers(
        profile_roots={
            "lean": {
                "template_roots": ["templates"],
                "template": "lean-plan.md",
            }
        },
    )
    write_json(module_file(module_dir, "module.json"), manifest)
    write_text(template_path(module_dir, "lean-plan.md"), "# Lean Plan")
    write_text(template_path(module_dir, "ticket-info.md"), "# Lean Ticket Info")

    explicit = template_layers.resolve_template(
        tmp,
        "story-flow",
        "ticket-info.md",
        profile="lean",
    )
    default = template_layers.resolve_template(
        tmp,
        "story-flow",
        "plan.md",
        profile="lean",
    )

    assert_ok(explicit)
    assert_field(
        explicit["selected"],
        "path",
        "automations/story-flow/templates/ticket-info.md",
    )
    assert_ok(default)
    assert_field(
        default["selected"],
        "path",
        "automations/story-flow/templates/lean-plan.md",
    )


def test_public_template_resolve_keeps_explicit_non_default_name_with_profile(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    manifest = workflow_manifest("story-flow")
    manifest["template_layers"] = declared_template_layers(
        profile_roots={
            "lean": {
                "template_roots": ["templates"],
                "template": "lean-plan.md",
            }
        },
    )
    write_json(module_file(module_dir, "module.json"), manifest)
    write_text(template_path(module_dir, "lean-plan.md"), "# Lean Plan")
    write_text(template_path(module_dir, "ticket-info.md"), "# Lean Ticket Info")

    with patch("builtins.print") as print_output:
        exit_code = workflow_repo_manager.main(
            [
                "template-run",
                "--root",
                str(tmp),
                "resolve",
                "--name",
                "story-flow",
                "--template",
                "ticket-info.md",
                "--profile",
                "lean",
                "--format",
                "json",
            ]
        )

    print_output.assert_called_once()
    report = json.loads(print_output.call_args.args[0])
    assert exit_code == 0
    assert_field(report, "template", "ticket-info.md")
    assert_field(
        report["selected"],
        "path",
        "automations/story-flow/templates/ticket-info.md",
    )


def test_public_start_treats_v3_lean_as_ordinary_named_profile(tmp):
    workflow_name = "ordinary-lean-workflow"
    module_dir = write_fixture(tmp, workflow_name, with_run=False)
    manifest, errors, _warnings = manifests.module_contract_v3.normalize_module_contract(
        workflow_manifest(workflow_name)
    )
    assert_empty(errors)
    manifest["template_layers"] = declared_template_layers(
        profile_roots={"lean": {"template_roots": ["custom/lean"]}},
    )
    write_json(module_file(module_dir, "module.json"), manifest)
    write_text(
        module_file(module_dir, "custom", "lean", "plan.md"),
        "# Ordinary Lean Start\n\nDeclared v3 profile content.",
    )

    exit_code = workflow_repo_manager.main(
        [
            "start-run",
            "--root",
            str(tmp),
            "--name",
            workflow_name,
            "--run-id",
            "lean-run",
            "--profile",
            "lean",
            "--format",
            "json",
        ]
    )

    run_dir = run_dir_for(module_dir, "lean-run")
    assert exit_code == 0
    assert_has_all(
        run_file(run_dir, "plan.md").read_text(encoding="utf-8"),
        "Ordinary Lean Start",
        "Declared v3 profile content.",
    )
    assert not run_file(run_dir, "lean-plan.md").exists()


def test_public_ticket_workflow_runs_use_stable_type_prefixed_identifiers(tmp):
    write_skill(tmp)
    cases = (
        ("user-story-workflow", "TR-001", "US-TR-001"),
        ("bug-ticket-workflow", "4812", "BUG-4812"),
    )
    for workflow_name, supplied_id, expected_id in cases:
        module_dir = write_workflow(tmp, workflow_name, with_run=False)
        write_text(
            template_path(module_dir),
            f"# {workflow_name} Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}",
        )
        write_text(
            template_path(module_dir, "ticket-info.md"),
            f"# {workflow_name} Ticket\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}",
        )

        with patch("builtins.print"):
            exit_code = workflow_repo_manager.main(
                [
                    "start-run",
                    "--root",
                    str(tmp),
                    "--name",
                    workflow_name,
                    "--run-id",
                    supplied_id,
                    "--format",
                    "json",
                ]
            )

        assert exit_code == 0
        run_dir = run_dir_for(module_dir, expected_id)
        assert run_dir.exists()
        assert_field(read_json(run_file(run_dir)), "run_id", expected_id)


def test_public_ticket_workflow_start_requires_identifier(tmp):
    write_skill(tmp)
    write_workflow(tmp, "user-story-workflow", with_run=False)

    try:
        workflow_repo_manager.main(
            [
                "start-run",
                "--root",
                str(tmp),
                "--name",
                "user-story-workflow",
            ]
        )
    except SystemExit as exc:
        assert "requires --run-id <identifier>" in str(exc)
    else:
        raise AssertionError("ticket workflow start should require a stable identifier")


def test_public_named_profile_uses_declared_default_template_for_resolve_and_start(tmp):
    module_dir = write_fixture(tmp, "named-profile-workflow", with_run=False)
    manifest = workflow_manifest("named-profile-workflow")
    manifest["template_layers"] = declared_template_layers(
        profile_roots={"audit": {"template_roots": ["custom/profiles/audit"]}},
    )
    manifest["template_layers"]["default_template"] = "work-plan.md"
    write_json(module_file(module_dir, "module.json"), manifest)
    write_text(
        module_file(module_dir, "custom", "profiles", "audit", "work-plan.md"),
        "# Audit Work Plan\n\nNamed profile content.",
    )

    resolved_exit = workflow_repo_manager.main(
        [
            "template-run",
            "--root",
            str(tmp),
            "resolve",
            "--name",
            "named-profile-workflow",
            "--profile",
            "audit",
            "--format",
            "json",
        ]
    )
    start_exit = workflow_repo_manager.main(
        [
            "start-run",
            "--root",
            str(tmp),
            "--name",
            "named-profile-workflow",
            "--run-id",
            "audit-run",
            "--profile",
            "audit",
            "--format",
            "json",
        ]
    )

    assert resolved_exit == 0
    assert start_exit == 0
    assert_has_all(
        run_file(run_dir_for(module_dir, "audit-run"), "plan.md").read_text(encoding="utf-8"),
        "Audit Work Plan",
        "Named profile content.",
    )


def test_start_rejects_unavailable_and_conflicting_profiles_before_creating_run(tmp):
    unavailable_dir = write_fixture(tmp, "unavailable-profile-workflow", with_run=False)
    unavailable_manifest = workflow_manifest("unavailable-profile-workflow")
    unavailable_manifest["template_layers"] = declared_template_layers()
    write_json(module_file(unavailable_dir, "module.json"), unavailable_manifest)
    write_text(template_path(unavailable_dir), "# Default Plan")

    try:
        workflow_run_support.start_workflow_run(
            tmp,
            "unavailable-profile-workflow",
            run_id="unavailable-run",
            profile="audit",
        )
    except SystemExit as exc:
        unavailable_message = str(exc)
    else:
        raise AssertionError("expected unavailable profile to stop before run creation")

    conflict_dir = write_fixture(tmp, "conflicting-profile-workflow", with_run=False)
    conflict_manifest = workflow_manifest("conflicting-profile-workflow")
    conflict_manifest["template_layers"] = declared_template_layers(
        profile_roots={"audit": {"template_roots": ["custom/profiles/audit"]}},
        override_roots=["custom/override-a", "custom/override-b"],
    )
    write_json(module_file(conflict_dir, "module.json"), conflict_manifest)
    write_text(tmp / "custom" / "override-a" / "plan.md", "# Override A")
    write_text(tmp / "custom" / "override-b" / "plan.md", "# Override B")
    write_text(
        module_file(conflict_dir, "custom", "profiles", "audit", "plan.md"),
        "# Audit Plan",
    )

    try:
        workflow_run_support.start_workflow_run(
            tmp,
            "conflicting-profile-workflow",
            run_id="conflict-run",
            profile="audit",
        )
    except SystemExit as exc:
        conflict_message = str(exc)
    else:
        raise AssertionError("expected provider conflict to stop before run creation")

    assert_has_all(unavailable_message, "profile", "audit", "unavailable")
    assert_has_all(conflict_message, "equal-priority", "override-a", "override-b")
    assert not module_file(unavailable_dir, "runs", "unavailable-run").exists()
    assert not module_file(conflict_dir, "runs", "conflict-run").exists()


def test_template_lint_and_gate_enumerate_every_declared_profile(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    manifest = workflow_manifest("story-flow")
    manifest["template_layers"] = declared_template_layers(
        profile_roots={"audit": {"template_roots": ["custom/profiles/audit"]}},
    )
    manifest["gates"] = [
        {
            "id": "visual-evidence",
            "type": "validation",
            "summary": "Visual evidence is recorded.",
            "evidence": "Visual Evidence",
            "required": True,
        }
    ]
    write_json(module_file(module_dir, "module.json"), manifest)
    write_text(template_path(module_dir), "# Plan\n\n## Visual Evidence\n\n- default")
    write_text(
        module_file(module_dir, "custom", "profiles", "audit", "plan.md"),
        "# Audit Plan\n\n## Visual Evidence\n\n- audit",
    )

    linted = template_layers.lint_templates(tmp, "story-flow")
    gated = template_layers.template_gate_check(tmp, "story-flow")

    assert_ok(linted)
    assert_field_set(linted["templates"], "profile", {"audit", "default"})
    assert_ok(gated)
    assert_field_set(gated["workflows"][0]["profiles"], "profile", {"audit", "default"})


def test_template_layers_fail_equal_priority_conflicts_before_selection(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    manifest = workflow_manifest("story-flow")
    manifest["template_layers"] = declared_template_layers(
        override_roots=["custom/override-a", "custom/override-b"],
    )
    write_json(module_file(module_dir, "module.json"), manifest)
    write_text(tmp / "custom" / "override-a" / "plan.md", "# A")
    write_text(tmp / "custom" / "override-b" / "plan.md", "# B")

    resolved = template_layers.resolve_template(tmp, "story-flow", "plan.md")
    assert_not_ok(resolved)
    assert_status(resolved, "conflict")
    assert resolved["selected"] == {}
    assert_contains(resolved["issues"], "equal-priority template providers")

    linted = template_layers.lint_templates(tmp, "story-flow")
    assert_not_ok(linted)
    assert_contains(linted["issues"], "equal-priority template providers")


def test_template_layer_lint_requires_plan_template_for_plan_workflows(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    manifest = workflow_manifest("story-flow")
    manifest["template_layers"] = manifests.module_contract_v3.conventional_template_layers(
        "story-flow"
    )
    write_json(module_file(module_dir, "module.json"), manifest)
    template_path(module_dir).unlink(missing_ok=True)

    report = template_layers.lint_templates(tmp, "story-flow")

    assert_not_ok(report)
    assert_contains(report["issues"], "required template has no provider")


def test_template_gate_check_covers_default_and_lean_profiles(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    manifest = workflow_manifest("story-flow")
    manifest["template_layers"] = manifests.module_contract_v3.conventional_template_layers(
        "story-flow"
    )
    manifest["gates"] = [
        {
            "id": "visual-evidence",
            "type": "validation",
            "summary": "Visual evidence is recorded.",
            "evidence": "Visual Evidence",
            "required": True,
        }
    ]
    write_json(module_file(module_dir, "module.json"), manifest)
    write_text(template_path(module_dir), "# Plan\n\n## Visual Evidence\n\n- default")
    write_text(template_path(module_dir, "lean-plan.md"), "# Lean Plan\n\n## Other Evidence\n\n- lean")

    failed = template_layers.template_gate_check(tmp, "story-flow")

    assert_not_ok(failed)
    assert_contains(failed["issues"], "Visual Evidence")

    write_text(template_path(module_dir, "lean-plan.md"), "# Lean Plan\n\n## Visual Evidence\n\n- lean")
    passed = template_layers.template_gate_check(tmp, "story-flow")

    assert_ok(passed)
    assert_empty(passed["issues"])


def test_branch_policy_accepts_explicit_branch_override(tmp):
    ok_report = template_layers.branch_policy_check(tmp, branch="feature/spec-driven-runtime")
    failed_report = template_layers.branch_policy_check(tmp, branch="detached-head")

    assert_ok(ok_report)
    assert_not_ok(failed_report)
    assert_has_all(ok_report["branch"], "feature/spec-driven-runtime")
    assert_field(ok_report, "next_command", "none; explicit branch check only")
    assert_field(ok_report, "next_command_scope", "explicit-branch-check")
    assert "commit-readiness" not in ok_report["next_command"]


def test_workflow_validation_packet_checks_playwright_screenshots(tmp):
    module_dir = write_fixture(tmp, "user-story-workflow", with_run=False)
    run_dir = run_dir_for(module_dir)
    validation_dir = run_file(run_dir, "validation", "playwright")
    screenshots = validation_dir / "screenshots"
    write_json(run_file(run_dir), run_packet("user-story-workflow"))
    write_text(run_file(run_dir, "REPORT.md"), "# Report\n\nEvidence.")
    desktop = screenshots / "desktop-1440x900.png"
    mobile = screenshots / "mobile-390x844.png"
    accepted_dir = tmp / "project" / "validation" / "accepted-playwright"
    accepted_desktop = accepted_dir / "desktop-1440x900.png"
    accepted_mobile = accepted_dir / "mobile-390x844.png"
    accepted_manifest = accepted_dir / "accepted-screenshots.json"
    prompt = validation_dir / "llm-analysis-prompt.md"
    write_text(desktop, "png")
    write_text(mobile, "png")
    write_text(accepted_desktop, "png")
    write_text(accepted_mobile, "png")
    write_text(prompt, "# Prompt")
    write_json(
        accepted_manifest,
        {
            "schema_version": 1,
            "tool": "playwright-integration.accepted-screenshots",
            "status": "accepted",
            "screenshots": [
                {"name": "desktop", "width": 1440, "height": 900, "path": str(accepted_desktop)},
                {"name": "mobile", "width": 390, "height": 844, "path": str(accepted_mobile)},
            ],
        },
    )
    write_json(
        validation_dir / "playwright-screenshot-validation.json",
        {
            "schema_version": 1,
            "tool": "playwright-integration.screenshot-validation",
            "ok": True,
            "status": "passed",
            "captures": [
                {"name": "desktop", "width": 1440, "height": 900, "path": str(desktop)},
                {"name": "mobile", "width": 390, "height": 844, "path": str(mobile)},
            ],
            "llm_analysis": {
                "status": "skipped",
                "prompt_path": str(prompt),
            },
            "accepted_screenshots": {
                "status": "accepted",
                "directory": str(accepted_dir),
                "manifest_path": str(accepted_manifest),
                "screenshots": [
                    {"name": "desktop", "width": 1440, "height": 900, "path": str(accepted_desktop)},
                    {"name": "mobile", "width": 390, "height": 844, "path": str(accepted_mobile)},
                ],
            },
        },
    )

    passed = validation_packets.validate_packet(
        tmp,
        "user-story-workflow",
        "run-a",
        kind="playwright-screenshots",
    )

    assert_ok(passed)
    assert_has_all(passed["captured_viewports"], "desktop", "mobile")
    assert_field(passed["accepted_screenshots"], "status", "accepted")
    assert_contains(passed["skipped"], "prompt packet exists")

    mobile.unlink()
    failed = validation_packets.validate_packet(
        tmp,
        "user-story-workflow",
        "run-a",
        kind="playwright-screenshots",
    )

    assert_not_ok(failed)
    assert_contains(failed["issues"], "mobile")


def test_start_run_uses_lean_template_profile(tmp):
    module_dir = write_fixture(tmp, "user-story-workflow", with_run=False)
    write_text(template_path(module_dir, "ticket-info.md"), "# Ticket")
    write_text(template_path(module_dir), "# Default Plan\n\n## Out Of Scope\n\n- default")
    write_text(template_path(module_dir, "lean-plan.md"), "# Lean Plan\n\n## Out Of Scope\n\n- lean")

    report = workflow_run_support.start_workflow_run(
        tmp,
        "user-story-workflow",
        run_id="lean-run",
        profile="lean",
    )
    plan_text = run_file(run_dir_for(module_dir, "lean-run"), "plan.md").read_text(encoding="utf-8")
    packet = read_json(run_file(run_dir_for(module_dir, "lean-run")))

    assert_ok(report)
    assert plan_text.startswith("# Lean Plan")
    assert_field(packet, "template_profile", "lean")


def test_plan_check_uses_external_gate_metadata(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["metadata_path"] = "metadata/workflow-metadata.json"
    write_json(module_file(module_dir, "module.json"), manifest)
    write_json(
        module_file(module_dir, "metadata", "workflow-metadata.json"),
        {
            "gates": [
                {
                    "id": "custom-gate",
                    "type": "quality",
                    "summary": "Fixture.",
                    "evidence": "Custom Evidence",
                    "required": True,
                }
            ]
        },
    )
    write_text(template_path(module_dir), f"# Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}")

    report = workflow_plan_check.check_plan(tmp, "story-flow", template=True)

    assert_not_ok(report)
    assert_has_all(report["issues"], "gate 'custom-gate' declares evidence section 'Custom Evidence' but plan is missing it")


def test_scorecard_fails_when_metadata_template_gate_is_missing(tmp):
    module_dir = write_fixture(tmp, "feedback-improvement-workflow", with_run=False)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["metadata_path"] = "metadata/workflow-metadata.json"
    write_json(module_file(module_dir, "module.json"), manifest)
    write_json(
        module_file(module_dir, "metadata", "workflow-metadata.json"),
        {
            "gates": [
                {
                    "id": "feedback-plan-proof",
                    "type": "validation",
                    "summary": "Fixture.",
                    "evidence": "Feedback Plan Proof",
                    "required": True,
                }
            ],
            "updated": "2026-06-21",
        },
    )
    write_text(template_path(module_dir), f"# Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}")
    write_text(template_path(module_dir, "lean-plan.md"), f"# Lean Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}")

    report = workflow_scorecard.workflow_scorecard(tmp, "feedback-improvement-workflow", run_lifecycle=False)
    plan_gate = next(item for item in report["checks"] if item["name"] == "plan-gate")

    assert_not_ok(report)
    assert_not_ok(plan_gate)
    assert_field(plan_gate["details"], "status", "failed")
    assert_contains(plan_gate["details"]["issues"], "Feedback Plan Proof")


def test_scorecard_passes_metadata_template_gate_profiles(tmp):
    module_dir = write_fixture(tmp, "feedback-improvement-workflow", with_run=False)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["metadata_path"] = "metadata/workflow-metadata.json"
    write_json(module_file(module_dir, "module.json"), manifest)
    write_json(
        module_file(module_dir, "metadata", "workflow-metadata.json"),
        {
            "gates": [
                {
                    "id": "feedback-plan-proof",
                    "type": "validation",
                    "summary": "Fixture.",
                    "evidence": "Feedback Plan Proof",
                    "required": True,
                }
            ],
            "updated": "2026-06-21",
        },
    )
    plan_text = f"# Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}\n\n## Feedback Plan Proof\n\n- fixture"
    write_text(template_path(module_dir), plan_text)
    write_text(template_path(module_dir, "lean-plan.md"), plan_text.replace("# Plan", "# Lean Plan", 1))

    report = workflow_scorecard.workflow_scorecard(tmp, "feedback-improvement-workflow", run_lifecycle=False)
    plan_gate = next(item for item in report["checks"] if item["name"] == "plan-gate")

    assert_ok(plan_gate)
    assert_field(plan_gate["details"], "status", "passed")
    assert_has_all([row["profile"] for row in plan_gate["details"]["profiles"]], "default", "lean")


def test_metadata_inspect_merges_external_metadata(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["metadata_path"] = "metadata/workflow-metadata.json"
    manifest["integrations"] = ["inline-integration"]
    write_json(module_file(module_dir, "module.json"), manifest)
    write_json(
        module_file(module_dir, "metadata", "workflow-metadata.json"),
        {
            "integrations": ["external-integration"],
            "template_layers": {"default_template": "plan.md"},
        },
    )

    report = template_layers.metadata_inspect(tmp, "story-flow")

    assert_ok(report)
    assert_field(report, "metadata_path", "metadata/workflow-metadata.json")
    assert_field(report["metadata"], "integrations", ["external-integration"])
    assert_field(report["merged_manifest"]["template_layers"], "default_template", "plan.md")
    assert_has_all(report["next_command"], "workflow template lint", "--name story-flow", "--format json")


def test_template_commands_keep_strict_read_only_next_commands_json_friendly(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    write_text(template_path(module_dir), f"# Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}")
    write_text(template_path(module_dir, "lean-plan.md"), f"# Lean Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}")

    resolved = template_layers.resolve_template(tmp, "story-flow", "plan.md")
    linted = template_layers.lint_templates(tmp, "story-flow")
    gate = template_layers.template_gate_check(tmp, "story-flow")

    assert_ok(resolved)
    assert_ok(linted)
    assert_ok(gate)
    assert_has_all(resolved["next_command"], "workflow template lint", "--name story-flow", "--format json")
    assert_has_all(linted["next_command"], "workflow template resolve", "--name story-flow", "--format json")
    assert_has_all(gate["next_command"], "workflow template lint", "--name story-flow", "--format json")


def test_scorecard_no_lifecycle_next_command_preserves_no_lifecycle(tmp):
    write_fixture(tmp, with_run=False)

    report = workflow_scorecard.scorecards(tmp, ["story-flow"], run_lifecycle=False)
    compact = workflow_scorecard.compact_scorecards(report)

    assert_has_all(report["next_command"], "workflow scorecard", "--name story-flow", "--no-lifecycle")
    assert_lacks(report["next_command"], "--all")
    assert_has_all(compact["next_command"], "workflow scorecard", "--name story-flow", "--no-lifecycle")


def test_workflow_metadata_requires_updated_date(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["metadata_path"] = "metadata/workflow-metadata.json"
    write_json(module_file(module_dir, "module.json"), manifest)
    write_json(module_file(module_dir, "metadata", "workflow-metadata.json"), {"gates": []})

    errors, _warnings, _modules = validate_automations.validate_automations(tmp, workflow_name="story-flow")

    assert_contains(errors, "metadata/workflow-metadata.json.updated")


def test_integration_descriptor_check_validates_schema(tmp):
    write_json(
        tmp / "integrations" / "demo-integration" / "integration.json",
        {
            "schema_version": 1,
            "integration": {
                "id": "demo-integration",
                "name": "Demo Integration",
                "version": "1.0.0",
                "description": "Fixture.",
                "owner": "engineering",
                "license": "repository",
            },
            "provides": {
                "commands": ["python -B .agents/manage.py check"],
                "managed_files": ["docs/demo.md"],
                "tools": [],
            },
        },
    )
    ok_report = template_layers.integration_check(tmp)
    assert_ok(ok_report)

    write_json(
        tmp / "integrations" / "broken" / "integration.json",
        {
            "schema_version": 1,
            "integration": {
                "id": "different",
                "name": "Broken",
                "version": "1",
                "description": "Broken.",
                "owner": "engineering",
                "license": "repository",
            },
        },
    )
    failed_report = template_layers.integration_check(tmp)
    assert_not_ok(failed_report)
    assert_contains(failed_report["issues"], "must match containing folder")


def test_integration_check_requires_descriptors_for_workflow_references(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["metadata_path"] = "metadata/workflow-metadata.json"
    write_json(module_file(module_dir, "module.json"), manifest)
    write_json(module_file(module_dir, "metadata", "workflow-metadata.json"), {"integrations": ["demo-integration"]})

    missing_report = template_layers.integration_check(tmp)

    assert_not_ok(missing_report)
    assert_contains(missing_report["issues"], "integration has no descriptor: demo-integration")

    write_json(
        tmp / ".agents" / "integrations" / "demo-integration" / "integration.json",
        {
            "schema_version": 1,
            "integration": {
                "id": "demo-integration",
                "name": "Demo Integration",
                "version": "1.0.0",
                "description": "Fixture.",
                "owner": "engineering",
                "license": "repository",
            },
            "provides": {},
        },
    )
    ok_report = template_layers.integration_check(tmp)
    assert_ok(ok_report)
    assert_field(ok_report, "workflow_reference_count", 1)


def test_validation_requires_copyable_workflow_prompts(tmp):
    module_dir = write_fixture(tmp)
    write_text(
        module_file(module_dir, "WORKFLOW.md"),
        """# Story Flow

Read `module.json`.

## Example Prompts

- Start story-flow.
- Resume story-flow.
""",
    )
    errors, _warnings, _modules = validate_automations.validate_automations(tmp, workflow_name="story-flow")
    assert_contains_each(errors, "copyable Start prompt", "copyable Finish prompt")


def test_external_signal_scan_skips_generated_navigation_maps(tmp):
    workflow = tmp / "automations" / "navigation"
    write_text(workflow / "WORKFLOW.md", "# Navigation\n\nNo external access.\n")
    upload_word = "up" + "load"
    map_signal = "clone-or-" + "fetch cred" + "ential " + upload_word
    script_path = workflow / "scripts" / f"do_{upload_word}.py"
    write_text(workflow / "artifacts" / "maps" / "staleness.json", '{"note":"' + map_signal + '"}\n')
    write_text(script_path, f"def {upload_word}():\n    return True\n")

    signals = scanning.detect_external_signals(workflow)
    paths = {item["path"].replace("\\", "/") for item in signals}

    assert "artifacts/maps/staleness.json" not in paths
    assert f"scripts/do_{upload_word}.py" in paths


def test_strict_phase_quality_promotes_step_warnings(tmp):
    module_dir = write_fixture(tmp)
    write_text(module_file(module_dir, "instructions.md"), "# Instructions\n\nDo.")
    assert_strict_promotion(tmp, "resumable")


def test_strict_phase_quality_requires_structured_instruction_sections(tmp):
    module_dir = write_fixture(tmp)
    write_text(
        module_file(module_dir, "instructions.md"),
        """# Instructions

## Phase: execute

- [ ] Read: `WORKFLOW.md`.
  Do: execute.
  Write: update `run.json`.
  Done when: evidence is recorded.
  If blocked: record the blocker.
""",
    )

    assert_strict_promotion(
        tmp,
        MISSING_ALWAYS_LOAD,
        (
            MISSING_ALWAYS_LOAD,
            "missing structured section ## Stop Rules",
            "missing structured section ## Completion Contract",
        ),
    )


def test_strict_phase_quality_requires_declared_phase_instruction_mapping(tmp):
    module_dir = write_fixture(tmp)
    write_text(
        module_file(module_dir, "instructions.md"),
        """# Instructions

## Always Load

- Keep `run.json` current.

## Stop Rules

- Stop when evidence is missing.

## Completion Contract

- Report validation.

## Phase: intake

- [ ] Read: `WORKFLOW.md`.
  Do: gather facts.
  Write: update `run.json`.
  Done when: intake is recorded.
  If blocked: record the blocker.
""",
    )

    assert_strict_promotion(tmp, "phase 'execute' has no matching ## Phase")


def test_context_packet_declaration_consistency_is_validated(tmp):
    module_dir = write_fixture(tmp)
    manifest = workflow_manifest("story-flow")
    manifest["outputs"] = [
        output
        for output in manifest["outputs"]
        if "artifacts/context/context-packet.json" not in str(output)
    ]
    context_evidence = manifest["context_evidence"]
    resume_query = context_evidence["resume_queries"][0]
    resume_query["question"] = "What is the current run state, validation evidence, context packet, and next action?"
    write_json(module_file(module_dir, "module.json"), manifest)

    assert_strict_promotion(tmp, "context_evidence.resume_queries references context packets but outputs do not declare")


def test_context_outputs_require_context_command_and_documentation_delta_outputs(tmp):
    module_dir = write_fixture(tmp)
    manifest = workflow_manifest("story-flow")
    manifest["outputs"] = [
        output
        for output in manifest["outputs"]
        if "artifacts/documentation/documentation-delta" not in str(output)
    ]
    manifest["commands"] = [
        command
        for command in manifest["commands"]
        if not (
            isinstance(command, dict)
            and isinstance(command.get("argv"), list)
            and command["argv"][2:5] == [".agents/manage.py", "workflow", "context"]
        )
    ]
    write_json(module_file(module_dir, "module.json"), manifest)

    errors, warnings, _modules = validate_automations.validate_automations(
        tmp,
        workflow_name="story-flow",
        strict_phase_quality=False,
    )
    assert_empty(errors)
    assert_contains_each(
        warnings,
        "outputs declare context-packet.json but commands do not include workflow context --write",
        "outputs declare context-packet.json but do not declare documentation-delta.json",
        "outputs declare context-packet.json but do not declare documentation-delta.md",
    )


def test_asset_workflow_template_is_evidence_and_decision_based(_tmp):
    template_dir = Path(__file__).resolve().parents[1] / "assets" / "workflow-template"
    start_text = (template_dir / "WORKFLOW.md").read_text(encoding="utf-8")
    instructions_text = (template_dir / "instructions.md").read_text(encoding="utf-8")
    assert_has_all(
        start_text,
        "Decisions",
        "Evidence",
        "workflow hooks --name",
        "out-of-scope",
        "context-use-check",
        "review-autopilot",
        "finish --summary --compact --format json",
        "status --fast",
        "affected owner capsule",
    )
    assert_lacks_all(
        start_text,
        "merge-readiness",
        "ready-packet",
        "can-i-finish",
        "completion-packet",
    )
    assert_has_all(
        instructions_text,
        "## Always Load",
        "## Stop Rules",
        "## Completion Contract",
        "Read:",
        "Do:",
        "Write:",
        "Decision:",
        "Evidence:",
        "Done when:",
        "If blocked:",
    )


def test_story_bug_templates_and_evals_include_reusable_lessons(_tmp):
    repo_root = Path(__file__).resolve().parents[4]
    for workflow_name in ("user-story-workflow", "bug-ticket-workflow"):
        workflow_dir = repo_root / "automations" / workflow_name
        pr_text = (workflow_dir / "templates" / "pr-description.md").read_text(encoding="utf-8")
        execution_log_text = (workflow_dir / "templates" / "execution-log.md").read_text(encoding="utf-8")
        plan_text = (workflow_dir / "templates" / "plan.md").read_text(encoding="utf-8")
        eval_text = (workflow_dir / "suites" / "workflow-evals.json").read_text(encoding="utf-8")

        assert_has_all(pr_text, "## Reusable Lessons", "Plan Item Progress", "Plan Variance", "Independent Review Evidence", "Validation Evidence Map")
        assert_has_all(
            execution_log_text,
            "## Reusable Lessons",
            "## Plan Item Progress",
            "## Plan Variance",
            "## Independent Review Evidence",
            "## Validation Evidence Map",
        )
        assert_has_all(plan_text, "## Fill Order")
        assert_has_all(eval_text, "Reusable Lessons")
        assert_has_all(eval_text.lower(), "execution queue", "proof mapping")
        if workflow_name == "bug-ticket-workflow":
            assert_has_all(plan_text, "## Root Cause Evidence")
            assert_has_all(eval_text.lower(), "regression proof")


def test_run_packet_index_and_eval(tmp):
    write_fixture(tmp)
    status = index_workflow_runs.run(
        index_workflow_runs.Args(
            root=tmp,
            workflow_name="story-flow",
            write=True,
            check=False,
            output_format="json",
        )
    )
    assert status == 0
    index_path = workflow_runs_dir(tmp) / "index.json"
    assert index_path.exists()
    run_index = read_json(index_path)
    indexed_run = run_index["runs"][0]
    assert_lacks_all(indexed_run, "files")
    assert_fields(indexed_run, file_count=2, key_files=["REPORT.md", "run.json"])
    suite = workflow_suite_path(tmp)
    write_json(
        suite,
        {
            "evals": [
                {
                    "id": "run-packet",
                    "assertions": [
                        {"type": "validation_ok"},
                        {"type": "run_index_exists"},
                        {"type": "run_index_contains", "run_id": "run-a"},
                        {"type": "run_evidence_ledger_valid", "run_id": "run-a"},
                        {"type": "run_resume_state_valid", "run_id": "run-a"},
                        {"type": "run_handoff_valid", "run_id": "run-a"},
                        {"type": "unsupported_claims_recorded", "run_id": "run-a"},
                    ],
                }
            ]
        },
    )
    report = eval_workflow.run_eval(
        eval_workflow.Args(root=tmp, workflow_name="story-flow", suite=suite, output_format="json")
    )
    assert_single_eval_passed(report)


def test_eval_all_workflows_discovers_eval_suites(tmp):
    write_skill(tmp)
    for workflow_name in ("bug-flow", "story-flow"):
        write_workflow(tmp, workflow_name)
        suite = workflow_suite_path(tmp, workflow_name)
        write_json(
            suite,
            {
                "evals": [
                    {
                        "id": "valid",
                        "assertions": [{"type": "validation_ok"}],
                    }
                ]
            },
        )
        write_json(suite.parent / "embedding-retrieval-fixtures.json", {"fixtures": [{"id": "retrieval-fixture"}]})
        write_json(
            suite.parent / "retrieval-vision-candidates-2026-06-11.json",
            {"candidates": [{"id": "vision-candidate"}]},
        )

    report = workflow_repo_manager.eval_all_workflows(tmp)

    assert_ok(report)
    assert_field(report, "summary", {"workflows": 2, "suites": 2, "passed": 2, "failed": 0, "cases": 2})
    assert [item["workflow"] for item in report["results"]] == ["bug-flow", "story-flow"]
    assert_fields(
        report["execution"],
        strategy="parallel-by-workflow",
        workers=2,
        max_workers=4,
        workflow_groups=2,
        parallel_safe=True,
    )

    compact = workflow_repo_manager.eval_all_workflows(tmp, summary=True)
    assert_field(compact, "summary", report["summary"])
    assert_empty(compact["results"])
    assert_has_all(compact["next_command"], "--summary")


def test_eval_all_workflows_parallelizes_workflows_but_serializes_each_workflow(tmp):
    write_skill(tmp)
    workflow_names = [f"flow-{index}" for index in range(6)]
    for workflow_name in workflow_names:
        write_workflow(tmp, workflow_name)
        write_json(
            workflow_suite_path(tmp, workflow_name),
            {"evals": [{"id": "primary", "assertions": [{"type": "validation_ok"}]}]},
        )
    write_json(
        workflow_suite_path(tmp, workflow_names[0]).parent / "supplemental-evals.json",
        {"evals": [{"id": "supplemental", "assertions": [{"type": "validation_ok"}]}]},
    )

    lock = threading.Lock()
    active = 0
    maximum_active = 0
    active_by_workflow: dict[str, int] = {}
    same_workflow_overlap = False

    def timed_eval(args):
        nonlocal active, maximum_active, same_workflow_overlap
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
            active_by_workflow[args.workflow_name] = active_by_workflow.get(args.workflow_name, 0) + 1
            same_workflow_overlap = same_workflow_overlap or active_by_workflow[args.workflow_name] > 1
        time.sleep(0.05)
        with lock:
            active -= 1
            active_by_workflow[args.workflow_name] -= 1
        return {
            "summary": {"passed": 1, "failed": 0, "total": 1},
            "results": [{"id": args.suite.stem, "ok": True, "assertions": []}],
        }

    with patch.object(eval_workflow, "run_eval", side_effect=timed_eval):
        report = workflow_repo_manager.eval_all_workflows(tmp)

    assert_ok(report)
    assert_field(report["summary"], "workflows", 6)
    assert_field(report["summary"], "suites", 7)
    assert_field(report["summary"], "cases", 7)
    assert maximum_active > 1, maximum_active
    assert maximum_active <= workflow_repo_manager.MAX_WORKFLOW_EVAL_WORKERS, maximum_active
    assert not same_workflow_overlap
    assert [row["workflow"] for row in report["results"]] == [
        "flow-0",
        "flow-0",
        "flow-1",
        "flow-2",
        "flow-3",
        "flow-4",
        "flow-5",
    ]


def test_eval_all_workflows_falls_back_to_serial_for_unknown_repo_command(tmp):
    write_skill(tmp)
    for workflow_name in ("bug-flow", "story-flow"):
        write_workflow(tmp, workflow_name)
        assertion = (
            {"type": "repo_command_succeeds", "command": ["unknown-write-command"]}
            if workflow_name == "bug-flow"
            else {"type": "validation_ok"}
        )
        write_json(
            workflow_suite_path(tmp, workflow_name),
            {"evals": [{"id": "case", "assertions": [assertion]}]},
        )

    with patch.object(
        eval_workflow,
        "run_eval",
        return_value={
            "summary": {"passed": 1, "failed": 0, "total": 1},
            "results": [{"id": "case", "ok": True, "assertions": []}],
        },
    ):
        report = workflow_repo_manager.eval_all_workflows(tmp)

    assert_ok(report)
    assert_fields(report["execution"], strategy="serial", workers=1, parallel_safe=False)
    assert any("repository command is not parallel-safe" in reason for reason in report["execution"]["fallback_reasons"])


def test_eval_all_workflows_falls_back_to_serial_for_workflow_local_hooks(tmp):
    write_skill(tmp)
    for workflow_name in ("bug-flow", "story-flow"):
        module_dir = write_workflow(tmp, workflow_name)
        manifest = read_json(module_file(module_dir, "module.json"))
        if workflow_name == "bug-flow":
            manifest["hooks"] = [{"id": "local-hook"}]
            write_json(module_file(module_dir, "module.json"), manifest)
        write_json(
            workflow_suite_path(tmp, workflow_name),
            {"evals": [{"id": "case", "assertions": [{"type": "validation_ok"}]}]},
        )

    with patch.object(
        eval_workflow,
        "run_eval",
        return_value={
            "summary": {"passed": 1, "failed": 0, "total": 1},
            "results": [{"id": "case", "ok": True, "assertions": []}],
        },
    ):
        report = workflow_repo_manager.eval_all_workflows(tmp)

    assert_ok(report)
    assert_fields(report["execution"], strategy="serial", workers=1, parallel_safe=False)
    assert any("workflow-local hooks" in reason for reason in report["execution"]["fallback_reasons"])


def test_eval_all_workflows_falls_back_to_serial_for_noncanonical_global_hooks(tmp):
    write_skill(tmp)
    for workflow_name in ("bug-flow", "story-flow"):
        write_workflow(tmp, workflow_name)
        write_json(
            workflow_suite_path(tmp, workflow_name),
            {"evals": [{"id": "case", "assertions": [{"type": "validation_ok"}]}]},
        )
    write_json(
        workflow_hooks_path(tmp),
        {
            "schema_version": 1,
            "hooks": [
                {
                    "id": "global-workflow-pre",
                    "event": "workflow-pre",
                    "command": (
                        workflow_repo_manager.CANONICAL_GLOBAL_HOOK_COMMAND
                        + " --run-id shared --run-dir automations/shared/runs/shared"
                    ),
                    "required": True,
                    "timeout_seconds": 30,
                    "evidence_path": workflow_repo_manager.CANONICAL_GLOBAL_HOOK_EVIDENCE,
                }
            ],
        },
    )

    with patch.object(
        eval_workflow,
        "run_eval",
        return_value={
            "summary": {"passed": 1, "failed": 0, "total": 1},
            "results": [{"id": "case", "ok": True, "assertions": []}],
        },
    ):
        report = workflow_repo_manager.eval_all_workflows(tmp)

    assert_ok(report)
    assert_fields(report["execution"], strategy="serial", workers=1, parallel_safe=False)
    assert any("global workflow hook" in reason for reason in report["execution"]["fallback_reasons"])


def test_eval_all_workflows_continues_after_suite_system_exit(tmp):
    write_skill(tmp)
    for workflow_name in ("bug-flow", "story-flow"):
        write_workflow(tmp, workflow_name)
        write_json(
            workflow_suite_path(tmp, workflow_name),
            {"evals": [{"id": "primary", "assertions": [{"type": "validation_ok"}]}]},
        )
    write_json(
        workflow_suite_path(tmp, "bug-flow").parent / "supplemental-evals.json",
        {"evals": [{"id": "supplemental", "assertions": [{"type": "validation_ok"}]}]},
    )

    def controlled_eval(args):
        if args.workflow_name == "bug-flow" and args.suite.name == "supplemental-evals.json":
            raise SystemExit("controlled malformed suite")
        return {
            "summary": {"passed": 1, "failed": 0, "total": 1},
            "results": [{"id": args.suite.stem, "ok": True, "assertions": []}],
        }

    with patch.object(eval_workflow, "run_eval", side_effect=controlled_eval):
        report = workflow_repo_manager.eval_all_workflows(tmp)

    assert_not_ok(report)
    assert_fields(report["summary"], suites=3, passed=2, failed=1, cases=3)
    assert [(row["workflow"], Path(row["suite"]).name) for row in report["results"]] == [
        ("bug-flow", "supplemental-evals.json"),
        ("bug-flow", "workflow-evals.json"),
        ("story-flow", "workflow-evals.json"),
    ]
    assert report["results"][0]["ok"] is False
    assert report["results"][1]["ok"] is True
    assert report["results"][2]["ok"] is True


def test_eval_repo_commands_require_exact_read_only_allowlist(tmp):
    workflow_name = "story-flow"
    assert eval_workflow.normalize_command(["status", "--fast"], workflow_name) == ["status", "--fast"]
    assert eval_workflow.normalize_command(
        ["workflow", "plan-check", "--name", "<workflow>", "--template", "--format", "json"],
        workflow_name,
    ) == ["workflow", "plan-check", "--name", workflow_name, "--template", "--format", "json"]
    rejected = [
        ["benchmark", "tool-call", "--check", "--llama-endpoint", "http://127.0.0.1:9"],
        ["workflow", "plan-check", "--name", workflow_name, "--template", "--format", "json", "--write"],
        ["unknown-write-command"],
        [],
    ]
    for command in rejected:
        try:
            eval_workflow.normalize_command(command, workflow_name)
        except SystemExit as exc:
            assert_has_all(str(exc), "exact allowlisted read-only")
        else:
            raise AssertionError(f"expected repository command to be rejected: {command}")


def test_eval_all_workflows_fails_when_accepted_workflow_has_no_suite(tmp):
    write_skill(tmp)
    write_workflow(tmp, "bug-flow")
    write_workflow(tmp, "story-flow")
    write_json(
        workflow_suite_path(tmp, "story-flow"),
        {"evals": [{"id": "valid", "assertions": [{"type": "validation_ok"}]}]},
    )

    report = workflow_repo_manager.eval_all_workflows(tmp)

    assert_not_ok(report)
    assert_field(report["summary"], "workflows", 2)
    assert_field(report["summary"], "suites", 1)
    assert_field(report["summary"], "failed", 1)
    failed = [row for row in report["results"] if row.get("ok") is False]
    assert_field(failed[0], "workflow", "bug-flow")
    assert_has_all(failed[0]["error"], "no discovered eval suite")


def test_eval_workflow_rejects_empty_suite_and_empty_case(tmp):
    module_dir = write_fixture(tmp)
    suite = workflow_suite_path(tmp)

    write_json(suite, {"evals": []})
    try:
        eval_workflow.run_eval(
            eval_workflow.Args(root=tmp, workflow_name=module_dir.name, suite=suite, output_format="json")
        )
    except SystemExit as exc:
        assert_has_all(str(exc), "at least one eval case")
    else:
        raise AssertionError("expected an empty workflow eval suite to fail")

    write_json(suite, {"evals": [{"id": "empty", "assertions": []}]})
    try:
        eval_workflow.run_eval(
            eval_workflow.Args(root=tmp, workflow_name=module_dir.name, suite=suite, output_format="json")
        )
    except SystemExit as exc:
        assert_has_all(str(exc), "at least one assertion")
    else:
        raise AssertionError("expected an eval case without assertions to fail")


def test_plan_check_template_and_filled_story_plan(tmp):
    module_dir = write_fixture(tmp, "user-story-workflow")
    run_dir = run_dir_for(module_dir)
    write_text(template_path(module_dir), filled_story_plan())
    write_text(run_file(run_dir, "plan.md"), filled_story_plan())
    write_start_context_evidence(tmp, "user-story-workflow", run_dir)

    template_report = workflow_plan_check.check_plan(tmp, "user-story-workflow", template=True)
    run_report = workflow_plan_check.check_plan(tmp, "user-story-workflow", run_id="run-a")

    assert_ok(template_report)
    assert_field(template_report, "mode", "template")
    assert_ok(run_report)
    assert_field(run_report, "plan_path", "automations/user-story-workflow/runs/run-a/plan.md")


def test_story_bug_plan_only_smoke_writes_valid_pending_plan(tmp):
    story_dir = write_fixture(tmp, "user-story-workflow", with_run=False)
    bug_dir = write_workflow(tmp, "bug-ticket-workflow", with_run=False)
    for module_dir in (story_dir, bug_dir):
        write_text(template_path(module_dir), f"# Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}")
        write_text(template_path(module_dir, "lean-plan.md"), f"# Lean Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}")

    story = plan_smoke.story_bug_plan_only_smoke(tmp, "user-story-workflow")
    bug = plan_smoke.story_bug_plan_only_smoke(tmp, "bug-ticket-workflow")

    assert_ok(story)
    assert_ok(bug)
    assert_empty(smoke_run_dirs(tmp, "user-story-workflow"))
    assert_empty(smoke_run_dirs(tmp, "bug-ticket-workflow"))


def test_start_scaffolds_story_bug_plan_and_ticket_info(tmp):
    write_skill(tmp)
    for workflow_name in ("user-story-workflow", "bug-ticket-workflow"):
        module_dir = write_workflow(tmp, workflow_name, with_run=False)
        write_text(template_path(module_dir), f"# {workflow_name} Plan\n\n## Out Of Scope\n\n- template")
        write_text(template_path(module_dir, "ticket-info.md"), f"# {workflow_name} Ticket\n\n## Out Of Scope\n\n- template")

        report = workflow_run_support.start_workflow_run(tmp, workflow_name, run_id=f"{workflow_name}-run")
        run_dir = run_dir_for(module_dir, f"{workflow_name}-run")
        packet = read_json(run_file(run_dir))

        plan_path = f"automations/{workflow_name}/runs/{workflow_name}-run/plan.md"
        ticket_path = f"automations/{workflow_name}/runs/{workflow_name}-run/ticket-info.md"
        assert run_file(run_dir, "plan.md").exists()
        assert run_file(run_dir, "ticket-info.md").exists()
        assert_has_all(report["created_files"], plan_path, ticket_path)
        assert_has_all(packet["evidence_paths"], plan_path, ticket_path)
        assert_contains(
            packet["handoff"]["required_next_context"],
            f"automations/{workflow_name}/runs/{workflow_name}-run/artifacts/context/context-packet.json",
        )
        assert_lacks_all(
            packet["handoff"]["required_next_context"],
            plan_path,
            ticket_path,
        )
        assert_lacks_all("\n".join(report["created_files"]), "pr-description.md")


def test_start_auto_writes_declared_context_packet(tmp):
    write_skill(tmp)
    module_dir = write_workflow(tmp, "user-story-workflow", with_run=False)
    write_text(tmp / "automations" / "navigation" / "artifacts" / "maps" / "HANDOFF.md", "# Handoff")
    write_text(template_path(module_dir), "# Story Plan\n\n## Out Of Scope\n\n- template")
    write_text(template_path(module_dir, "ticket-info.md"), "# Story Ticket\n\n## Out Of Scope\n\n- template")

    report = workflow_run_support.start_workflow_run(tmp, "user-story-workflow", run_id="start-context")
    run_dir = run_dir_for(module_dir, "start-context")
    context_json = run_file(run_dir, "artifacts", "context", "context-packet.json")
    context_markdown = run_file(run_dir, "artifacts", "context", "context-packet.md")
    run_packet = read_json(run_file(run_dir))

    assert context_json.exists()
    assert context_markdown.exists()
    assert_true(report, "context_packet_refreshed")
    assert report["context_packet_path"].endswith("artifacts/context/context-packet.json")
    assert_field(report["workflow_preflight"], "tool", "workflow-manager.start-preflight")
    assert_field(report["workflow_preflight"], "owner", "workflow:user-story-workflow")
    assert_field(report["workflow_preflight"], "confidence", "explicit-workflow")
    assert_has_all(
        report["workflow_preflight"]["read_first"],
        "automations/user-story-workflow/runs/start-context/artifacts/context/context-packet.json",
        "automations/navigation/artifacts/maps/HANDOFF.md",
        "automations/user-story-workflow/WORKFLOW.md",
        "automations/user-story-workflow/module.json",
    )
    assert_has_all(
        report["workflow_preflight"]["tool_only_inputs"],
        "automations/navigation/artifacts/maps/handoff.json",
        "raw navigation JSON",
    )
    assert_has_all(
        report["workflow_preflight"]["evidence_targets"],
        "automations/user-story-workflow/runs/start-context/run.json",
        "automations/user-story-workflow/runs/start-context/REPORT.md",
        "automations/user-story-workflow/runs/start-context/artifacts/checkpoint/checkpoint.json",
    )
    assert_has_all(report["workflow_preflight"]["next_command"], "workflow resume", "--run-id start-context")
    assert_field(run_packet["workflow_preflight"], "tool", "workflow-manager.start-preflight")
    rendered = workflow_run_support.render_start_run(report)
    assert_has_all(rendered, "## Preflight Read First", "## Tool-Only Inputs", "raw navigation JSON")
    assert_has_all(report["created_files"], "automations/user-story-workflow/runs/start-context/artifacts/context/context-packet.json")
    assert_has_all(run_packet["handoff"]["required_next_context"], "automations/user-story-workflow/runs/start-context/artifacts/context/context-packet.json")


def test_start_compact_packet_references_verbose_files_without_dumping_them(tmp):
    write_skill(tmp)
    module_dir = write_workflow(tmp, "user-story-workflow", with_run=False)
    write_text(tmp / "automations" / "navigation" / "artifacts" / "maps" / "HANDOFF.md", "# Handoff")
    write_text(template_path(module_dir), "# Story Plan\n\n## Out Of Scope\n\n- template")
    write_text(template_path(module_dir, "ticket-info.md"), "# Story Ticket\n\n## Out Of Scope\n\n- template")

    report = workflow_run_support.start_workflow_run(tmp, "user-story-workflow", run_id="start-compact")
    compact = workflow_repo_manager.compact_start_run_report(report)

    assert_fields(
        compact,
        workflow="user-story-workflow",
        run_id="start-compact",
        current_phase="orientation",
        phase_status="not-started",
    )
    assert_field(compact, "tool", "workflow-manager.start-run")
    assert_has_all(
        compact["read_first"],
        "automations/user-story-workflow/runs/start-compact/artifacts/context/context-packet.json",
        "automations/navigation/artifacts/maps/HANDOFF.md",
        "automations/user-story-workflow/WORKFLOW.md",
        "automations/user-story-workflow/module.json",
    )
    assert_has_all(
        compact["evidence_paths"],
        "automations/user-story-workflow/runs/start-compact/run.json",
        "automations/user-story-workflow/runs/start-compact/REPORT.md",
        "automations/user-story-workflow/runs/start-compact/artifacts/context/context-packet.json",
        "automations/user-story-workflow/runs/start-compact/artifacts/checkpoint/checkpoint.json",
    )
    assert_has_all(compact["raw_detail_paths"], "context_evidence", "checkpoint", "context_packet")
    assert_has_all(compact["stop_conditions"], "plan-check fails before implementation", "raw navigation JSON would be needed for model context")
    assert_has_all(compact["tool_only_inputs"], "raw navigation JSON")
    assert_has_all(compact["next_command"], "workflow resume", "--summary", "--compact", "--format json")
    assert_field(compact["output_budget"], "status", "within-budget")
    assert_has_all(compact["output_budget"], "command", "estimated_output_tokens", "budget_tokens", "tokens_over_budget", "summary")
    assert_lacks_all(compact["output_budget"], "scope", "counter")
    assert_field(compact["output_budget"], "estimated_output_tokens", estimated_compact_json_tokens(compact))
    assert compact["output_budget"]["estimated_output_tokens"] <= 700
    assert estimated_compact_json_tokens(compact) <= 2000
    assert_lacks_all(compact, "context_packet", "checkpoint", "context_evidence", "start_checklist")


def test_start_from_request_records_request_in_run_and_context_packet(tmp):
    write_skill(tmp)
    module_dir = write_workflow(tmp, "user-story-workflow", with_run=False)
    write_text(template_path(module_dir), "# Story Plan\n\n## Out Of Scope\n\n- template")
    write_text(template_path(module_dir, "ticket-info.md"), "# Story Ticket\n\n## Out Of Scope\n\n- template")

    report = workflow_run_support.start_workflow_run(
        tmp,
        "user-story-workflow",
        run_id="start-from-request",
        from_request="implement Azure DevOps user story 123",
    )
    run_dir = run_dir_for(module_dir, "start-from-request")
    packet = read_json(run_file(run_dir))
    context = read_json(run_file(run_dir, "artifacts", "context", "context-packet.json"))

    assert_field(report, "from_request", "implement Azure DevOps user story 123")
    assert_field(report["workflow_preflight"], "confidence", "routed-from-request")
    assert_field(packet["request"], "text", "implement Azure DevOps user story 123")
    assert_field(packet["workflow_preflight"], "confidence", "routed-from-request")
    assert_contains(context["decisions"], "initial-user-request")


def test_start_from_ticket_copies_imported_ticket_info(tmp):
    module_dir = write_fixture(tmp, "user-story-workflow", with_run=False)
    write_text(template_path(module_dir), "# Plan Template\n\n## Out Of Scope\n\n- template")
    write_text(template_path(module_dir, "ticket-info.md"), "# Template Ticket\n\n## Out Of Scope\n\n- template")
    imported = tmp / "imports" / "story-123"
    write_text(imported / "ticket-info.md", "# Imported Ticket\n\n## Out Of Scope\n\n- imported")

    report = workflow_run_support.start_workflow_run(
        tmp,
        "user-story-workflow",
        run_id="run-from-ticket",
        from_ticket="imports/story-123",
    )

    ticket_path = run_file(run_dir_for(module_dir, "run-from-ticket"), "ticket-info.md")
    assert ticket_path.read_text(encoding="utf-8").startswith("# Imported Ticket")
    assert_has_all(report["created_files"], "automations/user-story-workflow/runs/run-from-ticket/ticket-info.md")
    assert_has_all(report["ticket_intake"]["evidence_files"], "imports/story-123/ticket-info.md")


def test_dotnet_plan_ready_smoke_writes_valid_pending_plan(tmp):
    upgrade_dir = write_fixture(tmp, "dotnet-upgrade", with_run=False)
    migration_dir = write_workflow(tmp, "dotnet-framework-migration", with_run=False)
    for module_dir in (upgrade_dir, migration_dir):
        write_text(template_path(module_dir), f"# Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}")
        write_text(template_path(module_dir, "lean-plan.md"), f"# Lean Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}")

    upgrade = plan_smoke.dotnet_plan_ready_smoke(tmp, "dotnet-upgrade")
    migration = plan_smoke.dotnet_plan_ready_smoke(tmp, "dotnet-framework-migration")

    assert_ok(upgrade)
    assert_ok(migration)
    assert_empty(smoke_run_dirs(tmp, "dotnet-upgrade"))
    assert_empty(smoke_run_dirs(tmp, "dotnet-framework-migration"))


def test_plan_check_detects_unfilled_story_plan_rows(tmp):
    module_dir = write_fixture(tmp, "user-story-workflow")
    blank_plan = filled_story_plan().replace(
        STORY_SECURITY_PROOF_ROW,
        "| Roles, authorization, or tenant boundaries | | |",
    )
    write_text(run_plan_path(module_dir), blank_plan)

    report = workflow_plan_check.check_plan(tmp, "user-story-workflow", run_id="run-a")

    assert_not_ok(report)
    assert_has_all(report["issues"], "Security Impact: incomplete row(s) 1")


def test_plan_check_reports_story_acceptance_quality_issues(tmp):
    module_dir = write_fixture(tmp, "user-story-workflow")
    bad_plan = filled_story_plan().replace(
        STORY_AC_PROOF_ROW,
        STORY_AC_MISSING_DOC_ROW,
    )
    write_text(run_plan_path(module_dir), bad_plan)

    report = workflow_plan_check.check_plan(tmp, "user-story-workflow", run_id="run-a")

    quality_issues = report.get("quality_issues", [])
    assert_not_ok(report)
    assert report.get("quality_summary", {}).get("failed", 0) >= 1, report
    assert_contains_all(quality_issues, AC_MAPPING, "Documentation")
    assert_contains(report["issues"], f"{AC_MAPPING} row 1 Documentation")


def test_plan_check_reports_bug_reproduction_regression_quality_issues(tmp):
    module_dir = write_fixture(tmp, "bug-ticket-workflow")
    bad_plan = plan_smoke.filled_bug_plan().replace(
        "- Test before fix: planned failing regression check before implementation.\n"
        "- Test after fix: planned passing regression check after implementation.\n"
        "- Manual proof accepted: not required: automated fixture proof is planned.\n"
        "- Reason: regression proof is required before the fix changes code.",
        "- Test before fix:\n- Test after fix:\n- Manual proof accepted:\n- Reason:",
    ).replace(
        "| Affected versions | unknown: fixture has no product version | smoke fixture |",
        "| Affected versions | | |",
    )
    write_text(run_plan_path(module_dir), bad_plan)

    report = workflow_plan_check.check_plan(tmp, "bug-ticket-workflow", run_id="run-a")

    quality_issues = report.get("quality_issues", [])
    fix_queue = report.get("fix_queue", [])
    assert_not_ok(report)
    assert_contains(quality_issues, "Regression-Proof Decision")
    assert_contains_all(quality_issues, "Triage", "Affected versions")
    assert_contains(report["issues"], "Regression-Proof Decision")
    assert fix_queue, report
    assert_field(report, "operator_next_action", fix_queue[0]["action"])


def test_plan_check_allows_explicit_no_impact_reasons(tmp):
    write_plan_run(tmp, "user-story-workflow", filled_story_plan(), ticket=False)

    report = workflow_plan_check.check_plan(tmp, "user-story-workflow", run_id="run-a")

    assert_ok(report)
    assert_empty(report.get("quality_issues"))


def test_plan_check_reports_operator_fix_queue_and_next_action(tmp):
    bad_plan = filled_story_plan().replace(
        STORY_AC_PROOF_ROW,
        STORY_AC_MISSING_DOC_ROW,
    )
    write_plan_run(tmp, "user-story-workflow", bad_plan, ticket=False)

    report = workflow_plan_check.check_plan(tmp, "user-story-workflow", run_id="run-a")

    fix_queue = report.get("fix_queue", [])
    assert_not_ok(report)
    assert fix_queue, report
    assert_fields(
        fix_queue[0],
        section=AC_MAPPING,
        row=1,
        field="Documentation",
        target_path="automations/user-story-workflow/runs/run-a/plan.md",
    )
    assert_field(report, "operator_next_action", fix_queue[0]["action"])
    assert_false(report, "ready_for_approval")
    assert_false(report, "implementation_allowed")


def test_plan_check_reports_approval_and_implementation_state(tmp):
    run_dir = write_plan_run(tmp, "user-story-workflow", filled_story_plan(), ticket=False)

    pending_report = workflow_plan_check.check_plan(tmp, "user-story-workflow", run_id="run-a")

    assert_ok(pending_report)
    assert_true(pending_report, "ready_for_approval")
    assert_false(pending_report, "implementation_allowed")
    assert_has_all(pending_report["operator_next_action"].lower(), "approval")

    approved_plan = filled_story_plan().replace(APPROVAL_PLANNED, APPROVAL_APPROVED)
    write_text(run_file(run_dir, "plan.md"), approved_plan)

    approved_report = workflow_plan_check.check_plan(tmp, "user-story-workflow", run_id="run-a")

    assert_ok(approved_report)
    assert_false(approved_report, "ready_for_approval")
    assert_true(approved_report, "implementation_allowed")
    assert_has_all(approved_report["operator_next_action"].lower(), "implementation")


def test_resume_surfaces_story_bug_plan_gate_fix_queue(tmp):
    bad_plan = filled_story_plan().replace(
        STORY_AC_PROOF_ROW,
        STORY_AC_MISSING_DOC_ROW,
    )
    write_plan_run(tmp, "user-story-workflow", bad_plan)

    report = workflow_run_support.resume_workflow_run(tmp, "user-story-workflow", run_id="run-a")

    plan_gate = report.get("plan_gate", {})
    assert_status(plan_gate, "failed")
    assert_field(plan_gate["fix_queue"][0], "field", "Documentation")
    assert_fields(
        report,
        operator_next_action=plan_gate["operator_next_action"],
        next_action=plan_gate["operator_next_action"],
    )


def test_resume_compact_packet_references_context_and_checkpoint_without_dumping_evidence(tmp):
    write_guidance_savings_fixture(tmp)
    write_skill(tmp)
    module_dir = write_workflow(tmp, "user-story-workflow", with_run=False)
    write_text(tmp / "automations" / "navigation" / "artifacts" / "maps" / "HANDOFF.md", "# Handoff")
    write_text(template_path(module_dir), "# Story Plan\n\n## Out Of Scope\n\n- template")
    write_text(template_path(module_dir, "ticket-info.md"), "# Story Ticket\n\n## Out Of Scope\n\n- template")
    with module_file(module_dir, "WORKFLOW.md").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "\n## Resume Context Baseline\n\n"
            + "Handle-only workflow evidence keeps resume accounting measurable.\n" * 500
        )
    workflow_run_support.start_workflow_run(tmp, "user-story-workflow", run_id="resume-compact")

    report = workflow_run_support.resume_workflow_run(tmp, "user-story-workflow", run_id="resume-compact")
    compact = workflow_repo_manager.compact_resume_run_report(report)

    assert_fields(
        compact,
        workflow="user-story-workflow",
        run_id="resume-compact",
        current_phase="orientation",
        phase_status="not-started",
        external_validation_status="not-recorded",
        context_auto_refreshed=True,
        checkpoint_auto_refreshed=True,
    )
    assert_field(compact, "tool", "workflow-manager.resume-run")
    assert_has_all(
        compact["context_budget"],
        "status",
        "compact_packet_tokens_estimated",
        "effective_load_tokens_estimated",
        "raw_reference_inventory_tokens_estimated",
        "raw_reference_inventory_is_loaded",
        "effective_load_reduction_percent",
        "estimated_tokens_saved",
    )
    assert_has_all(
        compact["read_first"],
        "automations/user-story-workflow/runs/resume-compact/artifacts/context/context-packet.json",
        "automations/navigation/artifacts/maps/HANDOFF.md",
    )
    assert_has_all(
        compact["evidence_paths"],
        "automations/user-story-workflow/runs/resume-compact/run.json",
        "automations/user-story-workflow/runs/resume-compact/REPORT.md",
        "automations/user-story-workflow/runs/resume-compact/artifacts/context/context-packet.json",
        "automations/user-story-workflow/runs/resume-compact/artifacts/checkpoint/checkpoint.json",
    )
    assert_has_all(compact["raw_detail_paths"], "context_evidence", "context_packet", "checkpoint")
    assert_has_all(compact["stop_conditions"], "plan-check fails before implementation", "raw navigation JSON would be needed for model context")
    assert_has_all(compact["next_command"], "workflow plan-check", "--run-id resume-compact")
    assert_field(compact["output_budget"], "status", "within-budget")
    assert_has_all(compact["output_budget"], "command", "estimated_output_tokens", "budget_tokens", "tokens_over_budget", "summary")
    assert_lacks_all(compact["output_budget"], "scope", "counter")
    assert_field(compact["output_budget"], "estimated_output_tokens", estimated_compact_json_tokens(compact))
    assert compact["output_budget"]["estimated_output_tokens"] <= 620
    assert estimated_compact_json_tokens(compact) <= 2000
    assert_lacks_all(compact, "context_evidence", "plan_gate", "execution_queue", "proof_gap_summary")


def write_start_context_evidence(tmp, workflow_name, run_dir):
    packet = read_json(run_file(run_dir))
    workflow_context_evidence.write_context_evidence_packet(
        tmp,
        workflow_name,
        run_dir,
        packet,
        event="start",
        write=True,
        write_run=True,
    )


def write_plan_run(tmp, workflow_name, plan, *, ticket=True):
    run_dir = write_fixture(tmp, workflow_name) / "runs" / "run-a"
    write_text(run_file(run_dir, "plan.md"), plan)
    if ticket:
        write_text(run_file(run_dir, "ticket-info.md"), TICKET_INFO_FIXTURE)
    write_start_context_evidence(tmp, workflow_name, run_dir)
    return run_dir


def approved_story_plan():
    return filled_story_plan().replace(APPROVAL_PLANNED, APPROVAL_APPROVED)


def approved_bug_plan():
    return plan_smoke.filled_bug_plan().replace(APPROVAL_PENDING, APPROVAL_APPROVED)


def filled_common_plan(*, approval="planned"):
    return plan_smoke.filled_user_story_plan().replace(APPROVAL_PENDING, f"Approval status: {approval}")


def test_start_scaffolds_generic_execution_log_and_plan_when_template_exists(tmp):
    module_dir = write_fixture(tmp, "disciplined-change-workflow", with_run=False)
    write_text(template_path(module_dir), filled_common_plan())

    report = workflow_run_support.start_workflow_run(tmp, "disciplined-change-workflow", run_id="run-a")

    run_dir = run_dir_for(module_dir)
    assert run_file(run_dir, "execution-log.md").exists(), report
    assert run_file(run_dir, "plan.md").exists(), report
    assert_has_all(
        report["created_files"],
        "automations/disciplined-change-workflow/runs/run-a/execution-log.md",
        "automations/disciplined-change-workflow/runs/run-a/plan.md",
    )
    assert_has_all(report["start_scaffold"]["created"], "automations/disciplined-change-workflow/runs/run-a/execution-log.md")
    assert_has_all(report["operator_next_action"], "plan-check")


def test_plan_check_reports_generic_readiness_and_approval_state(tmp):
    run_dir = write_plan_run(tmp, "disciplined-change-workflow", filled_common_plan(), ticket=False)

    pending = workflow_plan_check.check_plan(tmp, "disciplined-change-workflow", run_id="run-a")

    assert_ok(pending)
    assert_true(pending, "ready_for_approval")
    assert_false(pending, "implementation_allowed")
    assert pending["quality_summary"]["checked"] > 0

    write_text(run_file(run_dir, "plan.md"), filled_common_plan(approval="approved"))

    approved = workflow_plan_check.check_plan(tmp, "disciplined-change-workflow", run_id="run-a")

    assert_ok(approved)
    assert_true(approved, "implementation_allowed")
    assert_has_all(approved["operator_next_action"].lower(), "implementation")


def test_resume_surfaces_generic_task_queue_and_evidence_completeness(tmp):
    module_dir = write_fixture(tmp)
    write_json(module_file(module_dir, "module.json"), workflow_manifest_with_tasks("story-flow"))
    update_run_packet(run_dir_for(module_dir), status="partial", current_phase="execute", task_status={"shape-plan": "done"})

    report = workflow_run_support.resume_workflow_run(tmp, "story-flow", run_id="run-a")

    assert report["execution_queue"], report
    assert_fields(report["current_work_item"], id="apply-change", section="Task Graph")
    assert_has_all(report["operator_next_action"], "Apply the approved change")
    completeness = report["evidence_completeness"]
    assert_fields(completeness["core"], run_json="present", report="present")


def test_plan_terminal_status_overrides_stale_run_task_status(tmp):
    module_dir = write_fixture(tmp, "disciplined-change-workflow")
    write_json(module_file(module_dir, "module.json"), {"version": "1.0.0", "phases": []})
    run_dir = run_dir_for(module_dir)
    write_text(
        run_file(run_dir, "plan.md"),
        """# Plan

## Bounded Work Packages

| Package ID | Status | Outcome | Depends On | Verification | Handoff |
|---|---|---|---|---|---|
| WP1 | complete | first | none | check-a | continue |
| WP2 | pending | second | WP1 | check-b | finish |

## Approval Gate

Approval status: approved
""",
    )
    update_run_packet(run_dir, status="partial", task_status={"WP1": "pending", "WP2": "pending"})

    queue = run_common.generic_execution_queue(
        tmp,
        "disciplined-change-workflow",
        run_dir,
        read_json(run_file(run_dir, "run.json")),
        {},
    )

    assert len(queue) == 1, queue
    assert_fields(queue[0], id="WP2", status="pending")


def test_optional_declared_outputs_are_advisory_not_incomplete(tmp):
    module_dir = write_fixture(tmp)
    manifest = workflow_manifest("story-flow")
    manifest["outputs"] = [
        *manifest["outputs"],
        {"path": "runs/<run-id>/artifacts/optional-note.md", "required": False},
    ]
    write_json(module_file(module_dir, "module.json"), manifest)
    run_dir = run_dir_for(module_dir)

    completeness = run_common.evidence_completeness(
        tmp,
        "story-flow",
        run_dir,
        read_json(run_file(run_dir, "run.json")),
    )

    assert_status(completeness, "ok")
    assert_field(completeness, "missing_count", 0)
    assert_field(completeness, "optional_missing_count", 1)


def test_finish_parser_and_compact_report_support_bounded_output(_tmp):
    args = cli_parser.build_parser().parse_args(
        ["finish-run", "--name", "story-flow", "--summary", "--compact", "--format", "json"]
    )
    assert_true(vars(args), "summary")
    assert_true(vars(args), "compact")
    compact = workflow_repo_manager.compact_finish_run_report(
        {
            "ok": True,
            "workflow": "story-flow",
            "run_id": "run-a",
            "issues": [],
            "advisories": ["fixture advisory"],
            "missing_proof": [],
            "evidence_completeness": {"status": "ok", "missing_count": 0},
        }
    )
    assert_fields(compact, workflow="story-flow", run_id="run-a", issue_count=0, advisory_count=1)
    assert_lacks_all(compact, "proof_matrix", "checkpoint")


def test_resume_without_context_packet_declaration_returns_empty_context_budget(tmp):
    module_dir = write_fixture(tmp, "no-context-flow")
    manifest = workflow_manifest("no-context-flow")
    manifest["outputs"] = [
        output for output in manifest["outputs"] if "artifacts/context/context-packet.json" not in output
    ]
    write_json(module_file(module_dir, "module.json"), manifest)

    report = workflow_run_support.resume_workflow_run(tmp, "no-context-flow", run_id="run-a")

    assert_fields(report, workflow="no-context-flow", run_id="run-a")
    assert_fields(
        report["context_budget"],
        status="",
        compact_packet_tokens_estimated=0,
        raw_context_tokens_estimated=0,
        estimated_tokens_saved=0,
    )


def test_doctor_treats_empty_runs_as_no_retained_runs_not_risk(tmp):
    write_fixture(tmp, with_run=False)

    report = workflow_repo_manager.review_all_workflows(tmp, summary=True)
    row = report["workflows"][0]

    assert_status(report, "ok")
    assert_fields(row, risk="ok", run_status="no-retained-runs", run_retention_policy="none-retained")


def test_finish_reports_generic_declared_output_proof_gap(tmp):
    module_dir = write_fixture(tmp)
    manifest = workflow_manifest("story-flow")
    manifest["outputs"] = [
        *manifest["outputs"],
        {"path": "runs/<run-id>/artifacts/required-proof.md", "required": True},
    ]
    write_json(module_file(module_dir, "module.json"), manifest)
    run_dir = run_dir_for(module_dir)
    write_start_context_evidence(tmp, "story-flow", run_dir)

    missing_report = workflow_run_support.finish_workflow_run(tmp, "story-flow", run_id="run-a")

    assert_not_ok(missing_report)
    assert_contains(missing_report["missing_proof"], "required-proof.md")
    assert missing_report["proof_matrix"]["declared_outputs"]["missing_count"] == 1

    write_text(run_file(run_dir, "artifacts", "required-proof.md"), "fixture proof")

    present_report = workflow_run_support.finish_workflow_run(tmp, "story-flow", run_id="run-a")

    assert_lacks(present_report["issues"], "required-proof.md")
    assert present_report["proof_matrix"]["declared_outputs"]["missing_count"] == 0


def test_finish_records_feedback_for_missing_declared_output_proof(tmp):
    write_feedback_manager(tmp)
    module_dir = write_fixture(tmp)
    manifest = workflow_manifest("story-flow")
    manifest["outputs"] = [
        *manifest["outputs"],
        {"path": "runs/<run-id>/artifacts/required-proof.md", "required": True},
    ]
    write_json(module_file(module_dir, "module.json"), manifest)
    run_dir = run_dir_for(module_dir)
    write_start_context_evidence(tmp, "story-flow", run_dir)

    report = workflow_run_support.finish_workflow_run(tmp, "story-flow", run_id="run-a")

    feedback_path = tmp / ".agents/local-ai/cache/feedback/failure-feedback.jsonl"
    entries = [json.loads(line) for line in feedback_path.read_text(encoding="utf-8").splitlines()]

    assert_not_ok(report)
    assert len(entries) == 1
    assert_fields(entries[0], target_kind="workflow", target="story-flow", source_tool="workflow-manager.finish-run")
    assert_has_all(entries[0]["what_failed"], "required-proof.md")
    assert_contains(entries[0]["context_paths"], "automations/story-flow/runs/run-a/run.json")


def feedback_improvement_manifest():
    manifest = workflow_manifest("feedback-improvement-workflow")
    manifest["phases"] = [
        {"id": "collect-feedback", "summary": "Collect explicit feedback summary and export packets."},
        {"id": "build-action-plan", "summary": "Build the compact improvement action plan."},
        {"id": "review-validation-delta", "summary": "Verify each action item has validation and regression coverage."},
        {"id": "clear-ledger", "summary": "Truncate the processed feedback ledger after action-plan evidence exists."},
        {"id": "handoff", "summary": "Handoff the plan-only improvement packet."},
    ]
    manifest["outputs"] = [
        *manifest["outputs"],
        "runs/<run-id>/action-plan.md",
        "runs/<run-id>/artifacts/feedback/feedback-candidates.json",
        "runs/<run-id>/validation/clear-feedback.json",
    ]
    manifest["commands"] = [
        *manifest["commands"],
        "python -B .agents/manage.py feedback summary --all --summary --compact --format json",
        "python -B .agents/manage.py feedback export --all --min-count 2 --output automations/feedback-improvement-workflow/runs/<run-id>/artifacts/feedback",
        "python -B .agents/manage.py feedback clear --all --confirm-truncate --reason <reason> --action-plan automations/feedback-improvement-workflow/runs/<run-id>/action-plan.md --format json",
    ]
    manifest["related_modules"] = ["skill-manager", "workflow-manager", "disciplined-change-workflow"]
    manifest["local_ai"] = {"use_cases": ["failure-cluster", "test-gap-summary", "handoff-draft"]}
    return manifest


def write_feedback_improvement_fixture(tmp):
    module_dir = write_workflow(tmp, "feedback-improvement-workflow", with_run=True)
    write_json(module_file(module_dir, "module.json"), feedback_improvement_manifest())
    run_dir = run_dir_for(module_dir)
    write_start_context_evidence(tmp, "feedback-improvement-workflow", run_dir)
    return module_dir


def complete_feedback_action_plan() -> str:
    return """# Feedback Improvement Action Plan

## Candidate Action Items

| Target | Failure Type | Count | First Failing Fact | Owner | Follow-up Vehicle | Evidence References | Risk | Baseline Command | Expected Failing Fact Before Change | Expected Behavior After Change | Acceptance Commands | Evidence To Capture | Regression Guard | Regression Owner | Regression Rationale |
|---|---:|---:|---|---|---|---|---|---|---|---|---|---|---|---|---|
| skill-manager | stale-generated-or-cache | 2 | generated routing is stale | skill-manager | disciplined-change-workflow | .agents/local-ai/cache/last-validation.txt | low | python -B .agents/manage.py sync --check | generated routing is stale | sync --check passes | python -B .agents/manage.py check-additions; python -B .agents/manage.py check | evidence/feedback/sync-check.json | skill-manager self-test fixture | skill-manager | catches stale generated routing before finish |

## Not Actionable Now

- None.
"""


def test_feedback_improvement_workflow_validates_and_eval_contract_is_plan_only(_tmp):
    module_dir = REPO_ROOT / "automations" / "feedback-improvement-workflow"
    suite = module_dir / "suites" / "workflow-evals.json"

    errors, warnings, _modules = validate_automations.validate_automations(
        REPO_ROOT,
        workflow_name="feedback-improvement-workflow",
        strict_phase_quality=True,
    )
    report = eval_workflow.run_eval(
        eval_workflow.Args(
            root=REPO_ROOT,
            workflow_name="feedback-improvement-workflow",
            suite=suite,
            output_format="json",
        )
    )

    assert_empty(errors)
    assert report["summary"]["failed"] == 0, report


def test_feedback_improvement_finish_requires_action_plan_clear_and_regression_fields(tmp):
    module_dir = write_feedback_improvement_fixture(tmp)
    run_dir = run_dir_for(module_dir)

    missing_report = workflow_run_support.finish_workflow_run(
        tmp,
        "feedback-improvement-workflow",
        run_id="run-a",
    )

    assert_not_ok(missing_report)
    assert_contains(missing_report["missing_proof"], "action-plan.md")
    assert_contains(missing_report["missing_proof"], "clear-feedback.json")

    write_text(run_file(run_dir, "action-plan.md"), "# Feedback Improvement Action Plan\n\n## Candidate Action Items\n\n| Target | Failure Type |\n|---|---|\n| skill-manager | failed-check |\n")
    write_json(run_file(run_dir, "artifacts", "feedback", "feedback-candidates.json"), {"candidates": []})
    write_json(run_file(run_dir, "validation", "clear-feedback.json"), {"ok": True, "status": "cleared"})

    incomplete_report = workflow_run_support.finish_workflow_run(
        tmp,
        "feedback-improvement-workflow",
        run_id="run-a",
    )

    assert_not_ok(incomplete_report)
    assert_contains_each(
        incomplete_report["missing_proof"],
        "Expected Behavior After Change",
        "Regression Guard",
    )

    write_text(run_file(run_dir, "action-plan.md"), complete_feedback_action_plan())

    complete_report = workflow_run_support.finish_workflow_run(
        tmp,
        "feedback-improvement-workflow",
        run_id="run-a",
    )

    assert_ok(complete_report)


def test_handoff_and_finish_include_generic_reusable_lessons(tmp):
    module_dir = write_fixture(tmp)
    run_dir = run_dir_for(module_dir)
    write_text(
        run_file(run_dir, "REPORT.md"),
        f"""# Fixture Report

## Reusable Lessons

- {FIXTURE_PROOF_LESSON} for workflow runtime changes.
""",
    )
    write_start_context_evidence(tmp, "story-flow", run_dir)

    handoff = workflow_run_support.handoff_workflow_run(tmp, "story-flow", run_id="run-a")
    finish = workflow_run_support.finish_workflow_run(tmp, "story-flow", run_id="run-a")

    assert_has_all(" ".join(handoff["lesson_candidates"]), FIXTURE_PROOF_LESSON)
    assert_has_all(" ".join(finish["lesson_candidates"]), FIXTURE_PROOF_LESSON)


def test_resume_execution_queue_for_approved_story_plan(tmp):
    plan = approved_story_plan()
    run_dir = write_plan_run(tmp, "user-story-workflow", plan)
    packet = read_json(run_file(run_dir, "run.json"))
    packet["task_status"] = {"WP1": "completed", "WP2": "pending"}
    write_json(run_file(run_dir, "run.json"), packet)

    report = workflow_run_support.resume_workflow_run(tmp, "user-story-workflow", run_id="run-a")

    assert_true(report["plan_gate"], "implementation_allowed")
    assert report["execution_queue"], report
    assert_fields(report["current_work_item"], row=2, step=ADD_PLAN_CHECK)
    assert_has_all(report["operator_next_action"], ADD_PLAN_CHECK)


def test_resume_execution_queue_for_approved_bug_plan(tmp):
    plan = approved_bug_plan().replace(
        "| WP1 | Request approval | No fix before approval | none | No source changes | workflow run | plan-check | approval is recorded | Continue with approved fix |",
        "| WP1 | Approval recorded | Preserve defect scope | none | No unrelated changes | workflow run | approval evidence | approval recorded | Continue to WP2 |\n"
        "| WP2 | Apply targeted fix | Preserve unaffected behavior | WP1 | No broad refactor | target files | regression test | bug behavior changes | Record validation handoff |",
    )
    run_dir = write_plan_run(tmp, "bug-ticket-workflow", plan)
    packet = read_json(run_file(run_dir, "run.json"))
    packet["task_status"] = {"WP1": "completed", "WP2": "pending"}
    write_json(run_file(run_dir, "run.json"), packet)

    report = workflow_run_support.resume_workflow_run(tmp, "bug-ticket-workflow", run_id="run-a")

    assert_true(report["plan_gate"], "implementation_allowed")
    assert_fields(report["current_work_item"], row=2, step="Apply targeted fix")
    assert_field(report["execution_queue"][0], "section", "Bounded Work Packages")


def test_resume_pending_approval_blocks_execution_queue(tmp):
    write_plan_run(tmp, "user-story-workflow", filled_story_plan())

    report = workflow_run_support.resume_workflow_run(tmp, "user-story-workflow", run_id="run-a")

    assert_true(report["plan_gate"], "ready_for_approval")
    assert_empty(report.get("execution_queue"))
    assert_field(report, "current_work_item", {})
    assert_has_all(report["operator_next_action"].lower(), "approval")


def test_bounded_work_packages_reject_unknown_dependencies_and_cycles(_tmp):
    text = """# Plan

## Bounded Work Packages

| Package ID | Outcome | Invariant | Depends On | Non-Goals | Owner Paths | Verification | Completion Criteria | Handoff |
|---|---|---|---|---|---|---|---|---|
| WP1 | first | stable | WP2 | no unrelated work | owner/a | check-a | first done | continue |
| WP2 | second | stable | WP1, missing | no unrelated work | owner/b | check-b | second done | finish |
"""
    checked, issues = workflow_plan_check.check_bounded_work_packages(workflow_plan_check.parse_sections(text))

    assert checked > 0
    messages = "\n".join(str(item.get("message", "")) for item in issues)
    assert_has_all(
        messages,
        "unknown dependency: missing",
        "dependency must reference an earlier Package ID: WP2",
        "dependency graph contains a cycle",
    )

    missing_dependency_text = text.replace("| WP1 | first | stable | WP2 |", "| WP1 | first | stable | |")
    _checked, missing_dependency_issues = workflow_plan_check.check_bounded_work_packages(
        workflow_plan_check.parse_sections(missing_dependency_text)
    )
    assert_has_all(
        "\n".join(str(item.get("message", "")) for item in missing_dependency_issues),
        "Depends On is empty or unresolved",
    )


def test_checkpoint_records_plan_hash_and_next_unblocked_package(tmp):
    workflow_name = "disciplined-change-workflow"
    module_dir = tmp / "automations" / workflow_name
    run_dir = module_dir / "runs" / "run-a"
    write_json(module_dir / "module.json", {"version": "1.0.0", "phases": []})
    write_text(module_dir / "WORKFLOW.md", "# Fixture")
    write_text(
        run_dir / "plan.md",
        """# Plan

## Bounded Work Packages

| Package ID | Outcome | Invariant | Depends On | Non-Goals | Owner Paths | Verification | Completion Criteria | Handoff |
|---|---|---|---|---|---|---|---|---|
| WP1 | first | stable | none | no unrelated work | owner/a | check-a | first done | continue |
| WP2 | second | stable | WP1 | no unrelated work | owner/b | check-b | second done | finish |

## Approval Gate

Approval status: approved
""",
    )
    packet = {"run_id": "run-a", "status": "partial", "current_phase": "execute", "task_status": {"WP1": "completed", "WP2": "pending"}}

    report = workflow_checkpoint.build_workflow_checkpoint(tmp, workflow_name, run_dir, packet)

    plan = report["snapshot"]["plan"]
    assert plan["sha256"]
    assert_fields(plan["next_unblocked_package"], id="WP2", step="second")


def test_blocked_dependency_does_not_unlock_downstream_work(tmp):
    plan = approved_story_plan()
    run_dir = write_plan_run(tmp, "user-story-workflow", plan)
    packet = read_json(run_file(run_dir, "run.json"))
    packet["task_status"] = {"WP1": "blocked", "WP2": "pending"}
    write_json(run_file(run_dir, "run.json"), packet)

    blocked = workflow_run_support.resume_workflow_run(tmp, "user-story-workflow", run_id="run-a")
    assert_empty(blocked["execution_queue"])

    packet["task_status"] = {"WP1": "skipped", "WP2": "pending"}
    write_json(run_file(run_dir, "run.json"), packet)
    waived = workflow_run_support.resume_workflow_run(tmp, "user-story-workflow", run_id="run-a")
    assert_field(waived["execution_queue"][0], "id", "WP2")


def test_checkpoint_does_not_advertise_execution_before_approval(tmp):
    workflow_name = "disciplined-change-workflow"
    module_dir = tmp / "automations" / workflow_name
    run_dir = module_dir / "runs" / "run-a"
    write_json(module_dir / "module.json", {"version": "1.0.0", "phases": []})
    write_text(module_dir / "WORKFLOW.md", "# Fixture")
    plan = filled_common_plan(approval="pending")
    write_text(run_dir / "plan.md", plan)
    packet = {"run_id": "run-a", "status": "partial", "current_phase": "plan", "task_status": {"WP1": "pending"}}

    report = workflow_checkpoint.build_workflow_checkpoint(tmp, workflow_name, run_dir, packet)

    assert_field(report["snapshot"]["plan"], "next_unblocked_package", {})


def test_closeout_evidence_requires_variance_and_each_review_axis(tmp):
    run_dir = tmp / "automations" / "user-story-workflow" / "runs" / "run-a"
    write_text(run_dir / "execution-log.md", "# Log\n\n## Plan Variance\n\n## Independent Review Evidence\n")
    missing = story_bug_quality.closeout_evidence_issues(tmp, "user-story-workflow", run_dir)
    assert_contains_each(missing, "Plan Variance needs", "missing axis")

    write_text(run_dir / "plan.md", approved_story_plan())
    valid = progress_log_text("user-story-workflow")
    write_text(run_dir / "execution-log.md", valid)
    assert_empty(story_bug_quality.closeout_evidence_issues(tmp, "user-story-workflow", run_dir))

    write_text(run_dir / "execution-log.md", valid.replace("| No variance |", "| WP404 |"))
    assert_contains_each(
        story_bug_quality.closeout_evidence_issues(tmp, "user-story-workflow", run_dir),
        "references unknown package",
    )

    write_text(run_dir / "execution-log.md", valid.replace("| fixture review | passed |", "| fixture review | failed |", 1))
    assert_contains_each(
        story_bug_quality.closeout_evidence_issues(tmp, "user-story-workflow", run_dir),
        "non-finishable result",
    )

    write_text(
        run_dir / "execution-log.md",
        valid.replace(
            "skipped: fixture has no security-sensitive changes",
            "skipped with reason",
        ),
    )
    assert_contains_each(
        story_bug_quality.closeout_evidence_issues(tmp, "user-story-workflow", run_dir),
        "substantive reason",
    )


def test_pr_handoff_requires_filled_closeout_tables_and_substantive_skip_reason(tmp):
    run_dir = tmp / "automations" / "user-story-workflow" / "runs" / "run-a"
    valid = pr_description()
    write_text(run_dir / "pr-description.md", valid)
    assert_empty(story_bug_quality.pr_handoff_issues(tmp, run_dir))

    write_text(
        run_dir / "pr-description.md",
        valid.replace(
            "| No variance | fixture plan | fixture result | execution matched plan | none accepted by owner | validation unchanged |",
            "",
        ),
    )
    assert_contains_each(story_bug_quality.pr_handoff_issues(tmp, run_dir), "Plan Variance needs")

    write_text(
        run_dir / "pr-description.md",
        valid.replace("skipped: fixture has no security-sensitive changes", "skipped with reason"),
    )
    assert_contains_each(story_bug_quality.pr_handoff_issues(tmp, run_dir), "substantive reason")


def test_plan_check_requires_start_context_evidence_for_run(tmp):
    module_dir = write_fixture(tmp, "user-story-workflow")
    write_text(run_plan_path(module_dir), filled_story_plan())

    report = workflow_plan_check.check_plan(tmp, "user-story-workflow", run_id="run-a")

    assert_not_ok(report)
    assert_contains(report["issues"], "context evidence packet is missing or invalid")


def test_context_evidence_packet_writes_deterministic_scan_and_updates_run(tmp):
    module_dir = write_fixture(tmp, "user-story-workflow")
    run_dir = run_dir_for(module_dir)
    packet = read_json(run_file(run_dir))

    report = workflow_context_evidence.write_context_evidence_packet(
        tmp,
        "user-story-workflow",
        run_dir,
        packet,
        event="start",
        write=True,
        write_run=True,
    )

    assert_ok(report)
    assert_status(report, "complete")
    assert_fields(report["quality"], bounded_path_query_count=report["quality"]["query_count"])
    assert_true(report["queries"][0]["quality"], "evidence_available")
    assert_field(report["queries"][0], "retrieval_mode", "deterministic-file-scan")
    assert run_file(run_dir, "validation", "context-evidence-start.json").exists()
    assert run_file(run_dir, "validation", "context-evidence-start.md").exists()
    updated = read_json(run_file(run_dir))
    assert updated["context_evidence"]["start"]["packet"].endswith("validation/context-evidence-start.json")
    assert_has_all(updated["evidence_paths"], "automations/user-story-workflow/runs/run-a/validation/context-evidence-start.json")


def test_context_evidence_ignores_local_ai_configuration(tmp):
    module_dir = write_fixture(tmp, "user-story-workflow")
    run_dir = run_dir_for(module_dir)
    packet = read_json(run_file(run_dir))
    write_json(
        tmp / ".agents" / "local-ai.json",
        {"enabled": True, "context_evidence": {"auto_maintain": False, "auto_refresh": "never"}},
    )
    report = workflow_context_evidence.write_context_evidence_packet(
        tmp,
        "user-story-workflow",
        run_dir,
        packet,
        event="start",
        write=False,
    )

    assert_ok(report)
    assert_status(report, "complete")
    assert_field(report["queries"][0], "retrieval_mode", "deterministic-file-scan")
    assert "repository_index" not in report["queries"][0]


def test_finish_context_evidence_records_changed_file_relevance(tmp):
    module_dir = write_fixture(tmp, "user-story-workflow")
    run_dir = run_dir_for(module_dir)
    packet = read_json(run_file(run_dir))
    packet["changed_files"] = ["automations/user-story-workflow/WORKFLOW.md"]

    report = workflow_context_evidence.write_context_evidence_packet(
        tmp,
        "user-story-workflow",
        run_dir,
        packet,
        event="finish",
        write=False,
    )

    refresh = report["changed_file_refresh"]
    assert_status(refresh, "complete")
    assert refresh["relevance"][0]["path"] == "automations/user-story-workflow/WORKFLOW.md"
    assert_status(refresh["relevance"][0], "related")
    assert_has_all(refresh["relevance"][0]["matched_query_ids"], "finish-evidence")


def test_smoke_workflows_runs_lifecycle_and_cleans_fixture_run(tmp):
    module_dir = write_fixture(tmp)
    write_text(template_path(module_dir), f"# Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}")
    write_text(template_path(module_dir, "lean-plan.md"), f"# Lean Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}")
    write_guidance_savings_fixture(tmp)

    report = workflow_smoke.smoke_workflows(
        tmp,
        workflow_names=["story-flow"],
        include_domain_checks=False,
        summary=True,
        compact=True,
    )

    assert_ok(report)
    assert_field(report["summary"], "workflows", 1)
    assert_field(report["summary"], "workflows_passed", 1)
    assert_field(report["summary"], "workflows_failed", 0)
    assert report["summary"]["checks"] >= 5
    assert report["summary"]["passed_checks"] >= 5
    assert_field(report["summary"], "failed_checks", 0)
    assert_has_all(report["summary"]["check_names"], "start-run", "resume-after-abort", "checkpoint-write", "handoff-write", "finish-run")
    assert_has_all(report["workflows"][0]["check_names"], "start-run", "resume-after-abort", "checkpoint-write", "handoff-write", "finish-run")
    assert_empty(smoke_run_dirs(tmp, "story-flow"))


def test_disciplined_smoke_fits_measured_evidence_phase_budget(_tmp):
    config = read_json(REPO_ROOT / ".agents" / "project-policy.json")
    evidence_budget = config["cost_policy"]["budgets"]["phases"]["overrides"]["evidence"]
    packet_budget = config["limits"]["workflow"]["context_packet_token_limit"]
    assert evidence_budget > packet_budget

    captured = {}
    original_context = workflow_smoke.context_workflow_run

    def capture_context(*args, **kwargs):
        result = original_context(*args, **kwargs)
        captured.update(
            {
                "execution_profile": result.get("execution_profile", {}),
                "token_estimates": result.get("token_estimates", {}),
            }
        )
        return result

    with patch.object(
        workflow_smoke,
        "context_workflow_run",
        side_effect=capture_context,
    ):
        report = workflow_smoke.workflow_lifecycle_smoke(
            REPO_ROOT,
            "disciplined-change-workflow",
        )

    assert_ok(report)
    execution = captured["execution_profile"]
    estimates = captured["token_estimates"]
    assert_fields(
        execution,
        context_budget_ref="evidence",
        budget_tokens=evidence_budget,
        budget_source="configured",
        within_budget=True,
    )
    assert execution["effective_context_tokens"] == estimates["effective_load_tokens_estimated"]
    assert execution["remaining_margin_tokens"] > 0


def test_smoke_public_orchestrator_stays_low_context(_tmp):
    text = Path(workflow_smoke.__file__).read_text(encoding="utf-8")
    lines = text.splitlines()
    top_level_functions = [line for line in lines if line.startswith("def ")]

    assert len(lines) <= 950, f"smoke.py has {len(lines)} lines; move fixture details to routed helpers"
    assert len(top_level_functions) <= 30, (
        f"smoke.py has {len(top_level_functions)} top-level functions; "
        "keep smoke.py as the orchestrator and route fixtures to helper modules"
    )


def test_smoke_workflows_dry_run_reports_plan_without_run_writes(tmp):
    write_fixture(tmp)

    report = workflow_smoke.smoke_workflows(
        tmp,
        workflow_names=["story-flow"],
        include_domain_checks=False,
        dry_run=True,
        summary=True,
        compact=True,
    )

    assert_ok(report)
    assert_field(report, "status", "planned")
    assert_true(report, "dry_run")
    assert_field(report["latency_budget"], "command", "smoke-workflows")
    assert_field(report["output_budget"], "command", "smoke-workflows")
    assert_field(report["output_budget"], "status", "within-budget")
    assert_field(report["summary"], "workflows", 1)
    assert_field(report["summary"], "planned", 1)
    assert_field(report["summary"], "workflows_planned", 1)
    assert_field(report["summary"], "passed_checks", 0)
    assert_field(report["summary"], "failed_checks", 0)
    assert report["summary"]["planned_checks"] >= 5
    assert_has_all(report["next_command"], "workflow smoke", "--name story-flow", "--dry-run")
    assert_lacks(report["next_command"], "--all")
    assert report["summary"]["checks"] >= 5
    assert_field(report["workflows"][0], "workflow", "story-flow")
    assert_field(report["workflows"][0], "status", "planned")
    assert report["workflows"][0]["planned_checks"] == [
        "start",
        "checkpoint",
        "handoff",
        "context",
        "finish",
        "cleanup",
    ]
    assert_empty(smoke_run_dirs(tmp, "story-flow"))


def test_default_workflow_context_uses_generated_navigation_handoff(tmp):
    write_fixture(tmp, with_run=False)
    handoff = tmp / "automations" / "navigation" / "artifacts" / "maps" / "HANDOFF.md"
    write_text(handoff, "# Handoff\n")

    context = workflow_run_support.default_workflow_context(tmp, "story-flow")

    assert_contains(context, "automations/navigation/artifacts/maps/HANDOFF.md")


def test_smoke_cleanup_removes_empty_runs_parent(tmp):
    run_dir = workflow_run_dir(tmp, run_id="smoke-local-fixture")
    write_json(run_file(run_dir), {"status": "partial"})
    write_text(workflow_runs_dir(tmp, "story-flow") / "INDEX.md", "# stale\n")
    write_json(workflow_runs_dir(tmp, "story-flow") / "index.json", {"stale": True})

    cleanup = workflow_smoke.cleanup_smoke_run(tmp, "story-flow", "smoke-local-fixture")

    assert_true(cleanup, "removed")
    assert_true(cleanup, "removed_empty_runs_dir")
    assert_has_all(
        cleanup["removed_index_files"],
        "automations/story-flow/runs/INDEX.md",
        "automations/story-flow/runs/index.json",
    )
    assert not workflow_runs_dir(tmp, "story-flow").exists()


def test_smoke_cleanup_preserves_tracked_empty_run_index(tmp):
    run_dir = workflow_run_dir(tmp, run_id="smoke-local-fixture")
    write_json(run_file(run_dir), {"status": "partial"})
    index_md = workflow_runs_dir(tmp, "story-flow") / "INDEX.md"
    index_json = workflow_runs_dir(tmp, "story-flow") / "index.json"
    write_text(index_md, "# tracked\n")
    write_json(index_json, {"tracked": True})
    original = getattr(workflow_smoke, "is_tracked_file", None)
    workflow_smoke.is_tracked_file = lambda _root, path: path in {index_md, index_json}
    try:
        cleanup = workflow_smoke.cleanup_smoke_run(tmp, "story-flow", "smoke-local-fixture")
    finally:
        if original is None:
            delattr(workflow_smoke, "is_tracked_file")
        else:
            workflow_smoke.is_tracked_file = original

    assert_true(cleanup, "removed")
    assert_false(cleanup, "removed_empty_runs_dir")
    assert_empty(cleanup["removed_index_files"])
    assert index_md.exists()
    assert index_json.exists()
    assert_field(read_json(index_json)["summary"], "total", 0)
    assert "Total runs: 0" in index_md.read_text(encoding="utf-8")


def test_smoke_cleanup_refreshes_tracked_run_index_with_retained_runs(tmp):
    retained_run = workflow_run_dir(tmp, run_id="run-a")
    write_json(run_file(retained_run), {"schema_version": 2, "status": "completed", "summary": "Retained"})
    write_text(run_file(retained_run, "REPORT.md"), "# Retained\n")
    smoke_run = workflow_run_dir(tmp, run_id="smoke-local-fixture")
    write_json(run_file(smoke_run), {"status": "partial"})
    write_text(run_file(smoke_run, "REPORT.md"), "# Smoke\n")
    index_md = workflow_runs_dir(tmp, "story-flow") / "INDEX.md"
    index_json = workflow_runs_dir(tmp, "story-flow") / "index.json"
    write_text(index_md, "# stale\n\nsmoke-local-fixture\n")
    write_json(index_json, {"summary": {"total": 2}, "runs": [{"run_id": "smoke-local-fixture"}]})
    original = getattr(workflow_smoke, "is_tracked_file", None)
    workflow_smoke.is_tracked_file = lambda _root, path: path in {index_md, index_json}
    try:
        cleanup = workflow_smoke.cleanup_smoke_run(tmp, "story-flow", "smoke-local-fixture")
    finally:
        if original is None:
            delattr(workflow_smoke, "is_tracked_file")
        else:
            workflow_smoke.is_tracked_file = original

    assert_true(cleanup, "removed")
    assert_empty(cleanup["removed_index_files"])
    assert_has_all(
        cleanup["refreshed_index_files"],
        "automations/story-flow/runs/INDEX.md",
        "automations/story-flow/runs/index.json",
    )
    refreshed = read_json(index_json)
    assert_field(refreshed["summary"], "total", 1)
    assert_field(refreshed["runs"][0], "id", "run-a")
    assert "smoke-local-fixture" not in index_md.read_text(encoding="utf-8")


def test_smoke_cleanup_refreshes_untracked_run_index_with_retained_runs(tmp):
    retained_run = workflow_run_dir(tmp, run_id="run-a")
    write_json(run_file(retained_run), {"schema_version": 2, "status": "completed", "summary": "Retained"})
    write_text(run_file(retained_run, "REPORT.md"), "# Retained\n")
    smoke_run = workflow_run_dir(tmp, run_id="smoke-local-fixture")
    write_json(run_file(smoke_run), {"status": "partial"})
    write_text(run_file(smoke_run, "REPORT.md"), "# Smoke\n")
    index_md = workflow_runs_dir(tmp, "story-flow") / "INDEX.md"
    index_json = workflow_runs_dir(tmp, "story-flow") / "index.json"
    write_text(index_md, "# stale\n\nsmoke-local-fixture\n")
    write_json(index_json, {"summary": {"total": 2}, "runs": [{"run_id": "smoke-local-fixture"}]})
    original = getattr(workflow_smoke, "is_tracked_file", None)
    workflow_smoke.is_tracked_file = lambda _root, _path: False
    try:
        cleanup = workflow_smoke.cleanup_smoke_run(tmp, "story-flow", "smoke-local-fixture")
    finally:
        if original is None:
            delattr(workflow_smoke, "is_tracked_file")
        else:
            workflow_smoke.is_tracked_file = original

    assert_true(cleanup, "removed")
    assert_empty(cleanup["removed_index_files"])
    assert_has_all(
        cleanup["refreshed_index_files"],
        "automations/story-flow/runs/INDEX.md",
        "automations/story-flow/runs/index.json",
    )
    refreshed = read_json(index_json)
    assert_field(refreshed["summary"], "total", 1)
    assert_field(refreshed["runs"][0], "id", "run-a")
    assert "smoke-local-fixture" not in index_md.read_text(encoding="utf-8")


def test_smoke_cleanup_retries_transient_file_locks(tmp):
    run_dir = workflow_run_dir(tmp, run_id="smoke-local-fixture")
    locked_file = run_file(run_dir, "artifacts", "context", "context-packet.md")
    write_text(locked_file, "# Context\n")
    write_json(run_file(run_dir), {"status": "partial"})
    attempts = {"count": 0}
    original_unlink = Path.unlink
    original_sleep = workflow_smoke.time.sleep

    def flaky_unlink(self, *args, **kwargs):
        if self == locked_file and attempts["count"] == 0:
            attempts["count"] += 1
            raise PermissionError(32, "file is locked", str(self))
        return original_unlink(self, *args, **kwargs)

    Path.unlink = flaky_unlink
    workflow_smoke.time.sleep = lambda _seconds: None
    try:
        cleanup = workflow_smoke.cleanup_smoke_run(tmp, "story-flow", "smoke-local-fixture")
    finally:
        Path.unlink = original_unlink
        workflow_smoke.time.sleep = original_sleep

    assert_true(cleanup, "removed")
    assert_field(attempts, "count", 1)
    assert not run_dir.exists()


def test_run_index_check_accepts_empty_runs_parent(tmp):
    write_fixture(tmp, with_run=False)
    workflow_runs_dir(tmp, "story-flow").mkdir(parents=True, exist_ok=True)

    status = index_workflow_runs.run(
        index_workflow_runs.Args(
            root=tmp,
            workflow_name="story-flow",
            write=False,
            check=True,
            output_format="json",
        )
    )

    assert status == 0


def test_smoke_and_scorecard_fail_when_cleanup_fails(tmp):
    module_dir = write_fixture(tmp)
    write_text(template_path(module_dir), f"# Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}")
    write_text(template_path(module_dir, "lean-plan.md"), f"# Lean Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}")
    original_cleanup = workflow_smoke.cleanup_smoke_run

    def fake_cleanup(root, workflow_name, run_id):
        run_dir = root / "automations" / workflow_name / "runs" / run_id
        return {"removed": False, "path": common.relative(root, run_dir), "reason": "outside-smoke-boundary"}

    workflow_smoke.cleanup_smoke_run = fake_cleanup
    try:
        smoke_report = workflow_smoke.smoke_workflows(
            tmp,
            workflow_names=["story-flow"],
            include_domain_checks=False,
            summary=False,
        )
        scorecard = workflow_scorecard.workflow_scorecard(tmp, "story-flow", run_lifecycle=True)
    finally:
        workflow_smoke.cleanup_smoke_run = original_cleanup

    assert_not_ok(smoke_report)
    workflow_row = smoke_report["workflows"][0]
    assert_false(workflow_row, "cleanup_ok")
    assert_field(workflow_row["cleanup"], "reason", "outside-smoke-boundary")
    lifecycle = next(item for item in scorecard["checks"] if item["name"] == "lifecycle-smoke")
    assert_not_ok(lifecycle)
    assert lifecycle["details"]["cleanup"]["reason"] == "outside-smoke-boundary"


def test_lifecycle_smoke_restores_index_when_cleanup_raises(tmp):
    write_fixture(tmp, with_run=False)
    restoration = {"ok": True, "status": "restored", "issues": []}
    with (
        patch.object(workflow_smoke, "start_workflow_run", side_effect=SystemExit("controlled stop")),
        patch.object(workflow_smoke, "cleanup_smoke_run", side_effect=PermissionError("locked run")),
        patch.object(workflow_smoke, "restore_run_index_state", return_value=restoration) as restore,
    ):
        report = workflow_smoke.workflow_lifecycle_smoke(tmp, "story-flow")

    assert_not_ok(report)
    assert_field(report["cleanup"], "reason", "cleanup-exception")
    assert_field(report["cleanup"], "index_restoration", restoration)
    assert restore.call_count == 1


def test_run_index_restore_preserves_concurrent_retained_run_changes(tmp):
    write_fixture(tmp)
    runs_dir = workflow_runs_dir(tmp, "story-flow")
    index_md = runs_dir / "INDEX.md"
    index_json = runs_dir / "index.json"
    baseline_index = index_workflow_runs.build_index(tmp, "story-flow")
    index_workflow_runs.write_outputs(tmp, baseline_index)
    snapshot = workflow_smoke.snapshot_run_index_state(tmp, "story-flow")
    assert_true(snapshot, "ok")

    concurrent_run = workflow_run_dir(tmp, "story-flow", "run-concurrent")
    write_json(run_file(concurrent_run), run_packet("story-flow"))
    write_text(run_file(concurrent_run, "REPORT.md"), "# Concurrent\n")
    restoration = workflow_smoke.restore_run_index_state(snapshot)

    assert_true(restoration, "ok")
    assert_field(restoration, "status", "concurrent-change-preserved")
    assert_true(restoration, "concurrent_change_detected")
    rebuilt = read_json(index_json)
    assert "run-concurrent" in {row["id"] for row in rebuilt["runs"]}
    assert "run-concurrent" in index_md.read_text(encoding="utf-8")


def test_run_index_snapshot_retries_when_real_run_changes_during_capture(tmp):
    write_fixture(tmp)
    index_workflow_runs.write_outputs(
        tmp,
        index_workflow_runs.build_index(tmp, "story-flow"),
    )
    index_md = workflow_runs_dir(tmp, "story-flow") / "INDEX.md"
    original_read_bytes = Path.read_bytes
    injected = {"done": False}

    def racing_read_bytes(path):
        if path == index_md and not injected["done"]:
            injected["done"] = True
            concurrent_run = workflow_run_dir(tmp, "story-flow", "run-concurrent")
            write_json(run_file(concurrent_run), run_packet("story-flow"))
            write_text(run_file(concurrent_run, "REPORT.md"), "# Concurrent\n")
            index_workflow_runs.write_outputs(
                tmp,
                index_workflow_runs.build_index(tmp, "story-flow"),
            )
        return original_read_bytes(path)

    with patch.object(Path, "read_bytes", racing_read_bytes):
        snapshot = workflow_smoke.snapshot_run_index_state(tmp, "story-flow")

    assert_true(snapshot, "ok")
    assert_field(snapshot, "snapshot_attempts", 2)
    captured = json.loads(snapshot["files"]["index.json"].decode("utf-8"))
    assert "run-concurrent" in {row["id"] for row in captured["runs"]}


def test_run_index_snapshot_fingerprints_overlapping_smoke_runs(tmp):
    write_fixture(tmp, with_run=False)
    before = workflow_smoke.snapshot_run_index_state(tmp, "story-flow")
    assert_true(before, "ok")
    smoke_one = workflow_run_dir(tmp, "story-flow", "smoke-local-overlap-one")
    write_json(run_file(smoke_one), {"status": "partial"})
    index_workflow_runs.write_outputs(
        tmp,
        index_workflow_runs.build_index(tmp, "story-flow"),
    )
    during = workflow_smoke.snapshot_run_index_state(tmp, "story-flow")
    assert_true(during, "ok")
    assert before["retained_state_digest"] != during["retained_state_digest"]

    smoke_two = workflow_run_dir(tmp, "story-flow", "smoke-local-overlap-two")
    write_json(run_file(smoke_two), {"status": "partial"})
    index_workflow_runs.write_outputs(
        tmp,
        index_workflow_runs.build_index(tmp, "story-flow"),
    )
    cleanup_one = workflow_smoke.cleanup_smoke_run(
        tmp,
        "story-flow",
        "smoke-local-overlap-one",
    )
    restore_one = workflow_smoke.restore_run_index_state(before)
    cleanup_two = workflow_smoke.cleanup_smoke_run(
        tmp,
        "story-flow",
        "smoke-local-overlap-two",
    )
    restore_two = workflow_smoke.restore_run_index_state(during)

    assert_true(cleanup_one, "removed")
    assert_true(cleanup_two, "removed")
    assert_field(restore_one, "status", "concurrent-change-preserved")
    assert_field(restore_two, "status", "concurrent-change-preserved")
    assert_empty(smoke_run_dirs(tmp, "story-flow"))
    assert not workflow_runs_dir(tmp, "story-flow").exists()


def test_run_index_snapshot_rejects_symlink_index_entry(tmp):
    write_fixture(tmp, with_run=False)
    runs_dir = workflow_runs_dir(tmp, "story-flow")
    runs_dir.mkdir(parents=True, exist_ok=True)
    index_md = runs_dir / "INDEX.md"
    write_text(index_md, "# pretend symlink\n")
    original_is_symlink = Path.is_symlink

    def fake_is_symlink(path):
        return path == index_md or original_is_symlink(path)

    with patch.object(Path, "is_symlink", fake_is_symlink):
        snapshot = workflow_smoke.snapshot_run_index_state(tmp, "story-flow")

    assert_not_ok(snapshot)
    assert_has_all(snapshot["issue"], "must not be a symbolic link")


def test_dotnet_smoke_fixtures_cover_upgrade_and_framework_signals(tmp):
    upgrade_rows = workflow_smoke.dotnet_upgrade_fixture_model_checks(tmp / "upgrade")
    framework_rows = workflow_smoke.dotnet_framework_migration_fixture_model_checks(tmp / "framework")

    assert_field_set(upgrade_rows, "ok", {True})
    assert_field_set(upgrade_rows, "kind", {"domain-fixture"})
    assert_field_set(
        upgrade_rows,
        "name",
        {"dotnet-upgrade-fixture-feed-policy", "dotnet-upgrade-fixture-package-owners"},
    )
    assert_field_set(framework_rows, "ok", {True})
    assert_field_set(framework_rows, "name", {"dotnet-framework-migration-fixture-legacy-signals"})


def test_navigation_smoke_fixture_installs_checks_and_detects_stale_maps(tmp):
    rows = smoke_domain.navigation_checks(REPO_ROOT, tmp / "navigation")

    assert_field_set(rows, "kind", {"domain-fixture"})
    assert_field_set(rows, "ok", {True})
    assert_field_set(
        rows,
        "name",
        {
            "navigation-install-fixture",
            "navigation-fresh-check",
            "navigation-project-context-draft",
            "navigation-stale-detection",
        },
    )


def test_run_index_treats_completed_with_findings_as_completed(tmp):
    write_fixture(tmp)
    run_dir = workflow_run_dir(tmp)
    packet = run_packet("story-flow")
    packet["status"] = "completed-with-findings"
    write_json(run_file(run_dir), packet)
    write_json(run_file(run_dir, "artifacts", "rejected-candidate.json"), {"ok": False, "status": "completed-with-findings"})

    files = sorted(path for path in run_dir.rglob("*") if path.is_file())
    assert index_workflow_runs.run_status(run_dir, files) == "completed"


def test_eval_supports_structured_v2_contract_assertions(tmp):
    write_fixture(tmp)
    suite = workflow_suite_path(tmp)
    write_json(
        suite,
        {
            "evals": [
                {
                    "id": "structured-contract",
                    "assertions": [
                        {"type": "contract_declares_related_module", "module": "demo-skill"},
                        {"type": "contract_declares_command", "command": "validate-automations --name story-flow"},
                        {"type": "contract_declares_command", "command": "workflow context --name story-flow"},
                        {"type": "contract_declares_output", "path": "runs/<run-id>/artifacts/context/context-packet.json"},
                        {"type": "contract_declares_phase", "phase": "execute"},
                        {"type": "contract_declares_worker_profile", "phase": "execute", "profile": "general-medium"},
                        {"type": "contract_local_ai_use_cases", "use_cases": []},
                        {"type": "file_contains", "path": "WORKFLOW.md", "text": "Example Prompts"},
                        {"type": "run_packet_valid", "run_id": "run-a"},
                    ],
                }
            ]
        },
    )
    status = index_workflow_runs.run(
        index_workflow_runs.Args(
            root=tmp,
            workflow_name="story-flow",
            write=True,
            check=False,
            output_format="json",
        )
    )
    assert status == 0
    report = eval_workflow.run_eval(
        eval_workflow.Args(root=tmp, workflow_name="story-flow", suite=suite, output_format="json")
    )
    assert_single_eval_passed(report)


def test_eval_command_assertions_render_typed_v3_argv(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    manifest = read_json(module_file(module_dir, "module.json"))
    normalized, adapter_errors, _adapter_warnings = (
        manifests.module_contract_v3.normalize_module_contract(manifest)
    )
    assert_empty(adapter_errors)
    context_argv = next(
        command["argv"]
        for command in normalized["commands"]
        if command["argv"][2:5] == [".agents/manage.py", "workflow", "context"]
    )
    normalized["commands"].append(
        {
            "id": "spaced-argument",
            "argv": ["tool", "arg with spaces"],
            "timeout_seconds": 300,
            "working_directory": "repository",
            "effects": [],
        }
    )
    write_json(module_file(module_dir, "module.json"), normalized)
    suite = workflow_suite_path(tmp)
    write_json(
        suite,
        {
            "evals": [
                {
                    "id": "typed-command-contract",
                    "assertions": [
                        {
                            "type": "contract_declares_command",
                            "argv": context_argv,
                        },
                        {
                            "type": "contract_contains",
                            "text": "arg with spaces",
                        },
                        {
                            "type": "contract_declares_command",
                            "argv": ["tool", "arg with spaces"],
                        },
                    ],
                }
            ]
        },
    )

    report = eval_workflow.run_eval(
        eval_workflow.Args(
            root=tmp,
            workflow_name="story-flow",
            suite=suite,
            output_format="json",
        )
    )

    assert_single_eval_passed(report)


def test_eval_lifecycle_smoke_replaces_retained_dogfood_runs(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    write_text(template_path(module_dir), f"# Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}")
    write_text(template_path(module_dir, "lean-plan.md"), f"# Lean Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}")
    write_guidance_savings_fixture(tmp)
    runs_dir = module_dir / "runs"
    index_markdown = runs_dir / "INDEX.md"
    index_json = runs_dir / "index.json"
    write_text(index_markdown, "# Pre-Smoke Index\n\n- exact fixture")
    write_json(index_json, {"schema_version": 1, "runs": [], "sentinel": "exact fixture"})
    index_markdown_before = index_markdown.read_bytes()
    index_json_before = index_json.read_bytes()
    suite = workflow_suite_path(tmp)
    write_json(
        suite,
        {
            "evals": [
                {
                    "id": "lifecycle-smoke",
                    "assertions": [
                        {"type": "workflow_lifecycle_smoke_ok"},
                    ],
                }
            ]
        },
    )

    report = eval_workflow.run_eval(
        eval_workflow.Args(root=tmp, workflow_name="story-flow", suite=suite, output_format="json")
    )

    assert_single_eval_passed(report)
    message = report["results"][0]["assertions"][0]["message"]
    assert_has_all(message, "workflow lifecycle smoke passed", "cleanup", "removed=True")
    rendered = eval_workflow.render_markdown(report)
    assert_has_all(rendered, "workflow lifecycle smoke passed", "cleanup", "removed=True")
    assert_empty(smoke_run_dirs(tmp, "story-flow"))
    assert index_markdown.read_bytes() == index_markdown_before
    assert index_json.read_bytes() == index_json_before


def test_eval_lifecycle_smoke_restores_absent_run_indexes(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    write_text(template_path(module_dir), f"# Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}")
    write_text(template_path(module_dir, "lean-plan.md"), f"# Lean Plan\n\n{OUT_OF_SCOPE_FIXTURE_SECTION}")
    write_guidance_savings_fixture(tmp)
    suite = workflow_suite_path(tmp)
    write_json(
        suite,
        {
            "evals": [
                {
                    "id": "lifecycle-smoke",
                    "assertions": [{"type": "workflow_lifecycle_smoke_ok"}],
                }
            ]
        },
    )

    report = eval_workflow.run_eval(
        eval_workflow.Args(root=tmp, workflow_name="story-flow", suite=suite, output_format="json")
    )

    assert_single_eval_passed(report)
    assert not (module_dir / "runs" / "INDEX.md").exists()
    assert not (module_dir / "runs" / "index.json").exists()


def test_context_packet_write_and_eval_assertion(tmp):
    module_dir = write_fixture(tmp)
    write_guidance_savings_fixture(tmp)
    with module_file(module_dir, "WORKFLOW.md").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "\n## Deterministic Context Baseline\n\n"
            + "Fixture context keeps this savings assertion away from the minimum-ratio boundary.\n" * 500
        )
    report = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)
    assert_tool(report, "workflow-manager.context-packet")
    assert_ok(report)
    estimates = report["token_estimates"]
    assert estimates["estimated_tokens_saved"] > 0
    assert estimates["packet_tokens_estimated"] >= estimates["compact_packet_tokens_estimated"]
    assert_status(report["context_budget"], "ok")
    guidance = report["guidance_savings"]
    assert_field(guidance, "status", "measurably-better")
    assert_field(guidance, "token_counter", "estimated_utf8_bytes_div_4")
    assert_true(guidance, "use_by_default")
    assert_true(guidance, "meets_minimum")
    assert guidance["saved_tokens_estimated"] > 0
    assert_field_set(report["context_budget"]["checks"], "ok", {True})
    execution = report["execution_profile"]
    assert_fields(
        execution,
        profile_id="general-medium",
        prompt_adapter="general",
        context_budget="standard",
        tool_policy="bounded-write",
        validation_gate="record-evidence",
    )
    assert_has_all(
        execution,
        "profile_id",
        "route_set",
        "endpoint_status",
        "prompt_overlay",
        "surface_adapter",
    )
    assert_lacks_all(
        execution,
        "declared_model_provider",
        "declared_model",
        "declared_deliberation_tier",
    )
    assert "model_target" not in execution
    assert_status(report["quality_gate"], "ok")
    assert_field_set(report["quality_gate"]["checks"], "ok", {True})
    run_dir = workflow_run_dir(tmp)
    assert run_file(run_dir, "artifacts", "context", "context-packet.json").exists()
    assert run_file(run_dir, "artifacts", "context", "context-packet.md").exists()
    context_markdown = run_file(run_dir, "artifacts", "context", "context-packet.md").read_text(encoding="utf-8")
    assert_has_all(
        context_markdown,
        "## Execution Profile",
        "deliberation=unspecified",
        "Endpoint status: unattested-active",
        "Semantic instruction header",
        "Prompt delivery overlay: `generic-v1`",
        "Default guidance status: measurably-better",
    )

    resume = workflow_run_support.resume_workflow_run(tmp, "story-flow", run_id="run-a")
    assert str(resume["context_handoff_path"]).endswith("artifacts/context/context-packet.json")
    assert_fields(resume["execution_profile"], profile_id="general-medium", prompt_adapter="general")
    assert_has_all(resume["handoff_prompt"], "execution_profile")
    check = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", check=True)
    assert_status(check["quality_gate"], "ok")
    compact = workflow_repo_manager.compact_context_run_report(check)
    assert_fields(
        compact,
        quality_gate_status="ok",
        quality_gate_failed_count=0,
        raw_tokens=check["token_estimates"]["raw_context_tokens_estimated"],
        packet_tokens=check["token_estimates"]["packet_tokens_estimated"],
        effective_load_tokens=check["token_estimates"]["effective_load_tokens_estimated"],
        compression_ratio=check["context_budget"]["packet_only_ratio"],
        effective_load_ratio=check["context_budget"]["effective_load_ratio"],
        savings_ratio=check["context_budget"]["savings_ratio"],
    )
    assert compact["raw_tokens"] > compact["effective_load_tokens"] == compact["packet_tokens"] > 0
    assert check["token_estimates"]["must_open_tokens_estimated"] == 0
    suite = workflow_suite_path(tmp)
    write_json(
        suite,
        {
            "evals": [
                {
                    "id": "context-packet",
                    "assertions": [
                        {"type": "run_context_packet_valid", "run_id": "run-a"},
                    ],
                }
            ]
        },
    )
    eval_report = eval_workflow.run_eval(
        eval_workflow.Args(root=tmp, workflow_name="story-flow", suite=suite, output_format="json")
    )
    assert_single_eval_passed(eval_report)


def test_runtime_observation_file_persists_and_drives_context_and_handoff(tmp):
    module_dir = write_fixture(tmp)
    write_guidance_savings_fixture(tmp)
    with module_file(module_dir, "WORKFLOW.md").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "\n## Runtime Observation Context Baseline\n\n"
            + "Durable runtime evidence stays behind a compact handle.\n" * 500
        )
    run_dir = run_dir_for(module_dir)
    observation_path = run_file(run_dir, "validation", "runtime-observation.json")
    write_json(
        observation_path,
        runtime_observation(capabilities=["model-selection", "deliberation-control"]),
    )

    observation = workflow_run_support.load_runtime_observation_packet(
        tmp,
        "story-flow",
        "run-a",
        common.relative(tmp, observation_path),
    )
    report = workflow_run_support.context_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
        write=True,
        runtime_observation=observation,
    )

    expected_path = "automations/story-flow/runs/run-a/validation/runtime-observation.json"
    assert_fields(
        report["execution_profile"],
        endpoint_status="attested-primary",
        capability_status="attested",
        effective_execution_mode="declared-endpoint",
        observed_model="gpt-5.5",
        observation_evidence_path=expected_path,
    )
    assert_field(report["execution_profile"]["prompt_overlay"], "id", "openai-5.5-v1")
    stored_run = read_json(run_file(run_dir, "run.json"))
    assert_fields(
        stored_run["runtime_observation"],
        workflow="story-flow",
        run_id="run-a",
        phase="execute",
        evidence_path=expected_path,
    )
    assert_field(stored_run["runtime_observation"]["model"], "model", "gpt-5.5")
    handoff = workflow_run_support.handoff_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
    )
    assert_fields(
        handoff["execution_profile"],
        observed_model="gpt-5.5",
        observation_evidence_path=expected_path,
    )
    assert_has_all(handoff["new_chat_prompt"], "instruction header", "surface_adapter")
    resume = workflow_run_support.resume_workflow_run(tmp, "story-flow", run_id="run-a")
    compact_resume = workflow_repo_manager.compact_resume_run_report(resume)
    assert_field(compact_resume["execution_profile"], "profile_id", "general-medium")
    assert_field(compact_resume["execution_profile"]["prompt_overlay"], "id", "openai-5.5-v1")
    assert_has_all(
        compact_resume["execution_profile"]["prompt_overlay"]["delivery_directive"],
        "expected outcome",
    )
    assert_field(compact_resume["output_budget"], "status", "within-budget")
    context_markdown = run_file(
        run_dir,
        "artifacts",
        "context",
        "context-packet.md",
    ).read_text(encoding="utf-8")
    assert_has_all(
        context_markdown,
        "Observed runtime: codex / openai gpt-5.5",
        "Surface adapter: `codex-v1`",
        expected_path,
    )

    observation_path.unlink()
    missing = workflow_run_support.context_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
        write=True,
    )
    assert_fields(
        missing["execution_profile"],
        endpoint_status="unattested-active",
        capability_status="unavailable",
        effective_execution_mode="serial-active-model",
    )
    assert_lacks_all(missing["execution_profile"], "observed_model")
    assert_field(missing["execution_profile"]["prompt_overlay"], "id", "generic-v1")
    assert_has_all(missing["execution_profile"]["fallback_reason"], "evidence file is missing")
    missing_handoff = workflow_run_support.handoff_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
    )
    assert_fields(
        missing_handoff["execution_profile"],
        endpoint_status="unattested-active",
        observed_model="",
    )

    mutated = json.loads(json.dumps(observation))
    mutated.pop("evidence_path", None)
    mutated["model"]["model"] = "gpt-5.6-sol"
    write_json(observation_path, mutated)
    mismatch = workflow_run_support.context_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
        write=True,
    )
    assert_fields(
        mismatch["execution_profile"],
        endpoint_status="unattested-active",
        effective_execution_mode="serial-active-model",
    )
    assert_lacks_all(mismatch["execution_profile"], "observed_model")
    assert_has_all(mismatch["execution_profile"]["fallback_reason"], "does not match the run record")


def test_runtime_observation_file_rejects_non_validation_and_unattested_packets(tmp):
    module_dir = write_fixture(tmp)
    run_dir = run_dir_for(module_dir)
    outside = run_file(run_dir, "runtime-observation.json")
    write_json(outside, {})
    try:
        workflow_run_support.load_runtime_observation_packet(
            tmp,
            "story-flow",
            "run-a",
            common.relative(tmp, outside),
        )
    except SystemExit as exc:
        assert_has_all(str(exc), "selected run's validation directory")
    else:
        raise AssertionError("expected observation outside validation to be rejected")

    invalid = run_file(run_dir, "validation", "runtime-observation.json")
    write_json(
        invalid,
        {
            "schema_version": 1,
            "tool": "workflow-manager.runtime-observation",
            "workflow": "story-flow",
            "run_id": "run-a",
            "phase": "execute",
            "host": {
                "attested": False,
                "source": "operator-input",
                "surface": "codex",
                "capabilities": [],
            },
        },
    )
    try:
        workflow_run_support.load_runtime_observation_packet(
            tmp,
            "story-flow",
            "run-a",
            common.relative(tmp, invalid),
        )
    except SystemExit as exc:
        assert_has_all(str(exc), "invalid runtime observation packet", "host.attested must be true")
    else:
        raise AssertionError("expected unattested observation to be rejected")


def test_runtime_observation_phase_transition_requires_fresh_attestation(tmp):
    module_dir = write_fixture(tmp)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["worker_profiles"]["phase_assignments"]["plan"] = "planning-high"
    write_json(module_file(module_dir, "module.json"), manifest)
    run_dir = run_dir_for(module_dir)
    observation_path = run_file(run_dir, "validation", "runtime-observation.json")
    write_json(
        observation_path,
        runtime_observation(capabilities=["model-selection", "deliberation-control"]),
    )
    observation = workflow_run_support.load_runtime_observation_packet(
        tmp,
        "story-flow",
        "run-a",
        common.relative(tmp, observation_path),
    )
    initial = workflow_run_support.context_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
        write=True,
        runtime_observation=observation,
    )
    assert_field(initial["execution_profile"], "endpoint_status", "attested-primary")

    stored_run = read_json(run_file(run_dir, "run.json"))
    stored_run["current_phase"] = "plan"
    write_json(run_file(run_dir, "run.json"), stored_run)

    transitioned = workflow_run_support.context_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
        write=True,
    )
    assert_fields(
        transitioned["execution_profile"],
        endpoint_status="unattested-active",
        capability_status="unavailable",
        effective_execution_mode="serial-active-model",
    )
    assert "observed_model" not in transitioned["execution_profile"], transitioned["execution_profile"]
    assert_field(transitioned["execution_profile"]["prompt_overlay"], "id", "generic-v1")
    assert_has_all(
        transitioned["execution_profile"]["fallback_reason"],
        "trusted runtime observation is unavailable",
    )
    transitioned_run = read_json(run_file(run_dir, "run.json"))
    assert "runtime_observation" not in transitioned_run, transitioned_run
    assert observation_path.is_file()
    assert_field(read_json(observation_path), "phase", "execute")

    transitioned_run["current_phase"] = "execute"
    write_json(run_file(run_dir, "run.json"), transitioned_run)
    returned = workflow_run_support.context_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
        write=True,
    )
    assert_fields(
        returned["execution_profile"],
        endpoint_status="unattested-active",
        capability_status="unavailable",
        effective_execution_mode="serial-active-model",
    )
    assert_field(returned["execution_profile"]["prompt_overlay"], "id", "generic-v1")
    assert "runtime_observation" not in read_json(run_file(run_dir, "run.json"))

    reattested = workflow_run_support.context_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
        write=True,
        runtime_observation=observation,
    )
    assert_field(reattested["execution_profile"], "endpoint_status", "attested-primary")
    handoff_transition_run = read_json(run_file(run_dir, "run.json"))
    handoff_transition_run["current_phase"] = "plan"
    write_json(run_file(run_dir, "run.json"), handoff_transition_run)
    transitioned_handoff = workflow_run_support.handoff_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
        write=True,
    )
    assert_fields(
        transitioned_handoff["execution_profile"],
        endpoint_status="unattested-active",
        observed_model="",
    )
    after_handoff = read_json(run_file(run_dir, "run.json"))
    assert "runtime_observation" not in after_handoff, after_handoff
    assert observation_path.is_file()

    after_handoff["current_phase"] = "execute"
    write_json(run_file(run_dir, "run.json"), after_handoff)
    handoff = workflow_run_support.handoff_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
    )
    assert_fields(
        handoff["execution_profile"],
        endpoint_status="unattested-active",
        observed_model="",
    )


def test_context_packet_accounting_matches_exact_persisted_bytes(tmp):
    module_dir = write_fixture(tmp)
    write_guidance_savings_fixture(tmp)
    with module_file(module_dir, "WORKFLOW.md").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(
            "\n## Persisted Packet Accounting Baseline\n\n"
            + "Persisted context evidence stays handle-only during resume.\n" * 500
        )

    workflow_run_support.context_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
        write=True,
    )

    packet_path = run_file(
        run_dir_for(module_dir),
        "artifacts",
        "context",
        "context-packet.json",
    )
    persisted_text = packet_path.read_text(encoding="utf-8")
    stored = json.loads(persisted_text)
    assert persisted_text == workflow_context_budget.serialize_context_packet(stored)
    persisted_tokens = workflow_context_budget.approx_tokens(persisted_text)
    estimates = stored["token_estimates"]
    assert estimates["packet_tokens_estimated"] == persisted_tokens
    effective_load = persisted_tokens + estimates["must_open_tokens_estimated"]
    assert estimates["effective_load_tokens_estimated"] == effective_load
    assert stored["context_budget"]["effective_load_tokens_estimated"] == effective_load
    packet_check = next(
        check
        for check in stored["context_budget"]["checks"]
        if check["name"] == "packet-token-limit"
    )
    assert packet_check["actual"] == persisted_tokens


def test_context_packet_v3_matches_owner_generated_strict_schema(tmp):
    module_dir = write_fixture(tmp)
    write_guidance_savings_fixture(tmp)

    workflow_run_support.context_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
        write=True,
    )
    stored = read_json(
        run_file(
            run_dir_for(module_dir),
            "artifacts",
            "context",
            "context-packet.json",
        )
    )

    assert_field(stored, "schema_version", 3)
    assert "instructions" not in stored["execution_profile"]["prompt_overlay"]
    assert "observed_model" not in stored["execution_profile"]
    schema = workflow_context_packet.context_packet_schema()
    assert schema["additionalProperties"] is False
    assert_empty(workflow_context_packet.validate_context_packet(stored, schema=schema))

    unexpected = dict(stored)
    unexpected["undeclared_packet_field"] = True
    assert_contains(
        workflow_context_packet.validate_context_packet(unexpected, schema=schema),
        "undeclared_packet_field",
    )


def test_context_packet_schema_rejects_schema_v2(_tmp):
    schema = workflow_context_packet.context_packet_schema()
    assert_field(schema["properties"]["schema_version"], "const", 3)
    assert_contains(
        workflow_context_packet.validate_context_packet({"schema_version": 2}),
        "schema_version must equal 3",
    )


def test_runtime_observation_committed_input_schema_and_fixture_align_with_loader(tmp):
    assets = REPO_ROOT / ".agents" / "skills" / "workflow-manager" / "assets"
    schema = read_json(assets / "schemas" / "runtime-observation-input-v1.schema.json")
    fixture = read_json(assets / "fixtures" / "runtime-observation-v1.json")
    assert_empty(workflow_context_packet.validate_context_packet(fixture, schema=schema))
    assert_field(schema["properties"]["schema_version"], "const", 1)
    assert schema["additionalProperties"] is False
    assert "host" not in schema["required"]
    assert "model" not in schema["required"]
    assert len(schema["anyOf"]) == 2
    assert "context-inheritance-control" in schema["$defs"]["capability"]["enum"]
    assert "context-inheritance-control" in fixture["host"]["capabilities"]
    assert "context-inheritance-control" in workflow_workers.CAPABILITY_IDS
    assert "context-inheritance-control" in workflow_workers.PROVIDER_RESPONSE_FORBIDDEN_CAPABILITIES
    without_axes = {key: value for key, value in fixture.items() if key not in {"host", "model"}}
    assert_contains(
        workflow_context_packet.validate_context_packet(without_axes, schema=schema),
        "is required",
    )
    no_capabilities = json.loads(json.dumps(fixture))
    no_capabilities["host"].pop("capabilities")
    assert_empty(workflow_context_packet.validate_context_packet(no_capabilities, schema=schema))
    no_capabilities["evidence_path"] = "automations/story-flow/runs/run-a/validation/runtime-observation.json"
    assert_empty(
        workflow_workers.runtime_observation_issues(
            no_capabilities,
            expected_workflow="story-flow",
            expected_run_id="run-a",
            expected_phase="execute",
        )
    )
    for obsolete_version in (2, 3, True, 1.0, "1", None):
        obsolete = json.loads(json.dumps(no_capabilities))
        obsolete["schema_version"] = obsolete_version
        schema_issues = workflow_context_packet.validate_context_packet(obsolete, schema=schema)
        runtime_issues = workflow_workers.runtime_observation_issues(
            obsolete,
            expected_workflow="story-flow",
            expected_run_id="run-a",
            expected_phase="execute",
        )
        assert any("schema_version" in issue for issue in schema_issues), obsolete_version
        assert any("schema_version" in issue for issue in runtime_issues), obsolete_version
    provider_conflict = json.loads(json.dumps(fixture))
    provider_conflict["host"]["surface"] = "openai-responses-api"
    provider_conflict["host"]["source"] = "provider-response"
    provider_conflict["host"]["capabilities"] = []
    provider_conflict["model"]["source"] = "provider-response"
    provider_conflict["model"]["provider"] = "anthropic"
    assert_contains(
        workflow_context_packet.validate_context_packet(provider_conflict, schema=schema),
        "must equal 'openai'",
    )
    provider_conflict["evidence_path"] = "automations/story-flow/runs/run-a/validation/runtime-observation.json"
    assert_contains(
        workflow_workers.runtime_observation_issues(
            provider_conflict,
            expected_workflow="story-flow",
            expected_run_id="run-a",
            expected_phase="execute",
        ),
        "requires model provider openai",
    )

    module_dir = write_fixture(tmp)
    observation_path = run_file(
        run_dir_for(module_dir),
        "validation",
        "runtime-observation.json",
    )
    write_json(observation_path, fixture)
    loaded = workflow_run_support.load_runtime_observation_packet(
        tmp,
        "story-flow",
        "run-a",
        common.relative(tmp, observation_path),
    )
    assert_fields(loaded, schema_version=1, workflow="story-flow", run_id="run-a", phase="execute")
    assert_fields(loaded["host"], surface="codex", source="host-runtime")
    assert_fields(
        loaded["model"],
        provider="openai",
        model="gpt-5.6-sol",
        observed_deliberation="high",
    )
    resolved_fixture = workflow_workers.resolve_model_delivery(
        workflow_workers.surface_route_sets()["review-high"],
        loaded,
        expected_workflow="story-flow",
        expected_run_id="run-a",
        expected_phase="execute",
    )
    assert_fields(resolved_fixture, endpoint_status="attested-primary")
    assert_fields(resolved_fixture["prompt_overlay"], id="openai-5.6-v1")
    blank = json.loads(json.dumps(fixture))
    blank["model"]["observed_deliberation"] = 42
    blank["evidence_path"] = common.relative(tmp, observation_path)
    assert_contains(
        workflow_workers.runtime_observation_issues(
            blank,
            expected_workflow="story-flow",
            expected_run_id="run-a",
            expected_phase="execute",
        ),
        "observed_deliberation must be a string",
    )
    assert_contains(
        workflow_context_packet.validate_context_packet(blank, schema=schema),
        "must be a string",
    )
    empty_model = json.loads(json.dumps(fixture))
    empty_model["model"]["model"] = ""
    empty_model["evidence_path"] = common.relative(tmp, observation_path)
    assert_contains(
        workflow_context_packet.validate_context_packet(empty_model, schema=schema),
        "must contain at least 1 characters",
    )
    duplicate = json.loads(json.dumps(fixture))
    duplicate["host"]["capabilities"] = ["model-selection", "model-selection"]
    duplicate["evidence_path"] = common.relative(tmp, observation_path)
    assert_contains(
        workflow_context_packet.validate_context_packet(duplicate, schema=schema),
        "must contain unique items",
    )
    assert_contains(
        workflow_workers.runtime_observation_issues(
            duplicate,
            expected_workflow="story-flow",
            expected_run_id="run-a",
            expected_phase="execute",
        ),
        "must contain unique values",
    )
    padded = json.loads(json.dumps(fixture))
    padded["host"]["capabilities"] = [" model-selection"]
    padded["evidence_path"] = common.relative(tmp, observation_path)
    assert_contains(
        workflow_context_packet.validate_context_packet(padded, schema=schema),
        "must be one of the declared values",
    )
    assert_contains(
        workflow_workers.runtime_observation_issues(
            padded,
            expected_workflow="story-flow",
            expected_run_id="run-a",
            expected_phase="execute",
        ),
        "unsupported capabilities",
    )
    padded_source = json.loads(json.dumps(fixture))
    padded_source["host"]["source"] = " host-runtime "
    padded_source["evidence_path"] = common.relative(tmp, observation_path)
    assert_contains(
        workflow_context_packet.validate_context_packet(padded_source, schema=schema),
        "must be one of the declared values",
    )
    assert_contains(
        workflow_workers.runtime_observation_issues(
            padded_source,
            expected_workflow="story-flow",
            expected_run_id="run-a",
            expected_phase="execute",
        ),
        "source must be one of",
    )
    overlong_provider = json.loads(json.dumps(fixture))
    overlong_provider["model"]["provider"] = "x" * 81
    overlong_provider["evidence_path"] = common.relative(tmp, observation_path)
    assert_contains(
        workflow_context_packet.validate_context_packet(overlong_provider, schema=schema),
        "must be one of the declared values",
    )
    assert_contains(
        workflow_workers.runtime_observation_issues(
            overlong_provider,
            expected_workflow="story-flow",
            expected_run_id="run-a",
            expected_phase="execute",
        ),
        "model.provider must be one of",
    )
    normalized_scope = dict(fixture)
    normalized_scope.update(
        workflow=" story-flow ",
        run_id=" run-a ",
        phase=" execute ",
        evidence_path=common.relative(tmp, observation_path),
    )
    assert_empty(workflow_context_packet.validate_context_packet(normalized_scope, schema=schema))
    assert_empty(
        workflow_workers.runtime_observation_issues(
            normalized_scope,
            expected_workflow="story-flow",
            expected_run_id="run-a",
            expected_phase="execute",
        )
    )


def test_context_packet_v3_rejects_empty_required_nested_shapes(tmp):
    module_dir = write_fixture(tmp)
    write_guidance_savings_fixture(tmp)
    workflow_run_support.context_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
        write=True,
    )
    stored = read_json(
        run_file(
            run_dir_for(module_dir),
            "artifacts",
            "context",
            "context-packet.json",
        )
    )
    schema = workflow_context_packet.context_packet_schema()

    for field in (
        "execution_profile",
        "instruction_context",
        "scope",
        "work_item_summary",
        "documentation_delta",
        "validation_summary",
        "guidance_savings",
        "token_estimates",
        "context_budget",
        "context_packet_paths",
    ):
        malformed = dict(stored)
        malformed[field] = {}
        assert_contains(
            workflow_context_packet.validate_context_packet(malformed, schema=schema),
            f"context-packet.{field}.",
        )


def test_context_packet_v3_requires_effective_load_nested_fields(tmp):
    module_dir = write_fixture(tmp)
    write_guidance_savings_fixture(tmp)
    workflow_run_support.context_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
        write=True,
    )
    stored = read_json(
        run_file(
            run_dir_for(module_dir),
            "artifacts",
            "context",
            "context-packet.json",
        )
    )
    schema = workflow_context_packet.context_packet_schema()

    for parent, field in (
        ("execution_profile", "profile_id"),
        ("execution_profile", "route_set"),
        ("execution_profile", "prompt_adapter"),
        ("execution_profile", "expected_output"),
        ("execution_profile", "validation_gate"),
        ("execution_profile", "instruction_header"),
        ("execution_profile", "endpoint_status"),
        ("execution_profile", "capability_status"),
        ("execution_profile", "effective_execution_mode"),
        ("execution_profile", "fallback_reason"),
        ("execution_profile", "prompt_overlay"),
        ("execution_profile", "context_budget_ref"),
        ("execution_profile", "budget_tokens"),
        ("execution_profile", "effective_context_tokens"),
        ("execution_profile", "remaining_margin_tokens"),
        ("execution_profile", "within_budget"),
        ("token_estimates", "raw_context_file_count"),
        ("token_estimates", "validation_tokens_estimated"),
        ("token_estimates", "must_open_file_count"),
        ("token_estimates", "must_open_tokens_estimated"),
        ("token_estimates", "effective_load_tokens_estimated"),
        ("context_budget", "status"),
        ("context_budget", "effective_load_tokens_estimated"),
        ("context_budget", "effective_load_limit"),
        ("context_budget", "checks"),
        ("context_packet_paths", "json"),
        ("context_packet_paths", "markdown"),
    ):
        malformed = json.loads(json.dumps(stored))
        del malformed[parent][field]
        assert_contains(
            workflow_context_packet.validate_context_packet(malformed, schema=schema),
            f"context-packet.{parent}.{field} is required.",
        )
    missing_available_mode = json.loads(json.dumps(stored))
    del missing_available_mode["execution_profile"]["surface_adapter"][
        "available_orchestration_mode"
    ]
    assert_contains(
        workflow_context_packet.validate_context_packet(missing_available_mode, schema=schema),
        "context-packet.execution_profile.surface_adapter.available_orchestration_mode is required.",
    )


def declared_context_spec(*sources, budgets=None):
    return {
        "budgets": dict(budgets or {"critical": 2_000}),
        "sources": list(sources),
    }


def context_source(
    source_id,
    path,
    *,
    role="custom-state",
    load_policy="must_open",
    category="resume-critical",
    budget_ref="critical",
    preserve_coordinates=True,
):
    return {
        "id": source_id,
        "artifact_role": role,
        "path": path,
        "load_policy": load_policy,
        "critical_category": category,
        "budget_ref": budget_ref,
        "preserve_coordinates": preserve_coordinates,
    }


def test_context_packet_loads_custom_declared_source_without_core_branch(tmp):
    module_dir = write_fixture(tmp)
    write_guidance_savings_fixture(tmp)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["context"] = declared_context_spec(
        context_source(
            "custom-state",
            "automations/story-flow/runs/<run-id>/custom-state.md",
        )
    )
    write_json(module_file(module_dir, "module.json"), manifest)
    custom_path = run_file(run_dir_for(module_dir), "custom-state.md")
    write_text(custom_path, "# Custom State\n\nResume CUSTOM-771 at port 4317.")

    report = workflow_run_support.context_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
        write=True,
    )

    custom_rel = "automations/story-flow/runs/run-a/custom-state.md"
    assert_has_all(report["required_next_context"], custom_rel)
    assert len(report["context_sources"]) == 1
    source = report["context_sources"][0]
    assert_fields(
        source,
        id="custom-state",
        artifact_role="custom-state",
        load_policy="must_open",
        critical_category="resume-critical",
        budget_ref="critical",
    )
    assert_field(source["files"][0], "path", custom_rel)


def test_context_glob_rejects_resolved_file_escape(tmp):
    module_dir = write_fixture(tmp)
    write_guidance_savings_fixture(tmp)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["context"] = {
        "budgets": {"critical": 2_000},
        "sources": [
            {
                **context_source(
                    "globbed-state",
                    "unused.md",
                ),
                "pattern": "automations/story-flow/runs/<run-id>/*.escape.md",
            }
        ],
    }
    manifest["context"]["sources"][0].pop("path")
    write_json(module_file(module_dir, "module.json"), manifest)
    escaped = run_file(run_dir_for(module_dir), "state.escape.md")
    write_text(escaped, "# Escaped Context")
    outside = Path(tmp.parent) / "outside-context.md"
    original_resolve = Path.resolve
    outside_resolved = original_resolve(outside, strict=False)

    def resolve_with_escape(path, strict=False):
        if path == escaped:
            return outside_resolved
        return original_resolve(path, strict=strict)

    with patch.object(Path, "resolve", resolve_with_escape):
        report = workflow_run_support.context_workflow_run(
            tmp,
            "story-flow",
            run_id="run-a",
            write=True,
        )

    escaped_rel = "automations/story-flow/runs/run-a/state.escape.md"
    assert_not_ok(report)
    assert escaped_rel not in report["required_next_context"]
    assert_contains(report["issues"], "source pattern matched unsafe path")


def test_context_scope_prefers_actual_must_open_scope_source_over_template(tmp):
    module_dir = write_fixture(tmp)
    write_guidance_savings_fixture(tmp)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["context"] = declared_context_spec(
        context_source(
            "ticket-template",
            "automations/story-flow/templates/ticket-info.md",
            role="user-story",
            load_policy="handle_only",
            category="scope-required",
            preserve_coordinates=False,
        ),
        context_source(
            "ticket-info",
            "automations/story-flow/runs/<run-id>/ticket-info.md",
            role="user-story",
            category="scope-required",
        ),
    )
    write_json(module_file(module_dir, "module.json"), manifest)
    write_text(
        template_path(module_dir, "ticket-info.md"),
        "# Ticket Template\n\n## Scope\n\n- In scope: template placeholder",
    )
    write_text(
        run_file(run_dir_for(module_dir), "ticket-info.md"),
        "# Ticket\n\n## Scope\n\n- In scope: declared runtime scope\n- Assumptions: exact fixture\n\n"
        "## Out Of Scope\n\n- unrelated service",
    )

    report = workflow_run_support.context_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
        write=True,
    )

    assert_field(report["scope"], "ticket_scope_recorded", True)
    assert_has_all(report["scope"]["in_scope"], "declared runtime scope")
    assert_has_all(report["scope"]["assumptions"], "exact fixture")
    assert_has_all(report["scope"]["out_of_scope"], "unrelated service")
    assert_lacks(report["scope"]["in_scope"], "template placeholder")


def test_story_and_bug_context_packets_extract_declared_actual_ticket_scope(tmp):
    write_guidance_savings_fixture(tmp)
    for workflow_name, expected_scope in (
        ("user-story-workflow", "story runtime scope"),
        ("bug-ticket-workflow", "bug runtime scope"),
    ):
        module_dir = write_fixture(tmp, workflow_name)
        write_text(
            run_file(run_dir_for(module_dir), "ticket-info.md"),
            "# Ticket\n\n## Scope\n\n"
            f"- In scope: {expected_scope}\n\n"
            "## Out Of Scope\n\n- unrelated fixture\n",
        )

        report = workflow_run_support.context_workflow_run(
            tmp,
            workflow_name,
            run_id="run-a",
            write=True,
        )

        assert_field(report["scope"], "ticket_scope_recorded", True)
        assert_has_all(report["scope"]["in_scope"], expected_scope)
        assert_has_all(report["scope"]["out_of_scope"], "unrelated fixture")


def test_context_packet_effective_load_counts_full_oversized_must_open_plan(tmp):
    module_dir = write_fixture(tmp)
    write_guidance_savings_fixture(tmp)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["context"] = declared_context_spec(
        context_source(
            "actual-plan",
            "automations/story-flow/runs/<run-id>/plan.md",
            role="implementation-plan",
            budget_ref="plan",
            preserve_coordinates=False,
        ),
        budgets={"plan": 100},
    )
    write_json(module_file(module_dir, "module.json"), manifest)
    plan_path = run_file(run_dir_for(module_dir), "plan.md")
    write_text(plan_path, "# Plan\n\n" + ("full plan evidence " * 10_000))

    report = workflow_run_support.context_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
        write=True,
    )

    estimates = report["token_estimates"]
    assert estimates["packet_tokens_estimated"] <= 2_500
    assert estimates["must_open_tokens_estimated"] > 100
    assert estimates["effective_load_tokens_estimated"] == (
        estimates["packet_tokens_estimated"] + estimates["must_open_tokens_estimated"]
    )
    assert_status(report["context_budget"], "needs-attention")
    assert_fields(
        report["execution_profile"],
        context_budget_ref="implementation",
        budget_tokens=8000,
        effective_context_tokens=estimates["effective_load_tokens_estimated"],
        within_budget=False,
    )
    assert report["execution_profile"]["remaining_margin_tokens"] < 0
    failed_names = {
        check["name"]
        for check in report["context_budget"]["checks"]
        if check.get("ok") is not True
    }
    assert_has_all(
        failed_names,
        "must-open-budget:plan",
        "effective-load-limit",
        "phase-budget-limit",
    )


def test_context_packet_runtime_enforces_phase_budget_independently(tmp):
    module_dir = write_fixture(tmp)
    write_guidance_savings_fixture(tmp)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["context"] = declared_context_spec(
        context_source(
            "actual-plan",
            "automations/story-flow/runs/<run-id>/plan.md",
            role="implementation-plan",
            budget_ref="plan",
            preserve_coordinates=False,
        ),
        context_source(
            "large-reference",
            "automations/story-flow/runs/<run-id>/large-reference.md",
            role="reference-evidence",
            load_policy="handle_only",
            budget_ref="plan",
            preserve_coordinates=False,
        ),
        budgets={"plan": 20_000},
    )
    write_json(module_file(module_dir, "module.json"), manifest)
    run_dir = run_dir_for(module_dir)
    write_text(run_file(run_dir, "plan.md"), "# Plan\n\n" + ("p" * 34_000))
    write_text(
        run_file(run_dir, "large-reference.md"),
        "# Reference\n\n" + ("h" * 100_000),
    )

    report = workflow_run_support.context_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
        write=True,
    )

    failed_budget_checks = {
        check["name"]
        for check in report["context_budget"]["checks"]
        if check.get("ok") is not True
    }
    assert failed_budget_checks == {"phase-budget-limit"}
    assert_fields(
        report["execution_profile"],
        budget_tokens=8_000,
        budget_source="configured",
        within_budget=False,
    )
    assert report["execution_profile"]["remaining_margin_tokens"] < 0
    assert_status(report["context_budget"], "needs-attention")
    assert_not_ok(report)


def test_context_budget_charges_overlapping_must_open_path_to_each_budget(_tmp):
    shared_file = {
        "path": "automations/story-flow/runs/run-a/shared.md",
        "exists": True,
        "bytes": 600,
        "chars": 600,
        "tokens_estimated": 150,
    }
    sources = [
        {
            "load_policy": "must_open",
            "budget_ref": budget_ref,
            "files": [dict(shared_file)],
        }
        for budget_ref in ("large", "small")
    ]
    budgets = {"large": 200, "small": 100}

    for declared in (sources, list(reversed(sources))):
        files, usage = workflow_context_budget.must_open_budget_rows(
            declared,
            budgets,
        )
        usage_by_ref = {row["budget_ref"]: row for row in usage}

        assert len(files) == 1
        assert_field(usage_by_ref["large"], "actual", 150)
        assert_field(usage_by_ref["large"], "file_count", 1)
        assert_field(usage_by_ref["small"], "actual", 150)
        assert_field(usage_by_ref["small"], "file_count", 1)
        status = workflow_context_budget.context_budget_status(
            raw_tokens=500,
            packet_tokens=50,
            must_open_tokens=150,
            must_open_budget_usage=usage,
        )
        small_check = next(
            row
            for row in status["checks"]
            if row["name"] == "must-open-budget:small"
        )
        assert_field(small_check, "ok", False)


def test_context_budget_counts_must_open_files_with_missing_or_invalid_budget_ref(_tmp):
    large_file = {
        "path": "automations/story-flow/runs/run-a/plan.md",
        "exists": True,
        "bytes": 200_000,
        "chars": 200_000,
        "tokens_estimated": 50_000,
    }

    for budget_ref, budgets, expected_label in (
        ("", {"declared": 60_000}, "missing"),
        ("unknown", {"declared": 60_000}, "unknown"),
        ("declared", {"declared": 0}, "declared"),
    ):
        files, usage = workflow_context_budget.must_open_budget_rows(
            [
                {
                    "load_policy": "must_open",
                    "budget_ref": budget_ref,
                    "files": [dict(large_file)],
                }
            ],
            budgets,
        )

        assert len(files) == 1
        assert_field(files[0], "tokens_estimated", 50_000)
        assert len(usage) == 1
        assert_field(usage[0], "actual", 50_000)
        assert_field(usage[0], "file_count", 1)
        assert_field(usage[0], "valid", False)
        assert "budget_ref" in usage[0]["issue"]

        status = workflow_context_budget.context_budget_status(
            raw_tokens=55_000,
            packet_tokens=500,
            must_open_tokens=50_000,
            must_open_budget_usage=usage,
        )
        failed = [row for row in status["checks"] if row.get("ok") is not True]
        assert_has_all(
            {row["name"] for row in failed},
            f"must-open-budget:{expected_label}",
        )
        assert any("budget_ref" in issue for issue in status["issues"])


def test_context_packet_fails_closed_and_counts_invalid_budget_ref_without_validation(tmp):
    module_dir = write_fixture(tmp)
    write_guidance_savings_fixture(tmp)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["context"] = {
        "budgets": {"declared": 60_000},
        "sources": [
            context_source(
                "actual-plan",
                "automations/story-flow/runs/<run-id>/plan.md",
                role="implementation-plan",
                budget_ref="unknown",
                preserve_coordinates=False,
            )
        ],
    }
    write_json(module_file(module_dir, "module.json"), manifest)
    write_text(
        run_file(run_dir_for(module_dir), "plan.md"),
        "# Plan\n\n" + ("runtime evidence " * 12_500),
    )

    report = workflow_run_support.context_workflow_run(
        tmp,
        "story-flow",
        run_id="run-a",
        write=False,
    )

    estimates = report["token_estimates"]
    assert estimates["must_open_tokens_estimated"] > 40_000
    assert estimates["effective_load_tokens_estimated"] == (
        estimates["packet_tokens_estimated"] + estimates["must_open_tokens_estimated"]
    )
    assert_not_ok(report)
    assert_status(report, "needs-attention")
    assert_status(report["context_budget"], "needs-attention")
    assert_contains(report["issues"], "budget_ref")
    invalid_check = next(
        row
        for row in report["context_budget"]["checks"]
        if row["name"] == "must-open-budget:unknown"
    )
    assert_field(invalid_check, "ok", False)


def test_context_budget_savings_uses_effective_load_not_packet_only(_tmp):
    no_savings = workflow_context_budget.context_budget_status(
        raw_tokens=5_000,
        packet_tokens=500,
        must_open_tokens=4_500,
    )
    negative_savings = workflow_context_budget.context_budget_status(
        raw_tokens=5_000,
        packet_tokens=500,
        must_open_tokens=5_000,
    )

    assert_fields(
        no_savings,
        status="needs-attention",
        packet_only_ratio=0.1,
        effective_load_ratio=1.0,
        savings_ratio=0.0,
    )
    assert_fields(
        negative_savings,
        status="needs-attention",
        packet_only_ratio=0.1,
        effective_load_ratio=1.1,
        savings_ratio=-0.1,
    )
    for status in (no_savings, negative_savings):
        savings_check = next(
            row
            for row in status["checks"]
            if row["name"] == "minimum-savings-ratio"
        )
        assert_field(savings_check, "ok", False)


def test_context_budget_savings_thresholds_come_from_project_policy(_tmp):
    original = workflow_context_budget.common.project_policy_int
    configured = {
        "limits.workflow.context_packet_token_limit": 2500,
        "limits.workflow.context_packet_min_savings_percent": 40,
        "limits.workflow.context_packet_min_savings_raw_tokens": 6000,
    }
    workflow_context_budget.common.project_policy_int = configured.__getitem__
    try:
        below_activation = workflow_context_budget.context_budget_status(
            raw_tokens=5000,
            packet_tokens=4500,
        )
        above_activation = workflow_context_budget.context_budget_status(
            raw_tokens=6000,
            packet_tokens=3500,
        )
    finally:
        workflow_context_budget.common.project_policy_int = original

    below_check = next(
        row for row in below_activation["checks"] if row["name"] == "minimum-savings-ratio"
    )
    above_check = next(
        row for row in above_activation["checks"] if row["name"] == "minimum-savings-ratio"
    )
    assert_fields(below_check, applies=False, minimum=0.4, minimum_raw_tokens=6000, ok=True)
    assert_fields(above_check, applies=True, minimum=0.4, minimum_raw_tokens=6000, ok=True)


def test_context_budget_enforces_measured_phase_budget_boundaries(_tmp):
    for effective_load, expected_ok, expected_margin in (
        (7_999, True, 1),
        (8_000, True, 0),
        (8_001, False, -1),
    ):
        status = workflow_context_budget.context_budget_status(
            raw_tokens=30_000,
            packet_tokens=500,
            must_open_tokens=effective_load - 500,
            must_open_budget_usage=[
                {
                    "budget_ref": "run-critical",
                    "check_label": "run-critical",
                    "valid": True,
                    "issue": "",
                    "limit": 10_000,
                    "actual": effective_load - 500,
                    "file_count": 1,
                }
            ],
            phase_budget_tokens=8_000,
        )
        phase_check = next(
            check
            for check in status["checks"]
            if check["name"] == "phase-budget-limit"
        )
        assert_fields(
            phase_check,
            ok=expected_ok,
            applies=True,
            limit=8_000,
            actual=effective_load,
            remaining_margin_tokens=expected_margin,
        )
        assert (status["status"] == "ok") is expected_ok

    unmeasured = workflow_context_budget.context_budget_status(
        raw_tokens=30_000,
        packet_tokens=500,
        must_open_tokens=7_501,
        must_open_budget_usage=[
            {
                "budget_ref": "run-critical",
                "check_label": "run-critical",
                "valid": True,
                "issue": "",
                "limit": 10_000,
                "actual": 7_501,
                "file_count": 1,
            }
        ],
    )
    phase_check = next(
        check
        for check in unmeasured["checks"]
        if check["name"] == "phase-budget-limit"
    )
    assert_fields(phase_check, ok=True, applies=False)


def test_context_packet_accounting_convergence_is_explicit_and_conservative(_tmp):
    converged_packet = {
        "ok": True,
        "status": "ok",
        "issues": [],
        "execution_profile": {"budget_tokens": 20_000},
        "filler": "",
    }
    workflow_context_budget.apply_token_estimates(
        converged_packet,
        raw_tokens=20_000,
        validation_tokens=0,
        compact_packet_tokens=100,
        raw_estimates=[],
        context_sources=[],
        context_budgets={},
    )
    converged_check = next(
        row
        for row in converged_packet["context_budget"]["checks"]
        if row["name"] == "context-accounting-converged"
    )
    assert_field(converged_check, "ok", True)
    assert converged_packet["token_estimates"]["packet_tokens_estimated"] == (
        workflow_context_budget.approx_tokens(
            workflow_context_budget.serialize_context_packet(converged_packet)
        )
    )

    reviewer_packet = {
        "ok": True,
        "status": "ok",
        "issues": [],
        "execution_profile": {"budget_tokens": 48_199},
        "filler": "y" * 9_661,
    }
    reviewer_sources = [
        {
            "load_policy": "must_open",
            "budget_ref": "b",
            "files": [
                {
                    "path": "x" * 173,
                    "tokens_estimated": 151_082,
                }
            ],
        }
    ]
    workflow_context_budget.apply_token_estimates(
        reviewer_packet,
        raw_tokens=180_466,
        validation_tokens=0,
        compact_packet_tokens=4_930,
        raw_estimates=[],
        context_sources=reviewer_sources,
        context_budgets={"b": 58_615},
    )
    reviewer_check = next(
        row
        for row in reviewer_packet["context_budget"]["checks"]
        if row["name"] == "context-accounting-converged"
    )
    assert_field(reviewer_check, "ok", True)
    assert reviewer_packet["token_estimates"]["packet_tokens_estimated"] == (
        workflow_context_budget.approx_tokens(
            workflow_context_budget.serialize_context_packet(reviewer_packet)
        )
    )

    cycle_packet = {
        "ok": True,
        "status": "ok",
        "issues": [],
        "execution_profile": {"budget_tokens": 20_000},
    }
    original_approx_tokens = workflow_context_budget.approx_tokens

    def oscillating_approx_tokens(text):
        payload = json.loads(text)
        stored = payload["token_estimates"]["packet_tokens_estimated"]
        return 101 if stored != 101 else 100

    workflow_context_budget.approx_tokens = oscillating_approx_tokens
    try:
        workflow_context_budget.apply_token_estimates(
            cycle_packet,
            raw_tokens=20_000,
            validation_tokens=0,
            compact_packet_tokens=100,
            raw_estimates=[],
            context_sources=[],
            context_budgets={},
        )
        serialized = oscillating_approx_tokens(
            workflow_context_budget.serialize_context_packet(cycle_packet)
        )
    finally:
        workflow_context_budget.approx_tokens = original_approx_tokens
    cycle_check = next(
        row
        for row in cycle_packet["context_budget"]["checks"]
        if row["name"] == "context-accounting-converged"
    )
    stored = cycle_packet["token_estimates"]["packet_tokens_estimated"]

    assert_field(cycle_check, "ok", False)
    assert_not_ok(cycle_packet)
    assert_status(cycle_packet, "needs-attention")
    assert stored >= serialized
    assert cycle_packet["token_estimates"]["effective_load_tokens_estimated"] == stored
    assert_contains(cycle_packet["issues"], "context-accounting-converged")


def test_context_packet_preserves_exact_identifiers_for_resume(tmp):
    write_fixture(tmp)
    write_guidance_savings_fixture(tmp)
    run_dir = workflow_run_dir(tmp)
    commit = "1234567890abcdef1234567890abcdef12345678"
    write_text(tmp / "docs" / "workflow" / "workflows.md", "# Workflows\n\nDOC-1234 fixture.")
    write_text(run_file(run_dir, "validation", "context-DOC-1234.json"), '{"ok": true}')
    write_text(
        run_file(run_dir, "validation", "historical-benchmark.json"),
        '{"retired_environment": "RETIRED_RAG_ENV"}',
    )
    write_text(
        run_file(run_dir, "artifacts", "context", "context-packet.json"),
        '{"retired_environment": "RETIRED_RAG_ENV"}',
    )
    update_run_packet(
        run_dir,
        next_action=(
            "Resume DOC-1234 with docs/workflow/workflows.md, "
            f"validation/context-DOC-1234.json, commit {commit}, "
            "port 8080, and FEATURE_FLAG_ENABLED."
        ),
        commands=[
            {
                "command": (
                    "python -B .agents/manage.py workflow resume --name story-flow "
                    f"--run-id run-a --evidence validation/context-DOC-1234.json --commit {commit}"
                ),
                "status": "ok",
            }
        ],
        evidence_paths=[
            "docs/workflow/workflows.md",
            "validation/context-DOC-1234.json",
            "validation/historical-benchmark.json",
        ],
        decisions=[
            {
                "decision": "Preserve DOC-1234 coordinate",
                "why": f"Commit {commit} identifies the validation evidence.",
            }
        ],
    )

    report = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)

    coordinates = report["coordinate_closet"]
    assert_field(coordinates, "status", "present")
    assert_has_all(coordinates["paths"], "docs/workflow/workflows.md", "validation/context-DOC-1234.json")
    assert_has_all(coordinates["hashes"], commit)
    assert_has_all(coordinates["ids"], "DOC-1234")
    assert_has_all(coordinates["ports"], "8080")
    assert_has_all(coordinates["env"], "FEATURE_FLAG_ENABLED")
    assert_lacks_all(coordinates["env"], "RETIRED_RAG_ENV")
    context_markdown = run_file(run_dir, "artifacts", "context", "context-packet.md").read_text(encoding="utf-8")
    assert_has_all(context_markdown, "## Exact Identifier Closet", commit, "DOC-1234")


def test_context_coordinate_preservation_survives_handle_and_file_caps(tmp):
    module_dir = write_fixture(tmp)
    write_guidance_savings_fixture(tmp)
    manifest = read_json(module_file(module_dir, "module.json"))
    manifest["context"] = declared_context_spec(
        context_source(
            "coordinate-source",
            "automations/story-flow/runs/<run-id>/coordinate-source.md",
            budget_ref="coordinates",
        ),
        budgets={"coordinates": 100_000},
    )
    write_json(module_file(module_dir, "module.json"), manifest)
    run_dir = run_dir_for(module_dir)
    commit = "abcdef1234567890abcdef1234567890abcdef12"
    coordinate_text = (
        "# Coordinate Source\n\n"
        + ("padding without coordinates\n" * 8_000)
        + f"Resume EXACT-90210 at commit {commit} on port 6553 with EXACT_ENV_NAME.\n"
    )
    write_text(run_file(run_dir, "coordinate-source.md"), coordinate_text)
    update_run_packet(
        run_dir,
        evidence_paths=[f"validation/decoy-{index}.json" for index in range(40)],
    )
    for index in range(40):
        write_json(run_file(run_dir, "validation", f"decoy-{index}.json"), {"ok": True})

    coordinate_path = run_file(run_dir, "coordinate-source.md")
    original_read_text = Path.read_text

    def reject_full_coordinate_read(path, *args, **kwargs):
        if path == coordinate_path:
            raise AssertionError("coordinate-preservation source must be streamed")
        return original_read_text(path, *args, **kwargs)

    with patch.object(Path, "read_text", reject_full_coordinate_read):
        report = workflow_run_support.context_workflow_run(
            tmp,
            "story-flow",
            run_id="run-a",
            write=True,
        )

    coordinates = report["coordinate_closet"]
    assert_has_all(coordinates["hashes"], commit)
    assert_has_all(coordinates["ids"], "EXACT-90210")
    assert_has_all(coordinates["ports"], "6553")
    assert_has_all(coordinates["env"], "EXACT_ENV_NAME")


def test_context_packet_does_not_treat_model_labels_as_ticket_coordinates(tmp):
    write_fixture(tmp, "user-story-workflow")
    write_guidance_savings_fixture(tmp)
    run_dir = workflow_run_dir(tmp, "user-story-workflow")
    update_run_packet(
        run_dir,
        next_action="Use GPT-5.4-mini for bounded work and escalate to GPT-5.5 when needed.",
        decisions=[{"decision": "Use GPT-5.4-mini", "why": "Fixture model-routing guidance."}],
    )

    report = workflow_run_support.context_workflow_run(tmp, "user-story-workflow", run_id="run-a", write=True)

    assert_fields(report, schema_version=3, tool="workflow-manager.context-packet")
    assert "coordinate_closet" not in report


def test_context_packet_does_not_treat_uppercase_filename_as_environment_coordinate(tmp):
    write_fixture(tmp, "user-story-workflow")
    write_guidance_savings_fixture(tmp)
    run_dir = workflow_run_dir(tmp, "user-story-workflow")
    update_run_packet(
        run_dir,
        next_action="Read PROJECT_CONTEXT.md before continuing.",
    )

    report = workflow_run_support.context_workflow_run(tmp, "user-story-workflow", run_id="run-a", write=True)

    assert_fields(report, schema_version=3, tool="workflow-manager.context-packet")
    assert "coordinate_closet" not in report


def test_context_packet_quality_gate_rejects_guidance_below_threshold(tmp):
    write_fixture(tmp)
    write_guidance_savings_fixture(tmp, min_saved_percent=99)

    report = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)

    assert_not_ok(report)
    assert_field(report["guidance_savings"], "status", "better-below-threshold")
    assert_status(report["quality_gate"], "failed")
    failed_names = {item["name"] for item in report["quality_gate"]["failed_checks"]}
    assert "guidance-threshold-if-measurable" in failed_names
    assert_contains(report["issues"], "context packet quality gate failed:")
    assert_contains(report["issues"], "guidance-threshold-if-measurable")


def test_context_packet_quality_gate_rejects_guidance_over_absolute_budget(tmp):
    write_fixture(tmp)
    write_guidance_savings_fixture(tmp, default_guidance_budget_tokens=1)

    report = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)

    assert_not_ok(report)
    assert_field(report["guidance_savings"], "status", "over-budget")
    assert_false(report["guidance_savings"], "within_absolute_budget")
    failed_names = {item["name"] for item in report["quality_gate"]["failed_checks"]}
    assert "guidance-absolute-budget" in failed_names


def test_context_packet_quality_gate_rejects_invalid_guidance_budget_configuration(tmp):
    for index, value in enumerate(("5000", True, 0, -1)):
        case_root = tmp / f"case-{index}"
        write_fixture(case_root)
        write_guidance_savings_fixture(
            case_root,
            default_guidance_budget_tokens=value,
        )

        report = workflow_run_support.context_workflow_run(
            case_root,
            "story-flow",
            run_id="run-a",
            write=True,
        )

        assert_not_ok(report)
        assert_fields(
            report["guidance_savings"],
            budget_tokens=5000,
            budget_source="fallback-invalid",
        )
        assert_contains(
            [report["guidance_savings"]["budget_issue"]],
            "cost_policy.guidance.default.budget_tokens",
        )
        failed_names = {
            item["name"] for item in report["quality_gate"]["failed_checks"]
        }
        assert "guidance-budget-config-valid" in failed_names


def test_context_packet_quality_gate_rejects_missing_guidance_files(tmp):
    write_fixture(tmp)
    write_guidance_savings_fixture(tmp)
    (tmp / "docs" / "fixture-guidance" / "HANDOFF.md").unlink()

    report = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)

    assert_not_ok(report)
    assert_field(report["guidance_savings"], "status", "incomplete")
    assert_false(report["guidance_savings"], "complete")
    failed_names = {item["name"] for item in report["quality_gate"]["failed_checks"]}
    assert "guidance-files-complete" in failed_names


def test_context_packet_includes_structured_instruction_context(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp)
    extend_fixture_workflow(module_dir, "Retained workflow guidance for the structured-context fixture.", 160)
    write_text(
        module_file(module_dir, "instructions.md"),
        """# Instructions

## Always Load

- Keep approval status explicit before implementation.
- Preserve generated-file boundaries.

## Stop Rules

- Stop when validation evidence is missing.

## Completion Contract

- Report changed paths, validation, skipped checks, and remaining risks.

## Phase: execute

- [ ] Execute the current phase from deterministic evidence.

## Phase: validate

- [ ] Validate the completed change.

""",
    )

    report = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)

    assert_ok(report)
    instruction_context = report["instruction_context"]
    assert_status(instruction_context, "ok")
    assert_false(instruction_context, "requires_full_instructions")
    assert_has_all(instruction_context["always_load"], "Keep approval status explicit")
    assert_has_all(instruction_context["stop_rules"], "validation evidence is missing")
    assert_has_all(instruction_context["completion_contract"], "remaining risks")
    assert_field(instruction_context, "current_phase", "execute")
    assert_has_all(instruction_context["current_phase_instructions"], "Execute the current phase")
    assert instruction_context["instructions_sha256"]
    assert_lacks_all(report["required_next_context"], "automations/story-flow/instructions.md")


def test_context_packet_requires_full_instructions_when_phase_section_missing(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp)
    write_text(
        module_file(module_dir, "instructions.md"),
        """# Instructions

## Always Load

- Keep approval status explicit before implementation.

## Phase: intake

- [ ] Gather the initial facts.
""",
    )

    report = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)

    assert_true(report["instruction_context"], "requires_full_instructions")
    assert_has_all(report["instruction_context"]["issues"], CURRENT_PHASE_MISSING)
    assert_has_all(report["required_next_context"], "automations/story-flow/instructions.md")


def test_context_packet_matches_phase_tokens_and_completed_phase(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp)
    run_dir = run_dir_for(module_dir)
    update_run_packet(run_dir, current_phase="implementation")
    write_text(
        module_file(module_dir, "instructions.md"),
        """# Instructions

## Always Load

- Keep the run packet current.

## Phase: Fix Implementation

- [ ] Implement the approved fix.
""",
    )

    report = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)

    assert_false(report["instruction_context"], "requires_full_instructions")
    assert_has_all(report["instruction_context"]["current_phase_instructions"], "approved fix")

    update_run_packet(run_dir, current_phase="complete")
    write_text(
        module_file(module_dir, "instructions.md"),
        """# Instructions

## Always Load

- Keep the run packet current.
""",
    )

    completed_report = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)

    assert_false(completed_report["instruction_context"], "requires_full_instructions")
    assert_lacks_all(completed_report["instruction_context"]["issues"], CURRENT_PHASE_MISSING)


def test_context_packet_caps_retained_findings_without_unresolved_failure(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp)
    extend_fixture_workflow(module_dir, "Baseline workflow evidence for retained-findings compaction.", 240)
    run_dir = run_dir_for(module_dir)
    commands = [
        {"command": f"command-{index}", "status": "passed", "evidence_path": f"artifacts/command-{index}.json"}
        for index in range(12)
    ]
    commands.insert(
        3,
        {"command": "known failed command", "status": "failed", "evidence_path": "artifacts/failed-command.json"},
    )
    update_run_packet(
        run_dir,
        status="completed-with-findings",
        workflow_status="completed-with-findings",
        failed=[{"name": "known-finding", "reason": "Retained benchmark finding."}],
        commands=commands,
        evidence_paths=[f"artifacts/evidence-{index}.json" for index in range(12)],
    )
    for index in range(12):
        write_json(run_file(run_dir, "artifacts", f"command-{index}.json"), {"ok": True})
        write_json(run_file(run_dir, "artifacts", f"evidence-{index}.json"), {"ok": True})
    write_json(run_file(run_dir, "artifacts", "failed-command.json"), {"ok": False})

    report = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)

    assert_ok(report)
    assert_lacks_all(report["issues"], "failed checks recorded")
    commands = report["validation_summary"]["commands"]
    assert len(commands) <= 8
    assert_contains_each(commands, "known failed command", "omitted")
    assert_field_set([row for row in commands if row["status"] == "passed"], "ok", {True})
    assert len(report["evidence_handles"]) <= 12
    assert_contains(report["evidence_handles"], "evidence handle(s) omitted")


def test_resume_auto_refreshes_declared_context_packet(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp)
    extend_fixture_workflow(module_dir, "Baseline workflow evidence for automatic context refresh.", 240)
    run_dir = workflow_run_dir(tmp)
    context_path = run_file(run_dir, "artifacts", "context", "context-packet.json")
    checkpoint_path = run_file(run_dir, "artifacts", "checkpoint", "checkpoint.json")
    assert not context_path.exists()
    assert not checkpoint_path.exists()

    resume = workflow_run_support.resume_workflow_run(tmp, "story-flow", run_id="run-a")

    assert_true(resume, "context_auto_refreshed")
    assert_true(resume, "checkpoint_auto_refreshed")
    assert str(resume["context_handoff_path"]).endswith("artifacts/context/context-packet.json")
    assert str(resume["checkpoint_path"]).endswith("artifacts/checkpoint/checkpoint.json")
    assert context_path.exists()
    assert checkpoint_path.exists()
    run_packet = read_json(run_file(run_dir))
    required_context = run_packet["handoff"]["required_next_context"]
    assert_has_all(required_context, "automations/story-flow/runs/run-a/artifacts/context/context-packet.json")


def test_context_packet_writes_documentation_delta(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp)
    run_dir = run_dir_for(module_dir)
    update_run_packet(
        run_dir,
        changed_files=["docs/start-here.md", "src/Feature.cs"],
        documentation={
            "required_updates": ["Refresh docs/start-here.md"],
            "frontmatter_checked": True,
            "map_checked": True,
            "evidence_paths": ["validation/docs-check.json"],
        },
    )

    report = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)

    documentation = report["documentation_delta"]
    assert_status(documentation, "ok")
    assert_field(documentation, "changed_docs", ["docs/start-here.md"])
    assert_true(documentation, "frontmatter_checked")
    assert_true(documentation, "map_checked")
    assert documentation["paths"]["json"].endswith("artifacts/documentation/documentation-delta.json")
    delta_path = run_file(run_dir, "artifacts", "documentation", "documentation-delta.json")
    delta_markdown = run_file(run_dir, "artifacts", "documentation", "documentation-delta.md")
    assert delta_path.exists()
    assert delta_markdown.exists()
    written_delta = read_json(delta_path)
    assert_field(written_delta, "changed_docs", ["docs/start-here.md"])
    assert_contains(report["evidence_handles"], "artifacts/documentation/documentation-delta.json")


def test_context_packet_surfaces_documentation_delta_issues(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp)
    update_run_packet(run_dir_for(module_dir), changed_files=["docs/workflow/workflows.md"])

    report = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)

    assert_not_ok(report)
    assert_status(report["documentation_delta"], "needs-attention")
    assert_has_all(
        report["issues"],
        "documentation delta: changed docs need frontmatter check evidence",
        "documentation delta: changed docs need documentation-map reachability check evidence",
    )


def test_recover_rebuilds_missing_run_packet(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp)
    extend_fixture_workflow(module_dir, "Baseline workflow evidence for recovered context refresh.", 240)
    run_dir = workflow_run_dir(tmp)
    run_file(run_dir).unlink()

    check = workflow_run_support.recover_workflow_run(tmp, "story-flow", run_id="run-a")
    assert_not_ok(check)
    assert_true(check, "needs_recovery")
    assert not run_file(run_dir).exists()

    report = workflow_run_support.recover_workflow_run(tmp, "story-flow", run_id="run-a", write=True)
    assert_ok(report)
    assert_true(report, "needs_recovery")
    assert_true(report, "context_auto_refreshed")
    assert run_file(run_dir).exists()
    packet = read_json(run_file(run_dir))
    assert_fields(packet, schema_version=2, workflow="story-flow")
    assert_has_all(packet["handoff"]["required_next_context"], "automations/story-flow/runs/run-a/artifacts/context/context-packet.json")


def test_recover_backs_up_invalid_run_packet(tmp):
    write_fixture(tmp)
    run_dir = workflow_run_dir(tmp)
    run_file(run_dir).write_text("{ invalid json", encoding="utf-8", newline="\n")

    report = workflow_run_support.recover_workflow_run(tmp, "story-flow", run_id="run-a", write=True)

    assert_ok(report)
    assert report["backup_path"].endswith(".txt")
    assert list(run_dir.glob("run.invalid.*.txt"))
    packet = read_json(run_file(run_dir))
    assert packet["decisions"][0]["decision"] == "recover-run-packet"


def test_context_packet_check_detects_stale_packet_without_writing(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp)
    extend_fixture_workflow(module_dir, "Baseline workflow evidence for context freshness checks.", 240)
    report = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)
    assert_ok(report)

    run_dir = workflow_run_dir(tmp)
    packet_path = run_file(run_dir, "artifacts", "context", "context-packet.json")
    before = packet_path.read_text(encoding="utf-8")
    assert_has_all(before, FIXTURE_NEXT_ACTION)

    update_run_packet(run_dir, next_action="Continue.")

    check = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", check=True)

    assert_not_ok(check)
    assert_status(check, "stale")
    assert_has_all(check["issues"], STALE_CONTEXT_PREFIX)
    assert check["existing_packet_path"].endswith("artifacts/context/context-packet.json")
    assert packet_path.read_text(encoding="utf-8") == before
    compact = workflow_repo_manager.compact_context_run_report(check)
    assert_fields(compact, workflow="story-flow", issue_count=1)
    assert_status(compact, "stale")
    assert_false(compact, "context_packet_fresh")
    assert_lacks_all(compact, "required_next_context")


def test_context_packet_quality_gate_detects_missing_markdown(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp)
    extend_fixture_workflow(module_dir, "Baseline workflow evidence for quality-gate checks.", 240)
    workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)
    run_dir = workflow_run_dir(tmp)
    run_file(run_dir, "artifacts", "context", "context-packet.md").unlink()

    check = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", check=True)

    assert_not_ok(check)
    assert_status(check, "quality-failed")
    assert_true(check["check"], "fresh")
    assert_false(check["check"], "markdown_exists")
    assert_status(check["quality_gate"], "failed")
    assert_contains(check["quality_gate"]["failed_checks"], "packet-markdown-exists")
    assert_contains(check["issues"], "context packet quality gate failed")
    compact = workflow_repo_manager.compact_context_run_report(check)
    assert_fields(compact, quality_gate_status="failed", quality_gate_failed_count=1)
    assert_contains(compact["failed_quality_checks"], "packet-markdown-exists")


def test_context_packet_check_reports_fresh_packet_issues_without_stale_label(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp)
    extend_fixture_workflow(module_dir, "Baseline workflow evidence for unsupported-claim advisory checks.", 240)
    run_dir = workflow_run_dir(tmp)
    update_run_packet(run_dir, unsupported_claims=[{"claim": "fixture claim", "evidence": ""}])

    report = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)
    assert_status(report, "ok")
    assert_has_all(" ".join(report["advisories"]), "unsupported claims recorded")

    check = workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", check=True)

    assert_ok(check)
    assert_true(check["check"], "fresh")
    assert_status(check, "ok")
    assert_has_all(" ".join(check["advisories"]), "unsupported claims recorded")
    assert_lacks_all(check["issues"], STALE_CONTEXT_PREFIX)


def test_context_audit_reports_resume_handoff_packet_freshness_and_required_paths(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp)
    extend_fixture_workflow(module_dir, "Baseline workflow evidence for context-audit checks.", 240)
    workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)

    audit = workflow_run_support.context_audit_workflow_run(tmp, "story-flow", run_id="run-a")

    assert_tool(audit, "workflow-manager.context-audit")
    assert_ok(audit)
    assert_fields(audit, workflow="story-flow", run_id="run-a", context_packet_status="ok")
    assert_true(audit, "context_packet_fresh")
    assert audit["context_packet_path"].endswith("artifacts/context/context-packet.json")
    assert audit["required_next_context_count"] == 1
    assert_field(audit, "missing_required_context", [])
    assert_field(audit, "missing_evidence_paths", [])
    assert_field(audit, "quality_gate_status", "ok")
    assert_fields(audit["resume_contract"], status="ready", can_resume=True)
    assert_field(audit["resume_contract"], "next_command_mode", "resume")
    assert_field(audit["resume_contract"], "blocking_reasons", [])
    assert any(str(item).endswith("artifacts/context/context-packet.json") for item in audit["resume_contract"]["read_first"])
    assert_contains(audit["resume_contract"]["read_first"], "automations/navigation/artifacts/maps/HANDOFF.md")
    compact = workflow_repo_manager.compact_context_audit_report(audit)
    assert_fields(compact, workflow="story-flow", issue_count=0, quality_gate_status="ok")
    assert_fields(compact["resume_contract"], status="ready", can_resume=True)


def test_context_audit_resume_contract_reports_blocking_reasons(tmp):
    write_guidance_savings_fixture(tmp)
    write_fixture(tmp)
    workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)
    run_dir = workflow_run_dir(tmp)
    packet = read_json(run_file(run_dir))
    handoff = packet["handoff"]
    handoff["required_next_context"] = ["automations/story-flow/runs/run-a/missing-plan.md"]
    write_json(run_file(run_dir), packet)

    audit = workflow_run_support.context_audit_workflow_run(tmp, "story-flow", run_id="run-a")

    assert_not_ok(audit)
    assert_fields(audit["resume_contract"], status="blocked", can_resume=False)
    assert_field(audit["resume_contract"], "next_command_mode", "refresh-context")
    assert_contains(audit["resume_contract"]["blocking_reasons"], "missing-required-context")
    assert_field(audit["resume_contract"]["reason_counts"], "missing_required_context", 1)
    compact = workflow_repo_manager.compact_context_audit_report(audit)
    assert_fields(compact["resume_contract"], status="blocked", can_resume=False)
    assert_contains(compact["resume_contract"]["blocking_reasons"], "missing-required-context")


def test_checkpoint_write_is_compact_and_check_detects_staleness(tmp):
    module_dir = write_fixture(tmp)
    run_dir = run_dir_for(module_dir)

    report = workflow_run_support.checkpoint_workflow_run(tmp, "story-flow", run_id="run-a", write=True)

    assert_tool(report, "workflow-manager.checkpoint")
    assert_ok(report)
    assert_field(report, "checkpoint_kind", "compact-generated")
    assert_field(report["snapshot"]["workflow"], "name", "story-flow")
    assert_field(report["snapshot"]["run"], "run_id", "run-a")
    assert report["context_budget"]["checkpoint_tokens_estimated"] < report["context_budget"]["raw_tokens_estimated"]
    assert all("sha256" in item for item in report["source_files"] if item["exists"])
    assert_lacks_all(json.dumps(report), "Coordinates a deterministic story flow")
    checkpoint_path = run_file(run_dir, "artifacts", "checkpoint", "checkpoint.json")
    markdown_path = run_file(run_dir, "artifacts", "checkpoint", "checkpoint.md")
    assert checkpoint_path.exists()
    assert markdown_path.exists()

    before = checkpoint_path.read_text(encoding="utf-8")
    update_run_packet(run_dir, next_action="Changed checkpoint.")

    check = workflow_run_support.checkpoint_workflow_run(tmp, "story-flow", run_id="run-a", check=True)

    assert_not_ok(check)
    assert_status(check, "stale")
    assert_has_all(check["issues"], "checkpoint is stale")
    assert checkpoint_path.read_text(encoding="utf-8") == before
    compact = workflow_repo_manager.compact_checkpoint_run_report(check)
    assert_field(compact, "workflow", "story-flow")
    assert_status(compact, "stale")
    assert_false(compact, "checkpoint_fresh")
    assert_lacks_all(compact, "snapshot")


def test_doctor_summary_reports_context_packet_freshness(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp)
    extend_fixture_workflow(
        module_dir,
        "fixturebaselinecontextpayloadxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        120,
    )
    workflow_run_support.context_workflow_run(tmp, "story-flow", run_id="run-a", write=True)

    report = workflow_repo_manager.review_all_workflows(tmp, summary=True)
    row = report["workflows"][0]

    assert_fields(row, context_packet_status="ok", risk="ok")
    assert_true(row, "context_packet_fresh")
    compact_report = workflow_repo_manager.review_all_workflows(tmp, summary=True, compact=True)
    assert_fields(compact_report["summary"], workflow_count=1, risk_count=0)
    assert_lacks_all(compact_report, "workflows", "risks", "next_command")

    update_run_packet(workflow_run_dir(tmp), next_action="Continue after changing the run packet.")

    stale_report = workflow_repo_manager.review_all_workflows(tmp, summary=True)
    stale_row = stale_report["workflows"][0]

    assert_status(stale_report, "warning")
    assert_fields(
        stale_row,
        risk="ok",
        context_packet_status="stale",
        run_advisory=True,
        run_advisory_issue=True,
    )
    assert_false(stale_row, "context_packet_fresh")
    stale_compact = workflow_repo_manager.review_all_workflows(tmp, summary=True, compact=True)
    assert_field(stale_compact["workflows"][0], "risk", "ok")
    assert_field(stale_compact["summary"], "advisory_count", 1)

    strict_report = workflow_repo_manager.review_all_workflows(tmp, summary=True, include_completed=True)
    strict_row = strict_report["workflows"][0]
    assert_not_ok(strict_report)
    assert_status(strict_report, "failed")
    assert_fields(strict_row, risk="context-packet", context_packet_status="stale", run_advisory=False)
    strict_compact = workflow_repo_manager.review_all_workflows(
        tmp,
        summary=True,
        compact=True,
        include_completed=True,
    )
    assert_field(strict_compact["workflows"][0], "risk", "context-packet")


def test_doctor_aggregates_every_retained_run_and_active_is_never_hidden(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp)
    active = run_packet("story-flow", "active-old")
    active.update({"status": "partial", "updated_at": "2026-07-01T00:00:00Z"})
    write_json(module_dir / "runs" / "active-old" / "run.json", active)

    default = workflow_repo_manager.review_all_workflows(tmp, summary=True, compact=True)
    strict = workflow_repo_manager.review_all_workflows(
        tmp,
        summary=True,
        compact=True,
        include_completed=True,
    )

    assert_not_ok(default)
    assert_fields(default["summary"], blocking_context_count=1, advisory_count=1, context_row_count=2)
    default_rows = {row["run_id"]: row for row in default["context_rows"]}
    assert_true(default_rows["active-old"], "blocking")
    assert_true(default_rows["run-a"], "advisory")
    assert "--run-id active-old" in default_rows["active-old"]["next_command"]
    assert_not_ok(strict)
    assert_fields(strict["summary"], blocking_context_count=2, advisory_count=0, context_row_count=2)
    strict_rows = {row["run_id"]: row for row in strict["context_rows"]}
    assert_true(strict_rows["active-old"], "blocking")
    assert_true(strict_rows["run-a"], "blocking")

    with patch("builtins.print"):
        default_exit = workflow_repo_manager.main(
            ["review-workflow", "--root", str(tmp), "--all", "--summary", "--compact", "--format", "json"]
        )
        strict_exit = workflow_repo_manager.main(
            [
                "review-workflow",
                "--root",
                str(tmp),
                "--all",
                "--include-completed",
                "--summary",
                "--compact",
                "--format",
                "json",
            ]
        )
    assert default_exit == 1
    assert strict_exit == 1


def test_doctor_completed_only_is_advisory_default_and_strict_nonzero(tmp):
    write_guidance_savings_fixture(tmp)
    write_fixture(tmp)

    default = workflow_repo_manager.review_all_workflows(tmp, summary=True, compact=True)
    strict = workflow_repo_manager.review_all_workflows(
        tmp,
        summary=True,
        compact=True,
        include_completed=True,
    )

    assert_ok(default)
    assert_status(default, "warning")
    assert_fields(default["summary"], blocking_context_count=0, advisory_count=1, context_row_count=1)
    assert_not_ok(strict)
    assert_status(strict, "failed")
    assert_fields(strict["summary"], blocking_context_count=1, advisory_count=0, context_row_count=1)

    with patch("builtins.print"):
        default_exit = workflow_repo_manager.main(
            ["review-workflow", "--root", str(tmp), "--all", "--summary", "--compact", "--format", "json"]
        )
        strict_exit = workflow_repo_manager.main(
            [
                "review-workflow",
                "--root",
                str(tmp),
                "--all",
                "--include-completed",
                "--summary",
                "--compact",
                "--format",
                "json",
            ]
        )
    assert default_exit == 0
    assert strict_exit == 1


def test_doctor_summary_reports_story_bug_out_of_scope_templates(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp, "user-story-workflow")
    write_story_bug_templates(module_dir)
    workflow_run_support.context_workflow_run(tmp, "user-story-workflow", run_id="run-a", write=True)

    report = workflow_repo_manager.review_all_workflows(tmp, summary=True)
    row = report["workflows"][0]

    assert_fields(row, out_of_scope_status="ok", risk="ok")
    assert_empty(row["out_of_scope_missing"])

    write_text(template_path(module_dir), MISSING_OUT_OF_SCOPE_PLAN)

    stale_report = workflow_repo_manager.review_all_workflows(tmp, summary=True)
    stale_row = stale_report["workflows"][0]

    assert_status(stale_report, "warning")
    assert_fields(
        stale_row,
        risk="out-of-scope",
        out_of_scope_status="missing",
        out_of_scope_missing=["automations/user-story-workflow/templates/plan.md"],
    )


def test_finish_refreshes_required_context_packet_after_run_state_changes(tmp):
    write_guidance_savings_fixture(tmp)
    write_fixture(tmp, "user-story-workflow")
    workflow_run_support.context_workflow_run(tmp, "user-story-workflow", run_id="run-a", write=True)
    update_run_packet(workflow_run_dir(tmp, "user-story-workflow"), next_action="Changed run.")

    report = workflow_run_support.finish_workflow_run(tmp, "user-story-workflow", run_id="run-a")

    assert_not_ok(report)
    assert_true(report, "context_packet_refreshed")
    assert_lacks_all(report["issues"], STALE_CONTEXT_ISSUE)
    assert report["next_command"].endswith("workflow resume --name user-story-workflow --run-id run-a")


def test_finish_fails_completed_run_without_external_validation_status(tmp):
    write_fixture(tmp)
    update_run_packet(workflow_run_dir(tmp), status="completed", external_validation_status="not-recorded")

    report = workflow_run_support.finish_workflow_run(tmp, "story-flow", run_id="run-a")

    assert_not_ok(report)
    assert_has_all(report["issues"], "completed run has no external validation status")


def test_finish_fails_completed_run_without_evidence_entries(tmp):
    write_fixture(tmp)
    update_run_packet(workflow_run_dir(tmp), status="completed", evidence=[], evidence_paths=[])

    report = workflow_run_support.finish_workflow_run(tmp, "story-flow", run_id="run-a")

    assert_not_ok(report)
    assert_has_all(report["issues"], "completed run has no evidence entries")


def test_successful_finish_promotes_partial_run_to_completed(tmp):
    write_fixture(tmp)
    run_dir = workflow_run_dir(tmp)
    write_start_context_evidence(tmp, "story-flow", run_dir)
    update_run_packet(run_dir, status="partial", next_action="Finish the run.")

    report = workflow_run_support.finish_workflow_run(tmp, "story-flow", run_id="run-a")
    stored = read_json(run_file(run_dir, "run.json"))

    assert_ok(report)
    assert_field(report, "status", "completed")
    assert_field(stored, "status", "completed")
    assert_field(stored["phase"], "status", "completed")
    assert_field(stored, "next_action", "")


def test_finish_fails_when_story_bug_out_of_scope_template_is_missing(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp, "user-story-workflow")
    write_story_bug_templates(module_dir, OUT_OF_SCOPE_DEFERRED_SECTION, names=("ticket-info.md", "pr-description.md"))
    write_text(template_path(module_dir), MISSING_OUT_OF_SCOPE_PLAN)
    workflow_run_support.context_workflow_run(tmp, "user-story-workflow", run_id="run-a", write=True)

    report = workflow_run_support.finish_workflow_run(tmp, "user-story-workflow", run_id="run-a")

    assert_not_ok(report)
    assert_has_all(report["issues"], "out-of-scope template missing: automations/user-story-workflow/templates/plan.md")
    assert_lacks_all(report["issues"], STALE_CONTEXT_ISSUE)


def test_finish_fails_when_pr_handoff_has_template_artifacts(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp, "user-story-workflow")
    write_story_bug_templates(module_dir)
    run_dir = run_dir_for(module_dir)
    write_text(
        run_file(run_dir, "pr-description.md"),
        f"""# Pull Request

**Ticket:** [number] - [title]
**Type:** User Story | Bug

## Summary

## Changes

-

## Validation

| Check | Result | Evidence |
|---|---|---|
""",
    )
    workflow_run_support.context_workflow_run(tmp, "user-story-workflow", run_id="run-a", write=True)

    report = workflow_run_support.finish_workflow_run(tmp, "user-story-workflow", run_id="run-a")

    issues = "\n".join(report["issues"])
    assert_not_ok(report)
    assert_has_all(
        issues,
        "pr-description.md contains template placeholder: [number]",
        "pr-description.md contains template placeholder: [title]",
        "pr-description.md contains template placeholder: User Story | Bug",
        "pr-description.md contains empty template item",
        "pr-description.md has no final content in section: Summary",
        "pr-description.md has no final content in section: Validation",
    )
    assert_lacks_all(issues, STALE_CONTEXT_ISSUE)


def test_pr_handoff_requires_reusable_lesson_or_no_lesson_reason(tmp):
    module_dir = write_fixture(tmp, "user-story-workflow")
    run_dir = run_dir_for(module_dir)
    write_text(
        run_file(run_dir, "pr-description.md"),
        pr_description(lesson="-"),
    )

    empty_issues = story_bug_quality.pr_handoff_issues(tmp, run_dir)

    assert_contains(empty_issues, "Reusable Lessons")

    write_text(
        run_file(run_dir, "pr-description.md"),
        pr_description(),
    )

    filled_issues = story_bug_quality.pr_handoff_issues(tmp, run_dir)

    assert_lacks(filled_issues, "Reusable Lessons")


def prepare_story_bug_finish_fixture(tmp, workflow_name, plan_text, pr_text):
    module_dir = write_workflow(tmp, workflow_name)
    write_story_bug_templates(module_dir, OUT_OF_SCOPE_FIXTURE_SECTION)
    run_dir = run_dir_for(module_dir)
    write_text(run_file(run_dir, "ticket-info.md"), TICKET_INFO_FIXTURE)
    write_text(run_file(run_dir, "plan.md"), plan_text)
    write_text(run_file(run_dir, "pr-description.md"), pr_text)
    write_text(run_file(run_dir, "execution-log.md"), progress_log_text(workflow_name))
    write_start_context_evidence(tmp, workflow_name, run_dir)
    return run_dir


def test_finish_reports_story_acceptance_proof_gap(tmp):
    write_skill(tmp)
    run_dir = prepare_story_bug_finish_fixture(
        tmp,
        "user-story-workflow",
        approved_story_plan(),
        pr_description("Delivered fixture.", "Self-test evidence."),
    )

    report = workflow_run_support.finish_workflow_run(tmp, "user-story-workflow", run_id=run_dir.name)

    assert_not_ok(report)
    missing = report.get("missing_proof", [])
    assert missing, report
    assert_contains_all(missing, AC_MAPPING, "Validation Evidence")
    assert_contains(report["issues"], f"{AC_MAPPING} row 1 Validation Evidence")
    assert report.get("proof_matrix", {}).get("status") == "failed"


def test_finish_reports_bug_regression_and_root_cause_proof_gap(tmp):
    write_skill(tmp)
    run_dir = prepare_story_bug_finish_fixture(
        tmp,
        "bug-ticket-workflow",
        approved_bug_plan(),
        pr_description(
            "Delivered bug fix.",
            "Self-test evidence.\n\n## Regression Proof\n\nRegression smoke.",
            "{FIXTURE_LESSON}",
        ),
    )

    report = workflow_run_support.finish_workflow_run(tmp, "bug-ticket-workflow", run_id=run_dir.name)

    assert_not_ok(report)
    missing = report.get("missing_proof", [])
    assert missing, report
    assert_contains_each(missing, "Regression-Proof Decision", "Root Cause Evidence")
    assert report.get("proof_matrix", {}).get("missing_count", 0) >= 2


def test_finish_proof_accepts_explicit_skip_with_reason_and_owner(tmp):
    module_dir = write_fixture(tmp, "user-story-workflow")
    run_dir = run_dir_for(module_dir)
    plan_text = approved_story_plan().replace(
        STORY_AC_PROOF_ROW,
        "| AC1 | No code change: owner decision DOC-1 | workflow plan-check output | No docs required: owner decision DOC-1 |",
    )
    write_text(run_file(run_dir, "plan.md"), plan_text)
    write_text(
        run_file(run_dir, "pr-description.md"),
        "No code change: owner decision DOC-1\nworkflow plan-check output\n"
        "No docs required: owner decision DOC-1\n",
    )

    proof = story_bug_quality.story_bug_finish_proof_report(tmp, "user-story-workflow", run_dir, run_packet("user-story-workflow"))

    assert_ok(proof)
    assert_empty(proof["missing_proof"])


def test_finish_fails_when_progress_document_is_missing_or_template_like(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp, "user-story-workflow")
    write_story_bug_templates(module_dir)
    run_dir = run_dir_for(module_dir)
    run_file(run_dir, "execution-log.md").unlink()
    workflow_run_support.context_workflow_run(tmp, "user-story-workflow", run_id="run-a", write=True)

    missing_report = workflow_run_support.finish_workflow_run(tmp, "user-story-workflow", run_id="run-a")

    assert_not_ok(missing_report)
    assert_contains(missing_report["issues"], "execution-log.md is missing")

    write_text(run_file(run_dir, "execution-log.md"), "# User Story Execution Log\n\n## Current State\n\n- Status: not started\n- Current phase:\n- Last updated:")
    workflow_run_support.context_workflow_run(tmp, "user-story-workflow", run_id="run-a", write=True)

    template_report = workflow_run_support.finish_workflow_run(tmp, "user-story-workflow", run_id="run-a")

    issues = "\n".join(template_report["issues"])
    assert_not_ok(template_report)
    assert_has_all(issues, "execution-log.md still has template status: not started", "execution-log.md current phase is empty")


def test_progress_log_requires_reusable_lesson_or_no_lesson_reason(tmp):
    module_dir = write_fixture(tmp, "bug-ticket-workflow")
    run_dir = run_dir_for(module_dir)
    run_packet = read_json(run_file(run_dir))
    missing_lesson_text = progress_log_text("bug-ticket-workflow").replace(
        f"\n## Reusable Lessons\n\n{FIXTURE_LESSON}\n",
        "\n",
    )
    write_text(run_file(run_dir, "execution-log.md"), missing_lesson_text)

    missing_issues = story_bug_quality.progress_log_issues(tmp, "bug-ticket-workflow", run_dir, run_packet)

    assert_contains(missing_issues, "Reusable Lessons")

    write_text(run_file(run_dir, "execution-log.md"), progress_log_text("bug-ticket-workflow"))

    filled_issues = story_bug_quality.progress_log_issues(tmp, "bug-ticket-workflow", run_dir, run_packet)

    assert_lacks(filled_issues, "Reusable Lessons")


def test_context_all_check_reports_required_context_packets(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp, "user-story-workflow")
    extend_fixture_workflow(
        module_dir,
        "fixturebaselinecontextpayloadxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        120,
    )
    write_workflow(tmp, "bug-ticket-workflow")
    write_workflow(tmp, "clean-workflow", with_run=False)
    workflow_run_support.context_workflow_run(tmp, "user-story-workflow", run_id="run-a", write=True)

    report = workflow_repo_manager.context_all_workflow_runs(tmp)

    assert_ok(report)
    assert_status(report, "advisory")
    assert_fields(report, checked_count=2, skipped_count=1, blocking_count=0, advisory_count=1)
    assert_field(report["skipped_workflows"][0], "workflow", "clean-workflow")
    rows = {row["workflow"]: row for row in report["workflows"]}
    assert_field(rows["user-story-workflow"], "context_packet_status", "ok")
    assert_true(rows["user-story-workflow"], "context_packet_fresh")
    assert_field(rows["user-story-workflow"], "quality_gate_status", "ok")
    assert_field(rows["bug-ticket-workflow"], "context_packet_status", "missing")
    assert_field(rows["bug-ticket-workflow"], "quality_gate_status", "failed")
    assert_true(rows["bug-ticket-workflow"], "advisory")
    assert_false(rows["bug-ticket-workflow"], "blocking")
    assert_has_all(rows["bug-ticket-workflow"]["issues"], "context packet is missing")
    compact = workflow_repo_manager.compact_context_all_report(report)
    assert_fields(compact, checked_count=2, skipped_count=1, issue_count=1, missing_count=1, quality_failed_count=1)
    assert_field(compact["skipped_workflows"][0], "workflow", "clean-workflow")
    assert_field(compact["skipped_workflows"][0], "status", "skipped")
    assert "no runs folder" in compact["skipped_workflows"][0]["reason"]
    assert [row["workflow"] for row in compact["workflows"]] == ["bug-ticket-workflow"]

    strict = workflow_repo_manager.context_all_workflow_runs(tmp, include_completed=True)
    assert_not_ok(strict)
    assert_fields(strict, blocking_count=1, advisory_count=0, include_completed=True)
    strict_rows = {row["workflow"]: row for row in strict["workflows"]}
    assert_true(strict_rows["bug-ticket-workflow"], "blocking")
    assert_false(strict_rows["bug-ticket-workflow"], "advisory")


def test_expected_output_classification(tmp):
    module_dir = workflow_dir(tmp)
    assert module_checks.expected_output_classification(module_dir, "runs/<run-id>/run.json") == "workflow-owned"
    assert module_checks.expected_output_classification(module_dir, "src/App.cs") == "target-project"
    assert module_checks.expected_output_classification(module_dir, ".agents/report.md") == "external"
    assert module_checks.expected_output_classification(module_dir, "../outside.md") == "invalid"


def test_validation_summary_json_omits_passing_module_rows(tmp):
    module_dir = write_fixture(tmp)
    rendered = reporting.render_json_report(tmp, [], [], [module_dir], summary=True, compact=True)
    report = json.loads(rendered)

    assert report == {
        "valid": True,
        "automation_count": 1,
        "error_count": 0,
        "warning_count": 0,
        "errors": [],
        "warnings": [],
    }


def test_start_run_executes_declared_required_hook(tmp):
    assert common.child_env()["PYTHONDONTWRITEBYTECODE"] == "1"
    write_fixture(tmp, hooks=True)
    report = workflow_run_support.start_workflow_run(tmp, "story-flow", run_id="hook-run")
    assert_ok(report)
    checklist = report["start_checklist"]
    assert_has_all(
        checklist["first_commands"],
        "python -B .agents/manage.py workflow hooks --name story-flow --run-id hook-run --format json",
        "python -B .agents/manage.py workflow checkpoint --name story-flow --run-id hook-run --write",
        "python -B .agents/manage.py workflow context --name story-flow --run-id hook-run --write",
    )
    assert_field(checklist, "declared_validation", [f"{VALIDATE_AUTOMATIONS_NAME}story-flow"])
    assert_contains(report["created_files"], "artifacts/checkpoint/checkpoint.json")

    run_dir = workflow_run_dir(tmp, run_id="hook-run")
    assert run_file(run_dir, "artifacts", "checkpoint", "checkpoint.json").exists()
    assert run_file(run_dir, "artifacts", "checkpoint", "checkpoint.md").exists()
    assert_hook_markers(
        run_dir,
        ("workflow-pre", "write-workflow-pre-marker"),
        ("run-started", "write-start-marker"),
        ("phase-pre", "write-phase-pre-marker"),
    )
    assert_hook_marker(run_dir, "phase-started", "write-phase-start-marker")
    packet = read_json(run_file(run_dir))
    assert_status(packet["start_checklist"], "pending")
    assert packet["start_checklist"]["required_before_work"]
    by_event = hook_result_map(packet)
    run_key = ("run-started", "write-start-marker")
    assert_hook_ok(by_event, ("workflow-pre", "write-workflow-pre-marker"), ("phase-pre", "write-phase-pre-marker"), run_key)
    run_hook = by_event[run_key]
    assert_true(run_hook, "required")
    assert Path(run_hook["evidence_path"]).as_posix().endswith("validation/hooks/run-started-write-start-marker.txt")
    hook_phase(by_event, ("phase-started", "write-phase-start-marker"))
    assert not list(tmp.rglob("__pycache__"))


def test_handoff_write_executes_declared_phase_handoff_hook(tmp):
    write_fixture(tmp, hooks=True)
    workflow_run_support.start_workflow_run(tmp, "story-flow", run_id="hook-run")
    report = workflow_run_support.handoff_workflow_run(tmp, "story-flow", run_id="hook-run", write=True)
    assert_ok(report)

    run_dir = workflow_run_dir(tmp, run_id="hook-run")
    assert_hook_markers(run_dir, ("phase-between", "write-phase-between-marker"))
    assert_hook_marker(run_dir, "phase-handoff", "write-phase-handoff-marker")
    packet = read_json(run_file(run_dir))
    by_event = hook_result_map(packet)
    assert_hook_ok(by_event, ("phase-between", "write-phase-between-marker"))
    hook_phase(by_event, ("phase-handoff", "write-phase-handoff-marker"))


def test_finish_executes_declared_phase_completed_hook(tmp):
    write_fixture(tmp, hooks=True)
    workflow_run_support.start_workflow_run(tmp, "story-flow", run_id="hook-run")
    update_run_packet(workflow_run_dir(tmp, run_id="hook-run"), external_validation_status="not-required")
    report = workflow_run_support.finish_workflow_run(tmp, "story-flow", run_id="hook-run")
    assert_ok(report)
    assert_field(report, "status", "completed")
    assert report["next_command"].endswith("index-workflow-runs --name story-flow --check")
    assert_fields(report["run_index"], status="written")
    assert_has_all(report["run_index"]["paths"], "automations/story-flow/runs/INDEX.md", "automations/story-flow/runs/index.json")
    assert_contains(report["checkpoint_written"], "artifacts/checkpoint/checkpoint.json")

    run_dir = workflow_run_dir(tmp, run_id="hook-run")
    assert (workflow_runs_dir(tmp) / "INDEX.md").exists()
    assert (workflow_runs_dir(tmp) / "index.json").exists()
    index_check = index_workflow_runs.run(
        index_workflow_runs.Args(
            root=tmp,
            workflow_name="story-flow",
            write=False,
            check=True,
            output_format="json",
        )
    )
    assert index_check == 0
    assert run_file(run_dir, "artifacts", "checkpoint", "checkpoint.json").exists()
    assert_hook_marker(run_dir, "phase-completed", "write-phase-complete-marker")
    assert_hook_markers(
        run_dir,
        ("phase-post", "write-phase-post-marker"),
        ("workflow-post", "write-workflow-post-marker"),
    )
    packet = read_json(run_file(run_dir))
    by_event = hook_result_map(packet)
    hook_phase(by_event, ("phase-completed", "write-phase-complete-marker"))
    assert_hook_ok(by_event, ("phase-post", "write-phase-post-marker"), ("workflow-post", "write-workflow-post-marker"))


def test_finish_executes_declared_phase_blocked_hook_when_run_has_blockers(tmp):
    write_fixture(tmp, hooks=True)
    workflow_run_support.start_workflow_run(tmp, "story-flow", run_id="hook-run")
    run_path = run_file(workflow_run_dir(tmp, run_id="hook-run"))
    packet = read_json(run_path)
    packet["blocked"] = ["fixture blocker"]
    packet["checks"]["blocked"] = ["fixture blocker"]
    run_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    report = workflow_run_support.finish_workflow_run(tmp, "story-flow", run_id="hook-run")
    assert_ok(report)
    assert_field(report, "status", "partial")

    run_dir = workflow_run_dir(tmp, run_id="hook-run")
    assert_hook_marker(run_dir, "phase-blocked", "write-phase-blocked-marker")
    packet = read_json(run_path)
    assert_field(packet, "status", "partial")
    by_event = hook_result_map(packet)
    assert_hook_ok(
        by_event,
        ("phase-blocked", "write-phase-blocked-marker"),
        ("phase-post", "write-phase-post-marker"),
        ("workflow-post", "write-workflow-post-marker"),
    )
    assert_lacks_all(by_event, ("phase-completed", "write-phase-complete-marker"))


def test_global_hooks_execute_for_every_workflow(tmp):
    write_fixture(tmp, hooks=True)
    write_json(
        workflow_hooks_path(tmp),
        {
            "schema_version": 1,
            "hooks": [
                hook_spec("global-workflow-pre", "workflow-pre"),
                hook_spec("global-phase-between", "phase-between"),
                hook_spec("global-workflow-post", "workflow-post"),
            ],
        },
    )
    errors, _warnings, _modules = validate_automations.validate_automations(tmp, workflow_name="story-flow")
    assert_empty(errors)

    workflow_run_support.start_workflow_run(tmp, "story-flow", run_id="global-hook-run")
    workflow_run_support.handoff_workflow_run(tmp, "story-flow", run_id="global-hook-run", write=True)
    workflow_run_support.finish_workflow_run(tmp, "story-flow", run_id="global-hook-run")

    run_dir = workflow_run_dir(tmp, run_id="global-hook-run")
    assert_hook_markers(
        run_dir,
        ("workflow-pre", "global-workflow-pre"),
        ("phase-between", "global-phase-between"),
        ("workflow-post", "global-workflow-post"),
    )
    packet = read_json(run_file(run_dir))
    by_event = hook_result_map(packet, scoped=True)
    assert_hook_ok(
        by_event,
        ("workflow-pre", "global-workflow-pre", "global"),
        ("phase-between", "global-phase-between", "global"),
        ("workflow-post", "global-workflow-post", "global"),
    )


def test_hook_audit_packet_writes_normalized_evidence(tmp):
    write_fixture(tmp)
    run_dir = workflow_run_dir(tmp)
    output_path = run_file(run_dir, "validation", "hooks", "workflow-pre-global-workflow-pre.json")

    packet = workflow_run_support.write_hook_audit_packet(
        tmp,
        "story-flow",
        run_dir,
        event="workflow-pre",
        hook_id="global-workflow-pre",
        output_path=output_path,
    )

    assert_ok(packet)
    assert_tool(packet, "workflow-manager.hook-audit")
    assert_fields(packet, workflow="story-flow", run_id="run-a")
    assert_status(packet, "ok")
    assert_fields(
        packet,
        event="workflow-pre",
        hook_id="global-workflow-pre",
        evidence_paths=["automations/story-flow/runs/run-a/validation/hooks/workflow-pre-global-workflow-pre.json"],
    )
    written = read_json(output_path)
    assert written == packet


def test_hook_support_module_matches_public_exports(tmp):
    _ = tmp
    assert workflow_run_support.WORKFLOW_HOOK_EVENTS == workflow_hooks.WORKFLOW_HOOK_EVENTS
    assert_same_attrs(workflow_run_support, workflow_hooks, "execute_workflow_hooks", "write_hook_audit_packet")


def test_lifecycle_support_module_matches_public_exports(tmp):
    _ = tmp
    from workflow_support import run_lifecycle

    assert_same_attrs(
        workflow_run_support,
        run_lifecycle,
        "safe_run_id",
        "resolve_repo_path",
        "ticket_intake_context",
        "default_workflow_context",
        "workflow_handoff_packet",
        "normalized_run_state",
        "normalized_ledger",
        "latest_or_selected_run_dir",
        "read_json_object",
        "comparable_context_packet",
        "phase_has_blockers",
        "refresh_run_index",
    )


def test_cli_parser_module_matches_public_dispatch(tmp):
    _ = tmp
    assert_same_attrs(workflow_repo_manager, cli_parser, "build_parser")
    parser = workflow_repo_manager.build_parser()
    cases = [
        (["eval-workflow", "--name", WORKFLOW_FIXTURE_NAME, "--suite", "automations/story-flow/suites/workflow-evals.json", *COMPACT_JSON], {"command": "eval-workflow", "workflow_name": WORKFLOW_FIXTURE_NAME, "suite": "automations/story-flow/suites/workflow-evals.json", "summary": True, "compact": True, "output_format": "json"}),
        (["start-run", "--name", WORKFLOW_FIXTURE_NAME, "--profile", "audit", *COMPACT_JSON], {"command": "start-run", "workflow_name": WORKFLOW_FIXTURE_NAME, "profile": "audit", "summary": True, "compact": True, "output_format": "json"}),
        (["resume-run", "--name", WORKFLOW_FIXTURE_NAME, "--run-id", "run-a", *COMPACT_JSON], {"command": "resume-run", "workflow_name": WORKFLOW_FIXTURE_NAME, "run_id": "run-a", "summary": True, "compact": True, "output_format": "json"}),
        (["hooks-run", "--all", "--check", "--compact", "--format", "json"], {"command": "hooks-run", "all": True, "check": True, "compact": True, "output_format": "json"}),
        (["context-run", "--name", WORKFLOW_FIXTURE_NAME, "--check", *COMPACT_JSON], {"command": "context-run", "workflow_name": WORKFLOW_FIXTURE_NAME, "check": True, "summary": True, "compact": True, "output_format": "json"}),
        (["context-run", "--name", WORKFLOW_FIXTURE_NAME, "--run-id", "run-a", "--runtime-observation-file", "automations/story-flow/runs/run-a/validation/runtime-observation.json", "--write", "--format", "json"], {"command": "context-run", "workflow_name": WORKFLOW_FIXTURE_NAME, "run_id": "run-a", "runtime_observation_file": "automations/story-flow/runs/run-a/validation/runtime-observation.json", "write": True, "output_format": "json"}),
        (["eval-workflows", *COMPACT_JSON], {"command": "eval-workflows", "summary": True, "compact": True, "output_format": "json"}),
        (["hooks-run", "--name", WORKFLOW_FIXTURE_NAME, *COMPACT_JSON], {"command": "hooks-run", "workflow_name": WORKFLOW_FIXTURE_NAME, "summary": True, "compact": True, "output_format": "json"}),
        (["checkpoint-run", "--name", WORKFLOW_FIXTURE_NAME, "--check", "--compact", "--format", "json"], {"command": "checkpoint-run", "workflow_name": WORKFLOW_FIXTURE_NAME, "check": True, "compact": True, "output_format": "json"}),
        (["plan-check-run", "--name", WORKFLOW_FIXTURE_NAME, "--template", "--format", "json"], {"command": "plan-check-run", "workflow_name": WORKFLOW_FIXTURE_NAME, "template": True, "output_format": "json"}),
        (["template-run", "resolve", "--name", WORKFLOW_FIXTURE_NAME, "--profile", "audit", *COMPACT_JSON], {"command": "template-run", "template_command": "resolve", "workflow_name": WORKFLOW_FIXTURE_NAME, "template": None, "profile": "audit", "summary": True, "compact": True, "output_format": "json"}),
        (["template-run", "lint", "--name", WORKFLOW_FIXTURE_NAME, *COMPACT_JSON], {"command": "template-run", "template_command": "lint", "workflow_name": WORKFLOW_FIXTURE_NAME, "summary": True, "compact": True, "output_format": "json"}),
        (["template-run", "gate-check", "--all", *COMPACT_JSON], {"command": "template-run", "template_command": "gate-check", "all": True, "summary": True, "compact": True, "output_format": "json"}),
        (["context-evidence-run", "--name", WORKFLOW_FIXTURE_NAME, "--event", "start", "--write", "--format", "json"], {"command": "context-evidence-run", "workflow_name": WORKFLOW_FIXTURE_NAME, "event": "start", "write": True, "output_format": "json"}),
        (["metadata-run", "inspect", "--name", WORKFLOW_FIXTURE_NAME, *COMPACT_JSON], {"command": "metadata-run", "metadata_command": "inspect", "workflow_name": WORKFLOW_FIXTURE_NAME, "summary": True, "compact": True, "output_format": "json"}),
        (["smoke-workflows", "--all", *COMPACT_JSON], {"command": "smoke-workflows", "all": True, "summary": True, "compact": True, "output_format": "json"}),
        (["smoke-workflows", "--name", WORKFLOW_FIXTURE_NAME, "--dry-run", *COMPACT_JSON], {"command": "smoke-workflows", "workflow_names": [WORKFLOW_FIXTURE_NAME], "dry_run": True, "summary": True, "compact": True, "output_format": "json"}),
        (["workflow-workers", "--name", WORKFLOW_FIXTURE_NAME, "--phase", "execute", "--run-id", "run-a", "--delegation-requested", "--task-class", "independent-read-heavy", "--format", "json"], {"command": "workflow-workers", "workflow_names": [WORKFLOW_FIXTURE_NAME], "phase": "execute", "run_id": "run-a", "delegation_requested": True, "task_class": "independent-read-heavy", "output_format": "json"}),
        (["workflow-workers", "--profiles", "--format", "json"], {"command": "workflow-workers", "profiles": True, "output_format": "json"}),
        (["propose-workflow", "--from-request", "review release evidence", *COMPACT_JSON], {"command": "propose-workflow", "from_request": "review release evidence", "summary": True, "compact": True, "output_format": "json"}),
        (["workflow-recipes", *COMPACT_JSON], {"command": "workflow-recipes", "summary": True, "compact": True, "output_format": "json"}),
        (["create-workflow-from-request", "--from-request", "review release evidence", "--name", "release-evidence-workflow", "--write", "--format", "json"], {"command": "create-workflow-from-request", "from_request": "review release evidence", "workflow_name": "release-evidence-workflow", "write": True, "output_format": "json"}),
        (["adjust-workflow", "--name", WORKFLOW_FIXTURE_NAME, "--from-request", "tighten validation", "--plan", "--format", "json"], {"command": "adjust-workflow", "workflow_name": WORKFLOW_FIXTURE_NAME, "from_request": "tighten validation", "plan": True, "output_format": "json"}),
    ]
    for args, expected in cases:
        assert_parsed(parser, args, expected)


def test_workflow_workers_dispatch_forwards_delegation_attestation(tmp):
    captured = {}

    def fake_report(root, **kwargs):
        captured["root"] = root
        captured.update(kwargs)
        return {"ok": True}

    with patch.object(workflow_repo_manager, "workflow_workers_report", fake_report):
        with patch("builtins.print"):
            exit_code = workflow_repo_manager.main(
                [
                    "workflow-workers",
                    "--root",
                    str(tmp),
                    "--name",
                    WORKFLOW_FIXTURE_NAME,
                    "--phase",
                    "intake",
                    "--delegation-requested",
                    "--task-class",
                    "independent-read-heavy",
                    "--format",
                    "json",
                ]
            )

    assert exit_code == 0
    assert_fields(
        captured,
        root=tmp.resolve(),
        workflow_names=[WORKFLOW_FIXTURE_NAME],
        phase="intake",
        delegation_requested=True,
        task_class="independent-read-heavy",
        summary=False,
        compact=False,
        runtime_observation=None,
        runtime_observation_verification_issues=[],
        observation_run_id="",
    )


def test_workflow_workers_run_id_rejects_noncurrent_requested_phase(tmp):
    module_dir = write_fixture(tmp)
    run_dir = run_dir_for(module_dir)
    observation_path = run_file(run_dir, "validation", "runtime-observation.json")
    write_json(observation_path, runtime_observation())
    stored = workflow_run_support.load_runtime_observation_packet(
        tmp,
        WORKFLOW_FIXTURE_NAME,
        "run-a",
        common.relative(tmp, observation_path),
    )
    packet = read_json(run_file(run_dir, "run.json"))
    packet["current_phase"] = "plan"
    packet["runtime_observation"] = stored
    write_json(run_file(run_dir, "run.json"), packet)

    try:
        workflow_repo_manager.main(
            [
                "workflow-workers",
                "--root",
                str(tmp),
                "--name",
                WORKFLOW_FIXTURE_NAME,
                "--phase",
                "execute",
                "--run-id",
                "run-a",
                "--delegation-requested",
                "--format",
                "json",
            ]
        )
    except SystemExit as exc:
        assert_has_all(
            str(exc),
            "phase must match run.json current_phase",
            "requested 'execute'",
            "current 'plan'",
        )
    else:
        raise AssertionError("expected workflow workers to reject a non-current requested phase")


def test_persisted_runtime_observation_rejects_alias_and_identity_mutation(tmp):
    module_dir = write_fixture(tmp)
    run_dir = run_dir_for(module_dir)
    validation_dir = run_file(run_dir, "validation")
    observation_path = validation_dir / "runtime-observation.json"
    write_json(observation_path, runtime_observation())
    stored = workflow_run_support.load_runtime_observation_packet(
        tmp,
        WORKFLOW_FIXTURE_NAME,
        "run-a",
        common.relative(tmp, observation_path),
    )

    loaded, issues = workflow_workers.verify_persisted_runtime_observation(
        tmp,
        WORKFLOW_FIXTURE_NAME,
        "run-a",
        "execute",
        stored,
    )
    assert_empty(issues)
    assert isinstance(loaded, dict)

    alias_path = validation_dir / "runtime-observation-alias.json"
    os.link(observation_path, alias_path)
    alias_record = dict(stored)
    alias_record["evidence_path"] = common.relative(tmp, alias_path)
    _loaded, alias_issues = workflow_workers.verify_persisted_runtime_observation(
        tmp,
        WORKFLOW_FIXTURE_NAME,
        "run-a",
        "execute",
        alias_record,
    )
    assert_has_all("; ".join(alias_issues), "must not use a hard-link alias")
    alias_path.unlink()

    lexical_alias_record = dict(stored)
    lexical_alias_record["evidence_path"] = (
        f"automations/{WORKFLOW_FIXTURE_NAME}/runs/run-a/validation/unused/../runtime-observation.json"
    )
    _loaded, lexical_alias_issues = workflow_workers.verify_persisted_runtime_observation(
        tmp,
        WORKFLOW_FIXTURE_NAME,
        "run-a",
        "execute",
        lexical_alias_record,
    )
    assert_has_all("; ".join(lexical_alias_issues), "must not contain lexical aliases")

    replacement_path = validation_dir / "runtime-observation-replacement.json"
    write_json(replacement_path, runtime_observation())
    real_open = os.open

    def replace_before_open(path, flags):
        if Path(path) == observation_path:
            os.replace(replacement_path, observation_path)
        return real_open(path, flags)

    with patch.object(workflow_workers.os, "open", side_effect=replace_before_open):
        _loaded, mutation_issues = workflow_workers.verify_persisted_runtime_observation(
            tmp,
            WORKFLOW_FIXTURE_NAME,
            "run-a",
            "execute",
            stored,
        )
    assert_has_all(
        "; ".join(mutation_issues),
        "file identity changed while opening",
    )


def test_finish_refreshes_context_packet_after_required_hook_results(tmp):
    write_guidance_savings_fixture(tmp)
    module_dir = write_fixture(tmp, "user-story-workflow", hooks=True)
    manifest = read_json(module_file(module_dir, "module.json"))
    # This case proves that a required post-finish hook is included in the
    # refreshed packet. The full hook-event matrix is covered by dedicated hook
    # tests; retaining it here would make unrelated fixture history dominate the
    # effective resume context.
    manifest["hooks"] = [
        hook for hook in manifest["hooks"] if hook["event"] == "workflow-post"
    ]
    manifest["context"] = manifests.module_contract_v3.conventional_context(
        "user-story-workflow"
    )
    manifest["context"]["sources"].append(
        context_source(
            "required-hook-protocol",
            "automations/user-story-workflow/docs/required-hook-protocol.md",
            role="hook-protocol",
            load_policy="handle_only",
            category="workflow-contract",
            budget_ref="core",
            preserve_coordinates=False,
        )
    )
    write_json(module_file(module_dir, "module.json"), manifest)
    hook_events = (
        "workflow-post",
        "phase-completed",
        "phase-post",
        "phase-handoff",
    )
    result_fields = (
        "declared effects",
        "normalized exit status",
        "evidence path",
        "phase identifier",
        "timeout boundary",
        "stable result ordering",
    )
    protocol_lines = [
        (
            f"- HC-{index:03d}: {hook_events[index % len(hook_events)]} result case "
            f"{index:03d} records {result_fields[index % len(result_fields)]}, command "
            "identity, required status, and remediation ownership before context refresh."
        )
        for index in range(1, 1201)
    ]
    write_text(
        module_file(module_dir, "docs", "required-hook-protocol.md"),
        "# Required Hook Protocol\n\n"
        "This handle-only fixture models the verbose hook contract that a compact "
        "resume packet references without loading by default.\n\n"
        + "\n".join(protocol_lines)
        + "\n",
    )
    write_story_bug_templates(module_dir, OUT_OF_SCOPE_DEFERRED_SECTION)
    workflow_run_support.start_workflow_run(tmp, "user-story-workflow", run_id="hook-run")
    run_dir = run_dir_for(module_dir, "hook-run")
    write_text(
        run_file(run_dir, "execution-log.md"),
        common.read_text(run_file(run_dir, "execution-log.md")).replace(
            "- Reusable lesson or `No reusable lesson: <reason>`.",
            "No reusable lesson: hook fixture.",
        ),
    )
    smoke_domain.fill_smoke_domain_outputs(tmp, "user-story-workflow", "hook-run")
    workflow_run_support.context_workflow_run(tmp, "user-story-workflow", run_id="hook-run", write=True)
    update_run_packet(run_dir, external_validation_status="not-required")

    report = workflow_run_support.finish_workflow_run(tmp, "user-story-workflow", run_id="hook-run")

    assert_ok(report)
    assert_true(report, "context_packet_refreshed")
    assert report["context_packet_path"].endswith("artifacts/context/context-packet.json")
    assert_contains_all(report["hook_results"], "event", "workflow-post")
    check = workflow_run_support.context_workflow_run(tmp, "user-story-workflow", run_id="hook-run", check=True)
    assert_ok(check)


def test_repeated_finish_replaces_hook_command_and_evidence_entries(tmp):
    write_fixture(tmp, hooks=True)
    workflow_run_support.start_workflow_run(tmp, "story-flow", run_id="hook-run")
    workflow_run_support.finish_workflow_run(tmp, "story-flow", run_id="hook-run")

    workflow_run_support.finish_workflow_run(tmp, "story-flow", run_id="hook-run")

    run_path = run_file(workflow_run_dir(tmp, run_id="hook-run"))
    packet = read_json(run_path)
    command_keys = [
        (
            item.get("hook_scope"),
            item.get("hook_event"),
            item.get("hook_id"),
            item.get("hook_phase"),
        )
        for item in packet["commands"]
        if isinstance(item, dict) and item.get("hook_id")
    ]
    evidence_keys = [
        (
            item.get("scope"),
            item.get("event"),
            item.get("id"),
            item.get("phase"),
        )
        for item in packet["evidence"]
        if isinstance(item, dict) and item.get("kind") == "workflow-hook"
    ]

    assert len(command_keys) == len(set(command_keys))
    assert len(evidence_keys) == len(set(evidence_keys))


def test_apply_hook_results_preserves_structured_failed_entries(tmp):
    structured_failure = {"name": "known-finding", "reason": "Retained benchmark finding."}
    packet = {
        "checks": {"skipped": [], "blocked": [], "failed": [structured_failure]},
        "failed": [structured_failure],
    }

    workflow_hooks.apply_hook_results(
        tmp,
        packet,
        [
            {
                "id": "hook-a",
                "scope": "workflow",
                "event": "workflow-post",
                "phase": "",
                "required": True,
                "ok": True,
                "status": "ok",
                "command": "noop",
                "returncode": 0,
                "elapsed_seconds": 0,
                "evidence_path": "validation/hooks/workflow-post-hook-a.txt",
            }
        ],
    )

    assert packet["checks"]["failed"] == [structured_failure]
    assert packet["failed"] == [structured_failure]


def test_hook_inspection_lists_hooks_without_executing(tmp):
    write_fixture(tmp, hooks=True)
    write_json(
        workflow_hooks_path(tmp),
        {
            "schema_version": 1,
            "hooks": [
                hook_spec("global-workflow-pre", "workflow-pre")
            ],
        },
    )

    report = workflow_run_support.hooks_workflow_run(
        tmp,
        "story-flow",
        run_id="planned-run",
        event="workflow-pre",
    )

    assert_ok(report)
    assert_false(report, "run_exists")
    assert_fields(report, hook_count=2, required_count=2, events=["workflow-pre"])
    scopes = {item["scope"] for item in report["hooks"]}
    assert scopes == {"global", "workflow"}
    assert_field_set(report["hooks"], "safe", {True})
    assert_field_set(report["hooks"], "would_execute", {True})
    compact = workflow_repo_manager.compact_hooks_run_report(report)
    assert_fields(compact, workflow="story-flow", hook_count=2, required_count=2, unsafe_count=0)
    assert_lacks_all(compact, "hooks")
    assert not workflow_run_dir(tmp, run_id="planned-run").exists()


def test_hooks_all_check_reports_every_workflow_hook_surface(tmp):
    write_fixture(tmp, hooks=True)
    write_workflow(tmp, "plain-flow")
    write_json(
        workflow_hooks_path(tmp),
        {
            "schema_version": 1,
            "hooks": [
                hook_spec("global-workflow-pre", "workflow-pre", hook_audit_command())
            ],
        },
    )

    report = workflow_repo_manager.hooks_all_workflow_runs(tmp, check=True)

    assert_ok(report)
    assert_fields(report, checked_count=2, required_count=12)
    rows = {row["workflow"]: row for row in report["workflows"]}
    assert_fields(rows["story-flow"], hook_count=11, unsafe_count=0)
    assert_fields(rows["plain-flow"], hook_count=1, unsafe_count=0)
    compact = workflow_repo_manager.compact_hooks_all_report(report)
    assert_fields(compact, hook_count=12, required_count=12, unsafe_count=0)
    assert_empty(compact["workflows"])


def test_hook_audit_json_defaults_to_json_evidence_and_rejects_txt(tmp):
    write_fixture(tmp, "plain-flow")
    write_json(
        workflow_hooks_path(tmp),
        {
            "schema_version": 1,
            "hooks": [
                hook_spec("global-workflow-pre", "workflow-pre", hook_audit_command())
            ],
        },
    )

    report = workflow_run_support.hooks_workflow_run(tmp, "plain-flow", run_id="planned-run", event="workflow-pre")

    assert_ok(report)
    assert report["hooks"][0]["evidence_path"].endswith("validation/hooks/workflow-pre-global-workflow-pre.json")

    hooks_path = workflow_hooks_path(tmp)
    manifest = read_json(hooks_path)
    manifest["hooks"][0]["evidence_path"] = "validation/hooks/{event}-{hook_id}.txt"
    write_json(hooks_path, manifest)

    errors, _warnings, _modules = validate_automations.validate_automations(tmp, workflow_name="plain-flow")

    assert_contains(errors, "evidence_path must end with .json for hook-audit JSON output")


def test_hooks_all_check_fails_when_any_workflow_hook_is_unsafe(tmp):
    write_fixture(tmp, "plain-flow")
    write_json(
        workflow_hooks_path(tmp),
        {
            "schema_version": 1,
            "hooks": [
                {
                    "id": "global-unsafe",
                    "event": "workflow-pre",
                    "command": "powershell ./unsafe.ps1",
                    "required": True,
                }
            ],
        },
    )

    report = workflow_repo_manager.hooks_all_workflow_runs(tmp, check=True)

    assert_not_ok(report)
    assert_status(report, "failed")
    assert_field(report, "unsafe_count", 1)
    assert_fields(report["workflows"][0], workflow="plain-flow", unsafe_count=1)


def test_invalid_workflow_hook_contract_is_rejected(tmp):
    module_dir = write_fixture(tmp)
    manifest = workflow_manifest("story-flow")
    manifest["hooks"] = [
        {
            "id": "bad hook",
            "event": "unknown-event",
            "command": "powershell ./unsafe.ps1",
            "required": "yes",
            "timeout_seconds": 0,
        }
    ]
    write_json(module_file(module_dir, "module.json"), manifest)

    errors, _warnings, _modules = validate_automations.validate_automations(tmp, workflow_name="story-flow")
    assert_contains_each(
        errors,
        "hooks[0].id",
        "unknown lifecycle event",
        "hooks[0].command",
        "hooks[0].required",
        "hooks[0].timeout_seconds",
    )


def test_workflow_run_evidence_is_not_external_access_scanned(tmp):
    module_dir = write_fixture(tmp)
    write_text(
        run_file(run_dir_for(module_dir), "validation", "tool-output.txt"),
        "attachments upload network credentials.",
    )

    errors, _warnings, _modules = validate_automations.validate_automations(tmp, workflow_name="story-flow")
    assert_empty(errors)


def test_active_workflow_txt_files_are_rejected_but_run_evidence_is_allowed(tmp):
    module_dir = write_fixture(tmp)
    write_text(module_file(module_dir, "docs", "notes.txt"), "notes.")
    write_text(run_file(run_dir_for(module_dir), "validation", "tool-output.txt"), "Raw command transcript.")

    errors, _warnings, _modules = validate_automations.validate_automations(tmp, workflow_name="story-flow")

    assert_contains_all(errors, "docs/notes.txt", "raw .txt is only allowed under runs/")
    assert_lacks(errors, "validation/tool-output.txt")


def test_workflow_diagrams_folder_is_allowed(tmp):
    module_dir = write_fixture(tmp)
    write_text(module_file(module_dir, "diagrams", "workflow-process.mmd"), MERMAID_START_DONE)
    write_text(module_file(module_dir, "diagrams", "workflow-process.svg"), "<svg></svg>")

    errors, _warnings, _modules = validate_automations.validate_automations(tmp, workflow_name="story-flow")

    assert_lacks(errors, "current workflow layout")


def test_scorecard_accepts_linked_mermaid_diagrams(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    write_text(module_file(module_dir, "diagrams", "workflow-process-diagram.mmd"), MERMAID_START_DONE)
    write_text(module_file(module_dir, "diagrams", "workflow-process-diagram.svg"), "<svg></svg>")
    write_text(module_file(module_dir, "diagrams", "workflow-connection-diagram.mmd"), 'graph TD;\n  A["Workflow"] --> B["Skill"];')
    write_text(module_file(module_dir, "diagrams", "workflow-connection-diagram.svg"), "<svg></svg>")

    report = workflow_scorecard.workflow_scorecard(tmp, "story-flow", run_lifecycle=False)
    diagrams = next(item for item in report["checks"] if item["name"] == "mermaid-diagrams")

    assert_ok(diagrams)
    assert_true(diagrams["details"], "has_process")
    assert_true(diagrams["details"], "has_connection")
    assert diagrams["details"]["mermaid_count"] >= 2
    assert_true(diagrams["details"]["syntax"], "ok")


def test_scorecard_rejects_invalid_mermaid_syntax(tmp):
    module_dir = write_fixture(tmp, with_run=False)
    write_text(module_file(module_dir, "diagrams", "workflow-process-diagram.mmd"), MERMAID_START_DONE)
    write_text(module_file(module_dir, "diagrams", "workflow-process-diagram.svg"), "<svg></svg>")
    write_text(
        module_file(module_dir, "diagrams", "workflow-connection-diagram.mmd"),
        'graph TD;\n  A["runs/<run-id>/run.json"] --> B["Done"];',
    )
    write_text(module_file(module_dir, "diagrams", "workflow-connection-diagram.svg"), "<svg></svg>")

    report = workflow_scorecard.workflow_scorecard(tmp, "story-flow", run_lifecycle=False)
    diagrams = next(item for item in report["checks"] if item["name"] == "mermaid-diagrams")

    assert_not_ok(diagrams)
    assert_false(diagrams["details"]["syntax"], "ok")
    assert_contains(diagrams["details"]["syntax"]["errors"], "HTML label")


def run_test(name, func):
    with tempfile.TemporaryDirectory() as tmp:
        func(Path(tmp))
    print(f"PASS {name}")


def filter_tests(tests, matches):
    if not matches:
        return tests
    needles = [value.lower() for value in matches if value.strip()]
    return [
        test
        for test in tests
        if any(needle in test.__name__.lower() for needle in needles)
    ]


def internal_self_tests():
    return [
        value
        for name, value in globals().items()
        if name.startswith("test_") and callable(value) and name not in UNLISTED_INTERNAL_SELF_TESTS
    ]


def external_self_tests():
    tests_dir = Path(__file__).resolve().parent / "self_tests"
    if not tests_dir.exists():
        return []
    tests = []
    for path in sorted(tests_dir.glob("test_*.py")):
        module_name = f"_workflow_manager_self_tests_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        for name in sorted(dir(module)):
            value = getattr(module, name)
            if name.startswith("test_") and callable(value):
                tests.append(value)
    return tests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match", action="append", default=[], help="run tests whose function name contains this text")
    args = parser.parse_args()
    tests = internal_self_tests() + external_self_tests()
    selected = filter_tests(tests, args.match)
    if args.match and not selected:
        print(f"no self-tests matched: {', '.join(args.match)}", file=sys.stderr)
        return 2
    for test in selected:
        run_test(test.__name__, test)
    if args.match:
        print(f"workflow-manager focused self-tests passed ({len(selected)}/{len(tests)}).")
    else:
        print(FIXTURE_SELF_TEST_RESULT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
