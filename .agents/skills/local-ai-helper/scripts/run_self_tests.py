#!/usr/bin/env python3
"""Self-tests for local-ai-helper scripts."""

import ast
import argparse
import contextlib
import hashlib
import io
import importlib.util
import json
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import local_ai_routing
import setup_local_ai
from local_ai_support import (
    benchmark_metrics,
    broker_tools,
    policy_impl,
    resources_impl,
    routing_defaults,
    routing_impl,
    setup_catalog,
    setup_impl,
    setup_parser,
)

DOC_MAPS_TEXT = "skill maps.\n"
AMD_780M = "AMD Radeon 780M Graphics"
GPU_DOWNLOAD_FAILURE = "GPU failed"
ROCM_KERNEL_ERROR = "ROCm error"
VULKAN_SLOW_REASON = "vulkan slower"
VALIDATION_FAILED_SUMMARY = "Failed."
VALIDATION_FAILED_SUGGESTION = "Sync."
COMPACT_JSON = ["--json", "--summary", "--compact"]
TEXT_PROFILE = "nemotron3-nano4b"
EMBEDDING_PROFILE = "qwen3-embedding-4b"
VISION_PROFILE = "qwen3vl-2b-q4"
DEFAULT_PROFILES = [TEXT_PROFILE, VISION_PROFILE]
VISION_BLOCK_SUMMARY = "The image contains a red block beside a blue block."


def test_model_lease_concurrency_priority_stale_reclaim_and_exception_cleanup(tmp):
    model_lease = __import__("local_ai_support.model_lease", fromlist=["model_lease"])
    now = [100.0]
    clock = lambda: now[0]
    absent = lambda pid: False

    try:
        with model_lease.exclusive_lease(
            tmp,
            profile=TEXT_PROFILE,
            role="text",
            priority="interactive",
            command_kind="task",
            pid=101,
            clock=clock,
            process_exists=absent,
        ) as first:
            assert first.acquired is True
            with model_lease.exclusive_lease(
                tmp,
                profile=TEXT_PROFILE,
                role="text",
                priority="validation",
                command_kind="task",
                timeout_ms=0,
                pid=202,
                clock=clock,
                process_exists=absent,
            ) as second:
                assert_fields(
                    second.report(),
                    status="local-ai-busy",
                    fallback_used=True,
                    conflict_count=1,
                )
            raise RuntimeError("fixture exception")
    except RuntimeError as exc:
        assert str(exc) == "fixture exception"
    assert not (tmp / ".agents/local-ai/cache/model-lease.lock").exists()

    lock_dir = tmp / ".agents/local-ai/cache/model-lease.lock"
    lock_dir.mkdir(parents=True)
    write_json(
        lock_dir / "lease.json",
        {
            "schema_version": 1,
            "pid": 303,
            "profile": TEXT_PROFILE,
            "role": "text",
            "priority": "benchmark",
            "command_kind": "bench",
            "acquired_at_unix": 1,
            "heartbeat_at_unix": 1,
            "state": "active",
        },
    )
    now[0] = 1000.0
    with model_lease.exclusive_lease(
        tmp,
        profile=TEXT_PROFILE,
        role="text",
        priority="interactive",
        command_kind="task",
        pid=404,
        clock=clock,
        process_exists=absent,
        stale_after_seconds=30,
    ) as reclaimed:
        assert reclaimed.acquired is True
        assert reclaimed.reclaimed_stale is True


def test_model_lease_cooperatively_stops_only_recorded_persistent_server(tmp):
    model_lease = __import__("local_ai_support.model_lease", fromlist=["model_lease"])
    state_path = tmp / ".agents/local-ai/cache/server.json"
    write_json(
        state_path,
        {
            "pid": 601,
            "profile": "old-profile",
            "backend": "cpu",
            "command": ["llama-server"],
            "log": ".agents/local-ai/cache/server/old.log",
            "started_at_unix": 1,
        },
    )
    write_json(
        tmp / ".agents/local-ai/cache/model-lease.lock/lease.json",
        {
            "schema_version": 1,
            "pid": 601,
            "profile": "old-profile",
            "role": "text",
            "priority": "interactive",
            "command_kind": "server",
            "acquired_at_unix": 1,
            "heartbeat_at_unix": 1,
            "state": "active",
        },
    )
    stopped = []
    report = model_lease.cooperative_stop_recorded_server(
        tmp,
        requested_profile=TEXT_PROFILE,
        process_exists=lambda pid: pid == 601,
        stop_recorded=lambda state: stopped.append(state["pid"]) or True,
    )
    assert_fields(report, stopped=True, recorded_pid=601, arbitrary_process_killed=False)
    assert stopped == [601]
    assert not (tmp / ".agents/local-ai/cache/model-lease.lock").exists()

    missing = model_lease.cooperative_stop_recorded_server(
        tmp,
        requested_profile=TEXT_PROFILE,
        process_exists=lambda _pid: True,
        stop_recorded=lambda _state: (_ for _ in ()).throw(AssertionError("must not stop")),
    )
    assert_fields(missing, stopped=False, recorded_pid=0, arbitrary_process_killed=False)

    write_json(
        state_path,
        {
            "pid": 602,
            "profile": "unattested-profile",
            "command": ["llama-server"],
        },
    )
    mismatched = model_lease.cooperative_stop_recorded_server(
        tmp,
        requested_profile=TEXT_PROFILE,
        process_exists=lambda pid: pid == 602,
        stop_recorded=lambda _state: (_ for _ in ()).throw(AssertionError("must not stop")),
    )
    assert_fields(mismatched, stopped=False, recorded_pid=602, arbitrary_process_killed=False)


def test_model_lease_transfers_to_and_releases_recorded_persistent_server(tmp):
    model_lease = __import__("local_ai_support.model_lease", fromlist=["model_lease"])
    with model_lease.exclusive_lease(
        tmp,
        profile=TEXT_PROFILE,
        role="text",
        priority="interactive",
        command_kind="server",
        pid=701,
        process_exists=lambda pid: pid in {701, 702},
    ) as lease:
        assert lease.acquired is True
        assert lease.transfer_to_pid(702) is True
        assert lease.acquired is False
        assert lease.status == "transferred-to-server"

    state = read_json(tmp / ".agents/local-ai/cache/model-lease.lock/lease.json")
    assert_fields(state, pid=702, profile=TEXT_PROFILE, command_kind="server")
    assert model_lease.release_recorded_server_lease(
        tmp,
        pid=702,
        profile=TEXT_PROFILE,
    ) is True
    assert not (tmp / ".agents/local-ai/cache/model-lease.lock").exists()


def test_foreground_model_lease_preempts_only_attested_persistent_server(tmp):
    model_lease = __import__("local_ai_support.model_lease", fromlist=["model_lease"])
    server_state = tmp / ".agents/local-ai/cache/server.json"
    write_json(server_state, {"pid": 801, "profile": "warm-profile"})
    write_json(
        tmp / ".agents/local-ai/cache/model-lease.lock/lease.json",
        {
            "schema_version": 1,
            "pid": 801,
            "profile": "warm-profile",
            "role": "text",
            "priority": "interactive",
            "command_kind": "server",
            "acquired_at_unix": 1,
            "heartbeat_at_unix": 1,
            "state": "active",
        },
    )
    stopped = []
    with model_lease.exclusive_lease(
        tmp,
        profile=TEXT_PROFILE,
        role="text",
        priority="interactive",
        command_kind="task",
        pid=802,
        process_exists=lambda pid: pid in {801, 802},
        stop_recorded=lambda state: stopped.append(state["pid"]) or True,
    ) as foreground:
        assert foreground.acquired is True
        assert foreground.conflict_count == 1
    assert stopped == [801]
    assert not server_state.exists()


def test_persistent_server_start_transfers_lease_and_stop_releases_it(tmp):
    class FakeProcess:
        pid = 901

    class Completed:
        returncode = 0

    config = {"limits": {}, "server": {}}
    manifest = {"models": [], "runtimes": []}
    model = {"profile": TEXT_PROFILE}
    runtime = {"backend": "cpu"}
    output = io.StringIO()
    with patched_attrs(setup_impl, config_for_profile=lambda _root, _profile: config):
        with patched_attrs(
            setup_impl.local_ai_routing,
            load_bundle=lambda _root, _config: (manifest, []),
            select_model=lambda _root, _config, _manifest: (model, []),
            select_runtime=lambda _root, _config, _manifest, check_only=False: (runtime, []),
            llama_server_command=lambda _runtime, _model, _config: ["llama-server", "--port", "8765"],
        ):
            with patched_attrs(setup_impl.subprocess, Popen=lambda *_args, **_kwargs: FakeProcess()):
                with contextlib.redirect_stdout(output):
                    assert setup_impl.start_server(tmp, profile=TEXT_PROFILE) == 0

    state = read_json(tmp / ".agents/local-ai/cache/server.json")
    lease = read_json(tmp / ".agents/local-ai/cache/model-lease.lock/lease.json")
    assert_fields(state, pid=901, profile=TEXT_PROFILE, fallback_used=False)
    assert_has_all(state, "lease_wait_ms", "load_ms", "inference_ms", "unload_ms", "conflict_count")
    assert_fields(lease, pid=901, profile=TEXT_PROFILE, command_kind="server", state="active")

    calls = []
    with patched_attrs(setup_impl, process_running=lambda pid: pid == 901):
        with patched_attrs(setup_impl.os, name="nt"):
            with patched_attrs(
                setup_impl.subprocess,
                run=lambda command, **_kwargs: calls.append(command) or Completed(),
            ):
                with contextlib.redirect_stdout(output):
                    assert setup_impl.stop_server(tmp) == 0
    assert calls and "901" in calls[0]
    assert not (tmp / ".agents/local-ai/cache/server.json").exists()
    assert not (tmp / ".agents/local-ai/cache/model-lease.lock").exists()


def local_model_candidate(**overrides):
    candidate = {
        "schema_version": 1,
        "id": "candidate-4b-q4",
        "roles": ["text", "routing"],
        "source_url": "https://example.invalid/model.gguf",
        "license_url": "https://example.invalid/LICENSE",
        "sha256": "a" * 64,
        "runtime_family": "llama.cpp",
        "minimum_runtime": "b9222",
        "total_parameters_billion": 4.0,
        "active_parameters_billion": 4.0,
        "context_tokens": 4096,
        "quantization": "Q4_K_M",
        "expected_download_size_gb": 2.64,
        "overlap_classification": "distinct",
        "seeking_promotion": False,
        "benchmark_suite_ref": "",
        "requires_credentials": False,
        "requires_account": False,
        "accelerators": ["cpu"],
        "extensions": {},
    }
    candidate.update(overrides)
    return candidate


def test_local_model_candidate_screening_rejects_oversized_and_gated_models(_tmp):
    screening = __import__(
        "local_ai_support.candidate_screening",
        fromlist=["candidate_screening"],
    )
    resources = {
        "memory": {"total_gb": 32.0, "available_gb": 16.0},
        "disk": {"free_gb": 100.0},
        "gpu": {
            "devices": [
                {
                    "device_type": "dedicated",
                    "memory_free_mb": 2048,
                    "memory_total_mb": 2048,
                }
            ]
        },
    }
    policy = {"max_download_gb": 20.0}

    eligible = screening.evaluate_candidate(
        local_model_candidate(),
        resources=resources,
        policy=policy,
        supported_runtime_families={"llama.cpp"},
    )
    assert_fields(eligible, decision="eligible", eligible=True)

    overlap = screening.evaluate_candidate(
        local_model_candidate(overlap_classification="overlaps-accepted"),
        resources=resources,
        policy=policy,
        supported_runtime_families={"llama.cpp"},
    )
    assert_fields(overlap, decision="benchmark-only", eligible=False)

    oversized = screening.evaluate_candidate(
        local_model_candidate(
            id="deepseek-v4-flash-generic",
            expected_download_size_gb=82.5,
            total_parameters_billion=236.0,
            active_parameters_billion=21.0,
        ),
        resources=resources,
        policy=policy,
        supported_runtime_families={"llama.cpp"},
    )
    assert_fields(oversized, decision="reject", eligible=False)
    assert_has_all(
        " ".join(oversized["reasons"]),
        "20.0 GB download policy",
        "60% of system RAM",
        "available RAM cannot hold the model plus 4 GB",
    )

    gated = screening.evaluate_candidate(
        local_model_candidate(requires_account=True),
        resources=resources,
        policy=policy,
        supported_runtime_families={"llama.cpp"},
    )
    assert_fields(gated, decision="reject")
    assert_has_all(" ".join(gated["reasons"]), "credentials or an account gate")

    gpu = screening.evaluate_candidate(
        local_model_candidate(accelerators=["gpu"], expected_download_size_gb=3.0),
        resources=resources,
        policy=policy,
        supported_runtime_families={"llama.cpp"},
    )
    assert_fields(gpu, decision="reject")
    assert_has_all(" ".join(gpu["reasons"]), "80% of usable dedicated VRAM")

    unsupported_build = screening.evaluate_candidate(
        local_model_candidate(minimum_runtime="b9777"),
        resources=resources,
        policy=policy,
        supported_runtime_families={"llama.cpp": "b9222"},
    )
    assert_fields(unsupported_build, decision="reject")
    assert_has_all(" ".join(unsupported_build["reasons"]), "exceeds supported b9222")

    indirect = screening.evaluate_candidate(
        local_model_candidate(source_url="https://example.invalid/model-card"),
        resources=resources,
        policy=policy,
        supported_runtime_families={"llama.cpp"},
    )
    assert_fields(indirect, decision="reject")
    assert_has_all(" ".join(indirect["reasons"]), "direct HTTPS GGUF URL")


def test_local_model_candidate_schema_requires_promotion_suite_and_rejects_typos(_tmp):
    screening = __import__(
        "local_ai_support.candidate_screening",
        fromlist=["candidate_screening"],
    )
    candidate = local_model_candidate(seeking_promotion=True, benchmark_suite_ref="")
    candidate["runtime_famly"] = "typo"

    report = screening.evaluate_candidate(
        candidate,
        resources={
            "memory": {"total_gb": 32.0, "available_gb": 16.0},
            "disk": {"free_gb": 100.0},
            "gpu": {"devices": []},
        },
        policy={"max_download_gb": 20.0},
        supported_runtime_families={"llama.cpp"},
    )

    assert_fields(report, decision="reject", eligible=False)
    assert_has_all(
        " ".join(report["reasons"]),
        "unknown field runtime_famly",
        "benchmark_suite_ref is required when seeking promotion",
    )


def test_models_evaluate_candidate_cli_rejects_82gb_without_download(tmp):
    write_config(tmp)
    candidate_path = tmp / "deepseek.json"
    write_json(
        candidate_path,
        local_model_candidate(
            id="deepseek-v4-flash-generic",
            expected_download_size_gb=82.5,
            total_parameters_billion=236.0,
            active_parameters_billion=21.0,
        ),
    )
    resources = {
        "memory": {"total_gb": 32.0, "available_gb": 3.8},
        "disk": {"free_gb": 100.0},
        "gpu": {"devices": []},
    }
    output = io.StringIO()
    with patched_attrs(setup_impl._resources_impl, resource_report=lambda _root: resources):
        with contextlib.redirect_stdout(output):
            exit_code = setup_impl.main(
                [
                    "--root",
                    str(tmp),
                    "models",
                    "evaluate-candidate",
                    "--candidate",
                    str(candidate_path),
                    "--summary",
                    "--compact",
                    "--json",
                ]
            )
    report = json.loads(output.getvalue())

    assert exit_code == 1
    assert_fields(report, decision="reject", candidate_id="deepseek-v4-flash-generic")
    assert not (tmp / ".agents/local-ai/bundle/models").exists()


def test_operational_local_model_paths_share_busy_fallback(tmp):
    from local_ai_support import daily_impl, vision_impl

    lock_dir = tmp / ".agents/local-ai/cache/model-lease.lock"
    write_json(
        lock_dir / "lease.json",
        {
            "schema_version": 1,
            "pid": 99999999,
            "profile": "blocking-profile",
            "role": "text",
            "priority": "benchmark",
            "command_kind": "fixture",
            "acquired_at_unix": int(time.time()),
            "heartbeat_at_unix": int(time.time()) + 3600,
            "state": "active",
        },
    )
    try:
        text_ok, _text, text_config, text_issues = daily_impl.run_text_completion(
            tmp,
            task="validation-triage",
            profile=TEXT_PROFILE,
            prompt="fixture",
        )
        assert text_ok is False
        assert_has_all(" ".join(text_issues), "local-ai-busy")
        assert_fields(text_config["lease"], fallback_used=True, conflict_count=1)

        route_config = {}
        route = routing_impl.run_model(
            root=tmp,
            task="skill-routing",
            item={"id": "fixture"},
            allowed_categories={"skill"},
            model={},
            runtime={},
            config=route_config,
        )
        assert route[0] is False
        assert_has_all(route[3], "local-ai-busy")
        assert_fields(route_config["lease"], fallback_used=True, conflict_count=1)

        batch_config = {}
        batch = routing_impl.run_model_batch_with_server(
            tmp,
            task="skill-routing",
            items=[{"id": "fixture"}],
            allowed_categories={"skill"},
            model={},
            runtime={},
            config=batch_config,
        )
        assert_has_all(batch["fixture"][3], "local-ai-busy")
        assert_fields(batch_config["lease"], fallback_used=True, conflict_count=1)

        vision_lease = {}
        vision_ok, _vision_output, vision_issues = vision_impl.run_vision_model(
            tmp,
            tmp / "fixture.png",
            "describe",
            lease_report=vision_lease,
        )
        assert vision_ok is False
        assert_has_all(" ".join(vision_issues), "local-ai-busy")
        assert_fields(vision_lease, fallback_used=True, conflict_count=1)
    finally:
        for child in lock_dir.iterdir() if lock_dir.exists() else []:
            child.unlink()
        if lock_dir.exists():
            lock_dir.rmdir()


