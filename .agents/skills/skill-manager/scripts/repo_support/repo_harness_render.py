#!/usr/bin/env python3
"""Markdown and JSON rendering helpers for harness install reports."""

from __future__ import annotations

import json

PAYLOAD_MANIFEST_REL = ".agents/harness-payload.json"
PAYLOAD_MANIFEST_LABEL = "Payload manifest"


def limited_rows(values: list[object], *, limit: int = 20) -> tuple[list[object], int]:
    return values[:limit], max(0, len(values) - limit)


def render_markdown(report: dict[str, object]) -> str:
    summary = report.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    lines = [
        "# Install Harness",
        "",
        f"- Status: {report.get('status')}",
        f"- Source: `{report.get('source_root')}`",
        f"- Target: `{report.get('target_root')}`",
        f"- Mode: {'dry-run' if report.get('dry_run') else 'write'}",
        f"- Profile: `{(report.get('profile') or {}).get('name', 'standard') if isinstance(report.get('profile'), dict) else 'standard'}`",
        f"- Resolved features: {', '.join(str(item) for item in report.get('resolved_features', [])) or 'none'}",
        f"- Resolved source manifest: {len(report.get('resolved_file_manifest', [])) if isinstance(report.get('resolved_file_manifest'), list) else 0} file(s)",
        f"- Resolved manifest SHA-256: `{report.get('resolved_manifest_digest', '')}`",
        f"- Planned: {summary.get('planned_files', 0)}",
        f"- Copied: {summary.get('copied_files', 0)}",
        f"- Already present: {summary.get('already_present_files', 0)}",
        f"- Collisions: {summary.get('collision_files', 0)}",
        f"- Excluded files: {summary.get('excluded_files', 0)}",
        f"- Manifest: `{report.get('install_manifest')}`",
        f"- {PAYLOAD_MANIFEST_LABEL}: `{PAYLOAD_MANIFEST_REL}`",
        "",
        "## What Happened",
        "",
        "- Copied the reusable harness surface into the target project, or reported the planned copy in dry-run mode.",
        "- Excluded local state by default: Git data, workflow runs, local AI caches, model payloads, tool caches, secrets, and Python bytecode.",
        "- Requires the source `.agents/harness-payload.json` schema_version 2 contract to select roots, features, profiles, and exclusions.",
        "- Writes tracked `.agents/harness.lock.json` in write mode so every clone can distinguish harness-managed files from project-owned files.",
        "- Writes `.agents/harness-install-plan.json` and `.agents/harness-install-plan.md` in write mode so the target keeps a reviewable install packet.",
    ]
    retained = report.get("retained_previous_profile_files", [])
    if isinstance(retained, list) and retained:
        lines.append(f"- Retained from the previous profile without deletion: {len(retained)}")

    human_summary = report.get("human_summary", {})
    if isinstance(human_summary, dict):
        lines.extend(["", "## Plain English Summary", "", f"- {human_summary.get('headline', report.get('status'))}"])
        plain_changes = human_summary.get("plain_changes", [])
        if isinstance(plain_changes, list):
            for item in plain_changes:
                lines.append(f"- {item}")

    install_plan_artifacts = report.get("install_plan_artifacts", {})
    if isinstance(install_plan_artifacts, dict):
        json_artifact = install_plan_artifacts.get("json", {})
        markdown_artifact = install_plan_artifacts.get("markdown", {})
        if isinstance(json_artifact, dict) and isinstance(markdown_artifact, dict):
            lines.extend(
                [
                    "",
                    "## Install Plan",
                    "",
                    f"- JSON: `{json_artifact.get('path')}` ({json_artifact.get('status')})",
                    f"- Markdown: `{markdown_artifact.get('path')}` ({markdown_artifact.get('status')})",
                ]
            )

    issues = report.get("issues", [])
    if isinstance(issues, list) and issues:
        lines.extend(["", "## Issues", ""])
        for issue in issues:
            lines.append(f"- {issue}")

    collisions = report.get("collisions", [])
    if isinstance(collisions, list) and collisions:
        rows, remaining = limited_rows(collisions)
        lines.extend(["", "## Collisions", ""])
        for row in rows:
            if isinstance(row, dict):
                lines.append(f"- `{row.get('path')}`: {row.get('reason')}")
        if remaining:
            lines.append(f"- ... {remaining} more")

    planned = report.get("planned", [])
    if isinstance(planned, list) and planned:
        rows, remaining = limited_rows(planned)
        heading = "Planned Writes" if report.get("dry_run") else "Written Files"
        lines.extend(["", f"## {heading}", ""])
        for row in rows:
            if isinstance(row, dict):
                lines.append(f"- `{row.get('path')}`")
            else:
                lines.append(f"- `{row}`")
        if remaining:
            lines.append(f"- ... {remaining} more")

    excluded = report.get("excluded", [])
    if isinstance(excluded, list) and excluded:
        rows, remaining = limited_rows(excluded)
        lines.extend(["", "## Skipped By Copy Contract", ""])
        for row in rows:
            lines.append(f"- `{row}`")
        if remaining:
            lines.append(f"- ... {remaining} more")

    planned_post_install = report.get("planned_post_install", [])
    if isinstance(planned_post_install, list) and planned_post_install:
        lines.extend(["", "## Post Install", ""])
        for row in planned_post_install:
            if isinstance(row, dict):
                lines.append(f"- `{row.get('command')}`")

    post_install = report.get("post_install", [])
    if isinstance(post_install, list) and post_install:
        lines.extend(["", "## Post Install Results", ""])
        for row in post_install:
            if isinstance(row, dict):
                status = "ok" if row.get("ok") else "failed"
                lines.append(f"- {row.get('name')}: {status} - `{row.get('command')}`")

    next_commands = report.get("next_commands", [])
    if isinstance(next_commands, list) and next_commands and report.get("ok"):
        lines.extend(["", "## Next", ""])
        for command in next_commands:
            lines.append(f"- `{command}`")
    return "\n".join(lines) + "\n"


