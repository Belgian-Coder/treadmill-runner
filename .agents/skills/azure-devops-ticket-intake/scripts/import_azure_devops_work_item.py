#!/usr/bin/env python3
"""Import an Azure DevOps ticket into a workflow-owned folder."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from intake_support.common import attachment_description
from intake_support.common import attachment_follow_up_commands
from intake_support.common import attachment_type
from intake_support.common import CredentialPreflightError
from intake_support.common import ensure_inside
from intake_support.common import enrich_attachment_entry
from intake_support.common import field
from intake_support.common import find_repo_root
from intake_support.common import load_server_profiles
from intake_support.common import normalize_type
from intake_support.common import redact_secret_like
from intake_support.common import relation_filename
from intake_support.common import retry_after_seconds
from intake_support.common import retry_delay
from intake_support.common import rewrite_description_image_sources
from intake_support.common import slugify
from intake_support.common import strip_html
from intake_support.common import utc_now
from intake_support.common import with_query_param


API_VERSION = "7.1"
COMMENT_API_VERSION = "7.1-preview.4"
USER_AGENT = "repo-azure-devops-ticket-intake"
DEFAULT_SECRETS_FILE = ".agents/local-ai/secrets.local.json"
DEFAULT_RETRIES = 3


def resolve_item_type(
    requested_type: str | None,
    fields: dict[str, Any],
    *,
    allow_default: bool = False,
) -> str:
    field_type_raw = fields.get("System.WorkItemType")
    if requested_type and field_type_raw:
        requested = normalize_type(requested_type)
        detected = normalize_type(str(field_type_raw))
        if requested != detected:
            raise ValueError(
                f"requested work item type {requested!r} conflicts with Azure DevOps type {detected!r}"
        )
        return detected
    if requested_type:
        return normalize_type(requested_type)
    if field_type_raw:
        return normalize_type(str(field_type_raw))
    if allow_default:
        return "story"
    raise ValueError("work item payload is missing System.WorkItemType")


def ado_request(url: str, pat: str, *, timeout: int, retries: int = DEFAULT_RETRIES) -> tuple[bytes, dict[str, str]]:
    token = base64.b64encode(f":{pat}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
                headers = {key.lower(): value for key, value in response.headers.items()}
                return payload, headers
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429 and exc.code < 500:
                raise
            if attempt >= retries:
                raise
            time.sleep(retry_after_seconds(exc) if exc.code == 429 else retry_delay(attempt))
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt >= retries:
                raise
            time.sleep(retry_delay(attempt))
    raise RuntimeError(f"Azure DevOps request failed after retries: {last_error}")


def ado_get_json_page(url: str, pat: str) -> tuple[dict[str, Any], dict[str, str]]:
    payload, headers = ado_request(url, pat, timeout=60)
    return json.loads(payload.decode("utf-8")), headers


def ado_get_json(url: str, pat: str) -> dict[str, Any]:
    payload, _headers = ado_get_json_page(url, pat)
    return payload


def ado_get_json_pages(url: str, pat: str, item_key: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    next_url = url
    seen_tokens: set[str] = set()
    while True:
        data, headers = ado_get_json_page(next_url, pat)
        raw_items = data.get(item_key, [])
        if isinstance(raw_items, list):
            items.extend(item for item in raw_items if isinstance(item, dict))
        continuation = (
            headers.get("x-ms-continuationtoken")
            or headers.get("x-ms-continuation-token")
            or str(data.get("continuationToken", "") or "")
        ).strip()
        next_link = str(data.get("nextLink", "") or data.get("@odata.nextLink", "") or "").strip()
        if next_link:
            next_url = next_link
            continue
        if not continuation or continuation in seen_tokens:
            break
        seen_tokens.add(continuation)
        next_url = with_query_param(url, "continuationToken", continuation)
    return items


def ado_download(url: str, pat: str, max_bytes: int) -> bytes:
    payload, _headers = ado_request(url, pat, timeout=120)
    if len(payload) > max_bytes:
        raise ValueError(f"attachment exceeds max size of {max_bytes} bytes")
    return payload


def apply_server_profile(args: argparse.Namespace) -> None:
    if not args.server_name:
        return
    secrets_file = Path(args.secrets_file or DEFAULT_SECRETS_FILE)
    if not secrets_file.is_absolute():
        secrets_file = find_repo_root(Path.cwd()) / secrets_file
    profiles = load_server_profiles(secrets_file)
    profile = next((item for item in profiles if str(item.get("name", "")) == args.server_name), None)
    if profile is None:
        service = service_kind_for_args(args)
        raise CredentialPreflightError(
            service,
            f"{service} profile {args.server_name!r} was not found in {secrets_file}",
            missing=[f"profile:{args.server_name}"],
            configure_command=configure_command(service, name=args.server_name),
        )
    if not args.organization_url:
        args.organization_url = str(profile.get("organization_url") or profile.get("server_url") or "").rstrip("/")
    if not args.project and profile.get("project"):
        args.project = str(profile["project"])
    if not args.pat:
        env_name = str(profile.get("pat_env", "")).strip()
        args.pat = os.environ.get(env_name, "") if env_name else ""
    if not args.pat and profile.get("pat"):
        args.pat = str(profile["pat"])
    if not args.organization_url:
        service = service_kind_for_args(args)
        raise CredentialPreflightError(
            service,
            f"{service} profile {args.server_name!r} does not define organization_url or server_url",
            missing=["organization-url-or-server-url"],
            configure_command=configure_command(service, name=args.server_name),
        )


def service_kind_for_args(args: argparse.Namespace) -> str:
    value = " ".join(str(item or "") for item in (getattr(args, "organization_url", ""), getattr(args, "server_name", "")))
    return "tfs" if "tfs" in value.lower() else "azure-devops"


def configure_command(service: str, *, name: str | None = None) -> str:
    parts = ["python -B .agents/manage.py credential-doctor --configure", f"--service {service}"]
    if name:
        parts.append(f"--name {name}")
    return " ".join(parts)


def live_import_requested(args: argparse.Namespace) -> bool:
    return bool(args.server_name or (args.work_item_id and (args.organization_url or args.project or args.pat)))


def require_live_service_ready(args: argparse.Namespace) -> None:
    apply_server_profile(args)
    if not live_import_requested(args):
        return
    service = service_kind_for_args(args)
    missing: list[str] = []
    if not args.organization_url:
        missing.append("organization-url" if service == "azure-devops" else "server-url")
    if not args.project:
        missing.append("project")
    pat = args.pat or os.environ.get("AZURE_DEVOPS_PAT")
    if not pat:
        missing.append("pat-env-or-pat")
    if not args.work_item_id:
        missing.append("work-item-id")
    if missing:
        raise CredentialPreflightError(
            service,
            f"{service} live import is missing guided local configuration: {', '.join(missing)}",
            missing=missing,
            configure_command=configure_command(service, name=args.server_name),
        )


def work_item_url(args: argparse.Namespace) -> str:
    base = args.organization_url.rstrip("/")
    project = urllib.parse.quote(args.project.strip("/"))
    item_id = urllib.parse.quote(str(args.work_item_id))
    return f"{base}/{project}/_apis/wit/workitems/{item_id}?$expand=all&api-version={API_VERSION}"


def comments_url(args: argparse.Namespace) -> str:
    base = args.organization_url.rstrip("/")
    project = urllib.parse.quote(args.project.strip("/"))
    item_id = urllib.parse.quote(str(args.work_item_id))
    return f"{base}/{project}/_apis/wit/workItems/{item_id}/comments?api-version={COMMENT_API_VERSION}"


def load_source(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    if args.fixture_json:
        return json.loads(Path(args.fixture_json).read_text(encoding="utf-8")), "fixture"
    require_live_service_ready(args)
    if args.work_item_id and args.organization_url and args.project:
        pat = args.pat or os.environ.get("AZURE_DEVOPS_PAT")
        return ado_get_json(work_item_url(args), pat), "azure-devops"
    fields = {
        "System.Id": args.work_item_id or "manual",
        "System.WorkItemType": args.work_item_type or "story",
        "System.Title": args.title or "Manual intake",
        "System.Description": args.description or "",
        "Microsoft.VSTS.Common.AcceptanceCriteria": args.acceptance_criteria or "",
    }
    return {"id": args.work_item_id or "manual", "fields": fields, "relations": []}, "manual"


def attachment_downloads_enabled(args: argparse.Namespace, source: str) -> bool:
    if getattr(args, "skip_attachments", False):
        return False
    return bool(args.include_attachments or source == "azure-devops")


def load_comments(args: argparse.Namespace, source: str) -> list[dict[str, Any]]:
    if not args.include_comments:
        return []
    if source == "fixture" and args.fixture_json:
        payload = json.loads(Path(args.fixture_json).read_text(encoding="utf-8"))
        comments = payload.get("comments", [])
        return comments if isinstance(comments, list) else []
    if source != "azure-devops":
        return []
    require_live_service_ready(args)
    pat = args.pat or os.environ.get("AZURE_DEVOPS_PAT")
    return ado_get_json_pages(comments_url(args), pat or "", "comments")


def copy_or_download_attachment(
    relation: dict[str, Any],
    target: Path,
    args: argparse.Namespace,
    source: str,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": target.name,
        "relative_path": f"attachments/{target.name}",
        "source_url": relation.get("url"),
        "description": attachment_description(relation),
        "copied": False,
        "size_bytes": 0,
    }
    enrich_attachment_entry(item)
    local_path = relation.get("local_path")
    if local_path:
        source_path = Path(local_path)
        if not source_path.is_absolute() and args.fixture_json:
            source_path = Path(args.fixture_json).resolve().parent / source_path
        data = source_path.read_bytes()
        if len(data) > args.max_attachment_bytes:
            raise ValueError(f"attachment {source_path} exceeds max size")
        target.write_bytes(data)
        item["copied"] = True
        item["size_bytes"] = len(data)
        item["sha256"] = hashlib.sha256(data).hexdigest()
        return enrich_attachment_entry(item)
    if source == "azure-devops":
        require_live_service_ready(args)
        pat = args.pat or os.environ.get("AZURE_DEVOPS_PAT")
        data = ado_download(str(relation.get("url")), pat, args.max_attachment_bytes)
        target.write_bytes(data)
        item["copied"] = True
        item["size_bytes"] = len(data)
        item["sha256"] = hashlib.sha256(data).hexdigest()
    return enrich_attachment_entry(item)


def write_attachments(
    source_payload: dict[str, Any],
    folder: Path,
    args: argparse.Namespace,
    source: str,
) -> list[dict[str, Any]]:
    attachments_dir = folder / "attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    relations = source_payload.get("relations", [])
    if not isinstance(relations, list):
        relations = []
    for index, relation in enumerate(relations, start=1):
        if relation.get("rel") != "AttachedFile":
            continue
        filename = relation_filename(relation, index)
        destination = ensure_inside(attachments_dir, attachments_dir / filename)
        entry = {
            "name": filename,
            "source_url": relation.get("url"),
            "relative_path": f"attachments/{filename}",
            "description": attachment_description(relation),
            "copied": False,
            "size_bytes": 0,
        }
        enrich_attachment_entry(entry)
        if attachment_downloads_enabled(args, source):
            entry = copy_or_download_attachment(relation, destination, args, source)
        manifest.append(entry)
    (attachments_dir / "manifest.json").write_text(
        json.dumps({"attachments": manifest}, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def render_ticket_info(
    item_id: str,
    item_type: str,
    fields: dict[str, Any],
    comments: list[dict[str, Any]],
    attachments: list[dict[str, Any]],
    source: str,
    description_html: str | None = None,
) -> str:
    prefix = {
        "story": "User Story",
        "bug": "Bug",
        "task": "Task",
        "feature": "Feature",
        "epic": "Epic",
    }.get(item_type, "Work Item")
    title = field(fields, "System.Title")
    description = description_html.strip() if description_html and description_html.strip() else field(fields, "System.Description")
    acceptance = field(fields, "Microsoft.VSTS.Common.AcceptanceCriteria")
    repro = field(fields, "Microsoft.VSTS.TCM.ReproSteps")
    severity = field(fields, "Microsoft.VSTS.Common.Severity")
    state = field(fields, "System.State")
    area = field(fields, "System.AreaPath")
    iteration = field(fields, "System.IterationPath")
    comment_lines = []
    for comment in comments:
        author = comment.get("createdBy", {}).get("displayName") if isinstance(comment.get("createdBy"), dict) else ""
        body = strip_html(comment.get("text") or comment.get("renderedText") or "")
        comment_lines.append(f"- {author or 'Unknown'}: {body}" if body else f"- {author or 'Unknown'}: <empty>")
    attachment_lines = [
        f"- {entry['name']} ({entry.get('type', 'other')}, {entry.get('size_bytes', 0)} bytes, copied={str(entry.get('copied', False)).lower()})"
        for entry in attachments
    ]
    return f"""# {prefix} Intake

