#!/usr/bin/env python3
"""Capture Playwright screenshots and local LLM visual-analysis evidence."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_RESOLUTIONS: tuple[tuple[str, int, int], ...] = (
    ("desktop", 1440, 900),
    ("mobile", 390, 844),
)


def resolve_command(command: list[str]) -> list[str]:
    if not command:
        return command
    resolved = shutil.which(command[0])
    if not resolved:
        return command
    return [resolved, *command[1:]]


def command_result(command: list[str], cwd: Path, timeout_seconds: int) -> dict[str, Any]:
    started = time.perf_counter()
    resolved_command = resolve_command(command)
    try:
        completed = subprocess.run(
            resolved_command,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else ""
        return {
            "command": command,
            "resolved_command": resolved_command,
            "returncode": None,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "output": output[-4000:],
            "error": f"timed out after {timeout_seconds}s",
        }
    except OSError as exc:
        return {
            "command": command,
            "resolved_command": resolved_command,
            "returncode": None,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "output": "",
            "error": str(exc),
        }
    return {
        "command": command,
        "resolved_command": resolved_command,
        "returncode": completed.returncode,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "output": completed.stdout[-4000:],
    }


def parse_resolution(value: str) -> tuple[str, int, int]:
    if "=" not in value or "x" not in value:
        raise argparse.ArgumentTypeError("resolution must be name=WIDTHxHEIGHT")
    name, size = value.split("=", 1)
    width_text, height_text = size.lower().split("x", 1)
    try:
        width = int(width_text)
        height = int(height_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("resolution width and height must be integers") from exc
    if not name or width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("resolution name, width, and height must be positive")
    return name, width, height


def default_backend(project_root: Path) -> str:
    if (project_root / "node_modules" / "playwright").exists() or (project_root / "node_modules" / "@playwright").exists():
        return "node"
    result = command_result(["npx", "--no-install", "playwright", "--version"], project_root, 30)
    if result.get("returncode") == 0:
        return "node-cli"
    return "python"


def write_node_runner(validation_dir: Path) -> Path:
    runner = validation_dir / "generated" / "capture-playwright.cjs"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text(
        """const fs = require('fs');
const path = require('path');
const { createRequire } = require('module');

const input = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const projectRequire = createRequire(path.join(input.projectRoot, 'package.json'));
const { chromium } = projectRequire('playwright');

