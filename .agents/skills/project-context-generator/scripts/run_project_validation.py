#!/usr/bin/env python3
"""Run project validation commands and save proof artifacts."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

IGNORED_DIRS = {
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".venv",
    "__pycache__",
    "bin",
    "build",
    "dist",
    "node_modules",
    "obj",
    "venv",
}
IGNORED_PATH_SEGMENTS = {"fixtures", "samples"}
IGNORED_RELATIVE_PREFIXES = (
    (".agents", ".deps"),
    (".agents", "local-ai"),
    (".agents", "tools", "cache"),
    ("automations", "*", "runs"),
)


def now_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def relative_parts(root: Path, path: Path) -> tuple[str, ...]:
    try:
        return path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        return path.parts


def matches_prefix(parts: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    if len(parts) < len(prefix):
        return False
    return all(pattern == "*" or fnmatch.fnmatch(part, pattern) for part, pattern in zip(parts, prefix))


def is_ignored_project_path(root: Path, path: Path) -> bool:
    lowered = tuple(part.lower() for part in relative_parts(root, path))
    if any(part in IGNORED_DIRS or part.startswith(".cache") for part in lowered):
        return True
    if any(part in IGNORED_PATH_SEGMENTS for part in lowered):
        return True
    return any(matches_prefix(lowered, prefix) for prefix in IGNORED_RELATIVE_PREFIXES)


def iter_project_files(root: Path, max_files: int = 5000) -> list[Path]:
    files: list[Path] = []
    for current_root, dirnames, filenames in os.walk(root):
        current = Path(current_root)
        dirnames[:] = [
            name
            for name in dirnames
            if not is_ignored_project_path(root, current / name)
        ]
        for filename in sorted(filenames):
            path = current / filename
            if is_ignored_project_path(root, path):
                continue
            files.append(path)
            if len(files) >= max_files:
                return files
    return files


def command_text(command: list[str]) -> str:
    return " ".join(command)


def python_command(*parts: str) -> list[str]:
    return ["python", "-B", *parts]


def executable_command(command: list[Any]) -> list[str]:
    parts = [str(part) for part in command]
    if parts and parts[0] == "python":
        parts[0] = os.environ.get("AGENTS_PYTHON") or sys.executable
    if parts and os.name == "nt" and parts[0] in {"npm", "npx"}:
        shim = shutil.which(f"{parts[0]}.cmd") or shutil.which(f"{parts[0]}.exe")
        if shim:
            parts[0] = shim
    return parts


def missing_executable_reason(command: dict[str, Any]) -> str:
    original = [str(part) for part in command["command"]]
    resolved = executable_command(original)
    if not resolved:
        return f"Required executable not found for {command['id']}: empty command"
    executable = resolved[0]
    if Path(executable).exists() or shutil.which(executable):
        return ""
    return f"Required executable not found for {command['id']}: {original[0]}"


def package_scripts(root: Path) -> dict[str, str]:
    data = read_json(root / "package.json")
    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
    return {str(key): str(value) for key, value in scripts.items()}


def package_declares(root: Path, name: str) -> bool:
    data = read_json(root / "package.json")
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps = data.get(key)
        if isinstance(deps, dict) and name in deps:
            return True
    return False


def has_files(root: Path, patterns: tuple[str, ...]) -> bool:
    files = [path.relative_to(root).as_posix() for path in iter_project_files(root)]
    for pattern in patterns:
        normalized = pattern.replace("\\", "/")
        if any(fnmatch.fnmatch(path, normalized) for path in files):
            return True
    return False


def read_project_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def pyproject_declares_pytest(path: Path) -> bool:
    if not path.exists():
        return False
    text = read_project_text(path)
    lowered = text.lower()
    if "pytest" in lowered:
        return True
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return False
    tool = data.get("tool") if isinstance(data, dict) else {}
    if isinstance(tool, dict) and "pytest" in tool:
        return True
    project = data.get("project") if isinstance(data, dict) else {}
    dependencies: list[Any] = []
    if isinstance(project, dict):
        raw_dependencies = project.get("dependencies")
        if isinstance(raw_dependencies, list):
            dependencies.extend(raw_dependencies)
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for group in optional.values():
                if isinstance(group, list):
                    dependencies.extend(group)
    return any("pytest" in str(item).lower() for item in dependencies)


def file_declares_pytest(path: Path) -> bool:
    if not path.exists():
        return False
    return "pytest" in read_project_text(path).lower()


def has_pytest_signal(root: Path) -> bool:
    if (root / "pytest.ini").exists():
        return True
    if pyproject_declares_pytest(root / "pyproject.toml"):
        return True
    for name in ("setup.cfg", "tox.ini"):
        text = read_project_text(root / name).lower()
        if "[tool:pytest]" in text or "[pytest]" in text or "pytest" in text:
            return True
    for path in iter_project_files(root):
        if path.name.lower().startswith("requirements") and path.suffix.lower() == ".txt":
            if file_declares_pytest(path):
                return True
    return False


def has_unittest_signal(root: Path) -> bool:
    if (root / "tests").is_dir():
        return True
    return has_files(root, ("test_*.py", "*_test.py", "**/test_*.py", "**/*_test.py"))


def has_playwright(root: Path) -> bool:
    scripts = package_scripts(root)
    return (
        package_declares(root, "@playwright/test")
        or package_declares(root, "playwright")
        or any("playwright" in value.lower() for value in scripts.values())
        or has_files(root, ("playwright.config.*", "**/playwright.config.*"))
    )


def add_command(commands: list[dict[str, Any]], command_id: str, label: str, command: list[str], kind: str, required: bool = True) -> None:
    if any(item["id"] == command_id for item in commands):
        return
    commands.append(
        {
            "id": command_id,
            "label": label,
            "command": command,
            "command_text": command_text(command),
            "kind": kind,
            "required": required,
        }
    )


def discover_commands(root: Path) -> list[dict[str, Any]]:
    root = root.resolve()
    commands: list[dict[str, Any]] = []
    if (root / ".agents" / "manage.py").exists():
        add_command(
            commands,
            "harness-check-additions",
            "Harness addition ownership",
            python_command(".agents/manage.py", "check-additions"),
            "validation",
        )
        add_command(
            commands,
            "harness-sync-check",
            "Harness generated sync",
            python_command(".agents/manage.py", "sync", "--check"),
            "validation",
        )
        add_command(
            commands,
            "harness-agent-compatibility",
            "Agent compatibility",
            python_command(".agents/manage.py", "validate-agent-compatibility"),
            "validation",
        )
        add_command(
            commands,
            "harness-check",
            "Harness repository check",
            python_command(".agents/manage.py", "check"),
            "test",
        )
    if has_files(root, ("*.sln", "**/*.csproj")):
        add_command(commands, "dotnet-build", ".NET build", ["dotnet", "build"], "build")
        add_command(commands, "dotnet-test", ".NET tests", ["dotnet", "test"], "test")
    scripts = package_scripts(root)
    if scripts:
        if "build" in scripts:
            add_command(commands, "npm-build", "Node build", ["npm", "run", "build"], "build")
        if "lint" in scripts:
            add_command(commands, "npm-lint", "Node lint", ["npm", "run", "lint"], "lint", required=False)
        test_script = scripts.get("test", "")
        if test_script and "no test specified" not in test_script.lower():
            add_command(commands, "npm-test", "Node tests", ["npm", "test"], "test")
    if has_pytest_signal(root):
        add_command(commands, "python-pytest", "Python tests", python_command("-m", "pytest"), "test")
    elif has_unittest_signal(root):
        unittest_command = python_command("-m", "unittest", "discover")
        if (root / "tests").is_dir():
            unittest_command.extend(["-s", "tests"])
        add_command(commands, "python-unittest", "Python unittest tests", unittest_command, "test")
    if has_playwright(root):
        add_command(commands, "playwright-test", "Playwright browser tests", ["npx", "playwright", "test", "--reporter=json"], "browser-test")
    return commands


def run_command(command: dict[str, Any], cwd: Path, output_dir: Path, timeout_seconds: int) -> dict[str, Any]:
    started = time.perf_counter()
    log_path = output_dir / f"{command['id']}.log"
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = subprocess.run(
            executable_command(command["command"]),
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
        output = completed.stdout or ""
        returncode = completed.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        returncode = 124
        timed_out = True
    log_path.write_text(output, encoding="utf-8", newline="\n")
    return {
        **command,
        "ok": returncode == 0,
        "returncode": returncode,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "log_path": str(log_path),
        "output_tail": output[-2000:],
    }


def copy_if_exists(source: Path, target: Path) -> str:
    if not source.exists():
        return ""
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return str(target)


def collect_playwright_artifacts(root: Path, output_dir: Path) -> list[str]:
    artifacts: list[str] = []
    for name in ("test-results", "playwright-report"):
        copied = copy_if_exists(root / name, output_dir / "playwright" / name)
        if copied:
            artifacts.append(copied)
    return artifacts


def capture_screenshot(root: Path, output_dir: Path, url: str, timeout_seconds: int) -> dict[str, Any] | None:
    if not url:
        return None
    screenshot = output_dir / "screenshots" / "page.png"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    command = executable_command(["npx", "playwright", "screenshot", "--full-page", url, str(screenshot)])
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
        output = completed.stdout or ""
        returncode = completed.returncode
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        returncode = 124
    except OSError as exc:
        output = str(exc)
        returncode = 127
    log_path = output_dir / "screenshots" / "screenshot.log"
    log_path.write_text(output, encoding="utf-8", newline="\n")
    return {
        "url": url,
        "command": command,
        "ok": returncode == 0 and screenshot.exists(),
        "returncode": returncode,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "screenshot_path": str(screenshot) if screenshot.exists() else "",
        "log_path": str(log_path),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Project Validation Evidence",
        "",
        f"- Status: {report['status']}",
        f"- Target: `{report['target']}`",
        f"- Evidence: `{report['evidence_dir']}`",
        "",
        "## Commands",
        "",
    ]
    if not report["commands"]:
        lines.append("- No validation commands discovered.")
    for item in report["commands"]:
        marker = "pass" if item.get("ok") else "fail"
        lines.append(f"- {marker}: `{item['command_text']}` -> `{item.get('log_path', '')}`")
    if report.get("screenshot"):
        shot = report["screenshot"]
        lines.extend(["", "## Screenshot", "", f"- URL: `{shot.get('url')}`", f"- Path: `{shot.get('screenshot_path') or 'not captured'}`"])
    if report.get("playwright_artifacts"):
        lines.extend(["", "## Playwright Artifacts", ""])
        lines.extend(f"- `{path}`" for path in report["playwright_artifacts"])
    if report.get("blocked"):
        lines.extend(["", "## Blocked", ""])
        lines.extend(f"- {item}" for item in report["blocked"])
    return "\n".join(lines) + "\n"


def build_report(root: Path, evidence_dir: Path, *, list_only: bool, screenshot_url: str, timeout_seconds: int) -> dict[str, Any]:
    root = root.expanduser().resolve()
    evidence_root = (root / evidence_dir).resolve() if not evidence_dir.is_absolute() else evidence_dir.resolve()
    run_dir = evidence_root / now_id()
    commands = discover_commands(root)
    blocked: list[str] = []
    if not commands:
        blocked.append("No validation commands were discovered.")
    results: list[dict[str, Any]] = []
    playwright_artifacts: list[str] = []
    screenshot = None
    if not list_only:
        run_dir.mkdir(parents=True, exist_ok=True)
        for command in commands:
            missing = missing_executable_reason(command)
            if missing:
                blocked.append(missing)
                results.append(
                    {
                        **command,
                        "ok": False,
                        "blocked": True,
                        "returncode": None,
                        "timed_out": False,
                        "elapsed_seconds": 0,
                        "log_path": "",
                        "output_tail": missing,
                    }
                )
                continue
            result = run_command(command, root, run_dir, timeout_seconds)
            results.append(result)
            if command["kind"] == "browser-test":
                playwright_artifacts.extend(collect_playwright_artifacts(root, run_dir))
        screenshot = capture_screenshot(root, run_dir, screenshot_url, min(timeout_seconds, 120)) if screenshot_url else None
    else:
        run_dir.mkdir(parents=True, exist_ok=True)
        results = [{**command, "ok": None, "log_path": ""} for command in commands]
    failed = [item for item in results if item.get("ok") is False and item.get("required") and not item.get("blocked")]
    ok = not blocked and not failed and not list_only
    status = "listed" if list_only else "passed" if ok else "failed" if failed else "blocked"
    report = {
        "schema_version": 1,
        "tool": "project-context-generator.validation",
        "ok": ok,
        "status": status,
        "target": str(root),
        "evidence_dir": str(run_dir),
        "commands": results,
        "playwright_artifacts": playwright_artifacts,
        "screenshot": screenshot,
        "blocked": blocked,
        "skipped": ["command execution skipped by --list"] if list_only else [],
    }
    (run_dir / "validation-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    (run_dir / "validation-report.md").write_text(render_markdown(report), encoding="utf-8", newline="\n")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="write/runtime: run or list validation commands and save proof artifacts")
    parser.add_argument("--target", default=".", help="read target project root")
    parser.add_argument("--evidence-dir", default="docs/project/validation/evidence", help="write evidence reports/logs under this path")
    parser.add_argument("--screenshot-url", default="", help="runtime/browser/write: capture a Playwright screenshot for this running URL")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--list", action="store_true", help="write a report of discovered commands without running them")
    parser.add_argument("--no-fail", action="store_true", help="always exit 0 after writing evidence")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown", dest="output_format", help="stdout report format")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(
        Path(args.target),
        Path(args.evidence_dir),
        list_only=args.list,
        screenshot_url=args.screenshot_url,
        timeout_seconds=args.timeout_seconds,
    )
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    if args.no_fail:
        return 0
    return 0 if report["ok"] or args.list else 1


if __name__ == "__main__":
    raise SystemExit(main())
