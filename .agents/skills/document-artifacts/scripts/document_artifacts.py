#!/usr/bin/env python3

import json
import subprocess
import sys
from pathlib import Path

FORMAT_SPECS = [
    {
        "id": "excel",
        "aliases": ["xlsx"],
        "extensions": [".xlsx"],
        "entry": "excel/excel_tools.py",
        "operations": ["doctor", "inspect", "extract-tables", "formulas", "external-links", "recalc-check", "metadata", "links", "outline", "accessibility", "compare", "to-markdown", "write-cells", "render", "extract-assets", "bundle-evidence", "batch"],
    },
    {
        "id": "word",
        "aliases": ["docx"],
        "extensions": [".docx"],
        "entry": "word/word_tools.py",
        "operations": ["doctor", "inspect", "extract-markdown", "comments", "tracked-changes", "metadata", "links", "outline", "accessibility", "compare", "to-markdown", "replace-text", "render", "extract-assets", "bundle-evidence", "batch"],
    },
    {
        "id": "powerpoint",
        "aliases": ["pptx"],
        "extensions": [".pptx"],
        "entry": "powerpoint/powerpoint_tools.py",
        "operations": ["doctor", "inspect", "extract-text", "inventory", "metadata", "links", "outline", "accessibility", "compare", "to-markdown", "replace-text", "rearrange", "render", "extract-assets", "bundle-evidence", "batch"],
    },
    {
        "id": "pdf",
        "aliases": ["pdf-workflow"],
        "extensions": [".pdf"],
        "entry": "pdf/pdf_tools.py",
        "operations": ["doctor", "inspect", "extract-text", "validate", "metadata", "links", "outline", "accessibility", "compare", "to-markdown", "render-pages", "extract-assets", "bundle-evidence", "batch", "forms"],
    },
    {
        "id": "markdown",
        "aliases": ["md"],
        "extensions": [".md", ".markdown"],
        "entry": "markdown/markdown_tools.py",
        "operations": ["scan"],
    },
]

TOOLS = {
    name: Path(spec["entry"])
    for spec in FORMAT_SPECS
    for name in [spec["id"], *spec["aliases"]]
}


def formats_report():
    return {
        "schema_version": 1,
        "tool": "document-artifacts.formats",
        "status": "passed",
        "portable_dispatcher": True,
        "inventory_kind": "static-command-contract",
        "runtime_availability": "not-probed",
        "availability_guidance": "For formats that advertise doctor, run that format's doctor --json command before optional rendering or write operations. Markdown scan is stdlib-only and has no doctor command.",
        "formats": [
            {key: value for key, value in spec.items() if key != "entry"}
            for spec in FORMAT_SPECS
        ],
    }


def print_formats(argv):
    if argv not in ([], ["--json"]):
        print("usage: document_artifacts.py formats [--json]", file=sys.stderr)
        return 2
    report = formats_report()
    if argv == ["--json"]:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for spec in report["formats"]:
            aliases = f" (aliases: {', '.join(spec['aliases'])})" if spec["aliases"] else ""
            print(f"{spec['id']}{aliases}: {', '.join(spec['extensions'])}")
    return 0


def main(argv):
    if not argv or argv[0] in {"-h", "--help"}:
        formats = ", ".join(sorted(TOOLS))
        print(
            "usage: document_artifacts.py <format> <tool-args>\n"
            "       document_artifacts.py formats [--json]\n"
            f"formats: {formats}"
        )
        return 0
    fmt = argv[0].lower()
    if fmt == "formats":
        return print_formats(argv[1:])
    target = TOOLS.get(fmt)
    if target is None:
        print(f"Unknown document format: {argv[0]}", file=sys.stderr)
        return 2
    script = Path(__file__).resolve().parent / target
    return subprocess.call([sys.executable, "-B", str(script), *argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
