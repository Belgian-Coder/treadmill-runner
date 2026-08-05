"""Derived .NET context sections for CI, graph, config, persistence, and features."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .common import read_json, read_text, rel

PERSISTENCE_PACKAGE_MARKERS = (
    "entityframeworkcore",
    "dapper",
    "mongodb.driver",
    "npgsql",
    "mysqlconnector",
    "system.data.sqlclient",
    "microsoft.data.sqlclient",
)
FEATURE_PACKAGE_MARKERS = {
    "aspnet-core": ("microsoft.aspnetcore",),
    "openapi": ("openapi", "swashbuckle", "nswag"),
    "ef-core": ("entityframeworkcore",),
    "opentelemetry": ("opentelemetry",),
    "grpc": ("grpc.aspnetcore", "grpc.net"),
    "signalr": ("signalr",),
    "blazor": ("blazor", "components.webassembly", "components.server"),
    "aspire": ("aspire.", "aspirehosting"),
    "health-checks": ("healthchecks", "diagnostics.healthchecks"),
    "message-bus": ("masstransit", "servicebus", "rabbitmq", "kafka"),
}

def is_ci_file(root: Path, path: Path) -> bool:
    relative = rel(root, path).lower()
    name = path.name.lower()
    if path.suffix.lower() not in {".yml", ".yaml"}:
        return False
    return (
        relative.startswith(".github/workflows/")
        or relative.startswith(".azuredevops/")
        or relative.startswith("eng/pipelines/")
        or name.startswith("azure-pipelines")
        or name.startswith("pipeline")
    )

def extract_ci_dotnet_command(line: str) -> str:
    value = line.strip()
    lowered = value.lower()
    for prefix in ("- run:", "run:", "- script:", "script:"):
        if lowered.startswith(prefix):
            value = value.split(":", 1)[1].strip()
            break
    value = value.strip().strip("'\"")
    lowered = value.lower()
    if "dotnet " not in lowered:
        return ""
    start = lowered.index("dotnet ")
    command = value[start:].strip()
    return re.split(r"\s+#", command, maxsplit=1)[0].strip().strip("'\"")

def ci_report(root: Path, files: list[Path]) -> dict[str, Any]:
    workflow_files = sorted((path for path in files if is_ci_file(root, path)), key=lambda item: rel(root, item))
    commands: list[dict[str, Any]] = []
    task_signals: list[dict[str, Any]] = []
    for path in workflow_files[:40]:
        for line_number, line in enumerate(read_text(path).splitlines(), start=1):
            command = extract_ci_dotnet_command(line)
            if command:
                commands.append({"path": rel(root, path), "line": line_number, "command": command})
            lowered = line.lower()
            if "dotnetcorecli@" in lowered:
                task_signals.append({"path": rel(root, path), "line": line_number, "task": "DotNetCoreCLI"})
            elif re.search(r"\bcommand:\s*(restore|build|test|publish|pack)\b", lowered):
                task_signals.append({"path": rel(root, path), "line": line_number, "task": line.strip()})
    return {
        "workflow_paths": [rel(root, path) for path in workflow_files],
        "dotnet_commands": commands[:80],
        "task_signals": task_signals[:80],
        "workflow_file_count": len(workflow_files),
        "skipped_after_limit": max(0, len(workflow_files) - 40),
        "policy": "CI files were scanned as text for dotnet command candidates; no CI command was executed.",
    }

def normalize_project_reference(root: Path, project_path: str, include: str, known_paths: set[str]) -> str:
    normalized = include.replace("\\", "/").strip()
    if not normalized:
        return ""
    if normalized in known_paths:
        return normalized
    source = root / project_path
    candidate = (source.parent / include).resolve(strict=False)
    return rel(root, candidate).replace("\\", "/")

def project_graph_report(root: Path, solutions: list[dict[str, Any]], projects: list[dict[str, Any]]) -> dict[str, Any]:
    known_paths = {str(project.get("path")) for project in projects if project.get("path")}
    nodes = [
        {
            "path": str(project.get("path")),
            "classification": str(project.get("classification", "")),
            "target_frameworks": project.get("target_frameworks", []),
        }
        for project in projects
        if project.get("path")
    ]
    edges: list[dict[str, Any]] = []
    for project in projects:
        source = str(project.get("path", ""))
        if not source:
            continue
        references = [str(item) for item in project.get("project_references", []) if item]
        references.extend(str(item) for item in project.get("evaluated_project_references", []) if item)
        for include in sorted(dict.fromkeys(references)):
            target = normalize_project_reference(root, source, include, known_paths)
            if target:
                edges.append({"from": source, "to": target, "type": "project-reference", "resolved": target in known_paths})
    solution_members: list[dict[str, Any]] = []
    for solution in solutions:
        members = solution.get("listed_projects") if isinstance(solution.get("listed_projects"), list) else solution.get("projects")
        if not isinstance(members, list):
            members = []
        solution_members.append({"solution": solution.get("path", ""), "projects": [str(item).replace("\\", "/") for item in members]})
    return {
        "nodes": nodes,
        "edges": edges[:200],
        "solution_members": solution_members,
        "test_project_edges": [edge for edge in edges if any(node.get("path") == edge["from"] and node.get("classification") == "test" for node in nodes)],
    }

def collect_package_names(projects: list[dict[str, Any]], nuget: dict[str, Any]) -> dict[str, list[str]]:
    evidence: dict[str, list[str]] = {}
    for project in projects:
        project_path = str(project.get("path", ""))
        for key in ("package_references", "evaluated_package_references"):
            values = project.get(key)
            if not isinstance(values, list):
                continue
            for package in values:
                name = str(package)
                if name:
                    evidence.setdefault(name, []).append(project_path)
    directory_packages = nuget.get("directory_packages") if isinstance(nuget.get("directory_packages"), list) else []
    for directory in directory_packages:
        if not isinstance(directory, dict):
            continue
        path = str(directory.get("path", ""))
        versions = directory.get("package_versions")
        if not isinstance(versions, list):
            continue
        for item in versions:
            if isinstance(item, dict) and item.get("include"):
                evidence.setdefault(str(item.get("include")), []).append(path)
    return {name: sorted(dict.fromkeys(paths)) for name, paths in evidence.items()}

def restore_prerequisites_report(root: Path, files: list[Path], nuget: dict[str, Any], build_policy: dict[str, Any]) -> dict[str, Any]:
    lock_files = sorted(rel(root, path) for path in files if path.name == "packages.lock.json")
    questions: list[str] = []
    if nuget.get("private_feeds_detected"):
        questions.append("Which project-approved credentials and package source mapping rules are required before restore?")
    if nuget.get("package_source_mapping_present"):
        questions.append("Which packages are expected from each mapped source, and who owns mapping updates?")
    if nuget.get("central_package_management"):
        questions.append("Who owns Directory.Packages.props changes and version review?")
    if build_policy.get("restore_lock_policy", {}).get("restore_packages_with_lock_file") and not lock_files:
        questions.append("Restore lock files are enabled, but no packages.lock.json files were detected in active paths.")
    return {
        "private_feeds_detected": bool(nuget.get("private_feeds_detected")),
        "package_source_mapping_present": bool(nuget.get("package_source_mapping_present")),
        "central_package_management": bool(nuget.get("central_package_management")),
        "lock_files": lock_files[:80],
        "global_config_skipped": bool(nuget.get("global_config_skipped")),
        "user_config_skipped": bool(nuget.get("user_config_skipped")),
        "questions": questions,
    }

def json_key_paths(value: Any, prefix: str = "", *, limit: int = 80) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_prefix = f"{prefix}:{key_text}" if prefix else key_text
            paths.append(child_prefix)
            if len(paths) >= limit:
                return paths[:limit]
            paths.extend(json_key_paths(child, child_prefix, limit=limit - len(paths)))
            if len(paths) >= limit:
                return paths[:limit]
    elif isinstance(value, list) and value:
        paths.append(f"{prefix}:[]" if prefix else "[]")
    return paths[:limit]

def configuration_report(root: Path, files: list[Path], projects: list[dict[str, Any]]) -> dict[str, Any]:
    appsettings: list[dict[str, Any]] = []
    launch_settings: list[dict[str, Any]] = []
    for path in sorted(files, key=lambda item: rel(root, item)):
        name = path.name.lower()
        if name.startswith("appsettings") and path.suffix.lower() == ".json":
            data = read_json(path)
            connection_strings = data.get("ConnectionStrings") if isinstance(data.get("ConnectionStrings"), dict) else {}
            appsettings.append(
                {
                    "path": rel(root, path),
                    "key_paths": json_key_paths(data),
                    "connection_string_names": sorted(str(key) for key in connection_strings),
                }
            )
        elif name == "launchsettings.json":
            data = read_json(path)
            profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else {}
            launch_settings.append({"path": rel(root, path), "profiles": sorted(str(key) for key in profiles)})
    user_secrets = []
    for project in projects:
        static = project.get("static_properties") if isinstance(project.get("static_properties"), dict) else {}
        user_secrets_id = static.get("UserSecretsId")
        if user_secrets_id:
            user_secrets.append({"project": project.get("path", ""), "id": str(user_secrets_id)})
    return {
        "appsettings_files": appsettings[:40],
        "launch_settings": launch_settings[:20],
        "user_secrets_ids": user_secrets[:40],
        "values_emitted": False,
        "policy": "Only JSON key paths, launch profile names, connection string names, and UserSecretsId identifiers were reported; values are not emitted.",
    }

def persistence_report(root: Path, files: list[Path], projects: list[dict[str, Any]], packages: dict[str, list[str]]) -> dict[str, Any]:
    db_contexts: list[dict[str, Any]] = []
    dbset_paths: list[str] = []
    migration_paths: list[str] = []
    for path in sorted(files, key=lambda item: rel(root, item)):
        relative = rel(root, path)
        if path.suffix.lower() == ".cs":
            text = read_text(path)
            class_names = re.findall(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?:[A-Za-z0-9_<>,\s]+\s*,\s*)?DbContext\b", text)
            if class_names:
                db_contexts.append({"path": relative, "class_names": sorted(dict.fromkeys(class_names))})
            if "DbSet<" in text:
                dbset_paths.append(relative)
        if "/migrations/" in relative.lower() or relative.lower().endswith("/migrations"):
            migration_paths.append(relative)
    provider_packages = sorted(
        name
        for name in packages
        if any(marker in name.lower() for marker in PERSISTENCE_PACKAGE_MARKERS)
    )
    return {
        "db_contexts": db_contexts[:80],
        "dbset_paths": sorted(dict.fromkeys(dbset_paths))[:80],
        "migration_paths": sorted(dict.fromkeys(migration_paths))[:80],
        "provider_packages": provider_packages[:80],
        "project_count": len(projects),
    }

def features_report(projects: list[dict[str, Any]], packages: dict[str, list[str]], persistence: dict[str, Any]) -> dict[str, Any]:
    signals: dict[str, set[str]] = {}
    for project in projects:
        project_path = str(project.get("path", ""))
        classification = str(project.get("classification", ""))
        sdk = str(project.get("sdk", "")).lower()
        if classification == "web" or "web" in sdk:
            signals.setdefault("aspnet-core", set()).add(project_path)
        if classification == "worker":
            signals.setdefault("worker-service", set()).add(project_path)
        if classification == "test":
            signals.setdefault("test-projects", set()).add(project_path)
    for package, evidence_paths in packages.items():
        lowered = package.lower()
        for signal_id, markers in FEATURE_PACKAGE_MARKERS.items():
            if any(marker in lowered for marker in markers):
                signals.setdefault(signal_id, set()).update(evidence_paths)
    if persistence.get("db_contexts") or persistence.get("provider_packages"):
        signals.setdefault("ef-core", set()).update(
            str(item.get("path", ""))
            for item in persistence.get("db_contexts", [])
            if isinstance(item, dict) and item.get("path")
        )
        signals["ef-core"].update(str(item) for item in persistence.get("provider_packages", []))
    rows = [{"id": signal_id, "evidence_paths": sorted(path for path in paths if path)} for signal_id, paths in sorted(signals.items())]
    return {"signals": rows, "signal_ids": [row["id"] for row in rows]}

def diff_reports(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    categories: list[str] = []
    details: dict[str, Any] = {}

    def project_frameworks(report: dict[str, Any]) -> dict[str, list[str]]:
        projects = report.get("projects") if isinstance(report.get("projects"), list) else []
        return {
            str(project.get("path")): sorted(str(item) for item in project.get("target_frameworks", []) if item)
            for project in projects
            if isinstance(project, dict) and project.get("path")
        }

    before_frameworks = project_frameworks(baseline)
    after_frameworks = project_frameworks(current)
    added_projects = sorted(set(after_frameworks) - set(before_frameworks))
    removed_projects = sorted(set(before_frameworks) - set(after_frameworks))
    changed_frameworks = {
        path: {"before": before_frameworks[path], "after": after_frameworks[path]}
        for path in sorted(set(before_frameworks).intersection(after_frameworks))
        if before_frameworks[path] != after_frameworks[path]
    }
    if added_projects:
        categories.append("projects_added")
        details["projects_added"] = added_projects
    if removed_projects:
        categories.append("projects_removed")
        details["projects_removed"] = removed_projects
    if changed_frameworks:
        categories.append("target_frameworks_changed")
        details["target_frameworks_changed"] = changed_frameworks

    before_sources = sorted(
        str(item.get("key", ""))
        for item in baseline.get("nuget", {}).get("sources", [])
        if isinstance(item, dict)
    )
    after_sources = sorted(
        str(item.get("key", ""))
        for item in current.get("nuget", {}).get("sources", [])
        if isinstance(item, dict)
    )
    if before_sources != after_sources:
        categories.append("nuget_sources_changed")
        details["nuget_sources_changed"] = {"before": before_sources, "after": after_sources}

    before_policy = baseline.get("build_policy", {}).get("properties", {}) if isinstance(baseline.get("build_policy"), dict) else {}
    after_policy = current.get("build_policy", {}).get("properties", {}) if isinstance(current.get("build_policy"), dict) else {}
    if before_policy != after_policy:
        categories.append("build_policy_changed")
        details["build_policy_changed"] = {"before": before_policy, "after": after_policy}

    return {"changed": bool(categories), "categories": categories, "details": details}
