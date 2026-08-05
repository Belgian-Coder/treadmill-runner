#!/usr/bin/env python3
"""Workflow-specific domain fixtures for offline smoke checks."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import workflow_manager_common as common
from workflow_support import plan_smoke
from workflow_support.smoke_common import (
    cleanup_smoke_run,
    domain_fixture_row,
    fixture_project,
    named_command_check,
    skipped_check,
    smoke_run_id,
    workflow_eval_suite_check,
    workflow_manifest,
    write_json,
    write_text,
    xml_elements,
    xml_text_values,
)


EXTERNAL_SKIP_REASONS = {
    "Azure DevOps": "live Azure DevOps access is outside offline smoke; fixture intake covers the local path",
    "SonarQube": "live SonarQube access is outside offline smoke; credentialed export scripts are not required locally",
}


def workflow_external_skips(root: Path, workflow_name: str) -> list[dict[str, Any]]:
    manifest = workflow_manifest(root, workflow_name)
    source_systems = manifest.get("external_access", {}).get("source_systems", [])
    if not isinstance(source_systems, list):
        return []
    rows: list[dict[str, Any]] = []
    for service, reason in EXTERNAL_SKIP_REASONS.items():
        if service in source_systems:
            rows.append(skipped_check(f"{service.lower().replace(' ', '-')}-live-access", reason, service=service))
    return rows


def fill_smoke_domain_outputs(root: Path, workflow_name: str, run_id: str) -> None:
    run_dir = root / "automations" / workflow_name / "runs" / run_id
    if workflow_name in {"user-story-workflow", "bug-ticket-workflow", "disciplined-change-workflow"}:
        path = run_dir / "execution-log.md"
        if not path.exists():
            return
        text = common.read_text(path)
        updated = text.replace(
            "- Reusable lesson or `No reusable lesson: <reason>`.",
            "No reusable lesson: lifecycle smoke only verifies workflow command plumbing.",
        )
        updated = updated.replace(
            "| Package | Planned | Actual | Reason | Approval Impact | Validation Impact |\n|---|---|---|---|---|---|",
            "| Package | Planned | Actual | Reason | Approval Impact | Validation Impact |\n|---|---|---|---|---|---|\n| No variance | lifecycle smoke | lifecycle smoke | execution matched fixture plan | none accepted by fixture | validation unchanged |",
        )
        review_rows = (
            "| Axis | Reviewer Or Method | Result | Evidence | Disposition |\n|---|---|---|---|---|\n"
            "| Spec and plan compliance | lifecycle smoke | passed | run.json | accepted |\n"
            "| Standards and maintainability | lifecycle smoke | passed | REPORT.md | accepted |\n"
            "| Security and authority | lifecycle smoke | skipped: fixture performs no security-sensitive changes | REPORT.md | accepted by fixture |\n"
            "| Validation and generated artifacts | lifecycle smoke | passed | validation evidence | accepted |"
        )
        updated = updated.replace(
            "| Axis | Reviewer Or Method | Result | Evidence | Disposition |\n|---|---|---|---|---|\n"
            "| Spec and plan compliance | | | | |\n"
            "| Standards and maintainability | | | | |\n"
            "| Security and authority | | | | |\n"
            "| Validation and generated artifacts | | | | |",
            review_rows,
        )
        if updated != text:
            write_text(path, updated)
    elif workflow_name == "feedback-improvement-workflow":
        write_json(
            run_dir / "artifacts" / "feedback" / "feedback-candidates.json",
            {
                "schema_version": 1,
                "tool": "workflow-smoke.fixture",
                "ok": True,
                "candidates": [
                    {
                        "target_kind": "skill",
                        "target": "skill-manager",
                        "failure_type": "failed-check",
                        "count": 2,
                        "first_failing_fact": "fixture failed check",
                        "context_paths": [".agents/local-ai/cache/last-validation.txt"],
                    }
                ],
            },
        )
        write_text(
            run_dir / "action-plan.md",
            """# Feedback Improvement Action Plan

