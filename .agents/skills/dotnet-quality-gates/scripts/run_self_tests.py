#!/usr/bin/env python3
"""Self-tests for dotnet-quality-gates."""

from __future__ import annotations

import tempfile
import unittest
import json
import subprocess
from argparse import Namespace
from pathlib import Path

import validate_coverage
import validate_line_endings
import validate_local_quality
import verify_static_analysis


SKILL_DIR = Path(__file__).resolve().parents[1]


class QualityGateTests(unittest.TestCase):
    def test_skill_contract_names_read_only_boundaries(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            "Read-Only Dogfood",
            "Skip unless writes are approved",
            "planned `dotnet` commands are documentation only",
            "Skip self-tests/eval-skill when no temp writes are allowed",
            "`--output-json`, `--output-md`, or `--output-generic-xml`",
            "Write-approved examples, not strict dogfood",
            "These commands are write-capable",
            "without `--plan-only` as a build/format/test runner",
            "not part of strict no-temp dogfood",
            "reports are caller-owned workflow or project evidence",
            "project-native restore/build/test commands may still contact package feeds or services",
        ):
            self.assertIn(phrase, text)

    def test_line_ending_detection_and_fix(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "Sample.cs"
            path.write_bytes(b"line1\r\nline2\n")
            result = validate_line_endings.validate(Namespace(target=str(path), expected="lf", fix=False))
            self.assertFalse(result["ok"])
            self.assertEqual(result["files_skipped"], 0)
            result = validate_line_endings.validate(Namespace(target=str(path), expected="lf", fix=True))
            self.assertTrue(result["ok"])
            self.assertEqual(path.read_bytes(), b"line1\nline2\n")

    def test_line_ending_reports_skipped_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Sample.cs").write_text("line1\n", encoding="utf-8")
            (root / "image.bin").write_bytes(b"\x01\x02")

            result = validate_line_endings.validate(Namespace(target=str(root), expected="consistent", fix=False))

            self.assertTrue(result["ok"])
            self.assertEqual(result["files_checked"], 1)
            self.assertEqual(result["files_skipped"], 1)

    def test_line_endings_changed_only_supports_repository_without_head_and_honors_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            (root / ".gitignore").write_text("artifacts/\n", encoding="utf-8")
            (root / "Tracked.cs").write_text("tracked\n", encoding="utf-8")
            (root / "New.cs").write_text("new\n", encoding="utf-8")
            ignored = root / "artifacts" / "Generated.json"
            ignored.parent.mkdir()
            ignored.write_bytes(b"ignored-without-newline")
            subprocess.run(["git", "add", ".gitignore", "Tracked.cs"], cwd=root, check=True)

            changed = validate_line_endings.validate(
                Namespace(target=str(root), expected="consistent", fix=False, changed_only=True)
            )
            full = validate_line_endings.validate(
                Namespace(target=str(root), expected="consistent", fix=False, changed_only=False)
            )

            self.assertTrue(changed["ok"], changed)
            self.assertEqual(changed["files_checked"], 2)
            self.assertTrue(full["ok"], full)
            self.assertEqual(full["files_checked"], 2)
            self.assertNotIn(str(ignored), {row["path"] for row in full["failures"]})

    def test_coverage_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            coverage = Path(temp) / "coverage.xml"
            coverage.write_text(
                """<?xml version="1.0"?>
<coverage>
  <packages>
    <package>
      <classes>
        <class filename="A.cs">
          <lines>
            <line number="1" hits="1" />
            <line number="2" hits="0" />
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
""",
                encoding="utf-8",
            )
            summary = validate_coverage.summarize([coverage])
            self.assertEqual(summary["lines"], 2)
            self.assertEqual(summary["covered_lines"], 1)
            self.assertEqual(summary["coverage_percent"], 50.0)
            self.assertEqual(summary["formats"], {"cobertura": 1})

    def test_opencover_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            coverage = Path(temp) / "opencover.xml"
            coverage.write_text(
                """<?xml version="1.0"?>
<CoverageSession>
  <Modules><Module><Files><File uid="1" fullPath="A.cs" /></Files>
  <Classes><Class><Methods><Method><SequencePoints>
    <SequencePoint vc="1" sl="10" fileid="1" />
    <SequencePoint vc="0" sl="11" fileid="1" />
  </SequencePoints></Method></Methods></Class></Classes></Module></Modules>
</CoverageSession>
""",
                encoding="utf-8",
            )
            summary = validate_coverage.summarize([coverage])
            self.assertEqual(summary["lines"], 2)
            self.assertEqual(summary["covered_lines"], 1)
            self.assertEqual(summary["formats"], {"opencover": 1})

    def test_dotnet_target_discovery_and_sarif_parse(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "tests" / "Demo.Tests" / "Demo.Tests.csproj"
            project.parent.mkdir(parents=True)
            project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFrameworks>net8.0;net9.0</TargetFrameworks>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.10.0" />
    <PackageReference Include="Microsoft.Playwright" Version="1.44.0" />
    <PackageReference Include="Microsoft.Testing.Platform" Version="1.2.0" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )
            (root / "Demo.sln").write_text("\n", encoding="utf-8")
            (root / "cover.runsettings").write_text("<RunSettings />\n", encoding="utf-8")

            targets = validate_coverage.discover_dotnet_targets(root)
            self.assertEqual(targets["summary"]["project_count"], 1)
            self.assertEqual(targets["summary"]["test_project_count"], 1)
            self.assertEqual(targets["summary"]["playwright_project_count"], 1)
            self.assertEqual(targets["summary"]["mtp_project_count"], 1)
            self.assertEqual(targets["summary"]["target_frameworks"], ["net8.0", "net9.0"])

            sarif = root / "analysis.sarif"
            sarif.write_text(
                json.dumps(
                    {
                        "version": "2.1.0",
                        "runs": [
                            {
                                "tool": {"driver": {"name": "demo"}},
                                "results": [
                                    {
                                        "ruleId": "CA9999",
                                        "level": "error",
                                        "message": {"text": "bad"},
                                        "locations": [
                                            {
                                                "physicalLocation": {
                                                    "artifactLocation": {"uri": "Program.cs"},
                                                    "region": {"startLine": 4},
                                                }
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            check = validate_local_quality.sarif_check([str(sarif)])
            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["findings"], 1)
            self.assertEqual(check["summary"]["levels"], {"error": 1})

    def test_static_analysis_uses_no_restore_for_packages_config_solution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project_dir = root / "LegacyApp"
            project_dir.mkdir()
            solution = root / "LegacyFixture.sln"
            solution.write_text("\n", encoding="utf-8")
            (project_dir / "LegacyApp.csproj").write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<Project ToolsVersion="15.0" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <Import Project="$(MSBuildToolsPath)\\Microsoft.CSharp.targets" />
</Project>
""",
                encoding="utf-8",
            )
            (project_dir / "packages.config").write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<packages>
  <package id="Newtonsoft.Json" version="13.0.3" targetFramework="net48" />
</packages>
""",
                encoding="utf-8",
            )
            output_json = root / "static-analysis-plan.json"

            code = verify_static_analysis.main(
                [
                    "--project-root",
                    str(root),
                    "--solution",
                    str(solution),
                    "--plan-only",
                    "--output-json",
                    str(output_json),
                ]
            )

            self.assertEqual(code, 0)
            payload = json.loads(output_json.read_text(encoding="utf-8"))
            build_command = next(command for command in payload["commands"] if command[:2] == ["dotnet", "build"])
            format_command = next(command for command in payload["commands"] if command[:2] == ["dotnet", "format"])
            self.assertTrue(payload["uses_packages_config"])
            self.assertTrue(payload["build_no_restore"])
            self.assertIn("--no-restore", format_command)
            self.assertIn("--no-restore", build_command)

    def test_static_analysis_plan_skips_without_dotnet_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output_json = root / "plan.json"

            code = verify_static_analysis.main(
                ["--project-root", str(root), "--plan-only", "--output-json", str(output_json)]
            )

            self.assertEqual(code, 0)
            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "skipped")
            self.assertEqual(payload["commands"], [])

    def test_parallel_orchestrator_evidence_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            doc = root / "README.md"
            linked = root / "linked.md"
            linked.write_text("# Linked\n", encoding="utf-8")
            doc.write_text("[linked](linked.md)\n", encoding="utf-8")
            coverage = root / "coverage.xml"
            coverage.write_text(
                """<?xml version="1.0"?>
<coverage>
  <packages><package><classes><class filename="A.cs"><lines>
    <line number="1" hits="1" />
  </lines></class></classes></package></packages>
</coverage>
""",
                encoding="utf-8",
            )
            junit = root / "junit.xml"
            junit.write_text('<testsuite tests="2" failures="0" errors="0" skipped="1" />\n', encoding="utf-8")
            output_json = root / "quality.json"
            payload = validate_local_quality.orchestrate(
                Namespace(
                    target=str(root),
                    coverage=[str(coverage)],
                    solution=None,
                    run_security=False,
                    security_target=None,
                    security_changed_only=False,
                    security_fail_on="high",
                    docs_target=[str(root)],
                    test_result=[str(junit)],
                    max_workers=4,
                    timeout_seconds=60,
                    success_output_tail_chars=1000,
                    failure_output_tail_chars=4000,
                    output_json=str(output_json),
                    output_md=None,
                )
            )
            self.assertEqual(payload["schema_version"], 1)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["parallel"]["enabled"])
            self.assertEqual(payload["summary"]["failed"], 0)
            self.assertEqual(payload["summary_schema"]["schema_version"], 1)
            check = next(item for item in payload["checks"] if item["name"] == "test-result-parse")
            self.assertEqual(check["format"], "junit")
            self.assertIn("local_ai_triage", payload)

    def test_run_security_ignores_installed_harness_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            harness = root / ".agents" / "skills" / "demo" / "fixtures" / "Unsafe.cs"
            harness.parent.mkdir(parents=True)
            harness.write_text("[AllowAnonymous]\n", encoding="utf-8")
            app = root / "src" / "Safe.cs"
            app.parent.mkdir(parents=True)
            app.write_text("public class Safe {}\n", encoding="utf-8")
            output_json = root / "quality.json"

            payload = validate_local_quality.orchestrate(
                Namespace(
                    target=str(root),
                    coverage=None,
                    solution=None,
                    run_security=True,
                    security_target=None,
                    security_changed_only=False,
                    security_fail_on="high",
                    docs_target=None,
                    test_result=None,
                    max_workers=1,
                    timeout_seconds=60,
                    success_output_tail_chars=1000,
                    failure_output_tail_chars=4000,
                    output_json=str(output_json),
                    output_md=None,
                )
            )

            self.assertTrue(payload["ok"])
            check = next(item for item in payload["checks"] if item["name"] == "security-patterns")
            self.assertEqual(check["summary"].get("high", 0), 0)

    def test_orchestrator_markdown_survives_failed_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output_json = root / "quality.json"
            output_md = root / "quality.md"
            code = validate_local_quality.main(
                [
                    "--target",
                    str(root),
                    "--test-result",
                    str(root / "missing.xml"),
                    "--output-json",
                    str(output_json),
                    "--output-md",
                    str(output_md),
                ]
            )
            self.assertEqual(code, 1)
            self.assertTrue(output_md.exists())
            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertFalse(payload["ok"])

    def test_test_result_parse_reports_flaky_candidates_from_repeated_junit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run1 = root / "junit-1.xml"
            run1.write_text(
                """<testsuite tests="2" failures="1" errors="0" skipped="0">
  <testcase classname="Demo.Tests.PricingTests" name="Calculates_discount" />
  <testcase classname="Demo.Tests.PaymentTests" name="Retries_gateway">
    <failure message="timeout" />
  </testcase>
</testsuite>
""",
                encoding="utf-8",
            )
            run2 = root / "junit-2.xml"
            run2.write_text(
                """<testsuite tests="2" failures="0" errors="0" skipped="0">
  <testcase classname="Demo.Tests.PricingTests" name="Calculates_discount" />
  <testcase classname="Demo.Tests.PaymentTests" name="Retries_gateway" />
</testsuite>
""",
                encoding="utf-8",
            )

            check = validate_local_quality.test_result_check([str(run1), str(run2)])

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["files"], 2)
            self.assertEqual(check["summary"]["tests"], 4)
            self.assertEqual(check["summary"]["failed"], 1)
            self.assertEqual(check["summary"]["case_count"], 2)
            self.assertEqual(check["summary"]["flaky_candidates"], 1)
            self.assertEqual(check["flaky_tests"][0]["name"], "Demo.Tests.PaymentTests.Retries_gateway")
            self.assertEqual(check["flaky_tests"][0]["outcomes"], {"failed": 1, "passed": 1})

    def test_test_result_parse_reports_trx_cases(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            trx = root / "results.trx"
            trx.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
  <Results>
    <UnitTestResult testName="Demo.Tests.PricingTests.Calculates_discount" outcome="Passed" />
    <UnitTestResult testName="Demo.Tests.PaymentTests.Retries_gateway" outcome="Failed" />
    <UnitTestResult testName="Demo.Tests.ExternalTests.Calls_service" outcome="NotExecuted" />
  </Results>
  <ResultSummary>
    <Counters total="3" executed="2" passed="1" failed="1" error="0" timeout="0" aborted="0" inconclusive="0" passedButRunAborted="0" notRunnable="0" notExecuted="1" disconnected="0" warning="0" completed="0" inProgress="0" pending="0" />
  </ResultSummary>
</TestRun>
""",
                encoding="utf-8",
            )

            parsed = validate_local_quality.parse_test_result(trx)

            self.assertEqual(parsed["format"], "trx")
            self.assertEqual(parsed["tests"], 3)
            self.assertEqual(parsed["failed"], 1)
            self.assertEqual(parsed["skipped"], 1)
            self.assertEqual(len(parsed["cases"]), 3)
            self.assertEqual(parsed["cases"][1], {"name": "Demo.Tests.PaymentTests.Retries_gateway", "outcome": "failed"})

    def test_test_result_parse_reports_namespaced_junit_suites(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            junit = root / "junit.xml"
            junit.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<testsuites xmlns="urn:junit">
  <testsuite name="net10.0" tests="4" failures="1" errors="1" skipped="1">
    <testcase classname="Demo.Tests.PricingTests" name="Calculates_discount" />
    <testcase classname="Demo.Tests.PaymentTests" name="Retries_gateway"><failure /></testcase>
    <testcase classname="Demo.Tests.AuthTests" name="Rejects_expired_token"><error /></testcase>
    <testcase classname="Demo.Tests.ExternalTests" name="Calls_service"><skipped /></testcase>
  </testsuite>
</testsuites>
""",
                encoding="utf-8",
            )

            parsed = validate_local_quality.parse_test_result(junit)

            self.assertEqual(parsed["format"], "junit")
            self.assertEqual(parsed["tests"], 4)
            self.assertEqual(parsed["failed"], 2)
            self.assertEqual(parsed["skipped"], 1)
            self.assertEqual(
                parsed["cases"],
                [
                    {"name": "Demo.Tests.PricingTests.Calculates_discount", "outcome": "passed"},
                    {"name": "Demo.Tests.PaymentTests.Retries_gateway", "outcome": "failed"},
                    {"name": "Demo.Tests.AuthTests.Rejects_expired_token", "outcome": "failed"},
                    {"name": "Demo.Tests.ExternalTests.Calls_service", "outcome": "skipped"},
                ],
            )

    def test_mutation_result_parse_flags_survived_and_no_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "mutation.json"
            report.write_text(
                json.dumps(
                    {
                        "files": {
                            "Pricing.cs": {
                                "mutants": [
                                    {"id": "1", "status": "Killed"},
                                    {"id": "2", "status": "Survived"},
                                    {"id": "3", "status": "NoCoverage"},
                                ]
                            }
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            check = validate_local_quality.mutation_result_check(
                [str(report)],
                minimum=80.0,
                fail_on_survived=True,
            )

            self.assertFalse(check["ok"])
            self.assertEqual(check["format"], "mutation-json")
            self.assertEqual(check["summary"]["reports"], 1)
            self.assertEqual(check["summary"]["files"], 1)
            self.assertEqual(check["summary"]["mutants"], 3)
            self.assertEqual(check["summary"]["killed"], 1)
            self.assertEqual(check["summary"]["survived"], 1)
            self.assertEqual(check["summary"]["no_coverage"], 1)
            self.assertEqual(check["summary"]["mutation_score"], 33.33)
            self.assertIn("survived mutations", check["failures"][0])

    def test_orchestrator_includes_mutation_result_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "mutation.json"
            report.write_text(
                json.dumps(
                    {
                        "mutationScore": 100.0,
                        "files": [
                            {
                                "path": "Shipping.cs",
                                "mutants": [
                                    {"id": "1", "status": "Killed"},
                                    {"id": "2", "status": "Timeout"},
                                ],
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output_json = root / "quality.json"
            payload = validate_local_quality.orchestrate(
                Namespace(
                    target=str(root),
                    coverage=None,
                    solution=None,
                    run_security=False,
                    security_target=None,
                    security_changed_only=False,
                    security_fail_on="high",
                    docs_target=None,
                    test_result=None,
                    mutation_result=[str(report)],
                    mutation_minimum=90.0,
                    mutation_fail_on_survived=True,
                    max_workers=2,
                    timeout_seconds=60,
                    success_output_tail_chars=1000,
                    failure_output_tail_chars=4000,
                    output_json=str(output_json),
                    output_md=None,
                    packet_root=None,
                )
            )

            self.assertTrue(payload["ok"])
            check = next(item for item in payload["checks"] if item["name"] == "mutation-result-parse")
            self.assertEqual(check["summary"]["mutation_score"], 100.0)
            self.assertEqual(check["summary"]["timeout"], 1)

    def test_benchmark_result_parse_detects_time_and_allocation_regressions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = root / "baseline"
            current = root / "current"
            baseline.mkdir()
            current.mkdir()
            (baseline / "demo-report-full.json").write_text(
                json.dumps(
                    {
                        "Benchmarks": [
                            {
                                "FullName": "Demo.Benchmarks.CriticalPath",
                                "Statistics": {"Mean": 100.0, "Median": 98.0, "StandardDeviation": 2.0},
                                "Memory": {"BytesAllocatedPerOperation": 1000},
                            },
                            {
                                "FullName": "Demo.Benchmarks.RemovedPath",
                                "Statistics": {"Mean": 50.0},
                            },
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (current / "demo-report-full.json").write_text(
                json.dumps(
                    {
                        "Benchmarks": [
                            {
                                "FullName": "Demo.Benchmarks.CriticalPath",
                                "Statistics": {"Mean": 125.0, "Median": 123.0, "StandardDeviation": 3.0},
                                "Memory": {"BytesAllocatedPerOperation": 1128},
                            },
                            {
                                "FullName": "Demo.Benchmarks.NewPath",
                                "Statistics": {"Mean": 10.0},
                            },
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            check = validate_local_quality.benchmark_result_check(
                [str(current)],
                baseline_paths=[str(baseline)],
                threshold_percent=10.0,
                allocation_threshold_bytes=0,
            )

            self.assertFalse(check["ok"])
            self.assertEqual(check["format"], "benchmarkdotnet-json")
            self.assertEqual(check["summary"]["benchmarks"], 2)
            self.assertEqual(check["summary"]["baseline_benchmarks"], 2)
            self.assertEqual(check["summary"]["regressions"], 1)
            self.assertEqual(check["summary"]["new_benchmarks"], 1)
            self.assertEqual(check["summary"]["missing_current"], 1)
            self.assertEqual(check["regressions"][0]["name"], "Demo.Benchmarks.CriticalPath")
            self.assertEqual(check["regressions"][0]["time_change_pct"], 25.0)
            self.assertEqual(check["regressions"][0]["allocation_change_bytes"], 128)

    def test_orchestrator_includes_benchmark_result_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / "demo-report-full.json"
            report.write_text(
                json.dumps(
                    {
                        "Benchmarks": [
                            {
                                "FullName": "Demo.Benchmarks.CriticalPath",
                                "Statistics": {"Mean": 100.0},
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output_json = root / "quality.json"
            payload = validate_local_quality.orchestrate(
                Namespace(
                    target=str(root),
                    coverage=None,
                    solution=None,
                    run_security=False,
                    security_target=None,
                    security_changed_only=False,
                    security_fail_on="high",
                    docs_target=None,
                    test_result=None,
                    mutation_result=None,
                    mutation_minimum=None,
                    mutation_fail_on_survived=False,
                    benchmark_result=[str(report)],
                    benchmark_baseline=None,
                    benchmark_threshold_percent=10.0,
                    benchmark_allocation_threshold_bytes=None,
                    max_workers=2,
                    timeout_seconds=60,
                    success_output_tail_chars=1000,
                    failure_output_tail_chars=4000,
                    output_json=str(output_json),
                    output_md=None,
                    packet_root=None,
                )
            )

            self.assertTrue(payload["ok"])
            check = next(item for item in payload["checks"] if item["name"] == "benchmark-result-parse")
            self.assertEqual(check["summary"]["benchmarks"], 1)

    def test_slop_scan_flags_disabled_tests_suppression_and_delays(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            test_file = root / "tests" / "OrderTests.cs"
            test_file.parent.mkdir(parents=True)
            test_file.write_text(
                """using Xunit;

public class OrderTests
{
    [Fact(Skip = "flaky")]
    public void Disabled_test() { }

    public async Task Slow_test()
    {
        await Task.Delay(1000);
    }

    public void Swallows()
    {
        try { DoWork(); } catch { }
    }
}
""",
                encoding="utf-8",
            )
            project = root / "src" / "Demo.csproj"
            project.parent.mkdir(parents=True)
            project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TreatWarningsAsErrors>false</TreatWarningsAsErrors>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Demo" VersionOverride="1.2.3" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["format"], "slop-patterns")
            self.assertEqual(check["summary"]["findings"], 5)
            self.assertEqual(check["summary"]["rules"]["SW001"], 1)
            self.assertEqual(check["summary"]["rules"]["SW003"], 1)
            self.assertEqual(check["summary"]["rules"]["SW004"], 1)
            self.assertEqual(check["summary"]["rules"]["SW005"], 1)
            self.assertEqual(check["summary"]["rules"]["SW006"], 1)

    def test_slop_scan_flags_suppressmessage_without_justification(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_file = root / "src" / "Formatter.cs"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                """using System.Diagnostics.CodeAnalysis;

public sealed class Formatter
{
    [SuppressMessage("Design", "CA1062")]
    public void Process(string input) { }

    [SuppressMessage("Globalization", "CA1303", Justification = "Message is a stable test fixture.")]
    public string Describe() => "ready";
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW054", 0), 1)
            self.assertNotIn("SW002", check["summary"]["rules"])

    def test_slop_scan_flags_buildserviceprovider_in_production_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_file = root / "src" / "Program.cs"
            test_file = root / "tests" / "ProviderTests.cs"
            source_file.parent.mkdir(parents=True)
            test_file.parent.mkdir(parents=True)
            source_file.write_text(
                """using Microsoft.Extensions.DependencyInjection;

var builder = WebApplication.CreateBuilder(args);
var provider = builder.Services.BuildServiceProvider();
""",
                encoding="utf-8",
            )
            test_file.write_text(
                """using Microsoft.Extensions.DependencyInjection;

public sealed class ProviderTests
{
    public void CanResolveService()
    {
        var services = new ServiceCollection();
        using var provider = services.BuildServiceProvider();
    }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW055", 0), 1)

    def test_slop_scan_flags_external_lock_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_file = root / "src" / "SharedCache.cs"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                """public sealed class SharedCache
{
    private readonly object _gate = new();

    public void Safe()
    {
        lock (_gate)
        {
        }
    }

    public void Unsafe()
    {
        lock (this) { }
        lock (typeof(SharedCache)) { }
        lock ("shared-cache") { }
        // lock (this) is mentioned in documentation only.
    }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW058", 0), 3)

    def test_slop_scan_flags_concurrent_dictionary_check_then_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_file = root / "src" / "WidgetCache.cs"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                """using System.Collections.Concurrent;
using System.Collections.Generic;

public sealed class WidgetCache
{
    private readonly ConcurrentDictionary<string, Widget> _cache = new();
    private readonly Dictionary<string, Widget> _local = new();

    public Widget GetOrCreate(string key)
    {
        if (!_cache.ContainsKey(key))
        {
            _cache[key] = CreateWidget(key);
        }

        if (!_local.ContainsKey(key))
        {
            _local[key] = CreateWidget(key);
        }

        return _cache.GetOrAdd(key, CreateWidget);
    }

    private static Widget CreateWidget(string key) => new(key);
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW059", 0), 1)

    def test_slop_scan_flags_unbounded_channel_in_production(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_file = root / "src" / "WorkQueue.cs"
            test_file = root / "tests" / "WorkQueueTests.cs"
            source_file.parent.mkdir(parents=True)
            test_file.parent.mkdir(parents=True)
            source_file.write_text(
                """using System.Threading.Channels;

public sealed class WorkQueue
{
    private readonly Channel<WorkItem> _queue =
        Channel.CreateUnbounded<WorkItem>();

    private readonly Channel<WorkItem> _bounded =
        Channel.CreateBounded<WorkItem>(100);
}
""",
                encoding="utf-8",
            )
            test_file.write_text(
                """using System.Threading.Channels;

public sealed class WorkQueueTests
{
    private readonly Channel<object> _fixture =
        Channel.CreateUnbounded<object>();
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW060", 0), 1)

    def test_slop_scan_flags_linq_asenumerable_before_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_file = root / "src" / "OrderRepository.cs"
            test_file = root / "tests" / "OrderRepositoryTests.cs"
            source_file.parent.mkdir(parents=True)
            test_file.parent.mkdir(parents=True)
            source_file.write_text(
                """using System.Linq;

public sealed class OrderRepository(AppDbContext dbContext)
{
    public List<Order> FindHighValue(decimal minimum)
    {
        var clientFiltered = dbContext.Orders
            .AsEnumerable()
            .Where(order => order.Total > minimum)
            .ToList();

        var serverFiltered = dbContext.Orders
            .Where(order => order.Total > minimum)
            .AsEnumerable()
            .Where(order => IsHighValue(order))
            .ToList();

        return clientFiltered.Concat(serverFiltered).ToList();
    }
}
""",
                encoding="utf-8",
            )
            test_file.write_text(
                """using System.Linq;

public sealed class OrderRepositoryTests
{
    public void Filters_fixture()
    {
        var values = new[] { 1, 2, 3 }
            .AsEnumerable()
            .Where(value => value > 1)
            .ToList();
    }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW061", 0), 1)

    def test_slop_scan_flags_xunit_false_success_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            test_file = root / "tests" / "PaymentTests.cs"
            test_file.parent.mkdir(parents=True)
            test_file.write_text(
                """using Xunit;

public class PaymentTests
{
    [Fact]
    public async void Async_void_test()
    {
        await Task.CompletedTask;
    }

    [Fact]
    [Theory]
    [InlineData(1)]
    public void Mixed_fact_and_theory(int value) { }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)])

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"]["SW007"], 1)
            self.assertEqual(check["summary"]["rules"]["SW008"], 1)

    def test_slop_scan_flags_infrastructure_testing_smells(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            test_file = root / "tests" / "RepositoryTests.cs"
            test_file.parent.mkdir(parents=True)
            test_file.write_text(
                """using Microsoft.EntityFrameworkCore;
using Moq;
using NSubstitute;

public class RepositoryTests
{
    [Fact]
    public void Mocks_infrastructure_types()
    {
        var context = Substitute.For<AppDbContext>();
        var client = new Mock<HttpClient>();
    }

    [Fact]
    public void Hardcodes_local_infrastructure()
    {
        var connection = "Host=localhost;Database=orders;Username=postgres";
    }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"]["SW009"], 2)
            self.assertEqual(check["summary"]["rules"]["SW010"], 1)

    def test_slop_scan_flags_clear_async_antipatterns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_file = root / "src" / "OrderService.cs"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                """using System.Threading.Tasks;

public sealed class OrderService
{
    public Order Load(int id)
    {
        return LoadAsync(id).GetAwaiter().GetResult();
    }

    public void Save(Order order)
    {
        SaveAsync(order).Wait();
    }

    public void Notify(Order order)
    {
        _ = Task.Run(async () => await SendAsync(order));
    }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"]["SW011"], 2)
            self.assertEqual(check["summary"]["rules"]["SW012"], 1)

    def test_slop_scan_flags_ef_async_without_cancellation_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_file = root / "src" / "OrderRepository.cs"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                """using Microsoft.EntityFrameworkCore;

public sealed class OrderRepository
{
    public async Task<List<Order>> ListAsync(CancellationToken ct)
    {
        var pending = await db.Orders.Where(o => o.Pending).ToListAsync();
        var first = await db.Orders.FirstOrDefaultAsync();
        await db.SaveChangesAsync();
        var safe = await db.Orders.ToListAsync(ct);
        await db.SaveChangesAsync(ct);
        return pending;
    }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"]["SW013"], 3)

    def test_slop_scan_flags_direct_httpclient_construction(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_file = root / "src" / "CatalogGateway.cs"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                """public sealed class CatalogGateway
{
    private static readonly HttpClient SharedClient = new();

    public async Task<string> LoadAsync()
    {
        using var client = new HttpClient();
        using var legacyClient = new WebClient();
        var response = await client.GetStringAsync("/products");
        return response;
    }
}
""",
                encoding="utf-8",
            )
            test_file = root / "tests" / "CatalogGatewayTests.cs"
            test_file.parent.mkdir(parents=True)
            test_file.write_text(
                """public sealed class CatalogGatewayTests
{
    public void Uses_fake_handler()
    {
        var client = new HttpClient(handler);
    }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"]["SW014"], 3)

    def test_slop_scan_flags_test_project_missing_test_sdk(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "tests" / "Orders.Tests" / "Orders.Tests.csproj"
            project.parent.mkdir(parents=True)
            project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
    <IsTestProject>true</IsTestProject>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="xunit.v3" Version="3.2.2" />
    <PackageReference Include="xunit.runner.visualstudio" Version="3.1.5" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )
            central = root / "Directory.Packages.props"
            central.write_text(
                """<Project>
  <ItemGroup>
    <PackageVersion Include="Microsoft.NET.Test.Sdk" Version="18.0.1" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW053", 0), 1)

    def test_slop_scan_flags_web_sdk_shared_framework_package_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            web_project = root / "src" / "CatalogApi" / "CatalogApi.csproj"
            class_library = root / "src" / "CatalogCore" / "CatalogCore.csproj"
            allowed_web_project = root / "src" / "AdminApi" / "AdminApi.csproj"
            web_project.parent.mkdir(parents=True)
            class_library.parent.mkdir(parents=True)
            allowed_web_project.parent.mkdir(parents=True)
            web_project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk.Web">
  <ItemGroup>
    <PackageReference Include="Microsoft.Extensions.Logging" Version="9.0.0" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )
            class_library.write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <ItemGroup>
    <PackageReference Include="Microsoft.Extensions.Logging" Version="9.0.0" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )
            allowed_web_project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk.Web">
  <ItemGroup>
    <PackageReference Include="Microsoft.Extensions.Logging.Abstractions" Version="9.0.0" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW056", 0), 1)

    def test_slop_scan_flags_superseded_http_polly_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "src" / "CatalogApi.csproj"
            project.parent.mkdir(parents=True)
            project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk.Web">
  <ItemGroup>
    <PackageReference Include="Microsoft.Extensions.Http.Polly" Version="8.0.0" />
    <PackageReference Include="Microsoft.Extensions.Http.Resilience" Version="9.0.0" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )
            central = root / "Directory.Packages.props"
            central.write_text(
                """<Project>
  <ItemGroup>
    <PackageVersion Include="Microsoft.Extensions.Http.Polly" Version="8.0.0" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"]["SW015"], 2)

    def test_slop_scan_flags_httpclient_base_address_path_without_trailing_slash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_file = root / "src" / "CatalogGateway.cs"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                """public static class CatalogGatewaySetup
{
    public static void AddClients(IServiceCollection services)
    {
        services.AddHttpClient("catalog", client =>
        {
            client.BaseAddress = new Uri("https://api.example.com/v2");
        });
        services.AddHttpClient("orders", client =>
        {
            client.BaseAddress = new("https://orders.example.com/api");
        });
        services.AddHttpClient("safe-versioned", client =>
        {
            client.BaseAddress = new Uri("https://safe.example.com/v2/");
        });
        services.AddHttpClient("safe-root", client =>
        {
            client.BaseAddress = new Uri("https://root.example.com");
        });
        // client.BaseAddress = new Uri("https://docs.example.com/v1");
    }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW038", 0), 2)

    def test_slop_scan_flags_duplicate_standard_resilience_handler(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_file = root / "src" / "CatalogGateway.cs"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                """public static class CatalogGatewaySetup
{
    public static void AddClients(IServiceCollection services)
    {
        services
            .AddHttpClient("catalog")
            .AddStandardResilienceHandler()
            .AddStandardResilienceHandler();

        services.AddHttpClient("orders").AddStandardResilienceHandler();
        services.AddHttpClient("payments").AddStandardResilienceHandler();

        // services.AddHttpClient("docs").AddStandardResilienceHandler().AddStandardResilienceHandler();
    }
}
""",
                encoding="utf-8",
            )
            test_file = root / "tests" / "CatalogGatewayTests.cs"
            test_file.parent.mkdir(parents=True)
            test_file.write_text(
                """public sealed class CatalogGatewayTests
{
    public void Uses_pipeline_twice_for_assertion()
    {
        services.AddHttpClient("fake").AddStandardResilienceHandler().AddStandardResilienceHandler();
    }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW039", 0), 1)

    def test_slop_scan_flags_legacy_polly_http_policy_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_file = root / "src" / "CatalogGateway.cs"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                """public static class CatalogGatewaySetup
{
    public static void AddClients(IServiceCollection services)
    {
        services.AddHttpClient("catalog")
            .AddTransientHttpErrorPolicy(p => p.WaitAndRetryAsync(3, attempt =>
                TimeSpan.FromSeconds(Math.Pow(2, attempt))));
    }
}

public sealed class LegacyPolicyFactory
{
    public IAsyncPolicy<HttpResponseMessage> CreatePolicy()
    {
        return HttpPolicyExtensions
            .HandleTransientHttpError()
            .WaitAndRetryAsync(3, attempt => TimeSpan.FromSeconds(attempt));
    }
}

// services.AddHttpClient("docs").AddTransientHttpErrorPolicy(p => p.WaitAndRetryAsync(3, _ => TimeSpan.Zero));
""",
                encoding="utf-8",
            )
            test_file = root / "tests" / "CatalogGatewayTests.cs"
            test_file.parent.mkdir(parents=True)
            test_file.write_text(
                """public sealed class CatalogGatewayTests
{
    public IAsyncPolicy<HttpResponseMessage> BuildLegacyTestPolicy()
        => Policy.HandleResult<HttpResponseMessage>(r => true).RetryAsync();
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW040", 0), 3)

    def test_slop_scan_flags_mixed_semantic_kernel_chat_providers_without_service_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_file = root / "src" / "AiKernelSetup.cs"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                """public static class AiKernelSetup
{
    public static void AddAi(IServiceCollection services, IConfiguration configuration)
    {
        services.AddAzureOpenAIChatCompletion(
            deploymentName: "gpt-4o",
            endpoint: configuration["AI:Endpoint"]!);

        services.AddOpenAIChatCompletion(
            modelId: "gpt-4o-mini");

        services.AddAzureOpenAIChatCompletion(
            deploymentName: "gpt-4o",
            endpoint: configuration["AI:Endpoint"]!,
            serviceId: "azure-gpt4o");

        services.AddOpenAIChatCompletion(
            modelId: "gpt-4o-mini",
            serviceId: "openai-mini");

        // services.AddOpenAIChatCompletion(modelId: "docs");
    }
}
""",
                encoding="utf-8",
            )
            test_file = root / "tests" / "AiKernelSetupTests.cs"
            test_file.parent.mkdir(parents=True)
            test_file.write_text(
                """public sealed class AiKernelSetupTests
{
    public void Allows_test_only_mixed_services()
    {
        services.AddAzureOpenAIChatCompletion(deploymentName: "fake", endpoint: endpoint);
        services.AddOpenAIChatCompletion(modelId: "fake");
    }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW041", 0), 2)

    def test_slop_scan_flags_semantic_kernel_async_plugin_without_cancellation_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_file = root / "src" / "OrderPlugin.cs"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                """public sealed class OrderPlugin
{
    [KernelFunction("get_order")]
    public async Task<OrderSummary?> GetOrderAsync(string orderId)
    {
        return await repository.GetByIdAsync(orderId);
    }

    [KernelFunction("list_orders")]
    public async Task<IReadOnlyList<OrderSummary>> ListOrdersAsync(
        string customerId,
        CancellationToken ct = default)
    {
        return await repository.ListAsync(customerId, ct);
    }

    [KernelFunction("count_orders")]
    public int CountOrders() => 0;
}
""",
                encoding="utf-8",
            )
            test_file = root / "tests" / "OrderPluginTests.cs"
            test_file.parent.mkdir(parents=True)
            test_file.write_text(
                """public sealed class OrderPluginTests
{
    [KernelFunction("test_helper")]
    public async Task<string> HelperAsync() => await Task.FromResult("ok");
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW042", 0), 1)

    def test_slop_scan_flags_legacy_api_versioning_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "src" / "CatalogApi.csproj"
            project.parent.mkdir(parents=True)
            project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk.Web">
  <ItemGroup>
    <PackageReference Include="Microsoft.AspNetCore.Mvc.Versioning" Version="5.1.0" />
    <PackageReference Include="Asp.Versioning.Http" Version="8.1.0" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )
            central = root / "Directory.Packages.props"
            central.write_text(
                """<Project>
  <ItemGroup>
    <PackageVersion Include="Microsoft.AspNetCore.Mvc.Versioning.ApiExplorer" Version="5.1.0" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW023", 0), 2)

    def test_slop_scan_flags_hallucinated_entityframeworkcore_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "src" / "OrdersApi.csproj"
            project.parent.mkdir(parents=True)
            project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk.Web">
  <ItemGroup>
    <PackageReference Include="EntityFrameworkCore" Version="9.0.0" />
    <PackageReference Include="Microsoft.EntityFrameworkCore" Version="9.0.0" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )
            central = root / "Directory.Packages.props"
            central.write_text(
                """<Project>
  <ItemGroup>
    <PackageVersion Include="EntityFrameworkCore" Version="9.0.0" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW051", 0), 2)

    def test_slop_scan_flags_broken_project_reference_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = root / "src" / "Orders.Api" / "Orders.Api.csproj"
            app.parent.mkdir(parents=True)
            lib = root / "src" / "Orders.Core" / "Orders.Core.csproj"
            lib.parent.mkdir(parents=True)
            lib.write_text("<Project Sdk=\"Microsoft.NET.Sdk\" />\n", encoding="utf-8")
            app.write_text(
                """<Project Sdk="Microsoft.NET.Sdk.Web">
  <ItemGroup>
    <ProjectReference Include="..\\Orders.Core\\Orders.Core.csproj" />
    <ProjectReference Include="..\\Missing.Core\\Missing.Core.csproj" />
    <ProjectReference Include="$(GeneratedProjectPath)" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW052", 0), 1)

    def test_slop_scan_flags_openapi_package_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "src" / "CatalogApi.csproj"
            project.parent.mkdir(parents=True)
            project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.AspNetCore.OpenApi" Version="10.0.0" />
    <PackageReference Include="Swashbuckle.AspNetCore" Version="6.7.0" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )
            safe_project = root / "src" / "SafeCatalogApi.csproj"
            safe_project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.AspNetCore.OpenApi" Version="9.*" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW026", 0), 1)
            self.assertEqual(check["summary"]["rules"].get("SW027", 0), 1)

    def test_slop_scan_flags_mvc_testing_package_tfm_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "tests" / "CatalogApi.Tests.csproj"
            project.parent.mkdir(parents=True)
            project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.AspNetCore.Mvc.Testing" Version="9.0.0" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )
            safe_project = root / "tests" / "OrdersApi.Tests.csproj"
            safe_project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.AspNetCore.Mvc.Testing" Version="9.*" />
    <PackageReference Include="Microsoft.AspNetCore.Mvc.Testing" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW057", 0), 1)

    def test_slop_scan_flags_grpc_protobuf_items_without_grpcservices(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "src" / "Orders.Client.csproj"
            project.parent.mkdir(parents=True)
            project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <ItemGroup>
    <Protobuf Include="Protos/orders.proto" />
    <Protobuf Include="Protos/catalog.proto" GrpcServices="Client" />
    <!-- <Protobuf Include="Protos/docs.proto" /> -->
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )
            test_project = root / "tests" / "Orders.Tests.csproj"
            test_project.parent.mkdir(parents=True)
            test_project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <ItemGroup>
    <Protobuf Include="Protos/test-fixture.proto" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW044", 0), 1)

    def test_slop_scan_flags_reflection_json_serialization_in_aot_projects(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app_dir = root / "src" / "AotApi"
            app_dir.mkdir(parents=True)
            project = app_dir / "AotApi.csproj"
            project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
    <PublishAot>true</PublishAot>
  </PropertyGroup>
</Project>
""",
                encoding="utf-8",
            )
            source = app_dir / "Serialization.cs"
            source.write_text(
                """using System.Text.Json;

public static class Serialization
{
    public static string Unsafe(Order order) => JsonSerializer.Serialize(order);

    public static string Safe(Order order) =>
        JsonSerializer.Serialize(order, AppJsonContext.Default.Order);

    public static Order? AlsoUnsafe(string json) =>
        JsonSerializer.Deserialize<Order>(json);

    // JsonSerializer.Serialize(order);
}
""",
                encoding="utf-8",
            )
            non_aot_dir = root / "src" / "ClassicApi"
            non_aot_dir.mkdir()
            (non_aot_dir / "ClassicApi.csproj").write_text(
                """<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
  </PropertyGroup>
</Project>
""",
                encoding="utf-8",
            )
            (non_aot_dir / "Serialization.cs").write_text(
                """using System.Text.Json;

public static class Serialization
{
    public static string Allowed(Order order) => JsonSerializer.Serialize(order);
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW046", 0), 2)

    def test_slop_scan_flags_json_serializer_context_without_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src" / "JsonContexts.cs"
            source.parent.mkdir(parents=True)
            source.write_text(
                """using System.Text.Json.Serialization;

[JsonSerializable(typeof(Order))]
internal sealed class OrderJsonContext : JsonSerializerContext
{
}

[JsonSerializable(typeof(Customer))]
internal sealed partial class CustomerJsonContext
    : global::System.Text.Json.Serialization.JsonSerializerContext
{
}

// internal class CommentedJsonContext : JsonSerializerContext {}
""",
                encoding="utf-8",
            )
            test_source = root / "tests" / "JsonContextTests.cs"
            test_source.parent.mkdir(parents=True)
            test_source.write_text(
                """using System.Text.Json.Serialization;

internal class FixtureJsonContext : JsonSerializerContext
{
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW047", 0), 1)

    def test_slop_scan_flags_logger_message_on_struct(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src" / "LogMessages.cs"
            source.parent.mkdir(parents=True)
            source.write_text(
                """using Microsoft.Extensions.Logging;

[LoggerMessage(EventId = 1, Level = LogLevel.Information, Message = "Processing {Item}")]
public static partial struct LogMessages
{
    public static partial void Processing(ILogger logger, string item);
}

public static partial class SafeLogMessages
{
    [LoggerMessage(EventId = 2, Level = LogLevel.Error, Message = "Failed {Item}")]
    public static partial void Failed(ILogger logger, string item);
}

// [LoggerMessage(EventId = 3, Message = "Ignored")]
// public static partial struct CommentedLogMessages {}
""",
                encoding="utf-8",
            )
            test_source = root / "tests" / "LogMessagesTests.cs"
            test_source.parent.mkdir(parents=True)
            test_source.write_text(
                """using Microsoft.Extensions.Logging;

[LoggerMessage(EventId = 1, Message = "Fixture")]
public static partial struct FixtureLogMessages
{
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW048", 0), 1)

    def test_slop_scan_flags_generated_regex_method_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src" / "Regexes.cs"
            source.parent.mkdir(parents=True)
            source.write_text(
                """using System.Text.RegularExpressions;

public partial class Regexes
{
    [GeneratedRegex("^[a-z]+$")]
    private partial Regex MissingStatic();

    [GeneratedRegex("^[0-9]+$")]
    private static Regex MissingPartial();

    [GeneratedRegex("^ok$")]
    private static partial string WrongReturnType();

    [GeneratedRegex("^safe$")]
    private static partial Regex SafeRegex();

    // [GeneratedRegex("^comment$")]
    // private string CommentedRegex();
}
""",
                encoding="utf-8",
            )
            test_source = root / "tests" / "RegexTests.cs"
            test_source.parent.mkdir(parents=True)
            test_source.write_text(
                """using System.Text.RegularExpressions;

public partial class RegexFixtures
{
    [GeneratedRegex("^fixture$")]
    private string FixtureRegex();
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW049", 0), 3)

    def test_slop_scan_flags_test_attributes_in_production_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src" / "MyApp.Api" / "OrderServiceTests.cs"
            source.parent.mkdir(parents=True)
            source.write_text(
                """namespace MyApp.Api;

public class OrderServiceTests
{
    [Fact]
    public void CalculateTotal_ReturnsExpectedTotal() { }

    // [Theory]
    // public void CommentedTest() { }
}
""",
                encoding="utf-8",
            )
            test_source = root / "tests" / "MyApp.Api.Tests" / "OrderServiceTests.cs"
            test_source.parent.mkdir(parents=True)
            test_source.write_text(
                """namespace MyApp.Api.Tests;

public class OrderServiceTests
{
    [Fact]
    public void CalculateTotal_ReturnsExpectedTotal() { }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW050", 0), 1)

    def test_slop_scan_flags_public_api_analyzer_tracking_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "src" / "Library.csproj"
            project.parent.mkdir(parents=True)
            project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.CodeAnalysis.PublicApiAnalyzers" Version="3.3.4" PrivateAssets="all" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )
            safe_dir = root / "safe"
            safe_dir.mkdir()
            safe_project = safe_dir / "SafeLibrary.csproj"
            safe_project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.CodeAnalysis.PublicApiAnalyzers" Version="3.3.4" PrivateAssets="all" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )
            (safe_dir / "PublicAPI.Shipped.txt").write_text("#nullable enable\n", encoding="utf-8")
            (safe_dir / "PublicAPI.Unshipped.txt").write_text("#nullable enable\n", encoding="utf-8")
            header_dir = root / "missing-header"
            header_dir.mkdir()
            header_project = header_dir / "HeaderLibrary.csproj"
            header_project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <ItemGroup>
    <PackageReference Include="Microsoft.CodeAnalysis.PublicApiAnalyzers" Version="3.3.4" PrivateAssets="all" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )
            (header_dir / "PublicAPI.Shipped.txt").write_text("Library.Widget\n", encoding="utf-8")
            (header_dir / "PublicAPI.Unshipped.txt").write_text("#nullable enable\n", encoding="utf-8")

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW028", 0), 2)
            self.assertEqual(check["summary"]["rules"].get("SW029", 0), 1)

    def test_slop_scan_flags_apicompat_suppression_file_in_property_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "src" / "Library.csproj"
            project.parent.mkdir(parents=True)
            project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
    <ApiCompatSuppressionFile>CompatibilitySuppressions.xml</ApiCompatSuppressionFile>
  </PropertyGroup>
  <ItemGroup>
    <ApiCompatSuppressionFile Include="ReviewedSuppressions.xml" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )
            safe_project = root / "src" / "SafeLibrary.csproj"
            safe_project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <ApiCompatSuppressionFile Include="CompatibilitySuppressions.xml" />
  </ItemGroup>
</Project>
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW030", 0), 1)

    def test_slop_scan_flags_nuget_license_metadata_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "src" / "PackableLibrary.csproj"
            project.parent.mkdir(parents=True)
            project.write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net9.0</TargetFramework>
    <PackageId>Demo.PackableLibrary</PackageId>
    <PackageLicenseExpression>MIT</PackageLicenseExpression>
    <PackageLicenseFile>LICENSE.txt</PackageLicenseFile>
  </PropertyGroup>
</Project>
""",
                encoding="utf-8",
            )
            safe_expression = root / "src" / "SafeExpression.csproj"
            safe_expression.write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <PackageLicenseExpression>Apache-2.0</PackageLicenseExpression>
  </PropertyGroup>
</Project>
""",
                encoding="utf-8",
            )
            safe_file = root / "src" / "SafeFile.csproj"
            safe_file.write_text(
                """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <PackageLicenseFile>LICENSE.txt</PackageLicenseFile>
  </PropertyGroup>
</Project>
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW032", 0), 1)

    def test_slop_scan_flags_unstructured_logger_templates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src" / "OrderWorker.cs"
            source.parent.mkdir(parents=True)
            source.write_text(
                """public sealed class OrderWorker(ILogger<OrderWorker> logger)
{
    public void Process(string orderId, string city, Exception ex)
    {
        logger.LogInformation($"Order {orderId} shipped to {city}");
        logger.LogWarning("Order " + orderId + " is delayed");
        logger.LogError(ex, $"Order {orderId} failed");
        logger.LogInformation("Order {OrderId} shipped to {City}", orderId, city);
    }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"]["SW016"], 3)

    def test_slop_scan_flags_secret_options_init_accessors(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src" / "SecretOptions.cs"
            source.parent.mkdir(parents=True)
            source.write_text(
                """public sealed class JwtOptions
{
    public string SigningKey { get; init; } = "";
    public string PreviousSigningKey { get; init; } = "";
    public string Issuer { get; set; } = "";
}

public sealed class SmtpOptions
{
    public string ApiKey { get; init; } = "";
    public string Host { get; init; } = "";
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"]["SW017"], 3)

    def test_slop_scan_flags_minimal_api_results_factories(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src" / "ProductEndpoints.cs"
            source.parent.mkdir(parents=True)
            source.write_text(
                """public static class ProductEndpoints
{
    public static void MapProducts(WebApplication app)
    {
        app.MapGet("/products/{id}", async (int id, AppDbContext db) =>
            await db.Products.FindAsync(id) is Product product
                ? Results.Ok(product)
                : Results.NotFound());

        app.MapGet("/products", () => TypedResults.Ok(Array.Empty<Product>()));
    }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"]["SW018"], 2)

    def test_slop_scan_flags_aspnet_middleware_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src" / "Program.cs"
            source.parent.mkdir(parents=True)
            source.write_text(
                """var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

app.UseAuthorization();
app.UseAuthentication();
app.UseRouting();
app.UseCors();

app.MapControllers();
""",
                encoding="utf-8",
            )
            safe_source = root / "src" / "SafeProgram.cs"
            safe_source.write_text(
                """var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

// app.UseAuthorization();
app.UseRouting();
app.UseCors();
app.UseAuthentication();
app.UseAuthorization();

app.MapControllers();
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"]["SW019"], 1)
            self.assertEqual(check["summary"]["rules"]["SW020"], 1)
            self.assertEqual(check["summary"]["rules"].get("SW031", 0), 1)

    def test_slop_scan_flags_yarp_reverse_proxy_before_auth_middleware(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src" / "ProxyProgram.cs"
            source.parent.mkdir(parents=True)
            source.write_text(
                """var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

app.MapReverseProxy();
app.UseAuthentication();
app.UseAuthorization();
""",
                encoding="utf-8",
            )
            safe_source = root / "src" / "SafeProxyProgram.cs"
            safe_source.write_text(
                """var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

app.UseAuthentication();
app.UseAuthorization();
app.MapReverseProxy();

// app.MapReverseProxy();
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW045", 0), 1)

    def test_slop_scan_flags_output_cache_middleware_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src" / "Program.cs"
            source.parent.mkdir(parents=True)
            source.write_text(
                """var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

app.UseOutputCache();
app.UseRouting();
app.UseCors();

app.MapGet("/products", () => TypedResults.Ok(Array.Empty<Product>()))
    .CacheOutput();
""",
                encoding="utf-8",
            )
            safe_source = root / "src" / "SafeProgram.cs"
            safe_source.write_text(
                """var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

// app.UseOutputCache();
app.UseRouting();
app.UseCors();
app.UseOutputCache();

app.MapGet("/products", () => TypedResults.Ok(Array.Empty<Product>()))
    .CacheOutput();
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW024", 0), 1)
            self.assertEqual(check["summary"]["rules"].get("SW025", 0), 1)

    def test_slop_scan_flags_hybrid_cache_local_expiration_longer_than_total_expiration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src" / "CachingSetup.cs"
            source.parent.mkdir(parents=True)
            source.write_text(
                """public static class CachingSetup
{
    public static void AddCaching(IServiceCollection services)
    {
        services.AddHybridCache(options =>
        {
            options.DefaultEntryOptions = new HybridCacheEntryOptions
            {
                Expiration = TimeSpan.FromMinutes(5),
                LocalCacheExpiration = TimeSpan.FromMinutes(30)
            };
        });

        var safeOptions = new HybridCacheEntryOptions
        {
            Expiration = TimeSpan.FromHours(1),
            LocalCacheExpiration = TimeSpan.FromMinutes(10)
        };
    }
}
""",
                encoding="utf-8",
            )
            test_file = root / "tests" / "CachingSetupTests.cs"
            test_file.parent.mkdir(parents=True)
            test_file.write_text(
                """public sealed class CachingSetupTests
{
    public void Allows_test_fixture_ttl_mismatch()
    {
        _ = new HybridCacheEntryOptions
        {
            Expiration = TimeSpan.FromSeconds(1),
            LocalCacheExpiration = TimeSpan.FromMinutes(5)
        };
    }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW043", 0), 1)

    def test_slop_scan_flags_rate_limiter_middleware_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src" / "Program.cs"
            source.parent.mkdir(parents=True)
            source.write_text(
                """var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

app.UseRateLimiter();
app.UseRouting();
app.UseAuthorization();

app.MapControllers();
""",
                encoding="utf-8",
            )
            late_source = root / "src" / "LateRateLimiter.cs"
            late_source.write_text(
                """var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

app.UseRouting();
app.UseAuthorization();
app.UseRateLimiter();

app.MapControllers();
""",
                encoding="utf-8",
            )
            mapped_source = root / "src" / "MappedBeforeRateLimiter.cs"
            mapped_source.write_text(
                """var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

app.UseRouting();
app.MapGet("/login", () => TypedResults.Ok())
    .RequireRateLimiting("auth");
app.UseRateLimiter();
""",
                encoding="utf-8",
            )
            safe_source = root / "src" / "SafeRateLimiter.cs"
            safe_source.write_text(
                """var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

// app.UseRateLimiter();
app.UseRouting();
app.UseRateLimiter();
app.UseAuthentication();
app.UseAuthorization();

app.MapGet("/login", () => TypedResults.Ok())
    .RequireRateLimiting("auth");
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW033", 0), 1)
            self.assertEqual(check["summary"]["rules"].get("SW034", 0), 1)
            self.assertEqual(check["summary"]["rules"].get("SW035", 0), 1)

    def test_slop_scan_flags_efcore_startup_migrations(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src" / "Program.cs"
            source.parent.mkdir(parents=True)
            source.write_text(
                """var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

using var scope = app.Services.CreateScope();
var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
db.Database.Migrate();
// db.Database.Migrate();

app.Run();
""",
                encoding="utf-8",
            )
            startup = root / "src" / "Startup.cs"
            startup.write_text(
                """public sealed class Startup
{
    public async Task Configure(AppDbContext context)
    {
        await context.Database.MigrateAsync();
    }
}
""",
                encoding="utf-8",
            )
            tool = root / "src" / "MigrationBundleRunner.cs"
            tool.write_text(
                """public sealed class MigrationBundleRunner
{
    public void Run(AppDbContext db)
    {
        db.Database.Migrate();
    }
}
""",
                encoding="utf-8",
            )
            test_source = root / "tests" / "ProgramTests.cs"
            test_source.parent.mkdir(parents=True)
            test_source.write_text(
                """public sealed class ProgramTests
{
    [Fact]
    public void Applies_test_migrations(AppDbContext db)
    {
        db.Database.Migrate();
    }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW036", 0), 2)

    def test_slop_scan_flags_efcore_startup_ensure_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src" / "Program.cs"
            source.parent.mkdir(parents=True)
            source.write_text(
                """var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

using var scope = app.Services.CreateScope();
var db = scope.ServiceProvider.GetRequiredService<AppDbContext>();
db.Database.EnsureCreated();
// db.Database.EnsureCreated();

app.Run();
""",
                encoding="utf-8",
            )
            startup = root / "src" / "Startup.cs"
            startup.write_text(
                """public sealed class Startup
{
    public async Task Configure(AppDbContext context)
    {
        await context.Database.EnsureCreatedAsync();
    }
}
""",
                encoding="utf-8",
            )
            tool = root / "src" / "SchemaBootstrapper.cs"
            tool.write_text(
                """public sealed class SchemaBootstrapper
{
    public void Run(AppDbContext db)
    {
        db.Database.EnsureCreated();
    }
}
""",
                encoding="utf-8",
            )
            test_source = root / "tests" / "ProgramTests.cs"
            test_source.parent.mkdir(parents=True)
            test_source.write_text(
                """public sealed class ProgramTests
{
    [Fact]
    public void Creates_test_schema(AppDbContext db)
    {
        db.Database.EnsureCreated();
    }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW037", 0), 2)

    def test_slop_scan_flags_background_service_blocking_delays(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src" / "InvoiceWorker.cs"
            source.parent.mkdir(parents=True)
            source.write_text(
                """public sealed class InvoiceWorker : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            Thread.Sleep(1000);
            await Task.Delay(TimeSpan.FromSeconds(5));
            await Task.Delay(TimeSpan.FromSeconds(10), stoppingToken);
        }
    }
}
""",
                encoding="utf-8",
            )
            test_source = root / "tests" / "InvoiceWorkerTests.cs"
            test_source.parent.mkdir(parents=True)
            test_source.write_text(
                """public sealed class InvoiceWorkerTests
{
    [Fact]
    public void Uses_fake_clock()
    {
        Thread.Sleep(1);
    }
}
""",
                encoding="utf-8",
            )

            check = validate_local_quality.slop_scan_check([str(root)], fail_on="warning")

            self.assertFalse(check["ok"])
            self.assertEqual(check["summary"]["rules"].get("SW021", 0), 1)
            self.assertEqual(check["summary"]["rules"].get("SW022", 0), 1)

    def test_snapshot_artifact_check_flags_received_files_and_missing_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshots = root / "tests" / "Snapshots"
            snapshots.mkdir(parents=True)
            (snapshots / "InvoiceTests.RendersInvoice.verified.txt").write_text("approved\n", encoding="utf-8")
            (snapshots / "InvoiceTests.RendersInvoice.received.txt").write_text("pending\n", encoding="utf-8")

            check = validate_local_quality.snapshot_artifact_check([str(root)], require_gitignore=True)

            self.assertFalse(check["ok"])
            self.assertEqual(check["format"], "snapshot-artifacts")
            self.assertEqual(check["summary"]["verified_files"], 1)
            self.assertEqual(check["summary"]["received_files"], 1)
            self.assertFalse(check["summary"]["gitignore_received_pattern"])
            self.assertIn("unapproved snapshot received files found: 1", check["failures"])
            self.assertIn("missing .gitignore pattern for *.received.*", check["failures"])

    def test_snapshot_artifact_check_passes_with_only_verified_files_and_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshots = root / "tests" / "Snapshots"
            snapshots.mkdir(parents=True)
            (snapshots / "InvoiceTests.RendersInvoice.verified.txt").write_text("approved\n", encoding="utf-8")
            (root / ".gitignore").write_text("bin/\n*.received.*\n", encoding="utf-8")

            check = validate_local_quality.snapshot_artifact_check([str(root)], require_gitignore=True)

            self.assertTrue(check["ok"])
            self.assertEqual(check["summary"]["verified_files"], 1)
            self.assertEqual(check["summary"]["received_files"], 0)
            self.assertTrue(check["summary"]["gitignore_received_pattern"])

    def test_orchestrator_includes_slop_scan_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "src" / "Demo.cs"
            source.parent.mkdir(parents=True)
            source.write_text("public class Demo {}\n", encoding="utf-8")
            output_json = root / "quality.json"
            payload = validate_local_quality.orchestrate(
                Namespace(
                    target=str(root),
                    coverage=None,
                    solution=None,
                    run_security=False,
                    security_target=None,
                    security_changed_only=False,
                    security_fail_on="high",
                    docs_target=None,
                    test_result=None,
                    mutation_result=None,
                    mutation_minimum=None,
                    mutation_fail_on_survived=False,
                    benchmark_result=None,
                    benchmark_baseline=None,
                    benchmark_threshold_percent=10.0,
                    benchmark_allocation_threshold_bytes=None,
                    run_snapshot_check=False,
                    snapshot_target=None,
                    snapshot_require_gitignore=False,
                    run_slop_scan=True,
                    slop_target=None,
                    slop_fail_on="error",
                    max_workers=2,
                    timeout_seconds=60,
                    success_output_tail_chars=1000,
                    failure_output_tail_chars=4000,
                    output_json=str(output_json),
                    output_md=None,
                    packet_root=None,
                )
            )

            self.assertTrue(payload["ok"])
            check = next(item for item in payload["checks"] if item["name"] == "slop-scan")
            self.assertEqual(check["summary"]["findings"], 0)

    def test_orchestrator_includes_snapshot_check_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshots = root / "tests" / "Snapshots"
            snapshots.mkdir(parents=True)
            (snapshots / "OrderTests.WritesOrder.verified.txt").write_text("approved\n", encoding="utf-8")
            (root / ".gitignore").write_text("*.received.*\n", encoding="utf-8")
            output_json = root / "quality.json"
            payload = validate_local_quality.orchestrate(
                Namespace(
                    target=str(root),
                    coverage=None,
                    solution=None,
                    run_security=False,
                    security_target=None,
                    security_changed_only=False,
                    security_fail_on="high",
                    docs_target=None,
                    test_result=None,
                    mutation_result=None,
                    mutation_minimum=None,
                    mutation_fail_on_survived=False,
                    benchmark_result=None,
                    benchmark_baseline=None,
                    benchmark_threshold_percent=10.0,
                    benchmark_allocation_threshold_bytes=None,
                    run_snapshot_check=True,
                    snapshot_target=None,
                    snapshot_require_gitignore=True,
                    run_slop_scan=False,
                    slop_target=None,
                    slop_fail_on="error",
                    max_workers=2,
                    timeout_seconds=60,
                    success_output_tail_chars=1000,
                    failure_output_tail_chars=4000,
                    output_json=str(output_json),
                    output_md=None,
                    packet_root=None,
                )
            )

            self.assertTrue(payload["ok"])
            check = next(item for item in payload["checks"] if item["name"] == "snapshot-artifact-check")
            self.assertEqual(check["summary"]["verified_files"], 1)

    def test_packet_root_writes_markdown_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            packet = root / "validation"
            code = validate_local_quality.main(
                [
                    "--target",
                    str(root),
                    "--packet-root",
                    str(packet),
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue((packet / "local-quality.json").exists())
            self.assertTrue((packet / "local-quality.md").exists())

    def test_bad_xml_is_reported_as_failed_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bad = root / "bad.xml"
            bad.write_text("<testsuite", encoding="utf-8")
            output_json = root / "quality.json"
            payload = validate_local_quality.orchestrate(
                Namespace(
                    target=str(root),
                    coverage=None,
                    solution=None,
                    run_security=False,
                    security_target=None,
                    security_changed_only=False,
                    security_fail_on="high",
                    docs_target=None,
                    test_result=[str(bad)],
                    max_workers=1,
                    timeout_seconds=60,
                    success_output_tail_chars=1000,
                    failure_output_tail_chars=4000,
                    output_json=str(output_json),
                    output_md=None,
                    packet_root=None,
                )
            )
            self.assertFalse(payload["ok"])
            failed = next(item for item in payload["checks"] if item["name"] == "test-result-parse")
            self.assertEqual(failed["kind"], "exception")

    def test_docs_link_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "file with spaces.md"
            target.write_text("# Target\n", encoding="utf-8")
            source = root / "README.md"
            source.write_text("[target](<file with spaces.md>)\n", encoding="utf-8")
            result = validate_local_quality.docs_check([str(root)])
            self.assertTrue(result["ok"])

    def test_namespaced_cobertura_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            coverage = Path(temp) / "coverage.xml"
            coverage.write_text(
                """<?xml version="1.0"?>
<coverage xmlns="urn:cobertura">
  <packages><package><classes><class filename="A.cs"><lines>
    <line number="1" hits="1" />
  </lines></class></classes></package></packages>
</coverage>
""",
                encoding="utf-8",
            )
            summary = validate_coverage.summarize([coverage])
            self.assertEqual(summary["lines"], 1)
            self.assertEqual(summary["covered_lines"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
