"""Cross-skill and workflow optimization diagnostics."""

from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any

import audit_skill_determinism
import measure_skill_budget
import skill_inventory
import skill_manager_common as common
import validate_skill
from repo_support import repo_cost_policy
from repo_support import repo_policy

PLACEHOLDER_RE = re.compile(r"\b(TBD|TODO|FIXME|LOREM IPSUM)\b|\{\{|<placeholder", re.IGNORECASE)
STARTUP_CONTEXT_TRIGGER_PATHS = {
    "AGENTS.md",
    ".aider.conf.yml",
    ".continue/rules/repository-instructions.md",
    ".github/copilot-instructions.md",
    ".claude/CLAUDE.md",
    "GEMINI.md",
    ".agents/local-ai.json",
    ".agents/routing.md",
    "automations/routing.md",
    ".agents/skills/skill-manager/scripts/repo_manager.py",
    ".agents/skills/skill-manager/scripts/repo_support/repo_cli_parser.py",
    ".agents/skills/skill-manager/scripts/repo_support/repo_qol.py",
    ".agents/skills/skill-manager/scripts/repo_support/repo_qol_daily.py",
    ".agents/skills/skill-manager/scripts/repo_support/repo_cost_policy.py",
}
STARTUP_CONTEXT_TRIGGER_PREFIXES = (
    ".agents/skills/skill-manager/scripts/repo_support/repo_qol",
    ".agents/skills/skill-manager/scripts/repo_support/repo_review",
)
STARTUP_CONTEXT_TRIGGER_SUPPORT_FILES = {
    ".agents/skills/skill-manager/scripts/repo_support/repo_command_metrics.py",
    ".agents/skills/skill-manager/scripts/repo_support/repo_context_guardrails.py",
    ".agents/skills/skill-manager/scripts/repo_support/repo_navigation_status.py",
}


def accepted_skill_dirs(root: Path) -> list[Path]:
    return common.discover_skill_dirs(root)


def skill_dir(root: Path, name_or_path: str) -> Path:
    raw = Path(name_or_path)
    if raw.is_absolute():
        return raw
    if raw.parts and raw.parts[0] in {".agents", "agents"}:
        return root / raw
    return root / ".agents" / "skills" / name_or_path


def selected_skill_dirs(root: Path, names: list[str] | None = None) -> list[Path]:
    return [skill_dir(root, name) for name in names] if names else accepted_skill_dirs(root)


