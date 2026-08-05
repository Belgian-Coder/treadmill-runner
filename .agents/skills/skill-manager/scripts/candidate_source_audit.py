#!/usr/bin/env python3
"""Audit external skill/agent source trees before import decisions."""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

IGNORED_DIRS = {".git", ".hg", ".svn", "__pycache__", "node_modules", "bin", "obj"}
TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
SKILL_REF_RE = re.compile(r"\[skill:([a-zA-Z0-9_-]+)\]")
SCOPE_RE = re.compile(r"^##\s+Scope\s*$", re.IGNORECASE | re.MULTILINE)
OUT_OF_SCOPE_RE = re.compile(r"^##\s+Out(?:\s+|-)?Of\s+Scope\s*$", re.IGNORECASE | re.MULTILINE)
STOPWORDS = {
    "a",
    "an",
    "and",
    "apps",
    "building",
    "creating",
    "for",
    "from",
    "in",
    "into",
    "net",
    "of",
    "on",
    "or",
    "the",
    "to",
    "use",
    "using",
    "when",
    "with",
}


def relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def iter_markdown(root: Path, pattern: str) -> list[Path]:
    rows: list[Path] = []
    for path in sorted(root.rglob(pattern), key=lambda item: item.as_posix().lower()):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        rows.append(path)
    return rows


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def parse_frontmatter(path: Path) -> dict[str, str | None]:
    result: dict[str, str | None] = {"name": None, "description": None}
    try:
        lines = read_text(path).split("\n")
    except OSError:
        return result
    if not lines or lines[0].strip() != "---":
        return result
    frontmatter: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        frontmatter.append(line)
    else:
        return result

    index = 0
    while index < len(frontmatter):
        line = frontmatter[index]
        if line != line.lstrip():
            index += 1
            continue
        match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_-]*)\s*:\s*(.*)", line)
        if not match:
            index += 1
            continue
        key = match.group(1)
        raw = match.group(2).strip()
        if key not in result:
            index += 1
            continue
        if raw in {"|", ">", "|+", "|-", ">+", ">-"}:
            block: list[str] = []
            index += 1
            while index < len(frontmatter):
                block_line = frontmatter[index]
                if block_line.strip() == "" or block_line[:1] in {" ", "\t"}:
                    block.append(block_line)
                    index += 1
                    continue
                break
            if block:
                indent = min((len(item) - len(item.lstrip())) for item in block if item.strip()) if any(item.strip() for item in block) else 0
                value = "\n".join(item[indent:] if len(item) >= indent else "" for item in block)
                if raw.startswith(">"):
                    value = re.sub(r"(?<!\n)\n(?!\n)", " ", value)
                result[key] = value.strip() or None
            continue
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
            raw = raw[1:-1]
        result[key] = raw or None
        index += 1
    return result


def body_after_frontmatter(text: str) -> str:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text
    for index, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1 :])
    return text


def extract_refs(text: str) -> list[str]:
    return list(dict.fromkeys(SKILL_REF_RE.findall(text)))


def token_set(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text) if token.lower() not in STOPWORDS}


def similarity_score(first: str, second: str) -> float:
    first_tokens = token_set(first)
    second_tokens = token_set(second)
    union = first_tokens | second_tokens
    jaccard = len(first_tokens & second_tokens) / len(union) if union else 0.0
    sequence = difflib.SequenceMatcher(a=first, b=second).ratio() if first or second else 0.0
    return round((jaccard + sequence) / 2, 3)


