"""Append-only local feedback ledger for managed skill and workflow failures."""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

from repo_support import repo_common as repo
from repo_support import repo_policy

FEEDBACK_LOG = Path(".agents/local-ai/cache/feedback/failure-feedback.jsonl")
TARGET_KINDS = {"skill", "workflow", "repo"}
MAX_CONTEXT_PATHS = 20
MAX_CORRECTION_EVENTS = 200
MAX_CORRECTION_PACKET_BYTES = 1024 * 1024
CORRECTION_FIELDS = {
    "id",
    "target_kind",
    "target",
    "task_class",
    "host_surface",
    "model_provider",
    "model",
    "semantic_profile",
    "prompt",
    "incorrect_behavior",
    "correct_behavior",
    "acceptance_criteria",
    "source_refs",
}
CORRECTION_REQUIRED_FIELDS = CORRECTION_FIELDS - {"model", "incorrect_behavior"}
HOST_SURFACES = {
    "codex",
    "github-copilot",
    "claude-code",
    "openai-responses-api",
    "anthropic-messages-api",
    "local-ai",
    "unknown",
}
MODEL_PROVIDERS = {"openai", "anthropic", "local", "other", "unknown"}
CORRECTION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,79}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def compact_text(
    value: object,
    *,
    limit: int | None = None,
    root: Path | None = None,
) -> str:
    if limit is None:
        limit = repo_policy.int_value(
            root or repo_policy.project_root(), "limits.feedback.text_chars"
        )
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def default_caller(root: Path | None = None) -> str:
    actor_chars = repo_policy.int_value(
        root or repo_policy.project_root(), "limits.feedback.actor_chars"
    )
    for name in ("CODEX_USER", "GITHUB_ACTOR", "USERNAME", "USER"):
        value = os.environ.get(name, "").strip()
        if value:
            return compact_text(value, limit=actor_chars)
    try:
        return compact_text(getpass.getuser(), limit=actor_chars)
    except OSError:
        return "unknown"


