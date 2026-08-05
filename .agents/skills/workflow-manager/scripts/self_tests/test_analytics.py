"""Focused self-tests for workflow analytics helpers."""

from __future__ import annotations

import json
from pathlib import Path

import workflow_manager_common as common
from workflow_support import analytics as workflow_analytics


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_workflow(root: Path, name: str = "story-flow") -> Path:
    module_dir = root / "automations" / name
    write_text(module_dir / "WORKFLOW.md", f"# {name}\n")
    write_text(
        module_dir / "instructions.md",
        """# Instructions

## Always Load

- Keep `run.json` current.

## Stop Rules

- Stop when evidence is missing.

## Completion Contract

- Report validation.

## Phase: execute

- Execute the deterministic fixture.
""",
    )
    write_json(
        module_dir / "module.json",
        {
            "schema_version": 3,
            "kind": "workflow",
            "id": name,
            "version": "1.0.0",
            "summary": "Fixture workflow.",
            "owners": ["engineering"],
            "phases": [{"id": "execute", "summary": "Execute."}],
            "inputs": ["WORKFLOW.md", "module.json", "instructions.md"],
            "outputs": ["runs/<run-id>/run.json", "runs/<run-id>/REPORT.md"],
            "commands": [],
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
    write_json(
        module_dir / "runs" / "run-a" / "run.json",
        {
            "schema_version": 2,
            "tool": "workflow-manager.run",
            "workflow": name,
            "run_id": "run-a",
            "status": "completed",
            "current_phase": "execute",
            "evidence": [{"kind": "validation", "path": "runs/run-a/REPORT.md"}],
            "evidence_paths": ["runs/run-a/REPORT.md"],
            "skipped": [],
            "blocked": [],
            "failed": [],
            "unsupported_claims": [],
            "external_validation_status": "passed",
        },
    )
    write_text(module_dir / "runs" / "run-a" / "REPORT.md", "# Report\n\nEvidence recorded.")
    return module_dir


def test_workflow_analytics_summarizes_retained_run_friction(tmp: Path) -> None:
    module_dir = write_workflow(tmp, "story-flow")
    run_dir = module_dir / "runs" / "run-a"
    packet = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    packet["status"] = "completed-with-findings"
    packet["skipped"] = [{"check": "browser", "reason": "not applicable"}]
    packet["failed"] = [{"command": "pytest", "summary": "one failure"}]
    packet["unsupported_claims"] = [{"claim": "coverage improved", "evidence": ""}]
    packet["lesson_candidates"] = ["Promote a focused regression fixture."]
    write_json(run_dir / "run.json", packet)

    report = workflow_analytics.workflow_analytics(tmp, workflow_names=["story-flow"])

    assert report["ok"] is True, report
    assert report["summary"]["run_count"] == 1
    assert report["summary"]["failed_check_count"] == 1
    assert report["summary"]["unsupported_claim_count"] == 1
    assert report["workflows"][0]["lesson_candidates"] == ["Promote a focused regression fixture."]
    assert report["summary"]["friction_triage"]["stale-historical"] >= 1
    assert report["summary"]["lesson_candidate_count"] == 1
    assert report["lesson_queue"][0]["lesson"] == "Promote a focused regression fixture."
    assert report["summary"]["friction_backlog_count"] == 3
    assert report["friction_backlog"][0]["classification"] == "stale-historical"
    assert report["friction_backlog"][0]["items"][0]["recommended_action"]
    assert report["friction_backlog"][0]["items"][0]["age_days"] >= 0

    compact = workflow_analytics.compact_analytics(report)

    assert compact["friction_backlog"][0]["items"][0]["workflow"] == "story-flow"
    assert common.relative(tmp, run_dir / "run.json") in compact["friction_backlog"][0]["items"][0]["source"]


def test_workflow_analytics_classifies_intentional_local_ai_skips(tmp: Path) -> None:
    module_dir = write_workflow(tmp, "local-ai-benchmark-workflow")
    run_dir = module_dir / "runs" / "run-a"
    packet = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    packet["skipped"] = [
        {
            "check": "local runtime",
            "reason": "local model/runtime bundle is not installed in this checkout; text/FTS experiment remains runnable without downloads",
        },
        {
            "check": "LLM graph expansion",
            "reason": "would require LLM extraction, prompt tuning, and model/API cost; this run tests deterministic graph expansion first",
        },
    ]
    write_json(run_dir / "run.json", packet)

    report = workflow_analytics.workflow_analytics(tmp, workflow_names=["local-ai-benchmark-workflow"])

    assert report["summary"]["friction_triage"] == {"expected-skip": 2}
    assert report["friction_backlog"][0]["classification"] == "expected-skip"
    assert "review" not in report["summary"]["friction_triage"]
