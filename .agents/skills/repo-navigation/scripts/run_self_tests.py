#!/usr/bin/env python3
"""Run repo-navigation self-tests."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

TESTS = [Path("briefing/run_self_tests.py"), Path("navigation/run_self_tests.py")]


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="write/temp: run repo-navigation self-tests using temporary fixture projects")


def assert_help_only(script: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-B", str(script), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise AssertionError(f"expected help to exit 0 for {script}: {completed.stderr}")
    if "write/temp" not in completed.stdout:
        raise AssertionError(f"expected temp-write help label for {script}")
    if "self-tests passed" in completed.stdout:
        raise AssertionError(f"help should not run self-tests for {script}")


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    root = Path(__file__).resolve().parent
    assert_help_only(Path(__file__).resolve())
    for test in TESTS:
        assert_help_only(root / test)
        completed = subprocess.run([sys.executable, "-B", str(root / test)], check=False)
        if completed.returncode:
            return completed.returncode
    print("repo-navigation self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
