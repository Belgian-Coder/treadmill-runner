#!/usr/bin/env python3
"""Lint Playwright tests for deterministic quality evidence."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import playwright_project_support as support

ACTION_RE = re.compile(r"\b(?:page|locator)\.(?:goto|click|fill|check|uncheck|selectOption|press|hover|reload|setInputFiles|waitFor[A-Z]\w*)\(")
EXPECT_ASYNC_RE = re.compile(r"\bexpect\(.+\)\.to(?:Be|Have|Contain|Equal|Match)\w*\(")
TEST_START_RE = re.compile(r"\btest(?:\.only)?\(\s*['\"]([^'\"]+)['\"]")


def finding(rule: str, severity: str, path: Path, root: Path, line: int, message: str, fix: str) -> dict[str, Any]:
    return {
        "rule": rule,
        "severity": severity,
        "path": str(path.relative_to(root)),
        "line": line,
        "message": message,
        "fix": fix,
    }


def lint_file(path: Path, root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    top_level_lets: list[int] = []
    in_test = False
    test_start = 0
    test_name = ""
    brace_depth = 0

    for index, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if "waitForTimeout" in line:
            findings.append(finding("wait-for-timeout", "critical", path, root, index, "Avoid arbitrary waits.", "Use a web-first assertion or specific event/response wait."))
        if "expect(await " in line:
            findings.append(finding("non-web-first-assertion", "critical", path, root, index, "Assertion checks a resolved value once.", "Use expect(locator) so Playwright can auto-retry."))
        if re.search(r"https?://(?:localhost|127\.0\.0\.1)", line):
            findings.append(finding("hardcoded-local-url", "warning", path, root, index, "Hardcoded local URL found.", "Use baseURL and page.goto('/path') where possible."))
        if re.search(r"\bpage\.\$\$?\(", line):
            findings.append(finding("page-dollar-selector", "critical", path, root, index, "page.$/page.$$ bypass locator semantics.", "Use locator or role/label/text based locators."))
        if re.search(r"page\.(?:locator|click|fill|check)\(\s*['\"](?:\.|#|//|xpath=|css=|\[)", line):
            findings.append(finding("css-or-xpath-first", "warning", path, root, index, "CSS/XPath selector used before semantic locators.", "Prefer getByRole, getByLabel, getByText, getByPlaceholder, then getByTestId."))
        if re.match(r"let\s+\w+", line) and not in_test:
            top_level_lets.append(index)
        if re.search(r"test\.describe\.serial|test\.serial", line):
            findings.append(finding("serial-tests", "warning", path, root, index, "Serial tests may hide order dependencies.", "Prefer independent tests with isolated setup."))
        if re.search(r"test\.only", line):
            findings.append(finding("focused-test", "critical", path, root, index, "Focused test would skip other tests.", "Remove test.only before committing."))

        test_match = TEST_START_RE.search(line)
        if test_match:
            in_test = True
            test_start = index
            test_name = test_match.group(1)
            brace_depth = raw.count("{") - raw.count("}")
            lowered = test_name.lower()
            if lowered in {"test", "test 1", "should work", "works", "login test"} or re.search(r"\bstep\s*\d+", lowered):
                findings.append(finding("generic-or-step-test-name", "warning", path, root, index, f"Low-signal test name: {test_name}", "Use a behavior name such as 'shows validation error for invalid email'."))
        elif in_test:
            brace_depth += raw.count("{") - raw.count("}")
            if brace_depth <= 0:
                if index - test_start > 50:
                    findings.append(finding("long-test", "info", path, root, test_start, f"Test spans {index - test_start + 1} lines.", "Split long tests or extract page helpers when behavior is unclear."))
                in_test = False

        if ACTION_RE.search(line) or EXPECT_ASYNC_RE.search(line):
            starts_ok = line.startswith(("await ", "return ", "void ", "const ", "let ", "var ", "if ", "for ", "while "))
            if not starts_ok and "=>" not in line:
                findings.append(finding("missing-await", "critical", path, root, index, "Possible missing await on Playwright action/assertion.", "Await Playwright actions and web-first assertions."))

    if len(top_level_lets) >= 2:
        findings.append(finding("shared-mutable-state", "warning", path, root, top_level_lets[0], "Multiple top-level let declarations may share state between tests.", "Keep test data per test or in isolated fixtures."))
    return findings


def candidate_files(root: Path, paths: list[str], changed_files: bool) -> list[Path]:
    if paths:
        result: list[Path] = []
        for item in paths:
            path = (root / item).resolve()
            if path.is_dir():
                result.extend(support.list_spec_files(path))
            elif path.is_file() and path.name.endswith(support.SPEC_SUFFIXES):
                result.append(path)
        return sorted(set(result), key=lambda item: item.as_posix())
    if changed_files:
        return [path for path in support.git_changed_files(root) if path.exists() and path.name.endswith(support.SPEC_SUFFIXES)]
    return support.list_spec_files(root)


def build_report(project_root: Path, paths: list[str] | None = None, changed_files: bool = False) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    files = candidate_files(root, paths or [], changed_files)
    all_findings: list[dict[str, Any]] = []
    for path in files:
        if path.exists() and path.is_file():
            all_findings.extend(lint_file(path, root))
    critical = [item for item in all_findings if item["severity"] == "critical"]
    warnings = [item for item in all_findings if item["severity"] == "warning"]
    return {
        "schema_version": 1,
        "tool": "playwright-integration.lint",
        "ok": not critical,
        "status": "failed" if critical else "warning" if warnings else "passed",
        "project_root": str(root),
        "changed_files_mode": changed_files,
        "files_checked": [str(path.relative_to(root)) for path in files],
        "summary": {
            "files_checked": len(files),
            "findings": len(all_findings),
            "critical": len(critical),
            "warning": len(warnings),
            "info": len([item for item in all_findings if item["severity"] == "info"]),
        },
        "findings": all_findings,
        "fallback": "Use findings directly when local AI is disabled or unavailable.",
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Playwright Test Lint",
        "",
        f"- Status: {report['status']}",
        f"- Files checked: {report['summary']['files_checked']}",
        f"- Critical: {report['summary']['critical']}",
        f"- Warning: {report['summary']['warning']}",
        f"- Info: {report['summary']['info']}",
        "",
    ]
    if report["findings"]:
        lines.append("## Findings")
        lines.append("")
        for item in report["findings"]:
            lines.append(f"- {item['severity'].upper()} `{item['path']}:{item['line']}` {item['rule']}: {item['message']} Fix: {item['fix']}")
    else:
        lines.append("No Playwright lint findings.")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--path", action="append", default=[], help="specific file or directory to lint")
    parser.add_argument("--changed-files", action="store_true", help="read-only: lint changed Playwright spec files only")
    parser.add_argument("--report-json", help="write JSON report to this path; omit for stdout-only read-only reporting")
    parser.add_argument("--report-md", help="write Markdown report to this path; omit for stdout-only read-only reporting")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(Path(args.project_root), args.path, args.changed_files)
    if args.report_json:
        write_json(Path(args.report_json), report)
    markdown = render_markdown(report)
    if args.report_md:
        path = Path(args.report_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8", newline="\n")
    print(markdown, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
