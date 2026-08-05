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
from pathlib import Path

import pdf_evidence_workflows

sys.dont_write_bytecode = True

SKILL_NAME = "document-artifacts"
REPO_ROOT = Path(__file__).resolve().parents[5]
LOCAL_DEPS = REPO_ROOT / ".agents" / ".deps" / SKILL_NAME
if LOCAL_DEPS.exists() and str(LOCAL_DEPS) not in sys.path:
    sys.path.insert(0, str(LOCAL_DEPS))

PYTHON_DEPENDENCIES = {
    "pypdf": "pypdf==6.10.2",
    "pdfplumber": "pdfplumber==0.11.9",
    "reportlab": "reportlab==4.4.10",
}

PDF_TEXT_RE = re.compile(rb"\(([^()\r\n]{2,240})\)\s*Tj|\[([^\]]{2,800})\]\s*TJ")
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
MAX_MARKDOWN_ITEMS = 100
INPUT_PDF_MISSING = "input PDF does not exist"
INPUT_PDF_NOT_FOUND = "Input PDF was not found."
PYPDF_UNAVAILABLE_PREFIX = "pypdf unavailable or could not parse file;"


def optional_module(name):
    try:
        return __import__(name)
    except Exception:
        return None


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


def rel(path):
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


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
        "format": "pdf",
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


validate_report_shape = functools.partial(pdf_evidence_workflows.validate_report_shape, sys.modules[__name__])
enforce_output_path = functools.partial(pdf_evidence_workflows.enforce_output_path, sys.modules[__name__])
enforce_output_dir = functools.partial(pdf_evidence_workflows.enforce_output_dir, sys.modules[__name__])
append_shape_validation = functools.partial(pdf_evidence_workflows.append_shape_validation, sys.modules[__name__])
report_digest = pdf_evidence_workflows.report_digest


def detect_capabilities():
    return {
        "python": True,
        "pypdf": optional_module("pypdf") is not None,
        "pdfplumber": optional_module("pdfplumber") is not None,
        "reportlab": optional_module("reportlab") is not None,
        "pdftoppm": shutil.which("pdftoppm") is not None,
        "mutool": shutil.which("mutool") is not None,
    }


def read_pdf_bytes(path):
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(path)
    return path.read_bytes()


def fallback_text(data, limit=50):
    texts = []
    for match in PDF_TEXT_RE.finditer(data[:2_000_000]):
        raw = match.group(1) or match.group(2) or b""
        text = raw.decode("latin-1", errors="ignore")
        text = " ".join(text.split())
        if text:
            texts.append(text)
        if len(texts) >= limit:
            break
    return texts


def pypdf_reader(path):
    pypdf = optional_module("pypdf")
    if pypdf is None:
        return None
    try:
        return pypdf.PdfReader(str(path))
    except Exception:
        return None


def pdf_security_markers(data):
    return {
        "encrypt_marker": b"/Encrypt" in data,
        "javascript_marker": b"/JavaScript" in data or b"/JS" in data,
        "embedded_file_marker": b"/EmbeddedFile" in data or b"/Filespec" in data,
        "signature_marker": b"/Sig" in data or b"/ByteRange" in data,
        "permissions_marker": b"/Perms" in data,
        "annotations_marker": b"/Annots" in data,
        "launch_action_marker": b"/Launch" in data,
        "remote_goto_marker": b"/GoToR" in data,
        "open_action_marker": b"/OpenAction" in data,
        "xfa_marker": b"/XFA" in data,
    }


def sanitize_asset_name(name, fallback):
    name = name.strip().replace("\\", "/").split("/")[-1] or fallback
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return safe or fallback


def ensure_output_child(output_dir, target):
    root = output_dir.resolve()
    try:
        return target.resolve().is_relative_to(root)
    except ValueError:
        return False


