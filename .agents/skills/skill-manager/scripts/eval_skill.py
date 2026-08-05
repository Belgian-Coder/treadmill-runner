#!/usr/bin/env python3
"""Run deterministic, local eval assertions against a skill folder."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import compare_skill_versions
import measure_skill_budget
import skill_manager_common as common
import validate_skill

BROAD_TRIGGER_PATTERN = re.compile(
    r"\b(always|anything|everything|expert|all tasks|any task|general purpose)\b",
    re.IGNORECASE,
)
COMPLETION_TERMS = ("skipped", "blocked", "failed", "validation")
STOP_FALLBACK_TERMS = ("stop", "fallback", "blocked", "ask", "retry", "non-blocking")
DEFAULT_COMMAND_TIMEOUT_SECONDS = 120


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


class CommandResultCache:
    """Cache subprocess results for identical commands within one eval run."""

    def __init__(self) -> None:
        self._results: dict[tuple[str, tuple[str, ...], int], tuple[int, str]] = {}
        self.command_assertions = 0
        self.unique_command_executions = 0
        self.command_cache_hits = 0

    def record_assertion(self) -> None:
        self.command_assertions += 1

    def run(self, argv: list[str], *, cwd: Path, timeout_seconds: int) -> tuple[int, str]:
        normalized_argv = tuple(str(part) for part in argv)
        key = (os.path.normcase(str(cwd.resolve())), normalized_argv, timeout_seconds)
        cached = self._results.get(key)
        if cached is not None:
            self.command_cache_hits += 1
            return cached

        self.unique_command_executions += 1
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
        self._results[key] = result
        return result

    def telemetry(self) -> dict[str, int]:
        return {
            "command_assertions": self.command_assertions,
            "unique_command_executions": self.unique_command_executions,
            "command_cache_hits": self.command_cache_hits,
        }


def load_suite(path: Path) -> dict[str, Any]:
    data, error = common.read_json_file(path)
    if error:
        raise SystemExit(error)
    if not isinstance(data, dict):
        raise SystemExit("eval suite must be a JSON object.")
    return data


def value_at(data: Any, dotted_path: str) -> Any:
    current = data
    for part in dotted_path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def repo_root_for_skill(skill_dir: Path) -> Path | None:
    for current in [skill_dir.resolve(), *skill_dir.resolve().parents]:
        if (current / ".agents" / "manage.py").exists():
            return current
    return None


def repo_root_for_current_context() -> Path | None:
    for start in (Path.cwd(), Path(__file__).resolve()):
        for current in [start.resolve(), *start.resolve().parents]:
            if (current / ".agents" / "manage.py").exists():
                return current
    return None


def resolve_skill_dir(value: str) -> Path:
    raw = value.strip()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if (candidate / "SKILL.md").exists() or (candidate / "module.json").exists():
        return candidate

    identifier = raw.removeprefix("skill:").strip()
    if identifier and not any(separator in identifier for separator in ("/", "\\")):
        root = repo_root_for_current_context()
        if root is not None:
            for skill_dir in common.discover_skill_dirs(root):
                if skill_dir.name == identifier:
                    return skill_dir.resolve()
                manifest, error = common.load_skill_manifest(skill_dir)
                if not error and isinstance(manifest, dict):
                    if identifier in {str(manifest.get("id") or ""), str(manifest.get("name") or "")}:
                        return skill_dir.resolve()
    return candidate


def text_contains_terms(text: str, terms: list[str] | tuple[str, ...], require: str) -> bool:
    lowered = text.lower()
    if require == "any":
        return any(term.lower() in lowered for term in terms)
    return all(term.lower() in lowered for term in terms)


def manifest_compatibility_required(manifest: dict[str, Any], tools: list[str]) -> tuple[bool, str]:
    compatibility = manifest.get("compatibility")
    if not isinstance(compatibility, dict):
        return False, "module.json compatibility must be an object"
    missing = [tool for tool in tools if compatibility.get(tool) != "required"]
    if missing:
        return False, f"expected compatibility required for: {', '.join(missing)}"
    return True, "compatibility requirements are present"


def repo_relative_or_absolute(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def resolve_skill_relative_path(skill_dir: Path, value: object) -> tuple[Path | None, str]:
    target = (skill_dir / str(value or "")).resolve()
    skill_root = skill_dir.resolve()
    if target != skill_root and skill_root not in target.parents:
        return None, f"expected path to stay under skill folder: {value}"
    return target, ""


def resolve_repo_relative_path(skill_dir: Path, value: object) -> tuple[Path | None, str]:
    root = repo_root_for_skill(skill_dir)
    if root is None:
        return None, "repository root not found for repo file assertion."
    raw = Path(str(value or ""))
    if raw.is_absolute():
        return None, f"expected repo-relative path, got absolute path: {value}"
    target = (root / raw).resolve()
    root = root.resolve()
    if target != root and root not in target.parents:
        return None, f"expected path to stay under repository root: {value}"
    return target, ""


def normalize_repo_command(command: object, skill_dir: Path) -> list[str]:
    if not isinstance(command, list):
        raise SystemExit("repo command assertion requires a command list.")
    root = repo_root_for_skill(skill_dir)
    skill_value = str(skill_dir)
    if root is not None:
        skill_value = repo_relative_or_absolute(root, skill_dir)
    return [
        skill_value if str(part) == "<skill>" else str(part)
        for part in command
    ]


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


def run_python_script(
    skill_dir: Path,
    script: object,
    *,
    command_cache: CommandResultCache,
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> tuple[int, str]:
    script_path, error = resolve_skill_relative_path(skill_dir, script)
    if error or script_path is None:
        return 1, error
    if not script_path.exists():
        return 1, f"script not found: {script_path}"
    if script_path.suffix.lower() != ".py":
        return 1, f"expected a Python script: {script_path}"
    root = repo_root_for_skill(skill_dir) or skill_dir
    return command_cache.run(
        [sys.executable, "-B", str(script_path)],
        cwd=root,
        timeout_seconds=timeout_seconds,
    )


def run_repo_command(
    skill_dir: Path,
    command: list[str],
    *,
    command_cache: CommandResultCache,
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> tuple[int, str]:
    root = repo_root_for_skill(skill_dir)
    if root is None:
        return 1, "repository launcher not found for skill."
    return command_cache.run(
        [sys.executable, "-B", str(root / ".agents" / "manage.py"), *command],
        cwd=root,
        timeout_seconds=timeout_seconds,
    )


def normalize_assertions(case: dict[str, Any]) -> list[dict[str, Any]]:
    values = case.get("assertions", [])
    if not isinstance(values, list):
        raise SystemExit(f"eval case {case.get('id', '<unknown>')} assertions must be a list.")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(values):
        if not isinstance(item, dict):
            raise SystemExit(
                f"eval case {case.get('id', '<unknown>')} assertion {index + 1} must be an object."
            )
        if not isinstance(item.get("type"), str):
            raise SystemExit(
                f"eval case {case.get('id', '<unknown>')} assertion {index + 1} is missing type."
            )
        normalized.append(item)
    return normalized


def check_assertion(
    assertion: dict[str, Any],
    *,
    skill_dir: Path,
    manifest: dict[str, Any],
    metadata: dict[str, str],
    validation: dict[str, Any],
    budget: dict[str, Any],
    comparison: dict[str, Any] | None,
    command_cache: CommandResultCache,
) -> tuple[bool, str]:
    assertion_type = assertion["type"]

    if assertion_type == "file_exists":
        target = skill_dir / str(assertion.get("path", ""))
        return target.exists(), f"expected file to exist: {target}"
    if assertion_type == "file_absent":
        target = skill_dir / str(assertion.get("path", ""))
        return not target.exists(), f"expected file to be absent: {target}"
    if assertion_type == "file_contains":
        target, error = resolve_skill_relative_path(skill_dir, assertion.get("path", ""))
        if error or target is None:
            return False, error
        expected = str(assertion.get("text", ""))
        if not target.exists():
            return False, f"expected file to exist: {target}"
        text = target.read_text(encoding="utf-8", errors="replace")
        return expected in text, f"expected {target} to contain: {expected}"
    if assertion_type == "repo_file_contains":
        target, error = resolve_repo_relative_path(skill_dir, assertion.get("path", ""))
        if error or target is None:
            return False, error
        expected = str(assertion.get("text", ""))
        if not target.exists():
            return False, f"expected repo file to exist: {target}"
        text = target.read_text(encoding="utf-8", errors="replace")
        return expected in text, f"expected repo file {target} to contain: {expected}"
    if assertion_type == "skill_contains":
        text = common.read_text(skill_dir / "SKILL.md")
        expected = str(assertion.get("text", ""))
        return expected in text, f"expected SKILL.md to contain: {expected}"
    if assertion_type == "description_contains":
        expected = str(assertion.get("text", "")).lower()
        description = metadata.get("description", "").lower()
        return expected in description, f"expected description to contain: {expected}"
    if assertion_type == "trigger_quality":
        description = metadata.get("description", "").strip()
        min_chars = int(assertion.get("min_chars", 30))
        max_chars = int(assertion.get("max_chars", 700))
        if not description.startswith("Use when "):
            return False, "expected description to start with 'Use when '"
        if not (min_chars <= len(description) <= max_chars):
            return False, f"expected description length between {min_chars} and {max_chars}, got {len(description)}"
        if BROAD_TRIGGER_PATTERN.search(description):
            return False, "expected description to avoid broad always-on trigger language"
        return True, "trigger quality checks passed"
    if assertion_type == "manifest_field_equals":
        path = str(assertion.get("path", ""))
        actual = value_at(manifest, path)
        expected = assertion.get("value")
        return actual == expected, f"expected manifest {path} == {expected!r}, got {actual!r}"
    if assertion_type == "risk_declared":
        key = str(assertion.get("key", ""))
        expected = bool(assertion.get("value", True))
        actual = bool(value_at(manifest, f"risk.{key}"))
        return actual == expected, f"expected risk.{key} == {expected}, got {actual}"
    if assertion_type == "compatibility_required":
        tools = assertion.get("tools", ["codex", "github_copilot", "claude_code"])
        if not isinstance(tools, list):
            return False, "compatibility_required tools must be a list"
        return manifest_compatibility_required(manifest, [str(tool) for tool in tools])
    if assertion_type == "risk_profile_covers_flags":
        risk = manifest.get("risk")
        if not isinstance(risk, dict):
            return False, "module.json risk must be an object"
        profile = str(risk.get("profile", ""))
        required = common.required_risk_profile(risk)
        return (
            common.risk_profile_covers(profile, required),
            f"expected risk.profile {profile!r} to cover required profile {required!r}",
        )
    if assertion_type == "completion_contract_terms":
        terms = assertion.get("terms", list(COMPLETION_TERMS))
        if not isinstance(terms, list):
            return False, "completion_contract_terms terms must be a list"
        text = common.read_text(skill_dir / "SKILL.md")
        return (
            text_contains_terms(text, [str(term) for term in terms], "all"),
            f"expected SKILL.md completion contract terms: {', '.join(str(term) for term in terms)}",
        )
    if assertion_type == "stop_or_fallback_terms":
        terms = assertion.get("terms", list(STOP_FALLBACK_TERMS))
        require = str(assertion.get("require", "any"))
        if not isinstance(terms, list):
            return False, "stop_or_fallback_terms terms must be a list"
        if require not in {"any", "all"}:
            return False, "stop_or_fallback_terms require must be 'any' or 'all'"
        text = common.read_text(skill_dir / "SKILL.md")
        return (
            text_contains_terms(text, [str(term) for term in terms], require),
            f"expected SKILL.md stop/fallback terms ({require}): {', '.join(str(term) for term in terms)}",
        )
    if assertion_type == "validation_ok":
        return bool(validation["ok"]), "expected skill validation to pass"
    if assertion_type == "budget_skill_words_at_most":
        limit = int(assertion.get("value", 0))
        actual = int(budget["skill_md"]["words"])
        return actual <= limit, f"expected SKILL.md words <= {limit}, got {actual}"
    if assertion_type == "compare_decision":
        if comparison is None:
            return False, "compare_decision requires --baseline"
        expected = str(assertion.get("value", ""))
        actual = str(comparison["recommended_decision"]["decision"])
        return actual == expected, f"expected compare decision {expected}, got {actual}"
    if assertion_type == "compare_change_class":
        if comparison is None:
            return False, "compare_change_class requires --baseline"
        expected = str(assertion.get("value", ""))
        actual = str(comparison["change_class"])
        return actual == expected, f"expected compare change class {expected}, got {actual}"
    if assertion_type == "repo_command_succeeds":
        command_cache.record_assertion()
        command = normalize_repo_command(assertion.get("command"), skill_dir)
        status, output = run_repo_command(
            skill_dir,
            command,
            command_cache=command_cache,
            timeout_seconds=timeout_seconds_from_assertion(assertion),
        )
        return status == 0, f"expected repo command to succeed: {' '.join(command)}; output: {output.strip()}"
    if assertion_type == "python_script_succeeds":
        command_cache.record_assertion()
        status, output = run_python_script(
            skill_dir,
            assertion.get("path", ""),
            command_cache=command_cache,
            timeout_seconds=timeout_seconds_from_assertion(assertion),
        )
        expected = str(assertion.get("output_contains", ""))
        ok = status == 0 and (not expected or expected in output)
        return (
            ok,
            f"expected Python script to succeed: {assertion.get('path', '')}; output: {output.strip()}",
        )
    if assertion_type == "repo_command_output_contains":
        command_cache.record_assertion()
        command = normalize_repo_command(assertion.get("command"), skill_dir)
        expected = str(assertion.get("text", ""))
        status, output = run_repo_command(
            skill_dir,
            command,
            command_cache=command_cache,
            timeout_seconds=timeout_seconds_from_assertion(assertion),
        )
        return (
            status == 0 and expected in output,
            f"expected repo command output to contain {expected!r}: {' '.join(command)}",
        )
    if assertion_type == "public_command_behavior":
        command_cache.record_assertion()
        command = normalize_repo_command(assertion.get("command"), skill_dir)
        expected = str(assertion.get("text", assertion.get("contains", "")))
        status, output = run_repo_command(
            skill_dir,
            command,
            command_cache=command_cache,
            timeout_seconds=timeout_seconds_from_assertion(assertion),
        )
        ok = status == 0 and (not expected or expected in output)
        return ok, f"expected public command behavior from {' '.join(command)}"
    if assertion_type == "repo_command_json_field_equals":
        command_cache.record_assertion()
        command = normalize_repo_command(assertion.get("command"), skill_dir)
        path = str(assertion.get("path", ""))
        expected = assertion.get("value")
        status, output = run_repo_command(
            skill_dir,
            command,
            command_cache=command_cache,
            timeout_seconds=timeout_seconds_from_assertion(assertion),
        )
        if status != 0:
            return False, f"expected repo command to succeed: {' '.join(command)}; output: {output.strip()}"
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            return False, f"expected JSON output from {' '.join(command)}: {exc}"
        actual = value_at(data, path)
        return actual == expected, f"expected command JSON {path} == {expected!r}, got {actual!r}"

    return False, f"unknown assertion type: {assertion_type}"


def run_eval(args: argparse.Namespace) -> dict[str, Any]:
    skill_dir = resolve_skill_dir(str(args.skill))
    suite = load_suite(Path(args.suite).expanduser().resolve())
    manifest, manifest_error = common.load_skill_manifest(skill_dir)
    if manifest_error or not isinstance(manifest, dict):
        raise SystemExit(manifest_error or "skill manifest could not be loaded.")
    metadata, metadata_error = common.parse_frontmatter_file(skill_dir / "SKILL.md")
    if metadata_error or not isinstance(metadata, dict):
        raise SystemExit(metadata_error or "skill metadata could not be loaded.")
    errors, warnings = validate_skill.validate_skill(skill_dir)
    budget = measure_skill_budget.measure_skill(skill_dir)
    comparison = None
    if args.baseline and args.baseline != "none":
        comparison = compare_skill_versions.compare_paths(
            Path(args.baseline).expanduser().resolve(),
            skill_dir,
        )

    cases = suite.get("evals") or suite.get("cases") or []
    if not isinstance(cases, list):
        raise SystemExit("eval suite must contain an evals or cases list.")

    results: list[dict[str, Any]] = []
    passed = 0
    failed = 0
    command_cache = CommandResultCache()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise SystemExit(f"eval case {index + 1} must be an object.")
        case_id = str(case.get("id", index + 1))
        assertion_results: list[dict[str, Any]] = []
        case_ok = True
        for assertion in normalize_assertions(case):
            ok, message = check_assertion(
                assertion,
                skill_dir=skill_dir,
                manifest=manifest,
                metadata=metadata,
                validation={"ok": not errors, "errors": errors, "warnings": warnings},
                budget=budget,
                comparison=comparison,
                command_cache=command_cache,
            )
            case_ok = case_ok and ok
            assertion_results.append(
                {"type": assertion["type"], "ok": ok, "message": message}
            )
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
        "version": 1,
        "skill": str(skill_dir),
        "suite": str(Path(args.suite).expanduser().resolve()),
        "baseline": None if not args.baseline or args.baseline == "none" else str(Path(args.baseline).expanduser().resolve()),
        "summary": {"passed": passed, "failed": failed, "total": passed + failed},
        "command_telemetry": command_cache.telemetry(),
        "validation": {"ok": not errors, "errors": errors, "warnings": warnings},
        "results": results,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Skill Eval Report",
        "",
        f"- Skill: `{report['skill']}`",
        f"- Suite: `{report['suite']}`",
        f"- Baseline: `{report['baseline'] or 'none'}`",
        f"- Passed: {summary['passed']}/{summary['total']}",
        f"- Command assertions: {report['command_telemetry']['command_assertions']}",
        f"- Unique command executions: {report['command_telemetry']['unique_command_executions']}",
        f"- Command cache hits: {report['command_telemetry']['command_cache_hits']}",
        "",
        "## Results",
        "",
    ]
    for result in report["results"]:
        status = "pass" if result["ok"] else "fail"
        lines.append(f"- `{result['id']}` {status}: {result['name']}")
        for assertion in result["assertions"]:
            if not assertion["ok"]:
                lines.append(f"  - {assertion['message']}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", required=True, help="skill id or folder")
    parser.add_argument("--suite", required=True, help="JSON eval suite")
    parser.add_argument("--baseline", default="none", help="old skill folder or 'none'")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown", dest="output_format")
    return parser


def main() -> int:
    common.require_supported_python()
    args = build_parser().parse_args()
    report = run_eval(args)
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 1 if report["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
