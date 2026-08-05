#!/usr/bin/env python3
"""Self-tests for dotnet-legacy guidance contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True


USAGE = """usage: run_self_tests.py [--help]

Runs dotnet-legacy self-tests. Normal execution reads skill files only and leaves
the repository unchanged.
"""


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    if args and args[0] in {"-h", "--help"}:
        print(USAGE, end="")
        return 0
    if args:
        print(f"unknown argument: {args[0]}", file=sys.stderr)
        print(USAGE, end="", file=sys.stderr)
        return 2

    skill_dir = Path(__file__).resolve().parents[1]
    manifest = json.loads((skill_dir / "module.json").read_text(encoding="utf-8-sig"))
    errors: list[str] = []
    if manifest.get("id") != skill_dir.name:
        errors.append("module id must match folder name")
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    if f"name: {skill_dir.name}" not in text:
        errors.append("SKILL.md frontmatter name must match folder name")
    for relative in manifest.get("inputs", []):
        if str(relative).endswith(".md") and not (skill_dir / str(relative)).exists():
            errors.append(f"missing declared input: {relative}")
    if ".NET Framework" not in text:
        errors.append(".NET Framework ownership must stay explicit")
    if "maintain-in-place" not in text:
        errors.append("maintain-in-place branch must stay explicit")
    for phrase in (
        "Read-Only Dogfood",
        "Run `scripts/run_self_tests.py` only after inspecting its help/source",
        "These commands are write-capable",
        "The module risk profile is `local-write` for real legacy maintenance and validation",
        "Safe command shape",
        "Unsafe command shape",
        "For strict read-only work, skip build/test/restore",
        "SDK-style projects that still target .NET Framework",
        "Approved modernization means explicit user, ticket, or accepted workflow-plan scope",
    ):
        if phrase not in text:
            errors.append(f"missing read-only guard phrase: {phrase}")
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print("dotnet-legacy self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