def write_asset_manifest(report, output_dir, assets, force=False):
    manifest = {
        "schema_version": 1,
        "tool": report["tool"],
        "command": report["command"],
        "input_path": report["input_path"],
        "input_sha256": report["input_sha256"],
        "assets": assets,
    }
    manifest_path = output_dir / "asset-manifest.json"
    if not enforce_output_path(report, None, manifest_path, force, "asset manifest"):
        return False
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    report["writes"].append(rel(manifest_path))
    report["artifacts"].append({"kind": "asset-manifest", "path": rel(manifest_path), "assets": len(assets)})
    return True


def pdf_metadata_report(path):
    report = base_report("metadata", path)
    try:
        data = read_pdf_bytes(path)
    except FileNotFoundError:
        report["blocked"].append(INPUT_PDF_MISSING)
        return finish(report, False, "blocked", INPUT_PDF_NOT_FOUND)
    markers = pdf_security_markers(data)
    metadata = {
        "header": data[:16].decode("latin-1", errors="ignore").strip(),
        "xmp_marker": b"/Metadata" in data or b"<?xpacket" in data,
        "root_marker": b"/Root" in data,
        **markers,
    }
    reader = pypdf_reader(path)
    if reader is not None:
        docinfo = getattr(reader, "metadata", None)
        metadata["pages"] = len(reader.pages)
        metadata["encrypted"] = bool(getattr(reader, "is_encrypted", False))
        metadata["document_info"] = {str(key): str(value) for key, value in dict(docinfo or {}).items()}
    else:
        report["skipped"].append(f"{PYPDF_UNAVAILABLE_PREFIX} metadata is limited to byte markers")
        metadata["approx_page_markers"] = data.count(b"/Type /Page")
    report["evidence"].append({"kind": "pdf-metadata", **metadata})
    if metadata.get("encrypted") or markers["encrypt_marker"]:
        report["warnings"].append("PDF encryption/protection markers detected.")
    if markers["javascript_marker"]:
        report["warnings"].append("PDF JavaScript/action markers detected.")
    return finish(report, True, "passed", "PDF metadata inspected with deterministic evidence.")


def pdf_links_report(path):
    report = base_report("links", path)
    try:
        data = read_pdf_bytes(path)
    except FileNotFoundError:
        report["blocked"].append(INPUT_PDF_MISSING)
        return finish(report, False, "blocked", INPUT_PDF_NOT_FOUND)
    links = []
    for raw in re.findall(rb"/URI\s*\(([^)]{1,500})\)", data[:5_000_000]):
        links.append({"kind": "uri", "target": raw.decode("latin-1", errors="ignore")})
    if b"/Launch" in data:
        links.append({"kind": "launch-action", "target": ""})
        report["warnings"].append("PDF launch action marker detected.")
    if b"/GoToR" in data:
        links.append({"kind": "remote-goto-action", "target": ""})
        report["warnings"].append("PDF remote goto action marker detected.")
    reader = pypdf_reader(path)
    if reader is not None:
        for page_index, page in enumerate(reader.pages, start=1):
            try:
                annots = page.get("/Annots") or []
            except Exception:
                annots = []
            for annot in annots:
                try:
                    obj = annot.get_object()
                    action = obj.get("/A") or {}
                    uri = action.get("/URI")
                    subtype = str(obj.get("/Subtype", ""))
                    if uri:
                        links.append({"kind": "annotation-uri", "page": page_index, "subtype": subtype, "target": str(uri)})
                except Exception:
                    continue
    else:
        report["skipped"].append(f"{PYPDF_UNAVAILABLE_PREFIX} annotation link detail is limited")
    report["evidence"].append({"kind": "pdf-links", "count": len(links), "links": links[:100]})
    return finish(report, True, "passed", f"Found {len(links)} PDF link/action marker(s).")


def flatten_pdf_outline(items, depth=1):
    flattened = []
    if not isinstance(items, list):
        items = [items]
    for item in items:
        if isinstance(item, list):
            flattened.extend(flatten_pdf_outline(item, depth + 1))
            continue
        title = getattr(item, "title", None)
        if title is None and isinstance(item, dict):
            title = item.get("/Title") or item.get("title")
        if title:
            flattened.append({"level": depth, "title": str(title)})
    return flattened


