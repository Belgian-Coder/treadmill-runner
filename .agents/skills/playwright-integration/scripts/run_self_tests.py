#!/usr/bin/env python3
"""Self-tests for playwright-integration."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import check_playwright_readiness
import detect_playwright_project
import diagnose_flaky_playwright
import lint_playwright_tests
import parse_playwright_results
import validate_playwright_screenshots


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def test_detects_playwright_dependency(tmp: Path) -> None:
    write_json(
        tmp / "package.json",
        {
            "devDependencies": {"@playwright/test": "^1.0.0"},
            "scripts": {"test:e2e": "playwright test"},
        },
    )
    report = check_playwright_readiness.build_report(tmp)
    assert report["status"] == "passed", report
    assert "devDependencies:@playwright/test" in report["signals"]
    assert "script:test:e2e" in report["signals"]


def test_detects_dotnet_playwright_dependency(tmp: Path) -> None:
    project = tmp / "tests" / "Demo.E2E" / "Demo.E2E.csproj"
    project.parent.mkdir(parents=True)
    project.write_text(
        """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.10.0" />
    <PackageReference Include="Microsoft.Playwright.Xunit" Version="1.44.0" />
  </ItemGroup>
</Project>
""",
        encoding="utf-8",
        newline="\n",
    )

    report = detect_playwright_project.build_report(tmp)

    assert report["status"] == "passed", report
    assert report["framework"] == "dotnet"
    assert report["language"] == "csharp"
    assert "csproj:tests/Demo.E2E/Demo.E2E.csproj:Microsoft.Playwright.Xunit" in report["signals"]
    assert report["dotnet_projects"][0]["packages"] == ["Microsoft.Playwright.Xunit"]


def test_readiness_accepts_dotnet_playwright_without_package_json(tmp: Path) -> None:
    project = tmp / "Demo.BrowserTests.csproj"
    project.write_text(
        """<Project Sdk="Microsoft.NET.Sdk">
  <ItemGroup>
    <PackageReference Include="Microsoft.Playwright.NUnit" Version="1.44.0" />
  </ItemGroup>
</Project>
""",
        encoding="utf-8",
        newline="\n",
    )

    report = check_playwright_readiness.build_report(tmp)

    assert report["status"] == "passed", report
    assert report["package_json"].endswith("package.json")
    assert "csproj:Demo.BrowserTests.csproj:Microsoft.Playwright.NUnit" in report["signals"]
    assert any(item["name"] == ".NET Playwright declared" and item["ok"] for item in report["checks"])


def test_detects_python_playwright_dependency(tmp: Path) -> None:
    (tmp / "requirements.txt").write_text("playwright==1.45.0\npytest-playwright>=0.5\n", encoding="utf-8", newline="\n")

    report = check_playwright_readiness.build_report(tmp)

    assert report["status"] == "passed", report
    assert "requirements:requirements.txt:playwright" in report["signals"]
    assert "requirements:requirements.txt:pytest-playwright" in report["signals"]
    assert report["framework"] == "python"
    assert report["language"] == "python"
    assert report["language_support"]["python"]["declared"] is True
    assert any(item["name"] == "Python Playwright declared" and item["ok"] for item in report["checks"])


def test_readiness_reports_node_python_and_dotnet_support(tmp: Path) -> None:
    write_json(tmp / "package.json", {"devDependencies": {"@playwright/test": "^1.0.0"}})
    (tmp / "requirements.txt").write_text("playwright==1.45.0\n", encoding="utf-8", newline="\n")
    project = tmp / "Demo.BrowserTests.csproj"
    project.write_text(
        """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.Playwright.NUnit" Version="1.44.0" />
  </ItemGroup>
