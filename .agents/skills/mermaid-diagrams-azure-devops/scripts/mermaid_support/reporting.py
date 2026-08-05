#!/usr/bin/env python3
"""Report and inventory rendering for Mermaid validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import setup_vscode_mermaid_preview

from mermaid_support.file_scanning import extract_blocks
from mermaid_support.models import DiagramBlock
from mermaid_support.syntax_rules import detect_diagram_type
from mermaid_support.validation_core import validate_paths


def diagram_inventory(paths: list[Path]) -> dict[str, Any]:
    blocks, files = extract_blocks(paths)
    by_type: dict[str, int] = {}
    by_wrapper: dict[str, int] = {}
    for block in blocks:
        diagram_type, _errors = detect_diagram_type(block)
        by_type[diagram_type or "unknown"] = by_type.get(diagram_type or "unknown", 0) + 1
        by_wrapper[block.wrapper] = by_wrapper.get(block.wrapper, 0) + 1
    return {
        "schema_version": 1,
        "tool": "mermaid-diagrams-azure-devops.inventory",
        "files_scanned": [str(path) for path in files],
        "diagram_count": len(blocks),
        "by_type": dict(sorted(by_type.items())),
        "by_wrapper": dict(sorted(by_wrapper.items())),
        "diagrams": [
            {
                "path": block.path,
                "start_line": block.start_line,
                "end_line": block.end_line,
                "wrapper": block.wrapper,
                "type": detect_diagram_type(block)[0] or "unknown",
            }
            for block in blocks
        ],
    }


def finding_status(errors: int, warnings: int = 0) -> str:
    if errors:
        return "fail"
    if warnings:
        return "warn"
    return "pass"


def render_status(render_report: dict[str, Any] | None) -> str:
    if not render_report or not render_report.get("attempted"):
        return "skipped"
    if render_report.get("failures"):
        return "fail"
    if render_report.get("warnings"):
        return "warn"
    if render_report.get("available"):
        return "pass"
    return "warn"


def setup_status(setup_report: dict[str, Any]) -> str:
    if setup_report.get("skipped"):
        return "skipped"
    if setup_report.get("errors"):
        return "fail"
    if setup_report.get("warnings"):
        return "warn"
    return "pass"


def build_doctor_report(paths: list[Path], *, mmdc: str = "mmdc") -> dict[str, Any]:
    validation = validate_paths(
        paths,
        render=True,
        require_render=False,
        mmdc=mmdc,
        auto_install_mmdc=False,
    )
    setup = setup_vscode_mermaid_preview.setup_vscode_preview(auto_install=False)
    blocks = validation["blocks"]
    wrapper_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for block in blocks:
        wrapper = str(block.get("wrapper") or "unknown")
        wrapper_counts[wrapper] = wrapper_counts.get(wrapper, 0) + 1
        diagram_type, _errors = detect_diagram_type(
            DiagramBlock(
                path=str(block.get("path", "")),
                start_line=int(block.get("start_line", 0)),
                end_line=int(block.get("end_line", 0)),
                wrapper=wrapper,
                opening=str(block.get("opening", "")),
                body=str(block.get("body", "")),
                raw_body=str(block.get("raw_body", "")),
            )
        )
        type_counts[diagram_type or "unknown"] = type_counts.get(diagram_type or "unknown", 0) + 1

    warning_groups = validation.get("warning_groups", {})
    parser_warnings = len(warning_groups.get("parser", [])) if isinstance(warning_groups, dict) else 0
    wrapper_warnings = len(warning_groups.get("azure_wrapper", [])) if isinstance(warning_groups, dict) else 0
    static_errors = [
        item for item in validation["errors"]
        if item.get("path") != "<render>" and "render failed" not in item.get("message", "")
    ]
    render_report = validation.get("render")
    render_failures = len(render_report.get("failures", [])) if isinstance(render_report, dict) else 0
    render_warnings = len(render_report.get("warnings", [])) if isinstance(render_report, dict) else 0

    hard_failures = len(static_errors) + render_failures
    soft_findings = (
        parser_warnings
        + wrapper_warnings
        + render_warnings
        + len(setup.get("warnings", []))
        + len(setup.get("errors", []))
    )
    status = {
        "overall": finding_status(hard_failures, soft_findings),
        "parser": finding_status(len(static_errors), parser_warnings),
        "wrapper": finding_status(0, wrapper_warnings),
        "render": render_status(render_report if isinstance(render_report, dict) else None),
        "setup": setup_status(setup),
    }
    return {
        "schema_version": 1,
        "tool": "mermaid-diagrams-azure-devops.doctor",
        "ok": status["overall"] != "fail",
        "status": status,
        "write_policy": {
            "writes_allowed": False,
            "auto_fix": False,
            "auto_install_mmdc": False,
            "vscode_auto_install": False,
        },
        "files_scanned": validation["files_scanned"],
        "block_count": validation["block_count"],
        "artifact_count": validation.get("artifact_count", 0),
        "artifacts": validation.get("artifacts", []),
        "diagram_types": dict(sorted(type_counts.items())),
        "wrappers": dict(sorted(wrapper_counts.items())),
        "static_validation": {
            "valid": not static_errors,
            "error_count": len(static_errors),
            "warning_count": parser_warnings,
            "errors": static_errors,
            "warnings": warning_groups.get("parser", []) if isinstance(warning_groups, dict) else [],
        },
        "azure_wrapper": {
            "status": status["wrapper"],
            "warning_count": wrapper_warnings,
            "warnings": warning_groups.get("azure_wrapper", []) if isinstance(warning_groups, dict) else [],
        },
        "render": {
            "status": status["render"],
            "attempted": bool(isinstance(render_report, dict) and render_report.get("attempted")),
            "available": bool(isinstance(render_report, dict) and render_report.get("available")),
            "command": mmdc,
            "auto_install_requested": False,
            "failure_count": render_failures,
            "warning_count": render_warnings,
            "failures": render_report.get("failures", []) if isinstance(render_report, dict) else [],
            "warnings": render_report.get("warnings", []) if isinstance(render_report, dict) else [],
        },
        "setup": {
            "status": status["setup"],
            "supported": bool(setup.get("supported")),
            "skipped": bool(setup.get("skipped")),
            "skip_reason": setup.get("skip_reason"),
            "ide_detected": setup.get("ide_detected"),
            "recommended_extension": setup.get("recommended_extension"),
            "recommended_installed": bool(setup.get("recommended_installed")),
            "install_attempted": bool(setup.get("install_attempted")),
            "conflict_count": len(setup.get("conflicts", [])),
            "warning_count": len(setup.get("warnings", [])),
            "error_count": len(setup.get("errors", [])),
            "warnings": setup.get("warnings", []),
            "errors": setup.get("errors", []),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Mermaid Validation Report",
        "",
        f"- Files scanned: {len(report['files_scanned'])}",
        f"- Mermaid blocks: {report['block_count']}",
        f"- Materialized artifacts: {report.get('artifact_count', 0)}",
        f"- Status: {'pass' if report['valid'] else 'fail'}",
        "",
    ]
    if report["errors"]:
        lines.extend(["## Errors", ""])
        for item in report["errors"]:
            location = f"{item['path']}:{item['line']}" if item["line"] else item["path"]
            lines.append(f"- `{location}` {item['message']}")
        lines.append("")
    if report["warnings"]:
        lines.extend(["## Warnings", ""])
        for item in report["warnings"]:
            location = f"{item['path']}:{item['line']}" if item["line"] else item["path"]
            lines.append(f"- `{location}` {item['message']}")
        lines.append("")
    if not report["errors"] and not report["warnings"]:
        lines.append("No Mermaid issues found.")
    groups = report.get("warning_groups")
    if isinstance(groups, dict) and any(groups.values()):
        lines.extend(["", "## Warning Groups", ""])
        lines.append(f"- Parser warnings: {len(groups.get('parser', []))}")
        lines.append(f"- Azure wrapper warnings: {len(groups.get('azure_wrapper', []))}")
        lines.append(f"- Render warnings: {len(groups.get('render', []))}")
    return "\n".join(lines).rstrip()


def render_doctor(report: dict[str, Any]) -> str:
    status = report["status"]
    lines = [
        "# Mermaid Doctor Evidence Packet",
        "",
        f"- Overall: {status['overall']}",
        f"- Parser/static: {status['parser']}",
        f"- Azure wrapper: {status['wrapper']}",
        f"- Render: {status['render']}",
        f"- Setup: {status['setup']}",
        f"- Files scanned: {len(report['files_scanned'])}",
        f"- Mermaid blocks: {report['block_count']}",
        f"- Materialized artifacts: {report.get('artifact_count', 0)}",
        "- Writes: none; auto-fix and auto-install are disabled",
        "",
        "## Diagram Inventory",
        "",
    ]
    if report["diagram_types"]:
        lines.extend(f"- `{key}`: {value}" for key, value in report["diagram_types"].items())
    else:
        lines.append("- None.")
    lines.extend(["", "## Wrappers", ""])
    if report["wrappers"]:
        lines.extend(f"- `{key}`: {value}" for key, value in report["wrappers"].items())
    else:
        lines.append("- None.")

    static = report["static_validation"]
    wrapper = report["azure_wrapper"]
    render = report["render"]
    setup = report["setup"]
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            f"- Static errors: {static['error_count']}",
            f"- Static warnings: {static['warning_count']}",
            f"- Wrapper warnings: {wrapper['warning_count']}",
            f"- Render command: `{render['command']}`",
            f"- Render available: {'yes' if render['available'] else 'no'}",
            f"- Render failures: {render['failure_count']}",
            f"- Render warnings: {render['warning_count']}",
            f"- Setup IDE: {setup['ide_detected'] or 'unknown'}",
            f"- Recommended extension installed: {'yes' if setup['recommended_installed'] else 'no'}",
            f"- Setup warnings: {setup['warning_count']}",
            f"- Setup errors: {setup['error_count']}",
        ]
    )

    for section_name, items in (
        ("Static Errors", static["errors"]),
        ("Static Warnings", static["warnings"]),
        ("Wrapper Warnings", wrapper["warnings"]),
        ("Render Failures", render["failures"]),
        ("Render Warnings", render["warnings"]),
    ):
        if not items:
            continue
        lines.extend(["", f"## {section_name}", ""])
        for item in items:
            location = f"{item['path']}:{item['line']}" if item.get("line") else item.get("path", "")
            lines.append(f"- `{location}` {item.get('message', '')}")
    if setup["skip_reason"]:
        lines.extend(["", "## Setup Skip", "", f"- {setup['skip_reason']}"])
    if setup["errors"]:
        lines.extend(["", "## Setup Errors", ""])
        lines.extend(f"- {item}" for item in setup["errors"])
    if setup["warnings"]:
        lines.extend(["", "## Setup Warnings", ""])
        lines.extend(f"- {item}" for item in setup["warnings"])
    return "\n".join(lines).rstrip()


def render_inventory(report: dict[str, Any]) -> str:
    lines = [
        "# Mermaid Diagram Inventory",
        "",
        f"- Files scanned: {len(report['files_scanned'])}",
        f"- Diagrams: {report['diagram_count']}",
        "",
        "## Types",
        "",
    ]
    lines.extend(f"- `{key}`: {value}" for key, value in report["by_type"].items()) if report["by_type"] else lines.append("- None.")
    lines.extend(["", "## Diagrams", ""])
    for item in report["diagrams"]:
        lines.append(f"- `{item['path']}:{item['start_line']}` {item['wrapper']} {item['type']}")
    if not report["diagrams"]:
        lines.append("- None.")
    return "\n".join(lines)
