"""module.json parsing and validation for workflow modules."""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path
from typing import Any

SKILL_MANAGER_SCRIPTS = (
    Path(__file__).resolve().parents[3] / "skill-manager" / "scripts"
)
if str(SKILL_MANAGER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_MANAGER_SCRIPTS))

import module_contract_v3

import routing_contract

import workflow_manager_common as common
from automation_validation_rules import PHASE_PATTERN
from validation_support.scanning import detect_external_signals
from workflow_support.workers import validate_worker_profiles

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
PHASE_LIFECYCLE_EVENTS = {
    "phase-pre",
    "phase-started",
    "phase-between",
    "phase-completed",
    "phase-blocked",
    "phase-post",
    "phase-handoff",
}
WORKFLOW_HOOK_EVENTS = PHASE_LIFECYCLE_EVENTS | {
    "workflow-pre",
    "workflow-post",
    "run-started",
    "run-finished",
}
PHASE_METADATA_LIST_FIELDS = {"entry_checks", "exit_checks", "evidence"}
STATE_FIELD_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
HOOK_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
TASK_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
CONTEXT_EVIDENCE_QUERY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
CONTEXT_EVIDENCE_SCOPES = {"repo", "documents", "workflow-runs", "all"}
INPUT_SCHEMA_TYPES = {"string", "number", "boolean", "enum", "path", "list", "object"}
GATE_TYPES = {"approval", "clarification", "quality", "validation", "human", "policy"}
ERR_OBJECT = "must be an object."
ERR_OBJECT_PROVIDED = "must be an object when provided."
ERR_LOWER_ID = "must use lowercase letters, digits, and hyphens."
ERR_BOOL_PROVIDED = "must be true or false when provided."
ERR_NON_EMPTY_STRING = "must be a non-empty string."
ERR_NON_EMPTY_STRING_PROVIDED = "must be a non-empty string when provided."
ERR_STRING_LIST = "must be a list of non-empty strings."
ERR_STRING_OR_OBJECT = "must be a string or object."
ERR_UNKNOWN_LIFECYCLE = "has unknown lifecycle event"
ERR_TIMEOUT = "must be an integer from 1 to 3600."
ERR_RUN_RELATIVE_PATH = "must be a relative path inside the run folder."
ERR_HOOK_AUDIT_JSON = "must end with .json for hook-audit JSON output."


def as_non_empty_string_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        elif isinstance(item, dict):
            label = str(item.get("id") or item.get("name") or "").strip()
            if label:
                result.append(label)
            else:
                return None
        else:
            return None
    return result


def command_texts(value: object) -> list[str]:
    """Render commands as canonical, explicitly non-executable JSON argv."""

    if not isinstance(value, list):
        return []
    rendered: list[str] = []
    for command in value:
        text = module_contract_v3.command_display(command).strip()
        if text and text not in rendered:
            rendered.append(text)
    return rendered


def command_argvs(value: object) -> list[list[str]]:
    """Return detached argv arrays without passing through display text."""

    if not isinstance(value, list):
        return []
    result: list[list[str]] = []
    for command in value:
        argv = module_contract_v3.command_argv(command)
        if argv and argv not in result:
            result.append(argv)
    return result


