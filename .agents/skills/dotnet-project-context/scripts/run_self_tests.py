#!/usr/bin/env python3
"""Self-tests for dotnet-project-context."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import dotnet_project_context
import dotnet_context_support.implementation as support_impl


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def fixture_project(root: Path) -> Path:
    project = root / "dotnet-business-app"
    write(
        project / "global.json",
        json.dumps({"sdk": {"version": "10.0.100", "rollForward": "latestFeature"}}, indent=2),
    )
    write(
        project / "Directory.Build.props",
        """<Project>
  <PropertyGroup>
    <Nullable>enable</Nullable>
    <TreatWarningsAsErrors>true</TreatWarningsAsErrors>
    <LangVersion>preview</LangVersion>
    <AnalysisLevel>latest</AnalysisLevel>
    <RestorePackagesWithLockFile>true</RestorePackagesWithLockFile>
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
    <PackageVersion Include="OpenTelemetry.Extensions.Hosting" Version="1.12.0" />
    <PackageVersion Include="xunit" Version="2.9.2" />
  </ItemGroup>
</Project>
""",
    )
    write(
        project / "NuGet.config",
        """<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
    <add key="nuget.org" value="https://api.nuget.org/v3/index.json" />
    <add key="ContosoInternal" value="https://feed-user:embedded-pat@nuget.contoso.local/v3/index.json?sig=secret-token" />
  </packageSources>
  <packageSourceMapping>
    <packageSource key="ContosoInternal">
      <package pattern="Contoso.*" />
    </packageSource>
  </packageSourceMapping>
  <packageSourceCredentials>
    <ContosoInternal>
      <add key="Username" value="svc-nuget" />
      <add key="ClearTextPassword" value="super-secret-token" />
    </ContosoInternal>
  </packageSourceCredentials>
</configuration>
""",
    )
    write(
        project / "Business.sln",
        """Microsoft Visual Studio Solution File, Format Version 12.00
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Business.Web", "src\\Business.Web\\Business.Web.csproj", "{11111111-1111-1111-1111-111111111111}"
EndProject
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Business.Core", "src\\Business.Core\\Business.Core.csproj", "{22222222-2222-2222-2222-222222222222}"
EndProject
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Business.Tests", "tests\\Business.Tests\\Business.Tests.csproj", "{33333333-3333-3333-3333-333333333333}"
EndProject
""",
    )
    write(
        project / ".github" / "workflows" / "build.yml",
        """name: build
on:
  pull_request:
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - run: dotnet restore Business.sln
      - run: dotnet build Business.sln --no-restore
      - run: dotnet test Business.sln --no-build --logger trx
      - task: DotNetCoreCLI@2
        inputs:
          command: test
""",
    )
    write(
        project / "src" / "Business.Web" / "Business.Web.csproj",
        """<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <TreatWarningsAsErrors>true</TreatWarningsAsErrors>
    <UserSecretsId>business-web-dev</UserSecretsId>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.AspNetCore.OpenApi" />
    <PackageReference Include="OpenTelemetry.Extensions.Hosting" />
    <PackageReference Include="Contoso.Platform.Client" />
    <ProjectReference Include="..\\Business.Core\\Business.Core.csproj" />
  </ItemGroup>
</Project>
""",
    )
    write(
        project / "src" / "Business.Web" / "appsettings.json",
        json.dumps(
            {
                "ConnectionStrings": {"DefaultConnection": "Endpoint=should-not-appear"},
                "Serilog": {"MinimumLevel": {"Default": "Information"}},
                "ExternalApis": {"Billing": {"BaseUrl": "https://billing.contoso.local"}},
            },
            indent=2,
        ),
    )
    write(
        project / "src" / "Business.Web" / "Properties" / "launchSettings.json",
        json.dumps(
            {
                "profiles": {
                    "Business.Web": {"commandName": "Project", "applicationUrl": "https://localhost:7001"},
                    "IIS Express": {"commandName": "IISExpress"},
                }
            },
            indent=2,
        ),
    )
    write(
        project / "src" / "Business.Core" / "Business.Core.csproj",
        """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFrameworks>net10.0;netstandard2.1</TargetFrameworks>
    <Nullable>enable</Nullable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.EntityFrameworkCore.SqlServer" />
  </ItemGroup>
</Project>
""",
    )
    write(
        project / "src" / "Business.Core" / "Data" / "AppDbContext.cs",
        """using Microsoft.EntityFrameworkCore;

