#!/usr/bin/env python3
"""Workflow-manager CLI parser registration."""

from __future__ import annotations

import argparse
import re

from workflow_support.hooks import WORKFLOW_HOOK_EVENTS


PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


def template_profile_id(value: str) -> str:
    if not PROFILE_ID_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "template profile must use lowercase letters, digits, and hyphens"
        )
    return value


def add_root_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", help="repository root; defaults to script parent")


def add_examples(parser: argparse.ArgumentParser, *examples: str) -> None:
    parser.epilog = "Examples:\n" + "\n".join(f"  {example}" for example in examples)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        prog="python -B .agents/manage.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate-automations",
        help="validate automation workflow modules",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(validate_parser)
    validate_parser.add_argument("--name", dest="workflow_name", help="validate one workflow name")
    validate_parser.add_argument(
        "--strict-phase-quality",
        action="store_true",
        help="promote phase-quality warnings to errors",
    )
    validate_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    validate_parser.add_argument("--summary", action="store_true", help="emit aggregate counts and failures only")
    validate_parser.add_argument("--compact", action="store_true", help="with --summary, omit passing module rows")
    add_examples(
        validate_parser,
        "python -B .agents/manage.py validate-automations",
        "python -B .agents/manage.py validate-automations --name my-workflow",
        "python -B .agents/manage.py validate-automations --strict-phase-quality",
    )

    eval_parser = subparsers.add_parser(
        "eval-workflow",
        help="runtime: run deterministic local eval assertions for one workflow; inspect suites before strict read-only use",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(eval_parser)
    eval_parser.add_argument("--name", required=True, dest="workflow_name")
    eval_parser.add_argument("--suite", required=True, help="JSON workflow eval suite")
    eval_parser.add_argument("--summary", action="store_true")
    eval_parser.add_argument("--compact", action="store_true")
    eval_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        eval_parser,
        "python -B .agents/manage.py eval-workflow --name my-workflow --suite automations/my-workflow/suites/workflow-evals.json",
    )

    eval_all_parser = subparsers.add_parser(
        "eval-workflows",
        help="runtime: run every workflow eval suite; suites can execute commands or temporary fixtures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(eval_all_parser)
    eval_all_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    eval_all_parser.add_argument("--summary", action="store_true", help="emit compact counts and failures only")
    eval_all_parser.add_argument("--compact", action="store_true", help="with --summary, omit passing suite rows")
    add_examples(
        eval_all_parser,
        "python -B .agents/manage.py workflow eval --all --summary --compact --format json",
    )

    smoke_parser = subparsers.add_parser(
        "smoke-workflows",
        help="write/temp: run offline fixture-backed smoke checks; use --dry-run for read-only planning",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(smoke_parser)
    smoke_target = smoke_parser.add_mutually_exclusive_group(required=True)
    smoke_target.add_argument("--all", action="store_true", help="smoke every accepted workflow")
    smoke_target.add_argument("--name", action="append", dest="workflow_names", help="workflow name to smoke; repeatable")
    smoke_parser.add_argument(
        "--lifecycle-only",
        action="store_true",
        help="write/temp: only start/checkpoint/handoff/context/finish temporary workflow runs",
    )
    smoke_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read-only: plan offline smoke checks without writing temporary workflow run files",
    )
    smoke_parser.add_argument("--summary", action="store_true", help="emit workflow-level counts")
    smoke_parser.add_argument("--compact", action="store_true", help="with --summary, omit fully passing workflow rows")
    smoke_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        smoke_parser,
        "python -B .agents/manage.py workflow smoke --all --summary --compact --format json",
        "python -B .agents/manage.py workflow smoke --name user-story-workflow --dry-run --summary --compact --format json",
        "python -B .agents/manage.py workflow smoke --name user-story-workflow --format json",
    )

    scorecard_parser = subparsers.add_parser(
        "scorecard-workflows",
        help="runtime: score workflow readiness; use --no-lifecycle for read-only/offline scoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(scorecard_parser)
    scorecard_target = scorecard_parser.add_mutually_exclusive_group(required=True)
    scorecard_target.add_argument("--all", action="store_true", help="score every accepted workflow")
    scorecard_target.add_argument("--name", action="append", dest="workflow_names", help="workflow name to score; repeatable")
    scorecard_parser.add_argument("--no-lifecycle", action="store_true", help="skip temporary lifecycle smoke scoring without lifecycle writes")
    scorecard_parser.add_argument("--summary", action="store_true", help="emit workflow-level counts")
    scorecard_parser.add_argument("--compact", action="store_true", help="with --summary, omit passing workflow rows")
    scorecard_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        scorecard_parser,
        "python -B .agents/manage.py workflow scorecard --all --summary --compact --format json",
        "python -B .agents/manage.py workflow scorecard --name user-story-workflow --format json",
    )

    analytics_parser = subparsers.add_parser(
        "analytics-workflows",
        help="summarize retained workflow run friction, proof gaps, and reusable lessons",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(analytics_parser)
    analytics_target = analytics_parser.add_mutually_exclusive_group(required=True)
    analytics_target.add_argument("--all", action="store_true", help="summarize every accepted workflow")
    analytics_target.add_argument("--name", action="append", dest="workflow_names", help="workflow name to summarize; repeatable")
    analytics_parser.add_argument("--summary", action="store_true", help="emit workflow-level counts")
    analytics_parser.add_argument("--compact", action="store_true", help="with --summary, omit workflows without retained runs")
    analytics_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        analytics_parser,
        "python -B .agents/manage.py workflow analytics --all --summary --compact --format json",
        "python -B .agents/manage.py workflow analytics --name local-ai-benchmark-workflow --format json",
    )

    workers_parser = subparsers.add_parser(
        "workflow-workers",
        help="report workflow phase worker/model profile assignments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(workers_parser)
    workers_target = workers_parser.add_mutually_exclusive_group(required=True)
    workers_target.add_argument("--all", action="store_true", help="report every accepted workflow")
    workers_target.add_argument("--name", action="append", dest="workflow_names", help="workflow name to report; repeatable")
    workers_target.add_argument("--profiles", action="store_true", help="report the shared worker profile catalog")
    workers_parser.add_argument("--phase", help="limit output to one workflow phase id")
    workers_parser.add_argument(
        "--run-id",
        help="verify and use the selected run's persisted current-host observation for delegation gating",
    )
    workers_parser.add_argument(
        "--delegation-requested",
        action="store_true",
        help="attest that the user or owner instruction explicitly requested eligible delegation",
    )
    workers_parser.add_argument(
        "--task-class",
        choices=("independent-read-heavy",),
        default="independent-read-heavy",
        help="bounded task class being evaluated for delegation",
    )
    workers_parser.add_argument("--summary", action="store_true", help="emit workflow-level counts")
    workers_parser.add_argument("--compact", action="store_true", help="with --summary, omit passing workflow rows")
    workers_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        workers_parser,
        "python -B .agents/manage.py workflow workers --all --summary --compact --format json",
        "python -B .agents/manage.py workflow workers --profiles",
        "python -B .agents/manage.py workflow workers --name user-story-workflow",
        "python -B .agents/manage.py workflow workers --name user-story-workflow --phase planning",
        "python -B .agents/manage.py workflow workers --name user-story-workflow --phase intake --run-id <run-id> --delegation-requested",
    )

    route_model_parser = subparsers.add_parser(
        "workflow-route-model",
        help="resolve a project task/task-set ordered model preference and fallback chain",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(route_model_parser)
    route_target = route_model_parser.add_mutually_exclusive_group()
    route_target.add_argument("--task", help="project task id; unknown ids use the default task set")
    route_target.add_argument("--task-set", help="declared task-set id")
    route_model_parser.add_argument("--host", required=True, help="host surface such as codex, github-copilot, or claude")
    route_model_parser.add_argument(
        "--available-model",
        action="append",
        dest="available_models",
        help="model the current host can invoke; repeatable and required for a concrete selection",
    )
    route_model_parser.add_argument(
        "--failed-model",
        action="append",
        dest="failed_models",
        help="failed model to exclude before advancing through fallbacks; repeatable",
    )
    route_model_parser.add_argument("--validate", action="store_true", help="validate configuration without resolving a route")
    route_model_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        route_model_parser,
        "python -B .agents/manage.py workflow route-model --task implementation --host codex --format json",
        "python -B .agents/manage.py workflow route-model --task implementation --host codex --available-model gpt-5.6-terra --failed-model gpt-5.6-terra --format json",
        "python -B .agents/manage.py workflow route-model --validate --host default --format json",
    )

    index_parser = subparsers.add_parser(
        "index-workflow-runs",
        help="read-only check or write workflow-local v2 run indexes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(index_parser)
    index_parser.add_argument("--name", required=True, dest="workflow_name")
    index_mode = index_parser.add_mutually_exclusive_group()
    index_mode.add_argument("--write", action="store_true", help="write runs/INDEX.md and index.json when run folders exist")
    index_mode.add_argument("--check", action="store_true", help="read-only check: fail when generated run indexes are stale")
    index_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        index_parser,
        "python -B .agents/manage.py index-workflow-runs --name my-workflow --write",
        "python -B .agents/manage.py index-workflow-runs --name my-workflow --check",
    )

    routing_parser = subparsers.add_parser(
        "sync-automation-routing",
        help="write generated automation workflow routing and registry artifacts; use --check for read-only drift detection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(routing_parser)
    routing_parser.add_argument(
        "--check",
        action="store_true",
        help="fail if generated automation routing and registry files are out of sync",
    )
    add_examples(
        routing_parser,
        "python -B .agents/manage.py sync-automation-routing",
        "python -B .agents/manage.py sync-automation-routing --check",
    )

    create_parser = subparsers.add_parser(
        "create-workflow",
        help="write scaffold files for a Markdown-first automation workflow starter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(create_parser)
    create_parser.add_argument("--name", required=True, dest="workflow_name")
    create_parser.add_argument("--summary", required=True)
    create_parser.add_argument(
        "--uses-skill",
        action="append",
        default=[],
        help="skill this workflow may invoke; repeat for multiple skills",
    )
    create_parser.add_argument(
        "--uses-script",
        action="append",
        default=[],
        help="repo-relative Python script path or manage command this workflow may run",
    )
    create_parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite scaffold-owned files in an existing workflow folder",
    )
    add_examples(
        create_parser,
        "python -B .agents/manage.py new --kind workflow --name my-workflow --summary \"Short purpose\"",
        "python -B .agents/manage.py new --kind workflow --name my-workflow --summary \"Short purpose\" --uses-skill skill-manager",
        "python -B .agents/manage.py new --kind workflow --name my-workflow --summary \"Short purpose\" --uses-script \".agents/manage.py check\"",
    )

    propose_parser = subparsers.add_parser(
        "propose-workflow",
        help="propose a workflow from a plain-language request without writing files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(propose_parser)
    propose_parser.add_argument("--from-request", "--request", required=True, dest="from_request")
    propose_parser.add_argument("--name", dest="workflow_name", help="preferred new workflow name")
    propose_parser.add_argument("--recipe", help="recipe id to use instead of auto-detection")
    propose_parser.add_argument("--profile", choices=("simple", "standard", "strict"), default="standard")
    propose_parser.add_argument("--force-new", action="store_true", help="treat overlap candidates as advisory")
    propose_parser.add_argument("--summary", action="store_true", help="emit compact proposal fields")
    propose_parser.add_argument("--compact", action="store_true", help="with --summary, omit verbose recipe detail")
    propose_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        propose_parser,
        "python -B .agents/manage.py workflow propose --from-request \"review release evidence\" --summary --compact --format json",
        "python -B .agents/manage.py workflow propose --from-request \"import partner tickets\" --recipe external-system-intake",
    )

    recipe_parser = subparsers.add_parser(
        "workflow-recipes",
        help="list intent-first workflow creation recipes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(recipe_parser)
    recipe_parser.add_argument("--summary", action="store_true", help="emit compact recipe fields")
    recipe_parser.add_argument("--compact", action="store_true", help="omit verbose recipe detail")
    recipe_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        recipe_parser,
        "python -B .agents/manage.py workflow recipes --summary --compact --format json",
    )

    create_intent_parser = subparsers.add_parser(
        "create-workflow-from-request",
        help="read-only by default; writes a workflow scaffold from a plain-language request only with --write",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(create_intent_parser)
    create_intent_parser.add_argument("--from-request", "--request", required=True, dest="from_request")
    create_intent_parser.add_argument("--name", dest="workflow_name", help="new workflow name")
    create_intent_parser.add_argument("--recipe", help="recipe id to use instead of auto-detection")
    create_intent_parser.add_argument("--profile", choices=("simple", "standard", "strict"), default="standard")
    create_intent_parser.add_argument("--uses-skill", action="append", default=[], help="skill this workflow may invoke")
    create_intent_parser.add_argument("--uses-script", action="append", default=[], help="repo command or Python script")
    create_intent_parser.add_argument("--write", action="store_true", help="write scaffold files under automations/<workflow-name>")
    create_intent_parser.add_argument("--force", action="store_true", help="overwrite scaffold-owned files")
    create_intent_parser.add_argument("--force-new", action="store_true", help="write even when overlap candidates exist")
    create_intent_parser.add_argument("--summary", action="store_true", help="emit compact fields")
    create_intent_parser.add_argument("--compact", action="store_true", help="omit verbose recipe detail")
    create_intent_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        create_intent_parser,
        "python -B .agents/manage.py workflow create --from-request \"review release evidence\" --name release-evidence-workflow --write",
        "python -B .agents/manage.py workflow create --from-request \"review release evidence\" --summary --compact --format json",
    )

    adjust_parser = subparsers.add_parser(
        "adjust-workflow",
        help="plan read-only changes to an existing workflow from plain-language intent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(adjust_parser)
    adjust_parser.add_argument("--name", required=True, dest="workflow_name")
    adjust_parser.add_argument("--from-request", "--request", required=True, dest="from_request")
    adjust_parser.add_argument("--recipe", help="recipe id to use instead of auto-detection")
    adjust_parser.add_argument("--profile", choices=("simple", "standard", "strict"), default="standard")
    adjust_parser.add_argument("--plan", action="store_true", help="show patch plan; included by default")
    adjust_parser.add_argument("--summary", action="store_true", help="emit compact fields")
    adjust_parser.add_argument("--compact", action="store_true", help="omit verbose recipe detail")
    adjust_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        adjust_parser,
        "python -B .agents/manage.py workflow adjust --name user-story-workflow --from-request \"tighten validation\" --plan",
    )

    review_parser = subparsers.add_parser(
        "review-workflow",
        help="compact review for one workflow: validation, routing, context budget, overlap, and local AI hints",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(review_parser)
    review_target = review_parser.add_mutually_exclusive_group(required=True)
    review_target.add_argument("--name", dest="workflow_name")
    review_target.add_argument("--all", action="store_true", help="review all accepted workflows")
    review_parser.add_argument("--summary", action="store_true", help="show compact aggregate output when using --all")
    review_parser.add_argument("--compact", action="store_true", help="with --summary, omit ok workflow rows")
    review_parser.add_argument(
        "--include-completed",
        action="store_true",
        help="with --all, treat completed-run context failures as blocking health risks",
    )
    review_parser.add_argument(
        "--plan",
        action="store_true",
        help="include an implementation packet with likely files, checks, artifacts, and evidence",
    )
    review_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        review_parser,
        "python -B .agents/manage.py review my-workflow",
        "python -B .agents/manage.py review my-workflow --plan",
        "python -B .agents/manage.py review my-workflow --format json",
    )

    start_parser = subparsers.add_parser(
        "start-run",
        help="write/run-state: create a workflow-local run folder with resume state and evidence ledger",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(start_parser)
    start_parser.add_argument("--name", required=True, dest="workflow_name")
    start_parser.add_argument(
        "--run-id",
        help=(
            "run identifier; required for ticket workflows, which store stories as "
            "US-<identifier> and bugs as BUG-<identifier>; other workflows default to a UTC timestamp"
        ),
    )
    start_parser.add_argument(
        "--from-ticket",
        help="repo-local Azure DevOps ticket intake folder to attach as initial evidence",
    )
    start_parser.add_argument(
        "--from-request",
        help="natural-language request that was routed into this workflow run",
    )
    start_parser.add_argument(
        "--profile",
        type=template_profile_id,
        default="default",
        help="declared template profile for scaffolded run files",
    )
    start_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    start_parser.add_argument("--summary", action="store_true", help="emit compact agent-facing start packet")
    start_parser.add_argument("--compact", action="store_true", help="with --summary, omit verbose evidence payloads")
    add_examples(
        start_parser,
        "python -B .agents/manage.py workflow start --name user-story-workflow",
        "python -B .agents/manage.py workflow start --name user-story-workflow --summary --compact --format json",
        "python -B .agents/manage.py workflow start --from-request \"implement Azure DevOps user story 123\" --summary --compact --format json",
        "python -B .agents/manage.py workflow start --name user-story-workflow --profile lean",
        "python -B .agents/manage.py workflow start --name bug-ticket-workflow --from-ticket evidence/ticket-123",
    )

    resume_parser = subparsers.add_parser(
        "resume-run",
        help="write/run-state: refresh context evidence, context packet, and checkpoint state, then show the selected workflow run state and next action",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(resume_parser)
    resume_parser.add_argument("--name", required=True, dest="workflow_name")
    resume_parser.add_argument("--run-id", help="optional run id; defaults to latest indexed or newest folder")
    resume_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    resume_parser.add_argument("--summary", action="store_true", help="emit compact agent-facing resume packet")
    resume_parser.add_argument("--compact", action="store_true", help="with --summary, omit verbose evidence payloads")
    add_examples(
        resume_parser,
        "python -B .agents/manage.py workflow resume --name user-story-workflow",
        "python -B .agents/manage.py workflow resume --name user-story-workflow --summary --compact --format json",
    )

    recover_parser = subparsers.add_parser(
        "recover-run",
        help="read-only diagnostic by default; reconstructs workflow run.json only with --write",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(recover_parser)
    recover_parser.add_argument("--name", required=True, dest="workflow_name")
    recover_parser.add_argument("--run-id", help="optional run id; defaults to latest indexed or newest folder")
    recover_parser.add_argument("--write", action="store_true", help="write recovered run.json and refresh context when declared")
    recover_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        recover_parser,
        "python -B .agents/manage.py workflow recover --name user-story-workflow --run-id US-123",
        "python -B .agents/manage.py workflow recover --name user-story-workflow --run-id US-123 --write",
    )

    context_parser = subparsers.add_parser(
        "context-run",
        help="read-only check or write a compact deterministic context packet for a workflow run",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(context_parser)
    context_parser.add_argument("--name", dest="workflow_name")
    context_parser.add_argument("--all", action="store_true", help="check every workflow that declares context packets")
    context_parser.add_argument("--run-id", help="optional run id; defaults to latest indexed or newest folder")
    context_parser.add_argument("--write", action="store_true", help="write artifacts/context/context-packet.json and .md")
    context_parser.add_argument("--check", action="store_true", help="read-only check: fail if the existing context packet is missing or stale")
    context_parser.add_argument(
        "--runtime-observation-file",
        help=(
            "repo-local host/provider observation JSON under the selected run's validation directory; "
            "requires --write"
        ),
    )
    context_parser.add_argument("--summary", action="store_true", help="emit compact context check counts")
    context_parser.add_argument("--compact", action="store_true", help="emit a compact context check packet")
    context_parser.add_argument(
        "--include-completed",
        action="store_true",
        help="with --all, treat completed-run context failures as blocking",
    )
    context_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        context_parser,
        "python -B .agents/manage.py workflow context --name user-story-workflow --run-id US-123 --write",
        "python -B .agents/manage.py workflow context --name user-story-workflow --run-id US-123 --runtime-observation-file automations/user-story-workflow/runs/US-123/validation/runtime-observation.json --write",
        "python -B .agents/manage.py workflow context --all --check --summary --compact --format json",
    )

    context_audit_parser = subparsers.add_parser(
        "context-audit-run",
        help="audit workflow resume/handoff context packet freshness, required context, and evidence references",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(context_audit_parser)
    context_audit_parser.add_argument("--name", required=True, dest="workflow_name")
    context_audit_parser.add_argument("--run-id", help="optional run id; defaults to latest indexed or newest folder")
    context_audit_parser.add_argument("--summary", action="store_true", help="emit compact context audit counts")
    context_audit_parser.add_argument("--compact", action="store_true", help="with --summary, omit passing details")
    context_audit_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        context_audit_parser,
        "python -B .agents/manage.py workflow context-audit --name user-story-workflow --run-id US-123 --summary --compact --format json",
    )

    checkpoint_parser = subparsers.add_parser(
        "checkpoint-run",
        help="read-only check or write a compact generated checkpoint for a workflow run",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(checkpoint_parser)
    checkpoint_parser.add_argument("--name", required=True, dest="workflow_name")
    checkpoint_parser.add_argument("--run-id", help="optional run id; defaults to latest indexed or newest folder")
    checkpoint_parser.add_argument("--write", action="store_true", help="write artifacts/checkpoint/checkpoint.json and .md")
    checkpoint_parser.add_argument("--check", action="store_true", help="read-only check: fail if the existing checkpoint is missing or stale")
    checkpoint_parser.add_argument("--compact", action="store_true", help="emit compact checkpoint status only")
    checkpoint_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        checkpoint_parser,
        "python -B .agents/manage.py workflow checkpoint --name user-story-workflow --run-id US-123 --write",
        "python -B .agents/manage.py workflow checkpoint --name user-story-workflow --check --compact --format json",
    )

    plan_parser = subparsers.add_parser(
        "plan-check-run",
        help="check a workflow plan template or run plan for required sections and filled evidence rows",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(plan_parser)
    plan_parser.add_argument("--name", required=True, dest="workflow_name")
    plan_parser.add_argument("--run-id", help="run id containing plan.md")
    plan_parser.add_argument("--template", action="store_true", help="check templates/plan.md shape instead of a run plan")
    plan_parser.add_argument("--plan", help="explicit repo-local plan.md path")
    plan_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        plan_parser,
        "python -B .agents/manage.py workflow plan-check --name user-story-workflow --template",
        "python -B .agents/manage.py workflow plan-check --name bug-ticket-workflow --run-id BUG-456 --format json",
    )

    template_parser = subparsers.add_parser(
        "template-run",
        help="resolve or lint workflow template override and preset layers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(template_parser)
    template_sub = template_parser.add_subparsers(dest="template_command", required=True)
    resolve_parser = template_sub.add_parser("resolve", help="show the selected provider for a workflow template")
    resolve_parser.add_argument("--name", required=True, dest="workflow_name")
    resolve_parser.add_argument("--template")
    resolve_parser.add_argument("--profile", type=template_profile_id, default="default")
    resolve_parser.add_argument("--summary", action="store_true")
    resolve_parser.add_argument("--compact", action="store_true")
    resolve_parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    lint_parser = template_sub.add_parser("lint", help="check workflow template provider conflicts")
    lint_parser.add_argument("--name", dest="workflow_name")
    lint_parser.add_argument("--summary", action="store_true")
    lint_parser.add_argument("--compact", action="store_true")
    lint_parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    gate_parser = template_sub.add_parser("gate-check", help="check required gate evidence sections across template profiles")
    gate_target = gate_parser.add_mutually_exclusive_group(required=True)
    gate_target.add_argument("--name", dest="workflow_name")
    gate_target.add_argument("--all", action="store_true")
    gate_parser.add_argument("--summary", action="store_true")
    gate_parser.add_argument("--compact", action="store_true")
    gate_parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    add_examples(
        template_parser,
        "python -B .agents/manage.py workflow template resolve --name user-story-workflow --template plan.md",
        "python -B .agents/manage.py workflow template resolve --name user-story-workflow --template plan.md --profile lean",
        "python -B .agents/manage.py workflow template lint --name user-story-workflow --format json",
        "python -B .agents/manage.py workflow template gate-check --all --format json",
    )

    integration_parser = subparsers.add_parser(
        "integration-check-run",
        help="validate repo-local integration descriptors",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(integration_parser)
    integration_parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")

    metadata_parser = subparsers.add_parser(
        "metadata-run",
        help="inspect merged workflow module and external metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(metadata_parser)
    metadata_sub = metadata_parser.add_subparsers(dest="metadata_command", required=True)
    metadata_inspect_parser = metadata_sub.add_parser("inspect", help="show merged module.json and metadata_path fields")
    metadata_inspect_parser.add_argument("--name", required=True, dest="workflow_name")
    metadata_inspect_parser.add_argument("--summary", action="store_true")
    metadata_inspect_parser.add_argument("--compact", action="store_true")
    metadata_inspect_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )

    managed_diff_parser = subparsers.add_parser(
        "managed-section-diff-run",
        help="show a diff for replacing a managed section without writing it",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(managed_diff_parser)
    managed_diff_parser.add_argument("--target", required=True)
    managed_diff_parser.add_argument("--replacement", required=True)
    managed_diff_parser.add_argument("--start-marker", default="<!-- MANAGED START -->")
    managed_diff_parser.add_argument("--end-marker", default="<!-- MANAGED END -->")
    managed_diff_parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")

    branch_parser = subparsers.add_parser(
        "branch-policy-run",
        help="validate current branch naming without creating branches or commits",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(branch_parser)
    branch_parser.add_argument("--pattern", default=r"^(feature|fix|docs|chore|release)/[a-z0-9][a-z0-9._-]*$")
    branch_parser.add_argument("--branch", help="branch name to validate instead of reading git state")
    branch_parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")

    context_evidence_parser = subparsers.add_parser(
        "context-evidence-run",
        help="read-only check or write required workflow context-evidence packets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(context_evidence_parser)
    context_evidence_parser.add_argument("--name", required=True, dest="workflow_name")
    context_evidence_parser.add_argument("--run-id", help="optional run id; defaults to latest indexed or newest folder")
    context_evidence_parser.add_argument("--event", choices=("start", "resume", "finish"), default="start")
    context_evidence_mode = context_evidence_parser.add_mutually_exclusive_group()
    context_evidence_mode.add_argument("--write", action="store_true", help="write validation/context-evidence-<event>.json and .md")
    context_evidence_mode.add_argument("--check", action="store_true", help="read-only check: validate an existing context-evidence packet")
    context_evidence_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        context_evidence_parser,
        "python -B .agents/manage.py workflow context-evidence --name user-story-workflow --run-id US-123 --event start --write",
        "python -B .agents/manage.py workflow context-evidence --name bug-ticket-workflow --run-id BUG-456 --event start --check --format json",
    )

    validation_packet_parser = subparsers.add_parser(
        "validation-packet-run",
        help="check workflow-owned validation evidence packets under runs/<run-id>/validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(validation_packet_parser)
    validation_packet_parser.add_argument("--name", required=True, dest="workflow_name")
    validation_packet_parser.add_argument("--run-id", required=True)
    validation_packet_parser.add_argument("--kind", choices=("playwright-screenshots",), required=True)
    validation_packet_parser.add_argument("--require-llm-analysis", action="store_true")
    validation_packet_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        validation_packet_parser,
        "python -B .agents/manage.py workflow validation-packet --name user-story-workflow --run-id US-123 --kind playwright-screenshots --format json",
    )

    hooks_parser = subparsers.add_parser(
        "hooks-run",
        help="inspect resolved workflow hooks without executing them",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(hooks_parser)
    hooks_parser.add_argument("--name", dest="workflow_name")
    hooks_parser.add_argument("--all", action="store_true", help="check resolved hooks for every workflow")
    hooks_parser.add_argument("--check", action="store_true", help="fail if any resolved hook is unsafe")
    hooks_parser.add_argument("--run-id", help="optional run id; defaults to dry-run placeholder")
    hooks_parser.add_argument("--event", choices=sorted(WORKFLOW_HOOK_EVENTS))
    hooks_parser.add_argument("--summary", action="store_true", help="emit compact hook counts and unsafe rows")
    hooks_parser.add_argument("--compact", action="store_true", help="emit compact hook counts and unsafe rows")
    hooks_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        hooks_parser,
        "python -B .agents/manage.py workflow hooks --name user-story-workflow --format json",
        "python -B .agents/manage.py workflow hooks --name user-story-workflow --event phase-between",
        "python -B .agents/manage.py workflow hooks --all --check --format json",
    )

    hook_audit_parser = subparsers.add_parser(
        "hook-audit-run",
        help="write a normalized evidence packet for a deterministic workflow hook",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(hook_audit_parser)
    hook_audit_parser.add_argument("--name", required=True, dest="workflow_name")
    hook_audit_parser.add_argument("--run-id", help="optional run id; defaults to run-dir name")
    hook_audit_parser.add_argument("--run-dir", required=True, help="workflow run folder")
    hook_audit_parser.add_argument("--event", required=True, choices=sorted(WORKFLOW_HOOK_EVENTS))
    hook_audit_parser.add_argument("--hook-id", required=True)
    hook_audit_parser.add_argument("--output", help="optional output path inside the run folder")
    hook_audit_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        hook_audit_parser,
        "python -B .agents/manage.py workflow hook-audit --name user-story-workflow --run-dir automations/user-story-workflow/runs/<run-id> --event workflow-pre --hook-id global-workflow-pre",
    )

    finish_parser = subparsers.add_parser(
        "finish-run",
        help="write/run-state: validate workflow run evidence, refresh lifecycle packets, and update final state when needed",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(finish_parser)
    finish_parser.add_argument("--name", required=True, dest="workflow_name")
    finish_parser.add_argument("--run-id", help="optional run id; defaults to latest indexed or newest folder")
    finish_parser.add_argument("--summary", action="store_true", help="emit compact agent-facing finish evidence")
    finish_parser.add_argument("--compact", action="store_true", help="omit verbose proof details from finish output")
    finish_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        finish_parser,
        "python -B .agents/manage.py workflow finish --name user-story-workflow --run-id US-123",
    )

    handoff_parser = subparsers.add_parser(
        "handoff-run",
        help="read-only by default; refresh the run.json handoff packet only with --write",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_root_arg(handoff_parser)
    handoff_parser.add_argument("--name", required=True, dest="workflow_name")
    handoff_parser.add_argument("--run-id", help="optional run id; defaults to latest indexed or newest folder")
    handoff_parser.add_argument("--write", action="store_true", help="write/normalize the handoff section in run.json")
    handoff_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        handoff_parser,
        "python -B .agents/manage.py workflow handoff --name user-story-workflow",
        "python -B .agents/manage.py workflow handoff --name user-story-workflow --run-id US-123 --write",
    )

    return parser
