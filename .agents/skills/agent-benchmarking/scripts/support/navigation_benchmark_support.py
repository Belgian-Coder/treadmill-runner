#!/usr/bin/env python3
"""Support helpers for navigation and tool-use benchmark runs."""

from __future__ import annotations

import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

STATUS_LABELS = {
    "A": "added",
    "C": "copied",
    "D": "deleted",
    "M": "modified",
    "R": "renamed",
    "T": "type_changed",
    "U": "unmerged",
    "X": "unknown",
}


def run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise RuntimeError(detail)
    return completed.stdout.replace("\r\n", "\n")


def status_label(raw_status: str) -> str:
    prefix = (raw_status or "X")[0].upper()
    return STATUS_LABELS.get(prefix, "unknown")


def path_area(path: str) -> str:
    parts = Path(path).parts
    return parts[0] if len(parts) > 1 else "[root]"


def parse_name_status(output: str) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        raw_status = parts[0]
        label = status_label(raw_status)
        if label in {"renamed", "copied"} and len(parts) >= 3:
            files.append(
                {
                    "status": label,
                    "raw_status": raw_status,
                    "path": parts[2],
                    "old_path": parts[1],
                }
            )
        elif len(parts) >= 2:
            files.append({"status": label, "raw_status": raw_status, "path": parts[1]})
    return files


def summarize_commit_range(repo: Path, base_ref: str, head_ref: str) -> dict[str, Any]:
    repo = repo.expanduser().resolve()
    name_status = run_git(repo, "diff", "--name-status", "--find-renames", base_ref, head_ref)
    shortstat = run_git(repo, "diff", "--shortstat", base_ref, head_ref).strip()
    log_output = run_git(repo, "log", "--format=%H%x09%s", f"{base_ref}..{head_ref}")
    files = parse_name_status(name_status)

    counts = Counter(item["status"] for item in files)
    files_by_status: dict[str, list[str]] = defaultdict(list)
    for item in files:
        files_by_status[item["status"]].append(item["path"])

    commits = []
    for line in log_output.splitlines():
        if not line.strip():
            continue
        sha, _, subject = line.partition("\t")
        commits.append({"sha": sha, "subject": subject})

    areas = Counter(path_area(item["path"]) for item in files)
    status_parts = [f"{name}: {counts[name]}" for name in sorted(counts) if counts[name]]
    area_text = ", ".join(f"{area} ({count})" for area, count in sorted(areas.items()))
    summary = (
        f"{len(commits)} commit(s) changed {len(files)} file(s)"
        + (f" ({', '.join(status_parts)})" if status_parts else "")
        + (f" across {area_text}." if area_text else ".")
    )

    return {
        "schema_version": 1,
        "tool": "agent-benchmarking",
        "ok": True,
        "repo": str(repo),
        "base_ref": base_ref,
        "head_ref": head_ref,
        "commit_count": len(commits),
        "commits": commits,
        "changed_file_count": len(files),
        "changed_files": files,
        "files_by_status": {key: sorted(value) for key, value in sorted(files_by_status.items())},
        "status_counts": {key: counts.get(key, 0) for key in sorted(set(STATUS_LABELS.values()) | set(counts))},
        "areas": dict(sorted(areas.items())),
        "shortstat": shortstat,
        "summary": summary,
    }