def pdf_outline_report(path):
    report = base_report("outline", path)
    reader = pypdf_reader(path)
    if reader is None:
        report["skipped"].append(f"{PYPDF_UNAVAILABLE_PREFIX} outline/bookmarks cannot be listed")
        return finish(report, True, "passed", "PDF outline inspection completed with limited fallback evidence.")
    try:
        outline = flatten_pdf_outline(getattr(reader, "outline", []))
    except Exception:
        outline = []
        report["skipped"].append("pypdf could not read the PDF outline")
    labels = []
    try:
        labels = [str(label) for label in (getattr(reader, "page_labels", []) or [])[:100]]
    except Exception:
        report["skipped"].append("page labels could not be read")
    report["evidence"].append({"kind": "pdf-outline", "items": outline[:100], "page_labels": labels})
    return finish(report, True, "passed", f"Found {len(outline)} outline item(s).")


def pdf_accessibility_report(path):
    report = base_report("accessibility", path)
    try:
        data = read_pdf_bytes(path)
    except FileNotFoundError:
        report["blocked"].append(INPUT_PDF_MISSING)
        return finish(report, False, "blocked", INPUT_PDF_NOT_FOUND)
    markers = {
        "tagged_structure": b"/StructTreeRoot" in data,
        "mark_info": b"/MarkInfo" in data,
        "language": b"/Lang" in data,
        "alternate_text": b"/Alt" in data,
        "annotations": b"/Annots" in data,
        "images": b"/Subtype /Image" in data or b"/Image" in data,
    }
    if not markers["tagged_structure"]:
        report["warnings"].append("No tagged PDF structure marker detected.")
    if not markers["language"]:
        report["warnings"].append("No PDF language marker detected.")
    if markers["images"] and not markers["alternate_text"]:
        report["warnings"].append("Image markers found without detectable alternate-text markers.")
    report["evidence"].append({"kind": "pdf-accessibility-markers", **markers})
    return finish(report, True, "passed", "PDF accessibility markers inspected deterministically.")


def collect_pdf_assets(path, report):
    assets = []
    payloads = []
    reader = pypdf_reader(path)
    if reader is None:
        try:
            data = read_pdf_bytes(path)
        except FileNotFoundError:
            report["blocked"].append(INPUT_PDF_MISSING)
            return assets, payloads
        image_markers = data.count(b"/Subtype /Image")
        embedded_markers = data.count(b"/EmbeddedFile") + data.count(b"/Filespec")
        report["skipped"].append(f"{PYPDF_UNAVAILABLE_PREFIX} asset extraction is limited to marker inventory")
        assets.append({"kind": "image-marker", "name": "pdf-image-markers", "count": image_markers, "extractable": False})
        assets.append({"kind": "embedded-file-marker", "name": "pdf-embedded-file-markers", "count": embedded_markers, "extractable": False})
        return assets, payloads
    attachments = getattr(reader, "attachments", {}) or {}
    if callable(attachments):
        try:
            attachments = attachments()
        except Exception:
            attachments = {}
    if isinstance(attachments, dict):
        for name, values in attachments.items():
            items = values if isinstance(values, list) else [values]
            for index, data in enumerate(items, start=1):
                if isinstance(data, str):
                    data = data.encode("utf-8")
                if isinstance(data, bytes):
                    safe_name = sanitize_asset_name(str(name), f"attachment-{index}.bin")
                    assets.append({"kind": "attachment", "name": safe_name, "size_bytes": len(data), "extractable": True})
                    payloads.append((safe_name, data))
    for page_index, page in enumerate(reader.pages, start=1):
        try:
            images = getattr(page, "images", []) or []
        except Exception:
            images = []
        for image_index, image in enumerate(images, start=1):
            data = getattr(image, "data", None)
            name = sanitize_asset_name(str(getattr(image, "name", "")), f"page-{page_index}-image-{image_index}.bin")
            if isinstance(data, bytes):
                assets.append({"kind": "image", "name": name, "page": page_index, "size_bytes": len(data), "extractable": True})
                payloads.append((name, data))
            else:
                assets.append({"kind": "image", "name": name, "page": page_index, "extractable": False})
    return assets, payloads