## Candidate Action Items

| Target | Failure Type | Count | First Failing Fact | Owner | Follow-up Vehicle | Evidence References | Risk | Baseline Command | Expected Failing Fact Before Change | Expected Behavior After Change | Acceptance Commands | Evidence To Capture | Regression Guard | Regression Owner | Regression Rationale |
|---|---:|---:|---|---|---|---|---|---|---|---|---|---|---|---|---|
| skill-manager | failed-check | 2 | fixture failed check | skill-manager | disciplined-change-workflow | .agents/local-ai/cache/last-validation.txt | low | python -B .agents/manage.py check | fixture failed check | check passes | python -B .agents/manage.py check | validation/fixture-check.json | skill-manager self-test fixture | skill-manager | catches repeated failed-check feedback |

## Not Actionable Now

- None.
""",
        )
        write_json(
            run_dir / "validation" / "clear-feedback.json",
            {
                "schema_version": 1,
                "tool": "skill-manager.feedback-clear",
                "ok": True,
                "status": "cleared",
                "dry_run": False,
                "entry_count_before": 1,
                "bytes_before": 100,
                "cleared_path": ".agents/local-ai/cache/feedback/failure-feedback.jsonl",
                "action_plan_path": f"automations/{workflow_name}/runs/{run_id}/action-plan.md",
                "reason": "workflow smoke fixture",
            },
        )


def agent_benchmarking_checks(root: Path, temp_root: Path) -> list[dict[str, Any]]:
    suite = temp_root / "benchmark-suite.json"
    context = temp_root / "context.md"
    output_root = temp_root / "benchmark-runs"
    result = temp_root / "raw-result.json"
    run_id = "smoke-local-agent-benchmarking"
    write_text(context, "# Context\n\nFixture context for workflow smoke.")
    write_json(
        suite,
        {
            "schema_version": 1,
            "name": "workflow-smoke",
            "tasks": [
                {
                    "id": "fixture-task",
                    "title": "Fixture task",
                    "prompt": "Record that the fixture command path works.",
                    "static_context": ["context.md"],
                    "task_context": [],
                    "expected_checks": ["benchmark run can be prepared and recorded"],
                }
            ],
        },
    )
    prepare = named_command_check(
        "prepare-benchmark-run",
        root,
        [
            sys.executable,
            "-B",
            str(root / ".agents" / "skills" / "agent-benchmarking" / "scripts" / "prepare_benchmark_run.py"),
            "--suite",
            str(suite),
            "--task-id",
            "fixture-task",
            "--output-root",
            str(output_root),
            "--run-id",
            run_id,
            "--agent-tool",
            "workflow-smoke",
            "--model-label",
            "fixture",
            "--workflow-name",
            "agent-benchmarking",
            "--write",
            "--format",
            "json",
        ],
    )
    checks = [prepare]
    if prepare.get("ok") is True:
        write_json(
            result,
            {
                "quality": {"passed": True, "score": 1.0},
                "commands": [{"command": "fixture", "ok": True, "status": "ok"}],
                "files_changed": [],
                "checks": [{"name": "fixture", "ok": True}],
                "skipped": ["model execution is skipped by offline workflow smoke"],
                "failures": [],
                "notes": ["fixture result"],
                "unsupported_claims": [],
                "invented_paths": [],
                "invented_commands": [],
                "false_validation_claims": [],
                "abstentions": [],
                "loaded_context": ["context.md"],
                "evidence": [{"tier": "primary", "path": "benchmark-task.json", "claim": "prepared fixture"}],
                "elapsed_seconds": 0.01,
                "output_text": "fixture passed",
            },
        )
        checks.append(
            named_command_check(
                "record-benchmark-result",
                root,
                [
                    sys.executable,
                    "-B",
                    str(root / ".agents" / "skills" / "agent-benchmarking" / "scripts" / "record_benchmark_result.py"),
                    "--run-dir",
                    str(output_root / run_id),
                    "--result",
                    str(result),
                    "--write",
                    "--format",
                    "json",
                ],
            )
        )
    return checks


def intake_fixture_checks(root: Path, temp_root: Path, *, item_type: str) -> list[dict[str, Any]]:
    fixture = temp_root / f"{item_type}-fixture.json"
    output_root = temp_root / f"{item_type}-intake"
    work_type = "User Story" if item_type == "story" else "Bug"
    fields: dict[str, Any] = {
        "System.Id": "1001" if item_type == "story" else "2001",
        "System.WorkItemType": work_type,
        "System.Title": f"Offline smoke {work_type.lower()}",
        "System.Description": "<p>Fixture description.</p>",
    }
    if item_type == "story":
        fields["Microsoft.VSTS.Common.AcceptanceCriteria"] = "<ul><li>Fixture passes.</li></ul>"
    else:
        fields["Microsoft.VSTS.TCM.ReproSteps"] = "<ol><li>Run fixture smoke.</li></ol>"
    write_json(fixture, {"id": fields["System.Id"], "fields": fields, "relations": [], "comments": []})
    return [
        named_command_check(
            f"{item_type}-ticket-intake-fixture",
            root,
            [
                sys.executable,
                "-B",
                str(root / ".agents" / "skills" / "azure-devops-ticket-intake" / "scripts" / "import_azure_devops_work_item.py"),
                "--fixture-json",
                str(fixture),
                "--output-root",
                str(output_root),
                "--skip-attachments",
                "--force",
                "--format",
                "json",
            ],
        )
    ]


def quality_profile_checks(root: Path, temp_root: Path, *, workflow_name: str, script_name: str, id_flag: str) -> list[dict[str, Any]]:
    project = fixture_project(temp_root)
    run_id = smoke_run_id(workflow_name, "quality")
    command = [
        sys.executable,
        "-B",
        str(root / "automations" / workflow_name / "scripts" / script_name),
        id_flag,
        run_id,
        "--project-root",
        str(project["root"]),
        "--docs-target",
        str(project["docs"]),
        "--run-security",
        "--security-target",
        str(project["root"]),
        "--security-fail-on",
        "high",
        "--max-workers",
        "1",
        "--timeout-seconds",
        "60",
        "--evidence-name",
        "smoke-local-quality",
    ]
    if workflow_name == "bug-ticket-workflow":
        command.extend(["--regression-test-result", str(project["test_result"])])
    else:
        command.extend(["--test-result", str(project["test_result"])])
    check = named_command_check(f"{workflow_name}-quality-profile", root, command, timeout_seconds=90)
    cleanup = cleanup_smoke_run(root, workflow_name, run_id)
    check["cleanup"] = cleanup
    return [check]


def dotnet_upgrade_fixture_model_checks(temp_root: Path) -> list[dict[str, Any]]:
    fixture = temp_root / "dotnet-upgrade-fixture"
    project_file = fixture / "src" / "ModernApp" / "ModernApp.csproj"
    central_versions = fixture / "Directory.Packages.props"
    nuget_config = fixture / "NuGet.config"
    write_text(
        nuget_config,
        """<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
    <add key="LocalOffline" value="./.nuget/packages" />
  </packageSources>
  <packageSourceMapping>
    <packageSource key="LocalOffline">
      <package pattern="Microsoft.*" />
      <package pattern="Serilog" />
    </packageSource>
  </packageSourceMapping>
