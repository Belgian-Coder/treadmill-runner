#!/usr/bin/env python3
"""Validate canonical skill compatibility across agent adapter surfaces."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

sys.dont_write_bytecode = True

import skill_manager_common as common
from repo_support import repo_common as repo
from repo_support import repo_context_guardrails
from repo_support import repo_generated
from repo_support import repo_health
from repo_support import repo_policy

REQUIRED_COMPATIBILITY = ("codex", "github_copilot", "claude_code")
HOST_PROBE_TIMEOUT_SECONDS = 10
HOST_PROBE_OUTPUT_LIMIT = 64_000
HOST_PROBE_SPECS: dict[str, dict[str, object]] = {
    "codex": {
        "executable": "codex",
        "capabilities": {
            "model-selection": ("--model",),
            "session-resume": ("resume",),
            "mcp": ("mcp",),
        },
        "required": ("model-selection", "session-resume", "mcp"),
    },
    "github_copilot": {
        "executable": "copilot",
        "capabilities": {
            "custom-agent": ("--agent",),
            "model-selection": ("--model",),
            "reasoning-effort": ("--reasoning-effort", "--effort"),
            "session-resume": ("--resume", "--continue"),
            "mcp": (" mcp", "mcp "),
            "tool-controls": ("--allow-tool", "--available-tools"),
            "telemetry": ("opentelemetry", "monitoring"),
        },
        "required": ("custom-agent", "model-selection", "session-resume", "mcp"),
    },
    "claude_code": {
        "executable": "claude",
        "capabilities": {
            "custom-agent": ("--agent", "--agents"),
            "model-selection": ("--model",),
            "reasoning-effort": ("--effort",),
            "session-resume": ("--resume", "--continue"),
            "mcp": (" mcp", "mcp "),
            "tool-controls": ("--allowedtools", "--allowed-tools", "--tools"),
            "structured-output": ("--output-format", "--json-schema"),
        },
        "required": (
            "custom-agent",
            "model-selection",
            "session-resume",
            "mcp",
            "structured-output",
        ),
    },
}


def forbidden_tool_setting_paths(root: Path) -> list[Path]:
    candidates = [
        root / ("." + "codex") / "config.toml",
        root / ("." + "mcp" + ".json"),
        root / ("." + "vscode") / "settings.json",
        root / ("." + "vscode") / "mcp.json",
        root / ("." + "claude") / "settings.json",
        root / ("." + "github") / "copilot" / "settings.json",
        root / ("." + "claude") / "settings.local.json",
    ]
    return [path for path in candidates if path.exists()]


def capture_check(name: str, callback) -> tuple[bool, str]:
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = callback()
    except SystemExit as exc:
        return False, str(exc)
    except Exception as exc:  # pragma: no cover - defensive reporting
        return False, str(exc)
    if int(status) != 0:
        return False, output.getvalue().strip() or f"status {status}"
    return True, output.getvalue().strip() or "ok"


def _safe_host_line(value: object) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return " ".join(text.split())[
        :repo_policy.int_value(repo_policy.project_root(), "limits.output.evidence_snippet_chars")
    ]


def run_host_probe_command(argv: list[str], root: Path) -> dict[str, object]:
    try:
        completed = subprocess.run(
            argv,
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=HOST_PROBE_TIMEOUT_SECONDS,
            check=False,
            env=repo.child_env(),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "output": "",
            "failure": f"timed out after {HOST_PROBE_TIMEOUT_SECONDS} seconds",
            "truncated": False,
        }
    except OSError as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "output": "",
            "failure": _safe_host_line(exc),
            "truncated": False,
        }

    stdout = completed.stdout if isinstance(completed.stdout, str) else ""
    stderr = completed.stderr if isinstance(completed.stderr, str) else ""
    stdout_truncated = len(stdout) > HOST_PROBE_OUTPUT_LIMIT
    stderr_truncated = len(stderr) > HOST_PROBE_OUTPUT_LIMIT
    stdout = stdout[:HOST_PROBE_OUTPUT_LIMIT]
    stderr = stderr[:HOST_PROBE_OUTPUT_LIMIT]
    output = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
    truncated = stdout_truncated or stderr_truncated
    failure_line = next(
        (_safe_host_line(line) for line in output.splitlines() if line.strip()),
        f"exit code {completed.returncode}",
    )
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "output": output,
        "failure": "" if completed.returncode == 0 else failure_line,
        "truncated": truncated,
    }


def _capability_facts(help_text: str, capability_patterns: object) -> dict[str, bool]:
    lowered = help_text.casefold()
    facts: dict[str, bool] = {}
    if not isinstance(capability_patterns, dict):
        return facts
    for name, raw_patterns in capability_patterns.items():
        patterns = raw_patterns if isinstance(raw_patterns, tuple) else ()
        facts[str(name)] = any(str(pattern).casefold() in lowered for pattern in patterns)
    return facts


def _copilot_skill_facts(
    stdout: str,
    stderr: str,
    *,
    root: Path,
    expected_names: set[str],
) -> dict[str, object]:
    parse_error = ""
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        payload = []
        parse_error = _safe_host_line(exc)
    rows = payload if isinstance(payload, list) else []
    project_names = [
        str(row.get("name"))
        for row in rows
        if isinstance(row, dict)
        and row.get("source") == "project"
        and isinstance(row.get("name"), str)
        and str(row.get("name")).strip()
    ]
    unique_project_names = sorted(set(project_names))
    duplicate_project_names = sorted(
        name for name in unique_project_names if project_names.count(name) > 1
    )
    missing_project_names = sorted(expected_names - set(unique_project_names))
    unexpected_project_names = sorted(set(unique_project_names) - expected_names)
    stderr_folded = stderr.casefold()
    repo_markers = (
        str(root).replace("/", "\\").casefold(),
        ".agents\\skills\\",
        ".claude\\skills\\",
        ".github\\skills\\",
        ".agents/skills/",
        ".claude/skills/",
        ".github/skills/",
    )
    failed_loads = "failed to load" in stderr_folded and any(
        marker in stderr_folded for marker in repo_markers
    )
    return {
        "project_skill_count": len(unique_project_names),
        "expected_project_skill_count": len(expected_names),
        "duplicate_project_names": duplicate_project_names,
        "missing_project_names": missing_project_names,
        "unexpected_project_names": unexpected_project_names,
        "failed_loads": failed_loads,
        "json_parse_error": parse_error,
    }


def installed_host_validation_report(
    root: Path,
    *,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[[list[str], Path], dict[str, object]] = run_host_probe_command,
) -> dict[str, object]:
    hosts: list[dict[str, object]] = []
    issues: list[str] = []
    installed_count = 0
    expected_project_skills = {
        skill_dir.name for skill_dir in repo.skill_directories(root / ".agents" / "skills")
    }
    for host_surface, spec in HOST_PROBE_SPECS.items():
        executable_name = str(spec["executable"])
        executable = which(executable_name)
        if not executable:
            hosts.append(
                {
                    "host_surface": host_surface,
                    "executable": executable_name,
                    "installed": False,
                    "ok": True,
                    "status": "not-installed",
                    "capabilities": {},
                }
            )
            continue

        installed_count += 1
        probe_prefix = (
            [executable, "--no-auto-update", "--no-color"]
            if host_surface == "github_copilot"
            else [executable]
        )
        version_result = runner([*probe_prefix, "--version"], root)
        help_result = runner([*probe_prefix, "--help"], root)
        help_output = str(help_result.get("output") or "")
        capabilities = _capability_facts(help_output, spec.get("capabilities"))
        required = tuple(str(item) for item in spec.get("required", ()))
        missing_capabilities = [name for name in required if capabilities.get(name) is not True]
        host_issues: list[str] = []
        if version_result.get("ok") is not True:
            host_issues.append(
                f"version probe failed: {_safe_host_line(version_result.get('failure'))}"
            )
        if help_result.get("ok") is not True:
            host_issues.append(
                f"help probe failed: {_safe_host_line(help_result.get('failure'))}"
            )
        if help_result.get("ok") is True and missing_capabilities:
            host_issues.append(
                f"help is missing required capabilities: {', '.join(missing_capabilities)}"
            )

        discovery: dict[str, object] | None = None
        if host_surface == "github_copilot" and not host_issues:
            discovery_result = runner(
                [executable, "--no-auto-update", "--no-color", "skill", "list", "--json"],
                root,
            )
            discovery_stdout = str(
                discovery_result.get("stdout")
                if "stdout" in discovery_result
                else discovery_result.get("output") or ""
            )
            discovery_stderr = str(discovery_result.get("stderr") or "")
            discovery = _copilot_skill_facts(
                discovery_stdout,
                discovery_stderr,
                root=root,
                expected_names=expected_project_skills,
            )
            discovery["ok"] = (
                discovery_result.get("ok") is True
                and not discovery["failed_loads"]
                and not discovery["json_parse_error"]
                and not discovery["duplicate_project_names"]
                and not discovery["missing_project_names"]
                and not discovery["unexpected_project_names"]
            )
            discovery["truncated"] = bool(discovery_result.get("truncated"))
            if discovery_result.get("ok") is not True:
                host_issues.append(
                    f"skill discovery failed: {_safe_host_line(discovery_result.get('failure'))}"
                )
            elif discovery["failed_loads"]:
                host_issues.append("skill discovery reported failed loads")
            elif discovery["json_parse_error"]:
                host_issues.append(
                    f"skill discovery returned invalid JSON: {discovery['json_parse_error']}"
                )
            elif discovery["duplicate_project_names"]:
                host_issues.append("skill discovery reported duplicate project skill names")
            elif discovery["missing_project_names"]:
                host_issues.append("skill discovery is missing canonical project skills")
            elif discovery["unexpected_project_names"]:
                host_issues.append("skill discovery reported unexpected project skills")

        for issue in host_issues:
            issues.append(f"{host_surface}: {issue}")
        version_output = str(version_result.get("output") or "")
        version = next(
            (_safe_host_line(line) for line in version_output.splitlines() if line.strip()),
            "",
        )
        host: dict[str, object] = {
            "host_surface": host_surface,
            "executable": str(executable),
            "installed": True,
            "ok": not host_issues,
            "status": "passed" if not host_issues else "failed",
            "version": version,
            "capabilities": capabilities,
            "missing_required_capabilities": missing_capabilities,
            "issues": host_issues,
        }
        if discovery is not None:
            host["skill_discovery"] = discovery
        hosts.append(host)

    failed_count = sum(1 for host in hosts if host.get("installed") and not host.get("ok"))
    not_installed_count = len(hosts) - installed_count
    status = "failed" if issues else ("passed" if not not_installed_count else "partial")
    return {
        "schema_version": 1,
        "tool": "validate-agent-compatibility.installed-hosts",
        "ok": not issues,
        "status": status,
        "host_count": len(hosts),
        "installed_count": installed_count,
        "not_installed_count": not_installed_count,
        "failed_count": failed_count,
        "does_not_invoke_models": True,
        "hosts": hosts,
        "issues": issues,
    }


def check_skill_compatibility(root: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for skill_dir in common.discover_skill_dirs(root):
        manifest, error = common.load_skill_manifest(skill_dir)
        if error or not isinstance(manifest, dict):
            errors.append(f"{repo.relative(root, skill_dir)} could not load module.json: {error}")
            continue
        if manifest.get("status", "accepted") != "accepted":
            continue
        compatibility = manifest.get("compatibility")
        if not isinstance(compatibility, dict):
            errors.append(f"{repo.relative(root, skill_dir)} compatibility must be an object")
            continue
        missing = [
            tool
            for tool in REQUIRED_COMPATIBILITY
            if compatibility.get(tool) != "required"
        ]
        if missing:
            errors.append(
                f"{repo.relative(root, skill_dir)} must keep compatibility required for: {', '.join(missing)}"
            )

        warnings.extend(portability_warnings(root, skill_dir))

        for forbidden in (
            skill_dir / "agents" / "openai.yaml",
            skill_dir / ("." + "claude"),
            skill_dir / ("." + "github"),
            skill_dir / ("." + "codex"),
        ):
            if forbidden.exists():
                warnings.append(
                    f"{repo.relative(root, forbidden)} is tool-specific; keep canonical behavior in SKILL.md, module.json, docs, and scripts."
                )
    return errors, warnings


def portability_warnings(root: Path, skill_dir: Path) -> list[str]:
    skill_path = skill_dir / "SKILL.md"
    if not skill_path.exists():
        return []
    text = common.read_text(skill_path, limit=120_000)
    warnings: list[str] = []
    words = common.word_count(text)
    lines = text.splitlines()
    word_warn = repo_policy.int_value(root, "limits.compatibility.skill_warn_words")
    line_warn = repo_policy.int_value(root, "limits.compatibility.skill_warn_lines")
    description_line = next((line.strip() for line in lines if line.strip().startswith("description:")), "")
    if reuses_yaml_quoting(description_line):
        warnings.append(
            f"{repo.relative(root, skill_path)} uses quoted frontmatter; keep frontmatter scalar syntax plain for adapter portability."
        )
    if words > word_warn:
        warnings.append(repo_policy.tagged_warning(
            "compatibility.skill.words",
            f"{repo.relative(root, skill_path)} has {words} words; cross-agent adapters stay cheaper when SKILL.md stays under {word_warn} words.",
        ))
    if len(lines) > line_warn:
        warnings.append(repo_policy.tagged_warning(
            "compatibility.skill.lines",
            f"{repo.relative(root, skill_path)} has {len(lines)} lines; prefer docs/ progressive disclosure for cross-agent adapter readability.",
        ))
    return warnings


def reuses_yaml_quoting(description_line: str) -> bool:
    if not description_line:
        return False
    value = description_line.split(":", 1)[1].strip() if ":" in description_line else ""
    return len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]


def flat_skill_layout_errors(root: Path) -> list[str]:
    skills_root = root / ".agents" / "skills"
    if not skills_root.is_dir():
        return []
    errors: list[str] = []
    for child in sorted(skills_root.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir() or (child / "SKILL.md").exists():
            continue
        nested = sorted(child.glob("*/SKILL.md"), key=lambda item: item.as_posix().lower())
        if nested:
            nested_names = [path.parent.name for path in nested[:5]]
            suffix = "..." if len(nested) > 5 else ""
            errors.append(
                f"{repo.relative(root, child)} contains nested skill layout {nested_names}{suffix}; "
                "skills must use flat .agents/skills/<name>/SKILL.md paths for cross-agent discovery."
            )
    return errors


def claude_adapter_portability_errors(root: Path) -> list[str]:
    adapters_root = root / ".claude" / "skills"
    if not adapters_root.is_dir():
        return []
    errors: list[str] = []
    for adapter in sorted(adapters_root.glob("*/SKILL.md"), key=lambda item: item.as_posix()):
        relative = repo.relative(root, adapter)
        try:
            text = common.read_text(adapter, limit=120_000)
        except (OSError, UnicodeError) as exc:
            errors.append(f"{relative} could not be read: {_safe_host_line(exc)}")
            continue
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            errors.append(f"{relative} must start with YAML frontmatter for cross-host skill discovery")
            continue
        metadata, metadata_error = common.parse_frontmatter_file(adapter)
        if metadata_error or not isinstance(metadata, dict):
            errors.append(f"{relative} has invalid YAML frontmatter: {metadata_error}")
            continue
        if metadata.get("name") != adapter.parent.name:
            errors.append(f"{relative} frontmatter name must match its skill directory")
        if not str(metadata.get("description") or "").strip():
            errors.append(f"{relative} frontmatter description must be non-empty")
        if repo_generated.GENERATED_CLAUDE_HEADER not in text[:4096]:
            errors.append(f"{relative} is missing its bounded generated-adapter marker")
        if "This is a generated Claude Code adapter. Read and follow " not in text[:8192]:
            errors.append(f"{relative} is missing its generated-adapter body signature")
    return errors


def adapter_surfaces(root: Path) -> list[dict[str, Any]]:
    surfaces = [
        {
            "surface": "codex",
            "path": "AGENTS.md",
            "canonical_source": "AGENTS.md",
            "generated": False,
            "exists": (root / "AGENTS.md").exists(),
        },
        {
            "surface": "opencode",
            "path": "AGENTS.md",
            "canonical_source": "AGENTS.md",
            "generated": False,
            "exists": (root / "AGENTS.md").exists(),
        },
        {
            "surface": "cline",
            "path": "AGENTS.md",
            "canonical_source": "AGENTS.md",
            "generated": False,
            "exists": (root / "AGENTS.md").exists(),
        },
        {
            "surface": "roo_code",
            "path": "AGENTS.md",
            "canonical_source": "AGENTS.md",
            "generated": False,
            "exists": (root / "AGENTS.md").exists(),
        },
        {
            "surface": "github_copilot",
            "path": ".github/copilot-instructions.md",
            "canonical_source": "AGENTS.md",
            "generated": True,
            "exists": (root / ".github" / "copilot-instructions.md").exists(),
        },
        {
            "surface": "claude_code",
            "path": ".claude/CLAUDE.md and .claude/skills/*/SKILL.md",
            "canonical_source": "AGENTS.md and .agents/skills/*/SKILL.md",
            "generated": True,
            "exists": (root / ".claude" / "CLAUDE.md").exists() and (root / ".claude" / "skills").exists(),
        },
        {
            "surface": "gemini_cli",
            "path": "GEMINI.md",
            "canonical_source": "AGENTS.md",
            "generated": True,
            "exists": (root / "GEMINI.md").exists(),
        },
        {
            "surface": "continue",
            "path": ".continue/rules/repository-instructions.md",
            "canonical_source": "AGENTS.md",
            "generated": True,
            "exists": (root / ".continue" / "rules" / "repository-instructions.md").exists(),
        },
        {
            "surface": "aider",
            "path": ".aider.conf.yml",
            "canonical_source": "AGENTS.md",
            "generated": True,
            "exists": (root / ".aider.conf.yml").exists(),
        },
    ]
    strategies = {
        "codex": ("root-router-loads-nested-files", "AGENTS.md route-first fallback"),
        "opencode": ("root-router-loads-nested-files", "AGENTS.md route-first fallback"),
        "cline": ("root-router-loads-nested-files", "AGENTS.md route-first fallback"),
        "roo_code": ("root-router-loads-nested-files", "AGENTS.md route-first fallback"),
        "github_copilot": ("generated-compact-root-instructions", ".github/copilot-instructions.md fallback"),
        "claude_code": ("generated-nested-skill-adapters", ".claude/CLAUDE.md plus .claude/skills fallback"),
        "gemini_cli": ("generated-compact-root-instructions", "GEMINI.md fallback"),
        "continue": ("generated-compact-root-instructions", ".continue/rules/repository-instructions.md fallback"),
        "aider": ("generated-compact-root-instructions", ".aider.conf.yml fallback"),
    }
    for surface in surfaces:
        strategy, fallback = strategies.get(str(surface.get("surface")), ("unknown", "AGENTS.md fallback"))
        surface["navigation_strategy"] = strategy
        surface["navigation_fallback"] = fallback
        surface["first_orientation_file"] = "automations/navigation/artifacts/maps/HANDOFF.md"
        surface["raw_navigation_json"] = "tool-only"
    return surfaces


def adapter_navigation_strategy_report(root: Path, surfaces: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        agents_text = (root / "AGENTS.md").read_text(encoding="utf-8")
    except OSError:
        agents_text = ""
    missing: list[str] = []
    if "automations/navigation/artifacts/maps/HANDOFF.md" not in agents_text:
        missing.append("AGENTS.md does not reference HANDOFF.md")
    if "raw navigation JSON is tool-only" not in agents_text:
        missing.append("AGENTS.md does not declare raw navigation JSON as tool-only")
    strategy_counts: dict[str, int] = {}
    for surface in surfaces:
        strategy = str(surface.get("navigation_strategy", "unknown"))
        strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
    return {
        "ok": not missing,
        "status": "passed" if not missing else "failed",
        "first_orientation_file": "automations/navigation/artifacts/maps/HANDOFF.md",
        "raw_navigation_json": "tool-only",
        "surface_count": len(surfaces),
        "strategy_counts": dict(sorted(strategy_counts.items())),
        "issues": missing,
    }


def adapter_guardrail_paths(root: Path, surfaces: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for surface in surfaces:
        raw_path = str(surface.get("path") or "")
        if raw_path.startswith(".claude/CLAUDE.md"):
            candidates = [".claude/CLAUDE.md"]
        else:
            candidates = [raw_path]
        for candidate in candidates:
            if candidate and (root / candidate).is_file() and candidate not in paths:
                paths.append(candidate)
    return paths


def adapter_context_guardrail_report(root: Path, surfaces: list[dict[str, Any]]) -> dict[str, Any]:
    paths = adapter_guardrail_paths(root, surfaces)
    report = repo_context_guardrails.context_guardrail_report(root, paths=paths, include_protected=False)
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    return {
        "ok": bool(report.get("ok")),
        "status": report.get("status", "unknown"),
        "scanned_count": report.get("scanned_count", 0),
        "finding_count": report.get("finding_count", 0),
        "findings": findings,
    }


def build_report(root: Path, *, include_installed_hosts: bool = False) -> dict[str, Any]:
    root = root.expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []

    compatibility_errors, compatibility_warnings = check_skill_compatibility(root)
    errors.extend(compatibility_errors)
    warnings.extend(compatibility_warnings)
    checks.append(
        {
            "name": "skill compatibility",
            "ok": not compatibility_errors,
            "summary": f"{len(compatibility_errors)} error(s), {len(compatibility_warnings)} warning(s)",
        }
    )

    layout_errors = flat_skill_layout_errors(root)
    errors.extend(layout_errors)
    checks.append(
        {
            "name": "flat skill layout",
            "ok": not layout_errors,
            "summary": "ok" if not layout_errors else f"{len(layout_errors)} issue(s)",
        }
    )

    instruction_errors = repo_health.instruction_quality_errors(root)
    errors.extend(instruction_errors)
    checks.append(
        {
            "name": "AGENTS.md compactness",
            "ok": not instruction_errors,
            "summary": "ok" if not instruction_errors else f"{len(instruction_errors)} issue(s)",
        }
    )

    for name, callback in [
        ("project instruction adapters", lambda: repo_generated.sync_instructions(root, check=True)),
        ("Claude skill adapters", lambda: repo_generated.sync_claude_skills(root, check=True)),
    ]:
        ok, message = capture_check(name, callback)
        checks.append({"name": name, "ok": ok, "summary": message})
        if not ok:
            errors.append(f"{name} are missing or stale: {message}")

    claude_portability_errors = claude_adapter_portability_errors(root)
    errors.extend(claude_portability_errors)
    checks.append(
        {
            "name": "Claude adapter portability",
            "ok": not claude_portability_errors,
            "summary": (
                "ok"
                if not claude_portability_errors
                else f"{len(claude_portability_errors)} issue(s)"
            ),
        }
    )

    surfaces = adapter_surfaces(root)
    navigation_strategy = adapter_navigation_strategy_report(root, surfaces)
    errors.extend(str(item) for item in navigation_strategy.get("issues", []) if str(item))
    checks.append(
        {
            "name": "adapter navigation strategy",
            "ok": bool(navigation_strategy.get("ok")),
            "summary": (
                "ok"
                if navigation_strategy.get("ok")
                else f"{len(navigation_strategy.get('issues', []))} issue(s)"
            ),
        }
    )
    adapter_guardrails = adapter_context_guardrail_report(root, surfaces)
    for finding in adapter_guardrails.get("findings", []):
        if isinstance(finding, dict):
            errors.append(
                f"{finding.get('path')}:{finding.get('line')}: {finding.get('issue')}"
            )
    checks.append(
        {
            "name": "adapter context guardrails",
            "ok": bool(adapter_guardrails.get("ok")),
            "summary": (
                "ok"
                if adapter_guardrails.get("ok")
                else f"{adapter_guardrails.get('finding_count', 0)} issue(s)"
            ),
        }
    )
    missing_surfaces = [surface["surface"] for surface in surfaces if not surface.get("exists")]
    if missing_surfaces:
        errors.append(f"adapter surfaces are missing: {', '.join(missing_surfaces)}")
    checks.append(
        {
            "name": "adapter surfaces",
            "ok": not missing_surfaces,
            "summary": "ok" if not missing_surfaces else f"missing: {', '.join(missing_surfaces)}",
        }
    )

    forbidden_settings = forbidden_tool_setting_paths(root)
    for path in forbidden_settings:
        errors.append(f"committed tool setting is not allowed: {repo.relative(root, path)}")
    checks.append(
        {
            "name": "forbidden tool settings",
            "ok": not forbidden_settings,
            "summary": "none" if not forbidden_settings else f"{len(forbidden_settings)} found",
        }
    )

    github_agents = root / ("." + "github") / "agents"
    if github_agents.exists():
        warnings.append(
            f"{repo.relative(root, github_agents)} is not generated yet; keep Copilot agents out of canonical surface for now."
        )
    checks.append(
        {
            "name": "copilot custom agents",
            "ok": True,
            "summary": "not present" if not github_agents.exists() else "present as warning",
        }
    )

    installed_hosts = None
    if include_installed_hosts:
        installed_hosts = installed_host_validation_report(root)
        errors.extend(str(issue) for issue in installed_hosts.get("issues", []) if str(issue))
        checks.append(
            {
                "name": "installed host surfaces",
                "ok": bool(installed_hosts.get("ok")),
                "summary": (
                    f"{installed_hosts.get('installed_count', 0)} installed, "
                    f"{installed_hosts.get('failed_count', 0)} failed, "
                    f"{installed_hosts.get('not_installed_count', 0)} not installed"
                ),
            }
        )

    warnings, escalated = repo_policy.classify_warnings(root, warnings)
    errors.extend(escalated)
    report = {
        "schema_version": 1,
        "tool": "validate-agent-compatibility",
        "ok": not errors,
        "status": "passed" if not errors else "failed",
        "root": str(root),
        "adapter_surfaces": surfaces,
        "navigation_strategy": navigation_strategy,
        "adapter_context_guardrails": adapter_guardrails,
        "checks": checks,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
    }
    if installed_hosts is not None:
        report["installed_host_validation"] = installed_hosts
    return report


def summarize_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    surfaces = report.get("adapter_surfaces") if isinstance(report.get("adapter_surfaces"), list) else []
    failed_checks = [check for check in checks if isinstance(check, dict) and check.get("ok") is not True]
    missing_surfaces = [
        surface
        for surface in surfaces
        if isinstance(surface, dict) and surface.get("exists") is not True
    ]
    generated_surfaces = [surface for surface in surfaces if isinstance(surface, dict) and surface.get("generated")]
    canonical_surfaces = [surface for surface in surfaces if isinstance(surface, dict) and not surface.get("generated")]
    summary: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "validate-agent-compatibility"),
        "ok": report.get("ok", False),
        "status": report.get("status", "unknown"),
        "root": report.get("root", ""),
        "check_count": len(checks),
        "failed_check_count": len(failed_checks),
        "surface_count": len(surfaces),
        "generated_surface_count": len(generated_surfaces),
        "canonical_surface_count": len(canonical_surfaces),
        "supported_surfaces": [str(surface.get("surface", "")) for surface in surfaces if isinstance(surface, dict)],
        "missing_surface_count": len(missing_surfaces),
        "navigation_strategy": report.get("navigation_strategy", {}),
        "adapter_context_guardrails": report.get("adapter_context_guardrails", {}),
        "error_count": len(report.get("errors", [])) if isinstance(report.get("errors"), list) else 0,
        "warning_count": len(report.get("warnings", [])) if isinstance(report.get("warnings"), list) else 0,
        "errors": report.get("errors", []),
        "warnings": report.get("warnings", []),
    }
    if compact:
        if failed_checks:
            summary["failed_checks"] = failed_checks
        if missing_surfaces:
            summary["missing_surfaces"] = missing_surfaces
        summary.pop("root", None)
        if not summary.get("errors"):
            summary.pop("errors", None)
        if not summary.get("warnings"):
            summary.pop("warnings", None)
        if report.get("installed_host_validation"):
            summary["installed_host_validation"] = report["installed_host_validation"]
        return summary
    summary["checks"] = checks
    summary["adapter_surfaces"] = surfaces
    if report.get("installed_host_validation"):
        summary["installed_host_validation"] = report["installed_host_validation"]
    return summary


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Agent Compatibility Validation",
        "",
        f"- Root: `{report['root']}`",
        f"- Status: {report['status']}",
        "",
        "## Checks",
        "",
    ]
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    for check in checks:
        status = "ok" if check["ok"] else "failed"
        lines.append(f"- {check['name']}: {status} - {check['summary']}")
    if not checks and "check_count" in report:
        lines.append(
            f"- Checks: {report.get('check_count', 0)} total, "
            f"{report.get('failed_check_count', 0)} failed"
        )
    if report.get("adapter_surfaces"):
        lines.extend(["", "## Adapter Surfaces", ""])
        for surface in report["adapter_surfaces"]:
            status = "present" if surface.get("exists") else "missing"
            generated = "generated" if surface.get("generated") else "canonical"
            lines.append(
                f"- {surface.get('surface')}: {status}, {generated}; source `{surface.get('canonical_source')}`; "
                f"navigation {surface.get('navigation_strategy')}"
            )
    if report.get("navigation_strategy"):
        strategy = report["navigation_strategy"]
        lines.extend(["", "## Navigation Strategy", ""])
        lines.append(f"- First orientation file: `{strategy.get('first_orientation_file', '')}`")
        lines.append(f"- Raw navigation JSON: {strategy.get('raw_navigation_json', '')}")
        lines.append(f"- Strategies: {strategy.get('strategy_counts', {})}")
    if report.get("adapter_context_guardrails"):
        guardrails = report["adapter_context_guardrails"]
        lines.extend(["", "## Adapter Context Guardrails", ""])
        lines.append(f"- Status: {guardrails.get('status', '')}")
        lines.append(f"- Findings: {guardrails.get('finding_count', 0)}")
    if report.get("installed_host_validation"):
        installed = report["installed_host_validation"]
        lines.extend(["", "## Installed Host Surfaces", ""])
        lines.append(f"- Status: {installed.get('status', '')}")
        lines.append(f"- Installed: {installed.get('installed_count', 0)}/{installed.get('host_count', 0)}")
        lines.append(f"- Failed: {installed.get('failed_count', 0)}")
        lines.append("- Model calls: disabled")
        for host in installed.get("hosts", []):
            if isinstance(host, dict):
                lines.append(
                    f"- {host.get('host_surface')}: {host.get('status')}"
                    + (f" - {host.get('version')}" if host.get("version") else "")
                )
    if report.get("errors"):
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {item}" for item in report["errors"])
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in report["warnings"])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="repository root; defaults to script parent")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    parser.add_argument("--summary", action="store_true", help="emit aggregate counts and failures only")
    parser.add_argument("--compact", action="store_true", help="with --summary, omit passing check rows")
    parser.add_argument(
        "--installed-hosts",
        action="store_true",
        help="run bounded local version/help and Copilot skill-discovery probes; never invoke a model",
    )
    return parser


def default_root() -> Path:
    return Path(__file__).resolve().parents[4]


def main() -> int:
    common.require_supported_python()
    args = build_parser().parse_args()
    root = Path(args.root).expanduser().resolve() if args.root else default_root()
    report = build_report(root, include_installed_hosts=args.installed_hosts)
    if args.summary or args.compact:
        report = summarize_report(report, compact=args.compact)
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
