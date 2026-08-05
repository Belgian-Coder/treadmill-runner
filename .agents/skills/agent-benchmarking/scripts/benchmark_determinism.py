#!/usr/bin/env python3
"""Determinism, evidence-tier, and runner-limit helpers for benchmarks."""

from __future__ import annotations

import hashlib
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

EVIDENCE_TIERS = ("primary", "derived", "diagnostic", "advisory", "unknown")
PERMANENT_FAILURE_CATEGORIES = {
    "config-error",
    "assertion-mismatch",
    "output-schema-error",
    "permanent-error",
}
CONFIG_ERROR_PATTERNS = (
    "unknown option",
    "unrecognized argument",
    "invalid choice",
    "no such file",
    "file not found",
    "not recognized",
    "module not found",
    "cannot find",
)
TRANSIENT_ERROR_PATTERNS = (
    "temporarily unavailable",
    "connection reset",
    "connection refused",
    "rate limit",
    "too many requests",
    "timed out",
    "timeout",
)
ASSERTION_MISMATCH_PATTERNS = (
    "assertionerror",
    "assert ",
    "expected",
    "actual",
    "mismatch",
    "not equal",
    "diff",
)


def normalize_artifact_dir(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts:
        return ""
    if any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def deterministic_metadata(
    *,
    run_id: str,
    task_id: str,
    artifact_dir: str = "",
    batch_run_id: str = "",
    unit_run_id: str = "",
) -> dict[str, Any]:
    safe_artifact_dir = normalize_artifact_dir(artifact_dir or run_id)
    batch = batch_run_id or run_id
    unit = unit_run_id or f"{batch}:{task_id}"
    return {
        "batch_run_id": batch,
        "unit_run_id": unit,
        "artifact_dir": safe_artifact_dir,
        "artifact_isolation": bool(safe_artifact_dir) and ".." not in Path(safe_artifact_dir).parts,
        "id_scheme": "batch_run_id + task_id",
    }


def normalize_determinism(value: Any, *, run_id: str, task_id: str, artifact_dir: str = "") -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return deterministic_metadata(
        run_id=run_id,
        task_id=task_id,
        artifact_dir=str(source.get("artifact_dir") or artifact_dir or run_id),
        batch_run_id=str(source.get("batch_run_id") or ""),
        unit_run_id=str(source.get("unit_run_id") or ""),
    )


def normalize_evidence_tier(value: Any) -> str:
    tier = str(value or "").strip().lower()
    aliases = {
        "source": "primary",
        "ground-truth": "primary",
        "ground_truth": "primary",
        "computed": "derived",
        "log": "diagnostic",
        "trace": "diagnostic",
        "ai": "advisory",
        "llm": "advisory",
    }
    tier = aliases.get(tier, tier)
    return tier if tier in EVIDENCE_TIERS else "unknown"


def normalize_evidence_tiers(value: Any) -> dict[str, Any]:
    rows = value if isinstance(value, list) else []
    normalized: list[dict[str, Any]] = []
    counts = {tier: 0 for tier in EVIDENCE_TIERS}
    for item in rows:
        if isinstance(item, dict):
            tier = normalize_evidence_tier(item.get("tier") or item.get("source_type"))
            path = str(item.get("path") or item.get("source") or item.get("evidence_path") or "")
            claim = str(item.get("claim") or item.get("summary") or "")[:240]
        else:
            tier = "unknown"
            path = str(item)
            claim = ""
        counts[tier] += 1
        normalized.append({"tier": tier, "path": path, "claim": claim})
    return {
        "items": normalized,
        "summary": counts,
        "primary_available": counts["primary"] > 0,
        "advisory_only": bool(normalized) and counts["primary"] == 0 and counts["derived"] == 0,
    }


def normalize_failure_text(*parts: object) -> str:
    text = " ".join(str(part or "") for part in parts)
    text = re.sub(r"\b[0-9a-f]{7,64}\b", "<hash>", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}T[\d:.-]+Z\b", "<timestamp>", text)
    text = re.sub(r"\b\d+\b", "<n>", text)
    text = re.sub(r"\s+", " ", text.lower()).strip()
    return text[:600]


def failure_fingerprint(*parts: object) -> str:
    normalized = normalize_failure_text(*parts)
    if not normalized:
        normalized = "unknown failure"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def classify_process_failure(
    *,
    returncode: int | None = None,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
) -> str:
    if timed_out:
        return "timeout"
    text = normalize_failure_text(stdout, stderr)
    if any(pattern in text for pattern in CONFIG_ERROR_PATTERNS):
        return "config-error"
    if any(pattern in text for pattern in TRANSIENT_ERROR_PATTERNS):
        return "transient-error"
    if any(pattern in text for pattern in ASSERTION_MISMATCH_PATTERNS):
        return "assertion-mismatch"
    if returncode is not None and returncode != 0:
        return "tool-failure"
    return "none"


def classify_mismatch(
    *,
    quality: dict[str, Any],
    grounding: dict[str, Any],
    failures: list[Any],
    checks: list[Any],
    raw: dict[str, Any] | None = None,
) -> str:
    raw = raw or {}
    explicit = str(raw.get("mismatch_kind", "")).strip()
    if explicit:
        return explicit
    if int(grounding.get("hallucination_count", 0) or 0) > 0:
        return "grounding-mismatch"
    if any(isinstance(check, dict) and check.get("ok") is False for check in checks):
        return "validation-mismatch"
    if failures:
        return "execution-mismatch"
    if not bool(quality.get("passed", False)):
        return "quality-mismatch"
    return "none"


def runner_process_kwargs() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def cleanup_process_group(process: subprocess.Popen[str]) -> dict[str, Any]:
    cleanup = {"attempted": True, "method": "kill", "ok": False}
    try:
        if sys.platform == "win32":
            process.kill()
            cleanup["method"] = "windows-process-kill"
        else:
            os.killpg(process.pid, signal.SIGKILL)
            cleanup["method"] = "posix-process-group-kill"
        cleanup["ok"] = True
    except Exception as exc:  # pragma: no cover - defensive cleanup record
        cleanup["error"] = str(exc)
        try:
            process.kill()
            cleanup["method"] = "process-kill-fallback"
            cleanup["ok"] = True
        except Exception as fallback_exc:  # pragma: no cover
            cleanup["fallback_error"] = str(fallback_exc)
    return cleanup


def run_command_with_limits(
    command: list[str],
    *,
    cwd: Path | str | None = None,
    timeout_seconds: float = 60.0,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> dict[str, Any]:
    if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
        fingerprint = failure_fingerprint("command must be a non-empty list of strings", command)
        return {
            "ok": False,
            "status": "failed",
            "command": command,
            "returncode": None,
            "timed_out": False,
            "elapsed_seconds": 0.0,
            "stdout_tail": "",
            "stderr_tail": "command must be a non-empty list of strings",
            "failure_category": "config-error",
            "failure_fingerprint": fingerprint,
            "cleanup": {"attempted": False, "method": "", "ok": True},
        }
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=True,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **runner_process_kwargs(),
    )
    cleanup = {"attempted": False, "method": "", "ok": True}
    timed_out = False
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        cleanup = cleanup_process_group(process)
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
    elapsed = round(time.monotonic() - started, 3)
    stdout = stdout or ""
    stderr = stderr or ""
    category = classify_process_failure(
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
    )
    fingerprint = failure_fingerprint(category, process.returncode, stdout[-800:], stderr[-800:])
    ok = process.returncode == 0 and not timed_out
    return {
        "ok": ok,
        "status": "passed" if ok else "failed",
        "command": command,
        "returncode": process.returncode,
        "timed_out": timed_out,
        "elapsed_seconds": elapsed,
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
        "failure_category": "none" if ok else category,
        "failure_fingerprint": "" if ok else fingerprint,
        "cleanup": cleanup,
    }


class ConsecutiveFailureTracker:
    def __init__(self, *, threshold: int = 3, permanent_categories: set[str] | None = None) -> None:
        self.threshold = max(1, threshold)
        self.permanent_categories = permanent_categories or PERMANENT_FAILURE_CATEGORIES
        self._last_key: tuple[str, str] | None = None
        self._count = 0

    def record(self, result: dict[str, Any]) -> dict[str, Any]:
        if result.get("ok") is True:
            self._last_key = None
            self._count = 0
            return {"abort": False, "consecutive_count": 0, "failure_category": "none", "failure_fingerprint": ""}
        category = str(result.get("failure_category") or "other")
        fingerprint = str(result.get("failure_fingerprint") or failure_fingerprint(result))
        key = (category, fingerprint)
        if key == self._last_key:
            self._count += 1
        else:
            self._last_key = key
            self._count = 1
        abort = self._count >= self.threshold and category in self.permanent_categories
        return {
            "abort": abort,
            "consecutive_count": self._count,
            "threshold": self.threshold,
            "failure_category": category,
            "failure_fingerprint": fingerprint,
            "reason": "same permanent failure fingerprint repeated" if abort else "",
        }
