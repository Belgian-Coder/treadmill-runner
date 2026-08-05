#!/usr/bin/env python3
"""Index workflow-local run folders."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import workflow_manager_common as common

KEY_RUN_FILES = {
    "REPORT.md",
    "benchmark-result.json",
    "benchmark-task.json",
    "context-packet.json",
    "run.json",
}


@dataclass(frozen=True)
class Args:
    root: Path
    workflow_name: str
    write: bool
    check: bool
    output_format: str


def workflow_dir(root: Path, workflow_name: str) -> Path:
    if not common.SKILL_NAME_PATTERN.match(workflow_name):
        raise SystemExit("workflow name must use lowercase letters, digits, and hyphens.")
    path = root / "automations" / workflow_name
    if not path.exists() or not path.is_dir():
        raise SystemExit(f"automation workflow not found: automations/{workflow_name}")
    return path


def read_json_ok(path: Path) -> bool | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and isinstance(data.get("ok"), bool):
        return bool(data["ok"])
    return None


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def normalize_status(value: object) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    if raw in {"complete", "completed", "completed-with-findings", "done", "passed", "passed-with-findings", "success"}:
        return "completed"
    if raw in {"blocked", "external-blocked"}:
        return "blocked"
    if raw in {"failed", "failure", "error"}:
        return "failed"
    if raw in {"partial", "in-progress", "running"}:
        return "partial"
    return raw or ""


def parse_timestamp(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw + "T00:00:00+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def timestamp_from_value(value: Any) -> str:
    timestamp_keys = (
        "updated_at",
        "finished_at",
        "completed_at",
        "generated_at",
        "created_at",
        "recorded_at",
        "timestamp",
        "time",
    )
    if isinstance(value, dict):
        for key in timestamp_keys:
            timestamp = parse_timestamp(value.get(key))
            if timestamp:
                return timestamp
        for item in value.values():
            timestamp = timestamp_from_value(item)
            if timestamp:
                return timestamp
    elif isinstance(value, list):
        for item in value:
            timestamp = timestamp_from_value(item)
            if timestamp:
                return timestamp
    return ""


def evidence_ledger(run_dir: Path) -> dict[str, Any]:
    return read_json_object(run_dir / "run.json")


def resume_state(run_dir: Path) -> dict[str, Any]:
    return read_json_object(run_dir / "run.json")


def first_heading(path: Path) -> str:
    for line in common.read_text(path, limit=20_000).splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def has_nonempty_status_field(log_text: str, field: str) -> bool:
    neutral_values = {"", "-", "0", "n/a", "no", "none", "not applicable"}
    prefix = f"{field.lower()}:"
    bullet_prefix = f"- {prefix}"
    for line in log_text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith(bullet_prefix):
            value = stripped.removeprefix(bullet_prefix).strip()
        elif stripped.startswith(prefix):
            value = stripped.removeprefix(prefix).strip()
        else:
            continue
        normalized = value.strip(" .;")
        if normalized not in neutral_values:
            return True
    return False


def run_status(run_dir: Path, files: list[Path]) -> str:
    if not files:
        return "empty"
    ledger_status = normalize_status(evidence_ledger(run_dir).get("status"))
    if ledger_status in {"completed", "failed", "blocked", "partial"}:
        return ledger_status
    log_text = "\n".join(
        common.read_text(path, limit=80_000).lower()
        for path in files
        if path.name in {"execution-log.md", "validation.md"}
    )
    validation_results = [
        value
        for path in files
        if path.suffix.lower() == ".json"
        for value in [read_json_ok(path)]
        if value is not None
    ]
    if False in validation_results or has_nonempty_status_field(log_text, "failed"):
        return "failed"
    if has_nonempty_status_field(log_text, "blocked"):
        return "blocked"
    if (run_dir / "pr-description.md").exists() or True in validation_results:
        return "completed"
    return "partial"


def external_validation_status(run_dir: Path) -> str:
    ledger = evidence_ledger(run_dir)
    for key in ("external_validation_status", "external_validation"):
        value = ledger.get(key)
        if isinstance(value, dict):
            status = normalize_status(value.get("status"))
        else:
            status = normalize_status(value)
        if status:
            return status
    blocked = ledger.get("blocked", [])
    if isinstance(blocked, list) and any("github actions" in str(item).lower() for item in blocked):
        return "blocked"
    return "not-recorded"


def run_summary(run_dir: Path) -> str:
    for name in ("REPORT.md", "ticket-info.md", "execution-log.md", "plan.md", "pr-description.md"):
        path = run_dir / name
        if path.exists():
            heading = first_heading(path)
            if heading:
                return heading
    return run_dir.name


def updated_at(run_dir: Path) -> str:
    for value in (evidence_ledger(run_dir), resume_state(run_dir)):
        timestamp = timestamp_from_value(value)
        if timestamp:
            return timestamp
    return ""


def file_summary(run_dir: Path, files: list[Path]) -> dict[str, Any]:
    relative_files = [common.relative(run_dir, path) for path in files]
    validation_count = sum(1 for item in relative_files if item.startswith("validation/"))
    artifact_count = sum(1 for item in relative_files if item.startswith("artifacts/"))
    key_files = [
        item
        for item in relative_files
        if item in KEY_RUN_FILES or item.startswith("validation/current-setup")
    ]
    return {
        "file_count": len(files),
        "validation_file_count": validation_count,
        "artifact_file_count": artifact_count,
        "key_files": sorted(key_files),
    }


def collect_run(root: Path, runs_dir: Path, run_dir: Path) -> dict[str, Any]:
    files = sorted(
        [path for path in run_dir.rglob("*") if path.is_file() and path.name not in {"INDEX.md", "index.json"}],
        key=lambda item: item.as_posix().lower(),
    )
    workflow_status = run_status(run_dir, files)
    return {
        "id": run_dir.name,
        "path": common.relative(root, run_dir),
        "status": workflow_status,
        "workflow_status": workflow_status,
        "external_validation_status": external_validation_status(run_dir),
        "summary": run_summary(run_dir),
        "updated_at": updated_at(run_dir),
        "resume_state_path": "run.json" if (run_dir / "run.json").exists() else "",
        "run_packet_path": "run.json" if (run_dir / "run.json").exists() else "",
        **file_summary(run_dir, files),
    }


def build_index(root: Path, workflow_name: str) -> dict[str, Any]:
    root = root.expanduser().resolve()
    module_dir = workflow_dir(root, workflow_name)
    runs_dir = module_dir / "runs"
    checks: list[str] = [f"workflow found: automations/{workflow_name}"]
    skipped: list[str] = []
    runs: list[dict[str, Any]] = []
    if not runs_dir.exists():
        skipped.append("runs folder not present; no active v2 runs indexed")
    else:
        checks.append(f"runs folder found: {common.relative(root, runs_dir)}")
        for child in sorted(runs_dir.iterdir(), key=lambda item: item.name.lower()):
            if child.is_dir():
                runs.append(collect_run(root, runs_dir, child))
            elif child.name not in {"README.md", "INDEX.md", "index.json"}:
                skipped.append(f"ignored non-run file `{common.relative(root, child)}`")

    counts: dict[str, int] = {}
    for item in runs:
        counts[str(item["status"])] = counts.get(str(item["status"]), 0) + 1
    summary = {
        "total": len(runs),
        "by_status": counts,
        "file_count": sum(int(item.get("file_count", 0) or 0) for item in runs),
    }
    return {
        "schema_version": 1,
        "tool": "index-workflow-runs",
        "ok": True,
        "status": "ok",
        "workflow": workflow_name,
        "workflow_path": common.relative(root, module_dir),
        "runs_path": common.relative(root, runs_dir),
        "summary": summary,
        "runs": runs,
        "checks": checks,
        "skipped": skipped,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Workflow Run Index",
        "",
        f"- Workflow: `{report['workflow']}`",
        f"- Runs path: `{report['runs_path']}`",
        f"- Status: {report['status']}",
        f"- Total runs: {report['summary']['total']}",
        "",
        "| Run | Status | Updated | Summary | Files |",
        "|---|---|---|---|---|",
    ]
    for item in report["runs"]:
        files = int(item.get("file_count", 0) or 0)
        lines.append(
            f"| `{item['id']}` | {item['status']} | {item.get('updated_at', '')} | "
            f"{item.get('summary', '')} | {files} |"
        )
    if not report["runs"]:
        lines.append("| none | - | - | No run folders indexed. | 0 |")
    lines.extend(["", "## Checks", ""])
    lines.extend(f"- {item}" for item in report["checks"])
    lines.extend(["", "## Skipped", ""])
    if report["skipped"]:
        lines.extend(f"- {item}" for item in report["skipped"])
    else:
        lines.append("- None.")
    return "\n".join(lines)


def expected_outputs(report: dict[str, Any]) -> tuple[str, str]:
    markdown = render_markdown(report) + "\n"
    data = json.dumps(report, indent=2, sort_keys=True) + "\n"
    return markdown, data


def write_outputs(root: Path, report: dict[str, Any]) -> None:
    markdown, data = expected_outputs(report)
    runs_dir = root / str(report["runs_path"])
    runs_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.joinpath("INDEX.md").write_text(markdown, encoding="utf-8", newline="\n")
    runs_dir.joinpath("index.json").write_text(data, encoding="utf-8", newline="\n")


def check_outputs(root: Path, report: dict[str, Any]) -> list[str]:
    runs_dir = root / str(report["runs_path"])
    if not runs_dir.exists():
        return []
    if not report.get("runs"):
        retained = [
            path
            for path in runs_dir.iterdir()
            if path.name not in {"README.md", "INDEX.md", "index.json"}
        ]
        if not retained:
            return []
    markdown, data = expected_outputs(report)
    stale: list[str] = []
    for name, expected in {"INDEX.md": markdown, "index.json": data}.items():
        path = runs_dir / name
        if not path.exists():
            stale.append(common.relative(root, path))
            continue
        actual = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        if actual != expected:
            stale.append(common.relative(root, path))
    return stale


def run(args: Args, emit: bool = False) -> int:
    report = build_index(args.root, args.workflow_name)
    root = args.root.expanduser().resolve()
    runs_dir = root / str(report["runs_path"])
    runs_available = runs_dir.exists()
    stale: list[str] = []
    if args.check and runs_available:
        stale = check_outputs(root, report)
        if stale:
            report["ok"] = False
            report["status"] = "stale"
            report["checks"].append("existing run index files are stale")
    elif args.write and runs_available:
        write_outputs(root, report)
        report["checks"].append("wrote runs/INDEX.md and runs/index.json")

    if emit:
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_markdown(report))
        if stale:
            print("Stale outputs:")
            for path in stale:
                print(f"- {path}")
    return 1 if stale else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="repository root; defaults to script parent")
    parser.add_argument("--name", required=True, dest="workflow_name")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write runs/INDEX.md and runs/index.json when present")
    mode.add_argument("--check", action="store_true", help="fail when generated run indexes are stale")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    return parser


def default_root() -> Path:
    return Path(__file__).resolve().parents[4]


def main() -> int:
    common.require_supported_python()
    parsed = build_parser().parse_args()
    args = Args(
        root=Path(parsed.root).expanduser().resolve() if parsed.root else default_root(),
        workflow_name=parsed.workflow_name,
        write=parsed.write,
        check=parsed.check,
        output_format=parsed.output_format,
    )
    return run(args, emit=True)


if __name__ == "__main__":
    raise SystemExit(main())
