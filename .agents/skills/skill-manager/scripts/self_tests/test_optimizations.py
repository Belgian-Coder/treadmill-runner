"""Focused self-tests for skill-manager optimization helpers."""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import subprocess
import sys
import time
from argparse import Namespace
from pathlib import Path

import measure_skill_budget
from repo_support import repo_benchmark
from repo_support import repo_changed
from repo_support import repo_cli_parser
from repo_support import repo_command_metrics
from repo_support import repo_commands
from repo_support import repo_optimizations
from repo_support import repo_qol
from repo_support import repo_qol_dashboard
from repo_support import repo_qol_daily
from repo_support import repo_qol_finish
from repo_support import repo_qol_finish_packets
from repo_support import repo_qol_readiness
from repo_support import repo_setup


DEMO_DESCRIPTION = "Use when validating skill evals."


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def module_contract(name: str = "demo-skill") -> dict[str, object]:
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
                "id": "demo-review",
                "argv": [
                    "python",
                    "-B",
                    ".agents/manage.py",
                    "review",
                    "--skill",
                    f".agents/skills/{name}",
                ],
                "working_directory": "repository",
                "effects": [],
                "timeout_seconds": 300,
            }
        ],
        "strict_read_only_commands": [],
        "extensions": {},
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
        "quality": {"eval_suites": ["suites/demo-evals.json"]},
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


def write_skill(root: Path, name: str = "demo-skill") -> Path:
    skill_dir = root / ".agents" / "skills" / name
    write_text(
        skill_dir / "SKILL.md",
        f"""---
name: {name}
description: {DEMO_DESCRIPTION}
---

# Demo Skill

## Goal

Provide a small current-contract fixture.

## Workflow

Validate.

## Rules

Stay inside the fixture.

## Validation

Run the declared eval suite.

## Completion Contract

Report.

## Stop Rules

Stop.
""",
    )
    write_json(skill_dir / "module.json", module_contract(name))
    write_text(skill_dir / "scripts" / "run_self_tests.py", "print('ok')")
    write_json(
        skill_dir / "suites" / "demo-evals.json",
        {
            "evals": [
                {
                    "id": "current-contract",
                    "assertions": [
                        {"type": "validation_ok"},
                        {"type": "risk_profile_covers_flags"},
                        {"type": "trigger_quality"},
                    ],
                }
            ]
        },
    )
    return skill_dir


def test_measure_skill_budget_summary_is_compact(tmp: Path) -> None:
    write_skill(tmp)
    report = measure_skill_budget.build_report(Namespace(root=str(tmp), all=True, skill=None, summary=True, compact=False))
    assert "summary" in report
    assert report["summary"]["skill_count"] == 1
    assert list(report["skills"][0]) == [
        "name",
        "skill_md_words",
        "skill_md_status",
        "routing_load_words",
        "route_activation_words",
        "route_activation_tokens",
        "route_activation_complete",
        "guidance_load_words",
        "tool_load_words",
        "total_text_words",
        "largest_file",
        "largest_file_words",
    ]
    compact = measure_skill_budget.build_report(Namespace(root=str(tmp), all=True, skill=None, summary=True, compact=True))
    assert compact["summary"]["skill_count"] == 1
    assert "root" not in compact
    assert "skills" not in compact
    assert compact["top"][0]["name"] == "demo-skill"
    assert "warnings" not in compact
    assert compact["top_by_load_class"]["routing"][0]["name"] == "demo-skill"
    assert compact["top_by_load_class"]["tool"][0]["load_words"] >= 1


def test_measure_skill_budget_reports_drilldown_suggestions(tmp: Path) -> None:
    skill_dir = write_skill(tmp)
    write_text(skill_dir / "docs" / "long-guide.md", "# Long Guide\n\n" + ("word " * 80))
    write_text(skill_dir / "scripts" / "large_tool.py", "print('x')\n" * 80)

    report = measure_skill_budget.measure_skill(skill_dir, tmp)

    assert report["budget_drilldown"]["largest_files"][0]["path"]
    assert report["budget_drilldown"]["by_load_class"]["guidance"]["words"] >= 80
    assert any(item["action"] for item in report["optimization_suggestions"])


def test_measure_skill_budget_writes_budget_trend_snapshot(tmp: Path) -> None:
    skill_dir = write_skill(tmp)
    report = measure_skill_budget.measure_skill(skill_dir, tmp)
    written = measure_skill_budget.write_budget_trend(skill_dir, report, today=dt.date(2026, 6, 2))

    history_path = skill_dir / "docs" / "context-budget-history.json"
    data = json.loads(history_path.read_text(encoding="utf-8"))
    row = data["history"][0]

    assert written["path"] == "docs/context-budget-history.json"
    assert row["date"] == "2026-06-02"
    assert row["skill_md_words"] == report["skill_md"]["words"]
    assert row["largest_file"]


def test_measure_skill_budget_compares_git_baseline(tmp: Path) -> None:
    skill_dir = write_skill(tmp)
    subprocess.run(["git", "init"], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "baseline"],
        cwd=tmp,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    write_text(skill_dir / "docs" / "delta.md", "# Delta\n\nextra words for the changed tree")

    report = measure_skill_budget.build_report(
        Namespace(root=str(tmp), all=True, skill=None, summary=True, compact=True, write_trend=False, baseline_ref="HEAD")
    )

    assert report["baseline"]["ref"] == "HEAD"
    assert report["baseline"]["ok"] is True
    assert report["delta"]["summary"]["total_text_words"] > 0
    assert report["delta"]["skills"][0]["name"] == "demo-skill"
    assert "Delta vs `HEAD`" in measure_skill_budget.render_summary_markdown(report)


def test_measure_skill_budget_reports_tool_hotspots(tmp: Path) -> None:
    skill_dir = write_skill(tmp)
    write_text(skill_dir / "scripts" / "setup_impl.py", "def f():\n    pass\n" + ("word " * 10050))
    report = measure_skill_budget.measure_skill(skill_dir, tmp)

    assert report["tool_hotspots"][0]["path"] == "scripts/setup_impl.py"
    assert "setup catalog" in report["tool_hotspots"][0]["action"]


def write_route_activation_fixture(tmp: Path) -> Path:
    skill_dir = write_skill(tmp)
    write_text(
        tmp / ".agents" / "routing.md",
        """# Skill Routing Index

| Category | Skill | Use When | Open |
|---|---|---|---|
| Testing | `demo-skill` | Use when validating skill evals. | `.agents/skills/demo-skill/SKILL.md` |
""",
    )
    write_text(skill_dir / "docs" / "required.md", "# Required\n\nOpen this directly for the routed task.")
    manifest = json.loads((skill_dir / "module.json").read_text(encoding="utf-8"))
    manifest["extensions"] = {
        "skills-harness/token-budget": {
            "direct_guidance": ["docs/required.md"],
        }
    }
    write_json(skill_dir / "module.json", manifest)
    return skill_dir


def test_measure_skill_budget_reports_exact_route_activation_bundle(tmp: Path) -> None:
    skill_dir = write_route_activation_fixture(tmp)
    write_text(skill_dir / "scripts" / "large_tool.py", "tool source words\n" * 200)

    first = measure_skill_budget.measure_skill(skill_dir, tmp)
    second = measure_skill_budget.measure_skill(skill_dir, tmp)
    activation = first["route_activation"]

    assert activation["complete"] is True
    assert [row["type"] for row in activation["components"]] == [
        "routing-entry",
        "skill-instructions",
        "direct-guidance",
    ]
    assert [row["path"] for row in activation["components"]] == [
        ".agents/routing.md#skill:demo-skill",
        ".agents/skills/demo-skill/SKILL.md",
        ".agents/skills/demo-skill/docs/required.md",
    ]
    assert all("large_tool.py" not in row["path"] for row in activation["components"])
    assert activation["bundle_sha256"] == second["route_activation"]["bundle_sha256"]
    assert activation["tokens_estimated"] > 0
    assert first["maintainability_inventory"]["tool_words"] > activation["words"]


def test_measure_skill_budget_marks_missing_and_unsafe_activation_guidance_incomplete(tmp: Path) -> None:
    skill_dir = write_route_activation_fixture(tmp)
    (tmp / ".agents" / "routing.md").unlink()
    manifest = json.loads((skill_dir / "module.json").read_text(encoding="utf-8"))
    manifest["extensions"]["skills-harness/token-budget"]["direct_guidance"] = [
        "docs/missing.md",
        "../escape.md",
    ]
    write_json(skill_dir / "module.json", manifest)

    activation = measure_skill_budget.measure_skill(skill_dir, tmp)["route_activation"]

    assert activation["complete"] is False
    assert ".agents/routing.md#skill:demo-skill" in activation["missing"]
    assert ".agents/skills/demo-skill/docs/missing.md" in activation["missing"]
    assert any("unsafe direct guidance" in issue for issue in activation["issues"])


