#!/usr/bin/env python3
"""Portable wrapper for common .NET static analysis commands."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def run(command: list[str], cwd: Path, timeout_seconds: int) -> dict[str, object]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return {
            "command": command,
            "returncode": None,
            "output": output[-8000:],
            "error": f"timed out after {timeout_seconds}s",
        }
    return {"command": command, "returncode": completed.returncode, "output": completed.stdout[-8000:]}


def discover_solution(root: Path) -> str | None:
    solutions = sorted(root.rglob("*.sln"))
    if solutions:
        return str(solutions[0].relative_to(root))
    projects = sorted(root.rglob("*.csproj"))
    if projects:
        return str(projects[0].relative_to(root))
    return None


def is_build_output_path(path: Path) -> bool:
    return any(part in {"bin", "obj"} for part in path.parts)


def uses_packages_config(root: Path) -> bool:
    return any(
        path.is_file() and not is_build_output_path(path)
        for path in sorted(root.rglob("packages.config"))
    )


def target_frameworks(root: Path) -> list[str]:
    frameworks: set[str] = set()
    for project in sorted(root.rglob("*.csproj")):
        if is_build_output_path(project):
            continue
        try:
            xml_root = ET.parse(project).getroot()
        except ET.ParseError:
            continue
        for tag in ("TargetFramework", "TargetFrameworks"):
            for node in xml_root.findall(f".//{{*}}{tag}"):
                for item in (node.text or "").replace(",", ";").split(";"):
                    if item.strip():
                        frameworks.add(item.strip())
    return sorted(frameworks)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--solution")
    parser.add_argument("--configuration", default="Release")
    parser.add_argument("--skip-format", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument(
        "--no-restore",
        action="store_true",
        help="pass --no-restore to dotnet build; packages.config projects enable this automatically",
    )
    parser.add_argument("--run-tests", action="store_true")
    parser.add_argument("--plan-only", action="store_true", help="emit planned commands and project facts without running dotnet")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--output-json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.project_root).resolve()
    target = args.solution or discover_solution(root)
    packages_config = uses_packages_config(root)
    build_no_restore = args.no_restore or packages_config
    commands: list[list[str]] = []
    if target and not args.skip_format:
        format_command = ["dotnet", "format", target, "--verify-no-changes"]
        if build_no_restore:
            format_command.append("--no-restore")
        commands.append(format_command)
    if target and not args.skip_build:
        build_command = ["dotnet", "build", target, "-c", args.configuration, "-warnaserror"]
        if build_no_restore:
            build_command.append("--no-restore")
        commands.append(build_command)
    if target and args.run_tests:
        commands.append(["dotnet", "test", target, "-c", args.configuration, "--no-build"])
    facts = {
        "project_root": str(root),
        "target": target,
        "target_frameworks": target_frameworks(root),
        "uses_packages_config": packages_config,
        "build_no_restore": build_no_restore,
        "commands": commands,
    }
    if target is None:
        payload = {"ok": True, "status": "skipped", "reason": "no .NET solution or project discovered", **facts}
        if args.output_json:
            path = Path(args.output_json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 0 if args.plan_only else 1
    if args.plan_only:
        payload = {"ok": True, "status": "planned", **facts}
        if args.output_json:
            path = Path(args.output_json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 0
    if shutil.which("dotnet") is None:
        print("ERROR: dotnet was not found on PATH", file=sys.stderr)
        return 1
    if not commands:
        payload = {"ok": False, "project_root": str(root), "results": [], "error": "no static analysis commands selected"}
        if args.output_json:
            path = Path(args.output_json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print("ERROR: no static analysis commands selected", file=sys.stderr)
        return 1
    results = [run(command, root, args.timeout_seconds) for command in commands]
    ok = all(result["returncode"] == 0 for result in results)
    payload = {"ok": ok, **facts, "results": results}
    if args.output_json:
        path = Path(args.output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    for result in results:
        print(f"{' '.join(result['command'])}: exit {result['returncode']}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
