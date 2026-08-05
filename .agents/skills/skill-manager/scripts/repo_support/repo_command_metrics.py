"""Small latency and output-budget helpers for machine-facing commands."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from repo_support import repo_cost_policy
from repo_support import repo_policy

LATENCY_BUDGETS_MS = dict(repo_policy.DEFAULT_LATENCY_BUDGETS_MS)
COMPONENT_LATENCY_BUDGETS_MS = dict(repo_policy.DEFAULT_COMPONENT_LATENCY_BUDGETS_MS)
OUTPUT_BUDGETS_TOKENS = dict(repo_policy.DEFAULT_OUTPUT_BUDGETS_TOKENS)
_POLICY_ROOT: Path | None = None


def configure_policy_root(root: Path | None) -> None:
    global _POLICY_ROOT
    _POLICY_ROOT = root.resolve() if root is not None else None


def _configured_budget(path: str, fallback: int) -> int:
    root = _POLICY_ROOT
    if root is None:
        return fallback
    return repo_policy.int_value(root, path)

DEFAULT_VALIDATION_PROGRESS_PATH = ".agents/local-ai/cache/validation-progress.json"

COMMAND_BUDGET_SPECS: dict[str, dict[str, Any]] = {
    "status-fast": {
        "args": ["status", "--fast", "--summary", "--compact", "--format", "json"],
        "timeout_seconds": 30,
        "profiles": {"fast", "standard"},
    },
    "startup-context": {
        "args": ["startup-context", "--summary", "--compact", "--format", "json"],
        "timeout_seconds": 30,
        "profiles": {"fast", "standard"},
    },
    "next-action": {
        "args": ["next-action", "--summary", "--compact", "--format", "json"],
        "timeout_seconds": 45,
        "profiles": {"fast", "standard"},
    },
    "context-use-check": {
        "args": ["context-use-check", "--summary", "--compact", "--format", "json"],
        "timeout_seconds": 45,
        "profiles": {"fast", "standard"},
    },
    "changed-evidence": {
        "args": ["changed-evidence", "--summary", "--compact", "--format", "json"],
        "timeout_seconds": 30,
        "profiles": {"standard"},
    },
    "review-loop": {
        "args": [
            "review-loop",
            "--dry-run",
            "--max-units",
            "3",
            "--summary",
            "--compact",
            "--format",
            "json",
        ],
        "timeout_seconds": 60,
        "profiles": {"standard"},
        "expected_returncodes": {0, 1},
    },
    "review-autopilot": {
        "args": [
            "review-autopilot",
            "--max-cycles",
            "1",
            "--max-units-per-cycle",
            "1",
            "--dry-run",
            "--summary",
            "--compact",
            "--format",
            "json",
        ],
        "timeout_seconds": 45,
        "profiles": {"standard"},
        "expected_returncodes": {0, 1},
    },
    "smoke-workflows": {
        "args": [
            "workflow",
            "smoke",
            "--all",
            "--dry-run",
            "--summary",
            "--compact",
            "--format",
            "json",
        ],
        "timeout_seconds": 60,
        "profiles": {"fast", "standard"},
    },
    "check-changed": {
        "args": ["check-changed", "--summary", "--compact", "--format", "json"],
        "timeout_seconds": 120,
        "profiles": {"standard"},
    },
}


def elapsed_ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def safe_validation_progress_path(root: Path, value: str | None = None) -> Path:
    candidate = Path(value).expanduser() if value else root / DEFAULT_VALIDATION_PROGRESS_PATH
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise SystemExit("validation progress path must stay inside the repository") from exc
    return resolved


def write_validation_progress(
    root: Path,
    *,
    command: str,
    phase: str,
    status: str,
    started: float,
    completed: int = 0,
    total: int = 0,
    current: str = "",
    path_value: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = safe_validation_progress_path(root, path_value)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "tool": "skill-manager.validation-progress",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "command": command,
        "phase": phase,
        "status": status,
        "completed": max(0, int(completed or 0)),
        "total": max(0, int(total or 0)),
        "current": current,
        "elapsed_ms": elapsed_ms_since(started),
    }
    if extra:
        payload["extra"] = extra
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return {
        "command": command,
        "status": status,
        "phase": phase,
        "path": path.as_posix(),
        "recorded_at": payload["recorded_at"],
        "completed": payload["completed"],
        "total": payload["total"],
        "elapsed_ms": payload["elapsed_ms"],
        **({"extra": extra} if extra else {}),
    }


def read_validation_progress(root: Path, *, path_value: str | None = None) -> dict[str, Any]:
    path = safe_validation_progress_path(root, path_value)
    if not path.exists():
        return {
            "schema_version": 1,
            "tool": "skill-manager.validation-progress",
            "ok": False,
            "status": "missing",
            "path": path.as_posix(),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "schema_version": 1,
            "tool": "skill-manager.validation-progress",
            "ok": False,
            "status": "invalid",
            "path": path.as_posix(),
            "issue": str(exc),
        }
    if not isinstance(payload, dict):
        return {
            "schema_version": 1,
            "tool": "skill-manager.validation-progress",
            "ok": False,
            "status": "invalid",
            "path": path.as_posix(),
            "issue": "validation progress payload is not an object",
        }
    payload = dict(payload)
    payload["ok"] = payload.get("status") == "passed"
    payload["path"] = path.as_posix()
    return payload


def validation_progress_covers_input(
    validation_progress: dict[str, Any],
    input_fingerprint: dict[str, Any],
    *,
    required_check_ids: list[str],
    profile: str = "changed",
) -> bool:
    """Return true only when persisted proof covers every required current check."""
    digest_value = input_fingerprint.get("digest")
    digest = digest_value.strip() if isinstance(digest_value, str) else ""
    extra = validation_progress.get("extra") if isinstance(validation_progress.get("extra"), dict) else {}
    required = {str(item) for item in required_check_ids if str(item)}
    recorded_required_values = extra.get("required_check_ids")
    passed_values = extra.get("passed_check_ids")
    failed_check_count = extra.get("failed_check_count")
    if (
        not isinstance(recorded_required_values, list)
        or not isinstance(passed_values, list)
        or isinstance(failed_check_count, bool)
        or not isinstance(failed_check_count, int)
        or not all(isinstance(item, str) and item for item in recorded_required_values)
        or not all(isinstance(item, str) and item for item in passed_values)
    ):
        return False
    recorded_required = set(recorded_required_values)
    passed = set(passed_values)
    if len(recorded_required) != len(recorded_required_values) or len(passed) != len(passed_values):
        return False
    recorded_digest = extra.get("input_fingerprint_digest")
    recorded_profile = extra.get("profile")
    return (
        bool(digest)
        and validation_progress.get("command") == "check-changed"
        and validation_progress.get("phase") == "complete"
        and validation_progress.get("status") == "passed"
        and failed_check_count == 0
        and isinstance(recorded_digest, str)
        and recorded_digest.strip() == digest
        and recorded_profile == profile
        and recorded_required == required == passed
    )


def timed_section(name: str, callback: Callable[[], Any]) -> tuple[Any, dict[str, Any]]:
    started = time.perf_counter()
    ok = True
    issue = ""
    try:
        value = callback()
    except Exception as exc:  # noqa: BLE001 - callers turn this into partial command evidence.
        ok = False
        issue = str(exc)
        value = {}
    return value, {
        "name": name,
        "ok": ok,
        "elapsed_ms": elapsed_ms_since(started),
        "issue": issue,
    }


def _elapsed(row: dict[str, Any]) -> float:
    try:
        return float(row.get("elapsed_ms", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def timing_budget_report(
    command_id: str,
    total_elapsed_ms: float,
    *,
    timings: list[dict[str, Any]] | None = None,
    budget_ms: int | None = None,
) -> dict[str, Any]:
    default_budget = int(LATENCY_BUDGETS_MS.get(command_id, 10000))
    budget = int(budget_ms) if budget_ms is not None else _configured_budget(
        f"commands.latency_ms.{command_id}", default_budget
    ) if command_id in LATENCY_BUDGETS_MS else default_budget
    default_component = int(COMPONENT_LATENCY_BUDGETS_MS.get(command_id, budget // 2))
    component_budget = max(
        500,
        _configured_budget(f"commands.component_latency_ms.{command_id}", default_component)
        if command_id in COMPONENT_LATENCY_BUDGETS_MS
        else default_component,
    )
    rows = [row for row in (timings or []) if isinstance(row, dict)]
    slow = [
        {
            "name": str(row.get("name", "")),
            "elapsed_ms": _elapsed(row),
            "budget_ms": component_budget,
            **({"issue": row.get("issue", "")} if row.get("issue") else {}),
        }
        for row in rows
        if _elapsed(row) > component_budget
    ]
    over = max(0, int(round(float(total_elapsed_ms or 0))) - budget)
    status = "over-budget" if over else ("slow-components" if slow else "within-budget")
    return {
        "command": command_id,
        "status": status,
        "elapsed_ms": round(float(total_elapsed_ms or 0), 2),
        "budget_ms": budget,
        "over_budget_ms": over,
        "component_budget_ms": component_budget,
        "slow_component_count": len(slow),
        "slow_components": slow[:8],
        "summary": (
            f"{command_id} elapsed {round(float(total_elapsed_ms or 0), 2)}ms "
            f"against {budget}ms budget"
        ),
    }


def estimated_json_output_tokens(payload: dict[str, Any]) -> int:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return repo_cost_policy.estimate_tokens_from_bytes(len(text.encode("utf-8")))


def output_budget_report(
    command_id: str,
    payload: dict[str, Any],
    *,
    budget_tokens: int | None = None,
    scope: str = "summary-compact-json-estimate",
) -> dict[str, Any]:
    default_budget = int(OUTPUT_BUDGETS_TOKENS.get(command_id, 2000))
    budget = int(budget_tokens) if budget_tokens is not None else _configured_budget(
        f"commands.output_tokens.{command_id}", default_budget
    ) if command_id in OUTPUT_BUDGETS_TOKENS else default_budget
    estimated = estimated_json_output_tokens(payload)
    over = max(0, estimated - budget)
    return {
        "command": command_id,
        "scope": scope,
        "status": "within-budget" if over == 0 else "over-budget",
        "estimated_output_tokens": estimated,
        "budget_tokens": budget,
        "tokens_over_budget": over,
        "counter": "compact_json_bytes_div_4",
        "summary": f"{estimated}/{budget} estimated output tokens",
    }


def attach_output_budget(
    payload: dict[str, Any],
    command_id: str,
    *,
    budget_tokens: int | None = None,
    scope: str = "summary-compact-json-estimate",
) -> dict[str, Any]:
    for _index in range(6):
        report = output_budget_report(
            command_id,
            payload,
            budget_tokens=budget_tokens,
            scope=scope,
        )
        if payload.get("output_budget") == report:
            break
        payload["output_budget"] = report
    payload["output_budget"] = output_budget_report(
        command_id,
        payload,
        budget_tokens=budget_tokens,
        scope=scope,
    )
    return payload


def command_budget_ids_for_profile(profile: str) -> list[str]:
    selected = []
    for command_id, spec in COMMAND_BUDGET_SPECS.items():
        profiles = spec.get("profiles") if isinstance(spec.get("profiles"), set) else set()
        if profile in profiles:
            selected.append(command_id)
    return selected


def _json_payload(stdout: str) -> tuple[dict[str, Any], str]:
    text = stdout.strip()
    if not text:
        return {}, "command emitted no JSON output"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, f"command output was not parseable JSON: {exc}"
    if not isinstance(parsed, dict):
        return {}, "command JSON output was not an object"
    return parsed, ""


def _budget_issues(command_id: str, payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    latency = payload.get("latency_budget") if isinstance(payload.get("latency_budget"), dict) else {}
    output = payload.get("output_budget") if isinstance(payload.get("output_budget"), dict) else {}
    issues.extend(_latency_budget_issues(command_id, latency))
    if not output:
        issues.append("missing output_budget")
    elif output.get("command") != command_id:
        issues.append(f"output_budget.command is {output.get('command')!r}, expected {command_id!r}")
    elif output.get("status") != "within-budget":
        issues.append(f"output budget is {output.get('status')}")
    return issues


def _latency_budget_issues(command_id: str, latency: dict[str, Any]) -> list[str]:
    if not latency:
        return ["missing latency_budget"]
    if latency.get("command") != command_id:
        return [f"latency_budget.command is {latency.get('command')!r}, expected {command_id!r}"]
    if latency.get("status") != "within-budget":
        return [f"latency budget is {latency.get('status')}"]
    return []


def _budget_row(
    root: Path,
    command_id: str,
    *,
    runner=subprocess.run,
) -> dict[str, Any]:
    spec = COMMAND_BUDGET_SPECS[command_id]
    command = [sys.executable, "-B", ".agents/manage.py", *[str(item) for item in spec["args"]]]
    started = time.perf_counter()
    try:
        completed = runner(
            command,
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=int(spec.get("timeout_seconds", 60)),
        )
        elapsed_ms = elapsed_ms_since(started)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        return {
            "command_id": command_id,
            "ok": False,
            "status": "failed",
            "returncode": None,
            "elapsed_ms": elapsed_ms_since(started),
            "command": " ".join(command),
            "issues": [f"timed out after {spec.get('timeout_seconds', 60)}s"],
            "output_tail": stdout[-1000:],
        }
    except OSError as exc:
        return {
            "command_id": command_id,
            "ok": False,
            "status": "failed",
            "returncode": None,
            "elapsed_ms": elapsed_ms_since(started),
            "command": " ".join(command),
            "issues": [str(exc)],
            "output_tail": "",
        }
    payload, parse_issue = _json_payload(completed.stdout or "")
    issues = [parse_issue] if parse_issue else _budget_issues(command_id, payload)
    expected_returncodes = spec.get("expected_returncodes")
    if not isinstance(expected_returncodes, set):
        expected_returncodes = {0}
    if completed.returncode not in expected_returncodes:
        issues.append(f"unexpected returncode {completed.returncode}")
    latency = payload.get("latency_budget") if isinstance(payload.get("latency_budget"), dict) else {}
    output = payload.get("output_budget") if isinstance(payload.get("output_budget"), dict) else {}
    return {
        "command_id": command_id,
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "returncode": completed.returncode,
        "elapsed_ms": elapsed_ms,
        "command": " ".join(command),
        "semantic_status": payload.get("status", ""),
        "latency_budget": latency,
        "output_budget": output,
        "issues": issues,
        "output_tail": "" if not issues else (completed.stdout or "")[-1000:],
    }


def command_budget_regression_report(
    root: Path,
    *,
    profile: str = "fast",
    command_ids: list[str] | None = None,
    runner=subprocess.run,
) -> dict[str, Any]:
    started = time.perf_counter()
    selected = command_ids or command_budget_ids_for_profile(profile)
    unknown = [command_id for command_id in selected if command_id not in COMMAND_BUDGET_SPECS]
    rows = []
    issues = []
    if unknown:
        issues.extend(f"unknown command budget id: {command_id}" for command_id in unknown)
    for command_id in selected:
        if command_id in COMMAND_BUDGET_SPECS:
            row = _budget_row(root, command_id, runner=runner)
            rows.append(row)
            issues.extend(f"{command_id}: {issue}" for issue in row.get("issues", []) if str(issue))
    latency_budget = timing_budget_report("command-budget-check", elapsed_ms_since(started))
    issues.extend(
        f"command-budget-check: {issue}"
        for issue in _latency_budget_issues("command-budget-check", latency_budget)
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "tool": "skill-manager.command-budget-check",
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "profile": profile,
        "command_count": len(rows),
        "failed_command_count": sum(1 for row in rows if not row.get("ok")),
        "commands": rows,
        "issues": issues,
        "next_command": (
            "fix command budget regressions, then rerun python -B .agents/manage.py command-budget-check "
            f"--profile {profile} --summary --compact --format json"
            if issues
            else "none"
        ),
    }
    report["latency_budget"] = latency_budget
    return report


def summarize_command_budget_regression_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    rows = report.get("commands") if isinstance(report.get("commands"), list) else []
    failed = [row for row in rows if isinstance(row, dict) and not row.get("ok")]
    output: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.command-budget-check"),
        "ok": bool(report.get("ok")),
        "status": report.get("status", "unknown"),
        "profile": report.get("profile", ""),
        "summary": {
            "command_count": report.get("command_count", len(rows)),
            "failed_command_count": report.get("failed_command_count", len(failed)),
            "issue_count": len(report.get("issues", []) if isinstance(report.get("issues"), list) else []),
        },
        "covered_command_ids": [
            str(row.get("command_id", ""))
            for row in rows
            if isinstance(row, dict) and str(row.get("command_id", "")).strip()
        ],
        "latency_budget": report.get("latency_budget", {}),
        "next_command": report.get("next_command", ""),
    }
    if failed or not compact:
        output["commands"] = failed if compact else rows
    if report.get("issues") or not compact:
        output["issues"] = report.get("issues", [])
    return attach_output_budget(output, "command-budget-check")


def render_command_budget_regression_report(report: dict[str, Any]) -> str:
    lines = [
        "# Command Budget Check",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Profile: {report.get('profile', '')}",
        f"- Commands: {report.get('command_count', 0)}",
        f"- Failed: {report.get('failed_command_count', 0)}",
        "",
    ]
    rows = report.get("commands") if isinstance(report.get("commands"), list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = "ok" if row.get("ok") else "failed"
        lines.append(f"- {row.get('command_id')}: {status}")
        for issue in row.get("issues", []) if isinstance(row.get("issues"), list) else []:
            lines.append(f"  - {issue}")
    if report.get("next_command"):
        lines.extend(["", f"Next command: `{report.get('next_command')}`"])
    return "\n".join(lines).rstrip() + "\n"
