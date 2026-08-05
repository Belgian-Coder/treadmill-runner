#!/usr/bin/env python3
"""Self-tests for dotnet-delivery guidance contracts."""

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
    if manifest.get("risk", {}).get("uploads") is not True or manifest.get("risk", {}).get("profile") != "networked":
        errors.append("upload-capable delivery guidance must keep the networked risk profile")
    if "dotnet-quality-gates" not in manifest.get("related_modules", []):
        errors.append("dotnet-quality-gates handoff must stay declared")
    for phrase in ("Read-Only Dogfood", "cloud CLIs/APIs", "write-capable"):
        if phrase not in text:
            errors.append(f"missing delivery boundary phrase: {phrase}")
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print("dotnet-delivery self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
