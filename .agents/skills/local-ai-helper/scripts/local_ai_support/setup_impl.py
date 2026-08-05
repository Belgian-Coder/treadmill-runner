#!/usr/bin/env python3

import contextlib
import copy
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import local_ai_routing
from local_ai_support import benchmark_metrics
from local_ai_support import model_lease
from local_ai_support import policy_impl
from local_ai_support.report_support import (
    as_text_list,
    cache_file,
    normalize_evidence,
    print_generated_report,
    print_json,
    print_report,
    relative,
    safe_cache_slug,
    stable_report,
    write_report_cache,
)
from local_ai_support import setup_parser
from local_ai_support.setup_catalog import (
    BENCH_FIXTURES,
    DAILY_TEXT_TASKS,
    DEFAULT_CONFIG,
    EMBEDDING_BENCH_TEXTS,
    EMBEDDING_PROFILE,
    GPU_RUNTIME_PACKAGES,
    MAX_DAILY_INPUT_BYTES,
    MODEL_PACKAGES,
    RUNTIME_PACKAGES,
    TEXT_TASK_PROFILE,
    VISION_PROFILE,
)
from local_ai_support.setup_integration_support import (
    integration_profile_for_use_case,
    integration_suggestions,
    integration_task_for_use_case,
    local_ai_metadata_use_cases,
    markdown_cells,
    metadata_integration_suggestions,
    parse_contract_local_ai_use_cases,
    separator_cells,
    strip_inline_code,
)

LLAMA_RELEASE = "b9222"
LLAMA_RELEASE_URL = f"https://github.com/ggml-org/llama.cpp/releases/tag/{LLAMA_RELEASE}"
LLAMA_LICENSE_URL = f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{LLAMA_RELEASE}/LICENSE"
USER_AGENT = "Skills-local-ai-setup/2.0"
LOCAL_AI_GITIGNORE_COMMENT = "# Local AI helper payloads. Reproduce these with local-ai bootstrap/download."
LOCAL_AI_GITIGNORE_RULES = [
    ".agents/local-ai/downloads/",
    ".agents/local-ai/cache/",
    ".agents/local-ai/bundle/models/*.gguf",
    ".agents/local-ai/bundle/runtimes/",
    ".agents/local-ai/secrets.local.json",
    ".agents/local-ai/local.settings.json",
]
DETACHED_BENCH_COMMAND = "python -B .agents/manage.py local-ai bench --detached-command --standard-metrics"
UNKNOWN_PROFILE_PREFIX = "Unknown local AI profile "
MODEL_SIZE_TOLERANCE_PERCENT = 15
LEASE_REPORT_FIELDS = (
    "lease_wait_ms",
    "load_ms",
    "inference_ms",
    "unload_ms",
    "conflict_count",
    "fallback_used",
)


def default_root():
    for parent in Path(__file__).resolve().parents:
        if (parent / ".agents" / "manage.py").exists():
            return parent
    return Path(__file__).resolve().parents[5]


def setup_report(name, *, ok=True, **fields):
    return {"schema_version": 1, "tool": f"local-ai-helper.{name}", "ok": ok, **fields}


def lease_report_fields(value):
    report = value if isinstance(value, dict) else {}
    fields = {}
    for name in LEASE_REPORT_FIELDS:
        default = False if name == "fallback_used" else 0
        fields[name] = report.get(name, default)
    return fields


def aggregate_lease_reports(values):
    reports = [value for value in values if isinstance(value, dict) and value]
    aggregate = {"schema_version": 1, "sample_count": len(reports)}
    for name in LEASE_REPORT_FIELDS:
        if name == "fallback_used":
            aggregate[name] = any(bool(report.get(name, False)) for report in reports)
        else:
            aggregate[name] = sum(int(report.get(name, 0) or 0) for report in reports)
    return aggregate


def drop_keys(data, *keys):
    for key in keys:
        data.pop(key, None)


def drop_empty(data, *keys):
    for key in keys:
        if not data.get(key):
            data.pop(key, None)


def split_missing_bundle_issues(issues):
    missing_prefixes = (
        "Local AI bundle file is missing:",
        "Local AI bundle manifest is missing:",
    )
    missing = []
    remaining = []
    for issue in issues if isinstance(issues, list) else []:
        text = str(issue)
        if text.startswith(missing_prefixes):
            missing.append(text)
        else:
            remaining.append(text)
    return missing, remaining


def add_missing_bundle_advisory(summary, missing_issues, *, message):
    if not missing_issues:
        return
    summary["missing_bundle_file_count"] = len(missing_issues)
    advisories = list(summary.get("advisories", []))
    advisories.append(message)
    summary["advisories"] = advisories


def display_issue_rows(issues):
    missing_bundle_issues, remaining_issues = split_missing_bundle_issues(issues)
    rows = []
    if missing_bundle_issues:
        rows.append(
            f"Missing local AI bundle files: {len(missing_bundle_issues)} "
            "(run local-ai bootstrap, or keep deterministic fallback)."
        )
    rows.extend(remaining_issues)
    return rows


def model_url(model, filename=None):
    file_name = filename or str(model["file"])
    return f"https://huggingface.co/{model['repo']}/resolve/{model['revision']}/{file_name}"


def repo_file_url(repo, revision, filename):
    return f"https://huggingface.co/{repo}/resolve/{revision}/{filename}"


def license_source_url(package):
    if package.get("license_url"):
        return str(package["license_url"])
    license_repo = str(package.get("license_repo", package["repo"]))
    license_revision = str(package.get("license_revision", package["revision"]))
    return repo_file_url(license_repo, license_revision, "LICENSE")


def license_target_name(package):
    if package.get("license_artifact"):
        return str(package["license_artifact"])
    license_repo = str(package.get("license_repo", package["repo"]))
    return f"{license_repo.replace('/', '-')}-LICENSE.txt"


def package_by_profile(profile):
    for package in MODEL_PACKAGES:
        if str(package["profile"]) == profile:
            return package
    return None


def profile_names(
    *,
    include_optional=True,
    include_embeddings=False,
    include_vision=False,
):
    packages = MODEL_PACKAGES if include_optional else [item for item in MODEL_PACKAGES if item.get("default_install")]
    if not include_embeddings:
        packages = [item for item in packages if str(item.get("kind", "text")) == "text"]
    if not include_vision:
        packages = [item for item in packages if str(item.get("kind", "text")) != "vision"]
    return [str(item["profile"]) for item in packages]


def download_profile_names():
    return [str(item["profile"]) for item in MODEL_PACKAGES]


def default_bootstrap_profile_names():
    return [str(item["profile"]) for item in MODEL_PACKAGES if item.get("default_install")]


def normalize_bootstrap_config(raw):
    defaults = dict(DEFAULT_CONFIG["bootstrap"])
    if isinstance(raw, dict):
        defaults.update(raw)
    profiles = defaults.get("default_profiles", default_bootstrap_profile_names())
    if not isinstance(profiles, list):
        profiles = default_bootstrap_profile_names()
    normalized_profiles = [
        str(profile).strip()
        for profile in profiles
        if str(profile).strip() and package_by_profile(str(profile).strip()) is not None
    ]
    defaults["default_profiles"] = normalized_profiles or default_bootstrap_profile_names()
    try:
        defaults["max_download_gb"] = float(defaults.get("max_download_gb", 20))
    except (TypeError, ValueError):
        defaults["max_download_gb"] = 20.0
    defaults["auto_config"] = bool(defaults.get("auto_config", True))
    defaults["direct_script_fallback"] = bool(defaults.get("direct_script_fallback", True))
    defaults["auto_download"] = str(defaults.get("auto_download", "on-local-ai-use")).strip().lower()
    return defaults


def model_catalog_entries():
    entries = {}
    for package in MODEL_PACKAGES:
        profile = str(package["profile"])
        entries[profile] = {
            "profile": profile,
            "kind": str(package.get("kind", "text")),
            "tier": str(package.get("tier", "optional")),
            "default_install": bool(package.get("default_install", False)),
            "model": package.get("model", package.get("base_model", profile)),
            "base_model": str(package.get("base_model", "")),
            "source": f"https://huggingface.co/{package['repo']}",
            "source_url": model_url(package),
            "license_url": license_source_url(package),
            "download_kind": "direct",
            "license": str(package.get("license", "Apache-2.0")),
            "quant": package["quant"],
            "expected_size_gb": package.get("expected_size_gb"),
            "roles": package["roles"],
            "context_tokens": package["context_tokens"],
            "output_tokens": package["output_tokens"],
            "purpose": package["purpose"],
        }
    return entries


def packages_for_download(profiles):
    wanted = profiles or [str(item["profile"]) for item in MODEL_PACKAGES if item.get("default_install")]
    unknown = [profile for profile in wanted if package_by_profile(profile) is None]
    if unknown:
        raise RuntimeError(f"Unknown local AI profile(s): {', '.join(sorted(unknown))}")
    return [package_by_profile(profile) for profile in wanted if package_by_profile(profile) is not None]


def package_download_urls(package):
    urls = [{"kind": "model", "url": model_url(package)}]
    if package.get("mmproj_url"):
        urls.append({"kind": "mmproj", "url": str(package["mmproj_url"])})
    return urls


def check_url(url, *, timeout_seconds=10):
    try:
        request_obj = request(url)
        request_obj.get_method = lambda: "HEAD"
        with urllib.request.urlopen(request_obj, timeout=timeout_seconds) as response:
            status = int(getattr(response, "status", 200) or 200)
            return {"ok": 200 <= status < 400, "status": status, "url": url, "issue": ""}
    except urllib.error.HTTPError as exc:
        if exc.code == 405:
            try:
                get_request = request(url)
                get_request.add_header("Range", "bytes=0-0")
                with urllib.request.urlopen(get_request, timeout=timeout_seconds) as response:
                    status = int(getattr(response, "status", 200) or 200)
                    return {"ok": 200 <= status < 400, "status": status, "url": url, "issue": ""}
            except (OSError, urllib.error.URLError) as get_exc:
                return {"ok": False, "status": None, "url": url, "issue": str(get_exc)}
        return {"ok": False, "status": exc.code, "url": url, "issue": f"HTTP {exc.code}"}
    except (OSError, urllib.error.URLError) as exc:
        return {"ok": False, "status": None, "url": url, "issue": str(exc)}


def model_url_validation_report(
    *,
    profiles,
    timeout_seconds=10,
):
    selected = packages_for_download(profiles or download_profile_names())
    rows = []
    issues = []
    for package in selected:
        checks = []
        for url_spec in package_download_urls(package):
            result = check_url(url_spec["url"], timeout_seconds=timeout_seconds)
            checks.append({"kind": url_spec["kind"], **result})
            if not result.get("ok"):
                issues.append(f"{package['profile']} {url_spec['kind']} URL failed: {result.get('issue') or result.get('status')}")
        rows.append(
            {
                "profile": package["profile"],
                "ok": all(bool(check.get("ok")) for check in checks),
                "checks": checks,
            }
        )
    return setup_report("model-url-validation", ok=not issues, checked_profile_count=len(rows), profiles=rows, issues=issues)


def estimated_download_gb(packages):
    total = 0.0
    for package in packages:
        try:
            total += float(package.get("expected_size_gb", 0) or 0)
        except (TypeError, ValueError):
            continue
        if package.get("mmproj_file"):
            total += 0.5
    total += 0.2
    return round(total, 2)


def request(url):
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT})