def extract_assets(path, output_dir, write, force=False):
    report = base_report("extract-assets", path)
    assets, payloads = collect_pdf_assets(path, report)
    if report["blocked"]:
        return finish(report, False, "blocked", "PDF assets could not be inspected.")
    output_root = output_dir.resolve()
    planned = []
    for name, data in payloads:
        target = output_root / sanitize_asset_name(name, "asset.bin")
        planned.append({"name": name, "path": rel(target), "size_bytes": len(data)})
        if not ensure_output_child(output_root, target):
            report["blocked"].append(f"asset target escapes output directory: {target}")
        if write and target.exists() and not force:
            report["blocked"].append(f"asset target already exists; pass --force to overwrite: {rel(target)}")
    report["evidence"].append({"kind": "pdf-assets", "assets": assets, "planned_targets": planned})
    if report["blocked"]:
        return finish(report, False, "blocked", "Asset output path containment failed.")
    if not write:
        return finish(report, True, "planned", f"Found {len(assets)} PDF asset marker(s); no files were written.")
    output_root.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in payloads:
        target = output_root / sanitize_asset_name(name, "asset.bin")
        target.write_bytes(data)
        asset_record = {"name": name, "path": rel(target), "sha256": file_sha256(target), "size_bytes": file_size(target)}
        written.append(asset_record)
        report["writes"].append(rel(target))
        report["artifacts"].append({"kind": "extracted-asset", **asset_record})
    if not write_asset_manifest(report, output_root, written, force):
        return finish(report, False, "blocked", "Asset manifest output path failed safety checks.")
    return finish(report, True, "passed", f"Extracted {len(written)} PDF asset file(s) and wrote an asset manifest.")


def inspect_pdf(path):
    report = base_report("inspect", path)
    try:
        data = read_pdf_bytes(path)
    except FileNotFoundError:
        report["blocked"].append(INPUT_PDF_MISSING)
        return finish(report, False, "blocked", INPUT_PDF_NOT_FOUND)
    header_ok = data.startswith(b"%PDF-")
    report["checks"].append({"name": "pdf-header", "ok": header_ok})
    if not header_ok:
        report["blocked"].append("file does not start with %PDF-")
        return finish(report, False, "blocked", "File is not recognized as a PDF.")
    markers = pdf_security_markers(data)
    report["evidence"].append({"kind": "pdf-markers", **markers})
    if markers["encrypt_marker"]:
        report["warnings"].append("PDF contains encryption markers; content extraction may require a password.")
    if markers["javascript_marker"]:
        report["warnings"].append("PDF contains JavaScript/action markers.")
    if markers["launch_action_marker"] or markers["remote_goto_marker"] or markers["open_action_marker"]:
        report["warnings"].append("PDF action markers detected; review before opening in a desktop reader.")
    if markers["xfa_marker"]:
        report["warnings"].append("XFA form markers detected; form handling may be viewer-dependent.")
    if markers["embedded_file_marker"]:
        report["findings"].append("Embedded file markers detected.")
    if markers["signature_marker"]:
        report["findings"].append("Signature markers detected.")
    reader = pypdf_reader(path)
    if reader is not None:
        page_count = len(reader.pages)
        fields = reader.get_fields() or {}
        encrypted = bool(getattr(reader, "is_encrypted", False))
        report["evidence"].append({"kind": "pypdf", "pages": page_count, "form_fields": len(fields), "encrypted": encrypted})
    else:
        report["skipped"].append(f"{PYPDF_UNAVAILABLE_PREFIX} page count and fields are limited")
        report["evidence"].append({"kind": "fallback", "bytes": len(data), "approx_page_markers": data.count(b"/Type /Page")})
    texts = fallback_text(data, limit=10)
    report["evidence"].append({"kind": "fallback-text", "hits": len(texts), "excerpt": " ".join(texts)[:500]})
    report["findings"].append("Selectable text detected." if texts else "No simple selectable text detected in sampled bytes.")
    if b"/Annots" in data:
        report["findings"].append("Annotation markers detected.")
    return finish(report, True, "passed", "PDF inspection completed with deterministic evidence.")


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
                "pdftoppm": shutil.which("pdftoppm") or "",
                "mutool": shutil.which("mutool") or "",
            },
        }
    )
    if not caps["pypdf"]:
        report["skipped"].append("pypdf unavailable: page counts, field lists, and PDF writes may be limited")
    if not caps["pdfplumber"]:
        report["skipped"].append("pdfplumber unavailable: richer text/table extraction is not available")
    if not (caps["pdftoppm"] or caps["mutool"]):
        report["skipped"].append("no PDF renderer found on PATH: render-pages will plan or skip")
    report["commands"].extend(
        [
            "python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py inspect --file <file.pdf> --json",
            "python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py extract-text --file <file.pdf> --json",
            "python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py compare --before <old.pdf> --after <new.pdf> --json",
        ]
    )
    ok = not any("install failed" in issue for issue in report["issues"])
    return finish(report, ok, "passed" if ok else "failed", "PDF helper readiness checked.")