def load_normalized_benchmark_module():
    path = SCRIPT_DIR.parents[3] / "automations" / "local-ai-benchmark-workflow" / "scripts" / "normalized_code_generation_benchmark.py"
    spec = importlib.util.spec_from_file_location("normalized_code_generation_benchmark_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_normalized_server_benchmark_module():
    path = (
        SCRIPT_DIR.parents[3]
        / "automations"
        / "local-ai-benchmark-workflow"
        / "scripts"
        / "normalized_server_code_generation_benchmark.py"
    )
    spec = importlib.util.spec_from_file_location("normalized_server_code_generation_benchmark_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_nemotron_xunit_benchmark_module():
    path = SCRIPT_DIR.parents[3] / "automations" / "local-ai-benchmark-workflow" / "scripts" / "nemotron_xunit_dotnet10_benchmark.py"
    spec = importlib.util.spec_from_file_location("nemotron_xunit_dotnet10_benchmark_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_gguf_string(handle, value: str):
    data = value.encode("utf-8")
    handle.write(struct.pack("<Q", len(data)))
    handle.write(data)


def write_synthetic_gguf(path: Path, metadata: dict[str, object], tensor_names: list[str]):
    with path.open("wb") as handle:
        handle.write(b"GGUF")
        handle.write(struct.pack("<I", 3))
        handle.write(struct.pack("<Q", len(tensor_names)))
        handle.write(struct.pack("<Q", len(metadata)))
        for key, value in metadata.items():
            write_gguf_string(handle, key)
            if isinstance(value, str):
                handle.write(struct.pack("<I", 8))
                write_gguf_string(handle, value)
            elif isinstance(value, int):
                handle.write(struct.pack("<I", 4))
                handle.write(struct.pack("<I", value))
            else:
                raise TypeError(f"unsupported synthetic GGUF metadata value: {value!r}")
        for name in tensor_names:
            write_gguf_string(handle, name)
            handle.write(struct.pack("<I", 1))
            handle.write(struct.pack("<Q", 1))
            handle.write(struct.pack("<I", 0))
            handle.write(struct.pack("<Q", 0))


def gpu_info_fixture(
    *,
    backend,
    name,
    vendor,
    device_type,
    safe_for_auto = True,
):
    return {
        "available": True,
        "devices": [
            {
                "backend": backend,
                "name": name,
                "vendor": vendor,
                "device_type": device_type,
                "safe_for_auto": safe_for_auto,
            }
        ],
    }


def nvidia_cuda_gpu_info():
    return gpu_info_fixture(backend="cuda", name="NVIDIA RTX 4070", vendor="nvidia", device_type="dedicated")


def intel_iris_gpu_info():
    return gpu_info_fixture(backend="windows-display", name="Intel Iris Xe Graphics", vendor="intel", device_type="integrated")


def intel_arc_gpu_info():
    return gpu_info_fixture(backend="windows-display", name="Intel Arc Graphics", vendor="intel", device_type="integrated")


def amd_7900_gpu_info():
    return gpu_info_fixture(backend="windows-display", name="AMD Radeon RX 7900 XT", vendor="amd", device_type="dedicated")


def amd_780m_gpu_info():
    return gpu_info_fixture(backend="windows-display", name=AMD_780M, vendor="amd", device_type="integrated")


def write_config(tmp):
    config_path = local_ai_config_path(tmp)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(setup_local_ai.DEFAULT_CONFIG, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    policy_impl.write_default_policy(tmp)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_text(path, text):
    path.write_text(text, encoding="utf-8", newline="\n")


def docs_dir(tmp):
    docs = tmp / "docs"
    docs.mkdir(exist_ok=True)
    return docs


def write_doc(tmp, name = "a.md", text = DOC_MAPS_TEXT):
    docs = docs_dir(tmp)
    write_text(docs / name, text)
    return docs


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def local_ai_config_path(tmp):
    return tmp / ".agents" / "local-ai.json"


def read_local_ai_config(tmp):
    return read_json(local_ai_config_path(tmp))


def write_local_ai_config(tmp, config):
    write_json(local_ai_config_path(tmp), config)


def local_settings_path(tmp):
    return tmp / ".agents" / "local-ai" / "local.settings.json"


def local_ai_cache_path(tmp, name):
    return tmp / ".agents" / "local-ai" / "cache" / name


def skill_root(tmp, name):
    return tmp / ".agents" / "skills" / name


def read_local_settings(tmp):
    return read_json(local_settings_path(tmp))


def write_local_settings(tmp, payload):
    write_json(local_settings_path(tmp), payload)


def write_auto_cpu_config(tmp):
    write_config(tmp)
    config = read_local_ai_config(tmp)
    config["backend_order"] = ["auto", "cpu"]
    write_local_ai_config(tmp, config)
    return config


def assert_parsed(parser, args, expected):
    parsed = parser.parse_args(args)
    for name, value in expected.items():
        assert getattr(parsed, name) == value


def assert_mapping_items(mapping, expected):
    for key, value in expected.items():
        actual = mapping[key]
        if isinstance(value, bool):
            assert actual is value
        else:
            assert actual == value


def assert_field(mapping, key, expected):
    assert_mapping_items(mapping, {key: expected})


def assert_fields(target, **expected):
    assert_mapping_items(target, expected)


def assert_empty(value):
    assert value == [], value


def assert_present(*values):
    for value in values:
        assert value is not None


def assert_ok(result):
    assert result.get("ok") is True, result


def assert_not_ok(result):
    assert result.get("ok") is False, result


def assert_contains(items, text):
    assert any(text in str(item) for item in items), items


def assert_contains_all(items, *texts):
    assert any(all(text in str(item) for text in texts) for item in items), items


def assert_contains_each(items, *texts):
    for text in texts:
        assert_contains(items, text)


def assert_lacks(items, text):
    assert all(text not in str(item) for item in items), items


def assert_has_all(container, *items):
    for item in items:
        assert item in container, container


def assert_lacks_all(container, *items):
    for item in items:
        assert item not in container, container


def assert_keys_lack(mapping, *keys):
    for key in keys:
        assert key not in mapping, mapping


def assert_path(rows, path):
    assert any(row.get("path") == path for row in rows), rows


def assert_command_rows_include(rows, *texts):
    for text in texts:
        assert all(text in str(row.get("command", "")) for row in rows), rows


def _write_local_ai_bundle(
    tmp,
    *,
    profiles,
    runtimes = None,
):
    bundle_dir = tmp / ".agents" / "local-ai" / "bundle"
    runtime_entries = []
    raw_runtimes = runtimes or [{"backend": "cpu", "folder": "fake-runtime"}]
    for raw_runtime in raw_runtimes:
        backend = str(raw_runtime.get("backend", "cpu"))
        folder = str(raw_runtime.get("folder", f"fake-{backend}-runtime"))
        runtime_path = bundle_dir / "runtimes" / folder / "llama-cli.exe"
        server_path = bundle_dir / "runtimes" / folder / "llama-server.exe"
        runtime_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_path.write_bytes(b"r")
        server_path.write_bytes(b"s")
        runtime_entries.append(
            {
                "backend": backend,
                "platform": str(raw_runtime.get("platform", local_ai_routing.platform_id())),
                "path": f"runtimes/{folder}/llama-cli.exe",
                "server_path": f"runtimes/{folder}/llama-server.exe",
                "sha256": local_ai_routing.sha256_file(runtime_path),
                "server_sha256": local_ai_routing.sha256_file(server_path),
            }
        )
    models = []
    for profile in profiles:
        model_path = bundle_dir / "models" / f"{profile}.gguf"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_bytes(b"m")
        models.append(
            {
                "profile": profile,
                "aliases": [],
                "path": f"models/{profile}.gguf",
                "sha256": local_ai_routing.sha256_file(model_path),
                "license": "NVIDIA Open Model License" if profile == TEXT_PROFILE else "Apache-2.0",
            }
        )
    bundle_dir.joinpath("manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runtime": {"name": "llama.cpp", "version": "test", "license": "MIT"},
                "models": models,
                "runtimes": runtime_entries,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_text_bundle(
    tmp,
    *,
    runtimes = None,
):
    _write_local_ai_bundle(tmp, profiles=[TEXT_PROFILE], runtimes=runtimes)


def model_ref(path, profile = TEXT_PROFILE):
    return {"profile": profile, "resolved_path": str(path)}


def write_gpu_test_config(
    tmp,
    *,
    preferred_backends,
    runtimes,
    mode = "auto",
    allow_integrated = True,
    allow_experimental = False,
    auto_calibrate = False,
    performance_threshold_percent = None,
):
    write_auto_cpu_config(tmp)
    gpu = {
        "mode": mode,
        "preferred_backends": preferred_backends,
        "smoke_test_runtime": True,
    }
    if allow_integrated:
        gpu["allow_integrated"] = True
    if allow_experimental:
        gpu["allow_experimental_workloads"] = True
    if auto_calibrate:
        gpu["auto_calibrate"] = True
    if performance_threshold_percent is not None:
        gpu["performance_threshold_percent"] = performance_threshold_percent
    write_local_settings(tmp, {"schema_version": 1, "gpu": gpu, "runtime_overrides": []})
    write_text_bundle(tmp, runtimes=runtimes)


def probe_with_manifest_hash(runtime, _config, *, check_only = False):
    runtime["actual_sha256"] = local_ai_routing.sha256_file(Path(str(runtime.get("resolved_path", ""))))
    return True, ""


def select_loaded_runtime(tmp, loaded):
    manifest, _issues = local_ai_routing.load_bundle(tmp, loaded)
    model, model_issues = local_ai_routing.select_model(tmp, loaded, manifest)
    runtime, runtime_issues = local_ai_routing.select_runtime(tmp, loaded, manifest, check_only=False)
    return model, model_issues, runtime, runtime_issues


def embedding_recorder(
    calls = None,
    *,
    vector_factory=None,
    issues = None,
    expected_profile = None,
    record_profile = False,
):
    def fake_embed(_root, texts, profile = None, *, lease_report=None):
        if expected_profile is not None:
            assert profile == expected_profile
        if isinstance(lease_report, dict):
            lease_report.update(
                {
                    "schema_version": 1,
                    "lease_wait_ms": 0,
                    "load_ms": 0,
                    "inference_ms": 1,
                    "unload_ms": 0,
                    "conflict_count": 0,
                    "fallback_used": False,
                }
            )
        if calls is not None:
            calls.append((list(texts), profile) if record_profile else list(texts))
        if issues is not None:
            return [], issues
        factory = vector_factory or (lambda _index, _text: [1.0, 0.0])
        return [factory(index, text) for index, text in enumerate(texts)], []

    return fake_embed


def skip_synthesis(_root, _task, _prompt):
    return {"ok": False, "issues": ["skip synthesis"], "summary": "", "findings": [], "suggestions": [], "evidence": []}


@contextlib.contextmanager
def patched_attrs(target, **values):
    originals = {name: (hasattr(target, name), getattr(target, name, None)) for name in values}
    for name, value in values.items():
        setattr(target, name, value)
    try:
        yield
    finally:
        for name, (existed, value) in originals.items():
            if existed:
                setattr(target, name, value)
            else:
                delattr(target, name)


def test_integration_suggestions():
    skill_suggestions = setup_local_ai.integration_suggestions("skill")
    workflow_suggestions = setup_local_ai.integration_suggestions("workflow")
    all_suggestions = setup_local_ai.integration_suggestions("all")
    assert {item["id"] for item in skill_suggestions} >= {"skill-routing", "skill-validation-triage"}
    assert {item["id"] for item in workflow_suggestions} >= {"workflow-routing", "workflow-validation-triage"}
    assert len(all_suggestions) == len(skill_suggestions) + len(workflow_suggestions)
    assert all("guardrail" in item for item in all_suggestions)


def test_default_config_enables_first_use_bootstrap():
    bootstrap = setup_local_ai.DEFAULT_CONFIG["bootstrap"]
    assert_mapping_items(
        bootstrap,
        {
            "auto_config": True,
            "auto_download": "on-local-ai-use",
            "direct_script_fallback": True,
            "max_download_gb": 20,
        },
    )
    assert set(bootstrap["default_profiles"]) == set(DEFAULT_PROFILES)
    assert_mapping_items(
        setup_local_ai.DEFAULT_CONFIG["task_attempt_policy"],
        {
            "max_attempts_per_profile": 2,
            "retry_on_low_confidence": True,
            "retry_on_plain_text": True,
            "fallback": "orchestrator-handoff",
        },
    )
    assert_has_all(setup_local_ai.DEFAULT_CONFIG["task_attempt_policy"]["retry_failure_classes"], "schema", "plain-text")
    assert_has_all(setup_local_ai.DEFAULT_CONFIG["task_attempt_policy"]["handoff_failure_classes"], "test", "mutation")
    assert_mapping_items(
        setup_local_ai.DEFAULT_CONFIG["benchmark_policy"],
        {
            "baseline_epoch": "fresh-2026-06-11",
            "candidate_memory_limit_gb": 20.0,
            "ignore_prior_results": True,
            "require_peak_memory_evidence": True,
        },
    )
    assert_field(setup_local_ai.DEFAULT_CONFIG["benchmark_policy"]["promotion_gates"], "must_pass_suite", True)
    assert_field(setup_local_ai.DEFAULT_CONFIG["task_envelopes"]["dotnet10-xunit-authoring"], "route", "benchmark-only")
    assert_field(setup_local_ai.DEFAULT_CONFIG["task_envelopes"]["dotnet10-xunit-authoring"], "max_attempts", 0)
    assert_field(setup_local_ai.DEFAULT_CONFIG["model_task_envelopes"][TEXT_PROFILE], "max_task_class", "installed-smoke-default")
    assert_keys_lack(setup_local_ai.DEFAULT_CONFIG, "rag", "rag_embedding_profile")
    assert_has_all(
        setup_local_ai.DEFAULT_CONFIG["tools"]["exclude_paths"],
        ".agents/local-ai/cache",
        ".aider.conf.yml",
        ".claude",
        ".continue",
        ".github/copilot-instructions.md",
        "GEMINI.md",
    )
    assert "cost_policy" not in setup_local_ai.DEFAULT_CONFIG


def test_cost_policy_is_central_project_policy_and_fresh_local_ai_setup_does_not_duplicate_it(tmp):
    repo_root = SCRIPT_DIR.parents[3]
    skill_manager_scripts = repo_root / ".agents" / "skills" / "skill-manager" / "scripts"
    policy_module_path = skill_manager_scripts / "repo_support" / "repo_cost_policy.py"
    spec = importlib.util.spec_from_file_location(
        "_local_ai_test_authoritative_cost_policy",
        policy_module_path,
    )
    assert spec and spec.loader
    sys.path.insert(0, str(skill_manager_scripts))
    try:
        policy_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(policy_module)
        from project_policy_contract_v2 import legacy_cost_policy_from_v2
    finally:
        sys.path.remove(str(skill_manager_scripts))

    authoritative = policy_module.default_cost_policy()
    tracked_document = json.loads((repo_root / ".agents" / "project-policy.json").read_text(encoding="utf-8"))
    assert tracked_document["schema_version"] == 2
    assert tracked_document["$schema"] == "skills/skill-manager/assets/schemas/project-policy.schema.json"
    tracked = tracked_document["cost_policy"]
    adapted = legacy_cost_policy_from_v2(tracked, authoritative)
    parity_fields = {
        "default_guidance_required",
        "default_guidance_budget_tokens",
        "default_guidance_files",
        "broad_guidance_baseline_files",
        "min_guidance_saved_percent",
        "startup_context_max_added_tokens",
        "startup_context_max_added_percent",
        "review_loop",
        "phase_budgets",
    }
    assert parity_fields <= set(authoritative)
    assert set(authoritative["phase_budgets"]) == {
        "routing",
        "planning",
        "implementation",
        "test-authoring",
        "validation",
        "evidence",
        "handoff",
    }
    def json_shape(value):
        if isinstance(value, dict):
            return {key: json_shape(item) for key, item in sorted(value.items())}
        if isinstance(value, list):
            return [json_shape(item) for item in value]
        return type(value).__name__

    assert json_shape(authoritative) == json_shape(adapted)
    assert not ({"id", "mode", "schema_version"} & set(tracked))
    local_ai_tracked = json.loads(
        (repo_root / ".agents" / "local-ai.json").read_text(encoding="utf-8")
    )
    assert "cost_policy" not in local_ai_tracked

    fresh_root = tmp / "fresh"
    setup_impl.write_default_config(fresh_root, force=True)
    fresh = json.loads(
        (fresh_root / ".agents" / "local-ai.json").read_text(encoding="utf-8")
    )
    assert "cost_policy" not in fresh

    first = setup_impl.load_raw_config(tmp / "first")
    second = setup_impl.load_raw_config(tmp / "second")
    assert "cost_policy" not in first
    assert "cost_policy" not in second


def test_resource_report_has_stable_shape(tmp):
    report = resources_impl.resource_report(tmp)
    assert_fields(report, schema_version=1, tool="local-ai-helper.resources")
    assert_ok(report)
    assert report["cpu"]["logical_cores"] >= 1
    assert report["cpu"]["suggested_threads"] >= 1
    assert_has_all(report["recommendations"], "memory_strategy")
    summary = resources_impl.resource_summary(report, compact=True)
    assert_field(summary, "tool", "local-ai-helper.resources")
    assert summary["gpu"]["device_count"] >= 0
    assert_lacks_all(summary["gpu"], "devices")
    assert_lacks_all(summary, "root")


def test_resource_report_includes_host_memory_topology(tmp):
    report = resources_impl.resource_report(tmp)
    topology = report["host_memory_topology"]

    assert_has_all(topology, "schema_version", "platform", "env", "notes", "portability_class")
    assert_field(topology, "schema_version", 1)
    assert_has_all(topology["platform"], "system", "machine")
    assert topology["portability_class"] in {
        "portable-no-admin",
        "process-env-only",
        "custom-fork",
    }


def test_host_memory_topology_records_runtime_driver_env():
    env_names = (
        "VK_ICD_FILENAMES",
        "VK_DRIVER_FILES",
        "RADV_PERFTEST",
        "MESA_VK_DEVICE_SELECT",
        "GPU_TARGETS",
        "GGML_HIP_NO_VMM",
        "GGML_VK_FORCE_MAX_ALLOCATION_SIZE",
    )
    original = {name: os.environ.get(name) for name in env_names}
    try:
        os.environ["VK_ICD_FILENAMES"] = "radv.json"
        os.environ["VK_DRIVER_FILES"] = "/usr/share/vulkan/icd.d/radeon_icd.x86_64.json"
        os.environ["RADV_PERFTEST"] = "unified_heap,nogttspill"
        os.environ["MESA_VK_DEVICE_SELECT"] = "1002:abcd"
        os.environ["GGML_HIP_NO_VMM"] = "1"
        os.environ["GGML_VK_FORCE_MAX_ALLOCATION_SIZE"] = "8589934592"
        os.environ.pop("GPU_TARGETS", None)

        topology = resources_impl.host_memory_topology()

        assert_field(topology["env"], "VK_ICD_FILENAMES", "radv.json")
        assert_field(topology["env"], "VK_DRIVER_FILES", "/usr/share/vulkan/icd.d/radeon_icd.x86_64.json")
        assert_field(topology["env"], "RADV_PERFTEST", "unified_heap,nogttspill")
        assert_field(topology["env"], "MESA_VK_DEVICE_SELECT", "1002:abcd")
        assert_field(topology["env"], "GGML_HIP_NO_VMM", "1")
        assert_field(topology["env"], "GGML_VK_FORCE_MAX_ALLOCATION_SIZE", "8589934592")
        assert_field(topology, "portability_class", "process-env-only")
        assert any("RADV_PERFTEST=unified_heap" in note for note in topology["notes"])
        assert any("not portable defaults" in note for note in topology["notes"])
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_host_memory_topology_keeps_vram_and_gtt_as_separate_pools():
    topology = {
        "drm": [
            {
                "card": "card0",
                "mem_info_vram_total": "8589934592",
                "mem_info_gtt_total": "133143986176",
            }
        ]
    }

    resources_impl.add_drm_memory_pool_semantics(topology)

    semantics = topology["drm"][0]["memory_pool_semantics"]
    assert_field(semantics, "vram_total_bytes", 8589934592)
    assert_field(semantics, "gtt_total_bytes", 133143986176)
    assert_field(semantics, "sum_reported_bytes", 141733920768)
    assert_field(semantics, "reported_gpu_total_bytes", 141733920768)
    assert_field(semantics, "memory_pool_policy", "do-not-sum-vram-gtt")
    assert_field(semantics, "overcount_risk", True)
    assert_field(semantics, "apu_unified_memory_guidance", "do-not-sum-vram-and-gtt-without-allocator-proof")
    assert_contains(semantics["measurement_sources"], "sysfs:mem_info_vram_total")
    assert "hipMallocManaged" in semantics["allocator_note"]


def test_gpu_classifier_recognizes_amd_integrated_graphics():
    classification = resources_impl.classify_gpu_device(AMD_780M, backend="windows-display")
    assert_mapping_items(classification, {"vendor": "amd", "device_type": "integrated", "safe_for_auto": True})


def test_bootstrap_writes_local_settings_and_gitignore_rule(tmp):
    gitignore = tmp / ".gitignore"
    gitignore.write_text("#\n", encoding="utf-8", newline="\n")

    report = setup_local_ai.bootstrap(root=tmp, download=False)
    settings = read_local_settings(tmp)
    gitignore_text = gitignore.read_text(encoding="utf-8")

    assert_field(report, "local_settings_written", True)
    assert_field(settings["gpu"], "mode", "auto")
    assert settings["gpu"]["preferred_backends"][:2] == ["cuda", "vulkan"]
    assert_has_all(gitignore_text, ".agents/local-ai/local.settings.json")


def test_gpu_local_settings_off_keeps_auto_backend_cpu(tmp):
    write_auto_cpu_config(tmp)
    write_local_settings(
        tmp,
        {"schema_version": 1, "gpu": {"mode": "off"}},
    )
    write_text_bundle(
        tmp,
        runtimes=[{"backend": "cuda", "folder": "fake-cuda"}, {"backend": "cpu", "folder": "fake-cpu"}],
    )
    loaded = local_ai_routing.load_config(tmp, "skill-routing")
    manifest, issues = local_ai_routing.load_bundle(tmp, loaded)

    runtime, runtime_issues = local_ai_routing.select_runtime(tmp, loaded, manifest, check_only=True)

    assert_empty(issues)
    assert_fields(loaded, backend_order=["cpu"])
    assert_fields(loaded["gpu"], mode="off")
    assert_present(runtime)
    assert_field(runtime, "backend", "cpu")
    assert_empty(runtime_issues)


def test_gpu_auto_prefers_dedicated_nvidia_cuda_runtime_without_env(tmp):
    write_auto_cpu_config(tmp)
    setup_local_ai.write_default_local_settings(tmp, force=False)
    write_text_bundle(
        tmp,
        runtimes=[{"backend": "cuda", "folder": "fake-cuda"}, {"backend": "cpu", "folder": "fake-cpu"}],
    )

    with patched_attrs(routing_impl.resources_impl, gpu_info=nvidia_cuda_gpu_info):
        loaded = local_ai_routing.load_config(tmp, "skill-routing")
        manifest, _issues = local_ai_routing.load_bundle(tmp, loaded)
        runtime, runtime_issues = local_ai_routing.select_runtime(tmp, loaded, manifest, check_only=True)

    assert loaded["backend_order"][:2] == ["cuda", "vulkan"]
    assert_present(runtime)
    assert_field(runtime, "backend", "cuda")
    assert_empty(runtime_issues)


def test_gpu_auto_keeps_integrated_gpu_on_cpu_unless_allowed(tmp):
    write_auto_cpu_config(tmp)
    setup_local_ai.write_default_local_settings(tmp, force=False)
    write_text_bundle(
        tmp,
        runtimes=[{"backend": "vulkan", "folder": "fake-vulkan"}, {"backend": "cpu", "folder": "fake-cpu"}],
    )

    with patched_attrs(routing_impl.resources_impl, gpu_info=intel_iris_gpu_info):
        loaded = local_ai_routing.load_config(tmp, "skill-routing")
        manifest, _issues = local_ai_routing.load_bundle(tmp, loaded)
        runtime, runtime_issues = local_ai_routing.select_runtime(tmp, loaded, manifest, check_only=True)

    assert_fields(loaded, backend_order=["cpu"])
    assert_present(runtime)
    assert_field(runtime, "backend", "cpu")
    assert_empty(runtime_issues)


def test_gpu_auto_allows_intel_sycl_when_explicitly_preferred(tmp):
    write_auto_cpu_config(tmp)
    write_local_settings(
        tmp,
        {
            "schema_version": 1,
            "gpu": {
                "mode": "auto",
                "allow_integrated": True,
                "preferred_backends": ["sycl", "vulkan", "cpu"],
            },
        },
    )
    write_text_bundle(
        tmp,
        runtimes=[
            {"backend": "sycl", "folder": "fake-sycl"},
            {"backend": "vulkan", "folder": "fake-vulkan"},
            {"backend": "cpu", "folder": "fake-cpu"},
        ],
    )

    with patched_attrs(routing_impl.resources_impl, gpu_info=intel_arc_gpu_info):
        loaded = local_ai_routing.load_config(tmp, "skill-routing")
        manifest, _issues = local_ai_routing.load_bundle(tmp, loaded)
        runtime, runtime_issues = local_ai_routing.select_runtime(tmp, loaded, manifest, check_only=True)

    assert loaded["backend_order"][:2] == ["sycl", "vulkan"]
    assert_present(runtime)
    assert_field(runtime, "backend", "sycl")
    assert_empty(runtime_issues)


def test_local_runtime_override_can_supply_gpu_runtime(tmp):
    write_auto_cpu_config(tmp)
    override_cli = tmp / "tools" / "llama-cuda" / "llama-cli.exe"
    override_server = override_cli.with_name("llama-server.exe")
    override_cli.parent.mkdir(parents=True)
    override_cli.write_bytes(b"local cuda runtime")
    override_server.write_bytes(b"local cuda server")
    write_local_settings(
        tmp,
        {
            "schema_version": 1,
            "gpu": {"mode": "force", "preferred_backends": ["cuda", "cpu"]},
            "runtime_overrides": [
                {
                    "backend": "cuda",
                    "path": "tools/llama-cuda/llama-cli.exe",
                    "server_path": "tools/llama-cuda/llama-server.exe",
                }
            ],
        },
    )
    write_text_bundle(tmp)

    loaded = local_ai_routing.load_config(tmp, "skill-routing")
    manifest, _issues = local_ai_routing.load_bundle(tmp, loaded)
    runtime, runtime_issues = local_ai_routing.select_runtime(tmp, loaded, manifest, check_only=True)
    model = model_ref(tmp / "model.gguf")
    prompt = tmp / "prompt.txt"
    prompt.write_text("x\n", encoding="utf-8", newline="\n")
    command = local_ai_routing.llama_command(runtime or {}, model, loaded, prompt)

    assert_present(runtime)
    assert_field(runtime, "backend", "cuda")
    assert_field(runtime, "resolved_path", str(override_cli.resolve()))
    assert_empty(runtime_issues)
    assert "-ngl" in command


def test_local_runtime_override_can_supply_cpu_runtime(tmp):
    write_auto_cpu_config(tmp)
    override_cli = tmp / "tools" / "llama-cpu-newer" / "llama-cli.exe"
    override_server = override_cli.with_name("llama-server.exe")
    override_cli.parent.mkdir(parents=True)
    override_cli.write_bytes(b"local cpu runtime")
    override_server.write_bytes(b"local cpu server")
    write_local_settings(
        tmp,
        {
            "schema_version": 1,
            "gpu": {"mode": "off", "preferred_backends": ["cpu"]},
            "runtime_overrides": [
                {
                    "backend": "cpu",
                    "path": "tools/llama-cpu-newer/llama-cli.exe",
                    "server_path": "tools/llama-cpu-newer/llama-server.exe",
                }
            ],
        },
    )
    write_text_bundle(tmp, runtimes=[{"backend": "cpu", "folder": "fake-cpu"}])

    loaded = local_ai_routing.load_config(tmp, "skill-routing")
    manifest, _issues = local_ai_routing.load_bundle(tmp, loaded)
    runtime, runtime_issues = local_ai_routing.select_runtime(tmp, loaded, manifest, check_only=True)

    assert_present(runtime)
    assert_field(runtime, "backend", "cpu")
    assert_field(runtime, "resolved_path", str(override_cli.resolve()))
    assert_empty(runtime_issues)


def test_gpu_auto_downloads_missing_runtime_before_cpu_fallback(tmp):
    write_auto_cpu_config(tmp)
    setup_local_ai.write_default_local_settings(tmp, force=False)
    write_text_bundle(tmp, runtimes=[{"backend": "cpu", "folder": "fake-cpu"}])

    original_probe = routing_impl.probe_runtime

    def fake_ensure(root, config, backends):
        assert backends == ["cuda", "vulkan"]
        override_cli = root / ".agents" / "local-ai" / "bundle" / "runtimes" / "fake-cuda" / "llama-cli.exe"
        override_server = override_cli.with_name("llama-server.exe")
        override_cli.parent.mkdir(parents=True, exist_ok=True)
        override_cli.write_bytes(b"local cuda runtime")
        override_server.write_bytes(b"local cuda server")
        settings = routing_impl.read_local_settings(root)
        settings["runtime_overrides"] = [
            {
                "backend": "cuda",
                "path": ".agents/local-ai/bundle/runtimes/fake-cuda/llama-cli.exe",
                "server_path": ".agents/local-ai/bundle/runtimes/fake-cuda/llama-server.exe",
            }
        ]
        routing_impl.write_local_settings(root, settings)
        return True, []

    def fake_probe(runtime, config, *, check_only = False):
        if str(runtime.get("backend", "")) == "cuda":
            return True, ""
        return original_probe(runtime, config, check_only=check_only)

    with patched_attrs(routing_impl.resources_impl, gpu_info=nvidia_cuda_gpu_info), patched_attrs(
        routing_impl,
        run_gpu_runtime_ensure_command=fake_ensure,
        probe_runtime=fake_probe,
    ):
        loaded = local_ai_routing.load_config(tmp, "skill-routing")
        manifest, _issues = local_ai_routing.load_bundle(tmp, loaded)
        runtime, runtime_issues = local_ai_routing.select_runtime(tmp, loaded, manifest, check_only=False)

    assert_present(runtime)
    assert_field(runtime, "backend", "cuda")
    assert_empty(runtime_issues)


def test_gpu_auto_download_failure_forces_local_cpu_mode(tmp):
    write_auto_cpu_config(tmp)
    setup_local_ai.write_default_local_settings(tmp, force=False)
    write_text_bundle(tmp, runtimes=[{"backend": "cpu", "folder": "fake-cpu"}])

    def fake_ensure(root, config, backends):
        return False, [GPU_DOWNLOAD_FAILURE]

    with patched_attrs(routing_impl.resources_impl, gpu_info=amd_7900_gpu_info), patched_attrs(
        routing_impl,
        run_gpu_runtime_ensure_command=fake_ensure,
    ):
        loaded = local_ai_routing.load_config(tmp, "skill-routing")
        manifest, _issues = local_ai_routing.load_bundle(tmp, loaded)
        runtime, runtime_issues = local_ai_routing.select_runtime(tmp, loaded, manifest, check_only=False)

    persisted = read_local_settings(tmp)
    assert_present(runtime)
    assert_field(runtime, "backend", "cpu")
    assert_empty(runtime_issues)
    assert_field(persisted["gpu"], "mode", "off")
    assert GPU_DOWNLOAD_FAILURE in persisted["gpu"]["last_failure"]


def test_gpu_auto_calibration_skips_slower_vulkan_for_profile(tmp):
    write_gpu_test_config(
        tmp,
        preferred_backends=["vulkan", "cpu"],
        runtimes=[{"backend": "vulkan", "folder": "fake-vulkan"}, {"backend": "cpu", "folder": "fake-cpu"}],
        auto_calibrate=True,
        performance_threshold_percent=10,
    )

    def fake_smoke(
        root,
        runtime,
        model,
        config,
    ):
        return True, ""

    def fake_measure(
        root,
        runtime,
        model,
        config,
        *,
        timeout_seconds,
    ):
        backend = str(runtime.get("backend", "cpu"))
        elapsed_ms = 1000.0 if backend == "cpu" else 1300.0
        return {"ok": True, "backend": backend, "elapsed_ms": elapsed_ms, "issue": ""}

    with patched_attrs(routing_impl.resources_impl, gpu_info=amd_780m_gpu_info), patched_attrs(
        routing_impl,
        probe_runtime=probe_with_manifest_hash,
        runtime_workload_smoke=fake_smoke,
        measure_runtime_sample=fake_measure,
    ):
        loaded = local_ai_routing.load_config(tmp, "skill-routing")
        model, model_issues, runtime, runtime_issues = select_loaded_runtime(tmp, loaded)

    persisted = read_local_settings(tmp)
    calibration = persisted["backend_calibrations"][0]
    assert_empty(model_issues)
    assert_present(model, runtime)
    assert_field(runtime, "backend", "cpu")
    assert_empty(runtime_issues)
    assert_mapping_items(
        calibration,
        {
            "profile": TEXT_PROFILE,
            "backend": "vulkan",
            "decision": "cpu",
            "gpu_e2e_latency_ms": 1300.0,
            "cpu_e2e_latency_ms": 1000.0,
        },
    )
    assert "slower" in calibration["reason"]


def test_gpu_workload_smoke_failure_quarantines_backend_without_disabling_auto(tmp):
    write_gpu_test_config(
        tmp,
        preferred_backends=["hip", "cpu"],
        runtimes=[{"backend": "hip", "folder": "fake-hip"}, {"backend": "cpu", "folder": "fake-cpu"}],
        allow_experimental=True,
    )

    def fake_smoke(
        root,
        runtime,
        model,
        config,
    ):
        if str(runtime.get("backend", "")) == "hip":
            return False, ROCM_KERNEL_ERROR
        return True, ""

    with patched_attrs(routing_impl.resources_impl, gpu_info=amd_780m_gpu_info), patched_attrs(
        routing_impl,
        probe_runtime=probe_with_manifest_hash,
        runtime_workload_smoke=fake_smoke,
    ):
        loaded = local_ai_routing.load_config(tmp, "skill-routing")
        model, model_issues, runtime, runtime_issues = select_loaded_runtime(tmp, loaded)

    persisted = read_local_settings(tmp)
    quarantine = persisted["backend_quarantine"][0]
    assert_empty(model_issues)
    assert_present(model, runtime)
    assert_field(runtime, "backend", "cpu")
    assert_empty(runtime_issues)
    assert_field(persisted["gpu"], "mode", "auto")
    assert_mapping_items(quarantine, {"profile": TEXT_PROFILE, "backend": "hip", "reason": ROCM_KERNEL_ERROR})


def test_gpu_experimental_backend_is_quarantined_before_workload_smoke_without_opt_in(tmp):
    write_gpu_test_config(
        tmp,
        preferred_backends=["hip", "cpu"],
        runtimes=[{"backend": "hip", "folder": "fake-hip"}, {"backend": "cpu", "folder": "fake-cpu"}],
    )

    def fake_smoke(
        root,
        runtime,
        model,
        config,
    ):
        raise AssertionError("experimental HIP auto mode should not run a model workload without opt-in")

    with patched_attrs(routing_impl.resources_impl, gpu_info=amd_780m_gpu_info), patched_attrs(
        routing_impl,
        probe_runtime=probe_with_manifest_hash,
        runtime_workload_smoke=fake_smoke,
    ):
        loaded = local_ai_routing.load_config(tmp, "skill-routing")
        model, model_issues, runtime, runtime_issues = select_loaded_runtime(tmp, loaded)

    persisted = read_local_settings(tmp)
    quarantine = persisted["backend_quarantine"][0]
    assert_empty(model_issues)
    assert_present(model, runtime)
    assert_field(runtime, "backend", "cpu")
    assert_empty(runtime_issues)
    assert_field(persisted["gpu"], "mode", "auto")
    assert_mapping_items(quarantine, {"profile": TEXT_PROFILE, "backend": "hip"})
    assert "experimental workload" in quarantine["reason"]


def test_benchmark_backend_override_allows_experimental_workload_trial(tmp):
    write_gpu_test_config(
        tmp,
        preferred_backends=["cuda", "vulkan", "cpu"],
        runtimes=[{"backend": "hip", "folder": "fake-hip"}, {"backend": "cpu", "folder": "fake-cpu"}],
        mode="off",
        allow_integrated=False,
    )

    smoke_calls = []

    def fake_smoke(
        root,
        runtime,
        model,
        config,
    ):
        smoke_calls.append(str(runtime.get("backend", "")))
        return True, ""

    with patched_attrs(routing_impl.resources_impl, gpu_info=amd_780m_gpu_info), patched_attrs(
        routing_impl,
        probe_runtime=probe_with_manifest_hash,
        runtime_workload_smoke=fake_smoke,
    ):
        loaded = local_ai_routing.load_config(tmp, "skill-routing")
        setup_impl.apply_benchmark_backend_override(loaded, "hip")
        routing_impl.refresh_local_gpu_config(tmp, loaded)
        model, model_issues, runtime, runtime_issues = select_loaded_runtime(tmp, loaded)

    persisted = read_local_settings(tmp)
    assert_empty(model_issues)
    assert_present(model, runtime)
    assert_field(runtime, "backend", "hip")
    assert_empty(runtime_issues)
    assert smoke_calls == ["hip"]
    assert_empty(persisted.get("backend_quarantine", []))


def test_status_explains_cpu_selected_by_calibration(tmp):
    write_auto_cpu_config(tmp)
    write_text_bundle(
        tmp,
        runtimes=[{"backend": "vulkan", "folder": "fake-vulkan"}, {"backend": "cpu", "folder": "fake-cpu"}],
    )
    manifest = read_json(tmp / ".agents" / "local-ai" / "bundle" / "manifest.json")
    model_entry = next(item for item in manifest["models"] if item["profile"] == TEXT_PROFILE)
    runtime_entry = next(item for item in manifest["runtimes"] if item["backend"] == "vulkan")
    write_local_settings(
        tmp,
        {
            "schema_version": 1,
            "gpu": {
                "mode": "auto",
                "allow_integrated": True,
                "preferred_backends": ["vulkan", "cpu"],
                "smoke_test_runtime": True,
            },
            "runtime_overrides": [],
            "backend_calibrations": [
                {
                    "profile": TEXT_PROFILE,
                    "backend": "vulkan",
                    "runtime_sha256": runtime_entry["sha256"],
                    "model_sha256": model_entry["sha256"],
                    "decision": "cpu",
                    "reason": VULKAN_SLOW_REASON,
                    "created_at_unix": 1,
                }
            ],
        },
    )

    def fake_probe(runtime, config, *, check_only = False):
        path = Path(str(runtime.get("resolved_path", "")))
        runtime["actual_sha256"] = local_ai_routing.sha256_file(path)
        return True, ""

    with patched_attrs(routing_impl.resources_impl, gpu_info=amd_780m_gpu_info), patched_attrs(routing_impl, probe_runtime=fake_probe):
        status = setup_impl.build_status(tmp, profile=TEXT_PROFILE)

    decision = status["backend_decision"]
    assert_field(status, "selected_runtime", "cpu")
    assert_mapping_items(decision, {"selected": "cpu", "reason": VULKAN_SLOW_REASON, "profile": TEXT_PROFILE})


def test_model_url_validation_reports_unreachable_profile_without_downloading(tmp):
    def fake_check(url, *, timeout_seconds = 10):
        if "Qwen3-Embedding-0.6B-Q8_0.gguf" in url:
            return {"ok": False, "status": 404, "url": url, "issue": "HTTP 404"}
        return {"ok": True, "status": 200, "url": url, "issue": ""}

    with patched_attrs(setup_impl, check_url=fake_check):
        report = setup_impl.model_url_validation_report(
            profiles=["qwen3-embedding-0.6b-q8"],
            timeout_seconds=3,
        )

    assert_not_ok(report)
    assert_field(report, "checked_profile_count", 1)
    assert_mapping_items(report["profiles"][0], {"profile": "qwen3-embedding-0.6b-q8", "ok": False})
    assert_contains(report["issues"], "HTTP 404")


def test_detached_benchmark_sweep_command_lists_backend_matrix(tmp):
    report = setup_impl.detached_benchmark_sweep_command(
        tmp,
        profiles=[TEXT_PROFILE],
        backends=["cpu", "vulkan", "hip", "sycl"],
        repetitions=2,
        standard_metrics=True,
        validate_model_urls=True,
    )

    commands = report["commands"]
    assert_field(report, "tool", "local-ai-helper.detached-benchmark-sweep-command")
    assert [item["backend"] for item in commands] == ["cpu", "vulkan", "hip", "sycl"]
    assert_command_rows_include(commands, f"--profile {TEXT_PROFILE}", "--standard-metrics", "--validate-model-urls")
    assert "--backend cpu" in commands[0]["command"]
    assert "--backend vulkan" in commands[1]["command"]


def test_benchmark_backend_override_survives_local_settings_refresh(tmp):
    write_config(tmp)
    write_local_settings(
        tmp,
        {
            "schema_version": 1,
            "gpu": {
                "mode": "off",
                "preferred_backends": ["cuda", "vulkan", "cpu"],
            },
            "runtime_overrides": [],
        },
    )
    loaded = local_ai_routing.load_config(tmp, "skill-routing")

    setup_impl.apply_benchmark_backend_override(loaded, "vulkan")
    routing_impl.refresh_local_gpu_config(tmp, loaded)

    assert_mapping_items(loaded, {"backend_order": ["vulkan", "cpu"], "configured_backend_order": ["vulkan", "cpu"]})
    assert_mapping_items(loaded["gpu"], {"mode": "force", "allow_integrated": True})


def test_bench_reports_effective_backend_when_override_falls_back_to_cpu(tmp):
    write_config(tmp)

    def fake_load_bundle(root, config):
        return {"models": [], "runtimes": []}, []

    def fake_select_model(root, config, manifest):
        return model_ref(Path("model.gguf"), TEXT_PROFILE), []

    def fake_select_runtime(root, config, manifest, *, check_only):
        config["backend_decision"] = {
            "selected": "cpu",
            "profile": TEXT_PROFILE,
            "backend": "vulkan",
            "reason": VULKAN_SLOW_REASON,
            "source": "local-calibration",
        }
        return {"backend": "cpu", "resolved_path": "llama-cli.exe"}, [VULKAN_SLOW_REASON]

    def fake_run_model(**_kwargs):
        return True, {}, 1.0, ""

    output = io.StringIO()
    with patched_attrs(
        local_ai_routing,
        load_bundle=fake_load_bundle,
        select_model=fake_select_model,
        select_runtime=fake_select_runtime,
        run_model=fake_run_model,
    ):
        with contextlib.redirect_stdout(output):
            status = setup_local_ai.bench(
                tmp,
                run_model=True,
                profiles=[TEXT_PROFILE],
                repetitions=1,
                standard_metrics=True,
                backend="vulkan",
                as_json=True,
            )

    report = json.loads(output.getvalue())
    row = report["rows"][0]
    assert status == 0
    assert_has_all(report, "host_memory_topology")
    assert_field(row, "requested_backend", "vulkan")
    assert_field(row, "effective_backend", "cpu")
    assert_field(row["backend_decision"], "reason", VULKAN_SLOW_REASON)
    assert_contains(row["setup_issues"], VULKAN_SLOW_REASON)


def test_runtime_ensure_gpu_dry_run_writes_no_local_files(tmp):
    original_platform_id = setup_impl.local_ai_routing.platform_id

    try:
        setup_impl.local_ai_routing.platform_id = lambda: "windows-x64"
        report = setup_impl.ensure_gpu_runtime_report(tmp, backends=["vulkan"], dry_run=True)
    finally:
        setup_impl.local_ai_routing.platform_id = original_platform_id

    assert_ok(report)
    assert_field(report, "status", "dry-run")
    assert not local_settings_path(tmp).exists()
    assert not tmp.joinpath(".gitignore").exists()


def test_llama_timing_parser_and_standard_aggregation():
    output = """
llama_perf_context_print:        load time =    420.50 ms
llama_perf_context_print: prompt eval time =   1200.00 ms /   300 tokens (    4.00 ms per token,   250.00 tokens per second)
llama_perf_context_print:        eval time =   2000.00 ms /    80 runs   (   25.00 ms per token,    40.00 tokens per second)
llama_perf_context_print:       total time =   3900.00 ms /   380 tokens
"""
    parsed = benchmark_metrics.parse_llama_timing_output(output)
    assert_mapping_items(
        parsed,
        {
            "model_load_ms": 420.5,
            "prompt_eval_ms": 1200.0,
            "decode_ms": 2000.0,
            "generated_tokens": 80,
            "tpot_ms": 25.0,
            "ttft_ms": 1620.5,
        },
    )

    first = benchmark_metrics.metrics_from_elapsed(1.0, cold_start=True, warm_cache=False, repetitions=2)
    second = benchmark_metrics.metrics_from_elapsed(2.0, cold_start=False, warm_cache=True, repetitions=2)
    aggregate = benchmark_metrics.aggregate_metrics([first, second])
    assert_fields(aggregate, repetitions=2)
    assert_fields(aggregate["p50"], e2e_latency_ms=1500.0)
    assert_fields(aggregate["p95"], e2e_latency_ms=1950.0)


def test_bootstrap_no_download_writes_config_and_reports_next_action(tmp):
    report = setup_local_ai.bootstrap(root=tmp, download=False)
    assert_mapping_items(report, {"config_written": True, "downloaded": False, "ready": False})
    assert "local-ai bootstrap" in report["next_action"]
    assert tmp.joinpath(".agents", "local-ai.json").exists()


def test_bootstrap_ensures_local_ai_gitignore_rules(tmp):
    gitignore = tmp / ".gitignore"
    gitignore.write_text("#\n", encoding="utf-8", newline="\n")

    first = setup_local_ai.bootstrap(root=tmp, download=False)
    second = setup_local_ai.bootstrap(root=tmp, download=False)
    text = gitignore.read_text(encoding="utf-8")

    assert_fields(first, gitignore_updated=True)
    assert_fields(second, gitignore_updated=False)
    assert_has_all(
        text,
        "# Local AI helper payloads",
        ".agents/local-ai/downloads/",
        ".agents/local-ai/cache/",
        ".agents/local-ai/bundle/models/*.gguf",
        ".agents/local-ai/bundle/runtimes/",
        ".agents/local-ai/secrets.local.json",
    )
    assert text.count(".agents/local-ai/bundle/models/*.gguf") == 1


def test_routing_auto_bootstrap_invokes_setup_when_bundle_missing(tmp):
    write_config(tmp)
    calls = []
    original = routing_impl.run_bootstrap_command

    def fake_bootstrap(root, config, *, task, check):
        calls.append((task, check))
        return False, ["bootstrap called"]

    routing_impl.run_bootstrap_command = fake_bootstrap
    try:
        result = local_ai_routing.route_items(
            tmp,
            "skill-routing",
            [{"id": "demo", "name": "demo", "description": "demo"}],
            allowed_categories=["General"],
            check=False,
        )
    finally:
        routing_impl.run_bootstrap_command = original

    assert calls == [("skill-routing", False)]
    assert "bootstrap called" in result["issues"]


def test_routing_check_does_not_auto_bootstrap(tmp):
    write_config(tmp)
    calls = []
    original = routing_impl.run_bootstrap_command

    def fake_bootstrap(root, config, *, task, check):
        calls.append(task)
        return True, []

    routing_impl.run_bootstrap_command = fake_bootstrap
    try:
        local_ai_routing.route_items(
            tmp,
            "skill-routing",
            [{"id": "demo", "name": "demo", "description": "demo"}],
            allowed_categories=["General"],
            check=True,
        )
    finally:
        routing_impl.run_bootstrap_command = original

    assert_empty(calls)


def test_default_config_routes_to_current_profiles(tmp):
    write_config(tmp)
    skill_config = local_ai_routing.load_config(tmp, "skill-routing")
    assert_field(skill_config, "profile_order", [TEXT_PROFILE])
    assert_mapping_items(skill_config["limits"], {"cache_type_k": "q4_0", "cache_type_v": "q4_0", "reasoning": "off"})
    assert_field(
        skill_config,
        "embedding_profiles",
        [
            EMBEDDING_PROFILE,
            "qwen3-embedding-0.6b-q8",
            "qwen3-embedding-4b-q5km",
            "qwen3-embedding-8b-q4km",
        ],
    )
    assert_field(skill_config, "vision_profiles", [VISION_PROFILE])


def test_layered_settings_precedence_and_task_source(tmp):
    write_config(tmp)
    local_ai_routing.write_project_settings(
        tmp,
        {
            "task_model_profiles": {"skill-routing": [TEXT_PROFILE]},
            "limits": {"threads": 4, "context_tokens": 2048},
            "backend_order": ["cpu"],
        },
    )
    local_ai_routing.write_local_settings(
        tmp,
        {
            "gpu": {"mode": "off"},
            "task_model_profiles": {"skill-routing": [TEXT_PROFILE]},
            "limits": {"threads": 6, "output_tokens": 77},
        },
    )

    loaded = local_ai_routing.load_config(tmp, "skill-routing")

    assert_field(loaded, "profile_order", [TEXT_PROFILE])
    assert_field(loaded, "task_route_source", "local-settings")
    assert_mapping_items(loaded["limits"], {"threads": 6, "context_tokens": 2048, "output_tokens": 77})
    assert_mapping_items(
        loaded["limit_sources"],
        {"threads": "local-settings", "context_tokens": "project-settings", "output_tokens": "local-settings"},
    )
    assert_field(loaded, "backend_order_source", "project-settings")


def test_layered_settings_reject_unknown_task_and_catalog_profile(tmp):
    write_config(tmp)
    write_json(
        tmp / local_ai_routing.PROJECT_SETTINGS_RELATIVE_PATH,
        {
            "schema_version": 1,
            "task_model_profiles": {
                "unknown-task": [TEXT_PROFILE],
                "skill-routing": ["arbitrary-gguf"],
            },
            "limits": {"tool_permissions": 1},
            "model_profiles": {TEXT_PROFILE: {"download_url": "https://example.invalid/model.gguf"}},
        },
    )

    loaded = local_ai_routing.load_config(tmp, "skill-routing")

    assert_field(loaded, "profile_order", [TEXT_PROFILE])
    assert_contains_each(
        loaded["settings_issues"],
        "unknown task",
        "outside the validated catalog",
        "Unsupported performance fields",
    )


def configure_resources(gpu):
    return {
        "cpu": {"logical_cores": 16, "suggested_threads": 8},
        "memory": {"available_gb": 24.0, "total_gb": 32.0},
        "disk": {"free_gb": 100.0},
        "gpu": gpu,
    }


def test_guided_backend_proposals_cover_cpu_nvidia_amd_intel_and_integrated_disabled():
    cases = [
        ({"available": False, "devices": []}, False, ("off", ["cpu"])),
        (nvidia_cuda_gpu_info(), False, ("auto", ["cuda", "vulkan", "cpu"])),
        (amd_7900_gpu_info(), False, ("auto", ["vulkan", "hip", "cpu"])),
        (intel_arc_gpu_info(), True, ("auto", ["sycl", "vulkan", "cpu"])),
        (amd_780m_gpu_info(), False, ("off", ["cpu"])),
    ]
    for gpu, allow_integrated, expected in cases:
        mode, order, _reason = setup_impl.detected_backend_proposal(
            configure_resources(gpu), allow_integrated=allow_integrated
        )
        assert (mode, order) == expected


def test_configure_preview_and_apply_are_download_free_and_scope_owned(tmp):
    write_config(tmp)

    def forbidden_download(*_args, **_kwargs):
        raise AssertionError("configure must not download")

    with patched_attrs(setup_impl._resources_impl, resource_report=lambda _root: configure_resources(nvidia_cuda_gpu_info())), patched_attrs(
        setup_impl, download_bundle=forbidden_download
    ):
        preview = setup_impl.local_ai_configuration_proposal(
            tmp,
            scope="local",
            route_values=[f"vision-describe={VISION_PROFILE}"],
            group_route_values=[],
            backend_order=None,
            gpu_mode=None,
            gpu_layers=None,
            allow_integrated=False,
            performance={"threads": 7, "context_tokens": 3072},
        )
        with contextlib.redirect_stdout(io.StringIO()):
            result = setup_impl.print_local_ai_configure(
                tmp,
                scope="project",
                route_values=[f"vision-describe={VISION_PROFILE}"],
                group_route_values=[],
                backend_order="cpu",
                gpu_mode=None,
                gpu_layers=None,
                allow_integrated=False,
                performance={"threads": 5},
                apply_requested=True,
                as_json=True,
            )

    assert_ok(preview)
    assert_field(preview, "download_performed", False)
    assert_mapping_items(preview["proposed_settings"]["gpu"], {"mode": "auto"})
    assert_field(preview["proposed_settings"]["task_model_profiles"], "vision-describe", [VISION_PROFILE])
    assert result == 0
    assert not (tmp / local_ai_routing.LOCAL_SETTINGS_RELATIVE_PATH).exists()
    project = read_json(tmp / local_ai_routing.PROJECT_SETTINGS_RELATIVE_PATH)
    assert_field(project["task_model_profiles"], "vision-describe", [VISION_PROFILE])
    assert_field(project["limits"], "threads", 5)


def test_configure_rejects_invalid_task_profile_and_machine_fields_in_project_scope(tmp):
    write_config(tmp)
    options = {
        "scope": "local",
        "group_route_values": [],
        "backend_order": None,
        "gpu_mode": None,
        "gpu_layers": None,
        "allow_integrated": False,
        "performance": {},
    }
    for route, expected in (("not-a-task=nemotron3-nano4b", "Unknown or disabled"), ("skill-routing=outside-catalog", "outside the validated catalog")):
        try:
            setup_impl.local_ai_configuration_proposal(tmp, route_values=[route], **options)
        except RuntimeError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError(f"invalid route was accepted: {route}")
    try:
        setup_impl.local_ai_configuration_proposal(
            tmp,
            scope="project",
            route_values=[],
            group_route_values=[],
            backend_order=None,
            gpu_mode="force",
            gpu_layers=None,
            allow_integrated=False,
            performance={},
        )
    except RuntimeError as exc:
        assert "machine-owned" in str(exc)
    else:
        raise AssertionError("project scope accepted machine-owned GPU mode")


def test_config_explain_reports_source_memory_install_calibration_and_quarantine(tmp):
    write_config(tmp)
    local_ai_routing.write_local_settings(
        tmp,
        {
            "gpu": {"mode": "force", "allow_integrated": True, "preferred_backends": ["vulkan", "cpu"]},
            "backend_order": ["vulkan", "cpu"],
            "task_model_profiles": {"vision-describe": [VISION_PROFILE]},
            "backend_calibrations": [
                {"profile": VISION_PROFILE, "backend": "vulkan", "decision": "gpu", "reason": "fixture win"}
            ],
            "backend_quarantine": [
                {"profile": VISION_PROFILE, "backend": "vulkan", "reason": "fixture quarantine"}
            ],
        },
    )
    with patched_attrs(setup_impl._resources_impl, resource_report=lambda _root: configure_resources(amd_7900_gpu_info())):
        report = setup_impl.effective_config_explanation(tmp, task="vision-describe")

    assert_mapping_items(
        report,
        {
            "selected_profile": VISION_PROFILE,
            "configuration_source": "local-settings",
            "backend": "vulkan",
            "backend_source": "local-settings",
            "installation_state": "missing",
            "fallback_order": [],
            "download_performed": False,
        },
    )
    assert_field(report["calibration_decision"], "decision", "gpu")
    assert_field(report["quarantine_decision"], "reason", "fixture quarantine")
    assert_field(report["memory_fit"], "decision", "fits")
    assert_has_all(report["recommended_download_command"], "local-ai download", VISION_PROFILE)


def test_routing_defaults_split_keeps_public_constants():
    assert local_ai_routing.DEFAULT_MODEL_PROFILE == routing_defaults.DEFAULT_MODEL_PROFILE
    assert TEXT_PROFILE in local_ai_routing.DEFAULT_MODEL_CATALOG
    for task in ["skill-routing", "changed-files-summary", "failure-cluster", "test-gap-summary", "handoff-draft", "duplicate-overlap-detection"]:
        assert local_ai_routing.DEFAULT_TASK_MODEL_PROFILES[task] == [TEXT_PROFILE]
    assert routing_impl.DEFAULT_TOOLS_CONFIG is routing_defaults.DEFAULT_TOOLS_CONFIG


def test_catalog_policy_rejects_unclear_or_indirect_models():
    issues = local_ai_routing.validate_model_catalog(
        {
            "bad": {
                "profile": "bad",
                "license": "custom-restrictive",
                "download_kind": "manual",
                "source_url": "file:///models/bad.gguf",
                "requires_account": True,
            }
        }
    )
    assert_contains_each(issues, "unsupported license", "direct downloads", "requires an account")


def test_catalog_policy_allows_nvidia_open_model_license():
    issues = local_ai_routing.validate_model_catalog(
        {
            "nemotron": {
                "profile": "nemotron",
                "license": "NVIDIA Open Model License",
                "download_kind": "direct",
                "source_url": "https://huggingface.co/nvidia/model/resolve/main/model.gguf",
            }
        }
    )
    assert_empty(issues)


def test_llama_server_command_is_cpu_only_and_cached(tmp):
    write_config(tmp)
    config = local_ai_routing.load_config(tmp, "skill-routing")
    runtime = {
        "backend": "cpu",
        "resolved_path": str(tmp / "llama-cli.exe"),
        "server_resolved_path": str(tmp / "llama-server.exe"),
    }
    model = model_ref(tmp / f"{TEXT_PROFILE}.gguf")
    command = local_ai_routing.llama_server_command(runtime, model, config)
    assert command[0].endswith("llama-server.exe")
    assert_has_all(command, "--cache-prompt", "-ctk", "-ctv", "q4_0")
    assert_lacks_all(command, "-ngl", "--mlock")


def test_llama_cli_command_uses_simple_io_for_subprocess_stability(tmp):
    runtime = {"backend": "cpu", "resolved_path": str(tmp / "llama-cli.exe")}
    model = model_ref(tmp / "model.gguf")
    config = {"limits": dict(local_ai_routing.DEFAULT_LIMITS)}
    prompt = tmp / "prompt.txt"
    prompt.write_text("x\n", encoding="utf-8", newline="\n")

    command = local_ai_routing.llama_command(runtime, model, config, prompt)

    assert_has_all(
        command,
        "--single-turn",
        "--simple-io",
        "--log-disable",
        "--no-perf",
        "--log-colors",
        "off",
        "-no-cnv",
        "--skip-chat-parsing",
    )


def test_llama_command_prefers_completion_binary_when_available(tmp):
    runtime_dir = tmp / "runtime"
    runtime_dir.mkdir()
    cli_path = runtime_dir / "llama-cli.exe"
    completion_path = runtime_dir / "llama-completion.exe"
    cli_path.write_text("cli", encoding="utf-8", newline="\n")
    completion_path.write_text("completion", encoding="utf-8", newline="\n")
    runtime = {"backend": "cpu", "resolved_path": str(cli_path)}
    model = model_ref(tmp / "model.gguf")
    config = {"limits": dict(local_ai_routing.DEFAULT_LIMITS)}
    prompt = tmp / "prompt.txt"
    prompt.write_text("hello\n", encoding="utf-8", newline="\n")

    command = local_ai_routing.llama_command(runtime, model, config, prompt)

    assert command[0] == str(completion_path)


def test_run_model_adds_routing_json_schema(tmp):
    runtime = {"backend": "cpu", "resolved_path": str(tmp / "llama-cli.exe")}
    model = model_ref(tmp / "model.gguf")
    config = {"limits": dict(local_ai_routing.DEFAULT_LIMITS)}
    observed_schema = {}

    class Completed:
        returncode = 0
        stdout = '{"category":"General","use_when":"check local routing","confidence":0.95}'

    original_run = routing_impl.subprocess.run

    def fake_run(command, **_kwargs):
        assert "--json-schema-file" in command
        schema_path = Path(command[command.index("--json-schema-file") + 1])
        observed_schema.update(read_json(schema_path))
        return Completed()

    try:
        routing_impl.subprocess.run = fake_run
        accepted, fields, confidence, reason = local_ai_routing.run_model(
            root=tmp,
            task="skill-routing",
            item={
                "id": "local-ai-helper",
                "name": "local-ai-helper",
                "category": "General",
                "description": "Use when routing.",
            },
            allowed_categories=["General", "AI Agents"],
            model=model,
            runtime=runtime,
            config=config,
        )
    finally:
        routing_impl.subprocess.run = original_run

    assert accepted is True
    assert_fields({"reason": reason, "confidence": confidence}, reason="", confidence=0.95)
    assert_fields(fields, category="General")
    assert_fields(observed_schema, required=["category", "use_when", "confidence"])
    assert_fields(observed_schema["properties"]["category"], enum=["General", "AI Agents"])


def test_runtime_doctor_detects_mtp_when_spec_help_is_not_in_tail(tmp):
    write_config(tmp)
    write_text_bundle(tmp)
    early_spec_help = "--spec-type none,draft-simple,draft-mtp\n" + ("x\n" * 3000)

    class Completed:
        returncode = 0
        stdout = early_spec_help

    original_run = setup_impl.subprocess.run

    def fake_run(_command, **_kwargs):
        return Completed()

    try:
        setup_impl.subprocess.run = fake_run
        report = setup_impl.runtime_doctor_report(tmp)
    finally:
        setup_impl.subprocess.run = original_run

    assert_field(report["runtimes"][0], "mtp_supported", True)
    summary = setup_impl.runtime_doctor_summary(report, compact=True)
    assert_mapping_items(summary, {"runtime_count": 1, "mtp_supported_count": 1})
    assert_keys_lack(summary, "runtimes", "crash_safe_procedure")


def test_brokered_repo_access_is_read_only_and_contained(tmp):
    write_config(tmp)
    write_doc(tmp, "local-ai.md", "local AI helper\n")
    config = local_ai_routing.load_config(tmp, "skill-routing")

    read_result = local_ai_routing.broker_tool_request(
        tmp, config, {"tool": "repo.read", "path": "docs/local-ai.md"}
    )
    assert_ok(read_result)
    assert "local AI helper" in read_result["content"]

    tree_result = local_ai_routing.broker_tool_request(tmp, config, {"tool": "repo.tree", "path": "docs"})
    assert_ok(tree_result)
    assert_field(tree_result, "entries", ["docs/local-ai.md"])

    escaped = local_ai_routing.broker_tool_request(
        tmp, config, {"tool": "repo.read", "path": "../outside.txt"}
    )
    assert_not_ok(escaped)

    write_attempt = local_ai_routing.broker_tool_request(
        tmp,
        config,
        {"tool": "repo.write", "path": ".agents/local-ai/cache/model.txt", "content": "x"},
    )
    assert_not_ok(write_attempt)


def test_brokered_search_reports_backend_and_uses_ripgrep_when_available(tmp):
    write_config(tmp)
    docs = write_doc(tmp, "local-ai.md", "local AI\n")
    config = local_ai_routing.load_config(tmp, "skill-routing")

    original_which = routing_impl.shutil.which
    original_run = routing_impl.subprocess.run
    commands = []

    class Completed:
        returncode = 0
        stdout = f"{docs / 'local-ai.md'}:1:local AI\n"
        stderr = ""

    def fake_run(command, **_kwargs):
        commands.append(command)
        return Completed()

    try:
        routing_impl.shutil.which = lambda name: "rg.exe" if name == "rg" else None
        routing_impl.subprocess.run = fake_run
        result = local_ai_routing.broker_tool_request(
            tmp, config, {"tool": "repo.search", "path": "docs", "pattern": "local AI"}
        )
    finally:
        routing_impl.shutil.which = original_which
        routing_impl.subprocess.run = original_run

    assert_ok(result)
    assert_field(result, "search_backend", "ripgrep")
    assert_field(result["results"][0], "path", "docs/local-ai.md")
    command = commands[0]
    assert_has_all(command, "--hidden", "--glob", "!.git/**")


def test_brokered_search_prefers_verified_portable_ripgrep(tmp):
    write_config(tmp)
    docs = write_doc(tmp, "portable.md", "portable\n")
    key = broker_tools.platform_key()
    executable = "rg.exe" if key.startswith("windows-") else "rg"
    binary = tmp / broker_tools.PORTABLE_RG_CACHE_REL / key / executable
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"rg")
    binary_hash = broker_tools.sha256_file(binary)
    write_json(
        tmp / broker_tools.PORTABLE_RG_MANIFEST_REL,
        {
            "schema_version": 1,
            "tool": "ripgrep",
            "version": "15.1.0",
            "assets": {key: {"executable": executable}},
        },
    )
    write_json(
        tmp / broker_tools.PORTABLE_RG_CACHE_REL / key / "install.json",
        {"version": "15.1.0", "binary_sha256": binary_hash},
    )
    config = local_ai_routing.load_config(tmp, "skill-routing")

    original_which = routing_impl.shutil.which
    original_run = routing_impl.subprocess.run
    commands = []

    class Completed:
        returncode = 0
        stdout = f"{docs / 'portable.md'}:1:portable\n"
        stderr = ""

    def fake_run(command, **_kwargs):
        commands.append(command)
        return Completed()

    try:
        routing_impl.shutil.which = lambda _name: None
        routing_impl.subprocess.run = fake_run
        result = local_ai_routing.broker_tool_request(
            tmp, config, {"tool": "repo.search", "path": "docs", "pattern": "portable"}
        )
    finally:
        routing_impl.shutil.which = original_which
        routing_impl.subprocess.run = original_run

    assert_ok(result)
    assert_field(result, "search_backend", "ripgrep")
    assert commands[0][0] == str(binary)


def test_brokered_search_reports_stdlib_fallback_when_ripgrep_is_missing(tmp):
    write_config(tmp)
    write_doc(tmp, "fallback.md", "unique-stdlib-fallback-token\n")
    config = local_ai_routing.load_config(tmp, "skill-routing")

    original_which = routing_impl.shutil.which
    try:
        routing_impl.shutil.which = lambda _name: None
        result = local_ai_routing.broker_tool_request(
            tmp, config, {"tool": "repo.search", "path": "docs", "pattern": "unique-stdlib-fallback-token"}
        )
    finally:
        routing_impl.shutil.which = original_which

    assert_ok(result)
    assert_field(result, "search_backend", "stdlib")
    assert_path(result["results"], "docs/fallback.md")


def test_brokered_search_stdlib_fallback_does_not_require_recursive_rglob(tmp):
    write_config(tmp)
    write_doc(tmp, "fallback.md", "unique-stdlib-fallback-token\n")
    config = local_ai_routing.load_config(tmp, "skill-routing")

    original_which = routing_impl.shutil.which
    original_rglob = Path.rglob

    def fail_rglob(self, pattern):
        raise AssertionError(f"rglob used: {pattern!r}")

    try:
        routing_impl.shutil.which = lambda _name: None
        Path.rglob = fail_rglob
        result = local_ai_routing.broker_tool_request(
            tmp, config, {"tool": "repo.search", "path": ".", "pattern": "unique-stdlib-fallback-token"}
        )
    finally:
        routing_impl.shutil.which = original_which
        Path.rglob = original_rglob

    assert_ok(result)
    assert_field(result, "search_backend", "stdlib")
    assert_path(result["results"], "docs/fallback.md")


def test_integrations_command_prints_targeted_rows(tmp):
    write_config(tmp)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = setup_local_ai.print_integrations(tmp, target="workflow", as_json=False)
    text = output.getvalue()
    assert status == 0
    assert_has_all(text, "workflow-routing")
    assert_lacks_all(text, "skill-routing")


def test_integrations_summary_can_omit_available_rows(tmp):
    _ = tmp
    rows = [
        {"id": "ready", "target": "skill", "task": "validation-triage", "available": True},
        {"id": "blocked", "target": "workflow", "task": "workflow-routing", "available": False, "mode": "missing"},
    ]
    summary = setup_impl.integration_summary(rows, compact=True)

    assert_mapping_items(summary, {"integration_count": 2, "available_count": 1, "unavailable_count": 1})
    assert [row["id"] for row in summary["integrations"]] == ["blocked"]


def test_bootstrap_command_help_mentions_direct_copy_use():
    help_text = setup_local_ai.build_parser().format_help()
    assert_has_all(help_text, "bootstrap", "ensure", "readiness", "policy", "doctor", "bench")
    subparsers = next(action for action in setup_local_ai.build_parser()._actions if getattr(action, "choices", None))
    bench_help = subparsers.choices["bench"].format_help()
    assert_has_all(bench_help, "--standard-metrics", "--repetitions")


def test_cli_help_classifies_read_only_and_write_boundaries():
    subparsers = next(action for action in setup_local_ai.build_parser()._actions if getattr(action, "choices", None))

    assert_has_all(subparsers.choices["readiness"].format_help(), "read-only")
    assert_has_all(subparsers.choices["policy"].format_help(), "read-only unless --write-default")
    assert_has_all(subparsers.choices["status"].format_help(), "may create gitignored local settings")
    assert_has_all(subparsers.choices["doctor"].format_help(), "read-only with --quick", "--full/--run-model")
    assert_has_all(subparsers.choices["task"].format_help(), "cache-writing")
    assert_has_all(subparsers.choices["vision"].format_help(), "cache-writing")
    assert_has_all(subparsers.choices["document"].format_help(), "cache-writing")
    assert_has_all(
        subparsers.choices["download"].format_help(),
        "marked for default installation",
        "currently text and",
        "vision",
    )
    assert_lacks_all(
        subparsers.choices["configure"].format_help(),
        "planning-review, embeddings",
    )
    assert "rag" not in subparsers.choices


def test_models_explain_defaults_separates_embedding_benchmark_candidate(tmp):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = setup_impl.print_models_explain_defaults(tmp, as_json=True)

    assert status == 0
    report = json.loads(output.getvalue())
    assert set(report["defaults"]) == {"text", "vision"}
    assert report["benchmark_candidates"]["embedding"] == setup_impl.EMBEDDING_PROFILE
    assert any("not defaults" in reason for reason in report["reasons"])


def test_setup_parser_split_keeps_public_surface():
    parser = setup_local_ai.build_parser()
    direct = setup_parser.build_parser(
        description="test parser",
        root_default=".",
        daily_text_tasks=setup_impl.DAILY_TEXT_TASKS,
        download_profiles=setup_impl.download_profile_names(),
        profile_choices=setup_impl.profile_names(),
        approved_owners=policy_impl.APPROVED_OWNERS,
        default_model_profile=local_ai_routing.DEFAULT_MODEL_PROFILE,
    )
    cases = [
        (["status", "--task", "skill-routing", *COMPACT_JSON], {"command": "status", "json": True, "task": "skill-routing", "summary": True, "compact": True}),
        (["readiness", *COMPACT_JSON], {"command": "readiness", "json": True, "summary": True, "compact": True}),
        (["doctor", "--quick", *COMPACT_JSON], {"command": "doctor", "quick": True, "json": True, "summary": True, "compact": True}),
        (["runtime", "doctor", *COMPACT_JSON], {"command": "runtime", "runtime_command": "doctor", "json": True, "summary": True, "compact": True}),
        (["runtime", "ensure-gpu", "--backend", "vulkan", "--probe", "--json"], {"command": "runtime", "runtime_command": "ensure-gpu", "backend": ["vulkan"], "probe": True, "json": True}),
        (["resources", *COMPACT_JSON], {"command": "resources", "json": True, "summary": True, "compact": True}),
        (["catalog", *COMPACT_JSON], {"command": "catalog", "json": True, "summary": True, "compact": True}),
        (["models", "inventory", "--disk", *COMPACT_JSON], {"command": "models", "models_action": "inventory", "disk": True, "summary": True, "compact": True}),
        (["models", "evaluate-candidate", "--candidate", "candidate.json", *COMPACT_JSON], {"command": "models", "models_action": "evaluate-candidate", "candidate": "candidate.json", "json": True, "summary": True, "compact": True}),
        (["vision", "describe", "--image", "image.png", "--json"], {"command": "vision", "vision_command": "describe", "image": "image.png", "json": True}),
        (["document", "inspect", "--file", "sample.pdf", "--json"], {"command": "document", "document_command": "inspect", "file_path": "sample.pdf", "json": True}),
    ]
    for args, expected in cases:
        assert_parsed(parser, args, expected)
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            parser.parse_args(["rag", "status"])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("retired RAG command was accepted")

    assert "bootstrap" in direct.format_help()


def test_setup_catalog_split_keeps_public_constants():
    assert setup_local_ai.MODEL_PACKAGES is setup_catalog.MODEL_PACKAGES
    assert setup_impl.MODEL_PACKAGES is setup_catalog.MODEL_PACKAGES
    assert setup_local_ai.DEFAULT_CONFIG is setup_catalog.DEFAULT_CONFIG
    assert "cost_policy" not in setup_catalog.DEFAULT_CONFIG
    assert_field(setup_local_ai.DEFAULT_CONFIG["bootstrap"], "default_profiles", DEFAULT_PROFILES)
    assert setup_local_ai.default_bootstrap_profile_names() == DEFAULT_PROFILES
    assert setup_local_ai.profile_names(
        include_optional=False,
        include_embeddings=True,
        include_vision=True,
    ) == DEFAULT_PROFILES


def test_bench_uses_embedding_path_for_embedding_profile(tmp):
    write_config(tmp)

    def fake_load_bundle(root, config):
        return {"models": [], "runtimes": []}, []

    def fake_select_model(root, config, manifest):
        return model_ref(Path("embedding.gguf"), EMBEDDING_PROFILE), []

    def fake_select_runtime(
        root,
        config,
        manifest,
        *,
        check_only,
    ):
        return {"backend": "cpu", "resolved_path": "llama-cli.exe"}, []

    output = io.StringIO()
    with patched_attrs(
        local_ai_routing,
        load_bundle=fake_load_bundle,
        select_model=fake_select_model,
        select_runtime=fake_select_runtime,
    ), patched_attrs(setup_impl, embed_texts=embedding_recorder(expected_profile=EMBEDDING_PROFILE)):
        with contextlib.redirect_stdout(output):
            status = setup_local_ai.bench(
                tmp,
                run_model=True,
                profiles=[EMBEDDING_PROFILE],
                repetitions=2,
                standard_metrics=True,
                as_json=True,
            )

    report = json.loads(output.getvalue())
    row = report["rows"][0]
    assert status == 0
    assert_mapping_items(row, {"mode": "embedding", "accepted": 2, "vectors": 8, "dimensions": [2]})


def test_doctor_uses_embedding_smoke_for_embedding_profile(tmp):
    write_config(tmp)
    _write_local_ai_bundle(tmp, profiles=[EMBEDDING_PROFILE])
    calls = []

    def fail_text_model(**_kwargs):
        raise AssertionError("embedding doctor smoke must not use text JSON routing")

    with patched_attrs(
        setup_impl,
        embed_texts=embedding_recorder(
            calls,
            vector_factory=lambda _index, _text: [1.0, 0.0, 0.5],
            record_profile=True,
        ),
    ), patched_attrs(local_ai_routing, run_model=fail_text_model):
        report = setup_impl.doctor_report(tmp, run_model=True, profile=EMBEDDING_PROFILE)

    smoke = report["model_smoke"]
    assert_ok(report)
    assert calls and calls[0][1] == EMBEDDING_PROFILE
    assert_mapping_items(
        smoke,
        {
            "mode": "embedding",
            "kind": "embedding",
            "vectors": len(setup_catalog.EMBEDDING_BENCH_TEXTS),
            "texts": len(setup_catalog.EMBEDDING_BENCH_TEXTS),
            "dimensions": [3],
        },
    )


def test_daily_command_help_mentions_task_and_vision():
    help_text = setup_local_ai.build_parser().format_help()
    assert_has_all(help_text, "task", "vision", "document")
    assert "rag" not in help_text.lower()


def test_mermaid_and_process_diagrams_are_not_local_ai_tasks(tmp):
    write_config(tmp)
    blocked_tasks = {"mermaid-diagram-draft", "workflow-process-draft"}
    assert setup_impl.DAILY_TEXT_TASKS.isdisjoint(blocked_tasks)
    for task in blocked_tasks:
        decision = policy_impl.evaluate_use_case(tmp, task, "local-ai-helper")
        assert decision["allowed"] is False
        assert "Unknown local AI use case" in decision["reason"]


def test_cost_saving_daily_tasks_are_advisory_text_tasks(tmp):
    write_config(tmp)
    expected = {
        "changed-files-summary",
        "failure-cluster",
        "test-gap-summary",
        "handoff-draft",
        "duplicate-overlap-detection",
    }
    assert expected.issubset(setup_impl.DAILY_TEXT_TASKS)
    for task in expected:
        decision = policy_impl.evaluate_use_case(tmp, task, "workflow-manager")
        prompt = setup_local_ai.daily_task_prompt(task, [{"path": "<stdin>", "text": "evidence", "truncated": False}])
        assert decision["allowed"] is True, decision
        assert_has_all(prompt, "Return one compact JSON object", "files were edited")


def test_daily_task_report_uses_stdin_and_stable_json_shape(tmp):
    original = setup_impl.run_daily_text_model

    def fake_run(root, task, prompt):
        assert root == tmp
        assert task == "validation-triage"
        assert "validation failed" in prompt
        return {
            "ok": True,
            "summary": VALIDATION_FAILED_SUMMARY,
            "findings": ["Missing."],
            "suggestions": [VALIDATION_FAILED_SUGGESTION],
            "evidence": [{"source": "stdin", "excerpt": "validation failed"}],
            "issues": [],
        }

    setup_impl.run_daily_text_model = fake_run
    try:
        report = setup_local_ai.daily_task_report(
            tmp,
            task="validation-triage",
            inputs=["-"],
            stdin_text="validation failed\n",
        )
    finally:
        setup_impl.run_daily_text_model = original

    assert_ok(report)
    assert_mapping_items(
        report,
        {
            "task": "validation-triage",
            "profile": TEXT_PROFILE,
            "input_paths": ["<stdin>"],
            "summary": VALIDATION_FAILED_SUMMARY,
            "findings": ["Missing."],
            "suggestions": [VALIDATION_FAILED_SUGGESTION],
        },
    )
    assert_field(report["evidence"][0], "source", "stdin")
    assert report["cache_path"].startswith(".agents/local-ai/cache/")
    assert_empty(report["issues"])

    def fail_if_called(_root, _task, _prompt):
        raise AssertionError("daily task cache was not used")

    setup_impl.run_daily_text_model = fail_if_called
    try:
        cached = setup_local_ai.daily_task_report(
            tmp,
            task="validation-triage",
            inputs=["-"],
            stdin_text="validation failed\n",
        )
    finally:
        setup_impl.run_daily_text_model = original

    assert_ok(cached)
    assert_field(cached, "cache_hit", True)
    assert_field(cached, "status", "cache")
    assert_field(cached, "summary", VALIDATION_FAILED_SUMMARY)


def test_small_changed_files_summary_uses_deterministic_fast_path(tmp):
    original = setup_impl.run_daily_text_model

    def fail_if_called(_root, _task, _prompt):
        raise AssertionError("small changed-files evidence should not start a model")

    setup_impl.run_daily_text_model = fail_if_called
    try:
        report = setup_local_ai.daily_task_report(
            tmp,
            task="changed-files-summary",
            inputs=["-"],
            stdin_text="src/App.cs: added lease renewal\ntests/AppTests.cs: added expiry coverage\nRisk: duplicate commands\n",
        )
    finally:
        setup_impl.run_daily_text_model = original

    assert_ok(report)
    assert_field(report, "model_invoked", False)
    assert_field(report, "decision", "deterministic-small-input")
    assert_field(report, "attempt_count", 0)
    assert_has_all(report["summary"], "src/App.cs", "tests/AppTests.cs")


def test_structured_changed_files_summary_avoids_slow_lossy_model_path(tmp):
    original = setup_impl.run_daily_text_model

    def fail_if_called(_root, _task, _prompt):
        raise AssertionError("structured changed-files evidence should not start a model")

    setup_impl.run_daily_text_model = fail_if_called
    try:
        report = setup_local_ai.daily_task_report(
            tmp,
            task="changed-files-summary",
            inputs=["-"],
            stdin_text="\n".join(
                f"src/Feature{index}.cs changed boundary {index}"
                for index in range(1, 21)
            ),
        )
    finally:
        setup_impl.run_daily_text_model = original

    assert_ok(report)
    assert_field(report, "model_invoked", False)
    assert_field(report, "decision", "deterministic-structured-input")
    assert_field(report, "input_line_count", 20)
    assert_field(report, "omitted_finding_count", 12)
    assert_has_all(report["summary"], "20 changed-file entries", "src/Feature1.cs")


def test_policy_disabled_task_does_not_bootstrap_or_start_runtime(tmp):
    bootstrap_calls = []
    disabled = {
        "enabled": False,
        "status": "policy-disabled",
        "reason": "Local AI use case 'validation-triage' is disabled by policy.",
    }
    with patched_attrs(setup_impl.local_ai_routing, load_config=lambda *_args, **_kwargs: disabled):
        with patched_attrs(setup_impl, bootstrap=lambda **kwargs: bootstrap_calls.append(kwargs)):
            model, runtime, config, issues = setup_impl.resolve_model_and_runtime(
                tmp,
                task="validation-triage",
                profile=TEXT_PROFILE,
            )

    assert model is None and runtime is None
    assert_field(config, "status", "policy-disabled")
    assert_empty(bootstrap_calls)
    assert_has_all(" ".join(issues), "disabled by policy")


def test_policy_disabled_daily_task_skips_lease_and_model_attempts(tmp):
    disabled = {
        "enabled": False,
        "status": "policy-disabled",
        "reason": "Local AI use case 'validation-triage' is disabled by policy.",
    }
    with patched_attrs(setup_impl.local_ai_routing, load_config=lambda *_args, **_kwargs: disabled):
        with patched_attrs(setup_impl, run_text_completion=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("model attempted"))):
            report = setup_local_ai.run_daily_text_model(tmp, "validation-triage", "failed")

    assert_not_ok(report)
    assert_field(report, "model_invoked", False)
    assert_field(report, "attempt_count", 0)
    assert_field(report, "fallback", "deterministic")
    assert_field(report, "decision", "policy-disabled")


def test_daily_text_command_uses_json_schema_file(tmp):
    write_config(tmp)
    original_resolve = setup_impl.resolve_model_and_runtime
    original_run = setup_impl.subprocess.run
    command_seen = []
    schema_payload = {}

    def fake_resolve(root, *, task, profile, check_only = False):
        return (
            model_ref(tmp / "model.gguf", profile),
            {"backend": "cpu", "resolved_path": str(tmp / "llama-cli.exe")},
            {
                "limits": {
                    "threads": 8,
                    "threads_batch": 8,
                    "context_tokens": 512,
                    "batch_size": 128,
                    "ubatch_size": 64,
                    "timeout_seconds": 5,
                    "output_tokens": 80,
                }
            },
            [],
        )

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "summary": VALIDATION_FAILED_SUMMARY,
                "findings": ["Stale."],
                "suggestions": [VALIDATION_FAILED_SUGGESTION],
                "evidence": [{"source": "validation", "excerpt": "stale"}],
                "confidence": 0.91,
            }
        )

    def fake_run(command, **_kwargs):
        nonlocal schema_payload
        command_seen.extend(command)
        schema_path = Path(command[command.index("--json-schema-file") + 1])
        schema_payload = read_json(schema_path)
        return Completed()

    setup_impl.resolve_model_and_runtime = fake_resolve
    setup_impl.subprocess.run = fake_run
    try:
        report = setup_local_ai.run_daily_text_model(
            tmp,
            "code-review",
            setup_local_ai.daily_task_prompt("code-review", [{"path": "<stdin>", "text": "failed", "truncated": False}]),
        )
    finally:
        setup_impl.resolve_model_and_runtime = original_resolve
        setup_impl.subprocess.run = original_run

    assert_has_all(command_seen, "--json-schema-file", "--no-jinja")
    assert_field(schema_payload, "type", "object")
    assert set(schema_payload["required"]) >= {"summary", "findings", "suggestions", "evidence", "confidence"}
    assert_field(schema_payload["properties"]["summary"], "maxLength", 240)
    assert_field(schema_payload["properties"]["evidence"], "maxItems", 3)
    assert_ok(report)
    assert_field(report, "confidence", 0.91)
    assert_field(report, "profile", TEXT_PROFILE)
    assert_field(report, "attempt_count", 1)


def test_daily_task_retries_low_confidence_then_accepts(tmp):
    write_config(tmp)
    calls = []
    payloads = [
        {
            "summary": "Weak confidence.",
            "findings": ["Maybe stale."],
            "suggestions": ["Retry."],
            "evidence": [{"source": "validation", "excerpt": "stale"}],
            "confidence": 0.12,
        },
        {
            "summary": "Accepted confidence.",
            "findings": ["Stale generated output."],
            "suggestions": ["Run sync."],
            "evidence": [{"source": "validation", "excerpt": "stale"}],
            "confidence": 0.91,
        },
    ]

    def fake_completion(root, *, task, profile, prompt, json_schema):
        assert root == tmp
        assert task == "validation-triage"
        assert profile == TEXT_PROFILE
        assert "validation failed" in prompt
        assert json_schema["type"] == "object"
        index = min(len(calls), len(payloads) - 1)
        calls.append(profile)
        return True, json.dumps(payloads[index]), {"limits": {"confidence_threshold": 0.7}}, []

    with patched_attrs(setup_impl, run_text_completion=fake_completion):
        report = setup_local_ai.run_daily_text_model(
            tmp,
            "validation-triage",
            setup_local_ai.daily_task_prompt("validation-triage", [{"path": "<stdin>", "text": "validation failed", "truncated": False}]),
        )

    assert_ok(report)
    assert calls == [TEXT_PROFILE, TEXT_PROFILE]
    assert_field(report, "summary", "Accepted confidence.")
    assert_field(report, "attempt_count", 2)
    assert_field(report, "handoff_required", False)
    assert report["attempts"][0]["accepted"] is False
    assert "below threshold" in report["attempts"][0]["issues"][0]
    assert report["attempts"][1]["accepted"] is True


def test_daily_task_hands_off_after_retries_exhausted(tmp):
    write_config(tmp)
    calls = []

    def fake_completion(_root, *, task, profile, prompt, json_schema):
        _ = task, prompt, json_schema
        calls.append(profile)
        return True, "plain summary only", {"limits": {"confidence_threshold": 0.7}}, []

    with patched_attrs(setup_impl, run_text_completion=fake_completion):
        report = setup_local_ai.run_daily_text_model(
            tmp,
            "validation-triage",
            setup_local_ai.daily_task_prompt("validation-triage", [{"path": "<stdin>", "text": "validation failed", "truncated": False}]),
        )

    assert_not_ok(report)
    assert calls == [TEXT_PROFILE, TEXT_PROFILE]
    assert_field(report, "handoff_required", True)
    assert_field(report, "fallback", "orchestrator-handoff")
    assert_field(report, "attempt_count", 2)
    assert all(attempt["accepted"] is False for attempt in report["attempts"])
    assert_contains(report["issues"], "local AI attempts exhausted")


def test_daily_task_does_not_retry_hard_validation_failure(tmp):
    write_config(tmp)
    calls = []

    def fake_completion(_root, *, task, profile, prompt, json_schema):
        _ = task, profile, prompt, json_schema
        calls.append(profile)
        return False, "", {"limits": {"confidence_threshold": 0.7}}, ["dotnet test failed against correct implementation"]

    with patched_attrs(setup_impl, run_text_completion=fake_completion):
        report = setup_local_ai.run_daily_text_model(
            tmp,
            "dotnet10-xunit-authoring",
            "Write xUnit tests.",
        )

    assert_not_ok(report)
    assert calls == [TEXT_PROFILE]
    assert_field(report, "handoff_required", True)
    assert_field(report, "attempt_count", 1)
    assert_field(report["attempts"][0], "failure_class", "test")
    assert_field(report["attempts"][0], "retryable", False)


def test_normalized_benchmark_repairs_required_file_path_when_enabled(tmp):
    benchmark = load_normalized_benchmark_module()
    bundle = {
        "files": [
            {"path": "Billing.Tests/InvoiceCalculatorTests.cs", "content": "public sealed class InvoiceCalculatorTests {}"},
            {"path": "Billing.Core/InvoiceCalculator.cs", "content": "production"},
        ]
    }

    strict = benchmark.safe_write_bundle(bundle, tmp / "strict", ["InvoiceCalculatorTests.cs"])
    repaired = benchmark.safe_write_bundle(
        bundle,
        tmp / "repaired",
        ["InvoiceCalculatorTests.cs"],
        allow_path_repair=True,
    )

    assert_field(strict, "missing", ["InvoiceCalculatorTests.cs"])
    assert_field(repaired, "missing", [])
    assert_field(repaired, "written", ["InvoiceCalculatorTests.cs"])
    assert_field(repaired["repaired_paths"][0], "from", "Billing.Tests/InvoiceCalculatorTests.cs")
    assert_contains(repaired["dropped_files"], "Billing.Core/InvoiceCalculator.cs")
    assert (tmp / "repaired" / "InvoiceCalculatorTests.cs").exists()


def test_normalized_benchmark_artifact_path_accepts_external_run_dir(tmp):
    benchmark = load_normalized_benchmark_module()
    external = (tmp / "outside" / "raw.txt").resolve()

    assert benchmark.artifact_path(external) == str(external)


def test_code_generation_candidate_manifest_references_pinned_runtime_catalog():
    repo_root = SCRIPT_DIR.parents[3]
    manifest_path = (
        repo_root
        / "automations"
        / "local-ai-benchmark-workflow"
        / "suites"
        / "code-generation-candidates-2026-06-11.json"
    )
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    allowed = {
        str(
            Path(".agents/local-ai/bundle/runtimes")
            / str(package["folder"])
            / executable
        ).replace("\\", "/")
        for package in [*setup_catalog.RUNTIME_PACKAGES, *setup_catalog.GPU_RUNTIME_PACKAGES]
        for executable in ("llama-cli.exe", "llama-server.exe")
    }
    unknown = []
    for row in data.get("models", []):
        for key in ("runtime_path", "server_runtime_path"):
            raw_path = row.get(key)
            if raw_path and not Path(str(raw_path)).is_absolute():
                normalized = str(raw_path).replace("\\", "/")
                if normalized not in allowed:
                    unknown.append(
                        {"profile": row.get("profile"), "key": key, "path": normalized}
                    )

    assert unknown == []


def test_code_generation_candidate_manifest_tracks_cascade_benchmark_candidate():
    repo_root = SCRIPT_DIR.parents[3]
    manifest_path = (
        repo_root
        / "automations"
        / "local-ai-benchmark-workflow"
        / "suites"
        / "code-generation-candidates-2026-06-11.json"
    )
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    models = {row.get("profile"): row for row in data.get("models", [])}
    excluded = {row.get("profile"): row for row in data.get("excluded_current_memory_policy", [])}

    cascade = models["nemotron-cascade2-30b-a3b-iq4-xs"]
    assert_field(cascade, "license", "NVIDIA Open Model License")
    assert_field(cascade, "expected_artifact_size_bytes", 18210351712)
    assert "license notice review" in cascade.get("candidate_reason", "")
    assert "same-suite output validation" in cascade.get("candidate_reason", "")
    assert_contains(excluded, "nemotron-cascade2-30b-a3b-high-memory-quants")
    super_row = excluded["nemotron3-super-120b-a12b-high-memory"]
    assert "64.5 GB" in super_row.get("reason", "")


def test_code_generation_candidate_manifest_tracks_stepfun_high_memory_evidence():
    repo_root = SCRIPT_DIR.parents[3]
    manifest_path = (
        repo_root
        / "automations"
        / "local-ai-benchmark-workflow"
        / "suites"
        / "code-generation-candidates-2026-06-11.json"
    )
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    excluded = {row.get("profile"): row for row in data.get("excluded_current_memory_policy", [])}

    step37 = excluded["step37-flash"]
    assert_field(step37, "license", "Apache-2.0")
    assert_field(step37, "expected_artifact_size_bytes", 95336010208)
    assert_field(step37, "expected_draft_artifact_size_bytes", 3707276416)
    assert_field(step37, "memory_policy_status", "outside-current-portable-20gb-cap")
    assert "RADV_PERFTEST=unified_heap" in step37.get("reason", "")
    assert "88.79 GiB" in step37.get("reason", "")


def test_code_generation_candidate_manifest_keeps_gemma_mtp_runner_blocked():
    repo_root = SCRIPT_DIR.parents[3]
    manifest_path = (
        repo_root
        / "automations"
        / "local-ai-benchmark-workflow"
        / "suites"
        / "code-generation-candidates-2026-06-11.json"
    )
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        "gemma4-12b-qat-mtp-q4-n2",
        "gemma4-12b-qat-mtp-q4-n4",
        "gemma4-12b-qat-mtp-q4-n4-reasoning-on",
        "gemma4-26b-a4b-qat-q4-0-mtp-q4-n2",
        "gemma4-26b-a4b-qat-mtp-q4-n2",
        "gemma4-26b-a4b-qat-mtp-q4-n3",
        "gemma4-31b-it-mtp-iq4-xs-q8-n2",
        "gemma4-31b-it-mtp-iq4-xs-q8-n4",
        "gemma4-31b-it-qat-q4-0-mtp-q4-n2",
    }
    runnable_profiles = {row.get("profile") for row in data.get("models", [])}
    blocked = {row.get("profile"): row for row in data.get("blocked_runner_support", [])}

    assert_contains(runnable_profiles, "gemma4-31b-it-qat-q4-0")
    assert expected.isdisjoint(runnable_profiles)
    assert expected.issubset(blocked)
    for profile in expected:
        row = blocked[profile]
        assert_field(row, "runner_kind", "upstream_llama_cpp_gemma4_mtp")
        assert_field(row, "requires_llama_cpp_min_build", "b9551")
        assert_field(row, "runtime_support_status", "upstream-release-available-runtime-override-required")
        assert_contains(row.get("known_good_builds", []), "b9553")
        assert_lacks(row.get("known_good_builds", []), "b9780")
        assert_contains(row.get("candidate_runtime_builds", []), "b9780")
        assert_contains(row.get("known_bad_builds", []), "b9702")
        assert_contains(row.get("known_bad_builds", []), "b9717")
        assert_contains(row.get("not_supported_by", []), "b9222")
    qat_row = blocked["gemma4-31b-it-qat-q4-0-mtp-q4-n2"]
    assert "Simplepotat/gemma-4-31b-it-qat-q4_0-assistant-gguf" in qat_row.get("draft_source_url", "")


