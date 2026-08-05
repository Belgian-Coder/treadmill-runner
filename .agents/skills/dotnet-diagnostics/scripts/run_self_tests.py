#!/usr/bin/env python3
"""Self-tests for dotnet-diagnostics guidance contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True


def main() -> int:
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
    if "dotnet-engineering" not in manifest.get("related_modules", []):
        errors.append("dotnet-engineering handoff must stay declared")
    for phrase in ("Read-Only Dogfood", "collect counters/logs/dumps/traces", "diagnostic configuration"):
        if phrase not in text:
            errors.append(f"missing diagnostics boundary phrase: {phrase}")
    runtime = (skill_dir / "docs" / "runtime-triage.md").read_text(encoding="utf-8")
    if "New low-impact counters or logs only after approval" not in runtime:
        errors.append("runtime triage must require approval for counters/logs")
    dump_trace = (skill_dir / "docs" / "dump-and-trace.md").read_text(encoding="utf-8")
    if "configuration changes are diagnostic mutations" not in dump_trace:
        errors.append("dump-and-trace must label createdump configuration as mutation")
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print("dotnet-diagnostics self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
