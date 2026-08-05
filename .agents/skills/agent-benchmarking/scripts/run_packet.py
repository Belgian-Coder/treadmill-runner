#!/usr/bin/env python3
"""Workflow-local run packet helpers for benchmark runs."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import benchmark_common as common

RUN_PACKET_FILENAME = "run.json"
CONFIDENCE_LABELS = {"high", "medium", "low", "unknown"}
CLAIM_CLASSIFICATIONS = {"source_truth", "generated_estimate", "review_judgment"}
URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_root_from_run_dir(run_dir: Path) -> Path:
    for candidate in [run_dir, *run_dir.parents]:
        if (candidate / ".agents").exists() or (candidate / ".git").exists():
            return candidate
    return run_dir


def resolve_source_path(run_dir: Path, source: str) -> Path | None:
    if not source.strip():
        return None
    raw_path = Path(source)
    candidates = [raw_path] if raw_path.is_absolute() else [
        run_dir / raw_path,
        repo_root_from_run_dir(run_dir) / raw_path,
        *(parent / raw_path for parent in run_dir.parents),
    ]
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved.exists() and resolved.is_file():
            return resolved
    return None


def normalize_source_type(raw: dict[str, Any]) -> str:
    source_type = str(raw.get("source_type") or "").strip().lower().replace("-", "_")
    source = str(raw.get("source") or "")
    if source_type in {"path", "local_path", "file"}:
        return "file"
    if source_type in {"url", "web"} or URL_PATTERN.match(source):
        return "url"
    if source_type == "command" or raw.get("command"):
        return "command"
    if source_type in {"generated", "estimate"}:
        return "generated"
    classification = str(raw.get("classification") or raw.get("claim_type") or "").strip().lower()
    if classification == "generated_estimate":
        return "generated"
    return source_type or "file"


def normalize_classification(raw: dict[str, Any], source_type: str) -> str:
    value = str(raw.get("classification") or raw.get("claim_type") or "").strip().lower().replace("-", "_")
    if value in CLAIM_CLASSIFICATIONS:
        return value
    if source_type == "generated":
        return "generated_estimate"
    if source_type in {"file", "url", "command"}:
        return "source_truth"
    return "review_judgment"


def normalize_confidence(value: Any) -> str:
    label = str(value or "").strip().lower()
    return label if label in CONFIDENCE_LABELS else "unknown"


def normalize_entry(raw: Any, run_dir: Path, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "id": f"evidence-{index:03d}",
            "claim": str(raw),
            "source": "",
            "source_type": "invalid",
            "command": "",
            "observed_result": "",
            "timestamp": "",
            "confidence": "unknown",
            "classification": "review_judgment",
            "status": "invalid_entry",
            "source_exists": False,
        }

    source_type = normalize_source_type(raw)
    classification = normalize_classification(raw, source_type)
    source = str(raw.get("source") or raw.get("path") or "").strip()
    command = str(raw.get("command") or "").strip()
    observed_result = str(raw.get("observed_result") or raw.get("result") or "").strip()
    entry: dict[str, Any] = {
        "id": str(raw.get("id") or f"evidence-{index:03d}"),
        "claim": str(raw.get("claim") or "").strip(),
        "source": source,
        "source_type": source_type,
        "command": command,
        "observed_result": observed_result,
        "timestamp": str(raw.get("timestamp") or ""),
        "confidence": normalize_confidence(raw.get("confidence")),
        "classification": classification,
        "status": "ok",
        "source_exists": None,
    }

    if not entry["claim"]:
        entry["status"] = "invalid_entry"
    elif source_type == "file":
        resolved = resolve_source_path(run_dir, source)
        entry["source_exists"] = resolved is not None
        if resolved is None:
            entry["status"] = "missing_source"
        else:
            current_hash = sha256_file(resolved)
            expected_hash = str(raw.get("source_sha256") or raw.get("expected_sha256") or "").strip()
            entry["source_path"] = str(resolved)
            entry["source_sha256"] = current_hash
            if expected_hash and expected_hash != current_hash:
                entry["status"] = "stale_source"
    elif source_type == "url":
        entry["source_exists"] = bool(URL_PATTERN.match(source))
        if not entry["source_exists"]:
            entry["status"] = "invalid_url"
    elif source_type == "command":
        entry["source_exists"] = bool(command and observed_result)
        if not entry["source_exists"]:
            entry["status"] = "invalid_command_evidence"
    elif source_type == "generated":
        entry["source_exists"] = None
        if not observed_result:
            entry["status"] = "generated_estimate_without_observation"
    else:
        entry["status"] = "unknown_source_type"
        entry["source_exists"] = False

    return entry


def build_run_packet(
    *,
    run_dir: Path,
    run_id: str,
    raw_entries: Any,
    unsupported_claims: list[str],
    workflow: str = "agent-benchmarking",
    current_phase: str = "record",
    status: str = "completed",
    next_action: str = "Review REPORT.md and compare benchmark-result.json when needed.",
    commands: list[Any] | None = None,
    checks: list[Any] | None = None,
    skipped: list[Any] | None = None,
    failed: list[Any] | None = None,
    decisions: list[str] | None = None,
    evidence_paths: list[str] | None = None,
) -> dict[str, Any]:
    if raw_entries is None:
        raw_entries = []
    if isinstance(raw_entries, dict):
        raw_entries = raw_entries.get("entries", [])
    if not isinstance(raw_entries, list):
        raw_entries = [{"claim": "Run packet input was not a list.", "source_type": "generated"}]
    entries = [normalize_entry(item, run_dir, index) for index, item in enumerate(raw_entries, start=1)]
    valid_entries = [item for item in entries if item.get("status") == "ok"]
    invalid_entries = [item for item in entries if item.get("status") != "ok"]
    supported_claims = len(valid_entries)
    unsupported_count = len(unsupported_claims) + len(invalid_entries)
    total_claims = supported_claims + unsupported_count
    coverage_percent = 100.0 if total_claims == 0 else round((supported_claims / total_claims) * 100, 2)
    commands = commands or []
    checks = checks or []
    skipped = skipped or []
    failed = failed or []
    decisions = decisions or []
    evidence_paths = evidence_paths or ["REPORT.md", "benchmark-result.json"]
    return {
        "schema_version": 2,
        "tool": "workflow-manager.run",
        "workflow": workflow,
        "ok": not invalid_entries,
        "status": status if not invalid_entries else "completed-with-findings",
        "run_id": run_id,
        "current_phase": current_phase,
        "decisions": decisions,
        "checks": {
            "skipped": skipped,
            "blocked": [],
            "failed": failed,
        },
        "commands": commands,
        "evidence": entries,
        "evidence_paths": evidence_paths,
        "skipped": skipped,
        "blocked": [],
        "failed": failed,
        "handoff": {
            "loaded_context": [
                "automations/routing.md",
                f"automations/{workflow}/WORKFLOW.md",
                f"automations/{workflow}/module.json",
            ],
            "required_next_context": [
                f"automations/{workflow}/runs/{run_id}/run.json",
                f"automations/{workflow}/runs/{run_id}/REPORT.md",
            ],
            "skipped_context": [],
            "blockers": [],
            "last_completed_step": current_phase,
            "last_command": str(commands[-1].get("command", "")) if commands and isinstance(commands[-1], dict) else "",
        },
        "next_action": next_action,
        "unsupported_claims": unsupported_claims,
        "coverage": {
            "supported_claims": supported_claims,
            "unsupported_claims": unsupported_count,
            "total_claims": total_claims,
            "coverage_percent": coverage_percent,
        },
        "warnings": [
            f"{item['id']} has status {item['status']}"
            for item in invalid_entries
        ],
    }


def validate_run_packet_file(path: Path, run_dir: Path | None = None) -> dict[str, Any]:
    data = common.read_json(path)
    if not isinstance(data, dict):
        raise SystemExit("run packet must be a JSON object.")
    entries = data.get("evidence")
    if not isinstance(entries, list):
        raise SystemExit("run packet must contain evidence as a list.")
    base = run_dir or path.parent
    rebuilt = build_run_packet(
        run_dir=base,
        run_id=str(data.get("run_id", "")),
        raw_entries=entries,
        unsupported_claims=[str(item) for item in data.get("unsupported_claims", []) if str(item).strip()],
        workflow=str(data.get("workflow", "agent-benchmarking")),
        current_phase=str(data.get("current_phase", "record")),
        status=str(data.get("status", "completed")),
    )
    return rebuilt
