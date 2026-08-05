#!/usr/bin/env python3
"""Daily/QoL argparse registration for the repository launcher."""

from __future__ import annotations

import argparse
from typing import Any

from repo_support import repo_review_progress
from repo_support import repo_service_config


def add_qol_parsers(
    subparsers: Any,
    add_parser: Any,
    add_shared_root_arg: Any,
    add_examples: Any,
) -> None:
    dashboard_parser = add_parser(
        subparsers,
        "dashboard",
        help="show one compact daily status packet and next safest command",
    )
    add_shared_root_arg(dashboard_parser)
    dashboard_parser.add_argument(
        "--watch-once",
        action="store_true",
        help="refresh once after a short pause; useful after running another command",
    )
    dashboard_depth = dashboard_parser.add_mutually_exclusive_group()
    dashboard_depth.add_argument("--fast", action="store_true", help="skip expensive advisory checks; default behavior")
    dashboard_depth.add_argument("--full", action="store_true", help="include benchmark and full local-AI readiness checks")
    dashboard_parser.add_argument("--no-local-ai", action="store_true", help="skip local-AI advisory checks")
    dashboard_parser.add_argument("--no-github", action="store_true", help="reserved for parity with release checks; dashboard does not call GitHub")
    dashboard_parser.add_argument("--fix-suggestions", action="store_true", help="include exact safe commands for failed or skipped sections")
    dashboard_parser.add_argument("--capabilities", action="store_true", help="include the broad harness capability audit")
    dashboard_parser.add_argument("--summary", action="store_true", help="emit a compact machine packet")
    dashboard_parser.add_argument("--compact", action="store_true", help="with --summary, omit passing detail rows")
    add_output_format(dashboard_parser)
    add_examples(
        dashboard_parser,
        "python -B .agents/manage.py status",
        "python -B .agents/manage.py status --json",
        "python -B .agents/manage.py status --full",
        "python -B .agents/manage.py status --format json",
    )

    startup_parser = add_parser(
        subparsers,
        "startup-context",
        help="report always-loaded and beginner context budgets before broad reading",
    )
    add_shared_root_arg(startup_parser)
    startup_parser.add_argument("--baseline-ref", help="compare startup context estimates with a git ref")
    startup_parser.add_argument("--summary", action="store_true", help="emit startup token budget counts")
    startup_parser.add_argument("--compact", action="store_true", help="with --summary, omit per-file rows")
    add_output_format(startup_parser)
    add_examples(
        startup_parser,
        "python -B .agents/manage.py startup-context --summary --compact --format json",
        "python -B .agents/manage.py startup-context --baseline-ref origin/main --format json",
    )

    clean_context_parser = add_parser(
        subparsers,
        "clean-context-proof",
        help="prove AGENTS.md plus startup-context can find the first source-orientation file",
    )
    add_shared_root_arg(clean_context_parser)
    clean_context_parser.add_argument("--summary", action="store_true", help="emit aggregate proof counts")
    clean_context_parser.add_argument("--compact", action="store_true", help="with --summary, omit passing check rows")
    add_output_format(clean_context_parser)
    add_examples(
        clean_context_parser,
        "python -B .agents/manage.py clean-context-proof",
        "python -B .agents/manage.py clean-context-proof --summary --compact --format json",
    )

    context_cost_parser = add_parser(
        subparsers,
        "context-cost-benchmark",
        help="compare raw diff, startup guidance, and next-action route token costs",
    )
    add_shared_root_arg(context_cost_parser)
    context_cost_parser.add_argument("--min-saved-percent", type=float, default=25.0, help="minimum routed input-token savings percent")
    context_cost_parser.add_argument("--record", action="store_true", help="append a compact ignored history entry")
    context_cost_parser.add_argument("--no-record", action="store_true", help="keep read-only even when paired with generated command aliases")
    context_cost_parser.add_argument("--history", help="repo-local JSONL history path; defaults under .agents/local-ai/cache")
    context_cost_parser.add_argument("--summary", action="store_true", help="emit compact benchmark fields")
    context_cost_parser.add_argument("--compact", action="store_true", help="with --summary, omit path rows")
    add_output_format(context_cost_parser)
    add_examples(
        context_cost_parser,
        "python -B .agents/manage.py context-cost-benchmark --summary --compact --format json",
    )

    next_action_parser = add_parser(
        subparsers,
        "next-action",
        help="emit one deterministic next command with required context and stop condition",
    )
    add_shared_root_arg(next_action_parser)
    next_action_depth = next_action_parser.add_mutually_exclusive_group()
    next_action_depth.add_argument("--fast", action="store_true", help="use fast status inputs; default behavior")
    next_action_depth.add_argument("--full", action="store_true", help="include full dashboard advisory inputs")
    next_action_parser.add_argument("--summary", action="store_true", help="emit compact next-action fields")
    next_action_parser.add_argument("--compact", action="store_true", help="with --summary, omit dashboard detail")
    add_output_format(next_action_parser)
    add_examples(
        next_action_parser,
        "python -B .agents/manage.py next-action --summary --compact --format json",
    )

    review_progress_parser = add_parser(
        subparsers,
        "review-progress",
        help="show or update repo-local progress for the current review packet",
    )
    add_shared_root_arg(review_progress_parser)
    review_progress_parser.add_argument("--mark-complete", help="mark a current review unit id complete")
    review_progress_parser.add_argument("--mark-command", help="mark the current unit matching this command complete")
    review_progress_parser.add_argument("--note", default="", help="optional completion note")
    review_progress_parser.add_argument("--reset", action="store_true", help="clear progress for the current input fingerprint")
    review_progress_parser.add_argument("--state", help="repo-local progress state path; defaults under .agents/local-ai/cache")
    review_progress_parser.add_argument("--summary", action="store_true", help="emit compact progress fields")
    review_progress_parser.add_argument("--compact", action="store_true", help="accepted with --summary for consistency")
    add_output_format(review_progress_parser)
    add_examples(
        review_progress_parser,
        "python -B .agents/manage.py review-progress --summary --compact --format json",
        "python -B .agents/manage.py review-progress --mark-command \"python -B .agents/manage.py review-packet --owner skill:skill-manager --summary --compact --format json\"",
    )

    review_loop_parser = add_parser(
        subparsers,
        "review-loop",
        help="run compact review packets sequentially and mark successful units complete",
    )
    add_shared_root_arg(review_loop_parser)
    review_loop_parser.add_argument("--max-units", type=int, default=1, help="maximum review units to process in this run")
    review_loop_parser.add_argument(
        "--max-estimated-tokens",
        type=int,
        default=repo_review_progress.DEFAULT_REVIEW_LOOP_MAX_ESTIMATED_TOKENS,
        help="stop before running a review unit that would exceed this estimated changed-token cap; 0 disables the cap",
    )
    review_loop_parser.add_argument(
        "--max-elapsed-ms",
        type=int,
        default=repo_review_progress.DEFAULT_REVIEW_LOOP_MAX_ELAPSED_MS,
        help="stop before the next review unit when total loop latency already exceeds this cap; 0 disables the cap",
    )
    review_loop_parser.add_argument("--timeout-seconds", type=int, default=120, help="timeout for each packet command")
    review_loop_parser.add_argument("--reset-stale", action="store_true", help="reset stale review progress before running the current loop")
    review_loop_parser.add_argument("--no-reset-stale", action="store_true", help="stop instead of automatically resetting stale review progress")
    review_loop_parser.add_argument("--continue", dest="continue_run", action="store_true", help="resume from current review progress; this is the default behavior")
    review_loop_parser.add_argument(
        "--include-validation",
        action="store_true",
        help="execute validation units after all review-packet units are complete",
    )
    review_loop_parser.add_argument("--dry-run", action="store_true", help="report the next command without executing it")
    review_loop_parser.add_argument("--summary", action="store_true", help="emit compact loop fields")
    review_loop_parser.add_argument("--compact", action="store_true", help="with --summary, omit mark-progress detail")
    add_output_format(review_loop_parser)
    add_examples(
        review_loop_parser,
        "python -B .agents/manage.py review-loop --max-units 3 --max-estimated-tokens 4000 --summary --compact --format json",
        "python -B .agents/manage.py review-loop --max-units 20 --max-estimated-tokens 8000 --max-elapsed-ms 180000 --summary --compact --format json",
        "python -B .agents/manage.py review-loop --continue --max-units 5 --summary --compact --format json",
        "python -B .agents/manage.py review-loop --include-validation --max-units 10 --summary --compact --format json",
        "python -B .agents/manage.py review-loop --dry-run --summary --compact --format json",
    )

    review_next_parser = add_parser(
        subparsers,
        "review-next",
        help="run the next compact review unit and record progress",
    )
    add_shared_root_arg(review_next_parser)
    review_next_parser.add_argument("--timeout-seconds", type=int, default=120, help="timeout for the packet command")
    review_next_parser.add_argument("--dry-run", action="store_true", help="report the next command without executing it")
    review_next_parser.add_argument("--summary", action="store_true", help="emit compact review-next fields")
    review_next_parser.add_argument("--compact", action="store_true", help="with --summary, omit iteration detail")
    add_output_format(review_next_parser)
    add_examples(
        review_next_parser,
        "python -B .agents/manage.py review-next --summary --compact --format json",
        "python -B .agents/manage.py review-next --dry-run --summary --compact --format json",
    )

    review_autopilot_parser = add_parser(
        subparsers,
        "review-autopilot",
        help="run bounded review-loop batches, then route to finish when review is complete",
    )
    add_shared_root_arg(review_autopilot_parser)
    review_autopilot_parser.add_argument("--max-cycles", type=int, default=3, help="maximum review-loop batches to run")
    review_autopilot_parser.add_argument("--max-units-per-cycle", type=int, default=20, help="maximum review units per review-loop batch")
    review_autopilot_parser.add_argument("--max-total-units", type=int, default=60, help="maximum review units across all batches")
    review_autopilot_parser.add_argument("--timeout-seconds", type=int, default=120, help="timeout for each packet command")
    review_autopilot_parser.add_argument("--max-estimated-tokens", type=int, default=0, help="total estimated review-token cap; 0 disables the cap")
    review_autopilot_parser.add_argument("--max-elapsed-ms", type=int, default=0, help="total elapsed-time cap; 0 disables the cap")
    review_autopilot_parser.add_argument("--no-reset-stale", action="store_true", help="stop instead of resetting stale review progress")
    review_autopilot_parser.add_argument("--dry-run", action="store_true", help="plan the next bounded loop without executing")
    review_finish_mode = review_autopilot_parser.add_mutually_exclusive_group()
    review_finish_mode.add_argument("--deep", action="store_true", help="route to deep finish after review")
    review_finish_mode.add_argument("--release-full", action="store_true", help="route to exhaustive release-full finish after review")
    review_autopilot_parser.add_argument("--budget-intent", choices=("off", "feature", "optimization"), default="off")
    review_autopilot_parser.add_argument("--summary", action="store_true", help="emit compact autopilot fields")
    review_autopilot_parser.add_argument("--compact", action="store_true", help="with --summary, omit full loop detail")
    add_output_format(review_autopilot_parser)
    add_examples(
        review_autopilot_parser,
        "python -B .agents/manage.py review-autopilot --max-cycles 3 --max-units-per-cycle 20 --max-total-units 60 --max-estimated-tokens 24000 --max-elapsed-ms 540000 --summary --compact --format json",
        "python -B .agents/manage.py review-autopilot --dry-run --summary --compact --format json",
    )

    claim_check_parser = add_parser(
        subparsers,
        "claim-check",
        help="check final-answer claims against compact JSON evidence",
    )
    add_shared_root_arg(claim_check_parser)
    claim_check_parser.add_argument("--input", dest="input_value", help="claim text file or '-' for stdin")
    claim_check_parser.add_argument("--text", help="literal claim text")
    claim_check_parser.add_argument("--evidence-file", dest="evidence_files", action="append", default=[], help="repo-local JSON evidence file; repeatable")
    claim_check_parser.add_argument("--summary", action="store_true", help="emit claim proof counts")
    claim_check_parser.add_argument("--compact", action="store_true", help="with --summary, omit proved claims")
    add_output_format(claim_check_parser)
    add_examples(
        claim_check_parser,
        "python -B .agents/manage.py claim-check --text \"finish passed\" --evidence-file evidence/finish/finish.json --summary --compact --format json",
    )

    budget_trend_parser = add_parser(
        subparsers,
        "budget-trend",
        help="summarize ignored local context-budget trend entries recorded by finish",
    )
    add_shared_root_arg(budget_trend_parser)
    budget_trend_parser.add_argument("--state", help="repo-local JSONL trend path; defaults under .agents/local-ai/cache")
    budget_trend_parser.add_argument("--summary", action="store_true", help="emit compact trend fields")
    budget_trend_parser.add_argument("--compact", action="store_true", help="with --summary, omit empty detail")
    add_output_format(budget_trend_parser)
    add_examples(
        budget_trend_parser,
        "python -B .agents/manage.py budget-trend --summary --compact --format json",
    )

    context_guardrails_parser = add_parser(
        subparsers,
        "context-guardrails",
        help="fail when agent-facing text routes agents to raw generated navigation JSON",
    )
    add_shared_root_arg(context_guardrails_parser)
    context_guardrails_parser.add_argument("--path", dest="paths", action="append", help="scan one repo-local path; defaults to changed files")
    context_guardrails_parser.add_argument("--changed-only", action="store_true", help="scan only changed files or --path values, skipping protected adapter/docs surfaces")
    context_guardrails_parser.add_argument("--summary", action="store_true", help="emit compact finding counts")
    context_guardrails_parser.add_argument("--compact", action="store_true", help="with --summary, omit passing path rows")
    add_output_format(context_guardrails_parser)
    add_examples(
        context_guardrails_parser,
        "python -B .agents/manage.py context-guardrails --summary --compact --format json",
    )

    context_use_parser = add_parser(
        subparsers,
        "context-use-check",
        help="prove compact command packets route through HANDOFF.md and skip raw navigation JSON",
    )
    add_shared_root_arg(context_use_parser)
    context_use_parser.add_argument("--summary", action="store_true", help="emit compact context-use proof fields")
    context_use_parser.add_argument("--compact", action="store_true", help="with --summary, omit trace detail")
    add_output_format(context_use_parser)
    add_examples(
        context_use_parser,
        "python -B .agents/manage.py context-use-check --summary --compact --format json",
    )

    command_budget_parser = add_parser(
        subparsers,
        "command-budget-check",
        help="run compact command budget regression checks for latency and output tokens",
    )
    add_shared_root_arg(command_budget_parser)
    command_budget_parser.add_argument(
        "--profile",
        choices=("fast", "standard"),
        default="fast",
        help="fast checks daily commands; standard also runs check-changed",
    )
    command_budget_parser.add_argument(
        "--command",
        dest="commands",
        action="append",
        help="run one budgeted command id such as next-action or check-changed; may be repeated",
    )
    command_budget_parser.add_argument("--summary", action="store_true", help="emit compact budget counts")
    command_budget_parser.add_argument("--compact", action="store_true", help="with --summary, omit passing command rows")
    add_output_format(command_budget_parser)
    add_examples(
        command_budget_parser,
        "python -B .agents/manage.py command-budget-check --summary --compact --format json",
        "python -B .agents/manage.py command-budget-check --profile standard --summary --compact --format json",
    )

    what_now_parser = add_parser(
        subparsers,
        "what-now",
        help="turn failed command output into the next deterministic action",
    )
    add_shared_root_arg(what_now_parser)
    what_now_parser.add_argument(
        "--input",
        dest="input_value",
        help="failed-output file, '-' for stdin, or omitted for last-validation.txt",
    )
    what_now_parser.add_argument(
        "--from-command",
        dest="from_command",
        help="run a command, capture its output, and explain the first failing fact",
    )
    what_now_parser.add_argument("--last", action="store_true", help="read .agents/local-ai/cache/last-validation.txt")
    what_now_parser.add_argument("--explain-owner", action="store_true", help="include owner routing rationale")
    what_now_parser.add_argument("--write", dest="write_dir", help="write JSON and Markdown triage evidence to this repo-local folder")
    what_now_parser.add_argument("--command-label", default="", help="optional failed command label")
    what_now_parser.add_argument("--summary", action="store_true", help="emit compact triage fields")
    what_now_parser.add_argument("--compact", action="store_true", help="with --summary, omit raw command details")
    add_output_format(what_now_parser)
    add_examples(
        what_now_parser,
        "python -B .agents/manage.py what-now",
        "python -B .agents/manage.py what-now --input .agents/local-ai/cache/last-validation.txt",
    )

    resume_parser = add_parser(
        subparsers,
        "resume-work",
        help="summarize branch, dirty files, latest evidence, and next action",
    )
    add_shared_root_arg(resume_parser)
    resume_depth = resume_parser.add_mutually_exclusive_group()
    resume_depth.add_argument("--fast", action="store_true", help="skip optional evidence-detail scans")
    resume_depth.add_argument("--full", action="store_true", help="include latest evidence detail")
    resume_parser.add_argument("--summary", action="store_true", help="emit branch, change, and evidence counts")
    resume_parser.add_argument("--compact", action="store_true", help="with --summary, omit changed-file rows")
    add_output_format(resume_parser)
    add_examples(resume_parser, "python -B .agents/manage.py resume-work")

    finish_parser = add_parser(
        subparsers,
        "finish",
        help="run sync, workflow hook safety, validation, changed-scope, and benchmark readiness checks",
    )
    add_shared_root_arg(finish_parser)
    finish_parser.add_argument("--deep", action="store_true", help="include impact-selected workflow, install, and benchmark checks")
    finish_mode = finish_parser.add_mutually_exclusive_group()
    finish_mode.add_argument("--skip-benchmark", action="store_true", help="skip an impact-selected benchmark doctor outside release-full")
    finish_parser.add_argument("--budget-intent", choices=("off", "feature", "optimization"), default="off")
    finish_parser.add_argument("--summary", action="store_true", help="emit compact machine packet")
    finish_parser.add_argument("--compact", action="store_true", help="with --summary, omit empty and repeated rows")
    finish_mode.add_argument("--release-full", action="store_true", help="release finish: exhaustive repository, workflow evidence, and benchmark validation")
    finish_parser.add_argument(
        "--commit-packet",
        help="write JSON and Markdown commit-ready evidence files to this repo-local folder",
    )
    add_output_format(finish_parser)
    add_examples(
        finish_parser,
        "python -B .agents/manage.py finish",
        "python -B .agents/manage.py finish --deep",
        "python -B .agents/manage.py finish --budget-intent optimization",
    )

    attachment_parser = add_parser(
        subparsers,
        "attachment-route",
        help="classify an attachment and print the deterministic evidence command",
    )
    add_shared_root_arg(attachment_parser)
    attachment_parser.add_argument("--file", required=True, dest="file_path", help="repo-local attachment path")
    attachment_parser.add_argument(
        "--write-plan",
        help="write a JSON and Markdown inspection plan to this repo-local folder",
    )
    add_output_format(attachment_parser)
    add_examples(attachment_parser, "python -B .agents/manage.py attachment-route --file docs/sample.pdf")

    evidence_parser = add_parser(
        subparsers,
        "evidence-index",
        help="list latest workflow, benchmark, document, validation, and local-AI evidence",
    )
    add_shared_root_arg(evidence_parser)
    evidence_parser.add_argument(
        "--open-latest",
        action="store_true",
        help="highlight the newest useful evidence file per category",
    )
    evidence_parser.add_argument("--summary", action="store_true", help="emit evidence counts and newest paths")
    evidence_parser.add_argument("--compact", action="store_true", help="with --summary, omit evidence row lists")
    add_output_format(evidence_parser)
    add_examples(evidence_parser, "python -B .agents/manage.py evidence-index")

    evidence_verify_parser = add_parser(
        subparsers,
        "evidence-verify",
        help="verify compact evidence raw-output references and digests",
    )
    add_shared_root_arg(evidence_verify_parser)
    evidence_verify_parser.add_argument(
        "--file",
        dest="files",
        action="append",
        help="compact evidence file to verify; defaults to last-validation.txt",
    )
    evidence_verify_parser.add_argument("--summary", action="store_true", help="emit compact verification counts")
    evidence_verify_parser.add_argument("--compact", action="store_true", help="with --summary, omit passing rows")
    add_output_format(evidence_verify_parser)
    add_examples(
        evidence_verify_parser,
        "python -B .agents/manage.py evidence-verify",
        "python -B .agents/manage.py evidence-verify --file evidence/finish/finish.json --summary --compact --format json",
    )

    changed_evidence_parser = add_parser(
        subparsers,
        "changed-evidence",
        help="summarize evidence commands for the current changed files",
    )
    add_shared_root_arg(changed_evidence_parser)
    changed_evidence_parser.add_argument(
        "--write",
        dest="write_dir",
        help="write JSON and Markdown changed-file evidence plan to this repo-local folder",
    )
    changed_evidence_parser.add_argument("--summary", action="store_true", help="emit compact changed-evidence counts")
    changed_evidence_parser.add_argument("--compact", action="store_true", help="with --summary, omit full path and command arrays")
    add_output_format(changed_evidence_parser)
    add_examples(
        changed_evidence_parser,
        "python -B .agents/manage.py changed-evidence",
        "python -B .agents/manage.py changed-evidence --summary --compact --format json",
    )

    change_ledger_parser = add_parser(
        subparsers,
        "change-ledger",
        help="summarize why changed files exist by owner without loading raw diff",
    )
    add_shared_root_arg(change_ledger_parser)
    change_ledger_parser.add_argument("--summary", action="store_true", help="emit compact owner/reason fields")
    change_ledger_parser.add_argument("--compact", action="store_true", help="with --summary, omit path rows")
    add_output_format(change_ledger_parser)
    add_examples(
        change_ledger_parser,
        "python -B .agents/manage.py change-ledger --summary --compact --format json",
    )

    changed_context_parser = add_parser(
        subparsers,
        "changed-context",
        help="emit a compact changed-file navigation packet grouped by owner",
    )
    add_shared_root_arg(changed_context_parser)
    changed_context_parser.add_argument("--summary", action="store_true", help="emit compact owner/read/validation fields")
    changed_context_parser.add_argument("--compact", action="store_true", help="with --summary, omit full path rows")
    add_output_format(changed_context_parser)
    add_examples(
        changed_context_parser,
        "python -B .agents/manage.py changed-context --summary --compact --format json",
    )

    credential_parser = add_parser(
        subparsers,
        "credential-doctor",
        help="check credential/profile readiness without exposing token values",
    )
    add_shared_root_arg(credential_parser)
    credential_parser.add_argument("--configure", action="store_true", help="write/create or update a gitignored local service profile")
    credential_parser.add_argument("--service", choices=repo_service_config.service_choices(), help="service to configure")
    credential_parser.add_argument("--name", help="local profile name, for example customer-a")
    credential_parser.add_argument("--organization-url", help="Azure DevOps organization URL")
    credential_parser.add_argument("--server-url", help="TFS collection/server URL")
    credential_parser.add_argument("--base-url", help="SonarQube base URL")
    credential_parser.add_argument("--project", help="Azure DevOps/TFS project name")
    credential_parser.add_argument("--project-key", help="SonarQube project key")
    credential_parser.add_argument("--pat-env", help="environment variable that contains the Azure DevOps/TFS PAT")
    credential_parser.add_argument("--pat", help="Azure DevOps/TFS PAT to store in the gitignored local profile")
    credential_parser.add_argument("--token-env", help="environment variable that contains the SonarQube token")
    credential_parser.add_argument("--token", help="SonarQube token to store in the gitignored local profile")
    credential_parser.add_argument("--overwrite", action="store_true", help="write/replace an existing local profile with the same name")
    credential_parser.add_argument("--no-input", action="store_true", help="do not prompt; report missing fields instead")
    credential_parser.add_argument("--summary", action="store_true", help="emit credential readiness counts")
    credential_parser.add_argument("--compact", action="store_true", help="with --summary, omit configured profile rows")
    add_output_format(credential_parser)
    add_examples(
        credential_parser,
        "python -B .agents/manage.py credential-doctor",
        "python -B .agents/manage.py credential-doctor --configure --service azure-devops --name customer-a",
        "python -B .agents/manage.py credential-doctor --configure --service sonarqube --name project-a",
    )

    commit_parser = add_parser(
        subparsers,
        "commit-readiness",
        help="check staged files for mainline-safe commit readiness",
    )
    add_shared_root_arg(commit_parser)
    add_output_format(commit_parser)
    add_examples(commit_parser, "python -B .agents/manage.py commit-readiness")


def add_output_format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    parser.add_argument(
        "--json",
        action="store_const",
        const="json",
        dest="output_format",
        help="alias for --format json",
    )
