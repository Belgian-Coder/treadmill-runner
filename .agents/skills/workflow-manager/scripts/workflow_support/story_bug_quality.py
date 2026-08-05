#!/usr/bin/env python3
"""Story and bug workflow finish-quality checks."""

from __future__ import annotations

import json
import re
from pathlib import Path

import workflow_manager_common as common
import workflow_plan_check

OUT_OF_SCOPE_WORKFLOWS = {"user-story-workflow", "bug-ticket-workflow"}
PROGRESS_DOCUMENT_WORKFLOWS = {"user-story-workflow", "bug-ticket-workflow"}
CLOSEOUT_EVIDENCE_WORKFLOWS = {"user-story-workflow", "bug-ticket-workflow", "disciplined-change-workflow"}
OUT_OF_SCOPE_TEMPLATE_FILES = (
    "templates/ticket-info.md",
    "templates/plan.md",
    "templates/pr-description.md",
)
PR_TEMPLATE_PLACEHOLDER_PATTERNS = (
    "[number]",
    "[title]",
    "[link to plan.md in the work folder]",
    "User Story | Bug",
)
PR_REQUIRED_SECTIONS = ("Summary", "Validation", "Plan Variance", "Independent Review Evidence")
REUSABLE_LESSON_SECTION = "Reusable Lessons"
REUSABLE_LESSON_NO_REASON_RE = re.compile(r"^\s*(?:[-*]\s*)?No reusable lessons?\s*:\s*\S+", re.IGNORECASE)
REUSABLE_LESSON_PLACEHOLDER_RE = re.compile(r"<[^>]+>|\b(?:todo|tbd|placeholder)\b", re.IGNORECASE)
EXPLICIT_FINAL_DECISION_RE = re.compile(
    r"\b(?:no impact|no docs?|no documentation|not required|skipped|blocked)\b",
    re.IGNORECASE,
)
OWNER_DECISION_RE = re.compile(r"\b(?:owner|decision|approved|accepted|because|reason|risk|ticket|doc-[0-9]+)\b", re.IGNORECASE)
SKIPPED_WITH_SUBSTANTIVE_REASON_RE = re.compile(r"\bskipped\s*(?::|[-—])\s*(?!reason\b)\S.+", re.IGNORECASE)


def review_result_issue(result: str) -> str:
    normalized = workflow_plan_check.normalize_heading(result)
    if any(state in normalized for state in ("failed", "blocked")):
        return f"has non-finishable result: {normalized}"
    if "skipped" in normalized and not SKIPPED_WITH_SUBSTANTIVE_REASON_RE.search(result):
        return "must record `skipped: <substantive reason>`"
    return ""


