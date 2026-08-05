#!/usr/bin/env python3
"""Repository maintenance commands for the skills repo."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import audit_skill_determinism
from repo_support import repo_changed
from repo_support import repo_checklists
from repo_support import repo_commands
from repo_support import repo_command_metrics
from repo_support import repo_common as repo
from repo_support import repo_cost_policy
from repo_support import repo_policy
from repo_support import repo_determinism
from repo_support import repo_portability
from repo_support import repo_doctor
from repo_support import repo_feedback
from repo_support import repo_generated
from repo_support import repo_harness_install
from repo_support import repo_harness_promote
from repo_support import repo_harness_update
from repo_support import repo_health
from repo_support import repo_local_ai
from repo_support import repo_onboarding
from repo_support import repo_portable_tools
from repo_support import repo_public_commands
from repo_support import repo_prevention
from repo_support import repo_qol
from repo_support import repo_routing
from repo_support import repo_setup
from repo_support import repo_syntax
import review_skill_command

TOOLS = repo.TOOLS
WORKFLOW_COMMANDS = repo.WORKFLOW_COMMANDS

require_supported_python = repo.require_supported_python
repo_root = repo.repo_root
relative = repo.relative
same_path = repo.same_path
child_env = repo.child_env
run_python_script_quiet = repo.run_python_script_quiet
skill_manager_script = repo.skill_manager_script
skill_script = repo.skill_script
workflow_manager_script = repo.workflow_manager_script
run_skill_script = repo.run_skill_script
run_skill_manager_script = repo.run_skill_manager_script
run_skill_manager_script_quiet = repo.run_skill_manager_script_quiet
run_workflow_repo_manager = repo.run_workflow_repo_manager
validate_skill_with_manager = repo.validate_skill_with_manager
validate_skill_with_manager_quiet = repo.validate_skill_with_manager_quiet
skill_directories = repo.skill_directories
get_skill_directories = repo.get_skill_directories

sync_instructions = repo_generated.sync_instructions
sync_skill_routing = repo_generated.sync_skill_routing
validate_automations = repo_generated.validate_automations
sync_automation_routing = repo_generated.sync_automation_routing
sync_claude_skills = repo_generated.sync_claude_skills
sync_all = repo_generated.sync_all

validate_python_only_scripts = repo_health.validate_python_only_scripts
validate_no_pycache = repo_health.validate_no_pycache
validate_repo_layout = repo_health.validate_repo_layout
validate_candidate_import_hygiene = repo_health.validate_candidate_import_hygiene
validate_manager_self_containment = repo_health.validate_manager_self_containment
instruction_quality_errors = repo_health.instruction_quality_errors
validate_repo = repo_health.validate_repo
deep_validate_repo = repo_health.deep_validate_repo
simplicity_warnings = repo_health.simplicity_warnings
check_repo_health = repo_health.check_repo_health
script_complexity_warnings = repo_health.script_complexity_warnings
build_link_skills_report = repo_setup.build_link_skills_report

changed_files = repo_changed.changed_files
changed_scope = repo_changed.changed_scope
check_changed = repo_changed.check_changed
command_index = repo_commands.command_index
print_commands = repo_commands.print_commands


def compare_skill(args: argparse.Namespace, root: Path) -> int:
    script = repo.skill_manager_script(root, "compare_skill_versions.py")
    command = repo.python_command(
        script,
        [
            args.old,
            args.new,
            "--format",
            args.output_format,
        ],
    )
    return subprocess.run(command, check=False, env=repo.child_env()).returncode


def upgrade_skill(args: argparse.Namespace, root: Path) -> int:
    script = repo.skill_manager_script(root, "upgrade_skill.py")
    command = repo.python_command(
        script,
        [
            "--root",
            str(root),
            "--old",
            args.old,
            "--new",
            args.new,
            "--target",
            args.target,
            "--strategy",
            args.strategy,
        ],
    )
    if args.apply:
        command.append("--apply")
    else:
        command.append("--dry-run")
    if args.allow_outside_active_skills:
        command.append("--allow-outside-active-skills")
    return subprocess.run(command, check=False, env=repo.child_env()).returncode


def eval_skill(args: argparse.Namespace, root: Path) -> int:
    command = [
        "--skill",
        args.skill,
        "--suite",
        args.suite,
        "--baseline",
        args.baseline,
        "--format",
        args.output_format,
    ]
    return repo.run_skill_manager_script(root, "eval_skill.py", command)


def attest_skill(args: argparse.Namespace, root: Path) -> int:
    command = ["--root", str(root), "--skill", args.skill, "--format", args.output_format]
    if bool(getattr(args, "summary", False)):
        command.append("--summary")
    if bool(getattr(args, "compact", False)):
        command.append("--compact")
    return repo.run_skill_manager_script(
        root,
        "attest_skill.py",
        command,
    )


def validate_agent_compatibility(args: argparse.Namespace, root: Path) -> int:
    command = ["--root", str(root), "--format", args.output_format]
    if bool(getattr(args, "summary", False)):
        command.append("--summary")
    if bool(getattr(args, "compact", False)):
        command.append("--compact")
    if bool(getattr(args, "installed_hosts", False)):
        command.append("--installed-hosts")
    return repo.run_skill_manager_script(
        root,
        "validate_agent_compatibility.py",
        command,
    )


def skill_inventory(args: argparse.Namespace, root: Path) -> int:
    command = ["--root", str(root), "--format", args.output_format]
    if args.all:
        command.append("--all")
    else:
        command.extend(["--skill", args.skill])
    if bool(getattr(args, "summary", False)):
        command.append("--summary")
    if bool(getattr(args, "compact", False)):
        command.append("--compact")
    return repo.run_skill_manager_script(root, "skill_inventory.py", command)


def triage_candidates(args: argparse.Namespace, root: Path) -> int:
    candidate_root = Path(args.candidate_root).expanduser()
    if not candidate_root.is_absolute():
        candidate_root = root / candidate_root
    command = [
        "--root",
        str(candidate_root),
        "--limit",
        str(args.limit),
        "--max-candidates",
        str(args.max_candidates),
        "--format",
        args.output_format,
        "--review-profile",
        args.review_profile,
        "--repo-root",
        str(root),
    ]
    return repo.run_skill_manager_script(root, "triage_candidates.py", command)


def audit_candidate_source(args: argparse.Namespace, root: Path) -> int:
    source = Path(args.source).expanduser()
    if not source.is_absolute():
        source = root / source
    command = [
        str(source),
        "--warn-threshold",
        str(args.warn_threshold),
        "--error-threshold",
        str(args.error_threshold),
        "--max-pairs",
        str(args.max_pairs),
        "--format",
        args.output_format,
    ]
    if bool(getattr(args, "summary", False)):
        command.append("--summary")
    if bool(getattr(args, "compact", False)):
        command.append("--compact")
    if bool(getattr(args, "strict", False)):
        command.append("--strict")
    return repo.run_skill_manager_script(root, "candidate_source_audit.py", command)


def measure_skill_budget(args: argparse.Namespace, root: Path) -> int:
    command = ["--root", str(root), "--format", args.output_format]
    if args.all:
        command.append("--all")
    else:
        command.extend(["--skill", args.skill])
    if args.summary:
        command.append("--summary")
    if getattr(args, "compact", False):
        command.append("--compact")
    if getattr(args, "write_trend", False):
        command.append("--write-trend")
    if getattr(args, "baseline_ref", None):
        command.extend(["--baseline-ref", args.baseline_ref])
    return repo.run_skill_manager_script(root, "measure_skill_budget.py", command)


def local_ai(args: argparse.Namespace, root: Path) -> int:
    command = ["--root", str(root), *args.local_ai_args]
    return run_skill_script(root, "local-ai-helper", "setup_local_ai.py", command)


def inspect_skill(args: argparse.Namespace, root: Path) -> int:
    command = ["--root", str(root), "--skill", args.skill, "--format", args.output_format]
    if getattr(args, "fast", False):
        command.append("--fast")
    else:
        command.append("--deep")
    if bool(getattr(args, "summary", False)):
        command.append("--summary")
    if bool(getattr(args, "compact", False)):
        command.append("--compact")
    return repo.run_skill_manager_script(root, "inspect_skill.py", command)


def analyze_location(args: argparse.Namespace, root: Path) -> int:
    command = [
        args.location,
        "--max-files",
        str(args.max_files),
        "--max-text-files",
        str(args.max_text_files),
        "--format",
        args.output_format,
        "--review-profile",
        args.review_profile,
    ]
    if args.output:
        command.extend(["--output", args.output])
    if bool(getattr(args, "summary", False)):
        command.append("--summary")
    if bool(getattr(args, "compact", False)):
        command.append("--compact")
    return repo.run_skill_manager_script(root, "analyze_location.py", command)


def new_skill_checklist(args: argparse.Namespace, root: Path) -> int:
    return repo_checklists.new_skill_checklist(args, root)


def forward_workflow_command(args: argparse.Namespace, root: Path) -> int:
    workflow_args = [args.command, "--root", str(root), *getattr(args, "workflow_args", [])]
    if args.command == "sync-automation-routing" and bool(getattr(args, "check", False)) and "--check" not in workflow_args:
        workflow_args.append("--check")
    if args.command == "validate-automations":
        return repo_local_ai.run_with_failure_triage(
            root,
            "validate-automations",
            lambda: repo.run_workflow_repo_manager(root, workflow_args),
        )
    return repo.run_workflow_repo_manager(root, workflow_args)


def workflow_command_from_raw(raw_args: list[str]) -> tuple[Path, list[str]]:
    command = raw_args[0]
    forwarded = list(raw_args[1:])
    root = repo.repo_root(None)
    if "--root" in forwarded:
        root_index = forwarded.index("--root")
        if root_index + 1 >= len(forwarded):
            raise SystemExit("--root requires a value")
        root = repo.repo_root(forwarded[root_index + 1])
        del forwarded[root_index : root_index + 2]
    return root, [command, "--root", str(root), *forwarded]


def default_user_skills_path(tool: str) -> Path:
    return repo_setup.default_user_skills_path(tool)


def link_skills(args: argparse.Namespace, root: Path) -> int:
    source_root = (
        Path(args.skill_source_path).expanduser().resolve()
        if args.skill_source_path
        else root / ".agents" / "skills"
    )
    report = repo_setup.build_link_skills_report(
        source_root=source_root,
        target_paths=repo_setup.target_paths_from_args(args),
        targets=list(args.targets),
        mode=args.mode,
        dry_run=args.dry_run,
        check=False,
    )
    print(repo_setup.render_link_report(report))
    for item in report["skipped"]:
        print(f"WARNING: {item}", file=sys.stderr)
    return 0


def install_harness(args: argparse.Namespace, root: Path) -> int:
    report = repo_harness_install.install_harness_report(
        root,
        repo_harness_install.resolved_target(args.target),
        dry_run=args.dry_run,
        force=args.force,
        profile=args.profile,
        with_features=list(args.with_feature),
        without_features=list(args.without_feature),
        run_setup_check=args.run_setup_check,
        install_rg_portable=args.install_rg_portable,
        bootstrap_local_ai=args.bootstrap_local_ai,
        download_ai_models=args.download_ai_models,
        local_ai_profiles=list(args.local_ai_profile),
        max_download_gb=args.max_download_gb,
    )
    repo_harness_install.print_report(report, args.output_format)
    return 0 if report["ok"] else 1


def harness_status(args: argparse.Namespace, root: Path) -> int:
    try:
        report = repo_harness_update.status_report(
            root,
            check_upstream=bool(args.check_upstream),
            offline=bool(args.offline),
        )
    except (RuntimeError, ValueError) as exc:
        report = {"schema_version": 1, "tool": "harness-status", "ok": False, "status": "blocked", "issues": [str(exc)]}
    repo_harness_update.print_report(report, args.output_format)
    return 0 if report.get("ok") else 1


def harness_update(args: argparse.Namespace, root: Path) -> int:
    try:
        report = repo_harness_update.update_report(
            root,
            requested=args.target_tag,
            apply=bool(args.apply),
            archive=args.archive,
            archive_metadata=args.archive_metadata,
        )
    except (RuntimeError, ValueError) as exc:
        report = {"schema_version": 1, "tool": "harness-update", "ok": False, "status": "blocked", "issues": [str(exc)]}
    repo_harness_update.print_report(report, args.output_format)
    return 0 if report.get("ok") else 1


def harness_rollback(args: argparse.Namespace, root: Path) -> int:
    try:
        report = repo_harness_update.rollback_report(root, transaction=args.transaction)
    except (RuntimeError, ValueError) as exc:
        report = {"schema_version": 1, "tool": "harness-rollback", "ok": False, "status": "blocked", "issues": [str(exc)]}
    repo_harness_update.print_report(report, args.output_format)
    return 0 if report.get("ok") else 1


def harness_adopt(args: argparse.Namespace, root: Path) -> int:
    try:
        report = repo_harness_update.adopt_report(
            root,
            tag=args.tag,
            archive=args.archive,
            archive_metadata=args.archive_metadata,
        )
    except (RuntimeError, ValueError) as exc:
        report = {"schema_version": 1, "tool": "harness-adopt", "ok": False, "status": "blocked", "issues": [str(exc)]}
    repo_harness_update.print_report(report, args.output_format)
    return 0 if report.get("ok") else 1


def harness_release_check(args: argparse.Namespace, root: Path) -> int:
    try:
        report = repo_harness_update.release_tag_report(root, tag=args.tag)
    except (RuntimeError, ValueError) as exc:
        report = {"schema_version": 1, "tool": "harness-release-check", "ok": False, "status": "blocked", "issues": [str(exc)]}
    repo_harness_update.print_report(report, args.output_format)
    return 0 if report.get("ok") else 1


def start_here(args: argparse.Namespace, root: Path) -> int:
    target = repo_harness_install.resolved_target(args.target) if getattr(args, "target", None) else None
    report = repo_onboarding.start_here_report(
        root,
        simple=args.simple,
        profile=args.profile,
        target=target,
        with_features=list(args.with_feature),
        without_features=list(args.without_feature),
    )
    repo_onboarding.print_report(report, args.output_format, repo_onboarding.render_start_here)
    return 0 if report["ok"] else 1


def project_context_review(args: argparse.Namespace, root: Path) -> int:
    report = repo_onboarding.project_context_review_report(
        repo_harness_install.resolved_target(args.target),
        from_request=args.from_request,
        write_review=args.write_review,
    )
    if args.output_format == "json" and (getattr(args, "summary", False) or getattr(args, "compact", False)):
        report = repo_onboarding.summarize_project_context_review_report(report, compact=bool(getattr(args, "compact", False)))
    repo_onboarding.print_report(report, args.output_format, repo_onboarding.render_project_context_review)
    return 0 if report["ok"] else 1


def project_context_apply_review(args: argparse.Namespace, root: Path) -> int:
    report = repo_onboarding.project_context_apply_review_report(
        repo_harness_install.resolved_target(args.target),
        review=Path(args.review) if getattr(args, "review", "") else None,
        apply=args.apply,
    )
    repo_onboarding.print_report(report, args.output_format, repo_onboarding.render_project_context_apply_review)
    return 0 if report["ok"] else 1


def project_kickoff(args: argparse.Namespace, root: Path) -> int:
    report = repo_onboarding.project_kickoff_report(
        root,
        target=repo_harness_install.resolved_target(args.target),
        apply=args.apply,
        from_request=args.from_request,
        profile=args.profile,
        with_features=list(args.with_feature),
        without_features=list(args.without_feature),
    )
    if args.output_format == "json" and (getattr(args, "summary", False) or getattr(args, "compact", False)):
        report = repo_onboarding.summarize_project_kickoff_report(report, compact=bool(getattr(args, "compact", False)))
    repo_onboarding.print_report(report, args.output_format, repo_onboarding.render_project_kickoff)
    return 0 if report["ok"] else 1


def dotnet_context(args: argparse.Namespace, root: Path) -> int:
    target = repo_harness_install.resolved_target(args.target)
    arguments = ["--target", str(target), "--format", args.output_format]
    if getattr(args, "no_cli_probes", False):
        arguments.append("--no-cli-probes")
    if getattr(args, "dotnet_executable", ""):
        arguments.extend(["--dotnet-executable", str(args.dotnet_executable)])
    if getattr(args, "baseline", ""):
        baseline = Path(args.baseline).expanduser()
        if not baseline.is_absolute():
            baseline = target / baseline
        arguments.extend(["--baseline", str(baseline.resolve(strict=False))])
    for solution in getattr(args, "solution", []) or []:
        arguments.extend(["--solution", str(solution)])
    for project in getattr(args, "project", []) or []:
        arguments.extend(["--project", str(project)])
    if getattr(args, "write_evidence", False):
        arguments.append("--write-evidence")
        arguments.extend(["--evidence-dir", str(args.evidence_dir)])
    return repo.run_skill_script(root, "dotnet-project-context", "dotnet_project_context.py", arguments)


def prompt_bool(prompt: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input(f"{prompt} [{suffix}] ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "true", "1"}


def install_wizard(args: argparse.Namespace, root: Path) -> int:
    target = args.target
    profile = args.profile
    run_setup_check = args.run_setup_check
    install_rg_portable = args.install_rg_portable
    bootstrap_local_ai = args.bootstrap_local_ai
    download_ai_models = args.download_ai_models
    apply = args.apply
    if not args.no_input:
        if not target:
            target = input("Target project path: ").strip()
        selected = input(f"Install profile or payload alias ({profile}): ").strip()
        if selected:
            profile = selected
        run_setup_check = prompt_bool("Run setup --check after copying?", True)
        install_rg_portable = prompt_bool("Install verified portable ripgrep?", False)
        bootstrap_local_ai = prompt_bool("Prepare local AI config without model downloads?", False)
        download_ai_models = prompt_bool("Download local AI model payloads now?", False)
        apply = prompt_bool("Run the install now?", False)
    if not target:
        print("install-wizard requires --target when --no-input is used", file=sys.stderr)
        return 2
    report = repo_onboarding.install_wizard_report(
        root,
        target=repo_harness_install.resolved_target(target),
        profile=profile,
        with_features=list(args.with_feature),
        without_features=list(args.without_feature),
        setup_check=run_setup_check,
        install_rg_portable=install_rg_portable,
        bootstrap_local_ai=bootstrap_local_ai,
        download_ai_models=download_ai_models,
        apply=apply,
        force=args.force,
    )
    repo_onboarding.print_report(report, args.output_format, repo_onboarding.render_install_wizard)
    return 0 if report["ok"] else 1


def validate_copy_contract(args: argparse.Namespace, root: Path) -> int:
    report = repo_harness_install.copy_contract_report(
        root,
        profile=args.profile,
        with_features=list(args.with_feature),
        without_features=list(args.without_feature),
    )
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(repo_harness_install.render_copy_contract(report), end="")
    return 0 if report["ok"] else 1


def harness_promote(args: argparse.Namespace, root: Path) -> int:
    report = repo_harness_promote.harness_promote_report(
        root,
        repo_harness_install.resolved_target(args.target),
        profile=args.profile,
        with_features=list(args.with_feature),
        without_features=list(args.without_feature),
        dry_run=args.dry_run or not args.apply,
        apply=args.apply,
        paths=list(args.paths),
    )
    repo_harness_promote.print_report(report, args.output_format)
    return 0 if report["ok"] else 1


def public_export(args: argparse.Namespace, root: Path) -> int:
    report = repo_harness_install.public_export_report(
        root,
        repo_harness_install.resolved_target(args.target),
        profile=args.profile,
        with_features=list(args.with_feature),
        without_features=list(args.without_feature),
        dry_run=args.dry_run,
        force=args.force,
    )
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(repo_harness_install.render_public_export(report), end="")
    return 0 if report["ok"] else 1


def reference_refresh(args: argparse.Namespace, root: Path) -> int:
    command = [
        "--manifest",
        args.manifest,
        "--output-root",
        args.output_root,
        "--workspace-root",
        str(root),
        "--stale-days",
        str(args.stale_days),
        "--format",
        args.output_format,
    ]
    if args.mode == "dry-run":
        command.append("--dry-run")
    elif args.mode == "write":
        command.append("--write")
    if args.no_fetch:
        command.append("--no-fetch")
    if args.allow_reset:
        command.append("--allow-reset")
    return repo.run_skill_script(root, "external-reference-manager", "sync_references.py", command)


def clean_room_validate(args: argparse.Namespace, root: Path) -> int:
    report = repo_prevention.clean_room_validate_report(
        root,
        work_dir=Path(args.work_dir).expanduser() if args.work_dir else None,
        source=args.source,
        keep=not bool(getattr(args, "remove", False)),
        quick=bool(getattr(args, "quick", False)),
    )
    if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
        report = repo_prevention.summarize_prevention_report(report, compact=bool(getattr(args, "compact", False)))
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("# Clean-Room Validation")
        print(f"- Status: {report.get('status')}")
        print(f"- Work dir: `{report.get('work_dir', '')}`")
        print(f"- Evidence: `{report.get('evidence_dir', '')}`")
        for issue in report.get("issues", []):
            print(f"- Issue: {issue}")
    return 0 if report.get("ok") else 1


def environment_preflight(args: argparse.Namespace, root: Path) -> int:
    report = repo_prevention.environment_preflight_report(root)
    if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
        report = {
            "schema_version": report["schema_version"],
            "tool": report["tool"],
            "ok": report["ok"],
            "status": report["status"],
            "summary": report["summary"],
            "issues": report["issues"],
            **({} if getattr(args, "compact", False) else {"skipped": report["skipped"], "paths": report["paths"]}),
        }
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("# Environment Preflight")
        print(f"- Status: {report.get('status')}")
        for issue in report.get("issues", []):
            print(f"- Issue: {issue}")
        for item in report.get("skipped", []):
            print(f"- Optional missing: {item}")
    return 0 if report.get("ok") else 1


def portable_tools(args: argparse.Namespace, root: Path) -> int:
    report = repo_portable_tools.portable_tools_report(
        root,
        require_installed=bool(getattr(args, "require_installed", False)),
    )
    if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
        report = repo_portable_tools.summarize_portable_tools_report(
            report,
            compact=bool(getattr(args, "compact", False)),
        )
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(repo_portable_tools.render_portable_tools_report(report), end="")
    if getattr(args, "check", False) or getattr(args, "require_installed", False):
        return 0 if report.get("ok") else 1
    return 0


def claude_adapter_budget(args: argparse.Namespace, root: Path) -> int:
    report = repo_generated.claude_adapter_budget_report(root)
    if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
        report = repo_generated.summarize_claude_adapter_budget_report(
            report,
            compact=bool(getattr(args, "compact", False)),
        )
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(repo_generated.render_claude_adapter_budget(report), end="")
    return 0 if report.get("ok") else 1


def command_docs_smoke(args: argparse.Namespace, root: Path) -> int:
    report = repo_prevention.command_docs_smoke_report(root)
    if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
        report = {
            "schema_version": report["schema_version"],
            "tool": report["tool"],
            "ok": report["ok"],
            "status": report["status"],
            "checked_command_count": report["checked_command_count"],
            "parse_checked_count": report.get("parse_checked_count", 0),
            "parse_skipped_count": report.get("parse_skipped_count", 0),
            "issue_count": len(report.get("issues", [])),
            **({} if getattr(args, "compact", False) and report.get("ok") else {"issues": report.get("issues", [])}),
        }
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("# Command Docs Smoke")
        print(f"- Status: {report.get('status')}")
        print(f"- Checked manage.py examples: {report.get('checked_command_count')}")
        for issue in report.get("issues", []):
            if isinstance(issue, dict):
                print(f"- {issue.get('path')}:{issue.get('line')}: {issue.get('issue')}")
    return 0 if report.get("ok") else 1


def setup_sync_all(root: Path, check: bool) -> int:
    status = repo_generated.sync_instructions(root, check=check)
    if status != 0:
        return status
    skill_args = ["--root", str(root)]
    if check:
        skill_args.append("--check")
    status, output = repo.run_python_script_quiet(
        repo.skill_manager_script(root, "sync_skill_routing.py"),
        skill_args,
    )
    if status != 0:
        print(output)
        return status
    workflow_args = ["sync-automation-routing", "--root", str(root)]
    if check:
        workflow_args.append("--check")
    status, output = repo.run_python_script_quiet(
        repo.workflow_manager_script(root, "workflow_repo_manager.py"),
        workflow_args,
    )
    if status != 0:
        print(output)
        return status
    return repo_generated.sync_claude_skills(root, check=check)


def setup_validate_repo(root: Path, deep: bool = False) -> int:
    command = [sys.executable, "-B", str(root / ".agents" / "manage.py"), "validate"]
    if deep:
        command.append("--deep")
    completed = subprocess.run(
        command,
        cwd=root,
        check=False,
        env=repo.child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if completed.returncode != 0:
        print(completed.stdout.strip())
    return completed.returncode


def build_setup_report(args: argparse.Namespace, root: Path) -> dict:
    update_action: dict[str, object] | None = None
    interactive_update = (
        not args.check
        and not args.dry_run
        and not bool(getattr(args, "offline", False))
        and args.output_format == "markdown"
        and sys.stdin.isatty()
        and sys.stdout.isatty()
        and (root / repo.HARNESS_INSTALL_MANIFEST_REL).is_file()
    )
    if interactive_update:
        try:
            status = repo_harness_update.status_report(root, check_upstream=True, offline=False)
            update_action = status
            if status.get("update_available"):
                preview = repo_harness_update.update_report(root, requested="latest", apply=False)
                repo_harness_update.print_report(preview, "markdown")
                apply_update = bool(preview.get("ok")) and input(
                    "Apply this complete harness update? [y/N] "
                ).strip().lower() in {"y", "yes"}
                if apply_update:
                    target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
                    applied = repo_harness_update.update_report(
                        root,
                        requested=str(target.get("tag", "")),
                        apply=True,
                        expected_commit=str(target.get("commit", "")),
                        expected_payload_digest=str(target.get("payload_digest", "")),
                    )
                    return {
                        "schema_version": 1,
                        "tool": "setup",
                        "ok": bool(applied.get("ok")),
                        "status": "self-updated" if applied.get("ok") else "self-update-verification-failed",
                        "root": str(root),
                        "checks": ["harness update previewed before confirmation", "updated manager restarted for setup verification"],
                        "navigation": {},
                        "actions": {"harness_update": applied},
                        "linked_skills": {},
                        "skipped": [],
                        "failures": [] if applied.get("ok") else ["updated manager setup verification failed"],
                        "next_prompt": repo_setup.NEXT_PROMPT,
                    }
                update_action = (
                    {**preview, "status": "declined", "apply_requested": False}
                    if preview.get("ok")
                    else preview
                )
        except (RuntimeError, ValueError) as exc:
            update_action = {"ok": True, "status": "check-unavailable", "issues": [str(exc)]}
    report = repo_setup.build_setup_report(
        args,
        root,
        sync_all_func=setup_sync_all,
        validate_func=lambda repo_root: setup_validate_repo(repo_root, deep=False),
        deep_validate_func=lambda repo_root: setup_validate_repo(repo_root, deep=True),
    )
    if update_action is not None:
        report["actions"]["harness_update"] = update_action
        if update_action.get("status") == "declined":
            report["skipped"].append("available harness update was previewed and declined (default No)")
        elif update_action.get("status") == "check-unavailable":
            report["skipped"].append("interactive harness update check was unavailable; setup continued")
    if getattr(args, "doctor", False):
        repo_doctor.add_setup_doctor_actions(report, root)
    return report


from repo_support.repo_cli_parser import add_examples, add_parser, add_shared_root_arg, build_parser
def main() -> int:
    repo.require_supported_python()
    raw_args = repo_public_commands.normalize_public_commands(sys.argv[1:])
    if raw_args and raw_args[0] in repo.WORKFLOW_COMMANDS:
        root, workflow_args = workflow_command_from_raw(raw_args)
        if raw_args[0] == "validate-automations":
            return repo_local_ai.run_with_failure_triage(
                root,
                "validate-automations",
                lambda: repo.run_workflow_repo_manager(root, workflow_args),
            )
        return repo.run_workflow_repo_manager(root, workflow_args)

    parser = build_parser()
    args = parser.parse_args(raw_args)
    root = repo.repo_root(args.root)
    repo_command_metrics.configure_policy_root(root)

    if args.command == "validate":
        if getattr(args, "tier", None) == "fast":
            return repo_health.check_repo_health(root, as_json=False)
        if getattr(args, "tier", None) == "release":
            return repo_doctor.release_evidence(
                argparse.Namespace(output_format="markdown", skip_fresh_clone=False, source="local"),
                root,
            )
        if getattr(args, "tier", None) == "normal":
            args.deep = False
        if getattr(args, "tier", None) == "deep":
            args.deep = True
        if getattr(args, "deep", False):
            return repo_local_ai.run_with_failure_triage(
                root,
                "check --deep",
                lambda: repo_health.deep_validate_repo(root),
            )
        return repo_local_ai.run_with_failure_triage(
            root,
            "check",
            lambda: repo_health.validate_repo(root),
        )
    qol_status = repo_qol.handle_qol_command(args, root)
    if qol_status is not None:
        return qol_status
    if args.command == "check-repo-health":
        if args.json or getattr(args, "summary", False) or getattr(args, "compact", False):
            return repo_health.check_repo_health(
                root,
                as_json=bool(args.json),
                summary=bool(getattr(args, "summary", False) or getattr(args, "compact", False)),
            )
        return repo_local_ai.run_with_failure_triage(
            root,
            "check-repo-health",
            lambda: repo_health.check_repo_health(root, as_json=False),
        )
    if args.command == "setup":
        report = build_setup_report(args, root)
        summarized = bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False))
        if summarized:
            report = repo_setup.setup_summary(report, compact=bool(getattr(args, "compact", False)))
        if args.output_format == "json":
            repo_setup.print_json(report)
        elif summarized:
            print(repo_setup.render_setup_summary(report))
        else:
            print(repo_setup.render_setup_report(report))
        return repo_setup.setup_status(report)
    if args.command == "commands":
        shortcut = next(
            (name for name, attr in (
                ("first-time", "first_time"), ("daily", "daily"), ("failure", "failure"),
                ("documents", "documents"), ("harness", "harness"), ("workflow", "workflow"), ("release", "release"),
            ) if getattr(args, attr, False)),
            None,
        )
        return print_commands(
            parser,
            args.output_format,
            root=root,
            shortcut=shortcut,
            write_path=args.write,
            summary=bool(args.summary),
            compact=bool(getattr(args, "compact", False)),
        )
    if args.command == "cost-policy":
        return repo_cost_policy.cost_policy_command(args, root)
    if args.command == "policy":
        return repo_policy.policy_command(args, root)
    if args.command == "determinism-check":
        return repo_determinism.determinism_check_command(args, root)
    if args.command == "explain-route":
        return repo_routing.explain_route_command(args, root)
    if args.command == "which-skill":
        return repo_routing.which_skill_command(args, root)
    if args.command == "which-workflow":
        return repo_routing.which_workflow_command(args, root)
    if args.command == "fresh-clone-smoke":
        return repo_doctor.fresh_clone_smoke(args, root)
    if args.command == "clean-room-validate":
        return clean_room_validate(args, root)
    if args.command == "environment-preflight":
        return environment_preflight(args, root)
    if args.command == "portable-tools":
        return portable_tools(args, root)
    if args.command == "command-docs-smoke":
        return command_docs_smoke(args, root)
    if args.command == "install-harness-smoke":
        return repo_doctor.install_harness_smoke(args, root)
    if args.command == "install-harness":
        return install_harness(args, root)
    if args.command == "harness-status":
        return harness_status(args, root)
    if args.command == "harness-update":
        return harness_update(args, root)
    if args.command == "harness-rollback":
        return harness_rollback(args, root)
    if args.command == "harness-adopt":
        return harness_adopt(args, root)
    if args.command == "harness-release-check":
        return harness_release_check(args, root)
    if args.command == "install-wizard":
        return install_wizard(args, root)
    if args.command == "start-here":
        return start_here(args, root)
    if args.command == "project-context-review":
        return project_context_review(args, root)
    if args.command == "project-context-apply-review":
        return project_context_apply_review(args, root)
    if args.command == "project-kickoff":
        return project_kickoff(args, root)
    if args.command == "dotnet-context":
        return dotnet_context(args, root)
    if args.command == "validate-copy-contract":
        return validate_copy_contract(args, root)
    if args.command == "harness-promote":
        return harness_promote(args, root)
    if args.command == "public-export":
        return public_export(args, root)
    if args.command == "release-evidence":
        return repo_doctor.release_evidence(args, root)
    if args.command == "reference-refresh":
        return reference_refresh(args, root)
    if args.command == "check-changed":
        return repo_local_ai.run_with_failure_triage(
            root,
            "check-changed",
            lambda: repo_changed.check_changed(args, root),
            json_stdout=str(getattr(args, "format", "")) == "json",
        )
    if args.command == "review-packet":
        return repo_changed.review_packet_command(args, root)
    if args.command == "handoff-packet":
        return repo_changed.handoff_packet_command(args, root)
    if args.command == "fresh-agent-packet":
        return repo_changed.fresh_agent_packet_command(args, root)
    if args.command == "portable-constraints":
        return repo_portability.portability_command(args, root)
    if args.command == "check-additions":
        return repo_local_ai.run_with_failure_triage(
            root,
            "check-additions",
            lambda: repo_changed.check_additions(args, root),
            json_stdout=str(getattr(args, "format", "")) == "json",
        )
    if args.command == "sync":
        if args.check:
            return repo_generated.sync_all(root, check=True)
        return repo_generated.sync_all(root, check=False)
    if args.command == "format-json":
        report = repo_health.format_json_files(root, check=bool(args.check))
        if getattr(args, "summary", False) or getattr(args, "compact", False):
            report = {
                "schema_version": report["schema_version"],
                "tool": report["tool"],
                "ok": report["ok"],
                "status": report["status"],
                "checked": report["checked"],
                "changed_count": len(report.get("changed", [])),
                "invalid_count": len(report.get("invalid", [])),
                "changed": [] if getattr(args, "compact", False) else report.get("changed", []),
                "invalid": report.get("invalid", []),
            }
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print("# JSON Formatting")
            print()
            print(f"- Status: {report['status']}")
            print(f"- Checked: {report['checked']}")
            print(f"- Changed: {len(report.get('changed', []))}")
            if report.get("invalid"):
                print()
                print("## Invalid")
                for item in report["invalid"]:
                    print(f"- {item}")
            if report.get("changed"):
                print()
                print("## Changed")
                for item in report["changed"]:
                    print(f"- {item}")
        return 0 if report["ok"] else 1
    if args.command == "syntax-check":
        return repo_syntax.syntax_check_command(args, root)
    if args.command == "local-ai":
        return local_ai(args, root)
    if args.command == "benchmark":
        return repo_doctor.benchmark_group(args, root)
    if args.command == "feedback":
        return repo_feedback.feedback_group(args.feedback_args, root)
    if args.command == "skill":
        return repo_doctor.skill_group(args, root, review_skill_command.review_skill)
    if args.command == "workflow":
        return repo_doctor.workflow_group(args, root)
    if args.command == "sync-instructions":
        return repo_generated.sync_instructions(root, check=args.check)
    if args.command == "sync-skill-routing":
        return repo_generated.sync_skill_routing(root, check=args.check, deep=args.deep)
    if args.command in repo.WORKFLOW_COMMANDS:
        return forward_workflow_command(args, root)
    if args.command == "sync-claude-skills":
        return repo_generated.sync_claude_skills(root, check=args.check)
    if args.command == "claude-adapter-budget":
        return claude_adapter_budget(args, root)
    if args.command == "compare-skill":
        return compare_skill(args, root)
    if args.command == "inspect-skill":
        return inspect_skill(args, root)
    if args.command == "review-skill":
        return review_skill_command.review_skill(args, root)
    if args.command == "analyze-location":
        return analyze_location(args, root)
    if args.command == "upgrade-skill":
        return upgrade_skill(args, root)
    if args.command == "eval-skill":
        return eval_skill(args, root)
    if args.command == "attest-skill":
        return attest_skill(args, root)
    if args.command == "validate-agent-compatibility":
        return validate_agent_compatibility(args, root)
    if args.command == "skill-inventory":
        return skill_inventory(args, root)
    if args.command == "triage-candidates":
        return triage_candidates(args, root)
    if args.command == "audit-candidate-source":
        return audit_candidate_source(args, root)
    if args.command == "measure-skill-budget":
        return measure_skill_budget(args, root)
    if args.command == "audit-skill-determinism":
        report = audit_skill_determinism.build_report(root, skill=args.skill, all_skills=args.all)
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = audit_skill_determinism.summarize_report(report, compact=bool(getattr(args, "compact", False)))
        if args.output_format == "json":
            json.dump(report, sys.stdout, indent=2, sort_keys=True)
            print()
        else:
            print(audit_skill_determinism.render_markdown(report), end="")
        return 1 if args.strict and not report["ok"] else 0
    if args.command == "new-skill-checklist":
        return new_skill_checklist(args, root)
    if args.command == "link-skills":
        return link_skills(args, root)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
