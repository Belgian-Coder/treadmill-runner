#!/usr/bin/env python3
"""Build a compact query-focused source navigation summary."""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import navigation_core

TOOL_NAME = "repo-navigation.source-focus"
DEPENDENCY_TOOL_NAME = "repo-navigation.dependency-query"
WORD_PATTERN = re.compile(r"[A-Za-z0-9_]+")
DOTNET_NAMESPACE_PATTERN = re.compile(
    r"^\s*namespace\s+([A-Za-z_][A-Za-z0-9_.]*)\s*[;{]",
    re.MULTILINE,
)
SOURCE_SUFFIXES = (".py", ".cs", ".js", ".jsx", ".ts", ".tsx")
JSTS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx")
GENERIC_MODULE_TARGETS = {
    "__future__",
    "argparse",
    "ast",
    "collections",
    "contextlib",
    "ctypes",
    "dataclasses",
    "datetime",
    "functools",
    "hashlib",
    "importlib",
    "io",
    "itertools",
    "json",
    "math",
    "os",
    "pathlib",
    "re",
    "shlex",
    "shutil",
    "sqlite3",
    "subprocess",
    "sys",
    "tempfile",
    "time",
    "typing",
    "urllib",
}
BROAD_MODULE_SUFFIXES = ("_support", "_routing")
LOW_SIGNAL_QUERY_TERMS = {"local", "ai", "repo", "project", "code", "source", "file", "files"}
MAX_EVIDENCE_PER_FILE = 3
DEPENDENCY_MAX_FILES = 5000


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def query_terms(query: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for match in WORD_PATTERN.finditer(query.lower()):
        term = match.group(0)
        if len(term) < 2 or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def match_score(text: str, terms: list[str], weight: int) -> int:
    haystack = text.lower()
    return sum(weight for term in terms if term in haystack)


def matched_terms(text: str, terms: list[str]) -> set[str]:
    haystack = text.lower()
    return {term for term in terms if term in haystack}


def specific_terms(terms: list[str]) -> set[str]:
    return {term for term in terms if term not in LOW_SIGNAL_QUERY_TERMS}


def compound_path_score(path: str, terms: list[str], weight: int) -> int:
    haystack = path.lower()
    score = 0
    for left, right in zip(terms, terms[1:]):
        if len(left) < 2 or len(right) < 2:
            continue
        if f"{left}-{right}" in haystack or f"{left}_{right}" in haystack:
            score += weight
    return score


def query_requests_tests(terms: list[str]) -> bool:
    return any("test" in term or term in {"spec", "suite", "eval", "validation"} for term in terms)


def is_test_path(path: str) -> bool:
    normalized = path.lower().replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    return (
        "/tests/" in normalized
        or "/test/" in normalized
        or name.startswith("test_")
        or name in {"run_self_tests.py", "self_tests.py"}
        or name.endswith(("_test.py", ".spec.ts", ".spec.js"))
    )


def is_generic_relationship_target(target: str) -> bool:
    normalized = target.lower()
    if normalized.startswith("module:"):
        module = normalized.split(":", 1)[1].split(".", 1)[0]
        return module in GENERIC_MODULE_TARGETS or module.endswith(BROAD_MODULE_SUFFIXES)
    return False


def relationship_target_score(target: str, terms: list[str]) -> int:
    if is_generic_relationship_target(target):
        return 0
    return match_score(target, terms, 1)


def path_identity_terms(path: str) -> set[str]:
    identities: set[str] = set()
    for part in path.replace("\\", "/").lower().split("/"):
        if not part:
            continue
        identities.add(part)
        if "." in part:
            identities.add(part.rsplit(".", 1)[0])
    return identities


def exact_path_score(path: str, terms: list[str], weight: int) -> int:
    identities = path_identity_terms(path)
    return sum(weight for term in terms if len(term) >= 8 and term in identities)


def source_role_score(path: str, terms: list[str]) -> int:
    name = Path(path).name.lower()
    if name.endswith("_impl.py") and "impl" not in terms:
        return 4
    return 0


def relationship_paths(scan: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    by_path: dict[str, list[dict[str, str]]] = {}
    for item in scan.get("relationships", []):
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", ""))
        target = str(item.get("target", ""))
        if source:
            by_path.setdefault(source, []).append(
                {
                    "type": str(item.get("type", "")),
                    "target": target,
                    "evidence": str(item.get("evidence", "")),
                }
            )
        if target and not target.startswith(("module:", "namespace:", "route:")):
            by_path.setdefault(target, []).append(
                {
                    "type": f"reverse_{item.get('type', '')}",
                    "target": source,
                    "evidence": str(item.get("evidence", "")),
                }
            )
    return by_path


def normalize_repo_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def scanned_source_paths(scan: dict[str, Any]) -> list[str]:
    return sorted(
        {
            normalize_repo_path(str(item.get("path", "")))
            for item in scan.get("entries", [])
            if isinstance(item, dict)
            and item.get("type") == "file"
            and str(item.get("path", "")).lower().endswith(SOURCE_SUFFIXES)
        }
    )


def resolve_requested_path(target: Path, scan: dict[str, Any], requested: str) -> tuple[str, list[str]]:
    value = requested.strip()
    requested_path = Path(value).expanduser()
    if requested_path.is_absolute():
        try:
            value = requested_path.resolve().relative_to(target).as_posix()
        except ValueError:
            return "", []
    normalized = normalize_repo_path(value)
    source_paths = scanned_source_paths(scan)
    exact = [path for path in source_paths if path.casefold() == normalized.casefold()]
    if len(exact) == 1:
        return exact[0], exact
    suffix = f"/{normalized.casefold()}"
    matches = [
        path
        for path in source_paths
        if path.casefold().endswith(suffix) or Path(path).name.casefold() == normalized.casefold()
    ]
    return (matches[0], matches) if len(matches) == 1 else ("", matches)


def code_path_candidates(base: str, suffixes: tuple[str, ...]) -> list[str]:
    normalized = normalize_repo_path(posixpath.normpath(base))
    if normalized == ".":
        normalized = ""
    if Path(normalized).suffix.lower() in SOURCE_SUFFIXES:
        return [normalized]
    rows = [f"{normalized}{suffix}" for suffix in suffixes]
    if suffixes == (".py",):
        rows.append(f"{normalized}/__init__.py")
    else:
        rows.extend(f"{normalized}/index{suffix}" for suffix in suffixes)
    return rows


def existing_resolution(paths: set[str], candidates: list[str]) -> tuple[list[str], bool]:
    exact = sorted({candidate for candidate in candidates if candidate in paths})
    if len(exact) == 1:
        return exact, False
    if len(exact) > 1:
        return [], False
    matches = sorted(
        {
            path
            for candidate in candidates
            for path in paths
            if path.casefold() == candidate.casefold()
        }
    )
    return (matches, True) if len(matches) == 1 else ([], False)


def unique_existing(paths: set[str], candidates: list[str]) -> list[str]:
    matches, _casefold_only = existing_resolution(paths, candidates)
    return matches


def namespace_index(target: Path, source_paths: list[str]) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for path in source_paths:
        if not path.lower().endswith(".cs"):
            continue
        text = navigation_core.read_text(
            target / path,
            limit=navigation_core.RELATIONSHIP_CONTENT_LIMIT_BYTES,
        )
        for match in DOTNET_NAMESPACE_PATTERN.finditer(text):
            rows.setdefault(match.group(1).casefold(), []).append(path)
    return {key: sorted(set(values)) for key, values in rows.items()}


def module_candidates(source: str, module_specifier: str, paths: set[str]) -> tuple[list[str], str, str]:
    source_suffix = Path(source).suffix.lower()
    source_dir = posixpath.dirname(source)
    if source_suffix == ".py":
        level = len(module_specifier) - len(module_specifier.lstrip("."))
        module = module_specifier[level:].replace(".", "/")
        if level:
            base_dir = source_dir
            for _ in range(max(0, level - 1)):
                base_dir = posixpath.dirname(base_dir)
            bases = [posixpath.join(base_dir, module)]
        else:
            bases = [posixpath.join(source_dir, module), module]
        candidates: list[str] = []
        for base in bases:
            candidates.extend(code_path_candidates(base, (".py",)))
        exact, casefold_only = existing_resolution(paths, candidates)
        if exact:
            return exact, (
                "casefold-python-module-path" if casefold_only else "python-module-path"
            ), ("inferred" if casefold_only else "high")
        suffixes = tuple(f"/{item}" for item in code_path_candidates(module, (".py",)))
        matches = sorted(path for path in paths if path.casefold().endswith(tuple(value.casefold() for value in suffixes)))
        if len(matches) == 1:
            return matches, "unique-python-module-suffix", "inferred"
        return [], "unresolved-python-module", "unknown"
    if source_suffix in JSTS_SUFFIXES:
        suffixes = JSTS_SUFFIXES
        if module_specifier.startswith("."):
            exact, casefold_only = existing_resolution(
                paths,
                code_path_candidates(posixpath.join(source_dir, module_specifier), suffixes),
            )
            return (
                (
                    exact,
                    "casefold-relative-js-ts-module" if casefold_only else "relative-js-ts-module",
                    "inferred",
                )
                if exact
                else ([], "unresolved-relative-js-ts-module", "unknown")
            )
        exact = unique_existing(paths, code_path_candidates(module_specifier, suffixes))
        if exact:
            return exact, "root-js-ts-module", "inferred"
        return [], "external-or-aliased-js-ts-module", "unknown"
    return [], "unsupported-module-source", "unknown"


def relationship_location(target: Path, source: str, evidence: str) -> str:
    if not evidence or evidence == "filename heuristic":
        return source
    needle = re.sub(r"\s+", " ", evidence.strip())
    source_text = navigation_core.read_text(
        target / source,
        limit=navigation_core.RELATIONSHIP_CONTENT_LIMIT_BYTES,
    )
    for line_number, line in enumerate(source_text.splitlines(), start=1):
        if needle and needle in re.sub(r"\s+", " ", line.strip()):
            return f"{source}:{line_number}"
    return source


def relationship_provenance(source: str, relationship_type: str) -> str:
    if relationship_type == "tests":
        return "filename-heuristic"
    suffix = Path(source).suffix.lower()
    if suffix == ".py":
        return "python-import-regex"
    if suffix == ".cs":
        return "dotnet-using-regex"
    if suffix in JSTS_SUFFIXES:
        return "js-ts-import-regex"
    return "static-relationship"


def unresolved_classification(source: str, logical_target: str) -> str:
    suffix = Path(source).suffix.lower()
    if logical_target.startswith("module:"):
        module_specifier = logical_target.split(":", 1)[1]
        root_module = module_specifier.lstrip(".").split(".", 1)[0]
        if suffix == ".py" and root_module in sys.stdlib_module_names:
            return "stdlib"
        if suffix in JSTS_SUFFIXES and not module_specifier.startswith("."):
            return "external-or-aliased"
    if logical_target.startswith("namespace:"):
        namespace = logical_target.split(":", 1)[1]
        if namespace.startswith(("System", "Microsoft")):
            return "external"
    return "unresolved"


def resolve_relationship_targets(
    target: Path,
    source: str,
    relationship: dict[str, Any],
    paths: set[str],
    namespaces: dict[str, list[str]],
) -> tuple[list[str], str, str]:
    relationship_type = str(relationship.get("type", ""))
    logical_target = normalize_repo_path(str(relationship.get("target", "")))
    if relationship_type == "tests" and logical_target in paths:
        return [logical_target], "declared-test-target", "high"
    if relationship_type != "imports":
        return [], "unsupported-relationship", "unknown"
    if logical_target.startswith("module:"):
        return module_candidates(source, logical_target.split(":", 1)[1], paths)
    if logical_target.startswith("namespace:"):
        matches = namespaces.get(logical_target.split(":", 1)[1].casefold(), [])
        return (
            (matches, "dotnet-namespace-scope", "inferred")
            if matches
            else ([], "external-or-unresolved-dotnet-namespace", "unknown")
        )
    if logical_target in paths:
        return [logical_target], "declared-file-target", "high"
    return [], "unresolved-logical-target", "unknown"


def dependency_graph(
    target: Path,
    scan: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    source_paths = scanned_source_paths(scan)
    paths = set(source_paths)
    namespaces = namespace_index(target, source_paths)
    outgoing: dict[str, list[dict[str, Any]]] = {}
    reverse: dict[str, list[dict[str, Any]]] = {}
    unresolved: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for relationship in scan.get("relationships", []):
        if not isinstance(relationship, dict):
            continue
        source = normalize_repo_path(str(relationship.get("source", "")))
        relationship_type = str(relationship.get("type", ""))
        if source not in paths or relationship_type not in {"imports", "tests"}:
            continue
        resolved, resolution, confidence = resolve_relationship_targets(
            target,
            source,
            relationship,
            paths,
            namespaces,
        )
        if not resolved:
            logical_target = str(relationship.get("target", ""))
            unresolved.append(
                {
                    "source": source,
                    "type": relationship_type,
                    "target": logical_target,
                    "resolution": resolution,
                    "classification": unresolved_classification(source, logical_target),
                }
            )
            continue
        for resolved_target in resolved:
            if resolved_target == source:
                continue
            key = (source, resolved_target, relationship_type)
            if key in seen:
                continue
            seen.add(key)
            edge = {
                "source": source,
                "target": resolved_target,
                "type": relationship_type,
                "evidence": str(relationship.get("evidence", "")),
                "location": relationship_location(target, source, str(relationship.get("evidence", ""))),
                "provenance": str(relationship.get("provenance_hint", ""))
                or relationship_provenance(source, relationship_type),
                "resolution": resolution,
                "confidence": (
                    "inferred"
                    if relationship.get("confidence_hint") == "inferred" and confidence == "high"
                    else confidence
                ),
            }
            outgoing.setdefault(source, []).append(edge)
            reverse.setdefault(resolved_target, []).append(edge)
    for collection in (outgoing, reverse):
        for key, values in collection.items():
            collection[key] = sorted(values, key=lambda item: (item["source"], item["target"], item["type"]))
    return outgoing, reverse, unresolved


def dependency_result(edge: dict[str, Any], *, path: str, depth: int, via: str) -> dict[str, Any]:
    return {
        "path": path,
        "depth": depth,
        "via": via,
        "relationship_type": edge["type"],
        "location": edge["location"],
        "evidence": edge["evidence"],
        "provenance": edge["provenance"],
        "resolution": edge["resolution"],
        "confidence": edge["confidence"],
    }


def build_dependency_report(
    target: Path,
    *,
    mode: str,
    requested_path: str,
    depth: int = 3,
    limit: int = 40,
    max_files: int = 5000,
) -> dict[str, Any]:
    target = target.expanduser().resolve()
    effective_max_files = max(1, min(max_files, DEPENDENCY_MAX_FILES))
    file_scan_limit = {
        "requested": max_files,
        "effective": effective_max_files,
        "ceiling": DEPENDENCY_MAX_FILES,
        "clamped": effective_max_files != max_files,
    }
    rules = [
        "Use dependency results as conservative route-first evidence, not source truth.",
        "Verify listed files and import or using sites directly before editing or claiming impact.",
        "Fall back to exact search and source reads for reflection, dependency injection, generated code, aliases, or unsupported languages.",
    ]
    scan = navigation_core.build_scan(target, max_files=effective_max_files)
    partial_reasons = []
    if any(
        str(item).startswith("file scan capped at ")
        for item in scan.get("skipped", [])
    ):
        partial_reasons.append("file-count-cap")
    skipped_large_sources = sorted(
        {
            item.removeprefix("skipped large file `").removesuffix("`")
            for raw_item in scan.get("skipped", [])
            if (item := str(raw_item)).startswith("skipped large file `")
            and item.endswith("`")
            and Path(item.removeprefix("skipped large file `").removesuffix("`")).suffix.lower()
            in SOURCE_SUFFIXES
        }
    )
    extraction = scan.get("relationship_extraction", {})
    if isinstance(extraction, dict):
        if extraction.get("relationship_cap_reached"):
            partial_reasons.append("relationship-count-cap")
        if extraction.get("content_capped_files"):
            partial_reasons.append("source-content-cap")
    if skipped_large_sources and "source-content-cap" not in partial_reasons:
        partial_reasons.append("source-content-cap")
    partial_scan = bool(partial_reasons)
    complete = bool(scan.get("ok")) and not partial_scan
    if not scan.get("ok"):
        return {
            "schema_version": 1,
            "tool": DEPENDENCY_TOOL_NAME,
            "ok": False,
            "complete": False,
            "status": "target-not-found",
            "mode": mode,
            "target": str(target),
            "requested_path": requested_path,
            "file_scan_limit": file_scan_limit,
            "resolved_path": "",
            "candidates": [],
            "results": [],
            "rules": rules,
            "partial_reasons": partial_reasons,
            "skipped": scan.get("skipped", []),
        }
    resolved_path, candidates = resolve_requested_path(target, scan, requested_path)
    if not resolved_path:
        return {
            "schema_version": 1,
            "tool": DEPENDENCY_TOOL_NAME,
            "ok": False,
            "complete": complete,
            "status": "ambiguous-path" if candidates else "path-not-found",
            "mode": mode,
            "target": str(target),
            "requested_path": requested_path,
            "file_scan_limit": file_scan_limit,
            "resolved_path": "",
            "candidates": candidates[:20],
            "results": [],
            "rules": rules,
            "partial_reasons": partial_reasons,
            "skipped": scan.get("skipped", []),
        }
    outgoing, reverse, unresolved = dependency_graph(target, scan)
    results: list[dict[str, Any]] = []
    if mode == "deps":
        results = [
            dependency_result(edge, path=edge["target"], depth=1, via=resolved_path)
            for edge in outgoing.get(resolved_path, [])
        ]
    elif mode == "rdeps":
        results = [
            dependency_result(edge, path=edge["source"], depth=1, via=resolved_path)
            for edge in reverse.get(resolved_path, [])
        ]
    elif mode == "impact":
        visited = {resolved_path}
        frontier = [resolved_path]
        for current_depth in range(1, max(1, min(depth, 10)) + 1):
            next_frontier: list[str] = []
            for current in sorted(frontier):
                for edge in reverse.get(current, []):
                    dependent = str(edge["source"])
                    if dependent in visited:
                        continue
                    visited.add(dependent)
                    next_frontier.append(dependent)
                    results.append(dependency_result(edge, path=dependent, depth=current_depth, via=current))
            frontier = sorted(set(next_frontier))
            if not frontier:
                break
    else:
        raise ValueError(f"unsupported dependency mode: {mode}")
    results = sorted(results, key=lambda item: (int(item["depth"]), str(item["path"])))
    total_results = len(results)
    bounded_limit = max(1, min(limit, 200))
    unresolved_for_subject = sorted(
        (item for item in unresolved if item["source"] == resolved_path),
        key=lambda item: (item["classification"] != "unresolved", item["target"]),
    )
    unresolved_classifications: dict[str, int] = {}
    for item in unresolved:
        classification = item["classification"]
        unresolved_classifications[classification] = unresolved_classifications.get(classification, 0) + 1
    if partial_scan:
        status = "partial-scan"
    elif total_results > bounded_limit:
        status = "truncated"
    elif not total_results:
        status = "no-resolved-relationships"
    else:
        status = "resolved"
    return {
        "schema_version": 1,
        "tool": DEPENDENCY_TOOL_NAME,
        "ok": complete,
        "complete": complete,
        "status": status,
        "mode": mode,
        "target": str(target),
        "requested_path": requested_path,
        "file_scan_limit": file_scan_limit,
        "resolved_path": resolved_path,
        "depth": max(1, min(depth, 10)) if mode == "impact" else 1,
        "result_count": total_results,
        "results": results[:bounded_limit],
        "unresolved_subject_relationships": unresolved_for_subject[:8],
        "coverage": {
            "supported_suffixes": list(SOURCE_SUFFIXES),
            "resolved_edge_count": sum(len(values) for values in outgoing.values()),
            "unresolved_or_external_count": len(unresolved),
            "unresolved_or_external_classifications": unresolved_classifications,
            "notes": [
                "Python AST imports resolve by deterministic file paths when unambiguous; case-fold-only paths are inferred and platform-dependent.",
                "JavaScript/TypeScript regex imports ignore comments and strings but remain inferred static evidence.",
                "C# namespace matches are over-approximated and marked inferred.",
                "Dynamic imports, reflection, dependency injection, generated code, and configured aliases may be absent.",
            ],
            "scan_limits": {
                **(extraction if isinstance(extraction, dict) else {}),
                "file_scan_limit": file_scan_limit,
                "skipped_oversized_source_files": skipped_large_sources,
            },
        },
        "rules": rules,
        "partial_reasons": partial_reasons,
        "skipped": scan.get("skipped", []),
    }


def compact_snippet(line: str) -> str:
    max_snippet_chars = navigation_core.project_policy_int("limits.navigation.source_snippet_chars")
    snippet = re.sub(r"\s+", " ", line.strip())
    if len(snippet) <= max_snippet_chars:
        return snippet
    return snippet[: max(1, max_snippet_chars - 3)].rstrip() + "..."


def markdown_code_span(value: object) -> str:
    text = str(value)
    longest_tick_run = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * (longest_tick_run + 1)
    if text.startswith("`") or text.endswith("`"):
        text = f" {text} "
    return f"{fence}{text}{fence}"


def evidence_terms(terms: list[str]) -> list[str]:
    selected = [term for term in terms if term not in LOW_SIGNAL_QUERY_TERMS]
    return selected or terms


def line_evidence_reason(query_hits: list[str], symbol_hits: list[str]) -> str:
    reasons: list[str] = []
    if query_hits:
        reasons.append("query terms " + ", ".join(sorted(query_hits)[:4]))
    if symbol_hits:
        reasons.append("symbol " + ", ".join(sorted(symbol_hits)[:3]))
    return "; ".join(reasons) if reasons else "matched focused source"


def source_line_evidence(
    target: Path,
    path: str,
    terms: list[str],
    matching_symbols: list[dict[str, str]],
) -> list[dict[str, Any]]:
    file_path = target / path
    if not file_path.exists() or not file_path.is_file():
        return []
    selected_terms = evidence_terms(terms)
    symbol_names = sorted(
        {
            str(symbol.get("name", "")).lower()
            for symbol in matching_symbols
            if str(symbol.get("name", "")).strip()
        }
    )
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for line_number, line in enumerate(navigation_core.read_text(file_path, limit=120_000).splitlines(), start=1):
        snippet = compact_snippet(line)
        if not snippet:
            continue
        lowered = snippet.lower()
        query_hits = [term for term in selected_terms if term in lowered]
        symbol_hits = [name for name in symbol_names if name and name in lowered]
        if not query_hits and not symbol_hits:
            continue
        score = len(query_hits) * 4 + len(symbol_hits) * 3
        if len(query_hits) >= min(2, len(selected_terms)):
            score += 4
        candidates.append(
            (
                -score,
                line_number,
                {
                    "location": f"{path}:{line_number}",
                    "line": line_number,
                    "reason": line_evidence_reason(query_hits, symbol_hits),
                    "snippet": snippet,
                },
            )
        )
    evidence: list[dict[str, Any]] = []
    seen_locations: set[str] = set()
    for _, _, item in sorted(candidates, key=lambda row: (row[0], row[1])):
        location = str(item.get("location", ""))
        if location in seen_locations:
            continue
        seen_locations.add(location)
        evidence.append(item)
        if len(evidence) >= MAX_EVIDENCE_PER_FILE:
            break
    return evidence


def build_focus_rows(target: Path, scan: dict[str, Any], terms: list[str], limit: int) -> list[dict[str, Any]]:
    symbols_by_path: dict[str, list[dict[str, str]]] = {}
    for symbol in scan.get("symbols", []):
        if not isinstance(symbol, dict):
            continue
        path = str(symbol.get("path", ""))
        if path:
            symbols_by_path.setdefault(path, []).append(
                {
                    "kind": str(symbol.get("kind", "")),
                    "name": str(symbol.get("name", "")),
                }
            )
    relationships = relationship_paths(scan)
    rows: list[dict[str, Any]] = []
    specific_query_terms = specific_terms(terms)
    for entry in scan.get("entries", []):
        if not isinstance(entry, dict) or entry.get("type") != "file":
            continue
        path = str(entry.get("path", ""))
        if not path:
            continue
        score = 0
        reasons: list[str] = []
        specific_match = False
        path_score = match_score(path, terms, 4)
        if path_score:
            score += path_score
            reasons.append("path matches query")
            specific_match = bool(matched_terms(path, list(specific_query_terms)))
        compound_score = compound_path_score(path, terms, 8)
        if compound_score:
            score += compound_score
            reasons.append("compound path terms match query")
            for left, right in zip(terms, terms[1:]):
                if left in specific_query_terms or right in specific_query_terms:
                    if f"{left}-{right}" in path.lower() or f"{left}_{right}" in path.lower():
                        specific_match = True
        exact_score = exact_path_score(path, terms, 80)
        if exact_score:
            score += exact_score
            reasons.append("exact path term matches query")
            specific_match = True
        role_score = source_role_score(path, terms)
        if role_score:
            score += role_score
            reasons.append("primary implementation filename")
        responsibility = str(entry.get("responsibility", ""))
        responsibility_score = match_score(responsibility, terms, 1)
        if responsibility_score:
            score += responsibility_score
            reasons.append("responsibility matches query")
            specific_match = specific_match or bool(matched_terms(responsibility, list(specific_query_terms)))
        matching_symbols: list[dict[str, str]] = []
        matched_symbol_terms: set[str] = set()
        for symbol in symbols_by_path.get(path, []):
            symbol_text = f"{symbol.get('kind', '')} {symbol.get('name', '')}"
            symbol_matches = {term for term in terms if term in symbol_text.lower()}
            if symbol_matches:
                matched_symbol_terms.update(symbol_matches)
                matching_symbols.append(symbol)
                if symbol_matches & specific_query_terms:
                    specific_match = True
        if matching_symbols:
            score += len(matched_symbol_terms) * 3
            reasons.append("symbol matches query")
        related = [
            relationship
            for relationship in relationships.get(path, [])
            if not is_generic_relationship_target(relationship.get("target", ""))
        ][:6]
        for relationship in related:
            target_score = relationship_target_score(relationship.get("target", ""), terms)
            if target_score:
                score += target_score
                reasons.append("relationship target matches query")
                specific_match = specific_match or bool(
                    matched_terms(relationship.get("target", ""), list(specific_query_terms))
                )
                break
        if is_test_path(path) and not query_requests_tests(terms):
            score -= 20
            reasons.append("test file down-ranked for non-test query")
        if score <= 0:
            continue
        if specific_query_terms and not specific_match:
            continue
        rows.append(
            {
                "path": path,
                "score": score,
                "responsibility": responsibility,
                "matching_symbols": matching_symbols[:8],
                "relationships": related,
                "evidence": source_line_evidence(target, path, terms, matching_symbols[:8]),
                "reasons": sorted(set(reasons)),
            }
        )
    return sorted(rows, key=lambda item: (-int(item["score"]), item["path"]))[: max(1, limit)]


def build_focus_report(target: Path, *, query: str, limit: int = 8, max_files: int = 5000) -> dict[str, Any]:
    target = target.expanduser().resolve()
    terms = query_terms(query)
    scan = navigation_core.build_scan(target, max_files=max_files)
    rows = build_focus_rows(target, scan, terms, limit) if terms else []
    rules = [
        "Use this as route-first navigation evidence, not source truth.",
        "Open only the listed files first, then expand through relationships when needed.",
        "Always reopen focused source files before editing, validating, or explaining behavior.",
    ]
    focus_payload = {
        "query": query,
        "terms": terms,
        "recommended_files": rows,
        "rules": rules,
        "skipped": scan.get("skipped", [])[:20],
    }
    full_payload = {
        "entries": scan.get("entries", []),
        "symbols": scan.get("symbols", []),
        "relationships": scan.get("relationships", []),
    }
    focus_tokens = estimate_tokens(json.dumps(focus_payload, sort_keys=True))
    full_tokens = estimate_tokens(json.dumps(full_payload, sort_keys=True))
    return {
        "schema_version": 1,
        "tool": TOOL_NAME,
        "ok": bool(scan.get("ok")) and bool(rows),
        "status": "focused" if rows else "no-query-match",
        "target": str(target),
        "query": query,
        "terms": terms,
        "recommended_files": rows,
        "rules": rules,
        "focus_token_estimate": focus_tokens,
        "full_map_token_estimate": full_tokens,
        "saved_vs_full_map_tokens": max(0, full_tokens - focus_tokens),
        "checks": [
            "used deterministic repo-navigation scan",
            "ranked path, responsibility, symbol, and relationship matches",
            "kept source reopen rule",
        ],
        "skipped": scan.get("skipped", []),
    }


def render_markdown(report: dict[str, Any]) -> str:
    if report.get("tool") == DEPENDENCY_TOOL_NAME:
        return render_dependency_markdown(report)
    lines = [
        "# Source Focus",
        "",
        f"- Status: {report['status']}",
        f"- Query: {markdown_code_span(report['query'])}",
        f"- Focus token estimate: {report['focus_token_estimate']}",
        f"- Full map token estimate: {report['full_map_token_estimate']}",
        f"- Saved vs full map: {report['saved_vs_full_map_tokens']}",
        "",
        "## Recommended Files",
        "",
    ]
    rows = report.get("recommended_files", [])
    if rows:
        for item in rows:
            lines.append(
                f"- {markdown_code_span(item['path'])} - score {item['score']}; {', '.join(item.get('reasons', []))}"
            )
            for evidence in item.get("evidence", []):
                lines.append(
                    f"  - {markdown_code_span(evidence['location'])} - "
                    f"{evidence['reason']}: {markdown_code_span(evidence['snippet'])}"
                )
    else:
        lines.append("- No deterministic matches.")
    lines.extend(["", "## Rules", ""])
    lines.extend(f"- {item}" for item in report.get("rules", []))
    return "\n".join(lines).rstrip() + "\n"


def render_dependency_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Dependency Query",
        "",
        f"- Status: {report['status']}",
        f"- Mode: {report['mode']}",
        f"- Requested path: {markdown_code_span(report['requested_path'])}",
    ]
    if report.get("resolved_path"):
        lines.append(f"- Resolved path: {markdown_code_span(report['resolved_path'])}")
    file_scan_limit = report.get("file_scan_limit", {})
    if file_scan_limit:
        lines.append(
            f"- File scan limit: {file_scan_limit.get('effective', 0)} "
            f"(ceiling {file_scan_limit.get('ceiling', 0)}; requested {file_scan_limit.get('requested', 0)})"
        )
    candidates = report.get("candidates", [])
    if candidates:
        lines.extend(["", "## Candidate Paths", ""])
        lines.extend(f"- {markdown_code_span(path)}" for path in candidates)
    lines.extend(["", "## Results", ""])
    results = report.get("results", [])
    if results:
        for item in results:
            lines.append(
                f"- {markdown_code_span(item['path'])} - depth {item['depth']}; "
                f"{item['relationship_type']}; {item['confidence']} confidence via {item['resolution']}"
            )
            lines.append(
                f"  - {markdown_code_span(item['location'])}: {markdown_code_span(item['evidence'])}"
            )
    else:
        lines.append("- No resolved file relationships.")
    unresolved = report.get("unresolved_subject_relationships", [])
    if unresolved:
        lines.extend(["", "## Unresolved Subject Relationships", ""])
        for item in unresolved:
            lines.append(
                f"- {markdown_code_span(item['target'])} - {item['resolution']}"
            )
    coverage = report.get("coverage", {})
    if coverage:
        lines.extend(["", "## Coverage", ""])
        lines.append(f"- Resolved edges: {coverage.get('resolved_edge_count', 0)}")
        lines.append(
            f"- Non-local or unresolved relationships: {coverage.get('unresolved_or_external_count', 0)}"
        )
        lines.extend(f"- {item}" for item in coverage.get("notes", []))
    lines.extend(["", "## Rules", ""])
    lines.extend(f"- {item}" for item in report.get("rules", []))
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--mode", choices=("focus", "deps", "rdeps", "impact"), default="focus")
    parser.add_argument("--query")
    parser.add_argument("--path")
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-files", type=int, default=5000)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    return parser


def main() -> int:
    navigation_core.require_supported_python()
    parser = build_parser()
    args = parser.parse_args()
    if args.mode == "focus":
        if not args.query:
            parser.error("--query is required for focus mode")
        report = build_focus_report(
            Path(args.target),
            query=args.query,
            limit=args.limit or 8,
            max_files=args.max_files,
        )
    else:
        if not args.path:
            parser.error("--path is required for dependency modes")
        report = build_dependency_report(
            Path(args.target),
            mode=args.mode,
            requested_path=args.path,
            depth=args.depth,
            limit=args.limit or 40,
            max_files=args.max_files,
        )
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
