"""Repo-local NuGet/feed inspection for dotnet-project-context."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .common import local_name, parse_xml, rel
from .project_files import item_references, property_values

PUBLIC_NUGET_SOURCES = {"https://api.nuget.org/v3/index.json", "https://www.nuget.org/api/v2/"}

def active_source(value: str) -> bool:
    return bool(value.strip())

def source_is_private(key: str, value: str) -> bool:
    normalized_value = value.strip().rstrip("/").lower()
    normalized_public = {item.rstrip("/").lower() for item in PUBLIC_NUGET_SOURCES}
    if not normalized_value:
        return False
    if normalized_value in normalized_public:
        return False
    if key.lower() in {"nuget.org", "nuget"} and "nuget.org" in normalized_value:
        return False
    return True

def redact_source_value(value: str) -> tuple[str, bool]:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return value, False
    redacted = bool(parsed.username or parsed.password or parsed.query or parsed.fragment)
    host = parsed.hostname or ""
    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"
    safe = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    return safe, redacted

def parse_nuget_config(root: Path, path: Path) -> dict[str, Any]:
    xml_root = parse_xml(path)
    sources: list[dict[str, object]] = []
    disabled: set[str] = set()
    mappings: list[dict[str, str]] = []
    credentials: list[str] = []
    clear_sources = False
    if xml_root is None:
        return {
            "path": rel(root, path),
            "sources": [],
            "package_source_mapping": [],
            "credential_sections": [],
            "clear_sources": False,
        }
    for child in xml_root:
        name = local_name(child.tag)
        if name == "disabledPackageSources":
            for source in child:
                key = source.attrib.get("key", "")
                if key:
                    disabled.add(key)
        if name == "packageSources":
            for source in child:
                if local_name(source.tag) == "clear":
                    clear_sources = True
                    continue
                if local_name(source.tag) != "add":
                    continue
                key = source.attrib.get("key", "")
                value = source.attrib.get("value", "")
                if not active_source(value):
                    continue
                safe_value, redacted = redact_source_value(value)
                sources.append(
                    {
                        "key": key,
                        "value": safe_value,
                        "enabled": key not in disabled,
                        "private": key not in disabled and source_is_private(key, value),
                        "redacted": redacted,
                    }
                )
        if name == "packageSourceMapping":
            for source in child:
                source_key = source.attrib.get("key", "")
                for package in source:
                    pattern = package.attrib.get("pattern", "")
                    if source_key or pattern:
                        mappings.append({"source": source_key, "pattern": pattern})
        if name == "packageSourceCredentials":
            for source in child:
                section = local_name(source.tag)
                if section:
                    credentials.append(section)
    return {
        "path": rel(root, path),
        "sources": sources,
        "package_source_mapping": mappings,
        "credential_sections": sorted(set(credentials)),
        "clear_sources": clear_sources,
    }
def parse_directory_packages(root: Path, path: Path) -> dict[str, Any]:
    xml_root = parse_xml(path)
    properties = property_values(xml_root)
    packages = item_references(xml_root, "PackageVersion")
    return {
        "path": rel(root, path),
        "central_package_management": properties.get("ManagePackageVersionsCentrally", "").lower() == "true",
        "package_versions": [
            {"include": str(item.get("Include", "")), "version": str(item.get("Version", ""))}
            for item in packages
            if item.get("Include")
        ][:80],
    }

def nuget_report(root: Path, files: list[Path]) -> dict[str, Any]:
    configs = [path for path in files if path.name.lower() == "nuget.config"]
    directory_packages = [path for path in files if path.name == "Directory.Packages.props"]
    parsed_configs = [parse_nuget_config(root, path) for path in sorted(configs, key=lambda item: rel(root, item))]
    package_management = [parse_directory_packages(root, path) for path in sorted(directory_packages, key=lambda item: rel(root, item))]
    all_sources = [
        {**source, "config_path": config.get("path", "")}
        for config in parsed_configs
        for source in config.get("sources", [])
        if isinstance(source, dict)
    ]
    private_sources = [source for source in all_sources if source.get("private")]
    mappings = [
        {**mapping, "config_path": config.get("path", "")}
        for config in parsed_configs
        for mapping in config.get("package_source_mapping", [])
        if isinstance(mapping, dict)
    ]
    credential_sections = sorted(
        {
            str(section)
            for config in parsed_configs
            for section in config.get("credential_sections", [])
            if isinstance(section, str)
        }
    )
    return {
        "config_paths": [str(config.get("path", "")) for config in parsed_configs],
        "sources": all_sources,
        "private_feeds_detected": bool(private_sources),
        "private_sources": private_sources,
        "package_source_mapping_present": bool(mappings),
        "package_source_mapping": mappings[:80],
        "credential_sections_present": credential_sections,
        "central_package_management": any(item.get("central_package_management") for item in package_management),
        "directory_packages": package_management,
        "global_config_skipped": True,
        "user_config_skipped": True,
        "policy": "Repo-local NuGet.config and Directory.Packages.props were inspected. User/global NuGet config was intentionally skipped, and credential values are not emitted.",
    }
