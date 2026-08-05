#!/usr/bin/env python3
"""Shared data models and constants for Mermaid validation."""

from __future__ import annotations

from dataclasses import dataclass, field

MIN_PYTHON = (3, 12)
MARKDOWN_SUFFIXES = {".md", ".markdown"}
MERMAID_SOURCE_SUFFIXES = {".mmd", ".mermaid"}
MERMAID_IMAGE_SUFFIXES = {".svg"}
SCAN_SUFFIXES = MARKDOWN_SUFFIXES | MERMAID_SOURCE_SUFFIXES
MMDC_RENDER_FLAGS = ["-t", "dark", "-b", "transparent"]


@dataclass
class DiagramBlock:
    path: str
    start_line: int
    end_line: int
    wrapper: str
    opening: str
    body: str
    raw_body: str


@dataclass
class Finding:
    severity: str
    path: str
    line: int
    message: str


@dataclass
class RenderResult:
    attempted: bool = False
    required: bool = False
    available: bool = False
    command: str = "mmdc"
    auto_install_requested: bool = False
    install_attempted: bool = False
    install_performed: bool = False
    node_version: str = ""
    installer: str = ""
    failures: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)


@dataclass
class MaterializedDiagramArtifact:
    markdown_path: str
    line: int
    image_path: str
    source_path: str
