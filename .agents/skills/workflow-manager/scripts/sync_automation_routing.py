#!/usr/bin/env python3
"""Generate automation workflow routing and registry artifacts for this repository."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import workflow_manager_common as common
from validation_support.discovery import discover_automation_dirs
from validation_support.manifests import (
    as_non_empty_string_list,
    local_ai_use_case_summary,
    command_argvs,
    command_specs,
    manifest_path,
    normalize_external_access,
    phase_ids,
    read_optional_manifest,
)
from validation_support.module_checks import validate_automations
from workflow_support.context_contract import context_packet_schema
from workflow_support.workers import normalized_phase_assignments

ROUTING_VERSION = 4
UNSPECIFIED = "unspecified"


def default_root() -> Path:
    return Path(__file__).resolve().parents[4]


def compact_values(values: list[str], empty: str = "None") -> str:
    if not values:
        return empty
    compact = values[:4]
    if len(values) > 4:
        compact.append(f"... {len(values) - 4} more")
    return "<br>".join(value.replace("|", "\\|") for value in compact)


def risk_flags(risk: object) -> list[str]:
    if not isinstance(risk, dict):
        return [UNSPECIFIED]
    flags = sorted(key for key in common.RISK_KEYS if risk.get(key) is True)
    profile = risk.get("profile")
    if isinstance(profile, str) and profile.strip():
        flags.insert(0, f"profile: {profile.strip()}")
    return flags or ["none declared"]


def string_list(value: object) -> list[str]:
    values = as_non_empty_string_list(value)
    return values if values is not None else [UNSPECIFIED]


def phase_list(manifest: dict[str, Any] | None) -> list[str]:
    if manifest is None:
        return [UNSPECIFIED]
    phases = phase_ids(manifest)
    return phases or [UNSPECIFIED]


def phase_lifecycle_summary(manifest: dict[str, Any] | None) -> dict[str, list[str]]:
    if manifest is None or not isinstance(manifest.get("phase_lifecycle"), dict):
        return {"events": [], "state_fields": [], "required_handoff_fields": []}
    lifecycle = manifest["phase_lifecycle"]
    return {
        "events": string_list(lifecycle.get("events")),
        "state_fields": string_list(lifecycle.get("state_fields")),
        "required_handoff_fields": string_list(lifecycle.get("required_handoff_fields")),
    }


def start_summary(path: Path) -> str:
    if not path.exists():
        return UNSPECIFIED
    text = common.read_text(path, limit=20_000)
    lines = [line.strip() for line in text.splitlines()]
    for line in lines:
        if not line or line.startswith("#"):
            continue
        return line
    return UNSPECIFIED


def compact_summary(value: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
    text = sentence or text
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def unspecified_external_access() -> dict[str, Any]:
    return {
        "source_systems": [UNSPECIFIED],
        "credential_expectations": UNSPECIFIED,
        "data_copied_locally": [UNSPECIFIED],
        "attachments_retrieved": UNSPECIFIED,
    }


def contract_external_access(contract: dict[str, Any]) -> dict[str, Any]:
    access = contract.get("external_access")
    if not isinstance(access, dict):
        return unspecified_external_access()
    return {
        "source_systems": access.get("source_systems") or ["None"],
        "credential_expectations": access.get("credential_expectations") or "none",
        "data_copied_locally": access.get("data_copied_locally") or ["unspecified"],
        "attachments_retrieved": access.get("attachments_retrieved", False),
    }


def build_entry(root: Path, module_dir: Path) -> dict[str, Any]:
    manifest, error = read_optional_manifest(module_dir)
    if error:
        raise ValueError(
            f"{common.relative(root, manifest_path(module_dir))} is invalid: {error}"
        )
    if manifest is None:
        raise ValueError(f"{common.relative(root, module_dir / 'module.json')} is required.")
    local_ai = local_ai_use_case_summary(manifest)
    entry = {
        "id": str(manifest.get("id", module_dir.name)),
        "folder": common.relative(root, module_dir),
        "start_file": "WORKFLOW.md",
        "contract_file": "module.json",
        "version": str(manifest.get("version", UNSPECIFIED)),
        "summary": str(manifest.get("summary") or start_summary(module_dir / "WORKFLOW.md")),
        "owners": string_list(manifest.get("owners")),
        "phases": phase_list(manifest),
        "inputs": string_list(manifest.get("inputs")),
        "outputs": string_list(manifest.get("outputs")),
        "related_modules": string_list(manifest.get("related_modules")),
        "related_skills": string_list(manifest.get("related_modules")),
        "scripts": command_argvs(manifest.get("commands")) or [[UNSPECIFIED]],
        "commands": command_specs(manifest.get("commands")),
        "skill_extensions": [],
        "local_ai": local_ai,
        "external_access": normalize_external_access(manifest.get("external_access")),
        "risk_flags": risk_flags(manifest.get("risk")),
        "manifest": "present",
        "contract": "module.json",
    }
    lifecycle = phase_lifecycle_summary(manifest)
    if any(lifecycle.values()):
        entry["phase_lifecycle"] = lifecycle
    routing = manifest.get("routing")
    if isinstance(routing, dict):
        entry["routing"] = dict(routing)
    worker_config = manifest.get("worker_profiles")
    phase_assignments = normalized_phase_assignments(manifest, worker_config) if isinstance(worker_config, dict) else {}
    if isinstance(worker_config, dict) and phase_assignments:
        entry["worker_profiles"] = {
            "mode": str(worker_config.get("mode", "auto-when-supported")),
            "extends": str(worker_config.get("extends", "portable-default")),
            "max_parallel_workers": worker_config.get("max_parallel_workers", 1),
            "phase_assignments": phase_assignments,
        }
    return entry


def build_registry_data_with_options(
    root: Path, *, use_local_ai: bool = True, check_local_ai: bool = False
) -> dict[str, Any]:
    automations = [
        build_entry(root, module_dir)
        for module_dir in discover_automation_dirs(root)
    ]
    automations.sort(key=lambda item: item["id"])
    if use_local_ai:
        apply_local_ai_routes(root, automations, check=check_local_ai)
    return {
        "version": ROUTING_VERSION,
        "source_root": "automations",
        "automations": automations,
    }


def local_ai_item_for_automation(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "name": item["id"],
        "task": "workflow-routing",
        "summary": item["summary"],
        "source_paths": [
            f"{item['folder']}/{item['start_file']}",
            f"{item['folder']}/{item.get('contract_file', 'module.json')}",
        ],
        "related_skills": item.get("related_skills", []),
        "scripts": item.get("scripts", []),
        "outputs": item.get("outputs", []),
    }


def load_local_ai_routing(root: Path) -> Any | None:
    helper_path = (
        root
        / ".agents"
        / "skills"
        / "local-ai-helper"
        / "scripts"
        / "local_ai_routing.py"
    )
    if not helper_path.exists():
        return None
    spec = importlib.util.spec_from_file_location("repo_local_ai_routing", helper_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def apply_local_ai_routes(
    root: Path, automations: list[dict[str, Any]], *, check: bool
) -> dict[str, Any]:
    if not automations:
        return {"status": "disabled", "check_failed": False, "issues": [], "items": {}}
    local_ai_routing = load_local_ai_routing(root)
    if local_ai_routing is None:
        return {
            "status": "fallback",
            "check_failed": False,
            "issues": ["Local AI helper is not available."],
            "items": {},
        }
    items = [local_ai_item_for_automation(item) for item in automations]
    result = local_ai_routing.route_items(
        root,
        "workflow-routing",
        items,
        allowed_categories=[],
        check=check,
    )
    routed_items = result.get("items", {})
    if isinstance(routed_items, dict):
        for item in automations:
            routed = routed_items.get(item["id"])
            if not isinstance(routed, dict) or not routed.get("accepted"):
                continue
            fields = routed.get("fields", {})
            if not isinstance(fields, dict):
                continue
            summary = fields.get("summary")
            if isinstance(summary, str) and summary.strip():
                item["summary"] = summary.strip()
    return result


def build_registry_data(
    root: Path, *, use_local_ai: bool = True, check_local_ai: bool = False
) -> dict[str, Any]:
    return build_registry_data_with_options(
        root, use_local_ai=use_local_ai, check_local_ai=check_local_ai
    )


def render_markdown(data: dict[str, Any]) -> str:
    lines = [
        "<!-- Generated by workflow-manager sync_automation_routing.py. Do not edit by hand. -->",
        "",
        "# Workflow Routing Index",
        "",
        "Use this file only to choose which workflow to open. Open one matching start file; do not load all workflow folders.",
        "",
        "`registry.json` is generated from module.json contracts and contains full workflow metadata for scripts and checks.",
        "",
        f"- Source root: `{data['source_root']}`",
        f"- Index schema version: `{data['version']}`",
    ]

    automations = data["automations"]
    if not automations:
        lines.extend(["", "No automation workflow modules found."])
        return "\n".join(lines)

    lines.extend(
        [
            "",
            "| Workflow | Use When | Open | Contract |",
            "|---|---|---|---|",
        ]
    )
    for item in automations:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{item['id']}`",
                    compact_summary(str(item["summary"])).replace("|", "\\|"),
                    f"`{item['folder']}/{item['start_file']}`",
                    f"`{item['folder']}/{item.get('contract_file', 'module.json')}`",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def expected_outputs(root: Path, data: dict[str, Any]) -> dict[Path, str]:
    return {
        root / "automations" / "routing.md": render_markdown(data) + "\n",
        root / "automations" / "registry.json": json.dumps(data, indent=2, sort_keys=True) + "\n",
        root
        / ".agents"
        / "skills"
        / "workflow-manager"
        / "assets"
        / "schemas"
        / "context-packet.schema.json": json.dumps(
            context_packet_schema(), indent=2, sort_keys=True
        )
        + "\n",
    }


def sync_automation_routing(root: Path, check: bool) -> int:
    errors, _warnings, _modules = validate_automations(root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    data = build_registry_data(root, use_local_ai=False)
    outputs = expected_outputs(root, data)

    if check:
        stale: list[Path] = []
        for path, expected in outputs.items():
            if not path.exists():
                stale.append(path)
                continue
            actual = path.read_text(encoding="utf-8").replace("\r\n", "\n")
            if actual != expected:
                stale.append(path)
        if stale:
            for path in stale:
                print(f"ERROR: {common.relative(root, path)} is missing or stale.", file=sys.stderr)
            print(
                "Run: python -B .agents/manage.py "
                "sync-automation-routing",
                file=sys.stderr,
            )
            return 1
        print("Automation routing and registry are in sync.")
        return 0

    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"Generated {common.relative(root, path)}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="repository root; defaults to the script parent repository")
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if generated workflow routing, registry, or context schema is stale",
    )
    return parser


def main() -> int:
    common.require_supported_python()
    args = build_parser().parse_args()
    root = Path(args.root).expanduser().resolve() if args.root else default_root()
    return sync_automation_routing(root, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
