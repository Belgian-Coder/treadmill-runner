#!/usr/bin/env python3
"""Repository validation and health checks owned by skill-manager."""

from __future__ import annotations

import concurrent.futures
import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from repo_support import repo_common as repo
from repo_support import repo_generated as generated
from repo_support import repo_policy
from repo_support.repo_health_links import (
    documentation_map_errors,
    manage_command_reference_errors,
    root_docs_link_errors,
    workflow_prompt_doc_errors,
)

from repo_support.repo_health_surface import (
    active_markdown_files,
    context_budget_warnings,
    estimated_tokens,
    eval_quality_report,
    folder_organization_kind,
    folder_organization_report,
    format_json_files,
    instruction_adapter_files,
    instruction_quality_errors,
    is_intentional_deep_guide,
    json_format_errors,
    local_tool_config_paths,
    root_docs_frontmatter_errors,
    routing_budget_warnings,
    script_complexity_hotspots,
    script_complexity_warnings,
    simplicity_warnings,
    text_word_count,
    tracked_and_untracked_files,
    tracked_files,
    unsupported_memory_claim_warnings,
    validate_candidate_import_hygiene,
    validate_manager_self_containment,
    validate_no_pycache,
    validate_python_only_scripts,
    validate_repo_layout,
)

def validate_repo(root: Path) -> int:
    generated.sync_instructions(root, check=True)

    all_errors: list[str] = validate_python_only_scripts(root)
    _policy_document, policy_config_errors, _policy_exists = repo_policy.load_project_policy(root)
    all_errors.extend(f"{repo_policy.PROJECT_POLICY_PATH}: {item}" for item in policy_config_errors)
    all_errors.extend(validate_no_pycache(root))
    all_errors.extend(validate_repo_layout(root))
    all_errors.extend(validate_candidate_import_hygiene(root))
    all_errors.extend(validate_manager_self_containment(root))
    all_errors.extend(instruction_quality_errors(root))
    all_errors.extend(json_format_errors(root))
    all_errors.extend(root_docs_frontmatter_errors(root))
    all_errors.extend(documentation_map_errors(root))
    all_errors.extend(root_docs_link_errors(root))
    all_errors.extend(workflow_prompt_doc_errors(root))
    all_errors.extend(manage_command_reference_errors(root))
    mermaid_health = mermaid_diagram_health(root)
    mermaid_errors = mermaid_health.get("errors", [])
    if isinstance(mermaid_errors, list):
        all_errors.extend(str(item) for item in mermaid_errors)
    skill_dirs = repo.get_skill_directories(root)

    if all_errors:
        for error in all_errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    from repo_support import repo_changed

    addition_acceptance = repo_changed.addition_acceptance_report(root)
    if not addition_acceptance.get("ok"):
        print("ERROR: addition acceptance gate failed.", file=sys.stderr)
        for item in addition_acceptance.get("issues", []):
            if isinstance(item, dict):
                print(
                    f"ERROR: {item.get('path')}: {item.get('reason')} "
                    f"({item.get('owner')}; {item.get('category')})",
                    file=sys.stderr,
                )
        print("Run: python -B .agents/manage.py check-additions --summary --format json", file=sys.stderr)
        return 1

    if skill_dirs:
        worker_count = min(len(skill_dirs), max(1, (os.cpu_count() or 1)), 8)
        if worker_count > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_by_skill = {
                    executor.submit(repo.validate_skill_with_manager_quiet, root, skill_dir): skill_dir
                    for skill_dir in skill_dirs
                }
                results: dict[Path, tuple[int, str]] = {}
                for future in concurrent.futures.as_completed(future_by_skill):
                    skill_dir = future_by_skill[future]
                    results[skill_dir] = future.result()
            for skill_dir in skill_dirs:
                status, output = results[skill_dir]
                if output:
                    print(output)
                if status != 0:
                    return status
        else:
            for skill_dir in skill_dirs:
                status = repo.validate_skill_with_manager(root, skill_dir)
                if status != 0:
                    return status

    automation_status = generated.validate_automations(root)
    if automation_status != 0:
        return automation_status

    routing_status = generated.sync_skill_routing(root, check=True)
    if routing_status != 0:
        return routing_status

    automation_routing_status = generated.sync_automation_routing(root, check=True)
    if automation_routing_status != 0:
        return automation_routing_status

    claude_status = generated.sync_claude_skills(root, check=True)
    if claude_status != 0:
        return claude_status

    warnings, policy_errors = repo_policy.classify_warnings(root, simplicity_warnings(root))
    if policy_errors:
        for error in policy_errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if warnings:
        print()
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")

    if not skill_dirs:
        print(f"No active skill folders found under {root}.")
        return 0

    print(f"Validated {len(skill_dirs)} skill folder(s).")
    return 0


