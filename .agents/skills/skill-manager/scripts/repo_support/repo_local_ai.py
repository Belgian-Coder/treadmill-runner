#!/usr/bin/env python3
"""Local AI triage hooks for repository manager commands."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

sys.dont_write_bytecode = True

from repo_support import repo_common as repo
from repo_support import repo_feedback

LAST_VALIDATION_PATH = Path(".agents/local-ai/cache/last-validation.txt")
TRIAGE_COMMAND = (
    "python -B .agents/manage.py local-ai task --task validation-triage "
    "--input .agents/local-ai/cache/last-validation.txt"
)
class Tee:
    def __init__(self, primary: object, buffer: io.StringIO) -> None:
        self.primary = primary
        self.buffer = buffer

    def write(self, value: str) -> int:
        self.buffer.write(value)
        return self.primary.write(value)

    def flush(self) -> None:
        self.buffer.flush()
        self.primary.flush()


def local_ai_disabled() -> bool:
    return os.environ.get("SKILLS_LOCAL_AI", "").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
    }


def local_ai_policy_allows(root: Path, use_case: str = "validation-triage", owner: str = "skill-manager") -> tuple[bool, str]:
    try:
        script = repo.skill_script(root, "local-ai-helper", "setup_local_ai.py")
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(script),
                "--root",
                str(root),
                "policy",
                "--check-use-case",
                use_case,
                "--owner",
                owner,
                "--json",
            ],
            cwd=root,
            check=False,
            env=repo.child_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except (OSError, SystemExit) as exc:
        return False, f"policy check could not start: {exc}"
    if completed.returncode != 0:
        try:
            payload = json.loads(completed.stdout)
            decision = payload.get("integration_policy", {}).get("decision", {})
            return False, str(decision.get("reason") or "policy denied local AI")
        except json.JSONDecodeError:
            return False, completed.stdout.strip() or "policy denied local AI"
    return True, "allowed"


def last_validation_path(root: Path) -> Path:
    return root / LAST_VALIDATION_PATH


def clear_last_validation(root: Path) -> bool:
    target = last_validation_path(root)
    try:
        if target.exists():
            target.unlink()
            return True
    except OSError:
        return False
    return False


def write_last_validation(root: Path, command_label: str, output: str) -> Path:
    target = last_validation_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"Command: {command_label}\n"
        "Exit: failed\n"
        "\n"
        "Output:\n"
        f"{output.rstrip()}\n"
    )
    target.write_text(body, encoding="utf-8", newline="\n")
    return target


def local_ai_ready(root: Path) -> bool:
    try:
        script = repo.skill_script(root, "local-ai-helper", "setup_local_ai.py")
        completed = subprocess.run(
            [sys.executable, "-B", str(script), "--root", str(root), "status", "--json"],
            cwd=root,
            check=False,
            env=repo.child_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except (OSError, SystemExit):
        return False
    if completed.returncode != 0:
        return False
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return False
    return bool(
        payload.get("enabled")
        and payload.get("manifest_found")
        and payload.get("model_found")
        and not payload.get("issues")
    )


def run_validation_triage(root: Path, input_path: Path) -> tuple[int, str]:
    try:
        script = repo.skill_script(root, "local-ai-helper", "setup_local_ai.py")
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(script),
                "--root",
                str(root),
                "task",
                "--task",
                "validation-triage",
                "--input",
                repo.relative(root, input_path),
            ],
            cwd=root,
            check=False,
            env=repo.child_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except (OSError, SystemExit) as exc:
        return 1, f"Local AI triage could not start: {exc}"
    return completed.returncode, completed.stdout


def triage_failed_check(
    root: Path,
    command_label: str,
    output: str,
    *,
    ready_func: Callable[[Path], bool] = local_ai_ready,
    run_func: Callable[[Path, Path], tuple[int, str]] = run_validation_triage,
    policy_func: Callable[[Path], tuple[bool, str]] = local_ai_policy_allows,
) -> dict[str, object]:
    input_path = write_last_validation(root, command_label, output)
    suggested_command = TRIAGE_COMMAND
    if local_ai_disabled():
        return {
            "attempted": False,
            "reason": "disabled",
            "input_path": repo.relative(root, input_path),
            "suggested_command": suggested_command,
        }
    allowed, policy_reason = policy_func(root)
    if not allowed:
        return {
            "attempted": False,
            "reason": "policy-disabled",
            "policy_reason": policy_reason,
            "input_path": repo.relative(root, input_path),
            "suggested_command": suggested_command,
        }
    if not ready_func(root):
        return {
            "attempted": False,
            "reason": "not-ready",
            "input_path": repo.relative(root, input_path),
            "suggested_command": suggested_command,
        }
    status, triage_output = run_func(root, input_path)
    return {
        "attempted": True,
        "status": status,
        "input_path": repo.relative(root, input_path),
        "output": triage_output,
        "suggested_command": suggested_command,
    }


def print_triage_result(result: dict[str, object]) -> None:
    print()
    print("## Local AI Validation Triage")
    if result.get("attempted"):
        output = str(result.get("output", "")).strip()
        if output:
            print(output)
        if result.get("status") not in (0, None):
            print(f"Local AI triage exited with {result['status']}.")
        return
    reason = result.get("reason")
    if reason == "disabled":
        print("Local AI triage skipped because SKILLS_LOCAL_AI disables it for this run.")
    elif reason == "policy-disabled":
        print("Local AI triage skipped by .agents/local-ai/policy.json.")
        print(f"Reason: {result.get('policy_reason')}")
        print(f"Fallback: use the deterministic failed command output in {result.get('input_path')}.")
    else:
        print("Local AI triage skipped because the helper is not ready.")
        print(f"Run after setup: {result.get('suggested_command')}")
    print(f"Evidence: {result.get('input_path')}")


def run_with_failure_triage(
    root: Path,
    command_label: str,
    runner: Callable[[], int],
    *,
    json_stdout: bool = False,
    ready_func: Callable[[Path], bool] = local_ai_ready,
    run_func: Callable[[Path, Path], tuple[int, str]] = run_validation_triage,
    policy_func: Callable[[Path], tuple[bool, str]] = local_ai_policy_allows,
) -> int:
    captured = io.StringIO()
    stdout = Tee(sys.stdout, captured)
    stderr = Tee(sys.stderr, captured)
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            status = runner()
        except SystemExit as exc:
            if exc.code not in (None, 0):
                print(str(exc.code))
            status = int(exc.code) if isinstance(exc.code, int) else 1
    if status != 0:
        result = triage_failed_check(
            root,
            command_label,
            captured.getvalue(),
            ready_func=ready_func,
            run_func=run_func,
            policy_func=policy_func,
        )
        repo_feedback.record_failure_triage(root, command_label, captured.getvalue(), result)
        if json_stdout:
            with contextlib.redirect_stdout(sys.stderr):
                print_triage_result(result)
        else:
            print_triage_result(result)
    else:
        clear_last_validation(root)
    return status