## Identity

- Work item id: {item_id}
- Work item type: {item_type}
- Source: {source}
- Imported at: {utc_now()}
- Title: {title or '<missing>'}
- State: {state or '<unknown>'}
- Area path: {area or '<unknown>'}
- Iteration path: {iteration or '<unknown>'}

## Description

{description or '<none>'}

## Acceptance Criteria

{acceptance or '<none>'}

## Reproduction Notes

{repro or '<none>'}

## Severity

{severity or '<none>'}

## Comments

{chr(10).join(comment_lines) if comment_lines else '- None imported'}

## Attachments

{chr(10).join(attachment_lines) if attachment_lines else '- None recorded'}

## Intake Review

- [ ] Confirm workflow type matches the work item.
- [ ] Confirm scope, acceptance criteria, and exclusions.
- [ ] Confirm references and local project paths.
- [ ] Record missing information before planning.

## Intake Facts

- Facts above are copied from the selected intake source.

## User Assumptions

- Record user-provided assumptions here before planning.

## Generated Summaries

- Keep generated summaries separate from source facts.
"""


def output_folder(output_root: Path, item_id: str, item_type: str, title: str) -> Path:
    prefix = {
        "story": "US",
        "bug": "BUG",
        "task": "TASK",
        "feature": "FEATURE",
        "epic": "EPIC",
    }.get(item_type, "WI")
    slug = slugify(title, "untitled")
    folder_name = f"{prefix}-{slugify(str(item_id), 'manual')}-{slug}"
    return ensure_inside(output_root, output_root / folder_name)


def existing_intake_for_work_item(output_root: Path, item_id: str) -> Path | None:
    if not output_root.exists():
        return None
    for intake_path in output_root.glob("*/intake.json"):
        try:
            data = json.loads(intake_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(data.get("work_item_id", "")) == str(item_id):
            return intake_path.parent
    return None


def resolve_output_root(args: argparse.Namespace) -> Path:
    output_root = Path(args.output_root).resolve()
    if args.workflow_root:
        workflow_root = Path(args.workflow_root).resolve()
        if output_root != workflow_root and workflow_root not in output_root.parents:
            raise ValueError(f"output root escapes workflow root: {output_root}")
    return output_root


def planned_files(folder: Path, include_attachments: bool) -> list[str]:
    files = [
        str(folder / "ticket-info.md"),
        str(folder / "intake.json"),
        str(folder / "fields.json"),
        str(folder / "relations.json"),
        str(folder / "comments.json"),
        str(folder / "plan.md"),
        str(folder / "execution-log.md"),
        str(folder / "pr-description.md"),
        str(folder / "attachments" / "manifest.json"),
    ]
    if include_attachments:
        files.append(str(folder / "attachments" / "<downloaded-or-copied-attachments>"))
    return files


def intake_plan(args: argparse.Namespace) -> dict[str, Any]:
    output_root = resolve_output_root(args)
    payload, source = load_source(args)
    fields = payload.get("fields", {})
    if not isinstance(fields, dict):
        raise ValueError("work item payload must contain a fields object")
    item_type = resolve_item_type(args.work_item_type, fields, allow_default=source == "manual")
    item_id = str(payload.get("id") or fields.get("System.Id") or args.work_item_id or "manual")
    title = field(fields, "System.Title") or args.title or "Manual intake"
    folder = output_folder(output_root, item_id, item_type, title)
    duplicate = existing_intake_for_work_item(output_root, item_id)
    return {
        "ok": duplicate is None or args.force,
        "status": "planned",
        "source": source,
        "work_item_id": item_id,
        "work_item_type": item_type,
        "title": title,
        "output_folder": str(folder),
        "duplicate_folder": str(duplicate) if duplicate else "",
        "planned_files": planned_files(folder, attachment_downloads_enabled(args, source)),
    }


def write_intake(args: argparse.Namespace) -> Path:
    output_root = resolve_output_root(args)
    output_root.mkdir(parents=True, exist_ok=True)
    payload, source = load_source(args)
    fields = payload.get("fields", {})
    if not isinstance(fields, dict):
        raise ValueError("work item payload must contain a fields object")
    item_type = resolve_item_type(args.work_item_type, fields, allow_default=source == "manual")
    relations = payload.get("relations", [])
    if not isinstance(relations, list):
        relations = []
    item_id = str(payload.get("id") or fields.get("System.Id") or args.work_item_id or "manual")
    title = field(fields, "System.Title") or args.title or "Manual intake"
    duplicate = existing_intake_for_work_item(output_root, item_id)
    if duplicate and not args.force:
        raise FileExistsError(f"duplicate intake exists for work item {item_id}: {duplicate}")
    folder = output_folder(output_root, item_id, item_type, title)
    if folder.exists() and not args.force:
        raise FileExistsError(f"output folder already exists: {folder}")
    folder.mkdir(parents=True, exist_ok=True)
    comments = load_comments(args, source)
    attachments = write_attachments(payload, folder, args, source)
    description_html_source = str(fields.get("System.Description", "") or "")
    description_html_local = rewrite_description_image_sources(description_html_source, attachments)
    ticket_info = render_ticket_info(
        item_id,
        item_type,
        fields,
        comments,
        attachments,
        source,
        description_html=description_html_local,
    )
    (folder / "ticket-info.md").write_text(ticket_info, encoding="utf-8")
    (folder / "fields.json").write_text(json.dumps(redact_secret_like(fields), indent=2) + "\n", encoding="utf-8")
    (folder / "relations.json").write_text(json.dumps(redact_secret_like(relations), indent=2) + "\n", encoding="utf-8")
    (folder / "comments.json").write_text(json.dumps(redact_secret_like(comments), indent=2) + "\n", encoding="utf-8")
    intake_payload = {
        "imported_at": utc_now(),
        "source": source,
        "source_details": {
            "organization_url": args.organization_url or "",
            "project": args.project or "",
            "work_item_url": work_item_url(args) if source == "azure-devops" else "",
            "comments_url": comments_url(args) if source == "azure-devops" and args.include_comments else "",
        },
        "work_item_id": item_id,
        "work_item_type": item_type,
        "title": title,
        "comments_imported": len(comments),
        "attachments_recorded": len(attachments),
        "fields": redact_secret_like(fields),
        "relations": redact_secret_like(relations),
        "comments": redact_secret_like(comments),
        "attachments": attachments,
        "description_html": {
            "source": description_html_source,
            "local": description_html_local,
        },
        "intake_facts": {
            "description": strip_html(description_html_local),
            "acceptance_criteria": field(fields, "Microsoft.VSTS.Common.AcceptanceCriteria"),
            "repro_steps": field(fields, "Microsoft.VSTS.TCM.ReproSteps"),
        },
        "user_assumptions": [],
        "generated_summaries": [],
    }
    if args.include_raw_source:
        intake_payload["raw_source"] = redact_secret_like(payload)
        intake_payload["comments"] = redact_secret_like(comments)
    (folder / "intake.json").write_text(json.dumps(intake_payload, indent=2) + "\n", encoding="utf-8")
    for name in ("plan.md", "execution-log.md", "pr-description.md"):
        path = folder / name
        if not path.exists():
            path.write_text(f"# {name.removesuffix('.md').replace('-', ' ').title()}\n\n- [ ] Not started\n", encoding="utf-8")
    return folder


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-item-id")
    parser.add_argument("--work-item-type", choices=["story", "bug", "task", "feature", "epic", "user story", "defect", "issue"])
    parser.add_argument("--title")
    parser.add_argument("--description")
    parser.add_argument("--acceptance-criteria")
    parser.add_argument("--organization-url")
    parser.add_argument("--project")
    parser.add_argument("--pat")
    parser.add_argument("--server-name", help="server profile name from .agents/local-ai/secrets.local.json")
    parser.add_argument("--secrets-file", default=DEFAULT_SECRETS_FILE)
    parser.add_argument("--fixture-json")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--workflow-root", help="optional owning workflow root; output-root must stay inside it")
    parser.add_argument("--include-comments", action="store_true")
    parser.add_argument(
        "--include-attachments",
        action="store_true",
        help="copy fixture/manual attachments; Azure DevOps attachments are downloaded by default",
    )
    parser.add_argument(
        "--skip-attachments",
        action="store_true",
        help="record attachment metadata but do not download or copy attachment bytes",
    )
    parser.add_argument("--include-raw-source", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="show target folder and files without writing")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--max-attachment-bytes", type=int, default=50 * 1024 * 1024)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.dry_run:
            plan = intake_plan(args)
            if args.format == "json":
                print(json.dumps(plan, indent=2))
            else:
                print(f"planned intake folder: {plan['output_folder']}")
                if plan["duplicate_folder"]:
                    print(f"duplicate intake: {plan['duplicate_folder']}")
                for path in plan["planned_files"]:
                    print(f"- {path}")
            return 0 if plan["ok"] else 1
        folder = write_intake(args)
    except Exception as exc:
        setup = exc.guidance() if isinstance(exc, CredentialPreflightError) else None
        if args.format == "json":
            payload: dict[str, Any] = {"ok": False, "error": str(exc)}
            if setup:
                payload["credential_setup"] = setup
            print(json.dumps(payload, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
            if setup:
                print(f"Configure: {setup['configure_command']}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps({"ok": True, "output_folder": str(folder)}, indent=2))
    else:
        print(f"created intake folder: {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
