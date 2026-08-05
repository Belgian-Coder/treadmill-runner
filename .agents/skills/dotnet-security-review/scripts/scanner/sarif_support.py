#!/usr/bin/env python3
"""Small stdlib SARIF helpers for dotnet-security-review."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True


def load_sarif(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or str(data.get("version")) != "2.1.0":
        raise ValueError(f"unsupported SARIF file: {path}")
    return data


def extract_findings(path: Path) -> list[dict[str, Any]]:
    sarif = load_sarif(path)
    rows: list[dict[str, Any]] = []
    for run in sarif.get("runs", []):
        if not isinstance(run, dict):
            continue
        tool = ((run.get("tool") or {}).get("driver") or {}).get("name", "sarif")
        for result in run.get("results", []):
            if not isinstance(result, dict):
                continue
            location = {}
            locations = result.get("locations")
            if isinstance(locations, list) and locations:
                location = locations[0] if isinstance(locations[0], dict) else {}
            physical = location.get("physicalLocation") if isinstance(location, dict) else {}
            artifact = (physical or {}).get("artifactLocation") or {}
            region = (physical or {}).get("region") or {}
            rows.append(
                {
                    "rule_id": str(result.get("ruleId", "")),
                    "severity": str(result.get("level", "warning")),
                    "message": str((result.get("message") or {}).get("text", "")),
                    "path": str(artifact.get("uri", "")),
                    "line": region.get("startLine"),
                    "tool": str(tool),
                    "source_sarif": str(path),
                }
            )
    return rows


def sarif_from_findings(findings: list[dict[str, Any]], tool_name: str) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for finding in findings:
        path = str(finding.get("path", ""))
        line = finding.get("line") or 1
        results.append(
            {
                "ruleId": str(finding.get("rule_id", "SEC000")),
                "level": "error" if str(finding.get("severity")) == "high" else "warning",
                "message": {"text": str(finding.get("message", ""))},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": path},
                            "region": {"startLine": int(line)},
                        }
                    }
                ],
            }
        )
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{"tool": {"driver": {"name": tool_name}}, "results": results}],
    }


def summarize_sarif(paths: list[Path]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for path in paths:
        findings.extend(extract_findings(path))
    levels: dict[str, int] = {}
    for row in findings:
        severity = str(row.get("severity", "warning"))
        levels[severity] = levels.get(severity, 0) + 1
    return {"files": [str(path) for path in paths], "findings": findings, "levels": levels}
