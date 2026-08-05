#!/usr/bin/env python3
"""Self-tests for repo-navigation."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import install_navigation_workflow
import navigation_core
import project_context
import source_focus
import update_navigation


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def fixture_project(root: Path) -> None:
    write(root / "README.md", "# Demo\n\nRun `dotnet test` and `npm test`.\n")
    write(root / "AGENTS.md", "# Agent Notes\n")
    write(root / "automations" / "hooks.json", "{}\n")
    write(root / "Demo.sln", "\n")
    write(root / "src" / "Demo.Api" / "Demo.Api.csproj", "<Project Sdk=\"Microsoft.NET.Sdk.Web\" />\n")
    write(root / "src" / "Demo.Api" / "Program.cs", "public class Program {}\n")
    write(root / "pyproject.toml", "[project]\nname = \"demo\"\n")
    write(root / "tests" / "test_demo.py", "def test_demo():\n    assert True\n")
    write(root / "package.json", '{"scripts":{"test":"vitest","build":"vite build"},"dependencies":{"react":"latest"}}\n')
    write(root / "src" / "client" / "demo.ts", "export function demo() { return 1; }\n")
    write(root / "src" / "client" / "index.ts", "import { demo } from './demo';\nexport const value = demo();\n")
    write(root / "node_modules" / "ignored" / "index.js", "ignored\n")


def test_navigation_policy_reader_is_canonical_and_v2_only(tmp: Path) -> None:
    fixture_project(tmp)
    document = navigation_core.repo_policy.default_policy_document()
    document["limits"]["navigation"]["map_warn_words"] = 999
    write(
        tmp / navigation_core.repo_policy.PROJECT_POLICY_PATH,
        json.dumps(document, indent=2) + "\n",
    )

    assert navigation_core.project_policy_int("limits.navigation.map_warn_words", start=tmp) == 999

    write(
        tmp / navigation_core.repo_policy.PROJECT_POLICY_PATH,
        json.dumps({"schema_version": 1, "limits": document["limits"]}) + "\n",
    )
    try:
        navigation_core.project_policy_int("limits.navigation.map_warn_words", start=tmp)
    except ValueError as exc:
        assert "schema_version must be 2" in str(exc)
    else:
        raise AssertionError("v1 project policy must be rejected by navigation consumers")


def test_install_generates_navigation_workflow(tmp: Path) -> None:
    fixture_project(tmp)
    report = install_navigation_workflow.install_navigation_workflow(tmp, write=True)

    assert report["ok"] is True
    workflow = tmp / "automations" / "navigation"
    for relative in (
        "WORKFLOW.md",
        "module.json",
        "metadata/workflow-metadata.json",
        "instructions.md",
        "diagrams/navigation-process.mmd",
        "diagrams/navigation-process.svg",
        "diagrams/navigation-connection.mmd",
        "diagrams/navigation-connection.svg",
        "suites/workflow-evals.json",
        "scripts/update_navigation.py",
        "scripts/navigation_core.py",
        "scripts/project_context.py",
        "artifacts/maps/NAVIGATION.md",
        "artifacts/maps/TECHNICAL_CONTEXT.md",
        "artifacts/maps/CONVENTIONS.md",
        "artifacts/maps/HANDOFF.md",
        "artifacts/maps/handoff.json",
        "artifacts/maps/staleness.json",
        "artifacts/maps/owners/workflow-navigation.md",
    ):
        assert workflow.joinpath(relative).exists(), relative

    assert workflow.joinpath("scripts/navigation_core.py").read_bytes() == (SCRIPT_DIR / "navigation_core.py").read_bytes()

    maps = workflow / "artifacts" / "maps"
    workflow_text = workflow.joinpath("WORKFLOW.md").read_text(encoding="utf-8")
    manifest = json.loads(workflow.joinpath("module.json").read_text(encoding="utf-8"))
    assert "## Example Prompts" in workflow_text
    assert "## Read-Only Dogfood" in workflow_text
    assert "navigation-process.svg" in workflow_text
    assert "navigation-process.mmd" in workflow_text
    assert "navigation-connection.svg" in workflow_text
    assert "navigation-connection.mmd" in workflow_text
    assert manifest["context_evidence"]["required"] is True
    assert manifest["schema_version"] == 3
    assert manifest["version"] == "1.0.0"
    assert manifest["context"] == install_navigation_workflow.module_contract_v3.conventional_context("navigation")
    expected_template_layers = install_navigation_workflow.module_contract_v3.conventional_template_layers("navigation")
    expected_template_layers["profiles"] = {}
    assert manifest["template_layers"] == expected_template_layers
    assert manifest["extensions"] == {}
    assert all(isinstance(command, dict) for command in manifest["commands"])
    assert all(
        {"id", "argv", "timeout_seconds", "working_directory", "effects"}
        <= set(command)
        for command in manifest["commands"]
    )
    assert manifest["routing"] == {
        "activation_terms": [
            "navigation", "project-context", "staleness"
        ],
        "terms": [
            "navigation", "map", "maps", "handoff", "capsule", "project-context", "staleness", "stale", "refresh", "read-order"
        ],
        "threshold": 2,
        "winner_margin": 1,
    }
    assert manifest["metadata_path"] == "metadata/workflow-metadata.json"
    generated_metadata = json.loads(
        workflow.joinpath("metadata/workflow-metadata.json").read_text(encoding="utf-8")
    )
    assert generated_metadata == {
        "updated": install_navigation_workflow.NAVIGATION_WORKFLOW_UPDATED
    }
    assert manifest["context_evidence"]["start_queries"][0]["fallback_paths"]
    commands_by_id = {command["id"]: command for command in manifest["commands"]}
    strict_argvs = [
        commands_by_id[command_id]["argv"]
        for command_id in manifest["strict_read_only_commands"]
    ]
    assert any(
        ["validate-automations", "--name", "navigation"]
        == argv[3:6]
        for argv in strict_argvs
    )
    assert not any(
        any(
            argv[index : index + 4]
            == ["workflow", "smoke", "--name", "navigation"]
            for index in range(len(argv) - 3)
        )
        and "--dry-run" in argv
        for argv in strict_argvs
    )
    assert workflow.joinpath("module.json").read_text(encoding="utf-8").endswith("\n")
    process_mmd = workflow.joinpath("diagrams", "navigation-process.mmd").read_text(encoding="utf-8")
    connection_mmd = workflow.joinpath("diagrams", "navigation-connection.mmd").read_text(encoding="utf-8")
    process_svg = workflow.joinpath("diagrams", "navigation-process.svg").read_text(encoding="utf-8")
    connection_svg = workflow.joinpath("diagrams", "navigation-connection.svg").read_text(encoding="utf-8")
    assert process_mmd.startswith("graph TD;")
    assert connection_mmd.startswith("graph LR;")
    assert "flowchart" not in process_mmd + connection_mmd
    assert "classDef" not in process_mmd + connection_mmd
    assert 'data-mermaid-vertical-padding="24"' in process_svg
    assert 'data-mermaid-vertical-padding="24"' in connection_svg
    assert "background-color: transparent" in process_svg
    assert "background-color: transparent" in connection_svg
    eval_suite = json.loads(workflow.joinpath("suites", "workflow-evals.json").read_text(encoding="utf-8"))
    assert eval_suite["workflow_name"] == "navigation"
    assert eval_suite["evals"]
    eval_text = json.dumps(eval_suite)
    assert "## Read-Only Dogfood" in eval_text
    assert "workflow smoke --name navigation --dry-run" not in eval_text
    assert "automations/navigation/scripts/project_context.py --target . --write" in eval_text
    instructions = workflow.joinpath("instructions.md").read_text(encoding="utf-8")
    assert "## Always Load" in instructions
    assert "## Stop Rules" in instructions
    assert "## Completion Contract" in instructions
    scan = update_navigation.navigation_core.build_scan(tmp)
    entry_paths = {item["path"] for item in scan["entries"]}
    symbol_names = {item["name"] for item in scan["symbols"]}
    category_ids = {item["category"] for item in scan["codebase_categories"]}
    assert "src/Demo.Api/Demo.Api.csproj" in entry_paths
    assert "pyproject.toml" in entry_paths
    assert "package.json" in entry_paths
    assert {"Program", "test_demo", "value"}.issubset(symbol_names)
    assert {"stack", "structure", "entrypoints", "testing"}.issubset(category_ids)
    assert all("node_modules" not in path for path in entry_paths)
    assert all(".agents/skills/" not in path or "/fixtures/" not in path for path in entry_paths)
    relationship_types = {item["type"] for item in scan["relationships"]}
    assert "imports" in relationship_types
    navigation = maps.joinpath("NAVIGATION.md").read_text(encoding="utf-8")
    assert "route-first" in navigation
    assert "Relationships" in navigation
    assert "Raw navigation JSON is tool-only" in navigation
    assert ".agents/local-ai/cache/command-output/" in navigation
    assert len(navigation) < 5000
    technical_context = maps.joinpath("TECHNICAL_CONTEXT.md").read_text(encoding="utf-8")
    assert "review-layer signals" in technical_context
    assert "source truth" not in technical_context.lower()
    conventions = maps.joinpath("CONVENTIONS.md").read_text(encoding="utf-8")
    assert "Observed file extensions" in conventions
    assert "Inferred from deterministic file facts" in conventions
    assert "Observed symbols" in conventions
    handoff = maps.joinpath("HANDOFF.md").read_text(encoding="utf-8")
    assert "Project Context Handoff" in handoff
    assert "Avoid Unless Needed" in handoff
    assert "Owner Capsules" in handoff
    assert "automations/navigation/artifacts/maps/handoff.json" in handoff
    assert "automations/navigation/artifacts/maps/staleness.json" in handoff
    assert "automations/navigation/artifacts/maps/project-map.json" not in handoff
    assert "automations/navigation/artifacts/maps/code-graph.json" not in handoff
    assert ".agents/local-ai/cache/" in handoff
    assert ".agents/local-ai/cache/command-output/" in handoff
    owner_capsule = maps.joinpath("owners", "workflow-navigation.md").read_text(encoding="utf-8")
    assert "Owner Capsule: workflow:navigation" in owner_capsule
    assert "Raw navigation JSON is tool-only" in owner_capsule
    assert '"validate-automations","--name","navigation"' in owner_capsule
    assert "workflow smoke --name navigation --dry-run" not in owner_capsule
    assert update_navigation.navigation_core.owner_from_path("automations/hooks.json") == ("repo", ".")
    assert not maps.joinpath("owners", "workflow-hooks-json.md").exists()
    handoff_json = json.loads(maps.joinpath("handoff.json").read_text(encoding="utf-8"))
    assert "automations/navigation/artifacts/maps/HANDOFF.md" in handoff_json["load_first"]
    assert "automations/navigation/artifacts/maps/owners/workflow-navigation.md" in handoff_json["owner_capsules"]
    assert "automations/navigation/artifacts/maps/handoff.json" in handoff_json["tool_only_maps"]
    assert "automations/navigation/artifacts/maps/staleness.json" in handoff_json["tool_only_maps"]
    assert "automations/navigation/artifacts/maps/project-map.json" not in handoff_json["tool_only_maps"]
    assert "automations/navigation/artifacts/maps/code-graph.json" not in handoff_json["tool_only_maps"]
    assert not maps.joinpath("project-map.json").exists()
    assert not maps.joinpath("code-graph.json").exists()
    assert ".agents/local-ai/cache/" in handoff_json["avoid_unless_needed"]
    assert ".agents/local-ai/cache/command-output/" in handoff_json["avoid_unless_needed"]
    assert handoff_json["staleness"]["check_command"]
    staleness_json = json.loads(maps.joinpath("staleness.json").read_text(encoding="utf-8"))
    assert "automations/navigation/artifacts/maps/owners/workflow-navigation.md" in staleness_json["map_files"]


def test_install_write_skips_unchanged_workflow_files_and_maps(tmp: Path) -> None:
    fixture_project(tmp)
    install_navigation_workflow.install_navigation_workflow(tmp, write=True)

    second_install = install_navigation_workflow.install_navigation_workflow(tmp, write=True)

    assert second_install["ok"] is True
    assert second_install["written"] == []


def test_owner_capsule_uses_safe_help_for_placeholder_commands(tmp: Path) -> None:
    fixture_project(tmp)
    write(
        tmp / ".agents" / "skills" / "demo-benchmarking" / "SKILL.md",
        """# Demo Benchmarking