def pdf_metrics(path):
    inspect = inspect_pdf(path)
    text = extract_text(path)
    forms = inspect_forms(path)
    fields = []
    if forms.get("evidence"):
        fields = forms["evidence"][0].get("fields", [])
    page_count = 0
    for item in inspect.get("evidence", []):
        if item.get("kind") == "pypdf":
            page_count = int(item.get("pages", 0))
        if item.get("kind") == "fallback":
            page_count = int(item.get("approx_page_markers", 0))
    text_chars = 0
    text_excerpt = ""
    for item in text.get("evidence", []):
        if item.get("kind") == "text":
            text_chars = int(item.get("characters", 0))
            text_excerpt = str(item.get("excerpt", ""))
    return {
        "path": rel(path),
        "sha256": file_sha256(path),
        "size_bytes": file_size(path),
        "ok": inspect["ok"],
        "status": inspect["status"],
        "pages": page_count,
        "text_characters": text_chars,
        "text_excerpt": text_excerpt,
        "form_fields": fields,
        "findings": inspect.get("findings", []),
        "warnings": inspect.get("warnings", []),
    }


def compare_pdfs(before, after):
    report = base_report("compare", before)
    report["evidence"].append({"kind": "compare-inputs", "before": rel(before), "after": rel(after), "after_sha256": file_sha256(after), "after_size_bytes": file_size(after)})
    if not before.exists() or not after.exists():
        report["blocked"].append("both --before and --after PDF files must exist")
        return finish(report, False, "blocked", "Compare requires two existing PDF files.")
    before_metrics = pdf_metrics(before)
    after_metrics = pdf_metrics(after)
    differences = {
        "pages_changed": before_metrics["pages"] != after_metrics["pages"],
        "text_characters_changed": before_metrics["text_characters"] != after_metrics["text_characters"],
        "form_fields_changed": before_metrics["form_fields"] != after_metrics["form_fields"],
        "sha256_changed": before_metrics["sha256"] != after_metrics["sha256"],
    }
    report["evidence"].append({"kind": "pdf-compare", "before": before_metrics, "after": after_metrics, "differences": differences})
    changed = [name for name, value in differences.items() if value]
    if changed:
        report["findings"].append("Changed: " + ", ".join(changed))
    else:
        report["findings"].append("No deterministic PDF differences detected.")
    return finish(report, True, "passed", "PDF comparison completed with deterministic evidence.")


