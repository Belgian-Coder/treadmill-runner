"""Workflow run lifecycle helpers kept out of the public command surface."""

from __future__ import annotations

import time
from pathlib import Path

import index_workflow_runs
import workflow_manager_common as common
from workflow_context_packet import context_packet_paths
from workflow_support.run_common import evidence_completeness, lesson_candidates
from workflow_support.context_sources import resolve_context_sources, source_file_paths
from workflow_support.start_checklist import unique_list
from workflow_support.workers import verify_persisted_runtime_observation, workflow_execution_profile

LIFECYCLE_EVIDENCE_KINDS = {
    "workflow-checkpoint",
    "workflow-context-packet",
    "workflow-hook",
    "workflow-context-evidence",
}
LIFECYCLE_EVIDENCE_PATH_PARTS = (
    "/artifacts/context/context-packet.",
    "/validation/checkpoints/",
    "/validation/hooks/",
    "/validation/context-evidence-",
)


def safe_run_id(value: str | None = None) -> str:
    raw = value or time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    normalized = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in raw.strip())
    normalized = normalized.strip("-_")
    if not normalized:
        raise SystemExit("run id must contain at least one letter or digit")
    return normalized


TICKET_WORKFLOW_RUN_PREFIXES = {
    "user-story-workflow": "US-",
    "bug-ticket-workflow": "BUG-",
}


def canonical_workflow_run_id(
    workflow_name: str,
    value: str | None,
    *,
    require_ticket_identifier: bool = False,
) -> str | None:
    """Return the stable public run id used by workflow lifecycle commands."""

    prefix = TICKET_WORKFLOW_RUN_PREFIXES.get(workflow_name)
    if value is None:
        if prefix and require_ticket_identifier:
            raise SystemExit(
                f"{workflow_name} requires --run-id <identifier>; "
                f"the run folder must be named {prefix}<identifier>"
            )
        return None

    normalized = safe_run_id(value)
    if not prefix:
        return normalized

    upper = normalized.upper()
    if upper.startswith(prefix):
        identifier = normalized[len(prefix) :].strip("-_")
    else:
        opposite_prefix = "BUG-" if prefix == "US-" else "US-"
        if upper.startswith(opposite_prefix):
            raise SystemExit(
                f"{workflow_name} run id cannot start with {opposite_prefix}; "
                f"expected {prefix}<identifier>"
            )
        identifier = normalized
    if not identifier:
        raise SystemExit(f"{workflow_name} run id requires an identifier after {prefix}")
    return f"{prefix}{identifier}"


