"""Intent-first workflow builder helpers.

The builder keeps the existing workflow-manager contracts authoritative while
giving users a plain-language front door. Propose and adjust are read-only;
create writes only through create_workflow.py.
"""

from __future__ import annotations

import json
import re
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import create_workflow
import workflow_manager_common as common
from validation_support import manifests as contract_manifests


STOPWORDS = {
    "about",
    "after",
    "again",
    "agent",
    "agents",
    "build",
    "can",
    "create",
    "easy",
    "for",
    "from",
    "help",
    "into",
    "make",
    "need",
    "new",
    "our",
    "please",
    "the",
    "this",
    "that",
    "their",
    "through",
    "to",
    "use",
    "user",
    "users",
    "want",
    "with",
    "workflow",
    "workflows",
}


OVERLAP_GENERIC_TERMS = {
    "change",
    "changes",
    "check",
    "checks",
    "evidence",
    "plan",
    "record",
    "release",
    "report",
    "review",
    "run",
    "runs",
    "validate",
    "validation",
}


INTAKE_QUESTIONS = [
    "When should this workflow run?",
    "What input does it need?",
    "What should it produce?",
    "What proof means done?",
    "What is risky, external, or needs approval?",
]


SYSTEM_DERIVED_ARTIFACTS = [
    "module.json contract",
    "WORKFLOW.md prompts",
    "instructions.md phase steps",
    "process and connection Mermaid diagrams",
    "plan templates",
    "workflow eval suite",
    "worker profile guidance",
    "validation command list",
]


ACTIVE_WORKFLOW_FILES = [
    "WORKFLOW.md",
    "instructions.md",
    "module.json",
    "metadata/workflow-metadata.json",
    "diagrams/workflow-process-diagram.mmd",
    "diagrams/workflow-process-diagram.svg",
    "diagrams/workflow-connection-diagram.mmd",
    "diagrams/workflow-connection-diagram.svg",
    "templates/plan.md",
    "templates/lean-plan.md",
    "suites/workflow-evals.json",
]


FOCUSED_VALIDATION_TEMPLATE = [
    "python -B .agents/manage.py validate-automations --name {workflow} --strict-phase-quality",
    "python -B .agents/manage.py eval-workflow --name {workflow} --suite automations/{workflow}/suites/workflow-evals.json",
    "python -B .agents/manage.py workflow scorecard --name {workflow} --format json",
    "python -B .agents/manage.py workflow template lint --name {workflow}",
    "python -B .agents/manage.py workflow metadata inspect --name {workflow} --format json",
]


BASE_OUTPUTS = [
    "runs/<run-id>/run.json",
    "runs/<run-id>/REPORT.md",
    "runs/<run-id>/execution-log.md",
    "runs/<run-id>/artifacts/context/context-packet.json",
    "runs/<run-id>/artifacts/documentation/documentation-delta.json",
    "runs/<run-id>/artifacts/documentation/documentation-delta.md",
    "runs/<run-id>/validation/context-evidence-start.json",
    "runs/<run-id>/validation/context-evidence-resume.json",
    "runs/<run-id>/validation/context-evidence-finish.json",
]


@dataclass(frozen=True)
class WorkflowRecipe:
    id: str
    title: str
    use_when: str
    request_terms: tuple[str, ...]
    phases: tuple[str, ...]
    outputs: tuple[str, ...]
    validation: tuple[str, ...]
    eval_expectations: tuple[str, ...]
    scorecard_expectations: tuple[str, ...]
    do_not_create_when: tuple[str, ...]
    related_modules: tuple[str, ...] = ("workflow-manager",)
    risk_notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "use_when": self.use_when,
            "phases": list(self.phases),
            "outputs": list(self.outputs),
            "validation": list(self.validation),
            "eval_expectations": list(self.eval_expectations),
            "scorecard_expectations": list(self.scorecard_expectations),
            "do_not_create_when": list(self.do_not_create_when),
            "related_modules": list(self.related_modules),
            "risk_notes": list(self.risk_notes),
        }


