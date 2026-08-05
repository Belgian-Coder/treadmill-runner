#!/usr/bin/env python3
"""Read-only .NET project context inspection orchestration."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cli_probes import (
    Runner,
    assert_safe_dotnet_command,
    cli_probe_report,
    default_runner,
    parse_dotnet_version,
    parse_key_value_lines,
    parse_repeated_key_value_lines,
    resolve_dotnet,
    run_safe_dotnet,
)
from .common import PROJECT_SUFFIXES, SOLUTION_SUFFIXES, iter_files, read_json, rel
from .context_sections import (
    ci_report,
    collect_package_names,
    configuration_report,
    diff_reports,
    features_report,
    persistence_report,
    project_graph_report,
    restore_prerequisites_report,
)
from .nuget import nuget_report, parse_nuget_config, redact_source_value
from .project_files import (
    build_policy_report,
    parse_dotnet_tools,
    parse_global_json,
    parse_project,
    parse_solution,
)

sys.dont_write_bytecode = True

SCHEMA_VERSION = 1
TOOL_ID = "dotnet-project-context"


def normalize_filter_paths(root: Path, values: list[str] | None) -> list[str]:
    paths: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if not text:
            continue
        candidate = Path(text)
        if candidate.is_absolute():
            normalized = rel(root, candidate.resolve(strict=False))
        else:
            normalized = rel(root, (root / candidate).resolve(strict=False))
        paths.append(normalized.replace("\\", "/"))
    return sorted(dict.fromkeys(paths))


def solution_project_paths(root: Path, solutions: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for solution in solutions:
        solution_path = root / str(solution.get("path", ""))
        members = solution.get("projects")
        if not isinstance(members, list):
            continue
        for member in members:
            candidate = (solution_path.parent / str(member)).resolve(strict=False)
            paths.add(rel(root, candidate).replace("\\", "/"))
    return paths


def filter_dotnet_inventory(
    root: Path,
    solution_files: list[Path],
    project_files: list[Path],
    *,
    solution_filters: list[str] | None,
    project_filters: list[str] | None,
) -> tuple[list[Path], list[Path], dict[str, Any]]:
    requested_solutions = normalize_filter_paths(root, solution_filters)
    requested_projects = normalize_filter_paths(root, project_filters)
    solution_by_rel = {rel(root, path).replace("\\", "/"): path for path in solution_files}
    project_by_rel = {rel(root, path).replace("\\", "/"): path for path in project_files}

    selected_solution_files = [solution_by_rel[item] for item in requested_solutions if item in solution_by_rel] if requested_solutions else solution_files
    selected_solutions = [parse_solution(root, path) for path in selected_solution_files]

    allowed_project_paths: set[str] | None = None
    if requested_solutions:
        solution_paths = solution_project_paths(root, selected_solutions)
        allowed_project_paths = solution_paths or None
    if requested_projects:
        requested_project_paths = {item for item in requested_projects if item in project_by_rel}
        allowed_project_paths = requested_project_paths if allowed_project_paths is None else allowed_project_paths.intersection(requested_project_paths)

    if allowed_project_paths is None:
        selected_project_files = project_files
    else:
        selected_project_files = [path for item, path in project_by_rel.items() if item in allowed_project_paths]

    filters = {
        "requested_solutions": requested_solutions,
        "requested_projects": requested_projects,
        "matched_solutions": [rel(root, path).replace("\\", "/") for path in selected_solution_files],
        "matched_projects": [rel(root, path).replace("\\", "/") for path in selected_project_files],
        "unmatched_solutions": [item for item in requested_solutions if item not in solution_by_rel],
        "unmatched_projects": [item for item in requested_projects if item not in project_by_rel],
        "active": bool(requested_solutions or requested_projects),
    }
    return selected_solution_files, selected_project_files, filters

def resolve_output_dir(root: Path, output_dir: Path | None) -> Path:
    if output_dir is None:
        candidate = root / "docs" / "project" / "dotnet-context"
    elif output_dir.is_absolute():
        candidate = output_dir
    else:
        candidate = root / output_dir
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(f"output directory is outside target: {resolved}") from exc
    return resolved

def write_evidence(report: dict[str, Any], target: Path, output_dir: Path | None = None) -> dict[str, Any]:
    root = target.expanduser().resolve(strict=False)
    directory = resolve_output_dir(root, output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    evidence = {
        "written": [
            rel(root, directory / "dotnet-context.json"),
            rel(root, directory / "dotnet-context.md"),
        ],
        "paths": {
            "json": rel(root, directory / "dotnet-context.json"),
            "markdown": rel(root, directory / "dotnet-context.md"),
        },
    }
    payload = dict(report)
    payload["evidence"] = evidence
    (directory / "dotnet-context.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    (directory / "dotnet-context.md").write_text(render_markdown(payload), encoding="utf-8", newline="\n")
    return evidence

def validation_candidates(solutions: list[dict[str, Any]], projects: list[dict[str, Any]], nuget: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    target = str(solutions[0].get("path")) if solutions else str(projects[0].get("path")) if projects else ""
    if not target:
        return candidates
    private_feeds = bool(nuget.get("private_feeds_detected"))
    candidates.append(
        {
            "id": "dotnet-restore",
            "kind": "restore",
            "command": f"dotnet restore {target}",
            "required": True,
            "runs_by_default": False,
            "requires_explicit_approval": True,
            "may_contact_feeds": True,
            "private_feed_prerequisites": private_feeds,
        }
    )
    candidates.append(
        {
            "id": "dotnet-build-no-restore",
            "kind": "build",
            "command": f"dotnet build {target} --no-restore",
            "required": True,
            "runs_by_default": False,
            "requires_explicit_approval": True,
            "may_contact_feeds": False,
            "prerequisite": "successful restore with project-approved feed credentials",
        }
    )
    if any(project.get("classification") == "test" for project in projects):
        candidates.append(
            {
                "id": "dotnet-test-no-restore",
                "kind": "test",
                "command": f"dotnet test {target} --no-restore",
                "required": True,
                "runs_by_default": False,
                "requires_explicit_approval": True,
                "may_contact_feeds": False,
                "prerequisite": "successful restore/build with project-approved feed credentials",
            }
        )
    return candidates

def context_facts(
    projects: list[dict[str, Any]],
    nuget: dict[str, Any],
    global_json: dict[str, Any],
    candidates: list[dict[str, Any]],
    ci: dict[str, Any],
    configuration: dict[str, Any],
    persistence: dict[str, Any],
) -> list[dict[str, Any]]:
    frameworks = sorted({framework for project in projects for framework in project.get("target_frameworks", [])})
    config_paths = [
        str(item.get("path", ""))
        for item in configuration.get("appsettings_files", [])
        if isinstance(item, dict) and item.get("path")
    ]
    persistence_paths = [
        str(item.get("path", ""))
        for item in persistence.get("db_contexts", [])
        if isinstance(item, dict) and item.get("path")
    ]
    facts = [
        {
            "id": "stack-runtime",
            "status": "present" if projects or global_json.get("sdk_version") else "missing",
            "value": {
                "sdk_version": global_json.get("sdk_version", ""),
                "target_frameworks": frameworks,
                "project_count": len(projects),
            },
            "evidence_paths": [item["path"] for item in projects[:5]],
        },
        {
            "id": "validation-commands",
            "status": "present" if candidates else "missing",
            "value": {"candidate_count": len(candidates), "restore_required": any(item.get("kind") == "restore" for item in candidates)},
            "evidence_paths": [str(item.get("command", "")) for item in candidates[:5]],
        },
        {
            "id": "generated-boundaries",
            "status": "present",
            "value": {"generated_dirs": ["bin/", "obj/", "TestResults/"]},
            "evidence_paths": [".gitignore"],
        },
        {
            "id": "external-systems",
            "status": "review-needed" if nuget.get("private_feeds_detected") or config_paths else "present",
            "value": {"nuget_private_feeds": bool(nuget.get("private_feeds_detected")), "configuration_files": len(config_paths)},
            "evidence_paths": [*nuget.get("config_paths", []), *config_paths],
        },
        {
            "id": "secrets-config",
            "status": "review-needed" if nuget.get("credential_sections_present") or nuget.get("private_feeds_detected") or config_paths else "present",
            "value": {
                "credential_sections_present": nuget.get("credential_sections_present", []),
                "appsettings_files": config_paths,
                "user_secrets_ids": configuration.get("user_secrets_ids", []),
                "user_global_config_skipped": True,
            },
            "evidence_paths": [*nuget.get("config_paths", []), *config_paths],
        },
        {
            "id": "persistence",
            "status": "present" if persistence_paths or persistence.get("provider_packages") or persistence.get("migration_paths") else "missing",
            "value": {
                "db_context_count": len(persistence.get("db_contexts", [])),
                "provider_packages": persistence.get("provider_packages", []),
                "migration_path_count": len(persistence.get("migration_paths", [])),
            },
            "evidence_paths": [*persistence_paths, *persistence.get("migration_paths", [])],
        },
        {
            "id": "ci",
            "status": "present" if ci.get("workflow_paths") else "missing",
            "value": {
                "workflow_file_count": ci.get("workflow_file_count", 0),
                "dotnet_command_count": len(ci.get("dotnet_commands", [])),
            },
            "evidence_paths": ci.get("workflow_paths", []),
        },
    ]
    return facts

def build_report(
    target: Path,
    *,
    probe_cli: bool = True,
    dotnet_executable: str | None = None,
    runner: Runner | None = None,
    baseline_report: dict[str, Any] | None = None,
    solution_filters: list[str] | None = None,
    project_filters: list[str] | None = None,
) -> dict[str, Any]:
    root = target.expanduser().resolve(strict=False)
    if root.exists() and not root.is_dir():
        return {
            "schema_version": SCHEMA_VERSION,
            "tool": TOOL_ID,
            "ok": False,
            "status": "blocked",
            "target": str(root),
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "dotnet_cli": {"available": False, "path": "", "version": "", "probes_run": [], "probes_failed": []},
            "solutions": [],
            "projects": [],
            "filters": {
                "requested_solutions": normalize_filter_paths(root, solution_filters),
                "requested_projects": normalize_filter_paths(root, project_filters),
                "matched_solutions": [],
                "matched_projects": [],
                "unmatched_solutions": normalize_filter_paths(root, solution_filters),
                "unmatched_projects": normalize_filter_paths(root, project_filters),
                "active": bool(solution_filters or project_filters),
            },
            "nuget": {},
            "global_json": {},
            "dotnet_tools": [],
            "build_policy": {},
            "ci": {},
            "project_graph": {},
            "restore_prerequisites": {},
            "configuration": {},
            "persistence": {},
            "features": {},
            "validation_candidates": [],
            "context_facts": [],
            "advisories": [{"id": "invalid-target", "message": f"Target exists and is not a directory: {root}"}],
            "skipped": [],
        }
    files = iter_files(root)
    solution_files = sorted((path for path in files if path.suffix.lower() in SOLUTION_SUFFIXES), key=lambda item: rel(root, item))
    project_files = sorted((path for path in files if path.suffix.lower() in PROJECT_SUFFIXES), key=lambda item: rel(root, item))
    solution_files, project_files, filters = filter_dotnet_inventory(
        root,
        solution_files,
        project_files,
        solution_filters=solution_filters,
        project_filters=project_filters,
    )
    solutions = [parse_solution(root, path) for path in solution_files]
    projects = [parse_project(root, path) for path in project_files]
    nuget = nuget_report(root, files)
    build_policy = build_policy_report(root, files)
    ci = ci_report(root, files)
    global_json = parse_global_json(root)
    tools = parse_dotnet_tools(root)
    skipped: list[dict[str, str]] = [
        {"id": "nuget-user-global-config", "reason": "user-level and machine-level NuGet config files were intentionally not inspected"},
        {"id": "dotnet-restore-build-test", "reason": "restore, build, test, package search, package install, and tool install commands are never run by this report"},
    ]
    advisories: list[dict[str, str]] = []
    dotnet_cli = {"available": False, "path": "", "version": "", "info": "", "probes_run": [], "probes_failed": []}
    if probe_cli:
        dotnet_path = resolve_dotnet(dotnet_executable)
        dotnet_cli, cli_skipped, cli_advisories = cli_probe_report(root, dotnet_path=dotnet_path, solutions=solutions, projects=projects, runner=runner or default_runner)
        skipped.extend(cli_skipped)
        advisories.extend(cli_advisories)
    else:
        skipped.append({"id": "dotnet-cli-probes", "reason": "CLI probes disabled by caller; static project facts only"})
    project_graph = project_graph_report(root, solutions, projects)
    packages = collect_package_names(projects, nuget)
    restore_prerequisites = restore_prerequisites_report(root, files, nuget, build_policy)
    configuration = configuration_report(root, files, projects)
    persistence = persistence_report(root, files, projects, packages)
    features = features_report(projects, packages, persistence)
    candidates = validation_candidates(solutions, projects, nuget)
    if nuget.get("private_feeds_detected"):
        advisories.append(
            {
                "id": "private-nuget-feeds",
                "message": "Private/internal NuGet feeds were detected. Restore requires project-approved feed credentials and should be run only after explicit approval.",
            }
        )
    if global_json.get("sdk_version"):
        advisories.append({"id": "global-json-sdk", "message": f"global.json pins .NET SDK {global_json.get('sdk_version')}."})
    for item in filters.get("unmatched_solutions", []):
        advisories.append({"id": "dotnet-solution-filter-unmatched", "message": f"Requested solution filter did not match an active solution file: {item}"})
    for item in filters.get("unmatched_projects", []):
        advisories.append({"id": "dotnet-project-filter-unmatched", "message": f"Requested project filter did not match an active project file: {item}"})
    facts = context_facts(projects, nuget, global_json, candidates, ci, configuration, persistence)
    if not solutions and not projects and not global_json.get("sdk_version"):
        status = "not-dotnet"
    elif not probe_cli or not dotnet_cli.get("available") or dotnet_cli.get("probes_failed"):
        status = "partial"
    else:
        status = "ready"
    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_ID,
        "ok": True,
        "status": status,
        "target": str(root),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "dotnet_cli": dotnet_cli,
        "solutions": solutions,
        "projects": projects,
        "filters": filters,
        "nuget": nuget,
        "global_json": global_json,
        "dotnet_tools": tools,
        "build_policy": build_policy,
        "ci": ci,
        "project_graph": project_graph,
        "restore_prerequisites": restore_prerequisites,
        "configuration": configuration,
        "persistence": persistence,
        "features": features,
        "validation_candidates": candidates,
        "context_facts": facts,
        "advisories": advisories,
        "skipped": skipped,
        "summary": {
            "solution_count": len(solutions),
            "project_count": len(projects),
            "private_feeds_detected": bool(nuget.get("private_feeds_detected")),
            "validation_candidate_count": len(candidates),
            "ci_workflow_count": len(ci.get("workflow_paths", [])),
            "feature_count": len(features.get("signals", [])),
        },
    }
    if baseline_report:
        report["diff"] = diff_reports(baseline_report, report)
    return report

def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# .NET Project Context",
        "",
        f"- Status: {report.get('status')}",
        f"- Target: `{report.get('target')}`",
        "- Safety: No restore/build/test/package commands were run.",
    ]
    dotnet_cli = report.get("dotnet_cli") if isinstance(report.get("dotnet_cli"), dict) else {}
    lines.append(f"- dotnet CLI: {'available' if dotnet_cli.get('available') else 'missing or not probed'}")
    if dotnet_cli.get("version"):
        lines.append(f"- SDK version: `{dotnet_cli.get('version')}`")

    solutions = report.get("solutions") if isinstance(report.get("solutions"), list) else []
    if solutions:
        lines.extend(["", "## Solutions", ""])
        for item in solutions:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('path')}` ({len(item.get('projects', []) if isinstance(item.get('projects'), list) else [])} static project references)")

    projects = report.get("projects") if isinstance(report.get("projects"), list) else []
    lines.extend(["", "## Projects", ""])
    if projects:
        for project in projects:
            if not isinstance(project, dict):
                continue
            frameworks = ", ".join(str(item) for item in project.get("target_frameworks", [])) or "not declared"
            packages = ", ".join(str(item) for item in project.get("package_references", [])[:5]) or "none declared"
            lines.append(f"- `{project.get('path')}`: {project.get('classification')} targets {frameworks}; packages: {packages}")
    else:
        lines.append("- No active .NET project files detected.")

    nuget = report.get("nuget") if isinstance(report.get("nuget"), dict) else {}
    lines.extend(["", "## NuGet/feed policy", ""])
    lines.append(f"- Repo-local NuGet config: {', '.join(f'`{item}`' for item in nuget.get('config_paths', []) if isinstance(item, str)) or 'none detected'}")
    lines.append(f"- Private/internal NuGet feeds detected: {str(bool(nuget.get('private_feeds_detected'))).lower()}")
    lines.append(f"- Package source mapping: {str(bool(nuget.get('package_source_mapping_present'))).lower()}")
    lines.append("- User/global NuGet config was intentionally skipped, and credential values are not emitted.")

    build_policy = report.get("build_policy") if isinstance(report.get("build_policy"), dict) else {}
    lines.extend(["", "## Build policy", ""])
    files = build_policy.get("files") if isinstance(build_policy.get("files"), list) else []
    if files:
        lines.append("- Policy files: " + ", ".join(f"`{item.get('path')}`" for item in files if isinstance(item, dict)))
    else:
        lines.append("- Policy files: none detected.")
    properties = build_policy.get("properties") if isinstance(build_policy.get("properties"), dict) else {}
    if properties:
        lines.append("- Key properties: " + ", ".join(f"`{key}={value}`" for key, value in sorted(properties.items())[:12]))

    ci = report.get("ci") if isinstance(report.get("ci"), dict) else {}
    lines.extend(["", "## CI signals", ""])
    workflow_paths = ci.get("workflow_paths") if isinstance(ci.get("workflow_paths"), list) else []
    if workflow_paths:
        lines.append("- Workflow files: " + ", ".join(f"`{item}`" for item in workflow_paths[:12]))
    else:
        lines.append("- Workflow files: none detected.")
    commands = ci.get("dotnet_commands") if isinstance(ci.get("dotnet_commands"), list) else []
    if commands:
        lines.append("- Dotnet command candidates:")
        for item in commands[:8]:
            if isinstance(item, dict):
                lines.append(f"  - `{item.get('command')}` ({item.get('path')}:{item.get('line')})")

    configuration = report.get("configuration") if isinstance(report.get("configuration"), dict) else {}
    lines.extend(["", "## Configuration inventory", ""])
    appsettings = configuration.get("appsettings_files") if isinstance(configuration.get("appsettings_files"), list) else []
    if appsettings:
        for item in appsettings[:8]:
            if isinstance(item, dict):
                names = ", ".join(f"`{name}`" for name in item.get("connection_string_names", [])) or "none"
                lines.append(f"- `{item.get('path')}` connection string names: {names}")
    else:
        lines.append("- Appsettings files: none detected.")
    user_secrets = configuration.get("user_secrets_ids") if isinstance(configuration.get("user_secrets_ids"), list) else []
    if user_secrets:
        lines.append("- UserSecretsId entries: " + ", ".join(f"`{item.get('project')}`" for item in user_secrets if isinstance(item, dict)))

    persistence = report.get("persistence") if isinstance(report.get("persistence"), dict) else {}
    lines.extend(["", "## Persistence signals", ""])
    db_contexts = persistence.get("db_contexts") if isinstance(persistence.get("db_contexts"), list) else []
    if db_contexts:
        for item in db_contexts[:8]:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('path')}` DbContext classes: {', '.join(str(name) for name in item.get('class_names', []))}")
    else:
        lines.append("- DbContext classes: none detected.")
    if persistence.get("provider_packages"):
        lines.append("- Provider packages: " + ", ".join(f"`{item}`" for item in persistence.get("provider_packages", [])[:12]))

    features = report.get("features") if isinstance(report.get("features"), dict) else {}
    signals = features.get("signals") if isinstance(features.get("signals"), list) else []
    if signals:
        lines.extend(["", "## Feature signals", ""])
        lines.append("- " + ", ".join(f"`{item.get('id')}`" for item in signals if isinstance(item, dict)))

    candidates = report.get("validation_candidates") if isinstance(report.get("validation_candidates"), list) else []
    lines.extend(["", "## Validation candidates", ""])
    if candidates:
        for candidate in candidates:
            if isinstance(candidate, dict):
                lines.append(f"- `{candidate.get('command')}` ({candidate.get('kind')}; runs by default: {str(bool(candidate.get('runs_by_default'))).lower()})")
    else:
        lines.append("- None detected.")

    advisories = report.get("advisories") if isinstance(report.get("advisories"), list) else []
    if advisories:
        lines.extend(["", "## Advisories", ""])
        for item in advisories:
            if isinstance(item, dict):
                lines.append(f"- {item.get('message')}")

    skipped = report.get("skipped") if isinstance(report.get("skipped"), list) else []
    if skipped:
        lines.extend(["", "## Skipped", ""])
        for item in skipped:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('id')}`: {item.get('reason')}")
    return "\n".join(lines).rstrip() + "\n"

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="read-only inspection for .NET project context; does not run restore/build/test/package-list/package-search/tool-install",
    )
    parser.add_argument("--target", required=True, help="project root to inspect")
    parser.add_argument("--solution", action="append", default=[], help="solution path to include; may be repeated and narrows project inventory to selected solution members")
    parser.add_argument("--project", action="append", default=[], help="project path to include; may be repeated and further narrows project inventory")
    parser.add_argument("--no-cli-probes", action="store_true", help="skip safe installed dotnet CLI probes and report static facts only")
    parser.add_argument("--dotnet-executable", help="explicit dotnet executable path for safe CLI probes; defaults to dotnet on PATH")
    parser.add_argument("--baseline", help="optional previous dotnet-context JSON report to compare for context drift")
    parser.add_argument("--write-evidence", action="store_true", help="write docs/project/dotnet-context evidence artifacts under the target")
    parser.add_argument("--evidence-dir", default="docs/project/dotnet-context", help="target-local evidence directory for --write-evidence")
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        dest="output_format",
        help="output format; default: markdown",
    )
    return parser

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    baseline = read_json(Path(args.baseline)) if args.baseline else None
    report = build_report(
        Path(args.target),
        probe_cli=not args.no_cli_probes,
        dotnet_executable=args.dotnet_executable,
        baseline_report=baseline or None,
        solution_filters=args.solution,
        project_filters=args.project,
    )
    if args.write_evidence:
        report["evidence"] = write_evidence(report, Path(args.target), Path(args.evidence_dir))
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report.get("ok", False) else 1

if __name__ == "__main__":
    raise SystemExit(main())