def test_measure_skill_budget_marks_malformed_activation_guidance_incomplete(tmp: Path) -> None:
    malformed_values = (
        "docs/required.md",
        {"path": "docs/required.md"},
        ["docs/required.md", 17, {"path": "docs/other.md"}],
    )
    for index, malformed in enumerate(malformed_values):
        case_root = tmp / f"case-{index}"
        skill_dir = write_route_activation_fixture(case_root)
        manifest = json.loads((skill_dir / "module.json").read_text(encoding="utf-8"))
        manifest["extensions"]["skills-harness/token-budget"]["direct_guidance"] = malformed
        write_json(skill_dir / "module.json", manifest)

        activation = measure_skill_budget.measure_skill(skill_dir, case_root)["route_activation"]

        assert activation["complete"] is False
        assert any("direct_guidance" in issue for issue in activation["issues"])
        if isinstance(malformed, list):
            assert any(
                row["path"].endswith("docs/required.md")
                for row in activation["components"]
            )


def test_measure_skill_budget_marks_unreadable_manifest_activation_incomplete(tmp: Path) -> None:
    skill_dir = write_route_activation_fixture(tmp)
    write_text(skill_dir / "module.json", "{ not valid json\n")

    activation = measure_skill_budget.measure_skill(skill_dir, tmp)["route_activation"]

    assert activation["complete"] is False
    assert any("module manifest unavailable" in issue for issue in activation["issues"])


def test_measure_skill_budget_summary_separates_activation_from_inventory(tmp: Path) -> None:
    write_route_activation_fixture(tmp)

    report = measure_skill_budget.build_report(
        Namespace(root=str(tmp), all=True, skill=None, summary=True, compact=False)
    )
    row = report["skills"][0]

    assert row["route_activation_tokens"] > 0
    assert row["route_activation_words"] > 0
    assert row["route_activation_complete"] is True
    assert row["tool_load_words"] >= 1


def test_check_changed_includes_ordered_validation_plan_and_compact_output(tmp: Path) -> None:
    write_skill(tmp)
    write_text(
        tmp / ".agents" / "skills" / "skill-manager" / "scripts" / "repo_support" / "repo_changed.py",
        "print('current')\n",
    )
    paths = [
        ".agents/skills/skill-manager/SKILL.md",
        ".agents/skills/skill-manager/scripts/repo_support/repo_changed.py",
        ".agents/skills/skill-manager/scripts/removed.py",
        "docs/start-here.md",
    ]
    scope = repo_changed.changed_scope(paths)

    plan = repo_optimizations.changed_validation_plan(tmp, paths, scope, deep=False)
    summary = repo_optimizations.compact_command_output("line one\n" + ("x" * 2000))

    assert [item["order"] for item in plan] == sorted(item["order"] for item in plan)
    syntax_commands = [item["command"] for item in plan if "syntax-check --paths" in item["command"]]
    assert syntax_commands
    assert all("removed.py" not in command for command in syntax_commands)
    assert any("validate_skill.py" in item["command"] for item in plan)
    assert any("check-changed --deep" in item["command"] for item in plan)
    assert scope["docs"] == ["docs/start-here.md"]
    assert scope["python_paths"] == [
        ".agents/skills/skill-manager/scripts/repo_support/repo_changed.py",
        ".agents/skills/skill-manager/scripts/removed.py",
    ]
    assert not scope["other"]
    assert summary["truncated"] is True
    assert summary["line_count"] == 2

    deep_plan = repo_optimizations.changed_validation_plan(tmp, paths, scope, deep=True)
    assert any("--match" in item["command"] for item in deep_plan), deep_plan


def test_compact_command_output_preserves_failure_tail(tmp: Path) -> None:
    output = "\n".join(
        ["command started"]
        + [f"PASS test_{index}" for index in range(30)]
        + ["Traceback (most recent call last):", "AssertionError: final failure"]
    )

    summary = repo_optimizations.compact_command_output(
        output,
        max_chars=240,
        max_lines=8,
        root=tmp,
    )

    assert summary["truncated"] is True
    assert "command started" in summary["summary"]
    assert "lines omitted" in summary["summary"]
    assert "Traceback (most recent call last):" in summary["summary"]
    assert "AssertionError: final failure" in summary["summary"]
    assert len(summary["summary"]) <= 240

    tiny_summary = repo_optimizations.compact_command_output(
        output,
        max_chars=10,
        max_lines=8,
        root=tmp,
    )
    assert tiny_summary["summary"] == "al failure"
    assert len(tiny_summary["summary"]) == 10


def test_status_compact_summary_omits_validation_command_array(_tmp: Path) -> None:
    report = {
        "schema_version": 1,
        "tool": "repo-dashboard",
        "ok": True,
        "status": "ok",
        "dirty_state": {"ok": True, "status": "clean", "dirty": False},
        "context_budget": {"status": "ok", "estimated_low_context_tokens": 100, "changed_file_count": 2},
        "navigation": {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
        "validation_router": {
            "status": "planned",
            "summary": {"command_count": 2, "required_count": 2, "optional_count": 0},
            "commands": [
                {"command": "python -B .agents/manage.py check-additions", "owner": "skill-manager"},
                {"command": "python -B .agents/manage.py check", "owner": "skill-manager"},
            ],
            "next_command": "python -B .agents/manage.py check-additions",
        },
        "checks": [],
        "generated_checks": [],
    }

    compact = repo_qol.summarize_dashboard_report(report, compact=True)
    expanded = repo_qol.summarize_dashboard_report(report, compact=False)

    assert "commands" not in compact["validation_router"]
    assert compact["validation_router"]["next_command"] == "python -B .agents/manage.py check-additions"
    assert compact["validation_router"]["summary"]["command_count"] == 2
    assert expanded["validation_router"]["commands"]


def test_startup_context_summary_includes_compact_context_trace(tmp: Path) -> None:
    write_text(tmp / "AGENTS.md", "# Repo\n")
    old_navigation = repo_qol_daily.navigation_status

    try:
        repo_qol_daily.navigation_status = lambda root, fast=False: {
            "status": "fresh",
            "reason": "fresh-navigation-staleness-cache",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "Navigation maps are fresh; read HANDOFF.md for source orientation.",
        }
        report = repo_qol_daily.startup_context_report(tmp, compact=True)
        compact = repo_qol_daily.summarize_startup_context_report(report, compact=True)
    finally:
        repo_qol_daily.navigation_status = old_navigation

    for packet in (report, compact):
        trace = packet["context_trace"]
        assert trace["read_first"] == "automations/navigation/artifacts/maps/HANDOFF.md"
        assert "automations/navigation/artifacts/maps/HANDOFF.md" in trace["read_now"]
        assert "automations/navigation/artifacts/maps/handoff.json" in trace["skip_raw_json"]
        assert "automations/navigation/artifacts/maps/staleness.json" in trace["skip_raw_json"]
        assert trace["next_command"] == "none, navigation maps are fresh"
        assert trace["reason"] == "fresh-navigation-staleness-cache"


def test_status_compact_summary_includes_compact_context_trace(_tmp: Path) -> None:
    report = {
        "schema_version": 1,
        "tool": "repo-dashboard",
        "ok": True,
        "status": "ok",
        "mode": "fast",
        "dirty_state": {"ok": True, "status": "clean", "dirty": False},
        "context_budget": {"status": "ok", "estimated_low_context_tokens": 100, "changed_file_count": 0},
        "navigation": {
            "status": "fresh",
            "reason": "fresh-navigation-staleness-cache",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        },
        "validation_router": {
            "status": "no-changes",
            "summary": {"command_count": 0, "required_count": 0, "optional_count": 0},
            "commands": [],
            "next_command": "none, no changed files",
        },
        "checks": [],
        "generated_checks": [],
    }

    compact = repo_qol.summarize_dashboard_report(report, compact=True)

    trace = compact["context_trace"]
    assert trace["read_first"] == "automations/navigation/artifacts/maps/HANDOFF.md"
    assert trace["read_now"] == ["AGENTS.md", "automations/navigation/artifacts/maps/HANDOFF.md"]
    assert "automations/navigation/artifacts/maps/handoff.json" in trace["skip_raw_json"]
    assert "automations/navigation/artifacts/maps/staleness.json" in trace["skip_raw_json"]
    assert trace["next_command"] == "none, navigation maps are fresh"


def test_status_no_local_ai_reports_skipped_advisory(tmp: Path) -> None:
    old_health = repo_qol.repo_health.build_repo_health_report
    old_dirty = repo_qol.repo_doctor.git_dirty_state
    old_changed_files = repo_qol.repo_changed.changed_files
    old_navigation = repo_qol.navigation_status
    old_github = repo_qol.github_validation_trigger_state

    try:
        repo_qol.repo_health.build_repo_health_report = lambda root: {"ok": True, "generated_checks": []}
        repo_qol.repo_doctor.git_dirty_state = lambda root: {"ok": True, "status": "clean", "dirty": False}
        repo_qol.repo_changed.changed_files = lambda root: []
        repo_qol.navigation_status = lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        }
        repo_qol.github_validation_trigger_state = lambda root: {"status": "local-only", "automatic_triggers": []}
        report = repo_qol.dashboard_report(tmp, skip_local_ai=True, skip_github=True)
        compact = repo_qol.summarize_dashboard_report(report, compact=True)
    finally:
        repo_qol.repo_health.build_repo_health_report = old_health
        repo_qol.repo_doctor.git_dirty_state = old_dirty
        repo_qol.repo_changed.changed_files = old_changed_files
        repo_qol.navigation_status = old_navigation
        repo_qol.github_validation_trigger_state = old_github

    assert report["status"] == "ok", report
    assert compact["status"] == "ok", compact
    assert compact["local_ai"]["status"] == "skipped"
    assert all(check["name"] != "local_ai_readiness" for check in compact.get("checks", []))


