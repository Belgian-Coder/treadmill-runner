#!/usr/bin/env python3
"""Compact accepted-skill review command."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from repo_support import repo_common as repo
from repo_support import repo_policy
import validate_skill as validate_skill_module


def build_implementation_packet(
    root: Path,
    skill_path: Path,
    *,
    validation_warnings: list[str],
) -> dict[str, object]:
    skill_name = skill_path.name
    likely_files = [
        f".agents/skills/{skill_name}/SKILL.md",
        f".agents/skills/{skill_name}/module.json",
    ]
    for optional in ("docs", "scripts", "assets"):
        if (skill_path / optional).exists():
            likely_files.append(f".agents/skills/{skill_name}/{optional}/")
    expected_checks = [
        f"python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/{skill_name}",
        f"python -B .agents/manage.py review --skill .agents/skills/{skill_name} --plan",
        "python -B .agents/manage.py sync-skill-routing",
        "python -B .agents/manage.py sync-claude-skills",
        "python -B .agents/manage.py check",
    ]
    return {
        "purpose": "Execution-ready checklist for a substantial skill change; read-only and advisory.",
        "likely_files": likely_files,
        "expected_checks": expected_checks,
        "generated_artifacts": [
            ".agents/routing.md",
            ".agents/registry.json",
            ".claude/CLAUDE.md",
            f".claude/skills/{skill_name}/SKILL.md",
            ".github/copilot-instructions.md",
        ],
        "completion_evidence": [
            "fresh validate_skill.py output",
            "generated routing and adapter sync status",
            "repo check output or explicit blocker",
            "skipped checks with reasons",
        ],
        "two_stage_review": [
            "Stage 1: requested behavior/spec compliance against the user request and skill trigger.",
            "Stage 2: implementation quality, owner boundaries, validation coverage, and context budget.",
        ],
        "do_not_overbuild": [
            "Prefer updating this skill when the capability already fits the trigger.",
            "Do not create a skill for repo policy, one command, static docs, or workflow phase orchestration.",
            "Move long examples into docs and keep repeatable behavior in Python scripts.",
        ],
        "selective_tdd": [
            "Use test-first fixtures for behavior/script changes when cheap and meaningful.",
            "Use fresh validation evidence for docs, metadata, routing, generated files, and config.",
        ],
        "active_warnings": validation_warnings,
        "source_path": repo.relative(root, skill_path),
    }


def summarize_review_report(report: dict[str, object], *, compact: bool = False) -> dict[str, object]:
    validation = report.get("validation") if isinstance(report.get("validation"), dict) else {}
    errors = validation.get("errors") if isinstance(validation.get("errors"), list) else []
    warnings = validation.get("warnings") if isinstance(validation.get("warnings"), list) else []
    failures = report.get("failures") if isinstance(report.get("failures"), list) else []
    inspect_report = report.get("inspect") if isinstance(report.get("inspect"), dict) else {}
    analysis = inspect_report.get("analysis") if isinstance(inspect_report.get("analysis"), dict) else {}
    impact = (
        inspect_report.get("context_budget_impact")
        if isinstance(inspect_report.get("context_budget_impact"), dict)
        else {}
    )
    budget = report.get("budget") if isinstance(report.get("budget"), dict) else {}
    skill_md = budget.get("skill_md") if isinstance(budget.get("skill_md"), dict) else {}
    inventory = report.get("inventory") if isinstance(report.get("inventory"), dict) else {}
    risk = inventory.get("risk") if isinstance(inventory.get("risk"), dict) else {}
    summary: dict[str, object] = {
        "schema_version": report.get("schema_version", 1),
        "tool": "skill-manager.review-skill-summary",
        "ok": bool(report.get("ok", False)),
        "skill": report.get("skill", ""),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "failure_count": len(failures),
        "validation": {"errors": errors, "warnings": warnings},
        "inspection": {
            "mode": inspect_report.get("mode", ""),
            "files_scanned": analysis.get("files_scanned", 0),
            "evidence_count": analysis.get("evidence_count", 0),
        },
        "context_budget": {
            "skill_md_words": impact.get("skill_md_words", skill_md.get("words", 0)),
            "skill_md_status": impact.get("skill_md_status", skill_md.get("status", "")),
            "routing_load_words": impact.get("routing_load_words", 0),
        },
        "risk": {
            "profile": risk.get("profile", ""),
            "declared_flags": risk.get("declared_flags", []),
        },
        "failures": failures,
    }
    if compact:
        if not errors and not warnings:
            summary.pop("validation", None)
        if not failures:
            summary.pop("failures", None)
        if not summary["risk"]["declared_flags"]:
            summary["risk"].pop("declared_flags", None)
    packet = report.get("implementation_packet")
    if isinstance(packet, dict):
        likely_files = packet.get("likely_files") if isinstance(packet.get("likely_files"), list) else []
        expected_checks = packet.get("expected_checks") if isinstance(packet.get("expected_checks"), list) else []
        summary["implementation_packet"] = {
            "likely_file_count": len(likely_files),
            "expected_check_count": len(expected_checks),
            "likely_files": likely_files if not compact else likely_files[:4],
            "expected_checks": expected_checks if not compact else expected_checks[:4],
        }
    if not compact:
        summary["recommended_next_steps"] = inspect_report.get("recommended_next_steps", [])
    return summary


def review_skill(args: argparse.Namespace, root: Path) -> int:
    skill_path = Path(args.skill).expanduser()
    if not skill_path.is_absolute():
        skill_path = root / skill_path
    commands = {
        "inspect": ["inspect-skill", "--skill", str(skill_path), "--fast", "--format", "json"],
        "budget": ["measure-skill-budget", "--skill", str(skill_path), "--format", "json"],
        "inventory": ["skill-inventory", "--skill", str(skill_path), "--format", "json"],
    }
    results: dict[str, dict[str, object]] = {}
    failures: list[str] = []
    for name, command_args in commands.items():
        completed = subprocess.run(
            [sys.executable, "-B", str(root / ".agents" / "manage.py"), *command_args],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=repo.child_env(),
            check=False,
        )
        if completed.returncode != 0:
            excerpt_chars = repo_policy.int_value(root, "limits.output.failure_excerpt_chars")
            failures.append(f"{name} failed: {completed.stdout.strip()[:excerpt_chars]}")
            continue
        try:
            results[name] = json.loads(completed.stdout)
        except json.JSONDecodeError:
            failures.append(f"{name} did not return JSON")
    validation_errors, validation_warnings = validate_skill_module.validate_skill(skill_path)
    report = {
        "schema_version": 1,
        "tool": "skill-manager.review-skill",
        "ok": not validation_errors and not failures,
        "skill": repo.relative(root, skill_path),
        "validation": {"errors": validation_errors, "warnings": validation_warnings},
        "inspect": results.get("inspect", {}),
        "budget": results.get("budget", {}),
        "inventory": results.get("inventory", {}),
        "failures": failures,
    }
    if getattr(args, "plan", False):
        report["implementation_packet"] = build_implementation_packet(
            root,
            skill_path,
            validation_warnings=validation_warnings,
        )
    if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
        report = summarize_review_report(report, compact=bool(getattr(args, "compact", False)))
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("# Skill Review")
        print("")
        print(f"- Skill: `{report['skill']}`")
        print(f"- Status: {'passed' if report['ok'] else 'failed'}")
        if report.get("tool") == "skill-manager.review-skill-summary":
            print(f"- Validation errors: {report.get('error_count', 0)}")
            print(f"- Validation warnings: {report.get('warning_count', 0)}")
            print(f"- Failures: {report.get('failure_count', 0)}")
            return 0 if report["ok"] else 1
        print(f"- Validation errors: {len(validation_errors)}")
        print(f"- Validation warnings: {len(validation_warnings)}")
        inspect_report = report["inspect"] if isinstance(report["inspect"], dict) else {}
        impact = (
            inspect_report.get("context_budget_impact", {})
            if isinstance(inspect_report, dict)
            else {}
        )
        if isinstance(impact, dict):
            print(
                f"- Context budget: {impact.get('skill_md_words', 'unknown')} words "
                f"({impact.get('skill_md_status', 'unknown')})"
            )
            print(f"- Context recommendation: {impact.get('recommendation', '')}")
        if validation_warnings:
            print("")
            print("## Warnings")
            for warning in validation_warnings:
                print(f"- {warning}")
        if failures:
            print("")
            print("## Failures")
            for failure in failures:
                print(f"- {failure}")
        packet = report.get("implementation_packet")
        if isinstance(packet, dict):
            print("")
            print("## Implementation Packet")
            print(f"- Purpose: {packet.get('purpose')}")
            print("- Likely files:")
            for item in packet.get("likely_files", []):
                print(f"  - `{item}`")
            print("- Expected checks:")
            for item in packet.get("expected_checks", []):
                print(f"  - `{item}`")
            print("- Generated artifacts:")
            for item in packet.get("generated_artifacts", []):
                print(f"  - `{item}`")
            print("- Completion evidence:")
            for item in packet.get("completion_evidence", []):
                print(f"  - {item}")
            print("- Two-stage review:")
            for item in packet.get("two_stage_review", []):
                print(f"  - {item}")
    return 0 if report["ok"] else 1
