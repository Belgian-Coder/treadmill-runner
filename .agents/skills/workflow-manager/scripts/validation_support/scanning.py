"""Text and risk signal scanning for workflow validation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import workflow_manager_common as common
from automation_validation_rules import NEGATION_TERMS, SIGNAL_RULES, TEXT_EXTENSIONS


def is_negated(line: str, match_start: int) -> bool:
    prefix = line[max(0, match_start - 48) : match_start].lower()
    suffix = line[match_start : match_start + 80].lower()
    return (
        any(term in prefix for term in NEGATION_TERMS)
        or re.search(r":\s*(?:none|no|false)\b", suffix) is not None
    )


def detect_external_signals(module_dir: Path) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for path in common.iter_files(module_dir, max_files=1000):
        relative_parts = path.relative_to(module_dir).parts
        if relative_parts and relative_parts[0] == "runs":
            continue
        if len(relative_parts) >= 2 and relative_parts[0] == "artifacts" and relative_parts[1] == "maps":
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        lines = common.read_text(path, limit=80_000).splitlines()
        for line_number, line in enumerate(lines, start=1):
            for category, signal, pattern in SIGNAL_RULES:
                match = pattern.search(line)
                if not match or is_negated(line, match.start()):
                    continue
                signals.append(
                    {
                        "category": category,
                        "path": common.relative(module_dir, path),
                        "line": line_number,
                        "signal": signal,
                        "snippet": common.compact_snippet(line),
                    }
                )
    return signals
