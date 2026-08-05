#!/usr/bin/env python3
"""Export SonarQube security hotspots."""

from __future__ import annotations

import argparse
import json

from sonarqube_client import SonarClient, evidence_payload, failure_payload, require_target, write_json, write_markdown

TOOL_NAME = "sonarqube-diagnostics.export_hotspots"


def export(args: argparse.Namespace) -> dict[str, object]:
    require_target(args)
    client = SonarClient(args.base_url, args.token)
    hotspots: list[dict[str, object]] = []
    total = None
    for page in range(1, args.max_pages + 1):
        response = client.get_json("/api/hotspots/search", {"projectKey": args.project_key, "ps": args.page_size, "p": page})
        hotspots.extend(response.get("hotspots", []))
        paging = response.get("paging", {})
        total = paging.get("total", len(hotspots)) if isinstance(paging, dict) else len(hotspots)
        if len(hotspots) >= int(total):
            break
    truncated = total is not None and len(hotspots) < int(total)
    payload = evidence_payload(
        TOOL_NAME,
        not truncated,
        {"hotspot_count": len(hotspots), "total": total, "truncated": truncated},
        project_key=args.project_key,
        hotspot_count=len(hotspots),
        total=total,
        truncated=truncated,
        hotspots=hotspots,
    )
    write_json(args.output_json, payload)
    lines = [f"- Project key: {args.project_key}", f"- Hotspots exported: {len(hotspots)}", "", "## Hotspots", ""]
    for hotspot in hotspots[: args.markdown_limit]:
        lines.append(f"- `{hotspot.get('status', 'UNKNOWN')}` {hotspot.get('component', '')}: {hotspot.get('message', '')}")
    write_markdown(args.output_md, "SonarQube Hotspots", lines)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="network/read-only-no-upload: export SonarQube security hotspots")
    parser.add_argument("--base-url", help="network SonarQube base URL")
    parser.add_argument("--project-key", help="SonarQube project key")
    parser.add_argument("--server-name", help="profile name from .agents/local-ai/secrets.local.json")
    parser.add_argument("--secrets-file", help="override the local profile store path")
    parser.add_argument("--token", help="credential value; prefer SONAR_TOKEN or a configured profile")
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--markdown-limit", type=int, default=50)
    parser.add_argument("--output-json", help="write JSON evidence to this path")
    parser.add_argument("--output-md", help="write Markdown evidence to this path")
    args = parser.parse_args(argv)
    try:
        payload = export(args)
    except Exception as error:
        payload = failure_payload(TOOL_NAME, error, project_key=args.project_key or "")
        write_json(args.output_json, payload)
        write_markdown(args.output_md, "SonarQube Hotspots", [f"- Project key: {args.project_key}", f"- Export failed: {error}"])
        output = {"ok": False, "error": str(error)}
        if "credential_setup" in payload:
            output["credential_setup"] = payload["credential_setup"]
        print(json.dumps(output, indent=2))
        return 1
    print(json.dumps({"ok": True, "hotspot_count": payload["hotspot_count"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
