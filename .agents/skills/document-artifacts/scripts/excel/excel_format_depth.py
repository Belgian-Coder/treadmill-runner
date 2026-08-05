#!/usr/bin/env python3

import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def xml_root(data):
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        return None


def xml_properties(data):
    root = xml_root(data)
    if root is None:
        return {}
    props = {}
    for node in root:
        name = local_name(node.tag)
        text = " ".join("".join(node.itertext()).split())
        if text:
            props[name] = text
    return props


def relationship_records(archive, names):
    records = []
    for name in names:
        if not name.endswith(".rels"):
            continue
        root = xml_root(archive.read(name))
        if root is None:
            continue
        for rel_node in root:
            if local_name(rel_node.tag) != "Relationship":
                continue
            records.append(
                {
                    "part": name,
                    "id": rel_node.attrib.get("Id", ""),
                    "type": rel_node.attrib.get("Type", ""),
                    "target": rel_node.attrib.get("Target", ""),
                    "target_mode": rel_node.attrib.get("TargetMode", ""),
                }
            )
    return records


def sanitize_asset_name(name, fallback):
    name = name.strip().replace("\\", "/").strip("/")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name.replace("/", "__")).strip("._")
    return safe or fallback


def ensure_output_child(output_dir, target):
    try:
        return target.resolve().is_relative_to(output_dir.resolve())
    except ValueError:
        return False


def asset_parts(names):
    prefixes = {
        "xl/media/": "media",
        "xl/embeddings/": "embedded-object",
        "xl/charts/": "chart",
        "xl/drawings/": "drawing",
    }
    assets = []
    for name in names:
        for prefix, kind in prefixes.items():
            if name.startswith(prefix):
                assets.append({"kind": kind, "part": name, "name": sanitize_asset_name(name, "asset.bin"), "extractable": True})
                break
    return assets


def write_asset_manifest(tools, report, output_dir, assets, force=False):
    manifest = {
        "schema_version": 1,
        "tool": report["tool"],
        "command": report["command"],
        "input_path": report["input_path"],
        "input_sha256": report["input_sha256"],
        "assets": assets,
    }
    manifest_path = output_dir / "asset-manifest.json"
    if not tools.enforce_output_path(report, None, manifest_path, force, "asset manifest"):
        return False
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    report["writes"].append(tools.rel(manifest_path))
    report["artifacts"].append({"kind": "asset-manifest", "path": tools.rel(manifest_path), "assets": len(assets)})
    return True


def validate_report_shape(tools, report):
    missing = [field for field in tools.REQUIRED_REPORT_FIELDS if field not in report]
    issues = []
    if missing:
        issues.append("missing required report fields: " + ", ".join(missing))
    for key in ["capabilities", "evidence", "findings", "warnings", "skipped", "issues", "artifacts"]:
        expected = dict if key == "capabilities" else list
        if key in report and not isinstance(report[key], expected):
            issues.append(f"field has wrong type: {key}")
    return issues


def append_shape_validation(tools, report):
    issues = validate_report_shape(tools, report)
    report["checks"].append({"name": "evidence-schema-shape", "ok": not issues})
    report["issues"].extend(issues)


def enforce_output_path(tools, report, input_path, output_path, force, purpose):
    if output_path is None:
        return True
    try:
        resolved_output = output_path.resolve()
    except OSError as exc:
        report["blocked"].append(f"{purpose} output path could not be resolved: {exc}")
        return False
    if input_path is not None and input_path.exists():
        resolved_input = input_path.resolve()
        if resolved_output == resolved_input or resolved_output.is_relative_to(resolved_input):
            report["blocked"].append(f"{purpose} output path must not be the input file or inside the input file path")
            return False
    if output_path.exists() and not force:
        report["blocked"].append(f"{purpose} output already exists; pass --force to overwrite: {tools.rel(output_path)}")
        return False
    return True


