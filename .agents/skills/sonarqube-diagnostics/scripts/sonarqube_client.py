#!/usr/bin/env python3
"""Small SonarQube HTTP helpers used by diagnostics scripts."""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_SECRETS_FILE = ".agents/local-ai/secrets.local.json"


class CredentialPreflightError(ValueError):
    """Raised when a live SonarQube call lacks guided local configuration."""

    def __init__(self, message: str, *, missing: list[str], configure_command: str) -> None:
        super().__init__(message)
        self.missing = missing
        self.configure_command = configure_command

    def guidance(self) -> dict[str, Any]:
        return {
            "service": "sonarqube",
            "missing": self.missing,
            "configure_command": self.configure_command,
            "secret_store": ".agents/local-ai/secrets.local.json",
            "gitignore_managed": True,
            "required_inputs": ["profile name", "base URL", "project key", "token source such as SONAR_TOKEN"],
            "token_policy": "Prefer SONAR_TOKEN; store the token only after explicit user approval.",
        }


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


class SonarClient:
    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token or os.environ.get("SONAR_TOKEN")

    def get_json(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{self.base_url}{endpoint}?{query}"
        headers = {"Accept": "application/json", "User-Agent": "repo-sonarqube-diagnostics"}
        if self.token:
            encoded = base64.b64encode(f"{self.token}:".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"
        request = urllib.request.Request(url, headers=headers)
        attempts = 3
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code == 429 and attempt < attempts:
                    retry_after = exc.headers.get("Retry-After")
                    delay = min(int(retry_after), 30) if retry_after and retry_after.isdigit() else attempt * 2
                    time.sleep(delay)
                    continue
                if 500 <= exc.code < 600 and attempt < attempts:
                    time.sleep(attempt * 2)
                    continue
                raise
            except urllib.error.URLError as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(attempt * 2)
                    continue
                raise
        raise RuntimeError(str(last_error) if last_error else "SonarQube request failed")


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".agents").exists() or (candidate / "AGENTS.md").exists():
            return candidate
    return current


def load_sonarqube_profiles(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    profiles: list[dict[str, Any]] = []
    if isinstance(data, dict):
        raw = data.get("sonarqube", [])
        if isinstance(raw, list):
            profiles.extend(item for item in raw if isinstance(item, dict))
    return profiles


def apply_server_profile(args: Any) -> None:
    server_name = str(getattr(args, "server_name", "") or "").strip()
    if not server_name:
        return
    secrets_file = Path(getattr(args, "secrets_file", "") or DEFAULT_SECRETS_FILE)
    if not secrets_file.is_absolute():
        secrets_file = find_repo_root(Path.cwd()) / secrets_file
    profiles = load_sonarqube_profiles(secrets_file)
    profile = next((item for item in profiles if str(item.get("name", "")) == server_name), None)
    if profile is None:
        raise CredentialPreflightError(
            f"SonarQube profile {server_name!r} was not found in {secrets_file}",
            missing=[f"profile:{server_name}"],
            configure_command=configure_command(name=server_name),
        )
    if not getattr(args, "base_url", None):
        args.base_url = str(profile.get("base_url") or "").rstrip("/")
    if hasattr(args, "project_key") and not getattr(args, "project_key", None):
        args.project_key = str(profile.get("project_key") or "")
    if not getattr(args, "token", None):
        env_name = str(profile.get("token_env", "")).strip()
        args.token = os.environ.get(env_name, "") if env_name else ""
    if not getattr(args, "token", None) and profile.get("token"):
        args.token = str(profile["token"])


def configure_command(*, name: str | None = None) -> str:
    parts = ["python -B .agents/manage.py credential-doctor --configure --service sonarqube"]
    if name:
        parts.append(f"--name {name}")
    return " ".join(parts)


def require_target(args: Any, *, project_required: bool = True, token_required: bool = True) -> None:
    apply_server_profile(args)
    missing: list[str] = []
    if not getattr(args, "base_url", None):
        missing.append("base-url")
    if project_required and not getattr(args, "project_key", None):
        missing.append("project-key")
    token = getattr(args, "token", None) or os.environ.get("SONAR_TOKEN")
    if token_required and not token:
        missing.append("token-env-or-token")
    if missing:
        server_name = str(getattr(args, "server_name", "") or "").strip() or None
        raise CredentialPreflightError(
            f"SonarQube command is missing guided local configuration: {', '.join(missing)}",
            missing=missing,
            configure_command=configure_command(name=server_name),
        )


def write_json(path: str | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def evidence_payload(tool: str, ok: bool, summary: dict[str, Any], **data: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "tool": tool,
        "ok": ok,
        "status": "passed" if ok else "failed",
        "exported_at": utc_now(),
        "read_only": True,
        "no_upload_assertion": True,
        "summary": summary,
        "checks": [
            {
                "name": tool.rsplit(".", 1)[-1],
                "kind": "network-export",
                "ok": ok,
                "status": "passed" if ok else "failed",
                "summary": summary,
            }
        ],
        "skipped": [],
        **data,
    }


def redacted_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value))
    if parsed.username or parsed.password:
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc += f":{parsed.port}"
        return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    return str(value)


def normalized_issue(issue: dict[str, Any]) -> dict[str, Any]:
    severity = str(issue.get("severity") or issue.get("impactSeverity") or "UNKNOWN").upper()
    category = str(issue.get("type") or issue.get("cleanCodeAttributeCategory") or "UNKNOWN").upper()
    return {
        "key": str(issue.get("key", "")),
        "component": str(issue.get("component", "")),
        "message": str(issue.get("message", "")),
        "severity": severity,
        "category": category,
        "line": issue.get("line"),
        "status": str(issue.get("status", "")),
    }


def failure_payload(tool: str, error: Exception, **data: Any) -> dict[str, Any]:
    summary = {"error": str(error), "error_type": type(error).__name__}
    payload = evidence_payload(tool, False, summary, error=str(error), error_type=type(error).__name__, **data)
    if isinstance(error, CredentialPreflightError):
        payload["credential_setup"] = error.guidance()
    return payload


def write_markdown(path: str | None, title: str, lines: list[str]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = [f"# {title}", "", f"- Exported at: {utc_now()}", "", *lines]
    target.write_text("\n".join(content) + "\n", encoding="utf-8")


def sarif_from_issues(issues: list[dict[str, Any]], tool_name: str = "sonarqube-diagnostics") -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for issue in issues:
        normalized = normalized_issue(issue)
        component = str(normalized.get("component", ""))
        if ":" in component:
            component = component.split(":", 1)[1]
        severity = str(normalized.get("severity", "")).upper()
        level = "error" if severity in {"BLOCKER", "CRITICAL", "HIGH"} else "warning"
        line = normalized.get("line") or 1
        results.append(
            {
                "ruleId": str(issue.get("rule") or normalized.get("category") or "SONAR"),
                "level": level,
                "message": {"text": str(normalized.get("message", ""))},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": component},
                            "region": {"startLine": int(line)},
                        }
                    }
                ],
                "properties": {
                    "sonar_key": normalized.get("key", ""),
                    "severity": normalized.get("severity", ""),
                    "category": normalized.get("category", ""),
                    "status": normalized.get("status", ""),
                },
            }
        )
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{"tool": {"driver": {"name": tool_name}}, "results": results}],
    }
