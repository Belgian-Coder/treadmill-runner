"""Pure helpers for Azure DevOps ticket intake."""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any


class CredentialPreflightError(ValueError):
    """Raised when a live external-service call is missing guided config."""

    def __init__(self, service: str, message: str, *, missing: list[str], configure_command: str) -> None:
        super().__init__(message)
        self.service = service
        self.missing = missing
        self.configure_command = configure_command

    def guidance(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "missing": self.missing,
            "configure_command": self.configure_command,
            "secret_store": ".agents/local-ai/secrets.local.json",
            "gitignore_managed": True,
            "required_inputs": [
                "profile name",
                "Azure DevOps organization URL or TFS collection/server URL",
                "project name",
                "PAT source such as AZURE_DEVOPS_PAT",
            ],
            "token_policy": "Prefer a PAT environment variable; store the PAT only after explicit user approval.",
        }


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def strip_html(value: Any) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def redact_secret_like(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if re.search(r"(pat|token|cookie|secret|password|authorization)", str(key), re.IGNORECASE):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = redact_secret_like(item)
        return redacted
    if isinstance(value, list):
        return [redact_secret_like(item) for item in value]
    if isinstance(value, str) and re.search(
        r"(pat|token|cookie|secret|password|authorization)",
        value,
        re.IGNORECASE,
    ):
        return "<redacted>"
    return value


def slugify(value: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    text = re.sub(r"-{2,}", "-", text)
    return (text or fallback)[:80]


def ensure_inside(root: Path, candidate: Path) -> Path:
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    if candidate_resolved != root_resolved and root_resolved not in candidate_resolved.parents:
        raise ValueError(f"path escapes output root: {candidate}")
    return candidate_resolved


def normalize_type(value: str | None, fields: dict[str, Any] | None = None) -> str:
    raw = value or ""
    if not raw and fields:
        raw = str(fields.get("System.WorkItemType", ""))
    lowered = raw.lower().strip()
    if lowered in {"user story", "story", "product backlog item", "pbi", "us"}:
        return "story"
    if lowered in {"bug", "defect", "issue"}:
        return "bug"
    if lowered in {"task"}:
        return "task"
    if lowered in {"feature"}:
        return "feature"
    if lowered in {"epic"}:
        return "epic"
    raise ValueError(f"unsupported work item type: {raw or '<missing>'}")


def retry_after_seconds(error: urllib.error.HTTPError) -> float:
    raw = error.headers.get("Retry-After", "") if error.headers else ""
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 1.0


def retry_delay(attempt: int) -> float:
    return min(8.0, 0.5 * (2 ** max(0, attempt - 1)))


def with_query_param(url: str, name: str, value: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    query[name] = [value]
    new_query = urllib.parse.urlencode(query, doseq=True)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".agents").exists() or (candidate / "AGENTS.md").exists():
            return candidate
    return current


def load_server_profiles(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    profiles: list[dict[str, Any]] = []
    if isinstance(data, dict):
        for key in ("azure_devops", "tfs", "servers"):
            raw = data.get(key, [])
            if isinstance(raw, list):
                profiles.extend(item for item in raw if isinstance(item, dict))
    return profiles


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


def enrich_attachment_entry(entry: dict[str, Any]) -> dict[str, Any]:
    name = str(entry.get("name", ""))
    relative_path = str(entry.get("relative_path") or f"attachments/{name}")
    entry["type"] = attachment_type(name)
    entry["suggested_follow_up_commands"] = attachment_follow_up_commands(name, relative_path)
    return entry


def normalize_attachment_source(value: Any) -> set[str]:
    raw = str(value or "").strip()
    if not raw:
        return set()
    unescaped = html.unescape(raw)
    parsed = urllib.parse.urlparse(unescaped)
    base_without_query = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    values = {raw, unescaped}
    if base_without_query:
        values.add(base_without_query)
    query = urllib.parse.parse_qs(parsed.query)
    for filename in query.get("fileName", []):
        if filename:
            values.add(filename)
            values.add(slugify(filename, filename))
    basename = Path(parsed.path).name
    if basename:
        values.add(basename)
    return {item for item in values if item}


def rewrite_description_image_sources(description: Any, attachments: list[dict[str, Any]]) -> str:
    text = "" if description is None else str(description)
    source_map: dict[str, str] = {}
    for entry in attachments:
        relative_path = str(entry.get("relative_path") or f"attachments/{entry.get('name', '')}").replace("\\", "/")
        if not relative_path or relative_path == "attachments/":
            continue
        for key in normalize_attachment_source(entry.get("source_url")):
            source_map[key] = relative_path
        for key in normalize_attachment_source(entry.get("name")):
            source_map[key] = relative_path

    def replace(match: re.Match[str]) -> str:
        prefix, src, suffix = match.groups()
        candidates = normalize_attachment_source(src)
        target = next((source_map[candidate] for candidate in candidates if candidate in source_map), "")
        if not target:
            return match.group(0)
        return f"{prefix}{target}{suffix}"

    return re.sub(r"(<img\b[^>]*\bsrc=[\"'])([^\"']+)([\"'])", replace, text, flags=re.IGNORECASE)


def relation_filename(relation: dict[str, Any], index: int) -> str:
    attributes = relation.get("attributes") if isinstance(relation.get("attributes"), dict) else {}
    parsed = urllib.parse.urlparse(str(relation.get("url", "")))
    query = urllib.parse.parse_qs(parsed.query)
    raw_name = attributes.get("name") or next(iter(query.get("fileName", [])), "")
    raw_name = raw_name or Path(parsed.path).name
    return slugify(str(raw_name), f"attachment-{index}")


def attachment_description(relation: dict[str, Any]) -> str:
    attributes = relation.get("attributes") if isinstance(relation.get("attributes"), dict) else {}
    values = [
        str(attributes.get("comment", "")).strip(),
        str(attributes.get("name", "")).strip(),
        str(relation.get("url", "")).strip(),
    ]
    return next((value for value in values if value), "Attached file")


def field(fields: dict[str, Any], name: str) -> str:
    return strip_html(fields.get(name, ""))
