#!/usr/bin/env python3
"""Core Mermaid validation orchestration."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from mermaid_support.artifact_validation import validate_materialized_diagram_artifacts
from mermaid_support.file_scanning import extract_blocks, path_allows_markdown_mermaid_blocks
from mermaid_support.models import Finding, RenderResult
from mermaid_support.rendering import render_blocks
from mermaid_support.syntax_rules import validate_body


def validate_paths(
    paths: list[Path],
    *,
    render: bool = False,
    require_render: bool = False,
    mmdc: str = "mmdc",
    auto_install_mmdc: bool | None = None,
    allow_markdown_blocks: bool = False,
) -> dict[str, Any]:
    blocks, files = extract_blocks(paths)
    errors: list[Finding] = []
    warnings: list[Finding] = []
    for block in blocks:
        block_errors, block_warnings = validate_body(block)
        errors.extend(block_errors)
        warnings.extend(block_warnings)
        if (
            not allow_markdown_blocks
            and block.wrapper in {"azure", "fenced"}
            and not path_allows_markdown_mermaid_blocks(block.path)
        ):
            errors.append(
                Finding(
                    "error",
                    block.path,
                    block.start_line,
                    "Durable repo docs must use linked SVG embeds generated from adjacent `.mmd` sources; "
                    "run `materialize_diagrams.py <path>` instead of committing Markdown Mermaid blocks.",
                )
            )
    artifacts, artifact_errors, artifact_warnings = validate_materialized_diagram_artifacts(paths)
    errors.extend(artifact_errors)
    warnings.extend(artifact_warnings)

    render_result: RenderResult | None = None
    if render or require_render:
        auto_install = (mmdc == "mmdc") if auto_install_mmdc is None else auto_install_mmdc
        render_result = render_blocks(
            blocks,
            command=mmdc,
            required=require_render,
            auto_install_mmdc=auto_install,
        )
        errors.extend(render_result.failures)
        warnings.extend(render_result.warnings)

    parser_warnings = [item for item in warnings if item.path != "<render>" and "wrapper" not in item.message.lower()]
    wrapper_warnings = [item for item in warnings if item.path != "<render>" and "wrapper" in item.message.lower()]
    render_warnings = render_result.warnings if render_result else []
    return {
        "valid": not errors,
        "files_scanned": [str(path) for path in files],
        "block_count": len(blocks),
        "artifact_count": len(artifacts),
        "artifacts": [asdict(item) for item in artifacts],
        "blocks": [asdict(block) for block in blocks],
        "errors": [asdict(item) for item in errors],
        "warnings": [asdict(item) for item in warnings],
        "warning_groups": {
            "parser": [asdict(item) for item in parser_warnings],
            "azure_wrapper": [asdict(item) for item in wrapper_warnings],
            "render": [asdict(item) for item in render_warnings],
        },
        "render": asdict(render_result) if render_result else None,
    }
