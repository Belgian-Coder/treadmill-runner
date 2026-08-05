"""Promote selected harness-owned consumer edits back to the source harness."""

from __future__ import annotations

import json
from pathlib import Path

from repo_support import repo_harness_install
from repo_support import repo_harness_paths
from repo_support import repo_harness_profiles


VALIDATION_COMMANDS = [
    "python -B .agents/manage.py check-additions",
    "python -B .agents/manage.py sync --check",
    "python -B .agents/manage.py check-changed --summary --compact --format json",
    "python -B .agents/manage.py check",
]


def read_install_manifest(target_root: Path) -> dict[str, object]:
    return repo_harness_install.read_install_manifest(target_root)


def normalize_relative_path(value: str) -> str:
    try:
        return repo_harness_paths.normalize_relative_path(value)
    except ValueError as exc:
        raise ValueError(f"unsafe path: {value}: {exc}") from exc


def project_local_context_path(path: str) -> bool:
    return path == "docs/project" or path.startswith("docs/project/")


def never_promotable_reason(path: str, payload_manifest: dict[str, object] | None = None) -> str:
    if project_local_context_path(path):
        return "project-local context/evidence is never promotable by default"
    if repo_harness_install.is_state_path(path, payload_manifest):
        return "ignored local state is never promotable"
    if path in {
        ".agents/harness-install-plan.json",
        ".agents/harness-install-plan.md",
        ".agents/harness.lock.json",
        ".agents/harness-install.json",
    }:
        return "generated install plan or manifest is never promotable"
    return ""


def payload_source_hashes(
    source_root: Path,
    profile: str,
    issues: list[str],
    *,
    with_features: list[str] | None = None,
    without_features: list[str] | None = None,
    source_guard: repo_harness_paths.HarnessPathGuard | None = None,
    unsafe_paths: list[dict[str, str]] | None = None,
) -> tuple[dict[str, str], dict[str, object], dict[str, object], list[dict[str, object]], str]:
    source_guard = source_guard or repo_harness_paths.HarnessPathGuard(source_root, label="source")
    unsafe_paths = unsafe_paths if unsafe_paths is not None else []
    manifest, manifest_issues = repo_harness_install.load_payload_manifest(
        source_root,
        path_guard=source_guard,
        unsafe_paths=unsafe_paths,
    )
    issues.extend(manifest_issues)
    manifest, selected_profile = repo_harness_install.effective_payload_manifest(
        manifest,
        profile,
        issues,
        with_features=with_features,
        without_features=without_features,
    )
    files: list[Path] = []
    if not issues and not unsafe_paths:
        files, _excluded = repo_harness_install.iter_payload_candidates(
            source_root,
            manifest,
            path_guard=source_guard,
            unsafe_paths=unsafe_paths,
        )
    rows, digest = repo_harness_profiles.source_file_manifest(source_root, files, unsafe_paths=unsafe_paths)
    hashes = {
        str(row.get("path")): str(row.get("sha256"))
        for row in rows
        if isinstance(row, dict) and str(row.get("path", "")) and str(row.get("sha256", ""))
    }
    return hashes, manifest, selected_profile, rows, digest


def classify_file(
    path: str,
    *,
    source_hash: str | None,
    target_hash: str | None,
    installed_hash: str | None,
    source_exists: bool,
    target_exists: bool,
    payload_manifest: dict[str, object],
) -> dict[str, object]:
    never_reason = never_promotable_reason(path, payload_manifest)
    if never_reason:
        classification = "project-local-context-evidence" if project_local_context_path(path) else "ignored-local-state"
        return {
            "path": path,
            "classification": classification,
            "promotable": False,
            "reason": never_reason,
        }
    if not installed_hash:
        return {
            "path": path,
            "classification": "not-installed",
            "promotable": False,
            "reason": "path is not recorded as harness-owned in the consumer install manifest",
        }
    if not source_exists:
        return {
            "path": path,
            "classification": "target-only-installed",
            "promotable": False,
            "reason": "source path no longer exists in the harness payload",
        }
    if not target_exists:
        return {
            "path": path,
            "classification": "target-missing",
            "promotable": False,
            "reason": "consumer path is missing",
        }
    if source_hash == target_hash:
        return {"path": path, "classification": "unchanged", "promotable": False, "reason": "source and consumer match"}
    source_changed = source_hash != installed_hash
    consumer_changed = target_hash != installed_hash
    if source_changed and consumer_changed:
        classification = "both-changed-diverged"
        promotable = False
        reason = "source and consumer both differ from the installed baseline"
    elif source_changed:
        classification = "source-changed-only"
        promotable = False
        reason = "source changed since install; update the consumer before promoting"
    elif consumer_changed:
        classification = "consumer-changed-only"
        promotable = True
        reason = "consumer changed from the installed baseline and source is unchanged"
    else:
        classification = "unchanged"
        promotable = False
        reason = "source and consumer match the installed baseline"
    return {
        "path": path,
        "classification": classification,
        "promotable": promotable,
        "reason": reason,
    }