def test_check_changed_summary_compact_omits_full_outputs(tmp: Path) -> None:
    old_changed_files = repo_changed.changed_files
    old_acceptance = repo_changed.addition_acceptance_report
    old_navigation = repo_changed.navigation_status
    old_sync_instructions = repo_changed.generated.sync_instructions
    old_planned_command = repo_changed.run_planned_command_check

    def fake_acceptance(root: Path, *, paths=None, new_paths=None):
        return {
            "schema_version": 1,
            "tool": "skill-manager.addition-acceptance",
            "ok": True,
            "status": "passed",
            "summary": {"issue_count": 0},
            "issues": [],
        }

    try:
        repo_changed.changed_files = lambda root: ["AGENTS.md"]
        repo_changed.addition_acceptance_report = fake_acceptance
        repo_changed.generated.sync_instructions = lambda root, check=True: 0
        repo_changed.run_planned_command_check = lambda root, command: (command, True, "ok", 1)
        repo_changed.navigation_status = lambda root: {
            "status": "fresh",
            "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
            "next_command": "none, navigation maps are fresh",
            "stale_output_count": 0,
            "summary": "fresh",
        }
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = repo_changed.check_changed(
                Namespace(format="json", deep=False, verbose=False, summary=True, compact=True),
                tmp,
            )
    finally:
        repo_changed.changed_files = old_changed_files
        repo_changed.addition_acceptance_report = old_acceptance
        repo_changed.navigation_status = old_navigation
        repo_changed.generated.sync_instructions = old_sync_instructions
        repo_changed.run_planned_command_check = old_planned_command

    payload = json.loads(stdout.getvalue())

    assert status == 0, payload
    assert payload["changed_file_count"] == 1
    assert payload["navigation"]["read_first"] == "automations/navigation/artifacts/maps/HANDOFF.md"
    assert payload["context_trace"]["read_first"] == "automations/navigation/artifacts/maps/HANDOFF.md"
    assert payload["context_trace"]["read_now"] == ["AGENTS.md", "automations/navigation/artifacts/maps/HANDOFF.md"]
    assert "automations/navigation/artifacts/maps/handoff.json" in payload["context_trace"]["skip_raw_json"]
    assert "automations/navigation/artifacts/maps/staleness.json" in payload["context_trace"]["skip_raw_json"]
    assert payload["proof_hygiene"]["status"] == "passed"
    assert payload["addition_acceptance"]["status"] == "passed"
    assert payload["input_fingerprint"]["algorithm"] == "sha256"
    assert payload["input_fingerprint"]["changed_file_count"] == 1
    assert payload["input_fingerprint"]["command_count"] >= 1
    assert payload["input_fingerprint"]["stale_if"]
    assert payload["validation_plan_summary"]["command_count"] >= 1
    assert payload["next_command"]
    assert "changed_files" not in payload
    assert "checks" not in payload


def test_planned_command_timeout_fails_closed(tmp: Path) -> None:
    old_run = repo_changed.repo_qol_capture.run_process_output

    def timeout(*_args, **_kwargs):
        return 124, "partial\nCOMMAND TIMEOUT: command timed out after 180 seconds.\n", True

    try:
        repo_changed.repo_qol_capture.run_process_output = timeout
        name, ok, output, elapsed_ms = repo_changed.run_planned_command_check(tmp, "python slow.py")
    finally:
        repo_changed.repo_qol_capture.run_process_output = old_run

    assert name == "python slow.py"
    assert ok is False
    assert "timed out after 180 seconds" in output
    assert elapsed_ms >= 0


def test_check_changed_summary_keeps_compact_review_cost_ledger(_tmp: Path) -> None:
    payload = repo_changed.summarize_check_changed_payload(
        {
            "status": "passed",
            "changed_files": ["AGENTS.md"],
            "checks": [],
            "skipped": [],
            "docs": [],
            "unclassified": [],
            "navigation": {},
            "proof_hygiene": {"status": "passed", "summary": {"finding_count": 0, "skipped_count": 0}},
            "addition_acceptance": {"status": "passed", "summary": {"issue_count": 0}},
            "validation_plan_summary": {"command_count": 1},
            "timing_summary": {},
            "review_packet": {
                "status": "over-budget",
                "review_budget_tokens": 5000,
                "changed_diff_estimated_tokens": 9000,
                "tokens_over_review_budget": 4000,
                "owner_review_packet_count": 1,
                "owner_review_packets": [{"owner": "repo", "read_first": [{"path": "AGENTS.md"}]}],
                "owner_review_commands": ["python -B .agents/manage.py review-packet --owner repo"],
                "read_first": [{"path": "AGENTS.md"}],
                "validation_first": [{"command": "python -B .agents/manage.py check"}],
                "cost_ledger": {
                    "status": "measured",
                    "billing_scope": "input-context-estimate-only",
                    "comparison_scope": "all-owner-packets",
                    "raw_changed_diff_estimated_tokens": 9000,
                    "review_budget_tokens": 5000,
                    "review_budget_exceeded": True,
                    "release_gate": "needs-owner-review",
                    "owner_packet_count": 1,
                    "largest_owner_packet_estimated_tokens": 3000,
                    "review_unit_count": 2,
                    "review_units_estimated_tokens_total": 4800,
                    "single_agent_saved_tokens_vs_raw_estimated": 6000,
                    "single_agent_saved_percent_vs_raw_estimated": 66.67,
                    "all_review_units_delta_tokens_vs_raw_estimated": -4200,
                    "billing_boundary": "too verbose for compact summary",
                },
            },
        },
        compact=True,
    )

    ledger = payload["review_packet"]["cost_ledger"]
    assert ledger["largest_owner_packet_estimated_tokens"] == 3000
    assert ledger["review_unit_count"] == 2
    assert ledger["review_units_estimated_tokens_total"] == 4800
    assert ledger["all_review_units_delta_tokens_vs_raw_estimated"] == -4200
    assert ledger["single_agent_saved_tokens_vs_raw_estimated"] == 6000
    assert "billing_boundary" not in ledger
    assert "owner_review_packets" not in payload["review_packet"]
    assert "owner_review_commands" not in payload["review_packet"]
    assert "read_first" not in payload["review_packet"]
    assert "validation_first" not in payload["review_packet"]


def test_check_changed_summary_exposes_deep_next_command_when_deep_checks_skipped(_tmp: Path) -> None:
    payload = repo_changed.summarize_check_changed_payload(
        {
            "status": "passed",
            "changed_files": [".agents/skills/demo/SKILL.md"],
            "checks": [],
            "skipped": ["changed skill self-tests - pass --deep to run scripts/run_self_tests.py"],
            "docs": [],
            "unclassified": [],
            "navigation": {},
            "proof_hygiene": {"status": "passed", "summary": {"finding_count": 0, "skipped_count": 0}},
            "addition_acceptance": {"status": "passed", "summary": {"issue_count": 0}},
            "validation_plan_summary": {"command_count": 1},
            "timing_summary": {},
        },
        compact=True,
    )

    assert payload["deep_next_command"] == "python -B .agents/manage.py check-changed --deep --summary --compact --format json"


def test_changed_evidence_summary_compact_keeps_samples_without_full_router(tmp: Path) -> None:
    old_changed_files = repo_qol_daily.repo_changed.changed_files

    try:
        repo_qol_daily.repo_changed.changed_files = lambda root: [
            "AGENTS.md",
            "docs/start-here.md",
            ".agents/skills/skill-manager/scripts/repo_support/repo_qol.py",
            ".agents/skills/skill-manager/scripts/repo_support/repo_changed.py",
        ]
        report = repo_qol_daily.changed_evidence_report(tmp)
        compact = repo_qol_daily.summarize_changed_evidence_report(report, compact=True)
        expanded = repo_qol_daily.summarize_changed_evidence_report(report, compact=False)
    finally:
        repo_qol_daily.repo_changed.changed_files = old_changed_files

    assert compact["changed_file_count"] == 4
    assert compact["suggestions"]
    assert all("path_count" in item for item in compact["suggestions"])
    assert all(len(item.get("sample_paths", [])) <= 3 for item in compact["suggestions"])
    assert "commands" not in compact["validation_router"]
    assert compact["validation_router"]["summary"]["command_count"] >= 1
    assert compact["next_command"]
    assert "paths" not in compact["suggestions"][0]
    assert "commands" in expanded["validation_router"]


