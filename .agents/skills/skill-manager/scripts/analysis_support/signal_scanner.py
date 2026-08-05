#!/usr/bin/env python3
"""Script, risk, and text signal scanning for skill-manager location analysis."""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.dont_write_bytecode = True

from analysis_support import analysis_common as analysis
import skill_manager_common as skill_common

SECRET_NAME_PATTERN = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|credential|pat|private[_-]?key)"
)
ENV_REFERENCE_PATTERN = re.compile(
    r"""
    \bos\.environ\[(?P<py_index>['\"](?P<py_index_name>[A-Za-z_][A-Za-z0-9_]*)['\"])\]
    |\bos\.environ\.get\((?P<py_get>['\"](?P<py_get_name>[A-Za-z_][A-Za-z0-9_]*)['\"])
    |\bos\.getenv\((?P<py_getenv>['\"](?P<py_getenv_name>[A-Za-z_][A-Za-z0-9_]*)['\"])
    |\bgetenv\((?P<c_getenv>['\"](?P<c_getenv_name>[A-Za-z_][A-Za-z0-9_]*)['\"])
    |\bprocess\.env\.(?P<node_dot>[A-Za-z_][A-Za-z0-9_]*)
    |\bprocess\.env\[(?P<node_index>['\"](?P<node_index_name>[A-Za-z_][A-Za-z0-9_]*)['\"])\]
    |\$env:(?P<ps>[A-Za-z_][A-Za-z0-9_]*)
    |\bexport\s+(?P<export>[A-Z][A-Z0-9_]{2,})
    """,
    re.VERBOSE,
)
SECRET_PATTERNS = {
    "possible private key marker": re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    "possible API key literal": re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]"),
}
NETWORK_PATTERNS = {
    "curl/wget command": re.compile(r"(^|\s)(curl|wget)\s+"),
    "Python HTTP client usage": re.compile(
        r"\b(import\s+(requests|httpx|aiohttp)|from\s+(requests|httpx|aiohttp)\s+import|(requests|httpx|aiohttp)\.(get|post|put|delete|request|Client|AsyncClient|Session))"
    ),
    "Node HTTP client usage": re.compile(
        r"\b(import\s+.*axios|require\(['\"]axios['\"]\)|axios\.|fetch\()"
    ),
    "remote side-effect call": re.compile(
        r"\b(upload|publish|deploy|sync)\s*(\(|:|=)", re.IGNORECASE
    ),
}
SCRIPT_QUALITY_PATTERNS = {
    "dangerous subprocess shell": re.compile(
        r"\bsubprocess\.(run|call|Popen|check_call|check_output)\s*\([^)]*shell\s*=\s*True",
        re.IGNORECASE | re.DOTALL,
    ),
    "dynamic Python execution": re.compile(r"\b(eval|exec)\s*\(", re.IGNORECASE),
    "untyped argparse option": re.compile(r"\.add_argument\([^)]*(?!type=|choices=)", re.IGNORECASE),
    "missing structured output signal": re.compile(r"\bprint\s*\([^)]*\)", re.IGNORECASE),
}
UNPINNED_DEPENDENCY_PATTERNS = {
    "unpinned pip dependency": re.compile(r"^\s*[-A-Za-z0-9_.]+\s*$", re.MULTILINE),
    "unpinned npm dependency": re.compile(r'"(?:dependencies|devDependencies)"\s*:\s*\{[^}]*"[^"]+"\s*:\s*"\*"', re.DOTALL),
    "floating GitHub action": re.compile(r"\buses:\s*[^@\s]+@(main|master|latest)\b", re.IGNORECASE),
}
SIDE_EFFECT_PATTERNS = {
    "destructive": {
        "destructive filesystem command": re.compile(
            r"\b(rm\s+-rf|Remove-Item\b.*\b-Recurse\b|shutil\.rmtree|rmdir\s+/s|del\s+/[sq])",
            re.IGNORECASE,
        ),
    },
    "uploads": {
        "upload/publish/deploy operation": re.compile(
            r"\b(upload|publish|deploy|release)\s*(?:\(|:|=|--|/|\.)",
            re.IGNORECASE,
        ),
    },
    "installs": {
        "package installation command": re.compile(
            r"\b(pip\s+install|uv\s+add|uv\s+pip\s+install|npm\s+install|npm\s+i\s|pnpm\s+add|yarn\s+add)\b",
            re.IGNORECASE,
        ),
    },
    "production_writes": {
        "production write indicator": re.compile(
            r"\b(production|prod)\b.*\b(write|delete|deploy|publish|migrate|apply)\b",
            re.IGNORECASE,
        ),
    },
    "generated_settings": {
        "committed tool settings path": re.compile(
            r"(\.codex/config\.toml|\.mcp\.json|\.vscode/settings\.json|\.vscode/mcp\.json|"
            r"\.claude/settings\.json|\.github/copilot/settings\.json)",
            re.IGNORECASE,
        ),
    },
}
def script_report(root: Path, files: list[Path]) -> tuple[list[str], list[str], list[str]]:
    scripts: list[str] = []
    disallowed: list[str] = []
    plans: list[str] = []

    base = root if root.is_dir() else root.parent
    for path in files:
        suffix = path.suffix.lower()
        if suffix not in analysis.SCRIPT_SUFFIXES:
            continue
        rel = analysis.relative(base, path)
        scripts.append(rel)
        if suffix in analysis.DISALLOWED_SCRIPT_SUFFIXES:
            disallowed.append(rel)
            plans.append(conversion_plan(path, base))

    return sorted(scripts), sorted(disallowed), plans


