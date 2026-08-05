#!/usr/bin/env python3
"""Mechanical Markdown Mermaid compatibility fixes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mermaid_support.file_scanning import markdown_files, read_text
from mermaid_support.syntax_rules import AZURE_CLOSE_RE, AZURE_COMPACT_OPEN_RE, AZURE_OPEN_RE


def autofix_text(text: str) -> tuple[str, list[str]]:
    fixes: list[str] = []
    updated = text
    if "```mermaid" in updated:
        updated = re.sub(r"(?im)^(\s*)```\s*mermaid\s*$", r"\1::: mermaid", updated)
        updated = re.sub(r"(?m)^(\s*)```\s*$", r"\1:::", updated)
        fixes.append("converted fenced Mermaid blocks to Azure DevOps ::: mermaid blocks")
    if re.search(r"(?im)^\s*:::mermaid\s*$", updated):
        updated = re.sub(r"(?im)^(\s*):::mermaid\s*$", r"\1::: mermaid", updated)
        fixes.append("normalized compact :::mermaid wrapper")
    if re.search(r"(?im)^\s*flowchart\s+", updated):
        updated = re.sub(r"(?im)^(\s*)flowchart\s+", r"\1graph ", updated)
        fixes.append("rewrote flowchart keyword to graph")
    lines = updated.splitlines()
    fixed_lines: list[str] = []
    in_azure = False
    for line in lines:
        if AZURE_OPEN_RE.match(line) or AZURE_COMPACT_OPEN_RE.match(line):
            in_azure = True
            fixed_lines.append("::: mermaid")
            continue
        if in_azure and AZURE_CLOSE_RE.match(line):
            in_azure = False
            fixed_lines.append(":::")
            continue
        if in_azure and line.strip() and not line.startswith(("    ", "\t")):
            fixed_lines.append("    " + line)
            fixes.append("indented Azure DevOps Mermaid body lines")
            continue
        fixed_lines.append(line)
    return "\n".join(fixed_lines) + ("\n" if text.endswith("\n") else ""), sorted(set(fixes))


def apply_autofix(paths: list[Path]) -> dict[str, Any]:
    files = markdown_files(paths)
    changed: list[str] = []
    fixes: dict[str, list[str]] = {}
    for path in files:
        original = read_text(path)
        updated, file_fixes = autofix_text(original)
        if updated != original:
            path.write_text(updated, encoding="utf-8", newline="\n")
            changed.append(str(path))
            fixes[str(path)] = file_fixes
    return {"changed": changed, "fixes": fixes}
