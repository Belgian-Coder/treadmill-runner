#!/usr/bin/env python3
"""Merge Cobertura coverage XML files into compact evidence."""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def coverage_format(path: Path) -> str:
    tree = ET.parse(path)
    root = tree.getroot()
    tag = root.tag.lower()
    if tag.endswith("coverage"):
        if root.find(".//{*}class") is not None:
            return "cobertura"
        if root.find(".//{*}File") is not None or root.find(".//{*}SequencePoint") is not None:
            return "opencover"
    if root.find(".//{*}SequencePoint") is not None:
        return "opencover"
    return "unknown"


def parse_cobertura(path: Path) -> dict[tuple[str, int], int]:
    tree = ET.parse(path)
    root = tree.getroot()
    coverage: dict[tuple[str, int], int] = {}
    for class_node in root.findall(".//{*}class"):
        filename = class_node.attrib.get("filename") or class_node.attrib.get("name") or "unknown"
        for line in class_node.findall(".//{*}line"):
            number = int(line.attrib.get("number", "0"))
            hits = int(float(line.attrib.get("hits", "0")))
            key = (filename, number)
            coverage[key] = max(coverage.get(key, 0), hits)
    return coverage


def parse_opencover(path: Path) -> dict[tuple[str, int], int]:
    tree = ET.parse(path)
    root = tree.getroot()
    file_map = {
        item.attrib.get("uid"): item.attrib.get("fullPath", "unknown")
        for item in root.findall(".//{*}File")
        if item.attrib.get("uid")
    }
    coverage: dict[tuple[str, int], int] = {}
    for point in root.findall(".//{*}SequencePoint"):
        file_id = point.attrib.get("fileid")
        filename = file_map.get(file_id, f"fileid:{file_id or 'unknown'}")
        number = int(point.attrib.get("sl", "0"))
        visits = int(float(point.attrib.get("vc", "0")))
        coverage[(filename, number)] = max(coverage.get((filename, number), 0), visits)
    return coverage


def parse_coverage(path: Path) -> tuple[str, dict[tuple[str, int], int]]:
    fmt = coverage_format(path)
    if fmt == "cobertura":
        return fmt, parse_cobertura(path)
    if fmt == "opencover":
        return fmt, parse_opencover(path)
    raise ValueError(f"unsupported coverage XML format: {path}")


def split_target_frameworks(value: str) -> list[str]:
    return [part.strip() for part in re_split_semicolon(value) if part.strip()]


def re_split_semicolon(value: str) -> list[str]:
    return value.replace(",", ";").split(";")


def csproj_text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def scan_dotnet_project(path: Path) -> dict[str, object]:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return {"path": str(path), "ok": False, "error": str(exc)}
    target_frameworks: list[str] = []
    for node in root.findall(".//{*}TargetFramework"):
        target_frameworks.extend(split_target_frameworks(csproj_text(node)))
    for node in root.findall(".//{*}TargetFrameworks"):
        target_frameworks.extend(split_target_frameworks(csproj_text(node)))
    package_refs = [
        str(node.attrib.get("Include") or node.attrib.get("Update") or "")
        for node in root.findall(".//{*}PackageReference")
        if node.attrib.get("Include") or node.attrib.get("Update")
    ]
    is_test = any(
        item.lower() in {"microsoft.net.test.sdk", "xunit", "nunit", "mstest.testframework"}
        for item in package_refs
    ) or path.name.lower().endswith(".tests.csproj")
    uses_playwright = any("playwright" in item.lower() for item in package_refs) or "playwright" in path.as_posix().lower()
    uses_mtp = any("microsoft.testing.platform" in item.lower() for item in package_refs)
    return {
        "path": str(path),
        "ok": True,
        "target_frameworks": sorted(set(target_frameworks)),
        "is_test_project": is_test,
        "uses_playwright": uses_playwright,
        "uses_mtp": uses_mtp,
        "package_references": sorted(set(package_refs)),
    }


