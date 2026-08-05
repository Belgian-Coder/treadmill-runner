#!/usr/bin/env python3
"""Scaffold Markdown-first automation workflow modules from one starter template."""

from __future__ import annotations

import json
import re
import sys
from argparse import Namespace
from datetime import date
from html import escape
from pathlib import Path

sys.dont_write_bytecode = True

import workflow_manager_common as common
import routing_contract
from automation_validation_rules import MANAGE_COMMAND_PATTERN, STATIC_SCRIPT_PATTERN
from validation_support.manifests import module_contract_v3
from validation_support.module_checks import available_manage_commands


def default_root() -> Path:
    return Path(__file__).resolve().parents[4]


def title_from_name(workflow_name: str) -> str:
    return " ".join(part.capitalize() for part in workflow_name.split("-"))


def normalized_skills(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        skill = value.strip()
        if not skill:
            continue
        if not common.SKILL_NAME_PATTERN.match(skill):
            raise SystemExit(f"invalid skill name: {value}")
        if skill not in result:
            result.append(skill)
    return result


def normalized_scripts(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        script = value.strip()
        if script and script not in result:
            result.append(script)
    return result


def accepted_skill_names(root: Path) -> set[str]:
    return {path.name for path in common.discover_skill_dirs(root)}


def workflow_terms(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2 and token not in {"and", "for", "the", "when", "with", "workflow"}
    }


def existing_workflow_summaries(root: Path) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    automations_root = root / "automations"
    if not automations_root.exists():
        return result
    for module_dir in sorted(automations_root.iterdir(), key=lambda item: item.name.lower()):
        if not module_dir.is_dir():
            continue
        summary = ""
        manifest_path = module_dir / "module.json"
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
            if isinstance(data, dict):
                summary = str(data.get("summary") or "")
        if not summary:
            start = common.workflow_start_path(module_dir)
            if start.exists():
                summary = start.read_text(encoding="utf-8", errors="replace")[:600]
        result.append((module_dir.name, summary))
    return result


def find_overlapping_workflows(root: Path, workflow_name: str, summary: str) -> list[str]:
    wanted = workflow_terms(f"{workflow_name} {summary}")
    if not wanted:
        return []
    overlaps: list[str] = []
    for existing_name, existing_summary in existing_workflow_summaries(root):
        existing = workflow_terms(f"{existing_name} {existing_summary}")
        if not existing:
            continue
        shared = wanted & existing
        score = len(shared) / max(1, min(len(wanted), len(existing)))
        name_shared = workflow_terms(workflow_name) & workflow_terms(existing_name)
        name_score = len(name_shared) / max(
            1, min(len(workflow_terms(workflow_name)), len(workflow_terms(existing_name)))
        )
        if (
            score >= 0.35
            or name_score >= 0.5
            or workflow_name in existing_name
            or existing_name in workflow_name
        ):
            overlaps.append(existing_name)
    return overlaps


def validate_related_skills(root: Path, related_skills: list[str]) -> None:
    known_skills = accepted_skill_names(root)
    missing = [skill for skill in related_skills if skill not in known_skills]
    if missing:
        raise SystemExit(
            "unknown skill for --uses-skill: "
            + ", ".join(missing)
            + ". Add the skill under .agents/skills first or omit it."
        )


def validate_script_entries(root: Path, script_entries: list[str]) -> None:
    manage_commands = available_manage_commands(root)
    errors: list[str] = []
    for entry in script_entries:
        manage_matches = list(MANAGE_COMMAND_PATTERN.finditer(entry))
        for match in manage_matches:
            command_name = match.group("command")
            if command_name not in manage_commands:
                errors.append(f"unknown .agents/manage.py command: {command_name}")
        script_matches = [
            match
            for match in STATIC_SCRIPT_PATTERN.finditer(entry)
            if match.group("path").replace("\\", "/") != ".agents/manage.py"
        ]
        if not manage_matches and not script_matches:
            errors.append(f"not a manage command or Python script path: {entry}")
            continue
        for match in script_matches:
            path_text = match.group("path")
            script_path = root / path_text.replace("\\", "/")
            if script_path.suffix.lower() != ".py":
                errors.append(f"script entry must point to a Python file: {entry}")
            elif not script_path.exists():
                errors.append(f"script path does not exist: {path_text}")
    if errors:
        raise SystemExit("invalid --uses-script value(s): " + "; ".join(errors))


def bullet_lines(values: list[str], code: bool = False) -> str:
    if not values:
        return "- none"
    if code:
        return "\n".join(f"- `{value}`" for value in values)
    return "\n".join(f"- {value}" for value in values)


def default_worker_profiles() -> dict[str, object]:
    return {
        "schema_version": 1,
        "extends": "portable-default",
        "mode": "auto-when-supported",
        "max_parallel_workers": 1,
        "delegation": module_contract_v3.default_delegation_contract(),
        "phase_assignments": {
            "intake": "evidence-mini",
            "execute": "general-medium",
            "record": "handoff-mini",
        },
        "task_assignments": {},
    }


def workflow_base_commands(workflow_name: str) -> list[str]:
    return [
        "python -B .agents/manage.py startup-context --summary --compact --format json",
        "python -B .agents/manage.py fresh-agent-packet --summary --compact --format json",
        "python -B .agents/manage.py next-action --summary --compact --format json",
        "python -B .agents/manage.py context-use-check --summary --compact --format json",
        "python -B .agents/manage.py changed-context --summary --compact --format json",
        "python -B .agents/manage.py review-loop --max-units 20 --max-estimated-tokens 8000 --max-elapsed-ms 180000 --summary --compact --format json",
        "python -B .agents/manage.py review-autopilot --dry-run --summary --compact --format json",
        "python -B .agents/manage.py finish --summary --compact --format json",
        "python -B .agents/manage.py check-changed --summary --compact --format json",
        "python -B .agents/manage.py context-cost-benchmark --summary --compact --format json",
        "python -B .agents/manage.py validate-automations --name " + workflow_name,
        "python -B .agents/manage.py review " + workflow_name + " --plan",
        "python -B .agents/manage.py workflow template resolve --name "
        + workflow_name
        + " --template plan.md",
        "python -B .agents/manage.py workflow template resolve --name "
        + workflow_name
        + " --template plan.md --profile lean",
        "python -B .agents/manage.py workflow template lint --name " + workflow_name,
        "python -B .agents/manage.py workflow metadata inspect --name " + workflow_name + " --format json",
        "python -B .agents/manage.py workflow branch-policy",
        "python -B .agents/manage.py workflow context --name " + workflow_name + " --run-id <run-id> --write",
        "python -B .agents/manage.py workflow checkpoint --name " + workflow_name + " --run-id <run-id> --write",
        "python -B .agents/manage.py workflow context-evidence --name "
        + workflow_name
        + " --run-id <run-id> --event start --write",
        "python -B .agents/manage.py workflow hooks --name " + workflow_name + " --format json",
        "python -B .agents/manage.py eval-workflow --name "
        + workflow_name
        + " --suite automations/"
        + workflow_name
        + "/suites/workflow-evals.json",
        "python -B .agents/manage.py workflow scorecard --name " + workflow_name + " --format json",
        "python -B .agents/manage.py workflow scorecard --name "
        + workflow_name
        + " --no-lifecycle --summary --compact --format json",
        "python -B .agents/manage.py workflow smoke --name " + workflow_name + " --format json",
    ]


def combined_commands(workflow_name: str, script_entries: list[str]) -> list[str]:
    commands: list[str] = []
    for command in [*workflow_base_commands(workflow_name), *script_entries]:
        if command not in commands:
            commands.append(command)
    return commands


def typed_command(command_text: str, workflow_name: str) -> dict[str, object]:
    argv = module_contract_v3.lexical_argv_from_text(command_text)
    command = {
        "id": module_contract_v3.command_id_for_argv(argv),
        "argv": argv,
        "timeout_seconds": 300,
        "working_directory": "repository",
        "effects": [],
    }
    command["effects"] = module_contract_v3.infer_command_effects(
        command,
        {"id": workflow_name},
    )
    return command


def typed_commands(workflow_name: str, script_entries: list[str]) -> list[dict[str, object]]:
    return [
        typed_command(command_text, workflow_name)
        for command_text in combined_commands(workflow_name, script_entries)
    ]


def workflow_metadata(workflow_name: str) -> dict[str, object]:
    return {
        "updated": date.today().isoformat(),
        "input_schema": {
            "properties": {
                "approval_required": {
                    "type": "boolean",
                    "description": "Whether execution must wait for explicit human approval.",
                },
                "project_root": {
                    "type": "path",
                    "description": "Target project root when the workflow affects a consumer repo.",
                },
                "request": {
                    "type": "string",
                    "description": "Concrete request or ticket summary that started the workflow run.",
                },
                "run_id": {
                    "type": "string",
                    "description": "Workflow run folder name under runs/.",
                },
            },
            "required": ["request", "run_id"],
        },
        "gates": [
            {
                "id": "clarification",
                "type": "clarification",
                "summary": "Ambiguities are resolved or explicitly skipped before planning.",
                "evidence": "Clarification Decisions",
                "required": True,
            },
            {
                "id": "requirements-quality",
                "type": "quality",
                "summary": "Inputs are clear, measurable, and independently testable enough for execution.",
                "evidence": "Requirements Quality Checklist",
                "required": True,
            },
            {
                "id": "approval",
                "type": "approval",
                "summary": "Implementation or target-project writes wait for explicit approval when required.",
                "evidence": "Workflow Inputs And Gates",
                "required": True,
            },
            {
                "id": "validation",
                "type": "validation",
                "summary": "Declared validation evidence supports completion claims.",
                "evidence": "REPORT.md and run.json validation entries",
                "required": True,
            },
        ],
        "template_layers": module_contract_v3.conventional_template_layers(
            workflow_name
        ),
        "branch_policy": {
            "pattern": "^(feature|fix|docs|chore|release)/[a-z0-9][a-z0-9._-]*$",
        },
    }


def default_routing_terms(workflow_name: str, summary: str) -> list[str]:
    terms: list[str] = []
    for source in (workflow_name, summary):
        for token in routing_contract.TOKEN_RE.findall(source.lower()):
            for component in routing_contract.SEPARATOR_RE.split(token):
                if len(component) <= 2 or component in routing_contract.STOP_WORDS:
                    continue
                if component not in terms:
                    terms.append(component)
                    if routing_contract.routing_score_capacity(terms) >= 2:
                        return terms
    return terms


def manifest(
    workflow_name: str,
    summary: str,
    related_skills: list[str],
    script_entries: list[str],
) -> dict[str, object]:
    commands = typed_commands(workflow_name, script_entries)
    strict_read_only_commands = [
        str(command["id"])
        for command in commands
        if command["effects"] == []
        and "--no-lifecycle" in command["argv"]
        and any(
            command["argv"][index : index + 2] == ["workflow", "scorecard"]
            for index in range(len(command["argv"]) - 1)
        )
    ]
    routing_terms = default_routing_terms(workflow_name, summary)
    activation_terms = [
        workflow_name,
        *[
            term
            for term in routing_terms
            if routing_contract.term_components(term) - routing_contract.GENERIC_SCORE_TERMS
        ][:2],
    ]
    if not routing_contract.has_non_generic_activation(activation_terms):
        raise ValueError(
            "workflow scaffold activation terms must include a non-generic routing concept"
        )
    routing_issues = routing_contract.routing_reachability_issues(
        routing_terms,
        threshold=2,
        winner_margin=1,
    )
    if routing_issues:
        raise ValueError("workflow scaffold routing is unreachable: " + "; ".join(routing_issues))
    return {
        "schema_version": 3,
        "kind": "workflow",
        "id": workflow_name,
        "version": "1.0.0",
        "summary": summary,
        "routing": {
            "terms": routing_terms,
            "activation_terms": activation_terms,
            "threshold": 2,
            "winner_margin": 1,
        },
        "owners": ["engineering"],
        "phases": [
            {"id": "intake", "summary": "Collect request and required context."},
            {"id": "execute", "summary": "Run the declared workflow steps."},
            {"id": "record", "summary": "Record results, skipped checks, and next steps."},
        ],
        "inputs": [
            "WORKFLOW.md",
            "instructions.md",
            "module.json",
            "suites/workflow-evals.json",
        ],
        "outputs": [
            "runs/<run-id>/run.json",
            "runs/<run-id>/REPORT.md",
            "runs/<run-id>/execution-log.md",
            "runs/<run-id>/artifacts/context/context-packet.json",
            "runs/<run-id>/artifacts/documentation/documentation-delta.json",
            "runs/<run-id>/artifacts/documentation/documentation-delta.md",
            "runs/<run-id>/validation/context-evidence-start.json",
            "runs/<run-id>/validation/context-evidence-resume.json",
            "runs/<run-id>/validation/context-evidence-finish.json",
        ],
        "worker_profiles": default_worker_profiles(),
        "context": module_contract_v3.conventional_context(workflow_name),
        "commands": commands,
        "related_modules": related_skills or ["workflow-manager"],
        "metadata_path": "metadata/workflow-metadata.json",
        "validation": [
            "python -B .agents/manage.py validate-automations --name " + workflow_name,
            "python -B .agents/manage.py eval-workflow --name "
            + workflow_name
            + " --suite automations/"
            + workflow_name
            + "/suites/workflow-evals.json",
            "python -B .agents/manage.py workflow scorecard --name " + workflow_name + " --format json",
        ],
        "risk": {
            "credentials": False,
            "destructive": False,
            "generated_settings": False,
            "installs": False,
            "network": False,
            "production_writes": False,
            "uploads": False,
            "profile": "local-write",
        },
        "external_access": {
            "source_systems": [],
            "credential_expectations": "none",
            "data_copied_locally": [],
            "attachments_retrieved": False,
        },
        "local_ai": {
            "use_cases": [
                "validation-triage",
                "changed-files-summary",
                "handoff-draft",
            ]
        },
        "context_evidence": {
            "required": True,
            "start_queries": [
                {
                    "id": "workflow-contract",
                    "question": "What instructions, phases, validation gates, evidence files, and approval rules define this workflow?",
                    "scope": "repo",
                    "required": True,
                    "fallback_paths": [
                        f"automations/{workflow_name}/WORKFLOW.md",
                        f"automations/{workflow_name}/module.json",
                        f"automations/{workflow_name}/instructions.md",
                    ],
                }
            ],
            "resume_queries": [
                {
                    "id": "run-state",
                    "question": "What is the current run state, latest report, blockers, validation evidence, and next action?",
                    "scope": "workflow-runs",
                    "required": True,
                    "fallback_paths": [
                        f"automations/{workflow_name}/runs/<run-id>/run.json",
                        f"automations/{workflow_name}/runs/<run-id>/REPORT.md",
                    ],
                }
            ],
            "finish_queries": [
                {
                    "id": "finish-evidence",
                    "question": "What evidence, validation status, skipped checks, blockers, unsupported claims, and handoff files must be complete before finishing?",
                    "scope": "repo",
                    "required": True,
                    "fallback_paths": [
                        f"automations/{workflow_name}/WORKFLOW.md",
                        f"automations/{workflow_name}/module.json",
                        f"automations/{workflow_name}/instructions.md",
                    ],
                }
            ],
        },
        "strict_read_only_commands": strict_read_only_commands,
        "extensions": {},
    }


def render_files(
    workflow_name: str,
    summary: str,
    related_skills: list[str],
    script_entries: list[str],
) -> dict[str, str]:
    title = title_from_name(workflow_name)
    workflow_md = f"""# {title}

{summary}

## Start

1. Run or read `python -B .agents/manage.py fresh-agent-packet --summary --compact --format json`; load its source-orientation file before broad source reads.
2. Read `docs/project/project-context.md` and `automations/navigation/artifacts/maps/HANDOFF.md` when present; run `python -B .agents/manage.py setup` first if they are missing.
3. Read `automations/routing.md`, then `module.json` for the canonical contract.
4. Read this `WORKFLOW.md` for the human entrypoint.
5. Read `instructions.md` only when phase details are needed.
6. Create or select `runs/<run-id>/` and keep `run.json` current.
7. Use `workflow resume` and the returned context packet before reopening raw workflow files.

Raw navigation JSON is tool-only. Use `status --fast`, `startup-context`, `context-use-check`, `changed-context`, `review-loop`, `review-autopilot`, and `finish` for compact routing, context-use proof, review, validation, and final-claim evidence instead of broad source or raw diff reads. When compact packets name an affected owner capsule, read only that capsule after `HANDOFF.md`.

## Evidence And Decisions

- Record decisions in `run.json` with the reason and evidence path.
- Record command output as evidence handles, not pasted logs.
- Keep skipped, blocked, failed, and unsupported claims explicit.
- Keep out-of-scope work explicit when the run excludes requested or related work.
- Record documentation impact in `run.json.documentation`; `workflow context --write` emits a documentation delta.

## Deterministic Hooks

State-writing workflow commands trigger declared workflow and global hooks. Inspect resolved hooks before the first run with:

```shell
python -B .agents/manage.py workflow hooks --name {workflow_name} --format json
```

```shell
python -B .agents/manage.py workflow hooks --all --check --format json
```

## Required Context Evidence

Workflow start, resume, and finish commands write `validation/context-evidence-<event>.json` and `.md`. The packet records bounded deterministic evidence before planning or implementation can proceed.

Lifecycle start, resume, and finish commands also write `artifacts/checkpoint/checkpoint.json` and `.md`. Use `workflow checkpoint --write` for a manual compact snapshot after large state changes.

Local AI is advisory only. Use it for validation triage, changed-file summaries, and handoff drafts when available; correctness, completion, and merge readiness come from deterministic commands and recorded evidence.

```shell
python -B .agents/manage.py workflow context-evidence --name {workflow_name} --run-id <run-id> --event start --write
```

## Process Diagram

[![Process Diagram](diagrams/workflow-process-diagram.svg)](diagrams/workflow-process-diagram.svg)

Source: [Mermaid](diagrams/workflow-process-diagram.mmd)

## Connection Diagram

[![Connection Diagram](diagrams/workflow-connection-diagram.svg)](diagrams/workflow-connection-diagram.svg)

Source: [Mermaid](diagrams/workflow-connection-diagram.mmd)

## Example Prompts

- Start: "Start `{workflow_name}`. Use `HANDOFF.md` when present, then read `automations/routing.md`, `module.json`, and `WORKFLOW.md` before creating the workflow run packet."
- Fresh agent: "Start from `fresh-agent-packet --summary --compact --format json`, load the source-orientation file it names, then follow `next_command`."
- Resume: "Resume `{workflow_name}` from the latest run. Load `run.json` first, then the required context it names."
- Handoff: "Prepare a handoff for `{workflow_name}` by updating `run.json` with exact required context and next action."
- Diagram: "Add or update Mermaid diagrams for the current flow or structure, validate them, and record the evidence path in `run.json`."
- Finish: "Finish `{workflow_name}` by checking `run.json`, `REPORT.md`, skipped/blocked/failed checks, unsupported claims, validation status, `context-use-check --summary --compact --format json`, and `finish --summary --compact --format json`."

## Execution Model

Use `module.json.worker_profiles` as portable phase execution guidance for Codex, GitHub Copilot, Claude Code, and fallback hosts. The current phase resolves independently to a semantic profile, a model-provider prompt overlay, and an attested host-surface adapter. Run sequentially by default. Enable native workers only when the host attests subagents, complete thread and usage evidence, and isolated worker runtime; otherwise use direct tools with the active model and record that fallback. Use declared Python scripts for repeatable validation and analyzer work. Workflow correctness must not depend on worker spawning.
"""
    instructions = f"""# {title} Instructions

## Always Load

- Keep `run.json` as the canonical run state and update it at every phase boundary.
- Start clean-context work with `fresh-agent-packet --summary --compact --format json`; do not load raw navigation JSON.
- Put workflow-wide recurring rules in this section; `workflow context` copies it into resume packets.
- Record command output as evidence paths or compact summaries, not pasted logs.
- Treat local AI output as advisory triage only; deterministic commands and evidence decide completion.

## Stop Rules

- Stop when required approval, required context, or validation evidence is missing.
- Record the blocker, owner decision needed, and next action before ending the turn.

## Completion Contract

- Final reports name changed paths, commands, generated artifacts, validation, skipped/blocked/failed checks, remaining risks, and next action.
- Unsupported claims must be empty or explicitly called out with evidence gaps.

## Phase: Intake

- [ ] Read: `WORKFLOW.md` and `module.json`.
  Do: identify the concrete request, related modules, expected outputs, risk, and required context-evidence packet.
  Write: update `runs/<run-id>/run.json` with loaded context, decisions, skipped checks, blocked checks, failed checks, command history, evidence paths, and next action.
  Decision: record the selected scope, out-of-scope work, and why this workflow is the right owner.
  Evidence: link the request source, intake file, and `validation/context-evidence-start.json`.
  Done when: the run has one clear next action.
  If blocked: record the blocker in `run.json` and `REPORT.md`.

## Phase: Execute

- [ ] Read: only the phase details and source files needed for the current action.
  Do: run declared commands or make the smallest workflow-owned change.
  Write: record command results, evidence paths, and decisions in `run.json`.
  Decision: record material tradeoffs, skipped work, and out-of-scope choices.
  Evidence: link generated validation, artifacts, or changed files.
  Done when: outputs are present or an explicit blocked/skipped/failed reason is recorded.
  If blocked: preserve the failing command and first failing fact.

## Phase: Record

- [ ] Read: `run.json`, `REPORT.md`, and validation output.
  Do: verify the evidence supports completion claims.
  Write: final completed/skipped/blocked/failed/validation status in `REPORT.md` and `run.json`.
  Decision: record whether the run is complete, blocked, or completed with findings.
  Evidence: link final validation and generated context packet paths.
  Done when: the next action is explicit and unsupported claims are empty.
  If blocked: leave a resumable next action in `run.json`.
"""
    plan_template = f"""# {title} Plan

## Clarification Decisions

| Ambiguity | Decision | Owner | Evidence | Status |
|---|---|---|---|---|
| No clarification needed | Replace when ambiguity exists. | agent | request | pending |

## Workflow Inputs And Gates

| Input Or Gate | Required | Evidence | Status |
|---|---|---|---|
| Request and run id | yes | `run.json` | pending |
| Approval before target writes | yes when required | owner decision | pending |

## Requirements Quality Checklist

| Check | Evidence | Status |
|---|---|---|
| Clear expected outcome | request and plan | pending |
| Measurable validation path | declared commands | pending |
| Edge cases or explicit none | plan notes | pending |

## Cross-Artifact Coverage Analysis

| Requirement Or Decision | Planned Work | Validation | Status |
|---|---|---|---|
| Primary request | implementation or workflow-owned output | declared validation | pending |

## Principles And Complexity Gate

| Decision | Reason | Simpler Alternative Rejected | Evidence |
|---|---|---|---|
| Smallest correct workflow path | Keep work in owning module. | Broad refactor | routing and plan |

## Template And Extension Layering

| Layer | Selection | Reason | Status |
|---|---|---|---|
| project override | none by default | use only for consumer-specific changes | pending |
| workflow template | `templates/plan.md` | module-owned default | pending |

## Task Plan

| Task | Writes | Validation | Status |
|---|---|---|---|
| Prepare run evidence | `runs/<run-id>/` | validate workflow state | pending |
"""
    lean_plan_template = f"""# {title} Lean Plan

## Clarification Decisions

| Ambiguity | Decision | Owner | Evidence | Status |
|---|---|---|---|---|
| No clarification needed | Replace when ambiguity exists. | agent | request | pending |

## Workflow Inputs And Gates

| Input Or Gate | Required | Evidence | Status |
|---|---|---|---|
| Request, run id, approval state | yes | `run.json` | pending |

## Requirements Quality Checklist

| Check | Evidence | Status |
|---|---|---|
| Clear outcome and validation | request plus commands | pending |

## Cross-Artifact Coverage Analysis

| Requirement Or Decision | Planned Work | Validation | Status |
|---|---|---|---|
| Primary request | task row | declared check | pending |

## Principles And Complexity Gate

| Decision | Reason | Simpler Alternative Rejected | Evidence |
|---|---|---|---|
| Lean path | Small scoped request. | Full workflow plan | request |

## Template And Extension Layering

| Layer | Selection | Reason | Status |
|---|---|---|---|
| workflow lean template | `templates/lean-plan.md` | compact run | pending |

## Task Plan

| Task | Writes | Validation | Status |
|---|---|---|---|
| Execute scoped request | declared paths | declared checks | pending |
"""
    process_diagram = """graph TD;
  request["Request"] --> route["Read routing"];
  route --> start["Start or resume"];
  start --> execute["Execute phase"];
  execute --> validate["Validate"];
  validate --> record["Record evidence"];
"""
    connection_diagram = f"""graph TD;
  workflow["Workflow"] --> routing["automations/routing.md"];
  workflow --> contract["module.json"];
  workflow --> guide["entry docs"];
  workflow --> evidence["run evidence"];
  workflow --> validation["validation gates"];
  workflow --> localai["local AI summaries"];
  validation --> generated["generated indexes"];
"""
    eval_suite = {
        "schema_version": 1,
        "workflow_name": workflow_name,
        "evals": [
            {
                "id": f"{workflow_name}-validates",
                "name": f"{title} validates and keeps required entry files",
                "assertions": [
                    {"type": "validation_ok"},
                    {"type": "file_exists", "path": "WORKFLOW.md"},
                    {"type": "file_exists", "path": "instructions.md"},
                    {"type": "start_contains", "text": "## Example Prompts"},
                    {"type": "start_contains", "text": "## Process Diagram"},
                    {"type": "start_contains", "text": "## Connection Diagram"},
                    {"type": "start_contains", "text": "HANDOFF.md"},
                    {"type": "start_contains", "text": "fresh-agent-packet"},
                ],
            },
            {
                "id": f"{workflow_name}-contract",
                "name": f"{title} declares lifecycle evidence and local summaries",
                "assertions": [
                    {"type": "contract_declares_phase", "phase": "intake"},
                    {"type": "contract_declares_phase", "phase": "execute"},
                    {"type": "contract_declares_phase", "phase": "record"},
                    {"type": "contract_declares_output", "path": "runs/<run-id>/run.json"},
                    {
                        "type": "contract_declares_output",
                        "path": "runs/<run-id>/artifacts/context/context-packet.json",
                    },
                    {
                        "type": "contract_local_ai_use_cases",
                        "use_cases": [
                            "validation-triage",
                            "changed-files-summary",
                            "handoff-draft",
                        ],
                    },
                ],
            },
            {
                "id": f"{workflow_name}-lifecycle-smoke",
                "name": f"{title} lifecycle smoke records resumable evidence",
                "assertions": [{"type": "workflow_lifecycle_smoke_ok"}],
            },
        ],
    }
    return {
        "WORKFLOW.md": workflow_md,
        "instructions.md": instructions,
        "diagrams/workflow-process-diagram.mmd": process_diagram,
        "diagrams/workflow-process-diagram.svg": simple_svg(
            "Process",
            ["Request", "Route", "Start/Resume", "Execute", "Validate", "Record"],
        ),
        "diagrams/workflow-connection-diagram.mmd": connection_diagram,
        "diagrams/workflow-connection-diagram.svg": simple_svg(
            "Connections",
            ["Workflow", "Contract", "Guide", "Run Evidence", "Validation", "Local AI"],
        ),
        "templates/plan.md": plan_template,
        "templates/lean-plan.md": lean_plan_template,
        "metadata/workflow-metadata.json": json.dumps(workflow_metadata(workflow_name), indent=2, sort_keys=True),
        "suites/workflow-evals.json": json.dumps(eval_suite, indent=2, sort_keys=True),
    }


def simple_svg(title: str, labels: list[str]) -> str:
    width = 920
    height = 140
    vertical_padding = 24
    padded_height = height + (vertical_padding * 2)
    boxes: list[str] = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="920" '
        f'height="{padded_height}" viewBox="0 -{vertical_padding} 920 {padded_height}" '
        'style="max-width: 920px; background-color: transparent;" '
        'preserveAspectRatio="xMidYMid meet" data-mermaid-vertical-padding="24" role="img">',
        f"<title>{escape(title)}</title>",
    ]
    box_width = 120
    gap = 24
    y = 58
    for index, label in enumerate(labels):
        x = 24 + index * (box_width + gap)
        boxes.append(f'<rect x="{x}" y="{y}" width="{box_width}" height="42" rx="6" fill="#1f2937" stroke="#d1d5db"/>')
        boxes.append(
            f'<text x="{x + box_width / 2}" y="{y + 26}" text-anchor="middle" '
            'font-family="Arial, sans-serif" font-size="12" fill="#f9fafb">'
            f"{escape(label)}</text>"
        )
        if index < len(labels) - 1:
            x1 = x + box_width
            x2 = x + box_width + gap - 6
            boxes.append(f'<line x1="{x1}" y1="{y + 21}" x2="{x2}" y2="{y + 21}" stroke="#d1d5db"/>')
            boxes.append(
                f'<polygon points="{x2},{y + 21} {x2 - 7},{y + 16} {x2 - 7},{y + 26}" fill="#d1d5db"/>'
            )
    boxes.append("</svg>")
    return "\n".join(boxes)


def write_file(path: Path, content: str, force: bool, written: list[Path]) -> None:
    if path.exists() and not force:
        raise SystemExit(f"{path} already exists; use --force to overwrite scaffold files")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8", newline="\n")
    written.append(path)


def create_workflow(args: Namespace) -> list[Path]:
    root = Path(args.root).expanduser().resolve() if args.root else default_root()
    workflow_name = args.workflow_name.strip()
    if not common.SKILL_NAME_PATTERN.match(workflow_name):
        raise SystemExit(
            "workflow name must use lowercase letters, digits, and hyphens, "
            "and start with a letter or digit"
        )

    related_skills = normalized_skills(getattr(args, "uses_skill", []) or [])
    script_entries = normalized_scripts(getattr(args, "uses_script", []) or [])
    validate_related_skills(root, related_skills)
    validate_script_entries(root, script_entries)
    target = root / "automations" / workflow_name
    if target.exists() and not args.force:
        raise SystemExit(f"workflow already exists: {target}; use --force to overwrite scaffold files")
    if not target.exists() and not args.force and not bool(getattr(args, "skip_overlap_check", False)):
        overlaps = find_overlapping_workflows(root, workflow_name, args.summary.strip())
        if overlaps:
            raise SystemExit(
                "possible workflow overlap with "
                + ", ".join(overlaps[:5])
                + "; ask whether to extend an existing workflow or choose a distinct new workflow."
            )

    try:
        manifest_data = manifest(workflow_name, args.summary.strip(), related_skills, script_entries)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    written: list[Path] = []
    for relative_path, content in render_files(
        workflow_name,
        args.summary.strip(),
        related_skills,
        script_entries,
    ).items():
        write_file(target / relative_path, content, args.force, written)

    content = json.dumps(
        manifest_data,
        indent=2,
        sort_keys=True,
    )
    write_file(target / "module.json", content, args.force, written)

    return written
