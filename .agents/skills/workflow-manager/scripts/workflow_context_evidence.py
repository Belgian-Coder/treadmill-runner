#!/usr/bin/env python3
"""Required deterministic context-evidence packets for workflow runs."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import workflow_manager_common as common

CONTEXT_EVIDENCE_EVENTS = {"start", "resume", "finish"}
CONTEXT_EVIDENCE_PACKET_SCHEMA_VERSION = 1
QUERY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
TEXT_SUFFIXES = {".cs", ".json", ".md", ".py", ".props", ".sln", ".toml", ".targets", ".txt", ".xml", ".yaml", ".yml"}
MAX_CANDIDATE_FILES = 128
MAX_SCAN_BYTES = 2_000_000
MAX_BYTES_PER_FILE = 80_000
STOP_WORDS = {
    "about",
    "after",
    "and",
    "are",
    "before",
    "current",
    "does",
    "evidence",
    "from",
    "have",
    "into",
    "must",
    "next",
    "that",
    "the",
    "this",
    "what",
    "when",
    "where",
    "which",
    "with",
    "workflow",
}


def default_context_evidence_queries(workflow_name: str) -> dict[str, list[dict[str, object]]]:
    workflow_prefix = f"automations/{workflow_name}"
    return {
        "start": [
            {
                "id": "workflow-contract",
                "question": (
                    f"What instructions, phases, validation gates, evidence files, and approval rules "
                    f"define the {workflow_name} workflow?"
                ),
                "scope": "repo",
                "required": True,
                "fallback_paths": [
                    "automations/routing.md",
                    f"{workflow_prefix}/WORKFLOW.md",
                    f"{workflow_prefix}/module.json",
                    f"{workflow_prefix}/instructions.md",
                    "docs/workflow/workflow-quickstart.md",
                ],
            },
            {
                "id": "project-context",
                "question": (
                    "What project context, technologies, commands, folder structure, validation rules, "
                    "generated-file boundaries, and external system facts should planning use?"
                ),
                "scope": "repo",
                "required": True,
                "fallback_paths": [
                    "docs/project/project-context.md",
                    "docs/agent-start.md",
                    "AGENTS.md",
                ],
            },
        ],
        "resume": [
            {
                "id": "run-state",
                "question": (
                    f"What is the current {workflow_name} run state, latest report, blockers, "
                    "validation evidence, context packet, and next action?"
                ),
                "scope": "workflow-runs",
                "required": True,
                "fallback_paths": [
                    f"{workflow_prefix}/runs/<run-id>/run.json",
                    f"{workflow_prefix}/runs/<run-id>/REPORT.md",
                    f"{workflow_prefix}/runs/<run-id>/execution-log.md",
                    f"{workflow_prefix}/runs/<run-id>/artifacts/context/context-packet.json",
                ],
            }
        ],
        "finish": [
            {
                "id": "finish-evidence",
                "question": (
                    f"What evidence, validation status, skipped checks, blockers, unsupported claims, "
                    f"and handoff files must be complete before finishing {workflow_name}?"
                ),
                "scope": "repo",
                "required": True,
                "fallback_paths": [
                    f"{workflow_prefix}/WORKFLOW.md",
                    f"{workflow_prefix}/module.json",
                    f"{workflow_prefix}/instructions.md",
                    f"{workflow_prefix}/runs/<run-id>/run.json",
                    f"{workflow_prefix}/runs/<run-id>/REPORT.md",
                    "docs/workflow/quality-evidence-packets.md",
                ],
            }
        ],
    }


def read_workflow_manifest(root: Path, workflow_name: str) -> dict[str, Any]:
    data, error = common.read_json_file(root / "automations" / workflow_name / "module.json")
    if error or not isinstance(data, dict):
        return {}
    return data


def context_evidence_config(root: Path, manifest: dict[str, Any], workflow_name: str) -> dict[str, Any]:
    raw = manifest.get("context_evidence")
    defaults = default_context_evidence_queries(workflow_name)
    if not isinstance(raw, dict):
        return {
            "required": False,
            "start_queries": [],
            "resume_queries": [],
            "finish_queries": [],
        }
    config: dict[str, Any] = {
        "required": bool(raw.get("required", False)),
    }
    for event in sorted(CONTEXT_EVIDENCE_EVENTS):
        key = f"{event}_queries"
        values = raw.get(key)
        config[key] = values if isinstance(values, list) and values else defaults[event]
    return config


def workflow_requires_context_evidence(root: Path, workflow_name: str) -> bool:
    return bool(context_evidence_config(root, read_workflow_manifest(root, workflow_name), workflow_name).get("required"))


def queries_for_event(config: dict[str, Any], event: str) -> list[dict[str, Any]]:
    values = config.get(f"{event}_queries")
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, dict)]


def context_evidence_paths(run_dir: Path, event: str) -> tuple[Path, Path]:
    base = run_dir / "validation"
    return base / f"context-evidence-{event}.json", base / f"context-evidence-{event}.md"


def now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def unique_list(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def normalize_run_path(root: Path, run_dir: Path, value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    text = text.replace("<run-id>", run_dir.name)
    path = Path(text)
    if not path.is_absolute():
        candidate = root / path
        if candidate.exists():
            return common.relative(root, candidate)
        if text.startswith("runs/"):
            candidate = run_dir.parent.parent / text
            if candidate.exists():
                return common.relative(root, candidate)
        if text in {"run.json", "REPORT.md"} or text.startswith(("validation/", "artifacts/")):
            candidate = run_dir / text
            if candidate.exists():
                return common.relative(root, candidate)
    return text


def query_terms(question: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9][a-z0-9._-]{2,}", question.lower()):
        normalized = token.strip("._-")
        if normalized and normalized not in STOP_WORDS and normalized not in terms:
            terms.append(normalized)
    return terms


def candidate_paths(root: Path, workflow_name: str, run_dir: Path, query: dict[str, Any]) -> list[Path]:
    configured = [
        normalize_run_path(root, run_dir, item)
        for item in query.get("fallback_paths", [])
        if isinstance(item, str)
    ] if isinstance(query.get("fallback_paths"), list) else []
    common_paths = [
        "AGENTS.md",
        "docs/agent-start.md",
        "docs/project/project-context.md",
        "docs/workflow/workflow-quickstart.md",
        "docs/workflow/quality-evidence-packets.md",
        "automations/routing.md",
        f"automations/{workflow_name}/WORKFLOW.md",
        f"automations/{workflow_name}/module.json",
        f"automations/{workflow_name}/instructions.md",
        common.relative(root, run_dir / "run.json"),
        common.relative(root, run_dir / "REPORT.md"),
        common.relative(root, run_dir / "execution-log.md"),
        common.relative(root, run_dir / "artifacts" / "context" / "context-packet.json"),
    ]
    values = unique_list(configured if configured else common_paths)
    paths: list[Path] = []
    for value in values:
        path = Path(value)
        if not path.is_absolute():
            path = root / path
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        if resolved.exists() and resolved.is_file():
            paths.append(resolved)

    scope = str(query.get("scope", "repo"))
    if scope in {"workflow-runs", "all"} and run_dir.exists():
        stop = False
        for directory, child_directories, filenames in os.walk(run_dir):
            child_directories.sort()
            for filename in sorted(filenames):
                path = Path(directory) / filename
                if path.suffix.lower() in TEXT_SUFFIXES and path.is_file():
                    paths.append(path)
                if len(paths) >= MAX_CANDIDATE_FILES:
                    stop = True
                    break
            if stop:
                break
    return list(dict.fromkeys(paths))


def fallback_evidence(root: Path, workflow_name: str, run_dir: Path, query: dict[str, Any], *, top_k: int) -> dict[str, Any]:
    question = str(query.get("question", ""))
    terms = query_terms(question)
    ranked: list[tuple[int, Path, str]] = []
    candidates = candidate_paths(root, workflow_name, run_dir, query)
    configured_paths: dict[str, int] = {}
    raw_fallback_paths = query.get("fallback_paths")
    if isinstance(raw_fallback_paths, list):
        for index, value in enumerate(raw_fallback_paths):
            if not isinstance(value, str):
                continue
            normalized = normalize_run_path(root, run_dir, value)
            path = Path(normalized)
            if not path.is_absolute():
                path = root / path
            resolved = path.resolve(strict=False)
            if resolved.exists() and resolved.is_file():
                configured_paths[common.relative(root, resolved)] = index
    scanned_bytes = 0
    scanned_file_count = 0
    scan_truncated = len(candidates) >= MAX_CANDIDATE_FILES
    for path in candidates:
        remaining_bytes = MAX_SCAN_BYTES - scanned_bytes
        if remaining_bytes <= 0:
            scan_truncated = True
            break
        text = common.read_text(path, limit=min(MAX_BYTES_PER_FILE, remaining_bytes))
        encoded = text.encode("utf-8")
        if len(encoded) > remaining_bytes:
            encoded = encoded[:remaining_bytes]
            text = encoded.decode("utf-8", errors="ignore")
            scan_truncated = True
        scanned_bytes += len(encoded)
        scanned_file_count += 1
        haystack = f"{common.relative(root, path)}\n{text}".lower()
        score = sum(haystack.count(term) for term in terms)
        path_score = sum(3 for term in terms if term in common.relative(root, path).lower())
        total = score + path_score
        relative_path = common.relative(root, path)
        if total > 0 or not terms or relative_path in configured_paths:
            ranked.append((total, path, common.compact_snippet(text, limit=240)))
    if not ranked:
        return {
            "method": "deterministic-file-scan",
            "ok": False,
            "evidence_paths": [],
            "reason": "no fallback file matched the query terms",
            "scan": {
                "candidate_file_count": len(candidates),
                "file_limit": MAX_CANDIDATE_FILES,
                "scanned_file_count": scanned_file_count,
                "byte_limit": MAX_SCAN_BYTES,
                "scanned_bytes": scanned_bytes,
                "truncated": scan_truncated,
            },
        }
    ranked.sort(
        key=lambda item: (
            0 if common.relative(root, item[1]) in configured_paths else 1,
            configured_paths.get(common.relative(root, item[1]), 0),
            -item[0],
            common.relative(root, item[1]),
        )
    )
    rows = [
        {
            "path": common.relative(root, path),
            "score": score,
            "excerpt": snippet,
        }
        for score, path, snippet in ranked[:top_k]
    ]
    return {
        "method": "deterministic-file-scan",
        "ok": True,
        "evidence_paths": [str(row["path"]) for row in rows],
        "evidence": rows,
        "scan": {
            "candidate_file_count": len(candidates),
            "file_limit": MAX_CANDIDATE_FILES,
            "scanned_file_count": scanned_file_count,
            "byte_limit": MAX_SCAN_BYTES,
            "scanned_bytes": scanned_bytes,
            "truncated": scan_truncated,
        },
    }


def result_paths(result: dict[str, Any], *, limit: int | None = None) -> list[str]:
    paths = [str(item).strip() for item in result.get("evidence_paths", []) if str(item).strip()]
    return paths[:limit] if limit is not None else paths


def deterministic_quality_metrics(fallback: dict[str, Any], *, top_k: int) -> dict[str, Any]:
    fallback_paths = result_paths(fallback, limit=top_k)
    return {
        "deterministic_top_path": fallback_paths[0] if fallback_paths else "",
        "evidence_available": bool(fallback.get("ok")),
        "bounded_path_count": len(fallback_paths),
    }


def evaluate_query(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    query: dict[str, Any],
) -> dict[str, Any]:
    query_id = str(query.get("id", "")).strip()
    question = str(query.get("question", "")).strip()
    default_top_k = common.project_policy_int("owner_defaults.workflow_manager.context_evidence.top_k", start=root)
    top_k = int(query.get("top_k", default_top_k) or default_top_k)
    fallback = fallback_evidence(root, workflow_name, run_dir, query, top_k=top_k)
    quality = deterministic_quality_metrics(fallback, top_k=top_k)
    if fallback.get("ok") is True:
        return {
            "id": query_id,
            "question": question,
            "required": bool(query.get("required", True)),
            "status": "complete",
            "ok": True,
            "scope": str(query.get("scope", "repo")),
            "retrieval_mode": "deterministic-file-scan",
            "confidence": 1.0,
            "evidence_paths": fallback.get("evidence_paths", []),
            "scan": fallback,
            "quality": quality,
        }
    return {
        "id": query_id,
        "question": question,
        "required": bool(query.get("required", True)),
        "status": "blocked" if bool(query.get("required", True)) else "skipped",
        "ok": not bool(query.get("required", True)),
        "scope": str(query.get("scope", "repo")),
        "retrieval_mode": "",
        "confidence": 0.0,
        "evidence_paths": [],
        "scan": fallback,
        "quality": quality,
    }


def changed_file_relevance(root: Path, paths: list[str], queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in paths:
        path = root / value
        text = common.read_text(path, limit=80_000) if path.exists() else ""
        haystack = f"{value}\n{text}".lower()
        matched_query_ids: list[str] = []
        term_hits = 0
        for query in queries:
            query_id = str(query.get("id", "")).strip()
            hits = sum(haystack.count(term) for term in query_terms(str(query.get("question", ""))))
            if hits > 0:
                term_hits += hits
                matched_query_ids.append(query_id)
        rows.append(
            {
                "path": value,
                "status": "related" if matched_query_ids else "uncertain",
                "matched_query_ids": unique_list(matched_query_ids),
                "term_hits": term_hits,
            }
        )
    return rows


def changed_file_refresh(root: Path, run_packet: dict[str, Any], queries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    paths: list[str] = []
    for key in ("changed_files", "files_changed"):
        values = run_packet.get(key)
        if isinstance(values, list):
            paths.extend(str(item).strip() for item in values if str(item).strip())
    paths = unique_list(paths)
    existing_paths = []
    for value in paths:
        path = Path(value)
        if not path.is_absolute():
            path = root / path
        try:
            path.resolve(strict=False).relative_to(root.resolve())
        except ValueError:
            continue
        if path.exists():
            existing_paths.append(common.relative(root, path))
    if not existing_paths:
        return {
            "status": "skipped",
            "ok": True,
            "reason": "no changed files recorded in run.json",
            "paths": [],
            "relevance": [],
        }
    relevance = changed_file_relevance(root, existing_paths, queries or [])
    return {
        "status": "complete",
        "ok": True,
        "reason": "changed files were evaluated directly; no repository index refresh is required",
        "paths": existing_paths,
        "relevance": relevance,
    }


def summarize_context_evidence_quality(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "query_count": len(results),
        "complete_count": sum(1 for item in results if item.get("status") == "complete"),
        "blocked_count": sum(1 for item in results if item.get("status") == "blocked"),
        "bounded_path_query_count": sum(
            1
            for item in results
            if isinstance(item.get("quality"), dict) and int(item["quality"].get("bounded_path_count", 0) or 0) > 0
        ),
    }


def build_context_evidence_packet(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    run_packet: dict[str, Any],
    *,
    event: str,
) -> dict[str, Any]:
    if event not in CONTEXT_EVIDENCE_EVENTS:
        raise SystemExit(f"unknown context-evidence event: {event}")
    manifest = read_workflow_manifest(root, workflow_name)
    config = context_evidence_config(root, manifest, workflow_name)
    queries = queries_for_event(config, event)
    results = [
        evaluate_query(root, workflow_name, run_dir, query)
        for query in queries
    ]
    required_blocked = [item for item in results if item.get("required") is True and item.get("ok") is not True]
    if required_blocked:
        status = "blocked"
    elif not bool(config.get("required")) and not results:
        status = "skipped"
    else:
        status = "complete"
    evidence_paths = unique_list(
        [
            str(path)
            for result in results
            for path in result.get("evidence_paths", [])
            if str(path).strip()
        ]
    )
    refresh = changed_file_refresh(root, run_packet, queries) if event == "finish" else {"status": "skipped", "ok": True}
    packet = {
        "schema_version": CONTEXT_EVIDENCE_PACKET_SCHEMA_VERSION,
        "tool": "workflow-manager.context-evidence",
        "ok": not required_blocked,
        "status": status,
        "workflow": workflow_name,
        "run_id": run_dir.name,
        "run_path": common.relative(root, run_dir),
        "event": event,
        "required": bool(config.get("required")),
        "generated_at": now_utc(),
        "queries": results,
        "quality": summarize_context_evidence_quality(results),
        "required_query_ids": [str(item.get("id")) for item in queries if bool(item.get("required", True))],
        "evidence_paths": evidence_paths,
        "changed_file_refresh": refresh,
        "issues": [
            f"required context-evidence query blocked: {item.get('id')}"
            for item in required_blocked
        ],
        "next_command": f"python -B .agents/manage.py workflow context-evidence --name {workflow_name} --run-id {run_dir.name} --event {event} --write",
    }
    return packet


def render_context_evidence_markdown(packet: dict[str, Any]) -> str:
    lines = ["# Workflow Context Evidence", ""]
    lines.append(f"- Workflow: `{packet.get('workflow')}`")
    lines.append(f"- Run: `{packet.get('run_id')}`")
    lines.append(f"- Event: `{packet.get('event')}`")
    lines.append(f"- Status: {packet.get('status')}")
    lines.append(f"- Required: {packet.get('required')}")
    quality = packet.get("quality") if isinstance(packet.get("quality"), dict) else {}
    if quality:
        lines.append(
            "- Quality: "
            f"{quality.get('bounded_path_query_count', 0)}/{quality.get('query_count', 0)} bounded queries"
        )
    queries = packet.get("queries") if isinstance(packet.get("queries"), list) else []
    if queries:
        lines.extend(["", "## Queries", ""])
        for item in queries:
            if not isinstance(item, dict):
                continue
            paths = item.get("evidence_paths") if isinstance(item.get("evidence_paths"), list) else []
            lines.append(
                f"- `{item.get('id')}`: {item.get('status')} "
                f"({item.get('retrieval_mode') or 'none'}, confidence {item.get('confidence')})"
            )
            for path in paths[:5]:
                lines.append(f"  - `{path}`")
    refresh = packet.get("changed_file_refresh") if isinstance(packet.get("changed_file_refresh"), dict) else {}
    if refresh and refresh.get("status") != "skipped":
        lines.extend(["", "## Changed File Refresh", ""])
        lines.append(f"- Status: {refresh.get('status')}")
        if refresh.get("reason"):
            lines.append(f"- Reason: {refresh.get('reason')}")
        for path in refresh.get("paths", []) if isinstance(refresh.get("paths"), list) else []:
            lines.append(f"- `{path}`")
        relevance = refresh.get("relevance") if isinstance(refresh.get("relevance"), list) else []
        if relevance:
            lines.extend(["", "### Changed File Relevance", ""])
            lines.append("| Path | Status | Query IDs | Term Hits |")
            lines.append("|---|---|---|---:|")
            for item in relevance:
                if not isinstance(item, dict):
                    continue
                query_ids = ", ".join(str(value) for value in item.get("matched_query_ids", []) if str(value))
                lines.append(
                    f"| `{item.get('path')}` | {item.get('status')} | {query_ids or 'none'} | {item.get('term_hits', 0)} |"
                )
    issues = packet.get("issues") if isinstance(packet.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {item}" for item in issues)
    lines.extend(["", f"Next command: `{packet.get('next_command')}`", ""])
    return "\n".join(lines)


def merge_context_evidence_into_run_packet(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    run_packet: dict[str, Any],
    packet: dict[str, Any],
    *,
    json_path: Path,
    markdown_path: Path,
) -> None:
    event = str(packet.get("event", ""))
    json_rel = common.relative(root, json_path)
    markdown_rel = common.relative(root, markdown_path)
    context_evidence = (
        run_packet.get("context_evidence")
        if isinstance(run_packet.get("context_evidence"), dict)
        else {}
    )
    context_evidence[event] = {
        "status": packet.get("status", ""),
        "ok": packet.get("ok", False),
        "packet": json_rel,
        "markdown": markdown_rel,
        "query_ids": [str(item.get("id", "")) for item in packet.get("queries", []) if isinstance(item, dict)],
        "updated_at": packet.get("generated_at", ""),
    }
    run_packet["context_evidence"] = context_evidence
    evidence_paths = run_packet.get("evidence_paths") if isinstance(run_packet.get("evidence_paths"), list) else []
    run_packet["evidence_paths"] = unique_list([*[str(item) for item in evidence_paths], json_rel, markdown_rel])
    evidence = run_packet.get("evidence") if isinstance(run_packet.get("evidence"), list) else []
    evidence = [
        item
        for item in evidence
        if not (isinstance(item, dict) and item.get("kind") == "workflow-context-evidence" and item.get("event") == event)
    ]
    evidence.append(
        {
            "kind": "workflow-context-evidence",
            "event": event,
            "status": packet.get("status", ""),
            "path": json_rel,
            "summary": "Workflow context evidence complete" if packet.get("status") == "complete" else f"Workflow context evidence {packet.get('status')}",
        }
    )
    run_packet["evidence"] = evidence
    handoff = run_packet.get("handoff") if isinstance(run_packet.get("handoff"), dict) else {}
    required_context = handoff.get("required_next_context") if isinstance(handoff.get("required_next_context"), list) else []
    handoff["required_next_context"] = unique_list([json_rel, *[str(item) for item in required_context]])
    run_packet["handoff"] = handoff
    commands = run_packet.get("commands") if isinstance(run_packet.get("commands"), list) else []
    command = f"python -B .agents/manage.py workflow context-evidence --name {workflow_name} --run-id {run_dir.name} --event {event} --write"
    commands = [
        item
        for item in commands
        if not (isinstance(item, dict) and item.get("kind") == "workflow-context-evidence" and item.get("event") == event)
    ]
    commands.append(
        {
            "kind": "workflow-context-evidence",
            "event": event,
            "command": command,
            "status": packet.get("status", ""),
            "ok": packet.get("ok", False),
            "evidence_path": json_rel,
        }
    )
    run_packet["commands"] = commands
    failure_label = f"required context evidence {event} is blocked"
    checks = run_packet.get("checks") if isinstance(run_packet.get("checks"), dict) else {}
    failed = checks.get("failed") if isinstance(checks.get("failed"), list) else []
    failed = [item for item in failed if item != failure_label]
    if packet.get("ok") is not True:
        failed.append(failure_label)
    checks["failed"] = failed
    checks.setdefault("blocked", [])
    checks.setdefault("skipped", [])
    run_packet["checks"] = checks
    flat_failed = run_packet.get("failed") if isinstance(run_packet.get("failed"), list) else []
    flat_failed = [item for item in flat_failed if item != failure_label]
    if packet.get("ok") is not True:
        flat_failed.append(failure_label)
    run_packet["failed"] = flat_failed
    run_packet["updated_at"] = now_utc()


def write_context_evidence_packet(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    run_packet: dict[str, Any],
    *,
    event: str,
    write: bool = False,
    write_run: bool = False,
) -> dict[str, Any]:
    packet = build_context_evidence_packet(root, workflow_name, run_dir, run_packet, event=event)
    json_path, markdown_path = context_evidence_paths(run_dir, event)
    if write:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        markdown_path.write_text(render_context_evidence_markdown(packet), encoding="utf-8", newline="\n")
        merge_context_evidence_into_run_packet(root, workflow_name, run_dir, run_packet, packet, json_path=json_path, markdown_path=markdown_path)
        if write_run:
            (run_dir / "run.json").write_text(
                json.dumps(run_packet, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        packet["written"] = [common.relative(root, json_path), common.relative(root, markdown_path)]
    else:
        packet["would_write"] = [common.relative(root, json_path), common.relative(root, markdown_path)]
    return packet


def validate_context_evidence_packet(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    *,
    event: str,
) -> list[str]:
    if not workflow_requires_context_evidence(root, workflow_name):
        return []
    json_path, markdown_path = context_evidence_paths(run_dir, event)
    data, error = common.read_json_file(json_path)
    if error or not isinstance(data, dict):
        return [f"context evidence packet is missing or invalid: {common.relative(root, json_path)}"]
    issues: list[str] = []
    if data.get("tool") != "workflow-manager.context-evidence":
        issues.append(f"context evidence packet has unexpected tool: {common.relative(root, json_path)}")
    if data.get("workflow") != workflow_name or data.get("run_id") != run_dir.name:
        issues.append(f"context evidence packet does not match workflow/run: {common.relative(root, json_path)}")
    if data.get("event") != event:
        issues.append(f"context evidence packet event is not {event}: {common.relative(root, json_path)}")
    if data.get("ok") is not True:
        issues.append(f"context evidence packet is blocked: {common.relative(root, json_path)}")
    if data.get("status") != "complete":
        issues.append(f"context evidence packet status is not finishable: {data.get('status')}")
    queries = data.get("queries") if isinstance(data.get("queries"), list) else []
    required_ids = set(str(item) for item in data.get("required_query_ids", []) if str(item).strip())
    seen_ids: set[str] = set()
    for item in queries:
        if not isinstance(item, dict):
            continue
        query_id = str(item.get("id", ""))
        seen_ids.add(query_id)
        if item.get("required") is True and item.get("status") != "complete":
            issues.append(f"required context-evidence query {query_id} is {item.get('status')}")
        evidence_paths = item.get("evidence_paths") if isinstance(item.get("evidence_paths"), list) else []
        if item.get("required") is True and not evidence_paths:
            issues.append(f"required context-evidence query {query_id} has no evidence paths")
    missing = sorted(required_ids - seen_ids)
    if missing:
        issues.append(f"context evidence packet missing required query ids: {', '.join(missing)}")
    if not markdown_path.exists():
        issues.append(f"context evidence Markdown is missing: {common.relative(root, markdown_path)}")
    return issues


def render_packet(packet: dict[str, Any]) -> str:
    return render_context_evidence_markdown(packet)
