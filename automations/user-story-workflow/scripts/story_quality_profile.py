#!/usr/bin/env python3
"""Run the user-story local quality profile into the story run validation folder."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / ".agents" / "manage.py").exists():
            return candidate
    raise SystemExit("Could not find repository root with .agents/manage.py.")


def safe_work_id(value: str) -> str:
    text = value.strip()
    if not text or "/" in text or "//" in text or ".." in Path(text).parts:
        raise SystemExit("--story-id must be a single workflow folder name.")
    return text


def resolve_from_repo(repo_root: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve(strict=False)


def append_path_args(command: list[str], repo_root: Path, flag: str, values: list[str] | None) -> None:
    for value in values or []:
        command.extend([flag, str(resolve_from_repo(repo_root, value))])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--story-id", required=True, help="story run folder under runs/")
    parser.add_argument("--project-root", required=True, help="target project/repo root to validate")
    parser.add_argument("--solution", help="solution or project path for static analysis")
    parser.add_argument("--coverage", action="append", help="Cobertura coverage XML input")
    parser.add_argument("--docs-target", action="append", help="Markdown docs path to link-check")
    parser.add_argument("--test-result", action="append", help="TRX/JUnit test result to summarize")
    parser.add_argument("--run-security", action="store_true", help="run changed-file security scan")
    parser.add_argument("--security-target", action="append", help="security scan target")
    parser.add_argument("--security-changed-only", action="store_true")
    parser.add_argument("--security-fail-on", choices=["low", "medium", "high"], default="high")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--evidence-name", default="local-quality")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    script_path = Path(__file__).resolve()
    workflow_dir = script_path.parents[1]
    repo_root = find_repo_root(script_path)
    project_root = resolve_from_repo(repo_root, args.project_root)
    if not project_root.exists():
        raise SystemExit(f"--project-root does not exist: {project_root}")

    validation_dir = workflow_dir / "runs" / safe_work_id(args.story_id) / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    skill_script = repo_root / ".agents" / "skills" / "dotnet-quality-gates" / "scripts" / "validate_local_quality.py"
    command = [
        sys.executable,
        "-B",
        str(skill_script),
        "--target",
        str(project_root),
        "--output-json",
        str(validation_dir / f"{args.evidence_name}.json"),
        "--output-md",
        str(validation_dir / f"{args.evidence_name}.md"),
        "--max-workers",
        str(args.max_workers),
        "--timeout-seconds",
        str(args.timeout_seconds),
    ]
    if args.solution:
        command.extend(["--solution", str(resolve_from_repo(repo_root, args.solution))])
    append_path_args(command, repo_root, "--coverage", args.coverage)
    append_path_args(command, repo_root, "--docs-target", args.docs_target)
    append_path_args(command, repo_root, "--test-result", args.test_result)
    if args.run_security:
        command.append("--run-security")
    append_path_args(command, repo_root, "--security-target", args.security_target)
    if args.security_changed_only:
        command.append("--security-changed-only")
    command.extend(["--security-fail-on", args.security_fail_on])
    return subprocess.run(command, cwd=repo_root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
