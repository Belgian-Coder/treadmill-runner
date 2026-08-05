"""Context-cost benchmark helpers for low-context repo commands."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from repo_support import repo_common as repo
from repo_support import repo_cost_policy
from repo_support.repo_qol_daily import (
    startup_context_report,
    summarize_startup_context_report,
)

DEFAULT_CONTEXT_COST_LEDGER_PATH = ".agents/local-ai/cache/context-cost-ledger.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_repo_path(root: Path, value: str | None, default: str) -> Path:
    candidate = Path(value).expanduser() if value else root / default
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise SystemExit("state path must stay inside the repository") from exc
    return resolved


def estimated_json_output_tokens(payload: dict[str, Any]) -> int:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return repo_cost_policy.estimate_tokens_from_bytes(len(text.encode("utf-8")))


def context_file_rows(root: Path, paths: list[str]) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    total = 0
    seen: set[str] = set()
    for raw in paths:
        rel = str(raw).replace("\\", "/").strip()
        if not rel or rel in seen:
            continue
        seen.add(rel)
        path = root / rel
        if not path.is_file():
            rows.append({"path": rel, "status": "missing", "estimated_tokens": 0})
            continue
        try:
            size = path.stat().st_size
        except OSError:
            rows.append({"path": rel, "status": "blocked", "estimated_tokens": 0})
            continue
        tokens = repo_cost_policy.estimate_tokens_from_bytes(size)
        total += tokens
        rows.append({"path": rel, "status": "loaded", "size_bytes": size, "estimated_tokens": tokens})
    return rows, total


def saved_percent(raw_tokens: int, route_tokens: int) -> float:
    if raw_tokens <= 0:
        return 0.0
    return round(((raw_tokens - route_tokens) / raw_tokens) * 100, 2)


def _weighted_route_total(route: dict[str, Any], output_multiplier: int) -> int:
    return int(route.get("input_tokens", 0) or 0) + int(route.get("output_tokens", 0) or 0) * output_multiplier


def _ledger_entry(report: dict[str, Any]) -> dict[str, Any]:
    comparison = report.get("comparison") if isinstance(report.get("comparison"), dict) else {}
    return {
        "schema_version": 1,
        "recorded_at": _now_iso(),
        "status": report.get("status", "unknown"),
        "raw_diff_input_tokens": comparison.get("raw_diff_input_tokens", 0),
        "selected_route": comparison.get("selected_route", ""),
        "selected_route_input_tokens": comparison.get("selected_route_input_tokens", 0),
        "selected_route_output_tokens": comparison.get("selected_route_output_tokens", 0),
        "selected_route_weighted_total_output_4x": comparison.get("selected_route_weighted_total_output_4x", 0),
        "saved_input_tokens_vs_raw": comparison.get("saved_input_tokens_vs_raw", 0),
        "saved_input_percent_vs_raw": comparison.get("saved_input_percent_vs_raw", 0.0),
        "money_saving_status": comparison.get("money_saving_status", ""),
        "total_elapsed_ms": report.get("total_elapsed_ms", 0.0),
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            entries.append(data)
    return entries


def _append_jsonl(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def _context_cost_history(root: Path, history_path: str | None, entry: dict[str, Any] | None) -> dict[str, Any]:
    path = _safe_repo_path(root, history_path, DEFAULT_CONTEXT_COST_LEDGER_PATH)
    previous = _read_jsonl(path)
    if entry is not None:
        _append_jsonl(path, entry)
    entries = [*previous, entry] if entry is not None else previous
    latest = entries[-1] if entries else {}
    prior = entries[-2] if len(entries) >= 2 else {}
    delta: dict[str, Any] = {}
    if latest and prior:
        for key in (
            "raw_diff_input_tokens",
            "selected_route_input_tokens",
            "selected_route_output_tokens",
            "selected_route_weighted_total_output_4x",
            "saved_input_percent_vs_raw",
            "total_elapsed_ms",
        ):
            delta[key] = round(float(latest.get(key, 0) or 0) - float(prior.get(key, 0) or 0), 2)
    return {
        "path": repo.relative(root, path),
        "recorded": entry is not None,
        "entry_count": len(entries),
        "latest": latest,
        "delta_from_previous": delta,
    }


def context_cost_benchmark_report(
    root: Path,
    *,
    min_saved_percent: float = 25.0,
    record: bool = False,
    history_path: str | None = None,
    startup_factory: Callable[..., dict[str, Any]] | None = None,
    next_action_factory: Callable[..., dict[str, Any]],
    next_action_summarizer: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    started = time.perf_counter()
    raw_diff = repo_cost_policy.changed_diff_estimate(root)
    raw_input_tokens = int(raw_diff.get("estimated_tokens", 0) or 0)
    startup_builder = startup_factory or startup_context_report

    startup_started = time.perf_counter()
    startup = startup_builder(root, compact=True)
    startup_elapsed = round((time.perf_counter() - startup_started) * 1000, 2)
    startup_summary = startup.get("summary") if isinstance(startup.get("summary"), dict) else {}
    startup_input_tokens = int(startup_summary.get("default_guidance_tokens", 0) or 0)
    startup_output_tokens = estimated_json_output_tokens(
        summarize_startup_context_report(startup, compact=True)
    )

    next_started = time.perf_counter()
    next_action = next_action_factory(root, fast=True)
    next_elapsed = round((time.perf_counter() - next_started) * 1000, 2)
    next_summary = next_action_summarizer(next_action, compact=True)
    next_paths = ["AGENTS.md", *[str(item) for item in next_action.get("required_context", []) if str(item).strip()]]
    next_path_rows, next_input_tokens = context_file_rows(root, next_paths)
    next_output_tokens = estimated_json_output_tokens(next_summary)

    raw_route = {
        "name": "raw-diff-context",
        "description": "Read the current changed diff directly.",
        "input_tokens": raw_input_tokens,
        "output_tokens": 0,
        "estimated_total_tokens_output_1x": raw_input_tokens,
        "estimated_total_tokens_output_4x": raw_input_tokens,
        "elapsed_ms": 0.0,
        "token_counter": "git_numstat_lines_x12_plus_untracked_bytes_div_4",
    }
    startup_route = {
        "name": "startup-handoff-guidance",
        "description": "Use AGENTS/routing/HANDOFF startup guidance instead of broad orientation files.",
        "input_tokens": startup_input_tokens,
        "output_tokens": startup_output_tokens,
        "estimated_total_tokens_output_1x": startup_input_tokens + startup_output_tokens,
        "estimated_total_tokens_output_4x": startup_input_tokens + startup_output_tokens * 4,
        "elapsed_ms": startup_elapsed,
        "saved_input_tokens_vs_raw": max(0, raw_input_tokens - startup_input_tokens),
        "saved_input_percent_vs_raw": saved_percent(raw_input_tokens, startup_input_tokens),
    }
    next_route = {
        "name": "next-action-review-route",
        "description": "Use next-action output plus only its required context paths.",
        "input_tokens": next_input_tokens,
        "output_tokens": next_output_tokens,
        "estimated_total_tokens_output_1x": next_input_tokens + next_output_tokens,
        "estimated_total_tokens_output_4x": next_input_tokens + next_output_tokens * 4,
        "elapsed_ms": next_elapsed,
        "saved_input_tokens_vs_raw": max(0, raw_input_tokens - next_input_tokens),
        "saved_input_percent_vs_raw": saved_percent(raw_input_tokens, next_input_tokens),
        "paths": next_path_rows,
        "next_command": next_action.get("next_command", ""),
    }
    routes = [raw_route, startup_route, next_route]
    saved_input = int(next_route["saved_input_tokens_vs_raw"])
    saved = float(next_route["saved_input_percent_vs_raw"])
    weighted_4x_better = raw_input_tokens <= 0 or _weighted_route_total(next_route, 4) < raw_input_tokens
    meets_threshold = raw_input_tokens <= 0 or saved >= float(min_saved_percent)
    ok = bool(weighted_4x_better and meets_threshold)
    status = "measurably-better" if ok and raw_input_tokens > 0 else ("no-changes" if raw_input_tokens <= 0 else "needs-review")
    break_even = {
        "output_price_multiplier_1x": saved_input,
        "output_price_multiplier_2x": saved_input // 2,
        "output_price_multiplier_4x": saved_input // 4,
        "output_price_multiplier_8x": saved_input // 8,
    }
    report = {
        "schema_version": 1,
        "tool": "skill-manager.context-cost-benchmark",
        "ok": ok,
        "status": status,
        "min_saved_percent": float(min_saved_percent),
        "comparison": {
            "raw_diff_input_tokens": raw_input_tokens,
            "selected_route": next_route["name"],
            "selected_route_input_tokens": next_input_tokens,
            "selected_route_output_tokens": next_output_tokens,
            "selected_route_weighted_total_output_4x": _weighted_route_total(next_route, 4),
            "saved_input_tokens_vs_raw": saved_input,
            "saved_input_percent_vs_raw": saved,
            "output_break_even_extra_tokens": break_even,
            "money_saving_status": (
                "potentially-cheaper-at-4x-output"
                if weighted_4x_better and raw_input_tokens > 0
                else "unproven-or-not-cheaper-at-4x-output"
            ),
        },
        "routes": routes,
        "startup_context": summarize_startup_context_report(startup, compact=True),
        "next_action": next_summary,
        "losses": [
            "Routed review reads the first required slice, not the entire raw diff.",
            "Cross-cutting issues may require following subsequent review-progress units.",
            "Output and reasoning token billing are estimated from command JSON size, not provider telemetry.",
        ],
        "boundary": (
            "Input tokens use deterministic local estimates. Output tokens are estimated command-output bytes/4. "
            "This is not provider billing telemetry and excludes hidden prompts, reasoning tokens, cache discounts, and rework."
        ),
        "use_by_default": ok,
        "next_command": (
            "python -B .agents/manage.py next-action --summary --compact --format json"
            if ok
            else "inspect context-cost-benchmark comparison and reduce route/context overhead"
        ),
        "total_elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    report["history"] = _context_cost_history(root, history_path, _ledger_entry(report) if record else None)
    return report


def summarize_context_cost_benchmark_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    routes = []
    for route in report.get("routes", []) if isinstance(report.get("routes"), list) else []:
        if not isinstance(route, dict):
            continue
        item = {
            "name": route.get("name", ""),
            "input_tokens": route.get("input_tokens", 0),
            "output_tokens": route.get("output_tokens", 0),
            "estimated_total_tokens_output_4x": route.get("estimated_total_tokens_output_4x", 0),
            "saved_input_tokens_vs_raw": route.get("saved_input_tokens_vs_raw", 0),
            "saved_input_percent_vs_raw": route.get("saved_input_percent_vs_raw", 0.0),
            "elapsed_ms": route.get("elapsed_ms", 0.0),
        }
        if not compact:
            item["paths"] = route.get("paths", [])
            item["description"] = route.get("description", "")
        routes.append(item)
    output = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.context-cost-benchmark"),
        "ok": bool(report.get("ok", False)),
        "status": report.get("status", "unknown"),
        "comparison": report.get("comparison", {}),
        "routes": routes,
        "losses": report.get("losses", []),
        "boundary": report.get("boundary", ""),
        "use_by_default": bool(report.get("use_by_default", False)),
        "next_command": report.get("next_command", ""),
        "history": report.get("history", {}),
        "total_elapsed_ms": report.get("total_elapsed_ms", 0.0),
    }
    if not compact:
        output["startup_context"] = report.get("startup_context", {})
        output["next_action"] = report.get("next_action", {})
    return output


def render_context_cost_benchmark(report: dict[str, Any]) -> str:
    comparison = report.get("comparison") if isinstance(report.get("comparison"), dict) else {}
    history = report.get("history") if isinstance(report.get("history"), dict) else {}
    lines = [
        "# Context Cost Benchmark",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Raw diff input estimate: {comparison.get('raw_diff_input_tokens', 0)} tokens",
        f"- Selected route: {comparison.get('selected_route', '')}",
        f"- Selected route input estimate: {comparison.get('selected_route_input_tokens', 0)} tokens",
        f"- Selected route output estimate: {comparison.get('selected_route_output_tokens', 0)} tokens",
        f"- Saved input estimate: {comparison.get('saved_input_tokens_vs_raw', 0)} tokens ({comparison.get('saved_input_percent_vs_raw', 0.0)}%)",
        f"- Money status: {comparison.get('money_saving_status', '')}",
        f"- History: `{history.get('path', '')}` ({history.get('entry_count', 0)} entries)",
        "",
        "## Routes",
        "",
    ]
    for route in report.get("routes", []) if isinstance(report.get("routes"), list) else []:
        if not isinstance(route, dict):
            continue
        lines.append(
            f"- {route.get('name')}: input {route.get('input_tokens', 0)}, "
            f"output {route.get('output_tokens', 0)}, 4x total {route.get('estimated_total_tokens_output_4x', 0)}"
        )
    lines.extend(["", f"Boundary: {report.get('boundary', '')}", ""])
    return "\n".join(lines)