namespace Business.Core.Data;

public sealed class AppDbContext : DbContext
{
    public DbSet<Customer> Customers => Set<Customer>();
}
""",
    )
    write(project / "src" / "Business.Core" / "Migrations" / "20260704120000_Initial.cs", "namespace Business.Core.Migrations;\n")
    write(
        project / "tests" / "Business.Tests" / "Business.Tests.csproj",
        """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <IsTestProject>true</IsTestProject>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="xunit" />
    <ProjectReference Include="..\\..\\src\\Business.Core\\Business.Core.csproj" />
  </ItemGroup>
</Project>
""",
    )
    return project


def fake_runner(calls: list[list[str]]):
    forbidden = {"restore", "build", "test", "publish", "pack", "package", "search", "install", "tool", "workload"}

    def run(command: list[str], cwd: Path, timeout_seconds: int) -> dict[str, object]:
        del cwd, timeout_seconds
        calls.append(command)
        lowered = {part.lower() for part in command}
        blocked = lowered.intersection(forbidden)
        assert_true(not blocked, f"unsafe dotnet command invoked: {command}")
        text = " ".join(command)
        if "--info" in command:
            return {"ok": True, "returncode": 0, "stdout": ".NET SDK:\n Version: 10.0.100\n", "stderr": ""}
        if "sln" in command:
            return {
                "ok": True,
                "returncode": 0,
                "stdout": "Project(s)\n----------\nsrc/Business.Web/Business.Web.csproj\nsrc/Business.Core/Business.Core.csproj\ntests/Business.Tests/Business.Tests.csproj\n",
                "stderr": "",
            }
        if "Business.Web.csproj" in text and "-getProperty:" in text:
            return {
                "ok": True,
                "returncode": 0,
                "stdout": "TargetFramework = net10.0\nOutputType = Exe\nNullable = enable\nImplicitUsings = enable\nTreatWarningsAsErrors = true\n",
                "stderr": "",
            }
        if "Business.Tests.csproj" in text and "-getProperty:" in text:
            return {
                "ok": True,
                "returncode": 0,
                "stdout": "TargetFramework = net10.0\nIsTestProject = true\n",
                "stderr": "",
            }
        if "Business.Core.csproj" in text and "-getProperty:" in text:
            return {
                "ok": True,
                "returncode": 0,
                "stdout": "TargetFrameworks = net10.0;netstandard2.1\nNullable = enable\n",
                "stderr": "",
            }
        if "-getItem:" in text:
            return {
                "ok": True,
                "returncode": 0,
                "stdout": "PackageReference = Microsoft.AspNetCore.OpenApi\nProjectReference = src/Business.Core/Business.Core.csproj\n",
                "stderr": "",
            }
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}

    return run


def test_modern_solution_static_and_cli_report(root: Path) -> None:
    project = fixture_project(root)
    calls: list[list[str]] = []

    report = dotnet_project_context.build_report(
        project,
        probe_cli=True,
        dotnet_executable="dotnet",
        runner=fake_runner(calls),
    )

    assert_true(report["schema_version"] == 1, "expected schema version")
    assert_true(report["tool"] == "dotnet-project-context", "expected tool id")
    assert_true(report["status"] == "ready", "expected ready report with fake CLI")
    assert_true(report["dotnet_cli"]["available"], "expected CLI available")
    assert_true(report["dotnet_cli"]["version"] == "10.0.100", "expected parsed SDK version")
    assert_true(len(report["solutions"]) == 1, "expected solution row")
    assert_true(len(report["projects"]) == 3, "expected three project rows")
    projects = {item["path"]: item for item in report["projects"]}
    web = projects["src/Business.Web/Business.Web.csproj"]
    tests = projects["tests/Business.Tests/Business.Tests.csproj"]
    assert_true(web["classification"] == "web", "expected web classification")
    assert_true(tests["classification"] == "test", "expected test classification")
    assert_true("net10.0" in web["target_frameworks"], "expected target framework")
    assert_true("Microsoft.AspNetCore.OpenApi" in web["package_references"], "expected package reference")
    assert_true(report["nuget"]["private_feeds_detected"], "expected private feed detection")
    assert_true(report["nuget"]["global_config_skipped"], "expected global config skip flag")
    assert_true(any(item["kind"] == "restore" for item in report["validation_candidates"]), "expected restore candidate")
    assert_true(any(item["id"] == "stack-runtime" for item in report["context_facts"]), "expected context facts")
    assert_true(report["build_policy"]["properties"]["TreatWarningsAsErrors"] == "true", "expected Directory.Build.props policy")
    assert_true(any(item["command"].startswith("dotnet test") for item in report["ci"]["dotnet_commands"]), "expected CI dotnet test command")
    assert_true(
        any(edge["from"] == "src/Business.Web/Business.Web.csproj" and edge["to"] == "src/Business.Core/Business.Core.csproj" for edge in report["project_graph"]["edges"]),
        "expected project graph edge",
    )
    assert_true(report["restore_prerequisites"]["private_feeds_detected"], "expected restore prerequisite private-feed flag")
    assert_true(report["restore_prerequisites"]["central_package_management"], "expected central package restore prerequisite")
    config = report["configuration"]
    assert_true(config["values_emitted"] is False, "configuration values must not be emitted")
    assert_true(config["user_secrets_ids"][0]["id"] == "business-web-dev", "expected UserSecretsId inventory")
    assert_true(config["appsettings_files"][0]["connection_string_names"] == ["DefaultConnection"], "expected connection string name inventory")
    assert_true("should-not-appear" not in json.dumps(config, sort_keys=True), "configuration values must not be emitted")
    persistence = report["persistence"]
    assert_true(any(item["class_names"] == ["AppDbContext"] for item in persistence["db_contexts"]), "expected DbContext signal")
    assert_true("Microsoft.EntityFrameworkCore.SqlServer" in persistence["provider_packages"], "expected EF provider package")
    feature_ids = {item["id"] for item in report["features"]["signals"]}
    assert_true({"aspnet-core", "openapi", "ef-core", "opentelemetry"}.issubset(feature_ids), "expected framework feature signals")
    assert_true(calls, "expected fake dotnet probes")


def test_solution_and_project_filters_narrow_report_scope(root: Path) -> None:
    project = fixture_project(root)

    report = dotnet_project_context.build_report(
        project,
        probe_cli=False,
        solution_filters=["Business.sln"],
        project_filters=["src/Business.Web/Business.Web.csproj"],
    )

    assert_true(report["filters"]["requested_solutions"] == ["Business.sln"], "expected requested solution filter")
    assert_true(report["filters"]["requested_projects"] == ["src/Business.Web/Business.Web.csproj"], "expected requested project filter")
    assert_true([item["path"] for item in report["solutions"]] == ["Business.sln"], "expected selected solution only")
    assert_true([item["path"] for item in report["projects"]] == ["src/Business.Web/Business.Web.csproj"], "expected selected project only")
    assert_true(report["summary"]["solution_count"] == 1, "expected filtered solution count")
    assert_true(report["summary"]["project_count"] == 1, "expected filtered project count")
    assert_true(report["validation_candidates"][0]["command"] == "dotnet restore Business.sln", "expected validation target to remain selected solution")


def test_ignored_temp_directories_do_not_enter_report(root: Path) -> None:
    project = root / "repo"
    write(
        project / "src" / "Real.App" / "Real.App.csproj",
        """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
  </PropertyGroup>
