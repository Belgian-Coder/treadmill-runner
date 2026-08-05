"""Requirement-level capability audit for the broad harness objective."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from repo_support import repo_policy


def rel(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except ValueError:
        return path.as_posix()


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def text_contains(path: Path, needle: str) -> bool:
    try:
        return needle in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def requirement(
    requirement_id: str,
    summary: str,
    *,
    status: str,
    evidence: list[str],
    next_action: str,
    risks: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": requirement_id,
        "summary": summary,
        "status": status,
        "ok": status == "proved",
        "evidence": evidence,
        "risks": risks or [],
        "next_action": next_action,
    }


def all_generated_checks_ok(dashboard: dict[str, Any]) -> bool:
    checks = dashboard.get("generated_checks")
    return isinstance(checks, list) and bool(checks) and all(
        isinstance(item, dict) and bool(item.get("ok"))
        for item in checks
    )


def workflow_modules(root: Path) -> list[tuple[str, Path, dict[str, Any]]]:
    rows: list[tuple[str, Path, dict[str, Any]]] = []
    for module_path in sorted((root / "automations").glob("*/module.json")):
        data = read_json(module_path)
        if data.get("kind") == "workflow":
            rows.append((module_path.parent.name, module_path, data))
    return rows


def workflow_run_packets(root: Path) -> list[Path]:
    return sorted((root / "automations").glob("*/runs/*/run.json"))


def benchmark_results(root: Path) -> list[Path]:
    return sorted((root / "automations" / "agent-benchmarking" / "runs").glob("*/benchmark-result.json"))


def active_run_directory_counts(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for runs_dir in sorted((root / "automations").glob("*/runs")):
        run_dirs = [
            item
            for item in runs_dir.iterdir()
            if item.is_dir() and (item / "run.json").exists()
        ]
        counts[runs_dir.parent.name] = len(run_dirs)
    return counts


def fast_daily_requirement(root: Path, dashboard: dict[str, Any]) -> dict[str, Any]:
    target_ms = repo_policy.int_value(root, "owner_defaults.skill_manager.capability_audit.fast_daily_target_ms")
    elapsed = float(dashboard.get("total_elapsed_ms", 0) or 0)
    mode = str(dashboard.get("mode") or "")
    slow = dashboard.get("slow_sections") if isinstance(dashboard.get("slow_sections"), list) else []
    proved = mode != "full" and 0 < elapsed <= target_ms and not slow
    status = "proved" if proved else "partial" if elapsed else "missing"
    return requirement(
        "fast_daily_path",
        "Daily status stays fast and reports slow/advisory sections separately.",
        status=status,
        evidence=[f"dashboard.mode={mode or 'unknown'}", f"dashboard.total_elapsed_ms={elapsed}"],
        risks=[f"elapsed_ms {elapsed} exceeds {target_ms}"] if elapsed > target_ms else [],
        next_action="python -B .agents/manage.py status --fast",
    )


def user_friendly_requirement(root: Path, dashboard: dict[str, Any]) -> dict[str, Any]:
    token_target = repo_policy.int_value(root, "owner_defaults.skill_manager.capability_audit.low_context_token_target")
    docs = ["README.md", "AGENTS.md", "docs/operations/daily-use.md", ".agents/routing.md", "automations/routing.md"]
    missing = [item for item in docs if not (root / item).exists()]
    context = dashboard.get("context_budget") if isinstance(dashboard.get("context_budget"), dict) else {}
    tokens = int(context.get("estimated_low_context_tokens", 0) or 0)
    low_context_files = context.get("low_context_files") if isinstance(context.get("low_context_files"), list) else []
    proved = not missing and tokens and tokens <= token_target and len(low_context_files) >= 2
    status = "proved" if proved else "partial" if not missing else "missing"
    risks = [f"missing {item}" for item in missing]
    if tokens > token_target:
        risks.append(f"low-context estimate {tokens} exceeds {token_target}")
    return requirement(
        "user_friendly_surface",
        "The daily path is documented, low-context, and easy to route without opening whole folders.",
        status=status,
        evidence=[*docs, f"estimated_low_context_tokens={tokens}"],
        risks=risks,
        next_action="python -B .agents/manage.py commands --daily",
    )


def deterministic_requirement(dashboard: dict[str, Any]) -> dict[str, Any]:
    benchmark = dashboard.get("benchmark") if isinstance(dashboard.get("benchmark"), dict) else {}
    generated_ok = all_generated_checks_ok(dashboard)
    benchmark_ok = bool(benchmark.get("ok", True))
    changed_count = int(dashboard.get("changed_file_count", 0) or 0)
    status = "proved" if generated_ok and benchmark_ok and changed_count == 0 else "partial"
    risks = []
    if not generated_ok:
        risks.append("generated artifacts are not proven in sync")
    if not benchmark_ok:
        risks.append("benchmark doctor is not ok")
    if changed_count:
        risks.append(f"{changed_count} changed files still need final validation and commit evidence")
    return requirement(
        "deterministic_validation",
        "Generated artifacts, validations, and benchmark gates are deterministic and script-backed.",
        status=status,
        evidence=["dashboard.generated_checks", "dashboard.benchmark", "python -B .agents/manage.py finish --deep"],
        risks=risks,
        next_action="python -B .agents/manage.py finish --deep",
    )


def workflow_customization_requirement(root: Path) -> dict[str, Any]:
    modules = workflow_modules(root)
    missing = [
        name
        for name, _path, manifest in modules
        if not isinstance(manifest.get("phases"), list) or not manifest.get("phases")
    ]
    multi_phase = [
        name
        for name, _path, manifest in modules
        if isinstance(manifest.get("phases"), list) and len(manifest.get("phases", [])) >= 2
    ]
    status = "proved" if modules and not missing and multi_phase else "missing" if not modules else "partial"
    return requirement(
        "workflow_steps",
        "Workflows expose configurable phase steps through module.json contracts.",
        status=status,
        evidence=[rel(root, path) for _name, path, _manifest in modules],
        risks=[f"{name} has no phases" for name in missing],
        next_action="python -B .agents/manage.py validate-automations --strict-phase-quality",
    )


def workflow_hooks_requirement(root: Path) -> dict[str, Any]:
    support = root / ".agents" / "skills" / "workflow-manager" / "scripts" / "workflow_run_support.py"
    tests = root / ".agents" / "skills" / "workflow-manager" / "scripts" / "run_self_tests.py"
    docs = root / "docs" / "workflow" / "workflows.md"
    has_runtime = text_contains(support, "execute_workflow_hooks")
    has_start = text_contains(tests, "run-started") or text_contains(tests, "test_workflow_start")
    has_phase = text_contains(tests, "phase-started") and text_contains(tests, "phase-completed")
    has_docs = text_contains(docs, "Workflow hooks")
    status = "proved" if has_runtime and has_start and has_phase and has_docs else "partial"
    risks = []
    if not has_runtime:
        risks.append("workflow hook runtime was not found")
    if not has_start or not has_phase:
        risks.append("workflow hook lifecycle self-tests are incomplete")
    if not has_docs:
        risks.append("workflow hook docs are missing")
    return requirement(
        "workflow_hooks",
        "Workflow hooks are declared in module.json, executed by state-writing commands, and covered by tests.",
        status=status,
        evidence=[rel(root, support), rel(root, tests), rel(root, docs)],
        risks=risks,
        next_action="python -B .agents/skills/workflow-manager/scripts/run_self_tests.py",
    )


def current_validation_evidence_requirement(root: Path, dashboard: dict[str, Any]) -> dict[str, Any]:
    evidence = dashboard.get("evidence") if isinstance(dashboard.get("evidence"), dict) else {}
    dashboard_benchmarks = evidence.get("benchmarks") if isinstance(evidence.get("benchmarks"), list) else []
    dashboard_workflows = evidence.get("workflow_runs") if isinstance(evidence.get("workflow_runs"), list) else []
    benchmarks = benchmark_results(root)
    workflows = workflow_run_packets(root)
    counts = active_run_directory_counts(root)
    bloated = sorted(name for name, count in counts.items() if count > 1)
    has_benchmark = bool(dashboard_benchmarks or benchmarks)
    has_workflows = bool(dashboard_workflows or workflows)
    status = "proved" if has_benchmark and has_workflows and not bloated else "missing"
    if has_benchmark and has_workflows and bloated:
        status = "partial"
    risks = [f"{name} has {counts[name]} active run dirs" for name in bloated]
    if not has_benchmark:
        risks.append("no retained benchmark-result.json found")
    if not has_workflows:
        risks.append("no workflow run.json packets found")
    return requirement(
        "current_validation_evidence",
        "Only current setup benchmark/workflow evidence is active, and it is saved for later comparison/resume.",
        status=status,
        evidence=[
            *[rel(root, item) for item in benchmarks[:5]],
            *[rel(root, item.parent) for item in workflows[:5]],
        ],
        risks=risks,
        next_action="python -B .agents/manage.py benchmark doctor",
    )


def build_capability_audit(root: Path, dashboard: dict[str, Any]) -> dict[str, Any]:
    requirements = [
        fast_daily_requirement(root, dashboard),
        user_friendly_requirement(root, dashboard),
        deterministic_requirement(dashboard),
        workflow_customization_requirement(root),
        workflow_hooks_requirement(root),
        current_validation_evidence_requirement(root, dashboard),
    ]
    missing = [item for item in requirements if item["status"] == "missing"]
    partial = [item for item in requirements if item["status"] == "partial"]
    completion_supported = not missing and not partial
    return {
        "schema_version": 1,
        "tool": "repo-capability-audit",
        "ok": True,
        "status": "proved" if completion_supported else "incomplete",
        "completion_supported": completion_supported,
        "requirements": requirements,
        "summary": {
            "proved": sum(1 for item in requirements if item["status"] == "proved"),
            "partial": len(partial),
            "missing": len(missing),
            "total": len(requirements),
        },
        "next_command": (
            "none, capability objective is currently proven"
            if completion_supported
            else str((missing or partial)[0]["next_action"])
        ),
    }
