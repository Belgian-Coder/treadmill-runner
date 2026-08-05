"""Failure triage and resume-work helpers for repo_qol."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Callable

from repo_support import repo_changed
from repo_support import repo_common as repo
from repo_support import repo_doctor
from repo_support import repo_feedback
from repo_support import repo_policy
from repo_support.repo_qol_capture import output_reference_text
from repo_support.repo_qol_dashboard import branch_name
from repo_support.repo_qol_evidence import latest_evidence_report

LAST_VALIDATION = Path(".agents/local-ai/cache/last-validation.txt")
MANAGE = "python -B .agents/manage.py"
SYNC_COMMAND = f"{MANAGE} sync"
SYNC_CHECK_COMMAND = f"{SYNC_COMMAND} --check"
CHECK_CHANGED_DEEP_COMMAND = f"{MANAGE} check-changed --deep"
STATUS_COMMAND = f"{MANAGE} status"
WHAT_NOW_COMMAND = f"{MANAGE} what-now"
LOCAL_AI_DOCTOR_COMMAND = f"{MANAGE} local-ai doctor --quick --json"
BENCHMARK_DOCTOR_COMMAND = f"{MANAGE} benchmark doctor"
VALIDATION_TRIAGE_COMMAND = f"{MANAGE} local-ai task --task validation-triage --input .agents/local-ai/cache/last-validation.txt"


def read_input_text(root: Path, value: str | None) -> tuple[str, str]:
    if value == "-":
        return "stdin", sys.stdin.read()
    if value:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = root / path
        try:
            path = path.resolve()
            path.relative_to(root.resolve())
        except ValueError:
            raise SystemExit("input path must stay inside the repository")
        rel_path = repo.relative(root, path)
        if not path.is_file():
            return f"{rel_path} (missing)", f"ERROR: input path is missing: {rel_path}\n"
        return rel_path, path.read_text(encoding="utf-8", errors="replace")
    path = root / LAST_VALIDATION
    if path.exists():
        return repo.relative(root, path), path.read_text(encoding="utf-8", errors="replace")
    return "", ""


def first_failing_fact(text: str) -> str:
    excerpt_chars = repo_policy.int_value(
        repo_policy.project_root(), "limits.output.failure_excerpt_chars"
    )
    patterns = (
        "error",
        "failed",
        "failure",
        "traceback",
        "assertionerror",
        "out of sync",
        "stale",
        "missing",
        "not found",
    )
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and any(token in stripped.lower() for token in patterns):
            return stripped[:excerpt_chars]
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:excerpt_chars]
    return "No failure evidence was provided."


def classify_failure_type(fact: str, text: str) -> str:
    haystack = f"{fact}\n{text}".lower()
    if "__pycache__" in haystack or ".pyc" in haystack or ".pyo" in haystack:
        return "stale-generated-or-cache"
    if "out of sync" in haystack or "generated" in haystack or "stale" in haystack:
        return "stale-generated-or-cache"
    if "syntaxerror" in haystack or "jsondecodeerror" in haystack or "invalid json" in haystack:
        return "syntax-or-schema"
    if "filenotfounderror" in haystack or "not found" in haystack or "missing" in haystack:
        return "missing-file-or-dependency"
    if "permission" in haystack or "access denied" in haystack:
        return "permission"
    if "timeout" in haystack or "timed out" in haystack:
        return "timeout"
    if "github" in haystack and ("billing" in haystack or "spending" in haystack or "external" in haystack):
        return "external-blocker"
    if "traceback" in haystack or "exception" in haystack:
        return "runtime"
    if "failed" in haystack or "failure" in haystack or "error" in haystack:
        return "failed-check"
    return "unknown"


def infer_owner_and_command(fact: str, text: str) -> tuple[str, str, str]:
    haystack = f"{fact}\n{text}".lower()
    if "__pycache__" in haystack or ".pyc" in haystack or ".pyo" in haystack:
        return (
            "skill-manager",
            "python -B .agents/manage.py syntax-check --paths .agents/skills automations --format json",
            "Use ast.parse syntax checking instead of py_compile so validation cannot write bytecode caches.",
        )
    if ".agents/skills/" in haystack or "skill" in haystack:
        match = re.search(r"\.agents/skills/([a-z0-9-]+)", haystack)
        skill = match.group(1) if match else "<skill-name>"
        return "skill-manager", f"python -B .agents/manage.py skill doctor --skill .agents/skills/{skill}", "Use the deterministic skill review and validation output."
    if "automations/" in haystack or "workflow" in haystack:
        match = re.search(r"automations/([a-z0-9-]+)", haystack)
        workflow = match.group(1) if match else "<workflow-name>"
        return "workflow-manager", f"python -B .agents/manage.py workflow doctor --name {workflow}", "Use workflow validation, run index, and review evidence."
    if "routing" in haystack or "generated" in haystack or "out of sync" in haystack:
        return "skill-manager", SYNC_CHECK_COMMAND, f"Run `{SYNC_COMMAND}` only when you intend to update generated artifacts."
    if "local ai" in haystack or "local-ai" in haystack or "embedding" in haystack:
        return "local-ai-helper", LOCAL_AI_DOCTOR_COMMAND, "Use the local-AI readiness and policy checks."
    if "benchmark" in haystack:
        return "agent-benchmarking", BENCHMARK_DOCTOR_COMMAND, "Use deterministic benchmark doctor before comparing runs."
    return "skill-manager", CHECK_CHANGED_DEEP_COMMAND, "Use changed-scope checks to narrow the owner."


def owner_rationale(owner: str, fact: str, text: str) -> str:
    haystack = f"{fact}\n{text}".lower()
    if owner == "workflow-manager":
        return "The failure mentions workflow or automations paths, so workflow validation and run evidence own the next step."
    if owner == "local-ai-helper":
        return "The failure mentions embeddings or local AI state, so local-ai-helper owns readiness and policy checks."
    if owner == "agent-benchmarking":
        return "The failure mentions benchmark runs or comparable evidence, so agent-benchmarking owns the doctor and comparison checks."
    if ".agents/skills/" in haystack:
        return "The failure mentions accepted skill files, so skill-manager owns review, validation, and routing evidence."
    return "The owner is inferred from the first failing fact; changed-scope checks can narrow it further."


def what_now_report(
    root: Path,
    *,
    input_value: str | None = None,
    command_label: str = "",
    from_command: str | None = None,
    explain_owner: bool = False,
    command_runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    command_result: dict[str, Any] = {}
    if from_command:
        if command_runner is None:
            raise ValueError("command_runner is required when from_command is provided")
        command_result = command_runner(root, from_command)
        cache_text = output_reference_text(command_result)
        text = str(command_result.get("distilled_output") or command_result.get("output_tail", ""))
        source = "command"
        command_label = command_label or from_command
        if not command_result.get("ok"):
            cache_path = root / LAST_VALIDATION
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(cache_text, encoding="utf-8", newline="\n")
    else:
        source, text = read_input_text(root, input_value)
    if command_result and command_result.get("ok"):
        warnings: list[str] = []
        try:
            (root / LAST_VALIDATION).unlink(missing_ok=True)
        except OSError as exc:
            warnings.append(f"Could not clear {LAST_VALIDATION.as_posix()}: {exc}")
        fact = "Command completed successfully; no failing fact was found."
        owner, next_command, fallback = (
            "skill-manager",
            "python -B .agents/manage.py status",
            "Use the successful command output as evidence.",
        )
    else:
        warnings = []
        fact = first_failing_fact(text)
        owner, next_command, fallback = infer_owner_and_command(fact, text)
    failure_type = "passed" if command_result and command_result.get("ok") else classify_failure_type(fact, text)
    local_ai_command = VALIDATION_TRIAGE_COMMAND
    report = {
        "schema_version": 1,
        "tool": "repo-what-now",
        "ok": True,
        "source": source,
        "command_label": command_label,
        "command_result": command_result,
        "first_failing_fact": fact,
        "failure_type": failure_type,
        "likely_owner": owner,
        "owner_rationale": owner_rationale(owner, fact, text) if explain_owner else "",
        "next_command": next_command,
        "deterministic_fallback": fallback,
        "optional_local_ai_command": local_ai_command if source.endswith("last-validation.txt") else "",
        "warnings": warnings,
    }
    repo_feedback.record_what_now_failure(root, report)
    return report


def summarize_what_now_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    command_result = report.get("command_result") if isinstance(report.get("command_result"), dict) else {}
    warnings = report.get("warnings") if isinstance(report.get("warnings"), list) else []
    output: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "repo-what-now"),
        "ok": bool(report.get("ok", True)),
        "source": report.get("source", ""),
        "command_label": report.get("command_label", ""),
        "first_failing_fact": report.get("first_failing_fact", ""),
        "failure_type": report.get("failure_type", ""),
        "likely_owner": report.get("likely_owner", ""),
        "next_command": report.get("next_command", ""),
        "deterministic_fallback": report.get("deterministic_fallback", ""),
    }
    if report.get("owner_rationale"):
        output["owner_rationale"] = report.get("owner_rationale")
    if report.get("optional_local_ai_command"):
        output["optional_local_ai_command"] = report.get("optional_local_ai_command")
    if warnings:
        output["warnings"] = warnings
    if command_result and not compact:
        output["command_result"] = {
            "ok": bool(command_result.get("ok")),
            "status": command_result.get("status"),
            "command": command_result.get("command"),
        }
    return output


def resume_work_report(
    root: Path,
    *,
    evidence_factory: Callable[[Path], dict[str, Any]] = latest_evidence_report,
    branch_factory: Callable[[Path], str] = branch_name,
) -> dict[str, Any]:
    dirty = repo_doctor.git_dirty_state(root)
    changed = repo_changed.changed_files(root)
    evidence = evidence_factory(root)
    next_command = STATUS_COMMAND
    if changed:
        next_command = CHECK_CHANGED_DEEP_COMMAND
    if evidence.get("latest_validation"):
        next_command = WHAT_NOW_COMMAND
    return {
        "schema_version": 1,
        "tool": "repo-resume-work",
        "ok": True,
        "mode": "deterministic",
        "branch": branch_factory(root),
        "dirty_state": dirty,
        "changed_files": changed[:200],
        "changed_groups": repo_changed.compact_path_groups(changed) if changed else "",
        "evidence": evidence,
        "next_command": next_command,
    }


def summarize_resume_work_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    dirty = report.get("dirty_state") if isinstance(report.get("dirty_state"), dict) else {}
    changed = report.get("changed_files") if isinstance(report.get("changed_files"), list) else []
    evidence = report.get("evidence") if isinstance(report.get("evidence"), dict) else {}
    workflow_runs = evidence.get("workflow_runs") if isinstance(evidence.get("workflow_runs"), list) else []
    benchmarks = evidence.get("benchmarks") if isinstance(evidence.get("benchmarks"), list) else []
    documents = evidence.get("document_evidence") if isinstance(evidence.get("document_evidence"), list) else []
    local_ai_reports = evidence.get("local_ai_reports") if isinstance(evidence.get("local_ai_reports"), list) else []
    evidence_summary: dict[str, Any] = {
        "latest_validation": evidence.get("latest_validation", ""),
        "workflow_run_count": len(workflow_runs),
        "benchmark_count": len(benchmarks),
        "document_evidence_count": len(documents),
        "local_ai_report_count": len(local_ai_reports),
    }
    if compact:
        latest_workflow = workflow_runs[0] if workflow_runs else {}
        latest_benchmark = benchmarks[0] if benchmarks else {}
        evidence_summary.update(
            {
                "latest_workflow_run": latest_workflow.get("path", ""),
                "latest_workflow_status": latest_workflow.get("status", ""),
                "latest_benchmark": latest_benchmark.get("path", ""),
                "latest_benchmark_status": latest_benchmark.get("status", ""),
            }
        )
    else:
        evidence_summary.update(
            {
                "latest_workflow_run": workflow_runs[0] if workflow_runs else {},
                "latest_benchmark": benchmarks[0] if benchmarks else {},
            }
        )
    summary: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "repo-resume-work"),
        "ok": bool(report.get("ok", True)),
        "mode": report.get("mode", ""),
        "branch": report.get("branch", ""),
        "dirty": bool(dirty.get("dirty", False)),
        "dirty_status": dirty.get("status", ""),
        "changed_file_count": len(changed),
        "changed_groups": report.get("changed_groups", ""),
        "evidence": evidence_summary,
        "next_command": report.get("next_command", ""),
    }
    if not compact:
        summary["dirty_state"] = {
            "dirty": bool(dirty.get("dirty", False)),
            "status": dirty.get("status", ""),
        }
        summary.pop("dirty", None)
        summary.pop("dirty_status", None)
        summary["changed_files"] = changed[:40]
    return summary