def test_code_generation_candidate_manifest_tracks_blocked_custom_runtime_lanes():
    repo_root = SCRIPT_DIR.parents[3]
    manifest_path = (
        repo_root
        / "automations"
        / "local-ai-benchmark-workflow"
        / "suites"
        / "code-generation-candidates-2026-06-11.json"
    )
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    blocked = {row.get("profile"): row for row in data.get("blocked_runner_support", [])}
    excluded = {row.get("profile"): row for row in data.get("excluded_current_memory_policy", [])}

    twotower = blocked["nemotron-twotower-30b-a3b-base-bf16"]
    assert_field(twotower, "runner_kind", "twotower-diffusion-transformer")
    assert_field(twotower, "dedicated_runner_required", True)
    assert "adapter" in twotower.get("runtime_support_status", "")
    assert "not supported by llama.cpp" in twotower.get("reason", "")
    diffusion = blocked["diffusiongemma-26b-a4b-it-q4km"]
    assert_field(diffusion, "runtime_support_status", "upstream-pr-open-draft-unmerged")
    assert_field(diffusion, "dedicated_runner_required", True)
    assert "24423" in diffusion.get("requires_llama_cpp_pr", "")
    nano = blocked["qwen36-27b-nanoquant-gptq-custom"]
    assert_field(nano, "runner_kind", "nanoquant-custom-group-int")
    assert "not GGUF" in nano.get("reason", "")
    litert = blocked["litert-lm-gemma4-e4b-it-mtp-openai-chat"]
    assert_field(litert, "runner_kind", "litert-lm-openai-chat-server")
    assert_field(litert, "endpoint", "/v1/chat/completions")
    assert "adapter" in litert.get("runtime_support_status", "")
    litert_12b = blocked["litert-lm-gemma4-12b-it-openai-chat"]
    assert "mtp-image-pending" in litert_12b.get("runtime_support_status", "")
    devnen = blocked["devnen-qwen36-27b-autoround-vllm-windows-ampere"]
    assert_field(devnen, "runner_kind", "patched-vllm-windows-openai-chat-server")
    assert_field(devnen, "api_endpoint", "/v1/chat/completions")
    assert "adapter-required" in devnen.get("runtime_support_status", "")
    devnen_blackwell = blocked["devnen-qwen36-27b-nvfp4-vllm-windows-blackwell"]
    assert "20 GB" in devnen_blackwell.get("memory_policy_status", "")
    assert_contains(excluded, "qwen36-35b-a3b-high-memory-q5-q6-q8")
    assert_contains(excluded, "gemma4-31b-it-high-memory-q5-q6-q8")
    assert_contains(excluded, "qwen35-27b-high-memory-q8")
    models = {row.get("profile"): row for row in data.get("models", [])}
    qwen_mtp_27b = models["qwen36-27b-mtp-iq4-xs-n2"]
    assert "external_evidence_id" not in qwen_mtp_27b
    qwen_mtp_35b = models["qwen36-35b-a3b-mtp-iq4-xs-q8nextn-n2"]
    assert "external_long_context_evidence_id" not in qwen_mtp_35b
    serialized_manifest = json.dumps(data).lower()
    assert ("str" + "ix") not in serialized_manifest
    assert ("ha" + "lo") not in serialized_manifest
    tmax = models["tmax27b-imatrix-iq4-xs"]
    assert_field(tmax, "license", "Apache-2.0")
    assert_field(tmax, "external_evidence_id", "tmax27b-terminal-bench-ai2-2026-06-22")
    tmax_mtp = models["tmax27b-imatrix-mtp-iq4-xs-n1"]
    assert_field(tmax_mtp, "spec_type", "draft-mtp")
    assert_field(tmax_mtp, "spec_draft_n_max", 1)
    assert "agentic smoke evidence only" in tmax_mtp.get("external_evidence_note", "")
    qwopus_v2 = models["qwopus36-27b-v2-mtp-iq4-xs-n4-ngram-map-k-small"]
    assert "IQ4_XS" in qwopus_v2.get("model_path", "")
    assert_field(qwopus_v2, "spec_draft_n_max", 4)
    assert_field(qwopus_v2, "spec_draft_p_split", 0.45)
    assert not any("qwopus36-27b-v2" in profile and "q8" in profile.lower() for profile in models)


