#!/usr/bin/env python3

import argparse
import functools
import hashlib
import importlib
import json
import platform
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

import excel_format_depth

sys.dont_write_bytecode = True

SKILL_NAME = "document-artifacts"
REPO_ROOT = Path(__file__).resolve().parents[5]
LOCAL_DEPS = REPO_ROOT / ".agents" / ".deps" / SKILL_NAME
if LOCAL_DEPS.exists() and str(LOCAL_DEPS) not in sys.path:
    sys.path.insert(0, str(LOCAL_DEPS))

PYTHON_DEPENDENCIES = {
    "openpyxl": "openpyxl==3.1.5",
}

MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
MAX_ZIP_ENTRIES = 5000
MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
REQUIRED_REPORT_FIELDS = [
    "schema_version",
    "tool",
    "ok",
    "status",
    "command",
    "input_path",
    "input_sha256",
    "input_size_bytes",
    "format",
    "summary",
    "capabilities",
    "evidence",
    "findings",
    "warnings",
    "skipped",
    "issues",
    "artifacts",
]
INPUT_XLSX_OPEN_FAILED = "Input XLSX could not be opened."
XLSX_UNSAFE_PACKAGE = "XLSX package contains unsafe paths."
UNSAFE_OOXML_PREFIX = "unsafe OOXML paths: "
WRITE_OUTPUT_REQUIRED_FLAG = "--output is required with --write"
WRITE_OUTPUT_REQUIRED = "Writing requires an explicit output path."
XLSX_OUTPUT_SAFETY_FAILED = "XLSX output path failed safety checks."
OUTPUT_VERIFICATION_FAILED = "output verification failed"
WORKBOOK_WRITTEN_VERIFY_FAILED = "Workbook was written but output verification failed."


def rel(path):
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def install_python_deps(report):
    packages = list(PYTHON_DEPENDENCIES.values())
    LOCAL_DEPS.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-B", "-m", "pip", "install", "--target", str(LOCAL_DEPS), *packages]
    started = time.monotonic()
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300, check=False)
    if str(LOCAL_DEPS) not in sys.path:
        sys.path.insert(0, str(LOCAL_DEPS))
    importlib.invalidate_caches()
    report["commands"].append(command)
    report["evidence"].append(
        {
            "kind": "python-dependency-install",
            "target": rel(LOCAL_DEPS),
            "packages": packages,
            "returncode": result.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "output": result.stdout[-2000:],
        }
    )
    if result.returncode != 0:
        report["issues"].append("python dependency install failed")


def file_sha256(path):
    if path is None or not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_size(path):
    if path is None or not path.exists() or not path.is_file():
        return 0
    return path.stat().st_size


def detect_capabilities():
    try:
        __import__("openpyxl")
        openpyxl = True
    except Exception:
        openpyxl = False
    return {
        "python": True,
        "openpyxl": openpyxl,
        "soffice": shutil.which("soffice") is not None,
        "libreoffice": shutil.which("libreoffice") is not None,
    }


def base_report(command, path=None):
    return {
        "schema_version": 1,
        "tool": "document-artifacts",
        "command": command,
        "ok": False,
        "status": "blocked",
        "summary": "",
        "input_path": rel(path) if path else "",
        "input_sha256": file_sha256(path),
        "input_size_bytes": file_size(path),
        "format": "xlsx",
        "findings": [],
        "evidence": [],
        "checks": [],
        "warnings": [],
        "skipped": [],
        "blocked": [],
        "issues": [],
        "commands": [],
        "writes": [],
        "artifacts": [],
        "capabilities": detect_capabilities(),
    }


def finish(report, ok, status, summary):
    report["ok"] = ok
    report["status"] = status
    report["summary"] = summary
    return report


validate_report_shape = functools.partial(excel_format_depth.validate_report_shape, sys.modules[__name__])
append_shape_validation = functools.partial(excel_format_depth.append_shape_validation, sys.modules[__name__])
enforce_output_path = functools.partial(excel_format_depth.enforce_output_path, sys.modules[__name__])
report_digest = excel_format_depth.report_digest