def extract_text(path):
    report = base_report("extract-text", path)
    try:
        data = read_pdf_bytes(path)
    except FileNotFoundError:
        report["blocked"].append(INPUT_PDF_MISSING)
        return finish(report, False, "blocked", INPUT_PDF_NOT_FOUND)
    chunks = []
    reader = pypdf_reader(path)
    if reader is not None:
        for page in reader.pages[:100]:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if text.strip():
                chunks.append(text.strip())
        report["evidence"].append({"kind": "pypdf-text", "chunks": len(chunks)})
    if not chunks:
        chunks = fallback_text(data)
        report["skipped"].append("library extraction unavailable or empty; used limited byte-pattern fallback")
    text = "\n\n".join(chunks)
    report["evidence"].append({"kind": "text", "characters": len(text), "excerpt": " ".join(text.split())[:800]})
    status = "passed" if text else "skipped"
    return finish(report, bool(text), status, "Extracted selectable text." if text else "No selectable text extracted deterministically.")


def markdown_from_pdf(
    path,
    max_pages=100,
    include_metadata=False,
    include_links=False,
    include_outline=False,
    include_assets=False,
):
    skipped = []
    warnings = []
    try:
        data = read_pdf_bytes(path)
    except FileNotFoundError:
        return "", skipped, [INPUT_PDF_MISSING]
    chunks = []
    reader = pypdf_reader(path)
    if reader is not None:
        page_count = len(reader.pages)
        if page_count > max_pages:
            skipped.append(f"markdown truncated after {max_pages} page(s)")
        for page_index in range(min(page_count, max_pages)):
            page = reader.pages[page_index]
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if text.strip():
                chunks.append(f"## Page {page_index + 1}\n\n{text.strip()}")
    if not chunks:
        fallback = fallback_text(data)
        if fallback:
            chunks.append("\n\n".join(fallback))
            skipped.append("library page extraction unavailable or empty; used limited byte-pattern fallback")
        else:
            skipped.append("no selectable text extracted deterministically")
    markers = pdf_security_markers(data)
    if markers["encrypt_marker"]:
        warnings.append("PDF contains encryption markers; markdown may be incomplete.")
    lines = [f"# {path.name}", "", *chunks]
    if include_metadata:
        lines.extend(["", "## Metadata Evidence", "", "```json", json.dumps(report_digest(pdf_metadata_report(path)), indent=2, sort_keys=True), "```"])
    if include_links:
        lines.extend(["", "## Link Evidence", "", "```json", json.dumps(report_digest(pdf_links_report(path)), indent=2, sort_keys=True), "```"])
    if include_outline:
        lines.extend(["", "## Outline Evidence", "", "```json", json.dumps(report_digest(pdf_outline_report(path)), indent=2, sort_keys=True), "```"])
    if include_assets:
        asset_report = extract_assets(path, Path("_assets"), write=False)
        lines.extend(["", "## Asset Evidence", "", "```json", json.dumps(report_digest(asset_report), indent=2, sort_keys=True), "```"])
    return "\n\n".join(lines).strip() + "\n", skipped, warnings


def to_markdown(
    path,
    output,
    force=False,
    max_pages=100,
    include_metadata=False,
    include_links=False,
    include_outline=False,
    include_assets=False,
    include_content=False,
):
    report = base_report("to-markdown", path)
    markdown, skipped, warnings = markdown_from_pdf(path, max_pages, include_metadata, include_links, include_outline, include_assets)
    report["skipped"].extend(skipped)
    report["warnings"].extend(warnings)
    if not markdown.strip():
        report["blocked"].append("no markdown content could be produced")
        return finish(report, False, "blocked", "PDF markdown conversion produced no content.")
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
    return finish(report, True, "passed", "Converted PDF text evidence to Markdown.")


