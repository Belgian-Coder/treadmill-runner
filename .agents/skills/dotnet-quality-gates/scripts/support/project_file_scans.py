"""Project-file scan helpers for validate_local_quality."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


PROJECT_REFERENCE_PATTERN = re.compile(
    r"<\s*ProjectReference\b[^>]*\bInclude\s*=\s*[\"'](?P<include>[^\"']+)[\"']",
    re.IGNORECASE,
)
PACKAGE_REFERENCE_PATTERN = re.compile(
    r"<\s*PackageReference\b[^>]*\bInclude\s*=\s*[\"'](?P<include>[^\"']+)[\"']",
    re.IGNORECASE,
)
PACKAGE_REFERENCE_TAG_PATTERN = re.compile(r"<\s*PackageReference\b(?P<attrs>[^>]*)>", re.IGNORECASE)
PACKAGE_INCLUDE_ATTR_PATTERN = re.compile(r"\bInclude\s*=\s*[\"'](?P<include>[^\"']+)[\"']", re.IGNORECASE)
PACKAGE_VERSION_ATTR_PATTERN = re.compile(r"\bVersion\s*=\s*[\"'](?P<version>[^\"']+)[\"']", re.IGNORECASE)
TARGET_FRAMEWORK_PATTERN = re.compile(r"<TargetFrameworks?>\s*([^<]+)</TargetFrameworks?>", re.IGNORECASE)
PROJECT_FILE_SUFFIXES = {".csproj", ".fsproj", ".vbproj", ".props", ".targets"}
PROJECT_ENTRY_SUFFIXES = {".csproj", ".fsproj", ".vbproj"}
DYNAMIC_REFERENCE_MARKERS = ("$(", "%(", "@(", "*", "?", ";")
WEB_SDK_PATTERN = re.compile(r"<\s*Project\b[^>]*\bSdk\s*=\s*[\"']Microsoft\.NET\.Sdk\.Web[\"']", re.IGNORECASE)
TEST_FRAMEWORK_PACKAGE_IDS = {
    "mstest.testframework",
    "nunit",
    "xunit",
    "xunit.v3",
}
TEST_SDK_PACKAGE_ID = "microsoft.net.test.sdk"
WEB_SDK_IMPLICIT_PACKAGE_IDS = {
    "microsoft.extensions.logging",
}
ASPNET_PACKAGE_IDS_MATCH_TFM_MAJOR = {
    "microsoft.aspnetcore.mvc.testing",
}


def line_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def line_snippet(text: str, line_number: int) -> str:
    lines = text.splitlines()
    if 1 <= line_number <= len(lines):
        return lines[line_number - 1].strip()[:240]
    return ""


def strip_xml_comments_preserve_offsets(text: str) -> str:
    def replace_comment(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group(0))

    return re.sub(r"<!--[\s\S]*?-->", replace_comment, text)


def resolve_reference(project_dir: Path, include: str) -> Path:
    reference = Path(include.replace("\\", "/"))
    if reference.is_absolute():
        return reference
    return (project_dir / reference).resolve()


def project_reference_path_findings(path: Path, text: str) -> list[dict[str, Any]]:
    if path.suffix.lower() not in PROJECT_FILE_SUFFIXES:
        return []
    code = strip_xml_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for match in PROJECT_REFERENCE_PATTERN.finditer(code):
        include = match.group("include").strip()
        if not include or any(marker in include for marker in DYNAMIC_REFERENCE_MARKERS):
            continue
        referenced_project = resolve_reference(path.parent, include)
        if referenced_project.exists():
            continue
        line_number = line_for_offset(text, match.start())
        findings.append(
            {
                "rule_id": "SW052",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "ProjectReference Include path does not resolve to an existing project file",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def package_reference_ids(text: str) -> list[tuple[str, int]]:
    code = strip_xml_comments_preserve_offsets(text)
    return [
        (match.group("include").strip().lower(), line_for_offset(code, match.start()))
        for match in PACKAGE_REFERENCE_PATTERN.finditer(code)
        if match.group("include").strip()
    ]


def target_framework_majors(text: str) -> set[int]:
    majors: set[int] = set()
    for match in TARGET_FRAMEWORK_PATTERN.finditer(strip_xml_comments_preserve_offsets(text)):
        for target in re.split(r"[;\s]+", match.group(1).strip()):
            version = re.match(r"net(\d+)\.0\b", target, re.IGNORECASE)
            if version:
                majors.add(int(version.group(1)))
    return majors


def package_major(version_text: str) -> int | None:
    match = re.match(r"\s*(\d+)", version_text)
    return int(match.group(1)) if match else None


def test_project_package_findings(path: Path, text: str) -> list[dict[str, Any]]:
    if path.suffix.lower() not in PROJECT_ENTRY_SUFFIXES:
        return []
    package_ids = package_reference_ids(text)
    package_id_set = {package_id for package_id, _line in package_ids}
    test_framework_lines = [
        line for package_id, line in package_ids if package_id in TEST_FRAMEWORK_PACKAGE_IDS
    ]
    if not test_framework_lines or TEST_SDK_PACKAGE_ID in package_id_set:
        return []
    line_number = test_framework_lines[0]
    return [
        {
            "rule_id": "SW053",
            "severity": "warning",
            "path": str(path),
            "line": line_number,
            "message": "test framework project is missing a Microsoft.NET.Test.Sdk PackageReference",
            "snippet": line_snippet(text, line_number),
        }
    ]


def web_sdk_shared_framework_package_findings(path: Path, text: str) -> list[dict[str, Any]]:
    if path.suffix.lower() not in PROJECT_ENTRY_SUFFIXES:
        return []
    code = strip_xml_comments_preserve_offsets(text)
    if WEB_SDK_PATTERN.search(code) is None:
        return []
    findings: list[dict[str, Any]] = []
    for package_id, line_number in package_reference_ids(text):
        if package_id not in WEB_SDK_IMPLICIT_PACKAGE_IDS:
            continue
        findings.append(
            {
                "rule_id": "SW056",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "Web SDK project explicitly references a package that is already in the ASP.NET Core shared framework",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def aspnet_package_tfm_mismatch_findings(path: Path, text: str) -> list[dict[str, Any]]:
    if path.suffix.lower() not in PROJECT_ENTRY_SUFFIXES:
        return []
    target_majors = target_framework_majors(text)
    if len(target_majors) != 1:
        return []
    expected_major = next(iter(target_majors))
    code = strip_xml_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for match in PACKAGE_REFERENCE_TAG_PATTERN.finditer(code):
        attrs = match.group("attrs")
        include_match = PACKAGE_INCLUDE_ATTR_PATTERN.search(attrs)
        version_match = PACKAGE_VERSION_ATTR_PATTERN.search(attrs)
        if include_match is None or version_match is None:
            continue
        package_id = include_match.group("include").strip().lower()
        actual_major = package_major(version_match.group("version"))
        if package_id not in ASPNET_PACKAGE_IDS_MATCH_TFM_MAJOR or actual_major is None or actual_major == expected_major:
            continue
        line_number = line_for_offset(text, match.start())
        findings.append(
            {
                "rule_id": "SW057",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "ASP.NET Core package major does not match target framework major",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def project_file_findings(path: Path, text: str) -> list[dict[str, Any]]:
    findings = project_reference_path_findings(path, text)
    findings.extend(test_project_package_findings(path, text))
    findings.extend(web_sdk_shared_framework_package_findings(path, text))
    findings.extend(aspnet_package_tfm_mismatch_findings(path, text))
    return findings
