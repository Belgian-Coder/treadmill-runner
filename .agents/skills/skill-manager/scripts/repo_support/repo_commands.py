"""Command discovery helpers for the repository launcher."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path


COMMAND_GROUPS = {
    "Daily": {
        "dashboard",
        "setup",
        "check-repo-health",
        "check-additions",
        "check-changed",
        "sync",
        "validate",
        "syntax-check",
        "commands",
        "feedback",
        "cost-policy",
        "policy",
        "startup-context",
        "clean-context-proof",
        "context-cost-benchmark",
        "context-use-check",
        "next-action",
        "review-progress",
        "review-loop",
        "review-next",
        "review-autopilot",
        "change-ledger",
        "changed-context",
        "claim-check",
        "budget-trend",
        "context-guardrails",
        "command-budget-check",
        "determinism-check",
        "explain-route",
        "which-skill",
        "which-workflow",
        "start-here",
        "project-kickoff",
        "project-context-review",
        "project-context-apply-review",
        "dotnet-context",
        "what-now",
        "resume-work",
        "attachment-route",
        "evidence-index",
        "evidence-verify",
        "changed-evidence",
        "review-packet",
        "handoff-packet",
        "portable-constraints",
        "credential-doctor",
    },
    "Readiness": {
        "clean-room-validate",
        "command-docs-smoke",
        "command-budget-check",
        "environment-preflight",
        "portable-tools",
        "fresh-clone-smoke",
        "install-harness-smoke",
        "install-harness",
        "install-wizard",
        "project-context-apply-review",
        "dotnet-context",
        "validate-copy-contract",
        "harness-promote",
        "public-export",
        "release-evidence",
        "benchmark",
        "finish",
        "commit-readiness",
        "determinism-check",
    },
    "Skills": {
        "analyze-location",
        "inspect-skill",
        "review-skill",
        "audit-skill-determinism",
        "audit-candidate-source",
        "compare-skill",
        "upgrade-skill",
        "eval-skill",
        "attest-skill",
        "skill-inventory",
        "measure-skill-budget",
        "claude-adapter-budget",
        "triage-candidates",
        "new-skill-checklist",
        "skill",
    },
    "Workflows": {
        "create-workflow",
        "create-workflow-from-request",
        "propose-workflow",
        "workflow-recipes",
        "adjust-workflow",
        "validate-automations",
        "eval-workflow",
        "index-workflow-runs",
        "reference-refresh",
        "review-workflow",
        "smoke-workflows",
        "analytics-workflows",
        "workflow-workers",
        "sync-automation-routing",
        "workflow",
    },
    "Generated": {
        "sync-instructions",
        "sync-skill-routing",
        "sync-claude-skills",
    },
    "Local AI": {
        "local-ai",
    },
    "Compatibility": {
        "validate-agent-compatibility",
        "link-skills",
    },
}

GROUP_DESCRIPTIONS = {
    "Daily": "Small commands for normal repository work and orientation.",
    "Readiness": "Release and benchmark readiness checks; these are evidence commands, not publishing commands.",
    "Skills": "Skill-manager owned commands for accepted skills and candidate analysis.",
    "Workflows": "Workflow-manager owned commands for workflow modules and workflow run evidence.",
    "Generated": "Generated adapter and routing synchronization commands.",
    "Local AI": "Local-ai-helper owned commands. All local AI use is optional and policy-controlled.",
    "Compatibility": "Adapter and user-level skill-link compatibility helpers.",
    "Other": "Commands without a more specific group.",
}

PUBLIC_COMMAND_NAMES = {
    "dashboard": "status",
    "check-repo-health": "status",
    "explain-route": "route",
    "validate": "check",
    "review-skill": "review",
    "review-workflow": "review",
    "new-skill-checklist": "new",
    "create-workflow": "new",
    "create-workflow-from-request": "workflow create",
    "propose-workflow": "workflow propose",
    "workflow-recipes": "workflow recipes",
    "adjust-workflow": "workflow adjust",
}

INTERNAL_MARKDOWN_SKIP = {"check-repo-health"}
MANAGE = "python -B .agents/manage.py"


def manage(command: str) -> str:
    return f"{MANAGE} {command}"

GROUP_DEFAULTS = {
    "Daily": {
        "owner": "skill-manager",
        "writes": "read-only unless command name says sync/setup",
        "local_ai": "failure triage only when policy allows",
        "fallback": "deterministic command output",
    },
    "Readiness": {
        "owner": "skill-manager",
        "writes": "read-only; fresh clone writes only to temporary folders",
        "local_ai": "advisory readiness only",
        "fallback": "skip unavailable external checks with reasons",
    },
    "Skills": {
        "owner": "skill-manager",
        "writes": "read-only unless upgrade/apply or generated sync is explicit",
        "local_ai": "suggestions only when metadata and policy allow",
        "fallback": "deterministic validation and inventory output",
    },
    "Workflows": {
        "owner": "workflow-manager",
        "writes": "read-only unless scaffold/index/sync writes are explicit",
        "local_ai": "snippets only when workflow metadata and policy allow",
        "fallback": "deterministic validation and review output",
    },
    "Generated": {
        "owner": "skill-manager",
        "writes": "generated files unless --check",
        "local_ai": "none",
        "fallback": "deterministic generator diff/status",
    },
    "Local AI": {
        "owner": "local-ai-helper",
        "writes": "read-only for classified checks; otherwise local-ai-helper may write config, gitignored settings, cache, model, and runtime paths",
        "local_ai": "owned",
        "fallback": "deterministic report when models are unavailable",
    },
    "Compatibility": {
        "owner": "skill-manager",
        "writes": "link-skills may write user-level links unless dry-run",
        "local_ai": "none",
        "fallback": "deterministic compatibility report",
    },
}

COMMAND_METADATA = {
    "dashboard": {
        "when": "Show branch, dirty state, health, local AI, latest evidence, and next safest command; --watch-once refreshes once after a command finishes.",
        "writes": "read-only",
    },
    "what-now": {
        "when": "Convert failed command output into the next deterministic command.",
        "writes": "read-only",
    },
    "resume-work": {
        "when": "Resume interrupted work from branch state, dirty files, latest evidence, and the next deterministic action.",
        "writes": "read-only",
    },
    "next-action": {
        "when": "Emit one deterministic next command, why it is next, required compact context, validation-after command, and stop condition.",
        "writes": "read-only",
    },
    "context-cost-benchmark": {
        "when": "Compare raw diff, startup/HANDOFF guidance, and next-action route token estimates with output-token break-even boundaries; --record appends ignored history for regressions.",
        "writes": "read-only unless --record appends ignored .agents/local-ai/cache/context-cost-ledger.jsonl",
    },
    "context-use-check": {
        "when": "Prove compact command packets route through HANDOFF.md, expose next_command, and mark raw navigation JSON as skipped/tool-only.",
        "writes": "read-only",
    },
    "review-progress": {
        "when": "Show or mark ignored repo-local progress for the current review plan so large-diff review resumes without rereading completed units.",
        "writes": "writes ignored .agents/local-ai/cache/review-progress.json only when marking or resetting progress",
    },
    "review-loop": {
        "when": "Run compact review-packet commands sequentially, automatically batch adjacent hunk slices under budget, mark successful tracked units complete, run an untracked validation action at most once, and route its declared follow-up.",
        "writes": "writes ignored .agents/local-ai/cache/review-progress.json only for non-dry-run stale resets or after successful packet commands; failed raw output may be cached under .agents/local-ai/cache/command-output",
    },
    "review-next": {
        "when": "Run exactly one next compact review unit and mark it complete only after successful command output.",
        "writes": "writes ignored .agents/local-ai/cache/review-progress.json only after a successful packet command",
    },
    "review-autopilot": {
        "when": "Run bounded review-loop batches, then route to finish when review evidence is complete or stop at a blocker or cap.",
        "writes": "writes the same ignored review-progress/command-output evidence as review-loop unless --dry-run is used",
    },
    "change-ledger": {
        "when": "Summarize changed files by owner, risk, and path-derived reason without loading raw diff.",
        "writes": "read-only",
    },
    "changed-context": {
        "when": "Emit a compact changed-file navigation packet with owner groups, read-first paths, validation commands, and raw-diff token savings.",
        "writes": "read-only",
    },
    "claim-check": {
        "when": "Check final-answer claims such as finish passed, navigation fresh, or workflow smoke passed against compact JSON evidence.",
        "writes": "read-only",
    },
    "budget-trend": {
        "when": "Summarize ignored local budget trend entries recorded by finish for input-context estimates and elapsed-time drift.",
        "writes": "read-only",
    },
    "context-guardrails": {
        "when": "Fail when changed agent-facing text routes agents to raw generated navigation JSON or registries without a tool-only guardrail.",
        "writes": "read-only",
    },
    "command-budget-check": {
        "when": "Run representative compact commands and fail when latency_budget or output_budget fields regress.",
        "writes": "read-only",
    },
    "determinism-check": {
        "when": "Replay changed or all strict commands twice in fresh isolated fixtures and compare outputs, files, artifact hashes, and observed effects.",
        "writes": "temporary isolated Git fixtures that are removed before exit",
        "owner": "skill-manager",
    },
    "finish": {
        "when": "Run sync, workflow hook safety, validation, changed-scope, benchmark readiness, navigation-map refresh, and optional budget checks before finalizing; --commit-packet writes evidence.",
        "writes": "may refresh generated navigation maps; --commit-packet writes evidence",
    },
    "attachment-route": {
        "when": "Classify an attachment and choose the deterministic evidence command; --write-plan records a safe inspection plan.",
        "writes": "read-only",
    },
    "evidence-index": {
        "when": "List latest workflow runs, benchmark reports, document evidence, and validation evidence.",
        "writes": "read-only",
    },
    "evidence-verify": {
        "when": "Verify compact evidence raw-output references and digests before relying on a compressed packet.",
        "writes": "read-only",
    },
    "changed-evidence": {
        "when": "Map changed files to focused deterministic evidence commands and optionally write a compact evidence packet.",
        "writes": "read-only",
    },
    "check-changed": {
        "when": "Run checks implied by changed files without writing generated navigation maps by default.",
        "writes": "read-only by default; refreshes known generated navigation maps only with --refresh-navigation",
    },
    "review-packet": {
        "when": "Emit or write compact owner/risk packets for large changed diffs; use --owner, --path, and repeated --hunk for fresh-agent review slices before raw diff.",
        "writes": "read-only unless --write records repo-local review packet, review plan, and cost report evidence and may refresh known generated navigation maps",
    },
    "handoff-packet": {
        "when": "Emit a compact route-first packet for a fresh agent or subagent, including navigation freshness, owner review routing, validation commands, and token-cost estimates.",
        "writes": "may refresh generated navigation maps before building the packet",
    },
    "portable-constraints": {
        "when": "Fail on nonportable changed-file assumptions such as hardware defaults, personal paths, or admin-only installs.",
        "writes": "read-only",
    },
    "credential-doctor": {
        "when": "Check Azure DevOps, SonarQube, external-reference, and local-AI credential readiness without printing secrets.",
        "writes": "read-only",
    },
    "commit-readiness": {
        "when": "Check staged files for unsafe payloads, stale generated artifacts, and commit readiness.",
        "writes": "read-only",
    },
    "feedback": {
        "when": "Record, summarize, export, convert reviewed corrections into eval candidates, and explicitly clear local managed-failure feedback without reading the raw ledger during normal work.",
        "writes": "record appends ignored local JSONL; export and eval-packet write candidate packets; clear truncates the ignored JSONL only with confirmation",
    },
    "syntax-check": {
        "when": "Parse Python files with ast.parse when syntax validation is needed without writing __pycache__ bytecode.",
        "writes": "read-only",
    },
    "setup": {
        "when": "First-time setup, copied-project smoke checks, or readiness doctor output.",
        "writes": "syncs generated files and links skills unless --check or --dry-run",
    },
    "check-repo-health": {
        "when": "Quick read-only harness health and clutter check.",
        "writes": "read-only",
    },
    "validate": {
        "when": "Authoritative skill/workflow validation, with --deep for release-level local checks.",
        "writes": "read-only",
    },
    "commands": {
        "when": "Find daily commands without opening skill folders.",
        "writes": "read-only",
    },
    "cost-policy": {
        "when": "Check local-first token, context, fallback, and compact-output policy before paid-model work.",
        "writes": "read-only",
        "owner": "skill-manager",
    },
    "policy": {
        "when": "List, explain, validate, or intentionally configure tracked project limits, warning actions, and command budgets.",
        "writes": "read-only for show/list/get/explain/validate; init/set/reset write tracked owner configuration",
        "owner": "skill-manager",
    },
    "startup-context": {
        "when": "Check always-loaded and beginner-loaded context estimates before opening broad repo docs.",
        "writes": "read-only",
        "owner": "skill-manager",
    },
    "clean-context-proof": {
        "when": "Prove a fresh agent can use only AGENTS.md plus startup-context to find HANDOFF.md first.",
        "writes": "read-only",
        "owner": "skill-manager",
    },
    "explain-route": {
        "when": "Explain which skill or workflow should handle a natural-language request.",
        "writes": "read-only",
    },
    "which-skill": {
        "when": "Choose the best accepted skill for a natural-language request using deterministic routing text and aliases.",
        "writes": "read-only",
    },
    "which-workflow": {
        "when": "Choose the best accepted workflow for a natural-language request, regression-test routing fixtures, and return the recommended workflow start command.",
        "writes": "read-only",
    },
    "fresh-clone-smoke": {
        "when": "Prove a new consumer can clone and validate the harness.",
        "writes": "temporary clone only",
    },
    "clean-room-validate": {
        "when": "Prove the repo from a fresh isolated D-drive clone with isolated HOME, TEMP, npm cache, and Playwright cache.",
        "writes": "D-drive validation folder only",
    },
    "environment-preflight": {
        "when": "Classify required/optional local tools, credentials, and D-drive validation path readiness before broad validation.",
        "writes": "read-only",
    },
    "portable-tools": {
        "when": "Validate pinned portable tool manifests and report repo-local cache status without downloading binaries.",
        "writes": "read-only",
    },
    "command-docs-smoke": {
        "when": "Catch documented manage.py command drift such as unsupported workflow command flags before users copy examples.",
        "writes": "read-only",
    },
    "install-harness-smoke": {
        "when": "Prove prepared install, clean-state copy, project-context check, and in full mode start/resume workflow behavior in a temporary target.",
        "writes": "temporary install target only",
    },
    "install-harness": {
        "when": "Resolve a payload profile and feature overrides, then copy its deterministic source manifest into a consumer project with local state, model caches, secrets, Git state, and workflow run history excluded.",
        "writes": "target project files; refuses differing collisions unless --force",
    },
    "install-wizard": {
        "when": "Guide a beginner through install profile and optional setup choices, then print or run the recommended install command.",
        "writes": "read-only unless --apply is used",
    },
    "start-here": {
        "when": "Print the smallest beginner-friendly next steps for a copied or source harness.",
        "writes": "read-only",
    },
    "project-kickoff": {
        "when": "Plan or run the first-use harness install/setup/context-review/status sequence for a target project with one primary next action and workflow command recommendations.",
        "writes": "read-only unless --apply is used; --apply writes target project harness/setup artifacts but does not start workflow runs",
        "owner": "skill-manager",
    },
    "project-context-review": {
        "when": "Inspect generated project context and list structured missing or draft facts before workflow planning.",
        "writes": "read-only unless --write-review writes docs/project/review artifacts in the target project",
        "owner": "skill-manager",
    },
    "project-context-apply-review": {
        "when": "Preview or apply answered project-context review facts into a managed section of canonical project context.",
        "writes": "read-only unless --apply writes docs/project/project-context.md in the target project",
        "owner": "skill-manager",
    },
    "dotnet-context": {
        "when": "Inspect .NET project shape, SDK/runtime signals, NuGet/feed policy, validation candidates, and context facts without running restore/build/test/package/tool commands; use --solution/--project to narrow large repos.",
        "writes": "read-only unless --write-evidence writes docs/project/dotnet-context artifacts in the target project",
        "owner": "dotnet-project-context",
    },
    "validate-copy-contract": {
        "when": "Validate feature/profile inheritance, dependency closure, deterministic source manifest digest, and required local-state exclusions.",
        "writes": "read-only",
    },
    "harness-promote": {
        "when": "Compare consumer harness-owned edits against the source payload and promote explicitly selected paths back to the source repo.",
        "writes": "read-only unless --apply --paths is used; never promotes local state, workflow runs, secrets, caches, or docs/project context",
        "owner": "skill-manager",
    },
    "public-export": {
        "when": "Create or preview a sanitized public export folder from the harness payload manifest.",
        "writes": "target export folder unless --dry-run is used",
    },
    "release-evidence": {
        "when": "Produce one release-readiness packet before publishing or pushing important changes.",
        "writes": "temporary clone unless --skip-fresh-clone",
    },
    "benchmark": {
        "when": "Validate, compare, run release-gate, or check tool-call benchmark evidence.",
        "owner": "agent-benchmarking",
        "writes": "read-only for doctor/compare; suite-specific commands may write run evidence",
    },
    "local-ai": {
        "when": "Install, inspect, benchmark, or troubleshoot repo-local AI under policy control.",
        "owner": "local-ai-helper",
    },
    "review-skill": {
        "when": "Review one accepted skill and optionally produce an implementation packet.",
    },
    "audit-candidate-source": {
        "when": "Audit external skill and agent source trees for reference health, invocation boundaries, cycles, and similar routing descriptions before import.",
    },
    "claude-adapter-budget": {
        "when": "Estimate generated Claude adapter description tokens and name-only savings without writing Claude settings.",
        "writes": "read-only",
    },
    "skill": {
        "when": "Run skill-manager grouped utilities such as skill doctor.",
    },
    "review-workflow": {
        "when": "Review one workflow and optionally produce an implementation packet.",
        "owner": "workflow-manager",
    },
    "reference-refresh": {
        "when": "Run the reference-refresh report, dry-run, or write path with workflow-owned manifest defaults.",
        "owner": "external-reference-manager",
        "writes": "read-only unless --mode write is used",
    },
    "smoke-workflows": {
        "when": "Run offline fixture-backed smoke checks for accepted workflows without Azure DevOps, SonarQube, or GitHub CI.",
        "owner": "workflow-manager",
        "writes": "temporary workflow runs and temp fixtures, removed before exit",
    },
    "analytics-workflows": {
        "when": "Summarize retained workflow run friction, proof gaps, skipped/failed checks, and reusable lesson candidates.",
        "owner": "workflow-manager",
    },
    "workflow-workers": {
        "when": "Report phase-to-worker model profile assignments for Codex, GitHub Copilot, Claude Code, and fallbacks.",
        "owner": "workflow-manager",
        "writes": "read-only",
    },
    "new-skill-checklist": {
        "when": "Print the minimal checklist for a new skill through `new --kind skill`.",
    },
    "create-workflow": {
        "when": "Scaffold a workflow through `new --kind workflow`.",
        "owner": "workflow-manager",
    },
    "create-workflow-from-request": {
        "when": "Dry-run or write a workflow scaffold from a plain-language request.",
        "owner": "workflow-manager",
        "writes": "read-only unless --write is used",
    },
    "propose-workflow": {
        "when": "Propose whether to create, adjust, or avoid a workflow from plain-language intent.",
        "owner": "workflow-manager",
        "writes": "read-only",
    },
    "workflow-recipes": {
        "when": "List intent-first workflow creation recipes and their validation expectations.",
        "owner": "workflow-manager",
        "writes": "read-only",
    },
    "adjust-workflow": {
        "when": "Plan read-only changes to an existing workflow from plain-language intent.",
        "owner": "workflow-manager",
        "writes": "read-only",
    },
    "workflow": {
        "when": "Run workflow-manager grouped utilities such as workflow doctor.",
        "owner": "workflow-manager",
    },
}

SHORTCUT_COMMON_PATHS = {
    "first-time": {"first-time"},
    "daily": {"daily"},
    "failure": {"failure"},
    "harness": {"harness-update"},
    "documents": {"documents"},
    "workflow": {"workflow"},
    "release": {"release"},
}

SHORTCUT_COMMAND_NAMES = {
    "first-time": {"start-here", "project-kickoff", "project-context-review", "project-context-apply-review", "dotnet-context", "install-wizard", "install-harness", "setup", "dashboard", "check-repo-health", "commands"},
    "daily": {
        "dashboard",
        "resume-work",
        "next-action",
        "changed-evidence",
        "change-ledger",
        "changed-context",
        "review-packet",
        "review-progress",
        "review-loop",
        "review-next",
        "review-autopilot",
        "handoff-packet",
        "portable-constraints",
        "context-guardrails",
        "context-use-check",
        "command-budget-check",
        "determinism-check",
        "which-skill",
        "which-workflow",
        "check-additions",
        "check-changed",
        "syntax-check",
        "evidence-index",
        "feedback",
        "commands",
        "cost-policy",
        "dotnet-context",
        "startup-context",
        "clean-context-proof",
        "context-cost-benchmark",
        "claim-check",
        "budget-trend",
        "evidence-verify",
    },
    "failure": {"what-now", "dashboard", "next-action", "claim-check", "feedback", "local-ai", "cost-policy", "startup-context", "clean-context-proof", "evidence-verify", "commands"},
    "harness": {"start-here", "project-kickoff", "project-context-review", "project-context-apply-review", "dotnet-context", "install-wizard", "install-harness", "harness-promote", "install-harness-smoke", "validate-copy-contract", "public-export", "portable-tools", "setup", "commands"},
    "documents": {"attachment-route", "changed-evidence", "local-ai", "commands"},
    "workflow": {
        "which-workflow",
        "workflow",
        "review-workflow",
        "propose-workflow",
        "workflow-recipes",
        "create-workflow-from-request",
        "adjust-workflow",
        "validate-automations",
        "eval-workflow",
        "smoke-workflows",
        "analytics-workflows",
        "workflow-workers",
        "index-workflow-runs",
        "reference-refresh",
        "commands",
    },
    "release": {
        "release-evidence",
        "fresh-clone-smoke",
        "install-harness-smoke",
        "commit-readiness",
        "finish",
        "command-budget-check",
        "determinism-check",
        "benchmark",
        "commands",
    },
}


def command_group(name: str) -> str:
    for group, names in COMMAND_GROUPS.items():
        if name in names:
            return group
    return "Other"


def command_index(parser: argparse.ArgumentParser) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for action in parser._actions:  # argparse does not expose subparser choices publicly.
        choices = getattr(action, "choices", None)
        if not isinstance(choices, dict):
            continue
        for name, subparser in sorted(choices.items()):
            if not isinstance(subparser, argparse.ArgumentParser):
                continue
            group = command_group(name)
            defaults = GROUP_DEFAULTS.get(group, {})
            metadata = {**defaults, **COMMAND_METADATA.get(name, {})}
            parser_summary = str(getattr(subparser, "description", "") or "").strip()
            fallback_summary = parser_summary or f"Run `{name}`; use `{name} --help` for flags."
            rows.append(
                {
                    "name": name,
                    "group": group,
                    "help": fallback_summary,
                    "usage": subparser.format_usage().replace("usage: ", "").strip(),
                    "owner": metadata.get("owner", "skill-manager"),
                    "writes": metadata.get("writes", "read-only unless explicitly requested"),
                    "local_ai": metadata.get("local_ai", "none"),
                    "fallback": metadata.get("fallback", "deterministic command output"),
                    "when": metadata.get("when", fallback_summary),
                }
            )
    return rows


def render_commands_markdown(report: dict[str, object]) -> str:
    lines: list[str] = []
    lines.append("# Repository Commands")
    lines.append("")
    lines.append(
        "Generated from the launcher parser. Use this as a daily index; "
        "`commands --format tsv` and each command's `--help` carry the full rows."
    )
    lines.append("")
    lines.append("## Common Paths")
    lines.append("")
    lines.append("| Path | Commands | Notes |")
    lines.append("|---|---|---|")
    for path in report.get("common_paths", []):
        if not isinstance(path, dict):
            continue
        commands = "<br>".join(f"`{command}`" for command in path.get("commands", []))
        lines.append(f"| {path.get('name')} | {commands} | {path.get('notes', '')} |")
    harness_notes = report.get("harness_notes", []) if isinstance(report.get("harness_notes"), list) else []
    if harness_notes:
        lines.extend(["", "## Harness Setup Notes", ""])
        lines.append("| Command | What It Does |")
        lines.append("|---|---|")
        for note in harness_notes:
            if not isinstance(note, dict):
                continue
            lines.append(f"| `{note.get('command')}` | {note.get('what')} |")
    lines.append("")
    groups = report.get("groups", []) if isinstance(report.get("groups"), list) else []
    commands = report.get("commands", []) if isinstance(report.get("commands"), list) else []
    lines.append("## Command Groups")
    lines.append("")
    lines.append("| Group | Commands | Owners | Write Boundary |")
    lines.append("|---|---|---|---|")
    for group in groups:
        group_name = str(group.get("name")) if isinstance(group, dict) else str(group)
        if isinstance(group, dict) and not commands:
            display_names = [str(item) for item in group.get("commands", [])]
            owners = [str(item) for item in group.get("owners", [])]
            lines.append(
                f"| {group_name} | {', '.join(f'`{name}`' for name in display_names)} | "
                f"{', '.join(f'`{owner}`' for owner in owners)} | see `--help` |"
            )
            continue
        group_rows = [
            row
            for row in commands
            if isinstance(row, dict)
            and row.get("group") == group_name
            and str(row.get("name", "")) not in INTERNAL_MARKDOWN_SKIP
        ]
        if not group_rows:
            continue
        display_names = sorted(
            {PUBLIC_COMMAND_NAMES.get(str(row["name"]), str(row["name"])) for row in group_rows}
        )
        owners = sorted({str(row.get("owner") or "skill-manager") for row in group_rows})
        write_values = sorted({str(row.get("writes") or "") for row in group_rows})
        write_boundary = write_values[0] if len(write_values) == 1 else "mixed; see TSV/--help"
        lines.append(
            f"| {group} | {', '.join(f'`{name}`' for name in display_names)} | "
            f"{', '.join(f'`{owner}`' for owner in owners)} | {write_boundary} |"
        )
    return "\n".join(lines)


def doc_frontmatter_for_commands() -> str:
    return "\n".join(
        [
            "---",
            "title: Repository Commands",
            "type: reference",
            "status: active",
            "owner: skill-manager",
            "audience: both",
            f"updated: {dt.date.today().isoformat()}",
            "---",
            "",
            "",
        ]
    )


def add_doc_frontmatter(markdown: str) -> str:
    if markdown.startswith("---\n"):
        return markdown
    return doc_frontmatter_for_commands() + markdown


def tsv_cell(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def render_commands_tsv(report: dict[str, object]) -> str:
    lines = ["name\tgroup\towner\twrites\twhen"]
    commands = report.get("commands", []) if isinstance(report.get("commands"), list) else []
    for row in commands:
        if not isinstance(row, dict):
            continue
        lines.append(
            "\t".join(
                [
                    tsv_cell(row.get("name")),
                    tsv_cell(row.get("group")),
                    tsv_cell(row.get("owner")),
                    tsv_cell(row.get("writes")),
                    tsv_cell(row.get("when") or row.get("help")),
                ]
            )
        )
    return "\n".join(lines) + "\n"


def summarize_command_report(report: dict[str, object], *, compact: bool = False) -> dict[str, object]:
    commands = report.get("commands", []) if isinstance(report.get("commands"), list) else []
    groups = report.get("groups", []) if isinstance(report.get("groups"), list) else []
    compact_groups: list[dict[str, object]] = []
    for group in groups:
        group_name = str(group)
        group_rows = [row for row in commands if isinstance(row, dict) and row.get("group") == group_name]
        row: dict[str, object] = {"name": group_name, "command_count": len(group_rows)}
        if not compact:
            row["owners"] = sorted({str(row.get("owner") or "skill-manager") for row in group_rows})
            row["commands"] = sorted(
                {PUBLIC_COMMAND_NAMES.get(str(item.get("name")), str(item.get("name"))) for item in group_rows}
            )
        compact_groups.append(row)
    compact_report: dict[str, object] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "repo-command-discovery"),
        "ok": bool(report.get("ok", True)),
        "status": report.get("status", "ok"),
        "shortcut": report.get("shortcut", ""),
        "summary": {
            "command_count": len(commands),
            "group_count": len(groups),
            "common_path_count": (
                len(report.get("common_paths", [])) if isinstance(report.get("common_paths"), list) else 0
            ),
        },
        "groups": compact_groups,
    }
    common_paths = report.get("common_paths", []) if isinstance(report.get("common_paths"), list) else []
    if compact:
        compact_report["group_names"] = [str(row.get("name", "")) for row in compact_groups]
        compact_report.pop("groups", None)
    else:
        compact_report["common_paths"] = common_paths
    if not compact:
        compact_report["commands"] = [
            {
                "name": PUBLIC_COMMAND_NAMES.get(str(row.get("name")), str(row.get("name"))),
                **({"canonical": row.get("name")} if PUBLIC_COMMAND_NAMES.get(str(row.get("name"))) else {}),
                "group": row.get("group"),
                "owner": row.get("owner"),
                "writes": row.get("writes"),
            }
            for row in commands
            if isinstance(row, dict)
        ]
    return compact_report


def print_commands(
    parser: argparse.ArgumentParser,
    output_format: str,
    *,
    root: Path | None = None,
    shortcut: str | None = None,
    write_path: str | None = None,
    summary: bool = False,
    compact: bool = False,
) -> int:
    common_paths = [
        {
            "name": "first-time",
            "notes": "Start from the source harness for project-kickoff/install commands, then run setup/status inside the real target project. If Python is missing, read `docs/harness/no-python.md` first.",
            "commands": [
                manage("start-here --simple"),
                manage("project-kickoff --target <project>"),
                manage("install-wizard --target <project>"),
                "cd <project>",
                manage("setup"),
                manage("setup --check"),
                manage("dotnet-context --target . --format json"),
                manage("dotnet-context --target . --solution <solution.sln> --project <project.csproj> --format json"),
                manage("project-context-review --target ."),
                manage("project-context-review --target . --write-review"),
                manage("project-context-apply-review --target ."),
                manage("status --fast"),
            ],
        },
        {
            "name": "harness-update",
            "notes": "Run install commands from the source harness, then run setup/status inside the target project. `--run-setup-check` uses no-link validation during copy.",
            "commands": [
                manage("validate-copy-contract"),
                manage("project-kickoff --target <project>"),
                manage("install-wizard --target <project>"),
                manage("install-harness --target <project> --dry-run"),
                manage("install-harness --target <project> --run-setup-check"),
                manage("harness-promote --target <project> --dry-run"),
                "cd <project>",
                manage("setup"),
                manage("setup --check"),
                manage("dotnet-context --target . --format json"),
                manage("project-context-review --target . --write-review"),
                manage("project-context-apply-review --target ."),
                manage("status --fast"),
            ],
        },
        {
            "name": "daily",
            "notes": "Normal low-context loop: inspect status, route the next action, advance review only when requested, then run finish once.",
            "commands": [
                manage("status --fast --summary --compact --format json"),
                manage("next-action --summary --compact --format json"),
                manage("review-autopilot --max-cycles 3 --max-units-per-cycle 20 --max-total-units 60 --max-estimated-tokens 24000 --max-elapsed-ms 540000 --summary --compact --format json"),
                manage("finish --summary --compact --format json"),
            ],
        },
        {
            "name": "skills",
            "notes": "Skill-maintenance and budget checks; write only when a command explicitly says it writes trend or generated artifacts.",
            "commands": [
                manage("skill scorecard --all --summary --compact --format json"),
                manage("skill handoff --skill <skill-name> --summary --compact --format json"),
                manage("skill eval-gap --all --summary --compact --format json"),
                manage("skill route-audit --summary --compact --format json"),
                manage("skill templates --summary --compact --format json"),
                manage("skill lessons --summary --compact --format json"),
                manage("which-skill \"review a workflow plan\" --summary --compact --format json"),
                manage("claude-adapter-budget --summary --compact --format json"),
                manage("measure-skill-budget --skill <skill-name> --write-trend --format json"),
            ],
        },
        {
            "name": "failure",
            "notes": "Use after a failed command to get the next local deterministic repair step and preserve evidence.",
            "commands": [
                manage("what-now"),
                "python -B .agents/manage.py what-now --from-command \"python -B .agents/manage.py check\"",
                manage("feedback summary --all --summary --compact --format json"),
                manage("feedback export --all --min-count 2 --output evidence/feedback"),
                manage("feedback review-digest --corrections evidence/reviewed-corrections.json --format json"),
                manage("feedback eval-packet --corrections evidence/reviewed-corrections.json --output evidence/correction-evals.json --format json"),
                manage("feedback clear --all --confirm-truncate --reason \"processed into action plan\" --action-plan automations/feedback-improvement-workflow/runs/<run-id>/action-plan.md --dry-run --format json"),
                manage("local-ai task --task validation-triage --input .agents/local-ai/cache/last-validation.txt"),
                manage("startup-context --summary --compact --format json"),
                manage("evidence-verify --summary --compact --format json"),
                manage("cost-policy --check --summary --compact --format json"),
            ],
        },
        {
            "name": "documents",
            "notes": "Document intake and inspection commands; write evidence only when a write-plan path is supplied.",
            "commands": [
                manage("attachment-route --file <path> --write-plan evidence/attachments"),
                manage("local-ai document inspect --file <path> --json"),
            ],
        },
        {
            "name": "portable-tools",
            "notes": "Pinned portable executable manifests and repo-local cache checks; installs require explicit setup flags.",
            "commands": [
                manage("portable-tools --summary --compact --format json"),
                manage("portable-tools --check"),
                manage("setup --install-rg-portable"),
            ],
        },
        {
            "name": "credentials",
            "notes": "Guided local-only external service configuration; writes gitignored local secret profiles only during configure.",
            "commands": [
                "python -B .agents/manage.py credential-doctor",
                "python -B .agents/manage.py credential-doctor --configure --service azure-devops --name customer-a",
                "python -B .agents/manage.py credential-doctor --configure --service sonarqube --name project-a",
                "python -B .agents/skills/azure-devops-ticket-intake/scripts/import_azure_devops_work_item.py --help",
                "python -B .agents/skills/sonarqube-diagnostics/scripts/export_issues.py --help",
            ],
        },
        {
            "name": "workflow",
            "notes": "Workflow lifecycle, context, hooks, smoke, analytics, and finish commands. For read-only/offline use, prefer which-workflow/propose, --help, --check, --dry-run, and scorecard --no-lifecycle; start/resume/finish/context/context-evidence/checkpoint writes and smoke can write run or temporary state.",
            "commands": [
                manage("workflow propose --from-request \"review release evidence\" --summary --compact --format json"),
                manage("workflow recipes --summary --compact --format json"),
                manage("workflow create --from-request \"review release evidence\" --name release-evidence-workflow --write"),
                manage("workflow adjust --name user-story-workflow --from-request \"tighten validation\" --plan"),
                manage("workflow start --name <workflow-name> --summary --compact --format json"),
                manage("workflow start --from-request \"implement Azure DevOps user story 123\" --summary --compact --format json"),
                manage("which-workflow \"start a user story\" --summary --compact --format json"),
                manage("which-workflow --suite .agents/skills/skill-manager/scripts/fixtures/workflow-routing-regression.json --check-suite --summary --compact --format json"),
                manage("reference-refresh --mode report --format markdown"),
                manage("workflow context --name <workflow-name> --run-id <run-id> --write"),
                manage("workflow context --name <workflow-name> --run-id <run-id> --runtime-observation-file automations/<workflow-name>/runs/<run-id>/validation/runtime-observation.json --write"),
                manage("workflow context --all --check --format json"),
                manage("workflow context --all --check --include-completed --format json"),
                manage("workflow context-audit --name <workflow-name> --run-id <run-id> --summary --compact --format json"),
                manage("workflow context-evidence --name <workflow-name> --run-id <run-id> --event start --write"),
                manage("workflow context-evidence --name <workflow-name> --run-id <run-id> --event start --check --format json"),
                manage("workflow template gate-check --all --format json"),
                manage("workflow validation-packet --name <workflow-name> --run-id <run-id> --kind playwright-screenshots --format json"),
                manage("workflow hooks --name <workflow-name> --format json"),
                manage("workflow hooks --all --check --format json"),
                manage("workflow scorecard --all --summary --compact --format json"),
                manage("workflow scorecard --all --summary --compact --format json --no-lifecycle"),
                manage("workflow smoke --all --summary --compact --format json"),
                manage("workflow smoke --name <workflow-name> --dry-run --summary --compact --format json"),
                manage("workflow analytics --all --summary --compact --format json"),
                manage("workflow workers --all --summary --compact --format json"),
                manage("workflow workers --profiles --format json"),
                manage("workflow eval-gap --all --summary --compact --format json"),
                manage("workflow plan-check --name <workflow-name> --run-id <run-id>"),
                manage("workflow checkpoint --name <workflow-name> --run-id <run-id> --write"),
                manage("workflow hook-audit --name <workflow-name> --run-dir automations/<workflow-name>/runs/<run-id> --event workflow-pre --hook-id <hook-id>"),
                manage("workflow resume --name <workflow-name> --summary --compact --format json"),
                manage("workflow handoff --name <workflow-name>"),
                manage("workflow finish --name <workflow-name> --run-id <run-id>"),
                manage("review <workflow-name>"),
            ],
        },
        {
            "name": "release",
            "notes": "Exhaustive release validation is owned by finish --release-full; inspect release evidence, then verify commit readiness.",
            "commands": [
                manage("finish --release-full --commit-packet evidence/finish"),
                manage("release-evidence"),
                manage("commit-readiness"),
            ],
        },
    ]
    report = {
        "schema_version": 1,
        "tool": "repo-command-discovery",
        "ok": True,
        "status": "ok",
        "groups": sorted({str(item["group"]) for item in command_index(parser)}),
        "common_paths": common_paths,
        "harness_notes": [
            {
                "command": "Python runtime missing",
                "what": "No harness command can run yet. Follow `docs/harness/no-python.md`: use a user-scoped, WinGet user-scope, `AGENTS_PYTHON`, or portable Python 3.12+ runtime, then rerun setup with that executable path.",
            },
            {
                "command": "python -B .agents/manage.py setup",
                "what": "Write-mode initializer for the current project. Syncs generated routing/adapters, initializes navigation maps and project context, links user-level skills unless skipped or a temporary smoke marker is present, then runs validation.",
            },
            {
                "command": "python -B .agents/manage.py setup --check",
                "what": "Read-only setup verification. It checks generated artifacts, project initialization state, validation, ripgrep availability, and user-level skill links; temporary smoke targets auto-skip global skill-link checks via `.agents/harness-smoke-target.json`.",
            },
            {
                "command": "python -B .agents/manage.py setup --check --no-link-skills",
                "what": "Read-only setup verification for temporary or inspection copies that must not claim Codex, Claude, or Copilot user skill folders.",
            },
            {
                "command": "python -B .agents/manage.py project-kickoff --target <project>",
                "what": "Read-only first-use planning for a target project: copy contract, install/update plan, one primary next action, source-vs-target command groups, project-context review, workflow command recommendations, and copyable chat prompts.",
            },
            {
                "command": "python -B .agents/manage.py project-kickoff --target <project> --apply",
                "what": "Runs the safe first-use sequence: install/update harness, setup, setup --check, status --fast, then reports project-context review status without starting or resuming workflows.",
            },
            {
                "command": "python -B .agents/manage.py project-context-review --target <project>",
                "what": "Reads generated project context and reports structured missing stack, commands, external systems, persistence, CI, config, generated-boundary, and validation facts.",
            },
            {
                "command": "python -B .agents/manage.py project-context-review --target <project> --write-review",
                "what": "Writes intermediate `docs/project/review/project-context-review.md` and `.json` answer artifacts without editing canonical project context.",
            },
            {
                "command": "python -B .agents/manage.py project-context-apply-review --target <project>",
                "what": "Previews how answered review artifact facts would be added to canonical `docs/project/project-context.md` without writing.",
            },
            {
                "command": "python -B .agents/manage.py project-context-apply-review --target <project> --apply",
                "what": "Writes answered review facts into a marker-bounded reviewed-facts section in `docs/project/project-context.md`; it does not rewrite other project context sections.",
            },
            {
                "command": "python -B .agents/manage.py dotnet-context --target <project>",
                "what": "Read-only .NET project context inspection for SDK/runtime, solution/project shape, build policy, CI command candidates, config key inventory, persistence, NuGet/feed policy, and validation candidates; it never runs restore/build/test/package/tool commands.",
            },
            {
                "command": "python -B .agents/manage.py dotnet-context --target <project> --solution <solution.sln> --project <project.csproj>",
                "what": "Narrows .NET context inspection to a selected solution and project for monorepos while keeping the same read-only no-restore policy.",
            },
            {
                "command": "python -B .agents/manage.py dotnet-context --target <project> --dotnet-executable <path-to-dotnet>",
                "what": "Uses a trusted local SDK executable for the same safe probes when the project-required dotnet is not first on PATH.",
            },
            {
                "command": "python -B .agents/manage.py dotnet-context --target <project> --write-evidence",
                "what": "Writes project-local `docs/project/dotnet-context/dotnet-context.json` and `.md` evidence artifacts without editing canonical `docs/project/project-context.md`.",
            },
            {
                "command": "python -B .agents/manage.py dotnet-context --target <project> --baseline docs/project/dotnet-context/dotnet-context.json",
                "what": "Compares the current static .NET context against a previous report and flags project, framework, NuGet source, or build-policy drift.",
            },
            {
                "command": "python -B .agents/manage.py install-harness --target <project> --run-setup-check",
                "what": "Copies the resolved standard feature profile, initializes partial-profile generated outputs with `setup --no-link-skills`, then runs `setup --check --no-link-skills` without depending on global user profile state.",
            },
            {
                "command": "python -B .agents/manage.py install-harness --target <project> --profile minimal --with-feature story-workflow --dry-run",
                "what": "Previews a feature-adjusted install and reports the resolved features, sorted source file manifest, and stable SHA-256 digest without writing the target.",
            },
            {
                "command": "python -B .agents/manage.py harness-promote --target <project> --dry-run",
                "what": "Classifies consumer harness edits against the source payload and install manifest before any selected file is promoted back.",
            },
            {
                "command": "python -B .agents/manage.py install-harness-smoke --fast --format json",
                "what": "Creates a temporary target, writes `.agents/harness-smoke-target.json`, verifies clean copy exclusions, runs no-link setup checks, project-context check, and startup navigation proof, skips local AI/workflow start-resume, and removes the target unless `--keep` is used.",
            },
            {
                "command": "python -B .agents/manage.py install-harness-smoke --format json",
                "what": "Runs the full temporary-target dogfood: prepared install, smoke marker, clean-state check, no-link setup, project-context check, startup navigation proof, portable ripgrep/local-AI preparation, workflow start/resume, context packet check, and consumer workflow smoke.",
            },
        ],
        "commands": command_index(parser),
    }
    if shortcut:
        selected_paths = [path for path in common_paths if path["name"] in SHORTCUT_COMMON_PATHS.get(shortcut, {shortcut})]
        selected_names = set(SHORTCUT_COMMAND_NAMES.get(shortcut, set()))
        report["shortcut"] = shortcut
        report["common_paths"] = selected_paths
        report["commands"] = [row for row in report["commands"] if row.get("name") in selected_names]
        report["groups"] = sorted({str(row["group"]) for row in report["commands"]})
    if summary:
        report = summarize_command_report(report, compact=compact)
    if output_format == "json":
        output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    elif output_format == "tsv":
        output = render_commands_tsv(report)
    else:
        output = render_commands_markdown(report)
    if write_path:
        target = Path(write_path).expanduser()
        if root is not None:
            if not target.is_absolute():
                target = root / target
            target = target.resolve(strict=False)
            try:
                target.relative_to(root.resolve())
            except ValueError as exc:
                raise SystemExit("commands --write path must stay inside the repository") from exc
        if output_format == "markdown" and target.suffix.lower() == ".md":
            output = add_doc_frontmatter(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output.rstrip() + "\n", encoding="utf-8", newline="\n")
        report["written_path"] = str(target)
        if output_format == "json":
            output = json.dumps(report, indent=2, sort_keys=True) + "\n"
        elif output_format == "markdown":
            output += f"\nWritten: `{target}`\n"
    print(output, end="" if output.endswith("\n") else "\n")
    return 0