def discover_dotnet_targets(project_root: Path) -> dict[str, object]:
    root = project_root.expanduser().resolve()
    projects = [scan_dotnet_project(path) for path in sorted(root.rglob("*.csproj")) if "bin" not in path.parts and "obj" not in path.parts]
    solutions = [str(path) for path in sorted(root.rglob("*.sln")) if "bin" not in path.parts and "obj" not in path.parts]
    runsettings = [str(path) for path in sorted(root.rglob("*.runsettings")) if "bin" not in path.parts and "obj" not in path.parts]
    test_projects = [item for item in projects if item.get("is_test_project")]
    return {
        "project_root": str(root),
        "solutions": solutions,
        "runsettings": runsettings,
        "projects": projects,
        "summary": {
            "project_count": len(projects),
            "test_project_count": len(test_projects),
            "playwright_project_count": sum(1 for item in projects if item.get("uses_playwright")),
            "mtp_project_count": sum(1 for item in projects if item.get("uses_mtp")),
            "target_frameworks": sorted({tf for item in projects for tf in item.get("target_frameworks", [])}),
        },
    }


def summarize(inputs: list[Path], project_root: Path | None = None) -> dict[str, object]:
    merged: dict[tuple[str, int], int] = {}
    formats: dict[str, int] = {}
    for path in inputs:
        fmt, rows = parse_coverage(path)
        formats[fmt] = formats.get(fmt, 0) + 1
        for key, hits in rows.items():
            merged[key] = max(merged.get(key, 0), hits)
    total = len(merged)
    covered = sum(1 for hits in merged.values() if hits > 0)
    percent = round((covered / total) * 100, 2) if total else 0.0
    files: dict[str, dict[str, int]] = {}
    for (filename, _line), hits in merged.items():
        bucket = files.setdefault(filename, {"lines": 0, "covered": 0})
        bucket["lines"] += 1
        if hits > 0:
            bucket["covered"] += 1
    payload = {
        "ok": total > 0,
        "inputs": [str(path) for path in inputs],
        "lines": total,
        "covered_lines": covered,
        "coverage_percent": percent,
        "formats": formats,
        "files": files,
    }
    if project_root is not None:
        payload["dotnet_targets"] = discover_dotnet_targets(project_root)
    return payload


def write_markdown(path: Path, summary: dict[str, object]) -> None:
    lines = [
        "# Coverage Summary",
        "",
        f"- Lines: {summary['lines']}",
        f"- Covered lines: {summary['covered_lines']}",
        f"- Coverage: {summary['coverage_percent']}%",
        "",
        "## Files",
        "",
    ]
    for filename, item in sorted(summary["files"].items()):
        percent = round((item["covered"] / item["lines"]) * 100, 2) if item["lines"] else 0
        lines.append(f"- `{filename}`: {item['covered']}/{item['lines']} lines ({percent}%)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_generic_xml(path: Path, summary: dict[str, object]) -> None:
    root = ET.Element("coverage", attrib={"version": "1"})
    for filename, item in sorted(summary["files"].items()):
        file_node = ET.SubElement(root, "file", attrib={"path": filename})
        ET.SubElement(
            file_node,
            "summary",
            attrib={"linesToCover": str(item["lines"]), "coveredLines": str(item["covered"])},
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append")
    parser.add_argument("--project-root", help="scan .NET project/test/coverage settings without requiring test execution")
    parser.add_argument("--list-projects-only", action="store_true", help="only emit discovered .NET targets")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--output-generic-xml")
    parser.add_argument("--minimum", type=float)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.list_projects_only:
            if not args.project_root:
                raise ValueError("--list-projects-only requires --project-root")
            summary = {"ok": True, "dotnet_targets": discover_dotnet_targets(Path(args.project_root))}
        else:
            if not args.input:
                raise ValueError("--input is required unless --list-projects-only is used")
            inputs = [Path(item) for item in args.input]
            summary = summarize(inputs, project_root=Path(args.project_root) if args.project_root else None)
        if args.minimum is not None and summary["coverage_percent"] < args.minimum:
            summary["ok"] = False
            summary["minimum"] = args.minimum
        if args.output_json:
            path = Path(args.output_json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        if args.output_md:
            write_markdown(Path(args.output_md), summary)
        if args.output_generic_xml:
            write_generic_xml(Path(args.output_generic_xml), summary)
    except Exception as exc:
        if args.format == "json":
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(summary, indent=2))
    elif "coverage_percent" in summary:
        print(f"coverage {summary['coverage_percent']}% ({summary['covered_lines']}/{summary['lines']} lines)")
    else:
        print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
