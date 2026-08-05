#!/usr/bin/env python3
"""Compare with/without-local-AI benchmark prompt-packet run artifacts."""

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
    "scope": "prompt-packet-artifacts",
    "included": [
        "prompt-packet markdown tokens from each arm",
        "explicitly listed saved output artifact files",
        "explicitly listed local-AI advisory artifact files as a separate non-paid bucket",
        "explicitly listed timing files",
    ],
    "excluded": [
        "full live workflow context",
        "project and repository files read outside the listed artifacts",
        "hidden orchestration prompts",
        "tool-call payloads not saved as listed artifacts",
        "subagent context",
        "billing telemetry",
        "full end-to-end agent wall-clock time",
    ],
    "billing_claim": False,
    "full_workflow_run_token_total": False,
    "interpretation": (
        "Use this report only as a deterministic artifact-count comparison for a prompt-packet "
        "candidate. It is not a billing export and not a complete story implementation run "
        "usage measurement."
    ),
}


def rel_path(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def measure_file(path: Path, root: Path, role: str) -> dict[str, Any]:
    text = common.read_text(path)
    return {
        "role": role,
        "path": rel_path(path, root),
        "exists": path.exists(),
        "tokens": common.estimate_tokens(text),
        "bytes": path.stat().st_size if path.exists() else 0,
    }


def load_summary(path: Path) -> dict[str, Any]:
    data = common.read_json(path)
    if not isinstance(data, dict) or data.get("tool") != "agent-benchmarking.benchmark-prompt-packet":
        raise SystemExit(f"not a prompt-packet summary: {path}")
    return data


def elapsed_seconds(paths: list[Path]) -> tuple[float, list[dict[str, Any]]]:
    total = 0.0
    evidence: list[dict[str, Any]] = []
    for path in paths:
        data = common.read_json(path)
        seconds = 0.0
        if isinstance(data, dict):
            if isinstance(data.get("elapsed_seconds"), (int, float)):
                seconds = float(data["elapsed_seconds"])
            elif isinstance(data.get("commands"), list):
                seconds = sum(
                    float(command.get("elapsed_seconds", 0) or 0)
                    for command in data["commands"]
                    if isinstance(command, dict)
                )
        total += seconds
        evidence.append({"path": str(path), "elapsed_seconds": round(seconds, 3)})
    return round(total, 3), evidence


def suite_identity(summary: dict[str, Any]) -> dict[str, Any]:
    suite = summary.get("suite", {})
    reference = suite.get("reference_fixture", {}) if isinstance(suite, dict) else {}
    return {
        "suite_id": suite.get("suite_id", ""),
        "prompt_version": suite.get("prompt_version", ""),
        "story_hash": suite.get("story_hash", ""),
        "fixture_hash": reference.get("fixture_hash", ""),
    }


def artifact_tokens(paths: list[Path], root: Path, role: str) -> tuple[int, list[dict[str, Any]]]:
    measurements = [measure_file(path, root, role) for path in paths]
    return sum(item["tokens"] for item in measurements), measurements


def compare_pair(
    *,
    without_summary_path: Path,
    with_summary_path: Path,
    without_output_paths: list[Path],
    with_output_paths: list[Path],
    without_local_ai_paths: list[Path],
    with_local_ai_paths: list[Path],
    without_timing_paths: list[Path],
    with_timing_paths: list[Path],
    root: Path,
) -> dict[str, Any]:
    without_summary = load_summary(without_summary_path)
    with_summary = load_summary(with_summary_path)
    without_identity = suite_identity(without_summary)
    with_identity = suite_identity(with_summary)
    without_output_tokens, without_outputs = artifact_tokens(without_output_paths, root, "paid-output")
    with_output_tokens, with_outputs = artifact_tokens(with_output_paths, root, "paid-output")
    without_local_tokens, without_local = artifact_tokens(without_local_ai_paths, root, "local-ai-artifact")
    with_local_tokens, with_local = artifact_tokens(with_local_ai_paths, root, "local-ai-artifact")
    without_elapsed, without_timing = elapsed_seconds(without_timing_paths)
    with_elapsed, with_timing = elapsed_seconds(with_timing_paths)
    without_input = int(without_summary.get("tokens", {}).get("prompt_packet_markdown", 0) or 0)
    with_input = int(with_summary.get("tokens", {}).get("prompt_packet_markdown", 0) or 0)
    without_paid_total = without_input + without_output_tokens
    with_paid_total = with_input + with_output_tokens
    checks = [
        {
            "name": "same suite identity",
            "ok": without_identity == with_identity,
            "without": without_identity,
            "with": with_identity,
        },
        {
            "name": "without arm has local AI disabled",
            "ok": without_summary.get("local_ai_enabled") is False,
        },
        {
            "name": "with arm has local AI enabled",
            "ok": with_summary.get("local_ai_enabled") is True,
        },
    ]
    return {
        "schema_version": 1,
        "tool": "agent-benchmarking.compare-prompt-packet-pair",
        "ok": all(check["ok"] for check in checks),
        "status": "compared",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "measurement_scope": MEASUREMENT_SCOPE,
        "suite_identity": without_identity,
        "checks": checks,
        "without_local_ai": {
            "summary_path": str(without_summary_path),
            "paid_input_tokens": without_input,
            "paid_output_tokens": without_output_tokens,
            "paid_total_tokens": without_paid_total,
            "local_ai_artifact_tokens": without_local_tokens,
            "elapsed_seconds": without_elapsed,
            "output_files": without_outputs,
            "local_ai_artifacts": without_local,
            "timing": without_timing,
        },
        "with_local_ai": {
            "summary_path": str(with_summary_path),
            "paid_input_tokens": with_input,
            "paid_output_tokens": with_output_tokens,
            "paid_total_tokens": with_paid_total,
            "local_ai_artifact_tokens": with_local_tokens,
            "elapsed_seconds": with_elapsed,
            "output_files": with_outputs,
            "local_ai_artifacts": with_local,
            "timing": with_timing,
        },
        "delta_with_minus_without": {
            "paid_input_tokens": with_input - without_input,
            "paid_output_tokens": with_output_tokens - without_output_tokens,
            "paid_total_tokens": with_paid_total - without_paid_total,
            "local_ai_artifact_tokens": with_local_tokens - without_local_tokens,
            "elapsed_seconds": round(with_elapsed - without_elapsed, 3),
        },
        "saved_by_with_local_ai": {
            "paid_input_tokens": without_input - with_input,
            "paid_output_tokens": without_output_tokens - with_output_tokens,
            "paid_total_tokens": without_paid_total - with_paid_total,
        },
        "token_counter": common.token_count_metadata(),
    }


def render_markdown(report: dict[str, Any]) -> str:
    without = report["without_local_ai"]
    with_ai = report["with_local_ai"]
    delta = report["delta_with_minus_without"]
    saved = report["saved_by_with_local_ai"]
    scope = report.get("measurement_scope", MEASUREMENT_SCOPE)
    lines = [
        "# Prompt-Packet Artifact Local-AI Pair Comparison",
        "",
        f"- OK: {str(report['ok']).lower()}",
        f"- Suite: `{report['suite_identity'].get('suite_id', '')}`",
        f"- Story hash: `{report['suite_identity'].get('story_hash', '')}`",
        f"- Measurement scope: {scope.get('scope', 'prompt-packet-artifacts')}",
        "- Boundary: saved prompt-packet and output artifacts only; not a complete workflow run usage measurement, billing export, or hidden orchestration measurement.",
        "",
        "| Metric | Without Local AI | With Local AI | Delta With - Without |",
        "|---|---:|---:|---:|",
        f"| Artifact input tokens (prompt packet) | {without['paid_input_tokens']} | {with_ai['paid_input_tokens']} | {delta['paid_input_tokens']} |",
        f"| Artifact output tokens (saved docs) | {without['paid_output_tokens']} | {with_ai['paid_output_tokens']} | {delta['paid_output_tokens']} |",
        f"| Artifact total tokens | {without['paid_total_tokens']} | {with_ai['paid_total_tokens']} | {delta['paid_total_tokens']} |",
        f"| Local-AI artifact tokens (separate) | {without['local_ai_artifact_tokens']} | {with_ai['local_ai_artifact_tokens']} | {delta['local_ai_artifact_tokens']} |",
        f"| Measured command seconds | {without['elapsed_seconds']} | {with_ai['elapsed_seconds']} | {delta['elapsed_seconds']} |",
        "",
        f"Artifact tokens avoided by the with-local-AI arm: `{saved['paid_total_tokens']}` total "
        f"(`{saved['paid_input_tokens']}` input, `{saved['paid_output_tokens']}` output).",
    ]
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--without-summary", required=True)
    parser.add_argument("--with-summary", required=True)
    parser.add_argument("--without-output-path", action="append", default=[])
    parser.add_argument("--with-output-path", action="append", default=[])
    parser.add_argument("--without-local-ai-path", action="append", default=[])
    parser.add_argument("--with-local-ai-path", action="append", default=[])
    parser.add_argument("--without-timing-path", action="append", default=[])
    parser.add_argument("--with-timing-path", action="append", default=[])
    parser.add_argument("--output-root", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--allow-existing", action="store_true")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown", dest="output_format")
    return parser


def main(argv: list[str] | None = None) -> int:
    common.require_supported_python()
    args = build_parser().parse_args(argv)
    root = Path.cwd().resolve()
    report = compare_pair(
        without_summary_path=Path(args.without_summary).resolve(),
        with_summary_path=Path(args.with_summary).resolve(),
        without_output_paths=[Path(item).resolve() for item in args.without_output_path],
        with_output_paths=[Path(item).resolve() for item in args.with_output_path],
        without_local_ai_paths=[Path(item).resolve() for item in args.without_local_ai_path],
        with_local_ai_paths=[Path(item).resolve() for item in args.with_local_ai_path],
        without_timing_paths=[Path(item).resolve() for item in args.without_timing_path],
        with_timing_paths=[Path(item).resolve() for item in args.with_timing_path],
        root=root,
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
