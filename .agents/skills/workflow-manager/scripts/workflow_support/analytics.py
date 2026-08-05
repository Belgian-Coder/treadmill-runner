"""Retained workflow run analytics."""

from __future__ import annotations

import json
import datetime as dt
from pathlib import Path

import workflow_manager_common as common
from workflow_support.run_common import lesson_candidates


def accepted_workflow_names(root: Path) -> list[str]:
    return [
        path.parent.name
        for path in sorted((root / "automations").glob("*/module.json"), key=lambda item: item.as_posix())
    ]


def run_packets(root: Path, workflow_name: str) -> list[tuple[Path, dict[str, object]]]:
    runs_dir = root / "automations" / workflow_name / "runs"
    if not runs_dir.exists():
        return []
    packets: list[tuple[Path, dict[str, object]]] = []
    for run_dir in sorted(runs_dir.iterdir(), key=lambda item: item.name.lower()):
        if not run_dir.is_dir():
            continue
        data, _error = common.read_json_file(run_dir / "run.json")
        if isinstance(data, dict):
            packets.append((run_dir, data))
    return packets


def list_count(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def classify_friction(packet: dict[str, object], kind: str, item: object) -> str:
    text = json.dumps(item, sort_keys=True).lower() if isinstance(item, (dict, list)) else str(item).lower()
    status = str(packet.get("status") or "").lower()
    if "historical" in text or "stale" in text or status == "completed-with-findings":
        return "stale-historical"
    if "not applicable" in text or "offline" in text or "outside offline smoke" in text:
        return "expected-skip"
    if any(
        phrase in text
        for phrase in (
            "local model/runtime bundle is not installed",
            "not installed in this checkout",
            "without downloads",
            "would require llm",
            "model/api cost",
        )
    ):
        return "expected-skip"
    if "fixture" in text:
        return "needs-fixture"
    if "doc" in text or "documentation" in text:
        return "needs-docs"
    if kind in {"failed", "blocked", "unsupported"}:
        return "needs-investigation"
    return "review"


def friction_summary(item: object) -> str:
    if isinstance(item, dict):
        for key in ("summary", "reason", "claim", "command", "check"):
            value = item.get(key)
            if value:
                return str(value)[:180]
        return json.dumps(item, sort_keys=True)[:180]
    return str(item)[:180]


def friction_action(classification: str) -> str:
    actions = {
        "stale-historical": "Confirm the retained run is still useful or reduce it into active docs, fixtures, or suites.",
        "expected-skip": "Keep the skip reason unless policy changes.",
        "needs-fixture": "Add or refresh fixture coverage for this retained-run gap.",
        "needs-docs": "Reduce the decision into the owning docs or template.",
        "needs-investigation": "Review the retained run packet and either fix evidence or reclassify the finding.",
        "review": "Review and classify this retained-run item.",
    }
    return actions.get(classification, actions["review"])


def grouped_friction_backlog(items: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for item in items:
        classification = str(item.get("classification") or "review")
        grouped.setdefault(classification, []).append(item)
    return [
        {
            "classification": classification,
            "count": len(values),
            "items": values[:20],
        }
        for classification, values in sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0]))
    ]