def compact_command_output(output: str, *, max_chars: int | None = None, max_lines: int | None = None, root: Path | None = None) -> dict[str, object]:
    policy_root = repo_policy.project_root(root)
    max_chars = max_chars or repo_policy.int_value(policy_root, "output_profiles.compact_command.max_chars")
    max_lines = max_lines or repo_policy.int_value(policy_root, "output_profiles.compact_command.max_lines")
    lines = output.splitlines()
    if len(lines) > max_lines and max_lines >= 3:
        head_count = max(1, (max_lines - 1) // 2)
        tail_count = max_lines - head_count - 1
        omitted_count = len(lines) - head_count - tail_count
        selected = lines[:head_count] + [f"... {omitted_count} lines omitted ..."] + lines[-tail_count:]
    elif len(lines) > max_lines and max_lines == 2:
        selected = [lines[0], lines[-1]]
    elif len(lines) > max_lines:
        selected = lines[-max_lines:]
    else:
        selected = lines[:max_lines]
    summary = "\n".join(selected)
    truncated = len(lines) > max_lines or len(summary) > max_chars
    if len(summary) > max_chars:
        marker = "\n... output truncated ...\n"
        if max_chars <= len(marker):
            summary = summary[-max(0, max_chars):] if max_chars > 0 else ""
        else:
            available = max_chars - len(marker)
            head_chars = available // 2
            tail_chars = available - head_chars
            summary = summary[:head_chars].rstrip() + marker + summary[-tail_chars:].lstrip()
    return {
        "summary": summary,
        "line_count": len(lines),
        "char_count": len(output),
        "truncated": truncated,
    }


def command_row(
    order: int,
    command: str,
    reason: str,
    *,
    owner: str,
    required: bool = True,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "check_id": f"{owner}:{hashlib.sha256(command.encode('utf-8')).hexdigest()[:12]}",
        "order": order,
        "command": command,
        "reason": reason,
        "owner": owner,
        "required": required,
    }
    if extra:
        row.update(extra)
    return row


def quote_command_arg(value: str) -> str:
    if re.search(r'\s|"', value):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def changed_validation_plan(root: Path, paths: list[str], scope: dict[str, object], *, deep: bool = False) -> list[dict[str, object]]:
    plan: list[dict[str, object]] = []
    order = 1

    def add(
        command: str,
        reason: str,
        owner: str,
        required: bool = True,
        extra: dict[str, object] | None = None,
    ) -> None:
        nonlocal order
        plan.append(command_row(order, command, reason, owner=owner, required=required, extra=extra))
        order += 1

    add("python -B .agents/manage.py check-additions", "changed or new files must have an owning contract", "skill-manager")
    existing_paths = [path for path in paths if (root / path).is_file()]
    python_paths = [
        str(item)
        for item in scope.get("python_paths", []) or []
        if str(item).strip() and (root / str(item)).is_file()
    ]
    if python_paths:
        command_paths = " ".join(quote_command_arg(path) for path in python_paths)
        add(
            f"python -B .agents/manage.py syntax-check --paths {command_paths} --format json",
            "changed Python files must parse without writing bytecode caches",
            "skill-manager",
        )
    if scope.get("instructions"):
        add("python -B .agents/manage.py sync-instructions --check", "instruction adapters changed", "skill-manager")
    if startup_context_inputs_changed(root, paths):
        add(
            "python -B .agents/manage.py startup-context --baseline-ref HEAD --summary --compact --format json",
            "always-loaded or startup-context inputs changed; compare token budget against HEAD",
            "skill-manager",
        )
        add(
            "python -B .agents/manage.py context-cost-benchmark --summary --compact --format json",
            "context routing changed; compare raw diff, startup guidance, and next-action route costs",
            "skill-manager",
        )
        add(
            "python -B .agents/manage.py command-budget-check --summary --compact --format json",
            "low-context command surfaces changed; verify compact latency and output budgets did not regress",
            "skill-manager",
        )
    for skill_name in sorted(scope.get("skill_names", set()) or []):
        add(
            f"python -B .agents/skills/skill-manager/scripts/validate_skill.py .agents/skills/{skill_name}",
            f"skill source changed for {skill_name}",
            "skill-manager",
        )
    if scope.get("skill_names") or scope.get("skills_generated"):
        add("python -B .agents/manage.py sync-skill-routing --check", "skill routing or skill source changed", "skill-manager")
        add("python -B .agents/manage.py sync-claude-skills --check", "skill adapters must stay generated", "skill-manager")
    if scope.get("skill_names") and not deep:
        add("python -B .agents/manage.py check-changed --deep --format json", "changed skill self-tests are available", "skill-manager", required=False)
    elif scope.get("skill_names") and deep:
        for skill_name in sorted(scope.get("skill_names", set()) or []):
            selection = self_test_selection_for_skill(str(skill_name), existing_paths)
            add(
                self_test_command_for_skill(str(skill_name), existing_paths),
                f"deep changed-scope self-tests for {skill_name}",
                "skill-manager",
                extra={"self_test_selection": selection},
            )
    if scope.get("workflows") or scope.get("workflow_generated"):
        add("python -B .agents/manage.py validate-automations --strict-phase-quality", "workflow source or generated routing changed", "workflow-manager")
        add("python -B .agents/manage.py sync-automation-routing --check", "workflow routing must stay generated", "workflow-manager")
    if scope.get("repo_surface"):
        add("python -B .agents/manage.py check-repo-health --json --summary --compact", "repository-level surfaces changed", "skill-manager")
    if scope.get("docs"):
        add("python -B .agents/manage.py check", "root docs changed; run the aggregate repository gate before finalizing", "skill-manager", required=False)
    return plan


def startup_context_inputs_changed(root: Path, paths: list[str]) -> bool:
    policy, _error = repo_cost_policy.load_cost_policy(root)
    configured = set(
        repo_cost_policy.configured_paths(
            policy,
            "always_loaded_files",
            repo_cost_policy.LOW_CONTEXT_FILES,
        )
    )
    configured.update(
        repo_cost_policy.configured_paths(
            policy,
            "beginner_loaded_files",
            repo_cost_policy.BEGINNER_CONTEXT_FILES,
        )
    )
    triggers = set(STARTUP_CONTEXT_TRIGGER_PATHS)
    triggers.update(path.replace("\\", "/") for path in configured)
    for path in paths:
        value = path.replace("\\", "/")
        if value in triggers or value in STARTUP_CONTEXT_TRIGGER_SUPPORT_FILES:
            return True
        if value.startswith(STARTUP_CONTEXT_TRIGGER_PREFIXES):
            return True
    return False


def validation_plan_summary(plan: list[dict[str, object]]) -> dict[str, object]:
    owners: dict[str, int] = {}
    for item in plan:
        owner = str(item.get("owner") or "unknown")
        owners[owner] = owners.get(owner, 0) + 1
    return {
        "command_count": len(plan),
        "required_count": sum(1 for item in plan if item.get("required") is not False),
        "optional_count": sum(1 for item in plan if item.get("required") is False),
        "owners": owners,
    }


def full_self_tests_required_for_path(skill_name: str, path: str) -> bool:
    value = path.replace("\\", "/")
    prefix = f".agents/skills/{skill_name}/"
    if not value.startswith(prefix):
        return False
    if value.endswith("scripts/run_self_tests.py"):
        return True
    return value.startswith(f"{prefix}scripts/") and value.endswith(".py")


def _mark_skill_manager_support_path(path: str) -> list[str]:
    if path.endswith("repo_addition_acceptance.py"):
        return ["addition_acceptance", "check_additions"]
    if path.endswith("repo_changed_summary.py"):
        return ["check_changed_summary"]
    if path.endswith("repo_changed_git.py"):
        return ["changed_file_statuses", "changed_path_token_estimates", "check_changed"]
    if path.endswith("repo_command_metrics.py"):
        return ["output_budget", "latency", "next_action_summary", "command_budget", "validation_progress"]
    if path.endswith("repo_fingerprint.py"):
        return ["input_fingerprint", "validation_progress", "finish"]
    if path.endswith("repo_context_guardrails.py"):
        return ["context_guardrails"]
    if path.endswith("repo_portability.py"):
        return ["portable_constraints"]
    if path.endswith("repo_qol_context.py"):
        return ["next_action", "review_loop", "review_autopilot", "changed_context", "context_cost_benchmark"]
    if path.endswith("repo_changed.py"):
        return ["check_changed", "planned_command_timeout", "run_capture_timeout"]
    if path.endswith("repo_qol_costs.py"):
        return ["context_cost_benchmark"]
    if path.endswith("repo_qol_dashboard.py"):
        return ["dashboard"]
    if path.endswith("repo_qol_triage.py"):
        return ["what_now", "triage"]
    if path.endswith("repo_qol_daily.py"):
        return ["startup_context", "changed_evidence", "clean_context"]
    if path.endswith("repo_qol_finish.py"):
        return ["finish", "finish_claim", "claim_receipt"]
    if path.endswith("repo_qol_finish_packets.py"):
        return ["finish", "finish_summary", "claim_receipt"]
    if path.endswith("repo_qol_readiness.py"):
        return ["finish_claim", "claim_receipt"]
    if path.endswith("repo_qol_parsers.py"):
        return ["repo_cli_parser", "repo_qol_parser", "daily_commands"]
    if path.endswith("repo_qol_render.py"):
        return ["dashboard_summary", "finish_summary"]
    if path.endswith("repo_review_costs.py"):
        return ["review_cost_report", "review_cost_ledger"]
    if path.endswith("repo_review_hunks.py") or path.endswith("repo_review_packet.py"):
        return ["review_packet"]
    if path.endswith("repo_review_progress.py"):
        return ["review_progress", "review_plan", "review_loop"]
    if path.endswith("repo_cost_policy.py"):
        return ["cost_policy", "review_cost_ledger"]
    if path.endswith("repo_health.py") or path.endswith("repo_health_surface.py"):
        return ["repo_health"]
    if path.endswith("repo_harness_install.py"):
        return ["install_harness"]
    if path.endswith("repo_local_ai.py"):
        return ["local_ai", "failure_triage"]
    if path.endswith("repo_prevention.py"):
        return ["clean_room", "command_docs"]
    if path.endswith("repo_routing.py"):
        return ["which_skill", "which_workflow", "routing"]
    if path.endswith("self_tests/test_optimizations.py"):
        return ["focused_self_test_matches", "changed_validation_plan"]
    return []


def focused_self_test_matches(skill_name: str, changed_paths: list[str]) -> list[str]:
    normalized = [path.replace("\\", "/") for path in changed_paths]
    prefix = f".agents/skills/{skill_name}/"
    scoped = [path for path in normalized if path.startswith(prefix)]
    matches: list[str] = []
    full_required = False

    def add(*values: str) -> None:
        for value in values:
            if value not in matches:
                matches.append(value)

    for path in scoped:
        path_matched = False

        def mark(*values: str) -> None:
            nonlocal path_matched
            path_matched = True
            add(*values)

        if skill_name == "skill-manager":
            if path.endswith("measure_skill_budget.py"):
                mark("measure_skill_budget")
            elif path.endswith("repo_benchmark.py"):
                mark("benchmark_")
            elif path.endswith("repo_changed.py"):
                mark("check_changed", "planned_command_timeout", "run_capture_timeout")
            elif path.endswith("repo_doctor_groups.py"):
                mark("skill_handoff", "check_changed", "measure_skill_budget")
            elif path.endswith("repo_optimizations.py"):
                mark(
                    "changed_validation_plan",
                    "deep_self_test_commands_focus_slow_changed_skill_owners",
                    "focused_self_test_matches",
                )
            elif path.endswith("repo_commands.py"):
                mark("test_commands_")
            elif path.endswith("repo_cli_parser.py"):
                mark("repo_cli_parser")
            else:
                support_matches = _mark_skill_manager_support_path(path)
                if support_matches:
                    mark(*support_matches)
        elif skill_name == "agent-benchmarking":
            if path.endswith("benchmark_common.py") or "/scripts/support/benchmark_common_" in path:
                mark("record_result", "standard_metrics", "compare_runs_optimization_gate")
        elif skill_name == "local-ai-helper":
            if path.endswith("local_ai_support/setup_impl.py") or path.endswith("local_ai_support/setup_catalog.py"):
                mark("bootstrap_no_download", "bootstrap_json", "setup_parser", "setup_catalog")
        elif skill_name == "workflow-manager":
            if path.endswith("workflow_support/analytics.py"):
                mark("workflow_analytics")
            elif path.endswith("create_workflow.py"):
                mark("create_workflow")
            elif path.endswith("assets/workflow-template/WORKFLOW.md"):
                mark("asset_workflow_template")
            elif path.endswith("workflow_plan_check.py") or path.endswith("workflow_support/story_bug_quality.py"):
                mark("plan_check")
            elif path.endswith("workflow_support/run_story_bug.py"):
                mark("story_bug")
            elif path.endswith("workflow_support/run_common.py"):
                mark("reusable_lessons", "generic")
            elif path.endswith("workflow_support/run_render.py"):
                mark("progress_document")
            elif path.endswith("workflow_context_packet.py"):
                mark("context_packet")
            elif path.endswith("workflow_run_support.py"):
                mark("resume_", "finish_", "start_")
            elif path.endswith("workflow_support/scorecard.py"):
                mark("scorecard")
            elif path.endswith("workflow_support/smoke.py"):
                mark("smoke_workflows", "lifecycle_smoke")
            elif path.endswith("workflow_support/cli_parser.py"):
                mark("cli_parser")
        if not path_matched and full_self_tests_required_for_path(skill_name, path):
            full_required = True
    return [] if full_required else matches


def self_test_selection_for_skill(skill_name: str, paths: list[str]) -> dict[str, object]:
    normalized = [path.replace("\\", "/") for path in paths]
    prefix = f".agents/skills/{skill_name}/"
    scoped = [path for path in normalized if path.startswith(prefix)]
    matches = focused_self_test_matches(skill_name, paths)
    full_required_paths = [
        path
        for path in scoped
        if full_self_tests_required_for_path(skill_name, path) and not focused_self_test_matches(skill_name, [path])
    ]
    if full_required_paths:
        mode = "full"
    elif matches:
        mode = "focused"
    elif scoped:
        mode = "none"
    else:
        mode = "not-applicable"
    return {
        "skill": skill_name,
        "mode": mode,
        "matches": matches,
        "full_required_paths": full_required_paths,
        "reason": (
            "test-runner-or-unmapped-script-changed"
            if full_required_paths
            else "mapped-focused-self-tests"
            if matches
            else "no-scoped-self-test-impact"
        ),
    }


def self_test_command_for_skill(skill_name: str, paths: list[str]) -> str:
    command = f"python -B .agents/skills/{skill_name}/scripts/run_self_tests.py"
    matches = focused_self_test_matches(skill_name, paths)
    for match in matches:
        command += f" --match {match}"
    return command


def skill_handoff_packet(root: Path, name_or_path: str) -> dict[str, object]:
    target = skill_dir(root, name_or_path)
    manifest, manifest_error = common.load_skill_manifest(target)
    errors, warnings = validate_skill.validate_skill(target)
    budget = measure_skill_budget.measure_skill(target, root)
    audit = audit_skill_determinism.audit_skill(root, target)
    metadata, _metadata_error = common.parse_frontmatter_file(target / "SKILL.md")
    name = str((metadata or {}).get("name") or target.name)
    rel = common.relative(root, target)
    required_context = [
        f"{rel}/SKILL.md",
        f"{rel}/module.json",
    ]
    validation_plan = [
        f"python -B .agents/skills/skill-manager/scripts/validate_skill.py {rel}",
        f"python -B .agents/manage.py measure-skill-budget --skill {rel} --format json",
        f"python -B .agents/manage.py audit-skill-determinism --skill {rel} --format json",
    ]
    if (target / "scripts" / "run_self_tests.py").exists():
        validation_plan.append(f"python -B .agents/skills/{target.name}/scripts/run_self_tests.py")
    for suite in quality_eval_suite_paths(manifest):
        validation_plan.append(f"python -B .agents/manage.py eval-skill --skill {rel} --suite {rel}/{suite} --format json")
    risks = []
    if manifest_error:
        risks.append(manifest_error)
    risks.extend(errors)
    risks.extend(warnings)
    risks.extend(audit.get("issues", []))
    if budget.get("skill_md", {}).get("status") != "ok":
        risks.append("SKILL.md budget is not ok")
    return {
        "schema_version": 1,
        "tool": "skill-manager.skill-handoff",
        "ok": not errors,
        "status": "ok" if not errors else "issues-found",
        "skill": name,
        "path": rel,
        "required_next_context": required_context,
        "validation_plan": validation_plan,
        "budget": {
            "skill_md_words": budget.get("skill_md", {}).get("words", 0),
            "status": budget.get("skill_md", {}).get("status", "unknown"),
        },
        "determinism": {
            "ok": audit.get("ok", False),
            "issue_count": len(audit.get("issues", [])),
            "warning_count": len(audit.get("warnings", [])),
        },
        "remaining_risks": risks,
        "next_command": validation_plan[0],
    }


def summarize_skill_handoff(report: dict[str, object], *, compact: bool = False) -> dict[str, object]:
    risks = report.get("remaining_risks") if isinstance(report.get("remaining_risks"), list) else []
    context = report.get("required_next_context") if isinstance(report.get("required_next_context"), list) else []
    validation = report.get("validation_plan") if isinstance(report.get("validation_plan"), list) else []
    output: dict[str, object] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.skill-handoff"),
        "ok": bool(report.get("ok")),
        "status": report.get("status", ""),
        "skill": report.get("skill", ""),
        "path": report.get("path", ""),
        "summary": {
            "required_context_count": len(context),
            "validation_command_count": len(validation),
            "remaining_risk_count": len(risks),
        },
        "next_command": report.get("next_command", ""),
    }
    if risks or not compact:
        output["remaining_risks"] = risks
    if not compact:
        output["required_next_context"] = context
        output["validation_plan"] = validation
        output["budget"] = report.get("budget", {})
        output["determinism"] = report.get("determinism", {})
    return output