def normalize_context_path(root: Path, value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        try:
            return candidate.resolve(strict=False).relative_to(root.resolve()).as_posix()
        except ValueError:
            return candidate.as_posix()
    return raw.replace("\\", "/")


def normalized_context_paths(root: Path, values: list[object] | tuple[object, ...] | None) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        normalized = normalize_context_path(root, value)
        if normalized and normalized not in seen:
            paths.append(normalized)
            seen.add(normalized)
        if len(paths) >= MAX_CONTEXT_PATHS:
            break
    return paths


def failure_fingerprint(
    failure_type: object,
    fact: object,
    fallback: object = "",
    *,
    root: Path | None = None,
) -> str:
    fact_chars = repo_policy.int_value(
        root or repo_policy.project_root(), "limits.feedback.fact_chars"
    )
    source = compact_text(fact or fallback, limit=fact_chars)
    source = re.sub(r"[A-Za-z]:/[^\s]+", "<path>", source.replace("\\", "/"))
    source = re.sub(r"\b\d{8}-\d{6}\b", "<timestamp>", source)
    source = re.sub(r"\s+", " ", source.casefold()).strip()
    raw = f"{failure_type or 'unknown'}|{source}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def entry_id(entry: dict[str, object]) -> str:
    raw = json.dumps(
        {
            "timestamp": entry.get("timestamp"),
            "target_kind": entry.get("target_kind"),
            "target": entry.get("target"),
            "trigger_command": entry.get("trigger_command"),
            "failure_fingerprint": entry.get("failure_fingerprint"),
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def log_path(root: Path) -> Path:
    return root / FEEDBACK_LOG


def record_feedback(
    root: Path,
    *,
    target_kind: str,
    target: str,
    summary: str,
    bad: str,
    good: str = "",
    context_paths: list[object] | tuple[object, ...] | None = None,
    caller: str | None = None,
    trigger_command: str = "",
    failure_type: str = "",
    first_failing_fact: str = "",
    raw_output_path: str = "",
    output_digest: str = "",
    suggested_next_command: str = "",
    source_tool: str = "",
) -> dict[str, object]:
    kind = target_kind.strip().lower()
    if kind not in TARGET_KINDS:
        raise ValueError(f"target_kind must be one of: {', '.join(sorted(TARGET_KINDS))}")
    text_chars = repo_policy.int_value(root, "limits.feedback.text_chars")
    actor_chars = repo_policy.int_value(root, "limits.feedback.actor_chars")
    target_chars = repo_policy.int_value(root, "limits.feedback.target_chars")
    command_chars = repo_policy.int_value(root, "limits.feedback.command_chars")
    digest_chars = repo_policy.int_value(root, "limits.feedback.digest_chars")
    target_name = compact_text(target, limit=target_chars) or "unknown"
    contexts = normalized_context_paths(root, context_paths)
    fingerprint = failure_fingerprint(failure_type, first_failing_fact, bad, root=root)
    entry: dict[str, object] = {
        "schema_version": 1,
        "timestamp": utc_now(),
        "caller": compact_text(caller, limit=actor_chars) if caller else default_caller(root),
        "target_kind": kind,
        "target": target_name,
        "summary": compact_text(summary, limit=text_chars),
        "what_worked": compact_text(good, limit=text_chars),
        "what_failed": compact_text(bad, limit=text_chars),
        "context_paths": contexts,
        "trigger_command": compact_text(trigger_command, limit=command_chars),
        "failure_type": compact_text(failure_type, limit=actor_chars) or "unknown",
        "first_failing_fact": compact_text(first_failing_fact, limit=text_chars),
        "raw_output_path": normalize_context_path(root, raw_output_path),
        "output_digest": compact_text(output_digest, limit=digest_chars),
        "suggested_next_command": compact_text(suggested_next_command, limit=command_chars),
        "source_tool": compact_text(source_tool, limit=target_chars) or "skill-manager.feedback",
        "failure_fingerprint": fingerprint,
    }
    entry["id"] = entry_id(entry)
    path = log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
    return entry


def try_record_feedback(root: Path, **kwargs: object) -> dict[str, object] | None:
    try:
        return record_feedback(root, **kwargs)  # type: ignore[arg-type]
    except Exception:
        return None


def read_entries(root: Path) -> tuple[list[dict[str, object]], int]:
    path = log_path(root)
    entries: list[dict[str, object]] = []
    skipped = 0
    if not path.exists():
        return entries, skipped
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(value, dict):
            entries.append(value)
        else:
            skipped += 1
    return entries, skipped


def filtered_entries(root: Path, *, target: str = "", all_targets: bool = False) -> tuple[list[dict[str, object]], int]:
    entries, skipped = read_entries(root)
    if all_targets or not target:
        return entries, skipped
    normalized = target.casefold()
    return [entry for entry in entries if str(entry.get("target", "")).casefold() == normalized], skipped


def group_entries(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, object]]] = {}
    for entry in entries:
        key = (
            str(entry.get("target_kind") or "repo"),
            str(entry.get("target") or "unknown"),
            str(entry.get("failure_type") or "unknown"),
            str(entry.get("failure_fingerprint") or failure_fingerprint(entry.get("failure_type"), entry.get("first_failing_fact"), entry.get("what_failed"), root=root)),
        )
        grouped.setdefault(key, []).append(entry)
    rows: list[dict[str, object]] = []
    for (target_kind, target, failure_type, fingerprint), values in grouped.items():
        values = sorted(values, key=lambda item: str(item.get("timestamp", "")))
        contexts: list[str] = []
        for entry in values:
            for path in entry.get("context_paths", []) if isinstance(entry.get("context_paths"), list) else []:
                text = str(path)
                if text and text not in contexts:
                    contexts.append(text)
        rows.append(
            {
                "target_kind": target_kind,
                "target": target,
                "failure_type": failure_type,
                "failure_fingerprint": fingerprint,
                "count": len(values),
                "first_seen": values[0].get("timestamp", ""),
                "last_seen": values[-1].get("timestamp", ""),
                "summary": values[-1].get("summary", ""),
                "what_failed": values[-1].get("what_failed", ""),
                "first_failing_fact": values[-1].get("first_failing_fact", ""),
                "context_paths": contexts[:10],
                "suggested_next_command": next(
                    (str(entry.get("suggested_next_command")) for entry in reversed(values) if entry.get("suggested_next_command")),
                    "",
                ),
                "source_tools": sorted(
                    {str(entry.get("source_tool")) for entry in values if entry.get("source_tool")}
                ),
            }
        )
    return sorted(rows, key=lambda item: (-int(item["count"]), str(item["target"]), str(item["failure_type"])))


def summary_report(
    root: Path,
    *,
    target: str = "",
    all_targets: bool = False,
    compact: bool = False,
) -> dict[str, object]:
    entries, skipped = filtered_entries(root, target=target, all_targets=all_targets)
    groups = group_entries(entries)
    report: dict[str, object] = {
        "schema_version": 1,
        "tool": "skill-manager.feedback-summary",
        "ok": True,
        "status": "ok",
        "log_path": repo.relative(root, log_path(root)),
        "summary": {
            "entry_count": len(entries),
            "group_count": len(groups),
            "skipped_line_count": skipped,
        },
        "groups": groups,
        "next_command": "python -B .agents/manage.py feedback export --all --min-count 2 --output evidence/feedback",
    }
    if compact and not groups:
        report.pop("groups", None)
    return report


def export_report(root: Path, *, target: str = "", all_targets: bool = False, min_count: int = 2) -> dict[str, object]:
    report = summary_report(root, target=target, all_targets=all_targets)
    groups = [
        group
        for group in report.get("groups", []) if isinstance(report.get("groups"), list)
        if isinstance(group, dict) and int(group.get("count", 0) or 0) >= min_count
    ]
    return {
        "schema_version": 1,
        "tool": "skill-manager.feedback-export",
        "ok": True,
        "status": "ok",
        "source_log": report.get("log_path", repo.relative(root, log_path(root))),
        "summary": {
            "candidate_count": len(groups),
            "min_count": min_count,
            "source_entry_count": report.get("summary", {}).get("entry_count", 0)
            if isinstance(report.get("summary"), dict)
            else 0,
        },
        "candidates": groups,
        "write_policy": "Raw feedback remains local-only; promote reviewed candidates into owning docs, tests, suites, templates, fixtures, or scripts.",
    }


def require_repo_file(root: Path, value: str, label: str) -> Path:
    target = Path(value).expanduser()
    if not target.is_absolute():
        target = root / target
    lexical = Path(os.path.abspath(target))
    resolved = lexical.resolve(strict=False)
    if os.path.normcase(str(lexical)) != os.path.normcase(str(resolved)):
        raise SystemExit(f"{label} must not use a symlink or reparse alias")
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise SystemExit(f"{label} must stay inside the repository") from exc
    if not lexical.exists():
        raise SystemExit(f"{label} does not exist: {repo.relative(root, resolved)}")
    if not lexical.is_file():
        raise SystemExit(f"{label} must be a file: {repo.relative(root, resolved)}")
    return lexical


def require_repo_file_pinned(root: Path, value: str, label: str) -> tuple[Path, tuple[int, int]]:
    target = Path(value).expanduser()
    if not target.is_absolute():
        target = root / target
    lexical = Path(os.path.abspath(target))
    try:
        before = os.lstat(lexical)
        resolved = lexical.resolve(strict=True)
        after = os.lstat(lexical)
    except OSError as exc:
        raise SystemExit(f"{label} is unavailable: {exc}") from exc
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise SystemExit(f"{label} changed while resolving")
    if os.path.normcase(str(lexical)) != os.path.normcase(str(resolved)):
        raise SystemExit(f"{label} must not use a symlink or reparse alias")
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise SystemExit(f"{label} must stay inside the repository") from exc
    reparse = bool(int(getattr(after, "st_file_attributes", 0)) & 0x400)
    if stat.S_ISLNK(after.st_mode) or reparse or not stat.S_ISREG(after.st_mode):
        raise SystemExit(f"{label} must be a no-follow regular file")
    return lexical, (after.st_dev, after.st_ino)


def read_bounded_no_follow(
    path: Path,
    *,
    label: str,
    max_bytes: int,
    expected_identity: tuple[int, int] | None = None,
) -> bytes:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise SystemExit(f"{label} is unavailable: {exc}") from exc
    reparse = bool(int(getattr(metadata, "st_file_attributes", 0)) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or reparse or not stat.S_ISREG(metadata.st_mode):
        raise SystemExit(f"{label} must be a no-follow regular file")
    if metadata.st_size > max_bytes:
        raise SystemExit(f"{label} exceeds the {max_bytes}-byte input limit")
    if expected_identity is not None and (metadata.st_dev, metadata.st_ino) != expected_identity:
        raise SystemExit(f"{label} identity changed before opening")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino):
            raise SystemExit(f"{label} changed while opening")
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise SystemExit(f"{label} exceeds the {max_bytes}-byte input limit")
    return data


def _nonempty_text(
    value: object,
    *,
    limit: int | None = None,
    root: Path | None = None,
) -> bool:
    if limit is None:
        limit = repo_policy.int_value(
            root or repo_policy.project_root(), "limits.feedback.text_chars"
        )
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def correction_events_sha256(events: list[object]) -> str:
    """Return the canonical event-set digest used for diagnostics."""
    ordered = sorted(events, key=lambda item: str(item.get("id", "")) if isinstance(item, dict) else "")
    canonical = json.dumps(
        ordered,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def correction_review_sha256(reviewed_by: str, events: list[object]) -> str:
    """Bind reviewer attribution and the exact canonical event set."""

    payload = {
        "reviewed_by": reviewed_by,
        "events_sha256": correction_events_sha256(events),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def correction_packet_issues(
    value: object,
    *,
    require_review_digest: bool = True,
    validate_review_digest: bool = True,
    root: Path | None = None,
) -> list[str]:
    policy_root = root or repo_policy.project_root()
    target_chars = repo_policy.int_value(policy_root, "limits.feedback.target_chars")
    text_chars = repo_policy.int_value(policy_root, "limits.feedback.text_chars")
    fact_chars = repo_policy.int_value(policy_root, "limits.feedback.fact_chars")
    if not isinstance(value, dict):
        return ["corrections packet must be a JSON object"]
    issues: list[str] = []
    allowed_top = {
        "schema_version",
        "tool",
        "review_state",
        "reviewed",
        "reviewed_by",
        "reviewed_events_sha256",
        "events",
    }
    for field in sorted(set(value) - allowed_top):
        issues.append(f"corrections.{field} is not allowed")
    if type(value.get("schema_version")) is not int or value.get("schema_version") != 1:
        issues.append("corrections.schema_version must be 1")
    if value.get("tool") != "skill-manager.corrections":
        issues.append("corrections.tool must be skill-manager.corrections")
    review_state = value.get("review_state")
    if review_state not in {"review-input", "reviewed"}:
        issues.append("corrections.review_state must be review-input or reviewed")
    if require_review_digest and review_state != "reviewed":
        issues.append("corrections.review_state must be reviewed before eval generation")
    if value.get("reviewed") is not True:
        issues.append("corrections.reviewed must be true before eval generation")
    if not _nonempty_text(value.get("reviewed_by"), limit=120):
        issues.append("corrections.reviewed_by must be a non-empty string")
    reviewed_digest = value.get("reviewed_events_sha256")
    if require_review_digest and (
        not isinstance(reviewed_digest, str) or not SHA256_PATTERN.fullmatch(reviewed_digest)
    ):
        issues.append("corrections.reviewed_events_sha256 must be a lowercase SHA-256 digest")
    events = value.get("events")
    if not isinstance(events, list) or not events:
        return [*issues, "corrections.events must be a non-empty array"]
    if len(events) > MAX_CORRECTION_EVENTS:
        return [*issues, f"corrections.events must contain at most {MAX_CORRECTION_EVENTS} events"]
    seen: set[str] = set()
    for index, event in enumerate(events):
        label = f"corrections.events[{index}]"
        if not isinstance(event, dict):
            issues.append(f"{label} must be an object")
            continue
        for field in sorted(CORRECTION_REQUIRED_FIELDS - set(event)):
            issues.append(f"{label}.{field} is required")
        for field in sorted(set(event) - CORRECTION_FIELDS):
            issues.append(f"{label}.{field} is not allowed")
        correction_id = str(event.get("id", ""))
        if not CORRECTION_ID_PATTERN.fullmatch(correction_id):
            issues.append(f"{label}.id must be a lowercase kebab-case identifier")
        elif correction_id in seen:
            issues.append(f"{label}.id duplicates '{correction_id}'")
        seen.add(correction_id)
        if event.get("target_kind") not in TARGET_KINDS:
            issues.append(f"{label}.target_kind must be one of: {', '.join(sorted(TARGET_KINDS))}")
        for field, limit in (
            ("target", target_chars),
            ("task_class", target_chars),
            ("semantic_profile", target_chars),
            ("prompt", text_chars),
            ("correct_behavior", text_chars),
        ):
            if not _nonempty_text(event.get(field), limit=limit):
                issues.append(f"{label}.{field} must be a non-empty string of at most {limit} characters")
        for optional_field in ("model", "incorrect_behavior"):
            if optional_field in event and event.get(optional_field) != "" and not _nonempty_text(event.get(optional_field), limit=text_chars):
                issues.append(f"{label}.{optional_field} must be a string of at most {text_chars} characters")
        if event.get("host_surface") not in HOST_SURFACES:
            issues.append(f"{label}.host_surface must be one of: {', '.join(sorted(HOST_SURFACES))}")
        if event.get("model_provider") not in MODEL_PROVIDERS:
            issues.append(f"{label}.model_provider must be one of: {', '.join(sorted(MODEL_PROVIDERS))}")
        for field, max_items in (("acceptance_criteria", 12), ("source_refs", 20)):
            items = event.get(field)
            if (
                not isinstance(items, list)
                or not items
                or len(items) > max_items
                or not all(_nonempty_text(item, limit=fact_chars) for item in items)
            ):
                issues.append(
                    f"{label}.{field} must be a non-empty array of at most {max_items} bounded strings"
                )
            elif len(items) != len(set(items)):
                issues.append(f"{label}.{field} must contain unique values")
    if (
        validate_review_digest
        and isinstance(reviewed_digest, str)
        and SHA256_PATTERN.fullmatch(reviewed_digest)
    ):
        actual_digest = correction_review_sha256(str(value.get("reviewed_by", "")), events)
        if reviewed_digest != actual_digest:
            issues.append(
                "corrections.reviewed_events_sha256 does not match the reviewer-bound canonical content; review the current events again"
            )
    return issues


def load_corrections_packet(
    root: Path,
    corrections_path: str,
    *,
    label: str,
) -> tuple[Path, bytes, object]:
    path, path_identity = require_repo_file_pinned(root, corrections_path, label)
    try:
        raw_bytes = read_bounded_no_follow(
            path,
            label="corrections packet",
            max_bytes=MAX_CORRECTION_PACKET_BYTES,
            expected_identity=path_identity,
        )
        value = json.loads(raw_bytes.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"corrections packet is unreadable: {exc}") from exc
    return path, raw_bytes, value


def build_review_digest_report(root: Path, corrections_path: str) -> dict[str, object]:
    path, raw_bytes, value = load_corrections_packet(
        root,
        corrections_path,
        label="feedback review-digest --corrections",
    )
    issues = correction_packet_issues(
        value,
        require_review_digest=False,
        validate_review_digest=False,
        root=root,
    )
    if issues:
        raise SystemExit("invalid corrections packet: " + "; ".join(issues))
    assert isinstance(value, dict)
    events = value["events"]
    assert isinstance(events, list)
    return {
        "schema_version": 1,
        "tool": "skill-manager.feedback-review-digest",
        "ok": True,
        "source": {
            "path": repo.relative(root, path),
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        },
        "event_count": len(events),
        "reviewed_events_sha256": correction_review_sha256(str(value["reviewed_by"]), events),
        "canonicalization": {
            "reviewer_binding": "reviewed_by plus canonical events digest",
            "event_order": "ascending event id",
            "object_keys": "recursive Unicode code-point order",
            "json": "compact separators with non-ASCII characters preserved",
            "encoding": "UTF-8",
        },
        "next_step": "Copy reviewed_events_sha256 into the reviewed correction packet before eval-packet generation.",
    }


def build_eval_packet(root: Path, corrections_path: str) -> dict[str, object]:
    path, raw_bytes, value = load_corrections_packet(
        root,
        corrections_path,
        label="feedback eval-packet --corrections",
    )
    issues = correction_packet_issues(value, root=root)
    if issues:
        raise SystemExit("invalid corrections packet: " + "; ".join(issues))
    events = value["events"]
    cases: list[dict[str, object]] = []
    for event in sorted(events, key=lambda item: str(item["id"])):
        case_payload = {
            "correction_id": event["id"],
            "target_kind": event["target_kind"],
            "target": event["target"],
            "task_class": event["task_class"],
            "host_surface": event["host_surface"],
            "model_provider": event["model_provider"],
            "model": event.get("model", ""),
            "semantic_profile": event["semantic_profile"],
            "prompt": event["prompt"],
            "incorrect_behavior": event.get("incorrect_behavior", ""),
            "expected_behavior": event["correct_behavior"],
            "acceptance_criteria": list(event["acceptance_criteria"]),
            "source_refs": list(event["source_refs"]),
        }
        digest = hashlib.sha256(
            json.dumps(case_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
        cases.append(
            {
                "id": f"correction-{event['id']}-{digest[:8]}",
                "status": "candidate",
                "source_review_status": "reviewed",
                "source_reviewed_by": value["reviewed_by"],
                **case_payload,
            }
        )
    return {
        "schema_version": 1,
        "tool": "skill-manager.feedback-eval-packet",
        "ok": True,
        "status": "generated",
        "source": {
            "path": repo.relative(root, path),
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "reviewed_by": value["reviewed_by"],
            "reviewed_events_sha256": value["reviewed_events_sha256"],
        },
        "summary": {"case_count": len(cases)},
        "cases": cases,
        "promotion_policy": "Generated cases are candidates; add them to an owning suite only after deterministic review.",
    }


def write_eval_packet(root: Path, report: dict[str, object], output: str) -> str:
    path = safe_output_file(root, output, "feedback eval-packet output")
    source = report.get("source", {})
    source_path, source_identity = require_repo_file_pinned(
        root,
        str(source.get("path", "")) if isinstance(source, dict) else "",
        "feedback eval-packet source",
    )
    aliases_source = path == source_path
    if not aliases_source and path.exists():
        try:
            aliases_source = os.path.samefile(path, source_path)
        except OSError:
            aliases_source = False
    if aliases_source:
        raise SystemExit("feedback eval-packet output must not alias or overwrite the corrections source")
    relative_output = path.relative_to(root.resolve())
    parts = {part.casefold() for part in relative_output.parts}
    if "suites" in parts or not (
        "evidence" in parts or ("runs" in parts and "artifacts" in parts)
    ):
        raise SystemExit(
            "feedback eval-packet output must be a new evidence file or workflow run artifact, never an active suite"
        )
    if path.exists():
        raise SystemExit("feedback eval-packet output already exists; candidate generation never overwrites files")
    current_source = read_bounded_no_follow(
        source_path,
        label="feedback eval-packet source",
        max_bytes=MAX_CORRECTION_PACKET_BYTES,
        expected_identity=source_identity,
    )
    if hashlib.sha256(current_source).hexdigest() != str(source.get("sha256", "")):
        raise SystemExit("feedback eval-packet source changed after review packet generation")
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.normcase(str(path.parent)) != os.path.normcase(str(path.parent.resolve(strict=False))):
        raise SystemExit("feedback eval-packet output parent changed to a symlink or reparse alias")
    parent_metadata = os.lstat(path.parent)
    parent_reparse = bool(int(getattr(parent_metadata, "st_file_attributes", 0)) & 0x400)
    if stat.S_ISLNK(parent_metadata.st_mode) or parent_reparse or not stat.S_ISDIR(parent_metadata.st_mode):
        raise SystemExit("feedback eval-packet output parent must be a no-follow directory")
    serialized = (json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    descriptor = -1
    temporary_name = ""
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".pending",
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        current_parent = os.lstat(path.parent)
        if (parent_metadata.st_dev, parent_metadata.st_ino) != (
            current_parent.st_dev,
            current_parent.st_ino,
        ):
            raise SystemExit("feedback eval-packet output parent changed during publication")
        try:
            os.link(temporary_name, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise SystemExit(
                "feedback eval-packet output already exists; candidate generation never overwrites files"
            ) from exc
        published = os.lstat(path)
        staged = os.lstat(temporary_name)
        if (published.st_dev, published.st_ino) != (staged.st_dev, staged.st_ino):
            raise SystemExit("feedback eval-packet publication identity could not be verified")
    except FileExistsError as exc:
        raise SystemExit(
            "feedback eval-packet output already exists; candidate generation never overwrites files"
        ) from exc
    except OSError as exc:
        raise SystemExit(f"feedback eval-packet publication failed safely: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                temporary_name = ""
    return repo.relative(root, path)


def clear_report(
    root: Path,
    *,
    all_targets: bool,
    confirm_truncate: bool,
    reason: str,
    action_plan: str,
    dry_run: bool = False,
) -> dict[str, object]:
    if not all_targets:
        raise SystemExit("feedback clear only supports --all; target-specific clearing is not available")
    if not confirm_truncate:
        raise SystemExit("feedback clear requires --confirm-truncate")
    command_chars = repo_policy.int_value(root, "limits.feedback.command_chars")
    if not compact_text(reason, limit=command_chars):
        raise SystemExit("feedback clear requires --reason")
    action_plan_path = require_repo_file(root, action_plan, "feedback clear --action-plan")
    path = log_path(root)
    entries, skipped = read_entries(root)
    bytes_before = path.stat().st_size if path.exists() else 0
    report: dict[str, object] = {
        "schema_version": 1,
        "tool": "skill-manager.feedback-clear",
        "ok": True,
        "status": "would-clear" if dry_run else "cleared",
        "dry_run": dry_run,
        "timestamp": utc_now(),
        "cleared_path": repo.relative(root, path),
        "action_plan_path": repo.relative(root, action_plan_path),
        "reason": compact_text(reason, limit=command_chars),
        "entry_count_before": len(entries),
        "skipped_line_count_before": skipped,
        "bytes_before": bytes_before,
    }
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8", newline="\n")
    return report


def safe_output_dir(root: Path, value: str) -> Path:
    target = Path(value).expanduser()
    if not target.is_absolute():
        target = root / target
    lexical = Path(os.path.abspath(target))
    resolved = lexical.resolve(strict=False)
    if os.path.normcase(str(lexical)) != os.path.normcase(str(resolved)):
        raise SystemExit("feedback export output must not use a symlink or reparse alias")
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise SystemExit("feedback export output must stay inside the repository") from exc
    relative_output = lexical.relative_to(root.resolve())
    parts = {part.casefold() for part in relative_output.parts}
    if "suites" in parts or not (
        "evidence" in parts or ("runs" in parts and "artifacts" in parts)
    ):
        raise SystemExit(
            "feedback export output must be an evidence directory or workflow run artifact, never an active suite"
        )
    return lexical


def safe_output_file(root: Path, value: str, label: str) -> Path:
    target = Path(value).expanduser()
    if not target.is_absolute():
        target = root / target
    lexical = Path(os.path.abspath(target))
    resolved = lexical.resolve(strict=False)
    if os.path.normcase(str(lexical)) != os.path.normcase(str(resolved)):
        raise SystemExit(f"{label} must not use a symlink or reparse alias")
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise SystemExit(f"{label} must stay inside the repository") from exc
    if resolved.exists() and not resolved.is_file():
        raise SystemExit(f"{label} must be a file path")
    return lexical


def write_export(root: Path, report: dict[str, object], output_dir: str) -> list[str]:
    target = safe_output_dir(root, output_dir)
    target.mkdir(parents=True, exist_ok=True)
    if os.path.normcase(str(target)) != os.path.normcase(str(target.resolve(strict=True))):
        raise SystemExit("feedback export output changed to a symlink or reparse alias")
    target_metadata = os.lstat(target)
    target_reparse = bool(int(getattr(target_metadata, "st_file_attributes", 0)) & 0x400)
    if stat.S_ISLNK(target_metadata.st_mode) or target_reparse or not stat.S_ISDIR(target_metadata.st_mode):
        raise SystemExit("feedback export output must be a no-follow directory")
    json_path = target / "feedback-candidates.json"
    markdown_path = target / "feedback-candidates.md"
    if json_path.exists() or markdown_path.exists():
        raise SystemExit("feedback export candidates already exist; export never overwrites files")
    try:
        with json_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        with markdown_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(render_export_markdown(report))
    except FileExistsError as exc:
        raise SystemExit("feedback export candidates already exist; export never overwrites files") from exc
    return [repo.relative(root, json_path), repo.relative(root, markdown_path)]


def render_summary_markdown(report: dict[str, object]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Failure Feedback Summary",
        "",
        f"- Entries: {summary.get('entry_count', 0)}",
        f"- Groups: {summary.get('group_count', 0)}",
        f"- Log: `{report.get('log_path', '')}`",
    ]
    groups = report.get("groups") if isinstance(report.get("groups"), list) else []
    if groups:
        lines.extend(["", "## Groups", ""])
        for group in groups[:40]:
            if not isinstance(group, dict):
                continue
            lines.append(
                f"- `{group.get('target_kind')}/{group.get('target')}` "
                f"{group.get('failure_type')}: {group.get('count')} - {group.get('what_failed')}"
            )
    lines.append(f"- Next command: `{report.get('next_command', '')}`")
    return "\n".join(lines) + "\n"


def render_export_markdown(report: dict[str, object]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Feedback Improvement Candidates",
        "",
        f"- Candidates: {summary.get('candidate_count', 0)}",
        f"- Minimum count: {summary.get('min_count', 0)}",
        f"- Source entries: {summary.get('source_entry_count', 0)}",
        f"- Policy: {report.get('write_policy', '')}",
    ]
    candidates = report.get("candidates") if isinstance(report.get("candidates"), list) else []
    if candidates:
        lines.extend(["", "## Candidates", ""])
        for item in candidates:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- `{item.get('target_kind')}/{item.get('target')}` "
                f"{item.get('failure_type')} ({item.get('count')}): {item.get('what_failed')}"
            )
            command = str(item.get("suggested_next_command") or "")
            if command:
                lines.append(f"  Next: `{command}`")
    return "\n".join(lines) + "\n"


def render_clear_markdown(report: dict[str, object]) -> str:
    return "\n".join(
        [
            "# Failure Feedback Clear",
            "",
            f"- Status: {report.get('status')}",
            f"- Dry run: {report.get('dry_run')}",
            f"- Entries before: {report.get('entry_count_before', 0)}",
            f"- Bytes before: {report.get('bytes_before', 0)}",
            f"- Cleared path: `{report.get('cleared_path', '')}`",
            f"- Action plan: `{report.get('action_plan_path', '')}`",
            f"- Reason: {report.get('reason', '')}",
        ]
    ) + "\n"


def render_eval_packet_markdown(report: dict[str, object]) -> str:
    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    return "\n".join(
        [
            "# Correction Eval Packet",
            "",
            f"- Status: {report.get('status')}",
            f"- Cases: {summary.get('case_count', 0)}",
            f"- Reviewed source: `{source.get('path', '')}`",
            f"- Output: `{report.get('written', '')}`",
            f"- Promotion policy: {report.get('promotion_policy', '')}",
        ]
    ) + "\n"


def render_review_digest_markdown(report: dict[str, object]) -> str:
    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    return "\n".join(
        [
            "# Correction Review Digest",
            "",
            f"- Events: {report.get('event_count', 0)}",
            f"- Source: `{source.get('path', '')}`",
            f"- Reviewed events SHA-256: `{report.get('reviewed_events_sha256', '')}`",
            f"- Next step: {report.get('next_step', '')}",
        ]
    ) + "\n"


def print_report(report: dict[str, object], output_format: str, markdown_renderer) -> int:
    if output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(markdown_renderer(report), end="")
    return 0 if report.get("ok", True) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -B .agents/manage.py feedback")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    record = sub.add_parser("record")
    record.add_argument("--target-kind", choices=sorted(TARGET_KINDS), required=True)
    record.add_argument("--target", required=True)
    record.add_argument("--summary", required=True)
    record.add_argument("--bad", required=True)
    record.add_argument("--good", default="")
    record.add_argument("--context", action="append", default=[])
    record.add_argument("--caller", default="")
    record.add_argument("--trigger-command", default="")
    record.add_argument("--failure-type", default="")
    record.add_argument("--first-failing-fact", default="")
    record.add_argument("--raw-output-path", default="")
    record.add_argument("--output-digest", default="")
    record.add_argument("--suggested-next-command", default="")
    record.add_argument("--source-tool", default="skill-manager.feedback")
    record.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")

    summary = sub.add_parser("summary")
    target = summary.add_mutually_exclusive_group(required=True)
    target.add_argument("--target")
    target.add_argument("--all", action="store_true", dest="all_targets")
    summary.add_argument("--summary", action="store_true")
    summary.add_argument("--compact", action="store_true")
    summary.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")

    export = sub.add_parser("export")
    export_target = export.add_mutually_exclusive_group(required=True)
    export_target.add_argument("--target")
    export_target.add_argument("--all", action="store_true", dest="all_targets")
    export.add_argument("--min-count", type=int, default=2)
    export.add_argument("--output", required=True)
    export.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")

    eval_packet = sub.add_parser("eval-packet")
    eval_packet.add_argument("--corrections", required=True)
    eval_packet.add_argument("--output", required=True)
    eval_packet.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")

    review_digest = sub.add_parser("review-digest")
    review_digest.add_argument("--corrections", required=True)
    review_digest.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")

    clear = sub.add_parser("clear")
    clear.add_argument("--all", action="store_true", dest="all_targets")
    clear.add_argument("--confirm-truncate", action="store_true")
    clear.add_argument("--reason", required=True)
    clear.add_argument("--action-plan", required=True)
    clear.add_argument("--dry-run", action="store_true")
    clear.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    return parser


def feedback_group(raw_args: list[str], root: Path) -> int:
    args = build_parser().parse_args(raw_args)
    if args.subcommand == "record":
        entry = record_feedback(
            root,
            target_kind=args.target_kind,
            target=args.target,
            summary=args.summary,
            bad=args.bad,
            good=args.good,
            context_paths=args.context,
            caller=args.caller or None,
            trigger_command=args.trigger_command,
            failure_type=args.failure_type,
            first_failing_fact=args.first_failing_fact,
            raw_output_path=args.raw_output_path,
            output_digest=args.output_digest,
            suggested_next_command=args.suggested_next_command,
            source_tool=args.source_tool,
        )
        report = {
            "schema_version": 1,
            "tool": "skill-manager.feedback-record",
            "ok": True,
            "status": "recorded",
            "entry": entry,
            "log_path": repo.relative(root, log_path(root)),
            "next_command": f"python -B .agents/manage.py feedback summary --target {entry['target']}",
        }
        return print_report(report, args.output_format, render_summary_markdown)
    if args.subcommand == "summary":
        report = summary_report(
            root,
            target=args.target or "",
            all_targets=bool(args.all_targets),
            compact=bool(args.compact),
        )
        return print_report(report, args.output_format, render_summary_markdown)
    if args.subcommand == "export":
        report = export_report(
            root,
            target=args.target or "",
            all_targets=bool(args.all_targets),
            min_count=max(1, int(args.min_count)),
        )
        written = write_export(root, report, args.output)
        report["written"] = written
        return print_report(report, args.output_format, render_export_markdown)
    if args.subcommand == "eval-packet":
        report = build_eval_packet(root, args.corrections)
        report["written"] = write_eval_packet(root, report, args.output)
        return print_report(report, args.output_format, render_eval_packet_markdown)
    if args.subcommand == "review-digest":
        report = build_review_digest_report(root, args.corrections)
        return print_report(report, args.output_format, render_review_digest_markdown)
    report = clear_report(
        root,
        all_targets=bool(args.all_targets),
        confirm_truncate=bool(args.confirm_truncate),
        reason=args.reason,
        action_plan=args.action_plan,
        dry_run=bool(args.dry_run),
    )
    return print_report(report, args.output_format, render_clear_markdown)


def classify_failure_type(text: object) -> str:
    haystack = str(text or "").casefold()
    if "stale" in haystack or "out of sync" in haystack or "generated" in haystack:
        return "stale-generated-or-cache"
    if "missing" in haystack or "not found" in haystack:
        return "missing-file-or-dependency"
    if "timeout" in haystack or "timed out" in haystack:
        return "timeout"
    if "permission" in haystack or "access denied" in haystack:
        return "permission"
    if "blocked" in haystack:
        return "blocked"
    if "failed" in haystack or "error" in haystack or "traceback" in haystack:
        return "failed-check"
    return "unknown"


def infer_target(command: object = "", text: object = "", owner: object = "") -> tuple[str, str]:
    haystack = f"{command}\n{text}\n{owner}".replace("\\", "/").casefold()
    workflow = re.search(r"automations/([a-z0-9-]+)", haystack)
    if workflow:
        return "workflow", workflow.group(1)
    skill = re.search(r"\.agents/skills/([a-z0-9-]+)", haystack)
    if skill:
        return "skill", skill.group(1)
    owner_text = str(owner or "").strip()
    if owner_text in {"skill-manager", "workflow-manager", "local-ai-helper", "agent-benchmarking"}:
        return "skill", owner_text
    if "workflow" in haystack:
        return "skill", "workflow-manager"
    return "repo", "harness"


def record_what_now_failure(root: Path, report: dict[str, Any]) -> None:
    command_result = report.get("command_result") if isinstance(report.get("command_result"), dict) else {}
    if command_result.get("ok") is True or report.get("failure_type") == "passed":
        return
    command = str(report.get("command_label") or command_result.get("command") or "")
    target_kind, target = infer_target(command, report.get("first_failing_fact", ""), report.get("likely_owner", ""))
    summary = f"Managed command failed: {command or 'unknown command'}"
    contexts = [".agents/local-ai/cache/last-validation.txt"]
    raw_path = str(command_result.get("raw_output_path") or "")
    if raw_path:
        contexts.append(raw_path)
    output_summary = command_result.get("output_summary") if isinstance(command_result.get("output_summary"), dict) else {}
    try_record_feedback(
        root,
        target_kind=target_kind,
        target=target,
        summary=summary,
        bad=str(report.get("first_failing_fact") or command_result.get("distilled_output") or command_result.get("output_tail") or ""),
        good="what-now identified an owner and next deterministic command",
        context_paths=contexts,
        trigger_command=command,
        failure_type=str(report.get("failure_type") or classify_failure_type(report.get("first_failing_fact"))),
        first_failing_fact=str(report.get("first_failing_fact") or ""),
        raw_output_path=raw_path,
        output_digest=str(output_summary.get("digest") or ""),
        suggested_next_command=str(report.get("next_command") or ""),
        source_tool=str(report.get("tool") or "repo-what-now"),
    )


def record_finish_feedback(root: Path, report: dict[str, Any]) -> None:
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    for check in checks:
        if not isinstance(check, dict) or check.get("ok") is True:
            continue
        command = str(check.get("command") or "")
        detail = str(check.get("distilled_output") or check.get("output_tail") or check.get("issue") or "finish check failed")
        target_kind, target = infer_target(command, detail, "skill-manager")
        output_summary = check.get("output_summary") if isinstance(check.get("output_summary"), dict) else {}
        try_record_feedback(
            root,
            target_kind=target_kind,
            target=target,
            summary=f"Finish check failed: {command or 'unknown command'}",
            bad=detail,
            good="finish captured compact failed-check evidence",
            context_paths=[check.get("raw_output_path", "")],
            trigger_command=command,
            failure_type=classify_failure_type(detail),
            first_failing_fact=detail,
            raw_output_path=str(check.get("raw_output_path") or ""),
            output_digest=str(output_summary.get("digest") or ""),
            suggested_next_command=str(report.get("next_command") or ""),
            source_tool=str(report.get("tool") or "repo-finish"),
        )


def record_failure_triage(root: Path, command_label: str, output: str, result: dict[str, object]) -> None:
    target_kind, target = infer_target(command_label, output, "skill-manager")
    fact_chars = repo_policy.int_value(root, "limits.feedback.fact_chars")
    try_record_feedback(
        root,
        target_kind=target_kind,
        target=target,
        summary=f"Managed validation failed: {command_label}",
        bad=output,
        good="failure triage captured deterministic validation evidence",
        context_paths=[result.get("input_path", ".agents/local-ai/cache/last-validation.txt")],
        trigger_command=command_label,
        failure_type=classify_failure_type(output),
        first_failing_fact=compact_text(output, limit=fact_chars),
        suggested_next_command=str(result.get("suggested_command") or ""),
        source_tool="skill-manager.failure-triage",
    )


def record_workflow_finish_feedback(root: Path, report: dict[str, object]) -> None:
    if report.get("ok") is True:
        return
    workflow = str(report.get("workflow") or "")
    run_id = str(report.get("run_id") or "")
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    missing = report.get("missing_proof") if isinstance(report.get("missing_proof"), list) else []
    issue_text = "; ".join(str(item) for item in [*issues[:8], *missing[:8]])
    contexts = [
        report.get("state_path", ""),
        report.get("final_report_path", ""),
        report.get("context_packet_path", ""),
    ]
    try_record_feedback(
        root,
        target_kind="workflow",
        target=workflow or "unknown",
        summary=f"Workflow finish failed: {workflow or 'unknown workflow'}",
        bad=issue_text or "workflow finish reported unresolved issues",
        good="workflow finish produced structured issue and missing-proof evidence",
        context_paths=contexts,
        trigger_command=f"python -B .agents/manage.py workflow finish --name {workflow} --run-id {run_id}".strip(),
        failure_type="missing-proof" if missing else classify_failure_type(issue_text),
        first_failing_fact=str(issues[0] if issues else (missing[0] if missing else "")),
        suggested_next_command=str(report.get("next_command") or ""),
        source_tool=str(report.get("tool") or "workflow-manager.finish-run"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.add_argument("--root", default="")
    raw = list(sys.argv[1:] if argv is None else argv)
    root = repo.repo_root(None)
    if "--root" in raw:
        index = raw.index("--root")
        if index + 1 >= len(raw):
            raise SystemExit("--root requires a value")
        root = repo.repo_root(raw[index + 1])
        del raw[index : index + 2]
    return feedback_group(raw, root)


if __name__ == "__main__":
    raise SystemExit(main())
