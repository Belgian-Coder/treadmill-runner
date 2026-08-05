#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from local_ai_support import broker_tools
from local_ai_support import model_lease
from local_ai_support import policy_impl
from local_ai_support import resources_impl
from local_ai_support.routing_defaults import (
    BOOTSTRAP_AUTO_DOWNLOAD_VALUES,
    CACHED_REJECTION,
    COMMERCIAL_OK_LICENSES,
    CONFIG_RELATIVE_PATH,
    DEFAULT_BENCHMARK_POLICY,
    DEFAULT_CACHE_DIR,
    DEFAULT_LOCAL_SETTINGS,
    DEFAULT_EMBEDDING_PROFILES,
    DEFAULT_IMAGE_DESCRIPTION_PROFILE,
    DEFAULT_LIMITS,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_PROJECT_SETTINGS,
    DEFAULT_MODEL_CATALOG,
    DEFAULT_MODEL_PROFILE,
    DEFAULT_MODEL_PROFILES,
    DEFAULT_OPTIONAL_PROFILES,
    DEFAULT_PRIMARY_PROFILES,
    DEFAULT_SERVER_CONFIG,
    DEFAULT_TASK_ATTEMPT_POLICY,
    DEFAULT_TASK_ENVELOPES,
    DEFAULT_MODEL_TASK_ENVELOPES,
    DEFAULT_TASK_MODEL_PROFILES,
    DEFAULT_TOOLS_CONFIG,
    DEFAULT_VISION_PROFILES,
    DISABLE_ENV_VALUES,
    GPU_BACKENDS,
    GPU_ALLOW_ENV,
    GPU_MODE_VALUES,
    LOCAL_SETTINGS_RELATIVE_PATH,
    PERFORMANCE_OVERRIDE_FIELDS,
    PROJECT_SETTINGS_RELATIVE_PATH,
    PROMPT_VERSION,
    REQUIRED_ENV_VALUE,
    SUPPORTED_CACHE_TYPES,
    SUPPORTED_REASONING_VALUES,
    TASK_GROUPS,
)


EXPERIMENTAL_WORKLOAD_BACKENDS = {"hip", "sycl", "opencl"}
CATALOG_PROFILE = "Local AI catalog profile "
NO_JSON_OBJECT = "model output did not contain a JSON object"
JSON_PROMPT_PREFIX = "Return only one compact JSON object with keys "
JSON_CONFIDENCE_RULE = "confidence must be a numeric value from 0 to 1, not a string."
UNSUPPORTED_LOCAL_AI_TASK = "unsupported local AI task "


def normalize_string_list(value, default):
    raw_items = value if isinstance(value, list) else default
    normalized = []
    for item in raw_items:
        text = str(item).replace("\\", "/").strip().strip("/")
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def input_hash(item):
    included = {
        "id": item.get("id", ""),
        "name": item.get("name", ""),
        "task": item.get("task", ""),
        "category": item.get("category", ""),
        "description": item.get("description", ""),
        "summary": item.get("summary", ""),
        "source_paths": item.get("source_paths", []),
        "related_skills": item.get("related_skills", []),
        "scripts": item.get("scripts", []),
        "outputs": item.get("outputs", []),
    }
    return hashlib.sha256(stable_json(included).encode("utf-8")).hexdigest()


def cache_path(root, task, item_id, cache_dir=DEFAULT_CACHE_DIR):
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", item_id).strip("-") or "item"
    return root / cache_dir / task / f"{safe_id}.json"


def config_path(root):
    return root / CONFIG_RELATIVE_PATH


def local_settings_path(root):
    return root / LOCAL_SETTINGS_RELATIVE_PATH


def project_settings_path(root):
    return root / PROJECT_SETTINGS_RELATIVE_PATH


def default_local_settings():
    return json.loads(json.dumps(DEFAULT_LOCAL_SETTINGS))


def default_project_settings():
    return json.loads(json.dumps(DEFAULT_PROJECT_SETTINGS))


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def normalize_performance_overrides(raw_values):
    raw = raw_values if isinstance(raw_values, dict) else {}
    values = {}
    integer_bounds = {
        "threads": (1, 512),
        "threads_batch": (1, 512),
        "context_tokens": (256, 262144),
        "output_tokens": (1, 32768),
        "batch_size": (1, 8192),
        "ubatch_size": (1, 8192),
        "timeout_seconds": (1, 3600),
        "max_text_chars": (1, 1_000_000),
        "parallel_slots": (1, 8),
    }
    for key, (minimum, maximum) in integer_bounds.items():
        if key in raw:
            values[key] = int_limit(raw[key], DEFAULT_LIMITS.get(key, 1), minimum=minimum, maximum=maximum)
    if "confidence_threshold" in raw:
        values["confidence_threshold"] = float_limit(
            raw["confidence_threshold"], DEFAULT_LIMITS["confidence_threshold"], minimum=0.0, maximum=1.0
        )
    for key in ("cache_type_k", "cache_type_v"):
        if key in raw:
            cache_type = str(raw[key]).strip().lower()
            if cache_type in SUPPORTED_CACHE_TYPES:
                values[key] = cache_type
    return values


def normalize_settings_layer(raw_settings, *, local):
    settings = default_local_settings() if local else default_project_settings()
    issues = []
    if isinstance(raw_settings, dict):
        settings["schema_version"] = raw_settings.get("schema_version", settings["schema_version"])
        raw_routes = raw_settings.get("task_model_profiles", {})
        if isinstance(raw_routes, dict):
            settings["task_model_profiles"] = {
                str(task).strip(): profiles
                for task, value in raw_routes.items()
                if str(task).strip() and (profiles := normalize_profile_list(value))
            }
        raw_limits = raw_settings.get("limits", {})
        if isinstance(raw_limits, dict):
            unsupported_limits = sorted(set(raw_limits) - PERFORMANCE_OVERRIDE_FIELDS)
            if unsupported_limits:
                issues.append("Unsupported performance fields: " + ", ".join(unsupported_limits))
        settings["limits"] = normalize_performance_overrides(raw_limits)
        raw_profile_overrides = raw_settings.get("model_profiles", {})
        if isinstance(raw_profile_overrides, dict):
            profile_overrides = {}
            for profile, values in raw_profile_overrides.items():
                name = normalized_profile_name(str(profile))
                if not name:
                    continue
                if isinstance(values, dict):
                    unsupported_values = sorted(set(values) - PERFORMANCE_OVERRIDE_FIELDS)
                    if unsupported_values:
                        issues.append(
                            f"Unsupported performance fields for profile {name}: "
                            + ", ".join(unsupported_values)
                        )
                normalized_values = normalize_performance_overrides(values)
                if normalized_values:
                    profile_overrides[name] = normalized_values
            settings["model_profiles"] = profile_overrides
        raw_order = raw_settings.get("backend_order", [])
        settings["backend_order"] = [
            item for item in normalize_profile_list(raw_order) if item == "auto" or item == "cpu" or item in GPU_BACKENDS
        ]
        if local:
            raw_gpu = raw_settings.get("gpu", {})
            if isinstance(raw_gpu, dict):
                settings["gpu"].update(raw_gpu)
            raw_overrides = raw_settings.get("runtime_overrides", [])
            if isinstance(raw_overrides, list):
                settings["runtime_overrides"] = [item for item in raw_overrides if isinstance(item, dict)]
            raw_quarantine = raw_settings.get("backend_quarantine", [])
            if isinstance(raw_quarantine, list):
                settings["backend_quarantine"] = [item for item in raw_quarantine if isinstance(item, dict)]
            raw_calibrations = raw_settings.get("backend_calibrations", [])
            if isinstance(raw_calibrations, list):
                settings["backend_calibrations"] = [item for item in raw_calibrations if isinstance(item, dict)]

        allowed = {
            "schema_version", "task_model_profiles", "limits", "model_profiles", "backend_order"
        }
        if local:
            allowed.update({"gpu", "runtime_overrides", "backend_quarantine", "backend_calibrations"})
        unsupported = sorted(set(raw_settings) - allowed)
        if unsupported:
            issues.append("Unsupported settings fields: " + ", ".join(unsupported))

    if issues:
        settings["issues"] = issues
    if not local:
        return settings

    gpu = settings["gpu"]
    mode = str(gpu.get("mode", "auto")).strip().lower()
    if mode not in GPU_MODE_VALUES:
        mode = "auto"
    gpu["mode"] = mode
    preferred = normalize_profile_list(gpu.get("preferred_backends", ["cuda", "vulkan", "cpu"]))
    gpu["preferred_backends"] = [
        backend for backend in preferred if backend == "cpu" or backend in GPU_BACKENDS
    ] or ["cuda", "vulkan", "cpu"]
    gpu["allow_integrated"] = bool(gpu.get("allow_integrated", False))
    gpu["auto_download_runtime"] = bool(gpu.get("auto_download_runtime", True))
    gpu["auto_calibrate"] = bool(gpu.get("auto_calibrate", True))
    gpu["force_cpu_on_failure"] = bool(gpu.get("force_cpu_on_failure", True))
    gpu["allow_experimental_workloads"] = bool(gpu.get("allow_experimental_workloads", False))
    experimental = normalize_profile_list(gpu.get("experimental_backends", []))
    gpu["experimental_backends"] = [backend for backend in experimental if backend in GPU_BACKENDS]
    gpu["smoke_test_runtime"] = bool(gpu.get("smoke_test_runtime", True))
    gpu["gpu_layers"] = int_limit(gpu.get("gpu_layers", 99), 99, minimum=0, maximum=999)
    gpu["probe_timeout_seconds"] = int_limit(
        gpu.get("probe_timeout_seconds", 5),
        5,
        minimum=1,
        maximum=60,
    )
    gpu["smoke_timeout_seconds"] = int_limit(
        gpu.get("smoke_timeout_seconds", 90),
        90,
        minimum=5,
        maximum=600,
    )
    gpu["performance_threshold_percent"] = float_limit(
        gpu.get("performance_threshold_percent", 10.0),
        10.0,
        minimum=0.0,
        maximum=1000.0,
    )
    return settings


def normalize_local_settings(raw_settings):
    return normalize_settings_layer(raw_settings, local=True)


def normalize_project_settings(raw_settings):
    return normalize_settings_layer(raw_settings, local=False)


def read_project_settings(root):
    path = project_settings_path(root)
    if not path.exists():
        settings = normalize_project_settings({})
        settings["exists"] = False
        return settings
    try:
        settings = normalize_project_settings(read_json(path))
        settings["exists"] = True
        return settings
    except ValueError as exc:
        settings = normalize_project_settings({})
        settings["exists"] = True
        settings["issues"] = [str(exc)]
        return settings


def read_local_settings(root):
    path = local_settings_path(root)
    if not path.exists():
        settings = normalize_local_settings({})
        settings["exists"] = False
        settings["gpu"]["mode"] = "off"
        settings["gpu"]["reason"] = f"{LOCAL_SETTINGS_RELATIVE_PATH} is missing; using CPU until setup writes it."
        return settings
    try:
        settings = normalize_local_settings(read_json(path))
        settings["exists"] = True
        return settings
    except ValueError as exc:
        settings = normalize_local_settings({})
        settings["exists"] = True
        settings["issues"] = [str(exc)]
        return settings


def persistable_local_settings(settings):
    normalized = normalize_local_settings(settings)
    schema_version = settings.get("schema_version", 1)
    try:
        schema_version = int(schema_version)
    except (TypeError, ValueError):
        schema_version = 1
    return {
        "schema_version": max(2, schema_version),
        "gpu": normalized.get("gpu", {}),
        "runtime_overrides": normalized.get("runtime_overrides", []),
        "task_model_profiles": normalized.get("task_model_profiles", {}),
        "limits": normalized.get("limits", {}),
        "model_profiles": normalized.get("model_profiles", {}),
        "backend_order": normalized.get("backend_order", []),
        "backend_quarantine": normalized.get("backend_quarantine", []),
        "backend_calibrations": normalized.get("backend_calibrations", []),
    }


def persistable_project_settings(settings):
    normalized = normalize_project_settings(settings)
    return {
        "schema_version": 1,
        "task_model_profiles": normalized.get("task_model_profiles", {}),
        "limits": normalized.get("limits", {}),
        "model_profiles": normalized.get("model_profiles", {}),
        "backend_order": normalized.get("backend_order", []),
    }


