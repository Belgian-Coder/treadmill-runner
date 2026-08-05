#!/usr/bin/env python3
"""Argparse surface for the repository launcher."""

from __future__ import annotations

import argparse

from repo_support import repo_common as repo
from repo_support import repo_qol
from repo_support import repo_cli_skill_commands

LAUNCHER_DESCRIPTION = "Repository maintenance commands for the skills repo."
MANAGE = "python -B .agents/manage.py"
FORMAT_MARKDOWN_HELP = "output format; default: markdown"
SUMMARY_COMPACT_HELP = "with --summary, omit passing check rows"
INSTALL_TARGET_HELP = "consumer project folder to install into"
TEMP_PARENT_HELP = "optional parent directory for the temporary "
NEW_SKILL_EXAMPLE = f"{MANAGE} new --kind skill --name demo-skill"

def add_shared_root_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", help="repository root; defaults to script parent")


def add_examples(parser: argparse.ArgumentParser, *examples: str) -> None:
    parser.epilog = "Examples:\n" + "\n".join(f"  {example}" for example in examples)


def add_parser(subparsers, name: str, **kwargs) -> argparse.ArgumentParser:
    kwargs.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
    return subparsers.add_parser(name, **kwargs)


def add_harness_profile_args(parser: argparse.ArgumentParser, *, default: str) -> None:
    parser.add_argument(
        "--profile",
        default=default,
        help="payload-defined install profile or alias; validated against .agents/harness-payload.json",
    )
    parser.add_argument(
        "--with-feature",
        action="append",
        default=[],
        help="payload feature bundle to add; repeatable",
    )
    parser.add_argument(
        "--without-feature",
        action="append",
        default=[],
        help="payload feature bundle to remove; repeatable; required core features are protected",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=LAUNCHER_DESCRIPTION,
        prog=f"{MANAGE}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Command groups:\n"
            "  Daily/QoL: status, startup-context, what-now, resume-work, finish, evidence-verify, changed-evidence\n"
            "  Skills/Workflows: skill, workflow, review, eval-skill, workflow eval\n"
            "  Readiness/Generated: setup, sync, check, release-evidence, benchmark, commands\n"
            "  Local AI: local-ai bootstrap/status/policy/task/vision/document/models/bench; local-ai doctor; local-ai integrations\n"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    repo_qol.add_qol_parsers(subparsers, add_parser, add_shared_root_arg, add_examples)

    determinism_parser = add_parser(
        subparsers,
        "determinism-check",
        help="run selected strict commands twice in fresh isolated fixtures and compare outputs and effects",
    )
    add_shared_root_arg(determinism_parser)
    determinism_target = determinism_parser.add_mutually_exclusive_group(required=True)
    determinism_target.add_argument(
        "--changed",
        action="store_true",
        help="replay strict commands owned by changed skill or workflow source paths",
    )
    determinism_target.add_argument(
        "--all",
        action="store_true",
        help="replay commands for every accepted skill and workflow module",
    )
    determinism_parser.add_argument(
        "--deep",
        action="store_true",
        help="release mode: expand each selected module to every strict read-only command",
    )
    determinism_parser.add_argument("--summary", action="store_true", help="emit aggregate counts and command failures")
    determinism_parser.add_argument("--compact", action="store_true", help="with --summary, omit passing command rows")
    determinism_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        determinism_parser,
        f"{MANAGE} determinism-check --changed --summary --compact --format json",
        f"{MANAGE} determinism-check --all --deep --summary --compact --format json",
    )

    commands_parser = add_parser(
        subparsers,
        "commands",
        help="print a generated index of repository launcher commands",
    )
    add_shared_root_arg(commands_parser)
    commands_parser.add_argument(
        "--format",
        choices=("json", "markdown", "tsv"),
        default="markdown",
        dest="output_format",
    )
    commands_parser.add_argument("--summary", action="store_true", help="emit compact command rows")
    commands_parser.add_argument("--compact", action="store_true", help="with --summary, omit per-command rows")
    commands_parser.add_argument("--write", help="write the generated command index to this repo-local path; omit for stdout-only read-only reporting")
    command_shortcuts = commands_parser.add_mutually_exclusive_group()
    for flag, dest, help_text in (
        ("--first-time", "first_time", "show first-time setup commands only"),
        ("--daily", "daily", "show daily commands only"),
        ("--failure", "failure", "show after-failure recovery commands only"),
        ("--harness", "harness", "show harness install and update commands only"),
        ("--documents", "documents", "show document and attachment commands only"),
        ("--workflow", "workflow", "show workflow commands only"),
        ("--release", "release", "show release-readiness commands only"),
    ):
        command_shortcuts.add_argument(flag, action="store_true", dest=dest, help=help_text)
    route_parser = add_parser(
        subparsers,
        "explain-route",
        help="explain which skill or workflow best matches a request",
    )
    add_shared_root_arg(route_parser)
    route_parser.add_argument("query", help="natural-language task or request")
    route_parser.add_argument("--limit", type=int, default=6, help="maximum routes to show")
    route_parser.add_argument("--summary", action="store_true", help="emit selected route and aggregate counts")
    route_parser.add_argument("--compact", action="store_true", help="with --summary, omit candidate route rows")
    route_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        route_parser,
        f"{MANAGE} route \"inspect a PDF attachment\"",
        f"{MANAGE} route \"review a workflow plan\" --format json",
    )

    which_skill_parser = add_parser(
        subparsers,
        "which-skill",
        help="choose the best accepted skill for a natural-language request",
    )
    add_shared_root_arg(which_skill_parser)
    which_skill_parser.add_argument("query", help="natural-language task or request")
    which_skill_parser.add_argument("--limit", type=int, default=5, help="maximum skill routes to show")
    which_skill_parser.add_argument("--summary", action="store_true", help="emit selected skill and aggregate counts")
    which_skill_parser.add_argument("--compact", action="store_true", help="with --summary, omit candidate route rows")
    which_skill_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        which_skill_parser,
        f"{MANAGE} which-skill \"review my API authentication\"",
        f"{MANAGE} which-skill \"inspect a PPTX\" --summary --compact --format json",
    )

    which_workflow_parser = add_parser(
        subparsers,
        "which-workflow",
        help="choose the best accepted workflow for a natural-language request, such as user-story-workflow or bug-ticket-workflow",
    )
    add_shared_root_arg(which_workflow_parser)
    which_workflow_parser.add_argument("query", nargs="?", help="natural-language task or request")
    which_workflow_parser.add_argument("--suite", help="workflow routing regression suite JSON to evaluate")
    which_workflow_parser.add_argument("--check-suite", action="store_true", help="fail when any --suite case misses its expected route")
    which_workflow_parser.add_argument("--limit", type=int, default=5, help="maximum workflow routes to show")
    which_workflow_parser.add_argument("--summary", action="store_true", help="emit selected workflow and aggregate counts")
    which_workflow_parser.add_argument("--compact", action="store_true", help="with --summary, omit candidate route rows")
    which_workflow_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        which_workflow_parser,
        f"{MANAGE} which-workflow \"start a user story for checkout\"",
        f"{MANAGE} which-workflow \"fix a bug ticket\" --summary --compact --format json",
        f"{MANAGE} which-workflow --suite .agents/skills/skill-manager/scripts/fixtures/workflow-routing-regression.json --check-suite --format json",
        f"{MANAGE} workflow start --name user-story-workflow",
        f"{MANAGE} workflow start --name bug-ticket-workflow",
    )

    cost_policy_parser = add_parser(
        subparsers,
        "cost-policy",
        help="report local-first token and context savings policy",
        description=(
            "Validate the fine-grained local-first cost policy from .agents/project-policy.json. "
            "The report keeps local AI preferred for evidence shaping and treats paid small models as explicit fallbacks."
        ),
    )
    add_shared_root_arg(cost_policy_parser)
    cost_policy_parser.add_argument("--workflow", dest="workflow_name", help="show workflow phase budgets for one workflow")
    cost_policy_parser.add_argument("--phase", help="filter by a phase id or phase category")
    cost_policy_parser.add_argument("--task", help="filter by one task route such as validation or review")
    cost_policy_parser.add_argument("--check", action="store_true", help="exit non-zero when the policy is unsafe or over budget")
    cost_policy_parser.add_argument("--summary", action="store_true", help="emit compact policy and issue counts")
    cost_policy_parser.add_argument("--compact", action="store_true", help="with --summary, omit route and phase detail")
    cost_policy_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        cost_policy_parser,
        f"{MANAGE} cost-policy --summary --compact --format json",
        f"{MANAGE} cost-policy --workflow user-story-workflow --phase validation",
    )

    policy_parser = add_parser(
        subparsers,
        "policy",
        help="inspect and configure project-owned repository policies",
        description=(
            "List, explain, validate, set, or reset human-tunable repository limits and warnings. "
            "The complete portable policy is tracked in .agents/project-policy.json. Safety-critical "
            "path, archive, credential, and resource "
            "ceilings are intentionally not configurable."
        ),
    )
    add_shared_root_arg(policy_parser)
    policy_parser.add_argument(
        "policy_action",
        nargs="?",
        choices=("show", "list", "get", "explain", "validate", "init", "refresh", "migrate", "set", "reset"),
        default="show",
    )
    policy_parser.add_argument("path", nargs="?", help="policy path or section, such as limits.agents.warn_chars")
    policy_parser.add_argument("value", nargs="?", help="JSON value for set; quote strings when required by the shell")
    policy_parser.add_argument("--section", help="filter show/list output to one policy section")
    policy_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        policy_parser,
        f"{MANAGE} policy list --section limits --format json",
        f"{MANAGE} policy explain limits.agents.warn_chars",
        f"{MANAGE} policy set limits.agents.warn_chars 3600",
        f"{MANAGE} policy set warnings.health.script.lines off",
        f"{MANAGE} policy reset limits.agents.warn_chars",
        f"{MANAGE} policy refresh --format json",
        f"{MANAGE} policy migrate --format json",
        f"{MANAGE} policy validate --format json",
    )

    start_parser = add_parser(
        subparsers,
        "start-here",
        help="print beginner-friendly next steps for this harness",
        description="Print a small first-read path and the next commands a beginner should run.",
    )
    add_shared_root_arg(start_parser)
    start_parser.add_argument("--target", help="optional target project whose onboarding state should drive the next action")
    start_parser.add_argument("--simple", action="store_true", help="print the shortest beginner path")
    add_harness_profile_args(start_parser, default="standard")
    start_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        start_parser,
        f"{MANAGE} start-here --simple",
        f"{MANAGE} start-here --target D:/Projects/NewProject --profile minimal --format json",
        f"{MANAGE} start-here --simple --profile minimal --format json",
    )

    kickoff_parser = add_parser(
        subparsers,
        "project-kickoff",
        help="plan or run first-use harness setup for a target project",
        description=(
            "Validate the harness copy contract, inspect target readiness, review project context, "
            "and optionally apply the safe install/setup/status sequence."
        ),
    )
    add_shared_root_arg(kickoff_parser)
    kickoff_parser.add_argument("--target", required=True, help=INSTALL_TARGET_HELP)
    add_harness_profile_args(kickoff_parser, default="standard")
    kickoff_parser.add_argument("--apply", action="store_true", help="install/update the harness, run setup, setup --check, and status --fast in the target")
    kickoff_parser.add_argument("--from-request", default="", help="plain-language project goal to include in context-review prompts")
    kickoff_parser.add_argument("--summary", action="store_true", help="emit compact lifecycle status fields")
    kickoff_parser.add_argument("--compact", action="store_true", help="with --summary, omit command groups and workflow recommendation detail")
    kickoff_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        kickoff_parser,
        f"{MANAGE} project-kickoff --target D:/Projects/NewProject",
        f"{MANAGE} project-kickoff --target D:/Projects/NewProject --from-request \"build a customer portal\" --format json",
        f"{MANAGE} project-kickoff --target D:/Projects/NewProject --apply",
    )

    context_review_parser = add_parser(
        subparsers,
        "project-context-review",
        help="inspect generated project context and list missing review facts",
    )
    add_shared_root_arg(context_review_parser)
    context_review_parser.add_argument("--target", required=True, help="project root containing docs/project/project-context.md")
    context_review_parser.add_argument("--from-request", default="", help="plain-language project goal to compare with the context")
    context_review_parser.add_argument("--write-review", action="store_true", help="write docs/project/review/project-context-review artifacts without editing canonical context")
    context_review_parser.add_argument("--summary", action="store_true", help="emit compact review status fields")
    context_review_parser.add_argument("--compact", action="store_true", help="with --summary, omit questions and fact review detail")
    context_review_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        context_review_parser,
        f"{MANAGE} project-context-review --target D:/Projects/NewProject",
        f"{MANAGE} project-context-review --target D:/Projects/NewProject --write-review",
        f"{MANAGE} project-context-review --target D:/Projects/NewProject --from-request \"build a customer portal\" --format json",
    )

    context_apply_parser = add_parser(
        subparsers,
        "project-context-apply-review",
        help="apply answered project-context review facts into canonical project context",
        description=(
            "Read structured answers from docs/project/review/project-context-review.md and preview or write a managed "
            "reviewed-facts section into docs/project/project-context.md. Default is read-only."
        ),
    )
    add_shared_root_arg(context_apply_parser)
    context_apply_parser.add_argument("--target", required=True, help="project root containing docs/project/project-context.md")
    context_apply_parser.add_argument("--review", default="docs/project/review/project-context-review.md", help="target-local structured review Markdown to read")
    context_apply_parser.add_argument("--apply", action="store_true", help="write the managed reviewed-facts section into canonical project context")
    context_apply_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        context_apply_parser,
        f"{MANAGE} project-context-apply-review --target D:/Projects/NewProject",
        f"{MANAGE} project-context-apply-review --target D:/Projects/NewProject --apply",
        f"{MANAGE} project-context-apply-review --target D:/Projects/NewProject --review docs/project/review/project-context-review.md --format json",
    )

    dotnet_context_parser = add_parser(
        subparsers,
        "dotnet-context",
        help="inspect .NET project context without running restore/build/test/package/tool commands",
        description=(
            "Read-only .NET project context inspection for kickoff/setup. Reports static facts and safe CLI probes only; "
            "does not run restore, build, test, package search/list, package install, or tool install."
        ),
    )
    add_shared_root_arg(dotnet_context_parser)
    dotnet_context_parser.add_argument("--target", required=True, help="project root to inspect")
    dotnet_context_parser.add_argument("--no-cli-probes", action="store_true", help="skip safe installed dotnet CLI probes and report static facts only")
    dotnet_context_parser.add_argument("--dotnet-executable", help="explicit dotnet executable path for safe CLI probes; defaults to dotnet on PATH")
    dotnet_context_parser.add_argument("--baseline", help="optional previous dotnet-context JSON report to compare for context drift")
    dotnet_context_parser.add_argument("--solution", action="append", default=[], help="solution path to include; may be repeated and narrows project inventory to selected solution members")
    dotnet_context_parser.add_argument("--project", action="append", default=[], help="project path to include; may be repeated and further narrows project inventory")
    dotnet_context_parser.add_argument("--write-evidence", action="store_true", help="write docs/project/dotnet-context evidence artifacts under the target")
    dotnet_context_parser.add_argument("--evidence-dir", default="docs/project/dotnet-context", help="target-local evidence directory for --write-evidence")
    dotnet_context_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        dotnet_context_parser,
        f"{MANAGE} dotnet-context --target D:/Projects/App",
        f"{MANAGE} dotnet-context --target D:/Projects/App --format json",
        f"{MANAGE} dotnet-context --target D:/Projects/App --no-cli-probes",
        f"{MANAGE} dotnet-context --target D:/Projects/App --solution App.sln --project src/App/App.csproj --format json",
        f"{MANAGE} dotnet-context --target D:/Projects/App --dotnet-executable D:/dotnet/dotnet.exe --format json",
        f"{MANAGE} dotnet-context --target D:/Projects/App --write-evidence",
        f"{MANAGE} dotnet-context --target D:/Projects/App --baseline docs/project/dotnet-context/dotnet-context.json --format json",
    )

    validate_parser = add_parser(subparsers, "validate", help="validate skills and generated files")
    add_shared_root_arg(validate_parser)
    validate_parser.add_argument(
        "--deep",
        action="store_true",
        help="also run skill self-tests, declared eval suites, and git diff whitespace checks",
    )
    validate_parser.add_argument(
        "--tier",
        choices=("fast", "normal", "deep", "release"),
        help="CI-safe validation tier; release emits the release evidence packet",
    )
    add_examples(
        validate_parser,
        f"{MANAGE} check",
        f"{MANAGE} check --deep",
    )

    health_parser = add_parser(
        subparsers,
        "check-repo-health",
        help="summarize repository health without writing files",
    )
    add_shared_root_arg(health_parser)
    health_parser.add_argument("--json", action="store_true", help="print machine-readable health report")
    health_parser.add_argument("--summary", action="store_true", help="emit compact health counts")
    health_parser.add_argument("--compact", action="store_true", help="with --summary, omit nested health rows")
    add_examples(
        health_parser,
        f"{MANAGE} status --full",
        f"{MANAGE} status --full --json",
    )

    fresh_clone_parser = add_parser(
        subparsers,
        "fresh-clone-smoke",
        help="clone this repo into a temporary folder and run setup/check/validation release smoke checks",
    )
    add_shared_root_arg(fresh_clone_parser)
    fresh_clone_parser.add_argument(
        "--source",
        choices=("local", "origin"),
        default="local",
        help="clone from the current working tree path or remote origin; default: local",
    )
    fresh_clone_parser.add_argument("--work-dir", help=f"{TEMP_PARENT_HELP}clone")
    fresh_clone_parser.add_argument("--keep", action="store_true", help="keep the temporary clone for inspection")
    fresh_clone_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        fresh_clone_parser,
        f"{MANAGE} fresh-clone-smoke",
        f"{MANAGE} fresh-clone-smoke --format json",
    )

    clean_room_parser = add_parser(
        subparsers,
        "clean-room-validate",
        help="clone into an isolated D-drive folder and run clean validation gates with evidence logs",
        description=(
            "Create a fresh validation folder, isolate HOME/TEMP/npm/Playwright caches, "
            "clone from origin with local fallback, and run the standard clean-room gates."
        ),
    )
    add_shared_root_arg(clean_room_parser)
    clean_room_parser.add_argument("--work-dir", help="D-drive validation folder; defaults under D:/AgentValidation/skills-repo")
    clean_room_parser.add_argument("--source", choices=("auto", "local", "origin"), default="auto")
    clean_room_parser.add_argument("--quick", action="store_true", help="run the shorter clean-room gate set")
    clean_room_parser.add_argument("--remove", action="store_true", help="remove the clean-room folder after writing the report")
    clean_room_parser.add_argument("--summary", action="store_true", help="emit compact counts and failures")
    clean_room_parser.add_argument("--compact", action="store_true", help=SUMMARY_COMPACT_HELP)
    clean_room_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        clean_room_parser,
        f"{MANAGE} clean-room-validate --work-dir D:/AgentValidation/skills-repo/run-YYYYMMDD",
        f"{MANAGE} clean-room-validate --quick --format json",
    )

    env_preflight_parser = add_parser(
        subparsers,
        "environment-preflight",
        help="report required and optional local tool, credential, and validation-path readiness",
    )
    add_shared_root_arg(env_preflight_parser)
    env_preflight_parser.add_argument("--summary", action="store_true", help="emit readiness counts only")
    env_preflight_parser.add_argument("--compact", action="store_true", help=SUMMARY_COMPACT_HELP)
    env_preflight_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        env_preflight_parser,
        f"{MANAGE} environment-preflight --summary --compact --format json",
    )

    portable_tools_parser = add_parser(
        subparsers,
        "portable-tools",
        help="validate pinned portable tool manifests and report repo-local cache status",
    )
    add_shared_root_arg(portable_tools_parser)
    portable_tools_parser.add_argument("--check", action="store_true", help="return non-zero when manifests are invalid")
    portable_tools_parser.add_argument(
        "--require-installed",
        action="store_true",
        help="also require the current platform portable binaries to be installed and verified",
    )
    portable_tools_parser.add_argument("--summary", action="store_true", help="emit compact manifest and install counts")
    portable_tools_parser.add_argument("--compact", action="store_true", help=SUMMARY_COMPACT_HELP)
    portable_tools_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        portable_tools_parser,
        f"{MANAGE} portable-tools --summary --compact --format json",
        f"{MANAGE} portable-tools --check --require-installed",
    )

    command_docs_parser = add_parser(
        subparsers,
        "command-docs-smoke",
        help="smoke-check documented manage.py command examples for known unsafe command/flag drift",
    )
    add_shared_root_arg(command_docs_parser)
    command_docs_parser.add_argument("--summary", action="store_true", help="emit command and issue counts")
    command_docs_parser.add_argument("--compact", action="store_true", help=SUMMARY_COMPACT_HELP)
    command_docs_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        command_docs_parser,
        f"{MANAGE} command-docs-smoke --summary --compact --format json",
    )

    install_smoke_parser = add_parser(
        subparsers,
        "install-harness-smoke",
        help="install the harness into a temporary target and run first-time setup/workflow smoke checks",
    )
    add_shared_root_arg(install_smoke_parser)
    install_smoke_parser.add_argument("--work-dir", help=f"{TEMP_PARENT_HELP}install target")
    install_smoke_parser.add_argument("--keep", action="store_true", help="keep the temporary install target for inspection")
    install_smoke_parser.add_argument(
        "--fast",
        action="store_true",
        help="skip local-AI bootstrap and workflow start/resume; still checks install clean-state, setup, and project context",
    )
    install_smoke_parser.add_argument(
        "--workflow-name",
        default="user-story-workflow",
        help="workflow to start/resume in the installed target; default: user-story-workflow",
    )
    install_smoke_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        install_smoke_parser,
        f"{MANAGE} install-harness-smoke --fast",
        f"{MANAGE} install-harness-smoke",
        f"{MANAGE} install-harness-smoke --format json",
    )

    install_parser = add_parser(
        subparsers,
        "install-harness",
        help="copy this reusable harness into a consumer project",
        description=(
            "Copy the reusable harness surface into a target project while excluding "
            "Git state, model/cache payloads, local secrets, and workflow run history."
        ),
    )
    add_shared_root_arg(install_parser)
    install_parser.add_argument("--target", required=True, help=INSTALL_TARGET_HELP)
    add_harness_profile_args(install_parser, default="standard")
    install_parser.add_argument("--dry-run", action="store_true", help="show planned writes without copying files")
    install_parser.add_argument("--force", action="store_true", help="overwrite differing target files")
    install_parser.add_argument(
        "--run-setup-check",
        action="store_true",
        help="initialize the copied target, then run setup --check without linking user-profile skills",
    )
    install_parser.add_argument("--install-rg-portable", action="store_true", help="download pinned portable ripgrep in the target after copying")
    install_parser.add_argument("--bootstrap-local-ai", action="store_true", help="write/check local AI config in the target without downloading models")
    install_parser.add_argument("--download-ai-models", action="store_true", help="download configured local AI models in the target after copying")
    install_parser.add_argument("--local-ai-profile", action="append", default=[], help="local AI profile to bootstrap/download; repeatable")
    install_parser.add_argument("--max-download-gb", type=float, help="override local AI bootstrap max_download_gb")
    install_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        install_parser,
        f"{MANAGE} install-harness --target D:/Projects/NewProject --dry-run",
        f"{MANAGE} install-harness --target D:/Projects/NewProject --profile minimal",
        f"{MANAGE} install-harness --target D:/Projects/NewProject --run-setup-check --install-rg-portable --bootstrap-local-ai",
    )

    harness_status_parser = add_parser(
        subparsers,
        "harness-status",
        help="show the locked harness version and optionally check stable upstream tags",
    )
    add_shared_root_arg(harness_status_parser)
    harness_status_parser.add_argument("--check-upstream", action="store_true", help="fetch the bounded stable Git-tag index")
    harness_status_parser.add_argument("--offline", action="store_true", help="use only the cached tag index")
    harness_status_parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    add_examples(harness_status_parser, f"{MANAGE} harness-status --check-upstream --format json")

    harness_update_parser = add_parser(
        subparsers,
        "harness-update",
        help="download and preview or transactionally apply an immutable tagged harness archive",
    )
    add_shared_root_arg(harness_update_parser)
    harness_update_parser.add_argument("--to", required=True, dest="target_tag", help="stable vMAJOR.MINOR.PATCH tag or latest")
    harness_update_parser.add_argument("--apply", action="store_true", help="apply the complete collision-free preview")
    harness_update_parser.add_argument("--archive", help="explicit local commit archive for a restricted environment")
    harness_update_parser.add_argument("--archive-metadata", help="JSON proof for --archive: repository, tag, commit, payload_digest")
    harness_update_parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    add_examples(
        harness_update_parser,
        f"{MANAGE} harness-update --to latest",
        f"{MANAGE} harness-update --to v1.0.0 --apply --format json",
    )

    harness_rollback_parser = add_parser(
        subparsers,
        "harness-rollback",
        help="restore a previously applied harness update transaction",
    )
    add_shared_root_arg(harness_rollback_parser)
    harness_rollback_parser.add_argument("--transaction", required=True, help="transaction id reported by harness-update")
    harness_rollback_parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")

    harness_adopt_parser = add_parser(
        subparsers,
        "harness-adopt",
        help="verify a legacy ignored install manifest and create the tracked harness lock",
    )
    add_shared_root_arg(harness_adopt_parser)
    harness_adopt_parser.add_argument("--tag", required=True, help="stable annotated tag matching the legacy installation")
    harness_adopt_parser.add_argument("--archive", help="explicit local commit archive for a restricted environment")
    harness_adopt_parser.add_argument("--archive-metadata", help="JSON proof for --archive: repository, tag, commit, payload_digest")
    harness_adopt_parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")

    harness_release_parser = add_parser(
        subparsers,
        "harness-release-check",
        help="validate a clean annotated stable semantic harness tag and payload digests",
    )
    add_shared_root_arg(harness_release_parser)
    harness_release_parser.add_argument("--tag", help="tag to validate; defaults to the exact tag at HEAD")
    harness_release_parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")

    wizard_parser = add_parser(
        subparsers,
        "install-wizard",
        help="guide a beginner through harness install choices",
        description="Ask or accept a few install choices, then print or run the recommended install-harness command.",
    )
    add_shared_root_arg(wizard_parser)
    wizard_parser.add_argument("--target", help=INSTALL_TARGET_HELP)
    add_harness_profile_args(wizard_parser, default="minimal")
    wizard_parser.add_argument("--apply", action="store_true", help="run the install after selecting options")
    wizard_parser.add_argument("--force", action="store_true", help="overwrite differing target files when applying")
    wizard_parser.add_argument("--run-setup-check", action="store_true", help="run setup --check after copying")
    wizard_parser.add_argument("--install-rg-portable", action="store_true", help="install verified portable ripgrep after copying")
    wizard_parser.add_argument("--bootstrap-local-ai", action="store_true", help="prepare local AI config without model downloads")
    wizard_parser.add_argument("--download-ai-models", action="store_true", help="download local AI model payloads")
    wizard_parser.add_argument("--no-input", action="store_true", help="do not prompt; use flags only")
    wizard_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        wizard_parser,
        f"{MANAGE} install-wizard --target D:/Projects/NewProject",
        f"{MANAGE} install-wizard --target D:/Projects/NewProject --no-input --profile minimal --run-setup-check --format json",
        f"{MANAGE} install-wizard --target D:/Projects/NewProject --apply --install-rg-portable",
    )

    copy_contract_parser = add_parser(
        subparsers,
        "validate-copy-contract",
        help="validate the harness payload manifest and copy exclusions",
    )
    add_shared_root_arg(copy_contract_parser)
    add_harness_profile_args(copy_contract_parser, default="standard")
    copy_contract_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        copy_contract_parser,
        f"{MANAGE} validate-copy-contract",
        f"{MANAGE} validate-copy-contract --format json",
    )

    promote_parser = add_parser(
        subparsers,
        "harness-promote",
        help="compare consumer harness edits and optionally promote selected files back to this source repo",
    )
    add_shared_root_arg(promote_parser)
    promote_parser.add_argument("--target", required=True, help="consumer project folder to compare")
    add_harness_profile_args(promote_parser, default="standard")
    promote_parser.add_argument("--dry-run", action="store_true", help="show classification without copying files")
    promote_parser.add_argument("--apply", action="store_true", help="copy selected --paths from the consumer back into the source harness")
    promote_parser.add_argument("--paths", nargs="+", default=[], help="explicit harness-owned paths to promote when --apply is used")
    promote_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        promote_parser,
        f"{MANAGE} harness-promote --target D:/Projects/NewProject --dry-run",
        f"{MANAGE} harness-promote --target D:/Projects/NewProject --apply --paths docs/agent-start.md",
    )

    public_export_parser = add_parser(
        subparsers,
        "public-export",
        help="copy a sanitized public export of the harness into a target folder",
    )
    add_shared_root_arg(public_export_parser)
    public_export_parser.add_argument("--target", required=True, help="export target folder")
    add_harness_profile_args(public_export_parser, default="standard")
    public_export_parser.add_argument("--dry-run", action="store_true", help="show planned export without copying files")
    public_export_parser.add_argument("--force", action="store_true", help="overwrite differing files in the export target")
    public_export_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        public_export_parser,
        f"{MANAGE} public-export --target temp/public-export --dry-run",
        f"{MANAGE} public-export --target temp/public-export",
    )

    release_parser = add_parser(
        subparsers,
        "release-evidence",
        help="produce a release-readiness evidence packet without publishing anything",
    )
    add_shared_root_arg(release_parser)
    release_parser.add_argument(
        "--skip-fresh-clone",
        action="store_true",
        help="skip the temporary fresh-clone smoke section",
    )
    release_parser.add_argument(
        "--source",
        choices=("local", "origin"),
        default="local",
        help="fresh-clone source when not skipped; default: local",
    )
    release_parser.add_argument(
        "--include-deep-validation",
        action="store_true",
        help="run check --deep inside the release evidence packet",
    )
    release_scope = release_parser.add_mutually_exclusive_group()
    release_scope.add_argument("--github-only", action="store_true", help="emit only GitHub hygiene/action readiness")
    release_scope.add_argument("--local-only", action="store_true", help="skip GitHub hygiene/action readiness")
    release_parser.add_argument("--summary", action="store_true", help="emit compact release evidence counts")
    release_parser.add_argument("--compact", action="store_true", help=SUMMARY_COMPACT_HELP)
    release_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        release_parser,
        f"{MANAGE} release-evidence",
        f"{MANAGE} release-evidence --skip-fresh-clone --format json",
    )

    reference_parser = add_parser(
        subparsers,
        "reference-refresh",
        help="run reference-refresh report, dry-run, or write with workflow defaults",
        description=(
            "Delegate reference-refresh report, dry-run, and write modes to external-reference-manager. "
            "Report mode is read-only. Dry-run does not fetch or write. Write mode may clone, fetch, "
            "reset with --allow-reset, and write caller-owned pins/cards."
        ),
    )
    add_shared_root_arg(reference_parser)
    reference_parser.add_argument(
        "--mode",
        choices=("report", "dry-run", "write"),
        default="report",
        help="report is read-only; dry-run plans without fetching/writing; write performs approved refresh",
    )
    reference_parser.add_argument(
        "--manifest",
        default="automations/reference-refresh/artifacts/references/reference-manifest.json",
        help="reference manifest inside the workspace",
    )
    reference_parser.add_argument(
        "--output-root",
        default="automations/reference-refresh/artifacts/references",
        help="caller-owned reference output folder inside the workspace",
    )
    reference_parser.add_argument("--no-fetch", action="store_true", help="do not fetch; use existing local mirrors only")
    reference_parser.add_argument(
        "--allow-reset",
        action="store_true",
        help="allow write mode to reset and clean dirty reference mirrors",
    )
    reference_parser.add_argument("--stale-days", type=int, default=180, help="warn when pinned commit is older than this")
    reference_parser.add_argument(
        "--format",
        choices=("text", "json", "markdown"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        reference_parser,
        f"{MANAGE} reference-refresh --mode report --format markdown",
        f"{MANAGE} reference-refresh --mode dry-run --format json",
        f"{MANAGE} reference-refresh --mode write --no-fetch",
    )

    setup_parser = add_parser(
        subparsers,
        "setup",
        help="set up this repository and user-level skill links",
        description=(
            "Set up this repository for beginner use. By default this syncs generated "
            "artifacts, links accepted skills into user-level tool folders, validates "
            "the repository, and prints first-agent guidance."
        ),
    )
    add_shared_root_arg(setup_parser)
    setup_mode = setup_parser.add_mutually_exclusive_group()
    setup_mode.add_argument("--check", action="store_true", help="read-only setup verification")
    setup_mode.add_argument("--dry-run", action="store_true", help="show intended setup writes")
    setup_parser.add_argument("--no-link-skills", action="store_true", help="skip user-level skill linking")
    setup_parser.add_argument(
        "--targets",
        nargs="+",
        choices=repo.TOOLS,
        default=["Codex", "Claude", "Copilot"],
        help="tools to link; default: Codex Claude Copilot",
    )
    setup_parser.add_argument("--skill-source-path")
    setup_parser.add_argument("--codex-skills-path")
    setup_parser.add_argument("--claude-skills-path")
    setup_parser.add_argument("--copilot-skills-path")
    setup_parser.add_argument(
        "--mode",
        choices=("auto", "link", "copy"),
        default="auto",
        help="skill installation mode; default: auto",
    )
    setup_parser.add_argument("--deep", action="store_true", help="run deep validation")
    setup_parser.add_argument("--doctor", action="store_true", help="include harness and local AI readiness diagnostics")
    setup_parser.add_argument("--install-rg", action="store_true", help="install ripgrep when missing using a detected package manager")
    setup_parser.add_argument("--install-rg-portable", action="store_true", help="download pinned portable ripgrep into the repo-local ignored tool cache")
    setup_parser.add_argument("--no-tool-prompts", action="store_true", help="do not prompt for optional tool installs during interactive setup")
    setup_parser.add_argument("--offline", action="store_true", help="perform no network access, including harness update checks")
    setup_parser.add_argument("--summary", action="store_true", help="emit setup status counts")
    setup_parser.add_argument("--compact", action="store_true", help="with --summary, omit tool target rows and captured output")
    setup_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        setup_parser,
        f"{MANAGE} setup",
        f"{MANAGE} setup --install-rg-portable",
        f"{MANAGE} setup --install-rg",
        f"{MANAGE} setup --doctor",
        f"{MANAGE} setup --check",
        f"{MANAGE} setup --dry-run",
    )

    changed_parser = add_parser(
        subparsers,
        "check-changed",
        help="run checks implied by changed files without writing generated navigation maps",
    )
    add_shared_root_arg(changed_parser)
    changed_parser.add_argument(
        "--deep",
        action="store_true",
        help="also run changed skills' scripts/run_self_tests.py when present",
    )
    changed_parser.add_argument(
        "--verbose",
        action="store_true",
        help="include captured output for successful checks and unclassified paths",
    )
    changed_parser.add_argument(
        "--refresh-navigation",
        action="store_true",
        help="safely refresh stale generated navigation maps before running changed-scope checks",
    )
    changed_parser.add_argument("--summary", action="store_true", help="emit compact counts and next command")
    changed_parser.add_argument("--compact", action="store_true", help="with --summary, omit full check and path arrays")
    changed_parser.add_argument(
        "--full",
        action="store_true",
        help="with --format json, emit full raw check details instead of the default compact budgeted summary",
    )
    changed_parser.add_argument(
        "--record-progress",
        action="store_true",
        help="write ignored validation progress evidence under .agents/local-ai/cache",
    )
    changed_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help=FORMAT_MARKDOWN_HELP,
    )
    add_examples(
        changed_parser,
        f"{MANAGE} check-changed",
        f"{MANAGE} check-changed --summary --compact --format json",
        f"{MANAGE} check-changed --full --format json",
        f"{MANAGE} check-changed --record-progress --format json",
        f"{MANAGE} check-changed --refresh-navigation --summary --compact --format json",
        f"{MANAGE} check-changed --deep --verbose",
    )

    review_packet_parser = add_parser(
        subparsers,
        "review-packet",
        help="emit or write the compact owner/risk packet for a large changed diff",
    )
    add_shared_root_arg(review_packet_parser)
    review_packet_parser.add_argument("--deep", action="store_true", help="include deep validation commands in the packet")
    review_packet_parser.add_argument("--owner", help="emit only one owner-scoped packet, such as skill:skill-manager")
    review_packet_parser.add_argument("--path", dest="paths", action="append", help="with --owner, focus on one changed repo-local path; may be repeated")
    review_packet_parser.add_argument("--hunk", dest="hunks", action="append", help="with --owner and --path, focus on one changed hunk id such as h001; may be repeated")
    review_packet_parser.add_argument("--write", dest="write_dir", help="write review packet, review plan, and cost report artifacts to this repo-local folder")
    review_packet_parser.add_argument("--summary", action="store_true", help="emit compact packet fields")
    review_packet_parser.add_argument("--compact", action="store_true", help="with --summary, omit nonessential rows")
    review_packet_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help=FORMAT_MARKDOWN_HELP,
    )
    add_examples(
        review_packet_parser,
        f"{MANAGE} review-packet --summary --compact --format json",
        f"{MANAGE} review-packet --owner skill:skill-manager --summary --compact --format json",
        f"{MANAGE} review-packet --owner skill:skill-manager --path .agents/skills/skill-manager/scripts/repo_support/repo_changed.py --summary --compact --format json",
        f"{MANAGE} review-packet --owner skill:skill-manager --path .agents/skills/skill-manager/scripts/run_self_tests.py --hunk h001 --summary --compact --format json",
        f"{MANAGE} review-packet --write evidence/review-packet",
    )

    handoff_packet_parser = add_parser(
        subparsers,
        "handoff-packet",
        help="emit the compact route-first packet for a fresh agent or subagent",
    )
    add_shared_root_arg(handoff_packet_parser)
    handoff_packet_parser.add_argument("--owner", help="focus the handoff on one owner, such as skill:skill-manager")
    handoff_packet_parser.add_argument("--summary", action="store_true", help="emit compact packet fields")
    handoff_packet_parser.add_argument("--compact", action="store_true", help="with --summary, omit repeated detail rows")
    handoff_packet_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help=FORMAT_MARKDOWN_HELP,
    )
    add_examples(
        handoff_packet_parser,
        f"{MANAGE} handoff-packet --summary --compact --format json",
        f"{MANAGE} handoff-packet --owner skill:skill-manager --summary --compact --format json",
    )

    fresh_agent_packet_parser = add_parser(
        subparsers,
        "fresh-agent-packet",
        help="emit the minimal packet a clean-context agent should load before work",
    )
    add_shared_root_arg(fresh_agent_packet_parser)
    fresh_agent_packet_parser.add_argument("--owner", help="focus the packet on one owner, such as skill:skill-manager")
    fresh_agent_packet_parser.add_argument("--summary", action="store_true", help="emit compact packet fields")
    fresh_agent_packet_parser.add_argument("--compact", action="store_true", help="with --summary, omit nested handoff detail")
    fresh_agent_packet_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help=FORMAT_MARKDOWN_HELP,
    )
    add_examples(
        fresh_agent_packet_parser,
        f"{MANAGE} fresh-agent-packet --summary --compact --format json",
        f"{MANAGE} fresh-agent-packet --owner skill:skill-manager --summary --compact --format json",
    )

    portability_parser = add_parser(
        subparsers,
        "portable-constraints",
        help="fail on nonportable changed-file assumptions such as hardware defaults, personal paths, or admin-only installs",
    )
    add_shared_root_arg(portability_parser)
    portability_parser.add_argument("--changed", action="store_true", help="scan changed files; this is the default")
    portability_parser.add_argument("--path", dest="paths", action="append", help="scan one repo-local path; may be repeated")
    portability_parser.add_argument("--summary", action="store_true", help="emit compact counts and findings")
    portability_parser.add_argument("--compact", action="store_true", help="with --summary, omit scanned/skipped path arrays")
    portability_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
        help=FORMAT_MARKDOWN_HELP,
    )
    add_examples(
        portability_parser,
        f"{MANAGE} portable-constraints --changed --summary --compact --format json",
        f"{MANAGE} portable-constraints --path automations/local-ai-benchmark-workflow/docs/benchmark-lessons.md",
    )

    additions_parser = add_parser(
        subparsers,
        "check-additions",
        help="fail when new files lack an owning skill/workflow or generated-source match",
    )
    add_shared_root_arg(additions_parser)
    additions_parser.add_argument("--summary", action="store_true", help="emit compact counts and issues")
    additions_parser.add_argument("--compact", action="store_true", help="accepted with --summary for command-surface consistency")
    additions_parser.add_argument("--verbose", action="store_true", help="include classified new/source/generated paths")
    additions_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help=FORMAT_MARKDOWN_HELP,
    )
    add_examples(
        additions_parser,
        f"{MANAGE} check-additions",
        f"{MANAGE} check-additions --summary --format json",
        f"{MANAGE} check-additions --summary --compact --format json",
    )

    candidate_source_parser = add_parser(
        subparsers,
        "audit-candidate-source",
        help="audit external skill/agent source routing before import",
    )
    add_shared_root_arg(candidate_source_parser)
    candidate_source_parser.add_argument("source", help="candidate source folder to scan for routing references")
    candidate_source_parser.add_argument("--warn-threshold", type=float, default=0.55)
    candidate_source_parser.add_argument("--error-threshold", type=float, default=0.75)
    candidate_source_parser.add_argument("--max-pairs", type=int, default=50)
    candidate_source_parser.add_argument("--summary", action="store_true", help="emit compact audit fields")
    candidate_source_parser.add_argument("--compact", action="store_true", help="with --summary, omit passing issue/warning arrays")
    candidate_source_parser.add_argument("--strict", action="store_true", help="return non-zero when the audit reports issues")
    candidate_source_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
        help=FORMAT_MARKDOWN_HELP,
    )
    add_examples(
        candidate_source_parser,
        f"{MANAGE} audit-candidate-source temp/dotnet-artisan-main/plugins/dotnet-artisan",
        f"{MANAGE} audit-candidate-source temp/dotnet-artisan-main/plugins/dotnet-artisan --summary --format json",
    )

    sync_all_parser = add_parser(
        subparsers,
        "sync",
        help="sync or check all generated repository artifacts",
        description="write generated repository artifacts by default; use --check for read-only drift detection",
    )
    add_shared_root_arg(sync_all_parser)
    sync_all_parser.add_argument(
        "--check",
        action="store_true",
        help="read-only: fail if any generated artifact is out of sync",
    )
    add_examples(
        sync_all_parser,
        f"{MANAGE} sync",
        f"{MANAGE} sync --check",
    )

    format_json_parser = add_parser(
        subparsers,
        "format-json",
        help="pretty-print tracked JSON files with the repo canonical format",
    )
    add_shared_root_arg(format_json_parser)
    format_json_parser.add_argument(
        "--check",
        action="store_true",
        help="fail if any tracked JSON file is not already pretty-printed",
    )
    format_json_parser.add_argument("--summary", action="store_true", help="emit compact counts")
    format_json_parser.add_argument("--compact", action="store_true", help="with --summary, omit changed path rows")
    format_json_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        format_json_parser,
        f"{MANAGE} format-json",
        f"{MANAGE} format-json --check",
    )

    syntax_parser = add_parser(
        subparsers,
        "syntax-check",
        help="parse Python files without writing __pycache__ bytecode",
        description="Check Python syntax with ast.parse across repo-local files or directories without py_compile or bytecode writes.",
    )
    add_shared_root_arg(syntax_parser)
    syntax_parser.add_argument("--paths", nargs="+", required=True, help="repo-local Python files or directories to parse")
    syntax_parser.add_argument("--summary", action="store_true", help="accepted for consistency; output already summarizes counts")
    syntax_parser.add_argument("--compact", action="store_true", help="omit success prose in markdown output")
    syntax_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        syntax_parser,
        f"{MANAGE} syntax-check --paths .agents/skills automations --format json",
        f"{MANAGE} syntax-check --paths .agents/manage.py --format markdown",
    )

    local_ai_parser = add_parser(
        subparsers,
        "local-ai",
        help="inspect or explicitly prepare the optional repo-local AI routing bundle",
        description=(
            "inspect or explicitly prepare the optional repo-local AI routing bundle; "
            "diagnostics may inspect local settings, profiles, caches, or host state. "
            "Strict no-profile/no-cache dogfood uses local-ai-helper strict_read_only_commands."
        ),
    )
    add_shared_root_arg(local_ai_parser)
    local_ai_parser.add_argument("local_ai_args", nargs=argparse.REMAINDER)
    add_examples(
        local_ai_parser,
        f"{MANAGE} local-ai readiness --summary --compact --json",
        f"{MANAGE} local-ai policy --summary --compact --json",
        f"{MANAGE} local-ai doctor --quick --summary --compact --json",
        f"{MANAGE} local-ai runtime doctor --summary --compact --json",
        f"{MANAGE} local-ai status",
        f"{MANAGE} local-ai integrations --target skill",
        f"{MANAGE} local-ai integrations --target workflow",
        f"{MANAGE} local-ai policy",
        f"{MANAGE} local-ai policy explain validation-triage --owner skill-manager",
        f"{MANAGE} local-ai task --task validation-triage --input .agents/local-ai/cache/last-validation.txt",
        f"{MANAGE} local-ai vision describe --image docs/screenshot.png",
        f"{MANAGE} local-ai vision pdf --pdf docs/sample.pdf --pages 1-5",
        f"{MANAGE} local-ai catalog",
        f"{MANAGE} local-ai models",
        f"{MANAGE} local-ai select --profile nemotron3-nano4b",
        f"{MANAGE} local-ai bootstrap --dry-run",
        f"{MANAGE} local-ai bootstrap",
        f"{MANAGE} local-ai bootstrap --run-model",
        f"{MANAGE} local-ai bench",
        f"{MANAGE} local-ai bench --detached-command --standard-metrics",
        f"{MANAGE} local-ai runtime doctor",
        f"{MANAGE} local-ai models inventory --disk",
        f"{MANAGE} local-ai download",
        f"{MANAGE} local-ai download --profile qwen3vl-2b-q4",
        f"{MANAGE} local-ai doctor --quick",
        f"{MANAGE} local-ai doctor --full --profile nemotron3-nano4b",
        f"{MANAGE} local-ai resources --json",
    )

    benchmark_parser = add_parser(
        subparsers,
        "benchmark",
        help="benchmark utility group owned by agent-benchmarking",
    )
    add_shared_root_arg(benchmark_parser)
    benchmark_parser.add_argument("benchmark_args", nargs=argparse.REMAINDER)
    add_examples(
        benchmark_parser,
        f"{MANAGE} benchmark doctor --suite automations/agent-benchmarking/suites/workflow-evals.json",
        f"{MANAGE} benchmark release-gate",
        f"{MANAGE} benchmark tool-call --check --json",
        f"{MANAGE} benchmark routing-eval --suite automations/agent-benchmarking/suites/routing-evidence-real-use.json --check-suite --format json",
        f"{MANAGE} benchmark capability-matrix --baseline-root D:/baseline --candidate-root . --format json --compact",
        f"{MANAGE} benchmark compare-latest automations/agent-benchmarking/runs",
        f"{MANAGE} benchmark compare-matrix automations/agent-benchmarking/runs/run-a automations/agent-benchmarking/runs/run-b",
        f"{MANAGE} benchmark friction --summary --compact --format json",
        f"{MANAGE} benchmark lesson-promotions --format markdown",
    )

    feedback_parser = add_parser(
        subparsers,
        "feedback",
        help="record, summarize, export, review-digest, and convert corrections into eval packets",
    )
    add_shared_root_arg(feedback_parser)
    feedback_parser.add_argument("feedback_args", nargs=argparse.REMAINDER)
    add_examples(
        feedback_parser,
        f"{MANAGE} feedback record --target-kind skill --target skill-manager --summary \"check failed\" --bad \"generated routing was stale\"",
        f"{MANAGE} feedback summary --all --summary --compact --format json",
        f"{MANAGE} feedback export --all --min-count 2 --output evidence/feedback",
        f"{MANAGE} feedback review-digest --corrections evidence/corrections.json --format json",
        f"{MANAGE} feedback eval-packet --corrections evidence/corrections.json --output evidence/correction-evals.json",
        f"{MANAGE} feedback clear --all --confirm-truncate --reason \"processed into action plan\" --action-plan automations/feedback-improvement-workflow/runs/<run-id>/action-plan.md --dry-run --format json",
    )

    skill_group_parser = add_parser(
        subparsers,
        "skill",
        help="skill utility group",
    )
    add_shared_root_arg(skill_group_parser)
    skill_group_parser.add_argument("skill_args", nargs=argparse.REMAINDER)
    add_examples(
        skill_group_parser,
        f"{MANAGE} skill doctor --skill .agents/skills/skill-manager",
    )

    workflow_group_parser = add_parser(
        subparsers,
        "workflow",
        help="workflow utility group",
        description=(
            "Workflow utility front door. Common actions: propose, recipes, create, adjust, start, resume, finish, "
            "scorecard, smoke, eval, hooks, context, checkpoint, workers, and analytics. For read-only/offline use, "
            "prefer propose/which-workflow, --help, --check, --dry-run, and scorecard --no-lifecycle."
        ),
        epilog=(
            "Examples:\n"
            "  python -B .agents/manage.py workflow propose --from-request \"review release evidence\" --summary --compact --format json\n"
            "  python -B .agents/manage.py workflow create --from-request \"review release evidence\" --name release-evidence-workflow --write\n"
            "  python -B .agents/manage.py workflow adjust --name user-story-workflow --from-request \"tighten validation\" --plan\n"
            "  python -B .agents/manage.py workflow start --name user-story-workflow --summary --compact --format json\n"
            "  python -B .agents/manage.py workflow resume --name user-story-workflow --summary --compact --format json\n"
            "  python -B .agents/manage.py workflow scorecard --all --summary --compact --format json\n"
            "  python -B .agents/manage.py workflow scorecard --all --summary --compact --format json --no-lifecycle\n"
            "  python -B .agents/manage.py workflow smoke --name user-story-workflow --dry-run --summary --compact --format json\n"
        ),
    )
    add_shared_root_arg(workflow_group_parser)
    workflow_group_parser.add_argument("workflow_args", nargs=argparse.REMAINDER)

    sync_parser = add_parser(
        subparsers,
        "sync-instructions",
        help="generate project instruction adapters from AGENTS.md",
    )
    add_shared_root_arg(sync_parser)
    sync_parser.add_argument(
        "--check",
        action="store_true",
        help="fail if generated adapters are out of sync",
    )
    add_examples(
        sync_parser,
        f"{MANAGE} sync-instructions",
        f"{MANAGE} sync-instructions --check",
    )

    routing_parser = add_parser(
        subparsers,
        "sync-skill-routing",
        help="generate accepted-skill routing Markdown and registry JSON artifacts",
    )
    add_shared_root_arg(routing_parser)
    routing_parser.add_argument(
        "--check",
        action="store_true",
        help="fail if generated accepted-skill routing Markdown or registry JSON is out of sync",
    )
    routing_parser.add_argument(
        "--deep",
        action="store_true",
        help="include analyzer-derived risk/dependency signals; slower for large skill sets",
    )
    add_examples(
        routing_parser,
        f"{MANAGE} sync-skill-routing",
        f"{MANAGE} sync-skill-routing --check",
    )

    for command_name, help_text in [
        ("eval-workflow", "run deterministic local eval assertions for one workflow"),
        ("index-workflow-runs", "index workflow-local runs folders"),
        ("validate-automations", "validate automation workflow modules"),
        ("sync-automation-routing", "write generated automation workflow routing and registry artifacts; use --check for read-only drift detection"),
        ("create-workflow", "scaffold a Markdown-first automation workflow module"),
        ("create-workflow-from-request", "dry-run or write a workflow scaffold from a plain-language request"),
        ("propose-workflow", "propose a workflow from plain-language intent without writing files"),
        ("workflow-recipes", "list intent-first workflow creation recipes"),
        ("adjust-workflow", "plan read-only changes to an existing workflow"),
        ("review-workflow", "compact review for one workflow"),
        ("scorecard-workflows", "score workflow readiness"),
        ("analytics-workflows", "summarize retained workflow run analytics"),
        ("smoke-workflows", "run offline fixture-backed smoke checks for accepted workflows"),
        ("workflow-workers", "report workflow phase worker/model profile assignments"),
    ]:
        workflow_parser = subparsers.add_parser(command_name, help=help_text, add_help=False)
        add_shared_root_arg(workflow_parser)
        if command_name == "sync-automation-routing":
            workflow_parser.add_argument(
                "--check",
                action="store_true",
                help="fail if generated automation routing artifacts are out of sync",
            )
        workflow_parser.add_argument("workflow_args", nargs=argparse.REMAINDER)

    claude_sync_parser = add_parser(
        subparsers,
        "sync-claude-skills",
        help="generate Claude Code project-skill adapters from canonical .agents skills",
    )
    add_shared_root_arg(claude_sync_parser)
    claude_sync_parser.add_argument(
        "--check",
        action="store_true",
        help="fail if generated Claude skill adapters are out of sync",
    )
    add_examples(
        claude_sync_parser,
        f"{MANAGE} sync-claude-skills",
        f"{MANAGE} sync-claude-skills --check",
    )

    claude_budget_parser = add_parser(
        subparsers,
        "claude-adapter-budget",
        help="estimate Claude skill-adapter description tokens and name-only savings",
    )
    add_shared_root_arg(claude_budget_parser)
    claude_budget_parser.add_argument("--summary", action="store_true", help="emit compact adapter budget fields")
    claude_budget_parser.add_argument("--compact", action="store_true", help="with --summary, omit top adapter rows")
    claude_budget_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        claude_budget_parser,
        f"{MANAGE} claude-adapter-budget",
        f"{MANAGE} claude-adapter-budget --summary --compact --format json",
    )

    repo_cli_skill_commands.add_skill_parsers(subparsers, add_parser, add_shared_root_arg, add_examples)

    return parser
