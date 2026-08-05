#!/usr/bin/env python3

import json
from pathlib import Path


def validate_report_shape(tools, report):
    missing = [field for field in tools.REQUIRED_REPORT_FIELDS if field not in report]
    issues = []
    if missing:
        issues.append("missing required report fields: " + ", ".join(missing))
    for key in ["capabilities", "evidence", "findings", "warnings", "skipped", "issues", "artifacts"]:
        expected = dict if key == "capabilities" else list
        if key in report and not isinstance(report[key], expected):
            issues.append(f"field has wrong type: {key}")
    return issues


def append_shape_validation(tools, report):
    issues = validate_report_shape(tools, report)
    report["checks"].append({"name": "evidence-schema-shape", "ok": not issues})
    report["issues"].extend(issues)


def enforce_output_path(tools, report, input_path, output_path, force, purpose):
    if output_path is None:
        return True
    try:
        resolved_output = output_path.resolve()
    except OSError as exc:
        report["blocked"].append(f"{purpose} output path could not be resolved: {exc}")
        return False
    if input_path is not None and input_path.exists():
        resolved_input = input_path.resolve()
        if resolved_output == resolved_input or resolved_output.is_relative_to(resolved_input):
            report["blocked"].append(f"{purpose} output path must not be the input file or inside the input file path")
            return False
    if output_path.exists() and not force:
        report["blocked"].append(f"{purpose} output already exists; pass --force to overwrite: {tools.rel(output_path)}")
        return False
    return True


def enforce_output_dir(tools, report, input_paths, output_dir, purpose):
    if output_dir is None:
        return True
    try:
        resolved_output = output_dir.resolve()
    except OSError as exc:
        report["blocked"].append(f"{purpose} output directory could not be resolved: {exc}")
        return False
    for input_path in input_paths:
        if not input_path.exists():
            continue
        resolved_input = input_path.resolve()
        if resolved_output == resolved_input or resolved_output.is_relative_to(resolved_input):
            report["blocked"].append(f"{purpose} output directory must not be the input file or inside the input file path")
            return False
    if output_dir.exists() and not output_dir.is_dir():
        report["blocked"].append(f"{purpose} output directory is an existing file: {tools.rel(output_dir)}")
        return False
    return True


def report_digest(report):
    return {
        "command": report.get("command", ""),
        "ok": report.get("ok", False),
        "status": report.get("status", ""),
        "summary": report.get("summary", ""),
        "findings": report.get("findings", [])[:10],
        "warnings": report.get("warnings", [])[:10],
        "skipped": report.get("skipped", [])[:10],
        "issues": report.get("issues", [])[:10],
        "evidence_kinds": [item.get("kind", "") for item in report.get("evidence", []) if isinstance(item, dict)][:20],
    }


def write_report_file(tools, report, output_path, force=False):
    if not enforce_output_path(tools, report, None, output_path, force, "evidence report"):
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return True


def next_safe_commands(tools, path, output_dir):
    input_arg = tools.rel(path)
    output_arg = tools.rel(output_dir)
    render_available = bool(tools.shutil.which("pdftoppm") or tools.shutil.which("mutool"))
    commands = [
        f"python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py inspect --file {input_arg} --json",
        f"python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py to-markdown --file {input_arg} --output {output_arg}/content.md --write --json",
        f"python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py extract-assets --file {input_arg} --output-dir {output_arg}/assets --write --json",
        "python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py compare --before <before.pdf> --after <after.pdf> --json",
    ]
    render_command = f"python -B .agents/skills/document-artifacts/scripts/pdf/pdf_tools.py render-pages --file {input_arg} --output-dir {output_arg}/pages --write --json"
    commands.append(render_command if render_available else f"{render_command}  # renderer unavailable; run doctor for capability details")
    return {"kind": "next-safe-commands", "render_available": render_available, "commands": commands}


