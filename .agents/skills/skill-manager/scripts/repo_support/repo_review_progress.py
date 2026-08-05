#!/usr/bin/env python3
"""Review plan, progress, and cost-report helpers for changed-file review packets."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_support import repo_cost_policy
from repo_support import repo_review_hunks

DEFAULT_REVIEW_PROGRESS_PATH = ".agents/local-ai/cache/review-progress.json"
DEFAULT_BUDGET_TREND_PATH = ".agents/local-ai/cache/budget-trend-ledger.jsonl"
DEFAULT_REVIEW_LOOP_MAX_UNITS = 20
DEFAULT_REVIEW_LOOP_MAX_ESTIMATED_TOKENS = 8000
DEFAULT_REVIEW_LOOP_MAX_ELAPSED_MS = 180000
DEFAULT_REVIEW_BATCH_MAX_HUNKS = 12


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _review_command(value: object) -> str:
    text = str(value or "")
    return text if "review-packet" in text else ""


def default_review_loop_command(
    *,
    max_units: int = DEFAULT_REVIEW_LOOP_MAX_UNITS,
    max_estimated_tokens: int = DEFAULT_REVIEW_LOOP_MAX_ESTIMATED_TOKENS,
    max_elapsed_ms: int = DEFAULT_REVIEW_LOOP_MAX_ELAPSED_MS,
    include_validation: bool = False,
) -> str:
    command = (
        "python -B .agents/manage.py review-loop "
        f"--max-units {max_units} "
        f"--max-estimated-tokens {max_estimated_tokens} "
        f"--max-elapsed-ms {max_elapsed_ms} "
    )
    if include_validation:
        command += "--include-validation "
    return command + "--summary --compact --format json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _unit_id(scope: str, owner: str, path: str = "", hunk: str = "") -> str:
    parts = [f"scope:{scope}", f"owner:{owner or 'repo'}"]
    if path:
        parts.append(f"path:{path}")
    if hunk:
        parts.append(f"hunk:{hunk}")
    return "|".join(parts)


def _review_unit(
    *,
    scope: str,
    owner: str,
    packet: dict[str, Any],
    command: str,
    path: str = "",
    hunk: str = "",
    parent_id: str = "",
) -> dict[str, Any]:
    read_first = [row for row in _list(packet.get("read_first")) if isinstance(row, dict)]
    return {
        "id": _unit_id(scope, owner, path, hunk),
        "parent_id": parent_id,
        "status": "pending",
        "scope": scope,
        "owner": owner,
        "path": path,
        "hunk": hunk,
        "range": str(packet.get("range", "")),
        "priority": str(packet.get("priority", "")),
        "estimated_changed_tokens": int(packet.get("estimated_changed_tokens", 0) or 0),
        "risk_counts": _dict(packet.get("risk_counts")),
        "read_first": read_first[:4],
        "command": command,
        "next_command_after_unit": str(packet.get("next_command", "")),
        "validation_first": [str(item) for item in _list(packet.get("validation_first"))[:4]],
    }


def _int_value(value: object, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _merge_risk_counts(packets: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for packet in packets:
        risks = packet.get("risk_counts") if isinstance(packet.get("risk_counts"), dict) else {}
        for risk, count in risks.items():
            counts[str(risk)] = counts.get(str(risk), 0) + _int_value(count)
    return dict(sorted(counts.items()))


def _merge_validation_first(packets: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for packet in packets:
        for command in _list(packet.get("validation_first")):
            text = str(command)
            if text and text not in values:
                values.append(text)
            if len(values) >= 4:
                return values
    return values


def _hunk_batch_unit(
    *,
    owner: str,
    path: str,
    packets: list[dict[str, Any]],
    parent_id: str,
) -> dict[str, Any]:
    if len(packets) == 1:
        packet = packets[0]
        hunk = str(packet.get("hunk", ""))
        command = _review_command(packet.get("next_command"))
        return _review_unit(
            scope="hunk",
            owner=owner,
            path=path,
            hunk=hunk,
            packet=packet,
            command=command,
            parent_id=parent_id,
        )
    hunk_ids = [str(packet.get("hunk", "")) for packet in packets if packet.get("hunk")]
    command = repo_review_hunks.owner_review_command(owner, [path], hunk_ids)
    risk_rank = {"high": 0, "medium": 1, "low": 2}
    priority = min(
        (str(packet.get("priority") or "medium") for packet in packets),
        key=lambda value: risk_rank.get(value, 9),
        default="medium",
    )
    read_first: list[dict[str, Any]] = []
    ranges: list[str] = []
    for packet in packets:
        read_first.extend(row for row in _list(packet.get("read_first")) if isinstance(row, dict))
        if packet.get("range"):
            ranges.append(str(packet.get("range")))
    merged_packet = {
        "range": ", ".join(ranges),
        "priority": priority,
        "estimated_changed_tokens": sum(_int_value(packet.get("estimated_changed_tokens")) for packet in packets),
        "risk_counts": _merge_risk_counts(packets),
        "read_first": read_first,
        "next_command": command,
        "validation_first": _merge_validation_first(packets),
    }
    unit = _review_unit(
        scope="hunk-batch",
        owner=owner,
        path=path,
        hunk=",".join(hunk_ids),
        packet=merged_packet,
        command=command,
        parent_id=parent_id,
    )
    unit["hunks"] = hunk_ids
    unit["source_review_unit_count"] = len(packets)
    return unit


def _packet_paths(packet: dict[str, Any]) -> list[str]:
    paths = [str(item) for item in _list(packet.get("paths")) if str(item).strip()]
    if not paths and packet.get("path"):
        paths = [str(packet.get("path"))]
    return list(dict.fromkeys(paths))


def _path_batch_unit(
    *,
    owner: str,
    packets: list[dict[str, Any]],
    parent_id: str,
) -> dict[str, Any]:
    if len(packets) == 1:
        packet = packets[0]
        path = str(packet.get("path", ""))
        command = _review_command(packet.get("path_summary_command")) or _review_command(packet.get("next_command"))
        return _review_unit(
            scope="path",
            owner=owner,
            path=path,
            packet=packet,
            command=command,
            parent_id=parent_id,
        )
    paths: list[str] = []
    for packet in packets:
        paths.extend(_packet_paths(packet))
    paths = list(dict.fromkeys(paths))
    command = repo_review_hunks.owner_review_command(owner, paths)
    risk_rank = {"high": 0, "medium": 1, "low": 2}
    priority = min(
        (str(packet.get("priority") or "medium") for packet in packets),
        key=lambda value: risk_rank.get(value, 9),
        default="medium",
    )
    read_first: list[dict[str, Any]] = []
    for packet in packets:
        read_first.extend(row for row in _list(packet.get("read_first")) if isinstance(row, dict))
    merged_packet = {
        "priority": priority,
        "estimated_changed_tokens": sum(_int_value(packet.get("estimated_changed_tokens")) for packet in packets),
        "risk_counts": _merge_risk_counts(packets),
        "read_first": read_first,
        "next_command": command,
        "validation_first": _merge_validation_first(packets),
    }
    unit = _review_unit(
        scope="path-batch",
        owner=owner,
        path=",".join(paths),
        packet=merged_packet,
        command=command,
        parent_id=parent_id,
    )
    unit["paths"] = paths
    unit["source_review_unit_count"] = len(packets)
    return unit


def _batched_hunk_units(
    *,
    owner: str,
    path: str,
    hunk_packets: list[dict[str, Any]],
    parent_id: str,
    review_budget: int,
    max_hunks: int = DEFAULT_REVIEW_BATCH_MAX_HUNKS,
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    batch: list[dict[str, Any]] = []
    batch_tokens = 0
    max_hunks = max(1, _int_value(max_hunks, DEFAULT_REVIEW_BATCH_MAX_HUNKS))

    def flush() -> None:
        nonlocal batch, batch_tokens
        if batch:
            units.append(_hunk_batch_unit(owner=owner, path=path, packets=batch, parent_id=parent_id))
        batch = []
        batch_tokens = 0

    for hunk_packet in hunk_packets:
        hunk_command = _review_command(hunk_packet.get("next_command"))
        if not hunk_command:
            flush()
            continue
        hunk_tokens = max(0, _int_value(hunk_packet.get("estimated_changed_tokens")))
        if not review_budget or hunk_tokens > review_budget:
            flush()
            units.append(_hunk_batch_unit(owner=owner, path=path, packets=[hunk_packet], parent_id=parent_id))
            continue
        if batch and (len(batch) >= max_hunks or batch_tokens + hunk_tokens > review_budget):
            flush()
        batch.append(hunk_packet)
        batch_tokens += hunk_tokens
    flush()
    return units


def _batched_path_units(
    *,
    owner: str,
    path_packets: list[dict[str, Any]],
    parent_id: str,
    review_budget: int,
    max_paths: int = DEFAULT_REVIEW_BATCH_MAX_HUNKS,
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    batch: list[dict[str, Any]] = []
    batch_tokens = 0
    max_paths = max(1, _int_value(max_paths, DEFAULT_REVIEW_BATCH_MAX_HUNKS))

    def flush() -> None:
        nonlocal batch, batch_tokens
        if batch:
            units.append(_path_batch_unit(owner=owner, packets=batch, parent_id=parent_id))
        batch = []
        batch_tokens = 0

    for path_packet in path_packets:
        path_command = _review_command(path_packet.get("path_summary_command")) or _review_command(path_packet.get("next_command"))
        if not path_command:
            flush()
            continue
        path_tokens = max(0, _int_value(path_packet.get("estimated_changed_tokens")))
        if not review_budget or path_tokens > review_budget:
            flush()
            units.append(_path_batch_unit(owner=owner, packets=[path_packet], parent_id=parent_id))
            continue
        if batch and (len(batch) >= max_paths or batch_tokens + path_tokens > review_budget):
            flush()
        batch.append(path_packet)
        batch_tokens += path_tokens
    flush()
    return units


def _review_batching_summary(
    review_units: list[dict[str, Any]],
    *,
    max_hunks: int = DEFAULT_REVIEW_BATCH_MAX_HUNKS,
) -> dict[str, Any]:
    source_count = sum(max(1, _int_value(unit.get("source_review_unit_count"), 1)) for unit in review_units)
    batch_units = [
        unit
        for unit in review_units
        if unit.get("scope") == "hunk-batch" and isinstance(unit.get("hunks"), list)
    ]
    path_batch_units = [
        unit
        for unit in review_units
        if unit.get("scope") == "path-batch" and isinstance(unit.get("paths"), list)
    ]
    batch_units = [*batch_units, *path_batch_units]
    saved_count = max(0, source_count - len(review_units))
    return {
        "status": "batched" if saved_count else "not-needed",
        "source_review_unit_count": source_count,
        "batched_review_unit_count": len(review_units),
        "saved_review_unit_count": saved_count,
        "hunk_batch_count": len([unit for unit in batch_units if unit.get("scope") == "hunk-batch"]),
        "path_batch_count": len(path_batch_units),
        "max_hunks_per_batch_limit": max(1, _int_value(max_hunks, DEFAULT_REVIEW_BATCH_MAX_HUNKS)),
        "max_hunks_per_batch": max(
            (len(unit.get("hunks", [])) for unit in batch_units if unit.get("scope") == "hunk-batch"),
            default=1,
        ),
        "max_paths_per_batch": max((len(unit.get("paths", [])) for unit in path_batch_units), default=1),
        "max_batch_estimated_tokens": max(
            (_int_value(unit.get("estimated_changed_tokens")) for unit in batch_units),
            default=0,
        ),
    }


def _units_for_owner(
    owner_packet: dict[str, Any],
    *,
    max_hunks: int = DEFAULT_REVIEW_BATCH_MAX_HUNKS,
) -> list[dict[str, Any]]:
    owner = str(owner_packet.get("owner", ""))
    owner_id = _unit_id("owner", owner)
    subpackets = [item for item in _list(owner_packet.get("owner_review_subpackets")) if isinstance(item, dict)]
    if not subpackets:
        command = _review_command(owner_packet.get("owner_summary_command")) or _review_command(owner_packet.get("next_command"))
        if not command:
            return []
        return [
            _review_unit(
                scope=str(owner_packet.get("scope", "owner")),
                owner=owner,
                packet=owner_packet,
                command=command,
                parent_id="",
            )
        ]
    units: list[dict[str, Any]] = []
    path_batch: list[dict[str, Any]] = []

    def flush_path_batch(*, parent_id: str, review_budget: int) -> None:
        nonlocal path_batch
        if path_batch:
            units.extend(
                _batched_path_units(
                    owner=owner,
                    path_packets=path_batch,
                    parent_id=parent_id,
                    review_budget=review_budget,
                    max_paths=max_hunks,
                )
            )
        path_batch = []

    for subpacket in subpackets:
        path = str(subpacket.get("path", ""))
        path_command = _review_command(subpacket.get("path_summary_command")) or _review_command(subpacket.get("next_command"))
        path_id = _unit_id("path", owner, path)
        hunk_packets = [item for item in _list(subpacket.get("path_review_hunks")) if isinstance(item, dict)]
        review_budget = _int_value(subpacket.get("review_budget_tokens"), _int_value(owner_packet.get("review_budget_tokens")))
        if path_command and not hunk_packets:
            path_batch.append(subpacket)
            continue
        flush_path_batch(parent_id=owner_id, review_budget=review_budget)
        if hunk_packets:
            units.extend(
                _batched_hunk_units(
                    owner=owner,
                    path=path,
                    hunk_packets=hunk_packets,
                    parent_id=path_id,
                    review_budget=review_budget,
                    max_hunks=max_hunks,
                )
            )
    flush_path_batch(parent_id=owner_id, review_budget=_int_value(owner_packet.get("review_budget_tokens")))
    return units


def _units_for_selected_packet(
    packet: dict[str, Any],
    *,
    max_hunks: int = DEFAULT_REVIEW_BATCH_MAX_HUNKS,
) -> list[dict[str, Any]]:
    owner = str(packet.get("owner", ""))
    units: list[dict[str, Any]] = []
    subpackets = [item for item in _list(packet.get("owner_review_subpackets")) if isinstance(item, dict)]
    if subpackets:
        return _units_for_owner(packet, max_hunks=max_hunks)
    path_hunks = [item for item in _list(packet.get("path_review_hunks")) if isinstance(item, dict)]
    if path_hunks:
        grouped_by_path: dict[str, list[dict[str, Any]]] = {}
        for hunk_packet in path_hunks:
            grouped_by_path.setdefault(str(hunk_packet.get("path", "")), []).append(hunk_packet)
        for path, hunk_packets in grouped_by_path.items():
            units.extend(
                _batched_hunk_units(
                    owner=owner,
                    path=path,
                    hunk_packets=hunk_packets,
                    parent_id=_unit_id("path", owner, path),
                    review_budget=_int_value(packet.get("review_budget_tokens")),
                    max_hunks=max_hunks,
                )
            )
    if units:
        return units
    command = _review_command(packet.get("owner_summary_command")) or _review_command(packet.get("next_command"))
    if not command:
        return []
    paths = [str(item) for item in _list(packet.get("paths")) if item]
    hunks = [str(item) for item in _list(packet.get("selected_hunks")) if item]
    return [
        _review_unit(
            scope=str(packet.get("scope", "owner")),
            owner=owner,
            path=paths[0] if len(paths) == 1 else "",
            hunk=hunks[0] if len(hunks) == 1 else "",
            packet=packet,
            command=command,
            parent_id="",
        )
    ]


def build_review_plan(packet: dict[str, Any]) -> dict[str, Any]:
    max_hunks = max(1, _int_value(packet.get("review_batch_max_hunks"), DEFAULT_REVIEW_BATCH_MAX_HUNKS))
    owner_packets = [item for item in _list(packet.get("owner_review_packets")) if isinstance(item, dict)]
    owner_groups = [
        {
            "owner": str(item.get("owner", "")),
            "status": str(item.get("status", "unknown")),
            "priority": str(item.get("priority", "")),
            "changed_file_count": int(item.get("changed_file_count", 0) or 0),
            "estimated_changed_tokens": int(item.get("estimated_changed_tokens", 0) or 0),
            "command": _review_command(item.get("owner_summary_command")) or _review_command(item.get("next_command")),
            "next_command": str(item.get("next_command", "")),
        }
        for item in owner_packets
    ]
    review_units: list[dict[str, Any]] = []
    if owner_packets:
        for owner_packet in owner_packets:
            scoped_owner_packet = dict(owner_packet)
            scoped_owner_packet.setdefault("review_budget_tokens", packet.get("review_budget_tokens", 0))
            review_units.extend(_units_for_owner(scoped_owner_packet, max_hunks=max_hunks))
    elif packet.get("tool") == "skill-manager.owner-review-packet":
        review_units.extend(_units_for_selected_packet(packet, max_hunks=max_hunks))
        owner_groups = [
            {
                "owner": str(packet.get("owner", "")),
                "status": str(packet.get("status", "unknown")),
                "priority": str(packet.get("priority", "")),
                "changed_file_count": int(packet.get("changed_file_count", 0) or 0),
                "estimated_changed_tokens": int(packet.get("estimated_changed_tokens", 0) or 0),
                "command": _review_command(packet.get("owner_summary_command")) or _review_command(packet.get("next_command")),
                "next_command": str(packet.get("next_command", "")),
            }
        ]
    validation_commands = list(
        dict.fromkeys(
            command
            for item in _list(packet.get("validation_first"))
            if (command := str(item or "").strip())
        )
    )
    validation_units = [
        {"id": f"validation:{index:03d}", "status": "pending", "scope": "validation", "command": str(command)}
        for index, command in enumerate(validation_commands, start=1)
    ]
    next_command = (review_units[0].get("command", "") if review_units else "") or _review_command(packet.get("next_command"))
    if not next_command and validation_units:
        next_command = str(validation_units[0].get("command", ""))
    review_batching = _review_batching_summary(review_units, max_hunks=max_hunks)
    cost_ledger = repo_cost_policy.compact_review_cost_ledger(_dict(packet.get("cost_ledger")))
    if cost_ledger:
        cost_ledger["source_review_unit_count"] = review_batching.get("source_review_unit_count", len(review_units))
        cost_ledger["batched_review_unit_count"] = review_batching.get("batched_review_unit_count", len(review_units))
        cost_ledger["saved_batched_review_unit_count"] = review_batching.get("saved_review_unit_count", 0)
        cost_ledger["max_hunks_per_batch_limit"] = review_batching.get("max_hunks_per_batch_limit", max_hunks)
        if review_units:
            batched_tokens = [_int_value(unit.get("estimated_changed_tokens")) for unit in review_units]
            raw_tokens = _int_value(cost_ledger.get("raw_changed_diff_estimated_tokens"))
            next_tokens = batched_tokens[0]
            total_tokens = sum(batched_tokens)
            cost_ledger["review_unit_count"] = len(review_units)
            cost_ledger["next_review_unit_estimated_tokens"] = next_tokens
            cost_ledger["largest_review_unit_estimated_tokens"] = max(batched_tokens, default=0)
            cost_ledger["review_units_estimated_tokens_total"] = total_tokens
            cost_ledger["next_review_unit_saved_tokens_vs_raw_estimated"] = max(0, raw_tokens - next_tokens)
            cost_ledger["next_review_unit_saved_percent_vs_raw_estimated"] = repo_cost_policy.percent_saved(
                raw_tokens,
                next_tokens,
            )
            cost_ledger["all_review_units_delta_tokens_vs_raw_estimated"] = total_tokens - raw_tokens
    return {
        "schema_version": 1,
        "tool": "skill-manager.review-plan",
        "status": "needs-review" if review_units else "ready-for-validation",
        "review_state": "initial",
        "progress_storage": (
            "repo-local ignored state via `python -B .agents/manage.py review-progress`; "
            "state is stale when the input fingerprint changes"
        ),
        "changed_file_count": int(packet.get("changed_file_count", 0) or 0),
        "owner_group_count": len(owner_groups),
        "review_unit_count": len(review_units),
        "review_batching": review_batching,
        "validation_unit_count": len(validation_units),
        "owner_groups": owner_groups,
        "review_units": review_units,
        "validation_units": validation_units,
        "cost_ledger": cost_ledger,
        "next_pending_command": next_command,
        "resume_rule": "Follow next_pending_command; after each unit, continue to the next review unit before broad raw diff review.",
    }


def summarize_review_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": plan.get("status", "unknown"),
        "review_state": plan.get("review_state", "unknown"),
        "owner_group_count": plan.get("owner_group_count", 0),
        "review_unit_count": plan.get("review_unit_count", 0),
        "review_batching": plan.get("review_batching", {}),
        "validation_unit_count": plan.get("validation_unit_count", 0),
        "next_pending_command": plan.get("next_pending_command", ""),
        "resume_rule": plan.get("resume_rule", ""),
    }


def _review_loop_unit_summary(unit: dict[str, Any]) -> dict[str, Any]:
    row = {
        "id": unit.get("id", ""),
        "scope": unit.get("scope", ""),
        "owner": unit.get("owner", ""),
        "path": unit.get("path", ""),
        "hunk": unit.get("hunk", ""),
        "estimated_changed_tokens": _int_value(unit.get("estimated_changed_tokens")),
        "command": unit.get("command", ""),
    }
    if isinstance(unit.get("hunks"), list):
        row["hunk_count"] = len(unit.get("hunks", []))
    if unit.get("source_review_unit_count"):
        row["source_review_unit_count"] = unit.get("source_review_unit_count")
    return row


def build_review_loop_forecast(
    plan: dict[str, Any],
    *,
    completed_unit_ids: list[str] | set[str] | tuple[str, ...] | None = None,
    max_units: int = DEFAULT_REVIEW_LOOP_MAX_UNITS,
    max_estimated_tokens: int = DEFAULT_REVIEW_LOOP_MAX_ESTIMATED_TOKENS,
    include_validation: bool = False,
) -> dict[str, Any]:
    completed = {str(item) for item in (completed_unit_ids or []) if str(item).strip()}
    max_units = max(1, _int_value(max_units, DEFAULT_REVIEW_LOOP_MAX_UNITS))
    max_estimated_tokens = max(0, _int_value(max_estimated_tokens, DEFAULT_REVIEW_LOOP_MAX_ESTIMATED_TOKENS))
    pending_review_units = [
        item
        for item in _list(plan.get("review_units"))
        if isinstance(item, dict) and str(item.get("id", "")) not in completed
    ]
    pending_validation_units = [
        item
        for item in _list(plan.get("validation_units"))
        if isinstance(item, dict) and str(item.get("id", "")) not in completed
    ]
    candidates = [*pending_review_units, *(pending_validation_units if include_validation else [])]
    planned: list[dict[str, Any]] = []
    planned_tokens = 0
    pending_review_tokens = sum(max(0, _int_value(item.get("estimated_changed_tokens"))) for item in pending_review_units)
    stop_reason = ""
    for unit in candidates:
        command = str(unit.get("command") or "")
        if not command or command.startswith("none"):
            stop_reason = "no-command"
            break
        command_is_review = "review-packet" in command
        if not command_is_review and not include_validation:
            stop_reason = "needs-validation"
            break
        unit_tokens = max(0, _int_value(unit.get("estimated_changed_tokens")))
        if command_is_review and max_estimated_tokens and planned_tokens + unit_tokens > max_estimated_tokens:
            stop_reason = "token-limit"
            break
        planned.append(_review_loop_unit_summary(unit))
        if command_is_review:
            planned_tokens += unit_tokens
        if len(planned) >= max_units:
            stop_reason = "unit-limit" if len(candidates) > len(planned) else ""
            break
    planned_review_units = sum(1 for item in planned if "review-packet" in str(item.get("command") or ""))
    remaining_after_planned = max(0, len(pending_review_units) - planned_review_units)
    projected_basis = planned_review_units or min(max_units, len(pending_review_units))
    projected_loop_count = 0
    token_projected_loop_count = 0
    if pending_review_units and projected_basis:
        projected_loop_count = (len(pending_review_units) + projected_basis - 1) // projected_basis
    if pending_review_units and max_estimated_tokens:
        token_projected_loop_count = (pending_review_tokens + max_estimated_tokens - 1) // max_estimated_tokens
        projected_loop_count = max(projected_loop_count, token_projected_loop_count)
    if not candidates:
        status = "complete"
    elif planned:
        status = "planned"
    elif stop_reason == "needs-validation":
        status = "needs-validation"
    elif stop_reason == "token-limit":
        status = "token-limit"
    else:
        status = "blocked"
    next_after = ""
    if remaining_after_planned:
        next_index = planned_review_units
        if next_index < len(pending_review_units):
            next_after = str(pending_review_units[next_index].get("command") or "")
    elif pending_validation_units:
        next_after = str(pending_validation_units[0].get("command") or "")
    return {
        "status": status,
        "planned_unit_count": len(planned),
        "planned_review_unit_count": planned_review_units,
        "planned_estimated_tokens": planned_tokens,
        "pending_review_tokens_estimated": pending_review_tokens,
        "max_units": max_units,
        "max_estimated_tokens": max_estimated_tokens,
        "remaining_review_units": len(pending_review_units),
        "remaining_after_planned_review_units": remaining_after_planned,
        "remaining_validation_units": len(pending_validation_units),
        "projected_loop_count": projected_loop_count,
        "token_projected_loop_count": token_projected_loop_count,
        "stop_reason": stop_reason,
        "next_command_after_planned": next_after,
        "planned_units": planned,
    }


def summarize_review_loop_forecast(forecast: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(forecast, dict) or not forecast:
        return {}
    return {
        "status": forecast.get("status", "unknown"),
        "planned_unit_count": forecast.get("planned_unit_count", 0),
        "planned_review_unit_count": forecast.get("planned_review_unit_count", 0),
        "planned_estimated_tokens": forecast.get("planned_estimated_tokens", 0),
        "pending_review_tokens_estimated": forecast.get("pending_review_tokens_estimated", 0),
        "max_units": forecast.get("max_units", 0),
        "max_estimated_tokens": forecast.get("max_estimated_tokens", 0),
        "remaining_review_units": forecast.get("remaining_review_units", 0),
        "remaining_after_planned_review_units": forecast.get("remaining_after_planned_review_units", 0),
        "remaining_validation_units": forecast.get("remaining_validation_units", 0),
        "projected_loop_count": forecast.get("projected_loop_count", 0),
        "token_projected_loop_count": forecast.get("token_projected_loop_count", 0),
        "stop_reason": forecast.get("stop_reason", ""),
    }


def review_loop_forecast_matches_limits(
    forecast: dict[str, Any],
    *,
    max_units: int,
    max_estimated_tokens: int,
) -> bool:
    if not isinstance(forecast, dict) or not forecast:
        return False
    planned_units = [item for item in _list(forecast.get("planned_units")) if isinstance(item, dict)]
    forecast_max_units = _int_value(forecast.get("max_units"))
    forecast_max_tokens = _int_value(forecast.get("max_estimated_tokens"))
    planned_count = _int_value(forecast.get("planned_unit_count"), len(planned_units))
    planned_tokens = _int_value(forecast.get("planned_estimated_tokens"))
    actual_planned_count = len(planned_units)
    actual_planned_tokens = sum(
        max(0, _int_value(item.get("estimated_changed_tokens")))
        for item in planned_units
        if "review-packet" in str(item.get("command") or "")
    )
    return (
        (not forecast_max_units or forecast_max_units == max_units)
        and (not max_estimated_tokens or not forecast_max_tokens or forecast_max_tokens == max_estimated_tokens)
        and planned_count <= max_units
        and actual_planned_count <= max_units
        and planned_count == actual_planned_count
        and (not max_estimated_tokens or planned_tokens <= max_estimated_tokens)
        and planned_tokens == actual_planned_tokens
        and (not max_estimated_tokens or actual_planned_tokens <= max_estimated_tokens)
    )


def completed_unit_ids_from_report(report: dict[str, Any]) -> list[str]:
    return [str(item) for item in _list(report.get("completed_units")) if str(item).strip()]


def build_review_owner_forecast(plan: dict[str, Any], forecast: dict[str, Any]) -> dict[str, Any]:
    owner_groups = [item for item in _list(plan.get("owner_groups")) if isinstance(item, dict)]
    top = max(
        owner_groups,
        key=lambda item: (_int_value(item.get("estimated_changed_tokens")), str(item.get("owner", ""))),
        default={},
    )
    planned_units = [item for item in _list(forecast.get("planned_units")) if isinstance(item, dict)]
    first = planned_units[0] if planned_units else {}
    return {
        "top_owner": top.get("owner", ""),
        "top_owner_changed_file_count": top.get("changed_file_count", 0),
        "top_owner_estimated_tokens": top.get("estimated_changed_tokens", 0),
        "pending_review_unit_count": forecast.get("remaining_review_units", 0),
        "planned_unit_count": forecast.get("planned_unit_count", 0),
        "planned_estimated_tokens": forecast.get("planned_estimated_tokens", 0),
        "remaining_after_planned_review_units": forecast.get("remaining_after_planned_review_units", 0),
        "projected_loop_count": forecast.get("projected_loop_count", 0),
        "first_review_command": first.get("command", "") or plan.get("next_pending_command", ""),
    }


def _all_units(plan: dict[str, Any]) -> list[dict[str, Any]]:
    review_units = [item for item in _list(plan.get("review_units")) if isinstance(item, dict)]
    validation_units = [item for item in _list(plan.get("validation_units")) if isinstance(item, dict)]
    return [*review_units, *validation_units]


def _fingerprint_digest(plan: dict[str, Any], input_fingerprint: dict[str, Any] | None = None) -> str:
    explicit = str((input_fingerprint or {}).get("digest") or "").strip()
    if explicit:
        return explicit
    digest = hashlib.sha256()
    for unit in _all_units(plan):
        digest.update(str(unit.get("id", "")).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(unit.get("command", "")).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def default_review_progress_path(root: Path) -> Path:
    return root / DEFAULT_REVIEW_PROGRESS_PATH


def default_budget_trend_path(root: Path) -> Path:
    return root / DEFAULT_BUDGET_TREND_PATH


def _safe_state_path(root: Path, value: str | None, default: Path) -> Path:
    candidate = Path(value).expanduser() if value else default
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise SystemExit("state path must stay inside the repository") from exc
    return resolved


def _load_progress_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"status": "unreadable"}
    return data if isinstance(data, dict) else {"status": "invalid"}


def _write_progress_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _matching_unit_ids(plan: dict[str, Any], *, unit_id: str = "", command: str = "") -> list[str]:
    if unit_id:
        return [unit_id]
    wanted = " ".join(str(command or "").split())
    if not wanted:
        return []
    exact: list[str] = []
    for unit in _all_units(plan):
        current = " ".join(str(unit.get("command") or "").split())
        if current == wanted:
            exact.append(str(unit.get("id") or ""))
    if exact:
        return [item for item in exact if item]
    fuzzy: list[str] = []
    for unit in _all_units(plan):
        current = " ".join(str(unit.get("command") or "").split())
        if wanted and (wanted in current or current in wanted):
            fuzzy.append(str(unit.get("id") or ""))
    return [item for item in fuzzy if item]


def _matching_unit_id(plan: dict[str, Any], *, unit_id: str = "", command: str = "") -> str:
    matches = _matching_unit_ids(plan, unit_id=unit_id, command=command)
    return matches[0] if len(matches) == 1 else ""


def build_review_progress(
    plan: dict[str, Any],
    *,
    input_fingerprint: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    state_path: str = DEFAULT_REVIEW_PROGRESS_PATH,
    mark_unit_id: str = "",
    mark_command: str = "",
    note: str = "",
    reset: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return progress applied to a plan plus optional state to persist."""
    state = state if isinstance(state, dict) else {}
    digest = _fingerprint_digest(plan, input_fingerprint)
    stored_digest = str(state.get("fingerprint_digest") or "")
    stale = bool(stored_digest and stored_digest != digest)
    completed: dict[str, Any] = {}
    signatures: dict[str, str] = {}
    for unit in _all_units(plan):
        unit_id = str(unit.get("id") or "")
        if not unit_id:
            continue
        payload = {key: value for key, value in unit.items() if key not in {"status", "completed_at"}}
        signatures[unit_id] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    reused_completed_unit_count = 0
    stale_completed_unit_count = 0
    reuse_status = "not-needed"
    stored_completed = state.get("completed_units") if isinstance(state.get("completed_units"), dict) else {}
    if not stale and isinstance(stored_completed, dict):
        completed = dict(stored_completed or {})
    elif stale and isinstance(stored_completed, dict):
        reusable: dict[str, Any] = {}
        stale_count = 0
        for raw_unit_id, raw_meta in stored_completed.items():
            unit_id = str(raw_unit_id)
            meta = raw_meta if isinstance(raw_meta, dict) else {}
            stored_signature = str(meta.get("unit_signature") or "")
            if stored_signature and signatures.get(unit_id) == stored_signature:
                reusable[unit_id] = dict(meta)
            else:
                stale_count += 1
        if reusable:
            completed = reusable
            reused_completed_unit_count = len(reusable)
            stale_completed_unit_count = stale_count
            reuse_status = "matched-unit-signatures"
            stale = False
        else:
            stale_completed_unit_count = stale_count
            reuse_status = "no-matching-unit-signatures"
    pending_write: dict[str, Any] | None = None
    status = "ok"
    issue = ""
    if reset:
        completed = {}
        reused_completed_unit_count = 0
        stale_completed_unit_count = 0
        reuse_status = "reset"
        pending_write = {
            "schema_version": 1,
            "tool": "skill-manager.review-progress-state",
            "fingerprint_digest": digest,
            "updated_at": _now_iso(),
            "completed_units": {},
        }
        stale = False
    target_matches = _matching_unit_ids(plan, unit_id=mark_unit_id, command=mark_command)
    target_id = target_matches[0] if len(target_matches) == 1 else ""
    if mark_unit_id or mark_command:
        known = {str(unit.get("id") or "") for unit in _all_units(plan)}
        if len(target_matches) > 1:
            status = "unit-ambiguous"
            issue = "Requested review progress mark matched multiple current units; use the exact next_pending_command or --mark-unit-id."
        elif not target_id or target_id not in known:
            status = "unit-not-found"
            issue = "No current review or validation unit matched the requested id or command."
        else:
            completed[target_id] = {
                "completed_at": _now_iso(),
                "note": note,
                "command": next(
                    (str(unit.get("command") or "") for unit in _all_units(plan) if unit.get("id") == target_id),
                    "",
                ),
                "unit_signature": signatures.get(target_id, ""),
            }
            pending_write = {
                "schema_version": 1,
                "tool": "skill-manager.review-progress-state",
                "fingerprint_digest": digest,
                "updated_at": _now_iso(),
                "completed_units": completed,
            }
            stale = False
    if reuse_status == "matched-unit-signatures" and pending_write is None:
        pending_write = {
            "schema_version": 1,
            "tool": "skill-manager.review-progress-state",
            "fingerprint_digest": digest,
            "updated_at": _now_iso(),
            "completed_units": completed,
            "reuse_status": reuse_status,
        }
    applied_units: list[dict[str, Any]] = []
    for unit in _all_units(plan):
        item = dict(unit)
        unit_id = str(item.get("id") or "")
        if unit_id in completed:
            item["status"] = "completed"
            meta = completed.get(unit_id)
            if isinstance(meta, dict):
                item["completed_at"] = meta.get("completed_at", "")
        else:
            item["status"] = "pending"
        applied_units.append(item)
    pending = [unit for unit in applied_units if unit.get("status") != "completed"]
    completed_count = len(applied_units) - len(pending)
    next_unit = pending[0] if pending else {}
    if stale:
        review_state = "stale"
        progress_status = "stale"
    elif not applied_units:
        review_state = "no-units"
        progress_status = "ready-for-validation"
    elif not pending:
        review_state = "complete"
        progress_status = "complete"
    elif completed_count:
        review_state = "partial"
        progress_status = "in-progress"
    else:
        review_state = "initial"
        progress_status = "needs-review"
    report = {
        "schema_version": 1,
        "tool": "skill-manager.review-progress",
        "ok": status == "ok",
        "status": progress_status if status == "ok" else status,
        "issue": issue,
        "review_state": review_state,
        "state_path": state_path,
        "fingerprint_digest": digest,
        "stored_fingerprint_digest": stored_digest,
        "stale": stale,
        "completed_unit_count": completed_count,
        "pending_unit_count": len(pending),
        "reused_completed_unit_count": reused_completed_unit_count,
        "stale_completed_unit_count": stale_completed_unit_count,
        "reuse_status": reuse_status,
        "review_unit_count": len(_list(plan.get("review_units"))),
        "review_batching": plan.get("review_batching", {}),
        "validation_unit_count": len(_list(plan.get("validation_units"))),
        "current_unit": next_unit,
        "coverage": review_coverage_summary(plan, applied_units),
        "next_pending_command": str(next_unit.get("command") or ""),
        "completed_units": sorted(completed),
        "resume_rule": (
            "Run next_pending_command, then mark it complete with `review-progress --mark-command \"<command>\"`; "
            "if stale is true, regenerate/review from the first current unit."
        ),
    }
    return report, pending_write