def resolve_repo_path(root: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise SystemExit("workflow path must stay inside the repository") from exc
    return resolved


def is_completion_evidence_entry(value: object) -> bool:
    if not isinstance(value, dict):
        return bool(value)
    if value.get("kind") in LIFECYCLE_EVIDENCE_KINDS:
        return False
    return bool(value.get("path") or value.get("summary") or value.get("command") or value.get("status"))


def is_completion_evidence_path(value: object) -> bool:
    text = str(value or "").replace("\\", "/")
    if not text:
        return False
    return not any(part in text for part in LIFECYCLE_EVIDENCE_PATH_PARTS)


def ticket_intake_context(root: Path, ticket_folder: str | None) -> dict[str, object] | None:
    if not ticket_folder:
        return None
    path = resolve_repo_path(root, ticket_folder)
    if not path.exists() or not path.is_dir():
        raise SystemExit(f"ticket intake folder not found: {common.relative(root, path)}")
    files = [
        item
        for item in sorted(path.rglob("*"))
        if item.is_file() and item.suffix.lower() in {".json", ".md", ".markdown", ".txt"}
    ][:30]
    attachments = [item for item in sorted(path.rglob("*")) if item.is_file() and "attachment" in item.parent.name.lower()]
    return {
        "path": common.relative(root, path),
        "evidence_files": [common.relative(root, item) for item in files],
        "attachment_count": len(attachments),
        "classification": "ticket-intake",
        "next_command": f"python -B .agents/manage.py attachment-route --file <attachment-path> --write-plan {common.relative(root, path)}/evidence-plan",
    }


def default_workflow_context(root: Path, workflow_name: str, run_dir: Path | None = None) -> list[str]:
    module_dir = root / "automations" / workflow_name
    manifest, _error = common.read_json_file(module_dir / "module.json")
    manifest = manifest if isinstance(manifest, dict) else {}
    source_run_dir = run_dir or module_dir / "runs" / "pending"
    context_sources, _issues = resolve_context_sources(
        root,
        workflow_name,
        source_run_dir,
        manifest.get("context"),
    )
    core_roles = {"workflow-entry", "module-contract", "instructions"}
    values = ["automations/routing.md"]
    for source in context_sources:
        if source.get("artifact_role") in core_roles or source.get("load_policy") == "must_open":
            values.extend(source_file_paths([source]))
    if run_dir is not None:
        context_json, _context_md = context_packet_paths(run_dir)
        if context_json.exists():
            values.append(common.relative(root, context_json))
    navigation_handoff = root / "automations" / "navigation" / "artifacts" / "maps" / "HANDOFF.md"
    if navigation_handoff.exists():
        values.append(common.relative(root, navigation_handoff))
    return values


def workflow_handoff_packet(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    run_packet: dict[str, object],
    ledger: dict[str, object] | None = None,
) -> dict[str, object]:
    handoff = run_packet.get("handoff") if isinstance(run_packet.get("handoff"), dict) else {}
    required_next_context = handoff.get("required_next_context")
    if not isinstance(required_next_context, list):
        required_next_context = default_workflow_context(root, workflow_name, run_dir)
    context_json, _context_markdown = context_packet_paths(run_dir)
    if context_json.exists():
        required_next_context = [
            common.relative(root, context_json),
            *[str(item) for item in required_next_context],
        ]
    loaded_context = handoff.get("loaded_context")
    if not isinstance(loaded_context, list):
        loaded_context = []
    skipped_context = handoff.get("skipped_context")
    if not isinstance(skipped_context, list):
        skipped_context = []
    blockers = handoff.get("blockers")
    if not isinstance(blockers, list):
        blockers = []
    decisions = run_packet.get("decisions")
    if not isinstance(decisions, list):
        decisions = []
    failed = run_packet.get("failed")
    if not isinstance(failed, list):
        failed = []
    evidence = run_packet.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
    unsupported_claims = run_packet.get("unsupported_claims")
    if not isinstance(unsupported_claims, list):
        unsupported_claims = []
    current_phase = str(run_packet.get("current_phase") or "")
    phase = run_packet.get("phase") if isinstance(run_packet.get("phase"), dict) else {}
    manifest, _manifest_error = common.read_json_file(root / "automations" / workflow_name / "module.json")
    if not isinstance(manifest, dict):
        manifest = {}
    persisted_observation = (
        run_packet.get("runtime_observation")
        if isinstance(run_packet.get("runtime_observation"), dict)
        else None
    )
    runtime_observation, runtime_observation_verification_issues = verify_persisted_runtime_observation(
        root,
        workflow_name,
        run_dir.name,
        current_phase,
        persisted_observation,
    )
    execution_profile = workflow_execution_profile(
        manifest,
        current_phase,
        runtime_observation=runtime_observation,
        runtime_observation_verification_issues=runtime_observation_verification_issues,
        workflow=workflow_name,
        run_id=run_dir.name,
    )
    lessons = lesson_candidates(root, run_dir, run_packet)
    return {
        "schema_version": 2,
        "tool": "workflow-manager.handoff-run",
        "workflow": workflow_name,
        "run_id": run_packet.get("run_id") or run_dir.name,
        "run_path": common.relative(root, run_dir),
        "current_phase": current_phase,
        "phase_status": phase.get("status", run_packet.get("status", "unknown")),
        "execution_profile": execution_profile,
        "phase_lifecycle": {
            "phase_started_at": phase.get("started_at", ""),
            "phase_completed_at": phase.get("completed_at", ""),
            "phase_entry_checks": phase.get("entry_checks", []),
            "phase_exit_checks": phase.get("exit_checks", []),
            "phase_decisions": decisions,
            "phase_blockers": blockers,
            "phase_evidence": run_packet.get("evidence_paths", []),
        },
        "last_completed_step": handoff.get("last_completed_step", ""),
        "next_action": run_packet.get("next_action", ""),
        "last_command": handoff.get("last_command", ""),
        "blockers": blockers,
        "loaded_context": loaded_context,
        "required_next_context": unique_list([str(item) for item in required_next_context]),
        "skipped_context": skipped_context,
        "decisions": decisions,
        "reasoning_notes": run_packet.get("reasoning_notes", []),
        "things_that_went_wrong": failed,
        "external_validation_status": run_packet.get("external_validation_status", "not-recorded"),
        "evidence_ledger_path": common.relative(root, run_dir / "run.json"),
        "evidence_count": len(evidence),
        "unsupported_claim_count": len(unsupported_claims),
        "evidence_completeness": evidence_completeness(root, workflow_name, run_dir, run_packet),
        "lesson_candidates": lessons,
        "new_chat_prompt": (
            f"Resume `{workflow_name}` run `{run_dir.name}`. Start with "
            f"`{common.relative(root, context_json) if context_json.exists() else common.relative(root, run_dir / 'run.json')}`, then load only the files "
            "listed under required_next_context before continuing. Apply the execution_profile instruction header, "
            "prompt_overlay delivery_directive, and only effective surface_adapter modes for the current phase. "
            "Available or blocked optimizations do not grant delegation, authority, or weaker validation."
        ),
        "next_command": f"python -B .agents/manage.py workflow resume --name {workflow_name} --run-id {run_dir.name}",
    }


def normalized_run_state(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    state: dict[str, object],
    ledger: dict[str, object] | None = None,
) -> dict[str, object]:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    checks = state.get("checks") if isinstance(state.get("checks"), dict) else {}
    handoff = state.get("handoff") if isinstance(state.get("handoff"), dict) else {}
    normalized = dict(state)
    normalized.setdefault("schema_version", 2)
    normalized.setdefault("tool", "workflow-manager.run")
    normalized.setdefault("workflow", workflow_name)
    normalized.setdefault("run_id", run_dir.name)
    normalized.setdefault("status", "partial")
    normalized.setdefault("created_at", normalized.get("updated_at", now))
    normalized.setdefault("updated_at", now)
    normalized.setdefault("current_phase", "")
    normalized.setdefault("next_action", "")
    normalized.setdefault("decisions", [])
    normalized.setdefault("commands", [])
    normalized.setdefault("evidence", [])
    normalized.setdefault("evidence_paths", [])
    normalized.setdefault("unsupported_claims", [])
    normalized.setdefault("skipped", checks.get("skipped", []))
    normalized.setdefault("blocked", checks.get("blocked", []))
    normalized.setdefault("failed", checks.get("failed", []))
    normalized["checks"] = {
        "skipped": normalized.get("skipped", []),
        "blocked": normalized.get("blocked", []),
        "failed": normalized.get("failed", []),
    }
    normalized.setdefault(
        "phase",
        {
            "current": normalized.get("current_phase", ""),
            "status": normalized.get("status", "unknown"),
            "started_at": normalized.get("created_at", now),
            "completed_at": "",
            "entry_checks": [],
            "exit_checks": [],
        },
    )
    normalized["handoff"] = {
        "loaded_context": handoff.get("loaded_context", default_workflow_context(root, workflow_name)),
        "required_next_context": handoff.get(
            "required_next_context",
            default_workflow_context(root, workflow_name, run_dir),
        ),
        "skipped_context": handoff.get("skipped_context", []),
        "blockers": handoff.get("blockers", normalized.get("blocked", [])),
        "last_completed_step": handoff.get("last_completed_step", ""),
        "last_command": handoff.get("last_command", ""),
    }
    normalized.setdefault("reasoning_notes", [])
    normalized.setdefault("external_validation_status", "not-recorded")
    return normalized


def normalized_ledger(workflow_name: str, run_dir: Path, ledger: dict[str, object]) -> dict[str, object]:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    normalized = dict(ledger)
    normalized.setdefault("schema_version", 2)
    normalized.setdefault("workflow", workflow_name)
    normalized.setdefault("run_id", run_dir.name)
    normalized.setdefault("status", "partial")
    normalized.setdefault("created_at", normalized.get("updated_at", now))
    normalized.setdefault("updated_at", now)
    normalized.setdefault("commands", [])
    normalized.setdefault("evidence", [])
    normalized.setdefault("files_changed", [])
    normalized.setdefault("skipped", [])
    normalized.setdefault("blocked", [])
    normalized.setdefault("decisions", [])
    normalized.setdefault("reasoning_notes", [])
    normalized.setdefault("things_that_went_wrong", [])
    normalized.setdefault("unsupported_claims", [])
    normalized.setdefault("external_validation_status", "not-recorded")
    normalized.setdefault("phase_events", [])
    return normalized


def latest_or_selected_run_dir(root: Path, workflow_name: str, run_id: str | None = None) -> Path:
    module_dir = root / "automations" / workflow_name
    runs_dir = module_dir / "runs"
    if not runs_dir.exists():
        raise SystemExit(f"workflow has no runs folder: automations/{workflow_name}/runs")
    if run_id:
        selected = runs_dir / safe_run_id(run_id)
        if not selected.exists():
            raise SystemExit(f"workflow run not found: {common.relative(root, selected)}")
        return selected
    candidates = [path for path in runs_dir.iterdir() if path.is_dir()]
    if not candidates:
        raise SystemExit(f"workflow has no run folders: automations/{workflow_name}/runs")
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)[0]


