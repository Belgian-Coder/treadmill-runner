#!/usr/bin/env python3
"""Shared helpers for local skill maintenance scripts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import module_contract_v3
from urllib.parse import urlparse

sys.dont_write_bytecode = True

MIN_PYTHON = (3, 12)
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
FRONTMATTER_PATTERN = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*:\s*(.*)\s*$")
DISALLOWED_SCRIPT_SUFFIXES = {
    ".bash",
    ".bat",
    ".cmd",
    ".fish",
    ".ps1",
    ".psd1",
    ".psm1",
    ".sh",
    ".zsh",
}
ALLOWED_TOP_LEVEL = {
    "SKILL.md",
    "LICENSE.txt",
    "NOTICE.txt",
    "assets",
    "docs",
    "module.json",
    "scripts",
    "suites",
}
IGNORED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "bin",
    "dist",
    "node_modules",
    "obj",
    "venv",
}
RISK_KEYS = {
    "credentials",
    "destructive",
    "generated_settings",
    "installs",
    "network",
    "production_writes",
    "uploads",
}
RISK_PROFILES = {
    "read-only",
    "local-write",
    "local-destructive",
    "networked",
    "credentialed",
    "production-write",
}
RISK_PROFILE_RANK = {
    "read-only": 0,
    "local-write": 1,
    "local-destructive": 2,
    "networked": 3,
    "credentialed": 4,
    "production-write": 5,
}
CHANGE_CLASSES = {
    "breaking",
    "feature",
    "fix",
    "docs",
    "dependency",
    "risk",
    "metadata",
    "unknown",
}
EVAL_ASSERTION_TYPES = {
    "budget_skill_words_at_most",
    "compare_change_class",
    "compare_decision",
    "description_contains",
    "completion_contract_terms",
    "compatibility_required",
    "file_absent",
    "file_contains",
    "file_exists",
    "manifest_field_equals",
    "public_command_behavior",
    "python_script_succeeds",
    "repo_command_json_field_equals",
    "repo_file_contains",
    "repo_command_output_contains",
    "repo_command_succeeds",
    "risk_declared",
    "risk_profile_covers_flags",
    "skill_contains",
    "stop_or_fallback_terms",
    "trigger_quality",
    "validation_ok",
}
LOCAL_AI_USE_CASE_IDS = {
    "validation-triage",
    "code-review",
    "patch-draft",
    "implementation-planning",
    "inventory-summary",
    "changelog-draft",
    "changed-files-summary",
    "failure-cluster",
    "test-gap-summary",
    "handoff-draft",
    "duplicate-overlap-detection",
    "vision-describe",
    "vision-pdf",
    "skill-routing",
    "workflow-routing",
}
LOCAL_AI_USE_CASE_FIELDS = {
    "id",
    "command",
    "applies_when",
    "guardrail",
    "evidence_input",
    "owner",
}
LOCAL_AI_USE_CASE_OWNERS = {"local-ai-helper", "skill-manager", "workflow-manager"}
LOCAL_AI_COMMAND_PREFIX = "python -B .agents/manage.py local-ai"
DOTNET_SKILL_PREFIX = "dotnet-"
DOTNET_LEGACY_SKILL_NAME = "dotnet-legacy"
DOTNET_LEGACY_SKILL_PREFIX = "dotnet-legacy-"
DOTNET_LEGACY_SURFACE_MARKERS = (
    ".net framework",
    "net48",
    "net472",
    "net471",
    "net47",
    "net462",
    "net461",
    "net46",
    "net452",
    "net451",
    "net45",
    "packages.config",
    "binding redirect",
    "binding redirects",
    "web forms",
    "classic asp.net",
    "wcf",
    "old csproj",
    "non-sdk",
    "gac",
    "com registration",
    "iis-hosted",
)
DOTNET_LEGACY_HANDOFF_MARKERS = (
    "dotnet-legacy",
    "route .net framework",
    "routes .net framework",
    "routed .net framework",
    "hand off .net framework",
    "handoff .net framework",
    "switch to dotnet-legacy",
    "use dotnet-legacy",
    "do not use for .net framework",
    "not use for .net framework",
)


@dataclass(frozen=True)
class Evidence:
    category: str
    path: str
    line: int | None
    signal: str
    source: str
    confidence: str = "medium"
    snippet: str = ""
    declared: bool = False

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        if not self.snippet:
            data.pop("snippet", None)
        return data


def require_supported_python() -> None:
    if sys.version_info >= MIN_PYTHON:
        return
    current = ".".join(str(part) for part in sys.version_info[:3])
    required = ".".join(str(part) for part in MIN_PYTHON)
    raise SystemExit(
        f"Python {required}+ is required; current interpreter is Python {current}."
    )


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def read_text(path: Path, limit: int = 200_000) -> str:
    data = path.read_bytes()[:limit]
    return data.decode("utf-8-sig", errors="replace")


def parse_frontmatter_text(text: str) -> tuple[dict[str, str] | None, str | None]:
    lines = text.splitlines()
    if len(lines) < 4 or lines[0].strip() != "---":
        return None, "SKILL.md must start with YAML frontmatter."

    end = -1
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end < 0:
        return None, "SKILL.md frontmatter is missing a closing --- line."

    metadata: dict[str, str] = {}
    extra_lines: list[str] = []
    for line in lines[1:end]:
        if not line.strip():
            continue
        match = FRONTMATTER_PATTERN.match(line)
        if not match:
            extra_lines.append(line)
            continue
        key = match.group(1)
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        metadata[key] = value

    if extra_lines:
        return None, "SKILL.md frontmatter contains unsupported YAML syntax."
    return metadata, None


def parse_frontmatter_file(skill_path: Path) -> tuple[dict[str, str] | None, str | None]:
    if not skill_path.exists():
        return None, f"Skill file not found: {skill_path}"
    return parse_frontmatter_text(read_text(skill_path))


def extract_frontmatter_description(skill_path: Path) -> tuple[str | None, str | None]:
    metadata, error = parse_frontmatter_file(skill_path)
    if error or metadata is None:
        return None, None
    return metadata.get("name"), metadata.get("description")


def read_json_file(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")), None
    except FileNotFoundError:
        return None, f"JSON file not found: {path}"
    except json.JSONDecodeError as exc:
        return None, f"{path.name} is invalid JSON: {exc.msg} at line {exc.lineno}."


def skill_manifest_path(skill_dir: Path) -> Path:
    return skill_dir / "module.json"


def load_skill_manifest_with_path(skill_dir: Path) -> tuple[dict[str, Any] | None, Path, str | None]:
    path = skill_manifest_path(skill_dir)
    data, error = read_json_file(path)
    if error:
        return None, path, error
    if not isinstance(data, dict):
        return None, path, f"{path.name} must contain a JSON object."
    return data, path, None


def load_skill_manifest(skill_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    data, _path, error = load_skill_manifest_with_path(skill_dir)
    return data, error


def semver_tuple(value: str) -> tuple[int, int, int] | None:
    match = SEMVER_PATTERN.match(value)
    if not match:
        return None
    return tuple(int(part) for part in match.groups()[:3])


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", strip_frontmatter(text)))


def strip_frontmatter(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                return "\n".join(lines[index + 1 :])
    return text


def iter_files(root: Path, max_files: int = 5000) -> list[Path]:
    if root.is_file():
        return [root]

    files: list[Path] = []
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in IGNORED_DIRS and not name.endswith(".egg-info")
        ]
        current = Path(current_root)
        for filename in sorted(filenames, key=str.lower):
            files.append(current / filename)
            if len(files) >= max_files:
                return files
    return files


def discover_skill_dirs(root: Path, source_root: str = ".agents/skills") -> list[Path]:
    skills_root = root / source_root
    if not skills_root.exists():
        return []
    return [
        child
        for child in sorted(skills_root.iterdir(), key=lambda item: item.name.lower())
        if child.is_dir() and (child / "SKILL.md").exists()
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_file_hashes(root: Path, max_files: int = 5000) -> dict[str, str]:
    base = root if root.is_dir() else root.parent
    return {
        relative(base, path): sha256_file(path)
        for path in iter_files(root, max_files=max_files)
        if path.is_file()
    }


def source_kind(path: Path) -> str:
    parts = set(path.parts)
    if path.name in {"package.json", "pyproject.toml", "requirements.txt", "module.json"}:
        return "manifest"
    if "scripts" in parts or path.suffix.lower() in DISALLOWED_SCRIPT_SUFFIXES | {".py"}:
        return "script"
    if "docs" in parts or path.suffix.lower() in {".md", ".txt"}:
        return "docs"
    if "assets" in parts:
        return "asset"
    return "inferred"


def file_bucket(path: str | Path) -> str:
    value = path.as_posix() if isinstance(path, Path) else str(path).replace("\\", "/")
    name = Path(value).name
    if value == "SKILL.md":
        return "routing" if name == "SKILL.md" else "instructions"
    if value == "module.json":
        return "manifest"
    if value.startswith("scripts/"):
        return "scripts"
    if value.startswith("docs/"):
        return "docs"
    if value.startswith("assets/"):
        return "assets"
    if value.startswith("agents/"):
        return "metadata"
    if value in {"LICENSE.txt", "NOTICE.txt"}:
        return "provenance"
    if value.startswith("automations/"):
        return "generated-indexes"
    return "other"


def compact_snippet(line: str, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", line).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def manifest_dependency_labels(manifest: dict[str, Any] | None) -> list[str]:
    if not manifest:
        return []
    dependencies = manifest.get("dependencies")
    if not isinstance(dependencies, list):
        return []
    labels: list[str] = []
    for item in dependencies:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            purpose = str(item.get("purpose", "")).strip()
            version = str(item.get("version", "")).strip()
            if name and version:
                name = f"{name} {version}"
            if name and purpose:
                labels.append(f"{name}: {purpose}")
            elif name:
                labels.append(name)
        elif isinstance(item, str) and item.strip():
            labels.append(item.strip())
    return labels


def manifest_risk_flags(manifest: dict[str, Any] | None) -> list[str]:
    if not manifest:
        return []
    risk = manifest.get("risk")
    if not isinstance(risk, dict):
        return []
    return sorted(key for key in RISK_KEYS if risk.get(key) is True)


def manifest_risk_profile(manifest: dict[str, Any] | None) -> str:
    if not manifest:
        return ""
    risk = manifest.get("risk")
    if not isinstance(risk, dict):
        return ""
    profile = risk.get("profile")
    return str(profile).strip() if isinstance(profile, str) else ""


def required_risk_profile(risk: dict[str, Any]) -> str:
    required = "read-only"
    if risk.get("generated_settings"):
        required = "local-write"
    if risk.get("destructive"):
        required = "local-destructive"
    if risk.get("network") or risk.get("uploads") or risk.get("installs"):
        required = "networked"
    if risk.get("credentials"):
        required = "credentialed"
    if risk.get("production_writes"):
        required = "production-write"
    return required


def risk_profile_covers(profile: str, required: str) -> bool:
    return RISK_PROFILE_RANK.get(profile, -1) >= RISK_PROFILE_RANK.get(required, 999)


def routing_example_counts(manifest: dict[str, Any] | None) -> dict[str, int]:
    result = {"eval_suites": 0}
    if not manifest:
        return result
    quality = manifest.get("quality")
    if not isinstance(quality, dict):
        return result
    suites = quality.get("eval_suites")
    if isinstance(suites, list):
        result["eval_suites"] = len(suites)
    return result


def local_ai_use_cases(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not manifest:
        return []
    local_ai = manifest.get("local_ai")
    if not isinstance(local_ai, dict):
        return []
    use_cases = local_ai.get("use_cases")
    if not isinstance(use_cases, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in use_cases:
        if isinstance(item, dict):
            normalized.append(item)
        elif isinstance(item, str) and item.strip():
            normalized.append(
                {
                    "id": item.strip(),
                    "owner": "local-ai-helper",
                    "guardrail": "Advisory; deterministic checks and source evidence remain authoritative.",
                }
            )
    return normalized


def local_ai_use_case_summary(manifest: dict[str, Any] | None) -> dict[str, Any]:
    ids: list[str] = []
    for item in local_ai_use_cases(manifest):
        use_case_id = item.get("id")
        if isinstance(use_case_id, str) and use_case_id.strip():
            ids.append(use_case_id.strip())
    return {"use_case_count": len(ids), "use_cases": ids}


def normalized_policy_text(*values: object) -> str:
    return re.sub(r"\s+", " ", " ".join(str(value or "") for value in values)).strip().lower()


def mentions_dotnet_legacy_surface(text: str) -> bool:
    for marker in DOTNET_LEGACY_SURFACE_MARKERS:
        if re.fullmatch(r"[a-z0-9]+", marker):
            pattern = rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])"
            if re.search(pattern, text):
                return True
        elif marker in text:
            return True
    return False


def mentions_dotnet_legacy_handoff(text: str) -> bool:
    return any(marker in text for marker in DOTNET_LEGACY_HANDOFF_MARKERS)


def dotnet_skill_naming_errors(
    skill_name: str,
    description: str,
    summary: str,
) -> list[str]:
    """Return errors for .NET skill names that blur modern and Framework ownership."""

    if not skill_name.startswith(DOTNET_SKILL_PREFIX):
        return []

    text = normalized_policy_text(description, summary)
    mentions_legacy_surface = mentions_dotnet_legacy_surface(text)
    if skill_name == DOTNET_LEGACY_SKILL_NAME or skill_name.startswith(DOTNET_LEGACY_SKILL_PREFIX):
        if not mentions_legacy_surface:
            return [
                "dotnet-legacy skills must explicitly name .NET Framework or a "
                "Framework-era surface in SKILL.md description or module.json summary."
            ]
        return []

    if mentions_legacy_surface and not mentions_dotnet_legacy_handoff(text):
        return [
            "Modern dotnet-* skills must not claim .NET Framework or Framework-era "
            "ownership directly; use dotnet-legacy or route the work there."
        ]
    return []


def validate_local_ai_metadata(value: object, label: str) -> list[str]:
    errors: list[str] = []
    if value is None:
        return errors
    if not isinstance(value, dict):
        return [f"{label} must be an object when provided."]

    use_cases = value.get("use_cases")
    if use_cases is None:
        return errors
    if not isinstance(use_cases, list):
        return [f"{label}.use_cases must be a list when provided."]

    seen_ids: set[str] = set()
    for index, item in enumerate(use_cases):
        item_label = f"{label}.use_cases[{index}]"
        if isinstance(item, str):
            use_case_id = item.strip()
            if not use_case_id:
                errors.append(f"{item_label} must be a non-empty string or object.")
                continue
            if use_case_id not in LOCAL_AI_USE_CASE_IDS:
                errors.append(f"{item_label} has unknown local_ai.use_cases id '{use_case_id}'.")
            if use_case_id in seen_ids:
                errors.append(f"{item_label} duplicates local_ai.use_cases id '{use_case_id}'.")
            seen_ids.add(use_case_id)
            continue
        if not isinstance(item, dict):
            errors.append(f"{item_label} must be a string or object.")
            continue
        missing = LOCAL_AI_USE_CASE_FIELDS - set(item)
        if missing:
            errors.append(f"{item_label} is missing keys: {', '.join(sorted(missing))}.")
        for key in sorted(LOCAL_AI_USE_CASE_FIELDS):
            value_text = item.get(key)
            if not isinstance(value_text, str) or not value_text.strip():
                errors.append(f"{item_label}.{key} is required.")

        use_case_id = str(item.get("id", "")).strip()
        if use_case_id:
            if use_case_id not in LOCAL_AI_USE_CASE_IDS:
                errors.append(f"{item_label} has unknown local_ai.use_cases id '{use_case_id}'.")
            if use_case_id in seen_ids:
                errors.append(f"{item_label} duplicates local_ai.use_cases id '{use_case_id}'.")
            seen_ids.add(use_case_id)

        command = str(item.get("command", "")).strip()
        if command and not command.startswith(LOCAL_AI_COMMAND_PREFIX):
            errors.append(f"{item_label}.command must use `{LOCAL_AI_COMMAND_PREFIX} ...`.")

        owner = str(item.get("owner", "")).strip()
        if owner and owner not in LOCAL_AI_USE_CASE_OWNERS:
            errors.append(
                f"{item_label}.owner must be one of: "
                f"{', '.join(sorted(LOCAL_AI_USE_CASE_OWNERS))}."
            )
    return errors


def validate_manifest_shape(
    skill_dir: Path, metadata: dict[str, str] | None
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    manifest, manifest_path, manifest_error = load_skill_manifest_with_path(skill_dir)
    manifest_label = "module.json"
    if manifest_error:
        errors.append(f"module.json is required and could not be loaded: {manifest_error}")
        return errors, warnings
    assert manifest is not None

    manifest, contract_errors, contract_warnings = (
        module_contract_v3.normalize_module_contract(manifest)
    )
    errors.extend(contract_errors)
    warnings.extend(contract_warnings)

    required = {
        "schema_version",
        "kind",
        "id",
        "version",
        "summary",
        "owners",
        "inputs",
        "outputs",
        "commands",
        "related_modules",
        "validation",
        "risk",
    }
    missing = required - set(manifest)
    if missing:
        errors.append(f"{manifest_label} is missing required keys: {', '.join(sorted(missing))}.")

    if manifest.get("kind") != "skill":
        errors.append("module.json kind must be 'skill'.")

    name = manifest.get("id")
    if name != skill_dir.name:
        errors.append(
            f"module.json id '{name}' must match folder name '{skill_dir.name}'."
        )
    if metadata and name != metadata.get("name"):
        errors.append("module.json id must match SKILL.md frontmatter name.")

    version = manifest.get("version")
    if not isinstance(version, str) or semver_tuple(version) is None:
        errors.append(f"{manifest_label} version must be a valid SemVer value.")

    if "status" in manifest and manifest.get("status") not in {"accepted", "staged", "deprecated", "retired"}:
        errors.append("module.json status must be accepted, staged, deprecated, or retired.")

    summary = manifest.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append(f"{manifest_label} summary must be a non-empty string.")

    for key in ("owners", "inputs", "outputs", "related_modules", "validation"):
        value = manifest.get(key)
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            errors.append(f"module.json {key} must be a list of non-empty strings.")

    compatibility = manifest.get("compatibility")
    if compatibility is None:
        compatibility = {}
    if compatibility is not None and not isinstance(compatibility, dict):
        errors.append(f"{manifest_label} compatibility must be an object.")
    elif isinstance(compatibility, dict) and compatibility:
        if compatibility.get("codex") != "required":
            errors.append(f"{manifest_label} compatibility.codex must be 'required'.")
        if compatibility.get("github_copilot") != "required":
            errors.append(f"{manifest_label} compatibility.github_copilot must be 'required'.")
        if compatibility.get("claude_code") != "required":
            errors.append(f"{manifest_label} compatibility.claude_code must be 'required'.")

    dependencies = manifest.get("dependencies")
    if dependencies is None:
        dependencies = []
    if dependencies is not None and not isinstance(dependencies, list):
        errors.append(f"{manifest_label} dependencies must be a list.")
    elif isinstance(dependencies, list):
        for index, item in enumerate(dependencies):
            if not isinstance(item, dict):
                errors.append(f"{manifest_label} dependencies[{index}] must be an object.")
                continue
            if not str(item.get("name", "")).strip():
                errors.append(f"{manifest_label} dependencies[{index}].name is required.")
            if not str(item.get("purpose", "")).strip():
                errors.append(f"{manifest_label} dependencies[{index}].purpose is required.")

    risk = manifest.get("risk")
    if not isinstance(risk, dict):
        errors.append(f"{manifest_label} risk must be an object.")
    else:
        missing_risks = RISK_KEYS - set(risk)
        if missing_risks:
            errors.append(
                f"{manifest_label} risk is missing keys: "
                f"{', '.join(sorted(missing_risks))}."
            )
        for key in sorted(RISK_KEYS & set(risk)):
            if not isinstance(risk.get(key), bool):
                errors.append(f"{manifest_label} risk.{key} must be true or false.")
        profile = risk.get("profile")
        if profile is not None:
            if not isinstance(profile, str) or profile not in RISK_PROFILES:
                errors.append(
                    f"{manifest_label} risk.profile must be one of: "
                    f"{', '.join(sorted(RISK_PROFILES))}."
                )
            else:
                required_profile = required_risk_profile(risk)
                if not risk_profile_covers(profile, required_profile):
                    errors.append(
                        f"{manifest_label} risk.profile '{profile}' does not cover declared "
                        f"risk behavior; minimum profile is '{required_profile}'."
                    )

    provenance = manifest.get("provenance")
    if provenance is None:
        provenance = {}
    if provenance is not None and not isinstance(provenance, dict):
        errors.append(f"{manifest_label} provenance must be an object.")
    elif isinstance(provenance, dict) and provenance:
        for key in ("source", "license"):
            if not str(provenance.get(key, "")).strip():
                errors.append(f"{manifest_label} provenance.{key} is required.")
        attestations = provenance.get("attestations")
        if attestations is not None and not isinstance(attestations, list):
            errors.append(f"{manifest_label} provenance.attestations must be a list when provided.")
        source_hashes = provenance.get("source_hashes")
        if source_hashes is not None and not isinstance(source_hashes, dict):
            errors.append(f"{manifest_label} provenance.source_hashes must be an object when provided.")
        reviewed_at = provenance.get("reviewed_at")
        if reviewed_at is not None and (
            not isinstance(reviewed_at, str) or not reviewed_at.strip()
        ):
            errors.append(f"{manifest_label} provenance.reviewed_at must be a non-empty string when provided.")

    quality = manifest.get("quality")
    if quality is not None:
        if not isinstance(quality, dict):
            errors.append(f"{manifest_label} quality must be an object when provided.")
        else:
            eval_suites = quality.get("eval_suites")
            if eval_suites is not None:
                if not isinstance(eval_suites, list):
                    errors.append(f"{manifest_label} quality.eval_suites must be a list when provided.")
                else:
                    for index, item in enumerate(eval_suites):
                        suite_path_text = ""
                        if isinstance(item, str):
                            if not item.strip():
                                errors.append(f"{manifest_label} quality.eval_suites[{index}] must not be empty.")
                            suite_path_text = item.strip()
                        elif isinstance(item, dict):
                            path = item.get("path")
                            if not isinstance(path, str) or not path.strip():
                                errors.append(f"{manifest_label} quality.eval_suites[{index}] must include path.")
                            else:
                                suite_path_text = path.strip()
                        else:
                            errors.append(
                                f"{manifest_label} quality.eval_suites[{index}] must be a string or object."
                            )
                        if suite_path_text:
                            errors.extend(
                                validate_eval_suite_reference(
                                    skill_dir, suite_path_text, f"{manifest_label} quality.eval_suites[{index}]"
                                )
                            )
            eval_gap = quality.get("eval_gap_rationale")
            if eval_gap is not None and (not isinstance(eval_gap, str) or not eval_gap.strip()):
                errors.append(f"{manifest_label} quality.eval_gap_rationale must be a non-empty string when provided.")
            self_tests = quality.get("self_tests")
            if self_tests is not None:
                if not isinstance(self_tests, list):
                    errors.append(f"{manifest_label} quality.self_tests must be a list when provided.")
                else:
                    for index, item in enumerate(self_tests):
                        path_text = ""
                        if isinstance(item, str):
                            path_text = item.strip()
                        elif isinstance(item, dict):
                            path = item.get("path")
                            if isinstance(path, str):
                                path_text = path.strip()
                        else:
                            errors.append(
                                f"{manifest_label} quality.self_tests[{index}] must be a string or object."
                            )
                        if not path_text:
                            errors.append(f"{manifest_label} quality.self_tests[{index}] must include path.")
                            continue
                        test_path = Path(path_text)
                        if test_path.is_absolute() or ".." in test_path.parts:
                            errors.append(
                                f"{manifest_label} quality.self_tests[{index}] must be a repository-local path inside the skill folder."
                            )
                        elif test_path.suffix.lower() != ".py":
                            errors.append(f"{manifest_label} quality.self_tests[{index}] must point to a Python file.")
                        elif not (skill_dir / test_path).exists():
                            errors.append(
                                f"{manifest_label} quality.self_tests[{index}] does not exist: {path_text}."
                            )

    rationale = manifest.get("size_exception_rationale")
    if rationale is not None and (
        not isinstance(rationale, str) or not rationale.strip()
    ):
        errors.append(f"{manifest_label} size_exception_rationale must be a non-empty string.")

    errors.extend(validate_local_ai_metadata(manifest.get("local_ai"), f"{manifest_label} local_ai"))

    return errors, warnings


def validate_eval_suite_reference(
    skill_dir: Path, suite_path_text: str, label: str
) -> list[str]:
    errors: list[str] = []
    suite_path = Path(suite_path_text)
    if suite_path.is_absolute() or ".." in suite_path.parts:
        return [f"{label} must be a repository-local path inside the skill folder."]
    if suite_path.suffix.lower() != ".json":
        errors.append(f"{label} must point to a JSON eval suite.")
    resolved = skill_dir / suite_path
    data, error = read_json_file(resolved)
    if error:
        errors.append(f"{label} could not be loaded: {error}")
        return errors
    if not isinstance(data, dict):
        errors.append(f"{label} must contain a JSON object.")
        return errors
    cases = data.get("evals") or data.get("cases")
    if not isinstance(cases, list):
        errors.append(f"{label} must contain an evals or cases list.")
        return errors
    for case_index, case in enumerate(cases):
        if not isinstance(case, dict):
            errors.append(f"{label} case {case_index + 1} must be an object.")
            continue
        assertions = case.get("assertions")
        if not isinstance(assertions, list):
            errors.append(f"{label} case {case_index + 1} assertions must be a list.")
            continue
        for assertion_index, assertion in enumerate(assertions):
            if not isinstance(assertion, dict):
                errors.append(
                    f"{label} case {case_index + 1} assertion {assertion_index + 1} must be an object."
                )
                continue
            assertion_type = assertion.get("type")
            if assertion_type not in EVAL_ASSERTION_TYPES:
                errors.append(
                    f"{label} case {case_index + 1} assertion {assertion_index + 1} "
                    f"has unknown type: {assertion_type}."
                )
            command = assertion.get("command")
            if (
                str(assertion_type).startswith("repo_command")
                or assertion_type == "public_command_behavior"
            ) and not isinstance(command, list):
                errors.append(
                    f"{label} case {case_index + 1} assertion {assertion_index + 1} "
                    "must include a command list."
                )
    return errors
