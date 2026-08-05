"""Review cost reports, budget trends, and review-plan rendering."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repo_support import repo_cost_policy
from repo_support import repo_policy

DEFAULT_BUDGET_TREND_PATH = ".agents/local-ai/cache/budget-trend-ledger.jsonl"
OUTPUT_PRICE_MULTIPLIERS = (1, 2, 4, 8)


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int_value(value: object, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def build_review_cost_report(packet: dict[str, Any]) -> dict[str, Any]:
    from repo_support import repo_review_progress

    plan = repo_review_progress.build_review_plan(packet)
    ledger = _dict(plan.get("cost_ledger")) or repo_cost_policy.compact_review_cost_ledger(_dict(packet.get("cost_ledger")))
    next_saved = int(ledger.get("next_review_unit_saved_tokens_vs_raw_estimated", 0) or 0)
    all_units_delta = int(ledger.get("all_review_units_delta_tokens_vs_raw_estimated", 0) or 0)
    full_review_saved = max(0, -all_units_delta)
    money_saving_estimate = build_money_saving_estimate(
        input_tokens_saved=next_saved,
        full_review_input_tokens_saved=full_review_saved,
        root=repo_policy.project_root(),
    )
    return {
        "schema_version": 1,
        "tool": "skill-manager.review-cost-report",
        "status": ledger.get("status", "unknown"),
        "billing_scope": ledger.get("billing_scope", "input-context-estimate-only"),
        "measurement": "estimated input-context tokens only",
        "token_counter": "git_numstat_lines_x12_plus_untracked_bytes_div_4",
        "raw_changed_diff_estimated_tokens": ledger.get("raw_changed_diff_estimated_tokens", 0),
        "next_review_unit_estimated_tokens": ledger.get("next_review_unit_estimated_tokens", 0),
        "largest_review_unit_estimated_tokens": ledger.get("largest_review_unit_estimated_tokens", 0),
        "review_unit_count": ledger.get("review_unit_count", 0),
        "review_units_estimated_tokens_total": ledger.get("review_units_estimated_tokens_total", 0),
        "next_review_unit_saved_tokens_vs_raw_estimated": next_saved,
        "next_review_unit_saved_percent_vs_raw_estimated": ledger.get("next_review_unit_saved_percent_vs_raw_estimated", 0.0),
        "full_review_saved_tokens_vs_raw_estimated": full_review_saved,
        "all_review_units_delta_tokens_vs_raw_estimated": all_units_delta,
        "break_even_extra_output_tokens": {
            "output_price_multiplier_1x": next_saved,
            "output_price_multiplier_2x": next_saved // 2,
            "output_price_multiplier_4x": next_saved // 4,
            "output_price_multiplier_8x": next_saved // 8,
        },
        "money_saving_status": (
            "potential-full-review-input-saving"
            if full_review_saved > 0
            else "next-unit-context-saving-only"
            if next_saved > 0
            else "not-proven"
        ),
        "money_saving_estimate": money_saving_estimate,
        "boundary": [
            "Does not include output tokens, reasoning tokens, hidden prompts, cache discounts, or provider prices.",
            "Extra routed review turns can add output tokens; use provider telemetry for billing claims.",
            "The primary guarantee is smaller maximum review context and deterministic review order.",
        ],
        "recommendation": (
            "Use routed review when raw diffs exceed the review budget or when fresh-agent review needs deterministic slices; "
            "treat money savings as unproven unless provider telemetry confirms output-token overhead stays below the break-even values."
        ),
        "ledger": ledger,
    }


def build_money_saving_estimate(
    *,
    input_tokens_saved: int,
    full_review_input_tokens_saved: int,
    assumed_extra_output_tokens: int | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    policy_root = repo_policy.project_root(root)
    configured_output_tokens = repo_policy.int_value(policy_root, "owner_defaults.skill_manager.review_cost.extra_output_tokens")
    default_multiplier = repo_policy.int_value(policy_root, "owner_defaults.skill_manager.review_cost.output_price_multiplier")
    saved = max(0, _int_value(input_tokens_saved))
    full_saved = max(0, _int_value(full_review_input_tokens_saved))
    output_tokens = max(0, _int_value(assumed_extra_output_tokens, configured_output_tokens))
    scenarios: list[dict[str, Any]] = []
    for multiplier in OUTPUT_PRICE_MULTIPLIERS:
        output_equivalent = output_tokens * multiplier
        net = saved - output_equivalent
        scenarios.append(
            {
                "output_price_multiplier": multiplier,
                "assumed_extra_output_tokens": output_tokens,
                "extra_output_input_token_equivalent": output_equivalent,
                "net_input_token_equivalent_savings": net,
                "status": "likely-saves-money" if net > 0 else "break-even" if net == 0 else "not-proven",
            }
        )
    default = next(
        (row for row in scenarios if row.get("output_price_multiplier") == default_multiplier),
        scenarios[0] if scenarios else {},
    )
    likely_count = sum(1 for row in scenarios if row.get("status") == "likely-saves-money")
    status = (
        "likely-saves-money-at-default-multiplier"
        if default.get("status") == "likely-saves-money"
        else "mixed-by-output-price"
        if likely_count
        else "not-proven"
    )
    return {
        "status": status,
        "billing_scope": "scenario-not-provider-telemetry",
        "input_tokens_saved": saved,
        "full_review_input_tokens_saved": full_saved,
        "assumed_extra_output_tokens": output_tokens,
        "default_output_price_multiplier": default_multiplier,
        "default_net_input_token_equivalent_savings": default.get("net_input_token_equivalent_savings", 0),
        "scenarios": scenarios,
        "boundary": (
            "Converts assumed extra output tokens into input-token-equivalent cost with simple multipliers. "
            "It is not billing telemetry and excludes cache, hidden prompts, reasoning tokens, latency, and provider-specific pricing."
        ),
    }


def summarize_review_cost_report(report: dict[str, Any]) -> dict[str, Any]:
    estimate = report.get("money_saving_estimate") if isinstance(report.get("money_saving_estimate"), dict) else {}
    return {
        "status": report.get("status", "unknown"),
        "billing_scope": report.get("billing_scope", "input-context-estimate-only"),
        "money_saving_status": report.get("money_saving_status", "unknown"),
        "raw_changed_diff_estimated_tokens": report.get("raw_changed_diff_estimated_tokens", 0),
        "next_review_unit_estimated_tokens": report.get("next_review_unit_estimated_tokens", 0),
        "next_review_unit_saved_tokens_vs_raw_estimated": report.get("next_review_unit_saved_tokens_vs_raw_estimated", 0),
        "money_saving_estimate": {
            "status": estimate.get("status", "unknown"),
            "billing_scope": estimate.get("billing_scope", ""),
            "input_tokens_saved": estimate.get("input_tokens_saved", 0),
            "assumed_extra_output_tokens": estimate.get("assumed_extra_output_tokens", 0),
            "default_output_price_multiplier": estimate.get("default_output_price_multiplier", 0),
            "default_net_input_token_equivalent_savings": estimate.get(
                "default_net_input_token_equivalent_savings",
                0,
            ),
        } if estimate else {},
    }


def append_budget_trend(root: Path, report: dict[str, Any], *, source: str = "finish", path_value: str | None = None) -> dict[str, Any]:
    path = _safe_state_path(root, path_value, default_budget_trend_path(root))
    review_packet = report.get("review_packet") if isinstance(report.get("review_packet"), dict) else {}
    cost_ledger = review_packet.get("cost_ledger") if isinstance(review_packet.get("cost_ledger"), dict) else {}
    budget_hotspots = report.get("budget_hotspots") if isinstance(report.get("budget_hotspots"), dict) else {}
    check_metrics = report.get("check_metrics") if isinstance(report.get("check_metrics"), dict) else {}
    entry = {
        "schema_version": 1,
        "tool": "skill-manager.budget-trend-entry",
        "recorded_at": _now_iso(),
        "source": source,
        "status": report.get("status", "unknown"),
        "changed_file_count": (
            report.get("changed_file_count")
            or (
                report.get("finish_readiness", {}).get("changed_file_count", 0)
                if isinstance(report.get("finish_readiness"), dict)
                else 0
            )
        ),
        "changed_diff_estimated_tokens": review_packet.get("changed_diff_estimated_tokens", 0),
        "review_budget_tokens": review_packet.get("review_budget_tokens", 0),
        "next_review_unit_estimated_tokens": cost_ledger.get("next_review_unit_estimated_tokens", 0),
        "single_agent_saved_tokens_vs_raw_estimated": cost_ledger.get("single_agent_saved_tokens_vs_raw_estimated", 0),
        "next_review_unit_saved_tokens_vs_raw_estimated": cost_ledger.get("next_review_unit_saved_tokens_vs_raw_estimated", 0),
        "budget_hotspot_status": budget_hotspots.get("status", ""),
        "largest_budget_hotspot_words": (
            budget_hotspots.get("top", [{}])[0].get("total_text_words", 0)
            if isinstance(budget_hotspots.get("top"), list) and budget_hotspots.get("top")
            else 0
        ),
        "finish_elapsed_seconds": check_metrics.get("elapsed_seconds", 0),
        "input_context_only": True,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    summary = budget_trend_summary(root, path_value=path.as_posix())
    summary["recorded"] = entry
    return summary


def _load_budget_entries(path: Path, limit: int = 50) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def budget_trend_summary(root: Path, *, path_value: str | None = None, limit: int | None = None) -> dict[str, Any]:
    limit = limit or repo_policy.int_value(root, "owner_defaults.skill_manager.review_cost.visible_history_entries")
    path = _safe_state_path(root, path_value, default_budget_trend_path(root))
    entries = _load_budget_entries(path, limit=limit)
    latest = entries[-1] if entries else {}
    previous = entries[-2] if len(entries) > 1 else {}

    def delta(key: str) -> int:
        if not latest or not previous:
            return 0
        try:
            return int(latest.get(key, 0) or 0) - int(previous.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "schema_version": 1,
        "tool": "skill-manager.budget-trend",
        "ok": True,
        "status": "measured" if entries else "missing",
        "path": path.as_posix(),
        "entry_count": len(entries),
        "latest": latest,
        "delta_from_previous": {
            "changed_diff_estimated_tokens": delta("changed_diff_estimated_tokens"),
            "next_review_unit_estimated_tokens": delta("next_review_unit_estimated_tokens"),
            "largest_budget_hotspot_words": delta("largest_budget_hotspot_words"),
            "finish_elapsed_seconds": delta("finish_elapsed_seconds"),
        },
        "boundary": "input-context estimates and local elapsed time only; not provider billing telemetry",
    }


def render_review_plan(plan: dict[str, Any]) -> str:
    lines = [
        "# Review Plan",
        "",
        f"- Status: {plan.get('status', 'unknown')}",
        f"- Review units: {plan.get('review_unit_count', 0)}",
        f"- Validation units: {plan.get('validation_unit_count', 0)}",
        f"- Next command: `{plan.get('next_pending_command', '')}`",
        "",
        "## Review Units",
        "",
    ]
    units = [item for item in _list(plan.get("review_units")) if isinstance(item, dict)]
    if not units:
        lines.append("- None.")
    for unit in units:
        label = unit.get("path") or unit.get("owner") or unit.get("scope")
        hunk = f" `{unit.get('hunk')}`" if unit.get("hunk") else ""
        lines.append(
            f"- `{unit.get('scope')}` `{label}`{hunk}: "
            f"{unit.get('estimated_changed_tokens', 0)} tokens, command `{unit.get('command', '')}`"
        )
    validation = [item for item in _list(plan.get("validation_units")) if isinstance(item, dict)]
    if validation:
        lines.extend(["", "## Validation Units", ""])
        for unit in validation:
            lines.append(f"- `{unit.get('command', '')}`")
    lines.extend(["", f"Rule: {plan.get('resume_rule', '')}", ""])
    return "\n".join(lines)


def render_review_cost_report(report: dict[str, Any]) -> str:
    break_even = _dict(report.get("break_even_extra_output_tokens"))
    lines = [
        "# Review Cost Report",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Billing scope: {report.get('billing_scope', 'input-context-estimate-only')}",
        f"- Money-saving status: {report.get('money_saving_status', 'unknown')}",
        f"- Raw diff estimate: {report.get('raw_changed_diff_estimated_tokens', 0)} tokens",
        f"- Next review unit estimate: {report.get('next_review_unit_estimated_tokens', 0)} tokens",
        f"- Next-unit input tokens saved: {report.get('next_review_unit_saved_tokens_vs_raw_estimated', 0)}",
        f"- All review units delta: {report.get('all_review_units_delta_tokens_vs_raw_estimated', 0)}",
        "",
        "## Output Break-Even",
        "",
    ]
    for label, value in break_even.items():
        lines.append(f"- {label}: {value} extra output tokens")
    lines.extend(["", "## Boundary", ""])
    for item in _list(report.get("boundary")):
        lines.append(f"- {item}")
    lines.extend(["", f"Recommendation: {report.get('recommendation', '')}", ""])
    return "\n".join(lines)