def enforce_output_dir(report, input_paths, output_dir, purpose):
    if output_dir is None:
        return True
    try:
        resolved_output = output_dir.resolve()
    except OSError as exc:
        report["blocked"].append(f"{purpose} output directory could not be resolved: {exc}")
        return False
    for input_path in input_paths:
        if not input_path.exists():
            continue
        resolved_input = input_path.resolve()
        if resolved_output == resolved_input or resolved_output.is_relative_to(resolved_input):
            report["blocked"].append(f"{purpose} output directory must not be the input file or inside the input file path")
            return False
    if output_dir.exists() and not output_dir.is_dir():
        report["blocked"].append(f"{purpose} output directory is an existing file: {rel(output_dir)}")
        return False
    return True


def safe_names(archive):
    infos = archive.infolist()
    names = [info.filename for info in infos]
    unsafe = []
    if len(names) > MAX_ZIP_ENTRIES:
        unsafe.append(f"entry count exceeds limit: {len(names)} > {MAX_ZIP_ENTRIES}")
    total_uncompressed = sum(info.file_size for info in infos)
    if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
        unsafe.append(f"uncompressed size exceeds limit: {total_uncompressed} > {MAX_UNCOMPRESSED_BYTES}")
    seen = set()
    for info in infos:
        name = info.filename
        parts = PurePosixPath(name).parts
        if name.startswith("/") or ".." in parts:
            unsafe.append(name)
        if name in seen:
            unsafe.append(f"duplicate ZIP entry: {name}")
        seen.add(name)
        if info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO:
            unsafe.append(f"suspicious compression ratio: {name}")
    return names, unsafe


def relationship_warnings(archive, names):
    warnings = []
    target_re = re.compile(rb'Target="([^"]+)"')
    for name in names:
        if not name.endswith(".rels"):
            continue
        try:
            data = archive.read(name)
        except Exception:
            continue
        for raw_target in target_re.findall(data):
            target = raw_target.decode("utf-8", errors="ignore")
            lowered = target.lower()
            if lowered.startswith(("http://", "https://", "file:", "\\\\")) or ".." in PurePosixPath(target).parts:
                warnings.append(f"suspicious relationship target in {name}: {target}")
    return warnings[:20]


def active_content_markers(path, names):
    lowered = [name.lower() for name in names]
    return {
        "macro_extension": path.suffix.lower() in {".xlsm", ".xltm", ".xlam"},
        "vba_project": any(name.endswith("vbaproject.bin") for name in lowered),
        "active_x": any("activex/" in name for name in lowered),
        "ole_object": any("embeddings/" in name or name.endswith(".bin") for name in lowered),
        "external_links": any(name.startswith("xl/externallinks/") for name in lowered),
    }


def open_xlsx(path):
    if not path.exists() or not path.is_file():
        return None, {"blocked": "input XLSX does not exist"}
    try:
        return zipfile.ZipFile(path), None
    except zipfile.BadZipFile:
        return None, {"blocked": "file has .xlsx extension but is not a valid ZIP package"}


def parse_shared_strings(archive, names):
    if "xl/sharedStrings.xml" not in names:
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values = []
    for si in root.iter(f"{MAIN_NS}si"):
        values.append("".join(node.text or "" for node in si.iter(f"{MAIN_NS}t")))
    return values


def parse_sheets(archive, names):
    if "xl/workbook.xml" not in names:
        return []
    root = ET.fromstring(archive.read("xl/workbook.xml"))
    sheets = []
    for sheet in root.iter(f"{MAIN_NS}sheet"):
        sheets.append(
            {
                "name": sheet.attrib.get("name", ""),
                "state": sheet.attrib.get("state", "visible"),
                "relationship_id": sheet.attrib.get(f"{REL_NS}id", ""),
            }
        )
    return sheets


def worksheet_paths(names):
    return sorted(name for name in names if name.startswith("xl/worksheets/") and name.endswith(".xml"))


def cell_value(cell, shared_strings):
    value = cell.find(f"{MAIN_NS}v")
    if value is None or value.text is None:
        inline = "".join(node.text or "" for node in cell.iter(f"{MAIN_NS}t"))
        return inline
    if cell.attrib.get("t") == "s":
        try:
            return shared_strings[int(value.text)]
        except Exception:
            return value.text
    return value.text


