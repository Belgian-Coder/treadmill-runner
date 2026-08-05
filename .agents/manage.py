#!/usr/bin/env python3
"""Thin repository launcher for skill-owned manager commands."""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

sys.dont_write_bytecode = True

MIN_PYTHON = (3, 12)


def require_supported_python() -> None:
    if sys.version_info >= MIN_PYTHON:
        return
    current = ".".join(str(part) for part in sys.version_info[:3])
    required = ".".join(str(part) for part in MIN_PYTHON)
    raise SystemExit(
        f"Python {required}+ is required; current interpreter is Python {current}. "
        "Run this command with a Python 3.12+ launcher, such as python3 or py -3."
    )


def use_local_ai_fast_path(arguments: list[str]) -> bool:
    return bool(
        arguments
        and arguments[0] == "local-ai"
        and not any(argument == "--root" or argument.startswith("--root=") for argument in arguments[1:])
        and not any(argument in {"-h", "--help"} for argument in arguments[1:])
    )


def main() -> int:
    require_supported_python()
    root = Path(__file__).resolve().parents[1]
    original_argv = sys.argv[:]
    if use_local_ai_fast_path(original_argv[1:]):
        target = root / ".agents" / "skills" / "local-ai-helper" / "scripts" / "setup_local_ai.py"
        forwarded = ["--root", str(root), *original_argv[2:]]
    else:
        target = root / ".agents" / "skills" / "skill-manager" / "scripts" / "repo_manager.py"
        forwarded = original_argv[1:]
    if not target.exists():
        raise SystemExit(f"repository command not found: {target}")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    target_dir = str(target.parent)
    if not sys.path or sys.path[0] != target_dir:
        sys.path.insert(0, target_dir)
    sys.argv = [str(target), *forwarded]
    try:
        runpy.run_path(str(target), run_name="__main__")
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        raise
    finally:
        sys.argv = original_argv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