def test_normalized_benchmark_parses_linux_proc_memory_status():
    benchmark = load_normalized_benchmark_module()
    status_text = """
Name:\tllama-cli
VmRSS:\t  102400 kB
VmHWM:\t  204800 kB
"""

    assert_field({"peak": benchmark.parse_proc_status_memory_mib(status_text)}, "peak", 200.0)
    assert benchmark.parse_proc_status_memory_mib("Name:\tllama-cli\n") is None


def test_normalized_benchmark_report_includes_host_memory_topology(tmp):
    benchmark = load_normalized_benchmark_module()
    models_file = tmp / "models.json"
    run_dir = tmp / "run"
    task_id = "python-" + "to" + "do-summary"
    models_file.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "profile": "missing-default",
                        "model_path": str(tmp / "missing.gguf"),
                        "runtime_path": str(tmp / "missing-runtime.exe"),
                        "source_url": "https://example.invalid/missing.gguf",
                        "license": "Apache-2.0",
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    argv = [
        "normalized_code_generation_benchmark.py",
        "--run-dir",
        str(run_dir),
        "--models-file",
        str(models_file),
        "--profiles",
        "missing-default",
        "--tasks",
        task_id,
        "--current-default-profile",
        "missing-default",
        "--memory-limit-mib",
        "20480",
    ]
    with patched_attrs(
        benchmark,
        detect_host_memory_topology=lambda: {"platform": {"system": "TestOS"}, "env": {}, "notes": []},
    ), patched_attrs(sys, argv=argv):
        assert benchmark.main() == 0

    report = json.loads((run_dir / "code-generation-output-testable.json").read_text(encoding="utf-8"))
    assert_fields(report["host_memory_topology"], env={}, notes=[])
    assert_field(report["host_memory_topology"]["platform"], "system", "TestOS")
    assert_field(report["rows"][0], "status", "missing-dependency")


