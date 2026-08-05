#!/usr/bin/env python3
"""Analyze a local folder or file as a skill candidate.

The analyzer is intentionally offline-only. It reads local files, reports likely
purpose, dependencies, risks, and disallowed scripts, and suggests a Python
conversion plan where needed. It does not scrape, upload, call APIs, or install
packages.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from analysis_support.core import analyze_target, build_parser, main, render_report, render_summary_markdown, summarize_report
from analysis_support.report_rendering import render_report_from_analysis

__all__ = [
    "analyze_target",
    "build_parser",
    "main",
    "render_report",
    "render_report_from_analysis",
    "render_summary_markdown",
    "summarize_report",
]


if __name__ == "__main__":
    raise SystemExit(main())
