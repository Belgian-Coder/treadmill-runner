"""Focused tests for deterministic replay selection and planning."""

from __future__ import annotations

import json
from pathlib import Path

from repo_support import repo_determinism


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, value: object) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def command(command_id: str, *tail: str) -> dict[str, object]:
    return {
        "id": command_id,
        "argv": ["python", "-B", f"scripts/{command_id}.py", *tail],
        "timeout_seconds": 30,
        "working_directory": "repository",
        "effects": [],
    }


def manifest(
    module_id: str,
    commands: list[dict[str, object]],
    *,
    kind: str = "skill",
    replay_commands: list[str] | None | object = (),
) -> dict[str, object]:
    strict_ids = [str(item["id"]) for item in commands]
    value: dict[str, object] = {
        "schema_version": 3,
        "kind": kind,
        "id": module_id,
        "name": module_id,
        "version": "1.0.0",
        "status": "accepted",
        "summary": "Determinism planning fixture.",
        "owners": ["test"],
        "inputs": ["module.json"],
        "outputs": [],
        "commands": commands,
        "related_modules": [],
        "validation": [],
        "risk": {
            "credentials": False,
            "destructive": False,
            "generated_settings": False,
            "installs": False,
            "network": False,
            "production_writes": False,
            "uploads": False,
            "profile": "read-only",
        },
        "external_access": {
            "source_systems": [],
            "credential_expectations": "none",
            "data_copied_locally": [],
            "attachments_retrieved": False,
        },
        "local_ai": {"use_cases": []},
        "strict_read_only_commands": strict_ids,
        "extensions": {},
    }
    if replay_commands is not None:
        selected = strict_ids if replay_commands == () else list(replay_commands)
        value["determinism"] = {
            "replay_commands": selected,
            "allowed_temporary_effects": [],
            "volatile_json_pointers": [],
            "environment_requirements": {
                "minimum_python": "3.12",
                "executables": [],
                "platforms": ["windows", "linux", "macos"],
            },
        }
    return value


def skill_manifest(root: Path, module_id: str) -> Path:
    return root / ".agents" / "skills" / module_id / "module.json"


def workflow_manifest(root: Path, module_id: str) -> Path:
    return root / "automations" / module_id / "module.json"


def test_determinism_changed_owner_mapping_excludes_state_and_empty_selection(tmp):
    root = tmp / "repo"
    write_json(skill_manifest(root, "demo"), manifest("demo", [command("check")]))
    write_json(workflow_manifest(root, "flow"), manifest("flow", [command("inspect")], kind="workflow"))

    selected = repo_determinism.changed_module_paths(
        root,
        [
            ".agents/skills/demo/scripts/check.py",
            "automations/flow/instructions.md",
            "automations/flow/runs/protected/run.json",
            ".agents/local-ai/cache/index.json",
            ".agents/local-ai/secrets.local.json",
            ".superpowers/sdd/review.diff",
        ],
    )
    empty = repo_determinism.build_plan(
        root,
        changed=True,
        changed_paths=["docs/readme.md", "automations/flow/runs/protected/run.json"],
    )

    assert selected == [
        ".agents/skills/demo/module.json",
        "automations/flow/module.json",
    ]
    assert empty["ok"] is True
    assert empty["status"] == "empty-selection"
    assert empty["summary"]["command_count"] == 0


def test_determinism_changed_owner_mapping_ignores_root_generated_automation_files(tmp):
    root = tmp / "repo"
    write_json(workflow_manifest(root, "flow"), manifest("flow", [command("inspect")], kind="workflow"))

    selected = repo_determinism.changed_module_paths(
        root,
        [
            "automations/routing.md",
            "automations/registry.json",
            "automations/flow/WORKFLOW.md",
        ],
    )

    assert selected == ["automations/flow/module.json"]


def test_determinism_changed_deleted_and_invalid_manifest_block(tmp):
    root = tmp / "repo"
    write_text(skill_manifest(root, "invalid"), "{not json}\n")

    report = repo_determinism.build_plan(
        root,
        changed=True,
        changed_paths=[
            ".agents/skills/deleted/scripts/check.py",
            ".agents/skills/invalid/module.json",
        ],
    )

    assert report["ok"] is False
    assert report["status"] == "blocked"
    assert [row["status"] for row in report["modules"]] == [
        "missing-manifest",
        "invalid-manifest",
    ]
    assert report["summary"]["blocked_module_count"] == 2


def test_determinism_all_discovery_and_command_order_are_stable(tmp):
    root = tmp / "repo"
    write_json(skill_manifest(root, "zeta"), manifest("zeta", [command("z-last"), command("a-first")]))
    write_json(skill_manifest(root, "alpha"), manifest("alpha", [command("middle")]))
    write_json(workflow_manifest(root, "beta"), manifest("beta", [command("workflow-check")], kind="workflow"))

    report = repo_determinism.build_plan(root, all_modules=True)

    assert report["ok"] is True
    assert [row["module_id"] for row in report["commands"]] == [
        "alpha",
        "zeta",
        "zeta",
        "beta",
    ]
    assert [row["command_id"] for row in report["commands"]] == [
        "middle",
        "a-first",
        "z-last",
        "workflow-check",
    ]


def test_determinism_explicit_subset_and_deep_all_strict(tmp):
    root = tmp / "repo"
    write_json(
        skill_manifest(root, "demo"),
        manifest(
            "demo",
            [command("first"), command("second"), command("third")],
            replay_commands=["second"],
        ),
    )

    normal = repo_determinism.build_plan(root, all_modules=True)
    deep = repo_determinism.build_plan(root, all_modules=True, deep=True)

    assert [row["command_id"] for row in normal["commands"]] == ["second"]
    assert [row["command_id"] for row in deep["commands"]] == [
        "first",
        "second",
        "third",
    ]
    assert normal["commands"][0]["selection_source"] == "determinism.replay_commands"
    assert all(row["selection_source"] == "deep-strict-read-only" for row in deep["commands"])


def test_determinism_implicit_strict_warning_and_fourteen_placeholders_never_run(tmp):
    root = tmp / "repo"
    marker = root / "must-not-exist.txt"
    placeholder_commands = [
        command(f"placeholder-{index:02d}", f"<value-{index}>")
        for index in range(14)
    ]
    write_json(
        skill_manifest(root, "implicit"),
        manifest("implicit", placeholder_commands, replay_commands=None),
    )
    write_text(
        root / "scripts" / "placeholder-00.py",
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
    )

    report = repo_determinism.build_plan(root, all_modules=True)

    assert report["ok"] is False
    assert report["summary"]["blocked_placeholder_count"] == 14
    assert len(report["commands"]) == 14
    assert all(row["status"] == "blocked-placeholder" for row in report["commands"])
    assert any("implicit-strict-default" in warning for warning in report["warnings"])
    assert marker.exists() is False
