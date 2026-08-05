"""Evidence and command summaries for workflow context packets."""

from __future__ import annotations

from pathlib import Path

import workflow_manager_common as common
from workflow_support.context_budget import relative_file_token_estimate
from workflow_support.context_markdown import (
    compact_markdown_snippet,
    first_markdown_section,
    keyed_bullets,
    list_items,
    markdown_section,
)
from workflow_support.context_paths import (
    normalize_path_handle,
    read_optional_text,
    unique_list,
)
from workflow_support.context_sources import source_file_paths


def collect_validation_files(root: Path, run_dir: Path) -> list[dict[str, object]]:
    validation_dir = run_dir / "validation"
    if not validation_dir.exists():
        return []
    rows: list[dict[str, object]] = []
    for path in sorted(validation_dir.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_file():
            rows.append(relative_file_token_estimate(root, path))
    return rows


def collect_evidence_handles(root: Path, run_dir: Path, run_packet: dict[str, object]) -> list[str]:
    values: list[str] = []
    for key in ("evidence_paths", "files_changed", "changed_files"):
        items = run_packet.get(key)
        if isinstance(items, list):
            values.extend(normalize_path_handle(root, run_dir, item) for item in items)
    evidence = run_packet.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict):
                values.extend(
                    normalize_path_handle(root, run_dir, item.get(key))
                    for key in ("path", "source", "evidence_path")
                    if item.get(key)
                )
    commands = run_packet.get("commands")
    if isinstance(commands, list):
        for item in commands:
            if isinstance(item, dict) and item.get("evidence_path"):
                values.append(normalize_path_handle(root, run_dir, item.get("evidence_path")))
    validation_dir = run_dir / "validation"
    if validation_dir.exists():
        values.extend(common.relative(root, path) for path in sorted(validation_dir.rglob("*")) if path.is_file())
    return unique_list([value for value in values if value])


def cap_evidence_handles(handles: list[str], *, limit: int | None = None) -> list[str]:
    limit = limit or common.project_policy_int("limits.workflow.context_packet_evidence_handle_limit")
    if len(handles) <= limit:
        return handles
    required: list[str] = []
    for handle in handles:
        if "/validation/" in handle:
            required.append(handle)
    selected = unique_list(required + handles)[: max(1, limit - 1)]
    omitted = len(handles) - len(selected)
    if omitted > 0:
        selected.append(f"... {omitted} evidence handle(s) omitted; see run.json")
    return selected


def extract_scope(
    ticket_info: str,
    run_packet: dict[str, object],
    *,
    missing_scope_default: str = "",
) -> dict[str, object]:
    section = markdown_section(ticket_info, "Scope")
    explicit_out_of_scope = first_markdown_section(ticket_info, ("Out Of Scope", "Out of Scope", "Out-of-scope"))
    keyed = keyed_bullets(section)
    in_scope = keyed.get("in scope", "")
    out_of_scope = keyed.get("out of scope", "")
    assumptions = keyed.get("assumptions", "")
    out_items = list_items(explicit_out_of_scope)
    if out_of_scope:
        out_items.insert(0, out_of_scope)
    if missing_scope_default and not ticket_info:
        out_items.append(missing_scope_default)
    return {
        "in_scope": [in_scope] if in_scope else [],
        "out_of_scope": unique_list([item for item in out_items if item]),
        "assumptions": [assumptions] if assumptions else [],
        "ticket_scope_recorded": bool(section or explicit_out_of_scope),
        "run_status": str(run_packet.get("status", "unknown")),
    }


def _first_source_text(
    root: Path,
    context_sources: list[dict[str, object]],
    roles: tuple[str, ...],
) -> str:
    for role in roles:
        for path in source_file_paths(context_sources, artifact_role=role):
            text = read_optional_text(root / path)
            if text:
                return text
    return ""


