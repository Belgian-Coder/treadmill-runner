#!/usr/bin/env python3
"""Detect Playwright project shape and produce deterministic evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import playwright_project_support as support


def coverage_gap_summary(root: Path, specs: list[Path], routes: list[dict[str, Any]]) -> dict[str, Any]:
    spec_text = "\n".join(path.read_text(encoding="utf-8", errors="replace").lower() for path in specs[:200])
    covered: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for route in routes:
        route_path = route["path"].lower()
        stem = Path(route_path).stem.lower()
        if stem in {"index", "page", "route"}:
            parent = Path(route_path).parent.name.lower()
            needle = parent if parent else stem
        else:
            needle = stem
        target = covered if needle and needle in spec_text else missing
        target.append({"path": route["path"], "match_key": needle})
    return {
        "route_candidates": len(routes),
        "test_files": len(specs),
        "covered_route_candidates": covered[:100],
        "missing_route_candidates": missing[:100],
        "note": "Heuristic only: confirms test text references route names, not behavioral coverage.",
    }


def build_report(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    package_path = root / "package.json"
    package_json, package_error = support.read_package_json(package_path) if package_path.exists() else (None, "package.json not found")
    configs = support.find_configs(root)
    specs = support.list_spec_files(root)
    routes = support.route_candidates(root)
    framework = support.detect_framework(root, package_json)
    language = support.detect_language(root, package_json)
    scripts = support.script_items(package_json or {})
    playwright_scripts = {name: value for name, value in scripts.items() if "playwright" in value.lower()}
    signals = support.playwright_signals(package_json, root)
    dotnet_projects = support.dotnet_playwright_projects(root)
    python_manifests = support.python_playwright_manifests(root)

    deps = support.dependency_names(package_json or {})
    cypress = any((root / name).exists() for name in ("cypress", "cypress.config.ts", "cypress.config.js")) or any(
        "cypress" in dep.lower() for dep in deps
    )
    selenium = any("selenium" in dep.lower() or "webdriver" in dep.lower() for dep in deps)
    migration = {
        "cypress_detected": cypress,
        "selenium_detected": selenium,
        "recommendation": "Produce a read-only migration assessment before changing tests." if cypress or selenium else "No Cypress/Selenium migration signal detected.",
    }

    ok = bool(signals)
    return {
        "schema_version": 1,
        "tool": "playwright-integration.detect",
        "ok": ok,
        "status": "passed" if ok else "skipped" if package_json else "blocked",
        "project_root": str(root),
        "package_json": {"path": str(package_path), "ok": package_json is not None, "error": package_error},
        "framework": framework,
        "language": language,
        "configs": [str(path.relative_to(root)) for path in configs],
        "signals": signals,
        "dotnet_projects": dotnet_projects,
        "python_manifests": python_manifests,
        "scripts": playwright_scripts,
        "reporters": support.detect_reporters(root),
        "test_files": [str(path.relative_to(root)) for path in specs],
        "ci_files": [str(path.relative_to(root)) for path in (root / ".github" / "workflows").glob("*.yml")] if (root / ".github" / "workflows").exists() else [],
        "gitignore": support.gitignore_report(root),
        "config_suggestions": support.config_suggestions(framework, language),
        "coverage_gaps": coverage_gap_summary(root, specs, routes),
        "migration": migration,
        "fallback": "Use this deterministic report directly when local AI is disabled or unavailable.",
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Playwright Project Detection",
        "",
        f"- Status: {report['status']}",
        f"- Project root: `{report['project_root']}`",
        f"- Framework: {report['framework']}",
        f"- Language: {report['language']}",
        f"- Configs: {', '.join(report['configs']) if report['configs'] else 'none'}",
        f"- Test files: {len(report['test_files'])}",
        f"- Signals: {', '.join(report['signals']) if report['signals'] else 'none'}",
        "",
        "## Recommendations",
        "",
    ]
    for item in report["config_suggestions"]:
        lines.append(f"- {item}")
    if report["gitignore"]["missing"]:
        lines.append(f"- Add ignored Playwright artifacts: {', '.join(report['gitignore']['missing'])}")
    lines.extend(
        [
            "",
            "## Coverage Gap Heuristic",
            "",
            f"- Route candidates: {report['coverage_gaps']['route_candidates']}",
            f"- Missing route candidates: {len(report['coverage_gaps']['missing_route_candidates'])}",
            f"- Note: {report['coverage_gaps']['note']}",
            "",
            "## Migration",
            "",
            f"- Cypress detected: {report['migration']['cypress_detected']}",
            f"- Selenium detected: {report['migration']['selenium_detected']}",
            f"- Recommendation: {report['migration']['recommendation']}",
        ]
    )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--report-json", help="write JSON report to this path; omit for stdout-only read-only reporting")
    parser.add_argument("--report-md", help="write Markdown report to this path; omit for stdout-only read-only reporting")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(Path(args.project_root))
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
