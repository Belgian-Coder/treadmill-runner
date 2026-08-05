"""Path and low-level helpers for workflow context packets."""

from __future__ import annotations

from pathlib import Path

import workflow_manager_common as common

CONTEXT_PACKET_DIR = "artifacts/context"
CONTEXT_PACKET_JSON = "context-packet.json"
CONTEXT_PACKET_MARKDOWN = "context-packet.md"
DOCUMENTATION_DELTA_DIR = "artifacts/documentation"
DOCUMENTATION_DELTA_JSON = "documentation-delta.json"
DOCUMENTATION_DELTA_MARKDOWN = "documentation-delta.md"
TERMINAL_PHASES = {"complete", "completed", "done", "closed", "finish", "finished"}


def unique_list(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def approx_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def context_packet_paths(run_dir: Path) -> tuple[Path, Path]:
    context_dir = run_dir / CONTEXT_PACKET_DIR
    return context_dir / CONTEXT_PACKET_JSON, context_dir / CONTEXT_PACKET_MARKDOWN


def documentation_delta_paths(run_dir: Path) -> tuple[Path, Path]:
    documentation_dir = run_dir / DOCUMENTATION_DELTA_DIR
    return documentation_dir / DOCUMENTATION_DELTA_JSON, documentation_dir / DOCUMENTATION_DELTA_MARKDOWN


def documentation_delta_relative_paths(root: Path, run_dir: Path) -> tuple[str, str]:
    json_path, markdown_path = documentation_delta_paths(run_dir)
    return common.relative(root, json_path), common.relative(root, markdown_path)


def context_packet_relative_paths(root: Path, run_dir: Path) -> tuple[str, str]:
    json_path, markdown_path = context_packet_paths(run_dir)
    return common.relative(root, json_path), common.relative(root, markdown_path)


def read_optional_text(path: Path, *, limit: int = 80_000) -> str:
    return common.read_text(path, limit=limit) if path.exists() else ""


def normalize_path_handle(root: Path, run_dir: Path, value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    if path.exists():
        return common.relative(root, path)
    if raw.startswith("runs/"):
        return common.relative(root, run_dir.parent.parent / raw)
    if raw in {"run.json", "REPORT.md"} or raw.startswith(("validation/", "artifacts/")):
        return common.relative(root, run_dir / raw)
    return raw.replace("\\", "/")