def classify_harness_files(
    source_root: Path,
    target_root: Path,
    *,
    profile: str,
    with_features: list[str] | None,
    without_features: list[str] | None,
    issues: list[str],
    source_guard: repo_harness_paths.HarnessPathGuard,
    target_guard: repo_harness_paths.HarnessPathGuard,
    unsafe_paths: list[dict[str, str]],
) -> tuple[list[dict[str, object]], dict[str, object], dict[str, object], list[dict[str, object]], str]:
    source_hashes, payload_manifest, selected_profile, resolved_file_manifest, resolved_manifest_digest = payload_source_hashes(
        source_root,
        profile,
        issues,
        with_features=with_features,
        without_features=without_features,
        source_guard=source_guard,
        unsafe_paths=unsafe_paths,
    )
    manifest = repo_harness_install.read_install_manifest(
        target_root,
        path_guard=target_guard,
        unsafe_paths=unsafe_paths,
        manifest_issues=issues,
    )
    installed_rows = repo_harness_install.validated_manifest_rows(manifest, target_guard, unsafe_paths, issues)
    installed_hashes = {
        path: str(row.get("sha256", "")).strip()
        for path, row in installed_rows.items()
        if str(row.get("sha256", "")).strip()
    }
    if not manifest:
        issues.append(f"missing or unreadable {repo_harness_install.INSTALL_MANIFEST_REL} in target")
    paths = sorted(set(source_hashes) | set(installed_hashes)) if not unsafe_paths else []
    rows: list[dict[str, object]] = []
    for path in paths:
        try:
            source_exists = source_guard.is_file(path, operation="promote-source-stat")
            target_exists = target_guard.is_file(path, operation="promote-target-stat")
        except repo_harness_paths.UnsafeHarnessPathError as exc:
            repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
            continue
        if source_exists and path not in source_hashes:
            rows.append(
                {
                    "path": path,
                    "classification": "outside-selected-profile",
                    "promotable": False,
                    "reason": "path is outside the resolved profile/feature selection",
                }
            )
            continue
        source_hash = source_hashes.get(path) if source_exists else None
        target_hash = target_guard.sha256(path, operation="promote-target-hash") if target_exists else None
        row = classify_file(
            path,
            source_hash=source_hash,
            target_hash=target_hash,
            installed_hash=installed_hashes.get(path),
            source_exists=source_exists,
            target_exists=target_exists,
            payload_manifest=payload_manifest,
        )
        rows.append(row)
    return rows, payload_manifest, selected_profile, resolved_file_manifest, resolved_manifest_digest


def summary_for_rows(rows: list[dict[str, object]]) -> dict[str, int]:
    classifications = {}
    for row in rows:
        key = str(row.get("classification", "unknown"))
        classifications[key] = classifications.get(key, 0) + 1
    return {
        "file_count": len(rows),
        "promotable_files": sum(1 for row in rows if row.get("promotable") is True),
        "diverged_files": classifications.get("both-changed-diverged", 0),
        "source_changed_only_files": classifications.get("source-changed-only", 0),
        "consumer_changed_only_files": classifications.get("consumer-changed-only", 0),
        "ignored_local_state_files": classifications.get("ignored-local-state", 0),
        "project_context_files": classifications.get("project-local-context-evidence", 0),
        "outside_selected_profile_files": classifications.get("outside-selected-profile", 0),
    }


def selected_path_issues(
    path: str,
    *,
    source_root: Path,
    target_root: Path,
    installed_hashes: dict[str, str],
    payload_manifest: dict[str, object],
    selected_source_paths: set[str],
    source_guard: repo_harness_paths.HarnessPathGuard,
    target_guard: repo_harness_paths.HarnessPathGuard,
) -> list[str]:
    issues: list[str] = []
    reason = never_promotable_reason(path, payload_manifest)
    if reason:
        issues.append(f"{path}: {reason}")
    if path not in installed_hashes:
        issues.append(f"{path}: path is not harness-owned in {repo_harness_install.INSTALL_MANIFEST_REL}")
    if path not in selected_source_paths:
        issues.append(f"{path}: path is outside the resolved profile/feature selection")
    if not target_guard.is_file(path, operation="promote-selected-target-stat"):
        issues.append(f"{path}: consumer file is missing")
    source_parent = "/".join(path.split("/")[:-1])
    if source_parent and not source_guard.is_dir(source_parent, operation="promote-selected-source-parent-stat"):
        issues.append(f"{path}: source parent does not exist")
    return issues