def render_pages(path, output_dir, write):
    report = base_report("render-pages", path)
    renderer = shutil.which("pdftoppm") or shutil.which("mutool")
    if not renderer:
        report["skipped"].append("no supported PDF renderer found on PATH: pdftoppm or mutool")
        return finish(report, False, "skipped", "Rendering skipped because no supported renderer is available.")
    if not write:
        report["commands"].append([renderer, "<args omitted>", str(path)])
        report["skipped"].append("render command planned only; pass --write with --output-dir to create images")
        return finish(report, True, "planned", "Renderer is available; no files were written.")
    if output_dir is None:
        report["blocked"].append("--output-dir is required with --write")
        return finish(report, False, "blocked", "Rendering requires an explicit output directory.")
    if not enforce_output_dir(report, [path], output_dir, "render-pages"):
        return finish(report, False, "blocked", "Rendering output directory failed safety checks.")
    output_dir.mkdir(parents=True, exist_ok=True)
    if Path(renderer).name.lower().startswith("pdftoppm"):
        command = [renderer, "-png", str(path), str(output_dir / path.stem)]
    else:
        command = [renderer, "draw", "-o", str(output_dir / f"{path.stem}-%d.png"), str(path)]
    started = time.monotonic()
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60, check=False)
    report["commands"].append(command)
    report["evidence"].append({"kind": "renderer-output", "returncode": result.returncode, "duration_seconds": round(time.monotonic() - started, 3), "output": result.stdout[-1000:]})
    files = sorted(output_dir.glob(f"{path.stem}*.png"))
    report["writes"] = [rel(item) for item in files]
    return finish(report, result.returncode == 0, "passed" if result.returncode == 0 else "failed", f"Rendered {len(files)} page image(s).")


def inspect_forms(path):
    report = base_report("forms inspect", path)
    reader = pypdf_reader(path)
    if reader is None:
        report["skipped"].append(f"{PYPDF_UNAVAILABLE_PREFIX} field names cannot be listed")
        return finish(report, False, "skipped", "Form inspection needs pypdf for reliable field listing.")
    fields = reader.get_fields() or {}
    report["evidence"].append({"kind": "form-fields", "fields": sorted(fields.keys())})
    return finish(report, True, "passed", f"Found {len(fields)} form field(s).")


def fill_forms(path, values_path, output_path, write, force=False, verify_output=False):
    report = base_report("forms fill", path)
    try:
        values = json.loads(values_path.read_text(encoding="utf-8"))
    except Exception as exc:
        report["blocked"].append(f"values file is not valid JSON: {exc}")
        return finish(report, False, "blocked", "Form fill requires a JSON values file.")
    if not isinstance(values, dict):
        report["blocked"].append("values JSON must be an object")
        return finish(report, False, "blocked", "Form values must be a JSON object.")
    fields_report = inspect_forms(path)
    known_fields = set(fields_report.get("evidence", [{}])[0].get("fields", [])) if fields_report["ok"] else set()
    missing = sorted(set(values) - known_fields) if known_fields else sorted(values)
    report["evidence"].append({"kind": "fill-plan", "requested_fields": sorted(values), "unknown_fields": missing})
    if not write:
        return finish(report, True, "planned", "Form fill dry-run completed; no PDF was written.")
    pypdf = optional_module("pypdf")
    if pypdf is None:
        report["blocked"].append("pypdf unavailable; cannot write filled PDF")
        return finish(report, False, "blocked", "Writing filled PDFs requires pypdf.")
    if output_path is None:
        report["blocked"].append("--output is required with --write")
        return finish(report, False, "blocked", "Writing requires an explicit output path.")
    if not enforce_output_path(report, path, output_path, force, "filled PDF"):
        return finish(report, False, "blocked", "Filled PDF output path failed safety checks.")
    reader = pypdf.PdfReader(str(path))
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    for page in writer.pages:
        writer.update_page_form_field_values(page, values)
    with output_path.open("wb") as handle:
        writer.write(handle)
    report["writes"].append(rel(output_path))
    if verify_output:
        verification = compare_pdfs(path, output_path)
        report["evidence"].append({"kind": "output-verification", "report": report_digest(verification)})
        if not verification["ok"]:
            report["issues"].append("output verification failed")
            return finish(report, False, "failed", "Filled PDF was written but output verification failed.")
    return finish(report, True, "passed", "Filled PDF was written to the explicit output path.")


def validate_pdf(path):
    report = inspect_pdf(path)
    report["command"] = "validate"
    if report["ok"]:
        report["summary"] = "PDF passed basic deterministic validation."
    return report


