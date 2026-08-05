"""Render workflow validation reports."""

from __future__ import annotations

import json
from pathlib import Path

import workflow_manager_common as common

WORKFLOW_DECISION_TABLE = [
    {
        "decision": "keep",
        "use_when": "workflow is valid, distinct, and declared",
        "next": "sync routing and run relevant evals",
    },
    {
        "decision": "extend",
        "use_when": "validation warns about overlap with an existing workflow",
        "next": "ask whether to extend before creating a new workflow",
    },
    {
        "decision": "rewrite-first",
        "use_when": "imported workflow is broad, high-context, or missing evidence/state",
        "next": "reduce it to minimal workflow shape before promotion",
    },
    {
        "decision": "reject",
        "use_when": "request is a skill, one command, static docs, or unsafe layout",
        "next": "do not route it as a workflow",
    },
]


def render_json_report(
    root: Path,
    errors: list[str],
    warnings: list[str],
    modules: list[Path],
    *,
    summary: bool = False,
    compact: bool = False,
) -> str:
    if summary:
        data: dict[str, object] = {
            "valid": not errors,
            "automation_count": len(modules),
            "error_count": len(errors),
            "warning_count": len(warnings),
            "errors": errors,
            "warnings": warnings,
        }
        if not compact or errors or warnings:
            data["automations"] = [common.relative(root, module) for module in modules]
        return json.dumps(data, indent=2) + "\n"
    data = {
        "valid": not errors,
        "automation_count": len(modules),
        "automations": [common.relative(root, module) for module in modules],
        "decision_table": WORKFLOW_DECISION_TABLE,
        "errors": errors,
        "warnings": warnings,
    }
    return json.dumps(data, indent=2) + "\n"


def render_markdown_report(
    root: Path,
    errors: list[str],
    warnings: list[str],
    modules: list[Path],
    *,
    summary: bool = False,
    compact: bool = False,
) -> str:
    lines = [
        "# Automation Validation",
        "",
        f"- Automation modules: {len(modules)}",
        f"- Status: {'failed' if errors else 'passed'}",
    ]
    show_modules = modules and (not summary or not compact or errors or warnings)
    if show_modules:
        lines.extend(["", "## Modules", ""])
        lines.extend(f"- `{common.relative(root, module)}`" for module in modules)
    if errors:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {error}" for error in errors)
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    if summary:
        return "\n".join(lines) + "\n"
    lines.extend(["", "## Workflow Decision Table", ""])
    lines.extend(["| Decision | Use When | Next |", "|---|---|---|"])
    for row in WORKFLOW_DECISION_TABLE:
        lines.append(f"| {row['decision']} | {row['use_when']} | {row['next']} |")
    return "\n".join(lines) + "\n"