def download_file(url, target, *, force):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        print(f"Already present: {target.name}")
        return
    temp_target = target.with_suffix(target.suffix + ".tmp")
    print(f"Downloading {url}")
    try:
        with urllib.request.urlopen(request(url), timeout=60) as response, temp_target.open("wb") as out:
            total = int(response.headers.get("Content-Length", "0") or "0")
            done = 0
            next_report = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total and done >= next_report:
                    percent = min(100, int(done * 100 / total))
                    print(f"  {percent}% ({done // (1024 * 1024)} MB)", flush=True)
                    next_report = done + max(total // 10, 8 * 1024 * 1024)
    except (OSError, urllib.error.URLError) as exc:
        if temp_target.exists():
            temp_target.unlink()
        raise RuntimeError(f"Download failed for {url}: {exc}") from exc
    temp_target.replace(target)


def safe_extract(zip_path, target_dir):
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    extract_zip_into(zip_path, target_dir)


def extract_zip_into(zip_path, target_dir):
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            member_path = (target_dir / member.filename).resolve()
            try:
                member_path.relative_to(target_dir.resolve())
            except ValueError as exc:
                raise RuntimeError(f"Unsafe zip member path: {member.filename}") from exc
        archive.extractall(target_dir)


def find_executable(runtime_dir, name):
    candidates = sorted(runtime_dir.rglob(name), key=lambda item: len(item.parts))
    if not candidates:
        raise RuntimeError(f"{name} was not found under {runtime_dir}")
    return candidates[0]


def write_default_config(root, *, force):
    path = root / local_ai_routing.CONFIG_RELATIVE_PATH
    if path.exists() and not force:
        print(f"Config already present: {relative(root, path)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {relative(root, path)}")


def write_default_local_settings(root, *, force, quiet=False):
    path = root / local_ai_routing.LOCAL_SETTINGS_RELATIVE_PATH
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(local_ai_routing.DEFAULT_LOCAL_SETTINGS, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if not quiet:
        print(f"Wrote {relative(root, path)}")
    return True


def ensure_local_ai_gitignore(root):
    path = root / ".gitignore"
    try:
        current = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as exc:
        raise RuntimeError(f"Unable to read {relative(root, path)}: {exc}") from exc
    lines = current.splitlines()
    existing = {line.strip() for line in lines}
    missing = [rule for rule in LOCAL_AI_GITIGNORE_RULES if rule not in existing]
    if not missing:
        return False

    if lines and lines[-1].strip():
        lines.append("")
    if LOCAL_AI_GITIGNORE_COMMENT not in existing:
        lines.append(LOCAL_AI_GITIGNORE_COMMENT)
    lines.extend(missing)
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        raise RuntimeError(f"Unable to write {relative(root, path)}: {exc}") from exc
    print(f"Updated {relative(root, path)} with local AI payload ignore rules")
    return True


def gpu_runtime_package(backend, platform_name=None):
    normalized_backend = str(backend).strip().lower()
    current_platform = platform_name or local_ai_routing.platform_id()
    for package in GPU_RUNTIME_PACKAGES:
        if str(package.get("backend", "")).strip().lower() != normalized_backend:
            continue
        if str(package.get("platform", "")).strip().lower() != current_platform:
            continue
        return dict(package)
    return None


def upsert_runtime_override(
    root,
    *,
    backend,
    label,
    cli_path,
    server_path,
):
    settings = local_ai_routing.read_local_settings(root)
    overrides = settings.get("runtime_overrides", [])
    if not isinstance(overrides, list):
        overrides = []
    platform_name = local_ai_routing.platform_id()
    normalized_backend = backend.strip().lower()
    override = {
        "backend": normalized_backend,
        "label": label,
        "platform": platform_name,
        "path": relative(root, cli_path),
        "server_path": relative(root, server_path),
    }
    retained = [
        item
        for item in overrides
        if not (
            isinstance(item, dict)
            and str(item.get("backend", "")).strip().lower() == normalized_backend
            and str(item.get("platform", platform_name)).strip().lower() == platform_name
        )
    ]
    settings["runtime_overrides"] = [override] + retained
    local_ai_routing.write_local_settings(root, settings)


def probe_gpu_runtime(
    executable,
    *,
    backend,
    timeout_seconds,
    cwd,
):
    try:
        completed = subprocess.run(
            [str(executable), "--list-devices"],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "backend": backend,
            "returncode": None,
            "output": str(exc),
            "issue": f"Local AI {backend} probe failed: {exc}",
        }
    output = completed.stdout or ""
    normalized = output.lower()
    ok = completed.returncode == 0 and ("vulkan" in normalized or "device" in normalized) and "no device" not in normalized
    return {
        "ok": ok,
        "backend": backend,
        "returncode": completed.returncode,
        "output": output[-4000:],
        "issue": "" if ok else f"Local AI {backend} runtime did not report a usable device.",
    }


def install_gpu_runtime_package(
    root,
    package,
    *,
    force=False,
):
    bundle_dir = root / ".agents" / "local-ai" / "bundle"
    downloads_dir = root / ".agents" / "local-ai" / "downloads"
    runtimes_dir = bundle_dir / "runtimes"
    runtime_dir = runtimes_dir / str(package["folder"])
    archive_specs = [
        {"url": str(package["url"]), "archive": str(package["archive"]), "kind": "runtime"},
        *[
            {
                "url": str(dependency.get("url", "")),
                "archive": str(dependency.get("archive", "")),
                "kind": "dependency",
            }
            for dependency in package.get("dependencies", [])
            if isinstance(dependency, dict)
        ],
    ]
    if runtime_dir.exists() and not force:
        try:
            cli_path = find_executable(runtime_dir, "llama-cli.exe")
            server_path = find_executable(runtime_dir, "llama-server.exe")
            return {
                "ok": True,
                "downloaded": False,
                "backend": package["backend"],
                "label": package["label"],
                "runtime_dir": relative(root, runtime_dir),
                "cli_path": relative(root, cli_path),
                "server_path": relative(root, server_path),
                "issues": [],
            }
        except RuntimeError:
            pass
    if runtime_dir.exists():
        resolved_runtime_dir = runtime_dir.resolve()
        try:
            resolved_runtime_dir.relative_to(runtimes_dir.resolve())
        except ValueError as exc:
            raise RuntimeError(f"Refusing to replace GPU runtime outside local AI runtimes: {runtime_dir}") from exc
        shutil.rmtree(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    for index, spec in enumerate(archive_specs):
        archive_path = downloads_dir / spec["archive"]
        download_file(spec["url"], archive_path, force=force)
        print(f"Extracting {archive_path.name}")
        if index == 0:
            safe_extract(archive_path, runtime_dir)
        else:
            extract_zip_into(archive_path, runtime_dir)
        archive_path.unlink(missing_ok=True)
    cli_path = find_executable(runtime_dir, "llama-cli.exe")
    server_path = find_executable(runtime_dir, "llama-server.exe")
    return {
        "ok": True,
        "downloaded": True,
        "backend": package["backend"],
        "label": package["label"],
        "runtime_dir": relative(root, runtime_dir),
        "cli_path": relative(root, cli_path),
        "server_path": relative(root, server_path),
        "issues": [],
    }


def ensure_gpu_runtime_report(
    root,
    *,
    backends,
    force=False,
    probe=False,
    dry_run=False,
    timeout_seconds=None,
):
    if not dry_run:
        ensure_local_ai_gitignore(root)
        write_default_local_settings(root, force=False, quiet=True)
    settings = (
        local_ai_routing.read_local_settings(root)
        if (root / local_ai_routing.LOCAL_SETTINGS_RELATIVE_PATH).exists()
        else local_ai_routing.default_local_settings()
    )
    requested = [str(backend).strip().lower() for backend in backends if str(backend).strip()]
    if not requested:
        requested = [
            str(backend).strip().lower()
            for backend in settings.get("gpu", {}).get("preferred_backends", ["cuda", "vulkan"])
            if str(backend).strip().lower() in local_ai_routing.GPU_BACKENDS
        ]
    issues = []
    attempts = []
    current_platform = local_ai_routing.platform_id()
    timeout = timeout_seconds or int(settings.get("gpu", {}).get("probe_timeout_seconds", 5))
    for backend in requested:
        package = gpu_runtime_package(backend, current_platform)
        if package is None:
            issue = f"No pinned {current_platform} GPU runtime package is available for backend {backend}."
            issues.append(issue)
            attempts.append({"backend": backend, "ok": False, "issues": [issue]})
            continue
        if bool(dry_run):
            attempts.append(
                {
                    "backend": backend,
                    "ok": True,
                    "dry_run": True,
                    "label": package["label"],
                    "url": package["url"],
                    "dependencies": package.get("dependencies", []),
                }
            )
            return setup_report("runtime-ensure-gpu", backend=backend, status="dry-run", attempts=attempts, issues=issues)
        try:
            install = install_gpu_runtime_package(root, package, force=force)
        except RuntimeError as exc:
            issue = str(exc)
            issues.append(issue)
            attempts.append({"backend": backend, "ok": False, "label": package["label"], "issues": [issue]})
            continue
        cli_path = root / str(install["cli_path"])
        server_path = root / str(install["server_path"])
        probe_report = (
            probe_gpu_runtime(cli_path, backend=backend, timeout_seconds=timeout, cwd=root)
            if probe
            else {"ok": True, "backend": backend, "output": "", "issue": ""}
        )
        attempt = {**install, "probe": probe_report}
        attempts.append(attempt)
        if probe_report.get("ok"):
            upsert_runtime_override(
                root,
                backend=backend,
                label=str(package["label"]),
                cli_path=cli_path,
                server_path=server_path,
            )
            return setup_report(
                "runtime-ensure-gpu",
                backend=backend,
                status="ready",
                runtime_override={"backend": backend, "path": relative(root, cli_path), "server_path": relative(root, server_path)},
                attempts=attempts,
                issues=issues,
            )
        issue = str(probe_report.get("issue", f"Local AI {backend} runtime probe failed."))
        issues.append(issue)
    if issues:
        local_ai_routing.disable_gpu_in_local_settings(root, "; ".join(issues))
    return setup_report(
        "runtime-ensure-gpu",
        ok=False,
        status="cpu-fallback",
        attempts=attempts,
        issues=issues or ["No GPU runtime backend was requested."],
    )


def print_ensure_gpu_runtime(
    root,
    *,
    backends,
    force=False,
    probe=False,
    dry_run=False,
    timeout_seconds=None,
    as_json=False,
):
    if as_json:
        with contextlib.redirect_stdout(sys.stderr):
            report = ensure_gpu_runtime_report(
                root,
                backends=backends,
                force=force,
                probe=probe,
                dry_run=dry_run,
                timeout_seconds=timeout_seconds,
            )
    else:
        report = ensure_gpu_runtime_report(
            root,
            backends=backends,
            force=force,
            probe=probe,
            dry_run=dry_run,
            timeout_seconds=timeout_seconds,
        )
    if as_json:
        print_json(report)
    else:
        print("Local AI GPU runtime ensure")
        print(f"  Status: {report.get('status', '')}")
        if report.get("backend"):
            print(f"  Backend: {report['backend']}")
        for issue in report.get("issues", []):
            print(f"  - {issue}")
    return 0 if report.get("ok") else 1


def load_raw_config(root):
    path = root / local_ai_routing.CONFIG_RELATIVE_PATH
    if not path.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{relative(root, path)} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{relative(root, path)} must contain a JSON object")
    return value


def save_raw_config(root, config):
    path = root / local_ai_routing.CONFIG_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def parse_route_assignment(value, *, label):
    name, separator, raw_profiles = str(value).partition("=")
    name = name.strip()
    profiles = [item.strip() for item in raw_profiles.split(",") if item.strip()]
    if not separator or not name or not profiles:
        raise RuntimeError(f"{label} must use NAME=PROFILE[,PROFILE]")
    return name, list(dict.fromkeys(profiles))


def detected_backend_proposal(resources, *, allow_integrated=False):
    gpu = resources.get("gpu") if isinstance(resources.get("gpu"), dict) else {}
    devices = gpu.get("devices") if isinstance(gpu.get("devices"), list) else []
    usable = [
        item for item in devices
        if isinstance(item, dict)
        and item.get("device_type") != "virtual"
        and (allow_integrated or item.get("device_type") != "integrated")
    ]
    vendors = {str(item.get("vendor", "")).lower() for item in usable}
    if "nvidia" in vendors:
        return "auto", ["cuda", "vulkan", "cpu"], "dedicated NVIDIA GPU detected"
    if "amd" in vendors:
        return "auto", ["vulkan", "hip", "cpu"], "AMD GPU detected"
    if "intel" in vendors:
        return "auto", ["sycl", "vulkan", "cpu"], "Intel GPU detected"
    if devices and not usable:
        return "off", ["cpu"], "integrated or virtual GPU left disabled"
    return "off", ["cpu"], "no supported GPU detected"


def local_ai_configuration_proposal(
    root,
    *,
    scope,
    route_values,
    group_route_values,
    backend_order,
    gpu_mode,
    gpu_layers,
    allow_integrated,
    performance,
):
    raw_config = load_raw_config(root)
    tasks = {str(item).strip() for item in raw_config.get("tasks", []) if str(item).strip()}
    catalog = local_ai_routing.normalize_model_catalog(raw_config.get("model_catalog", {}))
    catalog_issues = local_ai_routing.validate_model_catalog(catalog)
    if catalog_issues:
        raise RuntimeError("Validated model catalog is invalid: " + "; ".join(catalog_issues))

    target = (
        local_ai_routing.read_local_settings(root)
        if scope == "local"
        else local_ai_routing.read_project_settings(root)
    )
    target.pop("exists", None)
    target.pop("issues", None)
    routes = dict(target.get("task_model_profiles", {}))

    requested_routes = []
    for value in group_route_values:
        group, profiles = parse_route_assignment(value, label="--group-route")
        if group not in local_ai_routing.TASK_GROUPS:
            raise RuntimeError(
                f"Unknown task group {group!r}; choose from {', '.join(sorted(local_ai_routing.TASK_GROUPS))}"
            )
        requested_routes.extend((task, profiles) for task in local_ai_routing.TASK_GROUPS[group])
    for value in route_values:
        requested_routes.append(parse_route_assignment(value, label="--route"))

    for task, profiles in requested_routes:
        if task not in tasks:
            raise RuntimeError(f"Unknown or disabled local-AI task {task!r}")
        unknown = [profile for profile in profiles if profile not in catalog]
        if unknown:
            raise RuntimeError(
                f"Task {task!r} names profiles outside the validated catalog: {', '.join(unknown)}"
            )
        routes[task] = profiles
    target["task_model_profiles"] = routes

    resources = _resources_impl.resource_report(root)
    detected_mode, detected_order, detected_reason = detected_backend_proposal(
        resources, allow_integrated=allow_integrated
    )
    if backend_order:
        requested_order = [item.strip().lower() for item in backend_order.split(",") if item.strip()]
        invalid_backends = [
            item for item in requested_order
            if item not in {"auto", "cpu", *local_ai_routing.GPU_BACKENDS}
        ]
        if invalid_backends:
            raise RuntimeError("Unsupported backend values: " + ", ".join(invalid_backends))
        target["backend_order"] = list(dict.fromkeys(requested_order))
    elif scope == "local" and not target.get("backend_order"):
        target["backend_order"] = detected_order

    bounded_performance = local_ai_routing.normalize_performance_overrides(performance)
    if set(performance) - set(bounded_performance):
        invalid = sorted(set(performance) - set(bounded_performance))
        raise RuntimeError("Unsupported performance values: " + ", ".join(invalid))
    limits = dict(target.get("limits", {}))
    limits.update(bounded_performance)
    if scope == "local" and "threads" not in performance:
        limits["threads"] = int(resources.get("cpu", {}).get("suggested_threads", 1) or 1)
        limits.setdefault("threads_batch", limits["threads"])
    target["limits"] = limits

    if scope == "project" and (gpu_mode is not None or gpu_layers is not None or allow_integrated):
        raise RuntimeError("GPU mode and integrated-GPU choices are machine-owned; use --scope local")
    if scope == "local":
        gpu = target.get("gpu") if isinstance(target.get("gpu"), dict) else {}
        gpu["mode"] = gpu_mode or detected_mode
        gpu["preferred_backends"] = target.get("backend_order") or detected_order
        gpu["allow_integrated"] = bool(allow_integrated)
        if gpu_layers is not None:
            gpu["gpu_layers"] = local_ai_routing.int_limit(gpu_layers, 99, minimum=0, maximum=999)
        gpu["reason"] = detected_reason
        target["gpu"] = gpu

    normalized = (
        local_ai_routing.persistable_local_settings(target)
        if scope == "local"
        else local_ai_routing.persistable_project_settings(target)
    )
    statuses = model_install_status(root, None)
    installed = [row["profile"] for row in statuses if row.get("installed")]
    missing = [row["profile"] for row in statuses if not row.get("installed")]
    settings_path = (
        local_ai_routing.LOCAL_SETTINGS_RELATIVE_PATH
        if scope == "local"
        else local_ai_routing.PROJECT_SETTINGS_RELATIVE_PATH
    )
    return setup_report(
        "configure",
        ok=True,
        status="preview",
        scope=scope,
        settings_path=settings_path,
        tracking="gitignored" if scope == "local" else "tracked",
        detected={
            "cpu": resources.get("cpu", {}),
            "memory": resources.get("memory", {}),
            "disk": resources.get("disk", {}),
            "gpu": resources.get("gpu", {}),
            "backend_reason": detected_reason,
        },
        task_groups=local_ai_routing.TASK_GROUPS,
        installed_profiles=installed,
        missing_profiles=missing,
        previous_calibrations=list(target.get("backend_calibrations", [])) if scope == "local" else [],
        quarantined_backends=list(target.get("backend_quarantine", [])) if scope == "local" else [],
        proposed_settings=normalized,
        download_performed=False,
        recommended_download_command=(
            "python -B .agents/manage.py local-ai download --profile " + missing[0]
            if missing else ""
        ),
    )


def print_local_ai_configure(root, **options):
    as_json = bool(options.pop("as_json"))
    apply_requested = bool(options.pop("apply_requested"))
    report = local_ai_configuration_proposal(root, **options)
    if as_json:
        print_json(report)
    else:
        print("Local AI configuration preview")
        print(f"  Scope: {report['scope']} ({report['tracking']})")
        print(f"  Target: {report['settings_path']}")
        print(f"  CPU threads: {report['proposed_settings'].get('limits', {}).get('threads', 'unchanged')}")
        print(f"  Backend order: {', '.join(report['proposed_settings'].get('backend_order', [])) or 'harness default'}")
        print(f"  Installed profiles: {', '.join(report['installed_profiles']) or 'none'}")
        print(f"  Missing profiles: {', '.join(report['missing_profiles']) or 'none'}")
        print("  Downloads: none")
        if report.get("recommended_download_command"):
            print(f"  Optional next download: {report['recommended_download_command']}")

    should_apply = apply_requested
    if not should_apply and not as_json and sys.stdin.isatty() and sys.stdout.isatty():
        should_apply = input("Apply this configuration? [y/N] ").strip().lower() in {"y", "yes"}
    if not should_apply:
        return 0
    if report["scope"] == "local":
        ensure_local_ai_gitignore(root)
        local_ai_routing.write_local_settings(root, report["proposed_settings"])
    else:
        local_ai_routing.write_project_settings(root, report["proposed_settings"])
    if not as_json:
        print(f"Wrote {report['settings_path']}; no downloads were started.")
    return 0


def effective_config_explanation(root, *, task):
    raw_config = load_raw_config(root)
    tasks = {str(item).strip() for item in raw_config.get("tasks", []) if str(item).strip()}
    if task not in tasks:
        raise RuntimeError(f"Unknown or disabled local-AI task {task!r}")
    config = local_ai_routing.load_config(root, task)
    profile_order = list(config.get("profile_order", []))
    selected_profile = profile_order[0] if profile_order else config.get("selected_profile", "")
    manifest, _ = local_ai_routing.load_bundle(root, config) if config.get("enabled") else (None, [])
    statuses = {row["profile"]: row for row in model_install_status(root, manifest)}
    selected_status = statuses.get(selected_profile, {})
    resources = _resources_impl.resource_report(root)
    catalog_entry = config.get("model_catalog", {}).get(selected_profile, {})
    expected_gb = float(catalog_entry.get("expected_size_gb", 0) or 0)
    available_gb = float(resources.get("memory", {}).get("available_gb", 0) or 0)
    memory_fit = "unknown" if not expected_gb else "fits" if available_gb >= expected_gb * 1.25 else "tight"
    backend_order = list(config.get("backend_order", ["cpu"]))
    selected_backend = backend_order[0] if backend_order else "cpu"
    local_settings = config.get("local_settings", {})
    calibrations = [
        row for row in local_settings.get("backend_calibrations", [])
        if isinstance(row, dict)
        and str(row.get("profile", "")) == selected_profile
        and str(row.get("backend", "")).lower() == selected_backend
    ]
    quarantine = [
        row for row in local_settings.get("backend_quarantine", [])
        if isinstance(row, dict)
        and str(row.get("profile", "")) == selected_profile
        and str(row.get("backend", "")).lower() == selected_backend
    ]
    return setup_report(
        "config-explain",
        ok=not config.get("settings_issues"),
        task=task,
        selected_profile=selected_profile,
        configuration_source=config.get("task_route_source", "harness-defaults"),
        project_settings_path=local_ai_routing.PROJECT_SETTINGS_RELATIVE_PATH,
        local_settings_path=local_ai_routing.LOCAL_SETTINGS_RELATIVE_PATH,
        backend=selected_backend,
        backend_source=config.get("backend_order_source", "harness-defaults"),
        memory_fit={"decision": memory_fit, "available_gb": available_gb, "expected_model_gb": expected_gb},
        installation_state=selected_status.get("install_state", "missing"),
        calibration_decision=calibrations[0] if calibrations else None,
        quarantine_decision=quarantine[0] if quarantine else None,
        fallback_order=profile_order[1:],
        full_route=profile_order,
        limits=config.get("limits", {}),
        limit_sources=config.get("limit_sources", {}),
        issues=config.get("settings_issues", []),
        download_performed=False,
        recommended_download_command=(
            f"python -B .agents/manage.py local-ai download --profile {selected_profile}"
            if not selected_status.get("installed") else ""
        ),
    )


def print_effective_config_explanation(root, *, task, as_json):
    report = effective_config_explanation(root, task=task)
    if as_json:
        print_json(report)
    else:
        print(f"Local AI effective configuration for {task}")
        print(f"  Profile: {report['selected_profile']} ({report['configuration_source']})")
        print(f"  Fallback: {', '.join(report['fallback_order']) or 'deterministic/orchestrator fallback'}")
        print(f"  Backend: {report['backend']} ({report['backend_source']})")
        print(f"  Memory fit: {report['memory_fit']['decision']}")
        print(f"  Installation: {report['installation_state']}")
        print(f"  Calibration: {report['calibration_decision'] or 'none'}")
        print(f"  Quarantine: {report['quarantine_decision'] or 'none'}")
        if report.get("recommended_download_command"):
            print(f"  Optional download: {report['recommended_download_command']}")
    return 0 if report.get("ok") else 1


def collect_manifest_files(bundle_dir):
    files = []
    current_runtime_prefixes = {
        f"runtimes/{package['folder']}/" for package in RUNTIME_PACKAGES
    }
    catalog_model_paths = {f"models/{package['file']}" for package in MODEL_PACKAGES}
    catalog_model_paths.update(
        f"models/{package['mmproj_file']}" for package in MODEL_PACKAGES if package.get("mmproj_file")
    )
    for path in sorted(bundle_dir.rglob("*"), key=lambda item: item.as_posix().lower()):
        if not path.is_file() or path.name == "manifest.json":
            continue
        rel_path = relative(bundle_dir, path)
        if rel_path.startswith("models/") and rel_path not in catalog_model_paths:
            continue
        if rel_path.startswith("runtimes/") and not any(
            rel_path.startswith(prefix) for prefix in current_runtime_prefixes
        ):
            continue
        if "win-vulkan" in rel_path:
            continue
        files.append({"path": rel_path, "sha256": local_ai_routing.sha256_file(path)})
    return files


def remove_disabled_gpu_runtimes(bundle_dir):
    runtimes_dir = bundle_dir / "runtimes"
    if not runtimes_dir.exists():
        return
    for path in runtimes_dir.iterdir():
        if path.is_dir() and "vulkan" in path.name.lower():
            print(f"Removing disabled GPU runtime: {relative(bundle_dir, path)}")
            shutil.rmtree(path)


def model_manifest_entry(root, bundle_dir, package):
    model_path = bundle_dir / "models" / str(package["file"])
    if not model_path.exists():
        return None
    return {
        "profile": package["profile"],
        "aliases": package.get("aliases", []),
        "kind": str(package.get("kind", "text")),
        "tier": package.get("tier", "optional"),
        "default_install": bool(package.get("default_install", False)),
        "path": f"models/{package['file']}",
        "sha256": local_ai_routing.sha256_file(model_path),
        "sidecar_files": [
            {
                "kind": "mmproj",
                "path": f"models/{package['mmproj_file']}",
                "sha256": local_ai_routing.sha256_file(bundle_dir / "models" / str(package["mmproj_file"])),
                "source_url": package["mmproj_url"],
            }
            for _ in [package]
            if package.get("mmproj_file") and (bundle_dir / "models" / str(package["mmproj_file"])).exists()
        ],
        "source": f"https://huggingface.co/{package['repo']}",
        "source_url": model_url(package),
        "source_revision": package["revision"],
        "base_model": package.get("base_model", ""),
        "license": str(package.get("license", "Apache-2.0")),
        "license_url": license_source_url(package),
        "quant": package["quant"],
        "roles": package["roles"],
        "context_tokens": package["context_tokens"],
        "output_tokens": package["output_tokens"],
        "expected_size_gb": package.get("expected_size_gb"),
        "purpose": package["purpose"],
    }


def runtime_manifest_entries(root, bundle_dir):
    entries = []
    runtimes_dir = bundle_dir / "runtimes"
    for package in RUNTIME_PACKAGES:
        runtime_dir = runtimes_dir / str(package["folder"])
        if not runtime_dir.exists():
            continue
        cli_path = find_executable(runtime_dir, "llama-cli.exe")
        server_path = find_executable(runtime_dir, "llama-server.exe")
        entries.append(
            {
                "backend": package["backend"],
                "platform": package["platform"],
                "path": relative(bundle_dir, cli_path),
                "sha256": local_ai_routing.sha256_file(cli_path),
                "server_path": relative(bundle_dir, server_path),
                "server_sha256": local_ai_routing.sha256_file(server_path),
                "package_url": package["url"],
                "license": "MIT",
            }
        )
    return entries


def write_manifest(root):
    bundle_dir = root / ".agents" / "local-ai" / "bundle"
    model_entries = [
        entry
        for package in MODEL_PACKAGES
        if (entry := model_manifest_entry(root, bundle_dir, package)) is not None
    ]
    manifest = {
        "schema_version": 1,
        "generated_by": "local-ai-helper setup_local_ai.py",
        "generated_at_unix": int(time.time()),
        "runtime": {
            "name": "llama.cpp",
            "version": LLAMA_RELEASE,
            "source": LLAMA_RELEASE_URL,
            "license": "MIT",
            "gpu_backends_default": "local-settings-auto-cpu-fallback",
        },
        "primary_profiles": local_ai_routing.DEFAULT_PRIMARY_PROFILES,
        "optional_profiles": local_ai_routing.DEFAULT_OPTIONAL_PROFILES,
        "model_catalog": model_catalog_entries(),
        "models": model_entries,
        "runtimes": runtime_manifest_entries(root, bundle_dir),
        "files": collect_manifest_files(bundle_dir),
    }
    manifest_path = bundle_dir / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {relative(root, manifest_path)}")


def download_bundle(root, *, force, profiles=None):
    bundle_dir = root / ".agents" / "local-ai" / "bundle"
    downloads_dir = root / ".agents" / "local-ai" / "downloads"
    models_dir = bundle_dir / "models"
    runtimes_dir = bundle_dir / "runtimes"
    licenses_dir = bundle_dir / "licenses"
    write_default_config(root, force=False)
    ensure_local_ai_gitignore(root)
    remove_disabled_gpu_runtimes(bundle_dir)

    for model in packages_for_download(profiles or []):
        file_name = str(model["file"])
        model_path = models_dir / file_name
        download_file(model_url(model), model_path, force=force)
        if model.get("mmproj_file"):
            download_file(str(model["mmproj_url"]), models_dir / str(model["mmproj_file"]), force=force)
        download_file(
            license_source_url(model),
            licenses_dir / license_target_name(model),
            force=force,
        )
        readme_repo = str(model.get("readme_repo", model["repo"]))
        readme_revision = str(model.get("readme_revision", model["revision"]))
        readme_prefix = readme_repo.replace("/", "-")
        download_file(
            repo_file_url(readme_repo, readme_revision, "README.md"),
            licenses_dir / f"{readme_prefix}-README.md",
            force=force,
        )

    download_file(LLAMA_LICENSE_URL, licenses_dir / "llama.cpp-LICENSE.txt", force=force)

    for package in RUNTIME_PACKAGES:
        archive_path = downloads_dir / str(package["archive"])
        runtime_dir = runtimes_dir / str(package["folder"])
        if force or not runtime_dir.exists():
            download_file(str(package["url"]), archive_path, force=force)
            print(f"Extracting {archive_path.name}")
            safe_extract(archive_path, runtime_dir)
            archive_path.unlink(missing_ok=True)
        else:
            print(f"Already extracted: {relative(root, runtime_dir)}")
    write_manifest(root)


def cache_counts(root):
    cache_root = root / ".agents" / "local-ai" / "cache"
    counts = {}
    tasks = sorted(local_ai_routing.DEFAULT_TASK_MODEL_PROFILES)
    for task in tasks:
        task_dir = cache_root / task
        accepted = 0
        rejected = 0
        if task_dir.exists():
            for path in task_dir.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("accepted") is False:
                    rejected += 1
                else:
                    accepted += 1
        counts[task] = {"accepted": accepted, "rejected": rejected, "total": accepted + rejected}
    return counts


def manifest_model_map(manifest):
    if not manifest:
        return {}
    models = manifest.get("models", [])
    if not isinstance(models, list):
        return {}
    return {str(model.get("profile", "")): model for model in models if isinstance(model, dict)}


def expected_model_size_bytes(manifest_entry):
    raw_bytes = manifest_entry.get("expected_size_bytes") if isinstance(manifest_entry, dict) else None
    try:
        if raw_bytes not in (None, ""):
            return int(raw_bytes)
    except (TypeError, ValueError):
        return 0
    raw_gb = manifest_entry.get("expected_size_gb") if isinstance(manifest_entry, dict) else None
    try:
        if raw_gb not in (None, ""):
            return int(float(raw_gb) * (1024 ** 3))
    except (TypeError, ValueError):
        return 0
    return 0


def installed_model_size_bytes(bundle_dir, manifest_entry, model_path):
    if not model_path.exists():
        return 0
    total = model_path.stat().st_size
    sidecars = manifest_entry.get("sidecar_files") if isinstance(manifest_entry, dict) else []
    for sidecar in sidecars if isinstance(sidecars, list) else []:
        if not isinstance(sidecar, dict):
            continue
        sidecar_path = bundle_dir / str(sidecar.get("path", ""))
        if sidecar_path.exists():
            total += sidecar_path.stat().st_size
    return total


def model_size_state(bundle_dir, manifest_entry, model_path):
    actual = installed_model_size_bytes(bundle_dir, manifest_entry, model_path)
    expected = expected_model_size_bytes(manifest_entry)
    if not model_path.exists():
        return {
            "size_state": "missing",
            "actual_size_bytes": actual,
            "expected_size_bytes": expected,
            "size_tolerance_percent": MODEL_SIZE_TOLERANCE_PERCENT,
        }
    if expected <= 0:
        return {
            "size_state": "unchecked",
            "actual_size_bytes": actual,
            "expected_size_bytes": 0,
            "size_tolerance_percent": MODEL_SIZE_TOLERANCE_PERCENT,
        }
    tolerance = MODEL_SIZE_TOLERANCE_PERCENT / 100
    minimum = int(expected * (1 - tolerance))
    maximum = int(expected * (1 + tolerance))
    ok = minimum <= actual <= maximum
    return {
        "size_state": "ok" if ok else "size-mismatch",
        "actual_size_bytes": actual,
        "expected_size_bytes": expected,
        "min_expected_size_bytes": minimum,
        "max_expected_size_bytes": maximum,
        "size_tolerance_percent": MODEL_SIZE_TOLERANCE_PERCENT,
    }


def model_size_issues(statuses):
    issues = []
    for status in statuses:
        if status.get("size_state") != "size-mismatch":
            continue
        issues.append(
            f"{status.get('profile')} installed size {status.get('actual_size_bytes')} bytes is outside expected range "
            f"{status.get('min_expected_size_bytes')}-{status.get('max_expected_size_bytes')} bytes."
        )
    return issues


def model_install_status(root, manifest):
    manifest_path = root / local_ai_routing.DEFAULT_MANIFEST_PATH
    bundle_dir = manifest_path.parent
    model_map = manifest_model_map(manifest)
    statuses = []
    for package in MODEL_PACKAGES:
        profile = str(package["profile"])
        manifest_entry = model_map.get(profile, {})
        if (
            manifest_entry
            and manifest_entry.get("base_model")
            and str(manifest_entry.get("base_model", "")) != str(package.get("base_model", ""))
        ):
            manifest_entry = {}
        if (
            manifest_entry
            and manifest_entry.get("quant")
            and str(manifest_entry.get("quant", "")) != str(package.get("quant", ""))
        ):
            manifest_entry = {}
        path = bundle_dir / str(manifest_entry.get("path", f"models/{package['file']}"))
        exists = path.exists()
        sha = local_ai_routing.sha256_file(path) if exists else ""
        manifest_sha = str(manifest_entry.get("sha256", ""))
        size = model_size_state(bundle_dir, manifest_entry, path)
        if not manifest_entry:
            manifest_state = "not-in-manifest"
        elif not exists:
            manifest_state = "manifest-only"
        elif manifest_sha and manifest_sha == sha:
            manifest_state = "hash-valid"
        elif manifest_sha:
            manifest_state = "hash-mismatch"
        else:
            manifest_state = "manifest-no-hash"
        source_url = model_url(package)
        statuses.append(
            {
                "profile": profile,
                "kind": str(package.get("kind", "text")),
                "tier": package.get("tier", "optional"),
                "default_install": bool(package.get("default_install", False)),
                "quant": package["quant"],
                "roles": package["roles"],
                "license": str(package.get("license", "Apache-2.0")),
                "download_kind": "direct",
                "source": f"https://huggingface.co/{package['repo']}",
                "source_url": model_url(package),
                "base_model": package.get("base_model", ""),
                "expected_size_gb": package.get("expected_size_gb"),
                "installed": exists,
                "install_state": "size-mismatch" if exists and size.get("size_state") == "size-mismatch" else "installed" if exists else "missing",
                "path": relative(root, path),
                "sha256": sha,
                "manifest_sha256": manifest_sha,
                "manifest_state": manifest_state,
                **size,
                "direct_download": source_url.startswith("https://huggingface.co/") and "/resolve/" in source_url,
                "purpose": package["purpose"],
            }
        )
    return statuses


def profile_management_checks(config, statuses):
    aliases = {}
    duplicates = []
    for package in MODEL_PACKAGES:
        profile = str(package["profile"])
        values = [profile] + [str(item) for item in package.get("aliases", [])]
        for value in values:
            key = value.strip().lower()
            if not key:
                continue
            if key in aliases and aliases[key] != profile:
                duplicates.append(key)
            aliases[key] = profile
    backend_order = [str(item).strip().lower() for item in config.get("backend_order", ["cpu"]) if str(item).strip()]
    return {
        "alias_unique": not duplicates,
        "duplicate_aliases": sorted(set(duplicates)),
        "cpu_default": backend_order == ["cpu"],
        "backend_order": backend_order,
        "new_dependency_count": 0,
        "installed_profiles": [item["profile"] for item in statuses if item.get("installed")],
        "missing_profiles": [item["profile"] for item in statuses if not item.get("installed")],
        "manifest_mismatch_count": sum(1 for item in statuses if item.get("manifest_state") == "hash-mismatch"),
        "size_mismatch_count": sum(1 for item in statuses if item.get("size_state") == "size-mismatch"),
    }


def model_rows_summary(statuses):
    by_kind = {}
    by_state = {}
    installed_profiles = []
    missing_profiles = []
    for status in statuses:
        kind = str(status.get("kind", "text"))
        by_kind[kind] = by_kind.get(kind, 0) + 1
        state = str(status.get("install_state", "installed" if status.get("installed") else "missing"))
        by_state[state] = by_state.get(state, 0) + 1
        if status.get("installed"):
            installed_profiles.append(str(status.get("profile", "")))
        else:
            missing_profiles.append(str(status.get("profile", "")))
    return {
        "model_count": len(statuses),
        "installed_count": len(installed_profiles),
        "missing_count": len(missing_profiles),
        "by_kind": dict(sorted(by_kind.items())),
        "by_state": dict(sorted(by_state.items())),
        "installed_profiles": sorted(installed_profiles),
        "missing_profiles": sorted(missing_profiles),
    }


def catalog_summary(report, *, compact=False):
    models = report.get("models") if isinstance(report.get("models"), list) else []
    profile_checks = report.get("profile_checks") if isinstance(report.get("profile_checks"), dict) else {}
    summary = setup_report(
        "catalog-summary",
        ok=not report.get("issues"),
        issues=report.get("issues", []),
        profile_checks=profile_checks,
        policy=report.get("policy", {}),
        **model_rows_summary(models),
    )
    if compact:
        summary["policy"] = "direct-local"
        summary["alias_unique"] = bool(profile_checks.get("alias_unique", True))
        summary["manifest_mismatch_count"] = int(profile_checks.get("manifest_mismatch_count", 0) or 0)
        summary["size_mismatch_count"] = int(profile_checks.get("size_mismatch_count", 0) or 0)
        summary["new_dependency_count"] = int(profile_checks.get("new_dependency_count", 0) or 0)
        drop_keys(summary, "profile_checks", "installed_profiles", "missing_profiles", "by_state")
        drop_empty(summary, "issues")
    else:
        summary["models"] = [
            {
                "profile": item.get("profile", ""),
                "kind": item.get("kind", ""),
                "tier": item.get("tier", ""),
                "installed": bool(item.get("installed", False)),
                "license": item.get("license", ""),
            }
            for item in models
            if isinstance(item, dict)
        ]
    return summary


def print_catalog(root, *, as_json, summary=False, compact=False):
    config = local_ai_routing.load_config(root, "skill-routing")
    manifest, issues = local_ai_routing.load_bundle(root, config) if config.get("enabled") else (None, [])
    catalog_issues = list(config.get("catalog_issues", []))
    statuses = model_install_status(root, manifest)
    size_issues = model_size_issues(statuses)
    profile_checks = profile_management_checks(config, statuses)
    report = {
        "models": statuses,
        "profile_checks": profile_checks,
        "issues": issues + catalog_issues + size_issues,
        "policy": {
            "downloads": "direct-only",
            "licenses": sorted(local_ai_routing.COMMERCIAL_OK_LICENSES),
            "local_only": True,
        },
    }
    if as_json:
        output = catalog_summary(report, compact=compact) if summary or compact else report
        print_json(output)
        return 1 if issues or catalog_issues or size_issues else 0
    print("Local AI model catalog")
    print("  Policy: direct downloads only; commercial-use local licenses only; local inference only.")
    for status in statuses:
        installed = "installed" if status["installed"] else "not installed"
        print(
            f"  {status['profile']}: {status['tier']}, {installed}, "
            f"{status['quant']}, {status['license']}, ~{status['expected_size_gb']} GB"
        )
        print(f"    Source: {status['source']}")
        print(f"    Use: {status['purpose']}")
    if issues or catalog_issues or size_issues:
        print("  Issues:")
        for issue in issues + catalog_issues + size_issues:
            print(f"    - {issue}")
    return 1 if issues or catalog_issues or size_issues else 0


def build_status(root, *, task="skill-routing", profile=None):
    config = local_ai_routing.load_config(root, task)
    if profile:
        config["profile_order"] = [profile]
        local_ai_routing.apply_profile_to_config(config, profile)
    manifest, manifest_issues = (
        local_ai_routing.load_bundle(root, config) if config.get("enabled") else (None, [])
    )
    model, model_issues = (
        local_ai_routing.select_model(root, config, manifest) if manifest else (None, [])
    )
    runtime, runtime_issues = (
        local_ai_routing.select_runtime(root, config, manifest, check_only=True) if manifest else (None, [])
    )
    statuses = model_install_status(root, manifest)
    return {
        "config_path": local_ai_routing.CONFIG_RELATIVE_PATH,
        "local_settings_path": config.get("local_settings_path", local_ai_routing.LOCAL_SETTINGS_RELATIVE_PATH),
        "enabled": bool(config.get("enabled")),
        "mode": config.get("mode", config.get("status")),
        "gpu": config.get("gpu", {}),
        "configured_backend_order": config.get("configured_backend_order", []),
        "backend_order": config.get("backend_order", ["cpu"]),
        "task": task,
        "profile_order": config.get("profile_order", []),
        "model_profile": model.get("profile") if model else config.get("selected_profile"),
        "bundle_manifest": config.get("bundle_manifest", local_ai_routing.DEFAULT_MANIFEST_PATH),
        "manifest_found": manifest is not None,
        "model_found": model is not None,
        "selected_runtime": runtime.get("backend") if runtime else None,
        "backend_decision": config.get("backend_decision", {}),
        "models": statuses,
        "cache_counts": cache_counts(root),
        "issues": (
            list(config.get("catalog_issues", []))
            + list(config.get("local_settings_issues", []))
            + manifest_issues
            + model_issues
            + runtime_issues
            + model_size_issues(statuses)
        ),
    }


def model_policy_report(root, *, use_case=None, owner=None):
    config = local_ai_routing.load_config(root, "skill-routing")
    integration_policy = policy_impl.policy_report(root, use_case=use_case, owner=owner)
    manifest, manifest_issues = (
        local_ai_routing.load_bundle(root, config) if config.get("enabled") else (None, [])
    )
    install_statuses = model_install_status(root, manifest)
    size_issues = model_size_issues(install_statuses)
    by_profile = {str(item.get("profile")): item for item in install_statuses}
    selected_profiles = {
        "text": TEXT_TASK_PROFILE,
        "embedding": EMBEDDING_PROFILE,
        "vision": VISION_PROFILE,
        "routing": first_profile(config.get("task_model_profiles", {}).get("skill-routing", TEXT_TASK_PROFILE)),
    }
    return setup_report(
        "policy",
        ok=bool(integration_policy.get("ok", True)),
        policy_path=policy_impl.POLICY_RELATIVE_PATH,
        secrets_path=integration_policy.get("secrets_path", policy_impl.SECRETS_RELATIVE_PATH),
        integration_policy=integration_policy,
        enabled=bool(config.get("enabled")),
        backend_order=config.get("backend_order", ["cpu"]),
        configured_backend_order=config.get("configured_backend_order", ["auto", "cpu"]),
        text_model=TEXT_TASK_PROFILE,
        embedding_model=EMBEDDING_PROFILE,
        vision_model=VISION_PROFILE,
        selected_profiles={
            role: {
                "profile": profile,
                "installed": bool(by_profile.get(profile, {}).get("installed", False)),
                "path": by_profile.get(profile, {}).get("path", ""),
                "license": by_profile.get(profile, {}).get("license", ""),
                "tier": by_profile.get(profile, {}).get("tier", ""),
            }
            for role, profile in selected_profiles.items()
        },
        task_routes=config.get("task_model_profiles", {}),
        task_envelopes=config.get("task_envelopes", local_ai_routing.DEFAULT_TASK_ENVELOPES),
        model_task_envelopes=config.get("model_task_envelopes", local_ai_routing.DEFAULT_MODEL_TASK_ENVELOPES),
        task_attempt_policy=config.get("task_attempt_policy", local_ai_routing.DEFAULT_TASK_ATTEMPT_POLICY),
        benchmark_policy=config.get("benchmark_policy", local_ai_routing.DEFAULT_BENCHMARK_POLICY),
        gpu_default=str(config.get("gpu", {}).get("mode", "auto")) if isinstance(config.get("gpu"), dict) else "auto",
        reasoning_default=str(config.get("limits", {}).get("reasoning", "off")),
        kv_cache={
            "cache_type_k": str(config.get("limits", {}).get("cache_type_k", "q4_0")),
            "cache_type_v": str(config.get("limits", {}).get("cache_type_v", "q4_0")),
        },
        issues=manifest_issues + size_issues,
    )


def model_policy_summary(report, *, compact=False):
    integration_policy = report.get("integration_policy") if isinstance(report.get("integration_policy"), dict) else {}
    owners = integration_policy.get("owners") if isinstance(integration_policy.get("owners"), dict) else {}
    use_cases = integration_policy.get("use_cases") if isinstance(integration_policy.get("use_cases"), dict) else {}
    selected_profiles = report.get("selected_profiles") if isinstance(report.get("selected_profiles"), dict) else {}
    profiles = {
        role: {
            "profile": details.get("profile", ""),
            "installed": bool(details.get("installed", False)),
            "tier": details.get("tier", ""),
        }
        for role, details in selected_profiles.items()
        if isinstance(details, dict)
    }
    summary = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "local-ai-helper.policy"),
        "ok": report.get("ok", False),
        "policy_path": report.get("policy_path", ""),
        "secrets_file_present": bool(integration_policy.get("secrets_file_present", False)),
        "enabled": bool(report.get("enabled", False)),
        "mode": integration_policy.get("mode", "auto"),
        "owner_count": len(owners),
        "use_case_count": len(use_cases),
        "selected_profiles": profiles,
        "issues": report.get("issues", []),
    }
    decision = integration_policy.get("decision")
    if isinstance(decision, dict):
        summary["decision"] = decision
    if compact:
        summary["installed_profile_count"] = sum(1 for details in profiles.values() if details.get("installed"))
        missing_profiles = [
            {"role": role, "profile": details.get("profile", "")}
            for role, details in profiles.items()
            if not details.get("installed")
        ]
        missing_bundle_issues, remaining_issues = split_missing_bundle_issues(summary.get("issues", []))
        drop_keys(summary, "selected_profiles")
        drop_keys(summary, "policy_path")
        if missing_profiles and summary["installed_profile_count"]:
            summary["model_download_status"] = "partial"
        elif missing_profiles:
            summary["model_download_status"] = "not-downloaded"
        else:
            summary["model_download_status"] = "ready"
        if missing_profiles:
            summary["models_not_downloaded"] = missing_profiles
        add_missing_bundle_advisory(
            summary,
            missing_bundle_issues,
            message="Local AI config and policy are ready, but model/runtime payloads were not downloaded.",
        )
        summary["issues"] = remaining_issues
        drop_empty(summary, "issues")
    else:
        summary["task_route_count"] = len(report.get("task_routes", {})) if isinstance(report.get("task_routes"), dict) else 0
        summary["task_envelope_count"] = len(report.get("task_envelopes", {})) if isinstance(report.get("task_envelopes"), dict) else 0
        summary["model_task_envelope_count"] = (
            len(report.get("model_task_envelopes", {})) if isinstance(report.get("model_task_envelopes"), dict) else 0
        )
        summary["gpu_default"] = report.get("gpu_default", "disabled")
        summary["reasoning_default"] = report.get("reasoning_default", "off")
    return summary


def first_profile(value):
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, tuple) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return TEXT_TASK_PROFILE


