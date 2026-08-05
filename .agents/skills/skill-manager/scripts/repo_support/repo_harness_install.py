#!/usr/bin/env python3
"""Copy the reusable harness into a consumer project."""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from repo_support import repo_common
from repo_support import repo_harness_paths
from repo_support import repo_harness_profiles
from repo_support.repo_harness_render import (
    limited_rows,
    print_report,
    render_copy_contract,
    render_markdown,
    render_public_export,
)


ROOT_PAYLOAD_ENTRIES = (
    "AGENTS.md",
    "orchestration.md",
    "README.md",
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    ".aider.conf.yml",
    "GEMINI.md",
    "docs",
    ".agents",
    "automations",
    ".github",
    ".claude",
    ".continue",
)

PAYLOAD_MANIFEST_REL = ".agents/harness-payload.json"
STATUS_FAST_COMMAND = "python -B .agents/manage.py status --fast"
NEXT_ACTION_COMMAND = "python -B .agents/manage.py next-action --summary --compact --format json"
PAYLOAD_MANIFEST_LABEL = "Payload manifest"
SOURCE_ROOT_MISSING = "source root does not exist: "
TARGET_NOT_DIR = "target exists and is not a directory: "
INSTALL_MANIFEST_REL = ".agents/harness.lock.json"
LEGACY_INSTALL_MANIFEST_REL = ".agents/harness-install.json"
PROJECT_OVERLAY_REL = ".agents/harness.overlay.json"
INSTALL_PLAN_JSON_REL = ".agents/harness-install-plan.json"
INSTALL_PLAN_MARKDOWN_REL = ".agents/harness-install-plan.md"
SMOKE_TARGET_MARKER_REL = repo_common.HARNESS_SMOKE_TARGET_MARKER_REL

STATE_EXCLUDE_GLOBS = (
    ".agents/harness.lock.json",
    ".agents/harness-install.json",
    ".agents/harness.overlay.json",
    ".agents/harness-install-plan.json",
    ".agents/harness-install-plan.md",
    SMOKE_TARGET_MARKER_REL,
    ".agents/project-policy.json",
    ".agents/orchestration.json",
    ".agents/local-ai/cache/**",
    ".agents/local-ai/bundle/**",
    ".agents/local-ai/downloads/**",
    ".agents/local-ai/runtime/**",
    ".agents/tools/cache/**",
    ".agents/local-ai/secrets.json",
    ".agents/local-ai/secrets.local.json",
    ".agents/local-ai/local.settings.json",
    ".agents/local-ai/project.settings.json",
    ".agents/.deps/**",
    ".claude/settings.local.json",
    ".github/copilot/settings.local.json",
    "*.local",
    "*.local.*",
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "automations/**/Scripts/output/**",
    "automations/reference-refresh/References/repositories/**",
    "automations/*/runs/**",
    "automations/*/runs",
)

GENERAL_EXCLUDE_GLOBS = (
    ".git/**",
    ".git",
    ".cache/**",
    "**/.cache/**",
    ".vscode/**",
    "**/.vscode/**",
    ".idea/**",
    "**/.idea/**",
    ".mypy_cache/**",
    "**/.mypy_cache/**",
    ".pytest_cache/**",
    "**/.pytest_cache/**",
    ".ruff_cache/**",
    "**/.ruff_cache/**",
    ".venv/**",
    "**/.venv/**",
    "venv/**",
    "**/venv/**",
    "node_modules/**",
    "**/node_modules/**",
    "tmp/**",
    "**/tmp/**",
    "temp/**",
    "**/temp/**",
    "dist/**",
    "**/dist/**",
    "build/**",
    "**/build/**",
    "coverage/**",
    "**/coverage/**",
    "benchmark/**",
    "**/benchmark/**",
    "**/bin/**",
    "**/obj/**",
    "__pycache__/**",
    "**/__pycache__/**",
    "*.pyc",
    "*.pyo",
    "*.log",
    ".DS_Store",
)

PROJECT_LOCAL_EXCLUDE_GLOBS = (
    "docs/project/project-context.md",
    "docs/project/project-context.generated.md",
    "docs/project/project-context.json",
    "docs/project/diagrams/**",
    "docs/project/review/**",
    "docs/project/validation/**",
)

REQUIRED_STATE_EXCLUDES = STATE_EXCLUDE_GLOBS
REQUIRED_GENERAL_EXCLUDES = (*GENERAL_EXCLUDE_GLOBS, *PROJECT_LOCAL_EXCLUDE_GLOBS)
PRESERVE_EXISTING_CONSUMER_PATHS = {"README.md"}
MERGE_EXISTING_CONSUMER_PATHS = {".gitignore"}


def path_key(path: Path) -> str:
    return path.as_posix()


