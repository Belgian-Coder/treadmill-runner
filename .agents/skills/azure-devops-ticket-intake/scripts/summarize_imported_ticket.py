#!/usr/bin/env python3
"""Validate and summarize an imported Azure DevOps ticket folder."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
TOOL = "azure-devops-ticket-intake.summarize-imported-ticket"
REQUIRED_FILES = ("ticket-info.md", "intake.json", "fields.json", "relations.json", "comments.json")
OPTIONAL_ATTACHMENT_MANIFEST = Path("attachments") / "manifest.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> tuple[Any, str]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except OSError as exc:
        return None, str(exc)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"


def file_evidence(import_folder: Path, relative_path: Path) -> dict[str, Any]:
    path = import_folder / relative_path
    item: dict[str, Any] = {
        "path": relative_path.as_posix(),
        "present": path.is_file(),
    }
    if path.is_file():
        item["size_bytes"] = path.stat().st_size
        item["sha256"] = sha256_file(path)
    return item


def safe_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def sorted_mapping_keys(value: Any) -> list[str]:
    return sorted(str(key) for key in value.keys()) if isinstance(value, dict) else []


def attachment_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".doc", ".docx", ".rtf"}:
        return "word-document"
    if suffix in {".xls", ".xlsx", ".xlsm", ".csv", ".tsv"}:
        return "spreadsheet"
    if suffix in {".ppt", ".pptx"}:
        return "presentation"
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}:
        return "image"
    if suffix in {".log", ".txt", ".md", ".json", ".xml", ".yaml", ".yml"}:
        return "text-log"
    if suffix in {".zip", ".7z", ".rar", ".tar", ".gz"}:
        return "archive"
    if suffix in {".har", ".etl", ".dmp", ".trace", ".trx"}:
        return "trace"
    return "other"


def attachment_follow_up_commands(filename: str, relative_path: str) -> list[str]:
    kind = attachment_type(filename)
    if kind == "pdf":
        return [
            f"python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py bundle-evidence --file {relative_path} --output-dir <evidence-dir>/pdf --write --json",
            f"python -B .agents/manage.py local-ai document inspect --file {relative_path} --json  # optional; fallback: document-artifacts bundle-evidence",
            f"python -B .agents/manage.py local-ai vision pdf --pdf {relative_path} --pages 1-5  # optional; fallback: document-artifacts render-pages or manual review",
        ]
    if kind == "word-document":
        return [
            f"python -B .agents/skills/document-artifacts/scripts/word/word_tools.py bundle-evidence --file {relative_path} --output-dir <evidence-dir>/docx --write --json",
            f"python -B .agents/manage.py local-ai document inspect --file {relative_path} --json  # optional; fallback: document-artifacts bundle-evidence",
        ]
    if kind == "spreadsheet":
        return [
            f"python -B .agents/skills/document-artifacts/scripts/excel/excel_tools.py bundle-evidence --file {relative_path} --output-dir <evidence-dir>/xlsx --write --json",
            f"python -B .agents/manage.py local-ai document inspect --file {relative_path} --json  # optional; fallback: document-artifacts bundle-evidence",
        ]
    if kind == "presentation":
        return [
            f"python -B .agents/skills/document-artifacts/scripts/powerpoint/powerpoint_tools.py bundle-evidence --file {relative_path} --output-dir <evidence-dir>/pptx --write --json",
            f"python -B .agents/manage.py local-ai document inspect --file {relative_path} --json  # optional; fallback: document-artifacts bundle-evidence",
        ]
    if kind == "image":
        return [f"python -B .agents/manage.py local-ai vision describe --image {relative_path} --json  # optional; fallback: attachment manifest hash plus manual visual review"]
    if kind == "text-log":
        return [f"python -B .agents/manage.py local-ai task --task inventory-summary --input {relative_path}  # optional; fallback: inspect the text/log directly and cite lines"]
    if kind == "archive":
        return [
            "python -B .agents/skills/dotnet-security-review/scripts/scanner/scan_security_patterns.py --target <extracted-safe-folder> --fail-on high"
        ]
    if kind == "trace":
        return [
            f"python -B .agents/skills/dotnet-quality-gates/scripts/validate_local_quality.py --test-result {relative_path} --output-json <evidence-dir>/trace-quality.json --output-md <evidence-dir>/trace-quality.md"
        ]
    return []


def load_required_files(import_folder: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    parsed: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []
    for name in REQUIRED_FILES:
        path = import_folder / name
        if not path.is_file():
            checks.append({"id": f"file:{name}", "ok": False, "severity": "error", "message": "missing required file"})
            continue
        if name.endswith(".json"):
            data, error = read_json(path)
            if error:
                checks.append({"id": f"json:{name}", "ok": False, "severity": "error", "message": error})
            else:
                parsed[name] = data
                checks.append({"id": f"json:{name}", "ok": True, "severity": "info", "message": "parsed"})
        else:
            parsed[name] = path.read_text(encoding="utf-8", errors="replace")
            checks.append({"id": f"file:{name}", "ok": True, "severity": "info", "message": "present"})
    return parsed, checks


def normalize_attachment(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {
            "name": "",
            "relative_path": "",
            "copied": False,
            "size_bytes": 0,
            "sha256": "",
            "type": "other",
            "suggested_follow_up_commands": [],
        }
    name = str(entry.get("name", ""))
    relative_path = str(entry.get("relative_path", ""))
    kind = str(entry.get("type", "") or attachment_type(name or relative_path) or "other")
    commands = entry.get("suggested_follow_up_commands")
    if not isinstance(commands, list):
        commands = attachment_follow_up_commands(name or Path(relative_path).name, relative_path)
    return {
        "name": name,
        "relative_path": relative_path,
        "copied": bool(entry.get("copied", False)),
        "size_bytes": int(entry.get("size_bytes", 0) or 0),
        "sha256": str(entry.get("sha256", "")),
        "source_url_present": bool(entry.get("source_url")),
        "description": str(entry.get("description", "")),
        "type": kind,
        "suggested_follow_up_commands": [
            str(item) for item in commands if str(item).strip()
        ],
    }


def attachment_summary(import_folder: Path, intake: Any, checks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    manifest_path = import_folder / OPTIONAL_ATTACHMENT_MANIFEST
    manifest_attachments: list[Any] = []
    manifest_error = ""
    manifest_present = manifest_path.is_file()
    if manifest_present:
        manifest, manifest_error = read_json(manifest_path)
        if isinstance(manifest, dict) and isinstance(manifest.get("attachments"), list):
            manifest_attachments = manifest["attachments"]
            checks.append({"id": "json:attachments/manifest.json", "ok": True, "severity": "info", "message": "parsed"})
        else:
            checks.append(
                {
                    "id": "json:attachments/manifest.json",
                    "ok": False,
                    "severity": "error",
                    "message": manifest_error or "manifest must contain an attachments list",
                }
            )
    intake_attachments = intake.get("attachments", []) if isinstance(intake, dict) else []
    expected_manifest = bool(manifest_attachments or intake_attachments or (import_folder / "attachments").exists())
    if expected_manifest and not manifest_present:
        checks.append(
            {
                "id": "file:attachments/manifest.json",
                "ok": False,
                "severity": "warning",
                "message": "attachment folder or intake attachment records exist without a manifest",
            }
        )
    source_entries = manifest_attachments if manifest_present else intake_attachments
    attachments = [normalize_attachment(entry) for entry in source_entries if isinstance(entry, dict)]
    attachment_files: list[dict[str, Any]] = []
    for entry in attachments:
        relative_path = entry["relative_path"]
        if not relative_path:
            continue
        evidence = file_evidence(import_folder, Path(relative_path))
        if entry["copied"] and not evidence["present"]:
            checks.append(
                {
                    "id": f"attachment:{relative_path}",
                    "ok": False,
                    "severity": "warning",
                    "message": "manifest says copied but file is missing",
                }
            )
        attachment_files.append(evidence)
    return attachments, attachment_files


def build_report(import_folder: Path) -> dict[str, Any]:
    folder = import_folder.resolve()
    checks: list[dict[str, Any]] = []
    skipped: list[str] = []
    if not folder.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "tool": TOOL,
            "ok": False,
            "status": "missing",
            "import_folder": str(folder),
            "summary": {"message": "Import folder does not exist."},
            "ticket": {},
            "counts": {},
            "attachments": [],
            "files": [],
            "checks": [{"id": "folder", "ok": False, "severity": "error", "message": "folder missing"}],
            "skipped": skipped,
        }
    if not folder.is_dir():
        return {
            "schema_version": SCHEMA_VERSION,
            "tool": TOOL,
            "ok": False,
            "status": "invalid",
            "import_folder": str(folder),
            "summary": {"message": "Import path is not a directory."},
            "ticket": {},
            "counts": {},
            "attachments": [],
            "files": [],
            "checks": [{"id": "folder", "ok": False, "severity": "error", "message": "not a directory"}],
            "skipped": skipped,
        }

    parsed, required_checks = load_required_files(folder)
    checks.extend(required_checks)
    intake = parsed.get("intake.json") if isinstance(parsed.get("intake.json"), dict) else {}
    fields = parsed.get("fields.json") if isinstance(parsed.get("fields.json"), dict) else {}
    relations = parsed.get("relations.json") if isinstance(parsed.get("relations.json"), list) else []
    comments = parsed.get("comments.json") if isinstance(parsed.get("comments.json"), list) else []
    attachments, attachment_files = attachment_summary(folder, intake, checks)

    missing_required = [name for name in REQUIRED_FILES if not (folder / name).is_file()]
    parse_failures = [check["id"] for check in checks if not check["ok"] and str(check["id"]).startswith("json:")]
    error_count = sum(1 for check in checks if not check["ok"] and check.get("severity") == "error")
    warning_count = sum(1 for check in checks if not check["ok"] and check.get("severity") == "warning")
    status = "complete"
    if missing_required or parse_failures:
        status = "partial"
    if error_count and len(missing_required) == len(REQUIRED_FILES):
        status = "missing"

    files = [file_evidence(folder, Path(name)) for name in REQUIRED_FILES]
    if (folder / OPTIONAL_ATTACHMENT_MANIFEST).exists():
        files.append(file_evidence(folder, OPTIONAL_ATTACHMENT_MANIFEST))
    files.extend(attachment_files)
    files = sorted(files, key=lambda item: item["path"])

    title = str(intake.get("title") or fields.get("System.Title") or "")
    work_item_id = str(intake.get("work_item_id") or fields.get("System.Id") or "")
    work_item_type = str(intake.get("work_item_type") or fields.get("System.WorkItemType") or "")
    source = str(intake.get("source") or "")
    ticket = {
        "work_item_id": work_item_id,
        "work_item_type": work_item_type,
        "title": title,
        "source": source,
        "state": str(fields.get("System.State", "")) if isinstance(fields, dict) else "",
        "area_path": str(fields.get("System.AreaPath", "")) if isinstance(fields, dict) else "",
        "iteration_path": str(fields.get("System.IterationPath", "")) if isinstance(fields, dict) else "",
    }
    counts = {
        "fields": len(fields) if isinstance(fields, dict) else 0,
        "relations": safe_count(relations),
        "comments": safe_count(comments),
        "attachments": len(attachments),
        "files_present": sum(1 for item in files if item["present"]),
        "missing_required_files": len(missing_required),
        "warnings": warning_count,
        "errors": error_count,
    }
    summary = {
        "message": "Import folder is complete." if status == "complete" else "Import folder is missing required evidence.",
        "missing_required_files": missing_required,
        "parse_failures": parse_failures,
        "field_keys": sorted_mapping_keys(fields),
    }
    if not attachments:
        skipped.append("attachment hash visibility - no attachments were recorded")
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL,
        "ok": error_count == 0,
        "status": status,
        "import_folder": str(folder),
        "summary": summary,
        "ticket": ticket,
        "counts": counts,
        "attachments": sorted(attachments, key=lambda item: (item["relative_path"], item["name"])),
        "files": files,
        "checks": sorted(checks, key=lambda item: item["id"]),
        "skipped": skipped,
    }


def render_markdown(report: dict[str, Any]) -> str:
    ticket = report.get("ticket", {})
    counts = report.get("counts", {})
    lines = [
        "# Azure DevOps Ticket Import Summary",
        "",
        f"- Status: {report.get('status')}",
        f"- OK: {str(report.get('ok')).lower()}",
        f"- Import folder: {report.get('import_folder')}",
        f"- Work item: {ticket.get('work_item_id') or '<unknown>'}",
        f"- Type: {ticket.get('work_item_type') or '<unknown>'}",
        f"- Title: {ticket.get('title') or '<missing>'}",
        f"- Source: {ticket.get('source') or '<unknown>'}",
        "",
        "## Counts",
        "",
    ]
    for key in sorted(counts):
        lines.append(f"- {key}: {counts[key]}")
    missing = report.get("summary", {}).get("missing_required_files", [])
    if missing:
        lines.extend(["", "## Missing Evidence", ""])
        lines.extend(f"- {item}" for item in missing)
    attachments = report.get("attachments", [])
    lines.extend(["", "## Attachments", ""])
    if attachments:
        for entry in attachments:
            digest = entry.get("sha256") or "<missing hash>"
            lines.append(
                f"- {entry.get('relative_path') or entry.get('name') or '<unnamed>'}: "
                f"{entry.get('type', 'other')}, {entry.get('size_bytes', 0)} bytes, "
                f"copied={str(entry.get('copied')).lower()}, sha256={digest}"
            )
            for command in entry.get("suggested_follow_up_commands", []):
                lines.append(f"  - Follow-up: `{command}`")
    else:
        lines.append("- None recorded")
    failed = [check for check in report.get("checks", []) if not check.get("ok")]
    lines.extend(["", "## Checks", ""])
    if failed:
        for check in failed:
            lines.append(f"- {check.get('severity')}: {check.get('id')} - {check.get('message')}")
    else:
        lines.append("- All deterministic checks passed")
    lines.append("")
    return "\n".join(lines)


def write_explicit(path_text: str | None, content: str) -> None:
    if not path_text:
        return
    path = Path(path_text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("import_folder", help="Path to an import folder produced by import_azure_devops_work_item.py")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Console output format")
    parser.add_argument("--output-json", help="Optional explicit path for stable JSON evidence")
    parser.add_argument("--output-markdown", help="Optional explicit path for the human Markdown summary")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(Path(args.import_folder))
    json_text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    markdown_text = render_markdown(report)
    write_explicit(args.output_json, json_text)
    write_explicit(args.output_markdown, markdown_text)
    if args.format == "json":
        print(json_text, end="")
    else:
        print(markdown_text, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
