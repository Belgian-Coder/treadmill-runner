#!/usr/bin/env python3
"""Azure DevOps Mermaid syntax rules."""

from __future__ import annotations

import re

from mermaid_support.models import DiagramBlock, Finding

AZURE_OPEN_RE = re.compile(r"^\s*:::\s+mermaid\s*$", re.IGNORECASE)
AZURE_COMPACT_OPEN_RE = re.compile(r"^\s*:::mermaid\s*$", re.IGNORECASE)
AZURE_CLOSE_RE = re.compile(r"^\s*:::\s*$")
FENCED_OPEN_RE = re.compile(r"^\s*```\s*mermaid\s*$", re.IGNORECASE)
FENCED_CLOSE_RE = re.compile(r"^\s*```\s*$")
GENERIC_FENCE_RE = re.compile(r"^\s*```")
GRAPH_RE = re.compile(r"^graph\s+(TD|TB|LR|RL|BT);$")
SUPPORTED_DIAGRAM_TYPES = {
    "classDiagram",
    "erDiagram",
    "gantt",
    "gitGraph",
    "graph",
    "journey",
    "pie",
    "requirementDiagram",
    "sequenceDiagram",
    "stateDiagram",
    "stateDiagram-v2",
    "timeline",
}
SUPPORTED_DIAGRAM_SUMMARY = (
    "graph, sequenceDiagram, gantt, classDiagram, stateDiagram, "
    "stateDiagram-v2, journey, pie, requirementDiagram, gitGraph, "
    "erDiagram, timeline"
)
GRAPH_LABEL_RE = re.compile(
    r"\b(?P<node>[A-Za-z][A-Za-z0-9_]*)\s*"
    r"(?:\(\[(?P<stadium>[^\n]+?)\]\)|\[\((?P<cylinder>[^\n]+?)\)\]|\[\[(?P<subroutine>[^\n]+?)\]\]|"
    r"\{\{(?P<hexagon>[^\n]+?)\}\}|\[(?P<square>[^\]\n]+?)\]|"
    r"\((?P<round>[^\)\n]+?)\)|\{(?P<diamond>[^\}\n]+?)\})"
)
GRAPH_EDGE_RE = re.compile(
    r"^\s*(?P<left>[A-Za-z][A-Za-z0-9_]*)\b.*?(?:-->|---|-\.\->|==>)\s*"
    r"(?:\|[^|]*\|\s*)?(?P<right>[A-Za-z][A-Za-z0-9_]*)\b"
)
GRAPH_EDGE_OPERATOR_RE = re.compile(r"-->|---|-\.\->|==>")
SUBGRAPH_RE = re.compile(r"^\s*subgraph\s+(?P<body>.+?)\s*;?\s*$", re.IGNORECASE)
SUBGRAPH_END_RE = re.compile(r"^\s*end\s*;?\s*$", re.IGNORECASE)
IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
REQUIREMENT_ID_RE = re.compile(r"^\s*id:\s*(?P<value>.+?)\s*$", re.IGNORECASE)
UNQUOTED_LABEL_SPECIALS_RE = re.compile(r"[\[\]\(\)\{\}/|#&<>\"']")
ERROR_PATTERNS = {
    "flowchart syntax": re.compile(r"^\s*flowchart\b", re.IGNORECASE | re.MULTILINE),
    "Mermaid init/theme block": re.compile(r"%%\{"),
    "HTML label": re.compile(r"<[^>\n]+>"),
    "click callback": re.compile(r"^\s*click\s+", re.IGNORECASE | re.MULTILINE),
    "custom styling": re.compile(r"^\s*(classDef|style|linkStyle)\s+", re.IGNORECASE | re.MULTILINE),
    "Font Awesome icon syntax": re.compile(r"\bfa:fa-|fa-[a-z0-9-]+", re.IGNORECASE),
    "Azure-unsupported LongArrow": re.compile(r"-{4,}>"),
}


def first_content_line(body: str) -> str:
    line, _offset = first_content_line_info(body)
    return line


def first_content_line_info(body: str) -> tuple[str, int]:
    for offset, line in enumerate(body.splitlines(), start=1):
        stripped = line.strip()
        if stripped and not stripped.startswith("%%"):
            return stripped, offset
    return "", 0


