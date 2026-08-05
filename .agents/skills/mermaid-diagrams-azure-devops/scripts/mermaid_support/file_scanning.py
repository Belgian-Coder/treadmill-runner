#!/usr/bin/env python3
"""File discovery and Markdown Mermaid block extraction."""

from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path
from urllib.parse import unquote

from mermaid_support.models import DiagramBlock, MARKDOWN_SUFFIXES, MERMAID_IMAGE_SUFFIXES, MERMAID_SOURCE_SUFFIXES, SCAN_SUFFIXES
from mermaid_support.syntax_rules import (
    AZURE_CLOSE_RE,
    AZURE_COMPACT_OPEN_RE,
    AZURE_OPEN_RE,
    FENCED_CLOSE_RE,
    FENCED_OPEN_RE,
    GENERIC_FENCE_RE,
)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def markdown_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            if path.suffix.lower() in MARKDOWN_SUFFIXES:
                files.append(path)
            continue
        if path.is_dir():
            for candidate in sorted(path.rglob("*"), key=lambda item: item.as_posix().lower()):
                if candidate.is_file() and candidate.suffix.lower() in MARKDOWN_SUFFIXES:
                    files.append(candidate)
    return files


def diagram_input_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            if path.suffix.lower() in SCAN_SUFFIXES:
                files.append(path)
            continue
        if path.is_dir():
            for candidate in sorted(path.rglob("*"), key=lambda item: item.as_posix().lower()):
                if candidate.is_file() and candidate.suffix.lower() in SCAN_SUFFIXES:
                    files.append(candidate)
    return files


def mermaid_asset_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    suffixes = MERMAID_SOURCE_SUFFIXES | MERMAID_IMAGE_SUFFIXES
    for path in paths:
        if path.is_file():
            continue
        if path.is_dir():
            for candidate in sorted(path.rglob("*"), key=lambda item: item.as_posix().lower()):
                if candidate.is_file() and candidate.suffix.lower() in suffixes:
                    files.append(candidate)
    return files


def strip_markdown_link_target(value: str) -> str:
    target = value.strip()
    if "://" in target or target.startswith(("#", "mailto:")):
        return ""
    target = target.split("#", 1)[0].split("?", 1)[0]
    return unquote(target).strip()


def resolve_markdown_target(markdown_path: Path, value: str) -> Path | None:
    target = strip_markdown_link_target(value)
    if not target:
        return None
    path = Path(target)
    if path.is_absolute():
        return path.resolve()
    return (markdown_path.parent / path).resolve()


def code_fence_mask(lines: list[str]) -> list[bool]:
    mask: list[bool] = []
    in_fence = False
    for line in lines:
        mask.append(in_fence)
        if GENERIC_FENCE_RE.match(line):
            in_fence = not in_fence
    return mask


def normalize_body(lines: list[str]) -> str:
    body = textwrap.dedent("\n".join(lines)).strip("\n")
    return body + "\n" if body else ""


def extract_blocks_from_text(path: Path, text: str) -> list[DiagramBlock]:
    lines = text.splitlines()
    blocks: list[DiagramBlock] = []
    index = 0
    in_non_mermaid_fence = False
    while index < len(lines):
        line = lines[index]
        if in_non_mermaid_fence:
            if FENCED_CLOSE_RE.match(line):
                in_non_mermaid_fence = False
            index += 1
            continue
        if GENERIC_FENCE_RE.match(line) and not FENCED_OPEN_RE.match(line):
            in_non_mermaid_fence = True
            index += 1
            continue
        wrapper = ""
        close_re: re.Pattern[str] | None = None
        if AZURE_OPEN_RE.match(line) or AZURE_COMPACT_OPEN_RE.match(line):
            wrapper = "azure"
            close_re = AZURE_CLOSE_RE
        elif FENCED_OPEN_RE.match(line):
            wrapper = "fenced"
            close_re = FENCED_CLOSE_RE
        else:
            index += 1
            continue

        start = index
        body_lines: list[str] = []
        index += 1
        while index < len(lines):
            if close_re and close_re.match(lines[index]):
                break
            body_lines.append(lines[index])
            index += 1

        end = index if index < len(lines) else len(lines) - 1
        blocks.append(
            DiagramBlock(
                path=str(path),
                start_line=start + 1,
                end_line=end + 1,
                wrapper=wrapper,
                opening=line,
                body=normalize_body(body_lines),
                raw_body="\n".join(body_lines) + ("\n" if body_lines else ""),
            )
        )
        index += 1
    return blocks


def path_allows_markdown_mermaid_blocks(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return "/assets/mermaid-templates/" in normalized


def extract_blocks(paths: list[Path]) -> tuple[list[DiagramBlock], list[Path]]:
    files = diagram_input_files(paths)
    blocks: list[DiagramBlock] = []
    for path in files:
        text = read_text(path)
        if path.suffix.lower() in MERMAID_SOURCE_SUFFIXES:
            body = text.strip("\n")
            blocks.append(
                DiagramBlock(
                    path=str(path),
                    start_line=1,
                    end_line=max(1, len(text.splitlines())),
                    wrapper="source",
                    opening="",
                    body=body + ("\n" if body else ""),
                    raw_body=text,
                )
            )
        else:
            blocks.extend(extract_blocks_from_text(path, text))
    return blocks, files


def changed_markdown_files(root: Path) -> list[Path]:
    completed = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMRT", "HEAD"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.stderr.strip() or "git diff failed")
    return [
        root / line.strip()
        for line in completed.stdout.splitlines()
        if line.strip() and Path(line.strip()).suffix.lower() in SCAN_SUFFIXES
    ]
