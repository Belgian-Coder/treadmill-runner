#!/usr/bin/env python3
"""Mermaid CLI setup and render validation."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import re
from pathlib import Path

from mermaid_support.models import DiagramBlock, Finding, MMDC_RENDER_FLAGS, RenderResult

NODE_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def command_output(command: list[str], timeout: int = 15) -> tuple[int, str]:
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    return completed.returncode, completed.stdout.strip()


def parse_node_version(text: str) -> tuple[int, int, int] | None:
    match = NODE_VERSION_RE.search(text)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def node_version_is_compatible(version: tuple[int, int, int]) -> bool:
    major, minor, patch = version
    return major >= 20 or (major == 18 and (minor, patch) >= (19, 0))


def add_setup_finding(result: RenderResult, message: str) -> None:
    finding = Finding("error" if result.required else "warning", "<render>", 0, message)
    if result.required:
        result.failures.append(finding)
    else:
        result.warnings.append(finding)


def install_mmdc(result: RenderResult) -> None:
    if result.command != "mmdc":
        add_setup_finding(
            result,
            "Automatic Mermaid CLI setup only supports the default `mmdc` command name.",
        )
        return

    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        add_setup_finding(
            result,
            "Cannot install Mermaid CLI automatically because node and npm were not both found on PATH.",
        )
        return

    status, version_text = command_output([node, "--version"])
    version = parse_node_version(version_text)
    result.node_version = version_text
    if status != 0 or version is None:
        add_setup_finding(result, "Could not determine the local Node.js version.")
        return
    if not node_version_is_compatible(version):
        add_setup_finding(
            result,
            "Mermaid CLI requires Node.js ^18.19 or >=20.0; "
            f"found {version_text}.",
        )
        return

    result.install_attempted = True
    result.installer = "npm install -g @mermaid-js/mermaid-cli"
    completed = subprocess.run(
        [npm, "install", "-g", "@mermaid-js/mermaid-cli"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        detail = completed.stdout.strip() or "npm reported an install failure"
        if len(detail) > 240:
            detail = detail[:237].rstrip() + "..."
        add_setup_finding(result, f"Mermaid CLI install failed: {detail}")
        return
    result.install_performed = True


def render_blocks(
    blocks: list[DiagramBlock],
    *,
    command: str,
    required: bool,
    auto_install_mmdc: bool = False,
) -> RenderResult:
    result = RenderResult(
        attempted=True,
        required=required,
        command=command,
        auto_install_requested=auto_install_mmdc,
    )
    executable = shutil.which(command)
    if not executable and auto_install_mmdc:
        install_mmdc(result)
        executable = shutil.which(command)

    if not executable:
        setup_note = (
            "Automatic setup was attempted or checked. "
            if auto_install_mmdc
            else "Automatic setup was disabled. "
        )
        message = (
            f"Mermaid CLI command `{command}` was not found. "
            f"{setup_note}"
            "Install Mermaid CLI manually, ensure compatible Node/npm are on PATH, "
            "or rerun without --require-render."
        )
        finding = Finding("error" if required else "warning", "<render>", 0, message)
        if required:
            result.failures.append(finding)
        else:
            result.warnings.append(finding)
        return result

    result.available = True
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryDirectory(prefix="mermaid-render-") as temp_name:
        temp_root = Path(temp_name)
        for index, block in enumerate(blocks, start=1):
            source = temp_root / f"diagram-{index}.mmd"
            output = temp_root / f"diagram-{index}.svg"
            source.write_text(block.body, encoding="utf-8", newline="\n")
            completed = subprocess.run(
                [executable, "-i", str(source), "-o", str(output), *MMDC_RENDER_FLAGS],
                check=False,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=45,
            )
            if completed.returncode == 0 and output.exists():
                continue
            detail = (completed.stderr or completed.stdout or "render failed").strip()
            if len(detail) > 240:
                detail = detail[:237].rstrip() + "..."
            result.failures.append(
                Finding(
                    "error",
                    block.path,
                    block.start_line,
                    f"Mermaid render failed with `{command}`: {detail}",
                )
            )
    return result
