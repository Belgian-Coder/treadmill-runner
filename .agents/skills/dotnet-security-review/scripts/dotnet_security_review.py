#!/usr/bin/env python3
"""Dispatcher for dotnet-security-review scanner evidence."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True


HELP_TEXT = """usage: dotnet_security_review.py scan <scanner-args>

Commands:
  scan    Run the deterministic .NET-adjacent security pattern scanner.

Common read-only examples:
  python -B .agents/skills/dotnet-security-review/scripts/dotnet_security_review.py scan --target . --changed-only
  python -B .agents/skills/dotnet-security-review/scripts/dotnet_security_review.py scan --target <files-or-dirs> --fail-on high

Read-only boundary:
  Scanner runs without --output-json, --output-md, or --output-sarif print a compact summary only.
  --changed-only uses local Git diff discovery for tracked changes under the requested target.
  --input-sarif reads local SARIF only and does not upload or mutate data.
  Output flags are write-capable, may create parent directories, and produce caller-owned review evidence files.
  Scanner mode does not mutate source files, upload data, install tools, or configure credentials.

Use `dotnet_security_review.py scan --help` for scanner flags."""


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(HELP_TEXT)
        return 0
    command = argv[0].lower()
    if command != "scan":
        print(f"Unknown dotnet-security-review command: {argv[0]}", file=sys.stderr)
        return 2
    script = Path(__file__).resolve().parent / "scanner" / "scan_security_patterns.py"
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.call([sys.executable, "-B", str(script), *argv[1:]], env=env)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