def test_check_changed_deep_uses_focused_skill_self_tests(tmp: Path) -> None:
    skill_dir = tmp / ".agents" / "skills" / "skill-manager"
    write_text(skill_dir / "SKILL.md", "# Skill Manager")
    write_json(skill_dir / "module.json", module_contract("skill-manager"))
    write_text(skill_dir / "scripts" / "run_self_tests.py", "print('ok')")
    write_text(skill_dir / "scripts" / "validate_skill.py", "print('ok')")
    write_text(skill_dir / "scripts" / "sync_skill_routing.py", "print('ok')")
    write_text(skill_dir / "scripts" / "repo_support" / "repo_optimizations.py", "print('ok')")
    calls: list[tuple[str, list[str]]] = []

    old_changed_files = repo_changed.changed_files
    old_acceptance = repo_changed.addition_acceptance_report
    old_run = repo_changed.repo.run_python_script_quiet
    old_claude = repo_changed.generated.sync_claude_skills
    old_module_schema = repo_changed.generated.sync_module_schema
    old_project_policy_schema = repo_changed.generated.sync_project_policy_schema

    def fake_acceptance(root: Path, *, paths=None, new_paths=None):
        return {
            "schema_version": 1,
            "tool": "skill-manager.addition-acceptance",
            "ok": True,
            "status": "passed",
            "summary": {"issue_count": 0},
            "issues": [],
        }

    def fake_run(script: Path, arguments: list[str]) -> tuple[int, str]:
        calls.append((script.name, list(arguments)))
        return 0, "ok"

    try:
        repo_changed.changed_files = lambda root: [".agents/skills/skill-manager/scripts/repo_support/repo_optimizations.py"]
        repo_changed.addition_acceptance_report = fake_acceptance
        repo_changed.repo.run_python_script_quiet = fake_run
        repo_changed.generated.sync_claude_skills = lambda root, check=True: 0
        repo_changed.generated.sync_module_schema = lambda root, check=True: 0
        repo_changed.generated.sync_project_policy_schema = lambda root, check=True: 0
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = repo_changed.check_changed(
                Namespace(format="json", deep=True, verbose=False, full=True, record_progress=True),
                tmp,
            )
    finally:
        repo_changed.changed_files = old_changed_files
        repo_changed.addition_acceptance_report = old_acceptance
        repo_changed.repo.run_python_script_quiet = old_run
        repo_changed.generated.sync_claude_skills = old_claude
        repo_changed.generated.sync_module_schema = old_module_schema
        repo_changed.generated.sync_project_policy_schema = old_project_policy_schema

    payload = json.loads(stdout.getvalue())
    self_test_calls = [arguments for name, arguments in calls if name == "run_self_tests.py"]

    assert status == 0, payload
    assert self_test_calls, calls
    assert "--match" in self_test_calls[0]
    assert "changed_validation_plan" in self_test_calls[0]
    assert "deep_self_test_commands_focus_slow_changed_skill_owners" in self_test_calls[0]
    assert "check_changed" not in self_test_calls[0]
    assert payload["checks"][0]["elapsed_ms"] >= 0
    assert payload["timing_summary"]["total_elapsed_ms"] >= 0
    assert payload["validation_progress"]["status"] == "passed"
    assert payload["validation_progress"]["phase"] == "complete"
    assert payload["validation_progress"]["path"].endswith(".agents/local-ai/cache/validation-progress.json")
    progress_path = Path(payload["validation_progress"]["path"])
    assert progress_path.is_file()
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress["command"] == "check-changed"
    assert progress["status"] == "passed"
    assert progress["recorded_at"]
    assert progress["extra"]["command_argv"][:3] == [
        sys.executable,
        "-B",
        ".agents/manage.py",
    ]
    assert progress["extra"]["post_input_fingerprint_digest"]
    assert progress["extra"]["post_input_fingerprint_digest"] == (
        progress["extra"]["input_fingerprint_digest"]
    )
    assert progress["extra"]["input_stable"] is True
    assert progress["extra"]["side_effect_boundary"] == (
        "repository-read-only-and-temporary-restored"
    )
    assert payload["post_validation_fingerprint"]["digest"] == (
        progress["extra"]["post_input_fingerprint_digest"]
    )


def test_check_changed_deep_runs_full_suite_for_changed_test_runner(tmp: Path) -> None:
    skill_dir = tmp / ".agents" / "skills" / "skill-manager"
    write_text(skill_dir / "SKILL.md", "# Skill Manager")
    write_json(skill_dir / "module.json", module_contract("skill-manager"))
    write_text(skill_dir / "scripts" / "run_self_tests.py", "print('ok')")
    write_text(skill_dir / "scripts" / "validate_skill.py", "print('ok')")
    write_text(skill_dir / "scripts" / "sync_skill_routing.py", "print('ok')")
    calls: list[tuple[str, list[str]]] = []

    old_changed_files = repo_changed.changed_files
    old_acceptance = repo_changed.addition_acceptance_report
    old_run = repo_changed.repo.run_python_script_quiet
    old_claude = repo_changed.generated.sync_claude_skills
    old_module_schema = repo_changed.generated.sync_module_schema
    old_project_policy_schema = repo_changed.generated.sync_project_policy_schema

    def fake_acceptance(root: Path, *, paths=None, new_paths=None):
        return {
            "schema_version": 1,
            "tool": "skill-manager.addition-acceptance",
            "ok": True,
            "status": "passed",
            "summary": {"issue_count": 0},
            "issues": [],
        }

    def fake_run(script: Path, arguments: list[str]) -> tuple[int, str]:
        calls.append((script.name, list(arguments)))
        return 0, "ok"

    try:
        repo_changed.changed_files = lambda root: [".agents/skills/skill-manager/scripts/run_self_tests.py"]
        repo_changed.addition_acceptance_report = fake_acceptance
        repo_changed.repo.run_python_script_quiet = fake_run
        repo_changed.generated.sync_claude_skills = lambda root, check=True: 0
        repo_changed.generated.sync_module_schema = lambda root, check=True: 0
        repo_changed.generated.sync_project_policy_schema = lambda root, check=True: 0
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = repo_changed.check_changed(Namespace(format="json", deep=True, verbose=False, full=True), tmp)
    finally:
        repo_changed.changed_files = old_changed_files
        repo_changed.addition_acceptance_report = old_acceptance
        repo_changed.repo.run_python_script_quiet = old_run
        repo_changed.generated.sync_claude_skills = old_claude
        repo_changed.generated.sync_module_schema = old_module_schema
        repo_changed.generated.sync_project_policy_schema = old_project_policy_schema

    payload = json.loads(stdout.getvalue())
    self_test_calls = [arguments for name, arguments in calls if name == "run_self_tests.py"]

    assert status == 0, payload
    assert self_test_calls == [[]], calls
    assert all("(focused)" not in check["name"] for check in payload["checks"])