```shell
python -B .agents/skills/demo-benchmarking/scripts/prepare_benchmark_run.py --suite <suite.json> --task-id <task-id> --write
python -B .agents/manage.py benchmark capability-matrix --baseline-root <old-root> --candidate-root <new-root> --format json --compact
```
""",
    )
    write(
        tmp / ".agents" / "skills" / "demo-benchmarking" / "module.json",
        '{"schema_version":3,"kind":"skill","id":"demo-benchmarking","version":"1.0.0","summary":"Demo.","owners":["engineering"],"inputs":["SKILL.md"],"outputs":[],"commands":[],"strict_read_only_commands":[],"related_modules":[],"validation":[],"external_access":{"source_systems":[],"credential_expectations":"none","data_copied_locally":[],"attachments_retrieved":false},"local_ai":{"use_cases":[]},"risk":{"credentials":false,"destructive":false,"generated_settings":false,"installs":false,"network":false,"production_writes":false,"uploads":false,"profile":"read-only"},"extensions":{},"status":"accepted"}\n',
    )
    write(tmp / ".agents" / "skills" / "demo-benchmarking" / "scripts" / "run_self_tests.py", "print('ok')\n")
    write(tmp / ".agents" / "skills" / "demo-benchmarking" / "scripts" / "prepare_benchmark_run.py", "print('help')\n")

    scan = update_navigation.navigation_core.build_scan(tmp)
    capsule = update_navigation.navigation_core.owner_capsule_outputs(scan)[
        "automations/navigation/artifacts/maps/owners/skill-demo-benchmarking.md"
    ]

    assert "prepare_benchmark_run.py --help" in capsule
    assert "benchmark capability-matrix --help" in capsule
    assert "scripts/run_self_tests.py" not in capsule
    assert "prepare_benchmark_run.py --suite" not in capsule
    assert "capability-matrix --baseline-root" not in capsule


def test_workflow_owner_capsule_prefers_strict_read_only_commands(tmp: Path) -> None:
    fixture_project(tmp)
    write(tmp / "automations" / "demo-workflow" / "WORKFLOW.md", "# Demo Workflow\n")
    write(tmp / "automations" / "demo-workflow" / "instructions.md", "# Demo Instructions\n")
    write(
        tmp / "automations" / "demo-workflow" / "module.json",
        json.dumps(
            {
                "schema_version": 3,
                "kind": "workflow",
                "id": "demo-workflow",
                "version": "1.0.0",
                "summary": "Demo workflow.",
                "owners": ["engineering"],
                "inputs": ["WORKFLOW.md"],
                "outputs": ["runs/<run-id>/run.json"],
                "commands": [
                    {
                        "id": "benchmark",
                        "argv": [
                            "python",
                            "-B",
                            "automations/demo-workflow/scripts/benchmark.py",
                            "--run-dir",
                            "automations/demo-workflow/runs/<run-id>",
                        ],
                        "timeout_seconds": 300,
                        "working_directory": "repository",
                        "effects": ["temporary_write"],
                    },
                    {
                        "id": "validate",
                        "argv": [
                            "python",
                            "-B",
                            ".agents/manage.py",
                            "validate-automations",
                            "--name",
                            "demo-workflow",
                            "--summary",
                            "--compact",
                            "--format",
                            "json",
                        ],
                        "timeout_seconds": 300,
                        "working_directory": "repository",
                        "effects": [],
                    },
                    {
                        "id": "metadata",
                        "argv": [
                            "python",
                            "-B",
                            ".agents/manage.py",
                            "workflow",
                            "metadata",
                            "inspect",
                            "--name",
                            "demo-workflow",
                            "--summary",
                            "--compact",
                            "--format",
                            "json",
                        ],
                        "timeout_seconds": 300,
                        "working_directory": "repository",
                        "effects": [],
                    },
                    {
                        "id": "spaced-argument",
                        "argv": ["tool", "arg with spaces"],
                        "timeout_seconds": 300,
                        "working_directory": "repository",
                        "effects": [],
                    },
                ],
                "strict_read_only_commands": ["validate", "metadata", "spaced-argument"],
                "related_modules": [],
                "validation": ["python -B .agents/manage.py validate-automations --name demo-workflow"],
                "external_access": {
                    "source_systems": [],
                    "credential_expectations": "none",
                    "data_copied_locally": [],
                    "attachments_retrieved": False,
                },
                "local_ai": {"use_cases": []},
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
                "extensions": {},
            },
            indent=2,
        )
        + "\n",
    )
    write(
        tmp / ".agents" / "local-ai" / "BENCHMARKS.md",
        "Run `python -B automations/demo-workflow/scripts/benchmark.py --run-dir automations/demo-workflow/runs/`.\n",
    )

    scan = update_navigation.navigation_core.build_scan(tmp)
    strict_rows = [
        row
        for row in scan["strict_read_only_commands"]
        if row.get("path") == "automations/demo-workflow/module.json"
    ]
    assert ["tool", "arg with spaces"] in [row["argv"] for row in strict_rows]
    groups = update_navigation.navigation_core.owner_capsule_groups(scan)
    demo_group = next(row for row in groups if row["owner"] == "workflow:demo-workflow")
    assert ["tool", "arg with spaces"] in demo_group["validation_commands"]
    capsule = update_navigation.navigation_core.owner_capsule_outputs(scan)[
        "automations/navigation/artifacts/maps/owners/workflow-demo-workflow.md"
    ]

    assert '"workflow","metadata","inspect","--name","demo-workflow"' in capsule
    assert '["tool","arg with spaces"]' in capsule
    assert "tool arg with spaces" not in capsule
    assert "JSON argv display is not shell-executable command text" in capsule
    assert "workflow smoke --name demo-workflow --dry-run" not in capsule
    assert "benchmark.py --run-dir" not in capsule


def test_workflow_owner_capsule_keeps_late_strict_read_only_commands(tmp: Path) -> None:
    fixture_project(tmp)
    for index in range(25):
        name = f"demo-workflow-{index:02d}"
        write(tmp / "automations" / name / "WORKFLOW.md", f"# {name}\n")
        write(
            tmp / "automations" / name / "module.json",
            json.dumps(
                {
                    "schema_version": 3,
                    "kind": "workflow",
                    "id": name,
                    "version": "1.0.0",
                    "summary": "Demo workflow.",
                    "owners": ["engineering"],
                    "inputs": ["WORKFLOW.md"],
                    "outputs": ["runs/<run-id>/run.json"],
                    "commands": [
                        {
                            "id": command_id,
                            "argv": argv,
                            "timeout_seconds": 300,
                            "working_directory": "repository",
                            "effects": [],
                        }
                        for command_id, argv in (
                            (
                                "validate",
                                [
                                    "python", "-B", ".agents/manage.py", "validate-automations",
                                    "--name", name, "--summary", "--compact", "--format", "json",
                                ],
                            ),
                            (
                                "metadata",
                                [
                                    "python", "-B", ".agents/manage.py", "workflow", "metadata", "inspect",
                                    "--name", name, "--summary", "--compact", "--format", "json",
                                ],
                            ),
                            (
                                "hooks",
                                [
                                    "python", "-B", ".agents/manage.py", "workflow", "hooks",
                                    "--name", name, "--check", "--summary", "--compact", "--format", "json",
                                ],
                            ),
                            (
                                "scorecard",
                                [
                                    "python", "-B", ".agents/manage.py", "workflow", "scorecard",
                                    "--name", name, "--no-lifecycle", "--summary", "--compact", "--format", "json",
                                ],
                            ),
                        )
                    ],
                    "strict_read_only_commands": ["validate", "metadata", "hooks", "scorecard"],
                    "related_modules": [],
                    "validation": [f"python -B .agents/manage.py validate-automations --name {name}"],
                    "external_access": {
                        "source_systems": [],
                        "credential_expectations": "none",
                        "data_copied_locally": [],
                        "attachments_retrieved": False,
                    },
                    "local_ai": {"use_cases": []},
                    "extensions": {},
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
                },
                indent=2,
            )
            + "\n",
        )

    scan = update_navigation.navigation_core.build_scan(tmp)
    capsule = update_navigation.navigation_core.owner_capsule_outputs(scan)[
        "automations/navigation/artifacts/maps/owners/workflow-demo-workflow-24.md"
    ]

    assert '"validate-automations","--name","demo-workflow-24"' in capsule
    assert "workflow smoke --name demo-workflow-24 --dry-run" not in capsule


def test_strict_read_only_commands_reject_legacy_untyped_commands(tmp: Path) -> None:
    fixture_project(tmp)
    module_path = tmp / "automations" / "untyped-workflow" / "module.json"
    write(
        module_path,
        json.dumps(
            {
                "schema_version": 2,
                "kind": "workflow",
                "id": "untyped-workflow",
                "commands": [],
                "strict_read_only_commands": [
                    "python -B .agents/manage.py validate-automations --name untyped-workflow"
                ],
            }
        )
        + "\n",
    )

    scan = update_navigation.navigation_core.build_scan(tmp)

    assert not any(
        row.get("path") == "automations/untyped-workflow/module.json"
        for row in scan["strict_read_only_commands"]
    )


def test_update_check_detects_stale_maps(tmp: Path) -> None:
    fixture_project(tmp)
    install_navigation_workflow.install_navigation_workflow(tmp, write=True)

    ok_report = update_navigation.update_navigation(tmp, write=False, check=True)
    assert ok_report["ok"] is True
    assert ok_report["installation_status"] == "installed"

    write(tmp / "src" / "Demo.Api" / "NewEndpoint.cs", "public class NewEndpoint {}\n")
    stale_report = update_navigation.update_navigation(tmp, write=False, check=True)
    assert stale_report["ok"] is False
    assert stale_report["status"] == "stale"
    assert stale_report["installation_status"] == "installed"
    assert any("artifacts/maps/" in item for item in stale_report["stale"])
    assert "src/Demo.Api/NewEndpoint.cs" in stale_report["stale_source_changes"]["added"]
    assert stale_report["map_size_budget"]["status"] == "ok"
    assert any("ignored build" in item for item in stale_report["route_quality_warnings"])

    refreshed = update_navigation.update_navigation(tmp, write=True, check=False)
    assert refreshed["ok"] is True
    final_report = update_navigation.update_navigation(tmp, write=False, check=True)
    assert final_report["ok"] is True


def test_staleness_metadata_records_source_git_tree_hash(tmp: Path) -> None:
    fixture_project(tmp)
    subprocess.run(["git", "init", "--quiet"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "self-tests@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Self Tests"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=tmp, check=True)
    tracked = subprocess.run(
        ["git", "--no-optional-locks", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=tmp,
        check=True,
        capture_output=True,
    ).stdout
    digest = hashlib.sha256()
    paths = []
    for raw_path in sorted({item for item in tracked.split(b"\0") if item}):
        path = raw_path.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        if update_navigation.navigation_core.skip_generated_navigation(path) or any(
            part in update_navigation.navigation_core.IGNORED_DIRS for part in Path(path).parts
        ):
            continue
        paths.append(path)
    object_ids = subprocess.run(
        ["git", "--no-optional-locks", "hash-object", "--stdin-paths"],
        cwd=tmp,
        check=True,
        input="".join(f"{path}\n" for path in paths),
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    for path, object_id in zip(paths, object_ids):
        digest.update(path.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(object_id.encode("ascii"))
        digest.update(b"\0")
    expected = digest.hexdigest()

    outputs, _scan = update_navigation.navigation_core.build_outputs(tmp)
    staleness = json.loads(outputs["automations/navigation/artifacts/maps/staleness.json"])

    assert staleness.get("source_git_tree_hash") == expected, staleness
    assert staleness.get("source_git_tree_kind") == update_navigation.navigation_core.SOURCE_GIT_TREE_KIND, staleness
    assert staleness.get("source_hash_kind") == update_navigation.navigation_core.SOURCE_HASH_KIND, staleness
    assert staleness.get("map_hashes"), staleness
    assert set(staleness["map_hashes"]) == set(outputs) - {
        "automations/navigation/artifacts/maps/staleness.json"
    }, staleness["map_hashes"]


def test_source_git_tree_hash_is_stable_across_dirty_generation_and_commit(tmp: Path) -> None:
    fixture_project(tmp)
    subprocess.run(["git", "init", "--quiet"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "self-tests@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Self Tests"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "AGENTS.md"], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "existing target"], cwd=tmp, check=True)

    generated_hash = update_navigation.navigation_core.source_git_tree_hash(tmp)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "install harness"], cwd=tmp, check=True)
    committed_hash = update_navigation.navigation_core.source_git_tree_hash(tmp)

    assert generated_hash == committed_hash, (generated_hash, committed_hash)


def test_source_git_tree_hash_is_stable_across_staging_a_deletion(tmp: Path) -> None:
    fixture_project(tmp)
    subprocess.run(["git", "init", "--quiet"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "self-tests@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Self Tests"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=tmp, check=True)

    (tmp / "AGENTS.md").unlink()
    unstaged_hash = update_navigation.navigation_core.source_git_tree_hash(tmp)
    subprocess.run(["git", "add", "-u"], cwd=tmp, check=True)
    staged_hash = update_navigation.navigation_core.source_git_tree_hash(tmp)

    assert unstaged_hash, "unstaged deletion disabled the navigation cache"
    assert unstaged_hash == staged_hash, (unstaged_hash, staged_hash)


def test_navigation_outputs_are_portable_across_clone_and_linked_worktree(tmp: Path) -> None:
    repository = tmp / "repository"
    linked = tmp / "linked"
    clone = tmp / "clone"
    fixture_project(repository)
    write(repository / ".gitattributes", "* text=auto\n*.mmd text\n")
    write(repository / "docs" / "diagram.mmd", "flowchart TD\n    A --> B\n")
    subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "self-tests@example.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Self Tests"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=repository, check=True)
    subprocess.run(
        ["git", "-c", "core.longpaths=true", "worktree", "add", "--quiet", "--detach", str(linked)],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "core.longpaths=true",
            "-c",
            "core.autocrlf=false",
            "clone",
            "--quiet",
            "--no-local",
            str(repository),
            str(clone),
        ],
        check=True,
    )
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=clone, check=True)

    write_bytes(repository / "docs" / "diagram.mmd", b"flowchart TD\r\n    A --> B\r\n")
    assert (
        update_navigation.navigation_core.source_git_tree_hash(repository)
        == update_navigation.navigation_core.source_git_tree_hash(linked)
    )

    repository_outputs, _repository_scan = update_navigation.navigation_core.build_outputs(repository)
    linked_outputs, _linked_scan = update_navigation.navigation_core.build_outputs(linked)
    clone_outputs, _clone_scan = update_navigation.navigation_core.build_outputs(clone)

    different = sorted(
        path
        for path in set(repository_outputs) | set(linked_outputs)
        if repository_outputs.get(path) != linked_outputs.get(path)
    )
    repository_staleness = json.loads(
        repository_outputs["automations/navigation/artifacts/maps/staleness.json"]
    )
    linked_staleness = json.loads(
        linked_outputs["automations/navigation/artifacts/maps/staleness.json"]
    )
    staleness_differences = sorted(
        key
        for key in set(repository_staleness) | set(linked_staleness)
        if repository_staleness.get(key) != linked_staleness.get(key)
    )
    assert not different, {"outputs": different, "staleness": staleness_differences}
    assert repository_outputs == clone_outputs


def test_navigation_source_hash_preserves_binary_newline_bytes(tmp: Path) -> None:
    crlf = tmp / "payload-crlf.bin"
    lf = tmp / "payload-lf.bin"
    write_bytes(crlf, b"\x01\r\n\x02")
    write_bytes(lf, b"\x01\n\x02")

    assert update_navigation.navigation_core.sha256_file(crlf) != update_navigation.navigation_core.sha256_file(lf)


def test_navigation_source_hash_honors_explicit_git_binary_attribute(tmp: Path) -> None:
    write(tmp / ".gitattributes", "*.opaque -text\n")
    opaque = tmp / "payload.opaque"
    write_bytes(opaque, b"opaque\r\npayload\r\n")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "self-tests@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Self Tests"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=tmp, check=True)

    crlf_hash = update_navigation.navigation_core.build_scan(tmp)["hashes"]["payload.opaque"]
    write_bytes(opaque, b"opaque\npayload\n")
    lf_hash = update_navigation.navigation_core.build_scan(tmp)["hashes"]["payload.opaque"]

    assert crlf_hash != lf_hash


def test_source_git_tree_hash_omits_cache_when_custom_filter_is_active(tmp: Path) -> None:
    fixture_project(tmp)
    subprocess.run(["git", "init", "--quiet"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "self-tests@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Self Tests"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=tmp, check=True)
    real_run = update_navigation.navigation_core.subprocess.run

    def guarded_run(args, **kwargs):
        if "check-attr" in args:
            paths = [item for item in kwargs["input"].split(b"\0") if item]
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=b"".join(
                    path
                    + b"\0filter\0"
                    + (b"dangerous" if path == b"README.md" else b"unspecified")
                    + b"\0"
                    for path in paths
                ),
                stderr=b"",
            )
        if "hash-object" in args:
            raise AssertionError("hash-object must not run when a custom filter is active")
        return real_run(args, **kwargs)

    with mock.patch.object(update_navigation.navigation_core.subprocess, "run", side_effect=guarded_run):
        assert update_navigation.navigation_core.source_git_tree_hash(tmp) == ""


def test_git_path_attributes_handles_unicode_filenames(tmp: Path) -> None:
    unicode_path = tmp / "日本語.md"
    write(unicode_path, "# Unicode path\n")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "self-tests@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Self Tests"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=tmp, check=True)

    attributes = update_navigation.navigation_core.git_path_attributes(tmp, ["日本語.md"], ("text",))
    scan = update_navigation.navigation_core.build_scan(tmp)
    outputs, _output_scan = update_navigation.navigation_core.build_outputs(tmp)
    staleness = json.loads(outputs["automations/navigation/artifacts/maps/staleness.json"])

    assert attributes == {"日本語.md": {"text": "unspecified"}}, attributes
    assert "日本語.md" in scan["hashes"], scan["hashes"]
    assert staleness.get("source_git_tree_hash"), staleness


def test_navigation_scan_excludes_gitignored_text_sources(tmp: Path) -> None:
    fixture_project(tmp)
    write(tmp / ".gitignore", "ignored-config.txt\n")
    write(tmp / "ignored-config.txt", "secret-like local config must not enter maps\n")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "self-tests@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Self Tests"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=tmp, check=True)

    scan = update_navigation.navigation_core.build_scan(tmp)

    assert "ignored-config.txt" not in scan["hashes"], scan["hashes"]


def test_navigation_scan_filters_gitignored_files_before_file_budget(tmp: Path) -> None:
    write(tmp / ".gitignore", "a-ignored*.txt\n")
    write(tmp / "a-ignored-1.txt", "ignored\n")
    write(tmp / "a-ignored-2.txt", "ignored\n")
    write(tmp / "z-tracked.txt", "tracked\n")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "self-tests@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Self Tests"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=tmp, check=True)

    scan = update_navigation.navigation_core.build_scan(tmp, max_files=2)

    assert "z-tracked.txt" in scan["hashes"], scan["hashes"]


def test_update_check_reports_not_installed_when_all_outputs_missing(tmp: Path) -> None:
    fixture_project(tmp)

    report = update_navigation.update_navigation(tmp, write=False, check=True)

    assert report["ok"] is False
    assert report["status"] == "not-installed"
    assert report["installation_status"] == "not-installed"
    assert any(item.endswith("HANDOFF.md") for item in report["stale"])


def test_update_write_skips_unchanged_outputs(tmp: Path) -> None:
    fixture_project(tmp)
    install_navigation_workflow.install_navigation_workflow(tmp, write=True)

    second_write = update_navigation.update_navigation(tmp, write=True, check=False)

    assert second_write["ok"] is True
    assert second_write["written"] == []


def test_update_check_detects_obsolete_owner_capsules(tmp: Path) -> None:
    fixture_project(tmp)
    install_navigation_workflow.install_navigation_workflow(tmp, write=True)
    obsolete = tmp / "automations" / "navigation" / "artifacts" / "maps" / "owners" / "obsolete.md"
    write(obsolete, "# Old Capsule\n")

    stale_report = update_navigation.update_navigation(tmp, write=False, check=True)

    assert stale_report["ok"] is False
    assert "automations/navigation/artifacts/maps/owners/obsolete.md" in stale_report["stale"]

    refreshed = update_navigation.update_navigation(tmp, write=True, check=False)
    assert refreshed["ok"] is True
    assert not obsolete.exists()
    final_report = update_navigation.update_navigation(tmp, write=False, check=True)
    assert final_report["ok"] is True


def test_repo_navigation_check_wrapper_matches_update_check(tmp: Path) -> None:
    fixture_project(tmp)
    install_navigation_workflow.install_navigation_workflow(tmp, write=True)
    write(tmp / "src" / "Demo.Api" / "NewEndpoint.cs", "public class NewEndpoint {}\n")

    direct = update_navigation.update_navigation(tmp, write=False, check=True)
    wrapper = subprocess.run(
        [
            sys.executable,
            "-B",
            str(SCRIPT_DIR.parent / "repo_navigation.py"),
            "check",
            "--target",
            str(tmp),
            "--format",
            "json",
        ],
        text=True,
        capture_output=True,
    )

    assert wrapper.returncode == 1
    wrapped = json.loads(wrapper.stdout)
    for key in ("ok", "status", "installation_status", "stale", "stale_source_changes", "map_size_budget"):
        assert wrapped[key] == direct[key]


def test_update_check_ignores_runtime_evidence_and_local_ai_cache(tmp: Path) -> None:
    fixture_project(tmp)
    install_navigation_workflow.install_navigation_workflow(tmp, write=True)

    write(
        tmp / "automations" / "user-story-workflow" / "runs" / "smoke-first-run" / "run.json",
        '{"status":"partial"}\n',
    )
    write(
        tmp / "automations" / "bug-ticket-workflow" / "runs" / "smoke-first-run" / "REPORT.md",
        "# Smoke Report\n",
    )
    write(tmp / ".agents" / "local-ai" / "cache" / "results" / "query.json", '{"ok":true}\n')
    write(tmp / ".agents" / "local-ai" / "local.settings.json", '{"backend":"vulkan"}\n')
    write(tmp / ".agents" / "local-ai" / "secrets.local.json", '{"token":"secret"}\n')
    write(tmp / ".agents" / "local-ai" / "downloads" / "model.zip", "download\n")
    write(tmp / ".agents" / "local-ai" / "bundle" / "licenses" / "license.txt", "license\n")
    write_bytes(
        tmp / ".agents" / "local-ai" / "bundle" / "runtimes" / "llama" / "llama.exe",
        b"\0binary",
    )
    write(tmp / ".agents" / ".deps" / "helper" / "module.py", "def helper():\n    return 1\n")
    write_bytes(tmp / ".agents" / "tools" / "cache" / "ripgrep" / "rg.exe", b"\0binary")

    report = update_navigation.update_navigation(tmp, write=False, check=True)
    assert report["ok"] is True
    changes = report["stale_source_changes"]
    assert "automations/user-story-workflow/runs/smoke-first-run/run.json" not in changes["added"]
    assert "automations/bug-ticket-workflow/runs/smoke-first-run/REPORT.md" not in changes["added"]
    assert ".agents/local-ai/cache/results/query.json" not in changes["added"]
    assert ".agents/local-ai/local.settings.json" not in changes["added"]
    assert ".agents/local-ai/secrets.local.json" not in changes["added"]
    assert ".agents/local-ai/downloads/model.zip" not in changes["added"]
    assert ".agents/local-ai/bundle/licenses/license.txt" not in changes["added"]
    assert ".agents/local-ai/bundle/runtimes/llama/llama.exe" not in changes["added"]
    assert ".agents/.deps/helper/module.py" not in changes["added"]
    assert ".agents/tools/cache/ripgrep/rg.exe" not in changes["added"]

    _, scan = update_navigation.navigation_core.build_outputs(tmp)
    scan_text = json.dumps({"entries": scan["entries"], "skipped": scan["skipped"]}, sort_keys=True)
    assert ".agents/local-ai/downloads" not in scan_text
    assert ".agents/local-ai/local.settings.json" not in scan_text
    assert ".agents/local-ai/secrets.local.json" not in scan_text
    assert ".agents/local-ai/bundle" not in scan_text
    assert ".agents/.deps" not in scan_text
    assert ".agents/tools/cache" not in scan_text


def test_update_check_ignores_local_ignored_directory_presence_in_git_repo(tmp: Path) -> None:
    fixture_project(tmp)
    subprocess.run(["git", "init", "--quiet"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "self-tests@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Self Tests"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=tmp, check=True)
    install_navigation_workflow.install_navigation_workflow(tmp, write=True)

    write(tmp / "temp" / "scratch.md", "# Ignored scratch\n")

    report = update_navigation.update_navigation(tmp, write=False, check=True)
    outputs, scan = update_navigation.navigation_core.build_outputs(tmp)
    navigation = outputs["automations/navigation/artifacts/maps/NAVIGATION.md"]
    staleness = json.loads(outputs["automations/navigation/artifacts/maps/staleness.json"])

    assert report["ok"] is True, report
    assert "ignored directory `temp`" in scan["skipped"]
    assert "ignored directory `temp`" not in navigation
    assert "ignored directory `temp`" not in staleness["skipped"]


def test_update_check_ignores_superpowers_scratch(tmp: Path) -> None:
    fixture_project(tmp)
    install_navigation_workflow.install_navigation_workflow(tmp, write=True)
    write(tmp / ".superpowers" / "sdd" / "task-report.md", "# Temporary TDD Report\n")

    report = update_navigation.update_navigation(tmp, write=False, check=True)

    assert report["ok"] is True, report
    changes = report["stale_source_changes"]
    assert ".superpowers/sdd/task-report.md" not in changes["added"]


def test_update_check_ignores_project_validation_evidence(tmp: Path) -> None:
    fixture_project(tmp)
    install_navigation_workflow.install_navigation_workflow(tmp, write=True)

    write(
        tmp / "docs" / "project" / "validation" / "evidence" / "run-1" / "validation-report.json",
        '{"status":"listed"}\n',
    )
    write(
        tmp / "docs" / "project" / "validation" / "evidence" / "run-1" / "validation-report.md",
        "# Project Validation Evidence\n",
    )

    report = update_navigation.update_navigation(tmp, write=False, check=True)

    assert report["ok"] is True
    changes = report["stale_source_changes"]
    assert "docs/project/validation/evidence/run-1/validation-report.json" not in changes["added"]
    assert "docs/project/validation/evidence/run-1/validation-report.md" not in changes["added"]


def test_handoff_filters_noisy_non_command_fragments(tmp: Path) -> None:
    write(tmp / "pyproject.toml", "[tool.pytest.ini_options]\npythonpath = ['.']\n")
    write(tmp / "docs" / "guide.md", "# Guide\n\nRun `dotnet build or dotnet test.` before handoff.\n")
    outputs, scan = update_navigation.navigation_core.build_outputs(tmp)
    commands = [item["command"] for item in scan["commands"]]
    handoff = json.loads(outputs["automations/navigation/artifacts/maps/handoff.json"])
    validation_commands = handoff["validation_commands"]

    assert "pytest.ini_options]" not in commands
    assert "pytest.ini_options]" not in validation_commands
    assert "dotnet build or dotnet test.\"," not in validation_commands
    assert "dotnet build" in validation_commands


def test_navigation_skips_harness_fixture_assets(tmp: Path) -> None:
    fixture_project(tmp)
    write(
        tmp / ".agents" / "skills" / "demo" / "assets" / "fixtures" / "Fixture.cs",
        "public class HarnessFixture {}\n",
    )

    outputs, scan = update_navigation.navigation_core.build_outputs(tmp)
    entry_paths = {item["path"] for item in scan["entries"]}
    handoff = json.loads(outputs["automations/navigation/artifacts/maps/handoff.json"])

    assert ".agents/skills/demo/assets/fixtures/Fixture.cs" not in entry_paths
    assert ".agents/skills/demo/assets/fixtures/Fixture.cs" not in handoff["read_first_files"]


def test_project_context_writes_draft_and_check_requires_review(tmp: Path) -> None:
    fixture_project(tmp)

    missing = project_context.project_context_report(tmp, check=True)
    assert missing["ok"] is False
    assert "missing project context" in "\n".join(missing["issues"])

    written = project_context.project_context_report(tmp, write=True)
    assert written["ok"] is True
    context_path = tmp / "docs" / "project" / "project-context.md"
    assert context_path.exists()
    text = context_path.read_text(encoding="utf-8")
    expected_check_command = "python -B automations/navigation/scripts/project_context.py --target . --check"
    expected_write_command = "python -B automations/navigation/scripts/project_context.py --target . --write"
    assert written["next_command"] == "review and complete docs/project/project-context.md"
    assert expected_check_command in text
    assert ".agents/skills/repo-navigation/scripts/repo_navigation.py project-context" not in text
    assert "## Technology Stack" in text
    assert "## Local Run Commands" in text
    assert "## Project And Folder Structure" in text
    assert "## Planning Inputs" in text
    assert "::: mermaid" in text
    assert "Context status: draft" in text
    assert "handled in the workflow plan" in text

    review_needed = project_context.project_context_report(tmp, check=True)
    assert review_needed["ok"] is False
    assert review_needed["next_command"] == expected_write_command
    assert any("placeholder content remains" in issue for issue in review_needed["issues"])
    assert any("context status must be reviewed" in issue for issue in review_needed["issues"])

    draft = project_context.project_context_report(tmp, write=True)
    assert draft["ok"] is True
    assert "automations/navigation/artifacts/maps/PROJECT_CONTEXT_DRAFT.md" in draft["written"]

    reviewed_text = """# Project Context

