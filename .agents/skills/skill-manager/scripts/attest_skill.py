#!/usr/bin/env python3
"""Emit a local provenance and hash attestation for a skill folder."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import skill_manager_common as common
import validate_skill


def default_root() -> Path:
    return Path(__file__).resolve().parents[4]


def git_value(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def build_attestation(
    skill_dir: Path,
    root: Path,
    *,
    validation: tuple[list[str], list[str]] | None = None,
) -> dict[str, Any]:
    manifest, manifest_error = common.load_skill_manifest(skill_dir)
    metadata, metadata_error = common.parse_frontmatter_file(skill_dir / "SKILL.md")
    if validation is None:
        errors, warnings = validate_skill.validate_skill(skill_dir)
    else:
        errors, warnings = validation
    status = git_value(root, "status", "--short", "--", str(skill_dir))
    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "skill": {
            "name": (metadata or {}).get("name", skill_dir.name),
            "path": common.relative(root, skill_dir) if common.is_inside(skill_dir, root) else str(skill_dir),
            "version": str((manifest or {}).get("version", "")),
        },
        "manifest": manifest or {},
        "manifest_error": manifest_error,
        "metadata_error": metadata_error,
        "git": {
            "commit": git_value(root, "rev-parse", "HEAD"),
            "branch": git_value(root, "branch", "--show-current"),
            "dirty_for_skill": bool(status),
        },
        "validation": {"ok": not errors, "errors": errors, "warnings": warnings},
        "hash_algorithm": "sha256",
        "file_hashes": common.collect_file_hashes(skill_dir, max_files=5000),
        "provenance_note": (
            "Local unsigned attestation. It records source files, hashes, validation "
            "status, and git identity when available; it does not fetch or upload data."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    file_hashes = report.get("file_hashes") if isinstance(report.get("file_hashes"), dict) else {}
    file_count = len(file_hashes) if file_hashes else int(report.get("file_count", 0) or 0)
    lines = [
        "# Skill Attestation",
        "",
        f"- Skill: `{report['skill']['name']}`",
        f"- Version: `{report['skill']['version'] or 'unversioned'}`",
        f"- Path: `{report['skill']['path']}`",
        f"- Generated at: `{report['generated_at']}`",
        f"- Git commit: `{report['git']['commit'] or 'unavailable'}`",
        f"- Dirty for skill: {report['git']['dirty_for_skill']}",
        f"- Validation ok: {report['validation']['ok']}",
        f"- Files hashed: {file_count}",
        "",
    ]
    if not file_hashes:
        return "\n".join(lines)
    lines.extend(["## File Hashes", ""])
    for path, digest in sorted(file_hashes.items())[:80]:
        lines.append(f"- `{path}`: `{digest}`")
    if len(file_hashes) > 80:
        lines.append(f"- ... {len(file_hashes) - 80} more files omitted.")
    return "\n".join(lines)


def summarize_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    validation = report.get("validation") if isinstance(report.get("validation"), dict) else {}
    git = report.get("git") if isinstance(report.get("git"), dict) else {}
    file_hashes = report.get("file_hashes") if isinstance(report.get("file_hashes"), dict) else {}
    errors = validation.get("errors") if isinstance(validation.get("errors"), list) else []
    warnings = validation.get("warnings") if isinstance(validation.get("warnings"), list) else []
    summary: dict[str, Any] = {
        "version": report.get("version", 1),
        "format": "skill-attestation-summary",
        "ok": bool(validation.get("ok", False)),
        "generated_at": report.get("generated_at", ""),
        "skill": report.get("skill", {}),
        "git": {
            "commit": git.get("commit", ""),
            "branch": git.get("branch", ""),
            "dirty_for_skill": bool(git.get("dirty_for_skill", False)),
        },
        "validation": {
            "ok": bool(validation.get("ok", False)),
            "error_count": len(errors),
            "warning_count": len(warnings),
            "errors": errors,
            "warnings": warnings,
        },
        "manifest_error": report.get("manifest_error"),
        "metadata_error": report.get("metadata_error"),
        "hash_algorithm": report.get("hash_algorithm", ""),
        "file_count": len(file_hashes),
        "provenance_note": report.get("provenance_note", ""),
    }
    if not compact:
        summary["manifest"] = report.get("manifest", {})
        summary["file_hashes"] = file_hashes
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="repository root; defaults to script parent")
    parser.add_argument("--skill", required=True, help="skill folder")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown", dest="output_format")
    parser.add_argument("--summary", action="store_true", help="emit counts and validation facts")
    parser.add_argument("--compact", action="store_true", help="with --summary, omit manifest and file hashes")
    return parser


def main() -> int:
    common.require_supported_python()
    args = build_parser().parse_args()
    root = Path(args.root).expanduser().resolve() if args.root else default_root()
    skill_dir = Path(args.skill).expanduser().resolve()
    report = build_attestation(skill_dir, root)
    if args.summary or args.compact:
        report = summarize_report(report, compact=args.compact)
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0 if report["validation"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
