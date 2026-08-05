#!/usr/bin/env python3
"""Measure a direct clean artifact envelope without workflow or skill context."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import benchmark_common as common


MEASUREMENT_SCOPE = {
    "scope": "plain-direct-artifact-envelope",
    "included": [
        "input/direct-request.md",
        "input/suite-facts.json",
        "ticket-info.md",
        "plan.md",
        "REPORT.md",
        "execution-log.md",
    ],
    "excluded": [
        "workflow packets",
        "skill instructions",
        "routing context",
        "project context outside suite facts",
        "local-AI advisory artifacts",
        "hidden prompts",
        "billing telemetry",
        "live model transcript",
    ],
    "billing_claim": False,
    "live_llm_run": False,
    "interpretation": (
        "Direct clean artifact envelope for overhead comparison, not a billing export "
        "and not a live true no-harness implementation run."
    ),
}


def task_rows(suite: dict[str, Any], task_ids: list[str]) -> list[dict[str, Any]]:
    tasks = common.task_list(suite)
    wanted = set(task_ids)
    rows: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task.get("id", ""))
        if wanted and task_id not in wanted:
            continue
        rows.append(
            {
                "id": task_id,
                "language": str(task.get("language", "")),
                "required_files": common.as_string_list(task.get("required_files"), "required_files"),
                "summary": str(task.get("summary", "")),
                "minimum_test_attributes": task.get("minimum_test_attributes"),
            }
        )
    if not rows:
        raise SystemExit("no benchmark tasks selected")
    return rows


def render_request(suite: dict[str, Any], tasks: list[dict[str, Any]]) -> str:
    validators = common.as_string_list(suite.get("validators"), "validators")
    lines = [
        "# Direct Clean-Folder Artifact Request",
        "",
        "No workflow instructions, skill instructions, generated routing, run packet, or project context are loaded.",
        "Use only the benchmark task facts below and return a result matching the suite output contract.",
        "",
        f"Suite: {suite.get('suite_id') or suite.get('suite') or 'unknown'}",
        f"Description: {suite.get('description', '')}",
        "",
        "## Output Contract",
        "",
        "Return JSON with `files`, where each item has `path` and complete `content`.",
        "",
        "## Tasks",
        "",
        "| Task | Language | Required Files | Summary |",
        "|---|---|---|---|",
    ]
    for task in tasks:
        files = ", ".join(task["required_files"])
        lines.append(f"| `{task['id']}` | {task['language']} | {files} | {task['summary']} |")
    lines.extend(["", "## Validators", ""])
    for item in validators:
        lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def render_result(tasks: list[dict[str, Any]]) -> str:
    lines = [
        "# Direct Clean-Folder Artifact Result",
        "",
        "This control measures a no-workflow/no-skill artifact envelope from a clean folder.",
        "It does not claim model quality, provider billing usage, or true live no-harness implementation cost.",
        "",
        "## Selected Tasks",
        "",
    ]
    for task in tasks:
        lines.append(f"- `{task['id']}`: {', '.join(task['required_files'])}")
    lines.extend(
        [
            "",
            "## Measurement Boundary",
            "",
            "- Included input: direct request and suite facts saved under `input/`.",
            "- Included output: this result and structure JSON saved under `output/`.",
            "- Excluded: workflow packets, skill instructions, routing registries, local-AI advisory output, hidden prompts, and provider billing telemetry.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_ticket_info(tasks: list[dict[str, Any]]) -> str:
    task_list = "\n".join(f"- [x] Include `{task['id']}`." for task in tasks)
    return f"""# User Story Intake

## Identity

- Work item id: direct-clean-control
- Source: clean-folder control
- Title: Measure direct benchmark execution without workflow or skill context
- Owner: agent-benchmarking
- State: measured

## Description

Measure the direct prompt/result envelope for the benchmark task classes from a clean folder. No workflow instructions, skill instructions, routing docs, project context, or run packet context are loaded.

## Acceptance Criteria

- [x] Use the real benchmark task classes.
{task_list}
- [x] Measure input and output artifact tokens.
- [x] Save the result in the same core document set as workflow runs.

## Scope

- In scope: direct request, suite facts, core handoff docs, exact token measurement.
- Assumptions: provider billing telemetry and hidden prompts are unavailable.

## Out Of Scope

