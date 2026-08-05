"""Exact identifier extraction for workflow context packets."""

from __future__ import annotations

import json
import re
from pathlib import Path

import workflow_manager_common as common
from workflow_support.context_paths import read_optional_text, unique_list

HASH_CONTEXT_RE = re.compile(
    r"\b(?:commit|sha|hash)\s+([A-Fa-f0-9]{32,64})(?![A-Fa-f0-9])"
    r"|(?<![A-Fa-f0-9])([A-Fa-f0-9]{32,64})\s+(?:commit|sha|hash)\b",
    re.IGNORECASE,
)
TICKET_ID_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,12}-\d{1,8}\b")
PATH_RE = re.compile(r"(?<![\w./\\-])(?:[A-Za-z0-9_.-]+[\\/])+(?:[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,12})(?![\w./\\-])")
ROOT_MARKDOWN_RE = re.compile(r"(?<![\w./\\-])(?:AGENTS|README|GEMINI|CLAUDE)\.md(?![\w./\\-])")
PORT_RE = re.compile(r"\bport\s+([1-9][0-9]{1,4})\b", re.IGNORECASE)
ENV_RE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b(?!\.[A-Za-z0-9]{1,12}\b)")
MODEL_ID_PREFIXES = {"GPT", "CLAUDE", "GEMINI", "LLAMA", "MISTRAL", "QWEN"}
MAX_SOURCE_CHARS = 120_000
MAX_VALUES = 20
MAX_SOURCES = 16
STREAM_CHUNK_CHARS = 64_000
STREAM_OVERLAP_CHARS = 512


def _walk_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        strings: list[str] = []
        for item in value:
            strings.extend(_walk_strings(item))
        return strings
    if isinstance(value, dict):
        strings = []
        for key, item in value.items():
            if isinstance(key, str):
                strings.append(key)
            strings.extend(_walk_strings(item))
        return strings
    return []


def _cap(values: list[str], limit: int = MAX_VALUES) -> list[str]:
    return unique_list(values)[:limit]


def _normalize_path(value: str) -> str:
    normalized = value.strip("`'\"<>()[]{}.,;").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _extract_paths(text: str) -> list[str]:
    paths = [_normalize_path(match.group(0)) for match in PATH_RE.finditer(text)]
    paths.extend(_normalize_path(match.group(0)) for match in ROOT_MARKDOWN_RE.finditer(text))
    return [path for path in paths if path and ".." not in Path(path).parts]


def _extract_ports(text: str) -> list[str]:
    values: list[str] = []
    for match in PORT_RE.finditer(text):
        value = int(match.group(1))
        if 1 <= value <= 65535:
            values.append(str(value))
    return values


def _extract_hashes(text: str) -> list[str]:
    values: list[str] = []
    for match in HASH_CONTEXT_RE.finditer(text):
        value = match.group(1) or match.group(2)
        if value:
            values.append(value)
    return values


def _extract_env_names(text: str) -> list[str]:
    return [match.group(0) for match in ENV_RE.finditer(text) if "_" in match.group(0)]


def _extract_ticket_ids(text: str) -> list[str]:
    values: list[str] = []
    for match in TICKET_ID_RE.finditer(text):
        value = match.group(0)
        prefix = value.split("-", 1)[0].upper()
        if prefix in MODEL_ID_PREFIXES:
            continue
        values.append(value)
    return values


def _source_texts(
    root: Path,
    run_dir: Path,
    run_packet: dict[str, object],
    required_next_context: list[str],
    evidence_handles: list[str],
    scope: dict[str, object],
    preserve_source_paths: list[str],
) -> list[dict[str, object]]:
    sources = [
        {
            "kind": "run-packet",
            "path": common.relative(root, run_dir / "run.json"),
            "text": json.dumps(run_packet, ensure_ascii=False, sort_keys=True),
        },
        {
            "kind": "packet-fields",
            "path": "context-packet",
            "text": json.dumps(
                {
                    "required_next_context": required_next_context,
                    "evidence_handles": evidence_handles,
                    "scope": scope,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
    ]
    preserved: set[str] = set()
    for rel in unique_list(preserve_source_paths):
        candidate = root / rel
        try:
            resolved = candidate.resolve()
            root_resolved = root.resolve()
        except OSError:
            continue
        if resolved != root_resolved and root_resolved not in resolved.parents:
            continue
        if not candidate.is_file():
            continue
        preserved.add(rel)
        sources.append(
            {
                "kind": "coordinate-preservation",
                "path": rel,
                "stream_path": candidate,
            }
        )
    for rel in unique_list(required_next_context)[:MAX_SOURCES]:
        if rel in preserved:
            continue
        candidate = root / rel
        if candidate == run_dir / "artifacts" / "context" / "context-packet.json":
            continue
        try:
            resolved = candidate.resolve()
            root_resolved = root.resolve()
        except OSError:
            continue
        if resolved != root_resolved and root_resolved not in resolved.parents:
            continue
        if not candidate.is_file() or candidate.stat().st_size > MAX_SOURCE_CHARS:
            continue
        text = read_optional_text(candidate)
        if text:
            sources.append({"kind": "file", "path": rel, "text": text})
    return sources


def _stream_coordinate_text(path: Path):
    """Yield bounded text windows while preserving matches across chunk edges."""

    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            carry = ""
            while True:
                chunk = stream.read(STREAM_CHUNK_CHARS)
                if not chunk:
                    break
                text = carry + chunk
                yield text
                carry = text[-STREAM_OVERLAP_CHARS:]
    except OSError:
        return


def build_coordinate_closet(
    root: Path,
    run_dir: Path,
    run_packet: dict[str, object],
    *,
    required_next_context: list[str],
    evidence_handles: list[str],
    scope: dict[str, object],
    preserve_source_paths: list[str] | None = None,
) -> dict[str, object]:
    """Build a compact exact-string inventory for resumable workflow coordinates."""
    paths: list[str] = []
    hashes: list[str] = []
    ids: list[str] = []
    ports: list[str] = []
    env: list[str] = []
    source_count = 0
    for source in _source_texts(
        root,
        run_dir,
        run_packet,
        required_next_context,
        evidence_handles,
        scope,
        preserve_source_paths or [],
    ):
        stream_path = source.get("stream_path")
        texts = (
            _stream_coordinate_text(stream_path)
            if isinstance(stream_path, Path)
            else (str(source.get("text", "")),)
        )
        counted = False
        for text in texts:
            if not counted:
                source_count += 1
                counted = True
            paths.extend(_extract_paths(text))
            hashes.extend(_extract_hashes(text))
            ids.extend(_extract_ticket_ids(text))
            ports.extend(_extract_ports(text))
            env.extend(_extract_env_names(text))

    capped_hashes = _cap(hashes)
    capped_ids = _cap(ids)
    capped_ports = _cap(ports)
    capped_env = _cap(env)
    preserve_paths = bool(capped_hashes or capped_ids or capped_ports or capped_env)
    closet = {
        "status": "present",
        "paths": _cap(paths) if preserve_paths else [],
        "hashes": capped_hashes,
        "ids": capped_ids,
        "ports": capped_ports,
        "env": capped_env,
        "source_count": source_count,
    }
    if not any(closet[key] for key in ("paths", "hashes", "ids", "ports", "env")):
        closet["status"] = "empty"
    return closet
