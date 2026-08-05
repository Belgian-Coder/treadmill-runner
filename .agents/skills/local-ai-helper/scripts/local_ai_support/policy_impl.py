#!/usr/bin/env python3
"""Central local AI integration policy helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

POLICY_RELATIVE_PATH = ".agents/local-ai/policy.json"
SECRETS_RELATIVE_PATH = ".agents/local-ai/secrets.local.json"
SECRETS_EXAMPLE_RELATIVE_PATH = ".agents/local-ai/secrets.example.json"
APPROVED_COMMAND_PREFIX = "python -B .agents/manage.py local-ai"
APPROVED_OWNERS = {"local-ai-helper", "skill-manager", "workflow-manager"}
TEXT_USE_CASES = {
    "validation-triage",
    "code-review",
    "patch-draft",
    "implementation-planning",
    "inventory-summary",
    "changelog-draft",
    "changed-files-summary",
    "failure-cluster",
    "test-gap-summary",
    "handoff-draft",
    "duplicate-overlap-detection",
    "skill-routing",
    "workflow-routing",
    "simple-python-script",
    "dotnet10-di-console",
    "dotnet10-xunit-authoring",
    "dotnet10-xunit-repair",
}
VISION_USE_CASES = {"vision-describe", "vision-pdf"}
APPROVED_USE_CASES = TEXT_USE_CASES | VISION_USE_CASES
DEFAULT_ALLOWED_OWNERS = {
    "validation-triage": ["skill-manager", "workflow-manager", "local-ai-helper"],
    "code-review": ["skill-manager", "local-ai-helper"],
    "patch-draft": ["skill-manager", "local-ai-helper"],
    "implementation-planning": ["workflow-manager", "skill-manager", "local-ai-helper"],
    "inventory-summary": ["workflow-manager", "skill-manager", "local-ai-helper"],
    "changelog-draft": ["workflow-manager", "skill-manager", "local-ai-helper"],
    "changed-files-summary": ["workflow-manager", "skill-manager", "local-ai-helper"],
    "failure-cluster": ["workflow-manager", "skill-manager", "local-ai-helper"],
    "test-gap-summary": ["workflow-manager", "skill-manager", "local-ai-helper"],
    "handoff-draft": ["workflow-manager", "skill-manager", "local-ai-helper"],
    "duplicate-overlap-detection": ["workflow-manager", "skill-manager", "local-ai-helper"],
    "vision-describe": ["workflow-manager", "local-ai-helper"],
    "vision-pdf": ["workflow-manager", "local-ai-helper"],
    "skill-routing": ["skill-manager", "local-ai-helper"],
    "workflow-routing": ["workflow-manager", "local-ai-helper"],
    "simple-python-script": ["workflow-manager", "skill-manager", "local-ai-helper"],
    "dotnet10-di-console": ["workflow-manager", "skill-manager", "local-ai-helper"],
    "dotnet10-xunit-authoring": ["workflow-manager", "skill-manager", "local-ai-helper"],
    "dotnet10-xunit-repair": ["workflow-manager", "skill-manager", "local-ai-helper"],
}
DEFAULT_USE_CASE_FALLBACKS = {
    "simple-python-script": "orchestrator-until-fresh-benchmark",
    "dotnet10-di-console": "orchestrator-until-fresh-benchmark",
    "dotnet10-xunit-authoring": "orchestrator-until-fresh-benchmark",
    "dotnet10-xunit-repair": "orchestrator-until-fresh-benchmark",
}

DEFAULT_POLICY: dict[str, Any] = {
    "schema_version": 1,
    "enabled": True,
    "mode": "auto",
    "require_declared_metadata": True,
    "fallback": {
        "mode": "deterministic",
        "must_preserve_original_exit_code": True,
        "message": "Use deterministic validation, reports, and evidence directly when local AI is unavailable.",
    },
    "secrets_file": SECRETS_RELATIVE_PATH,
    "approved_command_prefix": APPROVED_COMMAND_PREFIX,
    "owners": {owner: {"enabled": True} for owner in sorted(APPROVED_OWNERS)},
    "use_cases": {
        use_case: {
            "enabled": True,
            "owners": DEFAULT_ALLOWED_OWNERS[use_case],
            "fallback": DEFAULT_USE_CASE_FALLBACKS.get(use_case, "deterministic"),
        }
        for use_case in sorted(APPROVED_USE_CASES)
    },
}

DEFAULT_SECRETS_EXAMPLE: dict[str, Any] = {
    "schema_version": 1,
    "description": "Copy to secrets.local.json if you need local-only tokens. Do not commit real secrets.",
    "azure_devops": [
        {
            "name": "example",
            "server_url": "https://dev.azure.com/example",
            "pat_env": "AZURE_DEVOPS_PAT",
            "pat": "",
            "notes": "Prefer environment variables. PAT scope should be limited to work items and attachments needed for intake.",
        }
    ],
    "tfs": [
        {
            "name": "on-prem-example",
            "server_url": "https://tfs.example.local/tfs/DefaultCollection",
            "pat_env": "TFS_PAT",
            "pat": "",
        }
    ],
}


def policy_path(root: Path) -> Path:
    return root / POLICY_RELATIVE_PATH


def secrets_path(root: Path) -> Path:
    return root / SECRETS_RELATIVE_PATH


def default_policy() -> dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_POLICY))


def write_default_policy(root: Path, *, force: bool = False) -> bool:
    path = policy_path(root)
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(default_policy(), indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return True


def write_secrets_example(root: Path, *, force: bool = False) -> bool:
    path = root / SECRETS_EXAMPLE_RELATIVE_PATH
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(DEFAULT_SECRETS_EXAMPLE, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return True


def load_raw_policy(root: Path) -> tuple[dict[str, Any], list[str]]:
    path = policy_path(root)
    if not path.exists():
        return default_policy(), [f"{POLICY_RELATIVE_PATH} is missing; deterministic fallback is required."]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return default_policy(), [f"{POLICY_RELATIVE_PATH} is not valid JSON: {exc}"]
    if not isinstance(payload, dict):
        return default_policy(), [f"{POLICY_RELATIVE_PATH} must contain a JSON object."]
    return payload, []


def normalize_policy(raw: dict[str, Any]) -> dict[str, Any]:
    policy = default_policy()
    policy.update({key: value for key, value in raw.items() if key not in {"owners", "use_cases", "fallback"}})
    if isinstance(raw.get("fallback"), dict):
        fallback = dict(policy["fallback"])
        fallback.update(raw["fallback"])
        policy["fallback"] = fallback
    if isinstance(raw.get("owners"), dict):
        owners = {owner: {"enabled": True} for owner in sorted(APPROVED_OWNERS)}
        for owner, value in raw["owners"].items():
            owner_name = str(owner).strip()
            if owner_name in APPROVED_OWNERS and isinstance(value, dict):
                owners[owner_name] = {"enabled": bool(value.get("enabled", True))}
        policy["owners"] = owners
    if isinstance(raw.get("use_cases"), dict):
        use_cases = dict(policy["use_cases"])
        for use_case, value in raw["use_cases"].items():
            use_case_id = str(use_case).strip()
            if use_case_id not in APPROVED_USE_CASES or not isinstance(value, dict):
                continue
            owners = [
                str(owner).strip()
                for owner in value.get("owners", DEFAULT_ALLOWED_OWNERS[use_case_id])
                if str(owner).strip() in APPROVED_OWNERS
            ]
            use_cases[use_case_id] = {
                "enabled": bool(value.get("enabled", True)),
                "owners": owners or list(DEFAULT_ALLOWED_OWNERS[use_case_id]),
                "fallback": str(
                    value.get("fallback", DEFAULT_USE_CASE_FALLBACKS.get(use_case_id, "deterministic"))
                    or DEFAULT_USE_CASE_FALLBACKS.get(use_case_id, "deterministic")
                ),
            }
        policy["use_cases"] = use_cases
    policy["enabled"] = bool(policy.get("enabled", True))
    policy["mode"] = str(policy.get("mode", "auto")).strip().lower() or "auto"
    policy["secrets_file"] = str(policy.get("secrets_file", SECRETS_RELATIVE_PATH))
    policy["approved_command_prefix"] = str(policy.get("approved_command_prefix", APPROVED_COMMAND_PREFIX))
    return policy


def load_policy(root: Path) -> tuple[dict[str, Any], list[str]]:
    raw, issues = load_raw_policy(root)
    return normalize_policy(raw), issues


def evaluate_use_case(root: Path, use_case: str, owner: str | None = None) -> dict[str, Any]:
    policy, issues = load_policy(root)
    use_case_id = str(use_case).strip()
    owner_name = str(owner or "").strip()
    allowed = True
    reason = "allowed"
    use_case_policy: dict[str, Any] = {}
    if issues:
        allowed = False
        reason = issues[0]
    elif not policy.get("enabled", True):
        allowed = False
        reason = "Local AI policy disables all integrations."
    elif str(policy.get("mode", "auto")).lower() in {"0", "false", "off", "no", "disabled"}:
        allowed = False
        reason = "Local AI policy mode disables integrations."
    elif use_case_id not in APPROVED_USE_CASES:
        allowed = False
        reason = f"Unknown local AI use case: {use_case_id}"
    else:
        owners = policy.get("owners", {})
        use_cases = policy.get("use_cases", {})
        owner_policy = owners.get(owner_name) if owner_name else None
        use_case_policy = use_cases.get(use_case_id, {})
        if owner_name and owner_name not in APPROVED_OWNERS:
            allowed = False
            reason = f"Unknown local AI owner: {owner_name}"
        elif isinstance(owner_policy, dict) and not owner_policy.get("enabled", True):
            allowed = False
            reason = f"Local AI owner {owner_name!r} is disabled by policy."
        elif not bool(use_case_policy.get("enabled", True)):
            allowed = False
            reason = f"Local AI use case {use_case_id!r} is disabled by policy."
        elif owner_name and owner_name not in set(use_case_policy.get("owners", [])):
            allowed = False
            reason = f"Local AI use case {use_case_id!r} is not enabled for owner {owner_name!r}."
    return {
        "schema_version": 1,
        "ok": allowed,
        "allowed": allowed,
        "use_case": use_case_id,
        "use_case_policy": use_case_policy,
        "owner": owner_name or None,
        "reason": reason,
        "policy_path": POLICY_RELATIVE_PATH,
        "secrets_path": policy.get("secrets_file", SECRETS_RELATIVE_PATH),
        "fallback": policy.get("fallback", {}).get("mode", "deterministic"),
        "issues": issues,
    }


def policy_report(root: Path, *, use_case: str | None = None, owner: str | None = None) -> dict[str, Any]:
    policy, issues = load_policy(root)
    report = {
        "schema_version": 1,
        "tool": "local-ai-helper.policy",
        "ok": not issues,
        "policy_path": POLICY_RELATIVE_PATH,
        "secrets_path": policy.get("secrets_file", SECRETS_RELATIVE_PATH),
        "secrets_file_present": secrets_path(root).exists(),
        "enabled": bool(policy.get("enabled", True)),
        "mode": policy.get("mode", "auto"),
        "require_declared_metadata": bool(policy.get("require_declared_metadata", True)),
        "approved_command_prefix": policy.get("approved_command_prefix", APPROVED_COMMAND_PREFIX),
        "fallback": policy.get("fallback", {}),
        "owners": policy.get("owners", {}),
        "use_cases": policy.get("use_cases", {}),
        "issues": issues,
    }
    if use_case:
        report["decision"] = evaluate_use_case(root, use_case, owner)
        report["ok"] = bool(report["decision"]["ok"])
    return report
