#!/usr/bin/env python3
"""Check workflow plan templates and run plans for required filled sections."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import workflow_manager_common as common
import workflow_context_evidence
from workflow_support.template_layers import workflow_metadata


HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")
SEPARATOR_RE = re.compile(r"^\|\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?$")
BLANK_VALUES = {"", "-", "- [ ]", "[ ]", "todo", "tbd", "n/a?", "?"}
UNRESOLVED_VALUES = BLANK_VALUES | {"n/a", "na", "none?", "pending", "not started", "unknown", "placeholder"}
PLACEHOLDER_RE = re.compile(
    r"\b(?:TBD|TODO|PLACEHOLDER)\b|\[(?:number|title|ticket|run-id|fill|todo|tbd|placeholder)[^\]]*\]",
    re.IGNORECASE,
)
EXPLICIT_SKIP_PHRASES = (
    "no impact",
    "no security impact",
    "no persistence impact",
    "no ui impact",
    "no diagram",
    "no documentation impact",
    "no docs required",
    "not required",
    "skipped",
)


@dataclass(frozen=True)
class SectionRule:
    title: str
    table: bool = False
    label_columns: int = 0
    explicit_skip_phrases: tuple[str, ...] = ()


COMMON_RULES = [
    SectionRule("Out Of Scope"),
    SectionRule("Context Evidence", table=True),
    SectionRule("Project Context", table=True, label_columns=1),
    SectionRule("Impact Analysis", table=True),
    SectionRule("Security Impact", table=True, label_columns=1, explicit_skip_phrases=("no security impact",)),
    SectionRule("Persistence Impact", table=True, label_columns=1, explicit_skip_phrases=("no persistence impact",)),
    SectionRule("Diagram Plan", table=True, label_columns=1),
    SectionRule("UI And Screenshot Evidence", table=True, label_columns=1, explicit_skip_phrases=("no ui impact",)),
    SectionRule("Coverage And Quality Targets", table=True, label_columns=1),
    SectionRule("Planned Validation", table=True),
    SectionRule("Approval Gate"),
]

SPEC_KIT_GATE_RULES = [
    SectionRule("Clarification Decisions", table=True),
    SectionRule("Workflow Inputs And Gates", table=True, label_columns=1),
    SectionRule("Requirements Quality Checklist", table=True, label_columns=1),
    SectionRule("Cross-Artifact Coverage Analysis", table=True),
    SectionRule("Principles And Complexity Gate", table=True, label_columns=1),
    SectionRule("Template And Extension Layering", table=True, label_columns=1),
]

STORY_RULES = [
    SectionRule("Outcome"),
    SectionRule("Out Of Scope"),
    *SPEC_KIT_GATE_RULES,
    SectionRule("Acceptance Criteria Mapping", table=True),
    SectionRule("Impact Discovery Evidence", table=True, label_columns=1),
    *COMMON_RULES[1:],
    SectionRule("Bounded Work Packages", table=True),
    SectionRule("Implementation Checklist", table=True),
]

BUG_RULES = [
    SectionRule("Defect Statement"),
    SectionRule("Out Of Scope"),
    *SPEC_KIT_GATE_RULES,
    SectionRule("Assess Fix Test Boundaries", table=True, label_columns=1),
    SectionRule("Triage", table=True, label_columns=1),
    SectionRule("Reproduction Plan", table=True),
    SectionRule("Regression-Proof Decision"),
    SectionRule("Root Cause Evidence", table=True),
    *COMMON_RULES[1:],
    SectionRule("Bounded Work Packages", table=True),
]

DISCIPLINED_CHANGE_RULES = [
    SectionRule("Out Of Scope"),
    *SPEC_KIT_GATE_RULES,
    *COMMON_RULES[1:],
    SectionRule("Bounded Work Packages", table=True),
]

DOTNET_UPGRADE_RULES = [
    SectionRule("Upgrade Target"),
    SectionRule("Project Context", table=True, label_columns=1),
    SectionRule("Baseline Evidence", table=True),
    SectionRule("Microsoft Changelog Impact", table=True),
    SectionRule("NuGet Feed And Package Ownership", table=True),
    SectionRule("Dependency Resolution Plan", table=True),
    SectionRule("Plan Proof Checklist", table=True),
    SectionRule("Impact Analysis", table=True),
    SectionRule("Security Impact", table=True, label_columns=1, explicit_skip_phrases=("no security impact",)),
    SectionRule("Persistence Impact", table=True, label_columns=1, explicit_skip_phrases=("no persistence impact",)),
    SectionRule("Diagram Plan", table=True, label_columns=1),
    SectionRule("Upgrade Checklist", table=True),
    SectionRule("Rollback Plan", table=True),
    SectionRule("Planned Validation", table=True),
    SectionRule("Approval Gate"),
]

DOTNET_FRAMEWORK_MIGRATION_RULES = [
    SectionRule("Migration Target"),
    SectionRule("Project Context", table=True, label_columns=1),
    SectionRule("Legacy Inventory", table=True),
    SectionRule("Baseline Evidence", table=True),
    SectionRule("Compatibility Assessment", table=True),
    SectionRule("Migration Strategy", table=True),
    SectionRule("Plan Proof Checklist", table=True),
    SectionRule("Impact Analysis", table=True),
    SectionRule("Security Impact", table=True, label_columns=1, explicit_skip_phrases=("no security impact",)),
    SectionRule("Persistence Impact", table=True, label_columns=1, explicit_skip_phrases=("no persistence impact",)),
    SectionRule("Diagram Plan", table=True, label_columns=1),
    SectionRule("Migration Checklist", table=True),
    SectionRule("Rollback Plan", table=True),
    SectionRule("Planned Validation", table=True),
    SectionRule("Approval Gate"),
]


def normalize_heading(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def parse_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match and match.group(1) == "##":
            current = normalize_heading(match.group(2))
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def markdown_table_rows(section_text: str) -> list[list[str]]:
    rows = markdown_table_all_rows(section_text)
    if rows:
        return rows[1:]
    return rows


def markdown_table_all_rows(section_text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "|" not in stripped[1:]:
            continue
        if SEPARATOR_RE.match(stripped):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        rows.append(cells)
    return rows


def markdown_table_records(section_text: str) -> list[tuple[int, dict[str, str]]]:
    rows = markdown_table_all_rows(section_text)
    if len(rows) < 2:
        return []
    headers = rows[0]
    records: list[tuple[int, dict[str, str]]] = []
    for row_index, row in enumerate(rows[1:], start=1):
        record: dict[str, str] = {}
        for index, header in enumerate(headers):
            record[normalize_heading(header)] = row[index].strip() if index < len(row) else ""
        records.append((row_index, record))
    return records


def useful(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value.strip()).lower()
    return normalized not in BLANK_VALUES


def value_is_filled(value: str, *, allow_skip: bool = False, allow_pending: bool = False) -> bool:
    normalized = re.sub(r"\s+", " ", value.strip()).lower()
    if allow_pending and normalized in {"pending", "planned", "approval pending"}:
        return True
    if normalized in UNRESOLVED_VALUES:
        return False
    if PLACEHOLDER_RE.search(value):
        return False
    if allow_skip and any(phrase in normalized for phrase in EXPLICIT_SKIP_PHRASES):
        return True
    return bool(normalized)


def row_has_content(row: list[str], *, label_columns: int) -> bool:
    cells = row[label_columns:] if len(row) > label_columns else []
    return any(useful(cell) for cell in cells)


def check_table(rule: SectionRule, section_text: str, *, template: bool) -> list[str]:
    if template:
        return []
    lowered = section_text.lower()
    if any(phrase in lowered for phrase in rule.explicit_skip_phrases):
        return []
    rows = markdown_table_rows(section_text)
    if not rows:
        return [f"{rule.title}: no filled table rows or explicit skip reason"]
    incomplete = [
        index
        for index, row in enumerate(rows, start=1)
        if not row_has_content(row, label_columns=rule.label_columns)
    ]
    if incomplete:
        return [f"{rule.title}: incomplete row(s) {', '.join(str(item) for item in incomplete)}"]
    return []


def quality_issue(
    section: str,
    message: str,
    *,
    row: int | None = None,
    field: str = "",
    action: str = "",
    line: int | None = None,
) -> dict[str, object]:
    issue: dict[str, object] = {
        "section": section,
        "message": message,
        "severity": "error",
    }
    if row is not None:
        issue["row"] = row
    if field:
        issue["field"] = field
    if action:
        issue["action"] = action
    if line is not None:
        issue["line"] = line
    return issue


def format_quality_issue(issue: dict[str, object]) -> str:
    parts = [str(issue.get("section", "Plan"))]
    if issue.get("row") is not None:
        parts.append(f"row {issue['row']}")
    if issue.get("line") is not None:
        parts.append(f"line {issue['line']}")
    if issue.get("field"):
        parts.append(str(issue["field"]))
    message = " ".join(parts) + f": {issue.get('message')}"
    if issue.get("action"):
        message += f"; action: {issue['action']}"
    return message


def approval_status(sections: dict[str, str]) -> str:
    approval_text = sections.get(normalize_heading("Approval Gate"), "")
    match = re.search(r"Approval status:\s*(.+)", approval_text, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def approval_state(status: str) -> str:
    normalized = re.sub(r"\s+", " ", status.strip().lower())
    if not normalized:
        return "missing"
    if "not approved" in normalized:
        return "pending"
    if "approved" in normalized:
        return "approved"
    if any(term in normalized for term in ("pending", "planned", "requested", "waiting")):
        return "pending"
    if any(term in normalized for term in ("blocked", "rejected", "revised")):
        return "blocked"
    return "unknown"


def fix_queue(
    *,
    quality_issues: list[dict[str, object]],
    regular_issues: list[str],
    target_path: str,
) -> list[dict[str, object]]:
    queue: list[dict[str, object]] = []
    for issue in quality_issues:
        action = str(issue.get("action") or format_quality_issue(issue))
        item: dict[str, object] = {
            "section": str(issue.get("section") or "Plan"),
            "action": action,
            "target_path": target_path,
        }
        for key in ("row", "field", "line"):
            value = issue.get(key)
            if value is not None and value != "":
                item[key] = value
        queue.append(item)
    for issue in regular_issues:
        text = str(issue)
        section = "Plan"
        if text.startswith("missing section:"):
            section = text.removeprefix("missing section:").strip() or "Plan"
        elif ":" in text:
            section = text.split(":", 1)[0].strip() or "Plan"
        queue.append(
            {
                "section": section,
                "action": text,
                "target_path": target_path,
            }
        )
    return queue


def operator_next_action(
    *,
    queue: list[dict[str, object]],
    ok: bool,
    approval: str,
    run_id: str | None,
    workflow_name: str,
) -> str:
    if queue:
        return str(queue[0].get("action") or "Fix the first plan issue.")
    if not ok:
        return "Fix the remaining plan-check issues, then run plan-check again."
    state = approval_state(approval)
    if state == "approved":
        return "Resume approved implementation or validation from the current workflow phase."
    if state == "pending":
        return "Request and record approval before implementation."
    target = f"--run-id {run_id}" if run_id else "--template"
    return (
        f"Record approval status, then run workflow plan-check --name {workflow_name} {target} again."
    )


def require_cell(
    issues: list[dict[str, object]],
    *,
    section: str,
    row: int,
    record: dict[str, str],
    field: str,
    action: str,
    allow_skip: bool = False,
    allow_pending: bool = False,
) -> int:
    value = record.get(normalize_heading(field), "")
    if not value_is_filled(value, allow_skip=allow_skip, allow_pending=allow_pending):
        issues.append(
            quality_issue(
                section,
                f"{field} is empty, unresolved, or still a placeholder",
                row=row,
                field=field,
                action=action,
            )
        )
    return 1


def record_value(record: dict[str, str], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = record.get(normalize_heading(field), "")
        if value:
            return value
    return ""


def find_labeled_record(
    records: list[tuple[int, dict[str, str]]],
    labels: tuple[str, ...],
    *,
    label_fields: tuple[str, ...] = ("Item", "Area", "Discovery Item", "Topic", "Target"),
) -> tuple[int, dict[str, str]] | None:
    normalized_labels = {normalize_heading(label) for label in labels}
    normalized_label_fields = [normalize_heading(field) for field in label_fields]
    for row_index, record in records:
        for field in normalized_label_fields:
            label = normalize_heading(record.get(field, ""))
            if label in normalized_labels:
                return row_index, record
    return None


def require_labeled_value(
    issues: list[dict[str, object]],
    *,
    section: str,
    records: list[tuple[int, dict[str, str]]],
    labels: tuple[str, ...],
    value_fields: tuple[str, ...],
    action: str,
    allow_skip: bool = False,
    label_fields: tuple[str, ...] = ("Item", "Area", "Discovery Item", "Topic", "Target"),
) -> int:
    row = find_labeled_record(records, labels, label_fields=label_fields)
    field_label = labels[0]
    if row is None:
        issues.append(
            quality_issue(
                section,
                f"{field_label} row is missing",
                field=field_label,
                action=action,
            )
        )
        return 1
    row_index, record = row
    value = record_value(record, value_fields)
    if not value_is_filled(value, allow_skip=allow_skip):
        issues.append(
            quality_issue(
                section,
                f"{field_label} is empty, unresolved, or still a placeholder",
                row=row_index,
                field=field_label,
                action=action,
            )
        )
    return 1


def require_nonempty_table(
    issues: list[dict[str, object]],
    *,
    section: str,
    records: list[tuple[int, dict[str, str]]],
    action: str,
) -> int:
    if not records:
        issues.append(quality_issue(section, "no filled table rows", action=action))
    return 1


def check_placeholder_values(text: str) -> tuple[int, list[dict[str, object]]]:
    issues: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if PLACEHOLDER_RE.search(line):
            issues.append(
                quality_issue(
                    "Plan",
                    "unresolved placeholder/TBD value remains",
                    field="Placeholder",
                    line=line_number,
                    action="replace placeholders with evidence or an explicit skipped/blocked reason",
                )
            )
    return 1, issues


def check_shared_quality(sections: dict[str, str]) -> tuple[int, list[dict[str, object]]]:
    checked = 0
    issues: list[dict[str, object]] = []

    context_evidence_records = markdown_table_records(sections.get(normalize_heading("Context Evidence"), ""))
    checked += require_nonempty_table(
        issues,
        section="Context Evidence",
        records=context_evidence_records,
        action="record start context-evidence status, evidence path, and decision before implementation",
    )
    for row_index, record in context_evidence_records:
        checked += require_cell(
            issues,
            section="Context Evidence",
            row=row_index,
            record=record,
            field="Status",
            action="write the packet status, for example complete",
        )
        checked += require_cell(
            issues,
            section="Context Evidence",
            row=row_index,
            record=record,
            field="Evidence Path",
            action="point to validation/context-evidence-start.json or equivalent evidence",
        )
        checked += require_cell(
            issues,
            section="Context Evidence",
            row=row_index,
            record=record,
            field="Decision",
            action="state how the evidence affects the plan",
        )

    context_records = markdown_table_records(sections.get(normalize_heading("Project Context"), ""))
    checked += require_labeled_value(
        issues,
        section="Project Context",
        records=context_records,
        labels=("Context path",),
        value_fields=("Value", "Evidence Or Missing Fact"),
        action="record the project-context path or an explicit missing-context decision",
        allow_skip=True,
    )
    checked += require_labeled_value(
        issues,
        section="Project Context",
        records=context_records,
        labels=("Context check result",),
        value_fields=("Value", "Evidence Or Missing Fact"),
        action="record whether project context was current, fallback, missing, or blocked",
        allow_skip=True,
    )

    validation_records = markdown_table_records(sections.get(normalize_heading("Planned Validation"), ""))
    checked += require_nonempty_table(
        issues,
        section="Planned Validation",
        records=validation_records,
        action="add each planned command or manual check with expected evidence",
    )
    for row_index, record in validation_records:
        for field in ("Check", "Command Or Method", "Expected Evidence", "Required"):
            checked += require_cell(
                issues,
                section="Planned Validation",
                row=row_index,
                record=record,
                field=field,
                action="fill the validation row or remove it",
            )

    approval_text = sections.get(normalize_heading("Approval Gate"), "")
    checked += 1
    if not re.search(r"Approval status:\s*\S+", approval_text, re.IGNORECASE):
        issues.append(
            quality_issue(
                "Approval Gate",
                "approval status is empty",
                field="Approval status",
                action="record pending, planned, approved, blocked, or revised before continuing",
            )
        )

    return checked, issues


def require_table_cells(
    issues: list[dict[str, object]],
    *,
    section: str,
    records: list[tuple[int, dict[str, str]]],
    fields: tuple[str, ...],
    action: str,
    allow_skip_fields: tuple[str, ...] = (),
    allow_pending_fields: tuple[str, ...] = (),
) -> int:
    checked = require_nonempty_table(issues, section=section, records=records, action=action)
    skip_fields = {normalize_heading(field) for field in allow_skip_fields}
    pending_fields = {normalize_heading(field) for field in allow_pending_fields}
    for row_index, record in records:
        for field in fields:
            field_key = normalize_heading(field)
            checked += require_cell(
                issues,
                section=section,
                row=row_index,
                record=record,
                field=field,
                action=action,
                allow_skip=field_key in skip_fields,
                allow_pending=field_key in pending_fields,
            )
    return checked


def check_spec_driven_quality(sections: dict[str, str]) -> tuple[int, list[dict[str, object]]]:
    checked = 0
    issues: list[dict[str, object]] = []

    checked += require_table_cells(
        issues,
        section="Clarification Decisions",
        records=markdown_table_records(sections.get(normalize_heading("Clarification Decisions"), "")),
        fields=("Question Or Ambiguity", "Decision", "Evidence Or Owner", "Status"),
        action="record each resolved ambiguity, or one explicit no-clarification-needed decision",
        allow_skip_fields=("Decision",),
        allow_pending_fields=("Status",),
    )
    checked += require_table_cells(
        issues,
        section="Workflow Inputs And Gates",
        records=markdown_table_records(sections.get(normalize_heading("Workflow Inputs And Gates"), "")),
        fields=("Input Or Gate", "Type Or State", "Evidence", "Required"),
        action="record typed inputs, gate states, and whether each gate is required",
        allow_pending_fields=("Type Or State",),
    )
    checked += require_table_cells(
        issues,
        section="Requirements Quality Checklist",
        records=markdown_table_records(sections.get(normalize_heading("Requirements Quality Checklist"), "")),
        fields=("Check", "Result", "Evidence Or Gap", "Action"),
        action="treat the requirements checklist as pre-implementation quality evidence",
        allow_skip_fields=("Action",),
        allow_pending_fields=("Result",),
    )
    checked += require_table_cells(
        issues,
        section="Cross-Artifact Coverage Analysis",
        records=markdown_table_records(sections.get(normalize_heading("Cross-Artifact Coverage Analysis"), "")),
        fields=("Requirement Or Decision", "Covered By Plan Item", "Covered By Validation", "Gap Or Follow-Up"),
        action="map each requirement or decision to plan work, validation, and any remaining gap",
        allow_skip_fields=("Gap Or Follow-Up",),
    )
    checked += require_table_cells(
        issues,
        section="Principles And Complexity Gate",
        records=markdown_table_records(sections.get(normalize_heading("Principles And Complexity Gate"), "")),
        fields=("Principle Or Complexity Risk", "Decision", "Simpler Alternative Or Constraint", "Evidence"),
        action="record the constitution/principle check and justify complexity before implementation",
        allow_skip_fields=("Simpler Alternative Or Constraint",),
    )
    checked += require_table_cells(
        issues,
        section="Template And Extension Layering",
        records=markdown_table_records(sections.get(normalize_heading("Template And Extension Layering"), "")),
        fields=("Layer", "Decision", "Evidence Or Override Path", "Status"),
        action="record whether project overrides, presets, extensions, or core templates apply",
        allow_skip_fields=("Decision", "Evidence Or Override Path"),
        allow_pending_fields=("Status",),
    )

    return checked, issues


def check_story_quality(sections: dict[str, str]) -> tuple[int, list[dict[str, object]]]:
    checked = 0
    issues: list[dict[str, object]] = []

    acceptance_records = markdown_table_records(sections.get(normalize_heading("Acceptance Criteria Mapping"), ""))
    checked += require_nonempty_table(
        issues,
        section="Acceptance Criteria Mapping",
        records=acceptance_records,
        action="map each acceptance criterion to implementation, validation, and documentation decisions",
    )
    for row_index, record in acceptance_records:
        checked += require_cell(
            issues,
            section="Acceptance Criteria Mapping",
            row=row_index,
            record=record,
            field="Acceptance Criterion",
            action="copy or summarize the specific criterion",
        )
        checked += require_cell(
            issues,
            section="Acceptance Criteria Mapping",
            row=row_index,
            record=record,
            field="Implementation",
            action="name the implementation change or explicit no-code decision",
            allow_skip=True,
        )
        checked += require_cell(
            issues,
            section="Acceptance Criteria Mapping",
            row=row_index,
            record=record,
            field="Validation Evidence",
            action="name the command, artifact, screenshot, or manual proof",
        )
        checked += require_cell(
            issues,
            section="Acceptance Criteria Mapping",
            row=row_index,
            record=record,
            field="Documentation",
            action="name the documentation update or explicit no-documentation reason",
            allow_skip=True,
        )

    discovery_records = markdown_table_records(sections.get(normalize_heading("Impact Discovery Evidence"), ""))
    checked += require_labeled_value(
        issues,
        section="Impact Discovery Evidence",
        records=discovery_records,
        labels=("Candidate files read directly",),
        value_fields=("Evidence", "Decision Or Missing Fact"),
        action="list the direct files read after deterministic search candidate discovery",
        allow_skip=True,
    )

    return checked, issues


def bounded_work_packages(sections: dict[str, str]) -> list[dict[str, object]]:
    """Return normalized plan packages for execution-queue and checkpoint consumers."""

    records = markdown_table_records(sections.get(normalize_heading("Bounded Work Packages"), ""))
    packages: list[dict[str, object]] = []
    for row_index, record in records:
        package_id = record.get(normalize_heading("Package ID"), "").strip()
        raw_dependencies = record.get(normalize_heading("Depends On"), "").strip()
        dependencies = [] if normalize_heading(raw_dependencies) in {"none", "no dependencies"} else [
            item.strip() for item in re.split(r"[,;]", raw_dependencies) if item.strip()
        ]
        packages.append(
            {
                "row": row_index,
                "id": package_id,
                "outcome": record.get(normalize_heading("Outcome"), "").strip(),
                "invariant": record.get(normalize_heading("Invariant"), "").strip(),
                "depends_on": raw_dependencies,
                "dependencies": dependencies,
                "non_goals": record.get(normalize_heading("Non-Goals"), "").strip(),
                "owner_paths": record.get(normalize_heading("Owner Paths"), "").strip(),
                "verification": record.get(normalize_heading("Verification"), "").strip(),
                "completion_criteria": record.get(normalize_heading("Completion Criteria"), "").strip(),
                "handoff": record.get(normalize_heading("Handoff"), "").strip(),
                "status": record.get(normalize_heading("Status"), "").strip(),
            }
        )
    return packages


def check_bounded_work_packages(sections: dict[str, str]) -> tuple[int, list[dict[str, object]]]:
    checked = 0
    issues: list[dict[str, object]] = []
    packages = bounded_work_packages(sections)
    section = "Bounded Work Packages"
    if not packages:
        return 1, [quality_issue(section, "at least one work package is required", action="add one bounded package, or 3-8 dependency-ordered packages for complex work")]
    checked += 1
    if len(packages) > 8:
        issues.append(quality_issue(section, "V1 supports at most eight work packages", action="merge packages until the plan has no more than eight independently verifiable outcomes"))

    ids = [str(item["id"]) for item in packages]
    known = {item for item in ids if item}
    earlier_ids: set[str] = set()
    for package in packages:
        row = int(package["row"])
        for field, key in (
            ("Package ID", "id"),
            ("Outcome", "outcome"),
            ("Invariant", "invariant"),
            ("Non-Goals", "non_goals"),
            ("Owner Paths", "owner_paths"),
            ("Verification", "verification"),
            ("Completion Criteria", "completion_criteria"),
            ("Handoff", "handoff"),
        ):
            checked += 1
            if not value_is_filled(str(package[key]), allow_skip=(key in {"non_goals", "handoff"})):
                issues.append(quality_issue(section, f"{field} is empty, unresolved, or still a placeholder", row=row, field=field, action=f"record the package {field.lower()}"))
        checked += 1
        raw_dependencies = normalize_heading(str(package["depends_on"]))
        if not raw_dependencies or (raw_dependencies in UNRESOLVED_VALUES and raw_dependencies != "none"):
            issues.append(quality_issue(section, "Depends On is empty or unresolved", row=row, field="Depends On", action="name earlier Package IDs or write `none`"))
        checked += 1
        package_id = str(package["id"])
        if package_id and ids.count(package_id) > 1:
            issues.append(quality_issue(section, "Package ID must be unique", row=row, field="Package ID", action="assign a stable unique package ID"))
        for dependency in package["dependencies"]:
            checked += 1
            if dependency == package_id:
                issues.append(quality_issue(section, "a package cannot depend on itself", row=row, field="Depends On", action="remove the self-dependency"))
            elif dependency not in known:
                issues.append(quality_issue(section, f"unknown dependency: {dependency}", row=row, field="Depends On", action="reference a declared Package ID or write `none`"))
            elif dependency not in earlier_ids:
                issues.append(quality_issue(section, f"dependency must reference an earlier Package ID: {dependency}", row=row, field="Depends On", action="reorder the packages or depend only on an earlier row"))
        if package_id:
            earlier_ids.add(package_id)

    graph = {str(item["id"]): [str(dep) for dep in item["dependencies"] if dep in known] for item in packages if item["id"]}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(dependency) for dependency in graph.get(node, [])):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    checked += 1
    if any(visit(node) for node in graph if node not in visited):
        issues.append(quality_issue(section, "dependency graph contains a cycle", field="Depends On", action="make package dependencies acyclic"))
    return checked, issues


def regression_field_value(section_text: str, label: str) -> str:
    pattern = re.compile(rf"^\s*[-*]?\s*{re.escape(label)}\s*:\s*(.*)$", re.IGNORECASE | re.MULTILINE)
    match = pattern.search(section_text)
    if not match:
        return ""
    return match.group(1).strip()


def check_bug_quality(sections: dict[str, str]) -> tuple[int, list[dict[str, object]]]:
    checked = 0
    issues: list[dict[str, object]] = []

    boundary_records = markdown_table_records(sections.get(normalize_heading("Assess Fix Test Boundaries"), ""))
    checked += require_nonempty_table(
        issues,
        section="Assess Fix Test Boundaries",
        records=boundary_records,
        action="record assess/fix/test boundaries before approval",
    )
    for stage in ("Assess", "Fix", "Test"):
        checked += require_labeled_value(
            issues,
            section="Assess Fix Test Boundaries",
            records=boundary_records,
            labels=(stage,),
            label_fields=("Stage",),
            value_fields=("Allowed Writes", "Evidence Artifact", "Status"),
            action=f"record the {stage.lower()} stage write boundary and evidence artifact",
            allow_skip=True,
        )

    triage_records = markdown_table_records(sections.get(normalize_heading("Triage"), ""))
    checked += require_labeled_value(
        issues,
        section="Triage",
        records=triage_records,
        labels=("Affected versions",),
        value_fields=("Value", "Notes", "Evidence"),
        action="record affected versions or an explicit unknown/not-applicable reason",
        allow_skip=True,
    )
    checked += require_labeled_value(
        issues,
        section="Triage",
        records=triage_records,
        labels=("Release-line decision",),
        value_fields=("Value", "Notes", "Evidence"),
        action="record the target version/backport decision or explicit skipped reason",
        allow_skip=True,
    )

    reproduction_records = markdown_table_records(sections.get(normalize_heading("Reproduction Plan"), ""))
    checked += require_nonempty_table(
        issues,
        section="Reproduction Plan",
        records=reproduction_records,
        action="add reproduction steps or a non-reproduction proof decision",
    )
    for row_index, record in reproduction_records:
        checked += require_cell(
            issues,
            section="Reproduction Plan",
            row=row_index,
            record=record,
            field="Step",
            action="describe the reproduction step",
        )
        value = record_value(
            record,
            ("Command Or Action", "Expected Failure", "Expected Result", "Evidence", "Evidence Path", "Status"),
        )
        checked += 1
        if not value_is_filled(value, allow_skip=True):
            issues.append(
                quality_issue(
                    "Reproduction Plan",
                    "reproduction proof is empty, unresolved, or still a placeholder",
                    row=row_index,
                    field="Evidence",
                    action="record the command/action plus expected failure/evidence or an explicit skip reason",
                )
            )

    regression_text = sections.get(normalize_heading("Regression-Proof Decision"), "")
    for field in ("Test before fix", "Test after fix", "Manual proof accepted", "Reason"):
        checked += 1
        value = regression_field_value(regression_text, field)
        if not value_is_filled(value, allow_skip=True):
            issues.append(
                quality_issue(
                    "Regression-Proof Decision",
                    f"{field} is empty, unresolved, or still a placeholder",
                    field=field,
                    action="fill the regression proof decision or explain why it is skipped",
                )
            )

    root_cause_records = markdown_table_records(sections.get(normalize_heading("Root Cause Evidence"), ""))
    checked += require_nonempty_table(
        issues,
        section="Root Cause Evidence",
        records=root_cause_records,
        action="record the current root-cause hypothesis and evidence source before approval",
    )
    for row_index, record in root_cause_records:
        for field in ("Hypothesis", "Evidence", "Decision"):
            checked += require_cell(
                issues,
                section="Root Cause Evidence",
                row=row_index,
                record=record,
                field=field,
                action="fill root-cause evidence or an explicit unknown-with-reason decision",
                allow_skip=True,
            )

    return checked, issues


def check_quality(workflow_name: str, sections: dict[str, str], text: str) -> tuple[int, list[dict[str, object]]]:
    checked, issues = check_placeholder_values(text)
    shared_checked, shared_issues = check_shared_quality(sections)
    checked += shared_checked
    issues.extend(shared_issues)
    if workflow_name in {"user-story-workflow", "bug-ticket-workflow", "disciplined-change-workflow"}:
        spec_checked, spec_issues = check_spec_driven_quality(sections)
        checked += spec_checked
        issues.extend(spec_issues)
        package_checked, package_issues = check_bounded_work_packages(sections)
        checked += package_checked
        issues.extend(package_issues)
    if workflow_name not in {"user-story-workflow", "bug-ticket-workflow"}:
        return checked, issues
    if workflow_name == "user-story-workflow":
        workflow_checked, workflow_issues = check_story_quality(sections)
    else:
        workflow_checked, workflow_issues = check_bug_quality(sections)
    checked += workflow_checked
    issues.extend(workflow_issues)
    return checked, issues


def rules_for_workflow(workflow_name: str) -> list[SectionRule]:
    if workflow_name == "user-story-workflow":
        return STORY_RULES
    if workflow_name == "bug-ticket-workflow":
        return BUG_RULES
    if workflow_name == "disciplined-change-workflow":
        return DISCIPLINED_CHANGE_RULES
    if workflow_name == "dotnet-upgrade":
        return DOTNET_UPGRADE_RULES
    if workflow_name == "dotnet-framework-migration":
        return DOTNET_FRAMEWORK_MIGRATION_RULES
    return COMMON_RULES


def resolve_plan_path(root: Path, workflow_name: str, *, run_id: str | None, template: bool, plan_path: Path | None) -> Path:
    selected = sum(1 for item in (bool(run_id), template, bool(plan_path)) if item)
    if selected != 1:
        raise SystemExit("workflow plan-check requires exactly one of --run-id, --template, or --plan")
    if plan_path:
        return plan_path if plan_path.is_absolute() else root / plan_path
    workflow_dir = root / "automations" / workflow_name
    if template:
        return workflow_dir / "templates" / "plan.md"
    return workflow_dir / "runs" / run_id / "plan.md"


def metadata_gate_issues(root: Path, workflow_name: str, sections: dict[str, str], *, template: bool) -> list[str]:
    metadata = workflow_metadata(root, workflow_name)
    gates = metadata.get("gates") if isinstance(metadata.get("gates"), list) else []
    issues: list[str] = []
    for gate in gates:
        if not isinstance(gate, dict) or gate.get("required") is False:
            continue
        evidence = str(gate.get("evidence", "")).strip()
        gate_id = str(gate.get("id", "")).strip() or "unnamed"
        if not evidence or any(token in evidence for token in ("/", "\\")) or evidence.lower().endswith((".md", ".json")):
            continue
        section_key = normalize_heading(evidence)
        section_text = sections.get(section_key)
        if section_text is None:
            issues.append(f"gate '{gate_id}' declares evidence section '{evidence}' but plan is missing it")
        elif not template and not any(useful(line) for line in section_text.splitlines()):
            issues.append(f"gate '{gate_id}' evidence section '{evidence}' is empty")
    return issues


def check_plan(
    root: Path,
    workflow_name: str,
    *,
    run_id: str | None = None,
    template: bool = False,
    plan_path: Path | None = None,
) -> dict[str, object]:
    root = root.expanduser().resolve()
    path = resolve_plan_path(root, workflow_name, run_id=run_id, template=template, plan_path=plan_path)
    text = common.read_text(path)
    issues: list[str] = []
    if not text:
        issues.append(f"plan file is missing or empty: {common.relative(root, path)}")
    sections = parse_sections(text)
    rules = rules_for_workflow(workflow_name)
    for rule in rules:
        section_key = normalize_heading(rule.title)
        section_text = sections.get(section_key)
        if section_text is None:
            issues.append(f"missing section: {rule.title}")
            continue
        if rule.table:
            issues.extend(check_table(rule, section_text, template=template))
        elif not template and not any(useful(line) for line in section_text.splitlines()):
            issues.append(f"{rule.title}: section is empty")
    issues.extend(metadata_gate_issues(root, workflow_name, sections, template=template))
    quality_checked = 0
    quality_issues: list[dict[str, object]] = []
    if not template:
        quality_checked, quality_issues = check_quality(workflow_name, sections, text)
        issues.extend(format_quality_issue(issue) for issue in quality_issues)
    if run_id and not template:
        run_dir = root / "automations" / workflow_name / "runs" / run_id
        issues.extend(workflow_context_evidence.validate_context_evidence_packet(root, workflow_name, run_dir, event="start"))
    target_path = common.relative(root, path)
    quality_messages = {format_quality_issue(issue) for issue in quality_issues}
    regular_issues = [issue for issue in issues if issue not in quality_messages]
    approval = approval_status(sections)
    state = approval_state(approval)
    queue = fix_queue(
        quality_issues=quality_issues,
        regular_issues=regular_issues,
        target_path=target_path,
    )
    ok = not issues
    report = {
        "schema_version": 1,
        "tool": "workflow-manager.plan-check",
        "ok": ok,
        "status": "ok" if ok else "failed",
        "workflow": workflow_name,
        "run_id": run_id or "",
        "mode": "template" if template else "run",
        "plan_path": target_path,
        "checked_sections": [rule.title for rule in rules],
        "issue_count": len(issues),
        "issues": issues,
        "quality_summary": {
            "checked": quality_checked,
            "passed": max(quality_checked - len(quality_issues), 0),
            "failed": len(quality_issues),
        },
        "quality_issues": quality_issues,
        "fix_queue": queue,
        "operator_next_action": operator_next_action(
            queue=queue,
            ok=ok,
            approval=approval,
            run_id=run_id,
            workflow_name=workflow_name,
        ),
        "approval_status": approval,
        "ready_for_approval": (not template and ok and state == "pending"),
        "implementation_allowed": (not template and ok and state == "approved"),
        "next_command": (
            f"python -B .agents/manage.py workflow plan-check --name {workflow_name} --run-id {run_id}"
            if run_id
            else f"python -B .agents/manage.py workflow plan-check --name {workflow_name} --template"
        ),
    }
    return report


def render_plan_check(report: dict[str, object]) -> str:
    lines = ["# Workflow Plan Check", ""]
    lines.append(f"- Workflow: `{report.get('workflow')}`")
    lines.append(f"- Mode: {report.get('mode')}")
    lines.append(f"- Plan: `{report.get('plan_path')}`")
    lines.append(f"- Status: {report.get('status')}")
    if report.get("operator_next_action"):
        lines.append(f"- Next action: {report.get('operator_next_action')}")
    lines.append(f"- Ready for approval: {str(report.get('ready_for_approval')).lower()}")
    lines.append(f"- Implementation allowed: {str(report.get('implementation_allowed')).lower()}")
    if report.get("quality_summary"):
        summary = report.get("quality_summary")
        if isinstance(summary, dict) and summary.get("checked"):
            lines.append(
                f"- Quality: {summary.get('passed', 0)} passed, {summary.get('failed', 0)} failed"
            )
    quality_messages = {
        format_quality_issue(issue)
        for issue in report.get("quality_issues", [])
        if isinstance(issue, dict)
    }
    regular_issues = [
        issue
        for issue in report.get("issues", [])
        if str(issue) not in quality_messages
    ]
    fix_items = report.get("fix_queue") if isinstance(report.get("fix_queue"), list) else []
    if fix_items:
        lines.extend(["", "## Fix Queue", ""])
        for index, item in enumerate(fix_items, start=1):
            if not isinstance(item, dict):
                continue
            details = []
            if item.get("row") is not None:
                details.append(f"row {item.get('row')}")
            if item.get("field"):
                details.append(str(item.get("field")))
            where = f" ({', '.join(details)})" if details else ""
            lines.append(
                f"{index}. {item.get('section', 'Plan')}{where}: {item.get('action')} "
                f"[`{item.get('target_path')}`]"
            )
    if regular_issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in regular_issues)
    if report.get("quality_issues"):
        lines.extend(["", "## Quality Issues", ""])
        for issue in report.get("quality_issues", []):
            if isinstance(issue, dict):
                lines.append(f"- {format_quality_issue(issue)}")
    lines.append(f"- Next command: `{report.get('next_command')}`")
    return "\n".join(lines) + "\n"