def score_item(name: str, ok: bool, points: int, details: dict[str, object] | None = None) -> dict[str, object]:
    return {"name": name, "ok": ok, "points": points if ok else 0, "max_points": points, "details": details or {}}


def quality_eval_suite_paths(manifest: dict[str, Any] | None) -> list[str]:
    quality = manifest.get("quality") if isinstance(manifest, dict) and isinstance(manifest.get("quality"), dict) else {}
    values = quality.get("eval_suites") if isinstance(quality, dict) and isinstance(quality.get("eval_suites"), list) else []
    paths: list[str] = []
    for value in values:
        if isinstance(value, dict):
            path = str(value.get("path") or "").strip()
        else:
            path = str(value).strip()
        if path:
            paths.append(path)
    return paths


def skill_scorecard(root: Path, names: list[str] | None = None) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for target in selected_skill_dirs(root, names):
        manifest, manifest_error = common.load_skill_manifest(target)
        errors, warnings = validate_skill.validate_skill(target)
        budget = measure_skill_budget.measure_skill(target, root)
        audit = audit_skill_determinism.audit_skill(root, target)
        suites = quality_eval_suite_paths(manifest)
        suite_paths = [target / str(suite) for suite in suites]
        checks = [
            score_item("validation", not errors and manifest_error is None, 30, {"errors": errors, "manifest_error": manifest_error or ""}),
            score_item("budget", budget.get("skill_md", {}).get("status") == "ok", 20, {"skill_md": budget.get("skill_md", {})}),
            score_item("self-tests", (target / "scripts" / "run_self_tests.py").exists(), 15),
            score_item("eval-suites", bool(suite_paths) and all(path.exists() for path in suite_paths), 20, {"declared": len(suite_paths)}),
            score_item("determinism", not audit.get("issues"), 15, {"issues": audit.get("issues", []), "warnings": audit.get("warnings", [])}),
        ]
        score = sum(int(item["points"]) for item in checks)
        gap_report = skill_eval_gap(root, [target.name])
        gap_rows = gap_report.get("skills") if isinstance(gap_report.get("skills"), list) else []
        gap_row = gap_rows[0] if gap_rows and isinstance(gap_rows[0], dict) else {}
        missing_assertions = gap_row.get("missing_assertions") if isinstance(gap_row.get("missing_assertions"), list) else []
        missing_suites = gap_row.get("missing_suites") if isinstance(gap_row.get("missing_suites"), list) else []
        rows.append(
            {
                "skill": target.name,
                "path": common.relative(root, target),
                "ok": score == 100,
                "score": score,
                "max_score": 100,
                "percent": score,
                "checks": checks,
                "advisory_eval_gap": {
                    "ok": not missing_assertions and not missing_suites,
                    "missing_assertion_count": len(missing_assertions),
                    "missing_suite_count": len(missing_suites),
                    "missing_assertions": missing_assertions,
                    "missing_suites": missing_suites,
                },
                "next_command": f"python -B .agents/manage.py skill handoff --skill {target.name} --format json",
            }
        )
    eval_gap_count = sum(
        int(row.get("advisory_eval_gap", {}).get("missing_assertion_count", 0))
        + int(row.get("advisory_eval_gap", {}).get("missing_suite_count", 0))
        for row in rows
        if isinstance(row.get("advisory_eval_gap"), dict)
    )
    return {
        "schema_version": 1,
        "tool": "skill-manager.scorecard",
        "ok": all(row.get("ok") for row in rows),
        "status": "passed" if all(row.get("ok") for row in rows) else "warning",
        "summary": {
            "skill_count": len(rows),
            "passing": sum(1 for row in rows if row.get("ok")),
            "failing": sum(1 for row in rows if not row.get("ok")),
            "minimum_percent": min([int(row.get("percent", 0)) for row in rows], default=0),
            "eval_gap_count": eval_gap_count,
        },
        "skills": rows,
    }


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def assertion_types(suite_path: Path) -> set[str]:
    suite = load_json(suite_path)
    cases = suite.get("evals") or suite.get("cases") or []
    values: set[str] = set()
    for case in cases if isinstance(cases, list) else []:
        if not isinstance(case, dict):
            continue
        assertions = case.get("assertions") if isinstance(case.get("assertions"), list) else []
        for assertion in assertions:
            if isinstance(assertion, dict) and assertion.get("type"):
                values.add(str(assertion["type"]))
    return values


