"""Focused self-tests for task/model orchestration and ordered fallbacks."""

from __future__ import annotations

import json
from pathlib import Path

from workflow_support.orchestration import CONFIG_REL, resolve_orchestration, validate_orchestration


def fixture_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "default_task_set": "normal",
        "chains": {
            "ordered": {
                "hosts": {
                    "codex": [
                        {"model": "cheap", "reasoning": "low"},
                        {"model": "strong", "reasoning": "high"},
                        {"model": "active", "reasoning": "inherit"},
                    ],
                    "default": [{"model": "inherit", "reasoning": "inherit"}],
                }
            }
        },
        "task_sets": {
            "normal": {
                "responsibility": "Do bounded work.",
                "execution": "orchestrator-decides",
                "chain": "ordered",
            },
            "scripts": {
                "responsibility": "Run deterministic checks.",
                "execution": "deterministic",
            },
        },
        "tasks": {
            "build": {"responsibility": "Build the slice.", "task_set": "normal"},
            "validate": {"responsibility": "Validate it.", "task_set": "scripts"},
        },
    }


def write_fixture(root: Path, document: dict[str, object] | None = None) -> None:
    path = root / CONFIG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document or fixture_document(), indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def test_orchestration_reports_preferences_without_availability(tmp: Path) -> None:
    write_fixture(tmp)
    report = resolve_orchestration(tmp, task="build", task_set=None, host="codex")

    assert report["ok"] is True
    assert report["status"] == "preference-only"
    assert report["selected"] is None
    assert [row["model"] for row in report["candidates"]] == ["cheap", "strong", "active"]


def test_orchestration_advances_after_failed_preference(tmp: Path) -> None:
    write_fixture(tmp)
    report = resolve_orchestration(
        tmp,
        task="build",
        task_set=None,
        host="codex",
        available_models=["cheap", "strong"],
        failed_models=["cheap"],
    )

    assert report["status"] == "selected"
    assert report["selected"] == {"model": "strong", "reasoning": "high", "priority": 2}


def test_orchestration_uses_active_model_as_portable_fallback(tmp: Path) -> None:
    write_fixture(tmp)
    report = resolve_orchestration(
        tmp,
        task="build",
        task_set=None,
        host="codex",
        available_models=[],
    )

    assert report["selected"]["model"] == "active"
    assert report["selected"]["priority"] == 3


def test_orchestration_unknown_task_uses_default_task_set_and_unknown_host_uses_default(tmp: Path) -> None:
    write_fixture(tmp)
    report = resolve_orchestration(
        tmp,
        task="new-task",
        task_set=None,
        host="github-copilot",
        available_models=[],
    )

    assert report["task_known"] is False
    assert report["task_set"] == "normal"
    assert report["host_route"] == "default"
    assert report["selected"]["model"] == "inherit"


def test_orchestration_deterministic_task_has_no_model_route(tmp: Path) -> None:
    write_fixture(tmp)
    report = resolve_orchestration(tmp, task="validate", task_set=None, host="claude")

    assert report["status"] == "deterministic"
    assert report["chain"] is None
    assert report["candidates"] == []


def test_orchestration_rejects_chain_without_active_fallback(tmp: Path) -> None:
    document = fixture_document()
    document["chains"]["ordered"]["hosts"]["codex"] = [{"model": "cheap", "reasoning": "low"}]

    issues = validate_orchestration(document)

    assert any("must end with active or inherit fallback" in issue for issue in issues)


def test_orchestration_rejects_unknown_fields_and_deterministic_chain_override(tmp: Path) -> None:
    document = fixture_document()
    document["unexpected"] = True
    document["tasks"]["validate"]["chain"] = "ordered"

    issues = validate_orchestration(document)

    assert "unsupported root field: unexpected" in issues
    assert any("deterministic task set" in issue for issue in issues)


def test_orchestration_blocks_when_every_candidate_including_active_failed(tmp: Path) -> None:
    write_fixture(tmp)
    report = resolve_orchestration(
        tmp,
        task="build",
        task_set=None,
        host="codex",
        available_models=["cheap", "strong"],
        failed_models=["cheap", "strong", "active"],
    )

    assert report["ok"] is False
    assert report["status"] == "blocked"