</Project>
""",
        encoding="utf-8",
        newline="\n",
    )

    report = check_playwright_readiness.build_report(tmp, auto_install=True, preflight_install=True)

    assert set(report["language_support"]) == {"nodejs", "python", "dotnet"}
    assert all(report["language_support"][name]["supported"] for name in ("nodejs", "python", "dotnet"))
    assert all(report["language_support"][name]["declared"] for name in ("nodejs", "python", "dotnet"))
    assert report["auto_install_requested"] is True
    assert report["auto_configure_requested"] is True
    assert any(command[:2] == ["npm", "install"] or command[:2] == ["npm", "ci"] for command in report["planned_commands"])
    assert any(command[1:4] == ["-m", "pip", "install"] for command in report["planned_commands"])
    assert any(command[:2] == ["dotnet", "build"] for command in report["planned_commands"])


def test_auto_install_preflight_plans_python_browser_setup(tmp: Path) -> None:
    (tmp / "requirements.txt").write_text("playwright==1.45.0\n", encoding="utf-8", newline="\n")
    original_run = check_playwright_readiness.run

    def fail_run(command: list[str], cwd: Path, timeout_seconds: int) -> dict[str, object]:
        raise AssertionError(f"unexpected command execution: {command}")

    check_playwright_readiness.run = fail_run
    try:
        report = check_playwright_readiness.build_report(tmp, auto_install=True, preflight_install=True)
    finally:
        check_playwright_readiness.run = original_run

    assert report["planned_commands"]
    assert report["commands"] == []
    assert any(command[1:4] == ["-m", "pip", "install"] for command in report["planned_commands"])
    assert any(command[1:] == ["-m", "playwright", "install"] for command in report["planned_commands"])
    assert report["no_install_default"] is False


def test_missing_package_is_blocked(tmp: Path) -> None:
    report = check_playwright_readiness.build_report(tmp)
    assert report["ok"] is False
    assert report["status"] == "blocked"
    assert report["blocked"]


def test_no_playwright_is_skipped_without_installs(tmp: Path) -> None:
    write_json(tmp / "package.json", {"scripts": {"test": "node test.js"}})
    report = check_playwright_readiness.build_report(tmp)
    assert report["ok"] is False
    assert report["status"] == "skipped"
    assert report["skipped"]
    assert report["commands"] == []


def test_install_is_blocked_when_playwright_not_declared(tmp: Path) -> None:
    write_json(tmp / "package.json", {"scripts": {"test": "node test.js"}})
    report = check_playwright_readiness.build_report(tmp, install=True)
    assert report["ok"] is False
    assert report["status"] == "blocked"
    assert any("not declared" in item for item in report["blocked"])
    assert report["commands"] == []


def test_cli_writes_json(tmp: Path) -> None:
    write_json(tmp / "package.json", {"devDependencies": {"playwright": "^1.0.0"}})
    output = tmp / "validation" / "playwright-readiness.json"
    status = check_playwright_readiness.main(["--project-root", str(tmp), "--output-json", str(output)])
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert status == 0
    assert payload["tool"] == "playwright-integration"
    assert payload["checks"]


def test_runtime_probe_uses_no_install(tmp: Path) -> None:
    write_json(tmp / "package.json", {"devDependencies": {"@playwright/test": "^1.0.0"}})
    calls: list[list[str]] = []
    original_run = check_playwright_readiness.run

    def fake_run(command: list[str], cwd: Path, timeout_seconds: int) -> dict[str, object]:
        calls.append(command)
        return {"command": command, "returncode": 0, "duration_seconds": 0.0, "output": "Version 1.0.0"}

    check_playwright_readiness.run = fake_run
    try:
        report = check_playwright_readiness.build_report(tmp, probe_runtime=True)
    finally:
        check_playwright_readiness.run = original_run
    assert report["status"] == "passed"
    assert calls
    assert all("--no-install" in command for command in calls)


def test_preflight_install_does_not_run_commands(tmp: Path) -> None:
    write_json(tmp / "package.json", {"devDependencies": {"@playwright/test": "^1.0.0"}})
    original_run = check_playwright_readiness.run

    def fail_run(command: list[str], cwd: Path, timeout_seconds: int) -> dict[str, object]:
        raise AssertionError(f"unexpected command execution: {command}")

    check_playwright_readiness.run = fail_run
    try:
        report = check_playwright_readiness.build_report(tmp, install=True, install_browsers=True, preflight_install=True)
    finally:
        check_playwright_readiness.run = original_run
    assert report["planned_commands"]
    assert report["commands"] == []
    assert report["no_install_default"] is False


def test_malformed_package_is_blocked(tmp: Path) -> None:
    (tmp / "package.json").write_text("{not-json", encoding="utf-8", newline="\n")
    report = check_playwright_readiness.build_report(tmp)
    assert report["status"] == "blocked"
    assert "valid JSON" in report["blocked"][0]


def test_detect_project_reports_framework_gitignore_and_migration(tmp: Path) -> None:
    write_json(
        tmp / "package.json",
        {
            "dependencies": {"next": "^15.0.0"},
            "devDependencies": {"@playwright/test": "^1.0.0", "cypress": "^13.0.0"},
            "scripts": {"e2e": "playwright test"},
        },
    )
    (tmp / "playwright.config.ts").write_text("export default { reporter: [['json'], ['html']] }\n", encoding="utf-8", newline="\n")
    (tmp / "app").mkdir()
    (tmp / "app" / "page.tsx").write_text("export default function Page() { return null }\n", encoding="utf-8", newline="\n")
    (tmp / "tests").mkdir()
    (tmp / "tests" / "home.spec.ts").write_text("test('home page', async ({ page }) => { await page.goto('/') })\n", encoding="utf-8", newline="\n")
    report = detect_playwright_project.build_report(tmp)
    assert report["framework"] == "nextjs"
    assert report["language"] == "typescript"
    assert report["migration"]["cypress_detected"] is True
    assert "json" in report["reporters"]
    assert report["gitignore"]["missing"]


def test_lint_reports_antipatterns_and_markdown(tmp: Path) -> None:
    write_json(tmp / "package.json", {"devDependencies": {"@playwright/test": "^1.0.0"}})
    tests = tmp / "tests"
    tests.mkdir()
    spec = tests / "bad.spec.ts"
    spec.write_text(
        "\n".join(
            [
                "import { test, expect } from '@playwright/test';",
                "let shared;",
                "let other;",
                "test.only('should work', async ({ page }) => {",
                "page.goto('http://localhost:3000/login');",
                "await page.waitForTimeout(1000);",
                "expect(await page.textContent('.msg')).toBe('ok');",
                "await page.locator('#submit').click();",
                "});",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    report = lint_playwright_tests.build_report(tmp)
    markdown = lint_playwright_tests.render_markdown(report)
    assert report["status"] == "failed"
    assert any(item["rule"] == "wait-for-timeout" for item in report["findings"])
    assert any(item["rule"] == "css-or-xpath-first" for item in report["findings"])
    assert "Playwright Test Lint" in markdown


def test_changed_files_lint_only_uses_changed_specs(tmp: Path) -> None:
    write_json(tmp / "package.json", {"devDependencies": {"@playwright/test": "^1.0.0"}})
    tests = tmp / "tests"
    tests.mkdir()
    changed = tests / "changed.spec.ts"
    unchanged = tests / "unchanged.spec.ts"
    changed.write_text("test('changed', async ({ page }) => { page.goto('/'); })\n", encoding="utf-8", newline="\n")
    unchanged.write_text("test('unchanged', async ({ page }) => { await page.goto('/'); })\n", encoding="utf-8", newline="\n")
    original_changed_files = lint_playwright_tests.support.git_changed_files
    lint_playwright_tests.support.git_changed_files = lambda root: [changed.resolve()]
    try:
        report = lint_playwright_tests.build_report(tmp, changed_files=True)
    finally:
        lint_playwright_tests.support.git_changed_files = original_changed_files
    assert report["changed_files_mode"] is True
    assert [item.replace("\\", "/") for item in report["files_checked"]] == ["tests/changed.spec.ts"]


def test_readiness_skips_browser_setup_by_default(tmp: Path) -> None:
    write_json(tmp / "package.json", {"devDependencies": {"@playwright/test": "^1.0.0"}})
    report = check_playwright_readiness.build_report(tmp)
    assert report["install_requested"] is False
    assert report["browser_install_requested"] is False
    assert report["planned_commands"] == []
    assert report["commands"] == []
    assert report["no_install_default"] is True


def test_screenshot_validation_writes_desktop_mobile_packet_and_llm_analysis(tmp: Path) -> None:
    validation_dir = tmp / "workflow" / "validation" / "playwright"
    captured: list[dict[str, object]] = []

    original_capture = validate_playwright_screenshots.capture_one
    original_llm = validate_playwright_screenshots.run_llm_analysis

    def fake_capture(
        *,
        backend: str,
        url: str,
        capture: dict[str, object],
        project_root: Path,
        timeout_seconds: int,
    ) -> dict[str, object]:
        captured.append(capture)
        path = Path(str(capture["path"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return {"command": ["fake-playwright"], "returncode": 0, "duration_seconds": 0.0, "output": ""}

    def fake_llm(*, prompt_path: Path, screenshots: list[Path], validation_dir: Path, timeout_seconds: int) -> dict[str, object]:
        analysis = validation_dir / "llm-analysis.md"
        analysis.write_text("Screenshots reviewed by local vision model.\n", encoding="utf-8", newline="\n")
        return {"ok": True, "status": "passed", "path": str(analysis), "command": ["fake-llm"], "screenshots": [str(path) for path in screenshots]}

    validate_playwright_screenshots.capture_one = fake_capture
    validate_playwright_screenshots.run_llm_analysis = fake_llm
    try:
        report = validate_playwright_screenshots.build_report(
            project_root=tmp,
            validation_dir=validation_dir,
            url="http://127.0.0.1:4173",
        )
    finally:
        validate_playwright_screenshots.capture_one = original_capture
        validate_playwright_screenshots.run_llm_analysis = original_llm

    assert report["status"] == "passed", report
    assert [item["name"] for item in report["captures"]] == ["desktop", "mobile"]
    assert [(item["width"], item["height"]) for item in report["captures"]] == [(1440, 900), (390, 844)]
    assert len(captured) == 2
    assert all(Path(item["path"]).exists() for item in report["captures"])
    assert (validation_dir / "llm-analysis-prompt.md").exists()
    assert Path(str(report["llm_analysis"]["path"])).exists()


def test_screenshot_validation_writes_accepted_screenshots_after_success(tmp: Path) -> None:
    validation_dir = tmp / "workflow" / "runs" / "run-a" / "validation" / "playwright"
    accepted_dir = tmp / "project" / "docs" / "validation" / "playwright-accepted"

    original_capture = validate_playwright_screenshots.capture_one

    def fake_capture(
        *,
        backend: str,
        url: str,
        capture: dict[str, object],
        project_root: Path,
        timeout_seconds: int,
    ) -> dict[str, object]:
        path = Path(str(capture["path"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return {"command": ["fake-playwright"], "returncode": 0, "duration_seconds": 0.0, "output": ""}

    validate_playwright_screenshots.capture_one = fake_capture
    try:
        report = validate_playwright_screenshots.build_report(
            project_root=tmp,
            validation_dir=validation_dir,
            url="http://127.0.0.1:4173",
            skip_llm_analysis=True,
            accepted_dir=accepted_dir,
        )
    finally:
        validate_playwright_screenshots.capture_one = original_capture

    accepted = report["accepted_screenshots"]
    assert report["status"] == "passed", report
    assert accepted["status"] == "accepted"
    assert Path(str(accepted["manifest_path"])).exists()
    manifest = json.loads(Path(str(accepted["manifest_path"])).read_text(encoding="utf-8"))
    assert manifest["status"] == "accepted"
    assert Path(str(accepted["directory"])) == accepted_dir.resolve()
    assert {item["name"] for item in accepted["screenshots"]} == {"desktop", "mobile"}
    assert all(Path(str(item["path"])).exists() for item in accepted["screenshots"])
    assert not str(Path(str(accepted["directory"]))).startswith(str(validation_dir.resolve()))


def test_screenshot_backend_uses_playwright_cli_when_available(tmp: Path) -> None:
    original_command_result = validate_playwright_screenshots.command_result

    def fake_command_result(command: list[str], cwd: Path, timeout_seconds: int) -> dict[str, object]:
        assert command == ["npx", "--no-install", "playwright", "--version"]
        return {"command": command, "returncode": 0, "duration_seconds": 0.0, "output": "Version 1.60.0"}

    validate_playwright_screenshots.command_result = fake_command_result
    try:
        assert validate_playwright_screenshots.default_backend(tmp) == "node-cli"
    finally:
        validate_playwright_screenshots.command_result = original_command_result


def test_node_screenshot_runner_resolves_playwright_from_project_root(tmp: Path) -> None:
    project = tmp / "project"
    validation = tmp / "outside-validation" / "playwright"
    screenshot = validation / "screenshots" / "desktop-1440x900.png"
    project.mkdir()
    (project / "package.json").write_text('{"devDependencies":{"playwright":"^1.60.0"}}\n', encoding="utf-8", newline="\n")
    capture = {"name": "desktop", "width": 1440, "height": 900, "is_mobile": False, "path": str(screenshot)}
    original_command_result = validate_playwright_screenshots.command_result

    def fake_command_result(command: list[str], cwd: Path, timeout_seconds: int) -> dict[str, object]:
        return {"command": command, "returncode": 0, "duration_seconds": 0.0, "output": ""}

    validate_playwright_screenshots.command_result = fake_command_result
    try:
        result = validate_playwright_screenshots.capture_one(
            backend="node",
            url="http://127.0.0.1:4173",
            capture=capture,
            project_root=project,
            timeout_seconds=30,
        )
    finally:
        validate_playwright_screenshots.command_result = original_command_result

    assert result["returncode"] == 0
    runner = validation / "generated" / "capture-playwright.cjs"
    payload = validation / "generated" / "desktop-capture.json"
    assert "createRequire" in runner.read_text(encoding="utf-8")
    assert json.loads(payload.read_text(encoding="utf-8"))["projectRoot"] == str(project)


def test_screenshot_command_resolution_uses_pathext_match(tmp: Path) -> None:
    original_which = validate_playwright_screenshots.shutil.which
    validate_playwright_screenshots.shutil.which = lambda name: "C:/tools/npx.CMD" if name == "npx" else None
    try:
        assert validate_playwright_screenshots.resolve_command(["npx", "--version"]) == ["C:/tools/npx.CMD", "--version"]
        assert validate_playwright_screenshots.resolve_command(["missing-tool", "--version"]) == ["missing-tool", "--version"]
    finally:
        validate_playwright_screenshots.shutil.which = original_which


def test_screenshot_local_ai_repo_root_finds_manage_py(tmp: Path) -> None:
    root = validate_playwright_screenshots.repo_root_from_script()
    assert (root / ".agents" / "manage.py").exists(), root


def test_cli_help_classifies_read_only_write_install_and_capture_modes(tmp: Path) -> None:
    _ = tmp
    readiness_help = " ".join(check_playwright_readiness.build_parser().format_help().split())
    detect_help = " ".join(detect_playwright_project.build_parser().format_help().split())
    lint_help = " ".join(lint_playwright_tests.build_parser().format_help().split())
    parse_help = " ".join(parse_playwright_results.build_parser().format_help().split())
    flaky_help = " ".join(diagnose_flaky_playwright.build_parser().format_help().split())
    screenshot_help = " ".join(validate_playwright_screenshots.build_parser().format_help().split())

    assert "install/write" in readiness_help
    assert "read-only preview" in readiness_help
    assert "stdout-only read-only reporting" in readiness_help
    assert "process: explicit short-lived dev server" in readiness_help
    assert "no-install/process: run local Playwright metadata probes" in readiness_help

    for help_text in (detect_help, lint_help, parse_help, flaky_help):
        assert "stdout-only read-only reporting" in help_text
    assert "read-only: lint changed" in lint_help
    assert "read existing Playwright JSON results" in parse_help
    assert "read existing failure output file" in flaky_help

    assert "write/capture" in screenshot_help
    assert "write workflow-owned validation folder" in screenshot_help
    assert "write optional committable folder" in screenshot_help


def test_parse_playwright_results_counts_flaky(tmp: Path) -> None:
    results = {
        "suites": [
            {
                "specs": [
                    {
                        "tests": [
                            {"title": "passes", "expectedStatus": "passed", "results": [{"status": "passed", "duration": 10}]},
                            {
                                "title": "flaky",
                                "expectedStatus": "passed",
                                "results": [{"status": "failed", "duration": 10}, {"status": "passed", "duration": 12}],
                            },
                            {
                                "title": "fails",
                                "expectedStatus": "passed",
                                "results": [{"status": "failed", "duration": 5, "errors": [{"message": "boom"}]}],
                            },
                        ]
                    }
                ]
            }
        ]
    }
    path = tmp / "results.json"
    write_json(path, results)
    report = parse_playwright_results.build_report(path)
    assert report["summary"]["total"] == 3
    assert report["summary"]["flaky"] == 1
    assert report["summary"]["failed"] == 1
    assert report["status"] == "failed"


def test_flaky_diagnosis_classifies_timing(tmp: Path) -> None:
    report = diagnose_flaky_playwright.build_report("Timeout waiting for element to be visible", "fixture")
    assert report["category"] == "timing-async"
    assert report["recommended_commands"]


def test_reviewed_candidate_folders_are_removed(tmp: Path) -> None:
    repo_root = SCRIPT_DIR.parents[3]
    candidates = [
        repo_root / "temp" / "awesome-copilot-main" / "skills" / "playwright-automation-fill-in-form",
        repo_root / "temp" / "awesome-copilot-main" / "skills" / "playwright-explore-website",
        repo_root / "temp" / "awesome-copilot-main" / "skills" / "playwright-generate-test",
        repo_root / "temp" / "claude-skills-main" / "engineering-team" / "playwright-pro",
    ]
    assert not any(path.exists() for path in candidates)


def run_tests() -> None:
    tests = [
        test_detects_playwright_dependency,
        test_detects_dotnet_playwright_dependency,
        test_readiness_accepts_dotnet_playwright_without_package_json,
        test_detects_python_playwright_dependency,
        test_readiness_reports_node_python_and_dotnet_support,
        test_auto_install_preflight_plans_python_browser_setup,
        test_missing_package_is_blocked,
        test_no_playwright_is_skipped_without_installs,
        test_install_is_blocked_when_playwright_not_declared,
        test_cli_writes_json,
        test_runtime_probe_uses_no_install,
        test_preflight_install_does_not_run_commands,
        test_malformed_package_is_blocked,
        test_detect_project_reports_framework_gitignore_and_migration,
        test_lint_reports_antipatterns_and_markdown,
        test_changed_files_lint_only_uses_changed_specs,
        test_readiness_skips_browser_setup_by_default,
        test_screenshot_validation_writes_desktop_mobile_packet_and_llm_analysis,
        test_screenshot_validation_writes_accepted_screenshots_after_success,
        test_screenshot_backend_uses_playwright_cli_when_available,
        test_node_screenshot_runner_resolves_playwright_from_project_root,
        test_screenshot_command_resolution_uses_pathext_match,
        test_screenshot_local_ai_repo_root_finds_manage_py,
        test_cli_help_classifies_read_only_write_install_and_capture_modes,
        test_parse_playwright_results_counts_flaky,
        test_flaky_diagnosis_classifies_timing,
        test_reviewed_candidate_folders_are_removed,
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        for test in tests:
            root = base / test.__name__
            root.mkdir()
            test(root)
            print(f"PASS {test.__name__}")


def main() -> int:
    run_tests()
    print("playwright-integration self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
