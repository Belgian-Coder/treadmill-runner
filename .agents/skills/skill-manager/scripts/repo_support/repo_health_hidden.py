"""Hidden-character checks for accepted repository instruction surfaces."""

from __future__ import annotations

from pathlib import Path

from repo_support import repo_common as repo

HIDDEN_TEXT_CHARS = {
    "\u200b": "zero-width space",
    "\u200c": "zero-width non-joiner",
    "\u200d": "zero-width joiner",
    "\ufeff": "byte-order mark",
    "\u200e": "left-to-right mark",
    "\u200f": "right-to-left mark",
    "\u202a": "left-to-right embedding",
    "\u202b": "right-to-left embedding",
    "\u202c": "pop directional formatting",
    "\u202d": "left-to-right override",
    "\u202e": "right-to-left override",
    "\u2066": "left-to-right isolate",
    "\u2067": "right-to-left isolate",
    "\u2068": "first strong isolate",
    "\u2069": "pop directional isolate",
}
INSTRUCTION_ADAPTER_REL_PATHS = [
    "GEMINI.md",
    ".github/copilot-instructions.md",
    ".claude/CLAUDE.md",
    ".continue/rules/repository-instructions.md",
]


def _instruction_adapter_files(root: Path) -> list[Path]:
    return [root / rel for rel in INSTRUCTION_ADAPTER_REL_PATHS if (root / rel).exists()]


def accepted_surface_text_files(root: Path) -> list[Path]:
    candidates: list[Path] = [
        root / "AGENTS.md",
        root / "README.md",
        *_instruction_adapter_files(root),
    ]
    skills_root = root / ".agents" / "skills"
    if skills_root.exists():
        for pattern in ("*/SKILL.md", "*/docs/**/*.md"):
            candidates.extend(skills_root.glob(pattern))
    automations_root = root / "automations"
    if automations_root.exists():
        for pattern in ("*/WORKFLOW.md", "*/instructions.md", "*/templates/**/*.md"):
            candidates.extend(automations_root.glob(pattern))
    docs_root = root / "docs"
    if docs_root.exists():
        candidates.extend(docs_root.rglob("*.md"))

    files: list[Path] = []
    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        try:
            relative_parts = path.relative_to(root).parts
        except ValueError:
            relative_parts = path.parts
        if "__pycache__" in relative_parts or "runs" in relative_parts:
            continue
        files.append(path)
    return sorted(set(files), key=lambda item: item.as_posix())


def hidden_character_warnings(root: Path) -> list[str]:
    warnings: list[str] = []
    for path in accepted_surface_text_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for char, label in HIDDEN_TEXT_CHARS.items():
                column = line.find(char)
                if column >= 0:
                    warnings.append(
                        f"{repo.relative(root, path)} contains hidden character {label} "
                        f"on line {line_number}, column {column + 1}; remove invisible text from accepted surfaces."
                    )
                    break
    return warnings