</configuration>
""",
    )
    write_text(fixture / "global.json", '{ "sdk": { "version": "8.0.100", "rollForward": "latestFeature" } }')
    write_text(
        central_versions,
        """<Project>
  <PropertyGroup>
    <ManagePackageVersionsCentrally>true</ManagePackageVersionsCentrally>
  </PropertyGroup>
  <ItemGroup>
    <PackageVersion Include="Microsoft.Extensions.Hosting" Version="8.0.0" />
    <PackageVersion Include="Serilog" Version="3.1.1" />
    <GlobalPackageReference Include="Microsoft.SourceLink.GitHub" Version="8.0.0" PrivateAssets="all" />
  </ItemGroup>
</Project>
""",
    )
    write_text(
        project_file,
        """<Project Sdk="Microsoft.NET.Sdk.Worker">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.Extensions.Hosting" />
    <PackageReference Include="Serilog" VersionOverride="3.1.1" />
    <PackageDownload Include="Microsoft.NETCore.App.Ref" Version="[10.0.0]" />
  </ItemGroup>
</Project>
""",
    )
    write_json(
        fixture / ".config" / "dotnet-tools.json",
        {
            "version": 1,
            "isRoot": True,
            "tools": {
                "dotnet-ef": {
                    "version": "8.0.0",
                    "commands": ["dotnet-ef"],
                }
            },
        },
    )
    sources = [item.attrib.get("value", "") for item in xml_elements(nuget_config, "add")]
    package_versions = {item.attrib.get("Include", ""): item.attrib.get("Version", "") for item in xml_elements(central_versions, "PackageVersion")}
    global_refs = {item.attrib.get("Include", ""): item.attrib.get("Version", "") for item in xml_elements(central_versions, "GlobalPackageReference")}
    package_refs = [item.attrib for item in xml_elements(project_file, "PackageReference")]
    downloads = [item.attrib for item in xml_elements(project_file, "PackageDownload")]
    central_enabled = "true" in [value.lower() for value in xml_text_values(central_versions, "ManagePackageVersionsCentrally")]
    source_ok = bool(sources) and all("nuget.org" not in source.lower() for source in sources)
    owners_ok = (
        central_enabled
        and package_versions.get("Microsoft.Extensions.Hosting") == "8.0.0"
        and global_refs.get("Microsoft.SourceLink.GitHub") == "8.0.0"
        and any(item.get("Include") == "Microsoft.Extensions.Hosting" and "Version" not in item for item in package_refs)
        and any(item.get("Include") == "Serilog" and item.get("VersionOverride") == "3.1.1" for item in package_refs)
        and any(item.get("Include") == "Microsoft.NETCore.App.Ref" for item in downloads)
    )
    return [
        domain_fixture_row(
            "dotnet-upgrade-fixture-feed-policy",
            source_ok,
            issue="fixture should use repository NuGet.config sources without nuget.org fallback",
            details={"sources": sources},
        ),
        domain_fixture_row(
            "dotnet-upgrade-fixture-package-owners",
            owners_ok,
            issue="fixture should cover central package management, project references, overrides, global references, and downloads",
            details={"package_versions": package_versions, "global_refs": global_refs, "package_refs": package_refs, "downloads": downloads},
        ),
    ]


def dotnet_plan_template_check(root: Path, workflow_name: str) -> dict[str, Any]:
    return named_command_check(
        f"{workflow_name}-plan-template-check",
        root,
        [
            sys.executable,
            "-B",
            str(root / ".agents" / "manage.py"),
            "workflow",
            "plan-check",
            "--name",
            workflow_name,
            "--template",
            "--format",
            "json",
        ],
        timeout_seconds=60,
    )


def dotnet_upgrade_checks(root: Path, temp_root: Path) -> list[dict[str, Any]]:
    fixture_rows = dotnet_upgrade_fixture_model_checks(temp_root)
    fixture_root = temp_root / "dotnet-upgrade-fixture"
    plan_template = dotnet_plan_template_check(root, "dotnet-upgrade")
    plan_ready = plan_smoke.dotnet_plan_ready_smoke(root, "dotnet-upgrade")
    inspector = named_command_check(
        "dotnet-upgrade-inspector-fixture",
        root,
        [
            sys.executable,
            "-B",
            str(root / ".agents" / "skills" / "dotnet-engineering" / "scripts" / "dotnet_repo_inspector.py"),
            "all",
            "--target",
            str(fixture_root),
            "--target-version",
            "net10.0",
            "--format",
            "json",
        ],
        timeout_seconds=90,
    )
    return [*fixture_rows, plan_template, plan_ready, inspector, workflow_eval_suite_check(root, "dotnet-upgrade")]


def dotnet_framework_migration_fixture_model_checks(temp_root: Path) -> list[dict[str, Any]]:
    fixture = temp_root / "dotnet-framework-migration-fixture"
    project_file = fixture / "LegacyApp" / "LegacyApp.csproj"
    packages_config = fixture / "LegacyApp" / "packages.config"
    app_config = fixture / "LegacyApp" / "app.config"
    write_text(
        project_file,
        """<?xml version="1.0" encoding="utf-8"?>
