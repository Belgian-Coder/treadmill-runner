#!/usr/bin/env python3
"""Changed-file checks owned by skill-manager."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shlex
import sys
import time
from pathlib import Path

from repo_support import repo_addition_acceptance
from repo_support import repo_command_metrics
from repo_support import repo_common as repo
from repo_support import repo_context_guardrails
from repo_support import repo_cost_policy
from repo_support import repo_policy
from repo_support import repo_changed_git
from repo_support import repo_generated as generated
from repo_support import repo_health as health
from repo_support import repo_optimizations
from repo_support import repo_fingerprint
from repo_support import repo_portability
from repo_support import repo_proof_hygiene
from repo_support import repo_qol_capture
from repo_support import repo_review_hunks
from repo_support import repo_review_progress
from repo_support import repo_syntax
from repo_support.repo_navigation_status import auto_refresh_navigation, navigation_status

review_command_arg = repo_review_hunks.review_command_arg
owner_review_command = repo_review_hunks.owner_review_command
normalize_review_path = repo_review_hunks.normalize_review_path
diff_hunk_ranges = repo_review_hunks.diff_hunk_ranges
path_review_hunk_subpackets = repo_review_hunks.path_review_hunk_subpackets
selected_hunk_packet = repo_review_hunks.selected_hunk_packet
owner_path_review_packet = repo_review_hunks.owner_path_review_packet
INSTRUCTION_GENERATED = repo_addition_acceptance.INSTRUCTION_GENERATED
SKILL_GENERATED = repo_addition_acceptance.SKILL_GENERATED
WORKFLOW_GENERATED = repo_addition_acceptance.WORKFLOW_GENERATED
WORKFLOW_GLOBAL_FILES = repo_addition_acceptance.WORKFLOW_GLOBAL_FILES
ADDITION_STATUS_MARKERS = repo_addition_acceptance.ADDITION_STATUS_MARKERS
ALLOWED_UNOWNED_NEW_PREFIXES = repo_addition_acceptance.ALLOWED_UNOWNED_NEW_PREFIXES
ALLOWED_UNOWNED_NEW_FILES = repo_addition_acceptance.ALLOWED_UNOWNED_NEW_FILES
normalized_paths = repo_addition_acceptance.normalized_paths
skill_name_from_path = repo_addition_acceptance.skill_name_from_path
workflow_name_from_path = repo_addition_acceptance.workflow_name_from_path
is_generated_path = repo_addition_acceptance.is_generated_path
generated_path_has_source = repo_addition_acceptance.generated_path_has_source
issue = repo_addition_acceptance.issue
dotnet_skill_naming_issues = repo_addition_acceptance.dotnet_skill_naming_issues
is_integration_descriptor_path = repo_addition_acceptance.is_integration_descriptor_path
render_addition_acceptance = repo_addition_acceptance.render_addition_acceptance
changed_files = repo_changed_git.changed_files
changed_file_statuses = repo_changed_git.changed_file_statuses


def addition_acceptance_report(
    root: Path,
    *,
    paths: list[str] | None = None,
    new_paths: list[str] | None = None,
) -> dict[str, object]:
    return repo_addition_acceptance.addition_acceptance_report(
        root,
        paths=paths,
        new_paths=new_paths,
        changed_files_func=changed_files,
        changed_file_statuses_func=changed_file_statuses,
    )


def changed_scope(paths: list[str]) -> dict[str, object]:
    scope: dict[str, object] = {
        "instructions": False,
        "skill_names": set(),
        "skills_generated": False,
        "workflows": False,
        "workflow_generated": False,
        "repo_surface": False,
        "python_paths": [],
        "docs": [],
        "other": [],
    }
    for path in paths:
        value = path.replace("\\", "/")
        parts = value.split("/")
        if value == ".agents/manage.py" or (
            value.endswith(".py") and (value.startswith(".agents/") or value.startswith("automations/"))
        ):
            python_paths = scope["python_paths"]
            assert isinstance(python_paths, list)
            python_paths.append(value)
        if value == "AGENTS.md" or value in INSTRUCTION_GENERATED:
            scope["instructions"] = True
        elif value.startswith(".agents/skills/") and len(parts) >= 3:
            cast_set = scope["skill_names"]
            assert isinstance(cast_set, set)
            cast_set.add(parts[2])
        elif value in {".agents/routing.md", ".agents/registry.json"} or value.startswith(
            ".claude/skills/"
        ):
            scope["skills_generated"] = True
        elif value.startswith("automations/"):
            if value in {"automations/routing.md", "automations/registry.json"}:
                scope["workflow_generated"] = True
            else:
                scope["workflows"] = True
        elif value.startswith("docs/"):
            docs = scope["docs"]
            assert isinstance(docs, list)
            docs.append(value)
        elif value.startswith(".agents/manage.py") or value.startswith(
            ".agents/skills/skill-manager/scripts/"
        ) or value.startswith(".agents/skills/workflow-manager/scripts/") or value.startswith(
            ".github/workflows/"
        ):
            scope["repo_surface"] = True
        else:
            other = scope["other"]
            assert isinstance(other, list)
            other.append(value)
    return scope


def existing_changed_python_paths(root: Path, paths: list[str]) -> list[str]:
    """Return changed Python paths that still exist after additions, edits, or deletions."""
    return [path for path in paths if (root / path).is_file()]


CheckResult = tuple[str, bool, str, int]


def elapsed_ms_since(start: float) -> int:
    return max(0, int(round((time.perf_counter() - start) * 1000)))


def run_named_check(name: str, callback) -> CheckResult:
    started = time.perf_counter()
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        status = callback()
    text = output.getvalue().strip()
    return name, status == 0, text, elapsed_ms_since(started)


def run_named_script_check(name: str, script: Path, arguments: list[str]) -> CheckResult:
    started = time.perf_counter()
    status, output = repo.run_python_script_quiet(script, arguments)
    return name, status == 0, output, elapsed_ms_since(started)


def run_planned_command_check(root: Path, command: str) -> CheckResult:
    """Run a deterministic validation-plan command that has no in-process equivalent."""
    started = time.perf_counter()
    arguments = shlex.split(command, posix=True)
    if not arguments:
        return command, False, "empty validation-plan command", elapsed_ms_since(started)
    if arguments[0].lower() in {"python", "python3", "py"}:
        arguments[0] = sys.executable
    try:
        returncode, output, timed_out = repo_qol_capture.run_process_output(
            arguments,
            cwd=root,
            timeout=180,
        )
    except OSError as exc:
        return command, False, str(exc), elapsed_ms_since(started)
    if timed_out:
        return command, False, output.strip(), elapsed_ms_since(started)
    return command, returncode == 0, output.strip(), elapsed_ms_since(started)


def path_group(path: str) -> str:
    value = path.replace("\\", "/")
    parts = value.split("/")
    if value.startswith(".agents/skills/") and len(parts) >= 3:
        return f".agents/skills/{parts[2]}/"
    if value.startswith(".claude/skills/") and len(parts) >= 3:
        return f".claude/skills/{parts[2]}/"
    if value.startswith("automations/") and len(parts) >= 2:
        if parts[1] in {"routing.md", "registry.json"}:
            return "automations/generated/"
        if len(parts) == 2:
            return value
        return f"automations/{parts[1]}/"
    if "/" in value:
        return f"{parts[0]}/"
    return value


def compact_path_groups(paths: list[str]) -> str:
    groups: dict[str, int] = {}
    for path in paths:
        group = path_group(path)
        groups[group] = groups.get(group, 0) + 1
    return ", ".join(f"{name} ({count})" for name, count in sorted(groups.items()))


def review_owner(path: str) -> str:
    skill_name = skill_name_from_path(path)
    if skill_name:
        return f"skill:{skill_name}"
    workflow_name = workflow_name_from_path(path)
    if workflow_name:
        return f"workflow:{workflow_name}"
    if is_generated_path(path):
        return "generated-adapter"
    if path.startswith("docs/"):
        return "docs"
    if path == ".agents/local-ai.json":
        return "local-ai-policy"
    if path.startswith(".agents/"):
        return "agent-harness"
    return "repo"


def review_risk(path: str) -> str:
    value = path.replace("\\", "/")
    if value in {"AGENTS.md", ".agents/local-ai.json"} or value in INSTRUCTION_GENERATED:
        return "high"
    if value.endswith("module.json") or value.endswith("WORKFLOW.md") or value.endswith("SKILL.md"):
        return "high"
    if value == ".agents/manage.py" or value.startswith(".agents/skills/skill-manager/scripts/"):
        return "high"
    if value.endswith(".py") or value in WORKFLOW_GENERATED or value in SKILL_GENERATED:
        return "medium"
    if value.startswith("automations/navigation/artifacts/maps/"):
        return "low"
    return "medium" if value.startswith("automations/") or value.startswith(".agents/") else "low"


def changed_path_token_estimates(
    root: Path,
    paths: list[str],
    *,
    statuses: dict[str, set[str]] | None = None,
) -> dict[str, dict[str, int]]:
    normalized = normalized_paths(paths)
    estimates: dict[str, dict[str, int]] = {
        path: {
            "added": 0,
            "deleted": 0,
            "tracked_estimated_tokens": 0,
            "untracked_estimated_tokens": 0,
            "estimated_tokens": 0,
        }
        for path in normalized
    }
    path_set = set(normalized)
    status, lines = repo.git_output(root, "diff", "HEAD", "--numstat")
    if status != 0:
        lines = []
        for args in (("diff", "--numstat"), ("diff", "--cached", "--numstat")):
            fallback_status, fallback_lines = repo.git_output(root, *args)
            if fallback_status == 0:
                lines.extend(fallback_lines)
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        path = parts[-1].strip().replace("\\", "/")
        if path not in path_set:
            continue
        added = int(parts[0]) if parts[0].isdigit() else 0
        deleted = int(parts[1]) if parts[1].isdigit() else 0
        row = estimates[path]
        row["added"] += added
        row["deleted"] += deleted
        row["tracked_estimated_tokens"] += (added + deleted) * 12
    statuses = statuses if statuses is not None else changed_file_statuses(root)
    for path in normalized:
        if "?" not in statuses.get(path, set()):
            continue
        candidate = root / path
        if not candidate.is_file():
            continue
        try:
            estimates[path]["untracked_estimated_tokens"] = repo_cost_policy.estimate_tokens_from_bytes(
                candidate.stat().st_size
            )
        except OSError:
            continue
    for row in estimates.values():
        row["estimated_tokens"] = row["tracked_estimated_tokens"] + row["untracked_estimated_tokens"]
    return estimates


def risk_counts_for_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        risk = str(row.get("risk") or "unknown")
        counts[risk] = counts.get(risk, 0) + 1
    return dict(sorted(counts.items()))


def owner_validation_commands(owner: str, required_commands: list[str]) -> list[str]:
    if not required_commands:
        return []
    generic_needles = ("check-additions", "check-changed", "portable-constraints")
    selected: list[str] = []
    if owner.startswith("skill:"):
        name = owner.split(":", 1)[1]
        needles = (
            f"validate_skill.py .agents/skills/{name}",
            f".agents/skills/{name}/scripts/run_self_tests.py",
            "sync-skill-routing",
            "sync-claude-skills",
            *generic_needles,
        )
    elif owner.startswith("workflow:"):
        name = owner.split(":", 1)[1]
        needles = (
            f"automations/{name}",
            f" {name}",
            f"--name {name}",
            "validate-automations",
            "sync-automation-routing",
            *generic_needles,
        )
    else:
        needles = generic_needles
    for command in required_commands:
        if "syntax-check --paths" in command:
            continue
        if any(needle in command for needle in needles):
            selected.append(command)
    return selected[:6] or required_commands[:4]


def owner_syntax_check_command(owner: str, owner_rows: list[dict[str, Any]]) -> str:
    python_paths = [str(row.get("path")) for row in owner_rows if str(row.get("path", "")).endswith(".py")]
    if not python_paths:
        return ""
    if owner.startswith("skill:"):
        return f"python -B .agents/manage.py syntax-check --paths .agents/skills/{owner.split(':', 1)[1]} --format json"
    if owner.startswith("workflow:"):
        return f"python -B .agents/manage.py syntax-check --paths automations/{owner.split(':', 1)[1]} --format json"
    if len(python_paths) > 8:
        return "python -B .agents/manage.py syntax-check --paths .agents/skills automations --format json"
    return f"python -B .agents/manage.py syntax-check --paths {' '.join(python_paths)} --format json"


def owner_review_slug(owner: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in owner).strip("-") or "owner"


def summarize_owner_review_packets(owner_packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for packet in owner_packets:
        if not isinstance(packet, dict):
            continue
        summary.append(
            {
                "owner": packet.get("owner", ""),
                "status": packet.get("status", "unknown"),
                "priority": packet.get("priority", "unknown"),
                "changed_file_count": packet.get("changed_file_count", 0),
                "estimated_changed_tokens": packet.get("estimated_changed_tokens", 0),
                "tokens_over_review_budget": packet.get("tokens_over_review_budget", 0),
                "owner_review_subpacket_count": packet.get("owner_review_subpacket_count", 0),
                "largest_owner_subpacket_estimated_tokens": packet.get("largest_owner_subpacket_estimated_tokens", 0),
                "owner_review_hunk_count": packet.get("owner_review_hunk_count", 0),
                "largest_owner_hunk_estimated_tokens": packet.get("largest_owner_hunk_estimated_tokens", 0),
                "risk_counts": packet.get("risk_counts", {}),
                "next_command": packet.get("next_command", ""),
                "owner_summary_command": packet.get("owner_summary_command", ""),
            }
        )
    return summary


def _first_nested_review_command(packet: dict[str, Any]) -> str:
    for subpacket in packet.get("owner_review_subpackets", []) if isinstance(packet.get("owner_review_subpackets"), list) else []:
        if not isinstance(subpacket, dict):
            continue
        for key in ("path_summary_command", "next_command"):
            command = str(subpacket.get(key) or "")
            if command:
                return command
        hunks = subpacket.get("path_review_hunks") if isinstance(subpacket.get("path_review_hunks"), list) else []
        for hunk in hunks:
            if isinstance(hunk, dict) and hunk.get("next_command"):
                return str(hunk.get("next_command"))
    return ""


def affected_owner_context(packet: dict[str, Any], *, limit: int = 3) -> dict[str, Any]:
    owner_packets = packet.get("owner_review_packets") if isinstance(packet.get("owner_review_packets"), list) else []
    selected_packets = [item for item in owner_packets if isinstance(item, dict)]
    if not selected_packets and packet.get("tool") == "skill-manager.owner-review-packet":
        selected_packets = [packet]
    rows: list[dict[str, Any]] = []
    for owner_packet in selected_packets:
        owner = str(owner_packet.get("owner") or "").strip()
        if not owner:
            continue
        next_command = str(
            owner_packet.get("owner_summary_command")
            or owner_packet.get("next_command")
            or _first_nested_review_command(owner_packet)
            or packet.get("next_review_command")
            or ""
        )
        rows.append(
            {
                "owner": owner,
                "capsule": f"automations/navigation/artifacts/maps/owners/{owner_review_slug(owner)}.md",
                "status": owner_packet.get("status", "unknown"),
                "changed_file_count": owner_packet.get("changed_file_count", 0),
                "estimated_changed_tokens": owner_packet.get("estimated_changed_tokens", 0),
                "next_command": next_command,
            }
        )
    limited = rows[: max(1, int(limit or 1))]
    return {
        "status": "present" if rows else "missing",
        "owner_count": len(rows),
        "owners": limited,
        "omitted_owner_count": max(0, len(rows) - len(limited)),
        "read_rule": "Read HANDOFF.md, then only the matching owner capsule; raw navigation JSON is tool-only.",
    }


def summarize_validation_commands(commands: list[Any], *, limit: int = 8) -> list[str]:
    compacted: list[str] = []
    root = repo_policy.project_root()
    syntax_chars = repo_policy.int_value(root, "limits.review.syntax_command_chars")
    command_chars = repo_policy.int_value(root, "limits.review.validation_command_chars")
    for command in commands:
        value = str(command)
        if "syntax-check --paths" in value and len(value) > syntax_chars:
            value = "python -B .agents/manage.py syntax-check --paths <changed-python-files> --format json"
        elif len(value) > command_chars:
            value = value[: command_chars - 4].rstrip() + " ..."
        compacted.append(value)
        if len(compacted) >= limit:
            break
    return compacted


def compact_next_command(command: Any, *, root: Path | None = None) -> str:
    value = str(command or "").strip()
    policy_root = root or repo_policy.project_root()
    limit = repo_policy.int_value(policy_root, "limits.context.validation_command_chars")
    if len(value) <= limit:
        return value
    if "review-packet" in value:
        return (
            "python -B .agents/manage.py review-loop --max-units 1 "
            "--summary --compact --format json"
        )
    return (
        "python -B .agents/manage.py check-changed --record-progress "
        "--summary --compact --format json"
    )


def build_owner_review_packets(
    root: Path,
    rows: list[dict[str, Any]],
    estimates: dict[str, dict[str, int]],
    required_commands: list[str],
    review_budget: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("owner") or "repo"), []).append(row)
    packets: list[dict[str, Any]] = []
    risk_rank = {"high": 0, "medium": 1, "low": 2}
    for owner, owner_rows in grouped.items():
        owner_rows = sorted(
            owner_rows,
            key=lambda row: (risk_rank.get(str(row.get("risk")), 9), str(row.get("path"))),
        )
        estimated_tokens = sum(
            int(estimates.get(str(row.get("path")), {}).get("estimated_tokens", 0) or 0)
            for row in owner_rows
        )
        risk_counts = risk_counts_for_rows(owner_rows)
        priority = "high" if risk_counts.get("high") else "medium" if risk_counts.get("medium") else "low"
        validation_first = owner_validation_commands(owner, required_commands)
        syntax_command = owner_syntax_check_command(owner, owner_rows)
        if syntax_command and not any("syntax-check" in command for command in validation_first):
            validation_first.insert(0, syntax_command)
        subpackets = owner_review_subpackets(owner, owner_rows, estimates, root, validation_first[:6], review_budget)
        owner_summary_command = owner_review_command(owner)
        owner_over_budget = estimated_tokens > review_budget
        subpacket_commands = [
            str(item.get("next_command") or item.get("path_summary_command", ""))
            for item in subpackets
            if item.get("path_summary_command") or item.get("next_command")
        ]
        next_command = subpacket_commands[0] if owner_over_budget and subpacket_commands else owner_summary_command
        hunk_count = sum(
            int(item.get("path_review_hunk_count", 0) or 0)
            for item in subpackets
            if isinstance(item, dict)
        )
        largest_hunk_tokens = max(
            (
                int(item.get("largest_path_hunk_estimated_tokens", 0) or 0)
                for item in subpackets
                if isinstance(item, dict)
            ),
            default=0,
        )
        packets.append(
            {
                "schema_version": 1,
                "tool": "skill-manager.owner-review-packet",
                "owner": owner,
                "status": "over-budget" if owner_over_budget else "within-budget",
                "priority": priority,
                "changed_file_count": len(owner_rows),
                "estimated_changed_tokens": estimated_tokens,
                "review_budget_tokens": review_budget,
                "tokens_over_review_budget": max(0, estimated_tokens - review_budget),
                "risk_counts": risk_counts,
                "read_first": owner_rows[:8],
                "paths": [str(row.get("path")) for row in owner_rows],
                "validation_first": validation_first[:6],
                "owner_summary_command": owner_summary_command,
                "owner_review_subpacket_count": len(subpackets) if owner_over_budget else 0,
                "owner_review_subpackets": subpackets if owner_over_budget else [],
                "owner_review_subpacket_commands": subpacket_commands if owner_over_budget else [],
                "owner_review_hunk_count": hunk_count if owner_over_budget else 0,
                "largest_owner_hunk_estimated_tokens": largest_hunk_tokens if owner_over_budget else 0,
                "largest_owner_subpacket_estimated_tokens": max(
                    (int(item.get("estimated_changed_tokens", 0) or 0) for item in subpackets),
                    default=0,
                ) if owner_over_budget else 0,
                "next_command": next_command,
                "review_rule": (
                    "Owner slice is still over budget; follow next_command for the first deterministic path subpacket."
                    if owner_over_budget
                    else "Review this owner slice before loading the raw full diff."
                ),
            }
        )
    packets.sort(
        key=lambda packet: (
            risk_rank.get(str(packet.get("priority")), 9),
            -int(packet.get("estimated_changed_tokens", 0) or 0),
            str(packet.get("owner")),
        )
    )
    return packets


def owner_review_subpackets(
    owner: str,
    owner_rows: list[dict[str, Any]],
    estimates: dict[str, dict[str, int]],
    root: Path,
    validation_first: list[str],
    review_budget: int,
) -> list[dict[str, Any]]:
    risk_rank = {"high": 0, "medium": 1, "low": 2}
    subpackets: list[dict[str, Any]] = []
    for row in owner_rows:
        path = str(row.get("path", ""))
        estimated_tokens = int(estimates.get(path, {}).get("estimated_tokens", 0) or 0)
        risk = str(row.get("risk") or "low")
        hunk_subpackets = path_review_hunk_subpackets(
            root,
            owner,
            row,
            estimated_tokens,
            validation_first,
            review_budget,
        )
        hunk_commands = [
            str(item.get("next_command", ""))
            for item in hunk_subpackets
            if item.get("next_command")
        ]
        next_command = hunk_commands[0] if hunk_commands else owner_review_command(owner, [path])
        path_summary_command = owner_review_command(owner, [path])
        subpackets.append(
            {
                "schema_version": 1,
                "tool": "skill-manager.owner-review-subpacket",
                "owner": owner,
                "scope": "path",
                "path": path,
                "status": "over-budget" if estimated_tokens > review_budget else "within-budget",
                "priority": risk,
                "changed_file_count": 1,
                "estimated_changed_tokens": estimated_tokens,
                "review_budget_tokens": review_budget,
                "tokens_over_review_budget": max(0, estimated_tokens - review_budget),
                "risk_counts": {risk: 1},
                "read_first": [row],
                "paths": [path],
                "validation_first": validation_first[:6],
                "path_review_hunk_count": len(hunk_subpackets),
                "path_review_hunks": hunk_subpackets,
                "path_review_hunk_commands": hunk_commands,
                "largest_path_hunk_estimated_tokens": max(
                    (int(item.get("estimated_changed_tokens", 0) or 0) for item in hunk_subpackets),
                    default=0,
                ),
                "path_summary_command": path_summary_command,
                "next_command": next_command,
                "review_rule": (
                    "Path slice is still over budget; follow next_command for the first deterministic hunk subpacket."
                    if hunk_subpackets
                    else "Review this path slice before loading broader owner or raw diff context."
                ),
            }
        )
    subpackets.sort(
        key=lambda item: (
            risk_rank.get(str(item.get("priority")), 9),
            -int(item.get("estimated_changed_tokens", 0) or 0),
            str(item.get("path", "")),
        )
    )
    return subpackets


def owner_review_packet(
    packet: dict[str, Any],
    owner: str,
    selected_paths: list[str] | None = None,
    selected_hunks: list[str] | None = None,
) -> dict[str, Any]:
    requested = owner.strip()
    owner_packets = packet.get("owner_review_packets") if isinstance(packet.get("owner_review_packets"), list) else []
    for item in owner_packets:
        if isinstance(item, dict) and item.get("owner") == requested:
            if selected_paths:
                result = owner_path_review_packet(packet, item, selected_paths, selected_hunks)
                if result.get("ok", result.get("status") != "path-not-found"):
                    result["navigation_status"] = packet.get("navigation_status", "unknown")
                    result["navigation_read_first"] = packet.get("navigation_read_first", "")
                    result["navigation_next_command"] = packet.get("navigation_next_command", "")
                return result
            result = dict(item)
            result["navigation_status"] = packet.get("navigation_status", "unknown")
            result["navigation_read_first"] = packet.get("navigation_read_first", "")
            result["navigation_next_command"] = packet.get("navigation_next_command", "")
            result["raw_changed_diff_estimated_tokens"] = packet.get(
                "changed_diff_estimated_tokens",
                result.get("estimated_changed_tokens", 0),
            )
            result["parent_changed_file_count"] = packet.get("changed_file_count", 0)
            result["parent_owner_review_packet_count"] = packet.get("owner_review_packet_count", len(owner_packets))
            result["cost_ledger"] = repo_cost_policy.review_cost_ledger(result)
            return result
    return {
        "schema_version": 1,
        "tool": "skill-manager.owner-review-packet",
        "ok": False,
        "status": "owner-not-found",
        "owner": requested,
        "available_owners": [str(item.get("owner")) for item in owner_packets if isinstance(item, dict)],
        "next_command": "python -B .agents/manage.py review-packet --summary --compact --format json",
    }


def large_diff_review_packet(
    root: Path,
    paths: list[str],
    validation_plan: list[dict[str, object]],
    navigation: dict[str, Any],
) -> dict[str, Any]:
    policy, policy_error = repo_cost_policy.load_cost_policy(root)
    routes = repo_cost_policy.task_routes(policy)
    review_budget = repo_cost_policy.int_field(routes.get("review", {}).get("max_context_tokens"), 5000)
    diff_estimate = repo_cost_policy.changed_diff_estimate(root)
    statuses = changed_file_statuses(root)
    untracked_tokens = int(diff_estimate.get("untracked_estimated_tokens", 0) or 0)
    estimated_tokens = int(diff_estimate.get("estimated_tokens", 0) or 0)
    tracked_tokens = int(
        diff_estimate.get(
            "tracked_estimated_tokens",
            max(0, estimated_tokens - untracked_tokens),
        )
        or 0
    )
    owner_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for path in paths:
        owner = review_owner(path)
        risk = review_risk(path)
        owner_counts[owner] = owner_counts.get(owner, 0) + 1
        risk_counts[risk] = risk_counts.get(risk, 0) + 1
        rows.append(
            {
                "path": path,
                "owner": owner,
                "risk": risk,
                "status": "".join(sorted(statuses.get(path, {"M"}))),
                "read": "inspect-before-raw-diff" if risk == "high" else "inspect-if-owner-relevant",
            }
        )
    risk_order = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda row: (risk_order.get(str(row.get("risk")), 9), str(row.get("owner")), str(row.get("path"))))
    required_commands = [
        str(item.get("command", ""))
        for item in validation_plan
        if isinstance(item, dict) and item.get("required") is not False and str(item.get("command", "")).strip()
    ]
    path_estimates = changed_path_token_estimates(root, paths)
    owner_packets = build_owner_review_packets(root, rows, path_estimates, required_commands, review_budget)
    owner_subpacket_count = sum(
        int(packet.get("owner_review_subpacket_count", 0) or 0)
        for packet in owner_packets
        if isinstance(packet, dict)
    )
    owner_review_commands = [str(packet.get("next_command")) for packet in owner_packets if packet.get("next_command")]
    owner_summary_commands = [
        str(packet.get("owner_summary_command"))
        for packet in owner_packets
        if packet.get("owner_summary_command")
    ]
    largest_owner_subpacket_tokens = max(
        (
            int(packet.get("largest_owner_subpacket_estimated_tokens", 0) or 0)
            for packet in owner_packets
            if isinstance(packet, dict)
        ),
        default=0,
    )
    owner_hunk_count = sum(
        int(packet.get("owner_review_hunk_count", 0) or 0)
        for packet in owner_packets
        if isinstance(packet, dict)
    )
    largest_owner_hunk_tokens = max(
        (
            int(packet.get("largest_owner_hunk_estimated_tokens", 0) or 0)
            for packet in owner_packets
            if isinstance(packet, dict)
        ),
        default=0,
    )
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.large-diff-review-packet",
        "status": "over-budget" if estimated_tokens > review_budget else "within-budget",
        "review_budget_tokens": review_budget,
        "changed_diff_estimated_tokens": estimated_tokens,
        "tracked_diff_estimated_tokens": tracked_tokens,
        "untracked_file_estimated_tokens": untracked_tokens,
        "tokens_over_review_budget": max(0, estimated_tokens - review_budget),
        "policy_error": policy_error or "",
        "changed_file_count": len(paths),
        "changed_groups": compact_path_groups(paths),
        "tracked_changed_file_count": diff_estimate.get("tracked_files", 0),
        "untracked_changed_file_count": diff_estimate.get("untracked_files", 0),
        "owner_counts": dict(sorted(owner_counts.items())),
        "owner_review_packet_count": len(owner_packets),
        "owner_review_packets": owner_packets,
        "owner_review_commands": owner_review_commands,
        "owner_summary_commands": owner_summary_commands,
        "owner_review_subpacket_count": owner_subpacket_count,
        "largest_owner_subpacket_estimated_tokens": largest_owner_subpacket_tokens,
        "owner_review_hunk_count": owner_hunk_count,
        "largest_owner_hunk_estimated_tokens": largest_owner_hunk_tokens,
        "next_review_command": owner_review_commands[0] if owner_review_commands else "",
        "risk_counts": dict(sorted(risk_counts.items())),
        "navigation_status": navigation.get("status", "unknown"),
        "navigation_read_first": navigation.get("read_first", ""),
        "navigation_next_command": navigation.get("next_command", ""),
        "read_first": rows[:12],
        "validation_first": required_commands[:8],
        "review_rule": "Read this packet and route files before raw diff; use owner packets for fresh-agent review.",
    }
    packet["cost_ledger"] = repo_cost_policy.review_cost_ledger(packet)
    return packet


def render_large_diff_review_packet(packet: dict[str, Any]) -> str:
    from repo_support import repo_review_packet

    return repo_review_packet.render_large_diff_review_packet(packet)


def review_packet_command(args: argparse.Namespace, root: Path) -> int:
    from repo_support import repo_review_packet

    return repo_review_packet.review_packet_command(args, root)


def handoff_packet_command(args: argparse.Namespace, root: Path) -> int:
    from repo_support import repo_review_packet

    return repo_review_packet.handoff_packet_command(args, root)


def fresh_agent_packet_command(args: argparse.Namespace, root: Path) -> int:
    from repo_support import repo_review_packet

    return repo_review_packet.fresh_agent_packet_command(args, root)


def changed_skill_self_tests(root: Path, skill_names: list[str]) -> dict[str, Path | None]:
    scripts: dict[str, Path | None] = {}
    for skill_name in skill_names:
        skill_dir = root / ".agents" / "skills" / str(skill_name)
        if not skill_dir.exists():
            continue
        script = skill_dir / "scripts" / "run_self_tests.py"
        scripts[skill_name] = script if script.exists() else None
    return scripts


def summarize_check_changed_payload(payload: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    from repo_support import repo_changed_summary

    return repo_changed_summary.summarize_check_changed_payload(payload, compact=compact)

def check_changed(args: argparse.Namespace, root: Path) -> int:
    command_started = time.perf_counter()
    record_progress = bool(getattr(args, "record_progress", False))

    def heartbeat(
        phase: str,
        status: str,
        *,
        completed: int = 0,
        total: int = 0,
        current: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not record_progress:
            payload: dict[str, Any] = {
                "status": status,
                "phase": phase,
                "path": "",
                "recorded": False,
                "completed": max(0, int(completed or 0)),
                "total": max(0, int(total or 0)),
                "elapsed_ms": repo_command_metrics.elapsed_ms_since(command_started),
            }
            if current:
                payload["current"] = current
            if extra:
                payload["extra"] = extra
            return payload
        try:
            progress = repo_command_metrics.write_validation_progress(
                root,
                command="check-changed",
                phase=phase,
                status=status,
                started=command_started,
                completed=completed,
                total=total,
                current=current,
                extra=extra,
            )
            progress["recorded"] = True
            return progress
        except Exception as exc:  # noqa: BLE001 - progress is advisory; validation still runs.
            return {
                "status": "blocked",
                "phase": phase,
                "path": repo_command_metrics.DEFAULT_VALIDATION_PROGRESS_PATH,
                "recorded": False,
                "issue": str(exc),
            }

    validation_progress = heartbeat("scan", "running")
    refresh_navigation = bool(getattr(args, "refresh_navigation", False))
    navigation_auto_refresh: dict[str, Any] = {
        "schema_version": 1,
        "tool": "repo-navigation.auto-refresh",
        "status": "skipped-read-only",
        "ok": True,
        "written": [],
        "summary": "Navigation auto-refresh skipped; pass --refresh-navigation to update generated maps.",
    }
    if refresh_navigation:
        navigation_auto_refresh = auto_refresh_navigation(root)
    navigation_auto_refresh_failed = refresh_navigation and not bool(navigation_auto_refresh.get("ok", True))
    paths = changed_files(root)
    navigation = navigation_status(root)

    if not paths:
        checks = []
        if navigation_auto_refresh_failed:
            checks.append(
                {
                    "name": "navigation auto-refresh gate",
                    "ok": False,
                    "output": str(navigation_auto_refresh.get("summary") or "navigation auto-refresh failed"),
                    "output_summary": repo_optimizations.compact_command_output(str(navigation_auto_refresh.get("summary") or "")),
                    "elapsed_ms": 0,
                }
            )
        if args.format == "json":
            total_elapsed_ms = repo_command_metrics.elapsed_ms_since(command_started)
            validation_progress = heartbeat(
                "complete",
                "failed" if navigation_auto_refresh_failed else "passed",
                completed=0,
                total=0,
            )
            timing_summary = {
                "check_count": len(checks),
                "total_elapsed_ms": total_elapsed_ms,
                "slowest_check": {"name": "", "elapsed_ms": 0},
            }
            payload = {
                "changed_files": [],
                "checks": checks,
                "navigation": navigation,
                "navigation_auto_refresh": navigation_auto_refresh,
                "status": "failed" if navigation_auto_refresh_failed else "passed",
                "validation_plan": [],
                "validation_plan_summary": {"command_count": 0, "required_count": 0, "optional_count": 0, "owners": {}},
                "validation_progress": validation_progress,
                "timing_summary": timing_summary,
                "latency_budget": repo_command_metrics.timing_budget_report(
                    "check-changed",
                    total_elapsed_ms,
                    timings=checks,
                ),
                "next_command_reason": (
                    "Navigation auto-refresh failed."
                    if navigation_auto_refresh_failed
                    else "No changed files; no changed-scope validation is required."
                ),
            }
            if (
                getattr(args, "summary", False)
                or getattr(args, "compact", False)
                or not bool(getattr(args, "full", False))
            ):
                payload = summarize_check_changed_payload(
                    payload,
                    compact=bool(getattr(args, "compact", False)) or not bool(getattr(args, "full", False)),
                )
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print("# Changed-Scope Check\n\nNo changed files detected.")
            print(f"\nNavigation maps: {navigation.get('status', 'unknown')} - {navigation.get('summary', '')}")
            if navigation_auto_refresh_failed:
                print(f"\nNavigation auto-refresh failed: {navigation_auto_refresh.get('summary', '')}")
            print("\nNext recommended command: none")
        return 1 if navigation_auto_refresh_failed else 0

    scope = changed_scope(paths)
    existing_paths = [path for path in paths if (root / path).is_file()]
    checks: list[CheckResult] = []
    skipped: list[str] = []
    json_mode = args.format == "json"
    skill_names = sorted(scope["skill_names"])
    self_test_scripts = changed_skill_self_tests(root, skill_names)
    validation_plan = repo_optimizations.changed_validation_plan(root, paths, scope, deep=bool(getattr(args, "deep", False)))
    plan_results: dict[str, bool] = {}

    def append_check(result: CheckResult, *, plan_command: str = "") -> None:
        checks.append(result)
        if plan_command:
            plan_results[plan_command] = bool(result[1])

    validation_progress = heartbeat(
        "plan",
        "running",
        completed=0,
        total=len(validation_plan),
        extra={"changed_file_count": len(paths), "deep": bool(getattr(args, "deep", False))},
    )
    input_fingerprint = repo_fingerprint.input_fingerprint_report(root, paths, validation_plan)
    review_packet = large_diff_review_packet(root, paths, validation_plan, navigation)
    review_plan = repo_review_progress.build_review_plan(review_packet)
    review_progress = repo_review_progress.summarize_review_progress(
        repo_review_progress.review_progress_report(root, review_plan, input_fingerprint=input_fingerprint)
    )
    started = time.perf_counter()
    addition_acceptance = addition_acceptance_report(root, paths=paths)
    append_check(
        (
            "addition acceptance gate",
            bool(addition_acceptance.get("ok")),
            render_addition_acceptance(
                addition_acceptance,
                verbose=bool(getattr(args, "verbose", False)) or not bool(addition_acceptance.get("ok")),
            ),
            elapsed_ms_since(started),
        ),
        plan_command="python -B .agents/manage.py check-additions",
    )
    started = time.perf_counter()
    proof_hygiene = repo_proof_hygiene.proof_hygiene_report(root, paths)
    append_check(
        (
            "proof hygiene gate",
            bool(proof_hygiene.get("ok")),
            repo_proof_hygiene.render_proof_hygiene(proof_hygiene),
            elapsed_ms_since(started),
        )
    )
    started = time.perf_counter()
    portability = repo_portability.portability_report(root, paths=paths)
    append_check(
        (
            "portable constraints gate",
            bool(portability.get("ok")),
            repo_portability.render_portability_report(portability),
            elapsed_ms_since(started),
        )
    )
    started = time.perf_counter()
    context_guardrails = repo_context_guardrails.context_guardrail_report(root, paths=paths)
    append_check(
        (
            "context guardrails gate",
            bool(context_guardrails.get("ok")),
            repo_context_guardrails.render_context_guardrail_report(
                context_guardrails,
                compact=not bool(getattr(args, "verbose", False)),
            ),
            elapsed_ms_since(started),
        )
    )
    syntax_check: dict[str, object] = {
        "schema_version": 1,
        "tool": "skill-manager.syntax-check",
        "ok": True,
        "status": "skipped",
        "checked": 0,
        "failed": 0,
        "bytecode_written": False,
        "paths": [],
        "issues": [],
    }
    python_paths = existing_changed_python_paths(
        root,
        [str(item) for item in scope.get("python_paths", []) if str(item).strip()],
    )
    if python_paths:
        started = time.perf_counter()
        syntax_check = repo_syntax.syntax_check_report(root, python_paths)
        syntax_command = next(
            (
                str(item.get("command"))
                for item in validation_plan
                if isinstance(item, dict) and str(item.get("command", "")).startswith("python -B .agents/manage.py syntax-check ")
            ),
            "",
        )
        append_check(
            (
                "python syntax gate",
                bool(syntax_check.get("ok")),
                repo_syntax.render_syntax_check_markdown(syntax_check, compact=True),
                elapsed_ms_since(started),
            ),
            plan_command=syntax_command,
        )

    if scope["instructions"]:
        instruction_command = "python -B .agents/manage.py sync-instructions --check"
        append_check(
            run_named_check(
                "instructions sync check",
                lambda: generated.sync_instructions(root, check=True),
            ),
            plan_command=instruction_command,
        )

    for skill_name in skill_names:
        skill_dir = root / ".agents" / "skills" / str(skill_name)
        if skill_dir.exists():
            if json_mode:
                skill_command = f"python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/{skill_name}"
                append_check(
                    run_named_script_check(
                        f"validate skill {skill_name}",
                        repo.skill_manager_script(root, "validate_skill.py"),
                        [str(skill_dir)],
                    ),
                    plan_command=skill_command,
                )
            else:
                skill_command = f"python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/{skill_name}"
                append_check(
                    run_named_check(
                        f"validate skill {skill_name}",
                        lambda skill_dir=skill_dir: repo.validate_skill_with_manager(root, skill_dir),
                    ),
                    plan_command=skill_command,
                )

    if skill_names or scope["skills_generated"]:
        if json_mode:
            append_check(
                run_named_script_check(
                    "skill routing/registry sync check",
                    repo.skill_manager_script(root, "sync_skill_routing.py"),
                    ["--root", str(root), "--check"],
                ),
                plan_command="python -B .agents/manage.py sync-skill-routing --check",
            )
        else:
            append_check(
                run_named_check(
                    "skill routing/registry sync check",
                    lambda: generated.sync_skill_routing(root, check=True),
                ),
                plan_command="python -B .agents/manage.py sync-skill-routing --check",
            )
        checks.append(
            run_named_check(
                "module schema sync check",
                lambda: generated.sync_module_schema(root, check=True),
            )
        )
        checks.append(
            run_named_check(
                "project policy schema sync check",
                lambda: generated.sync_project_policy_schema(root, check=True),
            )
        )
        append_check(
            run_named_check(
                "Claude adapter sync check",
                lambda: generated.sync_claude_skills(root, check=True),
            ),
            plan_command="python -B .agents/manage.py sync-claude-skills --check",
        )

    if getattr(args, "deep", False):
        for skill_name, script in self_test_scripts.items():
            if script is None:
                skipped.append(f"self-tests {skill_name} - no scripts/run_self_tests.py found")
                continue
            arguments: list[str] = []
            matches = repo_optimizations.focused_self_test_matches(str(skill_name), paths)
            for match in matches:
                arguments.extend(["--match", match])
            validation_progress = heartbeat(
                "self-tests",
                "running",
                completed=len(checks),
                total=len(validation_plan),
                current=str(skill_name),
                extra={
                    "focused": bool(matches),
                    "match_count": len(matches),
                    "script": repo.relative(root, script),
                },
            )
            started = time.perf_counter()
            status, output = repo.run_python_script_quiet(script, arguments)
            label = f"self-tests {skill_name}"
            if matches:
                label += " (focused)"
            self_test_command = repo_optimizations.self_test_command_for_skill(str(skill_name), existing_paths)
            append_check(
                (label, status == 0, output, elapsed_ms_since(started)),
                plan_command=self_test_command,
            )
    elif any(script is not None for script in self_test_scripts.values()):
        skipped.append("changed skill self-tests - pass --deep to run scripts/run_self_tests.py")

    if scope["workflows"] or scope["workflow_generated"]:
        if json_mode:
            append_check(
                run_named_script_check(
                    "automation validation",
                    repo.workflow_manager_script(root, "workflow_repo_manager.py"),
                    ["validate-automations", "--root", str(root), "--strict-phase-quality"],
                ),
                plan_command="python -B .agents/manage.py validate-automations --strict-phase-quality",
            )
            append_check(
                run_named_script_check(
                    "automation routing/registry sync check",
                    repo.workflow_manager_script(root, "workflow_repo_manager.py"),
                    ["sync-automation-routing", "--root", str(root), "--check"],
                ),
                plan_command="python -B .agents/manage.py sync-automation-routing --check",
            )
        else:
            append_check(
                run_named_script_check(
                    "automation validation",
                    repo.workflow_manager_script(root, "workflow_repo_manager.py"),
                    ["validate-automations", "--root", str(root), "--strict-phase-quality"],
                ),
                plan_command="python -B .agents/manage.py validate-automations --strict-phase-quality",
            )
            append_check(
                run_named_check(
                    "automation routing/registry sync check",
                    lambda: generated.sync_automation_routing(root, check=True),
                ),
                plan_command="python -B .agents/manage.py sync-automation-routing --check",
            )

    if scope["repo_surface"]:
        append_check(
            run_named_check("repository health", lambda: health.check_repo_health(root)),
            plan_command="python -B .agents/manage.py check-repo-health --json --summary --compact",
        )

    required_plan = [
        item for item in validation_plan if isinstance(item, dict) and item.get("required") is not False
    ]
    for item in required_plan:
        command = str(item.get("command") or "").strip()
        if not command or command in plan_results:
            continue
        append_check(
            run_planned_command_check(root, command),
            plan_command=command,
        )

    if navigation_auto_refresh_failed:
        checks.append(
            (
                "navigation auto-refresh gate",
                False,
                json.dumps(navigation_auto_refresh, indent=2, sort_keys=True),
                0,
            )
        )

    post_validation_fingerprint = repo_fingerprint.input_fingerprint_report(root, paths, validation_plan)
    input_stable = (
        bool(input_fingerprint.get("digest"))
        and input_fingerprint.get("digest") == post_validation_fingerprint.get("digest")
    )
    if not input_stable:
        checks.append(
            (
                "validation input stability gate",
                False,
                (
                    "Changed source, validation commands, dependency/config inputs, HEAD, or runtime identity "
                    "changed while validation was running; rerun check-changed on the final input."
                ),
                0,
            )
        )
    failed = [item for item in checks if not item[1]]
    required_check_ids = [str(item.get("check_id") or "") for item in required_plan if str(item.get("check_id") or "")]
    passed_check_ids = [
        str(item.get("check_id") or "")
        for item in required_plan
        if str(item.get("check_id") or "") and plan_results.get(str(item.get("command") or "")) is True
    ]
    total_elapsed_ms = repo_command_metrics.elapsed_ms_since(command_started)
    validation_progress = heartbeat(
        "complete",
        "failed" if failed else "passed",
        completed=len(checks),
        total=max(len(validation_plan), len(checks)),
        extra={
            "command_argv": [
                sys.executable,
                "-B",
                ".agents/manage.py",
                *sys.argv[1:],
            ],
            "failed_check_count": len(failed),
            "input_fingerprint_digest": input_fingerprint.get("digest", ""),
            "post_input_fingerprint_digest": post_validation_fingerprint.get("digest", ""),
            "input_stable": input_stable,
            "profile": "deep" if bool(getattr(args, "deep", False)) else "changed",
            "side_effect_boundary": "repository-read-only-and-temporary-restored",
            "required_check_ids": required_check_ids,
            "passed_check_ids": passed_check_ids,
            "skipped_count": len(skipped),
        },
    )
    timing_rows = [
        {"name": name, "ok": ok, "elapsed_ms": elapsed_ms}
        for name, ok, _output, elapsed_ms in checks
    ]
    timing_summary = {
        "check_count": len(checks),
        "total_elapsed_ms": total_elapsed_ms,
        "check_elapsed_ms": sum(int(item[3]) for item in checks),
        "slowest_check": max(
            (
                {"name": name, "elapsed_ms": elapsed_ms}
                for name, _ok, _output, elapsed_ms in checks
            ),
            key=lambda item: int(item["elapsed_ms"]),
            default={"name": "", "elapsed_ms": 0},
        ),
    }
    if args.format == "json":
        payload = {
            "profile": "deep" if bool(getattr(args, "deep", False)) else "changed",
            "changed_files": paths,
            "checks": [
                {
                    "name": name,
                    "ok": ok,
                    "output": output,
                    "output_summary": repo_optimizations.compact_command_output(output),
                    "elapsed_ms": elapsed_ms,
                }
                for name, ok, output, elapsed_ms in checks
            ],
            "skipped": skipped,
            "docs": scope["docs"],
            "unclassified": scope["other"],
            "addition_acceptance": addition_acceptance,
            "proof_hygiene": proof_hygiene,
            "portable_constraints": portability,
            "context_guardrails": context_guardrails,
            "navigation_auto_refresh": navigation_auto_refresh,
            "input_fingerprint": input_fingerprint,
            "post_validation_fingerprint": repo_fingerprint.summarize_input_fingerprint(
                post_validation_fingerprint
            ),
            "review_packet": review_packet,
            "review_progress": review_progress,
            "syntax_check": syntax_check,
            "validation_plan": validation_plan,
            "validation_plan_summary": repo_optimizations.validation_plan_summary(validation_plan),
            "validation_progress": validation_progress,
            "navigation": navigation,
            "timing_summary": timing_summary,
            "latency_budget": repo_command_metrics.timing_budget_report(
                "check-changed",
                total_elapsed_ms,
                timings=timing_rows,
            ),
            "next_command_reason": (
                "One or more changed-scope checks failed."
                if failed
                else (
                    "Run the first required validation command for the changed files."
                    if required_plan
                    else "Changed-scope checks passed; run the full repository check."
                )
            ),
            "status": "passed" if not failed else "failed",
        }
        if (
            getattr(args, "summary", False)
            or getattr(args, "compact", False)
            or not bool(getattr(args, "full", False))
        ):
            payload = summarize_check_changed_payload(
                payload,
                compact=bool(getattr(args, "compact", False)) or not bool(getattr(args, "full", False)),
            )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if not failed else 1

    print("# Changed-Scope Check")
    print()
    print(f"- Changed files: {len(paths)}")
    print(f"- Changed groups: {compact_path_groups(paths)}")
    print(f"- Navigation maps: {navigation.get('status', 'unknown')} - {navigation.get('summary', '')}")
    if navigation_auto_refresh.get("status") not in {"skipped", "skipped-fresh"}:
        print(f"- Navigation auto-refresh: {navigation_auto_refresh.get('status')} - {navigation_auto_refresh.get('summary', '')}")
    if review_packet.get("status") == "over-budget":
        print(
            "- Review packet: over budget "
            f"({review_packet.get('changed_diff_estimated_tokens', 0)} > "
            f"{review_packet.get('review_budget_tokens', 0)} tokens); read high-risk paths before raw diff."
        )
    if scope["docs"]:
        print(f"- Docs files: {len(scope['docs'])}")
    if scope["other"]:
        print(f"- Unclassified files: {len(scope['other'])}")
        if getattr(args, "verbose", False):
            for path in scope["other"]:
                print(f"  - `{path}`")
    print()
    print("## Validation Plan")
    if validation_plan:
        for item in validation_plan:
            optional = "optional" if item.get("required") is False else "required"
            print(f"- {item['order']}. `{item['command']}` ({optional}) - {item['reason']}")
    else:
        print("- No changed-scope validation commands detected.")
    if review_packet.get("status") == "over-budget":
        print()
        print("## Review Packet")
        print(f"- Owner counts: {review_packet.get('owner_counts', {})}")
        print(f"- Risk counts: {review_packet.get('risk_counts', {})}")
        print("- Read first:")
        for row in review_packet.get("read_first", [])[:12]:
            if isinstance(row, dict):
                print(f"  - `{row.get('path')}` ({row.get('owner')}; {row.get('risk')}; {row.get('status')})")
    print()
    print("## Checks")
    if not checks:
        print("- No active skill, workflow, generated, instruction, or manager checks matched these changes.")
    for name, ok, output, elapsed_ms in checks:
        print(f"- {name}: {'ok' if ok else 'failed'} ({elapsed_ms} ms)")
        if output and (getattr(args, "verbose", False) or not ok):
            print("  " + output.replace("\n", "\n  "))
    if skipped:
        print()
        print("## Skipped")
        for item in skipped:
            print(f"- Skipped: {item}")
    print()
    if failed:
        print("Next recommended command: fix failed changed-scope checks, then rerun `python -B .agents/manage.py check-changed`.")
    else:
        print("Next recommended command: `python -B .agents/manage.py check` before finalizing.")
    return 0 if not failed else 1


def check_additions(args: argparse.Namespace, root: Path) -> int:
    paths = changed_files(root)
    report = addition_acceptance_report(root, paths=paths)
    if getattr(args, "summary", False):
        report = {
            "schema_version": report.get("schema_version", 1),
            "tool": report.get("tool", "skill-manager.addition-acceptance"),
            "ok": bool(report.get("ok")),
            "status": report.get("status", ""),
            "summary": report.get("summary", {}),
            "issues": report.get("issues", []),
        }
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("# Addition Acceptance Gate")
        print()
        print(render_addition_acceptance(report, verbose=bool(getattr(args, "verbose", False))))
    return 0 if report.get("ok") else 1
