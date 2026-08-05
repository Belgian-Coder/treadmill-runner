#!/usr/bin/env python3
"""Safely add or update deterministic routing metadata on a workflow module."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import workflow_manager_common as common
import routing_contract


def default_root() -> Path:
    return Path(__file__).resolve().parents[4]


def next_patch_version(value: object) -> str:
    parsed = common.semver_tuple(str(value or ""))
    if parsed is None:
        return ""
    major, minor, patch = parsed
    return f"{major}.{minor}.{patch + 1}"


def routing_update_report(
    root: Path,
    *,
    name: str,
    terms: list[str],
    activation_terms: list[str],
    threshold: int,
    winner_margin: int,
    write: bool,
) -> dict[str, Any]:
    root = root.expanduser().resolve(strict=False)
    module_path = root / "automations" / name / "module.json"
    normalized, issues = routing_contract.normalized_terms(terms, label="routing term")
    normalized_activation, activation_issues = routing_contract.normalized_terms(
        activation_terms,
        label="activation term",
    )
    issues.extend(activation_issues)
    if normalized_activation and not routing_contract.has_non_generic_activation(normalized_activation):
        issues.append("activation terms must include a non-generic routing concept")
    if not common.SKILL_NAME_PATTERN.fullmatch(name):
        issues.append("workflow name must use lowercase letters, digits, and hyphens")
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 2:
        issues.append("threshold must be an integer of at least 2")
    if not isinstance(winner_margin, int) or isinstance(winner_margin, bool) or winner_margin < 1:
        issues.append("winner margin must be an integer of at least 1")
    issues.extend(
        routing_contract.routing_reachability_issues(
            normalized,
            threshold=threshold,
            winner_margin=winner_margin,
        )
    )
    manifest, read_issue = common.read_json_file(module_path)
    if read_issue:
        issues.append(read_issue)
    manifest = manifest or {}
    if manifest and (manifest.get("kind") != "workflow" or manifest.get("id") != name):
        issues.append("module.json must be a workflow contract whose id matches the folder name")
    next_version = next_patch_version(manifest.get("version")) if manifest else ""
    if manifest and not next_version:
        issues.append("module.json version must be valid SemVer")
    routing = {
        "terms": normalized,
        "activation_terms": normalized_activation,
        "threshold": threshold,
        "winner_margin": winner_margin,
    }
    changed = bool(manifest) and manifest.get("routing") != routing
    status = "blocked" if issues else "planned" if changed and not write else "written" if changed else "unchanged"
    report: dict[str, Any] = {
        "schema_version": 1,
        "tool": "workflow-manager.update-workflow-routing",
        "ok": not issues,
        "status": status,
        "root": str(root),
        "workflow": name,
        "module": module_path.relative_to(root).as_posix() if module_path.is_relative_to(root) else str(module_path),
        "routing": routing,
        "changed": changed,
        "write": write,
        "issues": issues,
    }
    if issues or not changed or not write:
        return report
    manifest["routing"] = routing
    manifest["version"] = next_version
    module_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    report["version"] = next_version
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Workflow Routing Metadata Update",
        "",
        f"- Status: {report.get('status')}",
        f"- Workflow: `{report.get('workflow')}`",
        f"- Module: `{report.get('module')}`",
        f"- Changed: {str(bool(report.get('changed'))).lower()}",
    ]
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in issues)
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--name", required=True)
    parser.add_argument("--term", action="append", default=[], dest="terms")
    parser.add_argument(
        "--activation-term",
        action="append",
        default=[],
        dest="activation_terms",
        help="specific subject anchor; at least one must match before the workflow can route",
    )
    parser.add_argument("--threshold", type=int, required=True)
    parser.add_argument("--winner-margin", type=int, required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    return parser


def main(argv: list[str] | None = None) -> int:
    common.require_supported_python()
    args = build_parser().parse_args(argv)
    report = routing_update_report(
        args.root,
        name=args.name,
        terms=args.terms,
        activation_terms=args.activation_terms,
        threshold=args.threshold,
        winner_margin=args.winner_margin,
        write=args.write,
    )
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
