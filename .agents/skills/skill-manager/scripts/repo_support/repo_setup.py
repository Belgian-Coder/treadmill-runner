#!/usr/bin/env python3
"""Beginner setup and user-level skill linking helpers."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

sys.dont_write_bytecode = True

from repo_support import repo_common as repo
from repo_support import repo_policy
from repo_support import repo_portable_tools
from repo_support.repo_navigation_status import navigation_status

NEXT_PROMPT = (
    "Read AGENTS.md, .agents/routing.md, and automations/routing.md. "
    "Then run setup --check and report generated-file status, skill-link status, "
    "project-initialization status, validation result, skipped checks, and the "
    "next recommended file to open. If present, load docs/project/project-context.md "
    "and automations/navigation/artifacts/maps/HANDOFF.md before implementation planning."
)
PROJECT_CONTEXT_REL = Path("docs/project/project-context.md")
SMOKE_TARGET_MARKER_REL = Path(repo.HARNESS_SMOKE_TARGET_MARKER_REL)
NAVIGATION_REQUIRED_RELS = (
    Path("automations/navigation/WORKFLOW.md"),
    Path("automations/navigation/module.json"),
    Path("automations/navigation/artifacts/maps/HANDOFF.md"),
    Path("automations/navigation/artifacts/maps/staleness.json"),
)


def default_user_skills_path(tool: str) -> Path:
    home = Path.home()
    if tool == "Codex":
        return home / ".codex" / "skills"
    if tool == "Claude":
        return home / ".claude" / "skills"
    if tool == "Copilot":
        return home / ".copilot" / "skills"
    raise ValueError(f"unknown tool: {tool}")


def existing_resolved(path: Path) -> Path | None:
    if not path.exists() and not path.is_symlink():
        return None
    try:
        return path.resolve(strict=True)
    except FileNotFoundError:
        return path.resolve(strict=False)


def iter_skill_files(skill_dir: Path) -> list[Path]:
    files: list[Path] = []
    for current_root, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = [
            name
            for name in sorted(dirnames, key=str.lower)
            if name not in repo.IGNORED_SCAN_DIRS and not name.endswith(".egg-info")
        ]
        current = Path(current_root)
        for filename in sorted(filenames, key=str.lower):
            files.append(current / filename)
    return files


def file_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def skill_copy_matches(source: Path, candidate: Path) -> bool:
    if not candidate.is_dir():
        return False
    source_files = {
        repo.relative(source, path): file_bytes(path)
        for path in iter_skill_files(source)
        if path.is_file()
    }
    candidate_files = {
        repo.relative(candidate, path): file_bytes(path)
        for path in iter_skill_files(candidate)
        if path.is_file()
    }
    return bool(source_files) and source_files == candidate_files


def create_skill_link_or_copy(
    link_path: Path,
    target_path: Path,
    mode: str,
    dry_run: bool,
) -> str:
    if dry_run:
        return "copy" if mode == "copy" else "link"
    if mode != "copy":
        try:
            os.symlink(target_path, link_path, target_is_directory=True)
            return "linked"
        except OSError:
            if mode == "link":
                raise
    shutil.copytree(target_path, link_path)
    return "copied"


def build_link_skills_report(
    *,
    source_root: Path,
    target_paths: dict[str, Path],
    targets: list[str],
    mode: str,
    dry_run: bool,
    check: bool,
) -> dict[str, Any]:
    source_root = source_root.expanduser().resolve()
    if not source_root.exists():
        raise SystemExit(f"Skill source path not found: {source_root}")
    skills = repo.skill_directories(source_root)
    report: dict[str, Any] = {
        "schema_version": 1,
        "tool": "link-skills",
        "ok": True,
        "status": "ok",
        "source_root": str(source_root),
        "mode": "check" if check else "dry-run" if dry_run else mode,
        "tools": {},
        "actions": [],
        "skipped": [],
        "failures": [],
    }
    if not skills:
        report["skipped"].append(f"No skill folders found under {source_root}.")
        return report

    for target in targets:
        skills_root = target_paths[target].expanduser()
        summary = {
            "target_path": str(skills_root.resolve(strict=False)),
            "planned": 0,
            "linked": 0,
            "copied": 0,
            "already_present": 0,
            "missing": 0,
            "skipped": 0,
        }
        report["tools"][target] = summary
        if not dry_run and not check:
            skills_root.mkdir(parents=True, exist_ok=True)
        for skill_dir in skills:
            link_path = skills_root / skill_dir.name
            target_path = skill_dir.resolve()
            existing_path = existing_resolved(link_path)
            action: dict[str, Any] = {
                "tool": target,
                "skill": skill_dir.name,
                "path": str(link_path),
            }
            if dry_run:
                if existing_path is None:
                    summary["planned"] += 1
                    action["status"] = "planned"
                    action["action"] = "copy" if mode == "copy" else "link"
                elif repo.same_path(existing_path, target_path) or skill_copy_matches(
                    target_path, link_path
                ):
                    summary["already_present"] += 1
                    action["status"] = "already-present"
                else:
                    summary["skipped"] += 1
                    action["status"] = "skipped"
                    message = (
                        f"Skipping {skill_dir.name} for {target}: {link_path} "
                        f"already exists and points to {existing_path}."
                    )
                    action["message"] = message
                    report["skipped"].append(message)
                report["actions"].append(action)
                continue

            if existing_path is not None:
                if repo.same_path(existing_path, target_path) or skill_copy_matches(
                    target_path, link_path
                ):
                    summary["already_present"] += 1
                    action["status"] = "already-present"
                else:
                    summary["skipped"] += 1
                    action["status"] = "skipped"
                    message = (
                        f"Skipping {skill_dir.name} for {target}: {link_path} "
                        f"already exists and points to {existing_path}."
                    )
                    action["message"] = message
                    report["skipped"].append(message)
                report["actions"].append(action)
                continue

            if check:
                summary["missing"] += 1
                action["status"] = "missing"
                report["failures"].append(
                    f"Missing {target} skill link or copy: {link_path}"
                )
                report["actions"].append(action)
                continue

            result = create_skill_link_or_copy(link_path, target_path, mode, dry_run=False)
            summary[result] += 1
            action["status"] = result
            report["actions"].append(action)

    if report["failures"] or report["skipped"]:
        report["ok"] = False
        report["status"] = "issues found"
    return report


def captured_action(label: str, callback: Callable[[], int]) -> dict[str, Any]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            status = callback()
        except SystemExit as exc:
            status = int(exc.code) if isinstance(exc.code, int) else 1
            if exc.code and not isinstance(exc.code, int):
                print(str(exc.code), file=sys.stderr)
    return {
        "ok": status == 0,
        "status": status,
        "stdout": stdout.getvalue().strip(),
        "stderr": stderr.getvalue().strip(),
        "label": label,
    }


def command_text(parts: list[str]) -> str:
    return " ".join(parts)


def ripgrep_install_candidates() -> list[list[str]]:
    if sys.platform.startswith("win"):
        candidates = [
            ["winget", "install", "--id", "BurntSushi.ripgrep.MSVC", "-e", "--accept-package-agreements", "--accept-source-agreements"],
            ["scoop", "install", "ripgrep"],
            ["choco", "install", "ripgrep", "-y"],
        ]
    elif sys.platform == "darwin":
        candidates = [["brew", "install", "ripgrep"]]
    else:
        candidates = [
            ["brew", "install", "ripgrep"],
            ["apt-get", "install", "-y", "ripgrep"],
            ["dnf", "install", "-y", "ripgrep"],
            ["pacman", "-S", "--noconfirm", "ripgrep"],
        ]
    return [candidate for candidate in candidates if shutil.which(candidate[0])]


def install_ripgrep() -> dict[str, Any]:
    candidates = ripgrep_install_candidates()
    if not candidates:
        return {
            "ok": False,
            "status": "no-package-manager",
            "command": "",
            "suggested": "Install portable ripgrep with setup --install-rg-portable, or install ripgrep with your OS package manager.",
        }
    command = candidates[0]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=900,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "status": "failed", "command": command_text(command), "output_tail": str(exc)[-4000:]}
    return {
        "ok": completed.returncode == 0 and shutil.which("rg") is not None,
        "status": "installed" if completed.returncode == 0 and shutil.which("rg") else "failed",
        "command": command_text(command),
        "returncode": completed.returncode,
        "output_tail": completed.stdout[-4000:],
    }


def ripgrep_tool_report(args: Any, root: Path | None = None) -> dict[str, Any]:
    repo_root = root or Path.cwd()
    portable_requested = bool(getattr(args, "install_rg_portable", False))
    if portable_requested:
        return repo_portable_tools.install_portable_ripgrep(repo_root)

    portable = repo_portable_tools.verified_portable_ripgrep(repo_root)
    if portable.get("ok"):
        return portable
    if portable.get("status") in {"hash-mismatch", "version-mismatch", "version-check-failed", "unverified"}:
        portable["suggested"] = "Portable ripgrep cache is invalid; rerun setup --install-rg-portable."
        return portable

    path = shutil.which("rg")
    if path:
        version = ""
        try:
            completed = subprocess.run(
                [path, "--version"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=10,
            )
            version = completed.stdout.splitlines()[0] if completed.stdout else ""
        except (OSError, subprocess.TimeoutExpired):
            version = ""
        return {"ok": True, "status": "present", "source": "global", "path": path, "version": version}

    install_requested = bool(getattr(args, "install_rg", False))
    interactive_prompt = (
        not bool(getattr(args, "check", False))
        and not bool(getattr(args, "dry_run", False))
        and not bool(getattr(args, "no_tool_prompts", False))
        and sys.stdin.isatty()
    )
    if not install_requested and interactive_prompt:
        try:
            answer = input(
                "ripgrep (rg) was not found. Download pinned portable rg? [p=portable/g=global/n=skip] "
            ).strip().lower()
        except EOFError:
            answer = ""
        if answer in {"p", "portable", "y", "yes"}:
            return repo_portable_tools.install_portable_ripgrep(repo_root)
        install_requested = answer in {"g", "global"}

    report: dict[str, Any] = {
        "ok": True,
        "status": "missing",
        "required": False,
        "portable_status": portable.get("status", "unknown"),
        "suggested": "Install pinned portable ripgrep with setup --install-rg-portable, or use setup --install-rg for a package-manager install.",
    }
    if install_requested:
        install = install_ripgrep()
        report["install"] = install
        report["ok"] = bool(install.get("ok"))
        report["status"] = "installed" if install.get("ok") else "install-failed"
    return report


def load_json_output(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def summarize_command_result(command: list[str], completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    data = load_json_output(completed.stdout)
    report: dict[str, Any] = {
        "ok": completed.returncode == 0,
        "status": str(data.get("status") or ("passed" if completed.returncode == 0 else "failed")),
        "returncode": completed.returncode,
        "command": command_text(command),
    }
    for key in ("written", "stale", "issues", "context_path", "draft_path", "next_command", "detected"):
        if key in data:
            report[key] = data[key]
    if "files_scanned" in data:
        report["files_scanned"] = data["files_scanned"]
    if "skipped" in data and isinstance(data["skipped"], list):
        report["skipped_count"] = len(data["skipped"])
    if completed.returncode != 0 and not data:
        report["output_tail"] = completed.stdout[-2000:]
    return report


def run_json_command(root: Path, command: list[str], timeout_seconds: int = 180) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            check=False,
            env=repo.child_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return {
            "ok": False,
            "status": "timeout",
            "returncode": None,
            "command": command_text(command),
            "output_tail": output[-2000:],
        }
    except OSError as exc:
        return {
            "ok": False,
            "status": "failed-to-start",
            "returncode": None,
            "command": command_text(command),
            "output_tail": str(exc),
        }
    return summarize_command_result(command, completed)


def repo_navigation_command(root: Path, *args: str, timeout_seconds: int = 180) -> dict[str, Any]:
    script = root / ".agents" / "skills" / "repo-navigation" / "scripts" / "repo_navigation.py"
    command = [sys.executable, "-B", str(script), *args, "--format", "json"]
    if not script.exists():
        return {
            "ok": False,
            "status": "missing-repo-navigation",
            "command": command_text(command),
            "issues": [f"missing repo-navigation script: {script}"],
        }
    return run_json_command(root, command, timeout_seconds=timeout_seconds)


def project_context_generator_command(root: Path, *, overwrite: bool = False) -> dict[str, Any]:
    script = root / ".agents" / "skills" / "project-context-generator" / "scripts" / "generate_project_context.py"
    command = [
        sys.executable,
        "-B",
        str(script),
        "--target",
        str(root),
        "--write",
        "--format",
        "json",
    ]
    if overwrite:
        command.append("--overwrite")
    if not script.exists():
        return {
            "ok": False,
            "status": "missing-project-context-generator",
            "command": command_text(command),
            "issues": [f"missing project-context-generator script: {script}"],
        }
    return run_json_command(root, command, timeout_seconds=180)


def navigation_missing_paths(root: Path) -> list[str]:
    return [path.as_posix() for path in NAVIGATION_REQUIRED_RELS if not (root / path).exists()]


def context_looks_like_copied_harness(root: Path) -> bool:
    context_path = root / PROJECT_CONTEXT_REL
    if not (root / repo.HARNESS_INSTALL_MANIFEST_REL).exists() or not context_path.exists():
        return False
    try:
        text = context_path.read_text(encoding="utf-8-sig", errors="replace").lower()
    except OSError:
        return False
    return (
        "reusable ai skills" in text
        and "stateful workflow modules" in text
        and "generated routing" in text
    )


def is_temporary_smoke_target(root: Path) -> bool:
    marker = root / SMOKE_TARGET_MARKER_REL
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(data, dict)
        and data.get("tool") == "install-harness-smoke"
        and data.get("temporary_validation_target") is True
    )


def is_installed_harness_consumer(root: Path) -> bool:
    return bool(repo.installed_harness_manifest_paths(root))


def build_project_initialization_report(args: Any, root: Path) -> dict[str, Any]:
    mode = "check" if args.check else "dry-run" if args.dry_run else "write"
    report: dict[str, Any] = {
        "ok": True,
        "ready": True,
        "status": "ready",
        "mode": mode,
        "navigation": {},
        "project_context": {},
        "project_policy": {},
        "planned": [],
        "warnings": [],
    }
    write_enabled = not args.check and not args.dry_run
    policy_path = root / repo_policy.PROJECT_POLICY_PATH
    if args.dry_run:
        report["project_policy"] = {
            "ok": True,
            "ready": policy_path.is_file(),
            "status": "planned-refresh" if policy_path.is_file() else "planned-initialization",
            "path": repo_policy.PROJECT_POLICY_PATH,
            "next_command": "python -B .agents/manage.py setup",
        }
        report["planned"].append(
            "refresh complete project policy" if policy_path.is_file() else "initialize complete project policy"
        )
    elif write_enabled:
        policy_ok, policy_message = repo_policy.refresh_project_policy(root)
        _policy, policy_issues, policy_exists = repo_policy.load_project_policy(root)
        report["project_policy"] = {
            "ok": policy_ok and policy_exists and not policy_issues,
            "ready": policy_ok and policy_exists and not policy_issues,
            "status": "ready" if policy_ok and policy_exists and not policy_issues else "failed",
            "path": repo_policy.PROJECT_POLICY_PATH,
            "message": policy_message,
            "issues": policy_issues,
        }
        if not report["project_policy"]["ok"]:
            report["ok"] = False
            report["ready"] = False
    else:
        _policy, policy_issues, policy_exists = repo_policy.load_project_policy(root)
        report["project_policy"] = {
            "ok": not policy_issues,
            "ready": policy_exists and not policy_issues,
            "status": "ready" if policy_exists and not policy_issues else "needs-initialization" if not policy_exists else "failed",
            "path": repo_policy.PROJECT_POLICY_PATH,
            "issues": policy_issues,
            "next_command": "python -B .agents/manage.py setup" if not policy_exists else "python -B .agents/manage.py policy validate",
        }
        if policy_issues:
            report["ok"] = False
        if not policy_exists or policy_issues:
            report["ready"] = False
    navigation_missing = navigation_missing_paths(root)
    if args.dry_run:
        report["navigation"] = {
            "ok": True,
            "ready": not navigation_missing,
            "status": "planned-install" if navigation_missing else "planned-check",
            "missing": navigation_missing,
            "next_command": "python -B .agents/manage.py setup",
        }
        if navigation_missing:
            report["planned"].append("install navigation workflow and generated maps")
    elif write_enabled:
        if navigation_missing:
            navigation = repo_navigation_command(root, "install", "--target", str(root), "--write")
        else:
            navigation = repo_navigation_command(root, "check", "--target", str(root))
            if not navigation.get("ok"):
                navigation = repo_navigation_command(root, "update", "--target", str(root), "--write")
        report["navigation"] = navigation
        if not navigation.get("ok"):
            report["ok"] = False
            report["ready"] = False
    elif navigation_missing:
        report["navigation"] = {
            "ok": True,
            "ready": False,
            "status": "needs-initialization",
            "missing": navigation_missing,
            "next_command": "python -B .agents/manage.py setup",
        }
        report["ready"] = False
    else:
        navigation = repo_navigation_command(root, "check", "--target", str(root))
        if not navigation.get("ok"):
            navigation["raw_ok"] = False
            navigation["ok"] = True
            navigation["ready"] = False
            navigation["next_command"] = "python -B .agents/manage.py setup"
            report["ready"] = False
        else:
            navigation["ready"] = True
        report["navigation"] = navigation

    context_path = root / PROJECT_CONTEXT_REL
    copied_harness_context = context_looks_like_copied_harness(root)
    if args.dry_run:
        context_missing = not context_path.exists()
        report["project_context"] = {
            "ok": True,
            "ready": not context_missing and not copied_harness_context,
            "status": "planned-generation" if context_missing or copied_harness_context else "planned-check",
            "context_path": PROJECT_CONTEXT_REL.as_posix(),
            "next_command": "python -B .agents/manage.py setup",
        }
        if context_missing or copied_harness_context:
            report["planned"].append("generate project context package")
    elif write_enabled:
        context_check = repo_navigation_command(root, "project-context", "--target", str(root), "--check")
        should_generate = copied_harness_context or not context_check.get("ok")
        if should_generate:
            generated = project_context_generator_command(root, overwrite=copied_harness_context)
            context_check = repo_navigation_command(root, "project-context", "--target", str(root), "--check")
            context_check["generation"] = generated
            if not generated.get("ok"):
                context_check["ok"] = False
        if not context_check.get("ok"):
            context_check["raw_ok"] = False
            context_check["ok"] = bool(context_check.get("generation", {}).get("ok"))
            context_check["ready"] = False
            context_check["next_command"] = str(
                context_check.get("next_command")
                or "review docs/project/project-context.md or docs/project/project-context.generated.md"
            )
            report["ready"] = False
        else:
            context_check["ready"] = True
        report["project_context"] = context_check
        if not context_check.get("ok"):
            report["ok"] = False
    else:
        if not context_path.exists() or copied_harness_context:
            report["project_context"] = {
                "ok": True,
                "ready": False,
                "status": "needs-generation",
                "context_path": PROJECT_CONTEXT_REL.as_posix(),
                "next_command": "python -B .agents/manage.py setup",
            }
            report["ready"] = False
        else:
            context_check = repo_navigation_command(root, "project-context", "--target", str(root), "--check")
            if not context_check.get("ok"):
                context_check["raw_ok"] = False
                context_check["ok"] = True
                context_check["ready"] = False
                context_check["next_command"] = "python -B .agents/manage.py setup"
                report["ready"] = False
            else:
                context_check["ready"] = True
            report["project_context"] = context_check

    if not report["ok"]:
        report["status"] = "failed"
    elif report["ready"]:
        report["status"] = "ready"
    else:
        report["status"] = "needs-initialization" if mode != "write" else "review-needed"
    return report


def target_paths_from_args(args: Any) -> dict[str, Path]:
    return {
        "Codex": Path(args.codex_skills_path).expanduser()
        if args.codex_skills_path
        else default_user_skills_path("Codex"),
        "Claude": Path(args.claude_skills_path).expanduser()
        if args.claude_skills_path
        else default_user_skills_path("Claude"),
        "Copilot": Path(args.copilot_skills_path).expanduser()
        if args.copilot_skills_path
        else default_user_skills_path("Copilot"),
    }


def build_setup_report(
    args: Any,
    root: Path,
    *,
    sync_all_func: Callable[[Path, bool], int],
    validate_func: Callable[[Path], int],
    deep_validate_func: Callable[[Path], int],
) -> dict[str, Any]:
    checks: list[str] = []
    skipped: list[str] = []
    failures: list[str] = []
    actions: dict[str, Any] = {
        "python": {
            "ok": True,
            "version": ".".join(str(part) for part in sys.version_info[:3]),
        }
    }
    rg_report = ripgrep_tool_report(args, root)
    actions["ripgrep"] = rg_report
    checks.append("ripgrep checked")
    if rg_report["status"] == "missing":
        skipped.append(str(rg_report.get("suggested", "ripgrep is optional and missing")))
    elif rg_report["status"] == "install-failed":
        failures.append("ripgrep install failed")
    elif not rg_report.get("ok", True):
        failures.append(str(rg_report.get("suggested", "ripgrep setup failed")))
    sync_check = bool(args.check or args.dry_run)
    sync_result = captured_action(
        "generated artifacts",
        lambda: sync_all_func(root, check=sync_check),
    )
    sync_result["mode"] = "check" if sync_check else "write"
    actions["sync"] = sync_result
    checks.append("generated artifacts checked" if sync_check else "generated artifacts synced")
    if not sync_result["ok"]:
        failures.append("generated artifacts are missing or stale")

    project_initialization = build_project_initialization_report(args, root)
    actions["project_initialization"] = project_initialization
    checks.append("project navigation and context checked" if args.check else "project navigation and context planned" if args.dry_run else "project navigation and context initialized")
    if not project_initialization.get("ok", True):
        failures.append("project initialization failed")
    elif not project_initialization.get("ready", True):
        skipped.append(
            "project initialization needs review; for read-only tasks, read docs/project/project-context.md and automations/navigation/artifacts/maps/HANDOFF.md when present; run write-mode setup only when repo maintenance writes are allowed"
        )

    if not args.check and not args.dry_run:
        post_project_sync = captured_action(
            "generated artifacts after project initialization",
            lambda: sync_all_func(root, check=False),
        )
        post_project_sync["mode"] = "write"
        actions["post_project_sync"] = post_project_sync
        checks.append("generated artifacts synced after project initialization")
        if not post_project_sync["ok"]:
            failures.append("generated artifacts are missing or stale after project initialization")
        final_project_initialization = build_project_initialization_report(args, root)
        actions["final_project_initialization"] = final_project_initialization
        checks.append("project navigation and context refreshed after generated artifact sync")
        if not final_project_initialization.get("ok", True):
            failures.append("project initialization refresh failed")
        elif not final_project_initialization.get("ready", True):
            skipped.append(
                "project initialization refresh needs review; for read-only tasks, read docs/project/project-context.md and automations/navigation/artifacts/maps/HANDOFF.md when present; run write-mode setup only when repo maintenance writes are allowed"
            )

    link_report: dict[str, Any] | None = None
    if args.no_link_skills:
        skipped.append("skill linking skipped by --no-link-skills")
        actions["link_skills"] = {"ok": True, "status": "skipped"}
    elif is_temporary_smoke_target(root):
        skipped.append(
            f"skill linking skipped because {SMOKE_TARGET_MARKER_REL.as_posix()} marks this as a temporary install-harness-smoke target"
        )
        actions["link_skills"] = {
            "ok": True,
            "status": "skipped-temporary-target",
            "mode": "temporary-smoke-target",
        }
        checks.append("skill links skipped for temporary smoke target")
    elif is_installed_harness_consumer(root):
        skipped.append(
            "skill linking skipped because .agents/harness.lock.json marks this as an installed consumer project; repo-local .agents/skills are authoritative for this project"
        )
        actions["link_skills"] = {
            "ok": True,
            "status": "skipped-installed-consumer",
            "mode": "installed-consumer",
        }
        checks.append("skill links skipped for installed consumer project")
    else:
        source_root = (
            Path(args.skill_source_path).expanduser().resolve()
            if args.skill_source_path
            else root / ".agents" / "skills"
        )
        link_report = build_link_skills_report(
            source_root=source_root,
            target_paths=target_paths_from_args(args),
            targets=list(args.targets),
            mode=args.mode,
            dry_run=bool(args.dry_run),
            check=bool(args.check),
        )
        link_ok = bool(link_report["ok"])
        link_status = str(link_report["status"])
        if (args.check or args.dry_run) and not link_report["failures"]:
            link_ok = True
            if link_report["skipped"]:
                link_status = "warnings found"
        actions["link_skills"] = {
            "ok": link_ok,
            "status": link_status,
            "mode": link_report["mode"],
        }
        skipped.extend(link_report["skipped"])
        failures.extend(link_report["failures"])
        checks.append("skill links checked" if args.check else "skill links planned" if args.dry_run else "skills linked")

    if args.dry_run:
        actions["validation"] = {"ok": True, "status": "skipped", "mode": "dry-run"}
        skipped.append("validation skipped in --dry-run mode")
    else:
        validation = captured_action(
            "validation",
            lambda: deep_validate_func(root) if args.deep else validate_func(root),
        )
        validation["mode"] = "deep" if args.deep else "normal"
        actions["validation"] = validation
        checks.append("deep validation run" if args.deep else "validation run")
        if not validation["ok"]:
            failures.append("repository validation failed")

    navigation = navigation_status(root)
    ok = not failures and all(
        bool(value.get("ok", True)) for value in actions.values() if isinstance(value, dict)
    )
    if args.dry_run:
        status = "dry-run"
    elif args.check:
        status = "ready" if ok else "check-failed"
    else:
        status = "ready" if ok else "failed"
    return {
        "schema_version": 1,
        "tool": "setup",
        "ok": ok,
        "status": status,
        "root": str(root),
        "checks": checks,
        "navigation": navigation,
        "actions": actions,
        "linked_skills": link_report["tools"] if link_report else {},
        "skipped": skipped,
        "failures": failures,
        "next_prompt": NEXT_PROMPT,
    }


def render_link_report(report: dict[str, Any]) -> str:
    lines = ["# Skill Link Report", ""]
    for tool, summary in report["tools"].items():
        lines.append(f"- {tool} skills path: `{summary['target_path']}`")
        lines.append(
            "  "
            f"planned: {summary['planned']}, linked: {summary['linked']}, "
            f"copied: {summary['copied']}, already present: {summary['already_present']}, "
            f"missing: {summary['missing']}, skipped: {summary['skipped']}"
        )
    if report["skipped"]:
        lines.extend(["", "## Skipped", ""])
        lines.extend(f"- {item}" for item in report["skipped"])
    return "\n".join(lines)


def setup_skipped_markdown_rows(skipped: list[str]) -> list[str]:
    link_collision_count = 0
    rows: list[str] = []
    for item in skipped:
        if item.startswith("Skipping ") and " already exists and points to " in item:
            link_collision_count += 1
            continue
        rows.append(item)
    if link_collision_count:
        rows.append(
            f"{link_collision_count} skill link collision(s) skipped; "
            "see link-skills or non-compact setup JSON output for details."
        )
    return rows


def setup_status_detail(report: dict[str, Any]) -> tuple[str, int]:
    skipped = report.get("skipped") if isinstance(report.get("skipped"), list) else []
    failures = report.get("failures") if isinstance(report.get("failures"), list) else []
    navigation = report.get("navigation") if isinstance(report.get("navigation"), dict) else {}
    advisory_count = len(skipped)
    if navigation.get("status") in {"stale", "missing"}:
        advisory_count += 1
    if failures or report.get("ok") is False:
        return "blocked", advisory_count
    if report.get("status") == "ready" and advisory_count:
        return "ready-with-advisories", advisory_count
    return str(report.get("status") or "unknown"), advisory_count


def render_setup_report(report: dict[str, Any]) -> str:
    status_detail, advisory_count = setup_status_detail(report)
    status_text = str(report["status"])
    if status_detail != status_text:
        status_text = f"{status_text} ({status_detail}; non-blocking advisories: {advisory_count})"
    lines = [
        "# Repository Setup",
        "",
        f"- Status: {status_text}",
        f"- Root: `{report['root']}`",
        f"- Python: {report['actions']['python']['version']}",
        f"- ripgrep: {report['actions']['ripgrep']['status']}",
        f"- Generated artifacts: {'ok' if report['actions']['sync']['ok'] else 'failed'} ({report['actions']['sync']['mode']})",
        f"- Project initialization: {report['actions']['project_initialization']['status']} ({report['actions']['project_initialization']['mode']})",
        f"- Navigation maps: {report.get('navigation', {}).get('status', 'unknown')}",
        f"- Skill links: {'ok' if report['actions']['link_skills']['ok'] else 'issues found'}",
        f"- Validation: {'ok' if report['actions']['validation']['ok'] else 'failed'} ({report['actions']['validation']['mode']})",
    ]
    if "post_project_sync" in report["actions"]:
        post_sync = report["actions"]["post_project_sync"]
        lines.insert(
            8,
            f"- Generated artifacts after project initialization: {'ok' if post_sync['ok'] else 'failed'} ({post_sync['mode']})",
        )
    if "final_project_initialization" in report["actions"]:
        final_init = report["actions"]["final_project_initialization"]
        lines.insert(
            9,
            f"- Project initialization after generated sync: {final_init['status']} ({final_init['mode']})",
        )
    if "local_ai_readiness" in report["actions"]:
        local_ai = report["actions"]["local_ai_readiness"]
        lines.append(f"- Local AI readiness: {'ok' if local_ai['ok'] else 'fallback'} ({local_ai['status']})")
    if "harness_health" in report["actions"]:
        health = report["actions"]["harness_health"]
        lines.append(f"- Harness health: {'ok' if health['ok'] else 'issues found'} ({health['status']})")
    if "release_readiness" in report["actions"]:
        readiness = report["actions"]["release_readiness"]
        lines.append(
            f"- Release readiness: {'ok' if readiness['ok'] else 'issues found'} ({readiness['status']})"
        )
        for check in readiness.get("checks", []):
            name = check.get("name", "check")
            lines.append(f"  - {name}: {'ok' if check.get('ok') else 'issues found'}")
        for warning in readiness.get("warnings", []):
            lines.append(f"  - Warning: {warning}")
    lines.extend(["", "## Skill Links", ""])
    if report["linked_skills"]:
        lines.append("| Tool | Target | Planned | Linked | Copied | Already Present | Missing | Skipped |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
        for tool, summary in report["linked_skills"].items():
            lines.append(
                f"| {tool} | `{summary['target_path']}` | {summary['planned']} | "
                f"{summary['linked']} | {summary['copied']} | {summary['already_present']} | "
                f"{summary['missing']} | {summary['skipped']} |"
            )
    else:
        lines.append("- Skill linking skipped.")
    if report["failures"]:
        lines.extend(["", "## Failures", ""])
        lines.extend(f"- {item}" for item in report["failures"])
    if report["skipped"]:
        lines.extend(["", "## Skipped", ""])
        lines.extend(f"- {item}" for item in setup_skipped_markdown_rows(report["skipped"]))
    lines.extend(
        [
            "",
            "## First Agent Prompt",
            "",
            "```text",
            report["next_prompt"],
            "```",
            "",
            "Restart or open a new agent session if newly linked skills are not visible yet.",
        ]
    )
    return "\n".join(lines)


def setup_summary(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    actions = report.get("actions") if isinstance(report.get("actions"), dict) else {}
    link_report = report.get("linked_skills") if isinstance(report.get("linked_skills"), dict) else {}
    failures = report.get("failures") if isinstance(report.get("failures"), list) else []
    skipped = report.get("skipped") if isinstance(report.get("skipped"), list) else []
    action_rows = []
    for name, action in sorted(actions.items()):
        if not isinstance(action, dict):
            continue
        row = {
            "name": name,
            "ok": bool(action.get("ok", False)),
            "ready": bool(action.get("ready", action.get("ok", False))),
            "status": action.get("status", ""),
            "mode": action.get("mode", ""),
        }
        if not compact:
            row["label"] = action.get("label", "")
        action_rows.append(row)
    linked_totals = {
        "tools": len(link_report),
        "planned": 0,
        "linked": 0,
        "copied": 0,
        "already_present": 0,
        "missing": 0,
        "skipped": 0,
    }
    for summary in link_report.values():
        if not isinstance(summary, dict):
            continue
        for key in ("planned", "linked", "copied", "already_present", "missing", "skipped"):
            linked_totals[key] += int(summary.get(key, 0) or 0)
    output: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "setup"),
        "ok": bool(report.get("ok", False)),
        "status": report.get("status", ""),
    }
    status_detail, advisory_count = setup_status_detail(report)
    output.update({
        "status_detail": status_detail,
        "advisory_count": advisory_count,
        "check_count": len(report.get("checks", []) if isinstance(report.get("checks"), list) else []),
        "action_count": len(action_rows),
        "failed_action_count": sum(1 for row in action_rows if not row["ok"]),
        "actions": [row for row in action_rows if not row["ok"]] if compact else action_rows,
        "navigation": report.get("navigation", {}),
        "linked_skills": linked_totals,
        "skipped_count": len(skipped),
        "failure_count": len(failures),
        "skipped": skipped,
        "failures": failures,
    })
    if compact:
        if not output.get("actions"):
            output.pop("actions", None)
        if not skipped:
            output.pop("skipped", None)
        else:
            output["skipped"] = setup_skipped_markdown_rows(skipped)
        if not failures:
            output.pop("failures", None)
        output["linked_skills"] = {
            key: value
            for key, value in linked_totals.items()
            if value or key in {"tools", "already_present"}
        }
    if not compact:
        output["root"] = report.get("root", "")
        output["next_prompt"] = report.get("next_prompt", "")
    return output


def render_setup_summary(report: dict[str, Any]) -> str:
    lines = [
        "# Repository Setup",
        "",
        f"- Status: {report.get('status')}",
        f"- Actions: {report.get('action_count', 0)} checked, {report.get('failed_action_count', 0)} failed",
        f"- Navigation maps: {report.get('navigation', {}).get('status', 'unknown')}",
        f"- Skill links: {report.get('linked_skills', {}).get('already_present', 0)} already present, "
        f"{report.get('linked_skills', {}).get('missing', 0)} missing",
        f"- Skipped: {report.get('skipped_count', 0)}",
        f"- Failures: {report.get('failure_count', 0)}",
    ]
    failures = report.get("failures") if isinstance(report.get("failures"), list) else []
    if failures:
        lines.extend(["", "## Failures", ""])
        lines.extend(f"- {item}" for item in failures)
    return "\n".join(lines)


def setup_status(report: dict[str, Any]) -> int:
    return 0 if report["ok"] else 1


def print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))
