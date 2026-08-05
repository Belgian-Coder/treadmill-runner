#!/usr/bin/env python3
"""Deterministic document inspection helpers for local AI routing."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any

import local_ai_routing
from local_ai_support import setup_impl as support

TEXT_SUFFIXES = {
    ".cs",
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".txt",
    ".ts",
    ".xml",
    ".yaml",
    ".yml",
}
OOXML_SUFFIXES = {".docx", ".xlsx", ".pptx"}
PDF_TEXT_RE = re.compile(rb"\(([^()\r\n]{4,200})\)\s*Tj|\[([^\]]{4,500})\]\s*TJ")


def inspect_text_file(path: Path) -> tuple[str, list[dict[str, Any]], list[str]]:
    try:
        text = path.read_bytes()[: support.MAX_DAILY_INPUT_BYTES].decode("utf-8")
    except UnicodeDecodeError:
        return "unsupported-binary", [], ["file extension looked textual but content is not UTF-8"]
    lines = text.splitlines()
    evidence = [{"kind": "text", "line_count": len(lines), "excerpt": " ".join(text.split())[:500]}]
    return "deterministic-text", evidence, []


def inspect_pdf(path: Path) -> tuple[str, list[dict[str, Any]], list[str]]:
    data = path.read_bytes()[: min(path.stat().st_size, 2_000_000)]
    matches: list[str] = []
    for match in PDF_TEXT_RE.finditer(data):
        raw = match.group(1) or match.group(2) or b""
        text = raw.decode("latin-1", errors="ignore").strip()
        if text:
            matches.append(text)
        if len(matches) >= 20:
            break
    evidence: list[dict[str, Any]] = [{"kind": "pdf", "bytes_sampled": len(data), "selectable_text_hits": len(matches)}]
    if matches:
        evidence.append({"kind": "selectable-text", "excerpt": " ".join(matches)[:500]})
        return "hybrid-pdf-text-first", evidence, []
    return (
        "rendered-page-vision",
        evidence,
        ["no selectable text detected in sampled PDF bytes; render pages and use vision for scanned/raster content"],
    )


def inspect_ooxml(path: Path) -> tuple[str, list[dict[str, Any]], list[str]]:
    evidence: list[dict[str, Any]] = []
    issues: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.namelist()
            unsafe = [
                name
                for name in entries
                if name.startswith("/") or ".." in Path(name).parts
            ]
            if unsafe:
                issues.append(f"OOXML archive contains unsafe paths: {', '.join(unsafe[:5])}")
            media = [name for name in entries if "/media/" in name]
            comments = [name for name in entries if "comments" in name.lower()]
            charts = [name for name in entries if "/charts/" in name]
            formulas = 0
            for name in entries:
                if path.suffix.lower() == ".xlsx" and name.startswith("xl/worksheets/") and name.endswith(".xml"):
                    try:
                        formulas += archive.read(name, pwd=None).count(b"<f")
                    except (OSError, RuntimeError, zipfile.BadZipFile):
                        continue
    except zipfile.BadZipFile:
        return "unsupported-binary", [], ["file has an Office extension but is not a valid OOXML ZIP archive"]
    evidence.append(
        {
            "kind": "ooxml",
            "entries": len(entries),
            "media_files": len(media),
            "comment_parts": len(comments),
            "chart_parts": len(charts),
            "formula_count": formulas,
        }
    )
    return "deterministic-ooxml-metadata", evidence, issues


def document_inspect_report(root: Path, *, file_path: str) -> dict[str, Any]:
    path = local_ai_routing.resolve_repo_request_path(root, file_path)
    if not path.exists() or not path.is_file():
        raise RuntimeError(f"document inspect input is not a file: {file_path}")
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        strategy, evidence, issues = inspect_text_file(path)
    elif suffix == ".pdf":
        strategy, evidence, issues = inspect_pdf(path)
    elif suffix in OOXML_SUFFIXES:
        strategy, evidence, issues = inspect_ooxml(path)
    else:
        strategy, evidence, issues = (
            "unsupported-binary",
            [{"kind": "file", "suffix": suffix or "<none>", "size_bytes": path.stat().st_size}],
            ["unsupported binary type; use a deterministic extractor or rendered-page workflow before local AI"],
        )
    summary = {
        "deterministic-text": "Text can be inspected deterministically without model calls.",
        "hybrid-pdf-text-first": "PDF has selectable text; inspect text first and render pages only for images/charts/scans.",
        "rendered-page-vision": "PDF appears scanned or raster-heavy; render pages then use vision if policy allows.",
        "deterministic-ooxml-metadata": "Office file can be inspected safely as OOXML metadata; rendering is needed for visual layout.",
        "unsupported-binary": "File type is unsupported by deterministic inspection.",
    }[strategy]
    report = support.stable_report(
        ok=strategy != "unsupported-binary",
        task="document-inspect",
        profile="deterministic",
        input_paths=[support.relative(root, path)],
        summary=summary,
        findings=[f"Strategy: {strategy}"],
        suggestions=[],
        evidence=evidence,
        cache_path="",
        issues=issues,
        strategy=strategy,
        strategy_order=["selectable-text", "rendered-page-vision"] if suffix == ".pdf" else [strategy],
    )
    return report


def print_document_inspect(root: Path, *, file_path: str, as_json: bool) -> int:
    report = document_inspect_report(root, file_path=file_path)
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("# Document Inspect")
        print()
        print(f"- Status: {'ok' if report['ok'] else 'fallback'}")
        print(f"- Strategy: {report['strategy']}")
        print(f"- Summary: {report['summary']}")
        if report["issues"]:
            print()
            print("## Issues")
            for issue in report["issues"]:
                print(f"- {issue}")
    return 0 if report["ok"] else 1