def conversion_plan(path: Path, base: Path) -> str:
    rel = analysis.relative(base, path)
    suffix = path.suffix.lower()
    text = analysis.read_text(path, limit=50_000)
    detected_actions: list[str] = []

    action_patterns = {
        "filesystem operations": r"\b(cp|copy|mv|move|rm|del|mkdir|rmdir|robocopy|xcopy)\b",
        "process execution": r"\b(npm|npx|node|python|python3|dotnet|git|gh|docker|kubectl)\b",
        "network download": r"\b(curl|wget|Invoke-WebRequest|Invoke-RestMethod)\b",
        "environment variables": r"(\$env:|\bexport\s+|process\.env)",
        "archive/compression": r"\b(zip|tar|Expand-Archive|Compress-Archive)\b",
    }
    for label, pattern in action_patterns.items():
        if re.search(pattern, text, flags=re.IGNORECASE):
            detected_actions.append(label)

    target = path.with_suffix(".py").name
    actions = ", ".join(detected_actions) if detected_actions else "review command intent manually"
    return (
        f"- `{rel}` ({suffix}) -> replace with `{target}`. "
        f"Detected concerns: {actions}. Convert argument parsing to `argparse`, "
        "filesystem changes to `pathlib`/`shutil`, process calls to `subprocess.run([...], check=True)`, "
        "HTTP calls to `urllib.request` unless a documented dependency is justified, and add a dry-run mode "
        "before deleting, moving, uploading, or overwriting files."
    )


