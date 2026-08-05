"""Deterministic strict-command replay selection and report planning.

This module intentionally performs no command execution.  The planning boundary
loads owner manifests, resolves replay IDs, and blocks unresolved argv
placeholders before a later isolated runner is allowed to start.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from typing import Any, Iterable

import module_contract_v3
from repo_support import repo_changed_git
from repo_support import repo_harness_paths


PLACEHOLDER_PATTERN = re.compile(r"<[^<>]+>")

EXCLUDED_CHANGED_PREFIXES = (
    ".agents/local-ai/cache/",
    ".agents/local-ai/bundle/",
    ".agents/local-ai/runtime/",
    ".agents/tools/cache/",
    ".agents/tmp/",
    ".superpowers/",
)

EXCLUDED_CHANGED_FILES = {
    ".agents/local-ai/secrets.local.json",
    ".agents/local-ai/local.settings.json",
    ".agents/harness.lock.json",
    ".agents/harness-install.json",
    ".agents/harness-install-plan.json",
    ".agents/harness-install-plan.md",
    ".agents/harness-smoke-target.json",
}

POST_TIMEOUT_PARENT_WAIT_SECONDS = 1.0


def _normalize_path(value: object) -> str:
    normalized = str(value or "").replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _excluded_changed_path(path: str) -> bool:
    if not path:
        return True
    lowered_parts = {part.lower() for part in path.split("/")}
    if "__pycache__" in lowered_parts:
        return True
    if path in EXCLUDED_CHANGED_FILES:
        return True
    if path.startswith(EXCLUDED_CHANGED_PREFIXES):
        return True
    parts = path.split("/")
    return len(parts) >= 3 and parts[0] == "automations" and parts[2] == "runs"


def _owner_manifest_for_changed_path(root: Path, path: str) -> str:
    parts = path.split("/")
    if len(parts) >= 3 and parts[:2] == [".agents", "skills"]:
        return f".agents/skills/{parts[2]}/module.json"
    if len(parts) >= 3 and parts[0] == "automations":
        return f"automations/{parts[1]}/module.json"
    if path == ".agents/manage.py" or path.startswith(".agents/harness-"):
        owner = ".agents/skills/skill-manager/module.json"
        if (root / owner).exists():
            return owner
    return ""


def changed_module_paths(root: Path, changed_paths: Iterable[object]) -> list[str]:
    """Map changed source paths to owner manifests without opening excluded state."""

    selected: set[str] = set()
    for raw_path in changed_paths:
        path = _normalize_path(raw_path)
        if _excluded_changed_path(path):
            continue
        owner = _owner_manifest_for_changed_path(root, path)
        if owner:
            selected.add(owner)
    return sorted(selected)


def discover_module_paths(root: Path) -> list[str]:
    """Discover direct accepted-module candidates without generated registries."""

    paths = [
        *root.glob(".agents/skills/*/module.json"),
        *root.glob("automations/*/module.json"),
    ]
    return sorted(
        path.relative_to(root).as_posix()
        for path in paths
        if path.is_file()
    )


def _module_identity(relative_path: str) -> tuple[str, str]:
    parts = relative_path.split("/")
    if parts[:2] == [".agents", "skills"] and len(parts) >= 3:
        return "skill", parts[2]
    if parts and parts[0] == "automations" and len(parts) >= 2:
        return "workflow", parts[1]
    return "unknown", Path(relative_path).parent.name


def _read_manifest(path: Path) -> tuple[object, str]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except FileNotFoundError:
        return (None, "manifest does not exist")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return (None, f"manifest could not be read: {exc}")


def _command_has_placeholder(command: dict[str, Any]) -> bool:
    argv = command.get("argv")
    return isinstance(argv, list) and any(
        isinstance(item, str) and PLACEHOLDER_PATTERN.search(item)
        for item in argv
    )


def _module_sort_key(row: dict[str, object]) -> tuple[int, str, str]:
    kind_order = 0 if row.get("kind") == "skill" else 1
    return kind_order, str(row.get("module_id", "")), str(row.get("module_path", ""))


def _command_sort_key(row: dict[str, object]) -> tuple[int, str, str]:
    kind_order = 0 if row.get("kind") == "skill" else 1
    return kind_order, str(row.get("module_id", "")), str(row.get("command_id", ""))


def _implicit_determinism(strict_ids: list[str]) -> dict[str, object]:
    return {
        "replay_commands": list(strict_ids),
        "allowed_temporary_effects": [],
        "volatile_json_pointers": [],
        "environment_requirements": {
            "minimum_python": "3.12",
            "executables": ["git"],
            "platforms": ["windows", "linux", "macos"],
        },
    }


def _module_plan(root: Path, relative_path: str, *, deep: bool) -> tuple[dict[str, object], list[dict[str, object]], list[str]]:
    expected_kind, expected_id = _module_identity(relative_path)
    manifest_path = root / relative_path
    if not manifest_path.exists():
        return (
            {
                "module_path": relative_path,
                "module_id": expected_id,
                "kind": expected_kind,
                "ok": False,
                "status": "missing-manifest",
                "issues": ["manifest does not exist"],
                "command_count": 0,
            },
            [],
            [],
        )

    raw, read_error = _read_manifest(manifest_path)
    if read_error:
        return (
            {
                "module_path": relative_path,
                "module_id": expected_id,
                "kind": expected_kind,
                "ok": False,
                "status": "invalid-manifest",
                "issues": [read_error],
                "command_count": 0,
            },
            [],
            [],
        )
    normalized, errors, compatibility_warnings = module_contract_v3.normalize_module_contract(raw)
    module_id = str(normalized.get("id") or expected_id)
    kind = str(normalized.get("kind") or expected_kind)
    if errors:
        return (
            {
                "module_path": relative_path,
                "module_id": module_id,
                "kind": kind,
                "ok": False,
                "status": "invalid-manifest",
                "issues": list(errors),
                "command_count": 0,
            },
            [],
            [f"{relative_path}: {warning}" for warning in compatibility_warnings],
        )
    if normalized.get("status") != "accepted":
        return (
            {
                "module_path": relative_path,
                "module_id": module_id,
                "kind": kind,
                "ok": True,
                "status": "skipped-not-accepted",
                "issues": [],
                "command_count": 0,
            },
            [],
            [],
        )

    commands = normalized.get("commands") if isinstance(normalized.get("commands"), list) else []
    commands_by_id = {
        str(command.get("id")): command
        for command in commands
        if isinstance(command, dict) and command.get("id")
    }
    strict_ids = [
        str(command_id)
        for command_id in normalized.get("strict_read_only_commands", [])
        if isinstance(command_id, str)
    ]
    determinism = normalized.get("determinism") if isinstance(normalized.get("determinism"), dict) else None
    effective_determinism = dict(determinism) if determinism is not None else _implicit_determinism(strict_ids)
    warnings = [f"{relative_path}: {warning}" for warning in compatibility_warnings]
    if deep:
        replay_ids = strict_ids
        selection_source = "deep-strict-read-only"
    elif determinism is not None:
        replay_ids = [
            str(command_id)
            for command_id in determinism.get("replay_commands", [])
            if isinstance(command_id, str)
        ]
        selection_source = "determinism.replay_commands"
    else:
        replay_ids = strict_ids
        selection_source = "implicit-strict-default"
        warnings.append(
            f"{relative_path}: implicit-strict-default selected every strict read-only command because determinism is not declared"
        )

    command_rows: list[dict[str, object]] = []
    for command_id in replay_ids:
        command = commands_by_id[command_id]
        blocked = _command_has_placeholder(command)
        command_rows.append(
            {
                "module_path": relative_path,
                "module_id": module_id,
                "kind": kind,
                "command_id": command_id,
                "argv": list(command.get("argv", [])),
                "timeout_seconds": command.get("timeout_seconds", 0),
                "working_directory": command.get("working_directory", ""),
                "declared_effects": list(command.get("effects", [])),
                "selection_source": selection_source,
                "determinism": effective_determinism,
                "ok": not blocked,
                "status": "blocked-placeholder" if blocked else "planned",
                "issues": ["argv contains an unresolved placeholder"] if blocked else [],
            }
        )
    module_ok = all(row.get("ok") is True for row in command_rows)
    return (
        {
            "module_path": relative_path,
            "module_id": module_id,
            "kind": kind,
            "ok": module_ok,
            "status": "planned" if module_ok else "blocked",
            "issues": [],
            "command_count": len(command_rows),
            "selection_source": selection_source,
        },
        command_rows,
        warnings,
    )


def build_plan(
    root: Path,
    *,
    changed: bool = False,
    all_modules: bool = False,
    deep: bool = False,
    changed_paths: Iterable[object] | None = None,
) -> dict[str, object]:
    """Build a stable replay plan without running any selected command."""

    if changed == all_modules:
        raise ValueError("select exactly one of changed or all_modules")
    root = root.resolve()
    if changed:
        source_paths = (
            list(changed_paths)
            if changed_paths is not None
            else repo_changed_git.changed_files(root)
        )
        module_paths = changed_module_paths(root, source_paths)
        mode = "changed"
    else:
        module_paths = discover_module_paths(root)
        mode = "all"

    modules: list[dict[str, object]] = []
    commands: list[dict[str, object]] = []
    warnings: list[str] = []
    for relative_path in module_paths:
        module_row, command_rows, module_warnings = _module_plan(
            root,
            relative_path,
            deep=deep,
        )
        modules.append(module_row)
        commands.extend(command_rows)
        warnings.extend(module_warnings)
    modules.sort(key=_module_sort_key)
    commands.sort(key=_command_sort_key)
    warnings = sorted(dict.fromkeys(warnings))

    blocked_modules = [row for row in modules if row.get("ok") is not True]
    blocked_placeholders = [
        row for row in commands if row.get("status") == "blocked-placeholder"
    ]
    ok = not blocked_modules and not blocked_placeholders
    if not modules and not commands:
        status = "empty-selection"
    elif ok:
        status = "planned"
    else:
        status = "blocked"
    return {
        "schema_version": 1,
        "tool": "skill-manager.determinism-check",
        "mode": mode,
        "deep": deep,
        "ok": ok,
        "status": status,
        "summary": {
            "module_count": len(modules),
            "command_count": len(commands),
            "blocked_module_count": len(blocked_modules),
            "blocked_placeholder_count": len(blocked_placeholders),
            "warning_count": len(warnings),
        },
        "modules": modules,
        "commands": commands,
        "warnings": warnings,
        "next_command": (
            "Resolve blocked manifest or argv declarations before isolated replay."
            if not ok
            else "Run the selected commands twice in fresh isolated fixtures."
        ),
    }


SEED_EXCLUDED_PREFIXES = (
    ".git/",
    ".agents/local-ai/cache/",
    ".agents/local-ai/bundle/",
    ".agents/local-ai/runtime/",
    ".agents/local-ai/models/",
    ".agents/tools/cache/",
    ".agents/tmp/",
    ".superpowers/",
)

SEED_EXCLUDED_FILES = {
    *EXCLUDED_CHANGED_FILES,
    ".agents/harness-install-plan.json",
    ".agents/harness-install-plan.md",
}

SEED_EXCLUDED_DIRECTORY_NAMES = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
}

_CREDENTIAL_NAME_PATTERNS = (
    re.compile(r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|CREDENTIALS?|API_KEY|AUTHORIZATION)(?:_|$)", re.IGNORECASE),
    re.compile(r"(?:^|_)PAT(?:_|$)", re.IGNORECASE),
)

_AMBIENT_ENVIRONMENT_ALLOWLIST = {
    "COMSPEC",
    "PATH",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "WINDIR",
}

_EXPLICIT_SENSITIVE_ENVIRONMENT_NAMES = {
    "GIT_ASKPASS",
    "SSH_ASKPASS",
    "SSH_AUTH_SOCK",
    "SYSTEM_ACCESSTOKEN",
}


def _seed_excluded_path(path: str) -> bool:
    normalized = _normalize_path(path)
    if not normalized:
        return True
    if normalized in SEED_EXCLUDED_FILES:
        return True
    if normalized.startswith(SEED_EXCLUDED_PREFIXES):
        return True
    parts = normalized.split("/")
    if any(part in SEED_EXCLUDED_DIRECTORY_NAMES for part in parts):
        return True
    return len(parts) >= 3 and parts[0] == "automations" and parts[2] == "runs"


def _run_git(
    cwd: Path,
    arguments: list[str],
    *,
    timeout_seconds: int = 120,
    environment: dict[str, str] | None = None,
) -> dict[str, object]:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            env=environment,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": b"",
            "stderr": str(exc).encode("utf-8", errors="replace"),
        }
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout or b"",
        "stderr": completed.stderr or b"",
    }


def _git_source_paths(
    root: Path,
    *,
    environment: dict[str, str] | None = None,
) -> tuple[list[str], str]:
    result = _run_git(
        root,
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        environment=environment,
    )
    if result.get("ok") is not True:
        error = bytes(result.get("stderr") or b"").decode("utf-8", errors="replace").strip()
        return [], error or "git ls-files failed"
    values = bytes(result.get("stdout") or b"").decode("utf-8", errors="strict").split("\0")
    return sorted(dict.fromkeys(_normalize_path(value) for value in values if value)), ""


def _git_initialize_seed(
    seed_root: Path,
    *,
    environment: dict[str, str] | None = None,
) -> tuple[bool, list[dict[str, object]]]:
    rows: list[dict[str, object]] = []
    commands = (
        ["init", "--quiet"],
        ["config", "user.name", "Determinism Fixture"],
        ["config", "user.email", "determinism@example.invalid"],
        ["config", "core.longpaths", "true"],
        ["add", "-A"],
        ["commit", "--quiet", "-m", "determinism seed"],
    )
    for arguments in commands:
        result = _run_git(seed_root, list(arguments), environment=environment)
        rows.append(
            {
                "argv": ["git", *arguments],
                "ok": result.get("ok") is True,
                "returncode": result.get("returncode"),
                "stderr": bytes(result.get("stderr") or b"").decode("utf-8", errors="replace")[-1000:],
            }
        )
        if result.get("ok") is not True:
            return False, rows
    return True, rows


def build_isolated_seed(
    source_root: Path,
    seed_root: Path,
    *,
    source_paths: Iterable[object] | None = None,
) -> dict[str, object]:
    """Copy a sanitized source snapshot without following filesystem indirections."""

    source_root = repo_harness_paths.absolute_path(source_root)
    seed_root = repo_harness_paths.absolute_path(seed_root)
    git_environment, git_environment_report = _sanitized_environment(
        seed_root.parent / f".{seed_root.name}-home",
        seed_root.parent,
        seed_root.parent,
    )
    unsafe_paths: list[dict[str, str]] = []
    issues: list[str] = []
    try:
        relationship = repo_harness_paths.root_relationship(
            source_root,
            seed_root,
            operation="determinism-seed-root",
        )
    except repo_harness_paths.UnsafeHarnessPathError as exc:
        relationship = repo_harness_paths.RootRelationship("ambiguous", reason=str(exc))
        repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
    if relationship.kind != "distinct":
        issues.append(
            "determinism seed root must be filesystem-distinct from the source root"
        )
    if seed_root.exists() and any(seed_root.iterdir()):
        issues.append("determinism seed root must be absent or empty")

    if source_paths is None:
        paths, git_error = _git_source_paths(
            source_root,
            environment=git_environment,
        )
        if git_error:
            issues.append(f"source file discovery failed: {git_error}")
    else:
        paths = sorted(dict.fromkeys(_normalize_path(value) for value in source_paths))
    selected: list[str] = []
    excluded: list[str] = []
    source_guard = repo_harness_paths.HarnessPathGuard(source_root, label="determinism-source")
    try:
        source_guard.check_root(operation="determinism-source-root")
    except repo_harness_paths.UnsafeHarnessPathError as exc:
        repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
    for raw_path in paths:
        if _seed_excluded_path(raw_path):
            excluded.append(raw_path)
            continue
        try:
            normalized = repo_harness_paths.normalize_relative_path(raw_path)
            if not source_guard.is_file(normalized, operation="determinism-source-preflight"):
                if source_guard.exists(normalized, operation="determinism-source-preflight"):
                    issues.append(f"source path is not a regular file: {normalized}")
                continue
            selected.append(normalized)
        except (ValueError, repo_harness_paths.UnsafeHarnessPathError) as exc:
            if isinstance(exc, repo_harness_paths.UnsafeHarnessPathError):
                repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
            else:
                issues.append(f"unsafe source path {raw_path!r}: {exc}")
    if issues or unsafe_paths:
        return {
            "ok": False,
            "status": "blocked",
            "source_root": str(source_root),
            "seed_root": str(seed_root),
            "copied_count": 0,
            "excluded_count": len(excluded),
            "excluded_paths": sorted(excluded),
            "unsafe_paths": repo_harness_paths.sorted_unsafe_paths(unsafe_paths),
            "issues": issues,
            "git": [],
        }

    seed_guard = repo_harness_paths.HarnessPathGuard(seed_root, label="determinism-seed")
    try:
        seed_guard.ensure_root(operation="determinism-seed-create")
        for relative_path in sorted(dict.fromkeys(selected)):
            seed_guard.copy_from(
                source_guard,
                relative_path,
                operation="determinism-seed-copy",
            )
    except repo_harness_paths.UnsafeHarnessPathError as exc:
        repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
        return {
            "ok": False,
            "status": "blocked",
            "source_root": str(source_root),
            "seed_root": str(seed_root),
            "copied_count": 0,
            "excluded_count": len(excluded),
            "excluded_paths": sorted(excluded),
            "unsafe_paths": repo_harness_paths.sorted_unsafe_paths(unsafe_paths),
            "issues": ["sanitized source copy failed"],
            "git": [],
        }
    git_ok, git_rows = _git_initialize_seed(
        seed_root,
        environment=git_environment,
    )
    if not git_ok:
        issues.append("private seed Git initialization failed")
    return {
        "ok": git_ok,
        "status": "ready" if git_ok else "failed",
        "source_root": str(source_root),
        "seed_root": str(seed_root),
        "copied_count": len(selected),
        "excluded_count": len(excluded),
        "excluded_paths": sorted(excluded),
        "unsafe_paths": repo_harness_paths.sorted_unsafe_paths(unsafe_paths),
        "issues": issues,
        "git": git_rows,
        "environment": git_environment_report,
        "private_git_directory": (seed_root / ".git").is_dir(),
    }


def _git_path_set(
    root: Path,
    arguments: list[str],
    *,
    environment: dict[str, str] | None = None,
) -> set[str]:
    result = _run_git(root, arguments, environment=environment)
    if result.get("ok") is not True:
        return set()
    return {
        _normalize_path(value)
        for value in bytes(result.get("stdout") or b"").decode("utf-8", errors="replace").split("\0")
        if value
    }


def _snapshot_git_states(
    root: Path,
    *,
    environment: dict[str, str] | None = None,
) -> dict[str, str]:
    tracked = _git_path_set(root, ["ls-files", "-z"], environment=environment)
    untracked = _git_path_set(
        root,
        ["ls-files", "-z", "--others", "--exclude-standard"],
        environment=environment,
    )
    ignored = _git_path_set(
        root,
        ["ls-files", "-z", "--others", "--ignored", "--exclude-standard"],
        environment=environment,
    )
    states = {path: "tracked" for path in tracked}
    states.update({path: "untracked" for path in untracked})
    states.update({path: "ignored" for path in ignored})
    return states


def snapshot_tree(
    root: Path,
    *,
    git_root: Path | None = None,
    exclude_root_git: bool = False,
    environment: dict[str, str] | None = None,
) -> dict[str, object]:
    """Capture a no-follow stable filesystem manifest."""

    root = root.resolve(strict=False)
    guard = repo_harness_paths.HarnessPathGuard(root, label="determinism-snapshot")
    files: list[dict[str, object]] = []
    empty_directories: list[str] = []
    unsafe_paths: list[dict[str, str]] = []
    issues: list[str] = []
    states = (
        _snapshot_git_states(git_root, environment=environment)
        if git_root is not None
        else {}
    )
    try:
        guard.check_root(operation="determinism-snapshot-root")
    except repo_harness_paths.UnsafeHarnessPathError as exc:
        repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
        return {
            "ok": False,
            "files": [],
            "empty_directories": [],
            "unsafe_paths": repo_harness_paths.sorted_unsafe_paths(unsafe_paths),
            "issues": [],
        }

    def visit(directory: Path, prefix: str) -> bool:
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            issues.append(f"could not enumerate {prefix or '.'}: {exc}")
            return False
        included = False
        for entry in entries:
            if exclude_root_git and not prefix and entry.name == ".git":
                continue
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            try:
                normalized = repo_harness_paths.normalize_relative_path(relative)
                metadata = entry.stat(follow_symlinks=False)
            except (OSError, ValueError) as exc:
                issues.append(f"could not inspect {relative}: {exc}")
                continue
            if repo_harness_paths._is_reparse_stat(metadata):
                unsafe = repo_harness_paths.UnsafeHarnessPathError(
                    path=normalized,
                    root=str(root),
                    operation="determinism-snapshot",
                    reason="path is a symbolic link, junction, or reparse point",
                )
                repo_harness_paths.add_unsafe_path(unsafe_paths, unsafe)
                included = True
                continue
            if stat.S_ISDIR(metadata.st_mode):
                child_included = visit(Path(entry.path), normalized)
                if not child_included:
                    empty_directories.append(normalized + "/")
                included = True
                continue
            if not stat.S_ISREG(metadata.st_mode):
                unsafe = repo_harness_paths.UnsafeHarnessPathError(
                    path=normalized,
                    root=str(root),
                    operation="determinism-snapshot",
                    reason="path is not a regular file or directory",
                )
                repo_harness_paths.add_unsafe_path(unsafe_paths, unsafe)
                included = True
                continue
            try:
                files.append(
                    {
                        "path": normalized,
                        "bytes": metadata.st_size,
                        "sha256": guard.sha256(normalized, operation="determinism-snapshot-hash"),
                        "executable": bool(metadata.st_mode & 0o111),
                        "git_state": states.get(normalized, "external" if git_root is None else "unclassified"),
                    }
                )
            except repo_harness_paths.UnsafeHarnessPathError as exc:
                repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
            included = True
        return included

    if root.exists():
        visit(root, "")
    return {
        "ok": not issues and not unsafe_paths,
        "files": sorted(files, key=lambda row: str(row["path"])),
        "empty_directories": sorted(empty_directories),
        "unsafe_paths": repo_harness_paths.sorted_unsafe_paths(unsafe_paths),
        "issues": issues,
    }


def snapshot_changes(
    before: dict[str, object],
    after: dict[str, object],
    *,
    root_name: str,
) -> list[dict[str, object]]:
    """Return stable file and empty-directory operations between snapshots."""

    before_files = {
        str(row.get("path")): row
        for row in before.get("files", [])
        if isinstance(row, dict) and row.get("path")
    }
    after_files = {
        str(row.get("path")): row
        for row in after.get("files", [])
        if isinstance(row, dict) and row.get("path")
    }
    rows: list[dict[str, object]] = []
    for path in sorted(set(before_files) | set(after_files)):
        prior = before_files.get(path)
        current = after_files.get(path)
        if prior is None:
            operation = "create"
            metadata = current or {}
        elif current is None:
            operation = "delete"
            metadata = prior
        elif prior == current:
            continue
        else:
            operation = "modify"
            metadata = current
        rows.append(
            {
                "root": root_name,
                "path": path,
                "operation": operation,
                "entry_type": "file",
                "git_state": metadata.get("git_state", "external"),
                "executable": bool(metadata.get("executable", False)),
                "sha256": metadata.get("sha256", ""),
                "bytes": metadata.get("bytes", 0),
            }
        )
    before_dirs = {
        str(path)
        for path in before.get("empty_directories", [])
        if isinstance(path, str)
    }
    after_dirs = {
        str(path)
        for path in after.get("empty_directories", [])
        if isinstance(path, str)
    }
    for path in sorted(after_dirs - before_dirs):
        rows.append(
            {
                "root": root_name,
                "path": path,
                "operation": "create",
                "entry_type": "directory",
                "git_state": "external",
                "executable": False,
                "sha256": "",
                "bytes": 0,
            }
        )
    for path in sorted(before_dirs - after_dirs):
        rows.append(
            {
                "root": root_name,
                "path": path,
                "operation": "delete",
                "entry_type": "directory",
                "git_state": "external",
                "executable": False,
                "sha256": "",
                "bytes": 0,
            }
        )
    return sorted(rows, key=lambda row: (str(row["path"]), str(row["operation"])))


def _changed_artifacts(
    roots: dict[str, Path],
    changes: dict[str, list[dict[str, object]]],
) -> dict[str, dict[str, object]]:
    artifacts: dict[str, dict[str, object]] = {}
    for root_name, rows in changes.items():
        root = roots[root_name]
        guard = repo_harness_paths.HarnessPathGuard(root, label="determinism-artifact")
        for row in rows:
            if row.get("entry_type") != "file" or row.get("operation") not in {"create", "modify"}:
                continue
            path = str(row.get("path", ""))
            try:
                candidate = guard.check(path, operation="determinism-artifact-read")
                if not guard.is_file(path, operation="determinism-artifact-read"):
                    continue
                content = candidate.read_bytes()
            except (OSError, repo_harness_paths.UnsafeHarnessPathError):
                continue
            label = f"{root_name}:{path}"
            artifacts[label] = {
                "label": label,
                "root": root_name,
                "path": path,
                "content_base64": base64.b64encode(content).decode("ascii"),
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
    return artifacts


_VOLATILE_SENTINEL = "<skills-harness-volatile>"


def _decode_pointer(pointer: str) -> tuple[list[str], str]:
    if not pointer.startswith("/"):
        return [], "JSON pointer must start with /"
    tokens: list[str] = []
    for raw in pointer[1:].split("/"):
        index = 0
        while index < len(raw):
            if raw[index] == "~" and (index + 1 >= len(raw) or raw[index + 1] not in {"0", "1"}):
                return [], f"JSON pointer has invalid escape: {pointer}"
            index += 2 if raw[index] == "~" else 1
        tokens.append(raw.replace("~1", "/").replace("~0", "~"))
    return tokens, ""


def _replace_pointer(document: object, pointer: str) -> tuple[bool, str]:
    tokens, issue = _decode_pointer(pointer)
    if issue:
        return False, issue
    if not tokens:
        return False, "root JSON pointer is not supported"
    current = document
    for token in tokens[:-1]:
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return False, f"JSON pointer is missing: {pointer}"
    final = tokens[-1]
    if isinstance(current, dict) and final in current:
        current[final] = _VOLATILE_SENTINEL
        return True, ""
    if isinstance(current, list) and final.isdigit() and int(final) < len(current):
        current[int(final)] = _VOLATILE_SENTINEL
        return True, ""
    return False, f"JSON pointer is missing: {pointer}"


def _parse_json_bytes(content: bytes) -> tuple[object | None, str]:
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        return (None, f"content is not complete UTF-8 JSON: {exc}")
    try:
        return json.loads(text), ""
    except json.JSONDecodeError as exc:
        return (None, f"content is not complete UTF-8 JSON: {exc}")


def canonicalize_json_bytes(
    content: bytes,
    pointers: Iterable[str],
) -> dict[str, object]:
    """Canonicalize one complete JSON document at exactly declared pointers."""

    document, parse_issue = _parse_json_bytes(content)
    if parse_issue:
        return {
            "ok": False,
            "content": content,
            "applications": [],
            "issues": [parse_issue],
        }
    applications: list[str] = []
    issues: list[str] = []
    for pointer in pointers:
        applied, issue = _replace_pointer(document, str(pointer))
        if applied:
            applications.append(str(pointer))
        else:
            issues.append(issue)
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
    ).encode("utf-8")
    return {
        "ok": not issues,
        "content": canonical,
        "applications": applications,
        "issues": issues,
    }


def _canonicalize_if_json(
    content: bytes,
    pointers: list[str],
) -> tuple[bytes, set[str], bool]:
    if not pointers:
        return content, set(), False
    document, parse_issue = _parse_json_bytes(content)
    if parse_issue:
        return content, set(), False
    applications: set[str] = set()
    for pointer in pointers:
        applied, _issue = _replace_pointer(document, pointer)
        if applied:
            applications.add(pointer)
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
    ).encode("utf-8")
    return canonical, applications, True


def _credential_name(name: str) -> bool:
    return any(pattern.search(name) for pattern in _CREDENTIAL_NAME_PATTERNS)


def _sensitive_environment_name(name: str) -> bool:
    upper = name.upper()
    return (
        _credential_name(upper)
        or upper in _EXPLICIT_SENSITIVE_ENVIRONMENT_NAMES
        or upper.startswith("GIT_CONFIG_")
        or upper.startswith("AZURE_")
        or upper.startswith("ARM_")
    )


def _sanitized_environment(
    home: Path,
    temporary: Path,
    workspace: Path,
) -> tuple[dict[str, str], dict[str, object]]:
    allowed = sorted(
        name
        for name in os.environ
        if name.upper() in _AMBIENT_ENVIRONMENT_ALLOWLIST
    )
    removed = sorted(
        name
        for name in os.environ
        if name.upper() not in _AMBIENT_ENVIRONMENT_ALLOWLIST
    )
    removed_credentials = sorted(
        name for name in removed if _sensitive_environment_name(name)
    )
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in _AMBIENT_ENVIRONMENT_ALLOWLIST
    }
    home_value = home.as_posix()
    temp_value = temporary.as_posix()
    environment.update(
        {
            "HOME": home_value,
            "USERPROFILE": home_value,
            "TEMP": temp_value,
            "TMP": temp_value,
            "TMPDIR": temp_value,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "TZ": "UTC",
            "LANG": "C",
            "LC_ALL": "C",
            "NO_COLOR": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "",
            "PIP_NO_INDEX": "1",
        }
    )
    return environment, {
        "home": home_value,
        "temporary": temp_value,
        "workspace": workspace.as_posix(),
        "allowlisted_ambient_names": allowed,
        "removed_environment_names": removed,
        "removed_credential_names": removed_credentials,
        "fixed": {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "TZ": "UTC",
            "LANG": "C",
            "LC_ALL": "C",
            "NO_COLOR": "1",
        },
    }


def isolated_environment(replay_root: Path) -> tuple[dict[str, str], dict[str, object]]:
    home = replay_root / "home"
    temporary = replay_root / "temporary"
    workspace = temporary / "workspace"
    for path in (home, temporary, workspace):
        path.mkdir(parents=True, exist_ok=True)
    return _sanitized_environment(home, temporary, workspace)


def _process_group_kwargs() -> dict[str, object]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _terminate_process_group(process: subprocess.Popen[bytes]) -> dict[str, object]:
    cleanup: dict[str, object] = {
        "attempted": True,
        "ok": False,
        "method": "",
        "process_tree_termination_confirmed": False,
        "parent_termination_confirmed": False,
    }
    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                shell=False,
            )
            cleanup["method"] = "taskkill-process-tree"
            if completed.returncode == 0:
                cleanup["ok"] = True
                cleanup["process_tree_termination_confirmed"] = True
                cleanup["parent_termination_confirmed"] = True
            else:
                cleanup["issue"] = (
                    "taskkill process-tree termination failed with exit code "
                    f"{completed.returncode}"
                )
                if process.poll() is None:
                    process.kill()
                    cleanup["method"] = "process-kill-fallback"
                    cleanup["parent_termination_confirmed"] = True
        else:
            os.killpg(process.pid, signal.SIGKILL)
            cleanup["method"] = "posix-process-group-kill"
            cleanup["ok"] = True
            cleanup["process_tree_termination_confirmed"] = True
            cleanup["parent_termination_confirmed"] = True
    except (OSError, subprocess.TimeoutExpired) as exc:
        cleanup["issue"] = str(exc)
        try:
            process.kill()
            cleanup["method"] = "process-kill-fallback"
            cleanup["parent_termination_confirmed"] = True
        except OSError as fallback_exc:
            cleanup["fallback_issue"] = str(fallback_exc)
    return cleanup


def _effective_argv(command: dict[str, object]) -> tuple[list[str], str]:
    argv = command.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        return [], "argv must be a non-empty array of non-empty strings"
    effective = list(argv)
    if effective[0].lower() in {"python", "python3"}:
        effective[0] = sys.executable
    return effective, ""


def _working_directory(
    repository: Path,
    replay_root: Path,
    module_path: str,
    policy: object,
) -> tuple[Path | None, str]:
    if policy == "repository":
        return repository, ""
    if policy == "module":
        try:
            normalized = repo_harness_paths.normalize_relative_path(module_path)
        except ValueError as exc:
            return (None, str(exc))
        module_dir = repository.joinpath(*normalized.split("/")).parent
        if not module_dir.is_dir():
            return None, f"module working directory does not exist: {module_dir}"
        return module_dir, ""
    if policy == "temporary":
        return replay_root / "temporary" / "workspace", ""
    return None, f"unsupported working-directory policy: {policy}"


def capture_replay(
    seed_root: Path,
    module_path: str,
    command: dict[str, object],
    replay_root: Path,
) -> dict[str, object]:
    """Clone one private fixture and capture a command plus before/after manifests."""

    seed_root = seed_root.resolve(strict=False)
    replay_root = replay_root.resolve(strict=False)
    repository = replay_root / "repository"
    replay_root.mkdir(parents=True, exist_ok=True)
    environment, environment_report = isolated_environment(replay_root)
    clone = _run_git(
        seed_root,
        [
            "-c",
            "core.longpaths=true",
            "clone",
            "--no-local",
            "--quiet",
            str(seed_root),
            str(repository),
        ],
        environment=environment,
    )
    if clone.get("ok") is not True:
        return {
            "capture_ok": False,
            "status": "clone-failed",
            "returncode": None,
            "timed_out": False,
            "stdout_text": "",
            "stderr_text": bytes(clone.get("stderr") or b"").decode("utf-8", errors="replace"),
            "environment": environment_report,
            "process_cleanup": {"attempted": False, "ok": True, "method": ""},
            "snapshots": {},
            "private_git_directory": False,
        }
    private_git = (repository / ".git").is_dir() and not (repository / ".git").is_file()
    before = {
        "repository": snapshot_tree(
            repository,
            git_root=repository,
            exclude_root_git=True,
            environment=environment,
        ),
        "temporary": snapshot_tree(replay_root / "temporary"),
        "home": snapshot_tree(replay_root / "home"),
    }
    argv, argv_error = _effective_argv(command)
    cwd, cwd_error = _working_directory(
        repository,
        replay_root,
        module_path,
        command.get("working_directory"),
    )
    if argv_error or cwd_error or cwd is None:
        return {
            "capture_ok": False,
            "status": "preflight-failed",
            "returncode": None,
            "timed_out": False,
            "stdout_text": "",
            "stderr_text": argv_error or cwd_error,
            "environment": environment_report,
            "process_cleanup": {"attempted": False, "ok": True, "method": ""},
            "snapshots": {"before": before, "after": before},
            "private_git_directory": private_git,
        }
    stdout = b""
    stderr = b""
    timed_out = False
    process_cleanup: dict[str, object] = {
        "attempted": False,
        "ok": True,
        "method": "",
    }
    process: subprocess.Popen[bytes] | None = None
    spawn_issue = ""
    with tempfile.TemporaryFile() as stdout_capture, tempfile.TemporaryFile() as stderr_capture:
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout_capture,
                stderr=stderr_capture,
                shell=False,
                **_process_group_kwargs(),
            )
            process.wait(
                timeout=int(command.get("timeout_seconds") or 1)
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            process_cleanup = _terminate_process_group(process)
            process_cleanup["output_capture"] = "temporary-files"
            process_cleanup["parent_wait_timeout_seconds"] = (
                POST_TIMEOUT_PARENT_WAIT_SECONDS
            )
            try:
                process.wait(timeout=POST_TIMEOUT_PARENT_WAIT_SECONDS)
                process_cleanup["parent_termination_confirmed"] = True
            except subprocess.TimeoutExpired:
                process_cleanup["ok"] = False
                process_cleanup["process_tree_termination_confirmed"] = False
                existing_issue = str(process_cleanup.get("issue") or "")
                wait_issue = (
                    "timed-out parent did not exit within "
                    f"{POST_TIMEOUT_PARENT_WAIT_SECONDS:.1f}s after cleanup"
                )
                process_cleanup["issue"] = (
                    f"{existing_issue}; {wait_issue}"
                    if existing_issue
                    else wait_issue
                )
                try:
                    if process.poll() is None:
                        process.kill()
                except OSError as kill_exc:
                    process_cleanup["parent_kill_issue"] = str(kill_exc)
                try:
                    process.wait(timeout=POST_TIMEOUT_PARENT_WAIT_SECONDS)
                    process_cleanup["parent_termination_confirmed"] = True
                except (OSError, subprocess.TimeoutExpired) as wait_exc:
                    process_cleanup["parent_termination_confirmed"] = False
                    process_cleanup["parent_wait_issue"] = str(wait_exc)
        except OSError as exc:
            spawn_issue = str(exc)
        stdout_capture.flush()
        stderr_capture.flush()
        stdout_capture.seek(0)
        stderr_capture.seek(0)
        stdout = stdout_capture.read()
        stderr = stderr_capture.read()
    after = {
        "repository": snapshot_tree(
            repository,
            git_root=repository,
            exclude_root_git=True,
            environment=environment,
        ),
        "temporary": snapshot_tree(replay_root / "temporary"),
        "home": snapshot_tree(replay_root / "home"),
    }
    changes = {
        root_name: snapshot_changes(
            before[root_name],
            after[root_name],
            root_name=root_name,
        )
        for root_name in ("repository", "temporary", "home")
    }
    artifacts = _changed_artifacts(
        {
            "repository": repository,
            "temporary": replay_root / "temporary",
            "home": replay_root / "home",
        },
        changes,
    )
    snapshot_ok = all(
        row.get("ok") is True
        for stage in (before, after)
        for row in stage.values()
        if isinstance(row, dict)
    )
    returncode = process.returncode if process is not None else None
    capture_ok = not spawn_issue and not timed_out and snapshot_ok and private_git
    status = (
        "capture-failed"
        if spawn_issue or not snapshot_ok or not private_git
        else "timeout"
        if timed_out
        else "captured"
        if returncode == 0
        else "captured-nonzero"
    )
    return {
        "capture_ok": capture_ok,
        "status": status,
        "returncode": returncode,
        "timed_out": timed_out,
        "argv": list(command.get("argv", [])),
        "effective_argv": argv,
        "working_directory": cwd.as_posix(),
        "stdout_base64": base64.b64encode(stdout or b"").decode("ascii"),
        "stderr_base64": base64.b64encode(stderr or b"").decode("ascii"),
        "stdout_sha256": hashlib.sha256(stdout or b"").hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr or b"").hexdigest(),
        "stdout_text": (stdout or b"").decode("utf-8", errors="replace"),
        "stderr_text": (stderr or b"").decode("utf-8", errors="replace") or spawn_issue,
        "environment": environment_report,
        "process_cleanup": process_cleanup,
        "snapshots": {"before": before, "after": after},
        "changes": changes,
        "artifacts": artifacts,
        "private_git_directory": private_git,
    }


def capture_replay_pair(
    source_root: Path,
    module_path: str,
    command: dict[str, object],
    work_dir: Path,
    *,
    prepared_seed: Path | None = None,
    prepared_seed_report: dict[str, object] | None = None,
) -> dict[str, object]:
    """Capture two isolated replays and always remove the temporary fixture root."""

    work_dir = work_dir.resolve(strict=False)
    report: dict[str, object] = {
        "ok": False,
        "status": "failed",
        "seed": {},
        "runs": [],
        "fixture_isolation": {"independent_git_directories": False},
        "cleanup": {"attempted": False, "ok": False, "issue": ""},
    }
    try:
        work_dir.mkdir(parents=True, exist_ok=False)
        seed_root = (
            prepared_seed.resolve(strict=False)
            if prepared_seed is not None
            else work_dir / "seed"
        )
        if prepared_seed is not None:
            seed = dict(prepared_seed_report or {})
            seed_issues = [
                str(value)
                for value in seed.get("issues", [])
                if isinstance(value, str) and value
            ]
            source_root = repo_harness_paths.absolute_path(source_root)
            recorded_source = str(seed.get("source_root", ""))
            recorded_seed = str(seed.get("seed_root", ""))
            if recorded_source != str(source_root):
                seed_issues.append("prepared seed report recorded source_root does not match the replay source")
            if recorded_seed != str(seed_root):
                seed_issues.append("prepared seed report recorded seed_root does not match the prepared seed")
            if seed.get("ok") is not True or seed.get("status") != "ready":
                seed_issues.append("prepared seed report is not ready")
            if seed.get("private_git_directory") is not True:
                seed_issues.append("prepared seed report does not attest a private Git directory")
            try:
                source_relationship = repo_harness_paths.root_relationship(
                    source_root,
                    seed_root,
                    operation="determinism-prepared-seed-source",
                )
                work_relationship = repo_harness_paths.root_relationship(
                    seed_root,
                    work_dir,
                    operation="determinism-prepared-seed-work",
                )
                seed_guard = repo_harness_paths.HarnessPathGuard(
                    seed_root,
                    label="determinism-prepared-seed",
                )
                seed_guard.check_root(operation="determinism-prepared-seed-root")
                if not seed_guard.is_dir(
                    ".git",
                    operation="determinism-prepared-seed-git",
                ):
                    seed_issues.append("prepared seed does not contain a private .git directory")
            except repo_harness_paths.UnsafeHarnessPathError as exc:
                seed_issues.append(str(exc))
            else:
                if source_relationship.kind != "distinct":
                    seed_issues.append("prepared seed must be filesystem-distinct from the source")
                if work_relationship.kind != "distinct":
                    seed_issues.append("prepared seed must be filesystem-distinct from the command work root")
            seed["issues"] = sorted(dict.fromkeys(seed_issues))
            seed["ok"] = not seed["issues"]
            seed["status"] = "ready" if seed["ok"] else "blocked"
        else:
            seed = build_isolated_seed(source_root, seed_root)
        report["seed"] = seed
        if seed.get("ok") is True:
            runs = [
                capture_replay(seed_root, module_path, command, work_dir / "run-a"),
                capture_replay(seed_root, module_path, command, work_dir / "run-b"),
            ]
            report["runs"] = runs
            git_directories = [
                work_dir / name / "repository" / ".git"
                for name in ("run-a", "run-b")
            ]
            independent = (
                all(row.get("private_git_directory") is True for row in runs)
                and repo_harness_paths.root_relationship(
                    git_directories[0],
                    git_directories[1],
                    operation="determinism-clone-identity",
                ).kind
                == "distinct"
            )
            report["fixture_isolation"] = {
                "independent_git_directories": independent,
                "clone_mode": "git-clone---no-local",
                "git_directories": [
                    f"{name}/repository/.git" for name in ("run-a", "run-b")
                ],
            }
            report["ok"] = independent and all(row.get("capture_ok") is True for row in runs)
            report["status"] = "captured" if report["ok"] else "capture-failed"
        else:
            report["status"] = "seed-failed"
    except (OSError, ValueError) as exc:
        report["status"] = "fixture-failed"
        report["issue"] = str(exc)
    finally:
        cleanup = {"attempted": True, "ok": True, "issue": ""}
        try:
            if work_dir.exists():
                def make_writable_and_retry(function, path, _error) -> None:
                    os.chmod(path, stat.S_IWRITE)
                    function(path)

                shutil.rmtree(work_dir, onexc=make_writable_and_retry)
        except OSError as exc:
            cleanup["ok"] = False
            cleanup["issue"] = str(exc)
            report["ok"] = False
            report["status"] = "cleanup-failed"
        report["cleanup"] = cleanup
    return report


def _decoded_capture_bytes(capture: dict[str, object], key: str) -> bytes:
    try:
        return base64.b64decode(str(capture.get(key, "")), validate=True)
    except (ValueError, binascii.Error):
        return b""


def _artifact_content(row: dict[str, object]) -> bytes:
    try:
        return base64.b64decode(str(row.get("content_base64", "")), validate=True)
    except (ValueError, binascii.Error):
        return b""


def _canonical_capture_material(
    capture: dict[str, object],
    pointers: list[str],
) -> tuple[dict[str, bytes], dict[str, dict[str, object]], dict[str, set[str]]]:
    streams: dict[str, bytes] = {}
    applications = {pointer: set() for pointer in pointers}
    for label, key in (("stdout", "stdout_base64"), ("stderr", "stderr_base64")):
        raw = _decoded_capture_bytes(capture, key)
        canonical, applied, _is_json = _canonicalize_if_json(raw, pointers)
        streams[label] = canonical
        for pointer in applied:
            applications[pointer].add(label)

    artifacts_value = capture.get("artifacts")
    artifacts = artifacts_value if isinstance(artifacts_value, dict) else {}
    canonical_artifacts: dict[str, dict[str, object]] = {}
    for label in sorted(artifacts):
        row = artifacts[label]
        if not isinstance(row, dict):
            continue
        raw = _artifact_content(row)
        path = str(row.get("path") or str(label).split(":", 1)[-1])
        if pointers and path.lower().endswith(".json"):
            canonical, applied, _is_json = _canonicalize_if_json(raw, pointers)
        else:
            canonical, applied = raw, set()
        for pointer in applied:
            applications[pointer].add(str(label))
        canonical_artifacts[str(label)] = {
            "sha256": hashlib.sha256(canonical).hexdigest(),
            "bytes": len(canonical),
        }
    return streams, canonical_artifacts, applications


def _capture_change_rows(
    capture: dict[str, object],
    root_name: str,
) -> list[dict[str, object]]:
    changes = capture.get("changes") if isinstance(capture.get("changes"), dict) else {}
    rows = changes.get(root_name) if isinstance(changes.get(root_name), list) else []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _canonical_change_rows(
    capture: dict[str, object],
    root_name: str,
    canonical_artifacts: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    rows = _capture_change_rows(capture, root_name)
    normalized: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        label = f"{root_name}:{item.get('path', '')}"
        artifact_row = canonical_artifacts.get(label)
        if artifact_row is not None and item.get("operation") in {"create", "modify"}:
            item["sha256"] = artifact_row["sha256"]
            item["bytes"] = artifact_row["bytes"]
        normalized.append(item)
    return sorted(
        normalized,
        key=lambda row: (
            str(row.get("path", "")),
            str(row.get("operation", "")),
            str(row.get("entry_type", "")),
        ),
    )


def _temporary_change_allowed(
    row: dict[str, object],
    allowed: list[dict[str, object]],
) -> bool:
    row_path = str(row.get("path", "")).rstrip("/")
    operation = str(row.get("operation", ""))
    for rule in allowed:
        if not isinstance(rule, dict):
            continue
        rule_path = str(rule.get("path", "")).rstrip("/")
        recursive = rule.get("recursive") is True
        path_matches = row_path == rule_path or (
            recursive and bool(rule_path) and row_path.startswith(rule_path + "/")
        )
        operations = rule.get("operations") if isinstance(rule.get("operations"), list) else []
        if path_matches and operation in operations:
            return True
    return False


def compare_replay_captures(
    first: dict[str, object],
    second: dict[str, object],
    determinism: dict[str, object],
) -> dict[str, object]:
    """Compare two captures exactly and enforce strict-command effect boundaries."""

    pointers = [
        str(pointer)
        for pointer in determinism.get("volatile_json_pointers", [])
        if isinstance(pointer, str)
    ]
    first_streams, first_artifacts, first_applications = _canonical_capture_material(
        first,
        pointers,
    )
    second_streams, second_artifacts, second_applications = _canonical_capture_material(
        second,
        pointers,
    )
    mismatches: list[str] = []
    if first.get("returncode") != second.get("returncode"):
        mismatches.append("exit-code")
    if bool(first.get("timed_out")) != bool(second.get("timed_out")):
        mismatches.append("timeout-state")
    for label in ("stdout", "stderr"):
        if first_streams[label] != second_streams[label]:
            mismatches.append(label)

    for pointer in pointers:
        first_labels = first_applications.get(pointer, set())
        second_labels = second_applications.get(pointer, set())
        if not first_labels and not second_labels:
            mismatches.append(f"volatile-pointer-unapplied:{pointer}")
        elif first_labels != second_labels:
            mismatches.append(f"volatile-pointer-application:{pointer}")

    artifact_labels = sorted(set(first_artifacts) | set(second_artifacts))
    generated_artifact_hashes: list[dict[str, object]] = []
    for label in artifact_labels:
        first_row = first_artifacts.get(label)
        second_row = second_artifacts.get(label)
        generated_artifact_hashes.append(
            {
                "label": label,
                "first_sha256": (first_row or {}).get("sha256", ""),
                "second_sha256": (second_row or {}).get("sha256", ""),
                "match": first_row == second_row,
            }
        )
        if first_row != second_row:
            mismatches.append(f"artifact:{label}")

    roots = ("repository", "temporary", "home")
    normalized_changes: dict[str, dict[str, list[dict[str, object]]]] = {
        "first": {},
        "second": {},
    }
    for root_name in roots:
        first_rows = _canonical_change_rows(first, root_name, first_artifacts)
        second_rows = _canonical_change_rows(second, root_name, second_artifacts)
        normalized_changes["first"][root_name] = first_rows
        normalized_changes["second"][root_name] = second_rows
        if first_rows != second_rows:
            mismatches.append(f"filesystem:{root_name}")

    observed_effects: list[str] = []
    if normalized_changes["first"]["repository"] or normalized_changes["second"]["repository"]:
        observed_effects.append("repository_write")
    if normalized_changes["first"]["temporary"] or normalized_changes["second"]["temporary"]:
        observed_effects.append("temporary_write")
    if normalized_changes["first"]["home"] or normalized_changes["second"]["home"]:
        observed_effects.append("external_write")

    undeclared_effects: list[str] = []
    if "repository_write" in observed_effects:
        undeclared_effects.append("repository_write")
    allowed_value = determinism.get("allowed_temporary_effects")
    allowed = allowed_value if isinstance(allowed_value, list) else []
    if "temporary_write" in observed_effects:
        all_temporary_rows = [
            *normalized_changes["first"]["temporary"],
            *normalized_changes["second"]["temporary"],
        ]
        if not all(_temporary_change_allowed(row, allowed) for row in all_temporary_rows):
            undeclared_effects.append("temporary_write")
    if "external_write" in observed_effects:
        undeclared_effects.append("external_write")

    capture_ok = first.get("capture_ok") is True and second.get("capture_ok") is True
    timed_out = bool(first.get("timed_out")) or bool(second.get("timed_out"))
    mismatches = list(dict.fromkeys(mismatches))
    repeatable = capture_ok and not mismatches
    command_succeeded = (
        not timed_out
        and first.get("returncode") == 0
        and second.get("returncode") == 0
    )
    if timed_out:
        status = "timeout"
    elif not capture_ok:
        status = "capture-failed"
    elif mismatches:
        status = "nondeterministic"
    elif undeclared_effects:
        status = "undeclared-effects"
    elif not command_succeeded:
        status = "deterministic-command-failure"
    else:
        status = "passed"
    return {
        "ok": status == "passed",
        "status": status,
        "repeatable": repeatable,
        "command_succeeded": command_succeeded,
        "mismatches": mismatches,
        "observed_effects": observed_effects,
        "undeclared_effects": undeclared_effects,
        "volatile_pointer_applications": {
            pointer: {
                "first": sorted(first_applications.get(pointer, set())),
                "second": sorted(second_applications.get(pointer, set())),
            }
            for pointer in pointers
        },
        "generated_artifact_hashes": generated_artifact_hashes,
        "changes": normalized_changes,
    }


BLOCKED_DECLARED_EFFECTS = {
    "repository_write",
    "temporary_write",
    "network",
    "credentials",
    "install",
    "upload",
    "external_write",
}


def current_platform_id() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def validate_environment_requirements(
    determinism: dict[str, object],
) -> dict[str, object]:
    requirements = (
        determinism.get("environment_requirements")
        if isinstance(determinism.get("environment_requirements"), dict)
        else {}
    )
    issues: list[str] = []
    minimum = str(requirements.get("minimum_python") or "3.12")
    try:
        major, minor = (int(value) for value in minimum.split(".", 1))
    except (TypeError, ValueError):
        issues.append(f"invalid minimum Python requirement: {minimum}")
        major, minor = (999, 0)
    if sys.version_info[:2] < (major, minor):
        issues.append(
            f"Python {minimum}+ is required; current is {sys.version_info.major}.{sys.version_info.minor}"
        )
    platforms = requirements.get("platforms") if isinstance(requirements.get("platforms"), list) else []
    platform = current_platform_id()
    if platforms and platform not in platforms:
        issues.append(
            f"platform {platform} is not allowed; expected one of {', '.join(str(value) for value in platforms)}"
        )
    executables = {
        str(value)
        for value in requirements.get("executables", [])
        if isinstance(value, str) and value
    }
    executables.add("git")
    missing = sorted(value for value in executables if shutil.which(value) is None)
    if missing:
        issues.append("missing required executables: " + ", ".join(missing))
    return {
        "ok": not issues,
        "minimum_python": minimum,
        "current_python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": platform,
        "allowed_platforms": [str(value) for value in platforms],
        "executables": sorted(executables),
        "missing_executables": missing,
        "issues": issues,
    }


def _command_capture_summary(row: dict[str, object]) -> dict[str, object]:
    failure_reason = ""
    if row.get("capture_ok") is not True:
        status = str(row.get("status", "capture-failed"))
        stderr = str(row.get("stderr_text", "")).lower()
        if status == "clone-failed" and "filename too long" in stderr:
            failure_reason = "git-clone-filename-too-long"
        elif status == "clone-failed" and "checkout" in stderr:
            failure_reason = "git-clone-checkout-failed"
        elif status == "clone-failed":
            failure_reason = "git-clone-failed"
        else:
            failure_reason = status
    return {
        "capture_ok": row.get("capture_ok") is True,
        "status": row.get("status", "unknown"),
        "returncode": row.get("returncode"),
        "timed_out": bool(row.get("timed_out")),
        "stdout_sha256": row.get("stdout_sha256", ""),
        "stderr_sha256": row.get("stderr_sha256", ""),
        "process_cleanup": row.get("process_cleanup", {}),
        "failure_reason": failure_reason,
    }


def _remove_fixture_root(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return True, ""
    try:
        def make_writable_and_retry(function, item, _error) -> None:
            os.chmod(item, stat.S_IWRITE)
            function(item)

        shutil.rmtree(path, onexc=make_writable_and_retry)
    except OSError as exc:
        return False, str(exc)
    return True, ""


def run_determinism_check(
    root: Path,
    *,
    changed: bool = False,
    all_modules: bool = False,
    deep: bool = False,
    changed_paths: Iterable[object] | None = None,
    work_dir: Path | None = None,
    pair_runner=None,
) -> dict[str, object]:
    """Plan, preflight, capture twice, compare, and clean selected commands."""

    root = root.resolve()
    plan = build_plan(
        root,
        changed=changed,
        all_modules=all_modules,
        deep=deep,
        changed_paths=changed_paths,
    )
    planned_commands = [
        dict(row)
        for row in plan.get("commands", [])
        if isinstance(row, dict)
    ]
    blocked_module_count = sum(
        1
        for row in plan.get("modules", [])
        if isinstance(row, dict) and row.get("ok") is not True
    )
    if not planned_commands and blocked_module_count == 0:
        return {
            "schema_version": 1,
            "tool": "skill-manager.determinism-check",
            "mode": plan.get("mode", "changed" if changed else "all"),
            "deep": deep,
            "ok": True,
            "status": "empty-selection",
            "summary": {
                "module_count": len(plan.get("modules", [])),
                "command_count": 0,
                "executed_command_count": 0,
                "executed_replay_count": 0,
                "passed_count": 0,
                "blocked_module_count": 0,
                "blocked_placeholder_count": 0,
                "blocked_environment_count": 0,
                "blocked_effect_count": 0,
                "cleanup_failed_count": 0,
            },
            "commands": [],
            "warnings": plan.get("warnings", []),
            "observation_boundary": {
                "filesystem": "repository, isolated temporary, and isolated home trees are fully manifested",
                "network": "declared network effects are blocked; direct native network absence is not claimed",
                "credentials": "credential-like environment variable names are removed without exposing values",
            },
            "next_command": "No strict replay command was selected.",
        }

    fixture_root = (
        Path(tempfile.mkdtemp(prefix="skills-determinism-check-"))
        if work_dir is None
        else work_dir.resolve(strict=False)
    )
    created_fixture_root = work_dir is None
    if not created_fixture_root:
        try:
            fixture_root.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            return {
                "schema_version": 1,
                "tool": "skill-manager.determinism-check",
                "mode": plan.get("mode", "unknown"),
                "deep": deep,
                "ok": False,
                "status": "fixture-failed",
                "summary": {
                    "module_count": len(plan.get("modules", [])),
                    "command_count": len(planned_commands),
                    "executed_command_count": 0,
                    "executed_replay_count": 0,
                    "passed_count": 0,
                    "blocked_module_count": blocked_module_count,
                    "blocked_placeholder_count": 0,
                    "blocked_environment_count": 0,
                    "blocked_effect_count": 0,
                    "cleanup_failed_count": 0,
                },
                "commands": [],
                "warnings": plan.get("warnings", []),
                "issues": [str(exc)],
                "observation_boundary": {},
                "next_command": "Choose an absent writable temporary fixture root.",
            }

    command_reports: list[dict[str, object]] = []
    executed_replay_count = 0
    root_cleanup_ok = True
    root_cleanup_issue = ""
    use_shared_seed = pair_runner is None
    effective_pair_runner = capture_replay_pair if pair_runner is None else pair_runner
    shared_seed_root = fixture_root / "seed"
    shared_seed_report: dict[str, object] | None = None
    try:
        for index, planned in enumerate(planned_commands, start=1):
            report = dict(planned)
            determinism = (
                planned.get("determinism")
                if isinstance(planned.get("determinism"), dict)
                else _implicit_determinism([])
            )
            report["environment"] = validate_environment_requirements(determinism)
            if planned.get("status") == "blocked-placeholder":
                report["ok"] = False
                report["status"] = "blocked-placeholder"
                report["execution_count"] = 0
                command_reports.append(report)
                continue
            declared_effects = {
                str(value)
                for value in planned.get("declared_effects", [])
                if isinstance(value, str)
            }
            blocked_effects = sorted(declared_effects & BLOCKED_DECLARED_EFFECTS)
            if blocked_effects:
                report["ok"] = False
                report["status"] = "blocked-effects"
                report["blocked_effects"] = blocked_effects
                report["execution_count"] = 0
                command_reports.append(report)
                continue
            if report["environment"].get("ok") is not True:
                report["ok"] = False
                report["status"] = "blocked-environment"
                report["execution_count"] = 0
                command_reports.append(report)
                continue
            command_spec = {
                "id": planned.get("command_id", ""),
                "argv": list(planned.get("argv", [])),
                "timeout_seconds": planned.get("timeout_seconds", 1),
                "working_directory": planned.get("working_directory", "repository"),
                "effects": list(planned.get("declared_effects", [])),
            }
            command_work_dir = fixture_root / f"{index:04d}-{planned.get('module_id', 'module')}-{planned.get('command_id', 'command')}"
            if use_shared_seed:
                if shared_seed_report is None:
                    shared_seed_report = build_isolated_seed(root, shared_seed_root)
                pair = effective_pair_runner(
                    root,
                    str(planned.get("module_path", "")),
                    command_spec,
                    command_work_dir,
                    prepared_seed=shared_seed_root,
                    prepared_seed_report=shared_seed_report,
                )
            else:
                pair = effective_pair_runner(
                    root,
                    str(planned.get("module_path", "")),
                    command_spec,
                    command_work_dir,
                )
            runs = [
                row
                for row in pair.get("runs", [])
                if isinstance(row, dict)
            ] if isinstance(pair, dict) else []
            executed_replay_count += len(runs)
            if len(runs) == 2:
                verdict = compare_replay_captures(runs[0], runs[1], determinism)
            else:
                verdict = {
                    "ok": False,
                    "status": "capture-failed",
                    "repeatable": False,
                    "command_succeeded": False,
                    "mismatches": ["expected-two-replay-captures"],
                    "observed_effects": [],
                    "undeclared_effects": [],
                    "volatile_pointer_applications": {},
                    "generated_artifact_hashes": [],
                }
            cleanup = pair.get("cleanup") if isinstance(pair, dict) and isinstance(pair.get("cleanup"), dict) else {}
            if cleanup.get("ok") is not True:
                verdict = dict(verdict)
                verdict["ok"] = False
                verdict["status"] = "cleanup-failed"
            report.update(
                {
                    key: verdict.get(key)
                    for key in (
                        "ok",
                        "status",
                        "repeatable",
                        "command_succeeded",
                        "mismatches",
                        "observed_effects",
                        "undeclared_effects",
                        "volatile_pointer_applications",
                        "generated_artifact_hashes",
                    )
                }
            )
            report["execution_count"] = len(runs)
            report["replays"] = [_command_capture_summary(row) for row in runs]
            report["fixture_isolation"] = pair.get("fixture_isolation", {}) if isinstance(pair, dict) else {}
            report["cleanup"] = cleanup
            command_reports.append(report)
    finally:
        root_cleanup_ok, root_cleanup_issue = _remove_fixture_root(fixture_root)

    if not root_cleanup_ok:
        command_reports.append(
            {
                "module_id": "<orchestrator>",
                "command_id": "fixture-cleanup",
                "ok": False,
                "status": "cleanup-failed",
                "execution_count": 0,
                "issues": [root_cleanup_issue],
            }
        )

    def count_status(*statuses: str) -> int:
        return sum(1 for row in command_reports if row.get("status") in statuses)

    executed_command_count = sum(1 for row in command_reports if int(row.get("execution_count", 0)) > 0)
    passed_count = count_status("passed")
    blocked_count = count_status("blocked-placeholder", "blocked-environment", "blocked-effects")
    cleanup_failed_count = count_status("cleanup-failed")
    overall_ok = (
        blocked_module_count == 0
        and bool(command_reports)
        and all(row.get("ok") is True for row in command_reports)
    )
    if overall_ok:
        status = "passed"
    elif executed_command_count == 0 and (blocked_count or blocked_module_count):
        status = "blocked"
    else:
        status = "failed"
    first_failure = next((row for row in command_reports if row.get("ok") is not True), None)
    next_command = (
        "No remediation is required; every selected replay passed."
        if overall_ok
        else (
            f"Resolve {first_failure.get('module_id', '')}/{first_failure.get('command_id', '')}: "
            f"{first_failure.get('status', 'failed')}."
            if first_failure
            else "Resolve invalid or missing module declarations before replay."
        )
    )
    return {
        "schema_version": 1,
        "tool": "skill-manager.determinism-check",
        "mode": plan.get("mode", "changed" if changed else "all"),
        "deep": deep,
        "ok": overall_ok,
        "status": status,
        "summary": {
            "module_count": len(plan.get("modules", [])),
            "command_count": len(planned_commands),
            "executed_command_count": executed_command_count,
            "executed_replay_count": executed_replay_count,
            "passed_count": passed_count,
            "blocked_module_count": blocked_module_count,
            "blocked_placeholder_count": count_status("blocked-placeholder"),
            "blocked_environment_count": count_status("blocked-environment"),
            "blocked_effect_count": count_status("blocked-effects"),
            "deterministic_command_failure_count": count_status("deterministic-command-failure"),
            "nondeterministic_count": count_status("nondeterministic"),
            "timeout_count": count_status("timeout"),
            "capture_failed_count": count_status("capture-failed"),
            "undeclared_effect_count": count_status("undeclared-effects"),
            "cleanup_failed_count": cleanup_failed_count,
            "warning_count": len(plan.get("warnings", [])),
        },
        "commands": command_reports,
        "warnings": plan.get("warnings", []),
        "observation_boundary": {
            "filesystem": "repository, isolated temporary, and isolated home trees are fully manifested before and after each replay",
            "network": "declared network effects are blocked; direct native network absence is not claimed",
            "credentials": "credential-like environment variable names are removed without exposing values",
            "external_effects": "declared install, upload, credential, network, repository-write, and external-write commands are blocked before execution",
        },
        "orchestrator_cleanup": {
            "attempted": True,
            "ok": root_cleanup_ok,
            "issue": root_cleanup_issue,
        },
        "next_command": next_command,
    }


def summarize_report(
    report: dict[str, object],
    *,
    compact: bool = False,
) -> dict[str, object]:
    commands = report.get("commands") if isinstance(report.get("commands"), list) else []
    selected_commands = [
        row
        for row in commands
        if isinstance(row, dict) and (not compact or row.get("ok") is not True)
    ]
    return {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.determinism-check"),
        "mode": report.get("mode", "unknown"),
        "deep": bool(report.get("deep")),
        "ok": report.get("ok") is True,
        "status": report.get("status", "unknown"),
        "summary": report.get("summary", {}),
        "commands": selected_commands,
        "warnings": report.get("warnings", []),
        "observation_boundary": report.get("observation_boundary", {}),
        "next_command": report.get("next_command", ""),
    }


def render_markdown(report: dict[str, object]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Determinism Check",
        "",
        f"- Status: `{report.get('status', 'unknown')}`",
        f"- Mode: `{report.get('mode', 'unknown')}`",
        f"- Deep: {bool(report.get('deep'))}",
        f"- Modules: {summary.get('module_count', 0)}",
        f"- Commands: {summary.get('command_count', 0)}",
        f"- Replays executed: {summary.get('executed_replay_count', 0)}",
        f"- Passed: {summary.get('passed_count', 0)}",
    ]
    commands = report.get("commands") if isinstance(report.get("commands"), list) else []
    if commands:
        lines.extend(["", "## Commands", ""])
        for row in commands:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- `{row.get('module_id', '')}/{row.get('command_id', '')}`: "
                f"{row.get('status', 'unknown')}"
            )
            for mismatch in row.get("mismatches", []) if isinstance(row.get("mismatches"), list) else []:
                lines.append(f"  - mismatch: {mismatch}")
    warnings = report.get("warnings") if isinstance(report.get("warnings"), list) else []
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.extend(["", f"- Next action: {report.get('next_command', '')}", ""])
    return "\n".join(lines)


def determinism_check_command(args: object, root: Path) -> int:
    report = run_determinism_check(
        root,
        changed=bool(getattr(args, "changed", False)),
        all_modules=bool(getattr(args, "all", False)),
        deep=bool(getattr(args, "deep", False)),
    )
    if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
        report = summarize_report(
            report,
            compact=bool(getattr(args, "compact", False)),
        )
    if getattr(args, "output_format", "markdown") == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report.get("ok") is True else 1