<Project ToolsVersion="15.0" DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFrameworkVersion>v4.8</TargetFrameworkVersion>
  </PropertyGroup>
  <ItemGroup>
    <Reference Include="System" />
    <Compile Include="Program.cs" />
    <None Include="app.config" />
    <None Include="packages.config" />
  </ItemGroup>
  <Import Project="$(MSBuildToolsPath)\\Microsoft.CSharp.targets" />
</Project>
""",
    )
    write_text(
        packages_config,
        """<?xml version="1.0" encoding="utf-8"?>
<packages>
  <package id="Newtonsoft.Json" version="12.0.3" targetFramework="net48" />
</packages>
""",
    )
    write_text(
        app_config,
        """<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <runtime>
    <assemblyBinding xmlns="urn:schemas-microsoft-com:asm.v1">
      <dependentAssembly>
        <assemblyIdentity name="Newtonsoft.Json" culture="neutral" />
        <bindingRedirect oldVersion="0.0.0.0-12.0.0.0" newVersion="12.0.0.0" />
      </dependentAssembly>
    </assemblyBinding>
  </runtime>
</configuration>
""",
    )
    target_versions = xml_text_values(project_file, "TargetFrameworkVersion")
    imports = [item.attrib.get("Project", "") for item in xml_elements(project_file, "Import")]
    packages = [item.attrib for item in xml_elements(packages_config, "package")]
    redirects = [item.attrib for item in xml_elements(app_config, "bindingRedirect")]
    legacy_ok = (
        "v4.8" in target_versions
        and any("MSBuildToolsPath" in value for value in imports)
        and any(item.get("id") == "Newtonsoft.Json" and item.get("targetFramework") == "net48" for item in packages)
        and any(item.get("newVersion") == "12.0.0.0" for item in redirects)
    )
    return [
        domain_fixture_row(
            "dotnet-framework-migration-fixture-legacy-signals",
            legacy_ok,
            issue="fixture should cover .NET Framework target, old MSBuild import, packages.config, and binding redirects",
            details={"target_versions": target_versions, "imports": imports, "packages": packages, "binding_redirects": redirects},
        )
    ]


def dotnet_framework_migration_checks(root: Path, temp_root: Path) -> list[dict[str, Any]]:
    fixture_rows = dotnet_framework_migration_fixture_model_checks(temp_root)
    fixture_root = temp_root / "dotnet-framework-migration-fixture"
    plan_template = dotnet_plan_template_check(root, "dotnet-framework-migration")
    plan_ready = plan_smoke.dotnet_plan_ready_smoke(root, "dotnet-framework-migration")
    inspector = named_command_check(
        "dotnet-framework-migration-inspector-fixture",
        root,
        [
            sys.executable,
            "-B",
            str(root / ".agents" / "skills" / "dotnet-engineering" / "scripts" / "dotnet_repo_inspector.py"),
            "all",
            "--target",
            str(fixture_root),
            "--target-version",
            "net10.0",
            "--format",
            "json",
        ],
        timeout_seconds=90,
    )
    return [*fixture_rows, plan_template, plan_ready, inspector, workflow_eval_suite_check(root, "dotnet-framework-migration")]


def candidate_import_checks(root: Path, temp_root: Path) -> list[dict[str, Any]]:
    candidate = temp_root / "candidate-skill"
    write_text(
        candidate / "SKILL.md",
        """---
