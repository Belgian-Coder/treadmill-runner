#!/usr/bin/env python3
"""Shared helpers for local agent benchmark scripts."""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from benchmark_determinism import (
    ConsecutiveFailureTracker,
    classify_mismatch,
    classify_process_failure,
    deterministic_metadata,
    failure_fingerprint,
    normalize_determinism,
    normalize_evidence_tier,
    normalize_evidence_tiers,
    run_command_with_limits,
)
from support.benchmark_common_contracts import (
    FAILURE_TAXONOMY_CATEGORIES,
    QUALITY_RUBRICS,
    RUN_CONFIG_COMPARE_KEYS,
    RUN_ID_PATTERN,
    SCHEMA_VERSION,
    STANDARD_AGENT_BOOL_METRICS,
    STANDARD_AGENT_NUMERIC_METRICS,
    STANDARD_BOOL_METRICS,
    STANDARD_NUMERIC_METRICS,
    TOKEN_ENCODING_NAME,
    TOKEN_ESTIMATION_METHOD,
    TOOL_NAME,
    TRAJECTORY_SIGNAL_COUNT_KEYS,
)


def require_supported_python() -> None:
    if sys.version_info >= (3, 12):
        return
    current = ".".join(str(part) for part in sys.version_info[:3])
    raise SystemExit(f"Python 3.12+ is required; current interpreter is Python {current}.")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise SystemExit(f"JSON file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path.name} is invalid JSON: {exc.msg} at line {exc.lineno}.") from None


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def read_text(path: Path, limit: int = 200_000) -> str:
    try:
        data = path.read_bytes()[:limit]
    except OSError:
        return ""
    return data.decode("utf-8-sig", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def token_count_metadata() -> dict[str, Any]:
    try:
        import tiktoken  # type: ignore[import-not-found]

        tiktoken.get_encoding(TOKEN_ENCODING_NAME)
    except Exception as exc:
        return {
            "available": False,
            "exact": False,
            "method": "estimated_chars_div_4",
            "encoding": "",
            "package": "tiktoken",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "available": True,
        "exact": True,
        "method": "tiktoken",
        "encoding": TOKEN_ENCODING_NAME,
        "package": "tiktoken",
        "version": str(getattr(tiktoken, "__version__", "unknown")),
    }


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    try:
        import tiktoken  # type: ignore[import-not-found]

        encoding = tiktoken.get_encoding(TOKEN_ENCODING_NAME)
        return len(encoding.encode(text))
    except Exception:
        pass
    return max(1, math.ceil(len(text) / 4))


def suite_name(data: dict[str, Any], path: Path) -> str:
    value = data.get("suite") or data.get("name") or path.stem
    return str(value).strip() or path.stem


def task_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = data.get("tasks") or data.get("cases") or []
    if not isinstance(tasks, list):
        raise SystemExit("benchmark suite must contain a tasks or cases list.")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(tasks, start=1):
        if not isinstance(item, dict):
            raise SystemExit(f"benchmark task {index} must be an object.")
        if not str(item.get("id", "")).strip():
            raise SystemExit(f"benchmark task {index} is missing id.")
        normalized.append(item)
    return normalized


def load_suite(path: Path) -> dict[str, Any]:
    data = read_json(path)
    if not isinstance(data, dict):
        raise SystemExit("benchmark suite must be a JSON object.")
    task_list(data)
    return data


def find_task(suite: dict[str, Any], task_id: str) -> dict[str, Any]:
    for task in task_list(suite):
        if str(task.get("id")) == task_id:
            return task
    raise SystemExit(f"task not found in suite: {task_id}")


def as_string_list(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SystemExit(f"{label} must be a list.")
    return [str(item) for item in value if str(item).strip()]


def repo_root_from_context_base(base: Path) -> Path:
    for candidate in [base, *base.parents]:
        if (candidate / ".agents" / "manage.py").exists():
            return candidate
    return base


def resolve_context_path(base: Path, raw_path: str) -> tuple[Path | None, str]:
    normalized = raw_path.replace("\\", "/")
    if normalized.startswith("repo:"):
        rel_text = normalized.removeprefix("repo:").lstrip("/")
        rel = Path(rel_text)
        if rel.is_absolute() or ".." in rel.parts:
            return None, f"skipped unsafe context path `{raw_path}`"
        return repo_root_from_context_base(base) / rel, normalized
    rel = Path(raw_path)
    if rel.is_absolute() or ".." in rel.parts:
        return None, f"skipped unsafe context path `{raw_path}`"
    return base / rel, normalized


def collect_context(base: Path, paths: list[str]) -> tuple[str, list[str], list[str]]:
    parts: list[str] = []
    included: list[str] = []
    skipped: list[str] = []
    for raw_path in paths:
        path, issue = resolve_context_path(base, raw_path)
        if path is None:
            skipped.append(issue)
            continue
        if not path.exists() or not path.is_file():
            skipped.append(f"missing context path `{raw_path}`")
            continue
        included.append(issue)
        parts.append(f"## {issue}\n\n{read_text(path)}")
    return "\n\n".join(parts), included, skipped


def display_agent(value: str) -> str:
    text = value.strip()
    if not text:
        return "Agent"
    if text.lower() == "codex":
        return "Codex"
    return text[:1].upper() + text[1:]


def subject_line(
    agent_tool: str,
    model_label: str,
    workflow_name: str | None,
    workflow_version: str | None,
) -> str:
    tool = display_agent(agent_tool)
    model = model_label.strip() or "unlabeled-model"
    if workflow_name:
        version = f" {workflow_version}" if workflow_version else ""
        return f"{tool} {model} on {workflow_name}{version}"
    return f"{tool} {model}"


from support.benchmark_common_metrics import (
    aggregate_standard_metrics,
    comparability_issues,
    detect_outliers,
    document_vision_score,
    metric_distribution,
    metrics_standard_from_timings,
    normalize_agent_task_metrics,
    normalize_failure_taxonomy,
    normalize_metrics_standard,
    normalize_quality,
    normalize_run_config,
    normalize_trajectory_signals,
    normalized_model_benchmark_report,
    percentile,
    quality_section_summary,
    require_list,
    retrieval_score,
    run_report_path,
    trajectory_score,
    validate_agent_task_metrics,
    validate_benchmark_result_shape,
    validate_metrics_standard,
    validate_run_config,
    validate_trajectory_signals,
)
