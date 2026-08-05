#!/usr/bin/env python3
"""Materialize Markdown Mermaid blocks as adjacent .mmd sources and SVG embeds."""

from __future__ import annotations

import argparse
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.dont_write_bytecode = True

from mermaid_support.validation_impl import (
    DiagramBlock,
    Finding,
    MMDC_RENDER_FLAGS,
    RenderResult,
    detect_diagram_type,
    extract_blocks_from_text,
    install_mmdc,
    materialized_diagram_artifacts,
    markdown_files,
    read_text,
    validate_body,
)


HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.+?)\s*#*\s*$")
SLUG_RE = re.compile(r"[^a-z0-9]+")
SVG_OPEN_RE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)
SVG_VIEWBOX_RE = re.compile(
    r'\bviewBox="(?P<x>-?\d+(?:\.\d+)?) (?P<y>-?\d+(?:\.\d+)?) '
    r'(?P<width>\d+(?:\.\d+)?) (?P<height>\d+(?:\.\d+)?)"'
)
SVG_VERTICAL_PADDING = 24.0
SVG_PADDING_ATTR = "data-mermaid-vertical-padding"


@dataclass
class MaterializedDiagram:
    markdown_path: Path
    source_path: Path
    image_path: Path
    title: str
    diagram_type: str


def slugify(value: str, fallback: str) -> str:
    value = value.lower()
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = SLUG_RE.sub("-", value).strip("-")
    return value or fallback


def nearest_heading(lines: list[str], before_index: int, fallback: str) -> str:
    for index in range(before_index - 1, -1, -1):
        match = HEADING_RE.match(lines[index])
        if match:
            return match.group("title").strip()
    return fallback


def unique_asset_name(stem: str, base: str, used: set[str]) -> str:
    candidate = f"{stem}-{base}"
    if candidate not in used:
        used.add(candidate)
        return candidate
    suffix = 2
    while f"{candidate}-{suffix}" in used:
        suffix += 1
    unique = f"{candidate}-{suffix}"
    used.add(unique)
    return unique


def markdown_link(path: Path, target: Path) -> str:
    return target.relative_to(path.parent).as_posix()


def asset_dir_for(path: Path) -> Path:
    return path.parent / "diagrams"


def ensure_no_target_collisions(diagrams: list[MaterializedDiagram]) -> None:
    collisions: list[Path] = []
    seen_targets: set[Path] = set()
    for diagram in diagrams:
        for target in (diagram.source_path, diagram.image_path):
            if target in seen_targets or target.exists():
                collisions.append(target)
            seen_targets.add(target)
    if collisions:
        rendered = "\n".join(f"- {path}" for path in collisions)
        raise RuntimeError(
            "Refusing to materialize Mermaid blocks because generated target files already exist. "
            "Run --dry-run and choose distinct headings or refresh linked diagrams explicitly.\n"
            f"{rendered}"
        )


def ensure_mmdc(command: str, *, auto_install: bool) -> str:
    executable = shutil.which(command)
    if executable:
        return executable
    if command == "mmdc" and auto_install:
        result = RenderResult(attempted=True, required=True, command=command, auto_install_requested=True)
        install_mmdc(result)
        executable = shutil.which(command)
        if executable:
            return executable
        messages = [item.message for item in result.failures or result.warnings]
        detail = "; ".join(messages) if messages else "Mermaid CLI command `mmdc` was not found."
        raise RuntimeError(detail)
    raise RuntimeError(f"Mermaid CLI command `{command}` was not found.")


def format_svg_number(value: float) -> str:
    if math.isclose(value, round(value), abs_tol=0.001):
        return str(int(round(value)))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def set_svg_attr(opening: str, name: str, value: str) -> str:
    attr_re = re.compile(rf'\s{re.escape(name)}="[^"]*"')
    replacement = f' {name}="{value}"'
    if attr_re.search(opening):
        return attr_re.sub(replacement, opening, count=1)
    return opening[:-1] + replacement + ">"


