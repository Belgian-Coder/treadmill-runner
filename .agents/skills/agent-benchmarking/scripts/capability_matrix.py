#!/usr/bin/env python3
"""Run the same neutral command probes against two repository roots."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

DEFAULT_PROBES = [
    {
        "id": "workflow-eval-compact",
        "command": ["python", "-B", ".agents/manage.py", "workflow", "eval", "--all", "--summary", "--compact", "--format", "json"],
    },
    {
        "id": "workflow-smoke-compact",
        "command": ["python", "-B", ".agents/manage.py", "workflow", "smoke", "--all", "--summary", "--compact", "--format", "json"],
    },
    {
        "id": "workflow-context-all-compact",
        "command": ["python", "-B", ".agents/manage.py", "workflow", "context", "--all", "--check", "--summary", "--compact", "--format", "json"],
    },
    {
        "id": "credential-doctor-compact",
        "command": ["python", "-B", ".agents/manage.py", "credential-doctor", "--summary", "--compact", "--format", "json"],
    },
    {
        "id": "benchmark-doctor-summary",
        "command": ["python", "-B", ".agents/manage.py", "benchmark", "doctor", "--json", "--summary"],
    },
]


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise SystemExit(f"suite not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"suite is invalid JSON at line {exc.lineno}: {path}") from None


def load_probes(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return DEFAULT_PROBES
    data = read_json(path)
    if not isinstance(data, dict):
        raise SystemExit("capability matrix suite must be a JSON object.")
    probes = data.get("probes")
    if not isinstance(probes, list) or not probes:
        raise SystemExit("capability matrix suite must contain non-empty probes.")
    normalized: list[dict[str, Any]] = []
    for index, probe in enumerate(probes, start=1):
        if not isinstance(probe, dict):
            raise SystemExit(f"probe {index} must be an object.")
        command = probe.get("command")
        if not isinstance(command, list) or not command:
            raise SystemExit(f"probe {probe.get('id', index)!r} command must be a non-empty list.")
        normalized.append({"id": str(probe.get("id") or f"probe-{index}"), "command": [str(part) for part in command]})
    return normalized


def classify(exit_code: int, output: str) -> str:
    lowered = output.lower()
    if exit_code == 0:
        return "passed"
    if exit_code == 2 or "unrecognized arguments" in lowered or "unknown" in lowered and "command" in lowered:
        return "unsupported-command"
    if "not found" in lowered or "no such file" in lowered or "cannot find path" in lowered:
        return "missing-capability"
    return "failed"


def parse_json_output(output: str) -> Any:
    stripped = output.strip()
    if not stripped.startswith("{"):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def run_probe(root: Path, command: list[str], timeout_seconds: int) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
        output = completed.stdout or ""
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return {
            "ok": False,
            "status": "timeout",
            "exit_code": None,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "output_head": output[:800],
            "output_tail": output[-1200:],
        }
    parsed = parse_json_output(output)
    return {
        "ok": exit_code == 0,
        "status": classify(exit_code, output),
        "exit_code": exit_code,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "output_head": output[:800],
        "output_tail": output[-1200:],
        "summary": parsed.get("summary") if isinstance(parsed, dict) and isinstance(parsed.get("summary"), dict) else None,
    }


def compare_probe(probe: dict[str, Any], baseline_root: Path, candidate_root: Path, timeout_seconds: int) -> dict[str, Any]:
    baseline = run_probe(baseline_root, probe["command"], timeout_seconds)
    candidate = run_probe(candidate_root, probe["command"], timeout_seconds)
    if baseline["ok"] and candidate["ok"]:
        delta = "unchanged-pass"
    elif not baseline["ok"] and candidate["ok"]:
        delta = "candidate-gained"
    elif baseline["ok"] and not candidate["ok"]:
        delta = "candidate-regressed"
    else:
        delta = "unchanged-fail"
    return {
        "id": probe["id"],
        "command": probe["command"],
        "delta": delta,
        "baseline": baseline,
        "candidate": candidate,
    }


def build_report(
    baseline_root: Path,
    candidate_root: Path,
    probes: list[dict[str, Any]],
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    rows = [
        compare_probe(probe, baseline_root, candidate_root, timeout_seconds)
        for probe in probes
    ]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["delta"]] = counts.get(row["delta"], 0) + 1
    baseline_statuses: dict[str, int] = {}
    candidate_statuses: dict[str, int] = {}
    for row in rows:
        baseline_statuses[row["baseline"]["status"]] = baseline_statuses.get(row["baseline"]["status"], 0) + 1
        candidate_statuses[row["candidate"]["status"]] = candidate_statuses.get(row["candidate"]["status"], 0) + 1
    regressions = [row for row in rows if row["delta"] == "candidate-regressed"]
    return {
        "schema_version": 1,
        "tool": "agent-benchmarking.capability-matrix",
        "ok": not regressions,
        "status": "passed" if not regressions else "failed",
        "baseline_root": str(baseline_root),
        "candidate_root": str(candidate_root),
        "summary": {
            "probe_count": len(rows),
            "candidate_gained": counts.get("candidate-gained", 0),
            "candidate_regressed": counts.get("candidate-regressed", 0),
            "unchanged_pass": counts.get("unchanged-pass", 0),
            "unchanged_fail": counts.get("unchanged-fail", 0),
            "baseline_statuses": baseline_statuses,
            "candidate_statuses": candidate_statuses,
        },
        "probes": rows,
        "interpretation": "Capability deltas only. Do not treat candidate-only probes as old-vs-new quality deltas.",
    }


def compact_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": report["schema_version"],
        "tool": report["tool"],
        "ok": report["ok"],
        "status": report["status"],
        "summary": report["summary"],
        "regressions": [
            {
                "id": row["id"],
                "baseline_status": row["baseline"]["status"],
                "candidate_status": row["candidate"]["status"],
            }
            for row in report["probes"]
            if row["delta"] == "candidate-regressed"
        ],
        "gains": [
            {
                "id": row["id"],
                "baseline_status": row["baseline"]["status"],
                "candidate_status": row["candidate"]["status"],
            }
            for row in report["probes"]
            if row["delta"] == "candidate-gained"
        ],
        "interpretation": report["interpretation"],
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = ["# Capability Matrix", ""]
    lines.append(f"- Status: {report['status']}")
    lines.append(f"- Probes: {summary['probe_count']}")
    lines.append(f"- Candidate gained: {summary['candidate_gained']}")
    lines.append(f"- Candidate regressed: {summary['candidate_regressed']}")
    lines.append(f"- Unchanged pass: {summary['unchanged_pass']}")
    lines.append(f"- Unchanged fail: {summary['unchanged_fail']}")
    lines.append(f"- Interpretation: {report['interpretation']}")
    lines.extend(["", "| Probe | Delta | Baseline | Candidate |", "|---|---|---|---|"])
    for row in report["probes"]:
        lines.append(f"| `{row['id']}` | `{row['delta']}` | `{row['baseline']['status']}` | `{row['candidate']['status']}` |")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", required=True)
    parser.add_argument("--candidate-root", required=True)
    parser.add_argument("--suite")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    probes = load_probes(Path(args.suite) if args.suite else None)
    report = build_report(
        Path(args.baseline_root).expanduser().resolve(),
        Path(args.candidate_root).expanduser().resolve(),
        probes,
        timeout_seconds=args.timeout_seconds,
    )
    output = compact_report(report) if args.compact else report
    if args.format == "json":
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