def build_work_item_summary(
    root: Path,
    context_sources: list[dict[str, object]],
) -> dict[str, object]:
    roles = {str(source.get("artifact_role", "")) for source in context_sources}
    ticket = _first_source_text(
        root,
        context_sources,
        ("bug-ticket", "user-story", "ticket-info"),
    )
    plan = _first_source_text(
        root,
        context_sources,
        ("implementation-plan", "implementation-plan-template"),
    )
    execution = _first_source_text(
        root,
        context_sources,
        ("execution-log", "execution-log-template"),
    )
    pr = _first_source_text(
        root,
        context_sources,
        ("pr-handoff", "pr-handoff-template"),
    )
    if "bug-ticket" in roles:
        return {
            "type": "bug",
            "observed_behavior": compact_markdown_snippet(markdown_section(ticket, "Observed Behavior"), limit_chars=500),
            "expected_behavior": compact_markdown_snippet(markdown_section(ticket, "Expected Behavior"), limit_chars=500),
            "reproduction": compact_markdown_snippet(
                first_markdown_section(ticket, ("Reproduction", "Reproduction / Evidence")),
                limit_chars=700,
            ),
            "regression_proof": compact_markdown_snippet(
                first_markdown_section(plan + "\n" + pr, ("Regression-Proof Plan", "Validation")),
                limit_chars=700,
            ),
            "execution_evidence": compact_markdown_snippet(
                markdown_section(execution, "Context And Claim Support"),
                limit_chars=500,
            ),
        }
    if "user-story" in roles:
        return {
        "type": "story",
        "acceptance_criteria": list_items(markdown_section(ticket, "Acceptance Criteria"), limit=20),
        "acceptance_mapping": compact_markdown_snippet(
            markdown_section(plan, "Acceptance Criteria Mapping"),
            limit_chars=1200,
        ),
        "execution_evidence": compact_markdown_snippet(
            markdown_section(execution, "Context And Claim Support"),
            limit_chars=500,
        ),
        }
    return {
        "type": "workflow",
        "execution_evidence": compact_markdown_snippet(
            markdown_section(execution, "Context And Claim Support"),
            limit_chars=500,
        ),
    }


def command_status_ok(item: dict[str, object]) -> bool:
    explicit = item.get("ok")
    if isinstance(explicit, bool):
        return explicit
    status = str(item.get("status", "")).strip().lower()
    return status in {"ok", "passed", "success", "completed"}


def compact_command_text(
    command: object,
    *,
    root: Path | None = None,
    run_dir: Path | None = None,
    limit: int | None = None,
) -> str:
    limit = limit or common.project_policy_int("limits.workflow.context_packet_command_chars", start=root)
    text = str(command or "").replace("\\", "/").strip()
    replacements: list[tuple[str, str]] = []
    if root:
        replacements.append((str(root).replace("\\", "/"), "<repo>"))
    if root and run_dir:
        replacements.append((str(run_dir).replace("\\", "/"), common.relative(root, run_dir)))
    for source, target in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        text = text.replace(source, target)
    if len(text) <= limit:
        return text
    return text[: max(20, limit - 20)].rstrip() + " ... [truncated]"


def summarize_commands(
    run_packet: dict[str, object],
    *,
    root: Path | None = None,
    run_dir: Path | None = None,
    limit: int | None = None,
) -> list[dict[str, object]]:
    limit = limit or common.project_policy_int("limits.workflow.context_packet_command_limit", start=root)
    commands = run_packet.get("commands")
    if not isinstance(commands, list):
        return []
    rows: list[dict[str, object]] = []
    for item in commands:
        if isinstance(item, dict):
            rows.append(
                {
                    "command": compact_command_text(item.get("command", ""), root=root, run_dir=run_dir),
                    "ok": command_status_ok(item),
                    "status": item.get("status", ""),
                    "returncode": item.get("returncode", ""),
                    "elapsed_seconds": item.get("elapsed_seconds", ""),
                    "evidence_path": item.get("evidence_path", ""),
                }
            )
    if len(rows) <= limit:
        return rows
    attention_rows = [row for row in rows if not row["ok"]]
    selected: list[dict[str, object]] = []
    for row in [*attention_rows, *rows[-limit:]]:
        if row not in selected:
            selected.append(row)
        if len(selected) >= max(1, limit - 1):
            break
    omitted = len(rows) - len(selected)
    if omitted > 0:
        selected.append(
            {
                "command": f"... {omitted} command(s) omitted; see run.json",
                "ok": True,
                "status": "omitted",
                "returncode": "",
                "elapsed_seconds": "",
                "evidence_path": "",
            }
        )
    return selected


def failed_checks_are_unresolved(run_packet: dict[str, object]) -> bool:
    failed = run_packet.get("failed")
    if not isinstance(failed, list) or not failed:
        return False
    status = str(run_packet.get("status", "")).strip().lower()
    workflow_status = str(run_packet.get("workflow_status", "")).strip().lower()
    external_status = str(run_packet.get("external_validation_status", "")).strip().lower()
    completed_with_findings = {
        "completed-with-findings",
        "passed-with-findings",
        "complete-with-findings",
    }
    if status in completed_with_findings or workflow_status in completed_with_findings:
        return False
    return "with-existing-warnings" not in external_status
