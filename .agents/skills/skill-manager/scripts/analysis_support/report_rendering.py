#!/usr/bin/env python3
"""Markdown rendering for skill-manager location analysis."""

from __future__ import annotations

import sys
from collections import defaultdict
from typing import Any

sys.dont_write_bytecode = True


def grouped_warning_lines(warnings: list[object], *, limit: int = 12) -> list[str]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in warnings:
        if not isinstance(item, dict):
            continue
        groups[str(item.get("category") or "unknown")].append(item)
    if not groups:
        return []
    lines = ["Warning Groups:"]
    for category, items in sorted(groups.items(), key=lambda entry: (-len(entry[1]), entry[0]))[:limit]:
        examples: list[str] = []
        for item in items[:3]:
            path = str(item.get("path") or "unknown")
            if item.get("line"):
                path = f"{path}:{item.get('line')}"
            examples.append(f"`{path}`")
        suffix = f" examples: {', '.join(examples)}" if examples else ""
        lines.append(f"- {category}: {len(items)}{suffix}")
    if len(groups) > limit:
        lines.append(f"- ... {len(groups) - limit} more warning groups omitted.")
    return lines


def render_report_from_analysis(analysis: dict[str, object]) -> str:
    if "result" in analysis:
        lines = [
            "# Skill Manager Analysis",
            "",
            f"- Location: `{analysis['location']}`",
            f"- Type: {analysis['type']}",
            "",
            "## Result",
            "",
            str(analysis["result"]),
            "",
            "## Suggested Next Step",
            "",
        ]
        lines.extend(f"- {item}" for item in analysis["suggested_next_steps"])
        return "\n".join(lines)

    structure = analysis["structure"]
    top_level_areas = structure["top_level_areas"]
    common_file_types = structure["common_file_types"]
    manifests = structure["manifests"]

    def render_counter(items: object) -> str:
        values = [
            f"{item['name']} ({item['count']})"
            for item in items
        ]
        return ", ".join(values) or "none"

    lines: list[str] = [
        "# Skill Manager Analysis",
        "",
        f"- Location: `{analysis['location']}`",
        f"- Type: {analysis['type']}",
        f"- Files scanned: {analysis['files_scanned']}",
        "",
        "## What It Appears To Do",
        "",
    ]
    lines.extend(f"- {item}" for item in analysis["purpose"])

    review_packet = analysis.get("review_packet", {})
    if isinstance(review_packet, dict):
        lines.extend(["", "## Review Packet", ""])
        read_first = review_packet.get("read_these_first", [])
        if isinstance(read_first, list) and read_first:
            lines.append("Read first:")
            for item in read_first:
                if isinstance(item, dict):
                    lines.append(f"- `{item.get('path')}` - {item.get('reason')}")
        else:
            lines.append("Read first: no high-signal entry files identified.")

        entry_points = review_packet.get("likely_entry_points", [])
        if isinstance(entry_points, list) and entry_points:
            lines.extend(["", "Likely entry points:"])
            for item in entry_points:
                if isinstance(item, dict):
                    lines.append(f"- `{item.get('path')}` - {item.get('reason')}")

        active_work = review_packet.get("active_work", [])
        if isinstance(active_work, list) and active_work:
            lines.extend(["", "Active work:"])
            lines.extend(f"- {item}" for item in active_work[:8])

        caveats = review_packet.get("caveats", [])
        if isinstance(caveats, list) and caveats:
            lines.extend(["", "Caveats:"])
            lines.extend(f"- {item}" for item in caveats[:6])

    lines.extend(
        [
            "",
            "## Structure Signals",
            "",
            f"- Top-level areas: {render_counter(top_level_areas)}",
            f"- Common file types: {render_counter(common_file_types)}",
            f"- Manifests found: {', '.join(f'`{item}`' for item in manifests) if manifests else 'none'}",
            "",
            "## Dependencies",
            "",
        ]
    )
    dependencies = analysis["dependencies"]
    lines.extend(f"- {item}" for item in dependencies[:80])
    if len(dependencies) > 80:
        lines.append(f"- ... {len(dependencies) - 80} more dependency entries omitted.")

    lines.extend(["", "## Scripts", ""])
    scripts = analysis["scripts"]
    if scripts:
        lines.extend(f"- `{item}`" for item in scripts[:80])
        if len(scripts) > 80:
            lines.append(f"- ... {len(scripts) - 80} more scripts omitted.")
    else:
        lines.append("- No scripts detected.")

    lines.extend(["", "## Disallowed Scripts And Python Conversion Plan", ""])
    conversion_plans = analysis["conversion_plans"]
    if conversion_plans:
        grouped: dict[str, list[str]] = defaultdict(list)
        for item in conversion_plans:
            text = str(item)
            key = text.split(":", 1)[0].lstrip("- ").strip() or "script"
            grouped[key].append(text)
        for key, values in sorted(grouped.items(), key=lambda entry: (-len(entry[1]), entry[0])):
            lines.append(f"- {key}: {len(values)} conversion item(s)")
            for value in values[:4]:
                lines.append(f"  - {value.lstrip('- ')}")
            if len(values) > 4:
                lines.append(f"  - ... {len(values) - 4} more omitted.")
    else:
        lines.append("- No disallowed shell, batch, or PowerShell scripts detected.")

    lines.extend(["", "## Data And Security Signals", ""])
    network = analysis["network_signals"]
    if network:
        lines.append("Network or upload indicators:")
        lines.extend(f"- {item}" for item in network)
    else:
        lines.append("- No obvious network or upload indicators detected in scanned text files.")
    security = analysis["credential_signals"]
    if security:
        lines.append("")
        lines.append("Credential or secret indicators:")
        lines.extend(f"- {item}" for item in security)
    else:
        lines.append("- No obvious credential or secret indicators detected in scanned text files.")
    evidence = analysis.get("evidence", [])
    if isinstance(evidence, list) and evidence:
        lines.extend(["", "Evidence records:"])
        for item in evidence[:40]:
            if not isinstance(item, dict):
                continue
            line = item.get("line")
            location = item.get("path")
            if line:
                location = f"{location}:{line}"
            declared = "declared" if item.get("declared") else "undeclared"
            snippet = item.get("snippet")
            suffix = f" - {snippet}" if snippet else ""
            lines.append(
                f"- {item.get('category')}: `{location}` ({item.get('source')}, {declared}) - "
                f"{item.get('signal')}{suffix}"
            )
        if len(evidence) > 40:
            lines.append(f"- ... {len(evidence) - 40} more evidence records omitted.")

    import_review = analysis.get("import_review", {})
    if isinstance(import_review, dict):
        lines.extend(["", "## Import Review", ""])
        lines.append(f"- Profile: {import_review.get('profile', 'unknown')}")
        lines.append(f"- Status: {import_review.get('status', 'unknown')}")
        lines.append(f"- Warnings: {import_review.get('warning_count', 0)}")
        facts = import_review.get("facts", {})
        if isinstance(facts, dict):
            lines.append("")
            lines.append("Facts:")
            for key in sorted(facts):
                values = facts.get(key)
                if isinstance(values, list) and values:
                    rendered_values = ", ".join(
                        f"`{item}`" if isinstance(item, str) else str(item)
                        for item in values[:8]
                    )
                    suffix = f" (+{len(values) - 8} more)" if len(values) > 8 else ""
                    lines.append(f"- {key}: {rendered_values}{suffix}")
        warnings = import_review.get("warnings", [])
        if isinstance(warnings, list) and warnings:
            lines.append("")
            lines.extend(grouped_warning_lines(warnings))
            lines.append("")
            lines.append("Warning Details:")
            for item in warnings[:40]:
                if not isinstance(item, dict):
                    continue
                location = item.get("path")
                if item.get("line"):
                    location = f"{location}:{item.get('line')}"
                lines.append(
                    f"- {item.get('category')}: `{location}` - {item.get('message')}"
                )
            if len(warnings) > 40:
                lines.append(f"- ... {len(warnings) - 40} more import warnings omitted.")

    static_audit = analysis.get("static_audit", {})
    if isinstance(static_audit, dict):
        lines.extend(["", "## Static Audit", ""])
        lines.append(f"- Verdict: {str(static_audit.get('verdict', 'unknown')).upper()}")
        lines.append(f"- Summary: {static_audit.get('summary', 'No summary.')}")
        findings = static_audit.get("findings", [])
        if isinstance(findings, list) and findings:
            lines.append("")
            lines.append("Findings:")
            for item in findings[:50]:
                if not isinstance(item, dict):
                    continue
                location = item.get("path")
                if item.get("line"):
                    location = f"{location}:{item.get('line')}"
                lines.append(
                    f"- {str(item.get('severity')).upper()} `{location}` "
                    f"{item.get('rule')}: {item.get('detail')}"
                )
            if len(findings) > 50:
                lines.append(f"- ... {len(findings) - 50} more audit findings omitted.")
        else:
            lines.append("- No static audit findings in scanned files.")

    fit = analysis.get("skill_or_workflow_fit", {})
    if isinstance(fit, dict):
        lines.extend(["", "## Skill Or Workflow Fit", ""])
        lines.append(f"- Fit: {fit.get('fit', 'unknown')}")
        lines.append(f"- Recommendation: {fit.get('recommendation', 'Review manually.')}")
        reasons = fit.get("reasons", [])
        if isinstance(reasons, list) and reasons:
            lines.append("- Reasons:")
            lines.extend(f"  - {item}" for item in reasons[:6])

    lines.extend(["", "## Improvement Opportunities", ""])
    lines.extend(f"- {item}" for item in analysis["improvement_opportunities"])

    options = analysis.get("promotion_decision_options", [])
    if isinstance(options, list) and options:
        lines.extend(["", "## Promotion Decision Table", ""])
        lines.append("| Decision | Use When | Next Step |")
        lines.append("|---|---|---|")
        for item in options:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"| {item.get('decision', '')} | {item.get('use_when', '')} | "
                f"{item.get('next_step', '')} |"
            )

    lines.extend(
        [
            "",
            "## Local AI Skill Review Snippets",
            "",
            "- `python -B .agents/manage.py local-ai task --task code-review --input <analysis-report>`",
            "- `python -B .agents/manage.py local-ai task --task patch-draft --input <analysis-report>`",
            "- `rg -n -i \"<candidate keywords>\" .agents/skills <candidate-path>`",
        ]
    )

    lines.extend(
        [
            "",
            "## Recommended Review Decision",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in analysis["recommended_review_decision"])
    return "\n".join(lines)