- Workflow execution, skill routing, local-AI advisory output, model-quality scoring, and provider billing claims.
"""


def render_plan(tasks: list[dict[str, Any]]) -> str:
    rows = "\n".join(
        f"| `{task['id']}` | Direct clean-folder request | Token summary and structure JSON | none |"
        for task in tasks
    )
    return f"""# Lean User Story Plan

## Outcome

Measure the direct clean-folder artifact envelope with no workflow or skill context. This is a static envelope measurement, not a live implementation run and not proof of true no-harness quality.

## Out Of Scope

- Excluded: workflow execution, skill instructions, routing context, project context, local-AI advisory output, model-quality scoring, and provider billing claims.

## Acceptance Criteria Mapping

| Acceptance Criterion | Implementation | Validation Evidence | Documentation |
|---|---|---|---|
{rows}

## Impact Discovery Evidence

| Discovery Item | Evidence | Decision Or Missing Fact |
|---|---|---|
| Candidate facts read | `input/suite-facts.json` | Use only direct suite facts. |

## Planned Validation

| Check | Command Or Method | Expected Evidence | Required |
|---|---|---|---|
| Token measurement | clean-folder control script | `summary.json` | yes |
| Context isolation | empty workflow, skill, and routing context lists | `summary.json` | yes |

## Approval Gate

- [x] Stop before implementation.
- Approval status: measured
"""


def render_report(tasks: list[dict[str, Any]]) -> str:
    task_names = ", ".join(f"`{task['id']}`" for task in tasks)
    return f"""# Direct Clean-Folder Artifact Envelope Report

- Current phase: measured
- Workflow context loaded: 0
- Skill context loaded: 0
- Routing context loaded: 0

## Summary

Measured direct clean-folder artifact envelope for {task_names}. The folder writes a small fixed handoff document set for token accounting, but it deliberately excludes workflow packets, skill instructions, routing docs, project context, and local-AI advisory artifacts. Do not treat this as a live true no-harness implementation benchmark.

## Evidence

- `input/direct-request.md`
- `input/suite-facts.json`
- `ticket-info.md`
- `plan.md`
- `REPORT.md`
- `execution-log.md`
- `summary.json`
"""


def render_execution_log(tasks: list[dict[str, Any]]) -> str:
    return f"""# Direct Clean-Folder Execution Log

## Current State

- Status: measured
- Current phase: direct-control

## Commands And Evidence

| Command Or Action | Result | Evidence |
|---|---|---|
| clean-folder control | ok | `summary.json` |

## Context And Claim Support