def test_normalized_benchmark_blocks_invalid_builtin_mtp_before_runtime(tmp):
    benchmark = load_normalized_benchmark_module()
    models_file = tmp / "models.json"
    run_dir = tmp / "run"
    model_path = tmp / "invalid-mtp.gguf"
    runtime_path = tmp / "llama-cli.exe"
    task_id = "python-" + "to" + "do-summary"
    write_synthetic_gguf(
        model_path,
        {
            "general.architecture": "qwen35",
            "qwen35.block_count": 65,
            "qwen35.nextn_predict_layers": 1,
        },
        ["blk.0.attn_q.weight"],
    )
    runtime_path.write_bytes(b"not an executable")
    models_file.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "profile": "bad-mtp",
                        "model_path": str(model_path),
                        "runtime_path": str(runtime_path),
                        "source_url": "https://example.invalid/bad-mtp.gguf",
                        "license": "Apache-2.0",
                        "spec_type": "draft-mtp",
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    argv = [
        "normalized_code_generation_benchmark.py",
        "--run-dir",
        str(run_dir),
        "--models-file",
        str(models_file),
        "--profiles",
        "bad-mtp",
        "--tasks",
        task_id,
        "--current-default-profile",
        "bad-mtp",
    ]
    with patched_attrs(
        benchmark,
        detect_host_memory_topology=lambda: {"platform": {"system": "TestOS"}, "env": {}, "notes": []},
    ), patched_attrs(sys, argv=argv):
        assert benchmark.main() == 0

    report = json.loads((run_dir / "code-generation-output-testable.json").read_text(encoding="utf-8"))
    row = report["rows"][0]
    assert_field(row, "status", "blocked-preflight")
    assert_field(row, "failure_class", "gguf-mtp-metadata-tensor-mismatch")
    assert_field(row["mtp_gguf_preflight"], "nextn_tensor_count", 0)
    assert_contains(row["issues"], "GGUF metadata claims nextn layers")


