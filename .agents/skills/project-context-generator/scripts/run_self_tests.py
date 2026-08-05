#!/usr/bin/env python3
"""Self-tests for project-context-generator."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import generate_project_context
import run_project_validation


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def fixture_project(root: Path) -> Path:
    project = root / "sample-app"
    write(project / "global.json", json.dumps({"sdk": {"version": "10.0.100"}}, indent=2))
    write(
        project / "Directory.Build.props",
        """<Project>
  <PropertyGroup>
    <TreatWarningsAsErrors>true</TreatWarningsAsErrors>
    <Nullable>enable</Nullable>
    <AnalysisLevel>latest</AnalysisLevel>
  </PropertyGroup>
</Project>
""",
    )
    write(
        project / "Directory.Packages.props",
        """<Project>
  <PropertyGroup>
    <ManagePackageVersionsCentrally>true</ManagePackageVersionsCentrally>
  </PropertyGroup>
  <ItemGroup>
    <PackageVersion Include="Microsoft.AspNetCore.OpenApi" Version="10.0.0" />
    <PackageVersion Include="Microsoft.EntityFrameworkCore.SqlServer" Version="10.0.0" />
  </ItemGroup>
</Project>
""",
    )
    write(
        project / "NuGet.config",
        """<configuration>
  <packageSources>
    <add key="nuget.org" value="https://api.nuget.org/v3/index.json" />
    <add key="InternalFeed" value="https://nuget.internal.example/v3/index.json" />
  </packageSources>
  <packageSourceMapping>
    <packageSource key="InternalFeed">
      <package pattern="Company.*" />
    </packageSource>
  </packageSourceMapping>
</configuration>
""",
    )
    write(project / "Sample.sln", "\n")
    write(
        project / "src" / "Sample.App" / "Sample.App.csproj",
        """<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.AspNetCore.OpenApi" />
    <PackageReference Include="Microsoft.EntityFrameworkCore.SqlServer" />
    <PackageReference Include="Company.Platform.Client" />
  </ItemGroup>
</Project>
""",
    )
    write(
        project / "src" / "Sample.App" / "Data" / "SampleDbContext.cs",
        """using Microsoft.EntityFrameworkCore;