def command_specs(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [copy.deepcopy(command) for command in value if isinstance(command, dict)]


def phase_id(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("id", "")).strip()
    return ""


def phase_ids(manifest: dict[str, Any]) -> list[str]:
    phases = manifest.get("phases")
    if not isinstance(phases, list):
        return []
    return [phase_id(item) for item in phases if phase_id(item)]


def normalize_external_access(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "source_systems": [],
            "credential_expectations": "",
            "data_copied_locally": [],
            "attachments_retrieved": False,
        }

    source_systems = value.get("source_systems", value.get("systems", []))
    if not isinstance(source_systems, list):
        source_systems = []

    data_copied = value.get("data_copied_locally", value.get("copied_data", []))
    if isinstance(data_copied, str) and data_copied.strip():
        data_copied = [data_copied.strip()]
    if not isinstance(data_copied, list):
        data_copied = []

    credential_expectations = value.get(
        "credential_expectations", value.get("credentials", "")
    )
    if isinstance(credential_expectations, bool):
        credential_expectations = "required" if credential_expectations else "none"

    attachments = value.get(
        "attachments_retrieved", value.get("retrieves_attachments", False)
    )

    return {
        "source_systems": [
            str(item).strip() for item in source_systems if str(item).strip()
        ],
        "credential_expectations": str(credential_expectations).strip(),
        "data_copied_locally": [
            str(item).strip() for item in data_copied if str(item).strip()
        ],
        "attachments_retrieved": bool(attachments),
    }


def manifest_path(module_dir: Path) -> Path:
    return module_dir / "module.json"


def is_v2_manifest(module_dir: Path) -> bool:
    return True


def read_manifest(module_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    path = manifest_path(module_dir)
    data, error = common.read_json_file(path)
    if error:
        return None, error
    if not isinstance(data, dict):
        return None, f"{path.name} must contain a JSON object."
    return data, None


def read_optional_manifest(module_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    path = manifest_path(module_dir)
    if not path.exists():
        return None, None
    return read_manifest(module_dir)


def local_ai_use_cases(container: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not container:
        return []
    if isinstance(container.get("local_ai"), dict):
        use_cases = container["local_ai"].get("use_cases")
    else:
        use_cases = container.get("local_ai_use_cases")
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


def local_ai_use_case_summary(container: dict[str, Any] | None) -> dict[str, Any]:
    ids: list[str] = []
    for item in local_ai_use_cases(container):
        use_case_id = item.get("id")
        if isinstance(use_case_id, str) and use_case_id.strip():
            ids.append(use_case_id.strip())
    return {"use_case_count": len(ids), "use_cases": ids}


def validate_local_ai_metadata(value: object, label: str) -> list[str]:
    errors: list[str] = []
    if value is None:
        return errors
    if not isinstance(value, dict):
        return [f"{label} {ERR_OBJECT_PROVIDED}"]

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
            errors.append(f"{item_label} {ERR_STRING_OR_OBJECT}")
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


def validate_string_list_field(value: object, label: str, *, allow_empty: bool = False) -> list[str]:
    values = as_non_empty_string_list(value)
    if values is None:
        return [f"{label} {ERR_STRING_LIST}"]
    if not values and not allow_empty:
        return [f"{label} must include at least one value."]
    return []


def validate_routing_metadata(manifest: dict[str, Any]) -> list[str]:
    routing = manifest.get("routing")
    if routing is None:
        return ["module.json routing is required for accepted workflows."]
    if not isinstance(routing, dict):
        return ["module.json routing must be an object when provided."]
    errors: list[str] = []
    raw_terms = routing.get("terms")
    terms = (
        [item.strip() for item in raw_terms]
        if isinstance(raw_terms, list)
        and raw_terms
        and all(isinstance(item, str) and item.strip() for item in raw_terms)
        else None
    )
    if terms is None:
        errors.append("module.json routing.terms must be a list of non-empty strings.")
    elif not routing_contract.has_specific_concept(terms):
        errors.append("module.json routing.terms must include a specific routing concept.")
    raw_activation_terms = routing.get("activation_terms")
    activation_terms = (
        [item.strip() for item in raw_activation_terms]
        if isinstance(raw_activation_terms, list)
        and raw_activation_terms
        and all(isinstance(item, str) and item.strip() for item in raw_activation_terms)
        else None
    )
    if activation_terms is None:
        errors.append("module.json routing.activation_terms must be a list of non-empty strings.")
    elif not routing_contract.has_non_generic_activation(activation_terms):
        errors.append(
            "module.json routing.activation_terms must include a non-generic routing concept."
        )
    threshold = routing.get("threshold")
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 2:
        errors.append("module.json routing.threshold must be an integer of at least 2.")
    winner_margin = routing.get("winner_margin")
    if not isinstance(winner_margin, int) or isinstance(winner_margin, bool) or winner_margin < 1:
        errors.append("module.json routing.winner_margin must be an integer of at least 1.")
    if terms is not None:
        errors.extend(
            "module.json " + issue + "."
            for issue in routing_contract.routing_reachability_issues(
                terms,
                threshold=threshold,
                winner_margin=winner_margin,
            )
        )
    return errors


def validate_phase_lifecycle_metadata(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    label = "module.json"

    phases = manifest.get("phases")
    if isinstance(phases, list):
        for index, phase in enumerate(phases):
            if not isinstance(phase, dict):
                continue
            phase_label = f"{label} phases[{index}]"
            for field in sorted(PHASE_METADATA_LIST_FIELDS):
                if field in phase:
                    errors.extend(validate_string_list_field(phase.get(field), f"{phase_label}.{field}"))
            if "hooks" in phase:
                hook_values = as_non_empty_string_list(phase.get("hooks"))
                if hook_values is None or not hook_values:
                    errors.append(f"{phase_label}.hooks {ERR_STRING_LIST}")
                else:
                    for event in hook_values:
                        if event not in PHASE_LIFECYCLE_EVENTS:
                            errors.append(
                                f"{phase_label}.hooks {ERR_UNKNOWN_LIFECYCLE} '{event}'."
                            )

    lifecycle = manifest.get("phase_lifecycle")
    if lifecycle is None:
        return errors
    if not isinstance(lifecycle, dict):
        return errors + [f"{label} phase_lifecycle {ERR_OBJECT_PROVIDED}"]

    for field in ("events", "state_fields", "required_handoff_fields"):
        errors.extend(validate_string_list_field(lifecycle.get(field), f"{label} phase_lifecycle.{field}"))

    events = as_non_empty_string_list(lifecycle.get("events")) or []
    for event in events:
        if event not in PHASE_LIFECYCLE_EVENTS:
            errors.append(
                f"{label} phase_lifecycle.events {ERR_UNKNOWN_LIFECYCLE} '{event}'."
            )

    state_fields = as_non_empty_string_list(lifecycle.get("state_fields")) or []
    for field in state_fields:
        if not STATE_FIELD_PATTERN.match(field):
            errors.append(
                f"{label} phase_lifecycle.state_fields value '{field}' must use snake_case."
            )

    return errors


def validate_workflow_hooks(manifest: dict[str, Any], module_dir: Path) -> list[str]:
    errors: list[str] = []
    hooks = manifest.get("hooks")
    if hooks is None:
        return errors
    if not isinstance(hooks, list):
        return ["module.json hooks must be a list when provided."]

    workflow_script_prefix = f"python -B automations/{module_dir.name}/scripts/"
    workflow_script_placeholder_prefix = "python -B automations/{workflow}/scripts/"
    for index, hook in enumerate(hooks):
        label = f"module.json hooks[{index}]"
        if not isinstance(hook, dict):
            errors.append(f"{label} {ERR_OBJECT}")
            continue

        hook_id = str(hook.get("id", "")).strip()
        if not HOOK_ID_PATTERN.match(hook_id):
            errors.append(f"{label}.id {ERR_LOWER_ID}")

        event = str(hook.get("event", "")).strip()
        if event not in WORKFLOW_HOOK_EVENTS:
            errors.append(f"{label}.event {ERR_UNKNOWN_LIFECYCLE} '{event}'.")

        command = str(hook.get("command", "")).strip()
        if not command:
            errors.append(f"{label}.command {ERR_NON_EMPTY_STRING}")
        elif not (
            command.startswith("python -B .agents/manage.py ")
            or command.startswith("python -B .agents/skills/")
            or command.startswith(workflow_script_prefix)
            or command.startswith(workflow_script_placeholder_prefix)
        ):
            errors.append(
                f"{label}.command must use `python -B .agents/manage.py ...`, "
                f"`python -B .agents/skills/...`, `{workflow_script_prefix}...`, "
                f"or `{workflow_script_placeholder_prefix}...`."
            )

        if "required" in hook and not isinstance(hook.get("required"), bool):
            errors.append(f"{label}.required {ERR_BOOL_PROVIDED}")

        if "timeout_seconds" in hook:
            timeout = hook.get("timeout_seconds")
            if not isinstance(timeout, int) or timeout < 1 or timeout > 3600:
                errors.append(f"{label}.timeout_seconds {ERR_TIMEOUT}")

        if "evidence_path" in hook:
            evidence_path = str(hook.get("evidence_path", "")).strip()
            if (
                not evidence_path
                or Path(evidence_path).is_absolute()
                or ".." in Path(evidence_path).parts
            ):
                errors.append(f"{label}.evidence_path {ERR_RUN_RELATIVE_PATH}")
            if "workflow hook-audit" in command and "--format json" in command and evidence_path.endswith(".txt"):
                errors.append(f"{label}.evidence_path {ERR_HOOK_AUDIT_JSON}")

    return errors


def validate_task_graph(manifest: dict[str, Any]) -> list[str]:
    """Validate optional workflow task dependency metadata.

    Tasks are a contract surface only. Runtime status stays in run.json, and a
    task is ready when every declared dependency is complete in the run state.
    """
    errors: list[str] = []
    tasks = manifest.get("tasks")
    if tasks is None:
        return errors
    if not isinstance(tasks, list) or not tasks:
        return ["module.json tasks must be a non-empty list when provided."]

    known_phases = set(phase_ids(manifest))
    task_ids: list[str] = []
    dependencies: dict[str, list[str]] = {}
    seen_ids: set[str] = set()

    for index, task in enumerate(tasks):
        label = f"module.json tasks[{index}]"
        if not isinstance(task, dict):
            errors.append(f"{label} {ERR_OBJECT}")
            continue

        task_id = str(task.get("id", "")).strip()
        if not TASK_ID_PATTERN.match(task_id):
            errors.append(f"{label}.id {ERR_LOWER_ID}")
        elif task_id in seen_ids:
            errors.append(f"{label}.id duplicates task id '{task_id}'.")
        elif task_id:
            seen_ids.add(task_id)
            task_ids.append(task_id)

        summary = str(task.get("summary", "")).strip()
        if not summary:
            errors.append(f"{label}.summary {ERR_NON_EMPTY_STRING}")

        phase = task.get("phase")
        if phase is not None:
            phase_text = str(phase).strip()
            if phase_text not in known_phases:
                errors.append(f"{label}.phase references unknown phase '{phase_text}'.")

        depends_on = task.get("depends_on", [])
        dep_values: list[str] = []
        if not isinstance(depends_on, list):
            errors.append(f"{label}.depends_on {ERR_STRING_LIST}")
        else:
            for dep_index, dep in enumerate(depends_on):
                if isinstance(dep, str) and dep.strip():
                    dep_values.append(dep.strip())
                else:
                    errors.append(f"{label}.depends_on[{dep_index}] {ERR_NON_EMPTY_STRING}")
        if len(dep_values) != len(set(dep_values)):
            errors.append(f"{label}.depends_on contains duplicate task ids.")
        if task_id and task_id in dep_values:
            errors.append(f"{label}.depends_on must not include itself.")
        dependencies[task_id] = dep_values

    known_tasks = set(task_ids)
    for task_id, dep_values in dependencies.items():
        if not task_id:
            continue
        for dep in dep_values:
            if dep not in known_tasks:
                errors.append(f"module.json tasks '{task_id}' depends_on unknown task '{dep}'.")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str, stack: list[str]) -> None:
        if task_id in visited:
            return
        if task_id in visiting:
            cycle = [*stack[stack.index(task_id):], task_id] if task_id in stack else [*stack, task_id]
            errors.append(f"module.json tasks dependency cycle: {' -> '.join(cycle)}.")
            return
        visiting.add(task_id)
        for dep in dependencies.get(task_id, []):
            if dep in known_tasks:
                visit(dep, [*stack, task_id])
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in task_ids:
        visit(task_id, [])

    return errors


def validate_context_evidence_metadata(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    label = "module.json context_evidence"
    value = manifest.get("context_evidence")
    if value is None:
        return [f"{label} is required so workflow start/resume/finish can prove bounded context evidence."]
    if not isinstance(value, dict):
        return [f"{label} {ERR_OBJECT}"]
    if value.get("required") is not True:
        errors.append(f"{label}.required must be true.")
    for field in ("start_queries", "resume_queries", "finish_queries"):
        queries = value.get(field)
        if not isinstance(queries, list) or not queries:
            errors.append(f"{label}.{field} must be a non-empty list.")
            continue
        seen_ids: set[str] = set()
        for index, query in enumerate(queries):
            query_label = f"{label}.{field}[{index}]"
            if not isinstance(query, dict):
                errors.append(f"{query_label} {ERR_OBJECT}")
                continue
            query_id = str(query.get("id", "")).strip()
            if not CONTEXT_EVIDENCE_QUERY_ID_PATTERN.match(query_id):
                errors.append(f"{query_label}.id {ERR_LOWER_ID}")
            elif query_id in seen_ids:
                errors.append(f"{query_label}.id duplicates query id '{query_id}'.")
            seen_ids.add(query_id)
            question = str(query.get("question", "")).strip()
            if not question:
                errors.append(f"{query_label}.question {ERR_NON_EMPTY_STRING}")
            scope = str(query.get("scope", "repo")).strip()
            if scope not in CONTEXT_EVIDENCE_SCOPES:
                errors.append(
                    f"{query_label}.scope must be one of: "
                    f"{', '.join(sorted(CONTEXT_EVIDENCE_SCOPES))}."
                )
            if "required" in query and not isinstance(query.get("required"), bool):
                errors.append(f"{query_label}.required {ERR_BOOL_PROVIDED}")
            paths = query.get("fallback_paths")
            if query.get("required", True) is True and (not isinstance(paths, list) or not paths):
                errors.append(f"{query_label}.fallback_paths must be a non-empty list for required queries.")
            elif "fallback_paths" in query:
                if not isinstance(paths, list) or not paths:
                    errors.append(f"{query_label}.fallback_paths must be a non-empty list when provided.")
                else:
                    for path_index, path in enumerate(paths):
                        if not isinstance(path, str) or not path.strip():
                            errors.append(f"{query_label}.fallback_paths[{path_index}] {ERR_NON_EMPTY_STRING}")
    return errors


def validate_input_schema(manifest: dict[str, Any]) -> list[str]:
    schema = manifest.get("input_schema")
    if schema is None:
        return []
    if not isinstance(schema, dict):
        return [f"module.json input_schema {ERR_OBJECT_PROVIDED}"]
    errors: list[str] = []
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        errors.append("module.json input_schema.properties must be a non-empty object.")
        properties = {}
    required = schema.get("required", [])
    if required is not None and not isinstance(required, list):
        errors.append("module.json input_schema.required must be a list when provided.")
        required = []
    property_names = set()
    for name, spec in properties.items():
        name_text = str(name)
        property_names.add(name_text)
        label = f"module.json input_schema.properties.{name_text}"
        if not STATE_FIELD_PATTERN.match(name_text):
            errors.append(f"{label} must use snake_case.")
        if not isinstance(spec, dict):
            errors.append(f"{label} {ERR_OBJECT}")
            continue
        type_value = str(spec.get("type", "")).strip()
        if type_value not in INPUT_SCHEMA_TYPES:
            errors.append(f"{label}.type must be one of: {', '.join(sorted(INPUT_SCHEMA_TYPES))}.")
        if type_value == "enum":
            values = spec.get("values")
            if not isinstance(values, list) or not values or not all(isinstance(item, str) and item.strip() for item in values):
                errors.append(f"{label}.values must be a non-empty list of strings for enum inputs.")
        if "description" in spec and (not isinstance(spec.get("description"), str) or not str(spec.get("description")).strip()):
            errors.append(f"{label}.description {ERR_NON_EMPTY_STRING_PROVIDED}")
    for item in required or []:
        if not isinstance(item, str) or not item.strip():
            errors.append("module.json input_schema.required values must be non-empty strings.")
        elif item not in property_names:
            errors.append(f"module.json input_schema.required references unknown input '{item}'.")
    return errors


def validate_gate_metadata(manifest: dict[str, Any]) -> list[str]:
    gates = manifest.get("gates")
    if gates is None:
        return []
    if not isinstance(gates, list):
        return ["module.json gates must be a list when provided."]
    errors: list[str] = []
    seen: set[str] = set()
    for index, gate in enumerate(gates):
        label = f"module.json gates[{index}]"
        if not isinstance(gate, dict):
            errors.append(f"{label} {ERR_OBJECT}")
            continue
        gate_id = str(gate.get("id", "")).strip()
        if not HOOK_ID_PATTERN.match(gate_id):
            errors.append(f"{label}.id {ERR_LOWER_ID}")
        elif gate_id in seen:
            errors.append(f"{label}.id duplicates gate id '{gate_id}'.")
        seen.add(gate_id)
        gate_type = str(gate.get("type", "")).strip()
        if gate_type not in GATE_TYPES:
            errors.append(f"{label}.type must be one of: {', '.join(sorted(GATE_TYPES))}.")
        for field in ("summary", "evidence"):
            if not isinstance(gate.get(field), str) or not str(gate.get(field)).strip():
                errors.append(f"{label}.{field} {ERR_NON_EMPTY_STRING}")
        if "required" in gate and not isinstance(gate.get("required"), bool):
            errors.append(f"{label}.required {ERR_BOOL_PROVIDED}")
    return errors


def validate_template_layers_metadata(manifest: dict[str, Any]) -> list[str]:
    layers = manifest.get("template_layers")
    if layers is None:
        return []
    if not isinstance(layers, dict):
        return [f"module.json template_layers {ERR_OBJECT_PROVIDED}"]
    errors: list[str] = []
    for field in ("override_roots", "preset_roots"):
        value = layers.get(field, [])
        if value is not None and not isinstance(value, list):
            errors.append(f"module.json template_layers.{field} must be a list when provided.")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if not isinstance(item, str) or not item.strip():
                    errors.append(f"module.json template_layers.{field}[{index}] {ERR_NON_EMPTY_STRING}")
    if "default_template" in layers and (not isinstance(layers.get("default_template"), str) or not str(layers.get("default_template")).strip()):
        errors.append(f"module.json template_layers.default_template {ERR_NON_EMPTY_STRING_PROVIDED}")
    return errors


def validate_branch_policy_metadata(manifest: dict[str, Any]) -> list[str]:
    policy = manifest.get("branch_policy")
    if policy is None:
        return []
    if not isinstance(policy, dict):
        return [f"module.json branch_policy {ERR_OBJECT_PROVIDED}"]
    pattern = policy.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return [f"module.json branch_policy.pattern {ERR_NON_EMPTY_STRING}"]
    try:
        re.compile(pattern)
    except re.error as exc:
        return [f"module.json branch_policy.pattern is not a valid regex: {exc}."]
    return []


def validate_integration_metadata(manifest: dict[str, Any]) -> list[str]:
    integrations = manifest.get("integrations")
    if integrations is None:
        return []
    if not isinstance(integrations, list):
        return ["module.json integrations must be a list when provided."]
    errors: list[str] = []
    for index, item in enumerate(integrations):
        label = f"module.json integrations[{index}]"
        if isinstance(item, str):
            if not HOOK_ID_PATTERN.match(item):
                errors.append(f"{label} {ERR_LOWER_ID}")
            continue
        if not isinstance(item, dict):
            errors.append(f"{label} {ERR_STRING_OR_OBJECT}")
            continue
        integration_id = str(item.get("id", "")).strip()
        if not HOOK_ID_PATTERN.match(integration_id):
            errors.append(f"{label}.id {ERR_LOWER_ID}")
        descriptor = item.get("descriptor")
        if descriptor is not None and (not isinstance(descriptor, str) or not descriptor.strip()):
            errors.append(f"{label}.descriptor {ERR_NON_EMPTY_STRING_PROVIDED}")
    return errors


def validate_global_hooks_manifest(manifest: dict[str, Any], path: Path) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != 1:
        errors.append(f"{path.as_posix()} schema_version must be 1.")
    hooks = manifest.get("hooks")
    if hooks is None:
        return errors
    if not isinstance(hooks, list):
        return errors + [f"{path.as_posix()} hooks must be a list."]

    workflow_script_placeholder_prefix = "python -B automations/{workflow}/scripts/"
    for index, hook in enumerate(hooks):
        label = f"{path.as_posix()} hooks[{index}]"
        if not isinstance(hook, dict):
            errors.append(f"{label} {ERR_OBJECT}")
            continue

        hook_id = str(hook.get("id", "")).strip()
        if not HOOK_ID_PATTERN.match(hook_id):
            errors.append(f"{label}.id {ERR_LOWER_ID}")

        event = str(hook.get("event", "")).strip()
        if event not in WORKFLOW_HOOK_EVENTS:
            errors.append(f"{label}.event {ERR_UNKNOWN_LIFECYCLE} '{event}'.")

        command = str(hook.get("command", "")).strip()
        if not command:
            errors.append(f"{label}.command {ERR_NON_EMPTY_STRING}")
        elif not (
            command.startswith("python -B .agents/manage.py ")
            or command.startswith("python -B .agents/skills/")
            or command.startswith(workflow_script_placeholder_prefix)
        ):
            errors.append(
                f"{label}.command must use `python -B .agents/manage.py ...`, "
                f"`python -B .agents/skills/...`, or `{workflow_script_placeholder_prefix}...`."
            )

        if "required" in hook and not isinstance(hook.get("required"), bool):
            errors.append(f"{label}.required {ERR_BOOL_PROVIDED}")

        if "timeout_seconds" in hook:
            timeout = hook.get("timeout_seconds")
            if not isinstance(timeout, int) or timeout < 1 or timeout > 3600:
                errors.append(f"{label}.timeout_seconds {ERR_TIMEOUT}")

        if "evidence_path" in hook:
            evidence_path = str(hook.get("evidence_path", "")).strip()
            if (
                not evidence_path
                or Path(evidence_path).is_absolute()
                or ".." in Path(evidence_path).parts
            ):
                errors.append(f"{label}.evidence_path {ERR_RUN_RELATIVE_PATH}")
            if "workflow hook-audit" in command and "--format json" in command and evidence_path.endswith(".txt"):
                errors.append(f"{label}.evidence_path {ERR_HOOK_AUDIT_JSON}")

    return errors


def validate_manifest(
    module_dir: Path, manifest: dict[str, Any], skills: set[str]
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    label = "module.json"
    related_key = "related_modules"

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
        "phases",
        "inputs",
        "outputs",
        "commands",
        "related_modules",
        "risk",
        "external_access",
        "validation",
    }
    missing = required - set(manifest)
    if missing:
        errors.append(
            f"{label} is missing required keys: {', '.join(sorted(missing))}."
        )

    if manifest.get("kind") != "workflow":
        errors.append("module.json kind must be 'workflow'.")

    automation_id = manifest.get("id")
    if automation_id != module_dir.name:
        errors.append(
            f"{label} id '{automation_id}' must match folder name '{module_dir.name}'."
        )
    if not isinstance(automation_id, str) or not common.SKILL_NAME_PATTERN.match(automation_id):
        errors.append(
            f"{label} id must use lowercase letters, digits, and hyphens, "
            "and be under 64 characters."
        )

    version = manifest.get("version")
    if not isinstance(version, str) or common.semver_tuple(version) is None:
        errors.append(f"{label} version must be a valid SemVer value.")

    summary = manifest.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append(f"{label} summary {ERR_NON_EMPTY_STRING}")

    list_fields = ("owners", "inputs", "outputs", related_key)
    list_fields = (*list_fields, "validation")
    for key in list_fields:
        values = as_non_empty_string_list(manifest.get(key))
        if values is None:
            errors.append(f"{label} {key} {ERR_STRING_LIST}")

    phases = manifest.get("phases")
    if not isinstance(phases, list) or not phases:
        errors.append(f"{label} phases must be a non-empty list.")
    else:
        seen_phases: set[str] = set()
        for index, item in enumerate(phases):
            value = phase_id(item)
            if not value:
                errors.append(f"module.json phases[{index}] must define a phase id.")
                continue
            if not PHASE_PATTERN.match(value):
                errors.append(
                    f"module.json phases[{index}] id '{value}' must use lowercase "
                    "letters, digits, and hyphens."
                )
            if value in seen_phases:
                errors.append(f"module.json phases contains duplicate id '{value}'.")
            seen_phases.add(value)

    related = as_non_empty_string_list(manifest.get(related_key)) or []
    for skill_name in related:
        if skill_name not in skills:
            errors.append(
                f"{label} {related_key} references unknown skill '{skill_name}'."
            )

    risk = manifest.get("risk")
    if not isinstance(risk, dict):
        errors.append(f"{label} risk {ERR_OBJECT}")
        risk = {}
    else:
        missing_risks = common.RISK_KEYS - set(risk)
        if missing_risks:
            errors.append(
                f"{label} risk is missing keys: "
                f"{', '.join(sorted(missing_risks))}."
            )
        for key in sorted(common.RISK_KEYS & set(risk)):
            if not isinstance(risk.get(key), bool):
                errors.append(f"{label} risk.{key} must be true or false.")
        profile = risk.get("profile")
        if profile is not None:
            if not isinstance(profile, str) or profile not in common.RISK_PROFILES:
                errors.append(
                    f"{label} risk.profile must be one of: "
                    f"{', '.join(sorted(common.RISK_PROFILES))}."
                )
            else:
                required_profile = common.required_risk_profile(risk)
                if not common.risk_profile_covers(profile, required_profile):
                    errors.append(
                        f"{label} risk.profile '{profile}' does not cover "
                        f"declared risk behavior; minimum profile is '{required_profile}'."
                    )

    external_access = manifest.get("external_access")
    if not isinstance(external_access, dict):
        errors.append(f"{label} external_access {ERR_OBJECT}")
        normalized_access = normalize_external_access(None)
    else:
        normalized_access = normalize_external_access(external_access)
        if "source_systems" not in external_access and "systems" not in external_access:
            errors.append(f"{label} external_access.source_systems is required.")
        if (
            "credential_expectations" not in external_access
            and "credentials" not in external_access
        ):
            errors.append(
                f"{label} external_access.credential_expectations is required."
            )
        if (
            "data_copied_locally" not in external_access
            and "copied_data" not in external_access
        ):
            errors.append(f"{label} external_access.data_copied_locally is required.")
        if (
            "attachments_retrieved" not in external_access
            and "retrieves_attachments" not in external_access
        ):
            errors.append(f"{label} external_access.attachments_retrieved is required.")

    signals = detect_external_signals(module_dir)
    for signal in signals:
        category = str(signal["category"])
        location = f"{signal['path']}:{signal['line']}"
        if category in {"network", "uploads", "attachments"} and not normalized_access[
            "source_systems"
        ]:
            errors.append(
                f"{location} has external-access evidence ({signal['signal']}) but "
                f"{label} external_access.source_systems is empty."
            )
        if category == "credentials" and not normalized_access["credential_expectations"]:
            errors.append(
                f"{location} has credential evidence ({signal['signal']}) but "
                f"{label} external_access.credential_expectations is empty."
            )
        if category == "attachments" and not normalized_access["attachments_retrieved"]:
            errors.append(
                f"{location} references attachments but "
                f"{label} external_access.attachments_retrieved is false."
            )
        risk_key = {
            "attachments": "network",
            "credentials": "credentials",
            "network": "network",
            "uploads": "uploads",
        }.get(category)
        if risk_key and risk.get(risk_key) is not True:
            errors.append(
                f"{location} has {category} evidence ({signal['signal']}) but "
                f"{label} risk.{risk_key} is false."
            )

    errors.extend(validate_local_ai_metadata(manifest.get("local_ai"), f"{label} local_ai"))
    errors.extend(validate_routing_metadata(manifest))
    errors.extend(validate_phase_lifecycle_metadata(manifest))
    errors.extend(validate_worker_profiles(manifest))
    errors.extend(validate_workflow_hooks(manifest, module_dir))
    errors.extend(validate_task_graph(manifest))
    errors.extend(validate_context_evidence_metadata(manifest))
    errors.extend(validate_input_schema(manifest))
    errors.extend(validate_gate_metadata(manifest))
    errors.extend(validate_template_layers_metadata(manifest))
    errors.extend(validate_branch_policy_metadata(manifest))
    errors.extend(validate_integration_metadata(manifest))

    return errors, warnings
