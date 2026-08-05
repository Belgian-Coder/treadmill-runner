#!/usr/bin/env python3
"""Generate a compact paid-model prompt packet for benchmark workflow runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import benchmark_common as common


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_root_from(path: Path) -> Path:
    for candidate in [path, *path.parents]:
        if (candidate / ".agents" / "manage.py").exists():
            return candidate
    return Path.cwd()


def rel_path(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def measure(path: Path, root: Path, role: str) -> dict[str, Any]:
    text = common.read_text(path)
    return {
        "role": role,
        "path": rel_path(path, root),
        "exists": path.exists(),
        "bytes": path.stat().st_size if path.exists() else 0,
        "sha256": sha256_file(path) if path.exists() else "",
        "tokens": common.estimate_tokens(text),
    }


def load_feature_card(path: Path) -> dict[str, Any]:
    raw = common.read_json(path)
    if not isinstance(raw, dict) or raw.get("tool") != "agent-benchmarking.benchmark-feature-card":
        raise SystemExit(f"feature card JSON has unexpected shape: {path}")
    return raw


def local_ai_summary(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "enabled": False,
            "status": "not-used",
            "summary": "No local-AI advisory was used for this packet.",
            "findings": [],
            "evidence": [],
        }
    data = common.read_json(path)
    if not isinstance(data, dict):
        raise SystemExit(f"local-AI output must be a JSON object: {path}")
    return {
        "enabled": True,
        "status": "ok" if data.get("ok") is True else str(data.get("status", "unknown")),
        "summary": str(data.get("summary", "")),
        "findings": [str(item) for item in data.get("findings", []) if str(item).strip()][:6],
        "evidence": [
            {
                "source": str(item.get("source", "")),
                "excerpt": str(item.get("excerpt", ""))[:220],
            }
            for item in data.get("evidence", [])
            if isinstance(item, dict)
        ][:4],
    }


def render_packet(
    *,
    feature_card: dict[str, Any],
    acceptance: list[str],
    local_ai: dict[str, Any],
    run_id: str,
    workflow_name: str,
    packet_profile: str,
) -> str:
    suite = feature_card["suite"]
    lines = [
        "# Benchmark Prompt Packet",
        "",
        f"- Workflow: `{workflow_name}`",
        f"- Run: `{run_id}`",
        f"- Local AI advisory: `{local_ai['status']}`",
        f"- Suite: `{suite['suite_id']}`",
        f"- Prompt version: `{suite.get('prompt_version', '')}`",
        f"- Story hash: `{suite.get('story_hash', '')}`",
        "",
        "## Workflow Gate Authority",
        "",
        f"- Run `python -B .agents/manage.py workflow plan-check --name {workflow_name} --run-id {run_id} --format json` before treating the plan as accepted.",
        "- Deterministic validators and workflow gates outrank local-AI advisory output.",
        "- Reopen source files when changing benchmark tooling, debugging failed validators, or auditing command implementation details.",
        "",
        "## Required Output Docs",
        "",
        "- `ticket-info.md`",
        "- `plan.md`",
        "- `REPORT.md`",
        "- `execution-log.md`",
        "",
        "## Acceptance Criteria",
        "",
    ]
    for item in acceptance:
        lines.append(f"- {item}")
    if packet_profile == "micro":
        lines = [
            "# Benchmark Micro Packet",
            "",
            f"- Workflow/run: `{workflow_name}` / `{run_id}`",
            f"- Suite: `{suite['suite_id']}`; prompt `{suite.get('prompt_version', '')}`; story `{suite.get('story_hash', '')}`",
            f"- Local AI: `{local_ai['status']}`",
            "",
            "## Workflow Gate Authority",
            "",
            f"- Plan check: `python -B .agents/manage.py workflow plan-check --name {workflow_name} --run-id {run_id} --format json`",
            "- Deterministic gates outrank advisory output; reopen source before editing tooling, validators, or failed checks.",
            "",
            "## Tasks",
            "",
        ]
        for task in suite["tasks"]:
            prompt = task.get("prompt") or task.get("summary", "")
            lines.append(f"- Task `{task['id']}`: {prompt}")
            if task.get("required_files"):
                lines.append("  Required files: " + ", ".join(task["required_files"]))
        lines.extend(["", "## Validators", ""])
        for item in suite["validators"]:
            lines.append(f"- {item}")
        if local_ai["enabled"]:
            lines.extend(["", "## Local AI Advisory", ""])
            lines.append(f"- Summary: {local_ai['summary']}")
            if local_ai["findings"]:
                lines.append("- Findings: " + "; ".join(local_ai["findings"]))
        lines.extend(["", "## Minimal Source Fingerprints", ""])
        source_fingerprints = feature_card.get("source_fingerprints", {})
        suite_source = source_fingerprints.get("suite", {})
        if isinstance(suite_source, dict):
            lines.append(f"- Suite `{suite_source.get('path', '')}`: sha256 `{suite_source.get('sha256', '')}`")
        for item in source_fingerprints.get("verifier_references", []):
            lines.append(f"- Verifier `{item.get('path', '')}`: sha256 `{item.get('sha256', '')}`")
        lines.extend(["", "## Reopen Source When", ""])
        for item in feature_card["replacement_policy"]["must_load_source_when"]:
            lines.append(f"- {item}")
        return "\n".join(lines).rstrip() + "\n"
    if packet_profile == "condensed":
        lines.extend(["", "## Condensed Task Contract", ""])
        lines.append("- Use the fixed suite identity and hashes above as the comparison contract.")
        lines.append("- Preserve the required output docs and run the workflow gate before approval.")
        for task in suite["tasks"]:
            lines.append(f"- Task `{task['id']}`: {task.get('prompt', task.get('summary', ''))}")
            if task.get("required_files"):
                lines.append("- Required files: " + ", ".join(task["required_files"]))
        reference = suite.get("reference_fixture", {})
        if reference:
            lines.append(f"- Reference fixture hash: `{reference.get('fixture_hash', '')}`")
        lines.extend(["", "## Validators", ""])
        for item in suite["validators"]:
            lines.append(f"- {item}")
        lines.extend(["", "## Local AI Advisory", ""])
        lines.append(f"- Enabled: {str(local_ai['enabled']).lower()}")
        lines.append(f"- Summary: {local_ai['summary']}")
        if local_ai["findings"]:
            lines.append("- Findings: " + "; ".join(local_ai["findings"]))
        lines.extend(["", "## Minimal Source Fingerprints", ""])
        source_fingerprints = feature_card.get("source_fingerprints", {})
        suite_source = source_fingerprints.get("suite", {})
        if isinstance(suite_source, dict):
            lines.append(
                f"- Suite `{suite_source.get('path', '')}`: sha256 `{suite_source.get('sha256', '')}`, "
                f"{suite_source.get('tokens', 0)} source tokens"
            )
        for item in source_fingerprints.get("verifier_references", []):
            lines.append(
                f"- Verifier `{item.get('path', '')}`: sha256 `{item.get('sha256', '')}`, "
                f"{item.get('tokens', 0)} source tokens"
            )
        lines.extend(["", "## Reopen Source When", ""])
        for item in feature_card["replacement_policy"]["must_load_source_when"]:
            lines.append(f"- {item}")
        return "\n".join(lines).rstrip() + "\n"
    story = suite.get("project_story", {})
    if story:
        lines.extend(["", "## Fixed Story", ""])
        lines.append(f"- Title: {story.get('title', '')}")
        lines.append(f"- Need: {story.get('need', '')}")
        for item in story.get("acceptance_criteria", [])[:12]:
            lines.append(f"- Acceptance: {item}")
    architecture = suite.get("architecture", {})
    if architecture:
        lines.extend(["", "## Architecture Requirements", ""])
        for item in architecture.get("requirements", [])[:12]:
            lines.append(f"- {item}")
        slices = ", ".join(architecture.get("slices", []))
        if slices:
            lines.append(f"- Slices: {slices}")
    lines.extend(["", "## Benchmark Tasks", "", "| Task | Files | Summary |", "|---|---|---|"])
    for task in suite["tasks"]:
        files = ", ".join(task.get("required_files", []))
        lines.append(f"| `{task['id']}` | {files} | {task.get('summary', '')} |")
        if task.get("prompt"):
            lines.extend(["", f"### Prompt: `{task['id']}`", "", task["prompt"]])
        if task.get("expected_checks"):
            lines.extend(["", f"### Expected Checks: `{task['id']}`", ""])
            for check in task["expected_checks"]:
                lines.append(f"- {check}")
    reference = suite.get("reference_fixture", {})
    if reference:
        lines.extend(["", "## Reference Fixture Boundary", ""])
        lines.append(f"- Generator: `{reference.get('generator', '')}`")
        lines.append(f"- Fixture hash: `{reference.get('fixture_hash', '')}`")
        lines.append(f"- Allowed in prompt context: `{reference.get('allowed_in_prompt_context', '')}`")
    run_pair = suite.get("run_pair_contract", {})
    if run_pair:
        lines.extend(["", "## Run Pair Contract", ""])
        lines.append("- Compare both local-AI modes for every variant under the same pair id.")
        for item in run_pair.get("same_for_both_modes", [])[:12]:
            lines.append(f"- Same for both modes: {item}")
    lines.extend(["", "## Validators", ""])
    for item in suite["validators"]:
        lines.append(f"- {item}")
    if suite.get("validation_commands"):
        lines.extend(["", "## Validation Commands", ""])
        for command in suite["validation_commands"]:
            lines.append(f"- `{command}`")
    lines.extend(["", "## Quality Boundary", ""])
    for item in feature_card["quality_boundary"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Token Boundary", ""])
    for item in feature_card["token_accounting_boundary"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Local AI Advisory", ""])
    lines.append(f"- Enabled: {str(local_ai['enabled']).lower()}")
    lines.append(f"- Summary: {local_ai['summary']}")
    if local_ai["findings"]:
        lines.append("- Findings: " + "; ".join(local_ai["findings"]))
    lines.extend(["", "## Source Fingerprints", ""])
    source_fingerprints = feature_card.get("source_fingerprints", {})
    for role, value in source_fingerprints.items():
        if isinstance(value, list):
            for item in value:
                lines.append(
                    f"- `{item.get('path', '')}` ({item.get('role', role)}): "
                    f"{item.get('tokens', 0)} tokens, sha256 `{item.get('sha256', '')}`"
                )
        elif isinstance(value, dict):
            lines.append(
                f"- `{value.get('path', '')}` ({value.get('role', role)}): "
                f"{value.get('tokens', 0)} tokens, sha256 `{value.get('sha256', '')}`"
            )
    lines.extend(["", "## Reopen Source When", ""])
    for item in feature_card["replacement_policy"]["must_load_source_when"]:
        lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def write_prompt_packet(
    *,
    feature_card_path: Path,
    output_root: Path,
    run_id: str,
    acceptance: list[str],
    replace_paths: list[Path],
    local_ai_output: Path | None = None,
    workflow_name: str = "user-story-workflow",
    packet_profile: str = "full",
    allow_existing: bool = False,
) -> dict[str, Any]:
    feature_card_path = feature_card_path.expanduser().resolve()
    root = repo_root_from(feature_card_path)
    output_root = output_root.expanduser().resolve()
    packet_root = output_root / run_id
    if packet_root.exists() and any(packet_root.iterdir()) and not allow_existing:
        raise SystemExit(f"prompt-packet folder already exists and is not empty: {packet_root}")
    packet_root.mkdir(parents=True, exist_ok=True)
    feature_card = load_feature_card(feature_card_path)
    local_ai = local_ai_summary(local_ai_output.expanduser().resolve() if local_ai_output else None)
    packet_text = render_packet(
        feature_card=feature_card,
        acceptance=acceptance,
        local_ai=local_ai,
        run_id=run_id,
        workflow_name=workflow_name,
        packet_profile=packet_profile,
    )
    packet_path = packet_root / "prompt-packet.md"
    packet_json_path = packet_root / "prompt-packet.json"
    common.write_text(packet_path, packet_text)
    common.write_json(
        packet_json_path,
        {
            "schema_version": 1,
            "tool": "agent-benchmarking.benchmark-prompt-packet",
            "run_id": run_id,
            "workflow": workflow_name,
            "packet_profile": packet_profile,
            "workflow_gate_authority": {
                "plan_check_command": f"python -B .agents/manage.py workflow plan-check --name {workflow_name} --run-id {run_id} --format json",
                "deterministic_validators_are_authoritative": True,
                "local_ai_is_advisory_only": True,
            },
            "local_ai": local_ai,
            "suite": feature_card["suite"],
            "quality_boundary": feature_card["quality_boundary"],
            "token_accounting_boundary": feature_card["token_accounting_boundary"],
            "replacement_policy": feature_card.get("replacement_policy", {}),
            "source_fingerprints": feature_card.get("source_fingerprints", {}),
            "acceptance": acceptance,
        },
    )
    resolved_replace = [(root / path).resolve() if not path.is_absolute() else path.resolve() for path in replace_paths]
    replaced = [measure(path, root, "replaced-paid-context") for path in resolved_replace]
    packet_files = [
        measure(packet_path, packet_root, "prompt-packet"),
        measure(packet_json_path, packet_root, "prompt-packet"),
    ]
    packet_tokens = sum(item["tokens"] for item in packet_files)
    replaced_tokens = sum(item["tokens"] for item in replaced)
    summary = {
        "schema_version": 1,
        "tool": "agent-benchmarking.benchmark-prompt-packet",
        "ok": True,
        "status": "written",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "workflow": workflow_name,
        "packet_profile": packet_profile,
        "local_ai_enabled": bool(local_ai["enabled"]),
        "suite": feature_card["suite"],
        "prompt_packet_folder": str(packet_root),
        "prompt_packet_files": packet_files,
        "replaced_paid_context": replaced,
        "token_counter": common.token_count_metadata(),
        "tokens": {
            "prompt_packet": packet_tokens,
            "prompt_packet_markdown": packet_files[0]["tokens"],
            "replaced_paid_context": replaced_tokens,
            "saved_if_packet_replaces_context": max(0, replaced_tokens - packet_tokens),
            "saved_percent": round(((replaced_tokens - packet_tokens) / replaced_tokens) * 100, 2)
            if replaced_tokens
            else 0,
        },
        "quality_boundary": feature_card["quality_boundary"],
        "token_accounting_boundary": feature_card["token_accounting_boundary"],
        "replacement_policy": feature_card.get("replacement_policy", {}),
        "source_fingerprints": feature_card.get("source_fingerprints", {}),
    }
    common.write_json(packet_root / "summary.json", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-card", required=True, help="feature-card JSON path")
    parser.add_argument("--output-root", required=True, help="folder that receives the prompt-packet run folder")
    parser.add_argument("--run-id", required=True, help="prompt-packet run folder name")
    parser.add_argument("--acceptance", action="append", default=[], help="acceptance criterion to include")
    parser.add_argument("--replace-path", action="append", default=[], help="paid-context path this packet replaces")
    parser.add_argument("--local-ai-output", default="", help="optional local-AI advisory JSON")
    parser.add_argument("--workflow-name", default="user-story-workflow")
    parser.add_argument("--packet-profile", choices=("full", "condensed", "micro"), default="full")
    parser.add_argument("--allow-existing", action="store_true")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown", dest="output_format")
    return parser


def main(argv: list[str] | None = None) -> int:
    common.require_supported_python()
    args = build_parser().parse_args(argv)
    summary = write_prompt_packet(
        feature_card_path=Path(args.feature_card),
        output_root=Path(args.output_root),
        run_id=args.run_id,
        acceptance=[str(item) for item in args.acceptance],
        replace_paths=[Path(item) for item in args.replace_path],
        local_ai_output=Path(args.local_ai_output) if args.local_ai_output else None,
        workflow_name=args.workflow_name,
        packet_profile=args.packet_profile,
        allow_existing=args.allow_existing,
    )
    if args.output_format == "json":
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            f"# Benchmark Prompt Packet\n\n"
            f"- Run: `{summary['run_id']}`\n"
            f"- Local AI enabled: {str(summary['local_ai_enabled']).lower()}\n"
            f"- Prompt packet tokens: {summary['tokens']['prompt_packet']}\n"
            f"- Replaced context tokens: {summary['tokens']['replaced_paid_context']}\n"
            f"- Saved if used: {summary['tokens']['saved_if_packet_replaces_context']} "
            f"({summary['tokens']['saved_percent']}%)\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
