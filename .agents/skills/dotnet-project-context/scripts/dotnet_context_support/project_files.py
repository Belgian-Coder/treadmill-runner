"""Project, solution, global.json, and build-policy parsing."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .common import local_name, parse_xml, read_json, read_text, rel

PROPERTIES_TO_PROBE = (
    "TargetFramework",
    "TargetFrameworks",
    "OutputType",
    "Nullable",
    "ImplicitUsings",
    "TreatWarningsAsErrors",
    "IsTestProject",
    "UseWPF",
    "UseWindowsForms",
)
ITEMS_TO_PROBE = ("PackageReference", "ProjectReference")
BUILD_POLICY_FILE_NAMES = {
    "Directory.Build.props",
    "Directory.Build.targets",
    "Directory.Solution.props",
    "Directory.Solution.targets",
    "Directory.Packages.props",
}
BUILD_POLICY_PROPERTIES = {
    "AnalysisLevel",
    "ContinuousIntegrationBuild",
    "EnableNETAnalyzers",
    "EnforceCodeStyleInBuild",
    "ImplicitUsings",
    "LangVersion",
    "ManagePackageVersionsCentrally",
    "Nullable",
    "RestoreLockedMode",
    "RestorePackagesWithLockFile",
    "TargetFramework",
    "TargetFrameworks",
    "TreatWarningsAsErrors",
}

def split_frameworks(*values: str) -> list[str]:
    frameworks: list[str] = []
    for value in values:
        for item in re.split(r"[;,]", value or ""):
            item = item.strip()
            if item:
                frameworks.append(item)
    return sorted(dict.fromkeys(frameworks))

def property_values(xml_root: ET.Element | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if xml_root is None:
        return values
    for group in xml_root:
        if local_name(group.tag) != "PropertyGroup":
            continue
        for child in group:
            name = local_name(child.tag)
            text = (child.text or "").strip()
            if text and name not in values:
                values[name] = text
    return values

def item_references(xml_root: ET.Element | None, item_name: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if xml_root is None:
        return rows
    for group in xml_root:
        if local_name(group.tag) != "ItemGroup":
            continue
        for child in group:
            if local_name(child.tag) != item_name:
                continue
            include = child.attrib.get("Include") or child.attrib.get("Update") or child.attrib.get("Remove") or ""
            row = {str(key): str(value) for key, value in child.attrib.items()}
            if include:
                row["Include"] = include
            rows.append(row)
    return rows

def classify_project(path: Path, sdk: str, properties: dict[str, str], packages: list[str]) -> str:
    lowered_path = path.as_posix().lower()
    lowered_sdk = sdk.lower()
    lowered_packages = {package.lower() for package in packages}
    if properties.get("IsTestProject", "").lower() == "true" or lowered_path.endswith(".tests.csproj") or "/test" in lowered_path:
        return "test"
    if lowered_packages.intersection({"microsoft.net.test.sdk", "xunit", "nunit", "mstest.testframework"}):
        return "test"
    if "web" in lowered_sdk or any(package.startswith("microsoft.aspnetcore") for package in lowered_packages):
        return "web"
    if properties.get("UseWPF", "").lower() == "true":
        return "wpf"
    if properties.get("UseWindowsForms", "").lower() == "true":
        return "winforms"
    if "microsoft.extensions.hosting" in lowered_packages or "worker" in lowered_path:
        return "worker"
    if properties.get("OutputType", "").lower() == "exe":
        return "app"
    return "library"

def parse_project(root: Path, path: Path) -> dict[str, Any]:
    xml_root = parse_xml(path)
    properties = property_values(xml_root)
    sdk = ""
    if xml_root is not None:
        sdk = xml_root.attrib.get("Sdk", "")
    packages = item_references(xml_root, "PackageReference")
    project_refs = item_references(xml_root, "ProjectReference")
    package_names = sorted(dict.fromkeys(str(item.get("Include", "")) for item in packages if item.get("Include")))
    frameworks = split_frameworks(properties.get("TargetFramework", ""), properties.get("TargetFrameworks", ""))
    return {
        "path": rel(root, path),
        "language": path.suffix.lower().lstrip("."),
        "sdk": sdk,
        "target_frameworks": frameworks,
        "output_type": properties.get("OutputType", ""),
        "nullable": properties.get("Nullable", ""),
        "implicit_usings": properties.get("ImplicitUsings", ""),
        "warnings_as_errors": properties.get("TreatWarningsAsErrors", ""),
        "package_references": package_names[:80],
        "package_reference_rows": packages[:80],
        "project_references": [str(item.get("Include", "")) for item in project_refs if item.get("Include")][:80],
        "classification": classify_project(path, sdk, properties, package_names),
        "static_properties": {
            key: value
            for key, value in properties.items()
            if key in {"TargetFramework", "TargetFrameworks", "OutputType", "Nullable", "ImplicitUsings", "TreatWarningsAsErrors", "IsTestProject", "UseWPF", "UseWindowsForms", "UserSecretsId"}
        },
        "evaluated": {},
        "evaluated_package_references": [],
        "evaluated_project_references": [],
    }

def parse_solution(root: Path, path: Path) -> dict[str, Any]:
    text = read_text(path)
    if path.suffix.lower() == ".slnx":
        xml_root = parse_xml(path)
        project_paths = []
        if xml_root is not None:
            for element in xml_root.iter():
                for key, value in element.attrib.items():
                    if key.lower() in {"path", "file"} and Path(value).suffix.lower() in {".csproj", ".fsproj", ".vbproj"}:
                        project_paths.append(value.replace("\\", "/"))
        if not project_paths:
            project_paths = [
                item.replace("\\", "/")
                for item in re.findall(r'(?:Path|File)\s*=\s*"([^"]+\.(?:csproj|fsproj|vbproj))"', text, flags=re.IGNORECASE)
            ]
    else:
        project_paths = [
            item.replace("\\", "/")
            for item in re.findall(r'Project\("[^"]+"\)\s*=\s*"[^"]+",\s*"([^"]+\.(?:csproj|fsproj|vbproj))"', text, flags=re.IGNORECASE)
        ]
    project_paths = sorted(dict.fromkeys(project_paths))
    return {"path": rel(root, path), "format": path.suffix.lower().lstrip("."), "projects": project_paths}

def parse_global_json(root: Path) -> dict[str, Any]:
    data = read_json(root / "global.json")
    sdk = data.get("sdk") if isinstance(data.get("sdk"), dict) else {}
    return {
        "path": "global.json" if data else "",
        "sdk_version": str(sdk.get("version", "")) if isinstance(sdk, dict) else "",
        "roll_forward": str(sdk.get("rollForward", "")) if isinstance(sdk, dict) else "",
        "paths": data.get("paths", []) if isinstance(data.get("paths"), list) else [],
    }

def parse_dotnet_tools(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in (root / ".config").glob("dotnet-tools.json"):
        data = read_json(path)
        tools = data.get("tools") if isinstance(data.get("tools"), dict) else {}
        rows.append({"path": rel(root, path), "tools": sorted(str(name) for name in tools)})
    return rows

def build_policy_report(root: Path, files: list[Path]) -> dict[str, Any]:
    policy_files = [path for path in files if path.name in BUILD_POLICY_FILE_NAMES]
    rows: list[dict[str, Any]] = []
    properties: dict[str, str] = {}
    for path in sorted(policy_files, key=lambda item: rel(root, item)):
        xml_root = parse_xml(path)
        parsed = property_values(xml_root)
        selected = {key: value for key, value in parsed.items() if key in BUILD_POLICY_PROPERTIES}
        for key, value in selected.items():
            properties.setdefault(key, value)
        rows.append(
            {
                "path": rel(root, path),
                "kind": path.name,
                "properties": selected,
            }
        )
    return {
        "files": rows,
        "properties": properties,
        "policy_file_count": len(rows),
        "warnings_as_errors": properties.get("TreatWarningsAsErrors", ""),
        "nullable": properties.get("Nullable", ""),
        "analysis_level": properties.get("AnalysisLevel", ""),
        "central_package_management": properties.get("ManagePackageVersionsCentrally", "").lower() == "true",
        "restore_lock_policy": {
            "restore_packages_with_lock_file": properties.get("RestorePackagesWithLockFile", "").lower() == "true",
            "restore_locked_mode": properties.get("RestoreLockedMode", "").lower() == "true",
        },
    }