- Context status: reviewed

## Project Purpose

Reviewed.

## Technology Stack

Reviewed.

## Local Run Commands

Reviewed.

## Validation Commands

Reviewed.

## Project And Folder Structure

::: mermaid
    graph TD;
      root["Repository root"] --> src["src"];
:::

## Architecture And Flow

::: mermaid
    graph TD;
      request["Work request"] --> plan["Plan"];
:::

## Data And Persistence

Reviewed.

## Planning Inputs

Reviewed.

## External Systems And Credentials

Reviewed.

## Generated Files And Do Not Edit

Reviewed.

## Agent Workflow Notes

Reviewed.

## Freshness

- Last reviewed: 2026-05-26
"""
    write(context_path, reviewed_text)
    reviewed = project_context.project_context_report(tmp, check=True)
    assert reviewed["ok"] is True
    assert reviewed["next_command"] == "python -B automations/navigation/scripts/update_navigation.py --target . --check"


def test_project_context_accepts_generated_materialized_context(tmp: Path) -> None:
    fixture_project(tmp)
    context_path = tmp / "docs" / "project" / "project-context.md"
    generated_text = """# Project Context

- Context status: generated; ready for workflow use with recorded assumptions.

## Project Information

Generated.

## Technologies

Generated.

## Structure And Responsibilities

