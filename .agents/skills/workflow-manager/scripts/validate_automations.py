#!/usr/bin/env python3
"""Validate repository automation workflow modules."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import workflow_manager_common as common
from validation_support.module_checks import validate_automations
from validation_support.reporting import render_json_report, render_markdown_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="repository root; defaults to script parent")
    parser.add_argument("--name", dest="workflow_name", help="validate one workflow name")
    parser.add_argument(
        "--strict-phase-quality",
        action="store_true",
        help="promote phase-quality warnings to errors",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
    )
    parser.add_argument("--summary", action="store_true", help="emit aggregate counts and failures only")
    parser.add_argument("--compact", action="store_true", help="with --summary, omit passing module rows")
    return parser


def default_root() -> Path:
    return Path(__file__).resolve().parents[4]


def main() -> int:
    common.require_supported_python()
    args = build_parser().parse_args()
    root = Path(args.root).expanduser().resolve() if args.root else default_root()
    errors, warnings, modules = validate_automations(
        root,
        workflow_name=args.workflow_name,
        strict_phase_quality=args.strict_phase_quality,
    )
    if args.output_format == "json":
        print(render_json_report(root, errors, warnings, modules, summary=args.summary, compact=args.compact), end="")
    else:
        print(render_markdown_report(root, errors, warnings, modules, summary=args.summary, compact=args.compact), end="")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
