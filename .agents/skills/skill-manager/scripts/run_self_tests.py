#!/usr/bin/env python3
"""Self-tests for the current skill-manager helpers."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import argparse
import importlib.util
from argparse import Namespace
from pathlib import Path

sys.dont_write_bytecode = True

import eval_skill
import analyze_location
import audit_skill_determinism
import attest_skill
import candidate_source_audit
import inspect_skill
import measure_skill_budget
import review_skill_command
import skill_inventory
import repo_manager
from repo_support import repo_benchmark
from repo_support import repo_capability_audit
from repo_support import repo_changed
from repo_support import repo_changed_summary
from repo_support import repo_cli_parser
from repo_support import repo_command_metrics
from repo_support import repo_commands
from repo_support import repo_common
from repo_support import repo_context_guardrails
from repo_support import repo_cost_policy
from repo_support import repo_doctor
from repo_support import repo_doctor_benchmarks
from repo_support import repo_doctor_checks
from repo_support import repo_doctor_clone
from repo_support import repo_doctor_groups
from repo_support import repo_harness_install
from repo_support import repo_health
from repo_support import repo_health_links
from repo_support import repo_health_surface
from repo_support import repo_generated
from repo_support import repo_local_ai
from repo_support import repo_navigation_status
from repo_support import repo_harness_promote
from repo_support import repo_onboarding
from repo_support import repo_optimizations
from repo_support import repo_portable_tools
from repo_support import repo_portability
from repo_support import repo_policy
from project_policy_contract_v2 import v2_cost_policy_from_v1
from repo_support import repo_prevention
from repo_support import repo_proof_hygiene
from repo_support import repo_public_commands
from repo_support import repo_qol
from repo_support import repo_qol_context
from repo_support import repo_qol_daily
from repo_support import repo_qol_dashboard
from repo_support import repo_qol_evidence
from repo_support import repo_qol_finish
from repo_support import repo_qol_github
from repo_support import repo_qol_parsers
from repo_support import repo_qol_readiness
from repo_support import repo_qol_review_loop
from repo_support import repo_review_packet
from repo_support import repo_review_progress
from repo_support import repo_service_config
from repo_support import repo_routing
from repo_support import repo_setup
from repo_support import repo_syntax
import sync_skill_routing
import validate_agent_compatibility
import validate_skill
import skill_manager_common as common
import module_contract_v3


DEMO_DESCRIPTION = "Use when validating skill evals."
DEMO_SELF_TEST_OUTPUT = "ok"
INSTALL_WIZARD_TARGET = "python -B .agents/manage.py install-wizard --target"
OPEN_TARGET_PROJECT = "Open the target project."
HARNESS_EDITED_AFTER_INSTALL = "target file was edited after last harness install"
PROJECT_CONTEXT_CHECK = "project-context --target . --check"
STORY_WORKFLOW_START = "workflow start --name user-story-workflow"
STORY_WORKFLOW_RESUME = "workflow resume --name user-story-workflow"
REPO_CHECK_COMMAND = "python -B .agents/manage.py check"
FALSE_VALIDATION_CLAIM = "claimed validation passed without evidence"
SKILLS_HARNESS_MD = "# Skills\n"
REPO_INSTRUCTIONS_MD = "# Repo\n"
START_HERE_MD = "# Start\n"
GENERATED_MARKER = "# generated"
LOCAL_DETERMINISTIC_GATES = "local deterministic gates"
ARCH_ENGINEERING_CATEGORY = "Architecture And Engineering"
BENCHMARK_RUN_JSON = {"schema_version": 2, "ok": True}
BENCHMARK_WORKFLOW = "agent-benchmarking"
COMPACT_JSON = ["--summary", "--compact", "--format", "json"]
SUMMARY_COMPACT = {"summary": True, "compact": True}
JSON_OUT = {"output_format": "json"}
COMPACT_JSON_EXPECTED = {**SUMMARY_COMPACT, **JSON_OUT}
SKILL_MANAGER_PATH = ".agents/skills/skill-manager"
MANAGE_PY = ".agents/manage.py"
NEW_PROJECT_TARGET = "D:/Projects/NewProject"
VALIDATE_SKILLS_WORKFLOW = """name: Validate skills

on:
  workflow_dispatch:
"""
HARNESS_ACTIVE_FILES = {
    "AGENTS.md": REPO_INSTRUCTIONS_MD,
    "README.md": SKILLS_HARNESS_MD,
    ".editorconfig": "root=true\n",
    ".gitattributes": "* text\n",
    ".gitignore": "__pycache__/\n**/bin/\n**/obj/\n",
    ".aider.conf.yml": "read: AGENTS.md\n",
    "GEMINI.md": "@AGENTS.md\n",
    ".agents/manage.py": "print('x')\n",
    ".agents/routing.md": "#\n",
    ".agents/skills/demo/SKILL.md": "# Demo\n",
    ".agents/local-ai/policy.json": "{}\n",
    "automations/demo/WORKFLOW.md": "# Demo\n",
    "docs/agent-start.md": "#\n",
    "docs/start-here.md": "#\n",
    ".github/copilot-instructions.md": "#\n",
    ".claude/CLAUDE.md": "#\n",
    ".continue/rules/repository-instructions.md": "#\n",
}
HARNESS_STATE_FILES = {
    ".agents/local-ai/cache/state.json": "{}\n",
    ".agents/local-ai/bundle/model.gguf": "state\n",
    ".agents/tools/cache/ripgrep/windows-x64/install.json": "{}\n",
    ".agents/local-ai/secrets.local.json": "{}\n",
    ".agents/local-ai/local.settings.json": "{}\n",
    ".agents/local-ai/project.settings.json": "{}\n",
    ".agents/harness.lock.json": "{}\n",
    ".agents/harness-install.json": "{}\n",
    ".agents/harness-install-plan.json": "{}\n",
    ".agents/harness-install-plan.md": "# Plan\n",
    ".agents/harness-smoke-target.json": "{}\n",
    "automations/demo/runs/run-a/run.json": "{}\n",
}


def install_wizard_apply_command(target, profile):
    return f'{INSTALL_WIZARD_TARGET} "{target.resolve(strict=False)}" --profile {profile} --apply'


def assert_parsed(parser, args, expected):
    parsed = parser.parse_args(args)
    for name, value in expected.items():
        assert getattr(parsed, name) == value


def assert_ok(result):
    assert result.get("ok") is True, result


def assert_not_ok(result):
    assert result.get("ok") is False, result


def assert_true(result, field):
    assert result[field] is True, result


def assert_false(result, field):
    assert result[field] is False, result


def assert_status(result, status):
    assert_field(result, "status", status)


def assert_tool(result, tool):
    assert_field(result, "tool", tool)


def assert_name(result, name):
    assert_field(result, "name", name)


def assert_field(result, field, expected):
    assert result[field] == expected, result


def assert_fields(result, **expected):
    for field, value in expected.items():
        assert_field(result, field, value)


def assert_exists(path):
    assert path.exists(), path


def assert_missing(path):
    assert not path.exists(), path


def assert_empty(value):
    assert value == [], value


def assert_none(value):
    assert value is None, value


def assert_not_none(value):
    assert value is not None, value


def assert_summary(result, **expected):
    assert_fields(result["summary"], **expected)


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


def assert_keys_lack(mapping, *keys):
    for key in keys:
        assert key not in mapping, mapping


def assert_same_attrs(left, right, *names):
    for name in names:
        assert getattr(left, name) is getattr(right, name)


def record_command(calls, command):
    calls.append([str(item) for item in command])


def captured_command_result(command, output_tail=""):
    return {"ok": True, "status": 0, "command": " ".join(command), "output_tail": output_tail}


def read_feedback_lines(root):
    path = root / ".agents" / "local-ai" / "cache" / "feedback" / "failure-feedback.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def captured_manage_result(args):
    return {
        "command": f"python -B {MANAGE_PY} " + " ".join(args),
        "ok": True,
        "status": "passed",
        "returncode": 0,
        "output_tail": "",
    }


def assert_manage_command_ran(commands, *args):
    assert [MANAGE_PY, *args] in [command[2:] for command in commands]


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def agent_path(root, *parts):
    return root.joinpath(".agents", *parts)


def skill_root(root, name="demo-skill"):
    return agent_path(root, "skills", name)


def skill_path(skill_dir, *parts):
    return skill_dir.joinpath(*parts)


def skill_md(skill_dir):
    return skill_path(skill_dir, "SKILL.md")


def local_ai_config(root):
    return agent_path(root, "local-ai.json")


def local_ai_policy(root):
    return agent_path(root, "local-ai", "policy.json")


def doc_path(root, *parts):
    return root.joinpath("docs", *parts)


def automation_path(root, *parts):
    return root.joinpath("automations", *parts)


def write_files(root, files):
    for relative, text in files.items():
        write_text(root / relative, text)


def completed_workflow_run(workflow, run_id="run-a", **extra):
    packet = {"schema_version": 2, "workflow": workflow, "run_id": run_id, "status": "completed"}
    packet.update(extra)
    return packet


def write_workflow_run(root, workflow, run_id="run-a", **extra):
    run_dir = automation_path(root, workflow, "runs", run_id)
    write_json(run_dir / "run.json", completed_workflow_run(workflow, run_id, **extra))
    return run_dir


def benchmark_dir(root, child):
    return automation_path(root, BENCHMARK_WORKFLOW, child)


def write_benchmark_suite(suites, suite, task):
    write_json(suites / f"{suite}.json", {"schema_version": 1, "suite": suite, "tasks": [task]})


def write_benchmark_result(run_dir, report):
    write_json(run_dir / "benchmark-result.json", report)
    write_json(run_dir / "run.json", BENCHMARK_RUN_JSON)


def write_candidate_skill(candidate, name, description, title, body=""):
    write_text(
        skill_md(candidate / "skills" / name),
        f"""---
name: {name}
description: {description}
---

# {title}

{body}
""",
    )


def registry_entry(root):
    registry = sync_skill_routing.build_registry_data(
        root,
        max_files=200,
        max_text_files=100,
        deep=False,
        use_local_ai=False,
    )
    return registry["skills"][0]


def addition_report(root, paths):
    selected = [paths] if isinstance(paths, str) else paths
    return repo_changed.addition_acceptance_report(root, paths=selected, new_paths=selected)


def capture_json(func, *args):
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        status = func(*args)
    return status, json.loads(stdout.getvalue())


def parse_help(parser, args):
    stream = io.StringIO()
    try:
        with contextlib.redirect_stdout(stream):
            parser.parse_args(args)
    except SystemExit as exc:
        assert exc.code == 0
    return stream.getvalue()


def workflow_group_command(root, args):
    captured = {}

    def fake_run(_root, command):
        captured["command"] = command
        return 0

    with patched_attrs(repo_doctor_groups.repo, run_workflow_repo_manager=fake_run):
        rc = repo_doctor_groups.workflow_group(Namespace(workflow_args=args), root)
    return rc, captured["command"]


@contextlib.contextmanager
def patched_attrs(target, **values):
    missing = object()
    originals = {name: getattr(target, name, missing) for name in values}
    for name, value in values.items():
        setattr(target, name, value)
    try:
        yield
    finally:
        for name, value in originals.items():
            if value is missing:
                delattr(target, name)
            else:
                setattr(target, name, value)


def finish_report_with_commands(root, output_by_args=None, *, release_full=False):
    commands = []
    changed = [
        "AGENTS.md",
        ".agents/skills/skill-manager/assets/example.txt",
        "automations/demo-flow/WORKFLOW.md",
        "automations/agent-benchmarking/WORKFLOW.md",
    ]

    def fake_run_capture(_root, command, *, timeout=90):
        commands.append(command)
        return captured_command_result(command, (output_by_args or {}).get(tuple(command[2:]), ""))

    with patched_attrs(
        repo_qol,
        run_capture=fake_run_capture,
        navigation_status=lambda _root: {"status": "fresh"},
        auto_refresh_navigation=lambda _root: {"ok": True, "status": "skipped-fresh"},
    ), patched_attrs(
        repo_changed,
        changed_files=lambda _root: list(changed),
    ):
        report = repo_qol.finish_work_report(
            root,
            deep=not release_full,
            release_full=release_full,
            skip_benchmark=not release_full,
        )
    return report, commands


def module_contract(name="demo-skill"):
    return {
        "schema_version": 3,
        "kind": "skill",
        "id": name,
        "version": "1.0.0",
        "summary": "Demo.",
        "owners": ["engineering"],
        "inputs": ["SKILL.md", "module.json"],
        "outputs": ["suites/demo-evals.json"],
        "commands": [
            {
                "id": "review-skill",
                "argv": [
                    "python",
                    "-B",
                    ".agents/manage.py",
                    "review",
                    "--skill",
                    f".agents/skills/{name}",
                ],
                "timeout_seconds": 300,
                "working_directory": "repository",
                "effects": [],
            }
        ],
        "related_modules": ["skill-manager"],
        "validation": [f"python -B .agents/manage.py eval-skill --skill .agents/skills/{name} --suite .agents/skills/{name}/suites/demo-evals.json"],
        "compatibility": {
            "codex": "required",
            "github_copilot": "required",
            "claude_code": "required",
        },
        "external_access": {
            "source_systems": [],
            "credential_expectations": "none",
            "data_copied_locally": [],
            "attachments_retrieved": False,
        },
        "local_ai": {"use_cases": []},
        "quality": {
            "eval_suites": [
                {
                    "path": "suites/demo-evals.json",
                    "purpose": "Deterministic fixture validation.",
                }
            ]
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
        "strict_read_only_commands": [],
        "extensions": {},
    }


def write_skill(root, name="demo-skill"):
    skill_dir = skill_root(root, name)
    write_text(
        skill_md(skill_dir),
        f"""---
name: {name}
description: {DEMO_DESCRIPTION}
---

# Demo Skill

## Workflow

Validate.

## Completion Contract

Report.

## Stop Rules

Stop.
""",
    )
    write_json(skill_path(skill_dir, "module.json"), module_contract(name))
    write_text(
        skill_path(skill_dir, "scripts", "run_self_tests.py"),
        f"""#!/usr/bin/env python3
print("{DEMO_SELF_TEST_OUTPUT}")
""",
    )
    write_json(
        skill_path(skill_dir, "suites", "demo-evals.json"),
        {
            "evals": [
                {
                    "id": "module-contract",
                    "assertions": [
                        {"type": "validation_ok"},
                        {"type": "file_exists", "path": "module.json"},
                        {"type": "file_absent", "path": "skill" + ".json"},
                        {"type": "file_contains", "path": "SKILL.md", "text": "Demo Skill"},
                        {"type": "manifest_field_equals", "path": "schema_version", "value": 3},
                        {"type": "manifest_field_equals", "path": "kind", "value": "skill"},
                        {
                            "type": "python_script_succeeds",
                            "path": "scripts/run_self_tests.py",
                            "output_contains": DEMO_SELF_TEST_OUTPUT,
                        },
                        {"type": "risk_profile_covers_flags"},
                        {"type": "trigger_quality"},
                    ],
                }
            ]
        },
    )
    return skill_dir


def test_current_skill_contract_validates(tmp):
    skill_dir = write_skill(tmp)
    errors, warnings = validate_skill.validate_skill(skill_dir)
    assert_empty(errors)
    assert_lacks(warnings, "skill" + ".json")


def test_v3_skill_validates_and_rejects_unknown_core(tmp):
    skill_dir = write_skill(tmp)
    manifest = module_contract("demo-skill")
    normalized, adapter_errors, _adapter_warnings = module_contract_v3.normalize_module_contract(manifest)
    assert_empty(adapter_errors)
    write_json(skill_path(skill_dir, "module.json"), normalized)

    errors, warnings = validate_skill.validate_skill(skill_dir)
    assert_empty(errors)

    normalized["owner_hint"] = "engineering"
    write_json(skill_path(skill_dir, "module.json"), normalized)
    strict_errors, _strict_warnings = validate_skill.validate_skill(skill_dir)
    assert_contains(strict_errors, "owner_hint")


def test_compact_local_ai_use_case_ids_validate(tmp):
    skill_dir = write_skill(tmp)
    manifest = module_contract("demo-skill")
    manifest["local_ai"] = {"use_cases": ["validation-triage", "changed-files-summary"]}
    write_json(skill_path(skill_dir, "module.json"), manifest)
    errors, warnings = validate_skill.validate_skill(skill_dir)
    summary = common.local_ai_use_case_summary(manifest)
    fallback_warnings = audit_skill_determinism.fallback_mentions(manifest)
    assert_empty(errors)
    assert_lacks(warnings, "local_ai")
    assert_fields(summary, use_case_count=2, use_cases=[
        "validation-triage",
        "changed-files-summary",
    ])
    assert_empty(fallback_warnings)


def write_cost_policy_fixture(tmp, policy=None):
    write_files(
        tmp,
        {
            "AGENTS.md": "# Repo Instructions\n",
            "README.md": "# Repo\n",
            "docs/start-here.md": START_HERE_MD,
            "docs/operations/daily-agent-path.md": "# Daily Agent Path\n",
            ".agents/routing.md": "# Skill Routing\n",
            "automations/routing.md": "# Workflow Routing\n",
            "automations/navigation/artifacts/maps/HANDOFF.md": "# Handoff\n\nRead this compact file first.\n",
            "automations/navigation/artifacts/maps/NAVIGATION.md": "# Navigation\n\n" + "route map " * 120,
            "automations/navigation/artifacts/maps/TECHNICAL_CONTEXT.md": "# Technical Context\n\n" + "technical signal " * 160,
            "automations/navigation/artifacts/maps/CONVENTIONS.md": "# Conventions\n\n" + "convention signal " * 120,
        },
    )
    write_json(
        local_ai_config(tmp),
        {
            "enabled": True,
            "tasks": sorted(repo_cost_policy.LOCAL_AI_TASKS),
        },
    )
    project_policy = repo_policy.default_policy_document()
    project_policy["cost_policy"] = v2_cost_policy_from_v1(policy or repo_cost_policy.default_cost_policy())
    write_json(tmp / repo_policy.PROJECT_POLICY_PATH, project_policy)


def test_cost_policy_prefers_deterministic_validation_and_compact_context(tmp):
    write_cost_policy_fixture(tmp)
    report = repo_cost_policy.cost_policy_report(tmp, compact=True)
    validation_route = next(row for row in report["task_routes"] if row["id"] == "validation")
    handoff_route = next(row for row in report["task_routes"] if row["id"] == "handoff")

    assert_ok(report)
    assert_fields(
        report["policy"]["runtime_profile"],
        local_ai_preferred=True,
        compact_outputs=True,
        owner_first_retrieval=True,
    )
    assert_true(report["low_context"], "within_budget")
    assert_true(report["beginner_context"], "within_budget")
    assert report["summary"]["beginner_loaded_tokens"] > 0
    assert_field(report["summary"], "routine_skips_beginner_tokens", report["summary"]["beginner_loaded_tokens"])
    assert_field(report["summary"], "guidance_status", "measurably-better")
    assert report["summary"]["guidance_saved_tokens_estimated"] > 0
    assert_true(report["guidance_savings"], "use_by_default")
    assert_true(report["guidance_savings"], "meets_minimum")
    assert_true(report["token_savings"]["savings_controls"], "owner_first_retrieval")
    assert_fields(report["local_ai_warm_batch"], enabled=True, min_items=2)
    assert_field(validation_route, "prefer", "deterministic")
    assert_lacks_all(validation_route["local_ai_use_cases"], "validation-triage")
    assert_has_all(validation_route["local_ai_use_cases"], "failure-cluster")
    assert_field(handoff_route, "paid_model_fallback", "disabled-by-default")
    assert_contains(report["recommendations"], "exact search first")
    assert_contains_all(report["recommendations"], "id", "warm-server-batch")
    warm_batch = next(row for row in report["recommendations"] if row["id"] == "warm-server-batch")
    assert_contains(warm_batch["commands"], "changed-files-summary")
    assert_lacks_all(warm_batch["commands"], "validation-triage")


def test_default_cost_policy_keeps_routine_context_under_budget(tmp):
    policy = repo_cost_policy.default_cost_policy()
    assert_field(policy, "always_loaded_budget_tokens", 3500)
    assert_true(policy, "default_guidance_required")
    assert_lacks_all(policy["always_loaded_files"], "docs/start-here.md", "docs/operations/daily-agent-path.md")
    assert_lacks_all(policy["default_guidance_files"], "README.md", "docs/start-here.md")
    assert_has_all(policy["default_guidance_files"], "automations/navigation/artifacts/maps/HANDOFF.md")
    assert policy["phase_budgets"]["evidence"] == 12_000
    assert_field(policy["task_routes"]["validation"], "prefer", "deterministic")
    assert_lacks_all(policy["task_routes"]["validation"]["local_ai_use_cases"], "validation-triage")
    assert_lacks_all(policy["warm_server_batch"]["prefer_for_tasks"], "validation-triage")
    assert_has_all(policy["broad_guidance_baseline_files"], "automations/navigation/artifacts/maps/TECHNICAL_CONTEXT.md")
    assert_has_all(policy["beginner_loaded_files"], "docs/start-here.md")
    assert_fields(
        policy["review_loop"],
        max_units=20,
        max_estimated_tokens=8000,
        max_elapsed_ms=180000,
        max_hunks_per_batch=12,
    )


def test_delegation_balanced_gate_defaults_and_validation_are_strict(tmp):
    policy = repo_cost_policy.default_cost_policy()
    gate = policy["delegation_gates"]["delegation-balanced-v1"]
    assert_fields(
        gate,
        quality_noninferior=True,
        minimum_median_wall_time_improvement_percent=20,
        maximum_median_provider_token_increase_percent=25,
        minimum_trials_per_arm=3,
        maximum_tokens_per_trial=80000,
        maximum_seconds_per_trial=600,
        required_token_provenance="provider_telemetry",
        fallback="single-agent",
    )

    invalid = repo_cost_policy.default_cost_policy()
    invalid["delegation_gates"]["delegation-balanced-v1"].update(
        {
            "minimum_trials_per_arm": 1,
        }
    )
    write_cost_policy_fixture(tmp, invalid)
    report = repo_cost_policy.cost_policy_report(tmp, compact=True)

    assert_not_ok(report)
    assert_contains(report["issues"], "minimum_trials_per_arm must be at least 3")


def test_cost_policy_rejects_invalid_review_loop_defaults(tmp):
    policy = repo_cost_policy.default_cost_policy()
    policy["review_loop"] = {
        "max_units": 0,
        "max_estimated_tokens": 0,
        "max_elapsed_ms": -1,
        "max_hunks_per_batch": 0,
    }
    write_cost_policy_fixture(tmp, policy)

    report = repo_cost_policy.cost_policy_report(tmp, compact=True)

    assert_field(report, "status", "failed")
    assert_contains(report["issues"], "cost_policy.review.loop.max_units")
    assert_contains(report["issues"], "cost_policy.review.loop.max_estimated_tokens")
    assert_contains(report["issues"], "cost_policy.review.loop.max_elapsed_ms")
    assert_contains(report["issues"], "cost_policy.review.loop.max_hunks_per_batch")


def test_review_cost_ledger_measures_owner_packet_reduction(tmp):
    ledger = repo_cost_policy.review_cost_ledger(
        {
            "tool": "skill-manager.large-diff-review-packet",
            "review_budget_tokens": 5000,
            "changed_diff_estimated_tokens": 12000,
            "owner_review_packets": [
                {"owner": "skill:skill-manager", "estimated_changed_tokens": 4500},
                {"owner": "workflow:navigation", "estimated_changed_tokens": 1500},
            ],
        }
    )

    assert_fields(
        ledger,
        status="measured",
        review_budget_exceeded=True,
        release_gate="needs-owner-review",
        comparison_scope="all-owner-packets",
        raw_changed_diff_estimated_tokens=12000,
        largest_owner_packet_estimated_tokens=4500,
        review_unit_count=2,
        review_units_estimated_tokens_total=6000,
        single_agent_saved_tokens_vs_raw_estimated=7500,
        single_agent_saved_percent_vs_raw_estimated=62.5,
        all_review_units_saved_tokens_vs_raw_estimated=6000,
        all_review_units_saved_percent_vs_raw_estimated=50.0,
        all_owner_packets_delta_tokens_vs_raw_estimated=-6000,
        all_review_units_delta_tokens_vs_raw_estimated=-6000,
    )
    assert_has_all(ledger["billing_boundary"], "Excludes output tokens", "provider prices")


def test_changed_diff_estimate_counts_staged_and_untracked_files(tmp):
    subprocess.run(["git", "init"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp, check=True)
    write_text(tmp / "tracked.txt", "one\n")
    write_text(tmp / "staged.txt", "one\n")
    subprocess.run(["git", "add", "."], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    write_text(tmp / "tracked.txt", "one\ntwo\n")
    write_text(tmp / "staged.txt", "one\ntwo\n")
    subprocess.run(["git", "add", "staged.txt"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    write_text(tmp / "new-helper.py", "print('hello')\n" * 20)

    estimate = repo_cost_policy.changed_diff_estimate(tmp)

    assert_field(estimate, "tracked_files", 2)
    assert_field(estimate, "untracked_files", 1)
    assert estimate["tracked_estimated_tokens"] > 0
    assert estimate["untracked_estimated_tokens"] > 0
    assert_field(
        estimate,
        "estimated_tokens",
        estimate["tracked_estimated_tokens"] + estimate["untracked_estimated_tokens"],
    )


def test_changed_file_statuses_parses_porcelain_and_fallback(tmp):
    subprocess.run(["git", "init"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp, check=True)
    write_text(tmp / "modified.txt", "one\n")
    write_text(tmp / "staged.txt", "one\n")
    write_text(tmp / "renamed-old.txt", "one\n")
    write_text(tmp / "_candidate-imports" / "ignored.txt", "ignored\n")
    subprocess.run(["git", "add", "."], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    write_text(tmp / "modified.txt", "one\ntwo\n")
    write_text(tmp / "staged.txt", "one\ntwo\n")
    subprocess.run(["git", "add", "staged.txt"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "mv", "renamed-old.txt", "renamed-new.txt"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    write_text(tmp / "untracked.txt", "new\n")
    write_text(tmp / "_candidate-imports" / "ignored-new.txt", "ignored\n")

    statuses = repo_changed.changed_file_statuses(tmp)

    assert_field(statuses, "modified.txt", {"M"})
    assert_field(statuses, "staged.txt", {"M"})
    assert_field(statuses, "renamed-new.txt", {"R"})
    assert_field(statuses, "untracked.txt", {"?"})
    assert "_candidate-imports/ignored-new.txt" not in statuses
    assert repo_changed.changed_files(tmp) == sorted(statuses)

    calls: list[tuple[str, ...]] = []

    def fake_git_output(_root, *args):
        calls.append(args)
        if args == ("diff", "--name-status"):
            return 0, ["M\tfallback-modified.txt"]
        if args == ("diff", "--cached", "--name-status"):
            return 0, ["A\tfallback-staged.txt"]
        if args == ("ls-files", "--others", "--exclude-standard"):
            return 0, ["fallback-untracked.txt", "_candidate-imports/fallback-ignored.txt"]
        return 1, []

    with patched_attrs(repo_changed.repo_changed_git.subprocess, run=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("git missing"))), patched_attrs(
        repo_changed.repo_changed_git.repo,
        git_output=fake_git_output,
    ):
        fallback = repo_changed.changed_file_statuses(tmp)

    assert_field(fallback, "fallback-modified.txt", {"M"})
    assert_field(fallback, "fallback-staged.txt", {"A"})
    assert_field(fallback, "fallback-untracked.txt", {"?"})
    assert "_candidate-imports/fallback-ignored.txt" not in fallback
    assert calls == [
        ("diff", "--name-status"),
        ("diff", "--cached", "--name-status"),
        ("ls-files", "--others", "--exclude-standard"),
    ], calls


def test_changed_path_token_estimates_prefers_single_head_numstat(tmp):
    calls: list[tuple[str, ...]] = []

    def fake_git_output(_root, *args):
        calls.append(args)
        if args == ("diff", "HEAD", "--numstat"):
            return 0, ["2\t1\ttracked.py"]
        return 1, []

    with patched_attrs(repo_changed.repo, git_output=fake_git_output):
        estimates = repo_changed.changed_path_token_estimates(
            tmp,
            ["tracked.py"],
            statuses={"tracked.py": {"M"}},
        )

    assert_field(estimates["tracked.py"], "added", 2)
    assert_field(estimates["tracked.py"], "deleted", 1)
    assert_field(estimates["tracked.py"], "tracked_estimated_tokens", 36)
    assert calls == [("diff", "HEAD", "--numstat")], calls


def test_diff_hunk_ranges_prefers_single_head_diff(tmp):
    calls: list[tuple[str, ...]] = []

    def fake_git_output(_root, *args):
        calls.append(args)
        if args == ("diff", "HEAD", "--unified=0", "--", "tracked.py"):
            return 0, ["@@ -1 +1,2 @@", "+new"]
        return 1, []

    with patched_attrs(repo_changed.repo, git_output=fake_git_output):
        ranges = repo_changed.diff_hunk_ranges(tmp, "tracked.py")

    assert ranges == [
        {
            "old_start": 1,
            "old_count": 1,
            "new_start": 1,
            "new_count": 2,
            "line_count": 2,
        }
    ]
    assert calls == [("diff", "HEAD", "--unified=0", "--", "tracked.py")], calls


def test_startup_context_report_tracks_policy_files_and_baseline(tmp):
    write_cost_policy_fixture(tmp)
    subprocess.run(["git", "init"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    write_text(tmp / "AGENTS.md", "# Repo Instructions\n\nUse the compact daily path before broad reading.\n")

    report = repo_qol_daily.startup_context_report(tmp, baseline_ref="HEAD")
    compact = repo_qol_daily.summarize_startup_context_report(report, compact=True)

    assert_ok(report)
    assert_tool(report, "repo-startup-context")
    assert_true(report["summary"], "always_within_budget")
    assert_true(report["summary"], "beginner_within_budget")
    assert report["summary"]["always_loaded_tokens"] > 0
    assert_field(report["summary"], "guidance_status", "measurably-better")
    assert report["guidance_savings"]["saved_tokens_estimated"] > 0
    assert_true(report["guidance_savings"], "use_by_default")
    assert report["baseline"]["always_delta_tokens"] > 0
    assert_has_all([item["path"] for item in report["top_files"]], "AGENTS.md")
    assert "guidance_savings" in compact
    assert_keys_lack(compact, "top_files")
    assert_field(compact["latency_budget"], "budget_ms", 2500)
    assert compact["latency_budget"]["status"] in {"within-budget", "slow-components", "over-budget"}, compact["latency_budget"]
    assert compact["latency_budget"]["elapsed_ms"] >= 0
    assert_field(compact["output_budget"], "budget_tokens", 1600)
    assert_field(compact["output_budget"], "scope", "summary-compact-json-estimate")
    assert_field(compact, "next_command_reason", "Re-run after low-context routing or policy changes to refresh the startup budget evidence.")


def test_startup_context_uses_fast_navigation_status(tmp):
    write_cost_policy_fixture(tmp)
    calls: list[bool] = []

    def fake_navigation_status(_root, *, fast):
        calls.append(fast)
        return {
            "status": "fresh",
            "reason": "fresh-navigation-staleness-cache",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "Navigation maps are fresh from the fast cache.",
        }

    with patched_attrs(repo_qol_daily, navigation_status=fake_navigation_status):
        report = repo_qol_daily.startup_context_report(tmp)

    assert calls == [True], calls
    assert_field(report["navigation"], "reason", "fresh-navigation-staleness-cache")
    assert_field(report["summary"], "navigation_status", "fresh")


def test_startup_context_component_budget_keeps_dirty_navigation_margin(_tmp):
    report = repo_command_metrics.timing_budget_report(
        "startup-context",
        2000,
        timings=[{"name": "navigation_status", "elapsed_ms": 1400}],
    )

    assert_field(report, "status", "within-budget")
    assert_field(report, "component_budget_ms", 1500)


def test_startup_context_report_fails_when_baseline_growth_exceeds_policy(tmp):
    policy = repo_cost_policy.default_cost_policy()
    policy["startup_context_max_added_tokens"] = 0
    policy["startup_context_max_added_percent"] = 0
    write_cost_policy_fixture(tmp, policy)
    subprocess.run(["git", "init"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    write_text(
        tmp / "AGENTS.md",
        "# Repo Instructions\n\n"
        + "Use low-context routing before reading broad docs.\n" * 40,
    )

    report = repo_qol_daily.startup_context_report(tmp, baseline_ref="HEAD")
    compact = repo_qol_daily.summarize_startup_context_report(report, compact=True)

    assert_not_ok(report)
    assert_field(report["baseline_regression"], "status", "regressed")
    assert_contains(report["issues"], "startup context increased beyond policy")
    assert_field(compact["summary"], "baseline_regression_status", "regressed")
    assert "baseline_regression" in compact


def test_clean_context_proof_identifies_handoff_from_agents_and_startup_context(tmp):
    write_text(
        tmp / "AGENTS.md",
        "# Repo Instructions\n\n"
        "Route first to `.agents/routing.md`, `automations/routing.md`, and "
        "`automations/navigation/artifacts/maps/HANDOFF.md` when present; "
        "raw navigation JSON is tool-only. Final claims use "
        "`python -B .agents/manage.py finish --summary --compact --format json`.\n",
    )
    write_text(tmp / "automations" / "navigation" / "artifacts" / "maps" / "HANDOFF.md", "# Handoff\n")

    report = repo_qol_daily.clean_context_proof_report(tmp)
    compact = repo_qol_daily.summarize_clean_context_proof_report(report, compact=True)

    assert_ok(report)
    assert_field(report["agent_packet"], "source_orientation", "automations/navigation/artifacts/maps/HANDOFF.md")
    assert_field(report["agent_packet"], "raw_navigation_json", "tool-only")
    assert_has_all(report["agent_packet"]["completion_command"], "finish", "--summary", "--compact")
    assert_field(compact["summary"], "source_orientation", "automations/navigation/artifacts/maps/HANDOFF.md")


def test_context_cost_benchmark_compares_raw_startup_and_next_action_routes(tmp):
    write_text(tmp / "AGENTS.md", "Route first to HANDOFF.md and next-action.\n")
    write_text(
        tmp / "automations" / "navigation" / "artifacts" / "maps" / "HANDOFF.md",
        "# Handoff\n\nRead compact route data first.\n",
    )
    write_text(tmp / "src" / "feature.py", "print('focused source')\n")

    startup = {
        "schema_version": 1,
        "tool": "repo-startup-context",
        "ok": True,
        "status": "passed",
        "summary": {
            "default_guidance_tokens": 1000,
            "guidance_saved_tokens_estimated": 5000,
            "guidance_saved_percent_estimated": 83.33,
        },
        "navigation": {"status": "fresh"},
        "guidance_savings": {},
        "baseline_regression": {"status": "not-run"},
        "next_command": "python -B .agents/manage.py startup-context --summary --compact --format json",
    }
    next_action = {
        "schema_version": 1,
        "tool": "skill-manager.next-action",
        "ok": True,
        "status": "ready",
        "next_command": "python -B .agents/manage.py review-packet --owner skill:demo --summary --compact --format json",
        "why": "Changed diff exceeds the review budget.",
        "required_context": [
            "automations/navigation/artifacts/maps/HANDOFF.md",
            "src/feature.py",
        ],
        "validation_after": "python -B .agents/manage.py review-progress --mark-command \"...\"",
        "stop_condition": "Stop on failed next command.",
        "navigation": {"status": "fresh", "read_first": "automations/navigation/artifacts/maps/HANDOFF.md"},
        "review_progress": {"status": "needs-review"},
        "local_ai_route": {"status": "advisory-only"},
    }

    with patched_attrs(
        repo_qol.repo_cost_policy,
        changed_diff_estimate=lambda root: {"estimated_tokens": 12000, "tracked_estimated_tokens": 12000, "untracked_estimated_tokens": 0},
    ), patched_attrs(
        repo_qol,
        startup_context_report=lambda root, compact=True: startup,
        next_action_report=lambda root, fast=True: next_action,
    ):
        report = repo_qol.context_cost_benchmark_report(tmp, min_saved_percent=40.0)
        recorded = repo_qol.context_cost_benchmark_report(
            tmp,
            min_saved_percent=40.0,
            record=True,
            history_path=".agents/local-ai/cache/test-context-cost-ledger.jsonl",
        )
        default_history = ".agents/local-ai/cache/default-context-cost-ledger.jsonl"
        default_status, default_payload = capture_json(
            repo_qol.handle_qol_command,
            Namespace(
                command="context-cost-benchmark",
                min_saved_percent=40.0,
                record=False,
                no_record=False,
                history=default_history,
                summary=True,
                compact=True,
                output_format="json",
            ),
            tmp,
        )

    compact = repo_qol.summarize_context_cost_benchmark_report(report, compact=True)

    assert default_status == 0
    assert_field(default_payload["history"], "recorded", False)
    assert not (tmp / default_history).exists()
    assert_ok(report)
    assert_field(report, "status", "measurably-better")
    assert_field(report["comparison"], "raw_diff_input_tokens", 12000)
    assert_field(report["comparison"], "money_saving_status", "potentially-cheaper-at-4x-output")
    assert report["comparison"]["selected_route_input_tokens"] < 12000
    assert report["comparison"]["selected_route_output_tokens"] > 0
    assert_field(
        report["comparison"]["output_break_even_extra_tokens"],
        "output_price_multiplier_4x",
        report["comparison"]["saved_input_tokens_vs_raw"] // 4,
    )
    assert "not provider billing telemetry" in report["boundary"]
    assert_field(compact, "use_by_default", True)
    assert "src/feature.py" not in json.dumps(compact)
    assert_field(recorded["history"], "recorded", True)
    assert_field(recorded["history"], "entry_count", 1)
    assert (tmp / ".agents/local-ai/cache/test-context-cost-ledger.jsonl").is_file()


def test_review_loop_marks_successful_review_packets_only(tmp):
    commands = [
        "python -B .agents/manage.py review-packet --owner skill:demo --hunk h001 --summary --compact --format json",
        "python -B .agents/manage.py check-changed --summary --compact --format json",
    ]
    marked: list[str] = []

    def fake_next_action(_root, *, fast=True):
        command = commands[0] if not marked else commands[1]
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": command,
            "review_progress": {"status": "needs-review", "stale": False},
        }

    def fake_progress(_root, *, mark_unit_id="", mark_command="", note="", reset=False, state_path=None):
        marked.append(mark_command)
        return {
            "ok": True,
            "status": "in-progress",
            "completed_unit_count": len(marked),
            "pending_unit_count": 1,
            "current_unit": {},
            "next_pending_command": commands[1],
        }

    def fake_run(_root, command, *, timeout=120):
        return {
            "ok": True,
            "status": 0,
            "command": command,
            "elapsed_seconds": 0.01,
            "timeout_seconds": timeout,
            "output_summary": {"bytes": 2, "lines": 1, "digest": "ok"},
            "raw_output_path": ".agents/local-ai/cache/command-output/review-loop-demo.txt",
        }

    with patched_attrs(
        repo_qol,
        next_action_report=fake_next_action,
        current_review_progress_report=fake_progress,
        run_capture_shell=fake_run,
    ):
        report = repo_qol.review_loop_report(tmp, max_units=3)

    assert_ok(report)
    assert_field(report, "status", "needs-validation")
    assert_field(report, "executed_unit_count", 1)
    assert marked == [commands[0]], marked
    assert_has_all(report["next_command"], "check-changed")
    assert_field(report["progress_delta"], "completed_before", 0)
    assert_field(report["progress_delta"], "completed_after", 1)
    assert_field(report["progress_delta"], "completed_delta", 1)
    assert_field(report["progress_delta"], "raw_output_path_count", 1)
    assert_field(report, "raw_output_paths", [".agents/local-ai/cache/command-output/review-loop-demo.txt"])


def test_review_loop_exact_unit_boundary_reports_complete(tmp):
    command = "python -B .agents/manage.py review-packet --owner skill:demo --hunk h001 --summary --compact --format json"
    marked: list[str] = []

    def fake_next_action(_root, *, fast=True):
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": "none, repo is healthy" if marked else command,
            "review_progress": {
                "status": "complete" if marked else "needs-review",
                "stale": False,
                "completed_unit_count": len(marked),
                "pending_unit_count": 0 if marked else 1,
            },
        }

    def fake_progress(_root, *, mark_unit_id="", mark_command="", note="", reset=False, state_path=None):
        marked.append(mark_command)
        return {"ok": True, "status": "complete", "completed_unit_count": len(marked), "pending_unit_count": 0}

    def fake_run(_root, command, *, timeout=120):
        return {
            "ok": True,
            "status": 0,
            "command": command,
            "elapsed_seconds": 0.01,
            "timeout_seconds": timeout,
            "output_summary": {"bytes": 2, "lines": 1, "digest": "ok"},
        }

    report = repo_qol_context.review_loop_report(
        tmp,
        max_units=1,
        next_action_factory=fake_next_action,
        progress_factory=fake_progress,
        runner=fake_run,
    )

    assert_ok(report)
    assert_field(report, "status", "complete")
    assert_field(report, "executed_unit_count", 1)
    assert_has_all(report["next_command"], "none")


def test_review_loop_exact_unit_boundary_reports_needs_validation(tmp):
    commands = [
        "python -B .agents/manage.py review-packet --owner skill:demo --hunk h001 --summary --compact --format json",
        "python -B .agents/manage.py check-changed --summary --compact --format json",
    ]
    marked: list[str] = []

    def fake_next_action(_root, *, fast=True):
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": commands[1] if marked else commands[0],
            "review_progress": {
                "status": "in-progress",
                "stale": False,
                "completed_unit_count": len(marked),
                "pending_unit_count": 1,
            },
        }

    def fake_progress(_root, *, mark_unit_id="", mark_command="", note="", reset=False, state_path=None):
        marked.append(mark_command)
        return {"ok": True, "status": "in-progress", "completed_unit_count": len(marked), "pending_unit_count": 1}

    def fake_run(_root, command, *, timeout=120):
        return {
            "ok": True,
            "status": 0,
            "command": command,
            "elapsed_seconds": 0.01,
            "timeout_seconds": timeout,
            "output_summary": {"bytes": 2, "lines": 1, "digest": "ok"},
        }

    report = repo_qol_context.review_loop_report(
        tmp,
        max_units=1,
        next_action_factory=fake_next_action,
        progress_factory=fake_progress,
        runner=fake_run,
    )

    assert_ok(report)
    assert_field(report, "status", "needs-validation")
    assert_field(report, "executed_unit_count", 1)
    assert_has_all(report["next_command"], "check-changed")


def test_review_loop_stops_before_exceeding_estimated_token_cap(tmp):
    commands = [
        "python -B .agents/manage.py review-packet --owner skill:demo --path one.py --summary --compact --format json",
        "python -B .agents/manage.py review-packet --owner skill:demo --path two.py --summary --compact --format json",
    ]
    estimates = [400, 700]
    marked: list[str] = []

    def fake_next_action(_root, *, fast=True):
        index = min(len(marked), len(commands) - 1)
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": commands[index],
            "review_progress": {
                "status": "needs-review",
                "stale": False,
                "current_unit": {"estimated_changed_tokens": estimates[index]},
            },
        }

    def fake_progress(_root, *, mark_unit_id="", mark_command="", note="", reset=False, state_path=None):
        marked.append(mark_command)
        return {
            "ok": True,
            "status": "in-progress",
            "completed_unit_count": len(marked),
            "pending_unit_count": 1,
            "current_unit": {"estimated_changed_tokens": estimates[min(len(marked), len(estimates) - 1)]},
            "next_pending_command": commands[min(len(marked), len(commands) - 1)],
        }

    def fake_run(_root, command, *, timeout=120):
        return {
            "ok": True,
            "status": 0,
            "command": command,
            "elapsed_seconds": 0.01,
            "timeout_seconds": timeout,
            "output_summary": {"bytes": 2, "lines": 1, "digest": "ok"},
        }

    with patched_attrs(
        repo_qol,
        next_action_report=fake_next_action,
        current_review_progress_report=fake_progress,
        run_capture_shell=fake_run,
    ):
        report = repo_qol.review_loop_report(tmp, max_units=3, max_estimated_tokens=800)

    compact = repo_qol.summarize_review_loop_report(report, compact=True)

    assert_field(report, "status", "token-limit")
    assert_field(report, "executed_unit_count", 1)
    assert_field(report, "estimated_review_tokens", 400)
    assert marked == [commands[0]], marked
    assert_field(compact, "estimated_review_tokens", 400)
    assert_field(compact, "max_estimated_tokens", 800)
    assert_has_all(compact["iterations"][1]["reason"], "estimated review token cap")


def test_review_loop_dry_run_reports_effective_default_caps_and_budget_envelope(tmp):
    units = [
        {
            "id": "u1",
            "scope": "hunk",
            "owner": "skill:demo",
            "path": "src/demo.py",
            "hunk": "h001",
            "estimated_changed_tokens": 4000,
            "command": "python -B .agents/manage.py review-packet --owner skill:demo --path src/demo.py --hunk h001 --summary --compact --format json",
        },
        {
            "id": "u2",
            "scope": "hunk",
            "owner": "skill:demo",
            "path": "src/demo.py",
            "hunk": "h002",
            "estimated_changed_tokens": 3900,
            "command": "python -B .agents/manage.py review-packet --owner skill:demo --path src/demo.py --hunk h002 --summary --compact --format json",
        },
        {
            "id": "u3",
            "scope": "hunk",
            "owner": "skill:demo",
            "path": "src/demo.py",
            "hunk": "h003",
            "estimated_changed_tokens": 3000,
            "command": "python -B .agents/manage.py review-packet --owner skill:demo --path src/demo.py --hunk h003 --summary --compact --format json",
        },
    ]

    def fake_next_action(_root, *, fast=True):
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": units[0]["command"],
            "review_progress": {
                "status": "needs-review",
                "stale": False,
                "current_unit": {"estimated_changed_tokens": units[0]["estimated_changed_tokens"]},
            },
        }

    def fake_progress(_root, **_kwargs):
        return {"ok": True, "status": "needs-review", "completed_units": []}

    def fake_plan(_root):
        return {"review_plan": {"review_units": units, "validation_units": []}}

    parser = repo_cli_parser.build_parser()
    args = parser.parse_args(
        [
            "review-loop", "--dry-run", "--include-validation", "--max-units", "3",
            "--summary", "--compact", "--format", "json",
        ]
    )
    assert_field(vars(args), "include_validation", True)
    assert_field(vars(args), "max_estimated_tokens", repo_review_progress.DEFAULT_REVIEW_LOOP_MAX_ESTIMATED_TOKENS)
    assert_field(vars(args), "max_elapsed_ms", repo_review_progress.DEFAULT_REVIEW_LOOP_MAX_ELAPSED_MS)

    report = repo_qol_review_loop.review_loop_report(
        tmp,
        max_units=args.max_units,
        max_estimated_tokens=args.max_estimated_tokens,
        max_elapsed_ms=args.max_elapsed_ms,
        dry_run=True,
        next_action_factory=fake_next_action,
        progress_factory=fake_progress,
        plan_factory=fake_plan,
    )
    compact = repo_qol_context.summarize_review_loop_report(report, compact=True)

    assert_field(report, "max_estimated_tokens", repo_review_progress.DEFAULT_REVIEW_LOOP_MAX_ESTIMATED_TOKENS)
    assert_field(report, "max_elapsed_ms", repo_review_progress.DEFAULT_REVIEW_LOOP_MAX_ELAPSED_MS)
    assert_field(report["forecast"], "max_estimated_tokens", repo_review_progress.DEFAULT_REVIEW_LOOP_MAX_ESTIMATED_TOKENS)
    assert_field(report, "planned_unit_count", 2)
    assert_field(report, "planned_estimated_tokens", 7900)
    assert_field(compact["latency_budget"], "command", "review-loop")
    assert_field(compact["output_budget"], "command", "review-loop")
    assert_field(compact["output_budget"], "status", "within-budget")


def test_review_loop_failed_packet_does_not_mark_or_capture_raw_output_path(tmp):
    command = "python -B .agents/manage.py review-packet --owner skill:demo --hunk h001 --summary --compact --format json"
    marked: list[str] = []

    def fake_next_action(_root, *, fast=True):
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": command,
            "review_progress": {
                "status": "needs-review",
                "stale": False,
                "completed_unit_count": 0,
                "pending_unit_count": 2,
                "review_state": "initial",
                "current_unit": {"estimated_changed_tokens": 100},
            },
        }

    def fake_progress(_root, *, mark_unit_id="", mark_command="", note="", reset=False, state_path=None):
        marked.append(mark_command)
        return {"ok": True, "status": "complete"}

    def fake_run(_root, command, *, timeout=120):
        return {
            "ok": False,
            "status": 2,
            "command": command,
            "elapsed_seconds": 0.01,
            "timeout_seconds": timeout,
            "output_summary": {"bytes": 9, "lines": 1, "digest": "failed"},
            "raw_output_path": ".agents/local-ai/cache/command-output/failed-review-packet.txt",
            "distilled_output": "review packet failed",
        }

    report = repo_qol_context.review_loop_report(
        tmp,
        max_units=1,
        next_action_factory=fake_next_action,
        progress_factory=fake_progress,
        runner=fake_run,
    )
    compact = repo_qol_context.summarize_review_loop_report(report, compact=True)

    assert_not_ok(report)
    assert_field(report, "status", "failed")
    assert_field(report, "executed_unit_count", 0)
    assert_field(report, "raw_output_paths", [])
    assert_field(report["progress_delta"], "raw_output_path_count", 0)
    assert_field(report["progress_delta"], "completed_delta", 0)
    assert marked == [], marked
    assert_field(compact, "raw_output_paths", [])


def test_review_loop_expands_autopilot_next_action_to_pending_review_packet(tmp):
    loop_command = repo_review_progress.default_review_loop_command()
    packet_command = "python -B .agents/manage.py review-packet --owner skill:demo --summary --compact --format json"
    marked: list[str] = []
    ran: list[str] = []

    def fake_next_action(_root, *, fast=True):
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": loop_command,
            "review_progress": {
                "status": "needs-review",
                "stale": False,
                "current_unit": {"estimated_changed_tokens": 120},
                "next_pending_command": packet_command,
            },
        }

    def fake_progress(_root, *, mark_unit_id="", mark_command="", note="", reset=False, state_path=None):
        marked.append(mark_command)
        return {
            "ok": True,
            "status": "complete",
            "completed_unit_count": len(marked),
            "pending_unit_count": 0,
            "current_unit": {},
            "next_pending_command": "",
        }

    def fake_run(_root, command, *, timeout=120):
        ran.append(command)
        return {
            "ok": True,
            "status": 0,
            "command": command,
            "elapsed_seconds": 0.01,
            "timeout_seconds": timeout,
            "output_summary": {"bytes": 2, "lines": 1, "digest": "ok"},
        }

    with patched_attrs(
        repo_qol,
        next_action_report=fake_next_action,
        current_review_progress_report=fake_progress,
        run_capture_shell=fake_run,
    ):
        report = repo_qol.review_loop_report(tmp, max_units=1, reset_stale=True)

    assert_ok(report)
    assert_field(report, "executed_unit_count", 1)
    assert ran == [packet_command], ran
    assert marked == [packet_command], marked
    assert_has_all(report["iterations"][0]["next_command"], "review-packet --owner skill:demo")


def test_review_loop_prefers_pending_review_packet_before_validation_command(tmp):
    packet_command = "python -B .agents/manage.py review-packet --owner skill:demo --summary --compact --format json"
    validation_command = "python -B .agents/manage.py check-additions"
    marked: list[str] = []
    ran: list[str] = []

    def fake_next_action(_root, *, fast=True):
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": validation_command,
            "review_progress": {
                "status": "needs-review",
                "stale": False,
                "current_unit": {"estimated_changed_tokens": 120},
                "next_pending_command": packet_command,
            },
        }

    def fake_progress(_root, *, mark_unit_id="", mark_command="", note="", reset=False, state_path=None):
        marked.append(mark_command)
        return {
            "ok": True,
            "status": "complete",
            "completed_unit_count": len(marked),
            "pending_unit_count": 0,
            "current_unit": {},
            "next_pending_command": "",
        }

    def fake_run(_root, command, *, timeout=120):
        ran.append(command)
        return {
            "ok": True,
            "status": 0,
            "command": command,
            "elapsed_seconds": 0.01,
            "timeout_seconds": timeout,
            "output_summary": {"bytes": 2, "lines": 1, "digest": "ok"},
        }

    with patched_attrs(
        repo_qol,
        next_action_report=fake_next_action,
        current_review_progress_report=fake_progress,
        run_capture_shell=fake_run,
    ):
        report = repo_qol.review_loop_report(tmp, max_units=1, include_validation=True)

    assert_ok(report)
    assert ran == [packet_command], ran
    assert marked == [packet_command], marked
    assert_has_all(report["iterations"][0]["next_command"], "review-packet --owner skill:demo")


def test_review_loop_runs_untracked_validation_once_and_routes_declared_follow_up(tmp):
    validation_command = "python -B .agents/manage.py check-additions"
    follow_up = "python -B .agents/manage.py check-changed --summary --compact --format json"
    ran: list[str] = []

    def fake_next_action(_root, *, fast=True):
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": validation_command,
            "validation_after": follow_up,
            "review_progress": {
                "status": "complete",
                "review_state": "complete",
                "stale": False,
                "current_unit": {},
                "next_pending_command": "",
            },
        }

    def fail_progress(*_args, **_kwargs):
        raise AssertionError("untracked validation must not be marked as a review-plan unit")

    def fake_run(_root, command, *, timeout=120):
        ran.append(command)
        return {
            "ok": True,
            "status": 0,
            "command": command,
            "elapsed_seconds": 0.01,
            "timeout_seconds": timeout,
            "output_summary": {"bytes": 2, "lines": 1, "digest": "ok"},
        }

    report = repo_qol_context.review_loop_report(
        tmp,
        max_units=20,
        include_validation=True,
        next_action_factory=fake_next_action,
        progress_factory=fail_progress,
        runner=fake_run,
    )

    assert_ok(report)
    assert_field(report, "status", "needs-validation")
    assert_field(report, "executed_unit_count", 1)
    assert_field(report, "next_command", follow_up)
    assert ran == [validation_command], ran
    assert_field(report["iterations"][0], "progress_tracking", "not-applicable")


def test_review_loop_dry_run_does_not_reset_stale_progress(tmp):
    loop_command = repo_review_progress.default_review_loop_command()
    packet_command = "python -B .agents/manage.py review-packet --owner skill:demo --summary --compact --format json"
    reset_calls: list[dict[str, str]] = []

    def fake_next_action(_root, *, fast=True):
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": loop_command,
            "review_progress": {
                "status": "stale",
                "review_state": "stale",
                "stale": True,
                "fingerprint_digest": "digest-123",
                "state_path": ".agents/local-ai/cache/review-progress.json",
                "current_unit": {"estimated_changed_tokens": 120},
                "next_pending_command": packet_command,
            },
        }

    def fake_reset(_root, *, fingerprint_digest, state_path=None, note=""):
        reset_calls.append({"fingerprint_digest": fingerprint_digest, "state_path": state_path or "", "note": note})
        return {"ok": True, "status": "needs-review", "stale": False}

    with patched_attrs(repo_qol_context.repo_review_progress, reset_review_progress_state=fake_reset):
        report = repo_qol_context.review_loop_report(
            tmp,
            max_units=1,
            dry_run=True,
            reset_stale=True,
            next_action_factory=fake_next_action,
        )

    assert_ok(report)
    assert_field(report, "status", "planned")
    assert_field(report, "stale_reset_count", 0)
    assert_field(report, "stale_reset_planned_count", 1)
    assert_field(report["progress_delta"], "raw_output_path_count", 0)
    assert_field(report, "raw_output_paths", [])
    assert reset_calls == [], reset_calls
    assert_field(report["iterations"][0], "stale_reset", "planned")
    assert_has_all(report["iterations"][0]["next_command"], "review-packet --owner skill:demo")


def test_review_loop_command_no_reset_stale_stops_on_stale_progress(tmp):
    parser = repo_cli_parser.build_parser()
    args = parser.parse_args(["review-loop", "--no-reset-stale", "--summary", "--compact", "--format", "json"])
    packet_command = "python -B .agents/manage.py review-packet --owner skill:demo --summary --compact --format json"

    def fake_next_action(_root, *, fast=True):
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": repo_review_progress.default_review_loop_command(),
            "review_progress": {
                "status": "stale",
                "review_state": "stale",
                "stale": True,
                "current_unit": {"estimated_changed_tokens": 120},
                "next_pending_command": packet_command,
            },
        }

    with patched_attrs(repo_qol, next_action_report=fake_next_action):
        status, payload = capture_json(repo_qol.handle_qol_command, args, tmp)

    assert status == 1
    assert_field(payload, "status", "stale")
    assert_field(payload, "stale_reset_count", 0)
    assert_field(payload["iterations"][0], "status", "stale")
    assert_has_all(payload["iterations"][0]["next_command"], "review-packet --owner skill:demo")


def test_review_loop_dry_run_uses_multi_unit_forecast_without_writes(tmp):
    loop_command = repo_review_progress.default_review_loop_command()
    commands = [
        f"python -B .agents/manage.py review-packet --owner skill:demo --hunk h00{index} --summary --compact --format json"
        for index in range(1, 4)
    ]

    def fake_next_action(_root, *, fast=True):
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": loop_command,
            "review_progress": {
                "status": "needs-review",
                "stale": False,
                "current_unit": {"estimated_changed_tokens": 100},
                "next_pending_command": commands[0],
            },
            "review_autopilot": {
                "status": "default",
                "forecast": {
                    "status": "planned",
                    "planned_unit_count": 3,
                    "planned_estimated_tokens": 600,
                    "remaining_review_units": 9,
                    "projected_loop_count": 3,
                    "planned_units": [
                        {"command": commands[0], "estimated_changed_tokens": 100},
                        {"command": commands[1], "estimated_changed_tokens": 200},
                        {"command": commands[2], "estimated_changed_tokens": 300},
                    ],
                },
            },
        }

    def fail_progress(*_args, **_kwargs):
        raise AssertionError("dry-run forecast must not mark or rebuild review progress")

    report = repo_qol_context.review_loop_report(
        tmp,
        max_units=3,
        dry_run=True,
        next_action_factory=fake_next_action,
        progress_factory=fail_progress,
    )
    compact = repo_qol_context.summarize_review_loop_report(report, compact=True)

    assert_ok(report)
    assert_field(report, "status", "planned")
    assert_field(report, "planned_unit_count", 3)
    assert_field(report, "planned_estimated_tokens", 600)
    assert_field(report["forecast"], "projected_loop_count", 3)
    assert len(report["iterations"]) == 3
    assert [item["next_command"] for item in report["iterations"]] == commands
    assert_field(compact, "planned_unit_count", 3)
    assert_field(compact, "planned_estimated_tokens", 600)


def test_review_loop_dry_run_forecast_honors_explicit_command_limits(tmp):
    loop_command = repo_review_progress.default_review_loop_command()
    hunk_packets = [
        {
            "path": "src/demo.py",
            "hunk": f"h{index:03d}",
            "estimated_changed_tokens": 1000,
            "next_command": (
                "python -B .agents/manage.py review-packet "
                f"--owner skill:demo --path src/demo.py --hunk h{index:03d} "
                "--summary --compact --format json"
            ),
        }
        for index in range(1, 7)
    ]
    review_packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_diff_estimated_tokens": 10000,
        "review_budget_tokens": 5000,
        "review_batch_max_hunks": 1,
        "owner_review_packets": [
            {
                "owner": "skill:demo",
                "scope": "owner",
                "estimated_changed_tokens": 6000,
                "owner_review_subpackets": [
                    {
                        "path": "src/demo.py",
                        "estimated_changed_tokens": 6000,
                        "path_review_hunks": hunk_packets,
                    }
                ],
            }
        ],
    }
    context = {
        "changed": ["src/demo.py"],
        "scope": {},
        "validation_plan": [],
        "navigation": {},
        "review_packet": review_packet,
        "input_fingerprint": {"digest": "digest-a"},
        "review_plan": repo_review_progress.build_review_plan(review_packet),
    }

    def fake_next_action(_root, *, fast=True):
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": loop_command,
            "review_progress": {
                "status": "needs-review",
                "stale": False,
                "current_unit": {"estimated_changed_tokens": 1000},
                "next_pending_command": hunk_packets[0]["next_command"],
            },
            "review_autopilot": {
                "status": "default",
                "forecast": {
                    "status": "planned",
                    "max_units": 20,
                    "max_estimated_tokens": 8000,
                    "planned_unit_count": 6,
                    "planned_estimated_tokens": 6000,
                    "planned_units": [
                        {"command": packet["next_command"], "estimated_changed_tokens": 1000}
                        for packet in hunk_packets
                    ],
                },
            },
        }

    with patched_attrs(repo_qol_context, current_review_plan_packet=lambda root: context):
        report = repo_qol_context.review_loop_report(
            tmp,
            max_units=2,
            max_estimated_tokens=2500,
            dry_run=True,
            next_action_factory=fake_next_action,
        )

    assert_ok(report)
    assert_field(report, "planned_unit_count", 2)
    assert_field(report, "planned_estimated_tokens", 2000)
    assert_field(report["forecast"], "max_units", 2)
    assert_field(report["forecast"], "max_estimated_tokens", 2500)
    assert len(report["iterations"]) == 2
    assert_has_all(report["next_command"], "--hunk h003")


def test_review_loop_dry_run_rebuilds_inconsistent_cached_forecast(tmp):
    loop_command = repo_review_progress.default_review_loop_command()
    commands = [
        (
            "python -B .agents/manage.py review-packet "
            f"--owner skill:demo --path src/demo.py --hunk h{index:03d} "
            "--summary --compact --format json"
        )
        for index in range(1, 4)
    ]
    hunk_packets = [
        {
            "path": "src/demo.py",
            "hunk": f"h{index:03d}",
            "estimated_changed_tokens": 1000,
            "next_command": commands[index - 1],
        }
        for index in range(1, 4)
    ]
    review_packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_diff_estimated_tokens": 10000,
        "review_budget_tokens": 5000,
        "review_batch_max_hunks": 1,
        "owner_review_packets": [
            {
                "owner": "skill:demo",
                "scope": "owner",
                "estimated_changed_tokens": 3000,
                "owner_review_subpackets": [
                    {
                        "path": "src/demo.py",
                        "estimated_changed_tokens": 3000,
                        "path_review_hunks": hunk_packets,
                    }
                ],
            }
        ],
    }
    context = {
        "changed": ["src/demo.py"],
        "scope": {},
        "validation_plan": [],
        "navigation": {},
        "review_packet": review_packet,
        "input_fingerprint": {"digest": "digest-a"},
        "review_plan": repo_review_progress.build_review_plan(review_packet),
    }

    def fake_next_action(_root, *, fast=True):
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": loop_command,
            "review_progress": {
                "status": "needs-review",
                "stale": False,
                "current_unit": {"estimated_changed_tokens": 1000},
                "next_pending_command": commands[0],
            },
            "review_autopilot": {
                "status": "default",
                "forecast": {
                    "status": "planned",
                    "max_units": 1,
                    "max_estimated_tokens": 8000,
                    "planned_unit_count": 1,
                    "planned_estimated_tokens": 1000,
                    "planned_units": [
                        {"command": commands[0], "estimated_changed_tokens": 1000},
                        {"command": commands[1], "estimated_changed_tokens": 1000},
                        {"command": commands[2], "estimated_changed_tokens": 1000},
                    ],
                },
            },
        }

    with patched_attrs(repo_qol_context, current_review_plan_packet=lambda root: context):
        report = repo_qol_context.review_loop_report(
            tmp,
            max_units=1,
            max_estimated_tokens=8000,
            dry_run=True,
            next_action_factory=fake_next_action,
        )

    assert_ok(report)
    assert_field(report, "planned_unit_count", 1)
    assert_field(report, "planned_estimated_tokens", 1000)
    assert len(report["iterations"]) == 1
    assert_has_all(report["next_command"], "--hunk h002")


def test_review_next_runs_one_review_unit_with_review_next_tool(tmp):
    commands = [
        "python -B .agents/manage.py review-packet --owner skill:demo --summary --compact --format json",
        "python -B .agents/manage.py check-changed --summary --compact --format json",
    ]
    marked: list[str] = []

    def fake_next_action(_root, *, fast=True):
        command = commands[0] if not marked else commands[1]
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": command,
            "review_progress": {"status": "needs-review", "stale": False},
        }

    def fake_progress(_root, *, mark_unit_id="", mark_command="", note="", reset=False, state_path=None):
        marked.append(mark_command)
        return {
            "ok": True,
            "status": "in-progress",
            "completed_unit_count": len(marked),
            "pending_unit_count": 1,
            "current_unit": {},
            "next_pending_command": commands[1],
        }

    def fake_run(_root, command, *, timeout=120):
        return {
            "ok": True,
            "status": 0,
            "command": command,
            "elapsed_seconds": 0.01,
            "timeout_seconds": timeout,
            "output_summary": {"bytes": 2, "lines": 1, "digest": "ok"},
        }

    with patched_attrs(
        repo_qol,
        next_action_report=fake_next_action,
        current_review_progress_report=fake_progress,
        run_capture_shell=fake_run,
    ):
        report = repo_qol.review_next_report(tmp)

    compact = repo_qol.summarize_review_next_report(report, compact=True)

    assert_ok(report)
    assert_field(report, "tool", "skill-manager.review-next")
    assert_field(report, "status", "passed")
    assert marked == [commands[0]], marked
    assert_field(compact, "executed", True)
    assert_has_all(compact["next_command"], "check-changed")


def test_review_next_summary_reports_latency_and_output_budget(tmp):
    command = "python -B .agents/manage.py review-packet --owner skill:demo --summary --compact --format json"

    def fake_next_action(_root, *, fast=True):
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": command,
            "review_progress": {"status": "needs-review", "stale": False},
        }

    with patched_attrs(repo_qol, next_action_report=fake_next_action):
        report = repo_qol.review_next_report(tmp, dry_run=True)

    compact = repo_qol.summarize_review_next_report(report, compact=True)

    assert_field(compact["latency_budget"], "command", "review-next")
    assert_field(compact["latency_budget"], "budget_ms", 12000)
    assert_field(compact["output_budget"], "command", "review-next")
    assert_field(compact["output_budget"], "budget_tokens", 900)
    assert_field(compact["output_budget"], "scope", "summary-compact-json-estimate")


def test_review_next_dry_run_plans_stale_reset_without_writing(tmp):
    command = "python -B .agents/manage.py review-packet --owner skill:demo --summary --compact --format json"
    reset_calls: list[bool] = []

    def fake_next_action(_root, *, fast=True):
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": command,
            "review_progress": {"status": "stale", "stale": True},
        }

    def fake_progress(_root, *, mark_unit_id="", mark_command="", note="", reset=False, state_path=None):
        reset_calls.append(reset)
        return {
            "ok": True,
            "status": "needs-review",
            "completed_unit_count": 0,
            "pending_unit_count": 1,
            "current_unit": {},
            "next_pending_command": command,
        }

    with patched_attrs(
        repo_qol,
        next_action_report=fake_next_action,
        current_review_progress_report=fake_progress,
    ):
        report = repo_qol.review_next_report(tmp, dry_run=True)

    assert_ok(report)
    assert_field(report, "status", "planned")
    assert_field(report, "stale_reset_count", 0)
    assert_field(report, "stale_reset_planned_count", 1)
    assert reset_calls == [], reset_calls
    assert_field(report["iteration"], "stale_reset", "planned")
    assert_has_all(report["next_command"], "review-packet")


def test_review_next_dry_run_plans_fingerprint_reset_without_writing(tmp):
    command = "python -B .agents/manage.py review-packet --owner skill:demo --summary --compact --format json"
    next_calls: list[bool] = []
    resets: list[dict[str, str]] = []

    def fake_next_action(_root, *, fast=True):
        next_calls.append(fast)
        return {
            "schema_version": 1,
            "tool": "skill-manager.next-action",
            "ok": True,
            "status": "ready",
            "next_command": command,
            "review_progress": {
                "status": "stale",
                "review_state": "stale",
                "stale": True,
                "fingerprint_digest": "digest-123",
                "state_path": ".agents/local-ai/cache/review-progress.json",
                "next_pending_command": command,
            },
        }

    def fail_progress(*_args, **_kwargs):
        raise AssertionError("review-next should reset from next-action fingerprint without rebuilding progress")

    def fake_reset(_root, *, fingerprint_digest, state_path=None, note=""):
        resets.append({"fingerprint_digest": fingerprint_digest, "state_path": state_path or "", "note": note})
        return {
            "ok": True,
            "status": "needs-review",
            "stale": False,
            "state_path": state_path or "",
            "fingerprint_digest": fingerprint_digest,
        }

    with patched_attrs(repo_qol_context.repo_review_progress, reset_review_progress_state=fake_reset):
        report = repo_qol_context.review_next_report(
            tmp,
            dry_run=True,
            next_action_factory=fake_next_action,
            progress_factory=fail_progress,
        )

    assert_ok(report)
    assert_field(report, "status", "planned")
    assert next_calls == [True], next_calls
    assert_field(report, "stale_reset_count", 0)
    assert_field(report, "stale_reset_planned_count", 1)
    assert resets == [], resets
    assert_field(report["iteration"], "stale_reset", "planned")
    assert_has_all(report["next_command"], "review-packet")


def test_review_autopilot_runs_review_loop_until_finish_supports_completion(tmp):
    completion_calls = {"count": 0}
    loop_calls: list[dict[str, int]] = []

    def fake_completion(_root, *, deep=False, budget_intent="off"):
        completion_calls["count"] += 1
        if completion_calls["count"] == 1:
            return {
                "ok": False,
                "status": "blocked",
                "completion_supported": False,
                "gates": {"pending_review_unit_count": 2, "failed_check_count": 0},
                "next_command": repo_review_progress.default_review_loop_command(),
            }
        return {
            "ok": True,
            "status": "completion-supported",
            "completion_supported": True,
            "gates": {"pending_review_unit_count": 0, "failed_check_count": 0},
            "next_command": "none, completion packet supports final claim",
        }

    def fake_loop(_root, **kwargs):
        loop_calls.append(dict(kwargs))
        return {
            "ok": True,
            "status": "complete",
            "executed_unit_count": 2,
            "estimated_review_tokens": 700,
            "progress_delta": {"completed_delta": 2, "pending_after": 0},
            "next_command": "python -B .agents/manage.py finish --summary --compact --format json",
            "iterations": [{"index": 1, "status": "passed"}, {"index": 2, "status": "passed"}],
        }

    report = repo_qol_context.review_autopilot_report(
        tmp,
        max_cycles=3,
        max_units_per_cycle=5,
        max_total_units=10,
        max_estimated_tokens=2000,
        completion_factory=fake_completion,
        loop_factory=fake_loop,
    )
    compact = repo_qol_context.summarize_review_autopilot_report(report, compact=True)

    assert_ok(report)
    assert_field(report, "status", "completion-supported")
    assert_true(report, "completion_supported")
    assert_field(report, "cycle_count", 1)
    assert_field(report, "executed_unit_count", 2)
    assert_field(report, "estimated_review_tokens", 700)
    assert completion_calls["count"] == 2, completion_calls
    assert_field(loop_calls[0], "max_units", 5)
    assert_field(loop_calls[0], "max_estimated_tokens", 2000)
    assert_field(compact["latency_budget"], "command", "review-autopilot")
    assert_field(compact["output_budget"], "command", "review-autopilot")
    assert_has_all(compact["next_command"], "none")


def test_review_autopilot_accepts_finish_routing_to_autopilot(tmp):
    completion_calls = {"count": 0}

    def fake_completion(_root, *, deep=False, budget_intent="off"):
        completion_calls["count"] += 1
        if completion_calls["count"] == 1:
            return {
                "ok": False,
                "status": "needs-review-autopilot",
                "completion_supported": False,
                "gates": {"pending_review_unit_count": 1, "failed_check_count": 0},
                "next_command": (
                    "python -B .agents/manage.py review-autopilot "
                    "--max-cycles 1 --summary --compact --format json"
                ),
            }
        return {
            "ok": True,
            "status": "completion-supported",
            "completion_supported": True,
            "gates": {"pending_review_unit_count": 0, "failed_check_count": 0},
            "next_command": "none, completion packet supports final claim",
        }

    def fake_loop(_root, **_kwargs):
        return {
            "ok": True,
            "status": "complete",
            "executed_unit_count": 1,
            "estimated_review_tokens": 100,
            "progress_delta": {"completed_delta": 1, "pending_after": 0},
            "next_command": "python -B .agents/manage.py finish --summary --compact --format json",
            "iterations": [{"index": 1, "status": "passed"}],
        }

    report = repo_qol_context.review_autopilot_report(
        tmp,
        max_cycles=1,
        max_units_per_cycle=1,
        max_total_units=1,
        completion_factory=fake_completion,
        loop_factory=fake_loop,
    )

    assert_ok(report)
    assert_field(report, "status", "completion-supported")
    assert_field(report, "cycle_count", 1)
    assert_field(report, "executed_unit_count", 1)


def test_review_autopilot_accepts_finish_routing_to_review_packet(tmp):
    completion_calls = {"count": 0}

    def fake_completion(_root, *, deep=False, budget_intent="off"):
        completion_calls["count"] += 1
        return {
            "ok": completion_calls["count"] > 1,
            "status": "completion-supported" if completion_calls["count"] > 1 else "needs-review-autopilot",
            "completion_supported": completion_calls["count"] > 1,
            "gates": {
                "pending_review_unit_count": 0 if completion_calls["count"] > 1 else 1,
                "failed_check_count": 0,
            },
            "next_command": (
                "none, completion packet supports final claim"
                if completion_calls["count"] > 1
                else "python -B .agents/manage.py review-packet --owner skill:example --summary --compact --format json"
            ),
        }

    def fake_loop(_root, **_kwargs):
        return {
            "ok": True,
            "status": "complete",
            "executed_unit_count": 1,
            "estimated_review_tokens": 100,
            "progress_delta": {"completed_delta": 1, "pending_after": 0},
            "next_command": "python -B .agents/manage.py finish --summary --compact --format json",
            "iterations": [{"index": 1, "status": "passed"}],
        }

    report = repo_qol_context.review_autopilot_report(
        tmp,
        max_cycles=1,
        max_units_per_cycle=1,
        max_total_units=1,
        completion_factory=fake_completion,
        loop_factory=fake_loop,
    )

    assert_ok(report)
    assert_field(report, "status", "completion-supported")
    assert_field(report, "cycle_count", 1)
    assert_field(report, "executed_unit_count", 1)


def test_review_autopilot_stops_before_total_unit_cap(tmp):
    def fake_completion(_root, *, deep=False, budget_intent="off"):
        return {
            "ok": False,
            "status": "blocked",
            "completion_supported": False,
            "gates": {"pending_review_unit_count": 5, "failed_check_count": 0},
            "next_command": repo_review_progress.default_review_loop_command(),
        }

    def fake_loop(_root, **_kwargs):
        return {
            "ok": True,
            "status": "limit-reached",
            "executed_unit_count": 1,
            "estimated_review_tokens": 100,
            "progress_delta": {"completed_delta": 1, "pending_after": 4},
            "next_command": repo_review_progress.default_review_loop_command(max_units=1),
            "iterations": [{"index": 1, "status": "passed"}],
        }

    report = repo_qol_context.review_autopilot_report(
        tmp,
        max_cycles=5,
        max_units_per_cycle=3,
        max_total_units=1,
        completion_factory=fake_completion,
        loop_factory=fake_loop,
    )

    assert_ok(report)
    assert_field(report, "status", "unit-limit")
    assert_field(report, "executed_unit_count", 1)
    assert_has_all(report["next_command"], "review-loop")


def test_review_autopilot_propagates_loop_elapsed_limit(tmp):
    def fake_completion(_root, *, deep=False, budget_intent="off"):
        return {
            "ok": False,
            "status": "blocked",
            "completion_supported": False,
            "gates": {"pending_review_unit_count": 5, "failed_check_count": 0},
            "next_command": repo_review_progress.default_review_loop_command(),
        }

    def fake_loop(_root, **_kwargs):
        return {
            "ok": True,
            "status": "elapsed-limit",
            "executed_unit_count": 2,
            "estimated_review_tokens": 500,
            "progress_delta": {"completed_delta": 2, "pending_after": 3},
            "next_command": repo_review_progress.default_review_loop_command(max_units=2),
            "iterations": [{"index": 1, "status": "passed"}, {"index": 2, "status": "passed"}],
        }

    report = repo_qol_context.review_autopilot_report(
        tmp,
        max_cycles=1,
        max_units_per_cycle=5,
        max_total_units=10,
        max_elapsed_ms=480000,
        completion_factory=fake_completion,
        loop_factory=fake_loop,
    )

    assert_ok(report)
    assert_field(report, "status", "elapsed-limit")
    assert_field(report, "executed_unit_count", 2)
    assert_field(report, "estimated_review_tokens", 500)
    assert_has_all(report["next_command"], "review-loop")


def test_review_autopilot_stops_before_next_cycle_when_elapsed_budget_is_tight(tmp):
    loop_calls: list[dict[str, int]] = []
    elapsed_values = iter([0.0, 430000.0, 430000.0])

    def fake_completion(_root, *, deep=False, budget_intent="off"):
        return {
            "ok": False,
            "status": "blocked",
            "completion_supported": False,
            "gates": {"pending_review_unit_count": 5, "failed_check_count": 0},
            "next_command": repo_review_progress.default_review_loop_command(),
        }

    def fake_loop(_root, **kwargs):
        loop_calls.append(dict(kwargs))
        return {
            "ok": True,
            "status": "limit-reached",
            "executed_unit_count": 2,
            "estimated_review_tokens": 500,
            "progress_delta": {"completed_delta": 2, "pending_after": 3},
            "next_command": repo_review_progress.default_review_loop_command(max_units=2),
            "iterations": [{"index": 1, "status": "passed"}, {"index": 2, "status": "passed"}],
        }

    with patched_attrs(
        repo_qol_context.repo_qol_review_loop.repo_command_metrics,
        elapsed_ms_since=lambda _started: next(elapsed_values),
    ):
        report = repo_qol_context.review_autopilot_report(
            tmp,
            max_cycles=2,
            max_units_per_cycle=5,
            max_total_units=10,
            max_elapsed_ms=480000,
            completion_factory=fake_completion,
            loop_factory=fake_loop,
        )

    assert_ok(report)
    assert_field(report, "status", "elapsed-limit")
    assert_field(report, "cycle_count", 1)
    assert_field(report, "executed_unit_count", 2)
    assert len(loop_calls) == 1, loop_calls


def test_review_autopilot_summary_uses_explicit_elapsed_budget(tmp):
    report = {
        "schema_version": 1,
        "tool": "skill-manager.review-autopilot",
        "ok": True,
        "status": "cycle-limit",
        "completion_supported": False,
        "dry_run": False,
        "cycle_count": 1,
        "executed_unit_count": 20,
        "estimated_review_tokens": 24240,
        "completion": {"status": "blocked"},
        "next_command": repo_review_progress.default_review_loop_command(),
        "total_elapsed_ms": 405903.93,
        "max_elapsed_ms": 480000,
        "cycles": [],
    }

    compact = repo_qol_context.summarize_review_autopilot_report(report, compact=True)

    assert_field(compact["latency_budget"], "budget_ms", 480000)
    assert_field(compact["latency_budget"], "status", "within-budget")


def test_change_ledger_groups_changed_files_by_owner_and_reason(tmp):
    paths = [
        "AGENTS.md",
        ".agents/skills/skill-manager/scripts/repo_support/repo_qol.py",
        "automations/navigation/artifacts/maps/HANDOFF.md",
    ]

    with patched_attrs(
        repo_qol_context.repo_changed,
        changed_files=lambda root: paths,
        changed_file_statuses=lambda root: {path: {"M"} for path in paths},
    ), patched_attrs(
        repo_qol_context.repo_optimizations,
        changed_validation_plan=lambda root, values, scope, deep=False: [
            {"command": "python -B .agents/manage.py check-changed --summary --compact --format json", "required": True},
        ],
    ), patched_attrs(
        repo_qol_context,
        navigation_status=lambda root: {"status": "fresh", "read_first": "automations/navigation/artifacts/maps/HANDOFF.md"},
    ):
        report = repo_qol.change_ledger_report(tmp)

    compact = repo_qol.summarize_change_ledger_report(report, compact=True)

    assert_ok(report)
    assert_field(report, "changed_file_count", 3)
    assert_field(report["acceptance"], "status", "needs-review")
    assert_field(report["acceptance"], "review_required_owner_count", 3)
    assert_has_all(report["dominant_reason"], "agent instruction")
    assert_has_all(json.dumps(compact), "skill:skill-manager")
    assert_field(compact["acceptance"], "status", "needs-review")
    for group in compact["owner_groups"]:
        assert "paths" not in group, group
        assert_field(group, "acceptance_status", "needs-review")
        assert group.get("review_command") or group.get("validation_command")


def test_changed_context_summarizes_owner_routes_and_token_savings(tmp):
    paths = ["AGENTS.md", ".agents/skills/skill-manager/scripts/repo_support/repo_qol.py"]

    with patched_attrs(
        repo_qol_context.repo_changed,
        changed_files=lambda root: paths,
        changed_file_statuses=lambda root: {path: {"M"} for path in paths},
        changed_path_token_estimates=lambda root, values, **_kwargs: {path: {"estimated_tokens": 6000} for path in values},
    ), patched_attrs(
        repo_qol_context.repo_cost_policy,
        changed_diff_estimate=lambda root: {
            "estimated_tokens": 12000,
            "tracked_estimated_tokens": 12000,
            "untracked_estimated_tokens": 0,
        },
    ), patched_attrs(
        repo_qol_context.repo_optimizations,
        changed_validation_plan=lambda root, values, scope, deep=False: [
            {"command": "python -B .agents/manage.py check-changed --summary --compact --format json", "required": True},
        ],
    ), patched_attrs(
        repo_qol_context,
        navigation_status=lambda root, fast=True: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
        },
    ):
        report = repo_qol.changed_context_report(tmp)

    compact = repo_qol.summarize_changed_context_report(report, compact=True)

    assert_ok(report)
    assert_field(report, "tool", "skill-manager.changed-context")
    assert_field(compact["output_budget"], "command", "changed-context")
    assert_field(compact["output_budget"], "status", "within-budget")
    assert_field(report["comparison"], "raw_diff_input_tokens", 12000)
    assert report["comparison"]["selected_route_input_tokens"] < 12000
    assert report["comparison"]["saved_input_tokens_vs_raw"] > 0
    assert_has_all(report["next_command"], "review-packet")
    assert_has_all(compact["navigation"]["read_first"], "HANDOFF.md")
    assert compact["owner_groups"]
    for group in compact["owner_groups"]:
        assert_has_all(group, "owner", "risk_counts", "read_first", "review_command", "validation_command")
        assert "paths" not in group, group


def test_changed_context_compact_summary_limits_owner_detail_under_budget(tmp):
    groups = []
    for index in range(12):
        groups.append(
            {
                "owner": f"skill:owner-{index}",
                "status": "within-budget",
                "changed_file_count": 3,
                "estimated_changed_tokens": 900 - index,
                "risk_counts": {"medium": 3},
                "risk_tags": ["medium"],
                "read_first": [
                    f".agents/skills/owner-{index}/SKILL.md",
                    f".agents/skills/owner-{index}/module.json",
                    f".agents/skills/owner-{index}/scripts/tool.py",
                    f".agents/skills/owner-{index}/docs/reference.md",
                ],
                "tool_only_inputs": [
                    f"automations/navigation/artifacts/maps/owner-{index}.json",
                    f"automations/navigation/artifacts/maps/owner-{index}-graph.json",
                    f"automations/navigation/artifacts/maps/owner-{index}-symbols.json",
                    f"automations/navigation/artifacts/maps/owner-{index}-stale.json",
                ],
                "review_command": f"python -B .agents/manage.py review-packet --owner skill:owner-{index} --summary --compact --format json",
                "validation_command": "python -B .agents/manage.py syntax-check --paths "
                + " ".join(f"src/owner_{index}_{item}.py" for item in range(20))
                + " --summary --compact --format json",
                "paths": [f"src/owner_{index}_{item}.py" for item in range(10)],
            }
        )
    report = {
        "schema_version": 1,
        "tool": "skill-manager.changed-context",
        "ok": True,
        "status": "ready",
        "changed_file_count": 36,
        "changed_groups": "many owners",
        "navigation": {"status": "fresh", "read_first": "automations/navigation/artifacts/maps/HANDOFF.md"},
        "owner_groups": groups,
        "comparison": {
            "raw_diff_input_tokens": 50000,
            "selected_route_input_tokens": 1000,
            "saved_input_tokens_vs_raw": 49000,
            "saved_input_percent_vs_raw": 98.0,
            "route_paths": [{"path": "AGENTS.md", "estimated_tokens": 200}],
        },
        "review_packet": {"status": "over-budget", "review_budget_tokens": 5000},
        "validation_plan_summary": {"command_count": 1},
        "next_command": "python -B .agents/manage.py review-packet --owner skill:owner-0 --summary --compact --format json",
    }

    compact = repo_qol.summarize_changed_context_report(report, compact=True)

    assert_field(compact, "owner_group_count", 12)
    assert_field(compact, "owner_groups_returned", 5)
    assert_field(compact, "omitted_owner_group_count", 7)
    assert_field(compact["output_budget"], "status", "within-budget")
    assert len(compact["owner_groups"][0]["read_first"]) == 3
    assert len(compact["owner_groups"][0]["tool_only_inputs"]) == 3
    assert_field(
        compact["owner_groups"][0],
        "validation_command",
        "python -B .agents/manage.py check-additions --summary --compact --format json",
    )
    assert "route_paths" not in compact["comparison"]
    assert_lacks_all(json.dumps(compact), "src/owner_0_0.py")


def test_changed_context_uses_lightweight_owner_routing_without_hunk_packet(tmp):
    paths = [
        "AGENTS.md",
        ".agents/skills/skill-manager/scripts/repo_support/repo_qol_context.py",
    ]

    def fail_large_packet(*_args, **_kwargs):
        raise AssertionError("changed-context must not build full hunk-level review packets")

    def fail_raw_diff_estimate(*_args, **_kwargs):
        raise AssertionError("changed-context must not run a duplicate raw diff estimate")

    with patched_attrs(
        repo_qol_context.repo_changed,
        changed_files=lambda root: paths,
        changed_file_statuses=lambda root: {path: {"M"} for path in paths},
        changed_path_token_estimates=lambda root, values, **_kwargs: {path: {"estimated_tokens": 4500} for path in values},
        large_diff_review_packet=fail_large_packet,
    ), patched_attrs(
        repo_qol_context.repo_cost_policy,
        changed_diff_estimate=fail_raw_diff_estimate,
    ), patched_attrs(
        repo_qol_context.repo_optimizations,
        changed_validation_plan=lambda root, values, scope, deep=False: [
            {"command": "python -B .agents/manage.py check-additions", "required": True},
        ],
    ), patched_attrs(
        repo_qol_context,
        navigation_status=lambda root, fast=True: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
        },
    ):
        report = repo_qol.changed_context_report(tmp)

    assert_ok(report)
    assert_field(report["review_packet"], "status", "over-budget")
    assert_field(report["review_packet"], "changed_diff_estimated_tokens", 9000)
    assert_has_all(report["next_command"], "review-packet --owner")
    assert report["comparison"]["saved_input_tokens_vs_raw"] > 0


def test_changed_context_reuses_changed_statuses_for_token_estimates(tmp):
    paths = [
        "AGENTS.md",
        ".agents/skills/skill-manager/scripts/repo_support/repo_qol_context.py",
    ]
    status_map = {path: {"M"} for path in paths}
    observed: dict[str, object] = {}

    def fail_changed_files(_root):
        raise AssertionError("changed-context should derive changed paths from status map")

    def fake_estimates(_root, values, *, statuses=None):
        observed["values"] = list(values)
        observed["statuses"] = statuses
        return {path: {"estimated_tokens": 500} for path in values}

    with patched_attrs(
        repo_qol_context.repo_changed,
        changed_files=fail_changed_files,
        changed_file_statuses=lambda root: dict(status_map),
        changed_path_token_estimates=fake_estimates,
    ), patched_attrs(
        repo_qol_context.repo_cost_policy,
        changed_diff_estimate=lambda root: {
            "estimated_tokens": 8000,
            "tracked_estimated_tokens": 8000,
            "untracked_estimated_tokens": 0,
        },
    ), patched_attrs(
        repo_qol_context.repo_optimizations,
        changed_validation_plan=lambda root, values, scope, deep=False: [],
    ), patched_attrs(
        repo_qol_context,
        navigation_status=lambda root, fast=True: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
        },
    ):
        report = repo_qol.changed_context_report(tmp)

    assert_ok(report)
    assert observed["values"] == sorted(paths)
    assert observed["statuses"] == status_map


def test_changed_context_marks_raw_navigation_json_as_tool_only(tmp):
    paths = [
        "automations/navigation/instructions.md",
        "automations/navigation/scripts/navigation_core.py",
        "automations/navigation/artifacts/maps/CONVENTIONS.md",
        "automations/navigation/artifacts/maps/TECHNICAL_CONTEXT.md",
        "automations/navigation/suites/workflow-evals.json",
        "automations/navigation/artifacts/maps/NAVIGATION.md",
        "automations/navigation/artifacts/maps/handoff.json",
        "automations/navigation/artifacts/maps/staleness.json",
    ]

    with patched_attrs(
        repo_qol_context.repo_changed,
        changed_files=lambda root: paths,
        changed_file_statuses=lambda root: {path: {"M"} for path in paths},
        changed_path_token_estimates=lambda root, values: {path: {"estimated_tokens": 120} for path in values},
    ), patched_attrs(
        repo_qol_context.repo_cost_policy,
        changed_diff_estimate=lambda root: {
            "estimated_tokens": 1200,
            "tracked_estimated_tokens": 1200,
            "untracked_estimated_tokens": 0,
        },
    ), patched_attrs(
        repo_qol_context.repo_optimizations,
        changed_validation_plan=lambda root, values, scope, deep=False: [
            {"command": "python -B .agents/manage.py check-additions", "required": True},
        ],
    ), patched_attrs(
        repo_qol_context,
        navigation_status=lambda root, fast=True: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
        },
    ):
        report = repo_qol.changed_context_report(tmp)

    compact = repo_qol.summarize_changed_context_report(report, compact=True)
    navigation_group = next(group for group in compact["owner_groups"] if group["owner"] == "workflow:navigation")

    assert navigation_group["read_first"]
    assert_lacks_all(" ".join(navigation_group["read_first"]), "workflow-evals.json", "handoff.json", "staleness.json")
    assert_has_all(" ".join(navigation_group["tool_only_inputs"]), "handoff.json", "staleness.json")


def test_navigation_status_reports_stale_reasons_and_sources(tmp):
    write_text(tmp / "automations" / "navigation" / "artifacts" / "maps" / "HANDOFF.md", "# Handoff\n")
    report = {
        "ok": False,
        "status": "stale",
        "stale": ["automations/navigation/artifacts/maps/NAVIGATION.md"],
        "stale_source_changes": {
            "modified": ["AGENTS.md"],
            "added": ["docs/new.md"],
            "deleted": [],
        },
    }

    status = repo_navigation_status.navigation_status_from_report(tmp, report)

    assert_field(status, "status", "stale")
    assert_field(status, "reason", "stale-generated-navigation-output")
    assert_field(status, "stale_outputs", ["automations/navigation/artifacts/maps/NAVIGATION.md"])
    assert_field(status["stale_source_changes"], "modified", ["AGENTS.md"])
    assert_has_all(status["read_only_next_step"], "HANDOFF.md", "writes are allowed")

    trace = repo_navigation_status.navigation_context_trace(status)
    assert_has_all(trace["read_only_next_step"], "HANDOFF.md", "writes are allowed")


def write_complete_navigation_cache_packet(
    root,
    *,
    tree_hash,
    tree_kind=None,
    source_hash_kind=None,
    source_hashes=None,
):
    tree_kind = tree_kind or repo_navigation_status.SOURCE_GIT_TREE_KIND
    source_hash_kind = source_hash_kind or repo_navigation_status.SOURCE_HASH_KIND
    for rel in repo_navigation_status.NAVIGATION_OUTPUT_RELS:
        path = root / rel
        if not path.exists():
            write_text(path, "{}\n" if rel.endswith(".json") else "# Map\n")
    hashed_maps = [
        rel
        for rel in repo_navigation_status.NAVIGATION_OUTPUT_RELS
        if rel != repo_navigation_status.STALENESS_REL
    ]
    write_json(
        root / repo_navigation_status.STALENESS_REL,
        {
            "schema_version": 1,
            "ok": True,
            "source_git_tree_hash": tree_hash,
            "source_git_tree_kind": tree_kind,
            "source_hash_kind": source_hash_kind,
            "source_hashes": dict(source_hashes or {}),
            "map_files": list(repo_navigation_status.NAVIGATION_OUTPUT_RELS),
            "map_hashes": {
                rel: repo_navigation_status.file_sha256(root / rel)
                for rel in hashed_maps
            },
        },
    )


def initialize_git_fixture(root, message="fixture"):
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "self-tests@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Self Tests"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=root, check=True)


def test_navigation_status_fast_uses_staleness_cache_without_subprocess(tmp):
    handoff = tmp / "automations" / "navigation" / "artifacts" / "maps" / "HANDOFF.md"
    write_text(handoff, "# Handoff\n")
    for rel in repo_navigation_status.NAVIGATION_OUTPUT_RELS:
        write_text(tmp / rel, "{}\n" if rel.endswith(".json") else "# Map\n")
    write_text(tmp / "AGENTS.md", "# Repo\n")
    digest = repo_navigation_status.file_sha256(tmp / "AGENTS.md")
    staleness = {
        "schema_version": 1,
        "ok": True,
        "source_hash_kind": repo_navigation_status.SOURCE_HASH_KIND,
        "source_hashes": {"AGENTS.md": digest},
        "map_files": list(repo_navigation_status.NAVIGATION_OUTPUT_RELS),
        "map_hashes": {
            rel: repo_navigation_status.file_sha256(tmp / rel)
            for rel in repo_navigation_status.NAVIGATION_OUTPUT_RELS
            if rel != repo_navigation_status.STALENESS_REL
        },
    }
    write_json(tmp / "automations" / "navigation" / "artifacts" / "maps" / "staleness.json", staleness)
    subprocess.run(["git", "init", "--quiet"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "self-tests@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Self Tests"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=tmp, check=True)
    staleness["source_git_tree_hash"] = repo_navigation_status.git_tree_state(tmp)["tree_hash"]
    staleness["source_git_tree_kind"] = repo_navigation_status.SOURCE_GIT_TREE_KIND
    write_json(tmp / "automations" / "navigation" / "artifacts" / "maps" / "staleness.json", staleness)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "navigation metadata"], cwd=tmp, check=True)

    status = repo_navigation_status.fast_navigation_status(tmp)

    assert_field(status, "status", "fresh")
    assert_field(status, "reason", "fresh-navigation-git-tree-cache")


def test_navigation_status_fast_falls_back_when_clean_git_tree_mismatches_cache(tmp):
    write_text(tmp / "AGENTS.md", "# Repo\n")
    write_complete_navigation_cache_packet(
        tmp,
        tree_hash="stale-source-tree",
        source_hashes={"AGENTS.md": repo_navigation_status.file_sha256(tmp / "AGENTS.md")},
    )
    initialize_git_fixture(tmp)
    observed = []
    real_git_tree_state = repo_navigation_status.git_tree_state

    def record_git_tree_state(root):
        state = real_git_tree_state(root)
        observed.append(state)
        return state

    with patched_attrs(repo_navigation_status, git_tree_state=record_git_tree_state):
        fast = repo_navigation_status.fast_navigation_status(tmp)

    assert fast is None, fast
    assert len(observed) == 1, observed
    assert_true(observed[0], "available")
    assert_true(observed[0], "clean")
    assert observed[0]["tree_hash"] != "stale-source-tree", observed


def test_navigation_status_fast_falls_back_for_unknown_git_tree_cache_kind(tmp):
    write_text(tmp / "AGENTS.md", "# Repo\n")
    write_complete_navigation_cache_packet(tmp, tree_hash="pending")
    initialize_git_fixture(tmp)
    write_complete_navigation_cache_packet(
        tmp,
        tree_hash=repo_navigation_status.git_tree_state(tmp)["tree_hash"],
        tree_kind="unknown-cache-format",
        source_hashes={"AGENTS.md": repo_navigation_status.file_sha256(tmp / "AGENTS.md")},
    )
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "navigation metadata"], cwd=tmp, check=True)

    fast = repo_navigation_status.fast_navigation_status(tmp)

    assert fast is None, fast


def test_navigation_status_fast_falls_back_for_unknown_source_hash_kind(tmp):
    write_text(tmp / "AGENTS.md", "# Repo\n")
    write_complete_navigation_cache_packet(tmp, tree_hash="pending")
    initialize_git_fixture(tmp)
    write_complete_navigation_cache_packet(
        tmp,
        tree_hash=repo_navigation_status.git_tree_state(tmp)["tree_hash"],
        source_hash_kind="unknown-source-hash-format",
        source_hashes={"AGENTS.md": repo_navigation_status.file_sha256(tmp / "AGENTS.md")},
    )
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "navigation metadata"], cwd=tmp, check=True)

    fast = repo_navigation_status.fast_navigation_status(tmp)

    assert fast is None, fast


def test_navigation_source_hash_is_stable_across_dirty_generation_and_commit(tmp):
    write_text(tmp / "AGENTS.md", "# Existing project\n")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "self-tests@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Self Tests"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "AGENTS.md"], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "existing target"], cwd=tmp, check=True)
    write_text(tmp / "src" / "app.py", "print('installed')\n")

    navigation_core_path = (
        Path(__file__).resolve().parents[2]
        / "repo-navigation"
        / "scripts"
        / "navigation"
        / "navigation_core.py"
    )
    spec = importlib.util.spec_from_file_location("repo_navigation_hash_test", navigation_core_path)
    assert spec is not None and spec.loader is not None
    navigation_core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(navigation_core)
    generated = navigation_core.source_git_tree_hash(tmp)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "install harness"], cwd=tmp, check=True)
    committed = repo_navigation_status.git_tree_state(tmp)["tree_hash"]

    assert generated == committed, (generated, committed)


def test_navigation_fast_cache_verifies_owner_capsule_hashes(tmp):
    for rel in repo_navigation_status.NAVIGATION_OUTPUT_RELS:
        write_text(tmp / rel, "{}\n" if rel.endswith(".json") else "# Map\n")
    owner = "automations/navigation/artifacts/maps/owners/skill-demo.md"
    write_text(tmp / owner, "# Demo Owner\n")
    write_text(tmp / "AGENTS.md", "# Repo\n")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "self-tests@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Self Tests"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=tmp, check=True)
    map_paths = [
        rel
        for rel in [*repo_navigation_status.NAVIGATION_OUTPUT_RELS, owner]
        if rel != repo_navigation_status.STALENESS_REL
    ]
    write_json(
        tmp / repo_navigation_status.STALENESS_REL,
        {
            "schema_version": 1,
            "ok": True,
            "source_git_tree_hash": repo_navigation_status.git_tree_state(tmp)["tree_hash"],
            "source_git_tree_kind": repo_navigation_status.SOURCE_GIT_TREE_KIND,
            "source_hash_kind": repo_navigation_status.SOURCE_HASH_KIND,
            "source_hashes": {"AGENTS.md": repo_navigation_status.file_sha256(tmp / "AGENTS.md")},
            "map_files": sorted([*map_paths, repo_navigation_status.STALENESS_REL]),
            "map_hashes": {rel: repo_navigation_status.file_sha256(tmp / rel) for rel in map_paths},
        },
    )
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "navigation metadata"], cwd=tmp, check=True)
    write_text(tmp / owner, "# Tampered Owner\n")
    subprocess.run(["git", "add", owner], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "tamper owner"], cwd=tmp, check=True)

    fast = repo_navigation_status.fast_navigation_status(tmp)

    assert fast is None, fast


def test_navigation_fast_cache_rejects_unrecorded_owner_capsule(tmp):
    for rel in repo_navigation_status.NAVIGATION_OUTPUT_RELS:
        write_text(tmp / rel, "{}\n" if rel.endswith(".json") else "# Map\n")
    owner = "automations/navigation/artifacts/maps/owners/skill-demo.md"
    extra_owner = "automations/navigation/artifacts/maps/owners/skill-obsolete.md"
    write_text(tmp / owner, "# Demo Owner\n")
    write_text(tmp / "AGENTS.md", "# Repo\n")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "self-tests@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Self Tests"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=tmp, check=True)
    map_paths = [
        rel
        for rel in [*repo_navigation_status.NAVIGATION_OUTPUT_RELS, owner]
        if rel != repo_navigation_status.STALENESS_REL
    ]
    write_json(
        tmp / repo_navigation_status.STALENESS_REL,
        {
            "schema_version": 1,
            "ok": True,
            "source_git_tree_hash": repo_navigation_status.git_tree_state(tmp)["tree_hash"],
            "source_git_tree_kind": repo_navigation_status.SOURCE_GIT_TREE_KIND,
            "source_hash_kind": repo_navigation_status.SOURCE_HASH_KIND,
            "source_hashes": {"AGENTS.md": repo_navigation_status.file_sha256(tmp / "AGENTS.md")},
            "map_files": sorted([*map_paths, repo_navigation_status.STALENESS_REL]),
            "map_hashes": {rel: repo_navigation_status.file_sha256(tmp / rel) for rel in map_paths},
        },
    )
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "navigation metadata"], cwd=tmp, check=True)
    write_text(tmp / extra_owner, "# Obsolete Owner\n")
    subprocess.run(["git", "add", extra_owner], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "obsolete owner"], cwd=tmp, check=True)

    fast = repo_navigation_status.fast_navigation_status(tmp)

    assert fast is None, fast


def test_navigation_git_status_disables_optional_locks(_tmp):
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        stdout = b"" if "ls-files" in args else ""
        return Namespace(returncode=0, stdout=stdout)

    with patched_attrs(repo_navigation_status.subprocess, run=fake_run):
        repo_navigation_status.git_tree_state(Path("."))

    status_call = next(call for call in calls if "status" in call)
    assert status_call[:2] == ["git", "--no-optional-locks"], status_call


def test_navigation_status_fast_falls_back_outside_git(tmp):
    write_complete_navigation_cache_packet(tmp, tree_hash="cached-source-tree")
    observed = []
    real_git_tree_state = repo_navigation_status.git_tree_state

    def record_git_tree_state(root):
        state = real_git_tree_state(root)
        observed.append(state)
        return state

    with patched_attrs(repo_navigation_status, git_tree_state=record_git_tree_state):
        fast = repo_navigation_status.fast_navigation_status(tmp)

    assert fast is None, fast
    assert len(observed) == 1, observed
    assert_false(observed[0], "available")


def test_navigation_status_fast_uses_working_source_hash_for_dirty_non_route_sources(tmp):
    source = tmp / ".agents" / "skills" / "demo" / "scripts" / "fixtures" / "sample.json"
    write_text(source, "{}\n")
    write_complete_navigation_cache_packet(tmp, tree_hash="pending")
    initialize_git_fixture(tmp)
    cached_tree_hash = repo_navigation_status.git_tree_state(tmp)["tree_hash"]
    write_complete_navigation_cache_packet(
        tmp,
        tree_hash=cached_tree_hash,
    )
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "navigation metadata"], cwd=tmp, check=True)
    write_text(source, '{"dirty": true}\n')
    observed = []
    real_git_tree_state = repo_navigation_status.git_tree_state

    def record_git_tree_state(root):
        state = real_git_tree_state(root)
        observed.append(state)
        return state

    with patched_attrs(
        repo_navigation_status,
        git_tree_state=record_git_tree_state,
        working_source_git_tree_hash=lambda _root: cached_tree_hash,
    ):
        status = repo_navigation_status.fast_navigation_status(tmp)

    assert_field(status, "status", "fresh")
    assert_field(status, "reason", "fresh-navigation-incremental-source-cache")
    assert len(observed) == 1, observed
    assert_false(observed[0], "clean")


def test_navigation_status_fast_uses_working_source_hash_after_dirty_refresh(tmp):
    source = tmp / ".agents" / "skills" / "demo" / "SKILL.md"
    write_text(source, "# Demo\n")
    write_complete_navigation_cache_packet(tmp, tree_hash="pending")
    initialize_git_fixture(tmp)
    write_text(source, "# Refreshed Demo\n")
    refreshed_tree_hash = "refreshed-working-source-tree"
    write_complete_navigation_cache_packet(
        tmp,
        tree_hash=refreshed_tree_hash,
        source_hashes={
            source.relative_to(tmp).as_posix(): repo_navigation_status.file_sha256(source),
        },
    )

    with patched_attrs(
        repo_navigation_status,
        working_source_git_tree_hash=lambda _root: refreshed_tree_hash,
    ):
        status = repo_navigation_status.fast_navigation_status(tmp)

    assert_field(status, "status", "fresh")
    assert_field(status, "reason", "fresh-navigation-incremental-source-cache")


def test_navigation_status_fast_uses_incremental_cache_after_source_deletion(tmp):
    source = tmp / ".agents" / "skills" / "demo" / "SKILL.md"
    write_text(source, "# Demo\n")
    write_complete_navigation_cache_packet(tmp, tree_hash="pending")
    initialize_git_fixture(tmp)
    source.unlink()
    write_complete_navigation_cache_packet(
        tmp,
        tree_hash="refreshed-working-source-tree",
        source_hashes={},
    )

    status = repo_navigation_status.fast_navigation_status(tmp)

    assert_field(status, "status", "fresh")
    assert_field(status, "reason", "fresh-navigation-incremental-source-cache")


def test_navigation_status_fast_falls_back_for_dirty_route_sources(tmp):
    source = tmp / ".agents" / "skills" / "demo" / "SKILL.md"
    write_text(source, "# Demo\n")
    write_complete_navigation_cache_packet(tmp, tree_hash="pending")
    initialize_git_fixture(tmp)
    write_complete_navigation_cache_packet(
        tmp,
        tree_hash=repo_navigation_status.git_tree_state(tmp)["tree_hash"],
    )
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "navigation metadata"], cwd=tmp, check=True)
    write_text(source, "# Dirty Demo\n")
    observed = []
    real_git_tree_state = repo_navigation_status.git_tree_state

    def record_git_tree_state(root):
        state = real_git_tree_state(root)
        observed.append(state)
        return state

    with patched_attrs(repo_navigation_status, git_tree_state=record_git_tree_state):
        status = repo_navigation_status.fast_navigation_status(tmp)

    assert status is None, status
    assert len(observed) == 1, observed
    assert_false(observed[0], "clean")


def test_clean_tree_stale_navigation_readiness_entry_points_agree(tmp):
    full_report = {
        "ok": False,
        "status": "stale",
        "stale": ["automations/navigation/artifacts/maps/NAVIGATION.md"],
        "stale_source_changes": {"modified": ["AGENTS.md"], "added": [], "deleted": []},
    }
    write_text(tmp / "AGENTS.md", "# Repo\n")
    repo_navigation_script = (
        tmp / ".agents" / "skills" / "repo-navigation" / "scripts" / "repo_navigation.py"
    )
    write_text(
        repo_navigation_script,
        "import json\nprint(json.dumps(" + repr(full_report) + "))\n",
    )
    write_complete_navigation_cache_packet(
        tmp,
        tree_hash="stale-source-tree",
        source_hashes={
            "AGENTS.md": repo_navigation_status.file_sha256(tmp / "AGENTS.md"),
            repo_navigation_script.relative_to(tmp).as_posix(): repo_navigation_status.file_sha256(
                repo_navigation_script
            ),
        },
    )
    initialize_git_fixture(tmp, "stale clean tree")
    observed = []
    real_git_tree_state = repo_navigation_status.git_tree_state

    def record_git_tree_state(root):
        state = real_git_tree_state(root)
        observed.append(state)
        return state

    with patched_attrs(repo_navigation_status, git_tree_state=record_git_tree_state):
        direct = repo_navigation_status.navigation_status_from_report(tmp, full_report)
        setup_check = repo_setup.navigation_status(tmp)
        status = repo_qol_dashboard.dashboard_navigation_status(tmp, fast=True)
        startup_context = repo_qol_daily.startup_navigation_status(tmp)
        next_action = repo_qol_context.fast_navigation_status(tmp)

    expected = {
        "status": "stale",
        "reason": "stale-generated-navigation-output",
        "stale_outputs": ["automations/navigation/artifacts/maps/NAVIGATION.md"],
    }
    for name, report in {
        "direct": direct,
        "setup-check": setup_check,
        "status": status,
        "startup-context": startup_context,
        "next-action": next_action,
    }.items():
        assert {key: report.get(key) for key in expected} == expected, (name, report)
    assert observed, "clean-tree mismatch fixture must reach the Git-tree parity branch"
    assert all(state.get("available") and state.get("clean") for state in observed), observed
    assert all(state.get("tree_hash") != "stale-source-tree" for state in observed), observed


def test_check_changed_compact_reports_latency_output_budget_and_reason(tmp):
    with patched_attrs(
        repo_changed,
        changed_files=lambda root: [],
    ), patched_attrs(
        repo_changed,
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "Navigation maps are fresh.",
        },
    ):
        status, payload = capture_json(
            repo_changed.check_changed,
            Namespace(format="json", deep=False, verbose=False, summary=True, compact=True),
            tmp,
        )

    assert status == 0
    assert_field(payload["latency_budget"], "budget_ms", repo_command_metrics.LATENCY_BUDGETS_MS["check-changed"])
    assert_field(payload["output_budget"], "budget_tokens", 2400)
    assert_field(payload["output_budget"], "scope", "summary-compact-json-estimate")
    assert_field(payload, "next_command_reason", "No changed files; no changed-scope validation is required.")


def test_check_changed_json_defaults_to_compact_budgeted_summary(tmp):
    paths = ["src/demo.py", "src/other.py"]
    validation_plan = [
        {
            "order": 1,
            "command": "python -B .agents/manage.py check-additions",
            "required": True,
            "reason": "changed files need an owning contract",
        }
    ]
    with patched_attrs(
        repo_changed,
        changed_files=lambda root: paths,
        changed_scope=lambda values: {
            "skill_names": set(),
            "python_paths": [],
            "instructions": False,
            "skills_generated": False,
            "workflows": set(),
            "workflow_generated": False,
            "repo_surface": False,
            "docs": [],
            "other": [],
        },
        changed_skill_self_tests=lambda root, names: {},
        navigation_status=lambda root: {"status": "fresh"},
        large_diff_review_packet=lambda root, values, plan, navigation: {
            "status": "within-budget",
            "changed_file_count": len(values),
            "changed_diff_estimated_tokens": 200,
            "review_budget_tokens": 5000,
            "tokens_over_review_budget": 0,
        },
        addition_acceptance_report=lambda root, paths=None, new_paths=None: {
            "ok": True,
            "status": "passed",
            "summary": {"issue_count": 0},
        },
        render_addition_acceptance=lambda report, verbose=False: "acceptance ok",
    ), patched_attrs(
        repo_changed.repo_optimizations,
        changed_validation_plan=lambda root, values, scope, deep=False: validation_plan,
    ), patched_attrs(
        repo_changed.repo_fingerprint,
        input_fingerprint_report=lambda root, values, plan: {
            "digest": "abc",
            "changed_file_count": len(values),
        },
    ), patched_attrs(
        repo_changed.repo_review_progress,
        build_review_plan=lambda packet: {},
        review_progress_report=lambda root, plan, input_fingerprint=None: {},
        summarize_review_progress=lambda report: {},
    ), patched_attrs(
        repo_changed.repo_proof_hygiene,
        proof_hygiene_report=lambda root, values: {"ok": True, "status": "passed", "summary": {"finding_count": 0}},
        render_proof_hygiene=lambda report: "proof ok",
    ), patched_attrs(
        repo_changed.repo_portability,
        portability_report=lambda root, paths=None: {"ok": True, "status": "passed", "summary": {"finding_count": 0}},
        render_portability_report=lambda report: "portable ok",
    ), patched_attrs(
        repo_changed.repo_context_guardrails,
        context_guardrail_report=lambda root, paths=None: {"ok": True, "status": "passed", "finding_count": 0},
        render_context_guardrail_report=lambda report, compact=False: "guardrails ok",
    ):
        status, payload = capture_json(
            repo_changed.check_changed,
            Namespace(
                format="json",
                deep=False,
                verbose=False,
                summary=False,
                compact=False,
                refresh_navigation=False,
                full=False,
            ),
            tmp,
        )

    assert status == 0
    assert_field(payload, "changed_file_count", 2)
    assert "changed_files" not in payload
    assert "validation_plan" not in payload
    assert "checks" not in payload
    assert_field(payload["output_budget"], "command", "check-changed")
    assert_field(payload["output_budget"], "status", "within-budget")


def test_check_changed_does_not_record_progress_without_explicit_flag(tmp):
    def fail_write_progress(*_args, **_kwargs):
        raise AssertionError("check-changed default JSON path must not write validation progress")

    with patched_attrs(
        repo_changed,
        changed_files=lambda root: [],
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "Navigation maps are fresh.",
        },
    ), patched_attrs(
        repo_changed.repo_command_metrics,
        write_validation_progress=fail_write_progress,
    ):
        status, payload = capture_json(
            repo_changed.check_changed,
            Namespace(format="json", deep=False, verbose=False, summary=True, compact=True),
            tmp,
        )

    assert status == 0
    assert_field(payload["validation_progress"], "status", "passed")
    assert_field(payload["validation_progress"], "recorded", False)
    assert_field(payload["validation_progress"], "path", "")


def test_check_changed_summary_uses_batched_review_plan_cost_ledger(tmp):
    hunk_packets = [
        {
            "path": "src/demo.py",
            "hunk": f"h{index:03d}",
            "estimated_changed_tokens": tokens,
            "next_command": (
                "python -B .agents/manage.py review-packet "
                f"--owner skill:skill-manager --path src/demo.py --hunk h{index:03d} "
                "--summary --compact --format json"
            ),
        }
        for index, tokens in enumerate((100, 200, 300), start=1)
    ]
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_file_count": 1,
        "changed_diff_estimated_tokens": 5000,
        "review_budget_tokens": 5000,
        "review_batch_max_hunks": 2,
        "owner_review_packet_count": 1,
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "status": "within-budget",
                "scope": "owner",
                "estimated_changed_tokens": 600,
                "owner_review_subpackets": [
                    {
                        "path": "src/demo.py",
                        "estimated_changed_tokens": 600,
                        "path_review_hunks": hunk_packets,
                    }
                ],
            }
        ],
    }
    packet["cost_ledger"] = repo_cost_policy.review_cost_ledger(packet)
    payload = {
        "status": "passed",
        "changed_files": ["src/demo.py"],
        "checks": [],
        "validation_plan": [],
        "navigation": {"status": "fresh"},
        "review_packet": packet,
    }

    compact = repo_changed_summary.summarize_check_changed_payload(payload, compact=True)

    ledger = compact["review_packet"]["cost_ledger"]
    owner_context = compact["review_packet"]["affected_owner_context"]
    assert_field(ledger, "source_review_unit_count", 3)
    assert_field(ledger, "batched_review_unit_count", 2)
    assert_field(ledger, "next_review_unit_estimated_tokens", 300)
    assert_field(compact["review_packet"]["review_cost_report"], "next_review_unit_estimated_tokens", 300)
    assert_field(compact["review_packet"]["review_cost_report"]["money_saving_estimate"], "default_output_price_multiplier", 4)
    assert_field(owner_context, "status", "present")
    assert_field(owner_context, "owner_count", 1)
    assert_field(owner_context["owners"][0], "owner", "skill:skill-manager")
    assert_field(
        owner_context["owners"][0],
        "capsule",
        "automations/navigation/artifacts/maps/owners/skill-skill-manager.md",
    )
    assert_has_all(owner_context["owners"][0]["next_command"], "review-packet", "--owner skill:skill-manager")
    assert_field(
        compact["output_budget"],
        "estimated_output_tokens",
        repo_command_metrics.estimated_json_output_tokens(compact),
    )


def test_check_changed_summary_uses_review_progress_next_pending_command(tmp):
    hunk_packets = [
        {
            "path": "src/demo.py",
            "hunk": f"h{index:03d}",
            "estimated_changed_tokens": 300,
            "next_command": (
                "python -B .agents/manage.py review-packet "
                f"--owner skill:skill-manager --path src/demo.py --hunk h{index:03d} "
                "--summary --compact --format json"
            ),
        }
        for index in range(1, 4)
    ]
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_file_count": 1,
        "changed_diff_estimated_tokens": 9000,
        "review_budget_tokens": 5000,
        "review_batch_max_hunks": 1,
        "owner_review_packet_count": 1,
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "status": "within-budget",
                "scope": "owner",
                "estimated_changed_tokens": 900,
                "owner_review_subpackets": [
                    {
                        "path": "src/demo.py",
                        "estimated_changed_tokens": 900,
                        "path_review_hunks": hunk_packets,
                    }
                ],
            }
        ],
    }
    packet["cost_ledger"] = repo_cost_policy.review_cost_ledger(packet)
    plan = repo_review_progress.build_review_plan(packet)
    second_unit = plan["review_units"][1]
    payload = {
        "status": "passed",
        "changed_files": ["src/demo.py"],
        "checks": [],
        "validation_plan": [],
        "navigation": {"status": "fresh"},
        "review_packet": packet,
        "review_progress": {
            "status": "in-progress",
            "review_state": "partial",
            "completed_unit_count": 1,
            "pending_unit_count": 2,
            "stale": False,
            "next_pending_command": second_unit["command"],
            "current_unit": {
                "scope": second_unit["scope"],
                "owner": second_unit["owner"],
                "path": second_unit["path"],
                "hunk": second_unit["hunk"],
                "estimated_changed_tokens": second_unit["estimated_changed_tokens"],
            },
        },
    }

    compact = repo_changed_summary.summarize_check_changed_payload(payload, compact=True)
    summary = compact["review_packet"]["review_plan_summary"]

    assert_field(summary, "review_state", "partial")
    assert_field(summary, "completed_unit_count", 1)
    assert_field(summary, "pending_unit_count", 2)
    assert_field(summary, "next_pending_command", second_unit["command"])
    assert_has_all(summary["next_pending_command"], "--hunk h002")
    assert_false(compact["review_progress"], "stale")
    assert_field(compact["review_progress"]["current_unit"], "hunk", "h002")


def test_check_changed_summary_routes_to_completion_after_current_validation(tmp):
    hunk_packets = [
        {
            "path": "src/demo.py",
            "hunk": f"h{index:03d}",
            "estimated_changed_tokens": 300,
            "next_command": (
                "python -B .agents/manage.py review-packet "
                f"--owner skill:skill-manager --path src/demo.py --hunk h{index:03d} "
                "--summary --compact --format json"
            ),
        }
        for index in range(1, 3)
    ]
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_file_count": 1,
        "changed_diff_estimated_tokens": 9000,
        "tokens_over_review_budget": 4000,
        "review_budget_tokens": 5000,
        "review_batch_max_hunks": 1,
        "owner_review_packet_count": 1,
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "status": "within-budget",
                "scope": "owner",
                "estimated_changed_tokens": 600,
                "owner_review_subpackets": [
                    {
                        "path": "src/demo.py",
                        "estimated_changed_tokens": 600,
                        "path_review_hunks": hunk_packets,
                    }
                ],
            }
        ],
    }
    packet["cost_ledger"] = repo_cost_policy.review_cost_ledger(packet)
    plan = repo_review_progress.build_review_plan(packet)
    validation_command = "python -B .agents/manage.py check-additions"
    payload = {
        "status": "passed",
        "changed_files": ["src/demo.py"],
        "checks": [{"name": "changed checks", "ok": True, "output_summary": {}, "elapsed_ms": 1}],
        "validation_plan": [{"check_id": "check-additions", "command": validation_command, "required": True}],
        "validation_plan_summary": {"command_count": 1, "required_count": 1, "optional_count": 0, "owners": {}},
        "navigation": {"status": "fresh"},
        "input_fingerprint": {"digest": "digest-a", "changed_file_count": 1},
        "validation_progress": {
            "command": "check-changed",
            "status": "passed",
            "phase": "complete",
            "completed": 1,
            "total": 1,
            "extra": {
                "failed_check_count": 0,
                "input_fingerprint_digest": "digest-a",
                "profile": "changed",
                "required_check_ids": ["check-additions"],
                "passed_check_ids": ["check-additions"],
            },
        },
        "next_command_reason": "Run the first required validation command for the changed files.",
        "review_packet": packet,
        "review_progress": {
            "status": "in-progress",
            "review_state": "partial",
            "completed_unit_count": len(plan["review_units"]),
            "pending_unit_count": 1,
            "stale": False,
            "next_pending_command": validation_command,
            "current_unit": {"scope": "validation"},
        },
    }

    compact = repo_changed_summary.summarize_check_changed_payload(payload, compact=True)

    assert_field(compact, "next_command", "python -B .agents/manage.py finish --summary --compact --format json")
    assert_field(
        compact,
        "next_command_reason",
        "Changed-scope validation passed and matches current input; finish is the authoritative completion gate.",
    )
    assert_keys_lack(compact, "review_packet", "review_progress")
    assert_field(compact["output_budget"], "status", "within-budget")


def test_changed_validation_plan_runs_startup_baseline_for_low_context_inputs(tmp):
    write_cost_policy_fixture(tmp)
    scope = repo_changed.changed_scope(["AGENTS.md"])

    plan = repo_optimizations.changed_validation_plan(tmp, ["AGENTS.md"], scope)

    assert_contains(plan, "startup-context --baseline-ref HEAD")
    assert_contains(plan, "context-cost-benchmark")
    assert_contains(plan, "command-budget-check")
    assert_contains(plan, "sync-instructions --check")


def test_cost_policy_check_rejects_paid_primary_routes(tmp):
    policy = repo_cost_policy.default_cost_policy()
    policy["prefer_local_ai_over_paid_small_models"] = False
    policy["task_routes"]["validation"]["prefer"] = "paid"
    policy["task_routes"]["validation"]["paid_model_fallback"] = "primary-default"
    write_cost_policy_fixture(tmp, policy)
    report = repo_cost_policy.cost_policy_report(tmp)
    assert_not_ok(report)
    assert_contains_each(report["issues"], "must not be paid", "must not be primary")


def test_cost_policy_rejects_default_guidance_below_savings_threshold(tmp):
    policy = repo_cost_policy.default_cost_policy()
    policy["min_guidance_saved_percent"] = 99
    write_cost_policy_fixture(tmp, policy)

    report = repo_cost_policy.cost_policy_report(tmp)

    assert_not_ok(report)
    assert_field(report["guidance_savings"], "status", "better-below-threshold")
    assert_contains(report["issues"], "guidance.minimum_saved_percent")


def test_cost_policy_rejects_default_guidance_over_absolute_budget(tmp):
    policy = repo_cost_policy.default_cost_policy()
    policy["default_guidance_budget_tokens"] = 1
    write_cost_policy_fixture(tmp, policy)

    report = repo_cost_policy.cost_policy_report(tmp)

    assert_not_ok(report)
    assert_field(report["guidance_savings"], "status", "over-budget")
    assert_false(report["guidance_savings"], "within_absolute_budget")
    assert_contains(report["issues"], "guidance.default.budget_tokens")


def test_cost_policy_byte_based_guidance_estimator_is_named_precisely(tmp):
    write_cost_policy_fixture(tmp)

    report = repo_cost_policy.cost_policy_report(tmp)

    assert_field(
        report["guidance_savings"],
        "token_counter",
        "estimated_utf8_bytes_div_4",
    )


def test_cost_policy_numeric_budgets_are_strict_and_report_fallback_source(tmp):
    for value in ("1", True, 0, -1):
        policy = repo_cost_policy.default_cost_policy()
        policy["default_guidance_budget_tokens"] = value
        write_cost_policy_fixture(tmp, policy)

        report = repo_cost_policy.cost_policy_report(tmp)

        assert_not_ok(report)
        assert any(
            "cost_policy.guidance.default.budget_tokens" in str(issue)
            for issue in report["issues"]
        )
        assert_fields(
            report["guidance_savings"],
            budget_tokens=5000,
            budget_source="fallback-invalid",
        )
        assert_contains([report["guidance_savings"]["budget_issue"]], "cost_policy.guidance.default.budget_tokens")

    for value in ("1500", True, 0, -1):
        policy = repo_cost_policy.default_cost_policy()
        policy["phase_budgets"]["routing"] = value
        write_cost_policy_fixture(tmp, policy)

        report = repo_cost_policy.cost_policy_report(tmp)

        assert_not_ok(report)
        assert any(
            "cost_policy.budgets.phases.overrides.routing" in str(issue)
            for issue in report["issues"]
        )

    missing = repo_cost_policy.default_cost_policy()
    missing.pop("default_guidance_budget_tokens")
    write_cost_policy_fixture(tmp, missing)
    missing_report = repo_cost_policy.cost_policy_report(tmp)
    assert_not_ok(missing_report)
    assert_contains(missing_report["issues"], "cost_policy.guidance.default.budget_tokens")
    assert_fields(
        missing_report["guidance_savings"],
        budget_tokens=5000,
        budget_source="fallback-invalid",
    )

    valid = repo_cost_policy.default_cost_policy()
    valid["default_guidance_budget_tokens"] = 4321
    write_cost_policy_fixture(tmp, valid)
    valid_report = repo_cost_policy.cost_policy_report(tmp)
    assert_ok(valid_report)
    assert_fields(
        valid_report["guidance_savings"],
        budget_tokens=4321,
        budget_source="configured",
    )


def test_cost_policy_rejects_missing_guidance_files_as_incomplete(tmp):
    write_cost_policy_fixture(tmp)
    handoff = tmp / "automations" / "navigation" / "artifacts" / "maps" / "HANDOFF.md"
    handoff.unlink()

    report = repo_cost_policy.cost_policy_report(tmp)

    assert_not_ok(report)
    assert_field(report["guidance_savings"], "status", "incomplete")
    assert_false(report["guidance_savings"], "complete")
    assert_has_all(report["guidance_savings"]["default_context"]["missing"], handoff.relative_to(tmp).as_posix())
    assert_contains(report["issues"], "missing required files")


def test_cost_policy_rejects_missing_broad_baseline_files_as_incomplete(tmp):
    write_cost_policy_fixture(tmp)
    navigation = tmp / "automations" / "navigation" / "artifacts" / "maps" / "NAVIGATION.md"
    navigation.unlink()

    report = repo_cost_policy.cost_policy_report(tmp)

    assert_not_ok(report)
    assert_field(report["guidance_savings"], "status", "incomplete")
    assert_false(report["guidance_savings"], "complete")
    assert_contains(report["issues"], "broad guidance baseline is missing required files")


def test_cost_policy_rejects_explicitly_empty_guidance_lists(tmp):
    policy = repo_cost_policy.default_cost_policy()
    policy["default_guidance_files"] = []
    policy["broad_guidance_baseline_files"] = []
    write_cost_policy_fixture(tmp, policy)

    report = repo_cost_policy.cost_policy_report(tmp)

    assert_not_ok(report)
    assert_contains_each(
        report["issues"],
        "cost_policy.guidance.default.files must be a non-empty list",
        "cost_policy.guidance.baseline.files must be a non-empty list",
    )


def test_validate_skill_cli_supports_json_summary(tmp):
    skill_dir = write_skill(tmp)
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(Path(validate_skill.__file__)),
            str(skill_dir),
            "--format",
            "json",
            "--summary",
            "--compact",
        ],
        cwd=tmp,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert_ok(report)
    assert_fields(
        report,
        tool="skill-manager.validate-skill",
        error_count=0,
        skill_path=".agents/skills/demo-skill",
    )
    assert_lacks_all(report, "warnings")

    text_completed = subprocess.run(
        [sys.executable, "-B", str(Path(validate_skill.__file__)), str(skill_dir)],
        cwd=tmp,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert text_completed.returncode == 0, text_completed.stderr
    assert text_completed.stdout.strip() == "Validated skill folder: .agents/skills/demo-skill"


def test_module_json_required(tmp):
    skill_dir = write_skill(tmp)
    skill_path(skill_dir, "module.json").unlink()
    errors, _warnings = validate_skill.validate_skill(skill_dir)
    assert_contains(errors, "module.json is required")


def test_validate_skill_allows_benign_runtime_environment_selector(tmp):
    skill_dir = write_skill(tmp)
    write_text(
        skill_path(skill_dir, "scripts", "runtime.py"),
        """#!/usr/bin/env python3
import os
import sys

python_executable = os.environ.get("AGENTS_PYTHON") or sys.executable
print(python_executable)
""",
    )

    errors, _warnings = validate_skill.validate_skill(skill_dir)

    assert_lacks(errors, "credentials evidence")


def test_validate_skill_rejects_secret_like_environment_selector(tmp):
    skill_dir = write_skill(tmp)
    write_text(
        skill_path(skill_dir, "scripts", "runtime.py"),
        """#!/usr/bin/env python3
import os

value = os.environ.get("SONAR_TOKEN")
print(bool(value))
""",
    )

    errors, _warnings = validate_skill.validate_skill(skill_dir)

    assert_contains(errors, "secret-like environment variable access")


def test_manifest_path_is_module_json(tmp):
    skill_dir = write_skill(tmp)
    manifest, path, error = common.load_skill_manifest_with_path(skill_dir)
    assert_none(error)
    assert path.name == "module.json"
    assert manifest
    assert_field(manifest, "schema_version", 3)


def test_registry_generated_from_module_json(tmp):
    write_skill(tmp)
    entry = registry_entry(tmp)
    assert_name(entry, "demo-skill")
    assert_fields(entry, manifest_path="module.json", manifest_schema_version=3)


def test_registry_ignores_empty_auxiliary_dirs(tmp):
    skill_dir = write_skill(tmp)
    skill_path(skill_dir, "docs").mkdir()
    skill_path(skill_dir, "assets").mkdir()

    entry = registry_entry(tmp)
    assert_fields(entry, has_docs=False, has_assets=False)

    write_text(skill_path(skill_dir, "docs", "guide.md"), "# Guide")
    write_text(skill_path(skill_dir, "assets", "sample.txt"), "fixture")
    entry = registry_entry(tmp)
    assert_fields(entry, has_docs=True, has_assets=True)


def test_deep_skill_routing_check_accepts_canonical_fast_registry(tmp):
    write_skill(tmp)

    generated = sync_skill_routing.sync_skill_routing(
        tmp,
        check=False,
        max_files=200,
        max_text_files=100,
        deep=False,
    )
    assert generated == 0

    checked = sync_skill_routing.sync_skill_routing(
        tmp,
        check=True,
        max_files=200,
        max_text_files=100,
        deep=True,
    )

    assert checked == 0


def test_skill_routing_check_stale_message_separates_strict_and_write_mode(tmp):
    write_skill(tmp)
    stderr = io.StringIO()

    with contextlib.redirect_stderr(stderr):
        status = sync_skill_routing.sync_skill_routing(
            tmp,
            check=True,
            max_files=200,
            max_text_files=100,
            deep=False,
        )

    text = stderr.getvalue()
    assert status == 1
    assert "Strict read-only: report stale generated skill routing/registry" in text
    assert "Write-mode fix: python -B .agents/manage.py sync-skill-routing" in text


def test_routing_categories_keep_dotnet_and_ticket_skills_direct(_tmp):
    assert sync_skill_routing.infer_category(
        "dotnet-engineering",
        "Use for modern .NET upgrades and route security risk to dotnet-security-review.",
        "Guides .NET upgrade work with validation evidence.",
    ) == ARCH_ENGINEERING_CATEGORY
    assert sync_skill_routing.infer_category(
        "dotnet-quality-gates",
        "Use for .NET quality validation and auth-sensitive evidence.",
        "Runs deterministic .NET gates.",
    ) == ARCH_ENGINEERING_CATEGORY
    assert sync_skill_routing.infer_category(
        "dotnet-security-review",
        "Use for .NET security review.",
        "Reviews ASP.NET security risks.",
    ) == "Security"
    assert sync_skill_routing.infer_category(
        "azure-devops-ticket-intake",
        "Use for Azure DevOps ticket intake with PAT authentication.",
        "Imports work item attachments.",
    ) == "Ticket And Intake"
    assert sync_skill_routing.infer_category(
        "project-context-generator",
        "Use when generating workflow-ready project context files with structure, validation commands, and security notes.",
        "Generates workflow context and proof scripts.",
    ) == "Documentation And Diagrams"
    assert sync_skill_routing.infer_category(
        "document-artifacts",
        "Inspect PDF, DOCX, PPTX, and XLSX files with Markdown security scanning.",
        "Portable document evidence and security checks.",
    ) == "Documents And Office"
    assert sync_skill_routing.infer_category(
        "repo-navigation",
        "Build dependency impact queries and repository navigation maps.",
        "Portable source relationship discovery.",
    ) == "Documentation And Diagrams"


def test_eval_suite_uses_v2_contract(tmp):
    skill_dir = write_skill(tmp)
    report = eval_skill.run_eval(
        Namespace(
            skill=str(skill_dir),
            suite=str(skill_path(skill_dir, "suites", "demo-evals.json")),
            baseline="none",
            format="json",
        )
    )
    assert_summary(report, passed=1, failed=0, total=1)


def test_eval_suite_times_out_command_assertions(tmp):
    skill_dir = write_skill(tmp)
    write_text(
        skill_path(skill_dir, "scripts", "slow_eval.py"),
        "import time\nprint('starting slow eval')\ntime.sleep(5)\n",
    )
    write_json(
        skill_path(skill_dir, "suites", "slow-evals.json"),
        {
            "evals": [
                {
                    "id": "slow",
                    "assertions": [
                        {
                            "type": "python_script_succeeds",
                            "path": "scripts/slow_eval.py",
                            "timeout_seconds": 1,
                        }
                    ],
                }
            ]
        },
    )
    report = eval_skill.run_eval(
        Namespace(
            skill=str(skill_dir),
            suite=str(skill_path(skill_dir, "suites", "slow-evals.json")),
            baseline="none",
            format="json",
        )
    )
    assert_summary(report, passed=0, failed=1, total=1)
    message = report["results"][0]["assertions"][0]["message"]
    assert "timed out after 1s" in message


def test_eval_suite_timeout_kills_child_process_tree(tmp):
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
    cache = eval_skill.CommandResultCache()

    returncode, output = cache.run(
        [sys.executable, "-B", str(parent)],
        cwd=tmp,
        timeout_seconds=1,
    )
    time.sleep(2)

    assert returncode == 124, (returncode, output)
    assert not survived.exists(), "timed-out eval left its child process running"


def test_eval_suite_caches_command_results_by_argv_and_timeout(tmp):
    skill_dir = write_skill(tmp)
    write_json(
        skill_path(skill_dir, "suites", "cached-command-evals.json"),
        {
            "evals": [
                {
                    "id": "cached-command",
                    "assertions": [
                        {
                            "type": "python_script_succeeds",
                            "path": "scripts/run_self_tests.py",
                            "timeout_seconds": 5,
                        },
                        {
                            "type": "python_script_succeeds",
                            "path": "scripts/run_self_tests.py",
                            "timeout_seconds": 5,
                        },
                        {
                            "type": "python_script_succeeds",
                            "path": "scripts/run_self_tests.py",
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
        return subprocess.CompletedProcess(command, 7, stdout="shared failure output\n")

    original_run = eval_skill.execute_subprocess
    eval_skill.execute_subprocess = failed_run
    try:
        report = eval_skill.run_eval(
            Namespace(
                skill=str(skill_dir),
                suite=str(skill_path(skill_dir, "suites", "cached-command-evals.json")),
                baseline="none",
                format="json",
            )
        )
    finally:
        eval_skill.execute_subprocess = original_run

    assert len(calls) == 2, calls
    assert {timeout for _command, timeout in calls} == {5, 6}, calls
    assert report["command_telemetry"] == {
        "command_assertions": 3,
        "unique_command_executions": 2,
        "command_cache_hits": 1,
    }
    assertions = report["results"][0]["assertions"]
    assert assertions[0] is not assertions[1]
    assert_has_all(assertions[0]["message"], "shared failure output")
    assert assertions[0]["message"] == assertions[1]["message"]


def test_eval_suite_can_assert_repo_file_contains(tmp):
    skill_dir = write_skill(tmp)
    write_text(agent_path(tmp, "manage.py"), "# manage placeholder")
    write_text(tmp / "docs" / "workflow" / "workflows.md", "# Workflows\n\nStrict dogfood lane.\n")
    write_json(
        skill_path(skill_dir, "suites", "repo-file-evals.json"),
        {
            "evals": [
                {
                    "id": "repo-doc",
                    "assertions": [
                        {
                            "type": "repo_file_contains",
                            "path": "docs/workflow/workflows.md",
                            "text": "Strict dogfood lane",
                        }
                    ],
                }
            ]
        },
    )
    report = eval_skill.run_eval(
        Namespace(
            skill=str(skill_dir),
            suite=str(skill_path(skill_dir, "suites", "repo-file-evals.json")),
            baseline="none",
            format="json",
        )
    )
    assert_summary(report, passed=1, failed=0, total=1)


def test_eval_suite_accepts_skill_id_from_repo_root(tmp):
    skill_dir = write_skill(tmp)
    write_text(agent_path(tmp, "manage.py"), "# manage placeholder")
    current = Path.cwd()
    try:
        os.chdir(tmp)
        report = eval_skill.run_eval(
            Namespace(
                skill="demo-skill",
                suite=str(skill_path(skill_dir, "suites", "demo-evals.json")),
                baseline="none",
                format="json",
            )
        )
    finally:
        os.chdir(current)
    assert_summary(report, passed=1, failed=0, total=1)
    assert_field(report, "skill", str(skill_dir.resolve()))


def test_measure_skill_budget_accepts_skill_id_from_repo_root(tmp):
    skill_dir = write_skill(tmp)
    write_text(agent_path(tmp, "manage.py"), "# manage placeholder")
    current = Path.cwd()
    try:
        os.chdir(tmp)
        report = measure_skill_budget.build_report(
            Namespace(
                root=str(tmp),
                all=False,
                skill="demo-skill",
                summary=True,
                compact=True,
                baseline_ref=None,
                write_trend=False,
            )
        )
    finally:
        os.chdir(current)
    assert_field(report["summary"], "skill_count", 1)
    assert report["summary"]["skill_md_words"] > 0
    assert_field(report["top"][0], "name", "demo-skill")
    assert_field(report["top_by_load_class"]["routing"][0], "path", "SKILL.md")


def test_agent_compatibility_accepts_current_skill(tmp):
    write_skill(tmp)
    errors, warnings = validate_agent_compatibility.check_skill_compatibility(tmp)
    assert_empty(errors)
    assert_empty(warnings)


def test_agent_compatibility_rejects_nested_skill_layout(tmp):
    write_skill(tmp)
    nested = agent_path(tmp, "skills", "category", "nested-skill", "SKILL.md")
    write_text(
        nested,
        """---
name: nested-skill
description: Use when demonstrating a nested skill layout that adapters should reject.
---

# Nested Skill
""",
    )

    report = validate_agent_compatibility.build_report(tmp)

    assert_not_ok(report)
    assert_contains(report["errors"], "nested skill layout")


def test_generated_claude_adapter_is_frontmatter_first_and_cleanup_owned(tmp):
    skill_dir = write_skill(tmp)

    adapter = repo_generated.generated_claude_adapter(tmp, skill_dir)

    assert adapter.startswith("---\nname: demo-skill\n")
    assert not adapter.encode("utf-8").startswith(b"\xef\xbb\xbf")
    assert adapter.index(repo_generated.GENERATED_CLAUDE_HEADER) > adapter.index("---\n\n")
    assert repo_generated.is_generated_claude_adapter(adapter)
    legacy = repo_generated.GENERATED_CLAUDE_HEADER + "\n" + adapter.replace(
        repo_generated.GENERATED_CLAUDE_HEADER + "\n\n", ""
    )
    assert repo_generated.is_generated_claude_adapter(legacy)
    assert not repo_generated.is_generated_claude_adapter(
        repo_generated.GENERATED_CLAUDE_HEADER + "\n# User-authored file\n"
    )


def test_sync_claude_skills_removes_obsolete_generated_wrappers_and_preserves_user_skill(tmp):
    write_skill(tmp)
    obsolete_new = tmp / ".claude" / "skills" / "obsolete-new" / "SKILL.md"
    obsolete_old = tmp / ".claude" / "skills" / "obsolete-old" / "SKILL.md"
    user_skill = tmp / ".claude" / "skills" / "user-skill" / "SKILL.md"
    generated_body = (
        "# Obsolete\n\n"
        "This is a generated Claude Code adapter. Read and follow `missing`.\n"
    )
    write_text(
        obsolete_new,
        "---\nname: obsolete-new\ndescription: Obsolete generated wrapper.\n---\n\n"
        + repo_generated.GENERATED_CLAUDE_HEADER
        + "\n\n"
        + generated_body,
    )
    write_text(
        obsolete_old,
        repo_generated.GENERATED_CLAUDE_HEADER
        + "\n---\nname: obsolete-old\ndescription: Obsolete generated wrapper.\n---\n\n"
        + generated_body,
    )
    write_text(
        user_skill,
        "---\nname: user-skill\ndescription: User-owned Claude skill.\n---\n\n# User Skill\n",
    )

    assert repo_generated.sync_claude_skills(tmp, check=False) == 0

    assert_missing(obsolete_new)
    assert_missing(obsolete_old)
    assert user_skill.exists()
    generated = tmp / ".claude" / "skills" / "demo-skill" / "SKILL.md"
    assert generated.read_text(encoding="utf-8").startswith("---\n")


def test_agent_compatibility_rejects_marker_before_claude_frontmatter(tmp):
    skill_dir = write_skill(tmp)
    adapter_path = tmp / ".claude" / "skills" / skill_dir.name / "SKILL.md"
    generated = repo_generated.generated_claude_adapter(tmp, skill_dir)
    write_text(
        adapter_path,
        repo_generated.GENERATED_CLAUDE_HEADER
        + "\n"
        + generated.replace(repo_generated.GENERATED_CLAUDE_HEADER + "\n\n", ""),
    )

    errors = validate_agent_compatibility.claude_adapter_portability_errors(tmp)

    assert_contains(errors, "must start with YAML frontmatter")


def test_installed_host_validation_reports_static_capabilities_and_copilot_skills(tmp):
    write_skill(tmp)
    calls = []

    def fake_which(executable):
        return f"C:/tools/{executable}.exe"

    def fake_runner(argv, _root):
        calls.append(list(argv))
        executable = Path(argv[0]).stem
        if argv[-1:] == ["--version"]:
            return {
                "ok": True,
                "returncode": 0,
                "output": f"{executable} 1.0\n",
                "failure": "",
                "truncated": False,
            }
        if argv[-1:] == ["--help"]:
            help_by_host = {
                "codex": "--model\nresume\nmcp\n",
                "copilot": "--agent --model --reasoning-effort --resume --continue mcp --allow-tool monitoring\n",
                "claude": "--agent --agents --model --effort --resume --continue mcp --allowed-tools --output-format --json-schema\n",
            }
            return {
                "ok": True,
                "returncode": 0,
                "output": help_by_host[executable],
                "failure": "",
                "truncated": False,
            }
        assert argv[1:] == ["--no-auto-update", "--no-color", "skill", "list", "--json"], argv
        return {
            "ok": True,
            "returncode": 0,
            "stdout": json.dumps(
                [
                    {
                        "name": "demo-skill",
                        "description": "Demo",
                        "source": "project",
                        "path": str(tmp / ".agents" / "skills" / "demo-skill"),
                        "enabled": True,
                    },
                    {
                        "name": "builtin-skill",
                        "description": "Builtin",
                        "source": "builtin",
                        "path": "builtin",
                        "enabled": True,
                    },
                ]
            ),
            "stderr": "",
            "output": "",
            "failure": "",
            "truncated": False,
        }

    report = validate_agent_compatibility.installed_host_validation_report(
        tmp,
        which=fake_which,
        runner=fake_runner,
    )

    assert_ok(report)
    assert_fields(report, host_count=3, installed_count=3, failed_count=0)
    copilot = next(host for host in report["hosts"] if host["host_surface"] == "github_copilot")
    assert_field(copilot["skill_discovery"], "project_skill_count", 1)
    assert_false(copilot["skill_discovery"], "failed_loads")
    assert_true(report, "does_not_invoke_models")
    assert ["C:/tools/codex.exe", "--version"] in calls
    assert ["C:/tools/copilot.exe", "--no-auto-update", "--no-color", "--version"] in calls
    assert ["C:/tools/copilot.exe", "--no-auto-update", "--no-color", "--help"] in calls
    assert ["C:/tools/copilot.exe", "--no-auto-update", "--no-color", "skill", "list", "--json"] in calls
    assert ["C:/tools/claude.exe", "--help"] in calls
    forbidden = {"prompt", "print", "auth", "login", "update", "doctor"}
    assert not any(forbidden.intersection({part.casefold() for part in call[1:]}) for call in calls)


def test_installed_host_validation_reports_missing_hosts_without_running_commands(tmp):
    def forbidden_runner(_argv, _root):
        raise AssertionError("runner must not be called for missing executables")

    report = validate_agent_compatibility.installed_host_validation_report(
        tmp,
        which=lambda _executable: None,
        runner=forbidden_runner,
    )

    assert_ok(report)
    assert_field(report, "schema_version", 1)
    assert_field(report, "status", "partial")
    assert_fields(report, host_count=3, installed_count=0, not_installed_count=3)


def test_host_probe_command_reports_timeout_and_start_failure(tmp):
    original_run = validate_agent_compatibility.subprocess.run

    def timeout_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["host", "--help"], timeout=10)

    def denied_run(*_args, **_kwargs):
        raise PermissionError(13, "Access is denied")

    try:
        validate_agent_compatibility.subprocess.run = timeout_run
        timeout = validate_agent_compatibility.run_host_probe_command(
            ["host", "--help"], tmp
        )
        assert_false(timeout, "ok")
        assert "timed out after 10 seconds" in timeout["failure"]

        validate_agent_compatibility.subprocess.run = denied_run
        denied = validate_agent_compatibility.run_host_probe_command(
            ["host", "--version"], tmp
        )
        assert_false(denied, "ok")
        assert "Access is denied" in denied["failure"]
    finally:
        validate_agent_compatibility.subprocess.run = original_run


def test_installed_host_validation_rejects_copilot_failed_skill_loads(tmp):
    write_skill(tmp)

    def fake_which(executable):
        return f"C:/tools/{executable}.exe"

    def fake_runner(argv, _root):
        executable = Path(argv[0]).stem
        if argv[-1:] == ["--version"]:
            return {"ok": True, "returncode": 0, "output": "1.0\n", "failure": "", "truncated": False}
        if argv[-1:] == ["--help"]:
            output = {
                "codex": "--model resume mcp",
                "copilot": "--agent --model --resume mcp",
                "claude": "--agent --model --resume mcp --output-format",
            }[executable]
            return {"ok": True, "returncode": 0, "output": output, "failure": "", "truncated": False}
        return {
            "ok": True,
            "returncode": 0,
            "stdout": json.dumps(
                [
                    {
                        "name": "demo-skill",
                        "description": "Demo",
                        "source": "project",
                        "path": str(tmp / ".agents" / "skills" / "demo-skill"),
                        "enabled": True,
                    }
                ]
            ),
            "stderr": (
                "The following skills failed to load:\n"
                "  .claude\\skills\\bad\\SKILL.md: malformed YAML\n"
            ),
            "output": "",
            "failure": "",
            "truncated": False,
        }

    report = validate_agent_compatibility.installed_host_validation_report(
        tmp,
        which=fake_which,
        runner=fake_runner,
    )

    assert_not_ok(report)
    assert_field(report, "failed_count", 1)
    assert_contains(report["issues"], "skill discovery reported failed loads")


def test_invocation_contract_rejects_unknown_skill_reference(tmp):
    skill_dir = write_skill(tmp)
    text = skill_md(skill_dir).read_text(encoding="utf-8")
    write_text(
        skill_md(skill_dir),
        text
        + """
## Scope

- Invoke [skill:missing-skill] only when the owner exists.

## Out Of Scope

- Do not route missing owners.
""",
    )

    errors, warnings = validate_skill.validate_skill(skill_dir)

    assert_contains(errors, "[skill:missing-skill]")
    assert_lacks(warnings, "Scope should pair")


def test_invocation_contract_warns_when_scope_has_no_out_of_scope(tmp):
    skill_dir = write_skill(tmp)
    text = skill_md(skill_dir).read_text(encoding="utf-8")
    write_text(
        skill_md(skill_dir),
        text
        + """
## Scope

- Use for a focused fixture scenario.
""",
    )

    errors, warnings = validate_skill.validate_skill(skill_dir)

    assert_empty(errors)
    assert_contains(warnings, "Scope should pair with Out of Scope")


def test_validate_skill_rejects_utf8_bom_in_skill_file(tmp):
    skill_dir = write_skill(tmp)
    skill_path = skill_md(skill_dir)
    original = skill_path.read_bytes()
    skill_path.write_bytes(b"\xef\xbb\xbf" + original)

    errors, _warnings = validate_skill.validate_skill(skill_dir)

    assert_contains(errors, "UTF-8 BOM")


def test_validate_skill_rejects_progressive_docs_without_h1_or_with_scope(tmp):
    skill_dir = write_skill(tmp)
    write_text(skill_path(skill_dir, "docs", "missing-title.md"), "Notes without a title.")
    write_text(
        skill_path(skill_dir, "docs", "bad-routing.md"),
        """# Bad Routing

## Scope

- Invocation boundaries belong in SKILL.md.
""",
    )

    errors, _warnings = validate_skill.validate_skill(skill_dir)

    assert_contains_each(errors, "docs/missing-title.md is missing an H1 heading", "docs/bad-routing.md must not define Scope")


def test_validate_skill_rejects_large_skill_assets(tmp):
    skill_dir = write_skill(tmp)
    large_asset = skill_path(skill_dir, "assets", "oversized.bin")
    large_asset.parent.mkdir(parents=True, exist_ok=True)
    with large_asset.open("wb") as handle:
        handle.seek((5 * 1024 * 1024) + 1)
        handle.write(b"x")

    errors, _warnings = validate_skill.validate_skill(skill_dir)

    assert_contains_all(errors, "assets/oversized.bin", "5MB")


def test_validate_skill_resolves_docs_skill_refs_fence_aware(tmp):
    skill_dir = write_skill(tmp)
    write_text(
        skill_path(skill_dir, "docs", "refs.md"),
        """# References

```markdown
[skill:fenced-placeholder]
```

Use [skill:missing-owner] only when the owner exists.
""",
    )

    errors, _warnings = validate_skill.validate_skill(skill_dir)

    assert_contains(errors, "[skill:missing-owner]")
    assert_lacks(errors, "[skill:fenced-placeholder]")


def write_dotnet_naming_fixture(
    root,
    name,
    description,
    summary,
):
    skill_dir = write_skill(root, name)
    write_text(
        skill_md(skill_dir),
        f"""---
name: {name}
description: {description}
---

# {name}

## Workflow

Validate boundary.

## Stop Rules

Stop when blocked.

## Completion Contract

Report validation.
""",
    )
    manifest = module_contract(name)
    manifest["summary"] = summary
    write_json(skill_path(skill_dir, "module.json"), manifest)
    return skill_dir


def test_validate_skill_rejects_modern_dotnet_framework_ownership(tmp):
    skill_dir = write_dotnet_naming_fixture(
        tmp,
        "dotnet-maintenance",
        "Use when maintaining .NET Framework Web Forms, WCF, and packages.config applications.",
        "Maintains .NET Framework applications with Web Forms, WCF, and binding redirects.",
    )

    errors, _warnings = validate_skill.validate_skill(skill_dir)

    assert_contains_all(errors, "dotnet-legacy", ".NET Framework")


def test_validate_skill_accepts_modern_dotnet_framework_handoff(tmp):
    skill_dir = write_dotnet_naming_fixture(
        tmp,
        "dotnet-modern-review",
        (
            "Use when reviewing modern .NET services. "
            "Use dotnet-legacy for .NET Framework maintenance."
        ),
        (
            "Reviews modern .NET services and routes .NET Framework maintenance "
            "to dotnet-legacy."
        ),
    )

    errors, _warnings = validate_skill.validate_skill(skill_dir)

    assert_lacks(errors, "dotnet-legacy")


def test_validate_skill_accepts_modern_dotnet_legacy_word_without_framework_surface(tmp):
    skill_dir = write_dotnet_naming_fixture(
        tmp,
        "dotnet-quality-check",
        "Use when running modern .NET quality checks and analyzer evidence.",
        "Flags legacy ASP.NET API versioning packages in modern projects.",
    )

    errors, _warnings = validate_skill.validate_skill(skill_dir)

    assert_lacks(errors, "dotnet-legacy")


def test_validate_skill_rejects_legacy_dotnet_without_framework_surface(tmp):
    skill_dir = write_dotnet_naming_fixture(
        tmp,
        "dotnet-legacy-helper",
        "Use when managing older application code without changing its runtime.",
        "Helps with older application code that is not being migrated yet.",
    )

    errors, _warnings = validate_skill.validate_skill(skill_dir)

    assert_contains(errors, "dotnet-legacy skills must explicitly name")


def test_agent_compatibility_warns_on_frontmatter_portability(tmp):
    skill_dir = write_skill(tmp)
    text = skill_md(skill_dir).read_text(encoding="utf-8")
    write_text(
        skill_md(skill_dir),
        text.replace(
            f"description: {DEMO_DESCRIPTION}",
            f'description: "{DEMO_DESCRIPTION}"',
        ),
    )

    errors, warnings = validate_agent_compatibility.check_skill_compatibility(tmp)

    assert_empty(errors)
    assert_contains(warnings, "quoted frontmatter")


def test_agent_compatibility_summary_can_omit_passing_checks(tmp):
    report = {
        "schema_version": 1,
        "tool": "validate-agent-compatibility",
        "ok": True,
        "status": "passed",
        "checks": [{"name": "adapters", "ok": True, "summary": "ok"}],
        "adapter_surfaces": [{"surface": "codex", "exists": True}],
        "errors": [],
        "warnings": [],
    }
    summary = validate_agent_compatibility.summarize_report(report, compact=True)

    assert_fields(
        summary,
        check_count=1,
        surface_count=1,
        generated_surface_count=0,
        canonical_surface_count=1,
        failed_check_count=0,
    )
    assert_has_all(summary["supported_surfaces"], "codex")
    assert_keys_lack(summary, "checks", "adapter_surfaces", "errors", "warnings", "root")


def test_agent_compatibility_reports_navigation_strategy_for_surfaces(tmp):
    surfaces = validate_agent_compatibility.adapter_surfaces(tmp)

    codex = next(surface for surface in surfaces if surface["surface"] == "codex")
    claude = next(surface for surface in surfaces if surface["surface"] == "claude_code")
    copilot = next(surface for surface in surfaces if surface["surface"] == "github_copilot")

    assert_field(codex, "navigation_strategy", "root-router-loads-nested-files")
    assert_field(claude, "navigation_strategy", "generated-nested-skill-adapters")
    assert_field(copilot, "navigation_strategy", "generated-compact-root-instructions")
    assert_field(codex, "first_orientation_file", "automations/navigation/artifacts/maps/HANDOFF.md")
    assert_field(codex, "raw_navigation_json", "tool-only")


def test_agent_compatibility_guardrails_reject_adapter_raw_navigation_json(tmp):
    write_text(tmp / "AGENTS.md", "# Repo\n\nRoute to HANDOFF.md; raw navigation JSON is tool-only.\n")
    write_text(
        tmp / ".github" / "copilot-instructions.md",
        "Read automations/navigation/artifacts/maps/project-map.json before planning.",
    )

    surfaces = validate_agent_compatibility.adapter_surfaces(tmp)
    report = validate_agent_compatibility.adapter_context_guardrail_report(tmp, surfaces)

    assert_not_ok(report)
    assert_field(report, "finding_count", 1)
    assert_field(report["findings"][0], "path", ".github/copilot-instructions.md")


def test_public_commands_cover_daily_surface(tmp):
    expected = {
        ("new",): ["new"],
        ("new", "--help"): ["new", "--help"],
        ("status", "--fast"): ["dashboard", "--fast"],
        ("route", "inspect a PDF"): ["explain-route", "inspect a PDF"],
        ("check", "--deep"): ["validate", "--deep"],
        ("finish", "--release-full"): ["finish", "--release-full"],
        ("review", "--workflow", "story-flow", "--plan"): ["review-workflow", "--name", "story-flow", "--plan"],
        ("review", ".agents/skills/demo-skill", "--format", "json"): [
            "review-skill",
            "--skill",
            ".agents/skills/demo-skill",
            "--format",
            "json",
        ],
        ("new", "--kind", "workflow", "--name", "story-flow", "--summary", "Demo"): [
            "create-workflow",
            "--name",
            "story-flow",
            "--summary",
            "Demo",
        ],
        ("new", "--kind", "skill", "--name", "demo-skill"): ["new-skill-checklist", "--name", "demo-skill"],
    }
    for raw, normalized in expected.items():
        assert repo_public_commands.normalize_public_commands(list(raw)) == normalized


def test_public_new_help_mentions_skill_and_workflow(tmp):
    parser = repo_cli_parser.build_parser()
    help_text = parse_help(parser, ["new", "--help"])
    assert_has_all(help_text, "Public creation front door", "--kind", "workflow", "new --kind workflow")


def test_public_workflow_help_mentions_common_actions(tmp):
    parser = repo_cli_parser.build_parser()
    help_text = parse_help(parser, ["workflow", "--help"])
    assert_has_all(help_text, "start", "resume", "scorecard", "smoke")


def test_public_workflow_start_help_mentions_discovery_and_story_bug(tmp):
    help_text = repo_doctor_groups.workflow_start_help()
    assert_has_all(help_text, "which-workflow", "user-story-workflow", "bug-ticket-workflow", "--summary", "--compact")


def test_public_which_workflow_help_mentions_plain_language_routing(tmp):
    parser = repo_cli_parser.build_parser()
    help_text = parse_help(parser, ["which-workflow", "--help"])
    assert_has_all(help_text, "natural-language", "workflow", "user-story-workflow", "bug-ticket-workflow")


def test_repo_doctor_split_keeps_public_surface(tmp):
    assert_same_attrs(repo_doctor, repo_doctor_checks, "git_dirty_state", "github_actions_status", "tracked_bundle_integrity")
    assert_same_attrs(repo_doctor, repo_doctor_benchmarks, "benchmark_doctor_report", "benchmark_group")
    assert_same_attrs(repo_doctor, repo_doctor_clone, "fresh_clone_smoke_report")
    assert_same_attrs(repo_doctor, repo_doctor_groups, "skill_group", "workflow_group")
    assert callable(repo_doctor.release_readiness)


def completed(args, returncode=0, stdout=""):
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")


def test_github_actions_ignores_stale_runs_when_actions_are_disabled(tmp):
    def runner(command, **_):
        command_text = " ".join(str(item) for item in command)
        if "actions/permissions" in command_text:
            return completed(command, stdout=json.dumps({"enabled": False, "allowed_actions": None}))
        if command[:3] == ["gh", "run", "list"]:
            return completed(
                command,
                stdout=json.dumps(
                    [
                        {
                            "databaseId": 1,
                            "status": "completed",
                            "conclusion": "failure",
                            "workflowName": "Validate skills",
                            "headSha": "old-sha",
                            "createdAt": "2026-05-23T11:41:58Z",
                            "url": "https://example.test/run/1",
                        }
                    ]
                ),
            )
        return completed(command)

    with patched_attrs(
        repo_doctor_checks,
        find_gh=lambda: "gh",
        gh_auth_status=lambda root, gh: completed([gh, "auth", "status"]),
        github_repo_name=lambda root, gh: "owner/repo",
        git_current_branch=lambda root: "feature/demo",
        git_head_sha=lambda root: "current-sha",
    ):
        report = repo_doctor_checks.github_actions_status(tmp, runner=runner)

    assert_ok(report)
    assert_status(report, "disabled")
    assert_has_all(report, "latest_run")


def test_github_hygiene_skips_disabled_dependabot_alerts(tmp):
    def fake_run(command, **_):
        command_text = " ".join(str(item) for item in command)
        if "repo view" in command_text:
            return completed(command, stdout=json.dumps({"nameWithOwner": "owner/repo"}))
        if "pr list" in command_text:
            return completed(command, stdout="[]")
        if "dependabot/alerts" in command_text:
            return completed(
                command,
                returncode=1,
                stdout='{"message":"Dependabot alerts are disabled for this repository.","status":"403"}',
            )
        return completed(command)

    with patched_attrs(repo_doctor_checks, find_gh=lambda: "gh"), patched_attrs(repo_doctor_checks.subprocess, run=fake_run):
        report = repo_doctor_checks.github_hygiene(tmp)

    assert_ok(report)
    assert_status(report, "clean")
    assert_contains(report["skipped"], "Dependabot alerts are disabled")


def test_fresh_clone_smoke_enables_git_longpaths(tmp):
    commands = []

    def runner(command, **_):
        record_command(commands, command)
        return completed(command, stdout="ok")

    with patched_attrs(repo_doctor_clone.shutil, which=lambda name: "git"):
        report = repo_doctor_clone.fresh_clone_smoke_report(tmp, runner=runner)

    assert_ok(report)
    clone_command = commands[0]
    assert clone_command[:4] == ["git", "-c", "core.longpaths=true", "clone"]
    assert_has_all(commands, ["git", "config", "core.longpaths", "true"])


def test_release_evidence_summary_is_compact(tmp):
    _ = tmp
    report = {
        "schema_version": 1,
        "tool": "release-evidence",
        "ok": False,
        "status": "failed",
        "scope": "full",
        "checks": [
            {"name": "release_readiness", "ok": False, "result": {"status": "issues-found", "checks": [{"nested": True}]}},
            {"name": "repo_health", "ok": True, "result": {"status": "passed"}},
        ],
        "issues": ["fresh clone failed"],
        "warnings": ["advisory"],
        "skipped": ["deep validation skipped"],
        "next_action": "resolve release evidence issues",
    }

    compact = repo_doctor.summarize_release_evidence_report(report, compact=True)

    assert_summary(compact, check_count=2, failed_check_count=1)
    assert_field(compact, "failed_checks", [{"name": "release_readiness", "status": "issues-found"}])
    assert_lacks_all(compact, "checks")


def test_workflow_hooks_alias_accepts_summary(tmp):
    rc, command = workflow_group_command(tmp, ["hooks", "--name", "story-flow", "--summary", "--format", "json"])

    assert rc == 0
    assert command == ["hooks-run", "--root", str(tmp), "--format", "json", "--name", "story-flow", "--compact"]


def test_workflow_smoke_alias_accepts_dry_run(tmp):
    rc, command = workflow_group_command(
        tmp,
        ["smoke", "--name", "story-flow", "--dry-run", "--summary", "--compact", "--format", "json"],
    )

    assert rc == 0
    assert command == [
        "smoke-workflows",
        "--root",
        str(tmp),
        "--format",
        "json",
        "--name",
        "story-flow",
        "--dry-run",
        "--summary",
        "--compact",
    ]


def test_workflow_workers_alias_forwards_delegation_attestation(tmp):
    rc, command = workflow_group_command(
        tmp,
        [
            "workers",
            "--name",
            "story-flow",
            "--phase",
            "intake",
            "--delegation-requested",
            "--task-class",
            "independent-read-heavy",
            "--compact",
            "--format",
            "json",
        ],
    )

    assert rc == 0
    assert command == [
        "workflow-workers",
        "--root",
        str(tmp),
        "--format",
        "json",
        "--name",
        "story-flow",
        "--phase",
        "intake",
        "--delegation-requested",
        "--task-class",
        "independent-read-heavy",
        "--compact",
    ]


def test_workflow_checkpoint_alias_accepts_compact_check(tmp):
    rc, command = workflow_group_command(
        tmp, ["checkpoint", "--name", "story-flow", "--check", "--compact", "--format", "json"]
    )

    assert rc == 0
    assert command == [
        "checkpoint-run",
        "--root",
        str(tmp),
        "--format",
        "json",
        "--name",
        "story-flow",
        "--check",
        "--compact",
    ]


def test_workflow_context_alias_forwards_runtime_observation_file(tmp):
    observation = "automations/story-flow/runs/run-a/validation/runtime-observation.json"
    rc, command = workflow_group_command(
        tmp,
        [
            "context",
            "--name",
            "story-flow",
            "--run-id",
            "run-a",
            "--runtime-observation-file",
            observation,
            "--write",
            "--format",
            "json",
        ],
    )

    assert rc == 0
    assert command == [
        "context-run",
        "--root",
        str(tmp),
        "--format",
        "json",
        "--name",
        "story-flow",
        "--run-id",
        "run-a",
        "--write",
        "--runtime-observation-file",
        observation,
    ]


def test_workflow_eval_alias_accepts_summary_compact_for_named_suite(tmp):
    rc, command = workflow_group_command(
        tmp,
        [
            "eval",
            "--name",
            "story-flow",
            "--suite",
            "automations/story-flow/suites/workflow-evals.json",
            "--summary",
            "--compact",
            "--format",
            "json",
        ],
    )

    assert rc == 0
    assert command == [
        "eval-workflow",
        "--root",
        str(tmp),
        "--name",
        "story-flow",
        "--suite",
        "automations/story-flow/suites/workflow-evals.json",
        "--format",
        "json",
        "--summary",
        "--compact",
    ]


def test_workflow_template_resolve_alias_accepts_summary_compact(tmp):
    rc, command = workflow_group_command(
        tmp,
        ["template", "resolve", "--name", "story-flow", "--profile", "audit", "--summary", "--compact", "--format", "json"],
    )

    assert rc == 0
    assert command == [
        "template-run",
        "--root",
        str(tmp),
        "resolve",
        "--name",
        "story-flow",
        "--profile",
        "audit",
        "--format",
        "json",
        "--summary",
        "--compact",
    ]


def test_workflow_template_lint_alias_accepts_summary_compact(tmp):
    rc, command = workflow_group_command(
        tmp,
        ["template", "lint", "--name", "story-flow", "--summary", "--compact", "--format", "json"],
    )

    assert rc == 0
    assert command == [
        "template-run",
        "--root",
        str(tmp),
        "lint",
        "--name",
        "story-flow",
        "--format",
        "json",
        "--summary",
        "--compact",
    ]


def test_workflow_template_gate_alias_accepts_summary_compact(tmp):
    rc, command = workflow_group_command(
        tmp,
        ["template", "gate-check", "--all", "--summary", "--compact", "--format", "json"],
    )

    assert rc == 0
    assert command == [
        "template-run",
        "--root",
        str(tmp),
        "gate-check",
        "--all",
        "--format",
        "json",
        "--summary",
        "--compact",
    ]


def test_workflow_metadata_alias_accepts_summary_compact(tmp):
    rc, command = workflow_group_command(
        tmp,
        ["metadata", "inspect", "--name", "story-flow", "--summary", "--compact", "--format", "json"],
    )

    assert rc == 0
    assert command == [
        "metadata-run",
        "--root",
        str(tmp),
        "inspect",
        "--name",
        "story-flow",
        "--format",
        "json",
        "--summary",
        "--compact",
    ]


def test_workflow_start_alias_accepts_summary_compact(tmp):
    rc, command = workflow_group_command(
        tmp,
        ["start", "--name", "story-flow", "--run-id", "run-a", "--profile", "audit", "--summary", "--compact", "--format", "json"],
    )

    assert rc == 0
    assert command == [
        "start-run",
        "--root",
        str(tmp),
        "--name",
        "story-flow",
        "--format",
        "json",
        "--run-id",
        "run-a",
        "--profile",
        "audit",
        "--summary",
        "--compact",
    ]


def test_workflow_resume_alias_accepts_summary_compact(tmp):
    rc, command = workflow_group_command(
        tmp,
        ["resume", "--name", "story-flow", "--run-id", "run-a", "--summary", "--compact", "--format", "json"],
    )

    assert rc == 0
    assert command == [
        "resume-run",
        "--root",
        str(tmp),
        "--name",
        "story-flow",
        "--format",
        "json",
        "--run-id",
        "run-a",
        "--summary",
        "--compact",
    ]


def test_workflow_finish_alias_accepts_summary_compact(tmp):
    rc, command = workflow_group_command(
        tmp,
        ["finish", "--name", "story-flow", "--run-id", "run-a", "--summary", "--compact", "--format", "json"],
    )

    assert rc == 0
    assert command == [
        "finish-run",
        "--root",
        str(tmp),
        "--name",
        "story-flow",
        "--format",
        "json",
        "--run-id",
        "run-a",
        "--summary",
        "--compact",
    ]


def test_skill_doctor_all_summary_compact_omits_passing_rows(tmp):
    write_skill(tmp)

    rc, report = capture_json(
        repo_doctor_groups.skill_group,
        Namespace(skill_args=["doctor", "--all", "--summary", "--compact", "--format", "json"]),
        tmp,
        lambda _args, _root: 99,
    )
    assert rc == 0
    assert_summary(report, skills=1)
    assert_field(report["summary"], "risk_count", len(report["summary"]["risks"]))
    assert_lacks_all(report, "skills")


def test_setup_summary_compact_omits_captured_output(_tmp):
    report = {
        "schema_version": 1,
        "tool": "setup",
        "ok": True,
        "status": "ready",
        "root": "D:/repo",
        "checks": ["generated artifacts checked"],
        "actions": {
            "sync": {
                "ok": True,
                "status": 0,
                "mode": "check",
                "stdout": "large generated output",
                "stderr": "",
            },
            "validation": {"ok": True, "status": 0, "mode": "normal", "stdout": "", "stderr": ""},
        },
        "linked_skills": {
            "Codex": {
                "planned": 0,
                "linked": 0,
                "copied": 0,
                "already_present": 17,
                "missing": 0,
                "skipped": 0,
                "target_path": "C:/Users/example/.codex/skills",
            }
        },
        "skipped": [],
        "failures": [],
        "next_prompt": "long first prompt",
    }

    summary = repo_setup.setup_summary(report, compact=True)

    assert_ok(summary)
    assert_field(summary, "action_count", 2)
    assert_keys_lack(summary, "actions", "next_prompt", "root")
    assert_field(summary["linked_skills"], "already_present", 17)
    assert set(summary["linked_skills"]) == {"tools", "already_present"}


def test_setup_summary_compact_summarizes_repeated_skill_link_collisions(_tmp):
    report = {
        "schema_version": 1,
        "tool": "setup",
        "ok": True,
        "status": "ready",
        "checks": [],
        "actions": {},
        "linked_skills": {},
        "skipped": [
            "project initialization needs review or a write-mode setup run",
            "Skipping demo-skill for Codex: C:/skills/demo-skill already exists and points to C:/skills/demo-skill.",
            "Skipping other-skill for Codex: C:/skills/other-skill already exists and points to C:/skills/other-skill.",
        ],
        "failures": [],
    }

    summary = repo_setup.setup_summary(report, compact=True)

    assert_field(summary, "skipped_count", 3)
    assert_field(summary, "status_detail", "ready-with-advisories")
    assert_field(summary, "advisory_count", 3)
    assert_contains(summary["skipped"], "project initialization needs review")
    assert_contains(summary["skipped"], "2 skill link collision(s) skipped")
    assert_lacks(summary["skipped"], "Skipping demo-skill")
    assert_lacks(summary["skipped"], "Skipping other-skill")


def test_setup_reports_missing_ripgrep_as_optional(_tmp):
    with patched_attrs(repo_setup.shutil, which=lambda _name: None):
        report = repo_setup.ripgrep_tool_report(
            Namespace(check=True, dry_run=False, install_rg=False, install_rg_portable=False, no_tool_prompts=True),
            _tmp,
        )

    assert_ok(report)
    assert_status(report, "missing")
    assert_has_all(report["suggested"], "setup --install-rg-portable")


def test_setup_can_install_portable_ripgrep(tmp):
    calls = []

    def fake_install(root):
        calls.append(root)
        return {
            "ok": True,
            "status": "installed",
            "source": "portable",
            "platform": "windows-x64",
            "path": str(agent_path(root, "tools", "cache", "ripgrep", "windows-x64", "rg.exe")),
            "version": "ripgrep 15.1.0",
        }

    with patched_attrs(repo_setup.repo_portable_tools, install_portable_ripgrep=fake_install):
        report = repo_setup.ripgrep_tool_report(
            Namespace(check=False, dry_run=False, install_rg=False, install_rg_portable=True, no_tool_prompts=True),
            tmp,
        )

    assert calls == [tmp]
    assert_ok(report)
    assert_fields(report, status="installed", source="portable")


def test_portable_tools_report_validates_manifest_without_install(tmp):
    write_json(
        tmp / ".agents" / "skills" / "skill-manager" / "assets" / "tools" / "ripgrep" / "manifest.json",
        {
            "schema_version": 1,
            "tool": "ripgrep",
            "version": "15.1.0",
            "source": "BurntSushi/ripgrep",
            "license": "MIT OR Unlicense",
            "assets": {
                repo_portable_tools.platform_key(): {
                    "archive_type": "zip",
                    "executable": "rg.exe",
                    "name": "ripgrep.zip",
                    "sha256": "a" * 64,
                    "size": 123,
                    "url": "https://example.test/ripgrep.zip",
                }
            },
        },
    )

    report = repo_portable_tools.portable_tools_report(tmp)

    assert_ok(report)
    assert_summary(report, manifest_count=1, valid_manifest_count=1)
    assert_status(report["installed"][0], "missing")

    required = repo_portable_tools.portable_tools_report(tmp, require_installed=True)

    assert_not_ok(required)
    assert_summary(required, issue_count=1, require_installed=True)
    assert_has_all(required["issues"][0], "ripgrep", "missing", repo_portable_tools.platform_key(), ".agents")


def test_import_review_ignores_git_internals_but_keeps_project_hooks(tmp):
    write_text(tmp / ".git" / "hooks" / "pre-commit.sample", "echo ignored")
    write_text(tmp / ".githooks" / "pre-commit", "echo reviewed")
    report = analyze_location.analyze_target(
        str(tmp),
        tmp,
        max_files=50,
        max_text_files=50,
        review_profile="import",
    )

    review = report["import_review"]
    assert ".git/hooks/pre-commit.sample" not in review["facts"]["hook_files"]
    assert ".githooks/pre-commit" in review["facts"]["hook_files"]


def test_setup_check_reports_project_initialization_without_writes(tmp):
    calls = []

    def fake_navigation(_root, *args, **_kwargs):
        calls.append(list(args))
        return {"ok": True, "status": "ok"}

    args = Namespace(check=True, dry_run=False)
    with patched_attrs(repo_setup, repo_navigation_command=fake_navigation):
        report = repo_setup.build_project_initialization_report(args, tmp)

    assert_ok(report)
    assert_field(report, "ready", False)
    assert_status(report, "needs-initialization")
    assert calls == []
    assert "automations/navigation/WORKFLOW.md" in report["navigation"]["missing"]


def test_setup_write_initializes_navigation_and_project_context(tmp):
    calls = []
    context_checks = {"count": 0}

    def fake_navigation(_root, *args, **_kwargs):
        calls.append(list(args))
        if args[0] == "project-context":
            context_checks["count"] += 1
            if context_checks["count"] == 1:
                return {"ok": False, "status": "needs-attention", "issues": ["missing project context"]}
            return {"ok": True, "status": "ok", "context_path": "docs/project/project-context.md"}
        return {"ok": True, "status": "installed", "written": ["automations/navigation/WORKFLOW.md"]}

    def fake_generator(_root, *, overwrite=False):
        return {"ok": True, "status": "written", "written": ["docs/project/project-context.md"], "overwrite": overwrite}

    args = Namespace(check=False, dry_run=False)
    with patched_attrs(
        repo_setup,
        repo_navigation_command=fake_navigation,
        project_context_generator_command=fake_generator,
    ):
        report = repo_setup.build_project_initialization_report(args, tmp)

    assert_ok(report)
    assert_field(report, "ready", True)
    assert_status(report, "ready")
    assert calls[0][0] == "install"
    assert [call[0] for call in calls].count("project-context") == 2
    assert_field(report["project_context"]["generation"], "status", "written")
    assert_field(report["project_policy"], "status", "ready")
    assert (tmp / repo_policy.PROJECT_POLICY_PATH).is_file()
    policy_document = read_json(tmp / repo_policy.PROJECT_POLICY_PATH)
    assert policy_document == repo_policy.default_policy_document()


def test_setup_write_refreshes_navigation_after_post_sync(tmp):
    project_init_calls = []

    def fake_project_initialization(_args, _root):
        project_init_calls.append("project-initialization")
        return {"ok": True, "ready": True, "status": "ready", "mode": "write"}

    args = Namespace(
        check=False,
        dry_run=False,
        install_rg=False,
        install_rg_portable=False,
        no_tool_prompts=True,
        no_link_skills=True,
        skill_source_path=None,
        targets=["Codex"],
        mode="auto",
        codex_skills_path=str(tmp / "codex"),
        claude_skills_path=str(tmp / "claude"),
        copilot_skills_path=str(tmp / "copilot"),
        deep=False,
    )

    with patched_attrs(repo_setup, build_project_initialization_report=fake_project_initialization):
        report = repo_setup.build_setup_report(
            args,
            tmp,
            sync_all_func=lambda _root, check: 0,
            validate_func=lambda _root: 0,
            deep_validate_func=lambda _root: 0,
    )

    assert_ok(report)
    assert project_init_calls == ["project-initialization", "project-initialization"]
    assert "final_project_initialization" in report["actions"]


def test_setup_check_auto_skips_skill_links_for_temporary_smoke_target(tmp):
    write_skill(tmp)
    write_json(
        tmp / repo_common.HARNESS_SMOKE_TARGET_MARKER_REL,
        {
            "schema_version": 1,
            "tool": "install-harness-smoke",
            "temporary_validation_target": True,
        },
    )
    args = Namespace(
        check=True,
        dry_run=False,
        install_rg=False,
        install_rg_portable=False,
        no_tool_prompts=True,
        no_link_skills=False,
        skill_source_path=None,
        targets=["Codex", "Claude", "Copilot"],
        mode="auto",
        codex_skills_path=str(tmp / "missing" / "codex"),
        claude_skills_path=str(tmp / "missing" / "claude"),
        copilot_skills_path=str(tmp / "missing" / "copilot"),
        deep=False,
    )

    with patched_attrs(
        repo_setup,
        build_project_initialization_report=lambda _args, _root: {
            "ok": True,
            "ready": True,
            "status": "ready",
            "mode": "check",
        },
    ):
        report = repo_setup.build_setup_report(
            args,
            tmp,
            sync_all_func=lambda _root, check: 0,
            validate_func=lambda _root: 0,
            deep_validate_func=lambda _root: 0,
        )

    assert_ok(report)
    assert_status(report["actions"]["link_skills"], "skipped-temporary-target")
    assert_contains(report["skipped"], repo_common.HARNESS_SMOKE_TARGET_MARKER_REL)
    assert_empty(report["failures"])


def test_setup_auto_skips_skill_links_for_installed_consumer(tmp):
    write_skill(tmp)
    write_json(
        tmp / repo_common.HARNESS_INSTALL_MANIFEST_REL,
        {
            "schema_version": 1,
            "files": [
                {
                    "path": ".agents/manage.py",
                    "sha256": "fixture",
                }
            ],
        },
    )
    args = Namespace(
        check=False,
        dry_run=False,
        install_rg=False,
        install_rg_portable=False,
        no_tool_prompts=True,
        no_link_skills=False,
        skill_source_path=None,
        targets=["Codex", "Claude", "Copilot"],
        mode="auto",
        codex_skills_path=str(tmp / "missing" / "codex"),
        claude_skills_path=str(tmp / "missing" / "claude"),
        copilot_skills_path=str(tmp / "missing" / "copilot"),
        deep=False,
    )

    with patched_attrs(
        repo_setup,
        build_project_initialization_report=lambda _args, _root: {
            "ok": True,
            "ready": True,
            "status": "ready",
            "mode": "write",
        },
    ):
        report = repo_setup.build_setup_report(
            args,
            tmp,
            sync_all_func=lambda _root, check: 0,
            validate_func=lambda _root: 0,
            deep_validate_func=lambda _root: 0,
        )

    assert_ok(report)
    assert_status(report["actions"]["link_skills"], "skipped-installed-consumer")
    assert_contains(report["skipped"], ".agents/harness.lock.json")
    assert_empty(report["failures"])


def test_setup_check_still_fails_missing_skill_links_without_smoke_marker(tmp):
    write_skill(tmp)
    args = Namespace(
        check=True,
        dry_run=False,
        install_rg=False,
        install_rg_portable=False,
        no_tool_prompts=True,
        no_link_skills=False,
        skill_source_path=None,
        targets=["Codex"],
        mode="auto",
        codex_skills_path=str(tmp / "missing" / "codex"),
        claude_skills_path=None,
        copilot_skills_path=None,
        deep=False,
    )

    with patched_attrs(
        repo_setup,
        build_project_initialization_report=lambda _args, _root: {
            "ok": True,
            "ready": True,
            "status": "ready",
            "mode": "check",
        },
    ):
        report = repo_setup.build_setup_report(
            args,
            tmp,
            sync_all_func=lambda _root, check: 0,
            validate_func=lambda _root: 0,
            deep_validate_func=lambda _root: 0,
        )

    assert_not_ok(report)
    assert_status(report, "check-failed")
    assert_contains(report["failures"], "Missing Codex skill link or copy")


def test_setup_check_warns_on_user_owned_skill_name_collisions(tmp):
    write_skill(tmp)
    write_text(tmp / "codex" / "demo-skill" / "SKILL.md", "user-owned skill")
    args = Namespace(
        check=True,
        dry_run=False,
        install_rg=False,
        install_rg_portable=False,
        no_tool_prompts=True,
        no_link_skills=False,
        skill_source_path=None,
        targets=["Codex"],
        mode="auto",
        codex_skills_path=str(tmp / "codex"),
        claude_skills_path=None,
        copilot_skills_path=None,
        deep=False,
    )

    with patched_attrs(
        repo_setup,
        build_project_initialization_report=lambda _args, _root: {
            "ok": True,
            "ready": True,
            "status": "ready",
            "mode": "check",
        },
    ):
        report = repo_setup.build_setup_report(
            args,
            tmp,
            sync_all_func=lambda _root, check: 0,
            validate_func=lambda _root: 0,
            deep_validate_func=lambda _root: 0,
        )

    assert_ok(report)
    assert_status(report, "ready")
    assert_status(report["actions"]["link_skills"], "warnings found")
    assert_empty(report["failures"])
    assert_contains(report["skipped"], "already exists")


def test_setup_markdown_summarizes_repeated_skill_link_collisions(tmp):
    report = {
        "ok": True,
        "status": "ready",
        "root": str(tmp),
        "python": "3.12",
        "ripgrep": {"status": "present"},
        "actions": {
            "python": {"ok": True, "version": "3.12"},
            "ripgrep": {"ok": True, "status": "present"},
            "sync": {"ok": True, "status": 0, "mode": "check"},
            "project_initialization": {"ok": True, "status": "ready", "mode": "check"},
            "link_skills": {"ok": True, "status": "warnings found", "mode": "check"},
            "validation": {"ok": True, "status": 0, "mode": "normal"},
        },
        "checks": [],
        "linked_skills": {},
        "failures": [],
        "skipped": [
            "project initialization needs review or a write-mode setup run",
            "Skipping demo-skill for Codex: C:/skills/demo-skill already exists and points to C:/skills/demo-skill.",
            "Skipping other-skill for Codex: C:/skills/other-skill already exists and points to C:/skills/other-skill.",
        ],
        "next_prompt": "Read AGENTS.md",
    }

    rendered = repo_setup.render_setup_report(report)

    assert "Status: ready (ready-with-advisories" in rendered
    assert "project initialization needs review" in rendered
    assert "2 skill link collision(s) skipped; see link-skills or non-compact setup JSON output for details." in rendered
    assert "Skipping demo-skill" not in rendered
    assert "Skipping other-skill" not in rendered


def write_harness_install_fixture(root):
    write_files(
        root,
        {**HARNESS_ACTIVE_FILES, **HARNESS_STATE_FILES},
    )
    write_json(
        agent_path(root, "harness-payload.json"),
        {
            "schema_version": 2,
            "tool": "install-harness-payload",
            "owner": "skill-manager",
            "required_features": ["core"],
            "feature_bundles": {
                "core": {
                    "description": "Explicit schema-version-2 fixture surface.",
                    "include_globs": ["**"],
                    "requires": [],
                }
            },
            "profiles": {
                name: {"description": f"{name.title()}.", "features": ["core"], "exclude_features": []}
                for name in ("minimal", "standard", "full")
            },
            "include_roots": list(repo_harness_install.ROOT_PAYLOAD_ENTRIES),
            "exclude_globs": list(repo_harness_install.REQUIRED_GENERAL_EXCLUDES),
            "state_exclude_globs": list(repo_harness_install.REQUIRED_STATE_EXCLUDES),
        },
    )


def harness_source(tmp):
    source = tmp / "source"
    write_harness_install_fixture(source)
    return source


def harness_fixture(tmp, target_name="target"):
    source = harness_source(tmp)
    target = tmp / target_name
    return source, target


def copy_tree(source, target):
    for item in sorted(source.rglob("*"), key=lambda path: path.relative_to(source).as_posix()):
        relative = item.relative_to(source)
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)


def test_install_harness_dry_run_excludes_local_state(tmp):
    source, target = harness_fixture(tmp)

    report = repo_harness_install.install_harness_report(source, target, dry_run=True)
    planned = {str(item["path"]) for item in report["planned"] if isinstance(item, dict)}
    excluded = set(report["excluded"])

    assert_ok(report)
    assert_status(report, "planned")
    assert set(HARNESS_ACTIVE_FILES) <= planned
    assert set(HARNESS_STATE_FILES) <= excluded
    assert_lacks(planned, "/runs")
    assert_missing(target)
    assert_fields(report, clean_state=True, next_commands=[install_wizard_apply_command(target, "standard")])


def test_install_harness_quotes_target_paths_in_wizard_and_cd_handoffs(tmp):
    target = tmp / "Consumer Project"
    profile = {"name": "minimal"}

    dry_run = repo_harness_install.install_next_commands(
        target,
        dry_run=True,
        selected_profile=profile,
        run_setup_check=False,
    )
    applied = repo_harness_install.install_next_commands(
        target,
        dry_run=False,
        selected_profile=profile,
        run_setup_check=False,
    )

    assert_field(
        {"command": dry_run[0]},
        "command",
        f'python -B .agents/manage.py install-wizard --target "{target}" --profile minimal --apply',
    )
    assert_field({"command": applied[0]}, "command", f'cd "{target}"')


def test_repo_harness_render_support_matches_public_exports(tmp):
    _ = tmp
    from repo_support import repo_harness_render

    assert_same_attrs(
        repo_harness_install,
        repo_harness_render,
        "limited_rows",
        "render_markdown",
        "render_copy_contract",
        "render_public_export",
        "print_report",
    )


def test_install_harness_copies_dotnet_build_ignores(tmp):
    source, target = harness_fixture(tmp)

    report = repo_harness_install.install_harness_report(source, target, dry_run=False)
    gitignore = (target / ".gitignore").read_text(encoding="utf-8")

    assert_ok(report)
    assert_has_all(gitignore, "**/bin/", "**/obj/")


def test_install_harness_uses_payload_manifest_for_roots_and_excludes(tmp):
    source, target = harness_fixture(tmp)
    write_text(source / "support" / "guide.md", "# Support Guide\n")
    write_text(source / "support" / "private" / "secret.md", "# Secret\n")
    manifest = read_json(agent_path(source, "harness-payload.json"))
    manifest["include_roots"] = ["AGENTS.md", ".agents", "support"]
    manifest["exclude_globs"].append("support/private/**")
    write_json(agent_path(source, "harness-payload.json"), manifest)

    report = repo_harness_install.install_harness_report(source, target, dry_run=True)
    planned = {str(item["path"]) for item in report["planned"] if isinstance(item, dict)}
    excluded = set(report["excluded"])

    assert_ok(report)
    assert_fields(report["payload_manifest"], path=".agents/harness-payload.json", source="file")
    assert_summary(report, manifest_include_roots=3)
    assert_has_all(planned, "support/guide.md")
    assert_has_all(excluded, "support/private/secret.md")
    assert_lacks_all(planned, "README.md")


def test_install_harness_rejects_unsafe_payload_manifest_paths(tmp):
    source, target = harness_fixture(tmp)
    manifest = read_json(agent_path(source, "harness-payload.json"))
    manifest["include_roots"] = ["/tmp"]
    write_json(agent_path(source, "harness-payload.json"), manifest)

    report = repo_harness_install.install_harness_report(source, target, dry_run=True)

    assert_not_ok(report)
    assert_status(report, "blocked")
    assert_contains(report["issues"], "unsafe path")
    assert_missing(target)


def test_install_harness_dry_run_returns_install_plan_without_writing_target(tmp):
    source, target = harness_fixture(tmp)

    report = repo_harness_install.install_harness_report(source, target, dry_run=True)
    plan = report["install_plan"]

    assert_ok(report)
    assert_fields(plan, tool="install-harness-plan", dry_run=True)
    assert_has_all(plan["proposed_writes"], "docs/start-here.md")
    assert_fields(plan["payload_manifest"], path=".agents/harness-payload.json")
    assert plan["validation_commands"][0] == "python -B .agents/manage.py setup --no-link-skills"
    assert_has_all(plan["validation_commands"], "python -B .agents/manage.py setup --check --no-link-skills")
    assert_has_all(plan["validation_commands"], "python -B .agents/manage.py next-action --summary --compact --format json")
    assert_status(report["install_plan_artifacts"]["json"], "planned")
    assert_status(report["install_plan_artifacts"]["markdown"], "planned")
    assert_missing(target)


def test_install_harness_writes_install_plan_artifacts(tmp):
    source, target = harness_fixture(tmp)

    report = repo_harness_install.install_harness_report(source, target, dry_run=False)
    plan_json = agent_path(target, "harness-install-plan.json")
    plan_markdown = agent_path(target, "harness-install-plan.md")
    plan = read_json(plan_json)

    assert_ok(report)
    assert_status(report["install_plan_artifacts"]["json"], "written")
    assert_status(report["install_plan_artifacts"]["markdown"], "written")
    assert_fields(plan, tool="install-harness-plan", dry_run=False)
    assert_has_all(plan["proposed_writes"], ".agents/harness-payload.json")
    assert_has_all(plan["rollback_notes"], "Review `.agents/harness.lock.json` for copied file hashes.")
    assert_has_all(plan_markdown.read_text(encoding="utf-8"), "## Proposed Writes")


def test_install_harness_preserves_fresh_consumer_readme_and_merges_gitignore(tmp):
    source, target = harness_fixture(tmp)
    write_text(target / "README.md", "# Consumer App\n")
    write_text(target / ".gitignore", "consumer.log\n")

    dry_run = repo_harness_install.install_harness_report(source, target, dry_run=True)

    assert_ok(dry_run)
    assert_field(dry_run, "collisions", [])
    assert_has_all(dry_run["preserved_existing"], "README.md")
    merged = {row["path"]: row for row in dry_run["merged"] if isinstance(row, dict)}
    assert_field(merged[".gitignore"], "reason", "merge-missing-harness-entries")
    assert_has_all(merged[".gitignore"]["added_entries"], "__pycache__/", "**/bin/", "**/obj/")
    assert (target / "README.md").read_text(encoding="utf-8") == "# Consumer App\n"
    assert (target / ".gitignore").read_text(encoding="utf-8") == "consumer.log\n"

    installed = repo_harness_install.install_harness_report(source, target, dry_run=False)

    assert_ok(installed)
    assert_field(installed, "collisions", [])
    assert (target / "README.md").read_text(encoding="utf-8") == "# Consumer App\n"
    gitignore = (target / ".gitignore").read_text(encoding="utf-8")
    assert_has_all(gitignore, "consumer.log", "# Reusable AI harness", "__pycache__/", "**/bin/", "**/obj/")
    manifest_paths = set(repo_harness_install.manifest_hashes(read_json(agent_path(target, "harness.lock.json"))))
    assert "README.md" not in manifest_paths
    assert ".gitignore" not in manifest_paths

    write_text(source / ".gitignore", "__pycache__/\n**/bin/\n**/obj/\n.harness-new-cache/\n")
    updated = repo_harness_install.install_harness_report(source, target, dry_run=False)

    assert_ok(updated)
    updated_gitignore = (target / ".gitignore").read_text(encoding="utf-8")
    assert_has_all(updated_gitignore, "consumer.log", ".harness-new-cache/")
    assert (target / "README.md").read_text(encoding="utf-8") == "# Consumer App\n"


def test_install_harness_profile_and_human_summary_are_reported(tmp):
    source, target = harness_fixture(tmp)

    report = repo_harness_install.install_harness_report(source, target, dry_run=True, profile="minimal")
    rendered = repo_harness_install.render_markdown(report)

    assert_ok(report)
    assert_name(report["profile"], "minimal")
    assert report["human_summary"]["headline"].startswith("Plan")
    assert_contains(report["human_summary"]["plain_changes"], "new harness files")
    assert_field(report, "next_commands", [install_wizard_apply_command(target, "minimal")])
    assert_lacks_all(rendered, "python -B .agents/manage.py local-ai", "setup --install-rg-portable")


def test_copy_contract_validation_accepts_harness_fixture(tmp):
    source = harness_source(tmp)

    report = repo_harness_install.copy_contract_report(source, profile="standard")

    assert_ok(report)
    assert_status(report, "passed")
    assert_summary(report, include_roots=13)
    assert_has_all(report["required_state_excludes"], ".agents/harness.lock.json", ".agents/harness-install.json")


def test_public_export_dry_run_and_write_exclude_state(tmp):
    source = harness_source(tmp)
    dry_target = tmp / "dry-target"
    write_target = tmp / "write-target"

    dry_run = repo_harness_install.public_export_report(source, dry_target, dry_run=True)
    written = repo_harness_install.public_export_report(source, write_target, dry_run=False)

    assert_ok(dry_run)
    assert_status(dry_run, "planned")
    assert_missing(dry_target)
    assert_ok(written)
    assert_status(written, "exported")
    assert_exists(write_target / "AGENTS.md")
    assert_exists(doc_path(write_target, "agent-start.md"))
    assert_missing(agent_path(write_target, "harness.lock.json"))
    assert_missing(agent_path(write_target, "harness-install.json"))
    assert_missing(agent_path(write_target, "local-ai", "cache", "state.json"))


def test_public_export_allows_repo_local_excluded_temp_target(tmp):
    source = harness_source(tmp)
    target = source / "temp" / "public-export"

    report = repo_harness_install.public_export_report(source, target, dry_run=True)

    assert_ok(report)
    assert_status(report, "planned")
    assert_missing(target)


def test_start_here_simple_guides_beginner_without_internal_detail(tmp):
    report = repo_onboarding.start_here_report(tmp, simple=True, profile="minimal")
    rendered = repo_onboarding.render_start_here(report)

    assert_fields(report, profile="minimal", mode="simple")
    assert_has_all(rendered, f"{INSTALL_WIZARD_TARGET} <project>", "python -B .agents/manage.py status --fast")
    install_commands = [command for command in report["commands"] if "install-wizard" in command]
    assert install_commands and all("--profile minimal" in command for command in install_commands), report["commands"]
    assert_lacks_all(rendered, ".agents/registry.json")


def test_start_here_source_maintainer_has_one_structured_primary_action(tmp):
    source = harness_source(tmp)

    report = repo_onboarding.start_here_report(source, simple=True, profile="minimal")
    primary = report.get("primary_next_action", {})

    assert report.get("role") == "source-maintainer", report
    assert report.get("onboarding_state") == "source-maintainer", report
    assert set(primary) >= {"command", "working_directory", "effect"}, primary
    assert_field(primary, "working_directory", str(source.resolve(strict=False)))
    assert_field(primary, "effect", "read")
    assert_has_all(primary["command"], "project-kickoff", "--profile minimal")

    explicit = repo_onboarding.start_here_report(source, target=source, simple=True, profile="minimal")
    assert_field(explicit, "role", "source-maintainer")
    assert_field(explicit, "onboarding_state", "source-maintainer")
    assert_field(explicit["primary_next_action"], "effect", "read")


def test_start_here_onboarding_state_matrix(tmp):
    source, target = harness_fixture(tmp)
    try:
        missing = repo_onboarding.start_here_report(source, target=target, profile="full")
    except TypeError as exc:
        raise AssertionError("start-here must accept a target for state-aware onboarding") from exc
    assert missing.get("onboarding_state") == "missing-target", missing
    assert_has_all(missing["primary_next_action"]["command"], "project-kickoff", "--profile full", "--apply")
    assert_field(missing["primary_next_action"], "effect", "write")

    installed = repo_harness_install.install_harness_report(source, target, dry_run=False, profile="full")
    assert_ok(installed)
    uninitialized = repo_onboarding.start_here_report(source, target=target, profile="full")
    assert_field(uninitialized, "role", "installed-target")
    assert_field(uninitialized, "onboarding_state", "uninitialized-target")
    assert_field(uninitialized["primary_next_action"], "command", "python -B .agents/manage.py setup")
    assert_field(uninitialized["primary_next_action"], "working_directory", str(target.resolve(strict=False)))

    write_text(
        doc_path(target, "project", "project-context.md"),
        "# Project Context\n\n- Context status: generated.\n\n## Technologies\n\n- Python 3.12\n",
    )
    review_required = repo_onboarding.start_here_report(source, target=target, profile="full")
    assert_field(review_required, "onboarding_state", "context-review-required")
    assert_has_all(review_required["primary_next_action"]["command"], "project-context-review", "--write-review")

    write_text(
        doc_path(target, "project", "project-context.md"),
        """# Project Context

- Context status: reviewed

## Technologies
- Python 3.12
## Validation Commands
- `python -B .agents/manage.py check`
## Generated Files And Boundaries
- Generated outputs are not edited by hand.
## External Systems
- None.
## Persistence
- Local files only.
## CI
- CI mirrors local checks.
## Security And Configuration Notes
- Secrets use environment variables.
## Freshness
- Last reviewed: 2026-07-10
""",
    )
    write_json(
        doc_path(target, "project", "validation", "validation-manifest.json"),
        {"commands": [{"command": "python -B .agents/manage.py check"}]},
    )
    ready = repo_onboarding.start_here_report(source, target=target, profile="full")
    assert_field(ready, "onboarding_state", "ready-target")
    assert_field(ready["primary_next_action"], "command", "python -B .agents/manage.py status --fast")
    assert_field(ready["primary_next_action"], "effect", "read")


def test_start_here_cli_accepts_target_for_state_aware_report(_tmp):
    parser = repo_cli_parser.build_parser()
    try:
        args = parser.parse_args(["start-here", "--target", "D:/Projects/NewProject", "--profile", "minimal"])
    except SystemExit as exc:
        raise AssertionError("start-here must expose its target state selector") from exc

    assert args.target == "D:/Projects/NewProject", vars(args)
    assert args.profile == "minimal", vars(args)


def test_install_wizard_noninteractive_recommends_command_and_next_steps(tmp):
    source, target = harness_fixture(tmp)

    report = repo_onboarding.install_wizard_report(
        source,
        target=target,
        profile="minimal",
        setup_check=True,
        install_rg_portable=True,
        bootstrap_local_ai=False,
        download_ai_models=False,
        apply=False,
    )

    assert_ok(report)
    assert_field(report, "profile", "minimal")
    assert_has_all(report["recommended_command"], "--profile minimal", "--install-rg-portable")
    assert report["next_steps"][0] == "Run the recommended install command, or rerun this wizard with --apply."
    assert_none(report["install_report"])

    applied = repo_onboarding.install_wizard_report(
        source,
        target=target,
        profile="minimal",
        setup_check=False,
        install_rg_portable=False,
        bootstrap_local_ai=False,
        download_ai_models=False,
        apply=True,
    )

    assert_status(applied, "installed")
    assert applied["next_steps"].count(OPEN_TARGET_PROJECT) == 1
    assert applied["next_steps"][0] == OPEN_TARGET_PROJECT

    blocked_target = tmp / "blocked-target"
    write_text(blocked_target / "AGENTS.md", "consumer edit\n")
    blocked = repo_onboarding.install_wizard_report(
        source,
        target=blocked_target,
        profile="minimal",
        setup_check=False,
        install_rg_portable=False,
        bootstrap_local_ai=False,
        download_ai_models=False,
        apply=True,
    )

    assert_not_ok(blocked)
    assert_status(blocked, "blocked")
    assert blocked["next_steps"][0] == "Review the install issues below."
    assert_lacks_all(blocked["next_steps"], OPEN_TARGET_PROJECT)
    assert_contains(blocked["next_steps"], "install-wizard --target <project> --profile minimal --apply")


def test_install_wizard_quotes_target_path_in_recommended_install_command(tmp):
    source, target = harness_fixture(tmp, "Consumer Project")

    report = repo_onboarding.install_wizard_report(
        source,
        target=target,
        profile="minimal",
        setup_check=False,
        install_rg_portable=False,
        bootstrap_local_ai=False,
        download_ai_models=False,
        apply=False,
    )

    assert_ok(report)
    assert_has_all(report["recommended_command"], f'--target "{target}"', "--profile minimal")


def test_install_profile_propagates_and_wizard_apply_converges(tmp):
    source, target = harness_fixture(tmp)
    low_level_calls = []

    def low_level_runner(_target_root, args, timeout_seconds):
        low_level_calls.append((list(args), timeout_seconds))
        return {"ok": True, "status": "passed", "command": " ".join(args), "returncode": 0, "output_tail": ""}

    installed = repo_harness_install.install_harness_report(
        source,
        target,
        dry_run=False,
        profile="minimal",
        command_runner=low_level_runner,
    )

    assert_ok(installed)
    assert low_level_calls == [], "low-level install-harness must remain copy-only by default"
    start_handoffs = [command for command in installed["next_commands"] if "start-here" in command]
    assert start_handoffs and all("--profile minimal" in command for command in start_handoffs), installed["next_commands"]

    copy_contract = repo_harness_install.copy_contract_report(source, profile="minimal")
    exported = repo_harness_install.public_export_report(source, tmp / "export-target", profile="minimal", dry_run=True)
    assert_has_all(copy_contract["next_command"], "install-harness", "--profile minimal")
    assert_has_all(exported["next_command"], "public-export", "--profile minimal")

    wizard_target = tmp / "wizard-target"
    wizard_calls = []

    def wizard_runner(_target_root, args, timeout_seconds):
        wizard_calls.append(list(args))
        return {
            "command": "python -B .agents/manage.py " + " ".join(args),
            "ok": True,
            "status": "passed",
            "returncode": 0,
            "timeout_seconds": timeout_seconds,
            "output_tail": "",
        }

    try:
        wizard = repo_onboarding.install_wizard_report(
            source,
            target=wizard_target,
            profile="minimal",
            setup_check=False,
            install_rg_portable=False,
            bootstrap_local_ai=False,
            download_ai_models=False,
            apply=True,
            command_runner=wizard_runner,
        )
    except TypeError as exc:
        raise AssertionError("wizard apply must expose the converged initialization runner") from exc

    assert_ok(wizard)
    assert wizard_calls == [["setup"], ["setup", "--check"], ["status", "--fast"]]
    assert_field(wizard, "profile", "minimal")
    assert_contains(wizard["next_steps"], "start-here --simple --profile minimal")


def test_project_context_review_flags_missing_draft_facts_without_writing(tmp):
    target = tmp / "consumer"
    write_text(
        doc_path(target, "project", "project-context.md"),
        """---
title: Project Context
type: project-context
status: generated
owner: project-context-generator
audience: agent
updated: 2026-07-04
---

# Project Context

Generated project context for workflow planning.

## Technologies

- No major framework signals detected.

## Security And Configuration Notes

- Secret values are not emitted.

## Freshness

- Last reviewed: not reviewed.
""",
    )
    write_json(doc_path(target, "project", "project-context.json"), {"technologies": []})
    write_json(
        doc_path(target, "project", "validation", "validation-manifest.json"),
        {"commands": []},
    )
    before = sorted(path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file())

    report = repo_onboarding.project_context_review_report(
        target,
        from_request="Build an inventory tracker.",
    )
    after = sorted(path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file())

    assert_ok(report)
    assert_status(report, "review-needed")
    assert_true(report, "review_required")
    assert_field(report, "project_goal", "Build an inventory tracker.")
    assert_has_all(report["missing_facts"], "stack-runtime", "validation-commands", "external-systems", "persistence")
    assert_contains(report["questions"], "external systems")
    assert_contains(report["questions"], "persistence")
    compact = repo_onboarding.summarize_project_context_review_report(report, compact=True)
    assert_field(compact, "draft_like", True)
    assert before == after


def test_project_context_review_emits_structured_facts_for_missing_context(tmp):
    target = tmp / "consumer"

    report = repo_onboarding.project_context_review_report(target)
    facts = {fact["id"]: fact for fact in report["fact_reviews"]}

    assert_ok(report)
    assert_status(report, "missing")
    assert_has_all(facts, "stack-runtime", "validation-commands", "generated-boundaries", "external-systems", "persistence", "ci", "secrets-config")
    assert_field(facts["stack-runtime"], "status", "missing")
    assert_field(facts["stack-runtime"], "blocking", True)
    assert_has_all(facts["validation-commands"], "question", "evidence_paths", "suggested_answer_source")
    assert_contains(report["next_commands"], "python -B .agents/manage.py setup")
    assert_contains(report["next_commands"], "python -B .agents/manage.py project-context-review --target . --write-review")


def test_project_context_review_from_request_adds_goal_alignment_fact(tmp):
    target = tmp / "consumer"
    write_text(
        doc_path(target, "project", "project-context.md"),
        """# Project Context

- Context status: reviewed

## Technologies

- Python 3.12

## Generated Files And Boundaries

- Generated outputs stay under docs/project/diagrams.

## External Systems

- None.

## Persistence

- Local files only.

## CI

- GitHub Actions mirrors local checks.

## Security And Configuration Notes

- Secrets use environment variables.

## Freshness

- Last reviewed: 2026-07-04
""",
    )
    write_json(doc_path(target, "project", "project-context.json"), {"technologies": ["Python 3.12"]})
    write_json(
        doc_path(target, "project", "validation", "validation-manifest.json"),
        {"commands": [{"command": "python -B .agents/manage.py check"}]},
    )

    report = repo_onboarding.project_context_review_report(target, from_request="Add an inventory dashboard.")
    facts = {fact["id"]: fact for fact in report["fact_reviews"]}

    assert_ok(report)
    assert_status(report, "review-needed")
    assert_field(facts["project-goal-alignment"], "status", "review-needed")
    assert_field(facts["project-goal-alignment"], "blocking", True)
    assert_has_all(facts["project-goal-alignment"]["question"], "Add an inventory dashboard.")
    goal_questions = [item for item in report["questions"] if "Add an inventory dashboard." in item]
    assert len(goal_questions) == 1, report["questions"]


def test_project_context_review_write_review_artifact_only_under_review_dir(tmp):
    target = tmp / "consumer"
    write_text(
        doc_path(target, "project", "project-context.md"),
        "# Project Context\n\n- Context status: generated; ready for workflow use with recorded assumptions.\n",
    )
    before = sorted(path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file())

    report = repo_onboarding.project_context_review_report(
        target,
        from_request="Launch a field-service app.",
        write_review=True,
    )
    after = sorted(path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file())
    written = report["review_artifacts"]["written"]
    review_json = doc_path(target, "project", "review", "project-context-review.json")
    review_md = doc_path(target, "project", "review", "project-context-review.md")
    payload = json.loads(review_json.read_text(encoding="utf-8"))

    assert_ok(report)
    assert_has_all(written, "docs/project/review/project-context-review.json", "docs/project/review/project-context-review.md")
    assert_exists(review_json)
    assert_exists(review_md)
    assert_has_all(after, *before)
    assert_has_all(after, "docs/project/review/project-context-review.json", "docs/project/review/project-context-review.md")
    assert_field(payload, "project_goal", "Launch a field-service app.")
    assert_has_all(payload, "fact_reviews", "answer_slots")
    assert_has_all(payload["answer_slots"], "stack-runtime", "project-goal-alignment")


def structured_project_context_review_markdown(answer="Use Python 3.12."):
    return f"""# Project Context Review

{repo_onboarding.PROJECT_CONTEXT_REVIEW_FORMAT_MARKER}

- Canonical context: `docs/project/project-context.md`
- Expected question ids: `stack-runtime`

## Questions To Answer

<!-- BEGIN PROJECT CONTEXT REVIEW QUESTION id="stack-runtime" -->
### stack/runtime

- Question: What runtime should agents assume?
- Evidence paths: `pyproject.toml`

<!-- BEGIN PROJECT CONTEXT REVIEW ANSWER id="stack-runtime" -->
{answer}
<!-- END PROJECT CONTEXT REVIEW ANSWER id="stack-runtime" -->
<!-- END PROJECT CONTEXT REVIEW QUESTION id="stack-runtime" -->
"""


def test_stage_text_file_removes_temp_after_write_failure(tmp):
    destination = tmp / "evidence" / "review.json"
    destination.parent.mkdir(parents=True)
    staged_path = destination.parent / f".{destination.name}.injected.tmp"

    class FailingTemporaryFile:
        name = str(staged_path)

        def __enter__(self):
            staged_path.write_bytes(b"partial review evidence")
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

        def write(self, _content):
            raise OSError("simulated staged write failure")

    with patched_attrs(
        repo_onboarding.tempfile,
        NamedTemporaryFile=lambda *args, **kwargs: FailingTemporaryFile(),
    ):
        try:
            repo_onboarding.stage_text_file(destination, "review evidence")
        except OSError as exc:
            assert str(exc) == "simulated staged write failure"
        else:
            raise AssertionError("expected staged write failure")

    assert_missing(staged_path)


def test_stage_text_file_removes_temp_after_fsync_failure(tmp):
    destination = tmp / "evidence" / "review.json"
    destination.parent.mkdir(parents=True)

    def fail_fsync(_descriptor):
        raise OSError("simulated staged fsync failure")

    with patched_attrs(repo_onboarding.os, fsync=fail_fsync):
        try:
            repo_onboarding.stage_text_file(destination, "review evidence")
        except OSError as exc:
            assert str(exc) == "simulated staged fsync failure"
        else:
            raise AssertionError("expected staged fsync failure")

    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []


def test_stage_text_file_surfaces_cleanup_failure_without_masking_primary(tmp):
    destination = tmp / "evidence" / "review.json"
    destination.parent.mkdir(parents=True)
    staged_path = destination.parent / f".{destination.name}.injected.tmp"
    real_unlink = Path.unlink

    class FailingTemporaryFile:
        name = str(staged_path)

        def __enter__(self):
            staged_path.write_bytes(b"partial review evidence")
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

        def write(self, _content):
            raise OSError("simulated staged write failure")

    def fail_temp_cleanup(path, *args, **kwargs):
        if path == staged_path:
            raise OSError("simulated staged temp cleanup failure")
        return real_unlink(path, *args, **kwargs)

    try:
        with patched_attrs(
            repo_onboarding.tempfile,
            NamedTemporaryFile=lambda *args, **kwargs: FailingTemporaryFile(),
        ), patched_attrs(Path, unlink=fail_temp_cleanup):
            try:
                repo_onboarding.stage_text_file(destination, "review evidence")
            except OSError as exc:
                assert str(exc) == "simulated staged write failure"
                assert_contains(getattr(exc, "__notes__", ()), "simulated staged temp cleanup failure")
            else:
                raise AssertionError("expected staged write failure")
    finally:
        real_unlink(staged_path, missing_ok=True)


def test_project_context_apply_review_dry_run_and_apply_updates_canonical_context(tmp):
    target = tmp / "consumer"
    context_path = doc_path(target, "project", "project-context.md")
    review_path = doc_path(target, "project", "review", "project-context-review.json")
    original_context = """# Project Context

- Context status: generated; review required.

## Technologies

- .NET
"""
    write_text(context_path, original_context)
    write_json(
        review_path,
        {
            "schema_version": 1,
            "tool": "project-context-review-artifact",
            "canonical_context": "docs/project/project-context.md",
            "fact_reviews": [
                {
                    "id": "stack-runtime",
                    "label": "stack/runtime",
                    "question": "What runtime should agents assume?",
                    "evidence_paths": ["global.json", "src/App/App.csproj"],
                },
                {
                    "id": "validation-commands",
                    "label": "run/test commands",
                    "question": "Which validation commands are authoritative?",
                    "evidence_paths": ["docs/project/validation/validation-manifest.json"],
                },
            ],
            "answer_slots": {
                "stack-runtime": "Use .NET SDK 10.0.100 with target framework net10.0.",
                "validation-commands": "Run `dotnet restore App.sln`, then `dotnet build App.sln --no-restore` and `dotnet test App.sln --no-restore` after approved feed credentials are available.",
            },
        },
    )

    dry_run = repo_onboarding.project_context_apply_review_report(target, apply=False)

    assert_ok(dry_run)
    assert_status(dry_run, "planned")
    assert_field(dry_run["summary"], "answer_count", 2)
    assert_has_all(dry_run["planned_section"], "stack/runtime", "dotnet build App.sln --no-restore")
    assert str(target) in dry_run["next_command"], dry_run["next_command"]
    assert context_path.read_text(encoding="utf-8") == original_context, "dry-run must not edit canonical context"

    applied = repo_onboarding.project_context_apply_review_report(target, apply=True)
    updated = context_path.read_text(encoding="utf-8")

    assert_ok(applied)
    assert_status(applied, "applied")
    assert_field(applied["written"], "canonical_context", "docs/project/project-context.md")
    assert str(target) in applied["next_command"], applied["next_command"]
    assert_has_all(updated, "<!-- BEGIN PROJECT CONTEXT REVIEW ANSWERS -->", "Use .NET SDK 10.0.100", "dotnet test App.sln --no-restore")


def test_project_context_apply_review_uses_authoritative_markdown_answers(tmp):
    target = tmp / "consumer"
    context_path = doc_path(target, "project", "project-context.md")
    review_path = doc_path(target, "project", "review", "project-context-review.md")
    normalized_path = doc_path(target, "project", "review", "project-context-review.json")
    write_text(context_path, "# Project Context\n\n- Context status: generated.\n")
    write_text(
        review_path,
        structured_project_context_review_markdown(
            "Use .NET SDK 10.0.100 and target framework net10.0."
        ),
    )

    report = repo_onboarding.project_context_apply_review_report(target, apply=True)
    updated = context_path.read_text(encoding="utf-8")

    assert_ok(report)
    assert_status(report, "applied")
    normalized = read_json(normalized_path)
    assert_has_all(updated, "Use .NET SDK 10.0.100", "stack-runtime")
    assert_field(normalized, "source", "docs/project/review/project-context-review.md")
    assert_field(normalized["answers"][0], "id", "stack-runtime")


def test_project_context_apply_custom_markdown_creates_normalized_evidence_parent_atomically(tmp):
    target = tmp / "consumer"
    context_path = doc_path(target, "project", "project-context.md")
    review_path = target / "notes" / "review.md"
    normalized_path = doc_path(target, "project", "review", "project-context-review.json")
    write_text(context_path, "# Project Context\n\n- Context status: generated.\n")
    write_text(review_path, structured_project_context_review_markdown())
    assert_missing(normalized_path.parent)

    report = repo_onboarding.project_context_apply_review_report(
        target,
        review=Path("notes/review.md"),
        apply=True,
    )

    assert_ok(report)
    assert_status(report, "applied")
    assert_exists(normalized_path)
    assert_has_all(context_path.read_text(encoding="utf-8"), "Use Python 3.12", "stack-runtime")


def test_project_context_apply_output_failure_does_not_modify_canonical_context(tmp):
    target = tmp / "consumer"
    context_path = doc_path(target, "project", "project-context.md")
    review_path = target / "notes" / "review.md"
    normalized_path = doc_path(target, "project", "review", "project-context-review.json")
    original = "# Project Context\n\n- Context status: generated.\n"
    write_text(context_path, original)
    write_text(review_path, structured_project_context_review_markdown())
    real_replace = os.replace

    def fail_normalized_evidence(source, destination):
        if Path(destination).resolve(strict=False) == normalized_path.resolve(strict=False):
            raise OSError("simulated normalized evidence output failure")
        return real_replace(source, destination)

    with patched_attrs(os, replace=fail_normalized_evidence):
        report = repo_onboarding.project_context_apply_review_report(
            target,
            review=Path("notes/review.md"),
            apply=True,
        )

    assert_not_ok(report)
    assert_status(report, "blocked")
    assert_contains(report["issues"], "simulated normalized evidence output failure")
    assert context_path.read_text(encoding="utf-8") == original, report
    assert_missing(normalized_path)


def test_project_context_apply_staging_failure_removes_created_output_parent(tmp):
    target = tmp / "consumer"
    context_path = doc_path(target, "project", "project-context.md")
    review_path = target / "notes" / "review.md"
    normalized_path = doc_path(target, "project", "review", "project-context-review.json")
    original = "# Project Context\n\n- Context status: generated.\n"
    write_text(context_path, original)
    write_text(review_path, structured_project_context_review_markdown())

    def fail_fsync(_descriptor):
        raise OSError("simulated staged fsync failure")

    with patched_attrs(repo_onboarding.os, fsync=fail_fsync):
        report = repo_onboarding.project_context_apply_review_report(
            target,
            review=Path("notes/review.md"),
            apply=True,
        )

    assert_not_ok(report)
    assert_status(report, "blocked")
    assert_field(report["issues"], 0, "simulated staged fsync failure")
    assert context_path.read_text(encoding="utf-8") == original, report
    assert_missing(normalized_path)
    assert_missing(normalized_path.parent)


def test_project_context_apply_canonical_failure_removes_new_evidence_and_parent(tmp):
    target = tmp / "consumer"
    context_path = doc_path(target, "project", "project-context.md")
    review_path = target / "notes" / "review.md"
    normalized_path = doc_path(target, "project", "review", "project-context-review.json")
    original = "# Project Context\n\n- Context status: generated.\n"
    write_text(context_path, original)
    write_text(review_path, structured_project_context_review_markdown())
    real_replace = os.replace

    def fail_canonical_context(source, destination):
        if Path(destination).resolve(strict=False) == context_path.resolve(strict=False):
            raise OSError("simulated canonical context output failure")
        return real_replace(source, destination)

    with patched_attrs(os, replace=fail_canonical_context):
        report = repo_onboarding.project_context_apply_review_report(
            target,
            review=Path("notes/review.md"),
            apply=True,
        )

    assert_not_ok(report)
    assert_status(report, "blocked")
    assert_contains(report["issues"], "simulated canonical context output failure")
    assert context_path.read_text(encoding="utf-8") == original, report
    assert_missing(normalized_path)
    assert_missing(normalized_path.parent)


def test_project_context_apply_canonical_output_failure_rolls_back_normalized_evidence(tmp):
    target = tmp / "consumer"
    context_path = doc_path(target, "project", "project-context.md")
    review_path = target / "notes" / "review.md"
    normalized_path = doc_path(target, "project", "review", "project-context-review.json")
    original = "# Project Context\n\n- Context status: generated.\n"
    original_evidence = '{"preserve": true}\n'
    write_text(context_path, original)
    write_text(review_path, structured_project_context_review_markdown())
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_path.write_text(original_evidence, encoding="utf-8", newline="\n")
    real_replace = os.replace

    def fail_canonical_context(source, destination):
        if Path(destination).resolve(strict=False) == context_path.resolve(strict=False):
            raise OSError("simulated canonical context output failure")
        return real_replace(source, destination)

    with patched_attrs(os, replace=fail_canonical_context):
        report = repo_onboarding.project_context_apply_review_report(
            target,
            review=Path("notes/review.md"),
            apply=True,
        )

    assert_not_ok(report)
    assert_status(report, "blocked")
    assert_contains(report["issues"], "simulated canonical context output failure")
    assert context_path.read_text(encoding="utf-8") == original, report
    assert normalized_path.read_text(encoding="utf-8") == original_evidence, report


def test_project_context_apply_reports_primary_and_rollback_restore_failures(tmp):
    target = tmp / "consumer"
    context_path = doc_path(target, "project", "project-context.md")
    review_path = target / "notes" / "review.md"
    normalized_path = doc_path(target, "project", "review", "project-context-review.json")
    original = "# Project Context\n\n- Context status: generated.\n"
    write_text(context_path, original)
    write_text(review_path, structured_project_context_review_markdown())
    write_text(normalized_path, '{"preserve": true}\n')
    real_replace = os.replace
    normalized_replace_count = 0

    def fail_commit_and_rollback(source, destination):
        nonlocal normalized_replace_count
        resolved = Path(destination).resolve(strict=False)
        if resolved == normalized_path.resolve(strict=False):
            normalized_replace_count += 1
            if normalized_replace_count == 2:
                raise OSError("simulated rollback restore failure")
        if resolved == context_path.resolve(strict=False):
            raise OSError("simulated canonical context output failure")
        return real_replace(source, destination)

    with patched_attrs(os, replace=fail_commit_and_rollback):
        report = repo_onboarding.project_context_apply_review_report(
            target,
            review=Path("notes/review.md"),
            apply=True,
        )

    assert_not_ok(report)
    assert_status(report, "blocked")
    assert_field(report["issues"], 0, "simulated canonical context output failure")
    assert_contains(report["issues"], "simulated rollback restore failure")
    assert_field(report["summary"], "issue_count", 2)
    assert context_path.read_text(encoding="utf-8") == original, report
    assert list(normalized_path.parent.glob(f".{normalized_path.name}.*.tmp")) == [], report


def test_project_context_apply_rejects_normalized_evidence_symlink_escape(tmp):
    target = tmp / "consumer"
    outside = tmp / "outside"
    context_path = doc_path(target, "project", "project-context.md")
    review_path = target / "notes" / "review.md"
    review_dir = doc_path(target, "project", "review")
    original = "# Project Context\n\n- Context status: generated.\n"
    write_text(context_path, original)
    write_text(review_path, structured_project_context_review_markdown())
    outside.mkdir()
    review_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        review_dir.symlink_to(outside, target_is_directory=True)
    except OSError:
        return

    report = repo_onboarding.project_context_apply_review_report(
        target,
        review=Path("notes/review.md"),
        apply=True,
    )

    assert_not_ok(report)
    assert_status(report, "blocked")
    assert_contains(report["issues"], "inside target project")
    assert context_path.read_text(encoding="utf-8") == original, report
    assert_missing(outside / "project-context-review.json")


def test_project_context_generated_markdown_round_trips_without_companion_json(tmp):
    target = tmp / "consumer"
    context_path = doc_path(target, "project", "project-context.md")
    review_path = doc_path(target, "project", "review", "project-context-review.md")
    normalized_path = doc_path(target, "project", "review", "project-context-review.json")
    write_text(context_path, "# Project Context\n\n- Context status: generated.\n")
    review = repo_onboarding.project_context_review_report(
        target,
        from_request="Ship the field-service portal.",
        write_review=True,
    )
    stack_fact = next(fact for fact in review["fact_reviews"] if fact["id"] == "stack-runtime")
    markdown = review_path.read_text(encoding="utf-8").replace(
        "<!-- Replace this comment with the reviewed answer. -->",
        "Reviewed from project evidence.",
    )
    write_text(review_path, markdown)
    normalized_path.unlink()

    report = repo_onboarding.project_context_apply_review_report(target, apply=True)
    normalized = read_json(normalized_path)
    normalized_stack = next(fact for fact in normalized["fact_reviews"] if fact["id"] == "stack-runtime")

    assert_ok(report)
    assert_status(report, "applied")
    assert_field(normalized, "project_goal", "Ship the field-service portal.")
    assert_field(normalized_stack, "question", stack_fact["question"])
    assert_field(normalized_stack, "evidence_paths", stack_fact["evidence_paths"])


def test_project_context_generated_markdown_detects_removed_question_without_json(tmp):
    target = tmp / "consumer"
    context_path = doc_path(target, "project", "project-context.md")
    review_path = doc_path(target, "project", "review", "project-context-review.md")
    normalized_path = doc_path(target, "project", "review", "project-context-review.json")
    original = "# Project Context\n\n- Context status: generated.\n"
    write_text(context_path, original)
    repo_onboarding.project_context_review_report(target, write_review=True)
    markdown = review_path.read_text(encoding="utf-8")
    begin = f'{repo_onboarding.REVIEW_QUESTION_BEGIN_PREFIX}stack-runtime{repo_onboarding.REVIEW_MARKER_SUFFIX}'
    end = f'{repo_onboarding.REVIEW_QUESTION_END_PREFIX}stack-runtime{repo_onboarding.REVIEW_MARKER_SUFFIX}'
    block_start = markdown.index(begin)
    block_end = markdown.index(end, block_start) + len(end)
    markdown = (markdown[:block_start] + markdown[block_end:]).replace(
        "<!-- Replace this comment with the reviewed answer. -->",
        "Reviewed from project evidence.",
    )
    write_text(review_path, markdown)
    normalized_path.unlink()

    report = repo_onboarding.project_context_apply_review_report(target, apply=True)

    assert_not_ok(report)
    assert_status(report, "blocked")
    assert_contains(report["issues"], "missing question id `stack-runtime`")
    assert context_path.read_text(encoding="utf-8") == original, report
    assert_missing(normalized_path)


def test_project_context_generated_markdown_requires_authoritative_expected_ids(tmp):
    target = tmp / "consumer"
    context_path = doc_path(target, "project", "project-context.md")
    review_path = doc_path(target, "project", "review", "project-context-review.md")
    original = "# Project Context\n\n- Context status: generated.\n"
    write_text(context_path, original)
    repo_onboarding.project_context_review_report(target, write_review=True)
    markdown = review_path.read_text(encoding="utf-8")
    begin = f'{repo_onboarding.REVIEW_QUESTION_BEGIN_PREFIX}stack-runtime{repo_onboarding.REVIEW_MARKER_SUFFIX}'
    end = f'{repo_onboarding.REVIEW_QUESTION_END_PREFIX}stack-runtime{repo_onboarding.REVIEW_MARKER_SUFFIX}'
    block_start = markdown.index(begin)
    block_end = markdown.index(end, block_start) + len(end)
    markdown = markdown[:block_start] + markdown[block_end:]
    markdown = "\n".join(
        line for line in markdown.splitlines() if not line.startswith("- Expected question ids:")
    ).replace(
        "<!-- Replace this comment with the reviewed answer. -->",
        "Reviewed from project evidence.",
    )
    write_text(review_path, markdown)

    report = repo_onboarding.project_context_apply_review_report(target, apply=True)

    assert_not_ok(report)
    assert_status(report, "blocked")
    assert_contains(report["issues"], "expected question ids")
    assert context_path.read_text(encoding="utf-8") == original, report


def test_project_context_current_markdown_without_question_markers_never_uses_stale_json(tmp):
    target = tmp / "consumer"
    context_path = doc_path(target, "project", "project-context.md")
    review_path = doc_path(target, "project", "review", "project-context-review.md")
    companion_json_path = doc_path(target, "project", "review", "project-context-review.json")
    original = "# Project Context\n\n- Context status: generated.\n"
    write_text(context_path, original)
    repo_onboarding.project_context_review_report(target, write_review=True)
    markdown = review_path.read_text(encoding="utf-8")
    write_text(review_path, markdown.split("## Questions To Answer", 1)[0])
    stale_payload = read_json(companion_json_path)
    stale_payload["answer_slots"] = {
        fact_id: "Stale answer from companion JSON."
        for fact_id in stale_payload.get("answer_slots", {})
    }
    write_json(companion_json_path, stale_payload)

    report = repo_onboarding.project_context_apply_review_report(target, apply=True)

    assert_not_ok(report)
    assert_status(report, "blocked")
    assert_contains(report["issues"], "missing question id")
    assert context_path.read_text(encoding="utf-8") == original, report
    assert_lacks(context_path.read_text(encoding="utf-8"), "Stale answer")


def test_project_context_prediscriminator_structured_markdown_never_uses_stale_json(tmp):
    target = tmp / "consumer"
    context_path = doc_path(target, "project", "project-context.md")
    review_path = doc_path(target, "project", "review", "project-context-review.md")
    companion_json_path = doc_path(target, "project", "review", "project-context-review.json")
    original = "# Project Context\n\n- Context status: generated.\n"
    write_text(context_path, original)
    repo_onboarding.project_context_review_report(target, write_review=True)
    markdown = review_path.read_text(encoding="utf-8")
    markdown = markdown.replace(repo_onboarding.PROJECT_CONTEXT_REVIEW_FORMAT_MARKER, "")
    write_text(review_path, markdown.split("## Questions To Answer", 1)[0])
    stale_payload = read_json(companion_json_path)
    stale_payload["answer_slots"] = {
        fact_id: "Stale pre-discriminator companion answer."
        for fact_id in stale_payload.get("answer_slots", {})
    }
    write_json(companion_json_path, stale_payload)

    report = repo_onboarding.project_context_apply_review_report(target, apply=True)

    assert_not_ok(report)
    assert_status(report, "blocked")
    assert_field(report["paths"], "review", "docs/project/review/project-context-review.md")
    assert_contains(report["issues"], "missing question id")
    assert context_path.read_text(encoding="utf-8") == original, report


def test_project_context_damaged_structured_end_markers_never_use_stale_json(tmp):
    target = tmp / "consumer"
    context_path = doc_path(target, "project", "project-context.md")
    review_path = doc_path(target, "project", "review", "project-context-review.md")
    companion_json_path = doc_path(target, "project", "review", "project-context-review.json")
    original = "# Project Context\n\n- Context status: generated.\n"
    write_text(context_path, original)
    repo_onboarding.project_context_review_report(target, write_review=True)
    damaged_lines = [
        line
        for line in review_path.read_text(encoding="utf-8").splitlines()
        if repo_onboarding.PROJECT_CONTEXT_REVIEW_FORMAT_MARKER not in line
        and repo_onboarding.REVIEW_QUESTION_BEGIN_PREFIX not in line
    ]
    write_text(review_path, "\n".join(damaged_lines))
    stale_payload = read_json(companion_json_path)
    stale_payload["answer_slots"] = {
        fact_id: "Stale damaged-artifact companion answer."
        for fact_id in stale_payload.get("answer_slots", {})
    }
    write_json(companion_json_path, stale_payload)

    report = repo_onboarding.project_context_apply_review_report(target, apply=True)

    assert_not_ok(report)
    assert_status(report, "blocked")
    assert_field(report["paths"], "review", "docs/project/review/project-context-review.md")
    assert_contains(report["issues"], "answer")
    assert context_path.read_text(encoding="utf-8") == original, report


def test_project_context_markdown_metadata_bullets_inside_answers_remain_answer_text(tmp):
    target = tmp / "consumer"
    context_path = doc_path(target, "project", "project-context.md")
    review_path = doc_path(target, "project", "review", "project-context-review.md")
    normalized_path = doc_path(target, "project", "review", "project-context-review.json")
    write_text(context_path, "# Project Context\n\n- Context status: generated.\n")
    repo_onboarding.project_context_review_report(
        target,
        from_request="Ship the field-service portal.",
        write_review=True,
    )
    answer = """Reviewed answer text:
- Expected question ids: `made-up-fact`
- Project goal: this is answer text
- Canonical context: `answer-only/path.md`"""
    markdown = review_path.read_text(encoding="utf-8").replace(
        "<!-- Replace this comment with the reviewed answer. -->",
        answer,
    )
    write_text(review_path, markdown)

    report = repo_onboarding.project_context_apply_review_report(target, apply=True)
    normalized = read_json(normalized_path)

    assert_ok(report)
    assert_status(report, "applied")
    assert_field(normalized, "project_goal", "Ship the field-service portal.")
    assert_field(normalized, "canonical_context", "docs/project/project-context.md")
    assert_has_all(context_path.read_text(encoding="utf-8"), "made-up-fact", "this is answer text", "answer-only/path.md")


def test_project_context_apply_review_cli_defaults_to_markdown(_tmp):
    args = repo_cli_parser.build_parser().parse_args(["project-context-apply-review", "--target", "."])

    assert args.review == "docs/project/review/project-context-review.md", vars(args)


def test_project_context_apply_review_rejects_invalid_markdown_ids_without_writes(tmp):
    valid_block = """<!-- BEGIN PROJECT CONTEXT REVIEW QUESTION id="stack-runtime" -->
### stack/runtime
- Question: What runtime should agents assume?
<!-- BEGIN PROJECT CONTEXT REVIEW ANSWER id="stack-runtime" -->
Use Python 3.12.
<!-- END PROJECT CONTEXT REVIEW ANSWER id="stack-runtime" -->
<!-- END PROJECT CONTEXT REVIEW QUESTION id="stack-runtime" -->
"""
    cases = {
        "duplicate": (valid_block + "\n" + valid_block, "stack-runtime"),
        "missing": ("""<!-- BEGIN PROJECT CONTEXT REVIEW QUESTION id="stack-runtime" -->
### stack/runtime
- Question: What runtime should agents assume?
<!-- END PROJECT CONTEXT REVIEW QUESTION id="stack-runtime" -->
""", "stack-runtime"),
        "unknown": ("""<!-- BEGIN PROJECT CONTEXT REVIEW QUESTION id="made-up-fact" -->
### made-up fact
- Question: Is this recognized?
<!-- BEGIN PROJECT CONTEXT REVIEW ANSWER id="made-up-fact" -->
No.
<!-- END PROJECT CONTEXT REVIEW ANSWER id="made-up-fact" -->
<!-- END PROJECT CONTEXT REVIEW QUESTION id="made-up-fact" -->
""", "made-up-fact"),
    }

    for name, (body, expected_id) in cases.items():
        target = tmp / name
        context_path = doc_path(target, "project", "project-context.md")
        review_path = doc_path(target, "project", "review", "project-context-review.md")
        normalized_path = doc_path(target, "project", "review", "project-context-review.json")
        original = "# Project Context\n\n- Context status: generated.\n"
        write_text(context_path, original)
        write_text(
            review_path,
            "# Project Context Review\n\n"
            + repo_onboarding.PROJECT_CONTEXT_REVIEW_FORMAT_MARKER
            + f"\n\n- Expected question ids: `{expected_id}`\n\n"
            + body,
        )

        report = repo_onboarding.project_context_apply_review_report(target, apply=True)

        assert_not_ok(report)
        assert_status(report, "blocked")
        assert_contains(report["issues"], name if name != "missing" else "missing answer id")
        assert context_path.read_text(encoding="utf-8") == original, report
        assert_missing(normalized_path)


def test_project_context_apply_review_escapes_marker_text_in_answers(tmp):
    target = tmp / "consumer"
    context_path = doc_path(target, "project", "project-context.md")
    review_path = doc_path(target, "project", "review", "project-context-review.json")
    write_text(context_path, "# Project Context\n\n- Context status: generated.\n")
    write_json(
        review_path,
        {
            "schema_version": 1,
            "tool": "project-context-review-artifact",
            "project_goal": "Keep markers safe.",
            "fact_reviews": [
                {
                    "id": "generated-boundaries",
                    "label": "generated-file boundaries",
                    "question": "Which generated boundaries apply?",
                    "evidence_paths": ["docs/project/project-context.md"],
                },
            ],
            "answer_slots": {
                "generated-boundaries": "Never let <!-- END PROJECT CONTEXT REVIEW ANSWERS --> inside an answer close the managed section.",
            },
        },
    )

    first = repo_onboarding.project_context_apply_review_report(target, apply=True)
    second = repo_onboarding.project_context_apply_review_report(target, apply=True)
    updated = context_path.read_text(encoding="utf-8")

    assert_ok(first)
    assert_ok(second)
    assert updated.count("<!-- BEGIN PROJECT CONTEXT REVIEW ANSWERS -->") == 1, updated
    assert updated.count("<!-- END PROJECT CONTEXT REVIEW ANSWERS -->") == 1, updated
    assert "&lt;!-- END PROJECT CONTEXT REVIEW ANSWERS --&gt;" in updated, updated


def test_project_context_review_write_review_refuses_missing_context_without_creating_target(tmp):
    target = tmp / "typo target"

    report = repo_onboarding.project_context_review_report(target, write_review=True)

    assert_not_ok(report)
    assert_status(report, "blocked")
    assert_contains(report["issues"], "Run `python -B .agents/manage.py setup`")
    assert_field(report["review_artifacts"], "written", [])
    assert_missing(target)


def test_project_context_review_ready_context_with_evidence_has_present_facts(tmp):
    target = tmp / "consumer"
    write_text(
        doc_path(target, "project", "project-context.md"),
        """# Project Context

- Context status: reviewed

## Technologies

- Python 3.12

## Generated Files And Boundaries

- Generated outputs stay under docs/project/diagrams.

## External Systems

- Azure DevOps work items are referenced by URL.

## Persistence

- SQLite stores local development data.

## CI

- GitHub Actions runs local validation.

## Security And Configuration Notes

- Secrets use environment variables.

## Freshness

- Last reviewed: 2026-07-04
""",
    )
    write_json(doc_path(target, "project", "project-context.json"), {"technologies": ["Python 3.12"]})
    write_json(
        doc_path(target, "project", "validation", "validation-manifest.json"),
        {"commands": [{"command": "python -B .agents/manage.py check"}]},
    )

    report = repo_onboarding.project_context_review_report(target)

    assert_ok(report)
    assert_status(report, "ready")
    assert_empty(report["missing_facts"])
    assert all(fact["status"] == "present" for fact in report["fact_reviews"]), report["fact_reviews"]
    assert_field(report["summary"], "blocking_fact_count", 0)


def test_project_context_review_accepts_reviewed_project_context_section_names(tmp):
    target = tmp / "consumer"
    write_text(
        doc_path(target, "project", "project-context.md"),
        """---
status: reviewed
---

# Project Context

- Context status: reviewed

## Technology Stack

- Runtime and SDK versions: Python 3.12+ standard library.
- Package managers and lockfiles: none required.

## Local Run Commands

| Action | Command |
|---|---|
| setup check | `python -B .agents/manage.py setup --check` |

## Validation Commands

| Check | Command |
|---|---|
| repo check | `python -B .agents/manage.py check` |

## Data And Persistence

- Database engine: none.
- Known persistent data stores and ownership boundaries: Git-tracked files.

## External Systems And Credentials

- Optional external services: GitHub remote, Azure DevOps, and SonarQube when explicitly approved.
- Credential names or environment variables, without values: Azure DevOps PAT and SonarQube token.

## Generated Files And Do Not Edit

- Generated folders/files: `.agents/routing.md`, `.agents/registry.json`, and adapter surfaces.

## Agent Workflow Notes

- Preferred validation order: `check-additions`, `sync --check`, and `check`.

## Freshness

- Last reviewed: 2026-07-04
""",
    )
    write_json(doc_path(target, "project", "project-context.json"), {})
    write_json(doc_path(target, "project", "validation", "validation-manifest.json"), {"commands": []})

    report = repo_onboarding.project_context_review_report(target)
    facts = {fact["id"]: fact for fact in report["fact_reviews"]}

    assert_ok(report)
    assert_status(report, "ready")
    assert_empty(report["missing_facts"])
    assert_field(facts["stack-runtime"], "status", "present")
    assert_field(facts["validation-commands"], "status", "present")
    assert_field(facts["generated-boundaries"], "status", "present")


def test_project_context_review_uses_dotnet_context_and_sharpens_private_feed_questions(tmp):
    target = tmp / "consumer"
    write_text(
        doc_path(target, "project", "project-context.md"),
        """# Project Context

- Context status: reviewed

## Technologies

- .NET

## .NET Context

- SDK/runtime: .NET SDK 10.0.100, target framework net10.0.
- NuGet/feed policy: Private/internal NuGet feeds detected.

## Generated Files And Boundaries

- `bin/` and `obj/` are generated.

## Freshness

- Last reviewed: 2026-07-04
""",
    )
    write_json(
        doc_path(target, "project", "project-context.json"),
        {
            "technologies": [".NET"],
            "dotnet_context": {
                "status": "partial",
                "dotnet_cli": {"available": False},
                "projects": [{"path": "src/App/App.csproj", "target_frameworks": ["net10.0"], "classification": "web"}],
                "nuget": {
                    "config_paths": ["NuGet.config"],
                    "private_feeds_detected": True,
                    "package_source_mapping_present": True,
                    "global_config_skipped": True,
                },
                "validation_candidates": [
                    {"id": "dotnet-restore-solution", "kind": "restore", "command": "dotnet restore App.sln"},
                    {"id": "dotnet-build-no-restore", "kind": "build", "command": "dotnet build App.sln --no-restore"},
                ],
                "context_facts": [
                    {"id": "stack-runtime", "status": "present"},
                    {"id": "validation-commands", "status": "present"},
                    {"id": "external-systems", "status": "review-needed"},
                    {"id": "secrets-config", "status": "review-needed"},
                    {"id": "persistence", "status": "present"},
                    {"id": "ci", "status": "present"},
                ],
                "persistence": {
                    "db_contexts": [{"path": "src/App/AppDbContext.cs", "class_names": ["AppDbContext"]}],
                    "provider_packages": ["Microsoft.EntityFrameworkCore.SqlServer"],
                },
                "ci": {
                    "workflow_paths": [".github/workflows/build.yml"],
                    "dotnet_commands": [{"command": "dotnet test App.sln --no-restore", "path": ".github/workflows/build.yml"}],
                },
                "configuration": {
                    "appsettings_files": [{"path": "src/App/appsettings.json", "connection_string_names": ["DefaultConnection"]}],
                    "user_secrets_ids": [{"project": "src/App/App.csproj", "id": "app-dev"}],
                },
            },
        },
    )
    write_json(doc_path(target, "project", "validation", "validation-manifest.json"), {"commands": []})

    report = repo_onboarding.project_context_review_report(target)
    facts = {fact["id"]: fact for fact in report["fact_reviews"]}

    assert_ok(report)
    assert_status(report, "review-needed")
    assert_field(report["dotnet_context"], "status", "partial")
    assert_field(facts["stack-runtime"], "status", "present")
    assert_field(facts["validation-commands"], "status", "present")
    assert_field(facts["persistence"], "status", "present")
    assert_field(facts["ci"], "status", "present")
    assert_field(facts["secrets-config"], "status", "present")
    assert_field(facts["dotnet-nuget-feed-policy"], "status", "review-needed")
    assert_field(facts["dotnet-nuget-feed-policy"], "blocking", True)
    assert_has_all(facts["dotnet-nuget-feed-policy"]["question"], "private/internal NuGet feed", "restore prerequisites")
    assert_has_all(facts["dotnet-nuget-feed-policy"]["evidence_paths"], "NuGet.config", "docs/project/project-context.json")
    assert_contains(report["questions"], "private/internal NuGet feed")


def test_project_kickoff_plans_new_target_without_writes(tmp):
    source, target = harness_fixture(tmp)

    report = repo_onboarding.project_kickoff_report(
        source,
        target=target,
        apply=False,
        from_request="Start a customer portal project.",
    )
    rendered = repo_onboarding.render_project_kickoff(report)

    assert_ok(report)
    assert_tool(report, "project-kickoff")
    assert_status(report, "planned")
    assert_field(report["target_state"], "status", "missing")
    assert_status(report["install"], "planned")
    assert_status(report["context_review"], "missing")
    assert_has_all(report["next_command"], "project-kickoff", "--apply")
    assert_has_all(rendered, "## Copyable Chat Prompts", "review generated project context")
    assert_missing(target)


def test_project_kickoff_quotes_source_target_paths_with_spaces(tmp):
    source, _target = harness_fixture(tmp)
    target = tmp / "consumer project with spaces"

    report = repo_onboarding.project_kickoff_report(source, target=target, apply=False)
    source_group = next(group for group in report["command_groups"] if group["id"] == "source-harness")

    assert_ok(report)
    assert_has_all(report["next_command"], '--target "', str(target))
    assert_has_all(source_group["commands"][0], '--target "', str(target))


def test_project_kickoff_guides_installed_missing_context_to_target_setup_and_review(tmp):
    source, target = harness_fixture(tmp)
    installed = repo_harness_install.install_harness_report(source, target, dry_run=False)
    assert_ok(installed)

    report = repo_onboarding.project_kickoff_report(source, target=target, apply=False)

    assert_ok(report)
    assert_field(report["primary_next_action"], "id", "run-setup")
    assert_field(report["primary_next_action"], "run_from", "target-project")
    assert_has_all(report["primary_next_action"]["command"], "setup")
    target_group = next(group for group in report["command_groups"] if group["id"] == "target-project")
    assert_contains(target_group["commands"], "python -B .agents/manage.py project-context-review --target . --write-review")


def test_project_kickoff_guides_draft_context_to_review_artifact(tmp):
    source, target = harness_fixture(tmp)
    installed = repo_harness_install.install_harness_report(source, target, dry_run=False)
    assert_ok(installed)
    write_text(
        doc_path(target, "project", "project-context.md"),
        "# Project Context\n\n- Context status: generated; ready for workflow use with recorded assumptions.\n",
    )

    report = repo_onboarding.project_kickoff_report(source, target=target, apply=False)

    assert_ok(report)
    assert_field(report["primary_next_action"], "id", "write-context-review")
    assert_has_all(report["primary_next_action"]["command"], "project-context-review", "--write-review")


def test_project_kickoff_surfaces_dotnet_context_status_and_private_feed_review(tmp):
    source, target = harness_fixture(tmp)
    installed = repo_harness_install.install_harness_report(source, target, dry_run=False)
    assert_ok(installed)
    write_text(
        doc_path(target, "project", "project-context.md"),
        """# Project Context

- Context status: reviewed

## Technologies

- .NET

## .NET Context

- Private/internal NuGet feeds detected.

## Generated Files And Boundaries

- `bin/` and `obj/` are generated.

## Freshness

- Last reviewed: 2026-07-04
""",
    )
    write_json(
        doc_path(target, "project", "project-context.json"),
        {
            "technologies": [".NET"],
            "dotnet_context": {
                "status": "partial",
                "dotnet_cli": {"available": False},
                "projects": [{"path": "src/App/App.csproj", "target_frameworks": ["net10.0"], "classification": "web"}],
                "nuget": {"config_paths": ["NuGet.config"], "private_feeds_detected": True, "global_config_skipped": True},
                "validation_candidates": [{"id": "dotnet-restore-solution", "kind": "restore", "command": "dotnet restore App.sln"}],
                "context_facts": [{"id": "stack-runtime", "status": "present"}, {"id": "validation-commands", "status": "present"}],
            },
        },
    )
    write_json(doc_path(target, "project", "validation", "validation-manifest.json"), {"commands": []})

    report = repo_onboarding.project_kickoff_report(source, target=target, apply=False)
    rendered = repo_onboarding.render_project_kickoff(report)

    assert_ok(report)
    assert_field(report["dotnet_context"], "status", "partial")
    assert_field(report["primary_next_action"], "id", "write-context-review")
    assert_has_all(rendered, ".NET context: partial", "private/internal NuGet feed")


def test_project_kickoff_ready_context_without_request_recommends_workflow_without_starting_run(tmp):
    source, target = harness_fixture(tmp)
    installed = repo_harness_install.install_harness_report(source, target, dry_run=False)
    assert_ok(installed)
    write_text(
        doc_path(target, "project", "project-context.md"),
        """# Project Context

- Context status: reviewed

## Technologies

- Python 3.12

## Generated Files And Boundaries

- Generated outputs stay under docs/project/diagrams.

## External Systems

- None.

## Persistence

- Local files only.

## CI

- GitHub Actions mirrors local checks.

## Security And Configuration Notes

- Secrets use environment variables.

## Freshness

- Last reviewed: 2026-07-04
""",
    )
    write_json(doc_path(target, "project", "project-context.json"), {"technologies": ["Python 3.12"]})
    write_json(
        doc_path(target, "project", "validation", "validation-manifest.json"),
        {"commands": [{"command": "python -B .agents/manage.py check"}]},
    )

    report = repo_onboarding.project_kickoff_report(source, target=target, apply=False)

    assert_ok(report)
    assert_field(report["primary_next_action"], "id", "start-workflow")
    assert_field(report["primary_next_action"], "run_from", "target-project")
    assert_has_all(report["primary_next_action"]["command"], "workflow start --from-request")
    assert_has_all(report["workflow_recommendations"][0]["command"], "workflow start --from-request")
    assert not any((target / "automations").glob("*/runs/*"))


def test_project_kickoff_ready_context_with_request_requires_goal_alignment_review(tmp):
    source, target = harness_fixture(tmp)
    installed = repo_harness_install.install_harness_report(source, target, dry_run=False)
    assert_ok(installed)
    write_text(
        doc_path(target, "project", "project-context.md"),
        """# Project Context

- Context status: reviewed

## Technologies

- Python 3.12

## Generated Files And Boundaries

- Generated outputs stay under docs/project/diagrams.

## External Systems

- None.

## Persistence

- Local files only.

## CI

- GitHub Actions mirrors local checks.

## Security And Configuration Notes

- Secrets use environment variables.

## Freshness

- Last reviewed: 2026-07-04
""",
    )
    write_json(doc_path(target, "project", "project-context.json"), {"technologies": ["Python 3.12"]})
    write_json(
        doc_path(target, "project", "validation", "validation-manifest.json"),
        {"commands": [{"command": "python -B .agents/manage.py check"}]},
    )

    report = repo_onboarding.project_kickoff_report(
        source,
        target=target,
        apply=False,
        from_request="Implement the first inventory story.",
    )
    facts = {fact["id"]: fact for fact in report["context_review"]["fact_reviews"]}

    assert_ok(report)
    assert_status(report["context_review"], "review-needed")
    assert_field(facts["project-goal-alignment"], "blocking", True)
    assert_field(report["primary_next_action"], "id", "write-context-review")
    assert_has_all(report["primary_next_action"]["command"], "project-context-review", "--write-review", "Implement the first inventory story.")
    assert_has_all(report["workflow_recommendations"][0]["command"], "workflow start --from-request", "Implement the first inventory story.")
    assert not any((target / "automations").glob("*/runs/*"))


def test_project_kickoff_existing_installed_consumer_reports_context_review(tmp):
    source, target = harness_fixture(tmp)
    installed = repo_harness_install.install_harness_report(source, target, dry_run=False)
    assert_ok(installed)
    write_text(
        doc_path(target, "project", "project-context.md"),
        "# Project Context\n\nContext status: generated; ready for workflow use with recorded assumptions.\n\n## Technologies\n\n- Python\n",
    )

    report = repo_onboarding.project_kickoff_report(source, target=target, apply=False)

    assert_ok(report)
    assert_field(report["target_state"], "status", "installed-consumer")
    assert_status(report["install"], "planned")
    assert_status(report["context_review"], "review-needed")


def test_project_kickoff_reports_missing_tool_advisories(tmp):
    source, target = harness_fixture(tmp)

    def fake_ripgrep_report(_args, _root):
        return {
            "ok": True,
            "status": "missing",
            "required": False,
            "suggested": "Install pinned portable ripgrep with setup --install-rg-portable.",
        }

    with patched_attrs(repo_onboarding.repo_setup, ripgrep_tool_report=fake_ripgrep_report):
        report = repo_onboarding.project_kickoff_report(source, target=target, apply=False)

    assert_ok(report)
    assert_field(report["tool_advisories"]["ripgrep"], "status", "missing")
    assert_contains(report["tool_advisories"]["advisories"], "setup --install-rg-portable")


def test_project_kickoff_apply_uses_install_setup_check_status_sequence(tmp):
    source, target = harness_fixture(tmp)
    commands = []

    def fake_runner(_target_root, args, timeout_seconds):
        commands.append(list(args))
        return {
            "command": "python -B .agents/manage.py " + " ".join(args),
            "ok": True,
            "status": "passed",
            "returncode": 0,
            "timeout_seconds": timeout_seconds,
            "output_tail": "",
        }

    report = repo_onboarding.project_kickoff_report(
        source,
        target=target,
        apply=True,
        command_runner=fake_runner,
    )

    assert_ok(report)
    assert_status(report, "applied")
    assert_field(report["install"], "status", "installed")
    assert commands == [["setup"], ["setup", "--check"], ["status", "--fast"]]
    assert_lacks_all(" ".join(" ".join(command) for command in commands), "workflow", "resume")
    assert_exists(agent_path(target, "harness.lock.json"))


def test_harness_promote_classifies_manifest_changes(tmp):
    source, target = harness_fixture(tmp)
    installed = repo_harness_install.install_harness_report(source, target, dry_run=False)
    assert_ok(installed)
    write_text(doc_path(target, "agent-start.md"), "# Consumer context improvement\n")
    write_text(doc_path(source, "start-here.md"), "# Source onboarding improvement\n")
    write_text(source / "AGENTS.md", "# Source policy update\n")
    write_text(target / "AGENTS.md", "# Consumer policy update\n")

    report = repo_harness_promote.harness_promote_report(source, target, dry_run=True)
    by_path = {row["path"]: row for row in report["files"]}

    assert_ok(report)
    assert_status(report, "planned")
    assert_field(by_path["docs/agent-start.md"], "classification", "consumer-changed-only")
    assert_field(by_path["docs/start-here.md"], "classification", "source-changed-only")
    assert_field(by_path["AGENTS.md"], "classification", "both-changed-diverged")
    assert_summary(report, promotable_files=1, diverged_files=1)


def test_harness_promote_refuses_excluded_paths_and_requires_explicit_apply_paths(tmp):
    source, target = harness_fixture(tmp)
    installed = repo_harness_install.install_harness_report(source, target, dry_run=False)
    assert_ok(installed)
    write_text(doc_path(target, "project", "project-context.md"), "# Consumer Project Context\n")
    write_text(agent_path(target, "local-ai", "secrets.local.json"), "{}\n")

    missing_paths = repo_harness_promote.harness_promote_report(source, target, apply=True)
    excluded = repo_harness_promote.harness_promote_report(
        source,
        target,
        apply=True,
        paths=["docs/project/project-context.md", ".agents/local-ai/secrets.local.json"],
    )

    assert_not_ok(missing_paths)
    assert_contains(missing_paths["issues"], "requires --paths")
    assert_not_ok(excluded)
    assert_contains(excluded["issues"], "project-local context")
    assert_contains(excluded["issues"], "ignored local state")
    assert_empty(excluded["copied"])


def test_harness_promote_apply_copies_selected_consumer_file_and_reports_validation(tmp):
    source, target = harness_fixture(tmp)
    installed = repo_harness_install.install_harness_report(source, target, dry_run=False)
    assert_ok(installed)
    write_text(doc_path(target, "agent-start.md"), "# Consumer Agent Start\n")

    report = repo_harness_promote.harness_promote_report(
        source,
        target,
        apply=True,
        paths=["docs/agent-start.md"],
    )

    assert_ok(report)
    assert_status(report, "applied")
    assert_field(report, "copied", ["docs/agent-start.md"])
    assert (doc_path(source, "agent-start.md")).read_text(encoding="utf-8") == "# Consumer Agent Start\n"
    assert_has_all(
        report["validation_commands"],
        "python -B .agents/manage.py check-additions",
        "python -B .agents/manage.py sync --check",
        "python -B .agents/manage.py check-changed --summary --compact --format json",
        "python -B .agents/manage.py check",
    )


def test_install_harness_refuses_collisions_before_copying(tmp):
    source = tmp / "source"
    target = tmp / "target"
    write_harness_install_fixture(source)
    write_text(source / "AGENTS.md", "source\n")
    write_text(agent_path(source, "manage.py"), "print('source')\n")
    write_text(target / "AGENTS.md", "target\n")

    report = repo_harness_install.install_harness_report(source, target, dry_run=False, force=False)

    assert_not_ok(report)
    assert_fields(
        report,
        status="blocked",
        collisions=[{"path": "AGENTS.md", "reason": "target file differs from source"}],
        next_commands=[],
    )
    assert (target / "AGENTS.md").read_text(encoding="utf-8").strip() == "target"
    assert_missing(agent_path(target, "manage.py"))

    forced = repo_harness_install.install_harness_report(source, target, dry_run=False, force=True)
    assert_ok(forced)
    assert_status(forced, "installed")
    assert (target / "AGENTS.md").read_text(encoding="utf-8").strip() == "source"
    assert_exists(agent_path(target, "manage.py"))


def test_install_harness_updates_clean_manifest_files_without_git(tmp):
    source, target = harness_fixture(tmp)

    first = repo_harness_install.install_harness_report(source, target, dry_run=False)
    assert_ok(first)
    assert_exists(agent_path(target, "harness.lock.json"))

    write_text(source / "README.md", f"{SKILLS_HARNESS_MD}\nUpdated source.\n")
    second = repo_harness_install.install_harness_report(source, target, dry_run=False)
    assert_ok(second)
    assert_field(second, "operation", "update")
    assert (target / "README.md").read_text(encoding="utf-8").endswith("Updated source.\n")

    write_text(target / "README.md", "# Consumer edit\n")
    write_text(source / "README.md", f"{SKILLS_HARNESS_MD}\nSecond update.\n")
    blocked = repo_harness_install.install_harness_report(source, target, dry_run=False)
    assert_not_ok(blocked)
    assert_field(blocked, "collisions", [{"path": "README.md", "reason": HARNESS_EDITED_AFTER_INSTALL}])


def test_addition_acceptance_allows_installed_harness_manifest_files(tmp):
    write_json(
        agent_path(tmp, "harness.lock.json"),
        {
            "schema_version": 1,
            "files": [
                {"path": ".agents/local-ai.json", "sha256": "abc"},
                {"path": ".agents/manage.py", "sha256": "def"},
                {"path": ".editorconfig", "sha256": "ghi"},
                {"path": ".github/workflows.disabled/validate-skills.yml", "sha256": "jkl"},
            ],
        },
    )
    paths = [
        ".agents/local-ai.json",
        ".agents/manage.py",
        ".editorconfig",
        ".github/workflows.disabled/validate-skills.yml",
    ]

    report = repo_changed.addition_acceptance_report(tmp, paths=paths, new_paths=paths)

    assert_ok(report)
    assert_empty(report["issues"])


def test_install_harness_update_dogfood_preserves_no_git_consumer_fixture(tmp):
    source = harness_source(tmp)
    target = tmp / "consumer"
    write_text(doc_path(source, "project", "project-context.md"), "# Project Context\n\nContext status: draft\n")
    fixture = Path(__file__).resolve().parents[1] / "assets" / "fixtures" / "sample-consumer"

    first = repo_harness_install.install_harness_report(source, target, dry_run=False)
    assert_ok(first)
    for relative in ("Directory.Packages.props", "NuGet.config", "src"):
        source_item = fixture / relative
        target_item = target / relative
        if source_item.is_dir():
            copy_tree(source_item, target_item)
        else:
            target_item.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_item, target_item)
    assert_missing(target / ".git")
    assert_exists(target / "src" / "SampleConsumer" / "Program.cs")

    write_text(source / "README.md", f"{SKILLS_HARNESS_MD}\nClean source update.\n")
    write_text(doc_path(source, "project", "project-context.md"), "# Project Context\n\nUpdated source draft.")
    write_text(doc_path(target, "project", "project-context.md"), "# Project Context\n\nConsumer reviewed context.")
    dry_run = repo_harness_install.install_harness_report(source, target, dry_run=True)

    assert_ok(dry_run)
    assert_status(dry_run, "planned")
    planned = {row["path"]: row["reason"] for row in dry_run["planned"] if isinstance(row, dict)}
    assert_field(planned, "README.md", "update-clean-installed-file")
    assert_field(dry_run, "collisions", [])
    assert "docs/project/project-context.md" in dry_run["excluded"]
    assert (target / "README.md").read_text(encoding="utf-8") == SKILLS_HARNESS_MD
    assert_exists(target / "src" / "SampleConsumer" / "Program.cs")


def test_install_harness_can_run_post_install_commands(tmp):
    source, target = harness_fixture(tmp)
    calls = []

    def fake_runner(root, args, timeout):
        calls.append((root, list(args), timeout))
        return captured_manage_result(args)

    report = repo_harness_install.install_harness_report(
        source,
        target,
        dry_run=False,
        run_setup_check=True,
        bootstrap_local_ai=True,
        command_runner=fake_runner,
    )

    assert_ok(report)
    assert [args for _root, args, _timeout in calls] == [
        ["setup", "--no-link-skills"],
        ["setup", "--check", "--no-link-skills"],
        ["local-ai", "write-config"],
        ["local-ai", "policy", "--write-default", "--json", "--summary", "--compact"],
    ]


def test_install_harness_update_runs_setup_check_without_reinitializing_project(tmp):
    source, target = harness_fixture(tmp)
    first = repo_harness_install.install_harness_report(source, target, dry_run=False)
    assert_ok(first)
    calls = []

    def fake_runner(_root, args, _timeout):
        calls.append(list(args))
        return captured_manage_result(args)

    report = repo_harness_install.install_harness_report(
        source,
        target,
        dry_run=False,
        run_setup_check=True,
        command_runner=fake_runner,
    )

    assert_ok(report)
    assert calls == [["setup", "--check", "--no-link-skills"]]


def test_install_harness_can_prepare_portable_ripgrep(tmp):
    source, target = harness_fixture(tmp)
    calls = []

    def fake_runner(_root, args, _timeout):
        calls.append(list(args))
        return captured_manage_result(args)

    report = repo_harness_install.install_harness_report(
        source,
        target,
        dry_run=False,
        run_setup_check=True,
        install_rg_portable=True,
        command_runner=fake_runner,
    )

    assert_ok(report)
    assert calls == [
        ["setup", "--install-rg-portable", "--no-link-skills"],
        ["setup", "--check", "--no-link-skills"],
    ]


def test_install_harness_smoke_exercises_prepared_install_and_resume(tmp):
    source = harness_source(tmp)
    work_dir = tmp / "work"
    post_install_calls = []
    subprocess_calls = []

    def fake_command_runner(_root, args, _timeout):
        post_install_calls.append(list(args))
        return captured_manage_result(args)

    def fake_runner(command, **kwargs):
        record_command(subprocess_calls, command)
        cwd = Path(str(kwargs["cwd"]))
        if "startup-context" in command:
            return completed(
                command,
                stdout=json.dumps(
                    {
                        "schema_version": 1,
                        "tool": "repo-startup-context",
                        "ok": True,
                        "status": "passed",
                        "navigation": {
                            "status": "fresh",
                            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
                            "next_command": "none, navigation maps are fresh",
                        },
                    }
                )
                + "\n",
            )
        if "workflow" in command and "resume" in command:
            packet = (
                cwd
                / "automations"
                / "user-story-workflow"
                / "runs"
                / "smoke-first-run"
                / "artifacts"
                / "context"
                / "context-packet.json"
            )
            write_json(packet, {"schema_version": 1, "tool": "workflow-manager.context-packet"})
        return completed(command, stdout="{}\n")

    report = repo_doctor_clone.install_harness_smoke_report(
        source,
        work_dir=work_dir,
        runner=fake_runner,
        command_runner=fake_command_runner,
    )

    assert_ok(report)
    assert [call[0] for call in post_install_calls] == [
        "setup",
        "local-ai",
        "local-ai",
    ]
    flattened = [" ".join(call) for call in subprocess_calls]
    assert_contains_each(
        flattened,
        "setup --no-link-skills",
        PROJECT_CONTEXT_CHECK,
        "startup-context --summary --compact --format json",
        STORY_WORKFLOW_START,
        STORY_WORKFLOW_RESUME,
        "workflow smoke --name user-story-workflow",
        "workflow smoke --name bug-ticket-workflow",
    )
    assert_missing(Path(str(report["target_root"])))


def test_install_harness_smoke_kept_target_marks_temporary_setup_context(tmp):
    source = harness_source(tmp)
    work_dir = tmp / "work"

    def fake_command_runner(_root, args, _timeout):
        return captured_manage_result(args)

    def fake_runner(command, **_kwargs):
        if "startup-context" in command:
            return completed(
                command,
                stdout=json.dumps(
                    {
                        "navigation": {
                            "status": "fresh",
                            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
                            "next_command": "none, navigation maps are fresh",
                        },
                    }
                )
                + "\n",
            )
        return completed(command, stdout="{}\n")

    report = repo_doctor_clone.install_harness_smoke_report(
        source,
        work_dir=work_dir,
        keep=True,
        fast=True,
        runner=fake_runner,
        command_runner=fake_command_runner,
    )

    marker = Path(str(report["target_root"])) / repo_common.HARNESS_SMOKE_TARGET_MARKER_REL
    assert_ok(report)
    assert_exists(marker)
    assert_true(read_json(marker), "temporary_validation_target")
    assert_contains_all(report["checks"], "temporary-smoke-target-marker", "passed")


def test_install_harness_smoke_fast_skips_heavy_steps(tmp):
    source = harness_source(tmp)
    work_dir = tmp / "work"
    post_install_calls = []
    subprocess_calls = []

    def fake_command_runner(_root, args, _timeout):
        post_install_calls.append(list(args))
        return captured_manage_result(args)

    def fake_runner(command, **_kwargs):
        record_command(subprocess_calls, command)
        if "startup-context" in command:
            return completed(
                command,
                stdout=json.dumps(
                    {
                        "navigation": {
                            "status": "fresh",
                            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
                            "next_command": "none, navigation maps are fresh",
                        },
                    }
                )
                + "\n",
            )
        return completed(command, stdout="{}\n")

    report = repo_doctor_clone.install_harness_smoke_report(
        source,
        work_dir=work_dir,
        fast=True,
        runner=fake_runner,
        command_runner=fake_command_runner,
    )

    assert_ok(report)
    assert_field(report, "mode", "fast")
    assert post_install_calls == []
    flattened = [" ".join(call) for call in subprocess_calls]
    assert_contains(flattened, "setup --no-link-skills")
    assert_contains(flattened, PROJECT_CONTEXT_CHECK)
    assert_contains(flattened, "startup-context --summary --compact --format json")
    assert_lacks(flattened, STORY_WORKFLOW_START)
    assert_lacks(flattened, STORY_WORKFLOW_RESUME)
    assert_contains_all(report["checks"], "workflow-first-run", "skipped")
    assert_missing(Path(str(report["target_root"])))


def test_validate_agent_compatibility_dispatch_forwards_installed_hosts(tmp):
    observed = {}
    original = repo_manager.repo.run_skill_manager_script

    def fake_run(root, script, command):
        observed["root"] = root
        observed["script"] = script
        observed["command"] = command
        return 0

    repo_manager.repo.run_skill_manager_script = fake_run
    try:
        status = repo_manager.validate_agent_compatibility(
            Namespace(
                output_format="json",
                summary=True,
                compact=True,
                installed_hosts=True,
            ),
            tmp,
        )
    finally:
        repo_manager.repo.run_skill_manager_script = original

    assert status == 0
    assert_field(observed, "script", "validate_agent_compatibility.py")
    assert "--installed-hosts" in observed["command"]


def test_repo_cli_skill_command_support_matches_public_dispatch(tmp):
    _ = tmp
    from repo_support import repo_cli_skill_commands

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    repo_cli_skill_commands.add_skill_parsers(
        subparsers,
        repo_cli_parser.add_parser,
        repo_cli_parser.add_shared_root_arg,
        repo_cli_parser.add_examples,
    )

    cases = [
        (["compare-skill", "--old", "old-skill", "--new", "new-skill", "--format", "json"], {"command": "compare-skill", "old": "old-skill", "new": "new-skill", **JSON_OUT}),
        (["inspect-skill", "--skill", ".agents/skills/skill-manager", "--fast", "--summary", "--format", "json"], {"command": "inspect-skill", "fast": True, "summary": True, **JSON_OUT}),
        (["review-skill", "--skill", ".agents/skills/skill-manager", "--plan", "--summary", "--format", "json"], {"command": "review-skill", "plan": True, "summary": True, **JSON_OUT}),
        (["analyze-location", "incoming-skill", "--review-profile", "import", "--format", "json"], {"command": "analyze-location", "review_profile": "import", **JSON_OUT}),
        (["upgrade-skill", "--old", "old", "--new", "new", "--target", ".agents/skills/demo", "--strategy", "override", "--dry-run"], {"command": "upgrade-skill", "dry_run": True}),
        (["eval-skill", "--skill", ".agents/skills/demo", "--suite", "suite.json", "--format", "json"], {"command": "eval-skill", "baseline": "none", **JSON_OUT}),
        (["attest-skill", "--skill", ".agents/skills/demo", "--summary", "--format", "json"], {"command": "attest-skill", "summary": True, **JSON_OUT}),
        (["validate-agent-compatibility", "--installed-hosts", "--summary", "--compact", "--format", "json"], {"command": "validate-agent-compatibility", "installed_hosts": True, **COMPACT_JSON_EXPECTED}),
        (["skill-inventory", "--all", "--summary", "--format", "json"], {"command": "skill-inventory", "all": True, "summary": True, **JSON_OUT}),
        (["triage-candidates", "--candidate-root", "incoming-skills", "--review-profile", "import"], {"command": "triage-candidates", "review_profile": "import"}),
        (["measure-skill-budget", "--skill", ".agents/skills/demo", "--baseline-ref", "HEAD", "--summary", "--format", "json"], {"command": "measure-skill-budget", "baseline_ref": "HEAD", "summary": True, **JSON_OUT}),
        (["audit-skill-determinism", "--all", "--strict", "--summary", "--format", "json"], {"command": "audit-skill-determinism", "all": True, "strict": True, **JSON_OUT}),
        (["new", "--kind", "workflow", "--name", "demo-flow", "--summary", "Demo", "--uses-skill", "skill-manager"], {"command": "new", "kind": "workflow", "uses_skill": ["skill-manager"]}),
        (["new-skill-checklist", "--name", "demo-skill"], {"command": "new-skill-checklist", "name": "demo-skill"}),
        (["link-skills", "--targets", "Codex", "Claude", "--mode", "copy", "--dry-run"], {"command": "link-skills", "targets": ["Codex", "Claude"], "mode": "copy", "dry_run": True}),
    ]
    for args, expected in cases:
        assert_parsed(parser, args, expected)


def test_repo_cli_parser_covers_public_commands(tmp):
    parser = repo_cli_parser.build_parser()
    cases = [
        (["dashboard", "--fast"], {"command": "dashboard", "fast": True}),
        (["dashboard", *COMPACT_JSON], COMPACT_JSON_EXPECTED),
        (["startup-context", "--baseline-ref", "origin/main", *COMPACT_JSON], {"command": "startup-context", "baseline_ref": "origin/main", **COMPACT_JSON_EXPECTED}),
        (["context-cost-benchmark", "--min-saved-percent", "40", "--record", "--history", ".agents/local-ai/cache/context.jsonl", *COMPACT_JSON], {"command": "context-cost-benchmark", "min_saved_percent": 40.0, "record": True, "history": ".agents/local-ai/cache/context.jsonl", **COMPACT_JSON_EXPECTED}),
        (["context-cost-benchmark", "--no-record", *COMPACT_JSON], {"command": "context-cost-benchmark", "no_record": True, **COMPACT_JSON_EXPECTED}),
        (["next-action", *COMPACT_JSON], {"command": "next-action", **COMPACT_JSON_EXPECTED}),
        (["next-action", "--full", *COMPACT_JSON], {"command": "next-action", "full": True, **COMPACT_JSON_EXPECTED}),
        (["review-progress", "--mark-command", "python -B .agents/manage.py review-packet --summary --compact --format json", *COMPACT_JSON], {"command": "review-progress", "mark_command": "python -B .agents/manage.py review-packet --summary --compact --format json", **COMPACT_JSON_EXPECTED}),
        (
            [
                "review-autopilot",
                "--max-cycles",
                "2",
                "--max-units-per-cycle",
                "4",
                "--max-total-units",
                "8",
                "--max-estimated-tokens",
                "1200",
                "--max-elapsed-ms",
                "5000",
                "--dry-run",
                *COMPACT_JSON,
            ],
            {
                "command": "review-autopilot",
                "max_cycles": 2,
                "max_units_per_cycle": 4,
                "max_total_units": 8,
                "max_estimated_tokens": 1200,
                "max_elapsed_ms": 5000,
                "dry_run": True,
                **COMPACT_JSON_EXPECTED,
            },
        ),
        (
            [
                "review-loop",
                "--max-units",
                "3",
                "--timeout-seconds",
                "30",
                "--max-estimated-tokens",
                "800",
                "--max-elapsed-ms",
                "5000",
                "--reset-stale",
                "--no-reset-stale",
                "--continue",
                "--dry-run",
                *COMPACT_JSON,
            ],
            {
                "command": "review-loop",
                "max_units": 3,
                "timeout_seconds": 30,
                "max_estimated_tokens": 800,
                "max_elapsed_ms": 5000,
                "reset_stale": True,
                "no_reset_stale": True,
                "continue_run": True,
                "dry_run": True,
                **COMPACT_JSON_EXPECTED,
            },
        ),
        (["check-changed", "--refresh-navigation", *COMPACT_JSON], {"command": "check-changed", "refresh_navigation": True, "summary": True, "compact": True, "format": "json"}),
        (["check-changed", "--full", "--format", "json"], {"command": "check-changed", "full": True, "format": "json"}),
        (["check-changed", "--record-progress", "--format", "json"], {"command": "check-changed", "record_progress": True, "format": "json"}),
        (["finish", "--deep", "--budget-intent", "feature", *COMPACT_JSON], {"command": "finish", "deep": True, "budget_intent": "feature", **COMPACT_JSON_EXPECTED}),
        (["finish", "--release-full", "--budget-intent", "feature", *COMPACT_JSON], {"command": "finish", "release_full": True, "budget_intent": "feature", **COMPACT_JSON_EXPECTED}),
        (["context-use-check", *COMPACT_JSON], {"command": "context-use-check", **COMPACT_JSON_EXPECTED}),
        (["change-ledger", *COMPACT_JSON], {"command": "change-ledger", **COMPACT_JSON_EXPECTED}),
        (["claim-check", "--text", "finish passed", "--evidence-file", "evidence/finish.json", *COMPACT_JSON], {"command": "claim-check", "text": "finish passed", "evidence_files": ["evidence/finish.json"], **COMPACT_JSON_EXPECTED}),
        (["budget-trend", *COMPACT_JSON], {"command": "budget-trend", **COMPACT_JSON_EXPECTED}),
        (["context-guardrails", "--path", "AGENTS.md", *COMPACT_JSON], {"command": "context-guardrails", "paths": ["AGENTS.md"], **COMPACT_JSON_EXPECTED}),
        (["command-budget-check", "--profile", "standard", "--command", "next-action", *COMPACT_JSON], {"command": "command-budget-check", "profile": "standard", "commands": ["next-action"], **COMPACT_JSON_EXPECTED}),
        (["evidence-verify", "--file", "evidence/compact.json", *COMPACT_JSON], {"command": "evidence-verify", "files": ["evidence/compact.json"], **COMPACT_JSON_EXPECTED}),
        (["explain-route", "review workflow", *COMPACT_JSON], SUMMARY_COMPACT),
        (["which-skill", "inspect a PPTX", *COMPACT_JSON], SUMMARY_COMPACT),
        (["commands", "--harness", "--summary", "--format", "json"], {"harness": True, "summary": True}),
        (["install-harness-smoke", "--fast", "--format", "json"], {"fast": True, **JSON_OUT}),
        (["start-here", "--simple", "--profile", "minimal", "--format", "json"], {"command": "start-here", "simple": True, "profile": "minimal"}),
        (["project-kickoff", "--target", NEW_PROJECT_TARGET, "--from-request", "new app", "--summary", "--compact", "--format", "json"], {"command": "project-kickoff", "target": NEW_PROJECT_TARGET, "from_request": "new app", **COMPACT_JSON_EXPECTED}),
        (["project-kickoff", "--target", NEW_PROJECT_TARGET, "--apply"], {"command": "project-kickoff", "target": NEW_PROJECT_TARGET, "apply": True}),
        (["project-context-review", "--target", NEW_PROJECT_TARGET, "--from-request", "new app", "--write-review", "--summary", "--compact", "--format", "json"], {"command": "project-context-review", "target": NEW_PROJECT_TARGET, "from_request": "new app", "write_review": True, **COMPACT_JSON_EXPECTED}),
        (
            ["project-context-apply-review", "--target", NEW_PROJECT_TARGET, "--review", "docs/project/review/project-context-review.json", "--apply", "--format", "json"],
            {"command": "project-context-apply-review", "target": NEW_PROJECT_TARGET, "review": "docs/project/review/project-context-review.json", "apply": True, **JSON_OUT},
        ),
        (["dotnet-context", "--target", NEW_PROJECT_TARGET, "--format", "json"], {"command": "dotnet-context", "target": NEW_PROJECT_TARGET, **JSON_OUT}),
        (
            ["dotnet-context", "--target", NEW_PROJECT_TARGET, "--solution", "App.sln", "--project", "src/App/App.csproj", "--format", "json"],
            {"command": "dotnet-context", "target": NEW_PROJECT_TARGET, "solution": ["App.sln"], "project": ["src/App/App.csproj"], **JSON_OUT},
        ),
        (
            ["dotnet-context", "--target", NEW_PROJECT_TARGET, "--dotnet-executable", "D:/sdks/dotnet/dotnet.exe", "--format", "json"],
            {"command": "dotnet-context", "target": NEW_PROJECT_TARGET, "dotnet_executable": "D:/sdks/dotnet/dotnet.exe", **JSON_OUT},
        ),
        (
            ["dotnet-context", "--target", NEW_PROJECT_TARGET, "--write-evidence", "--baseline", "docs/project/dotnet-context/dotnet-context.json", "--format", "json"],
            {
                "command": "dotnet-context",
                "target": NEW_PROJECT_TARGET,
                "write_evidence": True,
                "baseline": "docs/project/dotnet-context/dotnet-context.json",
                **JSON_OUT,
            },
        ),
        (["install-wizard", "--target", NEW_PROJECT_TARGET, "--no-input", "--profile", "minimal", "--format", "json"], {"command": "install-wizard", "no_input": True, "profile": "minimal"}),
        (["validate-copy-contract", "--profile", "standard", "--format", "json"], {"command": "validate-copy-contract"}),
        (["harness-promote", "--target", NEW_PROJECT_TARGET, "--dry-run", "--format", "json"], {"command": "harness-promote", "target": NEW_PROJECT_TARGET, "dry_run": True, **JSON_OUT}),
        (["harness-promote", "--target", NEW_PROJECT_TARGET, "--apply", "--paths", "docs/agent-start.md"], {"command": "harness-promote", "target": NEW_PROJECT_TARGET, "apply": True, "paths": ["docs/agent-start.md"]}),
        (["harness-status", "--check-upstream", "--format", "json"], {"command": "harness-status", "check_upstream": True, **JSON_OUT}),
        (["harness-update", "--to", "latest", "--apply", "--format", "json"], {"command": "harness-update", "target_tag": "latest", "apply": True, **JSON_OUT}),
        (["harness-rollback", "--transaction", "tx-1", "--format", "json"], {"command": "harness-rollback", "transaction": "tx-1", **JSON_OUT}),
        (["harness-adopt", "--tag", "v1.0.0", "--archive", "mirror.zip", "--archive-metadata", "mirror.json"], {"command": "harness-adopt", "tag": "v1.0.0", "archive": "mirror.zip", "archive_metadata": "mirror.json"}),
        (["harness-release-check", "--tag", "v1.0.0", "--format", "json"], {"command": "harness-release-check", "tag": "v1.0.0", **JSON_OUT}),
        (["public-export", "--target", "temp/public-export", "--dry-run", "--format", "json"], {"command": "public-export", "dry_run": True}),
        (["validate", "--deep"], {"command": "validate", "deep": True}),
        (["check-additions", *COMPACT_JSON], {"command": "check-additions", "summary": True, "compact": True, "format": "json"}),
        (["review-packet", "--write", "evidence/review", *COMPACT_JSON], {"command": "review-packet", "write_dir": "evidence/review", "summary": True, "compact": True, "format": "json"}),
        (["review-packet", "--owner", "skill:skill-manager", *COMPACT_JSON], {"command": "review-packet", "owner": "skill:skill-manager", "summary": True, "compact": True, "format": "json"}),
        (["review-packet", "--owner", "skill:skill-manager", "--path", ".agents/skills/skill-manager/scripts/repo_support/repo_changed.py", *COMPACT_JSON], {"command": "review-packet", "owner": "skill:skill-manager", "paths": [".agents/skills/skill-manager/scripts/repo_support/repo_changed.py"], "summary": True, "compact": True, "format": "json"}),
        (["review-packet", "--owner", "skill:skill-manager", "--path", ".agents/skills/skill-manager/scripts/run_self_tests.py", "--hunk", "h001", *COMPACT_JSON], {"command": "review-packet", "owner": "skill:skill-manager", "paths": [".agents/skills/skill-manager/scripts/run_self_tests.py"], "hunks": ["h001"], "summary": True, "compact": True, "format": "json"}),
        (["handoff-packet", "--owner", "skill:skill-manager", *COMPACT_JSON], {"command": "handoff-packet", "owner": "skill:skill-manager", "summary": True, "compact": True, "format": "json"}),
        (["fresh-agent-packet", "--owner", "skill:skill-manager", *COMPACT_JSON], {"command": "fresh-agent-packet", "owner": "skill:skill-manager", "summary": True, "compact": True, "format": "json"}),
        (["portable-constraints", "--changed", *COMPACT_JSON], {"command": "portable-constraints", "changed": True, "summary": True, "compact": True, "output_format": "json"}),
        (["syntax-check", "--paths", ".agents/skills", "automations", "--format", "json"], {"command": "syntax-check", "paths": [".agents/skills", "automations"], **JSON_OUT}),
        (["audit-candidate-source", "temp/dotnet-artisan-main/plugins/dotnet-artisan", "--summary", "--format", "json"], {"command": "audit-candidate-source", "summary": True, **JSON_OUT}),
        (["workflow", "eval", "--all", *COMPACT_JSON], {"command": "workflow", "workflow_args": ["eval", "--all", *COMPACT_JSON]}),
        (["workflow", "doctor", "--all", *COMPACT_JSON], {"workflow_args": ["doctor", "--all", *COMPACT_JSON]}),
        (["workflow", "context", "--all", "--check", *COMPACT_JSON], {"workflow_args": ["context", "--all", "--check", *COMPACT_JSON]}),
        (["workflow", "workers", "--profiles", "--format", "json"], {"workflow_args": ["workers", "--profiles", "--format", "json"]}),
        (["sync-automation-routing", "--check"], {"command": "sync-automation-routing", "check": True}),
        (["check-repo-health", "--summary", "--json"], {"summary": True, "json": True}),
        (["check-repo-health", "--summary", "--compact", "--json"], {**SUMMARY_COMPACT, "json": True}),
        (["portable-tools", "--check", "--require-installed", *COMPACT_JSON], {"command": "portable-tools", "check": True, "require_installed": True, **COMPACT_JSON_EXPECTED}),
        (["setup", "--check", *COMPACT_JSON], {"command": "setup", **COMPACT_JSON_EXPECTED}),
        (["setup", "--offline"], {"command": "setup", "offline": True}),
        (["setup", "--install-rg", "--install-rg-portable", "--no-tool-prompts"], {"install_rg": True, "install_rg_portable": True, "no_tool_prompts": True}),
        (["credential-doctor", *COMPACT_JSON], {"command": "credential-doctor", **COMPACT_JSON_EXPECTED}),
        ([
            "credential-doctor",
            "--configure",
            "--service",
            "sonarqube",
            "--name",
            "project-a",
            "--base-url",
            "https://sonar.example",
            "--project-key",
            "ProjectA",
            "--token-env",
            "SONAR_TOKEN",
            "--format",
            "json",
        ], {"command": "credential-doctor", "configure": True, "service": "sonarqube", "name": "project-a", "base_url": "https://sonar.example", "project_key": "ProjectA", "token_env": "SONAR_TOKEN", **JSON_OUT}),
        ([
            "install-harness",
            "--target",
            NEW_PROJECT_TARGET,
            "--profile",
            "minimal",
            "--dry-run",
            "--force",
            "--run-setup-check",
            "--install-rg-portable",
            "--bootstrap-local-ai",
            "--format",
            "json",
        ], {"command": "install-harness", "target": NEW_PROJECT_TARGET, "profile": "minimal", "dry_run": True, "force": True, "run_setup_check": True, "install_rg_portable": True, "bootstrap_local_ai": True, **JSON_OUT}),
        (["release" + "-evidence", *COMPACT_JSON], SUMMARY_COMPACT),
        (["review-skill", "--skill", SKILL_MANAGER_PATH, "--plan"], {"command": "review-skill", "skill": SKILL_MANAGER_PATH, "plan": True}),
        (["review-skill", "--skill", SKILL_MANAGER_PATH, *COMPACT_JSON], SUMMARY_COMPACT),
        (["inspect-skill", "--skill", SKILL_MANAGER_PATH, "--deep", *COMPACT_JSON], SUMMARY_COMPACT),
        (["analyze-location", SKILL_MANAGER_PATH, *COMPACT_JSON], SUMMARY_COMPACT),
        (["attest-skill", "--skill", SKILL_MANAGER_PATH, *COMPACT_JSON], SUMMARY_COMPACT),
        (["claude-adapter-budget", *COMPACT_JSON], SUMMARY_COMPACT),
    ]
    for args, expected in cases:
        assert_parsed(parser, args, expected)


def test_dotnet_context_command_metadata_reports_write_evidence_boundary(_tmp):
    metadata = repo_commands.COMMAND_METADATA["dotnet-context"]

    assert_has_all(metadata["writes"], "read-only unless --write-evidence", "docs/project/dotnet-context")


def test_dotnet_context_resolves_relative_baseline_under_target(tmp):
    target = tmp / "consumer"
    captured = {}
    args = Namespace(
        target=str(target),
        output_format="json",
        no_cli_probes=True,
        dotnet_executable="",
        baseline="docs/project/dotnet-context/dotnet-context.json",
        solution=[],
        project=[],
        write_evidence=False,
        evidence_dir="docs/project/dotnet-context",
    )

    def fake_run_skill_script(_root, skill, script, arguments):
        captured["skill"] = skill
        captured["script"] = script
        captured["arguments"] = arguments
        return 0

    with patched_attrs(repo_manager.repo, run_skill_script=fake_run_skill_script):
        exit_code = repo_manager.dotnet_context(args, tmp / "source")

    expected = (target / "docs/project/dotnet-context/dotnet-context.json").resolve(strict=False)
    arguments = captured["arguments"]
    assert exit_code == 0
    assert_field(captured, "skill", "dotnet-project-context")
    assert Path(arguments[arguments.index("--baseline") + 1]) == expected


def test_local_ai_help_leads_with_read_only_checks(_tmp):
    parser = repo_cli_parser.build_parser()
    subparsers = next(action for action in parser._actions if getattr(action, "choices", None))
    help_text = subparsers.choices["local-ai"].format_help()

    assert_has_all(
        help_text,
        "inspect or explicitly prepare",
        "diagnostics may inspect local settings, profiles, caches, or host state",
        "Strict no-profile/no-cache dogfood uses local-ai-helper strict_read_only_commands",
        "local-ai readiness --summary --compact --json",
        "local-ai policy --summary --compact --json",
        "local-ai doctor --quick --summary --compact --json",
        "local-ai bootstrap --dry-run",
    )
    assert help_text.index("local-ai readiness") < help_text.index("local-ai bootstrap --dry-run")
    assert help_text.index("local-ai bootstrap --dry-run") < help_text.index("local-ai bootstrap --run-model")


def test_manage_launcher_runs_repo_manager_in_process(tmp):
    launcher = Path(__file__).resolve().parents[4] / ".agents" / "manage.py"
    text = launcher.read_text(encoding="utf-8")

    assert_has_all(
        text,
        "runpy.run_path",
        "repo_manager.py",
        "use_local_ai_fast_path",
        "setup_local_ai.py",
        "require_supported_python",
    )
    assert "subprocess.run" not in text


def test_manage_launcher_fast_path_preserves_root_help_and_version_contracts(tmp):
    launcher_path = Path(__file__).resolve().parents[4] / ".agents" / "manage.py"
    spec = importlib.util.spec_from_file_location("_manage_launcher_contract_test", launcher_path)
    assert spec is not None and spec.loader is not None
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    calls = []

    def fake_run_path(path, *, run_name):
        calls.append((Path(path).name, list(sys.argv), run_name))
        raise SystemExit(0)

    original_sys_path = list(sys.path)
    original_dont_write = os.environ.get("PYTHONDONTWRITEBYTECODE")
    try:
        with patched_attrs(launcher.runpy, run_path=fake_run_path):
            with patched_attrs(sys, argv=[str(launcher_path), "local-ai", "status", "--json"]):
                assert launcher.main() == 0
            with patched_attrs(sys, argv=[str(launcher_path), "local-ai", "--root", str(tmp), "status"]):
                assert launcher.main() == 0
            with patched_attrs(sys, argv=[str(launcher_path), "local-ai", f"--root={tmp}", "status"]):
                assert launcher.main() == 0
            with patched_attrs(sys, argv=[str(launcher_path), "local-ai", "--help"]):
                assert launcher.main() == 0
    finally:
        sys.path[:] = original_sys_path
        if original_dont_write is None:
            os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
        else:
            os.environ["PYTHONDONTWRITEBYTECODE"] = original_dont_write

    assert calls[0][0] == "setup_local_ai.py"
    assert calls[0][1][1:3] == ["--root", str(launcher_path.parents[1])]
    assert calls[1][0] == "repo_manager.py"
    assert calls[1][1][1:] == ["local-ai", "--root", str(tmp), "status"]
    assert calls[2][0] == "repo_manager.py"
    assert calls[2][1][1:] == ["local-ai", f"--root={tmp}", "status"]
    assert calls[3][0] == "repo_manager.py"

    with patched_attrs(sys, version_info=(3, 11, 9)), patched_attrs(
        launcher.runpy,
        run_path=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not dispatch")),
    ):
        try:
            launcher.main()
        except SystemExit as exc:
            assert "Python 3.12+ is required" in str(exc)
        else:
            raise AssertionError("unsupported Python must fail before dispatch")


def test_forward_workflow_command_preserves_sync_automation_check_flag(tmp):
    calls = []
    args = Namespace(command="sync-automation-routing", check=True, workflow_args=[])

    def fake_run_workflow_repo_manager(_root, command):
        calls.append(command)
        return 0

    with patched_attrs(repo_manager.repo, run_workflow_repo_manager=fake_run_workflow_repo_manager):
        status = repo_manager.forward_workflow_command(args, tmp)

    assert status == 0
    assert calls == [["sync-automation-routing", "--root", str(tmp), "--check"]]


def test_sync_check_bypasses_failure_triage_for_strict_read_only(tmp):
    calls = []

    def fake_sync_all(root, *, check):
        calls.append((root, check))
        return 23

    def forbidden_failure_triage(*_args, **_kwargs):
        raise AssertionError("sync --check must not write failure-triage cache")

    with patched_attrs(repo_manager.repo_generated, sync_all=fake_sync_all), patched_attrs(
        repo_manager.repo_local_ai,
        run_with_failure_triage=forbidden_failure_triage,
    ), patched_attrs(sys, argv=["manage.py", "sync", "--root", str(tmp), "--check"]):
        status = repo_manager.main()

    assert status == 23
    assert calls == [(tmp.resolve(), True)]


def test_syntax_check_parses_python_without_pycache_and_accepts_windows_paths(tmp):
    write_text(tmp / ".agents" / "skills" / "demo" / "ok.py", "VALUE = 1\n")
    report = repo_syntax.syntax_check_report(tmp, [".agents\\skills\\demo"])

    assert_ok(report)
    assert_fields(report, checked=1, failed=0, bytecode_written=False)
    assert report["paths"] == [".agents/skills/demo/ok.py"]
    assert not list(tmp.rglob("__pycache__"))


def test_syntax_check_reports_syntax_errors_without_bytecode(tmp):
    write_text(tmp / "automations" / "bad.py", "def broken(:\n    pass\n")
    report = repo_syntax.syntax_check_report(tmp, ["automations"])

    assert_not_ok(report)
    assert_fields(report, status="failed", checked=1, failed=1, bytecode_written=False)
    assert_fields(report["issues"][0], path="automations/bad.py", type="syntax")
    assert report["issues"][0]["line"] == 1
    assert not list(tmp.rglob("__pycache__"))


def test_syntax_check_rejects_outside_root_without_mutation(tmp):
    outside = tmp.parent / "outside.py"
    write_text(outside, "VALUE = 1\n")
    report = repo_syntax.syntax_check_report(tmp, [str(outside)])

    assert_not_ok(report)
    assert_fields(report, checked=0, failed=1, bytecode_written=False)
    assert_field(report["issues"][0], "type", "path")
    assert "outside repository" in report["issues"][0]["message"]
    assert not list(tmp.rglob("__pycache__"))


def test_credential_configure_writes_gitignored_service_profile(tmp):
    (tmp / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    args = Namespace(
        service="sonarqube",
        name="project-a",
        base_url="https://sonar.example",
        project_key="ProjectA",
        token_env="SONAR_TOKEN",
        token=None,
        no_input=True,
        overwrite=False,
    )
    report = repo_qol_daily.configure_credential_profile(tmp, args)

    assert_ok(report)
    assert_field(report, "status", "configured")
    assert_field(report, "service", "sonarqube")
    secrets = json.loads((tmp / repo_service_config.SECRET_STORE_REL).read_text(encoding="utf-8"))
    assert secrets["sonarqube"][0]["name"] == "project-a"
    assert secrets["sonarqube"][0]["base_url"] == "https://sonar.example"
    assert secrets["sonarqube"][0]["project_key"] == "ProjectA"
    assert secrets["sonarqube"][0]["token_env"] == "SONAR_TOKEN"
    gitignore = (tmp / ".gitignore").read_text(encoding="utf-8")
    assert ".agents/local-ai/secrets.local.json" in gitignore
    assert ".agents/local-ai/local.settings.json" in gitignore


def test_credential_doctor_reports_service_specific_profiles(tmp):
    secret_path = tmp / repo_service_config.SECRET_STORE_REL
    secret_path.parent.mkdir(parents=True)
    secret_path.write_text(
        json.dumps(
            {
                "sonarqube": [
                    {
                        "name": "project-a",
                        "base_url": "https://sonar.example",
                        "project_key": "ProjectA",
                        "token_env": "SONAR_TOKEN",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    removed: dict[str, str | None] = {}
    for name in ["AZURE_DEVOPS_PAT", "ADO_PAT", "SYSTEM_ACCESSTOKEN", "AZURE_DEVOPS_ORG_URL", "SONAR_TOKEN", "SONAR_HOST_URL", "SONAR_PROJECT_KEY"]:
        removed[name] = os.environ.pop(name, None)
    try:
        report = repo_qol_daily.credential_doctor_report(tmp)
    finally:
        for name, value in removed.items():
            if value is not None:
                os.environ[name] = value
    checks = {item["name"]: item for item in report["checks"]}

    assert checks["sonarqube-diagnostics"]["configured"] is True
    assert checks["azure-devops-ticket-intake"]["configured"] is False
    assert checks["sonarqube-diagnostics"]["service_config"]["complete_profile_count"] == 1


def test_credential_configure_no_input_reports_missing_fields(tmp):
    args = Namespace(service="azure-devops", name="customer-a", no_input=True)
    report = repo_qol_daily.configure_credential_profile(tmp, args)

    assert_not_ok(report)
    assert_field(report, "status", "needs-input")
    assert_contains(report["missing"], "organization_url")
    assert_contains(report["missing"], "project")
    assert_contains(report["missing"], "pat_env or pat")


def test_dashboard_summary_compact_omits_passing_detail(tmp):
    _ = tmp
    long_validation_command = (
        "python -B .agents/manage.py syntax-check --paths "
        + " ".join(f"src/module_{index}.py" for index in range(80))
        + " --format json"
    )
    report = {
        "schema_version": 1,
        "tool": "repo-dashboard",
        "ok": False,
        "status": "warning",
        "plain_status": "warning",
        "mode": "fast",
        "total_elapsed_ms": 12,
        "timing_sections": [{"name": "repo_health", "ok": True, "elapsed_ms": 4}],
        "latency_budget": {
            "status": "within-budget",
            "budget_ms": repo_command_metrics.LATENCY_BUDGETS_MS["status-fast"],
            "elapsed_ms": 12,
        },
        "branch": "feature/demo",
        "changed_file_count": 0,
        "changed_groups": "",
        "checks": [
            {"name": "generated", "ok": True},
            {"name": "validation", "ok": False},
        ],
        "generated_checks": [{"name": "routing", "ok": True}],
        "context_budget": {"status": "ok", "estimated_low_context_tokens": 10, "changed_file_count": 0},
        "validation_router": {
            "status": "planned",
            "summary": {"command_count": 1, "required_count": 1, "optional_count": 0, "owners": {"skill-manager": 1}},
            "commands": [
                {
                    "order": 1,
                    "command": "python -B .agents/manage.py check-additions",
                    "reason": "changed or new files must have an owning contract",
                    "owner": "skill-manager",
                    "required": True,
                }
            ],
            "next_command": "python -B .agents/manage.py check-additions",
        },
        "dirty_state": {"ok": True, "status": "clean", "dirty": False},
        "github_validation": {"status": "local-only", "automatic_triggers_enabled": False, "automatic_triggers": []},
        "skipped": [],
        "next_command": long_validation_command,
        "next_command_reason": "Changed files need finish evidence.",
    }
    seen_roots: list[Path | None] = []
    original_compact_next_command = repo_qol_dashboard.repo_changed.compact_next_command

    def capture_compact_next_command(command, *, root=None):
        seen_roots.append(root)
        return original_compact_next_command(command, root=root)

    with patched_attrs(
        repo_qol_dashboard.repo_changed,
        compact_next_command=capture_compact_next_command,
    ):
        compact = repo_qol.summarize_dashboard_report(
            report,
            compact=True,
            root=tmp,
        )

    assert_summary(compact, failed_check_count=1, context_status="ok")
    assert_field(compact["latency_budget"], "budget_ms", repo_command_metrics.LATENCY_BUDGETS_MS["status-fast"])
    assert_field(compact["output_budget"], "budget_tokens", 2000)
    assert_field(compact["output_budget"], "scope", "summary-compact-json-estimate")
    assert_field(
        compact,
        "next_command",
        "python -B .agents/manage.py check-changed --record-progress --summary --compact --format json",
    )
    assert seen_roots == [tmp], seen_roots
    assert_field(compact, "next_command_reason", "Changed files need finish evidence.")
    assert_field(compact, "checks", [{"name": "validation", "ok": False}])
    assert_field(compact["validation_router"], "next_command", "python -B .agents/manage.py check-additions")
    assert_field(compact["validation_router"]["summary"]["owners"], "skill-manager", 1)
    assert_keys_lack(compact["validation_router"], "commands")
    assert_keys_lack(compact, "context_budget", "skipped")


def test_dashboard_compact_stale_navigation_prioritizes_navigation_over_review_packet(tmp):
    _ = tmp
    review_packet = {
        "status": "over-budget",
        "review_budget_tokens": 5000,
        "changed_diff_estimated_tokens": 90000,
        "tokens_over_review_budget": 85000,
        "owner_review_packet_count": 12,
        "owner_review_subpacket_count": 48,
        "largest_owner_subpacket_estimated_tokens": 12000,
        "owner_review_hunk_count": 180,
        "largest_owner_hunk_estimated_tokens": 3600,
        "owner_review_packets": [
            {
                "owner": f"skill:owner-{index}",
                "changed_file_count": index + 1,
                "estimated_changed_tokens": 6000 + index,
                "owner_summary_command": (
                    "python -B .agents/manage.py review-packet "
                    f"--owner skill:owner-{index} --summary --compact --format json"
                ),
            }
            for index in range(12)
        ],
        "validation_first": [{"command": "python -B .agents/manage.py check-additions"}],
    }
    report = {
        "schema_version": 1,
        "tool": "repo-dashboard",
        "ok": True,
        "status": "ok",
        "plain_status": "dirty",
        "mode": "fast",
        "total_elapsed_ms": 50,
        "latency_budget": {"status": "within-budget", "budget_ms": 3000, "elapsed_ms": 50},
        "branch": "feature/demo",
        "dirty_state": {"ok": True, "status": "dirty", "dirty": True},
        "changed_file_count": 12,
        "changed_groups": "many changed files",
        "checks": [],
        "generated_checks": [],
        "context_budget": {
            "status": "warning",
            "estimated_low_context_tokens": 1200,
            "changed_file_count": 12,
            "changed_diff_estimated_tokens": 90000,
            "changed_diff_tokens_over_review_budget": 85000,
        },
        "navigation": {
            "status": "stale",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "python -B .agents/skills/repo-navigation/scripts/repo_navigation.py update --target . --write --format json",
            "read_only_next_step": "Read AGENTS.md and automations/navigation/artifacts/maps/HANDOFF.md; refresh navigation only when writes are allowed.",
            "stale_output_count": 1,
            "summary": "Navigation maps are stale; refresh before broad source reads.",
        },
        "validation_router": {
            "status": "planned",
            "summary": {"command_count": 1, "required_count": 1, "optional_count": 0, "owners": {"skill-manager": 1}},
            "next_command": "python -B .agents/manage.py check-additions",
        },
        "validation_progress": {"status": "running", "phase": "plan", "input_fingerprint_match": False},
        "review_packet": review_packet,
        "github_validation": {"status": "local-only", "automatic_triggers_enabled": False, "automatic_triggers": []},
        "skipped": [],
        "next_command": "python -B .agents/skills/repo-navigation/scripts/repo_navigation.py update --target . --write --format json",
        "next_command_reason": "Navigation maps are stale.",
    }

    compact = repo_qol.summarize_dashboard_report(report, compact=True)

    assert_field(compact["navigation"], "status", "stale")
    assert_has_all(compact["navigation"]["read_only_next_step"], "HANDOFF.md", "writes are allowed")
    assert "review_packet" not in compact
    assert_field(compact["output_budget"], "status", "within-budget")


def test_output_budget_counts_attached_budget_payload(_tmp):
    payload = {
        "schema_version": 1,
        "tool": "demo",
        "status": "ok",
        "detail": "x" * 7600,
    }

    with_budget = repo_command_metrics.attach_output_budget(payload, "status-fast")
    actual_tokens = repo_command_metrics.estimated_json_output_tokens(with_budget)

    assert_field(with_budget["output_budget"], "estimated_output_tokens", actual_tokens)
    assert_field(
        with_budget["output_budget"],
        "status",
        "within-budget" if actual_tokens <= with_budget["output_budget"]["budget_tokens"] else "over-budget",
    )


def test_route_summary_compact_omits_candidate_rows(tmp):
    _ = tmp
    report = {
        "schema_version": 1,
        "tool": "skill-manager.route-explainer",
        "ok": True,
        "status": "matched",
        "query": "review workflow",
        "query_terms": ["review", "workflow"],
        "selected_owner": "workflow-manager",
        "confidence": "medium",
        "matched_route_count": 3,
        "returned_route_count": 2,
        "rejected_route_count": 1,
        "selected_route": {
            "kind": "skill",
            "name": "workflow-manager",
            "score": 2,
            "matched_terms": ["workflow"],
            "open": ".agents/skills/workflow-manager/SKILL.md",
            "use_when": "long routed instruction text",
            "reason": "Matches workflow-manager.",
        },
        "routes": [{"name": "workflow-manager", "kind": "skill", "score": 2, "open": ".agents/skills/workflow-manager/SKILL.md"}],
        "rejected_routes": [{"name": "candidate-import-workflow", "kind": "workflow", "score": 1}],
        "next_command": "Open .agents/skills/workflow-manager/SKILL.md.",
    }

    compact = repo_routing.summarize_route_report(report, compact=True)

    assert_field(compact, "selected_owner", "workflow-manager")
    assert_summary(compact, matched_route_count=3)
    assert_keys_lack(compact, "routes", "rejected_routes")
    assert_keys_lack(compact["selected_route"], "use_when")


def test_which_skill_uses_aliases_and_excludes_workflows(tmp):
    write_text(
        tmp / ".agents" / "routing.md",
        """# Skill Routing Index

| Category | Skill | Use When | Open |
|---|---|---|---|
| Documentation | `workflow-manager` | Creating, reviewing, validating, routing, or resuming automations and workflow modules with module.json contracts. | `.agents/skills/workflow-manager/SKILL.md` |
| Documentation | `repo-navigation` | Orienting in a repository with compact briefs, direct search, dependency impact, and navigation maps. | `.agents/skills/repo-navigation/SKILL.md` |
| Documents | `document-artifacts` | Inspecting PowerPoint, Word, Excel, or PDF files. | `.agents/skills/document-artifacts/SKILL.md` |
| Web | `playwright-integration` | Checking browser automation and UI test readiness. | `.agents/skills/playwright-integration/SKILL.md` |
| Delivery | `dotnet-delivery` | Designing build and test pipelines for .NET projects. | `.agents/skills/dotnet-delivery/SKILL.md` |
| Delivery | `dotnet-engineering` | Building, reviewing, refactoring, or architecting modern .NET/C# applications, ASP.NET Core services, and libraries. Use dotnet-legacy for .NET Framework work. | `.agents/skills/dotnet-engineering/SKILL.md` |
| Delivery | `dotnet-legacy` | Maintaining, reviewing, safely refactoring, planning, or executing approved modernization for .NET Framework systems. | `.agents/skills/dotnet-legacy/SKILL.md` |
| Documentation | `local-ai-helper` | Installing and configuring repo-local AI models. | `.agents/skills/local-ai-helper/SKILL.md` |
| Skill Maintenance | `skill-manager` | Managing repository skills for creation, import, validation, inventory, and budgets. | `.agents/skills/skill-manager/SKILL.md` |
| Skill Maintenance | `agent-benchmarking` | Comparing model, tool, workflow, token-use, cost, and quality benchmark runs. | `.agents/skills/agent-benchmarking/SKILL.md` |
| Intake | `azure-devops-ticket-intake` | Importing Azure DevOps tickets into local workflow run folders. | `.agents/skills/azure-devops-ticket-intake/SKILL.md` |
""",
    )
    write_text(
        tmp / "automations" / "routing.md",
        """# Workflow Routing

| Workflow | Use When | Open | Contract |
|---|---|---|---|
| document-workflow | Inspecting PPTX workflows. | `automations/document-workflow/WORKFLOW.md` | `automations/document-workflow/module.json` |
""",
    )

    report = repo_routing.explain_routes(
        tmp,
        "inspect a PPTX deck",
        kinds={"skill"},
        tool_name="skill-manager.which-skill",
    )

    assert_tool(report, "skill-manager.which-skill")
    assert_field(report, "selected_owner", "document-artifacts")
    assert all(route["kind"] == "skill" for route in report["routes"])

    workflow_report = repo_routing.explain_routes(
        tmp,
        "review a workflow plan",
        kinds={"skill"},
        tool_name="skill-manager.which-skill",
    )
    assert_field(workflow_report, "selected_owner", "workflow-manager")

    workflow_evidence_report = repo_routing.explain_routes(
        tmp,
        "finish workflow run with evidence",
        kinds={"skill"},
        tool_name="skill-manager.which-skill",
    )
    assert_field(workflow_evidence_report, "selected_owner", "workflow-manager")

    playwright_report = repo_routing.explain_routes(
        tmp,
        "fix flaky Playwright tests",
        kinds={"skill"},
        tool_name="skill-manager.which-skill",
    )
    assert_field(playwright_report, "selected_owner", "playwright-integration")

    retrieval_report = repo_routing.explain_routes(
        tmp,
        "repository search with ripgrep",
        kinds={"skill"},
        tool_name="skill-manager.which-skill",
    )
    assert_field(retrieval_report, "selected_owner", "repo-navigation")

    portable_report = repo_routing.explain_routes(
        tmp,
        "portable executable manifest",
        kinds={"skill"},
        tool_name="skill-manager.which-skill",
    )
    assert_field(portable_report, "selected_owner", "skill-manager")

    legacy_report = repo_routing.explain_routes(
        tmp,
        "maintain classic ASP.NET MVC WCF .NET Framework packages.config binding redirects",
        kinds={"skill"},
        tool_name="skill-manager.which-skill",
    )
    assert_field(legacy_report, "selected_owner", "dotnet-legacy")

    unrelated_report = repo_routing.explain_routes(
        tmp,
        "make espresso and croissants",
        kinds={"skill"},
        tool_name="skill-manager.which-skill",
    )
    assert_field(unrelated_report, "status", "no-match")
    assert_field(unrelated_report, "selected_owner", "")


def test_which_workflow_selects_story_and_bug_with_lifecycle_commands(tmp):
    write_text(
        tmp / ".agents" / "routing.md",
        """# Skill Routing Index

| Category | Skill | Use When | Open |
|---|---|---|---|
| Documentation | `workflow-manager` | Creating, reviewing, validating, routing, or resuming automations and workflow modules with module.json contracts. | `.agents/skills/workflow-manager/SKILL.md` |
| Skill Maintenance | `skill-manager` | Managing repository skills for creation, import, validation, inventory, and budgets. | `.agents/skills/skill-manager/SKILL.md` |
""",
    )
    write_text(
        tmp / "automations" / "routing.md",
        """# Workflow Routing

| Workflow | Use When | Open | Contract |
|---|---|---|---|
| `agent-benchmarking` | Runs benchmark suites, records packets, compares results, checks routing baselines, promotes lessons, and indexes evidence. | `automations/agent-benchmarking/WORKFLOW.md` | `automations/agent-benchmarking/module.json` |
| `bug-ticket-workflow` | Bug workflow for intake, reproduction, approved fix, validation, and PR handoff. | `automations/bug-ticket-workflow/WORKFLOW.md` | `automations/bug-ticket-workflow/module.json` |
| `disciplined-change-workflow` | Disciplined change workflow for larger changes, fresh evidence, validation, and implementation handoff. | `automations/disciplined-change-workflow/WORKFLOW.md` | `automations/disciplined-change-workflow/module.json` |
| `local-ai-benchmark-workflow` | Coordinates local AI model/runtime benchmarks, including GGUF, llama-server, speculative MTP/n-gram code-generation, embedding, repository-search, and vision evidence. | `automations/local-ai-benchmark-workflow/WORKFLOW.md` | `automations/local-ai-benchmark-workflow/module.json` |
| `user-story-workflow` | Story workflow for intake, planning, approved implementation, validation, follow-up, and PR handoff evidence. | `automations/user-story-workflow/WORKFLOW.md` | `automations/user-story-workflow/module.json` |
| `feedback-improvement-workflow` | Reviews local failure feedback and writes an improvement action plan. | `automations/feedback-improvement-workflow/WORKFLOW.md` | `automations/feedback-improvement-workflow/module.json` |
""",
    )

    story_report = repo_routing.explain_routes(
        tmp,
        "we need to implement a user story with acceptance criteria",
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )
    assert_tool(story_report, "skill-manager.which-workflow")
    assert_field(story_report, "selected_owner", "user-story-workflow")
    assert_field(
        story_report,
        "next_command",
        "python -B .agents/manage.py workflow start --name user-story-workflow --summary --compact --format json",
    )
    assert all(route["kind"] == "workflow" for route in story_report["routes"])

    for query in (
        "implement Azure DevOps user story 123",
        "implement a feature request",
        "add a new checkout discount feature",
        "build a new API endpoint",
    ):
        report = repo_routing.explain_routes(
            tmp,
            query,
            kinds={"workflow"},
            tool_name="skill-manager.which-workflow",
        )
        assert_field(report, "selected_owner", "user-story-workflow")
        assert_field(
            report,
            "next_command",
            "python -B .agents/manage.py workflow start --name user-story-workflow --summary --compact --format json",
        )

    bug_report = repo_routing.explain_routes(
        tmp,
        "fix a bug ticket with reproduction and regression proof",
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )
    assert_field(bug_report, "selected_owner", "bug-ticket-workflow")
    assert_field(
        bug_report,
        "next_command",
        "python -B .agents/manage.py workflow start --name bug-ticket-workflow --summary --compact --format json",
    )

    local_ai_report = repo_routing.explain_routes(
        tmp,
        "warm llama-server speculative code-generation benchmark ngram MTP rows",
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )
    assert_field(local_ai_report, "selected_owner", "local-ai-benchmark-workflow")
    assert "agent-benchmarking" != local_ai_report["selected_owner"]
    matched_terms = set(local_ai_report["selected_route"]["matched_terms"])
    assert {"llama-server", "speculative", "mtp"}.issubset(matched_terms)

    unrelated_report = repo_routing.explain_routes(
        tmp,
        "order pizza for lunch",
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )
    assert_field(unrelated_report, "status", "no-match")
    assert_field(unrelated_report, "next_command", "Open automations/routing.md.")

    analysis_report = repo_routing.explain_routes(
        tmp,
        "What would the next best improvements or features be?",
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )
    assert_field(analysis_report, "read_only_request", True)
    assert_field(analysis_report, "status", "no-match")
    assert "workflow start" not in analysis_report["next_command"]

    avoid_local_ai_report = repo_routing.explain_routes(
        tmp,
        "Report benchmark ideas without local AI or model setup; do not start a run",
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )
    assert_field(avoid_local_ai_report, "local_ai_avoidance_request", True)
    assert "local-ai-benchmark-workflow" != avoid_local_ai_report["selected_owner"]
    assert "workflow start" not in avoid_local_ai_report["next_command"]


def test_which_workflow_read_only_request_suppresses_lifecycle_start(tmp):
    write_workflow_routing_fixture(tmp)

    report = repo_routing.explain_routes(
        tmp,
        "read-only inspect workflow routing for a bug ticket, do not start a run",
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )

    assert_field(report, "selected_owner", "bug-ticket-workflow")
    assert_field(report, "read_only_request", True)
    assert_field(
        report,
        "next_command",
        "Report selected workflow `bug-ticket-workflow`; do not start, resume, or finish a retained workflow run.",
    )
    assert "workflow start" not in report["next_command"]
    assert_field(report, "start_command_if_confirmed", "")
    assert_field(report, "start_ready", False)
    assert_field(report, "confirmation_required", False)

    dogfood_report = repo_routing.explain_routes(
        tmp,
        "dogfood the accepted bug ticket workflow in read-only offline mode",
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )

    assert_field(dogfood_report, "selected_owner", "bug-ticket-workflow")
    assert_field(dogfood_report, "read_only_request", True)
    assert dogfood_report["next_commands"][0]["argv"][3:5] == ["workflow", "smoke"]
    assert_has_all(
        dogfood_report["next_commands"][0]["argv"],
        "bug-ticket-workflow",
        "--dry-run",
    )
    assert "workflow start" not in dogfood_report["next_command"]


def test_workflow_strict_read_only_commands_resolve_v3_ids_to_argv(tmp):
    write_json(
        tmp / "automations" / "demo-workflow" / "module.json",
        {
            "schema_version": 3,
            "commands": [
                {
                    "id": "validate",
                    "argv": [
                        "tool",
                        "arg with spaces",
                    ],
                    "timeout_seconds": 300,
                    "working_directory": "repository",
                    "effects": [],
                }
            ],
            "strict_read_only_commands": ["validate"],
        },
    )

    commands = repo_routing.workflow_strict_read_only_commands(
        tmp,
        "demo-workflow",
    )

    assert commands == [
        {
            "id": "validate",
            "argv": ["tool", "arg with spaces"],
            "timeout_seconds": 300,
            "working_directory": "repository",
            "effects": [],
        }
    ]

    next_command, next_commands = repo_routing.read_only_workflow_next_command(
        tmp,
        "demo-workflow",
        {"strict", "offline"},
        "strict offline inspect demo workflow",
    )
    assert next_commands == commands
    assert next_command == '["tool","arg with spaces"]'


def test_which_workflow_strict_dogfood_fix_text_does_not_route_to_bug_ticket(tmp):
    write_workflow_routing_fixture(tmp)

    report = repo_routing.explain_routes(
        tmp,
        "fix strict read-only workflow dogfood next_command ambiguity",
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )

    assert_field(report, "selected_owner", "disciplined-change-workflow")
    assert_field(report, "read_only_request", True)
    assert report["next_commands"][0]["argv"][3:5] == ["workflow", "smoke"]
    assert_has_all(
        report["next_commands"][0]["argv"],
        "disciplined-change-workflow",
        "--dry-run",
    )
    assert "bug-ticket-workflow" not in report["next_command"]


def test_which_workflow_medium_confidence_requires_confirmation_before_start(tmp):
    write_workflow_routing_fixture(tmp)

    report = repo_routing.explain_routes(
        tmp,
        "review feedback",
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )

    assert_field(report, "confidence", "medium")
    assert_field(report, "start_ready", False)
    assert_field(report, "confirmation_required", True)
    assert "workflow start" not in report["next_command"]
    assert_has_all(report["next_command"], "which-workflow", "more specific")
    assert_has_all(report["start_command_if_confirmed"], "workflow start", str(report["selected_owner"]))

    compact = repo_routing.summarize_route_report(report, compact=True)
    assert_field(compact, "start_ready", False)
    assert_field(compact, "confirmation_required", True)
    assert "workflow start" not in compact["next_command"]
    assert_has_all(compact["start_command_if_confirmed"], "workflow start", str(report["selected_owner"]))


def test_which_workflow_exact_roadmap_audit_query_abstains(tmp):
    write_workflow_routing_fixture(tmp)
    query = (
        "what are the best ways to improve onboarding, token usage, determinism and flexibility "
        "for the harness, workflows, skills, etc..."
    )

    report = repo_routing.explain_routes(
        tmp,
        query,
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )

    assert_field(report, "status", "no-match")
    assert_field(report, "selected_owner", "")
    assert_field(report, "confidence", "none")
    assert_field(report, "start_ready", False)
    assert_field(report, "start_command_if_confirmed", "")
    assert_field(report, "next_command", "Open automations/routing.md.")


def test_which_workflow_uses_module_routing_metadata_threshold_and_margin(tmp):
    write_text(tmp / "automations" / "routing.md", "# Workflow Routing\n")
    write_json(
        tmp / "automations" / "orbital-calibration" / "module.json",
        {
            "schema_version": 3,
            "kind": "workflow",
            "id": "orbital-calibration",
            "summary": "Calibrates an orbital telemetry stream.",
            "routing": {
                "terms": ["orbital", "telemetry", "calibration"],
                "threshold": 3,
                "winner_margin": 2,
            },
        },
    )
    write_text(tmp / "automations" / "orbital-calibration" / "WORKFLOW.md", "# Orbital Calibration\n")
    write_json(
        tmp / "automations" / "telemetry-review" / "module.json",
        {
            "schema_version": 3,
            "kind": "workflow",
            "id": "telemetry-review",
            "summary": "Reviews orbital telemetry.",
            "routing": {
                "terms": ["orbital", "telemetry"],
                "threshold": 2,
                "winner_margin": 1,
            },
        },
    )
    write_text(tmp / "automations" / "telemetry-review" / "WORKFLOW.md", "# Telemetry Review\n")

    report = repo_routing.explain_routes(
        tmp,
        "calibration for orbital telemetry",
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )

    assert_field(report, "selected_owner", "orbital-calibration")
    assert_field(report, "confidence", "medium")
    assert_field(report, "start_ready", False)
    assert_field(report, "confirmation_required", True)
    assert_field(report["selected_route"], "threshold", 3)
    assert_field(report["selected_route"], "winner_margin", 2)
    assert_field(report["selected_route"], "score_margin", 1)
    assert "workflow start" not in report["next_command"]


def test_which_workflow_preserves_registry_routing_metadata_when_module_has_none(tmp):
    write_text(tmp / "automations" / "routing.md", "# Workflow Routing\n")
    write_json(
        tmp / "automations" / "registry.json",
        {
            "automations": [
                {
                    "id": "zephyr-calibration",
                    "folder": "automations/zephyr-calibration",
                    "summary": "Registry-owned route metadata.",
                    "routing": {
                        "terms": ["zephyr", "lattice", "calibrate"],
                        "threshold": 3,
                        "winner_margin": 1,
                    },
                }
            ]
        },
    )
    write_json(
        tmp / "automations" / "zephyr-calibration" / "module.json",
        {
            "schema_version": 3,
            "kind": "workflow",
            "id": "zephyr-calibration",
            "summary": "Owned workflow without duplicated routing metadata.",
        },
    )
    write_text(tmp / "automations" / "zephyr-calibration" / "WORKFLOW.md", "# Zephyr Calibration\n")

    report = repo_routing.explain_routes(
        tmp,
        "calibrate the zephyr lattice",
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )

    assert_field(report, "selected_owner", "zephyr-calibration")
    assert_field(report, "confidence", "high")
    assert_field(report, "start_ready", True)
    assert_field(report["selected_route"], "threshold", 3)
    assert_field(report["selected_route"], "winner_margin", 1)


def test_which_workflow_alias_expansion_counts_as_one_query_concept(tmp):
    write_text(tmp / "automations" / "routing.md", "# Workflow Routing\n")
    write_json(
        tmp / "automations" / "ticket-triage" / "module.json",
        {
            "schema_version": 3,
            "kind": "workflow",
            "id": "ticket-triage",
            "summary": "Triages ticket metadata.",
            "routing": {
                "terms": ["ado", "azure-devops", "azure", "ticket", "work-item", "workitem"],
                "threshold": 3,
                "winner_margin": 1,
            },
        },
    )
    write_text(tmp / "automations" / "ticket-triage" / "WORKFLOW.md", "# Ticket Triage\n")

    candidate = repo_routing.route_candidates(tmp, kinds={"workflow"})[0]
    score, matches = repo_routing.score_route(
        repo_routing.tokens("ticket"),
        candidate,
        display_query_terms=repo_routing.display_tokens("ticket"),
        display_query_concepts=repo_routing.query_concepts("ticket"),
    )
    alias_score, alias_matches = repo_routing.score_route(
        repo_routing.tokens("ticket azure-devops"),
        candidate,
        display_query_terms=repo_routing.display_tokens("ticket azure-devops"),
        display_query_concepts=repo_routing.query_concepts("ticket azure-devops"),
    )
    report = repo_routing.explain_routes(
        tmp,
        "ticket",
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )

    assert score == 1, (score, matches)
    assert alias_score == 1, (alias_score, alias_matches)
    assert_field(report, "selected_owner", "")
    assert_field(report, "confidence", "none")
    assert_field(report, "start_ready", False)


def test_which_workflow_scaffold_terms_route_compound_and_separated_name(tmp):
    write_text(tmp / "automations" / "routing.md", "# Workflow Routing\n")
    write_json(
        tmp / "automations" / "compliance-workflow" / "module.json",
        {
            "schema_version": 3,
            "kind": "workflow",
            "id": "compliance-workflow",
            "summary": "Audits dependency evidence deterministically.",
            "routing": {
                "terms": ["compliance", "workflow"],
                "activation_terms": ["compliance-workflow"],
                "threshold": 2,
                "winner_margin": 1,
            },
        },
    )
    write_text(tmp / "automations" / "compliance-workflow" / "WORKFLOW.md", "# Compliance Workflow\n")

    reports = [
        repo_routing.explain_routes(
            tmp,
            query,
            kinds={"workflow"},
            tool_name="skill-manager.which-workflow",
        )
        for query in ("compliance-workflow dependency", "compliance workflow dependency")
    ]

    for report, expected_score in zip(reports, (3, 3), strict=True):
        assert_fields(report, selected_owner="compliance-workflow", confidence="high", start_ready=True)
        assert_field(report["selected_route"], "score", expected_score)


def test_canonical_workflow_registry_drives_high_medium_and_abstention_routes(_tmp):
    root = Path(__file__).resolve().parents[4]
    registry = read_json(root / "automations" / "registry.json")
    entries = registry.get("automations", [])
    assert entries and all(isinstance(entry.get("routing"), dict) for entry in entries), entries

    high = repo_routing.explain_routes(
        root,
        "implement a customer feature with acceptance criteria",
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )
    medium = repo_routing.explain_routes(
        root,
        "dogfood a workflow lifecycle run from a fresh maintainer perspective",
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )
    abstain = repo_routing.explain_routes(
        root,
        (
            "what are the best ways to improve onboarding, token usage, determinism and flexibility "
            "for the harness, workflows, skills, etc..."
        ),
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )

    assert_fields(high, selected_owner="user-story-workflow", confidence="high", start_ready=True)
    assert_field(high["selected_route"], "threshold", 2)
    assert_fields(medium, selected_owner="disciplined-change-workflow", confidence="medium", start_ready=False)
    assert_field(medium, "confirmation_required", True)
    assert_fields(abstain, selected_owner="", confidence="none", start_ready=False)


def test_which_workflow_read_only_diagnostics_do_not_route_to_bug_workflow(tmp):
    write_workflow_routing_fixture(tmp)

    report = repo_routing.explain_routes(
        tmp,
        (
            "Do not edit files. Inspect deterministic workflow/status behavior around current low-context changes. "
            "Run read-only status, check-changed, context-use-check, and finish commands. "
            "Look for contradictions, misleading next_command values, budget regressions, raw-map misuse, "
            "stale generated artifacts, or workflow template inheritance gaps. Report concrete issues with "
            "command/file references and suggested minimal fixes; if none, say no issues found with evidence."
        ),
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )

    assert_field(report, "status", "no-match")
    assert_field(report, "selected_owner", "")
    assert_field(report, "confidence", "none")
    assert_field(report, "next_command", "Open automations/routing.md.")


def test_which_workflow_read_only_skill_review_does_not_route_to_dotnet_workflow(tmp):
    write_workflow_routing_fixture(tmp)

    for query in (
        "read-only offline skill review for dotnet-quality-gates",
        "review dotnet quality gates read-only offline",
        "dogfood dotnet-quality-gates skill under strict read-only offline constraints",
    ):
        report = repo_routing.explain_routes(
            tmp,
            query,
            kinds={"workflow"},
            tool_name="skill-manager.which-workflow",
        )

        assert_field(report, "read_only_request", True)
        assert_field(report, "status", "no-match")
        assert_field(report, "selected_owner", "")
        assert_field(report, "confidence", "none")
        assert_field(report, "next_command", "Open automations/routing.md.")


def test_which_workflow_external_reference_manager_routes_to_reference_refresh(tmp):
    write_workflow_routing_fixture(tmp)
    report_command = (
        "python -B .agents/manage.py reference-refresh --mode report --format markdown"
    )
    dry_run_command = (
        "python -B .agents/manage.py reference-refresh --mode dry-run --no-fetch --format json"
    )
    write_json(
        tmp / "automations" / "reference-refresh" / "module.json",
        {
            "schema_version": 3,
            "commands": [
                {
                    "id": "reference-report",
                    "argv": report_command.split(),
                    "timeout_seconds": 300,
                    "working_directory": "repository",
                    "effects": [],
                },
                {
                    "id": "reference-dry-run",
                    "argv": dry_run_command.split(),
                    "timeout_seconds": 300,
                    "working_directory": "repository",
                    "effects": [],
                },
            ],
            "strict_read_only_commands": [
                "reference-report",
                "reference-dry-run",
            ]
        },
    )

    report = repo_routing.explain_routes(
        tmp,
        "Dogfood external-reference-manager under strict read-only offline constraints",
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )

    assert_field(report, "selected_owner", "reference-refresh")
    assert_field(report, "read_only_request", True)
    assert "candidate-import-workflow" != report["selected_owner"]
    assert report["next_command"] == json.dumps(report_command.split(), separators=(",", ":"))
    assert_lacks_all(report["next_command"], "workflow smoke")
    assert report["next_commands"][0]["argv"] == report_command.split()
    assert report["next_commands"][1]["argv"] == dry_run_command.split()


def test_workflow_context_audit_infers_workflow_name_from_unique_run_id(tmp):
    write_json(
        tmp / "automations" / "agent-benchmarking" / "runs" / "run-a" / "run.json",
        {"run_id": "run-a", "workflow": "agent-benchmarking"},
    )

    assert (
        repo_doctor_groups.infer_workflow_name_for_run_id(tmp, "run-a")
        == "agent-benchmarking"
    )

    write_json(
        tmp / "automations" / "local-ai-benchmark-workflow" / "runs" / "run-a" / "run.json",
        {"run_id": "run-a", "workflow": "local-ai-benchmark-workflow"},
    )
    try:
        repo_doctor_groups.infer_workflow_name_for_run_id(tmp, "run-a")
    except SystemExit as exc:
        assert "ambiguous" in str(exc)
    else:
        raise AssertionError("expected ambiguous run id to fail")


def write_workflow_routing_fixture(root):
    write_text(
        root / "automations" / "routing.md",
        """# Workflow Routing

| Workflow | Use When | Open | Contract |
|---|---|---|---|
| `bug-ticket-workflow` | Bug workflow for intake, reproduction, approved fix, validation, and PR handoff. | `automations/bug-ticket-workflow/WORKFLOW.md` | `automations/bug-ticket-workflow/module.json` |
| `candidate-import-workflow` | Reviews candidate skill, script, or workflow sources from temporary or staged folders; classifies take, rewrite, or reject decisions; rewrites useful behavior into accepted owners; validates; and cleans only reviewed candidate paths. | `automations/candidate-import-workflow/WORKFLOW.md` | `automations/candidate-import-workflow/module.json` |
| `local-ai-benchmark-workflow` | Coordinates local AI model/runtime benchmarks, including GGUF, llama-server, speculative MTP/n-gram code-generation, embedding, repository-search, and vision evidence. | `automations/local-ai-benchmark-workflow/WORKFLOW.md` | `automations/local-ai-benchmark-workflow/module.json` |
| `user-story-workflow` | Story workflow for intake, planning, approved implementation, validation, follow-up, and PR handoff evidence. | `automations/user-story-workflow/WORKFLOW.md` | `automations/user-story-workflow/module.json` |
| `feedback-improvement-workflow` | Reviews local failure feedback and writes an improvement action plan. | `automations/feedback-improvement-workflow/WORKFLOW.md` | `automations/feedback-improvement-workflow/module.json` |
| `disciplined-change-workflow` | Guides larger repo changes and workflow lifecycle dogfood through owner selection, scoped planning, evidence-led execution, review, and fresh validation. | `automations/disciplined-change-workflow/WORKFLOW.md` | `automations/disciplined-change-workflow/module.json` |
| `diagram-review-workflow` | Coordinates Azure DevOps Mermaid diagram review, materialization checks, and validation evidence. | `automations/diagram-review-workflow/WORKFLOW.md` | `automations/diagram-review-workflow/module.json` |
| `dotnet-framework-migration` | Guides .NET Framework migrations with inventory, compatibility assessment, validation, rollback, and handoff. | `automations/dotnet-framework-migration/WORKFLOW.md` | `automations/dotnet-framework-migration/module.json` |
| `dotnet-upgrade` | Guides .NET upgrades with baseline evidence, Microsoft notes, NuGet resolution, validation, and rollback. | `automations/dotnet-upgrade/WORKFLOW.md` | `automations/dotnet-upgrade/module.json` |
| `navigation` | Generates deterministic repository navigation maps, handoff capsules, project context drafts, and staleness evidence. | `automations/navigation/WORKFLOW.md` | `automations/navigation/module.json` |
| `reference-refresh` | Refreshes external Git references from manifests, pins commits, writes compact cards, and records no-fetch/offline evidence. | `automations/reference-refresh/WORKFLOW.md` | `automations/reference-refresh/module.json` |
""",
    )
    write_text(
        root / ".agents" / "routing.md",
        """# Skill Routing Index

| Category | Skill | Use When | Open |
|---|---|---|---|
| Ticket And Intake | `azure-devops-ticket-intake` | Importing Azure DevOps REST API or TFS story, bug, task, feature, or epic tickets into local workflow run folders with comments and attachments. | `.agents/skills/azure-devops-ticket-intake/SKILL.md` |
| Documentation | `workflow-manager` | Creating, reviewing, validating, routing, or resuming automations and workflow modules with module.json contracts. | `.agents/skills/workflow-manager/SKILL.md` |
""",
    )


def write_workflow_module_routing_fixture(root, name, summary, routing):
    write_json(
        root / "automations" / name / "module.json",
        {
            "schema_version": 3,
            "kind": "workflow",
            "id": name,
            "version": "1.0.0",
            "summary": summary,
            "routing": routing,
        },
    )


def test_which_workflow_regression_suite_fixture_gates_selection_confidence_and_next_command(tmp):
    write_workflow_routing_fixture(tmp)
    suite_path = tmp / "routing" / "workflow-routing-suite.json"
    write_json(
        suite_path,
        {
            "cases": [
                {
                    "id": "ado-story-implementation",
                    "query": "implement Azure DevOps user story 123",
                    "expected_owner": "user-story-workflow",
                    "expected_confidence": "high",
                    "expected_next_command": "python -B .agents/manage.py workflow start --name user-story-workflow --summary --compact --format json",
                },
                {
                    "id": "feature-request",
                    "query": "build a new API endpoint",
                    "expected_owner": "user-story-workflow",
                    "expected_confidence": "high",
                    "expected_next_command": "python -B .agents/manage.py workflow start --name user-story-workflow --summary --compact --format json",
                },
                {
                    "id": "bug-repro",
                    "query": "fix a bug ticket with reproduction and regression proof",
                    "expected_owner": "bug-ticket-workflow",
                    "expected_confidence": "high",
                    "expected_next_command": "python -B .agents/manage.py workflow start --name bug-ticket-workflow --summary --compact --format json",
                },
                {
                    "id": "import-only",
                    "query": "import Azure DevOps work item attachments",
                    "expected_owner": "",
                    "expected_confidence": "none",
                    "expected_next_command": "Open automations/routing.md.",
                },
                {
                    "id": "read-only-ranked-improvements",
                    "query": "Search the repo for cheaper maintenance improvements and report ranked ideas only; do not edit files.",
                    "expected_owner": "",
                    "expected_confidence": "none",
                    "expected_next_command": "Open automations/routing.md.",
                },
                {
                    "id": "read-only-next-feature-analysis",
                    "query": "What would the next best improvements or features be?",
                    "expected_owner": "",
                    "expected_confidence": "none",
                    "expected_next_command": "Open automations/routing.md.",
                },
                {
                    "id": "navigation-refresh-stale-maps",
                    "query": "refresh stale repository navigation maps and handoff evidence",
                    "expected_owner": "navigation",
                    "expected_confidence": "high",
                    "expected_next_command": "python -B .agents/manage.py workflow start --name navigation --summary --compact --format json",
                },
                {
                    "id": "reference-refresh-no-fetch",
                    "query": "refresh local external references from pinned manifests and write evidence without network fetch",
                    "expected_owner": "reference-refresh",
                    "expected_confidence": "high",
                    "expected_next_command": "python -B .agents/manage.py workflow start --name reference-refresh --summary --compact --format json",
                },
                {
                    "id": "read-only-lifecycle-dogfood",
                    "query": "read-only dogfood a workflow lifecycle run and explain how it works from a fresh maintainer perspective",
                    "expected_owner": "disciplined-change-workflow",
                    "expected_confidence": "medium",
                    "expected_next_command": '["python","-B",".agents/manage.py","workflow","smoke","--name","disciplined-change-workflow","--dry-run","--summary","--compact","--format","json"]',
                },
                {
                    "id": "candidate-import-dogfood",
                    "query": "read-only dogfood candidate import workflow",
                    "expected_owner": "candidate-import-workflow",
                    "expected_confidence": "high",
                    "expected_next_command": '["python","-B",".agents/manage.py","workflow","smoke","--name","candidate-import-workflow","--dry-run","--summary","--compact","--format","json"]',
                },
                {
                    "id": "candidate-import-temp-folder",
                    "query": "import candidate skills from a temporary folder, rewrite accepted behavior, validate, and clean reviewed candidates",
                    "expected_owner": "candidate-import-workflow",
                    "expected_confidence": "high",
                    "expected_next_command": "python -B .agents/manage.py workflow start --name candidate-import-workflow --summary --compact --format json",
                },
                {
                    "id": "diagram-review-dogfood",
                    "query": "read-only dogfood diagram review workflow",
                    "expected_owner": "diagram-review-workflow",
                    "expected_confidence": "high",
                    "expected_next_command": '["python","-B",".agents/manage.py","workflow","smoke","--name","diagram-review-workflow","--dry-run","--summary","--compact","--format","json"]',
                },
                {
                    "id": "diagram-review-mermaid-evidence",
                    "query": "review Azure DevOps Mermaid diagrams and collect validation evidence",
                    "expected_owner": "diagram-review-workflow",
                    "expected_confidence": "high",
                    "expected_next_command": "python -B .agents/manage.py workflow start --name diagram-review-workflow --summary --compact --format json",
                },
                {
                    "id": "dotnet-framework-binding-redirects",
                    "query": "migrate a .NET Framework app with binding redirects and validation evidence",
                    "expected_owner": "dotnet-framework-migration",
                    "expected_confidence": "high",
                    "expected_next_command": "python -B .agents/manage.py workflow start --name dotnet-framework-migration --summary --compact --format json",
                },
                {
                    "id": "dotnet-framework-dogfood",
                    "query": "read-only dogfood dotnet framework migration workflow",
                    "expected_owner": "dotnet-framework-migration",
                    "expected_confidence": "high",
                    "expected_next_command": '["python","-B",".agents/manage.py","workflow","smoke","--name","dotnet-framework-migration","--dry-run","--summary","--compact","--format","json"]',
                },
                {
                    "id": "dotnet-upgrade-modern-target",
                    "query": "upgrade a .NET app from net6.0 to net8.0 with package resolution and validation evidence",
                    "expected_owner": "dotnet-upgrade",
                    "expected_confidence": "high",
                    "expected_next_command": "python -B .agents/manage.py workflow start --name dotnet-upgrade --summary --compact --format json",
                },
                {
                    "id": "dotnet-upgrade-dogfood",
                    "query": "read-only dogfood dotnet upgrade workflow",
                    "expected_owner": "dotnet-upgrade",
                    "expected_confidence": "high",
                    "expected_next_command": '["python","-B",".agents/manage.py","workflow","smoke","--name","dotnet-upgrade","--dry-run","--summary","--compact","--format","json"]',
                },
                {
                    "id": "feedback-improvement-action-plan",
                    "query": "review local failure feedback and write an improvement action plan",
                    "expected_owner": "feedback-improvement-workflow",
                    "expected_confidence": "high",
                    "expected_next_command": "python -B .agents/manage.py workflow start --name feedback-improvement-workflow --summary --compact --format json",
                },
                {
                    "id": "feedback-improvement-dogfood",
                    "query": "read-only dogfood feedback improvement workflow",
                    "expected_owner": "feedback-improvement-workflow",
                    "expected_confidence": "high",
                    "expected_next_command": '["python","-B",".agents/manage.py","workflow","smoke","--name","feedback-improvement-workflow","--dry-run","--summary","--compact","--format","json"]',
                },
            ]
        },
    )

    report = repo_routing.workflow_routing_regression_suite_report(tmp, suite_path)

    assert_ok(report)
    assert_fields(report["summary"], case_count=19, failed_count=0)
    assert {case["ok"] for case in report["cases"]} == {True}


def test_workflow_start_from_request_routes_high_confidence_request_without_user_knowing_name(tmp):
    write_workflow_routing_fixture(tmp)
    calls = []

    def fake_run_workflow_repo_manager(_root, command):
        calls.append(command)
        return 0

    args = Namespace(
        workflow_args=[
            "start",
            "--from-request",
            "implement Azure DevOps user story 123",
            "--run-id",
            "story-123",
            "--summary",
            "--compact",
            "--format",
            "json",
        ]
    )
    with patched_attrs(repo_doctor_groups.repo, run_workflow_repo_manager=fake_run_workflow_repo_manager):
        status = repo_doctor_groups.workflow_group(args, tmp)

    assert status == 0
    assert calls == [
        [
            "start-run",
            "--root",
            str(tmp),
            "--name",
            "user-story-workflow",
            "--format",
            "json",
            "--run-id",
            "story-123",
            "--profile",
            "default",
            "--from-request",
            "implement Azure DevOps user story 123",
            "--summary",
            "--compact",
        ]
    ]


def test_workflow_start_from_request_honors_metadata_intents_threshold_and_winner_margin(tmp):
    write_workflow_routing_fixture(tmp)
    write_workflow_module_routing_fixture(
        tmp,
        "bug-ticket-workflow",
        "Bug intake, reproduction, approved fix, and validation.",
        {
            "activation_terms": ["bug", "defect", "crash", "reproduce", "root-cause"],
            "terms": ["bug", "defect", "crash", "reproduce", "fix", "root-cause"],
            "threshold": 2,
            "winner_margin": 1,
        },
    )
    write_workflow_module_routing_fixture(
        tmp,
        "dotnet-upgrade",
        "Guides .NET upgrades with package compatibility and rollback evidence.",
        {
            "activation_terms": ["dotnet", "net", "nuget", "target-framework"],
            "terms": ["dotnet", "net", "upgrade", "nuget", "package", "compatibility"],
            "threshold": 2,
            "winner_margin": 1,
        },
    )
    write_workflow_module_routing_fixture(
        tmp,
        "dotnet-framework-migration",
        "Guides .NET Framework migrations and compatibility assessment.",
        {
            "activation_terms": ["dotnet", "net", "framework", "legacy"],
            "terms": ["dotnet", "net", "framework", "migration", "compatibility"],
            "threshold": 3,
            "winner_margin": 1,
        },
    )
    write_workflow_module_routing_fixture(
        tmp,
        "navigation",
        "Refresh deterministic repository navigation maps and handoff evidence.",
        {
            "activation_terms": ["navigation", "project-context", "staleness"],
            "terms": ["navigation", "map", "maps", "handoff", "stale", "refresh"],
            "threshold": 2,
            "winner_margin": 1,
        },
    )
    write_workflow_module_routing_fixture(
        tmp,
        "reference-refresh",
        "Refresh pinned external Git references and manifest evidence.",
        {
            "activation_terms": ["reference", "references", "reference-refresh", "external-reference-manager"],
            "terms": ["reference", "repository", "manifest", "pin", "refresh", "fetch"],
            "threshold": 2,
            "winner_margin": 1,
        },
    )

    compatibility = repo_doctor_groups.workflow_start_from_request_report(
        tmp,
        "fix dotnet package compatibility",
    )
    navigation = repo_doctor_groups.workflow_start_from_request_report(
        tmp,
        "refresh stale repository navigation maps and handoff evidence",
    )

    assert_ok(compatibility)
    assert_status(compatibility, "ready")
    assert_field(compatibility, "selected_owner", "dotnet-upgrade")
    assert_fields(
        compatibility["selected_route"],
        threshold=2,
        winner_margin=1,
        score_margin=1,
    )
    assert_ok(navigation)
    assert_status(navigation, "ready")
    assert_field(navigation, "selected_owner", "navigation")


def test_workflow_route_specific_activation_anchor_counts_toward_threshold(tmp):
    write_workflow_module_routing_fixture(
        tmp,
        "treadmillrunner-delivery",
        "Deliver scoped TreadmillRunner changes.",
        {
            "activation_terms": ["treadmillrunner-delivery", "treadmillrunner"],
            "terms": ["treadmillrunner", "delivery", "protocol evidence"],
            "threshold": 2,
            "winner_margin": 1,
        },
    )

    report = repo_doctor_groups.workflow_start_from_request_report(
        tmp,
        "implement approved TR-004 for TreadmillRunner",
    )

    assert_ok(report)
    assert_status(report, "ready")
    assert_field(report, "selected_owner", "treadmillrunner-delivery")
    assert_field(report["selected_route"], "score", 2)


def test_workflow_start_from_request_rejects_ambiguous_or_low_confidence_without_creating_run(tmp):
    calls = []
    args = Namespace(workflow_args=["start", "--from-request", "do the ticket", "--format", "json"])
    medium_route = {
        "selected_route": {
            "kind": "workflow",
            "name": "user-story-workflow",
            "score": 2,
            "threshold": 3,
            "winner_margin": 1,
            "score_margin": 2,
        },
        "selected_owner": "user-story-workflow",
        "routes": [],
        "confidence": "medium",
        "start_ready": False,
        "confirmation_required": True,
    }

    with patched_attrs(repo_doctor_groups.repo, run_workflow_repo_manager=lambda _root, command: calls.append(command) or 0):
        with patched_attrs(repo_doctor_groups.repo_routing, explain_routes=lambda *_args, **_kwargs: medium_route):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                status = repo_doctor_groups.workflow_group(args, tmp)

    report = json.loads(stdout.getvalue())
    assert status == 1
    assert calls == []
    assert_not_ok(report)
    assert_fields(report, tool="workflow-manager.start-from-request", status="ambiguous")
    assert_has_all(report["next_command"], "which-workflow")


def test_route_prefers_story_workflow_for_feature_work_and_intake_for_imports(tmp):
    write_text(
        tmp / ".agents" / "routing.md",
        """# Skill Routing Index

| Category | Skill | Use When | Open |
|---|---|---|---|
| Intake | `azure-devops-ticket-intake` | Importing Azure DevOps REST API or TFS story, bug, task, feature, or epic tickets into local workflow run folders with comments and attachments. | `.agents/skills/azure-devops-ticket-intake/SKILL.md` |
| Documentation | `workflow-manager` | Creating, reviewing, validating, routing, or resuming automations and workflow modules with module.json contracts. | `.agents/skills/workflow-manager/SKILL.md` |
""",
    )
    write_text(
        tmp / "automations" / "routing.md",
        """# Workflow Routing

| Workflow | Use When | Open | Contract |
|---|---|---|---|
| `bug-ticket-workflow` | Bug workflow for intake, reproduction, approved fix, validation, and PR handoff. | `automations/bug-ticket-workflow/WORKFLOW.md` | `automations/bug-ticket-workflow/module.json` |
| `user-story-workflow` | Story workflow for intake, planning, approved implementation, validation, follow-up, and PR handoff evidence. | `automations/user-story-workflow/WORKFLOW.md` | `automations/user-story-workflow/module.json` |
""",
    )

    story_route = repo_routing.explain_routes(tmp, "implement Azure DevOps user story 123")
    assert_field(story_route, "selected_owner", "user-story-workflow")

    feature_route = repo_routing.explain_routes(tmp, "add a new checkout discount feature")
    assert_field(feature_route, "selected_owner", "user-story-workflow")

    intake_route = repo_routing.explain_routes(tmp, "import Azure DevOps work item attachments")
    assert_field(intake_route, "selected_owner", "azure-devops-ticket-intake")


def test_route_requires_specific_diagram_context_for_azure_mermaid(tmp):
    write_text(
        tmp / ".agents" / "routing.md",
        """# Skill Routing Index

| Category | Skill | Use When | Open |
|---|---|---|---|
| Documentation And Diagrams | `mermaid-diagrams-azure-devops` | Creating, editing, normalizing, or validating Azure DevOps-compatible Mermaid diagrams in Markdown or wiki documentation | `.agents/skills/mermaid-diagrams-azure-devops/SKILL.md` |
| Documentation And Diagrams | `project-context-generator` | Generating or refreshing workflow-ready project context files from an existing project. | `.agents/skills/project-context-generator/SKILL.md` |
| Documentation And Diagrams | `repo-navigation` | Orienting in a repository, building compact repo briefs, or installing and refreshing project-local navigation maps. | `.agents/skills/repo-navigation/SKILL.md` |
""",
    )
    write_text(
        tmp / "automations" / "routing.md",
        """# Workflow Routing

| Workflow | Use When | Open | Contract |
|---|---|---|---|
| `diagram-review-workflow` | Coordinates Azure DevOps Mermaid diagram review, materialization checks, and validation evidence. | `automations/diagram-review-workflow/WORKFLOW.md` | `automations/diagram-review-workflow/module.json` |
""",
    )

    generic_report = repo_routing.explain_routes(tmp, "make a diagram")

    assert_field(generic_report, "status", "no-match")
    assert_field(generic_report, "selected_owner", "")

    qualified_report = repo_routing.explain_routes(
        tmp,
        "make this Mermaid diagram render correctly in Azure DevOps wiki",
    )

    assert_field(qualified_report, "status", "matched")
    assert_field(qualified_report, "selected_owner", "mermaid-diagrams-azure-devops")
    matched_terms = qualified_report["selected_route"]["matched_terms"]
    assert "azure" in matched_terms
    assert "devops" in matched_terms
    assert "mermaid" in matched_terms
    assert "ticket" not in matched_terms
    assert "devop" not in matched_terms

    workflow_report = repo_routing.explain_routes(tmp, "diagram review workflow")

    assert_field(workflow_report, "status", "matched")
    assert_field(workflow_report, "selected_owner", "diagram-review-workflow")


def test_route_rejects_generic_automation_without_specific_context(tmp):
    write_text(
        tmp / ".agents" / "routing.md",
        """# Skill Routing Index

| Category | Skill | Use When | Open |
|---|---|---|---|
| Documentation And Diagrams | `workflow-manager` | Creating, reviewing, validating, routing, or resuming automations and workflow modules with module.json contracts. | `.agents/skills/workflow-manager/SKILL.md` |
| Web And Browser Automation | `playwright-integration` | Checking, preparing, or reporting Playwright browser-test readiness for a local project without making browser setup part of another quality-gate skill. | `.agents/skills/playwright-integration/SKILL.md` |
""",
    )
    write_text(
        tmp / "automations" / "routing.md",
        """# Workflow Routing

| Workflow | Use When | Open | Contract |
|---|---|---|---|
| `disciplined-change-workflow` | Guides larger repo changes through owner selection, scoped planning, evidence-led execution, review, and fresh validation. | `automations/disciplined-change-workflow/WORKFLOW.md` | `automations/disciplined-change-workflow/module.json` |
""",
    )

    generic_report = repo_routing.explain_routes(tmp, "random vague automation")

    assert_field(generic_report, "status", "no-match")
    assert_field(generic_report, "selected_owner", "")

    test_automation_report = repo_routing.explain_routes(tmp, "test automation")

    assert_field(test_automation_report, "status", "no-match")
    assert_field(test_automation_report, "selected_owner", "")

    browser_report = repo_routing.explain_routes(tmp, "browser automation")

    assert_field(browser_report, "status", "matched")
    assert_field(browser_report, "selected_owner", "playwright-integration")

    workflow_report = repo_routing.explain_routes(tmp, "workflow automation module contract")

    assert_field(workflow_report, "status", "matched")
    assert_field(workflow_report, "selected_owner", "workflow-manager")


def test_inspect_skill_summary_is_compact(tmp):
    write_skill(tmp)
    report = inspect_skill.build_report(skill_root(tmp), tmp, fast=True)
    compact = inspect_skill.summarize_report(report, compact=True)

    assert_field(compact, "format", "skill-inspection-summary")
    assert_name(compact["skill"], "demo-skill")
    assert_field(compact["validation"], "error_count", 0)
    assert_keys_lack(compact["skill"], "summary")
    assert_keys_lack(compact["validation"], "errors")
    assert_has_all(compact["validation"], "warnings")
    assert_keys_lack(compact, "recommended_next_steps", "analysis", "inventory", "attestation")


def test_inspect_skill_recommends_read_only_sync_checks_for_dogfood(tmp):
    write_skill(tmp)
    report = inspect_skill.build_report(skill_root(tmp), tmp, fast=True)

    recommendations = report["recommended_next_steps"]

    assert_contains(recommendations, "Run: python -B .agents/manage.py sync-skill-routing --check")
    assert_contains(recommendations, "Run: python -B .agents/manage.py sync-claude-skills --check")
    assert_contains(recommendations, "strict no-write dogfood")
    assert "Run: python -B .agents/manage.py sync-skill-routing" not in recommendations
    assert "Run: python -B .agents/manage.py sync-claude-skills" not in recommendations


def test_claude_adapter_budget_reports_name_only_savings(tmp):
    write_skill(tmp, "demo-skill")
    skill_dir = skill_root(tmp, "demo-skill")
    adapter = tmp / ".claude" / "skills" / "demo-skill" / "SKILL.md"
    write_text(adapter, repo_generated.generated_claude_adapter(tmp, skill_dir))

    report = repo_generated.claude_adapter_budget_report(tmp)

    assert_ok(report)
    assert_summary(report, adapter_count=1, worth_considering_name_only=False)
    assert report["summary"]["estimated_saved_tokens"] > 0
    assert_empty(report["stale_adapters"])


def test_module_schema_sync_is_owner_generated_and_checkable(tmp):
    target = (
        tmp
        / ".agents"
        / "skills"
        / "skill-manager"
        / "assets"
        / "schemas"
        / "module.schema.json"
    )

    assert repo_generated.sync_module_schema(tmp, check=False) == 0
    assert target.exists()
    assert repo_generated.sync_module_schema(tmp, check=True) == 0
    write_text(target, "{}\n")
    try:
        repo_generated.sync_module_schema(tmp, check=True)
    except SystemExit as exc:
        assert "module schema is missing or stale" in str(exc)
    else:
        raise AssertionError("stale module schema check unexpectedly passed")


def test_review_skill_summary_is_compact(tmp):
    _ = tmp
    report = {
        "schema_version": 1,
        "tool": "skill-manager.review-skill",
        "ok": True,
        "skill": ".agents/skills/demo-skill",
        "validation": {"errors": [], "warnings": ["warn"]},
        "inspect": {
            "mode": "fast",
            "analysis": {"files_scanned": 4, "evidence_count": 1},
            "context_budget_impact": {"skill_md_words": 100, "skill_md_status": "ok", "routing_load_words": 120},
            "recommended_next_steps": [f"Run: {REPO_CHECK_COMMAND}"],
        },
        "budget": {"skill_md": {"words": 100, "status": "ok"}, "total_text": {"words": 200}},
        "inventory": {"dependencies": ["python-stdlib"], "risk": {"profile": "read-only", "declared_flags": []}},
        "failures": [],
        "implementation_packet": {"likely_files": ["SKILL.md"], "expected_checks": ["check"]},
    }
    compact = review_skill_command.summarize_review_report(report, compact=True)

    assert_fields(compact, tool="skill-manager.review-skill-summary", warning_count=1)
    assert_field(compact["implementation_packet"], "likely_file_count", 1)
    assert_keys_lack(compact["risk"], "declared_flags")
    assert_keys_lack(compact, "failures", "inspect", "budget", "inventory")


def test_skill_inventory_summary_is_compact(tmp):
    write_skill(tmp)
    report = skill_inventory.build_report(Namespace(root=str(tmp), all=True, skill=None))
    compact = skill_inventory.summarize_report(report, compact=True)

    assert_summary(compact, skill_count=1, duplicate_trigger_group_count=0)
    assert_keys_lack(compact, "skills", "root", "duplicate_trigger_groups")
    assert_name(compact["top_by_text"][0], "demo-skill")
    assert_keys_lack(compact["top_by_text"][0], "path")
    assert compact["architecture_recommendations"][0]["decision"] == "rework-existing-skills"
    assert compact["architecture_recommendations"][0]["id"] == "keep-topology"


def test_skill_inventory_recommends_combining_duplicate_triggers(tmp):
    write_skill(tmp, "demo-alpha")
    write_skill(tmp, "demo-beta")
    for name in ("demo-alpha", "demo-beta"):
        write_text(
            skill_md(skill_root(tmp, name)),
            f"""---
name: {name}
description: Use when reviewing demo services.
---

# {name}

## Workflow

Validate.
""",
        )

    report = skill_inventory.build_report(Namespace(root=str(tmp), all=True, skill=None))
    compact = skill_inventory.summarize_report(report, compact=True)

    assert_summary(compact, duplicate_trigger_group_count=1)
    assert compact["architecture_recommendations"][0]["decision"] == "combine-or-merge"
    assert compact["architecture_recommendations"][0]["id"] == "resolve-duplicate-triggers"


def test_skill_inventory_recommends_tool_hotspot_refactor(tmp):
    write_skill(tmp, "large-tool")
    large_test_runner = skill_root(tmp, "large-tool") / "scripts" / "run_self_tests.py"
    write_text(large_test_runner, "\n".join("def test_case(): pass" for _ in range(11000)))

    report = skill_inventory.build_report(Namespace(root=str(tmp), all=True, skill=None))
    compact = skill_inventory.summarize_report(report, compact=True)

    recommendations = compact["architecture_recommendations"]
    assert any(item["id"] == "split-tool-hotspots" for item in recommendations)


def test_skill_inventory_summary_markdown_renders_recommendations(tmp):
    write_skill(tmp)
    report = skill_inventory.build_report(Namespace(root=str(tmp), all=True, skill=None))
    compact = skill_inventory.summarize_report(report, compact=True)

    rendered = skill_inventory.render_markdown(compact)

    assert "# Skill Inventory Summary" in rendered
    assert "Architecture Recommendations" in rendered
    assert "rework-existing-skills" in rendered


def test_skill_inventory_noncompact_summary_markdown_keeps_skill_rows(tmp):
    write_skill(tmp)
    report = skill_inventory.build_report(Namespace(root=str(tmp), all=True, skill=None))
    summary = skill_inventory.summarize_report(report, compact=False)

    rendered = skill_inventory.render_markdown(summary)

    assert "| Skill | Risk | Components | Dependencies | SKILL.md | Total Text |" in rendered
    assert "| `demo-skill` |" in rendered


def test_audit_skill_determinism_summary_is_compact(tmp):
    write_skill(tmp)
    report = audit_skill_determinism.build_report(tmp, all_skills=True)
    compact = audit_skill_determinism.summarize_report(report, compact=True)

    assert_field(compact["summary"], "skills_checked", 1)
    assert compact["summary"]["issue_count"] >= 1
    assert_name(compact["skills"][0], "demo-skill")
    assert_field(compact["issues"][0], "skill", "demo-skill")
    assert all(set(row) <= {"name", "issues", "warnings"} for row in compact["skills"])
    clean = audit_skill_determinism.summarize_report(
        {
            "schema_version": 1,
            "tool": "skill-manager.audit-skill-determinism",
            "ok": True,
            "status": "passed",
            "summary": {"skills_checked": 1, "issue_count": 0, "warning_count": 0},
            "skills": [{"name": "demo-skill", "issues": [], "warnings": []}],
        },
        compact=True,
    )
    assert_keys_lack(clean, "issues", "warnings", "skills")


def test_candidate_source_audit_reports_reference_and_similarity_risk(tmp):
    candidate = tmp / "candidate"
    write_candidate_skill(
        candidate,
        "dotnet-api",
        "Use when reviewing C# async service code for dependency injection and concurrency boundaries.",
        "Dotnet API",
        """## Scope

Use with [skill:dotnet-engineering] and [skill:missing-skill].

## Out Of Scope

- UI work.""",
    )
    write_candidate_skill(
        candidate,
        "dotnet-engineering",
        "Use when reviewing C# asynchronous service code for dependency injection and concurrency seams.",
        "Dotnet Testing",
        """## Scope

Use with [skill:dotnet-api].

## Out Of Scope

- Deployment work.""",
    )
    write_text(
        candidate / "agents" / "dotnet-reviewer.md",
        """---
name: dotnet-reviewer
description: >
  Reviews .NET service code and routes findings to skills.
---

Use [skill:dotnet-api] for endpoint work.
""",
    )

    report = candidate_source_audit.build_report(
        candidate,
        warn_threshold=0.4,
        error_threshold=0.55,
    )

    assert_summary(report, skill_count=2, agent_count=1, unresolved_reference_count=1, cycle_count=1)
    assert report["summary"]["high_similarity_pair_count"] >= 1
    assert_contains(report["issues"], "missing-skill")


def test_candidate_source_audit_compact_summary_omits_large_rows(tmp):
    candidate = tmp / "candidate"
    write_candidate_skill(candidate, "demo", "Use when testing compact source audit output.", "Demo")

    report = candidate_source_audit.build_report(candidate)
    summary = candidate_source_audit.summarize_report(report, compact=False)
    compact = candidate_source_audit.summarize_report(report, compact=True)

    assert_summary(summary, skill_count=1)
    assert_keys_lack(summary, "items", "similarity_pairs")
    assert_summary(compact, skill_count=1, issue_count=0)
    assert_keys_lack(compact, "items", "similarity_pairs")


def test_addition_acceptance_requires_skill_contract(tmp):
    write_text(
        skill_md(skill_root(tmp, "new-skill")),
        """---
name: new-skill
description: Use when testing addition acceptance for skill contracts.
---

# New Skill
""",
    )

    report = addition_report(tmp, ".agents/skills/new-skill/SKILL.md")

    assert_not_ok(report)
    assert_contains_all(report["issues"], "skill-manager", "missing module.json")


def test_addition_acceptance_requires_workflow_contract(tmp):
    write_text(
        automation_path(tmp, "new-flow", "WORKFLOW.md"),
        "# New Flow\n\nRead `module.json` before work.",
    )

    report = addition_report(tmp, "automations/new-flow/WORKFLOW.md")

    assert_not_ok(report)
    assert_contains_all(report["issues"], "workflow-manager", "missing module.json")


def test_addition_acceptance_allows_global_workflow_hooks_config(tmp):
    write_json(
        automation_path(tmp, "hooks.json"),
        {
            "schema_version": 1,
            "hooks": [],
        },
    )

    report = addition_report(tmp, "automations/hooks.json")

    assert_ok(report)
    assert_summary(report, workflows_checked=0)


def test_addition_acceptance_rejects_generated_without_source(tmp):
    write_text(agent_path(tmp, "routing.md"), GENERATED_MARKER)

    report = addition_report(tmp, ".agents/routing.md")

    assert_not_ok(report)
    assert_contains_all(report["issues"], "generated-without-source", ".agents/routing.md")


def test_addition_acceptance_accepts_skill_source_with_generated_sync(tmp):
    write_skill(tmp)
    write_text(agent_path(tmp, "routing.md"), GENERATED_MARKER)

    report = addition_report(
        tmp,
        [
            ".agents/skills/demo-skill/SKILL.md",
            ".agents/skills/demo-skill/module.json",
            ".agents/skills/demo-skill/suites/demo-evals.json",
            ".agents/routing.md",
        ],
    )

    assert_ok(report)
    assert_summary(report, skills_checked=1, generated_files=1)


def test_addition_acceptance_accepts_changed_claude_adapter_generator(tmp):
    write_skill(tmp, "skill-manager")
    generated_adapter = ".claude/skills/example/SKILL.md"
    generator_source = (
        ".agents/skills/skill-manager/scripts/repo_support/repo_generated.py"
    )
    write_text(tmp / generated_adapter, GENERATED_MARKER)
    write_text(tmp / generator_source, "def generated_claude_adapter():\n    pass\n")

    report = addition_report(tmp, [generator_source, generated_adapter])

    assert_ok(report)
    assert_summary(report, skills_checked=1, generated_files=1)


def test_addition_acceptance_rejects_unowned_new_file(tmp):
    write_text(tmp / "random.txt", "unowned")

    report = addition_report(tmp, "random.txt")

    assert_not_ok(report)
    assert_contains_all(report["issues"], "unowned-new-file", "random.txt")


def test_addition_acceptance_accepts_root_orchestration_contract(tmp):
    write_text(tmp / "orchestration.md", "# Orchestration\n\nPortable task routing contract.\n")

    report = addition_report(tmp, "orchestration.md")

    assert_ok(report)


def test_addition_acceptance_ignores_consumer_owned_paths_after_install(tmp):
    write_json(
        agent_path(tmp, "harness.lock.json"),
        {
            "schema_version": 1,
            "files": [
                {"path": "AGENTS.md", "sha256": "0" * 64},
                {"path": ".agents/manage.py", "sha256": "1" * 64},
            ],
        },
    )

    paths = [
        "package.json",
        "pyproject.toml",
        "src/ConsumerApp/ConsumerApp.csproj",
        "src/ConsumerApp/Program.cs",
        "tests/test_add.py",
        "AGENTS.md",
    ]
    report = addition_report(tmp, paths)

    assert_ok(report)


def test_addition_acceptance_allows_harness_payload_manifest(tmp):
    write_json(
        agent_path(tmp, "harness-payload.json"),
        {
            "schema_version": 2,
            "tool": "install-harness-payload",
            "include_roots": ["AGENTS.md"],
            "exclude_globs": [],
            "state_exclude_globs": [],
            "required_features": ["core"],
            "feature_bundles": {
                "core": {"include_globs": ["AGENTS.md"], "requires": []},
            },
            "profiles": {
                "standard": {"features": ["core"], "exclude_features": []},
            },
        },
    )

    report = addition_report(tmp, ".agents/harness-payload.json")

    assert_ok(report)


def test_addition_acceptance_allows_integration_descriptor(tmp):
    write_json(
        agent_path(tmp, "integrations", "demo-integration", "integration.json"),
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
        },
    )

    report = addition_report(tmp, ".agents/integrations/demo-integration/integration.json")

    assert_ok(report)


def test_addition_acceptance_treats_renamed_or_copied_paths_as_new(tmp):
    with patched_attrs(
        repo_changed,
        changed_files=lambda root: ["copied.txt", "renamed.txt"],
        changed_file_statuses=lambda root: {
            "copied.txt": {"C"},
            "renamed.txt": {"R"},
        },
    ):
        report = repo_changed.addition_acceptance_report(tmp)

    assert_not_ok(report)
    issue_paths = {issue["path"] for issue in report["issues"]}
    assert {"copied.txt", "renamed.txt"} <= issue_paths


def test_addition_acceptance_allows_deleted_skill_folder(tmp):
    with patched_attrs(
        repo_changed,
        changed_files=lambda root: [
            ".agents/skills/removed-skill/SKILL.md",
            ".agents/skills/removed-skill/module.json",
        ],
        changed_file_statuses=lambda root: {
            ".agents/skills/removed-skill/SKILL.md": {"D"},
            ".agents/skills/removed-skill/module.json": {"D"},
        },
    ):
        report = repo_changed.addition_acceptance_report(tmp)

    assert_ok(report)
    assert_summary(report, skills_checked=0)


def test_addition_acceptance_rejects_modern_dotnet_framework_skill(tmp):
    write_dotnet_naming_fixture(
        tmp,
        "dotnet-framework-support",
        "Use when maintaining .NET Framework Web Forms and WCF applications.",
        "Maintains .NET Framework applications with Web Forms and WCF.",
    )

    paths = [
        ".agents/skills/dotnet-framework-support/SKILL.md",
        ".agents/skills/dotnet-framework-support/module.json",
        ".agents/skills/dotnet-framework-support/suites/demo-evals.json",
    ]
    report = addition_report(tmp, paths)

    assert_not_ok(report)
    assert_contains(report["issues"], "dotnet-legacy-naming")


def test_check_changed_runs_addition_acceptance_gate(tmp):
    def fake_changed_files(root):
        return ["random.txt"]

    def fake_acceptance(root, *, paths=None, new_paths=None):
        return {
            "schema_version": 1,
            "tool": "skill-manager.addition-acceptance",
            "ok": False,
            "status": "failed",
            "summary": {"issue_count": 1},
            "issues": [
                {
                    "path": "random.txt",
                    "owner": "skill-manager",
                    "category": "unowned-new-file",
                    "reason": "new file is unowned",
                    "next_command": "move the file under an owner",
                }
            ],
        }

    with patched_attrs(repo_changed, changed_files=fake_changed_files, addition_acceptance_report=fake_acceptance):
        status, payload = capture_json(
            repo_changed.check_changed,
            Namespace(format="json", deep=False, verbose=False, full=True, record_progress=True),
            tmp,
        )

    assert status == 1
    assert_status(payload["addition_acceptance"], "failed")
    assert_contains(payload["checks"], "addition acceptance gate")


def test_check_changed_runs_proof_hygiene_gate(tmp):
    write_text(tmp / "app.py", "try:\n    run()\nexcept OSError:\n    pass\n")

    def fake_changed_files(root):
        return ["app.py"]

    def fake_acceptance(root, *, paths=None, new_paths=None):
        return {
            "schema_version": 1,
            "tool": "skill-manager.addition-acceptance",
            "ok": True,
            "status": "passed",
            "summary": {"issue_count": 0},
            "issues": [],
        }

    def fake_changed_scope(paths):
        return {
            "instructions": False,
            "skill_names": set(),
            "skills_generated": False,
            "workflows": False,
            "workflow_generated": False,
            "repo_surface": False,
            "python_paths": [".agents/skills/demo/scripts/tool.py"],
            "docs": [],
            "other": [],
        }

    with patched_attrs(
        repo_changed,
        changed_files=fake_changed_files,
        addition_acceptance_report=fake_acceptance,
        changed_scope=fake_changed_scope,
    ):
        status, payload = capture_json(
            repo_changed.check_changed,
            Namespace(format="json", deep=False, verbose=False, full=True),
            tmp,
        )

    assert status == 1
    assert_status(payload["proof_hygiene"], "failed")
    assert_status(payload["addition_acceptance"], "passed")
    assert_field(payload["input_fingerprint"], "algorithm", "sha256")
    assert_field(payload["input_fingerprint"], "changed_file_count", 1)
    assert_contains(payload["checks"], "proof hygiene gate")
    assert_contains(payload["proof_hygiene"]["findings"], "python_silent_failure")


def test_check_changed_runs_python_syntax_gate_for_changed_repo_python(tmp):
    script = tmp / ".agents" / "manage.py"
    write_text(script, "def broken(:\n    pass\n")

    def fake_changed_files(root):
        return [".agents/manage.py"]

    def fake_acceptance(root, *, paths=None, new_paths=None):
        return {
            "schema_version": 1,
            "tool": "skill-manager.addition-acceptance",
            "ok": True,
            "status": "passed",
            "summary": {"issue_count": 0},
            "issues": [],
        }

    def fake_health(root):
        return 0

    with patched_attrs(
        repo_changed,
        changed_files=fake_changed_files,
        addition_acceptance_report=fake_acceptance,
    ), patched_attrs(repo_changed.health, check_repo_health=fake_health):
        status, payload = capture_json(
            repo_changed.check_changed,
            Namespace(format="json", deep=False, verbose=False, full=True),
            tmp,
        )

    assert status == 1
    assert_status(payload["syntax_check"], "failed")
    assert_contains(payload["checks"], "python syntax gate")
    assert_contains(payload["syntax_check"]["issues"], "invalid syntax")


def test_changed_python_syntax_scope_ignores_deleted_files(tmp):
    existing = tmp / ".agents" / "manage.py"
    write_text(existing, "print('ok')\n")

    paths = repo_changed.existing_changed_python_paths(
        tmp,
        [".agents/manage.py", ".agents/skills/demo/deleted.py"],
    )

    assert paths == [".agents/manage.py"]


def test_deep_self_test_commands_focus_slow_changed_skill_owners(tmp):
    agent_command = repo_optimizations.self_test_command_for_skill(
        "agent-benchmarking",
        [
            ".agents/skills/agent-benchmarking/scripts/benchmark_common.py",
            ".agents/skills/agent-benchmarking/scripts/support/benchmark_common_metrics.py",
        ],
    )
    local_ai_command = repo_optimizations.self_test_command_for_skill(
        "local-ai-helper",
        [
            ".agents/skills/local-ai-helper/scripts/local_ai_support/setup_impl.py",
            ".agents/skills/local-ai-helper/scripts/local_ai_support/setup_catalog.py",
        ],
    )
    workflow_command = repo_optimizations.self_test_command_for_skill(
        "workflow-manager",
        [
            ".agents/skills/workflow-manager/scripts/create_workflow.py",
            ".agents/skills/workflow-manager/assets/workflow-template/WORKFLOW.md",
        ],
    )
    skill_manager_command = repo_optimizations.self_test_command_for_skill(
        "skill-manager",
        [".agents/skills/skill-manager/scripts/repo_support/repo_optimizations.py"],
    )
    agent_runner_changed_command = repo_optimizations.self_test_command_for_skill(
        "agent-benchmarking",
        [
            ".agents/skills/agent-benchmarking/scripts/benchmark_common.py",
            ".agents/skills/agent-benchmarking/scripts/run_self_tests.py",
        ],
    )
    unknown_support_changed_command = repo_optimizations.self_test_command_for_skill(
        "agent-benchmarking",
        [
            ".agents/skills/agent-benchmarking/scripts/benchmark_common.py",
            ".agents/skills/agent-benchmarking/scripts/support/new_support.py",
        ],
    )

    assert_has_all(agent_command, "--match record_result", "--match standard_metrics", "--match compare_runs_optimization_gate")
    assert "--match compare_ " not in f"{agent_command} "
    assert_has_all(
        local_ai_command,
        "--match bootstrap_no_download",
        "--match bootstrap_json",
        "--match setup_parser",
        "--match setup_catalog",
    )
    padded_local_ai_command = f"{local_ai_command} "
    assert "--match bootstrap " not in padded_local_ai_command
    assert "--match setup_ " not in padded_local_ai_command
    assert_has_all(workflow_command, "--match create_workflow", "--match asset_workflow_template")
    assert_has_all(
        skill_manager_command,
        "--match changed_validation_plan",
        "--match deep_self_test_commands_focus_slow_changed_skill_owners",
    )
    assert "--match check_changed " not in f"{skill_manager_command} "
    assert "--match" not in agent_runner_changed_command
    assert "--match" not in unknown_support_changed_command


def test_large_diff_review_packet_prioritizes_high_risk_paths(tmp):
    write_text(tmp / "new-helper.py", "print('hello')\n")

    with patched_attrs(
        repo_changed,
        changed_file_statuses=lambda root: {"AGENTS.md": {"M"}, "new-helper.py": {"?"}},
    ), patched_attrs(
        repo_changed.repo_cost_policy,
        changed_diff_estimate=lambda root: {"files": 1, "added": 600, "deleted": 0, "estimated_tokens": 7200},
    ):
        packet = repo_changed.large_diff_review_packet(
            tmp,
            ["new-helper.py", "AGENTS.md"],
            [{"command": "python -B .agents/manage.py startup-context --baseline-ref HEAD", "required": True}],
            {
                "status": "fresh",
                "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
                "next_command": "none, navigation maps are fresh",
            },
        )

    assert_field(packet, "status", "over-budget")
    assert_field(packet, "navigation_read_first", "automations/navigation/artifacts/maps/HANDOFF.md")
    assert packet["tokens_over_review_budget"] > 0
    assert_field(packet["read_first"][0], "path", "AGENTS.md")
    assert_field(packet["read_first"][0], "risk", "high")
    assert_contains(packet["validation_first"], "startup-context --baseline-ref HEAD")
    assert_field(packet, "owner_review_packet_count", 1)
    assert_field(packet["owner_review_packets"][0], "owner", "repo")
    assert_field(packet["owner_review_packets"][0], "priority", "high")
    assert_contains(packet["owner_review_commands"], "review-packet --owner")
    assert_field(packet["cost_ledger"], "raw_changed_diff_estimated_tokens", 7200)
    assert_field(packet["cost_ledger"], "release_gate", "needs-owner-review")
    assert_has_all(packet["cost_ledger"]["billing_boundary"], "Excludes output tokens")


def test_review_packet_command_writes_json_and_markdown(tmp):
    with patched_attrs(
        repo_changed,
        changed_files=lambda root: ["AGENTS.md"],
        changed_file_statuses=lambda root: {"AGENTS.md": {"M"}},
        changed_scope=lambda paths: {"skill_names": set(), "workflows": False},
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
    ), patched_attrs(
        repo_changed.repo_cost_policy,
        changed_diff_estimate=lambda root: {"estimated_tokens": 7000, "tracked_estimated_tokens": 7000, "untracked_estimated_tokens": 0},
    ), patched_attrs(
        repo_changed.repo_optimizations,
        changed_validation_plan=lambda root, paths, scope, deep=False: [
            {"command": "python -B .agents/manage.py startup-context --baseline-ref HEAD", "required": True}
        ],
    ):
        status, payload = capture_json(
            repo_changed.review_packet_command,
            Namespace(format="json", summary=True, compact=True, deep=False, write_dir="evidence/review"),
            tmp,
        )

    assert status == 0
    assert_field(payload, "status", "over-budget")
    assert_contains(payload["artifacts"], "evidence/review/review-packet.json")
    assert_contains(payload["artifacts"], "evidence/review/owners/repo.json")
    assert_contains(payload["artifacts"], "evidence/review/review-plan.json")
    assert_contains(payload["artifacts"], "evidence/review/review-cost-ledger.json")
    assert_field(payload["review_plan_summary"], "status", "needs-review")
    assert_field(payload["review_cost_report"], "billing_scope", "input-context-estimate-only")
    assert (tmp / "evidence" / "review" / "review-packet.json").is_file()
    assert (tmp / "evidence" / "review" / "review-packet.md").is_file()
    assert (tmp / "evidence" / "review" / "review-plan.json").is_file()
    assert (tmp / "evidence" / "review" / "review-plan.md").is_file()
    assert (tmp / "evidence" / "review" / "review-cost-ledger.json").is_file()
    assert (tmp / "evidence" / "review" / "review-cost-ledger.md").is_file()
    assert (tmp / "evidence" / "review" / "owners" / "repo.json").is_file()
    assert (tmp / "evidence" / "review" / "owners" / "repo.md").is_file()
    review_plan = json.loads((tmp / "evidence" / "review" / "review-plan.json").read_text(encoding="utf-8"))
    assert_field(review_plan, "tool", "skill-manager.review-plan")
    assert review_plan["review_unit_count"] >= 1
    assert_has_all(review_plan["next_pending_command"], "review-packet")
    cost_report = json.loads((tmp / "evidence" / "review" / "review-cost-ledger.json").read_text(encoding="utf-8"))
    assert_field(cost_report, "tool", "skill-manager.review-cost-report")
    assert_has_all(" ".join(cost_report["boundary"]), "output tokens")
    assert_field(cost_report["break_even_extra_output_tokens"], "output_price_multiplier_4x", cost_report["next_review_unit_saved_tokens_vs_raw_estimated"] // 4)


def test_review_packet_command_emits_owner_slice(tmp):
    owner_paths = [
        f".agents/skills/skill-manager/scripts/repo_support/owner_file_{index}.py"
        for index in range(10)
    ]
    paths = [*owner_paths, "docs/notes.md"]
    with patched_attrs(
        repo_changed,
        changed_files=lambda root: paths,
        changed_file_statuses=lambda root: {path: {"M"} for path in paths},
        changed_path_token_estimates=lambda root, values: {
            **{path: {"estimated_tokens": 700} for path in owner_paths},
            "docs/notes.md": {"estimated_tokens": 200},
        },
        changed_scope=lambda values: {"skill_names": {"skill-manager"}, "workflows": False},
    ), patched_attrs(
        repo_review_packet,
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
    ), patched_attrs(
        repo_changed.repo_cost_policy,
        changed_diff_estimate=lambda root: {"estimated_tokens": 6400, "tracked_estimated_tokens": 6400, "untracked_estimated_tokens": 0},
    ), patched_attrs(
        repo_changed.repo_optimizations,
        changed_validation_plan=lambda root, values, scope, deep=False: [
            {"command": "python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/skill-manager", "required": True},
            {"command": "python -B .agents/manage.py check-changed", "required": True},
        ],
    ):
        status, payload = capture_json(
            repo_changed.review_packet_command,
            Namespace(
                format="json",
                summary=True,
                compact=True,
                deep=False,
                write_dir=None,
                owner="skill:skill-manager",
            ),
            tmp,
        )

    assert status == 0
    assert_field(payload, "tool", "skill-manager.owner-review-packet")
    assert_field(payload, "owner", "skill:skill-manager")
    assert_field(payload, "status", "over-budget")
    assert_field(payload, "changed_file_count", 10)
    assert_field(payload, "estimated_changed_tokens", 7000)
    assert_field(payload["cost_ledger"], "raw_changed_diff_estimated_tokens", 6400)
    assert_field(payload["cost_ledger"], "largest_owner_packet_estimated_tokens", 7000)
    assert_field(payload["cost_ledger"], "largest_owner_subpacket_estimated_tokens", 700)
    assert_field(payload["cost_ledger"], "next_review_unit_estimated_tokens", 700)
    assert_field(payload["cost_ledger"], "comparison_scope", "selected-owner-packet")
    assert_field(payload["cost_ledger"], "release_gate", "needs-owner-review")
    assert_field(payload, "owner_review_subpacket_count", 10)
    assert_field(payload, "largest_owner_subpacket_estimated_tokens", 700)
    assert_contains(payload["owner_review_subpacket_commands"], "--path")
    assert_has_all(payload["next_command"], "--path")
    assert_has_all(payload["owner_summary_command"], "review-packet --owner skill:skill-manager")
    assert len(payload["read_first"]) == 8
    assert payload["paths"] == owner_paths
    assert_field(payload["read_first"][0], "path", owner_paths[0])
    assert_contains(payload["validation_first"], "syntax-check --paths .agents/skills/skill-manager")
    assert_contains(payload["validation_first"], "validate_skill.py .agents/skills/skill-manager")


def test_review_packet_command_emits_path_subpacket(tmp):
    owner_paths = [
        f".agents/skills/skill-manager/scripts/repo_support/owner_file_{index}.py"
        for index in range(10)
    ]
    paths = [*owner_paths, "docs/notes.md"]
    with patched_attrs(
        repo_changed,
        changed_files=lambda root: paths,
        changed_file_statuses=lambda root: {path: {"M"} for path in paths},
        changed_path_token_estimates=lambda root, values: {
            **{path: {"estimated_tokens": 700} for path in owner_paths},
            "docs/notes.md": {"estimated_tokens": 200},
        },
        changed_scope=lambda values: {"skill_names": {"skill-manager"}, "workflows": False},
    ), patched_attrs(
        repo_review_packet,
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
    ), patched_attrs(
        repo_changed.repo_cost_policy,
        changed_diff_estimate=lambda root: {"estimated_tokens": 6400, "tracked_estimated_tokens": 6400, "untracked_estimated_tokens": 0},
    ), patched_attrs(
        repo_changed.repo_optimizations,
        changed_validation_plan=lambda root, values, scope, deep=False: [
            {"command": "python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/skill-manager", "required": True},
            {"command": "python -B .agents/manage.py check-changed", "required": True},
        ],
    ):
        status, payload = capture_json(
            repo_changed.review_packet_command,
            Namespace(
                format="json",
                summary=True,
                compact=True,
                deep=False,
                write_dir=None,
                owner="skill:skill-manager",
                paths=[owner_paths[3]],
            ),
            tmp,
        )

    assert status == 0
    assert_field(payload, "tool", "skill-manager.owner-review-packet")
    assert_field(payload, "owner", "skill:skill-manager")
    assert_field(payload, "scope", "path-slice")
    assert_field(payload, "status", "within-budget")
    assert_field(payload, "changed_file_count", 1)
    assert_field(payload, "estimated_changed_tokens", 700)
    assert_field(payload, "parent_owner_changed_file_count", 10)
    assert_field(payload, "parent_owner_estimated_changed_tokens", 7000)
    assert payload["paths"] == [owner_paths[3]]
    assert_field(payload["read_first"][0], "path", owner_paths[3])
    assert_field(payload["cost_ledger"], "comparison_scope", "selected-owner-subpacket")
    assert_field(payload["cost_ledger"], "raw_changed_diff_estimated_tokens", 6400)
    assert_field(payload["cost_ledger"], "next_review_unit_estimated_tokens", 700)
    assert_field(payload["cost_ledger"], "next_review_unit_saved_tokens_vs_raw_estimated", 5700)
    assert_has_all(payload["next_command"], "syntax-check --paths .agents/skills/skill-manager")


def test_review_packet_command_accepts_absolute_repo_path_subpacket(tmp):
    owner_paths = [
        f".agents/skills/skill-manager/scripts/repo_support/owner_file_{index}.py"
        for index in range(10)
    ]
    paths = [*owner_paths, "docs/notes.md"]
    absolute_path = str((tmp / owner_paths[3]).resolve())
    with patched_attrs(
        repo_changed,
        changed_files=lambda root: paths,
        changed_file_statuses=lambda root: {path: {"M"} for path in paths},
        changed_path_token_estimates=lambda root, values: {
            **{path: {"estimated_tokens": 700} for path in owner_paths},
            "docs/notes.md": {"estimated_tokens": 200},
        },
        changed_scope=lambda values: {"skill_names": {"skill-manager"}, "workflows": False},
    ), patched_attrs(
        repo_review_packet,
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
    ), patched_attrs(
        repo_changed.repo_cost_policy,
        changed_diff_estimate=lambda root: {"estimated_tokens": 6400, "tracked_estimated_tokens": 6400, "untracked_estimated_tokens": 0},
    ), patched_attrs(
        repo_changed.repo_optimizations,
        changed_validation_plan=lambda root, values, scope, deep=False: [
            {"command": "python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/skill-manager", "required": True},
            {"command": "python -B .agents/manage.py check-changed", "required": True},
        ],
    ):
        status, payload = capture_json(
            repo_changed.review_packet_command,
            Namespace(
                format="json",
                summary=True,
                compact=True,
                deep=False,
                write_dir=None,
                owner="skill:skill-manager",
                paths=[absolute_path],
            ),
            tmp,
        )

    assert status == 0
    assert_field(payload, "status", "within-budget")
    assert payload["paths"] == [owner_paths[3]]
    assert payload["selected_paths"] == [owner_paths[3]]
    assert_field(payload["read_first"][0], "path", owner_paths[3])


def test_review_packet_routes_large_path_to_hunk_subpacket(tmp):
    big_path = ".agents/skills/skill-manager/scripts/run_self_tests.py"
    small_path = ".agents/skills/skill-manager/scripts/repo_support/repo_changed.py"
    paths = [big_path, small_path]

    def fake_hunks(root, owner, row, estimated_tokens, validation_first, review_budget):
        if row.get("path") != big_path:
            return []
        return [
            {
                "schema_version": 1,
                "tool": "skill-manager.owner-review-hunk",
                "owner": owner,
                "scope": "hunk",
                "path": big_path,
                "hunk": "h001",
                "range": f"{big_path}:100-160",
                "line_start": 100,
                "line_end": 160,
                "status": "within-budget",
                "priority": "high",
                "changed_file_count": 1,
                "estimated_changed_tokens": 732,
                "review_budget_tokens": review_budget,
                "tokens_over_review_budget": 0,
                "risk_counts": {"high": 1},
                "read_first": [{"path": big_path, "risk": "high", "status": "M", "hunk": "h001", "range": f"{big_path}:100-160"}],
                "paths": [big_path],
                "validation_first": validation_first[:6],
                "next_command": repo_changed.owner_review_command(owner, [big_path], ["h001"]),
                "review_rule": "Review this hunk.",
            }
        ]

    with patched_attrs(
        repo_changed,
        changed_files=lambda root: paths,
        changed_file_statuses=lambda root: {path: {"M"} for path in paths},
        changed_path_token_estimates=lambda root, values: {
            big_path: {"estimated_tokens": 9000},
            small_path: {"estimated_tokens": 700},
        },
        changed_scope=lambda values: {"skill_names": {"skill-manager"}, "workflows": False},
        path_review_hunk_subpackets=fake_hunks,
    ), patched_attrs(
        repo_review_packet,
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
    ), patched_attrs(
        repo_changed.repo_cost_policy,
        changed_diff_estimate=lambda root: {"estimated_tokens": 9700, "tracked_estimated_tokens": 9700, "untracked_estimated_tokens": 0},
    ), patched_attrs(
        repo_changed.repo_optimizations,
        changed_validation_plan=lambda root, values, scope, deep=False: [
            {"command": "python -B .agents/manage.py syntax-check --paths .agents/skills/skill-manager --format json", "required": True},
        ],
    ):
        status, payload = capture_json(
            repo_changed.review_packet_command,
            Namespace(
                format="json",
                summary=True,
                compact=True,
                deep=False,
                write_dir=None,
                owner="skill:skill-manager",
            ),
            tmp,
        )
        full_packet = repo_review_packet.build_review_packet(
            tmp,
            owner="skill:skill-manager",
            refresh_navigation=False,
        )

    assert status == 0
    assert_field(payload, "status", "over-budget")
    assert_field(payload, "owner_review_subpacket_count", 2)
    assert_field(payload, "owner_review_hunk_count", 1)
    assert_field(payload, "largest_owner_subpacket_estimated_tokens", 9000)
    assert_field(payload, "largest_owner_hunk_estimated_tokens", 732)
    assert_has_all(payload["next_command"], "--path", big_path, "--hunk h001")
    assert_field(payload["cost_ledger"], "next_review_unit_estimated_tokens", 732)
    assert_field(payload["cost_ledger"], "largest_review_unit_estimated_tokens", 732)
    review_plan = repo_review_progress.build_review_plan(full_packet)
    assert_has_all(review_plan["next_pending_command"], "--path", big_path, "--hunk h001")
    assert_field(review_plan["review_units"][0], "scope", "hunk")
    assert_field(review_plan["review_units"][0], "estimated_changed_tokens", 732)
    assert review_plan["review_units"][0]["estimated_changed_tokens"] <= full_packet["review_budget_tokens"]


def test_review_packet_command_emits_hunk_subpacket(tmp):
    big_path = ".agents/skills/skill-manager/scripts/run_self_tests.py"

    def fake_hunks(root, owner, row, estimated_tokens, validation_first, review_budget):
        return [
            {
                "schema_version": 1,
                "tool": "skill-manager.owner-review-hunk",
                "owner": owner,
                "scope": "hunk",
                "path": big_path,
                "hunk": "h001",
                "range": f"{big_path}:100-160",
                "line_start": 100,
                "line_end": 160,
                "status": "within-budget",
                "priority": "high",
                "changed_file_count": 1,
                "estimated_changed_tokens": 732,
                "review_budget_tokens": review_budget,
                "tokens_over_review_budget": 0,
                "risk_counts": {"high": 1},
                "read_first": [{"path": big_path, "risk": "high", "status": "M", "hunk": "h001", "range": f"{big_path}:100-160"}],
                "paths": [big_path],
                "validation_first": validation_first[:6],
                "next_command": repo_changed.owner_review_command(owner, [big_path], ["h001"]),
                "review_rule": "Review this hunk.",
            },
            {
                "schema_version": 1,
                "tool": "skill-manager.owner-review-hunk",
                "owner": owner,
                "scope": "hunk",
                "path": big_path,
                "hunk": "h002",
                "range": f"{big_path}:200-230",
                "line_start": 200,
                "line_end": 230,
                "status": "within-budget",
                "priority": "high",
                "changed_file_count": 1,
                "estimated_changed_tokens": 372,
                "review_budget_tokens": review_budget,
                "tokens_over_review_budget": 0,
                "risk_counts": {"high": 1},
                "read_first": [{"path": big_path, "risk": "high", "status": "M", "hunk": "h002", "range": f"{big_path}:200-230"}],
                "paths": [big_path],
                "validation_first": validation_first[:6],
                "next_command": repo_changed.owner_review_command(owner, [big_path], ["h002"]),
                "review_rule": "Review this hunk.",
            },
        ]

    with patched_attrs(
        repo_changed,
        changed_files=lambda root: [big_path],
        changed_file_statuses=lambda root: {big_path: {"M"}},
        changed_path_token_estimates=lambda root, values: {big_path: {"estimated_tokens": 9000}},
        changed_scope=lambda values: {"skill_names": {"skill-manager"}, "workflows": False},
        path_review_hunk_subpackets=fake_hunks,
    ), patched_attrs(
        repo_review_packet,
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
    ), patched_attrs(
        repo_changed.repo_cost_policy,
        changed_diff_estimate=lambda root: {"estimated_tokens": 9000, "tracked_estimated_tokens": 9000, "untracked_estimated_tokens": 0},
    ), patched_attrs(
        repo_changed.repo_optimizations,
        changed_validation_plan=lambda root, values, scope, deep=False: [
            {"command": "python -B .agents/manage.py syntax-check --paths .agents/skills/skill-manager --format json", "required": True},
        ],
    ):
        status, payload = capture_json(
            repo_changed.review_packet_command,
            Namespace(
                format="json",
                summary=True,
                compact=True,
                deep=False,
                write_dir=None,
                owner="skill:skill-manager",
                paths=[big_path],
                hunks=["h001"],
            ),
            tmp,
        )
        last_status, last_payload = capture_json(
            repo_changed.review_packet_command,
            Namespace(
                format="json",
                summary=True,
                compact=True,
                deep=False,
                write_dir=None,
                owner="skill:skill-manager",
                paths=[big_path],
                hunks=["h002"],
            ),
            tmp,
        )

    assert status == 0
    assert_field(payload, "scope", "hunk-slice")
    assert_field(payload, "status", "within-budget")
    assert_field(payload, "estimated_changed_tokens", 732)
    assert payload["selected_hunks"] == ["h001"]
    assert payload["selected_ranges"] == [f"{big_path}:100-160"]
    assert_field(payload, "available_hunk_count", 2)
    assert_field(payload, "remaining_hunk_count", 1)
    assert_has_all(payload["next_hunk_command"], "--hunk h002")
    assert_has_all(payload["next_command"], "--hunk h002")
    assert_field(payload["cost_ledger"], "comparison_scope", "selected-owner-hunk")
    assert_field(payload["cost_ledger"], "next_review_unit_estimated_tokens", 732)
    assert last_status == 0
    assert_field(last_payload, "scope", "hunk-slice")
    assert_field(last_payload, "remaining_hunk_count", 0)
    assert_field(last_payload, "next_hunk_command", "")
    assert_has_all(last_payload["next_command"], "syntax-check --paths .agents/skills/skill-manager")


def test_selected_hunk_packet_routes_non_contiguous_selection_to_gap(tmp):
    path = ".agents/skills/skill-manager/scripts/run_self_tests.py"
    hunk_packets = []
    for index, start in enumerate((100, 200, 300), start=1):
        hunk = f"h{index:03d}"
        hunk_packets.append(
            {
                "schema_version": 1,
                "tool": "skill-manager.owner-review-hunk",
                "owner": "skill:skill-manager",
                "scope": "hunk",
                "path": path,
                "hunk": hunk,
                "range": f"{path}:{start}-{start + 10}",
                "line_start": start,
                "line_end": start + 10,
                "status": "within-budget",
                "priority": "high",
                "changed_file_count": 1,
                "estimated_changed_tokens": 132,
                "review_budget_tokens": 5000,
                "tokens_over_review_budget": 0,
                "risk_counts": {"high": 1},
                "read_first": [{"path": path, "risk": "high", "status": "M", "hunk": hunk}],
                "paths": [path],
                "validation_first": ["python -B .agents/manage.py syntax-check --paths .agents/skills/skill-manager --format json"],
                "next_command": repo_changed.owner_review_command("skill:skill-manager", [path], [hunk]),
                "review_rule": "Review this hunk.",
            }
        )

    packet = repo_changed.selected_hunk_packet(
        {
            "changed_diff_estimated_tokens": 9000,
            "changed_file_count": 1,
            "owner_review_packet_count": 1,
        },
        {
            "owner": "skill:skill-manager",
            "review_budget_tokens": 5000,
            "validation_first": ["python -B .agents/manage.py syntax-check --paths .agents/skills/skill-manager --format json"],
            "changed_file_count": 1,
            "estimated_changed_tokens": 396,
            "tokens_over_review_budget": 0,
            "owner_review_subpacket_count": 1,
        },
        [{"path_review_hunks": hunk_packets}],
        ["h001", "h003"],
    )

    assert_field(packet, "status", "within-budget")
    assert_field(packet, "skipped_hunk_gap_count", 1)
    assert_field(packet, "remaining_hunk_count", 1)
    assert_has_all(packet["next_hunk_command"], "--hunk h002")
    assert_has_all(packet["next_command"], "--hunk h002")


def test_review_progress_marks_command_and_skips_completed_unit(tmp):
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_file_count": 2,
        "validation_first": [],
        "owner_review_packets": [
            {
                "owner": "skill:first",
                "status": "within-budget",
                "scope": "owner",
                "changed_file_count": 1,
                "estimated_changed_tokens": 100,
                "owner_summary_command": "python -B .agents/manage.py review-packet --owner skill:first --summary --compact --format json",
            },
            {
                "owner": "skill:second",
                "status": "within-budget",
                "scope": "owner",
                "changed_file_count": 1,
                "estimated_changed_tokens": 120,
                "owner_summary_command": "python -B .agents/manage.py review-packet --owner skill:second --summary --compact --format json",
            },
        ],
    }
    plan = repo_review_progress.build_review_plan(packet)
    first_command = plan["review_units"][0]["command"]

    initial, _state = repo_review_progress.build_review_progress(
        plan,
        input_fingerprint={"digest": "digest-a"},
    )
    marked, state = repo_review_progress.build_review_progress(
        plan,
        input_fingerprint={"digest": "digest-a"},
        mark_command=first_command,
    )
    resumed, _state = repo_review_progress.build_review_progress(
        plan,
        input_fingerprint={"digest": "digest-a"},
        state=state,
    )
    stale, _state = repo_review_progress.build_review_progress(
        plan,
        input_fingerprint={"digest": "digest-b"},
        state=state,
    )

    assert_field(initial, "review_state", "initial")
    assert_field(initial["coverage"], "owner_total", 2)
    assert_field(initial["coverage"], "pending_review_unit_count", 2)
    assert_field(initial["coverage"], "largest_unreviewed_owner", "skill:second")
    assert_field(marked, "completed_unit_count", 1)
    assert_field(marked["coverage"], "owners_complete", 1)
    assert_field(resumed, "review_state", "partial")
    assert_field(resumed["coverage"], "cross_cutting_sample_required", True)
    assert_has_all(resumed["next_pending_command"], "--owner skill:second")
    assert_false(stale, "stale")
    assert_field(stale, "reuse_status", "matched-unit-signatures")
    assert_field(stale, "reused_completed_unit_count", 1)
    assert_has_all(stale["next_pending_command"], "--owner skill:second")


def test_review_progress_reuses_matching_unit_signatures_after_input_changes(tmp):
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_file_count": 2,
        "validation_first": [],
        "owner_review_packets": [
            {
                "owner": "skill:first",
                "status": "within-budget",
                "scope": "owner",
                "changed_file_count": 1,
                "estimated_changed_tokens": 100,
                "owner_summary_command": "python -B .agents/manage.py review-packet --owner skill:first --summary --compact --format json",
            },
            {
                "owner": "skill:second",
                "status": "within-budget",
                "scope": "owner",
                "changed_file_count": 1,
                "estimated_changed_tokens": 120,
                "owner_summary_command": "python -B .agents/manage.py review-packet --owner skill:second --summary --compact --format json",
            },
        ],
    }
    plan = repo_review_progress.build_review_plan(packet)
    first_command = plan["review_units"][0]["command"]
    marked, state = repo_review_progress.build_review_progress(
        plan,
        input_fingerprint={"digest": "digest-a"},
        mark_command=first_command,
    )
    assert_field(marked, "completed_unit_count", 1)

    changed_packet = dict(packet)
    changed_packet["changed_file_count"] = 3
    changed_packet["owner_review_packets"] = [
        *packet["owner_review_packets"],
        {
            "owner": "skill:third",
            "status": "within-budget",
            "scope": "owner",
            "changed_file_count": 1,
            "estimated_changed_tokens": 80,
            "owner_summary_command": "python -B .agents/manage.py review-packet --owner skill:third --summary --compact --format json",
        },
    ]
    changed_plan = repo_review_progress.build_review_plan(changed_packet)

    reused, pending_write = repo_review_progress.build_review_progress(
        changed_plan,
        input_fingerprint={"digest": "digest-b"},
        state=state,
    )

    assert_false(reused, "stale")
    assert_field(reused, "review_state", "partial")
    assert_field(reused, "completed_unit_count", 1)
    assert_field(reused, "pending_unit_count", 2)
    assert_field(reused, "reused_completed_unit_count", 1)
    assert_field(reused, "stale_completed_unit_count", 0)
    assert_has_all(reused["next_pending_command"], "--owner skill:second")
    assert pending_write is not None
    assert_field(pending_write, "fingerprint_digest", reused["fingerprint_digest"])
    assert_contains(pending_write["completed_units"], plan["review_units"][0]["id"])


def test_review_progress_rejects_ambiguous_partial_mark_command(tmp):
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "owner_review_packets": [
            {
                "owner": "skill:demo",
                "status": "over-budget",
                "scope": "owner",
                "estimated_changed_tokens": 200,
                "owner_review_subpackets": [
                    {
                        "path": "src/demo.py",
                        "estimated_changed_tokens": 200,
                        "path_review_hunks": [
                            {
                                "path": "src/demo.py",
                                "hunk": f"h{index:03d}",
                                "estimated_changed_tokens": 100,
                                "next_command": (
                                    "python -B .agents/manage.py review-packet "
                                    f"--owner skill:demo --path src/demo.py --hunk h{index:03d} "
                                    "--summary --compact --format json"
                                ),
                            }
                            for index in range(1, 3)
                        ],
                    }
                ],
            }
        ],
    }
    plan = repo_review_progress.build_review_plan(packet)

    report, state = repo_review_progress.build_review_progress(
        plan,
        input_fingerprint={"digest": "digest-a"},
        mark_command="python -B .agents/manage.py review-packet --owner skill:demo",
    )

    assert_field(report, "status", "unit-ambiguous")
    assert "matched multiple" in report["issue"]
    assert state is None
    assert_field(report, "completed_unit_count", 0)


def test_review_plan_deduplicates_validation_commands_before_progress_matching(tmp):
    command = "python -B .agents/manage.py check-additions"
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_file_count": 1,
        "owner_review_packets": [],
        "validation_first": [command, f"  {command}  ", command],
    }
    plan = repo_review_progress.build_review_plan(packet)

    assert_field(plan, "validation_unit_count", 1)
    assert_field(plan["validation_units"][0], "command", command)
    marked, state = repo_review_progress.build_review_progress(
        plan,
        input_fingerprint={"digest": "digest-a"},
        mark_command=command,
    )
    assert_ok(marked)
    assert_field(marked, "status", "complete")
    assert_field(marked, "completed_unit_count", 1)
    assert state is not None


def test_finish_specs_run_changed_scope_and_only_impacted_workflow_checks(tmp):
    (tmp / "automations" / "demo-flow" / "runs" / "run-a").mkdir(parents=True)
    with patched_attrs(
        repo_changed,
        changed_files=lambda _root: ["automations/demo-flow/WORKFLOW.md"],
    ):
        specs = repo_qol_finish.finish_check_specs(tmp, deep=False)
    phases = [str(item.get("phase", "")) for item in specs]

    assert "sync-check" not in phases, phases
    assert_contains_each(phases, "workflow-hooks", "workflow-run-index", "changed-scope")
    assert "repo-check" not in phases, phases
    assert "workflow-evals" not in phases, phases
    assert phases.count("changed-scope") == 1, phases


def test_finish_specs_keep_unrelated_normal_work_to_changed_scope_only(tmp):
    with patched_attrs(repo_changed, changed_files=lambda _root: ["src/demo.py"]):
        specs = repo_qol_finish.finish_check_specs(tmp, deep=False)

    assert [item["phase"] for item in specs] == ["changed-scope"], specs
    assert "--record-progress" in specs[0]["command"]
    assert "--deep" not in specs[0]["command"]


def test_finish_release_full_keeps_exhaustive_repository_checks(tmp):
    with patched_attrs(repo_changed, changed_files=lambda _root: []):
        specs = repo_qol_finish.finish_check_specs(tmp, deep=False, release_full=True)
        deep_specs = repo_qol_finish.finish_check_specs(tmp, deep=True)

    phases = [str(item.get("phase", "")) for item in specs]
    assert_contains_each(
        phases,
        "workflow-hooks",
        "clean-context-proof",
        "install-harness-smoke-fast",
        "user-story-workflow-smoke",
        "workflow-evals",
        "repo-check",
        "changed-scope",
        "benchmark-doctor",
    )
    changed_scope = next(item for item in specs if item["phase"] == "changed-scope")
    deep_changed_scope = next(item for item in deep_specs if item["phase"] == "changed-scope")
    assert repo_qol_finish.FINISH_CHANGED_DEEP_TIMEOUT_SECONDS == 1800
    assert_field(
        changed_scope,
        "timeout_seconds",
        repo_qol_finish.FINISH_CHANGED_DEEP_TIMEOUT_SECONDS,
    )
    assert_field(
        deep_changed_scope,
        "timeout_seconds",
        repo_qol_finish.FINISH_CHANGED_DEEP_TIMEOUT_SECONDS,
    )
    assert changed_scope["timeout_seconds"] > repo_qol_finish.FINISH_DEEP_TIMEOUT_SECONDS


def test_review_plan_batches_adjacent_hunks_under_review_budget(tmp):
    path = ".agents/skills/skill-manager/scripts/run_self_tests.py"

    def hunk_packet(hunk: str, line_start: int, tokens: int) -> dict[str, object]:
        return {
            "schema_version": 1,
            "tool": "skill-manager.owner-review-hunk",
            "owner": "skill:skill-manager",
            "scope": "hunk",
            "path": path,
            "hunk": hunk,
            "range": f"{path}:{line_start}-{line_start + 10}",
            "line_start": line_start,
            "line_end": line_start + 10,
            "status": "within-budget",
            "priority": "high",
            "changed_file_count": 1,
            "estimated_changed_tokens": tokens,
            "review_budget_tokens": 5000,
            "tokens_over_review_budget": 0,
            "risk_counts": {"high": 1},
            "read_first": [{"path": path, "risk": "high", "status": "M", "hunk": hunk}],
            "paths": [path],
            "validation_first": ["python -B .agents/manage.py syntax-check --paths .agents/skills/skill-manager --format json"],
            "next_command": repo_changed.owner_review_command("skill:skill-manager", [path], [hunk]),
            "review_rule": "Review this hunk.",
        }

    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_file_count": 1,
        "changed_diff_estimated_tokens": 9000,
        "review_budget_tokens": 5000,
        "validation_first": [],
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "status": "over-budget",
                "scope": "owner",
                "priority": "high",
                "changed_file_count": 1,
                "estimated_changed_tokens": 7800,
                "review_budget_tokens": 5000,
                "owner_summary_command": repo_changed.owner_review_command("skill:skill-manager"),
                "owner_review_subpackets": [
                    {
                        "owner": "skill:skill-manager",
                        "scope": "path",
                        "path": path,
                        "status": "over-budget",
                        "priority": "high",
                        "changed_file_count": 1,
                        "estimated_changed_tokens": 7800,
                        "review_budget_tokens": 5000,
                        "path_summary_command": repo_changed.owner_review_command("skill:skill-manager", [path]),
                        "path_review_hunks": [
                            hunk_packet("h001", 100, 2000),
                            hunk_packet("h002", 120, 2200),
                            hunk_packet("h003", 300, 3600),
                        ],
                    }
                ],
            }
        ],
    }

    plan = repo_review_progress.build_review_plan(packet)
    first_unit = plan["review_units"][0]
    second_unit = plan["review_units"][1]

    assert_field(plan, "review_unit_count", 2)
    assert_field(plan["review_batching"], "status", "batched")
    assert_field(plan["review_batching"], "source_review_unit_count", 3)
    assert_field(plan["review_batching"], "saved_review_unit_count", 1)
    assert_field(first_unit, "scope", "hunk-batch")
    assert_field(first_unit, "hunk", "h001,h002")
    assert first_unit["hunks"] == ["h001", "h002"]
    assert_field(first_unit, "estimated_changed_tokens", 4200)
    assert_has_all(first_unit["command"], "--hunk h001 --hunk h002")
    assert_field(second_unit, "scope", "hunk")
    assert_field(second_unit, "hunk", "h003")
    assert_has_all(plan["next_pending_command"], "--hunk h001 --hunk h002")


def test_review_plan_caps_hunk_batch_width_for_compact_commands(tmp):
    path = ".agents/skills/skill-manager/scripts/run_self_tests.py"
    hunks = []
    for index in range(1, 21):
        hunk = f"h{index:03d}"
        hunks.append(
            {
                "schema_version": 1,
                "tool": "skill-manager.owner-review-hunk",
                "owner": "skill:skill-manager",
                "scope": "hunk",
                "path": path,
                "hunk": hunk,
                "range": f"{path}:{index}-{index}",
                "status": "within-budget",
                "priority": "high",
                "estimated_changed_tokens": 100,
                "review_budget_tokens": 5000,
                "risk_counts": {"high": 1},
                "read_first": [{"path": path, "risk": "high", "status": "M", "hunk": hunk}],
                "paths": [path],
                "next_command": repo_changed.owner_review_command("skill:skill-manager", [path], [hunk]),
            }
        )
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_file_count": 1,
        "review_budget_tokens": 5000,
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "status": "over-budget",
                "scope": "owner",
                "priority": "high",
                "changed_file_count": 1,
                "estimated_changed_tokens": 2000,
                "review_budget_tokens": 5000,
                "owner_summary_command": repo_changed.owner_review_command("skill:skill-manager"),
                "owner_review_subpackets": [
                    {
                        "owner": "skill:skill-manager",
                        "scope": "path",
                        "path": path,
                        "status": "over-budget",
                        "priority": "high",
                        "estimated_changed_tokens": 2000,
                        "review_budget_tokens": 5000,
                        "path_review_hunks": hunks,
                    }
                ],
            }
        ],
    }

    plan = repo_review_progress.build_review_plan(packet)

    assert_field(plan["review_batching"], "source_review_unit_count", 20)
    assert_field(plan["review_batching"], "batched_review_unit_count", 2)
    assert_field(plan["review_batching"], "max_hunks_per_batch", 12)
    assert len(plan["review_units"][0]["hunks"]) == 12
    assert len(plan["review_units"][1]["hunks"]) == 8


def test_review_plan_batches_path_only_subpackets_under_review_budget(tmp):
    def path_packet(path: str, tokens: int) -> dict[str, object]:
        return {
            "schema_version": 1,
            "tool": "skill-manager.owner-review-subpacket",
            "owner": "workflow:navigation",
            "scope": "path",
            "path": path,
            "status": "within-budget",
            "priority": "low",
            "estimated_changed_tokens": tokens,
            "review_budget_tokens": 2500,
            "risk_counts": {"low": 1},
            "read_first": [{"path": path, "owner": "workflow:navigation", "risk": "low"}],
            "paths": [path],
            "validation_first": ["python -B .agents/manage.py check-changed --summary --compact --format json"],
            "path_review_hunks": [],
            "path_summary_command": repo_changed.owner_review_command("workflow:navigation", [path]),
            "next_command": repo_changed.owner_review_command("workflow:navigation", [path]),
        }

    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_diff_estimated_tokens": 8000,
        "review_budget_tokens": 2500,
        "owner_review_packets": [
            {
                "owner": "workflow:navigation",
                "status": "over-budget",
                "scope": "owner",
                "estimated_changed_tokens": 4000,
                "review_budget_tokens": 2500,
                "owner_summary_command": repo_changed.owner_review_command("workflow:navigation"),
                "owner_review_subpackets": [
                    path_packet("automations/navigation/artifacts/maps/a.md", 1000),
                    path_packet("automations/navigation/artifacts/maps/b.md", 1000),
                    path_packet("automations/navigation/artifacts/maps/c.md", 1000),
                    path_packet("automations/navigation/artifacts/maps/d.md", 1000),
                ],
            }
        ],
    }

    plan = repo_review_progress.build_review_plan(packet)
    first_unit = plan["review_units"][0]

    assert_field(plan, "review_unit_count", 2)
    assert_field(plan["review_batching"], "source_review_unit_count", 4)
    assert_field(plan["review_batching"], "saved_review_unit_count", 2)
    assert_field(plan["review_batching"], "path_batch_count", 2)
    assert_field(first_unit, "scope", "path-batch")
    assert first_unit["paths"] == [
        "automations/navigation/artifacts/maps/a.md",
        "automations/navigation/artifacts/maps/b.md",
    ]
    assert_field(first_unit, "estimated_changed_tokens", 2000)
    assert_has_all(
        first_unit["command"],
        "--path automations/navigation/artifacts/maps/a.md",
        "--path automations/navigation/artifacts/maps/b.md",
    )


def test_review_loop_forecast_uses_pending_token_lower_bound_for_projected_loops(tmp):
    review_units = []
    for index, tokens in enumerate([100, 100, 100, 100, 100, 5000, 5000, 5000, 5000, 5000], start=1):
        review_units.append(
            {
                "id": f"review:{index:03d}",
                "scope": "path",
                "owner": "skill:skill-manager",
                "estimated_changed_tokens": tokens,
                "command": (
                    "python -B .agents/manage.py review-packet "
                    f"--owner skill:skill-manager --path src/{index}.py --summary --compact --format json"
                ),
            }
        )
    plan = {
        "schema_version": 1,
        "tool": "skill-manager.review-plan",
        "review_units": review_units,
        "validation_units": [],
    }

    forecast = repo_review_progress.build_review_loop_forecast(
        plan,
        max_units=20,
        max_estimated_tokens=8000,
    )

    assert_field(forecast, "planned_review_unit_count", 6)
    assert_field(forecast, "pending_review_tokens_estimated", 25500)
    assert_field(forecast, "projected_loop_count", 4)


def test_next_action_routes_to_review_progress_before_raw_diff(tmp):
    review_packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_diff_estimated_tokens": 9000,
        "review_budget_tokens": 5000,
        "review_batch_max_hunks": 1,
        "validation_first": [],
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "status": "within-budget",
                "scope": "owner",
                "changed_file_count": 1,
                "estimated_changed_tokens": 120,
                "owner_summary_command": "python -B .agents/manage.py review-packet --owner skill:skill-manager --summary --compact --format json",
            }
        ],
    }
    context = {
        "changed": ["AGENTS.md"],
        "scope": {},
        "validation_plan": [],
        "navigation": {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
        },
        "review_packet": review_packet,
        "input_fingerprint": {"digest": "digest-a"},
        "review_plan": repo_review_progress.build_review_plan(review_packet),
    }
    def fail_dashboard(*_args, **_kwargs):
        raise AssertionError("default next-action must not run dashboard checks")

    with patched_attrs(
        repo_qol,
        dashboard_report=fail_dashboard,
        current_review_plan_packet=lambda root: context,
    ):
        report = repo_qol.next_action_report(tmp)

    assert_has_all(report["next_command"], "review-loop", "--max-units 20")
    assert_has_all(report["required_context"], "automations/navigation/artifacts/maps/HANDOFF.md")
    assert_field(report["local_ai_route"], "status", "advisory-only")
    assert_field(report["review_autopilot"], "status", "default")
    assert_has_all(report["validation_after"], "review-progress --summary")


def test_next_action_uses_policy_review_loop_defaults_and_reports_forecast(tmp):
    policy = repo_cost_policy.default_cost_policy()
    policy["review_loop"]["max_units"] = 7
    policy["review_loop"]["max_estimated_tokens"] = 900
    policy["review_loop"]["max_elapsed_ms"] = 45000
    write_cost_policy_fixture(tmp, policy)
    review_packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_diff_estimated_tokens": 9000,
        "review_budget_tokens": 5000,
        "review_batch_max_hunks": 1,
        "validation_first": [],
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "status": "over-budget",
                "scope": "owner",
                "changed_file_count": 2,
                "estimated_changed_tokens": 1200,
                "owner_review_subpackets": [
                    {
                        "path": "src/demo.py",
                        "estimated_changed_tokens": 1200,
                        "path_review_hunks": [
                            {
                                "path": "src/demo.py",
                                "hunk": f"h{index:03d}",
                                "estimated_changed_tokens": 200,
                                "next_command": (
                                    "python -B .agents/manage.py review-packet "
                                    f"--owner skill:skill-manager --path src/demo.py --hunk h{index:03d} "
                                    "--summary --compact --format json"
                                ),
                            }
                            for index in range(1, 7)
                        ],
                    }
                ],
            }
        ],
    }
    context = {
        "changed": ["src/demo.py"],
        "scope": {},
        "validation_plan": [],
        "navigation": {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
        },
        "review_packet": review_packet,
        "input_fingerprint": {"digest": "digest-a"},
        "review_plan": repo_review_progress.build_review_plan(review_packet),
    }

    with patched_attrs(repo_qol, current_review_plan_packet=lambda root: context):
        report = repo_qol.next_action_report(tmp)

    assert_has_all(
        report["next_command"],
        "review-loop",
        "--max-units 7",
        "--max-estimated-tokens 900",
        "--max-elapsed-ms 45000",
    )
    assert_fields(report["review_autopilot"], max_units=7, max_estimated_tokens=900, max_elapsed_ms=45000)
    assert_field(report["review_autopilot"]["forecast"], "planned_unit_count", 4)
    assert_field(report["review_autopilot"]["forecast"], "planned_estimated_tokens", 800)
    assert_field(report["review_owner_forecast"], "top_owner", "skill:skill-manager")
    assert_field(report["review_owner_forecast"], "projected_loop_count", 2)
    assert_has_all(report["review_owner_forecast"]["first_review_command"], "review-packet", "--hunk h001")


def test_review_plan_respects_policy_batch_hunk_limit(tmp):
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_diff_estimated_tokens": 10000,
        "review_budget_tokens": 5000,
        "review_batch_max_hunks": 4,
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "status": "over-budget",
                "scope": "owner",
                "estimated_changed_tokens": 2400,
                "owner_review_subpackets": [
                    {
                        "path": "src/demo.py",
                        "estimated_changed_tokens": 2400,
                        "path_review_hunks": [
                            {
                                "path": "src/demo.py",
                                "hunk": f"h{index:03d}",
                                "estimated_changed_tokens": 100,
                                "next_command": (
                                    "python -B .agents/manage.py review-packet "
                                    f"--owner skill:skill-manager --path src/demo.py --hunk h{index:03d} "
                                    "--summary --compact --format json"
                                ),
                            }
                            for index in range(1, 11)
                        ],
                    }
                ],
            }
        ],
    }

    plan = repo_review_progress.build_review_plan(packet)

    assert_field(plan["review_batching"], "source_review_unit_count", 10)
    assert_field(plan["review_batching"], "batched_review_unit_count", 3)
    assert_field(plan["review_batching"], "max_hunks_per_batch_limit", 4)
    assert_field(plan["review_batching"], "max_hunks_per_batch", 4)


def test_selected_owner_packet_respects_policy_batch_hunk_limit(tmp):
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.owner-review-packet",
        "owner": "skill:skill-manager",
        "status": "over-budget",
        "changed_diff_estimated_tokens": 10000,
        "review_budget_tokens": 5000,
        "review_batch_max_hunks": 4,
        "owner_review_subpackets": [
            {
                "path": "src/demo.py",
                "estimated_changed_tokens": 1000,
                "path_review_hunks": [
                    {
                        "path": "src/demo.py",
                        "hunk": f"h{index:03d}",
                        "estimated_changed_tokens": 100,
                        "next_command": (
                            "python -B .agents/manage.py review-packet "
                            f"--owner skill:skill-manager --path src/demo.py --hunk h{index:03d} "
                            "--summary --compact --format json"
                        ),
                    }
                    for index in range(1, 11)
                ],
            }
        ],
    }

    plan = repo_review_progress.build_review_plan(packet)

    assert_field(plan["review_batching"], "source_review_unit_count", 10)
    assert_field(plan["review_batching"], "batched_review_unit_count", 3)
    assert_field(plan["review_batching"], "max_hunks_per_batch_limit", 4)
    assert len(plan["review_units"][0]["hunks"]) == 4


def test_review_cost_report_uses_batched_next_unit_estimate(tmp):
    hunk_packets = [
        {
            "path": "src/demo.py",
            "hunk": f"h{index:03d}",
            "estimated_changed_tokens": tokens,
            "next_command": (
                "python -B .agents/manage.py review-packet "
                f"--owner skill:skill-manager --path src/demo.py --hunk h{index:03d} "
                "--summary --compact --format json"
            ),
        }
        for index, tokens in enumerate((100, 200, 300), start=1)
    ]
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_diff_estimated_tokens": 5000,
        "review_budget_tokens": 5000,
        "review_batch_max_hunks": 2,
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "status": "within-budget",
                "scope": "owner",
                "estimated_changed_tokens": 600,
                "owner_review_subpackets": [
                    {
                        "path": "src/demo.py",
                        "estimated_changed_tokens": 600,
                        "path_review_hunks": hunk_packets,
                    }
                ],
            }
        ],
    }
    packet["cost_ledger"] = repo_cost_policy.review_cost_ledger(packet)

    plan = repo_review_progress.build_review_plan(packet)
    cost_report = repo_review_progress.build_review_cost_report(packet)

    assert_field(plan["cost_ledger"], "source_review_unit_count", 3)
    assert_field(plan["cost_ledger"], "batched_review_unit_count", 2)
    assert_field(plan["cost_ledger"], "next_review_unit_estimated_tokens", 300)
    assert_field(plan["cost_ledger"], "largest_review_unit_estimated_tokens", 300)
    assert_field(cost_report, "next_review_unit_estimated_tokens", 300)
    assert_field(cost_report["break_even_extra_output_tokens"], "output_price_multiplier_4x", (5000 - 300) // 4)


def test_review_cost_report_includes_output_price_scenarios(tmp):
    hunk_packets = [
        {
            "path": "src/demo.py",
            "hunk": f"h{index:03d}",
            "estimated_changed_tokens": tokens,
            "next_command": (
                "python -B .agents/manage.py review-packet "
                f"--owner skill:skill-manager --path src/demo.py --hunk h{index:03d} "
                "--summary --compact --format json"
            ),
        }
        for index, tokens in enumerate((100, 200, 300), start=1)
    ]
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_diff_estimated_tokens": 5000,
        "review_budget_tokens": 5000,
        "review_batch_max_hunks": 2,
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "status": "within-budget",
                "scope": "owner",
                "estimated_changed_tokens": 600,
                "owner_review_subpackets": [
                    {
                        "path": "src/demo.py",
                        "estimated_changed_tokens": 600,
                        "path_review_hunks": hunk_packets,
                    }
                ],
            }
        ],
    }
    packet["cost_ledger"] = repo_cost_policy.review_cost_ledger(packet)

    cost_report = repo_review_progress.build_review_cost_report(packet)

    estimate = cost_report["money_saving_estimate"]
    scenarios = {row["output_price_multiplier"]: row for row in estimate["scenarios"]}
    assert_field(estimate, "billing_scope", "scenario-not-provider-telemetry")
    assert_field(estimate, "input_tokens_saved", 4700)
    assert_field(estimate, "assumed_extra_output_tokens", 1200)
    assert_field(scenarios[1], "net_input_token_equivalent_savings", 3500)
    assert_field(scenarios[4], "net_input_token_equivalent_savings", -100)
    assert_field(scenarios[4], "status", "not-proven")


def test_dashboard_summary_uses_batched_review_plan_cost_ledger(tmp):
    hunk_packets = [
        {
            "path": "src/demo.py",
            "hunk": f"h{index:03d}",
            "estimated_changed_tokens": tokens,
            "next_command": (
                "python -B .agents/manage.py review-packet "
                f"--owner skill:skill-manager --path src/demo.py --hunk h{index:03d} "
                "--summary --compact --format json"
            ),
        }
        for index, tokens in enumerate((100, 200, 300), start=1)
    ]
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_file_count": 1,
        "changed_diff_estimated_tokens": 5000,
        "review_budget_tokens": 5000,
        "review_batch_max_hunks": 2,
        "owner_review_packet_count": 1,
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "status": "within-budget",
                "scope": "owner",
                "estimated_changed_tokens": 600,
                "owner_review_subpackets": [
                    {
                        "path": "src/demo.py",
                        "estimated_changed_tokens": 600,
                        "path_review_hunks": hunk_packets,
                    }
                ],
            }
        ],
    }
    packet["cost_ledger"] = repo_cost_policy.review_cost_ledger(packet)
    report = healthy_dashboard_fixture()
    report["review_packet"] = packet
    report["review_progress"] = {}

    compact = repo_qol.summarize_dashboard_report(report, compact=True)

    ledger = compact["review_packet"]["cost_ledger"]
    owner_context = compact["review_packet"]["affected_owner_context"]
    assert_field(ledger, "source_review_unit_count", 3)
    assert_field(ledger, "batched_review_unit_count", 2)
    assert_field(ledger, "next_review_unit_estimated_tokens", 300)
    assert_field(compact["review_packet"]["review_cost_report"], "next_review_unit_estimated_tokens", 300)
    assert_field(compact["review_packet"]["review_cost_report"]["money_saving_estimate"], "default_output_price_multiplier", 4)
    assert_field(owner_context, "status", "present")
    assert_field(owner_context, "owner_count", 1)
    assert_field(owner_context["owners"][0], "capsule", "automations/navigation/artifacts/maps/owners/skill-skill-manager.md")
    assert_has_all(owner_context["owners"][0]["next_command"], "review-packet", "--owner skill:skill-manager")
    assert_summary(compact, review_next_unit_saved_tokens_estimated=4700)


def test_next_action_summary_reports_latency_and_output_budget(tmp):
    context = {
        "changed": [],
        "scope": {},
        "validation_plan": [],
        "navigation": {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
        },
        "review_packet": {"status": "no-changes"},
        "input_fingerprint": {"digest": "digest-a"},
        "review_plan": repo_review_progress.build_review_plan({"status": "no-changes"}),
    }

    with patched_attrs(repo_qol, current_review_plan_packet=lambda root: context):
        report = repo_qol.next_action_report(tmp)

    compact = repo_qol.summarize_next_action_report(report, compact=True)

    assert_field(compact["latency_budget"], "command", "next-action")
    assert_field(compact["latency_budget"], "budget_ms", repo_command_metrics.LATENCY_BUDGETS_MS["next-action"])
    assert_field(compact["output_budget"], "command", "next-action")
    assert_field(compact["output_budget"], "budget_tokens", repo_command_metrics.OUTPUT_BUDGETS_TOKENS["next-action"])
    assert_field(compact["output_budget"], "scope", "summary-compact-json-estimate")
    assert_field(compact["context_trace"], "read_first", "automations/navigation/artifacts/maps/HANDOFF.md")
    assert_contains(compact["context_trace"]["read_now"], "AGENTS.md")
    assert_contains(compact["context_trace"]["read_now"], "automations/navigation/artifacts/maps/HANDOFF.md")
    assert_contains(compact["context_trace"]["skip_raw_json"], "automations/navigation/artifacts/maps/handoff.json")
    assert_contains(compact["context_trace"]["skip_raw_json"], "automations/navigation/artifacts/maps/staleness.json")
    assert_contains(compact["context_trace"]["skip_raw_json"], "automations/navigation/artifacts/maps/project-map.json")
    assert_contains(compact["context_trace"]["skip_raw_json"], "automations/navigation/artifacts/maps/code-graph.json")
    assert "local_ai_route" not in compact


def test_changed_evidence_summary_is_budgeted_and_routes_navigation(tmp):
    suggestions = [
        {
            "kind": f"kind-{index}",
            "owner": "skill-manager",
            "command": f"python -B .agents/manage.py tool-{index}",
            "path_count": index + 1,
            "paths": [f"path-{index}-{path}.py" for path in range(4)],
        }
        for index in range(7)
    ]
    report = {
        "schema_version": 1,
        "tool": "repo-changed-evidence",
        "ok": True,
        "changed_file_count": 7,
        "changed_groups": "src/ (7)",
        "suggestions": suggestions,
        "navigation": {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
        },
        "latency_budget": repo_command_metrics.timing_budget_report("changed-evidence", 12.0),
        "validation_router": {
            "status": "planned",
            "summary": {"required_count": 1, "optional_count": 0},
            "next_command": "python -B .agents/manage.py check-additions",
        },
        "fallback_command": "python -B .agents/manage.py check-changed --deep",
        "next_command": "python -B .agents/manage.py finish",
    }

    compact = repo_qol_daily.summarize_changed_evidence_report(report, compact=True)

    assert_field(compact["latency_budget"], "command", "changed-evidence")
    assert_field(compact["output_budget"], "command", "changed-evidence")
    assert_field(compact["output_budget"], "budget_tokens", 1400)
    assert_field(compact["output_budget"], "status", "within-budget")
    assert len(compact["suggestions"]) == 5
    assert_field(compact, "omitted_suggestion_count", 2)
    assert_field(compact["context_trace"], "read_first", "automations/navigation/artifacts/maps/HANDOFF.md")
    assert_contains(compact["context_trace"]["read_now"], "AGENTS.md")
    assert_contains(compact["context_trace"]["skip_raw_json"], "automations/navigation/artifacts/maps/handoff.json")


def test_changed_evidence_routes_next_command_to_validation_router(tmp):
    validation_plan = [
        {
            "owner": "skill-manager",
            "command": "python -B .agents/manage.py check-additions",
            "required": True,
        }
    ]

    with patched_attrs(repo_qol_daily.repo_changed, changed_files=lambda root: ["src/app.py"]), patched_attrs(
        repo_qol_daily.repo_changed,
        changed_scope=lambda changed: {"files": changed},
    ), patched_attrs(
        repo_qol_daily.repo_optimizations,
        changed_validation_plan=lambda root, changed, scope, deep=False: validation_plan,
        validation_plan_summary=lambda plan: {"required_count": 1, "optional_count": 0, "command_count": 1},
    ), patched_attrs(
        repo_qol_daily,
        input_fingerprint_report=lambda root, changed, commands: {"digest": "digest-a", "changed_file_count": 1},
        startup_navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
        },
    ):
        report = repo_qol_daily.changed_evidence_report(tmp)

    assert_field(report, "next_command", "python -B .agents/manage.py check-additions")
    assert_field(report["validation_router"], "next_command", "python -B .agents/manage.py check-additions")


def test_command_budget_check_runs_budgeted_compact_commands(tmp):
    calls: list[list[str]] = []

    def fake_runner(command, **_kwargs):
        record_command(calls, command)
        command_id = "startup-context"
        if "status" in command:
            command_id = "status-fast"
        elif "next-action" in command:
            command_id = "next-action"
        elif "context-use-check" in command:
            command_id = "context-use-check"
        elif "workflow" in command and "smoke" in command:
            command_id = "smoke-workflows"
        payload = {
            "schema_version": 1,
            "tool": command_id,
            "ok": True,
            "status": "ready",
            "latency_budget": {
                "command": command_id,
                "status": "within-budget",
                "elapsed_ms": 10,
                "budget_ms": repo_command_metrics.LATENCY_BUDGETS_MS[command_id],
            },
            "output_budget": {
                "command": command_id,
                "status": "within-budget",
                "estimated_output_tokens": 100,
                "budget_tokens": repo_command_metrics.OUTPUT_BUDGETS_TOKENS[command_id],
            },
        }
        return completed(command, stdout=json.dumps(payload))

    report = repo_command_metrics.command_budget_regression_report(tmp, profile="fast", runner=fake_runner)
    compact = repo_command_metrics.summarize_command_budget_regression_report(report, compact=True)

    assert_ok(report)
    assert_field(report, "command_count", 5)
    assert_field(compact["summary"], "failed_command_count", 0)
    assert_has_all(compact["covered_command_ids"], "status-fast", "startup-context", "next-action", "smoke-workflows", "context-use-check")
    assert_lacks_all(compact["covered_command_ids"], "finish")
    assert_field(compact["output_budget"], "command", "command-budget-check")
    assert_has_all(
        repo_command_metrics.command_budget_ids_for_profile("standard"),
        "changed-evidence",
        "review-loop",
        "review-autopilot",
        "check-changed",
    )


def test_command_budget_check_fails_when_aggregate_latency_regresses(tmp):
    def fake_runner(command, **_kwargs):
        payload = {
            "schema_version": 1,
            "tool": "next-action",
            "ok": True,
            "status": "ready",
            "latency_budget": {
                "command": "next-action",
                "status": "within-budget",
                "elapsed_ms": 10,
                "budget_ms": repo_command_metrics.LATENCY_BUDGETS_MS["next-action"],
            },
            "output_budget": {
                "command": "next-action",
                "status": "within-budget",
                "estimated_output_tokens": 100,
                "budget_tokens": repo_command_metrics.OUTPUT_BUDGETS_TOKENS["next-action"],
            },
        }
        return completed(command, stdout=json.dumps(payload))

    def over_budget_latency(command_id, total_elapsed_ms, **_kwargs):
        assert_field({"command_id": command_id}, "command_id", "command-budget-check")
        return {
            "command": command_id,
            "status": "over-budget",
            "elapsed_ms": 181000,
            "budget_ms": repo_command_metrics.LATENCY_BUDGETS_MS["command-budget-check"],
            "over_budget_ms": 1000,
        }

    with patched_attrs(repo_command_metrics, timing_budget_report=over_budget_latency):
        report = repo_command_metrics.command_budget_regression_report(
            tmp,
            command_ids=["next-action"],
            runner=fake_runner,
        )

    compact = repo_command_metrics.summarize_command_budget_regression_report(report, compact=True)

    assert_not_ok(report)
    assert_field(report, "status", "failed")
    assert_contains(report["issues"], "command-budget-check: latency budget is over-budget")
    assert_field(compact["latency_budget"], "status", "over-budget")
    assert_contains(compact["issues"], "command-budget-check: latency budget is over-budget")


def test_next_action_compact_summary_trims_review_progress_noise(tmp):
    long_validation_command = (
        "python -B .agents/manage.py syntax-check --paths "
        + " ".join(f"src/module_{index}.py" for index in range(80))
        + " --format json"
    )
    report = {
        "schema_version": 1,
        "tool": "skill-manager.next-action",
        "ok": True,
        "status": "ready",
        "next_command": "python -B .agents/manage.py review-loop --summary --compact --format json",
        "why": "changed diff exceeds review budget",
        "required_context": ["automations/navigation/artifacts/maps/HANDOFF.md"],
        "validation_after": "python -B .agents/manage.py review-progress --summary --compact --format json",
        "stop_condition": "Stop on failure.",
        "navigation": {"status": "fresh", "read_first": "automations/navigation/artifacts/maps/HANDOFF.md"},
        "context_trace": {"status": "fresh", "read_first": "automations/navigation/artifacts/maps/HANDOFF.md"},
        "latency_budget": {"command": "next-action", "status": "within-budget"},
        "review_progress": {
            "status": "in-progress",
            "review_state": "partial",
            "completed_unit_count": 1,
            "pending_unit_count": 2,
            "stale": False,
            "state_path": ".agents/local-ai/cache/review-progress.json",
            "review_batching": {"source_review_unit_count": 99},
            "current_unit": {"scope": "hunk-batch", "owner": "skill:demo", "path": "src/demo.py"},
            "next_pending_command": long_validation_command,
        },
        "review_autopilot": {"status": "default", "forecast": {"remaining_review_units": 2}},
        "review_owner_forecast": {"owners": [{"owner": "skill:demo", "remaining_review_units": 2}]},
        "local_ai_route": {"status": "advisory-only"},
    }

    seen_roots: list[Path | None] = []
    original_compact_next_command = repo_qol_context.repo_changed.compact_next_command

    def capture_compact_next_command(command, *, root=None):
        seen_roots.append(root)
        return original_compact_next_command(command, root=root)

    with patched_attrs(
        repo_qol_context.repo_changed,
        compact_next_command=capture_compact_next_command,
    ):
        compact = repo_qol_context.summarize_next_action_report(
            report,
            compact=True,
            root=tmp,
        )

    assert_keys_lack(compact, "review_owner_forecast", "local_ai_route")
    assert_keys_lack(compact["review_progress"], "state_path", "review_batching")
    assert_has_all(compact["review_progress"], "status", "pending_unit_count", "current_unit", "next_pending_command")
    assert_field(
        compact["review_progress"],
        "next_pending_command",
        "python -B .agents/manage.py check-changed --record-progress --summary --compact --format json",
    )
    assert seen_roots and all(root == tmp for root in seen_roots), seen_roots
    assert_field(compact["output_budget"], "command", "next-action")


def test_compact_next_command_preserves_short_and_routes_oversized_commands(tmp):
    seen_roots: list[Path] = []

    def fixed_limit(root, dotted_path):
        seen_roots.append(root)
        assert dotted_path == "limits.context.validation_command_chars"
        return 80

    short_command = "python -B .agents/manage.py check-additions"
    long_review_command = (
        "python -B .agents/manage.py review-packet --owner skill:demo "
        + " ".join(f"--path src/module_{index}.py" for index in range(20))
    )
    long_validation_command = (
        "python -B .agents/manage.py syntax-check --paths "
        + " ".join(f"src/module_{index}.py" for index in range(20))
    )

    with patched_attrs(repo_changed.repo_policy, int_value=fixed_limit):
        preserved = repo_changed.compact_next_command(short_command, root=tmp)
        review = repo_changed.compact_next_command(long_review_command, root=tmp)
        validation = repo_changed.compact_next_command(long_validation_command, root=tmp)

    assert preserved == short_command
    assert review == (
        "python -B .agents/manage.py review-loop --max-units 1 "
        "--summary --compact --format json"
    )
    assert validation == (
        "python -B .agents/manage.py check-changed --record-progress "
        "--summary --compact --format json"
    )
    assert seen_roots == [tmp, tmp, tmp], seen_roots


def test_command_budget_check_allows_blocked_review_autopilot_returncode(tmp):
    def fake_runner(command, **_kwargs):
        payload = {
            "schema_version": 1,
            "tool": "skill-manager.review-autopilot",
            "ok": False,
            "status": "blocked",
            "latency_budget": {
                "command": "review-autopilot",
                "status": "within-budget",
                "elapsed_ms": 10,
                "budget_ms": repo_command_metrics.LATENCY_BUDGETS_MS["review-autopilot"],
            },
            "output_budget": {
                "command": "review-autopilot",
                "status": "within-budget",
                "estimated_output_tokens": 100,
                "budget_tokens": repo_command_metrics.OUTPUT_BUDGETS_TOKENS["review-autopilot"],
            },
        }
        return completed(command, returncode=1, stdout=json.dumps(payload))

    report = repo_command_metrics.command_budget_regression_report(
        tmp,
        command_ids=["review-autopilot"],
        runner=fake_runner,
    )

    assert_ok(report)
    assert_field(report["commands"][0], "semantic_status", "blocked")


def test_command_budget_check_covers_review_loop_dry_run(tmp):
    def fake_runner(command, **_kwargs):
        assert "review-loop" in command, command
        assert "--dry-run" in command, command
        payload = {
            "schema_version": 1,
            "tool": "skill-manager.review-loop",
            "ok": True,
            "status": "planned",
            "latency_budget": {
                "command": "review-loop",
                "status": "within-budget",
                "elapsed_ms": 10,
                "budget_ms": repo_command_metrics.LATENCY_BUDGETS_MS["review-loop"],
            },
            "output_budget": {
                "command": "review-loop",
                "status": "within-budget",
                "estimated_output_tokens": 100,
                "budget_tokens": repo_command_metrics.OUTPUT_BUDGETS_TOKENS["review-loop"],
            },
        }
        return completed(command, stdout=json.dumps(payload))

    report = repo_command_metrics.command_budget_regression_report(
        tmp,
        command_ids=["review-loop"],
        runner=fake_runner,
    )

    assert_ok(report)
    assert_field(report["commands"][0], "command_id", "review-loop")


def test_command_budget_check_covers_workflow_smoke_dry_run(tmp):
    def fake_runner(command, **_kwargs):
        assert_has_all(command, "workflow", "smoke", "--dry-run", "--summary", "--compact")
        payload = {
            "schema_version": 1,
            "tool": "workflow-manager.smoke",
            "ok": True,
            "status": "planned",
            "latency_budget": {
                "command": "smoke-workflows",
                "status": "within-budget",
                "elapsed_ms": 10,
                "budget_ms": repo_command_metrics.LATENCY_BUDGETS_MS["smoke-workflows"],
            },
            "output_budget": {
                "command": "smoke-workflows",
                "status": "within-budget",
                "estimated_output_tokens": 100,
                "budget_tokens": repo_command_metrics.OUTPUT_BUDGETS_TOKENS["smoke-workflows"],
            },
        }
        return completed(command, stdout=json.dumps(payload))

    report = repo_command_metrics.command_budget_regression_report(
        tmp,
        command_ids=["smoke-workflows"],
        runner=fake_runner,
    )

    assert_ok(report)
    assert_field(report["commands"][0], "command_id", "smoke-workflows")


def test_command_budget_check_fails_when_nested_output_budget_regresses(tmp):
    def fake_runner(command, **_kwargs):
        payload = {
            "latency_budget": {
                "command": "next-action",
                "status": "within-budget",
                "elapsed_ms": 10,
                "budget_ms": 12000,
            },
            "output_budget": {
                "command": "next-action",
                "status": "over-budget",
                "estimated_output_tokens": 2000,
                "budget_tokens": 1200,
            },
        }
        return completed(command, stdout=json.dumps(payload))

    report = repo_command_metrics.command_budget_regression_report(
        tmp,
        command_ids=["next-action"],
        runner=fake_runner,
    )

    assert_not_ok(report)
    assert_contains(report["issues"], "output budget is over-budget")


def test_context_cost_benchmark_is_required_for_low_context_support_changes(tmp):
    write_cost_policy_fixture(tmp)
    paths = [
        ".agents/skills/skill-manager/scripts/repo_support/repo_qol_context.py",
        ".agents/skills/skill-manager/scripts/repo_support/repo_review_progress.py",
    ]

    assert repo_optimizations.startup_context_inputs_changed(tmp, paths)
    plan = repo_optimizations.changed_validation_plan(
        tmp,
        paths,
        {"python_paths": paths, "skill_names": {"skill-manager"}},
        deep=False,
    )

    commands = [str(item.get("command", "")) for item in plan]
    assert_contains(commands, "context-cost-benchmark")
    assert_contains(commands, "startup-context --baseline-ref HEAD")


def test_claim_check_rejects_unproved_completion_claims(tmp):
    evidence = {
        "schema_version": 1,
        "tool": "repo-finish",
        "status": "passed",
        "navigation": {"status": "fresh"},
        "progress_events": [
            {"phase": "repo-check", "ok": True},
            {
                "command": "python -B .agents/manage.py workflow smoke --name user-story-workflow --summary --compact --format json",
                "ok": True,
                "status": "passed",
            },
        ],
    }
    write_json(tmp / "finish.json", evidence)

    passed = repo_qol.claim_check_report(
        tmp,
        text="finish passed, check passed, navigation maps are fresh, and user-story workflow smoke passed",
        evidence_files=["finish.json"],
    )
    failed = repo_qol.claim_check_report(
        tmp,
        text="fresh subagent validation passed",
        evidence_files=["finish.json"],
    )

    assert_field(passed, "status", "passed")
    assert_field(failed, "status", "failed")
    assert any(item["claim"] == "fresh subagent validation passed" for item in failed["claims"])


def test_context_guardrails_reject_raw_navigation_json_routing(tmp):
    write_text(tmp / "docs" / "bad.md", "# Bad\n\nRead project-map.json first for orientation.")
    write_text(tmp / "docs" / "good.md", "# Good\n\nproject-map.json is tool-only; do not load raw navigation JSON.")
    write_text(tmp / "AGENTS.md", "# Repo\n\n- Skip generated `registry.json` for normal routing.\n")

    report = repo_context_guardrails.context_guardrail_report(
        tmp,
        paths=["docs/bad.md", "docs/good.md", "AGENTS.md"],
    )

    assert_field(report, "status", "failed")
    assert_field(report, "finding_count", 1)
    assert_field(report["findings"][0], "path", "docs/bad.md")


def test_context_guardrails_allow_structured_module_output_declarations(tmp):
    write_text(
        tmp / "automations" / "navigation" / "module.json",
        '{"outputs":["artifacts/maps/handoff.json","artifacts/maps/staleness.json"]}\n',
    )

    report = repo_context_guardrails.context_guardrail_report(
        tmp,
        paths=["automations/navigation/module.json"],
        include_protected=False,
    )

    assert_ok(report)


def test_context_guardrails_skip_harness_payload_machine_contract(tmp):
    write_text(
        tmp / ".agents" / "harness-payload.json",
        '{"exclude_globs":[".agents/registry.json","automations/registry.json"]}\n',
    )

    report = repo_context_guardrails.context_guardrail_report(
        tmp,
        paths=[".agents/harness-payload.json"],
        include_protected=False,
    )

    assert_ok(report)
    assert_field(report, "scanned_count", 0)
    assert_field(report, "skipped_count", 1)


def test_context_guardrails_reject_broad_raw_diff_routing(tmp):
    write_text(tmp / "docs" / "bad.md", "# Bad\n\nOpen the full git diff before choosing a route.")
    write_text(tmp / "docs" / "good.md", "# Good\n\nUse review-packet before broad git diff review.")

    report = repo_context_guardrails.context_guardrail_report(
        tmp,
        paths=["docs/bad.md", "docs/good.md"],
    )

    assert_field(report, "status", "failed")
    assert_field(report, "finding_count", 1)
    assert_field(report["findings"][0], "path", "docs/bad.md")
    assert "raw diff" in report["findings"][0]["issue"]


def test_context_guardrails_reject_marker_collision_false_negatives(tmp):
    write_text(
        tmp / "docs" / "bad-nav.md",
        "# Bad Navigation\n\nLoad handoff.json as raw navigation JSON for orientation.",
    )
    write_text(
        tmp / "docs" / "bad-diff.md",
        "# Bad Diff\n\nReview the full git diff evidence before routing.",
    )
    write_text(
        tmp / "docs" / "safe.md",
        "# Safe\n\nhandoff.json is tool-only; do not load raw navigation JSON. Use review-packet before broad git diff review.",
    )

    report = repo_context_guardrails.context_guardrail_report(
        tmp,
        paths=["docs/bad-nav.md", "docs/bad-diff.md", "docs/safe.md"],
    )

    assert_field(report, "status", "failed")
    assert_field(report, "finding_count", 2)
    finding_paths = {item["path"] for item in report["findings"]}
    assert finding_paths == {"docs/bad-nav.md", "docs/bad-diff.md"}


def test_context_guardrails_scan_protected_adapter_surfaces_by_default(tmp):
    write_text(
        tmp / ".github" / "copilot-instructions.md",
        "Read automations/navigation/artifacts/maps/code-graph.json before implementation.",
    )
    write_text(tmp / "docs" / "unrelated.md", "# Unrelated\n\nNo navigation guidance.\n")

    report = repo_context_guardrails.context_guardrail_report(
        tmp,
        paths=["docs/unrelated.md"],
    )
    changed_only = repo_context_guardrails.context_guardrail_report(
        tmp,
        paths=["docs/unrelated.md"],
        include_protected=False,
    )

    assert_not_ok(report)
    assert_field(report["findings"][0], "path", ".github/copilot-instructions.md")
    assert_ok(changed_only)


def test_context_guardrails_load_snippet_policy_once_per_report(tmp):
    write_text(
        tmp / "docs" / "many-lines.md",
        "\n".join(f"ordinary line {index}" for index in range(200)),
    )
    calls = {"count": 0}

    def counted_int_value(_root, dotted_path):
        assert dotted_path == "limits.output.evidence_snippet_chars"
        calls["count"] += 1
        return 240

    with patched_attrs(repo_context_guardrails.repo_policy, int_value=counted_int_value):
        report = repo_context_guardrails.context_guardrail_report(
            tmp,
            paths=["docs/many-lines.md"],
            include_protected=False,
        )

    assert_ok(report)
    assert calls["count"] == 1, calls


def test_context_use_check_proves_handoff_and_raw_json_skips(tmp):
    def fake_startup(_root, **_kwargs):
        return {
            "ok": True,
            "status": "passed",
            "navigation": {
                "status": "fresh",
                "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
                "next_command": "none, navigation maps are fresh",
            },
            "context_trace": {
                "status": "fresh",
                "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
                "read_now": ["AGENTS.md", "automations/navigation/artifacts/maps/HANDOFF.md"],
                "skip_raw_json": [
                    "automations/navigation/artifacts/maps/handoff.json",
                    "automations/navigation/artifacts/maps/staleness.json",
                    "automations/navigation/artifacts/maps/project-map.json",
                    "automations/navigation/artifacts/maps/code-graph.json",
                ],
                "next_command": "none, navigation maps are fresh",
            },
        }

    def fake_next_action(_root, **_kwargs):
        return {
            "ok": True,
            "status": "ready",
            "next_command": "python -B .agents/manage.py review-loop --summary --compact --format json",
            "context_trace": {
                "status": "fresh",
                "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
                "read_now": ["AGENTS.md", "automations/navigation/artifacts/maps/HANDOFF.md"],
                "skip_raw_json": [
                    "automations/navigation/artifacts/maps/handoff.json",
                    "automations/navigation/artifacts/maps/staleness.json",
                    "automations/navigation/artifacts/maps/project-map.json",
                    "automations/navigation/artifacts/maps/code-graph.json",
                ],
            },
        }

    report = repo_context_guardrails.context_use_check_report(
        tmp,
        startup_factory=fake_startup,
        next_action_factory=fake_next_action,
        clean_context_factory=lambda root: {"ok": True, "status": "passed"},
        guardrail_factory=lambda root: {"ok": True, "status": "passed", "finding_count": 0},
        owner_capsule_factory=lambda root: {"status": "present", "count": 3},
    )
    compact = repo_context_guardrails.summarize_context_use_check_report(report, compact=True)

    assert_ok(report)
    assert_field(report, "status", "passed")
    assert_field(report["summary"], "source_orientation", "automations/navigation/artifacts/maps/HANDOFF.md")
    assert_field(report["summary"], "owner_capsule_status", "present")
    assert "next_command" not in compact["summary"]
    assert_field(compact["summary"], "effective_next_command", "none")
    assert_field(compact["summary"], "next_action_next_command", "python -B .agents/manage.py review-loop --summary --compact --format json")
    assert_field(compact["summary"], "next_command_scope", "next-action-proof-only")
    assert_field(compact["latency_budget"], "command", "context-use-check")
    assert_field(compact["output_budget"], "command", "context-use-check")


def test_context_use_check_fails_when_next_action_omits_raw_json_skip(tmp):
    trace = {
        "status": "fresh",
        "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
        "read_now": ["AGENTS.md"],
        "skip_raw_json": [],
    }

    report = repo_context_guardrails.context_use_check_report(
        tmp,
        startup_factory=lambda root, **_kwargs: {"ok": True, "status": "passed", "context_trace": trace},
        next_action_factory=lambda root, **_kwargs: {"ok": True, "status": "ready", "next_command": "none", "context_trace": trace},
        clean_context_factory=lambda root: {"ok": True, "status": "passed"},
        guardrail_factory=lambda root: {"ok": True, "status": "passed", "finding_count": 0},
        owner_capsule_factory=lambda root: {"status": "present", "count": 1},
    )

    assert_not_ok(report)
    assert_field(report, "status", "failed")
    assert_contains(report["issues"], "next-action context trace does not skip raw navigation JSON")


def test_review_packet_chunks_large_new_file_without_git_hunks(tmp):
    big_path = ".agents/skills/skill-manager/scripts/repo_support/repo_review_packet.py"
    write_text(tmp / big_path, "\n".join(f"line {index}" for index in range(700)) + "\n")

    with patched_attrs(
        repo_changed,
        changed_files=lambda root: [big_path],
        changed_file_statuses=lambda root: {big_path: {"?"}},
        changed_path_token_estimates=lambda root, values: {big_path: {"estimated_tokens": 9000}},
        changed_scope=lambda values: {"skill_names": {"skill-manager"}, "workflows": False},
    ), patched_attrs(
        repo_review_packet,
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
    ), patched_attrs(
        repo_changed.repo_cost_policy,
        changed_diff_estimate=lambda root: {"estimated_tokens": 9000, "tracked_estimated_tokens": 0, "untracked_estimated_tokens": 9000},
    ), patched_attrs(
        repo_changed.repo_optimizations,
        changed_validation_plan=lambda root, values, scope, deep=False: [
            {"command": "python -B .agents/manage.py syntax-check --paths .agents/skills/skill-manager --format json", "required": True},
        ],
    ):
        status, payload = capture_json(
            repo_changed.review_packet_command,
            Namespace(
                format="json",
                summary=True,
                compact=True,
                deep=False,
                write_dir=None,
                owner="skill:skill-manager",
            ),
            tmp,
        )
        full_packet = repo_review_packet.build_review_packet(
            tmp,
            owner="skill:skill-manager",
            refresh_navigation=False,
        )

    assert status == 0
    assert_field(payload, "owner_review_hunk_count", 3)
    assert_field(payload, "largest_owner_hunk_estimated_tokens", 3600)
    assert_has_all(payload["next_command"], "--path", big_path, "--hunk h001")
    assert_field(payload["cost_ledger"], "largest_review_unit_estimated_tokens", 3600)
    review_plan = repo_review_progress.build_review_plan(full_packet)
    assert_has_all(review_plan["next_pending_command"], "--path", big_path, "--hunk h001")
    assert_field(review_plan["review_units"][0], "scope", "hunk")
    assert review_plan["review_units"][0]["estimated_changed_tokens"] <= full_packet["review_budget_tokens"]


def test_review_packet_hunk_slice_requires_path(tmp):
    status, payload = capture_json(
        repo_changed.review_packet_command,
        Namespace(
            format="json",
            summary=True,
            compact=True,
            deep=False,
            write_dir=None,
            owner="skill:skill-manager",
            paths=[],
            hunks=["h001"],
        ),
        tmp,
    )

    assert status == 1
    assert_field(payload, "status", "hunk-slice-requires-path")
    assert_has_all(payload["review_rule"], "--path")


def test_review_packet_path_slice_requires_owner(tmp):
    status, payload = capture_json(
        repo_changed.review_packet_command,
        Namespace(
            format="json",
            summary=True,
            compact=True,
            deep=False,
            write_dir=None,
            owner="",
            paths=["AGENTS.md"],
        ),
        tmp,
    )

    assert status == 1
    assert_field(payload, "status", "path-slice-requires-owner")
    assert_has_all(payload["review_rule"], "--owner")


def test_review_packet_command_handles_no_changed_files(tmp):
    with patched_attrs(
        repo_changed,
        changed_files=lambda root: [],
    ), patched_attrs(
        repo_review_packet,
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
    ), patched_attrs(
        repo_changed.repo_cost_policy,
        changed_diff_estimate=lambda root: {"estimated_tokens": 0, "tracked_estimated_tokens": 0, "untracked_estimated_tokens": 0},
    ):
        status, payload = capture_json(
            repo_changed.review_packet_command,
            Namespace(format="json", summary=True, compact=True, deep=False, write_dir=None),
            tmp,
        )

    assert status == 0
    assert_field(payload, "status", "within-budget")
    assert_field(payload, "changed_file_count", 0)
    assert payload["read_first"] == []


def test_review_packet_write_rejects_outside_repo(tmp):
    with patched_attrs(
        repo_changed,
        changed_files=lambda root: [],
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
    ):
        try:
            repo_changed.review_packet_command(
                Namespace(format="json", summary=True, compact=True, deep=False, write_dir="../outside"),
                tmp,
            )
        except SystemExit as exc:
            assert "inside the repository" in str(exc)
        else:
            raise AssertionError("expected outside write path to be rejected")


def test_review_packet_command_auto_refreshes_navigation(tmp):
    with patched_attrs(
        repo_changed,
        changed_files=lambda root: ["AGENTS.md"],
        changed_file_statuses=lambda root: {"AGENTS.md": {"M"}},
        changed_scope=lambda paths: {"skill_names": set(), "workflows": False},
    ), patched_attrs(
        repo_review_packet,
        auto_refresh_navigation=lambda root: {
            "schema_version": 1,
            "tool": "repo-navigation.auto-refresh",
            "ok": True,
            "status": "refreshed",
            "written": ["automations/navigation/artifacts/maps/NAVIGATION.md"],
            "summary": "Navigation maps were refreshed safely.",
        },
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
    ), patched_attrs(
        repo_changed.repo_cost_policy,
        changed_diff_estimate=lambda root: {"estimated_tokens": 7000, "tracked_estimated_tokens": 7000, "untracked_estimated_tokens": 0},
    ):
        status, payload = capture_json(
            repo_changed.review_packet_command,
            Namespace(format="json", summary=True, compact=True, deep=False, write_dir="evidence/review", owner=""),
            tmp,
        )

    assert status == 0
    assert_field(payload, "status", "over-budget")
    assert_field(payload["navigation_auto_refresh"], "status", "refreshed")
    assert_contains(payload["navigation_auto_refresh"]["written"], "automations/navigation/artifacts/maps/NAVIGATION.md")


def test_review_packet_without_write_does_not_refresh_navigation(tmp):
    calls = []

    def fail_if_refreshed(root):
        calls.append(root)
        raise AssertionError("review-packet without --write must not refresh navigation maps")

    with patched_attrs(
        repo_changed,
        changed_files=lambda root: ["AGENTS.md"],
        changed_file_statuses=lambda root: {"AGENTS.md": {"M"}},
        changed_scope=lambda paths: {"skill_names": set(), "workflows": False},
    ), patched_attrs(
        repo_review_packet,
        auto_refresh_navigation=fail_if_refreshed,
        navigation_status=lambda root: {
            "status": "stale",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "python -B .agents/skills/repo-navigation/scripts/repo_navigation.py update --target . --write --format json",
            "stale_output_count": 1,
            "summary": "stale",
        },
    ), patched_attrs(
        repo_changed.repo_cost_policy,
        changed_diff_estimate=lambda root: {"estimated_tokens": 7000, "tracked_estimated_tokens": 7000, "untracked_estimated_tokens": 0},
    ):
        status, payload = capture_json(
            repo_changed.review_packet_command,
            Namespace(format="json", summary=True, compact=True, deep=False, write_dir=None, owner=""),
            tmp,
        )

    assert calls == []
    assert status == 1
    assert_field(payload, "status", "blocked")
    assert_field(payload, "ok", False)
    assert_field(payload["navigation_auto_refresh"], "status", "blocked-read-only")
    assert_field(payload["navigation_auto_refresh"], "written", [])
    assert_has_all(payload["review_rule"], "read-only without --write")
    assert_has_all(payload["next_command"], "repo_navigation.py update")


def test_review_packet_blocks_when_navigation_refresh_fails(tmp):
    with patched_attrs(
        repo_changed,
        changed_files=lambda root: ["AGENTS.md"],
        changed_file_statuses=lambda root: {"AGENTS.md": {"M"}},
        changed_scope=lambda paths: {"skill_names": set(), "workflows": False},
    ), patched_attrs(
        repo_review_packet,
        auto_refresh_navigation=lambda root: {
            "schema_version": 1,
            "tool": "repo-navigation.auto-refresh",
            "ok": False,
            "status": "blocked",
            "written": [],
            "summary": "Navigation refresh blocked.",
            "next_command": "python -B .agents/skills/repo-navigation/scripts/repo_navigation.py check --target . --format json",
        },
        navigation_status=lambda root: {
            "status": "blocked",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "python -B .agents/skills/repo-navigation/scripts/repo_navigation.py check --target . --format json",
            "stale_output_count": 0,
            "summary": "blocked",
        },
    ), patched_attrs(
        repo_changed.repo_cost_policy,
        changed_diff_estimate=lambda root: {"estimated_tokens": 7000, "tracked_estimated_tokens": 7000, "untracked_estimated_tokens": 0},
    ):
        status, payload = capture_json(
            repo_changed.review_packet_command,
            Namespace(format="json", summary=True, compact=True, deep=False, write_dir="evidence/review", owner=""),
            tmp,
        )

    assert status == 1
    assert_field(payload, "status", "blocked")
    assert_field(payload, "ok", False)
    assert_has_all(payload["review_rule"], "Navigation refresh failed")


def test_review_packet_build_inherits_policy_batch_hunk_limit(tmp):
    hunk_packets = [
        {
            "path": "src/demo.py",
            "hunk": f"h{index:03d}",
            "estimated_changed_tokens": 100,
            "next_command": (
                "python -B .agents/manage.py review-packet "
                f"--owner skill:skill-manager --path src/demo.py --hunk h{index:03d} "
                "--summary --compact --format json"
            ),
        }
        for index in range(1, 7)
    ]
    review_packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_diff_estimated_tokens": 9000,
        "review_budget_tokens": 5000,
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "status": "within-budget",
                "scope": "owner",
                "estimated_changed_tokens": 600,
                "owner_review_subpackets": [
                    {
                        "path": "src/demo.py",
                        "estimated_changed_tokens": 600,
                        "path_review_hunks": hunk_packets,
                    }
                ],
            }
        ],
    }
    policy = repo_cost_policy.default_cost_policy()
    policy["review_loop"]["max_hunks_per_batch"] = 2

    with patched_attrs(
        repo_changed,
        changed_files=lambda root: ["src/demo.py"],
        changed_scope=lambda paths: {"skill_names": {"skill-manager"}, "workflows": False},
        large_diff_review_packet=lambda root, paths, validation_plan, navigation: dict(review_packet),
    ), patched_attrs(
        repo_review_packet,
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
        },
    ), patched_attrs(
        repo_qol.repo_optimizations,
        changed_validation_plan=lambda root, paths, scope, deep=False: [],
    ), patched_attrs(
        repo_cost_policy,
        load_cost_policy=lambda root: (policy, ""),
    ):
        packet = repo_review_packet.build_review_packet(tmp, refresh_navigation=False)

    plan = repo_review_progress.build_review_plan(packet)

    assert_field(packet, "review_batch_max_hunks", 2)
    assert_field(plan["review_batching"], "max_hunks_per_batch_limit", 2)
    assert_field(plan["review_batching"], "batched_review_unit_count", 3)


def test_handoff_packet_routes_large_diff_to_owner_slice(tmp):
    with patched_attrs(
        repo_changed,
        changed_files=lambda root: [".agents/skills/skill-manager/scripts/repo_support/demo.py"],
        changed_file_statuses=lambda root: {".agents/skills/skill-manager/scripts/repo_support/demo.py": {"M"}},
        changed_scope=lambda paths: {"skill_names": {"skill-manager"}, "workflows": False},
    ), patched_attrs(
        repo_review_packet,
        auto_refresh_navigation=lambda root: {
            "schema_version": 1,
            "tool": "repo-navigation.auto-refresh",
            "ok": True,
            "status": "skipped-fresh",
            "written": [],
            "summary": "Navigation maps were already fresh.",
        },
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
    ), patched_attrs(
        repo_changed.repo_cost_policy,
        changed_diff_estimate=lambda root: {"estimated_tokens": 7000, "tracked_estimated_tokens": 7000, "untracked_estimated_tokens": 0},
    ):
        status, payload = capture_json(
            repo_changed.handoff_packet_command,
            Namespace(format="json", summary=True, compact=True, owner=""),
            tmp,
        )

    assert status == 0
    assert_field(payload, "tool", "skill-manager.handoff-packet")
    assert_field(payload, "status", "needs-owner-review")
    assert_field(payload, "raw_navigation_json", "tool-only")
    assert_contains(payload["route_first"], "automations/navigation/artifacts/maps/HANDOFF.md")
    assert_has_all(payload["next_command"], "review-packet --owner skill:skill-manager")


def test_fresh_agent_packet_names_source_orientation_and_tool_only_json(tmp):
    with patched_attrs(
        repo_changed,
        changed_files=lambda root: [".agents/skills/skill-manager/scripts/repo_support/demo.py"],
        changed_file_statuses=lambda root: {".agents/skills/skill-manager/scripts/repo_support/demo.py": {"M"}},
        changed_scope=lambda paths: {"skill_names": {"skill-manager"}, "workflows": False},
    ), patched_attrs(
        repo_review_packet,
        auto_refresh_navigation=lambda root: {
            "schema_version": 1,
            "tool": "repo-navigation.auto-refresh",
            "ok": True,
            "status": "skipped-fresh",
            "written": [],
            "summary": "Navigation maps were already fresh.",
        },
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
    ), patched_attrs(
        repo_changed.repo_cost_policy,
        changed_diff_estimate=lambda root: {"estimated_tokens": 7000, "tracked_estimated_tokens": 7000, "untracked_estimated_tokens": 0},
    ):
        status, payload = capture_json(
            repo_changed.fresh_agent_packet_command,
            Namespace(format="json", summary=True, compact=True, owner=""),
            tmp,
        )

    assert status == 0
    assert_field(payload, "tool", "skill-manager.fresh-agent-packet")
    assert_field(payload, "source_orientation_file", "automations/navigation/artifacts/maps/HANDOFF.md")
    assert_contains(payload["route_first"], "python -B .agents/manage.py startup-context --summary --compact --format json")
    assert_contains(payload["tool_only_inputs"], "raw navigation JSON")
    assert_field(payload["local_ai_route"], "status", "advisory-only")
    assert_field(payload, "next_command_source", "review-loop-autopilot")
    assert_has_all(payload["next_command"], "python -B .agents/manage.py", "review-loop", "--max-units")
    assert "raw navigation JSON" not in payload["next_command"]


def test_navigation_status_stale_uses_direct_update_command(tmp):
    write_text(tmp / "automations" / "navigation" / "artifacts" / "maps" / "HANDOFF.md", "# Handoff\n")

    report = repo_navigation_status.navigation_status_from_report(
        tmp,
        {"status": "stale", "stale": ["automations/navigation/artifacts/maps/NAVIGATION.md"]},
    )

    assert_field(report, "status", "stale")
    assert_has_all(report["next_command"], "repo_navigation.py update", "--write")
    assert "manage.py setup" not in report["next_command"]


def test_navigation_auto_refresh_updates_only_known_outputs(tmp):
    for relative in repo_navigation_status.NAVIGATION_OUTPUT_RELS:
        write_text(tmp / relative, "generated")
    calls = []

    def fake_repo_navigation_report(root, *args, timeout_seconds=60):
        calls.append(args)
        if "check" in args:
            return {
                "ok": False,
                "status": "stale",
                "stale": ["automations/navigation/artifacts/maps/NAVIGATION.md"],
            }
        if "update" in args:
            return {
                "ok": True,
                "status": "updated",
                "written": ["automations/navigation/artifacts/maps/NAVIGATION.md"],
            }
        raise AssertionError(args)

    with patched_attrs(
        repo_navigation_status,
        repo_navigation_report=fake_repo_navigation_report,
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
    ):
        report = repo_navigation_status.auto_refresh_navigation(tmp)

    assert_ok(report)
    assert_field(report, "status", "refreshed")
    assert_field(report["after"], "status", "fresh")
    assert any("update" in item for item in calls)


def test_navigation_auto_refresh_allows_generated_owner_capsules(tmp):
    for relative in repo_navigation_status.NAVIGATION_OUTPUT_RELS:
        write_text(tmp / relative, "generated")
    owner_capsule = "automations/navigation/artifacts/maps/owners/skill-skill-manager.md"

    def fake_repo_navigation_report(root, *args, timeout_seconds=60):
        if "check" in args:
            return {
                "ok": False,
                "status": "stale",
                "stale": [
                    "automations/navigation/artifacts/maps/staleness.json",
                    owner_capsule,
                ],
            }
        if "update" in args:
            return {
                "ok": True,
                "status": "updated",
                "written": [
                    "automations/navigation/artifacts/maps/staleness.json",
                    owner_capsule,
                ],
            }
        raise AssertionError(args)

    with patched_attrs(
        repo_navigation_status,
        repo_navigation_report=fake_repo_navigation_report,
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
    ):
        report = repo_navigation_status.auto_refresh_navigation(tmp)

    assert_ok(report)
    assert_field(report, "status", "refreshed")
    assert_field(report, "unsafe_written", [])
    assert repo_navigation_status.is_known_navigation_output(owner_capsule)
    assert not repo_navigation_status.is_known_navigation_output("automations/navigation/artifacts/maps/owners/nested/file.md")
    assert not repo_navigation_status.is_known_navigation_output("automations/navigation/artifacts/maps/owners/notes.txt")


def test_navigation_auto_refresh_blocks_unknown_stale_outputs(tmp):
    for relative in repo_navigation_status.NAVIGATION_OUTPUT_RELS:
        write_text(tmp / relative, "generated")

    with patched_attrs(
        repo_navigation_status,
        repo_navigation_report=lambda root, *args, timeout_seconds=60: {
            "ok": False,
            "status": "stale",
            "stale": ["docs/project/project-context.md"],
        },
    ):
        report = repo_navigation_status.auto_refresh_navigation(tmp)

    assert_not_ok(report)
    assert_field(report, "status", "blocked")
    assert_contains(report["unsafe_stale"], "docs/project/project-context.md")


def test_navigation_auto_refresh_blocks_unknown_written_outputs(tmp):
    for relative in repo_navigation_status.NAVIGATION_OUTPUT_RELS:
        write_text(tmp / relative, "generated")

    def fake_repo_navigation_report(root, *args, timeout_seconds=60):
        if "check" in args:
            return {
                "ok": False,
                "status": "stale",
                "stale": ["automations/navigation/artifacts/maps/NAVIGATION.md"],
            }
        if "update" in args:
            return {
                "ok": True,
                "status": "updated",
                "written": ["docs/project/project-context.md"],
            }
        raise AssertionError(args)

    with patched_attrs(
        repo_navigation_status,
        repo_navigation_report=fake_repo_navigation_report,
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
    ):
        report = repo_navigation_status.auto_refresh_navigation(tmp)

    assert_not_ok(report)
    assert_field(report, "status", "failed")
    assert_contains(report["unsafe_written"], "docs/project/project-context.md")


def test_check_changed_is_read_only_by_default(tmp):
    calls = []

    def fail_if_called(root):
        calls.append(root)
        raise AssertionError("check-changed default mode must not refresh maps")

    with patched_attrs(
        repo_changed,
        auto_refresh_navigation=fail_if_called,
        changed_files=lambda root: [],
        navigation_status=lambda root: {
            "status": "stale",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "python -B .agents/skills/repo-navigation/scripts/repo_navigation.py update --target . --write --format json",
            "stale_output_count": 1,
            "summary": "stale",
        },
    ):
        status, payload = capture_json(
            repo_changed.check_changed,
            Namespace(format="json", deep=False, verbose=False, full=True),
            tmp,
        )

    assert status == 0
    assert calls == [], calls
    assert_field(payload["navigation_auto_refresh"], "status", "skipped-read-only")


def test_check_changed_refreshes_navigation_when_explicit(tmp):
    with patched_attrs(
        repo_changed,
        auto_refresh_navigation=lambda root: {
            "schema_version": 1,
            "tool": "repo-navigation.auto-refresh",
            "ok": True,
            "status": "refreshed",
            "written": ["automations/navigation/artifacts/maps/NAVIGATION.md"],
            "summary": "Navigation maps were refreshed safely.",
        },
        changed_files=lambda root: [],
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
    ):
        status, payload = capture_json(
            repo_changed.check_changed,
            Namespace(format="json", deep=False, verbose=False, refresh_navigation=True, full=True),
            tmp,
        )

    assert status == 0
    assert_field(payload["navigation_auto_refresh"], "status", "refreshed")


def test_check_changed_records_input_fingerprint_in_validation_progress(tmp):
    scope = {
        "skill_names": [],
        "workflows": False,
        "skills_generated": False,
        "workflow_generated": False,
        "repo_surface": False,
        "instructions": False,
        "python_paths": [],
        "docs": [],
        "other": [],
    }
    validation_plan = [
        {
            "order": 1,
            "command": "python -B .agents/manage.py check-additions",
            "required": True,
            "reason": "fixture",
        }
    ]

    with patched_attrs(
        repo_changed,
        auto_refresh_navigation=lambda root: {"ok": True, "status": "skipped-fresh", "written": []},
        changed_files=lambda root: ["README.md"],
        changed_scope=lambda paths: dict(scope),
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
        large_diff_review_packet=lambda root, paths, plan, navigation: {"status": "within-budget"},
        addition_acceptance_report=lambda root, paths=None: {"ok": True, "status": "passed"},
        render_addition_acceptance=lambda report, verbose=False: "ok",
    ), patched_attrs(
        repo_changed.repo_optimizations,
        changed_validation_plan=lambda root, changed, changed_scope, deep=False: list(validation_plan),
    ), patched_attrs(
        repo_changed.repo_fingerprint,
        input_fingerprint_report=lambda root, paths, commands: {"digest": "digest-a", "changed_file_count": 1},
    ), patched_attrs(
        repo_changed.repo_review_progress,
        build_review_plan=lambda packet: {"status": "no-review-units"},
        review_progress_report=lambda *args, **kwargs: {"status": "complete", "coverage": {"status": "no-review-units"}},
        summarize_review_progress=lambda report: dict(report),
    ), patched_attrs(
        repo_changed.repo_proof_hygiene,
        proof_hygiene_report=lambda root, paths: {"ok": True, "status": "passed"},
        render_proof_hygiene=lambda report: "ok",
    ), patched_attrs(
        repo_changed.repo_portability,
        portability_report=lambda root, paths=None: {"ok": True, "status": "passed"},
        render_portability_report=lambda report: "ok",
    ), patched_attrs(
        repo_changed.repo_context_guardrails,
        context_guardrail_report=lambda root, paths=None: {"ok": True, "status": "passed"},
        render_context_guardrail_report=lambda report, compact=True: "ok",
    ):
        status, payload = capture_json(
            repo_changed.check_changed,
            Namespace(format="json", deep=False, verbose=False, full=True),
            tmp,
        )

    assert status == 0
    assert_field(payload["validation_progress"], "status", "passed")
    assert_field(payload["validation_progress"]["extra"], "input_fingerprint_digest", "digest-a")


def test_check_changed_fails_when_navigation_auto_refresh_fails(tmp):
    with patched_attrs(
        repo_changed,
        auto_refresh_navigation=lambda root: {
            "schema_version": 1,
            "tool": "repo-navigation.auto-refresh",
            "ok": False,
            "status": "blocked",
            "written": [],
            "summary": "Navigation refresh blocked.",
            "next_command": "python -B .agents/skills/repo-navigation/scripts/repo_navigation.py check --target . --format json",
        },
        changed_files=lambda root: [],
        navigation_status=lambda root: {
            "status": "blocked",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "python -B .agents/skills/repo-navigation/scripts/repo_navigation.py check --target . --format json",
            "stale_output_count": 0,
            "summary": "blocked",
        },
    ):
        status, payload = capture_json(
            repo_changed.check_changed,
            Namespace(format="json", deep=False, verbose=False, refresh_navigation=True, full=True),
            tmp,
        )

    assert status == 1
    assert_field(payload, "status", "failed")
    assert_field(payload["validation_progress"], "status", "failed")
    assert_contains(payload["checks"], "navigation auto-refresh gate")


def test_portable_constraints_flags_hardware_defaults_and_personal_paths(tmp):
    write_text(tmp / "docs" / "harness" / "portable.md", "Default to Strix Halo with D:\\Projects\\Skills\\models for all users.")

    report = repo_portability.portability_report(tmp, paths=["docs/harness/portable.md"])

    assert_not_ok(report)
    assert_summary(report, error_count=2)
    assert_contains(report["findings"], "specific-hardware-default")
    assert_contains(report["findings"], "personal-absolute-path")


def test_portable_constraints_allows_explicit_watchlist_context(tmp):
    write_text(
        tmp / "automations" / "local-ai-benchmark-workflow" / "docs" / "benchmark-runtime-watchlist.md",
        "ROCm/HIP rows are hardware-specific watchlist evidence, not portable defaults.",
    )

    report = repo_portability.portability_report(
        tmp,
        paths=["automations/local-ai-benchmark-workflow/docs/benchmark-runtime-watchlist.md"],
    )

    assert_ok(report)
    assert_summary(report, error_count=0, finding_count=0)


def test_portable_constraints_allows_model_identifiers_without_defaults(tmp):
    write_text(
        tmp / "automations" / "local-ai-benchmark-workflow" / "docs" / "benchmark-lessons.md",
        "NVIDIA Nemotron rows are benchmark-only candidates, not portable defaults.",
    )

    report = repo_portability.portability_report(
        tmp,
        paths=["automations/local-ai-benchmark-workflow/docs/benchmark-lessons.md"],
    )

    assert_ok(report)
    assert_summary(report, error_count=0, finding_count=0)


def test_portable_constraints_skips_unrelated_harness_text(tmp):
    write_text(tmp / "docs" / "workflow" / "notes.md", "Default to Strix Halo.")

    report = repo_portability.portability_report(tmp, paths=["docs/workflow/notes.md"])

    assert_ok(report)
    assert_summary(report, skipped_count=1, finding_count=0)


def test_portable_constraints_parser_does_not_expose_noisy_all_mode(tmp):
    parser = repo_cli_parser.build_parser()
    try:
        parser.parse_args(["portable-constraints", "--all"])
    except SystemExit as exc:
        assert exc.code != 0
    else:
        raise AssertionError("portable-constraints --all should not be a public mode")


def test_check_changed_runs_portable_constraints_gate(tmp):
    write_text(tmp / "docs" / "harness" / "portable.md", "Default route requires Strix Halo.")

    def fake_acceptance(root, *, paths=None, new_paths=None):
        return {
            "schema_version": 1,
            "tool": "skill-manager.addition-acceptance",
            "ok": True,
            "status": "passed",
            "summary": {"issue_count": 0},
            "issues": [],
        }

    with patched_attrs(
        repo_changed,
        changed_files=lambda root: ["docs/harness/portable.md"],
        addition_acceptance_report=fake_acceptance,
    ):
        status, payload = capture_json(
            repo_changed.check_changed,
            Namespace(format="json", deep=False, verbose=False, full=True),
            tmp,
        )

    assert status == 1
    assert_status(payload["portable_constraints"], "failed")
    assert_contains(payload["checks"], "portable constraints gate")


def test_proof_hygiene_records_unreadable_text_skips(tmp):
    binary = tmp / "binary.dat"
    binary.write_bytes(b"\xff\xfe\x00")

    report = repo_proof_hygiene.proof_hygiene_report(tmp, ["binary.dat"])

    assert_ok(report)
    assert_summary(report, finding_count=0, skipped_count=1)
    assert_contains(report["skipped"], "could not read text")


def test_proof_hygiene_git_output_uses_portable_utf8_decode(tmp):
    captured = {}

    class Completed:
        returncode = 0
        stdout = "diff --git a/docs/example.md b/docs/example.md\n+portable — text\n"

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return Completed()

    with patched_attrs(repo_proof_hygiene.subprocess, run=fake_run):
        status, lines = repo_proof_hygiene.run_git_lines(tmp, ["diff"])

    assert status == 0
    assert_contains(lines, "portable — text")
    assert_fields(captured, text=True, encoding="utf-8", errors="replace")


def test_proof_hygiene_ignores_preexisting_patterns_outside_added_lines(tmp):
    except_kw = "ex" + "cept"
    pass_kw = "pa" + "ss"
    write_text(
        tmp / "app.py",
        f"try:\n    run()\n{except_kw} OSError:\n    {pass_kw}\n",
    )
    subprocess.run(["git", "init"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "app.py"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    write_text(
        tmp / "app.py",
        f"try:\n    run()\n{except_kw} OSError:\n    {pass_kw}\n\nvalue = 1\n",
    )

    report = repo_proof_hygiene.proof_hygiene_report(tmp, ["app.py"])

    assert_ok(report)
    assert_empty(report["findings"])


def test_proof_hygiene_flags_new_silent_exception_line(tmp):
    except_kw = "ex" + "cept"
    pass_kw = "pa" + "ss"
    write_text(
        tmp / "app.py",
        f"try:\n    run()\n{except_kw} OSError:\n    log_error()\n",
    )
    subprocess.run(["git", "init"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "app.py"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    write_text(
        tmp / "app.py",
        f"try:\n    run()\n{except_kw} OSError:\n    {pass_kw}\n",
    )

    report = repo_proof_hygiene.proof_hygiene_report(tmp, ["app.py"])

    assert_not_ok(report)
    assert_contains(report["findings"], "python_silent_failure")


def test_proof_hygiene_flags_new_return_none_exception_line(tmp):
    except_kw = "ex" + "cept"
    write_text(
        tmp / "app.py",
        f"try:\n    run()\n{except_kw} OSError:\n    log_error()\n",
    )
    subprocess.run(["git", "init"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "app.py"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    write_text(
        tmp / "app.py",
        f"try:\n    run()\n{except_kw} OSError:\n    return None\n",
    )

    report = repo_proof_hygiene.proof_hygiene_report(tmp, ["app.py"])

    assert_not_ok(report)
    assert_contains(report["findings"], "python_silent_failure")


def test_proof_hygiene_ignores_return_none_tuple_and_tokenizer_names(tmp):
    except_kw = "ex" + "cept"
    write_text(
        tmp / "adapter.py",
        (
            'tokenizer = "tiktoken:o200k_base"\n'
            "try:\n"
            "    load_index()\n"
            f"{except_kw} OSError as exc:\n"
            "    return None, [str(exc)]\n"
        ),
    )

    report = repo_proof_hygiene.proof_hygiene_report(tmp, ["adapter.py"])

    assert_ok(report)
    assert_empty(report["findings"])


def test_proof_hygiene_covers_rule_surface_and_markdown_fences(tmp):
    marker = "TO" + "DO"
    catch_kw = "cat" + "ch"
    eval_kw = "ev" + "al"
    api_key = "api" + "_key"
    env_key = "API" + "_KEY"
    write_text(tmp / "README.md", f"```python\n# {marker} example\n```\n")
    write_text(tmp / "app.js", f"try {{ run(); }} {catch_kw} (error) {{}}\n{eval_kw}('1 + 1')\n")
    write_text(tmp / "config.json", f'{{"{api_key}": "abcdefghijkl"}}')
    write_text(tmp / ".env.sample", f"{env_key}=abcdefghijkl")
    write_text(tmp / "task.py", f"# {marker} before release\n")

    report = repo_proof_hygiene.proof_hygiene_report(
        tmp,
        ["README.md", "app.js", "config.json", ".env.sample", "task.py"],
    )

    assert_not_ok(report)
    assert_contains(report["findings"], "js_silent_failure")
    assert_contains(report["findings"], "unsafe_eval")
    assert_contains(report["findings"], "secret_literal")
    assert_contains(report["findings"], "unfinished_marker")
    assert_lacks(report["findings"], "README.md")


def test_proof_hygiene_flags_local_thin_proof_but_skips_generated_maps(tmp):
    write_text(tmp / "automations/demo/runs/001/REPORT.md", "All tests passed.")
    api_key = "api" + "_key"
    write_text(tmp / "automations/navigation/artifacts/maps/staleness.json", f'{{"{api_key}": "abcdefghijkl"}}')

    report = repo_proof_hygiene.proof_hygiene_report(
        tmp,
        [
            "automations/demo/runs/001/REPORT.md",
            "automations/navigation/artifacts/maps/staleness.json",
        ],
    )

    assert_not_ok(report)
    assert_contains(report["findings"], "thin_proof_file")
    assert_lacks(report["findings"], "staleness.json")


def test_proof_hygiene_flags_closeout_residue_but_skips_templates(tmp):
    write_text(tmp / "automations" / "demo" / "runs" / "001" / "REPORT.md", "- [ ] Run final validation\nRun id: [run-id]\n")
    write_text(tmp / "automations" / "demo" / "templates" / "pr-description.md", "- [ ] Template item\nRun id: [run-id]\n")

    report = repo_proof_hygiene.proof_hygiene_report(
        tmp,
        [
            "automations/demo/runs/001/REPORT.md",
            "automations/demo/templates/pr-description.md",
        ],
    )

    assert_not_ok(report)
    assert_contains(report["findings"], "unchecked_closeout_item")
    assert_contains(report["findings"], "closeout_placeholder")
    assert_lacks(report["findings"], "templates/pr-description.md")


def test_input_fingerprint_changes_with_file_command_and_stale_inputs(tmp):
    write_text(tmp / "app.py", "value = 1")
    write_text(tmp / "pyproject.toml", "[project]\nname = 'demo'")
    subprocess.run(["git", "init"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "add", "pyproject.toml"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    base = repo_qol_daily.input_fingerprint_report(
        tmp,
        ["app.py"],
        [{"command": "python -B .agents/manage.py check-changed"}],
    )
    changed_command = repo_qol_daily.input_fingerprint_report(
        tmp,
        ["app.py"],
        [{"command": "python -B .agents/manage.py check"}],
    )
    write_text(tmp / "pyproject.toml", "[project]\nname = 'demo2'")
    changed_stale_input = repo_qol_daily.input_fingerprint_report(
        tmp,
        ["app.py"],
        [{"command": "python -B .agents/manage.py check-changed"}],
    )

    assert base["digest"] != changed_command["digest"]
    assert base["digest"] != changed_stale_input["digest"]
    assert_contains(base["fingerprint_inputs"]["stale_inputs"], "pyproject.toml")


def test_benchmark_tool_call_uses_workflow_script(tmp):
    script = benchmark_dir(tmp, "scripts") / "local_ai_tool_call_benchmark.py"
    write_text(script, "print('fixture')")
    calls = []

    class Completed:
        returncode = 0

    def fake_run(command, check=False, env=None):
        record_command(calls, command)
        return Completed()

    with patched_attrs(repo_benchmark.subprocess, run=fake_run):
        status = repo_benchmark.benchmark_tool_call(["--check", "--json", "--compact"], tmp)

    assert status == 0
    assert calls
    assert calls[0][1] == "-B"
    assert calls[0][2] == str(script)
    assert calls[0][3:] == ["--root", str(tmp), "--check", "--json", "--compact"]


def test_benchmark_routing_eval_uses_skill_script(tmp):
    calls = []

    def fake_run(root, skill, script, command):
        calls.append((root, skill, script, command))
        return 0

    with patched_attrs(repo_benchmark.repo, run_skill_script=fake_run):
        status = repo_benchmark.benchmark_routing_eval(
            [
                "--suite",
                "automations/agent-benchmarking/suites/routing-evidence-real-use.json",
                "--check-suite",
                "--format",
                "json",
                "--summary",
            ],
            tmp,
        )

    assert status == 0
    assert calls == [
        (
            tmp,
            "agent-benchmarking",
            "routing_evidence_eval.py",
            [
                "--suite",
                "automations/agent-benchmarking/suites/routing-evidence-real-use.json",
                "--format",
                "json",
                "--proof-line-limit",
                "50",
                "--check-suite",
                "--summary",
            ],
        )
    ]


def test_benchmark_compare_wrappers_forward_optimization_gate_flags(tmp):
    calls = []

    def fake_run(root, skill, script, command):
        calls.append((root, skill, script, command))
        return 0

    with patched_attrs(repo_benchmark.repo, run_skill_script=fake_run):
        matrix_status = repo_benchmark.benchmark_group(
            argparse.Namespace(
                benchmark_args=[
                    "compare-matrix",
                    "run-a",
                    "run-b",
                    "--optimization-gate",
                    "--allow-quality-drop",
                    "0.1",
                    "--no-require-improvement",
                    "--format",
                    "json",
                ]
            ),
            tmp,
            doctor_report_func=lambda **_kwargs: {},
            doctor_command_func=lambda _args, _root: 0,
        )
        latest_status = repo_benchmark.benchmark_group(
            argparse.Namespace(
                benchmark_args=[
                    "compare-latest",
                    "runs-root",
                    "--optimization-gate",
                    "--allow-quality-drop",
                    "0.1",
                    "--no-require-improvement",
                    "--compact",
                    "--format",
                    "json",
                ]
            ),
            tmp,
            doctor_report_func=lambda **_kwargs: {},
            doctor_command_func=lambda _args, _root: 0,
        )

    assert matrix_status == 0
    assert latest_status == 0
    assert calls[0] == (
        tmp,
        "agent-benchmarking",
        "compare_benchmark_runs.py",
        [
            "run-a",
            "run-b",
            "--format",
            "json",
            "--optimization-gate",
            "--allow-quality-drop",
            "0.1",
            "--no-require-improvement",
        ],
    )
    assert calls[1] == (
        tmp,
        "agent-benchmarking",
        "compare_benchmark_runs.py",
        [
            "--compare-latest",
            "runs-root",
            "--format",
            "json",
            "--compact",
            "--optimization-gate",
            "--allow-quality-drop",
            "0.1",
            "--no-require-improvement",
        ],
    )


def test_repo_health_surface_split_keeps_public_surface(tmp):
    assert_same_attrs(
        repo_health,
        repo_health_surface,
        "validate_python_only_scripts",
        "validate_no_pycache",
        "validate_repo_layout",
        "folder_organization_report",
        "validate_candidate_import_hygiene",
        "validate_manager_self_containment",
        "instruction_quality_errors",
        "eval_quality_report",
        "json_format_errors",
        "root_docs_frontmatter_errors",
        "format_json_files",
        "script_complexity_hotspots",
        "simplicity_warnings",
    )
    assert_same_attrs(repo_health, repo_health_links, "documentation_map_errors", "workflow_prompt_doc_errors")
    assert callable(repo_health.build_repo_health_report)


def test_repo_health_fast_skips_workflow_routing_when_inputs_unchanged(tmp):
    calls: list[str] = []

    def fake_generated_check(name, callback):
        _ = callback
        calls.append(name)
        return name, True, "ok"

    with patched_attrs(
        repo_health,
        generated_check=fake_generated_check,
        workflow_routing_check_required=lambda root: False,
        workflow_directories=lambda root: [],
    ), patched_attrs(
        repo_health.repo,
        get_skill_directories=lambda root: [],
    ):
        report = repo_health.build_repo_health_report(tmp, fast=True)

    assert_ok(report)
    assert_field(report, "mode", "fast")
    assert_lacks(calls, "workflow routing/registry")
    workflow_check = [
        item for item in report["generated_checks"] if item["name"] == "workflow routing/registry"
    ][0]
    assert_has_all(workflow_check["message"], "skipped fast", "no changed workflow routing inputs")


def test_repo_health_rejects_active_pycache(tmp):
    pycache = skill_path(skill_root(tmp), "scripts", "__pycache__")
    pycache.mkdir(parents=True)

    errors = repo_health.validate_no_pycache(tmp)

    assert_contains_all(errors, "__pycache__", "bytecode")


def test_repo_common_python_child_commands_disable_bytecode(tmp):
    script = tmp / "child.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    calls: list[tuple[list[str], dict[str, object]]] = []
    original_run = repo_common.subprocess.run

    class Completed:
        returncode = 0
        stdout = "ok\n"

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    try:
        repo_common.subprocess.run = fake_run
        code, output = repo_common.run_python_script_quiet(script, ["--flag"])
    finally:
        repo_common.subprocess.run = original_run

    assert code == 0
    assert output == "ok"
    assert calls
    command, kwargs = calls[0]
    assert command == [sys.executable, "-B", str(script), "--flag"]
    assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_repo_health_diff_checks_skip_outside_git_worktree(tmp):
    unstaged_code, unstaged_output = repo_health.run_diff_check(tmp, staged=False)
    staged_code, staged_output = repo_health.run_diff_check(tmp, staged=True)

    assert unstaged_code == 0
    assert staged_code == 0
    assert unstaged_output == "skipped: not a git repository"
    assert staged_output == "skipped: not a git repository"


def test_repo_health_allows_root_doc_diagram_assets(tmp):
    write_text(doc_path(tmp, "diagrams", "guide-flow.mmd"), 'graph TD;\n  A["Start"] --> B["Done"];')
    write_text(doc_path(tmp, "diagrams", "guide-flow.svg"), "<svg></svg>")

    errors = repo_health.validate_repo_layout(tmp)

    assert_lacks(errors, "root docs/")


def test_repo_health_workflow_budget_excludes_mermaid_blocks(tmp):
    large_diagram = "\n".join(f'      node{i}["Readable step {i}"] --> node{i + 1}["Readable step {i + 1}"];' for i in range(80))
    write_text(
        automation_path(tmp, "demo-workflow", "WORKFLOW.md"),
        f"""# Demo Workflow

Use as a compact workflow with inline diagrams.

## Process Diagram

::: mermaid
    graph TD;
{large_diagram}
:::
""",
    )

    warnings = repo_health.context_budget_warnings(tmp)

    assert_empty(warnings)


def test_repo_health_workflow_budget_allows_readable_mermaid_entrypoint(tmp):
    prose = " ".join("readable" for _ in range(280))
    write_text(
        automation_path(tmp, "demo-workflow", "WORKFLOW.md"),
        f"""# Demo Workflow

{prose}

::: mermaid
    graph TD;
      start["Start"] --> finish["Finish"];
:::
""",
    )

    warnings = repo_health.context_budget_warnings(tmp)

    assert_empty(warnings)


def test_repo_health_context_budget_checks_generated_navigation_map(tmp):
    navigation_map = tmp / "automations" / "navigation" / "artifacts" / "maps" / "NAVIGATION.md"
    write_text(navigation_map, "# Navigation\n\n" + " ".join("navigation" for _ in range(2000)))

    warnings = repo_health.context_budget_warnings(tmp)

    assert_contains_all(warnings, "automations/navigation/artifacts/maps/NAVIGATION.md", "route-first navigation map")


def test_repo_health_rejects_missing_root_doc_frontmatter(tmp):
    write_text(doc_path(tmp, "guide.md"), "# Guide")

    errors = repo_health.root_docs_frontmatter_errors(tmp)

    assert_contains(errors, "docs/guide.md missing frontmatter")


def test_repo_health_accepts_valid_root_doc_frontmatter(tmp):
    write_text(
        doc_path(tmp, "guide.md"),
        """---
title: Guide
type: guide
status: active
owner: skill-manager
audience: both
updated: 2026-05-27
---

# Guide
""",
    )

    assert_empty(repo_health.root_docs_frontmatter_errors(tmp))


def test_repo_health_requires_docs_in_documentation_map(tmp):
    write_text(
        doc_path(tmp, "start-here.md"),
        START_HERE_MD,
    )
    write_text(
        doc_path(tmp, "guide.md"),
        "# Guide\n",
    )
    map_path = doc_path(tmp, "reference", "documentation-map.md")
    write_text(
        map_path,
        "# Documentation Map\n\n- [Start Here](../start-here.md)\n",
    )

    errors = repo_health.documentation_map_errors(tmp)
    assert_contains_all(errors, "docs/guide.md", "documentation-map.md")

    write_text(
        map_path,
        "# Documentation Map\n\n- [Start Here](../start-here.md)\n- [Guide](../guide.md)\n",
    )

    assert_empty(repo_health.documentation_map_errors(tmp))


def test_repo_health_requires_copyable_workflow_prompt_doc(tmp):
    write_text(
        doc_path(tmp, "start-here.md"),
        "# Start\n\n[Workflows](workflow/using-workflows.md).\n",
    )
    write_text(
        doc_path(tmp, "workflow", "using-workflows.md"),
        """## Copyable Prompts

Start a user story:
```text
workflow start
```

Start a bug investigation:
```text
workflow start
```

Resume in a new chat:
```text
workflow resume
```

Recover after interruption:
```text
workflow recover
```

Review a plan:
```text
workflow start
```

Finish a run:
```text
workflow finish
```
""",
    )

    assert_empty(repo_health.workflow_prompt_doc_errors(tmp))

    bad_path = doc_path(tmp, "workflow", "using-workflows.md")
    bad_path.write_text(bad_path.read_text(encoding="utf-8").replace("Finish a run:", "Close a run:"), encoding="utf-8")
    errors = repo_health.workflow_prompt_doc_errors(tmp)
    assert_contains(errors, "Finish a run")


def test_repo_health_rejects_and_formats_compact_json(tmp):
    subprocess.run(["git", "init"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    target = tmp / "module.json"
    write_text(target, '{"schema_version":1,"kind":"workflow"}')
    cache_json = agent_path(tmp, "local-ai", "cache", "index.json")
    write_text(cache_json, '{"cache":true}')
    subprocess.run(["git", "add", "module.json"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    errors = repo_health.json_format_errors(tmp)
    assert_contains_all(errors, "module.json", "pretty-printed JSON")
    assert_lacks(errors, ".agents/local-ai/cache")

    report = repo_health.format_json_files(tmp)
    assert_ok(report)
    assert_field(report, "changed", ["module.json"])
    assert target.read_text(encoding="utf-8") == '{\n  "schema_version": 1,\n  "kind": "workflow"\n}\n'
    assert cache_json.read_text(encoding="utf-8") == '{"cache":true}\n'
    assert_empty(repo_health.json_format_errors(tmp))


def test_repo_health_ignores_deleted_tracked_json(tmp):
    subprocess.run(["git", "init"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    target = tmp / "removed.json"
    write_json(target, {"schema_version": 1})
    subprocess.run(["git", "add", "removed.json"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    target.unlink()

    assert_empty(repo_health.json_format_errors(tmp))
    report = repo_health.format_json_files(tmp, check=True)
    assert_ok(report)
    assert_field(report, "checked", 0)


def test_repo_health_consumer_install_ignores_host_project_json(tmp):
    subprocess.run(["git", "init"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    write_json(
        agent_path(tmp, "harness.lock.json"),
        {
            "schema_version": 1,
            "files": [
                {"path": ".agents/local-ai/policy.json", "sha256": "abc"},
            ],
        },
    )
    write_text(tmp / "package.json", '{"scripts":{"test":"echo host"}}')
    write_json(agent_path(tmp, "local-ai", "policy.json"), {"ok": True})
    subprocess.run(["git", "add", "package.json"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    errors = repo_health.json_format_errors(tmp)

    assert_empty(errors)


def test_repo_health_consumer_install_ignores_project_context_evidence_docs(tmp):
    write_json(
        agent_path(tmp, "harness.lock.json"),
        {
            "schema_version": 1,
            "files": [
                {"path": "docs/start-here.md", "sha256": "abc"},
            ],
        },
    )
    write_text(doc_path(tmp, "project", "project-context.generated.md"), "# Generated Project Context")
    write_text(doc_path(tmp, "project", "validation", "evidence", "run-1", "validation-report.md"), "# Evidence")
    write_json(doc_path(tmp, "project", "validation", "evidence", "run-1", "validation-report.json"), {"ok": True})
    write_text(agent_path(tmp, "manage.py"), "import runpy\n# dispatch to repo_manager.py\nrunpy.run_path('repo_manager.py')\n")

    assert_empty(repo_health.validate_repo_layout(tmp))
    assert_empty(repo_health.root_docs_frontmatter_errors(tmp))
    assert_empty(repo_health.documentation_map_errors(tmp))
    assert_empty(repo_health.root_docs_link_errors(tmp))


def test_repo_health_consumer_install_ignores_host_project_docs(tmp):
    write_json(
        agent_path(tmp, "harness.lock.json"),
        {
            "schema_version": 1,
            "files": [
                {"path": "docs/start-here.md", "sha256": "abc"},
                {"path": "docs/reference/documentation-map.md", "sha256": "def"},
            ],
        },
    )
    write_text(
        doc_path(tmp, "start-here.md"),
        """---
title: Start Here
type: guide
status: active
owner: skill-manager
audience: both
updated: 2026-06-12
---

# Start

[Documentation Map](reference/documentation-map.md).
""",
    )
    write_text(
        doc_path(tmp, "reference", "documentation-map.md"),
        """---
title: Documentation Map
type: reference
status: active
owner: skill-manager
audience: both
updated: 2026-06-12
---

# Documentation Map

- [Start Here](../start-here.md)
""",
    )
    write_text(doc_path(tmp, "architecture.md"), "# Architecture\n\nConsumer-owned docs need not use harness metadata.\n")

    assert_empty(repo_health.root_docs_frontmatter_errors(tmp))
    assert_empty(repo_health.documentation_map_errors(tmp))
    assert_empty(repo_health.root_docs_link_errors(tmp))


def test_repo_qol_parser_split_keeps_public_surface(tmp):
    assert_same_attrs(repo_qol, repo_qol_parsers, "add_qol_parsers", "add_output_format")

    parser = repo_cli_parser.build_parser()
    assert_parsed(parser, ["dashboard", "--full", "--capabilities", "--format", "json"], {"command": "dashboard", "full": True, "capabilities": True, **JSON_OUT})
    assert_parsed(parser, ["startup-context", *COMPACT_JSON], {"command": "startup-context", **COMPACT_JSON_EXPECTED})
    assert_parsed(parser, ["review-next", *COMPACT_JSON], {"command": "review-next", **COMPACT_JSON_EXPECTED})
    assert_parsed(parser, ["changed-context", *COMPACT_JSON], {"command": "changed-context", **COMPACT_JSON_EXPECTED})
    assert_parsed(parser, ["evidence-verify", *COMPACT_JSON], {"command": "evidence-verify", **COMPACT_JSON_EXPECTED})
    assert_parsed(parser, ["what-now", *COMPACT_JSON], {"command": "what-now", **COMPACT_JSON_EXPECTED})
    assert_parsed(parser, ["finish", *COMPACT_JSON], {"command": "finish", **COMPACT_JSON_EXPECTED})
    assert_parsed(
        parser,
        [
            "finish",
            "--release-full",
            "--budget-intent",
            "feature",
            "--commit-packet",
            "evidence/finish",
        ],
        {"command": "finish", "release_full": True, "budget_intent": "feature", "commit_packet": "evidence/finish"},
    )
    assert_parsed(
        parser,
        ["review-autopilot", "--release-full", "--budget-intent", "feature"],
        {"command": "review-autopilot", "release_full": True, "budget_intent": "feature"},
    )
    assert_parsed(
        parser,
        ["review-loop", "--include-validation", "--max-units", "10"],
        {"command": "review-loop", "include_validation": True, "max_units": 10},
    )
    assert_parsed(parser, ["finish", "--summary", "--format", "json"], {"summary": True, **JSON_OUT})
    assert_parsed(parser, ["finish", *COMPACT_JSON], {"compact": True})


def test_benchmark_release_gate_uses_latest_retained_run(tmp):
    old_run = automation_path(tmp, "agent-benchmarking", "runs", "old-run", "benchmark-result.json")
    current_run = automation_path(tmp, "agent-benchmarking", "runs", "current-run", "benchmark-result.json")
    write_json(old_run, {"schema_version": 1})
    write_json(current_run, {"schema_version": 1})
    # Make the intended run deterministically newer without relying on sleep or
    # filesystem timestamp granularity.
    os.utime(old_run, (1_700_000_000, 1_700_000_000))
    os.utime(current_run, (1_700_000_100, 1_700_000_100))
    calls = []

    def doctor_report(root, *, suite=None, run=None):
        calls.append((suite, run))
        return {"ok": True, "issues": []}

    status = repo_benchmark.benchmark_release_gate(["--json"], tmp, doctor_report)
    assert status == 0
    assert len(calls) == 6
    assert {run for _suite, run in calls} == {"automations/agent-benchmarking/runs/current-run"}


def test_benchmark_release_gate_reports_manual_only_github_validation(tmp):
    def doctor_report(root, *, suite=None, run=None):
        return {"ok": True, "issues": []}

    def github_validation_trigger_state(root):
        return {
            "status": "manual-only",
            "triggers": ["workflow_dispatch"],
            "manual_dispatch_enabled": True,
            "automatic_triggers": [],
            "automatic_triggers_enabled": False,
            "note": "automatic GitHub validation is paused",
        }

    with patched_attrs(repo_benchmark, github_validation_trigger_state=github_validation_trigger_state):
        status, report = capture_json(repo_benchmark.benchmark_release_gate, ["--json"], tmp, doctor_report)

    assert status == 0
    assert_fields(report["github_validation"], status="manual-only", automatic_triggers_enabled=False)
    assert_has_all(" ".join(report["advisories"]), LOCAL_DETERMINISTIC_GATES)


def test_benchmark_release_gate_summary_omits_nested_results(tmp):
    def doctor_report(root, *, suite=None, run=None):
        return {"ok": True, "status": "passed", "checks": [{"name": "suite", "ok": True}], "issues": [], "warnings": []}

    status, report = capture_json(repo_benchmark.benchmark_release_gate, ["--json", "--summary"], tmp, doctor_report)
    assert status == 0
    assert_summary(report, suite_count=6)
    assert_lacks_all(report["checks"][0], "result")
    assert_field(report["checks"][0], "check_count", 1)

    compact_status, compact = capture_json(
        repo_benchmark.benchmark_release_gate, ["--json", "--summary", "--compact"], tmp, doctor_report
    )
    assert compact_status == 0
    assert_summary(compact, suite_count=6)
    assert_keys_lack(compact, "checks", "skipped")


def test_benchmark_doctor_summary_keeps_counts_only(tmp):
    report = {
        "schema_version": 1,
        "tool": "agent-benchmarking.doctor",
        "ok": True,
        "status": "passed",
        "checks": [
            {"name": "suite", "ok": True, "path": "suite-a.json"},
            {"name": "run", "ok": True, "path": "run-a"},
        ],
        "issues": [],
        "warnings": ["non-comparable retained run"],
    }

    compact = repo_doctor_benchmarks.summarize_benchmark_doctor_report(report)

    assert_summary(compact, check_count=2, run_check_count=1, warning_count=1)
    assert_keys_lack(compact, "failed_checks")


def test_benchmark_doctor_summary_reports_comparable_pairs(tmp):
    suites = benchmark_dir(tmp, "suites")
    runs = benchmark_dir(tmp, "runs")
    write_benchmark_suite(suites, "current-agent-smoke", {"id": "summarize-guidance", "expected_checks": ["passes"]})
    assert_not_none(repo_doctor_benchmarks.benchmark_common)
    for run_id, score in (("current-a", 0.9), ("current-b", 1.0)):
        run_dir = runs / run_id
        report = repo_doctor_benchmarks.benchmark_common.normalized_model_benchmark_report(
            run_id=run_id,
            task_id="summarize-guidance",
            subject="current setup local gate",
            agent_tool="codex",
            model_label="current-setup-local-gate",
            workflow_name=BENCHMARK_WORKFLOW,
            workflow_version="1.0.0",
            quality={"passed": True, "score": score},
        )
        report["suite"] = "current-agent-smoke"
        write_benchmark_result(run_dir, report)

    summary = repo_doctor_benchmarks.summarize_benchmark_doctor_report(
        repo_doctor_benchmarks.benchmark_doctor_report(tmp)
    )

    assert_summary(
        summary,
        suite_check_count=1,
        run_check_count=2,
        comparable_run_pair_count=1,
        latest_comparison_status="comparable",
        lesson_promotion_candidate_count=0,
    )


def test_benchmark_doctor_surfaces_lesson_promotion_candidates(tmp):
    suites = benchmark_dir(tmp, "suites")
    runs = benchmark_dir(tmp, "runs")
    write_benchmark_suite(
        suites,
        "discipline-pressure-scenarios",
        {"id": "no-false-completion", "expected_checks": ["does not claim unrun checks"]},
    )
    assert_not_none(repo_doctor_benchmarks.benchmark_common)
    for run_id in ("lesson-a", "lesson-b"):
        run_dir = runs / run_id
        report = repo_doctor_benchmarks.benchmark_common.normalized_model_benchmark_report(
            run_id=run_id,
            task_id="no-false-completion",
            subject="false completion fixture",
            agent_tool="codex",
            model_label="fixture",
            workflow_name=BENCHMARK_WORKFLOW,
            workflow_version="1.0.0",
            quality={"passed": False, "score": 0.2},
            failures=[FALSE_VALIDATION_CLAIM],
            failure_taxonomy=[
                {
                    "category": "false-validation-claim",
                    "detail": FALSE_VALIDATION_CLAIM,
                    "evidence": "REPORT.md",
                }
            ],
            ok=False,
        )
        report["suite"] = "discipline-pressure-scenarios"
        write_benchmark_result(run_dir, report)

    report = repo_doctor_benchmarks.benchmark_doctor_report(tmp)
    summary = repo_doctor_benchmarks.summarize_benchmark_doctor_report(report)

    assert_ok(report)
    assert_summary(summary, lesson_promotion_candidate_count=1)
    assert_has_all(" ".join(report["warnings"]), "lesson-promotions")


def test_skill_lessons_groups_repeated_candidates(tmp):
    write_workflow_run(
        tmp,
        "story-flow",
        "run-a",
        lesson_candidates=[
            "Prefer deterministic evidence before promoting a lesson.",
            "Keep a one-off lesson as run evidence.",
        ],
    )
    write_workflow_run(
        tmp,
        "bug-flow",
        "run-b",
        lesson_candidates=["prefer deterministic evidence before promoting a lesson."],
    )

    report = repo_optimizations.lesson_promotion_queue(tmp)
    summary = repo_optimizations.summarize_lesson_queue(report, compact=True)

    assert_summary(report, candidate_count=3, unique_lesson_count=2, repeated_lesson_count=1, promotion_ready_count=1)
    repeated = summary["lesson_groups"][0]
    assert_fields(repeated, count=2, ready=True)
    assert_has_all(repeated["workflows"], "bug-flow", "story-flow")
    assert_keys_lack(summary, "candidates")


def test_skill_handoff_keeps_self_tests_in_validation_not_required_context(tmp):
    write_skill(tmp, "demo-skill")

    report = repo_optimizations.skill_handoff_packet(tmp, "demo-skill")

    required_context = report["required_next_context"]
    validation_plan = report["validation_plan"]
    assert_lacks(required_context, ".agents/skills/demo-skill/scripts/run_self_tests.py")
    assert_contains(validation_plan, "python -B .agents/skills/demo-skill/scripts/run_self_tests.py")


def test_benchmark_doctor_validates_routing_evidence_suite(tmp):
    suites = benchmark_dir(tmp, "suites")
    write_benchmark_suite(
        suites,
        "routing-evidence-real-use",
        {
            "id": "skill-routing-owner-evidence",
            "prompt": "Who owns accepted skill routing?",
            "expected_owner": "skill-manager",
            "required_skills": ["skill-manager"],
            "expected_checks": ["skill-manager is direct evidence"],
        },
    )

    report = repo_doctor_benchmarks.benchmark_doctor_report(tmp)
    summary = repo_doctor_benchmarks.summarize_benchmark_doctor_report(report)
    routing_check = next(item for item in report["checks"] if item["name"] == "routing-evidence-suite")

    assert_ok(report)
    assert_ok(routing_check)
    assert_summary(summary, routing_evidence_status="passed", routing_evidence_case_count=1)


def test_eval_quality_report_catches_stale_layout_and_behavior_gaps(tmp):
    write_json(
        skill_path(skill_root(tmp, "skill-manager"), "module.json"),
        {"schema_version": 3, "quality": {"eval_suites": ["suites/skill-manager-evals.json"]}},
    )
    write_json(
        skill_path(skill_root(tmp, "skill-manager"), "suites", "skill-manager-evals.json"),
        {"evals": [{"id": "shape-only", "assertions": [{"type": "file_exists", "path": "skill.json"}]}]},
    )
    write_json(
        skill_path(skill_root(tmp, "demo"), "docs", "demo-evals.json"),
        {"evals": [{"id": "old-layout", "assertions": [{"type": "validation_ok"}]}]},
    )
    write_json(
        automation_path(tmp, "story-flow", "Suites", "workflow-evals.json"),
        {"evals": []},
    )

    report = repo_health.eval_quality_report(tmp)

    assert_not_ok(report)
    assert_contains_each(report["issues"], "old eval suite layout", "empty eval suite")
    assert_contains_all(report["issues"], "stale eval path", "skill.json")
    assert_contains_all(report["issues"], "skill-manager", "command-level behavior")


def test_finish_deep_runs_aggregate_workflow_hook_gate(tmp):
    report, commands = finish_report_with_commands(tmp)

    assert_ok(report)
    assert_true(report, "completion_supported")
    assert_empty(report["missing_evidence"])
    assert_field(report["claim_receipt"], "status", "supported")
    assert_manage_command_ran(commands, "workflow", "hooks", "--all", "--check", "--format", "json")


def test_finish_deep_runs_clean_context_install_and_user_story_dogfood_gates(tmp):
    report, commands = finish_report_with_commands(tmp)

    assert_ok(report)
    assert_manage_command_ran(commands, "clean-context-proof", "--summary", "--compact", "--format", "json")
    assert_manage_command_ran(commands, "install-harness-smoke", "--fast", "--format", "json")
    assert_manage_command_ran(
        commands,
        "workflow",
        "smoke",
        "--name",
        "user-story-workflow",
        "--summary",
        "--compact",
        "--format",
        "json",
    )


def test_finish_deep_routes_changed_scope_without_running_exhaustive_repo_check(tmp):
    report, commands = finish_report_with_commands(tmp)

    assert_ok(report)
    assert not any(command[2:] == [MANAGE_PY, "check"] for command in commands), commands
    assert [MANAGE_PY, "check", "--deep"] not in [command[2:] for command in commands]
    assert any(
        command[2:4] == [MANAGE_PY, "check-changed"] and "--deep" in command
        for command in commands
    ), commands


def test_diff_checks_skip_outside_git_worktree(tmp):
    code, output = repo_health.run_diff_check(tmp, staged=False)
    assert code == 0
    assert "skipped: not a git repository" in output

    staged_code, staged_output = repo_health.run_diff_check(tmp, staged=True)
    assert staged_code == 0
    assert "skipped: not a git repository" in staged_output


def test_finish_deep_runs_aggregate_workflow_eval_gate(tmp):
    eval_args = (MANAGE_PY, "workflow", "eval", "--all", "--summary", "--compact", "--format", "json")
    report, commands = finish_report_with_commands(
        tmp,
        {
            eval_args: json.dumps(
                {
                    "ok": True,
                    "status": "passed",
                    "summary": {"workflows": 2, "suites": 2, "passed": 5, "failed": 0, "cases": 5},
                }
            )
        },
    )

    assert_ok(report)
    assert_manage_command_ran(commands, "workflow", "eval", "--all", "--summary", "--compact", "--format", "json")
    assert_field(report, "workflow_eval", {
        "status": "passed",
        "workflows": 2,
        "suites": 2,
        "passed": 5,
        "failed": 0,
        "cases": 5,
    })
    assert_has_all(repo_qol.render_finish_work(report), "Workflow eval suites: 2 checked, 5/5 cases")


def test_finish_deep_checks_workflow_run_indexes(tmp):
    for workflow in ("bug-flow", "story-flow"):
        write_workflow_run(tmp, workflow)
    report, commands = finish_report_with_commands(tmp)

    assert_ok(report)
    assert_manage_command_ran(commands, "index-workflow-runs", "--name", "story-flow", "--check", "--format", "json")
    assert_fields(report["workflow_run_indexes"], checked_count=2, workflows=["bug-flow", "story-flow"])
    assert_has_all(repo_qol.render_finish_work(report), "Workflow run indexes: 2 checked")


def test_finish_deep_checks_workflow_evidence_references(tmp):
    run_dir = write_workflow_run(
        tmp,
        "story-flow",
        commands=[{"evidence_path": "REPORT.md"}],
        evidence=[{"source": "validation/proof.json"}],
    )
    write_text(run_dir / "REPORT.md", "# Report")
    write_text(run_dir / "validation" / "proof.json", "{}")

    report = repo_qol.workflow_run_evidence_reference_report(tmp)

    assert_fields(report, checked_count=2, missing_count=0, run_count=1, status="ok")
    assert_empty(report["missing"])


def test_finish_deep_reports_missing_workflow_evidence_references(tmp):
    write_workflow_run(
        tmp,
        "story-flow",
        commands=[{"evidence_path": "missing.txt"}],
        evidence=[],
    )

    report = repo_qol.workflow_run_evidence_reference_report(tmp)

    assert_fields(report, status="missing", checked_count=1, missing_count=1)
    assert_field(report["missing"][0], "reference", "missing.txt")


def test_finish_deep_checks_story_bug_out_of_scope_templates(tmp):
    for workflow in ("bug-ticket-workflow", "user-story-workflow"):
        for template in ("ticket-info.md", "plan.md", "pr-description.md"):
            write_text(automation_path(tmp, workflow, "templates", template), "## Out Of Scope\n\n- Item")

    report = repo_qol.story_bug_out_of_scope_template_report(tmp)

    assert_fields(report, checked_count=6, missing_count=0, status="ok")
    assert_empty(report["missing"])


def test_finish_work_compacts_success_output_and_reports_metrics(tmp):
    checks = [
        {"ok": True, "command": "ok-command", "output_tail": "x" * 50, "elapsed_seconds": 1.25},
        {"ok": False, "command": "failed-command", "output_tail": "failure", "elapsed_seconds": 2.0},
    ]

    compacted, metrics = repo_qol.compact_finish_checks(checks)

    assert_field(compacted[0], "output_tail", "")
    assert_field(compacted[1], "output_tail", "failure")
    assert_fields(metrics, check_count=2, failed_count=1, output_tail_bytes_saved=50, elapsed_seconds=3.25)


def test_finish_work_uses_distilled_failed_output_when_available(tmp):
    checks = [
        {
            "ok": False,
            "command": "failed-command",
            "output_tail": "raw failure output " * 20,
            "distilled_output": "Notable lines:\n- ERROR: broken fixture",
            "raw_output_path": ".agents/local-ai/cache/command-output/raw.txt",
            "output_summary": {"bytes": 600},
            "elapsed_seconds": 1.0,
        },
    ]

    compacted, metrics = repo_qol.compact_finish_checks(checks)

    assert_field(compacted[0], "output_tail", "Notable lines:\n- ERROR: broken fixture")
    assert_field(compacted[0], "raw_output_path", ".agents/local-ai/cache/command-output/raw.txt")
    assert_fields(metrics, check_count=1, failed_count=1, output_tail_bytes_before=600)
    assert metrics["output_tail_bytes_saved"] > 500


def test_run_capture_reports_elapsed_seconds(tmp):
    result = repo_qol.run_capture(tmp, [sys.executable, "-B", "-c", "print('ok')"], timeout=30)

    assert_ok(result)
    assert result["elapsed_seconds"] >= 0.0


def test_run_capture_timeout_writes_raw_output_digest_and_timeout_metadata(tmp):
    result = repo_qol.run_capture(
        tmp,
        [sys.executable, "-B", "-c", "import time; time.sleep(2)"],
        timeout=1,
    )

    assert_not_ok(result)
    assert_fields(result, status=124, issue="command timed out", timeout_seconds=1)
    assert_has_all(result["distilled_output"], "timed out after 1 second")
    assert_has_all(result, "raw_output_path", "output_summary")
    assert_exists(tmp / result["raw_output_path"])
    assert result["output_summary"]["digest"]


def test_run_capture_timeout_kills_child_process_tree(tmp):
    script = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(30)'])\n"
        "time.sleep(30)\n"
    )

    result = repo_qol.run_capture(tmp, [sys.executable, "-B", "-c", script], timeout=1)

    assert_not_ok(result)
    assert_fields(result, status=124, issue="command timed out", timeout_seconds=1)
    assert result["elapsed_seconds"] < 10
    assert_has_all(result["distilled_output"], "timed out after 1 second")


def test_finish_budget_hotspots_are_bounded_advisory(tmp):
    skill_dir = tmp / ".agents" / "skills" / "demo"
    write_text(skill_dir / "SKILL.md", "---\nname: demo\ndescription: Demo skill.\n---\n\n# Demo\n")
    write_json(skill_dir / "module.json", {"name": "demo", "summary": "Demo skill.", "version": "0.1.0"})

    def fake_run_capture(_root, command, *, timeout=90):
        raise AssertionError("current budget hotspot report should not spawn measure-skill-budget")

    with patched_attrs(repo_qol_finish, run_capture=fake_run_capture):
        report = repo_qol_finish.budget_hotspots_report(tmp)

    assert_fields(report, ok=True, status="measured", advisory=True, parse_source="in-process-current")
    assert_field(report["summary"], "skill_count", 1)
    assert_field(report["baseline"], "status", "not-run")
    assert report["top"]


def test_finish_budget_hotspots_falls_back_to_bounded_timeout(tmp):
    calls: list[tuple[list[str], int]] = []

    def fake_current_budget_hotspots_report(_root):
        return {
            "ok": False,
            "status": "unavailable",
            "advisory": True,
            "issue": "forced current measurement failure",
            "top": [],
            "delta": {},
        }

    def fake_run_capture(_root, command, *, timeout=90):
        calls.append((command, timeout))
        return {
            "ok": False,
            "status": 124,
            "command": " ".join(command),
            "output_tail": "COMMAND TIMEOUT: budget stalled",
            "distilled_output": "COMMAND TIMEOUT: budget stalled",
            "raw_output_path": ".agents/local-ai/cache/command-output/budget-timeout.txt",
            "output_summary": {"bytes": 32, "lines": 1, "digest": "budgettimeout"},
            "issue": "command timed out",
            "elapsed_seconds": float(timeout),
            "timeout_seconds": timeout,
        }

    with patched_attrs(repo_qol_finish, run_capture=fake_run_capture):
        with patched_attrs(repo_qol_finish, current_budget_hotspots_report=fake_current_budget_hotspots_report):
            report = repo_qol_finish.budget_hotspots_report(tmp)

    assert_fields(report, ok=True, status="timeout", advisory=True, issue="command timed out")
    assert_field(report, "current_issue", "forced current measurement failure")
    assert_field(report, "raw_output_path", ".agents/local-ai/cache/command-output/budget-timeout.txt")
    assert calls
    assert calls[0][1] == 45
    assert calls[0][0][2:] == [
        ".agents/manage.py",
        "measure-skill-budget",
        "--all",
        "--baseline-ref",
        "HEAD",
        "--summary",
        "--compact",
        "--format",
        "json",
    ]


def test_finish_budget_hotspots_parse_truncated_raw_output(tmp):
    raw_rel = ".agents/local-ai/cache/command-output/budget-hotspots.json"
    write_json(
        tmp / raw_rel,
        {
            "summary": {"skill_count": 2},
            "delta": {"summary": {"total_text_words": 12}, "skills": [{"name": "skill-manager"}]},
            "top": [{"name": "skill-manager", "total_text_words": 100}],
            "baseline": {"ref": "HEAD", "ok": True, "issues": []},
        },
    )

    def fake_run_capture(_root, command, *, timeout=90):
        return {
            "ok": True,
            "status": 0,
            "command": " ".join(command),
            "output_tail": "\"truncated tail without object close",
            "raw_output_path": raw_rel,
            "output_summary": {"bytes": 9000, "lines": 80, "digest": "budgetdigest", "truncated": True},
            "elapsed_seconds": 0.25,
            "timeout_seconds": timeout,
        }

    with patched_attrs(
        repo_qol_finish,
        current_budget_hotspots_report=lambda root: {"ok": False, "status": "unavailable", "issue": "forced current failure"},
        run_capture=fake_run_capture,
    ):
        report = repo_qol_finish.budget_hotspots_report(tmp)

    assert_fields(report, ok=True, status="measured", advisory=True, parse_source="raw_output_path")
    assert_field(report["delta"]["summary"], "total_text_words", 12)
    assert_field(report["top"][0], "name", "skill-manager")


def test_finish_work_records_progress_events_timeout_metadata_and_timeout_feedback(tmp):
    def fake_run_capture(_root, command, *, timeout=90):
        command_text = " ".join(command)
        if "check-changed" in command:
            return {
                "ok": False,
                "status": 124,
                "command": command_text,
                "output_tail": "COMMAND TIMEOUT: check timed out",
                "distilled_output": "COMMAND TIMEOUT: check timed out",
                "raw_output_path": ".agents/local-ai/cache/command-output/check-timeout.txt",
                "output_summary": {"bytes": 32, "lines": 1, "digest": "timeoutdigest"},
                "issue": "command timed out",
                "elapsed_seconds": float(timeout),
                "timeout_seconds": timeout,
            }
        return {
            **captured_command_result(command),
            "elapsed_seconds": 0.1,
            "timeout_seconds": timeout,
            "phase": "fixture",
        }

    with patched_attrs(repo_qol, run_capture=fake_run_capture):
        report = repo_qol.finish_work_report(tmp, deep=False, skip_benchmark=True)

    assert_not_ok(report)
    assert_contains(report["progress_events"], "started")
    assert_contains(report["progress_events"], "completed")
    failed = [item for item in report["checks"] if not item["ok"]]
    assert len(failed) == 1
    assert_fields(failed[0], status=124, timeout_seconds=180, issue="command timed out")
    entries = read_feedback_lines(tmp)
    assert len(entries) == 1
    assert_fields(entries[0], failure_type="timeout", output_digest="timeoutdigest")


def test_run_capture_persists_raw_output_and_distills_failures(tmp):
    script = (
        "import sys\n"
        "for index in range(40): print(f'noise line {index}')\n"
        "print('ERROR: broken fixture')\n"
        "sys.exit(1)\n"
    )

    result = repo_qol.run_capture(tmp, [sys.executable, "-B", "-c", script], timeout=30)

    assert_not_ok(result)
    assert_has_all(result, "raw_output_path", "output_summary", "distilled_output")
    assert_contains([result["distilled_output"]], "ERROR: broken fixture")
    raw_path = tmp / result["raw_output_path"]
    assert_exists(raw_path)
    raw_text = raw_path.read_text(encoding="utf-8")
    assert_has_all(raw_text, "noise line 0", "ERROR: broken fixture")
    assert result["output_summary"]["bytes"] > len(result["distilled_output"])


def test_successful_validation_clears_stale_last_validation(tmp):
    stale = repo_local_ai.write_last_validation(tmp, "sync --check", "old failure")

    assert repo_local_ai.run_with_failure_triage(
        tmp,
        "sync --check",
        lambda: 0,
        ready_func=lambda _root: False,
        run_func=lambda _root, _path: (0, ""),
        policy_func=lambda _root: (False, "not needed"),
    ) == 0
    assert_missing(stale)


def test_failure_triage_keeps_json_stdout_parseable_for_machine_commands(tmp):
    def failing_json_runner():
        print(json.dumps({"ok": False, "status": "failed"}))
        return 1

    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        status = repo_local_ai.run_with_failure_triage(
            tmp,
            "check-changed",
            failing_json_runner,
            ready_func=lambda _root: False,
            run_func=lambda _root, _path: (0, ""),
            policy_func=lambda _root: (False, "not needed"),
            json_stdout=True,
        )

    assert status == 1
    assert_field(json.loads(stdout.getvalue()), "status", "failed")
    assert_has_all(stderr.getvalue(), "Evidence:", "last-validation.txt")


def test_what_now_summary_is_compact(tmp):
    missing = repo_qol.what_now_report(tmp, input_value=".agents/local-ai/cache/last-validation.txt")
    repo_local_ai.write_last_validation(tmp, "check", "ERROR: broken fixture")

    report = repo_qol.what_now_report(tmp)
    compact = repo_qol.summarize_what_now_report(report, compact=True)

    assert_fields(compact, failure_type="failed-check", likely_owner="skill-manager")
    assert_lacks_all(compact, "command_result")
    assert compact["next_command"].startswith("python -B .agents/manage.py")
    assert_fields(missing, failure_type="missing-file-or-dependency", optional_local_ai_command="")


def test_what_now_from_command_writes_distilled_last_validation(tmp):
    raw_rel = ".agents/local-ai/cache/command-output/raw.txt"
    write_text(tmp / raw_rel, "full raw output\nERROR: broken fixture")

    def fake_run_capture_shell(_root, command, *, timeout=600):
        return {
            "ok": False,
            "status": 1,
            "command": command,
            "output_tail": "NOISE" * 1000,
            "distilled_output": "Notable lines:\n- ERROR: broken fixture",
            "raw_output_path": raw_rel,
            "output_summary": {"bytes": 5000, "lines": 200, "digest": "abc123"},
        }

    with patched_attrs(repo_qol, run_capture_shell=fake_run_capture_shell):
        report = repo_qol.what_now_report(tmp, from_command="demo command")

    cached = (tmp / ".agents/local-ai/cache/last-validation.txt").read_text(encoding="utf-8")
    assert_fields(report, failure_type="failed-check", likely_owner="skill-manager")
    assert_field(report, "first_failing_fact", "- ERROR: broken fixture")
    assert_has_all(cached, "Raw output:", raw_rel, "ERROR: broken fixture", "digest abc123")
    assert "NOISE" * 20 not in cached


def test_what_now_from_success_reports_cache_cleanup_warning(tmp):
    cache_path = tmp / ".agents/local-ai/cache/last-validation.txt"
    cache_path.mkdir(parents=True)

    def fake_run_capture_shell(_root, command, *, timeout=600):
        return {
            "ok": True,
            "status": 0,
            "command": command,
            "output_tail": "ok",
            "distilled_output": "ok",
        }

    with patched_attrs(repo_qol, run_capture_shell=fake_run_capture_shell):
        report = repo_qol.what_now_report(tmp, from_command="demo command")

    compact = repo_qol.summarize_what_now_report(report, compact=True)

    assert_fields(report, failure_type="passed", likely_owner="skill-manager")
    assert_has_all(" ".join(report.get("warnings", [])), "last-validation", "Could not clear")
    assert_has_all(" ".join(compact.get("warnings", [])), "last-validation", "Could not clear")


def test_finish_deep_reports_manual_only_github_validation(tmp):
    write_text(
        tmp / ".github" / "workflows" / "validate-skills.yml",
        VALIDATE_SKILLS_WORKFLOW,
    )

    def fake_run_capture(root, command, *, timeout=90):
        return captured_command_result(command)

    with patched_attrs(
        repo_qol,
        run_capture=fake_run_capture,
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "stale_output_count": 0,
            "summary": "fresh",
        },
        auto_refresh_navigation=lambda root: {"ok": True, "status": "skipped-fresh"},
    ):
        report = repo_qol.finish_work_report(tmp, deep=True, skip_benchmark=True)

    assert_ok(report)
    assert_status(report["github_validation"], "manual-only")
    assert_has_all(" ".join(report["advisories"]), LOCAL_DETERMINISTIC_GATES)
    assert_has_all(repo_qol.render_finish_work(report), "GitHub validation is manual-only")


def test_github_validation_absence_is_local_only(tmp):
    state = repo_qol_github.github_validation_trigger_state(tmp)
    advisories = repo_qol_github.github_validation_advisories(state)

    assert_fields(state, status="local-only", automatic_triggers_enabled=False, manual_dispatch_enabled=False)
    assert_contains(advisories, "local-only")


def test_dashboard_reports_manual_only_github_validation(tmp):
    write_text(
        tmp / ".github" / "workflows" / "validate-skills.yml",
        VALIDATE_SKILLS_WORKFLOW,
    )

    with patched_attrs(
        repo_qol.repo_health,
        build_repo_health_report=lambda root: {
            "ok": True,
            "generated_checks": [{"name": "routing", "ok": True, "message": "ok"}],
        },
    ), patched_attrs(
        repo_qol.repo_doctor,
        git_dirty_state=lambda root: {
            "ok": True,
            "dirty": False,
            "status": "clean",
            "tracked_dirty": [],
            "untracked": [],
        },
        setup_local_ai_readiness=lambda root: {"ok": True, "status": "skipped"},
    ), patched_attrs(
        repo_qol.repo_changed,
        changed_files=lambda root: [],
    ), patched_attrs(repo_qol_dashboard, branch_name=lambda root: "test-branch"):
        report = repo_qol.dashboard_report(tmp, skip_local_ai=True, skip_github=True)

    github_validation = report["github_validation"]
    assert_fields(
        github_validation,
        status="manual-only",
        manual_dispatch_enabled=True,
        automatic_triggers_enabled=False,
    )
    assert_empty(github_validation["automatic_triggers"])
    assert_has_all(repo_qol.render_dashboard(report), "GitHub validation: manual-only")


def test_dashboard_reuses_review_packet_diff_estimate_for_context_budget(tmp):
    changed_path = ".agents/skills/skill-manager/scripts/repo_support/demo.py"
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_file_count": 1,
        "changed_diff_estimated_tokens": 7000,
        "tracked_diff_estimated_tokens": 6000,
        "untracked_file_estimated_tokens": 1000,
        "tracked_changed_file_count": 1,
        "untracked_changed_file_count": 2,
        "review_budget_tokens": 5000,
        "tokens_over_review_budget": 2000,
        "owner_review_packet_count": 1,
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "status": "within-budget",
                "scope": "owner",
                "changed_file_count": 1,
                "estimated_changed_tokens": 3000,
                "owner_summary_command": "python -B .agents/manage.py review-packet --owner skill:skill-manager --summary --compact --format json",
            }
        ],
        "cost_ledger": {
            "status": "measured",
            "billing_scope": "input-context-estimate-only",
            "raw_changed_diff_estimated_tokens": 7000,
            "next_review_unit_estimated_tokens": 3000,
            "single_agent_saved_tokens_vs_raw_estimated": 4000,
            "single_agent_saved_percent_vs_raw_estimated": 57.14,
        },
    }

    def fail_diff_estimate(_root):
        raise AssertionError("dashboard should reuse review_packet token estimates")

    with patched_attrs(
        repo_qol.repo_health,
        build_repo_health_report=lambda root: {
            "ok": True,
            "generated_checks": [{"name": "routing", "ok": True, "message": "ok"}],
        },
    ), patched_attrs(
        repo_qol.repo_doctor,
        git_dirty_state=lambda root: {"ok": True, "dirty": True, "status": "dirty"},
    ), patched_attrs(
        repo_qol_dashboard.repo_changed,
        changed_files=lambda root: [changed_path],
        large_diff_review_packet=lambda root, paths, validation_plan, navigation: dict(packet),
        compact_path_groups=lambda paths: ".agents/skills/skill-manager/ (1)",
    ), patched_attrs(
        repo_qol_dashboard,
        changed_validation_router_report=lambda root, changed: {
            "status": "planned",
            "changed_file_count": len(changed),
            "summary": {"command_count": 0, "required_count": 0, "optional_count": 0, "owners": {}},
            "commands": [],
            "next_command": "python -B .agents/manage.py check-changed --summary --compact --format json",
        },
        branch_name=lambda root: "test-branch",
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "Navigation maps are fresh.",
        },
    ), patched_attrs(
        repo_qol_dashboard.repo_cost_policy,
        changed_diff_estimate=fail_diff_estimate,
    ):
        report = repo_qol.dashboard_report(tmp, skip_local_ai=True)

    assert_field(report["context_budget"], "changed_diff_estimated_tokens", 7000)
    assert_field(report["context_budget"], "tracked_diff_estimated_tokens", 6000)
    assert_field(report["context_budget"], "untracked_file_estimated_tokens", 1000)
    assert_field(report["context_budget"], "tracked_changed_file_count", 1)
    assert_field(report["context_budget"], "untracked_changed_file_count", 2)
    assert_has_all(report["next_command"], "review-loop", "--max-units")


def test_dashboard_routes_over_budget_diff_to_owner_review(tmp):
    changed_path = ".agents/skills/skill-manager/scripts/repo_support/demo.py"
    with patched_attrs(
        repo_qol.repo_health,
        build_repo_health_report=lambda root: {
            "ok": True,
            "generated_checks": [{"name": "routing", "ok": True, "message": "ok"}],
        },
    ), patched_attrs(
        repo_qol.repo_doctor,
        git_dirty_state=lambda root: {"ok": True, "dirty": True, "status": "dirty"},
    ), patched_attrs(
        repo_qol_dashboard.repo_changed,
        changed_files=lambda root: [changed_path],
        changed_file_statuses=lambda root: {changed_path: {"M"}},
        changed_path_token_estimates=lambda root, paths: {changed_path: {"estimated_tokens": 3000}},
        changed_scope=lambda paths: {"skill_names": {"skill-manager"}, "workflows": False},
    ), patched_attrs(
        repo_qol_dashboard,
        branch_name=lambda root: "test-branch",
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "Navigation maps are fresh.",
        },
    ), patched_attrs(
        repo_qol_dashboard.repo_cost_policy,
        changed_diff_estimate=lambda root: {"estimated_tokens": 7000, "tracked_estimated_tokens": 7000, "untracked_estimated_tokens": 0},
    ):
        report = repo_qol.dashboard_report(tmp, skip_local_ai=True)

    compact = repo_qol.summarize_dashboard_report(report, compact=True)
    assert_has_all(report["next_command"], "review-loop", "--max-units 20")
    assert_has_all(compact["next_command"], "review-loop", "--max-units 20")
    assert_summary(
        compact,
        review_packet_status="over-budget",
        owner_review_packet_count=1,
        review_single_agent_saved_tokens_estimated=4000,
        review_single_agent_saved_percent_estimated=57.14,
    )
    assert_field(compact["review_packet"]["cost_ledger"], "next_review_unit_estimated_tokens", 3000)
    assert_field(compact["review_packet"]["review_plan_summary"], "status", "needs-review")
    assert_keys_lack(
        compact["review_packet"],
        "read_first",
        "owner_review_commands",
        "owner_summary_commands",
        "validation_first",
    )
    assert_keys_lack(
        compact["review_packet"]["review_plan_summary"],
        "next_pending_command",
        "resume_rule",
    )
    assert_field(compact["review_packet"]["review_cost_report"], "billing_scope", "input-context-estimate-only")
    assert_field(
        compact["review_packet"]["review_cost_report"]["money_saving_estimate"],
        "default_output_price_multiplier",
        4,
    )
    assert_field(compact["output_budget"], "status", "within-budget")
    assert compact["output_budget"]["estimated_output_tokens"] <= compact["output_budget"]["budget_tokens"]
    assert_field(
        compact["output_budget"],
        "estimated_output_tokens",
        repo_command_metrics.estimated_json_output_tokens(compact),
    )


def test_dashboard_uses_review_progress_next_pending_command(tmp):
    changed = ["AGENTS.md"]
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_file_count": 1,
        "changed_diff_estimated_tokens": 7000,
        "review_budget_tokens": 5000,
        "validation_first": [],
        "owner_review_packets": [
            {
                "owner": "skill:first",
                "status": "within-budget",
                "scope": "owner",
                "changed_file_count": 1,
                "estimated_changed_tokens": 100,
                "owner_summary_command": "python -B .agents/manage.py review-packet --owner skill:first --summary --compact --format json",
            },
            {
                "owner": "skill:second",
                "status": "within-budget",
                "scope": "owner",
                "changed_file_count": 1,
                "estimated_changed_tokens": 120,
                "owner_summary_command": "python -B .agents/manage.py review-packet --owner skill:second --summary --compact --format json",
            },
        ],
    }
    plan = repo_review_progress.build_review_plan(packet)
    first_command = plan["review_units"][0]["command"]
    fingerprint = repo_qol_dashboard.input_fingerprint_report(tmp, changed, [])
    repo_review_progress.review_progress_report(
        tmp,
        plan,
        input_fingerprint=fingerprint,
        mark_command=first_command,
    )

    with patched_attrs(
        repo_qol.repo_health,
        build_repo_health_report=lambda root: {
            "ok": True,
            "generated_checks": [{"name": "routing", "ok": True, "message": "ok"}],
        },
    ), patched_attrs(
        repo_qol.repo_doctor,
        git_dirty_state=lambda root: {"ok": True, "dirty": True, "status": "dirty"},
    ), patched_attrs(
        repo_qol_dashboard.repo_changed,
        changed_files=lambda root: changed,
        large_diff_review_packet=lambda root, paths, validation_plan, navigation: packet,
        compact_path_groups=lambda paths: "AGENTS.md (1)",
    ), patched_attrs(
        repo_qol_dashboard,
        changed_validation_router_report=lambda root, changed: {
            "status": "planned",
            "changed_file_count": len(changed),
            "summary": {"command_count": 0, "required_count": 0, "optional_count": 0, "owners": {}},
            "commands": [],
            "next_command": "python -B .agents/manage.py check-changed --summary --compact --format json",
        },
        branch_name=lambda root: "test-branch",
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "Navigation maps are fresh.",
        },
    ):
        report = repo_qol.dashboard_report(tmp, skip_local_ai=True)

    compact = repo_qol.summarize_dashboard_report(report, compact=True)
    assert_has_all(report["next_command"], "review-loop", "--max-units 20")
    assert_has_all(report["review_packet"]["next_review_command"], "--owner skill:second")
    assert_has_all(compact["review_packet"]["next_review_command"], "--owner skill:second")
    assert_field(compact["review_progress"], "review_state", "partial")
    assert_field(compact["review_progress"]["current_unit"], "owner", "skill:second")
    assert_field(compact["review_packet"]["review_plan_summary"], "review_state", "partial")
    assert_field(compact["review_packet"]["review_plan_summary"], "pending_unit_count", 1)
    assert_keys_lack(compact["review_progress"], "next_pending_command", "state_path", "fingerprint_digest")
    assert_keys_lack(compact["review_packet"]["review_plan_summary"], "next_pending_command")


def test_dashboard_compact_review_progress_summarizes_long_path_batches(_tmp):
    long_paths = [
        "automations/navigation/artifacts/maps/owners/workflow-local-ai-benchmark-workflow.md",
        "automations/navigation/artifacts/maps/handoff.json",
        "automations/navigation/artifacts/maps/owners/workflow-agent-benchmarking.md",
        "automations/navigation/artifacts/maps/owners/skill-agent-benchmarking.md",
        "automations/navigation/artifacts/maps/owners/skill-mermaid-diagrams-azure-devops.md",
        "automations/navigation/artifacts/maps/owners/workflow-disciplined-change-workflow.md",
        "automations/navigation/artifacts/maps/owners/workflow-dotnet-framework-migration.md",
        "automations/navigation/artifacts/maps/owners/workflow-candidate-import-workflow.md",
        "automations/navigation/artifacts/maps/owners/skill-playwright-integration.md",
        "automations/navigation/artifacts/maps/owners/skill-workflow-manager.md",
    ]
    compact = repo_qol_dashboard._compact_dashboard_review_progress(
        {
            "status": "in-progress",
            "review_state": "partial",
            "completed_unit_count": 156,
            "pending_unit_count": 10,
            "stale": False,
            "current_unit": {
                "scope": "path-batch",
                "owner": "workflow:navigation",
                "path": ",".join(long_paths),
                "hunk": "",
                "estimated_changed_tokens": 4714,
            },
        }
    )

    current = compact["current_unit"]
    assert_field(current, "scope", "path-batch")
    assert_field(current, "owner", "workflow:navigation")
    assert_field(current, "first_path", long_paths[0])
    assert_field(current, "path_count", len(long_paths))
    assert_field(current, "omitted_path_count", len(long_paths) - 1)
    assert_keys_lack(current, "path")
    assert repo_command_metrics.estimated_json_output_tokens(compact) < 120


def test_dashboard_compact_drops_owner_context_when_review_progress_is_active(_tmp):
    owner_rows = [
        {
            "owner": f"skill:demo-{index}",
            "status": "over-budget",
            "changed_file_count": index,
            "estimated_changed_tokens": 1000 + index,
            "capsule": f"automations/navigation/artifacts/maps/owners/skill-demo-{index}.md",
            "next_command": (
                "python -B .agents/manage.py review-packet "
                f"--owner skill:demo-{index} --summary --compact --format json"
            ),
        }
        for index in range(1, 10)
    ]
    report = {
        "schema_version": 1,
        "tool": "repo-dashboard",
        "status": "ok",
        "ok": True,
        "mode": "fast",
        "branch": "feature/demo",
        "changed_file_count": 20,
        "changed_groups": ".agents/skills/skill-manager/ (20)",
        "plain_status": "20 changed file(s) need evidence.",
        "next_command": "python -B .agents/manage.py review-loop --max-units 20 --summary --compact --format json",
        "next_command_reason": "Changed diff exceeds the review budget.",
        "dirty_state": {"ok": True, "dirty": True, "status": "dirty"},
        "navigation": {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "Navigation maps are fresh.",
        },
        "context_trace": {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "skip_raw_json": ["automations/navigation/artifacts/maps/project-map.json"],
        },
        "review_packet": {
            "status": "over-budget",
            "changed_diff_estimated_tokens": 420000,
            "review_budget_tokens": 5000,
            "tokens_over_review_budget": 415000,
            "owner_review_packet_count": 9,
            "owner_review_subpacket_count": 30,
            "owner_review_hunk_count": 300,
            "largest_owner_subpacket_estimated_tokens": 9000,
            "largest_owner_hunk_estimated_tokens": 1200,
            "next_review_command": (
                "python -B .agents/manage.py review-packet --owner skill:demo "
                "--path .agents/skills/skill-manager/scripts/run_self_tests.py --summary --compact --format json"
            ),
            "affected_owner_context": {
                "status": "present",
                "owner_count": 9,
                "omitted_owner_count": 0,
                "read_rule": "Read HANDOFF.md, then only the matching owner capsule.",
                "owners": owner_rows,
            },
            "cost_ledger": {
                "status": "measured",
                "billing_scope": "input-context-estimate-only",
                "raw_changed_diff_estimated_tokens": 420000,
                "next_review_unit_estimated_tokens": 1200,
                "largest_review_unit_estimated_tokens": 3000,
                "review_unit_count": 100,
                "source_review_unit_count": 500,
                "batched_review_unit_count": 100,
                "saved_batched_review_unit_count": 400,
                "max_hunks_per_batch_limit": 12,
            },
            "review_plan_summary": {
                "status": "needs-review",
                "review_state": "partial",
                "completed_unit_count": 10,
                "pending_unit_count": 90,
                "pending_review_unit_count": 82,
                "pending_validation_unit_count": 8,
                "review_unit_count": 100,
                "validation_unit_count": 8,
            },
        },
        "review_progress": {
            "status": "in-progress",
            "review_state": "partial",
            "completed_unit_count": 10,
            "pending_unit_count": 90,
            "stale": False,
            "current_unit": {
                "scope": "hunk-batch",
                "owner": "skill:demo",
                "path": ".agents/skills/skill-manager/scripts/run_self_tests.py",
                "hunk": "h001,h002",
                "estimated_changed_tokens": 1200,
            },
        },
        "summary": {
            "changed_diff_estimated_tokens": 420000,
            "review_packet_status": "over-budget",
            "owner_review_packet_count": 9,
            "owner_review_subpacket_count": 30,
            "owner_review_hunk_count": 300,
            "low_context_tokens": 4500,
        },
        "benchmark": {"ok": True, "status": "skipped"},
        "github_validation": {"status": "local-only"},
        "validation_router": {"status": "planned", "summary": {}, "commands": []},
        "validation_progress": {"status": "passed"},
        "latency_budget": {"status": "within-budget"},
    }

    compact = repo_qol_dashboard.summarize_dashboard_report(report, compact=True)

    assert_field(compact["review_progress"]["current_unit"], "owner", "skill:demo")
    assert_field(compact["review_packet"]["cost_ledger"], "next_review_unit_estimated_tokens", 1200)
    assert_keys_lack(compact["review_packet"], "affected_owner_context")
    assert compact["output_budget"]["estimated_output_tokens"] <= 1600


def test_dashboard_compact_keeps_review_packet_when_review_progress_is_stale(_tmp):
    report = healthy_dashboard_fixture()
    report["review_packet"] = {
        "status": "over-budget",
        "review_budget_tokens": 5000,
        "changed_diff_estimated_tokens": 6000,
        "tokens_over_review_budget": 1000,
    }
    report["review_progress"] = {
        "status": "stale",
        "review_state": "stale",
        "stale": True,
        "completed_unit_count": 1,
        "pending_unit_count": 0,
        "coverage": {
            "status": "complete",
            "pending_review_unit_count": 0,
        },
    }

    compact = repo_qol_dashboard.summarize_dashboard_report(report, compact=True)

    assert_field(compact["review_progress"], "stale", True)
    assert_field(compact["review_packet"], "status", "over-budget")


def test_dashboard_routes_to_validation_after_review_progress_complete(tmp):
    changed = ["AGENTS.md"]
    (tmp / "AGENTS.md").write_text("# Repo\n", encoding="utf-8")
    validation_command = "python -B .agents/manage.py check-additions"
    validation_plan = [
        {
            "order": 1,
            "command": validation_command,
            "reason": "changed or new files must have an owning contract",
            "owner": "skill-manager",
            "required": True,
        }
    ]
    owner_command = "python -B .agents/manage.py review-packet --owner skill:skill-manager --summary --compact --format json"
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_file_count": 1,
        "changed_diff_estimated_tokens": 7000,
        "review_budget_tokens": 5000,
        "validation_first": [validation_command],
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "status": "within-budget",
                "scope": "owner",
                "changed_file_count": 1,
                "estimated_changed_tokens": 100,
                "owner_summary_command": owner_command,
            }
        ],
    }
    plan = repo_review_progress.build_review_plan(packet)
    fingerprint = repo_qol_dashboard.input_fingerprint_report(tmp, changed, validation_plan)
    repo_review_progress.review_progress_report(
        tmp,
        plan,
        input_fingerprint=fingerprint,
        mark_command=owner_command,
    )

    with patched_attrs(
        repo_qol.repo_health,
        build_repo_health_report=lambda root: {
            "ok": True,
            "generated_checks": [{"name": "routing", "ok": True, "message": "ok"}],
        },
    ), patched_attrs(
        repo_qol.repo_doctor,
        git_dirty_state=lambda root: {"ok": True, "dirty": True, "status": "dirty"},
    ), patched_attrs(
        repo_qol_dashboard.repo_changed,
        changed_files=lambda root: changed,
        large_diff_review_packet=lambda root, paths, validation_plan_arg, navigation: packet,
        compact_path_groups=lambda paths: "AGENTS.md (1)",
    ), patched_attrs(
        repo_qol_dashboard,
        changed_validation_router_report=lambda root, changed_arg: {
            "status": "planned",
            "changed_file_count": len(changed_arg),
            "summary": {"command_count": 1, "required_count": 1, "optional_count": 0, "owners": {"skill-manager": 1}},
            "commands": validation_plan,
            "next_command": validation_command,
        },
        branch_name=lambda root: "test-branch",
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "Navigation maps are fresh.",
        },
    ):
        report = repo_qol.dashboard_report(tmp, skip_local_ai=True)

    compact = repo_qol.summarize_dashboard_report(report, compact=True)
    assert report["next_command"] == validation_command, report
    assert compact["next_command"] == validation_command, compact
    assert_field(report["review_progress"]["coverage"], "status", "complete")
    assert_field(report["review_progress"]["coverage"], "pending_review_unit_count", 0)
    assert_field(report["review_progress"]["current_unit"], "scope", "validation")
    assert_lacks_all(report["next_command"], "review-loop")
    assert_lacks_all(compact["next_command"], "review-loop")
    assert "review_packet" not in compact


def test_dashboard_routes_to_finish_when_validation_progress_covers_every_required_check(tmp):
    changed = ["AGENTS.md"]
    (tmp / "AGENTS.md").write_text("# Repo\n", encoding="utf-8")
    validation_command = "python -B .agents/manage.py check-additions"
    finish_command = "python -B .agents/manage.py finish"
    validation_plan = [
        {
            "order": 1,
            "check_id": "check-additions",
            "command": validation_command,
            "reason": "changed or new files must have an owning contract",
            "owner": "skill-manager",
            "required": True,
        }
    ]
    owner_command = "python -B .agents/manage.py review-packet --owner skill:skill-manager --summary --compact --format json"
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget",
        "changed_file_count": 1,
        "changed_diff_estimated_tokens": 7000,
        "review_budget_tokens": 5000,
        "validation_first": [validation_command],
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "status": "within-budget",
                "scope": "owner",
                "changed_file_count": 1,
                "estimated_changed_tokens": 100,
                "owner_summary_command": owner_command,
            }
        ],
    }
    plan = repo_review_progress.build_review_plan(packet)
    fingerprint = repo_qol_dashboard.input_fingerprint_report(tmp, changed, validation_plan)
    repo_review_progress.review_progress_report(
        tmp,
        plan,
        input_fingerprint=fingerprint,
        mark_command=owner_command,
    )

    with patched_attrs(
        repo_qol.repo_health,
        build_repo_health_report=lambda root: {
            "ok": True,
            "generated_checks": [{"name": "routing", "ok": True, "message": "ok"}],
        },
    ), patched_attrs(
        repo_qol.repo_doctor,
        git_dirty_state=lambda root: {"ok": True, "dirty": True, "status": "dirty"},
    ), patched_attrs(
        repo_qol_dashboard.repo_changed,
        changed_files=lambda root: changed,
        large_diff_review_packet=lambda root, paths, validation_plan_arg, navigation: packet,
        compact_path_groups=lambda paths: "AGENTS.md (1)",
    ), patched_attrs(
        repo_qol_dashboard,
        changed_validation_router_report=lambda root, changed_arg: {
            "status": "planned",
            "changed_file_count": len(changed_arg),
            "summary": {"command_count": 1, "required_count": 1, "optional_count": 0, "owners": {"skill-manager": 1}},
            "commands": validation_plan,
            "next_command": validation_command,
        },
        branch_name=lambda root: "test-branch",
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "Navigation maps are fresh.",
        },
    ), patched_attrs(
        repo_qol_dashboard.repo_command_metrics,
        read_validation_progress=lambda root: {
            "command": "check-changed",
            "phase": "complete",
            "status": "passed",
            "extra": {
                "failed_check_count": 0,
                "input_fingerprint_digest": fingerprint["digest"],
                "profile": "changed",
                "required_check_ids": ["check-additions"],
                "passed_check_ids": ["check-additions"],
            },
        },
    ):
        report = repo_qol.dashboard_report(tmp, skip_local_ai=True)

    compact = repo_qol.summarize_dashboard_report(report, compact=True)
    assert report["next_command"] == finish_command, report
    assert compact["next_command"] == finish_command, compact
    assert_field(report["validation_router"], "status", "satisfied-by-validation-progress")
    assert_field(report["validation_router"], "next_command", "none, validation progress is current")
    assert_field(report["validation_router"], "planned_next_command", validation_command)
    assert_field(compact["validation_router"], "status", "satisfied-by-validation-progress")
    assert_field(compact["validation_router"], "next_command", "none, validation progress is current")
    assert_field(compact["validation_router"], "planned_next_command", validation_command)
    assert_field(compact["validation_progress"], "input_fingerprint_match", True)
    assert_field(report["review_packet"], "next_review_command", "none, validation progress is current")
    assert_field(report["review_packet"], "planned_next_review_command", validation_command)
    assert "review_packet" not in compact
    assert_field(compact["summary"], "review_packet_status", "complete")
    assert_field(compact["summary"], "review_packet_tokens_over_budget", 0)
    assert_field(compact["review_progress"], "status", "complete")
    assert_field(compact["review_progress"], "review_state", "complete")
    assert_field(compact["review_progress"], "pending_unit_count", 0)
    assert_keys_lack(compact["review_progress"], "current_unit")
    assert_field(report["review_progress"]["coverage"], "status", "complete")
    assert_field(report["review_progress"]["coverage"], "pending_review_unit_count", 0)
    assert_has_all(report["next_command_reason"], "validation progress", "run finish")


def test_dashboard_summary_keeps_actionable_counts(tmp):
    report = healthy_dashboard_fixture()
    compact = repo_qol.summarize_dashboard_report(report)

    assert_summary(compact, check_count=0, low_context_tokens=3200)
    assert_lacks_all(compact, "loaded_context_ledger")


def test_finish_summary_keeps_failures_and_counts(tmp):
    report = {
        "schema_version": 1,
        "tool": "repo-finish",
        "ok": True,
        "status": "passed",
        "checks": [
            {"command": "check-a", "ok": True, "status": 0, "output_tail": ""},
            {"command": "check-b", "ok": False, "status": 1, "returncode": 1, "output_tail": "failed"},
        ],
        "workflow_run_indexes": {"checked_count": 2, "workflows": ["a", "b"]},
        "workflow_eval": {"passed": 3, "failed": 0},
        "workflow_evidence_references": {"checked_count": 4, "missing_count": 0},
        "story_bug_out_of_scope_templates": {"checked_count": 6, "missing_count": 0},
        "budget_hotspots": {
            "ok": True,
            "status": "measured",
            "delta": {"summary": {"total_text_words": 12, "tool_load_words": 0}},
            "top": [{"name": "skill-manager", "total_text_words": 100, "largest_file": "SKILL.md"}],
            "baseline": {"ref": "HEAD", "ok": True},
        },
        "check_metrics": {"output_tail_bytes_saved": 100},
        "github_validation": {"status": "local-only", "automatic_triggers_enabled": False},
        "advisories": ["github local-only"],
        "next_command": "python -B .agents/manage.py commit-readiness",
    }

    compact = repo_qol.summarize_finish_work_report(report)

    assert_summary(compact, check_count=2, failed_check_count=1)
    assert_summary(compact, budget_hotspot_status="measured", budget_hotspot_count=1, budget_delta_total_text_words=12)
    assert_field(compact["failed_checks"][0], "command", "check-b")
    assert_lacks_all(compact, "checks")
    assert_has_all(repo_qol.render_finish_work(report), "Budget hotspots: measured")


def test_finish_markdown_exposes_blocked_claim(tmp):
    report = {
        "status": "blocked",
        "ok": False,
        "checks": [],
        "completion_supported": False,
        "missing_evidence": ["review-coverage"],
        "claim_receipt": {"status": "blocked"},
        "next_command": "python -B .agents/manage.py review-loop",
    }

    rendered = repo_qol.render_finish_work(report)

    assert_has_all(
        rendered,
        "Status: blocked",
        "Completion supported: False",
        "Claim receipt: blocked",
        "Missing evidence: review-coverage",
    )
    assert_lacks_all(rendered, "query-safe=no")


def test_finish_summary_exposes_navigation_status(tmp):
    base_report = {
        "schema_version": 1,
        "tool": "repo-finish",
        "ok": True,
        "status": "passed",
        "checks": [],
        "workflow_run_indexes": {"checked_count": 0, "workflows": []},
        "workflow_eval": {"status": "skipped", "ok": True},
        "workflow_evidence_references": {"status": "skipped", "ok": True},
        "story_bug_out_of_scope_templates": {"status": "skipped", "ok": True},
        "budget_hotspots": {"status": "skipped", "ok": True},
        "budget_gate": {"status": "skipped", "ok": True},
        "check_metrics": {},
        "progress_events": [],
        "github_validation": {"status": "local-only", "automatic_triggers_enabled": False, "automatic_triggers": []},
        "advisories": [],
        "next_command": "python -B .agents/manage.py commit-readiness",
    }
    navigation = {
        "status": "stale",
        "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
        "next_command": "python -B .agents/manage.py setup",
        "stale_output_count": 2,
        "summary": "Navigation maps are stale; refresh before broad source reads.",
    }
    navigation_auto_refresh = {
        "schema_version": 1,
        "tool": "repo-navigation.auto-refresh",
        "ok": True,
        "status": "refreshed",
        "written": ["automations/navigation/artifacts/maps/NAVIGATION.md"],
        "summary": "Navigation maps were refreshed safely.",
    }

    with patched_attrs(
        repo_qol_finish,
        finish_work_report=lambda *args, **kwargs: dict(base_report),
    ), patched_attrs(
        repo_qol,
        navigation_status=lambda root: dict(navigation),
        auto_refresh_navigation=lambda root: dict(navigation_auto_refresh),
    ), patched_attrs(
        repo_changed,
        changed_files=lambda root: [],
    ):
        report = repo_qol.finish_work_report(tmp, deep=False, skip_benchmark=True)

    compact = repo_qol.summarize_finish_work_report(report, compact=True)

    assert report["navigation"] == navigation
    assert_field(compact["summary"], "navigation_status", "stale")
    assert_field(compact["summary"], "navigation_auto_refresh_status", "refreshed")
    assert_field(compact["navigation"], "read_first", "automations/navigation/artifacts/maps/HANDOFF.md")
    assert_field(compact["navigation_auto_refresh"], "status", "refreshed")
    assert_has_all(repo_qol.render_finish_work(report), "Navigation: stale")
    assert_has_all(repo_qol.render_finish_work(report), "Navigation auto-refresh: refreshed")


def test_finish_fails_when_navigation_auto_refresh_fails(tmp):
    base_report = {
        "schema_version": 1,
        "tool": "repo-finish",
        "ok": True,
        "status": "passed",
        "checks": [],
        "workflow_run_indexes": {"checked_count": 0, "workflows": []},
        "workflow_eval": {"status": "skipped", "ok": True},
        "workflow_evidence_references": {"status": "skipped", "ok": True},
        "story_bug_out_of_scope_templates": {"status": "skipped", "ok": True},
        "budget_hotspots": {"status": "skipped", "ok": True},
        "budget_gate": {"status": "skipped", "ok": True},
        "check_metrics": {},
        "progress_events": [],
        "github_validation": {"status": "local-only", "automatic_triggers_enabled": False, "automatic_triggers": []},
        "advisories": [],
        "next_command": "python -B .agents/manage.py commit-readiness",
    }

    with patched_attrs(
        repo_qol_finish,
        finish_work_report=lambda *args, **kwargs: dict(base_report),
    ), patched_attrs(
        repo_qol,
        navigation_status=lambda root: {
            "status": "blocked",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "python -B .agents/skills/repo-navigation/scripts/repo_navigation.py check --target . --format json",
            "stale_output_count": 0,
            "summary": "blocked",
        },
        auto_refresh_navigation=lambda root: {
            "schema_version": 1,
            "tool": "repo-navigation.auto-refresh",
            "ok": False,
            "status": "blocked",
            "written": [],
            "summary": "Navigation refresh blocked.",
            "next_command": "python -B .agents/skills/repo-navigation/scripts/repo_navigation.py check --target . --format json",
        },
    ), patched_attrs(
        repo_changed,
        changed_files=lambda root: [],
    ):
        report = repo_qol.finish_work_report(tmp, deep=False, skip_benchmark=True)

    compact = repo_qol.summarize_finish_work_report(report, compact=True)

    assert_not_ok(report)
    assert_field(report, "status", "blocked")
    assert_field(report["finish_readiness"], "status", "blocked")
    assert_field(compact["navigation_auto_refresh"], "status", "blocked")
    assert_field(compact["finish_readiness"], "status", "blocked")
    assert_has_all(report["next_command"], "repo_navigation.py check")


def test_finish_readiness_recommends_owner_packets_when_review_over_budget(tmp):
    base_report = {
        "schema_version": 1,
        "tool": "repo-finish",
        "profile": "deep",
        "ok": True,
        "status": "passed",
        "checks": [{"phase": "changed-scope", "ok": True}],
        "workflow_run_indexes": {"checked_count": 0, "workflows": []},
        "workflow_eval": {"status": "skipped", "ok": True},
        "workflow_evidence_references": {"status": "skipped", "ok": True},
        "story_bug_out_of_scope_templates": {"status": "skipped", "ok": True},
        "budget_hotspots": {"status": "skipped", "ok": True},
        "budget_gate": {"status": "skipped", "ok": True},
        "check_metrics": {},
        "progress_events": [],
        "github_validation": {"status": "local-only", "automatic_triggers_enabled": False, "automatic_triggers": []},
        "advisories": [],
        "next_command": "python -B .agents/manage.py commit-readiness",
    }
    review_packet = {
        "status": "over-budget",
        "review_budget_tokens": 5000,
        "changed_diff_estimated_tokens": 9000,
        "tokens_over_review_budget": 4000,
        "owner_review_packet_count": 1,
        "owner_review_commands": [
            "python -B .agents/manage.py review-packet --owner skill:skill-manager --summary --compact --format json"
        ],
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "estimated_changed_tokens": 3000,
                "next_command": "python -B .agents/manage.py review-packet --owner skill:skill-manager --summary --compact --format json",
            }
        ],
        "cost_ledger": {
            "schema_version": 1,
            "tool": "skill-manager.review-cost-ledger",
            "status": "measured",
            "billing_scope": "input-context-estimate-only",
            "billing_boundary": "Excludes output tokens, reasoning tokens, hidden prompts, cache discounts, provider prices, and rework.",
            "review_budget_tokens": 5000,
            "raw_changed_diff_estimated_tokens": 9000,
            "review_budget_exceeded": True,
            "tokens_over_review_budget": 4000,
            "owner_packet_count": 1,
            "first_owner_packet_estimated_tokens": 3000,
            "largest_owner_packet_estimated_tokens": 3000,
            "owner_packets_estimated_tokens_total": 3000,
            "single_agent_saved_tokens_vs_raw_estimated": 6000,
            "single_agent_saved_percent_vs_raw_estimated": 66.67,
            "release_gate": "needs-owner-review",
        },
    }

    with patched_attrs(
        repo_qol_finish,
        finish_work_report=lambda *args, **kwargs: dict(base_report),
    ), patched_attrs(
        repo_qol,
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
        auto_refresh_navigation=lambda root: {"ok": True, "status": "skipped-fresh"},
        input_fingerprint_report=lambda root, changed, validation_plan: {"status": "ok"},
    ), patched_attrs(
        repo_changed,
        changed_files=lambda root: [".agents/skills/skill-manager/SKILL.md"],
        changed_scope=lambda paths: {"skill_names": {"skill-manager"}, "workflows": False},
        large_diff_review_packet=lambda root, paths, validation_plan, navigation: dict(review_packet),
    ), patched_attrs(
        repo_qol.repo_optimizations,
        changed_validation_plan=lambda root, changed, scope, deep=False: [
            {"command": "python -B .agents/manage.py check-changed", "required": True}
        ],
    ):
        report = repo_qol.finish_work_report(tmp, deep=True, skip_benchmark=True)

    compact = repo_qol.summarize_finish_work_report(report, compact=True)

    assert_not_ok(report)
    assert_field(report, "status", "blocked")
    assert_false(report, "completion_supported")
    assert_field(report["finish_readiness"], "status", "needs-owner-review")
    assert_has_all(report["finish_readiness"]["next_command"], "review-loop", "--max-units")
    assert_has_all(report["next_command"], "review-loop", "--max-units")
    assert_field(report["finish_readiness"]["review_coverage"], "pending_review_unit_count", 1)
    assert_summary(compact, finish_readiness_status="needs-owner-review", owner_review_packet_count=1)
    assert_field(compact["review_packet"], "owner_review_packet_count", 1)
    assert_field(compact["review_packet"]["review_plan_summary"], "status", "needs-review")
    assert_has_all(
        compact["review_packet"]["review_plan_summary"]["next_pending_command"],
        "review-packet --owner skill:skill-manager",
    )
    assert_field(compact["review_packet"]["review_cost_report"], "billing_scope", "input-context-estimate-only")
    assert_field(report["finish_readiness"]["cost_ledger"], "single_agent_saved_tokens_vs_raw_estimated", 6000)
    assert_field(compact["review_packet"]["cost_ledger"], "raw_changed_diff_estimated_tokens", 9000)
    assert_has_all(
        repo_qol.render_finish_work(report),
        "Finish readiness: needs-owner-review",
        "Review packet: over-budget",
        "Cost ledger: largest owner packet",
    )


def test_finish_readiness_accepts_over_budget_diff_after_review_coverage_complete(tmp):
    base_report = {
        "schema_version": 1,
        "tool": "repo-finish",
        "ok": True,
        "status": "passed",
        "checks": [],
        "workflow_run_indexes": {"checked_count": 0, "workflows": []},
        "workflow_eval": {"status": "skipped", "ok": True},
        "workflow_evidence_references": {"status": "skipped", "ok": True},
        "story_bug_out_of_scope_templates": {"status": "skipped", "ok": True},
        "budget_hotspots": {"status": "skipped", "ok": True},
        "budget_gate": {"status": "skipped", "ok": True},
        "check_metrics": {},
        "progress_events": [],
        "github_validation": {"status": "local-only", "automatic_triggers_enabled": False, "automatic_triggers": []},
        "advisories": [],
        "next_command": "python -B .agents/manage.py commit-readiness",
    }
    review_packet = {
        "status": "over-budget",
        "review_budget_tokens": 5000,
        "changed_diff_estimated_tokens": 9000,
        "tokens_over_review_budget": 4000,
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "estimated_changed_tokens": 3000,
                "owner_summary_command": "python -B .agents/manage.py review-packet --owner skill:skill-manager --summary --compact --format json",
            }
        ],
    }

    with patched_attrs(
        repo_qol_finish,
        finish_work_report=lambda *args, **kwargs: dict(base_report),
    ), patched_attrs(
        repo_qol,
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
        auto_refresh_navigation=lambda root: {"ok": True, "status": "skipped-fresh"},
        input_fingerprint_report=lambda root, changed, validation_plan: {"digest": "digest-a"},
    ), patched_attrs(
        repo_changed,
        changed_files=lambda root: [".agents/skills/skill-manager/SKILL.md"],
        changed_scope=lambda paths: {"skill_names": {"skill-manager"}, "workflows": False},
        large_diff_review_packet=lambda root, paths, validation_plan, navigation: dict(review_packet),
    ), patched_attrs(
        repo_qol.repo_optimizations,
        changed_validation_plan=lambda root, changed, scope, deep=False: [
            {"command": "python -B .agents/manage.py check-changed", "required": True}
        ],
    ), patched_attrs(
        repo_qol.repo_review_progress,
        review_progress_report=lambda *args, **kwargs: {
            "ok": True,
            "status": "complete",
            "review_state": "complete",
            "coverage": {
                "status": "complete",
                "owner_total": 1,
                "owners_complete": 1,
                "review_unit_count": 1,
                "completed_review_unit_count": 1,
                "pending_review_unit_count": 0,
            },
        },
    ):
        report = repo_qol.finish_work_report(tmp, deep=False, skip_benchmark=True)

    compact = repo_qol.summarize_finish_work_report(report, compact=True)

    assert_field(report["finish_readiness"], "status", "ready")
    assert_field(report["finish_readiness"]["review_coverage"], "pending_review_unit_count", 0)
    assert_has_all(report["finish_readiness"]["next_command"], "commit-readiness")
    assert_field(compact["review_progress"], "status", "complete")
    assert "review_packet" not in compact


def test_finish_compact_success_omits_completed_diagnostics(_tmp):
    report = {
        "schema_version": 1,
        "tool": "repo-finish",
        "ok": True,
        "status": "passed",
        "completion_supported": True,
        "checks": [{"command": "check-a", "ok": True, "status": 0}],
        "review_packet": {
            "status": "over-budget",
            "review_budget_tokens": 5000,
            "changed_diff_estimated_tokens": 9000,
            "tokens_over_review_budget": 4000,
        },
        "review_progress": {
            "status": "complete",
            "review_state": "complete",
            "stale": False,
            "coverage": {
                "status": "complete",
                "pending_review_unit_count": 0,
            },
        },
        "budget_hotspots": {
            "ok": True,
            "status": "measured",
            "top": [
                {"name": f"skill-{index}", "total_text_words": 1000 + index}
                for index in range(50)
            ],
        },
        "progress_events": [
            {"event": "completed", "command": f"check-{index}", "ok": True}
            for index in range(20)
        ],
        "next_command": "python -B .agents/manage.py commit-readiness",
    }

    compact = repo_qol.summarize_finish_work_report(report, compact=True)

    assert "review_packet" not in compact
    assert "progress_events" not in compact
    assert "budget_hotspots" not in compact
    assert_summary(compact, budget_hotspot_status="measured", budget_hotspot_count=50)
    assert_field(compact["output_budget"], "status", "within-budget")


def test_finish_fast_path_skips_expensive_checks_when_review_coverage_blocks(tmp):
    review_packet = {
        "status": "over-budget",
        "review_budget_tokens": 5000,
        "changed_diff_estimated_tokens": 9000,
        "tokens_over_review_budget": 4000,
        "owner_review_packets": [
            {
                "owner": "skill:skill-manager",
                "estimated_changed_tokens": 3000,
                "next_command": "python -B .agents/manage.py review-packet --owner skill:skill-manager --summary --compact --format json",
            }
        ],
        "cost_ledger": {
            "schema_version": 1,
            "tool": "skill-manager.review-cost-ledger",
            "status": "measured",
            "review_budget_tokens": 5000,
            "raw_changed_diff_estimated_tokens": 9000,
            "review_budget_exceeded": True,
            "tokens_over_review_budget": 4000,
            "owner_packet_count": 1,
            "release_gate": "needs-owner-review",
        },
    }

    def fail_finish_work_report(*args, **kwargs):
        raise AssertionError("inner finish checks should not run while review coverage blocks default finish")

    with patched_attrs(
        repo_qol_finish,
        finish_work_report=fail_finish_work_report,
    ), patched_attrs(
        repo_qol,
        navigation_status=lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
        auto_refresh_navigation=lambda root: {"ok": True, "status": "skipped-fresh"},
        input_fingerprint_report=lambda root, changed, validation_plan: {"status": "ok"},
    ), patched_attrs(
        repo_changed,
        changed_files=lambda root: [".agents/skills/skill-manager/SKILL.md"],
        changed_scope=lambda paths: {"skill_names": {"skill-manager"}, "workflows": False},
        large_diff_review_packet=lambda root, paths, validation_plan, navigation: dict(review_packet),
    ), patched_attrs(
        repo_qol.repo_optimizations,
        changed_validation_plan=lambda root, changed, scope, deep=False: [
            {"command": "python -B .agents/manage.py check-changed", "required": True}
        ],
    ):
        report = repo_qol.finish_work_report(tmp, deep=False, skip_benchmark=True)

    compact = repo_qol.summarize_finish_work_report(report, compact=True)

    assert_not_ok(report)
    assert_field(report, "status", "skipped-review-blocked")
    assert_false(report, "completion_supported")
    assert_has_all(
        report["missing_evidence"],
        "review-coverage",
        "changed-validation",
        "selected-finish-checks",
    )
    assert_field(report["claim_receipt"], "status", "blocked")
    assert_field(report["fast_path"], "status", "used")
    assert_field(report["finish_readiness"], "status", "needs-owner-review")
    assert_has_all(report["next_command"], "review-loop", "--max-units")
    assert_summary(compact, finish_readiness_status="needs-owner-review")
    assert_field(compact["fast_path"], "status", "used")
    assert_field(compact["output_budget"], "command", "finish")
    assert_field(compact["output_budget"], "status", "within-budget")
    assert "cost_ledger" not in compact["finish_readiness"]
    assert "review_progress" not in compact["finish_readiness"]
    assert "owner_review_commands" not in compact["review_packet"]
    assert "owner_summary_commands" not in compact["review_packet"]


def test_finish_claim_receipt_supports_completion_from_same_run(tmp):
    report = {
        "tool": "repo-finish",
        "profile": "deep",
        "selected_validation_profile": "deep",
        "selected_phase_ids": ["changed-scope", "workflow-hooks"],
        "ok": True,
        "status": "passed",
        "navigation": {"status": "fresh"},
        "checks": [
            {
                "phase": "changed-scope",
                "ok": True,
                "command": "python -B .agents/manage.py check-changed --deep",
                "command_argv": [
                    sys.executable, "-B", ".agents/manage.py", "check-changed", "--deep",
                    "--record-progress", "--summary", "--compact", "--format", "json",
                ],
            },
            {
                "phase": "workflow-hooks",
                "ok": True,
                "command_argv": [sys.executable, *repo_qol_readiness.EXPECTED_PHASE_ARGV_TAILS["workflow-hooks"]],
            },
        ],
        "finish_readiness": {
            "ok": True,
            "status": "ready",
            "review_coverage": {
                "status": "complete",
                "pending_review_unit_count": 0,
            },
            "next_command": "python -B .agents/manage.py commit-readiness",
        },
        "review_packet": {"status": "within-budget"},
        "next_command": "python -B .agents/manage.py commit-readiness",
    }

    claim = repo_qol_readiness.finish_claim_report(report, deep=True)
    compact = repo_qol_readiness.summarize_claim_receipt(
        claim["claim_receipt"],
        compact=True,
    )

    assert_ok(claim)
    assert_true(claim, "completion_supported")
    assert_field(claim, "status", "supported")
    assert_empty(claim["missing_evidence"])
    assert_field(claim["claim_receipt"]["summary"], "required_missing_count", 0)
    by_id = {item["id"]: item for item in claim["claim_receipt"]["items"]}
    assert_field(by_id["navigation"], "status", "passed")
    assert_field(by_id["review-coverage"], "status", "passed")
    assert_field(by_id["changed-validation"], "status", "passed")
    assert_field(by_id["selected-finish-checks"], "status", "passed")
    assert_field(by_id["external-ci"], "status", "not-proven")
    assert_field(compact["items"][0], "id", "navigation")


def test_finish_claim_receipt_fails_closed_for_missing_contract_inputs(tmp):
    report = {
        "ok": True,
        "status": "passed",
        "navigation": {"status": "fresh"},
        "checks": [{"phase": "changed-scope", "ok": True}],
    }

    claim = repo_qol_readiness.finish_claim_report(report)

    assert_not_ok(claim)
    assert_false(claim, "completion_supported")
    assert_has_all(claim["missing_evidence"], "review-coverage", "selected-finish-checks")


def test_finish_claim_receipt_routes_stale_navigation_to_navigation_refresh(tmp):
    report = {
        "ok": False,
        "status": "blocked",
        "navigation": {
            "status": "stale",
            "next_command": "python -B navigation-refresh.py --write",
        },
        "checks": [],
        "finish_readiness": {
            "ok": False,
            "status": "blocked",
            "review_coverage": {},
            "next_command": "python -B .agents/manage.py commit-readiness",
        },
        "review_packet": {"status": "within-budget"},
    }

    claim = repo_qol_readiness.finish_claim_report(report)

    assert_not_ok(claim)
    assert_field(claim, "next_command", "python -B navigation-refresh.py --write")
    by_id = {item["id"]: item for item in claim["claim_receipt"]["items"]}
    assert_field(by_id["navigation"], "next_command", "python -B navigation-refresh.py --write")


def test_release_full_claim_requires_every_fixed_release_phase(tmp):
    required = {
        "workflow-hooks",
        "clean-context-proof",
        "install-harness-smoke-fast",
        "user-story-workflow-smoke",
        "workflow-evals",
        "repo-check",
        "changed-scope",
        "benchmark-doctor",
    }
    report = {
        "tool": "repo-finish",
        "profile": "release-full",
        "selected_validation_profile": "deep",
        "selected_phase_ids": sorted(required - {"benchmark-doctor"}),
        "ok": True,
        "status": "passed",
        "navigation": {"status": "fresh"},
        "checks": [
            {
                "phase": phase,
                "ok": True,
                "command": (
                    "python -B .agents/manage.py check-changed --deep"
                    if phase == "changed-scope"
                    else f"python gate.py {phase}"
                ),
                "command_argv": (
                    [
                        sys.executable, "-B", ".agents/manage.py", "check-changed", "--deep",
                        "--record-progress", "--summary", "--compact", "--format", "json",
                    ]
                    if phase == "changed-scope"
                    else [sys.executable, *repo_qol_readiness.EXPECTED_PHASE_ARGV_TAILS[phase]]
                ),
            }
            for phase in sorted(required - {"benchmark-doctor"})
        ],
        "review_packet": {"status": "within-budget"},
        "finish_readiness": {"ok": True, "status": "ready", "review_coverage": {}},
    }

    blocked = repo_qol_readiness.finish_claim_report(report, deep=True, release_full=True)
    report["checks"].append(
        {
            "phase": "benchmark-doctor",
            "ok": True,
            "command": "python benchmark.py",
            "command_argv": [sys.executable, *repo_qol_readiness.EXPECTED_PHASE_ARGV_TAILS["benchmark-doctor"]],
        }
    )
    report["checks"].extend(
        [
            {
                "phase": "workflow-run-index",
                "ok": True,
                "command": "python index.py workflow-a",
                "command_argv": [
                    sys.executable, "-B", ".agents/manage.py", "index-workflow-runs", "--name", "workflow-a",
                    "--check", "--format", "json",
                ],
            },
            {
                "phase": "workflow-run-index",
                "ok": True,
                "command": "python index.py workflow-b",
                "command_argv": [
                    sys.executable, "-B", ".agents/manage.py", "index-workflow-runs", "--name", "workflow-b",
                    "--check", "--format", "json",
                ],
            },
        ]
    )
    report["selected_phase_ids"] = [item["phase"] for item in report["checks"]]
    supported = repo_qol_readiness.finish_claim_report(report, deep=True, release_full=True)
    changed = next(item for item in report["checks"] if item["phase"] == "changed-scope")
    changed["command"] = "python -B .agents/manage.py check-changed"
    changed["command_argv"] = [sys.executable, "imposter-check-changed-command", "--deeply-invalid"]
    shallow_changed_scope = repo_qol_readiness.finish_claim_report(report, deep=True, release_full=True)

    assert_false(blocked, "completion_supported")
    assert_true(supported, "completion_supported")
    assert_false(shallow_changed_scope, "completion_supported")


def test_finish_parser_rejects_release_full_with_skip_benchmark(tmp):
    parser = repo_cli_parser.build_parser()
    try:
        parser.parse_args(["finish", "--release-full", "--skip-benchmark"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("release-full must not allow benchmark skipping")


def test_finish_projection_preserves_deep_and_budget_intent(tmp):
    report = repo_qol.finish_projection_report(tmp, deep=True, budget_intent="feature")
    release = repo_qol.finish_projection_report(tmp, release_full=True, budget_intent="feature")
    parser = repo_cli_parser.build_parser()
    parsed = parser.parse_args(["review-autopilot", "--release-full"])

    assert_has_all(report["next_command"], "finish --deep", "--budget-intent feature")
    assert_has_all(release["next_command"], "finish --release-full", "--budget-intent feature")
    assert parsed.release_full is True


def test_review_autopilot_propagates_release_full_to_completion_projection(tmp):
    calls = []

    def fake_completion(_root, *, deep=False, release_full=False, budget_intent="off"):
        calls.append((deep, release_full, budget_intent))
        return {
            "ok": True,
            "status": "needs-validation",
            "completion_supported": False,
            "gates": {"pending_review_unit_count": 0, "failed_check_count": 0},
            "next_command": "python -B .agents/manage.py finish --release-full",
        }

    report = repo_qol_context.review_autopilot_report(
        tmp,
        release_full=True,
        budget_intent="feature",
        completion_factory=fake_completion,
    )

    assert calls == [(True, True, "feature")]
    assert_true(report, "release_full")
    assert_has_all(report["next_command"], "finish --release-full")


def test_finish_claim_receipt_names_missing_evidence_without_rerunning_checks(tmp):
    report = {
        "ok": False,
        "status": "skipped-review-blocked",
        "navigation": {"status": "fresh"},
        "checks": [],
        "review_packet": {"status": "over-budget"},
        "finish_readiness": {
            "ok": False,
            "status": "needs-owner-review",
            "review_coverage": {
                "status": "not-started",
                "pending_review_unit_count": 2,
            },
            "next_command": repo_review_progress.default_review_loop_command(),
        },
        "next_command": repo_review_progress.default_review_loop_command(),
    }

    claim = repo_qol_readiness.finish_claim_report(report)

    assert_not_ok(claim)
    assert_false(claim, "completion_supported")
    assert_field(claim, "status", "blocked")
    assert_has_all(
        claim["missing_evidence"],
        "review-coverage",
        "changed-validation",
        "selected-finish-checks",
    )
    by_id = {item["id"]: item for item in claim["claim_receipt"]["items"]}
    assert_field(by_id["review-coverage"], "status", "missing")
    assert_field(by_id["changed-validation"], "status", "failed")
    assert_has_all(by_id["review-coverage"]["next_command"], "review-loop", "--max-units")
    assert_has_all(claim["claim_receipt"]["boundary"], "does not rerun validation", "push", "merge")


def test_validation_progress_requires_exact_required_set_and_complete_pass_coverage(tmp):
    fingerprint = {"digest": "digest-a"}
    current = {
        "command": "check-changed",
        "phase": "complete",
        "status": "passed",
        "extra": {
            "failed_check_count": 0,
            "input_fingerprint_digest": "digest-a",
            "profile": "changed",
            "required_check_ids": ["syntax", "owner"],
            "passed_check_ids": ["syntax", "owner"],
        },
    }

    assert repo_command_metrics.validation_progress_covers_input(
        current,
        fingerprint,
        required_check_ids=["syntax", "owner"],
    )
    incomplete = json.loads(json.dumps(current))
    incomplete["extra"]["passed_check_ids"] = ["syntax"]
    assert not repo_command_metrics.validation_progress_covers_input(
        incomplete,
        fingerprint,
        required_check_ids=["syntax", "owner"],
    )
    stale_plan = json.loads(json.dumps(current))
    stale_plan["extra"]["required_check_ids"] = ["syntax"]
    assert not repo_command_metrics.validation_progress_covers_input(
        stale_plan,
        fingerprint,
        required_check_ids=["syntax", "owner"],
    )
    wrong_profile = json.loads(json.dumps(current))
    wrong_profile["extra"]["profile"] = "bogus"
    assert not repo_qol_dashboard._validation_progress_matches_current_input(
        wrong_profile,
        fingerprint,
        ["syntax", "owner"],
    )
    assert not repo_changed_summary._validation_progress_matches_current_input(
        wrong_profile,
        fingerprint,
        ["syntax", "owner"],
        profile="changed",
    )


def test_repo_health_reports_structured_script_hotspots(tmp):
    oversized = "\n".join(f"def function_{index}():\n    return {index}" for index in range(41))
    write_text(skill_path(skill_root(tmp, "demo"), "scripts", "large.py"), oversized)

    hotspots = repo_health.script_complexity_hotspots(tmp)

    assert hotspots == [
        {
            "bytes": len((oversized + "\n").encode("utf-8")),
            "lines": 82,
            "path": ".agents/skills/demo/scripts/large.py",
            "public_command_file": True,
            "reasons": ["top-level-functions"],
            "top_level_functions": 41,
        }
    ]
    warnings = repo_health.script_complexity_warnings(tmp)
    assert_contains(warnings, "41 top-level functions")


def test_repo_health_skips_generated_navigation_workflow_script_copies(tmp):
    oversized = "\n".join(f"def function_{index}():\n    return {index}" for index in range(41))
    write_text(tmp / ".agents" / "skills" / "repo-navigation" / "scripts" / "navigation" / "navigation_core.py", oversized)
    write_text(tmp / "automations" / "navigation" / "scripts" / "navigation_core.py", oversized)

    hotspots = repo_health.script_complexity_hotspots(tmp)

    assert [item["path"] for item in hotspots] == [
        ".agents/skills/repo-navigation/scripts/navigation/navigation_core.py"
    ]


def test_repo_health_deduplicates_public_command_line_warnings(tmp):
    write_text(skill_path(skill_root(tmp, "demo"), "scripts", "huge.py"), "print('ok')\n" * 1201)

    warnings = repo_health.script_complexity_warnings(tmp)

    huge_warnings = [warning for warning in warnings if "huge.py" in warning]
    assert len(huge_warnings) == 1
    assert_contains(huge_warnings, "public command file")


def test_repo_health_warns_about_excess_top_level_script_files(tmp):
    scripts = skill_path(skill_root(tmp, "benchmark-demo"), "scripts")
    for index in range(18):
        write_text(scripts / f"script_{index}.py", "print('ok')\n")

    warnings = repo_health.script_complexity_warnings(tmp)

    assert_contains(warnings, "has 18 top-level scripts")


def test_repo_health_simplicity_scan_skips_transient_missing_markdown(tmp):
    transient = tmp / "automations" / "user-story-workflow" / "runs" / "run-a" / "validation" / "smoke-local-quality.md"

    with patched_attrs(
        repo_health_surface,
        active_markdown_files=lambda root: [transient],
        instruction_adapter_files=lambda root: [],
    ):
        warnings = repo_health_surface.simplicity_warnings(tmp)

    assert_lacks_all(warnings, "smoke-local-quality.md")


def test_repo_health_active_markdown_skips_run_evidence(tmp):
    write_text(tmp / "automations" / "demo" / "runs" / "run-a" / "plan.md", ("word " * 2000).strip())

    warnings = repo_health_surface.simplicity_warnings(tmp)

    assert_lacks(warnings, "automations/demo/runs/run-a/plan.md")


def test_repo_health_warns_about_hidden_characters_on_accepted_surface(tmp):
    write_text(tmp / "AGENTS.md", "# Repo\u200b\n\nKeep instructions visible.")
    write_text(tmp / "automations" / "demo" / "runs" / "run-a" / "REPORT.md", "# Report\u200b\n")

    warnings = repo_health_surface.simplicity_warnings(tmp)

    assert_contains_all(warnings, "AGENTS.md", "zero-width space", "line 1")
    assert_lacks(warnings, "automations/demo/runs/run-a/REPORT.md")


def test_repo_qol_router_stays_below_script_line_hotspot(_tmp):
    root = Path(__file__).resolve().parents[4]
    hotspots = repo_health.script_complexity_hotspots(root)

    repo_qol_hotspots = [
        item
        for item in hotspots
        if item.get("path") == ".agents/skills/skill-manager/scripts/repo_support/repo_qol.py"
    ]

    assert_empty(repo_qol_hotspots)


def test_repo_qol_context_stays_below_script_complexity_hotspots(_tmp):
    root = Path(__file__).resolve().parents[4]
    hotspots = repo_health.script_complexity_hotspots(root)

    context_hotspots = [
        item
        for item in hotspots
        if item.get("path") == ".agents/skills/skill-manager/scripts/repo_support/repo_qol_context.py"
    ]

    assert_empty(context_hotspots)


def test_repo_review_progress_stays_below_script_complexity_hotspots(_tmp):
    root = Path(__file__).resolve().parents[4]
    hotspots = repo_health.script_complexity_hotspots(root)

    progress_hotspots = [
        item
        for item in hotspots
        if item.get("path") == ".agents/skills/skill-manager/scripts/repo_support/repo_review_progress.py"
    ]

    assert_empty(progress_hotspots)


def test_repo_health_mermaid_gate_formats_validator_errors(tmp):
    script = (
        tmp
        / ".agents"
        / "skills"
        / "mermaid-diagrams-azure-devops"
        / "scripts"
        / "validate_mermaid.py"
    )
    write_text(
        script,
        """
import json
from pathlib import Path

root = Path(__file__).resolve().parents[4]
print(json.dumps({
    "files_scanned": ["docs/diagram.md"],
    "block_count": 2,
    "artifact_count": 1,
    "errors": [
        {
            "path": str(root / "docs" / "diagram.md"),
            "line": 4,
            "message": "Linked Mermaid SVG must use an intrinsic numeric width.",
        }
    ],
    "warnings": [],
}))
raise SystemExit(1)
""",
    )

    report = repo_health.mermaid_diagram_health(tmp)

    assert_not_ok(report)
    assert_field(report, "files_scanned", 1)
    assert_field(report, "block_count", 2)
    assert_field(report, "artifact_count", 1)
    assert_contains(report["errors"], "docs/diagram.md:4")
    assert_contains(report["errors"], "intrinsic numeric width")


def test_repo_health_summary_keeps_counts_and_failures(tmp):
    report = {
        "schema_version": 1,
        "tool": "check-repo-health",
        "ok": False,
        "status": "issues-found",
        "python": "3.12.0",
        "skills": ["demo"],
        "workflows": ["flow"],
        "generated_checks": [
            {"name": "routing", "ok": False, "message": "stale"},
            {"name": "instructions", "ok": True, "message": "ok"},
        ],
        "repository_surface": {
            "layout": ["bad layout"],
            "candidate_imports": [],
            "bytecode": [],
            "script_type": [],
            "self_contained_managers": [],
            "instruction_quality": [],
            "folder_organization": [{"folder": "validation"}],
            "script_complexity_hotspots": [{"path": "large.py"}],
            "mermaid_diagrams": [],
            "mermaid_diagram_summary": {
                "files_scanned": 12,
                "block_count": 3,
                "artifact_count": 9,
                "warning_count": 1,
            },
        },
        "warnings": ["trim docs"],
        "next_recommended_command": "python -B .agents/manage.py sync",
    }

    compact = repo_health.summarize_repo_health_report(report)

    assert_summary(
        compact,
        skill_count=1,
        workflow_count=1,
        issue_count=1,
        failed_generated_count=1,
        mermaid_files_scanned=12,
        mermaid_block_count=3,
        mermaid_artifact_count=9,
        mermaid_warning_count=1,
    )
    assert_name(compact["generated_failures"][0], "routing")


def test_commands_summary_removes_parser_help(tmp):
    parser = repo_cli_parser.build_parser()
    rows = repo_commands.command_index(parser)
    report = {
        "schema_version": 1,
        "tool": "repo-command-discovery",
        "ok": True,
        "status": "ok",
        "groups": sorted({str(item["group"]) for item in rows}),
        "common_paths": [{"name": "daily", "commands": ["python -B .agents/manage.py status"]}],
        "commands": rows,
    }

    compact = repo_commands.summarize_command_report(report)

    assert_summary(compact, command_count=len(rows))
    assert_keys_lack(compact["commands"][0], "usage", "help")
    assert isinstance(compact["commands"][0], dict)
    groups_only = repo_commands.summarize_command_report(report, compact=True)
    assert_summary(groups_only, command_count=len(rows))
    assert_keys_lack(groups_only, "commands", "groups")
    assert groups_only["group_names"]
    assert_keys_lack(groups_only, "common_path_names")


def test_evidence_verify_checks_raw_output_digest_for_json_and_text(tmp):
    raw_rel = ".agents/local-ai/cache/command-output/raw.txt"
    raw_text = "ERROR: broken fixture\n"
    write_text(tmp / raw_rel, raw_text)
    digest = repo_qol_evidence.digest_text(raw_text)
    write_json(
        tmp / "evidence" / "compact.json",
        {"command_result": {"raw_output_path": raw_rel, "output_summary": {"digest": digest}}},
    )
    write_text(
        tmp / ".agents/local-ai/cache/last-validation.txt",
        f"Raw output: {raw_rel}\nOutput: {len(raw_text)} bytes, 1 lines, digest {digest}\n",
    )

    json_report = repo_qol_evidence.evidence_verify_report(tmp, files=["evidence/compact.json"])
    text_report = repo_qol_evidence.evidence_verify_report(tmp)
    compact = repo_qol_evidence.summarize_evidence_verify_report(json_report, compact=True)

    assert_ok(json_report)
    assert_ok(text_report)
    assert_summary(json_report, reference_count=1, missing_count=0, digest_mismatch_count=0)
    assert_true(json_report["references"][0], "digest_ok")
    assert_keys_lack(compact, "references", "sources", "next_command")


def test_evidence_verify_default_passes_when_last_validation_is_absent(tmp):
    report = repo_qol_evidence.evidence_verify_report(tmp)

    assert_ok(report)
    assert_field(report, "status", "no-evidence")
    assert_summary(report, source_count=0, reference_count=0, issue_count=0)


def test_evidence_verify_rejects_missing_or_mismatched_raw_output(tmp):
    raw_rel = ".agents/local-ai/cache/command-output/raw.txt"
    write_text(tmp / raw_rel, "ERROR: current\n")
    write_json(
        tmp / "evidence" / "bad.json",
        {
            "items": [
                {"raw_output_path": raw_rel, "output_summary": {"digest": "0000000000000000"}},
                {"raw_output_path": ".agents/local-ai/cache/command-output/missing.txt"},
            ]
        },
    )

    report = repo_qol_evidence.evidence_verify_report(tmp, files=["evidence/bad.json"])

    assert_not_ok(report)
    assert_summary(report, reference_count=2, missing_count=1, digest_mismatch_count=1)
    assert_contains_each([row.get("issue", "") for row in report["references"]], "digest mismatch", "missing")


def test_daily_commands_include_startup_context(tmp):
    parser = repo_cli_parser.build_parser()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = repo_commands.print_commands(parser, "json", shortcut="daily", summary=True)

    assert result == 0
    report = json.loads(output.getvalue())
    command_names = {item.get("canonical", item.get("name")) for item in report["commands"]}
    assert_has_all(
        command_names,
        "startup-context",
        "next-action",
        "context-cost-benchmark",
        "context-use-check",
        "review-loop",
        "review-autopilot",
        "change-ledger",
        "evidence-verify",
        "review-packet",
        "handoff-packet",
        "portable-constraints",
    )
    command_groups = {item.get("name"): item.get("group") for item in repo_commands.command_index(parser)}
    assert_field(command_groups, "review-packet", "Daily")
    assert_field(command_groups, "handoff-packet", "Daily")
    assert_field(command_groups, "portable-constraints", "Daily")
    assert_field(command_groups, "context-cost-benchmark", "Daily")
    assert_field(command_groups, "context-use-check", "Daily")
    assert_field(command_groups, "review-loop", "Daily")
    assert_field(command_groups, "review-autopilot", "Daily")
    assert_field(command_groups, "finish", "Readiness")
    assert_field(command_groups, "change-ledger", "Daily")


def test_daily_common_path_ends_in_one_compact_finish_command(tmp):
    parser = repo_cli_parser.build_parser()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = repo_commands.print_commands(parser, "markdown")

    assert result == 0
    assert_has_all(
        output.getvalue(),
        "python -B .agents/manage.py status --fast --summary --compact --format json",
        "python -B .agents/manage.py next-action --summary --compact --format json",
        "python -B .agents/manage.py review-autopilot --max-cycles 3 --max-units-per-cycle 20 --max-total-units 60 --max-estimated-tokens 24000 --max-elapsed-ms 540000 --summary --compact --format json",
        "python -B .agents/manage.py finish --summary --compact --format json",
    )
    assert_lacks_all(
        output.getvalue(),
        "merge-readiness",
        "can-i-finish",
        "ready-packet",
        "completion-packet",
    )


def test_workflow_common_path_includes_checkpoint_command(tmp):
    parser = repo_cli_parser.build_parser()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = repo_commands.print_commands(parser, "markdown", shortcut="workflow")

    assert result == 0
    rendered = output.getvalue()
    assert_has_all(rendered, "python -B .agents/manage.py workflow checkpoint --name <workflow-name> --run-id <run-id> --write")


def test_commands_markdown_write_keeps_doc_frontmatter(tmp):
    markdown = repo_commands.add_doc_frontmatter("# Repository Commands\n")

    assert markdown.startswith("---\n")
    assert_has_all(markdown, "title: Repository Commands", "\n# Repository Commands\n")


def test_commands_markdown_documents_no_python_path(tmp):
    parser = repo_cli_parser.build_parser()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = repo_commands.print_commands(parser, "markdown")

    assert result == 0
    rendered = output.getvalue()
    assert_has_all(rendered, "Python runtime missing", "docs/harness/no-python.md", "AGENTS_PYTHON")


def test_analyze_location_summary_is_compact(tmp):
    write_skill(tmp)
    skill_dir = skill_root(tmp)
    report = analyze_location.analyze_target(
        str(skill_dir),
        skill_dir,
        max_files=2500,
        max_text_files=400,
    )
    compact = analyze_location.summarize_report(report, compact=True)

    assert_field(compact, "format", "skill-manager.analysis-summary")
    assert compact["files_scanned"] >= 2
    assert_keys_lack(compact, "review_packet", "evidence", "static_audit", "location", "recommended_review_decision")
    assert compact["recommended_review_decision_count"] >= 1
    markdown = analyze_location.render_summary_markdown(compact)
    assert_has_all(markdown, "# Location Analysis Summary", "Files scanned")


def test_attest_skill_summary_is_compact(tmp):
    write_skill(tmp)
    report = attest_skill.build_attestation(skill_root(tmp), tmp)
    compact = attest_skill.summarize_report(report, compact=True)

    assert_field(compact, "format", "skill-attestation-summary")
    assert_name(compact["skill"], "demo-skill")
    assert compact["file_count"] >= 2
    assert_keys_lack(compact, "file_hashes", "manifest")
    markdown = attest_skill.render_markdown(compact)
    assert_has_all(markdown, "Files hashed")
    assert_lacks_all(markdown, "## File Hashes")


def test_resume_and_evidence_summaries_are_compact(tmp):
    resume = {
        "schema_version": 1,
        "tool": "repo-resume-work",
        "ok": True,
        "mode": "full",
        "branch": "feature/demo",
        "dirty_state": {"dirty": False, "status": "clean"},
        "changed_files": ["a.py", "b.py"],
        "changed_groups": "python",
        "evidence": {"workflow_runs": [{"run_id": "run-a"}], "benchmarks": [{"run": "bench-a"}]},
        "next_command": REPO_CHECK_COMMAND,
    }
    compact_resume = repo_qol.summarize_resume_work_report(resume, compact=True)

    assert_fields(compact_resume, changed_file_count=2, dirty_status="clean")
    assert_fields(compact_resume["evidence"], workflow_run_count=1, latest_workflow_run="")
    assert_keys_lack(compact_resume, "dirty_state", "changed_files")

    evidence = {
        "schema_version": 1,
        "tool": "repo-evidence-index",
        "ok": True,
        "latest_validation": ".agents/local-ai/cache/last-validation.txt",
        "workflow_runs": [{"workflow": "story", "run_id": "run-a"}],
        "benchmarks": [{"run": "bench-a"}],
        "document_evidence": [{"path": "doc.json"}],
        "local_ai_reports": [{"path": "local.json"}],
        "next_command": "python -B .agents/manage.py resume-work",
    }
    compact_evidence = repo_qol_evidence.summarize_evidence_report(evidence, compact=True)

    assert_summary(compact_evidence, workflow_run_count=1, benchmark_count=1)
    assert_field(compact_evidence, "latest", {
        "document_evidence": "doc.json",
        "local_ai_report": "local.json",
    })
    assert_keys_lack(compact_evidence, "next_command", "workflow_runs", "local_ai_reports")


def write_capability_fixture(root):
    write_files(
        root,
        {
            "README.md": SKILLS_HARNESS_MD,
            "AGENTS.md": REPO_INSTRUCTIONS_MD,
            "docs/operations/daily-use.md": "#\n",
            ".agents/routing.md": "#\n",
            "automations/routing.md": "#\n",
            "docs/workflow/workflows.md": "Workflow hooks\n",
            ".agents/skills/workflow-manager/scripts/workflow_run_support.py": "execute_workflow_hooks\n",
            ".agents/skills/workflow-manager/scripts/run_self_tests.py": "run-started\nphase-started\nphase-completed\n",
        },
    )
    for name in ("story-flow", "bug-flow"):
        module = {
            "kind": "workflow",
            "phases": [{"id": "intake"}, {"id": "validation"}],
        }
        write_json(automation_path(root, name, "module.json"), module)
        write_text(automation_path(root, name, "WORKFLOW.md"), f"# {name}\n")
        run_dir = write_workflow_run(root, name, "current-setup", external_validation_status="passed")
        write_text(run_dir / "REPORT.md", "# Report\n")


def healthy_dashboard_fixture():
    return {
        "mode": "fast",
        "total_elapsed_ms": 2400,
        "changed_file_count": 0,
        "generated_checks": [{"ok": True}],
        "context_budget": {
            "estimated_low_context_tokens": 3200,
            "low_context_files": [{"path": ".agents/routing.md"}, {"path": "automations/routing.md"}],
        },
        "local_ai": {
            "ok": True,
            "enabled_use_cases": ["skill-routing", "workflow-routing", "validation-triage"],
        },
        "benchmark": {"ok": True},
        "evidence": {
            "benchmarks": [{}],
            "workflow_runs": [{}],
        },
    }


def test_capability_audit_proves_all_goal_requirements_when_evidence_is_complete(tmp):
    write_capability_fixture(tmp)
    report = repo_capability_audit.build_capability_audit(tmp, healthy_dashboard_fixture())
    statuses = {item["id"]: item["status"] for item in report["requirements"]}

    assert_true(report, "completion_supported")
    assert_fields(
        statuses,
        fast_daily_path="proved",
        workflow_hooks="proved",
        current_validation_evidence="proved",
    )
    assert_lacks_all(statuses, "token_saving_local_ai")


def test_capability_audit_blocks_completion_without_current_benchmark_evidence(tmp):
    write_capability_fixture(tmp)
    dashboard = healthy_dashboard_fixture()
    dashboard["evidence"] = {"benchmarks": [], "workflow_runs": []}
    report = repo_capability_audit.build_capability_audit(tmp, dashboard)
    benchmark = next(item for item in report["requirements"] if item["id"] == "current_validation_evidence")

    assert_false(report, "completion_supported")
    assert_status(benchmark, "missing")
    assert_contains(benchmark["risks"], "benchmark-result.json")


def test_capability_audit_does_not_require_local_ai_for_completion(tmp):
    write_capability_fixture(tmp)
    write_json(
        local_ai_policy(tmp),
        {
            "use_cases": {
                "skill-routing": {"enabled": True},
                "workflow-routing": {"enabled": True},
                "validation-triage": {"enabled": True},
            }
        },
    )
    dashboard = healthy_dashboard_fixture()
    dashboard["local_ai"] = {"ok": False, "status": "skipped", "enabled_use_cases": None}
    report = repo_capability_audit.build_capability_audit(tmp, dashboard)
    requirement_ids = [item["id"] for item in report["requirements"]]

    assert_true(report, "completion_supported")
    assert_lacks_all(requirement_ids, "token_saving_local_ai")


def test_tracked_policy_disables_automatic_validation_triage(_tmp):
    root = Path(__file__).resolve().parents[4]
    local_policy = json.loads((root / ".agents/local-ai/policy.json").read_text(encoding="utf-8"))
    project_policy = json.loads((root / repo_policy.PROJECT_POLICY_PATH).read_text(encoding="utf-8"))
    validation_route = project_policy["cost_policy"]["routing"]["tasks"]["validation"]

    assert_false(local_policy["use_cases"]["validation-triage"], "enabled")
    assert_field(validation_route, "prefer", "deterministic")
    assert_lacks_all(validation_route["local_ai_use_cases"], "validation-triage")
    assert_lacks_all(
        project_policy["cost_policy"]["local_ai"]["warm_batch"]["prefer_for_tasks"],
        "validation-triage",
    )
    assert_field(project_policy["commands"]["latency_ms"], "context-use-check", 40000)
    assert_field(repo_policy.DEFAULT_LATENCY_BUDGETS_MS, "context-use-check", 40000)


def test_capability_audit_blocks_completion_when_worktree_has_changed_files(tmp):
    write_capability_fixture(tmp)
    dashboard = healthy_dashboard_fixture()
    dashboard["changed_file_count"] = 2
    report = repo_capability_audit.build_capability_audit(tmp, dashboard)
    deterministic = next(item for item in report["requirements"] if item["id"] == "deterministic_validation")

    assert_false(report, "completion_supported")
    assert_status(deterministic, "partial")
    assert_has_all(" ".join(deterministic["risks"]), "changed files")


def test_agents_instructions_pin_structural_search_policy(_tmp):
    root = Path(__file__).resolve().parents[4]
    text = (root / "AGENTS.md").read_text(encoding="utf-8")

    assert_has_all(
        text,
        "Automatic search policy",
        "optional ast-grep silently",
        "never load raw ast-grep JSON",
        "docs/reference/tools-and-search.md",
    )


def test_agents_instructions_pin_workflow_self_discovery(_tmp):
    root = Path(__file__).resolve().parents[4]
    text = (root / "AGENTS.md").read_text(encoding="utf-8")

    assert_has_all(
        text,
        "Workflow self-discovery",
        'workflow start --from-request "<request>" --summary --compact --format json',
        'which-workflow "<request>" --summary --compact --format json',
        "read-only/no-start",
        "Workflow lifecycle owns retrieval/evidence",
        "don't ask users to run internal local-AI commands",
        "automations/*/runs/<run-id>/run.json",
        "workflow context-audit --name <workflow-name> --run-id <run-id> --summary --compact --format json",
        "status --no-local-ai --summary --compact --format json",
        "changed-evidence --summary --compact --format json",
    )


def test_agents_instructions_pin_navigation_handoff(_tmp):
    root = Path(__file__).resolve().parents[4]
    text = (root / "AGENTS.md").read_text(encoding="utf-8")

    assert_has_all(
        text,
        "Route first",
        "automations/navigation/artifacts/maps/HANDOFF.md",
    )
    assert len(text) <= repo_policy.int_value(root, "limits.agents.warn_chars")


def test_startup_context_discovers_navigation_handoff_from_clean_context(tmp):
    root = Path(__file__).resolve().parents[4]
    write_text(tmp / "AGENTS.md", (root / "AGENTS.md").read_text(encoding="utf-8"))
    write_text(tmp / "automations" / "navigation" / "artifacts" / "maps" / "HANDOFF.md", "# Handoff\n")

    report = repo_qol_daily.startup_context_report(tmp, compact=True)

    assert_field(report["navigation"], "read_first", "automations/navigation/artifacts/maps/HANDOFF.md")
    assert_field(report["summary"], "navigation_status", "missing")


def test_command_docs_smoke_catches_plan_check_profile_flag(tmp):
    write_text(
        tmp / "AGENTS.md",
        "Run `python -B .agents/manage.py workflow plan-check --name disciplined-change-workflow --template --profile lean`.",
    )

    report = repo_prevention.command_docs_smoke_report(tmp)

    assert_not_ok(report)
    assert_field(report, "checked_command_count", 1)
    assert_contains(report["issues"], "unsupported flag")


def test_command_docs_smoke_accepts_template_resolve_profile(tmp):
    write_text(
        tmp / "AGENTS.md",
        "Run `python -B .agents/manage.py workflow template resolve --name disciplined-change-workflow --template plan.md --profile lean`.",
    )

    report = repo_prevention.command_docs_smoke_report(tmp)

    assert_ok(report)
    assert_empty(report["issues"])


def test_command_docs_smoke_parses_compact_check_additions_example(tmp):
    write_text(
        tmp / "AGENTS.md",
        "Run `python -B .agents/manage.py check-additions --summary --compact --format json`.",
    )

    report = repo_prevention.command_docs_smoke_report(tmp)

    assert_ok(report)
    assert_field(report, "parse_checked_count", 1)
    assert_field(report, "parse_skipped_count", 0)
    assert_empty(report["issues"])


def test_command_docs_smoke_catches_unknown_documented_flags(tmp):
    write_text(
        tmp / "AGENTS.md",
        "Run `python -B .agents/manage.py check-additions --not-a-real-flag`.",
    )

    report = repo_prevention.command_docs_smoke_report(tmp)

    assert_not_ok(report)
    assert_field(report, "parse_checked_count", 1)
    assert_contains(report["issues"], "does not parse")


def test_command_docs_smoke_skips_workflow_run_evidence_commands(tmp):
    write_text(
        tmp / "automations" / "demo" / "runs" / "run-a" / "REPORT.md",
        "Historical command: `python -B .agents/manage.py validate-automations --name demo`.",
    )

    report = repo_prevention.command_docs_smoke_report(tmp)

    assert_ok(report)
    assert_field(report, "checked_command_count", 1)
    assert_field(report, "parse_checked_count", 0)
    assert_empty(report["issues"])


def test_command_docs_smoke_catches_raw_navigation_json_context(tmp):
    write_text(
        tmp / "AGENTS.md",
        "Read automations/navigation/artifacts/maps/project-map.json before planning implementation.",
    )

    report = repo_prevention.command_docs_smoke_report(tmp)

    assert_not_ok(report)
    assert_contains(report["issues"], "raw generated navigation JSON")
    assert_contains(report["issues"], "HANDOFF.md")


def test_command_docs_smoke_allows_tool_only_raw_navigation_json_reference(tmp):
    write_text(
        tmp / "AGENTS.md",
        "automations/navigation/artifacts/maps/staleness.json is a tool-only freshness index; do not load it into context.",
    )

    report = repo_prevention.command_docs_smoke_report(tmp)

    assert_ok(report)
    assert_empty(report["issues"])


def test_clean_room_validate_uses_isolated_d_drive_evidence_and_local_fallback(tmp):
    source = tmp / "source"
    source.mkdir()
    work_dir = tmp / "D-drive-clean-room"
    calls = []

    def fake_runner(command, **kwargs):
        command = [str(item) for item in command]
        calls.append(command)
        stdout = ""
        returncode = 0
        tail = command[1:]
        if tail[:3] == ["config", "--get", "remote.origin.url"]:
            stdout = "https://example.invalid/skills.git\n"
        elif "clone" in command:
            target = Path(command[-1])
            if "https://example.invalid/skills.git" in command:
                returncode = 1
                stdout = "remote unavailable\n"
            else:
                target.mkdir(parents=True, exist_ok=True)
                stdout = "cloned local\n"
        elif tail[:2] == ["rev-parse", "HEAD"]:
            stdout = "abc123\n"
        elif tail[:2] == ["branch", "--show-current"]:
            stdout = "main\n"
        elif tail[:3] == ["status", "--short", "--branch"]:
            stdout = "## main...origin/main\n"
        elif ".agents/manage.py" in command:
            stdout = "{}\n"
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")

    report = repo_prevention.clean_room_validate_report(
        source,
        work_dir=work_dir,
        source="auto",
        keep=True,
        quick=True,
        runner=fake_runner,
    )

    assert_ok(report)
    assert_field(report, "clone_kind", "local")
    assert_exists(work_dir / "evidence" / "clone-state.json")
    assert_exists(work_dir / "evidence" / "command-results.json")
    state = read_json(work_dir / "evidence" / "clone-state.json")
    assert_has_all(state["environment"]["npm_config_cache"], "npm-cache")
    assert_contains(calls, "https://example.invalid/skills.git")


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


def external_self_tests():
    tests_dir = Path(__file__).resolve().parent / "self_tests"
    if not tests_dir.exists():
        return []
    tests = []
    for path in sorted(tests_dir.glob("test_*.py")):
        module_name = f"_skill_manager_self_tests_{path.stem}"
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


def internal_self_tests():
    tests = [
        value
        for name, value in vars(sys.modules[__name__]).items()
        if name.startswith("test_") and callable(value)
    ]
    return sorted(tests, key=lambda test: test.__code__.co_firstlineno)


def main():
    parser = argparse.ArgumentParser(description="write/temp: run skill-manager self-tests using temporary fixture projects")
    parser.add_argument("--match", action="append", default=[], help="run tests whose function name contains this text")
    args = parser.parse_args()
    tests = internal_self_tests()
    tests.extend(external_self_tests())
    selected = filter_tests(tests, args.match)
    if args.match and not selected:
        print(f"no self-tests matched: {', '.join(args.match)}", file=sys.stderr)
        return 2
    for test in selected:
        run_test(test.__name__, test)
    if args.match:
        print(f"skill-manager focused self-tests passed ({len(selected)}/{len(tests)}).")
    else:
        print("skill-manager self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
