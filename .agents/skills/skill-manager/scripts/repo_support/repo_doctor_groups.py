#!/usr/bin/env python3
"""Grouped skill and workflow command adapters for the repository launcher."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from repo_support import repo_common as repo
from repo_support import repo_naming
from repo_support import repo_optimizations
from repo_support import repo_policy
from repo_support import repo_routing

skill_naming_report = repo_naming.skill_naming_report


def infer_workflow_name_for_run_id(root: Path, run_id: str) -> str:
    if not run_id or Path(run_id).is_absolute() or ".." in Path(run_id).parts:
        raise SystemExit("run id must be a safe workflow-local folder name")
    runs = sorted((root / "automations").glob(f"*/runs/{run_id}/run.json"))
    if not runs:
        raise SystemExit(f"workflow run not found: automations/*/runs/{run_id}/run.json")
    if len(runs) > 1:
        locations = ", ".join(repo.relative(root, path.parent) for path in runs)
        raise SystemExit(f"workflow run id is ambiguous across workflows: {locations}")
    return runs[0].parents[2].name


def skill_group(args: argparse.Namespace, root: Path, review_skill_func) -> int:
    if not args.skill_args:
        print("skill requires a subcommand: doctor, handoff, scorecard, eval-gap, route-audit, templates, or lessons", file=sys.stderr)
        return 2
    subcommand, *rest = args.skill_args
    if subcommand == "handoff":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py skill handoff")
        parser.add_argument("--skill", required=True)
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
        parsed = parser.parse_args(rest)
        report = repo_optimizations.skill_handoff_packet(root, parsed.skill)
        if parsed.summary:
            report = repo_optimizations.summarize_skill_handoff(report, compact=parsed.compact)
        if parsed.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(repo_optimizations.render_report(report, "Skill Handoff"), end="")
        return 0 if report.get("ok") else 1
    if subcommand == "scorecard":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py skill scorecard")
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--skill", action="append")
        target.add_argument("--all", action="store_true")
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
        parsed = parser.parse_args(rest)
        report = repo_optimizations.skill_scorecard(root, None if parsed.all else parsed.skill)
        if parsed.summary and parsed.compact:
            report = {k: v for k, v in report.items() if k != "skills" or report.get("ok") is not True}
        if parsed.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(repo_optimizations.render_report(report, "Skill Scorecard"), end="")
        return 0 if report.get("ok") else 1
    if subcommand == "eval-gap":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py skill eval-gap")
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--skill", action="append")
        target.add_argument("--all", action="store_true")
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
        parsed = parser.parse_args(rest)
        report = repo_optimizations.skill_eval_gap(root, None if parsed.all else parsed.skill)
        if parsed.summary:
            report = repo_optimizations.summarize_eval_gap(report, "skills", compact=parsed.compact)
        if parsed.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(repo_optimizations.render_report(report, "Skill Eval Gap"), end="")
        return 0 if report.get("ok") else 1
    if subcommand == "route-audit":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py skill route-audit")
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
        parsed = parser.parse_args(rest)
        report = repo_optimizations.routing_confidence_audit(root)
        if parsed.summary:
            report = repo_optimizations.summarize_route_audit(report, compact=parsed.compact)
        if parsed.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(repo_optimizations.render_report(report, "Routing Confidence Audit"), end="")
        return 0 if report.get("ok") else 1
    if subcommand == "templates":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py skill templates")
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
        parsed = parser.parse_args(rest)
        report = repo_optimizations.template_placeholder_scan(root)
        if parsed.summary:
            report = repo_optimizations.summarize_template_scan(report, compact=parsed.compact)
        if parsed.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(repo_optimizations.render_report(report, "Template Placeholder Scan"), end="")
        return 0 if report.get("ok") else 1
    if subcommand == "lessons":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py skill lessons")
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
        parsed = parser.parse_args(rest)
        report = repo_optimizations.lesson_promotion_queue(root)
        if parsed.summary:
            report = repo_optimizations.summarize_lesson_queue(report, compact=parsed.compact)
        if parsed.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(repo_optimizations.render_report(report, "Reusable Lesson Queue"), end="")
        return 0
    if subcommand != "doctor":
        print(f"unknown skill subcommand: {subcommand}", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog="python -B .agents/manage.py skill doctor")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--skill")
    target.add_argument("--all", action="store_true")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    parsed = parser.parse_args(rest)
    if parsed.all:
        skill_dirs = sorted((root / ".agents" / "skills").glob("*/SKILL.md"))
        rows = []
        for skill_md in skill_dirs:
            skill_dir = skill_md.parent
            word_count = len(skill_md.read_text(encoding="utf-8", errors="replace").split())
            manifest = skill_dir / "module.json"
            tests = skill_dir / "scripts" / "run_self_tests.py"
            naming = skill_naming_report(skill_dir)
            risk = "ok"
            if naming["warnings"]:
                risk = "naming"
            elif repo_policy.skill_word_status(root, word_count) in {"warn", "fail"}:
                risk = "budget"
            elif not tests.exists():
                risk = "missing-self-tests"
            rows.append(
                {
                    "skill": skill_dir.name,
                    "path": repo.relative(root, skill_dir),
                    "skill_md_words": word_count,
                    "has_manifest": manifest.exists(),
                    "has_self_tests": tests.exists(),
                    "naming": naming,
                    "risk": risk,
                    "next_command": f"python -B .agents/manage.py skill doctor --skill .agents/skills/{skill_dir.name}",
                }
            )
        report = {
            "schema_version": 1,
            "tool": "skill-manager.doctor-all",
            "ok": True,
            "status": "warning" if any(row["risk"] != "ok" for row in rows) else "ok",
            "summary": {
                "skills": len(rows),
                "risk_count": sum(1 for row in rows if row["risk"] != "ok"),
                "risks": [row for row in rows if row["risk"] != "ok"][:20],
            },
            "next_command": "python -B .agents/manage.py skill-inventory --all",
        }
        if not parsed.compact:
            report["skills"] = rows if not parsed.summary else rows[:20]
        if parsed.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print("# Skill Doctor Summary")
            print(f"- Skills: {len(rows)}")
            print(f"- Risks: {len(report['summary']['risks'])}")
            for row in report["summary"]["risks"]:
                print(f"- `{row['skill']}`: {row['risk']} - `{row['next_command']}`")
            unclear = [row for row in rows if row.get("naming", {}).get("warnings")]
            if unclear:
                print()
                print("## Naming Warnings")
                for row in unclear[:10]:
                    warnings = "; ".join(row.get("naming", {}).get("warnings", []))
                    print(f"- `{row['skill']}`: {warnings}")
            print(f"- Next command: `{report['next_command']}`")
        return 0
    review_args = argparse.Namespace(skill=parsed.skill, plan=True, output_format=parsed.output_format)
    return review_skill_func(review_args, root)


def workflow_start_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -B .agents/manage.py workflow start",
        description="write/run-state: start a workflow run by name or from a natural-language request.",
        epilog=(
            "Examples:\n"
            "  python -B .agents/manage.py which-workflow \"implement Azure DevOps user story 123\" --summary --compact --format json\n"
            "  python -B .agents/manage.py workflow start --from-request \"implement Azure DevOps user story 123\" --summary --compact --format json\n"
            "  python -B .agents/manage.py workflow start --name user-story-workflow --summary --compact --format json\n"
            "  python -B .agents/manage.py workflow start --name bug-ticket-workflow\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--name")
    target.add_argument("--from-request", help="route a natural-language request to a workflow and start it when confidence is high")
    parser.add_argument("--run-id")
    parser.add_argument("--from-ticket", help="repo-local ticket intake folder to attach as initial evidence")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--summary", action="store_true", help="emit compact agent-facing start packet")
    parser.add_argument("--compact", action="store_true", help="with --summary, omit verbose evidence payloads")
    return parser


def workflow_start_help() -> str:
    return workflow_start_parser().format_help()


def workflow_start_from_request_report(root: Path, request: str) -> dict[str, object]:
    route_report = repo_routing.explain_routes(
        root,
        request,
        kinds={"workflow"},
        tool_name="workflow-manager.start-from-request",
    )
    routes = route_report.get("routes") if isinstance(route_report.get("routes"), list) else []
    selected = route_report.get("selected_route") if isinstance(route_report.get("selected_route"), dict) else {}
    confidence = str(route_report.get("confidence") or "none")
    start_ready = bool(route_report.get("start_ready", False))
    status = "ready"
    issues: list[str] = []
    if not selected:
        status = "no-match"
        issues.append("no workflow route matched the request")
    elif not start_ready:
        status = "ambiguous"
        issues.append(
            f"workflow route confidence is {confidence}; the module-declared threshold and winner margin "
            "must be met before creating a run"
        )
    start_command = ""
    if status == "ready":
        start_command = (
            f"python -B .agents/manage.py workflow start --name {selected.get('name')} "
            "--summary --compact --format json"
        )
    return {
        "schema_version": 1,
        "tool": "workflow-manager.start-from-request",
        "ok": status == "ready",
        "status": status,
        "request": request,
        "selected_owner": route_report.get("selected_owner", ""),
        "selected_route": repo_routing.route_summary_row(selected, compact=True),
        "confidence": confidence,
        "issues": issues,
        "route_summary": repo_routing.summarize_route_report(route_report, compact=True),
        "routes": [repo_routing.route_summary_row(route, compact=True) for route in routes if isinstance(route, dict)],
        "next_command": start_command
        or f"python -B .agents/manage.py which-workflow {json.dumps(request)} --summary --compact --format json",
    }


def render_workflow_start_from_request(report: dict[str, object]) -> str:
    lines = ["# Workflow Start From Request", ""]
    lines.append(f"- Request: {report.get('request')}")
    lines.append(f"- Status: {report.get('status')}")
    lines.append(f"- Confidence: {report.get('confidence')}")
    if report.get("selected_owner"):
        lines.append(f"- Selected workflow: `{report.get('selected_owner')}`")
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {item}" for item in issues)
    routes = report.get("routes") if isinstance(report.get("routes"), list) else []
    if routes:
        lines.extend(["", "## Candidate Routes", ""])
        for route in routes[:5]:
            if isinstance(route, dict):
                lines.append(f"- `{route.get('name')}` score={route.get('score')} matches={', '.join(route.get('matched_terms', []))}")
    lines.append(f"- Next command: `{report.get('next_command')}`")
    return "\n".join(lines) + "\n"


def run_workflow_context_audit(root: Path, rest: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -B .agents/manage.py workflow context-audit")
    parser.add_argument("--name")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parsed = parser.parse_args(rest)
    workflow_name = parsed.name or infer_workflow_name_for_run_id(root, parsed.run_id)
    command = ["context-audit-run", "--root", str(root), "--name", workflow_name, "--format", parsed.format]
    command.extend(["--run-id", parsed.run_id])
    if parsed.summary:
        command.append("--summary")
    if parsed.compact:
        command.append("--compact")
    return repo.run_workflow_repo_manager(root, command)


def workflow_group(args: argparse.Namespace, root: Path) -> int:
    if not args.workflow_args:
        print(
            "workflow requires a subcommand: propose, recipes, create, adjust, doctor, eval, eval-gap, scorecard, smoke, analytics, workers, route-model, start, resume, context, context-audit, checkpoint, plan-check, template, metadata, integration-check, managed-section-diff, branch-policy, validation-packet, hooks, hook-audit, handoff, or finish",
            file=sys.stderr,
        )
        return 2
    subcommand, *rest = args.workflow_args
    if subcommand == "propose":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py workflow propose")
        parser.add_argument("--from-request", "--request", required=True, dest="from_request")
        parser.add_argument("--name", dest="workflow_name")
        parser.add_argument("--recipe")
        parser.add_argument("--profile", choices=("simple", "standard", "strict"), default="standard")
        parser.add_argument("--force-new", action="store_true")
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = [
            "propose-workflow",
            "--root",
            str(root),
            "--from-request",
            parsed.from_request,
            "--profile",
            parsed.profile,
            "--format",
            parsed.format,
        ]
        if parsed.workflow_name:
            command.extend(["--name", parsed.workflow_name])
        if parsed.recipe:
            command.extend(["--recipe", parsed.recipe])
        if parsed.force_new:
            command.append("--force-new")
        if parsed.summary:
            command.append("--summary")
        if parsed.compact:
            command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "recipes":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py workflow recipes")
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = ["workflow-recipes", "--root", str(root), "--format", parsed.format]
        if parsed.summary:
            command.append("--summary")
        if parsed.compact:
            command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "create":
        parser = argparse.ArgumentParser(
            prog="python -B .agents/manage.py workflow create",
            description="read-only by default; writes a workflow scaffold only with --write.",
        )
        parser.add_argument("--from-request", "--request", required=True, dest="from_request")
        parser.add_argument("--name", dest="workflow_name")
        parser.add_argument("--recipe")
        parser.add_argument("--profile", choices=("simple", "standard", "strict"), default="standard")
        parser.add_argument("--uses-skill", action="append", default=[])
        parser.add_argument("--uses-script", action="append", default=[])
        parser.add_argument("--write", action="store_true", help="write scaffold files under automations/<workflow-name>")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--force-new", action="store_true")
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = [
            "create-workflow-from-request",
            "--root",
            str(root),
            "--from-request",
            parsed.from_request,
            "--profile",
            parsed.profile,
            "--format",
            parsed.format,
        ]
        if parsed.workflow_name:
            command.extend(["--name", parsed.workflow_name])
        if parsed.recipe:
            command.extend(["--recipe", parsed.recipe])
        for skill in parsed.uses_skill:
            command.extend(["--uses-skill", skill])
        for script in parsed.uses_script:
            command.extend(["--uses-script", script])
        if parsed.write:
            command.append("--write")
        if parsed.force:
            command.append("--force")
        if parsed.force_new:
            command.append("--force-new")
        if parsed.summary:
            command.append("--summary")
        if parsed.compact:
            command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "adjust":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py workflow adjust")
        parser.add_argument("--name", required=True)
        parser.add_argument("--from-request", "--request", required=True, dest="from_request")
        parser.add_argument("--recipe")
        parser.add_argument("--profile", choices=("simple", "standard", "strict"), default="standard")
        parser.add_argument("--plan", action="store_true")
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = [
            "adjust-workflow",
            "--root",
            str(root),
            "--name",
            parsed.name,
            "--from-request",
            parsed.from_request,
            "--profile",
            parsed.profile,
            "--format",
            parsed.format,
        ]
        if parsed.recipe:
            command.extend(["--recipe", parsed.recipe])
        if parsed.plan:
            command.append("--plan")
        if parsed.summary:
            command.append("--summary")
        if parsed.compact:
            command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "eval-gap":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py workflow eval-gap")
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--all", action="store_true")
        target.add_argument("--name", action="append")
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        report = repo_optimizations.workflow_eval_gap(root, None if parsed.all else parsed.name)
        if parsed.summary:
            report = repo_optimizations.summarize_eval_gap(report, "workflows", compact=parsed.compact)
        if parsed.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(repo_optimizations.render_report(report, "Workflow Eval Gap"), end="")
        return 0 if report.get("ok") else 1
    if subcommand == "eval":
        parser = argparse.ArgumentParser(
            prog="python -B .agents/manage.py workflow eval",
            description="runtime: run workflow eval suites; inspect suites before strict read-only use.",
        )
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--all", action="store_true")
        target.add_argument("--name")
        parser.add_argument("--suite", help="JSON workflow eval suite; required with --name")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parser.add_argument("--summary", action="store_true", help="with --all, emit compact counts and failures only")
        parser.add_argument("--compact", action="store_true", help="with --summary, omit passing suite rows")
        parsed = parser.parse_args(rest)
        if parsed.all:
            if parsed.suite:
                print("workflow eval --all does not accept --suite", file=sys.stderr)
                return 2
            command = ["eval-workflows", "--root", str(root), "--format", parsed.format]
            if parsed.summary:
                command.append("--summary")
            if parsed.compact:
                command.append("--compact")
            return repo.run_workflow_repo_manager(
                root,
                command,
            )
        if not parsed.suite:
            print("workflow eval --name requires --suite", file=sys.stderr)
            return 2
        command = [
            "eval-workflow",
            "--root",
            str(root),
            "--name",
            parsed.name,
            "--suite",
            parsed.suite,
            "--format",
            parsed.format,
        ]
        if parsed.summary:
            command.append("--summary")
        if parsed.compact:
            command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "smoke":
        parser = argparse.ArgumentParser(
            prog="python -B .agents/manage.py workflow smoke",
            description="write/temp: run offline smoke checks; use --dry-run for read-only planning.",
        )
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--all", action="store_true")
        target.add_argument("--name", action="append")
        parser.add_argument("--lifecycle-only", action="store_true", help="write/temp: only run lifecycle temp-run checks")
        parser.add_argument("--dry-run", action="store_true", help="read-only: plan checks without writing temporary run files")
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = ["smoke-workflows", "--root", str(root), "--format", parsed.format]
        if parsed.all:
            command.append("--all")
        for workflow_name in parsed.name or []:
            command.extend(["--name", workflow_name])
        if parsed.lifecycle_only:
            command.append("--lifecycle-only")
        if parsed.dry_run:
            command.append("--dry-run")
        if parsed.summary:
            command.append("--summary")
        if parsed.compact:
            command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "scorecard":
        parser = argparse.ArgumentParser(
            prog="python -B .agents/manage.py workflow scorecard",
            description="runtime: score workflow readiness; use --no-lifecycle for strict read-only/offline use.",
        )
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--all", action="store_true")
        target.add_argument("--name", action="append")
        parser.add_argument("--no-lifecycle", action="store_true", help="skip temporary lifecycle smoke scoring without lifecycle writes")
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = ["scorecard-workflows", "--root", str(root), "--format", parsed.format]
        if parsed.all:
            command.append("--all")
        for workflow_name in parsed.name or []:
            command.extend(["--name", workflow_name])
        if parsed.no_lifecycle:
            command.append("--no-lifecycle")
        if parsed.summary:
            command.append("--summary")
        if parsed.compact:
            command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "analytics":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py workflow analytics")
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--all", action="store_true")
        target.add_argument("--name", action="append")
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = ["analytics-workflows", "--root", str(root), "--format", parsed.format]
        if parsed.all:
            command.append("--all")
        for workflow_name in parsed.name or []:
            command.extend(["--name", workflow_name])
        if parsed.summary:
            command.append("--summary")
        if parsed.compact:
            command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "workers":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py workflow workers")
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--all", action="store_true")
        target.add_argument("--name", action="append")
        target.add_argument("--profiles", action="store_true")
        parser.add_argument("--phase")
        parser.add_argument("--delegation-requested", action="store_true")
        parser.add_argument("--task-class", choices=("independent-read-heavy",))
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = ["workflow-workers", "--root", str(root), "--format", parsed.format]
        if parsed.all:
            command.append("--all")
        if parsed.profiles:
            command.append("--profiles")
        for workflow_name in parsed.name or []:
            command.extend(["--name", workflow_name])
        if parsed.phase:
            command.extend(["--phase", parsed.phase])
        if parsed.delegation_requested:
            command.append("--delegation-requested")
        if parsed.task_class:
            command.extend(["--task-class", parsed.task_class])
        if parsed.summary:
            command.append("--summary")
        if parsed.compact:
            command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "route-model":
        parser = argparse.ArgumentParser(
            prog="python -B .agents/manage.py workflow route-model",
            description="Resolve a project task/task-set ordered model preference and fallback chain.",
        )
        target = parser.add_mutually_exclusive_group()
        target.add_argument("--task")
        target.add_argument("--task-set")
        parser.add_argument("--host", required=True)
        parser.add_argument("--available-model", action="append", dest="available_models")
        parser.add_argument("--failed-model", action="append", dest="failed_models")
        parser.add_argument("--validate", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = ["workflow-route-model", "--root", str(root), "--host", parsed.host, "--format", parsed.format]
        if parsed.task:
            command.extend(["--task", parsed.task])
        if parsed.task_set:
            command.extend(["--task-set", parsed.task_set])
        for model in parsed.available_models or []:
            command.extend(["--available-model", model])
        for model in parsed.failed_models or []:
            command.extend(["--failed-model", model])
        if parsed.validate:
            command.append("--validate")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "start":
        parser = workflow_start_parser()
        parsed = parser.parse_args(rest)
        workflow_name = parsed.name
        if parsed.from_request:
            route_report = workflow_start_from_request_report(root, parsed.from_request)
            if route_report.get("ok") is not True:
                if parsed.format == "json":
                    print(json.dumps(route_report, indent=2, sort_keys=True))
                else:
                    print(render_workflow_start_from_request(route_report), end="")
                return 1
            workflow_name = str(route_report.get("selected_owner") or "")
        command = ["start-run", "--root", str(root), "--name", workflow_name, "--format", parsed.format]
        if parsed.run_id:
            command.extend(["--run-id", parsed.run_id])
        if parsed.from_ticket:
            command.extend(["--from-ticket", parsed.from_ticket])
        if parsed.profile:
            command.extend(["--profile", parsed.profile])
        if parsed.from_request:
            command.extend(["--from-request", parsed.from_request])
        if parsed.summary:
            command.append("--summary")
        if parsed.compact:
            command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "resume":
        parser = argparse.ArgumentParser(
            prog="python -B .agents/manage.py workflow resume",
            description="write/run-state: refresh context evidence, context packet, and checkpoint state before showing resume details.",
        )
        parser.add_argument("--name", required=True)
        parser.add_argument("--run-id")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parser.add_argument("--summary", action="store_true", help="emit compact agent-facing resume packet")
        parser.add_argument("--compact", action="store_true", help="with --summary, omit verbose evidence payloads")
        parsed = parser.parse_args(rest)
        command = ["resume-run", "--root", str(root), "--name", parsed.name, "--format", parsed.format]
        if parsed.run_id:
            command.extend(["--run-id", parsed.run_id])
        if parsed.summary:
            command.append("--summary")
        if parsed.compact:
            command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "recover":
        parser = argparse.ArgumentParser(
            prog="python -B .agents/manage.py workflow recover",
            description="read-only diagnostic by default; writes recovered run state only with --write.",
        )
        parser.add_argument("--name", required=True)
        parser.add_argument("--run-id")
        parser.add_argument("--write", action="store_true", help="write recovered run.json and refresh context when declared")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = ["recover-run", "--root", str(root), "--name", parsed.name, "--format", parsed.format]
        if parsed.run_id:
            command.extend(["--run-id", parsed.run_id])
        if parsed.write:
            command.append("--write")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "context-audit":
        return run_workflow_context_audit(root, rest)
    if subcommand == "context":
        if rest and rest[0] == "audit":
            return run_workflow_context_audit(root, rest[1:])
        parser = argparse.ArgumentParser(
            prog="python -B .agents/manage.py workflow context",
            description="read-only check or write deterministic workflow context packets.",
        )
        parser.add_argument("--name")
        parser.add_argument("--all", action="store_true")
        parser.add_argument("--run-id")
        parser.add_argument("--write", action="store_true", help="write artifacts/context/context-packet.json and .md")
        parser.add_argument("--check", action="store_true", help="read-only check: fail if existing context is missing or stale")
        parser.add_argument(
            "--runtime-observation-file",
            help=(
                "repo-local host/provider observation JSON under the selected run's validation directory; "
                "requires --write"
            ),
        )
        parser.add_argument(
            "--include-completed",
            action="store_true",
            help="with --all, treat completed-run context failures as blocking",
        )
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = ["context-run", "--root", str(root), "--format", parsed.format]
        if parsed.all:
            command.append("--all")
        elif parsed.name:
            command.extend(["--name", parsed.name])
        else:
            print("workflow context requires --name or --all", file=sys.stderr)
            return 2
        if parsed.run_id:
            command.extend(["--run-id", parsed.run_id])
        if parsed.write:
            command.append("--write")
        if parsed.runtime_observation_file:
            command.extend(["--runtime-observation-file", parsed.runtime_observation_file])
        if parsed.check:
            command.append("--check")
        if parsed.include_completed:
            command.append("--include-completed")
        if parsed.summary:
            command.append("--summary")
        if parsed.compact:
            command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "checkpoint":
        parser = argparse.ArgumentParser(
            prog="python -B .agents/manage.py workflow checkpoint",
            description="read-only check or write generated workflow checkpoints.",
        )
        parser.add_argument("--name", required=True)
        parser.add_argument("--run-id")
        parser.add_argument("--write", action="store_true", help="write artifacts/checkpoint/checkpoint.json and .md")
        parser.add_argument("--check", action="store_true", help="read-only check: fail if existing checkpoint is missing or stale")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = ["checkpoint-run", "--root", str(root), "--format", parsed.format, "--name", parsed.name]
        if parsed.run_id:
            command.extend(["--run-id", parsed.run_id])
        if parsed.write:
            command.append("--write")
        if parsed.check:
            command.append("--check")
        if parsed.compact:
            command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "plan-check":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py workflow plan-check")
        parser.add_argument("--name", required=True)
        parser.add_argument("--run-id")
        parser.add_argument("--template", action="store_true")
        parser.add_argument("--plan")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = ["plan-check-run", "--root", str(root), "--name", parsed.name, "--format", parsed.format]
        if parsed.run_id:
            command.extend(["--run-id", parsed.run_id])
        if parsed.template:
            command.append("--template")
        if parsed.plan:
            command.extend(["--plan", parsed.plan])
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "template":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py workflow template")
        template_sub = parser.add_subparsers(dest="template_command", required=True)
        resolve_parser = template_sub.add_parser("resolve")
        resolve_parser.add_argument("--name", required=True)
        resolve_parser.add_argument("--template")
        resolve_parser.add_argument("--profile", default="default")
        resolve_parser.add_argument("--summary", action="store_true")
        resolve_parser.add_argument("--compact", action="store_true")
        resolve_parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        lint_parser = template_sub.add_parser("lint")
        lint_parser.add_argument("--name")
        lint_parser.add_argument("--summary", action="store_true")
        lint_parser.add_argument("--compact", action="store_true")
        lint_parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        gate_parser = template_sub.add_parser("gate-check")
        gate_target = gate_parser.add_mutually_exclusive_group(required=True)
        gate_target.add_argument("--name")
        gate_target.add_argument("--all", action="store_true")
        gate_parser.add_argument("--summary", action="store_true")
        gate_parser.add_argument("--compact", action="store_true")
        gate_parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = ["template-run", "--root", str(root), parsed.template_command]
        if parsed.template_command == "resolve":
            command.extend(["--name", parsed.name])
            if parsed.template:
                command.extend(["--template", parsed.template])
            command.extend(["--profile", parsed.profile, "--format", parsed.format])
            if parsed.summary:
                command.append("--summary")
            if parsed.compact:
                command.append("--compact")
        elif parsed.template_command == "lint":
            if parsed.name:
                command.extend(["--name", parsed.name])
            command.extend(["--format", parsed.format])
            if parsed.summary:
                command.append("--summary")
            if parsed.compact:
                command.append("--compact")
        else:
            if parsed.all:
                command.append("--all")
            else:
                command.extend(["--name", parsed.name])
            command.extend(["--format", parsed.format])
            if parsed.summary:
                command.append("--summary")
            if parsed.compact:
                command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "integration-check":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py workflow integration-check")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        return repo.run_workflow_repo_manager(root, ["integration-check-run", "--root", str(root), "--format", parsed.format])
    if subcommand == "metadata":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py workflow metadata")
        metadata_sub = parser.add_subparsers(dest="metadata_command", required=True)
        inspect_parser = metadata_sub.add_parser("inspect")
        inspect_parser.add_argument("--name", required=True)
        inspect_parser.add_argument("--summary", action="store_true")
        inspect_parser.add_argument("--compact", action="store_true")
        inspect_parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = [
            "metadata-run",
            "--root",
            str(root),
            parsed.metadata_command,
            "--name",
            parsed.name,
            "--format",
            parsed.format,
        ]
        if parsed.summary:
            command.append("--summary")
        if parsed.compact:
            command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "managed-section-diff":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py workflow managed-section-diff")
        parser.add_argument("--target", required=True)
        parser.add_argument("--replacement", required=True)
        parser.add_argument("--start-marker", default="<!-- MANAGED START -->")
        parser.add_argument("--end-marker", default="<!-- MANAGED END -->")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        return repo.run_workflow_repo_manager(
            root,
            [
                "managed-section-diff-run",
                "--root",
                str(root),
                "--target",
                parsed.target,
                "--replacement",
                parsed.replacement,
                "--start-marker",
                parsed.start_marker,
                "--end-marker",
                parsed.end_marker,
                "--format",
                parsed.format,
            ],
        )
    if subcommand == "branch-policy":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py workflow branch-policy")
        parser.add_argument("--pattern", default=r"^(feature|fix|docs|chore|release)/[a-z0-9][a-z0-9._-]*$")
        parser.add_argument("--branch")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = ["branch-policy-run", "--root", str(root), "--pattern", parsed.pattern, "--format", parsed.format]
        if parsed.branch:
            command.extend(["--branch", parsed.branch])
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "validation-packet":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py workflow validation-packet")
        parser.add_argument("--name", required=True)
        parser.add_argument("--run-id", required=True)
        parser.add_argument("--kind", choices=("playwright-screenshots",), required=True)
        parser.add_argument("--require-llm-analysis", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = [
            "validation-packet-run",
            "--root",
            str(root),
            "--name",
            parsed.name,
            "--run-id",
            parsed.run_id,
            "--kind",
            parsed.kind,
            "--format",
            parsed.format,
        ]
        if parsed.require_llm_analysis:
            command.append("--require-llm-analysis")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "context-evidence":
        parser = argparse.ArgumentParser(
            prog="python -B .agents/manage.py workflow context-evidence",
            description="read-only check or write required workflow context-evidence packets.",
        )
        parser.add_argument("--name", required=True)
        parser.add_argument("--run-id")
        parser.add_argument("--event", choices=("start", "resume", "finish"), default="start")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--write", action="store_true", help="write validation/context-evidence-<event>.json and .md")
        mode.add_argument("--check", action="store_true", help="read-only check: validate an existing context-evidence packet")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = [
            "context-evidence-run",
            "--root",
            str(root),
            "--name",
            parsed.name,
            "--event",
            parsed.event,
            "--format",
            parsed.format,
        ]
        if parsed.run_id:
            command.extend(["--run-id", parsed.run_id])
        if parsed.write:
            command.append("--write")
        if parsed.check:
            command.append("--check")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "hooks":
        parser = argparse.ArgumentParser(prog="python -B .agents/manage.py workflow hooks")
        parser.add_argument("--name")
        parser.add_argument("--all", action="store_true")
        parser.add_argument("--check", action="store_true")
        parser.add_argument("--run-id")
        parser.add_argument("--event")
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = ["hooks-run", "--root", str(root), "--format", parsed.format]
        if parsed.all:
            command.append("--all")
        elif parsed.name:
            command.extend(["--name", parsed.name])
        else:
            print("workflow hooks requires --name or --all", file=sys.stderr)
            return 2
        if parsed.check:
            command.append("--check")
        if parsed.run_id:
            command.extend(["--run-id", parsed.run_id])
        if parsed.event:
            command.extend(["--event", parsed.event])
        if parsed.summary or parsed.compact:
            command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "hook-audit":
        parser = argparse.ArgumentParser(
            prog="python -B .agents/manage.py workflow hook-audit",
            description="write a normalized evidence packet for a deterministic workflow hook.",
        )
        parser.add_argument("--name", required=True)
        parser.add_argument("--run-id")
        parser.add_argument("--run-dir", required=True)
        parser.add_argument("--event", required=True)
        parser.add_argument("--hook-id", required=True)
        parser.add_argument("--output")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = [
            "hook-audit-run",
            "--root",
            str(root),
            "--name",
            parsed.name,
            "--run-dir",
            parsed.run_dir,
            "--event",
            parsed.event,
            "--hook-id",
            parsed.hook_id,
            "--format",
            parsed.format,
        ]
        if parsed.run_id:
            command.extend(["--run-id", parsed.run_id])
        if parsed.output:
            command.extend(["--output", parsed.output])
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "finish":
        parser = argparse.ArgumentParser(
            prog="python -B .agents/manage.py workflow finish",
            description="write/run-state: validate final evidence and refresh lifecycle packets.",
        )
        parser.add_argument("--name", required=True)
        parser.add_argument("--run-id")
        parser.add_argument("--summary", action="store_true")
        parser.add_argument("--compact", action="store_true")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = ["finish-run", "--root", str(root), "--name", parsed.name, "--format", parsed.format]
        if parsed.run_id:
            command.extend(["--run-id", parsed.run_id])
        if parsed.summary:
            command.append("--summary")
        if parsed.compact:
            command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand == "handoff":
        parser = argparse.ArgumentParser(
            prog="python -B .agents/manage.py workflow handoff",
            description="read-only by default; writes normalized handoff state only with --write.",
        )
        parser.add_argument("--name", required=True)
        parser.add_argument("--run-id")
        parser.add_argument("--write", action="store_true", help="write/normalize the handoff section in run.json")
        parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
        parsed = parser.parse_args(rest)
        command = ["handoff-run", "--root", str(root), "--name", parsed.name, "--format", parsed.format]
        if parsed.run_id:
            command.extend(["--run-id", parsed.run_id])
        if parsed.write:
            command.append("--write")
        return repo.run_workflow_repo_manager(root, command)
    if subcommand != "doctor":
        print(f"unknown workflow subcommand: {subcommand}", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog="python -B .agents/manage.py workflow doctor")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--name")
    target.add_argument("--all", action="store_true")
    parser.add_argument(
        "--include-completed",
        action="store_true",
        help="with --all, treat completed-run context failures as blocking health risks",
    )
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parsed = parser.parse_args(rest)
    if parsed.all:
        command = ["review-workflow", "--root", str(root), "--all", "--plan", "--format", parsed.format]
        if parsed.include_completed:
            command.append("--include-completed")
        if parsed.summary:
            command.append("--summary")
        if parsed.compact:
            command.append("--compact")
        return repo.run_workflow_repo_manager(root, command)
    if parsed.include_completed:
        print("workflow doctor --include-completed requires --all", file=sys.stderr)
        return 2
    return repo.run_workflow_repo_manager(
        root,
        ["review-workflow", "--root", str(root), "--name", parsed.name, "--plan", "--format", parsed.format],
    )
