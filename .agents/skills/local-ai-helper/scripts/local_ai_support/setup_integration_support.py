"""Local-AI integration metadata helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from local_ai_support.setup_catalog import (
    DAILY_TEXT_TASKS,
    INTEGRATION_SUGGESTIONS,
    LOCAL_AI_COMMAND_PREFIX,
    LOCAL_AI_METADATA_FIELDS,
    TEXT_TASK_PROFILE,
    VISION_PROFILE,
)


def integration_suggestions(target: str = "all") -> list[dict[str, str]]:
    normalized = str(target or "all").strip().lower()
    if normalized not in {"all", "skill", "workflow"}:
        raise ValueError("target must be one of: all, skill, workflow")
    return [
        dict(item)
        for item in INTEGRATION_SUGGESTIONS
        if normalized == "all" or item["target"] == normalized
    ]


def integration_task_for_use_case(use_case_id: str) -> str:
    if use_case_id in DAILY_TEXT_TASKS or use_case_id in {"skill-routing", "workflow-routing"}:
        return use_case_id
    return "inventory-summary"


def integration_profile_for_use_case(use_case_id: str) -> str:
    if use_case_id in {"vision-describe", "vision-pdf"}:
        return VISION_PROFILE
    return TEXT_TASK_PROFILE


def local_ai_metadata_use_cases(container: dict[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(container, dict):
        return []
    local_ai = container.get("local_ai")
    if isinstance(local_ai, dict):
        raw_cases = local_ai.get("use_cases")
    else:
        raw_cases = container.get("local_ai_use_cases")
    if not isinstance(raw_cases, list):
        return []

    cases: list[dict[str, str]] = []
    for raw in raw_cases:
        if not isinstance(raw, dict):
            continue
        item = {field: str(raw.get(field, "")).strip() for field in LOCAL_AI_METADATA_FIELDS}
        if not item["id"] or not item["command"].startswith(LOCAL_AI_COMMAND_PREFIX):
            continue
        if not item["guardrail"]:
            continue
        cases.append(item)
    return cases


def markdown_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def strip_inline_code(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] == "`":
        return text[1:-1].strip()
    return text


def separator_cells(cells: list[str]) -> bool:
    return bool(cells) and all(set(cell.replace(" ", "")) <= {"-", ":"} for cell in cells)


def parse_contract_local_ai_use_cases(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    section = ""
    header_index: dict[str, int] | None = None
    rows: list[dict[str, str]] = []
    headers = {
        "use case": "id",
        "command": "command",
        "applies when": "applies_when",
        "guardrail": "guardrail",
        "evidence input": "evidence_input",
        "owner": "owner",
    }
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            section = line[3:].strip().lower()
            header_index = None
            continue
        if section != "local ai use cases":
            continue
        cells = markdown_cells(line)
        if not cells or separator_cells(cells):
            continue
        lowered = [cell.lower() for cell in cells]
        if set(headers).issubset(set(lowered)):
            header_index = {header: lowered.index(header) for header in headers}
            continue
        if header_index is None:
            continue
        item = {
            field: strip_inline_code(cells[index]) if index < len(cells) else ""
            for header, field in headers.items()
            for index in [header_index[header]]
        }
        rows.extend(local_ai_metadata_use_cases({"local_ai_use_cases": [item]}))
    return rows


def metadata_integration_suggestions(root: Path, target: str = "all") -> list[dict[str, str]]:
    normalized = str(target or "all").strip().lower()
    if normalized not in {"all", "skill", "workflow"}:
        raise ValueError("target must be one of: all, skill, workflow")

    suggestions: list[dict[str, str]] = []
    if normalized in {"all", "skill"}:
        skills_root = root / ".agents" / "skills"
        skill_dirs = sorted(path for path in skills_root.iterdir() if path.is_dir()) if skills_root.exists() else []
        for skill_dir in skill_dirs:
            manifest_path = skill_dir / "module.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if str(manifest.get("status", "accepted")).strip().lower() not in {"accepted", ""}:
                continue
            skill_name = str(manifest.get("id") or manifest.get("name") or manifest_path.parent.name)
            for use_case in local_ai_metadata_use_cases(manifest):
                use_case_id = use_case["id"]
                suggestions.append(
                    {
                        "id": f"skill:{skill_name}:{use_case_id}",
                        "target": "skill",
                        "task": integration_task_for_use_case(use_case_id),
                        "use_case": use_case_id,
                        "manager": str(use_case.get("owner") or "skill-manager"),
                        "profile": integration_profile_for_use_case(use_case_id),
                        "command": use_case["command"],
                        "suggestion": f"{skill_name}: {use_case.get('applies_when', '')}",
                        "guardrail": use_case["guardrail"],
                        "evidence_input": use_case.get("evidence_input", ""),
                        "owner": use_case.get("owner", ""),
                    }
                )

    if normalized in {"all", "workflow"}:
        workflows_root = root / "automations"
        workflow_dirs = sorted(path for path in workflows_root.iterdir() if path.is_dir()) if workflows_root.exists() else []
        for workflow_dir in workflow_dirs:
            workflow_name = workflow_dir.name
            manifest_path = workflow_dir / "module.json"
            use_cases: list[dict[str, str]] = []
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    manifest = {}
                use_cases = local_ai_metadata_use_cases(manifest)
            for use_case in use_cases:
                use_case_id = use_case["id"]
                suggestions.append(
                    {
                        "id": f"workflow:{workflow_name}:{use_case_id}",
                        "target": "workflow",
                        "task": integration_task_for_use_case(use_case_id),
                        "use_case": use_case_id,
                        "manager": str(use_case.get("owner") or "workflow-manager"),
                        "profile": integration_profile_for_use_case(use_case_id),
                        "command": use_case["command"],
                        "suggestion": f"{workflow_name}: {use_case.get('applies_when', '')}",
                        "guardrail": use_case["guardrail"],
                        "evidence_input": use_case.get("evidence_input", ""),
                        "owner": use_case.get("owner", ""),
                    }
                )
    return suggestions
