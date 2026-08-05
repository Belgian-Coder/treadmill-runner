"""Evidence listing helpers for daily repository commands."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from repo_support import repo_common as repo

LAST_VALIDATION = Path(".agents/local-ai/cache/last-validation.txt")
RAW_OUTPUT_RE = re.compile(r"Raw output:\s*`?([^`\r\n]+)`?", re.IGNORECASE)
DIGEST_RE = re.compile(r"digest\s+([0-9a-f]{8,64})", re.IGNORECASE)


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def safe_repo_file(root: Path, value: str) -> tuple[Path, str, str]:
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return resolved, value, "path escapes repository"
    return resolved, repo.relative(root, resolved), ""


def latest_workflow_runs(root: Path, *, limit: int = 5) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run_json in sorted((root / "automations").glob("*/runs/*/run.json")):
        try:
            data = json.loads(run_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        workflow = str(data.get("workflow") or run_json.parents[2].name)
        rows.append(
            {
                "workflow": workflow,
                "run_id": data.get("run_id", run_json.parent.name),
                "status": data.get("status", ""),
                "external_validation_status": data.get("external_validation_status", ""),
                "updated_at": data.get("updated_at", ""),
                "path": repo.relative(root, run_json.parent),
                "summary": data.get("next_action", ""),
                "next_command": f"python -B .agents/manage.py review {workflow} --plan",
            }
        )
    return sorted(rows, key=lambda item: str(item.get("updated_at", "")), reverse=True)[:limit]


def latest_benchmark_reports(root: Path, *, limit: int = 5) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    benchmark_roots = [root / "automations" / "agent-benchmarking" / "runs"]
    for report_path in sorted(path for base in benchmark_roots for path in base.glob("*/benchmark-result.json")):
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        rows.append(
            {
                "path": repo.relative(root, report_path),
                "run": report_path.parent.name,
                "status": data.get("status", ""),
                "ok": data.get("ok", None),
                "task_id": data.get("task_id", ""),
                "model_label": data.get("model_label", ""),
            }
        )
    rows.sort(key=lambda item: (root / str(item["path"])).stat().st_mtime if (root / str(item["path"])).exists() else 0, reverse=True)
    return rows[:limit]


def document_evidence_packets(root: Path, *, limit: int = 8) -> list[dict[str, Any]]:
    base = root / ".agents" / "local-ai" / "cache" / "documents"
    if not base.exists():
        return []
    files = [path for path in base.rglob("*") if path.is_file() and path.suffix.lower() in {".json", ".md", ".markdown"}]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return [{"path": repo.relative(root, path), "size_bytes": path.stat().st_size} for path in files[:limit]]


def newest_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return rows[0] if rows else {}


def latest_evidence_report(root: Path, *, open_latest: bool = False) -> dict[str, Any]:
    validation = root / LAST_VALIDATION
    local_ai_reports = sorted(
        [
            path
            for path in (root / ".agents" / "local-ai" / "cache").rglob("*.json")
            if path.is_file()
        ],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:8] if (root / ".agents" / "local-ai" / "cache").exists() else []
    workflow_runs = latest_workflow_runs(root)
    benchmarks = latest_benchmark_reports(root)
    documents = document_evidence_packets(root)
    report = {
        "schema_version": 1,
        "tool": "repo-evidence-index",
        "ok": True,
        "latest_validation": repo.relative(root, validation) if validation.exists() else "",
        "workflow_runs": workflow_runs,
        "benchmarks": benchmarks,
        "document_evidence": documents,
        "local_ai_reports": [{"path": repo.relative(root, path), "size_bytes": path.stat().st_size} for path in local_ai_reports],
        "next_command": "python -B .agents/manage.py resume-work",
    }
    if open_latest:
        report["open_latest"] = {
            "workflow_run": newest_row(workflow_runs),
            "benchmark": newest_row(benchmarks),
            "document_evidence": newest_row(documents),
            "local_ai_report": newest_row(report["local_ai_reports"]),
            "note": "Paths are highlighted for the user; the command does not open external applications.",
        }
    return report


def summarize_evidence_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    workflow_runs = report.get("workflow_runs") if isinstance(report.get("workflow_runs"), list) else []
    benchmarks = report.get("benchmarks") if isinstance(report.get("benchmarks"), list) else []
    documents = report.get("document_evidence") if isinstance(report.get("document_evidence"), list) else []
    local_ai_reports = report.get("local_ai_reports") if isinstance(report.get("local_ai_reports"), list) else []
    summary: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "repo-evidence-index"),
        "ok": bool(report.get("ok", True)),
        "latest_validation": report.get("latest_validation", ""),
        "summary": {
            "workflow_run_count": len(workflow_runs),
            "benchmark_count": len(benchmarks),
            "document_evidence_count": len(documents),
            "local_ai_report_count": len(local_ai_reports),
        },
        "next_command": report.get("next_command", ""),
    }
    if compact:
        latest_workflow = newest_row(workflow_runs)
        latest_benchmark = newest_row(benchmarks)
        latest_document = newest_row(documents)
        latest_local_ai = newest_row(local_ai_reports)
        latest = {
            "workflow_run": latest_workflow.get("path", ""),
            "workflow_status": latest_workflow.get("status", ""),
            "benchmark": latest_benchmark.get("path", ""),
            "benchmark_status": latest_benchmark.get("status", ""),
            "document_evidence": latest_document.get("path", ""),
            "local_ai_report": latest_local_ai.get("path", ""),
        }
        summary["latest"] = {key: value for key, value in latest.items() if value}
        if bool(summary.get("ok")):
            summary.pop("next_command", None)
    else:
        summary["latest"] = {
            "workflow_run": newest_row(workflow_runs),
            "benchmark": newest_row(benchmarks),
            "document_evidence": newest_row(documents),
            "local_ai_report": newest_row(local_ai_reports),
        }
        summary["workflow_runs"] = workflow_runs
        summary["benchmarks"] = benchmarks
        summary["document_evidence"] = documents
        summary["local_ai_reports"] = local_ai_reports
    if isinstance(report.get("open_latest"), dict):
        summary["open_latest"] = report.get("open_latest", {})
    return summary


def collect_json_raw_references(value: Any, location: str = "$") -> list[dict[str, str]]:
    references: list[dict[str, str]] = []
    if isinstance(value, dict):
        raw_path = value.get("raw_output_path")
        if isinstance(raw_path, str) and raw_path.strip():
            summary = value.get("output_summary") if isinstance(value.get("output_summary"), dict) else {}
            digest = summary.get("digest", value.get("digest", ""))
            references.append({"raw_output_path": raw_path, "digest": str(digest or ""), "location": location})
        for key, child in value.items():
            references.extend(collect_json_raw_references(child, f"{location}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            references.extend(collect_json_raw_references(child, f"{location}[{index}]"))
    return references


def collect_text_raw_references(text: str) -> list[dict[str, str]]:
    raw_paths = [match.group(1).strip() for match in RAW_OUTPUT_RE.finditer(text)]
    digests = [match.group(1).lower() for match in DIGEST_RE.finditer(text)]
    references: list[dict[str, str]] = []
    for index, raw_path in enumerate(raw_paths):
        digest = digests[index] if index < len(digests) else (digests[0] if len(digests) == 1 else "")
        references.append({"raw_output_path": raw_path, "digest": digest, "location": f"text[{index}]"})
    return references


def evidence_file_references(path: Path) -> tuple[list[dict[str, str]], str | None]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        return [], str(exc)
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return [], f"invalid JSON: {exc}"
        references = collect_json_raw_references(data)
        if references:
            return references, None
    return collect_text_raw_references(text), None


def verify_raw_reference(root: Path, source: str, reference: dict[str, str]) -> dict[str, Any]:
    raw_path, rel, path_issue = safe_repo_file(root, reference.get("raw_output_path", ""))
    expected_digest = str(reference.get("digest", "") or "").lower()
    row: dict[str, Any] = {
        "source": source,
        "location": reference.get("location", ""),
        "raw_output_path": rel,
        "exists": raw_path.is_file(),
        "digest_expected": expected_digest,
        "digest_actual": "",
        "digest_ok": None,
        "ok": False,
        "issue": path_issue,
    }
    if path_issue:
        return row
    if not raw_path.is_file():
        row["issue"] = "raw output file is missing"
        return row
    try:
        actual = digest_bytes(raw_path.read_bytes())
    except OSError as exc:
        row["issue"] = str(exc)
        return row
    row["digest_actual"] = actual
    if expected_digest:
        row["digest_ok"] = actual.startswith(expected_digest[:16])
        if not row["digest_ok"]:
            row["issue"] = "raw output digest mismatch"
    else:
        row["digest_ok"] = None
    row["ok"] = bool(row["exists"]) and row["issue"] == ""
    return row


def evidence_verify_report(root: Path, files: list[str] | None = None) -> dict[str, Any]:
    if files is None and not (root / LAST_VALIDATION).is_file():
        return {
            "schema_version": 1,
            "tool": "repo-evidence-verify",
            "ok": True,
            "status": "no-evidence",
            "summary": {
                "source_count": 0,
                "reference_count": 0,
                "missing_count": 0,
                "digest_mismatch_count": 0,
                "issue_count": 0,
            },
            "sources": [],
            "references": [],
            "issues": [],
            "next_command": "python -B .agents/manage.py evidence-verify --file <compact-report> --summary --compact --format json",
        }
    selected = files or [str(LAST_VALIDATION)]
    sources: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    issues: list[str] = []
    for item in selected:
        source_path, source_rel, path_issue = safe_repo_file(root, item)
        if path_issue:
            issues.append(f"{item}: {path_issue}")
            sources.append({"path": item, "ok": False, "issue": path_issue, "reference_count": 0})
            continue
        refs, error = evidence_file_references(source_path)
        if error:
            issues.append(f"{source_rel}: {error}")
            sources.append({"path": source_rel, "ok": False, "issue": error, "reference_count": 0})
            continue
        rows = [verify_raw_reference(root, source_rel, reference) for reference in refs]
        references.extend(rows)
        bad_rows = [row for row in rows if not row.get("ok")]
        sources.append(
            {
                "path": source_rel,
                "ok": not bad_rows,
                "issue": "" if not bad_rows else "one or more raw references are invalid",
                "reference_count": len(rows),
            }
        )
    missing = [row for row in references if not row.get("exists")]
    mismatched = [row for row in references if row.get("digest_ok") is False]
    ok = not issues and not missing and not mismatched
    return {
        "schema_version": 1,
        "tool": "repo-evidence-verify",
        "ok": ok,
        "status": "passed" if ok else "failed",
        "summary": {
            "source_count": len(sources),
            "reference_count": len(references),
            "missing_count": len(missing),
            "digest_mismatch_count": len(mismatched),
            "issue_count": len(issues),
        },
        "sources": sources,
        "references": references,
        "issues": issues,
        "next_command": "python -B .agents/manage.py evidence-verify --file <compact-report> --summary --compact --format json",
    }


def summarize_evidence_verify_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    output: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "repo-evidence-verify"),
        "ok": bool(report.get("ok", False)),
        "status": report.get("status", "unknown"),
        "summary": report.get("summary", {}),
        "issues": report.get("issues", []),
        "next_command": report.get("next_command", ""),
    }
    failed = [row for row in report.get("references", []) if isinstance(row, dict) and not row.get("ok")]
    if failed:
        output["failed_references"] = failed
    if not compact:
        output["sources"] = report.get("sources", [])
        output["references"] = report.get("references", [])
    if compact:
        if not output.get("issues"):
            output.pop("issues", None)
        if output.get("ok"):
            output.pop("next_command", None)
    return output


def render_evidence_verify(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = ["# Evidence Verify", "", f"- Status: {report.get('status')}"]
    lines.append(f"- Sources: {summary.get('source_count', 0)}")
    lines.append(f"- Raw references: {summary.get('reference_count', 0)}")
    lines.append(f"- Missing: {summary.get('missing_count', 0)}")
    lines.append(f"- Digest mismatches: {summary.get('digest_mismatch_count', 0)}")
    failed = report.get("failed_references") if isinstance(report.get("failed_references"), list) else []
    if not failed:
        failed = [row for row in report.get("references", []) if isinstance(row, dict) and not row.get("ok")]
    if failed:
        lines.extend(["", "## Failed References", ""])
        for row in failed:
            lines.append(f"- `{row.get('source')}` -> `{row.get('raw_output_path')}`: {row.get('issue')}")
    issues = report.get("issues", []) if isinstance(report.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {item}" for item in issues)
    lines.extend(["", f"Next command: `{report.get('next_command')}`", ""])
    return "\n".join(lines)