metadata_report = lambda path: excel_format_depth.metadata_report(sys.modules[__name__], path)
links_report = lambda path: excel_format_depth.links_report(sys.modules[__name__], path)
outline_report = lambda path: excel_format_depth.outline_report(sys.modules[__name__], path)
accessibility_report = lambda path: excel_format_depth.accessibility_report(sys.modules[__name__], path)
extract_assets = lambda path, output_dir, write, force=False: excel_format_depth.extract_assets(sys.modules[__name__], path, output_dir, write, force)


def inspect_xlsx(path):
    report = base_report("inspect", path)
    archive, error = open_xlsx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return finish(report, False, "blocked", INPUT_XLSX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        report["checks"].append({"name": "safe-ooxml-paths", "ok": not unsafe})
        if unsafe:
            report["blocked"].append(f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return finish(report, False, "blocked", XLSX_UNSAFE_PACKAGE)
        report["warnings"].extend(relationship_warnings(archive, names))
        sheets = parse_sheets(archive, names)
        worksheets = worksheet_paths(names)
        formulas = sum(archive.read(name).count(b"<f") for name in worksheets)
        charts = [name for name in names if name.startswith("xl/charts/")]
        tables = [name for name in names if name.startswith("xl/tables/")]
        external = [name for name in names if name.startswith("xl/externalLinks/")]
        hidden = [sheet for sheet in sheets if sheet["state"] != "visible"]
        active = active_content_markers(path, names)
        report["evidence"].append(
            {
                "kind": "xlsx-package",
                "entries": len(names),
                "sheets": sheets,
                "worksheet_parts": len(worksheets),
                "formula_count": formulas,
                "table_parts": len(tables),
                "chart_parts": len(charts),
                "external_link_parts": len(external),
                "hidden_sheets": hidden,
                "active_content": active,
            }
        )
        report["findings"].append(f"Sheets: {len(sheets)}")
        if formulas:
            report["findings"].append(f"Formula cells detected: {formulas}")
        if external:
            report["findings"].append("External link parts detected.")
        if any(active.values()):
            report["warnings"].append("Active content, external link, or embedded object markers detected; review before opening in a desktop app.")
    return finish(report, True, "passed", "XLSX inspection completed with deterministic OOXML evidence.")


def doctor_report(install_deps=False):
    report = base_report("doctor")
    if install_deps:
        install_python_deps(report)
        report["capabilities"] = detect_capabilities()
    caps = report["capabilities"]
    report["evidence"].append(
        {
            "kind": "environment",
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "renderers": {
                "soffice": shutil.which("soffice") or "",
                "libreoffice": shutil.which("libreoffice") or "",
            },
        }
    )
    if not caps["openpyxl"]:
        report["skipped"].append("openpyxl unavailable: write-cells is blocked, but direct OOXML inspection remains available")
    if not (caps["soffice"] or caps["libreoffice"]):
        report["skipped"].append("no spreadsheet renderer found on PATH: render and formula recalculation evidence may be skipped")
    report["commands"].extend(
        [
            "python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py inspect --file <file.xlsx> --json",
            "python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py formulas --file <file.xlsx> --json",
            "python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py compare --before <old.xlsx> --after <new.xlsx> --json",
        ]
    )
    ok = not any("install failed" in issue for issue in report["issues"])
    return finish(report, ok, "passed" if ok else "failed", "Excel workbook helper readiness checked.")


def extract_tables(path):
    report = base_report("extract-tables", path)
    archive, error = open_xlsx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return finish(report, False, "blocked", INPUT_XLSX_OPEN_FAILED)
    tables = []
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return finish(report, False, "blocked", XLSX_UNSAFE_PACKAGE)
        shared = parse_shared_strings(archive, names)
        for worksheet in worksheet_paths(names)[:10]:
            root = ET.fromstring(archive.read(worksheet))
            rows = []
            for row in root.iter(f"{MAIN_NS}row"):
                values = [cell_value(cell, shared) for cell in row.iter(f"{MAIN_NS}c")]
                if values:
                    rows.append(values)
                if len(rows) >= 50:
                    break
            tables.append({"worksheet_part": worksheet, "rows": rows})
    report["evidence"].append({"kind": "tables", "tables": tables})
    return finish(report, True, "passed", f"Extracted table-like rows from {len(tables)} worksheet part(s).")


def markdown_table(rows, limit=50):
    rows = rows[:limit]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = normalized[0]
    lines = [
        "| " + " | ".join(cell.replace("|", "\\|") for cell in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in normalized[1:]:
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |")
    return "\n".join(lines)


def markdown_from_xlsx(
    path,
    max_sheets=10,
    max_rows=50,
    include_metadata=False,
    include_links=False,
    include_outline=False,
    include_assets=False,
):
    skipped = []
    warnings = []
    archive, error = open_xlsx(path)
    if error:
        return "", skipped, [error["blocked"]]
    sections = [f"# {path.name}", ""]
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        if unsafe:
            return "", skipped, [f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}"]
        warnings.extend(relationship_warnings(archive, names))
        shared = parse_shared_strings(archive, names)
        sheets = parse_sheets(archive, names)
        sections.append("## Sheets")
        for sheet in sheets:
            state = sheet.get("state", "visible")
            sections.append(f"- {sheet.get('name', '')} ({state})")
        worksheets = worksheet_paths(names)
        if len(worksheets) > max_sheets:
            skipped.append(f"markdown truncated after {max_sheets} worksheet part(s)")
        for index, worksheet in enumerate(worksheets[:max_sheets], start=1):
            root = ET.fromstring(archive.read(worksheet))
            rows = []
            formulas = []
            for row in root.iter(f"{MAIN_NS}row"):
                values = [cell_value(cell, shared) for cell in row.iter(f"{MAIN_NS}c")]
                if values:
                    rows.append(values)
                for cell in row.iter(f"{MAIN_NS}c"):
                    formula = cell.find(f"{MAIN_NS}f")
                    if formula is not None:
                        formulas.append(f"{cell.attrib.get('r', '')}: {formula.text or ''}")
                if len(rows) >= max_rows:
                    skipped.append(f"row extraction truncated at {max_rows} row(s) for {worksheet}")
                    break
            sections.append(f"\n## Worksheet {index}")
            table = markdown_table(rows)
            if table:
                sections.append(table)
            else:
                skipped.append(f"no table-like rows extracted from {worksheet}")
            if formulas:
                sections.append("\n### Formulas")
                sections.extend(f"- {item}" for item in formulas[:50])
        external = [name for name in names if name.startswith("xl/externalLinks/")]
        if external:
            warnings.append("workbook contains external link parts")
            sections.append("\n## External Links")
            sections.extend(f"- {name}" for name in external)
        if include_metadata:
            sections.extend(["", "## Metadata Evidence", "", "```json", json.dumps(report_digest(metadata_report(path)), indent=2, sort_keys=True), "```"])
        if include_links:
            sections.extend(["", "## Link Evidence", "", "```json", json.dumps(report_digest(links_report(path)), indent=2, sort_keys=True), "```"])
        if include_outline:
            sections.extend(["", "## Outline Evidence", "", "```json", json.dumps(report_digest(outline_report(path)), indent=2, sort_keys=True), "```"])
        if include_assets:
            sections.extend(["", "## Asset Evidence", "", "```json", json.dumps(report_digest(extract_assets(path, Path('_assets'), write=False)), indent=2, sort_keys=True), "```"])
    return "\n".join(sections).strip() + "\n", skipped, warnings


def to_markdown(
    path,
    output,
    force=False,
    max_sheets=10,
    max_rows=50,
    include_metadata=False,
    include_links=False,
    include_outline=False,
    include_assets=False,
    include_content=False,
):
    report = base_report("to-markdown", path)
    markdown, skipped, warnings = markdown_from_xlsx(path, max_sheets, max_rows, include_metadata, include_links, include_outline, include_assets)
    report["skipped"].extend(skipped)
    report["warnings"].extend(warnings)
    if not markdown.strip():
        report["blocked"].append("no markdown content could be produced")
        return finish(report, False, "blocked", "XLSX markdown conversion produced no content.")
    markdown_evidence = {"kind": "markdown", "characters": len(markdown), "excerpt": markdown[:800]}
    if include_content:
        markdown_evidence["content"] = markdown
    report["evidence"].append(markdown_evidence)
    if output is not None:
        if not enforce_output_path(report, path, output, force, "markdown"):
            return finish(report, False, "blocked", "Markdown output path failed safety checks.")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8", newline="\n")
        report["writes"].append(rel(output))
        report["artifacts"].append({"kind": "markdown-content", "path": rel(output)})
    elif not include_content:
        report["skipped"].append("no --output path was provided; only a compact markdown excerpt is in report evidence; pass --include-content for the full content")
    return finish(report, True, "passed", "Converted XLSX workbook evidence to Markdown.")


def formulas_report(path):
    report = base_report("formulas", path)
    archive, error = open_xlsx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return finish(report, False, "blocked", INPUT_XLSX_OPEN_FAILED)
    formulas = []
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return finish(report, False, "blocked", XLSX_UNSAFE_PACKAGE)
        for worksheet in worksheet_paths(names):
            root = ET.fromstring(archive.read(worksheet))
            for cell in root.iter(f"{MAIN_NS}c"):
                formula = cell.find(f"{MAIN_NS}f")
                if formula is not None:
                    formulas.append({"worksheet_part": worksheet, "cell": cell.attrib.get("r", ""), "formula": formula.text or ""})
    report["evidence"].append({"kind": "formulas", "count": len(formulas), "formulas": formulas[:100]})
    return finish(report, True, "passed", f"Found {len(formulas)} formula cell(s).")


def external_links_report(path):
    report = base_report("external-links", path)
    archive, error = open_xlsx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return finish(report, False, "blocked", INPUT_XLSX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return finish(report, False, "blocked", XLSX_UNSAFE_PACKAGE)
        links = [name for name in names if name.startswith("xl/externalLinks/")]
        rels = [name for name in names if "externalLink" in name and name.endswith(".rels")]
    report["evidence"].append({"kind": "external-links", "parts": links, "relationship_parts": rels})
    return finish(report, True, "passed", f"Found {len(links)} external link part(s).")


def parse_cell_assignment(text):
    match = re.match(r"([^!]+)!([A-Za-z]+\d+)=(.*)", text)
    if not match:
        raise ValueError(f"cell assignment must look like Sheet1!A1=value: {text}")
    return match.group(1), match.group(2).upper(), match.group(3)


def write_cells(path, cells, output, write, force=False, verify_output=False):
    report = base_report("write-cells", path)
    assignments = []
    try:
        for item in cells:
            sheet, cell, value = parse_cell_assignment(item)
            assignments.append({"sheet": sheet, "cell": cell, "value": value})
    except ValueError as exc:
        report["blocked"].append(str(exc))
        return finish(report, False, "blocked", "Invalid cell assignment.")
    report["evidence"].append({"kind": "write-plan", "assignments": assignments})
    if not write:
        return finish(report, True, "planned", "Cell write dry-run completed; no XLSX was written.")
    if output is None:
        report["blocked"].append(WRITE_OUTPUT_REQUIRED_FLAG)
        return finish(report, False, "blocked", WRITE_OUTPUT_REQUIRED)
    if not enforce_output_path(report, path, output, force, "XLSX"):
        return finish(report, False, "blocked", XLSX_OUTPUT_SAFETY_FAILED)
    try:
        import openpyxl
    except Exception:
        report["blocked"].append("openpyxl unavailable; cannot write workbook cells")
        return finish(report, False, "blocked", "Writing workbook cells requires openpyxl.")
    workbook = openpyxl.load_workbook(path)
    for item in assignments:
        if item["sheet"] not in workbook.sheetnames:
            report["blocked"].append(f"sheet not found: {item['sheet']}")
            return finish(report, False, "blocked", "Cannot write cells because a target sheet was missing.")
        workbook[item["sheet"]][item["cell"]] = item["value"]
    workbook.save(output)
    report["writes"].append(rel(output))
    if verify_output:
        verification = compare_xlsx(path, output)
        report["evidence"].append({"kind": "output-verification", "report": report_digest(verification)})
        if not verification["ok"]:
            report["issues"].append(OUTPUT_VERIFICATION_FAILED)
            return finish(report, False, "failed", WORKBOOK_WRITTEN_VERIFY_FAILED)
    return finish(report, True, "passed", "Workbook cell writes were saved to the explicit output path.")


def recalc_check(path):
    report = base_report("recalc-check", path)
    archive, error = open_xlsx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return finish(report, False, "blocked", INPUT_XLSX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return finish(report, False, "blocked", XLSX_UNSAFE_PACKAGE)
        has_calc_chain = "xl/calcChain.xml" in names
        formulas = sum(archive.read(name).count(b"<f") for name in worksheet_paths(names))
    renderer = shutil.which("soffice") or shutil.which("libreoffice")
    report["evidence"].append({"kind": "recalc", "formula_count": formulas, "calc_chain_present": has_calc_chain, "renderer_available": bool(renderer)})
    if formulas and not renderer:
        report["skipped"].append("formula cells exist but no Excel/LibreOffice recalculation path is available")
        return finish(report, False, "skipped", "Workbook has formulas; recalculation could not be verified.")
    return finish(report, True, "passed", "No recalculation blocker detected by deterministic checks.")


def render_workbook(path, output_dir, write):
    report = base_report("render", path)
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        report["skipped"].append("no supported XLSX renderer found on PATH: soffice or libreoffice")
        return finish(report, False, "skipped", "Rendering skipped because no supported renderer is available.")
    if not write:
        report["commands"].append([soffice, "--headless", "--convert-to", "pdf", "--outdir", "<output-dir>", str(path)])
        report["skipped"].append("render command planned only; pass --write with --output-dir to create a PDF")
        return finish(report, True, "planned", "Renderer is available; no files were written.")
    if output_dir is None:
        report["blocked"].append("--output-dir is required with --write")
        return finish(report, False, "blocked", "Rendering requires an explicit output directory.")
    if not enforce_output_dir(report, [path], output_dir, "render"):
        return finish(report, False, "blocked", "Rendering output directory failed safety checks.")
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(output_dir), str(path)]
    started = time.monotonic()
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60, check=False)
    report["commands"].append(command)
    report["evidence"].append({"kind": "renderer-output", "returncode": result.returncode, "duration_seconds": round(time.monotonic() - started, 3), "output": result.stdout[-1000:]})
    written = output_dir / f"{path.stem}.pdf"
    if written.exists():
        report["writes"].append(rel(written))
    return finish(report, result.returncode == 0, "passed" if result.returncode == 0 else "failed", "XLSX render command completed.")


def xlsx_metrics(path):
    inspect = inspect_xlsx(path)
    formulas = formulas_report(path)
    external = external_links_report(path)
    package = inspect["evidence"][0] if inspect.get("evidence") else {}
    formula_evidence = formulas["evidence"][0] if formulas.get("evidence") else {}
    external_evidence = external["evidence"][0] if external.get("evidence") else {}
    return {
        "path": rel(path),
        "sha256": file_sha256(path),
        "size_bytes": file_size(path),
        "ok": inspect["ok"],
        "status": inspect["status"],
        "sheets": package.get("sheets", []),
        "worksheet_parts": package.get("worksheet_parts", 0),
        "formula_count": formula_evidence.get("count", package.get("formula_count", 0)),
        "table_parts": package.get("table_parts", 0),
        "chart_parts": package.get("chart_parts", 0),
        "external_link_parts": len(external_evidence.get("parts", [])),
        "hidden_sheets": package.get("hidden_sheets", []),
        "warnings": inspect.get("warnings", []),
    }


def compare_xlsx(before, after):
    report = base_report("compare", before)
    report["evidence"].append({"kind": "compare-inputs", "before": rel(before), "after": rel(after), "after_sha256": file_sha256(after), "after_size_bytes": file_size(after)})
    if not before.exists() or not after.exists():
        report["blocked"].append("both --before and --after XLSX files must exist")
        return finish(report, False, "blocked", "Compare requires two existing XLSX files.")
    before_metrics = xlsx_metrics(before)
    after_metrics = xlsx_metrics(after)
    differences = {
        "sha256_changed": before_metrics["sha256"] != after_metrics["sha256"],
        "sheets_changed": before_metrics["sheets"] != after_metrics["sheets"],
        "formula_count_changed": before_metrics["formula_count"] != after_metrics["formula_count"],
        "external_links_changed": before_metrics["external_link_parts"] != after_metrics["external_link_parts"],
        "hidden_sheets_changed": before_metrics["hidden_sheets"] != after_metrics["hidden_sheets"],
        "tables_changed": before_metrics["table_parts"] != after_metrics["table_parts"],
    }
    report["evidence"].append({"kind": "xlsx-compare", "before": before_metrics, "after": after_metrics, "differences": differences})
    changed = [name for name, value in differences.items() if value]
    report["findings"].append("Changed: " + ", ".join(changed) if changed else "No deterministic XLSX differences detected.")
    return finish(report, True, "passed", "XLSX comparison completed with deterministic evidence.")


write_report_file = functools.partial(excel_format_depth.write_report_file, sys.modules[__name__])
bundle_evidence = functools.partial(excel_format_depth.bundle_evidence, sys.modules[__name__])
batch_evidence = functools.partial(excel_format_depth.batch_evidence, sys.modules[__name__])


def report_markdown(report):
    lines = [
        f"# {report['tool']} {report['command']}",
        "",
        f"- Status: {report['status']}",
        f"- OK: {report['ok']}",
        f"- Summary: {report['summary']}",
    ]
    if report.get("input_path"):
        lines.append(f"- Input: {report['input_path']}")
    for key in ["findings", "warnings", "skipped", "blocked", "issues"]:
        for item in report.get(key, []):
            lines.append(f"- {key[:-1].capitalize()}: {item}")
    return "\n".join(lines) + "\n"


def apply_strict(report, strict):
    if not strict:
        return report
    skipped_text = " ".join(str(item).lower() for item in report.get("skipped", []))
    strict_blocker = report.get("status") == "skipped" or any(term in skipped_text for term in ["fallback", "renderer", "render", "recalculation"])
    if strict_blocker:
        report["ok"] = False
        report["status"] = "failed"
        report["issues"].append("strict mode rejected skipped or fallback evidence")
    return report


def write_outputs(report, output_json, output_md, force=False):
    if output_json:
        report["artifacts"].append({"kind": "json-report", "path": rel(output_json)})
    if output_md:
        report["artifacts"].append({"kind": "markdown-report", "path": rel(output_md)})
    if output_json:
        if not enforce_output_path(report, None, output_json, force, "JSON report"):
            return
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    if output_md:
        if not enforce_output_path(report, None, output_md, force, "Markdown report"):
            return
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(report_markdown(report), encoding="utf-8", newline="\n")


def print_report(report, as_json, output_json=None, output_md=None, strict=False, force=False):
    append_shape_validation(report)
    report = apply_strict(report, strict)
    write_outputs(report, output_json, output_md, force)
    if report.get("blocked") and report.get("ok"):
        report["ok"] = False
        report["status"] = "blocked"
        report["summary"] = "Output path safety checks blocked report writing."
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(report_markdown(report), end="")
    return 0 if report["ok"] else 1


def add_report_options(parser):
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--force", action="store_true", help="Allow overwriting explicit report or output files.")


def build_parser():
    parser = argparse.ArgumentParser(description="Excel workbook helpers")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--install-python-deps", action="store_true")
    add_report_options(doctor)
    for name in ["inspect", "extract-tables", "formulas", "external-links", "recalc-check", "metadata", "links", "outline", "accessibility"]:
        child = sub.add_parser(name)
        child.add_argument("--file", required=True, type=Path)
        add_report_options(child)
    compare = sub.add_parser("compare")
    compare.add_argument("--before", required=True, type=Path)
    compare.add_argument("--after", required=True, type=Path)
    add_report_options(compare)
    markdown = sub.add_parser("to-markdown")
    markdown.add_argument("--file", required=True, type=Path)
    markdown.add_argument("--output", type=Path)
    markdown.add_argument("--max-sheets", type=int, default=10)
    markdown.add_argument("--max-rows", type=int, default=50)
    markdown.add_argument("--include-metadata", action="store_true")
    markdown.add_argument("--include-links", action="store_true")
    markdown.add_argument("--include-outline", action="store_true")
    markdown.add_argument("--include-assets", action="store_true")
    markdown.add_argument("--include-content", action="store_true", help="Embed the complete converted Markdown in report evidence; use --json or --output-json to emit it.")
    add_report_options(markdown)
    write = sub.add_parser("write-cells")
    write.add_argument("--file", required=True, type=Path)
    write.add_argument("--cell", action="append", required=True)
    write.add_argument("--output", type=Path)
    write.add_argument("--verify-output", action="store_true")
    mode = write.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--write", action="store_true")
    add_report_options(write)
    render = sub.add_parser("render")
    render.add_argument("--file", required=True, type=Path)
    render.add_argument("--output-dir", type=Path)
    render.add_argument("--write", action="store_true")
    add_report_options(render)
    assets = sub.add_parser("extract-assets")
    assets.add_argument("--file", required=True, type=Path)
    assets.add_argument("--output-dir", required=True, type=Path)
    assets.add_argument("--write", action="store_true")
    add_report_options(assets)
    bundle = sub.add_parser("bundle-evidence")
    bundle.add_argument("--file", required=True, type=Path)
    bundle.add_argument("--output-dir", required=True, type=Path)
    bundle.add_argument("--write", action="store_true")
    add_report_options(bundle)
    batch = sub.add_parser("batch")
    batch.add_argument("--paths", nargs="+", required=True, type=Path)
    batch.add_argument("--output-dir", required=True, type=Path)
    batch.add_argument("--write", action="store_true")
    add_report_options(batch)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    opts = {"as_json": args.json, "output_json": getattr(args, "output_json", None), "output_md": getattr(args, "output_md", None), "strict": getattr(args, "strict", False), "force": getattr(args, "force", False)}
    if args.command == "doctor":
        return print_report(doctor_report(args.install_python_deps), **opts)
    if args.command == "inspect":
        return print_report(inspect_xlsx(args.file), **opts)
    if args.command == "extract-tables":
        return print_report(extract_tables(args.file), **opts)
    if args.command == "formulas":
        return print_report(formulas_report(args.file), **opts)
    if args.command == "external-links":
        return print_report(external_links_report(args.file), **opts)
    if args.command == "recalc-check":
        return print_report(recalc_check(args.file), **opts)
    if args.command == "metadata":
        return print_report(metadata_report(args.file), **opts)
    if args.command == "links":
        return print_report(links_report(args.file), **opts)
    if args.command == "outline":
        return print_report(outline_report(args.file), **opts)
    if args.command == "accessibility":
        return print_report(accessibility_report(args.file), **opts)
    if args.command == "compare":
        return print_report(compare_xlsx(args.before, args.after), **opts)
    if args.command == "to-markdown":
        return print_report(to_markdown(args.file, args.output, args.force, args.max_sheets, args.max_rows, args.include_metadata, args.include_links, args.include_outline, args.include_assets, args.include_content), **opts)
    if args.command == "write-cells":
        return print_report(write_cells(args.file, args.cell, args.output, args.write, args.force, args.verify_output), **opts)
    if args.command == "render":
        return print_report(render_workbook(args.file, args.output_dir, args.write), **opts)
    if args.command == "extract-assets":
        return print_report(extract_assets(args.file, args.output_dir, args.write, args.force), **opts)
    if args.command == "bundle-evidence":
        return print_report(bundle_evidence(args.file, args.output_dir, args.write, args.force), **opts)
    if args.command == "batch":
        return print_report(batch_evidence(args.paths, args.output_dir, args.write, args.force), **opts)
    raise AssertionError(args)


if __name__ == "__main__":
    raise SystemExit(main())