RECIPES = [
    WorkflowRecipe(
        id="plan-gated-implementation",
        title="Plan-Gated Implementation",
        use_when="The work changes source, behavior, release output, or target-project files and needs approval before implementation.",
        request_terms=("story", "feature", "implementation", "change", "fix", "approval", "plan"),
        phases=("intake", "planning", "implementation", "validation", "handoff"),
        outputs=(
            "runs/<run-id>/run.json",
            "runs/<run-id>/plan.md",
            "runs/<run-id>/execution-log.md",
            "runs/<run-id>/REPORT.md",
            "runs/<run-id>/artifacts/context/context-packet.json",
        ),
        validation=(
            "validate-automations",
            "eval-workflow",
            "workflow scorecard",
            "workflow smoke",
        ),
        eval_expectations=(
            "validation_ok",
            "workflow_lifecycle_smoke_ok",
            "contract_declares_phase",
            "contract_declares_output",
        ),
        scorecard_expectations=(
            "copyable prompts",
            "linked process and connection diagrams",
            "plan gates",
            "context declarations",
        ),
        do_not_create_when=(
            "A user story, bug, or disciplined-change workflow already owns the request.",
            "The work is a single command or static documentation update.",
        ),
    ),
    WorkflowRecipe(
        id="evidence-only-read-only",
        title="Evidence-Only Read-Only",
        use_when="The workflow gathers, reviews, summarizes, or triages evidence without writing target-project files.",
        request_terms=("review", "triage", "audit", "inspect", "read", "summary", "evidence", "diagnose"),
        phases=("intake", "collect-evidence", "analyze", "report"),
        outputs=(
            "runs/<run-id>/run.json",
            "runs/<run-id>/REPORT.md",
            "runs/<run-id>/validation/",
        ),
        validation=("validate-automations", "eval-workflow", "workflow scorecard"),
        eval_expectations=("validation_ok", "contract_declares_output", "run_evidence_ledger_valid"),
        scorecard_expectations=("prompts", "diagrams", "evals", "context declarations"),
        do_not_create_when=(
            "An existing analytics, review, or diagnostics workflow already records the same evidence.",
            "The result is a one-off note that does not need pause/resume state.",
        ),
        risk_notes=("Keep writes inside the workflow run folder unless the contract declares otherwise.",),
    ),
    WorkflowRecipe(
        id="external-system-intake",
        title="External-System Intake",
        use_when="The workflow imports or reconciles tickets, references, issues, attachments, or other external system records.",
        request_terms=("azure", "ticket", "issue", "import", "candidate", "reference", "attachment", "external"),
        phases=("credential-check", "intake", "normalize", "review", "record"),
        outputs=(
            "runs/<run-id>/run.json",
            "runs/<run-id>/REPORT.md",
            "runs/<run-id>/artifacts/",
            "runs/<run-id>/validation/",
        ),
        validation=("validate-automations", "eval-workflow", "workflow scorecard", "credential-doctor when live credentials are used"),
        eval_expectations=("validation_ok", "contract_declares_output", "contract_declares_related_module"),
        scorecard_expectations=("external access declaration", "evidence paths", "finish criteria"),
        do_not_create_when=(
            "A dedicated intake workflow already exists for the external system.",
            "The task only needs a single credential-doctor or import command.",
        ),
        related_modules=("external-reference-manager", "workflow-manager"),
        risk_notes=("Declare credentials, network access, attachments, and copied data in module.json.",),
    ),
    WorkflowRecipe(
        id="benchmark-evaluation",
        title="Benchmark And Evaluation",
        use_when="The workflow compares models, tools, workflow versions, costs, speed, quality, or regression outcomes.",
        request_terms=("benchmark", "performance", "eval", "evaluate", "compare", "speed", "cost", "quality", "model"),
        phases=("baseline", "run-suite", "compare", "promote-lessons", "report"),
        outputs=(
            "runs/<run-id>/run.json",
            "runs/<run-id>/REPORT.md",
            "runs/<run-id>/artifacts/benchmark/",
            "runs/<run-id>/validation/",
        ),
        validation=("validate-automations", "eval-workflow", "workflow scorecard", "benchmark release-gate when release-facing"),
        eval_expectations=("validation_ok", "contract_declares_command", "contract_declares_output"),
        scorecard_expectations=("benchmark evidence", "skipped live checks recorded", "reusable lessons"),
        do_not_create_when=(
            "agent-benchmarking or local-ai-benchmark-workflow already owns the benchmark class.",
            "The comparison is a one-off local command with no reusable run packet.",
        ),
        related_modules=("agent-benchmarking", "workflow-manager"),
        risk_notes=("Separate provider telemetry from estimated token/cost evidence.",),
    ),
    WorkflowRecipe(
        id="documentation-diagram-review",
        title="Documentation And Diagram Review",
        use_when="The workflow creates, reviews, or validates Markdown, Mermaid, handoff docs, or architecture diagrams.",
        request_terms=("doc", "docs", "diagram", "mermaid", "architecture", "handoff", "wiki", "readme"),
        phases=("intake", "map-docs", "update", "validate-diagrams", "record"),
        outputs=(
            "runs/<run-id>/run.json",
            "runs/<run-id>/REPORT.md",
            "runs/<run-id>/artifacts/documentation/documentation-delta.json",
            "runs/<run-id>/validation/",
        ),
        validation=("validate-automations", "eval-workflow", "workflow scorecard", "Mermaid render or syntax check"),
        eval_expectations=("validation_ok", "file_contains", "contract_declares_output"),
        scorecard_expectations=("linked diagrams", "Mermaid syntax", "documentation delta"),
        do_not_create_when=(
            "diagram-review-workflow or documentation guidance already owns the request.",
            "Only one static Markdown page needs a normal edit.",
        ),
        related_modules=("mermaid-diagrams-azure-devops", "workflow-manager"),
    ),
    WorkflowRecipe(
        id="workflow-maintenance-repair",
        title="Workflow Maintenance And Repair",
        use_when="The workflow fixes skipped, blocked, stale, or weak workflow contracts, templates, evals, routing, or scorecard gaps.",
        request_terms=("workflow", "repair", "doctor", "blocked", "skipped", "scorecard", "template", "routing", "contract", "validation"),
        phases=("diagnose", "patch-contract", "validate", "sync", "report"),
        outputs=(
            "runs/<run-id>/run.json",
            "runs/<run-id>/REPORT.md",
            "workflow-owned source files",
        ),
        validation=("validate-automations", "eval-workflow", "workflow scorecard", "sync-automation-routing --check", "check-additions"),
        eval_expectations=("validation_ok", "repo_command_succeeds", "workflow_lifecycle_smoke_ok"),
        scorecard_expectations=("scorecard 100 or explicit residual risk", "routing sync", "eval coverage"),
        do_not_create_when=(
            "The fix belongs in workflow-manager scripts or docs rather than a new workflow.",
            "The issue is generated-file drift that should be fixed by a sync command.",
        ),
    ),
]


