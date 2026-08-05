#!/usr/bin/env python3
"""Plan or apply a local skill upgrade."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import compare_skill_versions
import skill_manager_common as common


def default_root() -> Path:
    return Path(__file__).resolve().parents[4]


def validate_target(root: Path, target: Path, allow_outside_active_skills: bool) -> Path:
    resolved = target.resolve(strict=False)
    active_root = (root / ".agents" / "skills").resolve(strict=False)
    if common.is_inside(resolved, active_root):
        return resolved
    if allow_outside_active_skills and common.is_inside(resolved, root):
        return resolved
    if not common.is_inside(resolved, root):
        raise SystemExit(f"refusing target outside repository: {resolved}")
    raise SystemExit(
        "refusing target outside .agents/skills; pass --allow-outside-active-skills "
        "only for an intentional repository-local target"
    )


def remove_existing(path: Path, root: Path) -> None:
    resolved = path.resolve(strict=False)
    if not common.is_inside(resolved, root):
        raise RuntimeError(f"refusing to remove outside repository: {resolved}")
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def copy_skill(source: Path, target: Path) -> None:
    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name == "__pycache__" or name.endswith(".pyc")}

    shutil.copytree(source, target, ignore=ignore)


def render_upgrade_plan(report: dict[str, object], strategy: str, target: Path) -> str:
    files = report["files"]
    decision = report["recommended_decision"]
    lines = [
        "# Skill Upgrade Plan",
        "",
        f"- Strategy: `{strategy}`",
        f"- Target: `{target}`",
        f"- Compare recommendation: `{decision['decision']}` - {decision['reason']}",
        f"- Added files: {len(files['added'])}",
        f"- Changed files: {len(files['changed'])}",
        f"- Removed files: {len(files['removed'])}",
    ]
    if strategy == "merge":
        lines.append("- Merge strategy is planning-only; apply the listed changes manually.")
    else:
        lines.append("- Override strategy replaces the target folder with the new folder when --apply is used.")
    return "\n".join(lines)


def upgrade_skill(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve() if args.root else default_root()
    old_path = Path(args.old).expanduser().resolve()
    new_path = Path(args.new).expanduser().resolve()
    target = validate_target(
        root,
        Path(args.target).expanduser(),
        allow_outside_active_skills=args.allow_outside_active_skills,
    )

    if not old_path.exists():
        raise SystemExit(f"old skill folder not found: {old_path}")
    if not new_path.exists():
        raise SystemExit(f"new skill folder not found: {new_path}")

    report = compare_skill_versions.compare_paths(old_path, new_path)
    print(render_upgrade_plan(report, args.strategy, target))

    apply = bool(args.apply)
    if args.strategy == "merge":
        if apply:
            print("Merge strategy is intentionally planning-only; no files were written.")
        return 0

    if not apply:
        print("Dry run complete; no files were written.")
        return 0

    decision = report["recommended_decision"]
    if isinstance(decision, dict) and decision.get("decision") == "keep-staged":
        raise SystemExit("refusing to apply upgrade because comparison recommends keep-staged")

    if target.exists() or target.is_symlink():
        remove_existing(target, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    copy_skill(new_path, target)
    print(f"Applied override upgrade to {target}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="repository root; defaults to script parent")
    parser.add_argument("--old", required=True, help="old skill folder")
    parser.add_argument("--new", required=True, help="new skill folder")
    parser.add_argument("--target", required=True, help="target skill folder")
    parser.add_argument("--strategy", choices=("override", "merge"), required=True)
    parser.add_argument("--dry-run", action="store_true", help="plan only; default behavior")
    parser.add_argument("--apply", action="store_true", help="apply override strategy")
    parser.add_argument(
        "--allow-outside-active-skills",
        action="store_true",
        help="allow a repository-local target outside .agents/skills",
    )
    return parser


def main() -> int:
    common.require_supported_python()
    parser = build_parser()
    args = parser.parse_args()
    if args.dry_run and args.apply:
        parser.error("--dry-run and --apply are mutually exclusive")
    return upgrade_skill(args)


if __name__ == "__main__":
    raise SystemExit(main())