def review_coverage_summary(plan: dict[str, Any], applied_units: list[dict[str, Any]]) -> dict[str, Any]:
    owner_names = [
        str(item.get("owner", ""))
        for item in _list(plan.get("owner_groups"))
        if isinstance(item, dict) and str(item.get("owner", "")).strip()
    ]
    review_units = [item for item in applied_units if item.get("scope") != "validation"]
    validation_units = [item for item in applied_units if item.get("scope") == "validation"]
    units_by_owner: dict[str, list[dict[str, Any]]] = {}
    for unit in review_units:
        owner = str(unit.get("owner", "") or "repo")
        units_by_owner.setdefault(owner, []).append(unit)
        if owner and owner not in owner_names:
            owner_names.append(owner)
    complete_owners = [
        owner
        for owner in owner_names
        if units_by_owner.get(owner) and all(unit.get("status") == "completed" for unit in units_by_owner[owner])
    ]
    pending_by_owner: dict[str, int] = {}
    for owner, units in units_by_owner.items():
        pending_by_owner[owner] = sum(
            int(unit.get("estimated_changed_tokens", 0) or 0)
            for unit in units
            if unit.get("status") != "completed"
        )
    largest_unreviewed_owner = ""
    if pending_by_owner:
        owner, tokens = max(pending_by_owner.items(), key=lambda item: (item[1], item[0]))
        if tokens > 0:
            largest_unreviewed_owner = owner
    review_unit_count = len(review_units)
    completed_review_unit_count = sum(1 for unit in review_units if unit.get("status") == "completed")
    validation_unit_count = len(validation_units)
    completed_validation_unit_count = sum(1 for unit in validation_units if unit.get("status") == "completed")
    return {
        "status": (
            "complete"
            if review_unit_count and completed_review_unit_count == review_unit_count
            else "no-review-units"
            if not review_unit_count
            else "partial"
            if completed_review_unit_count
            else "not-started"
        ),
        "owner_total": len(owner_names),
        "owners_complete": len(complete_owners),
        "review_unit_count": review_unit_count,
        "completed_review_unit_count": completed_review_unit_count,
        "pending_review_unit_count": max(0, review_unit_count - completed_review_unit_count),
        "validation_unit_count": validation_unit_count,
        "completed_validation_unit_count": completed_validation_unit_count,
        "largest_unreviewed_owner": largest_unreviewed_owner,
        "cross_cutting_sample_required": len(owner_names) > 1 and review_unit_count > 1,
    }


