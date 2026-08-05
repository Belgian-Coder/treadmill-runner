#!/usr/bin/env python3
"""Default local-AI routing config, model catalog, and policy constants."""

from __future__ import annotations

PROMPT_VERSION = "routing-v4"
CONFIG_RELATIVE_PATH = ".agents/local-ai.json"
PROJECT_SETTINGS_RELATIVE_PATH = ".agents/local-ai/project.settings.json"
LOCAL_SETTINGS_RELATIVE_PATH = ".agents/local-ai/local.settings.json"
DEFAULT_CACHE_DIR = ".agents/local-ai/cache"
DEFAULT_MANIFEST_PATH = ".agents/local-ai/bundle/manifest.json"
DEFAULT_MODEL_PROFILE = "nemotron3-nano4b"
DISABLE_ENV_VALUES = {"0", "false", "off", "no", "disabled"}
REQUIRED_ENV_VALUE = "required"
GPU_ALLOW_ENV = "SKILLS_LOCAL_AI_ALLOW_GPU"
GPU_BACKENDS = {"cuda", "vulkan", "hip", "sycl", "metal", "opencl"}
GPU_MODE_VALUES = {"off", "auto", "force"}
DEFAULT_LOCAL_SETTINGS = {
    "schema_version": 2,
    "gpu": {
        "mode": "auto",
        "preferred_backends": ["cuda", "vulkan", "cpu"],
        "allow_integrated": False,
        "auto_download_runtime": True,
        "auto_calibrate": True,
        "force_cpu_on_failure": True,
        "allow_experimental_workloads": False,
        "experimental_backends": [],
        "gpu_layers": 99,
        "probe_timeout_seconds": 5,
        "smoke_test_runtime": True,
        "smoke_timeout_seconds": 90,
        "performance_threshold_percent": 10.0,
    },
    "runtime_overrides": [],
    "task_model_profiles": {},
    "limits": {},
    "model_profiles": {},
    "backend_order": [],
    "backend_quarantine": [],
    "backend_calibrations": [],
}
DEFAULT_PROJECT_SETTINGS = {
    "schema_version": 1,
    "task_model_profiles": {},
    "limits": {},
    "model_profiles": {},
    "backend_order": [],
}
PERFORMANCE_OVERRIDE_FIELDS = {
    "threads",
    "threads_batch",
    "context_tokens",
    "output_tokens",
    "batch_size",
    "ubatch_size",
    "timeout_seconds",
    "confidence_threshold",
    "max_text_chars",
    "parallel_slots",
    "cache_type_k",
    "cache_type_v",
}
TASK_GROUPS = {
    "fast": [
        "skill-routing",
        "workflow-routing",
        "validation-triage",
        "changelog-draft",
        "inventory-summary",
        "changed-files-summary",
        "failure-cluster",
        "handoff-draft",
        "duplicate-overlap-detection",
    ],
    "planning-review": [
        "code-review",
        "implementation-planning",
        "patch-draft",
        "test-gap-summary",
    ],
    "vision": ["vision-describe", "vision-pdf"],
}
DEFAULT_LIMITS = {
    "threads": 8,
    "threads_batch": 8,
    "context_tokens": 4096,
    "output_tokens": 256,
    "batch_size": 512,
    "ubatch_size": 256,
    "timeout_seconds": 300,
    "confidence_threshold": 0.7,
    "max_text_chars": 180,
}
DEFAULT_TASK_ATTEMPT_POLICY = {
    "max_attempts_per_profile": 2,
    "retry_on_low_confidence": True,
    "retry_on_plain_text": True,
    "retry_failure_classes": [
        "schema",
        "path",
        "missing-required-file",
        "extra-file",
        "plain-text",
        "low-confidence",
    ],
    "handoff_failure_classes": [
        "compile",
        "test",
        "mutation",
        "wrong-framework",
        "wrong-package",
        "timeout",
        "memory-limit",
        "unsupported-task",
    ],
    "fallback": "orchestrator-handoff",
}
DEFAULT_BENCHMARK_POLICY = {
    "baseline_epoch": "fresh-2026-06-11",
    "candidate_memory_limit_gb": 20.0,
    "ignore_prior_results": True,
    "require_peak_memory_evidence": True,
    "promotion_gates": {
        "same_suite": True,
        "same_tasks": True,
        "same_validators": True,
        "same_repetitions": True,
        "license_required": True,
        "must_pass_suite": True,
        "must_fit_memory": True,
        "must_beat_current_default": True,
    },
}
DEFAULT_TASK_ENVELOPES = {
    "routing-summary-triage": {
        "route": "installed-smoke-default",
        "task_classes": ["routing", "summary", "triage", "classification", "inventory", "handoff"],
        "profiles": ["nemotron3-nano4b"],
        "max_attempts": 2,
        "validation_gate": "structured-json-confidence",
        "fallback": "deterministic-orchestrator",
    },
    "simple-python-script": {
        "route": "benchmark-only",
        "task_classes": ["simple-python-script"],
        "profiles": [],
        "max_attempts": 0,
        "validation_gate": "python-execution",
        "fallback": "orchestrator-until-fresh-benchmark",
    },
    "dotnet10-di-console": {
        "route": "benchmark-only",
        "task_classes": ["dotnet-di-console-draft"],
        "profiles": [],
        "max_attempts": 0,
        "validation_gate": "dotnet-build-and-run",
        "fallback": "orchestrator-until-fresh-benchmark",
    },
    "dotnet10-xunit-authoring": {
        "route": "benchmark-only",
        "task_classes": ["dotnet-xunit-authoring-final"],
        "profiles": [],
        "max_attempts": 0,
        "validation_gate": "dotnet-test-and-mutation-test",
        "fallback": "orchestrator-until-fresh-benchmark",
    },
    "dotnet10-xunit-repair": {
        "route": "benchmark-only",
        "task_classes": ["dotnet-xunit-repair-draft"],
        "profiles": [],
        "max_attempts": 0,
        "validation_gate": "dotnet-test-and-mutation-test",
        "fallback": "orchestrator-until-fresh-benchmark",
    },
    "vision-pdf-image": {
        "route": "installed-smoke-default",
        "task_classes": ["vision", "image-description", "pdf-page-pixels"],
        "profiles": ["qwen3vl-2b-q4"],
        "max_attempts": 1,
        "validation_gate": "pixel-evidence",
        "fallback": "deterministic-extraction",
    },
}
DEFAULT_MODEL_TASK_ENVELOPES = {
    "nemotron3-nano4b": {
        "max_task_class": "installed-smoke-default",
        "allowed_task_classes": ["routing", "summary", "triage", "classification", "inventory", "handoff"],
        "blocked_task_classes": [],
        "reason": "Operational smoke default only; old code-generation restrictions were retired pending a fresh benchmark baseline.",
    },
    "qwen3-embedding-4b": {
        "max_task_class": "benchmark-only",
        "allowed_task_classes": ["embedding-benchmark"],
        "blocked_task_classes": [],
        "reason": "Embedding profiles remain catalogued for explicit benchmarks; repository retrieval is direct and model-free.",
    },
    "qwen3vl-2b-q4": {
        "max_task_class": "installed-smoke-default",
        "allowed_task_classes": ["vision", "image-description", "pdf-page-pixels"],
        "blocked_task_classes": [],
        "reason": "Operational vision smoke default only; multimodal promotion requires a fresh benchmark baseline.",
    },
}
DEFAULT_PRIMARY_PROFILES = ["nemotron3-nano4b"]
DEFAULT_OPTIONAL_PROFILES = ["qwen3-embedding-0.6b-q8", "qwen3-embedding-4b-q5km", "qwen3-embedding-8b-q4km"]
DEFAULT_EMBEDDING_PROFILES = [
    "qwen3-embedding-4b",
    "qwen3-embedding-0.6b-q8",
    "qwen3-embedding-4b-q5km",
    "qwen3-embedding-8b-q4km",
]
DEFAULT_VISION_PROFILES = ["qwen3vl-2b-q4"]
DEFAULT_IMAGE_DESCRIPTION_PROFILE = "qwen3vl-2b-q4"
DEFAULT_MODEL_PROFILES = {
    "nemotron3-nano4b": {
        "roles": ["routing", "summary", "triage", "inventory", "json", "review", "planning", "draft"],
        "quant": "Q4_K_M",
        "context_tokens": 4096,
        "output_tokens": 192,
        "batch_size": 1024,
        "ubatch_size": 256,
        "cache_type_k": "q4_0",
        "cache_type_v": "q4_0",
        "reasoning": "off",
        "fallback_profiles": [],
    },
}
DEFAULT_TASK_MODEL_PROFILES = {
    "skill-routing": ["nemotron3-nano4b"],
    "workflow-routing": ["nemotron3-nano4b"],
    "validation-triage": ["nemotron3-nano4b"],
    "changelog-draft": ["nemotron3-nano4b"],
    "inventory-summary": ["nemotron3-nano4b"],
    "code-review": ["nemotron3-nano4b"],
    "implementation-planning": ["nemotron3-nano4b"],
    "patch-draft": ["nemotron3-nano4b"],
    "changed-files-summary": ["nemotron3-nano4b"],
    "failure-cluster": ["nemotron3-nano4b"],
    "test-gap-summary": ["nemotron3-nano4b"],
    "handoff-draft": ["nemotron3-nano4b"],
    "duplicate-overlap-detection": ["nemotron3-nano4b"],
    "vision-describe": ["qwen3vl-2b-q4"],
    "vision-pdf": ["qwen3vl-2b-q4"],
}
DEFAULT_MODEL_CATALOG = {
    "nemotron3-nano4b": {
        "profile": "nemotron3-nano4b",
        "kind": "text",
        "tier": "primary",
        "default_install": True,
        "model": "NVIDIA Nemotron 3 Nano 4B Q4_K_M GGUF",
        "base_model": "nvidia/NVIDIA-Nemotron-3-Nano-4B",
        "source": "https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF",
        "source_url": "https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF/resolve/main/NVIDIA-Nemotron3-Nano-4B-Q4_K_M.gguf",
        "license_url": "https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-nemotron-open-model-license/",
        "download_kind": "direct",
        "license": "NVIDIA Open Model License",
        "quant": "Q4_K_M",
        "expected_size_gb": 2.64,
        "roles": ["routing", "summary", "triage", "inventory", "json", "review", "planning", "draft"],
    },
    "qwen3-embedding-4b": {
        "profile": "qwen3-embedding-4b",
        "kind": "embedding",
        "tier": "embedding",
        "default_install": False,
        "model": "Qwen3 Embedding 4B Q4_K_M GGUF",
        "base_model": "Qwen/Qwen3-Embedding-4B",
        "source": "https://huggingface.co/Qwen/Qwen3-Embedding-4B-GGUF",
        "source_url": "https://huggingface.co/Qwen/Qwen3-Embedding-4B-GGUF/resolve/main/Qwen3-Embedding-4B-Q4_K_M.gguf",
        "license_url": "https://www.apache.org/licenses/LICENSE-2.0.txt",
        "download_kind": "direct",
        "license": "Apache-2.0",
        "quant": "Q4_K_M",
        "expected_size_gb": 2.33,
        "roles": ["embedding", "benchmark"],
    },
    "qwen3-embedding-0.6b-q8": {
        "profile": "qwen3-embedding-0.6b-q8",
        "kind": "embedding",
        "tier": "optional-embedding",
        "default_install": False,
        "model": "Qwen3 Embedding 0.6B Q8_0 GGUF",
        "base_model": "Qwen/Qwen3-Embedding-0.6B",
        "source": "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF",
        "source_url": "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf",
        "license_url": "https://www.apache.org/licenses/LICENSE-2.0.txt",
        "download_kind": "direct",
        "license": "Apache-2.0",
        "quant": "Q8_0",
        "expected_size_gb": 0.63,
        "roles": ["embedding", "benchmark"],
    },
    "qwen3-embedding-4b-q5km": {
        "profile": "qwen3-embedding-4b-q5km",
        "kind": "embedding",
        "tier": "optional-embedding",
        "default_install": False,
        "model": "Qwen3 Embedding 4B Q5_K_M GGUF",
        "base_model": "Qwen/Qwen3-Embedding-4B",
        "source": "https://huggingface.co/Qwen/Qwen3-Embedding-4B-GGUF",
        "source_url": "https://huggingface.co/Qwen/Qwen3-Embedding-4B-GGUF/resolve/main/Qwen3-Embedding-4B-Q5_K_M.gguf",
        "license_url": "https://www.apache.org/licenses/LICENSE-2.0.txt",
        "download_kind": "direct",
        "license": "Apache-2.0",
        "quant": "Q5_K_M",
        "expected_size_gb": 2.69,
        "roles": ["embedding", "benchmark"],
    },
    "qwen3-embedding-8b-q4km": {
        "profile": "qwen3-embedding-8b-q4km",
        "kind": "embedding",
        "tier": "optional-embedding",
        "default_install": False,
        "model": "Qwen3 Embedding 8B Q4_K_M GGUF",
        "base_model": "Qwen/Qwen3-Embedding-8B",
        "source": "https://huggingface.co/Qwen/Qwen3-Embedding-8B-GGUF",
        "source_url": "https://huggingface.co/Qwen/Qwen3-Embedding-8B-GGUF/resolve/main/Qwen3-Embedding-8B-Q4_K_M.gguf",
        "license_url": "https://www.apache.org/licenses/LICENSE-2.0.txt",
        "download_kind": "direct",
        "license": "Apache-2.0",
        "quant": "Q4_K_M",
        "expected_size_gb": 4.36,
        "roles": ["embedding", "benchmark"],
    },
    "qwen3vl-2b-q4": {
        "profile": "qwen3vl-2b-q4",
        "kind": "vision",
        "tier": "vision",
        "default_install": True,
        "model": "Qwen3-VL 2B Instruct Q4_K_M GGUF",
        "base_model": "Qwen/Qwen3-VL-2B-Instruct",
        "source": "https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF",
        "source_url": "https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF/resolve/main/Qwen3VL-2B-Instruct-Q4_K_M.gguf",
        "mmproj_url": "https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF/resolve/main/mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf",
        "license_url": "https://www.apache.org/licenses/LICENSE-2.0.txt",
        "download_kind": "direct",
        "license": "Apache-2.0",
        "quant": "Q4_K_M",
        "expected_size_gb": 1.45,
        "roles": ["vision", "image-description", "jpeg", "png"],
    },
}
DEFAULT_SERVER_CONFIG = {
    "host": "127.0.0.1",
    "port": 8765,
    "cache_prompt": True,
    "parallel_slots": 1,
    "mlock": False,
}
DEFAULT_TOOLS_CONFIG = {
    "mode": "brokered-read-only",
    "allow": ["repo.search", "repo.read", "repo.tree", "repo.generated-status"],
    "max_read_bytes": 20000,
    "max_search_results": 50,
    "max_tree_entries": 200,
    "timeout_seconds": 5,
    "exclude_paths": [
        ".git",
        ".agents/local-ai/cache",
        ".agents/local-ai/bundle",
        ".agents/local-ai/downloads",
        ".agents/tools/cache",
        ".agents/.deps",
        ".agents/registry.json",
        ".aider.conf.yml",
        ".claude",
        ".continue",
        ".github/copilot-instructions.md",
        "GEMINI.md",
        "automations/registry.json",
    ],
}
COMMERCIAL_OK_LICENSES = {"apache-2.0", "mit", "nvidia open model license"}
SUPPORTED_CACHE_TYPES = {"f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"}
SUPPORTED_REASONING_VALUES = {"on", "off", "auto"}
CACHED_REJECTION = "__cached_rejection__"
BOOTSTRAP_AUTO_DOWNLOAD_VALUES = {"1", "true", "yes", "on", "auto", "always", "on-local-ai-use"}
