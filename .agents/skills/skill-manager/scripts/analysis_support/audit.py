#!/usr/bin/env python3
"""Static audit helpers for skill-manager location analysis."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from analysis_support import analysis_common as common

PROMPT_INJECTION_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "critical",
        "instruction override wording",
        re.compile(
            r"\b(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above)\s+instructions\b",
            re.IGNORECASE,
        ),
    ),
    (
        "critical",
        "role hijack wording",
        re.compile(r"\byou\s+are\s+now\s+(root|admin|developer|system)\b", re.IGNORECASE),
    ),
    (
        "high",
        "system prompt extraction wording",
        re.compile(r"\b(reveal|print|show|dump)\s+(the\s+)?system\s+prompt\b", re.IGNORECASE),
    ),
    (
        "high",
        "hidden instruction marker",
        re.compile(r"<!--\s*(ignore|system|developer|instruction)", re.IGNORECASE),
    ),
)

SCRIPT_TEXT_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "critical",
        "shell command execution",
        re.compile(r"\b(os\.system|os\.popen|subprocess\.\w+\([^)]*shell\s*=\s*True)", re.IGNORECASE),
    ),
    (
        "critical",
        "dynamic code execution",
        re.compile(r"\b(eval|exec|compile)\s*\(", re.IGNORECASE),
    ),
    (
        "high",
        "credential file access",
        re.compile(r"(\.ssh|\.aws|\.azure|id_rsa|known_hosts)", re.IGNORECASE),
    ),
)

ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\ufeff]")
PINNED_REQUIREMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+==[^=<>!~]+$")


def audit_candidate(root: Path, files: list[Path], max_text_files: int) -> dict[str, object]:
    base = root if root.is_dir() else root.parent
    findings: list[dict[str, object]] = []

    checked_text = 0
    for path in files:
        if path.suffix.lower() in common.DISALLOWED_SCRIPT_SUFFIXES:
            findings.append(
                finding("high", "disallowed script", base, path, "Convert to Python 3.12+.")
            )

        if checked_text < max_text_files and (
            path.suffix.lower() in common.TEXT_SUFFIXES or path.name in common.MANIFEST_NAMES
        ):
            checked_text += 1
            scan_text_file(base, path, findings)

        if path.suffix.lower() == ".py":
            scan_python_ast(base, path, findings)

    verdict = audit_verdict(findings)
    return {
        "verdict": verdict,
        "summary": audit_summary(verdict, findings),
        "findings": sorted(
            findings,
            key=lambda item: (
                severity_rank(str(item["severity"])),
                str(item["path"]),
                int(item.get("line") or 0),
                str(item["rule"]),
            ),
        ),
    }


def finding(
    severity: str,
    rule: str,
    base: Path,
    path: Path,
    detail: str,
    line: int | None = None,
) -> dict[str, object]:
    return {
        "severity": severity,
        "rule": rule,
        "path": common.relative(base, path),
        "line": line,
        "detail": detail,
    }


def scan_text_file(base: Path, path: Path, findings: list[dict[str, object]]) -> None:
    try:
        text = common.read_text(path, limit=100_000)
    except OSError:
        findings.append(finding("high", "unreadable file", base, path, "Could not read file."))
        return

    if ZERO_WIDTH_RE.search(text):
        findings.append(
            finding(
                "high",
                "zero-width characters",
                base,
                path,
                "Review hidden characters before promotion.",
            )
        )

    for line_number, line in enumerate(text.splitlines(), start=1):
        if skip_static_pattern_line(line):
            continue
        if path.suffix.lower() in {".md", ".txt"}:
            for severity, rule, pattern in PROMPT_INJECTION_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        finding(
                            severity,
                            rule,
                            base,
                            path,
                            compact(line),
                            line_number,
                        )
                    )
        if path.suffix.lower() in {".py", ".js", ".ts", ".sh", ".ps1", ".cmd", ".bat"}:
            if path.suffix.lower() == ".py":
                continue
            for severity, rule, pattern in SCRIPT_TEXT_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        finding(severity, rule, base, path, compact(line), line_number)
                    )

    if path.name.lower().startswith("requirements") and path.suffix.lower() == ".txt":
        scan_requirements(base, path, text, findings)


def scan_requirements(
    base: Path, path: Path, text: str, findings: list[dict[str, object]]
) -> None:
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        if not PINNED_REQUIREMENT_RE.match(stripped):
            findings.append(
                finding(
                    "info",
                    "unpinned Python requirement",
                    base,
                    path,
                    stripped,
                    line_number,
                )
            )


def skip_static_pattern_line(line: str) -> bool:
    stripped = line.strip()
    if "re.compile(" in stripped:
        return True
    if any(fragment in stripped for fragment in ("\\b", "\\s", "\\.", "\\(")):
        return True
    if stripped.startswith(("if name in {", "if path.suffix", "SCRIPT_TEXT_PATTERNS")):
        return True
    return False


def scan_python_ast(base: Path, path: Path, findings: list[dict[str, object]]) -> None:
    try:
        tree = ast.parse(common.read_text(path, limit=200_000))
    except SyntaxError as exc:
        findings.append(
            finding(
                "high",
                "Python syntax error",
                base,
                path,
                str(exc),
                exc.lineno,
            )
        )
        return
    except OSError:
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = call_name(node.func)
            if name in {"eval", "exec", "compile", "__import__"}:
                findings.append(
                    finding(
                        "critical",
                        "dynamic Python execution",
                        base,
                        path,
                        name,
                        getattr(node, "lineno", None),
                    )
                )
            if name in {"os.system", "os.popen"}:
                findings.append(
                    finding(
                        "critical",
                        "shell command execution",
                        base,
                        path,
                        name,
                        getattr(node, "lineno", None),
                    )
                )
            if name.startswith("subprocess.") and has_shell_true(node):
                findings.append(
                    finding(
                        "critical",
                        "subprocess shell=True",
                        base,
                        path,
                        name,
                        getattr(node, "lineno", None),
                    )
                )


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def has_shell_true(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant):
            return keyword.value.value is True
    return False


def audit_verdict(findings: list[dict[str, object]]) -> str:
    severities = {str(item["severity"]) for item in findings}
    if "critical" in severities:
        return "fail"
    if "high" in severities:
        return "warn"
    return "pass"


def audit_summary(verdict: str, findings: list[dict[str, object]]) -> str:
    counts: dict[str, int] = {}
    for item in findings:
        severity = str(item["severity"])
        counts[severity] = counts.get(severity, 0) + 1
    if not findings:
        return "No static audit findings in scanned files."
    rendered = ", ".join(f"{name}: {counts[name]}" for name in sorted(counts, key=severity_rank))
    return f"{verdict.upper()} with {rendered}."


def severity_rank(value: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "info": 3}.get(value, 9)


def compact(value: str, limit: int = 160) -> str:
    text = " ".join(value.strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
