#!/usr/bin/env python3
"""Measure skill token-pressure budgets without calling external services."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import skill_manager_common as common
from repo_support import repo_policy

WARN_SKILL_WORDS = int(repo_policy.default_value("limits.skill.warn_words"))
FAIL_SKILL_WORDS = int(repo_policy.default_value("limits.skill.fail_words"))
TEXT_SUFFIXES = {".json", ".md", ".py", ".txt", ".yaml", ".yml"}
SUMMARY_DELTA_KEYS = (
    "skill_md_words",
    "routing_load_words",
    "guidance_load_words",
    "tool_load_words",
    "total_text_words",
    "warn_count",
    "fail_count",
)
ROUTE_ACTIVATION_EXTENSION = "skills-harness/token-budget"


def default_root() -> Path:
    return Path(__file__).resolve().parents[4]


def resolve_skill_dir(root: Path, value: str) -> Path:
    raw = value.strip()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if (candidate / "SKILL.md").exists() or (candidate / "module.json").exists():
        return candidate

    identifier = raw.removeprefix("skill:").strip()
    if identifier and not any(separator in identifier for separator in ("/", "\\")):
        for skill_dir in common.discover_skill_dirs(root):
            if skill_dir.name == identifier:
                return skill_dir.resolve()
            manifest, error = common.load_skill_manifest(skill_dir)
            if not error and isinstance(manifest, dict):
                if identifier in {str(manifest.get("id") or ""), str(manifest.get("name") or "")}:
                    return skill_dir.resolve()
    return candidate


def text_metrics_from_text(path_name: str, text: str) -> dict[str, Any]:
    return {
        "path": "",
        "words": common.word_count(text) if Path(path_name).name == "SKILL.md" else len(text.split()),
        "characters": len(text),
        "lines": len(text.splitlines()),
    }


def text_metrics(path: Path) -> dict[str, Any]:
    return text_metrics_from_text(path.name, common.read_text(path))


def support_load_bucket(relative_path: str) -> str:
    value = relative_path.replace("\\", "/")
    name = Path(value).name
    if value == "SKILL.md":
        return "routing"
    if value.startswith("scripts/") or name.endswith("-evals.json") or name.endswith("_evals.json"):
        return "tool"
    if value.startswith("docs/") or value.startswith("assets/"):
        return "guidance"
    return "other"


def reference_row_estimate(root: Path | None, skill_name: str, manifest: dict[str, Any] | None) -> dict[str, int]:
    if root is not None:
        registry = root / ".agents" / "registry.json"
        if registry.exists():
            data, error = common.read_json_file(registry)
            if error is None and isinstance(data, dict):
                for item in data.get("skills", []):
                    if isinstance(item, dict) and item.get("name") == skill_name:
                        text = json.dumps(item, sort_keys=True)
                        return {"words": len(text.split()), "characters": len(text)}

    fallback = {
        "name": skill_name,
        "summary": (manifest or {}).get("summary", ""),
        "version": (manifest or {}).get("version", ""),
        "dependencies": (manifest or {}).get("dependencies", []),
        "risk": (manifest or {}).get("risk", {}),
    }
    text = json.dumps(fallback, sort_keys=True)
    return {"words": len(text.split()), "characters": len(text)}


def git_output(root: Path, args: list[str]) -> tuple[bool, str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = completed.stdout if completed.returncode == 0 else completed.stderr
    return completed.returncode == 0, output


def git_text(root: Path, ref: str, relative_path: str) -> tuple[str | None, str | None]:
    normalized = relative_path.replace("\\", "/")
    ok, output = git_output(root, ["show", f"{ref}:{normalized}"])
    return (output, None) if ok else (None, output.strip() or f"cannot read {relative_path} at {ref}")


def git_file_list(root: Path, ref: str, relative_dir: str) -> tuple[list[str], str | None]:
    ok, output = git_output(root, ["ls-tree", "-r", "--name-only", ref, "--", relative_dir.replace("\\", "/")])
    if not ok:
        return [], output.strip() or f"cannot list {relative_dir} at {ref}"
    return [line.strip() for line in output.splitlines() if line.strip()], None


def reference_words_from_ref(root: Path, ref: str, skill_name: str, manifest: dict[str, Any] | None) -> int:
    registry_text, _error = git_text(root, ref, ".agents/registry.json")
    if registry_text:
        try:
            data = json.loads(registry_text)
        except json.JSONDecodeError:
            data = {}
        for item in data.get("skills", []) if isinstance(data, dict) else []:
            if isinstance(item, dict) and item.get("name") == skill_name:
                text = json.dumps(item, sort_keys=True)
                return len(text.split())
    fallback = {
        "name": skill_name,
        "summary": (manifest or {}).get("summary", ""),
        "version": (manifest or {}).get("version", ""),
        "dependencies": (manifest or {}).get("dependencies", []),
        "risk": (manifest or {}).get("risk", {}),
    }
    text = json.dumps(fallback, sort_keys=True)
    return len(text.split())


def status_for_skill_words(skill_words: int, root: Path | None = None) -> str:
    if root is None:
        if skill_words > FAIL_SKILL_WORDS:
            return "fail"
        if skill_words > WARN_SKILL_WORDS:
            return "warn"
        return "ok"
    return repo_policy.skill_word_status(root, skill_words)


def activation_metrics(*, component_type: str, path: str, text: str) -> dict[str, Any]:
    encoded = text.encode("utf-8")
    return {
        "type": component_type,
        "path": path,
        "words": len(text.split()),
        "characters": len(text),
        "tokens_estimated": (len(text) + 3) // 4 if text else 0,
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def exact_routing_entry(root: Path, skill_name: str) -> str:
    routing_path = root / ".agents" / "routing.md"
    if not routing_path.is_file():
        return ""
    try:
        text = routing_path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""
    marker = f"| `{skill_name}` |"
    rows = [line.strip() for line in text.splitlines() if marker in line]
    return rows[0] + "\n" if len(rows) == 1 else ""


def declared_direct_guidance(
    manifest: dict[str, Any] | None,
) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    extensions = manifest.get("extensions") if isinstance(manifest, dict) else {}
    extension = extensions.get(ROUTE_ACTIVATION_EXTENSION) if isinstance(extensions, dict) else None
    if extension is None:
        return [], issues
    if not isinstance(extension, dict):
        return [], [f"extensions.{ROUTE_ACTIVATION_EXTENSION} must be an object"]
    if "direct_guidance" not in extension:
        return [], issues
    values = extension.get("direct_guidance")
    if not isinstance(values, list):
        return [], [f"extensions.{ROUTE_ACTIVATION_EXTENSION}.direct_guidance must be a list"]
    paths: set[str] = set()
    for index, item in enumerate(values):
        if not isinstance(item, str) or not item.strip():
            issues.append(
                f"extensions.{ROUTE_ACTIVATION_EXTENSION}.direct_guidance[{index}] "
                "must be a non-empty string"
            )
            continue
        paths.add(item.replace("\\", "/"))
    return sorted(paths), issues


def route_activation_bundle(
    skill_dir: Path,
    root: Path | None,
    skill_name: str,
    manifest: dict[str, Any] | None,
    manifest_error: str | None = None,
) -> dict[str, Any]:
    components: list[dict[str, Any]] = []
    missing: list[str] = []
    issues: list[str] = []
    if manifest_error:
        issues.append(f"module manifest unavailable: {manifest_error}")
    resolved_root = root.resolve() if root is not None else skill_dir.parents[2].resolve()
    route_handle = f".agents/routing.md#skill:{skill_name}"
    routing_entry = exact_routing_entry(resolved_root, skill_name)
    if routing_entry:
        components.append(
            activation_metrics(
                component_type="routing-entry",
                path=route_handle,
                text=routing_entry,
            )
        )
    else:
        missing.append(route_handle)

    skill_path = skill_dir / "SKILL.md"
    skill_rel = common.relative(resolved_root, skill_path).replace("\\", "/")
    if skill_path.is_file():
        try:
            skill_text = skill_path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            missing.append(skill_rel)
            issues.append(f"could not read skill instructions: {exc}")
        else:
            components.append(
                activation_metrics(
                    component_type="skill-instructions",
                    path=skill_rel,
                    text=skill_text,
                )
            )
    else:
        missing.append(skill_rel)

    skill_root = skill_dir.resolve()
    direct_guidance, guidance_issues = declared_direct_guidance(manifest)
    issues.extend(guidance_issues)
    for declared in direct_guidance:
        candidate = skill_dir / declared
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(skill_root)
        except (OSError, ValueError):
            issues.append(f"unsafe direct guidance path: {declared}")
            continue
        rel = common.relative(resolved_root, resolved).replace("\\", "/")
        if not resolved.is_file():
            missing.append(rel)
            continue
        try:
            text = resolved.read_text(encoding="utf-8-sig")
        except OSError as exc:
            missing.append(rel)
            issues.append(f"could not read direct guidance {declared}: {exc}")
            continue
        components.append(
            activation_metrics(
                component_type="direct-guidance",
                path=rel,
                text=text,
            )
        )

    digest_rows = [
        {"type": row["type"], "path": row["path"], "sha256": row["sha256"]}
        for row in components
    ]
    digest = hashlib.sha256(
        (json.dumps(digest_rows, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "method": "estimated_chars_div_4",
        "provenance": "heuristic_estimate",
        "scope": "route_activation",
        "complete": not missing and not issues,
        "components": components,
        "component_count": len(components),
        "words": sum(int(row["words"]) for row in components),
        "characters": sum(int(row["characters"]) for row in components),
        "tokens_estimated": sum(int(row["tokens_estimated"]) for row in components),
        "bundle_sha256": digest,
        "missing": sorted(set(missing)),
        "issues": sorted(set(issues)),
    }


def measure_skill(skill_dir: Path, root: Path | None = None) -> dict[str, Any]:
    manifest, manifest_error = common.load_skill_manifest(skill_dir)
    metadata, _metadata_error = common.parse_frontmatter_file(skill_dir / "SKILL.md")
    files = [
        path
        for path in common.iter_files(skill_dir, max_files=5000)
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES
    ]
    base = skill_dir
    total_words = 0
    total_characters = 0
    by_bucket: dict[str, dict[str, int]] = {}
    load_split: dict[str, dict[str, int]] = {
        "routing": {"files": 0, "words": 0, "characters": 0, "lines": 0},
        "guidance": {"files": 0, "words": 0, "characters": 0, "lines": 0},
        "tool": {"files": 0, "words": 0, "characters": 0, "lines": 0},
        "other": {"files": 0, "words": 0, "characters": 0, "lines": 0},
    }
    largest: list[dict[str, Any]] = []

    for path in files:
        metrics = text_metrics(path)
        rel = common.relative(base, path)
        metrics["path"] = rel
        bucket = common.file_bucket(rel)
        bucket_metrics = by_bucket.setdefault(
            bucket, {"files": 0, "words": 0, "characters": 0, "lines": 0}
        )
        bucket_metrics["files"] += 1
        bucket_metrics["words"] += int(metrics["words"])
        bucket_metrics["characters"] += int(metrics["characters"])
        bucket_metrics["lines"] += int(metrics["lines"])
        split_metrics = load_split[support_load_bucket(rel)]
        split_metrics["files"] += 1
        split_metrics["words"] += int(metrics["words"])
        split_metrics["characters"] += int(metrics["characters"])
        split_metrics["lines"] += int(metrics["lines"])
        total_words += int(metrics["words"])
        total_characters += int(metrics["characters"])
        largest.append(metrics)

    skill_text = common.read_text(skill_dir / "SKILL.md") if (skill_dir / "SKILL.md").exists() else ""
    skill_words = common.word_count(skill_text)
    status = status_for_skill_words(skill_words, root)
    policy_root = root or repo_policy.project_root(skill_dir)
    warn_words = repo_policy.int_value(policy_root, "limits.skill.warn_words")
    fail_words = repo_policy.int_value(policy_root, "limits.skill.fail_words")

    skill_name = (metadata or {}).get("name", skill_dir.name)
    reference_estimate = reference_row_estimate(root, skill_name, manifest)
    routing_load = dict(load_split["routing"])
    routing_load["generated_reference_words"] = reference_estimate["words"]
    routing_load["generated_reference_characters"] = reference_estimate["characters"]
    routing_load["words"] += reference_estimate["words"]
    routing_load["characters"] += reference_estimate["characters"]

    trend = context_budget_trend(skill_dir, skill_words)
    route_activation = route_activation_bundle(
        skill_dir,
        root,
        str(skill_name),
        manifest,
        manifest_error,
    )
    report = {
        "name": skill_name,
        "path": str(skill_dir),
        "version": str((manifest or {}).get("version", "")),
        "skill_md": {
            "words": skill_words,
            "characters": len(skill_text),
            "status": status,
            "warn_above_words": warn_words,
            "fail_above_words": fail_words,
        },
        "total_text": {
            "files": len(files),
            "words": total_words,
            "characters": total_characters,
        },
        "routing_load": routing_load,
        "route_activation": route_activation,
        "guidance_load": load_split["guidance"],
        "tool_load": load_split["tool"],
        "other_load": load_split["other"],
        "by_bucket": dict(sorted(by_bucket.items())),
        "largest_files": sorted(largest, key=lambda item: int(item["words"]), reverse=True)[:10],
        "top_files_by_load_class": {
            load_class: sorted(
                [
                    item
                    for item in largest
                    if support_load_bucket(str(item.get("path", ""))) == load_class
                ],
                key=lambda item: int(item["words"]),
                reverse=True,
            )[:5]
            for load_class in ("routing", "guidance", "tool", "other")
        },
        "routing_context": {
            "frontmatter_only": {
                "name_characters": len(str((metadata or {}).get("name", ""))),
                "description_characters": len(str((metadata or {}).get("description", ""))),
            }
        },
        "context_budget_trend": trend,
        "maintainability_inventory": {
            "guidance_words": int(load_split["guidance"]["words"]),
            "tool_words": int(load_split["tool"]["words"]),
            "other_words": int(load_split["other"]["words"]),
            "total_text_words": total_words,
        },
    }
    report["budget_drilldown"] = budget_drilldown(report)
    report["tool_hotspots"] = tool_hotspots(report)
    report["optimization_suggestions"] = optimization_suggestions(report)
    return report


def context_budget_trend(skill_dir: Path, current_skill_words: int) -> dict[str, Any]:
    history_path = skill_dir / "docs" / "context-budget-history.json"
    if not history_path.exists():
        return {"available": False, "current_skill_md_words": current_skill_words}
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False, "current_skill_md_words": current_skill_words, "issue": "history is invalid JSON"}
    rows = data.get("history") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return {"available": False, "current_skill_md_words": current_skill_words, "issue": "history is empty"}
    previous = rows[-1] if isinstance(rows[-1], dict) else {}
    try:
        previous_words = int(previous.get("skill_md_words", current_skill_words))
    except (TypeError, ValueError):
        previous_words = current_skill_words
    return {
        "available": True,
        "current_skill_md_words": current_skill_words,
        "previous_skill_md_words": previous_words,
        "delta_skill_md_words": current_skill_words - previous_words,
    }


def budget_drilldown(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "by_load_class": {
            "routing": report.get("routing_load", {}),
            "guidance": report.get("guidance_load", {}),
            "tool": report.get("tool_load", {}),
            "other": report.get("other_load", {}),
        },
        "top_files_by_load_class": report.get("top_files_by_load_class", {}),
        "by_file_bucket": report.get("by_bucket", {}),
        "largest_files": report.get("largest_files", [])[:10],
    }


def tool_hotspots(report: dict[str, Any], *, threshold_words: int = 10000) -> list[dict[str, Any]]:
    hotspots: list[dict[str, Any]] = []
    top_files = (
        report.get("top_files_by_load_class", {}).get("tool", [])
        if isinstance(report.get("top_files_by_load_class"), dict)
        else []
    )
    for item in top_files if isinstance(top_files, list) else []:
        if not isinstance(item, dict):
            continue
        words = int(item.get("words", 0) or 0)
        if words < threshold_words:
            continue
        path = str(item.get("path", ""))
        if path.endswith("setup_impl.py"):
            action = "Split setup catalog, runtime doctor, and model inventory helpers into support modules."
        elif path.endswith("run_self_tests.py"):
            action = "Move focused self-test groups into scripts/self_tests/test_*.py modules."
        else:
            action = "Split stable helper groups into an imported support module before adding more behavior."
        hotspots.append(
            {
                "path": path,
                "words": words,
                "threshold_words": threshold_words,
                "action": action,
            }
        )
    return hotspots


def optimization_suggestions(report: dict[str, Any]) -> list[dict[str, str]]:
    suggestions: list[dict[str, str]] = []
    skill_md = report.get("skill_md", {}) if isinstance(report.get("skill_md"), dict) else {}
    guidance = report.get("guidance_load", {}) if isinstance(report.get("guidance_load"), dict) else {}
    tool = report.get("tool_load", {}) if isinstance(report.get("tool_load"), dict) else {}
    largest = report.get("largest_files", []) if isinstance(report.get("largest_files"), list) else []
    if skill_md.get("status") in {"warn", "fail"}:
        suggestions.append(
            {
                "area": "routing",
                "action": "Move examples and edge cases from SKILL.md into docs or scripts.",
                "target": "SKILL.md",
            }
        )
    if int(guidance.get("words", 0) or 0) > 1800:
        suggestions.append(
            {
                "area": "guidance",
                "action": "Split large guidance into narrowly routed docs and keep SKILL.md as the entrypoint.",
                "target": "docs/",
            }
        )
    if int(tool.get("words", 0) or 0) > 20000:
        suggestions.append(
            {
                "area": "tool",
                "action": "Review large script/test files when touched and split only around stable command boundaries.",
                "target": "scripts/",
            }
        )
    hotspots = tool_hotspots(report)
    for hotspot in hotspots[:3]:
        suggestions.append(
            {
                "area": "tool-hotspot",
                "action": str(hotspot.get("action", "")),
                "target": str(hotspot.get("path", "")),
            }
        )
    if largest:
        top = largest[0]
        suggestions.append(
            {
                "area": "largest-file",
                "action": "Inspect the largest file before adding more behavior to it.",
                "target": str(top.get("path", "")),
            }
        )
    if not suggestions:
        suggestions.append(
            {
                "area": "budget",
                "action": "No budget action needed.",
                "target": str(report.get("name", "")),
            }
        )
    return suggestions


def budget_trend_row(report: dict[str, Any], *, today: dt.date | None = None) -> dict[str, Any]:
    largest = report.get("largest_files", [])[0] if isinstance(report.get("largest_files"), list) and report.get("largest_files") else {}
    return {
        "date": (today or dt.date.today()).isoformat(),
        "skill_md_words": int(report.get("skill_md", {}).get("words", 0)) if isinstance(report.get("skill_md"), dict) else 0,
        "skill_md_status": report.get("skill_md", {}).get("status", "") if isinstance(report.get("skill_md"), dict) else "",
        "routing_load_words": int(report.get("routing_load", {}).get("words", 0)) if isinstance(report.get("routing_load"), dict) else 0,
        "guidance_load_words": int(report.get("guidance_load", {}).get("words", 0)) if isinstance(report.get("guidance_load"), dict) else 0,
        "tool_load_words": int(report.get("tool_load", {}).get("words", 0)) if isinstance(report.get("tool_load"), dict) else 0,
        "total_text_words": int(report.get("total_text", {}).get("words", 0)) if isinstance(report.get("total_text"), dict) else 0,
        "largest_file": str(largest.get("path", "")) if isinstance(largest, dict) else "",
        "largest_file_words": int(largest.get("words", 0)) if isinstance(largest, dict) else 0,
    }


def write_budget_trend(skill_dir: Path, report: dict[str, Any], *, today: dt.date | None = None) -> dict[str, Any]:
    history_path = skill_dir / "docs" / "context-budget-history.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    rows = data.get("history") if isinstance(data, dict) and isinstance(data.get("history"), list) else []
    row = budget_trend_row(report, today=today)
    kept = [item for item in rows if isinstance(item, dict) and item.get("date") != row["date"]]
    kept.append(row)
    kept.sort(key=lambda item: str(item.get("date", "")))
    output = {"schema_version": 1, "history": kept}
    history_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return {"skill": report.get("name", skill_dir.name), "path": common.relative(skill_dir, history_path), "history_count": len(kept)}


def baseline_skill_row(root: Path, ref: str, skill_dir: Path) -> tuple[dict[str, Any], list[str]]:
    rel_dir = common.relative(root, skill_dir).replace("\\", "/")
    files, error = git_file_list(root, ref, rel_dir)
    issues = [error] if error else []
    load_words = {key: 0 for key in ("routing", "guidance", "tool", "other")}
    total_words = 0
    skill_words = 0
    version = ""
    largest: list[dict[str, Any]] = []
    manifest: dict[str, Any] | None = None

    for path in files:
        if Path(path).suffix.lower() not in TEXT_SUFFIXES:
            continue
        text, text_error = git_text(root, ref, path)
        if text is None:
            issues.append(text_error or f"cannot read {path} at {ref}")
            continue
        rel = path[len(rel_dir) :].lstrip("/\\")
        metrics = text_metrics_from_text(rel, text)
        metrics["path"] = rel
        load_words[support_load_bucket(rel)] += int(metrics["words"])
        total_words += int(metrics["words"])
        largest.append(metrics)
        if rel == "SKILL.md":
            skill_words = int(metrics["words"])
        elif rel == "module.json":
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                value = {}
            if isinstance(value, dict):
                manifest = value
                version = str(value.get("version", ""))

    skill_name = skill_dir.name
    top = max(largest, key=lambda item: int(item["words"]), default={})
    return (
        {
            "name": skill_name,
            "skill_md_words": skill_words,
            "skill_md_status": status_for_skill_words(skill_words, root),
            "routing_load_words": load_words["routing"] + reference_words_from_ref(root, ref, skill_name, manifest),
            "guidance_load_words": load_words["guidance"],
            "tool_load_words": load_words["tool"],
            "total_text_words": total_words,
            "largest_file": str(top.get("path", "")),
            "largest_file_words": int(top.get("words", 0) or 0),
            "version": version,
        },
        [issue for issue in issues if issue],
    )


def baseline_summary_from_ref(root: Path, ref: str, skill_dirs: list[Path]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    for skill_dir in skill_dirs:
        row, row_issues = baseline_skill_row(root, ref, skill_dir)
        rows.append(row)
        issues.extend(row_issues)
    summary = {
        key: sum(int(row[key]) for row in rows)
        for key in SUMMARY_DELTA_KEYS[:5]
    }
    summary.update(
        {
            "warn_count": sum(1 for row in rows if row["skill_md_status"] == "warn"),
            "fail_count": sum(1 for row in rows if row["skill_md_status"] == "fail"),
        }
    )
    return {
        "ref": ref,
        "ok": not issues,
        "issues": issues,
        "summary": {"skill_count": len(rows), **summary},
        "skills": rows,
    }


def compare_summaries(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    current_summary = current.get("summary", {}) if isinstance(current.get("summary"), dict) else {}
    baseline_summary = baseline.get("summary", {}) if isinstance(baseline.get("summary"), dict) else {}
    current_rows = {str(row.get("name")): row for row in current.get("skills", []) if isinstance(row, dict)}
    baseline_rows = {str(row.get("name")): row for row in baseline.get("skills", []) if isinstance(row, dict)}
    skill_deltas = []
    for name in sorted(set(current_rows) | set(baseline_rows)):
        current_row = current_rows.get(name, {})
        baseline_row = baseline_rows.get(name, {})
        row = {"name": name}
        for key in SUMMARY_DELTA_KEYS[:-2]:
            row[key] = int(current_row.get(key, 0) or 0) - int(baseline_row.get(key, 0) or 0)
        if any(int(value) for key, value in row.items() if key != "name"):
            skill_deltas.append(row)
    return {
        "summary": {
            key: int(current_summary.get(key, 0) or 0) - int(baseline_summary.get(key, 0) or 0)
            for key in SUMMARY_DELTA_KEYS
        },
        "skills": sorted(
            skill_deltas,
            key=lambda item: abs(int(item.get("total_text_words", 0) or 0)),
            reverse=True,
        )[:10],
    }


def render_markdown(report: dict[str, Any]) -> str:
    if "summary" in report:
        return render_summary_markdown(report)
    lines = ["# Skill Budget Report", ""]
    for skill in report["skills"]:
        recommendations: list[str] = []
        if skill["skill_md"]["status"] == "fail":
            recommendations.append("Reduce SKILL.md below the hard limit or document a size exception.")
        elif skill["skill_md"]["status"] == "warn":
            recommendations.append("Move non-routing detail from SKILL.md into docs.")
        if skill["guidance_load"]["words"] > 1800:
            recommendations.append("Review large guidance files and keep low-context routing concise.")
        if skill["tool_load"]["words"]:
            recommendations.append("Tool-load words are script/test implementation size, not normal prompt load.")
        if not recommendations:
            recommendations.append("No budget action needed.")
        lines.extend(
            [
                f"## {skill['name']}",
                "",
                f"- Version: `{skill['version'] or 'unversioned'}`",
                f"- SKILL.md words: {skill['skill_md']['words']} ({skill['skill_md']['status']})",
                f"- Routing load words: {skill['routing_load']['words']} (SKILL.md + generated reference estimate)",
                f"- Route-activation bundle: {skill['route_activation']['tokens_estimated']} estimated tokens "
                f"({skill['route_activation']['words']} words; complete={skill['route_activation']['complete']})",
                f"- Guidance load words: {skill['guidance_load']['words']}",
                f"- Tool load words: {skill['tool_load']['words']}",
                f"- Total text words: {skill['total_text']['words']}",
                f"- Text files measured: {skill['total_text']['files']}",
                f"- Routing description characters: {skill['routing_context']['frontmatter_only']['description_characters']}",
                "",
                "Recommendation:",
            ]
        )
        trend = skill.get("context_budget_trend", {})
        if isinstance(trend, dict) and trend.get("available"):
            lines.append(f"- SKILL.md trend delta: {trend.get('delta_skill_md_words', 0)} words")
        lines.extend(f"- {item}" for item in recommendations)
        lines.extend(
            [
                "",
                "Largest files:",
            ]
        )
        for item in skill["largest_files"][:5]:
            lines.append(f"- `{item['path']}`: {item['words']} words")
        lines.append("")
    return "\n".join(lines).rstrip()


def summarize_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    skills = report["skills"]
    rows: list[dict[str, Any]] = []
    for skill in skills:
        largest = skill["largest_files"][0] if skill.get("largest_files") else {}
        rows.append(
            {
                "name": skill["name"],
                "skill_md_words": skill["skill_md"]["words"],
                "skill_md_status": skill["skill_md"]["status"],
                "routing_load_words": skill["routing_load"]["words"],
                "route_activation_words": skill.get("route_activation", {}).get("words", 0),
                "route_activation_tokens": skill.get("route_activation", {}).get("tokens_estimated", 0),
                "route_activation_complete": skill.get("route_activation", {}).get("complete", False),
                "guidance_load_words": skill["guidance_load"]["words"],
                "tool_load_words": skill["tool_load"]["words"],
                "total_text_words": skill["total_text"]["words"],
                "largest_file": largest.get("path", ""),
                "largest_file_words": largest.get("words", 0),
            }
        )
    compact_report: dict[str, Any] = {
        "version": report["version"],
        "summary": {
            "skill_count": len(skills),
            "skill_md_words": sum(int(item["skill_md"]["words"]) for item in skills),
            "routing_load_words": sum(int(item["routing_load"]["words"]) for item in skills),
            "route_activation_words": sum(int(item.get("route_activation", {}).get("words", 0)) for item in skills),
            "route_activation_tokens": sum(int(item.get("route_activation", {}).get("tokens_estimated", 0)) for item in skills),
            "route_activation_incomplete_count": sum(
                1 for item in skills if item.get("route_activation", {}).get("complete") is not True
            ),
            "guidance_load_words": sum(int(item["guidance_load"]["words"]) for item in skills),
            "tool_load_words": sum(int(item["tool_load"]["words"]) for item in skills),
            "total_text_words": sum(int(item["total_text"]["words"]) for item in skills),
            "warn_count": sum(1 for item in skills if item["skill_md"]["status"] == "warn"),
            "fail_count": sum(1 for item in skills if item["skill_md"]["status"] == "fail"),
        },
    }
    if compact:
        top_by_load_class: dict[str, list[dict[str, Any]]] = {}
        for load_class in ("routing", "guidance", "tool", "other"):
            load_key = f"{load_class}_load"
            file_rows: list[dict[str, Any]] = []
            for skill in skills:
                load = skill.get(load_key, {}) if isinstance(skill.get(load_key), dict) else {}
                top_files = (
                    skill.get("top_files_by_load_class", {}).get(load_class, [])
                    if isinstance(skill.get("top_files_by_load_class"), dict)
                    else []
                )
                for item in top_files if isinstance(top_files, list) else []:
                    if not isinstance(item, dict):
                        continue
                    file_rows.append(
                        {
                            "name": skill["name"],
                            "path": item.get("path", ""),
                            "words": item.get("words", 0),
                            "load_words": int(load.get("words", 0) or 0),
                        }
                    )
                if not top_files:
                    file_rows.append(
                        {
                            "name": skill["name"],
                            "path": "",
                            "words": 0,
                            "load_words": int(load.get("words", 0) or 0),
                        }
                    )
            top_by_load_class[load_class] = sorted(
                file_rows,
                key=lambda item: (
                    int(item.get("words", 0) or 0),
                    int(item.get("load_words", 0) or 0),
                ),
                reverse=True,
            )[:5]
            if not any(int(item.get("words", 0) or 0) for item in top_by_load_class[load_class]):
                top_by_load_class[load_class] = sorted(
                    [
                        {
                            "name": skill["name"],
                            "path": "",
                            "words": 0,
                            "load_words": int(
                                (skill.get(load_key, {}) if isinstance(skill.get(load_key), dict) else {}).get("words", 0)
                                or 0
                            ),
                        }
                        for skill in skills
                    ],
                    key=lambda item: int(item.get("load_words", 0) or 0),
                    reverse=True,
                )[:5]
        compact_report["top"] = sorted(
            [
                {
                    "name": row["name"],
                    "total_text_words": row["total_text_words"],
                    "largest_file": row["largest_file"],
                }
                for row in rows
            ],
            key=lambda item: int(item.get("total_text_words", 0) or 0),
            reverse=True,
        )[:5]
        compact_report["warnings"] = [
            {
                "name": row["name"],
                "skill_md_words": row["skill_md_words"],
                "skill_md_status": row["skill_md_status"],
            }
            for row in rows
            if row["skill_md_status"] in {"warn", "fail"}
        ]
        if not compact_report["warnings"]:
            compact_report.pop("warnings", None)
        compact_report["top_by_load_class"] = top_by_load_class
    else:
        compact_report["root"] = report["root"]
        compact_report["skills"] = rows
    return compact_report


def render_summary_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Skill Budget Summary",
        "",
        f"- Skills: {summary['skill_count']}",
        f"- SKILL.md words: {summary['skill_md_words']}",
        f"- Routing load words: {summary['routing_load_words']}",
        f"- Route-activation estimated tokens: {summary.get('route_activation_tokens', 0)}",
        f"- Incomplete route-activation bundles: {summary.get('route_activation_incomplete_count', 0)}",
        f"- Guidance load words: {summary['guidance_load_words']}",
        f"- Tool load words: {summary['tool_load_words']}",
        f"- Total text words: {summary['total_text_words']}",
        f"- Warnings/failures: {summary['warn_count']}/{summary['fail_count']}",
    ]
    baseline = report.get("baseline") if isinstance(report.get("baseline"), dict) else {}
    delta = report.get("delta") if isinstance(report.get("delta"), dict) else {}
    delta_summary = delta.get("summary") if isinstance(delta.get("summary"), dict) else {}
    if delta_summary:
        lines.extend(["", f"Delta vs `{baseline.get('ref', 'baseline')}`:"])
        for key in SUMMARY_DELTA_KEYS:
            lines.append(f"- {key}: {delta_summary.get(key, 0):+}")
    skills = report.get("skills") if isinstance(report.get("skills"), list) else []
    if skills:
        lines.extend(["", "| Skill | SKILL.md | Routing | Guidance | Tool | Total | Largest |", "|---|---:|---:|---:|---:|---:|---|"])
        for skill in skills:
            lines.append(
                f"| `{skill['name']}` | {skill['skill_md_words']} | {skill['routing_load_words']} | "
                f"{skill['guidance_load_words']} | {skill['tool_load_words']} | "
                f"{skill['total_text_words']} | `{skill['largest_file']}` |"
            )
    top = report.get("top") if isinstance(report.get("top"), list) else []
    if top:
        lines.extend(["", "Largest active totals:"])
        for skill in top:
            lines.append(f"- `{skill['name']}`: {skill['total_text_words']} words")
    return "\n".join(lines).rstrip()


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).expanduser().resolve() if args.root else default_root()
    if args.all:
        skill_dirs = common.discover_skill_dirs(root)
    else:
        skill_dirs = [resolve_skill_dir(root, str(args.skill))]
    skill_reports = [measure_skill(skill_dir, root) for skill_dir in skill_dirs]
    report = {
        "version": 1,
        "root": str(root),
        "skills": skill_reports,
    }
    baseline_ref = str(getattr(args, "baseline_ref", "") or "").strip()
    current_summary = summarize_report(report, compact=False)
    baseline_report: dict[str, Any] | None = None
    if baseline_ref:
        baseline_report = baseline_summary_from_ref(root, baseline_ref, skill_dirs)
        report["baseline"] = baseline_report
        report["delta"] = compare_summaries(current_summary, baseline_report)
    trend_written: list[dict[str, Any]] = []
    if getattr(args, "write_trend", False):
        trend_written = [
            write_budget_trend(skill_dir, skill_report)
            for skill_dir, skill_report in zip(skill_dirs, skill_reports)
        ]
        report["trend_written"] = trend_written
    if args.summary:
        summary_report = summarize_report(report, compact=bool(getattr(args, "compact", False)))
        if baseline_report is not None:
            summary_report["baseline"] = {
                "ref": baseline_report["ref"],
                "ok": baseline_report["ok"],
                "issue_count": len(baseline_report.get("issues", [])),
                "summary": baseline_report["summary"],
            }
            if baseline_report.get("issues"):
                summary_report["baseline"]["issues"] = baseline_report["issues"]
            summary_report["delta"] = compare_summaries(current_summary, baseline_report)
        if trend_written:
            summary_report["trend_written"] = trend_written
            summary_report["summary"]["trend_written_count"] = len(trend_written)
        return summary_report
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="repository root; defaults to script parent")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--skill", help="skill id or folder to measure")
    target.add_argument("--all", action="store_true", help="measure all accepted skills")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown", dest="output_format")
    parser.add_argument("--summary", action="store_true", help="emit compact aggregate rows without per-file detail")
    parser.add_argument("--compact", action="store_true", help="with --summary, omit per-skill rows except top/warning facts")
    parser.add_argument("--write-trend", action="store_true", help="write docs/context-budget-history.json for measured skill(s)")
    parser.add_argument("--baseline-ref", help="compare current measurements with a git ref such as HEAD")
    return parser


def main() -> int:
    common.require_supported_python()
    args = build_parser().parse_args()
    report = build_report(args)
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    baseline = report.get("baseline") if isinstance(report, dict) else None
    if isinstance(baseline, dict) and baseline.get("ok") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