def test_navigation_status_is_exposed_in_low_context_surfaces(tmp: Path) -> None:
    fake_navigation = {
        "status": "stale",
        "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
        "next_command": "python -B .agents/manage.py setup",
        "read_only_next_step": "Read AGENTS.md and automations/navigation/artifacts/maps/HANDOFF.md; refresh navigation only when writes are allowed.",
        "stale_output_count": 2,
        "summary": "Navigation maps are stale; refresh before broad source reads.",
    }
    write_text(tmp / "AGENTS.md", "# Repo")
    write_text(tmp / "README.md", "# Demo")

    old_daily = getattr(repo_qol_daily, "navigation_status", None)
    old_qol = getattr(repo_qol, "navigation_status", None)
    old_dashboard = getattr(repo_qol_dashboard, "navigation_status", None)
    old_setup = getattr(repo_setup, "navigation_status", None)
    old_changed = getattr(repo_changed, "navigation_status", None)
    old_changed_files = repo_changed.changed_files
    old_health = repo_qol.repo_health.build_repo_health_report
    old_dirty = repo_qol.repo_doctor.git_dirty_state
    old_run_local_ai = repo_qol.run_json_local_ai
    old_github = repo_qol.github_validation_trigger_state
    old_project_initialization = repo_setup.build_project_initialization_report

    def fake_status(root: Path) -> dict[str, object]:
        return dict(fake_navigation)

    try:
        repo_qol_daily.navigation_status = fake_status
        repo_qol.navigation_status = fake_status
        repo_qol_dashboard.navigation_status = fake_status
        repo_setup.navigation_status = fake_status
        repo_changed.navigation_status = fake_status
        repo_changed.changed_files = lambda root: []
        repo_qol.repo_health.build_repo_health_report = lambda root: {"ok": True, "generated_checks": []}
        repo_qol.repo_doctor.git_dirty_state = lambda root: {"ok": True, "status": "clean", "dirty": False}
        repo_qol.run_json_local_ai = lambda *args, **kwargs: {"ok": True, "result": {"query_readiness": {"status": "fresh", "query_safe": True}}}
        repo_qol.github_validation_trigger_state = lambda root: {"status": "not-checked", "automatic_triggers": []}
        repo_setup.build_project_initialization_report = lambda args, root: {
            "ok": True,
            "ready": False,
            "status": "needs-initialization",
            "mode": "check",
            "navigation": dict(fake_navigation),
            "project_context": {},
        }

        startup = repo_qol_daily.startup_context_report(tmp, compact=True)
        startup_summary = repo_qol_daily.summarize_startup_context_report(startup, compact=True)
        dashboard = repo_qol.dashboard_report(tmp, skip_local_ai=True, skip_github=True)
        dashboard_summary = repo_qol.summarize_dashboard_report(dashboard, compact=True)
        setup_report = repo_setup.build_setup_report(
            Namespace(
                check=True,
                dry_run=False,
                deep=False,
                no_link_skills=True,
                targets=[],
                mode="copy",
                codex_skills_path="",
                claude_skills_path="",
                copilot_skills_path="",
                skill_source_path="",
            ),
            tmp,
            sync_all_func=lambda root, check: 0,
            validate_func=lambda root: 0,
            deep_validate_func=lambda root: 0,
        )
        setup_summary = repo_setup.setup_summary(setup_report, compact=True)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            changed_status = repo_changed.check_changed(Namespace(format="json", deep=False, verbose=False), tmp)
    finally:
        if old_daily is not None:
            repo_qol_daily.navigation_status = old_daily
        if old_qol is not None:
            repo_qol.navigation_status = old_qol
        if old_dashboard is not None:
            repo_qol_dashboard.navigation_status = old_dashboard
        if old_setup is not None:
            repo_setup.navigation_status = old_setup
        if old_changed is not None:
            repo_changed.navigation_status = old_changed
        repo_changed.changed_files = old_changed_files
        repo_qol.repo_health.build_repo_health_report = old_health
        repo_qol.repo_doctor.git_dirty_state = old_dirty
        repo_qol.run_json_local_ai = old_run_local_ai
        repo_qol.github_validation_trigger_state = old_github
        repo_setup.build_project_initialization_report = old_project_initialization

    changed_payload = json.loads(stdout.getvalue())
    assert changed_status == 0
    assert startup["navigation"] == fake_navigation
    assert startup_summary["navigation"] == fake_navigation
    assert dashboard["navigation"] == fake_navigation
    assert dashboard_summary["navigation"] == fake_navigation
    assert setup_report["navigation"] == fake_navigation
    assert setup_summary["navigation"] == fake_navigation
    assert changed_payload["navigation"] == fake_navigation


def test_setup_report_uses_navigation_status_after_write_refresh(tmp: Path) -> None:
    stale_navigation = {
        "status": "stale",
        "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
        "next_command": "python -B .agents/manage.py setup",
        "stale_output_count": 8,
        "summary": "stale before refresh",
    }
    fresh_navigation = {
        "status": "fresh",
        "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
        "next_command": "none, navigation maps are fresh",
        "stale_output_count": 0,
        "summary": "fresh after refresh",
    }
    old_navigation = repo_setup.navigation_status
    old_project_initialization = repo_setup.build_project_initialization_report
    calls = {"project_initialization": 0}

    def fake_project_initialization(args, root: Path) -> dict[str, object]:
        calls["project_initialization"] += 1
        if calls["project_initialization"] >= 2:
            write_text(root / ".fresh-navigation", "ready")
        return {
            "ok": True,
            "ready": True,
            "status": "ready",
            "mode": "write",
            "navigation": {},
            "project_context": {},
        }

    def fake_navigation(root: Path) -> dict[str, object]:
        return fresh_navigation if (root / ".fresh-navigation").exists() else stale_navigation

    try:
        repo_setup.navigation_status = fake_navigation
        repo_setup.build_project_initialization_report = fake_project_initialization
        report = repo_setup.build_setup_report(
            Namespace(
                check=False,
                dry_run=False,
                deep=False,
                no_link_skills=True,
                targets=[],
                mode="copy",
                codex_skills_path="",
                claude_skills_path="",
                copilot_skills_path="",
                skill_source_path="",
            ),
            tmp,
            sync_all_func=lambda root, check: 0,
            validate_func=lambda root: 0,
            deep_validate_func=lambda root: 0,
        )
    finally:
        repo_setup.navigation_status = old_navigation
        repo_setup.build_project_initialization_report = old_project_initialization

    assert report["navigation"] == fresh_navigation


def test_skill_handoff_scorecard_eval_gap_and_route_audit(tmp: Path) -> None:
    skill_dir = write_skill(tmp)
    write_text(skill_dir / "templates" / "handoff.md", "# Template\n\n- Decision: TBD\n")

    handoff = repo_optimizations.skill_handoff_packet(tmp, "demo-skill")
    scorecard = repo_optimizations.skill_scorecard(tmp, ["demo-skill"])
    eval_gap = repo_optimizations.skill_eval_gap(tmp, ["demo-skill"])
    route_audit = repo_optimizations.routing_confidence_audit(tmp)
    template_scan = repo_optimizations.template_placeholder_scan(tmp)
    lessons = repo_optimizations.lesson_promotion_queue(tmp)
    compact_gap = repo_optimizations.summarize_eval_gap(eval_gap, "skills", compact=True)
    compact_templates = repo_optimizations.summarize_template_scan(template_scan, compact=True)
    compact_lessons = repo_optimizations.summarize_lesson_queue(lessons, compact=True)
    compact_handoff = repo_optimizations.summarize_skill_handoff(handoff, compact=True)
    compact_route_audit = repo_optimizations.summarize_route_audit(route_audit, compact=True)

    assert handoff["skill"] == "demo-skill"
    assert "SKILL.md" in " ".join(handoff["required_next_context"])
    assert scorecard["summary"]["skill_count"] == 1
    assert scorecard["skills"][0]["percent"] >= 80
    assert scorecard["summary"]["eval_gap_count"] == 0
    assert scorecard["skills"][0]["advisory_eval_gap"]["missing_assertion_count"] == 0
    assert eval_gap["summary"]["skill_count"] == 1
    assert compact_gap["summary"]["gap_count"] == 0
    assert "skills" not in compact_gap
    assert route_audit["summary"]["duplicate_trigger_group_count"] == 0
    assert compact_route_audit["summary"]["duplicate_trigger_group_count"] == 0
    assert "duplicate_trigger_groups" not in compact_route_audit
    assert compact_handoff["summary"]["validation_command_count"] >= 3
    assert "validation_plan" not in compact_handoff
    assert template_scan["summary"]["issue_count"] == 1
    assert compact_templates["issues"][0]["path"].endswith("templates/handoff.md")
    assert lessons["summary"]["candidate_count"] == 0
    assert "candidates" not in compact_lessons


def test_benchmark_friction_reports_actionable_local_ai_backlog(tmp: Path) -> None:
    run_dir = tmp / "automations" / "local-ai-benchmark-workflow" / "runs" / "gpu-probe"
    write_json(
        run_dir / "run.json",
        {
            "schema_version": 2,
            "workflow": "local-ai-benchmark-workflow",
            "run_id": "gpu-probe",
            "status": "completed-with-findings",
            "skipped": [{"reason": "No pinned Windows x64 OpenCL runtime package is available."}],
            "failed": [{"summary": "HIP text workload failed after ROCm device discovery."}],
            "blocked": [],
            "unsupported_claims": [],
        },
    )

    report = repo_benchmark.benchmark_friction_report(tmp, workflow_name="local-ai-benchmark-workflow")
    compact = repo_benchmark.summarize_benchmark_friction_report(report, compact=True)

    assert report["ok"] is True
    assert report["summary"]["friction_backlog_count"] == 2
    assert compact["summary"]["friction_backlog_count"] == 2
    assert compact["friction_backlog"][0]["items"][0]["recommended_action"]


def release_gate_doctor_ok(root: Path, *, suite: str | None = None, run: str | None = None) -> dict[str, object]:
    _ = root, suite, run
    return {"ok": True, "status": "passed", "checks": [], "issues": [], "warnings": []}


def run_release_gate_budget_fixture(tmp: Path, intent: str, budget: dict[str, object]) -> tuple[int, dict[str, object]]:
    def fake_budget(
        root: Path,
        *,
        baseline_ref: str,
        intent: str,
        max_total_growth: int | None,
        max_tool_growth: int | None,
    ) -> dict[str, object]:
        _ = root, max_total_growth, max_tool_growth
        assert baseline_ref == "HEAD"
        assert intent == expected_intent
        return {**budget, "intent": intent, "baseline_ref": baseline_ref}

    expected_intent = intent
    original = getattr(repo_benchmark, "budget_gate_report", None)
    repo_benchmark.budget_gate_report = fake_budget
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            status = repo_benchmark.benchmark_release_gate(
                ["--json", "--summary", "--compact", "--budget-intent", intent, "--budget-baseline-ref", "HEAD"],
                tmp,
                release_gate_doctor_ok,
            )
    finally:
        if original is None:
            delattr(repo_benchmark, "budget_gate_report")
        else:
            repo_benchmark.budget_gate_report = original
    return status, json.loads(stdout.getvalue())