def token_set(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 1 and token not in STOPWORDS
    }


def recipe_by_id(recipe_id: str | None) -> WorkflowRecipe:
    if recipe_id:
        for recipe in RECIPES:
            if recipe.id == recipe_id:
                return recipe
        raise SystemExit(f"unknown workflow recipe: {recipe_id}")
    return RECIPES[0]


def select_recipe(request: str, recipe_id: str | None = None) -> tuple[WorkflowRecipe, list[dict[str, Any]]]:
    if recipe_id:
        recipe = recipe_by_id(recipe_id)
        return recipe, [{"id": recipe.id, "score": 999, "matched_terms": ["explicit"]}]
    request_terms = token_set(request)
    scored: list[dict[str, Any]] = []
    for recipe in RECIPES:
        matches = sorted(request_terms & set(recipe.request_terms))
        score = len(matches)
        if recipe.id == "workflow-maintenance-repair" and "workflow" in request_terms:
            score += 1
        scored.append({"id": recipe.id, "score": score, "matched_terms": matches})
    scored.sort(key=lambda item: (-int(item["score"]), str(item["id"])))
    selected_id = str(scored[0]["id"]) if scored and int(scored[0]["score"]) > 0 else "plan-gated-implementation"
    return recipe_by_id(selected_id), scored


def workflow_name_from_request(request: str, recipe: WorkflowRecipe) -> str:
    terms = [
        token
        for token in re.findall(r"[a-z0-9]+", request.lower())
        if len(token) > 2 and token not in STOPWORDS
    ]
    if not terms:
        terms = recipe.id.split("-")[:3]
    if recipe.id == "benchmark-evaluation" and "benchmark" not in terms:
        terms.append("benchmark")
    if recipe.id == "documentation-diagram-review" and not {"doc", "docs", "diagram"} & set(terms):
        terms.append("docs")
    name = "-".join(terms[:5])
    if not name.endswith("workflow"):
        name = f"{name}-workflow"
    if not common.SKILL_NAME_PATTERN.match(name):
        name = "custom-workflow"
    return name


def existing_workflow_candidates(root: Path, request: str, proposed_name: str) -> list[dict[str, Any]]:
    request_terms = token_set(f"{request} {proposed_name}")
    useful_request_terms = request_terms - OVERLAP_GENERIC_TERMS
    candidates: list[dict[str, Any]] = []
    hints = {
        "bug-ticket-workflow": {"bug", "defect", "regression", "repro", "reproduction"},
        "user-story-workflow": {"story", "feature", "implementation", "acceptance", "criteria"},
        "disciplined-change-workflow": {"large", "change", "discipline", "architecture"},
        "diagram-review-workflow": {"diagram", "mermaid", "wiki"},
        "agent-benchmarking": {"benchmark", "eval", "evaluation", "cost", "quality"},
        "local-ai-benchmark-workflow": {"local", "model", "llama", "gpu", "benchmark"},
        "candidate-import-workflow": {"candidate", "import"},
        "reference-refresh": {"reference", "refresh", "external"},
    }
    for existing_name, summary in create_workflow.existing_workflow_summaries(root):
        existing_terms = token_set(f"{existing_name} {summary}")
        matched = sorted(useful_request_terms & (existing_terms - OVERLAP_GENERIC_TERMS))
        hint_matches = sorted(request_terms & hints.get(existing_name, set()))
        explicit_name_match = proposed_name == existing_name or proposed_name in existing_name or existing_name in proposed_name
        score = len(matched) + (2 * len(hint_matches))
        strong_match = False
        if hint_matches:
            matched = sorted(set(matched) | set(hint_matches))
            strong_match = len(hint_matches) >= 2
        if explicit_name_match:
            score += 3
            strong_match = True
        if score:
            confidence = round(score / max(1, min(len(useful_request_terms or request_terms), len(existing_terms))), 2)
            candidates.append(
                {
                    "workflow": existing_name,
                    "score": score,
                    "confidence": confidence,
                    "strong_match": strong_match,
                    "matched_terms": matched,
                    "summary": summary[:220],
                    "next_command": (
                        "python -B .agents/manage.py workflow adjust --name "
                        f"{existing_name} --from-request {json.dumps(request)} --plan"
                    ),
                }
            )
    candidates.sort(key=lambda item: (-int(item["score"]), -float(item["confidence"]), str(item["workflow"])))
    return candidates[:5]


def validation_commands(workflow_name: str) -> list[str]:
    return [template.format(workflow=workflow_name) for template in FOCUSED_VALIDATION_TEMPLATE]