def test_normalized_benchmark_server_mode_forwards_parallel_slots(tmp):
    benchmark = load_normalized_benchmark_module()
    captured = {}

    class Completed:
        returncode = 0

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Completed()

    argv = [
        "normalized_code_generation_benchmark.py",
        "--execution-mode",
        "server",
        "--run-dir",
        str(tmp / "run"),
        "--profiles",
        "qwen36-27b-mtp-iq4-xs-n2",
        "--parallel-slots",
        "8",
    ]

    with patched_attrs(benchmark.subprocess, run=fake_run), patched_attrs(sys, argv=argv):
        assert benchmark.main() == 0

    command = captured["command"]
    assert "--parallel-slots" in command
    assert command[command.index("--parallel-slots") + 1] == "8"
    assert_field(captured["kwargs"], "cwd", benchmark.ROOT)


def test_nemotron_xunit_benchmark_artifact_path_accepts_external_run_dir(tmp):
    benchmark = load_nemotron_xunit_benchmark_module()
    external = (tmp / "outside" / "raw.txt").resolve()

    assert benchmark.artifact_path(external) == str(external)


def test_normalized_benchmark_default_promotion_reason_is_explicit():
    benchmark = load_normalized_benchmark_module()
    row = {
        "profile": "nemotron3-nano4b",
        "license": "nvidia open model license",
        "accepted": 1,
        "total": 1,
        "metrics_standard": {"peak_memory_mib_max": 1024, "e2e_latency_ms": 1000},
    }

    gate = benchmark.promotion_gate_for_row(row, default_row=row, memory_limit_mib=20480)

    assert_field(gate, "status", "not-promotable")
    assert_field(gate, "reason", "current default baseline row is not a promotion candidate")
    assert all(gate["checks"].values())


def test_normalized_benchmark_promotion_gate_handles_missing_default_row():
    benchmark = load_normalized_benchmark_module()
    row = {
        "profile": "qwen36-27b-mtp-iq4-xs-n2",
        "license": "apache-2.0",
        "accepted": 0,
        "total": 4,
        "metrics_standard": {},
    }

    gate = benchmark.promotion_gate_for_row(row, default_row=None, memory_limit_mib=20480)

    assert_field(gate, "status", "not-promotable")
    assert_field(gate, "reason", "current default baseline row was not selected")
    assert_field(gate["checks"], "must_beat_current_default", False)


def test_normalized_benchmark_promotion_gate_requires_peak_memory_evidence():
    benchmark = load_normalized_benchmark_module()
    default_row = {
        "profile": "nemotron3-nano4b",
        "license": "nvidia open model license",
        "accepted": 4,
        "total": 4,
        "metrics_standard": {"peak_memory_mib_max": 1024, "e2e_latency_ms": 1000},
    }
    candidate = {
        "profile": "qwen36-27b-mtp-iq4-xs-n2",
        "license": "apache-2.0",
        "accepted": 4,
        "total": 4,
        "metrics_standard": {"peak_memory_mib_max": None, "e2e_latency_ms": 500},
    }

    gate = benchmark.promotion_gate_for_row(candidate, default_row=default_row, memory_limit_mib=20480)
    uncapped_gate = benchmark.promotion_gate_for_row(candidate, default_row=default_row, memory_limit_mib=0)

    assert_field(gate, "status", "not-promotable")
    assert_field(gate, "memory_evidence", "missing")
    assert_field(gate["checks"], "must_fit_memory", False)
    assert_field(gate["checks"], "must_pass_suite", True)
    assert_field(gate["checks"], "must_beat_current_default", True)
    assert_field(uncapped_gate, "status", "promotable")
    assert_field(uncapped_gate, "memory_evidence", "not-required")
    assert_field(uncapped_gate["checks"], "must_fit_memory", True)


def test_normalized_server_benchmark_promotion_gate_requires_peak_memory_evidence():
    codegen = load_normalized_benchmark_module()
    server = load_normalized_server_benchmark_module()
    metrics = server.aggregate(
        [
            {
                "accepted": True,
                "elapsed_seconds": 0.5,
                "server_timings": {"predicted_per_second": 12.0},
            }
        ]
    )
    row = {
        "profile": "qwen36-27b-mtp-iq4-xs-n2",
        "license": "apache-2.0",
        "accepted": 1,
        "total": 1,
        "metrics_standard": metrics,
    }
    default_row = {
        "profile": "nemotron3-nano4b",
        "license": "nvidia open model license",
        "accepted": 1,
        "total": 1,
        "metrics_standard": {"peak_memory_mib_max": 1024, "e2e_latency_ms": 1000},
    }

    gate = codegen.promotion_gate_for_row(row, default_row=default_row, memory_limit_mib=20480)

    assert_field(metrics, "peak_memory_mib_max", None)
    assert_field(gate, "status", "not-promotable")
    assert_field(gate, "memory_evidence", "missing")
    assert_field(gate["checks"], "must_fit_memory", False)


def test_normalized_server_benchmark_report_includes_host_memory_topology(tmp):
    benchmark = load_normalized_server_benchmark_module()
    models_file = tmp / "models.json"
    run_dir = tmp / "server-run"
    task_id = "python-" + "to" + "do-summary"
    models_file.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "profile": "missing-server",
                        "model_path": str(tmp / "missing.gguf"),
                        "runtime_path": str(tmp / "runtime" / "llama-cli.exe"),
                        "source_url": "https://example.invalid/missing.gguf",
                        "license": "Apache-2.0",
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    argv = [
        "normalized_server_code_generation_benchmark.py",
        "--run-dir",
        str(run_dir),
        "--models-file",
        str(models_file),
        "--profiles",
        "missing-server",
        "--tasks",
        task_id,
        "--current-default-profile",
        "missing-server",
        "--memory-limit-mib",
        "20480",
    ]
    with patched_attrs(
        benchmark.codegen,
        detect_host_memory_topology=lambda: {"platform": {"system": "ServerTestOS"}, "env": {}, "notes": []},
    ), patched_attrs(sys, argv=argv):
        assert benchmark.main() == 0

    report = json.loads((run_dir / "server-code-generation-output-testable.json").read_text(encoding="utf-8"))
    assert_field(report["host_memory_topology"]["platform"], "system", "ServerTestOS")
    assert_field(report["rows"][0], "status", "missing-dependency")


def test_normalized_benchmark_records_mtp_spec_without_draft_model():
    benchmark = load_normalized_benchmark_module()
    model = {"spec_type": "draft-mtp", "spec_draft_n_max": 3, "spec_draft_p_min": 0.75}

    fields = benchmark.benchmark_spec_fields(model)

    assert_field(fields, "spec_type", "draft-mtp")
    assert_field(fields, "spec_draft_n_max", 3)
    assert_field(fields, "spec_draft_p_min", 0.75)


def test_gguf_mtp_preflight_skips_non_builtin_mtp_rows(tmp):
    benchmark = load_normalized_benchmark_module()
    missing_model = tmp / "missing.gguf"

    non_mtp = benchmark.mtp_gguf_preflight({"model_path": missing_model, "spec_type": "ngram-mod"})
    separate_draft = benchmark.mtp_gguf_preflight(
        {"model_path": missing_model, "spec_type": "draft-mtp", "draft_model_path": tmp / "draft.gguf"}
    )

    assert_field(non_mtp, "status", "skipped")
    assert_field(separate_draft, "status", "skipped")