def collect_items(root: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for skill_md in iter_markdown(root, "SKILL.md"):
        text = read_text(skill_md)
        frontmatter = parse_frontmatter(skill_md)
        body = body_after_frontmatter(text)
        item_id = str(frontmatter.get("name") or skill_md.parent.name)
        refs = extract_refs(body)
        has_scope = bool(SCOPE_RE.search(body))
        has_out_of_scope = bool(OUT_OF_SCOPE_RE.search(body))
        items.append(
            {
                "id": item_id,
                "type": "skill",
                "path": relative(root, skill_md),
                "description": str(frontmatter.get("description") or ""),
                "description_length": len(str(frontmatter.get("description") or "")),
                "refs": refs,
                "has_scope": has_scope,
                "has_out_of_scope": has_out_of_scope,
                "missing_boundary": bool(refs) and not (has_scope and has_out_of_scope),
            }
        )
    for agent_md in iter_markdown(root, "agents/*.md"):
        text = read_text(agent_md)
        frontmatter = parse_frontmatter(agent_md)
        description = str(frontmatter.get("description") or "")
        items.append(
            {
                "id": str(frontmatter.get("name") or agent_md.stem),
                "type": "agent",
                "path": relative(root, agent_md),
                "description": description,
                "description_length": len(description),
                "refs": extract_refs(text),
                "has_scope": False,
                "has_out_of_scope": False,
                "missing_boundary": False,
            }
        )
    return sorted(items, key=lambda item: (str(item["type"]), str(item["id"]), str(item["path"])))


def detect_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    visiting: list[str] = []

    def visit(node: str) -> None:
        visiting.append(node)
        for neighbor in graph.get(node, []):
            if neighbor not in graph:
                continue
            if neighbor in visiting:
                index = visiting.index(neighbor)
                cycle = visiting[index:]
                smallest = min(range(len(cycle)), key=lambda pos: cycle[pos])
                normalized = tuple(cycle[smallest:] + cycle[:smallest])
                if normalized not in seen:
                    seen.add(normalized)
                    cycles.append(list(normalized))
            else:
                visit(neighbor)
        visiting.pop()

    for node in sorted(graph):
        visit(node)
    return cycles


def similarity_pairs(
    items: list[dict[str, Any]],
    *,
    warn_threshold: float,
    error_threshold: float,
    max_pairs: int,
) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    described = [item for item in items if item.get("description")]
    for left_index, left in enumerate(described):
        for right in described[left_index + 1 :]:
            score = similarity_score(str(left["description"]), str(right["description"]))
            if score < warn_threshold:
                continue
            pairs.append(
                {
                    "left": left["id"],
                    "right": right["id"],
                    "left_path": left["path"],
                    "right_path": right["path"],
                    "score": score,
                    "severity": "high" if score >= error_threshold else "warning",
                }
            )
    return sorted(pairs, key=lambda item: (-float(item["score"]), str(item["left"]), str(item["right"])))[:max_pairs]


def build_report(
    root: Path,
    *,
    warn_threshold: float = 0.55,
    error_threshold: float = 0.75,
    max_pairs: int = 50,
) -> dict[str, Any]:
    root = root.expanduser().resolve()
    items = collect_items(root)
    skill_ids = {str(item["id"]) for item in items if item.get("type") == "skill"}
    unresolved: list[dict[str, str]] = []
    for item in items:
        for ref in item.get("refs", []):
            if ref not in skill_ids:
                unresolved.append(
                    {
                        "path": str(item["path"]),
                        "source": str(item["id"]),
                        "ref": str(ref),
                        "category": "unresolved-skill-reference",
                    }
                )
    graph = {
        str(item["id"]): [str(ref) for ref in item.get("refs", []) if ref in skill_ids]
        for item in items
        if item.get("type") == "skill"
    }
    cycles = detect_cycles(graph)
    pairs = similarity_pairs(
        items,
        warn_threshold=warn_threshold,
        error_threshold=error_threshold,
        max_pairs=max_pairs,
    )
    high_pairs = [pair for pair in pairs if pair.get("severity") == "high"]
    boundary_issues = [
        {
            "path": str(item["path"]),
            "source": str(item["id"]),
            "category": "missing-invocation-boundary",
            "reason": "[skill:<id>] references should be paired with Scope and Out Of Scope sections",
        }
        for item in items
        if item.get("missing_boundary")
    ]
    issues: list[dict[str, Any]] = [*unresolved, *boundary_issues]
    warnings: list[dict[str, Any]] = [
        {"category": "reference-cycle", "cycle": cycle}
        for cycle in cycles
    ] + [
        {"category": "similarity", **pair}
        for pair in pairs
    ]
    summary = {
        "skill_count": sum(1 for item in items if item.get("type") == "skill"),
        "agent_count": sum(1 for item in items if item.get("type") == "agent"),
        "unresolved_reference_count": len(unresolved),
        "missing_boundary_count": len(boundary_issues),
        "cycle_count": len(cycles),
        "similarity_pair_count": len(pairs),
        "high_similarity_pair_count": len(high_pairs),
        "issue_count": len(issues),
        "warning_count": len(warnings),
    }
    return {
        "schema_version": 1,
        "tool": "skill-manager.candidate-source-audit",
        "ok": not issues and not high_pairs,
        "status": "passed" if not issues and not high_pairs else "issues-found",
        "root": str(root),
        "summary": summary,
        "items": items,
        "cycles": cycles,
        "similarity_pairs": pairs,
        "issues": issues,
        "warnings": warnings,
    }


def summarize_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.candidate-source-audit"),
        "ok": report.get("ok", False),
        "status": report.get("status", "unknown"),
        "root": report.get("root", ""),
        "summary": report.get("summary", {}),
        "issues": report.get("issues", []),
        "warnings": report.get("warnings", []),
    }
    if compact:
        summary.pop("root", None)
        if not summary.get("issues"):
            summary.pop("issues", None)
        if not summary.get("warnings"):
            summary.pop("warnings", None)
        return summary
    if summary.get("issues"):
        summary["issues"] = summary["issues"][:10]
    if summary.get("warnings"):
        summary["warnings"] = summary["warnings"][:10]
    return summary


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# Candidate Source Audit",
        "",
        f"- Status: `{report.get('status', 'unknown')}`",
        f"- Skills: {summary.get('skill_count', 0)}",
        f"- Agents: {summary.get('agent_count', 0)}",
        f"- Issues: {summary.get('issue_count', 0)}",
        f"- Warnings: {summary.get('warning_count', 0)}",
    ]
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        for item in issues[:40]:
            lines.append(
                f"- `{item.get('path', '')}`: {item.get('category', '')}"
                + (f" `{item.get('ref')}`" if item.get("ref") else "")
            )
    pairs = report.get("similarity_pairs") if isinstance(report.get("similarity_pairs"), list) else []
    if pairs:
        lines.extend(["", "## Similarity Pairs", ""])
        for pair in pairs[:20]:
            lines.append(
                f"- `{pair.get('left')}` / `{pair.get('right')}`: {pair.get('score')} ({pair.get('severity')})"
            )
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="candidate source folder to scan for routing references")
    parser.add_argument("--warn-threshold", type=float, default=0.55)
    parser.add_argument("--error-threshold", type=float, default=0.75)
    parser.add_argument("--max-pairs", type=int, default=50)
    parser.add_argument("--summary", action="store_true", help="emit compact audit fields")
    parser.add_argument("--compact", action="store_true", help="with --summary, omit passing issue/warning arrays")
    parser.add_argument("--strict", action="store_true", help="return non-zero when the audit reports issues")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(
        Path(args.source),
        warn_threshold=args.warn_threshold,
        error_threshold=args.error_threshold,
        max_pairs=args.max_pairs,
    )
    if args.summary or args.compact:
        report = summarize_report(report, compact=bool(args.compact))
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 1 if args.strict and not report.get("ok") else 0


if __name__ == "__main__":
    raise SystemExit(main())
