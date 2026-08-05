#!/usr/bin/env python3
"""Classify failing or flaky Playwright evidence without modifying tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


CATEGORY_RULES = {
    "timing-async": (
        "timeout",
        "waiting",
        "not visible",
        "detached",
        "waitfortimeout",
        "element is not attached",
        "strict mode violation",
    ),
    "test-isolation": (
        "passes alone",
        "fails in suite",
        "shared state",
        "already exists",
        "duplicate",
        "parallel",
        "workers",
    ),
    "environment": (
        "ci",
        "locally",
        "viewport",
        "timezone",
        "font",
        "webkit",
        "firefox",
        "chromium",
    ),
    "infrastructure": (
        "browser has been closed",
        "crash",
        "oom",
        "out of memory",
        "dns",
        "network error",
        "econnreset",
    ),
}


COMMANDS = {
    "timing-async": [
        "npx playwright test <file> --repeat-each=20 --reporter=list",
        "npx playwright test <file> --trace=on --retries=0",
    ],
    "test-isolation": [
        "npx playwright test <file> --workers=1 --reporter=list",
        "npx playwright test <file> --fully-parallel --workers=4 --repeat-each=5",
    ],
    "environment": [
        "npx playwright test <file> --project=chromium --reporter=list",
        "Compare local and CI screenshots, traces, viewport, timezone, fonts, and env vars.",
    ],
    "infrastructure": [
        "npx playwright test <file> --workers=1 --reporter=list",
        "Check browser install, system dependencies, memory pressure, and runner logs.",
    ],
}


def classify(text: str) -> tuple[str, list[str]]:
    lowered = text.lower()
    matches: dict[str, int] = {}
    evidence: list[str] = []
    for category, terms in CATEGORY_RULES.items():
        count = sum(1 for term in terms if term in lowered)
        if count:
            matches[category] = count
            evidence.extend(term for term in terms if term in lowered)
    if not matches:
        return "unknown", []
    category = sorted(matches.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return category, sorted(set(evidence))


def build_report(text: str, source: str) -> dict[str, Any]:
    category, evidence = classify(text)
    return {
        "schema_version": 1,
        "tool": "playwright-integration.flaky-diagnosis",
        "ok": category != "unknown",
        "status": "passed" if category != "unknown" else "warning",
        "source": source,
        "category": category,
        "evidence_terms": evidence,
        "recommended_commands": COMMANDS.get(category, ["Read complete output, isolate first failing fact, then rerun the smallest reproducible test."]),
        "fix_guidance": {
            "timing-async": "Replace arbitrary waits with web-first assertions, await actions, and wait for specific responses/events.",
            "test-isolation": "Remove shared mutable state, create per-test data, and isolate storage/cookies.",
            "environment": "Align CI/local viewport, timezone, browser project, fonts, and service availability.",
            "infrastructure": "Reduce workers, check browser dependencies, memory, DNS, and runner stability.",
            "unknown": "Collect a trace, repeat locally, and classify again from concrete failure output.",
        }[category],
        "fallback": "Use this deterministic classification directly when local AI is disabled or unavailable.",
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="read existing failure output file; stdin is used when omitted")
    parser.add_argument("--report-json", help="write JSON report to this path; omit for stdout-only read-only reporting")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.input:
        path = Path(args.input)
        text = path.read_text(encoding="utf-8", errors="replace")
        source = str(path)
    else:
        import sys

        text = sys.stdin.read()
        source = "stdin"
    report = build_report(text, source)
    if args.report_json:
        write_json(Path(args.report_json), report)
    print(f"Playwright flaky diagnosis: {report['category']}")
    print(f"Evidence terms: {', '.join(report['evidence_terms']) if report['evidence_terms'] else 'none'}")
    for command in report["recommended_commands"]:
        print(f"- {command}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