def skill_eval_gap(root: Path, names: list[str] | None = None) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    expected = {"validation_ok", "risk_profile_covers_flags", "trigger_quality"}
    for target in selected_skill_dirs(root, names):
        manifest, _error = common.load_skill_manifest(target)
        suites = quality_eval_suite_paths(manifest)
        actual: set[str] = set()
        missing_suites: list[str] = []
        for suite in suites:
            suite_path = target / str(suite)
            if suite_path.exists():
                actual.update(assertion_types(suite_path))
            else:
                missing_suites.append(str(suite))
        gaps = sorted(expected - actual)
        rows.append(
            {
                "skill": target.name,
                "declared_suite_count": len(suites),
                "missing_suites": missing_suites,
                "covered_assertions": sorted(actual),
                "missing_assertions": gaps,
                "ok": not gaps and not missing_suites,
            }
        )
    return {
        "schema_version": 1,
        "tool": "skill-manager.eval-gap",
        "ok": True,
        "status": "ok" if all(row.get("ok") for row in rows) else "gaps-found",
        "summary": {
            "skill_count": len(rows),
            "gap_count": sum(len(row.get("missing_assertions", [])) + len(row.get("missing_suites", [])) for row in rows),
        },
        "skills": rows,
    }


def workflow_eval_gap(root: Path, workflow_names: list[str] | None = None) -> dict[str, object]:
    names = workflow_names or [path.parent.name for path in sorted((root / "automations").glob("*/module.json"))]
    expected = {"validation_ok", "workflow_lifecycle_smoke_ok"}
    rows: list[dict[str, object]] = []
    for name in names:
        module_dir = root / "automations" / name
        suites = sorted((module_dir / "suites").glob("*.json"))
        actual: set[str] = set()
        for suite in suites:
            actual.update(assertion_types(suite))
        gaps = sorted(expected - actual)
        rows.append(
            {
                "workflow": name,
                "suite_count": len(suites),
                "covered_assertions": sorted(actual),
                "missing_assertions": gaps,
                "ok": not gaps,
            }
        )
    return {
        "schema_version": 1,
        "tool": "workflow-manager.eval-gap",
        "ok": True,
        "status": "ok" if all(row.get("ok") for row in rows) else "gaps-found",
        "summary": {
            "workflow_count": len(rows),
            "gap_count": sum(len(row.get("missing_assertions", [])) for row in rows),
        },
        "workflows": rows,
    }