name: candidate-smoke
description: Fixture candidate for local analysis.
---

# Candidate Smoke

Use only for offline workflow smoke.
""",
    )
    write_json(candidate / "module.json", {"schema_version": 3, "kind": "skill", "id": "candidate-smoke"})
    return [
        named_command_check(
            "candidate-location-analysis",
            root,
            [
                sys.executable,
                "-B",
                str(root / ".agents" / "manage.py"),
                "analyze-location",
                str(candidate),
                "--summary",
                "--compact",
                "--format",
                "json",
            ],
        )
    ]


def disciplined_change_checks(root: Path, _temp_root: Path) -> list[dict[str, Any]]:
    return [
        named_command_check(
            "disciplined-change-review-plan",
            root,
            [
                sys.executable,
                "-B",
                str(root / ".agents" / "manage.py"),
                "review",
                "disciplined-change-workflow",
                "--plan",
                "--format",
                "json",
            ],
        )
    ]


def init_local_git_fixture(repo_path: Path) -> dict[str, Any]:
    init = subprocess.run(["git", "init", "-b", "main", str(repo_path)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if init.returncode != 0:
        return {"ok": False, "issue": init.stderr.strip() or init.stdout.strip()}
    write_text(repo_path / "README.md", "# Reference Fixture\n\nLocal only.")
    for command in (
        ["git", "config", "user.name", "Workflow Smoke"],
        ["git", "config", "user.email", "workflow-smoke@example.invalid"],
        ["git", "add", "README.md"],
        ["git", "commit", "-m", "Add reference fixture"],
    ):
        completed = subprocess.run(command, cwd=str(repo_path), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if completed.returncode != 0:
            return {"ok": False, "issue": completed.stderr.strip() or completed.stdout.strip(), "command": command}
    return {"ok": True}


def reference_refresh_checks(root: Path, temp_root: Path) -> list[dict[str, Any]]:
    output_root = temp_root / "references"
    repo_path = output_root / "mirrors" / "fixture-reference"
    init = init_local_git_fixture(repo_path)
    if not init.get("ok"):
        return [{"name": "reference-local-git-fixture", "kind": "domain-fixture", "ok": False, "status": "failed", "issue": init.get("issue", "")}]
    manifest = temp_root / "reference-manifest.json"
    write_json(
        manifest,
        {
            "schema_version": 1,
            "references": [
                {
                    "name": "fixture-reference",
                    "repository_url": str(repo_path),
                    "path": "mirrors/fixture-reference",
                    "branch": "main",
                    "purpose": "Offline workflow smoke.",
                    "referenced_files": ["README.md"],
                }
            ],
        },
    )
    return [
        named_command_check(
            "reference-refresh-local-git",
            root,
            [
                sys.executable,
                "-B",
                str(root / ".agents" / "skills" / "external-reference-manager" / "scripts" / "sync_references.py"),
                "--manifest",
                str(manifest),
                "--output-root",
                str(output_root),
                "--workspace-root",
                str(temp_root),
                "--no-fetch",
                "--write",
                "--format",
                "json",
            ],
            timeout_seconds=90,
        )
    ]


def local_ai_benchmark_checks(root: Path, _temp_root: Path) -> list[dict[str, Any]]:
    return [
        named_command_check(
            "local-ai-resources",
            root,
            [sys.executable, "-B", str(root / ".agents" / "manage.py"), "local-ai", "resources", "--json"],
        ),
        named_command_check(
            "local-ai-tool-call-check",
            root,
            [
                sys.executable,
                "-B",
                str(root / "automations" / "agent-benchmarking" / "scripts" / "local_ai_tool_call_benchmark.py"),
                "--root",
                str(root),
                "--check",
                "--json",
                "--compact",
            ],
            timeout_seconds=90,
        ),
    ]


def navigation_checks(root: Path, temp_root: Path) -> list[dict[str, Any]]:
    project = fixture_project(temp_root)["root"]
    install = named_command_check(
        "navigation-install-fixture",
        root,
        [
            sys.executable,
            "-B",
            str(root / ".agents" / "skills" / "repo-navigation" / "scripts" / "navigation" / "install_navigation_workflow.py"),
            "--target",
            str(project),
            "--write",
            "--format",
            "json",
        ],
        timeout_seconds=90,
    )
    rows = [install]
    if install.get("ok") is not True:
        return rows

    update_script = project / "automations" / "navigation" / "scripts" / "update_navigation.py"
    context_script = project / "automations" / "navigation" / "scripts" / "project_context.py"
    rows.append(
        named_command_check(
            "navigation-fresh-check",
            root,
            [
                sys.executable,
                "-B",
                str(update_script),
                "--target",
                str(project),
                "--check",
                "--format",
                "json",
            ],
            timeout_seconds=90,
        )
    )
    rows.append(
        named_command_check(
            "navigation-project-context-draft",
            root,
            [
                sys.executable,
                "-B",
                str(context_script),
                "--target",
                str(project),
                "--write",
                "--format",
                "json",
            ],
            timeout_seconds=90,
        )
    )

    changed_source = project / "src" / "NewNavigationSignal.cs"
    write_text(changed_source, "namespace Fixture; public static class NewNavigationSignal { }")
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(update_script),
            "--target",
            str(project),
            "--check",
            "--format",
            "json",
        ],
        cwd=str(root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
        check=False,
    )
    try:
        stale_report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        stale_report = {}
    added = []
    changes = stale_report.get("stale_source_changes") if isinstance(stale_report, dict) else {}
    if isinstance(changes, dict) and isinstance(changes.get("added"), list):
        added = [str(item) for item in changes["added"]]
    stale_ok = (
        completed.returncode == 1
        and stale_report.get("status") == "stale"
        and "src/NewNavigationSignal.cs" in added
    )
    rows.append(
        domain_fixture_row(
            "navigation-stale-detection",
            stale_ok,
            issue="navigation check should report stale maps and the added fixture source file",
            details={
                "returncode": completed.returncode,
                "status": stale_report.get("status"),
                "added": added,
                "stderr_tail": completed.stderr[-400:],
            },
        )
    )
    return rows


DOMAIN_CHECKS = {
    "agent-benchmarking": agent_benchmarking_checks,
    "bug-ticket-workflow": lambda root, temp_root: [
        plan_smoke.story_bug_plan_only_smoke(root, "bug-ticket-workflow"),
        *intake_fixture_checks(root, temp_root, item_type="bug"),
        *quality_profile_checks(root, temp_root, workflow_name="bug-ticket-workflow", script_name="bug_regression_quality_profile.py", id_flag="--bug-id"),
    ],
    "candidate-import-workflow": candidate_import_checks,
    "disciplined-change-workflow": disciplined_change_checks,
    "dotnet-framework-migration": dotnet_framework_migration_checks,
    "dotnet-upgrade": dotnet_upgrade_checks,
    "local-ai-benchmark-workflow": local_ai_benchmark_checks,
    "navigation": navigation_checks,
    "reference-refresh": reference_refresh_checks,
    "user-story-workflow": lambda root, temp_root: [
        plan_smoke.story_bug_plan_only_smoke(root, "user-story-workflow"),
        *intake_fixture_checks(root, temp_root, item_type="story"),
        *quality_profile_checks(root, temp_root, workflow_name="user-story-workflow", script_name="story_quality_profile.py", id_flag="--story-id"),
    ],
}


def domain_smoke_checks(root: Path, workflow_name: str, temp_root: Path) -> list[dict[str, Any]]:
    check_func = DOMAIN_CHECKS.get(workflow_name)
    rows = workflow_external_skips(root, workflow_name)
    if check_func is None:
        rows.append(skipped_check("domain-fixture", "no workflow-specific fixture check registered"))
        return rows
    try:
        rows.extend(check_func(root, temp_root))
    except Exception as exc:
        rows.append({"name": "domain-fixture-exception", "kind": "domain-fixture", "ok": False, "status": "failed", "issue": str(exc)})
    return rows
