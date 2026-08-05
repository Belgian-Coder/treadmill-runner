#!/usr/bin/env python3
"""Deterministic LocalModelCandidateV1 screening without downloads."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse


REQUIRED_FIELDS = {
    "schema_version",
    "id",
    "roles",
    "source_url",
    "license_url",
    "sha256",
    "runtime_family",
    "minimum_runtime",
    "total_parameters_billion",
    "active_parameters_billion",
    "context_tokens",
    "quantization",
    "expected_download_size_gb",
    "overlap_classification",
    "seeking_promotion",
    "benchmark_suite_ref",
    "requires_credentials",
    "requires_account",
    "accelerators",
    "extensions",
}
ALLOWED_FIELDS = set(REQUIRED_FIELDS)
IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{0,95}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _positive_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) > 0


def _https_url(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username


def _direct_model_url(value: object) -> bool:
    if not _https_url(value):
        return False
    path = urlparse(str(value)).path.lower()
    return path.endswith(".gguf") or ("/resolve/" in path and ".gguf" in path)


def _runtime_build_number(value: object) -> int | None:
    match = re.fullmatch(r"b([0-9]+)", str(value or "").strip().lower())
    return int(match.group(1)) if match else None


def _dedicated_vram_gb(resources: dict[str, Any]) -> float:
    gpu = resources.get("gpu") if isinstance(resources.get("gpu"), dict) else {}
    devices = gpu.get("devices") if isinstance(gpu.get("devices"), list) else []
    values: list[float] = []
    for device in devices:
        if not isinstance(device, dict) or device.get("device_type") != "dedicated":
            continue
        free_mb = device.get("memory_free_mb")
        try:
            if free_mb not in (None, ""):
                values.append(float(free_mb) / 1024)
                continue
        except (TypeError, ValueError):
            free_mb = None
        adapter_bytes = device.get("adapter_ram_bytes")
        try:
            if adapter_bytes not in (None, ""):
                values.append(float(adapter_bytes) / (1024**3))
        except (TypeError, ValueError):
            continue
    return max(values, default=0.0)


def candidate_shape_issues(candidate: object) -> list[str]:
    if not isinstance(candidate, dict):
        return ["candidate must be a JSON object"]
    issues: list[str] = []
    for field in sorted(REQUIRED_FIELDS - set(candidate)):
        issues.append(f"missing required field {field}")
    for field in sorted(set(candidate) - ALLOWED_FIELDS):
        issues.append(f"unknown field {field}")
    if candidate.get("schema_version") != 1:
        issues.append("schema_version must be 1")
    if not isinstance(candidate.get("id"), str) or not IDENTIFIER.fullmatch(str(candidate.get("id", ""))):
        issues.append("id must use lowercase letters, digits, and hyphens")
    roles = candidate.get("roles")
    if (
        not isinstance(roles, list)
        or not roles
        or not all(isinstance(role, str) and role.strip() for role in roles)
        or len(roles) != len(set(roles))
    ):
        issues.append("roles must be a non-empty unique string list")
    if not _direct_model_url(candidate.get("source_url")):
        issues.append("source_url must be a direct HTTPS GGUF URL")
    if not _https_url(candidate.get("license_url")):
        issues.append("license_url must be a direct HTTPS URL")
    if not isinstance(candidate.get("sha256"), str) or not SHA256.fullmatch(str(candidate.get("sha256", ""))):
        issues.append("sha256 must be a lowercase SHA-256")
    for field in ("runtime_family", "minimum_runtime", "quantization"):
        if not isinstance(candidate.get(field), str) or not str(candidate.get(field, "")).strip():
            issues.append(f"{field} must be a non-empty string")
    for field in (
        "total_parameters_billion",
        "active_parameters_billion",
        "context_tokens",
        "expected_download_size_gb",
    ):
        if not _positive_number(candidate.get(field)):
            issues.append(f"{field} must be a positive number")
    if (
        _positive_number(candidate.get("active_parameters_billion"))
        and _positive_number(candidate.get("total_parameters_billion"))
        and float(candidate["active_parameters_billion"])
        > float(candidate["total_parameters_billion"])
    ):
        issues.append("active_parameters_billion cannot exceed total_parameters_billion")
    if candidate.get("overlap_classification") not in {"distinct", "overlaps-accepted"}:
        issues.append("overlap_classification must be distinct or overlaps-accepted")
    for field in ("seeking_promotion", "requires_credentials", "requires_account"):
        if not isinstance(candidate.get(field), bool):
            issues.append(f"{field} must be boolean")
    accelerators = candidate.get("accelerators")
    if (
        not isinstance(accelerators, list)
        or not accelerators
        or not all(item in {"cpu", "gpu"} for item in accelerators)
        or len(accelerators) != len(set(accelerators))
    ):
        issues.append("accelerators must be a unique list containing cpu and/or gpu")
    if candidate.get("seeking_promotion") is True and not str(candidate.get("benchmark_suite_ref", "")).strip():
        issues.append("benchmark_suite_ref is required when seeking promotion")
    if not isinstance(candidate.get("extensions"), dict):
        issues.append("extensions must be an object")
    return issues


def evaluate_candidate(
    candidate: object,
    *,
    resources: dict[str, Any],
    policy: dict[str, Any],
    supported_runtime_families: set[str] | dict[str, str],
) -> dict[str, Any]:
    reasons = candidate_shape_issues(candidate)
    value = candidate if isinstance(candidate, dict) else {}
    size_gb = float(value.get("expected_download_size_gb", 0) or 0)
    memory = resources.get("memory") if isinstance(resources.get("memory"), dict) else {}
    disk = resources.get("disk") if isinstance(resources.get("disk"), dict) else {}
    try:
        total_ram_gb = float(memory.get("total_gb", 0) or 0)
        available_ram_gb = float(memory.get("available_gb", 0) or 0)
        free_disk_gb = float(disk.get("free_gb", 0) or 0)
        max_download_gb = float(policy.get("max_download_gb", 20) or 20)
    except (TypeError, ValueError):
        total_ram_gb = available_ram_gb = free_disk_gb = 0.0
        max_download_gb = 20.0
        reasons.append("host resource or policy values are invalid")
    if value.get("requires_credentials") is True or value.get("requires_account") is True:
        reasons.append("candidate requires credentials or an account gate")
    runtime_family = str(value.get("runtime_family", ""))
    if runtime_family and runtime_family not in supported_runtime_families:
        reasons.append(f"runtime family {runtime_family} is not supported")
    elif runtime_family and isinstance(supported_runtime_families, dict):
        requested_build = _runtime_build_number(value.get("minimum_runtime"))
        supported_build = _runtime_build_number(supported_runtime_families.get(runtime_family))
        if requested_build is None or supported_build is None:
            reasons.append("minimum runtime or supported runtime build is not comparable")
        elif requested_build > supported_build:
            reasons.append(
                f"minimum runtime b{requested_build} exceeds supported b{supported_build}"
            )
    if size_gb > max_download_gb:
        reasons.append(
            f"expected download {size_gb} GB exceeds the {max_download_gb} GB download policy"
        )
    if size_gb > 0 and free_disk_gb < size_gb * 1.25:
        reasons.append("free disk lacks the required 25% safety margin")
    accelerators = value.get("accelerators") if isinstance(value.get("accelerators"), list) else []
    if "cpu" in accelerators and total_ram_gb > 0 and size_gb > total_ram_gb * 0.60:
        reasons.append("CPU model size exceeds 60% of system RAM")
    dedicated_vram_gb = _dedicated_vram_gb(resources)
    if "gpu" in accelerators and (dedicated_vram_gb <= 0 or size_gb > dedicated_vram_gb * 0.80):
        reasons.append("GPU model size exceeds 80% of usable dedicated VRAM")
    if size_gb > 0 and available_ram_gb < size_gb + 4:
        reasons.append("available RAM cannot hold the model plus 4 GB")

    if reasons:
        decision = "reject"
    elif value.get("overlap_classification") == "overlaps-accepted":
        decision = "benchmark-only"
    else:
        decision = "eligible"
    return {
        "schema_version": 1,
        "tool": "local-ai-helper.evaluate-candidate",
        "candidate_id": str(value.get("id", "")),
        "decision": decision,
        "eligible": decision == "eligible",
        "benchmark_required": decision == "benchmark-only",
        "reasons": reasons
        or (
            ["overlapping candidate must outperform an accepted profile on its declared suite"]
            if decision == "benchmark-only"
            else []
        ),
        "limits": {
            "max_download_gb": max_download_gb,
            "disk_safety_margin_percent": 25,
            "cpu_ram_percent": 60,
            "gpu_vram_percent": 80,
            "ram_headroom_gb": 4,
        },
        "resources": {
            "total_ram_gb": total_ram_gb,
            "available_ram_gb": available_ram_gb,
            "free_disk_gb": free_disk_gb,
            "usable_dedicated_vram_gb": round(dedicated_vram_gb, 4),
        },
    }
