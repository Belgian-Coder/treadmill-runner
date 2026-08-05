#!/usr/bin/env python3
"""Best-effort VS Code Mermaid Markdown preview setup."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

sys.dont_write_bytecode = True

CLI_CANDIDATES = (
    "code.cmd",
    "code",
    "code-insiders.cmd",
    "code-insiders",
    "codium.cmd",
    "codium",
)
VISUAL_STUDIO_CANDIDATES = ("devenv.com", "devenv.exe", "devenv")
RIDER_CANDIDATES = ("rider", "rider.cmd", "rider64.exe", "rider.bat", "rider.sh")
RECOMMENDED_EXTENSION = "bierner.markdown-mermaid"
KNOWN_CONFLICTS = {
    "mermaidchart.vscode-mermaid-chart": (
        "Mermaid Chart can also process Mermaid preview blocks and may conflict "
        "with Markdown Preview Mermaid Support."
    )
}


@dataclass(frozen=True)
class CliCandidate:
    command: str
    path: str


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def normalize_extension_id(line: str) -> tuple[str, str | None]:
    raw = line.strip()
    if not raw:
        return "", None
    extension_id, _, version = raw.partition("@")
    return extension_id.lower(), version or None


def list_cli_candidates() -> list[CliCandidate]:
    seen: set[str] = set()
    candidates: list[CliCandidate] = []
    for command in CLI_CANDIDATES:
        resolved = shutil.which(command)
        if not resolved:
            continue
        key = resolved.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(CliCandidate(command=command, path=resolved))
    return candidates


def list_visual_studio_candidates() -> list[str]:
    return list_resolved_commands(VISUAL_STUDIO_CANDIDATES)


def list_rider_candidates() -> list[str]:
    return list_resolved_commands(RIDER_CANDIDATES)


def list_resolved_commands(commands: tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    candidates: list[str] = []
    for command in commands:
        resolved = shutil.which(command)
        if not resolved:
            continue
        key = resolved.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(resolved)
    return candidates


def command_summary(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr or result.stdout or "").strip().splitlines()
    return text[0] if text else f"exit code {result.returncode}"


def probe_cli(candidate: CliCandidate) -> dict[str, Any]:
    version = run_command([candidate.path, "--version"])
    if version.returncode != 0:
        return {
            "usable": False,
            "candidate": candidate.__dict__,
            "reason": f"--version failed: {command_summary(version)}",
        }

    extensions = run_command([candidate.path, "--list-extensions", "--show-versions"])
    if extensions.returncode != 0:
        return {
            "usable": False,
            "candidate": candidate.__dict__,
            "reason": f"--list-extensions failed: {command_summary(extensions)}",
        }

    version_lines = [line for line in version.stdout.splitlines() if line.strip()]
    return {
        "usable": True,
        "candidate": candidate.__dict__,
        "version": version_lines[0] if version_lines else "unknown",
        "version_output": version.stdout.strip(),
        "extensions_output": extensions.stdout,
    }


def extension_inventory(output: str) -> dict[str, str | None]:
    inventory: dict[str, str | None] = {}
    for line in output.splitlines():
        extension_id, version = normalize_extension_id(line)
        if extension_id:
            inventory[extension_id] = version
    return inventory


def preview_like_extensions(inventory: dict[str, str | None]) -> list[dict[str, str | None]]:
    findings: list[dict[str, str | None]] = []
    for extension_id, version in sorted(inventory.items()):
        if extension_id in {RECOMMENDED_EXTENSION, *KNOWN_CONFLICTS}:
            continue
        looks_related = "mermaid" in extension_id or (
            "markdown" in extension_id and "preview" in extension_id
        )
        if looks_related:
            findings.append({"id": extension_id, "version": version})
    return findings


def setup_vscode_preview(auto_install: bool) -> dict[str, Any]:
    report: dict[str, Any] = {
        "valid": True,
        "recommended_extension": RECOMMENDED_EXTENSION,
        "cli": None,
        "cli_candidates": [],
        "extensions": [],
        "recommended_installed": False,
        "install_attempted": False,
        "install_succeeded": False,
        "install_verified": False,
        "skipped": False,
        "skip_reason": None,
        "visual_studio_detected": False,
        "visual_studio_candidates": [],
        "rider_detected": False,
        "rider_candidates": [],
        "conflicts": [],
        "warnings": [],
        "errors": [],
        "ide_detected": None,
        "supported": False,
        "actions_attempted": [],
        "can_continue": True,
    }

    selected: dict[str, Any] | None = None
    for candidate in list_cli_candidates():
        probe = probe_cli(candidate)
        report["cli_candidates"].append(probe)
        if probe["usable"] and selected is None:
            selected = probe
            break

    if selected is None:
        visual_studio = list_visual_studio_candidates()
        rider = list_rider_candidates()
        report["visual_studio_detected"] = bool(visual_studio)
        report["visual_studio_candidates"] = visual_studio
        report["rider_detected"] = bool(rider)
        report["rider_candidates"] = rider
        report["skipped"] = True
        if visual_studio and rider:
            report["ide_detected"] = "visual-studio,rider"
            report["skip_reason"] = (
                "Visual Studio and Rider were detected, but VS Code Markdown preview "
                "extension setup is not applicable to those IDEs."
            )
        elif visual_studio:
            report["ide_detected"] = "visual-studio"
            report["skip_reason"] = (
                "Visual Studio was detected, but VS Code Markdown preview extension setup "
                "is not applicable to Visual Studio."
            )
        elif rider:
            report["ide_detected"] = "rider"
            report["skip_reason"] = (
                "Rider was detected, but VS Code Markdown preview extension setup "
                "is not applicable to Rider."
            )
        else:
            report["ide_detected"] = "none"
            report["skip_reason"] = (
                "No usable VS Code CLI was found, so VS Code Markdown preview setup was skipped."
            )
        report["warnings"].append(report["skip_reason"])
        return report

    report["cli"] = {
        "command": selected["candidate"]["command"],
        "path": selected["candidate"]["path"],
        "version": selected["version"],
    }
    report["ide_detected"] = "vs-code"
    report["supported"] = True

    inventory = extension_inventory(selected["extensions_output"])
    report["extensions"] = [
        {"id": extension_id, "version": version}
        for extension_id, version in sorted(inventory.items())
    ]

    report["recommended_installed"] = RECOMMENDED_EXTENSION in inventory
    report["conflicts"] = [
        {"id": extension_id, "version": inventory.get(extension_id), "message": message}
        for extension_id, message in KNOWN_CONFLICTS.items()
        if extension_id in inventory
    ]

    for extension in preview_like_extensions(inventory):
        report["warnings"].append(
            "Possible Markdown/Mermaid preview extension detected: "
            f"{extension['id']}. Verify only one extension renders Mermaid preview blocks."
        )

    for conflict in report["conflicts"]:
        report["warnings"].append(f"Known preview conflict: {conflict['id']}. {conflict['message']}")

    if auto_install and not report["recommended_installed"]:
        report["install_attempted"] = True
        report["actions_attempted"].append(f"install-extension:{RECOMMENDED_EXTENSION}")
        install = run_command(
            [
                selected["candidate"]["path"],
                "--install-extension",
                RECOMMENDED_EXTENSION,
            ]
        )
        if install.returncode != 0:
            report["valid"] = False
            report["errors"].append(
                "Recommended extension install failed: " + command_summary(install)
            )
            return report

        report["install_succeeded"] = True
        after = run_command(
            [selected["candidate"]["path"], "--list-extensions", "--show-versions"]
        )
        if after.returncode == 0:
            inventory = extension_inventory(after.stdout)
            report["recommended_installed"] = RECOMMENDED_EXTENSION in inventory
            report["install_verified"] = report["recommended_installed"]
            report["extensions"] = [
                {"id": extension_id, "version": version}
                for extension_id, version in sorted(inventory.items())
            ]

        if not report["install_verified"]:
            report["valid"] = False
            report["errors"].append(
                "Recommended extension install ran, but the extension could not be verified."
            )

    elif not report["recommended_installed"]:
        report["valid"] = False
        report["errors"].append(
            "Recommended extension is missing. Re-run with --auto-install to install it."
        )

    return report


def format_markdown(report: dict[str, Any]) -> str:
    lines = ["# VS Code Mermaid Preview Setup", ""]
    if report["cli"]:
        cli = report["cli"]
        lines.append(f"- VS Code CLI: `{cli['command']}` ({cli['version']})")
    else:
        lines.append("- VS Code CLI: not found")

    status = "installed" if report["recommended_installed"] else "missing"
    lines.append(f"- Recommended extension `{RECOMMENDED_EXTENSION}`: {status}")
    if report["install_attempted"]:
        install_status = "verified" if report["install_verified"] else "not verified"
        lines.append(f"- Auto-install: attempted, {install_status}")
    else:
        lines.append("- Auto-install: not attempted")

    if report["skipped"]:
        lines.append(f"- Setup skipped: {report['skip_reason']}")
    if report["visual_studio_detected"]:
        lines.append("- Visual Studio detected: yes; VS Code extension setup is not applicable")
    if report.get("rider_detected"):
        lines.append("- Rider detected: yes; VS Code extension setup is not applicable")

    if report["conflicts"]:
        lines.extend(["", "## Preview Conflicts"])
        for conflict in report["conflicts"]:
            version = f"@{conflict['version']}" if conflict.get("version") else ""
            lines.append(f"- `{conflict['id']}{version}`: {conflict['message']}")

    if report["warnings"]:
        lines.extend(["", "## Warnings"])
        for warning in report["warnings"]:
            lines.append(f"- {warning}")

    if report["errors"]:
        lines.extend(["", "## Setup Issues"])
        for error in report["errors"]:
            lines.append(f"- {error}")

    if not report["errors"] and not report["warnings"]:
        lines.extend(["", "No setup issues detected."])

    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Best-effort setup for Azure DevOps Mermaid Markdown preview in VS Code. "
            "Without --auto-install this is read-only inspection; --auto-install may install "
            "the recommended user-level VS Code extension. Setup issues are reported but never block workflow execution."
        )
    )
    parser.add_argument("--auto-install", action="store_true", help="install the recommended VS Code extension when missing")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument(
        "--non-blocking",
        action="store_true",
        help="accepted for workflow consistency; setup always reports evidence and exits 0",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = setup_vscode_preview(auto_install=args.auto_install)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
