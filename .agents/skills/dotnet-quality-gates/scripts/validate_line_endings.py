#!/usr/bin/env python3
"""Validate or normalize line endings for text files."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


TEXT_SUFFIXES = {
    ".cs",
    ".csproj",
    ".sln",
    ".razor",
    ".cshtml",
    ".json",
    ".md",
    ".xml",
    ".yml",
    ".yaml",
    ".ts",
    ".tsx",
    ".js",
    ".css",
    ".html",
}
SKIP_DIRS = {".git", "bin", "obj", "node_modules", ".vs", ".idea", ".vscode"}


def git_output_paths(root: Path, args: list[str]) -> list[Path]:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        error = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(error or f"git {' '.join(args)} failed")
    return [root / item.decode("utf-8", errors="surrogateescape") for item in completed.stdout.split(b"\0") if item]


def git_has_head(root: Path) -> bool:
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=str(root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def git_changed_files(root: Path) -> list[Path]:
    if git_has_head(root):
        tracked = git_output_paths(root, ["diff", "--name-only", "--diff-filter=ACMRT", "-z", "HEAD", "--"])
        untracked = git_output_paths(root, ["ls-files", "--others", "--exclude-standard", "-z"])
        return list(dict.fromkeys([*tracked, *untracked]))
    return git_output_paths(root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"])


def git_visible_files(target: Path) -> list[Path] | None:
    probe = target if target.is_dir() else target.parent
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(probe),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        return None
    git_root = Path(completed.stdout.strip()).resolve()
    try:
        relative = target.resolve().relative_to(git_root)
    except ValueError:
        return None
    pathspec = relative.as_posix() or "."
    return git_output_paths(
        git_root,
        ["ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", pathspec],
    )


def collect_files(target: Path, changed_only: bool = False) -> tuple[list[Path], int]:
    if changed_only:
        root = target if target.is_dir() else target.parent
        visible = [path for path in git_changed_files(root) if path.exists()]
        files = [path for path in visible if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES]
        return files, len(visible) - len(files)
    if target.is_file():
        return [target], 0
    visible = git_visible_files(target)
    if visible is not None:
        files = [
            path
            for path in visible
            if path.is_file()
            and not any(part in SKIP_DIRS for part in path.parts)
            and path.suffix.lower() in TEXT_SUFFIXES
        ]
        skipped = sum(1 for path in visible if path.is_file() and path not in files)
        return files, skipped
    files: list[Path] = []
    skipped = 0
    for path in target.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES:
            files.append(path)
        else:
            skipped += 1
    return files, skipped


def iter_files(target: Path, changed_only: bool = False) -> list[Path]:
    files, _skipped = collect_files(target, changed_only=changed_only)
    return files


def line_ending_style(data: bytes) -> str:
    crlf = data.count(b"\r\n")
    normalized = data.replace(b"\r\n", b"")
    lone_lf = normalized.count(b"\n")
    lone_cr = normalized.count(b"\r")
    if lone_cr:
        return "mixed"
    if crlf and lone_lf:
        return "mixed"
    if crlf:
        return "crlf"
    if lone_lf:
        return "lf"
    return "none"


def has_final_newline(data: bytes) -> bool:
    return not data or data.endswith((b"\n", b"\r"))


def normalize(data: bytes, expected: str) -> bytes:
    text = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if expected == "crlf":
        return text.replace(b"\n", b"\r\n")
    return text


def validate(args: argparse.Namespace) -> dict[str, object]:
    target = Path(args.target).resolve()
    if not target.exists():
        raise FileNotFoundError(target)
    results: list[dict[str, object]] = []
    failed = False
    fixed = 0
    files, skipped_files = collect_files(target, changed_only=bool(getattr(args, "changed_only", False)))
    for path in files:
        data = path.read_bytes()
        if b"\0" in data:
            skipped_files += 1
            continue
        output_data = data
        style = line_ending_style(data)
        ok = True
        if args.expected == "consistent":
            ok = style != "mixed"
        elif style not in {args.expected, "none"}:
            ok = False
        if not ok:
            failed = True
            if args.fix and args.expected in {"lf", "crlf"}:
                normalized = normalize(output_data, args.expected)
                if not has_final_newline(normalized):
                    normalized += b"\r\n" if args.expected == "crlf" else b"\n"
                output_data = normalized
                fixed += 1
                ok = True
        final_newline_ok = has_final_newline(output_data)
        if not final_newline_ok:
            failed = True
            if args.fix and args.expected in {"lf", "crlf"} and ok:
                output_data = output_data + (b"\r\n" if args.expected == "crlf" else b"\n")
                fixed += 1
                final_newline_ok = True
        if args.fix and output_data != data:
            path.write_bytes(output_data)
        results.append({"path": str(path), "style": style, "ok": ok and final_newline_ok, "final_newline": final_newline_ok})
    return {
        "ok": not failed or (args.fix and args.expected in {"lf", "crlf"}),
        "target": str(target),
        "expected": args.expected,
        "changed_only": bool(getattr(args, "changed_only", False)),
        "files_checked": len(results),
        "files_skipped": skipped_files,
        "files_fixed": fixed,
        "failures": [item for item in results if not item["ok"]],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target")
    parser.add_argument("--expected", choices=["consistent", "lf", "crlf"], default="consistent")
    parser.add_argument("--changed-only", action="store_true")
    parser.add_argument("--fix", action="store_true")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = validate(args)
    except Exception as exc:
        if args.format == "json":
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"checked {result['files_checked']} files; skipped {result['files_skipped']}; fixed {result['files_fixed']}")
        for failure in result["failures"]:
            print(f"- {failure['path']}: {failure['style']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
