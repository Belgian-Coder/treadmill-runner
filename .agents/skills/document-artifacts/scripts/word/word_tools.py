#!/usr/bin/env python3

import argparse
import hashlib
import html
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

import word_format_depth

sys.dont_write_bytecode = True

SKILL_NAME = "document-artifacts"
REPO_ROOT = Path(__file__).resolve().parents[5]
LOCAL_DEPS = REPO_ROOT / ".agents" / ".deps" / SKILL_NAME
if LOCAL_DEPS.exists() and str(LOCAL_DEPS) not in sys.path:
    sys.path.insert(0, str(LOCAL_DEPS))

PYTHON_DEPENDENCIES = {
    "python-docx": "python-docx==1.2.0",
}

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
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
INPUT_DOCX_OPEN_FAILED = "Input DOCX could not be opened."
DOCX_UNSAFE_PACKAGE = "DOCX package contains unsafe paths."
DOCX_MISSING_DOCUMENT_TEXT = "DOCX package does not contain document text."
UNSAFE_OOXML_PREFIX = "unsafe OOXML paths: "
WRITE_OUTPUT_REQUIRED_FLAG = "--output is required with --write"
WRITE_OUTPUT_REQUIRED = "Writing requires an explicit output path."
DOCX_OUTPUT_SAFETY_FAILED = "DOCX output path failed safety checks."
OUTPUT_VERIFICATION_FAILED = "output verification failed"
DOCX_WRITTEN_VERIFY_FAILED = "DOCX was written but output verification failed."


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
        __import__("docx")
        python_docx = True
    except Exception:
        python_docx = False
    return {
        "python": True,
        "python-docx": python_docx,
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
        "format": "docx",
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
        "macro_extension": path.suffix.lower() in {".docm", ".dotm"},
        "vba_project": any(name.endswith("vbaproject.bin") for name in lowered),
        "active_x": any("activex/" in name for name in lowered),
        "ole_object": any("embeddings/" in name or name.endswith(".bin") for name in lowered),
        "external_relationships": False,
    }


def open_docx(path):
    if not path.exists() or not path.is_file():
        return None, {"blocked": "input DOCX does not exist"}
    try:
        return zipfile.ZipFile(path), None
    except zipfile.BadZipFile:
        return None, {"blocked": "file has .docx extension but is not a valid ZIP package"}


def paragraph_texts(data):
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    paragraphs = []
    for para in root.iter(f"{W_NS}p"):
        text = "".join(node.text or "" for node in para.iter(f"{W_NS}t"))
        if text.strip():
            paragraphs.append(text.strip())
    return paragraphs


metadata_report = lambda path: word_format_depth.metadata_report(sys.modules[__name__], path)
links_report = lambda path: word_format_depth.links_report(sys.modules[__name__], path)
outline_report = lambda path: word_format_depth.outline_report(sys.modules[__name__], path)
accessibility_report = lambda path: word_format_depth.accessibility_report(sys.modules[__name__], path)
extract_assets = lambda path, output_dir, write, force=False: word_format_depth.extract_assets(sys.modules[__name__], path, output_dir, write, force)


