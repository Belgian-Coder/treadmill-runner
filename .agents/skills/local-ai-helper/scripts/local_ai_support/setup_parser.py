#!/usr/bin/env python3
"""Argparse construction for the local AI helper CLI."""

from __future__ import annotations

import argparse


def build_parser(
    *,
    description: str | None,
    root_default: str,
    daily_text_tasks: set[str],
    download_profiles: list[str],
    profile_choices: list[str],
    approved_owners: set[str],
    default_model_profile: str,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description, prog="setup_local_ai.py")
    parser.add_argument("--root", default=root_default, help="repository root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser(
        "status",
        help="show status; may create gitignored local settings",
        description="show local AI status. may create gitignored local settings when they are missing.",
    )
    status_parser.add_argument("--json", action="store_true", help="print machine-readable status")
    status_parser.add_argument("--task", default="skill-routing", help="task route to inspect")
    status_parser.add_argument("--profile", help="force a profile for status inspection")
    status_parser.add_argument("--summary", action="store_true", help="emit readiness counts and selected model state")
    status_parser.add_argument("--compact", action="store_true", help="with --summary, omit verbose model/cache rows")

    readiness_parser = subparsers.add_parser(
        "readiness",
        help="read-only: separate first-run readiness causes",
        description="read-only check that separates first-run readiness causes without writing config, cache, or settings.",
    )
    readiness_parser.add_argument("--json", action="store_true", help="print machine-readable readiness")
    readiness_parser.add_argument("--task", default="skill-routing", help="task route to inspect")
    readiness_parser.add_argument("--profile", help="force a profile for readiness inspection")
    readiness_parser.add_argument("--summary", action="store_true", help="emit readiness counts and action state")
    readiness_parser.add_argument("--compact", action="store_true", help="with --summary, omit category details")

    configure_parser = subparsers.add_parser(
        "configure",
        help="detect this machine and preview or write bounded local-AI settings",
        description=(
            "Detect CPU, memory, disk, GPU support, installed profiles, and prior calibration; "
            "preview a catalog-only proposal. No model or runtime is downloaded."
        ),
    )
    configure_parser.add_argument("--scope", choices=("local", "project"), default="local")
    configure_parser.add_argument("--apply", action="store_true", help="write the previewed settings without an interactive prompt")
    configure_parser.add_argument("--route", action="append", default=[], metavar="TASK=PROFILE[,PROFILE]", help="override one task route; repeatable")
    configure_parser.add_argument("--group-route", action="append", default=[], metavar="GROUP=PROFILE[,PROFILE]", help="override fast, planning-review, or vision routes")
    configure_parser.add_argument("--backend-order", help="comma-separated portable backend preference order")
    configure_parser.add_argument("--gpu-mode", choices=("off", "auto", "force"), help="local scope only")
    configure_parser.add_argument("--allow-integrated", action="store_true", help="local scope only: permit an integrated GPU")
    configure_parser.add_argument("--threads", type=int)
    configure_parser.add_argument("--threads-batch", type=int)
    configure_parser.add_argument("--context-tokens", type=int)
    configure_parser.add_argument("--output-tokens", type=int)
    configure_parser.add_argument("--batch-size", type=int)
    configure_parser.add_argument("--ubatch-size", type=int)
    configure_parser.add_argument("--timeout-seconds", type=int)
    configure_parser.add_argument("--parallelism", type=int, dest="parallel_slots", help="bounded llama-server parallel slots")
    configure_parser.add_argument("--gpu-layers", type=int, help="local scope only: bounded GPU offload layers")
    configure_parser.add_argument("--cache-type-k")
    configure_parser.add_argument("--cache-type-v")
    configure_parser.add_argument("--json", action="store_true", help="print the proposal as JSON")

    config_parser = subparsers.add_parser("config", help="explain effective layered local-AI configuration")
    config_subparsers = config_parser.add_subparsers(dest="config_action", required=True)
    config_explain_parser = config_subparsers.add_parser("explain", help="explain the selected route and fallback for one task")
    config_explain_parser.add_argument("--task", required=True, help="configured local-AI task")
    config_explain_parser.add_argument("--json", action="store_true", help="print machine-readable explanation")

    policy_parser = subparsers.add_parser(
        "policy",
        help="read-only unless --write-default: show current local AI policy",
        description="show current local AI policy. read-only unless --write-default is supplied.",
    )
    policy_parser.add_argument("policy_action", nargs="?", choices=("explain",), help="use `explain <use-case>` for one policy decision")
    policy_parser.add_argument("policy_use_case", nargs="?", help="use case to explain when policy_action is explain")
    policy_parser.add_argument("--json", action="store_true", help="print machine-readable model policy")
    policy_parser.add_argument("--check-use-case", help="check whether a local AI use case is allowed")
    policy_parser.add_argument("--owner", choices=sorted(approved_owners), help="policy owner to check")
    policy_parser.add_argument("--write-default", action="store_true", help="write missing policy/example files and gitignore rules")
    policy_parser.add_argument("--summary", action="store_true", help="emit policy counts and selected profile status")
    policy_parser.add_argument("--compact", action="store_true", help="with --summary, omit verbose policy maps")

    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        aliases=["ensure"],
        help="write config/settings and prepare the pinned CPU bundle for first use",
        description=(
            "Prepare local AI for first use. This writes config/local settings unless "
            "--dry-run is used and can download pinned payloads unless --no-download "
            "or --dry-run is used. Use readiness/policy for strict read-only checks."
        ),
    )
    bootstrap_parser.add_argument("--json", action="store_true", help="print machine-readable bootstrap status")
    bootstrap_parser.add_argument("--task", default="skill-routing", help="task route to verify")
    bootstrap_parser.add_argument("--force", action="store_true", help="redownload and re-extract files")
    bootstrap_parser.add_argument("--no-download", action="store_true", help="write/check config without downloading")
    bootstrap_parser.add_argument("--dry-run", action="store_true", help="check what bootstrap would do without writes")
    bootstrap_parser.add_argument("--run-model", action="store_true", help="run one short CPU smoke test after setup")
    bootstrap_parser.add_argument("--max-download-gb", type=float, help="override bootstrap max_download_gb")
    bootstrap_parser.add_argument(
        "--profile",
        action="append",
        choices=download_profiles,
        default=[],
        help="profile to ensure; repeatable. Defaults to bootstrap.default_profiles.",
    )

    integrations_parser = subparsers.add_parser(
        "integrations",
        help="show optional skill and workflow integration points",
    )
    integrations_parser.add_argument(
        "--target",
        choices=("all", "skill", "workflow"),
        default="all",
        help="integration target to list; default: all",
    )
    integrations_parser.add_argument("--json", action="store_true", help="print machine-readable suggestions")
    integrations_parser.add_argument("--summary", action="store_true", help="emit integration counts and failures only")
    integrations_parser.add_argument("--compact", action="store_true", help="with --summary, omit available integration rows")

    download_parser = subparsers.add_parser("download", help="network/write: download and verify the pinned CPU bundle")
    download_parser.add_argument("--force", action="store_true", help="redownload and re-extract files")
    download_parser.add_argument(
        "--profile",
        action="append",
        choices=download_profiles,
        default=[],
        help="profile to download; repeatable. Defaults to profiles marked for default installation (currently text and vision).",
    )

    catalog_parser = subparsers.add_parser("catalog", help="list primary and optional local model catalog entries")
    catalog_parser.add_argument("--json", action="store_true", help="print machine-readable catalog")
    catalog_parser.add_argument("--summary", action="store_true", help="emit catalog counts and install states")
    catalog_parser.add_argument("--compact", action="store_true", help="with --summary, omit full catalog rows")

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="read-only with --quick; optional model smoke test with --full/--run-model",
        description="run local AI checks. read-only with --quick; --full/--run-model starts a model smoke test.",
    )
    doctor_parser.add_argument("--json", action="store_true", help="print machine-readable doctor report")
    doctor_parser.add_argument("--summary", action="store_true", help="emit doctor counts and failures only")
    doctor_parser.add_argument("--compact", action="store_true", help="with --summary, omit passing check rows")
    doctor_parser.add_argument("--run-model", action="store_true", help="run one short CPU inference")
    doctor_parser.add_argument("--profile", default=None, help="profile to smoke-test")
    doctor_parser.add_argument("--repair-plan", action="store_true", help="print exact safe repair commands instead of running repairs")
    doctor_depth = doctor_parser.add_mutually_exclusive_group()
    doctor_depth.add_argument("--quick", action="store_true", help="run readiness and policy checks only")
    doctor_depth.add_argument("--full", action="store_true", help="run readiness, policy, and a model smoke test")

    models_parser = subparsers.add_parser("models", help="list configured model profiles")
    models_parser.add_argument(
        "models_action",
        nargs="?",
        choices=("prune", "inventory", "compare-installed", "explain-defaults", "validate-urls", "evaluate-candidate"),
        help="optional models action",
    )
    models_parser.add_argument("--unused", action="store_true", help="with `models prune`, remove no inactive files unless explicitly safe")
    models_parser.add_argument("--disk", action="store_true", help="include installed model file sizes and prune-safe candidates")
    models_parser.add_argument(
        "--profile",
        action="append",
        choices=download_profiles,
        default=[],
        help="profile to validate for `models validate-urls`; repeatable",
    )
    models_parser.add_argument("--timeout-seconds", type=int, default=10, help="URL validation timeout in seconds")
    models_parser.add_argument(
        "--candidate",
        help="LocalModelCandidateV1 JSON file for `models evaluate-candidate`",
    )
    models_parser.add_argument("--json", action="store_true", help="print machine-readable model status")
    models_parser.add_argument("--summary", action="store_true", help="emit model counts and install states")
    models_parser.add_argument("--compact", action="store_true", help="with --summary, omit full model rows")

    select_parser = subparsers.add_parser("select", help="set the active local AI profile")
    select_parser.add_argument("--profile", required=True, choices=profile_choices)
    select_parser.add_argument("--task", help="also prefer this profile for a specific task")

    remove_parser = subparsers.add_parser("remove", help="remove an optional local AI model file")
    remove_parser.add_argument("--profile", required=True, choices=download_profiles)

    bench_parser = subparsers.add_parser("bench", help="run local AI benchmark fixtures")
    bench_parser.add_argument("--run-model", action="store_true", help="run actual CPU inference")
    bench_parser.add_argument("--profile", action="append", default=[], help="profile to include; repeatable")
    bench_parser.add_argument(
        "--backend",
        choices=("auto", "cpu", "cuda", "vulkan", "hip", "sycl", "opencl"),
        help="process-local backend override for this benchmark run",
    )
    bench_parser.add_argument("--repetitions", type=int, default=1, help="number of measured repetitions per fixture")
    bench_parser.add_argument("--cold", action="store_true", help="mark measured runs as cold-start runs")
    bench_parser.add_argument("--warm", action="store_true", help="mark repeated runs as warm-cache runs")
    bench_parser.add_argument("--standard-metrics", action="store_true", help="include standards-aligned timing fields")
    bench_parser.add_argument("--suite", help="optional suite label for benchmark metadata")
    bench_parser.add_argument("--detached-command", action="store_true", help="print a crash-safe CPU-only detached benchmark command and exit")
    bench_parser.add_argument("--sweep-command", action="store_true", help="print detached commands for a CPU/GPU backend matrix and exit")
    bench_parser.add_argument(
        "--sweep-backend",
        action="append",
        choices=("cpu", "cuda", "vulkan", "hip", "sycl", "opencl"),
        default=[],
        help="backend to include with --sweep-command; repeatable",
    )
    bench_parser.add_argument("--validate-model-urls", action="store_true", help="check configured model URLs before running")
    bench_parser.add_argument("--json", action="store_true", help="print machine-readable benchmark output")

    runtime_parser = subparsers.add_parser("runtime", help="inspect configured CPU llama.cpp runtimes")
    runtime_subparsers = runtime_parser.add_subparsers(dest="runtime_command", required=True)
    runtime_doctor_parser = runtime_subparsers.add_parser("doctor", help="compare runtime availability, MTP support, and crash-safe guidance")
    runtime_doctor_parser.add_argument("--json", action="store_true", help="print machine-readable report")
    runtime_doctor_parser.add_argument("--summary", action="store_true", help="emit runtime counts and failures only")
    runtime_doctor_parser.add_argument("--compact", action="store_true", help="with --summary, omit runtime path and hash rows")
    runtime_ensure_parser = runtime_subparsers.add_parser("ensure-gpu", help="download and probe a compatible local GPU runtime")
    runtime_ensure_parser.add_argument(
        "--backend",
        action="append",
        choices=("cuda", "hip", "opencl", "sycl", "vulkan"),
        default=[],
        help="GPU backend to try; repeatable. Defaults to local preferred GPU backends.",
    )
    runtime_ensure_parser.add_argument("--force", action="store_true", help="redownload and re-extract the selected runtime")
    runtime_ensure_parser.add_argument("--probe", action="store_true", help="run llama-cli --list-devices before enabling the runtime")
    runtime_ensure_parser.add_argument("--dry-run", action="store_true", help="report selected package without writing files")
    runtime_ensure_parser.add_argument("--timeout-seconds", type=int, help="override GPU probe timeout")
    runtime_ensure_parser.add_argument("--json", action="store_true", help="print machine-readable report")

    task_parser = subparsers.add_parser(
        "task",
        help="cache-writing: run a safe daily local AI text task",
        description="cache-writing command that runs a safe daily local AI text task.",
    )
    task_parser.add_argument("--task", required=True, choices=sorted(daily_text_tasks), help="daily task to run")
    task_parser.add_argument(
        "--input",
        action="append",
        required=True,
        dest="inputs",
        help="repo-local UTF-8 input file, or '-' to read stdin; repeatable",
    )
    task_parser.add_argument("--json", action="store_true", help="print machine-readable report")

    vision_parser = subparsers.add_parser(
        "vision",
        help="cache-writing: describe repo-local images or rendered PDF pages",
        description="cache-writing commands that describe repo-local images or rendered PDF pages.",
    )
    vision_subparsers = vision_parser.add_subparsers(dest="vision_command", required=True)
    vision_describe_parser = vision_subparsers.add_parser("describe", help="describe a JPEG or PNG image")
    vision_describe_parser.add_argument("--image", required=True, help="repo-local JPEG or PNG path")
    vision_describe_parser.add_argument("--json", action="store_true", help="print machine-readable report")
    vision_pdf_parser = vision_subparsers.add_parser("pdf", help="render selected PDF pages and describe the pixels")
    vision_pdf_parser.add_argument("--pdf", required=True, help="repo-local PDF path")
    vision_pdf_parser.add_argument("--pages", default="1", help="1-based page list or range, for example 1-5")
    vision_pdf_parser.add_argument("--json", action="store_true", help="print machine-readable report")

    document_parser = subparsers.add_parser(
        "document",
        help="cache-writing: inspect repo-local documents before local AI use",
        description="cache-writing commands that inspect repo-local documents before local AI use.",
    )
    document_subparsers = document_parser.add_subparsers(dest="document_command", required=True)
    document_inspect_parser = document_subparsers.add_parser("inspect", help="choose a deterministic document inspection strategy")
    document_inspect_parser.add_argument("--file", required=True, dest="file_path", help="repo-local PDF, Office, text, or image-adjacent file")
    document_inspect_parser.add_argument("--json", action="store_true", help="print machine-readable report")

    server_parser = subparsers.add_parser("server", help="manage the optional llama-server process")
    server_subparsers = server_parser.add_subparsers(dest="server_command", required=True)
    start_parser = server_subparsers.add_parser("start", help="start llama-server for one profile")
    start_parser.add_argument("--profile", default=default_model_profile, choices=profile_choices)
    server_subparsers.add_parser("stop", help="stop the recorded llama-server process")
    server_subparsers.add_parser("status", help="show the recorded llama-server process")

    subparsers.add_parser("write-config", help="write .agents/local-ai.json if it is missing")
    resources_parser = subparsers.add_parser("resources", help="detect local CPU, memory, disk, and visible GPU resources")
    resources_parser.add_argument("--json", action="store_true", help="print machine-readable resource facts")
    resources_parser.add_argument("--summary", action="store_true", help="emit resource counts and recommendations")
    resources_parser.add_argument("--compact", action="store_true", help="with --summary, omit device detail rows")
    return parser
