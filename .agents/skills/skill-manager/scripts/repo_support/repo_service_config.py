"""Local external-service profile configuration helpers."""

from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any


SECRET_STORE_REL = ".agents/local-ai/secrets.local.json"
GITIGNORE_PATTERNS = (
    ".agents/local-ai/secrets.local.json",
    ".agents/local-ai/local.settings.json",
)

SERVICE_SPECS: dict[str, dict[str, Any]] = {
    "azure-devops": {
        "section": "azure_devops",
        "aliases": ("azure_devops", "ado"),
        "url_field": "organization_url",
        "url_aliases": ("server_url",),
        "url_label": "Azure DevOps organization URL",
        "project_field": "project",
        "project_label": "Azure DevOps project",
        "token_field": "pat",
        "token_env_field": "pat_env",
        "token_label": "Azure DevOps PAT",
        "default_token_env": "AZURE_DEVOPS_PAT",
        "example_command": (
            "python -B .agents/skills/azure-devops-ticket-intake/scripts/"
            "import_azure_devops_work_item.py --server-name <name> --work-item-id <id> "
            "--output-root automations/user-story-workflow/runs --dry-run"
        ),
    },
    "tfs": {
        "section": "tfs",
        "aliases": ("team-foundation-server",),
        "url_field": "server_url",
        "url_aliases": ("organization_url",),
        "url_label": "TFS collection or server URL",
        "project_field": "project",
        "project_label": "TFS project",
        "token_field": "pat",
        "token_env_field": "pat_env",
        "token_label": "TFS PAT",
        "default_token_env": "AZURE_DEVOPS_PAT",
        "example_command": (
            "python -B .agents/skills/azure-devops-ticket-intake/scripts/"
            "import_azure_devops_work_item.py --server-name <name> --work-item-id <id> "
            "--output-root automations/user-story-workflow/runs --dry-run"
        ),
    },
    "sonarqube": {
        "section": "sonarqube",
        "aliases": ("sonar",),
        "url_field": "base_url",
        "url_aliases": (),
        "url_label": "SonarQube base URL",
        "project_field": "project_key",
        "project_label": "SonarQube project key",
        "token_field": "token",
        "token_env_field": "token_env",
        "token_label": "SonarQube token",
        "default_token_env": "SONAR_TOKEN",
        "example_command": (
            "python -B .agents/skills/sonarqube-diagnostics/scripts/export_issues.py "
            "--server-name <name> --output-json validation/sonarqube/issues.json"
        ),
    },
}


def normalize_service(value: str | None) -> str:
    text = (value or "").strip().lower().replace("_", "-")
    for service, spec in SERVICE_SPECS.items():
        aliases = {service, *(str(item).lower().replace("_", "-") for item in spec.get("aliases", ()))}
        if text in aliases:
            return service
    raise ValueError(f"unsupported service: {value or '<missing>'}")


def service_choices() -> list[str]:
    return sorted(SERVICE_SPECS)


def secret_store_path(root: Path) -> Path:
    return root / SECRET_STORE_REL


def read_secret_store(root: Path) -> tuple[dict[str, Any], list[str]]:
    path = secret_store_path(root)
    if not path.exists():
        return {}, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"{SECRET_STORE_REL} is not readable JSON: {exc}"]
    if not isinstance(data, dict):
        return {}, [f"{SECRET_STORE_REL} must contain a JSON object"]
    return data, []


def write_secret_store(root: Path, data: dict[str, Any]) -> None:
    path = secret_store_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def ensure_gitignore(root: Path) -> dict[str, Any]:
    path = root / ".gitignore"
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    lines = existing.splitlines()
    missing = [pattern for pattern in GITIGNORE_PATTERNS if pattern not in lines]
    if not missing:
        return {"path": ".gitignore", "updated": False, "added": []}
    new_lines = list(lines)
    if new_lines and new_lines[-1].strip():
        new_lines.append("")
    new_lines.append("# Local external service and AI settings")
    new_lines.extend(missing)
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8", newline="\n")
    return {"path": ".gitignore", "updated": True, "added": missing}


def profile_lists(data: dict[str, Any], service: str) -> list[dict[str, Any]]:
    spec = SERVICE_SPECS[service]
    sections = [str(spec["section"])]
    if service in {"azure-devops", "tfs"}:
        sections.append("servers")
    profiles: list[dict[str, Any]] = []
    for section in sections:
        raw = data.get(section, [])
        if isinstance(raw, list):
            profiles.extend(item for item in raw if isinstance(item, dict))
    return profiles


def profile_name(profile: dict[str, Any]) -> str:
    return str(profile.get("name", "")).strip()