def inspect_docx(path):
    report = base_report("inspect", path)
    archive, error = open_docx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return finish(report, False, "blocked", INPUT_DOCX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        report["checks"].append({"name": "safe-ooxml-paths", "ok": not unsafe})
        if unsafe:
            report["blocked"].append(f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return finish(report, False, "blocked", DOCX_UNSAFE_PACKAGE)
        rel_warnings = relationship_warnings(archive, names)
        report["warnings"].extend(rel_warnings)
        document = archive.read("word/document.xml") if "word/document.xml" in names else b""
        comments = [name for name in names if "comments" in name.lower()]
        media = [name for name in names if name.startswith("word/media/")]
        active = active_content_markers(path, names)
        revisions = document.count(b"<w:ins") + document.count(b"<w:del")
        paragraphs = paragraph_texts(document) if document else []
        tables = document.count(b"<w:tbl") if document else 0
        report["evidence"].append(
            {
                "kind": "docx-package",
                "entries": len(names),
                "paragraphs": len(paragraphs),
                "tables": tables,
                "media_files": len(media),
                "comment_parts": len(comments),
                "revision_markers": revisions,
                "active_content": active,
            }
        )
        report["findings"].append(f"Paragraphs: {len(paragraphs)}")
        if comments:
            report["findings"].append("Comment parts detected.")
        if revisions:
            report["findings"].append("Tracked-change markers detected.")
        if any(active.values()):
            report["warnings"].append("Active content or embedded object markers detected; review before opening in a desktop app.")
    return finish(report, True, "passed", "DOCX inspection completed with deterministic OOXML evidence.")


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
    if not caps["python-docx"]:
        report["skipped"].append("python-docx unavailable: direct OOXML fallback remains available for inspection/extraction")
    if not (caps["soffice"] or caps["libreoffice"]):
        report["skipped"].append("no DOCX renderer found on PATH: render will plan or skip")
    report["commands"].extend(
        [
            "python -B .agents/skills/document-artifacts/scripts/word/word_tools.py inspect --file <file.docx> --json",
            "python -B .agents/skills/document-artifacts/scripts/word/word_tools.py extract-markdown --file <file.docx> --json",
            "python -B .agents/skills/document-artifacts/scripts/word/word_tools.py compare --before <old.docx> --after <new.docx> --json",
        ]
    )
    ok = not any("install failed" in issue for issue in report["issues"])
    return finish(report, ok, "passed" if ok else "failed", "Word document helper readiness checked.")


def extract_markdown(path):
    report = base_report("extract-markdown", path)
    archive, error = open_docx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return finish(report, False, "blocked", INPUT_DOCX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return finish(report, False, "blocked", DOCX_UNSAFE_PACKAGE)
        if "word/document.xml" not in names:
            report["blocked"].append("word/document.xml is missing")
            return finish(report, False, "blocked", DOCX_MISSING_DOCUMENT_TEXT)
        paragraphs = paragraph_texts(archive.read("word/document.xml"))
    markdown = "\n\n".join(html.escape(text) for text in paragraphs)
    report["evidence"].append({"kind": "markdown", "paragraphs": len(paragraphs), "characters": len(markdown), "excerpt": markdown[:800]})
    return finish(report, bool(markdown), "passed" if markdown else "skipped", "Extracted Markdown-like text." if markdown else "No document text was extracted.")


def markdown_from_docx(
    path,
    max_paragraphs=500,
    include_metadata=False,
    include_links=False,
    include_outline=False,
    include_assets=False,
):
    skipped = []
    warnings = []
    archive, error = open_docx(path)
    if error:
        return "", skipped, [error["blocked"]]
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        if unsafe:
            return "", skipped, [f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}"]
        warnings.extend(relationship_warnings(archive, names))
        if "word/document.xml" not in names:
            return "", skipped, ["word/document.xml is missing"]
        document = archive.read("word/document.xml")
        paragraphs = paragraph_texts(document)
        if len(paragraphs) > max_paragraphs:
            skipped.append(f"markdown truncated after {max_paragraphs} paragraph(s)")
            paragraphs = paragraphs[:max_paragraphs]
        comments = [name for name in names if name.endswith("comments.xml")]
        if comments:
            warnings.append("document contains comments; review comments separately before relying on markdown")
        if document.count(b"<w:ins") or document.count(b"<w:del"):
            warnings.append("document contains tracked-change markers; review tracked changes separately")
    if not paragraphs:
        skipped.append("no paragraph text extracted")
    lines = [f"# {path.name}", ""]
    lines.extend(html.escape(text) for text in paragraphs)
    if include_metadata:
        lines.extend(["", "## Metadata Evidence", "", "```json", json.dumps(report_digest(metadata_report(path)), indent=2, sort_keys=True), "```"])
    if include_links:
        lines.extend(["", "## Link Evidence", "", "```json", json.dumps(report_digest(links_report(path)), indent=2, sort_keys=True), "```"])
    if include_outline:
        lines.extend(["", "## Outline Evidence", "", "```json", json.dumps(report_digest(outline_report(path)), indent=2, sort_keys=True), "```"])
    if include_assets:
        lines.extend(["", "## Asset Evidence", "", "```json", json.dumps(report_digest(extract_assets(path, Path('_assets'), write=False)), indent=2, sort_keys=True), "```"])
    return "\n\n".join(lines).strip() + "\n", skipped, warnings


def to_markdown(
    path,
    output,
    force=False,
    max_paragraphs=500,
    include_metadata=False,
    include_links=False,
    include_outline=False,
    include_assets=False,
    include_content=False,
):
    report = base_report("to-markdown", path)
    markdown, skipped, warnings = markdown_from_docx(path, max_paragraphs, include_metadata, include_links, include_outline, include_assets)
    report["skipped"].extend(skipped)
    report["warnings"].extend(warnings)
    if not markdown.strip():
        report["blocked"].append("no markdown content could be produced")
        return finish(report, False, "blocked", "DOCX markdown conversion produced no content.")
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
    return finish(report, True, "passed", "Converted DOCX content to Markdown.")


def comments_report(path):
    report = base_report("comments", path)
    archive, error = open_docx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return finish(report, False, "blocked", INPUT_DOCX_OPEN_FAILED)
    comments = []
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return finish(report, False, "blocked", DOCX_UNSAFE_PACKAGE)
        for name in names:
            if name.endswith("comments.xml"):
                comments.extend(paragraph_texts(archive.read(name)))
    report["evidence"].append({"kind": "comments", "count": len(comments), "comments": comments[:20]})
    return finish(report, True, "passed", f"Found {len(comments)} comment paragraph(s).")


def tracked_changes_report(path):
    report = base_report("tracked-changes", path)
    archive, error = open_docx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return finish(report, False, "blocked", INPUT_DOCX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return finish(report, False, "blocked", DOCX_UNSAFE_PACKAGE)
        document = archive.read("word/document.xml") if "word/document.xml" in names else b""
    insertions = document.count(b"<w:ins")
    deletions = document.count(b"<w:del")
    report["evidence"].append({"kind": "tracked-changes", "insertions": insertions, "deletions": deletions})
    return finish(report, True, "passed", f"Found {insertions} insertion and {deletions} deletion marker(s).")


def replace_text(path, find, replacement, output, write, force=False, verify_output=False):
    report = base_report("replace-text", path)
    archive, error = open_docx(path)
    if error:
        report["blocked"].append(error["blocked"])
        return finish(report, False, "blocked", INPUT_DOCX_OPEN_FAILED)
    assert archive is not None
    with archive:
        names, unsafe = safe_names(archive)
        if unsafe:
            report["blocked"].append(f"{UNSAFE_OOXML_PREFIX}{', '.join(unsafe[:5])}")
            return finish(report, False, "blocked", DOCX_UNSAFE_PACKAGE)
        replacements = {}
        for name in names:
            if name.startswith("word/") and name.endswith(".xml"):
                text = archive.read(name).decode("utf-8", errors="ignore")
                count = text.count(find)
                if count:
                    replacements[name] = count
    report["evidence"].append({"kind": "replace-plan", "find": find, "replacement_count": sum(replacements.values()), "parts": replacements})
    if not write:
        return finish(report, True, "planned", "Text replacement dry-run completed; no DOCX was written.")
    if output is None:
        report["blocked"].append(WRITE_OUTPUT_REQUIRED_FLAG)
        return finish(report, False, "blocked", WRITE_OUTPUT_REQUIRED)
    if not enforce_output_path(report, path, output, force, "DOCX"):
        return finish(report, False, "blocked", DOCX_OUTPUT_SAFETY_FAILED)
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename in replacements:
                data = data.decode("utf-8", errors="ignore").replace(find, replacement).encode("utf-8")
            target.writestr(info, data)
    report["writes"].append(rel(output))
    if verify_output:
        verification = compare_docx(path, output)
        report["evidence"].append({"kind": "output-verification", "report": report_digest(verification)})
        if not verification["ok"]:
            report["issues"].append(OUTPUT_VERIFICATION_FAILED)
            return finish(report, False, "failed", DOCX_WRITTEN_VERIFY_FAILED)
    return finish(report, True, "passed", "DOCX text replacement was written to the explicit output path.")


def render_docx(path, output_dir, write):
    report = base_report("render", path)
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        report["skipped"].append("no supported DOCX renderer found on PATH: soffice or libreoffice")
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
    return finish(report, result.returncode == 0, "passed" if result.returncode == 0 else "failed", "DOCX render command completed.")


def docx_metrics(path):
    inspect = inspect_docx(path)
    markdown = extract_markdown(path)
    comments = comments_report(path)
    tracked = tracked_changes_report(path)
    text_chars = 0
    excerpt = ""
    if markdown.get("evidence"):
        text_chars = int(markdown["evidence"][0].get("characters", 0))
        excerpt = str(markdown["evidence"][0].get("excerpt", ""))
    comments_count = comments["evidence"][0].get("count", 0) if comments.get("evidence") else 0
    tracked_evidence = tracked["evidence"][0] if tracked.get("evidence") else {}
    package = inspect["evidence"][0] if inspect.get("evidence") else {}
    return {
        "path": rel(path),
        "sha256": file_sha256(path),
        "size_bytes": file_size(path),
        "ok": inspect["ok"],
        "status": inspect["status"],
        "paragraphs": package.get("paragraphs", 0),
        "tables": package.get("tables", 0),
        "media_files": package.get("media_files", 0),
        "text_characters": text_chars,
        "text_excerpt": excerpt,
        "comments": comments_count,
        "insertions": tracked_evidence.get("insertions", 0),
        "deletions": tracked_evidence.get("deletions", 0),
        "warnings": inspect.get("warnings", []),
    }


def compare_docx(before, after):
    report = base_report("compare", before)
    report["evidence"].append({"kind": "compare-inputs", "before": rel(before), "after": rel(after), "after_sha256": file_sha256(after), "after_size_bytes": file_size(after)})
    if not before.exists() or not after.exists():
        report["blocked"].append("both --before and --after DOCX files must exist")
        return finish(report, False, "blocked", "Compare requires two existing DOCX files.")
    before_metrics = docx_metrics(before)
    after_metrics = docx_metrics(after)
    differences = {
        "sha256_changed": before_metrics["sha256"] != after_metrics["sha256"],
        "text_characters_changed": before_metrics["text_characters"] != after_metrics["text_characters"],
        "comments_changed": before_metrics["comments"] != after_metrics["comments"],
        "tracked_changes_changed": (before_metrics["insertions"], before_metrics["deletions"]) != (after_metrics["insertions"], after_metrics["deletions"]),
        "tables_changed": before_metrics["tables"] != after_metrics["tables"],
        "media_changed": before_metrics["media_files"] != after_metrics["media_files"],
    }
    report["evidence"].append({"kind": "docx-compare", "before": before_metrics, "after": after_metrics, "differences": differences})
    changed = [name for name, value in differences.items() if value]
    report["findings"].append("Changed: " + ", ".join(changed) if changed else "No deterministic DOCX differences detected.")
    return finish(report, True, "passed", "DOCX comparison completed with deterministic evidence.")


def write_report_file(report, output_path, force=False):
    if not enforce_output_path(report, None, output_path, force, "evidence report"):
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return True


def next_safe_commands(path, output_dir):
    input_arg = rel(path)
    output_arg = rel(output_dir)
    render_available = bool(shutil.which("soffice") or shutil.which("libreoffice"))
    commands = [
        f"python -B .agents/skills/document-artifacts/scripts/word/word_tools.py inspect --file {input_arg} --json",
        f"python -B .agents/skills/document-artifacts/scripts/word/word_tools.py to-markdown --file {input_arg} --output {output_arg}/content.md --write --json",
        f"python -B .agents/skills/document-artifacts/scripts/word/word_tools.py extract-assets --file {input_arg} --output-dir {output_arg}/assets --write --json",
        "python -B .agents/skills/document-artifacts/scripts/word/word_tools.py compare --before <before.docx> --after <after.docx> --json",
    ]
    render_command = f"python -B .agents/skills/document-artifacts/scripts/word/word_tools.py render --file {input_arg} --output-dir {output_arg}/rendered --write --json"
    commands.append(render_command if render_available else f"{render_command}  # renderer unavailable; run doctor for capability details")
    return {"kind": "next-safe-commands", "render_available": render_available, "commands": commands}


def bundle_evidence(path, output_dir, write, force=False):
    report = base_report("bundle-evidence", path)
    if write and not enforce_output_dir(report, [path], output_dir, "DOCX evidence bundle"):
        return finish(report, False, "blocked", "DOCX evidence bundle output directory failed safety checks.")
    reports = {
        "inspect": inspect_docx(path),
        "metadata": metadata_report(path),
        "links": links_report(path),
        "outline": outline_report(path),
        "accessibility": accessibility_report(path),
        "comments": comments_report(path),
        "tracked-changes": tracked_changes_report(path),
    }
    reports["to-markdown"] = to_markdown(path, output_dir / "content.md" if write else None, force=force, include_metadata=True, include_links=True, include_outline=True, include_assets=True)
    reports["extract-assets"] = extract_assets(path, output_dir / "assets", write=write, force=force)
    report["evidence"].append({"kind": "evidence-bundle", "reports": {name: report_digest(item) for name, item in reports.items()}})
    next_commands = next_safe_commands(path, output_dir)
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
    return finish(report, True, "passed" if write else "planned", "DOCX evidence bundle " + ("written." if write else "planned; no files were written."))


def batch_evidence(paths, output_dir, write, force=False):
    report = base_report("batch")
    if write and not enforce_output_dir(report, paths, output_dir, "DOCX batch"):
        return finish(report, False, "blocked", "DOCX batch output directory failed safety checks.")
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
    return finish(report, ok, "passed" if write and ok else "planned" if ok else "failed", f"Processed {len(paths)} DOCX file(s) for batch evidence.")


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
    parser = argparse.ArgumentParser(description="Word document helpers")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--install-python-deps", action="store_true")
    add_report_options(doctor)
    for name in ["inspect", "extract-markdown", "comments", "tracked-changes", "metadata", "links", "outline", "accessibility"]:
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
    markdown.add_argument("--max-paragraphs", type=int, default=500)
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
        return print_report(inspect_docx(args.file), **opts)
    if args.command == "extract-markdown":
        return print_report(extract_markdown(args.file), **opts)
    if args.command == "comments":
        return print_report(comments_report(args.file), **opts)
    if args.command == "tracked-changes":
        return print_report(tracked_changes_report(args.file), **opts)
    if args.command == "metadata":
        return print_report(metadata_report(args.file), **opts)
    if args.command == "links":
        return print_report(links_report(args.file), **opts)
    if args.command == "outline":
        return print_report(outline_report(args.file), **opts)
    if args.command == "accessibility":
        return print_report(accessibility_report(args.file), **opts)
    if args.command == "compare":
        return print_report(compare_docx(args.before, args.after), **opts)
    if args.command == "to-markdown":
        return print_report(to_markdown(args.file, args.output, args.force, args.max_paragraphs, args.include_metadata, args.include_links, args.include_outline, args.include_assets, args.include_content), **opts)
    if args.command == "replace-text":
        return print_report(replace_text(args.file, args.find, args.replace, args.output, args.write, args.force, args.verify_output), **opts)
    if args.command == "render":
        return print_report(render_docx(args.file, args.output_dir, args.write), **opts)
    if args.command == "extract-assets":
        return print_report(extract_assets(args.file, args.output_dir, args.write, args.force), **opts)
    if args.command == "bundle-evidence":
        return print_report(bundle_evidence(args.file, args.output_dir, args.write, args.force), **opts)
    if args.command == "batch":
        return print_report(batch_evidence(args.paths, args.output_dir, args.write, args.force), **opts)
    raise AssertionError(args)


if __name__ == "__main__":
    raise SystemExit(main())