def test_benchmark_release_gate_falls_back_to_suite_checks_without_runs(tmp: Path) -> None:
    calls: list[tuple[str | None, str | None]] = []

    def fake_doctor(root: Path, *, suite: str | None = None, run: str | None = None) -> dict[str, object]:
        _ = root
        calls.append((suite, run))
        return {
            "schema_version": 1,
            "tool": "agent-benchmarking.doctor",
            "ok": True,
            "status": "passed",
            "checks": [{"name": "suite", "ok": True}],
            "issues": [],
            "warnings": [],
        }

    old_latest = repo_benchmark.latest_benchmark_result_run
    try:
        repo_benchmark.latest_benchmark_result_run = lambda root: ""
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = repo_benchmark.benchmark_release_gate(["--json", "--summary", "--compact"], tmp, fake_doctor)
    finally:
        repo_benchmark.latest_benchmark_result_run = old_latest

    payload = json.loads(stdout.getvalue())

    assert status == 0, payload
    assert len(calls) == 6
    assert all(run is None for _suite, run in calls)
    assert payload["summary"]["passed"] == 6
    assert payload["summary"]["skipped_count"] == 2


def test_benchmark_release_gate_blocks_optimization_budget_growth(tmp: Path) -> None:
    status, payload = run_release_gate_budget_fixture(
        tmp,
        "optimization",
        {
            "ok": False,
            "status": "failed",
            "delta": {"summary": {"total_text_words": 5, "tool_load_words": 3}},
            "issues": ["optimization budget grew: total_text_words +5 > +0"],
        },
    )

    assert status == 1
    assert payload["summary"]["budget_gate_status"] == "failed"
    assert payload["budget_gate"]["intent"] == "optimization"
    assert payload["issues"] == ["optimization budget grew: total_text_words +5 > +0"]


def test_benchmark_release_gate_records_feature_budget_growth(tmp: Path) -> None:
    status, payload = run_release_gate_budget_fixture(
        tmp,
        "feature",
        {
            "ok": True,
            "status": "feature-growth-recorded",
            "delta": {"summary": {"total_text_words": 402, "tool_load_words": 402}},
            "issues": [],
        },
    )

    assert status == 0, payload
    assert payload["summary"]["budget_gate_status"] == "feature-growth-recorded"
    assert payload["budget_gate"]["delta"]["summary"]["total_text_words"] == 402


def ok_finish_capture(root: Path, command: list[str], *, timeout: int = 90) -> dict[str, object]:
    _ = root, timeout
    return {
        "ok": True,
        "status": 0,
        "command": " ".join(command),
        "output_tail": "ok",
        "elapsed_seconds": 0.01,
    }


def run_finish_budget_fixture(
    tmp: Path,
    *,
    intent: str,
    budget: dict[str, object],
) -> dict[str, object]:
    original_capture = repo_qol_finish.run_capture
    original_budget = repo_qol_finish.budget_gate_report
    calls: dict[str, object] = {}

    def fake_budget(root: Path, **kwargs):
        _ = root
        calls.update(kwargs)
        return budget

    try:
        repo_qol_finish.run_capture = ok_finish_capture
        repo_qol_finish.budget_gate_report = fake_budget
        report = repo_qol_finish.finish_work_report(
            tmp,
            budget_intent=intent,
        )
    finally:
        repo_qol_finish.run_capture = original_capture
        repo_qol_finish.budget_gate_report = original_budget
    assert calls["intent"] == intent
    assert calls["baseline_ref"] == "HEAD"
    assert calls["max_total_growth"] is None
    return report


def test_finish_work_budget_gate_handles_feature_and_optimization(tmp: Path) -> None:
    blocked = run_finish_budget_fixture(
        tmp,
        intent="optimization",
        budget={
            "ok": False,
            "status": "failed",
            "intent": "optimization",
            "delta": {"summary": {"total_text_words": 7, "tool_load_words": 2}},
            "issues": ["optimization budget grew: total_text_words +7 > +0"],
        },
    )
    recorded = run_finish_budget_fixture(
        tmp,
        intent="feature",
        budget={
            "ok": True,
            "status": "feature-growth-recorded",
            "intent": "feature",
            "delta": {"summary": {"total_text_words": 42, "tool_load_words": 12}},
            "issues": [],
        },
    )

    assert blocked["ok"] is False
    assert blocked["budget_gate"]["status"] == "failed"
    assert "measure-skill-budget" in blocked["next_command"]
    assert recorded["ok"] is True
    assert recorded["budget_gate"]["status"] == "feature-growth-recorded"
    assert recorded["next_command"] == "python -B .agents/manage.py commit-readiness"


def deep_validation_receipt_fixture(
    tmp: Path,
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    dict[str, object],
    dict[str, object],
    dt.datetime,
]:
    command = [
        sys.executable,
        "-B",
        ".agents/manage.py",
        "check-changed",
        "--deep",
        "--record-progress",
        "--summary",
        "--compact",
        "--format",
        "json",
    ]
    specs: list[dict[str, object]] = [
        {
            "phase": "changed-scope",
            "command": command,
            "timeout_seconds": repo_qol_finish.FINISH_CHANGED_DEEP_TIMEOUT_SECONDS,
        }
    ]
    validation_plan: list[dict[str, object]] = [
        {
            "check_id": "skill-manager:self-tests",
            "command": f"{sys.executable} -B run-self-tests.py --match finish",
            "required": True,
        }
    ]
    fingerprint: dict[str, object] = {
        "digest": "a" * 64,
        "runtime": {
            "python_executable": sys.executable,
            "python_implementation": "CPython",
            "python_version": "3.12.0",
            "platform_machine": "AMD64",
            "platform_release": "fixture",
            "platform_system": "Windows",
        },
    }
    now = dt.datetime(2026, 7, 25, 12, 5, tzinfo=dt.timezone.utc)
    progress: dict[str, object] = {
        "schema_version": 1,
        "tool": "skill-manager.validation-progress",
        "command": "check-changed",
        "phase": "complete",
        "status": "passed",
        "path": (tmp / ".agents/local-ai/cache/validation-progress.json").as_posix(),
        "recorded_at": "2026-07-25T12:00:00Z",
        "elapsed_ms": 620_381,
        "extra": {
            "command_argv": command,
            "failed_check_count": 0,
            "input_fingerprint_digest": fingerprint["digest"],
            "post_input_fingerprint_digest": fingerprint["digest"],
            "input_stable": True,
            "profile": "deep",
            "side_effect_boundary": "repository-read-only-and-temporary-restored",
            "required_check_ids": ["skill-manager:self-tests"],
            "passed_check_ids": ["skill-manager:self-tests"],
        },
    }
    return specs, validation_plan, fingerprint, progress, now


def test_finish_validation_receipt_reuses_only_exact_deep_proof(tmp: Path) -> None:
    specs, validation_plan, fingerprint, progress, now = deep_validation_receipt_fixture(tmp)

    reuse = repo_qol_finish.validation_receipt_reuse_report(
        tmp,
        specs,
        deep=True,
        release_full=False,
        input_fingerprint=fingerprint,
        validation_plan=validation_plan,
        validation_progress=progress,
        now=now,
    )

    assert reuse["eligible"] is True
    assert reuse["status"] == "reused"
    assert reuse["age_seconds"] == 300.0
    assert reuse["source_elapsed_ms"] == 620_381
    assert reuse["check"]["execution_mode"] == "validation-progress-receipt"
    assert reuse["check"]["validation_receipt"]["command_argv"] == specs[0]["command"]

    calls: list[list[str]] = []
    original_capture = repo_qol_finish.run_capture

    def unexpected_capture(root: Path, command: list[str], *, timeout: int = 90):
        _ = root, timeout
        calls.append(command)
        return {"ok": True, "status": 0}

    try:
        repo_qol_finish.run_capture = unexpected_capture
        checks, events = repo_qol_finish.run_finish_check_specs(
            tmp,
            specs,
            reusable_checks={"changed-scope": reuse["check"]},
        )
    finally:
        repo_qol_finish.run_capture = original_capture

    assert calls == []
    assert checks[0]["ok"] is True
    assert checks[0]["execution_mode"] == "validation-progress-receipt"
    assert events[-1]["execution_mode"] == "validation-progress-receipt"