def print_policy(
    root,
    *,
    as_json,
    use_case=None,
    owner=None,
    write_default=False,
    summary=False,
    compact=False,
):
    if write_default:
        policy_impl.write_default_policy(root, force=False)
        policy_impl.write_secrets_example(root, force=False)
        ensure_local_ai_gitignore(root)
        write_default_local_settings(root, force=False)
    report = model_policy_report(root, use_case=use_case, owner=owner)
    output_report = model_policy_summary(report, compact=compact) if summary or compact else report
    if as_json:
        print_json(output_report)
        decision = report.get("integration_policy", {}).get("decision")
        return 0 if report.get("ok") and (not decision or decision.get("ok")) else 1
    print("Local AI current model policy")
    print(f"  Policy file: {report['policy_path']}")
    print(f"  Secrets file: {report['secrets_path']} (gitignored)")
    print(f"  Enabled: {report['enabled']}")
    print(f"  Text: {report['text_model']}")
    print(f"  Embedding: {report['embedding_model']}")
    print(f"  Vision: {report['vision_model']}")
    print("  Selected profiles:")
    for role, details in report.get("selected_profiles", {}).items():
        installed = "installed" if details.get("installed") else "not installed"
        print(f"    - {role}: {details.get('profile')} ({installed})")
    print(f"  Backend order: {', '.join(report['backend_order'])}")
    print(f"  Reasoning: {report['reasoning_default']}")
    print(
        "  KV cache: "
        f"K={report['kv_cache']['cache_type_k']}, V={report['kv_cache']['cache_type_v']}"
    )
    integration_policy = report.get("integration_policy", {})
    issues = integration_policy.get("issues", []) if isinstance(integration_policy, dict) else []
    if issues:
        print("  Policy issues:")
        for issue in issues:
            print(f"    - {issue}")
    for issue in report.get("issues", []):
        print(f"  Model issue: {issue}")
    decision = integration_policy.get("decision") if isinstance(integration_policy, dict) else None
    if isinstance(decision, dict):
        print(
            f"  Use case {decision.get('use_case')}: "
            f"{'allowed' if decision.get('allowed') else 'fallback'}"
        )
        print(f"    Owner: {decision.get('owner') or 'any'}")
        print(f"    Reason: {decision.get('reason')}")
        return 0 if decision.get("ok") else 1
    return 0 if report.get("ok") and not issues else 1


