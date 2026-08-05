#!/usr/bin/env python3
"""Dispatcher for repo-navigation briefing and map commands."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

COMMANDS = {
    "brief": Path("briefing/brief_repo.py"),
    "lite": Path("briefing/brief_repo.py"),
    "changed": Path("briefing/brief_repo.py"),
    "install": Path("navigation/install_navigation_workflow.py"),
    "update": Path("navigation/update_navigation.py"),
    "check": Path("navigation/update_navigation.py"),
    "project-context": Path("navigation/project_context.py"),
    "focus": Path("navigation/source_focus.py"),
    "deps": Path("navigation/source_focus.py"),
    "rdeps": Path("navigation/source_focus.py"),
    "impact": Path("navigation/source_focus.py"),
}


def has_option(args: list[str], name: str) -> bool:
    return any(item == name or item.startswith(f"{name}=") for item in args)


def option_value(args: list[str], name: str) -> str:
    for index, item in enumerate(args):
        if item.startswith(f"{name}="):
            return item.split("=", 1)[1]
        if item == name and index + 1 < len(args):
            return args[index + 1]
    return ""


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print("usage: repo_navigation.py <brief|lite|changed|focus|deps|rdeps|impact|install|update|check|project-context> <args>")
        print("stdout-only: brief, lite, changed, focus, deps, rdeps, impact, check, and project-context --check when no output/write flags are used")
        print("write: brief --output, install/update/project-context --write, and project-context --overwrite")
        return 0
    command = argv[0].lower()
    target = COMMANDS.get(command)
    if target is None:
        print(f"Unknown repo-navigation command: {argv[0]}", file=sys.stderr)
        return 2
    args = argv[1:]
    if command == "lite":
        if not has_option(args, "--budget"):
            args = ["--budget", "short", *args]
    if command == "changed" and not has_option(args, "--mode"):
        args = ["--mode", "changed", *args]
    if command == "check" and "--check" not in args:
        args = ["--check", *args]
    if command in {"deps", "rdeps", "impact"}:
        explicit_mode = option_value(args, "--mode")
        if explicit_mode and explicit_mode != command:
            print(f"{command} cannot be combined with --mode {explicit_mode}", file=sys.stderr)
            return 2
        if not explicit_mode:
            args = ["--mode", command, *args]
    script = Path(__file__).resolve().parent / target
    return subprocess.call([sys.executable, "-B", str(script), *args])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
