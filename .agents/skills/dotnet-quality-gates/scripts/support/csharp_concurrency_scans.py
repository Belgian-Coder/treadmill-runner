"""C# concurrency-related static scan helpers for local quality gates."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

EXTERNAL_LOCK_TARGET_PATTERN = re.compile(
    r"\block\s*\(\s*(?:this|typeof\s*\([^)]*\)|@?\"(?:\"\"|\\.|[^\"\\])*?\")\s*\)",
    re.IGNORECASE,
)
CONCURRENT_DICTIONARY_DECLARATION_PATTERN = re.compile(
    r"\b(?:[\w.]+\.)?ConcurrentDictionary\s*<[^;\n=]+>\s+(?P<typed>@?\w+)\b"
    r"|\bvar\s+(?P<var>@?\w+)\s*=\s*new\s+(?:[\w.]+\.)?ConcurrentDictionary\s*<",
    re.IGNORECASE,
)
CONTAINS_KEY_CALL_PATTERN = re.compile(r"\b(?P<var>@?\w+)\s*\.\s*ContainsKey\s*\(", re.IGNORECASE)
UNBOUNDED_CHANNEL_PATTERN = re.compile(
    r"\b(?:System\.Threading\.Channels\.)?Channel\s*\.\s*CreateUnbounded\s*<",
    re.IGNORECASE,
)
TEST_PATH_MARKER_PATTERN = re.compile(
    r"(?:^|[._-])(?:tests?|unittests|integrationtests|functionaltests|e2etests|acceptancetests)(?:$|[._-])",
    re.IGNORECASE,
)


def strip_csharp_comments_preserve_offsets(text: str) -> str:
    def replace_comment(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group(0))

    return re.sub(r"//[^\r\n]*|/\*[\s\S]*?\*/", replace_comment, text)


def line_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def line_snippet(text: str, line_number: int) -> str:
    lines = text.splitlines()
    return lines[line_number - 1].strip()[:180] if 1 <= line_number <= len(lines) else ""


def concurrent_dictionary_names(code: str) -> set[str]:
    names: set[str] = set()
    for match in CONCURRENT_DICTIONARY_DECLARATION_PATTERN.finditer(code):
        name = match.group("typed") or match.group("var")
        if name:
            names.add(name)
    return names


def concurrent_dictionary_check_then_set_findings(path: Path, text: str, code: str) -> list[dict[str, Any]]:
    names = concurrent_dictionary_names(code)
    if not names:
        return []
    findings: list[dict[str, Any]] = []
    lines = code.splitlines()
    for index, line in enumerate(lines):
        for match in CONTAINS_KEY_CALL_PATTERN.finditer(line):
            name = match.group("var")
            if name not in names:
                continue
            assignment = re.compile(rf"\b{re.escape(name)}\s*\[[^\]]+\]\s*=", re.IGNORECASE)
            window = lines[index : index + 9]
            if not any(assignment.search(candidate) for candidate in window):
                continue
            line_number = index + 1
            findings.append(
                {
                    "rule_id": "SW059",
                    "severity": "warning",
                    "path": str(path),
                    "line": line_number,
                    "message": "ConcurrentDictionary ContainsKey followed by indexer assignment is a check-then-act race; use GetOrAdd, AddOrUpdate, or TryAdd",
                    "snippet": line_snippet(text, line_number),
                }
            )
    return findings


def is_test_path(path: Path) -> bool:
    normalized = path.as_posix().lower()
    return (
        "test" in path.stem.lower()
        or any(TEST_PATH_MARKER_PATTERN.search(part) for part in path.parts[:-1])
        or "/test/" in normalized
        or "/tests/" in normalized
    )


def unbounded_channel_findings(path: Path, text: str, code: str) -> list[dict[str, Any]]:
    if is_test_path(path):
        return []
    findings: list[dict[str, Any]] = []
    for match in UNBOUNDED_CHANNEL_PATTERN.finditer(code):
        line_number = line_for_offset(text, match.start())
        findings.append(
            {
                "rule_id": "SW060",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "unbounded Channel<T> has no back-pressure; prefer Channel.CreateBounded with explicit capacity for production work queues",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def csharp_concurrency_findings(path: Path, text: str) -> list[dict[str, Any]]:
    if path.suffix.lower() != ".cs":
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for match in EXTERNAL_LOCK_TARGET_PATTERN.finditer(code):
        line_number = line_for_offset(text, match.start())
        findings.append(
            {
                "rule_id": "SW058",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "lock target is externally visible or interned; use a private dedicated lock object",
                "snippet": line_snippet(text, line_number),
            }
        )
    findings.extend(concurrent_dictionary_check_then_set_findings(path, text, code))
    findings.extend(unbounded_channel_findings(path, text, code))
    return findings