def readiness_report(root, *, task="skill-routing", profile=None):
    status = build_status(root, task=task, profile=profile)
    raw_config = load_raw_config(root)
    manifest_path = root / local_ai_routing.DEFAULT_MANIFEST_PATH
    runtime_missing = bool(status.get("manifest_found")) and not bool(status.get("selected_runtime"))
    model_missing = bool(status.get("manifest_found")) and not bool(status.get("model_found"))
    disk = shutil.disk_usage(root)
    selected_profiles = normalize_bootstrap_config(raw_config.get("bootstrap")).get("default_profiles", [])
    estimated_gb = estimated_download_gb(packages_for_download(selected_profiles))
    cache_root = root / ".agents" / "local-ai" / "cache"
    invalid_cache_files = []
    stale_prompt_files = []
    if cache_root.exists():
        for path in sorted(cache_root.rglob("*.json"), key=lambda item: item.as_posix().lower())[:300]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                invalid_cache_files.append(relative(root, path))
                continue
            if not isinstance(data, dict):
                continue
            prompt_version = data.get("prompt_version")
            if prompt_version is not None and prompt_version != local_ai_routing.PROMPT_VERSION:
                stale_prompt_files.append(relative(root, path))
    categories = {
        "disabled_config": not bool(status.get("enabled")),
        "missing_manifest": not bool(status.get("manifest_found")),
        "missing_model": model_missing,
        "missing_runtime": runtime_missing,
        "bad_hash_or_selection": [
            issue for issue in status.get("issues", []) if "hash" in str(issue).lower() or "sha256" in str(issue).lower()
        ],
        "invalid_schema_cache": invalid_cache_files[:20],
        "stale_prompt_version": stale_prompt_files[:20],
    }
    ready = status_ready(status)
    return setup_report(
        "readiness",
        ok=ready,
        status="ready" if ready else "not-ready",
        task=task,
        profile=profile or status.get("model_profile") or "",
        categories=categories,
        disk={
            "free_gb": round(disk.free / (1024 ** 3), 2),
            "estimated_default_download_gb": estimated_gb,
            "enough_for_default_download": disk.free > int((estimated_gb + 1) * (1024 ** 3)),
        },
        memory={
            "note": "Runtime memory is checked by benchmark/doctor; setup estimates model disk size before download.",
            "max_configured_download_gb": normalize_bootstrap_config(raw_config.get("bootstrap")).get("max_download_gb"),
            "candidate_memory_limit_gb": local_ai_routing.normalize_benchmark_policy(
                raw_config.get("benchmark_policy", {})
            ).get("candidate_memory_limit_gb"),
        },
        issues=status.get("issues", []),
        next_action=(
            "Use normal repo commands; local AI is ready."
            if ready
            else "Run python -B .agents/manage.py local-ai bootstrap, or keep working without local AI fallback."
        ),
    )


def print_readiness(root, *, task, profile, as_json):
    report = readiness_report(root, task=task, profile=profile)
    if as_json:
        print_json(report)
        return 0 if report["ok"] else 1
    print("Local AI first-run readiness")
    print(f"  Status: {report['status']}")
    print(f"  Task: {report['task']}")
    print(f"  Profile: {report['profile'] or 'none'}")
    print(f"  Disk free: {report['disk']['free_gb']} GB")
    for key, value in report["categories"].items():
        print(f"  {key}: {value}")
    if report["issues"]:
        print("  Issues:")
        for issue in display_issue_rows(report["issues"]):
            print(f"    - {issue}")
    print(f"  Next: {report['next_action']}")
    return 0 if report["ok"] else 1


def readiness_summary(report, *, compact=False):
    categories = report.get("categories") if isinstance(report.get("categories"), dict) else {}
    not_ready_categories = [
        name
        for name, value in categories.items()
        if bool(value) and (not isinstance(value, list) or len(value) > 0)
    ]
    summary = {
        "schema_version": report.get("schema_version", 1),
        "tool": "local-ai-helper.readiness-summary",
        "ok": bool(report.get("ok", False)),
        "status": report.get("status", ""),
        "task": report.get("task", ""),
        "profile": report.get("profile", ""),
        "not_ready_category_count": len(not_ready_categories),
        "not_ready_categories": not_ready_categories,
        "issue_count": len(report.get("issues", []) if isinstance(report.get("issues"), list) else []),
        "issues": report.get("issues", []),
        "next_action": report.get("next_action", ""),
    }
    disk = report.get("disk") if isinstance(report.get("disk"), dict) else {}
    summary["disk"] = {
        "free_gb": disk.get("free_gb", 0),
        "enough_for_default_download": bool(disk.get("enough_for_default_download", False)),
    }
    if compact:
        missing_bundle_issues, remaining_issues = split_missing_bundle_issues(summary.get("issues", []))
        add_missing_bundle_advisory(
            summary,
            missing_bundle_issues,
            message="Local AI model/runtime payloads are not downloaded; deterministic fallback can continue.",
        )
        summary["issues"] = remaining_issues
        drop_empty(summary, "issues", "not_ready_categories")
        if bool(summary.get("ok")):
            drop_keys(summary, "next_action")
    else:
        summary["categories"] = categories
    return summary


def print_readiness_report(
    root,
    *,
    task,
    profile,
    as_json,
    summary=False,
    compact=False,
):
    report = readiness_report(root, task=task, profile=profile)
    output_report = readiness_summary(report, compact=compact) if summary or compact else report
    if as_json:
        print_json(output_report)
        return 0 if report["ok"] else 1
    if summary or compact:
        print("Local AI first-run readiness summary")
        print(f"  Status: {output_report['status']}")
        print(f"  Not-ready categories: {output_report['not_ready_category_count']}")
        print(f"  Next: {output_report['next_action']}")
        return 0 if report["ok"] else 1
    return print_readiness(root, task=task, profile=profile, as_json=False)


def status_summary(report, *, compact=False):
    models = report.get("models") if isinstance(report.get("models"), list) else []
    cache_counts = report.get("cache_counts") if isinstance(report.get("cache_counts"), dict) else {}
    summary = setup_report(
        "status-summary",
        ok=status_ready(report),
        enabled=bool(report.get("enabled")),
        mode=report.get("mode", ""),
        gpu=report.get("gpu", {}),
        backend_order=report.get("backend_order", []),
        backend_decision=report.get("backend_decision", {}),
        task=report.get("task", ""),
        model_profile=report.get("model_profile", ""),
        selected_runtime=report.get("selected_runtime", ""),
        manifest_found=bool(report.get("manifest_found")),
        model_found=bool(report.get("model_found")),
        profile_order=report.get("profile_order", []),
        issues=report.get("issues", []),
        **model_rows_summary(models),
    )
    if compact:
        drop_keys(summary, "installed_profiles", "missing_profiles", "by_state")
        drop_empty(summary, "issues")
        if summary.get("profile_order") == [summary.get("model_profile")]:
            drop_keys(summary, "profile_order")
        summary["cache_total"] = sum(int(row.get("total", 0) or 0) for row in cache_counts.values() if isinstance(row, dict))
    else:
        summary["cache_counts"] = cache_counts
        summary["models"] = [
            {
                "profile": item.get("profile", ""),
                "kind": item.get("kind", ""),
                "installed": bool(item.get("installed", False)),
                "manifest_state": item.get("manifest_state", ""),
            }
            for item in models
            if isinstance(item, dict)
        ]
    return summary


def print_status(
    root,
    *,
    as_json,
    task="skill-routing",
    profile=None,
    summary=False,
    compact=False,
):
    write_default_local_settings(root, force=False, quiet=as_json)
    status = build_status(root, task=task, profile=profile)
    if as_json:
        output = status_summary(status, compact=compact) if summary or compact else status
        print_json(output)
    else:
        print("Local AI helper")
        print(f"  Config: {status['config_path']}")
        print(f"  Local settings: {status['local_settings_path']} (gitignored)")
        print(f"  Enabled: {status['enabled']} ({status['mode']})")
        gpu = status.get("gpu", {}) if isinstance(status.get("gpu"), dict) else {}
        print(f"  GPU mode: {gpu.get('mode', 'auto')}")
        print(f"  Backend order: {', '.join(status.get('backend_order', [])) or 'none'}")
        decision = status.get("backend_decision", {}) if isinstance(status.get("backend_decision"), dict) else {}
        if decision.get("reason"):
            print(f"  Backend decision: {decision.get('selected', 'unknown')} ({decision['reason']})")
        print(f"  Task: {status['task']}")
        print(f"  Profile order: {', '.join(status['profile_order']) or 'none'}")
        print(f"  Bundle manifest: {'present' if status['manifest_found'] else 'missing'}")
        print(f"  Selected model: {'present' if status['model_found'] else 'missing'} ({status['model_profile']})")
        print(f"  Selected runtime: {status['selected_runtime'] or 'none'}")
        counts = status["cache_counts"]
        print(
            "  Cache: "
            f"skill-routing={counts.get('skill-routing', {}).get('accepted', 0)} accepted/"
            f"{counts.get('skill-routing', {}).get('rejected', 0)} rejected, "
            f"workflow-routing={counts.get('workflow-routing', {}).get('accepted', 0)} accepted/"
            f"{counts.get('workflow-routing', {}).get('rejected', 0)} rejected"
        )
        if status["issues"]:
            print("  Issues:")
            for issue in display_issue_rows(status["issues"]):
                print(f"    - {issue}")
        else:
            print("  Ready for automatic CPU-only use by sync and validate.")
    return 1 if status["issues"] and status["enabled"] else 0


