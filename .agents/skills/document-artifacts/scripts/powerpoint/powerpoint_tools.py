#!/usr/bin/env python3

import argparse
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

import powerpoint_format_depth

sys.dont_write_bytecode = True

SKILL_NAME = "document-artifacts"
REPO_ROOT = Path(__file__).resolve().parents[5]
LOCAL_DEPS = REPO_ROOT / ".agents" / ".deps" / SKILL_NAME
if LOCAL_DEPS.exists() and str(LOCAL_DEPS) not in sys.path:
    sys.path.insert(0, str(LOCAL_DEPS))

PYTHON_DEPENDENCIES = {}

A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P_NS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
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
INPUT_PPTX_OPEN_FAILED = "Input PPTX could not be opened."
PPTX_UNSAFE_PACKAGE = "PPTX package contains unsafe paths."
UNSAFE_OOXML_PREFIX = "unsafe OOXML paths: "
WRITE_OUTPUT_REQUIRED_FLAG = "--output is required with --write"
WRITE_OUTPUT_REQUIRED = "Writing requires an explicit output path."
PPTX_OUTPUT_SAFETY_FAILED = "PPTX output path failed safety checks."
OUTPUT_VERIFICATION_FAILED = "output verification failed"
PPTX_WRITTEN_VERIFY_FAILED = "PPTX was written but output verification failed."

ET.register_namespace("p", "http://schemas.openxmlformats.org/presentationml/2006/main")
ET.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")
ET.register_namespace("a", "http://schemas.openxmlformats.org/drawingml/2006/main")


def rel(path):
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def install_python_deps(report):
    packages = list(PYTHON_DEPENDENCIES.values())
    if not packages:
        report["skipped"].append("no optional Python packages are required for current PPTX helpers")
        return
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
    return {
        "python": True,
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
        "format": "pptx",
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


def validate_report_shape(report):
    missing = [field for field in REQUIRED_REPORT_FIELDS if field not in report]
    issues = []
    if missing:
        issues.append("missing required report fields: " + ", ".join(missing))
    for key in ["capabilities", "evidence", "findings", "warnings", "skipped", "issues", "artifacts"]:
        expected = dict if key == "capabilities" else list
        if key in report and not isinstance(report[key], expected):
            issues.append(f"field has wrong type: {key}")
    return issues


def append_shape_validation(report):
    issues = validate_report_shape(report)
    report["checks"].append({"name": "evidence-schema-shape", "ok": not issues})
    report["issues"].extend(issues)


def enforce_output_path(report, input_path, output_path, force, purpose):
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
        report["blocked"].append(f"{purpose} output already exists; pass --force to overwrite: {rel(output_path)}")
        return False
    return True


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
        "macro_extension": path.suffix.lower() in {".pptm", ".potm", ".ppam"},
        "vba_project": any(name.endswith("vbaproject.bin") for name in lowered),
        "active_x": any("activex/" in name for name in lowered),
        "ole_object": any("embeddings/" in name or name.endswith(".bin") for name in lowered),
        "external_media": False,
    }


def open_pptx(path):
    if not path.exists() or not path.is_file():
        return None, {"blocked": "input PPTX does not exist"}
    try:
        return zipfile.ZipFile(path), None
    except zipfile.BadZipFile:
        return None, {"blocked": "file has .pptx extension but is not a valid ZIP package"}


def slide_paths(names):
    def key(name):
        match = re.search(r"slide(\d+)\.xml$", name)
        return int(match.group(1)) if match else 0

    return sorted((name for name in names if re.match(r"ppt/slides/slide\d+\.xml$", name)), key=key)


def xml_texts(data):
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    return [node.text for node in root.iter(f"{A_NS}t") if node.text]


metadata_report = lambda path: powerpoint_format_depth.metadata_report(sys.modules[__name__], path)
links_report = lambda path: powerpoint_format_depth.links_report(sys.modules[__name__], path)
outline_report = lambda path: powerpoint_format_depth.outline_report(sys.modules[__name__], path)
accessibility_report = lambda path: powerpoint_format_depth.accessibility_report(sys.modules[__name__], path)
extract_assets = lambda path, output_dir, write, force=False: powerpoint_format_depth.extract_assets(sys.modules[__name__], path, output_dir, write, force)


