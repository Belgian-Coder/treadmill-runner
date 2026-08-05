"""Safe Python syntax checks that never write bytecode caches."""

from __future__ import annotations

import ast
import json
import tokenize
from pathlib import Path

from repo_support import repo_common as repo

SKIP_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "temp",
    "venv",
}


def repo_path(root: Path, value: str) -> tuple[Path | None, str]:
    if "\\" in value and not Path(value).is_absolute():
        value = value.replace("\\", "/")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    root_resolved = root.resolve()
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return None, f"path is outside repository: {value}"
    return resolved, ""


def is_skipped_python_path(root: Path, path: Path) -> bool:
    try:
        rel_parts = path.relative_to(root.resolve()).parts
    except ValueError:
        rel_parts = path.parts
    return any(part in SKIP_DIR_NAMES for part in rel_parts)


def iter_python_files(root: Path, raw_paths: list[str]) -> tuple[list[Path], list[dict[str, object]]]:
    files: list[Path] = []
    issues: list[dict[str, object]] = []
    for raw_path in raw_paths:
        resolved, error = repo_path(root, raw_path)
        if error or resolved is None:
            issues.append({"path": raw_path, "message": error, "type": "path"})
            continue
        if not resolved.exists():
            issues.append({"path": repo.relative(root, resolved), "message": "path does not exist", "type": "path"})
            continue
        if resolved.is_file():
            if resolved.suffix == ".py" and not is_skipped_python_path(root, resolved):
                files.append(resolved)
            continue
        for path in sorted(resolved.rglob("*.py")):
            if not is_skipped_python_path(root, path):
                files.append(path)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in files:
        key = path.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique, issues


def parse_python_file(root: Path, path: Path) -> dict[str, object] | None:
    rel = repo.relative(root, path)
    try:
        with tokenize.open(path) as handle:
            source = handle.read()
        ast.parse(source, filename=rel)
    except SyntaxError as exc:
        return {
            "path": rel,
            "line": exc.lineno or 0,
            "offset": exc.offset or 0,
            "message": exc.msg,
            "text": (exc.text or "").strip(),
            "type": "syntax",
        }
    except (OSError, UnicodeDecodeError, tokenize.TokenError) as exc:
        return {"path": rel, "line": 0, "offset": 0, "message": str(exc), "type": "read"}
    return None


def syntax_check_report(root: Path, paths: list[str]) -> dict[str, object]:
    files, path_issues = iter_python_files(root, paths)
    issues = list(path_issues)
    for path in files:
        issue = parse_python_file(root, path)
        if issue:
            issues.append(issue)
    checked_paths = [repo.relative(root, path) for path in files]
    return {
        "schema_version": 1,
        "tool": "skill-manager.syntax-check",
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "checked": len(files),
        "failed": len(issues),
        "bytecode_written": False,
        "paths": checked_paths,
        "issues": issues,
    }


def render_syntax_check_markdown(report: dict[str, object], *, compact: bool = False) -> str:
    lines = [
        "# Python Syntax Check",
        "",
        f"- Status: {report.get('status', 'unknown')}",
        f"- Checked: {report.get('checked', 0)}",
        f"- Failed: {report.get('failed', 0)}",
        "- Bytecode written: false",
    ]
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues"])
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            location = str(issue.get("path", ""))
            if issue.get("line"):
                location += f":{issue.get('line')}"
            lines.append(f"- {location}: {issue.get('message', '')}")
    elif not compact:
        lines.extend(["", "No Python syntax issues found."])
    return "\n".join(lines) + "\n"


def syntax_check_command(args, root: Path) -> int:
    report = syntax_check_report(root, list(getattr(args, "paths", []) or []))
    if getattr(args, "output_format", "markdown") == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_syntax_check_markdown(report, compact=bool(getattr(args, "compact", False))), end="")
    return 0 if report["ok"] else 1