def status_ready(status):
    return (
        bool(status.get("enabled"))
        and bool(status.get("manifest_found"))
        and bool(status.get("model_found"))
        and bool(status.get("selected_runtime"))
        and not status.get("issues")
    )


def bootstrap(
    *,
    root,
    task="skill-routing",
    profiles=None,
    download=True,
    force=False,
    run_model=False,
    max_download_gb=None,
    write_config=True,
):
    config_path = root / local_ai_routing.CONFIG_RELATIVE_PATH
    config_written = False
    local_settings_written = False
    policy_written = False
    secrets_example_written = False
    gitignore_updated = False
    if write_config:
        gitignore_updated = ensure_local_ai_gitignore(root)
    if not config_path.exists() and write_config:
        write_default_config(root, force=False)
        config_written = True
    if write_config:
        local_settings_written = write_default_local_settings(root, force=False)
        policy_written = policy_impl.write_default_policy(root, force=False)
        secrets_example_written = policy_impl.write_secrets_example(root, force=False)

    raw_config = load_raw_config(root)
    bootstrap_settings = normalize_bootstrap_config(raw_config.get("bootstrap"))
    selected_profiles = list(profiles or bootstrap_settings["default_profiles"])
    packages = packages_for_download(selected_profiles)
    estimated_gb = estimated_download_gb(packages)
    max_gb = float(max_download_gb if max_download_gb is not None else bootstrap_settings["max_download_gb"])
    before = build_status(root, task=task)
    ready_before = status_ready(before)
    downloaded = False
    blocked = []

    if not ready_before and download:
        if estimated_gb > max_gb:
            blocked.append(
                f"Estimated local AI download is {estimated_gb} GB, above max_download_gb {max_gb}."
            )
        else:
            download_bundle(root, force=force, profiles=selected_profiles)
            downloaded = True

    after = build_status(root, task=task)
    ready_after = status_ready(after)
    if ready_after and run_model:
        smoke_profile = after.get("model_profile")
        doctor_status = doctor(
            root,
            run_model=True,
            profile=str(smoke_profile) if smoke_profile else None,
        )
        if doctor_status != 0:
            blocked.append("Model smoke test failed during bootstrap.")
            ready_after = False

    if ready_after:
        next_action = "Use the normal repo commands; local AI is ready for automatic use."
    elif blocked:
        next_action = "Adjust .agents/local-ai.json bootstrap settings or run local-ai bootstrap with a higher limit."
    else:
        next_action = (
            "Run python -B .agents/manage.py local-ai bootstrap, or "
            "python -B .agents/skills/local-ai-helper/scripts/setup_local_ai.py --root . bootstrap."
        )
    return {
        "ready": ready_after,
        "config_written": config_written,
        "local_settings_written": local_settings_written,
        "policy_written": policy_written,
        "secrets_example_written": secrets_example_written,
        "gitignore_updated": gitignore_updated,
        "downloaded": downloaded,
        "profiles": selected_profiles,
        "estimated_download_gb": estimated_gb,
        "max_download_gb": max_gb,
        "issues": blocked + list(after.get("issues", [])),
        "next_action": next_action,
        "status": after,
    }


def print_bootstrap(
    root,
    *,
    task,
    profiles,
    download,
    force,
    run_model,
    max_download_gb,
    write_config,
    as_json,
):
    if as_json:
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            report = bootstrap(
                root=root,
                task=task,
                profiles=profiles or None,
                download=download,
                force=force,
                run_model=run_model,
                max_download_gb=max_download_gb,
                write_config=write_config,
            )
        log_lines = [line for line in captured.getvalue().splitlines() if line.strip()]
        if log_lines:
            report["log"] = log_lines
        print_json(report)
        return 0 if report["ready"] else 1

    report = bootstrap(
        root=root,
        task=task,
        profiles=profiles or None,
        download=download,
        force=force,
        run_model=run_model,
        max_download_gb=max_download_gb,
        write_config=write_config,
    )

    print("Local AI first-use bootstrap")
    print(f"  Config written: {report['config_written']}")
    print(f"  Gitignore updated: {report['gitignore_updated']}")
    print(f"  Downloaded payload: {report['downloaded']}")
    print(f"  Profiles: {', '.join(report['profiles'])}")
    print(f"  Estimated download: {report['estimated_download_gb']} GB")
    print(f"  Ready: {report['ready']}")
    if report["issues"]:
        print("  Issues:")
        for issue in display_issue_rows(report["issues"]):
            print(f"    - {issue}")
    print(f"  Next: {report['next_action']}")
    return 0 if report["ready"] else 1


def integration_summary(rows, *, compact=False):
    by_target = {}
    by_task = {}
    for row in rows:
        by_target[str(row.get("target", ""))] = by_target.get(str(row.get("target", "")), 0) + 1
        by_task[str(row.get("task", ""))] = by_task.get(str(row.get("task", "")), 0) + 1
    unavailable = [row for row in rows if not row.get("available")]
    summary = {
        "schema_version": 1,
        "tool": "local-ai-helper.integrations",
        "ok": True,
        "integration_count": len(rows),
        "available_count": sum(1 for row in rows if row.get("available")),
        "unavailable_count": len(unavailable),
        "targets": dict(sorted(by_target.items())),
        "tasks": dict(sorted(by_task.items())),
    }
    compact_rows = [
        {
            "id": row.get("id", ""),
            "target": row.get("target", ""),
            "task": row.get("task", ""),
            "available": bool(row.get("available", False)),
            "mode": row.get("mode", ""),
            "selected_profile": row.get("selected_profile", ""),
        }
        for row in rows
    ]
    summary["integrations"] = unavailable if compact else compact_rows
    return summary


def print_integrations(root, *, target, as_json, summary=False, compact=False):
    suggestions = integration_suggestions(target) + metadata_integration_suggestions(root, target)
    base_status = build_status(root, task="skill-routing")
    base_ready = (
        bool(base_status.get("enabled"))
        and bool(base_status.get("manifest_found"))
        and bool(base_status.get("model_found"))
        and bool(base_status.get("selected_runtime"))
        and not base_status.get("issues")
    )
    rows = []
    for item in suggestions:
        task_config = local_ai_routing.load_config(root, str(item["task"]))
        ready = base_ready and bool(task_config.get("enabled"))
        row = dict(item)
        row["available"] = ready
        row["mode"] = str(task_config.get("mode", base_status.get("mode", "disabled")))
        row["selected_profile"] = str(
            row.get("profile")
            or (task_config.get("profile_order") or [base_status.get("model_profile", "")])[0]
        )
        rows.append(row)

    if as_json:
        report = integration_summary(rows, compact=compact) if summary or compact else {"integrations": rows}
        print_json(report)
        return 0

    print("Local AI integration suggestions")
    if not rows:
        print("  No matching integrations.")
        return 0
    for row in rows:
        available = "available" if row["available"] else f"not ready ({row['mode']})"
        print(f"  {row['id']}: {row['target']} / {row['task']} - {available}")
        print(f"    Manager: {row['manager']}")
        print(f"    Command: {row['command']}")
        print(f"    Suggestion: {row['suggestion']}")
        print(f"    Guardrail: {row['guardrail']}")
    return 0


def print_models(root, *, as_json, summary=False, compact=False):
    config = local_ai_routing.load_config(root, "skill-routing")
    manifest, issues = local_ai_routing.load_bundle(root, config) if config.get("enabled") else (None, [])
    statuses = model_install_status(root, manifest)
    issues = issues + model_size_issues(statuses)
    if as_json:
        report = {"models": statuses, "issues": issues}
        output = catalog_summary(report, compact=compact) if summary or compact else report
        if summary or compact:
            output["tool"] = "local-ai-helper.models-summary"
        print_json(output)
        return 1 if issues else 0
    print("Local AI model profiles")
    for status in statuses:
        installed = "installed" if status["installed"] else "missing"
        roles = ", ".join(status["roles"])
        print(f"  {status['profile']}: {status['tier']}, {installed}, {status['quant']}, {status['license']}")
        print(f"    Kind: {status.get('kind', 'text')}")
        print(f"    Roles: {roles}")
        print(f"    Path: {status['path']}")
        print(f"    Use: {status['purpose']}")
    if issues:
        print("  Issues:")
        for issue in issues:
            print(f"    - {issue}")
    return 1 if issues else 0


def model_inventory_summary(report, *, compact=False):
    models = report.get("models") if isinstance(report.get("models"), list) else []
    active = [item for item in models if isinstance(item, dict) and item.get("active")]
    prune_safe = report.get("prune_safe_candidates")
    if not isinstance(prune_safe, list):
        prune_safe = []
    summary = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "local-ai-helper.models-inventory"),
        "ok": report.get("ok", False),
        "installed_size_bytes": report.get("installed_size_bytes", 0),
        "active_profiles": report.get("active_profiles", []),
        "active_count": len(active),
        "prune_safe_candidates": prune_safe,
        "prune_safe_count": len(prune_safe),
        "issues": report.get("issues", []),
        "next_command": report.get("next_command", ""),
        **model_rows_summary(models),
    }
    if not compact:
        summary["models"] = [
            {
                "profile": item.get("profile", ""),
                "kind": item.get("kind", ""),
                "installed": bool(item.get("installed", False)),
                "active": bool(item.get("active", False)),
                "size_bytes": item.get("size_bytes", 0),
            }
            for item in models
            if isinstance(item, dict)
        ]
    else:
        drop_keys(summary, "active_profiles", "installed_profiles", "missing_profiles", "by_state")
        drop_empty(summary, "issues", "prune_safe_candidates", "prune_safe_count")
        if bool(summary.get("ok")) and not summary.get("prune_safe_count"):
            drop_keys(summary, "next_command")
    return summary


def print_model_inventory(root, *, include_disk, as_json, summary=False, compact=False):
    report = model_inventory_report(root, include_disk=include_disk, include_prune=True)
    statuses = report["models"]
    if as_json:
        output = model_inventory_summary(report, compact=compact) if summary or compact else report
        print_json(output)
    else:
        print("Local AI model inventory")
        print(f"  Installed size: {report['installed_size_bytes']} bytes")
        for item in statuses:
            installed = "installed" if item.get("installed") else "missing"
            active = "active" if item.get("active") else "inactive"
            size = f", {item.get('size_bytes', 0)} bytes" if include_disk else ""
            print(f"  {item.get('profile')}: {installed}, {active}{size}")
        if report["prune_safe_candidates"]:
            print(f"  Prune-safe candidates: {', '.join(report['prune_safe_candidates'])}")
    return 0 if report["ok"] else 1


def active_model_profiles(config):
    return {
        profile
        for profile in (
            str(config.get("active_profile", "")),
            str(config.get("image_description_profile", "")),
            TEXT_TASK_PROFILE,
            EMBEDDING_PROFILE,
            VISION_PROFILE,
        )
        if profile
    }


def model_inventory_report(root, *, include_disk=True, include_prune=False):
    config = local_ai_routing.load_config(root, "skill-routing")
    manifest, issues = local_ai_routing.load_bundle(root, config) if config.get("enabled") else (None, [])
    statuses = model_install_status(root, manifest)
    issues = issues + model_size_issues(statuses)
    active_profiles = active_model_profiles(config)
    installed_total = 0
    for status in statuses:
        path = root / str(status.get("path", ""))
        size = path.stat().st_size if include_disk and path.exists() else 0
        status["size_bytes"] = size
        status["active"] = str(status.get("profile", "")) in active_profiles
        installed_total += size
        if include_prune:
            status["prune_safe_candidate"] = bool(status.get("installed")) and not bool(status.get("active"))
    report = setup_report(
        "models-inventory",
        ok=not issues,
        models=statuses,
        installed_size_bytes=installed_total,
        active_profiles=sorted(active_profiles),
        issues=issues,
    )
    if include_prune:
        report["prune_safe_candidates"] = [item["profile"] for item in statuses if item.get("prune_safe_candidate")]
        report["next_command"] = "python -B .agents/manage.py local-ai models prune --unused --json"
    return report


def models_compare_summary(report, *, compact=False):
    summary = model_inventory_summary(report, compact=True)
    by_kind = report.get("by_kind") if isinstance(report.get("by_kind"), dict) else {}
    summary.update(
        {
            "tool": report.get("tool", "local-ai-helper.models-compare-installed"),
            "installed_count": report.get("installed_count", 0),
            "installed_by_kind": {kind: len(rows) for kind, rows in sorted(by_kind.items()) if isinstance(rows, list)},
            "decision": report.get("decision", ""),
            "next_command": report.get("next_command", ""),
        }
    )
    if compact:
        drop_keys(summary, "by_state")
        if summary.get("decision"):
            summary["decision"] = "keep-defaults"
        if bool(summary.get("ok")):
            drop_keys(summary, "next_command")
    if not compact:
        summary["by_kind"] = {
            kind: [
                {
                    "profile": row.get("profile", ""),
                    "active": bool(row.get("active", False)),
                    "quant": row.get("quant", ""),
                    "size_bytes": row.get("size_bytes", 0),
                }
                for row in rows
                if isinstance(row, dict)
            ]
            for kind, rows in by_kind.items()
            if isinstance(rows, list)
        }
    return summary


def print_models_compare_installed(root, *, as_json, summary=False, compact=False):
    report = model_inventory_report(root, include_disk=True)
    installed = [item for item in report["models"] if item.get("installed")]
    by_kind = {}
    for item in installed:
        by_kind.setdefault(str(item.get("kind", "text")), []).append(item)
    report.update(
        {
            "tool": "local-ai-helper.models-compare-installed",
            "installed_count": len(installed),
            "by_kind": by_kind,
            "decision": "Installed models are operational smoke defaults only; fresh comparable benchmarks decide promotion.",
            "next_command": DETACHED_BENCH_COMMAND,
        }
    )
    if as_json:
        output = models_compare_summary(report, compact=compact) if summary or compact else report
        print_json(output)
    else:
        print("Installed local AI model comparison")
        print(f"  Installed: {len(installed)}")
        for kind, rows in sorted(by_kind.items()):
            print(f"  {kind}:")
            for row in rows:
                active = "active" if row.get("active") else "inactive"
                print(f"    - {row.get('profile')}: {active}, {row.get('quant')}, {row.get('size_bytes', 0)} bytes")
        print(f"  Decision: {report['decision']}")
    return 0 if report["ok"] else 1


def print_models_explain_defaults(root, *, as_json):
    config = local_ai_routing.load_config(root, "skill-routing")
    report = {
        "schema_version": 1,
        "tool": "local-ai-helper.models-explain-defaults",
        "ok": True,
        "defaults": {
            "text": config.get("active_profile", TEXT_TASK_PROFILE),
            "vision": config.get("image_description_profile", VISION_PROFILE),
        },
        "benchmark_candidates": {
            "embedding": EMBEDDING_PROFILE,
        },
        "reasons": [
            "Text default is the installed CPU-stable smoke route for bounded repo triage and planning.",
            "Vision default is the installed smoke route and is used only when deterministic document/image evidence is insufficient.",
            "Embedding profiles are optional benchmark candidates, not defaults or repository-search dependencies.",
        ],
        "override_guidance": [
            "Use local-ai select --profile <profile> for an explicit local choice.",
            "Use local-ai bench --detached-command for long CPU-only model comparisons outside Codex.",
        ],
    }
    if as_json:
        print_json(report)
    else:
        print("Local AI model defaults")
        for key, value in report["defaults"].items():
            print(f"  {key}: {value}")
        for key, value in report["benchmark_candidates"].items():
            print(f"  benchmark candidate ({key}): {value}")
        for reason in report["reasons"]:
            print(f"  - {reason}")
    return 0


def print_model_url_validation(
    *,
    profiles,
    timeout_seconds,
    as_json,
    summary=False,
    compact=False,
):
    report = model_url_validation_report(profiles=profiles, timeout_seconds=timeout_seconds)
    if as_json:
        if summary or compact:
            output = {
                "schema_version": 1,
                "tool": "local-ai-helper.model-url-validation-summary",
                "ok": bool(report.get("ok")),
                "checked_profile_count": report.get("checked_profile_count", 0),
                "failed_profile_count": sum(1 for row in report.get("profiles", []) if not row.get("ok")),
                "issues": report.get("issues", []),
            }
        else:
            output = report
        print_json(output)
    else:
        print("Local AI model URL validation")
        for row in report["profiles"]:
            print(f"  {row['profile']}: {'ok' if row['ok'] else 'failed'}")
            for check in row.get("checks", []):
                if not check.get("ok"):
                    print(f"    - {check.get('kind')}: {check.get('issue') or check.get('status')}")
    return 0 if report.get("ok") else 1