def inspect_pptx(path):
    report = base_report("inspect", path)
    archive, error = open_pptx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return finish(report, False, "blocked", INPUT_PPTX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        report["checks"].append({"name": "safe-ooxml-paths", "ok": not unsafe})
        if unsafe:
            report["blocked"].append(f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return finish(report, False, "blocked", PPTX_UNSAFE_PACKAGE)
        report["warnings"].extend(relationship_warnings(archive, names))
        slides = slide_paths(names)
        notes = [name for name in names if name.startswith("ppt/notesSlides/")]
        media = [name for name in names if name.startswith("ppt/media/")]
        charts = [name for name in names if name.startswith("ppt/charts/")]
        active = active_content_markers(path, names)
        hidden = 0
        if "ppt/presentation.xml" in names:
            hidden = archive.read("ppt/presentation.xml").count(b'show="0"')
        report["evidence"].append({"kind": "pptx-package", "entries": len(names), "slides": len(slides), "notes": len(notes), "media_files": len(media), "chart_parts": len(charts), "hidden_slides": hidden, "active_content": active})
        report["findings"].append(f"Slides: {len(slides)}")
        if notes:
            report["findings"].append("Speaker notes detected.")
        if charts:
            report["findings"].append("Chart parts detected.")
        if any(active.values()):
            report["warnings"].append("Active content, embedded object, or external media markers detected; review before opening in a desktop app.")
    return finish(report, True, "passed", "PPTX inspection completed with deterministic OOXML evidence.")


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
    if not (caps["soffice"] or caps["libreoffice"]):
        report["skipped"].append("no PPTX renderer found on PATH: render will plan or skip")
    report["commands"].extend(
        [
            "python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py inspect --file <file.pptx> --json",
            "python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py inventory --file <file.pptx> --json",
            "python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py compare --before <old.pptx> --after <new.pptx> --json",
        ]
    )
    ok = not any("install failed" in issue for issue in report["issues"])
    return finish(report, ok, "passed" if ok else "failed", "PowerPoint deck helper readiness checked.")


def extract_text(path):
    report = base_report("extract-text", path)
    archive, error = open_pptx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return finish(report, False, "blocked", INPUT_PPTX_OPEN_FAILED)
    texts = []
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return finish(report, False, "blocked", PPTX_UNSAFE_PACKAGE)
        for index, name in enumerate(slide_paths(names), start=1):
            values = xml_texts(archive.read(name))
            texts.append({"slide": index, "part": name, "text": values})
        for name in sorted(n for n in names if n.startswith("ppt/notesSlides/")):
            values = xml_texts(archive.read(name))
            if values:
                texts.append({"notes_part": name, "text": values})
    report["evidence"].append({"kind": "slide-text", "items": texts, "text_count": sum(len(item["text"]) for item in texts)})
    return finish(report, True, "passed", "Extracted slide and notes text deterministically.")


def inventory(path):
    report = base_report("inventory", path)
    archive, error = open_pptx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return finish(report, False, "blocked", INPUT_PPTX_OPEN_FAILED)
    slides = []
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return finish(report, False, "blocked", PPTX_UNSAFE_PACKAGE)
        for index, name in enumerate(slide_paths(names), start=1):
            values = xml_texts(archive.read(name))
            slides.append({"slide": index, "part": name, "title": values[0] if values else "", "text_items": len(values), "excerpt": " | ".join(values)[:300]})
    report["evidence"].append({"kind": "slide-inventory", "slides": slides})
    return finish(report, True, "passed", f"Inventoried {len(slides)} slide(s).")


def markdown_from_pptx(
    path,
    max_slides=100,
    include_metadata=False,
    include_links=False,
    include_outline=False,
    include_assets=False,
):
    skipped = []
    warnings = []
    archive, error = open_pptx(path)
    if error:
        return "", skipped, [error["blocked"]]
    sections = [f"# {path.name}", ""]
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        if unsafe:
            return "", skipped, [f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}"]
        warnings.extend(relationship_warnings(archive, names))
        slides = slide_paths(names)
        if len(slides) > max_slides:
            skipped.append(f"markdown truncated after {max_slides} slide(s)")
        if not slides:
            skipped.append("no slide XML parts found")
        for index, name in enumerate(slides[:max_slides], start=1):
            values = xml_texts(archive.read(name))
            sections.append(f"## Slide {index}")
            if values:
                sections.extend(f"- {value}" for value in values)
            else:
                skipped.append(f"no text extracted from {name}")
        notes_parts = sorted(n for n in names if n.startswith("ppt/notesSlides/"))
        if notes_parts:
            sections.append("\n## Speaker Notes")
            for name in notes_parts:
                values = xml_texts(archive.read(name))
                if values:
                    sections.append(f"### {name}")
                    sections.extend(f"- {value}" for value in values)
        media = [name for name in names if name.startswith("ppt/media/")]
        charts = [name for name in names if name.startswith("ppt/charts/")]
        if media or charts:
            sections.append("\n## Non-Text Assets")
            sections.extend(f"- media: {name}" for name in media)
            sections.extend(f"- chart: {name}" for name in charts)
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
    max_slides=100,
    include_metadata=False,
    include_links=False,
    include_outline=False,
    include_assets=False,
    include_content=False,
):
    report = base_report("to-markdown", path)
    markdown, skipped, warnings = markdown_from_pptx(path, max_slides, include_metadata, include_links, include_outline, include_assets)
    report["skipped"].extend(skipped)
    report["warnings"].extend(warnings)
    if not markdown.strip():
        report["blocked"].append("no markdown content could be produced")
        return finish(report, False, "blocked", "PPTX markdown conversion produced no content.")
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
    return finish(report, True, "passed", "Converted PPTX content to Markdown.")


def replace_text(path, find, replacement, output, write, force=False, verify_output=False):
    report = base_report("replace-text", path)
    archive, error = open_pptx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return finish(report, False, "blocked", INPUT_PPTX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return finish(report, False, "blocked", PPTX_UNSAFE_PACKAGE)
        replacements = {}
        for name in names:
            if name.startswith("ppt/") and name.endswith(".xml"):
                text = archive.read(name).decode("utf-8", errors="ignore")
                count = text.count(find)
                if count:
                    replacements[name] = count
    report["evidence"].append({"kind": "replace-plan", "find": find, "replacement_count": sum(replacements.values()), "parts": replacements})
    if not write:
        return finish(report, True, "planned", "Text replacement dry-run completed; no PPTX was written.")
    if output is None:
        report["blocked"].append(WRITE_OUTPUT_REQUIRED_FLAG)
        return finish(report, False, "blocked", WRITE_OUTPUT_REQUIRED)
    if not enforce_output_path(report, path, output, force, "PPTX"):
        return finish(report, False, "blocked", PPTX_OUTPUT_SAFETY_FAILED)
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename in replacements:
                data = data.decode("utf-8", errors="ignore").replace(find, replacement).encode("utf-8")
            target.writestr(info, data)
    report["writes"].append(rel(output))
    if verify_output:
        verification = compare_pptx(path, output)
        report["evidence"].append({"kind": "output-verification", "report": report_digest(verification)})
        if not verification["ok"]:
            report["issues"].append(OUTPUT_VERIFICATION_FAILED)
            return finish(report, False, "failed", PPTX_WRITTEN_VERIFY_FAILED)
    return finish(report, True, "passed", "PPTX text replacement was written to the explicit output path.")


def parse_order(order, slide_count):
    values = [int(item.strip()) for item in order.split(",") if item.strip()]
    if sorted(values) != list(range(1, slide_count + 1)):
        raise ValueError(f"order must contain each slide number exactly once from 1 to {slide_count}")
    return values


def rearrange(path, order, output, write, force=False, verify_output=False):
    report = base_report("rearrange", path)
    archive, error = open_pptx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return finish(report, False, "blocked", INPUT_PPTX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return finish(report, False, "blocked", PPTX_UNSAFE_PACKAGE)
        slides = slide_paths(names)
        try:
            requested = parse_order(order, len(slides))
        except ValueError as exc:
            report["blocked"].append(str(exc))
            return finish(report, False, "blocked", "Invalid slide order.")
        report["evidence"].append({"kind": "rearrange-plan", "current_order": list(range(1, len(slides) + 1)), "requested_order": requested})
        if not write:
            return finish(report, True, "planned", "Slide rearrange dry-run completed; no PPTX was written.")
        if output is None:
            report["blocked"].append(WRITE_OUTPUT_REQUIRED_FLAG)
            return finish(report, False, "blocked", WRITE_OUTPUT_REQUIRED)
        if not enforce_output_path(report, path, output, force, "PPTX"):
            return finish(report, False, "blocked", PPTX_OUTPUT_SAFETY_FAILED)
        if "ppt/presentation.xml" not in names:
            report["blocked"].append("ppt/presentation.xml is missing")
            return finish(report, False, "blocked", "Cannot reorder slides without presentation.xml.")
        presentation = archive.read("ppt/presentation.xml")
    root = ET.fromstring(presentation)
    sld_id_list = root.find(f"{P_NS}sldIdLst")
    if sld_id_list is None:
        report["blocked"].append("presentation.xml has no slide id list")
        return finish(report, False, "blocked", "Cannot reorder slides without a slide id list.")
    children = list(sld_id_list)
    reordered = [children[index - 1] for index in requested]
    sld_id_list[:] = reordered
    new_presentation = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            data = new_presentation if info.filename == "ppt/presentation.xml" else source.read(info.filename)
            target.writestr(info, data)
    report["writes"].append(rel(output))
    if verify_output:
        verification = compare_pptx(path, output)
        report["evidence"].append({"kind": "output-verification", "report": report_digest(verification)})
        if not verification["ok"]:
            report["issues"].append(OUTPUT_VERIFICATION_FAILED)
            return finish(report, False, "failed", PPTX_WRITTEN_VERIFY_FAILED)
    return finish(report, True, "passed", "PPTX slide order was written to the explicit output path.")


def render_deck(path, output_dir, write):
    report = base_report("render", path)
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        report["skipped"].append("no supported PPTX renderer found on PATH: soffice or libreoffice")
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
    return finish(report, result.returncode == 0, "passed" if result.returncode == 0 else "failed", "PPTX render command completed.")


def pptx_metrics(path):
    inspect = inspect_pptx(path)
    inv = inventory(path)
    text = extract_text(path)
    package = inspect["evidence"][0] if inspect.get("evidence") else {}
    slides = inv["evidence"][0].get("slides", []) if inv.get("evidence") else []
    text_count = text["evidence"][0].get("text_count", 0) if text.get("evidence") else 0
    return {
        "path": rel(path),
        "sha256": file_sha256(path),
        "size_bytes": file_size(path),
        "ok": inspect["ok"],
        "status": inspect["status"],
        "slides": package.get("slides", 0),
        "slide_inventory": slides,
        "notes": package.get("notes", 0),
        "media_files": package.get("media_files", 0),
        "chart_parts": package.get("chart_parts", 0),
        "hidden_slides": package.get("hidden_slides", 0),
        "text_count": text_count,
        "warnings": inspect.get("warnings", []),
    }


def compare_pptx(before, after):
    report = base_report("compare", before)
    report["evidence"].append({"kind": "compare-inputs", "before": rel(before), "after": rel(after), "after_sha256": file_sha256(after), "after_size_bytes": file_size(after)})
    if not before.exists() or not after.exists():
        report["blocked"].append("both --before and --after PPTX files must exist")
        return finish(report, False, "blocked", "Compare requires two existing PPTX files.")
    before_metrics = pptx_metrics(before)
    after_metrics = pptx_metrics(after)
    differences = {
        "sha256_changed": before_metrics["sha256"] != after_metrics["sha256"],
        "slide_count_changed": before_metrics["slides"] != after_metrics["slides"],
        "slide_inventory_changed": before_metrics["slide_inventory"] != after_metrics["slide_inventory"],
        "notes_changed": before_metrics["notes"] != after_metrics["notes"],
        "media_changed": before_metrics["media_files"] != after_metrics["media_files"],
        "charts_changed": before_metrics["chart_parts"] != after_metrics["chart_parts"],
        "hidden_slides_changed": before_metrics["hidden_slides"] != after_metrics["hidden_slides"],
    }
    report["evidence"].append({"kind": "pptx-compare", "before": before_metrics, "after": after_metrics, "differences": differences})
    changed = [name for name, value in differences.items() if value]
    report["findings"].append("Changed: " + ", ".join(changed) if changed else "No deterministic PPTX differences detected.")
    return finish(report, True, "passed", "PPTX comparison completed with deterministic evidence.")


def write_report_file(report, output_path, force=False):
    if not enforce_output_path(report, None, output_path, force, "evidence report"):
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return True


def bundle_evidence(path, output_dir, write, force=False):
    report = base_report("bundle-evidence", path)
    if write and not enforce_output_dir(report, [path], output_dir, "PPTX evidence bundle"):
        return finish(report, False, "blocked", "PPTX evidence bundle output directory failed safety checks.")
    reports = {
        "inspect": inspect_pptx(path),
        "metadata": metadata_report(path),
        "links": links_report(path),
        "outline": outline_report(path),
        "accessibility": accessibility_report(path),
        "inventory": inventory(path),
        "extract-text": extract_text(path),
    }
    reports["to-markdown"] = to_markdown(path, output_dir / "content.md" if write else None, force=force, include_metadata=True, include_links=True, include_outline=True, include_assets=True)
    reports["extract-assets"] = extract_assets(path, output_dir / "assets", write=write, force=force)
    report["evidence"].append({"kind": "evidence-bundle", "reports": {name: report_digest(item) for name, item in reports.items()}})
    input_arg = rel(path)
    output_arg = rel(output_dir)
    render_available = bool(shutil.which("soffice") or shutil.which("libreoffice"))
    commands = [
        f"python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py inspect --file {input_arg} --json",
        f"python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py to-markdown --file {input_arg} --output {output_arg}/content.md --write --json",
        f"python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py extract-assets --file {input_arg} --output-dir {output_arg}/assets --write --json",
        "python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py compare --before <before.pptx> --after <after.pptx> --json",
    ]
    render_command = f"python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py render --file {input_arg} --output-dir {output_arg}/rendered --write --json"
    commands.append(render_command if render_available else f"{render_command}  # renderer unavailable; run doctor for capability details")
    next_commands = {"kind": "next-safe-commands", "render_available": render_available, "commands": commands}
    report["evidence"].append(next_commands)
    report["findings"].extend(f"Next safe command: {command}" for command in next_commands["commands"])
    report["artifacts"].append({"kind": "evidence-bundle", "path": rel(output_dir), "write_requested": write})
    report["warnings"].extend(item for nested in reports.values() for item in nested.get("warnings", []))
    report["skipped"].extend(item for nested in reports.values() for item in nested.get("skipped", []))
    if write:
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, nested in reports.items():
            target = output_dir / f"{name}.json"
            if not write_report_file(nested, target, force):
                report["blocked"].extend(nested.get("blocked", []))
                return finish(report, False, "blocked", "Evidence bundle report output path failed safety checks.")
            report["writes"].append(rel(target))
        index_path = output_dir / "evidence-bundle.json"
        if not enforce_output_path(report, None, index_path, force, "evidence bundle index"):
            return finish(report, False, "blocked", "Evidence bundle index output path failed safety checks.")
        index_path.write_text(json.dumps({"schema_version": 1, "tool": report["tool"], "reports": {name: f"{name}.json" for name in reports}}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        report["writes"].append(rel(index_path))
        report["artifacts"].append({"kind": "evidence-bundle-index", "path": rel(index_path)})
    return finish(report, True, "passed" if write else "planned", "PPTX evidence bundle " + ("written." if write else "planned; no files were written."))


def batch_evidence(paths, output_dir, write, force=False):
    report = base_report("batch")
    if write and not enforce_output_dir(report, paths, output_dir, "PPTX batch"):
        return finish(report, False, "blocked", "PPTX batch output directory failed safety checks.")
    summaries = []
    for path in paths:
        child_dir = output_dir / re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._")
        child = bundle_evidence(path, child_dir, write=write, force=force)
        summaries.append({"path": rel(path), "output_dir": rel(child_dir), "summary": report_digest(child)})
        report["warnings"].extend(child.get("warnings", []))
        report["skipped"].extend(child.get("skipped", []))
        if not child["ok"]:
            report["issues"].append(f"bundle failed for {rel(path)}: {child['summary']}")
    report["evidence"].append({"kind": "batch-evidence", "inputs": [rel(path) for path in paths], "items": summaries})
    if write:
        output_dir.mkdir(parents=True, exist_ok=True)
        index_path = output_dir / "batch-index.json"
        if not enforce_output_path(report, None, index_path, force, "batch index"):
            return finish(report, False, "blocked", "Batch index output path failed safety checks.")
        index_path.write_text(json.dumps({"schema_version": 1, "tool": report["tool"], "items": summaries}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        report["writes"].append(rel(index_path))
        report["artifacts"].append({"kind": "batch-index", "path": rel(index_path)})
    ok = not report["issues"]
    return finish(report, ok, "passed" if write and ok else "planned" if ok else "failed", f"Processed {len(paths)} PPTX file(s) for batch evidence.")


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
    strict_blocker = report.get("status") == "skipped" or any(term in skipped_text for term in ["fallback", "renderer", "render"])
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
    parser = argparse.ArgumentParser(description="PowerPoint deck helpers")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--install-python-deps", action="store_true")
    add_report_options(doctor)
    for name in ["inspect", "extract-text", "inventory", "metadata", "links", "outline", "accessibility"]:
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
    markdown.add_argument("--max-slides", type=int, default=100)
    markdown.add_argument("--include-metadata", action="store_true")
    markdown.add_argument("--include-links", action="store_true")
    markdown.add_argument("--include-outline", action="store_true")
    markdown.add_argument("--include-assets", action="store_true")
    markdown.add_argument("--include-content", action="store_true", help="Embed the complete converted Markdown in report evidence; use --json or --output-json to emit it.")
    add_report_options(markdown)
    replace = sub.add_parser("replace-text")
    replace.add_argument("--file", required=True, type=Path)
    replace.add_argument("--find", required=True)
    replace.add_argument("--replace", required=True)
    replace.add_argument("--output", type=Path)
    replace.add_argument("--verify-output", action="store_true")
    mode = replace.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--write", action="store_true")
    add_report_options(replace)
    rearrange_parser = sub.add_parser("rearrange")
    rearrange_parser.add_argument("--file", required=True, type=Path)
    rearrange_parser.add_argument("--order", required=True)
    rearrange_parser.add_argument("--output", type=Path)
    rearrange_parser.add_argument("--verify-output", action="store_true")
    order_mode = rearrange_parser.add_mutually_exclusive_group()
    order_mode.add_argument("--dry-run", action="store_true", default=True)
    order_mode.add_argument("--write", action="store_true")
    add_report_options(rearrange_parser)
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
        return print_report(inspect_pptx(args.file), **opts)
    if args.command == "extract-text":
        return print_report(extract_text(args.file), **opts)
    if args.command == "inventory":
        return print_report(inventory(args.file), **opts)
    if args.command == "metadata":
        return print_report(metadata_report(args.file), **opts)
    if args.command == "links":
        return print_report(links_report(args.file), **opts)
    if args.command == "outline":
        return print_report(outline_report(args.file), **opts)
    if args.command == "accessibility":
        return print_report(accessibility_report(args.file), **opts)
    if args.command == "compare":
        return print_report(compare_pptx(args.before, args.after), **opts)
    if args.command == "to-markdown":
        return print_report(to_markdown(args.file, args.output, args.force, args.max_slides, args.include_metadata, args.include_links, args.include_outline, args.include_assets, args.include_content), **opts)
    if args.command == "replace-text":
        return print_report(replace_text(args.file, args.find, args.replace, args.output, args.write, args.force, args.verify_output), **opts)
    if args.command == "rearrange":
        return print_report(rearrange(args.file, args.order, args.output, args.write, args.force, args.verify_output), **opts)
    if args.command == "render":
        return print_report(render_deck(args.file, args.output_dir, args.write), **opts)
    if args.command == "extract-assets":
        return print_report(extract_assets(args.file, args.output_dir, args.write, args.force), **opts)
    if args.command == "bundle-evidence":
        return print_report(bundle_evidence(args.file, args.output_dir, args.write, args.force), **opts)
    if args.command == "batch":
        return print_report(batch_evidence(args.paths, args.output_dir, args.write, args.force), **opts)
    raise AssertionError(args)


if __name__ == "__main__":
    raise SystemExit(main())