def harness_promote_report(
    source_root: Path,
    target_root: Path,
    *,
    profile: str = "standard",
    with_features: list[str] | None = None,
    without_features: list[str] | None = None,
    dry_run: bool = True,
    apply: bool = False,
    paths: list[str] | None = None,
) -> dict[str, object]:
    source_guard = repo_harness_paths.HarnessPathGuard(source_root, label="source")
    target_guard = repo_harness_paths.HarnessPathGuard(target_root, label="target")
    source_root = source_guard.root
    target_root = target_guard.root
    issues: list[str] = []
    unsafe_paths: list[dict[str, str]] = []
    try:
        source_is_dir = source_guard.root_is_dir(operation="promote-source-root-check")
    except repo_harness_paths.UnsafeHarnessPathError as exc:
        repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
        source_is_dir = False
    try:
        target_is_dir = target_guard.root_is_dir(operation="promote-target-root-check")
    except repo_harness_paths.UnsafeHarnessPathError as exc:
        repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
        target_is_dir = False
    if not source_is_dir and not unsafe_paths:
        issues.append(f"source root does not exist: {source_root}")
    if not target_is_dir and not unsafe_paths:
        issues.append(f"target root does not exist: {target_root}")
    if not unsafe_paths:
        try:
            root_relation = repo_harness_paths.root_relationship(
                source_root,
                target_root,
                operation="promote-root-relationship",
            )
        except repo_harness_paths.UnsafeHarnessPathError as exc:
            repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
        else:
            if root_relation.overlaps:
                issues.append("target must be outside the source harness tree and its parents")
    normalized_paths: list[str] = []
    seen_normalized_paths: set[str] = set()
    if apply:
        if not paths:
            issues.append("harness-promote --apply requires --paths")
        for raw_path in paths or []:
            try:
                normalized = normalize_relative_path(raw_path)
                if normalized in seen_normalized_paths:
                    continue
                seen_normalized_paths.add(normalized)
                normalized_paths.append(normalized)
                source_guard.check_file_destination(normalized, operation="promote-source-destination-preflight")
                target_guard.check(normalized, operation="promote-target-read-preflight")
            except repo_harness_paths.UnsafeHarnessPathError as exc:
                repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
            except ValueError as exc:
                issues.append(str(exc))
    if unsafe_paths:
        issues.append(f"unsafe-path-blocked: {len(unsafe_paths)} unsafe path access(es) rejected")
    rows: list[dict[str, object]] = []
    payload_manifest: dict[str, object] = {}
    selected_profile: dict[str, object] = {"name": profile, "features": []}
    resolved_file_manifest: list[dict[str, object]] = []
    resolved_manifest_digest = ""
    if not issues:
        rows, payload_manifest, selected_profile, resolved_file_manifest, resolved_manifest_digest = classify_harness_files(
            source_root,
            target_root,
            profile=profile,
            with_features=with_features,
            without_features=without_features,
            issues=issues,
            source_guard=source_guard,
            target_guard=target_guard,
            unsafe_paths=unsafe_paths,
        )
    if unsafe_paths and not any(issue.startswith("unsafe-path-blocked:") for issue in issues):
        issues.append(f"unsafe-path-blocked: {len(unsafe_paths)} unsafe path access(es) rejected")

    copied: list[str] = []
    if apply:
        manifest = repo_harness_install.read_install_manifest(
            target_root,
            path_guard=target_guard,
            unsafe_paths=unsafe_paths,
            manifest_issues=issues,
        )
        installed_rows = repo_harness_install.validated_manifest_rows(manifest, target_guard, unsafe_paths, issues)
        installed_hashes = {
            path: str(row.get("sha256", "")).strip()
            for path, row in installed_rows.items()
            if str(row.get("sha256", "")).strip()
        }
        selected_source_paths = {
            str(row.get("path"))
            for row in resolved_file_manifest
            if isinstance(row, dict) and str(row.get("path", "")).strip()
        }
        classifications = {
            str(row.get("path")): row
            for row in rows
            if isinstance(row, dict) and str(row.get("path", "")).strip()
        }
        if not unsafe_paths:
            for path in normalized_paths:
                try:
                    issues.extend(
                        selected_path_issues(
                            path,
                            source_root=source_root,
                            target_root=target_root,
                            installed_hashes=installed_hashes,
                            payload_manifest=payload_manifest,
                            selected_source_paths=selected_source_paths,
                            source_guard=source_guard,
                            target_guard=target_guard,
                        )
                    )
                except repo_harness_paths.UnsafeHarnessPathError as exc:
                    repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
                classification = classifications.get(path, {})
                if classification.get("classification") != "consumer-changed-only" or classification.get("promotable") is not True:
                    issues.append(
                        f"{path}: promotion requires consumer-changed-only classification; "
                        f"got {classification.get('classification', 'unavailable')}"
                    )
        if unsafe_paths and not any(issue.startswith("unsafe-path-blocked:") for issue in issues):
            issues.append(f"unsafe-path-blocked: {len(unsafe_paths)} unsafe path access(es) rejected")
        if not issues:
            for path in normalized_paths:
                try:
                    source_exists = source_guard.is_file(path, operation="promote-immediate-source-stat")
                    target_exists = target_guard.is_file(path, operation="promote-immediate-target-stat")
                    current = classify_file(
                        path,
                        source_hash=source_guard.sha256(path, operation="promote-immediate-source-hash")
                        if source_exists
                        else None,
                        target_hash=target_guard.sha256(path, operation="promote-immediate-target-hash")
                        if target_exists
                        else None,
                        installed_hash=installed_hashes.get(path),
                        source_exists=source_exists,
                        target_exists=target_exists,
                        payload_manifest=payload_manifest,
                    )
                    if current.get("classification") != "consumer-changed-only" or current.get("promotable") is not True:
                        issues.append(
                            f"{path}: promotion state changed; expected consumer-changed-only, "
                            f"got {current.get('classification', 'unavailable')}"
                        )
                        break
                    source_guard.copy_from(target_guard, path, operation="harness-promote-copy")
                    copied.append(path)
                except repo_harness_paths.UnsafeHarnessPathError as exc:
                    repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
                    issues.append(f"unsafe-path-blocked: {exc}")

    issues = list(dict.fromkeys(issues))
    ok = not issues
    status = "unsafe-path-blocked" if unsafe_paths else "applied" if apply and ok else "blocked" if issues else "planned"
    return {
        "schema_version": 1,
        "tool": "harness-promote",
        "ok": ok,
        "status": status,
        "source_root": str(source_root),
        "target_root": str(target_root),
        "profile": profile,
        "resolved_features": list(selected_profile.get("features", [])),
        "resolved_file_manifest": resolved_file_manifest,
        "resolved_manifest_digest": resolved_manifest_digest,
        "unsafe_paths": repo_harness_paths.sorted_unsafe_paths(unsafe_paths),
        "dry_run": dry_run and not apply,
        "apply": apply,
        "files": rows,
        "selected_paths": normalized_paths,
        "copied": copied,
        "issues": issues,
        "summary": summary_for_rows(rows),
        "validation_commands": VALIDATION_COMMANDS if apply and ok else [],
    }