</Project>
""",
    )
    write(
        project / "tmp" / "dogfood" / "Temp.App" / "Temp.App.csproj",
        """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
  </PropertyGroup>
</Project>
""",
    )
    write(
        project / "temp" / "scratch" / "Scratch.sln",
        """Microsoft Visual Studio Solution File, Format Version 12.00
Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Scratch", "Scratch\\Scratch.csproj", "{11111111-1111-1111-1111-111111111111}"
EndProject
""",
    )

    report = dotnet_project_context.build_report(project, probe_cli=False)

    assert_true([item["path"] for item in report["projects"]] == ["src/Real.App/Real.App.csproj"], "expected tmp/temp project files to be ignored")
    assert_true(report["solutions"] == [], "expected tmp/temp solution files to be ignored")


def test_slnx_solution_filter_keeps_member_projects(root: Path) -> None:
    project = fixture_project(root)
    write(
        project / "Business.slnx",
        """<Solution>
  <Project Path="src\\Business.Web\\Business.Web.csproj" />
</Solution>
""",
    )

    report = dotnet_project_context.build_report(project, probe_cli=False, solution_filters=["Business.slnx"])

    assert_true([item["path"] for item in report["solutions"]] == ["Business.slnx"], "expected selected slnx solution")
    assert_true([item["path"] for item in report["projects"]] == ["src/Business.Web/Business.Web.csproj"], "expected slnx member project")
    assert_true(report["filters"]["matched_projects"] == ["src/Business.Web/Business.Web.csproj"], "expected slnx project filter match")


def test_missing_dotnet_returns_partial_static_report(root: Path) -> None:
    project = fixture_project(root)

    report = dotnet_project_context.build_report(project, probe_cli=True, dotnet_executable="")

    assert_true(report["status"] == "partial", "missing dotnet should be partial")
    assert_true(not report["dotnet_cli"]["available"], "expected unavailable CLI")
    assert_true(len(report["projects"]) == 3, "expected static project facts")
    assert_true(any(item["id"] == "dotnet-cli-missing" for item in report["advisories"]), "expected missing CLI advisory")
    assert_true(any(item["id"] == "dotnet-cli-probes" for item in report["skipped"]), "expected skipped probes")


def test_public_cli_accepts_explicit_dotnet_executable(root: Path) -> None:
    project = fixture_project(root)
    calls: list[list[str]] = []
    original_runner = support_impl.default_runner
    support_impl.default_runner = fake_runner(calls)
    try:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            exit_code = dotnet_project_context.main(
                [
                    "--target",
                    str(project),
                    "--dotnet-executable",
                    "D:/portable-dotnet/dotnet.exe",
                    "--format",
                    "json",
                ]
            )
    finally:
        support_impl.default_runner = original_runner

    report = json.loads(buffer.getvalue())

    assert_true(exit_code == 0, "expected explicit dotnet executable CLI run to succeed")
    assert_true(report["status"] == "ready", "expected fake CLI probes to make report ready")
    assert_true(report["dotnet_cli"]["path"] == "D:/portable-dotnet/dotnet.exe", "expected explicit dotnet path in report")
    assert_true(calls and all(call[0] == "D:/portable-dotnet/dotnet.exe" for call in calls), "expected probes to use explicit dotnet path")


def test_private_feed_detection_does_not_emit_credentials(root: Path) -> None:
    project = fixture_project(root)

    report = dotnet_project_context.build_report(project, probe_cli=False)
    serialized = json.dumps(report, sort_keys=True)

    assert_true(report["nuget"]["private_feeds_detected"], "expected private feed")
    assert_true("ContosoInternal" in serialized, "feed key may be reported")
    assert_true("super-secret-token" not in serialized, "secret values must not be emitted")
    assert_true("svc-nuget" not in serialized, "credential values must not be emitted")
    assert_true("embedded-pat" not in serialized, "embedded URL passwords must not be emitted")
    assert_true("feed-user" not in serialized, "embedded URL usernames must not be emitted")
    assert_true("secret-token" not in serialized, "signed URL query values must not be emitted")
    source = next(item for item in report["nuget"]["sources"] if item["key"] == "ContosoInternal")
    assert_true(source["value"] == "https://nuget.contoso.local/v3/index.json", "expected redacted feed URL")
    assert_true(source["redacted"], "expected redacted flag for credential-bearing source URL")
    assert_true(report["nuget"]["credential_sections_present"] == ["ContosoInternal"], "expected credential section names only")


def test_msbuild_probe_output_merges_evaluated_facts(root: Path) -> None:
    project = fixture_project(root)
    calls: list[list[str]] = []

    report = dotnet_project_context.build_report(
        project,
        probe_cli=True,
        dotnet_executable="dotnet",
        runner=fake_runner(calls),
    )
    web = next(item for item in report["projects"] if item["path"] == "src/Business.Web/Business.Web.csproj")

    assert_true(web["evaluated"]["TargetFramework"] == "net10.0", "expected evaluated TargetFramework")
    assert_true(web["evaluated"]["OutputType"] == "Exe", "expected evaluated OutputType")
    assert_true("Microsoft.AspNetCore.OpenApi" in web["evaluated_package_references"], "expected evaluated package ref")


def test_render_markdown_includes_policy_sections(root: Path) -> None:
    project = fixture_project(root)
    report = dotnet_project_context.build_report(project, probe_cli=False)

    markdown = dotnet_project_context.render_markdown(report)

    assert_true("# .NET Project Context" in markdown, "expected title")
    assert_true("NuGet/feed policy" in markdown, "expected feed policy section")
    assert_true("Build policy" in markdown, "expected build policy section")
    assert_true("CI signals" in markdown, "expected CI section")
    assert_true("Configuration inventory" in markdown, "expected configuration section")
    assert_true("No restore/build/test/package commands were run" in markdown, "expected safety note")
    assert_true("Business.Web.csproj" in markdown, "expected project row")


def test_write_evidence_stays_under_target(root: Path) -> None:
    project = fixture_project(root)
    report = dotnet_project_context.build_report(project, probe_cli=False)

    evidence = dotnet_project_context.write_evidence(report, project)

    assert_true(evidence["written"] == ["docs/project/dotnet-context/dotnet-context.json", "docs/project/dotnet-context/dotnet-context.md"], "expected default evidence paths")
    assert_true((project / "docs/project/dotnet-context/dotnet-context.json").exists(), "expected evidence json")
    assert_true((project / "docs/project/dotnet-context/dotnet-context.md").exists(), "expected evidence markdown")
    try:
        dotnet_project_context.write_evidence(report, project, output_dir=root / "outside")
    except ValueError as exc:
        assert_true("outside target" in str(exc), "expected outside-target guard")
    else:
        raise AssertionError("expected write_evidence to reject output outside target")


def test_diff_baseline_reports_context_drift(root: Path) -> None:
    project = fixture_project(root)
    baseline = dotnet_project_context.build_report(project, probe_cli=False)
    write(project / "src" / "Business.Core" / "Business.Core.csproj", "<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup></Project>\n")
    current = dotnet_project_context.build_report(project, probe_cli=False, baseline_report=baseline)

    diff = current["diff"]

    assert_true(diff["changed"], "expected baseline diff to report changes")
    assert_true("target_frameworks_changed" in diff["categories"], "expected target framework drift")


def test_cli_help_does_not_run_inspection() -> None:
    help_text = " ".join(dotnet_project_context.build_parser().format_help().split())

    assert_true("read-only inspection" in help_text, "expected read-only wording")
    assert_true("does not run restore/build/test" in help_text, "expected safety wording")
    assert_true("--solution" in help_text, "expected solution filter option")
    assert_true("--project" in help_text, "expected project filter option")
    assert_true("--dotnet-executable" in help_text, "expected explicit dotnet executable option")
    assert_true("--write-evidence" in help_text, "expected write evidence option")
    assert_true("--baseline" in help_text, "expected baseline option")


def test_public_cli_entrypoint_stays_thin() -> None:
    public_script = SCRIPT_DIR / "dotnet_project_context.py"
    lines = public_script.read_text(encoding="utf-8").splitlines()

    assert_true(len(lines) <= 120, "public dotnet_project_context.py should stay a thin CLI/import wrapper")
    assert_true(any("dotnet_context_support" in line for line in lines), "public wrapper should delegate to skill-owned support modules")


def test_support_implementation_is_split_by_responsibility() -> None:
    support_dir = SCRIPT_DIR / "dotnet_context_support"
    implementation = support_dir / "implementation.py"
    lines = implementation.read_text(encoding="utf-8").splitlines()
    modules = {path.name for path in support_dir.glob("*.py")}

    assert_true(len(lines) <= 650, "support implementation should keep orchestration thin and split inspectors by responsibility")
    assert_true(
        {"common.py", "project_files.py", "nuget.py", "context_sections.py", "cli_probes.py"}.issubset(modules),
        "expected separate support modules for common, project files, NuGet, context sections, and CLI probes",
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="write/temp: run deterministic dotnet-project-context fixture tests")


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="dotnet-project-context-") as temp_name:
        root = Path(temp_name)
        test_modern_solution_static_and_cli_report(root / "ready")
        test_solution_and_project_filters_narrow_report_scope(root / "filters")
        test_ignored_temp_directories_do_not_enter_report(root / "ignored-temp")
        test_slnx_solution_filter_keeps_member_projects(root / "slnx-filter")
        test_missing_dotnet_returns_partial_static_report(root / "missing-dotnet")
        test_public_cli_accepts_explicit_dotnet_executable(root / "explicit-dotnet")
        test_private_feed_detection_does_not_emit_credentials(root / "feeds")
        test_msbuild_probe_output_merges_evaluated_facts(root / "msbuild")
        test_render_markdown_includes_policy_sections(root / "markdown")
        test_write_evidence_stays_under_target(root / "evidence")
        test_diff_baseline_reports_context_drift(root / "diff")
        test_cli_help_does_not_run_inspection()
        test_public_cli_entrypoint_stays_thin()
        test_support_implementation_is_split_by_responsibility()
    print("dotnet-project-context self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
