"""Markdown extraction helpers for workflow context packets."""

from __future__ import annotations

import re


def markdown_section(text: str, heading: str) -> str:
    target = heading.strip().lower()
    lines = text.splitlines()
    selected: list[str] = []
    active = False
    active_level = 0
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip().lower()
            if active and level <= active_level:
                break
            if title == target:
                active = True
                active_level = level
                continue
        if active:
            selected.append(line)
    return "\n".join(selected).strip()


def markdown_sections(text: str) -> list[dict[str, object]]:
    lines = text.splitlines()
    sections: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            current = {
                "level": len(match.group(1)),
                "title": match.group(2).strip(),
                "lines": [],
            }
            sections.append(current)
            continue
        if current is not None:
            current_lines = current.get("lines")
            if isinstance(current_lines, list):
                current_lines.append(line)
    return sections


def normalize_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def phase_heading_matches(title: str, current_phase: str) -> bool:
    title_normalized = normalize_heading(title.replace(":", " "))
    phase_normalized = normalize_heading(current_phase)
    if not phase_normalized:
        return False
    wanted = f"phase-{phase_normalized}"
    if title_normalized == wanted or title_normalized.startswith(f"{wanted}-"):
        return True
    title_tokens = set(title_normalized.removeprefix("phase-").split("-"))
    phase_tokens = set(phase_normalized.split("-"))
    return bool(phase_tokens) and phase_tokens.issubset(title_tokens)


def first_markdown_section(text: str, headings: tuple[str, ...]) -> str:
    for heading in headings:
        section = markdown_section(text, heading)
        if section:
            return section
    return ""


def list_items(text: str, *, limit: int = 12) -> list[str]:
    items: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("-"):
            continue
        value = line.lstrip("-").strip()
        if value:
            items.append(value)
        if len(items) >= limit:
            break
    return items


def keyed_bullets(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("-") or ":" not in line:
            continue
        key, value = line.lstrip("-").split(":", 1)
        key = key.strip().lower()
        if key:
            values[key] = value.strip()
    return values


def compact_markdown_snippet(text: str, *, limit_chars: int = 1200) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    snippet = "\n".join(lines).strip()
    if len(snippet) <= limit_chars:
        return snippet
    return snippet[: max(0, limit_chars - 20)].rstrip() + "\n... [truncated]"


def current_phase_instruction_section(text: str, current_phase: str) -> str:
    if not current_phase:
        return ""
    for section in markdown_sections(text):
        title = str(section.get("title", ""))
        if phase_heading_matches(title, current_phase):
            lines = section.get("lines")
            return "\n".join(str(line) for line in lines).strip() if isinstance(lines, list) else ""
    return ""
