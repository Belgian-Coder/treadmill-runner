#!/usr/bin/env python3
"""Validation for materialized Mermaid source/SVG assets."""

from __future__ import annotations

import re
from pathlib import Path

from mermaid_support.file_scanning import (
    code_fence_mask,
    markdown_files,
    mermaid_asset_files,
    read_text,
    resolve_markdown_target,
)
from mermaid_support.models import Finding, MaterializedDiagramArtifact, MERMAID_IMAGE_SUFFIXES, MERMAID_SOURCE_SUFFIXES

MERMAID_SOURCE_LINK_RE = re.compile(r"Source:\s*\[Mermaid\]\((?P<source>[^)]+)\)", re.IGNORECASE)
SVG_IMAGE_LINK_RE = re.compile(r"!\[[^\]]*\]\((?P<image>[^)]+\.svg(?:[?#][^)]+)?)\)", re.IGNORECASE)
SVG_OPEN_RE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)
SVG_VIEWBOX_RE = re.compile(r'\bviewBox="[^"]+"')
SVG_MEASURE_RE = re.compile(r"^\d+(?:\.\d+)?(?:px)?$")
SVG_TRANSPARENT_BACKGROUND_RE = re.compile(r"background-color:\s*transparent", re.IGNORECASE)
SVG_WHITE_BACKGROUND_RE = re.compile(
    r"<rect\b[^>]*(?:fill=\"#(?:fff|ffffff)\"|fill:\s*#(?:fff|ffffff))",
    re.IGNORECASE,
)


def materialized_diagram_artifacts(paths: list[Path]) -> list[MaterializedDiagramArtifact]:
    artifacts: list[MaterializedDiagramArtifact] = []
    for path in markdown_files(paths):
        lines = read_text(path).splitlines()
        fenced = code_fence_mask(lines)
        for index, line in enumerate(lines):
            if fenced[index]:
                continue
            source_match = MERMAID_SOURCE_LINK_RE.search(line)
            if not source_match:
                continue
            source_path = resolve_markdown_target(path, source_match.group("source"))
            if source_path is None:
                continue
            image_path: Path | None = None
            image_line = index + 1
            for previous_index in range(max(0, index - 3), index + 1):
                if fenced[previous_index]:
                    continue
                image_match = SVG_IMAGE_LINK_RE.search(lines[previous_index])
                if image_match:
                    image_path = resolve_markdown_target(path, image_match.group("image"))
                    image_line = previous_index + 1
                    break
            if image_path is None:
                continue
            artifacts.append(
                MaterializedDiagramArtifact(
                    markdown_path=str(path),
                    line=image_line,
                    image_path=str(image_path),
                    source_path=str(source_path),
                )
            )
    return artifacts


def svg_attr(opening: str, name: str) -> str:
    match = re.search(rf'\b{re.escape(name)}="([^"]*)"', opening)
    return match.group(1).strip() if match else ""


def validate_materialized_svg(artifact: MaterializedDiagramArtifact) -> tuple[list[Finding], list[Finding]]:
    errors: list[Finding] = []
    warnings: list[Finding] = []
    image = Path(artifact.image_path)
    source = Path(artifact.source_path)
    if not source.exists():
        errors.append(
            Finding(
                "error",
                artifact.markdown_path,
                artifact.line,
                f"Mermaid source link is missing: {source}",
            )
        )
    if not image.exists():
        errors.append(
            Finding(
                "error",
                artifact.markdown_path,
                artifact.line,
                f"Mermaid SVG link is missing: {image}",
            )
        )
        return errors, warnings
    if source.exists() and source.parent != image.parent:
        warnings.append(
            Finding(
                "warning",
                artifact.markdown_path,
                artifact.line,
                "Mermaid source and SVG should live in the same diagrams folder.",
            )
        )
    text = read_text(image)
    opening_match = SVG_OPEN_RE.search(text)
    if not opening_match:
        errors.append(Finding("error", artifact.markdown_path, artifact.line, "Linked Mermaid SVG has no `<svg>` root."))
        return errors, warnings
    opening = opening_match.group(0)
    width = svg_attr(opening, "width")
    height = svg_attr(opening, "height")
    if not width or not SVG_MEASURE_RE.match(width) or width.endswith("%"):
        errors.append(
            Finding(
                "error",
                artifact.markdown_path,
                artifact.line,
                "Linked Mermaid SVG must use an intrinsic numeric width so small diagrams do not fill the page.",
            )
        )
    if not height or not SVG_MEASURE_RE.match(height) or height.endswith("%"):
        errors.append(
            Finding(
                "error",
                artifact.markdown_path,
                artifact.line,
                "Linked Mermaid SVG must use an intrinsic numeric height.",
            )
        )
    if not SVG_VIEWBOX_RE.search(opening):
        errors.append(Finding("error", artifact.markdown_path, artifact.line, "Linked Mermaid SVG must include a viewBox."))
    if "data-mermaid-vertical-padding=" not in opening:
        errors.append(
            Finding(
                "error",
                artifact.markdown_path,
                artifact.line,
                "Linked Mermaid SVG must be normalized with vertical padding metadata.",
            )
        )
    if not SVG_TRANSPARENT_BACKGROUND_RE.search(opening):
        errors.append(
            Finding(
                "error",
                artifact.markdown_path,
                artifact.line,
                "Linked Mermaid SVG must use a transparent background from dark-theme rendering.",
            )
        )
    if SVG_WHITE_BACKGROUND_RE.search(text[:2000]):
        errors.append(
            Finding(
                "error",
                artifact.markdown_path,
                artifact.line,
                "Linked Mermaid SVG contains a white background; rerender with dark transparent Mermaid settings.",
            )
        )
    return errors, warnings


