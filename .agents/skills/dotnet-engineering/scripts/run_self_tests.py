#!/usr/bin/env python3
"""Self-tests for dotnet-engineering guidance contracts."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import dotnet_repo_inspector


USAGE = """usage: run_self_tests.py [--help]

Runs dotnet-engineering self-tests. Normal execution writes only temporary fixtures
under the system temp directory and leaves skill files unchanged.
"""


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def test_repo_inspector_modern_upgrade_fixture() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        write_text(root / "NuGet.config", """<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
    <add key="Local" value="./packages" />
  </packageSources>
  <packageSourceMapping>
    <packageSource key="Local">
      <package pattern="Microsoft.*" />
    </packageSource>
  </packageSourceMapping>
</configuration>
""")
        write_text(root / "Directory.Packages.props", """<Project>
  <PropertyGroup>
    <ManagePackageVersionsCentrally>true</ManagePackageVersionsCentrally>
  </PropertyGroup>
  <ItemGroup>
    <PackageVersion Include="Microsoft.Extensions.Hosting" Version="8.0.0" />
    <GlobalPackageReference Include="Microsoft.SourceLink.GitHub" Version="8.0.0" />
  </ItemGroup>
</Project>
""")
        write_text(root / "src" / "Worker" / "Worker.csproj", """<Project Sdk="Microsoft.NET.Sdk.Worker">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.Extensions.Hosting" />
    <PackageReference Include="Serilog" VersionOverride="3.1.1" />
    <PackageDownload Include="Microsoft.NETCore.App.Ref" Version="[10.0.0]" />
  </ItemGroup>
</Project>
""")
        write_json(root / ".config" / "dotnet-tools.json", {"version": 1, "isRoot": True, "tools": {"dotnet-ef": {"version": "8.0.0", "commands": ["dotnet-ef"]}}})

        report = dotnet_repo_inspector.build_report("all", root, target_version="net10.0")

        assert report["summary"]["project_count"] == 1
        assert report["summary"]["fallback_to_general_nuget"] is False
        assert report["inventory"]["summary"]["central_package_management"] is True
        owner_kinds = report["inventory"]["summary"]["package_owner_kinds"]
        assert owner_kinds["central-package-version"] == 1
        assert owner_kinds["central-global-package-reference"] == 1
        assert owner_kinds["project-package-reference"] == 2
        assert owner_kinds["project-package-download"] == 1
        assert owner_kinds["dotnet-tool-manifest"] == 1


def test_repo_inspector_framework_compat_fixture() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        write_text(root / "Legacy" / "Legacy.csproj", """<?xml version="1.0" encoding="utf-8"?>
<Project ToolsVersion="15.0" DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <PropertyGroup>
    <TargetFrameworkVersion>v4.8</TargetFrameworkVersion>
  </PropertyGroup>
  <Import Project="$(MSBuildToolsPath)\\Microsoft.CSharp.targets" />
</Project>
""")
        write_text(root / "Legacy" / "packages.config", """<?xml version="1.0" encoding="utf-8"?>
<packages>
  <package id="Newtonsoft.Json" version="12.0.3" targetFramework="net48" />
</packages>
""")
        write_text(root / "Legacy" / "Controllers" / "HomeController.cs", """using System.Web.Mvc;
using System.Runtime.Serialization.Formatters.Binary;
public sealed class HomeController : Controller
{
    public void Save(BinaryFormatter formatter) { }
}
""")
        report = dotnet_repo_inspector.build_report("framework-compat", root, target_version="net10.0")

        compat = report["framework_compatibility"]
        rules = compat["summary"]["rule_counts"]
        assert rules["framework-target"] >= 1
        assert rules["packages-config"] >= 1
        assert rules["system-web"] >= 1
        assert rules["binaryformatter"] >= 1
        assert compat["summary"]["project_blocker_count"] == 1


def test_repo_inspector_prunes_cache_and_build_outputs() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        write_text(root / "src" / "App" / "App.csproj", """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
</Project>
""")
        write_text(root / ".agents" / "local-ai" / "cache" / "Cached.csproj", """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
  </PropertyGroup>
</Project>
""")
        write_text(root / "src" / "App" / "bin" / "Generated.csproj", """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net7.0</TargetFramework>
  </PropertyGroup>
</Project>
""")

        report = dotnet_repo_inspector.build_report("inventory", root)

        projects = report["inventory"]["projects"]
        assert [project["path"] for project in projects] == ["src/App/App.csproj"]


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    if args and args[0] in {"-h", "--help"}:
        print(USAGE, end="")
        return 0
    if args:
        print(f"unknown argument: {args[0]}", file=sys.stderr)
        print(USAGE, end="", file=sys.stderr)
        return 2

    skill_dir = Path(__file__).resolve().parents[1]
    manifest = json.loads((skill_dir / "module.json").read_text(encoding="utf-8-sig"))
    errors: list[str] = []
    if manifest.get("id") != skill_dir.name:
        errors.append("module id must match folder name")
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    if f"name: {skill_dir.name}" not in text:
        errors.append("SKILL.md frontmatter name must match folder name")
    for relative in manifest.get("inputs", []):
        if str(relative).endswith(".md") and not (skill_dir / str(relative)).exists():
            errors.append(f"missing declared input: {relative}")
    for owner in ("dotnet-legacy", "dotnet-security-review", "dotnet-quality-gates"):
        if owner not in manifest.get("related_modules", []):
            errors.append(f"{owner} handoff must stay declared")
    if "AI integration code" not in text:
        errors.append("stable AI integration boundary must stay documented")
    for phrase in (
        "Read-Only Dogfood",
        "without `--output-json` or `--output-md`",
        "These commands are write-capable",
        "current official docs lookup",
    ):
        if phrase not in text:
            errors.append(f"missing read-only guard phrase: {phrase}")
    for test in (
        test_repo_inspector_modern_upgrade_fixture,
        test_repo_inspector_framework_compat_fixture,
        test_repo_inspector_prunes_cache_and_build_outputs,
    ):
        try:
            test()
        except AssertionError as exc:
            errors.append(f"{test.__name__} failed: {exc}")
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print("dotnet-engineering self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
