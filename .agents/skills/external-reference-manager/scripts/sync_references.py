#!/usr/bin/env python3
"""Report, dry-run, or refresh declared Git references and pinned cards."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import urllib.parse
import subprocess
import sys
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def run_git(args: list[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def run_git_optional(args: list[str], cwd: Path | None = None) -> tuple[bool, str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    output = completed.stdout.strip() or completed.stderr.strip()
    return completed.returncode == 0, output


def ensure_inside(root: Path, candidate: Path) -> Path:
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    if candidate_resolved != root_resolved and root_resolved not in candidate_resolved.parents:
        raise ValueError(f"path escapes workspace: {candidate}")
    return candidate_resolved


def load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("references"), list):
        raise ValueError("manifest must contain a references array")
    return data


def missing_manifest_report(
    workspace: Path,
    manifest_path: Path,
    output_root: Path,
    mode: str,
) -> dict[str, Any]:
    example = manifest_path.with_name(f"{manifest_path.stem}.example{manifest_path.suffix}")
    example_manifest = str(example) if example.exists() else ""
    relative_manifest = manifest_path
    relative_example = example
    try:
        relative_manifest = manifest_path.relative_to(workspace)
    except ValueError:
        pass
    try:
        relative_example = example.relative_to(workspace)
    except ValueError:
        pass
    next_command = (
        f"When workspace writes are approved, copy {relative_example} to {relative_manifest}, "
        "fill in reference metadata, then rerun report mode. "
        "In strict read-only/no-write dogfood, stop here and report the missing manifest as a skipped result."
    )
    return {
        "schema_version": 2,
        "tool": "external-reference-manager.sync_references",
        "ok": True,
        "status": "skipped",
        "reason": "missing-active-manifest",
        "mode": mode,
        "generated_at": utc_now(),
        "manifest": str(manifest_path),
        "example_manifest": example_manifest,
        "output_root": str(output_root),
        "summary": {
            "reference_count": 0,
            "available_count": 0,
            "changed_count": 0,
            "stale_count": 0,
            "conflict_count": 0,
        },
        "references": [],
        "warnings": [next_command],
        "skipped": [{"name": "reference-manifest", "reason": "active manifest missing"}],
        "next_command": next_command,
    }


def redacted_url(value: str | None) -> str:
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(str(value))
    if parsed.username or parsed.password:
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc += f":{parsed.port}"
        return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    return str(value)


def credential_boundary_warnings(value: str | None) -> list[str]:
    warnings: list[str] = []
    text = str(value or "")
    parsed = urllib.parse.urlsplit(text)
    if parsed.username or parsed.password:
        warnings.append("repository_url contains inline credentials; use Git credential manager or environment auth.")
    lowered = text.lower()
    if "@" in text and parsed.scheme in {"http", "https"} and (":" in (parsed.netloc.split("@", 1)[0])):
        warnings.append("repository_url appears to include username/password material.")
    if "_git/" in lowered and "dev.azure.com" in lowered:
        warnings.append("Azure DevOps remote detected; keep PATs out of manifests and rely on configured credentials.")
    return warnings


def is_dirty(repo: Path) -> bool:
    return bool(run_git(["status", "--porcelain"], cwd=repo))


def resolve_checkout_path(workspace: Path, output_root: Path, entry: dict[str, Any]) -> Path:
    raw = entry.get("path") or f"repositories/{entry.get('name', 'reference')}"
    path = Path(str(raw))
    if not path.is_absolute():
        path = output_root / path
    return ensure_inside(workspace, path)


def checkout_ref(repo: Path, entry: dict[str, Any], fetch: bool) -> None:
    if fetch:
        run_git(["fetch", "--tags", "--prune"], cwd=repo)
    if entry.get("commit"):
        run_git(["checkout", "--detach", str(entry["commit"])], cwd=repo)
    elif entry.get("tag"):
        run_git(["checkout", "--detach", f"tags/{entry['tag']}"], cwd=repo)
    elif entry.get("branch"):
        branch = str(entry["branch"])
        if fetch:
            run_git(["checkout", "-B", branch, f"origin/{branch}"], cwd=repo)
        else:
            run_git(["checkout", branch], cwd=repo)
    else:
        raise ValueError(f"reference {entry.get('name')} must declare branch, tag, or commit")


def clone_or_update(entry: dict[str, Any], repo_path: Path, fetch: bool, allow_reset: bool) -> None:
    repo_url = entry.get("repository_url")
    if not repo_url:
        raise ValueError(f"reference {entry.get('name')} is missing repository_url")
    if repo_path.exists():
        if not (repo_path / ".git").exists():
            raise ValueError(f"reference path is not a Git repository: {repo_path}")
        if is_dirty(repo_path):
            if not allow_reset:
                raise ValueError(f"reference repository has local changes: {repo_path}")
            run_git(["reset", "--hard"], cwd=repo_path)
            run_git(["clean", "-fd"], cwd=repo_path)
        checkout_ref(repo_path, entry, fetch)
        return
    if not fetch:
        raise ValueError(f"reference path does not exist and --no-fetch was used: {repo_path}")
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    run_git(["clone", str(repo_url), str(repo_path)])
    checkout_ref(repo_path, entry, fetch)


def repo_summary(repo_path: Path) -> dict[str, str]:
    commit = run_git(["rev-parse", "HEAD"], cwd=repo_path)
    subject = run_git(["log", "-1", "--format=%s"], cwd=repo_path)
    committed_at = run_git(["log", "-1", "--format=%cI"], cwd=repo_path)
    branch = run_git(["branch", "--show-current"], cwd=repo_path)
    return {
        "commit": commit,
        "short_commit": commit[:12],
        "subject": subject,
        "committed_at": committed_at,
        "branch": branch or "detached",
    }


def parse_git_timestamp(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.UTC)
    except ValueError:
        return None


def commit_summary(repo_path: Path, commit: str) -> dict[str, str] | None:
    ok, output = run_git_optional(["show", "-s", "--format=%H%x00%cI%x00%s", commit], cwd=repo_path)
    if not ok or not output:
        return None
    parts = output.split("\x00", 2)
    if len(parts) != 3:
        return None
    return {"commit": parts[0], "committed_at": parts[1], "subject": parts[2]}


def age_summary(committed_at: str | None, max_age_days: int) -> dict[str, Any]:
    stale = False
    age_days: int | None = None
    committed = parse_git_timestamp(committed_at)
    if committed:
        age_days = max(0, (dt.datetime.now(dt.UTC) - committed).days)
        stale = age_days > max_age_days
    return {
        "committed_at": committed_at,
        "age_days": age_days,
        "stale_by_age": stale,
        "max_age_days": max_age_days,
        "available": committed is not None,
    }


def stale_reference_summary(repo_path: Path, committed_at: str, max_age_days: int) -> dict[str, Any]:
    stale = age_summary(committed_at, max_age_days)
    ok, divergence = run_git_optional(["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], cwd=repo_path)
    ahead = behind = 0
    if ok and divergence:
        parts = divergence.split()
        if len(parts) >= 2:
            ahead, behind = int(parts[0]), int(parts[1])
    stale.update(
        {
            "upstream_ahead": ahead,
            "upstream_behind": behind,
            "divergence_available": ok,
        }
    )
    return stale


def load_previous_pins(manifest: dict[str, Any], output_root: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    pins: dict[str, dict[str, Any]] = {
        str(item.get("name")): item
        for item in manifest.get("pinned_references", [])
        if isinstance(item, dict) and item.get("name")
    }
    sources = ["manifest.pinned_references"] if pins else []
    pinned_path = output_root / "pinned-references.json"
    if pinned_path.exists():
        data = json.loads(pinned_path.read_text(encoding="utf-8"))
        file_pins = {
            str(item.get("name")): item
            for item in data.get("pinned_references", [])
            if isinstance(item, dict) and item.get("name")
        }
        pins.update(file_pins)
        sources.append(str(pinned_path))
    return pins, sources


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reference_card_integrity(card_path: Path, previous_pin: dict[str, Any]) -> dict[str, Any]:
    expected = str(previous_pin.get("card_sha256") or "").strip()
    integrity: dict[str, Any] = {
        "status": "untracked",
        "algorithm": "sha256",
        "path": str(card_path),
    }
    if not expected:
        return integrity
    integrity["expected_sha256"] = expected
    if not card_path.exists():
        integrity["status"] = "missing"
        return integrity
    actual = file_sha256(card_path)
    integrity["actual_sha256"] = actual
    integrity["status"] = "ok" if actual == expected else "mismatch"
    return integrity


def card_integrity_conflicts(integrity: dict[str, Any]) -> list[str]:
    status = integrity.get("status")
    if status == "missing":
        return [f"reference card integrity missing: {integrity.get('path')}"]
    if status == "mismatch":
        return [f"card integrity mismatch: {integrity.get('path')}"]
    return []


def resolve_commit(repo_path: Path, spec: str) -> str | None:
    ok, output = run_git_optional(["rev-parse", "--verify", f"{spec}^{{commit}}"], cwd=repo_path)
    return output.strip() if ok and output else None


def target_commit(repo_path: Path, entry: dict[str, Any]) -> tuple[str | None, str, str | None]:
    if entry.get("commit"):
        spec = str(entry["commit"])
        return resolve_commit(repo_path, spec), "commit", spec
    if entry.get("tag"):
        spec = f"tags/{entry['tag']}"
        return resolve_commit(repo_path, spec), "tag", spec
    if entry.get("branch"):
        branch = str(entry["branch"])
        for spec in (f"origin/{branch}", branch):
            commit = resolve_commit(repo_path, spec)
            if commit:
                return commit, "branch", spec
        return None, "branch", f"origin/{branch}"
    return resolve_commit(repo_path, "HEAD"), "head", "HEAD"


def divergence_summary(repo_path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    upstream = None
    if entry.get("branch"):
        branch = str(entry["branch"])
        if resolve_commit(repo_path, f"origin/{branch}"):
            upstream = f"origin/{branch}"
    if not upstream:
        ok, output = run_git_optional(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], cwd=repo_path)
        if ok and output:
            upstream = output
    if not upstream:
        return {"available": False, "reason": "no local upstream tracking reference", "local_ahead": None, "local_behind": None}
    ok, output = run_git_optional(["rev-list", "--left-right", "--count", f"HEAD...{upstream}"], cwd=repo_path)
    if not ok:
        return {"available": False, "reason": output, "upstream": upstream, "local_ahead": None, "local_behind": None}
    parts = output.split()
    if len(parts) < 2:
        return {"available": False, "reason": "unexpected git divergence output", "upstream": upstream, "local_ahead": None, "local_behind": None}
    return {"available": True, "upstream": upstream, "local_ahead": int(parts[0]), "local_behind": int(parts[1])}


def changed_since_last_pin(repo_path: Path, old_commit: str | None) -> list[str]:
    if not old_commit:
        return []
    ok, output = run_git_optional(["log", "--oneline", "--max-count", "10", f"{old_commit}..HEAD"], cwd=repo_path)
    if not ok:
        return [f"could not compare with previous pin {old_commit[:12]}: {output}"]
    return [line.strip() for line in output.splitlines() if line.strip()]


def change_summary(repo_path: Path, old_commit: str | None, new_commit: str | None, max_count: int = 10) -> dict[str, Any]:
    if not old_commit:
        return {"available": False, "reason": "no previous pin", "commit_count": 0, "commits": []}
    if not new_commit:
        return {"available": False, "reason": "no target commit", "commit_count": None, "commits": []}
    ok, count_output = run_git_optional(["rev-list", "--count", f"{old_commit}..{new_commit}"], cwd=repo_path)
    if not ok:
        return {"available": False, "reason": count_output, "commit_count": None, "commits": []}
    ok, log_output = run_git_optional(["log", "--oneline", f"--max-count={max_count}", f"{old_commit}..{new_commit}"], cwd=repo_path)
    if not ok:
        return {"available": False, "reason": log_output, "commit_count": int(count_output or 0), "commits": []}
    commits = [line.strip() for line in log_output.splitlines() if line.strip()]
    return {
        "available": True,
        "commit_count": int(count_output or 0),
        "commits": commits,
        "truncated": int(count_output or 0) > len(commits),
    }


def git_path_exists(repo_path: Path, commit: str, raw: str) -> bool:
    normalized = str(raw).replace("\\", "/").lstrip("/")
    ok, _ = run_git_optional(["cat-file", "-e", f"{commit}:{normalized}"], cwd=repo_path)
    return ok


def git_diff_name_status(repo_path: Path, old_commit: str | None, new_commit: str | None) -> list[dict[str, Any]]:
    if not old_commit or not new_commit:
        return []
    ok, output = run_git_optional(["diff", "--name-status", "-M", old_commit, new_commit], cwd=repo_path)
    if not ok:
        return []
    changes: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            changes.append({"status": "renamed", "similarity": status[1:], "old_path": parts[1], "new_path": parts[2]})
        elif status == "D" and len(parts) >= 2:
            changes.append({"status": "deleted", "path": parts[1]})
        elif len(parts) >= 2:
            changes.append({"status": status, "path": parts[1]})
    return changes


def referenced_file_conflicts(repo_path: Path, entry: dict[str, Any]) -> list[str]:
    raw_files = entry.get("card_files") or entry.get("referenced_files") or []
    if not isinstance(raw_files, list):
        return ["referenced_files/card_files must be a list when provided"]
    warnings: list[str] = []
    for raw in raw_files:
        rel = Path(str(raw).replace("\\", "/"))
        if rel.is_absolute() or ".." in rel.parts:
            warnings.append(f"referenced file is unsafe and was ignored: {raw}")
            continue
        if not (repo_path / rel).exists():
            warnings.append(f"referenced upstream file is missing or renamed: {raw}")
    return warnings


def referenced_file_signals(
    repo_path: Path,
    entry: dict[str, Any],
    previous_commit: str | None,
    target: str | None,
) -> dict[str, Any]:
    raw_files = entry.get("card_files") or entry.get("referenced_files") or []
    if not isinstance(raw_files, list):
        return {
            "available": False,
            "reason": "referenced_files/card_files must be a list when provided",
            "files": [],
            "deleted": [],
            "renamed": [],
        }
    files: list[dict[str, Any]] = []
    for raw in raw_files:
        rel = Path(str(raw).replace("\\", "/"))
        if rel.is_absolute() or ".." in rel.parts:
            files.append({"path": str(raw), "safe": False, "exists_at_target": None, "issue": "unsafe path"})
            continue
        files.append(
            {
                "path": str(raw).replace("\\", "/"),
                "safe": True,
                "exists_at_target": bool(target and git_path_exists(repo_path, target, str(raw))),
            }
        )
    diff = git_diff_name_status(repo_path, previous_commit, target)
    tracked = {item["path"] for item in files if item.get("safe")}
    deleted = [item for item in diff if item.get("status") == "deleted" and item.get("path") in tracked]
    renamed = [item for item in diff if item.get("status") == "renamed" and item.get("old_path") in tracked]
    return {
        "available": target is not None,
        "files": files,
        "deleted": deleted,
        "renamed": renamed,
    }


def write_card(cards_dir: Path, entry: dict[str, Any], repo_path: Path, summary: dict[str, str]) -> Path:
    cards_dir.mkdir(parents=True, exist_ok=True)
    name = str(entry["name"])
    card_path = cards_dir / f"{name}.md"
    purpose = entry.get("purpose", "External implementation reference.")
    content = f"""# Reference Card: {name}

