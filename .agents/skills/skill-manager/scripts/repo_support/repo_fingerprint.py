"""Changed-input fingerprint helpers for validation receipts."""

from __future__ import annotations

import fnmatch
import hashlib
import platform
import sys
from pathlib import Path
from typing import Any

from repo_support import repo_common as repo


FINGERPRINT_STREAM_CHUNK_BYTES = 1_048_576
FINGERPRINT_SKIP_PATTERNS = (
    ".git/*",
    ".agents/local-ai/cache/*",
    ".agents/local-ai/bundle/models/*",
    ".agents/local-ai/bundle/runtimes/*",
    ".agents/tools/cache/*",
    "temp/*",
)
FINGERPRINT_STALE_INPUT_PATTERNS = (
    "*.lock",
    "Cargo.toml",
    "Gemfile.lock",
    "Pipfile.lock",
    "db/migrations/*",
    "db/migrations/**/*",
    "go.mod",
    "go.sum",
    "migrations/*",
    "migrations/**/*",
    "package-lock.json",
    "package.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pyproject.toml",
    "requirements*.txt",
    "schema.sql",
    "*.schema.json",
    ".env.example",
    ".env.sample",
)


def runtime_fingerprint_report() -> dict[str, str]:
    try:
        executable = str(Path(sys.executable).resolve(strict=False))
    except OSError:
        executable = str(sys.executable)
    return {
        "python_executable": executable,
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "platform_machine": platform.machine(),
        "platform_release": getattr(platform, "release")(),
        "platform_system": platform.system(),
    }


def fingerprint_excluded(path: str) -> bool:
    value = path.replace("\\", "/")
    return any(fnmatch.fnmatch(value, pattern) for pattern in FINGERPRINT_SKIP_PATTERNS)


def fingerprint_stale_input_paths(root: Path, changed: list[str]) -> list[str]:
    selected = {path.replace("\\", "/") for path in changed}
    status, tracked = repo.git_output(root, "ls-files")
    if status == 0:
        for path in tracked:
            value = path.replace("\\", "/")
            if any(fnmatch.fnmatch(value, pattern) for pattern in FINGERPRINT_STALE_INPUT_PATTERNS):
                selected.add(value)
    return sorted(path for path in selected if not fingerprint_excluded(path))


def input_fingerprint_report(root: Path, changed: list[str], validation_plan: list[dict[str, Any]]) -> dict[str, Any]:
    digest = hashlib.sha256()
    runtime = runtime_fingerprint_report()
    status, head_lines = repo.git_output(root, "rev-parse", "HEAD")
    head = head_lines[0] if status == 0 and head_lines else "no-git-head"
    commands = [str(item.get("command", "")) for item in validation_plan if isinstance(item, dict) and item.get("command")]
    paths = fingerprint_stale_input_paths(root, changed)
    skipped: list[dict[str, str]] = []
    hashed_paths: list[dict[str, Any]] = []

    for key, value in sorted(runtime.items()):
        digest.update(f"runtime:{key}:{value}\0".encode("utf-8"))
    digest.update(f"head:{head}\0".encode("utf-8"))
    for command in commands:
        digest.update(f"command:{command}\0".encode("utf-8"))
    for rel_path in paths:
        digest.update(f"path:{rel_path}\0".encode("utf-8"))
        path = root / rel_path
        if not path.is_file():
            digest.update(b"<missing>\0")
            skipped.append({"path": rel_path, "reason": "missing"})
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            digest.update(f"<stat-error:{exc.__class__.__name__}>\0".encode("utf-8"))
            skipped.append({"path": rel_path, "reason": f"stat failed: {exc.__class__.__name__}"})
            continue
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(FINGERPRINT_STREAM_CHUNK_BYTES):
                    digest.update(chunk)
        except OSError as exc:
            digest.update(f"<read-error:{exc.__class__.__name__}>\0".encode("utf-8"))
            skipped.append({"path": rel_path, "reason": f"read failed: {exc.__class__.__name__}"})
            continue
        digest.update(b"\0")
        hashed_paths.append({"path": rel_path, "size_bytes": size})

    return {
        "schema_version": 1,
        "tool": "skill-manager.input-fingerprint",
        "algorithm": "sha256",
        "digest": digest.hexdigest(),
        "head": head,
        "runtime": runtime,
        "changed_file_count": len(changed),
        "hashed_path_count": len(hashed_paths),
        "command_count": len(commands),
        "stale_input_count": len(paths),
        "fingerprint_inputs": {
            "changed_files": [path.replace("\\", "/") for path in changed],
            "stale_inputs": paths,
            "commands": commands,
        },
        "hashed_paths": hashed_paths,
        "skipped_fingerprint_paths": skipped,
        "stale_if": [
            "Python executable, version, implementation, or operating-system identity changes",
            "HEAD/base changes",
            "changed file content changes",
            "planned validation commands change",
            "dependency lock/config/schema/migration/env-contract files change",
            "fingerprint inputs become readable or unreadable",
        ],
    }


def summarize_input_fingerprint(report: dict[str, Any]) -> dict[str, Any]:
    if not report:
        return {}
    skipped = report.get("skipped_fingerprint_paths") if isinstance(report.get("skipped_fingerprint_paths"), list) else []
    return {
        "digest": report.get("digest", ""),
        "algorithm": report.get("algorithm", "sha256"),
        "runtime": report.get("runtime", {}),
        "changed_file_count": report.get("changed_file_count", 0),
        "hashed_path_count": report.get("hashed_path_count", 0),
        "command_count": report.get("command_count", 0),
        "skipped_count": len(skipped),
        "stale_if": report.get("stale_if", []),
    }
