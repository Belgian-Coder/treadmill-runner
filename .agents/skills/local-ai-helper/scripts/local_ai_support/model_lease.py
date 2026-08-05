#!/usr/bin/env python3
"""Exclusive, cache-only lease for every repo-local model runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any, Callable


LEASE_RELATIVE_PATH = Path(".agents/local-ai/cache/model-lease.lock")
STATE_FILE = "lease.json"
REQUEST_FILE = "preempt-request.json"
PRIORITY_ORDER = {
    "benchmark": 0,
    "validation": 1,
    "interactive": 2,
}


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _stop_recorded_process(state: dict[str, Any]) -> bool:
    pid = state.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return completed.returncode == 0
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            if temporary.exists():
                raise


def _safe_clear_lock_directory(path: Path) -> bool:
    if not path.exists():
        return True
    known = {STATE_FILE, REQUEST_FILE}
    try:
        children = list(path.iterdir())
    except OSError:
        return False
    if any(child.name not in known or not child.is_file() for child in children):
        return False
    try:
        for child in children:
            child.unlink(missing_ok=True)
        path.rmdir()
    except OSError:
        return False
    return True


class LocalModelLease:
    def __init__(
        self,
        root: Path,
        *,
        profile: str,
        role: str,
        priority: str,
        command_kind: str,
        timeout_ms: int = 0,
        stale_after_seconds: int = 120,
        pid: int | None = None,
        clock: Callable[[], float] = time.time,
        process_exists: Callable[[int], bool] = process_exists,
        sleep: Callable[[float], None] = time.sleep,
        stop_recorded: Callable[[dict[str, Any]], bool] = _stop_recorded_process,
    ) -> None:
        if priority not in PRIORITY_ORDER:
            raise ValueError(
                "priority must be interactive, validation, or benchmark"
            )
        if not profile or not role or not command_kind:
            raise ValueError("profile, role, and command_kind must be non-empty")
        self.root = Path(root)
        self.lock_dir = self.root / LEASE_RELATIVE_PATH
        self.state_path = self.lock_dir / STATE_FILE
        self.request_path = self.lock_dir / REQUEST_FILE
        self.profile = profile
        self.role = role
        self.priority = priority
        self.command_kind = command_kind
        self.timeout_ms = max(0, int(timeout_ms))
        self.stale_after_seconds = max(1, int(stale_after_seconds))
        self.pid = int(pid if pid is not None else os.getpid())
        self.clock = clock
        self.process_exists = process_exists
        self.sleep = sleep
        self.stop_recorded = stop_recorded
        self.acquired = False
        self.reclaimed_stale = False
        self.status = "not-acquired"
        self.conflict_count = 0
        self.lease_wait_ms = 0
        self.load_ms = 0
        self.inference_ms = 0
        self.unload_ms = 0
        self.fallback_used = False
        self._acquired_at = 0

    def _state(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "pid": self.pid,
            "profile": self.profile,
            "role": self.role,
            "priority": self.priority,
            "command_kind": self.command_kind,
            "acquired_at_unix": self._acquired_at,
            "heartbeat_at_unix": int(self.clock()),
            "state": "active",
        }

    def _try_create(self) -> bool:
        self.lock_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.lock_dir.mkdir()
        except FileExistsError:
            return False
        self._acquired_at = int(self.clock())
        try:
            _write_json_atomic(self.state_path, self._state())
        except Exception:
            _safe_clear_lock_directory(self.lock_dir)
            raise
        self.acquired = True
        self.status = "acquired"
        return True

    def _stale(self, state: dict[str, Any]) -> bool:
        pid = state.get("pid")
        heartbeat = state.get("heartbeat_at_unix")
        if not isinstance(pid, int) or isinstance(pid, bool):
            return False
        if not isinstance(heartbeat, (int, float)) or isinstance(heartbeat, bool):
            return False
        return (
            not self.process_exists(pid)
            and float(self.clock()) - float(heartbeat) > self.stale_after_seconds
        )

    def _request_preemption(self, state: dict[str, Any]) -> None:
        recorded_priority = str(state.get("priority", "benchmark"))
        if PRIORITY_ORDER[self.priority] <= PRIORITY_ORDER.get(recorded_priority, 0):
            return
        request = {
            "schema_version": 1,
            "pid": self.pid,
            "priority": self.priority,
            "requested_at_unix": int(self.clock()),
        }
        try:
            _write_json_atomic(self.request_path, request)
        except OSError:
            return

    def acquire(self) -> "LocalModelLease":
        started = time.monotonic()
        deadline = started + self.timeout_ms / 1000
        server_stop_attempted = False
        while True:
            if self._try_create():
                self.lease_wait_ms = int(max(0.0, time.monotonic() - started) * 1000)
                return self
            state = _read_json(self.state_path)
            if (
                not server_stop_attempted
                and state.get("command_kind") == "server"
            ):
                server_stop_attempted = True
                stop_report = cooperative_stop_recorded_server(
                    self.root,
                    requested_profile=self.profile,
                    process_exists=self.process_exists,
                    stop_recorded=self.stop_recorded,
                )
                if stop_report.get("stopped") is True:
                    self.conflict_count += 1
                    continue
            if state and self._stale(state):
                stale_path = self.lock_dir.with_name(
                    f"{self.lock_dir.name}.stale-{self.pid}-{time.time_ns()}"
                )
                try:
                    self.lock_dir.rename(stale_path)
                except OSError:
                    self.status = "stale-reclaim-contended"
                else:
                    _safe_clear_lock_directory(stale_path)
                    self.reclaimed_stale = True
                    continue
            self.conflict_count += 1
            self._request_preemption(state)
            if time.monotonic() >= deadline:
                self.status = "local-ai-busy"
                self.fallback_used = True
                self.lease_wait_ms = int(max(0.0, time.monotonic() - started) * 1000)
                return self
            self.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def heartbeat(self) -> bool:
        if not self.acquired:
            return False
        state = _read_json(self.state_path)
        if (
            state.get("pid") != self.pid
            or state.get("profile") != self.profile
            or state.get("acquired_at_unix") != self._acquired_at
        ):
            return False
        _write_json_atomic(self.state_path, self._state())
        return True

    def transfer_to_pid(self, pid: int) -> bool:
        """Transfer an acquired lease to a harness-started persistent server."""
        if not self.acquired or pid <= 0:
            return False
        state = _read_json(self.state_path)
        if (
            state.get("pid") != self.pid
            or state.get("profile") != self.profile
            or state.get("acquired_at_unix") != self._acquired_at
        ):
            return False
        transferred = dict(state)
        transferred["pid"] = int(pid)
        transferred["heartbeat_at_unix"] = int(self.clock())
        _write_json_atomic(self.state_path, transferred)
        self.pid = int(pid)
        self.acquired = False
        self.status = "transferred-to-server"
        return True

    def relinquish(self) -> bool:
        if not self.acquired:
            return False
        state = _read_json(self.state_path)
        owned = (
            state.get("pid") == self.pid
            and state.get("profile") == self.profile
            and state.get("acquired_at_unix") == self._acquired_at
        )
        released = _safe_clear_lock_directory(self.lock_dir) if owned else False
        self.acquired = False
        if released and self.status == "acquired":
            self.status = "released"
        return released

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "acquired": self.acquired,
            "profile": self.profile,
            "role": self.role,
            "priority": self.priority,
            "command_kind": self.command_kind,
            "lease_wait_ms": self.lease_wait_ms,
            "load_ms": self.load_ms,
            "inference_ms": self.inference_ms,
            "unload_ms": self.unload_ms,
            "conflict_count": self.conflict_count,
            "fallback_used": self.fallback_used,
            "reclaimed_stale": self.reclaimed_stale,
        }

    def __enter__(self) -> "LocalModelLease":
        return self.acquire()

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.relinquish()


def exclusive_lease(
    root: Path,
    *,
    profile: str,
    role: str,
    priority: str,
    command_kind: str,
    timeout_ms: int = 0,
    stale_after_seconds: int = 120,
    pid: int | None = None,
    clock: Callable[[], float] = time.time,
    process_exists: Callable[[int], bool] = process_exists,
    sleep: Callable[[float], None] = time.sleep,
    stop_recorded: Callable[[dict[str, Any]], bool] = _stop_recorded_process,
) -> LocalModelLease:
    return LocalModelLease(
        root,
        profile=profile,
        role=role,
        priority=priority,
        command_kind=command_kind,
        timeout_ms=timeout_ms,
        stale_after_seconds=stale_after_seconds,
        pid=pid,
        clock=clock,
        process_exists=process_exists,
        sleep=sleep,
        stop_recorded=stop_recorded,
    )


def cooperative_stop_recorded_server(
    root: Path,
    *,
    requested_profile: str,
    process_exists: Callable[[int], bool] = process_exists,
    stop_recorded: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    state_path = Path(root) / ".agents/local-ai/cache/server.json"
    state = _read_json(state_path)
    pid = state.get("pid")
    profile = str(state.get("profile", ""))
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or not profile
        or not process_exists(pid)
        or not recorded_server_lease_matches(root, pid=pid, profile=profile)
    ):
        return {
            "stopped": False,
            "recorded_pid": int(pid) if isinstance(pid, int) and not isinstance(pid, bool) else 0,
            "arbitrary_process_killed": False,
        }
    stopped = bool(stop_recorded(state))
    if stopped:
        state_path.unlink(missing_ok=True)
        release_recorded_server_lease(root, pid=pid, profile=profile)
    return {
        "stopped": stopped,
        "recorded_pid": pid,
        "arbitrary_process_killed": False,
    }


def release_recorded_server_lease(
    root: Path,
    *,
    pid: int,
    profile: str,
) -> bool:
    """Release only the lease transferred to the recorded harness server."""
    lock_dir = Path(root) / LEASE_RELATIVE_PATH
    state = _read_json(lock_dir / STATE_FILE)
    if (
        state.get("pid") != pid
        or state.get("profile") != profile
        or state.get("command_kind") != "server"
    ):
        return False
    return _safe_clear_lock_directory(lock_dir)


def recorded_server_lease_matches(root: Path, *, pid: int, profile: str) -> bool:
    state = _read_json(Path(root) / LEASE_RELATIVE_PATH / STATE_FILE)
    return (
        state.get("pid") == pid
        and state.get("profile") == profile
        and state.get("command_kind") == "server"
        and state.get("state") == "active"
    )