def shell_join(args):
    return " ".join(f'"{part}"' if " " in part else part for part in args)


def detached_benchmark_command(
    root,
    profiles,
    repetitions,
    standard_metrics,
    suite,
    *,
    backend=None,
    validate_model_urls=False,
):
    effective_backend = backend or "cpu"
    args = [
        sys.executable,
        "-B",
        ".agents/manage.py",
        "local-ai",
        "bench",
        "--run-model",
        "--repetitions",
        str(max(1, repetitions)),
    ]
    args.extend(["--backend", effective_backend])
    for profile in profiles:
        args.extend(["--profile", profile])
    if standard_metrics:
        args.append("--standard-metrics")
    if suite:
        args.extend(["--suite", suite])
    if validate_model_urls:
        args.append("--validate-model-urls")
    args.append("--json")
    quoted = shell_join(args)
    python_args = shell_join(args[1:])
    return {
        "schema_version": 1,
        "tool": "local-ai-helper.detached-benchmark-command",
        "ok": True,
        "gpu": "disabled" if effective_backend == "cpu" else effective_backend,
        "backend": effective_backend,
        "cwd": str(root),
        "command": quoted,
        "powershell_detached": f"Start-Process -WindowStyle Hidden -WorkingDirectory \"{root}\" -FilePath \"{sys.executable}\" -ArgumentList '{python_args}'",
        "posix_detached": f"cd \"{root}\" && nohup {quoted} > .agents/local-ai/cache/bench-detached.log 2>&1 &",
        "note": "Run outside the Codex foreground session for long model benchmarks; default detached mode is CPU-only.",
    }


def detached_benchmark_sweep_command(
    root,
    *,
    profiles,
    backends,
    repetitions,
    standard_metrics,
    validate_model_urls,
    suite=None,
):
    selected_backends = backends or ["cpu", "vulkan", "hip", "sycl"]
    commands = [
        detached_benchmark_command(
            root,
            profiles,
            repetitions,
            standard_metrics,
            suite or f"backend-{backend}-sweep",
            backend=backend,
            validate_model_urls=validate_model_urls,
        )
        for backend in selected_backends
    ]
    return {
        "schema_version": 1,
        "tool": "local-ai-helper.detached-benchmark-sweep-command",
        "ok": True,
        "cwd": str(root),
        "profiles": profiles,
        "backends": selected_backends,
        "commands": commands,
        "note": "Run these commands from separate terminals or as detached jobs; each command uses a process-local backend override.",
    }


def print_detached_benchmark_command(
    root,
    *,
    profiles,
    repetitions,
    standard_metrics,
    suite,
    backend=None,
    validate_model_urls=False,
    sweep_command=False,
    sweep_backends=None,
    as_json,
):
    if sweep_command:
        report = detached_benchmark_sweep_command(
            root,
            profiles=profiles,
            backends=sweep_backends or [],
            repetitions=repetitions,
            standard_metrics=standard_metrics,
            validate_model_urls=validate_model_urls,
            suite=suite,
        )
    else:
        report = detached_benchmark_command(
            root,
            profiles,
            repetitions,
            standard_metrics,
            suite,
            backend=backend,
            validate_model_urls=validate_model_urls,
        )
    if as_json:
        print_json(report)
    else:
        print("Local AI detached benchmark command" if not sweep_command else "Local AI detached benchmark sweep commands")
        print(f"  CWD: {report['cwd']}")
        if sweep_command:
            for command in report["commands"]:
                print(f"  {command['backend']}: {command['command']}")
        else:
            print(f"  Command: {report['command']}")
            print(f"  PowerShell detached: {report['powershell_detached']}")
            print(f"  POSIX detached: {report['posix_detached']}")
        print(f"  Note: {report['note']}")
    return 0


def runtime_doctor_report(root):
    config = local_ai_routing.load_config(root, "skill-routing")
    local_settings = local_ai_routing.read_local_settings(root)
    manifest, issues = local_ai_routing.load_bundle(root, config) if config.get("enabled") else (None, [])
    runtimes = []
    manifest_runtimes = manifest.get("runtimes", []) if isinstance(manifest, dict) else []
    for runtime in manifest_runtimes if isinstance(manifest_runtimes, list) else []:
        if not isinstance(runtime, dict):
            continue
        path = root / ".agents" / "local-ai" / "bundle" / str(runtime.get("path", ""))
        help_text = ""
        mtp_supported = False
        if path.exists():
            try:
                env = os.environ.copy()
                env.update(
                    {
                        "CUDA_VISIBLE_DEVICES": "",
                        "GGML_VK_VISIBLE_DEVICES": "",
                        "HIP_VISIBLE_DEVICES": "",
                        "SYCL_DEVICE_FILTER": "",
                    }
                )
                completed = subprocess.run(
                    [str(path), "--help"],
                    cwd=root,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=15,
                    env=env,
                )
                full_help_text = completed.stdout
                help_text = full_help_text[-12000:]
                normalized_help_text = full_help_text.lower()
                mtp_supported = "--spec-type" in normalized_help_text and "draft-mtp" in normalized_help_text
            except (OSError, subprocess.TimeoutExpired) as exc:
                help_text = str(exc)
        runtimes.append(
            {
                "backend": runtime.get("backend", "cpu"),
                "path": runtime.get("path", ""),
                "server_path": runtime.get("server_path", ""),
                "installed": path.exists(),
                "mtp_supported": mtp_supported,
                "help_probe_issue": "" if path.exists() else "runtime executable missing",
                "sha256": runtime.get("sha256", ""),
            }
        )
    return setup_report(
        "runtime-doctor",
        ok=not issues,
        gpu=config.get("gpu", {}),
        backend_order=config.get("backend_order", ["cpu"]),
        backend_quarantine=local_settings.get("backend_quarantine", []),
        backend_calibrations=local_settings.get("backend_calibrations", []),
        runtimes=runtimes,
        issues=issues,
        crash_safe_procedure=[
            "Use .agents/local-ai/local.settings.json gpu.mode=off to disable GPU even when detected.",
            "Run long MTP/model benchmarks with local-ai bench --detached-command from an external terminal.",
            "Record blocked or aborted runs as benchmark evidence instead of retrying foreground Codex sessions.",
        ],
        next_command=DETACHED_BENCH_COMMAND,
    )


def runtime_doctor_summary(report, *, compact=False):
    runtimes = report.get("runtimes") if isinstance(report.get("runtimes"), list) else []
    rows = [
        {
            "backend": runtime.get("backend", ""),
            "installed": bool(runtime.get("installed", False)),
            "mtp_supported": bool(runtime.get("mtp_supported", False)),
            "path": runtime.get("path", ""),
            "help_probe_issue": runtime.get("help_probe_issue", ""),
        }
        for runtime in runtimes
        if isinstance(runtime, dict)
    ]
    failed = [row for row in rows if not row["installed"] or bool(row["help_probe_issue"])]
    summary = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "local-ai-helper.runtime-doctor"),
        "ok": bool(report.get("ok", False)),
        "gpu": report.get("gpu", "disabled"),
        "runtime_count": len(rows),
        "installed_count": sum(1 for row in rows if row["installed"]),
        "mtp_supported_count": sum(1 for row in rows if row["mtp_supported"]),
        "failed_runtime_count": len(failed),
        "failed_runtimes": failed,
        "quarantined_backend_count": len(report.get("backend_quarantine", []) if isinstance(report.get("backend_quarantine"), list) else []),
        "calibrated_backend_count": len(report.get("backend_calibrations", []) if isinstance(report.get("backend_calibrations"), list) else []),
        "issues": report.get("issues", []),
        "next_command": report.get("next_command", ""),
    }
    if not compact:
        summary["runtimes"] = rows
        summary["backend_quarantine"] = report.get("backend_quarantine", [])
        summary["backend_calibrations"] = report.get("backend_calibrations", [])
        summary["crash_safe_procedure"] = report.get("crash_safe_procedure", [])
    return summary


def print_runtime_doctor(root, *, as_json, summary=False, compact=False):
    report = runtime_doctor_report(root)
    output_report = runtime_doctor_summary(report, compact=compact) if summary or compact else report
    if as_json:
        print_json(output_report)
    else:
        print("Local AI runtime doctor")
        gpu = report.get("gpu", {}) if isinstance(report.get("gpu"), dict) else {}
        print(f"  GPU mode: {gpu.get('mode', 'auto')}")
        print(f"  Backend order: {', '.join(report.get('backend_order', [])) or 'none'}")
        for runtime in report["runtimes"]:
            print(
                f"  {runtime.get('backend')}: {'installed' if runtime.get('installed') else 'missing'}, "
                f"MTP={'yes' if runtime.get('mtp_supported') else 'no'}"
            )
        print(f"  Next: {report['next_command']}")
    return 0 if report["ok"] else 1


def prune_unused_models(root, *, as_json=False):
    config = load_raw_config(root)
    active = set()
    for key in (
        "active_profile",
        "image_description_profile",
    ):
        value = str(config.get(key, "")).strip()
        if value:
            active.add(value)
    for key in (
        "primary_profiles",
        "optional_profiles",
        "embedding_profiles",
        "vision_profiles",
    ):
        raw = config.get(key, [])
        if isinstance(raw, list):
            active.update(str(item).strip() for item in raw if str(item).strip())
    routes = config.get("task_model_profiles", {})
    if isinstance(routes, dict):
        for raw in routes.values():
            if isinstance(raw, list):
                active.update(str(item).strip() for item in raw if str(item).strip())
            elif str(raw).strip():
                active.add(str(raw).strip())
    active.update(default_bootstrap_profile_names())

    manifest_path = root / local_ai_routing.DEFAULT_MANIFEST_PATH
    manifest = None
    issues = []
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            issues.append(f"manifest is not valid JSON: {exc}")
    removed = []
    protected = []
    if isinstance(manifest, dict):
        bundle_dir = manifest_path.parent
        for package in MODEL_PACKAGES:
            profile = str(package["profile"])
            model_path = bundle_dir / "models" / str(package["file"])
            if package.get("mmproj_file"):
                protected.append(f"models/{package['mmproj_file']}")
            if profile in active:
                protected.append(f"models/{package['file']}")
                continue
            if model_path.exists():
                model_path.unlink()
                removed.append(f"models/{package['file']}")
    if removed:
        write_manifest(root)
    report = {
        "schema_version": 1,
        "tool": "local-ai-helper.models-prune",
        "ok": not issues,
        "active_profiles": sorted(active),
        "removed": removed,
        "protected": sorted(set(protected)),
        "issues": issues,
    }
    if as_json:
        print_json(report)
    else:
        print("Local AI model prune")
        print(f"  Removed: {len(removed)}")
        for item in removed:
            print(f"    - {item}")
        print(f"  Protected active profiles: {', '.join(sorted(active))}")
        for issue in issues:
            print(f"  Issue: {issue}")
    return 0 if not issues else 1


def select_profile(root, *, profile, task):
    package = package_by_profile(profile)
    valid_profiles = set(profile_names())
    if package is None:
        print(f"{UNKNOWN_PROFILE_PREFIX}{profile!r}. Known profiles: {', '.join(sorted(valid_profiles))}")
        return 2
    if not bool(package.get("default_install", False)):
        manifest, _issues = local_ai_routing.load_bundle(
            root, {"bundle_manifest": local_ai_routing.DEFAULT_MANIFEST_PATH}
        )
        installed = False
        if manifest is not None:
            installed = any(
                item["profile"] == profile and item["installed"]
                for item in model_install_status(root, manifest)
            )
        if not installed:
            print(
                f"Optional local AI profile {profile!r} is not installed. "
                f"Run: python -B .agents/manage.py local-ai download --profile {profile}"
            )
            return 1
    config = load_raw_config(root)
    config.setdefault("enabled", True)
    config.setdefault("mode", "auto")
    config.setdefault("backend_order", ["auto", "cpu"])
    config.setdefault("model_profiles", local_ai_routing.DEFAULT_MODEL_PROFILES)
    config.setdefault("task_model_profiles", local_ai_routing.DEFAULT_TASK_MODEL_PROFILES)
    config.setdefault("primary_profiles", local_ai_routing.DEFAULT_PRIMARY_PROFILES)
    config.setdefault("optional_profiles", local_ai_routing.DEFAULT_OPTIONAL_PROFILES)
    config.setdefault("model_catalog", model_catalog_entries())
    config["active_profile"] = profile
    if task:
        task_routes = config.setdefault("task_model_profiles", {})
        if not isinstance(task_routes, dict):
            task_routes = {}
            config["task_model_profiles"] = task_routes
        existing = task_routes.get(task, [])
        profiles = [profile] + [str(item) for item in existing if str(item) != profile]
        task_routes[task] = profiles
    save_raw_config(root, config)
    suffix = f" for task {task}" if task else ""
    print(f"Selected local AI profile {profile}{suffix}.")
    return 0


def remove_profile(root, *, profile):
    package = package_by_profile(profile)
    if package is None:
        print(f"{UNKNOWN_PROFILE_PREFIX}{profile!r}. Known profiles: {', '.join(sorted(profile_names()))}")
        return 2
    if bool(package.get("default_install", False)):
        print(f"Refusing to remove primary local AI profile {profile!r}.")
        return 1
    bundle_dir = root / ".agents" / "local-ai" / "bundle"
    manifest = None
    try:
        manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = None
    manifest_entry = manifest_model_map(manifest).get(profile, {}) if isinstance(manifest, dict) else {}
    rel_model_path = str(manifest_entry.get("path", f"models/{package['file']}"))
    model_path = (bundle_dir / rel_model_path).resolve()
    models_dir = (bundle_dir / "models").resolve()
    try:
        model_path.relative_to(models_dir)
    except ValueError:
        print(f"Refusing to remove path outside local model directory: {model_path}")
        return 1
    model_path.unlink(missing_ok=True)
    write_manifest(root)
    print(f"Removed optional local AI profile {profile}.")
    return 0


def config_for_profile(root, profile, task="skill-routing"):
    config = local_ai_routing.load_config(root, task)
    config["profile_order"] = [profile]
    local_ai_routing.apply_profile_to_config(config, profile)
    return config


def repair_plan_report(root, *, profile=None):
    readiness = readiness_report(root, profile=profile)
    categories = readiness.get("categories", {}) if isinstance(readiness.get("categories"), dict) else {}
    commands = []
    if not categories.get("policy"):
        commands.append({"reason": "policy missing or disabled", "command": "python -B .agents/manage.py local-ai policy --write-default"})
    if not categories.get("runtime") or not categories.get("model"):
        commands.append({"reason": "runtime/model missing", "command": "python -B .agents/manage.py local-ai bootstrap"})
    if not categories.get("cache"):
        commands.append({"reason": "cache diagnostics need refresh", "command": "python -B .agents/manage.py local-ai doctor --quick --json"})
    commands.append({"reason": "deterministic fallback", "command": "python -B .agents/manage.py status"})
    return {
        "schema_version": 1,
        "tool": "local-ai-helper.repair-plan",
        "ok": True,
        "readiness": readiness,
        "commands": commands,
    }


def print_repair_plan(root, *, profile=None):
    report = repair_plan_report(root, profile=profile)
    print("Local AI repair plan")
    for item in report["commands"]:
        print(f"  - {item['reason']}: {item['command']}")
    return 0


def smoke_result(
    ok,
    status,
    mode,
    profile,
    kind,
    reason,
    issues,
    **extra,
):
    return {
        "ok": ok,
        "status": status,
        "mode": mode,
        "profile": profile,
        "kind": kind,
        **extra,
        "issues": issues,
        "reason": reason,
    }


def model_smoke_report(root, *, profile):
    smoke_profile = profile or local_ai_routing.DEFAULT_MODEL_PROFILE
    package = package_by_profile(smoke_profile) or {}
    profile_kind = str(package.get("kind", "text"))
    config = config_for_profile(root, smoke_profile)
    manifest, issues = local_ai_routing.load_bundle(root, config)
    if manifest is None:
        return smoke_result(False, "failed", "metadata", smoke_profile, profile_kind, "bundle is not valid", issues)
    model, model_issues = local_ai_routing.select_model(root, config, manifest)
    runtime, runtime_issues = local_ai_routing.select_runtime(root, config, manifest, check_only=False)
    issues.extend(model_issues + runtime_issues)
    if model is None or runtime is None:
        return smoke_result(False, "failed", "metadata", smoke_profile, profile_kind, "model or runtime is unavailable", issues)
    if profile_kind == "vision":
        return smoke_result(
            False,
            "manual-required",
            "vision",
            smoke_profile,
            profile_kind,
            "vision profiles need an image payload; use local-ai vision describe --image <jpg-or-png>",
            issues,
        )
    config["limits"]["timeout_seconds"] = max(int(config["limits"].get("timeout_seconds", 20)), 300)
    started = time.perf_counter()
    if profile_kind == "embedding":
        texts = list(EMBEDDING_BENCH_TEXTS)
        lease = {}
        vectors, embed_issues = embed_texts(
            root,
            texts,
            profile=smoke_profile,
            lease_report=lease,
        )
        elapsed = time.perf_counter() - started
        dimensions = [len(vector) for vector in vectors]
        dimensions_consistent = len(set(dimensions)) <= 1
        numeric_vectors = all(
            isinstance(value, (float, int))
            for vector in vectors
            for value in vector
        )
        smoke_issues = list(issues) + list(embed_issues)
        if len(vectors) != len(texts):
            smoke_issues.append("embedding response did not return one vector per input")
        if not dimensions or any(dimension <= 0 for dimension in dimensions):
            smoke_issues.append("embedding response returned empty vectors")
        if not dimensions_consistent:
            smoke_issues.append("embedding response returned inconsistent vector dimensions")
        if not numeric_vectors:
            smoke_issues.append("embedding response contained non-numeric values")
        ok = not smoke_issues
        return smoke_result(
            ok,
            "passed" if ok else "failed",
            "embedding",
            smoke_profile,
            profile_kind,
            "" if ok else "; ".join(smoke_issues[:3]),
            smoke_issues,
            vectors=len(vectors),
            texts=len(texts),
            dimensions=dimensions[:1],
            seconds=round(elapsed, 2),
            lease=lease,
            **lease_report_fields(lease),
        )
    config["limits"]["output_tokens"] = min(int(config["limits"].get("output_tokens", 96)), 64)
    item = {
        "id": f"smoke-test-{smoke_profile}",
        "name": "smoke-test",
        "task": "skill-routing",
        "category": "General",
        "description": "Use when checking a CPU-only local AI routing setup.",
        "summary": "Local AI setup smoke test.",
        "source_paths": [],
    }
    accepted, fields, confidence, reason = local_ai_routing.run_model(
        root=root,
        task="skill-routing",
        item=item,
        allowed_categories=["General", "AI Agents"],
        model=model,
        runtime=runtime,
        config=config,
    )
    elapsed = time.perf_counter() - started
    return smoke_result(
        accepted,
        "passed" if accepted else "failed",
        "text-json",
        smoke_profile,
        profile_kind,
        "" if accepted else reason,
        issues if accepted else issues + [reason],
        fields=fields,
        confidence=confidence,
        seconds=round(elapsed, 2),
        lease=dict(config.get("lease", {})),
        **lease_report_fields(config.get("lease", {})),
    )


