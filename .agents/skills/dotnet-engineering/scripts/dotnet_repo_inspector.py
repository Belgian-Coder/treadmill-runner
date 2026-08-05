#!/usr/bin/env python3
"""Deterministic .NET repository inventory, NuGet plan, and Framework compatibility evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

SKIP_DIR_NAMES = {".git", ".vs", "bin", "obj", "node_modules", "__pycache__"}
SKIP_SUBPATHS = ((".agents", "local-ai", "cache"),)
PROJECT_SUFFIXES = {".csproj", ".vbproj", ".fsproj"}
GENERAL_NUGET_SOURCE = "https://api.nuget.org/v3/index.json"
TEXT_EXTENSIONS = {".cs", ".vb", ".fs", ".cshtml", ".razor", ".config", ".xaml", ".xml", ".csproj", ".vbproj", ".fsproj"}

FRAMEWORK_PATTERNS: list[dict[str, str]] = [
    {"id": "system-web", "severity": "high", "pattern": r"\bSystem\.Web\b|<ProjectGuid>|<UseIISExpress>|<WebProjectProperties", "note": "classic ASP.NET/System.Web hosting needs a migration strategy"},
    {"id": "wcf", "severity": "high", "pattern": r"\bSystem\.ServiceModel\b|<system\.serviceModel\b|ServiceHost\(", "note": "WCF/server hosting requires replacement, bridge, or product decision"},
    {"id": "remoting", "severity": "high", "pattern": r"\bSystem\.Runtime\.Remoting\b|RemotingConfiguration|MarshalByRefObject", "note": "remoting is not a direct modern .NET migration"},
    {"id": "binaryformatter", "severity": "high", "pattern": r"\bBinaryFormatter\b", "note": "BinaryFormatter requires replacement before safe migration"},
    {"id": "cas", "severity": "high", "pattern": r"CodeAccessPermission|SecurityCritical|PermissionSet|SecurityPermission", "note": "Code Access Security-era assumptions need review"},
    {"id": "appdomain", "severity": "medium", "pattern": r"AppDomain\.CreateDomain|AppDomain\.Unload|CreateInstanceAndUnwrap", "note": "AppDomain isolation/unload behavior needs redesign"},
    {"id": "com", "severity": "medium", "pattern": r"\[ComImport\]|\bProgIdAttribute\b|\bGuidAttribute\b|RegisterForComInterop|tlbimp|regasm", "note": "COM interop/registration must stay explicit"},
    {"id": "registry", "severity": "medium", "pattern": r"\bMicrosoft\.Win32\.Registry\b|RegistryKey", "note": "registry access is Windows-specific and deployment-sensitive"},
    {"id": "binding-redirect", "severity": "medium", "pattern": r"<bindingRedirect\b|<assemblyBinding\b", "note": "binding redirects/config behavior do not migrate directly"},
    {"id": "packages-config", "severity": "medium", "pattern": r"<package\s+id=", "note": "packages.config requires package-mode migration planning"},
    {"id": "framework-target", "severity": "medium", "pattern": r"<TargetFrameworkVersion>\s*v4\.[5-9]", "note": ".NET Framework target requires legacy inventory and baseline evidence"},
    {"id": "windows-ui", "severity": "info", "pattern": r"<UseWPF>true</UseWPF>|<UseWindowsForms>true</UseWindowsForms>|System\.Windows\.Forms|PresentationFramework", "note": "desktop UI migration depends on Windows UI/runtime constraints"},
]


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def read_text(path: Path, limit: int = 500_000) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")[:limit]
    except OSError:
        return ""


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def should_skip(path: Path) -> bool:
    parts = tuple(part.replace("\\", "/").lower() for part in path.parts)
    if any(part in SKIP_DIR_NAMES for part in parts):
        return True
    return any(
        parts[index:index + len(skip)] == skip
        for skip in SKIP_SUBPATHS
        for index in range(0, len(parts) - len(skip) + 1)
    )


def walk_files(root: Path, *, suffixes: set[str] | None = None) -> list[Path]:
    files: list[Path] = []
    for current_root, dir_names, file_names in os.walk(root):
        current = Path(current_root)
        dir_names[:] = [
            dirname
            for dirname in sorted(dir_names)
            if not should_skip(current / dirname)
        ]
        for file_name in sorted(file_names):
            path = current / file_name
            if should_skip(path):
                continue
            if suffixes is not None and path.suffix.lower() not in suffixes:
                continue
            files.append(path)
    return files


def walk_named_files(root: Path, name: str) -> list[Path]:
    lower_name = name.lower()
    return [
        path
        for path in walk_files(root)
        if path.name.lower() == lower_name
    ]


def parse_xml(path: Path) -> ET.Element | None:
    try:
        return ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def elements(root: ET.Element | None, name: str) -> list[ET.Element]:
    if root is None:
        return []
    return [item for item in root.iter() if local_name(item.tag) == name]


def text_values(root: ET.Element | None, name: str) -> list[str]:
    return [(item.text or "").strip() for item in elements(root, name) if (item.text or "").strip()]


def bool_property(root: ET.Element | None, name: str) -> bool:
    return any(value.lower() == "true" for value in text_values(root, name))


def project_style(project_root: ET.Element | None, path: Path) -> str:
    if project_root is None:
        return "unreadable"
    if project_root.attrib.get("Sdk"):
        return "sdk-style"
    text = read_text(path, limit=100_000)
    if "Microsoft.CSharp.targets" in text or "ToolsVersion" in text:
        return "old-msbuild"
    return "unknown"


def target_frameworks(project_root: ET.Element | None) -> list[str]:
    values: list[str] = []
    for name in ("TargetFramework", "TargetFrameworks", "TargetFrameworkVersion"):
        for value in text_values(project_root, name):
            values.extend(part.strip() for part in re.split(r"[;,]", value) if part.strip())
    return sorted(dict.fromkeys(values))


def project_type(project_root: ET.Element | None, path: Path) -> str:
    text = read_text(path, limit=100_000)
    sdk = project_root.attrib.get("Sdk", "") if project_root is not None else ""
    if "Microsoft.NET.Sdk.Web" in sdk:
        return "web"
    if "Microsoft.NET.Sdk.Worker" in sdk:
        return "worker"
    if bool_property(project_root, "UseWPF"):
        return "wpf"
    if bool_property(project_root, "UseWindowsForms"):
        return "winforms"
    if "System.Web" in text or "Microsoft.WebApplication.targets" in text:
        return "classic-aspnet"
    if "System.ServiceModel" in text or "<system.serviceModel" in text:
        return "wcf"
    return "library-or-app"


def package_owner_rows(root: Path, project_path: Path, project_root: ET.Element | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item_name, owner_kind in (
        ("PackageReference", "project-package-reference"),
        ("PackageVersion", "project-package-version"),
        ("GlobalPackageReference", "project-global-package-reference"),
        ("PackageDownload", "project-package-download"),
        ("DotNetCliToolReference", "project-cli-tool"),
    ):
        for item in elements(project_root, item_name):
            package_id = item.attrib.get("Include") or item.attrib.get("Update") or item.attrib.get("Remove") or ""
            if not package_id:
                continue
            rows.append(
                {
                    "id": package_id,
                    "version": item.attrib.get("Version", ""),
                    "version_override": item.attrib.get("VersionOverride", ""),
                    "owner_kind": owner_kind,
                    "owner_file": rel(root, project_path),
                    "project": rel(root, project_path),
                }
            )
    return rows


def packages_config_rows(root: Path, path: Path) -> list[dict[str, Any]]:
    xml = parse_xml(path)
    rows: list[dict[str, Any]] = []
    for item in elements(xml, "package"):
        package_id = item.attrib.get("id", "")
        if not package_id:
            continue
        rows.append(
            {
                "id": package_id,
                "version": item.attrib.get("version", ""),
                "target_framework": item.attrib.get("targetFramework", ""),
                "owner_kind": "packages.config",
                "owner_file": rel(root, path),
                "project": "",
            }
        )
    return rows


def central_package_rows(root: Path, path: Path) -> list[dict[str, Any]]:
    xml = parse_xml(path)
    rows: list[dict[str, Any]] = []
    for item_name, owner_kind in (
        ("PackageVersion", "central-package-version"),
        ("GlobalPackageReference", "central-global-package-reference"),
        ("PackageDownload", "central-package-download"),
    ):
        for item in elements(xml, item_name):
            package_id = item.attrib.get("Include") or item.attrib.get("Update") or ""
            if not package_id:
                continue
            rows.append(
                {
                    "id": package_id,
                    "version": item.attrib.get("Version", ""),
                    "owner_kind": owner_kind,
                    "owner_file": rel(root, path),
                    "project": "",
                }
            )
    return rows


def tool_manifest_rows(root: Path, path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    tools = data.get("tools") if isinstance(data, dict) else {}
    if not isinstance(tools, dict):
        return []
    rows: list[dict[str, Any]] = []
    for package_id, item in sorted(tools.items()):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "id": str(package_id),
                "version": str(item.get("version", "")),
                "owner_kind": "dotnet-tool-manifest",
                "owner_file": rel(root, path),
                "project": "",
                "commands": item.get("commands", []),
            }
        )
    return rows


def parse_nuget_config(root: Path, path: Path) -> dict[str, Any]:
    xml = parse_xml(path)
    sources: list[dict[str, Any]] = []
    disabled: list[str] = []
    mapping: dict[str, list[str]] = {}
    for parent in elements(xml, "packageSources"):
        for child in list(parent):
            if local_name(child.tag) != "add":
                continue
            sources.append(
                {
                    "key": child.attrib.get("key", ""),
                    "value": child.attrib.get("value", ""),
                    "file": rel(root, path),
                }
            )
    for parent in elements(xml, "disabledPackageSources"):
        for child in list(parent):
            if local_name(child.tag) == "add" and str(child.attrib.get("value", "")).lower() == "true":
                disabled.append(child.attrib.get("key", ""))
    for source in elements(xml, "packageSource"):
        key = source.attrib.get("key", "")
        patterns = [package.attrib.get("pattern", "") for package in list(source) if local_name(package.tag) == "package"]
        mapping[key] = [pattern for pattern in patterns if pattern]
    return {
        "path": rel(root, path),
        "sources": sources,
        "disabled_sources": disabled,
        "source_mapping": mapping,
        "has_clear": "<clear" in read_text(path, limit=50_000),
    }


def build_inventory(root: Path) -> dict[str, Any]:
    projects: list[dict[str, Any]] = []
    package_rows: list[dict[str, Any]] = []
    central_package_files = walk_named_files(root, "Directory.Packages.props")
    central_enabled_files: list[str] = []
    for path in central_package_files:
        if should_skip(path):
            continue
        xml = parse_xml(path)
        if bool_property(xml, "ManagePackageVersionsCentrally"):
            central_enabled_files.append(rel(root, path))
        package_rows.extend(central_package_rows(root, path))
    for project_path in walk_files(root, suffixes=PROJECT_SUFFIXES):
        project_root = parse_xml(project_path)
        project_package_rows = package_owner_rows(root, project_path, project_root)
        package_rows.extend(project_package_rows)
        projects.append(
            {
                "path": rel(root, project_path),
                "style": project_style(project_root, project_path),
                "type": project_type(project_root, project_path),
                "target_frameworks": target_frameworks(project_root),
                "sdk": project_root.attrib.get("Sdk", "") if project_root is not None else "",
                "uses_wpf": bool_property(project_root, "UseWPF"),
                "uses_winforms": bool_property(project_root, "UseWindowsForms"),
                "package_reference_count": sum(1 for row in project_package_rows if row["owner_kind"] == "project-package-reference"),
                "project_reference_count": len(elements(project_root, "ProjectReference")),
                "project_references": [
                    item.attrib.get("Include", "")
                    for item in elements(project_root, "ProjectReference")
                    if item.attrib.get("Include")
                ],
            }
        )
    for packages_config in walk_named_files(root, "packages.config"):
        package_rows.extend(packages_config_rows(root, packages_config))
    for tool_manifest in walk_named_files(root, "dotnet-tools.json"):
        package_rows.extend(tool_manifest_rows(root, tool_manifest))
    nuget_configs = [
        parse_nuget_config(root, path)
        for path in walk_named_files(root, "nuget.config")
    ]
    sln_files = [rel(root, path) for path in walk_files(root, suffixes={".sln", ".slnx"})]
    target_counter: dict[str, int] = {}
    for project in projects:
        for tfm in project["target_frameworks"]:
            target_counter[tfm] = target_counter.get(tfm, 0) + 1
    owner_counter: dict[str, int] = {}
    for row in package_rows:
        owner_counter[row["owner_kind"]] = owner_counter.get(row["owner_kind"], 0) + 1
    return {
        "projects": projects,
        "solutions": sln_files,
        "nuget_configs": nuget_configs,
        "central_package_management": {
            "enabled": bool(central_enabled_files),
            "files": central_enabled_files,
        },
        "packages": sorted(package_rows, key=lambda item: (item.get("id", ""), item.get("owner_file", ""), item.get("owner_kind", ""))),
        "summary": {
            "project_count": len(projects),
            "old_msbuild_project_count": sum(1 for project in projects if project["style"] == "old-msbuild"),
            "sdk_style_project_count": sum(1 for project in projects if project["style"] == "sdk-style"),
            "package_owner_count": len(package_rows),
            "package_owner_kinds": owner_counter,
            "target_frameworks": dict(sorted(target_counter.items())),
            "solution_count": len(sln_files),
            "nuget_config_count": len(nuget_configs),
            "central_package_management": bool(central_enabled_files),
        },
    }


def build_nuget_plan(root: Path, inventory: dict[str, Any], target_version: str = "") -> dict[str, Any]:
    configs = inventory.get("nuget_configs") if isinstance(inventory.get("nuget_configs"), list) else []
    has_repo_config = bool(configs)
    sources: list[dict[str, Any]] = []
    disabled: set[str] = set()
    source_mapping: dict[str, list[str]] = {}
    for config in configs:
        if not isinstance(config, dict):
            continue
        sources.extend(config.get("sources", []) if isinstance(config.get("sources"), list) else [])
        disabled.update(str(item) for item in config.get("disabled_sources", []) if str(item))
        mapping = config.get("source_mapping") if isinstance(config.get("source_mapping"), dict) else {}
        for key, patterns in mapping.items():
            source_mapping[str(key)] = [str(pattern) for pattern in patterns if str(pattern)]
    active_sources = [
        source for source in sources
        if isinstance(source, dict) and str(source.get("key", "")) not in disabled
    ]
    if not has_repo_config:
        active_sources = [{"key": "nuget.org", "value": GENERAL_NUGET_SOURCE, "file": "<fallback>"}]
    packages = inventory.get("packages") if isinstance(inventory.get("packages"), list) else []
    package_decisions: list[dict[str, Any]] = []
    for row in packages:
        if not isinstance(row, dict):
            continue
        owner_kind = str(row.get("owner_kind", ""))
        current_version = str(row.get("version", "") or row.get("version_override", ""))
        decision = "inspect"
        reason = "source-backed version decision required"
        if owner_kind == "packages.config":
            decision = "migrate-package-mode"
            reason = "packages.config owner must be migrated or deliberately retained under legacy scope"
        elif owner_kind.endswith("download"):
            decision = "verify-target-asset"
            reason = "PackageDownload must be checked against the requested runtime/reference pack"
        elif not current_version and owner_kind == "project-package-reference":
            decision = "central-owner"
            reason = "PackageReference version is owned centrally or by imported props"
        package_decisions.append(
            {
                "id": row.get("id", ""),
                "current_version": current_version,
                "owner_kind": owner_kind,
                "owner_file": row.get("owner_file", ""),
                "decision": decision,
                "reason": reason,
                "target_version": target_version,
            }
        )
    blockers: list[dict[str, str]] = []
    if has_repo_config and not active_sources:
        blockers.append({"kind": "nuget-source", "reason": "repository NuGet config exists but no active package source was found"})
    if any(row.get("owner_kind") == "packages.config" for row in package_decisions):
        blockers.append({"kind": "package-mode", "reason": "packages.config entries require explicit migration or legacy decision"})
    return {
        "has_repository_or_solution_nuget_config": has_repo_config,
        "fallback_to_general_nuget": not has_repo_config,
        "active_sources": active_sources,
        "disabled_sources": sorted(disabled),
        "package_source_mapping": source_mapping,
        "package_decisions": package_decisions,
        "blockers": blockers,
        "summary": {
            "active_source_count": len(active_sources),
            "package_count": len(package_decisions),
            "fallback_to_general_nuget": not has_repo_config,
            "blocker_count": len(blockers),
            "owner_kinds": inventory.get("summary", {}).get("package_owner_kinds", {}),
        },
    }


def line_hits(root: Path, path: Path, pattern: re.Pattern[str], note: str, severity: str, rule_id: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for index, line in enumerate(read_text(path).splitlines(), start=1):
        if pattern.search(line):
            hits.append(
                {
                    "rule_id": rule_id,
                    "severity": severity,
                    "path": rel(root, path),
                    "line": index,
                    "excerpt": line.strip()[:240],
                    "note": note,
                }
            )
    return hits


def build_framework_compat(root: Path, inventory: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    compiled = [
        (item, re.compile(item["pattern"], flags=re.IGNORECASE))
        for item in FRAMEWORK_PATTERNS
    ]
    for path in walk_files(root, suffixes=TEXT_EXTENSIONS):
        for item, pattern in compiled:
            findings.extend(line_hits(root, path, pattern, item["note"], item["severity"], item["id"]))
    severity_counts: dict[str, int] = {}
    rule_counts: dict[str, int] = {}
    for finding in findings:
        severity_counts[finding["severity"]] = severity_counts.get(finding["severity"], 0) + 1
        rule_counts[finding["rule_id"]] = rule_counts.get(finding["rule_id"], 0) + 1
    project_blockers = [
        {
            "project": project["path"],
            "reason": "old MSBuild/.NET Framework project requires legacy migration path",
            "target_frameworks": project["target_frameworks"],
        }
        for project in inventory.get("projects", [])
        if isinstance(project, dict)
        and (project.get("style") == "old-msbuild" or any(str(tfm).startswith("v4.") or str(tfm).startswith("net4") for tfm in project.get("target_frameworks", [])))
    ]
    return {
        "findings": findings,
        "project_blockers": project_blockers,
        "summary": {
            "finding_count": len(findings),
            "severity_counts": dict(sorted(severity_counts.items())),
            "rule_counts": dict(sorted(rule_counts.items())),
            "project_blocker_count": len(project_blockers),
        },
    }


def build_report(command: str, target: Path, *, target_version: str = "") -> dict[str, Any]:
    root = target.expanduser().resolve()
    inventory = build_inventory(root)
    report: dict[str, Any] = {
        "schema_version": 1,
        "tool": "dotnet-engineering.repo-inspector",
        "command": command,
        "ok": True,
        "status": "passed",
        "generated_at": utc_now(),
        "target": str(root),
    }
    if command in {"inventory", "all"}:
        report["inventory"] = inventory
    if command in {"nuget-plan", "all"}:
        report["nuget_plan"] = build_nuget_plan(root, inventory, target_version=target_version)
    if command in {"framework-compat", "all"}:
        report["framework_compatibility"] = build_framework_compat(root, inventory)
    summary: dict[str, Any] = {"project_count": inventory["summary"]["project_count"]}
    if "nuget_plan" in report:
        summary["nuget_blocker_count"] = report["nuget_plan"]["summary"]["blocker_count"]
        summary["fallback_to_general_nuget"] = report["nuget_plan"]["summary"]["fallback_to_general_nuget"]
    if "framework_compatibility" in report:
        summary["compatibility_finding_count"] = report["framework_compatibility"]["summary"]["finding_count"]
        summary["compatibility_project_blockers"] = report["framework_compatibility"]["summary"]["project_blocker_count"]
    report["summary"] = summary
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# .NET Repository Inspector", ""]
    lines.append(f"- Command: `{report.get('command')}`")
    lines.append(f"- Status: {report.get('status')}")
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines.append(f"- Projects: {summary.get('project_count', 0)}")
    if "fallback_to_general_nuget" in summary:
        lines.append(f"- General NuGet fallback: {str(summary.get('fallback_to_general_nuget')).lower()}")
        lines.append(f"- NuGet blockers: {summary.get('nuget_blocker_count', 0)}")
    if "compatibility_finding_count" in summary:
        lines.append(f"- Compatibility findings: {summary.get('compatibility_finding_count', 0)}")
        lines.append(f"- Compatibility project blockers: {summary.get('compatibility_project_blockers', 0)}")
    inventory = report.get("inventory") if isinstance(report.get("inventory"), dict) else {}
    projects = inventory.get("projects") if isinstance(inventory.get("projects"), list) else []
    if projects:
        lines.extend(["", "## Projects", ""])
        for project in projects[:25]:
            if isinstance(project, dict):
                tfms = ", ".join(str(item) for item in project.get("target_frameworks", []))
                lines.append(f"- `{project.get('path')}`: {project.get('style')} {tfms}")
    nuget_plan = report.get("nuget_plan") if isinstance(report.get("nuget_plan"), dict) else {}
    blockers = nuget_plan.get("blockers") if isinstance(nuget_plan.get("blockers"), list) else []
    if blockers:
        lines.extend(["", "## NuGet Blockers", ""])
        for blocker in blockers:
            if isinstance(blocker, dict):
                lines.append(f"- `{blocker.get('kind')}`: {blocker.get('reason')}")
    compat = report.get("framework_compatibility") if isinstance(report.get("framework_compatibility"), dict) else {}
    findings = compat.get("findings") if isinstance(compat.get("findings"), list) else []
    if findings:
        lines.extend(["", "## Compatibility Findings", ""])
        for finding in findings[:25]:
            if isinstance(finding, dict):
                lines.append(f"- `{finding.get('severity')}` `{finding.get('rule_id')}` {finding.get('path')}:{finding.get('line')} - {finding.get('note')}")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("inventory", "nuget-plan", "framework-compat", "all"):
        command = subcommands.add_parser(name)
        command.add_argument("--target", default=".", help="repository or project root to inspect")
        command.add_argument("--target-version", default="", help="requested target .NET version for package decisions")
        command.add_argument("--output-json")
        command.add_argument("--output-md")
        command.add_argument("--format", choices=("json", "markdown"), default="json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(args.command, Path(args.target), target_version=args.target_version)
    if args.output_json:
        write_json(Path(args.output_json), report)
    if args.output_md:
        write_text(Path(args.output_md), render_markdown(report))
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