def matches_glob(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(path + "/", pattern)


def normalized_manifest_path(value: object, field: str, issues: list[str]) -> str:
    if not isinstance(value, str):
        issues.append(f"payload manifest {field} entries must be strings")
        return ""
    text = value.replace("\\", "/").strip()
    if not text:
        issues.append(f"payload manifest {field} contains an empty entry")
        return ""
    if (
        text.startswith("/")
        or Path(text).is_absolute()
        or (len(text) >= 2 and text[1] == ":")
        or any(part == ".." for part in text.split("/"))
    ):
        issues.append(f"payload manifest {field} contains an unsafe path: {value}")
        return ""
    return text.rstrip("/")


def normalized_manifest_list(manifest: dict[str, object], field: str, issues: list[str]) -> list[str]:
    values = manifest.get(field)
    if not isinstance(values, list):
        issues.append(f"payload manifest {field} must be a list")
        return []
    normalized: list[str] = []
    for item in values:
        text = normalized_manifest_path(item, field, issues)
        if text:
            normalized.append(text)
    return list(dict.fromkeys(normalized))


def empty_payload_manifest(source: str) -> dict[str, object]:
    return {
        "schema_version": repo_harness_profiles.PAYLOAD_SCHEMA_VERSION,
        "tool": "install-harness-payload",
        "owner": "skill-manager",
        "path": PAYLOAD_MANIFEST_REL,
        "source": source,
        "include_roots": [],
        "exclude_globs": [],
        "state_exclude_globs": [],
        "required_features": [],
        "feature_bundles": {},
        "profiles": {},
    }


def load_payload_manifest(
    source_root: Path,
    *,
    path_guard: repo_harness_paths.HarnessPathGuard | None = None,
    unsafe_paths: list[dict[str, str]] | None = None,
) -> tuple[dict[str, object], list[str]]:
    guard = path_guard or repo_harness_paths.HarnessPathGuard(source_root, label="source")
    try:
        if not guard.exists(PAYLOAD_MANIFEST_REL, operation="payload-manifest-read"):
            return empty_payload_manifest("missing-file"), [
                f"{PAYLOAD_MANIFEST_REL} is required and must use schema_version "
                f"{repo_harness_profiles.PAYLOAD_SCHEMA_VERSION}"
            ]
        manifest_text = guard.read_text(PAYLOAD_MANIFEST_REL, operation="payload-manifest-read")
    except repo_harness_paths.UnsafeHarnessPathError as exc:
        if unsafe_paths is None:
            raise
        repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
        return empty_payload_manifest("unsafe-file"), [f"unsafe-path-blocked: {exc}"]

    issues: list[str] = []
    try:
        payload = json.loads(manifest_text)
    except (OSError, json.JSONDecodeError) as exc:
        return empty_payload_manifest("invalid-file"), [f"{PAYLOAD_MANIFEST_REL} could not be read: {exc}"]
    if not isinstance(payload, dict):
        return empty_payload_manifest("invalid-file"), [f"{PAYLOAD_MANIFEST_REL} must contain a JSON object"]

    include_roots = normalized_manifest_list(payload, "include_roots", issues)
    exclude_globs = normalized_manifest_list(payload, "exclude_globs", issues)
    state_exclude_globs = normalized_manifest_list(payload, "state_exclude_globs", issues)
    if not include_roots:
        issues.append(f"{PAYLOAD_MANIFEST_REL} include_roots must not be empty")

    contract = repo_harness_profiles.normalize_contract(payload, issues)
    manifest = {
        "schema_version": payload.get("schema_version"),
        "tool": str(payload.get("tool") or "install-harness-payload"),
        "owner": str(payload.get("owner") or "skill-manager"),
        "path": PAYLOAD_MANIFEST_REL,
        "source": "file",
        "description": str(payload.get("description") or ""),
        "include_roots": include_roots,
        "exclude_globs": exclude_globs,
        "state_exclude_globs": state_exclude_globs,
        **contract,
    }
    return manifest, issues


def profile_details(
    manifest: dict[str, object],
    profile: str,
    issues: list[str],
    *,
    with_features: list[str] | None = None,
    without_features: list[str] | None = None,
) -> dict[str, object]:
    return repo_harness_profiles.resolve_profile(
        manifest,
        profile,
        with_features=with_features or [],
        without_features=without_features or [],
        issues=issues,
    )


def effective_payload_manifest(
    manifest: dict[str, object],
    profile: str,
    issues: list[str],
    *,
    with_features: list[str] | None = None,
    without_features: list[str] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    selected = profile_details(
        manifest,
        profile,
        issues,
        with_features=with_features,
        without_features=without_features,
    )
    effective = dict(manifest)
    effective["exclude_globs"] = list(manifest.get("exclude_globs", [])) + list(selected.get("exclude_globs", []))
    effective["state_exclude_globs"] = list(manifest.get("state_exclude_globs", [])) + list(selected.get("state_exclude_globs", []))
    effective["selected_profile"] = selected
    return effective, selected


def manifest_list(manifest: dict[str, object] | None, field: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if not manifest:
        return fallback
    value = manifest.get(field)
    if not isinstance(value, list):
        return fallback
    return tuple(str(item) for item in value if str(item))


def is_state_path(path: str, manifest: dict[str, object] | None = None) -> bool:
    patterns = tuple(
        dict.fromkeys(
            (
                *REQUIRED_STATE_EXCLUDES,
                *manifest_list(manifest, "state_exclude_globs", ()),
            )
        )
    )
    return any(matches_glob(path, pattern) for pattern in patterns)


def is_excluded_path(path: str, manifest: dict[str, object] | None = None) -> bool:
    patterns = tuple(
        dict.fromkeys(
            (
                *REQUIRED_GENERAL_EXCLUDES,
                *REQUIRED_STATE_EXCLUDES,
                *manifest_list(manifest, "exclude_globs", ()),
                *manifest_list(manifest, "state_exclude_globs", ()),
            )
        )
    )
    return any(matches_glob(path, pattern) for pattern in patterns)


def iter_payload_candidates(
    source_root: Path,
    manifest: dict[str, object] | None = None,
    *,
    path_guard: repo_harness_paths.HarnessPathGuard | None = None,
    unsafe_paths: list[dict[str, str]] | None = None,
) -> tuple[list[Path], list[str]]:
    guard = path_guard or repo_harness_paths.HarnessPathGuard(source_root, label="source")
    files_by_path: dict[str, Path] = {}
    excluded: list[str] = []
    for entry in manifest_list(manifest, "include_roots", ROOT_PAYLOAD_ENTRIES):
        entry_files, entry_excluded, entry_errors = guard.walk_files(
            entry,
            operation="payload-enumeration",
            excluded=lambda relative: is_excluded_path(relative, manifest),
        )
        excluded.extend(entry_excluded)
        for error in entry_errors:
            if unsafe_paths is None:
                raise error
            repo_harness_paths.add_unsafe_path(unsafe_paths, error)
        for child in entry_files:
            rel = path_key(child.relative_to(guard.root))
            files_by_path[rel] = child
    files = [files_by_path[path] for path in sorted(files_by_path)]
    if manifest and isinstance(manifest.get("selected_profile"), dict):
        selected = repo_harness_profiles.select_files(
            guard.root,
            files,
            manifest,
            manifest["selected_profile"],
        )
        selected_paths = {path_key(path.relative_to(guard.root)) for path in selected}
        excluded.extend(
            path_key(path.relative_to(guard.root))
            for path in files
            if path_key(path.relative_to(guard.root)) not in selected_paths
        )
        files = selected
    return files, sorted(set(excluded))


def resolved_target(args_target: str) -> Path:
    return repo_harness_paths.absolute_path(Path(args_target))


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def source_repository_metadata(source_root: Path) -> dict[str, str]:
    def git_value(*arguments: str) -> str:
        try:
            completed = subprocess.run(
                ["git", "-C", str(source_root), *arguments],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return completed.stdout.strip() if completed.returncode == 0 else ""

    repository = git_value("config", "--get", "remote.origin.url")
    if repository.startswith("git@github.com:"):
        repository = "https://github.com/" + repository.removeprefix("git@github.com:")
    if repository.endswith(".git"):
        repository = repository[:-4]
    commit = git_value("rev-parse", "HEAD")
    tag = git_value("describe", "--tags", "--exact-match", "--match", "v[0-9]*.[0-9]*.[0-9]*")
    if not tag or git_value("status", "--porcelain"):
        tag = ""
        commit = "0" * 40
    return {
        "repository": repository or "local-development-source",
        "tag": tag or "unreleased",
        "commit": commit or "0" * 40,
    }


def read_install_manifest(
    target_root: Path,
    *,
    path_guard: repo_harness_paths.HarnessPathGuard | None = None,
    unsafe_paths: list[dict[str, str]] | None = None,
    manifest_issues: list[str] | None = None,
) -> dict[str, object]:
    guard = path_guard or repo_harness_paths.HarnessPathGuard(target_root, label="target")
    try:
        if not guard.exists(INSTALL_MANIFEST_REL, operation="install-manifest-read"):
            return {}
        manifest_text = guard.read_text(INSTALL_MANIFEST_REL, operation="install-manifest-read")
    except repo_harness_paths.UnsafeHarnessPathError as exc:
        if unsafe_paths is None:
            raise
        repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
        return {}
    except UnicodeError as exc:
        if manifest_issues is not None:
            manifest_issues.append(f"{INSTALL_MANIFEST_REL}: file is not valid UTF-8: {exc}")
        return {}
    try:
        payload = json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        if manifest_issues is not None:
            manifest_issues.append(f"{INSTALL_MANIFEST_REL}: file is not valid JSON: {exc}")
        return {}
    if not isinstance(payload, dict):
        if manifest_issues is not None:
            manifest_issues.append(f"{INSTALL_MANIFEST_REL}: top-level value must be an object")
        return {}
    if not isinstance(payload.get("files"), list):
        if manifest_issues is not None:
            manifest_issues.append(f"{INSTALL_MANIFEST_REL}: files must be present as a list")
    return payload


def validated_manifest_rows(
    manifest: dict[str, object],
    target_guard: repo_harness_paths.HarnessPathGuard,
    unsafe_paths: list[dict[str, str]],
    manifest_issues: list[str] | None = None,
) -> dict[str, dict[str, object]]:
    files = manifest.get("files")
    if not isinstance(files, list):
        return {}
    rows: dict[str, dict[str, object]] = {}
    seen_paths: set[str] = set()
    validation_issues = manifest_issues if manifest_issues is not None else []
    for index, row in enumerate(files):
        if not isinstance(row, dict):
            validation_issues.append(f"{INSTALL_MANIFEST_REL}: files[{index}] must be an object")
            continue
        raw_path = row.get("path")
        try:
            normalized = repo_harness_paths.normalize_relative_path(raw_path)
            target_guard.check(normalized, operation="install-manifest-owned-path")
        except (ValueError, repo_harness_paths.UnsafeHarnessPathError) as exc:
            error = (
                exc
                if isinstance(exc, repo_harness_paths.UnsafeHarnessPathError)
                else repo_harness_paths.UnsafeHarnessPathError(
                    path=str(raw_path),
                    root=str(target_guard.root),
                    operation="install-manifest-owned-path",
                    reason=str(exc),
                )
            )
            repo_harness_paths.add_unsafe_path(unsafe_paths, error)
            continue
        if normalized in seen_paths:
            validation_issues.append(f"{normalized}: duplicate path in install manifest")
            continue
        seen_paths.add(normalized)
        digest = row.get("sha256")
        byte_count = row.get("bytes")
        row_is_valid = True
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            validation_issues.append(f"{normalized}: sha256 must be 64 lowercase hexadecimal characters")
            row_is_valid = False
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
            validation_issues.append(f"{normalized}: bytes must be a non-negative integer")
            row_is_valid = False
        if not row_is_valid:
            continue
        normalized_row = dict(row)
        normalized_row["path"] = normalized
        rows[normalized] = normalized_row
    return rows


def manifest_hashes(manifest: dict[str, object]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path, row in manifest_rows(manifest).items():
        digest = str(row.get("sha256", "")).strip()
        if path and digest:
            hashes[path] = digest
    return hashes


def manifest_rows(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    files = manifest.get("files")
    if not isinstance(files, list):
        return {}
    rows: dict[str, dict[str, object]] = {}
    for row in files:
        if not isinstance(row, dict):
            continue
        try:
            relative = repo_harness_paths.normalize_relative_path(row.get("path"))
        except ValueError:
            continue
        normalized_row = dict(row)
        normalized_row["path"] = relative
        rows.setdefault(relative, normalized_row)
    return rows


def write_install_manifest(
    source_root: Path,
    target_root: Path,
    files: list[Path],
    *,
    omit_paths: set[str] | None = None,
    target_hash_paths: set[str] | None = None,
    selected_profile: dict[str, object] | None = None,
    resolved_file_manifest: list[dict[str, object]] | None = None,
    resolved_manifest_digest: str = "",
    retained_rows: dict[str, dict[str, object]] | None = None,
    source_metadata: dict[str, str] | None = None,
    source_guard: repo_harness_paths.HarnessPathGuard | None = None,
    target_guard: repo_harness_paths.HarnessPathGuard | None = None,
) -> Path:
    source_guard = source_guard or repo_harness_paths.HarnessPathGuard(source_root, label="source")
    target_guard = target_guard or repo_harness_paths.HarnessPathGuard(target_root, label="target")
    omit_paths = omit_paths or set()
    target_hash_paths = target_hash_paths or set()
    rows = []
    for source_file in sorted(files, key=lambda item: path_key(item.relative_to(source_root))):
        rel = path_key(source_file.relative_to(source_root))
        if rel in omit_paths:
            continue
        use_target = rel in target_hash_paths and target_guard.is_file(rel, operation="install-manifest-target-stat")
        hash_guard = target_guard if use_target else source_guard
        rows.append(
            {
                "path": rel,
                "bytes": hash_guard.stat_size(rel, operation="install-manifest-file-stat"),
                "sha256": hash_guard.sha256(rel, operation="install-manifest-file-hash"),
            }
        )
    selected_paths = {str(row.get("path", "")) for row in rows}
    for path, previous in sorted((retained_rows or {}).items()):
        if path in selected_paths or not target_guard.is_file(path, operation="install-manifest-retained-stat"):
            continue
        row = dict(previous)
        row["path"] = path
        row["selection"] = "retained-from-previous-profile"
        rows.append(row)
    rows.sort(key=lambda row: str(row.get("path", "")))
    profile = dict(selected_profile or {})
    source = dict(source_metadata or source_repository_metadata(source_root))
    payload = {
        "schema_version": 1,
        "tool": "harness-lock",
        "repository": str(source.get("repository", "")),
        "tag": str(source.get("tag", "")),
        "commit": str(source.get("commit", "")),
        "install": {
            "profile": str(profile.get("name", "standard")),
            "features": sorted(str(item) for item in profile.get("features", []) if str(item)),
        },
        "payload_digest": resolved_manifest_digest,
        "files": rows,
    }
    return target_guard.write_text(
        INSTALL_MANIFEST_REL,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        operation="install-manifest-write",
    )


def merged_text_with_missing_lines(existing_text: str, source_text: str) -> tuple[str, list[str]]:
    existing_normalized = existing_text.replace("\r\n", "\n").replace("\r", "\n")
    source_lines = source_text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    existing_keys = {line.strip() for line in existing_normalized.splitlines() if line.strip()}
    missing = [line for line in source_lines if line.strip() and line.strip() not in existing_keys]
    if not missing:
        text = existing_normalized
        return text if text.endswith("\n") else text + "\n", []
    merged = [
        existing_normalized.rstrip("\n"),
        "",
        "# Reusable AI harness",
        *missing,
    ]
    return "\n".join(merged).rstrip("\n") + "\n", missing


def build_install_plan(
    *,
    source_root: Path,
    target_root: Path,
    operation: str,
    dry_run: bool,
    force: bool,
    payload_manifest: dict[str, object],
    planned: list[dict[str, object]],
    already_present: list[str],
    preserved_existing: list[str],
    merged: list[dict[str, object]],
    collisions: list[dict[str, object]],
    excluded: list[str],
    issues: list[str],
    planned_post_install: list[dict[str, object]],
    selected_profile: dict[str, object],
    resolved_file_manifest: list[dict[str, object]],
    resolved_manifest_digest: str,
    retained_previous_profile_files: list[str],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "tool": "install-harness-plan",
        "generated_at": now_utc(),
        "operation": operation,
        "dry_run": dry_run,
        "force": force,
        "source_root": str(source_root),
        "target_root": str(target_root),
        "profile": selected_profile,
        "resolved_features": list(selected_profile.get("features", [])),
        "resolved_file_manifest": resolved_file_manifest,
        "resolved_manifest_digest": resolved_manifest_digest,
        "retained_previous_profile_files": retained_previous_profile_files,
        "payload_manifest": {
            "path": payload_manifest.get("path", PAYLOAD_MANIFEST_REL),
            "source": payload_manifest.get("source", ""),
            "include_roots": payload_manifest.get("include_roots", []),
            "exclude_globs": payload_manifest.get("exclude_globs", []),
            "state_exclude_globs": payload_manifest.get("state_exclude_globs", []),
        },
        "summary": {
            "planned_files": len(planned),
            "already_present_files": len(already_present),
            "preserved_existing_files": len(preserved_existing),
            "merged_files": len(merged),
            "collision_files": len(collisions),
            "excluded_files": len(excluded),
            "post_install_steps": len(planned_post_install),
        },
        "proposed_writes": [str(item.get("path", "")) for item in planned if isinstance(item, dict)],
        "already_present": sorted(already_present),
        "preserved_existing": sorted(preserved_existing),
        "merged": merged,
        "collisions": collisions,
        "excluded": excluded,
        "issues": issues,
        "post_install_commands": [
            {"name": item.get("name", ""), "command": command_display([str(part) for part in item.get("args", [])])}
            for item in planned_post_install
        ],
        "validation_commands": [
            "python -B .agents/manage.py setup --no-link-skills",
            "python -B .agents/manage.py setup --check --no-link-skills",
            STATUS_FAST_COMMAND,
            NEXT_ACTION_COMMAND,
        ],
        "rollback_notes": [
            "Review `.agents/harness.lock.json` for copied file hashes.",
            "When the target is under version control, revert the copied harness files with the target repository tools.",
            "For an unversioned target, remove copied files listed in `.agents/harness.lock.json` after confirming they are not consumer-owned edits.",
        ],
    }


def render_install_plan_markdown(plan: dict[str, object]) -> str:
    summary = plan.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    lines = [
        "# Harness Install Plan",
        "",
        f"- Operation: {plan.get('operation')}",
        f"- Mode: {'dry-run' if plan.get('dry_run') else 'write'}",
        f"- Source: `{plan.get('source_root')}`",
        f"- Target: `{plan.get('target_root')}`",
        f"- Profile: `{(plan.get('profile') or {}).get('name', 'standard') if isinstance(plan.get('profile'), dict) else 'standard'}`",
        f"- Resolved features: {', '.join(str(item) for item in plan.get('resolved_features', [])) or 'none'}",
        f"- Resolved source manifest: {len(plan.get('resolved_file_manifest', [])) if isinstance(plan.get('resolved_file_manifest'), list) else 0} file(s)",
        f"- Resolved manifest SHA-256: `{plan.get('resolved_manifest_digest', '')}`",
        f"- Planned files: {summary.get('planned_files', 0)}",
        f"- Collisions: {summary.get('collision_files', 0)}",
        f"- Excluded files: {summary.get('excluded_files', 0)}",
    ]
    payload_manifest = plan.get("payload_manifest", {})
    if isinstance(payload_manifest, dict):
        lines.extend(
            [
                f"- {PAYLOAD_MANIFEST_LABEL}: `{payload_manifest.get('path', PAYLOAD_MANIFEST_REL)}` ({payload_manifest.get('source', '')})",
            ]
        )

    proposed_writes = plan.get("proposed_writes", [])
    if isinstance(proposed_writes, list) and proposed_writes:
        rows, remaining = limited_rows(proposed_writes)
        lines.extend(["", "## Proposed Writes", ""])
        for row in rows:
            lines.append(f"- `{row}`")
        if remaining:
            lines.append(f"- ... {remaining} more")

    preserved = plan.get("preserved_existing", [])
    if isinstance(preserved, list) and preserved:
        rows, remaining = limited_rows(preserved)
        lines.extend(["", "## Preserved Consumer Files", ""])
        for row in rows:
            lines.append(f"- `{row}`")
        if remaining:
            lines.append(f"- ... {remaining} more")

    retained = plan.get("retained_previous_profile_files", [])
    if isinstance(retained, list) and retained:
        rows, remaining = limited_rows(retained)
        lines.extend(["", "## Retained From Previous Profile", ""])
        for row in rows:
            lines.append(f"- `{row}`")
        if remaining:
            lines.append(f"- ... {remaining} more")

    merged = plan.get("merged", [])
    if isinstance(merged, list) and merged:
        rows, remaining = limited_rows(merged)
        lines.extend(["", "## Merged Consumer Files", ""])
        for row in rows:
            if isinstance(row, dict):
                lines.append(f"- `{row.get('path')}`: {row.get('reason')}")
        if remaining:
            lines.append(f"- ... {remaining} more")

    collisions = plan.get("collisions", [])
    if isinstance(collisions, list) and collisions:
        rows, remaining = limited_rows(collisions)
        lines.extend(["", "## Collisions", ""])
        for row in rows:
            if isinstance(row, dict):
                lines.append(f"- `{row.get('path')}`: {row.get('reason')}")
        if remaining:
            lines.append(f"- ... {remaining} more")

    validation_commands = plan.get("validation_commands", [])
    if isinstance(validation_commands, list) and validation_commands:
        lines.extend(["", "## Validation", ""])
        for command in validation_commands:
            lines.append(f"- `{command}`")

    rollback_notes = plan.get("rollback_notes", [])
    if isinstance(rollback_notes, list) and rollback_notes:
        lines.extend(["", "## Rollback Notes", ""])
        for note in rollback_notes:
            lines.append(f"- {note}")

    return "\n".join(lines) + "\n"


def write_install_plan(
    target_root: Path,
    plan: dict[str, object],
    *,
    path_guard: repo_harness_paths.HarnessPathGuard | None = None,
) -> dict[str, object]:
    guard = path_guard or repo_harness_paths.HarnessPathGuard(target_root, label="target")
    guard.write_text(
        INSTALL_PLAN_JSON_REL,
        json.dumps(plan, indent=2, sort_keys=True) + "\n",
        operation="install-plan-json-write",
    )
    guard.write_text(
        INSTALL_PLAN_MARKDOWN_REL,
        render_install_plan_markdown(plan),
        operation="install-plan-markdown-write",
    )
    return {
        "json": {"path": INSTALL_PLAN_JSON_REL, "status": "written"},
        "markdown": {"path": INSTALL_PLAN_MARKDOWN_REL, "status": "written"},
    }


def install_plan_artifact_status(*, dry_run: bool, blocked: bool, written: bool) -> dict[str, object]:
    if written:
        status = "written"
    elif blocked:
        status = "blocked"
    elif dry_run:
        status = "planned"
    else:
        status = "not-written"
    return {
        "json": {"path": INSTALL_PLAN_JSON_REL, "status": status},
        "markdown": {"path": INSTALL_PLAN_MARKDOWN_REL, "status": status},
    }


def build_human_summary(
    *,
    status: str,
    dry_run: bool,
    profile: dict[str, object],
    planned: list[dict[str, object]],
    copied: list[str],
    already_present: list[str],
    preserved_existing: list[str],
    merged: list[dict[str, object]],
    collisions: list[dict[str, object]],
    excluded: list[str],
) -> dict[str, object]:
    verb = "Plan" if dry_run else "Install"
    if collisions:
        headline = f"{verb} blocked by {len(collisions)} file collision(s)."
    elif dry_run:
        headline = f"Plan ready: {len(planned)} file(s) would be written."
    else:
        headline = f"Install finished: {len(copied)} file(s) written."
    plain_changes: list[str] = []
    if planned:
        plain_changes.append(f"{len(planned)} new harness files or clean updates are ready.")
    if copied:
        plain_changes.append(f"{len(copied)} harness files were copied into the target.")
    if already_present:
        plain_changes.append(f"{len(already_present)} files were already up to date.")
    if preserved_existing:
        plain_changes.append(f"{len(preserved_existing)} consumer-owned root file(s) were preserved.")
    if merged:
        plain_changes.append(f"{len(merged)} consumer-owned file(s) will receive merged harness entries.")
    if collisions:
        plain_changes.append("Some target files differ from the harness; review collisions before writing.")
    if excluded:
        plain_changes.append(
            f"{len(excluded)} files were skipped by the copy contract, including caches, secrets, run history, or generated install evidence."
        )
    return {
        "headline": headline,
        "profile": profile.get("name", "standard"),
        "status": status,
        "plain_changes": plain_changes,
    }


def target_is_unsafe(source_root: Path, target_root: Path) -> bool:
    return repo_harness_paths.root_relationship(
        source_root,
        target_root,
        operation="install-root-relationship",
    ).overlaps


def public_export_target_is_unsafe(source_root: Path, target_root: Path, manifest: dict[str, object]) -> bool:
    relationship = repo_harness_paths.root_relationship(
        source_root,
        target_root,
        operation="export-root-relationship",
    )
    if relationship.kind in {"same", "second-ancestor-of-first", "ambiguous"}:
        return True
    if relationship.kind == "first-ancestor-of-second":
        return not is_excluded_path(relationship.relative_path, manifest)
    return False


def command_display(args: list[str]) -> str:
    return "python -B .agents/manage.py " + " ".join(args)


def output_tail(text: str, limit: int = 4000) -> str:
    return text[-limit:] if len(text) > limit else text


def run_post_install_command(target_root: Path, args: list[str], timeout_seconds: int) -> dict[str, object]:
    command = [sys.executable, "-B", ".agents/manage.py", *args]
    try:
        completed = subprocess.run(
            command,
            cwd=target_root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command_display(args),
            "ok": False,
            "status": "timeout",
            "returncode": None,
            "output_tail": output_tail(str(exc)),
        }
    except OSError as exc:
        return {
            "command": command_display(args),
            "ok": False,
            "status": "failed-to-start",
            "returncode": None,
            "output_tail": str(exc),
        }
    return {
        "command": command_display(args),
        "ok": completed.returncode == 0,
        "status": "passed" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "output_tail": output_tail(completed.stdout),
    }


CommandRunner = Callable[[Path, list[str], int], dict[str, object]]


def post_install_commands(
    *,
    run_setup_check: bool,
    install_rg_portable: bool,
    bootstrap_local_ai: bool,
    download_ai_models: bool,
    local_ai_profiles: list[str] | None,
    max_download_gb: float | None,
) -> list[dict[str, object]]:
    commands: list[dict[str, object]] = []
    if install_rg_portable:
        commands.append(
            {
                "name": "setup-install-rg-portable",
                "args": ["setup", "--install-rg-portable", "--no-link-skills"],
                "timeout_seconds": 240,
            }
        )
    if run_setup_check:
        if not install_rg_portable:
            commands.append(
                {
                    "name": "setup-initialize",
                    "args": ["setup", "--no-link-skills"],
                    "timeout_seconds": 180,
                }
            )
        commands.append({"name": "setup-check", "args": ["setup", "--check", "--no-link-skills"], "timeout_seconds": 120})
    if download_ai_models:
        args = ["local-ai", "bootstrap", "--run-model", "--json"]
        for profile in local_ai_profiles or []:
            args.extend(["--profile", profile])
        if max_download_gb is not None:
            args.extend(["--max-download-gb", str(max_download_gb)])
        commands.append({"name": "local-ai-bootstrap-download", "args": args, "timeout_seconds": 3600})
    elif bootstrap_local_ai:
        commands.append(
            {
                "name": "local-ai-write-config",
                "args": ["local-ai", "write-config"],
                "timeout_seconds": 120,
            }
        )
        commands.append(
            {
                "name": "local-ai-policy-defaults",
                "args": ["local-ai", "policy", "--write-default", "--json", "--summary", "--compact"],
                "timeout_seconds": 180,
            }
        )
    return commands


def shell_quote(value: object) -> str:
    return '"' + str(value).replace('"', '\\"') + '"'


def install_next_commands(
    target_root: Path,
    *,
    dry_run: bool,
    selected_profile: dict[str, object],
    run_setup_check: bool,
    with_features: list[str] | None = None,
    without_features: list[str] | None = None,
) -> list[str]:
    profile = str(selected_profile.get("name") or "standard")
    feature_flags = repo_harness_profiles.feature_flag_text(with_features or [], without_features or [])
    if dry_run:
        return [
            f"python -B .agents/manage.py install-wizard --target {shell_quote(target_root)} --profile {profile}{feature_flags} --apply",
        ]
    commands = [
        f"cd {shell_quote(target_root)}",
        f"python -B .agents/manage.py start-here --simple --profile {profile}{feature_flags}",
        "python -B .agents/manage.py setup",
    ]
    commands.append("python -B .agents/manage.py setup --check")
    commands.append(STATUS_FAST_COMMAND)
    commands.append(NEXT_ACTION_COMMAND)
    return commands


def install_harness_report(
    source_root: Path,
    target_root: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
    profile: str = "standard",
    with_features: list[str] | None = None,
    without_features: list[str] | None = None,
    run_setup_check: bool = False,
    install_rg_portable: bool = False,
    bootstrap_local_ai: bool = False,
    download_ai_models: bool = False,
    local_ai_profiles: list[str] | None = None,
    max_download_gb: float | None = None,
    command_runner: CommandRunner | None = None,
) -> dict[str, object]:
    source_guard = repo_harness_paths.HarnessPathGuard(source_root, label="source")
    target_guard = repo_harness_paths.HarnessPathGuard(target_root, label="target")
    source_root = source_guard.root
    target_root = target_guard.root
    issues: list[str] = []
    unsafe_paths: list[dict[str, str]] = []
    planned_post_install = post_install_commands(
        run_setup_check=run_setup_check,
        install_rg_portable=install_rg_portable,
        bootstrap_local_ai=bootstrap_local_ai,
        download_ai_models=download_ai_models,
        local_ai_profiles=local_ai_profiles,
        max_download_gb=max_download_gb,
    )
    try:
        source_exists = source_guard.root_exists(operation="source-root-check")
    except repo_harness_paths.UnsafeHarnessPathError as exc:
        repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
        source_exists = False
    try:
        target_exists = target_guard.root_exists(operation="target-root-check")
        target_is_dir = target_guard.root_is_dir(operation="target-root-check") if target_exists else False
    except repo_harness_paths.UnsafeHarnessPathError as exc:
        repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
        target_exists = False
        target_is_dir = False
    if not source_exists and not unsafe_paths:
        issues.append(f"{SOURCE_ROOT_MISSING}{source_root}")
    if target_exists and not target_is_dir:
        issues.append(f"{TARGET_NOT_DIR}{target_root}")
    if not unsafe_paths:
        try:
            unsafe_root_relation = target_is_unsafe(source_root, target_root)
        except repo_harness_paths.UnsafeHarnessPathError as exc:
            repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
        else:
            if unsafe_root_relation:
                issues.append("target must be outside the source harness tree and its parents")
    if planned_post_install and not unsafe_paths:
        for error in target_guard.audit_existing_tree(operation="initialization-target-preflight"):
            repo_harness_paths.add_unsafe_path(unsafe_paths, error)

    payload_manifest = empty_payload_manifest("not-loaded")
    if source_exists and not unsafe_paths:
        payload_manifest, manifest_issues = load_payload_manifest(
            source_root,
            path_guard=source_guard,
            unsafe_paths=unsafe_paths,
        )
        issues.extend(manifest_issues)
    payload_manifest, selected_profile = effective_payload_manifest(
        payload_manifest,
        profile,
        issues,
        with_features=with_features,
        without_features=without_features,
    )

    files: list[Path] = []
    excluded: list[str] = []
    if not issues:
        files, excluded = iter_payload_candidates(
            source_root,
            payload_manifest,
            path_guard=source_guard,
            unsafe_paths=unsafe_paths,
        )
    resolved_file_manifest, resolved_manifest_digest = repo_harness_profiles.source_file_manifest(
        source_root,
        files,
        unsafe_paths=unsafe_paths,
    )

    install_manifest_issues: list[str] = []
    existing_manifest = read_install_manifest(
        target_root,
        path_guard=target_guard,
        unsafe_paths=unsafe_paths,
        manifest_issues=install_manifest_issues,
    )
    previous_rows = validated_manifest_rows(
        existing_manifest,
        target_guard,
        unsafe_paths,
        install_manifest_issues,
    )
    issues.extend(f"invalid-install-manifest: {issue}" for issue in install_manifest_issues)
    previous_hashes = {
        path: str(row.get("sha256", "")).strip()
        for path, row in previous_rows.items()
        if str(row.get("sha256", "")).strip()
    }
    selected_paths = {path_key(path.relative_to(source_root)) for path in files}
    for relative in sorted(selected_paths):
        try:
            target_guard.check_file_destination(relative, operation="install-target-preflight")
        except repo_harness_paths.UnsafeHarnessPathError as exc:
            repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
    for relative in (INSTALL_MANIFEST_REL, INSTALL_PLAN_JSON_REL, INSTALL_PLAN_MARKDOWN_REL):
        try:
            target_guard.check_file_destination(relative, operation="install-evidence-preflight")
        except repo_harness_paths.UnsafeHarnessPathError as exc:
            repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
    retained_rows: dict[str, dict[str, object]] = {}
    for path, row in previous_rows.items():
        if path in selected_paths:
            continue
        try:
            if target_guard.exists(path, operation="install-profile-contraction-check"):
                retained_rows[path] = row
        except repo_harness_paths.UnsafeHarnessPathError as exc:
            repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
    retained_previous_profile_files = sorted(retained_rows)
    contraction_blocked = bool(retained_previous_profile_files) and not issues and not unsafe_paths
    if contraction_blocked:
        issues.append(
            "profile-contraction-blocked: existing installed files fall outside the resolved profile: "
            + ", ".join(retained_previous_profile_files)
        )
    if unsafe_paths and not any(issue.startswith("unsafe-path-blocked:") for issue in issues):
        issues.append(f"unsafe-path-blocked: {len(unsafe_paths)} unsafe path access(es) rejected")
    operation = "update" if previous_hashes else "install"
    if operation == "update" and run_setup_check:
        planned_post_install = [
            command
            for command in planned_post_install
            if command.get("name") != "setup-initialize"
        ]
    if operation == "update" and force:
        issues.append("force-overwrite is not available for harness updates; use a project overlay or harness-promote")
    planned: list[dict[str, object]] = []
    collisions: list[dict[str, object]] = []
    already_present: list[str] = []
    preserved_existing: list[str] = []
    merged: list[dict[str, object]] = []
    manifest_omit_paths: set[str] = set()
    manifest_target_hash_paths: set[str] = set()
    for source_file in files if not issues else []:
        rel = path_key(source_file.relative_to(source_root))
        source_hash = source_guard.sha256(rel, operation="install-source-hash")
        target_file_exists = target_guard.exists(rel, operation="install-target-stat")
        target_is_file = target_guard.is_file(rel, operation="install-target-stat") if target_file_exists else False
        if target_file_exists:
            target_hash = target_guard.sha256(rel, operation="install-target-hash") if target_is_file else ""
            if target_is_file and target_hash == source_hash:
                already_present.append(rel)
                continue
            if (
                target_is_file
                and previous_hashes.get(rel) == target_hash
                and rel not in MERGE_EXISTING_CONSUMER_PATHS
            ):
                planned.append(
                    {
                        "path": rel,
                        "bytes": source_guard.stat_size(rel, operation="install-source-stat"),
                        "reason": "update-clean-installed-file",
                    }
                )
                continue
            if (
                not force
                and target_is_file
                and rel in PRESERVE_EXISTING_CONSUMER_PATHS
                and not previous_hashes.get(rel)
            ):
                preserved_existing.append(rel)
                manifest_omit_paths.add(rel)
                continue
            if not force and target_is_file and rel in MERGE_EXISTING_CONSUMER_PATHS:
                merged_text, missing_entries = merged_text_with_missing_lines(
                    target_guard.read_text(rel, operation="install-merge-target-read"),
                    source_guard.read_text(rel, operation="install-merge-source-read"),
                )
                merged.append(
                    {
                        "path": rel,
                        "bytes": len(merged_text.encode("utf-8")),
                        "reason": "merge-missing-harness-entries"
                        if missing_entries
                        else "existing-file-already-has-harness-entries",
                        "added_entries": missing_entries,
                    }
                )
                manifest_omit_paths.add(rel)
                continue
            if not force:
                reason = "target file differs from source"
                if previous_hashes.get(rel):
                    reason = "target file was edited after last harness install"
                collisions.append({"path": rel, "reason": reason})
                continue
        planned.append(
            {
                "path": rel,
                "bytes": source_guard.stat_size(rel, operation="install-source-stat"),
                "reason": "new-or-forced",
            }
        )

    install_plan = build_install_plan(
        source_root=source_root,
        target_root=target_root,
        operation=operation,
        dry_run=dry_run,
        force=force,
        payload_manifest=payload_manifest,
        planned=planned,
        already_present=already_present,
        preserved_existing=preserved_existing,
        merged=merged,
        collisions=collisions,
        excluded=excluded,
        issues=issues,
        planned_post_install=planned_post_install,
        selected_profile=selected_profile,
        resolved_file_manifest=resolved_file_manifest,
        resolved_manifest_digest=resolved_manifest_digest,
        retained_previous_profile_files=retained_previous_profile_files,
    )

    copied: list[str] = []
    manifest_path = ""
    install_plan_artifacts = install_plan_artifact_status(
        dry_run=dry_run,
        blocked=bool(issues or collisions),
        written=False,
    )
    if not dry_run and not issues and not collisions:
        for relative in [
            *(str(item["path"]) for item in planned),
            *(str(item["path"]) for item in merged),
            INSTALL_MANIFEST_REL,
            INSTALL_PLAN_JSON_REL,
            INSTALL_PLAN_MARKDOWN_REL,
        ]:
            try:
                target_guard.check_file_destination(relative, operation="install-write-preflight")
            except repo_harness_paths.UnsafeHarnessPathError as exc:
                repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
        if unsafe_paths:
            issues.append(f"unsafe-path-blocked: {len(unsafe_paths)} unsafe path access(es) rejected before write")
        if not issues:
            try:
                target_guard.ensure_root(operation="install-target-root-create")
                for item in planned:
                    rel = str(item["path"])
                    target_guard.copy_from(source_guard, rel, operation="install-copy")
                    copied.append(rel)
                for item in merged:
                    rel = str(item["path"])
                    merged_text, _missing_entries = merged_text_with_missing_lines(
                        target_guard.read_text(rel, operation="install-merge-target-read")
                        if target_guard.exists(rel, operation="install-merge-target-stat")
                        else "",
                        source_guard.read_text(rel, operation="install-merge-source-read"),
                    )
                    target_guard.write_text(rel, merged_text, operation="install-merge-target-write")
                    copied.append(rel)
                manifest_path = path_key(
                    write_install_manifest(
                        source_root,
                        target_root,
                        files,
                        omit_paths=manifest_omit_paths,
                        target_hash_paths=manifest_target_hash_paths,
                        selected_profile=selected_profile,
                        resolved_file_manifest=resolved_file_manifest,
                        resolved_manifest_digest=resolved_manifest_digest,
                        source_guard=source_guard,
                        target_guard=target_guard,
                    ).relative_to(target_root)
                )
                install_plan_artifacts = write_install_plan(target_root, install_plan, path_guard=target_guard)
            except repo_harness_paths.UnsafeHarnessPathError as exc:
                repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
                issues.append(f"unsafe-path-blocked: {exc}")

    post_install: list[dict[str, object]] = []
    if not dry_run and not issues and not collisions and planned_post_install:
        runner = command_runner or run_post_install_command
        for command in planned_post_install:
            command_errors = target_guard.audit_existing_tree(operation="initialization-command-preflight")
            if command_errors:
                for error in command_errors:
                    repo_harness_paths.add_unsafe_path(unsafe_paths, error)
                issues.append(
                    f"unsafe-path-blocked: {len(unsafe_paths)} unsafe path access(es) rejected before initialization command"
                )
                break
            args = [str(item) for item in command.get("args", [])] if isinstance(command.get("args"), list) else []
            timeout = int(command.get("timeout_seconds", 120) or 120)
            result = runner(target_root, args, timeout)
            result["name"] = command.get("name", "")
            post_install.append(result)

    if issues and not manifest_path:
        install_plan_artifacts = install_plan_artifact_status(dry_run=dry_run, blocked=True, written=False)
    blocked = bool(issues or collisions)
    post_failed = any(item.get("ok") is not True for item in post_install)
    if unsafe_paths:
        status = "unsafe-path-blocked"
    elif install_manifest_issues:
        status = "invalid-install-manifest"
    elif contraction_blocked:
        status = "profile-contraction-blocked"
    else:
        status = "blocked" if blocked else ("planned" if dry_run else ("post-install-failed" if post_failed else ("updated" if operation == "update" else "installed")))
    return {
        "schema_version": 1,
        "tool": "install-harness",
        "ok": not blocked and not post_failed,
        "status": status,
        "operation": operation,
        "source_root": str(source_root),
        "target_root": str(target_root),
        "dry_run": dry_run,
        "force": force,
        "profile": selected_profile,
        "resolved_features": list(selected_profile.get("features", [])),
        "resolved_file_manifest": resolved_file_manifest,
        "resolved_manifest_digest": resolved_manifest_digest,
        "retained_previous_profile_files": retained_previous_profile_files,
        "unsafe_paths": repo_harness_paths.sorted_unsafe_paths(unsafe_paths),
        "clean_state": not unsafe_paths,
        "payload_manifest": payload_manifest,
        "install_manifest": manifest_path or INSTALL_MANIFEST_REL,
        "install_manifest_issues": install_manifest_issues,
        "install_plan": install_plan,
        "install_plan_artifacts": install_plan_artifacts,
        "summary": {
            "candidate_files": len(files),
            "planned_files": len(planned),
            "copied_files": len(copied),
            "already_present_files": len(already_present),
            "preserved_existing_files": len(preserved_existing),
            "merged_files": len(merged),
            "collision_files": len(collisions),
            "excluded_files": len(excluded),
            "manifest_include_roots": len(payload_manifest.get("include_roots", [])) if isinstance(payload_manifest.get("include_roots"), list) else 0,
            "post_install_steps": len(planned_post_install),
            "post_install_failed": sum(1 for item in post_install if item.get("ok") is not True),
        },
        "human_summary": build_human_summary(
            status=status,
            dry_run=dry_run,
            profile=selected_profile,
            planned=planned,
            copied=copied,
            already_present=already_present,
            preserved_existing=preserved_existing,
            merged=merged,
            collisions=collisions,
            excluded=excluded,
        ),
        "planned": planned,
        "copied": copied,
        "already_present": sorted(already_present),
        "preserved_existing": sorted(preserved_existing),
        "merged": merged,
        "collisions": collisions,
        "excluded": excluded,
        "planned_post_install": [
            {"name": item.get("name", ""), "command": command_display([str(part) for part in item.get("args", [])])}
            for item in planned_post_install
        ],
        "post_install": post_install,
        "issues": issues,
        "next_commands": []
        if blocked or post_failed
        else install_next_commands(
            target_root,
            dry_run=dry_run,
            selected_profile=selected_profile,
            run_setup_check=run_setup_check,
            with_features=with_features,
            without_features=without_features,
        ),
    }


def copy_contract_report(
    source_root: Path,
    *,
    profile: str = "standard",
    with_features: list[str] | None = None,
    without_features: list[str] | None = None,
) -> dict[str, object]:
    source_guard = repo_harness_paths.HarnessPathGuard(source_root, label="source")
    source_root = source_guard.root
    issues: list[str] = []
    unsafe_paths: list[dict[str, str]] = []
    manifest, manifest_issues = load_payload_manifest(
        source_root,
        path_guard=source_guard,
        unsafe_paths=unsafe_paths,
    )
    issues.extend(manifest_issues)
    manifest, selected_profile = effective_payload_manifest(
        manifest,
        profile,
        issues,
        with_features=with_features,
        without_features=without_features,
    )
    include_roots = list(manifest.get("include_roots", [])) if isinstance(manifest.get("include_roots"), list) else []
    general_excludes = list(manifest.get("exclude_globs", [])) if isinstance(manifest.get("exclude_globs"), list) else []
    state_excludes = list(manifest.get("state_exclude_globs", [])) if isinstance(manifest.get("state_exclude_globs"), list) else []
    declared_required_general = REQUIRED_GENERAL_EXCLUDES
    declared_required_state = REQUIRED_STATE_EXCLUDES
    missing_roots: list[str] = []
    for entry in include_roots:
        try:
            if not source_guard.exists(entry, operation="copy-contract-include-root-check"):
                missing_roots.append(entry)
        except repo_harness_paths.UnsafeHarnessPathError as exc:
            repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
    missing_general_excludes = [pattern for pattern in declared_required_general if pattern not in general_excludes]
    missing_required_excludes = [pattern for pattern in declared_required_state if pattern not in state_excludes]
    for entry in missing_roots:
        issues.append(f"include root does not exist: {entry}")
    for pattern in missing_general_excludes:
        issues.append(f"required project-local exclude is missing: {pattern}")
    for pattern in missing_required_excludes:
        issues.append(f"required state exclude is missing: {pattern}")
    files: list[Path] = []
    excluded: list[str] = []
    if not issues:
        files, excluded = iter_payload_candidates(
            source_root,
            manifest,
            path_guard=source_guard,
            unsafe_paths=unsafe_paths,
        )
    resolved_file_manifest, resolved_manifest_digest = repo_harness_profiles.source_file_manifest(
        source_root,
        files,
        unsafe_paths=unsafe_paths,
    )
    if unsafe_paths and not any(issue.startswith("unsafe-path-blocked:") for issue in issues):
        issues.append(f"unsafe-path-blocked: {len(unsafe_paths)} unsafe path access(es) rejected")
    return {
        "schema_version": 1,
        "tool": "validate-copy-contract",
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "source_root": str(source_root),
        "profile": selected_profile,
        "resolved_features": list(selected_profile.get("features", [])),
        "resolved_file_manifest": resolved_file_manifest,
        "resolved_manifest_digest": resolved_manifest_digest,
        "unsafe_paths": repo_harness_paths.sorted_unsafe_paths(unsafe_paths),
        "payload_manifest": manifest,
        "required_general_excludes": list(declared_required_general),
        "required_state_excludes": list(declared_required_state),
        "summary": {
            "include_roots": len(include_roots),
            "candidate_files": len(files),
            "excluded_files": len(excluded),
            "missing_roots": len(missing_roots),
            "missing_general_excludes": len(missing_general_excludes),
            "missing_required_excludes": len(missing_required_excludes),
        },
        "missing_roots": missing_roots,
        "missing_general_excludes": missing_general_excludes,
        "missing_required_excludes": missing_required_excludes,
        "issues": issues,
        "next_command": (
            "python -B .agents/manage.py install-harness --target <project> --dry-run "
            f"--profile {selected_profile.get('name', 'standard')}"
            f"{repo_harness_profiles.feature_flag_text(with_features or [], without_features or [])}"
        ),
    }


def public_export_report(
    source_root: Path,
    target_root: Path,
    *,
    profile: str = "standard",
    with_features: list[str] | None = None,
    without_features: list[str] | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, object]:
    source_guard = repo_harness_paths.HarnessPathGuard(source_root, label="source")
    target_guard = repo_harness_paths.HarnessPathGuard(target_root, label="export-target")
    source_root = source_guard.root
    target_root = target_guard.root
    issues: list[str] = []
    unsafe_paths: list[dict[str, str]] = []
    try:
        source_exists = source_guard.root_exists(operation="export-source-root-check")
    except repo_harness_paths.UnsafeHarnessPathError as exc:
        repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
        source_exists = False
    try:
        target_exists = target_guard.root_exists(operation="export-target-root-check")
        target_is_dir = target_guard.root_is_dir(operation="export-target-root-check") if target_exists else False
    except repo_harness_paths.UnsafeHarnessPathError as exc:
        repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
        target_exists = False
        target_is_dir = False
    if not source_exists and not unsafe_paths:
        issues.append(f"{SOURCE_ROOT_MISSING}{source_root}")
    if target_exists and not target_is_dir:
        issues.append(f"{TARGET_NOT_DIR}{target_root}")
    manifest = empty_payload_manifest("not-loaded")
    if source_exists and not unsafe_paths:
        manifest, manifest_issues = load_payload_manifest(
            source_root,
            path_guard=source_guard,
            unsafe_paths=unsafe_paths,
        )
        issues.extend(manifest_issues)
    manifest, selected_profile = effective_payload_manifest(
        manifest,
        profile,
        issues,
        with_features=with_features,
        without_features=without_features,
    )
    if not unsafe_paths:
        try:
            unsafe_root_relation = public_export_target_is_unsafe(source_root, target_root, manifest)
        except repo_harness_paths.UnsafeHarnessPathError as exc:
            repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
        else:
            if unsafe_root_relation:
                issues.append("target must be outside the source tree, unless it is under an excluded export path")
    files: list[Path] = []
    excluded: list[str] = []
    if not issues:
        files, excluded = iter_payload_candidates(
            source_root,
            manifest,
            path_guard=source_guard,
            unsafe_paths=unsafe_paths,
        )
    resolved_file_manifest, resolved_manifest_digest = repo_harness_profiles.source_file_manifest(
        source_root,
        files,
        unsafe_paths=unsafe_paths,
    )
    selected_paths = {path_key(path.relative_to(source_root)) for path in files}
    for relative in sorted(selected_paths):
        try:
            target_guard.check_file_destination(relative, operation="export-target-preflight")
        except repo_harness_paths.UnsafeHarnessPathError as exc:
            repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
    existing_target_paths, existing_errors = target_guard.existing_paths(operation="export-target-reuse-check")
    for error in existing_errors:
        repo_harness_paths.add_unsafe_path(unsafe_paths, error)
    out_of_selection_existing_paths = sorted(
        path
        for path in existing_target_paths
        if path not in selected_paths
        and not (path.endswith("/") and any(selected.startswith(path) for selected in selected_paths))
    )
    target_not_empty = bool(out_of_selection_existing_paths) and not unsafe_paths
    if target_not_empty:
        issues.append(
            "export-target-not-empty: public export contains paths outside the resolved selection: "
            + ", ".join(out_of_selection_existing_paths)
        )
    if unsafe_paths and not any(issue.startswith("unsafe-path-blocked:") for issue in issues):
        issues.append(f"unsafe-path-blocked: {len(unsafe_paths)} unsafe path access(es) rejected")
    planned: list[dict[str, object]] = []
    collisions: list[dict[str, object]] = []
    already_present: list[str] = []
    if not issues:
        for source_file in files:
            rel = path_key(source_file.relative_to(source_root))
            source_hash = source_guard.sha256(rel, operation="export-source-hash")
            if target_guard.exists(rel, operation="export-target-stat"):
                target_hash = target_guard.sha256(rel, operation="export-target-hash")
                if target_hash == source_hash:
                    already_present.append(rel)
                    continue
                if not force:
                    collisions.append({"path": rel, "reason": "target file differs from source"})
                    continue
                reason = "forced-update"
            else:
                reason = "new"
            planned.append(
                {
                    "path": rel,
                    "bytes": source_guard.stat_size(rel, operation="export-source-stat"),
                    "reason": reason,
                }
            )
    exported: list[str] = []
    if not dry_run and not issues and not collisions:
        try:
            for item in planned:
                target_guard.check_file_destination(str(item["path"]), operation="export-write-preflight")
            target_guard.ensure_root(operation="export-target-root-create")
            for item in planned:
                rel = str(item["path"])
                target_guard.copy_from(source_guard, rel, operation="public-export-copy")
                exported.append(rel)
        except repo_harness_paths.UnsafeHarnessPathError as exc:
            repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
            issues.append(f"unsafe-path-blocked: {exc}")
    blocked = bool(issues or collisions)
    if unsafe_paths:
        status = "unsafe-path-blocked"
    elif target_not_empty:
        status = "export-target-not-empty"
    else:
        status = "blocked" if blocked else ("planned" if dry_run else "exported")
    return {
        "schema_version": 1,
        "tool": "public-export",
        "ok": not blocked,
        "status": status,
        "source_root": str(source_root),
        "target_root": str(target_root),
        "profile": selected_profile,
        "resolved_features": list(selected_profile.get("features", [])),
        "resolved_file_manifest": resolved_file_manifest,
        "resolved_manifest_digest": resolved_manifest_digest,
        "unsafe_paths": repo_harness_paths.sorted_unsafe_paths(unsafe_paths),
        "dry_run": dry_run,
        "force": force,
        "existing_target_paths": existing_target_paths,
        "out_of_selection_existing_paths": out_of_selection_existing_paths,
        "summary": {
            "candidate_files": len(files),
            "planned_files": len(planned),
            "exported_files": len(exported),
            "already_present_files": len(already_present),
            "excluded_files": len(excluded),
            "collision_files": len(collisions),
        },
        "planned": planned,
        "exported": exported,
        "already_present": sorted(already_present),
        "excluded": excluded,
        "collisions": collisions,
        "issues": issues,
        "next_command": (
            f"python -B .agents/manage.py public-export --target {target_root} "
            f"--profile {selected_profile.get('name', 'standard')}"
            f"{repo_harness_profiles.feature_flag_text(with_features or [], without_features or [])}"
        ),
    }