def read_json_object(path: Path) -> dict[str, object]:
    data, _error = common.read_json_file(path)
    return data if isinstance(data, dict) else {}


def comparable_context_packet(packet: dict[str, object]) -> dict[str, object]:
    comparable = dict(packet)
    comparable.pop("written", None)
    comparable.pop("quality_gate", None)
    return comparable


def phase_has_blockers(run_packet: dict[str, object]) -> bool:
    for key in ("blocked", "failed"):
        values = run_packet.get(key)
        if isinstance(values, list) and values:
            return True
    checks = run_packet.get("checks") if isinstance(run_packet.get("checks"), dict) else {}
    for key in ("blocked", "failed"):
        values = checks.get(key)
        if isinstance(values, list) and values:
            return True
    handoff = run_packet.get("handoff") if isinstance(run_packet.get("handoff"), dict) else {}
    values = handoff.get("blockers")
    return isinstance(values, list) and bool(values)


def refresh_run_index(root: Path, workflow_name: str) -> dict[str, object]:
    index_report = index_workflow_runs.build_index(root, workflow_name)
    runs_path = str(index_report.get("runs_path", ""))
    runs_dir = root / runs_path
    if not runs_dir.exists():
        return {
            "ok": True,
            "status": "skipped",
            "reason": "runs folder not present",
            "paths": [],
        }
    index_workflow_runs.write_outputs(root, index_report)
    return {
        "ok": True,
        "status": "written",
        "paths": [
            common.relative(root, runs_dir / "INDEX.md"),
            common.relative(root, runs_dir / "index.json"),
        ],
        "summary": index_report.get("summary", {}),
    }
