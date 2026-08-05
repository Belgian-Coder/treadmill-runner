"""Compact navigation-map freshness status for low-context command packets."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


HANDOFF_REL = "automations/navigation/artifacts/maps/HANDOFF.md"
TOOL_ONLY_NAVIGATION_JSON_RELS = (
    "automations/navigation/artifacts/maps/handoff.json",
    "automations/navigation/artifacts/maps/staleness.json",
    "automations/navigation/artifacts/maps/project-map.json",
    "automations/navigation/artifacts/maps/code-graph.json",
)
REQUIRED_TOOL_ONLY_NAVIGATION_JSON_RELS = (
    "automations/navigation/artifacts/maps/handoff.json",
    "automations/navigation/artifacts/maps/staleness.json",
)
NAVIGATION_OUTPUT_RELS = (
    HANDOFF_REL,
    "automations/navigation/artifacts/maps/NAVIGATION.md",
    "automations/navigation/artifacts/maps/TECHNICAL_CONTEXT.md",
    "automations/navigation/artifacts/maps/CONVENTIONS.md",
    *REQUIRED_TOOL_ONLY_NAVIGATION_JSON_RELS,
)
SETUP_COMMAND = "python -B .agents/manage.py setup"
CHECK_COMMAND = "python -B .agents/skills/repo-navigation/scripts/repo_navigation.py check --target . --format json"
UPDATE_COMMAND = "python -B .agents/skills/repo-navigation/scripts/repo_navigation.py update --target . --write --format json"
READ_ONLY_NAVIGATION_NEXT_STEP = (
    "Read AGENTS.md and automations/navigation/artifacts/maps/HANDOFF.md; "
    "refresh navigation only when writes are allowed."
)
NAVIGATION_OUTPUT_SET = set(NAVIGATION_OUTPUT_RELS)
STALENESS_REL = "automations/navigation/artifacts/maps/staleness.json"
OWNER_CAPSULE_PREFIX = "automations/navigation/artifacts/maps/owners/"
SOURCE_HASH_KIND = "sha256-text-lf-or-raw-v2"
SOURCE_GIT_TREE_KIND = "git-filtered-working-sources-v3"
SOURCE_MAX_FILE_BYTES = 8 * 1024 * 1024
SOURCE_IGNORED_DIRS = {
    ".cache", ".git", ".hg", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".svn", ".venv",
    "__pycache__", "bin", "build", "coverage", "dist", "node_modules", "obj", "out", "temp", "tmp", "venv",
}
SOURCE_IGNORED_PREFIXES = {
    ".superpowers",
    ".agents/.deps",
    ".agents/local-ai/bundle",
    ".agents/local-ai/cache",
    ".agents/local-ai/downloads",
    ".agents/tools/cache",
    "automations/navigation/artifacts/maps",
    "automations/navigation/runs",
    "docs/project/validation/evidence",
}
SOURCE_IGNORED_FILES = {
    ".agents/local-ai/local.settings.json",
    ".agents/local-ai/secrets.local.json",
}
SOURCE_TEXT_SUFFIXES = {
    ".cs", ".css", ".editorconfig", ".html", ".js", ".json", ".md", ".props", ".py", ".targets",
    ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
}


def is_known_navigation_output(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized in NAVIGATION_OUTPUT_SET:
        return True
    if not normalized.startswith(OWNER_CAPSULE_PREFIX) or not normalized.endswith(".md"):
        return False
    suffix = normalized[len(OWNER_CAPSULE_PREFIX) :]
    return bool(suffix) and "/" not in suffix and "\\" not in suffix


def missing_navigation_outputs(root: Path) -> list[str]:
    return [relative for relative in NAVIGATION_OUTPUT_RELS if not (root / relative).exists()]


def _read_first(root: Path) -> str:
    return HANDOFF_REL if (root / HANDOFF_REL).is_file() else ""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_navigation_source_path(root: Path, relative: str) -> bool:
    normalized = relative.replace("\\", "/")
    parts = Path(normalized).parts
    if any(part in SOURCE_IGNORED_DIRS for part in parts):
        return False
    if len(parts) >= 3 and parts[0] == "automations" and parts[2] == "runs":
        return False
    if normalized in SOURCE_IGNORED_FILES:
        return False
    if normalized.startswith(".agents/skills/") and "/fixtures/" in normalized:
        return False
    if any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in SOURCE_IGNORED_PREFIXES):
        return False
    path = root / normalized
    try:
        if not path.is_file() or path.stat().st_size > SOURCE_MAX_FILE_BYTES:
            return False
        if path.suffix.lower() not in SOURCE_TEXT_SUFFIXES:
            with path.open("rb") as handle:
                if b"\0" in handle.read(4096):
                    return False
    except OSError:
        return False
    return True


def git_changed_paths(root: Path) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", "status", "--short"],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    paths: list[str] = []
    for line in completed.stdout.splitlines():
        if len(line) < 4:
            continue
        value = line[3:].strip().replace("\\", "/")
        if " -> " in value:
            value = value.split(" -> ", 1)[1].strip()
        if value:
            paths.append(value)
    return sorted(dict.fromkeys(paths))


def porcelain_changed_paths(payload: bytes | str) -> list[str]:
    """Decode `git status --porcelain=v1 -z` without quoted-path ambiguity."""

    data = payload if isinstance(payload, bytes) else payload.encode("utf-8", errors="surrogateescape")
    records = data.split(b"\0")
    paths: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4 or record[2:3] != b" ":
            continue
        status = record[:2].decode("ascii", errors="replace")
        raw_path = record[3:]
        if raw_path:
            paths.append(raw_path.decode("utf-8", errors="surrogateescape").replace("\\", "/"))
        if any(marker in status for marker in ("R", "C")) and index < len(records):
            original = records[index]
            index += 1
            if original:
                paths.append(original.decode("utf-8", errors="surrogateescape").replace("\\", "/"))
    return sorted(dict.fromkeys(paths))


def git_tree_state(root: Path) -> dict[str, Any]:
    try:
        status = subprocess.run(
            ["git", "--no-optional-locks", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "clean": False, "tree_hash": ""}
    if status.returncode != 0:
        return {"available": False, "clean": False, "tree_hash": ""}
    changed_paths = porcelain_changed_paths(status.stdout)
    if status.stdout:
        return {
            "available": True,
            "clean": False,
            "tree_hash": "",
            "changed_paths": changed_paths,
        }
    try:
        tree = subprocess.run(
            ["git", "--no-optional-locks", "ls-files", "--stage", "-z"],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "clean": False, "tree_hash": ""}
    digest = hashlib.sha256()
    entries: list[tuple[str, str]] = []
    if tree.returncode == 0:
        for entry in tree.stdout.split(b"\0"):
            if not entry:
                continue
            metadata, _, raw_path = entry.partition(b"\t")
            path = raw_path.decode("utf-8", errors="surrogateescape").replace("\\", "/")
            if not is_navigation_source_path(root, path):
                continue
            fields = metadata.decode("ascii", errors="replace").split()
            if len(fields) != 3 or fields[2] != "0":
                return {"available": False, "clean": False, "tree_hash": ""}
            entries.append((path, fields[1]))
        for path, object_id in sorted(entries):
            digest.update(path.encode("utf-8", errors="surrogateescape"))
            digest.update(b"\0")
            digest.update(object_id.encode("ascii"))
            digest.update(b"\0")
    tree_hash = digest.hexdigest() if tree.returncode == 0 else ""
    available = bool(tree_hash)
    return {
        "available": available,
        "clean": available,
        "tree_hash": tree_hash,
        "changed_paths": changed_paths,
    }


def utf8_text_content(content: bytes) -> bool:
    if b"\0" in content:
        return False
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return not any(
        (ord(character) < 32 and character not in "\t\n\r\f")
        or 127 <= ord(character) < 160
        for character in text
    )


def git_text_attributes(root: Path, paths: list[str]) -> dict[str, str] | None:
    if not paths:
        return {}
    encoded = b"".join(path.encode("utf-8", errors="surrogateescape") + b"\0" for path in paths)
    try:
        completed = subprocess.run(
            ["git", "--no-optional-locks", "check-attr", "-z", "--stdin", "text"],
            cwd=root,
            check=False,
            input=encoded,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        completed = None
    if completed is None or completed.returncode != 0:
        return None
    fields = completed.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 3:
        return None
    attributes: dict[str, str] = {}
    for index in range(0, len(fields), 3):
        path = fields[index].decode("utf-8", errors="surrogateescape").replace("\\", "/")
        attribute = fields[index + 1].decode("ascii", errors="replace")
        value = fields[index + 2].decode("ascii", errors="replace")
        if attribute != "text":
            return None
        attributes[path] = value
    return attributes


def cached_source_hash(path: Path, *, text_attribute: str) -> str:
    content = path.read_bytes()
    if text_attribute not in {"unset", "unknown"} and utf8_text_content(content):
        content = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(content).hexdigest()


def dirty_sources_match_cache(
    root: Path,
    changed_paths: list[str],
    source_hashes: dict[str, Any],
) -> bool | None:
    current_sources: list[str] = []
    for relative in changed_paths:
        normalized = relative.replace("\\", "/")
        if is_known_navigation_output(normalized):
            continue
        path = root / normalized
        if not path.exists():
            if normalized in source_hashes:
                return False
            continue
        if not is_navigation_source_path(root, normalized):
            if normalized in source_hashes:
                return False
            continue
        expected = source_hashes.get(normalized)
        if not isinstance(expected, str) or not expected:
            return False
        current_sources.append(normalized)
    attribute_sources: list[str] = []
    for relative in current_sources:
        expected = source_hashes.get(relative)
        try:
            raw_hash = file_sha256(root / relative)
        except OSError:
            raw_hash = ""
        if not raw_hash:
            return None
        if raw_hash != expected:
            attribute_sources.append(relative)
    if not attribute_sources:
        return True
    attributes = git_text_attributes(root, attribute_sources)
    if attributes is None:
        return None
    for relative in attribute_sources:
        try:
            actual = cached_source_hash(
                root / relative,
                text_attribute=attributes.get(relative, "unknown"),
            )
        except OSError:
            actual = ""
        if not actual:
            return None
        if actual != source_hashes.get(relative):
            return False
    return True


def working_source_git_tree_hash(root: Path) -> str:
    """Hash current navigation sources with the repo-navigation owner's algorithm."""

    module_path = (
        root
        / ".agents"
        / "skills"
        / "repo-navigation"
        / "scripts"
        / "navigation"
        / "navigation_core.py"
    )
    if not module_path.is_file():
        return ""
    try:
        spec = importlib.util.spec_from_file_location(
            "_skills_harness_repo_navigation_core",
            module_path,
        )
        if spec is None or spec.loader is None:
            return ""
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source_hash = getattr(module, "source_git_tree_hash")
        value = source_hash(root)
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return ""
    return value if isinstance(value, str) else ""


