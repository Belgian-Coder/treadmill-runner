#!/usr/bin/env python3
"""Compare full and compact web-evidence packets without network or model calls."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.dont_write_bytecode = True

SCHEMA_VERSION = 1
TRUST_BOUNDARY = "untrusted-external-data"
ALLOWED_BLOCK_KINDS = {"prose", "list", "table", "code"}
ALLOWED_EVIDENCE_KINDS = {"search-snippet", "opened-page"}
ALLOWED_SOURCE_KINDS = {"primary", "secondary"}
ALLOWED_CACHE_STATES = {"fresh", "stale", "miss"}
TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "should",
    "show",
    "the",
    "their",
    "to",
    "what",
    "with",
}


class SuiteError(ValueError):
    """Raised when a suite cannot be trusted as a benchmark input."""


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SuiteError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_suite(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SuiteError(f"cannot read suite: {exc}") from exc
    try:
        value = json.loads(raw, object_pairs_hook=strict_object)
    except (json.JSONDecodeError, SuiteError) as exc:
        raise SuiteError(f"invalid suite JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SuiteError("suite must be a JSON object")
    validate_suite(value)
    return value


def require_exact_keys(value: dict[str, Any], required: set[str], optional: set[str], label: str) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing:
        raise SuiteError(f"{label} missing field(s): {', '.join(missing)}")
    if unknown:
        raise SuiteError(f"{label} has unknown field(s): {', '.join(unknown)}")


def require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SuiteError(f"{label} must be non-empty text")
    return value


def require_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SuiteError(f"{label} must be an integer >= {minimum}")
    return value


def require_timestamp(value: Any, label: str) -> str:
    text = require_text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SuiteError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SuiteError(f"{label} must include a timezone")
    return text


def validate_suite(suite: dict[str, Any]) -> None:
    require_exact_keys(
        suite,
        {"schema_version", "suite_id", "description", "as_of", "thresholds", "cases"},
        set(),
        "suite",
    )
    if suite["schema_version"] != SCHEMA_VERSION:
        raise SuiteError(f"suite schema_version must equal {SCHEMA_VERSION}")
    require_text(suite["suite_id"], "suite_id")
    require_text(suite["description"], "description")
    as_of = datetime.fromisoformat(require_timestamp(suite["as_of"], "as_of").replace("Z", "+00:00"))
    thresholds = suite["thresholds"]
    if not isinstance(thresholds, dict):
        raise SuiteError("thresholds must be an object")
    require_exact_keys(thresholds, {"minimum_byte_reduction_percent", "maximum_duration_ms"}, set(), "thresholds")
    reduction = thresholds["minimum_byte_reduction_percent"]
    if isinstance(reduction, bool) or not isinstance(reduction, (int, float)) or not 0 <= float(reduction) < 100:
        raise SuiteError("minimum_byte_reduction_percent must be a number in [0, 100)")
    require_int(thresholds["maximum_duration_ms"], "maximum_duration_ms", minimum=1)
    cases = suite["cases"]
    if not isinstance(cases, list) or not cases:
        raise SuiteError("cases must be a non-empty list")
    seen_case_ids: set[str] = set()
    for case_index, case in enumerate(cases):
        validate_case(case, case_index, seen_case_ids, as_of)


def validate_case(case: Any, case_index: int, seen_case_ids: set[str], as_of: datetime) -> None:
    label = f"case[{case_index}]"
    if not isinstance(case, dict):
        raise SuiteError(f"{label} must be an object")
    require_exact_keys(
        case,
        {"id", "query", "expect_status", "required_block_ids", "max_sources", "max_output_bytes", "sources"},
        set(),
        label,
    )
    case_id = require_text(case["id"], f"{label}.id")
    if case_id in seen_case_ids:
        raise SuiteError(f"duplicate case id: {case_id}")
    seen_case_ids.add(case_id)
    require_text(case["query"], f"{label}.query")
    if case["expect_status"] not in {"evidence", "no-evidence"}:
        raise SuiteError(f"{label}.expect_status must be evidence or no-evidence")
    require_int(case["max_sources"], f"{label}.max_sources", minimum=1)
    if case["max_sources"] > 5:
        raise SuiteError(f"{label}.max_sources cannot exceed 5")
    require_int(case["max_output_bytes"], f"{label}.max_output_bytes", minimum=256)
    required = case["required_block_ids"]
    if not isinstance(required, list) or any(not isinstance(item, str) or not item for item in required):
        raise SuiteError(f"{label}.required_block_ids must be a list of non-empty strings")
    if len(set(required)) != len(required):
        raise SuiteError(f"{label}.required_block_ids contains duplicates")
    sources = case["sources"]
    if not isinstance(sources, list) or not sources:
        raise SuiteError(f"{label}.sources must be a non-empty list")
    seen_source_ids: set[str] = set()
    seen_block_ids: set[str] = set()
    for source_index, source in enumerate(sources):
        validate_source(source, f"{label}.sources[{source_index}]", seen_source_ids, seen_block_ids, as_of)
    missing = sorted(set(required) - seen_block_ids)
    if missing:
        raise SuiteError(f"{label} required block(s) not found: {', '.join(missing)}")
    if case["expect_status"] == "no-evidence" and required:
        raise SuiteError(f"{label} no-evidence case cannot require blocks")


def validate_source(
    source: Any,
    label: str,
    seen_source_ids: set[str],
    seen_block_ids: set[str],
    as_of: datetime,
) -> None:
    if not isinstance(source, dict):
        raise SuiteError(f"{label} must be an object")
    require_exact_keys(
        source,
        {
            "id",
            "url",
            "title",
            "domain",
            "retrieved_at",
            "cache",
            "evidence_kind",
            "source_kind",
            "blocks",
        },
        {"published_at"},
        label,
    )
    source_id = require_text(source["id"], f"{label}.id")
    if source_id in seen_source_ids:
        raise SuiteError(f"duplicate source id: {source_id}")
    seen_source_ids.add(source_id)
    url = require_text(source["url"], f"{label}.url")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or "@" in parsed.netloc:
        raise SuiteError(f"{label}.url must be a credential-free HTTP(S) URL")
    domain = require_text(source["domain"], f"{label}.domain").lower()
    if parsed.hostname.lower() != domain:
        raise SuiteError(f"{label}.domain must match the URL hostname")
    require_text(source["title"], f"{label}.title")
    retrieved_at = datetime.fromisoformat(
        require_timestamp(source["retrieved_at"], f"{label}.retrieved_at").replace("Z", "+00:00")
    )
    if retrieved_at > as_of:
        raise SuiteError(f"{label}.retrieved_at cannot be after suite as_of")
    if "published_at" in source:
        published_at = datetime.fromisoformat(
            require_timestamp(source["published_at"], f"{label}.published_at").replace("Z", "+00:00")
        )
        if published_at > retrieved_at:
            raise SuiteError(f"{label}.published_at cannot be after retrieved_at")
    if source["evidence_kind"] not in ALLOWED_EVIDENCE_KINDS:
        raise SuiteError(f"{label}.evidence_kind is invalid")
    if source["source_kind"] not in ALLOWED_SOURCE_KINDS:
        raise SuiteError(f"{label}.source_kind is invalid")
    cache = source["cache"]
    if not isinstance(cache, dict):
        raise SuiteError(f"{label}.cache must be an object")
    require_exact_keys(cache, {"state", "age_seconds", "ttl_seconds"}, set(), f"{label}.cache")
    if cache["state"] not in ALLOWED_CACHE_STATES:
        raise SuiteError(f"{label}.cache.state is invalid")
    age_seconds = require_int(cache["age_seconds"], f"{label}.cache.age_seconds")
    ttl_seconds = require_int(cache["ttl_seconds"], f"{label}.cache.ttl_seconds", minimum=1)
    expected_age = int((as_of - retrieved_at).total_seconds())
    if age_seconds != expected_age:
        raise SuiteError(f"{label}.cache.age_seconds must match suite as_of minus retrieved_at")
    if cache["state"] == "miss":
        if age_seconds != 0:
            raise SuiteError(f"{label}.cache miss must have age_seconds 0")
    elif (cache["state"] == "fresh") != (age_seconds <= ttl_seconds):
        raise SuiteError(f"{label}.cache.state must agree with age_seconds and ttl_seconds")
    blocks = source["blocks"]
    if not isinstance(blocks, list) or not blocks:
        raise SuiteError(f"{label}.blocks must be a non-empty list")
    for block_index, block in enumerate(blocks):
        block_label = f"{label}.blocks[{block_index}]"
        if not isinstance(block, dict):
            raise SuiteError(f"{block_label} must be an object")
        require_exact_keys(block, {"id", "kind", "text"}, set(), block_label)
        block_id = require_text(block["id"], f"{block_label}.id")
        if block_id in seen_block_ids:
            raise SuiteError(f"duplicate block id: {block_id}")
        seen_block_ids.add(block_id)
        if block["kind"] not in ALLOWED_BLOCK_KINDS:
            raise SuiteError(f"{block_label}.kind is invalid")
        require_text(block["text"], f"{block_label}.text")


def tokens(value: str) -> set[str]:
    return {
        term
        for match in TOKEN_RE.finditer(value)
        if len(term := match.group(0).lower()) > 1 and term not in STOP_WORDS
    }


def source_tie_break(source: dict[str, Any]) -> tuple[int, int, int, int, str]:
    return (
        1 if source["evidence_kind"] == "opened-page" else 0,
        1 if source["source_kind"] == "primary" else 0,
        1 if source["cache"]["state"] != "stale" else 0,
        -int(source["cache"]["age_seconds"]),
        str(source["id"]),
    )


def ranked_blocks(case: dict[str, Any]) -> list[tuple[int, tuple[int, int, int, int, str], dict[str, Any], dict[str, Any]]]:
    query_terms = tokens(case["query"])
    rows = []
    for source in case["sources"]:
        title_terms = tokens(source["title"])
        for block in source["blocks"]:
            block_terms = tokens(block["text"])
            block_overlap = len(query_terms & block_terms)
            title_overlap = len(query_terms & title_terms)
            score = block_overlap * 4 + title_overlap
            minimum_overlap = 1 if len(query_terms) <= 2 else 2
            structured_in_relevant_source = block["kind"] in {"list", "table", "code"} and title_overlap >= 2
            if block_overlap >= minimum_overlap or (structured_in_relevant_source and block_overlap >= 1):
                rows.append((score, source_tie_break(source), source, block))
    rows.sort(key=lambda row: (-row[0], tuple(-item for item in row[1][:4]), row[1][4], row[3]["id"]))
    deduplicated = []
    seen_text_hashes: set[str] = set()
    for row in rows:
        block_hash = hashlib.sha256(row[3]["text"].encode("utf-8")).hexdigest()
        if block_hash in seen_text_hashes:
            continue
        seen_text_hashes.add(block_hash)
        deduplicated.append(row)
    return deduplicated


def packet_block(block: dict[str, Any]) -> dict[str, Any]:
    text = str(block["text"])
    return {
        "id": str(block["id"]),
        "kind": str(block["kind"]),
        "text": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def build_packet(case: dict[str, Any], selected: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for source, block in selected:
        source_id = str(source["id"])
        if source_id not in grouped:
            row = {
                "id": source_id,
                "url": source["url"],
                "title": source["title"],
                "domain": source["domain"],
                "retrieved_at": source["retrieved_at"],
                "cache": dict(source["cache"]),
                "evidence_kind": source["evidence_kind"],
                "source_kind": source["source_kind"],
                "blocks": [],
            }
            if "published_at" in source:
                row["published_at"] = source["published_at"]
            grouped[source_id] = row
            order.append(source_id)
        grouped[source_id]["blocks"].append(packet_block(block))
    return {
        "schema_version": SCHEMA_VERSION,
        "trust_boundary": TRUST_BOUNDARY,
        "instructions_authorized": False,
        "status": "evidence" if selected else "no-evidence",
        "query": case["query"],
        "sources": [grouped[source_id] for source_id in order],
    }


def canonical_bytes(packet: dict[str, Any]) -> bytes:
    return json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def validate_packet(packet: dict[str, Any]) -> None:
    raw = canonical_bytes(packet)
    parsed = json.loads(raw)
    if parsed != packet:
        raise SuiteError("packet failed canonical JSON round-trip")
    if packet.get("schema_version") != SCHEMA_VERSION:
        raise SuiteError("packet schema version changed")
    if packet.get("trust_boundary") != TRUST_BOUNDARY or packet.get("instructions_authorized") is not False:
        raise SuiteError("packet trust boundary changed")
    if set(packet) != {"schema_version", "trust_boundary", "instructions_authorized", "status", "query", "sources"}:
        raise SuiteError("packet contains unexpected top-level controls")
    for source in packet["sources"]:
        required_source_keys = {
            "id",
            "url",
            "title",
            "domain",
            "retrieved_at",
            "cache",
            "evidence_kind",
            "source_kind",
            "blocks",
        }
        source_keys = frozenset(source)
        if source_keys not in {frozenset(required_source_keys), frozenset(required_source_keys | {"published_at"})}:
            raise SuiteError(f"packet source schema changed: {source.get('id', '<unknown>')}")
        cache = source.get("cache")
        if not isinstance(cache, dict) or set(cache) != {"state", "age_seconds", "ttl_seconds"}:
            raise SuiteError(f"packet cache schema changed: {source.get('id', '<unknown>')}")
        if cache["state"] not in ALLOWED_CACHE_STATES or not isinstance(cache["age_seconds"], int) or not isinstance(cache["ttl_seconds"], int):
            raise SuiteError(f"packet cache metadata is invalid: {source.get('id', '<unknown>')}")
        for block in source["blocks"]:
            digest = hashlib.sha256(block["text"].encode("utf-8")).hexdigest()
            if block["sha256"] != digest:
                raise SuiteError(f"block hash mismatch: {block['id']}")


def all_blocks_control(case: dict[str, Any]) -> dict[str, Any]:
    selected = [(source, block) for source in case["sources"] for block in source["blocks"]]
    packet = build_packet(case, selected)
    validate_packet(packet)
    return packet


def compact_candidate(case: dict[str, Any]) -> dict[str, Any]:
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    selected_sources: set[str] = set()
    for _score, _tie, source, block in ranked_blocks(case):
        source_id = str(source["id"])
        if source_id not in selected_sources and len(selected_sources) >= int(case["max_sources"]):
            continue
        trial = selected + [(source, block)]
        packet = build_packet(case, trial)
        if len(canonical_bytes(packet)) > int(case["max_output_bytes"]):
            continue
        selected = trial
        selected_sources.add(source_id)
    packet = build_packet(case, selected)
    validate_packet(packet)
    return packet


def block_map(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(block["id"]): block for source in case["sources"] for block in source["blocks"]}


def packet_blocks(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(block["id"]): block for source in packet["sources"] for block in source["blocks"]}


def source_metadata(source: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in source.items() if key != "blocks"}


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    control = all_blocks_control(case)
    candidate = compact_candidate(case)
    source_blocks = block_map(case)
    selected = packet_blocks(candidate)
    required = set(case["required_block_ids"])
    selected_ids = set(selected)
    required_recall = 1.0 if not required else len(required & selected_ids) / len(required)
    preserved = all(
        selected[block_id]["text"] == source_blocks[block_id]["text"]
        and selected[block_id]["kind"] == source_blocks[block_id]["kind"]
        for block_id in selected_ids
    )
    original_sources = {str(source["id"]): source for source in case["sources"]}
    metadata_preserved = all(
        source_metadata(source) == source_metadata(original_sources[str(source["id"])])
        for source in candidate["sources"]
    )
    control_bytes = len(canonical_bytes(control))
    candidate_bytes = len(canonical_bytes(candidate))
    expected_status = case["expect_status"]
    checks = {
        "expected_status": candidate["status"] == expected_status,
        "required_block_recall": required_recall == 1.0,
        "source_metadata_preservation": metadata_preserved,
        "atomic_block_preservation": preserved,
        "source_cap": len(candidate["sources"]) <= int(case["max_sources"]),
        "byte_cap": candidate_bytes <= int(case["max_output_bytes"]),
        "not_larger_than_control": candidate_bytes <= control_bytes,
        "no_evidence_precision": expected_status != "no-evidence" or not selected,
    }
    return {
        "id": case["id"],
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "required_block_recall": round(required_recall, 4),
        "selected_block_ids": sorted(selected_ids),
        "selected_source_count": len(candidate["sources"]),
        "control_bytes": control_bytes,
        "candidate_bytes": candidate_bytes,
        "saved_bytes": control_bytes - candidate_bytes,
    }


def benchmark_report(suite: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    cases = [evaluate_case(case) for case in suite["cases"]]
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    control_bytes = sum(case["control_bytes"] for case in cases)
    candidate_bytes = sum(case["candidate_bytes"] for case in cases)
    saved_bytes = control_bytes - candidate_bytes
    reduction = round((saved_bytes / control_bytes) * 100, 2) if control_bytes else 0.0
    failed = sum(case["status"] == "failed" for case in cases)
    minimum = float(suite["thresholds"]["minimum_byte_reduction_percent"])
    maximum_duration = int(suite["thresholds"]["maximum_duration_ms"])
    aggregate_checks = {
        "all_cases_executable": len(cases) == len(suite["cases"]),
        "zero_blocked": True,
        "zero_skipped": True,
        "zero_failed": failed == 0,
        "minimum_byte_reduction": reduction >= minimum,
        "maximum_duration": duration_ms <= maximum_duration,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "agent-benchmarking.web-evidence-efficiency-v1",
        "suite_id": suite["suite_id"],
        "ok": all(aggregate_checks.values()),
        "measurement_scope": {
            "artifact_review_context": True,
            "live_web": False,
            "live_model": False,
            "provider_usage": False,
            "billing_claim": False,
            "network": False,
            "index_build": False,
        },
        "arms": ["all-blocks-control", "lexical-block-filter-v1"],
        "thresholds": dict(suite["thresholds"]),
        "aggregate_checks": aggregate_checks,
        "summary": {
            "case_count": len(cases),
            "passed": len(cases) - failed,
            "failed": failed,
            "blocked": 0,
            "skipped": 0,
            "control_bytes": control_bytes,
            "candidate_bytes": candidate_bytes,
            "saved_bytes": saved_bytes,
            "byte_reduction_percent": reduction,
            "duration_ms": duration_ms,
        },
        "cases": cases,
        "supported_claim": (
            "On the fixed V1 fixtures, the compact packet preserved all golden evidence while reducing serialized artifact context."
        ),
        "unsupported_claims": [
            "equivalent live web-search accuracy",
            "behavioral prompt-injection resistance",
            "provider-token savings",
            "provider cost reduction",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Web Evidence Efficiency V1",
        "",
        f"- Result: `{'pass' if report['ok'] else 'fail'}`",
        f"- Cases: {summary['passed']}/{summary['case_count']} passed; {summary['blocked']} blocked; {summary['skipped']} skipped",
        f"- Artifact bytes: {summary['control_bytes']} control -> {summary['candidate_bytes']} compact ({summary['byte_reduction_percent']}% reduction)",
        f"- Duration: {summary['duration_ms']} ms",
        f"- Claim boundary: {report['supported_claim']}",
        "",
        "| Case | Result | Required recall | Sources | Bytes control -> compact |",
        "|---|---|---:|---:|---:|",
    ]
    for case in report["cases"]:
        lines.append(
            f"| {case['id']} | {case['status']} | {case['required_block_recall']:.0%} | "
            f"{case['selected_source_count']} | {case['control_bytes']} -> {case['candidate_bytes']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, help="checked-in web evidence suite JSON")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args(argv)
    try:
        report = benchmark_report(load_suite(Path(args.suite)))
    except SuiteError as exc:
        print(f"web evidence suite error: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