def detect_diagram_type(block: DiagramBlock) -> tuple[str, list[Finding]]:
    first, offset = first_content_line_info(block.body)
    line = block.start_line + offset if offset else block.start_line
    if not first:
        return "", [Finding("error", block.path, block.start_line, "Mermaid block is empty.")]

    if GRAPH_RE.match(first):
        return "graph", []
    if re.match(r"^graph\b", first, re.IGNORECASE):
        return (
            "graph",
            [
                Finding(
                    "error",
                    block.path,
                    line,
                    "Graph diagrams must start with `graph <TD|TB|LR|RL|BT>;`.",
                )
            ],
        )

    keyword = first.split(maxsplit=1)[0].rstrip(";")
    if keyword in SUPPORTED_DIAGRAM_TYPES:
        return keyword, []

    return (
        keyword,
        [
            Finding(
                "error",
                block.path,
                line,
                "Unsupported Azure DevOps Mermaid diagram type. Supported types: "
                f"{SUPPORTED_DIAGRAM_SUMMARY}.",
            )
        ],
    )


def stripped_label(label: str) -> str:
    value = label.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'", "`"}:
        return value[1:-1].strip()
    return value


def is_quoted_label(label: str) -> bool:
    value = label.strip()
    return len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "`"}


def parse_subgraph_id(body: str) -> str:
    text = body.strip().rstrip(";").strip()
    if not text or text[0] in {"\"", "'", "`"}:
        return ""
    first_part = text.split(maxsplit=1)[0]
    first_part = re.split(r"[\[\(\{]", first_part, maxsplit=1)[0]
    return first_part if IDENTIFIER_RE.match(first_part) else ""


