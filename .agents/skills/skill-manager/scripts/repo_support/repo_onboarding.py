"""Beginner-focused onboarding helpers for the repository launcher."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from repo_support import repo_harness_install
from repo_support import repo_harness_paths
from repo_support import repo_harness_profiles
from repo_support import repo_setup


PROJECT_CONTEXT_REL = Path("docs/project/project-context.md")
PROJECT_CONTEXT_JSON_REL = Path("docs/project/project-context.json")
VALIDATION_MANIFEST_REL = Path("docs/project/validation/validation-manifest.json")
PROJECT_CONTEXT_REVIEW_DIR_REL = Path("docs/project/review")
PROJECT_CONTEXT_REVIEW_MD_REL = PROJECT_CONTEXT_REVIEW_DIR_REL / "project-context-review.md"
PROJECT_CONTEXT_REVIEW_JSON_REL = PROJECT_CONTEXT_REVIEW_DIR_REL / "project-context-review.json"
PROJECT_CONTEXT_REVIEW_BEGIN = "<!-- BEGIN PROJECT CONTEXT REVIEW ANSWERS -->"
PROJECT_CONTEXT_REVIEW_END = "<!-- END PROJECT CONTEXT REVIEW ANSWERS -->"
PROJECT_CONTEXT_REVIEW_FORMAT_MARKER = "<!-- PROJECT CONTEXT REVIEW FORMAT: structured-v1 -->"
REVIEW_QUESTION_BEGIN_PREFIX = '<!-- BEGIN PROJECT CONTEXT REVIEW QUESTION id="'
REVIEW_QUESTION_END_PREFIX = '<!-- END PROJECT CONTEXT REVIEW QUESTION id="'
REVIEW_ANSWER_BEGIN_PREFIX = '<!-- BEGIN PROJECT CONTEXT REVIEW ANSWER id="'
REVIEW_ANSWER_END_PREFIX = '<!-- END PROJECT CONTEXT REVIEW ANSWER id="'
REVIEW_MARKER_SUFFIX = '" -->'
REVIEW_EXPECTED_IDS_PREFIX = "- Expected question ids:"
REVIEW_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


CONTEXT_FACTS = (
    {
        "id": "stack-runtime",
        "label": "stack/runtime",
        "question": "What stack, runtime versions, SDKs, and package managers should agents assume?",
        "evidence_paths": [PROJECT_CONTEXT_REL.as_posix(), PROJECT_CONTEXT_JSON_REL.as_posix()],
        "suggested_answer_source": "package manifests, runtime config, lockfiles, and project README",
    },
    {
        "id": "validation-commands",
        "label": "run/test commands",
        "question": "Which restore, build, run, test, lint, and UI validation commands are authoritative?",
        "evidence_paths": [PROJECT_CONTEXT_REL.as_posix(), VALIDATION_MANIFEST_REL.as_posix()],
        "suggested_answer_source": "validation manifest, package scripts, solution files, CI, and README",
    },
    {
        "id": "generated-boundaries",
        "label": "generated-file boundaries",
        "question": "Which files or folders are generated and should not be edited by hand?",
        "evidence_paths": [PROJECT_CONTEXT_REL.as_posix(), ".gitignore"],
        "suggested_answer_source": "project context, generator docs, build output folders, and repository ignore rules",
    },
    {
        "id": "external-systems",
        "label": "external systems",
        "question": "Which external systems, APIs, queues, feeds, or service credentials are part of normal work?",
        "evidence_paths": [PROJECT_CONTEXT_REL.as_posix(), "README.md"],
        "suggested_answer_source": "project context, app configuration names, integration docs, and README",
    },
    {
        "id": "persistence",
        "label": "persistence",
        "question": "What persistence, database, migration, seed-data, or storage ownership should workflows respect?",
        "evidence_paths": [PROJECT_CONTEXT_REL.as_posix()],
        "suggested_answer_source": "project context, migration folders, ORM config, and storage documentation",
    },
    {
        "id": "ci",
        "label": "CI",
        "question": "Which CI checks or release gates should local validation compare against?",
        "evidence_paths": [PROJECT_CONTEXT_REL.as_posix(), ".github/workflows"],
        "suggested_answer_source": "CI workflow files, pipeline docs, and project context validation notes",
    },
    {
        "id": "secrets-config",
        "label": "secrets/configuration",
        "question": "Which config files, environment variables, and secret-handling rules are safe to document?",
        "evidence_paths": [PROJECT_CONTEXT_REL.as_posix(), ".env.example", "appsettings.json"],
        "suggested_answer_source": "safe example config, project context security notes, and environment variable docs",
    },
)
KNOWN_REVIEW_FACT_IDS = {str(fact["id"]) for fact in CONTEXT_FACTS} | {
    "project-goal-alignment",
    "dotnet-nuget-feed-policy",
}


POST_KICKOFF_COMMANDS = (
    (["setup"], 300),
    (["setup", "--check"], 180),
    (["status", "--fast"], 120),
)


CommandRunner = Callable[[Path, list[str], int], dict[str, object]]


def initialization_preflight(
    target: Path,
    *,
    operation: str,
) -> tuple[Path, list[dict[str, str]]]:
    guard = repo_harness_paths.HarnessPathGuard(target, label="initialization-target")
    rows: list[dict[str, str]] = []
    for error in guard.audit_existing_tree(operation=operation):
        repo_harness_paths.add_unsafe_path(rows, error)
    return guard.root, repo_harness_paths.sorted_unsafe_paths(rows)


def blocked_initialization_install_report(
    source: Path,
    target: Path,
    *,
    profile: str,
    unsafe_paths: list[dict[str, str]],
) -> dict[str, object]:
    issues = [f"unsafe-path-blocked: {len(unsafe_paths)} unsafe initialization path(s) rejected"]
    return {
        "schema_version": 1,
        "tool": "install-harness",
        "ok": False,
        "status": "unsafe-path-blocked",
        "operation": "install",
        "source_root": str(source),
        "target_root": str(target),
        "profile": {"name": profile, "features": []},
        "resolved_features": [],
        "unsafe_paths": unsafe_paths,
        "clean_state": False,
        "summary": {"copied_files": 0, "post_install_steps": 0},
        "copied": [],
        "collisions": [],
        "post_install": [],
        "issues": issues,
    }


def read_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def context_has_stack(text: str, data: dict[str, object]) -> bool:
    technologies = data.get("technologies")
    if isinstance(technologies, list) and any(str(item).strip() for item in technologies):
        return True
    lowered = text.lower()
    stack_markers = (
        "## technologies",
        "## technology stack",
        "runtime and sdk",
        "runtime versions",
        "sdk versions",
        "target framework",
        "package managers",
        "confirmed stack",
    )
    return any(marker in lowered for marker in stack_markers) and "no major framework signals detected" not in lowered


def dotnet_context(data: dict[str, object]) -> dict[str, object]:
    value = data.get("dotnet_context")
    return value if isinstance(value, dict) else {}


def dotnet_context_status(data: dict[str, object]) -> str:
    value = str(dotnet_context(data).get("status") or "")
    return value or "not-detected"


def dotnet_context_fact_status(data: dict[str, object], fact_id: str) -> str:
    facts = dotnet_context(data).get("context_facts")
    if not isinstance(facts, list):
        return ""
    for fact in facts:
        if isinstance(fact, dict) and fact.get("id") == fact_id:
            return str(fact.get("status") or "")
    return ""


def dotnet_context_fact(data: dict[str, object], fact_id: str) -> dict[str, object]:
    facts = dotnet_context(data).get("context_facts")
    if not isinstance(facts, list):
        return {}
    for fact in facts:
        if isinstance(fact, dict) and fact.get("id") == fact_id:
            return fact
    return {}


def dotnet_context_has_fact(data: dict[str, object], fact_id: str) -> bool:
    status = dotnet_context_fact_status(data, fact_id)
    return bool(status and status != "missing")


def dotnet_private_feeds(data: dict[str, object]) -> bool:
    nuget = dotnet_context(data).get("nuget")
    return isinstance(nuget, dict) and bool(nuget.get("private_feeds_detected"))


def dotnet_validation_candidates(data: dict[str, object]) -> list[object]:
    candidates = dotnet_context(data).get("validation_candidates")
    return candidates if isinstance(candidates, list) else []


def dotnet_fact_evidence_paths(data: dict[str, object], fact_id: str) -> list[str]:
    dotnet = dotnet_context(data)
    fact = dotnet_context_fact(data, fact_id)
    evidence = fact.get("evidence_paths")
    paths = [str(item) for item in evidence if isinstance(item, str)] if isinstance(evidence, list) else []
    if fact_id == "persistence":
        persistence = dotnet.get("persistence")
        if isinstance(persistence, dict):
            db_contexts = persistence.get("db_contexts")
            if isinstance(db_contexts, list):
                paths.extend(str(item.get("path")) for item in db_contexts if isinstance(item, dict) and item.get("path"))
            migration_paths = persistence.get("migration_paths")
            if isinstance(migration_paths, list):
                paths.extend(str(item) for item in migration_paths if isinstance(item, str))
    if fact_id == "ci":
        ci = dotnet.get("ci")
        if isinstance(ci, dict):
            workflow_paths = ci.get("workflow_paths")
            if isinstance(workflow_paths, list):
                paths.extend(str(item) for item in workflow_paths if isinstance(item, str))
    if fact_id in {"secrets-config", "external-systems"}:
        configuration = dotnet.get("configuration")
        if isinstance(configuration, dict):
            appsettings = configuration.get("appsettings_files")
            if isinstance(appsettings, list):
                paths.extend(str(item.get("path")) for item in appsettings if isinstance(item, dict) and item.get("path"))
        nuget = dotnet.get("nuget")
        if isinstance(nuget, dict):
            config_paths = nuget.get("config_paths")
            if isinstance(config_paths, list):
                paths.extend(str(item) for item in config_paths if isinstance(item, str))
    return sorted(dict.fromkeys(item for item in paths if item and item != "None"))


def validation_commands(manifest: dict[str, object]) -> list[object]:
    commands = manifest.get("commands")
    return commands if isinstance(commands, list) else []


def context_fact_present(fact_id: str, text: str, data: dict[str, object], manifest: dict[str, object]) -> bool:
    lowered = text.lower()
    if fact_id == "stack-runtime":
        return context_has_stack(text, data) or dotnet_context_has_fact(data, "stack-runtime") or bool(dotnet_context(data).get("projects"))
    if fact_id == "validation-commands":
        documented_commands = any(marker in lowered for marker in ("## validation commands", "## local run commands", "## run commands", "| command |", "`python -b", "`dotnet ", "`npm ", "`pnpm "))
        return (bool(validation_commands(manifest)) and "record project-specific commands" not in lowered) or documented_commands or dotnet_context_has_fact(data, "validation-commands") or bool(dotnet_validation_candidates(data))
    if fact_id == "generated-boundaries":
        return any(marker in lowered for marker in ("generated files and boundaries", "generated-file boundaries", "generated files and do not edit", "do not edit", "generated folders/files"))
    if fact_id == "external-systems":
        return dotnet_context_has_fact(data, "external-systems") or any(term in lowered for term in ("external systems", "external services", "api", "queue", "service bus", "feed", "sonarqube", "azure devops"))
    if fact_id == "persistence":
        dotnet = dotnet_context(data)
        persistence = dotnet.get("persistence") if isinstance(dotnet.get("persistence"), dict) else {}
        return (
            dotnet_context_has_fact(data, "persistence")
            or bool(persistence.get("db_contexts") or persistence.get("provider_packages") or persistence.get("migration_paths"))
            or any(term in lowered for term in ("persistence", "database", "migration", "data store", "storage"))
        )
    if fact_id == "ci":
        dotnet = dotnet_context(data)
        ci = dotnet.get("ci") if isinstance(dotnet.get("ci"), dict) else {}
        return (
            dotnet_context_has_fact(data, "ci")
            or bool(ci.get("workflow_paths") or ci.get("dotnet_commands"))
            or any(term in lowered for term in ("ci", "github actions", "pipeline", "release gate", "workflow files"))
        )
    if fact_id == "secrets-config":
        return dotnet_context_has_fact(data, "secrets-config") or any(term in lowered for term in ("security and configuration", "secret", "credential", "environment", "configuration", ".env"))
    return False


def existing_evidence_paths(target: Path, paths: list[str]) -> list[str]:
    present: list[str] = []
    for item in paths:
        if (target / item).exists():
            present.append(item)
    return present or paths[:1]


def goal_alignment_fact(target: Path, project_goal: str) -> dict[str, object]:
    return {
        "id": "project-goal-alignment",
        "label": "project goal alignment",
        "status": "review-needed",
        "question": f"Does the project context accurately reflect this chat-provided goal: {project_goal}",
        "evidence_paths": existing_evidence_paths(target, [PROJECT_CONTEXT_REL.as_posix()]),
        "blocking": True,
        "suggested_answer_source": "compare the chat-provided goal with the reviewed project context",
    }


def dotnet_private_feed_fact(target: Path, data: dict[str, object]) -> dict[str, object] | None:
    if not dotnet_private_feeds(data):
        return None
    nuget = dotnet_context(data).get("nuget")
    config_paths = []
    if isinstance(nuget, dict):
        config_paths = [str(item) for item in nuget.get("config_paths", []) if isinstance(item, str)]
    evidence_paths = [*config_paths, PROJECT_CONTEXT_JSON_REL.as_posix(), PROJECT_CONTEXT_REL.as_posix()]
    if not evidence_paths:
        evidence_paths = [PROJECT_CONTEXT_JSON_REL.as_posix()]
    return {
        "id": "dotnet-nuget-feed-policy",
        "label": ".NET NuGet/feed policy",
        "status": "review-needed",
        "question": "Which private/internal NuGet feed credentials, source mapping rules, and restore prerequisites are required before running restore/build/test commands?",
        "evidence_paths": evidence_paths,
        "blocking": True,
        "suggested_answer_source": "repo-local NuGet.config, Directory.Packages.props, CI restore steps, developer onboarding docs, and approved secret-management policy",
    }


def context_fact_reviews(
    target: Path,
    text: str,
    data: dict[str, object],
    manifest: dict[str, object],
    *,
    project_goal: str,
) -> list[dict[str, object]]:
    reviews: list[dict[str, object]] = []
    for fact in CONTEXT_FACTS:
        fact_id = str(fact["id"])
        present = context_fact_present(fact_id, text, data, manifest)
        evidence_paths = existing_evidence_paths(target, list(fact.get("evidence_paths", [])))
        dotnet_evidence_paths = dotnet_fact_evidence_paths(data, fact_id)
        if dotnet_evidence_paths:
            evidence_paths = sorted(dict.fromkeys([*evidence_paths, *dotnet_evidence_paths]))
        reviews.append(
            {
                "id": fact_id,
                "label": str(fact["label"]),
                "status": "present" if present else "missing",
                "question": str(fact["question"]),
                "evidence_paths": evidence_paths,
                "blocking": not present,
                "suggested_answer_source": str(fact.get("suggested_answer_source", "review project files and update the context")),
            }
        )
    if project_goal:
        reviews.append(goal_alignment_fact(target, project_goal))
    private_feed = dotnet_private_feed_fact(target, data)
    if private_feed:
        reviews.append(private_feed)
    return reviews


def review_artifact_payload(report: dict[str, object]) -> dict[str, object]:
    facts = report.get("fact_reviews") if isinstance(report.get("fact_reviews"), list) else []
    answer_slots = {
        str(fact.get("id")): ""
        for fact in facts
        if isinstance(fact, dict) and fact.get("status") != "present"
    }
    return {
        "schema_version": 1,
        "tool": "project-context-review-artifact",
        "target": str(report.get("target") or ""),
        "project_goal": str(report.get("project_goal") or ""),
        "context_status": str(report.get("status") or ""),
        "review_required": bool(report.get("review_required")),
        "fact_reviews": facts,
        "answer_slots": answer_slots,
        "canonical_context": PROJECT_CONTEXT_REL.as_posix(),
        "merge_policy": "review artifact only; do not overwrite canonical project context from this command",
    }


def render_review_artifact_markdown(payload: dict[str, object]) -> str:
    lines = [
        "# Project Context Review",
        "",
        PROJECT_CONTEXT_REVIEW_FORMAT_MARKER,
        "",
        f"- Target: `{payload.get('target')}`",
        f"- Context status: {payload.get('context_status')}",
        f"- Canonical context: `{payload.get('canonical_context')}`",
        "- Merge policy: review answers here first; update canonical context only through a later explicit review step.",
    ]
    if payload.get("project_goal"):
        lines.append(f"- Project goal: {payload.get('project_goal')}")
    facts = payload.get("fact_reviews") if isinstance(payload.get("fact_reviews"), list) else []
    expected_ids = [
        str(fact.get("id"))
        for fact in facts
        if isinstance(fact, dict) and fact.get("status") != "present" and fact.get("id")
    ]
    lines.extend(
        [
            f"- Expected question ids: {', '.join(f'`{fact_id}`' for fact_id in expected_ids)}",
            "",
            "## Questions To Answer",
            "",
        ]
    )
    for fact in facts:
        if not isinstance(fact, dict) or fact.get("status") == "present":
            continue
        lines.extend(
            [
                f'{REVIEW_QUESTION_BEGIN_PREFIX}{fact.get("id")}{REVIEW_MARKER_SUFFIX}',
                f"### {fact.get('label')}",
                "",
                f"- Fact id: `{fact.get('id')}`",
                f"- Status: {fact.get('status')}",
                f"- Blocking: {str(bool(fact.get('blocking'))).lower()}",
                f"- Evidence paths: {', '.join(f'`{item}`' for item in fact.get('evidence_paths', []) if isinstance(item, str))}",
                f"- Suggested answer source: {fact.get('suggested_answer_source')}",
                f"- Question: {fact.get('question') or ''}",
                "",
                f'{REVIEW_ANSWER_BEGIN_PREFIX}{fact.get("id")}{REVIEW_MARKER_SUFFIX}',
                "<!-- Replace this comment with the reviewed answer. -->",
                f'{REVIEW_ANSWER_END_PREFIX}{fact.get("id")}{REVIEW_MARKER_SUFFIX}',
                f'{REVIEW_QUESTION_END_PREFIX}{fact.get("id")}{REVIEW_MARKER_SUFFIX}',
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_review_artifacts(target: Path, report: dict[str, object]) -> dict[str, object]:
    review_dir = target / PROJECT_CONTEXT_REVIEW_DIR_REL
    review_dir.mkdir(parents=True, exist_ok=True)
    payload = review_artifact_payload(report)
    json_path = target / PROJECT_CONTEXT_REVIEW_JSON_REL
    md_path = target / PROJECT_CONTEXT_REVIEW_MD_REL
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    md_path.write_text(render_review_artifact_markdown(payload), encoding="utf-8", newline="\n")
    return {
        "written": [PROJECT_CONTEXT_REVIEW_JSON_REL.as_posix(), PROJECT_CONTEXT_REVIEW_MD_REL.as_posix()],
        "paths": {
            "json": PROJECT_CONTEXT_REVIEW_JSON_REL.as_posix(),
            "markdown": PROJECT_CONTEXT_REVIEW_MD_REL.as_posix(),
        },
    }


def target_relative_path(target: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(target.resolve(strict=False)).as_posix()
    except ValueError:
        return str(path)


def resolve_review_artifact_path(target: Path, review: Path | None) -> Path:
    if review is None:
        markdown = target / PROJECT_CONTEXT_REVIEW_MD_REL
        candidate = markdown if markdown.exists() else target / PROJECT_CONTEXT_REVIEW_JSON_REL
    elif review.expanduser().is_absolute():
        candidate = review.expanduser()
    else:
        candidate = target / review
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(target.resolve(strict=False))
    except ValueError as exc:
        raise ValueError(f"review artifact must be inside target project: {resolved}") from exc
    return resolved


def validate_target_local_output(target: Path, path: Path) -> None:
    target_root = target.resolve(strict=False)
    candidates = [path.parent.resolve(strict=False)]
    if path.exists() or path.is_symlink():
        candidates.append(path.resolve(strict=False))
    for candidate in candidates:
        try:
            candidate.relative_to(target_root)
        except ValueError as exc:
            raise ValueError(f"output path must stay inside target project: {path}") from exc


def stage_bytes_file(path: Path, content: bytes) -> Path:
    staged_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            staged_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            return staged_path
    except BaseException as exc:
        if staged_path is not None:
            try:
                staged_path.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                exc.add_note(f"cleanup failed while removing staged output for {path}: {cleanup_exc}")
        raise


def stage_text_file(path: Path, content: str) -> Path:
    return stage_bytes_file(path, content.encode("utf-8"))


def atomic_write_text_files(target: Path, writes: list[tuple[Path, str]]) -> None:
    destinations = [path for path, _content in writes]
    if len(destinations) != len(set(destinations)):
        raise ValueError("atomic output destinations must be unique")
    for destination in destinations:
        validate_target_local_output(target, destination)

    staged: dict[Path, Path] = {}
    originals: dict[Path, bytes | None] = {}
    committed: list[Path] = []
    created_dirs: list[Path] = []
    try:
        for destination in destinations:
            missing_dirs: list[Path] = []
            parent = destination.parent
            while not parent.exists():
                missing_dirs.append(parent)
                parent = parent.parent
            for directory in reversed(missing_dirs):
                directory.mkdir()
                created_dirs.append(directory)
            validate_target_local_output(target, destination)
        for destination, content in writes:
            originals[destination] = destination.read_bytes() if destination.exists() else None
            staged[destination] = stage_text_file(destination, content)
        for destination in destinations:
            os.replace(staged[destination], destination)
            staged.pop(destination, None)
            committed.append(destination)
    except BaseException as primary_exc:
        for destination in reversed(committed):
            original = originals[destination]
            try:
                if original is None:
                    destination.unlink(missing_ok=True)
                else:
                    rollback = stage_bytes_file(destination, original)
                    try:
                        os.replace(rollback, destination)
                    finally:
                        try:
                            rollback.unlink(missing_ok=True)
                        except OSError as cleanup_exc:
                            primary_exc.add_note(
                                "cleanup failed while removing rollback temp for "
                                f"{target_relative_path(target, destination)}: {cleanup_exc}"
                            )
            except BaseException as cleanup_exc:
                primary_exc.add_note(
                    "cleanup failed while restoring output "
                    f"{target_relative_path(target, destination)}: {cleanup_exc}"
                )
                for note in getattr(cleanup_exc, "__notes__", ()):
                    primary_exc.add_note(str(note))
        for destination, staged_path in staged.items():
            try:
                staged_path.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                primary_exc.add_note(
                    "cleanup failed while removing staged output for "
                    f"{target_relative_path(target, destination)}: {cleanup_exc}"
                )
        staged.clear()
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError as cleanup_exc:
                primary_exc.add_note(
                    "cleanup failed while removing created output directory "
                    f"{target_relative_path(target, directory)}: {cleanup_exc}"
                )
        raise


def review_marker_id(line: str, prefix: str) -> str:
    stripped = line.strip()
    if not stripped.startswith(prefix) or not stripped.endswith(REVIEW_MARKER_SUFFIX):
        return ""
    return stripped[len(prefix) : -len(REVIEW_MARKER_SUFFIX)].strip()


def markdown_review_payload(path: Path) -> tuple[dict[str, object], list[str]]:
    text = read_text(path)
    issues: list[str] = []
    questions: dict[str, dict[str, object]] = {}
    answers: dict[str, str] = {}
    question_order: list[str] = []
    active_question = ""
    active_answer = ""
    answer_lines: list[str] = []
    project_goal = ""
    canonical_context = PROJECT_CONTEXT_REL.as_posix()
    declared_expected_ids: list[str] = []
    expected_ids_declaration_count = 0

    for line_number, line in enumerate(text.splitlines(), start=1):
        question_begin = review_marker_id(line, REVIEW_QUESTION_BEGIN_PREFIX)
        question_end = review_marker_id(line, REVIEW_QUESTION_END_PREFIX)
        answer_begin = review_marker_id(line, REVIEW_ANSWER_BEGIN_PREFIX)
        answer_end = review_marker_id(line, REVIEW_ANSWER_END_PREFIX)
        if question_begin:
            if not REVIEW_ID_PATTERN.fullmatch(question_begin):
                issues.append(f"unknown or invalid question id `{question_begin}` at line {line_number}")
            if active_question:
                issues.append(f"nested question block `{question_begin}` at line {line_number}")
            if question_begin in questions:
                issues.append(f"duplicate question id `{question_begin}`")
            else:
                questions[question_begin] = {
                    "id": question_begin,
                    "label": question_begin,
                    "question": "",
                    "evidence_paths": [],
                    "status": "reviewed",
                    "blocking": True,
                    "suggested_answer_source": "",
                }
                question_order.append(question_begin)
            active_question = question_begin
            continue
        if answer_begin:
            if active_answer:
                issues.append(f"nested answer block `{answer_begin}` at line {line_number}")
            if answer_begin in answers:
                issues.append(f"duplicate answer id `{answer_begin}`")
            if not active_question or answer_begin != active_question:
                issues.append(f"unknown answer id `{answer_begin}` at line {line_number}")
            active_answer = answer_begin
            answer_lines = []
            continue
        if answer_end:
            if not active_answer or answer_end != active_answer:
                issues.append(f"answer end id `{answer_end}` does not match the open answer block")
            else:
                answer = "\n".join(answer_lines).strip()
                if answer.startswith("<!-- Replace this comment") and answer.endswith("-->"):
                    answer = ""
                if active_answer in answers:
                    issues.append(f"duplicate answer id `{active_answer}`")
                else:
                    answers[active_answer] = answer
            active_answer = ""
            answer_lines = []
            continue
        if question_end:
            if active_answer:
                issues.append(f"question `{question_end}` ended before its answer block closed")
            if not active_question or question_end != active_question:
                issues.append(f"question end id `{question_end}` does not match the open question block")
            active_question = ""
            continue
        if active_answer:
            answer_lines.append(line)
            continue
        if not active_question:
            if line.startswith("- Project goal:"):
                project_goal = line.split(":", 1)[1].strip()
            elif line.startswith("- Canonical context:"):
                canonical_context = line.split(":", 1)[1].strip().strip("`")
            elif line.startswith(REVIEW_EXPECTED_IDS_PREFIX):
                expected_ids_declaration_count += 1
                parsed_ids = re.findall(r"`([^`]+)`", line)
                if len(parsed_ids) != len(set(parsed_ids)):
                    issues.append(f"duplicate expected question id at line {line_number}")
                declared_expected_ids.extend(parsed_ids)
        if active_question:
            question = questions.get(active_question)
            if not isinstance(question, dict):
                continue
            if line.startswith("### "):
                question["label"] = line[4:].strip()
            elif line.startswith("- Question:"):
                question["question"] = line.split(":", 1)[1].strip()
            elif line.startswith("- Evidence paths:"):
                question["evidence_paths"] = re.findall(r"`([^`]+)`", line)
            elif line.startswith("- Status:"):
                question["status"] = line.split(":", 1)[1].strip()
            elif line.startswith("- Blocking:"):
                question["blocking"] = line.split(":", 1)[1].strip().lower() == "true"
            elif line.startswith("- Suggested answer source:"):
                question["suggested_answer_source"] = line.split(":", 1)[1].strip()

    if active_answer:
        issues.append(f"answer block `{active_answer}` is not closed")
    if active_question:
        issues.append(f"question block `{active_question}` is not closed")

    if expected_ids_declaration_count == 0:
        issues.append("structured review is missing the authoritative expected question ids declaration")
    elif expected_ids_declaration_count > 1:
        issues.append("structured review has duplicate expected question ids declarations")
    if expected_ids_declaration_count and not declared_expected_ids:
        issues.append("structured review expected question ids declaration is empty")

    expected_ids = set(declared_expected_ids)
    allowed_ids = KNOWN_REVIEW_FACT_IDS
    question_ids = set(question_order)
    answer_ids = set(answers)
    for fact_id in sorted(expected_ids - question_ids):
        issues.append(f"missing question id `{fact_id}`")
    for fact_id in sorted(expected_ids - allowed_ids):
        issues.append(f"unknown question id `{fact_id}`")
    for fact_id in sorted(question_ids - allowed_ids):
        issues.append(f"unknown question id `{fact_id}`")
    for fact_id in sorted(answer_ids - question_ids):
        issues.append(f"unknown answer id `{fact_id}`")
    for fact_id in sorted(expected_ids - answer_ids):
        issues.append(f"missing answer id `{fact_id}`")
    for fact_id in sorted(fact_id for fact_id in expected_ids & answer_ids if not answers.get(fact_id, "").strip()):
        issues.append(f"missing answer text for id `{fact_id}`")

    normalized_answers: list[dict[str, object]] = []
    normalized_facts: list[dict[str, object]] = []
    for fact_id in question_order:
        parsed = questions.get(fact_id, {})
        fact = {
            "id": fact_id,
            "label": str(parsed.get("label") or fact_id),
            "question": str(parsed.get("question") or ""),
            "evidence_paths": list(parsed.get("evidence_paths", [])),
            "status": str(parsed.get("status") or "reviewed"),
            "blocking": bool(parsed.get("blocking", True)),
            "suggested_answer_source": str(parsed.get("suggested_answer_source") or ""),
        }
        normalized_facts.append(fact)
        if fact_id in answers and answers[fact_id].strip():
            normalized_answers.append({**fact, "answer": answers[fact_id].strip()})

    payload = {
        "schema_version": 1,
        "tool": "project-context-review-evidence",
        "source": PROJECT_CONTEXT_REVIEW_MD_REL.as_posix(),
        "project_goal": project_goal,
        "canonical_context": canonical_context,
        "fact_reviews": normalized_facts,
        "answer_slots": {item["id"]: item["answer"] for item in normalized_answers},
        "answers": normalized_answers,
    }
    return payload, sorted(dict.fromkeys(issues))


def answered_review_facts(payload: dict[str, object]) -> list[dict[str, object]]:
    facts = payload.get("fact_reviews") if isinstance(payload.get("fact_reviews"), list) else []
    facts_by_id = {
        str(fact.get("id")): fact
        for fact in facts
        if isinstance(fact, dict) and fact.get("id")
    }
    answer_slots = payload.get("answer_slots") if isinstance(payload.get("answer_slots"), dict) else {}
    answers: list[dict[str, object]] = []
    for fact_id, raw_answer in answer_slots.items():
        answer = str(raw_answer).strip()
        if not answer:
            continue
        fact = facts_by_id.get(str(fact_id), {})
        evidence = fact.get("evidence_paths") if isinstance(fact.get("evidence_paths"), list) else []
        answers.append(
            {
                "id": str(fact_id),
                "label": str(fact.get("label") or fact_id),
                "question": str(fact.get("question") or ""),
                "evidence_paths": [str(item) for item in evidence if isinstance(item, str)],
                "answer": answer,
            }
        )
    return answers


def escape_review_marker_text(value: object) -> str:
    text = str(value or "")
    return (
        text.replace(PROJECT_CONTEXT_REVIEW_BEGIN, "&lt;!-- BEGIN PROJECT CONTEXT REVIEW ANSWERS --&gt;")
        .replace(PROJECT_CONTEXT_REVIEW_END, "&lt;!-- END PROJECT CONTEXT REVIEW ANSWERS --&gt;")
    )


def render_applied_review_section(payload: dict[str, object], answers: list[dict[str, object]], review_path: str) -> str:
    lines = [
        PROJECT_CONTEXT_REVIEW_BEGIN,
        "",
        "## Reviewed Project Context Facts",
        "",
        f"- Source review artifact: `{review_path}`",
    ]
    project_goal = str(payload.get("project_goal") or "").strip()
    if project_goal:
        lines.append(f"- Project goal: {escape_review_marker_text(project_goal)}")
    lines.append("")
    for answer in answers:
        evidence_paths = answer.get("evidence_paths") if isinstance(answer.get("evidence_paths"), list) else []
        evidence = ", ".join(f"`{item}`" for item in evidence_paths if isinstance(item, str)) or "`not recorded`"
        lines.extend(
            [
                f"### {escape_review_marker_text(answer.get('label'))}",
                "",
                f"- Fact id: `{escape_review_marker_text(answer.get('id'))}`",
                f"- Question: {escape_review_marker_text(answer.get('question'))}",
                f"- Evidence paths: {evidence}",
                "",
                "Answer:",
                "",
                escape_review_marker_text(answer.get("answer")).strip(),
                "",
            ]
        )
    lines.append(PROJECT_CONTEXT_REVIEW_END)
    return "\n".join(lines).rstrip() + "\n"


def replace_or_append_review_section(text: str, section: str) -> str:
    start = text.find(PROJECT_CONTEXT_REVIEW_BEGIN)
    end = text.find(PROJECT_CONTEXT_REVIEW_END)
    if (start == -1) != (end == -1):
        raise ValueError("existing project-context review answer markers are incomplete")
    section = section.rstrip()
    if start != -1 and end != -1 and start < end:
        end += len(PROJECT_CONTEXT_REVIEW_END)
        return (text[:start].rstrip() + "\n\n" + section + "\n\n" + text[end:].lstrip()).rstrip() + "\n"
    return text.rstrip() + "\n\n" + section + "\n"


def project_context_apply_review_report(
    target: Path,
    *,
    review: Path | None = None,
    apply: bool = False,
) -> dict[str, object]:
    target = target.expanduser().resolve(strict=False)
    context_path = target / PROJECT_CONTEXT_REL
    review_path: Path | None = None
    issues: list[str] = []
    payload: dict[str, object] = {}

    if target.exists() and not target.is_dir():
        issues.append(f"target exists and is not a directory: {target}")
    if not context_path.exists():
        issues.append(f"canonical project context is missing: {PROJECT_CONTEXT_REL.as_posix()}")
    try:
        review_path = resolve_review_artifact_path(target, review)
    except ValueError as exc:
        issues.append(str(exc))
    if review_path is not None:
        if not review_path.exists():
            issues.append(f"review artifact is missing: {target_relative_path(target, review_path)}")
        elif review_path.suffix.lower() == ".md":
            payload, markdown_issues = markdown_review_payload(review_path)
            payload["source"] = target_relative_path(target, review_path)
            issues.extend(markdown_issues)
        else:
            payload = read_json(review_path)
            if not payload:
                issues.append(f"review artifact is not valid JSON: {target_relative_path(target, review_path)}")

    answers = answered_review_facts(payload)
    if payload and not answers:
        issues.append("review artifact has no answered facts in answer_slots")

    review_rel = target_relative_path(target, review_path) if review_path is not None else PROJECT_CONTEXT_REVIEW_JSON_REL.as_posix()
    planned_section = render_applied_review_section(payload, answers, review_rel) if answers else ""
    report = {
        "schema_version": 1,
        "tool": "project-context-apply-review",
        "ok": not issues,
        "status": "blocked" if issues else "planned",
        "target": str(target),
        "apply": apply,
        "paths": {
            "canonical_context": PROJECT_CONTEXT_REL.as_posix(),
            "review": review_rel,
        },
        "answers": answers,
        "planned_section": planned_section,
        "written": {},
        "issues": issues,
        "next_command": "" if issues or apply else f"python -B .agents/manage.py project-context-apply-review --target {shell_quote(str(target))} --apply",
        "summary": {
            "answer_count": len(answers),
            "issue_count": len(issues),
        },
    }
    if issues:
        return report
    if apply:
        try:
            updated = replace_or_append_review_section(read_text(context_path), planned_section)
            writes: list[tuple[Path, str]] = []
            normalized_path: Path | None = None
            if review_path is not None and review_path.suffix.lower() == ".md":
                normalized_path = target / PROJECT_CONTEXT_REVIEW_JSON_REL
                writes.append((normalized_path, json.dumps(payload, indent=2, sort_keys=True) + "\n"))
            writes.append((context_path, updated))
            atomic_write_text_files(target, writes)
        except (OSError, ValueError) as exc:
            error_issues = [str(exc), *[str(note) for note in getattr(exc, "__notes__", ())]]
            report["ok"] = False
            report["status"] = "blocked"
            report["issues"] = error_issues
            report["summary"] = {"answer_count": len(answers), "issue_count": len(error_issues)}
            return report
        report["status"] = "applied"
        report["written"] = {"canonical_context": PROJECT_CONTEXT_REL.as_posix()}
        if normalized_path is not None:
            report["written"]["normalized_evidence"] = PROJECT_CONTEXT_REVIEW_JSON_REL.as_posix()
        report["next_command"] = f"python -B .agents/manage.py project-context-review --target {shell_quote(str(target))}"
    return report


def render_project_context_apply_review(report: dict[str, object]) -> str:
    lines = [
        "# Project Context Apply Review",
        "",
        f"- Status: {report.get('status')}",
        f"- Target: `{report.get('target')}`",
    ]
    paths = report.get("paths") if isinstance(report.get("paths"), dict) else {}
    lines.extend(
        [
            f"- Canonical context: `{paths.get('canonical_context')}`",
            f"- Review artifact: `{paths.get('review')}`",
        ]
    )
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines.append(f"- Answered facts: {summary.get('answer_count', 0)}")
    issues = report.get("issues")
    if isinstance(issues, list) and issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {item}" for item in issues)
    if report.get("status") == "planned" and report.get("planned_section"):
        lines.extend(["", "## Planned Canonical Section", "", "```markdown", str(report.get("planned_section")).rstrip(), "```"])
    written = report.get("written") if isinstance(report.get("written"), dict) else {}
    if written:
        lines.extend(["", "## Written", ""])
        for key, value in written.items():
            lines.append(f"- {key}: `{value}`")
    if report.get("next_command"):
        lines.extend(["", "## Next Command", "", f"`{report.get('next_command')}`"])
    return "\n".join(lines).rstrip() + "\n"


def project_context_next_commands(status: str, *, write_review: bool = False) -> list[str]:
    commands: list[str] = []
    if status == "missing":
        commands.append("python -B .agents/manage.py setup")
    if status in {"missing", "review-needed"}:
        command = "python -B .agents/manage.py project-context-review --target . --write-review"
        if write_review:
            command = "review the written docs/project/review/project-context-review.md answers"
        commands.append(command)
    return commands


def project_context_review_report(target: Path, *, from_request: str = "", write_review: bool = False) -> dict[str, object]:
    target = target.expanduser().resolve(strict=False)
    project_goal = from_request.strip()
    if target.exists() and not target.is_dir():
        return {
            "schema_version": 1,
            "tool": "project-context-review",
            "ok": False,
            "status": "blocked",
            "target": str(target),
            "project_goal": project_goal,
            "review_required": True,
            "issues": [f"target exists and is not a directory: {target}"],
            "dotnet_context": {"status": "not-detected"},
            "missing_facts": [],
            "fact_reviews": [],
            "questions": [],
            "paths": {},
            "review_artifacts": {"written": [], "paths": {}},
            "next_commands": [],
        }
    context_path = target / PROJECT_CONTEXT_REL
    json_path = target / PROJECT_CONTEXT_JSON_REL
    validation_path = target / VALIDATION_MANIFEST_REL
    paths = {
        PROJECT_CONTEXT_REL.as_posix(): context_path.exists(),
        PROJECT_CONTEXT_JSON_REL.as_posix(): json_path.exists(),
        VALIDATION_MANIFEST_REL.as_posix(): validation_path.exists(),
    }
    if not context_path.exists():
        fact_reviews = context_fact_reviews(target, "", {}, {}, project_goal=project_goal)
        questions = ["Run `python -B .agents/manage.py setup` in the target to generate project context, then review the generated assumptions."]
        if project_goal:
            questions.append(f"Confirm the generated context captures this project goal: {project_goal}")
        report = {
            "schema_version": 1,
            "tool": "project-context-review",
            "ok": True,
            "status": "missing",
            "target": str(target),
            "project_goal": project_goal,
            "review_required": True,
            "paths": paths,
            "missing_facts": [str(item["id"]) for item in CONTEXT_FACTS],
            "fact_reviews": fact_reviews,
            "dotnet_context": {"status": "not-detected"},
            "questions": questions,
            "issues": [],
            "next_commands": project_context_next_commands("missing", write_review=write_review),
            "review_artifacts": {"written": [], "paths": {}},
            "summary": {
                "missing_fact_count": len(CONTEXT_FACTS),
                "question_count": len(questions),
                "blocking_fact_count": sum(1 for fact in fact_reviews if fact.get("blocking")),
            },
        }
        if write_review:
            report.update(
                {
                    "ok": False,
                    "status": "blocked",
                    "issues": [
                        "Cannot write review artifacts until docs/project/project-context.md exists. Run `python -B .agents/manage.py setup` in the target first.",
                    ],
                    "next_commands": ["python -B .agents/manage.py setup"],
                }
            )
        return report

    text = read_text(context_path)
    lowered = text.lower()
    data = read_json(json_path)
    manifest = read_json(validation_path)
    fact_reviews = context_fact_reviews(target, text, data, manifest, project_goal=project_goal)
    missing = [str(fact["id"]) for fact in fact_reviews if fact.get("blocking")]
    draft_like = any(term in lowered for term in ("status: draft", "status: generated", "not reviewed", "to" + "do", "tb" + "d"))
    questions = []
    for fact in fact_reviews:
        if str(fact.get("id")) in missing:
            question = str(fact.get("question") or "")
            if question and question not in questions:
                questions.append(question)
    if draft_like:
        review_question = "Who has reviewed this project context, and can `status` or `Last reviewed` be updated after confirmation?"
        if review_question not in questions:
            questions.insert(0, review_question)
    status = "review-needed" if missing or draft_like else "ready"
    report = {
        "schema_version": 1,
        "tool": "project-context-review",
        "ok": True,
        "status": status,
        "target": str(target),
        "project_goal": project_goal,
        "review_required": status != "ready",
        "paths": paths,
        "missing_facts": missing,
        "fact_reviews": fact_reviews,
        "dotnet_context": dotnet_context(data) or {"status": "not-detected"},
        "draft_like": draft_like,
        "questions": questions,
        "validation_command_count": len(validation_commands(manifest)),
        "issues": [],
        "next_commands": project_context_next_commands(status, write_review=write_review),
        "review_artifacts": {"written": [], "paths": {}},
        "summary": {
            "missing_fact_count": len(missing),
            "question_count": len(questions),
            "blocking_fact_count": sum(1 for fact in fact_reviews if fact.get("blocking")),
            "draft_like": draft_like,
        },
    }
    if write_review:
        report["review_artifacts"] = write_review_artifacts(target, report)
    return report


def render_project_context_review(report: dict[str, object]) -> str:
    lines = [
        "# Project Context Review",
        "",
        f"- Status: {report.get('status')}",
        f"- Target: `{report.get('target')}`",
    ]
    if report.get("project_goal"):
        lines.append(f"- Project goal: {report.get('project_goal')}")
    dotnet = report.get("dotnet_context")
    if isinstance(dotnet, dict):
        lines.append(f"- .NET context: {dotnet.get('status')}")
    missing = report.get("missing_facts")
    if isinstance(missing, list) and missing:
        lines.extend(["", "## Missing Or Draft Facts", ""])
        lines.extend(f"- `{item}`" for item in missing)
    questions = report.get("questions")
    if isinstance(questions, list) and questions:
        lines.extend(["", "## Questions To Resolve", ""])
        lines.extend(f"- {item}" for item in questions)
    facts = report.get("fact_reviews")
    if isinstance(facts, list) and facts:
        lines.extend(["", "## Review Checklist", ""])
        for fact in facts:
            if isinstance(fact, dict):
                lines.extend(
                    [
                        f"- `{fact.get('id')}`: {fact.get('status')} - {fact.get('question')}",
                    ]
                )
    artifacts = report.get("review_artifacts")
    if isinstance(artifacts, dict) and artifacts.get("written"):
        lines.extend(["", "## Review Artifacts", ""])
        for item in artifacts.get("written", []):
            lines.append(f"- `{item}`")
    next_commands = report.get("next_commands")
    if isinstance(next_commands, list) and next_commands:
        lines.extend(["", "## Next Commands", ""])
        lines.extend(f"- `{item}`" for item in next_commands)
    return "\n".join(lines) + "\n"


def target_state_report(target: Path) -> dict[str, object]:
    target = target.expanduser().resolve(strict=False)
    if not target.exists():
        return {"status": "missing", "has_harness": False, "target": str(target)}
    if not target.is_dir():
        return {"status": "blocked", "has_harness": False, "target": str(target), "issue": "target exists and is not a directory"}
    manifest = target / repo_harness_install.INSTALL_MANIFEST_REL
    manage = target / ".agents" / "manage.py"
    if manifest.exists():
        return {"status": "installed-consumer", "has_harness": True, "target": str(target), "install_manifest": repo_harness_install.INSTALL_MANIFEST_REL}
    if manage.exists():
        return {"status": "harness-present", "has_harness": True, "target": str(target)}
    return {"status": "existing-project", "has_harness": False, "target": str(target)}


def kickoff_tool_advisories(root: Path) -> dict[str, object]:
    args = SimpleNamespace(
        install_rg_portable=False,
        install_rg=False,
        check=True,
        dry_run=False,
        no_tool_prompts=True,
    )
    ripgrep = repo_setup.ripgrep_tool_report(args, root)
    advisories: list[str] = []
    if ripgrep.get("status") == "missing":
        advisories.append(str(ripgrep.get("suggested", "Install ripgrep or rerun setup --install-rg-portable.")))
    elif ripgrep.get("ok") is False:
        advisories.append(str(ripgrep.get("suggested", "Rerun setup tool checks.")))
    return {
        "python": {
            "ok": True,
            "version": ".".join(str(part) for part in sys.version_info[:3]),
            "required": "3.12+",
        },
        "ripgrep": ripgrep,
        "advisories": advisories,
    }


def kickoff_next_command(
    target: Path,
    *,
    apply: bool,
    from_request: str,
    profile: str,
    with_features: list[str] | None = None,
    without_features: list[str] | None = None,
) -> str:
    if apply:
        return "none, kickoff apply already ran"
    parts = [
        "python -B .agents/manage.py project-kickoff",
        "--target",
        shell_quote(str(target)),
        "--profile",
        profile,
        "--apply",
        *repo_harness_profiles.feature_flag_parts(with_features or [], without_features or []),
    ]
    if from_request.strip():
        escaped = from_request.replace('"', '\\"')
        parts.extend(["--from-request", f'"{escaped}"'])
    return " ".join(parts)


def copyable_chat_prompts(target: Path, from_request: str = "") -> list[dict[str, str]]:
    goal = from_request.strip() or "<describe the project goal>"
    return [
        {
            "label": "initialize this project",
            "prompt": "Read AGENTS.md and docs/agent-start.md, run `python -B .agents/manage.py setup`, then `python -B .agents/manage.py setup --check`, and report project-context status before implementation.",
        },
        {
            "label": "review generated project context",
            "prompt": f"Review `docs/project/review/project-context-review.md` for `{target}` against this goal: {goal}. Answer missing stack, commands, external systems, persistence, CI, generated boundaries, and validation expectations before updating canonical context.",
        },
        {
            "label": "start user-story workflow",
            "prompt": f"Use the user-story workflow for this project goal: {goal}. Start a run only after project context is reviewed, inspect context, create the plan with validation and risks, then stop before implementation.",
        },
        {
            "label": "resume latest workflow run",
            "prompt": "Resume the latest workflow run. Run workflow resume, load the returned context packet, summarize blockers, current phase, last evidence, and next action before changing files.",
        },
    ]


def shell_quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def target_setup_command() -> str:
    return "python -B .agents/manage.py setup"


def target_context_review_command(from_request: str = "", *, write_review: bool = True) -> str:
    parts = ["python -B .agents/manage.py project-context-review", "--target", "."]
    if from_request.strip():
        parts.extend(["--from-request", shell_quote(from_request.strip())])
    if write_review:
        parts.append("--write-review")
    return " ".join(parts)


def workflow_start_command(from_request: str) -> str:
    goal = from_request.strip() or "<describe the project goal>"
    return (
        "python -B .agents/manage.py workflow start --from-request "
        f"{shell_quote(goal)} --summary --compact --format json"
    )


def workflow_resume_command() -> str:
    return "python -B .agents/manage.py workflow resume --name user-story-workflow --summary --compact --format json"


def workflow_recommendations(from_request: str) -> list[dict[str, str]]:
    return [
        {
            "id": "start-user-story-workflow",
            "run_from": "target-project",
            "command": workflow_start_command(from_request),
            "reason": "start the first workflow run after project context is reviewed",
        },
        {
            "id": "resume-user-story-workflow",
            "run_from": "target-project",
            "command": workflow_resume_command(),
            "reason": "resume the latest user-story workflow run when one already exists",
        },
    ]


def kickoff_command_groups(
    root: Path,
    target: Path,
    *,
    from_request: str,
    profile: str,
    with_features: list[str] | None = None,
    without_features: list[str] | None = None,
) -> list[dict[str, object]]:
    source_commands = [
        kickoff_next_command(
            target,
            apply=False,
            from_request=from_request,
            profile=profile,
            with_features=with_features,
            without_features=without_features,
        )
    ]
    target_commands = [
        target_setup_command(),
        "python -B .agents/manage.py setup --check",
        target_context_review_command(from_request, write_review=True),
        workflow_start_command(from_request),
        workflow_resume_command(),
    ]
    return [
        {
            "id": "source-harness",
            "label": "Run from source harness",
            "cwd": str(root),
            "commands": source_commands,
        },
        {
            "id": "target-project",
            "label": "Run from target project",
            "cwd": str(target),
            "commands": target_commands,
        },
    ]


def kickoff_primary_next_action(
    *,
    target_state: dict[str, object],
    install_report: dict[str, object],
    context_review: dict[str, object],
    target: Path,
    from_request: str,
    profile: str,
    with_features: list[str] | None,
    without_features: list[str] | None,
    issues: list[str],
    apply: bool,
) -> dict[str, object]:
    if issues:
        return {
            "id": "resolve-issues",
            "label": "Resolve kickoff issues",
            "run_from": "source-harness",
            "command": "review the reported issues",
            "reason": "kickoff cannot continue until blocking issues are resolved",
        }
    if apply:
        return {
            "id": "review-apply-result",
            "label": "Review apply result",
            "run_from": "target-project",
            "command": "python -B .agents/manage.py status --fast",
            "reason": "kickoff apply already ran install/setup/status sequence",
        }
    if target_state.get("status") == "missing" or install_report.get("status") in {"planned", "blocked"} and not target_state.get("has_harness"):
        return {
            "id": "apply-kickoff",
            "label": "Install or update harness",
            "run_from": "source-harness",
            "command": kickoff_next_command(
                target,
                apply=False,
                from_request=from_request,
                profile=profile,
                with_features=with_features,
                without_features=without_features,
            ),
            "reason": "target is not initialized with the harness yet",
        }
    if context_review.get("status") == "missing":
        return {
            "id": "run-setup",
            "label": "Initialize project context",
            "run_from": "target-project",
            "command": target_setup_command(),
            "reason": "project context is missing; setup creates navigation maps and context files",
        }
    if context_review.get("status") == "review-needed":
        return {
            "id": "write-context-review",
            "label": "Write context review artifact",
            "run_from": "target-project",
            "command": target_context_review_command(from_request, write_review=True),
            "reason": "project context has missing or draft facts that should be answered before planning",
        }
    return {
        "id": "start-workflow",
        "label": "Start workflow",
        "run_from": "target-project",
        "command": workflow_start_command(from_request),
        "reason": "harness and reviewed project context are ready for workflow planning",
    }


def project_kickoff_report(
    root: Path,
    *,
    target: Path,
    apply: bool = False,
    from_request: str = "",
    profile: str = "standard",
    with_features: list[str] | None = None,
    without_features: list[str] | None = None,
    command_runner: CommandRunner | None = None,
) -> dict[str, object]:
    root_guard = repo_harness_paths.HarnessPathGuard(root, label="kickoff-source")
    root = root_guard.root
    target_guard = repo_harness_paths.HarnessPathGuard(target, label="kickoff-target")
    target = target_guard.root
    unsafe_paths: list[dict[str, str]] = []
    if apply:
        target, unsafe_paths = initialization_preflight(
            target,
            operation="project-kickoff-initialization-preflight",
        )
    state = (
        {
            "schema_version": 1,
            "tool": "target-state",
            "ok": False,
            "status": "unsafe-path-blocked",
            "target": str(target),
            "issues": ["target state was not inspected after initialization containment failed"],
        }
        if unsafe_paths
        else target_state_report(target)
    )
    copy_contract = repo_harness_install.copy_contract_report(
        root,
        profile=profile,
        with_features=with_features,
        without_features=without_features,
    )
    tool_advisories = kickoff_tool_advisories(root)
    same_root = root == target
    issues: list[str] = []
    post_apply: list[dict[str, object]] = []
    install_report: dict[str, object]

    if unsafe_paths:
        install_report = blocked_initialization_install_report(
            root,
            target,
            profile=profile,
            unsafe_paths=unsafe_paths,
        )
    elif same_root:
        install_report = {
            "schema_version": 1,
            "tool": "install-harness",
            "ok": True,
            "status": "not-needed",
            "operation": "current-project",
            "summary": {},
            "issues": [],
        }
    else:
        install_report = repo_harness_install.install_harness_report(
            root,
            target,
            dry_run=not apply,
            force=False,
            profile=profile,
            with_features=with_features,
            without_features=without_features,
        )

    if not copy_contract.get("ok", False):
        issues.extend(str(item) for item in copy_contract.get("issues", []))
    if not install_report.get("ok", False):
        issues.extend(str(item) for item in install_report.get("issues", []))
        issues.extend(str(item.get("path", "")) for item in install_report.get("collisions", []) if isinstance(item, dict))

    if apply and not issues and install_report.get("ok", False):
        runner = command_runner or repo_harness_install.run_post_install_command
        for args, timeout in POST_KICKOFF_COMMANDS:
            _target, command_unsafe_paths = initialization_preflight(
                target,
                operation="project-kickoff-command-preflight",
            )
            if command_unsafe_paths:
                unsafe_paths = repo_harness_paths.sorted_unsafe_paths([*unsafe_paths, *command_unsafe_paths])
                issues.append(f"unsafe-path-blocked: {len(unsafe_paths)} unsafe initialization path(s) rejected")
                break
            else:
                result = runner(target, list(args), timeout)
                result["name"] = "-".join(args)
                post_apply.append(result)
        if any(item.get("ok") is not True for item in post_apply):
            issues.append("one or more post-kickoff commands failed")

    context_review = (
        {
            "schema_version": 1,
            "tool": "project-context-review",
            "ok": False,
            "status": "unsafe-path-blocked",
            "target": str(target),
            "missing_facts": [],
            "dotnet_context": {"status": "not-evaluated"},
            "issues": ["project context was not inspected after initialization containment failed"],
        }
        if unsafe_paths
        else project_context_review_report(target, from_request=from_request)
    )
    dotnet = context_review.get("dotnet_context") if isinstance(context_review.get("dotnet_context"), dict) else {"status": "not-detected"}
    primary_next_action = kickoff_primary_next_action(
        target_state=state,
        install_report=install_report,
        context_review=context_review,
        target=target,
        from_request=from_request,
        profile=profile,
        with_features=with_features,
        without_features=without_features,
        issues=issues,
        apply=apply,
    )
    status = "unsafe-path-blocked" if unsafe_paths else "applied" if apply and not issues else "blocked" if issues else "planned"
    ok = not issues and bool(copy_contract.get("ok", False)) and bool(install_report.get("ok", False)) and context_review.get("ok", True)
    return {
        "schema_version": 1,
        "tool": "project-kickoff",
        "ok": ok,
        "status": status,
        "source_root": str(root),
        "target_root": str(target),
        "profile": profile,
        "resolved_features": list(copy_contract.get("resolved_features", [])),
        "unsafe_paths": repo_harness_paths.sorted_unsafe_paths(unsafe_paths),
        "apply": apply,
        "project_goal": from_request.strip(),
        "target_state": state,
        "tool_advisories": tool_advisories,
        "copy_contract": copy_contract,
        "install": install_report,
        "post_apply": post_apply,
        "context_review": context_review,
        "dotnet_context": dotnet,
        "primary_next_action": primary_next_action,
        "command_groups": kickoff_command_groups(
            root,
            target,
            from_request=from_request,
            profile=profile,
            with_features=with_features,
            without_features=without_features,
        ),
        "workflow_recommendations": workflow_recommendations(from_request),
        "chat_prompts": copyable_chat_prompts(target, from_request),
        "issues": issues,
        "next_command": "" if issues else str(primary_next_action.get("command", "")),
        "summary": {
            "post_apply_steps": len(post_apply),
            "context_missing_facts": len(context_review.get("missing_facts", []) if isinstance(context_review.get("missing_facts"), list) else []),
            "dotnet_context_status": dotnet.get("status"),
            "dotnet_private_feeds": dotnet_private_feeds({"dotnet_context": dotnet}),
            "issue_count": len(issues),
        },
    }


def render_project_kickoff(report: dict[str, object]) -> str:
    lines = [
        "# Project Kickoff",
        "",
        f"- Status: {report.get('status')}",
        f"- Source: `{report.get('source_root')}`",
        f"- Target: `{report.get('target_root')}`",
        f"- Profile: {report.get('profile')}",
    ]
    if report.get("project_goal"):
        lines.append(f"- Project goal: {report.get('project_goal')}")
    state = report.get("target_state")
    if isinstance(state, dict):
        lines.append(f"- Target state: {state.get('status')}")
    install = report.get("install")
    if isinstance(install, dict):
        lines.append(f"- Install/update: {install.get('status')}")
    context = report.get("context_review")
    if isinstance(context, dict):
        lines.append(f"- Project context: {context.get('status')}")
    dotnet = report.get("dotnet_context")
    if isinstance(dotnet, dict):
        lines.append(f"- .NET context: {dotnet.get('status')}")
        if dotnet_private_feeds({"dotnet_context": dotnet}):
            lines.append("- .NET review: private/internal NuGet feed restore prerequisites need review.")
    primary = report.get("primary_next_action")
    if isinstance(primary, dict):
        lines.extend(
            [
                "",
                "## Primary Next Action",
                "",
                f"- Run from: {primary.get('run_from')}",
                f"- Reason: {primary.get('reason')}",
                f"- Command: `{primary.get('command')}`",
            ]
        )
    if report.get("next_command"):
        lines.extend(["", "## Next Command", "", f"`{report.get('next_command')}`"])
    groups = report.get("command_groups")
    if isinstance(groups, list) and groups:
        lines.extend(["", "## Command Groups", ""])
        for group in groups:
            if isinstance(group, dict):
                lines.extend([f"### {group.get('label')}", "", f"- Cwd: `{group.get('cwd')}`"])
                commands = group.get("commands")
                if isinstance(commands, list):
                    lines.extend(f"- `{command}`" for command in commands)
                lines.append("")
    issues = report.get("issues")
    if isinstance(issues, list) and issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {item}" for item in issues)
    prompts = report.get("chat_prompts")
    if isinstance(prompts, list) and prompts:
        lines.extend(["", "## Copyable Chat Prompts", ""])
        for item in prompts:
            if isinstance(item, dict):
                lines.extend([f"### {item.get('label')}", "", "```text", str(item.get("prompt", "")), "```", ""])
    return "\n".join(lines).rstrip() + "\n"


def start_here_report(
    root: Path,
    *,
    simple: bool = False,
    profile: str = "standard",
    target: Path | None = None,
    with_features: list[str] | None = None,
    without_features: list[str] | None = None,
) -> dict[str, object]:
    root = root.expanduser().resolve(strict=False)
    selected_target = target.expanduser().resolve(strict=False) if target is not None else root
    profile_issues: list[str] = []
    payload_manifest, manifest_issues = repo_harness_install.load_payload_manifest(root)
    profile_issues.extend(manifest_issues)
    _effective_manifest, selected_profile = repo_harness_install.effective_payload_manifest(
        payload_manifest,
        profile,
        profile_issues,
        with_features=with_features,
        without_features=without_features,
    )
    feature_flags = repo_harness_profiles.feature_flag_text(with_features or [], without_features or [])
    mode = "simple" if simple else "full"
    if simple:
        steps = [
            "If you are copying this harness into another project, run install-wizard here first.",
            "If this harness is already copied into the project, run setup once to initialize maps and project context.",
            "Then check status.",
            "Read docs/agent-start.md before loading larger docs.",
            "Use the next command from status.",
            "Ask for a workflow plan before implementation work.",
        ]
        commands = [
            f"python -B .agents/manage.py install-wizard --target <project> --profile {profile}{feature_flags}",
            "python -B .agents/manage.py setup",
            "python -B .agents/manage.py status --fast",
            "python -B .agents/manage.py setup --check",
            "python -B .agents/manage.py commands --daily",
        ]
    else:
        steps = [
            "Read AGENTS.md.",
            "Read docs/agent-start.md.",
            "Run setup once when project maps or context are missing, then check status and setup readiness.",
            "Use routing only when a skill or workflow is needed.",
            "Run validation before finishing.",
        ]
        commands = [
            "python -B .agents/manage.py status --fast",
            "python -B .agents/manage.py setup",
            "python -B .agents/manage.py setup --check",
            "python -B .agents/manage.py commands --first-time",
            "python -B .agents/manage.py check-additions",
            "python -B .agents/manage.py sync --check",
            "python -B .agents/manage.py check",
        ]
    install_manifest = repo_harness_install.read_install_manifest(selected_target)
    source_maintainer = (root / ".agents" / "harness-payload.json").is_file() and not repo_harness_install.manifest_hashes(
        repo_harness_install.read_install_manifest(root)
    )
    installed_target = bool(repo_harness_install.manifest_hashes(install_manifest))
    if profile_issues:
        role = "source-maintainer" if source_maintainer else "installed-target"
        onboarding_state = "profile-unavailable"
        primary_next_action = {
            "command": "review the reported profile issues",
            "working_directory": str(root),
            "effect": "read",
        }
    elif source_maintainer and selected_target == root:
        role = "source-maintainer"
        onboarding_state = "source-maintainer"
        primary_next_action = {
            "command": f"python -B .agents/manage.py project-kickoff --target <project> --profile {profile}{feature_flags}",
            "working_directory": str(root),
            "effect": "read",
        }
    elif not selected_target.exists():
        role = "source-maintainer"
        onboarding_state = "missing-target"
        primary_next_action = {
            "command": kickoff_next_command(
                selected_target,
                apply=False,
                from_request="",
                profile=profile,
                with_features=with_features,
                without_features=without_features,
            ),
            "working_directory": str(root),
            "effect": "write",
        }
    elif not installed_target and source_maintainer:
        role = "source-maintainer"
        onboarding_state = "uninitialized-target"
        primary_next_action = {
            "command": kickoff_next_command(
                selected_target,
                apply=False,
                from_request="",
                profile=profile,
                with_features=with_features,
                without_features=without_features,
            ),
            "working_directory": str(root),
            "effect": "write",
        }
    else:
        role = "installed-target"
        context = project_context_review_report(selected_target)
        if context.get("status") == "missing":
            onboarding_state = "uninitialized-target"
            command = "python -B .agents/manage.py setup"
            effect = "write"
        elif context.get("status") == "review-needed":
            onboarding_state = "context-review-required"
            command = target_context_review_command(write_review=True)
            effect = "write"
        else:
            onboarding_state = "ready-target"
            command = "python -B .agents/manage.py status --fast"
            effect = "read"
        primary_next_action = {
            "command": command,
            "working_directory": str(selected_target),
            "effect": effect,
        }
    return {
        "schema_version": 1,
        "tool": "start-here",
        "ok": not profile_issues,
        "mode": mode,
        "profile": profile,
        "resolved_features": list(selected_profile.get("features", [])),
        "root": str(root),
        "target": str(selected_target),
        "role": role,
        "onboarding_state": onboarding_state,
        "primary_next_action": primary_next_action,
        "steps": steps,
        "commands": commands,
        "issues": profile_issues,
        "next": primary_next_action["command"],
    }


def render_start_here(report: dict[str, object]) -> str:
    lines = [
        "# Start Here",
        "",
        f"- Mode: {report.get('mode')}",
        f"- Profile: {report.get('profile')}",
        f"- Role: {report.get('role')}",
        f"- State: {report.get('onboarding_state')}",
        "",
        "## Primary Next Action",
        "",
        f"- Command: `{report.get('primary_next_action', {}).get('command', '') if isinstance(report.get('primary_next_action'), dict) else ''}`",
        f"- Working directory: `{report.get('primary_next_action', {}).get('working_directory', '') if isinstance(report.get('primary_next_action'), dict) else ''}`",
        f"- Effect: {report.get('primary_next_action', {}).get('effect', '') if isinstance(report.get('primary_next_action'), dict) else ''}",
        "",
        "## What To Do Now",
        "",
    ]
    steps = report.get("steps", [])
    if isinstance(steps, list):
        for index, step in enumerate(steps, start=1):
            lines.append(f"{index}. {step}")
    commands = report.get("commands", [])
    if isinstance(commands, list):
        lines.extend(["", "## Commands", ""])
        for command in commands:
            lines.append(f"- `{command}`")
    issues = report.get("issues", [])
    if isinstance(issues, list) and issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in issues)
    return "\n".join(lines) + "\n"


def install_command(
    target: Path,
    *,
    profile: str,
    with_features: list[str] | None = None,
    without_features: list[str] | None = None,
    run_setup_check: bool,
    install_rg_portable: bool,
    bootstrap_local_ai: bool,
    download_ai_models: bool,
    force: bool = False,
) -> str:
    parts = [
        "python -B .agents/manage.py install-harness",
        "--target",
        shell_quote(str(target)),
        "--profile",
        profile,
        *repo_harness_profiles.feature_flag_parts(with_features or [], without_features or []),
    ]
    if force:
        parts.append("--force")
    if run_setup_check:
        parts.append("--run-setup-check")
    if install_rg_portable:
        parts.append("--install-rg-portable")
    if bootstrap_local_ai:
        parts.append("--bootstrap-local-ai")
    if download_ai_models:
        parts.append("--download-ai-models")
    return " ".join(parts)


def install_wizard_report(
    root: Path,
    *,
    target: Path,
    profile: str,
    with_features: list[str] | None = None,
    without_features: list[str] | None = None,
    setup_check: bool,
    install_rg_portable: bool,
    bootstrap_local_ai: bool,
    download_ai_models: bool,
    apply: bool,
    force: bool = False,
    command_runner: CommandRunner | None = None,
) -> dict[str, object]:
    root = repo_harness_paths.HarnessPathGuard(root, label="wizard-source").root
    target = repo_harness_paths.HarnessPathGuard(target, label="wizard-target").root
    unsafe_paths: list[dict[str, str]] = []
    if apply:
        target, unsafe_paths = initialization_preflight(
            target,
            operation="install-wizard-initialization-preflight",
        )
    command = install_command(
        target,
        profile=profile,
        with_features=with_features,
        without_features=without_features,
        run_setup_check=setup_check,
        install_rg_portable=install_rg_portable,
        bootstrap_local_ai=bootstrap_local_ai,
        download_ai_models=download_ai_models,
        force=force,
    )
    profile_validation = repo_harness_install.copy_contract_report(
        root,
        profile=profile,
        with_features=with_features,
        without_features=without_features,
    )
    next_steps = [
        "Run the recommended install command, or rerun this wizard with --apply.",
        "Open the target project.",
        f"Run `python -B .agents/manage.py start-here --simple --profile {profile}{repo_harness_profiles.feature_flag_text(with_features or [], without_features or [])}`.",
        "Run `python -B .agents/manage.py status --fast`.",
    ]
    install_report = None
    post_apply: list[dict[str, object]] = []
    if apply and unsafe_paths:
        install_report = blocked_initialization_install_report(
            root,
            target,
            profile=profile,
            unsafe_paths=unsafe_paths,
        )
        next_steps = [
            "Remove or replace the reported target indirection before applying initialization.",
            "Rerun the install wizard after the target tree passes containment preflight.",
        ]
    elif apply and profile_validation.get("ok", False):
        install_report = repo_harness_install.install_harness_report(
            root,
            target,
            dry_run=False,
            force=force,
            profile=profile,
            with_features=with_features,
            without_features=without_features,
            run_setup_check=False,
            install_rg_portable=install_rg_portable,
            bootstrap_local_ai=bootstrap_local_ai,
            download_ai_models=download_ai_models,
        )
        if install_report.get("ok", False):
            runner = command_runner or repo_harness_install.run_post_install_command
            for args, timeout in POST_KICKOFF_COMMANDS:
                _target, command_unsafe_paths = initialization_preflight(
                    target,
                    operation="install-wizard-command-preflight",
                )
                if command_unsafe_paths:
                    unsafe_paths = repo_harness_paths.sorted_unsafe_paths([*unsafe_paths, *command_unsafe_paths])
                    break
                else:
                    result = runner(target, list(args), timeout)
                    result["name"] = "-".join(args)
                    post_apply.append(result)
            if unsafe_paths:
                next_steps = [
                    "Remove or replace the reported target indirection before applying initialization.",
                    "Rerun the install wizard after the target tree passes containment preflight.",
                ]
            else:
                next_steps = [
                    "Open the target project.",
                    f"Run `python -B .agents/manage.py start-here --simple --profile {profile}{repo_harness_profiles.feature_flag_text(with_features or [], without_features or [])}`.",
                    "Run `python -B .agents/manage.py status --fast`.",
                ]
                if any(item.get("ok") is not True for item in post_apply):
                    next_steps = [
                        "Review the failed setup/setup-check/status result below.",
                        f"Rerun `python -B .agents/manage.py start-here --simple --profile {profile}{repo_harness_profiles.feature_flag_text(with_features or [], without_features or [])}` in the target.",
                    ]
        else:
            next_steps = [
                "Review the install issues below.",
                "Fix collisions or rerun with `--force` only when overwriting target edits is intentional.",
                f"Rerun `python -B .agents/manage.py install-wizard --target <project> --profile {profile}{repo_harness_profiles.feature_flag_text(with_features or [], without_features or [])} --apply`.",
            ]
    elif not profile_validation.get("ok", False):
        next_steps = [
            "Review the profile or feature issues below.",
            "Choose an available profile/features before applying the install.",
        ]
    initialization_failed = any(item.get("ok") is not True for item in post_apply)
    profile_blocked = profile_validation.get("ok") is not True
    return {
        "schema_version": 1,
        "tool": "install-wizard",
        "ok": not unsafe_paths
        and not profile_blocked
        and (install_report.get("ok", True) if isinstance(install_report, dict) else True)
        and not initialization_failed,
        "status": "unsafe-path-blocked"
        if unsafe_paths
        else "blocked"
        if profile_blocked
        else "initialization-failed"
        if initialization_failed
        else install_report.get("status", "planned")
        if isinstance(install_report, dict)
        else "planned",
        "profile": profile,
        "resolved_features": list(profile_validation.get("resolved_features", [])),
        "unsafe_paths": repo_harness_paths.sorted_unsafe_paths(unsafe_paths),
        "target": str(target),
        "recommended_command": command,
        "selected": {
            "setup_check": setup_check,
            "install_rg_portable": install_rg_portable,
            "bootstrap_local_ai": bootstrap_local_ai,
            "download_ai_models": download_ai_models,
            "force": force,
        },
        "install_report": install_report,
        "post_apply": post_apply,
        "issues": list(profile_validation.get("issues", []))
        + (list(install_report.get("issues", [])) if isinstance(install_report, dict) else [])
        + (
            [f"unsafe-path-blocked: {len(unsafe_paths)} unsafe initialization path(s) rejected"]
            if unsafe_paths
            and not (
                isinstance(install_report, dict)
                and any(str(issue).startswith("unsafe-path-blocked:") for issue in install_report.get("issues", []))
            )
            else []
        ),
        "next_steps": next_steps,
    }


def render_install_wizard(report: dict[str, object]) -> str:
    lines = [
        "# Install Wizard",
        "",
        f"- Status: {report.get('status')}",
        f"- Profile: {report.get('profile')}",
        f"- Target: `{report.get('target')}`",
        "",
        "## Recommended Command",
        "",
        f"`{report.get('recommended_command')}`",
        "",
        "## What Now",
        "",
    ]
    next_steps = report.get("next_steps", [])
    if isinstance(next_steps, list):
        for step in next_steps:
            lines.append(f"- {step}")
    issues = report.get("issues", [])
    if isinstance(issues, list) and issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {issue}" for issue in issues)
    install_report = report.get("install_report")
    if isinstance(install_report, dict):
        human = install_report.get("human_summary", {})
        if isinstance(human, dict):
            lines.extend(["", "## Install Result", "", f"- {human.get('headline', install_report.get('status'))}"])
    return "\n".join(lines) + "\n"


def summarize_project_context_review_report(report: dict[str, object], *, compact: bool = False) -> dict[str, object]:
    facts = report.get("fact_reviews") if isinstance(report.get("fact_reviews"), list) else []
    dotnet = report.get("dotnet_context") if isinstance(report.get("dotnet_context"), dict) else {"status": "not-detected"}
    summary = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "project-context-review"),
        "ok": report.get("ok", False),
        "status": report.get("status", ""),
        "target": report.get("target", ""),
        "project_goal": report.get("project_goal", ""),
        "review_required": report.get("review_required", False),
        "draft_like": bool(report.get("draft_like", False)),
        "missing_facts": report.get("missing_facts", []),
        "blocking_fact_count": sum(1 for fact in facts if isinstance(fact, dict) and fact.get("blocking")),
        "question_count": len(report.get("questions", []) if isinstance(report.get("questions"), list) else []),
        "dotnet_context": {
            "status": dotnet.get("status"),
            "private_feeds_detected": dotnet_private_feeds({"dotnet_context": dotnet}),
        },
        "next_commands": report.get("next_commands", []),
        "issues": report.get("issues", []),
        "review_artifacts": report.get("review_artifacts", {"written": [], "paths": {}}),
        "summary": report.get("summary", {}),
    }
    if not compact:
        summary["questions"] = report.get("questions", [])
        summary["fact_reviews"] = facts
    return summary


def summarize_project_kickoff_report(report: dict[str, object], *, compact: bool = False) -> dict[str, object]:
    target_state = report.get("target_state") if isinstance(report.get("target_state"), dict) else {}
    install = report.get("install") if isinstance(report.get("install"), dict) else {}
    context = report.get("context_review") if isinstance(report.get("context_review"), dict) else {}
    dotnet = report.get("dotnet_context") if isinstance(report.get("dotnet_context"), dict) else {"status": "not-detected"}
    summary = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "project-kickoff"),
        "ok": report.get("ok", False),
        "status": report.get("status", ""),
        "source_root": report.get("source_root", ""),
        "target_root": report.get("target_root", ""),
        "profile": report.get("profile", ""),
        "resolved_features": report.get("resolved_features", []),
        "apply": report.get("apply", False),
        "project_goal": report.get("project_goal", ""),
        "target_state": target_state.get("status", ""),
        "install_status": install.get("status", ""),
        "context_status": context.get("status", ""),
        "dotnet_context": {
            "status": dotnet.get("status"),
            "private_feeds_detected": dotnet_private_feeds({"dotnet_context": dotnet}),
        },
        "primary_next_action": report.get("primary_next_action", {}),
        "next_command": report.get("next_command", ""),
        "issues": report.get("issues", []),
        "summary": report.get("summary", {}),
    }
    if not compact:
        summary["workflow_recommendations"] = report.get("workflow_recommendations", [])
        summary["command_groups"] = report.get("command_groups", [])
    return summary


def print_report(report: dict[str, object], output_format: str, renderer) -> None:
    if output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(renderer(report), end="")