def render_copy_contract(report: dict[str, object]) -> str:
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Copy Contract Validation",
        "",
        f"- Status: {report.get('status')}",
        f"- Profile: `{(report.get('profile') or {}).get('name', 'standard') if isinstance(report.get('profile'), dict) else 'standard'}`",
        f"- Resolved features: {', '.join(str(item) for item in report.get('resolved_features', [])) or 'none'}",
        f"- Resolved manifest SHA-256: `{report.get('resolved_manifest_digest', '')}`",
        f"- Include roots: {summary.get('include_roots', 0)}",
        f"- Candidate files: {summary.get('candidate_files', 0)}",
        f"- Excluded files: {summary.get('excluded_files', 0)}",
    ]
    issues = report.get("issues", [])
    if isinstance(issues, list) and issues:
        lines.extend(["", "## Issues", ""])
        for issue in issues:
            lines.append(f"- {issue}")
    else:
        lines.extend(["", "No copy-contract issues found."])
    return "\n".join(lines) + "\n"


def render_public_export(report: dict[str, object]) -> str:
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Public Export",
        "",
        f"- Status: {report.get('status')}",
        f"- Source: `{report.get('source_root')}`",
        f"- Target: `{report.get('target_root')}`",
        f"- Profile: `{(report.get('profile') or {}).get('name', 'standard') if isinstance(report.get('profile'), dict) else 'standard'}`",
        f"- Resolved features: {', '.join(str(item) for item in report.get('resolved_features', [])) or 'none'}",
        f"- Resolved manifest SHA-256: `{report.get('resolved_manifest_digest', '')}`",
        f"- Planned files: {summary.get('planned_files', 0)}",
        f"- Exported files: {summary.get('exported_files', 0)}",
        f"- Excluded files: {summary.get('excluded_files', 0)}",
    ]
    issues = report.get("issues", [])
    if isinstance(issues, list) and issues:
        lines.extend(["", "## Issues", ""])
        for issue in issues:
            lines.append(f"- {issue}")
    collisions = report.get("collisions", [])
    if isinstance(collisions, list) and collisions:
        lines.extend(["", "## Collisions", ""])
        for row in collisions[:20]:
            if isinstance(row, dict):
                lines.append(f"- `{row.get('path')}`: {row.get('reason')}")
    return "\n".join(lines) + "\n"


def print_report(report: dict[str, object], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