def report_digest(report):
    return {
        "command": report.get("command", ""),
        "ok": report.get("ok", False),
        "status": report.get("status", ""),
        "summary": report.get("summary", ""),
        "findings": report.get("findings", [])[:10],
        "warnings": report.get("warnings", [])[:10],
        "skipped": report.get("skipped", [])[:10],
        "issues": report.get("issues", [])[:10],
        "evidence_kinds": [item.get("kind", "") for item in report.get("evidence", []) if isinstance(item, dict)][:20],
    }


def write_report_file(tools, report, output_path, force=False):
    if not enforce_output_path(tools, report, None, output_path, force, "evidence report"):
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return True


def metadata_report(tools, path):
    report = tools.base_report("metadata", path)
    archive, error = tools.open_xlsx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return tools.finish(report, False, "blocked", tools.INPUT_XLSX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = tools.safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{tools.UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return tools.finish(report, False, "blocked", tools.XLSX_UNSAFE_PACKAGE)
        core = xml_properties(archive.read("docProps/core.xml")) if "docProps/core.xml" in names else {}
        app = xml_properties(archive.read("docProps/app.xml")) if "docProps/app.xml" in names else {}
        custom = xml_properties(archive.read("docProps/custom.xml")) if "docProps/custom.xml" in names else {}
        workbook_root = xml_root(archive.read("xl/workbook.xml")) if "xl/workbook.xml" in names else None
        calc_mode = ""
        workbook_protection = False
        defined_names = []
        if workbook_root is not None:
            calc_pr = workbook_root.find(f"{tools.MAIN_NS}calcPr")
            if calc_pr is not None:
                calc_mode = calc_pr.attrib.get("calcMode", "")
            workbook_protection = workbook_root.find(f"{tools.MAIN_NS}workbookProtection") is not None
            for defined in workbook_root.iter(f"{tools.MAIN_NS}definedName"):
                defined_names.append(defined.attrib.get("name", ""))
        sheets = tools.parse_sheets(archive, names)
        protected_sheets = [worksheet for worksheet in tools.worksheet_paths(names) if b"<sheetProtection" in archive.read(worksheet)]
        report["evidence"].append({"kind": "xlsx-metadata", "core": core, "app": app, "custom": custom, "calc_mode": calc_mode, "workbook_protection": workbook_protection, "protected_sheet_parts": protected_sheets, "defined_names": defined_names[:100], "sheets": sheets})
        if workbook_protection or protected_sheets:
            report["warnings"].append("Workbook or sheet protection markers detected.")
    return tools.finish(report, True, "passed", "XLSX metadata inspected with deterministic OOXML evidence.")


def links_report(tools, path):
    report = tools.base_report("links", path)
    archive, error = tools.open_xlsx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return tools.finish(report, False, "blocked", tools.INPUT_XLSX_OPEN_FAILED)
    assert archive is not None
    links = []
    with archive:
        names, unsafe = tools.safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{tools.UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return tools.finish(report, False, "blocked", tools.XLSX_UNSAFE_PACKAGE)
        for item in relationship_records(archive, names):
            if item["target_mode"].lower() == "external" or item["target"].lower().startswith(("http://", "https://", "file:", "\\\\")):
                links.append(item)
        for worksheet in tools.worksheet_paths(names):
            root = xml_root(archive.read(worksheet))
            if root is None:
                continue
            for hyperlink in root.iter(f"{tools.MAIN_NS}hyperlink"):
                links.append({"part": worksheet, "id": hyperlink.attrib.get(f"{tools.REL_NS}id", ""), "ref": hyperlink.attrib.get("ref", ""), "location": hyperlink.attrib.get("location", ""), "target": hyperlink.attrib.get("display", ""), "type": "worksheet-hyperlink", "target_mode": ""})
    if any(str(item.get("target", "")).lower().startswith(("file:", "\\\\")) for item in links):
        report["warnings"].append("File or UNC hyperlink target detected.")
    report["evidence"].append({"kind": "xlsx-links", "count": len(links), "links": links[:100]})
    return tools.finish(report, True, "passed", f"Found {len(links)} workbook link record(s).")


def outline_report(tools, path):
    report = tools.base_report("outline", path)
    archive, error = tools.open_xlsx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return tools.finish(report, False, "blocked", tools.INPUT_XLSX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = tools.safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{tools.UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return tools.finish(report, False, "blocked", tools.XLSX_UNSAFE_PACKAGE)
        sheets = tools.parse_sheets(archive, names)
        worksheets = tools.worksheet_paths(names)
        formulas = sum(archive.read(name).count(b"<f") for name in worksheets)
        tables = [name for name in names if name.startswith("xl/tables/")]
        charts = [name for name in names if name.startswith("xl/charts/")]
        pivots = [name for name in names if name.startswith("xl/pivotTables/")]
        defined_names = []
        workbook_root = xml_root(archive.read("xl/workbook.xml")) if "xl/workbook.xml" in names else None
        if workbook_root is not None:
            defined_names = [node.attrib.get("name", "") for node in workbook_root.iter(f"{tools.MAIN_NS}definedName")]
    report["evidence"].append({"kind": "xlsx-outline", "sheets": sheets, "worksheet_parts": worksheets, "table_parts": tables, "chart_parts": charts, "pivot_table_parts": pivots, "defined_names": defined_names, "formula_count": formulas})
    return tools.finish(report, True, "passed", f"Outlined {len(sheets)} sheet(s), {len(tables)} table part(s), and {formulas} formula cell(s).")


def accessibility_report(tools, path):
    report = tools.base_report("accessibility", path)
    archive, error = tools.open_xlsx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return tools.finish(report, False, "blocked", tools.INPUT_XLSX_OPEN_FAILED)
    assert archive is not None
    volatile_names = ("NOW", "TODAY", "RAND", "RANDBETWEEN", "OFFSET", "INDIRECT")
    with archive:
        names, unsafe = tools.safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{tools.UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return tools.finish(report, False, "blocked", tools.XLSX_UNSAFE_PACKAGE)
        sheets = tools.parse_sheets(archive, names)
        hidden_sheets = [sheet for sheet in sheets if sheet.get("state") != "visible"]
        protected_sheets = [worksheet for worksheet in tools.worksheet_paths(names) if b"<sheetProtection" in archive.read(worksheet)]
        merged_cells = sum(archive.read(worksheet).count(b"<mergeCell") for worksheet in tools.worksheet_paths(names))
        formula_items = tools.formulas_report(path)["evidence"][0].get("formulas", [])
        volatile = [item for item in formula_items if any(name + "(" in item.get("formula", "").upper() for name in volatile_names)]
        external_formula_refs = [item for item in formula_items if "[" in item.get("formula", "")]
        drawing_props = []
        missing_alt = []
        for drawing in (name for name in names if name.startswith("xl/drawings/") and name.endswith(".xml")):
            root = xml_root(archive.read(drawing))
            if root is None:
                continue
            for node in root.iter():
                if local_name(node.tag) in {"docPr", "cNvPr"}:
                    prop = {"part": drawing, "name": node.attrib.get("name", ""), "descr": node.attrib.get("descr", ""), "title": node.attrib.get("title", "")}
                    drawing_props.append(prop)
                    if not (node.attrib.get("descr") or node.attrib.get("title")):
                        missing_alt.append({"part": drawing, "name": node.attrib.get("name", "")})
        if hidden_sheets:
            report["warnings"].append("Hidden sheets detected.")
        if protected_sheets:
            report["warnings"].append("Protected sheet markers detected.")
        if volatile:
            report["warnings"].append("Volatile formula functions detected.")
        if external_formula_refs:
            report["warnings"].append("External workbook formula references detected.")
        if missing_alt:
            report["warnings"].append("Drawing/image properties without detectable alt text found.")
        report["evidence"].append({"kind": "xlsx-accessibility", "hidden_sheets": hidden_sheets, "protected_sheet_parts": protected_sheets, "merged_cells": merged_cells, "volatile_formulas": volatile[:50], "external_formula_references": external_formula_refs[:50], "drawing_properties": drawing_props[:50], "missing_alt_text": missing_alt[:50]})
    return tools.finish(report, True, "passed", "XLSX accessibility and review-risk checks completed.")


def extract_assets(tools, path, output_dir, write, force=False):
    report = tools.base_report("extract-assets", path)
    archive, error = tools.open_xlsx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return tools.finish(report, False, "blocked", tools.INPUT_XLSX_OPEN_FAILED)
    output_root = output_dir.resolve()
    if output_root == path.resolve():
        report["blocked"].append("output directory cannot be the input file path")
        return tools.finish(report, False, "blocked", "Asset extraction output path is invalid.")
    assert archive is not None
    with archive:
        names, unsafe = tools.safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{tools.UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return tools.finish(report, False, "blocked", tools.XLSX_UNSAFE_PACKAGE)
        assets = asset_parts(names)
        planned = []
        for asset in assets:
            target = output_root / asset["name"]
            if not ensure_output_child(output_root, target):
                report["blocked"].append(f"asset target escapes output directory: {target}")
            if write and target.exists() and not force:
                report["blocked"].append(f"asset target already exists; pass --force to overwrite: {tools.rel(target)}")
            planned.append({**asset, "path": tools.rel(target), "size_bytes": archive.getinfo(asset["part"]).file_size})
        report["evidence"].append({"kind": "xlsx-assets", "assets": planned})
        if report["blocked"]:
            return tools.finish(report, False, "blocked", "Asset output path containment failed.")
        if not write:
            return tools.finish(report, True, "planned", f"Found {len(assets)} XLSX asset part(s); no files were written.")
        output_root.mkdir(parents=True, exist_ok=True)
        written = []
        for asset in assets:
            target = output_root / asset["name"]
            target.write_bytes(archive.read(asset["part"]))
            record = {**asset, "path": tools.rel(target), "sha256": tools.file_sha256(target), "size_bytes": tools.file_size(target)}
            written.append(record)
            report["writes"].append(tools.rel(target))
            report["artifacts"].append({"kind": "extracted-asset", **record})
        if not write_asset_manifest(tools, report, output_root, written, force):
            return tools.finish(report, False, "blocked", "Asset manifest output path failed safety checks.")
    return tools.finish(report, True, "passed", f"Extracted {len(written)} XLSX asset file(s) and wrote an asset manifest.")


def next_safe_commands(tools, path, output_dir):
    input_arg = tools.rel(path)
    output_arg = tools.rel(output_dir)
    render_available = bool(tools.shutil.which("soffice") or tools.shutil.which("libreoffice"))
    commands = [
        f"python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py inspect --file {input_arg} --json",
        f"python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py to-markdown --file {input_arg} --output {output_arg}/content.md --write --json",
        f"python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py extract-assets --file {input_arg} --output-dir {output_arg}/assets --write --json",
        "python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py compare --before <before.xlsx> --after <after.xlsx> --json",
    ]
    render_command = f"python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py render --file {input_arg} --output-dir {output_arg}/rendered --write --json"
    commands.append(render_command if render_available else f"{render_command}  # renderer unavailable; run doctor for capability details")
    return {"kind": "next-safe-commands", "render_available": render_available, "commands": commands}


def bundle_evidence(tools, path, output_dir, write, force=False):
    report = tools.base_report("bundle-evidence", path)
    if write and hasattr(tools, "enforce_output_dir") and not tools.enforce_output_dir(report, [path], output_dir, "XLSX evidence bundle"):
        return tools.finish(report, False, "blocked", "XLSX evidence bundle output directory failed safety checks.")
    reports = {
        "inspect": tools.inspect_xlsx(path),
        "metadata": tools.metadata_report(path),
        "links": tools.links_report(path),
        "outline": tools.outline_report(path),
        "accessibility": tools.accessibility_report(path),
        "formulas": tools.formulas_report(path),
        "external-links": tools.external_links_report(path),
        "recalc-check": tools.recalc_check(path),
    }
    reports["to-markdown"] = tools.to_markdown(path, output_dir / "content.md" if write else None, force=force, include_metadata=True, include_links=True, include_outline=True, include_assets=True)
    reports["extract-assets"] = tools.extract_assets(path, output_dir / "assets", write=write, force=force)
    report["evidence"].append({"kind": "evidence-bundle", "reports": {name: report_digest(item) for name, item in reports.items()}})
    next_commands = next_safe_commands(tools, path, output_dir)
    report["evidence"].append(next_commands)
    report["findings"].extend(f"Next safe command: {command}" for command in next_commands["commands"])
    report["artifacts"].append({"kind": "evidence-bundle", "path": tools.rel(output_dir), "write_requested": write})
    report["warnings"].extend(item for nested in reports.values() for item in nested.get("warnings", []))
    report["skipped"].extend(item for nested in reports.values() for item in nested.get("skipped", []))
    if write:
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, nested in reports.items():
            target = output_dir / f"{name}.json"
            if not write_report_file(tools, nested, target, force):
                report["blocked"].extend(nested.get("blocked", []))
                return tools.finish(report, False, "blocked", "Evidence bundle report output path failed safety checks.")
            report["writes"].append(tools.rel(target))
        index_path = output_dir / "evidence-bundle.json"
        if not enforce_output_path(tools, report, None, index_path, force, "evidence bundle index"):
            return tools.finish(report, False, "blocked", "Evidence bundle index output path failed safety checks.")
        index_path.write_text(json.dumps({"schema_version": 1, "tool": report["tool"], "reports": {name: f"{name}.json" for name in reports}}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        report["writes"].append(tools.rel(index_path))
        report["artifacts"].append({"kind": "evidence-bundle-index", "path": tools.rel(index_path)})
    return tools.finish(report, True, "passed" if write else "planned", "XLSX evidence bundle " + ("written." if write else "planned; no files were written."))


def batch_evidence(tools, paths, output_dir, write, force=False):
    report = tools.base_report("batch")
    if write and hasattr(tools, "enforce_output_dir") and not tools.enforce_output_dir(report, paths, output_dir, "XLSX batch"):
        return tools.finish(report, False, "blocked", "XLSX batch output directory failed safety checks.")
    summaries = []
    for path in paths:
        child_dir = output_dir / re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._")
        child = bundle_evidence(tools, path, child_dir, write=write, force=force)
        summaries.append({"path": tools.rel(path), "output_dir": tools.rel(child_dir), "summary": report_digest(child)})
        report["warnings"].extend(child.get("warnings", []))
        report["skipped"].extend(child.get("skipped", []))
        if not child["ok"]:
            report["issues"].append(f"bundle failed for {tools.rel(path)}: {child['summary']}")
    report["evidence"].append({"kind": "batch-evidence", "inputs": [tools.rel(path) for path in paths], "items": summaries})
    if write:
        output_dir.mkdir(parents=True, exist_ok=True)
        index_path = output_dir / "batch-index.json"
        if not enforce_output_path(tools, report, None, index_path, force, "batch index"):
            return tools.finish(report, False, "blocked", "Batch index output path failed safety checks.")
        index_path.write_text(json.dumps({"schema_version": 1, "tool": report["tool"], "items": summaries}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        report["writes"].append(tools.rel(index_path))
        report["artifacts"].append({"kind": "batch-index", "path": tools.rel(index_path)})
    ok = not report["issues"]
    return tools.finish(report, ok, "passed" if write and ok else "planned" if ok else "failed", f"Processed {len(paths)} XLSX file(s) for batch evidence.")
