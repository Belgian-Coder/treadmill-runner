#!/usr/bin/env python3
"""Local compute resource detection for local-ai-helper."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any


VIRTUAL_GPU_KEYWORDS = {
    "basic display",
    "citrix",
    "hyper-v",
    "llvmpipe",
    "microsoft remote display",
    "parsec",
    "qxl",
    "remote display",
    "software adapter",
    "svga",
    "virtual",
    "virtualbox",
    "vmware",
    "warp",
}
INTEGRATED_GPU_KEYWORDS = {
    "apu",
    "intel hd",
    "intel iris",
    "intel uhd",
    "radeon graphics",
    "vega graphics",
}
DEDICATED_GPU_KEYWORDS = {
    "arc",
    "geforce",
    "nvidia",
    "quadro",
    "radeon pro",
    "radeon rx",
    "rtx",
    "tesla",
}
TOPOLOGY_ENV_ALLOWLIST = [
    "ROCR_VISIBLE_DEVICES",
    "HIP_VISIBLE_DEVICES",
    "CUDA_VISIBLE_DEVICES",
    "GPU_DEVICE_ORDINAL",
    "AMD_VULKAN_ICD",
    "VK_ICD_FILENAMES",
    "VK_DRIVER_FILES",
    "VK_LOADER_LAYERS_DISABLE",
    "MESA_VK_DEVICE_SELECT",
    "MESA_VK_DEVICE_SELECT_FORCE_DEFAULT_DEVICE",
    "RADV_PERFTEST",
    "RADV_DEBUG",
    "RADV_EXPERIMENTAL",
    "RADV_PROFILE_PSTATE",
    "RADV_QUEUE_DISABLE",
    "HSA_OVERRIDE_GFX_VERSION",
    "HSA_ENABLE_SDMA",
    "ROCBLAS_USE_HIPBLASLT",
    "HSA_XNACK",
    "HSA_CU_MASK",
    "AMD_LOG_LEVEL",
    "GGML_CUDA_ENABLE_UNIFIED_MEMORY",
    "GGML_HIP_NO_VMM",
    "GGML_HIP_ROCWMMA_FATTN",
    "GGML_HIP_MMQ_MFMA",
    "GGML_HIP_ENABLE_UNIFIED_MEMORY",
    "GGML_VK_VISIBLE_DEVICES",
    "GGML_VK_FORCE_MAX_ALLOCATION_SIZE",
    "GPU_TARGETS",
    "CMAKE_HIP_ARCHITECTURES",
]


def run_probe(command: list[str], timeout_seconds: int = 5) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired, TypeError) as exc:
        return 1, str(exc)
    return completed.returncode, completed.stdout.strip()


def memory_info() -> dict[str, Any]:
    if platform.system().lower() == "windows":
        code, output = run_probe(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize,FreePhysicalMemory | ConvertTo-Json -Compress)",
            ]
        )
        if code == 0 and output:
            try:
                data = json.loads(output)
                total = int(data.get("TotalVisibleMemorySize", 0)) * 1024
                free = int(data.get("FreePhysicalMemory", 0)) * 1024
                return {"total_bytes": total, "available_bytes": free, "source": "Win32_OperatingSystem"}
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
    if Path("/proc/meminfo").exists():
        values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore").splitlines():
            key, _, raw_value = line.partition(":")
            parts = raw_value.strip().split()
            if parts and parts[0].isdigit():
                values[key] = int(parts[0]) * 1024
        return {
            "total_bytes": values.get("MemTotal", 0),
            "available_bytes": values.get("MemAvailable", values.get("MemFree", 0)),
            "source": "/proc/meminfo",
        }
    return {"total_bytes": 0, "available_bytes": 0, "source": "unknown"}


def gpu_vendor(name: str) -> str:
    normalized = name.lower()
    if "nvidia" in normalized or "geforce" in normalized or "quadro" in normalized or "rtx" in normalized:
        return "nvidia"
    if "amd" in normalized or "radeon" in normalized:
        return "amd"
    if "intel" in normalized or "arc" in normalized or "iris" in normalized or "uhd" in normalized:
        return "intel"
    return "unknown"


def classify_gpu_device(name: str, backend: str = "") -> dict[str, Any]:
    normalized = name.lower()
    vendor = gpu_vendor(name)
    amd_integrated_graphics = (
        vendor == "amd"
        and "radeon" in normalized
        and "graphics" in normalized
        and " rx " not in f" {normalized} "
        and "pro" not in normalized
    )
    if any(keyword in normalized for keyword in VIRTUAL_GPU_KEYWORDS):
        device_type = "virtual"
    elif amd_integrated_graphics or any(keyword in normalized for keyword in INTEGRATED_GPU_KEYWORDS):
        device_type = "integrated"
    elif any(keyword in normalized for keyword in DEDICATED_GPU_KEYWORDS):
        device_type = "dedicated"
    else:
        device_type = "unknown"
    if backend == "cuda" and vendor == "nvidia":
        device_type = "dedicated"
    return {
        "vendor": vendor,
        "device_type": device_type,
        "safe_for_auto": device_type in {"dedicated", "integrated"},
    }


def gpu_info() -> dict[str, Any]:
    devices: list[dict[str, Any]] = []
    if shutil.which("nvidia-smi"):
        code, output = run_probe(["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version", "--format=csv,noheader,nounits"])
        if code == 0:
            for line in output.splitlines():
                parts = [part.strip() for part in line.split(",")]
                if len(parts) >= 4:
                    classification = classify_gpu_device(parts[0], backend="cuda")
                    devices.append(
                        {
                            "backend": "cuda",
                            "name": parts[0],
                            "memory_total_mb": parts[1],
                            "memory_free_mb": parts[2],
                            "driver": parts[3],
                            **classification,
                        }
                    )
    if platform.system().lower() == "windows":
        code, output = run_probe(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion | ConvertTo-Json -Compress)",
            ]
        )
        if code == 0 and output:
            try:
                rows = json.loads(output)
                if isinstance(rows, dict):
                    rows = [rows]
                for row in rows if isinstance(rows, list) else []:
                    name = str(row.get("Name", ""))
                    if name and not any(item.get("name") == name for item in devices):
                        devices.append(
                            {
                                "backend": "windows-display",
                                "name": name,
                                "adapter_ram_bytes": row.get("AdapterRAM"),
                                "driver": row.get("DriverVersion"),
                                **classify_gpu_device(name, backend="windows-display"),
                            }
                        )
            except json.JSONDecodeError:
                pass
    return {"available": bool(devices), "devices": devices}


def read_text_file(path: Path, max_chars: int = 4096) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars].strip()
    except OSError:
        return ""


def parse_sysfs_uint(raw_value: Any) -> int | None:
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        value = -1
    if value < 0:
        return None
    return value


def add_drm_memory_pool_semantics(topology: dict[str, Any]) -> None:
    rows = topology.get("drm")
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        vram_total = parse_sysfs_uint(row.get("mem_info_vram_total"))
        gtt_total = parse_sysfs_uint(row.get("mem_info_gtt_total"))
        if vram_total is None or gtt_total is None:
            continue
        row["memory_pool_semantics"] = {
            "vram_total_bytes": vram_total,
            "gtt_total_bytes": gtt_total,
            "sum_reported_bytes": vram_total + gtt_total,
            "reported_gpu_total_bytes": vram_total + gtt_total,
            "effective_gpu_memory_bytes": None,
            "memory_pool_policy": "do-not-sum-vram-gtt",
            "overcount_risk": True,
            "apu_unified_memory_guidance": "do-not-sum-vram-and-gtt-without-allocator-proof",
            "measurement_sources": [
                "sysfs:mem_info_vram_total",
                "sysfs:mem_info_gtt_total",
            ],
            "allocator_note": "Record VRAM and GTT separately; hipMalloc and hipMallocManaged can have different practical limits on unified-memory APUs.",
        }


def linux_memory_topology() -> dict[str, Any]:
    topology: dict[str, Any] = {
        "cmdline": read_text_file(Path("/proc/cmdline")),
        "ttm": {},
        "drm": [],
    }
    for name in ("pages_limit", "page_pool_size"):
        value = read_text_file(Path("/sys/module/ttm/parameters") / name, max_chars=256)
        if value:
            topology["ttm"][name] = value
    drm_root = Path("/sys/class/drm")
    if drm_root.exists():
        for device in sorted(drm_root.glob("card*/device")):
            row: dict[str, Any] = {"card": device.parent.name}
            for name in (
                "mem_info_vram_total",
                "mem_info_vram_used",
                "mem_info_gtt_total",
                "mem_info_gtt_used",
            ):
                value = read_text_file(device / name, max_chars=256)
                if value:
                    row[name] = value
            if len(row) > 1:
                topology["drm"].append(row)
    add_drm_memory_pool_semantics(topology)
    if not topology["ttm"]:
        topology.pop("ttm")
    if not topology["drm"]:
        topology.pop("drm")
    return topology


def windows_memory_topology() -> dict[str, Any]:
    topology: dict[str, Any] = {}
    code, output = run_probe(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "(Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion,VideoMemoryType | ConvertTo-Json -Compress)",
        ]
    )
    if code == 0 and output:
        try:
            rows = json.loads(output)
            if isinstance(rows, dict):
                rows = [rows]
            if isinstance(rows, list):
                topology["video_controllers"] = rows
        except json.JSONDecodeError:
            topology["video_controller_probe_error"] = output[:512]
    return topology


def topology_portability_class(env: dict[str, str]) -> str:
    vulkan_runtime_overrides = (
        "VK_ICD_FILENAMES",
        "VK_DRIVER_FILES",
        "VK_LOADER_LAYERS_DISABLE",
        "AMD_VULKAN_ICD",
        "MESA_VK_DEVICE_SELECT",
        "MESA_VK_DEVICE_SELECT_FORCE_DEFAULT_DEVICE",
        "RADV_PERFTEST",
        "RADV_DEBUG",
        "RADV_EXPERIMENTAL",
        "RADV_PROFILE_PSTATE",
        "RADV_QUEUE_DISABLE",
        "GGML_VK_VISIBLE_DEVICES",
        "GGML_VK_FORCE_MAX_ALLOCATION_SIZE",
    )
    if any(name in env for name in vulkan_runtime_overrides):
        return "process-env-only"
    if any(name in env for name in ("GPU_TARGETS", "CMAKE_HIP_ARCHITECTURES")):
        return "custom-fork"
    hip_runtime_overrides = (
        "HSA_OVERRIDE_GFX_VERSION",
        "GGML_HIP_NO_VMM",
        "GGML_HIP_ROCWMMA_FATTN",
        "GGML_HIP_MMQ_MFMA",
        "GGML_HIP_ENABLE_UNIFIED_MEMORY",
        "ROCBLAS_USE_HIPBLASLT",
        "HSA_ENABLE_SDMA",
    )
    if any(name in env for name in hip_runtime_overrides):
        return "process-env-only"
    return "portable-no-admin"


def host_memory_topology() -> dict[str, Any]:
    system_name = platform.system().lower()
    uname = platform.uname()
    env = {name: os.environ[name] for name in TOPOLOGY_ENV_ALLOWLIST if name in os.environ}
    topology: dict[str, Any] = {
        "schema_version": 1,
        "platform": {
            "system": uname[0],
            "kernel": uname[2],
            "version": uname[3],
            "machine": uname[4],
        },
        "env": env,
        "portability_class": topology_portability_class(env),
        "notes": [],
    }
    if system_name == "linux":
        topology.update(linux_memory_topology())
        if any(isinstance(row, dict) and row.get("memory_pool_semantics") for row in topology.get("drm", [])):
            topology["notes"].append(
                "Linux DRM VRAM/GTT totals are separate pool evidence; on APUs or unified-memory systems do not sum them as independently usable GPU capacity without allocator proof."
            )
    elif system_name == "windows":
        topology.update(windows_memory_topology())
        topology["notes"].append("BIOS/driver VGM or UMA size is not reliably discoverable without machine-specific tooling.")
    else:
        topology["notes"].append("Host memory topology probe is best-effort for this platform.")
    if topology.get("env", {}).get("HSA_OVERRIDE_GFX_VERSION"):
        topology["notes"].append("HSA_OVERRIDE_GFX_VERSION is troubleshooting evidence and should block portable promotion.")
    radv_perftest = topology.get("env", {}).get("RADV_PERFTEST", "")
    if "unified_heap" in {part.strip() for part in radv_perftest.split(",")}:
        topology["notes"].append(
            "RADV_PERFTEST=unified_heap is not listed in current upstream Mesa RADV_PERFTEST docs/source; treat as unverified or downstream unless the row includes drirc or Mesa-source proof."
        )
    if topology["portability_class"] != "portable-no-admin":
        topology["notes"].append("Runtime driver environment overrides are evidence fields, not portable defaults.")
    return topology


def resource_report(root: Path) -> dict[str, Any]:
    disk = shutil.disk_usage(root)
    memory = memory_info()
    logical = os.cpu_count() or 1
    available_gb = round(int(memory.get("available_bytes", 0)) / (1024**3), 2)
    suggested_threads = max(1, min(logical, 8))
    return {
        "schema_version": 1,
        "tool": "local-ai-helper.resources",
        "ok": True,
        "status": "passed",
        "root": str(root),
        "cpu": {"logical_cores": logical, "suggested_threads": suggested_threads, "platform": platform.platform()},
        "memory": {**memory, "available_gb": available_gb, "total_gb": round(int(memory.get("total_bytes", 0)) / (1024**3), 2)},
        "disk": {"total_gb": round(disk.total / (1024**3), 2), "free_gb": round(disk.free / (1024**3), 2)},
        "gpu": gpu_info(),
        "host_memory_topology": host_memory_topology(),
        "recommendations": {
            "local_ai_default": "cpu",
            "max_parallel_generations": 1,
            "threads": suggested_threads,
            "memory_strategy": "low" if available_gb < 8 else "moderate" if available_gb < 24 else "comfortable",
        },
    }


def resource_summary(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    cpu = report.get("cpu") if isinstance(report.get("cpu"), dict) else {}
    memory = report.get("memory") if isinstance(report.get("memory"), dict) else {}
    disk = report.get("disk") if isinstance(report.get("disk"), dict) else {}
    gpu = report.get("gpu") if isinstance(report.get("gpu"), dict) else {}
    devices = gpu.get("devices") if isinstance(gpu.get("devices"), list) else []
    summary: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "local-ai-helper.resources"),
        "ok": bool(report.get("ok", False)),
        "status": report.get("status", ""),
        "cpu": {
            "logical_cores": cpu.get("logical_cores", 0),
            "suggested_threads": cpu.get("suggested_threads", 0),
        },
        "memory": {
            "available_gb": memory.get("available_gb", 0),
            "total_gb": memory.get("total_gb", 0),
        },
        "disk": {"free_gb": disk.get("free_gb", 0)},
        "gpu": {"available": bool(gpu.get("available", False)), "device_count": len(devices)},
        "recommendations": report.get("recommendations", {}),
    }
    if not compact:
        summary["gpu"]["devices"] = devices
        summary["root"] = report.get("root", "")
    return summary


def print_resources(root: Path, as_json: bool = False, summary: bool = False, compact: bool = False) -> int:
    report = resource_report(root)
    output_report = resource_summary(report, compact=compact) if summary or compact else report
    if as_json:
        print(json.dumps(output_report, indent=2, sort_keys=True))
    else:
        print("Local AI resource report")
        print(f"- CPU logical cores: {report['cpu']['logical_cores']}")
        print(f"- Suggested threads: {report['cpu']['suggested_threads']}")
        print(f"- Available memory: {report['memory']['available_gb']} GB")
        print(f"- Free disk: {report['disk']['free_gb']} GB")
        print(f"- GPU devices: {len(report['gpu']['devices'])}")
    return 0