def path_allows_unlinked_mermaid_asset(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return "/assets/mermaid-templates/" in normalized


def is_mermaid_svg_asset(path: Path) -> bool:
    if path.parent.name.lower() == "diagrams":
        return True
    try:
        text = read_text(path)[:4000]
    except OSError:
        return False
    return "data-mermaid-vertical-padding=" in text or "mermaid" in text.lower()


def validate_unlinked_materialized_assets(
    paths: list[Path], artifacts: list[MaterializedDiagramArtifact]
) -> tuple[list[Finding], list[Finding]]:
    linked_sources = {Path(item.source_path).resolve() for item in artifacts}
    linked_images = {Path(item.image_path).resolve() for item in artifacts}
    assets = mermaid_asset_files(paths)
    sources = {path.resolve() for path in assets if path.suffix.lower() in MERMAID_SOURCE_SUFFIXES}
    images = {path.resolve() for path in assets if path.suffix.lower() in MERMAID_IMAGE_SUFFIXES and is_mermaid_svg_asset(path)}
    errors: list[Finding] = []
    warnings: list[Finding] = []

    for source in sorted(sources - linked_sources, key=lambda item: item.as_posix().lower()):
        if path_allows_unlinked_mermaid_asset(str(source)):
            continue
        image = source.with_suffix(".svg")
        errors.append(
            Finding(
                "error",
                str(source),
                1,
                "Mermaid source file is not linked from Markdown with an adjacent SVG embed and `Source: [Mermaid](...)` line.",
            )
        )
        if image.exists() and image in images and image not in linked_images:
            artifact_errors, artifact_warnings = validate_materialized_svg(
                MaterializedDiagramArtifact(
                    markdown_path=str(source),
                    line=1,
                    image_path=str(image),
                    source_path=str(source),
                )
            )
            errors.extend(artifact_errors)
            warnings.extend(artifact_warnings)
        elif not image.exists():
            errors.append(Finding("error", str(source), 1, f"Mermaid source has no sibling SVG render: {image}"))

    for image in sorted(images - linked_images, key=lambda item: item.as_posix().lower()):
        if path_allows_unlinked_mermaid_asset(str(image)):
            continue
        source = image.with_suffix(".mmd")
        if source in sources - linked_sources:
            continue
        errors.append(
            Finding(
                "error",
                str(image),
                1,
                "Mermaid SVG file is not linked from Markdown with an adjacent `Source: [Mermaid](...)` line.",
            )
        )
        artifact_errors, artifact_warnings = validate_materialized_svg(
            MaterializedDiagramArtifact(
                markdown_path=str(image),
                line=1,
                image_path=str(image),
                source_path=str(source),
            )
        )
        errors.extend(artifact_errors)
        warnings.extend(artifact_warnings)
    return errors, warnings


def validate_materialized_diagram_artifacts(paths: list[Path]) -> tuple[list[MaterializedDiagramArtifact], list[Finding], list[Finding]]:
    artifacts = materialized_diagram_artifacts(paths)
    errors: list[Finding] = []
    warnings: list[Finding] = []
    for artifact in artifacts:
        artifact_errors, artifact_warnings = validate_materialized_svg(artifact)
        errors.extend(artifact_errors)
        warnings.extend(artifact_warnings)
    unlinked_errors, unlinked_warnings = validate_unlinked_materialized_assets(paths, artifacts)
    errors.extend(unlinked_errors)
    warnings.extend(unlinked_warnings)
    return artifacts, errors, warnings
