#!/usr/bin/env python3
"""Compare clean direct, harness without local AI, and harness with local AI artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import benchmark_common as common


MEASUREMENT_SCOPE = {
    "scope": "three-arm-artifact-envelope",
    "included": [
        "direct clean-control input and output artifacts",
        "workflow harness prompt-packet artifact input",
        "workflow harness saved output artifacts",
        "workflow harness local-AI artifacts as a separate non-paid bucket",
        "explicitly listed command timing artifacts from the pair report",
    ],
    "excluded": [
        "full live LLM transcript",
        "hidden orchestration prompts",
        "tool-call payloads not saved as listed artifacts",
        "project and repository files read outside listed artifacts",
        "subagent context",
        "billing telemetry",
        "full end-to-end wall-clock time",
    ],
    "billing_claim": False,
    "full_workflow_run_token_total": False,
    "plain_direct_control_is_live_llm_run": False,
    "interpretation": (
        "Use this report to compare explicit artifact envelopes for the same story. "
        "It is not a billing export and not proof of complete live plain LLM, true no-harness, or "
        "workflow implementation run usage."
    ),
}


def load_json(path: Path) -> dict[str, Any]:
    data = common.read_json(path)
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return data


def require_tool(data: dict[str, Any], tool: str, path: Path) -> None:
    if data.get("tool") != tool:
        raise SystemExit(f"{path} has tool {data.get('tool')!r}; expected {tool!r}")


def arm_from_plain(summary: dict[str, Any], path: Path) -> dict[str, Any]:
    tokens = summary.get("paid_model_tokens", {})
    return {
        "label": "direct-clean-artifact-envelope",
        "summary_path": str(path),
        "basis": summary.get("basis", ""),
        "input_tokens": int(tokens.get("input", 0) or 0),
        "output_tokens": int(tokens.get("output", 0) or 0),
        "total_tokens": int(tokens.get("total", 0) or 0),
        "local_ai_artifact_tokens": 0,
        "elapsed_seconds": None,
        "workflow_context_loaded": len(summary.get("workflow_context_loaded", [])),
        "skill_context_loaded": len(summary.get("skill_context_loaded", [])),
        "routing_context_loaded": len(summary.get("routing_context_loaded", [])),
    }


def arm_from_harness(summary: dict[str, Any], key: str, label: str) -> dict[str, Any]:
    arm = summary[key]
    return {
        "label": label,
        "summary_path": arm.get("summary_path", ""),
        "input_tokens": int(arm.get("paid_input_tokens", 0) or 0),
        "output_tokens": int(arm.get("paid_output_tokens", 0) or 0),
        "total_tokens": int(arm.get("paid_total_tokens", 0) or 0),
        "local_ai_artifact_tokens": int(arm.get("local_ai_artifact_tokens", 0) or 0),
        "elapsed_seconds": arm.get("elapsed_seconds"),
    }


def delta(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    result = {
        "input_tokens": int(right["input_tokens"]) - int(left["input_tokens"]),
        "output_tokens": int(right["output_tokens"]) - int(left["output_tokens"]),
        "total_tokens": int(right["total_tokens"]) - int(left["total_tokens"]),
        "local_ai_artifact_tokens": int(right["local_ai_artifact_tokens"]) - int(left["local_ai_artifact_tokens"]),
    }
    left_elapsed = left.get("elapsed_seconds")
    right_elapsed = right.get("elapsed_seconds")
    if isinstance(left_elapsed, (int, float)) and isinstance(right_elapsed, (int, float)):
        result["elapsed_seconds"] = round(float(right_elapsed) - float(left_elapsed), 3)
    else:
        result["elapsed_seconds"] = None
    return result


def compare_three_arm(*, plain_summary_path: Path, pair_summary_path: Path) -> dict[str, Any]:
    plain = load_json(plain_summary_path)
    pair = load_json(pair_summary_path)
    require_tool(plain, "agent-benchmarking.clean-folder-control", plain_summary_path)
    require_tool(pair, "agent-benchmarking.compare-prompt-packet-pair", pair_summary_path)

    plain_arm = arm_from_plain(plain, plain_summary_path)
    without_arm = arm_from_harness(pair, "without_local_ai", "workflow-harness-without-local-ai")
    with_arm = arm_from_harness(pair, "with_local_ai", "workflow-harness-with-local-ai")
    plain_counter = plain.get("advisory_token_estimates", {}).get("token_counter", {})
    pair_counter = pair.get("token_counter", {})
    checks = [
        {
            "name": "clean direct artifact envelope has no workflow, skill, or routing context",
            "ok": plain_arm["workflow_context_loaded"] == 0
            and plain_arm["skill_context_loaded"] == 0
            and plain_arm["routing_context_loaded"] == 0,
        },
        {
            "name": "harness pair comparison passed",
            "ok": pair.get("ok") is True,
        },
        {
            "name": "counter method matches",
            "ok": plain_counter.get("method") == pair_counter.get("method"),
            "plain": plain_counter,
            "harness_pair": pair_counter,
        },
    ]
    return {
        "schema_version": 1,
        "tool": "agent-benchmarking.compare-three-arm-artifact-tokens",
        "ok": all(check["ok"] for check in checks),
        "status": "compared",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "measurement_scope": MEASUREMENT_SCOPE,
        "suite_identity": pair.get("suite_identity", {}),
        "checks": checks,
        "arms": {
            "plain_direct_no_harness": plain_arm,
            "workflow_harness_without_local_ai": without_arm,
            "workflow_harness_with_local_ai": with_arm,
        },
        "deltas": {
            "harness_without_local_ai_minus_plain": delta(plain_arm, without_arm),
            "harness_with_local_ai_minus_plain": delta(plain_arm, with_arm),
            "harness_with_local_ai_minus_without_local_ai": delta(without_arm, with_arm),
        },
        "token_counter": pair_counter,
    }


def render_markdown(report: dict[str, Any]) -> str:
    arms = report["arms"]
    deltas = report["deltas"]
    plain = arms["plain_direct_no_harness"]
    without = arms["workflow_harness_without_local_ai"]
    with_ai = arms["workflow_harness_with_local_ai"]
    local_delta = deltas["harness_with_local_ai_minus_without_local_ai"]
    lines = [
        "# Three-Arm Artifact Comparison",
        "",
        f"- OK: {str(report['ok']).lower()}",
        f"- Suite: `{report['suite_identity'].get('suite_id', '')}`",
        f"- Story hash: `{report['suite_identity'].get('story_hash', '')}`",
        "- Boundary: artifact envelopes only; not a full live LLM transcript, billing export, or complete workflow run usage measurement.",
        "",
        "| Arm | Input Artifacts | Output Artifacts | Total Artifacts | Local-AI Artifacts | Command Seconds |",
        "|---|---:|---:|---:|---:|---:|",
        f"| Direct clean artifact envelope | {plain['input_tokens']} | {plain['output_tokens']} | {plain['total_tokens']} | {plain['local_ai_artifact_tokens']} | n/a |",
        f"| Workflow harness without local AI | {without['input_tokens']} | {without['output_tokens']} | {without['total_tokens']} | {without['local_ai_artifact_tokens']} | {without['elapsed_seconds']} |",
        f"| Workflow harness with local AI | {with_ai['input_tokens']} | {with_ai['output_tokens']} | {with_ai['total_tokens']} | {with_ai['local_ai_artifact_tokens']} | {with_ai['elapsed_seconds']} |",
        "",
        "## Deltas",
        "",
        f"- Harness without local AI minus direct clean artifact envelope: `{deltas['harness_without_local_ai_minus_plain']['total_tokens']}` artifact tokens.",
        f"- Harness with local AI minus direct clean artifact envelope: `{deltas['harness_with_local_ai_minus_plain']['total_tokens']}` artifact tokens.",
        f"- Harness with local AI minus harness without local AI: `{local_delta['total_tokens']}` artifact tokens and `{local_delta['elapsed_seconds']}` measured command seconds.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plain-summary", required=True)
    parser.add_argument("--pair-summary", required=True)
    parser.add_argument("--output-root", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--allow-existing", action="store_true")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown", dest="output_format")
    return parser


def main(argv: list[str] | None = None) -> int:
    common.require_supported_python()
    args = build_parser().parse_args(argv)
    report = compare_three_arm(
        plain_summary_path=Path(args.plain_summary).expanduser().resolve(),
        pair_summary_path=Path(args.pair_summary).expanduser().resolve(),
    )
    if args.output_root and args.run_id:
        output_root = Path(args.output_root).expanduser().resolve() / args.run_id
        if output_root.exists() and any(output_root.iterdir()) and not args.allow_existing:
            raise SystemExit(f"comparison folder already exists and is not empty: {output_root}")
        output_root.mkdir(parents=True, exist_ok=True)
        common.write_json(output_root / "summary.json", report)
        common.write_text(output_root / "SUMMARY.md", render_markdown(report))
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
