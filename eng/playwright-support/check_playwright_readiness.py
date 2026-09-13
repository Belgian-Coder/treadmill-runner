#!/usr/bin/env python3
"""Check or explicitly prepare Playwright readiness for a local project."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import playwright_project_support as support


def resolve_command(command: list[str]) -> list[str]:
    if not command:
        return command
    resolved = shutil.which(command[0])
    if not resolved:
        return command
    return [resolved, *command[1:]]


def run(command: list[str], cwd: Path, timeout_seconds: int) -> dict[str, Any]:
    started = time.perf_counter()
    resolved_command = resolve_command(command)
    try:
        completed = subprocess.run(
            resolved_command,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return {
            "command": command,
            "resolved_command": resolved_command,
            "returncode": None,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "output": output[-4000:],
            "error": f"timed out after {timeout_seconds}s",
        }
    except OSError as exc:
        return {
            "command": command,
            "resolved_command": resolved_command,
            "returncode": None,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "output": "",
            "error": str(exc),
        }
    return {
        "command": command,
        "resolved_command": resolved_command,
        "returncode": completed.returncode,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "output": completed.stdout[-4000:],
    }


def signal_groups(signals: list[str]) -> dict[str, list[str]]:
    return {
        "nodejs": [signal for signal in signals if not signal.startswith(("csproj:", "requirements:", "pyproject:"))],
        "python": [signal for signal in signals if signal.startswith(("requirements:", "pyproject:"))],
        "dotnet": [signal for signal in signals if signal.startswith("csproj:")],
    }


def python_executable() -> str:
    return sys.executable or shutil.which("python") or shutil.which("python3") or "python"


def add_planned_command(planned_commands: list[list[str]], command: list[str]) -> None:
    if command and command not in planned_commands:
        planned_commands.append(command)


def node_install_command(root: Path) -> list[str]:
    return ["npm", "ci"] if (root / "package-lock.json").exists() else ["npm", "install"]


def python_package_install_command(root: Path, python_path: str) -> list[str] | None:
    manifests = support.python_playwright_manifests(root)
    requirement_manifests = [item for item in manifests if item["kind"] == "requirements"]
    if requirement_manifests:
        return [python_path, "-m", "pip", "install", "-r", requirement_manifests[0]["path"]]
    packages: set[str] = set()
    for manifest in manifests:
        if manifest["kind"] == "requirements":
            packages.update(str(package) for package in manifest["packages"])
        elif manifest["kind"] == "pyproject":
            for values in manifest["packages_by_source"].values():
                packages.update(str(package) for package in values)
    if packages:
        return [python_path, "-m", "pip", "install", *sorted(packages)]
    return None


def dotnet_browser_install_commands(root: Path) -> list[list[str]]:
    commands: list[list[str]] = []
    shell_name = "pwsh" if shutil.which("pwsh") else "powershell"
    for project in support.dotnet_playwright_projects(root):
        project_path = Path(project["path"])
        add_planned_command(commands, ["dotnet", "build", project_path.as_posix()])
        target_frameworks = project.get("target_frameworks") or []
        if not target_frameworks:
            continue
        script = project_path.parent / "bin" / "Debug" / str(target_frameworks[0]) / "playwright.ps1"
        add_planned_command(commands, [shell_name, script.as_posix(), "install"])
    return commands


def ensure_gitignore_entries(root: Path) -> dict[str, Any]:
    report = support.gitignore_report(root)
    added = list(report["missing"])
    if not added:
        return {"requested": True, "changed": False, "path": report["path"], "added": [], "ok": True}
    gitignore = root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8", errors="replace") if gitignore.exists() else ""
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    lines = [prefix + "# Playwright validation artifacts.", *added]
    gitignore.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return {"requested": True, "changed": True, "path": str(gitignore), "added": added, "ok": True}


def language_support_report(
    *,
    groups: dict[str, list[str]],
    root: Path,
    node_path: str | None,
    npm_path: str | None,
    python_path: str,
    dotnet_path: str | None,
) -> dict[str, Any]:
    return {
        "nodejs": {
            "supported": True,
            "declared": bool(groups["nodejs"]),
            "signals": groups["nodejs"],
            "runtime": {"node": node_path or "", "npm": npm_path or ""},
        },
        "python": {
            "supported": True,
            "declared": bool(groups["python"]),
            "signals": groups["python"],
            "runtime": {"python": python_path},
            "manifests": support.python_playwright_manifests(root),
        },
        "dotnet": {
            "supported": True,
            "declared": bool(groups["dotnet"]),
            "signals": groups["dotnet"],
            "runtime": {"dotnet": dotnet_path or "", "powershell": shutil.which("pwsh") or shutil.which("powershell") or ""},
            "projects": support.dotnet_playwright_projects(root),
        },
    }


def build_report(
    project_root: Path,
    *,
    install: bool = False,
    install_browsers: bool = False,
    auto_configure: bool = False,
    auto_install: bool = False,
    probe_runtime: bool = False,
    preflight_install: bool = False,
    server_command: str = "",
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    package_path = root / "package.json"
    package_json: dict[str, Any] | None = None
    package_error: str | None = None
    checks: list[dict[str, Any]] = []
    skipped: list[str] = []
    blocked: list[str] = []
    commands: list[dict[str, Any]] = []
    planned_commands: list[list[str]] = []
    server_probe: dict[str, Any] = {"requested": bool(server_command), "ok": None}
    configuration: dict[str, Any] = {"requested": False, "changed": False, "ok": None}

    if auto_install:
        auto_configure = True

    if package_path.exists():
        package_json, package_error = support.read_package_json(package_path)
        if package_error or package_json is None:
            blocked.append(package_error or "package.json could not be read")
    else:
        package_error = f"package.json not found: {package_path}"

    signals = support.playwright_signals(package_json, root)
    groups = signal_groups(signals)
    node_signals = groups["nodejs"]
    python_signals = groups["python"]
    dotnet_signals = groups["dotnet"]

    npm_path = shutil.which("npm")
    node_path = shutil.which("node")
    python_path = python_executable()
    dotnet_path = shutil.which("dotnet")
    checks.extend(
        [
            {"name": "package.json", "ok": package_json is not None, "path": str(package_path)},
            {"name": "playwright declared", "ok": bool(signals), "signals": signals},
            {"name": "Node.js Playwright declared", "ok": bool(node_signals), "signals": node_signals},
            {"name": "Python Playwright declared", "ok": bool(python_signals), "signals": python_signals},
            {"name": ".NET Playwright declared", "ok": bool(dotnet_signals), "signals": dotnet_signals},
            {"name": "node on PATH", "ok": node_path is not None, "path": node_path or ""},
            {"name": "npm on PATH", "ok": npm_path is not None, "path": npm_path or ""},
            {"name": "python available", "ok": bool(python_path), "path": python_path},
            {"name": "dotnet on PATH", "ok": dotnet_path is not None, "path": dotnet_path or ""},
        ]
    )

    if not signals:
        skipped.append("playwright not declared in package.json, Python manifests, .NET project files, scripts, or config files")
    if not package_path.exists() and not dotnet_signals and not python_signals:
        blocked.append(package_error or f"package.json not found: {package_path}")
    if (install or install_browsers) and npm_path is None and not preflight_install:
        blocked.append("npm was not found on PATH; install commands cannot run")
    if (install or install_browsers) and not node_signals:
        if signals:
            blocked.append("npm install flags apply only to Node.js Playwright projects; use --auto-install for Python or .NET setup planning")
        else:
            blocked.append("Playwright is not declared; install commands are not safe to infer")
    if auto_install and not signals:
        blocked.append("Playwright is not declared; install commands are not safe to infer")
    if auto_install and not preflight_install:
        if node_signals and npm_path is None:
            blocked.append("npm was not found on PATH; Node.js Playwright setup cannot run")
        if python_signals and not python_path:
            blocked.append("python was not found; Python Playwright setup cannot run")
        if dotnet_signals and dotnet_path is None:
            blocked.append("dotnet was not found on PATH; .NET Playwright setup cannot run")
        if dotnet_signals and not (shutil.which("pwsh") or shutil.which("powershell")):
            blocked.append("PowerShell was not found on PATH; .NET Playwright browser install script cannot run")

    install_command = node_install_command(root)
    browser_install_command = ["npx", "playwright", "install"]
    if install:
        add_planned_command(planned_commands, install_command)
    if install_browsers:
        add_planned_command(planned_commands, browser_install_command)
    if auto_install and node_signals:
        add_planned_command(planned_commands, install_command)
        add_planned_command(planned_commands, browser_install_command)
    if auto_install and python_signals:
        python_install = python_package_install_command(root, python_path)
        if python_install:
            add_planned_command(planned_commands, python_install)
        add_planned_command(planned_commands, [python_path, "-m", "playwright", "install"])
    if auto_install and dotnet_signals:
        for command in dotnet_browser_install_commands(root):
            add_planned_command(planned_commands, command)
        if any(project.get("target_frameworks") == [] for project in support.dotnet_playwright_projects(root)) and not preflight_install:
            blocked.append(".NET Playwright project target framework could not be inferred; provide project-documented browser install command")
    if preflight_install and planned_commands:
        skipped.append("install preflight requested; planned commands were not executed")

    if auto_configure:
        if preflight_install:
            gitignore = support.gitignore_report(root)
            configuration = {
                "requested": True,
                "changed": False,
                "ok": True,
                "path": gitignore["path"],
                "preflight": True,
                "missing_gitignore_entries": gitignore["missing"],
            }
            skipped.append("auto-configure preflight requested; .gitignore was not updated")
        elif not blocked:
            configuration = ensure_gitignore_entries(root)

    if not blocked and probe_runtime and dotnet_signals and not node_signals:
        skipped.append("runtime probe skipped for .NET Playwright; use project-documented dotnet test/build commands")
    elif not blocked and probe_runtime and npm_path is not None and signals:
        commands.append(run(["npx", "--no-install", "playwright", "--version"], root, min(timeout_seconds, 60)))
        commands.append(run(["npx", "--no-install", "playwright", "install", "--dry-run"], root, min(timeout_seconds, 60)))
    elif probe_runtime and not signals:
        skipped.append("runtime probe skipped because Playwright is not declared")
    elif probe_runtime and npm_path is None:
        skipped.append("runtime probe skipped because npm was not found on PATH")

    if server_command:
        command = shlex.split(server_command)
        if not command:
            blocked.append("--server-command was empty after parsing")
        elif shutil.which(command[0]) is None:
            server_probe = {"requested": True, "ok": False, "error": f"server executable not found: {command[0]}", "command": command}
            blocked.append(server_probe["error"])
        else:
            started = time.perf_counter()
            process = subprocess.Popen(resolve_command(command), cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                time.sleep(2)
                alive = process.poll() is None
                output = ""
                if process.stdout is not None and not alive:
                    output = process.stdout.read()[-2000:]
                server_probe = {
                    "requested": True,
                    "ok": alive,
                    "command": command,
                    "duration_seconds": round(time.perf_counter() - started, 3),
                    "output_tail": output,
                }
                if not alive:
                    blocked.append("server command exited before the readiness window completed")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()

    if planned_commands:
        if preflight_install:
            skipped.append("setup commands not run because --preflight-install was set")
        elif not blocked:
            for command in planned_commands:
                commands.append(run(command, root, timeout_seconds))

    command_failures = [item for item in commands if item.get("returncode") != 0]
    ok = not blocked and not command_failures and bool(signals)
    status = "passed" if ok else "failed" if command_failures else "skipped" if skipped and not blocked else "blocked"
    return {
        "schema_version": 1,
        "tool": "playwright-integration",
        "ok": ok,
        "status": status,
        "project_root": str(root),
        "package_json": str(package_path),
        "signals": signals,
        "dotnet_projects": support.dotnet_playwright_projects(root),
        "python_manifests": support.python_playwright_manifests(root),
        "language_support": language_support_report(
            groups=groups,
            root=root,
            node_path=node_path,
            npm_path=npm_path,
            python_path=python_path,
            dotnet_path=dotnet_path,
        ),
        "checks": checks,
        "commands": commands,
        "planned_commands": planned_commands,
        "configuration": configuration,
        "server_probe": server_probe,
        "skipped": skipped,
        "blocked": blocked,
        "install_requested": install,
        "browser_install_requested": install_browsers,
        "auto_configure_requested": auto_configure,
        "auto_install_requested": auto_install,
        "runtime_probe_requested": probe_runtime,
        "preflight_install": preflight_install,
        "no_install_default": not install and not install_browsers and not auto_install,
        "gitignore": support.gitignore_report(root),
        "framework": support.detect_framework(root, package_json),
        "language": support.detect_language(root, package_json),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--install", action="store_true", help="install/write: run npm install/ci when explicitly approved")
    parser.add_argument("--install-browsers", action="store_true", help="install/write: run npx playwright install when explicitly approved")
    parser.add_argument("--auto-configure", action="store_true", help="write: add minimal Playwright validation ignore/configuration entries")
    parser.add_argument("--auto-install", action="store_true", help="install/write: run detected Node.js, Python, or .NET Playwright setup commands when explicitly approved")
    parser.add_argument("--probe-runtime", action="store_true", help="no-install/process: run local Playwright metadata probes")
    parser.add_argument("--preflight-install", action="store_true", help="read-only preview: show planned install/configure commands without running them")
    parser.add_argument("--server-command", default="", help="process: explicit short-lived dev server command to start and stop for readiness probing")
    parser.add_argument("--output-json", help="write JSON report to this path; omit for stdout-only read-only reporting")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(
        Path(args.project_root),
        install=args.install,
        install_browsers=args.install_browsers,
        auto_configure=args.auto_configure,
        auto_install=args.auto_install,
        probe_runtime=args.probe_runtime,
        preflight_install=args.preflight_install,
        server_command=args.server_command,
        timeout_seconds=args.timeout_seconds,
    )
    if args.output_json:
        write_json(Path(args.output_json), report)
    print(f"Playwright readiness: {report['status']}")
    for item in report["checks"]:
        print(f"- {item['name']}: {'ok' if item['ok'] else 'not ok'}")
    for item in report["skipped"]:
        print(f"- skipped: {item}")
    for item in report["blocked"]:
        print(f"- blocked: {item}")
    for item in report["commands"]:
        print(f"- {' '.join(item['command'])}: exit {item['returncode']}")
    for item in report["planned_commands"]:
        print(f"- planned: {' '.join(item)}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
