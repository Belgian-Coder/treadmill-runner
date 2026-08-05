#!/usr/bin/env python3
"""Import-review scanner for staged skill or workflow source folders."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from analysis_support import analysis_common as common
from repo_support import repo_policy

ZERO_WIDTH_CHARS = {
    "\u200b": "zero-width space",
    "\u200c": "zero-width non-joiner",
    "\u200d": "zero-width joiner",
    "\ufeff": "byte-order mark inside text",
}
PROMPT_INJECTION_PATTERNS = {
    "ignore previous instructions": re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE),
    "disregard system instructions": re.compile(r"disregard\s+(?:the\s+)?system\s+instructions", re.IGNORECASE),
    "reveal system prompt": re.compile(r"reveal\s+(?:your\s+)?system\s+prompt", re.IGNORECASE),
    "developer message override": re.compile(r"developer\s+message\s+override", re.IGNORECASE),
    "system tag": re.compile(r"</?system>", re.IGNORECASE),
}
HIDDEN_INSTRUCTION_PATTERNS = {
    "assistant instruction comment": re.compile(
        r"<!--[^>]*(assistant|system|developer|instruction|prompt)[^>]*-->",
        re.IGNORECASE,
    ),
    "hidden display style": re.compile(r"display\s*:\s*none[^;\n]*(assistant|instruction|prompt)", re.IGNORECASE),
}
HIGH_CONFIDENCE_SECRET_PATTERNS = {
    "private key marker": re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    "GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9_]{30,}"),
    "OpenAI token": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "credential assignment": re.compile(
        r"\b(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD)\b\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{12,}",
        re.IGNORECASE,
    ),
}
PACKAGE_OR_NETWORK_PATTERNS = {
    "package install command": re.compile(
        r"\b(pip\s+install|uv\s+add|npm\s+install|npm\s+i\s|pnpm\s+add|yarn\s+add)\b",
        re.IGNORECASE,
    ),
    "download command": re.compile(r"(^|\s)(curl|wget)\s+https?://", re.IGNORECASE),
    "network URL": re.compile(r"https?://", re.IGNORECASE),
    "postinstall script": re.compile(r"postinstall", re.IGNORECASE),
}
LICENSE_NAMES = {
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "NOTICE",
    "NOTICE.md",
    "NOTICE.txt",
    "COPYING",
}
HOOK_NAMES = {
    "applypatch-msg",
    "commit-msg",
    "post-checkout",
    "post-commit",
    "post-merge",
    "pre-commit",
    "pre-push",
    "pre-rebase",
    "prepare-commit-msg",
}
SECRET_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "secrets.json",
    "id_ed25519",
    "id_rsa",
}
GENERATED_ADAPTER_MARKERS = (
    "generated claude",
    "generated from agents",
    "generated adapter",
    "do not edit by hand",
)
IGNORED_VCS_PARTS = {".git"}


def tool_setting_paths() -> set[str]:
    return {
        "." + "codex" + "/config.toml",
        "." + "mcp" + ".json",
        "." + "vscode" + "/settings.json",
        "." + "vscode" + "/mcp.json",
        "." + "claude" + "/settings.json",
        "." + "github" + "/copilot/settings.json",
    }


def is_binary(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:4096]
    except OSError:
        return False
    return b"\0" in chunk


def extra_hook_files(root: Path, known_files: set[Path]) -> list[Path]:
    results: list[Path] = []
    for hooks_root in (root / ".githooks",):
        if not hooks_root.exists():
            continue
        for path in sorted(hooks_root.rglob("*"), key=lambda item: item.as_posix().lower()):
            if path.is_file() and path not in known_files:
                results.append(path)
    return results[:40]


def is_vcs_internal(rel: str) -> bool:
    return any(part.lower() in IGNORED_VCS_PARTS for part in Path(rel).parts)


def warn(category: str, path: str, message: str, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "category": category,
        "path": path,
        "severity": "warning",
        "message": message,
    }
    item.update(extra)
    return item


def line_warnings(category: str, rel: str, label: str, text: str, pattern: re.Pattern[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            results.append(
                warn(
                    category,
                    rel,
                    label,
                    line=line_number,
                    snippet=compact_snippet(line),
                )
            )
            break
    return results


def compact_snippet(text: str, limit: int = 140) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def generated_adapter_warning(rel: str, text: str) -> dict[str, Any] | None:
    lowered = text.lower()
    if (
        rel.startswith(".claude/skills/")
        or rel == "." + "github" + "/copilot-instructions.md"
        or any(marker in lowered for marker in GENERATED_ADAPTER_MARKERS)
    ):
        return warn("generated_adapter", rel, "generated adapter or generated instructions should not be imported as canonical source")
    return None


def is_hook_file(path: Path, rel: str) -> bool:
    parts = {part.lower() for part in Path(rel).parts}
    return path.name in HOOK_NAMES or "hooks" in parts or ".githooks" in parts


def scan_import_review(
    root: Path,
    files: list[Path],
    *,
    disallowed_scripts: list[str],
    evidence: list[dict[str, object]],
    max_text_files: int,
) -> dict[str, Any]:
    base = root if root.is_dir() else root.parent
    large_file_warn_bytes = repo_policy.int_value(
        repo_policy.project_root(base), "limits.import.large_file_warn_bytes"
    )
    known = {path.resolve(strict=False) for path in files}
    all_files = list(files)
    all_files.extend(
        path
        for path in extra_hook_files(base, known)
        if path.resolve(strict=False) not in known
    )
    settings_paths = tool_setting_paths()
    warnings: list[dict[str, Any]] = []
    facts: dict[str, Any] = {
        "license_files": [],
        "notice_files": [],
        "credential_filenames": [],
        "tool_settings": [],
        "generated_adapters": [],
        "hook_files": [],
        "large_files": [],
        "binary_files": [],
        "package_or_network_files": [],
    }

    text_checked = 0
    for path in all_files:
        rel = common.relative(base, path)
        if is_vcs_internal(rel):
            continue
        rel_lower = rel.lower()
        name = path.name
        suffix = path.suffix.lower()

        if name in LICENSE_NAMES:
            key = "notice_files" if name.startswith("NOTICE") else "license_files"
            facts[key].append(rel)
        if name in SECRET_FILENAMES or suffix in {".pem", ".key", ".p12"}:
            facts["credential_filenames"].append(rel)
            warnings.append(warn("credential_filename", rel, "credential-like filename requires manual review"))
        if rel in settings_paths:
            facts["tool_settings"].append(rel)
            warnings.append(warn("tool_settings", rel, "committed tool settings should not be imported as canonical source"))
        if rel in disallowed_scripts or suffix in common.DISALLOWED_SCRIPT_SUFFIXES:
            warnings.append(warn("disallowed_script", rel, "shell, batch, or PowerShell script must be converted or rejected"))
        if is_hook_file(path, rel):
            facts["hook_files"].append(rel)
            warnings.append(warn("hook_file", rel, "hook file requires explicit review before promotion"))

        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size > large_file_warn_bytes:
            facts["large_files"].append({"path": rel, "bytes": size})
            warnings.append(warn("large_file", rel, "large file should stay out of trigger-loaded skill context", bytes=size))
        if suffix not in common.TEXT_SUFFIXES and is_binary(path):
            facts["binary_files"].append(rel)
            warnings.append(warn("binary_file", rel, "binary file should be reviewed for provenance and necessity"))

        if text_checked >= max_text_files:
            continue
        if (
            suffix not in common.TEXT_SUFFIXES
            and name not in common.MANIFEST_NAMES
            and name not in LICENSE_NAMES
            and name not in SECRET_FILENAMES
        ):
            continue
        text_checked += 1
        text = common.read_text(path, limit=120_000)

        adapter_warning = generated_adapter_warning(rel, text)
        if adapter_warning:
            facts["generated_adapters"].append(rel)
            warnings.append(adapter_warning)

        for char, label in ZERO_WIDTH_CHARS.items():
            if char in text:
                warnings.append(warn("zero_width_characters", rel, f"{label} found in text"))
                break
        for label, pattern in PROMPT_INJECTION_PATTERNS.items():
            warnings.extend(line_warnings("prompt_injection_marker", rel, label, text, pattern))
        for label, pattern in HIDDEN_INSTRUCTION_PATTERNS.items():
            warnings.extend(line_warnings("hidden_instruction_text", rel, label, text, pattern))
        for label, pattern in HIGH_CONFIDENCE_SECRET_PATTERNS.items():
            warnings.extend(line_warnings("high_confidence_secret", rel, label, text, pattern))
        package_hit = False
        for label, pattern in PACKAGE_OR_NETWORK_PATTERNS.items():
            hits = line_warnings("package_install_or_network_signal", rel, label, text, pattern)
            if hits:
                package_hit = True
                warnings.extend(hits)
        if package_hit:
            facts["package_or_network_files"].append(rel)

    for item in evidence:
        category = str(item.get("category", ""))
        if category in {"installs", "network"}:
            path = str(item.get("path", "unknown"))
            if path not in facts["package_or_network_files"]:
                facts["package_or_network_files"].append(path)
            warnings.append(
                warn(
                    "package_install_or_network_signal",
                    path,
                    f"existing analyzer evidence: {item.get('signal')}",
                    line=item.get("line"),
                )
            )

    unique_warnings = {
        (
            item.get("category"),
            item.get("path"),
            item.get("line"),
            item.get("message"),
        ): item
        for item in warnings
    }
    for key, value in facts.items():
        if isinstance(value, list):
            facts[key] = sorted(value, key=lambda item: str(item))

    return {
        "profile": "import",
        "status": "warnings" if unique_warnings else "ok",
        "warning_count": len(unique_warnings),
        "warnings": sorted(
            unique_warnings.values(),
            key=lambda item: (str(item.get("category")), str(item.get("path")), int(item.get("line") or 0)),
        ),
        "facts": facts,
        "checks": [
            "zero-width characters",
            "prompt-injection markers",
            "hidden instruction text",
            "credential filenames",
            "high-confidence secret patterns",
            "disallowed scripts",
            "hooks",
            "tool settings",
            "generated adapters",
            "large and binary files",
            "package install or network signals",
            "license and notice facts",
        ],
    }
