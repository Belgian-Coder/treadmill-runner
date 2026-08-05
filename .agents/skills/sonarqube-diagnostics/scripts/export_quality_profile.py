#!/usr/bin/env python3
"""Export SonarQube quality profile metadata."""

from __future__ import annotations

import argparse
import json

from sonarqube_client import SonarClient, evidence_payload, failure_payload, require_target, write_json, write_markdown

TOOL_NAME = "sonarqube-diagnostics.export_quality_profile"


def export(args: argparse.Namespace) -> dict[str, object]:
    require_target(args, project_required=False)
    client = SonarClient(args.base_url, args.token)
    response = client.get_json(
        "/api/qualityprofiles/search",
        {"project": args.project_key, "language": args.language, "qualityProfile": args.quality_profile},
    )
    profiles = response.get("profiles", [])
    payload = evidence_payload(
        TOOL_NAME,
        True,
        {"profile_count": len(profiles)},
        project_key=args.project_key,
        profiles=profiles,
    )
    write_json(args.output_json, payload)
    lines = [f"- Project key: {args.project_key}", f"- Profiles exported: {len(profiles)}", "", "## Profiles", ""]
    for profile in profiles:
        lines.append(f"- `{profile.get('language', '<unknown>')}` {profile.get('name', '<unnamed>')}")
    write_markdown(args.output_md, "SonarQube Quality Profiles", lines)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="network/read-only-no-upload: export SonarQube quality profile metadata")
    parser.add_argument("--base-url", help="network SonarQube base URL")
    parser.add_argument("--server-name", help="profile name from .agents/local-ai/secrets.local.json")
    parser.add_argument("--secrets-file", help="override the local profile store path")
    parser.add_argument("--project-key", help="SonarQube project key")
    parser.add_argument("--language")
    parser.add_argument("--quality-profile")
    parser.add_argument("--token", help="credential value; prefer SONAR_TOKEN or a configured profile")
    parser.add_argument("--output-json", help="write JSON evidence to this path")
    parser.add_argument("--output-md", help="write Markdown evidence to this path")
    args = parser.parse_args(argv)
    try:
        payload = export(args)
    except Exception as error:
        payload = failure_payload(TOOL_NAME, error, project_key=args.project_key)
        write_json(args.output_json, payload)
        write_markdown(args.output_md, "SonarQube Quality Profiles", [f"- Project key: {args.project_key}", f"- Export failed: {error}"])
        output = {"ok": False, "error": str(error)}
        if "credential_setup" in payload:
            output["credential_setup"] = payload["credential_setup"]
        print(json.dumps(output, indent=2))
        return 1
    print(json.dumps({"ok": True, "profile_count": len(payload["profiles"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