(async () => {
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({
      viewport: { width: input.width, height: input.height },
      isMobile: input.isMobile,
      deviceScaleFactor: input.isMobile ? 3 : 1
    });
    const page = await context.newPage();
    await page.goto(input.url, { waitUntil: 'networkidle', timeout: input.timeoutMs });
    await page.screenshot({ path: input.path, fullPage: true });
    await context.close();
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
""",
        encoding="utf-8",
        newline="\n",
    )
    return runner


def capture_with_python(*, url: str, capture: dict[str, object], timeout_seconds: int) -> dict[str, object]:
    started = time.perf_counter()
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {
            "command": [sys.executable, "-m", "playwright"],
            "returncode": None,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "output": "",
            "error": f"Python Playwright is not importable: {exc}",
        }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(
                viewport={"width": int(capture["width"]), "height": int(capture["height"])},
                is_mobile=bool(capture["is_mobile"]),
                device_scale_factor=3 if capture["is_mobile"] else 1,
            )
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout_seconds * 1000)
            page.screenshot(path=str(capture["path"]), full_page=True)
            context.close()
            browser.close()
    except Exception as exc:
        return {
            "command": [sys.executable, "-m", "playwright", "screenshot"],
            "returncode": 1,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "output": str(exc)[-4000:],
        }
    return {
        "command": [sys.executable, "-m", "playwright", "screenshot"],
        "returncode": 0,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "output": "",
    }


def capture_one(
    *,
    backend: str,
    url: str,
    capture: dict[str, object],
    project_root: Path,
    timeout_seconds: int,
) -> dict[str, object]:
    if backend == "python":
        return capture_with_python(url=url, capture=capture, timeout_seconds=timeout_seconds)
    if backend == "node":
        validation_dir = Path(str(capture["path"])).parents[1]
        runner = write_node_runner(validation_dir)
        input_path = validation_dir / "generated" / f"{capture['name']}-capture.json"
        input_path.write_text(
            json.dumps(
                {
                    "url": url,
                    "path": str(capture["path"]),
                    "width": capture["width"],
                    "height": capture["height"],
                    "isMobile": capture["is_mobile"],
                    "projectRoot": str(project_root),
                    "timeoutMs": timeout_seconds * 1000,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return command_result(["node", str(runner), str(input_path)], project_root, timeout_seconds)
    if backend == "node-cli":
        return command_result(
            [
                "npx",
                "--no-install",
                "playwright",
                "screenshot",
                "--full-page",
                "--timeout",
                str(timeout_seconds * 1000),
                "--viewport-size",
                f"{capture['width']},{capture['height']}",
                url,
                str(capture["path"]),
            ],
            project_root,
            timeout_seconds,
        )
    return {
        "command": [],
        "returncode": None,
        "duration_seconds": 0.0,
        "output": "",
        "error": f"unsupported backend: {backend}",
    }


def write_llm_prompt(*, validation_dir: Path, url: str, captures: list[dict[str, object]]) -> Path:
    prompt_path = validation_dir / "llm-analysis-prompt.md"
    lines = [
        "# Playwright Screenshot Validation Review",
        "",
        f"Target URL: {url}",
        "",
        "Review the attached screenshots for rendering, layout, readability, overlap, responsive behavior, obvious console-visible failure states, blank screens, and viewport-specific regressions. Return concise findings with severity and screenshot evidence.",
        "",
        "## Screenshots",
        "",
    ]
    for capture in captures:
        lines.append(f"- {capture['name']}: `{capture['path']}` ({capture['width']}x{capture['height']})")
    prompt_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return prompt_path


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[4]


def run_llm_analysis(*, prompt_path: Path, screenshots: list[Path], validation_dir: Path, timeout_seconds: int) -> dict[str, object]:
    results: list[dict[str, object]] = []
    repo_root = repo_root_from_script()
    manage = repo_root / ".agents" / "manage.py"
    if not manage.exists():
        return {
            "ok": False,
            "status": "skipped",
            "reason": ".agents/manage.py was not found for local AI vision analysis",
            "prompt_path": str(prompt_path),
            "screenshots": [str(path) for path in screenshots],
        }
    for image in screenshots:
        command = [sys.executable, "-B", str(manage), "local-ai", "vision", "describe", "--image", str(image)]
        result = command_result(command, repo_root, timeout_seconds)
        result["image"] = str(image)
        results.append(result)
    analysis_path = validation_dir / "llm-analysis.md"
    lines = [
        "# Local LLM Screenshot Analysis",
        "",
        f"Prompt: `{prompt_path}`",
        "",
    ]
    ok = True
    for result in results:
        image_ok = result.get("returncode") == 0
        ok = ok and image_ok
        lines.append(f"## {result['image']}")
        lines.append("")
        if image_ok:
            lines.append(str(result.get("output", "")).strip() or "No output.")
        else:
            lines.append(f"Local AI vision failed or was unavailable: {result.get('error') or result.get('output') or 'unknown error'}")
        lines.append("")
    analysis_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return {
        "ok": ok,
        "status": "passed" if ok else "failed",
        "path": str(analysis_path),
        "prompt_path": str(prompt_path),
        "commands": results,
        "screenshots": [str(path) for path in screenshots],
    }


def build_report(
    *,
    project_root: Path,
    validation_dir: Path,
    url: str,
    backend: str = "auto",
    resolutions: list[tuple[str, int, int]] | None = None,
    skip_llm_analysis: bool = False,
    accepted_dir: Path | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    evidence_dir = validation_dir.expanduser().resolve()
    screenshots_dir = evidence_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    selected_backend = default_backend(root) if backend == "auto" else backend
    captures: list[dict[str, object]] = []
    capture_commands: list[dict[str, object]] = []
    blocked: list[str] = []
    skipped: list[str] = []

    for name, width, height in resolutions or list(DEFAULT_RESOLUTIONS):
        path = screenshots_dir / f"{name}-{width}x{height}.png"
        captures.append({"name": name, "width": width, "height": height, "is_mobile": width < 600, "path": str(path)})

    for capture in captures:
        result = capture_one(
            backend=selected_backend,
            url=url,
            capture=capture,
            project_root=root,
            timeout_seconds=timeout_seconds,
        )
        capture_commands.append(result)
        if result.get("returncode") != 0:
            blocked.append(f"screenshot capture failed for {capture['name']}")

    prompt_path = write_llm_prompt(validation_dir=evidence_dir, url=url, captures=captures)
    screenshots = [Path(str(capture["path"])) for capture in captures if Path(str(capture["path"])).exists()]
    if skip_llm_analysis:
        llm_analysis: dict[str, object] = {
            "ok": False,
            "status": "skipped",
            "reason": "LLM analysis skipped by --skip-llm-analysis",
            "prompt_path": str(prompt_path),
            "screenshots": [str(path) for path in screenshots],
        }
        skipped.append("LLM screenshot analysis skipped by request")
    elif screenshots:
        llm_analysis = run_llm_analysis(
            prompt_path=prompt_path,
            screenshots=screenshots,
            validation_dir=evidence_dir,
            timeout_seconds=timeout_seconds,
        )
        if llm_analysis.get("status") != "passed":
            skipped.append("local LLM screenshot analysis was unavailable or failed; prompt packet was saved")
    else:
        llm_analysis = {
            "ok": False,
            "status": "skipped",
            "reason": "no screenshots were captured",
            "prompt_path": str(prompt_path),
            "screenshots": [],
        }
        skipped.append("LLM screenshot analysis skipped because no screenshots were captured")

    command_failures = [item for item in capture_commands if item.get("returncode") != 0]
    ok = not command_failures and bool(screenshots)
    accepted: dict[str, object] = {
        "status": "skipped",
        "reason": "no accepted screenshot directory requested",
        "directory": "",
        "manifest_path": "",
        "screenshots": [],
    }
    if accepted_dir is not None:
        accepted_root = accepted_dir.expanduser().resolve()
        accepted_files: list[dict[str, object]] = []
        if ok:
            accepted_root.mkdir(parents=True, exist_ok=True)
            for capture in captures:
                source = Path(str(capture["path"]))
                if not source.exists():
                    continue
                target = accepted_root / source.name
                shutil.copyfile(source, target)
                accepted_files.append(
                    {
                        "name": capture["name"],
                        "width": capture["width"],
                        "height": capture["height"],
                        "source": str(source),
                        "path": str(target),
                    }
                )
            manifest_path = accepted_root / "accepted-screenshots.json"
            write_json(
                manifest_path,
                {
                    "schema_version": 1,
                    "tool": "playwright-integration.accepted-screenshots",
                    "status": "accepted",
                    "source_validation_dir": str(evidence_dir),
                    "url": url,
                    "screenshots": accepted_files,
                },
            )
            accepted = {
                "status": "accepted",
                "directory": str(accepted_root),
                "manifest_path": str(manifest_path),
                "screenshots": accepted_files,
            }
        else:
            accepted = {
                "status": "blocked",
                "reason": "accepted screenshots are only written after all required captures succeed",
                "directory": str(accepted_root),
                "manifest_path": "",
                "screenshots": [],
            }
    report = {
        "schema_version": 1,
        "tool": "playwright-integration.screenshot-validation",
        "ok": ok,
        "status": "passed" if ok else "blocked",
        "project_root": str(root),
        "validation_dir": str(evidence_dir),
        "url": url,
        "backend": selected_backend,
        "resolutions": [{"name": name, "width": width, "height": height} for name, width, height in resolutions or list(DEFAULT_RESOLUTIONS)],
        "captures": captures,
        "commands": capture_commands,
        "llm_analysis": llm_analysis,
        "accepted_screenshots": accepted,
        "skipped": skipped,
        "blocked": blocked,
    }
    write_json(evidence_dir / "playwright-screenshot-validation.json", report)
    return report


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="write/capture: capture Playwright screenshots and local LLM visual-analysis evidence")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--validation-dir", required=True, help="write workflow-owned validation folder for screenshots and LLM analysis")
    parser.add_argument("--url", required=True, help="running local app URL to validate")
    parser.add_argument("--backend", choices=["auto", "node", "node-cli", "python"], default="auto")
    parser.add_argument("--resolution", action="append", type=parse_resolution, help="named viewport such as desktop=1440x900; repeatable")
    parser.add_argument("--skip-llm-analysis", action="store_true", help="capture screenshots but only write the LLM prompt packet")
    parser.add_argument("--accepted-dir", help="write optional committable folder for accepted end-result screenshots")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(
        project_root=Path(args.project_root),
        validation_dir=Path(args.validation_dir),
        url=args.url,
        backend=args.backend,
        resolutions=args.resolution,
        skip_llm_analysis=args.skip_llm_analysis,
        accepted_dir=Path(args.accepted_dir) if args.accepted_dir else None,
        timeout_seconds=args.timeout_seconds,
    )
    print(f"Playwright screenshot validation: {report['status']}")
    print(f"- validation dir: {report['validation_dir']}")
    for capture in report["captures"]:
        print(f"- {capture['name']}: {capture['path']}")
    for item in report["blocked"]:
        print(f"- blocked: {item}")
    for item in report["skipped"]:
        print(f"- skipped: {item}")
    accepted = report.get("accepted_screenshots", {})
    if isinstance(accepted, dict) and accepted.get("status") == "accepted":
        print(f"- accepted screenshots: {accepted.get('directory')}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