def write_local_settings(root, settings):
    path = local_settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(persistable_local_settings(settings), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_project_settings(root, settings):
    path = project_settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(persistable_project_settings(settings), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def disable_gpu_in_local_settings(root, reason):
    settings = read_local_settings(root)
    gpu = settings.get("gpu") if isinstance(settings.get("gpu"), dict) else {}
    if not bool(gpu.get("force_cpu_on_failure", True)):
        return
    gpu["mode"] = "off"
    gpu["reason"] = "GPU acceleration was disabled locally after an automatic runtime failure."
    gpu["last_failure"] = reason
    gpu["last_failure_unix"] = int(time.time())
    settings["gpu"] = gpu
    write_local_settings(root, settings)


def selected_model_from_config(config):
    model = config.get("_selected_model")
    return model if isinstance(model, dict) else None


def selected_profile(config):
    model = selected_model_from_config(config)
    if model is not None and str(model.get("profile", "")).strip():
        return str(model.get("profile", "")).strip()
    return str(config.get("selected_profile") or DEFAULT_MODEL_PROFILE).strip()


def selected_model_sha(config):
    model = selected_model_from_config(config)
    if model is not None:
        return str(model.get("actual_sha256", "")).strip().lower()
    return str(config.get("selected_model_sha256", "")).strip().lower()


def current_device_fingerprint(backend=""):
    info = resources_impl.gpu_info()
    devices = info.get("devices") if isinstance(info.get("devices"), list) else []
    rows = []
    normalized_backend = str(backend).strip().lower()
    for device in devices:
        if not isinstance(device, dict):
            continue
        vendor = str(device.get("vendor", "")).strip().lower()
        device_backend = str(device.get("backend", "")).strip().lower()
        if normalized_backend == "cuda" and vendor != "nvidia" and device_backend != "cuda":
            continue
        if normalized_backend in {"hip", "vulkan"} and vendor not in {"amd", "intel", "nvidia", "unknown", ""}:
            continue
        name = " ".join(str(device.get("name", "")).split()).lower()
        device_type = str(device.get("device_type", "")).strip().lower()
        rows.append("|".join(part for part in [vendor, device_type, device_backend, name] if part))
    return hashlib.sha256("\n".join(sorted(rows)).encode("utf-8")).hexdigest()[:16] if rows else ""


def runtime_sha(runtime):
    value = str(runtime.get("actual_sha256") or runtime.get("sha256") or "").strip().lower()
    if value:
        return value
    path = Path(str(runtime.get("resolved_path", "")))
    return sha256_file(path).lower() if path.exists() else ""


def backend_record_matches(
    record,
    *,
    profile,
    backend,
    runtime_hash,
    model_hash,
    device_fingerprint,
):
    if str(record.get("profile", "")).strip() != profile:
        return False
    if str(record.get("backend", "")).strip().lower() != backend:
        return False
    record_runtime = str(record.get("runtime_sha256", "")).strip().lower()
    if record_runtime and runtime_hash and record_runtime != runtime_hash:
        return False
    record_model = str(record.get("model_sha256", "")).strip().lower()
    if record_model and model_hash and record_model != model_hash:
        return False
    record_device = str(record.get("device_fingerprint", "")).strip()
    if record_device and device_fingerprint and record_device not in {"*", device_fingerprint}:
        return False
    return True


def backend_quarantine_reason(
    settings,
    *,
    profile,
    backend,
    runtime_hash,
    model_hash,
    device_fingerprint,
):
    records = settings.get("backend_quarantine", [])
    if not isinstance(records, list):
        return ""
    for record in records:
        if not isinstance(record, dict):
            continue
        if backend_record_matches(
            record,
            profile=profile,
            backend=backend,
            runtime_hash=runtime_hash,
            model_hash=model_hash,
            device_fingerprint=device_fingerprint,
        ):
            return str(record.get("reason", "backend is quarantined")).strip() or "backend is quarantined"
    return ""


def calibration_decision(
    settings,
    *,
    profile,
    backend,
    runtime_hash,
    model_hash,
    device_fingerprint,
):
    records = settings.get("backend_calibrations", [])
    if not isinstance(records, list):
        return None
    for record in records:
        if not isinstance(record, dict):
            continue
        if not backend_record_matches(
            record,
            profile=profile,
            backend=backend,
            runtime_hash=runtime_hash,
            model_hash=model_hash,
            device_fingerprint=device_fingerprint,
        ):
            continue
        decision = str(record.get("decision", "")).strip().lower()
        if decision in {"cpu", "gpu"}:
            return dict(record)
    return None


def upsert_backend_record(settings, key, record):
    records = settings.get(key, [])
    if not isinstance(records, list):
        records = []
    profile = str(record.get("profile", "")).strip()
    backend = str(record.get("backend", "")).strip().lower()
    runtime_hash = str(record.get("runtime_sha256", "")).strip().lower()
    model_hash = str(record.get("model_sha256", "")).strip().lower()
    device_fingerprint = str(record.get("device_fingerprint", "")).strip()
    retained = [
        item
        for item in records
        if not (
            isinstance(item, dict)
            and backend_record_matches(
                item,
                profile=profile,
                backend=backend,
                runtime_hash=runtime_hash,
                model_hash=model_hash,
                device_fingerprint=device_fingerprint,
            )
        )
    ]
    settings[key] = [record] + retained[:49]


def record_backend_quarantine(
    root,
    *,
    profile,
    backend,
    runtime,
    model,
    reason,
):
    settings = read_local_settings(root)
    record = {
        "profile": profile,
        "backend": backend,
        "runtime_sha256": runtime_sha(runtime),
        "model_sha256": str((model or {}).get("actual_sha256") or selected_model_sha({"_selected_model": model}) or "").lower(),
        "device_fingerprint": current_device_fingerprint(backend),
        "reason": " ".join(str(reason).split())[:1000],
        "created_at_unix": int(time.time()),
    }
    upsert_backend_record(settings, "backend_quarantine", record)
    write_local_settings(root, settings)
    return record


def record_backend_calibration(
    root,
    *,
    profile,
    backend,
    runtime,
    model,
    cpu_ms,
    gpu_ms,
    threshold_percent,
):
    settings = read_local_settings(root)
    slower_percent = ((gpu_ms - cpu_ms) / max(cpu_ms, 0.0001)) * 100.0
    decision = "cpu" if slower_percent > threshold_percent else "gpu"
    if decision == "cpu":
        reason = f"{backend} was {slower_percent:.1f}% slower than CPU"
    else:
        faster_percent = max(0.0, ((cpu_ms - gpu_ms) / max(cpu_ms, 0.0001)) * 100.0)
        reason = f"{backend} passed calibration and was {faster_percent:.1f}% faster than CPU"
    record = {
        "profile": profile,
        "backend": backend,
        "runtime_sha256": runtime_sha(runtime),
        "model_sha256": str(model.get("actual_sha256", "")).strip().lower(),
        "device_fingerprint": current_device_fingerprint(backend),
        "decision": decision,
        "reason": reason,
        "cpu_e2e_latency_ms": round(cpu_ms, 2),
        "gpu_e2e_latency_ms": round(gpu_ms, 2),
        "threshold_percent": threshold_percent,
        "created_at_unix": int(time.time()),
    }
    upsert_backend_record(settings, "backend_calibrations", record)
    write_local_settings(root, settings)
    return record


def gpu_env_allowed():
    return os.environ.get(GPU_ALLOW_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def local_gpu_enabled(settings):
    gpu = settings.get("gpu") if isinstance(settings.get("gpu"), dict) else {}
    mode = str(gpu.get("mode", "auto")).strip().lower()
    return mode in {"auto", "force"} or (mode != "off" and gpu_env_allowed())


def detected_gpu_backend_order(settings):
    gpu = settings.get("gpu") if isinstance(settings.get("gpu"), dict) else {}
    if str(gpu.get("mode", "auto")).strip().lower() == "off":
        return []
    preferred = [
        str(item).strip().lower()
        for item in gpu.get("preferred_backends", ["cuda", "vulkan", "cpu"])
        if str(item).strip()
    ]
    allow_integrated = bool(gpu.get("allow_integrated", False))
    info = resources_impl.gpu_info()
    devices = info.get("devices") if isinstance(info.get("devices"), list) else []
    allowed = set()
    for device in devices:
        if not isinstance(device, dict):
            continue
        device_type = str(device.get("device_type", "unknown")).strip().lower()
        if not bool(device.get("safe_for_auto", device_type in {"dedicated", "integrated"})):
            continue
        if device_type == "integrated" and not allow_integrated:
            continue
        vendor = str(device.get("vendor", "unknown")).strip().lower()
        backend = str(device.get("backend", "")).strip().lower()
        if vendor == "nvidia" or backend == "cuda":
            allowed.update({"cuda", "vulkan"})
        elif vendor == "amd":
            allowed.update({"vulkan", "hip"})
        elif vendor == "intel":
            allowed.update({"vulkan", "sycl"})
        elif device_type in {"dedicated", "integrated"}:
            allowed.add("vulkan")
    return [backend for backend in preferred if backend in allowed]


def expanded_backend_order(root, configured_order, settings):
    raw_order = configured_order if isinstance(configured_order, list) else ["auto", "cpu"]
    order = []
    for raw_backend in raw_order:
        backend = str(raw_backend).strip().lower()
        if not backend:
            continue
        if backend == "auto":
            gpu = settings.get("gpu") if isinstance(settings.get("gpu"), dict) else {}
            mode = str(gpu.get("mode", "auto")).strip().lower()
            if mode == "force":
                candidates = [
                    str(item).strip().lower()
                    for item in gpu.get("preferred_backends", ["cuda", "vulkan", "cpu"])
                    if str(item).strip().lower() in GPU_BACKENDS
                ]
            else:
                candidates = detected_gpu_backend_order(settings)
            for detected_backend in candidates:
                if detected_backend not in order:
                    order.append(detected_backend)
            continue
        if backend != "cpu" and backend not in GPU_BACKENDS:
            continue
        if backend != "cpu" and not local_gpu_enabled(settings):
            continue
        if backend not in order:
            order.append(backend)
    if "cpu" not in order:
        order.append("cpu")
    return order


def normalize_bootstrap_config(raw_bootstrap):
    bootstrap = {
        "auto_download": "never",
    }
    if isinstance(raw_bootstrap, dict):
        bootstrap.update(raw_bootstrap)
    bootstrap["auto_download"] = str(bootstrap.get("auto_download", "never")).strip().lower()
    return bootstrap


def should_auto_bootstrap(config, *, check):
    if check:
        return False
    bootstrap = normalize_bootstrap_config(config.get("bootstrap", {}))
    return bootstrap["auto_download"] in BOOTSTRAP_AUTO_DOWNLOAD_VALUES


def run_bootstrap_command(
    root,
    config,
    *,
    task,
    check,
):
    if not should_auto_bootstrap(config, check=check):
        return False, []
    script = Path(__file__).resolve().parents[1] / "setup_local_ai.py"
    if not script.exists():
        return False, [f"Local AI bootstrap script is missing: {script}"]
    command = [
        sys.executable,
        "-B",
        str(script),
        "--root",
        str(root),
        "bootstrap",
        "--task",
        task,
        "--json",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    except OSError as exc:
        return False, [f"Local AI bootstrap failed to start: {exc}"]
    if completed.returncode == 0:
        return True, []
    output = completed.stdout.strip()
    if not output:
        return False, ["Local AI bootstrap failed without output."]
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return False, [line for line in output.splitlines() if line.strip()]
    issues = payload.get("issues", [])
    if isinstance(issues, list) and issues:
        return False, [str(issue) for issue in issues]
    next_action = payload.get("next_action")
    return False, [str(next_action or "Local AI bootstrap did not complete.")]


def normalized_profile_name(profile):
    return profile


def normalize_model_profiles(raw_profiles):
    profiles = {
        name: dict(value) for name, value in DEFAULT_MODEL_PROFILES.items()
    }
    if isinstance(raw_profiles, dict):
        for name, value in raw_profiles.items():
            if isinstance(value, dict) and str(name).strip():
                profiles[str(name).strip()] = dict(value)
    return profiles


def normalize_profile_list(value):
    if isinstance(value, list):
        profiles = [normalized_profile_name(str(item).strip()) for item in value if str(item).strip()]
        return list(dict.fromkeys(profiles))
    if isinstance(value, str) and value.strip():
        return [normalized_profile_name(value.strip())]
    return []


def normalize_task_model_profiles(raw_routes):
    routes = {
        task: list(profiles) for task, profiles in DEFAULT_TASK_MODEL_PROFILES.items()
    }
    if isinstance(raw_routes, dict):
        for task, value in raw_routes.items():
            profiles = normalize_profile_list(value)
            if str(task).strip() and profiles:
                routes[str(task).strip()] = profiles
    return routes


def normalize_task_attempt_policy(raw_policy):
    policy = dict(DEFAULT_TASK_ATTEMPT_POLICY)
    if isinstance(raw_policy, dict):
        policy.update(raw_policy)
    policy["max_attempts_per_profile"] = int_limit(
        policy.get("max_attempts_per_profile"),
        int(DEFAULT_TASK_ATTEMPT_POLICY["max_attempts_per_profile"]),
        minimum=1,
        maximum=3,
    )
    policy["retry_on_low_confidence"] = bool(policy.get("retry_on_low_confidence", True))
    policy["retry_on_plain_text"] = bool(policy.get("retry_on_plain_text", True))
    policy["retry_failure_classes"] = normalize_string_list(
        policy.get("retry_failure_classes"),
        DEFAULT_TASK_ATTEMPT_POLICY["retry_failure_classes"],
    )
    policy["handoff_failure_classes"] = normalize_string_list(
        policy.get("handoff_failure_classes"),
        DEFAULT_TASK_ATTEMPT_POLICY["handoff_failure_classes"],
    )
    fallback = str(policy.get("fallback", DEFAULT_TASK_ATTEMPT_POLICY["fallback"])).strip()
    policy["fallback"] = fallback or DEFAULT_TASK_ATTEMPT_POLICY["fallback"]
    return policy


def normalize_benchmark_policy(raw_policy):
    policy = dict(DEFAULT_BENCHMARK_POLICY)
    if isinstance(raw_policy, dict):
        policy.update(raw_policy)
    gates = dict(DEFAULT_BENCHMARK_POLICY["promotion_gates"])
    if isinstance(policy.get("promotion_gates"), dict):
        gates.update(policy["promotion_gates"])
    policy["promotion_gates"] = {str(key): bool(value) for key, value in gates.items()}
    policy["candidate_memory_limit_gb"] = float_limit(
        policy.get("candidate_memory_limit_gb"),
        float(DEFAULT_BENCHMARK_POLICY["candidate_memory_limit_gb"]),
        minimum=1.0,
        maximum=256.0,
    )
    policy["require_peak_memory_evidence"] = bool(policy.get("require_peak_memory_evidence", True))
    return policy


def normalize_task_envelopes(raw_envelopes):
    envelopes = {name: dict(value) for name, value in DEFAULT_TASK_ENVELOPES.items()}
    if isinstance(raw_envelopes, dict):
        for name, value in raw_envelopes.items():
            task_name = str(name).strip()
            if not task_name or not isinstance(value, dict):
                continue
            merged = dict(envelopes.get(task_name, {}))
            merged.update(value)
            envelopes[task_name] = merged
    for envelope in envelopes.values():
        envelope["route"] = str(envelope.get("route", "local")).strip() or "local"
        envelope["task_classes"] = normalize_string_list(envelope.get("task_classes"), [])
        envelope["profiles"] = normalize_profile_list(envelope.get("profiles", []))
        envelope["max_attempts"] = int_limit(envelope.get("max_attempts"), 1, minimum=0, maximum=3)
        envelope["validation_gate"] = str(envelope.get("validation_gate", "")).strip()
        envelope["fallback"] = str(envelope.get("fallback", "orchestrator-handoff")).strip() or "orchestrator-handoff"
    return envelopes


def normalize_model_task_envelopes(raw_envelopes):
    envelopes = {name: dict(value) for name, value in DEFAULT_MODEL_TASK_ENVELOPES.items()}
    if isinstance(raw_envelopes, dict):
        for name, value in raw_envelopes.items():
            profile = normalized_profile_name(str(name).strip())
            if not profile or not isinstance(value, dict):
                continue
            merged = dict(envelopes.get(profile, {}))
            merged.update(value)
            envelopes[profile] = merged
    for envelope in envelopes.values():
        envelope["max_task_class"] = str(envelope.get("max_task_class", "")).strip()
        envelope["allowed_task_classes"] = normalize_string_list(envelope.get("allowed_task_classes"), [])
        envelope["blocked_task_classes"] = normalize_string_list(envelope.get("blocked_task_classes"), [])
        envelope["reason"] = " ".join(str(envelope.get("reason", "")).split())[:240]
    return envelopes


def normalize_model_catalog(raw_catalog):
    catalog = {
        name: dict(value) for name, value in DEFAULT_MODEL_CATALOG.items()
    }
    if isinstance(raw_catalog, dict):
        for name, value in raw_catalog.items():
            if not isinstance(value, dict) or not str(name).strip():
                continue
            entry = dict(value)
            profile = normalized_profile_name(str(entry.get("profile", name)).strip())
            if not profile:
                continue
            entry["profile"] = profile
            catalog[profile] = entry
    return catalog


def validate_model_catalog(catalog):
    if not isinstance(catalog, dict):
        return ["Local AI model_catalog must be a JSON object."]
    issues = []
    for profile, value in catalog.items():
        if not isinstance(value, dict):
            issues.append(f"{CATALOG_PROFILE}{profile!r} must be an object.")
            continue
        profile_name = normalized_profile_name(str(value.get("profile", profile)).strip())
        license_name = str(value.get("license", "")).strip().lower()
        download_kind = str(value.get("download_kind", "")).strip().lower()
        source_url = str(value.get("source_url", value.get("source", ""))).strip().lower()
        if not profile_name:
            issues.append("Local AI catalog entries must declare a profile.")
        if license_name not in COMMERCIAL_OK_LICENSES:
            issues.append(
                f"{CATALOG_PROFILE}{profile_name or profile!r} uses unsupported license {license_name!r}."
            )
        if download_kind != "direct":
            issues.append(
                f"{CATALOG_PROFILE}{profile_name or profile!r} must use direct downloads, not {download_kind!r}."
            )
        if not source_url.startswith(("https://", "http://")):
            issues.append(f"{CATALOG_PROFILE}{profile_name or profile!r} must declare an HTTP source_url.")
        if bool(value.get("requires_account", False)):
            issues.append(f"{CATALOG_PROFILE}{profile_name or profile!r} requires an account.")
    return issues


def int_limit(value, default, *, minimum=1, maximum=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def float_limit(value, default, *, minimum=0.0, maximum=None):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def profile_limits(profile, profiles):
    profile_config = profiles.get(profile, {})
    return {
        "threads": int_limit(profile_config.get("threads", DEFAULT_LIMITS["threads"]), DEFAULT_LIMITS["threads"]),
        "threads_batch": int_limit(
            profile_config.get("threads_batch", DEFAULT_LIMITS["threads_batch"]),
            DEFAULT_LIMITS["threads_batch"],
        ),
        "context_tokens": int_limit(
            profile_config.get(
                "context_tokens",
                profile_config.get("ctx_size", DEFAULT_LIMITS["context_tokens"]),
            ),
            DEFAULT_LIMITS["context_tokens"],
        ),
        "output_tokens": int_limit(
            profile_config.get("output_tokens", DEFAULT_LIMITS["output_tokens"]),
            DEFAULT_LIMITS["output_tokens"],
        ),
        "batch_size": int_limit(
            profile_config.get("batch_size", DEFAULT_LIMITS["batch_size"]),
            DEFAULT_LIMITS["batch_size"],
            maximum=8192,
        ),
        "ubatch_size": int_limit(
            profile_config.get("ubatch_size", DEFAULT_LIMITS["ubatch_size"]),
            DEFAULT_LIMITS["ubatch_size"],
            maximum=8192,
        ),
    }


def merge_limits(base, profile, profiles):
    limits = DEFAULT_LIMITS.copy()
    limits.update(profile_limits(profile, profiles))
    profile_config = profiles.get(profile, {})
    for key, default in DEFAULT_LIMITS.items():
        raw_value = base.get(key, limits.get(key, default))
        if isinstance(default, int):
            max_value = 8192 if key in {"batch_size", "ubatch_size"} else None
            limits[key] = int_limit(raw_value, int(limits.get(key, default)), maximum=max_value)
        else:
            limits[key] = float_limit(raw_value, float(limits.get(key, default)))
    for key, value in profile_limits(profile, profiles).items():
        if key in {"context_tokens", "output_tokens", "batch_size", "ubatch_size"}:
            limits[key] = value
    for key in ("cache_type_k", "cache_type_v"):
        raw_value = profile_config.get(key, base.get(key, ""))
        cache_type = str(raw_value).strip().lower()
        if cache_type:
            if cache_type not in SUPPORTED_CACHE_TYPES:
                continue
            limits[key] = cache_type
    reasoning = str(profile_config.get("reasoning", base.get("reasoning", ""))).strip().lower()
    if reasoning in SUPPORTED_REASONING_VALUES:
        limits["reasoning"] = reasoning
    return limits


def apply_profile_to_config(config, profile):
    profiles = config.get("model_profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
    config["selected_profile"] = profile
    config["limits"] = merge_limits(dict(config.get("base_limits", {})), profile, profiles)
    config["limits"].update(config.get("settings_limit_overrides", {}))


def normalize_server_config(raw_server):
    server = dict(DEFAULT_SERVER_CONFIG)
    if isinstance(raw_server, dict):
        server.update(raw_server)
    server["host"] = str(server.get("host", DEFAULT_SERVER_CONFIG["host"]) or DEFAULT_SERVER_CONFIG["host"])
    server["port"] = int_limit(server.get("port"), int(DEFAULT_SERVER_CONFIG["port"]), minimum=1, maximum=65535)
    server["parallel_slots"] = int_limit(server.get("parallel_slots"), 1, minimum=1, maximum=8)
    server["cache_prompt"] = bool(server.get("cache_prompt", True))
    server["mlock"] = bool(server.get("mlock", False))
    return server


def normalize_tools_config(raw_tools):
    tools = dict(DEFAULT_TOOLS_CONFIG)
    if isinstance(raw_tools, dict):
        tools.update(raw_tools)
    tools["allow"] = normalize_profile_list(tools.get("allow", []))
    if not tools["allow"]:
        tools["allow"] = list(DEFAULT_TOOLS_CONFIG["allow"])
    tools["max_read_bytes"] = int_limit(
        tools.get("max_read_bytes"), int(DEFAULT_TOOLS_CONFIG["max_read_bytes"]), maximum=512_000
    )
    tools["max_search_results"] = int_limit(
        tools.get("max_search_results"), int(DEFAULT_TOOLS_CONFIG["max_search_results"]), maximum=500
    )
    tools["max_tree_entries"] = int_limit(
        tools.get("max_tree_entries"), int(DEFAULT_TOOLS_CONFIG["max_tree_entries"]), maximum=1000
    )
    tools["timeout_seconds"] = int_limit(
        tools.get("timeout_seconds"), int(DEFAULT_TOOLS_CONFIG["timeout_seconds"]), maximum=30
    )
    tools["exclude_paths"] = normalize_string_list(
        tools.get("exclude_paths"), DEFAULT_TOOLS_CONFIG["exclude_paths"]
    )
    tools["mode"] = str(tools.get("mode", DEFAULT_TOOLS_CONFIG["mode"]))
    return tools


def load_config(root, task, *, probe_hardware=True):
    env_value = os.environ.get("SKILLS_LOCAL_AI", "").strip().lower()
    if env_value in DISABLE_ENV_VALUES:
        return {
            "enabled": False,
            "status": "disabled",
            "reason": "SKILLS_LOCAL_AI disables local AI for this run.",
        }

    policy_decision = policy_impl.evaluate_use_case(root, task)
    if not policy_decision.get("allowed"):
        return {
            "enabled": False,
            "status": "policy-disabled",
            "required": env_value == REQUIRED_ENV_VALUE,
            "reason": str(policy_decision.get("reason", "Local AI policy requires fallback.")),
            "policy": policy_decision,
        }

    path = config_path(root)
    required_by_env = env_value == REQUIRED_ENV_VALUE
    if not path.exists():
        return {
            "enabled": False,
            "status": "required-failed" if required_by_env else "disabled",
            "required": required_by_env,
            "reason": f"{CONFIG_RELATIVE_PATH} is missing.",
        }

    try:
        config = read_json(path)
    except ValueError as exc:
        return {
            "enabled": False,
            "status": "required-failed" if required_by_env else "fallback",
            "required": required_by_env,
            "reason": str(exc),
        }

    mode = str(config.get("mode", "auto")).strip().lower()
    if required_by_env:
        mode = REQUIRED_ENV_VALUE
    if mode in DISABLE_ENV_VALUES or mode == "off":
        return {
            "enabled": False,
            "status": "disabled",
            "required": False,
            "reason": "Local AI is disabled in config.",
        }

    tasks = config.get("tasks", [])
    if not isinstance(tasks, list) or task not in tasks:
        return {
            "enabled": False,
            "status": "disabled",
            "required": mode == REQUIRED_ENV_VALUE,
            "reason": f"Local AI task {task!r} is not enabled.",
        }

    project_settings = read_project_settings(root)
    local_settings = read_local_settings(root)

    model_catalog = normalize_model_catalog(config.get("model_catalog", {}))
    catalog_issues = validate_model_catalog(model_catalog)
    catalog_profiles = set(model_catalog)
    enabled_tasks = {str(item).strip() for item in tasks if str(item).strip()}
    settings_issues = list(project_settings.get("issues", [])) + list(local_settings.get("issues", []))

    def accepted_routes(layer, source):
        accepted = {}
        for route_task, route_profiles in layer.get("task_model_profiles", {}).items():
            if route_task not in enabled_tasks:
                settings_issues.append(f"{source} route names unknown task {route_task!r}.")
                continue
            unknown = [profile for profile in route_profiles if profile not in catalog_profiles]
            if unknown:
                settings_issues.append(
                    f"{source} route for {route_task!r} names profiles outside the validated catalog: "
                    + ", ".join(unknown)
                )
                continue
            if route_profiles:
                accepted[route_task] = route_profiles
        return accepted

    configured_backend_order = config.get("backend_order", ["auto", "cpu"])
    backend_order_source = "harness-defaults"
    if project_settings.get("backend_order"):
        configured_backend_order = project_settings["backend_order"]
        backend_order_source = "project-settings"
    if local_settings.get("backend_order"):
        configured_backend_order = local_settings["backend_order"]
        backend_order_source = "local-settings"
    backend_order = configured_backend_order
    if not isinstance(backend_order, list) or not backend_order:
        backend_order = ["auto", "cpu"]
    configured_backend_order = [str(item).strip().lower() for item in backend_order if str(item).strip()]
    backend_order = (
        expanded_backend_order(root, backend_order, local_settings)
        if probe_hardware
        else list(configured_backend_order)
    )

    model_profiles = normalize_model_profiles(config.get("model_profiles", {}))
    for source, layer in (("project settings", project_settings), ("local settings", local_settings)):
        for profile, values in layer.get("model_profiles", {}).items():
            if profile not in catalog_profiles:
                settings_issues.append(f"{source} performance override names profile outside the validated catalog: {profile}")
                continue
            model_profiles.setdefault(profile, {}).update(values)
    task_model_profiles = normalize_task_model_profiles(config.get("task_model_profiles", {}))
    route_sources = {route_task: "harness-defaults" for route_task in task_model_profiles}
    for route_task, profiles in accepted_routes(project_settings, "Project settings").items():
        task_model_profiles[route_task] = profiles
        route_sources[route_task] = "project-settings"
    for route_task, profiles in accepted_routes(local_settings, "Local settings").items():
        task_model_profiles[route_task] = profiles
        route_sources[route_task] = "local-settings"
    task_envelopes = normalize_task_envelopes(config.get("task_envelopes", {}))
    model_task_envelopes = normalize_model_task_envelopes(config.get("model_task_envelopes", {}))
    primary_profiles = normalize_profile_list(config.get("primary_profiles", DEFAULT_PRIMARY_PROFILES))
    optional_profiles = normalize_profile_list(config.get("optional_profiles", DEFAULT_OPTIONAL_PROFILES))
    embedding_profiles = normalize_profile_list(config.get("embedding_profiles", DEFAULT_EMBEDDING_PROFILES))
    vision_profiles = normalize_profile_list(config.get("vision_profiles", DEFAULT_VISION_PROFILES))
    if not primary_profiles:
        primary_profiles = list(DEFAULT_PRIMARY_PROFILES)
    if not optional_profiles:
        optional_profiles = list(DEFAULT_OPTIONAL_PROFILES)
    if not embedding_profiles:
        embedding_profiles = list(DEFAULT_EMBEDDING_PROFILES)
    if not vision_profiles:
        vision_profiles = list(DEFAULT_VISION_PROFILES)
    active_profile = normalized_profile_name(
        str(config.get("active_profile", DEFAULT_MODEL_PROFILE)).strip()
        or DEFAULT_MODEL_PROFILE
    )
    profile_order = task_model_profiles.get(task, [])
    if not profile_order:
        profile_order = [active_profile]
    profile_order = list(dict.fromkeys(profile_order))

    configured_limits = config.get("limits", {})
    base_limits = dict(configured_limits) if isinstance(configured_limits, dict) else {}
    limit_sources = {name: "harness-defaults" for name in base_limits}
    for source_name, layer in (("project-settings", project_settings), ("local-settings", local_settings)):
        for name, value in layer.get("limits", {}).items():
            base_limits[name] = value
            limit_sources[name] = source_name
    selected_for_limits = profile_order[0] if profile_order else active_profile
    limits = merge_limits(dict(base_limits), selected_for_limits, model_profiles)
    settings_limit_overrides = {}
    settings_limit_overrides.update(project_settings.get("limits", {}))
    settings_limit_overrides.update(local_settings.get("limits", {}))
    limits.update(settings_limit_overrides)
    server_config = normalize_server_config(config.get("server", {}))
    if "parallel_slots" in settings_limit_overrides:
        server_config["parallel_slots"] = int(settings_limit_overrides["parallel_slots"])

    normalized = {
        "enabled": bool(config.get("enabled", False)),
        "status": "enabled" if bool(config.get("enabled", False)) else "disabled",
        "required": mode == REQUIRED_ENV_VALUE,
        "mode": mode,
        "limits": limits,
        "base_limits": dict(base_limits),
        "settings_limit_overrides": settings_limit_overrides,
        "configured_backend_order": configured_backend_order,
        "backend_order_source": backend_order_source,
        "backend_order": backend_order,
        "project_settings_path": PROJECT_SETTINGS_RELATIVE_PATH,
        "local_settings_path": LOCAL_SETTINGS_RELATIVE_PATH,
        "gpu": local_settings.get("gpu", {}),
        "runtime_overrides": local_settings.get("runtime_overrides", []),
        "project_settings": project_settings,
        "local_settings": local_settings,
        "settings_issues": settings_issues,
        "local_settings_issues": settings_issues,
        "model_profiles": model_profiles,
        "task_model_profiles": task_model_profiles,
        "task_route_sources": route_sources,
        "task_route_source": route_sources.get(task, "harness-defaults"),
        "limit_sources": limit_sources,
        "task_envelopes": task_envelopes,
        "model_task_envelopes": model_task_envelopes,
        "task_attempt_policy": normalize_task_attempt_policy(config.get("task_attempt_policy", {})),
        "benchmark_policy": normalize_benchmark_policy(config.get("benchmark_policy", {})),
        "model_catalog": model_catalog,
        "catalog_issues": catalog_issues,
        "primary_profiles": primary_profiles,
        "optional_profiles": optional_profiles,
        "embedding_profiles": embedding_profiles,
        "vision_profiles": vision_profiles,
        "image_description_profile": str(config.get("image_description_profile", DEFAULT_IMAGE_DESCRIPTION_PROFILE)),
        "profile_order": profile_order,
        "active_profile": active_profile,
        "selected_profile": selected_for_limits,
        "bundle_manifest": str(config.get("bundle_manifest", DEFAULT_MANIFEST_PATH)),
        "cache_dir": str(config.get("cache_dir", DEFAULT_CACHE_DIR)),
        "server": server_config,
        "tools": normalize_tools_config(config.get("tools", {})),
        "bootstrap": normalize_bootstrap_config(config.get("bootstrap", {})),
        "reason": "",
    }
    return normalized


def platform_id():
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "windows" and machine in {"amd64", "x86_64"}:
        return "windows-x64"
    if system == "linux" and machine in {"amd64", "x86_64"}:
        return "linux-x64"
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if system == "darwin" and machine in {"amd64", "x86_64"}:
        return "macos-x64"
    return f"{system}-{machine}"


def resolve_asset(base_dir, rel_path):
    path = (base_dir / rel_path).resolve()
    try:
        path.relative_to(base_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"Manifest path escapes bundle directory: {rel_path}") from exc
    return path


def load_bundle(root, config):
    manifest_path = root / str(config.get("bundle_manifest", DEFAULT_MANIFEST_PATH))
    if not manifest_path.exists():
        return None, [f"Local AI bundle manifest is missing: {manifest_path}"]
    try:
        manifest = read_json(manifest_path)
    except ValueError as exc:
        return None, [str(exc)]
    if int(manifest.get("schema_version", 0)) != 1:
        return None, ["Local AI bundle manifest schema_version must be 1."]
    file_issues = verify_manifest_files(manifest_path.parent, manifest)
    if file_issues:
        return None, file_issues
    return manifest, []


def verify_manifest_files(bundle_dir, manifest):
    files = manifest.get("files", [])
    if not files:
        return []
    if not isinstance(files, list):
        return ["Local AI bundle manifest files must be an array when present."]
    issues = []
    for entry in files:
        if not isinstance(entry, dict):
            issues.append("Local AI bundle manifest files entries must be objects.")
            continue
        rel_path = str(entry.get("path", ""))
        try:
            path = resolve_asset(bundle_dir, rel_path)
        except ValueError as exc:
            issues.append(str(exc))
            continue
        if not path.exists():
            issues.append(f"Local AI bundle file is missing: {path}")
            continue
        expected_hash = str(entry.get("sha256", "")).lower()
        if expected_hash and sha256_file(path) != expected_hash:
            issues.append(f"Local AI bundle file hash mismatch for {path}.")
    return issues


def select_model(
    root, config, manifest
):
    manifest_path = root / str(config.get("bundle_manifest", DEFAULT_MANIFEST_PATH))
    bundle_dir = manifest_path.parent
    profile_order = normalize_profile_list(config.get("profile_order", []))
    if not profile_order:
        profile_order = [
            normalized_profile_name(
                str(config.get("selected_profile", DEFAULT_MODEL_PROFILE))
            )
        ]
    models = manifest.get("models", [])
    if not isinstance(models, list):
        return None, ["Local AI bundle manifest models must be an array."]
    issues = []
    available_profiles = []
    for wanted_profile in profile_order:
        matched = False
        for model in models:
            if not isinstance(model, dict):
                continue
            model_profile = normalized_profile_name(str(model.get("profile", "")).strip())
            aliases = normalize_profile_list(model.get("aliases", []))
            available_profiles.append(model_profile)
            if wanted_profile != model_profile and wanted_profile not in aliases:
                continue
            matched = True
            license_name = str(model.get("license", "")).strip().lower()
            if license_name and license_name not in COMMERCIAL_OK_LICENSES:
                return None, [
                    f"Local AI model profile {wanted_profile!r} uses unsupported license {license_name!r}."
                ]
            catalog_ok, catalog_issue = manifest_model_matches_catalog(model, config, model_profile)
            if not catalog_ok:
                issues.append(catalog_issue)
                break
            rel_path = str(model.get("path", ""))
            try:
                path = resolve_asset(bundle_dir, rel_path)
            except ValueError as exc:
                return None, [str(exc)]
            if not path.exists():
                issues.append(f"Local AI model is missing: {path}")
                break
            expected_hash = str(model.get("sha256", "")).lower()
            actual_hash = sha256_file(path)
            if expected_hash and expected_hash != actual_hash:
                return None, [f"Local AI model hash mismatch for {path}."]
            selected = dict(model)
            selected["profile"] = model_profile
            selected["resolved_path"] = str(path)
            selected["actual_sha256"] = actual_hash
            selected["aliases"] = list(dict.fromkeys(aliases + [str(model.get("profile", ""))]))
            apply_profile_to_config(config, model_profile)
            config["_selected_model"] = dict(selected)
            config["selected_model_profile"] = model_profile
            config["selected_model_sha256"] = actual_hash
            return selected, []
        if not matched:
            catalog_model, catalog_issues = select_catalog_model(root, config, bundle_dir, wanted_profile)
            if catalog_model is not None:
                apply_profile_to_config(config, wanted_profile)
                config["_selected_model"] = dict(catalog_model)
                config["selected_model_profile"] = wanted_profile
                config["selected_model_sha256"] = catalog_model.get("actual_sha256", "")
                return catalog_model, []
            issues.extend(catalog_issues)
            continue
    if issues:
        return None, issues
    available = ", ".join(sorted(set(available_profiles))) or "none"
    wanted = ", ".join(profile_order)
    return None, [f"Local AI model profiles [{wanted}] are not in the bundle manifest (available: {available})."]


def select_catalog_model(root, config, bundle_dir, profile):
    catalog = config.get("model_catalog", {})
    if not isinstance(catalog, dict):
        return None, []
    expected = catalog.get(profile)
    if not isinstance(expected, dict):
        return None, []
    license_name = str(expected.get("license", "")).strip().lower()
    if license_name and license_name not in COMMERCIAL_OK_LICENSES:
        return None, [f"Local AI model profile {profile!r} uses unsupported license {license_name!r}."]
    rel_path = str(expected.get("path", "")).strip()
    if not rel_path:
        source_url = str(expected.get("source_url", "")).strip()
        filename = urllib.parse.unquote(Path(urllib.parse.urlparse(source_url).path).name)
        if not filename:
            return None, [f"Local AI model profile {profile!r} has no manifest path or source_url filename."]
        rel_path = f"models/{filename}"
    try:
        path = resolve_asset(bundle_dir, rel_path)
    except ValueError as exc:
        return None, [str(exc)]
    if not path.exists():
        return None, [f"Local AI model is missing: {path}"]
    actual_hash = sha256_file(path)
    selected = {
        "profile": profile,
        "aliases": normalize_profile_list(expected.get("aliases", [])) + [profile],
        "base_model": str(expected.get("base_model", "")),
        "kind": str(expected.get("kind", "text")),
        "license": str(expected.get("license", "")),
        "path": rel_path,
        "quant": str(expected.get("quant", "")),
        "resolved_path": str(path),
        "actual_sha256": actual_hash,
        "sha256": str(expected.get("sha256", "")),
        "source": str(expected.get("source", "")),
        "source_url": str(expected.get("source_url", "")),
        "tier": str(expected.get("tier", "optional")),
    }
    return selected, []


def manifest_model_matches_catalog(
    model, config, profile
):
    catalog = config.get("model_catalog", {})
    if not isinstance(catalog, dict):
        return True, ""
    expected = catalog.get(profile)
    if not isinstance(expected, dict):
        return True, ""
    for key in ("base_model", "quant"):
        expected_value = str(expected.get(key, "")).strip()
        actual_value = str(model.get(key, "")).strip()
        if expected_value and actual_value and expected_value != actual_value:
            return (
                False,
                f"Local AI model profile {profile!r} is stale in the bundle manifest "
                f"({key} is {actual_value!r}, expected {expected_value!r}).",
            )
    return True, ""


def resolve_local_runtime_path(root, raw_path):
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = root / path
    return str(path.resolve())


def local_runtime_overrides(root, config):
    current_platform = platform_id()
    overrides = config.get("runtime_overrides", [])
    if not isinstance(overrides, list):
        return []
    normalized = []
    for override in overrides:
        if not isinstance(override, dict):
            continue
        backend = str(override.get("backend", "")).strip().lower()
        if backend != "cpu" and backend not in GPU_BACKENDS:
            continue
        runtime_platform = str(override.get("platform", current_platform)).strip().lower()
        if runtime_platform != current_platform:
            continue
        raw_path = str(override.get("path", "")).strip()
        if not raw_path:
            continue
        selected = dict(override)
        selected["backend"] = backend
        selected["platform"] = runtime_platform
        selected["resolved_path"] = resolve_local_runtime_path(root, raw_path)
        raw_server_path = str(override.get("server_path", "")).strip()
        if raw_server_path:
            selected["server_resolved_path"] = resolve_local_runtime_path(root, raw_server_path)
        else:
            selected["server_resolved_path"] = str(Path(selected["resolved_path"]).with_name("llama-server.exe"))
        normalized.append(selected)
    return normalized


def runtime_candidates(
    root, config, manifest
):
    manifest_path = root / str(config.get("bundle_manifest", DEFAULT_MANIFEST_PATH))
    bundle_dir = manifest_path.parent
    runtimes = manifest.get("runtimes", [])
    if not isinstance(runtimes, list):
        return []
    current_platform = platform_id()
    order = list(config.get("backend_order", ["cpu"]))
    runtime_pool = local_runtime_overrides(root, config)
    runtime_pool.extend(runtime for runtime in runtimes if isinstance(runtime, dict))
    ordered = []
    for backend in order:
        for runtime in runtime_pool:
            if str(runtime.get("backend", "")).lower() != backend:
                continue
            runtime_platform = str(runtime.get("platform", current_platform)).lower()
            if runtime_platform != current_platform:
                continue
            selected = dict(runtime)
            selected["backend"] = backend
            if "resolved_path" not in selected:
                rel_path = str(runtime.get("path", ""))
                try:
                    selected["resolved_path"] = str(resolve_asset(bundle_dir, rel_path))
                except ValueError:
                    selected["resolved_path"] = ""
            if "server_resolved_path" not in selected:
                server_path = str(runtime.get("server_path", ""))
                if server_path:
                    try:
                        selected["server_resolved_path"] = str(resolve_asset(bundle_dir, server_path))
                    except ValueError:
                        selected["server_resolved_path"] = ""
                else:
                    selected["server_resolved_path"] = str(Path(selected["resolved_path"]).with_name("llama-server.exe"))
            ordered.append(selected)
    return ordered


def refresh_local_gpu_config(root, config):
    settings = read_local_settings(root)
    configured_order = config.get("configured_backend_order", ["auto", "cpu"])
    backend_override = str(config.get("backend_override", "")).strip().lower()
    if backend_override == "cpu":
        settings["gpu"]["mode"] = "off"
        configured_order = ["cpu"]
    elif backend_override in GPU_BACKENDS:
        settings["gpu"]["mode"] = "force"
        settings["gpu"]["allow_integrated"] = True
        settings["gpu"]["preferred_backends"] = [backend_override, "cpu"]
        configured_order = [backend_override, "cpu"]
    config["gpu"] = settings.get("gpu", {})
    config["runtime_overrides"] = settings.get("runtime_overrides", [])
    config["local_settings_issues"] = settings.get("issues", [])
    config["backend_order"] = expanded_backend_order(root, configured_order, settings)
    config["configured_backend_order"] = configured_order


def missing_gpu_runtime_backends(
    root,
    config,
    manifest,
):
    wanted = [str(backend).strip().lower() for backend in config.get("backend_order", [])]
    wanted = [backend for backend in wanted if backend in GPU_BACKENDS]
    if not wanted:
        return []
    candidates = runtime_candidates(root, config, manifest)
    present = {
        str(runtime.get("backend", "")).strip().lower()
        for runtime in candidates
        if str(runtime.get("backend", "")).strip().lower() in GPU_BACKENDS
    }
    return [backend for backend in wanted if backend not in present]


def run_gpu_runtime_ensure_command(
    root,
    config,
    backends,
):
    wanted = [backend for backend in backends if backend in GPU_BACKENDS]
    if not wanted:
        return False, []
    script = Path(__file__).resolve().parents[1] / "setup_local_ai.py"
    if not script.exists():
        return False, [f"Local AI setup script is missing: {script}"]
    command = [
        sys.executable,
        "-B",
        str(script),
        "--root",
        str(root),
        "runtime",
        "ensure-gpu",
        "--json",
        "--probe",
    ]
    for backend in wanted:
        command.extend(["--backend", backend])
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=max(120, int(config.get("gpu", {}).get("probe_timeout_seconds", 5)) + 600),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, [f"Local AI GPU runtime setup failed to start: {exc}"]
    output = completed.stdout.strip()
    if not output:
        return False, [f"Local AI GPU runtime setup exited with {completed.returncode} and no output."]
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return False, [line for line in output.splitlines() if line.strip()]
    issues = [str(issue) for issue in payload.get("issues", []) if str(issue).strip()] if isinstance(payload, dict) else []
    if completed.returncode == 0 and isinstance(payload, dict) and bool(payload.get("ok", False)):
        return True, []
    if not issues and isinstance(payload, dict):
        issues.append(str(payload.get("reason") or "Local AI GPU runtime setup did not complete."))
    stderr = completed.stderr.strip()
    if stderr:
        issues.append(stderr[-1200:])
    return False, issues


def maybe_auto_ensure_gpu_runtime(
    root,
    config,
    manifest,
    *,
    check_only,
):
    if check_only:
        return []
    gpu = config.get("gpu") if isinstance(config.get("gpu"), dict) else {}
    if not bool(gpu.get("auto_download_runtime", True)):
        return []
    if str(gpu.get("mode", "auto")).strip().lower() == "off":
        return []
    missing_backends = missing_gpu_runtime_backends(root, config, manifest)
    if not missing_backends:
        return []
    ok, issues = run_gpu_runtime_ensure_command(root, config, missing_backends)
    refresh_local_gpu_config(root, config)
    if ok:
        return []
    reason = "; ".join(issues) or "GPU runtime acquisition failed."
    disable_gpu_in_local_settings(root, reason)
    refresh_local_gpu_config(root, config)
    return issues


def gpu_runtime_allowed(config):
    return local_gpu_enabled({"gpu": config.get("gpu", {})})


def probe_runtime(
    runtime,
    config,
    *,
    check_only=False,
):
    path = Path(str(runtime.get("resolved_path", "")))
    if not path.exists():
        return False, f"Local AI runtime is missing: {path}"
    expected_hash = str(runtime.get("sha256", "")).lower()
    actual_hash = sha256_file(path)
    if expected_hash and expected_hash != actual_hash:
        return False, f"Local AI runtime hash mismatch for {path}."
    runtime["actual_sha256"] = actual_hash
    backend = str(runtime.get("backend", "cpu")).lower()
    if backend == "cpu":
        return True, ""
    if not gpu_runtime_allowed(config):
        return False, f"Local AI {backend} runtime is disabled by {LOCAL_SETTINGS_RELATIVE_PATH}."
    if check_only:
        return True, ""
    command = [str(path), "--list-devices"]
    gpu = config.get("gpu") if isinstance(config.get("gpu"), dict) else {}
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=int(gpu.get("probe_timeout_seconds", 5)),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Local AI {backend} probe failed: {exc}"
    output = completed.stdout.lower()
    if completed.returncode == 0 and ("vulkan" in output or "device" in output) and "no device" not in output:
        return True, ""
    return False, f"Local AI {backend} runtime did not report a usable device."


def measure_runtime_sample(
    root,
    runtime,
    model,
    config,
    *,
    timeout_seconds,
):
    sample_config = json.loads(json.dumps({key: value for key, value in config.items() if not key.startswith("_")}))
    sample_config["gpu"] = dict(config.get("gpu", {})) if isinstance(config.get("gpu"), dict) else {}
    sample_config["limits"] = dict(config.get("limits", DEFAULT_LIMITS))
    sample_config["limits"]["output_tokens"] = min(int(sample_config["limits"].get("output_tokens", 16)), 8)
    sample_config["limits"]["context_tokens"] = min(int(sample_config["limits"].get("context_tokens", 1024)), 1024)
    sample_config["limits"]["batch_size"] = min(int(sample_config["limits"].get("batch_size", 256)), 256)
    sample_config["limits"]["ubatch_size"] = min(int(sample_config["limits"].get("ubatch_size", 128)), 128)
    sample_config["limits"]["timeout_seconds"] = timeout_seconds
    prompt_file = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
            handle.write("Return exactly this word: ok\n")
            prompt_file = Path(handle.name)
        command = llama_command(runtime, model, sample_config, prompt_file)
        started = time.perf_counter()
        completed = subprocess.run(
            command,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "backend": runtime.get("backend", ""),
            "elapsed_ms": 0.0,
            "issue": str(exc),
            "output": "",
        }
    finally:
        if prompt_file is not None:
            prompt_file.unlink(missing_ok=True)
    output = completed.stdout or ""
    ok = completed.returncode == 0
    return {
        "ok": ok,
        "backend": runtime.get("backend", ""),
        "elapsed_ms": elapsed_ms,
        "returncode": completed.returncode,
        "issue": "" if ok else (output.strip()[-1000:] or f"process exited with {completed.returncode}"),
        "output": output[-2000:],
    }


def runtime_workload_smoke(
    root,
    runtime,
    model,
    config,
):
    gpu = config.get("gpu") if isinstance(config.get("gpu"), dict) else {}
    timeout_seconds = int(gpu.get("smoke_timeout_seconds", 90))
    sample = measure_runtime_sample(root, runtime, model, config, timeout_seconds=timeout_seconds)
    if bool(sample.get("ok")):
        return True, ""
    issue = str(sample.get("issue", "")).strip() or "GPU workload smoke test failed."
    return False, issue


def cpu_runtime_for_calibration(
    root,
    config,
    manifest,
):
    cpu_config = dict(config)
    cpu_config["backend_order"] = ["cpu"]
    for candidate in runtime_candidates(root, cpu_config, manifest):
        if str(candidate.get("backend", "")).strip().lower() != "cpu":
            continue
        ok, _issue = probe_runtime(candidate, cpu_config, check_only=True)
        if ok:
            return candidate
    return None


def evaluate_cached_backend_decision(
    root,
    config,
    runtime,
    model,
):
    backend = str(runtime.get("backend", "")).strip().lower()
    profile = selected_profile(config)
    model_hash = str((model or {}).get("actual_sha256") or selected_model_sha(config)).strip().lower()
    runtime_hash = runtime_sha(runtime)
    settings = read_local_settings(root)
    device_fingerprint = current_device_fingerprint(backend)
    quarantine = backend_quarantine_reason(
        settings,
        profile=profile,
        backend=backend,
        runtime_hash=runtime_hash,
        model_hash=model_hash,
        device_fingerprint=device_fingerprint,
    )
    if quarantine:
        return "cpu", quarantine
    calibration = calibration_decision(
        settings,
        profile=profile,
        backend=backend,
        runtime_hash=runtime_hash,
        model_hash=model_hash,
        device_fingerprint=device_fingerprint,
    )
    if calibration and str(calibration.get("decision", "")).strip().lower() == "cpu":
        return "cpu", str(calibration.get("reason", "GPU calibration selected CPU.")).strip()
    if calibration and str(calibration.get("decision", "")).strip().lower() == "gpu":
        return "gpu", str(calibration.get("reason", "GPU calibration selected this backend.")).strip()
    return "", ""


def experimental_workload_guard_issue(config, backend):
    normalized_backend = str(backend).strip().lower()
    if normalized_backend not in EXPERIMENTAL_WORKLOAD_BACKENDS:
        return ""
    if str(config.get("backend_override", "")).strip().lower() == normalized_backend:
        return ""
    gpu = config.get("gpu") if isinstance(config.get("gpu"), dict) else {}
    if str(gpu.get("mode", "auto")).strip().lower() != "auto":
        return ""
    if bool(gpu.get("allow_experimental_workloads", False)):
        return ""
    experimental_backends = {
        str(item).strip().lower()
        for item in gpu.get("experimental_backends", [])
        if str(item).strip().lower() in EXPERIMENTAL_WORKLOAD_BACKENDS
    }
    if normalized_backend in experimental_backends:
        return ""
    return (
        f"{normalized_backend} experimental workload trials are skipped in auto mode. "
        "Set gpu.allow_experimental_workloads=true or add the backend to gpu.experimental_backends "
        f"in {LOCAL_SETTINGS_RELATIVE_PATH} to run model smoke tests or calibration for this backend."
    )


def maybe_calibrate_gpu_runtime(
    root,
    config,
    manifest,
    runtime,
    model,
):
    gpu = config.get("gpu") if isinstance(config.get("gpu"), dict) else {}
    if not bool(gpu.get("auto_calibrate", True)):
        return None
    backend = str(runtime.get("backend", "")).strip().lower()
    profile = str(model.get("profile", selected_profile(config))).strip()
    runtime_hash = runtime_sha(runtime)
    model_hash = str(model.get("actual_sha256", "")).strip().lower()
    device_fingerprint = current_device_fingerprint(backend)
    settings = read_local_settings(root)
    existing = calibration_decision(
        settings,
        profile=profile,
        backend=backend,
        runtime_hash=runtime_hash,
        model_hash=model_hash,
        device_fingerprint=device_fingerprint,
    )
    if existing is not None:
        return existing
    cpu_runtime = cpu_runtime_for_calibration(root, config, manifest)
    if cpu_runtime is None:
        return None
    timeout_seconds = int(gpu.get("smoke_timeout_seconds", 90))
    cpu_sample = measure_runtime_sample(root, cpu_runtime, model, config, timeout_seconds=timeout_seconds)
    if not bool(cpu_sample.get("ok")):
        return None
    gpu_sample = measure_runtime_sample(root, runtime, model, config, timeout_seconds=timeout_seconds)
    if not bool(gpu_sample.get("ok")):
        record_backend_quarantine(
            root,
            profile=profile,
            backend=backend,
            runtime=runtime,
            model=model,
            reason=str(gpu_sample.get("issue", "GPU calibration sample failed.")),
        )
        return {
            "profile": profile,
            "backend": backend,
            "decision": "cpu",
            "reason": str(gpu_sample.get("issue", "GPU calibration sample failed.")),
        }
    return record_backend_calibration(
        root,
        profile=profile,
        backend=backend,
        runtime=runtime,
        model=model,
        cpu_ms=float(cpu_sample.get("elapsed_ms", 0.0) or 0.0),
        gpu_ms=float(gpu_sample.get("elapsed_ms", 0.0) or 0.0),
        threshold_percent=float(gpu.get("performance_threshold_percent", 10.0) or 10.0),
    )


def select_runtime(
    root,
    config,
    manifest,
    *,
    check_only=False,
):
    ensure_issues = maybe_auto_ensure_gpu_runtime(root, config, manifest, check_only=check_only)
    issues = list(ensure_issues)
    gpu_issues = []
    model = selected_model_from_config(config)
    config["backend_decision"] = {
        "selected": "",
        "profile": selected_profile(config),
        "reason": "",
        "source": "runtime-selection",
    }
    for runtime in runtime_candidates(root, config, manifest):
        backend = str(runtime.get("backend", "")).strip().lower()
        ok, issue = probe_runtime(runtime, config, check_only=check_only)
        if ok:
            if backend in GPU_BACKENDS:
                decision, reason = evaluate_cached_backend_decision(root, config, runtime, model)
                if decision == "cpu":
                    config["backend_decision"] = {
                        "selected": "cpu",
                        "profile": selected_profile(config),
                        "backend": backend,
                        "reason": reason,
                        "source": "local-calibration-or-quarantine",
                    }
                    if reason:
                        issues.append(reason)
                    continue
                if not check_only and model is not None:
                    guard_issue = experimental_workload_guard_issue(config, backend)
                    if guard_issue:
                        record_backend_quarantine(
                            root,
                            profile=str(model.get("profile", selected_profile(config))),
                            backend=backend,
                            runtime=runtime,
                            model=model,
                            reason=guard_issue,
                        )
                        config["backend_decision"] = {
                            "selected": "cpu",
                            "profile": selected_profile(config),
                            "backend": backend,
                            "reason": guard_issue,
                            "source": "experimental-workload-guard",
                        }
                        issues.append(guard_issue)
                        continue
                if not check_only and model is not None and bool(config.get("gpu", {}).get("smoke_test_runtime", True)):
                    smoke_ok, smoke_issue = runtime_workload_smoke(root, runtime, model, config)
                    if not smoke_ok:
                        record_backend_quarantine(
                            root,
                            profile=str(model.get("profile", selected_profile(config))),
                            backend=backend,
                            runtime=runtime,
                            model=model,
                            reason=smoke_issue,
                        )
                        config["backend_decision"] = {
                            "selected": "cpu",
                            "profile": selected_profile(config),
                            "backend": backend,
                            "reason": smoke_issue,
                            "source": "workload-smoke-quarantine",
                        }
                        issues.append(smoke_issue)
                        continue
                if not check_only and model is not None:
                    calibration = maybe_calibrate_gpu_runtime(root, config, manifest, runtime, model)
                    if calibration and str(calibration.get("decision", "")).strip().lower() == "cpu":
                        reason = str(calibration.get("reason", "GPU calibration selected CPU.")).strip()
                        config["backend_decision"] = {
                            "selected": "cpu",
                            "profile": selected_profile(config),
                            "backend": backend,
                            "reason": reason,
                            "source": "local-calibration",
                        }
                        if reason:
                            issues.append(reason)
                        continue
                    if calibration and str(calibration.get("decision", "")).strip().lower() == "gpu":
                        config["backend_decision"] = {
                            "selected": backend,
                            "profile": selected_profile(config),
                            "backend": backend,
                            "reason": str(calibration.get("reason", "")).strip(),
                            "source": "local-calibration",
                        }
                if not config.get("backend_decision", {}).get("selected"):
                    config["backend_decision"] = {
                        "selected": backend,
                        "profile": selected_profile(config),
                        "backend": backend,
                        "reason": "GPU backend passed runtime probes.",
                        "source": "runtime-selection",
                    }
            else:
                if not config.get("backend_decision", {}).get("selected"):
                    reason = "; ".join(gpu_issues) if gpu_issues else "CPU backend selected."
                    config["backend_decision"] = {
                        "selected": "cpu",
                        "profile": selected_profile(config),
                        "reason": reason,
                        "source": "runtime-selection",
                    }
            return runtime, []
        if issue:
            issues.append(issue)
            if backend in GPU_BACKENDS:
                gpu_issues.append(issue)
                if not check_only:
                    record_backend_quarantine(
                        root,
                        profile=selected_profile(config),
                        backend=backend,
                        runtime=runtime,
                        model=model,
                        reason=issue,
                    )
    if not issues:
        issues.append("No compatible Local AI runtime is listed in the bundle manifest.")
    return None, issues


def iter_json_objects(text):
    objects = []
    for start in [match.start() for match in re.finditer(r"\{", text)]:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : index + 1]
                    try:
                        json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    objects.append(candidate)
                    break
    return objects


def find_json_object(text):
    objects = iter_json_objects(text)
    if not objects:
        raise ValueError(NO_JSON_OBJECT)
    return objects[0]


def validate_model_json(
    text,
    *,
    task,
    allowed_categories,
    confidence_threshold,
    max_text_chars=180,
):
    candidates = iter_json_objects(text)
    if not candidates:
        return False, {}, NO_JSON_OBJECT
    last_reason = "model output did not contain an acceptable routing object"
    for candidate in candidates:
        parsed = json.loads(candidate)
        if not isinstance(parsed, dict):
            last_reason = "model output JSON must be an object"
            continue
        try:
            confidence = float(parsed.get("confidence", 0))
        except (TypeError, ValueError):
            last_reason = "confidence must be numeric"
            continue
        if confidence < confidence_threshold:
            last_reason = "confidence below configured threshold"
            continue

        fields = {}
        if task == "skill-routing":
            category = str(parsed.get("category", "")).strip()
            if category not in allowed_categories:
                last_reason = f"category {category!r} is not allowlisted"
                continue
            use_when = " ".join(str(parsed.get("use_when", "")).split())
            if not use_when:
                last_reason = "use_when is required"
                continue
            if len(use_when) > max_text_chars:
                last_reason = "use_when exceeds configured length limit"
                continue
            fields["category"] = category
            fields["use_when"] = use_when
        elif task == "workflow-routing":
            summary = " ".join(str(parsed.get("summary", parsed.get("use_when", ""))).split())
            if not summary:
                last_reason = "summary is required"
                continue
            if len(summary) > max_text_chars:
                last_reason = "summary exceeds configured length limit"
                continue
            fields["summary"] = summary
        else:
            return False, {}, f"{UNSUPPORTED_LOCAL_AI_TASK}{task!r}"

        return True, fields, ""
    return False, {}, last_reason


def validate_cache_entry(
    entry,
    *,
    item,
    task,
    model,
    runtime,
    allowed_categories,
    confidence_threshold,
    max_text_chars,
):
    current, reason = cache_metadata_matches(entry, item=item, task=task, model=model, runtime=runtime)
    if not current:
        return False, {}, reason

    fields = entry.get("fields", {})
    cache_text = json.dumps(
        {
            **fields,
            "confidence": entry.get("confidence", 0),
        },
        ensure_ascii=False,
    )
    accepted, validated_fields, reason = validate_model_json(
        cache_text,
        task=task,
        allowed_categories=allowed_categories,
        confidence_threshold=confidence_threshold,
        max_text_chars=max_text_chars,
    )
    if not accepted:
        return False, {}, reason
    return True, validated_fields, ""


def cache_metadata_matches(
    entry,
    *,
    item,
    task,
    model,
    runtime,
):
    if int(entry.get("schema_version", 0)) != 1:
        return False, "cache schema_version must be 1"
    if entry.get("task") != task or entry.get("item_id") != item.get("id"):
        return False, "cache task or item_id does not match"
    if entry.get("prompt_version") != PROMPT_VERSION:
        return False, "cache prompt_version is stale"
    if entry.get("input_hash") != input_hash(item):
        return False, "cache input_hash is stale"
    if not cache_model_profile_matches(str(entry.get("model_profile", "")), model):
        return False, "cache model_profile does not match"
    if str(entry.get("model_sha256", "")).lower() != str(model.get("actual_sha256", "")).lower():
        return False, "cache model_sha256 does not match"
    if str(entry.get("runtime_backend", "")).lower() != str(runtime.get("backend", "")).lower():
        return False, "cache runtime_backend does not match"
    if str(entry.get("runtime_sha256", "")).lower() != str(runtime.get("actual_sha256", "")).lower():
        return False, "cache runtime_sha256 does not match"
    return True, ""


def cache_model_profile_matches(cache_profile, model):
    normalized_cache = normalized_profile_name(cache_profile)
    model_profile = normalized_profile_name(str(model.get("profile", "")))
    aliases = normalize_profile_list(model.get("aliases", []))
    return normalized_cache == model_profile or normalized_cache in aliases or cache_profile in aliases


def read_valid_cache(
    root,
    config,
    *,
    item,
    task,
    model,
    runtime,
    allowed_categories,
):
    path = cache_path(root, task, str(item.get("id", "")), str(config.get("cache_dir", DEFAULT_CACHE_DIR)))
    if not path.exists():
        return None, "cache entry is missing"
    try:
        entry = read_json(path)
    except ValueError as exc:
        return None, str(exc)
    if entry.get("accepted") is False:
        current, reason = cache_metadata_matches(
            entry,
            item=item,
            task=task,
            model=model,
            runtime=runtime,
        )
        if current:
            return {CACHED_REJECTION: True}, str(entry.get("reason", "cached rejection"))
        return None, reason
    limits = config.get("limits", DEFAULT_LIMITS)
    accepted, fields, reason = validate_cache_entry(
        entry,
        item=item,
        task=task,
        model=model,
        runtime=runtime,
        allowed_categories=allowed_categories,
        confidence_threshold=float(limits.get("confidence_threshold", 0.7)),
        max_text_chars=int(limits.get("max_text_chars", DEFAULT_LIMITS["max_text_chars"])),
    )
    if not accepted:
        return None, reason
    return fields, ""


def write_cache_entry(
    root,
    config,
    *,
    task,
    item,
    model,
    runtime,
    confidence,
    fields,
):
    path = cache_path(root, task, str(item.get("id", "")), str(config.get("cache_dir", DEFAULT_CACHE_DIR)))
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "schema_version": 1,
        "task": task,
        "item_id": item.get("id", ""),
        "prompt_version": PROMPT_VERSION,
        "input_hash": input_hash(item),
        "model_profile": model.get("profile", ""),
        "model_sha256": model.get("actual_sha256", ""),
        "runtime_backend": runtime.get("backend", ""),
        "runtime_sha256": runtime.get("actual_sha256", ""),
        "confidence": confidence,
        "accepted": True,
        "fields": fields,
        "created_at_unix": int(time.time()),
    }
    path.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_rejection_cache(
    root,
    config,
    *,
    task,
    item,
    model,
    runtime,
    reason,
):
    path = cache_path(root, task, str(item.get("id", "")), str(config.get("cache_dir", DEFAULT_CACHE_DIR)))
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "schema_version": 1,
        "task": task,
        "item_id": item.get("id", ""),
        "prompt_version": PROMPT_VERSION,
        "input_hash": input_hash(item),
        "model_profile": model.get("profile", ""),
        "model_sha256": model.get("actual_sha256", ""),
        "runtime_backend": runtime.get("backend", ""),
        "runtime_sha256": runtime.get("actual_sha256", ""),
        "accepted": False,
        "reason": reason,
        "created_at_unix": int(time.time()),
    }
    path.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def prompt_for_item(task, item, allowed_categories):
    base = {
        "task": task,
        "item": {
            "id": item.get("id", ""),
            "name": item.get("name", ""),
            "category": item.get("category", ""),
            "description": item.get("description", ""),
            "summary": item.get("summary", ""),
            "related_skills": item.get("related_skills", []),
            "scripts": item.get("scripts", []),
            "outputs": item.get("outputs", []),
        },
    }
    if task == "skill-routing":
        return (
            "You classify repository assistant skills for a routing table.\n"
            f"Allowed categories: {', '.join(allowed_categories)}.\n"
            f"{JSON_PROMPT_PREFIX}category, use_when, confidence. No markdown.\n"
            f"{JSON_CONFIDENCE_RULE}\n"
            "Use the input category when it is listed in the allowed categories.\n"
            "use_when must be a route phrase under 120 characters. Prefer verbs. Do not start with 'When'.\n"
            f"Input: {stable_json(base)}\n"
        )
    return (
        "You summarize repository workflow modules for a routing table.\n"
        f"{JSON_PROMPT_PREFIX}summary, confidence. No markdown.\n"
        f"{JSON_CONFIDENCE_RULE}\n"
        "summary must be under 120 characters and describe when to use the workflow.\n"
        f"Input: {stable_json(base)}\n"
    )


def routing_json_schema(task, allowed_categories):
    if task == "skill-routing":
        return {
            "type": "object",
            "required": ["category", "use_when", "confidence"],
            "properties": {
                "category": {"type": "string", "enum": allowed_categories},
                "use_when": {"type": "string", "maxLength": 120},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "additionalProperties": False,
        }
    if task == "workflow-routing":
        return {
            "type": "object",
            "required": ["summary", "confidence"],
            "properties": {
                "summary": {"type": "string", "maxLength": 120},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "additionalProperties": False,
        }
    raise ValueError(f"{UNSUPPORTED_LOCAL_AI_TASK}{task!r}")


def llama_command(
    runtime,
    model,
    config,
    prompt_path,
):
    limits = config.get("limits", DEFAULT_LIMITS)
    runtime_path = Path(str(runtime.get("completion_resolved_path") or runtime.get("resolved_path", "")))
    if runtime_path.name == "llama-cli.exe":
        completion_path = runtime_path.with_name("llama-completion.exe")
        if completion_path.is_file():
            runtime_path = completion_path
    command = [
        str(runtime_path),
        "-m",
        str(model.get("resolved_path", "")),
        "-f",
        str(prompt_path),
        "--threads",
        str(int(limits.get("threads", DEFAULT_LIMITS["threads"]))),
        "-tb",
        str(int(limits.get("threads_batch", DEFAULT_LIMITS["threads_batch"]))),
        "-c",
        str(int(limits.get("context_tokens", DEFAULT_LIMITS["context_tokens"]))),
        "-b",
        str(int(limits.get("batch_size", DEFAULT_LIMITS["batch_size"]))),
        "-ub",
        str(int(limits.get("ubatch_size", DEFAULT_LIMITS["ubatch_size"]))),
        "-n",
        str(int(limits.get("output_tokens", DEFAULT_LIMITS["output_tokens"]))),
        "--temp",
        "0",
        "--top-p",
        "1",
        "--log-disable",
        "--no-perf",
        "--log-colors",
        "off",
        "--color",
        "off",
        "--no-display-prompt",
        "-no-cnv",
        "--skip-chat-parsing",
        "--single-turn",
        "--simple-io",
        "--no-warmup",
    ]
    cache_type_k = str(limits.get("cache_type_k", "")).strip().lower()
    cache_type_v = str(limits.get("cache_type_v", "")).strip().lower()
    if cache_type_k:
        command.extend(["-ctk", cache_type_k])
    if cache_type_v:
        command.extend(["-ctv", cache_type_v])
    reasoning = str(limits.get("reasoning", "")).strip().lower()
    if reasoning:
        command.extend(["--reasoning", reasoning])
    backend = str(runtime.get("backend", "")).lower()
    gpu = config.get("gpu") if isinstance(config.get("gpu"), dict) else {}
    if backend in GPU_BACKENDS and gpu_runtime_allowed(config):
        command.extend(["-ngl", str(int(gpu.get("gpu_layers", 99)))])
    return command


def llama_server_command(
    runtime,
    model,
    config,
):
    limits = config.get("limits", DEFAULT_LIMITS)
    server = config.get("server", DEFAULT_SERVER_CONFIG)
    server_path = str(runtime.get("server_resolved_path", "")).strip()
    if not server_path:
        server_path = str(Path(str(runtime.get("resolved_path", ""))).with_name("llama-server.exe"))
    command = [
        server_path,
        "-m",
        str(model.get("resolved_path", "")),
        "--host",
        str(server.get("host", DEFAULT_SERVER_CONFIG["host"])),
        "--port",
        str(int(server.get("port", DEFAULT_SERVER_CONFIG["port"]))),
        "--alias",
        str(model.get("profile", config.get("selected_profile", DEFAULT_MODEL_PROFILE))),
        "-c",
        str(int(limits.get("context_tokens", DEFAULT_LIMITS["context_tokens"]))),
        "-b",
        str(int(limits.get("batch_size", DEFAULT_LIMITS["batch_size"]))),
        "-ub",
        str(int(limits.get("ubatch_size", DEFAULT_LIMITS["ubatch_size"]))),
        "-t",
        str(int(limits.get("threads", DEFAULT_LIMITS["threads"]))),
        "-tb",
        str(int(limits.get("threads_batch", DEFAULT_LIMITS["threads_batch"]))),
        "-np",
        str(int(server.get("parallel_slots", 1))),
        "--temp",
        "0",
    ]
    cache_type_k = str(limits.get("cache_type_k", "")).strip().lower()
    cache_type_v = str(limits.get("cache_type_v", "")).strip().lower()
    if cache_type_k:
        command.extend(["-ctk", cache_type_k])
    if cache_type_v:
        command.extend(["-ctv", cache_type_v])
    reasoning = str(limits.get("reasoning", "")).strip().lower()
    if reasoning:
        command.extend(["--reasoning", reasoning])
    if bool(server.get("cache_prompt", True)):
        command.append("--cache-prompt")
    if bool(server.get("mlock", False)):
        command.append("--mlock")
    backend = str(runtime.get("backend", "")).lower()
    gpu = config.get("gpu") if isinstance(config.get("gpu"), dict) else {}
    if backend in GPU_BACKENDS and gpu_runtime_allowed(config):
        command.extend(["-ngl", str(int(gpu.get("gpu_layers", 99)))])
    return command


def find_free_local_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def validate_model_output(
    output,
    *,
    task,
    allowed_categories,
    config,
):
    limits = config.get("limits", DEFAULT_LIMITS)
    accepted, fields, reason = validate_model_json(
        output,
        task=task,
        allowed_categories=allowed_categories,
        confidence_threshold=float(limits.get("confidence_threshold", 0.7)),
        max_text_chars=int(limits.get("max_text_chars", DEFAULT_LIMITS["max_text_chars"])),
    )
    if not accepted:
        return False, {}, 0.0, reason
    confidence = float(limits.get("confidence_threshold", 0.7))
    for candidate in iter_json_objects(output):
        parsed = json.loads(candidate)
        if isinstance(parsed, dict) and all(parsed.get(key) == value for key, value in fields.items()):
            try:
                confidence = float(parsed.get("confidence", confidence))
            except (TypeError, ValueError):
                confidence = float(limits.get("confidence_threshold", 0.7))
            break
    return True, fields, confidence, ""


def wait_for_llama_server(host, port, *, timeout_seconds):
    deadline = time.time() + timeout_seconds
    url = f"http://{host}:{port}/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    return False


def llama_server_completion(
    *,
    host,
    port,
    prompt,
    config,
):
    limits = config.get("limits", DEFAULT_LIMITS)
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(limits.get("output_tokens", DEFAULT_LIMITS["output_tokens"])),
        "temperature": 0,
        "top_p": 1,
        "cache_prompt": True,
    }
    request = urllib.request.Request(
        f"http://{host}:{port}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=int(limits.get("timeout_seconds", DEFAULT_LIMITS["timeout_seconds"])),
        ) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return False, f"server completion failed: {exc}"
    if not isinstance(data, dict):
        return False, "server completion response was not a JSON object"
    content = ""
    choices = data.get("choices", [])
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message", {})
            if isinstance(message, dict):
                content = str(message.get("content", ""))
    if not content:
        content = data.get("content", data.get("response", ""))
    if not isinstance(content, str):
        return False, "server completion response did not contain text"
    return True, content


def _run_model_batch_with_server_unleased(
    root,
    *,
    task,
    items,
    allowed_categories,
    model,
    runtime,
    config,
):
    server_path = Path(str(runtime.get("server_resolved_path", "")))
    if not server_path.exists():
        return {
            str(item.get("id", "")): (False, {}, 0.0, f"llama-server.exe is missing: {server_path}")
            for item in items
        }
    server_config = dict(config.get("server", DEFAULT_SERVER_CONFIG))
    host = str(server_config.get("host", "127.0.0.1"))
    port = find_free_local_port()
    server_config["port"] = port
    server_config["parallel_slots"] = 1
    server_config["cache_prompt"] = True
    server_config["mlock"] = False
    server_config["host"] = host
    server_config["auto_started"] = True
    server_config["auto_port"] = port
    server_config["startup_timeout_seconds"] = max(
        int(config.get("limits", DEFAULT_LIMITS).get("timeout_seconds", 20)),
        180,
    )
    server_config["shutdown_after_batch"] = True
    server_config["log_prompt"] = False
    server_config["port"] = port
    server_config["host"] = host
    server_config["parallel_slots"] = 1
    batch_config = dict(config)
    batch_config["server"] = server_config
    command = llama_server_command(runtime, model, batch_config)
    log_dir = root / str(config.get("cache_dir", DEFAULT_CACHE_DIR)) / "server"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"auto-{task}-{int(time.time())}.log"
    results = {}
    log_handle = log_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(command, cwd=root, stdout=log_handle, stderr=subprocess.STDOUT)
    except OSError as exc:
        log_handle.close()
        reason = f"llama-server failed to start: {exc}"
        return {str(item.get("id", "")): (False, {}, 0.0, reason) for item in items}
    try:
        ready = wait_for_llama_server(
            host,
            port,
            timeout_seconds=int(server_config["startup_timeout_seconds"]),
        )
        if not ready:
            reason = f"llama-server did not become ready; see {relative_to_root(root, log_path)}"
            return {str(item.get("id", "")): (False, {}, 0.0, reason) for item in items}
        for item in items:
            prompt = prompt_for_item(task, item, allowed_categories)
            ok, output_or_reason = llama_server_completion(host=host, port=port, prompt=prompt, config=config)
            if not ok:
                results[str(item.get("id", ""))] = (False, {}, 0.0, output_or_reason)
                continue
            results[str(item.get("id", ""))] = validate_model_output(
                output_or_reason,
                task=task,
                allowed_categories=allowed_categories,
                config=config,
            )
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        log_handle.close()
    return results


def run_model_batch_with_server(
    root,
    *,
    task,
    items,
    allowed_categories,
    model,
    runtime,
    config,
):
    profile = str(model.get("profile", config.get("active_profile", task)))
    priority = "validation" if "validation" in str(task) else "interactive"
    with model_lease.exclusive_lease(
        root,
        profile=profile,
        role="text",
        priority=priority,
        command_kind="warm-batch",
        timeout_ms=0,
    ) as lease:
        if not lease.acquired:
            config["lease"] = lease.report()
            return {
                str(item.get("id", "")): (
                    False,
                    {},
                    0.0,
                    "local-ai-busy; deterministic fallback required",
                )
                for item in items
            }
        started = time.perf_counter()
        results = _run_model_batch_with_server_unleased(
            root,
            task=task,
            items=items,
            allowed_categories=allowed_categories,
            model=model,
            runtime=runtime,
            config=config,
        )
        lease.inference_ms = int(max(0.0, time.perf_counter() - started) * 1000)
        config["lease"] = lease.report()
        return results


def _run_model_unleased(
    *,
    task,
    item,
    allowed_categories,
    model,
    runtime,
    config,
):
    prompt = prompt_for_item(task, item, allowed_categories)
    limits = config.get("limits", DEFAULT_LIMITS)
    with tempfile.TemporaryDirectory() as temp_dir:
        prompt_path = Path(temp_dir) / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8", newline="\n")
        command = llama_command(runtime, model, config, prompt_path)
        schema_path = Path(temp_dir) / "schema.json"
        schema_path.write_text(
            json.dumps(routing_json_schema(task, allowed_categories), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        command.extend(["--json-schema-file", str(schema_path)])
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=int(limits.get("timeout_seconds", DEFAULT_LIMITS["timeout_seconds"])),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, {}, 0.0, "model timed out"
        except OSError as exc:
            return False, {}, 0.0, f"model failed to start: {exc}"
    if completed.returncode != 0:
        return False, {}, 0.0, f"model exited with {completed.returncode}"
    return validate_model_output(
        completed.stdout,
        task=task,
        allowed_categories=allowed_categories,
        config=config,
    )


def run_model(
    *,
    root,
    task,
    item,
    allowed_categories,
    model,
    runtime,
    config,
):
    profile = str(model.get("profile", config.get("active_profile", task)))
    priority = "validation" if "validation" in str(task) else "interactive"
    with model_lease.exclusive_lease(
        root,
        profile=profile,
        role="text",
        priority=priority,
        command_kind="one-shot",
        timeout_ms=0,
    ) as lease:
        if not lease.acquired:
            config["lease"] = lease.report()
            return False, {}, 0.0, "local-ai-busy; deterministic fallback required"
        started = time.perf_counter()
        result = _run_model_unleased(
            task=task,
            item=item,
            allowed_categories=allowed_categories,
            model=model,
            runtime=runtime,
            config=config,
        )
        lease.inference_ms = int(max(0.0, time.perf_counter() - started) * 1000)
        config["lease"] = lease.report()
        return result


def _sync_broker_dependencies():
    broker_tools.shutil = shutil
    broker_tools.subprocess = subprocess


relative_to_root = broker_tools.relative_to_root
normalized_repo_rel = broker_tools.normalized_repo_rel
path_matches_prefix = broker_tools.path_matches_prefix
broker_exclude_paths = broker_tools.broker_exclude_paths
broker_exclude_globs = broker_tools.broker_exclude_globs
broker_path_is_excluded = broker_tools.broker_path_is_excluded
broker_search_file_candidates = broker_tools.broker_search_file_candidates
resolve_repo_request_path = broker_tools.resolve_repo_request_path


def broker_read(root, config, requested_path):
    return broker_tools.broker_read(root, config, requested_path)


def broker_tree(root, config, requested_path, max_entries=None):
    return broker_tools.broker_tree(root, config, requested_path, max_entries)


def broker_search(root, config, pattern, requested_path="."):
    _sync_broker_dependencies()
    return broker_tools.broker_search(root, config, pattern, requested_path)


def broker_generated_status(root):
    return broker_tools.broker_generated_status(root)


def broker_tool_request(root, config, request):
    _sync_broker_dependencies()
    return broker_tools.broker_tool_request(root, config, request)


def empty_item_result(item_id, reason=""):
    return {"accepted": False, "fields": {}, "reason": reason, "item_id": item_id}


def route_items(
    root,
    task,
    items,
    *,
    allowed_categories=None,
    check=False,
):
    allowed = allowed_categories or ["General"]
    config = load_config(root, task)
    results = {
        "status": config.get("status", "disabled"),
        "check_failed": False,
        "issues": [],
        "items": {
            str(item.get("id", "")): empty_item_result(str(item.get("id", "")), config.get("reason", ""))
            for item in items
        },
    }
    if not config.get("enabled"):
        if config.get("required"):
            results["status"] = "required-failed"
            results["check_failed"] = True
            results["issues"].append(str(config.get("reason", "Local AI is required but disabled.")))
        return results

    manifest, issues = load_bundle(root, config)
    if manifest is None and should_auto_bootstrap(config, check=check):
        bootstrap_ok, bootstrap_issues = run_bootstrap_command(root, config, task=task, check=check)
        if bootstrap_ok:
            config = load_config(root, task)
            manifest, issues = load_bundle(root, config)
        else:
            issues.extend(bootstrap_issues)
    if manifest is None:
        results["status"] = "required-failed" if config.get("required") else "fallback"
        results["issues"].extend(issues)
        results["check_failed"] = bool(config.get("required"))
        return results

    model, issues = select_model(root, config, manifest)
    if model is None:
        results["status"] = "required-failed" if config.get("required") else "fallback"
        results["issues"].extend(issues)
        results["check_failed"] = bool(config.get("required"))
        return results

    runtime, issues = select_runtime(root, config, manifest, check_only=check)
    if runtime is None:
        results["status"] = "required-failed" if config.get("required") else "fallback"
        results["issues"].extend(issues)
        results["check_failed"] = bool(config.get("required"))
        return results

    any_accepted = False
    stale_items = []
    rejection_reasons = []
    pending_model_items = []
    for item in items:
        item_id = str(item.get("id", ""))
        cached_fields, cache_reason = read_valid_cache(
            root,
            config,
            item=item,
            task=task,
            model=model,
            runtime=runtime,
            allowed_categories=allowed,
        )
        if cached_fields is not None:
            if cached_fields.get(CACHED_REJECTION):
                results["items"][item_id] = empty_item_result(item_id, cache_reason)
                continue
            results["items"][item_id] = {
                "accepted": True,
                "fields": cached_fields,
                "reason": "cache",
                "item_id": item_id,
            }
            any_accepted = True
            continue
        if check:
            existing_cache = cache_path(
                root,
                task,
                item_id,
                str(config.get("cache_dir", DEFAULT_CACHE_DIR)),
            ).exists()
            if existing_cache or config.get("required"):
                stale_items.append(item_id)
            results["items"][item_id] = empty_item_result(item_id, cache_reason)
            continue
        pending_model_items.append(item)

    model_results = {}
    if pending_model_items:
        if len(pending_model_items) > 1:
            model_results = run_model_batch_with_server(
                root,
                task=task,
                items=pending_model_items,
                allowed_categories=allowed,
                model=model,
                runtime=runtime,
                config=config,
            )
        else:
            item = pending_model_items[0]
            model_results[str(item.get("id", ""))] = run_model(
                root=root,
                task=task,
                item=item,
                allowed_categories=allowed,
                model=model,
                runtime=runtime,
                config=config,
            )

    for item in pending_model_items:
        item_id = str(item.get("id", ""))
        accepted, fields, confidence, reason = model_results.get(
            item_id,
            (False, {}, 0.0, "model did not return a result"),
        )
        if accepted:
            write_cache_entry(
                root,
                config,
                task=task,
                item=item,
                model=model,
                runtime=runtime,
                confidence=confidence,
                fields=fields,
            )
            results["items"][item_id] = {
                "accepted": True,
                "fields": fields,
                "reason": "model",
                "item_id": item_id,
            }
            any_accepted = True
        else:
            write_rejection_cache(
                root,
                config,
                task=task,
                item=item,
                model=model,
                runtime=runtime,
                reason=reason,
            )
            rejection_reasons.append(f"{item_id}: {reason}")
            results["items"][item_id] = empty_item_result(item_id, reason)

    if any_accepted:
        results["status"] = "cache" if check else "model"
    elif stale_items:
        results["status"] = "stale-cache"
        results["issues"].append(
            "Local AI cache is stale or missing for: " + ", ".join(sorted(stale_items))
        )
        results["check_failed"] = True
    elif rejection_reasons:
        results["status"] = "fallback"
        results["issues"].extend(rejection_reasons)
        results["check_failed"] = bool(config.get("required"))
    else:
        results["status"] = "ready"
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the repo-local AI routing helper or inspect its status."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Show local AI readiness.")
    status_parser.add_argument("--root", default=".", help="Repository root.")
    status_parser.add_argument("--task", default="skill-routing", help="Task to inspect.")

    route_parser = subparsers.add_parser("route-items", help="Route JSON items from stdin.")
    route_parser.add_argument("--root", default=".", help="Repository root.")
    route_parser.add_argument("--task", required=True, help="Routing task name.")
    route_parser.add_argument("--check", action="store_true", help="Validate cache only.")
    route_parser.add_argument(
        "--allowed-category",
        action="append",
        default=[],
        help="Allowed category value; can be repeated.",
    )

    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.command == "status":
        config = load_config(root, args.task)
        manifest, manifest_issues = load_bundle(root, config) if config.get("enabled") else (None, [])
        model, model_issues = (select_model(root, config, manifest) if manifest else (None, []))
        runtime, runtime_issues = (
            select_runtime(root, config, manifest, check_only=True) if manifest else (None, [])
        )
        status = {
            "config": config,
            "manifest_found": manifest is not None,
            "model_found": model is not None,
            "runtime_found": runtime is not None,
            "issues": manifest_issues + model_issues + runtime_issues,
        }
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0 if not status["issues"] or not config.get("required") else 2

    payload = json.loads(sys.stdin.read() or "{}")
    items = payload.get("items", [])
    if not isinstance(items, list):
        print("stdin JSON must contain an items array", file=sys.stderr)
        return 2
    result = route_items(
        root,
        args.task,
        items,
        allowed_categories=list(args.allowed_category),
        check=bool(args.check),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result.get("check_failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
