"""Lightweight shared report helpers for local AI commands."""

from __future__ import annotations

import contextlib
import io
import json
import re


def relative(root, path):
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def print_json(report):
    print(json.dumps(report, indent=2, sort_keys=True))


def safe_cache_slug(value):
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return slug[:80] or "item"


def cache_file(root, group, name, suffix=".json"):
    safe_group = safe_cache_slug(group)
    safe_name = safe_cache_slug(name)
    path = root / ".agents" / "local-ai" / "cache" / safe_group / f"{safe_name}{suffix}"
    rel_path = relative(root, path)
    return path, rel_path


def write_report_cache(root, report):
    cache_path = str(report.get("cache_path", ""))
    if not cache_path:
        return
    path = (root / cache_path).resolve()
    cache_root = (root / ".agents" / "local-ai" / "cache").resolve()
    try:
        path.relative_to(cache_root)
    except ValueError as exc:
        raise RuntimeError(
            f"Refusing to write local AI report outside cache: {cache_path}"
        ) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def as_text_list(value, *, max_items=8):
    if isinstance(value, list):
        values = [" ".join(str(item).split()) for item in value]
    elif isinstance(value, str) and value.strip():
        values = [line.strip("- ").strip() for line in value.splitlines()]
    else:
        values = []
    return [item for item in values if item][:max_items]


def normalize_evidence(value, default_source="model"):
    if isinstance(value, list):
        evidence = []
        for item in value[:12]:
            if isinstance(item, dict):
                evidence.append(
                    {
                        str(key): val
                        for key, val in item.items()
                        if isinstance(key, str)
                    }
                )
            elif str(item).strip():
                evidence.append(
                    {
                        "source": default_source,
                        "excerpt": " ".join(str(item).split())[:500],
                    }
                )
        return evidence
    if isinstance(value, str) and value.strip():
        return [
            {
                "source": default_source,
                "excerpt": " ".join(value.split())[:500],
            }
        ]
    return []


def stable_report(
    *,
    ok,
    task,
    profile,
    input_paths,
    summary,
    findings=None,
    suggestions=None,
    evidence=None,
    cache_path="",
    issues=None,
    **extra,
):
    report = {
        "ok": bool(ok),
        "task": task,
        "profile": profile,
        "input_paths": input_paths,
        "summary": " ".join(str(summary).split())[:1200],
        "findings": findings or [],
        "suggestions": suggestions or [],
        "evidence": evidence or [],
        "cache_path": cache_path,
        "issues": issues or [],
    }
    report.update(extra)
    return report


def print_report(report, *, as_json):
    if as_json:
        print_json(report)
    else:
        title = str(report.get("task", "local-ai")).replace("-", " ").title()
        print(f"Local AI {title}")
        print(f"  OK: {bool(report.get('ok'))}")
        print(f"  Profile: {report.get('profile', '')}")
        if report.get("summary"):
            print(f"  Summary: {report['summary']}")
        for label in ("findings", "suggestions"):
            values = report.get(label, [])
            if isinstance(values, list) and values:
                print(f"  {label.title()}:")
                for value in values:
                    print(f"    - {value}")
        evidence = report.get("evidence", [])
        if isinstance(evidence, list) and evidence:
            print("  Evidence:")
            for item in evidence[:6]:
                if isinstance(item, dict):
                    source = item.get(
                        "path",
                        item.get("source", item.get("page", "")),
                    )
                    excerpt = item.get("excerpt", item.get("text", ""))
                    detail = f"{source}: {excerpt}".strip(": ")
                    print(
                        f"    - {detail or json.dumps(item, sort_keys=True)}"
                    )
        if report.get("cache_path"):
            print(f"  Cache: {report['cache_path']}")
        issues = report.get("issues", [])
        if isinstance(issues, list) and issues:
            print("  Issues:")
            for issue in issues:
                print(f"    - {issue}")
    return 0 if report.get("ok") or not report.get("required") else 1


def print_generated_report(factory, *, as_json):
    if as_json:
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            report = factory()
        log_lines = [
            line
            for line in captured.getvalue().splitlines()
            if line.strip()
        ]
        if log_lines:
            report["log"] = log_lines
        return print_report(report, as_json=True)
    return print_report(factory(), as_json=False)