def review_progress_report(
    root: Path,
    plan: dict[str, Any],
    *,
    input_fingerprint: dict[str, Any] | None = None,
    state_path: str | None = None,
    mark_unit_id: str = "",
    mark_command: str = "",
    note: str = "",
    reset: bool = False,
) -> dict[str, Any]:
    path = _safe_state_path(root, state_path, default_review_progress_path(root))
    state = _load_progress_state(path)
    report, pending_write = build_review_progress(
        plan,
        input_fingerprint=input_fingerprint,
        state=state,
        state_path=path.as_posix(),
        mark_unit_id=mark_unit_id,
        mark_command=mark_command,
        note=note,
        reset=reset,
    )
    if pending_write is not None and report.get("ok"):
        _write_progress_state(path, pending_write)
        report["written"] = path.as_posix()
    return report


def reset_review_progress_state(
    root: Path,
    *,
    fingerprint_digest: str,
    state_path: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    digest = str(fingerprint_digest or "").strip()
    if not digest:
        return {
            "schema_version": 1,
            "tool": "skill-manager.review-progress",
            "ok": False,
            "status": "reset-failed",
            "issue": "Missing fingerprint digest for review progress reset.",
        }
    path = _safe_state_path(root, state_path, default_review_progress_path(root))
    state = {
        "schema_version": 1,
        "tool": "skill-manager.review-progress-state",
        "fingerprint_digest": digest,
        "updated_at": _now_iso(),
        "note": note,
        "completed_units": {},
    }
    _write_progress_state(path, state)
    return {
        "schema_version": 1,
        "tool": "skill-manager.review-progress",
        "ok": True,
        "status": "needs-review",
        "review_state": "initial",
        "stale": False,
        "completed_unit_count": 0,
        "pending_unit_count": 0,
        "state_path": path.as_posix(),
        "fingerprint_digest": digest,
        "written": path.as_posix(),
    }


def summarize_review_progress(report: dict[str, Any]) -> dict[str, Any]:
    current = report.get("current_unit") if isinstance(report.get("current_unit"), dict) else {}
    return {
        "status": report.get("status", "unknown"),
        "review_state": report.get("review_state", "unknown"),
        "stale": bool(report.get("stale", False)),
        "completed_unit_count": report.get("completed_unit_count", 0),
        "pending_unit_count": report.get("pending_unit_count", 0),
        "current_unit": {
            "id": current.get("id", ""),
            "scope": current.get("scope", ""),
            "owner": current.get("owner", ""),
            "path": current.get("path", ""),
            "hunk": current.get("hunk", ""),
            "estimated_changed_tokens": current.get("estimated_changed_tokens", 0),
        } if current else {},
        "coverage": report.get("coverage", {}),
        "review_batching": report.get("review_batching", {}),
        "next_pending_command": report.get("next_pending_command", ""),
        "state_path": report.get("state_path", ""),
        "fingerprint_digest": report.get("fingerprint_digest", ""),
    }


from repo_support import repo_review_costs

build_review_cost_report = repo_review_costs.build_review_cost_report
build_money_saving_estimate = repo_review_costs.build_money_saving_estimate
summarize_review_cost_report = repo_review_costs.summarize_review_cost_report
append_budget_trend = repo_review_costs.append_budget_trend
budget_trend_summary = repo_review_costs.budget_trend_summary
render_review_plan = repo_review_costs.render_review_plan
render_review_cost_report = repo_review_costs.render_review_cost_report