def normalize_svg_canvas(image: Path, *, vertical_padding: float = SVG_VERTICAL_PADDING) -> None:
    text = image.read_text(encoding="utf-8")
    svg_match = SVG_OPEN_RE.search(text)
    if not svg_match:
        return
    opening = svg_match.group(0)
    if f"{SVG_PADDING_ATTR}=" in opening:
        return
    viewbox_match = SVG_VIEWBOX_RE.search(opening)
    if not viewbox_match:
        return

    x = float(viewbox_match.group("x"))
    y = float(viewbox_match.group("y"))
    width = float(viewbox_match.group("width"))
    height = float(viewbox_match.group("height"))
    padded_height = height + (vertical_padding * 2)
    padded_viewbox = " ".join(
        [
            format_svg_number(x),
            format_svg_number(y - vertical_padding),
            format_svg_number(width),
            format_svg_number(padded_height),
        ]
    )

    updated = SVG_VIEWBOX_RE.sub(f'viewBox="{padded_viewbox}"', opening, count=1)
    updated = set_svg_attr(updated, "width", format_svg_number(width))
    updated = set_svg_attr(updated, "height", format_svg_number(padded_height))
    updated = set_svg_attr(updated, "preserveAspectRatio", "xMidYMid meet")
    updated = set_svg_attr(updated, SVG_PADDING_ATTR, format_svg_number(vertical_padding))
    if updated != opening:
        normalized = text[: svg_match.start()] + updated + text[svg_match.end() :]
        image.write_text(normalized, encoding="utf-8", newline="\n")


def render_svg(source: Path, image: Path, executable: str) -> None:
    image.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [executable, "-i", str(source), "-o", str(image), *MMDC_RENDER_FLAGS],
        check=False,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )
    if completed.returncode == 0 and image.exists():
        normalize_svg_canvas(image)
        return
    detail = (completed.stderr or completed.stdout or "render failed").strip()
    if len(detail) > 400:
        detail = detail[:397].rstrip() + "..."
    raise RuntimeError(f"Mermaid render failed for {source}: {detail}")


def validate_blocks(blocks: list[DiagramBlock]) -> list[Finding]:
    findings: list[Finding] = []
    for block in blocks:
        errors, _warnings = validate_body(block)
        findings.extend(errors)
    return findings


def materialize_file(
    path: Path,
    *,
    executable: str | None,
    dry_run: bool,
    check_collisions: bool = True,
) -> list[MaterializedDiagram]:
    text = read_text(path)
    blocks = extract_blocks_from_text(path, text)
    if not blocks:
        return []
    errors = validate_blocks(blocks)
    if errors:
        summary = "\n".join(f"{item.path}:{item.line}: {item.message}" for item in errors)
        raise RuntimeError(f"Cannot materialize invalid Mermaid blocks:\n{summary}")

    lines = text.splitlines()
    replacements: list[tuple[int, int, str]] = []
    diagrams: list[MaterializedDiagram] = []
    asset_dir = asset_dir_for(path)
    used_names: set[str] = set()
    for index, block in enumerate(blocks, start=1):
        title = nearest_heading(lines, block.start_line - 1, f"Diagram {index}")
        name = unique_asset_name(path.stem.lower(), slugify(title, f"diagram-{index}"), used_names)
        source = asset_dir / f"{name}.mmd"
        image = asset_dir / f"{name}.svg"
        source_link = markdown_link(path, source)
        image_link = markdown_link(path, image)
        diagram_type, _errors = detect_diagram_type(block)
        alt = title if "diagram" in title.lower() else f"{title} diagram"
        replacement = f"[![{alt}]({image_link})]({image_link})\n\nSource: [Mermaid]({source_link})"
        replacements.append((block.start_line - 1, block.end_line, replacement))
        diagrams.append(
            MaterializedDiagram(
                markdown_path=path,
                source_path=source,
                image_path=image,
                title=title,
                diagram_type=diagram_type or "unknown",
            )
        )

    if not dry_run and check_collisions:
        ensure_no_target_collisions(diagrams)
    if not dry_run:
        for diagram, block in zip(diagrams, blocks, strict=True):
            diagram.source_path.parent.mkdir(parents=True, exist_ok=True)
            diagram.source_path.write_text(block.body, encoding="utf-8", newline="\n")
            if executable is None:
                raise RuntimeError("mmdc executable is required when not running --dry-run.")
            render_svg(diagram.source_path, diagram.image_path, executable)

    if not dry_run:
        updated_lines = lines[:]
        for start, end, replacement in sorted(replacements, reverse=True):
            updated_lines[start:end] = replacement.splitlines()
        trailing_newline = "\n" if text.endswith("\n") else ""
        path.write_text("\n".join(updated_lines) + trailing_newline, encoding="utf-8", newline="\n")
    return diagrams