- Workflow context loaded: none.
- Skill context loaded: none.
- Routing context loaded: none.
- Task classes measured: {", ".join(task["id"] for task in tasks)}.
"""


def file_measure(path: Path, role: str, root: Path) -> dict[str, Any]:
    text = common.read_text(path)
    return {
        "role": role,
        "path": path.resolve().relative_to(root.resolve()).as_posix(),
        "bytes": path.stat().st_size,
        "tokens": common.estimate_tokens(text),
    }


def render_markdown(report: dict[str, Any]) -> str:
    tokens = report["paid_model_tokens"]
    lines = [
        "# Direct Clean-Folder Artifact Envelope",
        "",
        f"- Run: `{report['run_id']}`",
        f"- Workflow context loaded: {len(report['workflow_context_loaded'])}",
        f"- Skill context loaded: {len(report['skill_context_loaded'])}",
        f"- Routing context loaded: {len(report['routing_context_loaded'])}",
        "- Boundary: artifact envelope only; not a billing export or a live model transcript.",
        f"- Input artifact tokens: {tokens['input']}",
        f"- Output artifact tokens: {tokens['output']}",
        f"- Total artifact tokens: {tokens['total']}",
        f"- Token counter: `{report['advisory_token_estimates']['token_counter']['method']}`",
        "",
        "## Core Docs",
        "",
        "- `ticket-info.md`",
        "- `plan.md`",
        "- `REPORT.md`",
        "- `execution-log.md`",
    ]
    return "\n".join(lines) + "\n"


def write_clean_control(
    *,
    suite_path: Path,
    output_root: Path,
    run_id: str,
    task_ids: list[str],
    allow_existing: bool = False,
) -> dict[str, Any]:
    suite_path = suite_path.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    clean_root = output_root / run_id
    if clean_root.exists() and any(clean_root.iterdir()) and not allow_existing:
        raise SystemExit(f"clean folder already exists and is not empty: {clean_root}")
    clean_root.mkdir(parents=True, exist_ok=True)
    suite = common.load_suite(suite_path)
    tasks = task_rows(suite, task_ids)
    input_dir = clean_root / "input"
    output_dir = clean_root / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    request_path = input_dir / "direct-request.md"
    suite_path_local = input_dir / "suite-facts.json"
    result_path = output_dir / "direct-result.md"
    structure_path = output_dir / "structure.json"
    ticket_path = clean_root / "ticket-info.md"
    plan_path = clean_root / "plan.md"
    report_path = clean_root / "REPORT.md"
    execution_log_path = clean_root / "execution-log.md"

    common.write_text(request_path, render_request(suite, tasks))
    common.write_json(
        suite_path_local,
        {
            "suite_id": suite.get("suite_id") or suite.get("suite") or suite_path.stem,
            "tasks": tasks,
            "validators": common.as_string_list(suite.get("validators"), "validators"),
            "output_contract": suite.get("output_contract", {}),
        },
    )
    common.write_text(result_path, render_result(tasks))
    common.write_text(ticket_path, render_ticket_info(tasks))
    common.write_text(plan_path, render_plan(tasks))
    common.write_text(report_path, render_report(tasks))
    common.write_text(execution_log_path, render_execution_log(tasks))
    common.write_json(
        structure_path,
        {
            "schema_version": 1,
            "workflow": "none",
            "skills": "none",
            "task_ids": [task["id"] for task in tasks],
            "output_contract": "json files bundle",
            "quality_claim": "not measured",
        },
    )

    input_measures = [file_measure(path, "input", clean_root) for path in (request_path, suite_path_local)]
    output_measures = [
        file_measure(path, "output", clean_root)
        for path in (ticket_path, plan_path, report_path, execution_log_path)
    ]
    auxiliary_measures = [file_measure(path, "auxiliary", clean_root) for path in (result_path, structure_path)]
    input_tokens = sum(item["tokens"] for item in input_measures)
    output_tokens = sum(item["tokens"] for item in output_measures)
    token_counter = common.token_count_metadata()
    report = {
        "schema_version": 1,
        "tool": "agent-benchmarking.clean-folder-control",
        "ok": True,
        "status": "measured",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "measurement_scope": MEASUREMENT_SCOPE,
        "clean_folder": str(clean_root),
        "suite_path": str(suite_path),
        "workflow_context_loaded": [],
        "skill_context_loaded": [],
        "routing_context_loaded": [],
        "task_classes": [task["id"] for task in tasks],
        "advisory_token_estimates": {
            "estimates": not token_counter["exact"],
            "exact": token_counter["exact"],
            "method": common.TOKEN_ESTIMATION_METHOD,
            "token_counter": token_counter,
            "input_tokens_estimated": input_tokens,
            "output_tokens_estimated": output_tokens,
            "loaded_context_tokens_estimated": input_tokens,
            "cacheable_static_tokens_estimated": 0,
        },
        "paid_model_tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "total": input_tokens + output_tokens,
        },
        "files": input_measures + output_measures + auxiliary_measures,
        "core_output_docs": [
            "ticket-info.md",
            "plan.md",
            "REPORT.md",
            "execution-log.md",
        ],
        "basis": "Direct clean-folder artifact envelope with no workflow, skill, routing, or run-packet context; not a true live no-harness implementation run.",
    }
    common.write_json(clean_root / "summary.json", report)
    common.write_text(clean_root / "SUMMARY.md", render_markdown(report))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, help="benchmark suite JSON")
    parser.add_argument("--output-root", required=True, help="folder that will receive the clean run folder")
    parser.add_argument("--run-id", required=True, help="clean run folder name")
    parser.add_argument("--task-id", action="append", default=[], help="task id to include; repeatable")
    parser.add_argument("--allow-existing", action="store_true", help="allow writing into an existing folder")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown", dest="output_format")
    return parser


def main(argv: list[str] | None = None) -> int:
    common.require_supported_python()
    args = build_parser().parse_args(argv)
    report = write_clean_control(
        suite_path=Path(args.suite),
        output_root=Path(args.output_root),
        run_id=args.run_id,
        task_ids=[str(item) for item in args.task_id],
        allow_existing=args.allow_existing,
    )
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