def progress_line_has_value(text: str, prefix: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            value = stripped[len(prefix):].strip()
            return bool(value)
    return False


def progress_log_issues(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    run_packet: dict[str, object],
) -> list[str]:
    if workflow_name not in PROGRESS_DOCUMENT_WORKFLOWS:
        return []
    path = run_dir / "execution-log.md"
    if not path.exists():
        return [f"{common.relative(root, path)} is missing; update progress before finish"]
    text = common.read_text(path, limit=80_000)
    issues: list[str] = []
    required_sections = [
        "## Progress Update Rules",
        "## Current State",
        "## Phase Handoffs",
        "## Plan Item Progress",
        "## Plan Variance",
        "## Independent Review Evidence",
        "## Validation Evidence Map",
        "## Context And Claim Support",
        "## Reusable Lessons",
    ]
    if workflow_name == "bug-ticket-workflow":
        required_sections.extend(["## Reproduction Evidence", "## Commands And Validation"])
    else:
        required_sections.append("## Commands And Evidence")
    for section in required_sections:
        if section not in text:
            issues.append(f"execution-log.md missing required section: {section}")
    if "- Status: not started" in text:
        issues.append("execution-log.md still has template status: not started")
    if "### Phase:" in text and "### Phase: " not in text:
        issues.append("execution-log.md contains an empty phase handoff placeholder")
    if not progress_line_has_value(text, "- Status:"):
        issues.append("execution-log.md current status is empty")
    if not progress_line_has_value(text, "- Current phase:"):
        issues.append("execution-log.md current phase is empty")
    if not progress_line_has_value(text, "- Last updated:"):
        issues.append("execution-log.md last updated is empty")
    current_phase = str(run_packet.get("current_phase") or "").strip()
    if current_phase and current_phase not in text:
        issues.append(f"execution-log.md does not mention current phase: {current_phase}")
    status = str(run_packet.get("status") or "").strip()
    if status and status not in text:
        issues.append(f"execution-log.md does not mention current status: {status}")
    if "Evidence ledger path:" in text and not progress_line_has_value(text, "- Evidence ledger path:"):
        issues.append("execution-log.md evidence ledger path is empty")
    reusable_body = markdown_section_body(text, REUSABLE_LESSON_SECTION)
    if reusable_body and not reusable_lesson_has_final_content(reusable_body):
        issues.append(
            "execution-log.md Reusable Lessons must include a reusable lesson or "
            "`No reusable lesson: <reason>`"
        )
    return [f"{common.relative(root, path)}: {issue}" for issue in issues]


def closeout_evidence_issues(root: Path, workflow_name: str, run_dir: Path) -> list[str]:
    """Require plan variance and independent review axes before a V1 workflow can finish."""

    if workflow_name not in CLOSEOUT_EVIDENCE_WORKFLOWS:
        return []
    path = run_dir / "execution-log.md"
    if not path.exists():
        return [f"{common.relative(root, path)} is missing; record closeout evidence before finish"]
    sections = workflow_plan_check.parse_sections(common.read_text(path, limit=120_000))
    issues: list[str] = []
    plan_sections = workflow_plan_check.parse_sections(common.read_text(run_dir / "plan.md", limit=120_000))
    known_packages = {
        workflow_plan_check.normalize_heading(str(package.get("id", "")))
        for package in workflow_plan_check.bounded_work_packages(plan_sections)
        if package.get("id")
    }

    variance = workflow_plan_check.markdown_table_records(
        sections.get(workflow_plan_check.normalize_heading("Plan Variance"), "")
    )
    if not variance:
        issues.append("Plan Variance needs at least one filled row, including an explicit `No variance` row when execution matched the plan")
    for row_index, record in variance:
        for field in ("Package", "Planned", "Actual", "Reason", "Approval Impact", "Validation Impact"):
            value = record.get(workflow_plan_check.normalize_heading(field), "")
            if not workflow_plan_check.value_is_filled(value, allow_skip=True):
                issues.append(f"Plan Variance row {row_index} field {field} is empty or unresolved")
        package_value = workflow_plan_check.normalize_heading(
            record.get(workflow_plan_check.normalize_heading("Package"), "")
        )
        if package_value and package_value != "no variance" and package_value not in known_packages:
            issues.append(f"Plan Variance row {row_index} references unknown package: {package_value}")

    reviews = workflow_plan_check.markdown_table_records(
        sections.get(workflow_plan_check.normalize_heading("Independent Review Evidence"), "")
    )
    expected_axes = {
        "spec and plan compliance",
        "standards and maintainability",
        "security and authority",
        "validation and generated artifacts",
    }
    by_axis = {
        workflow_plan_check.normalize_heading(record.get(workflow_plan_check.normalize_heading("Axis"), "")): (row_index, record)
        for row_index, record in reviews
    }
    for axis in sorted(expected_axes):
        if axis not in by_axis:
            issues.append(f"Independent Review Evidence is missing axis: {axis}")
            continue
        row_index, record = by_axis[axis]
        for field in ("Reviewer Or Method", "Result", "Evidence", "Disposition"):
            value = record.get(workflow_plan_check.normalize_heading(field), "")
            if not workflow_plan_check.value_is_filled(value, allow_skip=True):
                issues.append(f"Independent Review Evidence row {row_index} axis {axis} field {field} is empty or unresolved")
        raw_result = record.get(workflow_plan_check.normalize_heading("Result"), "")
        result_issue = review_result_issue(raw_result)
        if result_issue:
            issues.append(f"Independent Review Evidence axis {axis} {result_issue}")
    return [f"{common.relative(root, path)}: {issue}" for issue in issues]


def out_of_scope_template_issues(root: Path, workflow_name: str) -> list[str]:
    if workflow_name not in OUT_OF_SCOPE_WORKFLOWS:
        return []
    module_dir = root / "automations" / workflow_name
    issues: list[str] = []
    for relative_path in OUT_OF_SCOPE_TEMPLATE_FILES:
        path = module_dir / relative_path
        if not path.exists() or "## Out Of Scope" not in common.read_text(path, limit=20_000):
            issues.append(f"out-of-scope template missing: {common.relative(root, path)}")
    return issues


def markdown_section_body(text: str, section_title: str) -> list[str]:
    marker = f"## {section_title}".lower()
    lines = text.splitlines()
    body: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_section:
                break
            in_section = stripped.lower() == marker
            continue
        if in_section:
            body.append(line)
    return body


def section_has_final_content(lines: list[str]) -> bool:
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped in {"-", "1."}:
            continue
        if stripped.startswith("|") and set(stripped.replace("|", "").replace(" ", "")) <= {"-"}:
            continue
        if stripped.startswith("|") and any(header in stripped.lower() for header in ("check", "result", "evidence", "criterion")):
            continue
        return True
    return False


def reusable_lesson_has_final_content(lines: list[str]) -> bool:
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped in {"-", "1."}:
            continue
        if REUSABLE_LESSON_PLACEHOLDER_RE.search(stripped):
            continue
        if REUSABLE_LESSON_NO_REASON_RE.search(stripped):
            return True
        return True
    return False


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).lower()


