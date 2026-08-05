#!/usr/bin/env python3
"""Review packet helpers for path and hunk scoped changed-file review."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from repo_support import repo_common as repo
from repo_support import repo_cost_policy


HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


def review_command_arg(value: str) -> str:
    text = str(value)
    if text and all(char.isalnum() or char in {".", "/", "\\", "_", "-", ":"} for char in text):
        return text
    return json.dumps(text)


def owner_review_command(owner: str, paths: list[str] | None = None, hunks: list[str] | None = None) -> str:
    parts = [
        "python -B .agents/manage.py review-packet",
        "--owner",
        review_command_arg(owner),
    ]
    for path in paths or []:
        parts.extend(["--path", review_command_arg(path)])
    for hunk in hunks or []:
        parts.extend(["--hunk", review_command_arg(hunk)])
    parts.extend(["--summary", "--compact", "--format", "json"])
    return " ".join(parts)


def normalize_review_path(path: str, *, root: Path | None = None) -> str:
    raw = str(path or "").strip()
    if root is not None:
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            resolved_root = root.expanduser().resolve()
            resolved_candidate = candidate.resolve()
            if resolved_candidate.is_relative_to(resolved_root):
                return resolved_candidate.relative_to(resolved_root).as_posix()
    normalized = raw.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def diff_hunk_ranges(root: Path, path: str) -> list[dict[str, int]]:
    hunks: list[dict[str, int]] = []
    status, lines = repo.git_output(root, "diff", "HEAD", "--unified=0", "--", path)
    if status != 0:
        lines = []
        for args in (("diff", "--unified=0", "--", path), ("diff", "--cached", "--unified=0", "--", path)):
            fallback_status, fallback_lines = repo.git_output(root, *args)
            if fallback_status == 0:
                lines.extend(fallback_lines)
    for line in lines:
        match = HUNK_HEADER_RE.match(line)
        if not match:
            continue
        old_count = int(match.group("old_count") or "1")
        new_count = int(match.group("new_count") or "1")
        hunks.append(
            {
                "old_start": int(match.group("old_start")),
                "old_count": old_count,
                "new_start": int(match.group("new_start")),
                "new_count": new_count,
                "line_count": max(old_count, new_count, 1),
            }
        )
    return hunks


def path_review_hunk_subpackets(
    root: Path,
    owner: str,
    row: dict[str, Any],
    estimated_tokens: int,
    validation_first: list[str],
    review_budget: int,
) -> list[dict[str, Any]]:
    if estimated_tokens <= review_budget:
        return []
    path = str(row.get("path", ""))
    hunks = diff_hunk_ranges(root, path)
    if not hunks:
        candidate = root / path
        if candidate.is_file():
            try:
                with candidate.open("r", encoding="utf-8", errors="ignore") as handle:
                    line_count = sum(1 for _line in handle)
            except OSError:
                line_count = 0
            if line_count:
                hunks = [
                    {
                        "old_start": 1,
                        "old_count": 0,
                        "new_start": 1,
                        "new_count": line_count,
                        "line_count": line_count,
                    }
                ]
    if not hunks:
        return []
    max_lines = max(40, min(300, review_budget // 12 if review_budget else 300))
    subpackets: list[dict[str, Any]] = []
    for hunk in hunks:
        line_count = max(1, int(hunk.get("line_count", 1) or 1))
        base_start = max(1, int(hunk.get("new_start", 1) or 1))
        for offset in range(0, line_count, max_lines):
            chunk_lines = min(max_lines, line_count - offset)
            line_start = base_start + offset
            line_end = max(line_start, line_start + chunk_lines - 1)
            hunk_id = f"h{len(subpackets) + 1:03d}"
            chunk_estimate = min(max(1, chunk_lines * 12), estimated_tokens)
            read_row = dict(row)
            read_row.update(
                {
                    "hunk": hunk_id,
                    "line_start": line_start,
                    "line_end": line_end,
                    "range": f"{path}:{line_start}-{line_end}",
                    "read": "inspect-hunk-before-full-file",
                }
            )
            subpackets.append(
                {
                    "schema_version": 1,
                    "tool": "skill-manager.owner-review-hunk",
                    "owner": owner,
                    "scope": "hunk",
                    "path": path,
                    "hunk": hunk_id,
                    "range": f"{path}:{line_start}-{line_end}",
                    "line_start": line_start,
                    "line_end": line_end,
                    "status": "over-budget" if chunk_estimate > review_budget else "within-budget",
                    "priority": row.get("risk", "medium"),
                    "changed_file_count": 1,
                    "estimated_changed_tokens": chunk_estimate,
                    "review_budget_tokens": review_budget,
                    "tokens_over_review_budget": max(0, chunk_estimate - review_budget),
                    "risk_counts": {str(row.get("risk") or "unknown"): 1},
                    "read_first": [read_row],
                    "paths": [path],
                    "validation_first": validation_first[:6],
                    "next_command": owner_review_command(owner, [path], [hunk_id]),
                    "review_rule": "Review this changed hunk range before broader path, owner, or raw diff context.",
                }
            )
    return subpackets


def selected_hunk_packet(
    parent_packet: dict[str, Any],
    owner_packet: dict[str, Any],
    selected_path_packets: list[dict[str, Any]],
    selected_hunks: list[str],
) -> dict[str, Any]:
    hunk_packets: list[dict[str, Any]] = []
    for path_packet in selected_path_packets:
        hunks = (
            path_packet.get("path_review_hunks")
            if isinstance(path_packet.get("path_review_hunks"), list)
            else []
        )
        hunk_packets.extend(item for item in hunks if isinstance(item, dict))
    by_hunk = {str(item.get("hunk", "")): item for item in hunk_packets if item.get("hunk")}
    missing = [hunk for hunk in selected_hunks if hunk not in by_hunk]
    if missing:
        return {
            "schema_version": 1,
            "tool": "skill-manager.owner-review-packet",
            "ok": False,
            "status": "hunk-not-found",
            "owner": owner_packet.get("owner", ""),
            "requested_hunks": selected_hunks,
            "missing_hunks": missing,
            "available_hunks": [str(item.get("hunk", "")) for item in hunk_packets],
            "next_command": str(owner_packet.get("next_command") or owner_packet.get("owner_summary_command", "")),
        }
    selected = [by_hunk[hunk] for hunk in selected_hunks]
    selected_hunk_set = set(selected_hunks)
    selected_positions = [
        index for index, item in enumerate(hunk_packets) if str(item.get("hunk", "")) in selected_hunk_set
    ]
    selected_position_set = set(selected_positions)
    gap_positions: list[int] = []
    if selected_positions:
        for index in range(min(selected_positions), max(selected_positions) + 1):
            if index not in selected_position_set:
                gap_positions.append(index)
    next_hunk_command = ""
    if selected_positions:
        next_index = gap_positions[0] if gap_positions else max(selected_positions) + 1
        if next_index < len(hunk_packets):
            next_hunk_command = str(hunk_packets[next_index].get("next_command", "") or "")
    estimated_tokens = sum(int(item.get("estimated_changed_tokens", 0) or 0) for item in selected)
    risk_counts: dict[str, int] = {}
    read_first: list[dict[str, Any]] = []
    paths: list[str] = []
    ranges: list[str] = []
    for item in selected:
        item_risks = item.get("risk_counts") if isinstance(item.get("risk_counts"), dict) else {}
        for risk, count in item_risks.items():
            risk_counts[str(risk)] = risk_counts.get(str(risk), 0) + int(count or 0)
        read_first.extend(row for row in item.get("read_first", []) if isinstance(row, dict))
        paths.extend(str(path) for path in item.get("paths", []) if path)
        if item.get("range"):
            ranges.append(str(item.get("range")))
    result = dict(owner_packet)
    result.pop("owner_review_subpackets", None)
    result.pop("owner_review_subpacket_commands", None)
    result["scope"] = "hunk-slice"
    result["status"] = "over-budget" if estimated_tokens > int(owner_packet.get("review_budget_tokens", 0) or 0) else "within-budget"
    result["changed_file_count"] = len(set(paths))
    result["estimated_changed_tokens"] = estimated_tokens
    result["tokens_over_review_budget"] = max(0, estimated_tokens - int(owner_packet.get("review_budget_tokens", 0) or 0))
    result["risk_counts"] = risk_counts
    result["read_first"] = read_first[:8]
    result["paths"] = sorted(set(paths))
    result["selected_paths"] = sorted(set(paths))
    result["selected_hunks"] = selected_hunks
    result["selected_ranges"] = ranges
    result["available_hunk_count"] = len(hunk_packets)
    result["skipped_hunk_gap_count"] = len(gap_positions)
    result["remaining_hunk_count"] = max(
        0,
        len(hunk_packets) - (max(selected_positions) + 1 if selected_positions else 0) + len(gap_positions),
    )
    result["next_hunk_command"] = next_hunk_command
    result["parent_owner_changed_file_count"] = owner_packet.get("changed_file_count", 0)
    result["parent_owner_estimated_changed_tokens"] = owner_packet.get("estimated_changed_tokens", 0)
    result["parent_owner_tokens_over_review_budget"] = owner_packet.get("tokens_over_review_budget", 0)
    result["parent_owner_subpacket_count"] = owner_packet.get("owner_review_subpacket_count", 0)
    result["owner_summary_command"] = owner_packet.get("owner_summary_command", "")
    result["owner_review_subpacket_count"] = 0
    result["owner_review_hunk_count"] = 0
    result["largest_owner_subpacket_estimated_tokens"] = 0
    result["largest_owner_hunk_estimated_tokens"] = 0
    validation = result.get("validation_first") if isinstance(result.get("validation_first"), list) else []
    result["next_command"] = next_hunk_command or (validation[0] if validation else "python -B .agents/manage.py check-changed --summary --compact --format json")
    result["review_rule"] = (
        "Read the selected hunk range, then follow next_command for the next hunk or validation."
    )
    result["raw_changed_diff_estimated_tokens"] = parent_packet.get(
        "changed_diff_estimated_tokens",
        result.get("estimated_changed_tokens", 0),
    )
    result["parent_changed_file_count"] = parent_packet.get("changed_file_count", 0)
    result["parent_owner_review_packet_count"] = parent_packet.get(
        "owner_review_packet_count",
        0,
    )
    result["cost_ledger"] = repo_cost_policy.review_cost_ledger(result)
    return result


def owner_path_review_packet(
    parent_packet: dict[str, Any],
    owner_packet: dict[str, Any],
    selected_paths: list[str],
    selected_hunks: list[str] | None = None,
) -> dict[str, Any]:
    normalized_requested = [normalize_review_path(path) for path in selected_paths if normalize_review_path(path)]
    subpackets = (
        owner_packet.get("owner_review_subpackets")
        if isinstance(owner_packet.get("owner_review_subpackets"), list)
        else []
    )
    by_path = {
        normalize_review_path(str(item.get("path", ""))): item
        for item in subpackets
        if isinstance(item, dict) and item.get("path")
    }
    missing = [path for path in normalized_requested if path not in by_path]
    if not normalized_requested:
        missing = ["<empty>"]
    if missing:
        return {
            "schema_version": 1,
            "tool": "skill-manager.owner-review-packet",
            "ok": False,
            "status": "path-not-found",
            "owner": owner_packet.get("owner", ""),
            "requested_paths": normalized_requested,
            "missing_paths": missing,
            "available_paths": [str(item.get("path", "")) for item in subpackets if isinstance(item, dict)],
            "next_command": str(owner_packet.get("owner_summary_command") or owner_packet.get("next_command", "")),
        }
    selected = [by_path[path] for path in normalized_requested]
    normalized_hunks = [str(hunk).strip() for hunk in selected_hunks or [] if str(hunk).strip()]
    if normalized_hunks:
        return selected_hunk_packet(parent_packet, owner_packet, selected, normalized_hunks)
    estimated_tokens = sum(int(item.get("estimated_changed_tokens", 0) or 0) for item in selected)
    risk_counts: dict[str, int] = {}
    read_first: list[dict[str, Any]] = []
    paths: list[str] = []
    for item in selected:
        item_risks = item.get("risk_counts") if isinstance(item.get("risk_counts"), dict) else {}
        for risk, count in item_risks.items():
            risk_counts[str(risk)] = risk_counts.get(str(risk), 0) + int(count or 0)
        read_first.extend(row for row in item.get("read_first", []) if isinstance(row, dict))
        paths.extend(str(path) for path in item.get("paths", []) if path)
    result = dict(owner_packet)
    result.pop("owner_review_subpackets", None)
    result.pop("owner_review_subpacket_commands", None)
    result["scope"] = "path-slice"
    result["status"] = "over-budget" if estimated_tokens > int(owner_packet.get("review_budget_tokens", 0) or 0) else "within-budget"
    result["changed_file_count"] = len(paths)
    result["estimated_changed_tokens"] = estimated_tokens
    result["tokens_over_review_budget"] = max(0, estimated_tokens - int(owner_packet.get("review_budget_tokens", 0) or 0))
    result["risk_counts"] = risk_counts
    result["read_first"] = read_first[:8]
    result["paths"] = paths
    result["selected_paths"] = paths
    result["parent_owner_changed_file_count"] = owner_packet.get("changed_file_count", 0)
    result["parent_owner_estimated_changed_tokens"] = owner_packet.get("estimated_changed_tokens", 0)
    result["parent_owner_tokens_over_review_budget"] = owner_packet.get("tokens_over_review_budget", 0)
    result["parent_owner_subpacket_count"] = owner_packet.get("owner_review_subpacket_count", 0)
    result["owner_summary_command"] = owner_packet.get("owner_summary_command", "")
    path_hunks: list[dict[str, Any]] = []
    for item in selected:
        hunks = item.get("path_review_hunks") if isinstance(item.get("path_review_hunks"), list) else []
        path_hunks.extend(hunk for hunk in hunks if isinstance(hunk, dict))
    hunk_commands = [str(item.get("next_command", "")) for item in path_hunks if item.get("next_command")]
    result["path_review_hunk_count"] = len(path_hunks)
    result["path_review_hunks"] = path_hunks
    result["path_review_hunk_commands"] = hunk_commands
    result["largest_path_hunk_estimated_tokens"] = max(
        (int(item.get("estimated_changed_tokens", 0) or 0) for item in path_hunks),
        default=0,
    )
    result["owner_review_subpacket_count"] = 0
    result["owner_review_hunk_count"] = len(path_hunks)
    result["largest_owner_subpacket_estimated_tokens"] = 0
    result["largest_owner_hunk_estimated_tokens"] = result["largest_path_hunk_estimated_tokens"]
    validation = result.get("validation_first") if isinstance(result.get("validation_first"), list) else []
    result["next_command"] = hunk_commands[0] if hunk_commands else (validation[0] if validation else "python -B .agents/manage.py check-changed --summary --compact --format json")
    result["review_rule"] = (
        "Path slice is still over budget; follow next_command for the first deterministic hunk subpacket."
        if hunk_commands
        else "Read the selected path slice, then run validation_first before broader owner/raw diff review."
    )
    result["raw_changed_diff_estimated_tokens"] = parent_packet.get(
        "changed_diff_estimated_tokens",
        result.get("estimated_changed_tokens", 0),
    )
    result["parent_changed_file_count"] = parent_packet.get("changed_file_count", 0)
    result["parent_owner_review_packet_count"] = parent_packet.get(
        "owner_review_packet_count",
        0,
    )
    result["cost_ledger"] = repo_cost_policy.review_cost_ledger(result)
    return result
