"""Documentation-delta helpers for workflow context packets."""

from __future__ import annotations

from pathlib import Path

from workflow_support.context_paths import documentation_delta_relative_paths, normalize_path_handle, unique_list


def run_packet_path_values(root: Path, run_dir: Path, run_packet: dict[str, object], keys: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for key in keys:
        items = run_packet.get(key)
        if isinstance(items, list):
            values.extend(normalize_path_handle(root, run_dir, item) for item in items)
    return unique_list([value for value in values if value])


def documentation_path(value: str) -> bool:
    normalized = value.replace("\\", "/").lstrip("/")
    return normalized.startswith("docs/") and normalized.lower().endswith(".md")


def build_documentation_delta(root: Path, run_dir: Path, run_packet: dict[str, object]) -> dict[str, object]:
    raw = run_packet.get("documentation") if isinstance(run_packet.get("documentation"), dict) else {}
    discovered = run_packet_path_values(root, run_dir, run_packet, ("changed_files", "files_changed"))
    raw_changed_docs = raw.get("changed_docs") if isinstance(raw.get("changed_docs"), list) else []
    changed_docs = unique_list(
        [
            *[str(item) for item in raw_changed_docs],
            *[path for path in discovered if documentation_path(path)],
        ]
    )
    required_updates = (
        [str(item) for item in raw.get("required_updates", [])]
        if isinstance(raw.get("required_updates"), list)
        else []
    )
    evidence_paths = (
        [normalize_path_handle(root, run_dir, item) for item in raw.get("evidence_paths", [])]
        if isinstance(raw.get("evidence_paths"), list)
        else []
    )
    no_doc_impact_reason = str(raw.get("no_doc_impact_reason", "")).strip()
    frontmatter_checked = bool(raw.get("frontmatter_checked", False))
    map_checked = bool(raw.get("map_checked", False))
    issues: list[str] = []
    if changed_docs and not frontmatter_checked:
        issues.append("changed docs need frontmatter check evidence")
    if changed_docs and not map_checked:
        issues.append("changed docs need documentation-map reachability check evidence")
    if required_updates and not changed_docs and not no_doc_impact_reason:
        issues.append("documentation updates are required but no changed docs or no-impact reason was recorded")
    if not changed_docs and not required_updates and not no_doc_impact_reason:
        no_doc_impact_reason = "No documentation changes recorded."
    json_path, markdown_path = documentation_delta_relative_paths(root, run_dir)
    return {
        "schema_version": 1,
        "tool": "workflow-manager.documentation-delta",
        "status": "ok" if not issues else "needs-attention",
        "changed_docs": changed_docs,
        "required_updates": required_updates,
        "no_doc_impact_reason": no_doc_impact_reason,
        "frontmatter_checked": frontmatter_checked,
        "map_checked": map_checked,
        "evidence_paths": unique_list([path for path in evidence_paths if path]),
        "issues": issues,
        "paths": {"json": json_path, "markdown": markdown_path},
    }


def render_documentation_delta_markdown(packet: dict[str, object]) -> str:
    lines = [
        "# Documentation Delta",
        "",
        f"- Status: {packet.get('status')}",
        f"- Frontmatter checked: {packet.get('frontmatter_checked')}",
        f"- Documentation map checked: {packet.get('map_checked')}",
        "",
        "## Changed Docs",
        "",
    ]
    changed_docs = packet.get("changed_docs") if isinstance(packet.get("changed_docs"), list) else []
    lines.extend(f"- `{item}`" for item in changed_docs) if changed_docs else lines.append("- none recorded")
    required = packet.get("required_updates") if isinstance(packet.get("required_updates"), list) else []
    lines.extend(["", "## Required Updates", ""])
    lines.extend(f"- {item}" for item in required) if required else lines.append("- none recorded")
    reason = str(packet.get("no_doc_impact_reason", "")).strip()
    if reason:
        lines.extend(["", "## No Documentation Impact Reason", "", f"- {reason}"])
    evidence = packet.get("evidence_paths") if isinstance(packet.get("evidence_paths"), list) else []
    if evidence:
        lines.extend(["", "## Evidence", ""])
        lines.extend(f"- `{item}`" for item in evidence)
    issues = packet.get("issues") if isinstance(packet.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {item}" for item in issues)
    lines.append("")
    return "\n".join(lines)