def evidence_corpus(root: Path, run_dir: Path, run_packet: dict[str, object]) -> str:
    parts = [json.dumps(run_packet, sort_keys=True)]
    for path in (run_dir / "REPORT.md", run_dir / "execution-log.md", run_dir / "pr-description.md"):
        if path.exists():
            parts.append(common.read_text(path, limit=80_000))
    validation_dir = run_dir / "validation"
    if validation_dir.exists():
        for path in sorted(validation_dir.rglob("*"))[:80]:
            if not path.is_file() or path.suffix.lower() not in {".json", ".md", ".txt"}:
                continue
            parts.append(common.relative(root, path))
            parts.append(common.read_text(path, limit=20_000))
    return normalized_text("\n".join(parts))


def proof_value_reflected(value: str, corpus: str) -> bool:
    normalized = normalized_text(value)
    if not workflow_plan_check.value_is_filled(value, allow_skip=True):
        return False
    if EXPLICIT_FINAL_DECISION_RE.search(value) and not OWNER_DECISION_RE.search(value):
        return False
    return normalized in corpus


def proof_issue(
    *,
    section: str,
    row: int | None = None,
    field: str,
    expected: str,
    target_path: str,
) -> dict[str, object]:
    issue: dict[str, object] = {
        "section": section,
        "field": field,
        "expected": expected,
        "message": "missing final proof in run evidence or pr-description.md",
        "action": "record final proof using the same plan value or an explicit skipped/blocked reason with owner decision evidence",
        "target_path": target_path,
    }
    if row is not None:
        issue["row"] = row
    return issue


def format_proof_issue(issue: dict[str, object]) -> str:
    parts = [str(issue.get("section", "Proof"))]
    if issue.get("row") is not None:
        parts.append(f"row {issue['row']}")
    if issue.get("field"):
        parts.append(str(issue["field"]))
    message = " ".join(parts) + f": {issue.get('message')}"
    if issue.get("action"):
        message += f"; action: {issue['action']}"
    return message


def require_reflected_proof(
    missing: list[dict[str, object]],
    matrix: list[dict[str, object]],
    *,
    corpus: str,
    section: str,
    row: int | None,
    field: str,
    expected: str,
    target_path: str,
) -> None:
    reflected = proof_value_reflected(expected, corpus)
    matrix.append(
        {
            "section": section,
            "row": row,
            "field": field,
            "expected": expected,
            "reflected": reflected,
        }
    )
    if not reflected:
        missing.append(
            proof_issue(
                section=section,
                row=row,
                field=field,
                expected=expected,
                target_path=target_path,
            )
        )