def decision_fields(
    *,
    action: str,
    selected_workflow: str | None,
    new_workflow: str | None,
    write_mode: str,
    next_command: str,
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "selected_workflow": selected_workflow or "",
        "new_workflow": new_workflow or "",
        "write_mode": write_mode,
        "next_command": next_command,
        "reasons": reasons or [],
    }


def proposed_files(workflow_name: str) -> list[str]:
    return [f"automations/{workflow_name}/{path}" for path in ACTIVE_WORKFLOW_FILES]


def is_one_command_request(request: str) -> bool:
    return bool(re.search(r"\b(one|single)[ -]command\b", request, re.IGNORECASE))


def determine_recommendation(
    request: str,
    candidates: list[dict[str, Any]],
    *,
    target_workflow: str | None = None,
    force_new: bool = False,
) -> tuple[str, str | None, list[str]]:
    if target_workflow:
        return "adjust-existing", target_workflow, []
    if is_one_command_request(request):
        return "do-not-create", None, ["A one-command task should stay as a command or skill, not a workflow."]
    if force_new:
        return "create-new", None, ["Forced new workflow; overlap candidates are advisory."]
    if candidates:
        top = candidates[0]
        if bool(top.get("strong_match")) and (int(top.get("score", 0)) >= 4 or float(top.get("confidence", 0.0)) >= 0.5):
            return (
                "adjust-existing",
                str(top["workflow"]),
                [f"Existing workflow `{top['workflow']}` has overlapping terms: {', '.join(top.get('matched_terms', []))}."],
            )
    return "create-new", None, []


def proposal_report(
    root: Path,
    request: str,
    *,
    workflow_name: str | None = None,
    recipe_id: str | None = None,
    profile: str = "standard",
    target_workflow: str | None = None,
    force_new: bool = False,
    mode: str = "propose",
    compact: bool = False,
) -> dict[str, Any]:
    root = root.expanduser().resolve()
    recipe, recipe_scores = select_recipe(request, recipe_id)
    proposed_name = workflow_name or workflow_name_from_request(request, recipe)
    candidates = existing_workflow_candidates(root, request, proposed_name)
    recommendation, selected_existing, reasons = determine_recommendation(
        request,
        candidates,
        target_workflow=target_workflow,
        force_new=force_new,
    )
    public_proposed_name = proposed_name if recommendation == "create-new" else ""
    new_workflow = proposed_name if recommendation == "create-new" else ""
    selected_workflow = selected_existing or new_workflow
    validation_target = selected_workflow
    next_command = ""
    if recommendation == "adjust-existing" and selected_existing:
        next_command = (
            "python -B .agents/manage.py workflow adjust --name "
            f"{selected_existing} --from-request {json.dumps(request)} --plan"
        )
    elif recommendation == "create-new":
        next_command = (
            "python -B .agents/manage.py workflow create --from-request "
            f"{json.dumps(request)} --name {proposed_name} --recipe {recipe.id} --write"
        )
    else:
        next_command = "Keep this as a skill, command, or normal documentation change."

    report: dict[str, Any] = {
        "schema_version": 1,
        "tool": "workflow-manager.intent-builder",
        "mode": mode,
        "action": recommendation,
        "ok": True,
        "status": "proposed",
        "writes": False,
        "write_mode": "read-only",
        "request": request,
        "profile": profile,
        "recommendation": recommendation,
        "selected_workflow": selected_workflow,
        "new_workflow": new_workflow,
        "target_workflow": selected_workflow,
        "proposed_workflow_name": public_proposed_name,
        "decision": decision_fields(
            action=recommendation,
            selected_workflow=selected_workflow,
            new_workflow=new_workflow,
            write_mode="read-only",
            next_command=next_command,
            reasons=reasons,
        ),
        "recipe": recipe.as_dict(),
        "recipe_candidates": recipe_scores[:4],
        "existing_workflow_candidates": candidates,
        "recommendation_reasons": reasons,
        "user_only_answers": INTAKE_QUESTIONS,
        "system_derives": SYSTEM_DERIVED_ARTIFACTS,
        "proposed_files": proposed_files(validation_target) if validation_target else [],
        "forbidden_direct_edits": [
            "automations/routing.md",
            "automations/registry.json",
            ".agents/routing.md",
            ".agents/registry.json",
        ],
        "validation_commands": validation_commands(validation_target) if validation_target else [],
        "next_command": next_command,
        "skipped": [
            {
                "check": "write",
                "reason": "proposal mode is read-only; pass workflow create --write to scaffold files.",
            }
        ],
    }
    if compact:
        return compact_report(report)
    return report


def recipes_report(*, compact: bool = False) -> dict[str, Any]:
    recipes = [recipe.as_dict() for recipe in RECIPES]
    report: dict[str, Any] = {
        "schema_version": 1,
        "tool": "workflow-manager.intent-builder.recipes",
        "ok": True,
        "status": "ok",
        "writes": False,
        "write_mode": "read-only",
        "action": "list-recipes",
        "recipe_count": len(recipes),
        "recipes": recipes,
        "user_only_answers": INTAKE_QUESTIONS,
        "next_command": "python -B .agents/manage.py workflow propose --from-request \"<plain language request>\" --summary --compact --format json",
    }
    if compact:
        report["recipes"] = [
            {
                "id": recipe["id"],
                "title": recipe["title"],
                "phases": recipe["phases"],
                "validation": recipe["validation"],
                "outputs": recipe["outputs"],
                "do_not_create_when": recipe["do_not_create_when"],
            }
            for recipe in recipes
        ]
    return report