def bundle_evidence(tools, path, output_dir, write, force=False):
    report = tools.base_report("bundle-evidence", path)
    if write and hasattr(tools, "enforce_output_dir") and not tools.enforce_output_dir(report, [path], output_dir, "PDF evidence bundle"):
        return tools.finish(report, False, "blocked", "PDF evidence bundle output directory failed safety checks.")
    reports = {
        "inspect": tools.inspect_pdf(path),
        "metadata": tools.pdf_metadata_report(path),
        "links": tools.pdf_links_report(path),
        "outline": tools.pdf_outline_report(path),
        "accessibility": tools.pdf_accessibility_report(path),
    }
    markdown_report = tools.to_markdown(path, output_dir / "content.md" if write else None, force=force, include_metadata=True, include_links=True, include_outline=True, include_assets=True)
    reports["to-markdown"] = markdown_report
    assets_report = tools.extract_assets(path, output_dir / "assets", write=write, force=force)
    reports["extract-assets"] = assets_report
    report["evidence"].append({"kind": "evidence-bundle", "reports": {name: report_digest(item) for name, item in reports.items()}})
    next_commands = next_safe_commands(tools, path, output_dir)
    report["evidence"].append(next_commands)
    report["findings"].extend(f"Next safe command: {command}" for command in next_commands["commands"])
    report["artifacts"].append({"kind": "evidence-bundle", "path": tools.rel(output_dir), "write_requested": write})
    report["warnings"].extend(item for nested in reports.values() for item in nested.get("warnings", []))
    report["skipped"].extend(item for nested in reports.values() for item in nested.get("skipped", []))
    if write:
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, nested in reports.items():
            target = output_dir / f"{name}.json"
            if not write_report_file(tools, nested, target, force):
                report["blocked"].extend(nested.get("blocked", []))
                return tools.finish(report, False, "blocked", "Evidence bundle report output path failed safety checks.")
            report["writes"].append(tools.rel(target))
        index = {
            "schema_version": 1,
            "tool": report["tool"],
            "command": report["command"],
            "input_path": report["input_path"],
            "input_sha256": report["input_sha256"],
            "reports": {name: f"{name}.json" for name in reports},
        }
        index_path = output_dir / "evidence-bundle.json"
        if not enforce_output_path(tools, report, None, index_path, force, "evidence bundle index"):
            return tools.finish(report, False, "blocked", "Evidence bundle index output path failed safety checks.")
        index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        report["writes"].append(tools.rel(index_path))
        report["artifacts"].append({"kind": "evidence-bundle-index", "path": tools.rel(index_path)})
    status = "passed" if write else "planned"
    return tools.finish(report, True, status, "PDF evidence bundle " + ("written." if write else "planned; no files were written."))


def batch_evidence(tools, paths, output_dir, write, force=False):
    report = tools.base_report("batch")
    if write and hasattr(tools, "enforce_output_dir") and not tools.enforce_output_dir(report, paths, output_dir, "PDF batch"):
        return tools.finish(report, False, "blocked", "PDF batch output directory failed safety checks.")
    summaries = []
    for path in paths:
        child_dir = output_dir / tools.sanitize_asset_name(path.stem, "pdf")
        child = bundle_evidence(tools, path, child_dir, write=write, force=force)
        summaries.append({"path": tools.rel(path), "output_dir": tools.rel(child_dir), "summary": report_digest(child)})
        report["warnings"].extend(child.get("warnings", []))
        report["skipped"].extend(child.get("skipped", []))
        if not child["ok"]:
            report["issues"].append(f"bundle failed for {tools.rel(path)}: {child['summary']}")
    report["evidence"].append({"kind": "batch-evidence", "inputs": [tools.rel(path) for path in paths], "items": summaries})
    if write:
        output_dir.mkdir(parents=True, exist_ok=True)
        index_path = output_dir / "batch-index.json"
        if not enforce_output_path(tools, report, None, index_path, force, "batch index"):
            return tools.finish(report, False, "blocked", "Batch index output path failed safety checks.")
        index_path.write_text(json.dumps({"schema_version": 1, "tool": report["tool"], "items": summaries}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        report["writes"].append(tools.rel(index_path))
        report["artifacts"].append({"kind": "batch-index", "path": tools.rel(index_path)})
    ok = not report["issues"]
    return tools.finish(report, ok, "passed" if write and ok else "planned" if ok else "failed", f"Processed {len(paths)} PDF file(s) for batch evidence.")