def test_gguf_mtp_preflight_passes_valid_builtin_nextn(tmp):
    benchmark = load_normalized_benchmark_module()
    model_path = tmp / "valid-mtp.gguf"
    write_synthetic_gguf(
        model_path,
        {
            "general.architecture": "qwen35",
            "qwen35.block_count": 65,
            "qwen35.nextn_predict_layers": 1,
        },
        [
            "blk.0.attn_q.weight",
            "blk.64.nextn.eh_proj.weight",
            "blk.64.nextn.enorm.weight",
            "blk.64.nextn.hnorm.weight",
        ],
    )

    result = benchmark.mtp_gguf_preflight({"model_path": model_path, "spec_type": "draft-mtp"})

    assert_field(result, "status", "passed")
    assert_field(result, "nextn_predict_layers", 1)
    assert_field(result, "first_mtp_block", 64)
    assert_field(result, "nextn_tensor_count", 3)
    assert_empty(result["missing_common_nextn_tensors"])


def test_gguf_mtp_preflight_blocks_missing_nextn_metadata(tmp):
    benchmark = load_normalized_benchmark_module()
    model_path = tmp / "missing-nextn-metadata.gguf"
    write_synthetic_gguf(
        model_path,
        {"general.architecture": "qwen35", "qwen35.block_count": 64},
        ["blk.0.attn_q.weight"],
    )

    result = benchmark.mtp_gguf_preflight({"model_path": model_path, "spec_type": "draft-mtp"})

    assert_not_ok(result)
    assert_field(result, "status", "failed")
    assert_field(result, "issue_class", "gguf-mtp-metadata-missing")
    assert_field({"failure_class": benchmark.failure_class(result["issue_class"])}, "failure_class", "preflight")


def test_gguf_mtp_preflight_blocks_missing_nextn_tensors(tmp):
    benchmark = load_normalized_benchmark_module()
    model_path = tmp / "missing-nextn-tensors.gguf"
    write_synthetic_gguf(
        model_path,
        {
            "general.architecture": "qwen35",
            "qwen35.block_count": 65,
            "qwen35.nextn_predict_layers": 1,
        },
        ["blk.0.attn_q.weight"],
    )

    result = benchmark.mtp_gguf_preflight({"model_path": model_path, "spec_type": "draft-mtp"})

    assert_not_ok(result)
    assert_field(result, "status", "failed")
    assert_field(result, "issue_class", "gguf-mtp-metadata-tensor-mismatch")
    assert_field(result, "nextn_tensor_count", 0)


def test_normalized_benchmark_records_runtime_metadata_fields():
    benchmark = load_normalized_benchmark_module()
    model = {
        "runtime_lane": "llama.cpp-b9777-vulkan",
        "runtime_family": "llama.cpp",
        "runtime_build_tag": "b9777",
        "runtime_commit": "examplecommit",
        "runtime_source_url": "https://github.com/ggml-org/llama.cpp/releases/tag/b9777",
        "runtime_package_url": "https://example.invalid/llama-b9777.zip",
        "runtime_asset_name": "llama-b9777-bin-win-vulkan-x64.zip",
        "runtime_release_published_at": "2026-06-24T10:18:40Z",
        "runtime_binary_sha256": "binaryhash",
        "runtime_help_sha256": "helphash",
        "runtime_tool": "llama-server",
        "runtime_branch_or_fork": "upstream",
        "runtime_variant": "fork-patch",
        "runtime_base_commit": "1191758",
        "runtime_patch_commit": "c6d64e6",
        "runtime_patch_source_url": "https://github.com/accaldwell/llama.cpp/commit/c6d64e6",
        "runtime_patch_upstream_status": "discussion-only-not-upstream",
        "runtime_license_notices": ["MIT", "third-party-notice"],
        "runtime_support_status": "upstream-release-available-runtime-override-required",
        "requested_backend": "vulkan",
        "effective_backend": "vulkan",
        "backend_driver_stack": "RADV",
        "kernel_version": "7.0.0-15-generic",
        "firmware_version_or_date": "2026-05-31",
        "bios_version": "1.0.12",
        "uma_vgm_gb": 96,
        "gtt_limit_mib": 65536,
        "ttm_pages_limit": "default",
        "iommu_state": "disabled",
        "rocm_version": "7.2.2",
        "mesa_version": "26.0.3",
        "vulkan_icd": "RADV",
        "external_evidence_id": "qwen3-coder-30b-q4ks-speed-first",
        "external_evidence_url": "https://example.invalid/external-hardware-guide",
        "external_runtime_build": "llama.cpp b9592 / ac4cddeb0",
        "model_sha256": "modelhash",
        "draft_model_sha256": "drafthash",
        "command_argv_sha256": "commandhash",
        "device_list": [{"backend": "vulkan", "name": "AMD Radeon"}],
        "spec_combination_semantics": "independent-strategies",
        "mmap_policy": "no-mmap",
        "mlock_policy": "disabled",
        "direct_io_policy": "enabled",
        "power_measurement_source": "external-meter",
        "reproducibility_id": "external-server-shootout-2026-05-05",
        "raw_evidence_url": "https://raw.githubusercontent.com/example/evidence.csv",
        "benchmark_command": "llama-bench -m model.gguf -p 512 -n 128",
        "container_image": "example.invalid/rocm:7.2",
        "container_image_ref": "example.invalid/rocm:7.2",
        "container_image_digest": "sha256:example",
        "environment_overrides": {"HSA_OVERRIDE_GFX_VERSION": "11.5.1"},
        "driver_config_overrides": {"RADV_PERFTEST": "nogttspill"},
        "mesa_drirc_overrides": {"radv_enable_unified_heap_on_apu": True},
        "backend_kernel_family": "vulkan-khr-coopmat-mul-mat-id",
        "backend_kernel_feature": "KHR_coopmat",
        "kernel_tuning_id": "amd-moe-bm128-bn32",
        "kernel_tuning_params": "BM=128;BN=32",
        "driver_id": "RADV-gfx1151",
        "portability_class": "custom-fork-nonportable",
        "admin_requirements": ["custom source build"],
        "no_admin_setup_proof": "missing",
        "control_runtime_lane": "llama_cpp_b9780_ubuntu_vulkan_x64",
        "power_idle_w": 33.0,
        "power_peak_pp_w": 251.0,
        "power_sustained_tg_w": 150.0,
        "tokens_per_joule": 0.63,
        "joules_per_token": 1.59,
        "power_validation_status": "community-reported",
    }

    fields = benchmark.benchmark_spec_fields(model)

    assert_field(fields, "runtime_lane", "llama.cpp-b9777-vulkan")
    assert_field(fields, "runtime_build_tag", "b9777")
    assert_field(fields, "runtime_source_url", "https://github.com/ggml-org/llama.cpp/releases/tag/b9777")
    assert_field(fields, "runtime_asset_name", "llama-b9777-bin-win-vulkan-x64.zip")
    assert_field(fields, "runtime_binary_sha256", "binaryhash")
    assert_field(fields, "runtime_tool", "llama-server")
    assert_field(fields, "runtime_variant", "fork-patch")
    assert_field(fields, "runtime_patch_commit", "c6d64e6")
    assert_field(fields, "runtime_patch_upstream_status", "discussion-only-not-upstream")
    assert_field(fields, "runtime_support_status", "upstream-release-available-runtime-override-required")
    assert_field(fields, "runtime_license_notices", ["MIT", "third-party-notice"])
    assert_field(fields, "requested_backend", "vulkan")
    assert_field(fields, "backend_driver_stack", "RADV")
    assert_field(fields, "firmware_version_or_date", "2026-05-31")
    assert_field(fields, "bios_version", "1.0.12")
    assert_field(fields, "uma_vgm_gb", 96)
    assert_field(fields, "gtt_limit_mib", 65536)
    assert_field(fields, "ttm_pages_limit", "default")
    assert_field(fields, "iommu_state", "disabled")
    assert_field(fields, "mesa_version", "26.0.3")
    assert_field(fields, "external_evidence_id", "qwen3-coder-30b-q4ks-speed-first")
    assert_field(fields, "external_evidence_url", "https://example.invalid/external-hardware-guide")
    assert_field(fields, "external_runtime_build", "llama.cpp b9592 / ac4cddeb0")
    assert_field(fields, "model_sha256", "modelhash")
    assert_field(fields, "command_argv_sha256", "commandhash")
    assert_field(fields, "spec_combination_semantics", "independent-strategies")
    assert_field(fields, "mlock_policy", "disabled")
    assert_field(fields, "direct_io_policy", "enabled")
    assert_field(fields, "power_measurement_source", "external-meter")
    assert_field(fields, "reproducibility_id", "external-server-shootout-2026-05-05")
    assert_field(fields, "environment_overrides", {"HSA_OVERRIDE_GFX_VERSION": "11.5.1"})
    assert_field(fields, "driver_config_overrides", {"RADV_PERFTEST": "nogttspill"})
    assert_field(fields, "mesa_drirc_overrides", {"radv_enable_unified_heap_on_apu": True})
    assert_field(fields, "backend_kernel_feature", "KHR_coopmat")
    assert_field(fields, "kernel_tuning_params", "BM=128;BN=32")
    assert_field(fields, "portability_class", "custom-fork-nonportable")
    assert_field(fields, "no_admin_setup_proof", "missing")
    assert_field(fields, "control_runtime_lane", "llama_cpp_b9780_ubuntu_vulkan_x64")
    assert_field(fields, "power_idle_w", 33.0)
    assert_field(fields, "tokens_per_joule", 0.63)
    assert_field(fields, "power_validation_status", "community-reported")


def test_normalized_benchmark_infers_installed_runtime_lane():
    benchmark = load_normalized_benchmark_module()
    model = {
        "runtime_path": ".agents/local-ai/bundle/runtimes/llama-b9222-win-cpu-x64/llama-cli.exe",
        "spec_type": "draft-mtp,ngram-mod",
    }

    fields = benchmark.benchmark_spec_fields(model)

    assert_field(fields, "runtime_lane", "llama_cpp_b9222_win_cpu_x64_installed")
    assert_field(fields, "runtime_build_tag", "b9222")
    assert_field(fields, "runtime_tool", "llama-cli")
    assert_field(fields, "runtime_binary_sha256", "73c2c58899170735e78eac4ef054fbe4de0128e28bfcfcb77c513fc4551e9cb9")
    assert_field(fields, "requested_backend", "cpu")
    assert_field(fields, "spec_combination_semantics", "independent-strategies")


def test_normalized_benchmark_records_zero_and_ngram_spec_fields():
    benchmark = load_normalized_benchmark_module()
    model = {
        "draft_model_path": "draft.gguf",
        "draft_source_url": "https://example.invalid/draft.gguf",
        "spec_type": "draft-mtp,ngram-mod",
        "spec_draft_n_max": 2,
        "spec_draft_n_min": 0,
        "spec_draft_p_split": 0.45,
        "spec_draft_type_k": "q4_0",
        "spec_draft_type_v": "q8_0",
        "spec_ngram_mod_n_match": 24,
        "spec_ngram_mod_n_min": 48,
        "spec_ngram_mod_n_max": 64,
    }

    fields = benchmark.benchmark_spec_fields(model)

    assert_field(fields, "draft_model_path", "draft.gguf")
    assert_field(fields, "draft_source_url", "https://example.invalid/draft.gguf")
    assert_field(fields, "spec_type", "draft-mtp,ngram-mod")
    assert_field(fields, "spec_draft_n_max", 2)
    assert_field(fields, "spec_draft_n_min", 0)
    assert_field(fields, "spec_draft_p_split", 0.45)
    assert_field(fields, "spec_draft_type_k", "q4_0")
    assert_field(fields, "spec_draft_type_v", "q8_0")
    assert_field(fields, "spec_ngram_mod_n_match", 24)
    assert_field(fields, "spec_ngram_mod_n_min", 48)
    assert_field(fields, "spec_ngram_mod_n_max", 64)


def test_normalized_benchmark_command_passes_ngram_spec_flags(tmp):
    benchmark = load_normalized_benchmark_module()
    prompt_path = tmp / "prompt.txt"
    schema_path = tmp / "schema.json"
    model = {
        "model_path": tmp / "model.gguf",
        "runtime_path": tmp / "llama-cli.exe",
        "spec_type": "ngram-map-k",
        "spec_draft_p_min": 0.75,
        "spec_draft_p_split": 0.45,
        "spec_draft_type_k": "q4_0",
        "spec_draft_type_v": "q8_0",
        "spec_ngram_map_k_size_n": 12,
        "spec_ngram_map_k_size_m": 48,
        "spec_ngram_map_k_min_hits": 1,
    }

    command = benchmark.command_for(model, prompt_path, schema_path)

    assert "--spec-type" in command
    assert command[command.index("--spec-type") + 1] == "ngram-map-k"
    assert "--spec-draft-p-min" in command
    assert command[command.index("--spec-draft-p-min") + 1] == "0.75"
    assert "--spec-draft-p-split" in command
    assert command[command.index("--spec-draft-p-split") + 1] == "0.45"
    assert "--spec-draft-type-k" in command
    assert command[command.index("--spec-draft-type-k") + 1] == "q4_0"
    assert "--spec-draft-type-v" in command
    assert command[command.index("--spec-draft-type-v") + 1] == "q8_0"
    assert "--spec-ngram-map-k-size-n" in command
    assert command[command.index("--spec-ngram-map-k-size-n") + 1] == "12"
    assert "--spec-ngram-map-k-size-m" in command
    assert command[command.index("--spec-ngram-map-k-size-m") + 1] == "48"
    assert "--spec-ngram-map-k-min-hits" in command
    assert command[command.index("--spec-ngram-map-k-min-hits") + 1] == "1"


def test_normalized_server_benchmark_command_passes_spec_flags(tmp):
    benchmark = load_normalized_server_benchmark_module()
    runtime_path = tmp / "runtime" / "llama-cli.exe"
    runtime_path.parent.mkdir()
    schema_path = tmp / "schema.json"
    model = {
        "profile": "qwen36-27b-mtp-iq4-xs-n2-ngram-mod",
        "model_path": tmp / "model.gguf",
        "runtime_path": runtime_path,
        "spec_type": "draft-mtp,ngram-mod",
        "spec_draft_n_max": 2,
        "spec_draft_p_min": 0.75,
        "spec_draft_p_split": 0.45,
        "spec_draft_type_k": "q4_0",
        "spec_draft_type_v": "q8_0",
        "spec_ngram_mod_n_match": 24,
        "spec_ngram_mod_n_min": 48,
        "spec_ngram_mod_n_max": 64,
    }

    command = benchmark.server_command_for(model, port=49152, schema_path=schema_path, parallel_slots=8)

    assert command[0].endswith("llama-server.exe")
    assert "--host" in command
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert "--port" in command
    assert command[command.index("--port") + 1] == "49152"
    assert "-np" in command
    assert command[command.index("-np") + 1] == "8"
    assert "--cache-prompt" in command
    assert "--metrics" in command
    assert "--slots" in command
    assert "--spec-type" in command
    assert command[command.index("--spec-type") + 1] == "draft-mtp,ngram-mod"
    assert "--spec-draft-n-max" in command
    assert command[command.index("--spec-draft-n-max") + 1] == "2"
    assert "--spec-draft-p-min" in command
    assert command[command.index("--spec-draft-p-min") + 1] == "0.75"
    assert "--spec-draft-p-split" in command
    assert command[command.index("--spec-draft-p-split") + 1] == "0.45"
    assert "--spec-draft-type-k" in command
    assert command[command.index("--spec-draft-type-k") + 1] == "q4_0"
    assert "--spec-draft-type-v" in command
    assert command[command.index("--spec-draft-type-v") + 1] == "q8_0"
    assert "--spec-ngram-mod-n-match" in command
    assert command[command.index("--spec-ngram-mod-n-match") + 1] == "24"
    assert "--spec-ngram-mod-n-min" in command
    assert command[command.index("--spec-ngram-mod-n-min") + 1] == "48"
    assert "--spec-ngram-mod-n-max" in command
    assert command[command.index("--spec-ngram-mod-n-max") + 1] == "64"


def test_normalized_server_benchmark_parses_completion_shapes():
    benchmark = load_normalized_server_benchmark_module()

    text_completion = benchmark.extract_completion_text({"choices": [{"text": "{\"files\": []}"}]})
    chat_completion = benchmark.extract_completion_text(
        {"choices": [{"message": {"content": "{\"files\": []}"}}]}
    )
    native_completion = benchmark.extract_completion_text({"content": "{\"files\": []}"})

    assert text_completion == "{\"files\": []}"
    assert chat_completion == "{\"files\": []}"
    assert native_completion == "{\"files\": []}"


def test_normalized_server_benchmark_parses_metrics_and_missing_spec_counters():
    benchmark = load_normalized_server_benchmark_module()
    raw = """
# HELP llama_spec_draft_tokens_total draft tokens
llama_spec_draft_tokens_total{model_name="wanted"} 10
llama_spec_accepted_tokens_total{model_name="wanted"} 7
llama_spec_draft_tokens_total{model_name="other"} 99
llama_decode_seconds_sum 3.5
"""

    metrics = benchmark.parse_prometheus_metrics(raw, profile="wanted")
    spec_metrics = benchmark.spec_metrics_from_metrics(metrics)
    unavailable = benchmark.spec_metrics_from_metrics({"llama_decode_seconds_sum": 3.5})

    assert_field(metrics, 'llama_spec_draft_tokens_total{model_name="wanted"}', 10.0)
    assert 'llama_spec_draft_tokens_total{model_name="other"}' not in metrics
    assert_field(spec_metrics, "source", "metrics")
    assert_field(spec_metrics, "spec_draft_tokens", 10.0)
    assert_field(spec_metrics, "spec_accepted_tokens", 7.0)
    assert_field(spec_metrics, "spec_acceptance_rate", 0.7)
    assert_field(unavailable, "source", "unavailable")


def test_daily_task_uses_configured_profile_order(tmp):
    write_config(tmp)
    config = read_local_ai_config(tmp)
    config["task_model_profiles"]["validation-triage"] = ["qwen3-embedding-0.6b-q8", TEXT_PROFILE]
    write_local_ai_config(tmp, config)
    calls = []

    def fake_completion(_root, *, task, profile, prompt, json_schema):
        _ = task, prompt, json_schema
        calls.append(profile)
        return (
            True,
            json.dumps(
                {
                    "summary": "Optional route accepted.",
                    "findings": ["Profile order honored."],
                    "suggestions": [],
                    "evidence": [{"source": "unit", "excerpt": "profile order"}],
                    "confidence": 0.9,
                }
            ),
            {"limits": {"confidence_threshold": 0.7}},
            [],
        )

    with patched_attrs(setup_impl, run_text_completion=fake_completion):
        report = setup_local_ai.run_daily_text_model(
            tmp,
            "validation-triage",
            setup_local_ai.daily_task_prompt("validation-triage", [{"path": "<stdin>", "text": "validation failed", "truncated": False}]),
        )

    assert_ok(report)
    assert calls == ["qwen3-embedding-0.6b-q8"]
    assert_field(report, "profile", "qwen3-embedding-0.6b-q8")
    assert_field(report, "profile_order", ["qwen3-embedding-0.6b-q8", TEXT_PROFILE])


def test_schema_valid_model_output_preserves_confidence():
    report = setup_local_ai.report_from_model_output(
        json.dumps(
            {
                "summary": "Issue.",
                "findings": ["Finding."],
                "suggestions": ["Suggestion."],
                "evidence": [{"source": "unit", "excerpt": "evidence"}],
                "confidence": 0.82,
            }
        ),
        task="validation-triage",
    )
    assert_ok(report)
    assert_field(report, "confidence", 0.82)


def test_plain_model_output_falls_back_with_issue():
    report = setup_local_ai.report_from_model_output("plain summary only", task="validation-triage")
    assert_ok(report)
    assert_field(report, "summary", "plain summary only")
    assert "plain text" in report["issues"][0]


def test_daily_task_runtime_noise_is_not_accepted_as_summary():
    output = "\n".join(
        [
            "Loading model...",
            "â–„â–„ â–„â–„",
            "build      : b9222",
            "model      : NVIDIA-Nemotron3-Nano-4B-Q4_K_M.gguf",
            "available commands: /exit or Ctrl+C stop or exit",
            "> You are a repo-local assistant.",
        ]
    )
    report = setup_local_ai.report_from_model_output(output, task="changed-files-summary")
    assert_not_ok(report)
    assert_field(report, "summary", "Local AI returned no usable task output.")
    assert "runtime noise" in report["issues"][0]


def test_daily_task_rejects_paths_outside_repo(tmp):
    outside = tmp.parent / "outside-local-ai-test.txt"
    outside.write_text("outside", encoding="utf-8", newline="\n")
    try:
        try:
            setup_local_ai.daily_task_report(
                tmp,
                task="validation-triage",
                inputs=[str(outside)],
                stdin_text="",
            )
        except RuntimeError as exc:
            assert "escapes repository root" in str(exc)
        else:
            raise AssertionError("daily_task_report accepted a path outside the repo")
    finally:
        outside.unlink(missing_ok=True)


def test_model_inventory_and_detached_bench_command_are_report_only(tmp):
    write_config(tmp)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = setup_impl.print_model_inventory(tmp, include_disk=True, as_json=True)
    payload = json.loads(output.getvalue())
    assert status in {0, 1}
    assert_fields(payload, tool="local-ai-helper.models-inventory")
    detached = setup_impl.detached_benchmark_command(tmp, [TEXT_PROFILE], 2, True, "smoke")
    assert_fields(detached, gpu="disabled")
    assert_has_all(detached["command"], "--standard-metrics")
    summary = setup_impl.model_inventory_summary(payload, compact=True)
    assert_keys_lack(summary, "models", "active_profiles", "installed_profiles", "missing_profiles")
    assert summary["model_count"] >= 1
    assert_fields(summary, tool="local-ai-helper.models-inventory")


