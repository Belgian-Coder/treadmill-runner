"""Safe installed dotnet CLI probes for project-context enrichment."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from .common import PROJECT_SUFFIXES
from .project_files import ITEMS_TO_PROBE, PROPERTIES_TO_PROBE, split_frameworks

FORBIDDEN_DOTNET_TOKENS = {
    "add",
    "build",
    "format",
    "list",
    "new",
    "nuget",
    "pack",
    "package",
    "publish",
    "remove",
    "restore",
    "search",
    "test",
    "tool",
    "workload",
}
ALLOWED_DOTNET_COMMANDS = {"--info", "sln", "msbuild"}

Runner = Callable[[list[str], Path, int], dict[str, object]]

def default_runner(command: list[str], cwd: Path, timeout_seconds: int) -> dict[str, object]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_seconds,
    )
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }

def assert_safe_dotnet_command(command: list[str]) -> None:
    if len(command) < 2:
        raise RuntimeError("dotnet command is empty")
    dotnet_command = command[1].lower()
    if dotnet_command not in ALLOWED_DOTNET_COMMANDS:
        raise RuntimeError(f"unsafe dotnet command refused: {' '.join(command)}")
    if dotnet_command == "msbuild":
        switches = [part.lower() for part in command[2:] if part.startswith("-")]
        if not switches or not all(part.startswith("-getproperty:") or part.startswith("-getitem:") for part in switches):
            raise RuntimeError(f"unsafe dotnet msbuild command refused: {' '.join(command)}")
    for token in command[1:]:
        lowered = token.lower()
        if lowered in FORBIDDEN_DOTNET_TOKENS and not (command[1].lower() == "sln" and lowered == "list"):
            raise RuntimeError(f"unsafe dotnet command refused: {' '.join(command)}")

def run_safe_dotnet(command: list[str], cwd: Path, runner: Runner, *, timeout_seconds: int = 20) -> dict[str, object]:
    assert_safe_dotnet_command(command)
    try:
        return runner(command, cwd, timeout_seconds)
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "timed out"}
    except OSError as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(exc)}

def parse_key_value_lines(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            values[key] = value
    return values

def parse_repeated_key_value_lines(text: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            values.setdefault(key, []).append(value)
    return values

def parse_dotnet_version(info_text: str) -> str:
    for pattern in (r"\.NET SDK:\s+Version:\s*([^\s]+)", r"Version:\s*([^\s]+)"):
        match = re.search(pattern, info_text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip()
    return ""

def resolve_dotnet(dotnet_executable: str | None) -> str:
    if dotnet_executable is None:
        return shutil.which("dotnet") or ""
    return dotnet_executable

def cli_probe_report(
    root: Path,
    *,
    dotnet_path: str,
    solutions: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    runner: Runner,
) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, str]]]:
    dotnet_cli = {
        "available": bool(dotnet_path),
        "path": dotnet_path,
        "version": "",
        "info": "",
        "probes_run": [],
        "probes_failed": [],
    }
    skipped: list[dict[str, str]] = []
    advisories: list[dict[str, str]] = []
    if not dotnet_path:
        skipped.append({"id": "dotnet-cli-probes", "reason": "dotnet executable was not found; static project facts only"})
        advisories.append({"id": "dotnet-cli-missing", "message": "Install or expose the project-required .NET SDK to enrich evaluated MSBuild facts."})
        return dotnet_cli, skipped, advisories

    info = run_safe_dotnet([dotnet_path, "--info"], root, runner)
    dotnet_cli["probes_run"].append("dotnet --info")
    dotnet_cli["info"] = str(info.get("stdout", ""))[:12000]
    dotnet_cli["version"] = parse_dotnet_version(str(info.get("stdout", "")))
    if not info.get("ok"):
        dotnet_cli["probes_failed"].append({"command": "dotnet --info", "stderr": str(info.get("stderr", ""))[:1000]})

    for solution in solutions[:5]:
        command = [dotnet_path, "sln", str(root / str(solution.get("path", ""))), "list"]
        result = run_safe_dotnet(command, root, runner)
        dotnet_cli["probes_run"].append(f"dotnet sln {solution.get('path')} list")
        if result.get("ok"):
            listed = [
                line.strip().replace("\\", "/")
                for line in str(result.get("stdout", "")).splitlines()
                if line.strip().lower().endswith(tuple(PROJECT_SUFFIXES))
            ]
            solution["listed_projects"] = listed
        else:
            dotnet_cli["probes_failed"].append({"command": f"dotnet sln {solution.get('path')} list", "stderr": str(result.get("stderr", ""))[:1000]})
    if len(solutions) > 5:
        skipped.append({"id": "dotnet-solution-probe-limit", "reason": "only the first 5 solution files were probed"})

    for project in projects[:20]:
        project_path = root / str(project.get("path", ""))
        property_command = [dotnet_path, "msbuild", str(project_path), "-getProperty:" + ";".join(PROPERTIES_TO_PROBE)]
        property_result = run_safe_dotnet(property_command, root, runner)
        dotnet_cli["probes_run"].append(f"dotnet msbuild {project.get('path')} -getProperty")
        if property_result.get("ok"):
            evaluated = parse_key_value_lines(str(property_result.get("stdout", "")))
            project["evaluated"] = evaluated
            target_frameworks = split_frameworks(evaluated.get("TargetFramework", ""), evaluated.get("TargetFrameworks", ""))
            if target_frameworks:
                project["target_frameworks"] = sorted(dict.fromkeys([*project.get("target_frameworks", []), *target_frameworks]))
            if evaluated.get("OutputType"):
                project["output_type"] = evaluated["OutputType"]
        else:
            dotnet_cli["probes_failed"].append({"command": f"dotnet msbuild {project.get('path')} -getProperty", "stderr": str(property_result.get("stderr", ""))[:1000]})

        item_command = [dotnet_path, "msbuild", str(project_path), "-getItem:" + ";".join(ITEMS_TO_PROBE)]
        item_result = run_safe_dotnet(item_command, root, runner)
        dotnet_cli["probes_run"].append(f"dotnet msbuild {project.get('path')} -getItem")
        if item_result.get("ok"):
            items = parse_repeated_key_value_lines(str(item_result.get("stdout", "")))
            if items.get("PackageReference"):
                project["evaluated_package_references"] = sorted(dict.fromkeys(items["PackageReference"]))
            if items.get("ProjectReference"):
                project["evaluated_project_references"] = sorted(dict.fromkeys(items["ProjectReference"]))
        else:
            dotnet_cli["probes_failed"].append({"command": f"dotnet msbuild {project.get('path')} -getItem", "stderr": str(item_result.get("stderr", ""))[:1000]})
    if len(projects) > 20:
        skipped.append({"id": "dotnet-project-probe-limit", "reason": "only the first 20 project files were probed"})
    return dotnet_cli, skipped, advisories
