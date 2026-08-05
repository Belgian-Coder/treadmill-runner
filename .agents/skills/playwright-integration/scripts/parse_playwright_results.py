#!/usr/bin/env python3
"""Parse Playwright JSON results into workflow evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def iter_tests(node: Any) -> list[dict[str, Any]]:
    tests: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if isinstance(node.get("tests"), list):
            tests.extend(item for item in node["tests"] if isinstance(item, dict))
        for key in ("suites", "specs"):
            value = node.get(key)
            if isinstance(value, list):
                for child in value:
                    tests.extend(iter_tests(child))
    elif isinstance(node, list):
        for child in node:
            tests.extend(iter_tests(child))
    return tests


def test_status(test: dict[str, Any]) -> tuple[str, float, str]:
    expected = str(test.get("expectedStatus") or "passed")
    results = test.get("results") if isinstance(test.get("results"), list) else []
    if not results:
        return str(test.get("status") or "skipped"), 0.0, ""
    duration = sum(float(result.get("duration") or 0) for result in results if isinstance(result, dict))
    last = next((result for result in reversed(results) if isinstance(result, dict)), {})
    status = str(last.get("status") or test.get("status") or expected)
    errors = []
    for result in results:
        if not isinstance(result, dict):
            continue
        for error in result.get("errors") or []:
            if isinstance(error, dict):
                errors.append(str(error.get("message") or error.get("value") or ""))
    if len(results) > 1 and status == expected:
        status = "flaky"
    return status, duration, "\n".join(item for item in errors if item)[:2000]


def build_report(results_path: Path) -> dict[str, Any]:
    data = json.loads(results_path.read_text(encoding="utf-8"))
    tests = iter_tests(data)
    rows: list[dict[str, Any]] = []
    counts = {"passed": 0, "failed": 0, "skipped": 0, "flaky": 0, "timedOut": 0, "interrupted": 0}
    for test in tests:
        status, duration, error = test_status(test)
        counts[status] = counts.get(status, 0) + 1
        rows.append(
            {
                "title": " ".join(str(part) for part in test.get("titlePath", []) if part) or str(test.get("title", "")),
                "status": status,
                "duration_ms": round(duration, 2),
                "error": error,
            }
        )
    failed = [item for item in rows if item["status"] not in {"passed", "skipped", "flaky"}]
    return {
        "schema_version": 1,
        "tool": "playwright-integration.results",
        "ok": not failed,
        "status": "failed" if failed else "warning" if counts.get("flaky", 0) else "passed",
        "source": str(results_path),
        "summary": {
            "total": len(rows),
            "passed": counts.get("passed", 0),
            "failed": len(failed),
            "skipped": counts.get("skipped", 0),
            "flaky": counts.get("flaky", 0),
            "duration_ms": round(sum(item["duration_ms"] for item in rows), 2),
        },
        "tests": rows,
        "fallback": "Use summary and tests directly when local AI is disabled or unavailable.",
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Playwright Results",
        "",
        f"- Status: {report['status']}",
        f"- Total: {summary['total']}",
        f"- Passed: {summary['passed']}",
        f"- Failed: {summary['failed']}",
        f"- Skipped: {summary['skipped']}",
        f"- Flaky: {summary['flaky']}",
        f"- Duration ms: {summary['duration_ms']}",
    ]
    failures = [item for item in report["tests"] if item["status"] not in {"passed", "skipped", "flaky"}]
    if failures:
        lines.extend(["", "## Failed Tests", ""])
        for item in failures[:50]:
            lines.append(f"- {item['title']}: {item['error'][:200]}")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-json", required=True, help="read existing Playwright JSON results")
    parser.add_argument("--report-json", help="write JSON report to this path; omit for stdout-only read-only reporting")
    parser.add_argument("--report-md", help="write Markdown report to this path; omit for stdout-only read-only reporting")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(Path(args.results_json))
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