def test_catalog_reports_profile_install_and_manifest_states(tmp):
    write_config(tmp)
    write_text_bundle(tmp)

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = setup_impl.print_catalog(tmp, as_json=True)
    payload = json.loads(output.getvalue())
    rows = {item["profile"]: item for item in payload["models"]}

    assert status == 0
    assert_fields(payload["profile_checks"], alias_unique=True, cpu_default=True, new_dependency_count=0)
    assert_fields(rows[TEXT_PROFILE], install_state="installed", manifest_state="hash-valid")
    assert_fields(rows[EMBEDDING_PROFILE], install_state="missing", manifest_state="not-in-manifest")
    assert_fields(rows[VISION_PROFILE], direct_download=True)
    summary = setup_impl.catalog_summary(payload, compact=True)
    assert_keys_lack(summary, "models", "profile_checks", "installed_profiles")
    assert summary["missing_count"] >= 1
    assert_fields(summary, installed_count=1, policy="direct-local")
    assert "manifest_mismatch_count" in summary


def test_catalog_reports_model_size_mismatch_when_manifest_declares_expected_size(tmp):
    write_config(tmp)
    write_text_bundle(tmp)
    manifest_path = tmp / ".agents" / "local-ai" / "bundle" / "manifest.json"
    manifest = read_json(manifest_path)
    for model in manifest["models"]:
        if model["profile"] == TEXT_PROFILE:
            model["expected_size_gb"] = 1.0
    write_json(manifest_path, manifest)

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = setup_impl.print_catalog(tmp, as_json=True)
    payload = json.loads(output.getvalue())
    rows = {item["profile"]: item for item in payload["models"]}
    summary = setup_impl.catalog_summary(payload, compact=True)

    assert status == 1
    assert_fields(rows[TEXT_PROFILE], install_state="size-mismatch", size_state="size-mismatch")
    assert_contains(payload["issues"], "installed size")
    assert_field(payload["profile_checks"], "size_mismatch_count", 1)
    assert_field(summary, "size_mismatch_count", 1)


def test_local_ai_status_and_model_summaries_are_compact(tmp):
    write_config(tmp)
    status = setup_local_ai.build_status(tmp)
    status_summary = setup_impl.status_summary(status, compact=True)
    assert_fields(status_summary, tool="local-ai-helper.status-summary")
    assert_keys_lack(status_summary, "models", "cache_counts", "installed_profiles")
    assert "cache_total" in status_summary

    models_report = {
        "models": status["models"],
        "issues": status["issues"],
        "profile_checks": {},
        "policy": {},
    }
    models_summary = setup_impl.catalog_summary(models_report, compact=True)
    assert_keys_lack(models_summary, "models", "installed_profiles", "missing_profiles")
    assert models_summary["model_count"] >= 1


def test_status_json_stays_machine_readable_when_local_settings_are_written(tmp):
    write_config(tmp)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = setup_impl.print_status(tmp, as_json=True, summary=True)
    payload = json.loads(output.getvalue())

    assert exit_code in {0, 1}
    assert_fields(payload, tool="local-ai-helper.status-summary")
    assert tmp.joinpath(".agents", "local-ai", "local.settings.json").exists()


def test_vision_commands_validate_inputs_and_cache_paths(tmp):
    image = tmp / "screen.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    pdf = tmp / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    image_report = setup_local_ai.vision_describe_report(tmp, image=str(image), run_model=False)
    pdf_report = setup_local_ai.vision_pdf_report(tmp, pdf=str(pdf), pages="1,3-4", run_model=False)

    assert_ok(image_report)
    assert_fields(image_report, task="vision-describe", profile=VISION_PROFILE)
    assert image_report["cache_path"].startswith(".agents/local-ai/cache/vision/")
    assert_ok(pdf_report)
    assert_fields(pdf_report, task="vision-pdf", evidence=[{"page": 1}, {"page": 3}, {"page": 4}])
    assert pdf_report["cache_path"].startswith(".agents/local-ai/cache/vision/")

    gif = tmp / "bad.gif"
    gif.write_bytes(b"GIF89a")
    try:
        setup_local_ai.vision_describe_report(tmp, image=str(gif), run_model=False)
    except RuntimeError as exc:
        assert "JPEG or PNG" in str(exc)
    else:
        raise AssertionError("vision_describe_report accepted an unsupported image")


def test_clean_model_text_drops_llama_timestamp_logs():
    from local_ai_support import vision_impl

    output = "\n".join(
        [
            "0.00.049.354 I common_init_result: fitting params to device memory ...",
            "0.00.932.357 W load_hparams: more info: https://github.com/ggml-org/llama.cpp/issues/16842",
            f"You are a helpful assistant<|im_end|> Hello<|im_end|> {VISION_BLOCK_SUMMARY}",
        ]
    )
    assert vision_impl.clean_model_text(output) == VISION_BLOCK_SUMMARY


def test_bootstrap_json_is_machine_readable_when_config_is_written(tmp):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = setup_local_ai.print_bootstrap(
            tmp,
            task="skill-routing",
            profiles=[],
            download=False,
            force=False,
            run_model=False,
            max_download_gb=None,
            write_config=True,
            as_json=True,
        )
    payload = json.loads(output.getvalue())
    assert status == 1
    assert payload["config_written"] is True


def test_bootstrap_plain_text_collapses_missing_bundle_file_issues(tmp):
    report = {
        "schema_version": 1,
        "task": "skill-routing",
        "profiles": [TEXT_PROFILE],
        "config_written": True,
        "gitignore_updated": False,
        "downloaded": False,
        "estimated_download_gb": 1.2,
        "ready": False,
        "issues": [
            "Local AI bundle file is missing: D:\\Projects\\Skills\\.agents\\local-ai\\bundle\\models\\model.gguf",
            "Local AI bundle file is missing: D:\\Projects\\Skills\\.agents\\local-ai\\bundle\\runtimes\\runtime\\llama-cli.exe",
            "cache metadata is invalid",
        ],
        "next_action": "Run python -B .agents/manage.py local-ai bootstrap.",
    }
    original = setup_impl.bootstrap
    output = io.StringIO()

    setup_impl.bootstrap = lambda *_args, **_kwargs: report
    try:
        with contextlib.redirect_stdout(output):
            exit_code = setup_local_ai.print_bootstrap(
                tmp,
                task="skill-routing",
                profiles=[],
                download=False,
                force=False,
                run_model=False,
                max_download_gb=None,
                write_config=True,
                as_json=False,
            )
    finally:
        setup_impl.bootstrap = original

    text = output.getvalue()
    assert_field({"exit_code": exit_code}, "exit_code", 1)
    assert_has_all(text, "Missing local AI bundle files: 2", "cache metadata is invalid")
    assert_lacks_all(text, "model.gguf", "llama-cli.exe")


def test_readiness_report_separates_missing_parts_and_cache_diagnostics(tmp):
    write_config(tmp)
    cache = local_ai_cache_path(tmp, "skill-routing")
    cache.mkdir(parents=True)
    cache.joinpath("bad.json").write_text("{bad", encoding="utf-8", newline="\n")
    cache.joinpath("stale.json").write_text(
        json.dumps({"prompt_version": "old"}) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    report = setup_local_ai.readiness_report(tmp)

    assert_not_ok(report)
    assert_fields(report["categories"], missing_manifest=True)
    assert report["categories"]["invalid_schema_cache"]
    assert report["categories"]["stale_prompt_version"]
    assert "disk" in report
    assert_field(report["memory"], "candidate_memory_limit_gb", 20.0)
    summary = setup_impl.readiness_summary(report, compact=True)
    assert summary["not_ready_category_count"] >= 1
    assert_fields(summary, tool="local-ai-helper.readiness-summary")
    assert_lacks_all(summary, "categories")


def test_readiness_summary_compact_collapses_missing_bundle_file_issues(tmp):
    _ = tmp
    report = {
        "schema_version": 1,
        "ok": False,
        "status": "not-ready",
        "task": "skill-routing",
        "profile": TEXT_PROFILE,
        "categories": {"missing_manifest": True},
        "disk": {"free_gb": 100, "enough_for_default_download": True},
        "issues": [
            "Local AI bundle file is missing: D:\\Projects\\Skills\\.agents\\local-ai\\bundle\\models\\model.gguf",
            "Local AI bundle file is missing: D:\\Projects\\Skills\\.agents\\local-ai\\bundle\\runtimes\\runtime\\llama-cli.exe",
            "cache metadata is invalid",
        ],
        "next_action": "Run python -B .agents/manage.py local-ai bootstrap.",
    }

    summary = setup_impl.readiness_summary(report, compact=True)

    assert_field(summary, "issue_count", 3)
    assert_field(summary, "missing_bundle_file_count", 2)
    assert_contains(summary["advisories"], "model/runtime payloads are not downloaded")
    assert_field(summary, "issues", ["cache metadata is invalid"])
    assert_lacks(" ".join(summary.get("advisories", [])), "llama-cli.exe")


def test_readiness_plain_text_collapses_missing_bundle_file_issues(tmp):
    report = {
        "schema_version": 1,
        "ok": False,
        "status": "not-ready",
        "task": "skill-routing",
        "profile": TEXT_PROFILE,
        "categories": {"missing_manifest": True},
        "disk": {"free_gb": 100, "enough_for_default_download": True},
        "issues": [
            "Local AI bundle file is missing: D:\\Projects\\Skills\\.agents\\local-ai\\bundle\\models\\model.gguf",
            "Local AI bundle file is missing: D:\\Projects\\Skills\\.agents\\local-ai\\bundle\\runtimes\\runtime\\llama-cli.exe",
            "cache metadata is invalid",
        ],
        "next_action": "Run python -B .agents/manage.py local-ai bootstrap.",
    }
    original = setup_impl.readiness_report
    output = io.StringIO()

    setup_impl.readiness_report = lambda *_args, **_kwargs: report
    try:
        with contextlib.redirect_stdout(output):
            exit_code = setup_impl.print_readiness_report(
                tmp,
                task="skill-routing",
                profile=None,
                as_json=False,
            )
    finally:
        setup_impl.readiness_report = original

    text = output.getvalue()
    assert_field({"exit_code": exit_code}, "exit_code", 1)
    assert_has_all(text, "Missing local AI bundle files: 2", "cache metadata is invalid")
    assert_lacks_all(text, "model.gguf", "llama-cli.exe")


def test_status_plain_text_collapses_missing_bundle_file_issues(tmp):
    status = {
        "schema_version": 1,
        "tool": "local-ai-helper.status",
        "ok": False,
        "config_path": ".agents/local-ai.json",
        "local_settings_path": ".agents/local-ai/local.settings.json",
        "enabled": True,
        "mode": "auto",
        "gpu": {"mode": "auto"},
        "backend_order": ["cpu"],
        "backend_decision": {},
        "task": "skill-routing",
        "profile_order": [TEXT_PROFILE],
        "manifest_found": False,
        "model_found": False,
        "model_profile": TEXT_PROFILE,
        "selected_runtime": "",
        "cache_counts": {
            "skill-routing": {"accepted": 0, "rejected": 0},
            "workflow-routing": {"accepted": 0, "rejected": 0},
        },
        "issues": [
            "Local AI bundle file is missing: D:\\Projects\\Skills\\.agents\\local-ai\\bundle\\models\\model.gguf",
            "Local AI bundle file is missing: D:\\Projects\\Skills\\.agents\\local-ai\\bundle\\runtimes\\runtime\\llama-cli.exe",
            "cache metadata is invalid",
        ],
    }
    original = setup_impl.build_status
    output = io.StringIO()

    setup_impl.build_status = lambda *_args, **_kwargs: status
    try:
        with contextlib.redirect_stdout(output):
            exit_code = setup_impl.print_status(tmp, as_json=False, profile=None)
    finally:
        setup_impl.build_status = original

    text = output.getvalue()
    assert_field({"exit_code": exit_code}, "exit_code", 1)
    assert_has_all(text, "Missing local AI bundle files: 2", "cache metadata is invalid")
    assert_lacks_all(text, "model.gguf", "llama-cli.exe")


def test_readiness_report_skips_valid_non_object_cache_json(tmp):
    write_config(tmp)
    cache = local_ai_cache_path(tmp, "validation-triage")
    cache.mkdir(parents=True)
    cache.joinpath("rows.json").write_text("[1, 2, 3]\n", encoding="utf-8", newline="\n")

    report = setup_local_ai.readiness_report(tmp)

    assert_empty(report["categories"]["invalid_schema_cache"])
    assert_empty(report["categories"]["stale_prompt_version"])


def test_policy_report_lists_current_default_profiles(tmp):
    write_config(tmp)
    setup_local_ai.write_default_local_settings(tmp, force=False)
    report = setup_local_ai.model_policy_report(tmp)
    assert_fields(
        report,
        text_model=TEXT_PROFILE,
        embedding_model=EMBEDDING_PROFILE,
        vision_model=VISION_PROFILE,
        gpu_default="auto",
        policy_path=".agents/local-ai/policy.json",
    )
    assert_fields(report["integration_policy"], require_declared_metadata=True)
    assert_fields(report["selected_profiles"]["text"], profile=TEXT_PROFILE)
    assert_fields(report["selected_profiles"]["embedding"], profile=EMBEDDING_PROFILE)
    assert_fields(report["selected_profiles"]["vision"], profile=VISION_PROFILE)
    assert_fields(report["selected_profiles"]["routing"], profile=TEXT_PROFILE)
    assert_field(report["task_envelopes"]["dotnet10-xunit-authoring"], "route", "benchmark-only")
    assert_field(report["task_envelopes"]["dotnet10-xunit-repair"], "route", "benchmark-only")
    assert_empty(report["model_task_envelopes"][TEXT_PROFILE]["blocked_task_classes"])
    assert_field(report["benchmark_policy"], "baseline_epoch", "fresh-2026-06-11")
    assert_field(report["benchmark_policy"], "ignore_prior_results", True)
    assert_field(report["benchmark_policy"]["promotion_gates"], "must_beat_current_default", True)
    summary = setup_impl.model_policy_summary(report, compact=True)
    assert summary["owner_count"] >= 1
    assert summary["use_case_count"] >= 1
    assert_keys_lack(summary, "selected_profiles", "missing_profiles", "issues")
    assert_fields(summary, model_download_status="not-downloaded")
    assert_has_all(summary, "models_not_downloaded", "advisories")


def test_policy_compact_summary_collapses_missing_bundle_file_issues(tmp):
    _ = tmp
    report = {
        "schema_version": 1,
        "tool": "local-ai-helper.policy",
        "ok": True,
        "enabled": True,
        "policy_path": ".agents/local-ai/policy.json",
        "integration_policy": {
            "mode": "auto",
            "owners": {"skill-manager": {}},
            "use_cases": {"validation": {}},
            "secrets_file_present": False,
        },
        "selected_profiles": {
            "text": {"profile": TEXT_PROFILE, "installed": False, "tier": "small"},
        },
        "issues": [
            "Local AI bundle file is missing: D:\\Projects\\Skills\\.agents\\local-ai\\bundle\\models\\model.gguf",
            "Local AI bundle file is missing: D:\\Projects\\Skills\\.agents\\local-ai\\bundle\\runtimes\\runtime\\llama-cli.exe",
            "policy schema warning",
        ],
    }

    summary = setup_impl.model_policy_summary(report, compact=True)

    assert_field(summary, "missing_bundle_file_count", 2)
    assert_contains(summary["advisories"], "model/runtime payloads were not downloaded")
    assert_field(summary, "issues", ["policy schema warning"])
    assert_lacks(" ".join(summary.get("advisories", [])), "llama-cli.exe")


def test_policy_allows_governed_code_generation_use_cases(tmp):
    write_config(tmp)

    python_decision = policy_impl.evaluate_use_case(tmp, "simple-python-script", "workflow-manager")
    xunit_decision = policy_impl.evaluate_use_case(tmp, "dotnet10-xunit-authoring", "workflow-manager")

    assert python_decision["allowed"] is True, python_decision
    assert xunit_decision["allowed"] is True, xunit_decision
    assert_field(xunit_decision["use_case_policy"], "fallback", "orchestrator-until-fresh-benchmark")


def test_doctor_report_summary_is_compact(tmp):
    write_config(tmp)
    report = setup_impl.doctor_report(tmp, run_model=False, profile=None)
    summary = setup_impl.doctor_summary(report, compact=True)

    assert_fields(summary, tool="local-ai-helper.doctor-summary", check_count=3)
    assert_lacks_all(summary, "checks")
    assert_fields(summary, model_smoke_status="skipped")
    if not summary["ok"]:
        assert_field(summary, "next_command", "python -B .agents/manage.py local-ai readiness --summary --compact --json")


def test_doctor_compact_text_summary_does_not_require_next_command(tmp):
    _ = tmp
    original_doctor_report = setup_impl.doctor_report
    output = io.StringIO()

    def fake_doctor_report(root, *, run_model, profile):
        assert run_model is False
        assert profile is None
        return {
            "schema_version": 1,
            "tool": "local-ai-helper.doctor",
            "ok": True,
            "status": "passed",
            "checks": [{"name": "readiness", "ok": True, "result": {"status": "ready"}}],
            "model_smoke": {"status": "skipped"},
            "next_command": "python -B .agents/manage.py local-ai doctor --run-model",
        }

    try:
        setup_impl.doctor_report = fake_doctor_report
        with contextlib.redirect_stdout(output):
            exit_code = setup_impl.doctor(
                tmp,
                run_model=False,
                profile=None,
                as_json=False,
                summary=True,
                compact=True,
            )
    finally:
        setup_impl.doctor_report = original_doctor_report

    assert exit_code == 0
    text = output.getvalue()
    assert_has_all(text, "Local AI doctor summary", "Status: passed")
    assert_lacks_all(text, "Next:")


def test_policy_use_case_check_allows_and_denies_by_owner(tmp):
    write_config(tmp)
    allowed = policy_impl.evaluate_use_case(tmp, "validation-triage", "skill-manager")
    cost_allowed = policy_impl.evaluate_use_case(tmp, "failure-cluster", "skill-manager")
    denied = policy_impl.evaluate_use_case(tmp, "vision-pdf", "skill-manager")
    assert_fields(allowed, allowed=True)
    assert_fields(cost_allowed, allowed=True)
    assert_fields(denied, allowed=False)
    assert_has_all(denied["reason"], "not enabled for owner")


def test_bootstrap_writes_policy_and_secrets_example(tmp):
    report = setup_local_ai.bootstrap(root=tmp, download=False)
    assert_fields(report, policy_written=True, secrets_example_written=True)
    assert tmp.joinpath(".agents", "local-ai", "policy.json").exists()
    assert tmp.joinpath(".agents", "local-ai", "secrets.example.json").exists()


def test_document_inspect_reports_pdf_and_ooxml_strategy(tmp):
    pdf = tmp / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4\nBT\n(Selectable text in PDF) Tj\nET\n")
    pdf_report = setup_local_ai.document_inspect_report(tmp, file_path="sample.pdf")
    assert_ok(pdf_report)
    assert_fields(pdf_report, strategy="hybrid-pdf-text-first")
    assert "selectable-text" in pdf_report["strategy_order"]

    docx = tmp / "sample.docx"
    import zipfile

    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
        archive.writestr("word/media/image1.png", "png")
    doc_report = setup_local_ai.document_inspect_report(tmp, file_path="sample.docx")
    assert_ok(doc_report)
    assert_fields(doc_report, strategy="deterministic-ooxml-metadata")
    assert_fields(doc_report["evidence"][0], media_files=1)


def test_vision_prompt_requires_visible_text_not_metadata(tmp):
    from local_ai_support import vision_impl

    prompt_source = Path(vision_impl.__file__).read_text(encoding="utf-8")
    assert_has_all(prompt_source, "Use only pixel evidence", "Read visible raster text")


def test_filter_tests_selects_named_local_ai_tests():
    tests = [
        test_doctor_uses_embedding_smoke_for_embedding_profile,
        test_doctor_report_summary_is_compact,
    ]

    assert filter_tests(tests, []) == tests
    assert filter_tests(tests, ["embedding_smoke"]) == [test_doctor_uses_embedding_smoke_for_embedding_profile]


def _internal_self_tests():
    return [
        value
        for name, value in globals().items()
        if name.startswith("test_") and callable(value)
    ]


def filter_tests(tests, matches):
    if not matches:
        return tests
    needles = [match.lower() for match in matches if match.strip()]
    if not needles:
        return tests
    return [
        test
        for test in tests
        if any(needle in test.__name__.lower() for needle in needles)
    ]


def _run_test(test):
    if test.__code__.co_argcount:
        with tempfile.TemporaryDirectory() as temp_dir:
            test(Path(temp_dir))
    else:
        test()
    print(f"PASS {test.__name__}")


def run_tests(matches=None):
    tests = _internal_self_tests()
    selected = filter_tests(tests, matches or [])
    if matches and not selected:
        print(f"No local-ai-helper self-tests matched: {', '.join(matches)}", file=sys.stderr)
        return 2
    for test in selected:
        _run_test(test)
    if matches:
        print(f"local-ai-helper focused self-tests passed ({len(selected)}/{len(tests)}).")
    else:
        print("local-ai-helper self-tests passed.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Run local-ai-helper self-tests.")
    parser.add_argument(
        "--match",
        action="append",
        default=[],
        help="run only tests whose function name contains this text; repeatable",
    )
    args = parser.parse_args()
    return run_tests(args.match)


if __name__ == "__main__":
    raise SystemExit(main())