def validate_graph_subgraphs(block: DiagramBlock) -> tuple[list[Finding], set[str]]:
    errors: list[Finding] = []
    stack: list[tuple[str, int]] = []
    subgraph_ids: set[str] = set()

    for offset, line in enumerate(block.body.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("%%"):
            continue
        subgraph_match = SUBGRAPH_RE.match(line)
        if subgraph_match:
            raw_body = subgraph_match.group("body")
            subgraph_id = parse_subgraph_id(raw_body)
            if not subgraph_id and " " in stripped:
                errors.append(
                    Finding(
                        "error",
                        block.path,
                        block.start_line + offset,
                        "Subgraph titles with spaces need an explicit ASCII id, "
                        "for example `subgraph sourceSystems[\"Source Systems\"]`.",
                    )
                )
            if subgraph_id:
                subgraph_ids.add(subgraph_id)
            stack.append((subgraph_id, block.start_line + offset))
            continue
        if SUBGRAPH_END_RE.match(line):
            if stack:
                stack.pop()
            else:
                errors.append(
                    Finding(
                        "error",
                        block.path,
                        block.start_line + offset,
                        "Subgraph `end` has no matching `subgraph`.",
                    )
                )

    for _subgraph_id, line in stack:
        errors.append(
            Finding(
                "error",
                block.path,
                line,
                "Subgraph is missing a matching `end`.",
            )
        )
    return errors, subgraph_ids


def validate_graph_labels(block: DiagramBlock) -> list[Finding]:
    warnings: list[Finding] = []
    node_ids: set[str] = set()
    edge_count = 0

    for offset, line in enumerate(block.body.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("%%") or stripped.startswith("subgraph "):
            continue
        if GRAPH_EDGE_OPERATOR_RE.search(stripped):
            edge_count += len(GRAPH_EDGE_OPERATOR_RE.findall(stripped))

        for match in GRAPH_LABEL_RE.finditer(line):
            node_ids.add(match.group("node"))
            label = next(
                value
                for value in (
                    match.group("cylinder"),
                    match.group("stadium"),
                    match.group("subroutine"),
                    match.group("hexagon"),
                    match.group("square"),
                    match.group("round"),
                    match.group("diamond"),
                )
                if value is not None
            )
            visible = stripped_label(label)
            line_number = block.start_line + offset
            if visible.endswith("/"):
                warnings.append(
                    Finding(
                        "warning",
                        block.path,
                        line_number,
                        "Graph label ends with `/`; shorten it or reword it to avoid Mermaid parser drift.",
                    )
                )
            if not is_quoted_label(label) and UNQUOTED_LABEL_SPECIALS_RE.search(label):
                warnings.append(
                    Finding(
                        "warning",
                        block.path,
                        line_number,
                        "Graph labels with special characters should be quoted.",
                    )
                )
            if re.search(r"\bend\b", visible):
                warnings.append(
                    Finding(
                        "warning",
                        block.path,
                        line_number,
                        "Lowercase `end` in a graph label can break Mermaid parsing; use `End` or reword.",
                    )
                )
            if len(visible) > 42:
                warnings.append(
                    Finding(
                        "warning",
                        block.path,
                        line_number,
                        "Graph label is long; split the diagram or move detail into nearby prose.",
                    )
                )

    if len(node_ids) > 12 or edge_count > 18:
        warnings.append(
            Finding(
                "warning",
                block.path,
                block.start_line,
                "Large graph detected; split diagrams above roughly 12 nodes or 18 edges.",
            )
        )
    return warnings


def validate_graph_subgraph_edges(block: DiagramBlock, subgraph_ids: set[str]) -> list[Finding]:
    errors: list[Finding] = []
    if not subgraph_ids:
        return errors
    for offset, line in enumerate(block.body.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("%%", "subgraph ")) or SUBGRAPH_END_RE.match(line):
            continue
        match = GRAPH_EDGE_RE.match(line)
        if not match:
            continue
        endpoints = {match.group("left"), match.group("right")}
        linked_subgraphs = sorted(endpoints & subgraph_ids)
        if linked_subgraphs:
            errors.append(
                Finding(
                    "error",
                    block.path,
                    block.start_line + offset,
                    "Azure DevOps does not support links to or from subgraph ids; "
                    f"link real nodes inside the subgraph instead: {', '.join(linked_subgraphs)}.",
                )
            )
    return errors


def validate_requirement_ids(block: DiagramBlock) -> list[Finding]:
    errors: list[Finding] = []
    for offset, line in enumerate(block.body.splitlines(), start=1):
        match = REQUIREMENT_ID_RE.match(line)
        if not match:
            continue
        value = match.group("value").strip()
        if "-" not in value or is_quoted_label(value):
            continue
        errors.append(
            Finding(
                "error",
                block.path,
                block.start_line + offset,
                "Hyphenated requirement id values must be quoted for Mermaid CLI rendering.",
            )
        )
    return errors


def validate_azure_indentation(block: DiagramBlock) -> list[Finding]:
    if block.wrapper != "azure":
        return []
    findings: list[Finding] = []
    for offset, line in enumerate(block.raw_body.splitlines(), start=1):
        if not line.strip():
            continue
        if not line.startswith(("    ", "\t")):
            findings.append(
                Finding(
                    "error",
                    block.path,
                    block.start_line + offset,
                    "Azure Mermaid block body lines must be indented.",
                )
            )
            break
    return findings


def validate_azure_wrapper(block: DiagramBlock) -> list[Finding]:
    if block.wrapper != "azure":
        return []
    if AZURE_OPEN_RE.match(block.opening):
        return []
    return [
        Finding(
            "warning",
            block.path,
            block.start_line,
            "Preferred Azure Mermaid wrapper style is `::: mermaid`; compact `:::mermaid` "
            "is accepted for Azure compatibility.",
        )
    ]


def validate_body(block: DiagramBlock) -> tuple[list[Finding], list[Finding]]:
    errors: list[Finding] = []
    warnings: list[Finding] = []
    body = block.body
    diagram_type, diagram_errors = detect_diagram_type(block)
    errors.extend(diagram_errors)

    for label, pattern in ERROR_PATTERNS.items():
        match = pattern.search(body)
        if match:
            line = block.start_line + body[: match.start()].count("\n") + 1
            errors.append(Finding("error", block.path, line, f"Unsupported portable Mermaid syntax: {label}."))

    if diagram_type == "graph":
        subgraph_errors, subgraph_ids = validate_graph_subgraphs(block)
        errors.extend(subgraph_errors)
        errors.extend(validate_graph_subgraph_edges(block, subgraph_ids))
        warnings.extend(validate_graph_labels(block))
        for offset, line in enumerate(body.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("%%"):
                continue
            if stripped.startswith("subgraph ") or SUBGRAPH_END_RE.match(line):
                continue
            if not stripped.endswith(";"):
                warnings.append(
                    Finding(
                        "warning",
                        block.path,
                        block.start_line + offset,
                        "Graph statements should end with semicolons.",
                    )
                )

    if diagram_type == "requirementDiagram":
        errors.extend(validate_requirement_ids(block))

    if block.wrapper == "fenced":
        errors.append(
            Finding(
                "error",
                block.path,
                block.start_line,
                "Fenced Mermaid blocks are outside this Azure DevOps skill scope; use `::: mermaid` blocks.",
            )
        )
    warnings.extend(validate_azure_wrapper(block))
    errors.extend(validate_azure_indentation(block))
    return errors, warnings
