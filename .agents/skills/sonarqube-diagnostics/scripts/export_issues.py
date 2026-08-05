#!/usr/bin/env python3
"""Export SonarQube issues."""

from __future__ import annotations

import argparse
import json

from sonarqube_client import SonarClient, evidence_payload, failure_payload, normalized_issue, redacted_url, require_target, sarif_from_issues, write_json, write_markdown

TOOL_NAME = "sonarqube-diagnostics.export_issues"


def export(args: argparse.Namespace) -> dict[str, object]:
    require_target(args)
    client = SonarClient(args.base_url, args.token)
    issues: list[dict[str, object]] = []
    total = None
    for page in range(1, args.max_pages + 1):
        response = client.get_json(
            "/api/issues/search",
            {"componentKeys": args.project_key, "ps": args.page_size, "p": page, "resolved": args.resolved},
        )
        issues.extend(response.get("issues", []))
        paging = response.get("paging", {})
        total = paging.get("total", len(issues)) if isinstance(paging, dict) else len(issues)
        if len(issues) >= int(total):
            break
    truncated = total is not None and len(issues) < int(total)
    payload = evidence_payload(
        TOOL_NAME,
        not truncated,
        {"issue_count": len(issues), "total": total, "truncated": truncated},
        project_key=args.project_key,
        base_url=redacted_url(args.base_url),
        issue_count=len(issues),
        total=total,
        truncated=truncated,
        issues=issues,
        normalized_issues=[normalized_issue(issue) for issue in issues if isinstance(issue, dict)],
    )
    write_json(args.output_json, payload)
    if args.output_sarif:
        write_json(args.output_sarif, sarif_from_issues(issues))
    lines = [f"- Project key: {args.project_key}", f"- Issues exported: {len(issues)}", "", "## Issues", ""]
    for issue in issues[: args.markdown_limit]:
        lines.append(f"- `{issue.get('severity', 'UNKNOWN')}` {issue.get('component', '')}: {issue.get('message', '')}")
    write_markdown(args.output_md, "SonarQube Issues", lines)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="network/read-only-no-upload: export SonarQube issues")
    parser.add_argument("--base-url", help="network SonarQube base URL")
    parser.add_argument("--project-key", help="SonarQube project key")
    parser.add_argument("--server-name", help="profile name from .agents/local-ai/secrets.local.json")
    parser.add_argument("--secrets-file", help="override the local profile store path")
    parser.add_argument("--token", help="credential value; prefer SONAR_TOKEN or a configured profile")
    parser.add_argument("--resolved", default="false")
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--markdown-limit", type=int, default=50)
    parser.add_argument("--output-json", help="write JSON evidence to this path")
    parser.add_argument("--output-md", help="write Markdown evidence to this path")
    parser.add_argument("--output-sarif", help="write SARIF evidence to this path")
    args = parser.parse_args(argv)
    try:
        payload = export(args)
    except Exception as error:
        payload = failure_payload(TOOL_NAME, error, project_key=args.project_key or "")
        write_json(args.output_json, payload)
        write_markdown(args.output_md, "SonarQube Issues", [f"- Project key: {args.project_key}", f"- Export failed: {error}"])
        output = {"ok": False, "error": str(error)}
        if "credential_setup" in payload:
            output["credential_setup"] = payload["credential_setup"]
        print(json.dumps(output, indent=2))
        return 1
    print(json.dumps({"ok": True, "issue_count": payload["issue_count"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