def test_finish_validation_receipt_production_progress_handshake(tmp: Path) -> None:
    specs, validation_plan, fingerprint, progress, _now = deep_validation_receipt_fixture(tmp)
    written = repo_command_metrics.write_validation_progress(
        tmp,
        command="check-changed",
        phase="complete",
        status="passed",
        started=time.perf_counter(),
        completed=1,
        total=1,
        extra=progress["extra"],
    )
    persisted = repo_command_metrics.read_validation_progress(tmp)

    reuse = repo_qol_finish.validation_receipt_reuse_report(
        tmp,
        specs,
        deep=True,
        release_full=False,
        input_fingerprint=fingerprint,
        validation_plan=validation_plan,
        validation_progress=persisted,
    )

    assert written["recorded_at"]
    assert persisted["schema_version"] == 1
    assert persisted["tool"] == "skill-manager.validation-progress"
    assert persisted["extra"]["post_input_fingerprint_digest"] == fingerprint["digest"]
    assert persisted["extra"]["side_effect_boundary"] == (
        "repository-read-only-and-temporary-restored"
    )
    assert reuse["eligible"] is True


def test_finish_validation_receipt_fails_closed_for_stale_or_mutated_proof(tmp: Path) -> None:
    specs, validation_plan, fingerprint, progress, now = deep_validation_receipt_fixture(tmp)

    def report_for(
        candidate: dict[str, object],
        *,
        current: dt.datetime = now,
        release_full: bool = False,
    ) -> dict[str, object]:
        return repo_qol_finish.validation_receipt_reuse_report(
            tmp,
            specs,
            deep=True,
            release_full=release_full,
            input_fingerprint=fingerprint,
            validation_plan=validation_plan,
            validation_progress=candidate,
            now=current,
        )

    mutated: list[dict[str, object]] = []
    wrong_command = json.loads(json.dumps(progress))
    wrong_command["extra"]["command_argv"][-1] = "markdown"
    mutated.append(wrong_command)
    wrong_post = json.loads(json.dumps(progress))
    wrong_post["extra"]["post_input_fingerprint_digest"] = "b" * 64
    mutated.append(wrong_post)
    unstable = json.loads(json.dumps(progress))
    unstable["extra"]["input_stable"] = False
    mutated.append(unstable)
    incomplete = json.loads(json.dumps(progress))
    incomplete["extra"]["passed_check_ids"] = []
    mutated.append(incomplete)
    wrong_profile = json.loads(json.dumps(progress))
    wrong_profile["extra"]["profile"] = "changed"
    mutated.append(wrong_profile)
    missing_timestamp = json.loads(json.dumps(progress))
    missing_timestamp.pop("recorded_at")
    mutated.append(missing_timestamp)
    wrong_schema = json.loads(json.dumps(progress))
    wrong_schema["schema_version"] = 999
    mutated.append(wrong_schema)
    wrong_tool = json.loads(json.dumps(progress))
    wrong_tool["tool"] = "untrusted.writer"
    mutated.append(wrong_tool)
    missing_boundary = json.loads(json.dumps(progress))
    missing_boundary["extra"].pop("side_effect_boundary")
    mutated.append(missing_boundary)
    extra_pass = json.loads(json.dumps(progress))
    extra_pass["extra"]["passed_check_ids"].append("unselected:check")
    mutated.append(extra_pass)
    malformed_failed_count = json.loads(json.dumps(progress))
    malformed_failed_count["extra"]["failed_check_count"] = "not-an-int"
    mutated.append(malformed_failed_count)
    malformed_elapsed = json.loads(json.dumps(progress))
    malformed_elapsed["elapsed_ms"] = "not-a-number"
    mutated.append(malformed_elapsed)
    overflowing_elapsed = json.loads(json.dumps(progress))
    overflowing_elapsed["elapsed_ms"] = 10**4000
    mutated.append(overflowing_elapsed)
    malformed_command = json.loads(json.dumps(progress))
    malformed_command["extra"]["command_argv"] = {"bad": "shape"}
    mutated.append(malformed_command)
    malformed_passed = json.loads(json.dumps(progress))
    malformed_passed["extra"]["passed_check_ids"] = {"bad": "shape"}
    mutated.append(malformed_passed)

    for candidate in mutated:
        report = report_for(candidate)
        assert report["eligible"] is False, report
        assert "check" not in report

    stale = report_for(
        progress,
        current=now + dt.timedelta(seconds=repo_qol_finish.VALIDATION_RECEIPT_MAX_AGE_SECONDS + 1),
    )
    exhaustive_report = report_for(progress, release_full=True)
    malformed_release_report = report_for(malformed_elapsed, release_full=True)
    assert stale["eligible"] is False
    assert "older" in stale["reason"]
    assert exhaustive_report["eligible"] is False
    assert "fresh" in exhaustive_report["reason"]
    assert malformed_release_report["eligible"] is False
    assert "fresh" in malformed_release_report["reason"]


def test_finish_work_reruns_changed_scope_when_pre_phase_changes_input(tmp: Path) -> None:
    specs, validation_plan, fingerprint, progress, _now = deep_validation_receipt_fixture(tmp)
    initial_fingerprint = {**fingerprint, "digest": "1" * 64}
    current_fingerprint = {**fingerprint, "digest": "2" * 64}
    current_progress = json.loads(json.dumps(progress))
    current_progress["extra"]["input_fingerprint_digest"] = current_fingerprint["digest"]
    current_progress["extra"]["post_input_fingerprint_digest"] = current_fingerprint["digest"]
    pre_command = [
        sys.executable,
        "-B",
        ".agents/manage.py",
        "clean-context-proof",
        "--summary",
        "--compact",
        "--format",
        "json",
    ]
    selected_specs = [
        {"phase": "clean-context-proof", "command": pre_command, "timeout_seconds": 120},
        specs[0],
    ]
    states = [
        ([], {}, validation_plan, initial_fingerprint),
        ([], {}, validation_plan, current_fingerprint),
        ([], {}, validation_plan, current_fingerprint),
    ]
    calls: list[list[str]] = []
    original_state = repo_qol_finish.finish_validation_state
    original_specs = repo_qol_finish.finish_check_specs
    original_capture = repo_qol_finish.run_capture
    original_progress = repo_qol_finish.repo_command_metrics.read_validation_progress
    original_indexes = repo_qol_finish.workflows_with_run_folders
    original_hotspots = repo_qol_finish.budget_hotspots_report
    original_github = repo_qol_finish.github_validation_trigger_state

    def fake_capture(root: Path, command: list[str], *, timeout: int = 90):
        _ = root, timeout
        calls.append(command)
        return {
            "ok": True,
            "status": 0,
            "command": " ".join(command),
            "elapsed_seconds": 0.01,
        }

    try:
        repo_qol_finish.finish_validation_state = lambda root, deep: states.pop(0)
        repo_qol_finish.finish_check_specs = lambda root, **kwargs: selected_specs
        repo_qol_finish.run_capture = fake_capture
        repo_qol_finish.repo_command_metrics.read_validation_progress = (
            lambda root: current_progress
        )
        repo_qol_finish.workflows_with_run_folders = lambda root: []
        repo_qol_finish.budget_hotspots_report = lambda root: {"ok": True, "status": "passed"}
        repo_qol_finish.github_validation_trigger_state = lambda root: {
            "status": "local-only",
            "automatic_triggers_enabled": False,
            "automatic_triggers": [],
        }
        report = repo_qol_finish.finish_work_report(tmp, deep=True)
    finally:
        repo_qol_finish.finish_validation_state = original_state
        repo_qol_finish.finish_check_specs = original_specs
        repo_qol_finish.run_capture = original_capture
        repo_qol_finish.repo_command_metrics.read_validation_progress = original_progress
        repo_qol_finish.workflows_with_run_folders = original_indexes
        repo_qol_finish.budget_hotspots_report = original_hotspots
        repo_qol_finish.github_validation_trigger_state = original_github

    assert calls == [pre_command, specs[0]["command"]]
    assert report["validation_reuse"]["eligible"] is False
    assert "phase selection" in report["validation_reuse"]["reason"], report[
        "validation_reuse"
    ]
    assert report["finish_input_stability"]["phase_selection_stable"] is False
    assert report["ok"] is False


def test_finish_input_stability_rejects_post_phase_mutation(tmp: Path) -> None:
    specs, validation_plan, fingerprint, progress, _now = deep_validation_receipt_fixture(tmp)
    final_fingerprint = {**fingerprint, "digest": "f" * 64}
    stability = repo_qol_finish.finish_input_stability_report(
        initial_fingerprint=fingerprint,
        validated_fingerprint=fingerprint,
        final_fingerprint=final_fingerprint,
        validation_plan=validation_plan,
        validation_progress=progress,
        expected_command=specs[0]["command"],
        changed_check={
            "ok": True,
            "phase": "changed-scope",
            "command_argv": specs[0]["command"],
            "execution_mode": "subprocess",
        },
        profile="deep",
    )

    assert stability["phase_selection_stable"] is True
    assert stability["post_phase_stable"] is False
    assert stability["ok"] is False
    assert "after changed-scope validation" in stability["reasons"][0]