def _read_staleness(root: Path) -> dict[str, Any]:
    try:
        data = json.loads((root / STALENESS_REL).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def likely_route_source(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized in {"AGENTS.md", "README.md", ".agents/routing.md", "automations/routing.md"}:
        return True
    if normalized.endswith(("/SKILL.md", "/WORKFLOW.md", "/module.json")):
        return True
    if normalized.startswith("docs/") and normalized.endswith(".md"):
        return True
    return False


def _status_payload(
    root: Path,
    *,
    status: str,
    stale_output_count: int,
    summary: str,
    next_command: str,
    reason: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "read_first": _read_first(root),
        "next_command": next_command,
        "read_only_next_step": READ_ONLY_NAVIGATION_NEXT_STEP,
        "stale_output_count": stale_output_count,
        "summary": summary,
    }
    if extra:
        payload.update(extra)
    return payload


def navigation_context_trace(navigation: dict[str, Any]) -> dict[str, Any]:
    read_first = str(navigation.get("read_first") or "").strip()
    read_now = ["AGENTS.md"]
    if read_first and read_first not in read_now:
        read_now.append(read_first)
    return {
        "status": str(navigation.get("status") or "unknown"),
        "read_first": read_first,
        "read_now": read_now,
        "skip_raw_json": list(TOOL_ONLY_NAVIGATION_JSON_RELS),
        "reason": str(navigation.get("reason") or ""),
        "next_command": str(navigation.get("next_command") or ""),
        "read_only_next_step": str(navigation.get("read_only_next_step") or READ_ONLY_NAVIGATION_NEXT_STEP),
        "summary": "Read compact Markdown routes first; raw navigation JSON stays tool-only.",
    }


def navigation_status_from_report(root: Path, report: dict[str, Any]) -> dict[str, Any]:
    stale = report.get("stale") if isinstance(report.get("stale"), list) else []
    status = str(report.get("status") or "")
    if stale or status == "stale":
        stale_source_changes = report.get("stale_source_changes")
        if not isinstance(stale_source_changes, dict):
            stale_source_changes = {}
        return _status_payload(
            root,
            status="stale",
            stale_output_count=len(stale),
            summary="Navigation maps are stale; refresh before broad source reads.",
            next_command=UPDATE_COMMAND,
            reason="stale-generated-navigation-output",
            extra={
                "stale_outputs": [str(item).replace("\\", "/") for item in stale[:20]],
                "stale_source_changes": {
                    key: [str(item).replace("\\", "/") for item in value[:20]]
                    for key, value in stale_source_changes.items()
                    if isinstance(value, list)
                },
            },
        )
    if bool(report.get("ok")):
        return _status_payload(
            root,
            status="fresh",
            stale_output_count=0,
            summary="Navigation maps are fresh; read HANDOFF.md for source orientation.",
            next_command="none, navigation maps are fresh",
            reason="fresh-generated-navigation-output",
        )
    return _status_payload(
        root,
        status="blocked",
        stale_output_count=0,
        summary="Navigation freshness check did not return a fresh or stale map state.",
        next_command=CHECK_COMMAND,
        reason="navigation-check-unclassified",
    )


def fast_navigation_status(root: Path) -> dict[str, Any] | None:
    missing = missing_navigation_outputs(root)
    if missing:
        return _status_payload(
            root,
            status="missing",
            stale_output_count=len(missing),
            summary="Navigation maps are missing; initialize before broad source reads.",
            next_command=SETUP_COMMAND,
            reason="missing-generated-navigation-output",
            extra={"missing_outputs": missing[:20]},
        )
    staleness = _read_staleness(root)
    source_hashes = staleness.get("source_hashes")
    if (
        not isinstance(source_hashes, dict)
        or staleness.get("source_hash_kind") != SOURCE_HASH_KIND
        or staleness.get("ok") is not True
    ):
        return None
    map_files = staleness.get("map_files")
    map_hashes = staleness.get("map_hashes")
    if not isinstance(map_files, list) or not isinstance(map_hashes, dict) or not map_hashes:
        return None
    expected_hashed_maps = {str(item).replace("\\", "/") for item in map_files} - {STALENESS_REL}
    if set(map_hashes) != expected_hashed_maps:
        return None
    recorded_owner_paths = {
        path for path in expected_hashed_maps if path.startswith(OWNER_CAPSULE_PREFIX)
    }
    owners_dir = root / OWNER_CAPSULE_PREFIX
    try:
        actual_owner_paths = {
            path.relative_to(root).as_posix()
            for path in owners_dir.glob("*.md")
            if path.is_file()
        }
    except OSError:
        return None
    if actual_owner_paths != recorded_owner_paths:
        return None
    for relative, expected_hash in sorted(map_hashes.items()):
        normalized = str(relative).replace("\\", "/")
        if not is_known_navigation_output(normalized) or not isinstance(expected_hash, str):
            return None
        path = root / normalized
        try:
            if not path.is_file() or file_sha256(path) != expected_hash:
                return None
        except OSError:
            return None
    cached_tree_hash = str(staleness.get("source_git_tree_hash") or "").strip()
    if not cached_tree_hash or staleness.get("source_git_tree_kind") != SOURCE_GIT_TREE_KIND:
        return None
    tree_state = git_tree_state(root)
    if not tree_state.get("available"):
        return None
    if tree_state.get("clean"):
        if tree_state.get("tree_hash") != cached_tree_hash:
            return None
        reason = "fresh-navigation-git-tree-cache"
    else:
        changed_paths = tree_state.get("changed_paths")
        incremental = (
            dirty_sources_match_cache(root, changed_paths, source_hashes)
            if isinstance(changed_paths, list)
            else None
        )
        if incremental is False:
            return None
        if incremental is None:
            if working_source_git_tree_hash(root) != cached_tree_hash:
                return None
            reason = "fresh-navigation-working-source-hash"
        else:
            reason = "fresh-navigation-incremental-source-cache"
    return _status_payload(
        root,
        status="fresh",
        stale_output_count=0,
        summary="Navigation maps are fresh; read HANDOFF.md for source orientation.",
        next_command="none, navigation maps are fresh",
        reason=reason,
    )


def navigation_status(root: Path, *, fast: bool = False) -> dict[str, Any]:
    root = root.resolve()
    if fast:
        quick = fast_navigation_status(root)
        if quick:
            return quick
    missing = missing_navigation_outputs(root)
    if missing:
        return _status_payload(
            root,
            status="missing",
            stale_output_count=len(missing),
            summary="Navigation maps are missing; initialize before broad source reads.",
            next_command=SETUP_COMMAND,
            reason="missing-generated-navigation-output",
            extra={"missing_outputs": missing[:20]},
        )
    script = root / ".agents" / "skills" / "repo-navigation" / "scripts" / "repo_navigation.py"
    if not script.is_file():
        return _status_payload(
            root,
            status="blocked",
            stale_output_count=0,
            summary="repo-navigation command is unavailable, so map freshness could not be checked.",
            next_command=CHECK_COMMAND,
            reason="repo-navigation-command-missing",
        )
    try:
        completed = subprocess.run(
            [sys.executable, "-B", str(script), "check", "--target", str(root), "--format", "json"],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _status_payload(
            root,
            status="blocked",
            stale_output_count=0,
            summary="Navigation freshness check failed to run.",
            next_command=CHECK_COMMAND,
            reason="navigation-check-execution-failed",
        )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return _status_payload(
            root,
            status="blocked",
            stale_output_count=0,
            summary="Navigation freshness check did not return JSON.",
            next_command=CHECK_COMMAND,
            reason="navigation-check-non-json",
        )
    return navigation_status_from_report(root, report)


def repo_navigation_report(root: Path, *args: str, timeout_seconds: int = 60) -> dict[str, Any]:
    script = root / ".agents" / "skills" / "repo-navigation" / "scripts" / "repo_navigation.py"
    if not script.is_file():
        return {
            "ok": False,
            "status": "blocked",
            "issue": f"missing repo-navigation script: {script}",
        }
    try:
        completed = subprocess.run(
            [sys.executable, "-B", str(script), *args],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "status": "blocked",
            "issue": f"repo-navigation timed out after {timeout_seconds}s",
            "output_tail": str(exc.stdout or "")[-2000:],
        }
    except OSError as exc:
        return {
            "ok": False,
            "status": "blocked",
            "issue": str(exc),
        }
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "status": "blocked",
            "issue": "repo-navigation did not return JSON",
            "returncode": completed.returncode,
            "output_tail": (completed.stdout or "")[-2000:],
        }
    if isinstance(report, dict):
        report.setdefault("returncode", completed.returncode)
        return report
    return {
        "ok": False,
        "status": "blocked",
        "issue": "repo-navigation returned non-object JSON",
        "returncode": completed.returncode,
    }


def auto_refresh_navigation(root: Path) -> dict[str, Any]:
    root = root.resolve()
    before = repo_navigation_report(root, "check", "--target", str(root), "--format", "json", timeout_seconds=30)
    status = str(before.get("status") or "")
    stale = [str(item).replace("\\", "/") for item in before.get("stale", []) if str(item).strip()]
    missing = missing_navigation_outputs(root)
    if before.get("ok") and not stale and status != "stale":
        return {
            "schema_version": 1,
            "tool": "repo-navigation.auto-refresh",
            "ok": True,
            "status": "skipped-fresh",
            "written": [],
            "before": navigation_status_from_report(root, before),
            "summary": "Navigation maps were already fresh.",
        }
    if missing:
        return {
            "schema_version": 1,
            "tool": "repo-navigation.auto-refresh",
            "ok": True,
            "status": "skipped-missing",
            "written": [],
            "missing": missing,
            "before": navigation_status(root),
            "summary": "Navigation maps are missing; setup owns initial installation.",
        }
    unsafe_stale = sorted(path for path in stale if not is_known_navigation_output(path))
    if unsafe_stale:
        return {
            "schema_version": 1,
            "tool": "repo-navigation.auto-refresh",
            "ok": False,
            "status": "blocked",
            "written": [],
            "unsafe_stale": unsafe_stale,
            "before": navigation_status_from_report(root, before),
            "summary": "Navigation refresh blocked because stale outputs are outside the known generated map set.",
            "next_command": CHECK_COMMAND,
        }
    if not stale and status != "stale":
        return {
            "schema_version": 1,
            "tool": "repo-navigation.auto-refresh",
            "ok": False,
            "status": "blocked",
            "written": [],
            "before": navigation_status_from_report(root, before),
            "summary": "Navigation check did not report a refreshable stale state.",
            "next_command": CHECK_COMMAND,
        }
    updated = repo_navigation_report(
        root,
        "update",
        "--target",
        str(root),
        "--write",
        "--format",
        "json",
        timeout_seconds=180,
    )
    written = [str(item).replace("\\", "/") for item in updated.get("written", []) if str(item).strip()]
    unsafe_written = sorted(path for path in written if not is_known_navigation_output(path))
    after = navigation_status(root)
    ok = bool(updated.get("ok")) and not unsafe_written and after.get("status") == "fresh"
    return {
        "schema_version": 1,
        "tool": "repo-navigation.auto-refresh",
        "ok": ok,
        "status": "refreshed" if ok else "failed",
        "written": written,
        "unsafe_written": unsafe_written,
        "before": navigation_status_from_report(root, before),
        "after": after,
        "summary": (
            "Navigation maps were refreshed safely."
            if ok
            else "Navigation refresh did not produce a fresh known-output state."
        ),
        "next_command": "none, navigation maps are fresh" if ok else UPDATE_COMMAND,
    }