public sealed class SampleDbContext : DbContext
{
}
""",
    )
    write(project / "tests" / "Sample.Tests" / "Sample.Tests.csproj", '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup></Project>')
    write(
        project / "package.json",
        json.dumps(
            {
                "name": "sample-app",
                "scripts": {"build": "vite build", "test": "vitest run", "lint": "eslint ."},
                "devDependencies": {"@playwright/test": "latest", "vite": "latest"},
            },
            indent=2,
        ),
    )
    write(project / "playwright.config.ts", "export default {};\n")
    write(project / "appsettings.json", '{"ConnectionStrings":{"Default":"placeholder"},"ExternalApis":{"Billing":{"BaseUrl":"https://billing.example"}}}\n')
    write(project / ".github" / "workflows" / "build.yml", "name: build\nsteps:\n  - run: dotnet restore Sample.sln\n  - run: dotnet test Sample.sln --no-restore\n")
    write(project / "ops&docs" / "README.md", "# Ops\n")
    return project


def test_command_discovery(project: Path) -> None:
    commands = run_project_validation.discover_commands(project)
    ids = {item["id"] for item in commands}
    assert_true("dotnet-build" in ids, "expected .NET build command")
    assert_true("dotnet-test" in ids, "expected .NET test command")
    assert_true("npm-build" in ids, "expected npm build command")
    assert_true("npm-test" in ids, "expected npm test command")
    assert_true("playwright-test" in ids, "expected Playwright command")
    technologies = set(generate_project_context.detect_technologies(project, generate_project_context.iter_project_files(project)))
    assert_true("GitHub Actions" in technologies, "expected GitHub Actions detection")


def test_python_unittest_discovery_without_pytest_declaration(root: Path) -> None:
    project = root / "unittest-app"
    write(project / "pyproject.toml", '[project]\nname = "unittest-app"\nversion = "0.1.0"\n')
    write(project / "tests" / "test_sample.py", "import unittest\n\nclass SampleTests(unittest.TestCase):\n    def test_true(self):\n        self.assertTrue(True)\n")

    commands = run_project_validation.discover_commands(project)
    command_by_id = {item["id"]: item for item in commands}

    assert_true("python-unittest" in command_by_id, "expected unittest command when tests exist without pytest")
    assert_true("python-pytest" not in command_by_id, "unittest-only project should not get pytest command")
    assert_true(command_by_id["python-unittest"]["command_text"] == "python -B -m unittest discover -s tests", "expected portable unittest discovery command")


def test_python_pytest_discovery_requires_declared_signal(root: Path) -> None:
    project = root / "pytest-app"
    write(project / "requirements.txt", "pytest\n")
    write(project / "tests" / "test_sample.py", "def test_true():\n    assert True\n")

    command_ids = {item["id"] for item in run_project_validation.discover_commands(project)}

    assert_true("python-pytest" in command_ids, "expected pytest command when pytest is declared")
    assert_true("python-unittest" not in command_ids, "pytest-declared project should not also get unittest fallback")


def test_windows_node_commands_use_cmd_shims() -> None:
    npm_cmd = shutil.which("npm.cmd")
    npx_cmd = shutil.which("npx.cmd")
    if os.name != "nt" or not npm_cmd or not npx_cmd:
        return

    assert_true(run_project_validation.executable_command(["npm", "test"])[0] == npm_cmd, "expected npm.cmd on Windows")
    assert_true(run_project_validation.executable_command(["npx", "playwright", "test"])[0] == npx_cmd, "expected npx.cmd on Windows")


def test_harness_repo_ignores_fixture_technology_signals(root: Path) -> None:
    project = root / "skills-harness"
    write(project / ".agents" / "manage.py", "print('manage')\n")
    write(project / ".agents" / "skills" / "demo" / "scripts" / "tool.py", "print('tool')\n")
    write(
        project
        / ".agents"
        / "skills"
        / "demo"
        / "assets"
        / "fixtures"
        / "Sample"
        / "Sample.csproj",
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup></Project>',
    )
    write(project / ".github" / "workflows" / "build.yml", "name: build\n")

    files = generate_project_context.iter_project_files(project)
    technologies = set(generate_project_context.detect_technologies(project, files))
    command_ids = {item["id"] for item in run_project_validation.discover_commands(project)}

    assert_true("Python" in technologies, "expected Python detection from active scripts")
    assert_true("GitHub Actions" in technologies, "expected GitHub Actions detection")
    assert_true(".NET" not in technologies, "fixture csproj should not make the repo a .NET project")
    assert_true("harness-check" in command_ids, "expected harness check command")
    assert_true("dotnet-build" not in command_ids, "fixture csproj should not add dotnet validation")


def test_context_generation(project: Path) -> None:
    written = generate_project_context.write_outputs(project, Path("docs/project"), overwrite=True)
    context = project / "docs" / "project" / "project-context.md"
    context_json = project / "docs" / "project" / "project-context.json"
    manifest = project / "docs" / "project" / "validation" / "validation-manifest.json"
    runner = project / "docs" / "project" / "validation" / "run_project_validation.py"
    text = context.read_text(encoding="utf-8")
    data = json.loads(context_json.read_text(encoding="utf-8"))
    assert_true("docs/project/project-context.md" in "\n".join(written), "expected project context output")
    assert_true(context.exists(), "expected project-context.md")
    assert_true(manifest.exists(), "expected validation manifest")
    assert_true(runner.exists(), "expected copied validation runner")
    assert_true("## Technologies" in text, "expected technologies section")
    assert_true("## Structure And Responsibilities" in text, "expected responsibilities section")
    assert_true("## Security And Configuration Notes" in text, "expected security section")
    assert_true("type: project-context" in text, "expected project context metadata")
    assert_true("automations/navigation/artifacts/maps/HANDOFF.md" in text, "expected navigation handoff guidance")
    assert_true("## Generated Files And Boundaries" in text, "expected generated boundary section")
    assert_true("- Last reviewed:" in text, "expected freshness review marker")
    assert_true("Playwright" in text, "expected Playwright context")
    assert_true("### .NET Context" in text, "expected enriched .NET context section")
    assert_true("Private/internal NuGet feeds detected" in text, "expected private feed note")
    assert_true("No restore/build/test/package commands were run while generating this context." in text, "expected .NET safety note")
    assert_true("Build policy: `TreatWarningsAsErrors=true`" in text, "expected build policy summary")
    assert_true("CI dotnet candidates:" in text, "expected CI dotnet candidate summary")
    assert_true("Configuration inventory:" in text, "expected configuration inventory summary")
    assert_true("Persistence signals:" in text, "expected persistence summary")
    assert_true(data["dotnet_context"]["status"] in {"ready", "partial"}, "expected dotnet context status")
    assert_true(data["dotnet_context"]["nuget"]["private_feeds_detected"], "expected private feed in JSON")
    assert_true(data["dotnet_context"]["nuget"]["global_config_skipped"], "expected skipped user/global NuGet config")
    assert_true(data["dotnet_context"]["build_policy"]["properties"]["TreatWarningsAsErrors"] == "true", "expected build policy JSON")
    assert_true(any(item["command"].startswith("dotnet test") for item in data["dotnet_context"]["ci"]["dotnet_commands"]), "expected CI dotnet command JSON")
    assert_true(data["dotnet_context"]["configuration"]["appsettings_files"][0]["connection_string_names"] == ["Default"], "expected config key JSON")
    assert_true(data["dotnet_context"]["persistence"]["db_contexts"][0]["class_names"] == ["SampleDbContext"], "expected persistence JSON")
    assert_true(any(item["kind"] == "restore" for item in data["dotnet_context"]["validation_candidates"]), "expected dotnet validation candidates")
    assert_true("background-color: transparent" in (project / "docs" / "project" / "diagrams" / "project-context-structure.svg").read_text(encoding="utf-8"), "expected dark transparent SVG")
    assert_true("ops&amp;docs" in (project / "docs" / "project" / "diagrams" / "project-context-structure.svg").read_text(encoding="utf-8"), "expected escaped SVG label")
    manifest_text = manifest.read_text(encoding="utf-8")
    assert_true(sys.executable not in text, "context should not embed the current interpreter path")
    assert_true(sys.executable not in manifest_text, "validation manifest should not embed the current interpreter path")
    assert_true("python -B" in text, "context should show portable Python commands")


def test_output_dir_must_stay_under_target(project: Path) -> None:
    outside = project.parent / "outside-context"
    try:
        generate_project_context.write_outputs(project, outside, overwrite=True)
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected absolute output dir outside target to fail")
    assert_true("must resolve under target project" in message, "expected target containment error")
    assert_true(not outside.exists(), "outside output dir must not be created")


def test_existing_context_writes_full_sidecar_package(root: Path) -> None:
    project = fixture_project(root / "sidecar-root")
    reviewed_context = project / "docs" / "project" / "project-context.md"
    reviewed_json = project / "docs" / "project" / "project-context.json"
    write(reviewed_context, "reviewed context\n")
    write(reviewed_json, '{"reviewed": true}\n')

    written = generate_project_context.write_outputs(project, Path("docs/project"), overwrite=False)

    assert_true("docs/project/generated/project-context.md" in written, "expected generated sidecar context")
    assert_true("docs/project/generated/project-context.json" in written, "expected generated sidecar JSON")
    assert_true((project / "docs" / "project" / "generated" / "validation" / "validation-manifest.json").exists(), "expected sidecar validation manifest")
    assert_true((project / "docs" / "project" / "generated" / "diagrams" / "project-context-structure.svg").exists(), "expected sidecar diagrams")
    assert_true(reviewed_context.read_text(encoding="utf-8") == "reviewed context\n", "reviewed context must not be overwritten")
    assert_true(reviewed_json.read_text(encoding="utf-8") == '{"reviewed": true}\n', "reviewed JSON must not be overwritten")


def test_validation_runner_list_mode(project: Path) -> None:
    report = run_project_validation.build_report(
        project,
        Path("docs/project/validation/evidence"),
        list_only=True,
        screenshot_url="",
        timeout_seconds=5,
    )
    assert_true(report["status"] == "listed", "expected list mode")
    assert_true(report["commands"], "expected discovered commands")
    assert_true(Path(report["evidence_dir"], "validation-report.json").exists(), "expected report JSON")


def test_validation_runner_blocks_missing_executable(project: Path) -> None:
    original_executable_command = run_project_validation.executable_command

    def missing_command(_command: list[object]) -> list[str]:
        return ["definitely-missing-project-context-tool"]

    try:
        run_project_validation.executable_command = missing_command
        report = run_project_validation.build_report(
            project,
            Path("docs/project/validation/evidence"),
            list_only=False,
            screenshot_url="",
            timeout_seconds=5,
        )
    finally:
        run_project_validation.executable_command = original_executable_command

    assert_true(report["status"] == "blocked", "missing executable should block before command execution")
    assert_true(any("Required executable not found" in item for item in report["blocked"]), "expected missing executable evidence")
    assert_true(any(item.get("blocked") for item in report["commands"]), "expected blocked command row")


def test_screenshot_capture_uses_executable_command(project: Path) -> None:
    calls: list[list[object]] = []
    original_executable_command = run_project_validation.executable_command

    def fake_executable_command(command: list[object]) -> list[str]:
        calls.append(command)
        screenshot = str(command[-1])
        script = "import pathlib, sys; pathlib.Path(sys.argv[1]).write_bytes(b'png')"
        return [sys.executable, "-c", script, screenshot]

    try:
        run_project_validation.executable_command = fake_executable_command
        report = run_project_validation.capture_screenshot(
            project,
            project / "docs" / "project" / "validation" / "evidence" / "shot",
            "http://localhost:3000",
            5,
        )
    finally:
        run_project_validation.executable_command = original_executable_command

    assert_true(calls and calls[0][0] == "npx", "screenshot capture should use executable command resolution")
    assert_true(report is not None and report["ok"], "expected fake screenshot capture to pass")


def test_cli_help_classifies_read_only_write_and_runtime_modes() -> None:
    generator_help = " ".join(generate_project_context.build_parser().format_help().split())
    validation_help = " ".join(run_project_validation.build_parser().format_help().split())

    assert_true("read project root to inspect" in generator_help, "expected generator read-only target help")
    assert_true("prefer the narrow app/project root" in generator_help, "expected narrow target guidance")
    assert_true("write generated docs/project context files" in generator_help, "expected generator write label")
    assert_true("write/overwrite existing reviewed project context" in generator_help, "expected overwrite label")
    assert_true("write/runtime" in validation_help, "expected validation runner write/runtime label")
    assert_true("write evidence reports/logs" in validation_help, "expected evidence write label")
    assert_true("runtime/browser/write" in validation_help, "expected screenshot write/runtime label")
    assert_true("write a report of discovered commands without running them" in validation_help, "expected list-mode write label")


def test_self_test_help_does_not_run_temp_fixture_tests() -> None:
    result = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert_true(result.returncode == 0, "expected self-test help to exit 0")
    assert_true("write/temp" in result.stdout, "expected self-test help to label temp writes")
    assert_true("project-context-generator self-tests passed" not in result.stdout, "help should not run self-tests")


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="write/temp: run deterministic self-tests using temporary fixture projects")


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="project-context-generator-") as temp_name:
        project = fixture_project(Path(temp_name))
        test_command_discovery(project)
        test_python_unittest_discovery_without_pytest_declaration(Path(temp_name))
        test_python_pytest_discovery_requires_declared_signal(Path(temp_name))
        test_windows_node_commands_use_cmd_shims()
        test_harness_repo_ignores_fixture_technology_signals(Path(temp_name))
        test_context_generation(project)
        test_output_dir_must_stay_under_target(project)
        test_existing_context_writes_full_sidecar_package(Path(temp_name))
        test_validation_runner_list_mode(project)
        test_validation_runner_blocks_missing_executable(project)
        test_screenshot_capture_uses_executable_command(project)
        test_cli_help_classifies_read_only_write_and_runtime_modes()
        test_self_test_help_does_not_run_temp_fixture_tests()
    print("project-context-generator self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
