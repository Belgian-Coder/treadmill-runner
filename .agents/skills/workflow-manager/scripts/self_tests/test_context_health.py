"""Focused lifecycle-health tests for retained workflow context packets."""

from __future__ import annotations

import json
from pathlib import Path

import workflow_repo_manager
from workflow_support import cli_parser
from workflow_support import review


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_workflow(root: Path, name: str = "story-flow") -> Path:
    module_dir = root / "automations" / name
    write_text(module_dir / "WORKFLOW.md", f"# {name}")
    write_json(
        module_dir / "module.json",
        {
            "schema_version": 3,
            "kind": "workflow",
            "id": name,
            "outputs": [
                "runs/<run-id>/run.json",
                "runs/<run-id>/REPORT.md",
                "runs/<run-id>/artifacts/context/context-packet.json",
            ],
        },
    )
    return module_dir


def write_run(
    module_dir: Path,
    run_id: str,
    *,
    status: str | None,
    updated_at: str,
    content: object | None = None,
) -> Path:
    run_dir = module_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if content is not None:
        write_json(run_dir / "run.json", content)
    elif status is not None:
        write_json(
            run_dir / "run.json",
            {
                "schema_version": 2,
                "tool": "workflow-manager.run",
                "workflow": module_dir.name,
                "run_id": run_id,
                "status": status,
                "current_phase": "execute",
                "updated_at": updated_at,
            },
        )
    return run_dir


def rows_by_id(report: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = report.get("workflows") if isinstance(report.get("workflows"), list) else []
    return {
        str(row["run_id"]): row
        for row in rows
        if isinstance(row, dict) and row.get("run_id")
    }


def test_context_all_checks_every_run_and_demotes_completed_failures(tmp: Path) -> None:
    module_dir = write_workflow(tmp)
    write_run(module_dir, "active-old", status="partial", updated_at="2026-07-01T00:00:00Z")
    write_run(module_dir, "completed-new", status="completed", updated_at="2026-07-02T00:00:00Z")

    report = workflow_repo_manager.context_all_workflow_runs(tmp)
    rows = rows_by_id(report)

    assert report["checked_count"] == 2, report
    assert report["blocking_count"] == 1, report
    assert report["advisory_count"] == 1, report
    assert report["ok"] is False, report
    assert rows["active-old"]["blocking"] is True
    assert rows["completed-new"]["advisory"] is True
    assert "--run-id active-old" in str(rows["active-old"]["next_command"])
    assert "--run-id completed-new" in str(rows["completed-new"]["next_command"])


def test_context_all_include_completed_restores_completed_failure(tmp: Path) -> None:
    module_dir = write_workflow(tmp)
    write_run(module_dir, "done-a", status="passed", updated_at="2026-07-01T00:00:00Z")

    default = workflow_repo_manager.context_all_workflow_runs(tmp)
    strict = workflow_repo_manager.context_all_workflow_runs(tmp, include_completed=True)

    assert default["ok"] is True, default
    assert default["status"] == "advisory"
    assert default["blocking_count"] == 0
    assert default["advisory_count"] == 1
    assert strict["ok"] is False, strict
    assert strict["blocking_count"] == 1
    assert strict["advisory_count"] == 0
    assert strict["include_completed"] is True


def test_compact_context_health_keeps_counts_and_run_remediation(tmp: Path) -> None:
    module_dir = write_workflow(tmp)
    write_run(module_dir, "done-a", status="skipped", updated_at="2026-07-01T00:00:00Z")

    report = workflow_repo_manager.context_all_workflow_runs(tmp)
    compact = workflow_repo_manager.compact_context_all_report(report)

    assert compact["blocking_count"] == 0, compact
    assert compact["advisory_count"] == 1, compact
    assert compact["completed_count"] == 1, compact
    assert compact["include_completed"] is False
    assert compact["workflows"][0]["run_id"] == "done-a"
    assert "--run-id done-a" in str(compact["workflows"][0]["next_command"])


def test_context_all_missing_invalid_and_unknown_run_states_block(tmp: Path) -> None:
    module_dir = write_workflow(tmp)
    write_run(module_dir, "missing", status=None, updated_at="")
    invalid = write_run(module_dir, "invalid", status=None, updated_at="")
    write_text(invalid / "run.json", "{not-json")
    write_run(module_dir, "unknown", status=None, updated_at="", content={"run_id": "unknown"})

    report = workflow_repo_manager.context_all_workflow_runs(tmp)
    rows = rows_by_id(report)

    assert report["ok"] is False, report
    assert report["blocking_count"] == 3
    assert {rows[name]["run_status"] for name in rows} == {"missing", "invalid", "unknown"}
    assert all(rows[name]["blocking"] is True for name in rows)


def test_review_prefers_active_run_and_completed_only_is_advisory(tmp: Path) -> None:
    module_dir = write_workflow(tmp)
    write_run(module_dir, "active-old", status="active", updated_at="2026-07-01T00:00:00Z")
    write_run(module_dir, "completed-new", status="done", updated_at="2026-07-02T00:00:00Z")

    selected = review.latest_run_summary(tmp, module_dir, module_dir.name, [])
    strict = review.latest_run_summary(tmp, module_dir, module_dir.name, [], include_completed=True)

    assert selected["run_id"] == "active-old", selected
    assert selected["advisory"] is False
    assert strict["run_id"] == "active-old", strict
    assert strict["advisory"] is False
    assert selected["run_count"] == 2
    assert selected["blocking_count"] == 1
    assert selected["advisory_count"] == 1
    assert strict["run_count"] == 2
    assert strict["blocking_count"] == 2
    assert strict["advisory_count"] == 0
    assert {row["run_id"] for row in selected["runs"]} == {"active-old", "completed-new"}

    (module_dir / "runs" / "active-old" / "run.json").unlink()
    (module_dir / "runs" / "active-old").rmdir()
    completed_only = review.latest_run_summary(tmp, module_dir, module_dir.name, [])

    assert completed_only["run_id"] == "completed-new", completed_only
    assert completed_only["advisory"] is True
    assert completed_only["context_packet"]["ok"] is False
    assert completed_only["blocking_count"] == 0
    assert completed_only["advisory_count"] == 1


def test_include_completed_cli_flag_is_available_for_all_health_checks(tmp: Path) -> None:
    _ = tmp
    parser = cli_parser.build_parser()
    context = parser.parse_args(["context-run", "--all", "--check", "--include-completed"])
    doctor = parser.parse_args(["review-workflow", "--all", "--include-completed"])

    assert context.include_completed is True
    assert doctor.include_completed is True