def labeled_record(records: list[tuple[int, dict[str, str]]], label: str) -> tuple[int, dict[str, str]] | None:
    normalized = workflow_plan_check.normalize_heading(label)
    for row_index, record in records:
        for field in ("item", "area"):
            if workflow_plan_check.normalize_heading(record.get(field, "")) == normalized:
                return row_index, record
    return None


def reusable_lesson_candidates(run_dir: Path) -> list[str]:
    candidates: list[str] = []
    for path in (run_dir / "execution-log.md", run_dir / "pr-description.md"):
        if not path.exists():
            continue
        body = markdown_section_body(common.read_text(path, limit=80_000), REUSABLE_LESSON_SECTION)
        for line in body:
            stripped = line.strip().lstrip("-* ").strip()
            if not stripped or stripped in {"-", "1."}:
                continue
            if REUSABLE_LESSON_PLACEHOLDER_RE.search(stripped):
                continue
            if REUSABLE_LESSON_NO_REASON_RE.search(stripped):
                continue
            if stripped not in candidates:
                candidates.append(stripped)
    return candidates


def story_bug_finish_proof_report(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    run_packet: dict[str, object],
) -> dict[str, object]:
    if workflow_name not in PROGRESS_DOCUMENT_WORKFLOWS:
        return {"ok": True, "status": "skipped", "proof_matrix": [], "missing_proof": [], "lesson_candidates": []}
    plan_path = run_dir / "plan.md"
    text = common.read_text(plan_path, limit=120_000)
    sections = workflow_plan_check.parse_sections(text)
    if workflow_plan_check.approval_state(workflow_plan_check.approval_status(sections)) != "approved":
        return {"ok": True, "status": "skipped-unapproved", "proof_matrix": [], "missing_proof": [], "lesson_candidates": []}

    corpus = evidence_corpus(root, run_dir, run_packet)
    target_path = common.relative(root, plan_path)
    matrix: list[dict[str, object]] = []
    missing: list[dict[str, object]] = []

    if workflow_name == "user-story-workflow":
        records = workflow_plan_check.markdown_table_records(
            sections.get(workflow_plan_check.normalize_heading("Acceptance Criteria Mapping"), "")
        )
        for row_index, record in records:
            for field in ("Implementation", "Validation Evidence", "Documentation"):
                require_reflected_proof(
                    missing,
                    matrix,
                    corpus=corpus,
                    section="Acceptance Criteria Mapping",
                    row=row_index,
                    field=field,
                    expected=record.get(workflow_plan_check.normalize_heading(field), ""),
                    target_path=target_path,
                )
    elif workflow_name == "bug-ticket-workflow":
        triage_records = workflow_plan_check.markdown_table_records(
            sections.get(workflow_plan_check.normalize_heading("Triage"), "")
        )
        for label in ("Affected versions", "Release-line decision"):
            row = labeled_record(triage_records, label)
            if row:
                row_index, record = row
                require_reflected_proof(
                    missing,
                    matrix,
                    corpus=corpus,
                    section="Triage",
                    row=row_index,
                    field=label,
                    expected=workflow_plan_check.record_value(record, ("Value", "Notes", "Evidence")),
                    target_path=target_path,
                )
        reproduction_records = workflow_plan_check.markdown_table_records(
            sections.get(workflow_plan_check.normalize_heading("Reproduction Plan"), "")
        )
        for row_index, record in reproduction_records:
            require_reflected_proof(
                missing,
                matrix,
                corpus=corpus,
                section="Reproduction Plan",
                row=row_index,
                field="Evidence",
                expected=workflow_plan_check.record_value(
                    record,
                    ("Evidence", "Evidence Path", "Expected Failure", "Expected Result", "Status"),
                ),
                target_path=target_path,
            )
        regression_text = sections.get(workflow_plan_check.normalize_heading("Regression-Proof Decision"), "")
        for field in ("Test before fix", "Test after fix", "Manual proof accepted", "Reason"):
            require_reflected_proof(
                missing,
                matrix,
                corpus=corpus,
                section="Regression-Proof Decision",
                row=None,
                field=field,
                expected=workflow_plan_check.regression_field_value(regression_text, field),
                target_path=target_path,
            )
        root_records = workflow_plan_check.markdown_table_records(
            sections.get(workflow_plan_check.normalize_heading("Root Cause Evidence"), "")
        )
        for row_index, record in root_records:
            for field in ("Evidence", "Decision"):
                require_reflected_proof(
                    missing,
                    matrix,
                    corpus=corpus,
                    section="Root Cause Evidence",
                    row=row_index,
                    field=field,
                    expected=record.get(workflow_plan_check.normalize_heading(field), ""),
                    target_path=target_path,
                )

    by_section: dict[str, int] = {}
    for issue in missing:
        section = str(issue.get("section", "Proof"))
        by_section[section] = by_section.get(section, 0) + 1
    return {
        "ok": not missing,
        "status": "ok" if not missing else "failed",
        "proof_matrix": matrix,
        "missing_proof": missing,
        "missing_count": len(missing),
        "proof_gap_summary": {
            "missing_count": len(missing),
            "by_section": by_section,
        },
        "lesson_candidates": reusable_lesson_candidates(run_dir),
    }