[![Project structure](diagrams/project-context-structure.svg)](diagrams/project-context-structure.svg)

Source: [Mermaid](diagrams/project-context-structure.mmd)

## Architecture And Workflow Use

[![Project workflow architecture](diagrams/project-context-architecture.svg)](diagrams/project-context-architecture.svg)

Source: [Mermaid](diagrams/project-context-architecture.mmd)

## Security And Configuration Notes

Generated.

## Validation And Proof

Generated.

## Freshness

- Last reviewed: 2026-06-10
"""
    write(context_path, generated_text)

    generated = project_context.project_context_report(tmp, check=True)

    assert generated["ok"] is True
    assert generated["next_command"] == "python -B automations/navigation/scripts/update_navigation.py --target . --check"


def test_route_quality_warns_for_missing_entrypoints_and_stable_order(tmp: Path) -> None:
    write(tmp / "docs" / "only.md", "# Docs\n")
    report = update_navigation.update_navigation(tmp, write=False, check=False)
    assert report["route_quality_warnings"]
    outputs, scan = update_navigation.navigation_core.build_outputs(tmp)
    entries = [item["path"] for item in scan["entries"] if item["type"] == "file"]
    assert entries == sorted(entries, key=str.lower)
    assert report["map_size_budget"]["scan_entries"] >= 1


def test_route_quality_accepts_harness_entrypoints(tmp: Path) -> None:
    write(tmp / "README.md", "# Harness\n")
    write(tmp / "AGENTS.md", "# Repo Instructions\n")
    write(tmp / ".agents" / "manage.py", "print('manage')\n")
    write(tmp / ".agents" / "skills" / "demo" / "SKILL.md", "# Demo Skill\n")
    write(tmp / ".agents" / "skills" / "demo" / "module.json", "{}\n")
    write(tmp / "automations" / "demo" / "WORKFLOW.md", "# Demo Workflow\n")
    write(tmp / "automations" / "demo" / "module.json", "{}\n")

    report = update_navigation.update_navigation(tmp, write=False, check=False)

    assert "no common code entrypoint detected" not in report["route_quality_warnings"]


def test_source_focus_returns_compact_query_hits(tmp: Path) -> None:
    fixture_project(tmp)

    report = source_focus.build_focus_report(tmp, query="demo function", limit=4)

    assert report["ok"] is True
    assert report["query"] == "demo function"
    assert report["focus_token_estimate"] < report["full_map_token_estimate"]
    assert report["saved_vs_full_map_tokens"] > 0
    paths = [item["path"] for item in report["recommended_files"]]
    assert "src/client/demo.ts" in paths
    assert any(item["path"] == "src/client/demo.ts" and item["matching_symbols"] for item in report["recommended_files"])
    assert all("node_modules" not in item["path"] for item in report["recommended_files"])
    assert "reopen focused source files before editing" in " ".join(report["rules"])


def test_source_focus_includes_bounded_line_cited_evidence(tmp: Path) -> None:
    fixture_project(tmp)

    report = source_focus.build_focus_report(tmp, query="demo function", limit=4)

    demo = next(item for item in report["recommended_files"] if item["path"] == "src/client/demo.ts")
    evidence = demo.get("evidence", [])
    assert evidence
    assert evidence[0]["location"] == "src/client/demo.ts:1"
    assert evidence[0]["line"] == 1
    assert evidence[0]["snippet"] == "export function demo() { return 1; }"
    assert "query" in evidence[0]["reason"] or "symbol" in evidence[0]["reason"]
    assert len(evidence) <= 3


def test_source_focus_markdown_uses_safe_code_spans_for_backtick_snippets(tmp: Path) -> None:
    report = {
        "status": "focused",
        "query": "demo `tick`",
        "focus_token_estimate": 10,
        "full_map_token_estimate": 100,
        "saved_vs_full_map_tokens": 90,
        "recommended_files": [
            {
                "path": "src/demo.py",
                "score": 1,
                "reasons": ["path matches query"],
                "evidence": [
                    {
                        "location": "src/demo.py:1",
                        "reason": "query terms demo",
                        "snippet": "return \"`demo`\"",
                    }
                ],
            }
        ],
        "rules": ["Open the file before editing."],
    }

    rendered = source_focus.render_markdown(report)

    assert "- Query: `` demo `tick` ``" in rendered
    assert "query terms demo: ``return \"`demo`\"``" in rendered


def test_source_focus_prefers_broad_query_coverage_over_repeated_generic_symbols(tmp: Path) -> None:
    write(
        tmp / ".agents" / "skills" / "agent-benchmarking" / "scripts" / "run_self_tests.py",
        "\n".join(f"def test_benchmark_{index}():\n    pass\n" for index in range(20)),
    )
    write(
        tmp / ".agents" / "skills" / "local-ai-helper" / "scripts" / "local_ai_support" / "runtime_metrics.py",
        "def llama_runtime_benchmark():\n    return 'speed'\n",
    )

    report = source_focus.build_focus_report(tmp, query="local ai runtime benchmark", limit=3)

    paths = [item["path"] for item in report["recommended_files"]]
    assert paths[0] == ".agents/skills/local-ai-helper/scripts/local_ai_support/runtime_metrics.py"


def test_source_focus_prefers_context_evidence_owner_path_over_generic_tests(tmp: Path) -> None:
    write(
        tmp / ".agents" / "skills" / "agent-benchmarking" / "scripts" / "run_self_tests.py",
        "import contextlib\n"
        + "\n".join(
            f"def test_workflow_context_evidence_changed_file_relevance_{index}():\n    pass\n"
            for index in range(25)
        ),
    )
    write(
        tmp / ".agents" / "skills" / "workflow-manager" / "scripts" / "workflow_context_evidence.py",
        "import ast\nfrom workflow_support import run_common\n\n"
        "def context_evidence_summary():\n    return 'query-safe'\n\n"
        "def build_changed_file_relevance():\n    return 'owner'\n",
    )
    write(
        tmp / ".agents" / "skills" / "workflow-manager" / "scripts" / "workflow_support" / "run_lifecycle.py",
        "import workflow_repo_manager\nfrom workflow_support import run_common\n\n"
        "def context_evidence_lifecycle():\n    return 'helper'\n",
    )
    write(
        tmp / ".agents" / "skills" / "workflow-manager" / "scripts" / "workflow_support" / "cli_parser.py",
        "def add_context_evidence_parser():\n    return 'parser'\n",
    )

    report = source_focus.build_focus_report(
        tmp,
        query="workflow context evidence changed file relevance",
        limit=3,
    )

    paths = [item["path"] for item in report["recommended_files"]]
    assert paths[0] == ".agents/skills/workflow-manager/scripts/workflow_context_evidence.py"
    assert ".agents/skills/agent-benchmarking/scripts/run_self_tests.py" not in paths[:2]
    relationship_targets = [
        relationship["target"]
        for item in report["recommended_files"]
        for relationship in item.get("relationships", [])
    ]
    assert "module:contextlib" not in relationship_targets
    assert "module:ast" not in relationship_targets
    assert "module:workflow_support" not in relationship_targets


def test_source_focus_keeps_exact_filename_hits_in_mixed_queries(tmp: Path) -> None:
    write(
        tmp / ".agents" / "skills" / "workflow-manager" / "scripts" / "workflow_run_support.py",
        "def start_workflow_run():\n    return 'run'\n",
    )
    write(
        tmp / ".agents" / "skills" / "skill-manager" / "scripts" / "repo_support" / "repo_harness_install.py",
        "def install_harness_report():\n    return 'report'\n",
    )
    for index in range(6):
        write(
            tmp / "tools" / f"public_command_file_split_compact_{index}.py",
            "def public_command_file_split_compact():\n    return 'noise'\n",
        )

    report = source_focus.build_focus_report(
        tmp,
        query="repo_harness_install workflow_run_support split compact public command file",
        limit=4,
    )

    paths = [item["path"] for item in report["recommended_files"]]
    assert ".agents/skills/workflow-manager/scripts/workflow_run_support.py" in paths
    assert ".agents/skills/skill-manager/scripts/repo_support/repo_harness_install.py" in paths


def test_dependency_queries_resolve_python_js_ts_and_dotnet_edges(tmp: Path) -> None:
    write(tmp / "pkg" / "__init__.py", "\n")
    write(tmp / "pkg" / "core.py", "def core():\n    return 1\n")
    write(tmp / "pkg" / "service.py", "from .core import core\n\ndef service():\n    return core()\n")
    write(tmp / "pkg" / "test_service.py", "from . import service\n\ndef test_service():\n    assert service.service() == 1\n")
    write(tmp / "web" / "util.ts", "export const value = 1;\n")
    write(tmp / "web" / "app.ts", "import { value } from './util';\nexport const app = value;\n")
    write(tmp / "legacy" / "util.js", "module.exports = { value: 1 };\n")
    write(tmp / "legacy" / "app.js", "const util = require('./util');\nmodule.exports = util.value;\n")
    write(tmp / "src" / "Core" / "Thing.cs", "namespace Demo.Core;\npublic class Thing {}\n")
    write(tmp / "src" / "App" / "Program.cs", "using Demo.Core;\nnamespace Demo.App;\npublic class Program { Thing Value = new(); }\n")

    deps = source_focus.build_dependency_report(tmp, mode="deps", requested_path="pkg/service.py")
    reverse = source_focus.build_dependency_report(tmp, mode="rdeps", requested_path="pkg/core.py")
    impact = source_focus.build_dependency_report(tmp, mode="impact", requested_path="pkg/core.py", depth=3)
    js_ts = source_focus.build_dependency_report(tmp, mode="deps", requested_path="web/app.ts")
    js = source_focus.build_dependency_report(tmp, mode="deps", requested_path="legacy/app.js")
    dotnet = source_focus.build_dependency_report(tmp, mode="deps", requested_path="src/App/Program.cs")

    assert [item["path"] for item in deps["results"]] == ["pkg/core.py"]
    assert deps["results"][0]["location"] == "pkg/service.py:1"
    assert deps["results"][0]["confidence"] == "high"
    assert deps["results"][0]["provenance"] == "python-ast"
    assert [item["path"] for item in reverse["results"]] == ["pkg/service.py"]
    assert [item["path"] for item in impact["results"]] == ["pkg/service.py", "pkg/test_service.py"]
    assert [item["depth"] for item in impact["results"]] == [1, 2]
    assert [item["path"] for item in js_ts["results"]] == ["web/util.ts"]
    assert js_ts["results"][0]["confidence"] == "inferred"
    assert [item["path"] for item in js["results"]] == ["legacy/util.js"]
    assert js["results"][0]["confidence"] == "inferred"
    assert [item["path"] for item in dotnet["results"]] == ["src/Core/Thing.cs"]
    assert dotnet["results"][0]["confidence"] == "inferred"
    assert "source truth" in " ".join(impact["rules"])


def test_dependency_query_refuses_ambiguous_path_and_dispatcher_is_stdout_only(tmp: Path) -> None:
    write(tmp / "a" / "util.py", "VALUE = 'a'\n")
    write(tmp / "b" / "util.py", "VALUE = 'b'\n")
    report = source_focus.build_dependency_report(tmp, mode="deps", requested_path="util.py")

    assert report["ok"] is False
    assert report["status"] == "ambiguous-path"
    assert report["candidates"] == ["a/util.py", "b/util.py"]

    dispatcher = SCRIPT_DIR.parent / "repo_navigation.py"
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(dispatcher),
            "deps",
            "--target",
            str(tmp),
            "--path",
            "a/util.py",
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["mode"] == "deps"
    assert payload["resolved_path"] == "a/util.py"
    assert payload["results"] == []
    assert payload["status"] == "no-resolved-relationships"

    conflict = subprocess.run(
        [
            sys.executable,
            "-B",
            str(dispatcher),
            "deps",
            "--mode",
            "rdeps",
            "--target",
            str(tmp),
            "--path",
            "a/util.py",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert conflict.returncode == 2
    assert "cannot be combined" in conflict.stderr


def test_dependency_resolution_rejects_false_python_and_casefold_candidates(tmp: Path) -> None:
    import_rows = update_navigation.navigation_core.python_import_targets(
        "import pkg.core\nfrom pkg import core\nfrom .core import value\nfrom . import sibling\n"
    )
    import_targets = {item["target"] for item in import_rows}
    assert {"pkg.core", "pkg", ".core", ".core.value", ".", ".sibling"}.issubset(import_targets)

    write(tmp / "consumer.py", "import foo\n")
    write(tmp / "foo" / "index.py", "VALUE = 1\n")

    report = source_focus.build_dependency_report(tmp, mode="deps", requested_path="consumer.py")

    assert report["results"] == []
    unresolved_targets = {item["target"] for item in report["unresolved_subject_relationships"]}
    assert "module:foo" in unresolved_targets
    assert source_focus.unique_existing({"Foo.py", "foo.py"}, ["Foo.py", "foo.py"]) == []
    assert source_focus.unique_existing({"util.js", "util.ts"}, ["util.js", "util.ts"]) == []
    resolved, resolution, confidence = source_focus.module_candidates(
        "pkg/service.py",
        ".core",
        {"pkg/service.py", "Pkg/Core.py"},
    )
    assert resolved == ["Pkg/Core.py"]
    assert resolution == "casefold-python-module-path"
    assert confidence == "inferred"


def test_dependency_js_ts_ignores_import_text_in_comments_and_strings(tmp: Path) -> None:
    write(tmp / "web" / "ghost.ts", "export const value = 1;\n")
    write(
        tmp / "web" / "noise.ts",
        "// import { value } from './ghost';\n"
        "const sample = \"require('./ghost')\";\n",
    )

    report = source_focus.build_dependency_report(
        tmp,
        mode="deps",
        requested_path="web/noise.ts",
    )

    assert report["complete"] is True
    assert report["status"] == "no-resolved-relationships"
    assert report["results"] == []


def test_dependency_query_reports_missing_target_and_partial_scan(tmp: Path) -> None:
    missing = source_focus.build_dependency_report(
        tmp / "missing",
        mode="deps",
        requested_path="a.py",
    )
    assert missing["ok"] is False
    assert missing["complete"] is False
    assert missing["status"] == "target-not-found"

    write(tmp / "a.py", "VALUE = 1\n")
    write(tmp / "b.py", "import a\n")
    partial = source_focus.build_dependency_report(
        tmp,
        mode="rdeps",
        requested_path="a.py",
        max_files=1,
    )
    assert partial["ok"] is False
    assert partial["complete"] is False
    assert partial["status"] == "partial-scan"
    assert any(item.startswith("file scan capped at ") for item in partial["skipped"])


def test_dependency_query_enforces_file_ceiling_and_bounded_reads(tmp: Path) -> None:
    with mock.patch.object(source_focus.navigation_core, "build_scan") as build_scan:
        build_scan.return_value = {
            "ok": False,
            "skipped": [],
            "relationship_extraction": {},
        }
        report = source_focus.build_dependency_report(
            tmp,
            mode="deps",
            requested_path="a.py",
            max_files=1_000_000,
        )

    build_scan.assert_called_once_with(tmp.resolve(), max_files=source_focus.DEPENDENCY_MAX_FILES)
    assert report["file_scan_limit"] == {
        "requested": 1_000_000,
        "effective": source_focus.DEPENDENCY_MAX_FILES,
        "ceiling": source_focus.DEPENDENCY_MAX_FILES,
        "clamped": True,
    }

    stream = mock.MagicMock()
    stream.read.side_effect = lambda limit: b"abcdef"[:limit]
    opened = mock.MagicMock()
    opened.__enter__.return_value = stream
    fake_path = mock.MagicMock()
    fake_path.open.return_value = opened

    assert update_navigation.navigation_core.read_text(fake_path, limit=4) == "abcd"
    fake_path.open.assert_called_once_with("rb")
    stream.read.assert_called_once_with(4)

    write(tmp / "small.py", "VALUE = 1\n")
    scan = update_navigation.navigation_core.build_scan(tmp, max_files=1_000_000)
    assert any(
        item == (
            "file scan limit clamped from 1000000 to "
            f"{update_navigation.navigation_core.MAX_SCAN_FILES} files"
        )
        for item in scan["skipped"]
    )


def test_dependency_query_reports_relationship_and_content_caps(tmp: Path) -> None:
    relationship_root = tmp / "relationship-cap"
    write(relationship_root / "a.py", "VALUE = 1\n")
    write(relationship_root / "b.py", "import a\n")
    original_policy_int = update_navigation.navigation_core.project_policy_int

    def capped_policy_int(path: str, *, start: Path | None = None) -> int:
        if path == "limits.navigation.relationship_max_entries":
            return 1
        return original_policy_int(path, start=start)

    with mock.patch.object(update_navigation.navigation_core, "project_policy_int", capped_policy_int):
        relationship_report = source_focus.build_dependency_report(
            relationship_root,
            mode="rdeps",
            requested_path="a.py",
        )
    assert relationship_report["complete"] is False
    assert relationship_report["status"] == "partial-scan"
    assert "relationship-count-cap" in relationship_report["partial_reasons"]
    assert relationship_report["coverage"]["scan_limits"]["relationship_cap_reached"] is True

    late_import_root = tmp / "late-import"
    write(late_import_root / "a.py", "VALUE = 1\n")
    write(late_import_root / "late_consumer.py", ("# padding\n" * 14_000) + "import a\n")
    late_import_report = source_focus.build_dependency_report(
        late_import_root,
        mode="rdeps",
        requested_path="a.py",
    )
    assert late_import_report["ok"] is True
    assert late_import_report["complete"] is True
    assert late_import_report["status"] == "resolved"
    assert [item["path"] for item in late_import_report["results"]] == ["late_consumer.py"]
    assert late_import_report["results"][0]["location"].startswith("late_consumer.py:")

    content_root = tmp / "content-cap"
    write(content_root / "a.py", "VALUE = 1\n")
    oversized_padding = "# padding\n" * (
        update_navigation.navigation_core.RELATIONSHIP_CONTENT_LIMIT_BYTES
        // len("# padding\n")
        + 2
    )
    write(content_root / "large_consumer.py", oversized_padding + "import a\n")
    content_report = source_focus.build_dependency_report(
        content_root,
        mode="rdeps",
        requested_path="a.py",
    )
    assert content_report["complete"] is False
    assert content_report["status"] == "partial-scan"
    assert "source-content-cap" in content_report["partial_reasons"]
    assert "large_consumer.py" in content_report["coverage"]["scan_limits"]["content_capped_files"]

    globally_oversized_root = tmp / "global-content-cap"
    write(globally_oversized_root / "a.py", "VALUE = 1\n")
    write_bytes(
        globally_oversized_root / "omitted_consumer.py",
        b"#" * (update_navigation.navigation_core.MAX_FILE_BYTES + 1),
    )
    globally_oversized_report = source_focus.build_dependency_report(
        globally_oversized_root,
        mode="rdeps",
        requested_path="a.py",
    )
    assert globally_oversized_report["ok"] is False
    assert globally_oversized_report["complete"] is False
    assert globally_oversized_report["status"] == "partial-scan"
    assert "source-content-cap" in globally_oversized_report["partial_reasons"]
    assert globally_oversized_report["coverage"]["scan_limits"]["skipped_oversized_source_files"] == [
        "omitted_consumer.py"
    ]


def test_durable_skips_keep_material_coverage_exclusions(_tmp: Path) -> None:
    durable = update_navigation.navigation_core.durable_skipped(
        {
            "skipped": [
                "ignored directory `temp`",
                "could not stat `volatile.txt`",
                "skipped large file `large.bin`",
                "skipped binary file `opaque.bin`",
                "file scan capped at 50 files",
                "file scan limit clamped from 1000000 to 5000 files",
            ]
        }
    )
    assert durable == [
        "file scan capped at 50 files",
        "file scan limit clamped from 1000000 to 5000 files",
        "skipped binary file `opaque.bin`",
        "skipped large file `large.bin`",
    ]


def test_copied_updater_is_self_contained(tmp: Path) -> None:
    fixture_project(tmp)
    install_navigation_workflow.install_navigation_workflow(tmp, write=True)
    updater = tmp / "automations" / "navigation" / "scripts" / "update_navigation.py"
    completed = subprocess.run(
        [sys.executable, "-B", str(updater), "--target", str(tmp), "--check"],
        cwd=tmp,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout
    context = tmp / "automations" / "navigation" / "scripts" / "project_context.py"
    context_completed = subprocess.run(
        [sys.executable, "-B", str(context), "--target", str(tmp), "--write", "--format", "json"],
        cwd=tmp,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert context_completed.returncode == 0, context_completed.stdout
    assert (tmp / "docs" / "project" / "project-context.md").exists()


def run_tests() -> None:
    tests = [
        test_navigation_policy_reader_is_canonical_and_v2_only,
        test_install_generates_navigation_workflow,
        test_install_write_skips_unchanged_workflow_files_and_maps,
        test_owner_capsule_uses_safe_help_for_placeholder_commands,
        test_workflow_owner_capsule_prefers_strict_read_only_commands,
        test_workflow_owner_capsule_keeps_late_strict_read_only_commands,
        test_strict_read_only_commands_reject_legacy_untyped_commands,
        test_update_check_detects_stale_maps,
        test_staleness_metadata_records_source_git_tree_hash,
        test_source_git_tree_hash_is_stable_across_dirty_generation_and_commit,
        test_source_git_tree_hash_is_stable_across_staging_a_deletion,
        test_navigation_outputs_are_portable_across_clone_and_linked_worktree,
        test_navigation_source_hash_preserves_binary_newline_bytes,
        test_navigation_source_hash_honors_explicit_git_binary_attribute,
        test_source_git_tree_hash_omits_cache_when_custom_filter_is_active,
        test_git_path_attributes_handles_unicode_filenames,
        test_navigation_scan_excludes_gitignored_text_sources,
        test_navigation_scan_filters_gitignored_files_before_file_budget,
        test_update_check_reports_not_installed_when_all_outputs_missing,
        test_update_write_skips_unchanged_outputs,
        test_update_check_detects_obsolete_owner_capsules,
        test_update_check_ignores_runtime_evidence_and_local_ai_cache,
        test_update_check_ignores_local_ignored_directory_presence_in_git_repo,
        test_update_check_ignores_superpowers_scratch,
        test_update_check_ignores_project_validation_evidence,
        test_handoff_filters_noisy_non_command_fragments,
        test_navigation_skips_harness_fixture_assets,
        test_project_context_writes_draft_and_check_requires_review,
        test_project_context_accepts_generated_materialized_context,
        test_route_quality_warns_for_missing_entrypoints_and_stable_order,
        test_route_quality_accepts_harness_entrypoints,
        test_source_focus_returns_compact_query_hits,
        test_source_focus_includes_bounded_line_cited_evidence,
        test_source_focus_markdown_uses_safe_code_spans_for_backtick_snippets,
        test_source_focus_prefers_broad_query_coverage_over_repeated_generic_symbols,
        test_source_focus_prefers_context_evidence_owner_path_over_generic_tests,
        test_source_focus_keeps_exact_filename_hits_in_mixed_queries,
        test_dependency_queries_resolve_python_js_ts_and_dotnet_edges,
        test_dependency_query_refuses_ambiguous_path_and_dispatcher_is_stdout_only,
        test_dependency_resolution_rejects_false_python_and_casefold_candidates,
        test_dependency_js_ts_ignores_import_text_in_comments_and_strings,
        test_dependency_query_reports_missing_target_and_partial_scan,
        test_dependency_query_enforces_file_ceiling_and_bounded_reads,
        test_dependency_query_reports_relationship_and_content_caps,
        test_durable_skips_keep_material_coverage_exclusions,
        test_copied_updater_is_self_contained,
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        for test in tests:
            test_root = base / test.__name__
            test_root.mkdir()
            test(test_root)
            print(f"PASS {test.__name__}")


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="write/temp: run repo-navigation navigation self-tests using temporary fixture projects")


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    update_navigation.require_supported_python()
    run_tests()
    print("repo-navigation self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