def eval_suite_paths(skill_dir: Path) -> list[Path]:
    manifest_path = skill_dir / "module.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    quality = manifest.get("quality") if isinstance(manifest, dict) else None
    suites = quality.get("eval_suites") if isinstance(quality, dict) else None
    if not isinstance(suites, list):
        return []
    paths: list[Path] = []
    for item in suites:
        value = item.get("path") if isinstance(item, dict) else item
        if isinstance(value, str) and value.strip():
            paths.append(skill_dir / value.strip())
    return paths


def run_diff_check(root: Path, staged: bool) -> tuple[int, str]:
    git_probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if git_probe.returncode != 0:
        return 0, "skipped: not a git repository"

    command = ["git", "diff", "--cached" if staged else "--check"]
    if staged:
        command.append("--check")
    completed = subprocess.run(
        command,
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return completed.returncode, completed.stdout.strip()


def deep_validate_repo(root: Path) -> int:
    print("# Deep Repository Validation")
    print()
    status = validate_repo(root)
    if status != 0:
        return status

    failures: list[str] = []
    skill_dirs = repo.get_skill_directories(root)
    print()
    print("## Self-Tests")
    for skill_dir in skill_dirs:
        script = skill_dir / "scripts" / "run_self_tests.py"
        if not script.exists():
            print(f"- {skill_dir.name}: skipped, no scripts/run_self_tests.py")
            continue
        code, output = repo.run_python_script_quiet(script, [])
        print(f"- {skill_dir.name}: {'ok' if code == 0 else 'failed'}")
        if code != 0:
            failures.append(f"{skill_dir.name} self-tests failed")
            if output:
                print("  " + output.replace("\n", "\n  "))

    print()
    print("## Eval Suites")
    for skill_dir in skill_dirs:
        suites = eval_suite_paths(skill_dir)
        if not suites:
            print(f"- {skill_dir.name}: skipped, no eval suites declared")
            continue
        for suite in suites:
            code, output = repo.run_skill_manager_script_quiet(
                root,
                "eval_skill.py",
                [
                    "--skill",
                    str(skill_dir),
                    "--suite",
                    str(suite),
                    "--format",
                    "json",
                ],
            )
            label = f"{skill_dir.name}/{repo.relative(skill_dir, suite)}"
            print(f"- {label}: {'ok' if code == 0 else 'failed'}")
            if code != 0:
                failures.append(f"{label} eval failed")
                if output:
                    print("  " + output.replace("\n", "\n  "))

    print()
    print("## Eval Quality")
    eval_quality = eval_quality_report(root)
    print(f"- eval-quality: {'ok' if eval_quality.get('ok') else 'failed'}")
    for warning in eval_quality.get("warnings", []):
        print(f"  warning: {warning}")
    if not eval_quality.get("ok"):
        failures.append("eval-quality lint failed")
        for issue in eval_quality.get("issues", []):
            print(f"  {issue}")

    print()
    print("## Diff Checks")
    for staged in (False, True):
        code, output = run_diff_check(root, staged=staged)
        label = "git diff --cached --check" if staged else "git diff --check"
        print(f"- {label}: {'ok' if code == 0 else 'failed'}")
        if code != 0:
            failures.append(f"{label} failed")
            if output:
                print("  " + output.replace("\n", "\n  "))

    print()
    print(f"Deep status: {'passed' if not failures else 'failed'}")
    if failures:
        print()
        print("## Failures")
        for failure in failures:
            print(f"- {failure}")
    return 0 if not failures else 1


def generated_check(name: str, callback) -> tuple[str, bool, str]:
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = callback()
    except SystemExit as exc:
        return name, False, str(exc)
    except Exception as exc:  # pragma: no cover - defensive CLI reporting
        return name, False, str(exc)
    callback_output = ""
    if isinstance(result, tuple):
        status = result[0]
        callback_output = str(result[1]).strip() if len(result) > 1 else ""
    else:
        status = int(result)
    if status != 0:
        text = callback_output or output.getvalue().strip()
        return name, False, text or f"status {status}"
    return name, True, "ok"


def skipped_generated_check(name: str, reason: str) -> tuple[str, bool, str]:
    return name, True, f"skipped fast: {reason}"


def workflow_routing_check_required(root: Path) -> bool:
    status, lines = repo.git_output(root, "status", "--short")
    if status != 0:
        return True
    for line in lines:
        path = line[3:].strip().replace("\\", "/") if len(line) > 3 else ""
        if path in {"automations/routing.md", "automations/registry.json"}:
            return True
        if not path.startswith("automations/"):
            continue
        if path.endswith("/WORKFLOW.md") or path.endswith("/module.json"):
            return True
    return False


def workflow_directories(root: Path) -> list[Path]:
    automations = root / "automations"
    if not automations.exists():
        return []
    workflows: list[Path] = []
    for child in sorted(automations.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        if (child / "WORKFLOW.md").exists() and (child / "module.json").exists():
            workflows.append(child)
    return workflows


def format_mermaid_diagram_issue(root: Path, item: dict[str, object]) -> str:
    path_value = str(item.get("path") or "")
    line_value = item.get("line")
    message = str(item.get("message") or "Mermaid validation failed")
    if path_value and path_value != "<render>":
        location = repo.relative(root, Path(path_value))
        if isinstance(line_value, int) and line_value > 0:
            location = f"{location}:{line_value}"
        return f"{location}: {message}"
    return message


def mermaid_diagram_health(root: Path) -> dict[str, object]:
    script = (
        root
        / ".agents"
        / "skills"
        / "mermaid-diagrams-azure-devops"
        / "scripts"
        / "validate_mermaid.py"
    )
    if not script.exists():
        return {
            "ok": True,
            "skipped": True,
            "skip_reason": "Mermaid validator is not present.",
            "files_scanned": 0,
            "block_count": 0,
            "artifact_count": 0,
            "errors": [],
            "warnings": [],
        }

    scan_paths = [
        path
        for path in (
            root / ".agents" / "skills",
            root / "automations",
            root / "docs",
            root / "AGENTS.md",
            root / "README.md",
        )
        if path.exists()
    ]
    completed = subprocess.run(
        repo.python_command(script, [*(str(path) for path in scan_paths), "--static-only", "--format", "json"]),
        cwd=root,
        check=False,
        env=repo.child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        output = completed.stdout.strip()
        output_chars = repo_policy.int_value(root, "limits.diagnostics.callback_output_chars")
        if len(output) > output_chars:
            output = output[: output_chars - 3].rstrip() + "..."
        return {
            "ok": False,
            "skipped": False,
            "files_scanned": 0,
            "block_count": 0,
            "artifact_count": 0,
            "errors": [f"Mermaid validation did not return JSON: {output or 'no output'}"],
            "warnings": [],
        }

    raw_errors = report.get("errors") if isinstance(report.get("errors"), list) else []
    raw_warnings = report.get("warnings") if isinstance(report.get("warnings"), list) else []
    errors = [
        format_mermaid_diagram_issue(root, item)
        for item in raw_errors
        if isinstance(item, dict)
    ]
    if completed.returncode != 0 and not errors:
        errors.append("Mermaid validation failed without structured errors.")
    return {
        "ok": completed.returncode == 0 and not errors,
        "skipped": False,
        "files_scanned": len(report.get("files_scanned", [])) if isinstance(report.get("files_scanned"), list) else 0,
        "block_count": int(report.get("block_count", 0) or 0),
        "artifact_count": int(report.get("artifact_count", 0) or 0),
        "errors": errors,
        "warnings": raw_warnings,
    }


def build_repo_health_report(root: Path, *, fast: bool = False) -> dict[str, object]:
    skills = repo.get_skill_directories(root)
    workflows = workflow_directories(root)
    generated_checks: list[tuple[str, bool, str]] = [
        generated_check("instructions", lambda: generated.sync_instructions(root, check=True)),
        generated_check(
            "skill routing/registry",
            lambda: repo.run_python_script_quiet(
                repo.skill_manager_script(root, "sync_skill_routing.py"),
                ["--root", str(root), "--check"],
            ),
        ),
        generated_check("module schema", lambda: generated.sync_module_schema(root, check=True)),
        generated_check("project policy schema", lambda: generated.sync_project_policy_schema(root, check=True)),
        generated_check("Claude adapters", lambda: generated.sync_claude_skills(root, check=True)),
    ]
    if fast and not workflow_routing_check_required(root):
        generated_checks.insert(
            3,
            skipped_generated_check(
                "workflow routing/registry",
                "no changed workflow routing inputs",
            ),
        )
    else:
        generated_checks.insert(
            3,
            generated_check(
                "workflow routing/registry",
                lambda: repo.run_python_script_quiet(
                    repo.workflow_manager_script(root, "workflow_repo_manager.py"),
                    ["sync-automation-routing", "--root", str(root), "--check"],
                ),
            ),
        )
    failed_generated = [name for name, ok, _message in generated_checks if not ok]
    if fast:
        issue_count = len(failed_generated)
        return {
            "schema_version": 1,
            "tool": "check-repo-health",
            "ok": issue_count == 0,
            "status": "passed" if issue_count == 0 else "issues-found",
            "mode": "fast",
            "python": sys.version.split()[0],
            "root": str(root),
            "skills": [path.name for path in skills],
            "workflows": [path.name for path in workflows],
            "generated_checks": [
                {"name": name, "ok": ok, "message": message}
                for name, ok, message in generated_checks
            ],
            "repository_surface": {
                "fast_skipped": [
                    "layout, docs, Mermaid, command-reference, and complexity checks run in full repo health/check"
                ],
            },
            "warnings": [],
            "next_recommended_command": (
                "python -B .agents/manage.py sync"
                if failed_generated
                else "none, fast generated checks are healthy"
            ),
        }
    layout_errors = validate_repo_layout(root)
    candidate_import_errors = validate_candidate_import_hygiene(root)
    pycache_errors = validate_no_pycache(root)
    script_errors = validate_python_only_scripts(root)
    containment_errors = validate_manager_self_containment(root)
    instruction_errors = instruction_quality_errors(root)
    json_errors = json_format_errors(root)
    doc_metadata_errors = root_docs_frontmatter_errors(root)
    doc_map_errors = documentation_map_errors(root)
    doc_link_errors = root_docs_link_errors(root)
    workflow_prompt_errors = workflow_prompt_doc_errors(root)
    command_reference_errors = manage_command_reference_errors(root)
    _policy_document, policy_config_errors, _policy_exists = repo_policy.load_project_policy(root)
    mermaid_diagrams = mermaid_diagram_health(root)
    mermaid_errors = (
        mermaid_diagrams.get("errors", [])
        if isinstance(mermaid_diagrams.get("errors"), list)
        else []
    )
    warnings, policy_errors = repo_policy.classify_warnings(root, simplicity_warnings(root))
    folder_organization = folder_organization_report(root)
    script_hotspots = script_complexity_hotspots(root)
    local_settings = local_tool_config_paths(root)
    issue_count = (
        len(failed_generated)
        + len(layout_errors)
        + len(candidate_import_errors)
        + len(pycache_errors)
        + len(script_errors)
        + len(containment_errors)
        + len(instruction_errors)
        + len(json_errors)
        + len(doc_metadata_errors)
        + len(doc_map_errors)
        + len(doc_link_errors)
        + len(workflow_prompt_errors)
        + len(command_reference_errors)
        + len(mermaid_errors)
        + len(policy_errors)
        + len(policy_config_errors)
    )
    return {
        "schema_version": 1,
        "tool": "check-repo-health",
        "ok": issue_count == 0,
        "status": "passed" if issue_count == 0 else "issues-found",
        "python": sys.version.split()[0],
        "root": str(root),
        "skills": [path.name for path in skills],
        "workflows": [path.name for path in workflows],
        "generated_checks": [
            {"name": name, "ok": ok, "message": message}
            for name, ok, message in generated_checks
        ],
        "repository_surface": {
            "local_settings": [repo.relative(root, path) for path in local_settings],
            "layout": layout_errors,
            "candidate_imports": candidate_import_errors,
            "bytecode": pycache_errors,
            "script_type": script_errors,
            "self_contained_managers": containment_errors,
            "instruction_quality": instruction_errors,
            "json_format": json_errors,
            "doc_metadata": doc_metadata_errors,
            "doc_map": doc_map_errors,
            "doc_links": doc_link_errors,
            "workflow_prompts": workflow_prompt_errors,
            "command_references": command_reference_errors,
            "mermaid_diagrams": mermaid_errors,
            "policy_warnings_escalated": policy_errors,
            "project_policy": policy_config_errors,
            "mermaid_diagram_summary": {
                "ok": bool(mermaid_diagrams.get("ok")),
                "skipped": bool(mermaid_diagrams.get("skipped")),
                "files_scanned": int(mermaid_diagrams.get("files_scanned", 0) or 0),
                "block_count": int(mermaid_diagrams.get("block_count", 0) or 0),
                "artifact_count": int(mermaid_diagrams.get("artifact_count", 0) or 0),
                "warning_count": (
                    len(mermaid_diagrams.get("warnings", []))
                    if isinstance(mermaid_diagrams.get("warnings"), list)
                    else 0
                ),
                "skip_reason": str(mermaid_diagrams.get("skip_reason") or ""),
            },
            "folder_organization": folder_organization,
            "script_complexity_hotspots": script_hotspots,
        },
        "warnings": warnings,
        "next_recommended_command": (
            "python -B .agents/manage.py sync"
            if failed_generated
            else "python -B .agents/manage.py check"
            if issue_count
            else "review warnings, or continue if they are intentional"
            if warnings
            else "none, repo is healthy"
        ),
    }


def summarize_repo_health_report(report: dict[str, object]) -> dict[str, object]:
    surface = report.get("repository_surface") if isinstance(report.get("repository_surface"), dict) else {}
    issue_fields = (
        "layout",
        "candidate_imports",
        "bytecode",
        "script_type",
        "self_contained_managers",
        "instruction_quality",
        "json_format",
        "doc_metadata",
        "doc_map",
        "doc_links",
        "workflow_prompts",
        "command_references",
        "mermaid_diagrams",
        "policy_warnings_escalated",
        "project_policy",
    )
    issue_counts = {
        field: len(surface.get(field, [])) if isinstance(surface.get(field), list) else 0
        for field in issue_fields
    }
    generated = report.get("generated_checks") if isinstance(report.get("generated_checks"), list) else []
    generated_failures = [item for item in generated if isinstance(item, dict) and not item.get("ok")]
    issues = {
        field: surface.get(field, [])
        for field in issue_fields
        if isinstance(surface.get(field), list) and surface.get(field)
    }
    mermaid_summary = (
        surface.get("mermaid_diagram_summary", {})
        if isinstance(surface.get("mermaid_diagram_summary"), dict)
        else {}
    )
    return {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "check-repo-health"),
        "ok": bool(report.get("ok")),
        "status": report.get("status", ""),
        "python": report.get("python", ""),
        "summary": {
            "skill_count": len(report.get("skills", [])) if isinstance(report.get("skills"), list) else 0,
            "workflow_count": len(report.get("workflows", [])) if isinstance(report.get("workflows"), list) else 0,
            "generated_check_count": len(generated),
            "failed_generated_count": len(generated_failures),
            "issue_count": sum(issue_counts.values()),
            "warning_count": len(report.get("warnings", [])) if isinstance(report.get("warnings"), list) else 0,
            "folder_organization_count": (
                len(surface.get("folder_organization", []))
                if isinstance(surface.get("folder_organization"), list)
                else 0
            ),
            "script_hotspot_count": (
                len(surface.get("script_complexity_hotspots", []))
                if isinstance(surface.get("script_complexity_hotspots"), list)
                else 0
            ),
            "mermaid_files_scanned": int(mermaid_summary.get("files_scanned", 0) or 0),
            "mermaid_block_count": int(mermaid_summary.get("block_count", 0) or 0),
            "mermaid_artifact_count": int(mermaid_summary.get("artifact_count", 0) or 0),
            "mermaid_warning_count": int(mermaid_summary.get("warning_count", 0) or 0),
        },
        "generated_failures": generated_failures,
        "issues": issues,
        "warnings": report.get("warnings", []),
        "next_recommended_command": report.get("next_recommended_command", ""),
    }


def render_repo_health_summary(report: dict[str, object]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = ["# Repository Health Summary", ""]
    lines.append(f"- Status: {report.get('status')}")
    lines.append(f"- Skills/workflows: {summary.get('skill_count', 0)}/{summary.get('workflow_count', 0)}")
    lines.append(
        f"- Issues/warnings: {summary.get('issue_count', 0)}/{summary.get('warning_count', 0)}"
    )
    lines.append(
        f"- Generated failures: {summary.get('failed_generated_count', 0)}/{summary.get('generated_check_count', 0)}"
    )
    lines.append(
        "- Mermaid diagrams: "
        f"{summary.get('mermaid_artifact_count', 0)} artifacts, "
        f"{summary.get('mermaid_block_count', 0)} blocks, "
        f"{summary.get('mermaid_warning_count', 0)} warnings"
    )
    lines.append(f"- Next: `{report.get('next_recommended_command')}`")
    return "\n".join(lines) + "\n"


def check_repo_health(root: Path, *, as_json: bool = False, summary: bool = False) -> int:
    report = build_repo_health_report(root)
    if summary:
        compact = summarize_repo_health_report(report)
        if as_json:
            print(json.dumps(compact, indent=2, sort_keys=True))
        else:
            print(render_repo_health_summary(compact), end="")
        return 0 if report["ok"] else 1
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["ok"] else 1
    print("# Repository Health Check")
    print()
    print(f"- Python: {report['python']}")
    print(f"- Root: {root}")

    print(f"- Skills: {len(report['skills'])} ({', '.join(report['skills']) or 'none'})")
    print(f"- Workflows: {len(report['workflows'])} ({', '.join(report['workflows']) or 'none'})")
    print()
    print("## Generated Files")
    for item in report["generated_checks"]:
        name = item["name"]
        ok = item["ok"]
        message = item["message"]
        print(f"- {name}: {'ok' if ok else 'stale'}")
        if not ok and message:
            print(f"  {message}")

    surface = report["repository_surface"]

    print()
    print("## Repository Surface")
    for path in surface["local_settings"]:
        print(f"- local setting present: {path}")
    for label, values in [
        ("layout", surface["layout"]),
        ("candidate imports", surface["candidate_imports"]),
        ("bytecode", surface["bytecode"]),
        ("script type", surface["script_type"]),
        ("self-contained managers", surface["self_contained_managers"]),
        ("instruction quality", surface["instruction_quality"]),
        ("json format", surface["json_format"]),
        ("doc metadata", surface["doc_metadata"]),
        ("doc map", surface["doc_map"]),
        ("doc links", surface["doc_links"]),
        ("workflow prompts", surface["workflow_prompts"]),
        ("command references", surface["command_references"]),
        ("Mermaid diagrams", surface["mermaid_diagrams"]),
        ("policy warnings escalated to errors", surface["policy_warnings_escalated"]),
        ("project policy", surface["project_policy"]),
    ]:
        if values:
            for value in values:
                print(f"- {label}: {value}")
        else:
            print(f"- {label}: ok")
    mermaid_summary = surface.get("mermaid_diagram_summary", {})
    if isinstance(mermaid_summary, dict) and not mermaid_summary.get("skipped"):
        print(
            "- Mermaid diagram inventory: "
            f"{mermaid_summary.get('artifact_count', 0)} materialized artifacts, "
            f"{mermaid_summary.get('block_count', 0)} blocks, "
            f"{mermaid_summary.get('files_scanned', 0)} files scanned"
        )

    folder_organization = surface.get("folder_organization", [])
    if folder_organization:
        print("- folder organization: review")
        for item in folder_organization:
            print(
                f"  - {item['folder']}: {item['direct_files']} direct files "
                f"({item['kind']}); {item['recommendation']}"
            )
    else:
        print("- folder organization: ok")

    warnings = report["warnings"]
    if warnings:
        print()
        print("## Warnings")
        for warning in warnings:
            print(f"- {warning}")

    print()
    print(f"Status: {'passed' if report['ok'] else 'issues found'}")
    print()
    next_command = report["next_recommended_command"]
    if str(next_command).startswith("python "):
        print(f"Next recommended command: `{next_command}`")
    else:
        print(f"Next recommended command: {next_command}.")
    return 0 if report["ok"] else 1