def summarize_eval_gap(report: dict[str, object], row_key: str, *, compact: bool = False) -> dict[str, object]:
    rows = report.get(row_key) if isinstance(report.get(row_key), list) else []
    gap_rows = [
        row
        for row in rows
        if isinstance(row, dict)
        and (
            row.get("ok") is False
            or bool(row.get("missing_assertions"))
            or bool(row.get("missing_suites"))
        )
    ]
    summary = dict(report.get("summary") if isinstance(report.get("summary"), dict) else {})
    summary["row_count"] = len(rows)
    summary["gap_row_count"] = len(gap_rows)
    compact_report = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", ""),
        "ok": report.get("ok", True),
        "status": report.get("status", ""),
        "summary": summary,
    }
    selected = gap_rows if compact else rows
    if selected:
        compact_report[row_key] = selected
    return compact_report


def summarize_template_scan(report: dict[str, object], *, compact: bool = False) -> dict[str, object]:
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    compact_report = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", ""),
        "ok": report.get("ok", True),
        "status": report.get("status", ""),
        "summary": report.get("summary", {}),
    }
    if issues or not compact:
        compact_report["issues"] = issues
    return compact_report


def summarize_lesson_queue(report: dict[str, object], *, compact: bool = False) -> dict[str, object]:
    candidates = report.get("candidates") if isinstance(report.get("candidates"), list) else []
    lesson_groups = report.get("lesson_groups") if isinstance(report.get("lesson_groups"), list) else []
    threshold = int(report.get("summary", {}).get("promotion_min_count", 2)) if isinstance(report.get("summary"), dict) else 2
    repeated_groups = [item for item in lesson_groups if isinstance(item, dict) and int(item.get("count", 0) or 0) >= threshold]
    compact_report = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", ""),
        "ok": report.get("ok", True),
        "status": report.get("status", ""),
        "summary": report.get("summary", {}),
    }
    if not compact:
        compact_report["candidates"] = candidates
        compact_report["lesson_groups"] = lesson_groups
    elif repeated_groups:
        compact_report["lesson_groups"] = repeated_groups
    return compact_report


