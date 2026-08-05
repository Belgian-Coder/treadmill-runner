#!/usr/bin/env python3
"""Shared helpers for workflow smoke checks."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import index_workflow_runs
import workflow_manager_common as common


SMOKE_PREFIX = "smoke-local"
RUN_INDEX_FILENAMES = ("INDEX.md", "index.json")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, dict) else {}


def workflow_manifest(root: Path, workflow_name: str) -> dict[str, Any]:
    return read_json(root / "automations" / workflow_name / "module.json")


def smoke_run_id(workflow_name: str, label: str) -> str:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M%S")
    return f"{SMOKE_PREFIX}-{label}-{workflow_name}-{stamp}-{os.getpid()}"


def is_tracked_file(root: Path, path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return False
    completed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relative],
        cwd=str(root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return completed.returncode == 0


def remove_smoke_path(path: Path, *, attempts: int = 5, delay_seconds: float = 0.05) -> None:
    for attempt in range(attempts):
        try:
            if path.is_dir() and not path.is_symlink():
                path.rmdir()
            else:
                path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt >= attempts - 1:
                raise
            time.sleep(delay_seconds)


def cleanup_smoke_run(
    root: Path,
    workflow_name: str,
    run_id: str,
    *,
    is_tracked: Any | None = None,
) -> dict[str, Any]:
    tracker = is_tracked or is_tracked_file
    runs_dir = (root / "automations" / workflow_name / "runs").resolve()
    run_dir = (runs_dir / run_id).resolve()
    if not run_dir.exists():
        return {"removed": False, "path": common.relative(root, run_dir), "reason": "not-created"}
    if not run_dir.name.startswith(SMOKE_PREFIX) or runs_dir not in run_dir.parents:
        return {"removed": False, "path": common.relative(root, run_dir), "reason": "outside-smoke-boundary"}
    try:
        for child in sorted(run_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            remove_smoke_path(child)
        remove_smoke_path(run_dir)
    except OSError as exc:
        return {
            "removed": False,
            "path": common.relative(root, run_dir),
            "reason": "cleanup-failed",
            "issues": [str(exc)],
        }
    removed_empty_runs_dir = False
    removed_index_files: list[str] = []
    refreshed_index_files: list[str] = []
    try:
        retained_entries = (
            [
                item
                for item in runs_dir.iterdir()
                if item.name not in {"README.md", "INDEX.md", "index.json"}
            ]
            if runs_dir.exists()
            else []
        )
        if runs_dir.exists():
            index_present = False
            tracked_index_present = False
            for name in ("INDEX.md", "index.json"):
                index_path = runs_dir / name
                if index_path.exists() and index_path.is_file():
                    index_present = True
                    if tracker(root, index_path):
                        tracked_index_present = True
                    elif not retained_entries:
                        remove_smoke_path(index_path)
                        removed_index_files.append(common.relative(root, index_path))
            if tracked_index_present or (index_present and retained_entries):
                report = index_workflow_runs.build_index(root, workflow_name)
                index_workflow_runs.write_outputs(root, report)
                refreshed_index_files = [
                    common.relative(root, runs_dir / "INDEX.md"),
                    common.relative(root, runs_dir / "index.json"),
                ]
        if runs_dir.exists() and not any(runs_dir.iterdir()):
            remove_smoke_path(runs_dir)
            removed_empty_runs_dir = True
    except OSError:
        removed_empty_runs_dir = False
    return {
        "removed": True,
        "path": common.relative(root, run_dir),
        "reason": "cleaned",
        "removed_empty_runs_dir": removed_empty_runs_dir,
        "removed_index_files": removed_index_files,
        "refreshed_index_files": refreshed_index_files,
    }


def safe_run_index_path(runs_dir: Path, name: str) -> tuple[Path | None, str]:
    path = runs_dir / name
    if path.is_symlink():
        return None, f"{name} must not be a symbolic link"
    try:
        path.resolve(strict=False).relative_to(runs_dir)
    except (OSError, ValueError) as exc:
        return None, f"{name} resolves outside the workflow runs directory: {exc}"
    return path, ""


def retained_run_state_digest(runs_dir: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    if not runs_dir.exists():
        return {"ok": True, "digest": digest.hexdigest()}
    try:
        for path in sorted(runs_dir.rglob("*"), key=lambda item: item.relative_to(runs_dir).as_posix()):
            relative = path.relative_to(runs_dir)
            if relative.parts[0] in {"README.md", *RUN_INDEX_FILENAMES}:
                continue
            if path.is_symlink():
                return {
                    "ok": False,
                    "issue": f"retained run path must not be a symbolic link: {relative.as_posix()}",
                }
            resolved = path.resolve(strict=False)
            resolved.relative_to(runs_dir)
            relative_bytes = relative.as_posix().encode("utf-8")
            digest.update(len(relative_bytes).to_bytes(8, "big"))
            digest.update(relative_bytes)
            if path.is_dir():
                digest.update(b"D")
            elif path.is_file():
                content = path.read_bytes()
                digest.update(b"F")
                digest.update(len(content).to_bytes(8, "big"))
                digest.update(content)
            else:
                return {
                    "ok": False,
                    "issue": f"retained run path has an unsupported type: {relative.as_posix()}",
                }
    except (OSError, ValueError) as exc:
        return {"ok": False, "issue": f"could not fingerprint retained run state: {exc}"}
    return {"ok": True, "digest": digest.hexdigest()}


def snapshot_run_index_state(root: Path, workflow_name: str) -> dict[str, Any]:
    root_path = root.resolve()
    automations_dir = (root_path / "automations").resolve()
    workflow_dir = (automations_dir / workflow_name).resolve()
    runs_dir = (workflow_dir / "runs").resolve()
    try:
        workflow_dir.relative_to(automations_dir)
        runs_dir.relative_to(workflow_dir)
    except ValueError:
        return {
            "ok": False,
            "issue": "workflow runs directory resolves outside the repository workflow boundary",
        }
    files: dict[str, bytes | None] = {}
    retained_state: dict[str, Any] = {}
    snapshot_attempts = 0
    for snapshot_attempts in range(1, 4):
        retained_before = retained_run_state_digest(runs_dir)
        if retained_before.get("ok") is not True:
            return {
                "ok": False,
                "issue": str(retained_before.get("issue") or "retained run state is unsafe"),
            }
        captured: dict[str, bytes | None] = {}
        for name in RUN_INDEX_FILENAMES:
            path, issue = safe_run_index_path(runs_dir, name)
            if path is None:
                return {"ok": False, "issue": issue}
            try:
                captured[name] = path.read_bytes() if path.is_file() else None
            except OSError as exc:
                return {"ok": False, "issue": f"could not snapshot {name}: {exc}"}
        retained_after = retained_run_state_digest(runs_dir)
        if retained_after.get("ok") is not True:
            return {
                "ok": False,
                "issue": str(retained_after.get("issue") or "retained run state is unsafe"),
            }
        if retained_before.get("digest") == retained_after.get("digest"):
            files = captured
            retained_state = retained_after
            break
    else:
        return {
            "ok": False,
            "issue": "retained run state changed repeatedly while its index was snapshotted",
        }
    return {
        "ok": True,
        "root": root_path,
        "workflow_name": workflow_name,
        "runs_dir": runs_dir,
        "runs_dir_existed": runs_dir.exists(),
        "files": files,
        "retained_state_digest": retained_state["digest"],
        "snapshot_attempts": snapshot_attempts,
    }


def rebuild_current_run_index(snapshot: dict[str, Any]) -> dict[str, Any]:
    root = snapshot.get("root")
    workflow_name = snapshot.get("workflow_name")
    runs_dir = snapshot.get("runs_dir")
    if (
        not isinstance(root, Path)
        or not isinstance(workflow_name, str)
        or not workflow_name
        or not isinstance(runs_dir, Path)
    ):
        return {"ok": False, "issue": "run-index snapshot cannot rebuild current state"}
    for name in RUN_INDEX_FILENAMES:
        path, issue = safe_run_index_path(runs_dir, name)
        if path is None:
            return {"ok": False, "issue": issue}
    try:
        if not runs_dir.exists():
            return {"ok": True, "status": "current-index-absent"}
        retained_entries = [
            path
            for path in runs_dir.iterdir()
            if path.name not in {"README.md", *RUN_INDEX_FILENAMES}
        ]
        if not retained_entries:
            tracked_index_present = any(
                path.is_file() and is_tracked_file(root, path)
                for path in (runs_dir / name for name in RUN_INDEX_FILENAMES)
            )
            if not tracked_index_present:
                for name in RUN_INDEX_FILENAMES:
                    path = runs_dir / name
                    if path.exists():
                        remove_smoke_path(path)
                if not any(runs_dir.iterdir()):
                    remove_smoke_path(runs_dir)
                return {"ok": True, "status": "current-empty-index-policy-preserved"}
        report = index_workflow_runs.build_index(root, workflow_name)
        index_workflow_runs.write_outputs(root, report)
    except (OSError, SystemExit) as exc:
        return {"ok": False, "issue": f"could not rebuild current run index: {exc}"}
    return {"ok": True, "status": "rebuilt"}


def preserve_concurrent_run_index_state(
    snapshot: dict[str, Any],
    *,
    detected_digest: str,
) -> dict[str, Any]:
    for _attempt in range(2):
        rebuilt = rebuild_current_run_index(snapshot)
        if rebuilt.get("ok") is not True:
            return {
                "ok": False,
                "status": "failed",
                "concurrent_change_detected": True,
                "issues": [str(rebuilt.get("issue") or "current run index rebuild failed")],
            }
        runs_dir = snapshot["runs_dir"]
        after = retained_run_state_digest(runs_dir)
        if after.get("ok") is not True:
            return {
                "ok": False,
                "status": "failed",
                "concurrent_change_detected": True,
                "issues": [str(after.get("issue") or "retained run state is unsafe")],
            }
        if after.get("digest") == detected_digest:
            return {
                "ok": True,
                "status": "concurrent-change-preserved",
                "concurrent_change_detected": True,
                "restored_files": [],
                "removed_files": [],
                "issues": [],
            }
        detected_digest = str(after.get("digest") or "")
    return {
        "ok": False,
        "status": "failed",
        "concurrent_change_detected": True,
        "issues": ["retained run state kept changing while its index was rebuilt"],
    }


def restore_run_index_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot.get("ok") is not True:
        return {
            "ok": False,
            "status": "failed",
            "issue": str(snapshot.get("issue") or "run-index snapshot is unsafe"),
        }
    runs_dir = snapshot.get("runs_dir")
    files = snapshot.get("files")
    expected_retained_digest = snapshot.get("retained_state_digest")
    if (
        not isinstance(runs_dir, Path)
        or not isinstance(files, dict)
        or not isinstance(expected_retained_digest, str)
    ):
        return {"ok": False, "status": "failed", "issue": "run-index snapshot is malformed"}
    retained_state = retained_run_state_digest(runs_dir)
    if retained_state.get("ok") is not True:
        return {
            "ok": False,
            "status": "failed",
            "issue": str(retained_state.get("issue") or "retained run state is unsafe"),
        }
    current_retained_digest = str(retained_state.get("digest") or "")
    if current_retained_digest != expected_retained_digest:
        return preserve_concurrent_run_index_state(
            snapshot,
            detected_digest=current_retained_digest,
        )
    restored: list[str] = []
    removed: list[str] = []
    issues: list[str] = []
    for name in RUN_INDEX_FILENAMES:
        path, path_issue = safe_run_index_path(runs_dir, name)
        if path is None:
            issues.append(path_issue)
            continue
        expected = files.get(name)
        try:
            if isinstance(expected, bytes):
                current = path.read_bytes() if path.is_file() else None
                if current != expected:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(expected)
                    restored.append(name)
            elif expected is None:
                if path.exists():
                    if not path.is_file():
                        issues.append(f"{name} is not a file")
                        continue
                    remove_smoke_path(path)
                    removed.append(name)
            else:
                issues.append(f"{name} snapshot has unsupported content")
        except OSError as exc:
            issues.append(f"{name}: {exc}")
    if not snapshot.get("runs_dir_existed") and runs_dir.exists():
        try:
            if not any(runs_dir.iterdir()):
                remove_smoke_path(runs_dir)
        except OSError as exc:
            issues.append(f"runs directory: {exc}")
    verified = True
    for name in RUN_INDEX_FILENAMES:
        path, path_issue = safe_run_index_path(runs_dir, name)
        if path is None:
            issues.append(path_issue)
            verified = False
            continue
        try:
            current = path.read_bytes() if path.is_file() else None
        except OSError as exc:
            issues.append(f"{name} verification: {exc}")
            verified = False
            continue
        verified = verified and current == files.get(name)
    retained_after_restore = retained_run_state_digest(runs_dir)
    if retained_after_restore.get("ok") is not True:
        issues.append(str(retained_after_restore.get("issue") or "retained run state is unsafe"))
    elif retained_after_restore.get("digest") != expected_retained_digest:
        return preserve_concurrent_run_index_state(
            snapshot,
            detected_digest=str(retained_after_restore.get("digest") or ""),
        )
    if not verified:
        issues.append("run-index state did not match the pre-smoke snapshot")
    return {
        "ok": not issues,
        "status": "restored" if not issues else "failed",
        "restored_files": restored,
        "removed_files": removed,
        "issues": issues,
    }


def skipped_check(name: str, reason: str, *, service: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "kind": "external-boundary", "ok": True, "status": "skipped", "reason": reason}
    if service:
        row["service"] = service
    return row


def run_command(root: Path, command: list[str], *, timeout_seconds: int = 60, cwd: Path | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd or root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return {
            "name": Path(command[0]).name if command else "command",
            "kind": "command",
            "ok": False,
            "status": "timeout",
            "command": command,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "stdout_tail": stdout[-1200:],
            "stderr_tail": stderr[-1200:],
            "issue": f"timed out after {timeout_seconds}s",
        }
    return {
        "name": Path(command[0]).name if command else "command",
        "kind": "command",
        "ok": completed.returncode == 0,
        "status": "passed" if completed.returncode == 0 else "failed",
        "command": command,
        "returncode": completed.returncode,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "stdout_tail": completed.stdout[-1200:],
        "stderr_tail": completed.stderr[-1200:],
    }


def named_command_check(name: str, root: Path, command: list[str], *, timeout_seconds: int = 60, cwd: Path | None = None) -> dict[str, Any]:
    row = run_command(root, command, timeout_seconds=timeout_seconds, cwd=cwd)
    row["name"] = name
    row["kind"] = "domain-fixture"
    return row


def domain_fixture_row(name: str, ok: bool, *, issue: str = "", details: dict[str, Any] | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "kind": "domain-fixture", "ok": ok, "status": "passed" if ok else "failed"}
    if issue:
        row["issue"] = issue
    if details:
        row["details"] = details
    return row


def workflow_eval_suite_check(root: Path, workflow_name: str) -> dict[str, Any]:
    return named_command_check(
        f"{workflow_name}-eval-suite",
        root,
        [
            sys.executable,
            "-B",
            str(root / ".agents" / "manage.py"),
            "eval-workflow",
            "--name",
            workflow_name,
            "--suite",
            str(root / "automations" / workflow_name / "suites" / "workflow-evals.json"),
            "--format",
            "json",
        ],
        timeout_seconds=90,
    )


def xml_elements(path: Path, local_name: str) -> list[ET.Element]:
    root = ET.parse(path).getroot()
    return [item for item in root.iter() if item.tag.rsplit("}", 1)[-1] == local_name]


def xml_text_values(path: Path, local_name: str) -> list[str]:
    return [(item.text or "").strip() for item in xml_elements(path, local_name)]


def fixture_project(temp_root: Path) -> dict[str, Path]:
    project = temp_root / "fixture-project"
    write_text(project / "README.md", "# Fixture Project\n\nSee [self](README.md).")
    write_text(project / "docs" / "notes.md", "# Notes\n\nNo external links.")
    write_text(project / "src" / "Safe.cs", "namespace Fixture; public static class Safe { public static string Echo(string value) => value; }")
    write_text(
        project / "test-results.xml",
        '<testsuite name="Smoke" tests="1" failures="0" skipped="0"><testcase classname="Smoke" name="passes" /></testsuite>',
    )
    return {
        "root": project,
        "docs": project / "README.md",
        "test_result": project / "test-results.xml",
    }