def pr_handoff_issues(root: Path, run_dir: Path) -> list[str]:
    path = run_dir / "pr-description.md"
    if not path.exists():
        return []
    text = common.read_text(path, limit=80_000)
    issues: list[str] = []
    for placeholder in PR_TEMPLATE_PLACEHOLDER_PATTERNS:
        if placeholder in text:
            issues.append(f"pr-description.md contains template placeholder: {placeholder}")
    for line_number, line in enumerate(text.splitlines(), start=1):
        if line.strip() in {"-", "1."}:
            issues.append(f"pr-description.md contains empty template item on line {line_number}")
    for section_title in PR_REQUIRED_SECTIONS:
        body = markdown_section_body(text, section_title)
        if not body:
            issues.append(f"pr-description.md missing required section: {section_title}")
        elif not section_has_final_content(body):
            issues.append(f"pr-description.md has no final content in section: {section_title}")
    sections = workflow_plan_check.parse_sections(text)
    variance = workflow_plan_check.markdown_table_records(
        sections.get(workflow_plan_check.normalize_heading("Plan Variance"), "")
    )
    if not variance:
        issues.append("pr-description.md Plan Variance needs at least one filled row")
    reviews = workflow_plan_check.markdown_table_records(
        sections.get(workflow_plan_check.normalize_heading("Independent Review Evidence"), "")
    )
    expected_axes = {
        "spec and plan compliance",
        "standards and maintainability",
        "security and authority",
        "validation and generated artifacts",
    }
    review_by_axis = {
        workflow_plan_check.normalize_heading(record.get(workflow_plan_check.normalize_heading("Axis"), "")): record
        for _row_index, record in reviews
    }
    for axis in sorted(expected_axes):
        record = review_by_axis.get(axis)
        if record is None:
            issues.append(f"pr-description.md Independent Review Evidence is missing axis: {axis}")
            continue
        for field in ("Reviewer Or Method", "Result", "Evidence", "Disposition"):
            value = record.get(workflow_plan_check.normalize_heading(field), "")
            if not workflow_plan_check.value_is_filled(value, allow_skip=True):
                issues.append(f"pr-description.md Independent Review Evidence axis {axis} field {field} is empty or unresolved")
        result_issue = review_result_issue(record.get(workflow_plan_check.normalize_heading("Result"), ""))
        if result_issue:
            issues.append(f"pr-description.md Independent Review Evidence axis {axis} {result_issue}")
    reusable_body = markdown_section_body(text, REUSABLE_LESSON_SECTION)
    if not reusable_body:
        issues.append(f"pr-description.md missing required section: {REUSABLE_LESSON_SECTION}")
    elif not reusable_lesson_has_final_content(reusable_body):
        issues.append(
            "pr-description.md Reusable Lessons must include a reusable lesson or "
            "`No reusable lesson: <reason>`"
        )
    return [f"{common.relative(root, path)}: {issue}" for issue in issues]
