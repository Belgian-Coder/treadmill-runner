"""Git changed-file discovery helpers for changed-scope checks."""

from __future__ import annotations

import subprocess
from pathlib import Path

from repo_support import repo_common as repo


def changed_files(root: Path) -> list[str]:
    return sorted(changed_file_statuses(root))


def _changed_file_statuses_fallback(root: Path) -> dict[str, set[str]]:
    statuses: dict[str, set[str]] = {}
    for args in [
        ("diff", "--name-status"),
        ("diff", "--cached", "--name-status"),
    ]:
        status, lines = repo.git_output(root, *args)
        if status != 0:
            continue
        for line in lines:
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            marker = parts[0].strip() or "M"
            path = parts[-1].replace("\\", "/")
            if path.startswith(repo.DEFAULT_CHANGED_IGNORE_PREFIXES):
                continue
            statuses.setdefault(path, set()).add(marker[0])
    status, lines = repo.git_output(root, "ls-files", "--others", "--exclude-standard")
    if status == 0:
        for path in lines:
            value = path.replace("\\", "/")
            if value.startswith(repo.DEFAULT_CHANGED_IGNORE_PREFIXES):
                continue
            statuses.setdefault(value, set()).add("?")
    return statuses


def changed_file_statuses(root: Path) -> dict[str, set[str]]:
    statuses: dict[str, set[str]] = {}
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return _changed_file_statuses_fallback(root)
    if completed.returncode != 0:
        return _changed_file_statuses_fallback(root)
    entries = completed.stdout.decode("utf-8", errors="replace").split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        if len(entry) < 4:
            continue
        marker = entry[:2]
        path = entry[3:].replace("\\", "/")
        if not path or path.startswith(repo.DEFAULT_CHANGED_IGNORE_PREFIXES):
            if marker[0] in {"R", "C"} and index < len(entries):
                index += 1
            continue
        path_statuses = statuses.setdefault(path, set())
        for value in marker:
            if value == " ":
                continue
            path_statuses.add(value)
        if marker[0] in {"R", "C"} and index < len(entries):
            index += 1
    return statuses
