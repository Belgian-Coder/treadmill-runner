#!/usr/bin/env python3
"""CLI and compatibility surface for Mermaid diagram validation."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from mermaid_support.artifact_validation import (
    is_mermaid_svg_asset,
    materialized_diagram_artifacts,
    path_allows_unlinked_mermaid_asset,
    svg_attr,
    validate_materialized_diagram_artifacts,
    validate_materialized_svg,
    validate_unlinked_materialized_assets,
)
from mermaid_support.autofix import apply_autofix, autofix_text
from mermaid_support.file_scanning import (
    changed_markdown_files,
    code_fence_mask,
    diagram_input_files,
    extract_blocks,
    extract_blocks_from_text,
    markdown_files,
    mermaid_asset_files,
    normalize_body,
    path_allows_markdown_mermaid_blocks,
    read_text,
    resolve_markdown_target,
    subprocess,
    strip_markdown_link_target,
)
from mermaid_support.models import (
    DiagramBlock,
    Finding,
    MARKDOWN_SUFFIXES,
    MERMAID_IMAGE_SUFFIXES,
    MERMAID_SOURCE_SUFFIXES,
    MIN_PYTHON,
    MMDC_RENDER_FLAGS,
    MaterializedDiagramArtifact,
    RenderResult,
    SCAN_SUFFIXES,
)
from mermaid_support.rendering import (
    add_setup_finding,
    command_output,
    install_mmdc,
    node_version_is_compatible,
    parse_node_version,
    render_blocks,
    shutil,
)
from mermaid_support.reporting import (
    build_doctor_report,
    diagram_inventory,
    finding_status,
    render_doctor,
    render_inventory,
    render_markdown,
    render_status,
    setup_status,
)
from mermaid_support.syntax_rules import (
    AZURE_CLOSE_RE,
    AZURE_COMPACT_OPEN_RE,
    AZURE_OPEN_RE,
    FENCED_CLOSE_RE,
    FENCED_OPEN_RE,
    GENERIC_FENCE_RE,
    detect_diagram_type,
    first_content_line,
    first_content_line_info,
    is_quoted_label,
    parse_subgraph_id,
    stripped_label,
    validate_azure_indentation,
    validate_azure_wrapper,
    validate_body,
    validate_graph_labels,
    validate_graph_subgraph_edges,
    validate_graph_subgraphs,
)
from mermaid_support.validation_core import validate_paths

def require_supported_python() -> None:
    if sys.version_info >= MIN_PYTHON:
        return
    current = ".".join(str(part) for part in sys.version_info[:3])
    raise SystemExit(f"Python 3.12+ is required; current interpreter is Python {current}.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Markdown files or directories to scan")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown", dest="output_format")
    parser.add_argument("--static-only", action="store_true", help="read-only: run static checks only")
    parser.add_argument("--fix", action="store_true", help="write: apply common Azure DevOps Mermaid compatibility fixes")
    parser.add_argument("--changed-only", action="store_true", help="read-only: validate changed Markdown files from git diff")
    parser.add_argument("--inventory", action="store_true", help="read-only: print diagram inventory instead of validation")
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="read-only/no-install evidence packet; may use temporary render files when mmdc exists",
    )
    parser.add_argument(
        "--render",
        action="store_true",
        help="render with mmdc; may auto-install default mmdc unless --no-auto-install-mmdc is used",
    )
    parser.add_argument("--require-render", action="store_true", help="fail when mmdc is missing or rendering fails")
    parser.add_argument(
        "--allow-markdown-blocks",
        action="store_true",
        help="allow temporary Markdown Mermaid blocks for draft syntax checks before materialization",
    )
    parser.add_argument(
        "--non-blocking",
        action="store_true",
        help="always exit 0 while still reporting validation failures in the output",
    )
    auto_install_group = parser.add_mutually_exclusive_group()
    auto_install_group.add_argument(
        "--auto-install-mmdc",
        action="store_true",
        default=None,
        help="explicitly allow Mermaid CLI setup; this is already the default for --render with the default mmdc command",
    )
    auto_install_group.add_argument(
        "--no-auto-install-mmdc",
        action="store_false",
        dest="auto_install_mmdc",
        help="disable automatic Mermaid CLI setup when mmdc is missing; use when installs are forbidden",
    )
    parser.add_argument("--mmdc", default="mmdc", help="Mermaid CLI command name; default: mmdc")
    return parser


def main() -> int:
    require_supported_python()
    args = build_parser().parse_args()
    if args.changed_only:
        paths = changed_markdown_files(Path.cwd())
    else:
        if not args.paths:
            raise SystemExit("at least one path is required unless --changed-only is used")
        paths = [Path(value).expanduser().resolve() for value in args.paths]
    if args.doctor and args.fix:
        raise SystemExit("--doctor is read-only and cannot be combined with --fix")
    if args.doctor:
        report = build_doctor_report(paths, mmdc=args.mmdc)
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_doctor(report))
        if args.non_blocking:
            return 0
        return 1 if report["status"]["overall"] == "fail" else 0
    if args.fix:
        fix_report = apply_autofix(paths)
        if fix_report["changed"]:
            print("Applied Mermaid auto-fixes:")
            for path in fix_report["changed"]:
                print(f"- {path}")
    if args.inventory:
        report = diagram_inventory(paths)
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_inventory(report))
        return 0
    report = validate_paths(
        paths,
        render=False if args.static_only else args.render,
        require_render=False if args.static_only else args.require_render,
        mmdc=args.mmdc,
        auto_install_mmdc=args.auto_install_mmdc,
        allow_markdown_blocks=args.allow_markdown_blocks,
    )
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    if args.non_blocking:
        return 0
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