def render_harness_promote(report: dict[str, object]) -> str:
    lines = [
        "# Harness Promote",
        "",
        f"- Status: {report.get('status')}",
        f"- Source: `{report.get('source_root')}`",
        f"- Target: `{report.get('target_root')}`",
        f"- Profile: `{report.get('profile')}`",
        f"- Resolved features: {', '.join(str(item) for item in report.get('resolved_features', [])) or 'none'}",
        f"- Resolved manifest SHA-256: `{report.get('resolved_manifest_digest', '')}`",
    ]
    summary = report.get("summary")
    if isinstance(summary, dict):
        lines.extend(
            [
                f"- Promotable files: {summary.get('promotable_files', 0)}",
                f"- Diverged files: {summary.get('diverged_files', 0)}",
            ]
        )
    issues = report.get("issues")
    if isinstance(issues, list) and issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {item}" for item in issues)
    files = report.get("files")
    if isinstance(files, list) and files:
        lines.extend(["", "## Files", "", "| Path | Classification | Promotable |", "|---|---|---|"])
        for row in files[:40]:
            if isinstance(row, dict):
                lines.append(f"| `{row.get('path')}` | {row.get('classification')} | {str(row.get('promotable')).lower()} |")
        if len(files) > 40:
            lines.append(f"| ... | {len(files) - 40} more | |")
    validation = report.get("validation_commands")
    if isinstance(validation, list) and validation:
        lines.extend(["", "## Validation", ""])
        lines.extend(f"- `{command}`" for command in validation)
    return "\n".join(lines) + "\n"


def print_report(report: dict[str, object], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_harness_promote(report), end="")
