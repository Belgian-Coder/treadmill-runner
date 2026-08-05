"""GitHub validation trigger inspection for local release gates."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from repo_support import repo_common as repo


def github_validation_trigger_state(root: Path) -> dict[str, Any]:
    workflow_path = root / ".github" / "workflows" / "validate-skills.yml"
    if not workflow_path.exists():
        return {
            "schema_version": 1,
            "status": "local-only",
            "path": repo.relative(root, workflow_path),
            "triggers": [],
            "manual_dispatch_enabled": False,
            "automatic_triggers": [],
            "automatic_triggers_enabled": False,
            "note": "GitHub validation workflow is intentionally absent; local deterministic gates are authoritative.",
        }
    text = workflow_path.read_text(encoding="utf-8", errors="replace")
    triggers: list[str] = []
    inline = re.search(r"^on:\s*\[(?P<events>[^\]]+)\]\s*$", text, flags=re.MULTILINE)
    if inline:
        triggers.extend(item.strip().strip("'\"") for item in inline.group("events").split(",") if item.strip())
    else:
        in_on_block = False
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if re.match(r"^on:\s*$", line):
                in_on_block = True
                continue
            if not in_on_block:
                continue
            if line and not line.startswith((" ", "\t")):
                break
            match = re.match(r"^\s+(?P<event>[A-Za-z0-9_-]+)\s*:", line)
            if match:
                event = match.group("event")
                if event not in triggers:
                    triggers.append(event)
    automatic = [event for event in triggers if event in {"push", "pull_request", "pull_request_target", "schedule"}]
    manual = "workflow_dispatch" in triggers
    if automatic:
        status = "automatic"
        note = "GitHub validation can run automatically."
    elif manual:
        status = "manual-only"
        note = "Automatic GitHub validation is paused; run workflow_dispatch manually when credits are available."
    else:
        status = "disabled"
        note = "GitHub validation has no recognized manual or automatic trigger."
    return {
        "schema_version": 1,
        "status": status,
        "path": repo.relative(root, workflow_path),
        "triggers": triggers,
        "manual_dispatch_enabled": manual,
        "automatic_triggers": automatic,
        "automatic_triggers_enabled": bool(automatic),
        "note": note,
    }


def github_validation_advisories(state: dict[str, Any]) -> list[str]:
    status = str(state.get("status") or "")
    if status == "manual-only":
        return [
            "GitHub validation is manual-only; local deterministic gates are authoritative until GitHub Actions credits are available."
        ]
    if status == "local-only":
        return [
            "GitHub validation is intentionally local-only; local deterministic gates are authoritative and GitHub Actions credits are not used."
        ]
    if status in {"missing", "disabled"}:
        return ["GitHub validation is not automatically available; local deterministic gates are authoritative for this release gate."]
    return []