def refresh_existing_diagrams(paths: list[Path], *, executable: str | None, dry_run: bool) -> list[MaterializedDiagram]:
    diagrams: list[MaterializedDiagram] = []
    for artifact in materialized_diagram_artifacts(paths):
        source = Path(artifact.source_path)
        image = Path(artifact.image_path)
        if not source.exists():
            raise RuntimeError(f"Cannot refresh missing Mermaid source: {source}")
        body = read_text(source)
        block = DiagramBlock(
            path=str(source),
            start_line=1,
            end_line=max(1, len(body.splitlines())),
            wrapper="source",
            opening="",
            body=body.strip("\n") + ("\n" if body.strip("\n") else ""),
            raw_body=body,
        )
        errors, _warnings = validate_body(block)
        if errors:
            summary = "\n".join(f"{item.path}:{item.line}: {item.message}" for item in errors)
            raise RuntimeError(f"Cannot refresh invalid Mermaid source:\n{summary}")
        diagram_type, _errors = detect_diagram_type(block)
        diagrams.append(
            MaterializedDiagram(
                markdown_path=Path(artifact.markdown_path),
                source_path=source,
                image_path=image,
                title=source.stem,
                diagram_type=diagram_type or "unknown",
            )
        )
        if not dry_run:
            if executable is None:
                raise RuntimeError("mmdc executable is required when not running --dry-run.")
            render_svg(source, image, executable)
    return diagrams


def materialize_paths(
    paths: list[Path],
    *,
    mmdc: str,
    auto_install_mmdc: bool,
    dry_run: bool,
    refresh_existing: bool = False,
) -> dict[str, object]:
    files = markdown_files(paths)
    diagrams: list[MaterializedDiagram] = []
    if refresh_existing:
        executable = None if dry_run else ensure_mmdc(mmdc, auto_install=auto_install_mmdc)
        diagrams.extend(refresh_existing_diagrams(paths, executable=executable, dry_run=dry_run))
    elif dry_run:
        for path in files:
            diagrams.extend(materialize_file(path, executable=None, dry_run=True))
    else:
        planned: list[MaterializedDiagram] = []
        for path in files:
            planned.extend(materialize_file(path, executable=None, dry_run=True))
        ensure_no_target_collisions(planned)
        executable = ensure_mmdc(mmdc, auto_install=auto_install_mmdc) if planned else None
        for path in files:
            diagrams.extend(
                materialize_file(
                    path,
                    executable=executable,
                    dry_run=False,
                    check_collisions=False,
                )
            )
    by_type: dict[str, int] = {}
    for diagram in diagrams:
        by_type[diagram.diagram_type] = by_type.get(diagram.diagram_type, 0) + 1
    return {
        "files_scanned": [str(path) for path in files],
        "diagram_count": len(diagrams),
        "dry_run": dry_run,
        "refresh_existing": refresh_existing,
        "by_type": dict(sorted(by_type.items())),
        "diagrams": [
            {
                "markdown_path": str(item.markdown_path),
                "source_path": str(item.source_path),
                "image_path": str(item.image_path),
                "title": item.title,
                "type": item.diagram_type,
            }
            for item in diagrams
        ],
    }


def print_report(report: dict[str, object]) -> None:
    print("# Mermaid Materialization Report")
    print()
    print(f"- Files scanned: {len(report['files_scanned'])}")
    print(f"- Diagrams materialized: {report['diagram_count']}")
    print(f"- Dry run: {str(report['dry_run']).lower()}")
    print(f"- Refresh existing: {str(report.get('refresh_existing', False)).lower()}")
    print()
    by_type = report["by_type"]
    if isinstance(by_type, dict) and by_type:
        print("## Types")
        print()
        for key, value in by_type.items():
            print(f"- `{key}`: {value}")
        print()
    print("## Diagrams")
    print()
    diagrams = report["diagrams"]
    if isinstance(diagrams, list) and diagrams:
        for item in diagrams:
            if isinstance(item, dict):
                print(f"- `{item['markdown_path']}` -> `{item['source_path']}`, `{item['image_path']}`")
    else:
        print("- None.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Materialize Markdown Mermaid blocks as adjacent .mmd sources and SVG embeds; writes unless --dry-run is used."
    )
    parser.add_argument("paths", nargs="+", help="Markdown files or directories to materialize")
    parser.add_argument("--dry-run", action="store_true", help="read-only: report planned writes without rendering, installing, or changing files")
    parser.add_argument("--refresh-existing", action="store_true", help="write/render: rerender linked .mmd sources to their sibling SVGs")
    parser.add_argument("--mmdc", default="mmdc", help="Mermaid CLI command name; default: mmdc")
    parser.add_argument(
        "--no-auto-install-mmdc",
        action="store_true",
        help="disable automatic Mermaid CLI setup when mmdc is missing; use when installs are forbidden",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    paths = [Path(value).expanduser().resolve() for value in args.paths]
    try:
        report = materialize_paths(
            paths,
            mmdc=args.mmdc,
            auto_install_mmdc=not args.no_auto_install_mmdc,
            dry_run=args.dry_run,
            refresh_existing=args.refresh_existing,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
