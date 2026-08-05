"""Review-packet rendering and CLI helpers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from repo_support import repo_changed
from repo_support import repo_common as repo
from repo_support import repo_cost_policy
from repo_support import repo_optimizations
from repo_support import repo_review_progress
from repo_support.repo_navigation_status import auto_refresh_navigation, navigation_status

build_review_plan = repo_review_progress.build_review_plan
summarize_review_plan = repo_review_progress.summarize_review_plan
build_review_cost_report = repo_review_progress.build_review_cost_report
summarize_review_cost_report = repo_review_progress.summarize_review_cost_report
render_review_plan = repo_review_progress.render_review_plan
render_review_cost_report = repo_review_progress.render_review_cost_report


def summarize_navigation_refresh(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report.get("status", "unknown"),
        "ok": bool(report.get("ok", True)),
        "written": report.get("written", []),
        "summary": report.get("summary", ""),
        "next_command": report.get("next_command", ""),
    }


def render_owner_review_packet(packet: dict[str, Any]) -> str:
    lines = [
        "# Owner Review Packet",
        "",
        f"- Owner: `{packet.get('owner', '')}`",
        f"- Scope: {packet.get('scope', 'owner')}",
        f"- Status: {packet.get('status', 'unknown')}",
        f"- Priority: {packet.get('priority', 'unknown')}",
        f"- Changed files: {packet.get('changed_file_count', 0)}",
        f"- Estimated changed tokens: {packet.get('estimated_changed_tokens', 0)}",
        f"- Review budget: {packet.get('review_budget_tokens', 0)}",
        f"- Tokens over budget: {packet.get('tokens_over_review_budget', 0)}",
    ]
    if packet.get("owner_summary_command"):
        lines.append(f"- Owner summary command: `{packet.get('owner_summary_command')}`")
    if packet.get("next_command"):
        lines.append(f"- Next command: `{packet.get('next_command')}`")
    if packet.get("navigation_read_first"):
        lines.append(f"- Source orientation: `{packet.get('navigation_read_first')}`")
    cost_ledger = packet.get("cost_ledger") if isinstance(packet.get("cost_ledger"), dict) else {}
    if cost_ledger:
        lines.extend(
            [
                f"- Raw diff estimate: {cost_ledger.get('raw_changed_diff_estimated_tokens', 0)} tokens",
                f"- First owner packet estimate: {cost_ledger.get('first_owner_packet_estimated_tokens', 0)} tokens",
                f"- Next review unit estimate: {cost_ledger.get('next_review_unit_estimated_tokens', 0)} tokens",
                f"- Largest review unit estimate: {cost_ledger.get('largest_review_unit_estimated_tokens', 0)} tokens",
                f"- Single-agent saved estimate: {cost_ledger.get('single_agent_saved_tokens_vs_raw_estimated', 0)} tokens "
                f"({cost_ledger.get('single_agent_saved_percent_vs_raw_estimated', 0.0)}%)",
            ]
        )
    risks = packet.get("risk_counts") if isinstance(packet.get("risk_counts"), dict) else {}
    if risks:
        lines.extend(["", "## Risks", ""])
        for risk, count in sorted(risks.items()):
            lines.append(f"- `{risk}`: {count}")
    read_first = packet.get("read_first") if isinstance(packet.get("read_first"), list) else []
    if read_first:
        lines.extend(["", "## Read First", ""])
        for row in read_first:
            if isinstance(row, dict):
                lines.append(f"- `{row.get('path')}` ({row.get('risk')}; {row.get('status')})")
    paths = packet.get("paths") if isinstance(packet.get("paths"), list) else []
    if paths:
        lines.extend(["", "## Paths", ""])
        for path in paths:
            lines.append(f"- `{path}`")
    validation = packet.get("validation_first") if isinstance(packet.get("validation_first"), list) else []
    if validation:
        lines.extend(["", "## Validate First", ""])
        for command in validation:
            lines.append(f"- `{command}`")
    subpackets = packet.get("owner_review_subpackets") if isinstance(packet.get("owner_review_subpackets"), list) else []
    if subpackets:
        lines.extend(["", "## Path Review Subpackets", ""])
        for row in subpackets[:12]:
            if isinstance(row, dict):
                lines.append(
                    f"- `{row.get('path')}`: {row.get('estimated_changed_tokens', 0)} tokens, "
                    f"{row.get('priority', 'unknown')} priority"
                )
                if row.get("next_command"):
                    lines.append(f"  Command: `{row.get('next_command')}`")
    hunks = packet.get("path_review_hunks") if isinstance(packet.get("path_review_hunks"), list) else []
    if hunks:
        lines.extend(["", "## Hunk Review Subpackets", ""])
        for row in hunks[:12]:
            if isinstance(row, dict):
                lines.append(
                    f"- `{row.get('hunk')}` `{row.get('range')}`: "
                    f"{row.get('estimated_changed_tokens', 0)} tokens"
                )
                if row.get("next_command"):
                    lines.append(f"  Command: `{row.get('next_command')}`")
    lines.extend(["", f"Rule: {packet.get('review_rule', '')}", ""])
    return "\n".join(lines)


def render_large_diff_review_packet(packet: dict[str, Any]) -> str:
    if packet.get("tool") == "skill-manager.owner-review-packet":
        return render_owner_review_packet(packet)
    lines = [
        "# Large Diff Review Packet",
        "",
        f"- Status: {packet.get('status', 'unknown')}",
        f"- Changed files: {packet.get('changed_file_count', 0)}",
        f"- Changed groups: {packet.get('changed_groups', '')}",
        f"- Estimated changed tokens: {packet.get('changed_diff_estimated_tokens', 0)}",
        f"- Review budget: {packet.get('review_budget_tokens', 0)}",
        f"- Tokens over budget: {packet.get('tokens_over_review_budget', 0)}",
        f"- Navigation: {packet.get('navigation_status', 'unknown')}",
    ]
    if packet.get("navigation_read_first"):
        lines.append(f"- Read first: `{packet.get('navigation_read_first')}`")
    cost_ledger = packet.get("cost_ledger") if isinstance(packet.get("cost_ledger"), dict) else {}
    if cost_ledger:
        lines.extend(
            [
                f"- Largest owner packet estimate: {cost_ledger.get('largest_owner_packet_estimated_tokens', 0)} tokens",
                f"- Largest owner subpacket estimate: {cost_ledger.get('largest_owner_subpacket_estimated_tokens', 0)} tokens",
                f"- Largest owner hunk estimate: {cost_ledger.get('largest_owner_hunk_estimated_tokens', 0)} tokens",
                f"- Next review unit estimate: {cost_ledger.get('next_review_unit_estimated_tokens', 0)} tokens",
                f"- Single-agent saved estimate: {cost_ledger.get('single_agent_saved_tokens_vs_raw_estimated', 0)} tokens "
                f"({cost_ledger.get('single_agent_saved_percent_vs_raw_estimated', 0.0)}%)",
                f"- Cost boundary: {cost_ledger.get('billing_scope', 'input-context-estimate-only')}",
            ]
        )
    lines.extend(["", "## Owners", ""])
    owners = packet.get("owner_counts") if isinstance(packet.get("owner_counts"), dict) else {}
    if owners:
        for owner, count in sorted(owners.items()):
            lines.append(f"- `{owner}`: {count}")
    else:
        lines.append("- None.")
    lines.extend(["", "## Risks", ""])
    risks = packet.get("risk_counts") if isinstance(packet.get("risk_counts"), dict) else {}
    if risks:
        for risk, count in sorted(risks.items()):
            lines.append(f"- `{risk}`: {count}")
    else:
        lines.append("- None.")
    read_first = packet.get("read_first") if isinstance(packet.get("read_first"), list) else []
    if read_first:
        lines.extend(["", "## Read First", ""])
        for row in read_first:
            if isinstance(row, dict):
                lines.append(f"- `{row.get('path')}` ({row.get('owner')}; {row.get('risk')}; {row.get('status')})")
    validation = packet.get("validation_first") if isinstance(packet.get("validation_first"), list) else []
    if validation:
        lines.extend(["", "## Validate First", ""])
        for command in validation:
            lines.append(f"- `{command}`")
    owner_packets = packet.get("owner_review_packets") if isinstance(packet.get("owner_review_packets"), list) else []
    if owner_packets:
        lines.extend(["", "## Owner Review Slices", ""])
        for row in owner_packets[:12]:
            if isinstance(row, dict):
                lines.append(
                    f"- `{row.get('owner')}`: {row.get('changed_file_count', 0)} files, "
                    f"{row.get('estimated_changed_tokens', 0)} tokens, {row.get('priority')} priority"
                )
                if row.get("owner_review_subpacket_count"):
                    lines.append(
                        f"  Subpackets: {row.get('owner_review_subpacket_count')} "
                        f"(largest {row.get('largest_owner_subpacket_estimated_tokens', 0)} tokens)"
                    )
                if row.get("owner_review_hunk_count"):
                    lines.append(
                        f"  Hunks: {row.get('owner_review_hunk_count')} "
                        f"(largest {row.get('largest_owner_hunk_estimated_tokens', 0)} tokens)"
                    )
                if row.get("next_command"):
                    lines.append(f"  Command: `{row.get('next_command')}`")
    if packet.get("artifacts"):
        lines.extend(["", "## Artifacts", ""])
        for artifact in packet.get("artifacts", []):
            lines.append(f"- `{artifact}`")
    lines.extend(["", f"Rule: {packet.get('review_rule', '')}", ""])
    return "\n".join(lines)


def safe_repo_output_dir(root: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise SystemExit("output path must stay inside the repository") from exc
    return resolved


def write_review_packet(root: Path, packet: dict[str, Any], output_dir: str) -> list[str]:
    target = safe_repo_output_dir(root, output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "review-packet.json"
    md_path = target / "review-packet.md"
    plan_path = target / "review-plan.json"
    plan_md_path = target / "review-plan.md"
    cost_path = target / "review-cost-ledger.json"
    cost_md_path = target / "review-cost-ledger.md"
    artifacts = [
        repo.relative(root, json_path),
        repo.relative(root, md_path),
        repo.relative(root, plan_path),
        repo.relative(root, plan_md_path),
        repo.relative(root, cost_path),
        repo.relative(root, cost_md_path),
    ]
    owner_packets = packet.get("owner_review_packets") if isinstance(packet.get("owner_review_packets"), list) else []
    if owner_packets:
        owner_dir = target / "owners"
        owner_dir.mkdir(parents=True, exist_ok=True)
        for owner_packet in owner_packets:
            if not isinstance(owner_packet, dict):
                continue
            slug = repo_changed.owner_review_slug(str(owner_packet.get("owner") or "owner"))
            owner_json_path = owner_dir / f"{slug}.json"
            owner_md_path = owner_dir / f"{slug}.md"
            owner_artifacts = [repo.relative(root, owner_json_path), repo.relative(root, owner_md_path)]
            owner_packet.setdefault("artifacts", [])
            if isinstance(owner_packet["artifacts"], list):
                owner_packet["artifacts"].extend(owner_artifacts)
            owner_json_path.write_text(
                json.dumps(owner_packet, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            owner_md_path.write_text(render_owner_review_packet(owner_packet), encoding="utf-8", newline="\n")
            artifacts.extend(owner_artifacts)
    packet.setdefault("artifacts", [])
    if isinstance(packet["artifacts"], list):
        packet["artifacts"].extend(artifacts)
    review_plan = build_review_plan(packet)
    cost_report = build_review_cost_report(packet)
    packet["review_plan_summary"] = summarize_review_plan(review_plan)
    packet["review_cost_report"] = summarize_review_cost_report(cost_report)
    json_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    md_path.write_text(render_large_diff_review_packet(packet), encoding="utf-8", newline="\n")
    plan_path.write_text(json.dumps(review_plan, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    plan_md_path.write_text(render_review_plan(review_plan), encoding="utf-8", newline="\n")
    cost_path.write_text(json.dumps(cost_report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    cost_md_path.write_text(render_review_cost_report(cost_report), encoding="utf-8", newline="\n")
    return artifacts


def summarize_packet(packet: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    navigation_auto_refresh = packet.get("navigation_auto_refresh")
    refresh_summary = (
        summarize_navigation_refresh(navigation_auto_refresh)
        if isinstance(navigation_auto_refresh, dict)
        else {}
    )
    if packet.get("tool") == "skill-manager.owner-review-packet":
        summary = {
            "schema_version": packet.get("schema_version", 1),
            "tool": packet.get("tool", "skill-manager.owner-review-packet"),
            "status": packet.get("status", "unknown"),
            "ok": bool(packet.get("ok", packet.get("status") != "blocked")),
            "owner": packet.get("owner", ""),
            "scope": packet.get("scope", "owner"),
            "priority": packet.get("priority", ""),
            "changed_file_count": packet.get("changed_file_count", 0),
            "estimated_changed_tokens": packet.get("estimated_changed_tokens", 0),
            "review_budget_tokens": packet.get("review_budget_tokens", 0),
            "tokens_over_review_budget": packet.get("tokens_over_review_budget", 0),
            "risk_counts": packet.get("risk_counts", {}),
            "read_first": packet.get("read_first", []),
            "paths": packet.get("paths", []),
            "selected_paths": packet.get("selected_paths", []),
            "selected_hunks": packet.get("selected_hunks", []),
            "selected_ranges": packet.get("selected_ranges", []),
            "available_hunk_count": packet.get("available_hunk_count", 0),
            "remaining_hunk_count": packet.get("remaining_hunk_count", 0),
            "skipped_hunk_gap_count": packet.get("skipped_hunk_gap_count", 0),
            "next_hunk_command": packet.get("next_hunk_command", ""),
            "owner_summary_command": packet.get("owner_summary_command", ""),
            "owner_review_subpacket_count": packet.get("owner_review_subpacket_count", 0),
            "owner_review_subpacket_commands": packet.get("owner_review_subpacket_commands", [])[:8],
            "owner_review_hunk_count": packet.get("owner_review_hunk_count", 0),
            "largest_owner_hunk_estimated_tokens": packet.get("largest_owner_hunk_estimated_tokens", 0),
            "largest_owner_subpacket_estimated_tokens": packet.get("largest_owner_subpacket_estimated_tokens", 0),
            "path_review_hunk_count": packet.get("path_review_hunk_count", 0),
            "path_review_hunk_commands": packet.get("path_review_hunk_commands", [])[:8],
            "largest_path_hunk_estimated_tokens": packet.get("largest_path_hunk_estimated_tokens", 0),
            "parent_owner_changed_file_count": packet.get("parent_owner_changed_file_count", 0),
            "parent_owner_estimated_changed_tokens": packet.get("parent_owner_estimated_changed_tokens", 0),
            "validation_first": repo_changed.summarize_validation_commands(packet.get("validation_first", [])),
            "available_owners": packet.get("available_owners", []),
            "available_paths": packet.get("available_paths", []),
            "missing_paths": packet.get("missing_paths", []),
            "navigation_status": packet.get("navigation_status", "unknown"),
            "navigation_read_first": packet.get("navigation_read_first", ""),
            "navigation_next_command": packet.get("navigation_next_command", ""),
            "cost_ledger": repo_cost_policy.compact_review_cost_ledger(packet.get("cost_ledger", {}))
            if compact
            else packet.get("cost_ledger", {}),
            "review_plan_summary": packet.get("review_plan_summary", {}),
            "review_cost_report": packet.get("review_cost_report", {}),
            "artifacts": packet.get("artifacts", []),
            "next_command": packet.get("next_command", ""),
            "review_rule": packet.get("review_rule", ""),
        }
        if refresh_summary:
            summary["navigation_auto_refresh"] = refresh_summary
        return summary
    owner_packets = packet.get("owner_review_packets", [])
    if compact and isinstance(owner_packets, list):
        owner_packets = repo_changed.summarize_owner_review_packets(owner_packets)
    summary = {
        "schema_version": packet.get("schema_version", 1),
        "tool": packet.get("tool", "skill-manager.large-diff-review-packet"),
        "status": packet.get("status", "unknown"),
        "ok": bool(packet.get("ok", packet.get("status") != "blocked")),
        "changed_file_count": packet.get("changed_file_count", 0),
        "changed_groups": packet.get("changed_groups", ""),
        "review_budget_tokens": packet.get("review_budget_tokens", 0),
        "changed_diff_estimated_tokens": packet.get("changed_diff_estimated_tokens", 0),
        "tracked_diff_estimated_tokens": packet.get("tracked_diff_estimated_tokens", 0),
        "untracked_file_estimated_tokens": packet.get("untracked_file_estimated_tokens", 0),
        "tokens_over_review_budget": packet.get("tokens_over_review_budget", 0),
        "owner_counts": packet.get("owner_counts", {}),
        "owner_review_packet_count": packet.get("owner_review_packet_count", 0),
        "owner_review_subpacket_count": packet.get("owner_review_subpacket_count", 0),
        "largest_owner_subpacket_estimated_tokens": packet.get("largest_owner_subpacket_estimated_tokens", 0),
        "owner_review_hunk_count": packet.get("owner_review_hunk_count", 0),
        "largest_owner_hunk_estimated_tokens": packet.get("largest_owner_hunk_estimated_tokens", 0),
        "owner_review_packets": owner_packets[:8] if isinstance(owner_packets, list) else [],
        "owner_review_commands": packet.get("owner_review_commands", [])[:8],
        "owner_summary_commands": packet.get("owner_summary_commands", [])[:8],
        "risk_counts": packet.get("risk_counts", {}),
        "navigation_status": packet.get("navigation_status", "unknown"),
        "navigation_read_first": packet.get("navigation_read_first", ""),
        "navigation_next_command": packet.get("navigation_next_command", ""),
        "cost_ledger": repo_cost_policy.compact_review_cost_ledger(packet.get("cost_ledger", {}))
        if compact
        else packet.get("cost_ledger", {}),
        "review_plan_summary": packet.get("review_plan_summary", {}),
        "review_cost_report": packet.get("review_cost_report", {}),
        "read_first": packet.get("read_first", []),
        "validation_first": repo_changed.summarize_validation_commands(packet.get("validation_first", [])),
        "artifacts": packet.get("artifacts", []),
        "next_review_command": packet.get("next_review_command", ""),
        "next_command": packet.get("next_command", ""),
        "review_rule": packet.get("review_rule", ""),
    }
    if refresh_summary:
        summary["navigation_auto_refresh"] = refresh_summary
    return summary


def apply_navigation_refresh_gate(packet: dict[str, Any], refresh: dict[str, Any]) -> dict[str, Any]:
    packet["navigation_auto_refresh"] = summarize_navigation_refresh(refresh)
    if bool(refresh.get("ok", True)):
        return packet
    packet["ok"] = False
    packet["status"] = "blocked"
    packet["next_command"] = str(
        refresh.get("next_command")
        or "python -B .agents/skills/repo-navigation/scripts/repo_navigation.py check --target . --format json"
    )
    if refresh.get("status") == "blocked-read-only":
        packet["review_rule"] = str(
            refresh.get("summary")
            or "Navigation maps are not fresh; run the next command before review-packet."
        )
    else:
        packet["review_rule"] = "Navigation refresh failed; fix map freshness before broad source review."
    return packet


def read_only_navigation_preflight(navigation: dict[str, Any]) -> dict[str, Any]:
    status = str(navigation.get("status", "unknown") or "unknown")
    fresh = status == "fresh"
    return {
        "schema_version": 1,
        "tool": "repo-navigation.read-only-preflight",
        "ok": fresh,
        "status": "skipped-fresh" if fresh else "blocked-read-only",
        "written": [],
        "before": navigation,
        "summary": (
            "Navigation maps are fresh; no generated map writes needed."
            if fresh
            else f"Navigation maps are {status}; review-packet is read-only without --write."
        ),
        "next_command": navigation.get("next_command", "")
        or "python -B .agents/skills/repo-navigation/scripts/repo_navigation.py check --target . --format json",
    }


def build_review_packet(
    root: Path,
    *,
    deep: bool = False,
    owner: str = "",
    selected_paths: list[str] | None = None,
    selected_hunks: list[str] | None = None,
    refresh_navigation: bool = True,
) -> dict[str, Any]:
    paths = repo_changed.changed_files(root)
    navigation = navigation_status(root)
    if refresh_navigation:
        refresh = auto_refresh_navigation(root)
        if bool(refresh.get("ok", True)):
            navigation = navigation_status(root)
    else:
        refresh = read_only_navigation_preflight(navigation)
    scope = repo_changed.changed_scope(paths) if paths else {}
    validation_plan = repo_optimizations.changed_validation_plan(
        root,
        paths,
        scope,
        deep=deep,
    ) if paths else []
    packet = repo_changed.large_diff_review_packet(root, paths, validation_plan, navigation)
    policy, _policy_error = repo_cost_policy.load_cost_policy(root)
    review_loop = repo_cost_policy.review_loop_policy(policy)
    packet.setdefault("review_batch_max_hunks", review_loop["max_hunks_per_batch"])
    if owner:
        packet = repo_changed.owner_review_packet(
            packet,
            owner,
            selected_paths=selected_paths,
            selected_hunks=selected_hunks,
        )
        packet.setdefault("review_batch_max_hunks", review_loop["max_hunks_per_batch"])
    return apply_navigation_refresh_gate(packet, refresh)


def handoff_packet(root: Path, *, owner: str = "") -> dict[str, Any]:
    packet = build_review_packet(root, owner=owner, refresh_navigation=True)
    navigation = {
        "status": packet.get("navigation_status", "unknown"),
        "read_first": packet.get("navigation_read_first", ""),
        "next_command": packet.get("navigation_next_command", ""),
    }
    validation = repo_changed.summarize_validation_commands(packet.get("validation_first", []))
    owner_commands = packet.get("owner_review_commands") if isinstance(packet.get("owner_review_commands"), list) else []
    if packet.get("tool") == "skill-manager.owner-review-packet":
        status = "owner-review"
        if packet.get("status") == "over-budget" and (
            packet.get("owner_review_subpacket_count") or packet.get("path_review_hunk_count")
        ):
            status = "needs-owner-subpacket-review"
            next_command = str(packet.get("next_command") or "")
        else:
            next_command = validation[0] if validation else packet.get("next_command", "")
        read_first = packet.get("read_first", [])
        paths = packet.get("paths", [])
        owner_packets: list[dict[str, Any]] = []
    else:
        status = "needs-owner-review" if packet.get("status") == "over-budget" else "ready-for-validation"
        next_command = owner_commands[0] if owner_commands else (validation[0] if validation else "python -B .agents/manage.py check-changed --summary --compact --format json")
        read_first = packet.get("read_first", [])
        paths = []
        owner_packets = (
            repo_changed.summarize_owner_review_packets(packet.get("owner_review_packets", []))
            if isinstance(packet.get("owner_review_packets"), list)
            else []
        )
    if packet.get("status") == "blocked":
        status = "blocked"
        next_command = packet.get("next_command", "")
    route_first = [
        "AGENTS.md",
        ".agents/routing.md",
        "automations/routing.md",
    ]
    if navigation.get("read_first"):
        route_first.append(str(navigation["read_first"]))
    return {
        "schema_version": 1,
        "tool": "skill-manager.handoff-packet",
        "ok": status not in {"blocked"},
        "status": status,
        "owner": owner,
        "changed_file_count": packet.get("changed_file_count", 0),
        "changed_groups": packet.get("changed_groups", ""),
        "route_first": route_first,
        "navigation": navigation,
        "navigation_auto_refresh": packet.get("navigation_auto_refresh", {}),
        "review_packet": summarize_packet(packet, compact=True),
        "owner_review_packets": owner_packets[:8],
        "owner_review_commands": owner_commands[:8],
        "read_first": read_first,
        "paths": paths,
        "validation_first": validation,
        "cost_ledger": packet.get("cost_ledger", {}),
        "cost_measurement": {
            "changed_diff_estimated_tokens": packet.get("changed_diff_estimated_tokens", packet.get("estimated_changed_tokens", 0)),
            "review_budget_tokens": packet.get("review_budget_tokens", 0),
            "tokens_over_review_budget": packet.get("tokens_over_review_budget", 0),
            "measurement": "git diff and untracked text-size estimate; validate broad guidance with startup-context",
        },
        "raw_navigation_json": "tool-only",
        "next_command": next_command,
    }


def summarize_handoff_packet(packet: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    if not compact:
        return packet
    summary = dict(packet)
    summary["cost_ledger"] = repo_cost_policy.compact_review_cost_ledger(summary.get("cost_ledger", {}))
    review = summary.get("review_packet")
    if isinstance(review, dict):
        review.pop("read_first", None)
        review.pop("validation_first", None)
        review["cost_ledger"] = repo_cost_policy.compact_review_cost_ledger(review.get("cost_ledger", {}))
    if not summary.get("owner_review_packets"):
        summary.pop("owner_review_packets", None)
    if not summary.get("paths"):
        summary.pop("paths", None)
    return summary


def fresh_agent_next_command(root: Path, handoff: dict[str, Any], *, owner: str = "") -> tuple[str, str]:
    fallback = str(handoff.get("next_command") or "python -B .agents/manage.py next-action --summary --compact --format json")
    review = handoff.get("review_packet") if isinstance(handoff.get("review_packet"), dict) else {}
    if not owner and (review.get("status") == "over-budget" or handoff.get("status") == "needs-owner-review"):
        policy, _policy_error = repo_cost_policy.load_cost_policy(root)
        review_loop = repo_cost_policy.review_loop_policy(policy)
        return (
            repo_review_progress.default_review_loop_command(
                max_units=review_loop["max_units"],
                max_estimated_tokens=review_loop["max_estimated_tokens"],
                max_elapsed_ms=review_loop["max_elapsed_ms"],
            ),
            "review-loop-autopilot",
        )
    return fallback, "handoff"


def fresh_agent_packet(root: Path, *, owner: str = "") -> dict[str, Any]:
    handoff = handoff_packet(root, owner=owner)
    navigation = handoff.get("navigation") if isinstance(handoff.get("navigation"), dict) else {}
    source_orientation = str(navigation.get("read_first") or "automations/navigation/artifacts/maps/HANDOFF.md")
    next_command, next_command_source = fresh_agent_next_command(root, handoff, owner=owner)
    route_first = [
        "AGENTS.md",
        "python -B .agents/manage.py startup-context --summary --compact --format json",
        source_orientation,
        ".agents/routing.md",
        "automations/routing.md",
    ]
    return {
        "schema_version": 1,
        "tool": "skill-manager.fresh-agent-packet",
        "ok": bool(handoff.get("ok", True)),
        "status": handoff.get("status", "unknown"),
        "owner": owner,
        "source_orientation_file": source_orientation,
        "route_first": list(dict.fromkeys(route_first)),
        "read_only_commands": [
            "python -B .agents/manage.py startup-context --summary --compact --format json",
            "python -B .agents/manage.py next-action --summary --compact --format json",
            "python -B .agents/manage.py changed-context --summary --compact --format json",
        ],
        "tool_only_inputs": [
            "automations/navigation/artifacts/maps/handoff.json",
            "automations/navigation/artifacts/maps/staleness.json",
            "raw navigation JSON",
        ],
        "local_ai_route": {
            "status": "advisory-only",
            "allowed_use_cases": ["validation-triage", "changed-files-summary", "handoff-draft"],
            "must_not_decide": ["correctness", "completion", "merge readiness"],
            "fallback": "deterministic launcher commands, direct source reads, and validation evidence",
        },
        "handoff": summarize_handoff_packet(handoff, compact=True),
        "next_command": next_command,
        "next_command_source": next_command_source,
        "stop_conditions": [
            "navigation status is not fresh",
            "next command fails",
            "required context is missing",
            "raw navigation JSON would be needed for model context",
        ],
        "boundary": (
            "This packet is for fresh sessions and subagents. Load the listed human files and command output; "
            "do not load raw generated navigation JSON."
        ),
    }


def summarize_fresh_agent_packet(packet: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    output = {
        "schema_version": packet.get("schema_version", 1),
        "tool": packet.get("tool", "skill-manager.fresh-agent-packet"),
        "ok": bool(packet.get("ok", True)),
        "status": packet.get("status", "unknown"),
        "owner": packet.get("owner", ""),
        "source_orientation_file": packet.get("source_orientation_file", ""),
        "route_first": packet.get("route_first", []),
        "read_only_commands": packet.get("read_only_commands", []),
        "tool_only_inputs": packet.get("tool_only_inputs", []),
        "local_ai_route": packet.get("local_ai_route", {}),
        "next_command": packet.get("next_command", ""),
        "next_command_source": packet.get("next_command_source", ""),
        "stop_conditions": packet.get("stop_conditions", []),
        "boundary": packet.get("boundary", ""),
    }
    if not compact:
        output["handoff"] = packet.get("handoff", {})
    return output


def review_packet_command(args: argparse.Namespace, root: Path) -> int:
    owner = str(getattr(args, "owner", "") or "").strip()
    selected_paths = [
        repo_changed.normalize_review_path(str(path), root=root)
        for path in (getattr(args, "paths", None) or [])
        if str(path).strip()
    ]
    selected_hunks = [
        str(hunk).strip()
        for hunk in (getattr(args, "hunks", None) or [])
        if str(hunk).strip()
    ]
    if selected_paths and not owner:
        packet = {
            "schema_version": 1,
            "tool": "skill-manager.owner-review-packet",
            "ok": False,
            "status": "path-slice-requires-owner",
            "requested_paths": selected_paths,
            "next_command": "python -B .agents/manage.py review-packet --summary --compact --format json",
            "review_rule": "--path narrows an owner packet; pass --owner with each --path selection.",
        }
        if getattr(args, "format", "markdown") == "json":
            print(json.dumps(packet, indent=2, sort_keys=True))
        else:
            print(render_large_diff_review_packet(packet))
        return 1
    if selected_hunks and not selected_paths:
        packet = {
            "schema_version": 1,
            "tool": "skill-manager.owner-review-packet",
            "ok": False,
            "status": "hunk-slice-requires-path",
            "requested_hunks": selected_hunks,
            "next_command": "python -B .agents/manage.py review-packet --summary --compact --format json",
            "review_rule": "--hunk narrows a selected path; pass --owner and --path with each --hunk selection.",
        }
        if getattr(args, "format", "markdown") == "json":
            print(json.dumps(packet, indent=2, sort_keys=True))
        else:
            print(render_large_diff_review_packet(packet))
        return 1
    packet = build_review_packet(
        root,
        deep=bool(getattr(args, "deep", False)),
        owner=owner,
        selected_paths=selected_paths,
        selected_hunks=selected_hunks,
        refresh_navigation=bool(getattr(args, "write_dir", None)),
    )
    if getattr(args, "write_dir", None):
        write_review_packet(root, packet, str(args.write_dir))
    if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
        packet = summarize_packet(packet, compact=bool(getattr(args, "compact", False)))
    if getattr(args, "format", "markdown") == "json":
        print(json.dumps(packet, indent=2, sort_keys=True))
    else:
        print(render_large_diff_review_packet(packet))
    return 0 if packet.get("status") not in {"owner-not-found", "path-not-found", "hunk-not-found", "blocked"} else 1


def handoff_packet_command(args: argparse.Namespace, root: Path) -> int:
    owner = str(getattr(args, "owner", "") or "").strip()
    packet = handoff_packet(root, owner=owner)
    if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
        packet = summarize_handoff_packet(packet, compact=bool(getattr(args, "compact", False)))
    if getattr(args, "format", "markdown") == "json":
        print(json.dumps(packet, indent=2, sort_keys=True))
    else:
        lines = [
            "# Handoff Packet",
            "",
            f"- Status: {packet.get('status', 'unknown')}",
            f"- Changed files: {packet.get('changed_file_count', 0)}",
            f"- Next command: `{packet.get('next_command', '')}`",
            f"- Raw navigation JSON: {packet.get('raw_navigation_json', 'tool-only')}",
            "",
            "## Route First",
            "",
        ]
        for path in packet.get("route_first", []):
            lines.append(f"- `{path}`")
        print("\n".join(lines))
    return 0 if packet.get("ok", True) else 1


def fresh_agent_packet_command(args: argparse.Namespace, root: Path) -> int:
    owner = str(getattr(args, "owner", "") or "").strip()
    packet = fresh_agent_packet(root, owner=owner)
    if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
        packet = summarize_fresh_agent_packet(packet, compact=bool(getattr(args, "compact", False)))
    if getattr(args, "format", "markdown") == "json":
        print(json.dumps(packet, indent=2, sort_keys=True))
    else:
        lines = [
            "# Fresh Agent Packet",
            "",
            f"- Status: {packet.get('status', 'unknown')}",
            f"- Source orientation: `{packet.get('source_orientation_file', '')}`",
            f"- Next command: `{packet.get('next_command', '')}`",
            "",
            "## Route First",
            "",
        ]
        for path in packet.get("route_first", []):
            lines.append(f"- `{path}`")
        print("\n".join(lines))
    return 0 if packet.get("ok", True) else 1
