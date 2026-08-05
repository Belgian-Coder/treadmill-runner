"""Command capture helpers for daily repository commands."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from repo_support import repo_common as repo
from repo_support import repo_policy


RAW_OUTPUT_DIR = Path(".agents/local-ai/cache/command-output")
ERROR_LINE_PATTERN = re.compile(
    r"(error|failed|failure|exception|traceback|fatal|timeout|timed out|assert|not found|cannot|denied|"
    r"\bCS\d{4}\b|\bMSB\d{4}\b)",
    re.IGNORECASE,
)


def output_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def output_summary(text: str) -> dict[str, Any]:
    encoded = text.encode("utf-8", errors="replace")
    lines = text.splitlines()
    nonempty = [line.strip() for line in lines if line.strip()]
    notable = notable_lines(nonempty)
    return {
        "bytes": len(encoded),
        "lines": len(lines),
        "digest": output_digest(text),
        "notable_line_count": len(notable),
        "truncated": False,
    }


def notable_lines(lines: list[str], *, limit: int = 8) -> list[str]:
    matches: list[str] = []
    seen: set[str] = set()
    line_chars = repo_policy.int_value(
        repo_policy.project_root(), "limits.output.capture_line_chars"
    )
    for line in lines:
        compact = re.sub(r"\s+", " ", line).strip()
        if not compact or compact in seen:
            continue
        if ERROR_LINE_PATTERN.search(compact):
            matches.append(compact[:line_chars])
            seen.add(compact)
        if len(matches) >= limit:
            break
    if matches:
        return matches
    for line in lines[-min(limit, len(lines)):]:
        compact = re.sub(r"\s+", " ", line).strip()
        if compact and compact not in seen:
            matches.append(compact[:line_chars])
            seen.add(compact)
    return matches


def distilled_output(text: str, *, max_chars: int | None = None) -> str:
    root = repo_policy.project_root()
    max_chars = max_chars or repo_policy.int_value(root, "limits.output.distilled_chars")
    line_chars = repo_policy.int_value(root, "limits.output.capture_line_chars")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    notable = notable_lines(lines)
    tail = [re.sub(r"\s+", " ", line).strip()[:line_chars] for line in lines[-3:]]
    sections: list[str] = []
    if notable:
        sections.append("Notable lines:")
        sections.extend(f"- {line}" for line in notable)
    if tail and tail != notable[-len(tail):]:
        sections.append("Tail:")
        sections.extend(f"- {line}" for line in tail if line)
    compact = "\n".join(sections).strip()
    if not compact:
        compact = text.strip()[:max_chars]
    return compact[:max_chars]


def write_raw_output(root: Path, command_label: str, output: str) -> str:
    digest = output_digest(output)
    label_chars = repo_policy.int_value(root, "limits.output.command_label_chars")
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", command_label.strip())[:label_chars].strip("-") or "command"
    target_dir = root / RAW_OUTPUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"{timestamp}-{digest}-{safe_label}.txt"
    target.write_text(output, encoding="utf-8", newline="\n")
    return repo.relative(root, target)


def capture_output_fields(
    root: Path,
    command_label: str,
    output: str,
    *,
    ok: bool,
    tail_limit: int,
) -> dict[str, Any]:
    summary = output_summary(output)
    summary["truncated"] = len(output.encode("utf-8", errors="replace")) > tail_limit
    fields: dict[str, Any] = {
        "output_tail": output[-tail_limit:],
        "output_summary": summary,
        "distilled_output": distilled_output(output),
    }
    if output and (not ok or summary["truncated"]):
        fields["raw_output_path"] = write_raw_output(root, command_label, output)
    return fields


def output_reference_text(result: dict[str, Any]) -> str:
    summary = result.get("output_summary") if isinstance(result.get("output_summary"), dict) else {}
    lines = [
        f"Command: {result.get('command', '')}",
        f"Status: {result.get('status', '')}",
    ]
    if result.get("raw_output_path"):
        lines.append(f"Raw output: {result.get('raw_output_path')}")
    if summary:
        lines.append(
            "Output: "
            f"{summary.get('bytes', 0)} bytes, "
            f"{summary.get('lines', 0)} lines, "
            f"digest {summary.get('digest', '')}"
        )
    if result.get("distilled_output"):
        lines.extend(["", str(result.get("distilled_output"))])
    else:
        lines.extend(["", str(result.get("output_tail", ""))])
    return "\n".join(lines).strip() + "\n"


def timeout_output(exc: subprocess.TimeoutExpired, timeout: int) -> str:
    output = exc.stdout or ""
    if not isinstance(output, str):
        output = output.decode("utf-8", errors="replace")
    suffix = "second" if timeout == 1 else "seconds"
    timeout_fact = f"COMMAND TIMEOUT: command timed out after {timeout} {suffix}."
    text = output.rstrip()
    return f"{text}\n{timeout_fact}\n" if text else f"{timeout_fact}\n"


def timeout_output_text(output: str, timeout: int) -> str:
    suffix = "second" if timeout == 1 else "seconds"
    timeout_fact = f"COMMAND TIMEOUT: command timed out after {timeout} {suffix}."
    text = output.rstrip()
    return f"{text}\n{timeout_fact}\n" if text else f"{timeout_fact}\n"


def popen_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    try:
        os.killpg(process.pid, 9)
    except OSError:
        process.kill()


def run_process_output(
    command: list[str] | str,
    *,
    cwd: Path,
    timeout: int,
    shell: bool = False,
) -> tuple[int, str, bool]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=repo.child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        shell=shell,
        **popen_kwargs(),
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
        return int(process.returncode or 0), stdout or "", False
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        if not isinstance(partial, str):
            partial = partial.decode("utf-8", errors="replace")
        kill_process_tree(process)
        try:
            stdout, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, _ = process.communicate()
        combined = f"{partial}{stdout or ''}"
        return 124, timeout_output_text(combined, timeout), True


def run_capture(root: Path, command: list[str], *, timeout: int = 90) -> dict[str, Any]:
    start = time.perf_counter()

    def elapsed() -> float:
        return round(time.perf_counter() - start, 3)

    try:
        returncode, stdout, timed_out = run_process_output(
            command,
            cwd=root,
            timeout=timeout,
        )
    except OSError as exc:
        return {
            "ok": False,
            "status": 1,
            "command": " ".join(command),
            "output_tail": str(exc),
            "issue": "command could not start",
            "elapsed_seconds": elapsed(),
            "timeout_seconds": timeout,
        }
    if timed_out:
        command_text = " ".join(command)
        return {
            "ok": False,
            "status": 124,
            "command": command_text,
            **capture_output_fields(root, command_text, stdout, ok=False, tail_limit=2000),
            "issue": "command timed out",
            "elapsed_seconds": elapsed(),
            "timeout_seconds": timeout,
        }
    return {
        "ok": returncode == 0,
        "status": returncode,
        "command": " ".join(command),
        "elapsed_seconds": elapsed(),
        "timeout_seconds": timeout,
        **capture_output_fields(
            root,
            " ".join(command),
            stdout,
            ok=returncode == 0,
            tail_limit=3000,
        ),
    }


def run_capture_shell(root: Path, command: str, *, timeout: int = 600) -> dict[str, Any]:
    start = time.perf_counter()

    def elapsed() -> float:
        return round(time.perf_counter() - start, 3)

    try:
        returncode, stdout, timed_out = run_process_output(
            command,
            cwd=root,
            timeout=timeout,
            shell=True,
        )
    except OSError as exc:
        return {
            "ok": False,
            "status": 1,
            "command": command,
            "output_tail": str(exc),
            "issue": "command could not start",
            "timeout_seconds": timeout,
            "elapsed_seconds": elapsed(),
        }
    if timed_out:
        return {
            "ok": False,
            "status": 124,
            "command": command,
            **capture_output_fields(root, command, stdout, ok=False, tail_limit=5000),
            "issue": "command timed out",
            "timeout_seconds": timeout,
            "elapsed_seconds": elapsed(),
        }
    return {
        "ok": returncode == 0,
        "status": returncode,
        "command": command,
        "timeout_seconds": timeout,
        "elapsed_seconds": elapsed(),
        **capture_output_fields(root, command, stdout, ok=returncode == 0, tail_limit=5000),
    }


def run_json_local_ai(root: Path, args: list[str], *, timeout: int = 45) -> dict[str, Any]:
    script = repo.skill_script(root, "local-ai-helper", "setup_local_ai.py")
    command = [sys.executable, "-B", str(script), "--root", str(root), *args]
    result = run_capture(root, command, timeout=timeout)
    try:
        payload = json.loads(str(result.get("output_tail") or "{}"))
    except json.JSONDecodeError:
        payload = {"output_tail": result.get("output_tail", "")}
    return {"ok": bool(result.get("ok")), "status": result.get("status"), "result": payload}
