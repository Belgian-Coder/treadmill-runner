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
        "ppt/media/": "media",
        "ppt/embeddings/": "embedded-object",
        "ppt/charts/": "chart",
        "ppt/diagrams/": "diagram",
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
    archive, error = tools.open_pptx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return tools.finish(report, False, "blocked", tools.INPUT_PPTX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = tools.safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{tools.UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return tools.finish(report, False, "blocked", tools.PPTX_UNSAFE_PACKAGE)
        core = xml_properties(archive.read("docProps/core.xml")) if "docProps/core.xml" in names else {}
        app = xml_properties(archive.read("docProps/app.xml")) if "docProps/app.xml" in names else {}
        custom = xml_properties(archive.read("docProps/custom.xml")) if "docProps/custom.xml" in names else {}
        presentation = xml_root(archive.read("ppt/presentation.xml")) if "ppt/presentation.xml" in names else None
        slide_size = {}
        sections = []
        if presentation is not None:
            sld_size = presentation.find(f"{tools.P_NS}sldSz")
            if sld_size is not None:
                slide_size = {"cx": sld_size.attrib.get("cx", ""), "cy": sld_size.attrib.get("cy", ""), "type": sld_size.attrib.get("type", "")}
            sections = [node.attrib.get("name", "") for node in presentation.iter(f"{tools.P_NS}section") if node.attrib.get("name")]
        report["evidence"].append({"kind": "pptx-metadata", "core": core, "app": app, "custom": custom, "slide_size": slide_size, "sections": sections, "masters": len([name for name in names if name.startswith("ppt/slideMasters/")]), "layouts": len([name for name in names if name.startswith("ppt/slideLayouts/")]), "themes": len([name for name in names if name.startswith("ppt/theme/")])})
    return tools.finish(report, True, "passed", "PPTX metadata inspected with deterministic OOXML evidence.")


def links_report(tools, path):
    report = tools.base_report("links", path)
    archive, error = tools.open_pptx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return tools.finish(report, False, "blocked", tools.INPUT_PPTX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = tools.safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{tools.UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return tools.finish(report, False, "blocked", tools.PPTX_UNSAFE_PACKAGE)
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
    report["evidence"].append({"kind": "pptx-links", "count": len(links), "links": links[:100]})
    return tools.finish(report, True, "passed", f"Found {len(links)} deck link relationship(s).")


def outline_report(tools, path):
    report = tools.base_report("outline", path)
    archive, error = tools.open_pptx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return tools.finish(report, False, "blocked", tools.INPUT_PPTX_OPEN_FAILED)
    slides = []
    notes = []
    assert archive is not None
    with archive:
        names, unsafe = tools.safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{tools.UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return tools.finish(report, False, "blocked", tools.PPTX_UNSAFE_PACKAGE)
        hidden_count = archive.read("ppt/presentation.xml").count(b'show="0"') if "ppt/presentation.xml" in names else 0
        for index, name in enumerate(tools.slide_paths(names), start=1):
            values = tools.xml_texts(archive.read(name))
            slides.append({"slide": index, "part": name, "title": values[0] if values else "", "text_items": len(values), "excerpt": " | ".join(values)[:300]})
        for name in sorted(n for n in names if n.startswith("ppt/notesSlides/")):
            values = tools.xml_texts(archive.read(name))
            notes.append({"part": name, "text_items": len(values), "excerpt": " | ".join(values)[:300]})
    report["evidence"].append({"kind": "pptx-outline", "slides": slides, "notes": notes, "hidden_slides": hidden_count})
    return tools.finish(report, True, "passed", f"Outlined {len(slides)} slide(s) and {len(notes)} notes part(s).")


def accessibility_report(tools, path):
    report = tools.base_report("accessibility", path)
    archive, error = tools.open_pptx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return tools.finish(report, False, "blocked", tools.INPUT_PPTX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = tools.safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{tools.UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return tools.finish(report, False, "blocked", tools.PPTX_UNSAFE_PACKAGE)
        slides = []
        drawing_props = []
        missing_alt = []
        for index, name in enumerate(tools.slide_paths(names), start=1):
            data = archive.read(name)
            values = tools.xml_texts(data)
            root = xml_root(data)
            if root is not None:
                for node in root.iter():
                    if local_name(node.tag) in {"cNvPr", "docPr"}:
                        prop = {"slide": index, "part": name, "name": node.attrib.get("name", ""), "descr": node.attrib.get("descr", ""), "title": node.attrib.get("title", "")}
                        drawing_props.append(prop)
                        if not (prop["descr"] or prop["title"]) and prop["name"]:
                            missing_alt.append(prop)
            slides.append({"slide": index, "title": values[0] if values else "", "text_items": len(values)})
        hidden_count = archive.read("ppt/presentation.xml").count(b'show="0"') if "ppt/presentation.xml" in names else 0
        media = [name for name in names if name.startswith("ppt/media/")]
        missing_titles = [slide for slide in slides if not slide["title"]]
        if missing_titles:
            report["warnings"].append("Slides without detectable title text found.")
        if media and missing_alt:
            report["warnings"].append("Media/drawing properties without detectable alt text found.")
        if hidden_count:
            report["warnings"].append("Hidden slide markers detected.")
        report["evidence"].append({"kind": "pptx-accessibility", "slides": slides, "hidden_slides": hidden_count, "media_files": len(media), "drawing_properties": drawing_props[:100], "missing_alt_text": missing_alt[:50], "missing_titles": missing_titles[:50]})
    return tools.finish(report, True, "passed", "PPTX accessibility-style checks completed.")


def extract_assets(tools, path, output_dir, write, force=False):
    report = tools.base_report("extract-assets", path)
    archive, error = tools.open_pptx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return tools.finish(report, False, "blocked", tools.INPUT_PPTX_OPEN_FAILED)
    output_root = output_dir.resolve()
    if output_root == path.resolve():
        report["blocked"].append("output directory cannot be the input file path")
        return tools.finish(report, False, "blocked", "Asset extraction output path is invalid.")
    assert archive is not None
    with archive:
        names, unsafe = tools.safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{tools.UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return tools.finish(report, False, "blocked", tools.PPTX_UNSAFE_PACKAGE)
        assets = asset_parts(names)
        planned = []
        for asset in assets:
            target = output_root / asset["name"]
            if not ensure_output_child(output_root, target):
                report["blocked"].append(f"asset target escapes output directory: {target}")
            if write and target.exists() and not force:
                report["blocked"].append(f"asset target already exists; pass --force to overwrite: {tools.rel(target)}")
            planned.append({**asset, "path": tools.rel(target), "size_bytes": archive.getinfo(asset["part"]).file_size})
        report["evidence"].append({"kind": "pptx-assets", "assets": planned})
        if report["blocked"]:
            return tools.finish(report, False, "blocked", "Asset output path containment failed.")
        if not write:
            return tools.finish(report, True, "planned", f"Found {len(assets)} PPTX asset part(s); no files were written.")
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
    return tools.finish(report, True, "passed", f"Extracted {len(written)} PPTX asset file(s) and wrote an asset manifest.")
