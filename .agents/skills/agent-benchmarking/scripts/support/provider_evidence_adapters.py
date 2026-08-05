"""Provider-neutral evidence-adapter contracts for measured full-run usage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
EVIDENCE_FIELDS = {
    "schema_version",
    "adapter_id",
    "source_path",
    "source_sha256",
    "verifier_tool",
}
LEDGER_RECEIPT_FIELDS = {
    "schema_version",
    "adapter_id",
    "source_path",
    "source_sha256",
    "ledger_label",
    "thread_id",
}
CLAUDE_RECEIPT_FIELDS = {
    "schema_version",
    "tool",
    "run_id",
    "capture_nonce",
    "host_surface",
    "model_provider",
    "billing_route",
    "cli_version",
    "output_format",
    "process_exit_code",
    "session_id",
    "source",
}
CLAUDE_SOURCE_FIELDS = {"path", "sha256", "size_bytes"}
COPILOT_RECEIPT_FIELDS = {
    "schema_version",
    "tool",
    "run_id",
    "capture_nonce",
    "host_surface",
    "model_provider",
    "cli_version",
    "output_format",
    "content_capture",
    "process_exit_code",
    "session_id",
    "source",
}
COPILOT_SOURCE_FIELDS = {"path", "sha256", "size_bytes"}
CLAUDE_BILLING_ROUTES = {
    "anthropic-api",
    "aws-bedrock",
    "google-vertex",
    "microsoft-foundry",
    "gateway",
    "unknown",
}
HOST_CAPTURE_INDEX_FILE = "host-capture-index.json"
HOST_CAPTURE_INDEX_FIELDS = {"schema_version", "tool", "captures"}
HOST_CAPTURE_ENTRY_FIELDS = {
    "run_id",
    "receipt_path",
    "receipt_sha256",
    "capture_nonce",
    "model_label",
}
ARTIFACT_TOKENIZER_RECEIPT_FIELDS = {
    "schema_version",
    "tool",
    "tokenizer",
    "tokenizer_package",
    "inputs",
    "outputs",
}
ARTIFACT_TOKENIZER_ROW_FIELDS = {"path", "sha256", "tokens"}

# A declaration is not an implementation. Only implemented adapters may make a
# full-run token measurement eligible for an optimization claim.
ADAPTERS: dict[str, dict[str, Any]] = {
    "codex-rollout-v1": {
        "status": "implemented",
        "host_surfaces": ["codex"],
        "model_providers": ["openai", "anthropic", "local", "other"],
        "supported_arms": ["serial-active-model"],
        "verifier_tool": "agent-benchmarking.codex-usage-ledger",
        "evidence_kind": "durable-rollout-jsonl",
        "allowed_provenances": ["provider_telemetry"],
    },
    "claude-code-result-v1": {
        "status": "implemented",
        "host_surfaces": ["claude-code"],
        "model_providers": ["anthropic"],
        "supported_arms": ["serial-active-model"],
        "verifier_tool": "agent-benchmarking.claude-code-result",
        "evidence_kind": "coordinator-captured-terminal-result-jsonl",
        "allowed_provenances": ["provider_telemetry"],
    },
    "github-copilot-otel-v1": {
        "status": "implemented",
        "host_surfaces": ["github-copilot"],
        "model_providers": ["other"],
        "supported_arms": ["serial-active-model"],
        "verifier_tool": "agent-benchmarking.github-copilot-otel",
        "evidence_kind": "coordinator-captured-otel-file-jsonl",
        "allowed_provenances": ["provider_telemetry"],
    },
    "openai-responses-usage-v1": {
        "status": "implemented",
        "host_surfaces": ["openai-responses-api"],
        "model_providers": ["openai"],
        "supported_arms": ["serial-active-model"],
        "verifier_tool": "agent-benchmarking.openai-responses-adapter",
        "evidence_kind": "coordinator-sanitized-response-receipt",
        "allowed_provenances": ["provider_telemetry"],
    },
}


def normalize_model_provider(value: object) -> str:
    """Map a raw provider identifier to the portable V1 provider taxonomy."""

    normalized = str(value or "").strip().lower()
    if normalized in {"openai", "anthropic", "local"}:
        return normalized
    if normalized:
        return "other"
    return "unknown"


def unavailable_evidence() -> dict[str, Any]:
    """Return the canonical fail-closed evidence placeholder."""

    return {
        "schema_version": SCHEMA_VERSION,
        "adapter_id": "unverified",
        "source_path": "",
        "source_sha256": "",
        "verifier_tool": "",
    }


def codex_rollout_evidence(*, source_path: str, source_sha256: str) -> dict[str, Any]:
    """Bind a Codex measurement to the durable rollout verified by the ledger."""

    return {
        "schema_version": SCHEMA_VERSION,
        "adapter_id": "codex-rollout-v1",
        "source_path": str(source_path),
        "source_sha256": str(source_sha256),
        "verifier_tool": ADAPTERS["codex-rollout-v1"]["verifier_tool"],
    }


def codex_ledger_receipt(
    *,
    source_path: str,
    source_sha256: str,
    ledger_label: str,
    thread_id: str,
) -> dict[str, Any]:
    """Bind a report to a ledger row; the verifier supplies the trusted Codex root."""

    return {
        "schema_version": SCHEMA_VERSION,
        "adapter_id": "codex-usage-ledger-v1",
        "source_path": str(source_path),
        "source_sha256": str(source_sha256),
        "ledger_label": str(ledger_label),
        "thread_id": str(thread_id),
    }


def artifact_tokenizer_evidence(
    *, source_path: str, source_sha256: str
) -> dict[str, Any]:
    """Bind an artifact-scoped measurement to a deterministic tokenizer receipt."""

    return {
        "schema_version": SCHEMA_VERSION,
        "adapter_id": "artifact-tokenizer-v1",
        "source_path": str(source_path),
        "source_sha256": str(source_sha256),
        "verifier_tool": "agent-benchmarking.artifact-tokenizer",
    }


def validate_evidence_shape(value: object) -> list[str]:
    issues: list[str] = []
    if not isinstance(value, dict):
        return ["token_measurement.evidence must be an object"]
    issues.extend(
        f"token_measurement.evidence.{field} is required"
        for field in sorted(EVIDENCE_FIELDS - set(value))
    )
    issues.extend(
        f"token_measurement.evidence.{field} is not allowed"
        for field in sorted(set(value) - EVIDENCE_FIELDS)
    )
    if type(value.get("schema_version")) is not int or value.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"token_measurement.evidence.schema_version must be {SCHEMA_VERSION}")
    for field in ("adapter_id", "source_path", "source_sha256", "verifier_tool"):
        if not isinstance(value.get(field), str):
            issues.append(f"token_measurement.evidence.{field} must be a string")
    return issues


def eligibility_issues(
    value: object,
    *,
    host_surface: str,
    model_provider: str,
    provenance: str,
) -> list[str]:
    """Return fail-closed adapter issues for a measured full-run claim."""

    issues = validate_evidence_shape(value)
    if issues or not isinstance(value, dict):
        return issues
    adapter_id = str(value.get("adapter_id", ""))
    adapter = ADAPTERS.get(adapter_id)
    if adapter is None:
        return [f"full-run evidence adapter is unknown or unverified: {adapter_id or 'missing'}"]
    if adapter.get("status") != "implemented":
        issues.append(f"full-run evidence adapter {adapter_id} is declaration-only")
    if host_surface not in adapter.get("host_surfaces", []):
        issues.append(f"full-run evidence adapter {adapter_id} does not support host_surface {host_surface}")
    if model_provider not in adapter.get("model_providers", []):
        issues.append(f"full-run evidence adapter {adapter_id} does not support model_provider {model_provider}")
    if provenance not in adapter.get("allowed_provenances", []):
        issues.append(f"full-run evidence adapter {adapter_id} does not support provenance {provenance}")
    source_path = str(value.get("source_path", ""))
    if not source_path.strip():
        issues.append("full-run evidence source_path must be non-empty")
    if not SHA256_RE.fullmatch(str(value.get("source_sha256", ""))):
        issues.append("full-run evidence source_sha256 must be a lowercase SHA-256")
    expected_tool = str(adapter.get("verifier_tool", ""))
    if value.get("verifier_tool") != expected_tool:
        issues.append(
            f"full-run evidence verifier_tool must be {expected_tool or 'declared by the adapter'}"
        )
    return issues


def _lexical_no_alias_path(
    raw_path: str | Path,
    *,
    evidence_root: Path | None = None,
    label: str = "evidence source",
) -> tuple[Path, tuple[int, int]]:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        if evidence_root is None:
            raise OSError(f"relative {label} requires an evidence root")
        candidate = evidence_root / candidate
    lexical = Path(os.path.abspath(candidate))
    before = os.lstat(lexical)
    resolved = lexical.resolve(strict=True)
    if os.path.normcase(str(lexical)) != os.path.normcase(str(resolved)):
        raise OSError(f"{label} must not use a symlink or reparse alias")
    after = os.lstat(lexical)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise OSError(f"{label} changed while resolving")
    reparse = bool(int(getattr(after, "st_file_attributes", 0)) & 0x400)
    if stat.S_ISLNK(after.st_mode) or reparse:
        raise OSError(f"{label} must not use a symlink or reparse alias")
    if evidence_root is not None and not Path(raw_path).is_absolute():
        root = Path(os.path.abspath(evidence_root)).resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise OSError(f"relative {label} escapes its evidence root") from exc
    return lexical, (after.st_dev, after.st_ino)


def _read_no_follow(path: Path, *, expected_identity: tuple[int, int] | None = None) -> bytes:
    metadata = os.lstat(path)
    reparse = bool(int(getattr(metadata, "st_file_attributes", 0)) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or reparse or not stat.S_ISREG(metadata.st_mode):
        raise OSError("evidence source must be a no-follow regular file")
    if metadata.st_size > MAX_EVIDENCE_BYTES:
        raise OSError(f"evidence source exceeds {MAX_EVIDENCE_BYTES} bytes")
    if expected_identity is not None and (metadata.st_dev, metadata.st_ino) != expected_identity:
        raise OSError("evidence source identity changed before opening")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino):
            raise OSError("evidence source changed while opening")
        data = handle.read(MAX_EVIDENCE_BYTES + 1)
        if len(data) > MAX_EVIDENCE_BYTES:
            raise OSError(f"evidence source exceeds {MAX_EVIDENCE_BYTES} bytes")
        return data


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number: {value}")


def _strict_json(data: bytes, *, label: str) -> object:
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{label} is invalid JSON: {exc}") from exc


def _read_trusted_capture(
    raw_path: object,
    *,
    trusted_root: Path | None,
    label: str,
) -> tuple[Path, bytes]:
    if trusted_root is None:
        raise OSError(f"{label} requires an out-of-band trusted host capture root")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise OSError(f"{label} path must be a non-empty relative path")
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise OSError(f"{label} path must be relative to the trusted host capture root")
    source, identity = _lexical_no_alias_path(
        candidate,
        evidence_root=trusted_root,
        label=label,
    )
    metadata = os.lstat(source)
    if int(getattr(metadata, "st_nlink", 1)) != 1:
        raise OSError(f"{label} must not be hard-linked")
    return source, _read_no_follow(source, expected_identity=identity)


def _tokenizer_encoding(tokenizer: str):
    if not tokenizer.startswith("tiktoken:"):
        raise ValueError("artifact tokenizer must use a tiktoken:<encoding> identity")
    encoding_name = tokenizer.removeprefix("tiktoken:").strip()
    if not encoding_name:
        raise ValueError("artifact tokenizer encoding must be non-empty")
    try:
        import tiktoken  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ValueError("artifact tokenizer verification requires tiktoken") from exc
    try:
        return tiktoken.get_encoding(encoding_name), {
            "name": "tiktoken",
            "version": str(getattr(tiktoken, "__version__", "unknown")),
        }
    except (KeyError, ValueError) as exc:
        raise ValueError(f"artifact tokenizer encoding is unavailable: {encoding_name}") from exc


def build_artifact_tokenizer_receipt(
    *,
    evidence_root: Path,
    tokenizer: str,
    input_paths: list[str | Path],
    output_paths: list[str | Path],
) -> dict[str, Any]:
    """Read bounded UTF-8 artifacts and return a deterministic V1 count receipt."""

    root = Path(os.path.abspath(evidence_root)).resolve(strict=True)
    encoding, tokenizer_package = _tokenizer_encoding(tokenizer)
    seen: set[Path] = set()

    def rows(paths: list[str | Path], *, role: str) -> list[dict[str, Any]]:
        if not isinstance(paths, list) or len(paths) > 1000:
            raise ValueError(f"artifact tokenizer {role} paths must contain at most 1000 items")
        result: list[dict[str, Any]] = []
        for raw_path in paths:
            candidate = Path(raw_path)
            if candidate.is_absolute():
                try:
                    relative = candidate.resolve(strict=True).relative_to(root)
                except ValueError as exc:
                    raise ValueError(f"artifact tokenizer {role} path escapes its evidence root") from exc
            else:
                relative = candidate
            source, identity = _lexical_no_alias_path(
                relative,
                evidence_root=root,
                label=f"artifact tokenizer {role} source",
            )
            if source in seen:
                raise ValueError("artifact tokenizer paths must be unique across roles")
            seen.add(source)
            if int(getattr(os.lstat(source), "st_nlink", 1)) != 1:
                raise ValueError("artifact tokenizer sources must not be hard-linked")
            data = _read_no_follow(source, expected_identity=identity)
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("artifact tokenizer sources must be UTF-8") from exc
            result.append(
                {
                    "path": relative.as_posix(),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "tokens": len(encoding.encode(content)),
                }
            )
        return result

    inputs = rows(input_paths, role="input")
    outputs = rows(output_paths, role="output")
    if not inputs and not outputs:
        raise ValueError("artifact tokenizer receipt requires at least one artifact")
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "agent-benchmarking.artifact-tokenizer-receipt",
        "tokenizer": tokenizer,
        "tokenizer_package": tokenizer_package,
        "inputs": inputs,
        "outputs": outputs,
    }


def verify_artifact_tokenizer_binding(
    measurement: object,
    *,
    evidence_root: Path | None,
) -> list[str]:
    """Reopen every artifact and reproduce an artifact-scoped token measurement."""

    if not isinstance(measurement, dict):
        return ["artifact token measurement must be an object"]
    evidence = measurement.get("evidence")
    if not isinstance(evidence, dict):
        return ["artifact token measurement evidence must be an object"]
    issues: list[str] = []
    if evidence.get("adapter_id") != "artifact-tokenizer-v1":
        issues.append("artifact gate requires artifact-tokenizer-v1 evidence")
    if evidence.get("verifier_tool") != "agent-benchmarking.artifact-tokenizer":
        issues.append("artifact tokenizer verifier tool is invalid")
    if evidence_root is None:
        issues.append("artifact tokenizer verification requires the benchmark evidence root")
    if issues:
        return issues
    assert evidence_root is not None
    raw_receipt_path = evidence.get("source_path")
    if not isinstance(raw_receipt_path, str) or not raw_receipt_path.strip() or Path(raw_receipt_path).is_absolute():
        return ["artifact tokenizer receipt path must be a non-empty relative path"]
    try:
        receipt_path, receipt_identity = _lexical_no_alias_path(
            raw_receipt_path,
            evidence_root=evidence_root,
            label="artifact tokenizer receipt",
        )
        if int(getattr(os.lstat(receipt_path), "st_nlink", 1)) != 1:
            raise OSError("artifact tokenizer receipt must not be hard-linked")
        receipt_bytes = _read_no_follow(
            receipt_path,
            expected_identity=receipt_identity,
        )
        receipt = _strict_json(receipt_bytes, label="artifact tokenizer receipt")
    except (OSError, ValueError) as exc:
        return [f"artifact tokenizer receipt is unavailable or invalid: {exc}"]
    if hashlib.sha256(receipt_bytes).hexdigest() != evidence.get("source_sha256"):
        issues.append("artifact tokenizer receipt SHA-256 does not match evidence")
    if not isinstance(receipt, dict) or set(receipt) != ARTIFACT_TOKENIZER_RECEIPT_FIELDS:
        return [*issues, "artifact tokenizer receipt has an invalid shape"]
    if type(receipt.get("schema_version")) is not int or receipt.get("schema_version") != 1:
        issues.append("artifact tokenizer receipt.schema_version must be the integer 1")
    if receipt.get("tool") != "agent-benchmarking.artifact-tokenizer-receipt":
        issues.append("artifact tokenizer receipt.tool is invalid")
    tokenizer = receipt.get("tokenizer")
    if tokenizer != measurement.get("tokenizer_or_estimator"):
        issues.append("artifact tokenizer receipt tokenizer does not match token_measurement")
    try:
        encoding, installed_package = _tokenizer_encoding(str(tokenizer))
    except ValueError as exc:
        return [*issues, str(exc)]
    tokenizer_package = receipt.get("tokenizer_package")
    if tokenizer_package != installed_package:
        issues.append("artifact tokenizer package identity does not match the verifier runtime")
    root = Path(os.path.abspath(evidence_root)).resolve(strict=True)
    seen: set[Path] = set()
    totals: dict[str, int] = {"inputs": 0, "outputs": 0}
    for role in ("inputs", "outputs"):
        rows = receipt.get(role)
        if not isinstance(rows, list) or len(rows) > 1000:
            issues.append(f"artifact tokenizer receipt.{role} must contain at most 1000 rows")
            continue
        for position, row in enumerate(rows):
            label = f"artifact tokenizer receipt.{role}[{position}]"
            if not isinstance(row, dict) or set(row) != ARTIFACT_TOKENIZER_ROW_FIELDS:
                issues.append(f"{label} has an invalid shape")
                continue
            raw_path = row.get("path")
            if not isinstance(raw_path, str) or not raw_path.strip() or Path(raw_path).is_absolute():
                issues.append(f"{label}.path must be a non-empty relative path")
                continue
            if not SHA256_RE.fullmatch(str(row.get("sha256", ""))):
                issues.append(f"{label}.sha256 must be a lowercase SHA-256")
            if not _token_count(row.get("tokens")):
                issues.append(f"{label}.tokens must be an exact non-negative integer")
                continue
            try:
                source, identity = _lexical_no_alias_path(
                    raw_path,
                    evidence_root=root,
                    label=f"artifact tokenizer {role} source",
                )
                if source in seen:
                    raise OSError("artifact tokenizer paths must be unique across roles")
                seen.add(source)
                if int(getattr(os.lstat(source), "st_nlink", 1)) != 1:
                    raise OSError("artifact tokenizer source must not be hard-linked")
                data = _read_no_follow(source, expected_identity=identity)
                content = data.decode("utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                issues.append(f"{label} source is unavailable or invalid: {exc}")
                continue
            if hashlib.sha256(data).hexdigest() != row.get("sha256"):
                issues.append(f"{label} SHA-256 does not match source bytes")
            observed_tokens = len(encoding.encode(content))
            if observed_tokens != row.get("tokens"):
                issues.append(f"{label} token count does not match source bytes")
            totals[role] += observed_tokens
    if not seen:
        issues.append("artifact tokenizer receipt requires at least one artifact")
    expected_totals = {
        "inputs": measurement.get("input_tokens"),
        "outputs": measurement.get("output_tokens"),
    }
    if totals != expected_totals:
        issues.append("artifact tokenizer totals do not match token_measurement")
    details = measurement.get("details")
    if not isinstance(details, dict) or any(
        not isinstance(details.get(field), dict)
        or details[field].get("availability") != "unavailable"
        or details[field].get("value") is not None
        for field in (
            "cache_read_input_tokens",
            "cache_write_input_tokens",
            "reasoning_output_tokens",
        )
    ):
        issues.append("artifact tokenizer evidence cannot attest cache or reasoning details")
    return sorted(set(issues))


def _trusted_capture_entry(
    *,
    trusted_root: Path | None,
    expected_run_id: str | None,
    expected_model_label: str | None,
    receipt_path: str,
    receipt_sha256: str,
) -> tuple[dict[str, str] | None, list[str]]:
    if expected_run_id is None or not expected_run_id.strip():
        return None, ["host evidence requires an out-of-band benchmark run identity"]
    try:
        _index_path, index_bytes = _read_trusted_capture(
            HOST_CAPTURE_INDEX_FILE,
            trusted_root=trusted_root,
            label="host capture index",
        )
        index = _strict_json(index_bytes, label="host capture index")
    except (OSError, ValueError) as exc:
        return None, [f"host capture index is unavailable or invalid: {exc}"]
    if not isinstance(index, dict) or set(index) != HOST_CAPTURE_INDEX_FIELDS:
        return None, ["host capture index has an invalid shape"]
    if type(index.get("schema_version")) is not int or index.get("schema_version") != 1:
        return None, ["host capture index.schema_version must be the integer 1"]
    if index.get("tool") != "agent-benchmarking.host-capture-index":
        return None, ["host capture index.tool is invalid"]
    captures = index.get("captures")
    if not isinstance(captures, list) or not captures or len(captures) > 1000:
        return None, ["host capture index.captures must contain 1 through 1000 entries"]
    issues: list[str] = []
    entries: dict[str, dict[str, str]] = {}
    for position, raw_entry in enumerate(captures):
        if not isinstance(raw_entry, dict) or set(raw_entry) != HOST_CAPTURE_ENTRY_FIELDS:
            issues.append(f"host capture index.captures[{position}] has an invalid shape")
            continue
        if not all(
            isinstance(raw_entry.get(field), str)
            and bool(raw_entry[field].strip())
            for field in HOST_CAPTURE_ENTRY_FIELDS
        ):
            issues.append(f"host capture index.captures[{position}] fields must be non-empty strings")
            continue
        entry = {field: raw_entry[field] for field in HOST_CAPTURE_ENTRY_FIELDS}
        if not SHA256_RE.fullmatch(entry["receipt_sha256"]):
            issues.append(f"host capture index.captures[{position}].receipt_sha256 is invalid")
        if entry["run_id"] in entries:
            issues.append(f"host capture index repeats run_id {entry['run_id']}")
        entries[entry["run_id"]] = entry
    entry = entries.get(expected_run_id)
    if entry is None:
        issues.append("host capture index has no entry for the benchmark run")
    else:
        if entry["receipt_path"] != receipt_path:
            issues.append("host capture receipt path does not match the trusted index")
        if entry["receipt_sha256"] != receipt_sha256:
            issues.append("host capture receipt SHA-256 does not match the trusted index")
        if not expected_model_label or entry["model_label"] != expected_model_label:
            issues.append("host capture model label does not match the trusted benchmark identity")
    return entry, issues


def _token_count(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= (2**63 - 1)
    )


def _measurement_totals(measurement: dict[str, Any]) -> dict[str, int | None]:
    details = measurement.get("details")
    details = details if isinstance(details, dict) else {}

    def detail(field: str) -> int | None:
        row = details.get(field)
        if not isinstance(row, dict) or row.get("availability") == "unavailable":
            return None
        value = row.get("value")
        return int(value) if _token_count(value) else None

    return {
        "input_tokens": measurement.get("input_tokens") if _token_count(measurement.get("input_tokens")) else None,
        "cache_read_input_tokens": detail("cache_read_input_tokens"),
        "cache_write_input_tokens": detail("cache_write_input_tokens"),
        "output_tokens": measurement.get("output_tokens") if _token_count(measurement.get("output_tokens")) else None,
        "reasoning_output_tokens": detail("reasoning_output_tokens"),
        "total_tokens": measurement.get("total_tokens") if _token_count(measurement.get("total_tokens")) else None,
    }


def _required_count(value: dict[str, Any], names: tuple[str, ...], *, label: str) -> int:
    present = [name for name in names if name in value]
    if len(present) != 1:
        raise ValueError(f"{label} must contain exactly one of: {', '.join(names)}")
    count = value[present[0]]
    if not _token_count(count):
        raise ValueError(f"{label} must be an exact non-negative 64-bit integer")
    return int(count)


def _claude_usage(value: object, *, label: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return {
        "base_input": _required_count(value, ("input_tokens", "inputTokens"), label=f"{label}.input"),
        "cache_read": _required_count(
            value,
            ("cache_read_input_tokens", "cacheReadInputTokens"),
            label=f"{label}.cache_read",
        ),
        "cache_write": _required_count(
            value,
            ("cache_creation_input_tokens", "cacheCreationInputTokens"),
            label=f"{label}.cache_creation",
        ),
        "output": _required_count(value, ("output_tokens", "outputTokens"), label=f"{label}.output"),
    }


def _verify_claude_result(
    measurement: dict[str, Any],
    receipt: object,
    *,
    trusted_root: Path | None,
    expected_run_id: str | None,
    expected_model_label: str | None,
) -> list[str]:
    if not isinstance(receipt, dict):
        return ["Claude capture receipt must be an object"]
    issues: list[str] = []
    issues.extend(
        f"Claude capture receipt.{field} is required"
        for field in sorted(CLAUDE_RECEIPT_FIELDS - set(receipt))
    )
    issues.extend(
        f"Claude capture receipt.{field} is not allowed"
        for field in sorted(set(receipt) - CLAUDE_RECEIPT_FIELDS)
    )
    if type(receipt.get("schema_version")) is not int or receipt.get("schema_version") != 1:
        issues.append("Claude capture receipt.schema_version must be the integer 1")
    if receipt.get("tool") != "agent-benchmarking.claude-code-result-receipt":
        issues.append("Claude capture receipt.tool is invalid")
    if expected_run_id is None or not expected_run_id.strip():
        issues.append("Claude evidence requires an out-of-band expected benchmark run id")
    elif receipt.get("run_id") != expected_run_id:
        issues.append("Claude capture receipt.run_id does not match the benchmark report")
    for field in ("run_id", "capture_nonce", "cli_version"):
        if not isinstance(receipt.get(field), str) or not str(receipt.get(field)).strip():
            issues.append(f"Claude capture receipt.{field} must be a non-empty string")
    if not isinstance(receipt.get("session_id"), str) or not UUID_RE.fullmatch(
        receipt.get("session_id", "")
    ):
        issues.append("Claude capture receipt.session_id must be a UUID")
    if receipt.get("host_surface") != "claude-code":
        issues.append("Claude capture receipt.host_surface must be claude-code")
    if receipt.get("model_provider") != "anthropic":
        issues.append("Claude capture receipt.model_provider must be anthropic")
    if receipt.get("billing_route") not in CLAUDE_BILLING_ROUTES:
        issues.append("Claude capture receipt.billing_route is invalid")
    if receipt.get("output_format") != "stream-json":
        issues.append("Claude capture receipt.output_format must be stream-json")
    if type(receipt.get("process_exit_code")) is not int or receipt.get("process_exit_code") != 0:
        issues.append("Claude capture receipt.process_exit_code must be the integer 0")
    source = receipt.get("source")
    if not isinstance(source, dict):
        issues.append("Claude capture receipt.source must be an object")
        source = {}
    else:
        issues.extend(
            f"Claude capture receipt.source.{field} is required"
            for field in sorted(CLAUDE_SOURCE_FIELDS - set(source))
        )
        issues.extend(
            f"Claude capture receipt.source.{field} is not allowed"
            for field in sorted(set(source) - CLAUDE_SOURCE_FIELDS)
        )
    if not SHA256_RE.fullmatch(str(source.get("sha256", ""))):
        issues.append("Claude capture receipt.source.sha256 must be a lowercase SHA-256")
    if not _token_count(source.get("size_bytes")):
        issues.append("Claude capture receipt.source.size_bytes must be an exact non-negative integer")
    if issues:
        return issues
    try:
        _stream_path, stream = _read_trusted_capture(
            source["path"],
            trusted_root=trusted_root,
            label="Claude stream capture",
        )
    except OSError as exc:
        return [f"Claude stream capture is unavailable or unsafe: {exc}"]
    if len(stream) != source["size_bytes"]:
        issues.append("Claude stream capture size does not match its receipt")
    if hashlib.sha256(stream).hexdigest() != source["sha256"]:
        issues.append("Claude stream capture SHA-256 does not match its receipt")
    if not stream or not stream.endswith(b"\n"):
        issues.append("Claude stream capture must end with a terminal newline")
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(stream.splitlines(), start=1):
        try:
            row = _strict_json(raw_line, label=f"Claude stream line {line_number}")
        except ValueError as exc:
            issues.append(str(exc))
            continue
        if not isinstance(row, dict):
            issues.append(f"Claude stream line {line_number} must be an object")
            continue
        observed_session = row.get("session_id")
        if observed_session is not None and observed_session != receipt["session_id"]:
            issues.append(f"Claude stream line {line_number} session_id does not match its receipt")
        rows.append(row)
    results = [row for row in rows if row.get("type") == "result"]
    if len(results) != 1:
        issues.append("Claude stream capture must contain exactly one result record")
        return issues
    if not rows or rows[-1] is not results[0]:
        issues.append("Claude result record must be the terminal stream record")
    result = results[0]
    if result.get("session_id") != receipt["session_id"]:
        issues.append("Claude result session_id does not match its receipt")
    if result.get("subtype") != "success" or result.get("is_error") is not False:
        issues.append("Claude result must report subtype success and is_error false")
    try:
        aggregate = _claude_usage(result.get("usage"), label="Claude result.usage")
    except ValueError as exc:
        issues.append(str(exc))
        return issues
    model_usage = result.get("modelUsage")
    if not isinstance(model_usage, dict) or not model_usage:
        issues.append("Claude result.modelUsage must be a non-empty object")
        return issues
    if set(model_usage) != {expected_model_label}:
        issues.append("Claude result modelUsage does not match the trusted benchmark model")
    per_model: list[dict[str, int]] = []
    for model, usage in model_usage.items():
        if not isinstance(model, str) or not model.strip():
            issues.append("Claude result.modelUsage keys must be non-empty model identifiers")
            continue
        try:
            per_model.append(_claude_usage(usage, label=f"Claude result.modelUsage[{model}]"))
        except ValueError as exc:
            issues.append(str(exc))
    if len(per_model) == len(model_usage):
        for field in ("base_input", "cache_read", "cache_write", "output"):
            if sum(row[field] for row in per_model) != aggregate[field]:
                issues.append(f"Claude modelUsage {field} total does not match aggregate usage")
    observed = {
        "input_tokens": aggregate["base_input"] + aggregate["cache_read"] + aggregate["cache_write"],
        "cache_read_input_tokens": aggregate["cache_read"],
        "cache_write_input_tokens": aggregate["cache_write"],
        "output_tokens": aggregate["output"],
        "reasoning_output_tokens": None,
        "total_tokens": aggregate["base_input"] + aggregate["cache_read"] + aggregate["cache_write"] + aggregate["output"],
    }
    if _measurement_totals(measurement) != observed:
        issues.append("Claude result usage does not match token_measurement")
    return issues


def _otel_usage(attributes: dict[str, Any], *, label: str) -> dict[str, int | None]:
    result: dict[str, int | None] = {
        "input_tokens": attributes.get("gen_ai.usage.input_tokens"),
        "cache_read_input_tokens": attributes.get(
            "gen_ai.usage.cache_read.input_tokens"
        ),
        "cache_write_input_tokens": attributes.get(
            "gen_ai.usage.cache_creation.input_tokens"
        ),
        "output_tokens": attributes.get("gen_ai.usage.output_tokens"),
        "reasoning_output_tokens": attributes.get(
            "gen_ai.usage.reasoning.output_tokens"
        ),
    }
    for field in ("input_tokens", "output_tokens"):
        if not _token_count(result[field]):
            raise ValueError(f"{label}.{field} must be an exact non-negative integer")
    for field in (
        "cache_read_input_tokens",
        "cache_write_input_tokens",
        "reasoning_output_tokens",
    ):
        if result[field] is not None and not _token_count(result[field]):
            raise ValueError(
                f"{label}.{field} must be absent or an exact non-negative integer"
            )
    cache_read = int(result["cache_read_input_tokens"] or 0)
    cache_write = int(result["cache_write_input_tokens"] or 0)
    if cache_read + cache_write > int(result["input_tokens"] or 0):
        raise ValueError(f"{label} cache read plus creation exceeds input tokens")
    if (
        result["reasoning_output_tokens"] is not None
        and int(result["reasoning_output_tokens"] or 0)
        > int(result["output_tokens"] or 0)
    ):
        raise ValueError(f"{label} reasoning exceeds output tokens")
    result["total_tokens"] = int(result["input_tokens"] or 0) + int(
        result["output_tokens"] or 0
    )
    return result


def _aggregate_otel_usage(
    rows: list[dict[str, int | None]],
) -> dict[str, int | None]:
    aggregate: dict[str, int | None] = {}
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        aggregate[field] = sum(int(row[field] or 0) for row in rows)
    for field in (
        "cache_read_input_tokens",
        "cache_write_input_tokens",
        "reasoning_output_tokens",
    ):
        aggregate[field] = (
            sum(int(row[field] or 0) for row in rows)
            if all(row[field] is not None for row in rows)
            else None
        )
    return aggregate


def _verify_copilot_otel(
    measurement: dict[str, Any],
    receipt: object,
    *,
    trusted_root: Path | None,
    expected_run_id: str | None,
    expected_model_label: str | None,
) -> list[str]:
    if not isinstance(receipt, dict):
        return ["Copilot OTel capture receipt must be an object"]
    issues: list[str] = []
    issues.extend(
        f"Copilot OTel capture receipt.{field} is required"
        for field in sorted(COPILOT_RECEIPT_FIELDS - set(receipt))
    )
    issues.extend(
        f"Copilot OTel capture receipt.{field} is not allowed"
        for field in sorted(set(receipt) - COPILOT_RECEIPT_FIELDS)
    )
    if type(receipt.get("schema_version")) is not int or receipt.get("schema_version") != 1:
        issues.append("Copilot OTel capture receipt.schema_version must be the integer 1")
    if receipt.get("tool") != "agent-benchmarking.github-copilot-otel-receipt":
        issues.append("Copilot OTel capture receipt.tool is invalid")
    if not expected_run_id or receipt.get("run_id") != expected_run_id:
        issues.append("Copilot OTel capture receipt.run_id does not match the benchmark report")
    if receipt.get("host_surface") != "github-copilot":
        issues.append("Copilot OTel capture receipt.host_surface must be github-copilot")
    if receipt.get("model_provider") != "other":
        issues.append("Copilot OTel capture receipt.model_provider must preserve github as other")
    cli_version = receipt.get("cli_version")
    if not isinstance(cli_version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", cli_version):
        issues.append("Copilot OTel capture receipt.cli_version must be a semantic version")
    if receipt.get("output_format") != "otel-file-jsonl":
        issues.append("Copilot OTel capture receipt.output_format must be otel-file-jsonl")
    if receipt.get("content_capture") is not False:
        issues.append("Copilot OTel evidence requires content capture disabled")
    if type(receipt.get("process_exit_code")) is not int or receipt.get("process_exit_code") != 0:
        issues.append("Copilot OTel capture process must exit successfully")
    session_id = receipt.get("session_id")
    if not isinstance(session_id, str) or not UUID_RE.fullmatch(session_id):
        issues.append("Copilot OTel capture receipt.session_id must be a UUID")
    source = receipt.get("source")
    if not isinstance(source, dict) or set(source) != COPILOT_SOURCE_FIELDS:
        issues.append("Copilot OTel capture receipt.source has an invalid shape")
    elif (
        not isinstance(source.get("path"), str)
        or not source.get("path")
        or not SHA256_RE.fullmatch(str(source.get("sha256", "")))
        or not _token_count(source.get("size_bytes"))
    ):
        issues.append("Copilot OTel capture receipt.source metadata is invalid")
    if issues:
        return issues
    assert isinstance(source, dict)
    try:
        _path, data = _read_trusted_capture(
            source["path"],
            trusted_root=trusted_root,
            label="Copilot OTel source",
        )
    except OSError as exc:
        return [f"Copilot OTel source is unavailable or unsafe: {exc}"]
    if len(data) != source["size_bytes"]:
        issues.append("Copilot OTel source size does not match receipt")
    if hashlib.sha256(data).hexdigest() != source["sha256"]:
        issues.append("Copilot OTel source SHA-256 does not match receipt")
    if data and not data.endswith(b"\n"):
        issues.append("Copilot OTel source is missing its terminal newline")
    spans: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(data.splitlines(), start=1):
        if not raw_line.strip():
            issues.append(f"Copilot OTel line {line_number} is empty")
            continue
        try:
            row = _strict_json(raw_line, label=f"Copilot OTel line {line_number}")
        except ValueError as exc:
            issues.append(str(exc))
            continue
        if not isinstance(row, dict) or row.get("type") not in {"span", "metric"}:
            issues.append(f"Copilot OTel line {line_number} is not a supported span or metric")
            continue
        if row.get("type") == "span":
            spans.append(row)
    if not spans:
        return [*issues, "Copilot OTel source contains no spans"]
    span_ids: set[str] = set()
    trace_ids: set[str] = set()
    roots: list[dict[str, Any]] = []
    chats: list[dict[str, Any]] = []
    for position, span in enumerate(spans):
        label = f"Copilot OTel span[{position}]"
        required = {
            "type",
            "traceId",
            "spanId",
            "name",
            "kind",
            "startTime",
            "endTime",
            "attributes",
            "status",
            "events",
            "resource",
            "instrumentationScope",
        }
        if not required.issubset(span):
            issues.append(f"{label} is missing required file-export fields")
            continue
        trace_id = span.get("traceId")
        span_id = span.get("spanId")
        if not isinstance(trace_id, str) or not re.fullmatch(r"[0-9a-f]{32}", trace_id):
            issues.append(f"{label}.traceId is invalid")
        else:
            trace_ids.add(trace_id)
        if not isinstance(span_id, str) or not re.fullmatch(r"[0-9a-f]{16}", span_id):
            issues.append(f"{label}.spanId is invalid")
        elif span_id in span_ids:
            issues.append("Copilot OTel span ids must be unique")
        else:
            span_ids.add(span_id)
        attributes = span.get("attributes")
        status = span.get("status")
        resource = span.get("resource")
        scope = span.get("instrumentationScope")
        if not isinstance(attributes, dict):
            issues.append(f"{label}.attributes must be an object")
            continue
        if any(
            key in {
                "gen_ai.input.messages",
                "gen_ai.output.messages",
                "gen_ai.system_instructions",
            }
            or key.endswith(".content")
            for key in attributes
        ):
            issues.append("Copilot OTel source contains captured message content")
        events = span.get("events")
        if not isinstance(events, list):
            issues.append(f"{label}.events must be an array")
        else:
            for event in events:
                event_attributes = (
                    event.get("attributes") if isinstance(event, dict) else None
                )
                if not isinstance(event_attributes, dict):
                    continue
                if any(
                    key in {
                        "gen_ai.input.messages",
                        "gen_ai.output.messages",
                        "gen_ai.system_instructions",
                    }
                    or key.endswith(".content")
                    for key in event_attributes
                ):
                    issues.append("Copilot OTel events contain captured message content")
        if not isinstance(status, dict) or status.get("code") not in {0, 1}:
            issues.append(f"{label} has an error or invalid status")
        resource_attributes = resource.get("attributes") if isinstance(resource, dict) else None
        if not isinstance(resource_attributes, dict) or (
            resource_attributes.get("service.name") != "github-copilot"
            or resource_attributes.get("service.version") != cli_version
        ):
            issues.append(f"{label} resource does not match the Copilot CLI receipt")
        if not isinstance(scope, dict) or (
            scope.get("name") != "github.copilot"
            or scope.get("version") != cli_version
        ):
            issues.append(f"{label} instrumentation scope does not match the Copilot CLI receipt")
        if attributes.get("gen_ai.conversation.id") != session_id:
            issues.append(f"{label} conversation id does not match the coordinator session")
        operation = attributes.get("gen_ai.operation.name")
        if operation == "invoke_agent" and span.get("parentSpanId") in {None, ""}:
            roots.append(span)
        elif operation == "chat":
            chats.append(span)
    if len(trace_ids) != 1:
        issues.append("Copilot OTel capture must contain exactly one trace")
    if len(roots) != 1:
        issues.append("Copilot OTel capture must contain exactly one root invoke_agent span")
    if not chats:
        issues.append("Copilot OTel capture must contain at least one chat span")
    parentless = [span for span in spans if span.get("parentSpanId") in {None, ""}]
    if len(parentless) != 1:
        issues.append("Copilot OTel capture must contain exactly one parentless span")
    spans_by_id = {
        span["spanId"]: span
        for span in spans
        if isinstance(span.get("spanId"), str)
        and re.fullmatch(r"[0-9a-f]{16}", span["spanId"])
    }
    root_id = roots[0].get("spanId") if len(roots) == 1 else None
    missing_parent_reported = False
    disconnected_reported = False
    cycle_reported = False
    if isinstance(root_id, str):
        for span_id in spans_by_id:
            if span_id == root_id:
                continue
            current_id = span_id
            visited: set[str] = set()
            while current_id != root_id:
                if current_id in visited:
                    if not cycle_reported:
                        issues.append("Copilot OTel span parent graph contains a cycle")
                        cycle_reported = True
                    break
                visited.add(current_id)
                current = spans_by_id[current_id]
                parent_id = current.get("parentSpanId")
                if not isinstance(parent_id, str) or not parent_id:
                    if not disconnected_reported:
                        issues.append(
                            "Copilot OTel every non-root span must be connected to the root"
                        )
                        disconnected_reported = True
                    break
                if parent_id not in spans_by_id:
                    if not missing_parent_reported:
                        issues.append(
                            "Copilot OTel span parent is missing from the complete trace"
                        )
                        missing_parent_reported = True
                    break
                current_id = parent_id
    chat_usage: list[dict[str, int | None]] = []
    observed_models: set[str] = set()
    observed_providers: set[str] = set()
    for position, span in enumerate(chats):
        attributes = span.get("attributes")
        assert isinstance(attributes, dict)
        model = str(attributes.get("gen_ai.response.model", "")).strip()
        provider = str(attributes.get("gen_ai.provider.name", "")).strip()
        if not model:
            issues.append(f"Copilot OTel chat span[{position}] lacks an observed response model")
        else:
            observed_models.add(model)
        if not provider:
            issues.append(f"Copilot OTel chat span[{position}] lacks an observed provider")
        else:
            observed_providers.add(provider)
        try:
            chat_usage.append(_otel_usage(attributes, label=f"Copilot OTel chat span[{position}]"))
        except ValueError as exc:
            issues.append(str(exc))
    aggregate = _aggregate_otel_usage(chat_usage) if len(chat_usage) == len(chats) else {}
    if roots:
        root_attributes = roots[0].get("attributes")
        if isinstance(root_attributes, dict):
            try:
                root_usage = _otel_usage(root_attributes, label="Copilot OTel root span")
            except ValueError as exc:
                issues.append(str(exc))
            else:
                for field in ("input_tokens", "output_tokens", "total_tokens"):
                    if aggregate.get(field) != root_usage.get(field):
                        issues.append(f"Copilot OTel root {field} does not reconcile with chat spans")
                for field in (
                    "cache_read_input_tokens",
                    "cache_write_input_tokens",
                    "reasoning_output_tokens",
                ):
                    if aggregate.get(field) is not None and root_usage.get(field) != aggregate.get(field):
                        issues.append(f"Copilot OTel root {field} does not reconcile with chat spans")
    if observed_models != {expected_model_label}:
        issues.append("Copilot OTel response models do not match the trusted benchmark model")
    if {normalize_model_provider(value) for value in observed_providers} != {"other"}:
        issues.append("Copilot OTel provider identity does not match token_measurement")
    if aggregate and _measurement_totals(measurement) != aggregate:
        issues.append("Copilot OTel usage does not match token_measurement")
    return sorted(set(issues))


def _verify_openai_response(
    measurement: dict[str, Any],
    receipt: object,
    *,
    expected_run_id: str | None,
    expected_model_label: str | None,
) -> list[str]:
    from . import openai_responses_adapter_v1

    issues = openai_responses_adapter_v1.validate_receipt(
        receipt,
        expected_run_id=expected_run_id,
    )
    if issues or not isinstance(receipt, dict):
        return issues
    observed = openai_responses_adapter_v1.receipt_usage(receipt)
    if _measurement_totals(measurement) != observed:
        issues.append("OpenAI Responses usage does not match token_measurement")
    observed_models = {
        str(call.get("response", {}).get("model", ""))
        for call in receipt.get("calls", [])
        if isinstance(call, dict) and isinstance(call.get("response"), dict)
    }
    if observed_models != {expected_model_label}:
        issues.append("OpenAI Responses models do not match the trusted benchmark model")
    return issues


def _codex_rollout_totals(data: bytes) -> tuple[dict[str, int | None], set[str], list[str]]:
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    totals: dict[str, int | None] = {field: 0 for field in fields}
    detail_available = {
        field: True
        for field in ("cached_input_tokens", "cache_write_input_tokens", "reasoning_output_tokens")
    }
    providers: set[str] = set()
    issues: list[str] = []
    usage_events = 0
    for line_number, raw_line in enumerate(data.splitlines(), start=1):
        try:
            row = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            issues.append(f"rollout line {line_number} is not valid JSON")
            continue
        if not isinstance(row, dict):
            issues.append(f"rollout line {line_number} must be an object")
            continue
        if row.get("type") == "turn_context" and isinstance(row.get("payload"), dict):
            payload = row["payload"]
            provider = str(payload.get("model_provider") or payload.get("provider") or "").strip()
            if provider:
                providers.add(normalize_model_provider(provider))
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        usage = info.get("last_token_usage")
        if usage is None:
            continue
        if not isinstance(usage, dict):
            issues.append(f"rollout usage event at line {line_number} must be an object")
            continue
        core = ("input_tokens", "output_tokens", "total_tokens")
        if not all(_token_count(usage.get(field)) for field in core):
            issues.append(f"rollout usage event at line {line_number} has invalid core counts")
            continue
        input_tokens = int(usage["input_tokens"])
        output_tokens = int(usage["output_tokens"])
        total_tokens = int(usage["total_tokens"])
        if total_tokens != input_tokens + output_tokens:
            issues.append(f"rollout usage event at line {line_number} has invalid total arithmetic")
            continue
        details: dict[str, int | None] = {}
        for field in detail_available:
            raw = usage.get(field)
            if raw is None:
                detail_available[field] = False
                totals[field] = None
                details[field] = None
            elif not _token_count(raw):
                issues.append(f"rollout usage event at line {line_number} has invalid {field}")
                details[field] = None
            else:
                details[field] = int(raw)
        if any(
            details[field] is not None and int(details[field]) > input_tokens
            for field in ("cached_input_tokens", "cache_write_input_tokens")
        ):
            issues.append(f"rollout usage event at line {line_number} has an input detail above input_tokens")
            continue
        cache_read = details["cached_input_tokens"] or 0
        cache_write = details["cache_write_input_tokens"] or 0
        if cache_read + cache_write > input_tokens:
            issues.append(f"rollout usage event at line {line_number} has overlapping cache accounting")
            continue
        if details["reasoning_output_tokens"] is not None and int(details["reasoning_output_tokens"]) > output_tokens:
            issues.append(f"rollout usage event at line {line_number} has reasoning above output_tokens")
            continue
        usage_events += 1
        for field in core:
            totals[field] = int(totals[field] or 0) + int(usage[field])
        for field in detail_available:
            if detail_available[field] and details[field] is not None:
                totals[field] = int(totals[field] or 0) + int(details[field])
    if usage_events == 0:
        issues.append("rollout contains no valid usage events")
    if data and not data.endswith(b"\n"):
        issues.append("rollout is missing its terminal newline")
    return totals, providers, issues


def verify_measurement_binding(
    measurement: object,
    *,
    evidence_root: Path | None = None,
    trusted_host_capture_root: Path | None = None,
    expected_run_id: str | None = None,
    expected_model_label: str | None = None,
) -> list[str]:
    """Open adapter evidence and bind its bytes, identity, and counts to a measurement."""

    if not isinstance(measurement, dict):
        return ["token_measurement must be an object for evidence verification"]
    evidence = measurement.get("evidence")
    if not isinstance(evidence, dict):
        return ["token_measurement.evidence must be an object for evidence verification"]
    adapter_id = str(evidence.get("adapter_id", ""))
    if adapter_id not in {
        "codex-rollout-v1",
        "claude-code-result-v1",
        "github-copilot-otel-v1",
        "openai-responses-usage-v1",
    }:
        return [f"full-run evidence adapter has no implemented byte verifier: {adapter_id or 'missing'}"]
    raw_path = str(evidence.get("source_path", "")).strip()
    if not raw_path:
        return ["full-run evidence source_path must be non-empty"]
    if adapter_id != "codex-rollout-v1":
        trusted_entry, index_issues = _trusted_capture_entry(
            trusted_root=trusted_host_capture_root,
            expected_run_id=expected_run_id,
            expected_model_label=expected_model_label,
            receipt_path=raw_path,
            receipt_sha256=str(evidence.get("source_sha256", "")),
        )
        if index_issues:
            return index_issues
        try:
            _receipt_path, data = _read_trusted_capture(
                raw_path,
                trusted_root=trusted_host_capture_root,
                label="host capture receipt",
            )
        except OSError as exc:
            return [f"host capture receipt is unavailable or unsafe: {exc}"]
        issues: list[str] = []
        if evidence.get("source_sha256") != hashlib.sha256(data).hexdigest():
            issues.append("host capture receipt source_sha256 does not match receipt bytes")
        try:
            receipt = _strict_json(data, label="host capture receipt")
        except ValueError as exc:
            return [*issues, str(exc)]
        if (
            not isinstance(receipt, dict)
            or not isinstance(trusted_entry, dict)
            or receipt.get("capture_nonce") != trusted_entry.get("capture_nonce")
        ):
            issues.append("host capture receipt nonce does not match the trusted index")
        if adapter_id == "claude-code-result-v1":
            issues.extend(
                _verify_claude_result(
                    measurement,
                    receipt,
                    trusted_root=trusted_host_capture_root,
                    expected_run_id=expected_run_id,
                    expected_model_label=expected_model_label,
                )
            )
        elif adapter_id == "github-copilot-otel-v1":
            issues.extend(
                _verify_copilot_otel(
                    measurement,
                    receipt,
                    trusted_root=trusted_host_capture_root,
                    expected_run_id=expected_run_id,
                    expected_model_label=expected_model_label,
                )
            )
        else:
            issues.extend(
                _verify_openai_response(
                    measurement,
                    receipt,
                    expected_run_id=expected_run_id,
                    expected_model_label=expected_model_label,
                )
            )
        return issues
    try:
        source, source_identity = _lexical_no_alias_path(
            raw_path,
            evidence_root=evidence_root,
            label="full-run evidence source",
        )
        data = _read_no_follow(source, expected_identity=source_identity)
    except (OSError, RuntimeError) as exc:
        return [f"full-run evidence source is unavailable: {exc}"]
    issues: list[str] = []
    observed_sha = hashlib.sha256(data).hexdigest()
    if evidence.get("source_sha256") != observed_sha:
        issues.append("full-run evidence source_sha256 does not match source bytes")
    totals, providers, rollout_issues = _codex_rollout_totals(data)
    issues.extend(rollout_issues)
    measurement_details = measurement.get("details")
    details = measurement_details if isinstance(measurement_details, dict) else {}

    def detail_value(field: str) -> object:
        row = details.get(field)
        return row.get("value") if isinstance(row, dict) else None

    expected = {
        "input_tokens": measurement.get("input_tokens"),
        "cached_input_tokens": detail_value("cache_read_input_tokens"),
        "cache_write_input_tokens": detail_value("cache_write_input_tokens"),
        "output_tokens": measurement.get("output_tokens"),
        "reasoning_output_tokens": detail_value("reasoning_output_tokens"),
        "total_tokens": measurement.get("total_tokens"),
    }
    if totals != expected:
        issues.append("full-run evidence usage totals do not match token_measurement")
    provider = str(measurement.get("model_provider", ""))
    if providers != {provider}:
        issues.append("full-run evidence model provider does not match token_measurement")
    return issues


def _rollout_model_observation(data: bytes) -> tuple[dict[str, str], list[str]]:
    observed: set[tuple[str, str, str]] = set()
    issues: list[str] = []
    for line_number, raw_line in enumerate(data.splitlines(), start=1):
        try:
            row = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(row, dict) or row.get("type") != "turn_context":
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        provider = str(payload.get("model_provider") or payload.get("provider") or "").strip()
        model = str(payload.get("model") or "").strip()
        reasoning = str(payload.get("reasoning_effort") or "").strip()
        if provider and model:
            observed.add((provider, model, reasoning))
        else:
            issues.append(f"rollout turn_context at line {line_number} lacks provider or model")
    if len(observed) != 1:
        issues.append("rollout must contain one consistent provider/model observation")
        return {}, issues
    provider, model, reasoning = next(iter(observed))
    return {
        "provider": provider,
        "model": model,
        "reasoning_effort": reasoning,
    }, issues


def verify_codex_ledger_receipt(
    measurement: object,
    receipt: object,
    *,
    evidence_root: Path | None = None,
    trusted_codex_home: Path | None = None,
) -> list[str]:
    """Verify a claim against report-local evidence and an out-of-band Codex root."""

    if not isinstance(measurement, dict):
        return ["token measurement must be an object for Codex ledger verification"]
    if not isinstance(receipt, dict):
        return ["generic full-run token claims require a Codex usage-ledger receipt"]
    issues: list[str] = []
    issues.extend(
        f"token_measurement_receipt.{field} is required"
        for field in sorted(LEDGER_RECEIPT_FIELDS - set(receipt))
    )
    issues.extend(
        f"token_measurement_receipt.{field} is not allowed"
        for field in sorted(set(receipt) - LEDGER_RECEIPT_FIELDS)
    )
    if type(receipt.get("schema_version")) is not int or receipt.get("schema_version") != 1:
        issues.append("token_measurement_receipt.schema_version must be 1")
    if receipt.get("adapter_id") != "codex-usage-ledger-v1":
        issues.append("token_measurement_receipt.adapter_id must be codex-usage-ledger-v1")
    for field in ("source_path", "ledger_label", "thread_id"):
        if not isinstance(receipt.get(field), str) or not str(receipt.get(field)).strip():
            issues.append(f"token_measurement_receipt.{field} must be a non-empty string")
    if trusted_codex_home is None:
        issues.append("generic full-run token claims require an out-of-band trusted Codex home")
    if not SHA256_RE.fullmatch(str(receipt.get("source_sha256", ""))):
        issues.append("token_measurement_receipt.source_sha256 must be a lowercase SHA-256")
    if issues:
        return issues
    try:
        ledger_path, ledger_identity = _lexical_no_alias_path(
            str(receipt["source_path"]),
            evidence_root=evidence_root,
            label="Codex usage ledger",
        )
        ledger_bytes = _read_no_follow(ledger_path, expected_identity=ledger_identity)
    except OSError as exc:
        return [f"Codex usage ledger is unavailable or unsafe: {exc}"]
    if hashlib.sha256(ledger_bytes).hexdigest() != receipt["source_sha256"]:
        issues.append("Codex usage ledger SHA-256 does not match receipt")
    try:
        ledger = json.loads(ledger_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [*issues, f"Codex usage ledger is invalid JSON: {exc}"]
    if not isinstance(ledger, dict):
        return [*issues, "Codex usage ledger must be an object"]
    scope = ledger.get("measurement_scope")
    arms = ledger.get("arms")
    row = arms.get(receipt["ledger_label"]) if isinstance(arms, dict) else None
    if (
        ledger.get("tool") != "agent-benchmarking.codex-usage-ledger"
        or ledger.get("ok") is not True
        or not isinstance(scope, dict)
        or scope.get("complete_for_full_run_trials") is not True
        or not isinstance(row, dict)
    ):
        return [*issues, "Codex usage ledger row is missing or incomplete"]
    thread_id = str(receipt["thread_id"])
    if row.get("thread_id") != thread_id:
        issues.append("Codex usage ledger thread id does not match receipt")
    if row.get("source") != "state-sqlite":
        issues.append("Codex usage ledger row must be rooted in state-sqlite")
    if row.get("token_measurement") != measurement:
        issues.append("Codex usage ledger token measurement does not match benchmark report")
    assert trusted_codex_home is not None
    codex_home = Path(os.path.abspath(trusted_codex_home))
    try:
        state_db, state_db_identity = _lexical_no_alias_path(
            codex_home / "state_5.sqlite",
            label="Codex state database",
        )
    except OSError as exc:
        return [*issues, f"Codex state database is unavailable or unsafe: {exc}"]
    try:
        current_state_db = os.lstat(state_db)
        if (current_state_db.st_dev, current_state_db.st_ino) != state_db_identity:
            return [*issues, "Codex state database identity changed before opening"]
        connection = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
    except OSError as exc:
        return [*issues, f"Codex state database identity check failed: {exc}"]
    except sqlite3.Error as exc:
        return [*issues, f"Codex state database open failed: {exc}"]
    try:
        state_row = connection.execute(
            "select model_provider, cwd, rollout_path, tokens_used from threads where id=?",
            (thread_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        return [*issues, f"Codex state database query failed: {exc}"]
    finally:
        connection.close()
    try:
        current_state_db = os.lstat(state_db)
    except OSError as exc:
        return [*issues, f"Codex state database identity check failed: {exc}"]
    if (current_state_db.st_dev, current_state_db.st_ino) != state_db_identity:
        return [*issues, "Codex state database identity changed during verification"]
    if state_row is None:
        return [*issues, "Codex state database does not contain the receipt thread"]
    state_provider, state_cwd, state_rollout_raw, state_tokens = state_row
    for field, state_value in (
        ("model_provider", state_provider),
        ("cwd", state_cwd),
        ("rollout_path", state_rollout_raw),
        ("state_tokens_used", state_tokens),
    ):
        if row.get(field) != state_value:
            issues.append(f"Codex usage ledger {field} does not match live state")
    try:
        sessions_root, sessions_identity = _lexical_no_alias_path(
            codex_home / "sessions",
            label="Codex sessions root",
        )
        rollout_path, rollout_identity = _lexical_no_alias_path(
            str(state_rollout_raw),
            label="Codex rollout",
        )
        rollout_path.relative_to(sessions_root)
        rollout_bytes = _read_no_follow(rollout_path, expected_identity=rollout_identity)
        current_sessions = os.lstat(sessions_root)
        if (current_sessions.st_dev, current_sessions.st_ino) != sessions_identity:
            raise OSError("Codex sessions root identity changed during verification")
    except (OSError, ValueError) as exc:
        return [*issues, f"Codex rollout is unavailable, aliased, or outside sessions: {exc}"]
    rollout_sha = hashlib.sha256(rollout_bytes).hexdigest()
    if row.get("rollout_sha256") != rollout_sha:
        issues.append("Codex usage ledger rollout SHA-256 does not match live rollout")
    evidence = measurement.get("evidence")
    if not isinstance(evidence, dict):
        issues.append("token measurement evidence is missing")
    else:
        try:
            measurement_source, measurement_source_identity = _lexical_no_alias_path(
                str(evidence.get("source_path", "")),
                evidence_root=evidence_root,
                label="token measurement rollout",
            )
        except OSError as exc:
            issues.append(f"token measurement rollout is unavailable or unsafe: {exc}")
        else:
            if measurement_source != rollout_path or measurement_source_identity != rollout_identity:
                issues.append("token measurement rollout does not match Codex state")
        if evidence.get("source_sha256") != rollout_sha:
            issues.append("token measurement rollout SHA-256 does not match Codex state")
    issues.extend(verify_measurement_binding(measurement, evidence_root=evidence_root))
    observation, observation_issues = _rollout_model_observation(rollout_bytes)
    issues.extend(observation_issues)
    ledger_observation = row.get("model_observation")
    if not isinstance(ledger_observation, dict):
        issues.append("Codex usage ledger model observation is missing")
    else:
        for field in ("provider", "model", "reasoning_effort"):
            if ledger_observation.get(field) != observation.get(field):
                issues.append(f"Codex usage ledger {field} does not match rollout")
        if ledger_observation.get("complete") is not True:
            issues.append("Codex usage ledger model observation is incomplete")
    prompt = row.get("execution_prompt")
    if not isinstance(prompt, dict) or not all(
        prompt.get(field) is True
        for field in ("observed", "first_structured_user_message_matches", "fresh_thread_scope")
    ):
        issues.append("Codex usage ledger execution prompt evidence is incomplete")
    if evidence_root is None:
        issues.append("Codex usage ledger verification requires the benchmark evidence root")
    else:
        try:
            prompt_path, prompt_identity = _lexical_no_alias_path(
                evidence_root / "PROMPT.md",
                evidence_root=evidence_root,
                label="benchmark execution prompt",
            )
            prompt_bytes = _read_no_follow(prompt_path, expected_identity=prompt_identity)
        except OSError as exc:
            issues.append(f"benchmark execution prompt is unavailable or unsafe: {exc}")
        else:
            try:
                prompt_text = prompt_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                issues.append(f"benchmark execution prompt is not UTF-8: {exc}")
            else:
                expected_prompt_sha = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
                if not isinstance(prompt, dict) or prompt.get("prompt_sha256") != expected_prompt_sha:
                    issues.append("Codex ledger prompt does not match benchmark PROMPT.md")
    if normalize_model_provider(state_provider) != str(measurement.get("model_provider", "")):
        issues.append("Codex state provider does not match token measurement")
    if state_tokens != measurement.get("total_tokens"):
        issues.append("Codex state tokens_used does not match token measurement")
    return issues


def adapter_declarations() -> dict[str, dict[str, Any]]:
    """Expose a copy for reports/tests without permitting mutation of the registry."""

    return {adapter_id: dict(declaration) for adapter_id, declaration in ADAPTERS.items()}
