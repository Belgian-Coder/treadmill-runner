#!/usr/bin/env python3
"""Run deterministic eval assertions against one workflow module."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import workflow_manager_common as common
import validate_automations
import workflow_run_support
from validation_support.manifests import module_contract_v3
from workflow_support.workers import normalized_phase_assignments


@dataclass(frozen=True)
class Args:
    root: Path
    workflow_name: str
    suite: Path
    output_format: str
    summary: bool = False
    compact: bool = False


ASSERTION_TYPES = {
    "contract_declares_command",
    "contract_contains",
    "contract_declares_output",
    "contract_declares_phase",
    "contract_declares_related_module",
    "contract_declares_task",
    "contract_declares_worker_profile",
    "contract_local_ai_use_cases",
    "file_absent",
    "file_contains",
    "file_exists",
    "instructions_contains",
    "references_contains",
    "repo_command_succeeds",
    "run_evidence_ledger_valid",
    "run_index_contains",
    "run_index_exists",
    "run_packet_valid",
    "run_resume_state_valid",
    "run_handoff_valid",
    "run_context_packet_valid",
    "run_progress_document_valid",
    "start_contains",
    "unsupported_claims_recorded",
    "validation_ok",
    "workflow_lifecycle_smoke_ok",
}
DEFAULT_COMMAND_TIMEOUT_SECONDS = 120
ALLOWED_REPO_COMMANDS = {
    ("status", "--fast"),
    ("sync-automation-routing", "--check"),
    ("validate-automations",),
    ("benchmark", "tool-call", "--check", "--json", "--compact"),
}


def execute_subprocess(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    timeout = kwargs.pop("timeout", None)
    check = bool(kwargs.pop("check", False))
    process_group = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    process = subprocess.Popen(argv, **kwargs, **process_group)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            try:
                os.killpg(process.pid, 9)
            except OSError:
                process.kill()
        try:
            remaining, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            remaining, _ = process.communicate()
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        if isinstance(remaining, bytes):
            remaining = remaining.decode("utf-8", errors="replace")
        raise subprocess.TimeoutExpired(argv, timeout, output=f"{partial}{remaining or ''}") from exc
    completed = subprocess.CompletedProcess(argv, int(process.returncode or 0), stdout=stdout, stderr=stderr)
    if check:
        completed.check_returncode()
    return completed


def timeout_seconds_from_assertion(assertion: dict[str, Any]) -> int:
    try:
        value = int(assertion.get("timeout_seconds", DEFAULT_COMMAND_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_COMMAND_TIMEOUT_SECONDS
    return max(1, min(value, 3600))


def timeout_output(exc: subprocess.TimeoutExpired, timeout_seconds: int) -> str:
    output = exc.stdout or ""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    output_text = str(output).strip()
    prefix = f"command timed out after {timeout_seconds}s"
    return f"{prefix}; partial output: {output_text}" if output_text else prefix


class CommandRunner:
    """Execute every command assertion against the state visible at that point."""

    def __init__(self) -> None:
        self.command_assertions = 0
        self.command_executions = 0

    def record_assertion(self) -> None:
        self.command_assertions += 1

    def run(self, argv: list[str], *, cwd: Path, timeout_seconds: int) -> tuple[int, str]:
        normalized_argv = tuple(str(part) for part in argv)
        self.command_executions += 1
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            completed = execute_subprocess(
                list(normalized_argv),
                cwd=cwd,
                check=False,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout_seconds,
            )
            result = (completed.returncode, completed.stdout)
        except subprocess.TimeoutExpired as exc:
            result = (124, timeout_output(exc, timeout_seconds))
        return result

    def telemetry(self) -> dict[str, int]:
        return {
            "command_assertions": self.command_assertions,
            "command_executions": self.command_executions,
        }


def load_suite(path: Path) -> dict[str, Any]:
    data, error = common.read_json_file(path)
    if error:
        raise SystemExit(error)
    if not isinstance(data, dict):
        raise SystemExit("workflow eval suite must be a JSON object.")
    if data.get("skill_name") and not data.get("workflow_name"):
        skill_name = str(data.get("skill_name", "")).strip()
        hint = f"python -B .agents/manage.py eval-skill --skill .agents/skills/{skill_name} --suite {path}"
        raise SystemExit(f"{path} appears to be a skill eval suite; use {hint}")
    return data


def workflow_dir(root: Path, workflow_name: str) -> Path:
    if not common.SKILL_NAME_PATTERN.match(workflow_name):
        raise SystemExit("workflow name must use lowercase letters, digits, and hyphens.")
    return root / "automations" / workflow_name


def normalize_assertions(case: dict[str, Any]) -> list[dict[str, Any]]:
    values = case.get("assertions", [])
    if not isinstance(values, list):
        raise SystemExit(f"eval case {case.get('id', '<unknown>')} assertions must be a list.")
    assertions: list[dict[str, Any]] = []
    for index, item in enumerate(values):
        if not isinstance(item, dict):
            raise SystemExit(f"eval case {case.get('id', '<unknown>')} assertion {index + 1} must be an object.")
        assertion_type = item.get("type")
        if assertion_type not in ASSERTION_TYPES:
            raise SystemExit(f"unknown workflow assertion type: {assertion_type}")
        assertions.append(item)
    if not assertions:
        raise SystemExit(f"eval case {case.get('id', '<unknown>')} must contain at least one assertion.")
    return assertions


def repo_command_is_allowed(command: object, workflow_name: str) -> bool:
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        return False
    normalized = tuple(workflow_name if part == "<workflow>" else part for part in command)
    if normalized in ALLOWED_REPO_COMMANDS:
        return True
    return normalized in {
        ("workflow", "template", "lint", "--name", workflow_name, "--format", "json"),
        ("workflow", "plan-check", "--name", workflow_name, "--template", "--format", "json"),
    }


def normalize_command(command: object, workflow_name: str) -> list[str]:
    if not repo_command_is_allowed(command, workflow_name):
        raise SystemExit(
            "repo_command_succeeds requires an exact allowlisted read-only repository command."
        )
    assert isinstance(command, list)
    return [workflow_name if part == "<workflow>" else part for part in command]


def run_repo_command(
    root: Path,
    command: list[str],
    *,
    command_runner: CommandRunner,
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> tuple[int, str]:
    launcher = root / ".agents" / "manage.py"
    if not launcher.exists():
        return 1, "repository launcher not found"
    return command_runner.run(
        [sys.executable, "-B", str(launcher), *command],
        cwd=root,
        timeout_seconds=timeout_seconds,
    )


def load_run_json(module_dir: Path, run_id: object, name: str) -> dict[str, Any]:
    run = str(run_id or "")
    path = module_dir / "runs" / run / name
    if name != "run.json":
        path = module_dir / "runs" / run / "run.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_context_packet(module_dir: Path, run_id: object) -> dict[str, Any]:
    run = str(run_id or "")
    path = module_dir / "runs" / run / "artifacts" / "context" / "context-packet.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def contract_path(module_dir: Path) -> Path:
    return module_dir / "module.json"


def contract_json(module_dir: Path) -> dict[str, Any]:
    data, error = common.read_json_file(contract_path(module_dir))
    if error or not isinstance(data, dict):
        return {}
    return data


def command_display(command: object) -> str:
    return module_contract_v3.command_display(command)


def command_matches_tokens(command: object, expected: object) -> bool:
    """Match an assertion against argv tokens without flattening or reparsing argv."""

    argv = module_contract_v3.command_argv(command)
    if not argv:
        return False
    if isinstance(expected, list) and all(isinstance(item, str) for item in expected):
        expected_tokens = list(expected)
    elif isinstance(expected, str):
        if len(argv) == 1:
            return expected in argv[0]
        expected_tokens = expected.split()
    else:
        return False
    if not expected_tokens:
        return False
    for index in range(len(argv) - len(expected_tokens) + 1):
        actual = argv[index : index + len(expected_tokens)]
        if actual == expected_tokens:
            return True
        if actual and actual[0].replace("\\", "/").endswith(expected_tokens[0]) and actual[1:] == expected_tokens[1:]:
            return True
    return False


def contract_search_text(module_dir: Path) -> str:
    return common.read_text(contract_path(module_dir), limit=80_000)


def instructions_path(module_dir: Path) -> Path:
    return module_dir / "instructions.md"


def references_text(module_dir: Path) -> str:
    paths = [
        module_dir / "WORKFLOW.md",
        module_dir / "module.json",
        module_dir / "instructions.md",
    ]
    return "\n".join(common.read_text(path, limit=80_000) for path in paths if path.exists())


def normalized_output_lines(text: str) -> list[str]:
    return [
        line.strip().strip("`")
        for line in text.splitlines()
        if line.strip().startswith("-")
    ]


def check_assertion(
    assertion: dict[str, Any],
    *,
    root: Path,
    module_dir: Path,
    workflow_name: str,
    validation: dict[str, Any],
    command_runner: CommandRunner,
) -> tuple[bool, str]:
    assertion_type = assertion["type"]
    if assertion_type == "validation_ok":
        return bool(validation["ok"]), "expected workflow validation to pass"
    if assertion_type == "file_exists":
        path = module_dir / str(assertion.get("path", ""))
        return path.exists(), f"expected file to exist: {common.relative(root, path)}"
    if assertion_type == "file_absent":
        path = module_dir / str(assertion.get("path", ""))
        return not path.exists(), f"expected file to be absent: {common.relative(root, path)}"
    if assertion_type == "file_contains":
        path = module_dir / str(assertion.get("path", ""))
        expected = str(assertion.get("text", ""))
        text = common.read_text(path, limit=120_000)
        return expected in text, f"expected {common.relative(root, path)} to contain {expected!r}"
    if assertion_type == "start_contains":
        expected = str(assertion.get("text", ""))
        text = common.read_text(common.workflow_start_path(module_dir), limit=80_000)
        return expected in text, f"expected WORKFLOW.md to contain {expected!r}"
    if assertion_type == "contract_contains":
        expected = str(assertion.get("text", ""))
        text = contract_search_text(module_dir)
        commands = contract_json(module_dir).get("commands", [])
        command_match = isinstance(commands, list) and any(
            command_matches_tokens(command, expected) for command in commands
        )
        return expected in text or command_match, f"expected {common.relative(root, contract_path(module_dir))} to contain {expected!r}"
    if assertion_type == "contract_declares_related_module":
        expected = str(assertion.get("module", ""))
        modules = contract_json(module_dir).get("related_modules", [])
        ok = isinstance(modules, list) and expected in [str(module) for module in modules]
        return ok, f"expected module.json related_modules to declare {expected!r}"
    if assertion_type == "contract_declares_command":
        expected: object = assertion.get("argv", assertion.get("command", ""))
        commands = contract_json(module_dir).get("commands", [])
        ok = isinstance(commands, list) and any(
            command_matches_tokens(command, expected) for command in commands
        )
        return ok, f"expected module.json commands to include {expected!r}"
    if assertion_type == "contract_declares_phase":
        expected = str(assertion.get("phase", ""))
        phases = contract_json(module_dir).get("phases", [])
        phase_ids = [str(phase.get("id", "")) for phase in phases if isinstance(phase, dict)]
        return expected in phase_ids, f"expected module.json phases to declare {expected!r}"
    if assertion_type == "contract_declares_task":
        expected = str(assertion.get("task", ""))
        tasks = contract_json(module_dir).get("tasks", [])
        task_ids = [str(task.get("id", "")) for task in tasks if isinstance(task, dict)]
        return expected in task_ids, f"expected module.json tasks to declare {expected!r}"
    if assertion_type == "contract_declares_worker_profile":
        expected_phase = str(assertion.get("phase", ""))
        expected_profile = str(assertion.get("profile", ""))
        manifest = contract_json(module_dir)
        actual = normalized_phase_assignments(manifest).get(expected_phase)
        return (
            actual == expected_profile
        ), f"expected module.json worker_profiles.phase_assignments.{expected_phase} to be {expected_profile!r}"
    if assertion_type == "contract_local_ai_use_cases":
        expected = assertion.get("use_cases", [])
        if not isinstance(expected, list):
            return False, "expected contract_local_ai_use_cases use_cases to be a list"
        local_ai = contract_json(module_dir).get("local_ai", {})
        use_cases = local_ai.get("use_cases", []) if isinstance(local_ai, dict) else []
        if not isinstance(use_cases, list):
            return False, "expected module.json local_ai.use_cases to be a list"
        actual = [
            str(item.get("id", item.get("name", ""))) if isinstance(item, dict) else str(item)
            for item in use_cases
        ]
        return actual == [str(item) for item in expected], "expected module.json local_ai.use_cases to match"
    if assertion_type == "contract_declares_output":
        expected = str(assertion.get("path", ""))
        manifest = contract_json(module_dir)
        outputs = manifest.get("outputs", [])
        if isinstance(outputs, list):
            declared = [
                str(output.get("path") or output.get("name") or "")
                if isinstance(output, dict)
                else str(output)
                for output in outputs
            ]
            return expected in declared, f"expected module.json outputs to declare {expected!r}"
        text = common.read_text(contract_path(module_dir), limit=80_000)
        return expected in "\n".join(normalized_output_lines(text)), f"expected contract outputs to declare {expected!r}"
    if assertion_type == "instructions_contains":
        expected = str(assertion.get("text", ""))
        text = common.read_text(instructions_path(module_dir), limit=80_000)
        return expected in text, f"expected {common.relative(root, instructions_path(module_dir))} to contain {expected!r}"
    if assertion_type == "references_contains":
        expected = str(assertion.get("text", ""))
        text = references_text(module_dir)
        return expected in text, f"expected workflow entry, module, or instructions to contain {expected!r}"
    if assertion_type == "repo_command_succeeds":
        command_runner.record_assertion()
        command = normalize_command(assertion.get("command"), workflow_name)
        status, output = run_repo_command(
            root,
            command,
            command_runner=command_runner,
            timeout_seconds=timeout_seconds_from_assertion(assertion),
        )
        return status == 0, f"expected repo command to succeed: {' '.join(command)}; output: {output.strip()}"
    if assertion_type == "run_index_exists":
        index = module_dir / "runs" / "index.json"
        markdown = module_dir / "runs" / "INDEX.md"
        return index.exists() and markdown.exists(), "expected a generated run index to exist"
    if assertion_type == "run_index_contains":
        run_id = str(assertion.get("run_id", ""))
        index_path = module_dir / "runs" / "index.json"
        try:
            index = json.loads(index_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            index = {}
        runs = index.get("runs", []) if isinstance(index, dict) else []
        ok = any(isinstance(item, dict) and item.get("id") == run_id for item in runs)
        return ok, f"expected workflow run index to include {run_id!r}"
    if assertion_type == "run_evidence_ledger_valid":
        run_id = assertion.get("run_id")
        packet = load_run_json(module_dir, run_id, "run.json")
        ok = packet.get("schema_version") == 2 and isinstance(packet.get("status"), str)
        has_evidence = isinstance(packet.get("evidence"), list)
        return ok and has_evidence, f"expected run {run_id!r} to have a schema_version 2 run.json packet with evidence"
    if assertion_type == "run_packet_valid":
        run_id = assertion.get("run_id")
        packet = load_run_json(module_dir, run_id, "run.json")
        required = {
            "schema_version",
            "workflow",
            "run_id",
            "current_phase",
            "status",
            "checks",
            "commands",
            "evidence",
            "handoff",
            "next_action",
            "unsupported_claims",
        }
        missing = sorted(key for key in required if key not in packet)
        ok = (
            not missing
            and packet.get("schema_version") == 2
            and isinstance(packet.get("checks"), dict)
            and isinstance(packet.get("commands"), list)
            and isinstance(packet.get("evidence"), list)
            and isinstance(packet.get("handoff"), dict)
            and isinstance(packet.get("unsupported_claims"), list)
        )
        return ok, f"expected run {run_id!r} run.json to be a complete v2 run packet; missing: {', '.join(missing)}"
    if assertion_type == "run_resume_state_valid":
        run_id = assertion.get("run_id")
        state = load_run_json(module_dir, run_id, "run.json")
        required = {"current_phase", "status", "next_action", "handoff", "checks", "decisions"}
        missing = sorted(key for key in required if key not in state)
        return not missing, f"expected run {run_id!r} run.json to include: {', '.join(sorted(required))}; missing: {', '.join(missing)}"
    if assertion_type == "run_handoff_valid":
        run_id = str(assertion.get("run_id", ""))
        active = module_dir / "runs" / run_id / "run.json"
        payload = load_run_json(module_dir, run_id, "run.json")
        handoff = payload.get("handoff") if isinstance(payload.get("handoff"), dict) else {}
        required = {"required_next_context"}
        missing = sorted(key for key in required if key not in handoff)
        ok = active.exists() and not missing and bool(payload.get("next_action"))
        return ok, f"expected run {run_id!r} run.json handoff with {', '.join(sorted(required))}; missing: {', '.join(missing)}"
    if assertion_type == "run_context_packet_valid":
        run_id = assertion.get("run_id")
        packet = load_context_packet(module_dir, run_id)
        required = {
            "schema_version",
            "tool",
            "workflow",
            "run_id",
            "scope",
            "validation_summary",
            "evidence_handles",
            "required_next_context",
            "token_estimates",
            "context_budget",
        }
        missing = sorted(key for key in required if key not in packet)
        estimates = packet.get("token_estimates") if isinstance(packet.get("token_estimates"), dict) else {}
        budget = packet.get("context_budget") if isinstance(packet.get("context_budget"), dict) else {}
        scope = packet.get("scope") if isinstance(packet.get("scope"), dict) else {}
        required_next_context = (
            packet.get("required_next_context") if isinstance(packet.get("required_next_context"), list) else []
        )
        packet_tokens = estimates.get("packet_tokens_estimated")
        saved_tokens = estimates.get("estimated_tokens_saved")
        compact_tokens = estimates.get("compact_packet_tokens_estimated")
        ok = (
            not missing
            and packet.get("schema_version") == 3
            and packet.get("tool") == "workflow-manager.context-packet"
            and isinstance(packet.get("evidence_handles"), list)
            and isinstance(required_next_context, list)
            and isinstance(packet_tokens, int)
            and isinstance(saved_tokens, int)
            and isinstance(compact_tokens, int)
            and packet_tokens >= compact_tokens
            and saved_tokens > 0
            and budget.get("status") == "ok"
            and packet_tokens <= int(budget.get("packet_token_limit", 0) or 0)
            and "out_of_scope" in scope
            and any(str(item).endswith("artifacts/context/context-packet.json") for item in required_next_context)
        )
        return ok, f"expected run {run_id!r} to have a valid context-packet.json; missing: {', '.join(missing)}"
    if assertion_type == "run_progress_document_valid":
        run_id = str(assertion.get("run_id", ""))
        run_dir = module_dir / "runs" / run_id
        packet = load_run_json(module_dir, run_id, "run.json")
        issues = workflow_run_support.progress_log_issues(root, workflow_name, run_dir, packet)
        return not issues, f"expected run {run_id!r} to have a current execution-log.md; issues: {'; '.join(issues)}"
    if assertion_type == "unsupported_claims_recorded":
        run_id = assertion.get("run_id")
        packet = load_run_json(module_dir, run_id, "run.json")
        return "unsupported_claims" in packet, f"expected run {run_id!r} run.json to record unsupported_claims"
    if assertion_type == "workflow_lifecycle_smoke_ok":
        from workflow_support.smoke import workflow_lifecycle_smoke

        report = workflow_lifecycle_smoke(root, workflow_name)
        cleanup = report.get("cleanup") if isinstance(report.get("cleanup"), dict) else {}
        ok = report.get("ok") is True and cleanup.get("removed") in {True, False}
        if ok:
            cleanup_parts = [
                f"removed={cleanup.get('removed')}",
                f"path={cleanup.get('path', '')}",
            ]
            if cleanup.get("reason"):
                cleanup_parts.append(f"reason={cleanup.get('reason')}")
            return True, f"workflow lifecycle smoke passed; cleanup {' '.join(cleanup_parts)}"
        failed = [item for item in report.get("checks", []) if isinstance(item, dict) and item.get("ok") is False]
        return False, f"workflow lifecycle smoke failed: {failed or report}"
    return False, f"unknown assertion type: {assertion_type}"


def run_eval(args: Args) -> dict[str, Any]:
    root = args.root.expanduser().resolve()
    module_dir = workflow_dir(root, args.workflow_name)
    suite = load_suite(args.suite.expanduser().resolve())
    errors, warnings, _modules = validate_automations.validate_automations(
        root,
        workflow_name=args.workflow_name,
    )
    validation = {"ok": not errors, "errors": errors, "warnings": warnings}
    cases = suite.get("evals") or suite.get("cases") or []
    if not isinstance(cases, list):
        raise SystemExit("workflow eval suite must contain an evals or cases list.")
    if not cases:
        raise SystemExit("workflow eval suite must contain at least one eval case.")

    passed = 0
    failed = 0
    results: list[dict[str, Any]] = []
    command_runner = CommandRunner()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise SystemExit(f"eval case {index + 1} must be an object.")
        case_id = str(case.get("id", index + 1))
        assertion_results: list[dict[str, Any]] = []
        case_ok = True
        for assertion in normalize_assertions(case):
            ok, message = check_assertion(
                assertion,
                root=root,
                module_dir=module_dir,
                workflow_name=args.workflow_name,
                validation=validation,
                command_runner=command_runner,
            )
            case_ok = case_ok and ok
            assertion_results.append({"type": assertion["type"], "ok": ok, "message": message})
        if case_ok:
            passed += 1
        else:
            failed += 1
        results.append(
            {
                "id": case_id,
                "name": str(case.get("name", case_id)),
                "ok": case_ok,
                "assertions": assertion_results,
            }
        )

    return {
        "schema_version": 1,
        "tool": "eval-workflow",
        "workflow": args.workflow_name,
        "workflow_path": common.relative(root, module_dir),
        "suite": str(args.suite.expanduser().resolve()),
        "summary": {"passed": passed, "failed": failed, "total": passed + failed},
        "command_telemetry": command_runner.telemetry(),
        "validation": validation,
        "results": results,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Workflow Eval Report",
        "",
        f"- Workflow: `{report['workflow']}`",
        f"- Suite: `{report['suite']}`",
        f"- Passed: {summary['passed']}/{summary['total']}",
        f"- Command assertions: {report['command_telemetry']['command_assertions']}",
        f"- Command executions: {report['command_telemetry']['command_executions']}",
        "",
        "## Results",
        "",
    ]
    for result in report["results"]:
        status = "pass" if result["ok"] else "fail"
        lines.append(f"- `{result['id']}` {status}: {result['name']}")
        for assertion in result["assertions"]:
            has_lifecycle_evidence = assertion.get("type") == "workflow_lifecycle_smoke_ok" and "cleanup" in str(
                assertion.get("message", "")
            )
            if not assertion["ok"] or has_lifecycle_evidence:
                lines.append(f"  - {assertion['message']}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="repository root; defaults to script parent")
    parser.add_argument("--name", required=True, dest="workflow_name")
    parser.add_argument("--suite", required=True, help="JSON workflow eval suite")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    return parser


def default_root() -> Path:
    return Path(__file__).resolve().parents[4]


def main() -> int:
    common.require_supported_python()
    parsed = build_parser().parse_args()
    args = Args(
        root=Path(parsed.root).expanduser().resolve() if parsed.root else default_root(),
        workflow_name=parsed.workflow_name,
        suite=Path(parsed.suite).expanduser().resolve(),
        output_format=parsed.output_format,
        summary=bool(parsed.summary),
        compact=bool(parsed.compact),
    )
    report = run_eval(args)
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 1 if report["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
