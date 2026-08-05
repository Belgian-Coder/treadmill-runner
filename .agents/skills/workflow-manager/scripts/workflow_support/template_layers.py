#!/usr/bin/env python3
"""Workflow template layering, integration descriptors, and small policy checks."""

from __future__ import annotations

import difflib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import workflow_manager_common as common
from validation_support import manifests as contract_manifests

ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
ALLOWED_TEMPLATE_SUFFIXES = {".md"}
DEFAULT_CONTEXT_MARKERS = ("<!-- MANAGED START -->", "<!-- MANAGED END -->")


def read_json_object(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {}, "missing"
    data, error = common.read_json_file(path)
    if error or not isinstance(data, dict):
        return {}, error or "JSON root must be an object"
    return data, ""


def safe_relative_path(value: str) -> bool:
    path = Path(value)
    return bool(value.strip()) and not path.is_absolute() and ".." not in path.parts


def template_layers_config(root: Path, workflow_name: str) -> dict[str, Any]:
    workflow_dir = root / "automations" / workflow_name
    manifest, _error = read_json_object(workflow_dir / "module.json")
    metadata = workflow_metadata(root, workflow_name)
    merged = dict(manifest)
    merged.update(metadata)
    manifest_layers = manifest.get("template_layers")
    metadata_layers = metadata.get("template_layers")
    if isinstance(manifest_layers, dict) and isinstance(metadata_layers, dict):
        merged["template_layers"] = {**manifest_layers, **metadata_layers}
    layers = merged.get("template_layers")
    return layers if isinstance(layers, dict) else {}


def declared_root(
    root: Path,
    workflow_dir: Path,
    value: object,
    *,
    workflow_relative: bool,
) -> Path | None:
    text = str(value or "").strip().replace("\\", "/")
    if not safe_relative_path(text):
        return None
    candidate = (workflow_dir if workflow_relative else root) / text
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def _template_candidate_state(boundary: Path, candidate: Path) -> tuple[bool, bool]:
    """Return (is_file, unsafe) without statting a resolved-path escape."""

    try:
        candidate.resolve(strict=False).relative_to(boundary.resolve())
    except (OSError, ValueError):
        return False, True
    return candidate.is_file(), False


def _path_is_within(boundary: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(boundary.resolve())
    except (OSError, ValueError):
        return False
    return True


def layer_priority(layers: dict[str, Any], layer: str, default: int) -> int:
    priorities = layers.get("priorities")
    value = priorities.get(layer, default) if isinstance(priorities, dict) else default
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def template_providers(root: Path, workflow_name: str, template_name: str, *, profile: str = "default") -> list[dict[str, Any]]:
    workflow_dir = root / "automations" / workflow_name
    layers = template_layers_config(root, workflow_name)
    profiles = layers.get("profiles") if isinstance(layers.get("profiles"), dict) else {}
    profile_spec = profiles.get(profile)
    if not isinstance(profile_spec, dict):
        return []
    providers: list[dict[str, Any]] = []
    override_roots = layers.get("override_roots") if isinstance(layers.get("override_roots"), list) else []
    for index, root_value in enumerate(override_roots):
        override_root = declared_root(root, workflow_dir, root_value, workflow_relative=False)
        if override_root is None:
            continue
        override_path = override_root / template_name
        exists, unsafe = _template_candidate_state(override_root, override_path)
        providers.append(
            {
                "layer": "project-override",
                "root_index": index,
                "priority": layer_priority(layers, "project-override", 0),
                "path": common.relative(root, override_path),
                "exists": exists,
                "unsafe": unsafe,
                "source": str(root_value).replace("\\", "/"),
            }
        )
    preset_roots = layers.get("preset_roots") if isinstance(layers.get("preset_roots"), list) else []
    for root_value in preset_roots:
        presets_dir = declared_root(root, workflow_dir, root_value, workflow_relative=False)
        if presets_dir is None or not presets_dir.exists():
            continue
        for preset_dir in sorted(presets_dir.iterdir()):
            if not _path_is_within(presets_dir, preset_dir):
                providers.append(
                    {
                        "layer": "workflow-preset",
                        "preset": preset_dir.name,
                        "priority": layer_priority(layers, "workflow-preset", 50),
                        "path": common.relative(root, preset_dir),
                        "exists": False,
                        "unsafe": True,
                        "source": str(root_value).replace("\\", "/"),
                        "manifest_error": "preset child resolves outside declared root",
                    }
                )
                continue
            if not preset_dir.is_dir():
                continue
            manifest_path = preset_dir / "preset.json"
            candidate = preset_dir / "templates" / template_name
            manifest_safe = _path_is_within(preset_dir, manifest_path)
            candidate_exists, candidate_unsafe = _template_candidate_state(
                preset_dir,
                candidate,
            )
            if manifest_safe:
                manifest, error = read_json_object(manifest_path)
            else:
                manifest, error = {}, "preset manifest resolves outside declared root"
            default_priority = layer_priority(layers, "workflow-preset", 50)
            priority = manifest.get("priority", default_priority) if not error else default_priority
            try:
                priority_int = int(priority)
            except (TypeError, ValueError):
                priority_int = default_priority
            providers.append(
                {
                    "layer": "workflow-preset",
                    "preset": preset_dir.name,
                    "priority": priority_int,
                    "path": common.relative(root, candidate),
                    "exists": candidate_exists and manifest_safe,
                    "unsafe": candidate_unsafe or not manifest_safe,
                    "source": common.relative(root, preset_dir / "preset.json"),
                    "manifest_error": error,
                }
            )
    template_roots = profile_spec.get("template_roots")
    if isinstance(template_roots, list):
        for index, root_value in enumerate(template_roots):
            profile_root = declared_root(root, workflow_dir, root_value, workflow_relative=True)
            if profile_root is None:
                continue
            requested_name = str(template_name).strip().replace("\\", "/")
            default_name = str(layers.get("default_template") or "plan.md").strip().replace("\\", "/")
            candidate_name = requested_name
            if requested_name == default_name:
                candidate_name = str(profile_spec.get("template") or requested_name).strip().replace("\\", "/")
            if (
                not safe_relative_path(candidate_name)
                or Path(candidate_name).suffix.lower() not in ALLOWED_TEMPLATE_SUFFIXES
            ):
                providers.append(
                    {
                        "layer": f"workflow-{profile}-template",
                        "root_index": index,
                        "priority": layer_priority(layers, f"workflow-{profile}", 100),
                        "path": candidate_name,
                        "exists": False,
                        "unsafe": True,
                        "source": str(root_value).replace("\\", "/"),
                    }
                )
                continue
            candidate = profile_root / candidate_name
            exists, unsafe = _template_candidate_state(profile_root, candidate)
            layer = f"workflow-{profile}"
            providers.append(
                {
                    "layer": f"{layer}-template",
                    "root_index": index,
                    "priority": layer_priority(layers, layer, 100),
                    "path": common.relative(root, candidate),
                    "exists": exists,
                    "unsafe": unsafe,
                    "source": str(root_value).replace("\\", "/"),
                }
            )
    return sorted(providers, key=lambda row: (int(row.get("priority", 100)), str(row.get("path", ""))))


def provider_conflicts(providers: list[dict[str, Any]]) -> list[str]:
    existing = [row for row in providers if row.get("exists")]
    issues: list[str] = [
        f"unsafe template provider {str(row.get('path', ''))!r} resolves outside its declared root"
        for row in providers
        if row.get("unsafe") is True
    ]
    by_path: dict[str, list[dict[str, Any]]] = {}
    by_priority: dict[int, list[dict[str, Any]]] = {}
    for row in existing:
        by_path.setdefault(str(row.get("path", "")), []).append(row)
        by_priority.setdefault(int(row.get("priority", 100)), []).append(row)
    for path, rows in sorted(by_path.items()):
        if len(rows) > 1:
            issues.append(f"duplicate template provider {path!r} is declared more than once")
    for priority, rows in sorted(by_priority.items()):
        unique_paths = sorted({str(row.get("path", "")) for row in rows})
        if len(unique_paths) > 1:
            issues.append(
                f"same priority conflict: equal-priority template providers at priority {priority}: "
                f"{', '.join(unique_paths)}"
            )
    return issues


def resolve_template(
    root: Path,
    workflow_name: str,
    template_name: str | None = None,
    *,
    profile: str = "default",
) -> dict[str, Any]:
    if not ID_RE.match(workflow_name):
        raise SystemExit("workflow name must use lowercase letters, digits, and hyphens")
    if not ID_RE.match(profile):
        raise SystemExit("template profile must use lowercase letters, digits, and hyphens")
    layers = template_layers_config(root, workflow_name)
    template_name = str(template_name or layers.get("default_template") or "plan.md").strip()
    if not safe_relative_path(template_name) or Path(template_name).suffix.lower() not in ALLOWED_TEMPLATE_SUFFIXES:
        raise SystemExit("template name must be a repo-relative Markdown filename")
    profiles = layers.get("profiles") if isinstance(layers.get("profiles"), dict) else {}
    profile_available = isinstance(profiles.get(profile), dict)
    providers = template_providers(root, workflow_name, template_name, profile=profile)
    issues = provider_conflicts(providers)
    selected = None if issues else next((row for row in providers if row.get("exists")), None)
    if not profile_available:
        issues = [f"requested template profile '{profile}' is unavailable"]
    elif selected is None and not issues:
        issues.append(
            f"required template has no provider; no provider found for "
            f"{workflow_name}/{template_name}"
        )
    if not profile_available:
        status = "profile-unavailable"
    elif any(issue.startswith("unsafe template provider") for issue in issues):
        status = "unsafe"
    elif any(
        issue.startswith(("duplicate template provider", "same priority"))
        for issue in issues
    ):
        status = "conflict"
    elif selected:
        status = "resolved"
    else:
        status = "missing"
    return {
        "schema_version": 1,
        "tool": "workflow-manager.template-resolve",
        "ok": selected is not None and not issues,
        "status": status,
        "workflow": workflow_name,
        "template": template_name,
        "profile": profile,
        "selected": selected or {},
        "providers": providers,
        "issues": issues,
        "next_command": f"python -B .agents/manage.py workflow template lint --name {workflow_name} --format json",
    }


def lint_templates(root: Path, workflow_name: str | None = None) -> dict[str, Any]:
    workflows = [workflow_name] if workflow_name else [
        path.name for path in sorted((root / "automations").iterdir()) if path.is_dir() and (path / "module.json").exists()
    ]
    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for name in workflows:
        workflow_dir = root / "automations" / name
        manifest, manifest_error = read_json_object(workflow_dir / "module.json")
        layers = template_layers_config(root, name)
        profiles = layers.get("profiles") if isinstance(layers.get("profiles"), dict) else {}
        template_name = str(layers.get("default_template") or "plan.md")
        command_argvs = contract_manifests.command_argvs(manifest.get("commands"))
        requires_plan_template = any(
            any(argv[index : index + 2] == ["workflow", "plan-check"] for index in range(len(argv) - 1))
            for argv in command_argvs
        )
        if not profiles and requires_plan_template:
            issues.append(
                {
                    "workflow": name,
                    "template": template_name,
                    "status": "profile-unavailable",
                    "issue": (
                        "required template has no provider because no template profile "
                        "is available for the declared plan-check command"
                    ),
                    "providers": [],
                }
            )
        for profile in sorted(profiles):
            resolved = resolve_template(root, name, template_name, profile=profile)
            providers = resolved.get("providers") if isinstance(resolved.get("providers"), list) else []
            existing = [row for row in providers if row.get("exists")]
            if resolved.get("ok") is not True:
                resolved_issues = resolved.get("issues") if isinstance(resolved.get("issues"), list) else []
                for issue in resolved_issues or ["required template has no provider"]:
                    issues.append(
                        {
                            "workflow": name,
                            "template": template_name,
                            "profile": profile,
                            "status": resolved.get("status", "missing"),
                            "issue": str(issue),
                            "providers": [row.get("path") for row in providers],
                        }
                    )
            for row in existing:
                path = root / str(row.get("path", ""))
                if path.suffix.lower() not in ALLOWED_TEMPLATE_SUFFIXES:
                    issues.append(
                        {
                            "workflow": name,
                            "profile": profile,
                            "path": row.get("path"),
                            "issue": "template provider must be Markdown",
                        }
                    )
            for row in providers:
                if row.get("manifest_error") and row.get("layer") == "workflow-preset":
                    issues.append(
                        {
                            "workflow": name,
                            "preset": row.get("preset"),
                            "issue": f"preset manifest problem: {row.get('manifest_error')}",
                        }
                    )
            rows.append(
                {
                    "workflow": name,
                    "template": template_name,
                    "profile": profile,
                    "status": resolved.get("status", "missing"),
                    "providers": providers,
                }
            )
        if manifest_error:
            issues.append({"workflow": name, "issue": f"module manifest problem: {manifest_error}"})
    return {
        "schema_version": 1,
        "tool": "workflow-manager.template-lint",
        "ok": not issues,
        "status": "ok" if not issues else "failed",
        "workflow_count": len(workflows),
        "issues": issues,
        "templates": rows,
        "next_command": (
            f"python -B .agents/manage.py workflow template resolve --name {workflow_name} --format json"
            if workflow_name
            else "python -B .agents/manage.py check-additions"
        ),
    }


def template_gate_check(root: Path, workflow_name: str | None = None) -> dict[str, Any]:
    workflows = [workflow_name] if workflow_name else [
        path.name for path in sorted((root / "automations").iterdir()) if path.is_dir() and (path / "module.json").exists()
    ]
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    import workflow_plan_check

    for name in workflows:
        metadata = workflow_metadata(root, name)
        layers = template_layers_config(root, name)
        profiles = layers.get("profiles") if isinstance(layers.get("profiles"), dict) else {}
        template_name = str(layers.get("default_template") or "plan.md")
        gates = metadata.get("gates") if isinstance(metadata.get("gates"), list) else []
        required_section_gates = [
            gate
            for gate in gates
            if isinstance(gate, dict)
            and gate.get("required") is not False
            and isinstance(gate.get("evidence"), str)
            and str(gate.get("evidence", "")).strip()
            and "/" not in str(gate.get("evidence", ""))
            and "\\" not in str(gate.get("evidence", ""))
            and not str(gate.get("evidence", "")).lower().endswith((".md", ".json"))
        ]
        if not required_section_gates:
            rows.append(
                {
                    "workflow": name,
                    "status": "skipped",
                    "reason": "no required gates declare template evidence sections",
                    "profiles": [],
                }
            )
            continue
        profile_rows: list[dict[str, Any]] = []
        if not profiles:
            profile_issue = {
                "workflow": name,
                "profile": "",
                "status": "profile-unavailable",
                "issue": "no template profile is available for required evidence gates",
            }
            issues.append(profile_issue)
            profile_rows.append(
                {
                    "profile": "",
                    "ok": False,
                    "status": "profile-unavailable",
                    "issues": [profile_issue["issue"]],
                }
            )
        for profile in sorted(profiles):
            resolved = resolve_template(root, name, template_name, profile=profile)
            selected = resolved.get("selected") if isinstance(resolved.get("selected"), dict) else {}
            if not selected:
                profile_issue = {
                    "workflow": name,
                    "profile": profile,
                    "status": resolved.get("status", "missing"),
                    "issue": "no plan template provider found for required evidence gates",
                }
                issues.append(profile_issue)
                profile_rows.append(
                    {
                        "profile": profile,
                        "ok": False,
                        "status": resolved.get("status", "missing"),
                        "issues": [*resolved.get("issues", []), profile_issue["issue"]],
                    }
                )
                continue
            path_text = str(selected.get("path", "")).strip()
            template_text = common.read_text(root / path_text)
            sections = workflow_plan_check.parse_sections(template_text)
            profile_issues = workflow_plan_check.metadata_gate_issues(root, name, sections, template=True)
            profile_rows.append(
                {
                    "profile": profile,
                    "ok": not profile_issues,
                    "template": path_text,
                    "issues": profile_issues,
                }
            )
            for issue in profile_issues:
                issues.append({"workflow": name, "profile": profile, "template": path_text, "issue": str(issue)})
        rows.append(
            {
                "workflow": name,
                "status": "passed" if all(row.get("ok") for row in profile_rows) else "failed",
                "required_gate_count": len(required_section_gates),
                "profiles": profile_rows,
            }
        )
    return {
        "schema_version": 1,
        "tool": "workflow-manager.template-gate-check",
        "ok": not issues,
        "status": "passed" if not issues else "failed",
        "workflow_count": len(workflows),
        "issues": issues,
        "workflows": rows,
        "next_command": (
            f"python -B .agents/manage.py workflow template lint --name {workflow_name} --format json"
            if workflow_name
            else "python -B .agents/manage.py workflow template lint --format json"
        ),
    }


def resolved_template_path(
    root: Path,
    workflow_name: str,
    template_name: str | None = None,
    *,
    profile: str = "default",
) -> Path | None:
    report = resolve_template(root, workflow_name, template_name, profile=profile)
    selected = report.get("selected") if isinstance(report.get("selected"), dict) else {}
    path_text = str(selected.get("path", "")).strip()
    if not path_text:
        return None
    path = root / path_text
    return path if path.exists() else None


def integration_descriptor_paths(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for base in (root / "integrations", root / ".agents" / "integrations"):
        if base.exists():
            candidates.extend(sorted(base.glob("*/integration.json")))
    return candidates


def validate_integration_descriptor(root: Path, path: Path) -> list[str]:
    data, error = read_json_object(path)
    if error:
        return [f"{common.relative(root, path)}: {error}"]
    issues: list[str] = []
    if data.get("schema_version") != 1:
        issues.append("schema_version must be 1")
    integration = data.get("integration")
    if not isinstance(integration, dict):
        issues.append("integration must be an object")
        integration = {}
    integration_id = str(integration.get("id", "")).strip()
    if not ID_RE.match(integration_id):
        issues.append("integration.id must use lowercase letters, digits, and hyphens")
    if path.parent.name != integration_id:
        issues.append("integration.id must match containing folder name")
    for key in ("name", "version", "description", "owner", "license"):
        value = integration.get(key)
        if not isinstance(value, str) or not value.strip():
            issues.append(f"integration.{key} is required")
    if integration.get("version") and not SEMVER_RE.match(str(integration.get("version"))):
        issues.append("integration.version must be SemVer")
    provides = data.get("provides", {})
    if provides is not None and not isinstance(provides, dict):
        issues.append("provides must be an object")
        provides = {}
    for field in ("commands", "managed_files", "tools"):
        value = provides.get(field) if isinstance(provides, dict) else None
        if value is not None and not isinstance(value, list):
            issues.append(f"provides.{field} must be a list when provided")
    return issues


def integration_check(root: Path) -> dict[str, Any]:
    paths = integration_descriptor_paths(root)
    rows = []
    issues = []
    descriptor_ids: set[str] = set()
    for path in paths:
        descriptor_issues = validate_integration_descriptor(root, path)
        rel = common.relative(root, path)
        rows.append({"path": rel, "ok": not descriptor_issues, "issues": descriptor_issues})
        issues.extend(f"{rel}: {issue}" for issue in descriptor_issues)
        if not descriptor_issues:
            descriptor_ids.add(path.parent.name)
    workflow_rows: list[dict[str, Any]] = []
    workflows_root = root / "automations"
    if workflows_root.exists():
        for workflow_dir in sorted(item for item in workflows_root.iterdir() if item.is_dir()):
            if not (workflow_dir / "module.json").exists():
                continue
            metadata = workflow_metadata(root, workflow_dir.name)
            declared = metadata.get("integrations")
            if declared is None:
                continue
            if not isinstance(declared, list):
                issue = f"automations/{workflow_dir.name}/module.json: integrations must be a list"
                workflow_rows.append({"workflow": workflow_dir.name, "integrations": [], "ok": False, "issues": [issue]})
                issues.append(issue)
                continue
            workflow_issues: list[str] = []
            normalized_ids: list[str] = []
            for value in declared:
                integration_id = str(value).strip()
                normalized_ids.append(integration_id)
                if not ID_RE.match(integration_id):
                    workflow_issues.append(f"integration id is invalid: {integration_id}")
                elif integration_id not in descriptor_ids:
                    workflow_issues.append(f"integration has no descriptor: {integration_id}")
            rel_module = f"automations/{workflow_dir.name}/module.json"
            issues.extend(f"{rel_module}: {issue}" for issue in workflow_issues)
            workflow_rows.append(
                {
                    "workflow": workflow_dir.name,
                    "integrations": normalized_ids,
                    "ok": not workflow_issues,
                    "issues": workflow_issues,
                }
            )
    return {
        "schema_version": 1,
        "tool": "workflow-manager.integration-check",
        "ok": not issues,
        "status": "ok" if not issues else "failed",
        "descriptor_count": len(paths),
        "descriptors": rows,
        "workflow_reference_count": len(workflow_rows),
        "workflow_references": workflow_rows,
        "issues": issues,
        "next_command": "python -B .agents/manage.py check-additions",
    }


def managed_section_diff(
    root: Path,
    target_path: Path,
    replacement_path: Path,
    *,
    start_marker: str = DEFAULT_CONTEXT_MARKERS[0],
    end_marker: str = DEFAULT_CONTEXT_MARKERS[1],
) -> dict[str, Any]:
    target = (root / target_path).resolve(strict=False) if not target_path.is_absolute() else target_path
    replacement = (root / replacement_path).resolve(strict=False) if not replacement_path.is_absolute() else replacement_path
    for path in (target, replacement):
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise SystemExit("managed diff paths must stay inside the repository") from exc
    current = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
    new_section = replacement.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        rf"{re.escape(start_marker)}.*?{re.escape(end_marker)}",
        re.DOTALL,
    )
    block = f"{start_marker}\n{new_section.rstrip()}\n{end_marker}"
    if pattern.search(current):
        updated = pattern.sub(block, current, count=1)
        status = "replace-managed-section"
    else:
        updated = current.rstrip() + ("\n\n" if current.strip() else "") + block + "\n"
        status = "append-managed-section"
    diff = list(
        difflib.unified_diff(
            current.splitlines(),
            updated.splitlines(),
            fromfile=common.relative(root, target),
            tofile=f"{common.relative(root, target)} (updated)",
            lineterm="",
        )
    )
    return {
        "schema_version": 1,
        "tool": "workflow-manager.managed-section-diff",
        "ok": True,
        "status": status,
        "target": common.relative(root, target),
        "replacement": common.relative(root, replacement),
        "start_marker": start_marker,
        "end_marker": end_marker,
        "diff": diff,
        "changed": bool(diff),
        "next_command": "review diff, then update through the owning sync command",
    }


def workflow_metadata(root: Path, workflow_name: str) -> dict[str, Any]:
    module_path = root / "automations" / workflow_name / "module.json"
    manifest, _error = read_json_object(module_path)
    metadata: dict[str, Any] = {}
    for key in ("input_schema", "gates", "template_layers", "branch_policy", "integrations"):
        if key in manifest:
            metadata[key] = manifest[key]
    metadata_path = manifest.get("metadata_path")
    if isinstance(metadata_path, str) and safe_relative_path(metadata_path):
        data, error = read_json_object(root / "automations" / workflow_name / metadata_path)
        if not error:
            metadata.update(data)
            metadata["metadata_path"] = metadata_path
    return metadata


def metadata_inspect(root: Path, workflow_name: str) -> dict[str, Any]:
    if not ID_RE.match(workflow_name):
        raise SystemExit("workflow name must use lowercase letters, digits, and hyphens")
    workflow_dir = root / "automations" / workflow_name
    manifest_path = workflow_dir / "module.json"
    manifest, manifest_error = read_json_object(manifest_path)
    issues: list[str] = []
    if manifest_error:
        issues.append(f"{common.relative(root, manifest_path)}: {manifest_error}")
    inline_metadata: dict[str, Any] = {}
    for key in ("input_schema", "gates", "template_layers", "branch_policy", "integrations"):
        if key in manifest:
            inline_metadata[key] = manifest[key]
    external_metadata: dict[str, Any] = {}
    metadata_path = manifest.get("metadata_path")
    metadata_source = ""
    if metadata_path is not None:
        if not isinstance(metadata_path, str) or not safe_relative_path(metadata_path):
            issues.append(f"{common.relative(root, manifest_path)}: metadata_path must be a safe relative path")
        else:
            metadata_source = metadata_path
            external_path = workflow_dir / metadata_path
            external_metadata, external_error = read_json_object(external_path)
            if external_error:
                issues.append(f"{common.relative(root, external_path)}: {external_error}")
    merged_metadata = dict(inline_metadata)
    merged_metadata.update(external_metadata)
    if metadata_source:
        merged_metadata["metadata_path"] = metadata_source
    merged_manifest = dict(manifest)
    merged_manifest.update(merged_metadata)
    return {
        "schema_version": 1,
        "tool": "workflow-manager.metadata-inspect",
        "ok": not issues,
        "status": "ok" if not issues else "failed",
        "workflow": workflow_name,
        "module_path": common.relative(root, manifest_path),
        "metadata_path": metadata_source,
        "inline_fields": sorted(inline_metadata),
        "external_fields": sorted(external_metadata),
        "metadata": merged_metadata,
        "merged_manifest": merged_manifest,
        "issues": issues,
        "next_command": f"python -B .agents/manage.py workflow template lint --name {workflow_name} --format json",
    }


def current_branch(root: Path) -> str:
    completed = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def branch_policy_check(
    root: Path,
    pattern: str = r"^(feature|fix|docs|chore|release)/[a-z0-9][a-z0-9._-]*$",
    *,
    branch: str | None = None,
) -> dict[str, Any]:
    explicit_branch = branch is not None
    branch = branch if branch is not None else current_branch(root)
    issues: list[str] = []
    if not branch:
        issues.append("current git branch could not be determined; this is common in detached HEAD worktrees")
    elif branch in {"main", "master"}:
        issues.append("current branch is a protected mainline branch")
    elif not re.match(pattern, branch):
        issues.append(f"current branch does not match policy: {pattern}")
    if issues:
        next_command = "create or switch to a feature/* branch before commit"
    elif explicit_branch:
        next_command = "none; explicit branch check only"
    else:
        next_command = "python -B .agents/manage.py commit-readiness"
    return {
        "schema_version": 1,
        "tool": "workflow-manager.branch-policy",
        "ok": not issues,
        "status": "ok" if not issues else "failed",
        "branch": branch,
        "pattern": pattern,
        "issues": issues,
        "next_command": next_command,
        "next_command_scope": "explicit-branch-check" if explicit_branch else "current-branch-readiness",
    }


def render_simple_report(report: dict[str, Any], title: str) -> str:
    lines = [f"# {title}", ""]
    lines.append(f"- Status: {report.get('status')}")
    if report.get("workflow"):
        lines.append(f"- Workflow: `{report.get('workflow')}`")
    if report.get("selected"):
        selected = report.get("selected")
        if isinstance(selected, dict):
            lines.append(f"- Selected: `{selected.get('path')}` ({selected.get('layer')})")
    if report.get("branch"):
        lines.append(f"- Branch: `{report.get('branch')}`")
    if report.get("descriptor_count") is not None:
        lines.append(f"- Descriptors: {report.get('descriptor_count')}")
    if report.get("workflow_reference_count") is not None:
        lines.append(f"- Workflow references: {report.get('workflow_reference_count')}")
    if report.get("changed") is not None:
        lines.append(f"- Changed: {str(report.get('changed')).lower()}")
    if report.get("metadata_path"):
        lines.append(f"- Metadata path: `{report.get('metadata_path')}`")
    if report.get("inline_fields"):
        lines.append(f"- Inline fields: {', '.join(f'`{field}`' for field in report.get('inline_fields', []))}")
    if report.get("external_fields"):
        lines.append(f"- External fields: {', '.join(f'`{field}`' for field in report.get('external_fields', []))}")
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        for issue in issues[:50]:
            lines.append(f"- {issue}")
    if report.get("providers"):
        lines.extend(["", "## Providers", ""])
        for row in report.get("providers", []):
            if isinstance(row, dict):
                lines.append(f"- `{row.get('path')}`: {row.get('layer')} exists={row.get('exists')}")
    if report.get("diff"):
        lines.extend(["", "## Diff", "", "```diff"])
        lines.extend(str(line) for line in report.get("diff", [])[:200])
        lines.append("```")
    lines.append(f"- Next command: `{report.get('next_command')}`")
    return "\n".join(lines) + "\n"