def adjust_plan_report(
    root: Path,
    workflow_name: str,
    request: str,
    *,
    recipe_id: str | None = None,
    profile: str = "standard",
    compact: bool = False,
) -> dict[str, Any]:
    root = root.expanduser().resolve()
    module_dir = root / "automations" / workflow_name
    manifest, manifest_error = common.read_json_file(module_dir / "module.json")
    workflow_exists = module_dir.exists() and isinstance(manifest, dict)
    recipe, recipe_scores = select_recipe(request, recipe_id)
    changed_paths = proposed_files(workflow_name)
    existing_phases: list[str] = []
    existing_commands: list[str] = []
    if isinstance(manifest, dict):
        existing_phases = [
            str(phase.get("id", ""))
            for phase in manifest.get("phases", [])
            if isinstance(phase, dict) and phase.get("id")
        ]
        existing_commands = contract_manifests.command_texts(manifest.get("commands"))
    report: dict[str, Any] = {
        "schema_version": 1,
        "tool": "workflow-manager.intent-builder",
        "mode": "adjust",
        "action": "adjust-existing",
        "ok": workflow_exists,
        "status": "planned" if workflow_exists else "blocked",
        "writes": False,
        "write_mode": "read-only",
        "workflow": workflow_name,
        "workflow_path": f"automations/{workflow_name}",
        "recommendation": "adjust-existing",
        "selected_workflow": workflow_name,
        "new_workflow": "",
        "target_workflow": workflow_name,
        "request": request,
        "profile": profile,
        "recipe": recipe.as_dict(),
        "recipe_candidates": recipe_scores[:4],
        "existing_contract": {
            "phases": existing_phases,
            "command_count": len(existing_commands),
            "has_eval_suite": (module_dir / "suites" / "workflow-evals.json").exists(),
            "has_metadata": (module_dir / "metadata" / "workflow-metadata.json").exists(),
        },
        "patch_plan": [
            {
                "step": "Confirm ownership and overlap",
                "files": ["automations/routing.md", f"automations/{workflow_name}/module.json"],
                "write": False,
            },
            {
                "step": "Patch human workflow entry and phase instructions",
                "files": [
                    f"automations/{workflow_name}/WORKFLOW.md",
                    f"automations/{workflow_name}/instructions.md",
                ],
                "write": True,
            },
            {
                "step": "Patch machine contract, metadata, templates, and eval expectations",
                "files": [
                    f"automations/{workflow_name}/module.json",
                    f"automations/{workflow_name}/metadata/workflow-metadata.json",
                    f"automations/{workflow_name}/templates/plan.md",
                    f"automations/{workflow_name}/suites/workflow-evals.json",
                ],
                "write": True,
            },
            {
                "step": "Refresh workflow diagrams only when the phase or system shape changes",
                "files": [
                    f"automations/{workflow_name}/diagrams/workflow-process-diagram.mmd",
                    f"automations/{workflow_name}/diagrams/workflow-connection-diagram.mmd",
                ],
                "write": True,
            },
        ],
        "changed_paths": changed_paths,
        "forbidden_direct_edits": [
            "automations/routing.md",
            "automations/registry.json",
            ".agents/routing.md",
            ".agents/registry.json",
        ],
        "validation_commands": validation_commands(workflow_name),
        "next_command": f"python -B .agents/manage.py review {workflow_name} --plan",
        "decision": decision_fields(
            action="adjust-existing",
            selected_workflow=workflow_name,
            new_workflow=None,
            write_mode="read-only",
            next_command=f"python -B .agents/manage.py review {workflow_name} --plan",
        ),
        "issues": [] if workflow_exists else [manifest_error or f"workflow not found: {workflow_name}"],
    }
    if compact:
        return compact_report(report)
    return report


