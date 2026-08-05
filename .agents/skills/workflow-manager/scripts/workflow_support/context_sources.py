"""Resolve declarative ContextSpec sources into concrete workflow files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import workflow_manager_common as common
from workflow_support.context_budget import relative_file_token_estimate


def _substitute(value: str, workflow_name: str, run_id: str) -> str:
    replacements = {
        "<workflow-name>": workflow_name,
        "<workflow>": workflow_name,
        "{workflow_name}": workflow_name,
        "{workflow}": workflow_name,
        "<run-id>": run_id,
        "{run_id}": run_id,
    }
    result = value.replace("\\", "/")
    for marker, replacement in replacements.items():
        result = result.replace(marker, replacement)
    return result


def _safe_repo_relative(root: Path, value: str) -> Path | None:
    relative = Path(value)
    if not value.strip() or relative.is_absolute() or ".." in relative.parts:
        return None
    candidate = root / relative
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def _resolved_within(boundary: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(boundary.resolve())
    except (OSError, ValueError):
        return False
    return True


def _matching_files(
    root: Path,
    source: dict[str, Any],
    *,
    workflow_name: str,
    run_id: str,
) -> tuple[list[Path], list[str]]:
    issues: list[str] = []
    declared_path = source.get("path")
    declared_pattern = source.get("pattern")
    if isinstance(declared_path, str) == isinstance(declared_pattern, str):
        return [], ["source must declare exactly one of path or pattern"]
    if isinstance(declared_path, str):
        value = _substitute(declared_path, workflow_name, run_id)
        candidate = _safe_repo_relative(root, value)
        if candidate is None:
            return [], [f"source path is unsafe: {declared_path}"]
        return ([candidate] if candidate.is_file() else []), issues
    value = _substitute(str(declared_pattern), workflow_name, run_id)
    if Path(value).is_absolute() or ".." in Path(value).parts:
        return [], [f"source pattern is unsafe: {declared_pattern}"]
    try:
        matches: list[Path] = []
        for path in root.glob(value):
            if not _resolved_within(root, path):
                issues.append(
                    f"source pattern matched unsafe path outside repository: "
                    f"{common.relative(root, path)}"
                )
                continue
            if path.is_file():
                matches.append(path)
    except (OSError, ValueError) as exc:
        return [], [f"source pattern is invalid: {exc}"]
    return sorted(matches, key=lambda path: path.as_posix()), issues


def resolve_context_sources(
    root: Path,
    workflow_name: str,
    run_dir: Path,
    context_spec: object,
) -> tuple[list[dict[str, object]], list[str]]:
    """Resolve ContextSpec paths/patterns without workflow-name branches."""

    if not isinstance(context_spec, dict):
        return [], []
    raw_sources = context_spec.get("sources")
    if not isinstance(raw_sources, list):
        return [], ["context.sources must be a list"]
    rows: list[dict[str, object]] = []
    issues: list[str] = []
    for index, raw_source in enumerate(raw_sources):
        if not isinstance(raw_source, dict):
            issues.append(f"context.sources[{index}] must be an object")
            continue
        source_id = str(raw_source.get("id", "")).strip()
        paths, source_issues = _matching_files(
            root,
            raw_source,
            workflow_name=workflow_name,
            run_id=run_dir.name,
        )
        issues.extend(f"context source {source_id or index}: {issue}" for issue in source_issues)
        files = [relative_file_token_estimate(root, path) for path in paths]
        if not files:
            continue
        rows.append(
            {
                "id": source_id,
                "artifact_role": str(raw_source.get("artifact_role", "")).strip(),
                "load_policy": str(raw_source.get("load_policy", "")).strip(),
                "critical_category": str(raw_source.get("critical_category", "")).strip(),
                "budget_ref": str(raw_source.get("budget_ref", "")).strip(),
                "preserve_coordinates": raw_source.get("preserve_coordinates") is True,
                "declared": str(raw_source.get("path") or raw_source.get("pattern") or ""),
                "files": files,
                "tokens_estimated": sum(int(item.get("tokens_estimated", 0)) for item in files),
            }
        )
    return rows, issues


def source_file_paths(
    sources: list[dict[str, object]],
    *,
    load_policy: str | None = None,
    preserve_coordinates: bool | None = None,
    artifact_role: str | None = None,
    critical_category: str | None = None,
) -> list[str]:
    paths: list[str] = []
    for source in sources:
        if load_policy is not None and source.get("load_policy") != load_policy:
            continue
        if preserve_coordinates is not None and source.get("preserve_coordinates") is not preserve_coordinates:
            continue
        if artifact_role is not None and source.get("artifact_role") != artifact_role:
            continue
        if critical_category is not None and source.get("critical_category") != critical_category:
            continue
        files = source.get("files") if isinstance(source.get("files"), list) else []
        for item in files:
            if isinstance(item, dict):
                path = str(item.get("path", "")).strip()
                if path and path not in paths:
                    paths.append(path)
    return paths