def routing_confidence_audit(root: Path) -> dict[str, object]:
    args = type("Args", (), {"root": str(root), "all": True, "skill": None})()
    inventory = skill_inventory.build_report(args)
    compact = skill_inventory.summarize_report(inventory, compact=True)
    duplicate_count = int(compact.get("summary", {}).get("duplicate_trigger_group_count", 0))
    workflow_routes = root / "automations" / "routing.md"
    skill_routes = root / ".agents" / "routing.md"
    issues: list[str] = []
    for path in (workflow_routes, skill_routes):
        if path.exists() and len(common.read_text(path).split()) > repo_policy.int_value(
            root, "limits.optimization.skill_detail_words"
        ):
            issues.append(f"{common.relative(root, path)} is large for routing")
    return {
        "schema_version": 1,
        "tool": "skill-manager.routing-confidence",
        "ok": duplicate_count == 0 and not issues,
        "status": "ok" if duplicate_count == 0 and not issues else "warning",
        "summary": {
            "duplicate_trigger_group_count": duplicate_count,
            "routing_issue_count": len(issues),
            "skill_count": compact.get("summary", {}).get("skill_count", 0),
        },
        "issues": issues,
        "duplicate_trigger_groups": compact.get("duplicate_trigger_groups", []),
    }


def summarize_route_audit(report: dict[str, object], *, compact: bool = False) -> dict[str, object]:
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    duplicate_groups = report.get("duplicate_trigger_groups") if isinstance(report.get("duplicate_trigger_groups"), list) else []
    output: dict[str, object] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.routing-confidence"),
        "ok": bool(report.get("ok")),
        "status": report.get("status", ""),
        "summary": report.get("summary", {}),
    }
    if issues or not compact:
        output["issues"] = issues
    if duplicate_groups or not compact:
        output["duplicate_trigger_groups"] = duplicate_groups
    return output


