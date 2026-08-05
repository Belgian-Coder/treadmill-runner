#!/usr/bin/env python3
"""Compare local coverage summary with SonarQube coverage export."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def remote_coverage_percent(remote: dict[str, object]) -> float | None:
    for measure in remote.get("measures", []):
        if measure.get("metric") in {"coverage", "line_coverage"}:
            return float(measure.get("value", 0))
    component = remote.get("component", {})
    if isinstance(component, dict):
        for measure in component.get("measures", []):
            if measure.get("metric") in {"coverage", "line_coverage"}:
                return float(measure.get("value", 0))
    return None


def require_percent(value: object, label: str) -> float:
    if value is None:
        raise ValueError(f"{label} is missing")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not numeric: {value}") from exc


def compare(local_path: Path, remote_path: Path, tolerance: float) -> dict[str, object]:
    started_at = utc_now()
    local = json.loads(local_path.read_text(encoding="utf-8"))
    remote = json.loads(remote_path.read_text(encoding="utf-8"))
    local_percent = require_percent(local.get("coverage_percent"), "local coverage_percent")
    remote_percent = require_percent(remote_coverage_percent(remote), "remote coverage")
    delta = round(local_percent - remote_percent, 2)
    ok = abs(delta) <= tolerance
    return {
        "schema_version": 1,
        "tool": "sonarqube-diagnostics.compare_coverage",
        "ok": ok,
        "status": "passed" if ok else "failed",
        "started_at": started_at,
        "finished_at": utc_now(),
        "local_coverage_percent": local_percent,
        "remote_coverage_percent": remote_percent,
        "delta": delta,
        "tolerance": tolerance,
        "summary": {
            "local_coverage_percent": local_percent,
            "remote_coverage_percent": remote_percent,
            "delta": delta,
            "tolerance": tolerance,
        },
        "checks": [
            {
                "name": "coverage-comparison",
                "kind": "analysis",
                "ok": ok,
                "status": "passed" if ok else "failed",
                "summary": {"delta": delta, "tolerance": tolerance},
            }
        ],
        "skipped": [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="offline/read-only: compare existing local and SonarQube coverage exports")
    parser.add_argument("--local-summary", required=True, help="read existing local coverage summary JSON")
    parser.add_argument("--remote-export", required=True, help="read existing SonarQube coverage export JSON")
    parser.add_argument("--tolerance", type=float, default=1.0)
    parser.add_argument("--output-json", help="write JSON comparison evidence to this path")
    args = parser.parse_args(argv)
    try:
        payload = compare(Path(args.local_summary), Path(args.remote_export), args.tolerance)
    except Exception as exc:
        payload = {
            "schema_version": 1,
            "tool": "sonarqube-diagnostics.compare_coverage",
            "ok": False,
            "status": "failed",
            "started_at": utc_now(),
            "finished_at": utc_now(),
            "summary": {"error": str(exc)},
            "checks": [
                {
                    "name": "coverage-comparison",
                    "kind": "analysis",
                    "ok": False,
                    "status": "failed",
                    "summary": {"error": str(exc)},
                }
            ],
            "skipped": [],
        }
        if args.output_json:
            path = Path(args.output_json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 1
    if args.output_json:
        path = Path(args.output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