def test_finish_claim_revalidates_reused_receipt_contract(tmp: Path) -> None:
    specs, validation_plan, fingerprint, progress, now = deep_validation_receipt_fixture(tmp)
    reuse = repo_qol_finish.validation_receipt_reuse_report(
        tmp,
        specs,
        deep=True,
        release_full=False,
        input_fingerprint=fingerprint,
        validation_plan=validation_plan,
        validation_progress=progress,
        now=now,
    )
    check = reuse["check"]
    finish_report = {"input_fingerprint": fingerprint}

    assert repo_qol_readiness._changed_scope_receipt_matches(
        check,
        finish_report,
        expected_profile="deep",
    )

    mutations = [
        ("schema_version", 2),
        ("verified", False),
        ("source_schema_version", 2),
        ("source_tool", "untrusted.writer"),
        ("input_fingerprint_digest", "b" * 64),
        ("post_input_fingerprint_digest", "b" * 64),
        ("input_stable", False),
        ("profile", "changed"),
        ("side_effect_boundary", "unknown"),
        ("failed_check_count", 1),
        ("age_seconds", None),
        ("passed_check_ids", []),
    ]
    for field, value in mutations:
        candidate = json.loads(json.dumps(check))
        candidate["validation_receipt"][field] = value
        assert not repo_qol_readiness._changed_scope_receipt_matches(
            candidate,
            finish_report,
            expected_profile="deep",
        ), field

    wrong_environment = json.loads(json.dumps(check))
    wrong_environment["validation_receipt"]["environment_fingerprint"]["python_version"] = "0"
    assert not repo_qol_readiness._changed_scope_receipt_matches(
        wrong_environment,
        finish_report,
        expected_profile="deep",
    )


def test_finish_compact_metrics_report_reuse_savings_and_slowest_checks(tmp: Path) -> None:
    specs, validation_plan, fingerprint, progress, now = deep_validation_receipt_fixture(tmp)
    reuse = repo_qol_finish.validation_receipt_reuse_report(
        tmp,
        specs,
        deep=True,
        release_full=False,
        input_fingerprint=fingerprint,
        validation_plan=validation_plan,
        validation_progress=progress,
        now=now,
    )
    checks = [
        reuse["check"],
        {
            "ok": True,
            "phase": "workflow-hooks",
            "elapsed_seconds": 2.5,
            "execution_mode": "subprocess",
            "output_tail": "ok",
        },
    ]

    _compact, metrics = repo_qol_finish.compact_finish_checks(checks)

    assert metrics["reused_check_count"] == 1
    assert metrics["reused_source_elapsed_seconds"] == 620.381
    assert metrics["reuse_timing_basis"] == "counterfactual-source-duration"
    assert metrics["slowest_checks"][0]["name"] == "workflow-hooks"


def test_finish_compact_completion_omits_completed_review_progress(tmp: Path) -> None:
    _ = tmp
    report = {
        "schema_version": 1,
        "tool": "repo-finish",
        "ok": True,
        "status": "passed",
        "completion_supported": True,
        "checks": [{"command": "check-a", "ok": True, "status": 0}],
        "review_progress": {
            "status": "complete",
            "stale": False,
            "coverage": {
                "status": "complete",
                "pending_review_unit_count": 0,
            },
        },
        "next_command": "python -B .agents/manage.py commit-readiness",
    }

    compact = repo_qol_finish_packets.summarize_finish_work_report(
        report,
        compact=True,
    )

    assert "review_progress" not in compact


def test_focused_self_test_matches_are_precise_for_support_modules(tmp: Path) -> None:
    _ = tmp
    analytics = repo_optimizations.focused_self_test_matches(
        "workflow-manager",
        [".agents/skills/workflow-manager/scripts/workflow_support/analytics.py"],
    )
    story_bug = repo_optimizations.focused_self_test_matches(
        "workflow-manager",
        [".agents/skills/workflow-manager/scripts/workflow_support/run_story_bug.py"],
    )
    benchmark = repo_optimizations.focused_self_test_matches(
        "skill-manager",
        [".agents/skills/skill-manager/scripts/repo_support/repo_benchmark.py"],
    )
    review_support = repo_optimizations.self_test_selection_for_skill(
        "skill-manager",
        [
            ".agents/skills/skill-manager/scripts/repo_support/repo_review_packet.py",
            ".agents/skills/skill-manager/scripts/repo_support/repo_review_progress.py",
        ],
    )
    changed_git_support = repo_optimizations.self_test_selection_for_skill(
        "skill-manager",
        [".agents/skills/skill-manager/scripts/repo_support/repo_changed_git.py"],
    )
    changed_support = repo_optimizations.self_test_selection_for_skill(
        "skill-manager",
        [".agents/skills/skill-manager/scripts/repo_support/repo_changed.py"],
    )
    metrics_support = repo_optimizations.self_test_selection_for_skill(
        "skill-manager",
        [".agents/skills/skill-manager/scripts/repo_support/repo_command_metrics.py"],
    )
    fingerprint_support = repo_optimizations.self_test_selection_for_skill(
        "skill-manager",
        [".agents/skills/skill-manager/scripts/repo_support/repo_fingerprint.py"],
    )
    context_support = repo_optimizations.self_test_selection_for_skill(
        "skill-manager",
        [".agents/skills/skill-manager/scripts/repo_support/repo_qol_context.py"],
    )
    optimization_support = repo_optimizations.self_test_selection_for_skill(
        "skill-manager",
        [".agents/skills/skill-manager/scripts/repo_support/repo_optimizations.py"],
    )
    readiness_support = repo_optimizations.self_test_selection_for_skill(
        "skill-manager",
        [".agents/skills/skill-manager/scripts/repo_support/repo_qol_readiness.py"],
    )
    finish_packet_support = repo_optimizations.self_test_selection_for_skill(
        "skill-manager",
        [".agents/skills/skill-manager/scripts/repo_support/repo_qol_finish_packets.py"],
    )
    parser_support = repo_optimizations.self_test_selection_for_skill(
        "skill-manager",
        [".agents/skills/skill-manager/scripts/repo_support/repo_qol_parsers.py"],
    )
    runner_support = repo_optimizations.self_test_selection_for_skill(
        "skill-manager",
        [".agents/skills/skill-manager/scripts/run_self_tests.py"],
    )

    assert analytics == ["workflow_analytics"]
    assert story_bug == ["story_bug"]
    assert benchmark == ["benchmark_"]
    assert review_support["mode"] == "focused"
    assert "review_packet" in review_support["matches"]
    assert "review_loop" in review_support["matches"]
    assert changed_git_support["mode"] == "focused"
    assert "changed_file_statuses" in changed_git_support["matches"]
    assert "check_changed" in changed_git_support["matches"]
    assert "planned_command_timeout" in changed_support["matches"]
    assert "run_capture_timeout" in changed_support["matches"]
    assert "validation_progress" in metrics_support["matches"]
    assert fingerprint_support["mode"] == "focused"
    assert "input_fingerprint" in fingerprint_support["matches"]
    assert "finish" in fingerprint_support["matches"]
    assert "review_autopilot" in context_support["matches"]
    assert "focused_self_test_matches" in optimization_support["matches"]
    assert readiness_support["mode"] == "focused"
    assert "finish_claim" in readiness_support["matches"]
    assert "claim_receipt" in readiness_support["matches"]
    assert finish_packet_support["mode"] == "focused"
    assert "finish_summary" in finish_packet_support["matches"]
    assert "repo_qol_parser" in parser_support["matches"]
    assert runner_support["mode"] == "full"
    assert runner_support["full_required_paths"] == [".agents/skills/skill-manager/scripts/run_self_tests.py"]


def test_command_common_paths_include_compact_skill_and_release_examples(tmp: Path) -> None:
    parser = repo_cli_parser.build_parser()
    args = parser.parse_args(["reference-refresh", "--mode", "dry-run", "--format", "json"])
    finish = parser.parse_args(["finish", "--budget-intent", "optimization"])
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        status = repo_commands.print_commands(parser, "markdown", root=tmp)
    output = stdout.getvalue()
    help_stdout = io.StringIO()
    with contextlib.redirect_stdout(help_stdout):
        try:
            parser.parse_args(["reference-refresh", "--help"])
        except SystemExit as exc:
            assert exc.code == 0
        else:
            raise AssertionError("expected reference-refresh --help to exit")
    help_output = help_stdout.getvalue().replace("\n", " ")

    assert args.command == "reference-refresh"
    assert args.mode == "dry-run"
    assert finish.command == "finish"
    assert finish.budget_intent == "optimization"
    assert status == 0
    assert "skill route-audit --summary --compact --format json" in output
    assert "reference-refresh --mode report --format markdown" in output
    assert "finish --release-full --commit-packet evidence/finish" in output
    assert "release-evidence" in output
    assert "commit-readiness" in output
    assert "Report mode is read-only" in help_output
    assert "Dry-run does not fetch or write" in help_output
    assert "Write mode may clone" in help_output
