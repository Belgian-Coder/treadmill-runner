"""C# LINQ-related static scan helpers for local quality gates."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

AS_ENUMERABLE_PATTERN = re.compile(r"\.\s*AsEnumerable\s*\(\s*\)", re.IGNORECASE)
LINQ_CLIENT_OPERATOR_AFTER_PATTERN = re.compile(r"\.\s*(?:Where|Select)\s*\(", re.IGNORECASE)
LINQ_SERVER_OPERATOR_BEFORE_PATTERN = re.compile(r"\.\s*(?:Where|Select|Take|Skip)\s*\(", re.IGNORECASE)


def strip_csharp_comments_preserve_offsets(text: str) -> str:
    def replace_comment(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group(0))

    return re.sub(r"//[^\r\n]*|/\*[\s\S]*?\*/", replace_comment, text)


def line_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def line_snippet(text: str, line_number: int) -> str:
    lines = text.splitlines()
    return lines[line_number - 1].strip()[:180] if 1 <= line_number <= len(lines) else ""


def statement_segment(text: str, offset: int, max_chars: int = 700) -> str:
    end = text.find(";", offset)
    if end == -1:
        end = min(len(text), offset + max_chars)
    else:
        end = min(end + 1, offset + max_chars)
    return text[offset:end]


def csharp_linq_findings(path: Path, text: str, test_file: bool) -> list[dict[str, Any]]:
    if test_file or path.suffix.lower() != ".cs":
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for match in AS_ENUMERABLE_PATTERN.finditer(code):
        statement_start = code.rfind(";", 0, match.start()) + 1
        before = code[statement_start : match.start()]
        after = statement_segment(code, match.end())
        if LINQ_SERVER_OPERATOR_BEFORE_PATTERN.search(before) or not LINQ_CLIENT_OPERATOR_AFTER_PATTERN.search(after):
            continue
        line_number = line_for_offset(text, match.start())
        findings.append(
            {
                "rule_id": "SW061",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "AsEnumerable before filtering or projection can force client-side LINQ evaluation; filter/project before materializing",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings
