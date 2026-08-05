"""C# static scan helpers for validate_local_quality."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


SUPPRESS_MESSAGE_ATTRIBUTE_PATTERN = re.compile(
    r"\[\s*(?:[\w.]+\.)?SuppressMessage(?:Attribute)?\s*\((?P<args>[\s\S]*?)\)\s*\]",
    re.IGNORECASE,
)
SUPPRESS_MESSAGE_JUSTIFICATION_PATTERN = re.compile(r"\bJustification\s*=", re.IGNORECASE)
BUILD_SERVICE_PROVIDER_PATTERN = re.compile(r"\.\s*BuildServiceProvider\s*\(", re.IGNORECASE)


def line_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def line_snippet(text: str, line_number: int) -> str:
    lines = text.splitlines()
    return lines[line_number - 1].strip()[:180] if 1 <= line_number <= len(lines) else ""


def strip_csharp_comments_preserve_offsets(text: str) -> str:
    def replace_comment(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group(0))

    return re.sub(r"//[^\r\n]*|/\*[\s\S]*?\*/", replace_comment, text)


def suppressmessage_justification_findings(path: Path, text: str) -> list[dict[str, Any]]:
    if path.suffix.lower() != ".cs":
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for match in SUPPRESS_MESSAGE_ATTRIBUTE_PATTERN.finditer(code):
        if SUPPRESS_MESSAGE_JUSTIFICATION_PATTERN.search(match.group("args")):
            continue
        line_number = line_for_offset(text, match.start())
        findings.append(
            {
                "rule_id": "SW054",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "SuppressMessage attribute is missing a Justification value",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def build_service_provider_findings(path: Path, text: str, test_file: bool) -> list[dict[str, Any]]:
    if test_file or path.suffix.lower() != ".cs":
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for match in BUILD_SERVICE_PROVIDER_PATTERN.finditer(code):
        line_number = line_for_offset(text, match.start())
        findings.append(
            {
                "rule_id": "SW055",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": (
                    "BuildServiceProvider called in production-shaped source; "
                    "avoid constructing a second root service provider outside tests"
                ),
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def csharp_static_scan_findings(path: Path, text: str, test_file: bool) -> list[dict[str, Any]]:
    findings = suppressmessage_justification_findings(path, text)
    findings.extend(build_service_provider_findings(path, text, test_file))
    return findings
