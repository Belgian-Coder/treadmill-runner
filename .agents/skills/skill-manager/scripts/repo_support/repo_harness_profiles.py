#!/usr/bin/env python3
"""Resolve harness feature bundles and deterministic source manifests."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

from repo_support import repo_harness_paths


NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PAYLOAD_SCHEMA_VERSION = 2
PROFILE_FIELDS = {
    "description",
    "extends",
    "features",
    "exclude_features",
    "alias_of",
    "exclude_globs",
    "state_exclude_globs",
}
FEATURE_FIELDS = {"description", "include_globs", "exclude_globs", "requires"}


def _name(value: object, field: str, issues: list[str]) -> str:
    text = str(value).strip() if isinstance(value, str) else ""
    if not text or not NAME_PATTERN.fullmatch(text):
        issues.append(f"payload manifest {field} contains an invalid name: {value!r}")
        return ""
    return text


def _names(value: object, field: str, issues: list[str], *, allow_string: bool = False) -> list[str]:
    if allow_string and isinstance(value, str):
        values: object = [value]
    else:
        values = value
    if values is None:
        return []
    if not isinstance(values, list):
        issues.append(f"payload manifest {field} must be a list" + (" or string" if allow_string else ""))
        return []
    result: list[str] = []
    for item in values:
        name = _name(item, field, issues)
        if name and name not in result:
            result.append(name)
    return result


def _glob(value: object, field: str, issues: list[str]) -> str:
    if not isinstance(value, str):
        issues.append(f"payload manifest {field} entries must be strings")
        return ""
    text = value.replace("\\", "/").strip()
    if (
        not text
        or text.startswith("/")
        or Path(text).is_absolute()
        or (len(text) >= 2 and text[1] == ":")
        or any(part == ".." for part in text.split("/"))
    ):
        issues.append(f"payload manifest {field} contains an unsafe glob: {value}")
        return ""
    return text.rstrip("/")


def _globs(value: object, field: str, issues: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        issues.append(f"payload manifest {field} must be a list")
        return []
    result: list[str] = []
    for item in value:
        pattern = _glob(item, field, issues)
        if pattern and pattern not in result:
            result.append(pattern)
    return result


def normalize_feature_bundles(payload: dict[str, object], issues: list[str]) -> dict[str, dict[str, object]]:
    raw_bundles = payload.get("feature_bundles")
    if not isinstance(raw_bundles, dict) or not raw_bundles:
        issues.append("payload manifest feature_bundles must be a non-empty object for schema_version 2")
        return {}
    bundles: dict[str, dict[str, object]] = {}
    for raw_name, raw_bundle in raw_bundles.items():
        name = _name(raw_name, "feature_bundles", issues)
        if not name:
            continue
        if not isinstance(raw_bundle, dict):
            issues.append(f"payload manifest feature bundle {name} must be an object")
            continue
        unknown = sorted(set(raw_bundle) - FEATURE_FIELDS)
        for field in unknown:
            issues.append(f"payload manifest feature bundle {name} has unknown field: {field}")
        include_globs = _globs(raw_bundle.get("include_globs"), f"feature_bundles.{name}.include_globs", issues)
        if not include_globs:
            issues.append(f"payload manifest feature bundle {name} include_globs must not be empty")
        bundles[name] = {
            "description": str(raw_bundle.get("description") or ""),
            "include_globs": include_globs,
            "exclude_globs": _globs(
                raw_bundle.get("exclude_globs", []),
                f"feature_bundles.{name}.exclude_globs",
                issues,
            ),
            "requires": _names(raw_bundle.get("requires", []), f"feature_bundles.{name}.requires", issues),
        }
    for name, bundle in bundles.items():
        for dependency in bundle.get("requires", []):
            if dependency not in bundles:
                issues.append(f"payload manifest feature {name} requires unknown feature: {dependency}")
    return bundles


def normalize_profiles(
    payload: dict[str, object],
    issues: list[str],
) -> dict[str, dict[str, object]]:
    raw_profiles = payload.get("profiles")
    if raw_profiles in ({}, None):
        issues.append("payload manifest profiles must be a non-empty object for schema_version 2")
        return {}
    if not isinstance(raw_profiles, dict):
        issues.append("payload manifest profiles must be an object")
        return {}
    profiles: dict[str, dict[str, object]] = {}
    for raw_name, raw_profile in raw_profiles.items():
        name = _name(raw_name, "profiles", issues)
        if not name:
            continue
        if not isinstance(raw_profile, dict):
            issues.append(f"payload manifest profile {name} must be an object")
            continue
        for field in sorted(set(raw_profile) - PROFILE_FIELDS):
            issues.append(f"payload manifest profile {name} has unknown field: {field}")
        alias_of = _name(raw_profile.get("alias_of"), f"profiles.{name}.alias_of", issues) if raw_profile.get("alias_of") is not None else ""
        extends = _names(raw_profile.get("extends", []), f"profiles.{name}.extends", issues, allow_string=True)
        features = _names(raw_profile.get("features", []), f"profiles.{name}.features", issues)
        exclude_features = _names(
            raw_profile.get("exclude_features", []),
            f"profiles.{name}.exclude_features",
            issues,
        )
        exclude_globs = _globs(raw_profile.get("exclude_globs", []), f"profiles.{name}.exclude_globs", issues)
        state_exclude_globs = _globs(
            raw_profile.get("state_exclude_globs", []),
            f"profiles.{name}.state_exclude_globs",
            issues,
        )
        if alias_of and (extends or features or exclude_features or exclude_globs or state_exclude_globs):
            issues.append(
                f"payload manifest profile {name} alias_of cannot be combined with "
                "extends/features/exclude_features/exclude_globs/state_exclude_globs"
            )
        profiles[name] = {
            "description": str(raw_profile.get("description") or ""),
            "extends": extends,
            "features": features,
            "exclude_features": exclude_features,
            "alias_of": alias_of,
            "exclude_globs": exclude_globs,
            "state_exclude_globs": state_exclude_globs,
        }
    return profiles


def _validate_graph_cycles(
    nodes: Iterable[str],
    edges: dict[str, list[str]],
    issues: list[str],
    *,
    label: str,
) -> None:
    complete: set[str] = set()
    visiting: list[str] = []
    reported: set[frozenset[str]] = set()

    def visit(node: str) -> None:
        if node in complete:
            return
        if node in visiting:
            start = visiting.index(node)
            cycle = (*visiting[start:], node)
            key = frozenset(cycle)
            if key not in reported:
                reported.add(key)
                issues.append(f"{label}: {' -> '.join(cycle)}")
            return
        visiting.append(node)
        for child in edges.get(node, []):
            visit(child)
        visiting.pop()
        complete.add(node)

    for node in sorted(nodes):
        visit(node)


def normalize_contract(
    payload: dict[str, object],
    issues: list[str],
) -> dict[str, object]:
    schema_version = payload.get("schema_version")
    if schema_version != PAYLOAD_SCHEMA_VERSION:
        issues.append(f"payload manifest schema_version must be {PAYLOAD_SCHEMA_VERSION}")
        return {
            "required_features": [],
            "feature_bundles": {},
            "profiles": {},
        }
    profiles = normalize_profiles(payload, issues)
    bundles = normalize_feature_bundles(payload, issues)
    required = _names(payload.get("required_features", []), "required_features", issues)
    if not required:
        issues.append("payload manifest required_features must not be empty for schema_version 2")
    for feature in required:
        if feature not in bundles:
            issues.append(f"payload manifest required core feature is unknown: {feature}")
    for name, profile in profiles.items():
        for parent in profile.get("extends", []):
            if parent not in profiles:
                issues.append(f"payload manifest profile {name} extends unknown profile: {parent}")
        alias = str(profile.get("alias_of") or "")
        if alias and alias not in profiles:
            issues.append(f"payload manifest profile {name} aliases unknown profile: {alias}")
        for field in ("features", "exclude_features"):
            for feature in profile.get(field, []):
                if feature not in bundles:
                    issues.append(f"payload manifest profile {name} references unknown feature: {feature}")
    _validate_graph_cycles(
        profiles,
        {
            name: [
                *[str(item) for item in profile.get("extends", [])],
                *([str(profile.get("alias_of"))] if profile.get("alias_of") else []),
            ]
            for name, profile in profiles.items()
        },
        issues,
        label="profile inheritance cycle",
    )
    _validate_graph_cycles(
        bundles,
        {name: [str(item) for item in bundle.get("requires", [])] for name, bundle in bundles.items()},
        issues,
        label="feature dependency cycle",
    )
    return {
        "required_features": required,
        "feature_bundles": bundles,
        "profiles": profiles,
    }


def _profile_state(
    name: str,
    profiles: dict[str, dict[str, object]],
    issues: list[str],
    stack: tuple[str, ...] = (),
) -> tuple[set[str], set[str], list[str], list[str]]:
    if name in stack:
        issues.append(f"profile inheritance cycle: {' -> '.join((*stack, name))}")
        return set(), set(), [], []
    profile = profiles.get(name)
    if not isinstance(profile, dict):
        return set(), set(), [], []
    alias = str(profile.get("alias_of") or "")
    if alias:
        return _profile_state(alias, profiles, issues, (*stack, name))
    features: set[str] = set()
    excluded: set[str] = set()
    exclude_globs: list[str] = []
    state_exclude_globs: list[str] = []
    for parent in profile.get("extends", []):
        parent_features, parent_excluded, parent_globs, parent_state_globs = _profile_state(
            str(parent),
            profiles,
            issues,
            (*stack, name),
        )
        features.update(parent_features)
        excluded.update(parent_excluded)
        exclude_globs.extend(item for item in parent_globs if item not in exclude_globs)
        state_exclude_globs.extend(item for item in parent_state_globs if item not in state_exclude_globs)
    own_features = {str(item) for item in profile.get("features", [])}
    own_excluded = {str(item) for item in profile.get("exclude_features", [])}
    overlap = sorted(own_features & own_excluded)
    for feature in overlap:
        issues.append(f"payload manifest profile {name} both includes and excludes feature: {feature}")
    features.update(own_features)
    excluded.update(own_excluded)
    features.difference_update(excluded)
    for pattern in profile.get("exclude_globs", []):
        if pattern not in exclude_globs:
            exclude_globs.append(str(pattern))
    for pattern in profile.get("state_exclude_globs", []):
        if pattern not in state_exclude_globs:
            state_exclude_globs.append(str(pattern))
    return features, excluded, exclude_globs, state_exclude_globs


def _dependency_closure(
    selected: set[str],
    excluded: set[str],
    bundles: dict[str, dict[str, object]],
    issues: list[str],
) -> set[str]:
    resolved: set[str] = set()
    visiting: list[str] = []

    def visit(feature: str, parent: str = "") -> None:
        if feature in resolved:
            return
        if feature in visiting:
            start = visiting.index(feature)
            issues.append(f"feature dependency cycle: {' -> '.join((*visiting[start:], feature))}")
            return
        if feature in excluded:
            if parent:
                issues.append(f"feature {feature} is excluded but required by selected feature {parent}")
            return
        bundle = bundles.get(feature)
        if not isinstance(bundle, dict):
            return
        visiting.append(feature)
        for dependency in bundle.get("requires", []):
            visit(str(dependency), feature)
        visiting.pop()
        resolved.add(feature)

    for feature in sorted(selected):
        visit(feature)
    return resolved


def resolve_profile(
    contract: dict[str, object],
    profile: str,
    *,
    with_features: Iterable[str] = (),
    without_features: Iterable[str] = (),
    issues: list[str],
) -> dict[str, object]:
    requested = str(profile or "standard").strip()
    profiles = contract.get("profiles") if isinstance(contract.get("profiles"), dict) else {}
    if requested not in profiles:
        issues.append(f"profile-unavailable: {requested}")
        return {
            "name": requested,
            "resolved_profile": "",
            "description": "",
            "features": [],
            "excluded_features": [],
            "required_features": [],
            "exclude_globs": [],
            "state_exclude_globs": [],
        }
    raw_with = [str(item).strip() for item in with_features if str(item).strip()]
    raw_without = [str(item).strip() for item in without_features if str(item).strip()]
    with_names = set(raw_with)
    without_names = set(raw_without)
    for feature in sorted(with_names | without_names):
        if not NAME_PATTERN.fullmatch(feature):
            issues.append(f"invalid feature name: {feature}")
    overlap = sorted(with_names & without_names)
    for feature in overlap:
        issues.append(f"feature {feature} cannot be both included and excluded")

    selected, excluded, exclude_globs, state_globs = _profile_state(requested, profiles, issues)
    profile_row = profiles[requested]
    resolved_profile = requested
    seen_aliases: list[str] = []
    while isinstance(profile_row, dict) and profile_row.get("alias_of"):
        if resolved_profile in seen_aliases:
            issues.append(f"profile inheritance cycle: {' -> '.join((*seen_aliases, resolved_profile))}")
            break
        seen_aliases.append(resolved_profile)
        resolved_profile = str(profile_row.get("alias_of"))
        profile_row = profiles.get(resolved_profile, {})

    bundles = contract.get("feature_bundles") if isinstance(contract.get("feature_bundles"), dict) else {}
    for feature in sorted(with_names | without_names):
        if feature not in bundles:
            issues.append(f"unknown feature: {feature}")
    excluded.difference_update(with_names)
    selected.update(with_names)
    excluded.update(without_names)
    selected.difference_update(excluded)
    required = {str(item) for item in contract.get("required_features", [])}
    for feature in sorted(required & excluded):
        issues.append(f"required core feature cannot be removed: {feature}")
    selected.update(required)
    resolved = _dependency_closure(selected, excluded, bundles, issues)
    for feature in sorted(resolved):
        bundle = bundles.get(feature, {})
        if not isinstance(bundle, dict):
            continue
        for pattern in bundle.get("exclude_globs", []):
            if pattern not in exclude_globs:
                exclude_globs.append(str(pattern))
    requested_row = profiles.get(requested, {})
    return {
        "name": requested,
        "resolved_profile": resolved_profile,
        "description": str(requested_row.get("description") or "") if isinstance(requested_row, dict) else "",
        "features": sorted(resolved),
        "excluded_features": sorted(excluded),
        "required_features": sorted(required),
        "exclude_globs": exclude_globs,
        "state_exclude_globs": state_globs,
    }


def path_matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(path + "/", pattern)


def select_files(
    source_root: Path,
    candidates: Iterable[Path],
    contract: dict[str, object],
    resolution: dict[str, object],
) -> list[Path]:
    files = sorted(candidates, key=lambda item: item.relative_to(source_root).as_posix())
    bundles = contract.get("feature_bundles") if isinstance(contract.get("feature_bundles"), dict) else {}
    include_globs: list[str] = []
    for feature in resolution.get("features", []):
        bundle = bundles.get(str(feature), {})
        if not isinstance(bundle, dict):
            continue
        for pattern in bundle.get("include_globs", []):
            if pattern not in include_globs:
                include_globs.append(str(pattern))
    excluded_feature_globs: list[str] = []
    for feature in resolution.get("excluded_features", []):
        bundle = bundles.get(str(feature), {})
        if not isinstance(bundle, dict):
            continue
        for pattern in bundle.get("include_globs", []):
            if pattern not in excluded_feature_globs:
                excluded_feature_globs.append(str(pattern))
    protected_globs: list[str] = []
    for feature in resolution.get("required_features", []):
        bundle = bundles.get(str(feature), {})
        if not isinstance(bundle, dict):
            continue
        for pattern in bundle.get("include_globs", []):
            if pattern not in protected_globs:
                protected_globs.append(str(pattern))
    excludes = [str(item) for item in resolution.get("exclude_globs", [])]
    selected: list[Path] = []
    for path in files:
        relative = path.relative_to(source_root).as_posix()
        if not any(path_matches(relative, pattern) for pattern in include_globs):
            continue
        if any(path_matches(relative, pattern) for pattern in excludes):
            continue
        removed = any(path_matches(relative, pattern) for pattern in excluded_feature_globs)
        protected = any(path_matches(relative, pattern) for pattern in protected_globs)
        if removed and not protected:
            continue
        selected.append(path)
    return selected


def source_file_manifest(
    source_root: Path,
    files: Iterable[Path],
    *,
    unsafe_paths: list[dict[str, str]] | None = None,
) -> tuple[list[dict[str, object]], str]:
    guard = repo_harness_paths.HarnessPathGuard(source_root, label="source")
    rows: list[dict[str, object]] = []
    for path in sorted(files, key=lambda item: item.relative_to(guard.root).as_posix()):
        relative = path.relative_to(guard.root).as_posix()
        try:
            rows.append(
                {
                    "path": relative,
                    "bytes": guard.stat_size(relative, operation="source-manifest-stat"),
                    "sha256": guard.sha256(relative, operation="source-manifest-hash"),
                }
            )
        except repo_harness_paths.UnsafeHarnessPathError as exc:
            if unsafe_paths is None:
                raise
            repo_harness_paths.add_unsafe_path(unsafe_paths, exc)
    canonical = json.dumps(rows, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return rows, hashlib.sha256(canonical).hexdigest()


def feature_flag_parts(with_features: Iterable[str] = (), without_features: Iterable[str] = ()) -> list[str]:
    parts: list[str] = []
    for feature in dict.fromkeys(str(item).strip() for item in with_features if str(item).strip()):
        parts.extend(["--with-feature", feature])
    for feature in dict.fromkeys(str(item).strip() for item in without_features if str(item).strip()):
        parts.extend(["--without-feature", feature])
    return parts


def feature_flag_text(with_features: Iterable[str] = (), without_features: Iterable[str] = ()) -> str:
    parts = feature_flag_parts(with_features, without_features)
    return (" " + " ".join(parts)) if parts else ""