def template_placeholder_scan(root: Path) -> dict[str, object]:
    patterns = [
        root / "automations",
        root / ".agents" / "skills",
    ]
    issues: list[dict[str, object]] = []
    for base in patterns:
        if not base.exists():
            continue
        for path in sorted(base.rglob("templates/*")):
            if not path.is_file() or path.suffix.lower() not in {".md", ".json", ".txt"}:
                continue
            text = common.read_text(path)
            for line_number, line in enumerate(text.splitlines(), start=1):
                if PLACEHOLDER_RE.search(line):
                    issues.append(
                        {
                            "path": common.relative(root, path),
                            "line": line_number,
                            "placeholder": line.strip()[
                                :repo_policy.int_value(root, "limits.output.finding_snippet_chars")
                            ],
                        }
                    )
    return {
        "schema_version": 1,
        "tool": "skill-manager.template-placeholder-scan",
        "ok": not issues,
        "status": "ok" if not issues else "issues-found",
        "summary": {
            "issue_count": len(issues),
            "file_count": len({item["path"] for item in issues}),
        },
        "issues": issues,
    }


def lesson_promotion_queue(root: Path) -> dict[str, object]:
    promotion_min_count = repo_policy.int_value(root, "owner_defaults.skill_manager.optimization.lesson_promotion_min_count")
    candidates: list[dict[str, object]] = []
    for run_json in sorted((root / "automations").glob("*/runs/*/run.json")):
        data = load_json(run_json)
        values = data.get("lesson_candidates") if isinstance(data.get("lesson_candidates"), list) else []
        for lesson in values:
            text = str(lesson).strip()
            if text:
                candidates.append(
                    {
                        "workflow": run_json.parents[2].name,
                        "run_id": run_json.parent.name,
                        "lesson": text,
                        "source": common.relative(root, run_json),
                    }
                )
    grouped: dict[str, list[dict[str, object]]] = {}
    for item in candidates:
        lesson = str(item.get("lesson", "")).strip()
        key = re.sub(r"\s+", " ", lesson.casefold())
        if key:
            grouped.setdefault(key, []).append(item)
    lesson_groups = []
    for rows in grouped.values():
        workflows = sorted({str(item.get("workflow", "")) for item in rows if item.get("workflow")})
        sources = [str(item.get("source", "")) for item in rows if item.get("source")]
        lesson_groups.append(
            {
                "lesson": str(rows[0].get("lesson", "")),
                "count": len(rows),
                "workflows": workflows,
                "sources": sources[:10],
                "ready": len(rows) >= promotion_min_count,
            }
        )
    lesson_groups.sort(key=lambda item: (-int(item["count"]), str(item["lesson"]).casefold()))
    repeated_count = sum(1 for item in lesson_groups if int(item.get("count", 0)) >= promotion_min_count)
    return {
        "schema_version": 1,
        "tool": "skill-manager.lesson-promotion-queue",
        "ok": True,
        "status": "ok",
        "summary": {
            "candidate_count": len(candidates),
            "unique_lesson_count": len(lesson_groups),
            "repeated_lesson_count": repeated_count,
            "promotion_ready_count": repeated_count,
            "promotion_min_count": promotion_min_count,
        },
        "lesson_groups": lesson_groups,
        "candidates": candidates,
    }


def render_report(report: dict[str, object], title: str) -> str:
    lines = [f"# {title}", ""]
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    for key, value in summary.items():
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        for issue in issues[:20]:
            lines.append(f"- `{issue.get('path', '')}`: {issue.get('placeholder') or issue}")
    return "\n".join(lines).rstrip() + "\n"