def scan_text_signals(
    root: Path, files: list[Path], max_text_files: int
) -> tuple[list[str], list[str], list[dict[str, object]]]:
    base = root if root.is_dir() else root.parent
    security_hits: dict[str, set[str]] = defaultdict(set)
    network_hits: dict[str, set[str]] = defaultdict(set)
    evidence: list[skill_common.Evidence] = []
    checked = 0

    for path in files:
        if checked >= max_text_files:
            break
        if path.suffix.lower() not in analysis.TEXT_SUFFIXES and path.name not in analysis.MANIFEST_NAMES:
            continue
        checked += 1
        signal_lines = list(iter_signal_lines(analysis.read_text(path, limit=80_000)))
        rel = analysis.relative(base, path)
        source = skill_common.source_kind(path)
        for line_number, line in signal_lines:
            secret_env_names = secret_environment_names(line)
            if secret_env_names:
                security_hits["secret-like environment variable access"].add(rel)
                evidence.append(
                    skill_common.Evidence(
                        category="credentials",
                        path=rel,
                        line=line_number,
                        signal="secret-like environment variable access",
                        source=source,
                        snippet=skill_common.compact_snippet(line),
                    )
                )
                break
        for label, pattern in SECRET_PATTERNS.items():
            for line_number, line in signal_lines:
                if pattern.search(line):
                    security_hits[label].add(rel)
                    evidence.append(
                        skill_common.Evidence(
                            category="credentials",
                            path=rel,
                            line=line_number,
                            signal=label,
                            source=source,
                            snippet=skill_common.compact_snippet(line),
                        )
                    )
                    break
        for label, pattern in NETWORK_PATTERNS.items():
            for line_number, line in signal_lines:
                if pattern.search(line):
                    network_hits[label].add(rel)
                    evidence.append(
                        skill_common.Evidence(
                            category="network",
                            path=rel,
                            line=line_number,
                            signal=label,
                            source=source,
                            snippet=skill_common.compact_snippet(line),
                        )
                    )
                    break
        for category, patterns in SIDE_EFFECT_PATTERNS.items():
            for label, pattern in patterns.items():
                for line_number, line in signal_lines:
                    if pattern.search(line):
                        evidence.append(
                            skill_common.Evidence(
                                category=category,
                                path=rel,
                                line=line_number,
                                signal=label,
                                source=source,
                                snippet=skill_common.compact_snippet(line),
                            )
                        )
                        break
        if path.suffix.lower() in analysis.SCRIPT_SUFFIXES or path.name in {"package.json", "requirements.txt", "pyproject.toml"}:
            source_text = analysis.read_text(path, limit=120_000)
            for label, pattern in SCRIPT_QUALITY_PATTERNS.items():
                if pattern.search(source_text):
                    evidence.append(
                        skill_common.Evidence(
                            category="script_quality",
                            path=rel,
                            line=None,
                            signal=label,
                            source=source,
                            snippet="review script for safe subprocess/typed args/structured output behavior",
                            confidence="medium",
                        )
                    )
            for label, pattern in UNPINNED_DEPENDENCY_PATTERNS.items():
                if pattern.search(source_text):
                    evidence.append(
                        skill_common.Evidence(
                            category="dependencies",
                            path=rel,
                            line=None,
                            signal=label,
                            source=source,
                            snippet="dependency or action version appears floating or unpinned",
                            confidence="medium",
                        )
                    )

    security = [
        f"{label}: {', '.join(sorted(paths)[:8])}" for label, paths in sorted(security_hits.items())
    ]
    network = [
        f"{label}: {', '.join(sorted(paths)[:8])}" for label, paths in sorted(network_hits.items())
    ]
    unique_evidence = {
        (
            item.category,
            item.path,
            item.line,
            item.signal,
            item.source,
            item.confidence,
        ): item
        for item in evidence
    }
    return security, network, [
        item.to_dict()
        for item in sorted(
            unique_evidence.values(),
            key=lambda value: (value.category, value.path, value.line or 0, value.signal),
        )
    ]


def secret_environment_names(line: str) -> list[str]:
    names: list[str] = []
    for match in ENV_REFERENCE_PATTERN.finditer(line):
        for key, value in match.groupdict().items():
            if key.endswith("_name") or key in {"node_dot", "ps", "export"}:
                if value and SECRET_NAME_PATTERN.search(value):
                    names.append(value)
    return names


