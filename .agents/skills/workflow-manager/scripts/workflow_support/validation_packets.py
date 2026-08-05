"""Workflow-owned validation evidence packet checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import workflow_manager_common as common


PLAYWRIGHT_REPORT = "validation/playwright/playwright-screenshot-validation.json"
REQUIRED_VIEWPORTS = {"desktop", "mobile"}


def _read_json(path: Path) -> tuple[dict[str, Any], str]:
    data, error = common.read_json_file(path)
    if error:
        return {}, error
    if not isinstance(data, dict):
        return {}, "JSON root must be an object"
    return data, ""


def _safe_run_dir(root: Path, workflow_name: str, run_id: str) -> Path:
    if not common.SKILL_NAME_PATTERN.match(workflow_name):
        raise SystemExit("workflow name must use lowercase letters, digits, and hyphens")
    if not run_id or Path(run_id).is_absolute() or ".." in Path(run_id).parts:
        raise SystemExit("run id must be a safe workflow-local folder name")
    return root / "automations" / workflow_name / "runs" / run_id


def _path_from_report(root: Path, run_dir: Path, value: object) -> Path:
    text = str(value or "").strip()
    path = Path(text)
    if not text:
        return run_dir / "__missing__"
    if path.is_absolute():
        return path
    return run_dir / path


def _inside_validation(run_dir: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to((run_dir / "validation").resolve(strict=False))
        return True
    except ValueError:
        return False


def _validate_accepted_screenshots(
    root: Path,
    run_dir: Path,
    packet_report: dict[str, Any],
    issues: list[str],
    skipped: list[str],
) -> dict[str, Any]:
    accepted = packet_report.get("accepted_screenshots")
    if not isinstance(accepted, dict):
        skipped.append("accepted screenshot folder was not requested")
        return {"status": "skipped", "manifest_path": "", "screenshot_paths": []}

    status = str(accepted.get("status", "")).strip()
    if status in {"", "skipped"}:
        skipped.append("accepted screenshot folder was not requested")
        return {"status": status or "skipped", "manifest_path": "", "screenshot_paths": []}
    if status == "blocked":
        issues.append("accepted screenshot folder was requested but not written")
        return {"status": status, "manifest_path": "", "screenshot_paths": []}
    if status != "accepted":
        issues.append(f"accepted screenshot status is unsupported: {status}")
        return {"status": status, "manifest_path": "", "screenshot_paths": []}

    accepted_dir = _path_from_report(root, run_dir, accepted.get("directory"))
    manifest_path = _path_from_report(root, run_dir, accepted.get("manifest_path"))
    accepted_paths: list[str] = []
    accepted_viewports: set[str] = set()
    if not accepted_dir.exists() or not accepted_dir.is_dir():
        issues.append(f"accepted screenshot directory is missing: {common.relative(root, accepted_dir)}")
    elif _inside_validation(run_dir, accepted_dir):
        issues.append("accepted screenshot directory must be separate from the workflow run validation folder")
    if not manifest_path.exists():
        issues.append(f"accepted screenshot manifest is missing: {common.relative(root, manifest_path)}")
        manifest = {}
    elif _inside_validation(run_dir, manifest_path):
        issues.append("accepted screenshot manifest must be separate from the workflow run validation folder")
        manifest = {}
    else:
        manifest, error = _read_json(manifest_path)
        if error:
            issues.append(f"{common.relative(root, manifest_path)}: {error}")
            manifest = {}
        elif manifest.get("tool") != "playwright-integration.accepted-screenshots":
            issues.append(f"{common.relative(root, manifest_path)} tool must be playwright-integration.accepted-screenshots")
        elif manifest.get("status") != "accepted":
            issues.append(f"{common.relative(root, manifest_path)} status must be accepted")

    screenshots = accepted.get("screenshots") if isinstance(accepted.get("screenshots"), list) else []
    manifest_screenshots = manifest.get("screenshots") if isinstance(manifest.get("screenshots"), list) else []
    if not screenshots and manifest_screenshots:
        screenshots = manifest_screenshots
    for index, screenshot in enumerate(screenshots):
        if not isinstance(screenshot, dict):
            issues.append(f"accepted screenshot {index + 1} must be an object")
            continue
        name = str(screenshot.get("name", "")).strip()
        if name:
            accepted_viewports.add(name)
        path = _path_from_report(root, run_dir, screenshot.get("path"))
        accepted_paths.append(common.relative(root, path))
        if not path.exists():
            issues.append(f"accepted screenshot {name or index + 1} is missing: {common.relative(root, path)}")
        elif _inside_validation(run_dir, path):
            issues.append(f"accepted screenshot {name or index + 1} must be separate from the workflow run validation folder")
    missing_accepted = sorted(REQUIRED_VIEWPORTS - accepted_viewports)
    if missing_accepted:
        issues.append(f"accepted screenshots are missing required viewport(s): {', '.join(missing_accepted)}")
    return {
        "status": status,
        "directory": common.relative(root, accepted_dir),
        "manifest_path": common.relative(root, manifest_path),
        "screenshot_paths": accepted_paths,
    }


def validate_playwright_screenshot_packet(
    root: Path,
    run_dir: Path,
    *,
    require_llm_analysis: bool = False,
) -> dict[str, Any]:
    report_path = run_dir / PLAYWRIGHT_REPORT
    report, error = _read_json(report_path)
    issues: list[str] = []
    skipped: list[str] = []
    if error:
        issues.append(f"{common.relative(root, report_path)}: {error}")
        report = {}
    elif report.get("tool") != "playwright-integration.screenshot-validation":
        issues.append(f"{common.relative(root, report_path)} tool must be playwright-integration.screenshot-validation")
    if report and report.get("ok") is not True:
        issues.append(f"{common.relative(root, report_path)} status is not passed")

    captures = report.get("captures") if isinstance(report.get("captures"), list) else []
    viewport_names: set[str] = set()
    screenshot_paths: list[str] = []
    for index, capture in enumerate(captures):
        if not isinstance(capture, dict):
            issues.append(f"capture {index + 1} must be an object")
            continue
        name = str(capture.get("name", "")).strip()
        if name:
            viewport_names.add(name)
        path = _path_from_report(root, run_dir, capture.get("path"))
        screenshot_paths.append(common.relative(root, path))
        if not path.exists():
            issues.append(f"capture {name or index + 1} screenshot is missing: {common.relative(root, path)}")
        elif not _inside_validation(run_dir, path):
            issues.append(f"capture {name or index + 1} screenshot must stay under the workflow run validation folder")
        for field in ("width", "height"):
            value = capture.get(field)
            if not isinstance(value, int) or value <= 0:
                issues.append(f"capture {name or index + 1} {field} must be a positive integer")
    missing_viewports = sorted(REQUIRED_VIEWPORTS - viewport_names)
    if missing_viewports:
        issues.append(f"missing required viewport capture(s): {', '.join(missing_viewports)}")

    llm = report.get("llm_analysis") if isinstance(report.get("llm_analysis"), dict) else {}
    llm_status = str(llm.get("status", "")).strip()
    prompt_path = _path_from_report(root, run_dir, llm.get("prompt_path"))
    if not llm:
        issues.append("llm_analysis object is missing")
    elif llm_status == "passed":
        analysis_path = _path_from_report(root, run_dir, llm.get("path"))
        if not analysis_path.exists():
            issues.append(f"LLM analysis path is missing: {common.relative(root, analysis_path)}")
        elif not _inside_validation(run_dir, analysis_path):
            issues.append("LLM analysis path must stay under the workflow run validation folder")
    else:
        if require_llm_analysis:
            issues.append(f"LLM screenshot analysis is required but status is {llm_status or 'missing'}")
        elif prompt_path.exists():
            skipped.append(f"LLM analysis status was {llm_status or 'missing'}; prompt packet exists")
        else:
            issues.append(f"LLM analysis prompt is missing: {common.relative(root, prompt_path)}")

    accepted_info = _validate_accepted_screenshots(root, run_dir, report, issues, skipped) if report else {
        "status": "missing",
        "manifest_path": "",
        "screenshot_paths": [],
    }

    return {
        "schema_version": 1,
        "tool": "workflow-manager.validation-packet",
        "kind": "playwright-screenshots",
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "run_dir": common.relative(root, run_dir),
        "report_path": common.relative(root, report_path),
        "required_viewports": sorted(REQUIRED_VIEWPORTS),
        "captured_viewports": sorted(viewport_names),
        "screenshot_paths": screenshot_paths,
        "llm_analysis_status": llm_status,
        "accepted_screenshots": accepted_info,
        "issues": issues,
        "skipped": skipped,
        "next_command": "capture screenshots into validation/playwright and rerun validation-packet" if issues else "none",
    }


def validate_packet(
    root: Path,
    workflow_name: str,
    run_id: str,
    *,
    kind: str,
    require_llm_analysis: bool = False,
) -> dict[str, Any]:
    run_dir = _safe_run_dir(root, workflow_name, run_id)
    if not run_dir.exists():
        return {
            "schema_version": 1,
            "tool": "workflow-manager.validation-packet",
            "kind": kind,
            "ok": False,
            "status": "failed",
            "run_dir": common.relative(root, run_dir),
            "issues": [f"workflow run folder is missing: {common.relative(root, run_dir)}"],
            "skipped": [],
        }
    if kind == "playwright-screenshots":
        return validate_playwright_screenshot_packet(
            root,
            run_dir,
            require_llm_analysis=require_llm_analysis,
        )
    raise SystemExit(f"unknown validation packet kind: {kind}")


def render_validation_packet(report: dict[str, Any]) -> str:
    lines = ["# Workflow Validation Packet", ""]
    lines.append(f"- Kind: `{report.get('kind')}`")
    lines.append(f"- Status: {report.get('status')}")
    if report.get("run_dir"):
        lines.append(f"- Run: `{report.get('run_dir')}`")
    if report.get("report_path"):
        lines.append(f"- Report: `{report.get('report_path')}`")
    if report.get("captured_viewports"):
        lines.append(f"- Captured viewports: {', '.join(report.get('captured_viewports', []))}")
    if report.get("llm_analysis_status"):
        lines.append(f"- LLM analysis: {report.get('llm_analysis_status')}")
    accepted = report.get("accepted_screenshots")
    if isinstance(accepted, dict) and accepted.get("status"):
        lines.append(f"- Accepted screenshots: {accepted.get('status')}")
        if accepted.get("manifest_path"):
            lines.append(f"- Accepted manifest: `{accepted.get('manifest_path')}`")
    if report.get("issues"):
        lines.extend(["", "## Issues", ""])
        for issue in report.get("issues", []):
            lines.append(f"- {issue}")
    if report.get("skipped"):
        lines.extend(["", "## Skipped", ""])
        for item in report.get("skipped", []):
            lines.append(f"- {item}")
    lines.append(f"- Next command: `{report.get('next_command', 'none')}`")
    return "\n".join(lines) + "\n"