def profile_value(profile: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = profile.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def profile_has_required_fields(profile: dict[str, Any], service: str) -> bool:
    spec = SERVICE_SPECS[service]
    url = profile_value(profile, str(spec["url_field"]), *[str(item) for item in spec.get("url_aliases", ())])
    project = profile_value(profile, str(spec["project_field"]))
    token = profile_value(profile, str(spec["token_field"]))
    token_env = profile_value(profile, str(spec["token_env_field"]))
    return bool(profile_name(profile) and url and project and (token or token_env))


def profile_credential_ready(profile: dict[str, Any], service: str) -> bool:
    spec = SERVICE_SPECS[service]
    token = profile_value(profile, str(spec["token_field"]))
    token_env = profile_value(profile, str(spec["token_env_field"]))
    return bool(token or (token_env and os.environ.get(token_env)))


def service_status(root: Path, service: str) -> dict[str, Any]:
    data, issues = read_secret_store(root)
    profiles = profile_lists(data, service)
    complete = [item for item in profiles if profile_has_required_fields(item, service)]
    credential_ready = [item for item in complete if profile_credential_ready(item, service)]
    names = [profile_name(item) for item in profiles if profile_name(item)]
    return {
        "service": service,
        "profile_count": len(profiles),
        "complete_profile_count": len(complete),
        "credential_ready_profile_count": len(credential_ready),
        "profile_names": sorted(names),
        "configured": bool(complete),
        "credential_ready": bool(credential_ready),
        "issues": issues,
    }


def secret_store_keys(root: Path) -> list[str]:
    data, issues = read_secret_store(root)
    if issues:
        return ["<unreadable>"]
    return sorted(str(key) for key in data.keys())


def arg_text(args: Any, *names: str) -> str:
    for name in names:
        value = getattr(args, name, None)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def prompt_value(label: str, *, default: str = "", secret: bool = False, no_input: bool = False) -> str:
    if no_input or not sys.stdin.isatty():
        return ""
    prompt = f"{label}"
    if default:
        prompt += f" [{default}]"
    prompt += ": "
    value = getpass.getpass(prompt) if secret else input(prompt)
    text = value.strip()
    return text or default


def build_profile_from_args(args: Any, service: str) -> tuple[dict[str, Any], list[str]]:
    spec = SERVICE_SPECS[service]
    no_input = bool(getattr(args, "no_input", False))
    name = arg_text(args, "name") or prompt_value("Profile name", default="default", no_input=no_input) or "default"
    url_field = str(spec["url_field"])
    project_field = str(spec["project_field"])
    token_field = str(spec["token_field"])
    token_env_field = str(spec["token_env_field"])
    url = arg_text(args, url_field, "organization_url", "server_url", "base_url") or prompt_value(
        str(spec["url_label"]),
        no_input=no_input,
    )
    project = arg_text(args, project_field, "project", "project_key") or prompt_value(
        str(spec["project_label"]),
        no_input=no_input,
    )
    token_env = arg_text(args, token_env_field, "pat_env", "token_env") or prompt_value(
        f"{spec['token_label']} environment variable",
        default=str(spec["default_token_env"]),
        no_input=no_input,
    )
    token = arg_text(args, token_field, "pat", "token")
    if not token_env and not token:
        token = prompt_value(str(spec["token_label"]), secret=True, no_input=no_input)
    profile = {
        "name": name,
        "kind": service,
        url_field: url.rstrip("/") if url else "",
        project_field: project,
    }
    if token_env:
        profile[token_env_field] = token_env
    if token:
        profile[token_field] = token
    missing: list[str] = []
    if not url:
        missing.append(url_field)
    if not project:
        missing.append(project_field)
    if not token_env and not token:
        missing.append(f"{token_env_field} or {token_field}")
    return profile, missing


def redacted_profile(profile: dict[str, Any], service: str) -> dict[str, Any]:
    spec = SERVICE_SPECS[service]
    token_field = str(spec["token_field"])
    return {
        key: ("<redacted>" if key == token_field else value)
        for key, value in profile.items()
        if value not in (None, "")
    }


def configure_service_profile(root: Path, args: Any) -> dict[str, Any]:
    try:
        service = normalize_service(getattr(args, "service", None))
    except ValueError as exc:
        return {
            "schema_version": 1,
            "tool": "repo-credential-configure",
            "ok": False,
            "status": "needs-input",
            "service": "",
            "missing": ["service"],
            "issues": [str(exc)],
            "secret_store": SECRET_STORE_REL,
            "next_command": "python -B .agents/manage.py credential-doctor --configure --service <service>",
        }
    store, read_issues = read_secret_store(root)
    if read_issues:
        return {
            "schema_version": 1,
            "tool": "repo-credential-configure",
            "ok": False,
            "status": "failed",
            "service": service,
            "issues": read_issues,
            "secret_store": SECRET_STORE_REL,
        }
    profile, missing = build_profile_from_args(args, service)
    if missing:
        return {
            "schema_version": 1,
            "tool": "repo-credential-configure",
            "ok": False,
            "status": "needs-input",
            "service": service,
            "missing": missing,
            "secret_store": SECRET_STORE_REL,
            "next_command": (
                "ask the user for the missing values, then rerun "
                "python -B .agents/manage.py credential-doctor --configure"
            ),
        }
    spec = SERVICE_SPECS[service]
    section = str(spec["section"])
    raw_profiles = store.get(section, [])
    profiles = raw_profiles if isinstance(raw_profiles, list) else []
    name = profile_name(profile)
    existing_index = next((index for index, item in enumerate(profiles) if isinstance(item, dict) and profile_name(item) == name), None)
    overwrite = bool(getattr(args, "overwrite", False))
    action = "created"
    if existing_index is not None:
        if not overwrite:
            return {
                "schema_version": 1,
                "tool": "repo-credential-configure",
                "ok": False,
                "status": "exists",
                "service": service,
                "profile_name": name,
                "secret_store": SECRET_STORE_REL,
                "issues": [f"profile {name!r} already exists for {service}; pass --overwrite to replace it"],
            }
        profiles[existing_index] = profile
        action = "updated"
    else:
        profiles.append(profile)
    store.setdefault("schema_version", 1)
    store[section] = profiles
    gitignore = ensure_gitignore(root)
    write_secret_store(root, store)
    status = service_status(root, service)
    return {
        "schema_version": 1,
        "tool": "repo-credential-configure",
        "ok": True,
        "status": "configured",
        "service": service,
        "action": action,
        "profile_name": name,
        "secret_store": SECRET_STORE_REL,
        "gitignore": gitignore,
        "profile": redacted_profile(profile, service),
        "profile_status": status,
        "next_command": str(spec["example_command"]).replace("<name>", name),
    }