def iter_signal_lines(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    in_fence = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        lowered = stripped.lower()
        if "re.compile(" in stripped:
            continue
        if any(fragment in stripped for fragment in ("\\b", "\\s", "\\.", "\\(")):
            continue
        if "production-write" in lowered or "production_writes" in lowered:
            continue
        if "production-write" in lowered or "production_writes" in lowered:
            continue
        if lowered.startswith("|") and any(
            word in lowered
            for word in (
                "credential",
                "delete",
                "deploy",
                "install",
                "network",
                "production",
                "publish",
                "upload",
            )
        ):
            continue
        if re.match(r"^[\"']?[a-z0-9_-]+[\"']?\s*[:,]?$", stripped, flags=re.IGNORECASE):
            continue
        if re.match(
            r"^[\"'][a-z0-9_-]+[\"']\s*:\s*[0-9\"'a-z_-]+,?$",
            stripped,
            flags=re.IGNORECASE,
        ):
            continue
        if re.match(
            r"^[\"'][a-z0-9_-]+[\"']\s*:\s*[0-9\"'a-z_-]+,?$",
            stripped,
            flags=re.IGNORECASE,
        ):
            continue
        if (
            not in_fence
            and any(
                phrase in lowered
                for phrase in (
                    "does not",
                    "do not",
                    "must not",
                    "not allowed",
                    "never ",
                    "without ",
                    "avoid ",
                    "refuse ",
                )
            )
            and any(
                word in lowered
                for word in (
                    "delete",
                    "deploy",
                    "install",
                    "network",
                    "publish",
                    "remove",
                    "scrape",
                    "settings",
                    "upload",
                    "call apis",
                )
            )
        ):
            continue
        lines.append((line_number, line))
    return lines


def relevant_signal_lines(text: str) -> list[str]:
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        lowered = stripped.lower()
        if "re.compile(" in stripped:
            continue
        if any(fragment in stripped for fragment in ("\\b", "\\s", "\\.", "\\(")):
            continue
        if lowered.startswith("|") and any(
            word in lowered
            for word in (
                "credential",
                "delete",
                "deploy",
                "install",
                "network",
                "production",
                "publish",
                "upload",
            )
        ):
            continue
        if re.match(r"^[\"']?[a-z0-9_-]+[\"']?\s*[:,]?$", stripped, flags=re.IGNORECASE):
            continue
        if (
            not in_fence
            and any(phrase in lowered for phrase in ("does not", "do not", "not allowed", "without "))
            and any(word in lowered for word in ("upload", "scrape", "call apis", "install", "network"))
        ):
            continue
        lines.append(line)
    return lines


def improvement_suggestions(
    root: Path,
    manifests: list[str],
    dependencies: list[str],
    disallowed_scripts: list[str],
    security: list[str],
    network: list[str],
    evidence: list[dict[str, object]],
) -> list[str]:
    suggestions: list[str] = []
    is_skill = root.is_dir() and (root / "SKILL.md").exists()

    if is_skill:
        if not (root / "module.json").exists():
            suggestions.append("Add top-level `module.json` before accepting or upgrading this skill.")
        if (root / "README.md").exists():
            suggestions.append("Move per-skill README content into `docs/`.")
    else:
        suggestions.append("If promoting this as a skill, rewrite it into `.agents/skills/<skill-name>/SKILL.md` with a narrow trigger.")

    only_missing_manifest_note = dependencies == ["No package manifest dependencies detected."]
    if not manifests and only_missing_manifest_note:
        suggestions.append("Document dependencies explicitly, because no common package manifests were detected.")
    if disallowed_scripts:
        suggestions.append("Convert disallowed shell, batch, or PowerShell scripts to Python 3.12+ helpers before promotion.")
    if network:
        suggestions.append("Document every network call or upload path and make remote data transfer explicit and user-driven.")
    if security:
        suggestions.append("Review possible secrets or credential references before promotion; do not commit real secrets.")
    manifest, _manifest_error = (
        skill_common.load_skill_manifest(root)
        if root.is_dir() and (root / "module.json").exists()
        else (None, None)
    )
    declared_risks = set(skill_common.manifest_risk_flags(manifest))
    side_effect_categories = {
        str(item.get("category"))
        for item in evidence
        if str(item.get("category")) in {"destructive", "generated_settings", "installs", "production_writes", "uploads"}
        and str(item.get("category")) not in declared_risks
    }
    if side_effect_categories:
        suggestions.append(
            "Declare or remove side-effect behavior before promotion: "
            f"{', '.join(sorted(side_effect_categories))}."
        )
    script_quality_categories = {
        str(item.get("signal"))
        for item in evidence
        if str(item.get("category")) == "script_quality"
    }
    if script_quality_categories:
        suggestions.append(
            "Review script quality before promotion: "
            f"{', '.join(sorted(script_quality_categories))}. Prefer typed argparse options, no `shell=True`, and JSON/Markdown outputs."
        )
    dependency_categories = {
        str(item.get("signal"))
        for item in evidence
        if str(item.get("category")) == "dependencies"
    }
    if dependency_categories:
        suggestions.append(
            "Pin external dependencies or document why floating versions are acceptable: "
            f"{', '.join(sorted(dependency_categories))}."
        )
    has_external_dependencies = any(
        "No package manifest dependencies detected" not in item
        and "Python 3.12+ stdlib only" not in item
        for item in dependencies
    )
    if has_external_dependencies:
        suggestions.append("Keep only dependencies that are required for the skill workflow; move optional tools to docs with clear purpose.")

    suggestions.append("Run the skill-local validator before promotion.")
    return suggestions
