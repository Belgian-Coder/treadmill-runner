#!/usr/bin/env python3
"""Generate a compact benchmark feature card for low-token workflow runs."""

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


def source_measure(path: Path, root: Path, role: str) -> dict[str, Any]:
    text = common.read_text(path)
    return {
        "role": role,
        "path": rel_path(path, root),
        "exists": path.exists(),
        "bytes": path.stat().st_size if path.exists() else 0,
        "sha256": sha256_file(path) if path.exists() else "",
        "tokens": common.estimate_tokens(text),
    }


def normalize_tasks(suite: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in common.task_list(suite):
        row: dict[str, Any] = {
            "id": str(task.get("id", "")),
            "title": str(task.get("title", task.get("id", ""))),
            "language": str(task.get("language", "")),
            "prompt_version": str(task.get("prompt_version", suite.get("prompt_version", ""))),
            "prompt": str(task.get("prompt", "")),
            "required_files": common.as_string_list(task.get("required_files"), "required_files"),
            "expected_checks": common.as_string_list(task.get("expected_checks"), "expected_checks"),
            "features": common.as_string_list(task.get("features"), "features"),
            "summary": str(task.get("summary", "")),
            "output_contract": str(task.get("output_contract", "")),
        }
        for optional_key in ("minimum_test_attributes", "minimum_tests"):
            if optional_key in task:
                row[optional_key] = task[optional_key]
        rows.append(row)
    return rows


def build_card(
    *,
    suite_path: Path,
    replace_paths: list[Path],
    verifier_paths: list[Path],
    workflow_name: str,
    root: Path,
) -> dict[str, Any]:
    suite = common.load_suite(suite_path)
    suite_measure = source_measure(suite_path, root, "source-suite")
    replaced = [source_measure(path, root, "replaced-paid-context") for path in replace_paths]
    verifiers = [source_measure(path, root, "verifier-reference") for path in verifier_paths]
    card = {
        "schema_version": 1,
        "tool": "agent-benchmarking.benchmark-feature-card",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workflow": workflow_name,
        "suite": {
            "suite_id": suite.get("suite_id") or suite.get("suite") or suite_path.stem,
            "version": str(suite.get("version", "")),
            "prompt_version": str(suite.get("prompt_version", "")),
            "story_hash": str(suite.get("story_hash", "")),
            "description": str(suite.get("description", "")),
            "output_contract": suite.get("output_contract", {}),
            "runtime_contract": suite.get("runtime_contract", {}),
            "dotnet": suite.get("dotnet", {}),
            "project_story": suite.get("project_story", {}),
            "architecture": suite.get("architecture", {}),
            "data_model": suite.get("data_model", {}),
            "api_contract": suite.get("api_contract", []),
            "reference_fixture": suite.get("reference_fixture", {}),
            "run_pair_contract": suite.get("run_pair_contract", {}),
            "validators": common.as_string_list(suite.get("validators"), "validators"),
            "validation_commands": common.as_string_list(suite.get("validation_commands"), "validation_commands"),
            "tasks": normalize_tasks(suite),
        },
        "quality_boundary": [
            "The suite, output contract, task ids, required files, runtime contract, and validator summaries are unchanged.",
            "The card may replace broad paid-model reads when the agent is planning or reporting benchmark use.",
            "Agents must still run deterministic validators or workflow gates before claiming benchmark quality.",
            "Agents must reopen source scripts when changing benchmark implementation, debugging verifier internals, or investigating a failed validator.",
        ],
        "token_accounting_boundary": [
            "Token savings are artifact measurements over saved files.",
            "Use exact tiktoken counts with the o200k_base encoding when tiktoken is installed.",
            "When tiktoken is unavailable, report the explicit estimated_chars_div_4 fallback.",
            "Provider billing telemetry, hidden orchestration prompts, and full end-to-end agent wall-clock are not measured by local artifacts.",
            "Compare input, output, total, local-AI artifact tokens, and measured command time separately.",
        ],
        "replacement_policy": {
            "intended_use": "low-token user-story-workflow planning and benchmark handoff",
            "safe_to_replace": [item["path"] for item in replaced],
            "must_load_source_when": [
                "editing benchmark scripts",
                "changing validators",
                "debugging validator failures",
                "auditing command implementation details",
            ],
        },
        "source_fingerprints": {
            "suite": suite_measure,
            "verifier_references": verifiers,
            "replaced_paid_context": replaced,
        },
        "token_counter": common.token_count_metadata(),
    }
    return card


def render_markdown(card: dict[str, Any]) -> str:
    suite = card["suite"]
    lines = [
        "# Benchmark Feature Card",
        "",
        f"- Workflow: `{card['workflow']}`",
        f"- Suite: `{suite['suite_id']}`",
        f"- Description: {suite['description']}",
        f"- Token counter: `{card['token_counter']['method']}`",
        "",
        "## Tasks",
        "",
        "| Task | Language | Required Files | Summary |",
        "|---|---|---|---|",
    ]
    for task in suite["tasks"]:
        files = ", ".join(task["required_files"])
        lines.append(f"| `{task['id']}` | {task['language']} | {files} | {task['summary']} |")
    story = suite.get("project_story", {})
    if story:
        lines.extend(
            [
                "",
                "## Fixed Story",
                "",
                f"- Title: {story.get('title', '')}",
                f"- Need: {story.get('need', '')}",
            ]
        )
        for item in story.get("acceptance_criteria", [])[:12]:
            lines.append(f"- Acceptance: {item}")
    architecture = suite.get("architecture", {})
    if architecture:
        lines.extend(["", "## Architecture", ""])
        for item in architecture.get("requirements", [])[:12]:
            lines.append(f"- {item}")
        slices = ", ".join(architecture.get("slices", []))
        if slices:
            lines.append(f"- Required slices: {slices}")
    run_pair = suite.get("run_pair_contract", {})
    if run_pair:
        lines.extend(["", "## Run Pair Contract", ""])
        modes = ", ".join(run_pair.get("local_ai_modes", []))
        variants = ", ".join(run_pair.get("variants", []))
        lines.append(f"- Local-AI modes: {modes}")
        lines.append(f"- Variants: {variants}")
        for item in run_pair.get("same_for_both_modes", [])[:12]:
            lines.append(f"- Same for both modes: {item}")
    reference = suite.get("reference_fixture", {})
    if reference:
        lines.extend(["", "## Reference Fixture", ""])
        for key in ("generator", "solution_file", "fixture_hash", "allowed_in_prompt_context"):
            if key in reference:
                lines.append(f"- `{key}`: `{reference[key]}`")
    lines.extend(["", "## Validators", ""])
    for validator in suite["validators"]:
        lines.append(f"- {validator}")
    if suite.get("validation_commands"):
        lines.extend(["", "## Validation Commands", ""])
        for command in suite["validation_commands"]:
            lines.append(f"- `{command}`")
    lines.extend(["", "## Quality Boundary", ""])
    for item in card["quality_boundary"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Token Accounting Boundary", ""])
    for item in card["token_accounting_boundary"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Replaceable Paid Context", ""])
    for item in card["source_fingerprints"]["replaced_paid_context"]:
        lines.append(f"- `{item['path']}`: {item['tokens']} tokens, sha256 `{item['sha256']}`")
    return "\n".join(lines).rstrip() + "\n"


def write_feature_card(
    *,
    suite_path: Path,
    output_root: Path,
    run_id: str,
    replace_paths: list[Path],
    verifier_paths: list[Path],
    workflow_name: str = "user-story-workflow",
    allow_existing: bool = False,
) -> dict[str, Any]:
    suite_path = suite_path.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    root = repo_root_from(suite_path)
    card_root = output_root / run_id
    if card_root.exists() and any(card_root.iterdir()) and not allow_existing:
        raise SystemExit(f"feature-card folder already exists and is not empty: {card_root}")
    card_root.mkdir(parents=True, exist_ok=True)
    resolved_replace = [(root / path).resolve() if not path.is_absolute() else path.resolve() for path in replace_paths]
    resolved_verifiers = [(root / path).resolve() if not path.is_absolute() else path.resolve() for path in verifier_paths]
    card = build_card(
        suite_path=suite_path,
        replace_paths=resolved_replace,
        verifier_paths=resolved_verifiers,
        workflow_name=workflow_name,
        root=root,
    )
    common.write_json(card_root / "feature-card.json", card)
    common.write_text(card_root / "feature-card.md", render_markdown(card))
    card_files = [
        source_measure(card_root / "feature-card.md", card_root, "feature-card"),
        source_measure(card_root / "feature-card.json", card_root, "feature-card"),
    ]
    replaced_tokens = sum(item["tokens"] for item in card["source_fingerprints"]["replaced_paid_context"])
    card_tokens = sum(item["tokens"] for item in card_files)
    summary = {
        "schema_version": 1,
        "tool": "agent-benchmarking.benchmark-feature-card",
        "ok": True,
        "status": "written",
        "run_id": run_id,
        "feature_card_folder": str(card_root),
        "feature_card_files": card_files,
        "replaced_paid_context": card["source_fingerprints"]["replaced_paid_context"],
        "token_counter": card["token_counter"],
        "tokens": {
            "feature_card": card_tokens,
            "replaced_paid_context": replaced_tokens,
            "saved_if_card_replaces_context": max(0, replaced_tokens - card_tokens),
            "saved_percent": round(((replaced_tokens - card_tokens) / replaced_tokens) * 100, 2)
            if replaced_tokens
            else 0,
        },
        "quality_boundary": card["quality_boundary"],
        "token_accounting_boundary": card["token_accounting_boundary"],
    }
    common.write_json(card_root / "summary.json", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, help="benchmark suite JSON")
    parser.add_argument("--output-root", required=True, help="folder that receives the feature-card run folder")
    parser.add_argument("--run-id", required=True, help="feature-card run folder name")
    parser.add_argument("--replace-path", action="append", default=[], help="paid-context path the card can replace")
    parser.add_argument("--verifier-path", action="append", default=[], help="verifier path to fingerprint without loading")
    parser.add_argument("--workflow-name", default="user-story-workflow")
    parser.add_argument("--allow-existing", action="store_true")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown", dest="output_format")
    return parser


def main(argv: list[str] | None = None) -> int:
    common.require_supported_python()
    args = build_parser().parse_args(argv)
    summary = write_feature_card(
        suite_path=Path(args.suite),
        output_root=Path(args.output_root),
        run_id=args.run_id,
        replace_paths=[Path(item) for item in args.replace_path],
        verifier_paths=[Path(item) for item in args.verifier_path],
        workflow_name=args.workflow_name,
        allow_existing=args.allow_existing,
    )
    if args.output_format == "json":
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            f"# Benchmark Feature Card\n\n"
            f"- Run: `{summary['run_id']}`\n"
            f"- Feature-card tokens: {summary['tokens']['feature_card']}\n"
            f"- Replaced context tokens: {summary['tokens']['replaced_paid_context']}\n"
            f"- Saved if used: {summary['tokens']['saved_if_card_replaces_context']} "
            f"({summary['tokens']['saved_percent']}%)\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