def run_age_days(path: Path) -> int:
    try:
        modified = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)
    except OSError:
        return 0
    now = dt.datetime.now(dt.timezone.utc)
    return max(0, int((now - modified).total_seconds() // 86400))


def workflow_row(root: Path, workflow_name: str) -> dict[str, object]:
    packets = run_packets(root, workflow_name)
    status_counts: dict[str, int] = {}
    skipped_count = 0
    failed_count = 0
    blocked_count = 0
    unsupported_count = 0
    lesson_values: list[str] = []
    lesson_queue: list[dict[str, object]] = []
    missing_proof_classes: dict[str, int] = {}
    friction_triage: dict[str, int] = {}
    friction_items: list[dict[str, object]] = []
    for run_dir, packet in packets:
        status = str(packet.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        skipped_count += list_count(packet.get("skipped"))
        failed_count += list_count(packet.get("failed"))
        blocked_count += list_count(packet.get("blocked"))
        unsupported_count += list_count(packet.get("unsupported_claims"))
        for kind, values in (
            ("skipped", packet.get("skipped")),
            ("failed", packet.get("failed")),
            ("blocked", packet.get("blocked")),
            ("unsupported", packet.get("unsupported_claims")),
        ):
            for value in values if isinstance(values, list) else []:
                classification = classify_friction(packet, kind, value)
                friction_triage[classification] = friction_triage.get(classification, 0) + 1
                friction_items.append(
                    {
                        "workflow": workflow_name,
                        "run_id": run_dir.name,
                        "kind": kind,
                        "classification": classification,
                        "summary": friction_summary(value),
                        "source": common.relative(root, run_dir / "run.json"),
                        "age_days": run_age_days(run_dir / "run.json"),
                        "recommended_action": friction_action(classification),
                    }
                )
        for lesson in lesson_candidates(root, run_dir, packet):
            lesson_values.append(lesson)
            lesson_queue.append(
                {
                    "workflow": workflow_name,
                    "run_id": run_dir.name,
                    "lesson": lesson,
                    "source": common.relative(root, run_dir / "run.json"),
                }
            )
        for issue in packet.get("missing_proof", []) if isinstance(packet.get("missing_proof"), list) else []:
            if isinstance(issue, dict):
                key = str(issue.get("section") or issue.get("field") or "unknown")
            else:
                key = str(issue)
            missing_proof_classes[key] = missing_proof_classes.get(key, 0) + 1
    return {
        "workflow": workflow_name,
        "run_count": len(packets),
        "status_counts": status_counts,
        "skipped_check_count": skipped_count,
        "failed_check_count": failed_count,
        "blocked_check_count": blocked_count,
        "unsupported_claim_count": unsupported_count,
        "missing_proof_classes": missing_proof_classes,
        "friction_triage": friction_triage,
        "friction_backlog": grouped_friction_backlog(friction_items),
        "lesson_candidates": list(dict.fromkeys(lesson_values)),
        "lesson_queue": lesson_queue,
    }


def workflow_analytics(root: Path, workflow_names: list[str] | None = None) -> dict[str, object]:
    names = workflow_names or accepted_workflow_names(root)
    rows = [workflow_row(root, name) for name in names]
    summary = {
        "workflow_count": len(rows),
        "run_count": sum(int(row.get("run_count", 0)) for row in rows),
        "skipped_check_count": sum(int(row.get("skipped_check_count", 0)) for row in rows),
        "failed_check_count": sum(int(row.get("failed_check_count", 0)) for row in rows),
        "blocked_check_count": sum(int(row.get("blocked_check_count", 0)) for row in rows),
        "unsupported_claim_count": sum(int(row.get("unsupported_claim_count", 0)) for row in rows),
        "lesson_candidate_count": sum(len(row.get("lesson_queue", [])) for row in rows if isinstance(row.get("lesson_queue"), list)),
    }
    triage_totals: dict[str, int] = {}
    lesson_queue: list[dict[str, object]] = []
    friction_items: list[dict[str, object]] = []
    for row in rows:
        triage = row.get("friction_triage") if isinstance(row.get("friction_triage"), dict) else {}
        for key, value in triage.items():
            triage_totals[str(key)] = triage_totals.get(str(key), 0) + int(value)
        if isinstance(row.get("lesson_queue"), list):
            lesson_queue.extend(item for item in row["lesson_queue"] if isinstance(item, dict))
        for group in row.get("friction_backlog", []) if isinstance(row.get("friction_backlog"), list) else []:
            if not isinstance(group, dict):
                continue
            for item in group.get("items", []) if isinstance(group.get("items"), list) else []:
                if isinstance(item, dict):
                    friction_items.append(item)
    summary["friction_triage"] = triage_totals
    summary["friction_backlog_count"] = len(friction_items)
    return {
        "schema_version": 1,
        "tool": "workflow-manager.analytics",
        "ok": True,
        "status": "ok",
        "summary": summary,
        "workflows": rows,
        "lesson_queue": lesson_queue,
        "friction_backlog": grouped_friction_backlog(friction_items),
        "next_command": "python -B .agents/manage.py workflow doctor --all --summary --compact --format json",
    }


def compact_analytics(report: dict[str, object]) -> dict[str, object]:
    compact = dict(report)
    rows = []
    raw_rows = report.get("workflows") if isinstance(report.get("workflows"), list) else []
    for row in raw_rows:
        if not isinstance(row, dict) or int(row.get("run_count", 0)) <= 0:
            continue
        rows.append(
            {
                "workflow": row.get("workflow", ""),
                "run_count": row.get("run_count", 0),
                "status_counts": row.get("status_counts", {}),
                "skipped_check_count": row.get("skipped_check_count", 0),
                "failed_check_count": row.get("failed_check_count", 0),
                "blocked_check_count": row.get("blocked_check_count", 0),
                "unsupported_claim_count": row.get("unsupported_claim_count", 0),
                "friction_triage": row.get("friction_triage", {}),
                "missing_proof_classes": row.get("missing_proof_classes", {}),
            }
        )
    compact["workflows"] = rows
    if not rows:
        compact.pop("workflows", None)
    lesson_queue = compact.get("lesson_queue") if isinstance(compact.get("lesson_queue"), list) else []
    if not lesson_queue:
        compact.pop("lesson_queue", None)
    friction_backlog = compact.get("friction_backlog") if isinstance(compact.get("friction_backlog"), list) else []
    if not friction_backlog:
        compact.pop("friction_backlog", None)
    return compact


def render_analytics(report: dict[str, object]) -> str:
    lines = ["# Workflow Run Analytics", ""]
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines.append(f"- Workflows: {summary.get('workflow_count', 0)}")
    lines.append(f"- Retained runs: {summary.get('run_count', 0)}")
    lines.append(f"- Failed checks: {summary.get('failed_check_count', 0)}")
    lines.append(f"- Unsupported claims: {summary.get('unsupported_claim_count', 0)}")
    lines.append(f"- Lesson candidates: {summary.get('lesson_candidate_count', 0)}")
    lines.append(f"- Friction backlog items: {summary.get('friction_backlog_count', 0)}")
    rows = report.get("workflows") if isinstance(report.get("workflows"), list) else []
    if rows:
        lines.extend(["", "## Workflows", ""])
        for row in rows:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- `{row.get('workflow')}`: {row.get('run_count', 0)} runs, "
                f"{row.get('failed_check_count', 0)} failed checks, "
                f"{row.get('unsupported_claim_count', 0)} unsupported claims"
            )
    lines.append(f"- Next command: `{report.get('next_command')}`")
    return "\n".join(lines) + "\n"