## Pin

- Repository: {entry.get('repository_url')}
- Local path: {repo_path}
- Branch: {summary['branch']}
- Commit: {summary['commit']}
- Committed at: {summary['committed_at']}
- Subject: {summary['subject']}
- Refreshed at: {utc_now()}

## Purpose

{purpose}

## Agent Notes

- Load this card before opening the reference repository.
- Open source files only for the specific pattern being reused.
- Do not copy project-specific secrets, settings, or credentials.
"""
    card_path.write_text(content, encoding="utf-8")
    return card_path


def dry_run_plan(
    workspace: Path,
    manifest_path: Path,
    output_root: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    previous = {item.get("name"): item for item in manifest.get("pinned_references", []) if isinstance(item, dict)}
    planned: list[dict[str, Any]] = []
    warnings: list[str] = []
    for entry in manifest["references"]:
        repo_path = resolve_checkout_path(workspace, output_root, entry)
        old_commit = (previous.get(entry.get("name")) or {}).get("commit")
        warnings.extend(credential_boundary_warnings(entry.get("repository_url")))
        planned.append(
            {
                "name": entry.get("name"),
                "repository_url": redacted_url(entry.get("repository_url")),
                "path": str(repo_path),
                "action": "update" if repo_path.exists() else "clone",
                "previous_commit": old_commit,
                "card": str(output_root / "cards" / f"{entry.get('name')}.md"),
            }
        )
    return {
        "schema_version": 1,
        "status": "dry-run",
        "manifest": str(manifest_path),
        "output_root": str(output_root),
        "planned_changes": planned,
        "warnings": sorted(set(warnings)),
    }


def reference_report(
    workspace: Path,
    manifest_path: Path,
    output_root: Path,
    manifest: dict[str, Any],
    stale_days: int,
) -> dict[str, Any]:
    previous, pin_sources = load_previous_pins(manifest, output_root)
    references: list[dict[str, Any]] = []
    warnings: list[str] = []
    skipped: list[dict[str, str]] = []
    for entry in manifest["references"]:
        if not isinstance(entry, dict):
            raise ValueError("each reference entry must be an object")
        if not entry.get("name"):
            raise ValueError("reference entry is missing name")
        name = str(entry["name"])
        repo_path = resolve_checkout_path(workspace, output_root, entry)
        warnings.extend(credential_boundary_warnings(entry.get("repository_url")))
        previous_pin = previous.get(name, {})
        previous_commit = previous_pin.get("commit")
        card_path = output_root / "cards" / f"{name}.md"
        card_integrity = reference_card_integrity(card_path, previous_pin)
        base: dict[str, Any] = {
            "name": name,
            "repository_url": redacted_url(entry.get("repository_url")),
            "path": str(repo_path),
            "previous_commit": previous_commit,
            "card": str(card_path),
            "card_integrity": card_integrity,
        }
        if not repo_path.exists():
            skipped.append({"name": name, "reason": "local mirror missing"})
            references.append({**base, "available": False, "reason": "local mirror missing"})
            continue
        if not (repo_path / ".git").exists():
            skipped.append({"name": name, "reason": "local path is not a Git repository"})
            references.append({**base, "available": False, "reason": "local path is not a Git repository"})
            continue
        target, target_type, target_spec = target_commit(repo_path, entry)
        current = commit_summary(repo_path, target) if target else None
        pinned = commit_summary(repo_path, str(previous_commit)) if previous_commit else None
        stale_source = pinned if previous_commit else current
        file_signals = referenced_file_signals(repo_path, entry, previous_commit, target)
        integrity_conflicts = card_integrity_conflicts(card_integrity)
        references.append(
            {
                **base,
                "available": target is not None,
                "target_type": target_type,
                "target_spec": target_spec,
                "target_commit": target,
                "target_subject": (current or {}).get("subject"),
                "target_committed_at": (current or {}).get("committed_at"),
                "changed": bool(previous_commit and target and previous_commit != target),
                "stale_pin": age_summary((stale_source or {}).get("committed_at"), stale_days),
                "upstream_divergence": divergence_summary(repo_path, entry),
                "changed_since_last_pin": change_summary(repo_path, previous_commit, target),
                "file_signals": file_signals,
                "conflicts": [
                    *integrity_conflicts,
                    *(f"referenced upstream file is missing or renamed: {item['path']}" for item in file_signals.get("files", []) if item.get("safe") and item.get("exists_at_target") is False),
                    *(f"referenced upstream file was deleted since last pin: {item['path']}" for item in file_signals.get("deleted", [])),
                    *(f"referenced upstream file was renamed since last pin: {item['old_path']} -> {item['new_path']}" for item in file_signals.get("renamed", [])),
                ],
            }
        )
    stale_count = sum(1 for ref in references if ref.get("stale_pin", {}).get("stale_by_age"))
    changed_count = sum(1 for ref in references if ref.get("changed"))
    conflict_count = sum(len(ref.get("conflicts", [])) for ref in references)
    return {
        "schema_version": 2,
        "tool": "external-reference-manager.sync_references",
        "ok": True,
        "status": "report-only",
        "generated_at": utc_now(),
        "manifest": str(manifest_path),
        "output_root": str(output_root),
        "pin_sources": pin_sources,
        "summary": {
            "reference_count": len(references),
            "available_count": sum(1 for ref in references if ref.get("available")),
            "changed_count": changed_count,
            "stale_count": stale_count,
            "conflict_count": conflict_count,
        },
        "references": references,
        "warnings": sorted(set(warnings)),
        "skipped": skipped,
    }


def sync(args: argparse.Namespace) -> dict[str, Any]:
    workspace = Path(args.workspace_root).resolve()
    manifest_path = ensure_inside(workspace, Path(args.manifest))
    output_root = ensure_inside(workspace, Path(args.output_root))
    # Report and dry-run branches return before filesystem writes; write mode starts below.
    if not manifest_path.exists() and not getattr(args, "write", False):
        mode = "dry-run" if getattr(args, "dry_run", False) else "report"
        return missing_manifest_report(workspace, manifest_path, output_root, mode)
    manifest = load_manifest(manifest_path)
    if getattr(args, "dry_run", False):
        return dry_run_plan(workspace, manifest_path, output_root, manifest)
    if not getattr(args, "write", False):
        return reference_report(workspace, manifest_path, output_root, manifest, args.stale_days)
    output_root.mkdir(parents=True, exist_ok=True)
    cards_dir = output_root / "cards"
    refreshed: list[dict[str, Any]] = []
    previous = {item.get("name"): item for item in manifest.get("pinned_references", []) if isinstance(item, dict)}
    for entry in manifest["references"]:
        if not isinstance(entry, dict):
            raise ValueError("each reference entry must be an object")
        if not entry.get("name"):
            raise ValueError("reference entry is missing name")
        repo_path = resolve_checkout_path(workspace, output_root, entry)
        clone_or_update(entry, repo_path, fetch=not args.no_fetch, allow_reset=args.allow_reset)
        summary = repo_summary(repo_path)
        card_path = write_card(cards_dir, entry, repo_path, summary)
        card_sha256 = file_sha256(card_path)
        old_commit = (previous.get(entry["name"]) or {}).get("commit")
        drift = stale_reference_summary(repo_path, summary["committed_at"], args.stale_days)
        refreshed.append(
            {
                "name": entry["name"],
                "repository_url": redacted_url(entry.get("repository_url")),
                "path": str(repo_path),
                "commit": summary["commit"],
                "previous_commit": old_commit,
                "changed": bool(old_commit and old_commit != summary["commit"]),
                "changed_since_last_pin": changed_since_last_pin(repo_path, old_commit),
                "stale_reference": drift,
                "conflicts": referenced_file_conflicts(repo_path, entry),
                "card": str(card_path),
                "card_sha256": card_sha256,
                "card_integrity": {
                    "status": "recorded",
                    "algorithm": "sha256",
                    "path": str(card_path),
                    "sha256": card_sha256,
                },
            }
        )
    pinned = {
        "schema_version": 1,
        "refreshed_at": utc_now(),
        "manifest": str(manifest_path),
        "pinned_references": refreshed,
    }
    pinned_path = output_root / "pinned-references.json"
    pinned_path.write_text(json.dumps(pinned, indent=2) + "\n", encoding="utf-8")
    return pinned


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Default report mode is read-only and inspects only local manifests, mirrors, pins, and cards. "
            "Dry-run mode does not fetch or write. "
            "--write is write-capable and may clone, fetch, reset with --allow-reset, and write caller-owned pins/cards."
        ),
    )
    parser.add_argument("--manifest", required=True, help="reference manifest inside the workspace")
    parser.add_argument("--output-root", required=True, help="caller-owned reference output folder inside the workspace")
    parser.add_argument("--workspace-root", default=".", help="workspace boundary for manifest, mirrors, and outputs")
    parser.add_argument("--no-fetch", action="store_true", help="do not fetch; use existing local mirrors only")
    parser.add_argument("--allow-reset", action="store_true", help="allow --write to reset/clean dirty reference mirrors")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--write", action="store_true", help="clone/update repositories and write pins/cards")
    mode_group.add_argument("--dry-run", action="store_true", help="report clone/fetch/pin/card changes without fetching or writing")
    parser.add_argument("--stale-days", type=int, default=180, help="warn when pinned commit is older than this")
    parser.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    return parser


def render_text(result: dict[str, Any]) -> str:
    lines: list[str] = []
    if result.get("status") == "dry-run":
        lines.append(f"planned {len(result['planned_changes'])} reference change(s)")
        for ref in result["planned_changes"]:
            lines.append(f"- {ref['name']}: {ref['action']} -> {ref['card']}")
    elif result.get("status") == "skipped":
        lines.append(f"external reference report skipped: {result.get('reason')}")
        next_command = result.get("next_command")
        if next_command:
            lines.append(f"next: {next_command}")
    elif result.get("status") == "report-only":
        summary = result["summary"]
        lines.append(
            "external reference report: "
            f"{summary['reference_count']} reference(s), "
            f"{summary['changed_count']} changed, "
            f"{summary['stale_count']} stale, "
            f"{summary['conflict_count']} conflict(s)"
        )
        for ref in result["references"]:
            if not ref.get("available"):
                lines.append(f"- {ref['name']}: unavailable ({ref.get('reason')})")
                continue
            stale = ref.get("stale_pin", {})
            change = ref.get("changed_since_last_pin", {})
            divergence = ref.get("upstream_divergence", {})
            lines.append(
                f"- {ref['name']}: {str(ref.get('target_commit') or '')[:12]} "
                f"changed={str(ref.get('changed')).lower()} "
                f"age={stale.get('age_days')}d "
                f"commits_since_pin={change.get('commit_count')} "
                f"ahead={divergence.get('local_ahead')} behind={divergence.get('local_behind')} "
                f"conflicts={len(ref.get('conflicts', []))}"
            )
    else:
        lines.append(f"refreshed {len(result['pinned_references'])} reference(s)")
        for ref in result["pinned_references"]:
            marker = "changed" if ref["changed"] else "pinned"
            stale = ref.get("stale_reference", {})
            age = stale.get("age_days")
            stale_text = f", age={age}d" if age is not None else ""
            lines.append(f"- {ref['name']}: {ref['commit'][:12]} ({marker}{stale_text})")
    for warning in result.get("warnings", []):
        lines.append(f"WARNING: {warning}")
    return "\n".join(lines)


def render_markdown(result: dict[str, Any]) -> str:
    lines = ["# External Reference Report", ""]
    if result.get("status") == "dry-run":
        lines.extend(["## Planned Changes", ""])
        for ref in result["planned_changes"]:
            lines.append(f"- `{ref['name']}`: {ref['action']} -> `{ref['card']}`")
    elif result.get("status") == "skipped":
        lines.extend(
            [
                "## Skipped",
                "",
                f"- Reason: {result.get('reason')}",
                f"- Manifest: `{result.get('manifest')}`",
            ]
        )
        if result.get("example_manifest"):
            lines.append(f"- Example manifest: `{result.get('example_manifest')}`")
        if result.get("next_command"):
            lines.append(f"- Next: {result.get('next_command')}")
    elif result.get("status") == "report-only":
        summary = result["summary"]
        lines.extend(
            [
                "## Summary",
                "",
                f"- References: {summary['reference_count']}",
                f"- Available local mirrors: {summary['available_count']}",
                f"- Changed since last pin: {summary['changed_count']}",
                f"- Stale pins: {summary['stale_count']}",
                f"- File conflicts: {summary['conflict_count']}",
                "",
                "## References",
                "",
            ]
        )
        for ref in result["references"]:
            lines.append(f"### {ref['name']}")
            if not ref.get("available"):
                lines.extend(["", f"- Status: unavailable ({ref.get('reason')})", ""])
                continue
            stale = ref.get("stale_pin", {})
            change = ref.get("changed_since_last_pin", {})
            divergence = ref.get("upstream_divergence", {})
            lines.extend(
                [
                    "",
                    f"- Target commit: `{str(ref.get('target_commit') or '')[:12]}`",
                    f"- Previous commit: `{str(ref.get('previous_commit') or '')[:12]}`",
                    f"- Pinned age: {stale.get('age_days')} day(s), stale: {stale.get('stale_by_age')}",
                    f"- Commits since last pin: {change.get('commit_count')}",
                    f"- Upstream divergence: ahead {divergence.get('local_ahead')}, behind {divergence.get('local_behind')}",
                ]
            )
            for conflict in ref.get("conflicts", []):
                lines.append(f"- Conflict: {conflict}")
            lines.append("")
    else:
        lines.extend(["## Refreshed", ""])
        for ref in result["pinned_references"]:
            lines.append(f"- `{ref['name']}`: `{ref['commit']}`")
    if result.get("warnings"):
        lines.extend(["## Warnings", ""])
        for warning in result["warnings"]:
            lines.append(f"- {warning}")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = sync(args)
    except Exception as exc:
        if args.format == "json":
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        if "ok" in result:
            print(json.dumps(result, indent=2))
        else:
            print(json.dumps({"ok": True, **result}, indent=2))
    elif args.format == "markdown":
        print(render_markdown(result), end="")
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
