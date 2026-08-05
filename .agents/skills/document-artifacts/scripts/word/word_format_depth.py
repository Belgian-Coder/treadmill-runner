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
        "word/media/": "media",
        "word/embeddings/": "embedded-object",
        "word/charts/": "chart",
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


def metadata_report(tools, path):
    report = tools.base_report("metadata", path)
    archive, error = tools.open_docx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return tools.finish(report, False, "blocked", tools.INPUT_DOCX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = tools.safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{tools.UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return tools.finish(report, False, "blocked", tools.DOCX_UNSAFE_PACKAGE)
        core = xml_properties(archive.read("docProps/core.xml")) if "docProps/core.xml" in names else {}
        app = xml_properties(archive.read("docProps/app.xml")) if "docProps/app.xml" in names else {}
        custom = xml_properties(archive.read("docProps/custom.xml")) if "docProps/custom.xml" in names else {}
        settings = archive.read("word/settings.xml") if "word/settings.xml" in names else b""
        styles = archive.read("word/styles.xml") if "word/styles.xml" in names else b""
        protection = b"<w:documentProtection" in settings or b"documentProtection" in settings
        style_count = styles.count(b"<w:style") if styles else 0
        report["evidence"].append({"kind": "docx-metadata", "core": core, "app": app, "custom": custom, "style_count": style_count, "document_protection": protection})
        if protection:
            report["warnings"].append("Document protection marker detected.")
    return tools.finish(report, True, "passed", "DOCX metadata inspected with deterministic OOXML evidence.")


def links_report(tools, path):
    report = tools.base_report("links", path)
    archive, error = tools.open_docx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return tools.finish(report, False, "blocked", tools.INPUT_DOCX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = tools.safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{tools.UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return tools.finish(report, False, "blocked", tools.DOCX_UNSAFE_PACKAGE)
        relationships = relationship_records(archive, names)
        links = [
            item
            for item in relationships
            if item["target_mode"].lower() == "external"
            or item["target"].lower().startswith(("http://", "https://", "file:", "\\\\"))
            or "hyperlink" in item["type"].lower()
        ]
    if any(item["target"].lower().startswith(("file:", "\\\\")) for item in links):
        report["warnings"].append("File or UNC hyperlink target detected.")
    report["evidence"].append({"kind": "docx-links", "count": len(links), "links": links[:100]})
    return tools.finish(report, True, "passed", f"Found {len(links)} DOCX link relationship(s).")


def outline_report(tools, path):
    report = tools.base_report("outline", path)
    archive, error = tools.open_docx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return tools.finish(report, False, "blocked", tools.INPUT_DOCX_OPEN_FAILED)
    headings = []
    tables = 0
    assert archive is not None
    with archive:
        names, unsafe = tools.safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{tools.UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return tools.finish(report, False, "blocked", tools.DOCX_UNSAFE_PACKAGE)
        if "word/document.xml" not in names:
            report["blocked"].append("word/document.xml is missing")
            return tools.finish(report, False, "blocked", tools.DOCX_MISSING_DOCUMENT_TEXT)
        root = xml_root(archive.read("word/document.xml"))
        if root is not None:
            tables = sum(1 for _ in root.iter(f"{tools.W_NS}tbl"))
            for para_index, para in enumerate(root.iter(f"{tools.W_NS}p"), start=1):
                style = ""
                p_pr = para.find(f"{tools.W_NS}pPr")
                if p_pr is not None:
                    p_style = p_pr.find(f"{tools.W_NS}pStyle")
                    if p_style is not None:
                        style = p_style.attrib.get(f"{tools.W_NS}val", "") or p_style.attrib.get("val", "")
                text = "".join(node.text or "" for node in para.iter(f"{tools.W_NS}t")).strip()
                match = re.search(r"heading\s*(\d+)", style, flags=re.IGNORECASE)
                if text and match:
                    headings.append({"paragraph": para_index, "level": int(match.group(1)), "text": text})
    report["evidence"].append({"kind": "docx-outline", "headings": headings[:100], "tables": tables})
    return tools.finish(report, True, "passed", f"Found {len(headings)} heading(s) and {tables} table(s).")


def accessibility_report(tools, path):
    report = tools.base_report("accessibility", path)
    archive, error = tools.open_docx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return tools.finish(report, False, "blocked", tools.INPUT_DOCX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = tools.safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{tools.UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return tools.finish(report, False, "blocked", tools.DOCX_UNSAFE_PACKAGE)
        document = archive.read("word/document.xml") if "word/document.xml" in names else b""
        root = xml_root(document) if document else None
        doc_prs = [node for node in root.iter() if local_name(node.tag) == "docPr"] if root is not None else []
        images = [name for name in names if name.startswith("word/media/")]
        missing_alt = [node.attrib.get("name", "") for node in doc_prs if not (node.attrib.get("descr") or node.attrib.get("title"))]
        headings = outline_report(tools, path)["evidence"][0].get("headings", []) if document else []
        previous = 0
        heading_jumps = []
        for heading in headings:
            level = int(heading.get("level", 0))
            if previous and level > previous + 1:
                heading_jumps.append({"from": previous, "to": level, "text": heading.get("text", "")})
            previous = level
        if images and missing_alt:
            report["warnings"].append("Image/drawing markers found without detectable alt text on some drawing properties.")
        if heading_jumps:
            report["warnings"].append("Heading level jumps detected.")
        report["evidence"].append({"kind": "docx-accessibility", "images": len(images), "drawing_properties": len(doc_prs), "missing_alt_text": missing_alt[:50], "heading_jumps": heading_jumps[:20]})
    return tools.finish(report, True, "passed", "DOCX accessibility-style checks completed.")


def extract_assets(tools, path, output_dir, write, force=False):
    report = tools.base_report("extract-assets", path)
    archive, error = tools.open_docx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return tools.finish(report, False, "blocked", tools.INPUT_DOCX_OPEN_FAILED)
    output_root = output_dir.resolve()
    if output_root == path.resolve():
        report["blocked"].append("output directory cannot be the input file path")
        return tools.finish(report, False, "blocked", "Asset extraction output path is invalid.")
    assert archive is not None
    with archive:
        names, unsafe = tools.safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{tools.UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return tools.finish(report, False, "blocked", tools.DOCX_UNSAFE_PACKAGE)
        assets = asset_parts(names)
        planned = []
        for asset in assets:
            target = output_root / asset["name"]
            if not ensure_output_child(output_root, target):
                report["blocked"].append(f"asset target escapes output directory: {target}")
            if write and target.exists() and not force:
                report["blocked"].append(f"asset target already exists; pass --force to overwrite: {tools.rel(target)}")
            planned.append({**asset, "path": tools.rel(target), "size_bytes": archive.getinfo(asset["part"]).file_size})
        report["evidence"].append({"kind": "docx-assets", "assets": planned})
        if report["blocked"]:
            return tools.finish(report, False, "blocked", "Asset output path containment failed.")
        if not write:
            return tools.finish(report, True, "planned", f"Found {len(assets)} DOCX asset part(s); no files were written.")
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
    return tools.finish(report, True, "passed", f"Extracted {len(written)} DOCX asset file(s) and wrote an asset manifest.")
