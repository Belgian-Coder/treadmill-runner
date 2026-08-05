#!/usr/bin/env python3
"""Fresh-clone smoke helpers for repository release checks."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from repo_support import repo_common as repo
from repo_support import repo_harness_install


def _write_smoke_target_marker(target_root: Path, *, mode: str) -> dict[str, object]:
    marker = target_root / repo.HARNESS_SMOKE_TARGET_MARKER_REL
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "tool": "install-harness-smoke",
                    "temporary_validation_target": True,
                    "mode": mode,
                    "purpose": "Allow setup checks in this temporary target without claiming global user skill links.",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except OSError as exc:
        return {
            "name": "temporary-smoke-target-marker",
            "ok": False,
            "status": "failed",
            "path": repo.HARNESS_SMOKE_TARGET_MARKER_REL,
            "issue": str(exc),
        }
    return {
        "name": "temporary-smoke-target-marker",
        "ok": True,
        "status": "passed",
        "path": repo.HARNESS_SMOKE_TARGET_MARKER_REL,
    }


def _command_check(
    name: str,
    root: Path,
    command: list[str],
    *,
    timeout_seconds: int,
    runner=subprocess.run,
) -> dict[str, object]:
    try:
        completed = runner(
            command,
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_seconds,
            env=repo.child_env(),
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        return {
            "name": name,
            "ok": False,
            "status": "failed",
            "command": " ".join(command),
            "output_tail": output[-2000:] if isinstance(output, str) else "",
            "issue": f"timed out after {timeout_seconds}s",
        }
    except OSError as exc:
        return {
            "name": name,
            "ok": False,
            "status": "failed",
            "command": " ".join(command),
            "output_tail": "",
            "issue": str(exc),
        }
    return {
        "name": name,
        "ok": completed.returncode == 0,
        "status": "passed" if completed.returncode == 0 else "failed",
        "command": " ".join(command),
        "output_tail": completed.stdout[-2000:],
    }


def _startup_context_navigation_check(
    root: Path,
    *,
    runner=subprocess.run,
) -> dict[str, object]:
    command = [
        sys.executable,
        "-B",
        ".agents/manage.py",
        "startup-context",
        "--summary",
        "--compact",
        "--format",
        "json",
    ]
    try:
        completed = runner(
            command,
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
            env=repo.child_env(),
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return {
            "name": "consumer-startup-navigation",
            "ok": False,
            "status": "failed",
            "command": " ".join(command),
            "output_tail": output[-2000:],
            "issue": "timed out after 120s",
        }
    except OSError as exc:
        return {
            "name": "consumer-startup-navigation",
            "ok": False,
            "status": "failed",
            "command": " ".join(command),
            "output_tail": "",
            "issue": str(exc),
        }
    check: dict[str, object] = {
        "name": "consumer-startup-navigation",
        "ok": completed.returncode == 0,
        "status": "passed" if completed.returncode == 0 else "failed",
        "command": " ".join(command),
        "output_tail": (completed.stdout or "")[-2000:],
    }
    if completed.returncode != 0:
        return check
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        check["ok"] = False
        check["status"] = "failed"
        check["issue"] = f"startup-context output was not JSON: {exc}"
        return check
    navigation = payload.get("navigation") if isinstance(payload, dict) and isinstance(payload.get("navigation"), dict) else {}
    read_first = str(navigation.get("read_first") or "")
    status = str(navigation.get("status") or "unknown")
    check["navigation"] = {
        "status": status,
        "read_first": read_first,
        "next_command": navigation.get("next_command", ""),
    }
    if read_first != "automations/navigation/artifacts/maps/HANDOFF.md":
        check["ok"] = False
        check["status"] = "failed"
        check["issue"] = "startup-context did not route consumer agents to HANDOFF.md first"
    return check


def _installed_target_clean_state(target_root: Path) -> dict[str, object]:
    run_dirs = [
        path
        for path in (target_root / "automations").glob("*/runs/*")
        if path.is_dir()
    ]
    pycache_dirs = [path for path in target_root.rglob("__pycache__") if path.is_dir()]
    issues: list[str] = []
    if (target_root / ".git").exists():
        issues.append(".git directory was copied")
    if run_dirs:
        issues.append("workflow run history was copied")
    if pycache_dirs:
        issues.append("__pycache__ directories were copied or generated")
    if not (target_root / ".agents" / "harness.lock.json").exists():
        issues.append(".agents/harness.lock.json was not written")
    return {
        "name": "installed-target-clean-state",
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "run_history_count": len(run_dirs),
        "pycache_count": len(pycache_dirs),
        "issues": issues,
    }


def install_harness_smoke_report(
    root: Path,
    *,
    work_dir: Path | None = None,
    keep: bool = False,
    fast: bool = False,
    workflow_name: str = "user-story-workflow",
    runner=subprocess.run,
    command_runner: repo_harness_install.CommandRunner | None = None,
) -> dict[str, object]:
    created_temp = work_dir is None
    if work_dir is None:
        temp_parent = Path(tempfile.mkdtemp(prefix="skills-install-harness-smoke-"))
    else:
        temp_parent = work_dir.resolve()
        temp_parent.mkdir(parents=True, exist_ok=True)
    target_root = temp_parent / "prepared-target"
    checks: list[dict[str, object]] = []
    issues: list[str] = []
    run_id = "smoke-first-run"
    try:
        install_report = repo_harness_install.install_harness_report(
            root,
            target_root,
            run_setup_check=False,
            install_rg_portable=not fast,
            bootstrap_local_ai=not fast,
            command_runner=command_runner,
        )
        install_summary = install_report.get("summary") if isinstance(install_report.get("summary"), dict) else {}
        post_install = install_report.get("post_install") if isinstance(install_report.get("post_install"), list) else []
        checks.append(
            {
                "name": "prepared-install",
                "ok": bool(install_report.get("ok")),
                "status": install_report.get("status", "unknown"),
                "summary": install_summary,
                "post_install_steps": [
                    {
                        "name": row.get("name"),
                        "ok": bool(row.get("ok")),
                        "status": row.get("status", "unknown"),
                    }
                    for row in post_install
                    if isinstance(row, dict)
                ],
            }
        )
        if not install_report.get("ok"):
            issues.append("prepared install failed")
        if not issues:
            marker_check = _write_smoke_target_marker(
                target_root,
                mode="fast" if fast else "full",
            )
            checks.append(marker_check)
            if not marker_check.get("ok"):
                issues.append("temporary smoke target marker failed")
        if not issues:
            clean_state = _installed_target_clean_state(target_root)
            checks.append(clean_state)
            if not clean_state.get("ok"):
                issues.extend(str(item) for item in clean_state.get("issues", []))
        if not issues:
            project_setup = _command_check(
                "project-initialization-setup",
                target_root,
                [
                    sys.executable,
                    "-B",
                    ".agents/manage.py",
                    "setup",
                    "--no-link-skills",
                ],
                timeout_seconds=180,
                runner=runner,
            )
            checks.append(project_setup)
            if not project_setup.get("ok"):
                issues.append("project initialization setup failed")
        if not issues:
            project_context = _command_check(
                "project-context-check",
                target_root,
                [
                    sys.executable,
                    "-B",
                    ".agents/skills/repo-navigation/scripts/repo_navigation.py",
                    "project-context",
                    "--target",
                    ".",
                    "--check",
                ],
                timeout_seconds=120,
                runner=runner,
            )
            checks.append(project_context)
            if not project_context.get("ok"):
                issues.append("project context check failed")
        if not issues:
            startup_navigation = _startup_context_navigation_check(
                target_root,
                runner=runner,
            )
            checks.append(startup_navigation)
            if not startup_navigation.get("ok"):
                issues.append("consumer startup navigation check failed")
        if not issues and not fast:
            workflow_start = _command_check(
                "workflow-start-first-run",
                target_root,
                [
                    sys.executable,
                    "-B",
                    ".agents/manage.py",
                    "workflow",
                    "start",
                    "--name",
                    workflow_name,
                    "--run-id",
                    run_id,
                    "--format",
                    "json",
                ],
                timeout_seconds=180,
                runner=runner,
            )
            checks.append(workflow_start)
            if not workflow_start.get("ok"):
                issues.append("workflow start failed")
        if not issues and not fast:
            workflow_resume = _command_check(
                "workflow-resume-new-chat",
                target_root,
                [
                    sys.executable,
                    "-B",
                    ".agents/manage.py",
                    "workflow",
                    "resume",
                    "--name",
                    workflow_name,
                    "--run-id",
                    run_id,
                    "--format",
                    "json",
                ],
                timeout_seconds=180,
                runner=runner,
            )
            checks.append(workflow_resume)
            if not workflow_resume.get("ok"):
                issues.append("workflow resume failed")
        if not issues and not fast:
            context_packet = (
                target_root
                / "automations"
                / workflow_name
                / "runs"
                / run_id
                / "artifacts"
                / "context"
                / "context-packet.json"
            )
            checks.append(
                {
                    "name": "workflow-context-packet",
                    "ok": context_packet.exists(),
                    "status": "passed" if context_packet.exists() else "failed",
                    "path": str(context_packet.relative_to(target_root)),
                }
            )
            if not context_packet.exists():
                issues.append("workflow resume did not write context-packet.json")
        if not issues and not fast:
            for smoke_workflow in ("user-story-workflow", "bug-ticket-workflow"):
                workflow_smoke = _command_check(
                    f"consumer-{smoke_workflow}-smoke",
                    target_root,
                    [
                        sys.executable,
                        "-B",
                        ".agents/manage.py",
                        "workflow",
                        "smoke",
                        "--name",
                        smoke_workflow,
                        "--summary",
                        "--format",
                        "json",
                    ],
                    timeout_seconds=240,
                    runner=runner,
                )
                checks.append(workflow_smoke)
                if not workflow_smoke.get("ok"):
                    issues.append(f"{smoke_workflow} consumer workflow smoke failed")
        if not issues and fast:
            checks.append(
                {
                    "name": "workflow-first-run",
                    "ok": True,
                    "status": "skipped",
                    "reason": "fast mode skips workflow start/resume/context; run full install-harness-smoke before release",
                }
            )
    finally:
        if not keep:
            if created_temp:
                shutil.rmtree(temp_parent, ignore_errors=True)
            elif target_root.exists() and temp_parent in target_root.resolve().parents:
                shutil.rmtree(target_root, ignore_errors=True)
    return {
        "schema_version": 1,
        "tool": "install-harness-smoke",
        "mode": "fast" if fast else "full",
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "source_root": str(root),
        "target_root": str(target_root),
        "workflow_name": workflow_name,
        "run_id": run_id,
        "checks": checks,
        "issues": issues,
        "skipped": (
            ([] if keep else ["temporary install target was removed"])
            + (
                [
                    "portable ripgrep install, local-AI bootstrap, and workflow start/resume skipped by --fast",
                ]
                if fast
                else []
            )
        ),
        "next_command": (
            "python -B .agents/manage.py install-harness-smoke --format json"
            if fast
            else "python -B .agents/manage.py install-harness-smoke --fast --format json"
        ),
    }


def install_harness_smoke(args: argparse.Namespace, root: Path) -> int:
    report = install_harness_smoke_report(
        root,
        work_dir=Path(args.work_dir).expanduser() if args.work_dir else None,
        keep=args.keep,
        fast=bool(getattr(args, "fast", False)),
        workflow_name=args.workflow_name,
    )
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("# Install Harness Smoke")
        print(f"- Status: {report['status']}")
        print(f"- Mode: {report['mode']}")
        print(f"- Target: `{report['target_root']}`")
        print(f"- Workflow: `{report['workflow_name']}`")
        print("- What happened: temporary install, clean-state verification, setup check, project-context check, and startup navigation proof.")
        if report["mode"] == "full":
            print("- Additional proof: workflow start, resume like a new chat, and context packet creation.")
        else:
            print("- Additional proof: skipped; run full mode before release or installer changes.")
        for check in report["checks"]:
            status = check.get("status", "passed")
            print(f"- {check['name']}: {status if status == 'skipped' else ('ok' if check['ok'] else 'failed')}")
        if report["issues"]:
            print()
            print("## Issues")
            for issue in report["issues"]:
                print(f"- {issue}")
        print(f"- Next command: `{report['next_command']}`")
    return 0 if report["ok"] else 1

def fresh_clone_smoke_report(
    root: Path,
    *,
    source: str = "local",
    work_dir: Path | None = None,
    keep: bool = False,
    runner=subprocess.run,
) -> dict[str, object]:
    git = shutil.which("git")
    if not git:
        return {
            "schema_version": 1,
            "tool": "fresh-clone-smoke",
            "ok": False,
            "status": "failed",
            "issues": ["git was not found on PATH"],
            "checks": [],
        }
    temp_parent: Path
    created_temp = work_dir is None
    if work_dir is None:
        temp_parent = Path(tempfile.mkdtemp(prefix="skills-fresh-clone-"))
    else:
        temp_parent = work_dir.resolve()
        temp_parent.mkdir(parents=True, exist_ok=True)
    clone_path = temp_parent / "skills-fresh-clone"
    checks: list[dict[str, object]] = []
    issues: list[str] = []
    source_value = str(root)
    if source == "origin":
        origin = runner(
            [git, "config", "--get", "remote.origin.url"],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        source_value = origin.stdout.strip() if origin.returncode == 0 and origin.stdout.strip() else str(root)
    clone_command = [git, "-c", "core.longpaths=true", "clone", "--no-local", source_value, str(clone_path)]
    if source == "origin" and source_value != str(root):
        clone_command = [git, "-c", "core.longpaths=true", "clone", "--depth", "1", source_value, str(clone_path)]
    try:
        if clone_path.exists():
            issues.append(f"fresh clone target already exists: {clone_path}")
        else:
            clone = runner(
                clone_command,
                cwd=root,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            checks.append(
                {
                    "name": "git-clone",
                    "ok": clone.returncode == 0,
                    "command": " ".join(clone_command),
                    "output_tail": clone.stdout[-2000:],
                }
            )
            if clone.returncode != 0:
                issues.append("fresh clone failed")
            else:
                longpaths = runner(
                    [git, "config", "core.longpaths", "true"],
                    cwd=clone_path,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                checks.append(
                    {
                        "name": "git-longpaths",
                        "ok": longpaths.returncode == 0,
                        "command": f"{git} config core.longpaths true",
                        "output_tail": longpaths.stdout[-2000:],
                    }
                )
                if longpaths.returncode != 0:
                    issues.append("fresh clone could not enable git longpaths")
        commands = [
            [sys.executable, "-B", ".agents/manage.py", "setup", "--check", "--no-link-skills"],
            [sys.executable, "-B", ".agents/manage.py", "check-repo-health", "--json"],
            [sys.executable, "-B", ".agents/manage.py", "sync", "--check"],
            [sys.executable, "-B", ".agents/manage.py", "validate", "--deep"],
        ]
        if not issues:
            for command in commands:
                completed = runner(
                    command,
                    cwd=clone_path,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=repo.child_env(),
                )
                checks.append(
                    {
                        "name": " ".join(command[3:]) if len(command) > 3 else command[0],
                        "ok": completed.returncode == 0,
                        "command": " ".join(command),
                        "output_tail": completed.stdout[-2000:],
                    }
                )
                if completed.returncode != 0:
                    issues.append(f"fresh clone command failed: {' '.join(command[3:])}")
                    break
    finally:
        if not keep:
            if created_temp:
                shutil.rmtree(temp_parent, ignore_errors=True)
            elif clone_path.exists() and temp_parent in clone_path.resolve().parents:
                shutil.rmtree(clone_path, ignore_errors=True)
    return {
        "schema_version": 1,
        "tool": "fresh-clone-smoke",
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "source": source,
        "source_path": source_value,
        "clone_path": str(clone_path),
        "checks": checks,
        "issues": issues,
        "skipped": [] if keep else ["temporary clone was removed"],
    }


def fresh_clone_smoke(args: argparse.Namespace, root: Path) -> int:
    report = fresh_clone_smoke_report(
        root,
        source=args.source,
        work_dir=Path(args.work_dir).expanduser() if args.work_dir else None,
        keep=args.keep,
    )
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("# Fresh Clone Smoke")
        print(f"- Status: {report['status']}")
        print(f"- Source: `{report['source_path']}`")
        for check in report["checks"]:
            print(f"- {check['name']}: {'ok' if check['ok'] else 'failed'}")
        if report["issues"]:
            print()
            print("## Issues")
            for issue in report["issues"]:
                print(f"- {issue}")
    return 0 if report["ok"] else 1
