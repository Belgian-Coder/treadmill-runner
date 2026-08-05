#!/usr/bin/env python3
"""Explicitly publish .NET scanner analysis to SonarQube."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from sonarqube_client import CredentialPreflightError, require_target


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def run(command: list[str], cwd: Path) -> dict[str, object]:
    completed = subprocess.run(command, cwd=str(cwd), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return {"command": command, "returncode": completed.returncode, "output": completed.stdout[-12000:]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="upload/write/runtime: explicitly run .NET SonarScanner publishing")
    parser.add_argument("--project-root", default=".", help="read project root for scanner execution")
    parser.add_argument("--project-key", help="SonarQube project key")
    parser.add_argument("--base-url", help="network SonarQube base URL")
    parser.add_argument("--server-name", help="profile name from .agents/local-ai/secrets.local.json")
    parser.add_argument("--secrets-file", help="override the local profile store path")
    parser.add_argument("--token", help="credential value; prefer SONAR_TOKEN or a configured profile")
    parser.add_argument("--solution", default=".")
    parser.add_argument("--configuration", default="Release")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="upload/runtime: explicitly allow scanner publishing; default is no upload",
    )
    parser.add_argument("--output-json", help="write JSON scanner evidence to this path")
    args = parser.parse_args(argv)
    try:
        require_target(args, token_required=bool(args.publish))
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        if isinstance(error, CredentialPreflightError):
            print(f"Configure: {error.configure_command}", file=sys.stderr)
        return 1
    if not args.publish:
        payload = {
            "schema_version": 1,
            "tool": "sonarqube-diagnostics.run_analysis",
            "ok": True,
            "status": "skipped",
            "started_at": utc_now(),
            "finished_at": utc_now(),
            "project_key": args.project_key,
            "read_only": True,
            "no_upload_assertion": True,
            "summary": {"skipped": "scanner publishing requires --publish"},
            "checks": [
                {
                    "name": "sonarscanner-publish",
                    "kind": "command",
                    "ok": True,
                    "status": "skipped",
                    "summary": {"reason": "no upload by default"},
                }
            ],
            "skipped": ["scanner publishing skipped because --publish was not provided"],
            "results": [],
        }
        if args.output_json:
            path = Path(args.output_json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "status": "skipped", "reason": "pass --publish to upload"}, indent=2))
        return 0
    token = args.token or os.environ.get("SONAR_TOKEN")
    if not token:
        print("ERROR: scanner publishing requires --token or SONAR_TOKEN", file=sys.stderr)
        return 1
    if shutil.which("dotnet") is None:
        print("ERROR: dotnet was not found on PATH", file=sys.stderr)
        return 1
    root = Path(args.project_root).resolve()
    commands = [
        [
            "dotnet",
            "sonarscanner",
            "begin",
            f"/k:{args.project_key}",
            f"/d:sonar.host.url={args.base_url}",
            f"/d:sonar.token={token}",
        ],
        ["dotnet", "build", args.solution, "-c", args.configuration],
        ["dotnet", "sonarscanner", "end", f"/d:sonar.token={token}"],
    ]
    started_at = utc_now()
    results = []
    skipped: list[str] = []
    for index, command in enumerate(commands):
        result = run(command, root)
        results.append(result)
        if result["returncode"] != 0:
            for skipped_command in commands[index + 1 :]:
                skipped.append(" ".join(skipped_command[:3]))
            break
    for result in results:
        result["command"] = ["<redacted-token>" if str(part).startswith("/d:sonar.token=") else part for part in result["command"]]
        result["output"] = str(result["output"]).replace(token, "<redacted-token>")
    ok = all(result["returncode"] == 0 for result in results)
    payload = {
        "schema_version": 1,
        "tool": "sonarqube-diagnostics.run_analysis",
        "ok": ok,
        "status": "passed" if ok else "failed",
        "started_at": started_at,
        "finished_at": utc_now(),
        "project_key": args.project_key,
        "summary": {"steps": len(results), "failed": sum(1 for result in results if result["returncode"] != 0)},
        "checks": [
            {
                "name": "sonarscanner-publish",
                "kind": "command",
                "ok": ok,
                "status": "passed" if ok else "failed",
                "summary": {"steps": len(results)},
            }
        ],
        "skipped": skipped,
        "results": results,
    }
    if args.output_json:
        path = Path(args.output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": payload["ok"], "steps": len(results)}, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
