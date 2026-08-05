#!/usr/bin/env python3
"""Argparse surface for skill-management commands."""

from __future__ import annotations

import argparse

from repo_support import repo_common as repo

MANAGE = "python -B .agents/manage.py"
FORMAT_MARKDOWN_HELP = "output format; default: markdown"
SUMMARY_COMPACT_HELP = "with --summary, omit passing check rows"
NEW_SKILL_EXAMPLE = f"{MANAGE} new --kind skill --name demo-skill"


def add_skill_parsers(
    subparsers: argparse._SubParsersAction,
    add_parser,
    add_shared_root_arg,
    add_examples,
) -> None:
    compare_parser = add_parser(
        subparsers,
        "compare-skill",
        help="compare two local skill folders without writing files",
    )
    add_shared_root_arg(compare_parser)
    compare_parser.add_argument("--old", required=True, help="old skill folder")
    compare_parser.add_argument("--new", required=True, help="new skill folder")
    compare_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        compare_parser,
        f"{MANAGE} compare-skill --old old-skill --new .agents/skills/demo-skill",
    )

    inspect_parser = add_parser(
        subparsers,
        "inspect-skill",
        help="run one compact evidence pass for a skill",
    )
    add_shared_root_arg(inspect_parser)
    inspect_parser.add_argument("--skill", required=True, help="skill folder")
    inspect_mode = inspect_parser.add_mutually_exclusive_group()
    inspect_mode.add_argument(
        "--fast",
        action="store_true",
        help="run validation and budget checks only; skip text evidence scan and hashing",
    )
    inspect_mode.add_argument(
        "--deep",
        action="store_true",
        help="run full evidence scan and hash attestation; default behavior",
    )
    inspect_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    inspect_parser.add_argument("--summary", action="store_true", help="emit inspection counts and budget facts")
    inspect_parser.add_argument("--compact", action="store_true", help="with --summary, omit nested evidence blocks")
    add_examples(
        inspect_parser,
        f"{MANAGE} inspect-skill --skill .agents/skills/skill-manager --fast",
        f"{MANAGE} inspect-skill --skill .agents/skills/skill-manager --deep --format json",
    )

    review_parser = add_parser(
        subparsers,
        "review-skill",
        help="compact review for one accepted skill using inspect, budget, inventory, and validation",
        description=(
            "Compact review for one accepted skill using inspect, budget, "
            "inventory, and validation."
        ),
    )
    add_shared_root_arg(review_parser)
    review_parser.add_argument("--skill", required=True, help="accepted skill folder")
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
    review_parser.add_argument("--summary", action="store_true", help="emit review counts and actionable facts")
    review_parser.add_argument("--compact", action="store_true", help="with --summary, omit nested review packets")
    add_examples(
        review_parser,
        f"{MANAGE} review --skill .agents/skills/skill-manager",
        f"{MANAGE} review --skill .agents/skills/skill-manager --plan",
    )

    analyze_parser = add_parser(
        subparsers,
        "analyze-location",
        help="analyze a local folder or file as a skill candidate",
        description=(
            "Analyze a local folder or file as a skill candidate without "
            "fetching remote content or writing files."
        ),
    )
    add_shared_root_arg(analyze_parser)
    analyze_parser.add_argument("location", help="local folder/file path only; stage remote sources manually before analysis")
    analyze_parser.add_argument("--max-files", type=int, default=2500)
    analyze_parser.add_argument("--max-text-files", type=int, default=400)
    analyze_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
        help=FORMAT_MARKDOWN_HELP,
    )
    analyze_parser.add_argument("--output", help="write optional report path; omit for stdout-only read-only reporting")
    analyze_parser.add_argument("--summary", action="store_true", help="emit counts and decision facts")
    analyze_parser.add_argument("--compact", action="store_true", help="with --summary, omit nested evidence sections")
    analyze_parser.add_argument(
        "--review-profile",
        choices=("basic", "import"),
        default="basic",
        help="review strictness; import adds supply-chain-oriented warnings",
    )
    add_examples(
        analyze_parser,
        f"{MANAGE} analyze-location .agents/skills/skill-manager",
        f"{MANAGE} analyze-location incoming-skill --format json",
    )

    upgrade_parser = add_parser(
        subparsers,
        "upgrade-skill",
        help="plan or apply a local skill upgrade",
    )
    add_shared_root_arg(upgrade_parser)
    upgrade_parser.add_argument("--old", required=True, help="old skill folder")
    upgrade_parser.add_argument("--new", required=True, help="new skill folder")
    upgrade_parser.add_argument("--target", required=True, help="target skill folder")
    upgrade_parser.add_argument("--strategy", choices=("override", "merge"), required=True)
    apply_group = upgrade_parser.add_mutually_exclusive_group()
    apply_group.add_argument("--dry-run", action="store_true", help="plan only; default behavior")
    apply_group.add_argument("--apply", action="store_true", help="apply override strategy")
    upgrade_parser.add_argument(
        "--allow-outside-active-skills",
        action="store_true",
        help="allow a repository-local target outside .agents/skills",
    )
    add_examples(
        upgrade_parser,
        f"{MANAGE} upgrade-skill --old old-skill --new new-skill --target .agents/skills/demo-skill --strategy override --dry-run",
    )

    eval_parser = add_parser(
        subparsers,
        "eval-skill",
        help="run deterministic local eval assertions for a skill",
        description="Run deterministic local eval assertions; suites may execute commands or self-tests, so treat as write/runtime unless the suite is known read-only.",
    )
    add_shared_root_arg(eval_parser)
    eval_parser.add_argument("--skill", required=True, help="skill folder")
    eval_parser.add_argument("--suite", required=True, help="JSON eval suite")
    eval_parser.add_argument(
        "--baseline",
        default="none",
        help="old skill folder or 'none'; default: none",
    )
    eval_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        eval_parser,
        f"{MANAGE} eval-skill --skill .agents/skills/skill-manager --suite .agents/skills/skill-manager/suites/skill-manager-evals.json",
    )

    attest_parser = add_parser(
        subparsers,
        "attest-skill",
        help="emit local provenance, validation status, and file hashes for a skill",
    )
    add_shared_root_arg(attest_parser)
    attest_parser.add_argument("--skill", required=True, help="skill folder")
    attest_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    attest_parser.add_argument("--summary", action="store_true", help="emit counts and validation facts")
    attest_parser.add_argument("--compact", action="store_true", help="with --summary, omit manifest and file hashes")
    add_examples(
        attest_parser,
        f"{MANAGE} attest-skill --skill .agents/skills/skill-manager --format json",
    )

    compat_parser = add_parser(
        subparsers,
        "validate-agent-compatibility",
        help="validate canonical skill compatibility and generated adapter surfaces",
        description="Validate canonical skill compatibility and generated adapter surfaces.",
    )
    add_shared_root_arg(compat_parser)
    compat_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    compat_parser.add_argument("--summary", action="store_true", help="emit aggregate counts and failures only")
    compat_parser.add_argument("--compact", action="store_true", help=SUMMARY_COMPACT_HELP)
    compat_parser.add_argument(
        "--installed-hosts",
        action="store_true",
        help="probe installed Codex, Copilot, and Claude CLI help plus Copilot skill discovery without model calls",
    )
    add_examples(
        compat_parser,
        f"{MANAGE} validate-agent-compatibility",
        f"{MANAGE} validate-agent-compatibility --format json",
        f"{MANAGE} validate-agent-compatibility --installed-hosts --summary --compact --format json",
    )

    inventory_parser = add_parser(
        subparsers,
        "skill-inventory",
        help="build a compact SBOM-style inventory for one or all skills",
    )
    add_shared_root_arg(inventory_parser)
    inventory_target = inventory_parser.add_mutually_exclusive_group(required=True)
    inventory_target.add_argument("--skill", help="skill folder")
    inventory_target.add_argument("--all", action="store_true", help="inventory all accepted skills")
    inventory_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    inventory_parser.add_argument("--summary", action="store_true", help="emit aggregate counts and top rows")
    inventory_parser.add_argument("--compact", action="store_true", help="with --summary, omit per-skill rows")
    add_examples(
        inventory_parser,
        f"{MANAGE} skill-inventory --all",
        f"{MANAGE} skill-inventory --skill .agents/skills/skill-manager",
    )

    triage_parser = add_parser(
        subparsers,
        "triage-candidates",
        help="rank candidate skill folders with offline heuristics",
    )
    add_shared_root_arg(triage_parser)
    triage_parser.add_argument(
        "--candidate-root",
        required=True,
        help="folder containing candidate skill folders",
    )
    triage_parser.add_argument("--limit", type=int, default=50)
    triage_parser.add_argument("--max-candidates", type=int, default=5000)
    triage_parser.add_argument(
        "--review-profile",
        choices=("basic", "import"),
        default="basic",
        help="include richer import-review packet for each ranked candidate",
    )
    triage_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    add_examples(
        triage_parser,
        f"{MANAGE} triage-candidates --candidate-root incoming-skills --limit 20",
    )

    budget_parser = add_parser(
        subparsers,
        "measure-skill-budget",
        help="measure SKILL.md and support-file token-pressure budgets",
    )
    add_shared_root_arg(budget_parser)
    budget_target = budget_parser.add_mutually_exclusive_group(required=True)
    budget_target.add_argument("--skill", help="skill folder")
    budget_target.add_argument("--all", action="store_true", help="measure all accepted skills")
    budget_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    budget_parser.add_argument("--summary", action="store_true", help="emit compact aggregate rows")
    budget_parser.add_argument("--compact", action="store_true", help="with --summary, omit per-skill rows except top/warning facts")
    budget_parser.add_argument("--write-trend", action="store_true", help="write docs/context-budget-history.json for measured skill(s); omit for read-only reporting")
    budget_parser.add_argument("--baseline-ref", help="compare current measurements with a git ref")
    add_examples(
        budget_parser,
        f"{MANAGE} measure-skill-budget --all",
        f"{MANAGE} measure-skill-budget --all --summary --format json",
        f"{MANAGE} measure-skill-budget --all --baseline-ref HEAD --summary --compact --format json",
        f"{MANAGE} measure-skill-budget --skill .agents/skills/skill-manager --write-trend --format json",
        f"{MANAGE} measure-skill-budget --skill .agents/skills/skill-manager",
    )

    audit_parser = add_parser(
        subparsers,
        "audit-skill-determinism",
        help="audit accepted skills for deterministic script-backed guidance",
    )
    add_shared_root_arg(audit_parser)
    audit_target = audit_parser.add_mutually_exclusive_group(required=True)
    audit_target.add_argument("--skill", help="skill folder")
    audit_target.add_argument("--all", action="store_true", help="audit all accepted skills")
    audit_parser.add_argument(
        "--strict",
        action="store_true",
        help="exit nonzero when deterministic guidance issues are found",
    )
    audit_parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    audit_parser.add_argument("--summary", action="store_true", help="emit aggregate counts and issue rows")
    audit_parser.add_argument("--compact", action="store_true", help="with --summary, omit passing skill rows")
    add_examples(
        audit_parser,
        f"{MANAGE} audit-skill-determinism --all",
        f"{MANAGE} audit-skill-determinism --skill .agents/skills/skill-manager --strict",
    )

    new_parser = add_parser(
        subparsers,
        "new",
        help="create a new skill checklist or workflow scaffold",
        description=(
            "Public creation front door. Use `--kind skill` for the skill checklist "
            "or `--kind workflow` for the workflow scaffold."
        ),
    )
    add_shared_root_arg(new_parser)
    new_parser.add_argument("--kind", choices=("skill", "workflow"), required=True)
    new_parser.add_argument("--name", required=True, help="new skill or workflow name")
    new_parser.add_argument("--summary", help="workflow summary; required for --kind workflow")
    new_parser.add_argument("--uses-skill", action="append", default=[], help="workflow skill dependency; repeatable")
    new_parser.add_argument("--uses-script", action="append", default=[], help="workflow script/command dependency; repeatable")
    add_examples(
        new_parser,
        NEW_SKILL_EXAMPLE,
        f"{MANAGE} new --kind workflow --name demo-workflow --summary \"Short purpose\"",
    )

    checklist_parser = add_parser(
        subparsers,
        "new-skill-checklist",
        help="print a Markdown checklist for creating a new skill",
    )
    add_shared_root_arg(checklist_parser)
    checklist_parser.add_argument("--name", required=True, help="new skill name")
    add_examples(checklist_parser, NEW_SKILL_EXAMPLE)

    link_parser = add_parser(subparsers, "link-skills", help="link skills into user-level folders")
    add_shared_root_arg(link_parser)
    link_parser.add_argument(
        "--targets",
        nargs="+",
        choices=repo.TOOLS,
        default=["Codex", "Copilot", "Claude"],
        help="tools to link; default: Codex Copilot Claude",
    )
    link_parser.add_argument("--skill-source-path")
    link_parser.add_argument("--codex-skills-path")
    link_parser.add_argument("--claude-skills-path")
    link_parser.add_argument("--copilot-skills-path")
    link_parser.add_argument(
        "--mode",
        choices=("auto", "link", "copy"),
        default="auto",
        help="installation mode; default: auto",
    )
    link_parser.add_argument("--dry-run", action="store_true")
    add_examples(link_parser, f"{MANAGE} link-skills --dry-run")