def apply_benchmark_backend_override(config, backend):
    selected = str(backend or "").strip().lower()
    if not selected or selected == "local-settings":
        return
    if selected == "auto":
        return
    config["backend_override"] = selected
    if selected == "cpu":
        config["backend_order"] = ["cpu"]
        config["configured_backend_order"] = ["cpu"]
        gpu = dict(config.get("gpu", {})) if isinstance(config.get("gpu"), dict) else {}
        gpu["mode"] = "off"
        config["gpu"] = gpu
        return
    if selected not in local_ai_routing.GPU_BACKENDS:
        raise RuntimeError(f"Unsupported benchmark backend: {backend}")
    gpu = dict(config.get("gpu", {})) if isinstance(config.get("gpu"), dict) else {}
    gpu["mode"] = "force"
    gpu["allow_integrated"] = True
    preferred = [selected, "cpu"]
    gpu["preferred_backends"] = preferred
    config["gpu"] = gpu
    config["backend_order"] = preferred
    config["configured_backend_order"] = preferred


def benchmark_run_config(
    runtime,
    config,
    *,
    output_cap=None,
    suite="",
):
    limits = config.get("limits", {}) if isinstance(config.get("limits"), dict) else {}
    runtime_path = str(runtime.get("resolved_path", "") or "")
    runtime_version = ""
    if runtime_path:
        try:
            completed = subprocess.run(
                [runtime_path, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
            runtime_version = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else ""
        except (OSError, subprocess.TimeoutExpired):
            runtime_version = ""
    report = {
        "backend": runtime.get("backend", "cpu"),
        "runtime_path": runtime_path,
        "runtime_version": runtime_version,
        "threads": int(limits.get("threads", 8)),
        "context_size": int(limits.get("context_tokens", 4096)),
        "batch_size": int(limits.get("batch_size", 512)),
        "kv_cache": {
            "type_k": limits.get("cache_type_k", ""),
            "type_v": limits.get("cache_type_v", ""),
        },
        "prompt_version": local_ai_routing.PROMPT_VERSION,
        "output_cap": int(limits.get("output_tokens", 128)) if output_cap is None else output_cap,
    }
    if suite:
        report["suite"] = suite
    return report


def benchmark_backend_fields(runtime, config, requested_backend, setup_issues=None):
    return {
        "requested_backend": requested_backend or "local-settings",
        "effective_backend": str((runtime or {}).get("backend", "")),
        "backend_decision": dict(config.get("backend_decision", {}))
        if isinstance(config.get("backend_decision"), dict)
        else {},
        "setup_issues": [str(issue) for issue in (setup_issues or []) if str(issue).strip()],
    }


def doctor_report(root, *, run_model, profile):
    readiness = readiness_report(root, profile=profile)
    policy = model_policy_report(root)
    checks = [
        {"name": "readiness", "ok": bool(readiness.get("ok")), "result": readiness},
        {"name": "policy", "ok": bool(policy.get("ok")), "result": policy},
    ]
    model_smoke = {
        "ok": True,
        "status": "skipped",
        "reason": "model smoke test not requested",
    }
    if run_model:
        model_smoke = model_smoke_report(root, profile=profile)
    checks.append({"name": "model_smoke", "ok": bool(model_smoke.get("ok")), "result": model_smoke})
    ok = all(bool(check.get("ok")) for check in checks)
    if ok and run_model:
        next_command = "python -B .agents/manage.py local-ai bench --standard-metrics --repetitions 3"
    elif ok:
        next_command = "python -B .agents/manage.py local-ai doctor --run-model"
    else:
        next_command = "python -B .agents/manage.py local-ai readiness --json"
    return setup_report(
        "doctor",
        ok=ok,
        status="passed" if ok else "issues-found",
        checks=checks,
        model_smoke=model_smoke,
        next_command=next_command,
    )


def doctor_summary(report, *, compact=False):
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    rows = []
    failed = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        result = check.get("result") if isinstance(check.get("result"), dict) else {}
        row = {
            "name": check.get("name", ""),
            "ok": bool(check.get("ok")),
            "status": result.get("status", ""),
        }
        rows.append(row)
        if not check.get("ok"):
            failed.append(row)
    summary = {
        "schema_version": report.get("schema_version", 1),
        "tool": "local-ai-helper.doctor-summary",
        "ok": bool(report.get("ok", False)),
        "status": report.get("status", ""),
        "check_count": len(rows),
        "failed_check_count": len(failed),
        "failed_checks": failed,
        "model_smoke": report.get("model_smoke", {}),
        "next_command": report.get("next_command", ""),
    }
    if not compact:
        summary["checks"] = rows
    else:
        drop_empty(summary, "failed_checks")
        model_smoke = summary.get("model_smoke") if isinstance(summary.get("model_smoke"), dict) else {}
        summary["model_smoke_status"] = model_smoke.get("status", "")
        drop_keys(summary, "model_smoke")
        if summary.get("next_command") == "python -B .agents/manage.py local-ai readiness --json":
            summary["next_command"] = "python -B .agents/manage.py local-ai readiness --summary --compact --json"
        if bool(summary.get("ok")):
            drop_keys(summary, "next_command")
    return summary


def doctor(
    root,
    *,
    run_model,
    profile,
    repair_plan=False,
    as_json=False,
    summary=False,
    compact=False,
):
    if repair_plan:
        return print_repair_plan(root, profile=profile)
    if as_json or summary or compact:
        report = doctor_report(root, run_model=run_model, profile=profile)
        output_report = doctor_summary(report, compact=compact) if summary or compact else report
        if as_json:
            print_json(output_report)
        else:
            print("Local AI doctor summary")
            print(f"  Status: {output_report['status']}")
            print(f"  Failed checks: {output_report['failed_check_count']}")
            next_command = output_report.get("next_command")
            if next_command:
                print(f"  Next: {next_command}")
        return 0 if report.get("ok") else 1
    status_code = print_status(root, as_json=False, profile=profile)
    if status_code != 0 or not run_model:
        return status_code
    model_smoke = model_smoke_report(root, profile=profile)
    seconds = float(model_smoke.get("seconds", 0.0) or 0.0)
    if not model_smoke.get("ok"):
        reason = str(model_smoke.get("reason", "")).strip() or "model smoke test failed"
        print(f"Model smoke test rejected output after {seconds:.1f}s: {reason}")
        for issue in model_smoke.get("issues", []):
            print(f"  - {issue}")
        return 1
    if model_smoke.get("mode") == "embedding":
        dimensions = model_smoke.get("dimensions", [])
        dimension = dimensions[0] if dimensions else "unknown"
        print(
            f"Embedding smoke test accepted in {seconds:.1f}s: "
            f"{model_smoke.get('vectors', 0)}/{model_smoke.get('texts', 0)} vectors, "
            f"dimension {dimension}"
        )
    else:
        fields = model_smoke.get("fields", {})
        print(f"Model smoke test accepted in {seconds:.1f}s: {json.dumps(fields, sort_keys=True)}")
    return 0


def bench(
    root,
    *,
    run_model,
    profiles,
    repetitions=1,
    cold=False,
    warm=False,
    standard_metrics=False,
    suite=None,
    backend=None,
    validate_model_urls=False,
    as_json=False,
):
    selected_profiles = profiles or profile_names(include_optional=False)
    url_validation = (
        model_url_validation_report(profiles=selected_profiles, timeout_seconds=10)
        if validate_model_urls
        else {"ok": True, "issues": []}
    )
    if validate_model_urls and not bool(url_validation.get("ok")):
        report = {
            "schema_version": 1,
            "tool": "local-ai-helper.bench",
            "ok": False,
            "status": "issues-found",
            "run_model": run_model,
            "suite": suite or "built-in-routing-fixtures",
            "standard_metrics": standard_metrics,
            "backend_override": backend or "local-settings",
            "url_validation": url_validation,
            "host_memory_topology": _resources_impl.host_memory_topology(),
            "rows": [
                {"profile": profile, "ok": False, "issues": list(url_validation.get("issues", []))}
                for profile in selected_profiles
            ],
        }
        if as_json:
            print_json(report)
        else:
            print("Local AI benchmark")
            print("  Model URL validation failed before benchmark execution.")
            for issue in url_validation.get("issues", []):
                print(f"    - {issue}")
        return 1
    manifest_failures = 0
    rows = []
    repetitions = max(1, repetitions)
    for profile in selected_profiles:
        package = package_by_profile(profile) or {}
        profile_kind = str(package.get("kind", "text"))
        config = config_for_profile(root, profile)
        apply_benchmark_backend_override(config, backend)
        manifest, issues = local_ai_routing.load_bundle(root, config)
        model, model_issues = (
            local_ai_routing.select_model(root, config, manifest) if manifest else (None, [])
        )
        runtime, runtime_issues = (
            local_ai_routing.select_runtime(root, config, manifest, check_only=not run_model)
            if manifest
            else (None, [])
        )
        issues.extend(model_issues + runtime_issues)
        if model is None or runtime is None:
            manifest_failures += 1
            rows.append(
                {
                    "profile": profile,
                    "ok": False,
                    "issues": issues,
                    **benchmark_backend_fields(runtime or {}, config, backend, issues),
                }
            )
            continue
        config["limits"]["output_tokens"] = min(int(config["limits"].get("output_tokens", 96)), 128)
        config["limits"]["timeout_seconds"] = max(int(config["limits"].get("timeout_seconds", 20)), 300)
        backend_fields = benchmark_backend_fields(runtime, config, backend, issues)
        if not run_model:
            rows.append(
                {
                    "profile": profile,
                    "ok": True,
                    "mode": "metadata",
                    "kind": profile_kind,
                    "backend": runtime["backend"],
                    "run_config": benchmark_run_config(runtime, config),
                    **backend_fields,
                }
            )
            continue
        if profile_kind == "embedding":
            successful_repetitions = 0
            elapsed_total = 0.0
            samples = []
            reasons = []
            dimensions = []
            vectors_total = 0
            lease = {}
            for repetition in range(repetitions):
                started = time.perf_counter()
                vectors, embed_issues = embed_texts(
                    root,
                    list(EMBEDDING_BENCH_TEXTS),
                    profile=profile,
                    lease_report=lease,
                )
                elapsed = time.perf_counter() - started
                elapsed_total += elapsed
                metric_sample = benchmark_metrics.metrics_from_elapsed(
                    elapsed,
                    cold_start=cold or (not warm and repetition == 0),
                    warm_cache=warm or repetition > 0,
                    repetitions=repetitions,
                )
                if vectors:
                    metric_sample["request_throughput_rps"] = round(len(vectors) / max(elapsed, 0.0001), 4)
                samples.append(metric_sample)
                if len(vectors) == len(EMBEDDING_BENCH_TEXTS) and not embed_issues:
                    successful_repetitions += 1
                vectors_total += len(vectors)
                dimensions.extend(len(vector) for vector in vectors[:1])
                reasons.extend(embed_issues)
            rows.append(
                {
                    "profile": profile,
                    "ok": successful_repetitions == repetitions,
                    "mode": "embedding",
                    "kind": profile_kind,
                    "accepted": successful_repetitions,
                    "total": repetitions,
                    "vectors": vectors_total,
                    "texts_per_repetition": len(EMBEDDING_BENCH_TEXTS),
                    "dimensions": dimensions[:1],
                    "seconds": round(elapsed_total, 2),
                    "issues": reasons,
                    "metrics_standard": benchmark_metrics.aggregate_metrics(samples) if standard_metrics else {},
                    "run_config": benchmark_run_config(
                        runtime,
                        config,
                        output_cap=0,
                        suite=suite or "built-in-embedding-fixtures",
                    ),
                    **backend_fields,
                    "lease": lease,
                    **lease_report_fields(lease),
                }
            )
            continue
        accepted = 0
        elapsed_total = 0.0
        reasons = []
        samples = []
        for fixture in BENCH_FIXTURES:
            for repetition in range(repetitions):
                started = time.perf_counter()
                ok, _fields, _confidence, reason = local_ai_routing.run_model(
                    root=root,
                    task=str(fixture["task"]),
                    item=dict(fixture["item"]),
                    allowed_categories=list(fixture["allowed_categories"]),
                    model=model,
                    runtime=runtime,
                    config=config,
                )
                elapsed = time.perf_counter() - started
                elapsed_total += elapsed
                samples.append(
                    benchmark_metrics.metrics_from_elapsed(
                        elapsed,
                        cold_start=cold or (not warm and repetition == 0),
                        warm_cache=warm or repetition > 0,
                        repetitions=repetitions,
                    )
                )
                if ok:
                    accepted += 1
                else:
                    reasons.append(reason)
        rows.append(
            {
                "profile": profile,
                "ok": True,
                "mode": "model",
                "kind": profile_kind,
                "accepted": accepted,
                "total": len(BENCH_FIXTURES) * repetitions,
                "seconds": round(elapsed_total, 2),
                "issues": reasons,
                "metrics_standard": benchmark_metrics.aggregate_metrics(samples) if standard_metrics else {},
                "run_config": benchmark_run_config(runtime, config, suite=suite or "built-in-routing-fixtures"),
                **backend_fields,
                "lease": dict(config.get("lease", {})),
                **lease_report_fields(config.get("lease", {})),
            }
        )

    report = setup_report(
        "bench",
        ok=manifest_failures == 0,
        status="passed" if manifest_failures == 0 else "issues-found",
        run_model=run_model,
        suite=suite or "built-in-routing-fixtures",
        standard_metrics=standard_metrics,
        backend_override=backend or "local-settings",
        url_validation=url_validation if validate_model_urls else {},
        host_memory_topology=_resources_impl.host_memory_topology(),
        rows=rows,
    )
    if as_json:
        print_json(report)
        return 1 if manifest_failures else 0
    print("Local AI benchmark")
    if not run_model:
        print("  Metadata mode only. Add --run-model to measure CPU inference latency.")
    for row in rows:
        if not row["ok"]:
            print(f"  {row['profile']}: unavailable")
            for issue in row.get("issues", []):
                print(f"    - {issue}")
        elif row["mode"] == "metadata":
            print(f"  {row['profile']}: ready on {row['backend']}")
        elif row["mode"] == "embedding":
            print(
                f"  {row['profile']}: {row['accepted']}/{row['total']} embedding runs "
                f"returned {row.get('vectors', 0)} vector(s) in {row['seconds']}s"
            )
            if row.get("dimensions"):
                print(f"    dimensions: {', '.join(str(item) for item in row['dimensions'])}")
            if standard_metrics and row.get("metrics_standard"):
                metrics = row["metrics_standard"]
                print(
                    "    standard metrics: "
                    f"e2e={metrics.get('e2e_latency_ms', '')}ms "
                    f"p95={metrics.get('p95', {}).get('e2e_latency_ms', '')}ms "
                    f"vectors/s={metrics.get('request_throughput_rps', '')}"
                )
            for issue in row.get("issues", []):
                print(f"    - {issue}")
        else:
            print(
                f"  {row['profile']}: {row['accepted']}/{row['total']} accepted "
                f"in {row['seconds']}s"
            )
            if standard_metrics and row.get("metrics_standard"):
                metrics = row["metrics_standard"]
                print(
                    "    standard metrics: "
                    f"e2e={metrics.get('e2e_latency_ms', '')}ms "
                    f"p95={metrics.get('p95', {}).get('e2e_latency_ms', '')}ms"
                )
            for issue in row.get("issues", []):
                print(f"    - {issue}")
    return 1 if manifest_failures else 0


def server_state_path(root):
    return root / ".agents" / "local-ai" / "cache" / "server.json"


def read_server_state(root):
    path = server_state_path(root)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def process_running(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def stop_server(root):
    state = read_server_state(root)
    if not state:
        print("No local AI server state is recorded.")
        return 0
    pid = int(state.get("pid", 0))
    profile = str(state.get("profile", ""))
    if pid <= 0:
        server_state_path(root).unlink(missing_ok=True)
        print("Removed invalid local AI server state.")
        return 0
    if not process_running(pid):
        server_state_path(root).unlink(missing_ok=True)
        model_lease.release_recorded_server_lease(root, pid=pid, profile=profile)
        print(f"Local AI server process {pid} is not running; removed stale state.")
        return 0
    if not model_lease.recorded_server_lease_matches(root, pid=pid, profile=profile):
        print(
            "Refusing to stop the recorded PID because its persistent-server lease "
            "is missing or mismatched."
        )
        return 1
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
    else:
        os.kill(pid, signal.SIGTERM)
    server_state_path(root).unlink(missing_ok=True)
    model_lease.release_recorded_server_lease(root, pid=pid, profile=profile)
    print(f"Stopped local AI server process {pid}.")
    return 0


def start_server(root, *, profile):
    if stop_server(root) != 0:
        return 1
    with model_lease.exclusive_lease(
        root,
        profile=profile,
        role="text",
        priority="interactive",
        command_kind="server",
        timeout_ms=0,
    ) as lease:
        if not lease.acquired:
            print("Cannot start server: local-ai-busy; deterministic fallback required.")
            return 1
        load_started = time.perf_counter()
        config = config_for_profile(root, profile)
        manifest, issues = local_ai_routing.load_bundle(root, config)
        if manifest is None:
            print("Cannot start server: bundle manifest is not valid.")
            for issue in issues:
                print(f"  - {issue}")
            return 1
        model, model_issues = local_ai_routing.select_model(root, config, manifest)
        runtime, runtime_issues = local_ai_routing.select_runtime(root, config, manifest, check_only=False)
        issues.extend(model_issues + runtime_issues)
        if model is None or runtime is None:
            print("Cannot start server.")
            for issue in issues:
                print(f"  - {issue}")
            return 1
        command = local_ai_routing.llama_server_command(runtime, model, config)
        log_dir = root / ".agents" / "local-ai" / "cache" / "server"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{profile}.log"
        with log_path.open("a", encoding="utf-8") as log_handle:
            process = subprocess.Popen(
                command,
                cwd=root,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
        lease.load_ms = int(max(0.0, time.perf_counter() - load_started) * 1000)
        state = {
            "pid": process.pid,
            "profile": profile,
            "backend": runtime.get("backend", "cpu"),
            "command": command,
            "log": relative(root, log_path),
            "started_at_unix": int(time.time()),
            **lease_report_fields(lease.report()),
        }
        state_path = server_state_path(root)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            state_path.write_text(
                json.dumps(state, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            if not lease.transfer_to_pid(process.pid):
                raise RuntimeError("persistent server lease transfer failed")
        except Exception as exc:
            if process_running(process.pid):
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False)
                else:
                    os.kill(process.pid, signal.SIGTERM)
            state_path.unlink(missing_ok=True)
            print(f"Cannot start server: {exc}")
            return 1
        print(f"Started local AI server pid {process.pid} with profile {profile}.")
        print(f"Log: {relative(root, log_path)}")
        return 0


def server_status(root):
    state = read_server_state(root)
    if not state:
        print("No local AI server is recorded.")
        return 0
    pid = int(state.get("pid", 0))
    running = pid > 0 and process_running(pid)
    print(f"Local AI server: {'running' if running else 'stale'}")
    print(f"  pid: {pid}")
    print(f"  profile: {state.get('profile', 'unknown')}")
    print(f"  backend: {state.get('backend', 'unknown')}")
    print(f"  log: {state.get('log', '')}")
    return 0 if running else 1


def resolve_repo_file(root, value, *, allowed_suffixes=None):
    try:
        path = local_ai_routing.resolve_repo_request_path(root, value)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if not path.is_file():
        raise RuntimeError(f"path is not a file: {value}")
    if allowed_suffixes is not None and path.suffix.lower() not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        raise RuntimeError(f"expected file suffix in [{allowed}]: {value}")
    return path


def read_bounded_text_file(root, value, *, max_bytes=MAX_DAILY_INPUT_BYTES):
    path = resolve_repo_file(root, value)
    data = path.read_bytes()[: max_bytes + 1]
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"path is not valid UTF-8 text: {relative(root, path)}") from exc
    return text, relative(root, path), truncated


def read_daily_inputs(root, inputs, stdin_text):
    if not inputs:
        raise RuntimeError("at least one --input value is required")
    docs = []
    input_paths = []
    for value in inputs:
        if value == "-":
            text = stdin_text[:MAX_DAILY_INPUT_BYTES]
            docs.append({"path": "<stdin>", "text": text, "truncated": len(stdin_text) > MAX_DAILY_INPUT_BYTES})
            input_paths.append("<stdin>")
            continue
        text, rel_path, truncated = read_bounded_text_file(root, value)
        docs.append({"path": rel_path, "text": text, "truncated": truncated})
        input_paths.append(rel_path)
    return docs, input_paths


def resolve_model_and_runtime(
    root,
    *,
    task,
    profile,
    check_only=False,
):
    bootstrap_profiles = [profile]
    if profile == EMBEDDING_PROFILE and TEXT_TASK_PROFILE not in bootstrap_profiles:
        bootstrap_profiles.append(TEXT_TASK_PROFILE)
    config = local_ai_routing.load_config(root, task)
    if not config.get("enabled") and str(config.get("status", "")) == "policy-disabled":
        return None, None, config, [str(config.get("reason", "Local AI is disabled by policy."))]
    if not config.get("enabled"):
        raw_config = load_raw_config(root)
        bootstrap_settings = normalize_bootstrap_config(raw_config.get("bootstrap"))
        if bootstrap_settings["auto_download"] in local_ai_routing.BOOTSTRAP_AUTO_DOWNLOAD_VALUES:
            bootstrap(root=root, task=task, profiles=bootstrap_profiles, download=True, write_config=True)
            config = local_ai_routing.load_config(root, task)
    if not config.get("enabled"):
        return None, None, config, [str(config.get("reason", "Local AI is disabled."))]
    config["profile_order"] = [profile]
    local_ai_routing.apply_profile_to_config(config, profile)
    manifest, issues = local_ai_routing.load_bundle(root, config)
    if manifest is None:
        return None, None, config, issues
    model, model_issues = local_ai_routing.select_model(root, config, manifest)
    if model is None:
        raw_config = load_raw_config(root)
        bootstrap_settings = normalize_bootstrap_config(raw_config.get("bootstrap"))
        if bootstrap_settings["auto_download"] in local_ai_routing.BOOTSTRAP_AUTO_DOWNLOAD_VALUES:
            estimated_gb = estimated_download_gb(packages_for_download(bootstrap_profiles))
            if estimated_gb <= float(bootstrap_settings.get("max_download_gb", 20)):
                download_bundle(root, force=False, profiles=bootstrap_profiles)
                manifest, issues = local_ai_routing.load_bundle(root, config)
                if manifest is not None:
                    model, model_issues = local_ai_routing.select_model(root, config, manifest)
    if model is None:
        return None, None, config, model_issues
    runtime, runtime_issues = local_ai_routing.select_runtime(root, config, manifest, check_only=check_only)
    if runtime is None:
        return model, None, config, runtime_issues
    return model, runtime, config, []


def build_parser():
    return setup_parser.build_parser(
        description=__doc__,
        root_default=str(default_root()),
        daily_text_tasks=DAILY_TEXT_TASKS,
        download_profiles=download_profile_names(),
        profile_choices=profile_names(),
        approved_owners=policy_impl.APPROVED_OWNERS,
        default_model_profile=local_ai_routing.DEFAULT_MODEL_PROFILE,
    )


from local_ai_support import daily_impl as _daily_impl
from local_ai_support import candidate_screening as _candidate_screening
from local_ai_support import document_impl as _document_impl
from local_ai_support import resources_impl as _resources_impl
from local_ai_support import vision_impl as _vision_impl


def local_model_candidate_report(root, *, candidate_path, resources=None):
    path = Path(candidate_path).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError("candidate JSON must stay inside the repository root") from exc
    try:
        candidate = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise RuntimeError(f"candidate JSON could not be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"candidate JSON is invalid: {exc}") from exc
    try:
        raw_config = json.loads((root / ".agents/local-ai.json").read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        raw_config = DEFAULT_CONFIG
    bootstrap = normalize_bootstrap_config(
        raw_config.get("bootstrap") if isinstance(raw_config, dict) else {}
    )
    resource_evidence = resources if isinstance(resources, dict) else _resources_impl.resource_report(root)
    return _candidate_screening.evaluate_candidate(
        candidate,
        resources=resource_evidence,
        policy={"max_download_gb": bootstrap.get("max_download_gb", 20)},
        supported_runtime_families={"llama.cpp": LLAMA_RELEASE},
    )


def print_local_model_candidate_report(
    root,
    *,
    candidate_path,
    as_json,
    summary=False,
    compact=False,
):
    report = local_model_candidate_report(root, candidate_path=candidate_path)
    output = dict(report)
    if summary or compact:
        output.pop("resources", None)
        if compact and not output.get("reasons"):
            output.pop("reasons", None)
    if as_json:
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Local model candidate: {report.get('candidate_id', '')}")
        print(f"Decision: {report.get('decision', '')}")
        for reason in report.get("reasons", []):
            print(f"- {reason}")
    return 0 if report.get("decision") in {"eligible", "benchmark-only"} else 1

daily_task_prompt = _daily_impl.daily_task_prompt
daily_report_json_schema = _daily_impl.daily_report_json_schema
bounded_confidence = _daily_impl.bounded_confidence
report_from_model_output = _daily_impl.report_from_model_output
failure_class = _daily_impl.failure_class
retry_decision = _daily_impl.retry_decision
run_text_completion = _daily_impl.run_text_completion
run_daily_text_model = _daily_impl.run_daily_text_model
daily_task_report = _daily_impl.daily_task_report
print_daily_task = _daily_impl.print_daily_task

print_resources = _resources_impl.print_resources
document_inspect_report = _document_impl.document_inspect_report
print_document_inspect = _document_impl.print_document_inspect

parse_pages = _vision_impl.parse_pages
vision_model_paths = _vision_impl.vision_model_paths
clean_model_text = _vision_impl.clean_model_text
run_vision_model = _vision_impl.run_vision_model
vision_describe_report = _vision_impl.vision_describe_report
render_pdf_pages = _vision_impl.render_pdf_pages
vision_pdf_report = _vision_impl.vision_pdf_report
print_vision_describe = _vision_impl.print_vision_describe
print_vision_pdf = _vision_impl.print_vision_pdf


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    try:
        if args.command == "status":
            return print_status(
                root,
                as_json=args.json,
                task=args.task,
                profile=args.profile,
                summary=bool(args.summary or args.compact),
                compact=bool(args.compact),
            )
        if args.command == "readiness":
            return print_readiness_report(
                root,
                task=args.task,
                profile=args.profile,
                as_json=args.json,
                summary=bool(args.summary or args.compact),
                compact=bool(args.compact),
            )
        if args.command == "configure":
            performance = {
                name: getattr(args, name)
                for name in (
                    "threads",
                    "threads_batch",
                    "context_tokens",
                    "output_tokens",
                    "batch_size",
                    "ubatch_size",
                    "timeout_seconds",
                    "parallel_slots",
                    "cache_type_k",
                    "cache_type_v",
                )
                if getattr(args, name) is not None
            }
            return print_local_ai_configure(
                root,
                scope=args.scope,
                route_values=list(args.route),
                group_route_values=list(args.group_route),
                backend_order=args.backend_order,
                gpu_mode=args.gpu_mode,
                gpu_layers=args.gpu_layers,
                allow_integrated=bool(args.allow_integrated),
                performance=performance,
                apply_requested=bool(args.apply),
                as_json=bool(args.json),
            )
        if args.command == "config":
            if args.config_action == "explain":
                return print_effective_config_explanation(
                    root,
                    task=args.task,
                    as_json=bool(args.json),
                )
        if args.command == "policy":
            use_case = args.check_use_case
            if args.policy_action == "explain":
                use_case = args.policy_use_case
                if not use_case:
                    print("ERROR: local-ai policy explain requires a use case", file=sys.stderr)
                    return 2
            return print_policy(
                root,
                as_json=args.json,
                use_case=use_case,
                owner=args.owner,
                write_default=args.write_default,
                summary=bool(args.summary or args.compact),
                compact=bool(args.compact),
            )
        if args.command in {"bootstrap", "ensure"}:
            return print_bootstrap(
                root,
                task=args.task,
                profiles=list(args.profile),
                download=not args.no_download and not args.dry_run,
                force=args.force,
                run_model=args.run_model,
                max_download_gb=args.max_download_gb,
                write_config=not args.dry_run,
                as_json=args.json,
            )
        if args.command == "integrations":
            return print_integrations(
                root,
                target=args.target,
                as_json=args.json,
                summary=bool(args.summary or args.compact),
                compact=bool(args.compact),
            )
        if args.command == "download":
            download_bundle(root, force=args.force, profiles=list(args.profile))
            return print_status(root, as_json=False)
        if args.command == "catalog":
            return print_catalog(root, as_json=args.json, summary=bool(args.summary or args.compact), compact=bool(args.compact))
        if args.command == "doctor":
            return doctor(
                root,
                run_model=args.run_model or args.full,
                profile=args.profile,
                repair_plan=args.repair_plan,
                as_json=args.json,
                summary=bool(args.summary or args.compact),
                compact=bool(args.compact),
            )
        if args.command == "models":
            if args.models_action == "prune":
                if not args.unused:
                    print("ERROR: models prune requires --unused", file=sys.stderr)
                    return 2
                return prune_unused_models(root, as_json=args.json)
            if args.models_action == "inventory":
                return print_model_inventory(
                    root,
                    include_disk=args.disk,
                    as_json=args.json,
                    summary=bool(args.summary or args.compact),
                    compact=bool(args.compact),
                )
            if args.models_action == "compare-installed":
                return print_models_compare_installed(
                    root,
                    as_json=args.json,
                    summary=bool(args.summary or args.compact),
                    compact=bool(args.compact),
                )
            if args.models_action == "explain-defaults":
                return print_models_explain_defaults(root, as_json=args.json)
            if args.models_action == "validate-urls":
                return print_model_url_validation(
                    profiles=list(args.profile),
                    timeout_seconds=args.timeout_seconds,
                    as_json=args.json,
                    summary=bool(args.summary or args.compact),
                    compact=bool(args.compact),
                )
            if args.models_action == "evaluate-candidate":
                if not args.candidate:
                    print("ERROR: models evaluate-candidate requires --candidate", file=sys.stderr)
                    return 2
                return print_local_model_candidate_report(
                    root,
                    candidate_path=args.candidate,
                    as_json=args.json,
                    summary=bool(args.summary or args.compact),
                    compact=bool(args.compact),
                )
            return print_models(root, as_json=args.json, summary=bool(args.summary or args.compact), compact=bool(args.compact))
        if args.command == "select":
            return select_profile(root, profile=args.profile, task=args.task)
        if args.command == "remove":
            return remove_profile(root, profile=args.profile)
        if args.command == "bench":
            if args.detached_command or args.sweep_command:
                return print_detached_benchmark_command(
                    root,
                    profiles=list(args.profile),
                    repetitions=args.repetitions,
                    standard_metrics=args.standard_metrics,
                    suite=args.suite,
                    backend=args.backend,
                    validate_model_urls=bool(args.validate_model_urls),
                    sweep_command=bool(args.sweep_command),
                    sweep_backends=list(args.sweep_backend),
                    as_json=args.json,
                )
            return bench(
                root,
                run_model=args.run_model,
                profiles=list(args.profile),
                repetitions=args.repetitions,
                cold=args.cold,
                warm=args.warm,
                standard_metrics=args.standard_metrics,
                suite=args.suite,
                backend=args.backend,
                validate_model_urls=bool(args.validate_model_urls),
                as_json=args.json,
            )
        if args.command == "runtime":
            if args.runtime_command == "doctor":
                return print_runtime_doctor(
                    root,
                    as_json=args.json,
                    summary=bool(args.summary or args.compact),
                    compact=bool(args.compact),
                )
            if args.runtime_command == "ensure-gpu":
                return print_ensure_gpu_runtime(
                    root,
                    backends=list(args.backend),
                    force=bool(args.force),
                    probe=bool(args.probe),
                    dry_run=bool(args.dry_run),
                    timeout_seconds=args.timeout_seconds,
                    as_json=args.json,
                )
        if args.command == "task":
            return print_daily_task(root, task=args.task, inputs=list(args.inputs), as_json=args.json)
        if args.command == "vision":
            if args.vision_command == "describe":
                return print_vision_describe(root, image=args.image, as_json=args.json)
            if args.vision_command == "pdf":
                return print_vision_pdf(root, pdf=args.pdf, pages=args.pages, as_json=args.json)
        if args.command == "document":
            if args.document_command == "inspect":
                return print_document_inspect(root, file_path=args.file_path, as_json=args.json)
        if args.command == "server":
            if args.server_command == "start":
                return start_server(root, profile=args.profile)
            if args.server_command == "stop":
                return stop_server(root)
            if args.server_command == "status":
                return server_status(root)
        if args.command == "write-config":
            write_default_config(root, force=False)
            ensure_local_ai_gitignore(root)
            write_default_local_settings(root, force=False)
            return 0
        if args.command == "resources":
            return print_resources(
                root,
                as_json=args.json,
                summary=bool(args.summary or args.compact),
                compact=bool(args.compact),
            )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
