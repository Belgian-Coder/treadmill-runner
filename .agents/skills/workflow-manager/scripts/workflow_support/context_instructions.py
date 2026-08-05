"""Instruction-section extraction for workflow context packets."""

from __future__ import annotations

import hashlib
from pathlib import Path

import workflow_manager_common as common
from workflow_support.context_markdown import (
    compact_markdown_snippet,
    current_phase_instruction_section,
    first_markdown_section,
    normalize_heading,
)
from workflow_support.context_paths import TERMINAL_PHASES, read_optional_text


def build_instruction_context(root: Path, module_dir: Path, run_packet: dict[str, object]) -> dict[str, object]:
    instructions = module_dir / "instructions.md"
    if not instructions.exists():
        return {
            "status": "missing",
            "path": common.relative(root, instructions),
            "instructions_sha256": "",
            "always_load": "",
            "stop_rules": "",
            "completion_contract": "",
            "current_phase": str(run_packet.get("current_phase") or ""),
            "current_phase_instructions": "",
            "requires_full_instructions": False,
            "issues": ["instructions.md is missing"],
        }
    text = read_optional_text(instructions, limit=120_000)
    data = instructions.read_bytes()
    phase = run_packet.get("phase") if isinstance(run_packet.get("phase"), dict) else {}
    current_phase = str(run_packet.get("current_phase") or phase.get("current") or "")
    always_load = first_markdown_section(text, ("Always Load", "Always Use", "Global Instructions"))
    stop_rules = first_markdown_section(text, ("Stop Rules", "Stop/Fallback Rules"))
    completion_contract = first_markdown_section(text, ("Completion Contract",))
    current_phase_instructions = current_phase_instruction_section(text, current_phase)
    issues: list[str] = []
    if current_phase and normalize_heading(current_phase) not in TERMINAL_PHASES and not current_phase_instructions:
        issues.append("current phase section is missing")
    structured_sections_present = bool(always_load or stop_rules or completion_contract or current_phase_instructions)
    if not structured_sections_present:
        issues.append("structured instruction sections are missing")
    requires_full_instructions = bool(issues)
    return {
        "status": "needs-full-instructions" if requires_full_instructions else "ok",
        "path": common.relative(root, instructions),
        "instructions_sha256": hashlib.sha256(data).hexdigest(),
        "always_load": compact_markdown_snippet(always_load, limit_chars=500),
        "stop_rules": compact_markdown_snippet(stop_rules, limit_chars=500),
        "completion_contract": compact_markdown_snippet(completion_contract, limit_chars=500),
        "current_phase": current_phase,
        "current_phase_instructions": compact_markdown_snippet(current_phase_instructions, limit_chars=900),
        "requires_full_instructions": requires_full_instructions,
        "issues": issues,
    }
