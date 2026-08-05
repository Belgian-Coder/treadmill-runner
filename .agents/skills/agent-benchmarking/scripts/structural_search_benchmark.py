#!/usr/bin/env python3
"""Benchmark structural ast-grep filtering against broad rg candidate reads."""

from __future__ import annotations

import argparse
import ast
import json
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import benchmark_common

DEFAULT_AST_GREP_PACKAGE = "@ast-grep/cli@0.43.0"
DEFAULT_PATHS = [".agents", "automations"]


@dataclass(frozen=True)
class Query:
    query_id: str
    description: str
    rg_pattern: str
    ast_patterns: tuple[str, ...]
    broad_call: str
    target_kind: str


@dataclass(frozen=True)
class Match:
    path: str
    line: int
    end_line: int
    text: str


QUERIES = [
    Query(
        query_id="subprocess-run-check-false",
        description="subprocess.run calls that explicitly pass check=False",
        rg_pattern=r"subprocess\.run\(",
        ast_patterns=("subprocess.run($$$ARGS, check=False)", "subprocess.run($$$ARGS, check=False, $$$REST)"),
        broad_call="subprocess.run",
        target_kind="has_check_false",
    ),
    Query(
        query_id="json-dumps-sort-keys",
        description="json.dumps calls that explicitly pass sort_keys=True",
        rg_pattern=r"json\.dumps\(",
        ast_patterns=("json.dumps($$$ARGS, sort_keys=True)", "json.dumps($$$ARGS, sort_keys=True, $$$REST)"),
        broad_call="json.dumps",
        target_kind="has_sort_keys_true",
    ),
    Query(
        query_id="json-loads-read-text",
        description="json.loads calls that load directly from a read_text call",
        rg_pattern=r"json\.loads\(",
        ast_patterns=("json.loads($TARGET.read_text($$$ARGS))",),
        broad_call="json.loads",
        target_kind="loads_read_text",
    ),
    Query(
        query_id="path-constructor-read-text",
        description="read_text calls on an inline Path(...) constructor",
        rg_pattern=r"read_text\(",
        ast_patterns=("Path($$$ARGS).read_text($$$KW)",),
        broad_call="read_text",
        target_kind="path_constructor_read_text",
    ),
]


def relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def iter_python_files(root: Path, paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in paths:
        base = root / item
        if base.is_file() and base.suffix == ".py":
            files.append(base)
            continue
        if not base.exists():
            continue
        files.extend(
            path
            for path in base.rglob("*.py")
            if ".git" not in path.parts and "__pycache__" not in path.parts
        )
    return sorted(set(files))


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def keyword_value(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def is_true(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def is_false(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def target_matches(call: ast.Call, target_kind: str) -> bool:
    if target_kind == "has_check_false":
        return is_false(keyword_value(call, "check"))
    if target_kind == "has_sort_keys_true":
        return is_true(keyword_value(call, "sort_keys"))
    if target_kind == "loads_read_text":
        if not call.args or not isinstance(call.args[0], ast.Call):
            return False
        name = dotted_name(call.args[0].func)
        return name == "read_text" or name.endswith(".read_text")
    if target_kind == "path_constructor_read_text":
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "read_text":
            return False
        receiver = call.func.value
        return isinstance(receiver, ast.Call) and dotted_name(receiver.func) == "Path"
    raise ValueError(f"unknown target kind: {target_kind}")


def source_segment(lines: list[str], start: int, end: int) -> str:
    start = max(start, 1)
    end = max(end, start)
    return "\n".join(lines[start - 1 : end])


def compact_line(text: str) -> str:
    return " ".join(text.strip().split())


def collect_call_matches(root: Path, paths: list[str], query: Query) -> tuple[list[Match], list[Match], list[str]]:
    broad: list[Match] = []
    target: list[Match] = []
    parse_errors: list[str] = []
    for path in iter_python_files(root, paths):
        source = read_source(path)
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            parse_errors.append(f"{relative_path(root, path)}:{exc.lineno}: {exc.msg}")
            continue
        lines = source.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = dotted_name(node.func)
            broad_match = call_name == query.broad_call
            if "." not in query.broad_call:
                broad_match = broad_match or call_name.endswith(f".{query.broad_call}")
            if not broad_match:
                continue
            line = int(getattr(node, "lineno", 1))
            end_line = int(getattr(node, "end_lineno", line))
            match = Match(
                path=relative_path(root, path),
                line=line,
                end_line=end_line,
                text=source_segment(lines, line, end_line),
            )
            broad.append(match)
            if target_matches(node, query.target_kind):
                target.append(match)
    return broad, target, parse_errors


def run_command(command: list[str], cwd: Path) -> tuple[int, str, str, float]:
    start = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    return completed.returncode, completed.stdout or "", completed.stderr or "", elapsed_ms


def rg_command(rg: str, query: Query, paths: list[str]) -> list[str]:
    return [rg, "--no-heading", "--line-number", "--glob", "*.py", query.rg_pattern, *paths]


def ast_grep_command(ast_grep: list[str], pattern: str, paths: list[str]) -> list[str]:
    return [*ast_grep, "run", "--lang", "py", "-p", pattern, *paths, "--json=compact"]


def parse_ast_grep_matches(root: Path, output: str) -> list[Match]:
    if not output.strip():
        return []
    payload = json.loads(output)
    if not isinstance(payload, list):
        raise ValueError("ast-grep JSON output must be a list")
    matches: list[Match] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        item_range = item.get("range") if isinstance(item.get("range"), dict) else {}
        start = item_range.get("start", {}) if isinstance(item_range, dict) else {}
        end = item_range.get("end", {}) if isinstance(item_range, dict) else {}
        line = int(start.get("line", 0)) + 1 if isinstance(start, dict) else 0
        end_line = int(end.get("line", line - 1)) + 1 if isinstance(end, dict) else line
        file_value = str(item.get("file", "")).replace("\\", "/")
        path = relative_path(root, root / file_value) if file_value else ""
        matches.append(
            Match(path=path, line=line, end_line=end_line, text=str(item.get("lines") or item.get("text") or ""))
        )
    return matches


def match_key(match: Match) -> tuple[str, int, int]:
    return (match.path.replace("\\", "/"), match.line, match.end_line)


def render_matches(matches: list[Match]) -> str:
    lines = []
    for match in matches:
        location = f"{match.path}:{match.line}"
        lines.append(f"{location}: {compact_line(match.text)}")
    return "\n".join(lines) + ("\n" if lines else "")


def command_string(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def token_count(text: str) -> dict[str, Any]:
    return {"bytes": len(text.encode("utf-8")), "tokens": benchmark_common.estimate_tokens(text)}


def benchmark_query(root: Path, paths: list[str], query: Query, rg: str, ast_grep: list[str]) -> dict[str, Any]:
    broad, expected_target, parse_errors = collect_call_matches(root, paths, query)
    rg_cmd = rg_command(rg, query, paths)
    rg_code, rg_stdout, rg_stderr, rg_elapsed = run_command(rg_cmd, root)

    ast_matches_by_key: dict[tuple[str, int, int], Match] = {}
    ast_commands: list[dict[str, Any]] = []
    ast_raw_output = ""
    ast_returncodes: list[int] = []
    ast_elapsed = 0.0
    ast_stderr: list[str] = []
    for pattern in query.ast_patterns:
        ag_cmd = ast_grep_command(ast_grep, pattern, paths)
        ag_code, ag_stdout, ag_stderr, ag_elapsed_one = run_command(ag_cmd, root)
        ast_returncodes.append(ag_code)
        ast_elapsed += ag_elapsed_one
        ast_raw_output += ag_stdout
        if ag_stderr.strip():
            ast_stderr.append(ag_stderr.strip())
        ast_commands.append({"pattern": pattern, "command": command_string(ag_cmd), "returncode": ag_code})
        for match in parse_ast_grep_matches(root, ag_stdout) if ag_stdout.strip() else []:
            ast_matches_by_key.setdefault(match_key(match), match)

    ast_matches = [ast_matches_by_key[key] for key in sorted(ast_matches_by_key)]
    rg_review_text = render_matches(broad)
    ast_compact_text = render_matches(ast_matches)
    rg_review_tokens = benchmark_common.estimate_tokens(rg_review_text)
    ast_review_tokens = benchmark_common.estimate_tokens(ast_compact_text)
    expected_keys = {match_key(match) for match in expected_target}
    ast_keys = {match_key(match) for match in ast_matches}
    saved = rg_review_tokens - ast_review_tokens

    return {
        "id": query.query_id,
        "description": query.description,
        "rg": {
            "command": command_string(rg_cmd),
            "returncode": rg_code,
            "elapsed_ms": round(rg_elapsed, 2),
            "raw_output": token_count(rg_stdout),
            "stderr": rg_stderr.strip(),
        },
        "ast_grep": {
            "commands": ast_commands,
            "returncodes": ast_returncodes,
            "elapsed_ms": round(ast_elapsed, 2),
            "raw_json_output": token_count(ast_raw_output),
            "compact_output": token_count(ast_compact_text),
            "stderr": "\n".join(ast_stderr),
        },
        "review_context": {
            "rg_broad_candidate_count": len(broad),
            "expected_target_count": len(expected_target),
            "ast_grep_match_count": len(ast_matches),
            "rg_candidate_review": token_count(rg_review_text),
            "ast_grep_compact_review": token_count(ast_compact_text),
            "saved_tokens_estimated": saved,
            "saved_percent_estimated": round((saved / max(1, rg_review_tokens)) * 100, 2),
        },
        "accuracy": {
            "matches_expected_targets": expected_keys == ast_keys,
            "missing": sorted(f"{path}:{line}-{end}" for path, line, end in expected_keys - ast_keys),
            "extra": sorted(f"{path}:{line}-{end}" for path, line, end in ast_keys - expected_keys),
        },
        "parse_errors": parse_errors,
    }


def resolve_ast_grep_command(args: argparse.Namespace) -> list[str]:
    if args.ast_grep_command:
        return shlex.split(args.ast_grep_command)
    if shutil.which("ast-grep"):
        return ["ast-grep"]
    if shutil.which("sg"):
        return ["sg"]
    if args.allow_npx:
        npx = shutil.which("npx") or shutil.which("npx.cmd")
        if not npx:
            raise SystemExit("npx was not found; install ast-grep locally or pass --ast-grep-command.")
        return [npx, "--yes", "-p", DEFAULT_AST_GREP_PACKAGE, "ast-grep"]
    raise SystemExit("ast-grep was not found. Install it locally or rerun with --allow-npx for pinned npx.")


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).resolve()
    rg = args.rg_command or shutil.which("rg")
    if not rg:
        raise SystemExit("rg was not found; install ripgrep or pass --rg-command.")
    paths = args.paths or DEFAULT_PATHS
    ast_grep = resolve_ast_grep_command(args)
    selected = {item.strip() for item in args.query} if args.query else {query.query_id for query in QUERIES}
    queries = [query for query in QUERIES if query.query_id in selected]
    if not queries:
        raise SystemExit(f"no known benchmark query selected: {sorted(selected)}")

    results = [benchmark_query(root, paths, query, rg, ast_grep) for query in queries]
    total_rg_review = sum(item["review_context"]["rg_candidate_review"]["tokens"] for item in results)
    total_ast_review = sum(item["review_context"]["ast_grep_compact_review"]["tokens"] for item in results)
    total_saved = total_rg_review - total_ast_review
    raw_ast_json = sum(item["ast_grep"]["raw_json_output"]["tokens"] for item in results)
    raw_rg = sum(item["rg"]["raw_output"]["tokens"] for item in results)
    all_accurate = all(item["accuracy"]["matches_expected_targets"] for item in results)
    return {
        "tool": "agent-benchmarking.structural-search-benchmark",
        "schema_version": 1,
        "ok": all_accurate and total_saved > 0,
        "root": str(root),
        "paths": paths,
        "token_counter": benchmark_common.token_count_metadata(),
        "ast_grep_package": DEFAULT_AST_GREP_PACKAGE,
        "measurement_scope": {
            "billing_claim": False,
            "live_llm_run": False,
            "review_context_tokens": True,
            "raw_tool_output_tokens": True,
            "timing_note": "Elapsed milliseconds include command startup; npx includes package resolution/cache overhead.",
        },
        "summary": {
            "queries": len(results),
            "matches_expected_targets": all_accurate,
            "rg_candidate_review_tokens": total_rg_review,
            "ast_grep_compact_review_tokens": total_ast_review,
            "saved_tokens_estimated": total_saved,
            "saved_percent_estimated": round((total_saved / max(1, total_rg_review)) * 100, 2),
            "rg_raw_output_tokens": raw_rg,
            "ast_grep_raw_json_tokens": raw_ast_json,
        },
        "results": results,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Structural Search Benchmark",
        "",
        f"- Status: {'passed' if report.get('ok') else 'needs review'}",
        f"- Token counter: {report['token_counter']['method']} (exact: {report['token_counter']['exact']})",
        f"- Paths: {', '.join(report['paths'])}",
        f"- Review-context tokens, rg baseline: {summary['rg_candidate_review_tokens']}",
        f"- Review-context tokens, ast-grep compact: {summary['ast_grep_compact_review_tokens']}",
        f"- Estimated review-context tokens saved: {summary['saved_tokens_estimated']} ({summary['saved_percent_estimated']}%)",
        f"- Raw rg output tokens: {summary['rg_raw_output_tokens']}",
        f"- Raw ast-grep JSON tokens: {summary['ast_grep_raw_json_tokens']}",
        "",
        "Raw ast-grep JSON is measured separately because it is an interchange format, not the compact context an agent should read.",
        "",
        "## Queries",
        "",
    ]
    for item in report["results"]:
        review = item["review_context"]
        accuracy = item["accuracy"]
        lines.extend(
            [
                f"### {item['id']}",
                "",
                f"- Description: {item['description']}",
                f"- rg candidates to inspect: {review['rg_broad_candidate_count']}",
                f"- Expected targets: {review['expected_target_count']}",
                f"- ast-grep matches: {review['ast_grep_match_count']}",
                f"- Accurate: {accuracy['matches_expected_targets']}",
                f"- Tokens saved: {review['saved_tokens_estimated']} ({review['saved_percent_estimated']}%)",
                f"- rg elapsed ms: {item['rg']['elapsed_ms']}",
                f"- ast-grep elapsed ms: {item['ast_grep']['elapsed_ms']}",
                "",
            ]
        )
        if accuracy["missing"] or accuracy["extra"]:
            lines.append(f"- Missing: {', '.join(accuracy['missing']) or 'none'}")
            lines.append(f"- Extra: {', '.join(accuracy['extra']) or 'none'}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument("--paths", nargs="*", default=None, help="Repo-relative paths to scan.")
    parser.add_argument("--query", action="append", default=[], help="Query id to run; may be repeated.")
    parser.add_argument("--ast-grep-command", default="", help="Command prefix for ast-grep, e.g. 'ast-grep'.")
    parser.add_argument("--allow-npx", action="store_true", help="Use pinned npx @ast-grep/cli if ast-grep is absent.")
    parser.add_argument("--rg-command", default="", help="Path or command name for ripgrep.")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--output", default="", help="Optional output file.")
    args = parser.parse_args(argv)

    report = build_report(args)
    text = render_markdown(report) if args.format == "markdown" else json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        benchmark_common.write_text(Path(args.output), text)
    else:
        print(text, end="")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
