"""Measure Codex CLI runs with provider token receipts and external wall time."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path


TOOL = "agent-benchmarking.codex-exec-measure"
SCHEMA_VERSION = 1
DEFAULT_CODEX_PACKAGE = "@openai/codex@0.146.0"
RATE_SOURCE = "https://help.openai.com/en/articles/20001106-codex-rate-card"
API_RATE_SOURCE = "https://developers.openai.com/api/docs/models/compare"
RATES_CHECKED = date(2026, 8, 3)
RATE_MAX_AGE_DAYS = 30
RATES = {
    "gpt-5.6-sol": {"input": 125.0, "cached_input": 12.5, "output": 750.0},
    "gpt-5.6-terra": {"input": 50.0, "cached_input": 5.0, "output": 300.0},
    "gpt-5.6-luna": {"input": 5.0, "cached_input": 0.5, "output": 30.0},
    "gpt-5.3-codex": {"input": 43.75, "cached_input": 4.375, "output": 350.0},
    "gpt-5.4": {"input": 62.5, "cached_input": 6.25, "output": 375.0},
}
API_USD_RATES = {
    "gpt-5.6-sol": {"input": 5.0, "cached_input": 0.5, "output": 30.0},
    "gpt-5.6-terra": {"input": 2.0, "cached_input": 0.2, "output": 12.0},
    "gpt-5.6-luna": {"input": 0.2, "cached_input": 0.02, "output": 1.2},
    "gpt-5.3-codex": {"input": 1.75, "cached_input": 0.175, "output": 14.0},
    "gpt-5.4": {"input": 2.5, "cached_input": 0.25, "output": 15.0},
}
PREFERRED_CONFIG = {
    "routing": {
        "status": "provisional by task class until three matched repetitions pass promotion gates",
        "difficult_cross_layer_default": {
            "mode": "one persistent implementation agent followed by deterministic acceptance",
            "model": "gpt-5.6-terra",
            "reasoning": "high",
            "evidence": "Terra-high plus bounded Luna-high failure repair was 100/100 eligible on US-TR-008 at 62.292776 credits",
        },
        "bounded_cost_experiment": {
            "models": [
                {"model": "gpt-5.6-luna", "reasoning": "high"},
                {"model": "gpt-5.6-terra", "reasoning": "high"},
            ],
            "requires": "low-consequence bounded task plus exhaustive final acceptance, locked restore, and browser gate after all generated outputs",
        },
        "conditional_bounded_repair": {
            "model": "gpt-5.6-luna",
            "reasoning": "high",
            "when": "complete gate produces a clear bounded failure packet",
            "input": "story, changed-file summary, exact failures, and required final commands",
            "after": "rerun the complete deterministic gate",
            "evidence": "added 3.202946 credits to Terra-high and reached 100/100 eligible on US-TR-008",
        },
        "planning": "keep decision-complete planning in the implementation thread; no routine planner handoff",
        "high_consequence_escalation": {
            "model": "gpt-5.6-sol",
            "reasoning": "high",
            "when": "safety, protocol, security, recovery, architecture, or unresolved ambiguity justifies it",
        },
        "high_consequence_repair": "use bounded Sol-medium/high for safety, protocol, security, recovery, architecture, unresolved ambiguity, or after Luna fails the same packet",
        "review_policy": "no open-ended routine reviewer; stop after a green complete gate",
        "multi_stage": "conditional bounded repair only; disabled as an automatic route until three matched repetitions show a quality-adjusted benefit",
    },
    "measurement": {
        "cli_package": DEFAULT_CODEX_PACKAGE,
        "persist_thread": True,
        "ignore_user_config": True,
        "default_sandbox": "read-only",
        "minimum_repetitions_for_routing": 3,
        "measure_external_wall_time": True,
        "require_deterministic_quality_gate": True,
        "record_cold_and_warm_runs_separately": True,
        "retain_prompt_hash_not_prompt_text": True,
    },
    "cost_first_promotion_gate": {
        "quality_regression_allowed": False,
        "skips_or_fallbacks_allowed": False,
        "median_credit_ratio_max": 0.25,
        "median_wall_time_ratio_max": 2.0,
    },
}


def canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value), encoding="utf-8", newline="\n")


def resolved_file(value: str, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"{label} is not a file: {path}")
    return path


def resolved_directory(value: str, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"{label} is not a directory: {path}")
    return path


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


def parse_events(stdout: str) -> list[dict]:
    events: list[dict] = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def extract_receipt(events: list[dict]) -> tuple[str | None, dict | None, str]:
    thread_id = next(
        (event.get("thread_id") for event in events if event.get("type") == "thread.started"),
        None,
    )
    usage = next(
        (event.get("usage") for event in reversed(events) if event.get("type") == "turn.completed"),
        None,
    )
    messages = [
        event["item"]["text"]
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "agent_message"
        and isinstance(event["item"].get("text"), str)
    ]
    return thread_id if isinstance(thread_id, str) else None, usage if isinstance(usage, dict) else None, "\n".join(messages)


def normalized_usage(usage: dict) -> dict:
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    )
    normalized = {}
    for field in fields:
        value = usage.get(field, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"invalid {field}: {value!r}")
        normalized[field] = value
    if normalized["cached_input_tokens"] + normalized["cache_write_input_tokens"] > normalized["input_tokens"]:
        raise ValueError("cached plus cache-write input exceeds total input")
    if normalized["reasoning_output_tokens"] > normalized["output_tokens"]:
        raise ValueError("reasoning output exceeds total output")
    normalized["uncached_input_tokens"] = (
        normalized["input_tokens"]
        - normalized["cached_input_tokens"]
        - normalized["cache_write_input_tokens"]
    )
    normalized["total_tokens"] = normalized["input_tokens"] + normalized["output_tokens"]
    return normalized


def priced_usage(model: str, usage: dict, overrides: dict[str, float | None]) -> dict:
    defaults = RATES.get(model)
    selected = {
        "input": overrides.get("input") if overrides.get("input") is not None else (defaults or {}).get("input"),
        "cached_input": overrides.get("cached_input") if overrides.get("cached_input") is not None else (defaults or {}).get("cached_input"),
        "output": overrides.get("output") if overrides.get("output") is not None else (defaults or {}).get("output"),
    }
    age_days = (date.today() - RATES_CHECKED).days
    pricing_status = "current" if age_days <= RATE_MAX_AGE_DAYS else "stale"
    complete = all(isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) >= 0 for value in selected.values())
    credits = None
    api_rates = API_USD_RATES.get(model)
    api_list_price_usd = None
    if complete and pricing_status == "current":
        credits = (
            usage["uncached_input_tokens"] * float(selected["input"])
            + usage["cached_input_tokens"] * float(selected["cached_input"])
            + usage["output_tokens"] * float(selected["output"])
        ) / 1_000_000
        credits = round(credits, 8)
    if api_rates is not None and pricing_status == "current":
        api_list_price_usd = round((
            usage["uncached_input_tokens"] * api_rates["input"]
            + usage["cached_input_tokens"] * api_rates["cached_input"]
            + usage["output_tokens"] * api_rates["output"]
        ) / 1_000_000, 8)
    return {
        "status": pricing_status if complete else "unavailable",
        "checked_on": RATES_CHECKED.isoformat(),
        "age_days": age_days,
        "source": RATE_SOURCE,
        "rates_per_million_tokens": selected,
        "rate_card_credits": credits,
        "api_list_price": {
            "currency": "USD",
            "amount": api_list_price_usd,
            "rates_per_million_tokens": api_rates,
            "source": API_RATE_SOURCE if model != "gpt-5.3-codex" else "https://developers.openai.com/api/docs/models/gpt-5.3-codex",
            "kind": "API list-price equivalent; not a ChatGPT Pro charge or invoice",
        },
        "pro_20x_marginal_cash": {
            "currency": "USD",
            "amount_while_included_allowance_remains": 0.0,
            "after_limit": "account-specific purchased-credit price required",
        },
        "note": "output_tokens already includes reasoning_output_tokens; subscription credits and API-list-price equivalent are not a Pro invoice",
    }


def npx_executable() -> str:
    name = "npx.cmd" if os.name == "nt" else "npx"
    executable = shutil.which(name)
    if executable is None:
        raise ValueError(f"{name} is not available on PATH")
    return executable


def run_command(args: argparse.Namespace) -> int:
    prompt_path = resolved_file(args.prompt_file, "prompt file")
    working_directory = resolved_directory(args.working_directory, "working directory")
    output_path = Path(args.output).expanduser().resolve()
    prompt_bytes = prompt_path.read_bytes()
    prompt = prompt_bytes.decode("utf-8")
    if args.bypass_sandbox:
        if not args.disposable_root:
            raise ValueError("--bypass-sandbox requires --disposable-root")
        disposable_root = resolved_directory(args.disposable_root, "disposable root")
        if not is_within(working_directory, disposable_root):
            raise ValueError("working directory must be a strict descendant of disposable root")
    command = [
        npx_executable(),
        "-y",
        args.codex_package,
        "-a",
        "never",
        "exec",
    ]
    if args.ignore_user_config:
        command.append("--ignore-user-config")
    command.extend(("-m", args.model, "-c", f'model_reasoning_effort="{args.reasoning}"'))
    if args.bypass_sandbox:
        command.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        command.extend(("-s", args.sandbox))
    command.extend(("--skip-git-repo-check", "--json", "-"))
    started = time.perf_counter()
    process = subprocess.run(
        command,
        cwd=working_directory,
        input=prompt,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    wall_seconds = round(time.perf_counter() - started, 3)
    events = parse_events(process.stdout or "")
    thread_id, raw_usage, response_text = extract_receipt(events)
    usage = normalized_usage(raw_usage) if raw_usage is not None else None
    pricing = priced_usage(
        args.model,
        usage,
        {"input": args.input_rate, "cached_input": args.cached_input_rate, "output": args.output_rate},
    ) if usage is not None else None
    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL,
        "label": args.label,
        "status": "completed" if process.returncode == 0 and usage is not None else "failed",
        "configuration": {
            "model": args.model,
            "reasoning": args.reasoning,
            "codex_package": args.codex_package,
            "sandbox": "bypassed-disposable" if args.bypass_sandbox else args.sandbox,
            "ignore_user_config": args.ignore_user_config,
            "ephemeral": False,
        },
        "prompt": {
            "sha256": hashlib.sha256(prompt_bytes).hexdigest(),
            "utf8_bytes": len(prompt_bytes),
            "text_retained": False,
        },
        "thread_id": thread_id,
        "wall_seconds": wall_seconds,
        "usage": usage,
        "pricing": pricing,
        "process": {
            "exit_code": process.returncode,
            "stderr_tail": (process.stderr or "")[-2000:] if process.returncode != 0 else "",
        },
    }
    if args.include_response_text:
        result["response_text"] = response_text
    write_json(output_path, result)
    print(canonical_json(result), end="")
    return 0 if result["status"] == "completed" else 1


def aggregate_command(args: argparse.Namespace) -> int:
    stages = []
    for value in args.receipts:
        path = resolved_file(value, "receipt")
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt.get("tool") != TOOL or receipt.get("status") != "completed":
            raise ValueError(f"not a completed {TOOL} receipt: {path}")
        stages.append(receipt)
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "uncached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    credits = [stage.get("pricing", {}).get("rate_card_credits") for stage in stages]
    api_usd = [stage.get("pricing", {}).get("api_list_price", {}).get("amount") for stage in stages]
    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": f"{TOOL}.aggregate",
        "label": args.label,
        "stage_count": len(stages),
        "wall_seconds": round(sum(float(stage["wall_seconds"]) for stage in stages), 3),
        "usage": {field: sum(int(stage["usage"][field]) for stage in stages) for field in fields},
        "rate_card_credits": round(sum(float(value) for value in credits), 8) if all(value is not None for value in credits) else None,
        "api_list_price_usd": round(sum(float(value) for value in api_usd), 8) if all(value is not None for value in api_usd) else None,
        "pro_20x_marginal_cash_usd_while_included_allowance_remains": 0.0,
        "stages": [
            {
                "label": stage["label"],
                "thread_id": stage["thread_id"],
                "model": stage["configuration"]["model"],
                "reasoning": stage["configuration"]["reasoning"],
            }
            for stage in stages
        ],
        "quality": "not_measured_by_this_command",
    }
    if args.output:
        write_json(Path(args.output).expanduser().resolve(), result)
    print(canonical_json(result), end="")
    return 0


def recover_rollout_command(args: argparse.Namespace) -> int:
    rollout = resolved_file(args.rollout, "rollout")
    events = parse_events(rollout.read_text(encoding="utf-8", errors="replace"))
    task = next(
        (event.get("payload") for event in reversed(events) if event.get("type") == "event_msg" and isinstance(event.get("payload"), dict) and event["payload"].get("type") == "task_complete"),
        None,
    )
    usage_ledger = next(
        (event.get("payload", {}).get("info", {}).get("total_token_usage") for event in reversed(events) if event.get("type") == "event_msg" and event.get("payload", {}).get("type") == "token_count"),
        None,
    )
    if not isinstance(usage_ledger, dict) or (not isinstance(task, dict) and not args.allow_incomplete):
        raise ValueError("rollout has no completed task and final token ledger; use --allow-incomplete only after confirming the Codex process exited")
    incomplete = not isinstance(task, dict)
    timestamps = [
        datetime.fromisoformat(str(event["timestamp"]).replace("Z", "+00:00"))
        for event in events
        if event.get("timestamp")
    ]
    recovered_wall_seconds = (
        max(0.0, (timestamps[-1] - timestamps[0]).total_seconds())
        if timestamps
        else 0.0
    )
    usage = normalized_usage(usage_ledger)
    pricing = priced_usage(args.model, usage, {"input": None, "cached_input": None, "output": None})
    prompt = resolved_file(args.prompt_file, "prompt file") if args.prompt_file else None
    prompt_bytes = prompt.read_bytes() if prompt else b""
    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL,
        "label": args.label,
        "status": "interrupted" if incomplete else "completed",
        "configuration": {
            "model": args.model,
            "reasoning": args.reasoning,
            "codex_package": args.codex_package,
            "sandbox": "recovered-from-rollout",
            "ignore_user_config": True,
            "ephemeral": False,
        },
        "prompt": {
            "sha256": hashlib.sha256(prompt_bytes).hexdigest() if prompt else None,
            "utf8_bytes": len(prompt_bytes) if prompt else None,
            "text_retained": False,
        },
        "thread_id": task.get("turn_id") if isinstance(task, dict) else None,
        "wall_seconds": round(float(task.get("duration_ms", 0)) / 1000, 3) if isinstance(task, dict) else round(recovered_wall_seconds, 3),
        "usage": usage,
        "pricing": pricing,
        "process": {"exit_code": None, "stderr_tail": "", "recovery_reason": args.reason},
        "recovery": {
            "rollout": str(rollout),
            "sha256": hashlib.sha256(rollout.read_bytes()).hexdigest(),
            "incomplete": incomplete,
        },
    }
    if args.include_response_text:
        result["response_text"] = str(task.get("last_agent_message", "")) if isinstance(task, dict) else ""
    write_json(Path(args.output).expanduser().resolve(), result)
    print(canonical_json(result), end="")
    return 0


def self_test() -> int:
    events = parse_events(
        '\n'.join((
            '{"type":"thread.started","thread_id":"thread-1"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}',
            '{"type":"turn.completed","usage":{"input_tokens":1000,"cached_input_tokens":600,"cache_write_input_tokens":0,"output_tokens":100,"reasoning_output_tokens":40}}',
        ))
    )
    thread_id, raw_usage, message = extract_receipt(events)
    assert thread_id == "thread-1" and message == "ok" and raw_usage is not None
    usage = normalized_usage(raw_usage)
    assert usage["uncached_input_tokens"] == 400
    assert usage["total_tokens"] == 1100
    price = priced_usage(
        "test-model",
        usage,
        {"input": 10.0, "cached_input": 1.0, "output": 20.0},
    )
    assert price["rate_card_credits"] == 0.0066
    assert usage["output_tokens"] == 100 and usage["reasoning_output_tokens"] == 40
    print(canonical_json({"ok": True, "tool": TOOL, "checks": 6}), end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preferred = subparsers.add_parser("preferred-config")
    preferred.set_defaults(handler=lambda _args: (print(canonical_json(PREFERRED_CONFIG), end="") or 0))

    run = subparsers.add_parser("run")
    run.add_argument("--label", required=True)
    run.add_argument("--prompt-file", required=True)
    run.add_argument("--working-directory", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--model", default="gpt-5.6-sol")
    run.add_argument("--reasoning", choices=("low", "medium", "high", "xhigh", "max"), default="medium")
    run.add_argument("--sandbox", choices=("read-only", "workspace-write"), default="read-only")
    run.add_argument("--codex-package", default=DEFAULT_CODEX_PACKAGE)
    run.add_argument("--include-user-config", dest="ignore_user_config", action="store_false")
    run.add_argument("--include-response-text", action="store_true")
    run.add_argument("--bypass-sandbox", action="store_true")
    run.add_argument("--disposable-root")
    run.add_argument("--input-rate", type=float)
    run.add_argument("--cached-input-rate", type=float)
    run.add_argument("--output-rate", type=float)
    run.set_defaults(handler=run_command, ignore_user_config=True)

    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--label", required=True)
    aggregate.add_argument("--output")
    aggregate.add_argument("receipts", nargs="+")
    aggregate.set_defaults(handler=aggregate_command)

    recover = subparsers.add_parser("recover-rollout")
    recover.add_argument("--label", required=True)
    recover.add_argument("--rollout", required=True)
    recover.add_argument("--output", required=True)
    recover.add_argument("--model", required=True)
    recover.add_argument("--reasoning", choices=("low", "medium", "high", "xhigh", "max"), required=True)
    recover.add_argument("--prompt-file")
    recover.add_argument("--reason", default="outer measurement process did not exit after task_complete")
    recover.add_argument("--codex-package", default=DEFAULT_CODEX_PACKAGE)
    recover.add_argument("--include-response-text", action="store_true")
    recover.add_argument("--allow-incomplete", action="store_true", help="Recover an interrupted rollout with a final token ledger but no task_complete event")
    recover.set_defaults(handler=recover_rollout_command)

    test = subparsers.add_parser("self-test")
    test.set_defaults(handler=lambda _args: self_test())
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        return int(args.handler(args))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(canonical_json({"ok": False, "tool": TOOL, "error": str(exc)}), end="")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
