#!/usr/bin/env python3
"""Resolve immutable harness tags and update a consumer transactionally."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from repo_support import repo_harness_install
from repo_support import repo_harness_paths
from repo_support import repo_harness_profiles
from repo_support import repo_policy


LOCK_REL = repo_harness_install.INSTALL_MANIFEST_REL
LEGACY_LOCK_REL = repo_harness_install.LEGACY_INSTALL_MANIFEST_REL
PROJECT_OVERLAY_REL = repo_harness_install.PROJECT_OVERLAY_REL
UPDATE_STATE_REL = ".agents/harness-update"
TAG_INDEX_REL = f"{UPDATE_STATE_REL}/tag-index.json"
TRANSACTIONS_REL = f"{UPDATE_STATE_REL}/transactions"
SEMVER_RE = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_ARCHIVE_FILES = 20_000
MAX_ARCHIVE_BYTES = 750 * 1024 * 1024
USER_AGENT = "portable-harness-updater/1.0"
DEFAULT_REPOSITORY = "https://github.com/Belgian-Coder/skills"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_consumer_directory(root: Path, relative: str, *, operation: str) -> Path:
    guard = repo_harness_paths.HarnessPathGuard(root, label="consumer")
    try:
        directory = guard.check(relative, operation=operation)
        directory.mkdir(parents=True, exist_ok=True)
        return guard.check(relative, operation=operation)
    except (OSError, repo_harness_paths.UnsafeHarnessPathError) as exc:
        raise RuntimeError(f"could not prepare safe harness state directory {relative}: {exc}") from exc


def atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.harness-{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_copy(source: Path, target: Path) -> None:
    atomic_write_bytes(target, source.read_bytes())


def normalized_repository(value: object) -> str:
    repository = str(value or "").strip().rstrip("/")
    if repository.startswith("git@github.com:"):
        repository = "https://github.com/" + repository.removeprefix("git@github.com:")
    if repository.endswith(".git"):
        repository = repository[:-4]
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        repository = "https://github.com/" + repository
    if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError(f"unsupported harness repository identity: {repository!r}")
    return repository


def github_slug(repository: str) -> str:
    return normalized_repository(repository).removeprefix("https://github.com/")


def request_json(url: str, *, timeout: int = 20) -> object:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    github_auth = os.environ.get("GITHUB_TOKEN", "").strip()
    if github_auth:
        headers["Authorization"] = f"Bearer {github_auth}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GitHub request failed for {url}: {exc}") from exc


def stable_version_key(tag: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(tag)
    if not match:
        raise ValueError(f"tag must be a stable semantic version such as v1.2.3: {tag}")
    return tuple(int(match.group(index)) for index in range(1, 4))


def fetch_stable_tags(repository: str) -> list[str]:
    slug = github_slug(repository)
    payload = request_json(f"https://api.github.com/repos/{slug}/tags?per_page=100")
    if not isinstance(payload, list):
        raise RuntimeError("GitHub tags response was not a list")
    tags = [str(row.get("name", "")) for row in payload if isinstance(row, dict)]
    return sorted({tag for tag in tags if SEMVER_RE.fullmatch(tag)}, key=stable_version_key, reverse=True)


def write_tag_cache(root: Path, repository: str, tags: list[str]) -> None:
    payload = {"schema_version": 1, "repository": repository, "fetched_at": utc_now(), "stable_tags": tags[:100]}
    guard = repo_harness_paths.HarnessPathGuard(root, label="consumer")
    path = guard.ensure_parent(TAG_INDEX_REL, operation="harness-tag-cache-write")
    atomic_write_bytes(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def read_tag_cache(root: Path, repository: str) -> list[str]:
    guard = repo_harness_paths.HarnessPathGuard(root, label="consumer")
    try:
        if not guard.is_file(TAG_INDEX_REL, operation="harness-tag-cache-read"):
            return []
        payload = json.loads(guard.read_text(TAG_INDEX_REL, operation="harness-tag-cache-read"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("repository") != repository:
        return []
    rows = payload.get("stable_tags")
    return [str(item) for item in rows if SEMVER_RE.fullmatch(str(item))] if isinstance(rows, list) else []


def resolve_annotated_tag(repository: str, tag: str) -> str:
    stable_version_key(tag)
    slug = github_slug(repository)
    encoded = urllib.parse.quote(tag, safe="")
    reference = request_json(f"https://api.github.com/repos/{slug}/git/ref/tags/{encoded}")
    if not isinstance(reference, dict) or not isinstance(reference.get("object"), dict):
        raise RuntimeError(f"tag {tag} did not resolve to a Git object")
    target = reference["object"]
    if target.get("type") != "tag":
        raise RuntimeError(f"tag {tag} is lightweight; harness releases require annotated tags")
    for _ in range(5):
        tag_object = request_json(f"https://api.github.com/repos/{slug}/git/tags/{target.get('sha', '')}")
        if not isinstance(tag_object, dict) or not isinstance(tag_object.get("object"), dict):
            raise RuntimeError(f"annotated tag object for {tag} is invalid")
        target = tag_object["object"]
        if target.get("type") == "commit":
            commit = str(target.get("sha", "")).lower()
            if not COMMIT_RE.fullmatch(commit):
                break
            return commit
        if target.get("type") != "tag":
            break
    raise RuntimeError(f"annotated tag {tag} did not resolve to a commit")


def read_lock(root: Path) -> dict[str, object]:
    issues: list[str] = []
    payload = repo_harness_install.read_install_manifest(root, manifest_issues=issues)
    if issues:
        raise RuntimeError("; ".join(issues))
    if not payload:
        raise RuntimeError(f"{LOCK_REL} is missing; use harness-adopt for a legacy installation")
    if payload.get("tool") != "harness-lock":
        raise RuntimeError(f"{LOCK_REL} is not a harness-lock document")
    normalized_repository(payload.get("repository"))
    tag = str(payload.get("tag", ""))
    if tag != "unreleased" and not SEMVER_RE.fullmatch(tag):
        raise RuntimeError(f"{LOCK_REL} tag must be a stable semantic version")
    commit = str(payload.get("commit", "")).lower()
    if not COMMIT_RE.fullmatch(commit) or (tag != "unreleased" and commit == "0" * 40):
        raise RuntimeError(f"{LOCK_REL} commit must be a resolved 40-character SHA")
    digest = str(payload.get("payload_digest", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RuntimeError(f"{LOCK_REL} payload_digest must be a SHA-256")
    install = payload.get("install")
    if not isinstance(install, dict) or not str(install.get("profile", "")).strip() or not isinstance(install.get("features"), list):
        raise RuntimeError(f"{LOCK_REL} install profile/features are invalid")
    row_issues: list[str] = []
    repo_harness_install.validated_manifest_rows(
        payload,
        repo_harness_paths.HarnessPathGuard(root, label="consumer"),
        [],
        row_issues,
    )
    if row_issues:
        raise RuntimeError("; ".join(row_issues))
    return payload


def read_json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{path} must contain an object")
    return value


def read_project_overlay(root: Path) -> list[str]:
    """Read the tracked list of paths explicitly transferred to project ownership."""

    overlay_path = root / PROJECT_OVERLAY_REL
    if not overlay_path.exists():
        return []
    payload = read_json_object(overlay_path)
    if payload.get("schema_version") != 1 or payload.get("tool") != "harness-project-overlay":
        raise RuntimeError(
            f"{PROJECT_OVERLAY_REL} must use schema_version 1 and tool harness-project-overlay"
        )
    values = payload.get("paths")
    if not isinstance(values, list):
        raise RuntimeError(f"{PROJECT_OVERLAY_REL} paths must be a list")
    paths: list[str] = []
    for value in values:
        try:
            path = repo_harness_paths.normalize_relative_path(value)
        except ValueError as exc:
            raise RuntimeError(f"invalid project overlay path {value!r}: {exc}") from exc
        if repo_harness_install.is_state_path(path) or path == PROJECT_OVERLAY_REL:
            raise RuntimeError(f"project overlay path cannot claim harness state: {path}")
        paths.append(path)
    if len(paths) != len(set(paths)):
        raise RuntimeError(f"{PROJECT_OVERLAY_REL} contains duplicate paths")
    return sorted(paths)


def download_archive(repository: str, commit: str, destination: Path) -> Path:
    if destination.exists() and sha256_file(destination):
        return destination
    slug = github_slug(repository)
    url = f"https://github.com/{slug}/archive/{commit}.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    temporary = destination.with_suffix(".tmp")
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as handle:
            shutil.copyfileobj(response, handle, length=1024 * 1024)
        os.replace(temporary, destination)
    except (OSError, urllib.error.URLError) as exc:
        if temporary.exists():
            temporary.unlink()
        raise RuntimeError(f"archive download failed: {exc}") from exc
    return destination


def safe_extract_archive(archive_path: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    total_bytes = 0
    file_count = 0
    roots: set[str] = set()
    seen_paths: set[str] = set()
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise RuntimeError(f"invalid harness archive: {exc}") from exc
    with archive:
        for member in archive.infolist():
            raw = member.filename.replace("\\", "/")
            parts = [part for part in raw.split("/") if part]
            if not parts or raw.startswith("/") or any(part in {".", ".."} for part in parts) or ":" in parts[0]:
                raise RuntimeError(f"unsafe archive path: {member.filename}")
            roots.add(parts[0])
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise RuntimeError(f"archive symlink is not allowed: {member.filename}")
            file_type = stat.S_IFMT(mode)
            if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise RuntimeError(f"unsupported archive member type: {member.filename}")
            if member.flag_bits & 0x1:
                raise RuntimeError(f"encrypted archive member is not allowed: {member.filename}")
            if member.is_dir():
                continue
            normalized = "/".join(parts)
            if normalized in seen_paths:
                raise RuntimeError(f"duplicate archive path: {member.filename}")
            seen_paths.add(normalized)
            file_count += 1
            total_bytes += member.file_size
            if file_count > MAX_ARCHIVE_FILES or total_bytes > MAX_ARCHIVE_BYTES:
                raise RuntimeError("harness archive exceeds bounded extraction limits")
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    if len(roots) != 1:
        raise RuntimeError("harness archive must contain exactly one top-level directory")
    extracted_root = destination / next(iter(roots))
    if not (extracted_root / repo_harness_install.PAYLOAD_MANIFEST_REL).is_file():
        raise RuntimeError("archive does not contain the harness payload manifest")
    return extracted_root


def verify_archive_commit_root(extracted_root: Path, commit: str) -> None:
    if not extracted_root.name.lower().endswith(commit.lower()):
        raise RuntimeError(
            f"archive root {extracted_root.name!r} does not identify resolved commit {commit}"
        )


def selected_payload(source_root: Path, lock: dict[str, object]) -> tuple[list[Path], dict[str, object], list[dict[str, object]], str]:
    issues: list[str] = []
    manifest, manifest_issues = repo_harness_install.load_payload_manifest(source_root)
    issues.extend(manifest_issues)
    install = lock.get("install") if isinstance(lock.get("install"), dict) else {}
    profile = str(install.get("profile", "standard"))
    manifest, selected = repo_harness_install.effective_payload_manifest(manifest, profile, issues)
    files, _excluded = repo_harness_install.iter_payload_candidates(source_root, manifest) if not issues else ([], [])
    rows, digest = repo_harness_profiles.source_file_manifest(source_root, files)
    if issues:
        raise RuntimeError("archive payload is invalid: " + "; ".join(issues))
    return files, selected, rows, digest


def target_reference(
    root: Path,
    lock: dict[str, object],
    *,
    requested: str,
    archive: str | None,
    archive_metadata: str | None,
) -> tuple[str, str, str, Path, dict[str, object] | None]:
    repository = normalized_repository(lock.get("repository"))
    metadata = None
    if archive:
        if not archive_metadata:
            raise RuntimeError("--archive requires --archive-metadata with repository, tag, commit, and payload_digest")
        metadata = read_json_object(Path(archive_metadata).expanduser().resolve())
        metadata_repository = normalized_repository(metadata.get("repository"))
        if metadata_repository != repository:
            raise RuntimeError("local archive repository identity does not match the lock")
        tag = str(metadata.get("tag", ""))
        commit = str(metadata.get("commit", "")).lower()
        if requested != "latest" and requested != tag:
            raise RuntimeError("local archive tag does not match --to/--tag")
        stable_version_key(tag)
        if not COMMIT_RE.fullmatch(commit):
            raise RuntimeError("local archive metadata commit must be a 40-character SHA")
        archive_path = Path(archive).expanduser().resolve()
        if not archive_path.is_file():
            raise RuntimeError(f"local archive does not exist: {archive_path}")
        return repository, tag, commit, archive_path, metadata

    tags = fetch_stable_tags(repository)
    write_tag_cache(root, repository, tags)
    if requested == "latest":
        if not tags:
            raise RuntimeError("repository has no stable semantic tags")
        tag = tags[0]
    else:
        stable_version_key(requested)
        if requested not in tags:
            raise RuntimeError(f"stable tag was not found upstream: {requested}")
        tag = requested
    commit = resolve_annotated_tag(repository, tag)
    archive_relative = f"{UPDATE_STATE_REL}/cache/archives/{commit}.zip"
    archive_path = repo_harness_paths.HarnessPathGuard(root, label="consumer").ensure_parent(
        archive_relative, operation="harness-archive-cache-write"
    )
    return repository, tag, commit, download_archive(repository, commit, archive_path), None


def make_lock(
    *,
    repository: str,
    tag: str,
    commit: str,
    selected_profile: dict[str, object],
    digest: str,
    rows: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "tool": "harness-lock",
        "repository": repository,
        "tag": tag,
        "commit": commit,
        "install": {
            "profile": str(selected_profile.get("name", "standard")),
            "features": sorted(str(item) for item in selected_profile.get("features", []) if str(item)),
        },
        "payload_digest": digest,
        "files": sorted(rows, key=lambda row: str(row.get("path", ""))),
    }


def classify_update(root: Path, source_root: Path, old_lock: dict[str, object], new_rows: list[dict[str, object]]) -> dict[str, object]:
    guard = repo_harness_paths.HarnessPathGuard(root, label="consumer")
    old_rows = repo_harness_install.validated_manifest_rows(old_lock, guard, [], [])
    new_by_path = {str(row["path"]): dict(row) for row in new_rows}
    updates: list[dict[str, object]] = []
    additions: list[dict[str, object]] = []
    deletions: list[dict[str, object]] = []
    unchanged: list[str] = []
    preserved: list[str] = []
    collisions: list[dict[str, str]] = []
    overlay_paths = read_project_overlay(root)
    known_managed_paths = set(old_rows) | set(new_by_path)
    for path in overlay_paths:
        if path not in known_managed_paths:
            collisions.append({"path": path, "reason": "project overlay path is not managed by the current or target harness"})
    for path in sorted(known_managed_paths):
        old = old_rows.get(path)
        new = new_by_path.get(path)
        exists = guard.exists(path, operation="harness-update-preflight")
        is_file = guard.is_file(path, operation="harness-update-preflight") if exists else False
        current_hash = guard.sha256(path, operation="harness-update-preflight") if is_file else ""
        if path in overlay_paths:
            if is_file:
                preserved.append(path)
            else:
                collisions.append({"path": path, "reason": "project overlay path must be an existing regular file"})
            continue
        if old:
            if not is_file or current_hash != old.get("sha256"):
                collisions.append({"path": path, "reason": "managed file differs from its tracked base" if exists else "managed file is missing"})
                continue
            if new is None:
                deletions.append({"path": path, "base_sha256": current_hash})
            elif current_hash == new.get("sha256"):
                unchanged.append(path)
            else:
                updates.append({"path": path, "base_sha256": current_hash, "sha256": new.get("sha256"), "bytes": new.get("bytes")})
            continue
        if new is None:
            continue
        if exists:
            if path in repo_harness_install.PRESERVE_EXISTING_CONSUMER_PATHS | repo_harness_install.MERGE_EXISTING_CONSUMER_PATHS:
                preserved.append(path)
            else:
                collisions.append({"path": path, "reason": "unknown file occupies a new upstream-managed destination"})
        else:
            additions.append({"path": path, "sha256": new.get("sha256"), "bytes": new.get("bytes")})
    managed_new_rows = [row for row in new_rows if str(row.get("path")) not in set(preserved)]
    return {
        "updates": updates,
        "additions": additions,
        "deletions": deletions,
        "unchanged": unchanged,
        "preserved": preserved,
        "project_overlay": overlay_paths,
        "collisions": collisions,
        "managed_new_rows": managed_new_rows,
    }


def transaction_id(commit: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{commit[:12]}-{uuid.uuid4().hex[:8]}"


def transaction_path(root: Path, identifier: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", identifier):
        raise RuntimeError("unsafe transaction id")
    try:
        return repo_harness_paths.HarnessPathGuard(root, label="consumer").check(
            f"{TRANSACTIONS_REL}/{identifier}", operation="harness-transaction-directory"
        )
    except repo_harness_paths.UnsafeHarnessPathError as exc:
        raise RuntimeError(f"unsafe transaction directory: {exc}") from exc


def read_transaction(directory: Path) -> dict[str, object]:
    guard = repo_harness_paths.HarnessPathGuard(directory, label="transaction")
    try:
        payload = json.loads(guard.read_text("transaction.json", operation="harness-transaction-read"))
    except (json.JSONDecodeError, repo_harness_paths.UnsafeHarnessPathError) as exc:
        raise RuntimeError(f"could not read transaction: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("transaction.json must contain an object")
    return payload


def validated_transaction_operations(
    root: Path,
    transaction: dict[str, object],
    directory: Path,
) -> tuple[list[dict[str, object]], repo_harness_paths.HarnessPathGuard, repo_harness_paths.HarnessPathGuard]:
    raw_operations = transaction.get("operations")
    if not isinstance(raw_operations, list):
        raise RuntimeError("transaction operations must be a list")
    root_guard = repo_harness_paths.HarnessPathGuard(root, label="consumer")
    backup_guard = repo_harness_paths.HarnessPathGuard(directory / "backup", label="transaction-backup")
    operations: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_operations):
        if not isinstance(raw, dict):
            raise RuntimeError(f"transaction operations[{index}] must be an object")
        kind = str(raw.get("kind", ""))
        if kind not in {"add", "update", "delete"}:
            raise RuntimeError(f"transaction operations[{index}] has invalid kind {kind!r}")
        try:
            relative = repo_harness_paths.normalize_relative_path(raw.get("path"))
            root_guard.check(relative, operation="harness-transaction-target")
            backup_guard.check(relative, operation="harness-transaction-backup")
        except (ValueError, repo_harness_paths.UnsafeHarnessPathError) as exc:
            raise RuntimeError(f"unsafe transaction operation path: {exc}") from exc
        if relative == LOCK_REL or repo_harness_install.is_state_path(relative):
            raise RuntimeError(f"transaction operation cannot target harness state: {relative}")
        if relative in seen:
            raise RuntimeError(f"transaction contains duplicate operation path: {relative}")
        seen.add(relative)
        row = dict(raw)
        row["path"] = relative
        if kind != "delete" and not re.fullmatch(r"[0-9a-f]{64}", str(row.get("post_sha256", ""))):
            raise RuntimeError(f"transaction operation has invalid post hash: {relative}")
        operations.append(row)
    return operations, root_guard, backup_guard


def restore_transaction(root: Path, transaction: dict[str, object], directory: Path, *, verify_post: bool) -> list[str]:
    collisions: list[str] = []
    operations, root_guard, backup_guard = validated_transaction_operations(root, transaction, directory)
    if verify_post:
        for row in operations:
            relative = str(row["path"])
            kind = row.get("kind")
            if kind == "delete":
                if root_guard.exists(relative, operation="harness-rollback-verify"):
                    collisions.append(f"{relative}: expected deleted path to remain absent")
            elif (
                not root_guard.is_file(relative, operation="harness-rollback-verify")
                or root_guard.sha256(relative, operation="harness-rollback-verify") != row.get("post_sha256")
            ):
                collisions.append(f"{relative}: changed after harness update")
        if (
            not root_guard.is_file(LOCK_REL, operation="harness-rollback-lock-verify")
            or root_guard.sha256(LOCK_REL, operation="harness-rollback-lock-verify")
            != transaction.get("applied_lock_sha256")
        ):
            collisions.append(f"{LOCK_REL}: changed after harness update")
        policy_row = transaction.get("project_policy")
        if isinstance(policy_row, dict) and policy_row.get("migrated"):
            policy_path = root / ".agents" / "project-policy.json"
            if not policy_path.is_file() or sha256_file(policy_path) != policy_row.get("post_sha256"):
                collisions.append(".agents/project-policy.json: changed after harness update")
    if collisions:
        return collisions
    for row in reversed(operations):
        relative = str(row["path"])
        target = root_guard.check(relative, operation="harness-rollback-write")
        if row.get("kind") == "add":
            if root_guard.exists(relative, operation="harness-rollback-write"):
                target.unlink()
        elif backup_guard.is_file(relative, operation="harness-rollback-backup-read"):
            backup = backup_guard.check(relative, operation="harness-rollback-backup-read")
            atomic_copy(backup, target)
    transaction_guard = repo_harness_paths.HarnessPathGuard(directory, label="transaction")
    old_lock = transaction_guard.check("old-lock.json", operation="harness-rollback-old-lock-read")
    lock_path = root_guard.check(LOCK_REL, operation="harness-rollback-lock-write")
    if transaction_guard.is_file("old-lock.json", operation="harness-rollback-old-lock-read"):
        atomic_copy(old_lock, lock_path)
    elif root_guard.exists(LOCK_REL, operation="harness-rollback-lock-write"):
        lock_path.unlink()
    policy_row = transaction.get("project_policy")
    policy_backup = directory / "project-policy.json"
    if isinstance(policy_row, dict) and policy_backup.is_file():
        atomic_copy(policy_backup, root / ".agents" / "project-policy.json")
    return []


def apply_update(root: Path, source_root: Path, new_lock: dict[str, object], classification: dict[str, object]) -> dict[str, object]:
    identifier = transaction_id(str(new_lock.get("commit", "")))
    directory = ensure_consumer_directory(
        root, f"{TRANSACTIONS_REL}/{identifier}", operation="harness-transaction-create"
    )
    old_lock_path = root / LOCK_REL
    if old_lock_path.is_file():
        shutil.copy2(old_lock_path, directory / "old-lock.json")
    operations: list[dict[str, object]] = []
    for kind, key in (("update", "updates"), ("delete", "deletions"), ("add", "additions")):
        for raw in classification.get(key, []):
            row = dict(raw)
            row["kind"] = kind
            if kind != "delete":
                row["post_sha256"] = row.get("sha256")
            operations.append(row)
    transaction = {
        "schema_version": 1,
        "tool": "harness-update-transaction",
        "id": identifier,
        "status": "prepared",
        "created_at": utc_now(),
        "operations": operations,
    }
    policy_path = root / ".agents" / "project-policy.json"
    if policy_path.is_file():
        try:
            policy_data = json.loads(policy_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            policy_data = {}
        if isinstance(policy_data, dict) and policy_data.get("schema_version") == 1:
            shutil.copy2(policy_path, directory / "project-policy.json")
            transaction["project_policy"] = {
                "path": ".agents/project-policy.json",
                "pre_sha256": sha256_file(policy_path),
                "migrated": False,
            }
    atomic_write_bytes(directory / "transaction.json", (json.dumps(transaction, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    for row in operations:
        if row["kind"] in {"update", "delete"}:
            backup = repo_harness_paths.HarnessPathGuard(
                directory / "backup", label="transaction-backup"
            ).ensure_parent(str(row["path"]), operation="harness-transaction-backup-write")
            shutil.copy2(root / str(row["path"]), backup)
    try:
        for row in operations:
            relative = str(row["path"])
            target = root / relative
            if row["kind"] == "delete":
                target.unlink()
            else:
                atomic_copy(source_root / relative, target)
        lock_bytes = (json.dumps(new_lock, indent=2, sort_keys=True) + "\n").encode("utf-8")
        atomic_write_bytes(old_lock_path, lock_bytes)
        transaction["status"] = "applied"
        transaction["applied_lock_sha256"] = hashlib.sha256(lock_bytes).hexdigest()
        transaction["applied_at"] = utc_now()
        atomic_write_bytes(directory / "transaction.json", (json.dumps(transaction, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    except OSError as exc:
        restore_transaction(root, transaction, directory, verify_post=False)
        transaction["status"] = "auto-restored"
        transaction["error"] = str(exc)
        atomic_write_bytes(directory / "transaction.json", (json.dumps(transaction, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        raise RuntimeError(f"update write failed and was automatically restored: {exc}") from exc
    return transaction


def migrate_project_policy_after_update(root: Path, transaction_id_value: str) -> dict[str, object]:
    directory = root / TRANSACTIONS_REL / transaction_id_value
    try:
        transaction = json.loads((directory / "transaction.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "status": "failed", "output": f"transaction metadata unavailable: {exc}"}
    policy_row = transaction.get("project_policy")
    if not isinstance(policy_row, dict):
        return {"ok": True, "status": "not-required"}
    command = [sys.executable, "-B", ".agents/manage.py", "policy", "migrate", "--format", "json"]
    try:
        completed = subprocess.run(command, cwd=root, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        restore_transaction(root, transaction, directory, verify_post=False)
        return {"ok": False, "status": "auto-restored", "output": str(exc)}
    if completed.returncode != 0:
        restore_transaction(root, transaction, directory, verify_post=False)
        return {"ok": False, "status": "auto-restored", "returncode": completed.returncode, "output": completed.stdout[-4000:]}
    policy_path = root / ".agents" / "project-policy.json"
    migrated_document, migration_issues, migration_exists = repo_policy.load_project_policy(root)
    if (
        not migration_exists
        or migrated_document.get("schema_version") != repo_policy.SCHEMA_VERSION
        or migrated_document.get("$schema") != repo_policy.INSTANCE_SCHEMA
        or migration_issues
    ):
        restore_transaction(root, transaction, directory, verify_post=False)
        details = "; ".join(migration_issues) or "migration command did not produce a canonical schema-v2 project policy"
        transaction["status"] = "auto-restored"
        transaction["error"] = details
        atomic_write_bytes(
            directory / "transaction.json",
            (json.dumps(transaction, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        return {
            "ok": False,
            "status": "auto-restored",
            "returncode": completed.returncode,
            "output": (completed.stdout[-3000:] + "\nPostcondition failed: " + details).strip(),
        }
    policy_row["migrated"] = True
    policy_row["post_sha256"] = sha256_file(policy_path)
    transaction["project_policy"] = policy_row
    atomic_write_bytes(directory / "transaction.json", (json.dumps(transaction, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {"ok": True, "status": "migrated", "output": completed.stdout[-4000:]}


def setup_verification(root: Path) -> dict[str, object]:
    command = [sys.executable, "-B", ".agents/manage.py", "setup", "--check", "--no-link-skills", "--offline", "--format", "json", "--summary", "--compact"]
    try:
        completed = subprocess.run(command, cwd=root, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "status": "failed-to-start", "output": str(exc)}
    return {"ok": completed.returncode == 0, "status": "passed" if completed.returncode == 0 else "failed", "returncode": completed.returncode, "output": completed.stdout[-4000:]}


def update_report(
    root: Path,
    *,
    requested: str,
    apply: bool,
    archive: str | None = None,
    archive_metadata: str | None = None,
    expected_commit: str | None = None,
    expected_payload_digest: str | None = None,
) -> dict[str, object]:
    lock = read_lock(root)
    repository, tag, commit, archive_path, metadata = target_reference(
        root, lock, requested=requested, archive=archive, archive_metadata=archive_metadata
    )
    if expected_commit and commit != expected_commit:
        raise RuntimeError(
            f"previewed tag {tag} moved from {expected_commit} to {commit}; refusing to apply a different commit"
        )
    current_tag = str(lock.get("tag", ""))
    if not archive and SEMVER_RE.fullmatch(current_tag):
        current_commit = resolve_annotated_tag(repository, current_tag)
        if current_commit != lock.get("commit"):
            raise RuntimeError(
                f"moved tag detected: {current_tag} was locked to {lock.get('commit')} but now resolves to {current_commit}"
            )
    if tag == lock.get("tag") and commit != lock.get("commit"):
        raise RuntimeError(f"moved tag detected: {tag} was locked to {lock.get('commit')} but now resolves to {commit}")
    extraction_parent = ensure_consumer_directory(
        root, f"{UPDATE_STATE_REL}/cache/extracted", operation="harness-archive-extraction-cache"
    )
    with tempfile.TemporaryDirectory(prefix="harness-", dir=extraction_parent) as temporary:
        source_root = safe_extract_archive(archive_path, Path(temporary))
        verify_archive_commit_root(source_root, commit)
        files, selected, rows, digest = selected_payload(source_root, lock)
        if metadata and str(metadata.get("payload_digest", "")) != digest:
            raise RuntimeError("local archive payload digest does not match its metadata")
        if expected_payload_digest and digest != expected_payload_digest:
            raise RuntimeError("target payload digest changed after preview; refusing to apply")
        classification = classify_update(root, source_root, lock, rows)
        managed_rows = classification.pop("managed_new_rows")
        new_lock = make_lock(
            repository=repository,
            tag=tag,
            commit=commit,
            selected_profile=selected,
            digest=digest,
            rows=managed_rows,
        )
        collisions = classification.get("collisions", [])
        report = {
            "schema_version": 1,
            "tool": "harness-update",
            "ok": not collisions,
            "status": "blocked" if collisions else "preview",
            "apply_requested": apply,
            "current": {"tag": lock.get("tag"), "commit": lock.get("commit"), "payload_digest": lock.get("payload_digest")},
            "target": {"tag": tag, "commit": commit, "payload_digest": digest},
            "profile": selected,
            **classification,
            "summary": {
                "updated": len(classification["updates"]),
                "added": len(classification["additions"]),
                "deleted": len(classification["deletions"]),
                "unchanged": len(classification["unchanged"]),
                "preserved": len(classification["preserved"]),
                "collisions": len(collisions),
            },
            "transaction": None,
            "verification": None,
        }
        if apply and not collisions:
            transaction = apply_update(root, source_root, new_lock, classification)
            report["transaction"] = transaction["id"]
            report["status"] = "applied"
            report["policy_migration"] = migrate_project_policy_after_update(root, str(transaction["id"]))
            if not report["policy_migration"].get("ok"):
                report["ok"] = False
                report["status"] = "policy-migration-failed-auto-restored"
                return report
            report["verification"] = setup_verification(root)
            report["ok"] = bool(report["verification"].get("ok"))
            if not report["ok"]:
                report["status"] = "applied-verification-failed"
        return report


def status_report(root: Path, *, check_upstream: bool, offline: bool = False) -> dict[str, object]:
    lock = read_lock(root)
    repository = normalized_repository(lock.get("repository"))
    tags = read_tag_cache(root, repository)
    source = "cache" if tags else "none"
    moved_tag = False
    if check_upstream and not offline:
        tags = fetch_stable_tags(repository)
        write_tag_cache(root, repository, tags)
        source = "upstream"
        if SEMVER_RE.fullmatch(str(lock.get("tag", ""))):
            moved_tag = resolve_annotated_tag(repository, str(lock["tag"])) != lock.get("commit")
    latest = tags[0] if tags else ""
    current = str(lock.get("tag", ""))
    update_available = bool(latest and SEMVER_RE.fullmatch(current) and stable_version_key(latest) > stable_version_key(current))
    return {
        "schema_version": 1,
        "tool": "harness-status",
        "ok": not moved_tag,
        "status": "moved-tag-blocked" if moved_tag else "update-available" if update_available else "current" if latest else "upstream-not-checked",
        "repository": repository,
        "current_tag": current,
        "current_commit": lock.get("commit", ""),
        "available_stable_tag": latest,
        "tag_index_source": source,
        "update_available": update_available,
        "moved_tag": moved_tag,
    }


def rollback_report(root: Path, *, transaction: str) -> dict[str, object]:
    directory = transaction_path(root, transaction)
    payload = read_transaction(directory)
    if payload.get("status") != "applied":
        raise RuntimeError(f"transaction {transaction} is not in applied state")
    collisions = restore_transaction(root, payload, directory, verify_post=True)
    if collisions:
        return {"schema_version": 1, "tool": "harness-rollback", "ok": False, "status": "blocked", "transaction": transaction, "collisions": collisions}
    payload["status"] = "rolled-back"
    payload["rolled_back_at"] = utc_now()
    atomic_write_bytes(directory / "transaction.json", (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {"schema_version": 1, "tool": "harness-rollback", "ok": True, "status": "rolled-back", "transaction": transaction}


def adopt_report(
    root: Path,
    *,
    tag: str,
    archive: str | None = None,
    archive_metadata: str | None = None,
) -> dict[str, object]:
    if (root / LOCK_REL).exists():
        raise RuntimeError(f"{LOCK_REL} already exists")
    consumer_guard = repo_harness_paths.HarnessPathGuard(root, label="consumer")
    try:
        legacy_path = consumer_guard.check(LEGACY_LOCK_REL, operation="harness-adopt-legacy-read")
        legacy = json.loads(consumer_guard.read_text(LEGACY_LOCK_REL, operation="harness-adopt-legacy-read"))
    except (json.JSONDecodeError, repo_harness_paths.UnsafeHarnessPathError) as exc:
        raise RuntimeError(f"could not read legacy harness manifest: {exc}") from exc
    if not isinstance(legacy, dict):
        raise RuntimeError(f"{LEGACY_LOCK_REL} must contain an object")
    legacy_source = Path(str(legacy.get("source_root", ""))).expanduser()
    legacy_source_metadata = (
        repo_harness_install.source_repository_metadata(legacy_source)
        if legacy_source.is_dir()
        else {}
    )
    repository = normalized_repository(
        legacy.get("repository") or legacy_source_metadata.get("repository") or DEFAULT_REPOSITORY
    )
    provisional = {
        "tool": "harness-lock",
        "repository": repository,
        "install": {
            "profile": str((legacy.get("profile") or {}).get("name", "standard")) if isinstance(legacy.get("profile"), dict) else "standard"
        },
    }
    repository, resolved_tag, commit, archive_path, metadata = target_reference(
        root, provisional, requested=tag, archive=archive, archive_metadata=archive_metadata
    )
    update_state = ensure_consumer_directory(root, UPDATE_STATE_REL, operation="harness-adopt-state")
    with tempfile.TemporaryDirectory(prefix="harness-adopt-", dir=update_state) as temporary:
        source_root = safe_extract_archive(archive_path, Path(temporary))
        verify_archive_commit_root(source_root, commit)
        files, selected, rows, digest = selected_payload(source_root, provisional)
        if metadata and str(metadata.get("payload_digest", "")) != digest:
            raise RuntimeError("local archive payload digest does not match its metadata")
        legacy_digest = str(legacy.get("resolved_manifest_digest", ""))
        if legacy_digest and legacy_digest != digest:
            raise RuntimeError("legacy manifest payload digest does not match the selected tag")
        legacy_rows = repo_harness_install.manifest_rows(legacy)
        source_rows = {str(row["path"]): row for row in rows}
        managed_rows = []
        for path, row in source_rows.items():
            target = root / path
            consumer_root_paths = repo_harness_install.PRESERVE_EXISTING_CONSUMER_PATHS | repo_harness_install.MERGE_EXISTING_CONSUMER_PATHS
            if path not in legacy_rows and path in consumer_root_paths:
                continue
            if path in consumer_root_paths and path in legacy_rows and target.is_file():
                current_hash = sha256_file(target)
                legacy_hash = str(legacy_rows[path].get("sha256", ""))
                if current_hash == legacy_hash and legacy_hash != row.get("sha256"):
                    continue
            if path not in legacy_rows or not target.is_file() or sha256_file(target) != row.get("sha256"):
                raise RuntimeError(f"legacy installation does not match {resolved_tag} at {path}")
            managed_rows.append(row)
        new_lock = make_lock(repository=repository, tag=resolved_tag, commit=commit, selected_profile=selected, digest=digest, rows=managed_rows)
        adopted_dir = ensure_consumer_directory(
            root, f"{UPDATE_STATE_REL}/adopted", operation="harness-adopt-backup-directory"
        )
        adopted_backup = repo_harness_paths.HarnessPathGuard(
            adopted_dir, label="adopted-backup"
        ).check_file_destination("harness-install.json", operation="harness-adopt-backup-write")
        shutil.copy2(legacy_path, adopted_backup)
        atomic_write_bytes(root / LOCK_REL, (json.dumps(new_lock, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        legacy_path.unlink()
    return {"schema_version": 1, "tool": "harness-adopt", "ok": True, "status": "adopted", "tag": resolved_tag, "commit": commit, "payload_digest": digest, "legacy_backup": f"{UPDATE_STATE_REL}/adopted/harness-install.json"}


def release_tag_report(root: Path, *, tag: str | None = None) -> dict[str, object]:
    def git_value(*arguments: str) -> str:
        try:
            completed = subprocess.run(
                ["git", "-C", str(root), *arguments],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"git {' '.join(arguments)} failed: {exc}") from exc
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"git {' '.join(arguments)} failed")
        return completed.stdout.strip()

    selected_tag = tag or git_value("describe", "--tags", "--exact-match")
    version = stable_version_key(selected_tag)
    issues: list[str] = []
    if version[0] < 1:
        issues.append("the first supported harness release is v1.0.0")
    object_type = git_value("cat-file", "-t", f"refs/tags/{selected_tag}")
    if object_type != "tag":
        issues.append("harness release tag must be annotated")
    commit = git_value("rev-list", "-n", "1", f"refs/tags/{selected_tag}").lower()
    head = git_value("rev-parse", "HEAD").lower()
    if commit != head:
        issues.append("release tag does not point to the checked-out commit")
    dirty = git_value("status", "--porcelain")
    if dirty:
        issues.append("release worktree is not clean")

    profile_digests: dict[str, str] = {}
    manifest, manifest_issues = repo_harness_install.load_payload_manifest(root)
    issues.extend(manifest_issues)
    profiles = manifest.get("profiles") if isinstance(manifest.get("profiles"), dict) else {}
    for profile in sorted(profiles):
        profile_issues: list[str] = []
        effective, _selected = repo_harness_install.effective_payload_manifest(manifest, profile, profile_issues)
        files, _excluded = repo_harness_install.iter_payload_candidates(root, effective) if not profile_issues else ([], [])
        _rows, digest = repo_harness_profiles.source_file_manifest(root, files)
        issues.extend(f"{profile}: {issue}" for issue in profile_issues)
        if not profile_issues:
            profile_digests[profile] = digest
    return {
        "schema_version": 1,
        "tool": "harness-release-check",
        "ok": not issues,
        "status": "passed" if not issues else "blocked",
        "tag": selected_tag,
        "commit": commit,
        "annotated": object_type == "tag",
        "profile_payload_digests": profile_digests,
        "issues": issues,
    }


def print_report(report: dict[str, object], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"{report.get('tool')}: {report.get('status')}")
    if isinstance(report.get("current"), dict):
        print(f"  Current: {report['current'].get('tag')} ({report['current'].get('commit')})")
    if isinstance(report.get("target"), dict):
        print(f"  Target: {report['target'].get('tag')} ({report['target'].get('commit')})")
    if isinstance(report.get("summary"), dict):
        print("  " + ", ".join(f"{key}={value}" for key, value in report["summary"].items()))
    for label, key in (
        ("Updated", "updates"),
        ("Added", "additions"),
        ("Deleted", "deletions"),
        ("Preserved", "preserved"),
    ):
        rows = report.get(key, [])
        if not isinstance(rows, list) or not rows:
            continue
        print(f"  {label}:")
        for row in rows:
            path = row.get("path", "") if isinstance(row, dict) else row
            print(f"    - {path}")
    for collision in report.get("collisions", []):
        if isinstance(collision, dict):
            print(f"  COLLISION {collision.get('path')}: {collision.get('reason')}")
        else:
            print(f"  COLLISION {collision}")
    for issue in report.get("issues", []):
        print(f"  ERROR: {issue}")