def unique_strings(values: list[str] | tuple[str, ...]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def title_from_phase(phase: str) -> str:
    return " ".join(part.capitalize() for part in phase.split("-"))


def profile_for_phase(phase: str) -> str:
    lowered = phase.lower()
    if any(term in lowered for term in ("plan", "assess", "map")):
        return "planning-high"
    if any(term in lowered for term in ("implement", "patch", "update", "sync", "normalize")):
        return "implementation-mini"
    if any(term in lowered for term in ("validate", "test", "compare", "run-suite")):
        return "validation-local"
    if any(term in lowered for term in ("handoff", "report", "record", "promote")):
        return "handoff-mini"
    return "evidence-mini"


def render_recipe_instructions(workflow_name: str, recipe: WorkflowRecipe) -> str:
    lines = [
        f"# {create_workflow.title_from_name(workflow_name)} Instructions",
        "",
        "## Always Load",
        "",
        "- Keep `run.json` as the canonical run state and update it at every phase boundary.",
        "- Start clean-context work with `fresh-agent-packet --summary --compact --format json`; do not load raw navigation JSON.",
        "- Record command output as evidence paths or compact summaries, not pasted logs.",
        "- Treat local AI output as advisory triage only; deterministic commands and evidence decide completion.",
        "",
        "## Stop Rules",
        "",
        "- Stop when required approval, required context, or validation evidence is missing.",
        "- Record the blocker, owner decision needed, and next action before ending the turn.",
        "",
        "## Completion Contract",
        "",
        "- Final reports name changed paths, commands, generated artifacts, validation, skipped/blocked/failed checks, remaining risks, and next action.",
        "- Unsupported claims must be empty or explicitly called out with evidence gaps.",
    ]
    for phase in recipe.phases:
        title = title_from_phase(phase)
        lines.extend(
            [
                "",
                f"## Phase: {phase}",
                "",
                f"- [ ] Read: `WORKFLOW.md`, `module.json`, and evidence relevant to {title.lower()}.",
                f"  Do: complete the {title.lower()} work declared by the `{recipe.id}` recipe.",
                "  Write: update `runs/<run-id>/run.json`, `REPORT.md`, command history, decisions, evidence paths, and next action.",
                "  Decision: record material tradeoffs, skipped work, and out-of-scope choices.",
                "  Evidence: link generated validation, artifacts, changed files, or explicit skipped reasons.",
                "  Done when: outputs are present or an explicit blocked/skipped/failed reason is recorded.",
                "  If blocked: preserve the failing command and first failing fact.",
            ]
        )
    return "\n".join(lines) + "\n"


def render_recipe_plan_template(workflow_name: str, recipe: WorkflowRecipe, profile: str) -> str:
    rows = "\n".join(
        f"| {title_from_phase(phase)} | recipe phase `{phase}` | declared validation | pending |"
        for phase in recipe.phases
    )
    return f"""# {create_workflow.title_from_name(workflow_name)} Plan

## Builder Recipe

| Field | Value |
|---|---|
| Recipe | `{recipe.id}` |
| Profile | `{profile}` |

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
{rows}

## Principles And Complexity Gate

| Decision | Reason | Simpler Alternative Rejected | Evidence |
|---|---|---|---|
| Use `{recipe.id}` recipe | Match the requested workflow shape. | Generic scaffold only | builder proposal |

## Template And Extension Layering

| Layer | Selection | Reason | Status |
|---|---|---|---|
| workflow template | `templates/plan.md` | recipe-backed default | pending |

## Task Plan

| Task | Writes | Validation | Status |
|---|---|---|---|
{rows}
"""


def render_recipe_eval_suite(workflow_name: str, recipe: WorkflowRecipe) -> dict[str, Any]:
    phase_assertions = [{"type": "contract_declares_phase", "phase": phase} for phase in recipe.phases]
    output_assertions = [
        {"type": "contract_declares_output", "path": output}
        for output in unique_strings([*BASE_OUTPUTS, *recipe.outputs])[:8]
    ]
    return {
        "schema_version": 1,
        "workflow_name": workflow_name,
        "evals": [
            {
                "id": f"{workflow_name}-builder-contract",
                "name": "Builder recipe phases and outputs are declared",
                "assertions": [
                    {"type": "validation_ok"},
                    {"type": "start_contains", "text": "## Builder Recipe"},
                    {"type": "instructions_contains", "text": f"## Phase: {recipe.phases[0]}"},
                    *phase_assertions,
                    *output_assertions,
                ],
            },
            {
                "id": f"{workflow_name}-lifecycle-smoke",
                "name": "Lifecycle smoke records resumable evidence",
                "assertions": [{"type": "workflow_lifecycle_smoke_ok"}],
            },
        ],
    }


def apply_recipe_to_scaffold(
    root: Path,
    workflow_name: str,
    request: str,
    recipe: WorkflowRecipe,
    profile: str,
) -> list[Path]:
    module_dir = root / "automations" / workflow_name
    touched: list[Path] = []
    manifest_path = module_dir / "module.json"
    manifest, error = common.read_json_file(manifest_path)
    if error or not isinstance(manifest, dict):
        raise SystemExit(error or f"missing manifest for {workflow_name}")

    manifest["phases"] = [
        {"id": phase, "summary": f"{title_from_phase(phase)} phase for the {recipe.title} recipe."}
        for phase in recipe.phases
    ]
    manifest["outputs"] = unique_strings([*BASE_OUTPUTS, *recipe.outputs])
    manifest["related_modules"] = unique_strings(
        [*(str(item) for item in manifest.get("related_modules", []) if item), *recipe.related_modules]
    )
    manifest["validation"] = validation_commands(workflow_name)
    command_specs = contract_manifests.command_specs(manifest.get("commands"))
    known_argv = {
        tuple(command.get("argv", []))
        for command in command_specs
        if isinstance(command.get("argv"), list)
    }
    for command_text in validation_commands(workflow_name):
        command = create_workflow.typed_command(command_text, workflow_name)
        argv = tuple(command["argv"])
        if argv not in known_argv:
            command_specs.append(command)
            known_argv.add(argv)
    manifest["commands"] = command_specs
    manifest["worker_profiles"] = {
        "schema_version": 1,
        "extends": "portable-default",
        "mode": "auto-when-supported",
        "max_parallel_workers": 1,
        "phase_assignments": {phase: profile_for_phase(phase) for phase in recipe.phases},
        "task_assignments": {},
        "delegation": create_workflow.module_contract_v3.default_delegation_contract(),
    }
    extensions = manifest.setdefault("extensions", {})
    if not isinstance(extensions, dict):
        extensions = {}
        manifest["extensions"] = extensions
    extensions["skills-harness/workflow-builder"] = {
        "recipe": recipe.id,
        "profile": profile,
        "request": request,
        "user_only_answers": INTAKE_QUESTIONS,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    touched.append(manifest_path)

    metadata_path = module_dir / "metadata" / "workflow-metadata.json"
    metadata, metadata_error = common.read_json_file(metadata_path)
    if metadata_error or not isinstance(metadata, dict):
        metadata = {}
    metadata["builder_recipe"] = {
        "id": recipe.id,
        "title": recipe.title,
        "profile": profile,
        "request": request,
        "phases": list(recipe.phases),
        "outputs": list(recipe.outputs),
        "validation": list(recipe.validation),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    touched.append(metadata_path)

    workflow_path = module_dir / "WORKFLOW.md"
    workflow_text = common.read_text(workflow_path, limit=120_000)
    recipe_section = (
        "\n## Builder Recipe\n\n"
        f"- Recipe: `{recipe.id}` - {recipe.title}\n"
        f"- Profile: `{profile}`\n"
        f"- Request: {request}\n"
        f"- Derived phases: {', '.join(recipe.phases)}\n"
        f"- Derived outputs: {', '.join(recipe.outputs)}\n"
    )
    if "## Builder Recipe" not in workflow_text:
        insert_at = workflow_text.find("\n## Start")
        if insert_at >= 0:
            workflow_text = workflow_text[:insert_at] + recipe_section + workflow_text[insert_at:]
        else:
            workflow_text = workflow_text.rstrip() + recipe_section + "\n"
    workflow_path.write_text(workflow_text.rstrip() + "\n", encoding="utf-8", newline="\n")
    touched.append(workflow_path)

    instructions_path = module_dir / "instructions.md"
    instructions_path.write_text(render_recipe_instructions(workflow_name, recipe), encoding="utf-8", newline="\n")
    touched.append(instructions_path)

    for relative_path, content in {
        "templates/plan.md": render_recipe_plan_template(workflow_name, recipe, profile),
        "templates/lean-plan.md": render_recipe_plan_template(workflow_name, recipe, "simple"),
        "suites/workflow-evals.json": json.dumps(render_recipe_eval_suite(workflow_name, recipe), indent=2, sort_keys=True) + "\n",
    }.items():
        path = module_dir / relative_path
        path.write_text(content.rstrip() + "\n", encoding="utf-8", newline="\n")
        touched.append(path)

    return touched


def create_from_request(
    root: Path,
    request: str,
    *,
    workflow_name: str | None = None,
    recipe_id: str | None = None,
    profile: str = "standard",
    uses_skill: list[str] | None = None,
    uses_script: list[str] | None = None,
    write: bool = False,
    force: bool = False,
    force_new: bool = False,
    compact: bool = False,
) -> dict[str, Any]:
    proposal = proposal_report(
        root,
        request,
        workflow_name=workflow_name,
        recipe_id=recipe_id,
        profile=profile,
        force_new=force_new,
        mode="create",
        compact=False,
    )
    if not write:
        proposal["status"] = "dry-run"
        proposal["write_mode"] = "dry-run"
        if isinstance(proposal.get("decision"), dict):
            proposal["decision"]["write_mode"] = "dry-run"
        proposal["next_command"] = str(proposal["next_command"]).replace(" --write", " --write")
        if compact:
            return compact_report(proposal)
        return proposal
    if proposal["recommendation"] != "create-new":
        proposal["ok"] = False
        proposal["status"] = "blocked"
        proposal["writes"] = False
        proposal["write_mode"] = "blocked"
        if isinstance(proposal.get("decision"), dict):
            proposal["decision"]["write_mode"] = "blocked"
        if proposal["recommendation"] == "do-not-create":
            proposal["issues"] = [
                "Create was blocked because this request is too small for a workflow.",
                "Keep this as a skill, command, or normal documentation change.",
            ]
        else:
            proposal["issues"] = [
                "Create was blocked because an existing workflow appears to own this intent.",
                "Use workflow adjust or pass --force-new when a distinct workflow is still required.",
            ]
        if compact:
            return compact_report(proposal)
        return proposal

    target_name = str(proposal["proposed_workflow_name"])
    summary = request.strip()
    if len(summary) > 180:
        summary = summary[:177].rstrip() + "..."
    args = Namespace(
        root=str(root),
        workflow_name=target_name,
        summary=summary,
        uses_skill=uses_skill or [],
        uses_script=uses_script or [],
        force=force,
        skip_overlap_check=force_new,
    )
    written = create_workflow.create_workflow(args)
    recipe = recipe_by_id(str((proposal.get("recipe") or {}).get("id") if isinstance(proposal.get("recipe"), dict) else recipe_id))
    touched = apply_recipe_to_scaffold(root, target_name, request, recipe, profile)
    written_paths = unique_strings([*(common.relative(root, path) for path in written), *(common.relative(root, path) for path in touched)])
    proposal.update(
        {
            "ok": True,
            "status": "written",
            "writes": True,
            "write_mode": "written",
            "action": "create-new",
            "selected_workflow": target_name,
            "new_workflow": target_name,
            "target_workflow": target_name,
            "written_paths": written_paths,
            "derived_contract": {
                "recipe": recipe.id,
                "profile": profile,
                "phases": list(recipe.phases),
                "outputs": unique_strings([*BASE_OUTPUTS, *recipe.outputs]),
                "worker_profiles": {phase: profile_for_phase(phase) for phase in recipe.phases},
            },
            "next_command": f"python -B .agents/manage.py validate-automations --name {target_name} --strict-phase-quality",
            "decision": decision_fields(
                action="create-new",
                selected_workflow=target_name,
                new_workflow=target_name,
                write_mode="written",
                next_command=f"python -B .agents/manage.py validate-automations --name {target_name} --strict-phase-quality",
            ),
        }
    )
    if compact:
        return compact_report(proposal)
    return proposal


def compact_report(report: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "workflow-manager.intent-builder"),
        "ok": report.get("ok", False),
        "status": report.get("status", "unknown"),
        "mode": report.get("mode", ""),
        "action": report.get("action") or report.get("recommendation", ""),
        "writes": report.get("writes", False),
        "write_mode": report.get("write_mode", "read-only"),
        "recommendation": report.get("recommendation", ""),
        "selected_workflow": report.get("selected_workflow") or report.get("target_workflow") or report.get("workflow") or "",
        "new_workflow": report.get("new_workflow", ""),
        "target_workflow": report.get("target_workflow") or report.get("selected_workflow") or report.get("workflow") or "",
        "proposed_workflow_name": report.get("proposed_workflow_name", ""),
        "recipe": (report.get("recipe") or {}).get("id") if isinstance(report.get("recipe"), dict) else "",
        "next_command": report.get("next_command", ""),
    }
    if report.get("recipe_count") is not None:
        compact["recipe_count"] = report.get("recipe_count")
        compact["recipes"] = report.get("recipes", [])
    if report.get("existing_workflow_candidates"):
        compact["existing_workflow_candidates"] = report.get("existing_workflow_candidates", [])[:3]
    if report.get("changed_paths"):
        compact["changed_paths"] = report.get("changed_paths", [])
    if report.get("written_paths"):
        compact["written_paths"] = report.get("written_paths", [])
    if report.get("validation_commands"):
        compact["validation_commands"] = report.get("validation_commands", [])
    if report.get("issues"):
        compact["issues"] = report.get("issues", [])
    if report.get("skipped"):
        compact["skipped"] = report.get("skipped", [])
    return compact


def render_report(report: dict[str, Any]) -> str:
    title = "Workflow Intent Builder"
    if report.get("tool") == "workflow-manager.intent-builder.recipes":
        title = "Workflow Recipes"
    lines = [f"# {title}", ""]
    lines.append(f"- Status: {report.get('status')}")
    if report.get("mode"):
        lines.append(f"- Mode: `{report.get('mode')}`")
    lines.append(f"- Writes: `{str(report.get('writes', False)).lower()}`")
    if report.get("recommendation"):
        lines.append(f"- Recommendation: `{report.get('recommendation')}`")
    if report.get("target_workflow"):
        lines.append(f"- Target workflow: `{report.get('target_workflow')}`")
    if report.get("proposed_workflow_name"):
        lines.append(f"- Proposed workflow: `{report.get('proposed_workflow_name')}`")
    recipe = report.get("recipe")
    if isinstance(recipe, dict):
        lines.append(f"- Recipe: `{recipe.get('id')}` - {recipe.get('title')}")
    if report.get("user_only_answers"):
        lines.extend(["", "## User Answers", ""])
        for item in report["user_only_answers"]:
            lines.append(f"- {item}")
    if report.get("existing_workflow_candidates"):
        lines.extend(["", "## Existing Candidates", ""])
        for item in report["existing_workflow_candidates"][:5]:
            lines.append(
                f"- `{item.get('workflow')}` score={item.get('score')} "
                f"matches={', '.join(item.get('matched_terms', []))}"
            )
    if report.get("proposed_files"):
        lines.extend(["", "## Proposed Files", ""])
        for path in report["proposed_files"]:
            lines.append(f"- `{path}`")
    if report.get("changed_paths"):
        lines.extend(["", "## Changed Paths", ""])
        for path in report["changed_paths"]:
            lines.append(f"- `{path}`")
    if report.get("validation_commands"):
        lines.extend(["", "## Validation", ""])
        for command in report["validation_commands"]:
            lines.append(f"- `{command}`")
    if report.get("recipes"):
        lines.extend(["", "## Recipes", ""])
        for recipe_item in report["recipes"]:
            lines.append(f"- `{recipe_item.get('id')}` - {recipe_item.get('title')}")
    if report.get("issues"):
        lines.extend(["", "## Issues", ""])
        for issue in report["issues"]:
            lines.append(f"- {issue}")
    if report.get("next_command"):
        lines.extend(["", f"Next command: `{report.get('next_command')}`"])
    return "\n".join(lines) + "\n"
