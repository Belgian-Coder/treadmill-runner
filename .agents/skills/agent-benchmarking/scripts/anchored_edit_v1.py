#!/usr/bin/env python3
"""Read or apply digest-guarded anchored edits inside an explicit benchmark workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
REQUEST_TOOL = "agent-benchmarking.anchored-edit-request"
RESULT_TOOL = "agent-benchmarking.anchored-edit-result"
VIEW_TOOL = "agent-benchmarking.anchored-edit-view"
WORKSPACE_TOOL = "agent-benchmarking.anchored-edit-workspace"
WORKSPACE_MARKER = ".anchored-edit-benchmark-v1.json"
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(int(getattr(metadata, "st_file_attributes", 0)) & REPARSE_POINT)


def _assert_no_link_chain(path: Path, label: str) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path.cwd()
    parts = path.parts[1:] if path.is_absolute() else path.parts
    for part in parts:
        current /= part
        if not current.exists():
            continue
        metadata = os.lstat(current)
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
            raise SystemExit(f"{label} must not traverse a link or reparse point: {current}")


def _safe_relative(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"{label} must be a non-empty relative path")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise SystemExit(f"{label} must be valid Unicode encodable as UTF-8") from None
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SystemExit(f"{label} must be a normalized relative path without dot segments")
    return path


def _read_no_follow(path: Path, label: str, max_bytes: int) -> tuple[bytes, os.stat_result]:
    _assert_no_link_chain(path, label)
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        raise SystemExit(f"{label} not found: {path}") from None
    if stat.S_ISLNK(before.st_mode) or _is_reparse(before) or not stat.S_ISREG(before.st_mode):
        raise SystemExit(f"{label} must be a no-follow regular file: {path}")
    if before.st_size > max_bytes:
        raise SystemExit(f"{label} exceeds the {max_bytes}-byte limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise SystemExit(f"{label} changed or resolved through an alias")
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise SystemExit(f"{label} exceeds the {max_bytes}-byte limit")
    return data, opened


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def _load_json(path: Path, label: str, max_bytes: int) -> dict[str, Any]:
    data, _metadata = _read_no_follow(path, label, max_bytes)
    try:
        value = json.loads(
            data.decode("utf-8-sig"),
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise SystemExit(f"{label} is invalid UTF-8 JSON: {exc}") from None
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must contain a JSON object")
    return value


def _workspace_root(value: str) -> Path:
    root = Path(os.path.abspath(Path(value).expanduser()))
    _assert_no_link_chain(root, "workspace root")
    if not root.is_dir():
        raise SystemExit("workspace root must be an existing no-follow directory")
    marker = _load_json(root / WORKSPACE_MARKER, "anchored-edit workspace marker", 4096)
    if marker != {"schema_version": 1, "tool": WORKSPACE_TOOL}:
        raise SystemExit("anchored-edit workspace marker must match the exact V1 contract")
    return root


def _target(root: Path, value: object) -> tuple[Path, str]:
    relative = _safe_relative(value, "path")
    target = Path(os.path.abspath(root / relative))
    if target == root or not target.is_relative_to(root):
        raise SystemExit("path must remain strictly inside the benchmark workspace")
    _assert_no_link_chain(target, "target path")
    return target, relative.as_posix()


def _decode(data: bytes) -> tuple[str, str, str, bool, list[str]]:
    encoding = "utf-8-bom" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
    body = data[3:] if encoding == "utf-8-bom" else data
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise SystemExit("target must be valid UTF-8 or UTF-8 with BOM") from None
    separators = re.findall(r"\r\n|\r|\n", text)
    kinds = set(separators)
    if len(kinds) > 1:
        raise SystemExit("target uses mixed newline styles")
    newline = separators[0] if separators else "\n"
    final_newline = bool(separators) and text.endswith(newline)
    lines = text.split(newline)
    if final_newline:
        lines.pop()
    return text, encoding, newline, final_newline, lines


def _anchor(line_number: int, text: str) -> str:
    source = f"{line_number}\0{text}".encode("utf-8")
    return hashlib.sha256(source).hexdigest()[:16]


def read_view(root: Path, relative_path: object) -> dict[str, Any]:
    target, portable = _target(root, relative_path)
    data, metadata = _read_no_follow(target, "target", MAX_FILE_BYTES)
    if int(getattr(metadata, "st_nlink", 1)) != 1:
        raise SystemExit("target must not have multiple hard links")
    _text, encoding, newline, final_newline, lines = _decode(data)
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": VIEW_TOOL,
        "path": portable,
        "file_sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "encoding": encoding,
        "newline": {"\n": "lf", "\r\n": "crlf", "\r": "cr"}[newline],
        "final_newline": final_newline,
        "anchor_policy": "first-16-hex-of-sha256(line-number + NUL + UTF-8-line); navigation-only",
        "lines": [
            {"line": index, "anchor": _anchor(index, value), "text": value}
            for index, value in enumerate(lines, start=1)
        ],
    }


def _replacement(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SystemExit(f"{label} must be an array of strings")
    if any("\n" in item or "\r" in item for item in value):
        raise SystemExit(f"{label} entries must not contain newline characters")
    try:
        for item in value:
            item.encode("utf-8")
    except UnicodeEncodeError:
        raise SystemExit(f"{label} entries must be valid Unicode encodable as UTF-8") from None
    return list(value)


def _point(value: object, label: str, lines: list[str]) -> int:
    if not isinstance(value, dict) or set(value) != {"line", "anchor"}:
        raise SystemExit(f"{label} must contain exactly line and anchor")
    number = value.get("line")
    if type(number) is not int or not 1 <= number <= len(lines):
        raise SystemExit(f"{label}.line is outside the original file")
    if value.get("anchor") != _anchor(number, lines[number - 1]):
        raise SystemExit(f"{label}.anchor does not match the original line")
    return number


def _operations(value: object, lines: list[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise SystemExit("operations must be a non-empty array")
    normalized: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []
    for index, operation in enumerate(value):
        if not isinstance(operation, dict):
            raise SystemExit(f"operations[{index}] must be an object")
        op = operation.get("op")
        if op == "replace":
            if set(operation) != {"op", "start", "end", "replacement"}:
                raise SystemExit(f"operations[{index}] has an invalid replace shape")
            start = _point(operation["start"], f"operations[{index}].start", lines)
            end = _point(operation["end"], f"operations[{index}].end", lines)
            if end < start:
                raise SystemExit(f"operations[{index}] replace range is reversed")
            replacement = _replacement(operation["replacement"], f"operations[{index}].replacement")
        elif op == "insert-after":
            if set(operation) != {"op", "after", "replacement"}:
                raise SystemExit(f"operations[{index}] has an invalid insert-after shape")
            start = end = _point(operation["after"], f"operations[{index}].after", lines)
            replacement = _replacement(operation["replacement"], f"operations[{index}].replacement")
            if not replacement:
                raise SystemExit(f"operations[{index}] insert-after replacement must not be empty")
        else:
            raise SystemExit(f"operations[{index}].op must be replace or insert-after")
        if any(not (end < left or start > right) for left, right in occupied):
            raise SystemExit("operations must not overlap or share an anchor line")
        occupied.append((start, end))
        normalized.append(
            {"op": op, "start": start, "end": end, "replacement": replacement}
        )
    return normalized


def apply_request(root: Path, request: dict[str, Any]) -> dict[str, Any]:
    if set(request) != {
        "schema_version",
        "tool",
        "path",
        "expected_file_sha256",
        "operations",
    }:
        raise SystemExit("anchored-edit request fields must match the exact V1 contract")
    if type(request.get("schema_version")) is not int or request.get("schema_version") != 1:
        raise SystemExit("anchored-edit request schema_version must be the integer 1")
    if request.get("tool") != REQUEST_TOOL:
        raise SystemExit(f"anchored-edit request tool must be {REQUEST_TOOL}")
    expected = request.get("expected_file_sha256")
    if not isinstance(expected, str) or SHA256_PATTERN.fullmatch(expected) is None:
        raise SystemExit("expected_file_sha256 must be a lowercase SHA-256")
    target, portable = _target(root, request.get("path"))
    original, metadata = _read_no_follow(target, "target", MAX_FILE_BYTES)
    if int(getattr(metadata, "st_nlink", 1)) != 1:
        raise SystemExit("target must not have multiple hard links")
    original_sha256 = hashlib.sha256(original).hexdigest()
    if original_sha256 != expected:
        raise SystemExit("target SHA-256 does not match expected_file_sha256")
    _text, encoding, newline, final_newline, lines = _decode(original)
    operations = _operations(request.get("operations"), lines)
    updated = list(lines)
    for operation in sorted(operations, key=lambda item: item["start"], reverse=True):
        start = int(operation["start"])
        end = int(operation["end"])
        if operation["op"] == "replace":
            updated[start - 1 : end] = operation["replacement"]
        else:
            updated[start:start] = operation["replacement"]
    rendered = newline.join(updated)
    if final_newline:
        rendered += newline
    body = rendered.encode("utf-8")
    result_bytes = (b"\xef\xbb\xbf" + body) if encoding == "utf-8-bom" else body
    if len(result_bytes) > MAX_FILE_BYTES:
        raise SystemExit("edited target exceeds the file-size limit")
    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": RESULT_TOOL,
        "path": portable,
        "write_supported": False,
        "written": False,
        "changed": result_bytes != original,
        "original_sha256": original_sha256,
        "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "result_bytes": len(result_bytes),
        "operation_count": len(operations),
        "newline_preserved": True,
        "encoding_preserved": True,
        "trust_boundary": "dry-run only; requires an isolated quiescent benchmark workspace without concurrent path mutation",
    }
    return result


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    subparsers = cli.add_subparsers(dest="command", required=True)
    read_parser = subparsers.add_parser("read", help="emit anchored lines for a benchmark file")
    read_parser.add_argument("--workspace-root", required=True)
    read_parser.add_argument("--path", required=True)
    apply_parser = subparsers.add_parser(
        "apply",
        help="validate and simulate an anchored-edit request without writing",
    )
    apply_parser.add_argument("--workspace-root", required=True)
    apply_parser.add_argument("--request", required=True)
    return cli


def main() -> int:
    args = parser().parse_args()
    root = _workspace_root(args.workspace_root)
    if args.command == "read":
        result = read_view(root, args.path)
    else:
        request = _load_json(Path(args.request), "anchored-edit request", MAX_REQUEST_BYTES)
        result = apply_request(root, request)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