write_report_file = functools.partial(pdf_evidence_workflows.write_report_file, sys.modules[__name__])
bundle_evidence = functools.partial(pdf_evidence_workflows.bundle_evidence, sys.modules[__name__])
batch_evidence = functools.partial(pdf_evidence_workflows.batch_evidence, sys.modules[__name__])


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
    fallback_used = any(str(item.get("kind", "")).lower() == "fallback" for item in report.get("evidence", []) if isinstance(item, dict))
    strict_blocker = report.get("status") == "skipped" or fallback_used or any(term in skipped_text for term in ["fallback", "renderer", "rendering", "library extraction"])
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
    parser = argparse.ArgumentParser(description="PDF processing helpers")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--install-python-deps", action="store_true")
    add_report_options(doctor)
    for name in ["inspect", "extract-text", "validate", "metadata", "links", "outline", "accessibility"]:
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
    markdown.add_argument("--max-pages", type=int, default=100)
    markdown.add_argument("--include-metadata", action="store_true")
    markdown.add_argument("--include-links", action="store_true")
    markdown.add_argument("--include-outline", action="store_true")
    markdown.add_argument("--include-assets", action="store_true")
    markdown.add_argument("--include-content", action="store_true", help="Embed the complete converted Markdown in report evidence; use --json or --output-json to emit it.")
    add_report_options(markdown)
    render = sub.add_parser("render-pages")
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
    forms = sub.add_parser("forms")
    forms_sub = forms.add_subparsers(dest="forms_command", required=True)
    forms_inspect = forms_sub.add_parser("inspect")
    forms_inspect.add_argument("--file", required=True, type=Path)
    add_report_options(forms_inspect)
    forms_fill = forms_sub.add_parser("fill")
    forms_fill.add_argument("--file", required=True, type=Path)
    forms_fill.add_argument("--values", required=True, type=Path)
    forms_fill.add_argument("--output", type=Path)
    forms_fill.add_argument("--verify-output", action="store_true")
    mode = forms_fill.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--write", action="store_true")
    add_report_options(forms_fill)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    opts = {"as_json": args.json, "output_json": getattr(args, "output_json", None), "output_md": getattr(args, "output_md", None), "strict": getattr(args, "strict", False), "force": getattr(args, "force", False)}
    if args.command == "doctor":
        return print_report(doctor_report(args.install_python_deps), **opts)
    if args.command == "inspect":
        return print_report(inspect_pdf(args.file), **opts)
    if args.command == "extract-text":
        return print_report(extract_text(args.file), **opts)
    if args.command == "validate":
        return print_report(validate_pdf(args.file), **opts)
    if args.command == "metadata":
        return print_report(pdf_metadata_report(args.file), **opts)
    if args.command == "links":
        return print_report(pdf_links_report(args.file), **opts)
    if args.command == "outline":
        return print_report(pdf_outline_report(args.file), **opts)
    if args.command == "accessibility":
        return print_report(pdf_accessibility_report(args.file), **opts)
    if args.command == "compare":
        return print_report(compare_pdfs(args.before, args.after), **opts)
    if args.command == "to-markdown":
        return print_report(to_markdown(args.file, args.output, args.force, args.max_pages, args.include_metadata, args.include_links, args.include_outline, args.include_assets, args.include_content), **opts)
    if args.command == "render-pages":
        return print_report(render_pages(args.file, args.output_dir, args.write), **opts)
    if args.command == "extract-assets":
        return print_report(extract_assets(args.file, args.output_dir, args.write, args.force), **opts)
    if args.command == "forms" and args.forms_command == "inspect":
        return print_report(inspect_forms(args.file), **opts)
    if args.command == "forms" and args.forms_command == "fill":
        return print_report(fill_forms(args.file, args.values, args.output, args.write, args.force, args.verify_output), **opts)
    if args.command == "bundle-evidence":
        return print_report(bundle_evidence(args.file, args.output_dir, args.write, args.force), **opts)
    if args.command == "batch":
        return print_report(batch_evidence(args.paths, args.output_dir, args.write, args.force), **opts)
    raise AssertionError(args)


if __name__ == "__main__":
    raise SystemExit(main())
