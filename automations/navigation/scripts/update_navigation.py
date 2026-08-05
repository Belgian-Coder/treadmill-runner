#!/usr/bin/env python3
"""Update or check a target project's generated navigation maps."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import navigation_core


def require_supported_python() -> None:
    navigation_core.require_supported_python()


def update_navigation(
    target: Path,
    *,
    write: bool = False,
    check: bool = False,
    max_files: int = 5000,
) -> dict[str, Any]:
    target = target.expanduser().resolve()
    outputs, scan = navigation_core.build_outputs(target, max_files=max_files)
    stale = navigation_core.stale_outputs(target, outputs)
    source_changes = navigation_core.stale_source_changes(target, scan.get("hashes", {}))
    missing_outputs = [relative for relative in outputs if not (target / relative).exists()]
    installation_status = "installed"
    if missing_outputs:
        installation_status = "not-installed" if len(missing_outputs) == len(outputs) else "partial"
    written: list[str] = []
    status = "ok"
    if write:
        written = navigation_core.write_outputs(target, outputs)
        stale = []
        status = "written"
        installation_status = "installed"
    elif check and stale:
        status = "not-installed" if installation_status == "not-installed" else "stale"
    return {
        "schema_version": navigation_core.SCHEMA_VERSION,
        "tool": navigation_core.TOOL_NAME,
        "ok": bool(scan.get("ok")) and not stale,
        "status": status,
        "installation_status": installation_status,
        "target": str(target),
        "files_scanned": scan.get("files_scanned", 0),
        "written": written,
        "stale": stale,
        "stale_source_changes": source_changes,
        "map_size_budget": scan.get("map_size_budget", {}),
        "route_quality_warnings": scan.get("route_quality_warnings", []),
        "checks": ["navigation outputs built deterministically", *scan.get("checks", [])],
        "skipped": scan.get("skipped", []),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="read target project root")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write generated navigation maps")
    mode.add_argument("--check", action="store_true", help="read-only check: fail when maps are missing or stale")
    parser.add_argument("--max-files", type=int, default=5000)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    return parser


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Navigation Update",
        "",
        f"- Target: `{report['target']}`",
        f"- Status: {report['status']}",
        f"- Installation: {report.get('installation_status', 'unknown')}",
        f"- Files scanned: {report['files_scanned']}",
    ]
    if report["written"]:
        lines.extend(["", "## Written", ""])
        lines.extend(f"- `{item}`" for item in report["written"])
    if report["stale"]:
        lines.extend(["", "## Stale", ""])
        lines.extend(f"- `{item}`" for item in report["stale"])
    changes = report.get("stale_source_changes", {})
    if isinstance(changes, dict):
        lines.extend(["", "## Source Changes Since Last Refresh", ""])
        for key in ("added", "modified", "deleted"):
            values = changes.get(key, [])
            if values:
                lines.append(f"- {key}: {', '.join(f'`{item}`' for item in values[:10])}")
            else:
                lines.append(f"- {key}: none")
    budget = report.get("map_size_budget", {})
    if isinstance(budget, dict):
        lines.extend(["", "## Map Size Budget", ""])
        lines.append(f"- Status: {budget.get('status', 'unknown')}")
        lines.append(f"- NAVIGATION.md words: {budget.get('navigation_words', 0)}/{budget.get('navigation_budget', 0)}")
        lines.append(f"- scan entries: {budget.get('scan_entries', 0)}/{budget.get('scan_entry_budget', 0)}")
    quality = report.get("route_quality_warnings", [])
    if quality:
        lines.extend(["", "## Route Quality", ""])
        lines.extend(f"- {item}" for item in quality)
    lines.extend(["", "## Skipped", ""])
    skipped = report.get("skipped", [])
    lines.extend(f"- {item}" for item in skipped) if skipped else lines.append("- None.")
    return "\n".join(lines)


def main() -> int:
    navigation_core.require_supported_python()
    args = build_parser().parse_args()
    report = update_navigation(
        Path(args.target),
        write=args.write,
        check=args.check,
        max_files=args.max_files,
    )
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
