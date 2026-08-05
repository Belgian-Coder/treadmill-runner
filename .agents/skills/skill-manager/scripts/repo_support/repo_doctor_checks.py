#!/usr/bin/env python3
"""Environment, Git, and GitHub checks for repository doctor commands."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from repo_support import repo_common as repo
from repo_support import repo_health

def setup_local_ai_readiness(root: Path) -> dict[str, object]:
    script = repo.skill_script(root, "local-ai-helper", "setup_local_ai.py")
    checks: list[dict[str, object]] = []
    for label, command in (
        ("readiness", [sys.executable, "-B", str(script), "--root", str(root), "readiness", "--json"]),
        ("policy", [sys.executable, "-B", str(script), "--root", str(root), "policy", "--json"]),
    ):
        completed = subprocess.run(
            command,
            cwd=root,
            check=False,
            env=repo.child_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            payload: object = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload = {"output": completed.stdout.strip()}
        checks.append({"name": label, "ok": completed.returncode == 0, "result": payload})
    ok = all(bool(item["ok"]) for item in checks)
    policy_result = next((item.get("result") for item in checks if item.get("name") == "policy"), {})
    readiness_result = next((item.get("result") for item in checks if item.get("name") == "readiness"), {})
    selected_models: dict[str, object] = {}
    enabled_use_cases: list[object] = []
    if isinstance(policy_result, dict):
        selected_models = policy_result.get("selected_models", {}) if isinstance(policy_result.get("selected_models"), dict) else {}
        if not selected_models and isinstance(policy_result.get("selected_profiles"), dict):
            selected_models = policy_result.get("selected_profiles", {})
        enabled_use_cases = policy_result.get("enabled_use_cases", []) if isinstance(policy_result.get("enabled_use_cases"), list) else []
        integration_policy = policy_result.get("integration_policy")
        if not enabled_use_cases and isinstance(integration_policy, dict) and isinstance(integration_policy.get("use_cases"), dict):
            enabled_use_cases = [
                name for name, details in sorted(integration_policy["use_cases"].items())
                if isinstance(details, dict) and details.get("enabled") is True
            ]
    if not selected_models and isinstance(readiness_result, dict):
        selected_models = readiness_result.get("selected_models", {}) if isinstance(readiness_result.get("selected_models"), dict) else {}
    return {
        "ok": ok,
        "status": "ready" if ok else "issues-found",
        "checks": checks,
        "selected_models": selected_models,
        "enabled_use_cases": enabled_use_cases,
        "fallback": "Use deterministic JSON/Markdown evidence directly when local AI is disabled or unavailable.",
        "next_command": "python -B .agents/manage.py local-ai doctor --quick",
    }


def find_gh() -> str | None:
    gh = shutil.which("gh")
    if not gh:
        for candidate in (
            Path("C:/Program Files/GitHub CLI/gh.exe"),
            Path.home() / "AppData" / "Local" / "Programs" / "GitHub CLI" / "gh.exe",
        ):
            if candidate.exists():
                return str(candidate)
    return gh


def github_repo_name(root: Path, gh: str) -> str:
    repo_view = subprocess.run(
        [gh, "repo", "view", "--json", "nameWithOwner"],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    repo_name = "Belgian-Coder/skills"
    if repo_view.returncode == 0:
        try:
            repo_name = str(json.loads(repo_view.stdout).get("nameWithOwner") or repo_name)
        except json.JSONDecodeError:
            pass
    return repo_name


def gh_auth_status(root: Path, gh: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [gh, "auth", "status"],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def git_current_branch(root: Path) -> str:
    completed = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 and completed.stdout.strip() else "main"


def git_head_sha(root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def github_hygiene(root: Path) -> dict[str, object]:
    gh = find_gh()
    if not gh:
        return {"ok": True, "status": "skipped", "skipped": ["gh not found on PATH"]}
    auth = gh_auth_status(root, gh)
    if auth.returncode != 0:
        return {
            "ok": True,
            "status": "skipped",
            "skipped": ["gh is not authenticated"],
            "output": auth.stdout.strip(),
        }
    repo_name = github_repo_name(root, gh)
    checks: list[dict[str, object]] = []
    skipped: list[str] = []
    for name, command in (
        ("open_prs", [gh, "pr", "list", "--repo", repo_name, "--state", "open", "--limit", "50", "--json", "number,title,author"]),
        (
            "dependabot_alerts",
            [gh, "api", f"repos/{repo_name}/dependabot/alerts?state=open&per_page=100"],
        ),
    ):
        completed = subprocess.run(
            command,
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            payload: object = json.loads(completed.stdout or "[]")
            count = len(payload) if isinstance(payload, list) else 0
            stale_candidate_count = 0
            if name == "dependabot_alerts" and isinstance(payload, list):
                actionable: list[object] = []
                for alert in payload:
                    manifest_path = ""
                    if isinstance(alert, dict):
                        dependency = alert.get("dependency")
                        if isinstance(dependency, dict):
                            manifest_path = str(dependency.get("manifest_path") or "").replace("\\", "/")
                    candidate_stale = (
                        manifest_path.startswith("_candidate-imports/")
                        or manifest_path.startswith("candidate-imports/")
                        or manifest_path.startswith("temp/")
                    ) and manifest_path not in tracked_files(root, manifest_path)
                    if candidate_stale:
                        stale_candidate_count += 1
                    else:
                        actionable.append(alert)
                payload = actionable
                count = len(actionable)
        except json.JSONDecodeError:
            payload = completed.stdout.strip()
            count = -1
            stale_candidate_count = 0
        dependency_alerts_disabled = (
            name == "dependabot_alerts"
            and completed.returncode != 0
            and (
                "dependabot alerts are disabled" in str(payload).lower()
                or "admin:repo_hook" in str(payload).lower()
            )
        )
        if dependency_alerts_disabled:
            count = 0
            skipped.append("Dependabot alerts are disabled or unavailable for this repository")
        checks.append(
            {
                "name": name,
                "ok": (completed.returncode == 0 and count == 0) or dependency_alerts_disabled,
                "count": count,
                "stale_candidate_count": stale_candidate_count,
                "result": payload,
            }
        )
    ok = all(bool(item["ok"]) for item in checks)
    stale_alerts = sum(int(item.get("stale_candidate_count", 0)) for item in checks)
    warnings = [
        f"{stale_alerts} Dependabot alert(s) refer only to deleted candidate import paths"
    ] if stale_alerts else []
    return {
        "ok": ok,
        "status": "clean" if ok else "issues-found",
        "repo": repo_name,
        "checks": checks,
        "warnings": warnings,
        "skipped": skipped,
    }


def github_actions_permissions(root: Path, gh: str, repo_name: str, runner=subprocess.run) -> dict[str, object]:
    completed = runner(
        [gh, "api", f"repos/{repo_name}/actions/permissions"],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if completed.returncode != 0:
        return {}
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def github_actions_status(root: Path, runner=subprocess.run) -> dict[str, object]:
    gh = find_gh()
    if not gh:
        return {"ok": True, "status": "skipped", "skipped": ["gh not found on PATH"]}
    auth = runner(
        [gh, "auth", "status"],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if auth.returncode != 0:
        return {
            "ok": True,
            "status": "skipped",
            "skipped": ["gh is not authenticated"],
            "output": auth.stdout.strip(),
        }
    repo_name = github_repo_name(root, gh)
    branch = git_current_branch(root)
    head_sha = git_head_sha(root)
    permissions = github_actions_permissions(root, gh, repo_name, runner=runner)
    actions_enabled = permissions.get("enabled") if isinstance(permissions.get("enabled"), bool) else None
    run_list = runner(
        [
            gh,
            "run",
            "list",
            "--repo",
            repo_name,
            "--branch",
            branch,
            "--limit",
            "5",
            "--json",
            "databaseId,status,conclusion,workflowName,headSha,createdAt,url",
        ],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if run_list.returncode != 0:
        return {"ok": True, "status": "skipped", "skipped": ["GitHub Actions runs could not be queried"], "output": run_list.stdout.strip()}
    try:
        runs = json.loads(run_list.stdout or "[]")
    except json.JSONDecodeError:
        return {"ok": True, "status": "skipped", "skipped": ["GitHub Actions run output was not JSON"], "output": run_list.stdout.strip()}
    if not isinstance(runs, list) or not runs:
        if actions_enabled is False:
            return {
                "ok": True,
                "status": "disabled",
                "skipped": ["GitHub Actions are disabled and no run was found for the current branch"],
                "repo": repo_name,
                "branch": branch,
                "head_sha": head_sha,
                "permissions": permissions,
            }
        return {"ok": True, "status": "skipped", "skipped": ["no GitHub Actions runs found"], "repo": repo_name, "branch": branch}
    matching = [item for item in runs if isinstance(item, dict) and (not head_sha or item.get("headSha") == head_sha)]
    if not matching:
        latest_run = runs[0]
        if actions_enabled is False:
            return {
                "ok": True,
                "status": "disabled",
                "skipped": ["GitHub Actions are disabled and no run was found for the current head SHA"],
                "repo": repo_name,
                "branch": branch,
                "head_sha": head_sha,
                "latest_run": latest_run,
                "permissions": permissions,
            }
        return {
            "ok": True,
            "status": "skipped",
            "skipped": ["no GitHub Actions run found for the current head SHA"],
            "repo": repo_name,
            "branch": branch,
            "head_sha": head_sha,
            "latest_run": latest_run,
        }
    run = matching[0]
    run_status = str(run.get("status") or "")
    conclusion = str(run.get("conclusion") or "")
    if run_status != "completed":
        return {"ok": True, "status": "pending", "repo": repo_name, "branch": branch, "head_sha": head_sha, "run": run}
    if conclusion == "success":
        return {"ok": True, "status": "passed", "repo": repo_name, "branch": branch, "head_sha": head_sha, "run": run}
    annotations: list[str] = []
    if head_sha:
        check_runs = runner(
            [gh, "api", f"repos/{repo_name}/commits/{head_sha}/check-runs"],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if check_runs.returncode == 0:
            try:
                check_payload = json.loads(check_runs.stdout or "{}")
            except json.JSONDecodeError:
                check_payload = {}
            for check_run in check_payload.get("check_runs", []) if isinstance(check_payload, dict) else []:
                if not isinstance(check_run, dict):
                    continue
                url = check_run.get("output", {}).get("annotations_url") if isinstance(check_run.get("output"), dict) else None
                if not url:
                    continue
                annotation_run = runner(
                    [gh, "api", str(url)],
                    cwd=root,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                if annotation_run.returncode != 0:
                    continue
                try:
                    payload = json.loads(annotation_run.stdout or "[]")
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, list):
                    for annotation in payload:
                        if isinstance(annotation, dict):
                            message = str(annotation.get("message") or "")
                            if message:
                                annotations.append(message)
    billing_terms = ("payment", "payments", "spending limit", "billing", "billing & plans")
    external_blocked = any(any(term in message.lower() for term in billing_terms) for message in annotations)
    return {
        "ok": bool(external_blocked),
        "status": "external-blocked" if external_blocked else "failed",
        "external_blocker": external_blocked,
        "repo": repo_name,
        "branch": branch,
        "head_sha": head_sha,
        "run": run,
        "annotations": annotations[:10],
        "issues": [] if external_blocked else [f"latest GitHub Actions run concluded {conclusion or 'unknown'}"],
        "warnings": ["GitHub Actions is blocked by account billing or spending-limit state"] if external_blocked else [],
    }


def tracked_payload_hygiene(root: Path) -> dict[str, object]:
    health = repo_health.build_repo_health_report(root)
    surface = health.get("repository_surface", {}) if isinstance(health, dict) else {}
    candidate_imports = surface.get("candidate_imports", []) if isinstance(surface, dict) else []
    layout = surface.get("layout", []) if isinstance(surface, dict) else []
    local_settings = surface.get("local_settings", []) if isinstance(surface, dict) else []
    issues = list(candidate_imports or []) + [
        item for item in list(layout or []) + list(local_settings or [])
        if any(token in str(item).lower() for token in ("secret", "model", "runtime", "cache", "candidate", "temp"))
    ]
    return {
        "ok": not issues,
        "status": "clean" if not issues else "issues-found",
        "issues": issues,
    }


def tracked_files(root: Path, prefix: str) -> set[str]:
    completed = subprocess.run(
        ["git", "ls-files", prefix],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        return set()
    return {line.strip().replace("\\", "/") for line in completed.stdout.splitlines() if line.strip()}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tracked_bundle_integrity(root: Path) -> dict[str, object]:
    manifest_path = root / ".agents" / "local-ai" / "bundle" / "manifest.json"
    tracked = tracked_files(root, ".agents/local-ai/bundle")
    if not tracked:
        return {
            "ok": True,
            "status": "skipped",
            "skipped": ["no tracked local AI bundle files were reported by git"],
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "status": "issues-found",
            "issues": [f"bundle manifest could not be read: {exc}"],
        }
    entries = manifest.get("files", []) if isinstance(manifest, dict) else []
    expected = {
        ".agents/local-ai/bundle/" + str(entry.get("path", "")).replace("\\", "/"): str(entry.get("sha256", "")).lower()
        for entry in entries
        if isinstance(entry, dict) and str(entry.get("path", "")).strip()
    }
    issues: list[str] = []
    checked: list[str] = []
    for rel_path in sorted(path for path in tracked if path != ".agents/local-ai/bundle/manifest.json"):
        expected_hash = expected.get(rel_path)
        if not expected_hash:
            issues.append(f"tracked bundle file is missing from manifest: {rel_path}")
            continue
        path = root / rel_path
        if not path.exists():
            issues.append(f"tracked bundle file is missing on disk: {rel_path}")
            continue
        actual_hash = sha256_file(path)
        checked.append(rel_path)
        if actual_hash.lower() != expected_hash:
            issues.append(
                f"tracked bundle hash mismatch: {rel_path} expected {expected_hash} actual {actual_hash}"
            )
    return {
        "ok": not issues,
        "status": "clean" if not issues else "issues-found",
        "checked": checked,
        "issues": issues,
        "allowed_licenses": ["Apache-2.0", "MIT", "NVIDIA Open Model License"],
    }


def git_dirty_state(root: Path) -> dict[str, object]:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        return {
            "ok": True,
            "status": "skipped",
            "skipped": ["git status could not be read"],
            "output": (completed.stdout + completed.stderr).strip(),
        }
    entries = [line for line in completed.stdout.splitlines() if line.strip()]
    tracked_dirty = [line for line in entries if not line.startswith("??")]
    untracked = [line for line in entries if line.startswith("??")]
    return {
        "ok": True,
        "status": "dirty" if entries else "clean",
        "dirty": bool(entries),
        "tracked_dirty": tracked_dirty,
        "untracked": untracked,
    }
