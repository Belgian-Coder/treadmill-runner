#!/usr/bin/env python3
"""Self-tests for repo-navigation."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import brief_repo


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def section(report: dict[str, object], section_id: str) -> list[dict[str, object]]:
    for item in report["sections"]:
        if isinstance(item, dict) and item.get("id") == section_id:
            values = item.get("items", [])
            assert isinstance(values, list)
            return values
    raise AssertionError(f"section not found: {section_id}")


def test_briefing_uses_complete_canonical_project_policy(tmp: Path) -> None:
    document = brief_repo.repo_policy.default_policy_document()
    document["owner_defaults"]["repo_navigation"]["briefing"]["default_profile"] = "short"
    document["owner_defaults"]["repo_navigation"]["briefing"]["profiles"]["short"]["item_limit"] = 3
    write(
        tmp / brief_repo.repo_policy.PROJECT_POLICY_PATH,
        json.dumps(document, indent=2) + "\n",
    )

    profiles, default_profile = brief_repo.configured_output_budgets(tmp)

    assert default_profile == "short"
    assert profiles["short"]["item_limit"] == 3

    write(
        tmp / brief_repo.repo_policy.PROJECT_POLICY_PATH,
        json.dumps({"schema_version": 2, "owner_defaults": document["owner_defaults"]}) + "\n",
    )
    try:
        brief_repo.configured_output_budgets(tmp)
    except ValueError as exc:
        assert "$schema" in str(exc)
    else:
        raise AssertionError("incomplete v2 project policy must be rejected")


def test_brief_mode_maps_repository(tmp: Path) -> None:
    write(tmp / "AGENTS.md", "# Instructions\n")
    write(tmp / ".agents" / "manage.py", "print('manage')\n")
    write(tmp / "pyproject.toml", "[project]\nname='demo'\n")
    write(tmp / "README.md", "Run `python -B .agents/manage.py validate`.\n")
    write(tmp / "src" / "app.py", "print('app')\n")
    write(tmp / "temp" / "ignored" / "secret.py", "VALUE='bad'\n")

    report = brief_repo.build_report(tmp, mode="brief")

    assert report["ok"] is True
    assert report["schema_version"] == 1
    assert report["tool"] == "repo-navigation"
    assert report["next_file_to_open"] == {"path": "AGENTS.md", "reason": "low-context repository guidance"}
    guidance = section(report, "guidance")
    assert any(item.get("path") == "AGENTS.md" for item in guidance)
    commands = section(report, "commands")
    assert any("validate" in str(item.get("command")) for item in commands)
    skipped = report["skipped"]
    assert any("temp" in str(item) for item in skipped)
    assert any(item.get("path") == "temp" for item in report["do_not_open"])
    read_order = section(report, "recommended_read_order")
    assert read_order[0]["path"] == "AGENTS.md"


def test_changed_mode_prioritizes_git_changes(tmp: Path) -> None:
    write(tmp / "AGENTS.md", "# Instructions\n")
    write(tmp / "README.md", "# Demo\n")
    write(tmp / "src" / "app.py", "print('old')\n")
    subprocess.run(["git", "init"], cwd=tmp, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp, check=True, stdout=subprocess.DEVNULL)
    write(tmp / "src" / "app.py", "print('new')\n")

    report = brief_repo.build_report(tmp, mode="changed")

    git_items = section(report, "active_git_state")
    assert any(item.get("path") == "src/app.py" for item in git_items)
    read_order = section(report, "recommended_read_order")
    assert any(item.get("path") == "src/app.py" for item in read_order)
    knowledge = section(report, "knowledge_categories")
    assert {"stack", "structure", "entrypoints", "testing"}.issubset({item.get("category") for item in knowledge})
    history = section(report, "git_history")
    assert any(item.get("kind") == "repo-history" for item in history), history


def test_cli_writes_json_report(tmp: Path) -> None:
    write(tmp / "AGENTS.md", "# Instructions\n")
    output = tmp / "out" / "brief.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_DIR / "brief_repo.py"),
            "--target",
            str(tmp),
            "--mode",
            "brief",
            "--format",
            "json",
            "--output",
            str(output),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["mode"] == "brief"


def test_new_repo_and_resume_modes_with_budget_and_risks(tmp: Path) -> None:
    write(tmp / "AGENTS.md", "# Instructions\n")
    write(tmp / "README.md", "# Demo\n")
    write(tmp / "GEMINI.md", "# Gemini\n")
    write(tmp / ".github" / "copilot-instructions.md", "# Copilot\n")
    write(tmp / ".agents" / "local-ai" / "cache" / "payload.json", "{}\n")
    write(tmp / "src" / "app.generated.cs", "public class Generated {}\n")
    subprocess.run(["git", "init"], cwd=tmp, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp, check=True, stdout=subprocess.DEVNULL)
    write(tmp / "README.md", "# Demo\n\nChanged.\n")

    first = brief_repo.build_report(tmp, mode="new-repo", budget="short")
    resume = brief_repo.build_report(tmp, mode="resume", budget="short")

    assert first["mode"] == "new-repo"
    assert first["budget"] == "short"
    assert "New-repo mode" in first["summary"]
    risk_items = section(first, "risk_hints")
    assert first["risk_hints"] == risk_items
    assert any("local AI runtime" in str(item.get("reason")) for item in risk_items)
    assert any("generated file" in str(item.get("reason")) for item in risk_items)
    assert any(item.get("path") == ".agents/local-ai/cache/payload.json" for item in first["do_not_open"])
    assert "Resume mode" in resume["summary"]
    assert any(item.get("path") == "README.md" for item in section(resume, "recommended_read_order"))


def test_short_budget_caps_sections_and_surfaces_agent_guidance(tmp: Path) -> None:
    write(tmp / "AGENTS.md", "# Instructions\n")
    write(tmp / "llms.txt", "# LLM notes\n")
    write(tmp / "REPO_MEMORY.md", "# Repo memory\n")
    write(tmp / ".context-pack" / "memory.md", "# Context memory\n")
    write(tmp / ".clio" / "instructions.md", "# Clio instructions\n")
    for index in range(12):
        write(tmp / f"module-{index:02d}" / "module.json", "{}\n")

    report = brief_repo.build_report(tmp, mode="brief", budget="short")

    assert report["budget_limits"]["item_limit"] == 8
    assert isinstance(report["estimated_prompt_tokens"], int)
    assert report["estimated_prompt_tokens"] > 0
    guidance_paths = {item.get("path") for item in section(report, "guidance")}
    assert {"llms.txt", "REPO_MEMORY.md", ".context-pack/memory.md", ".clio/instructions.md"}.issubset(
        guidance_paths
    )
    assert len(section(report, "manifests")) == 8
    assert len(section(report, "recommended_read_order")) <= 12
    assert len(report["do_not_open"]) <= 12
    assert any("short budget capped section output" in warning for warning in report["warnings"])


def test_dispatcher_supports_lite_and_changed_aliases(tmp: Path) -> None:
    write(tmp / "AGENTS.md", "# Instructions\n")
    write(tmp / "README.md", "# Demo\n")
    dispatcher = SCRIPT_DIR.parent / "repo_navigation.py"

    lite = subprocess.run(
        [sys.executable, "-B", str(dispatcher), "lite", "--target", str(tmp), "--format", "json"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    changed = subprocess.run(
        [sys.executable, "-B", str(dispatcher), "changed", "--target", str(tmp), "--format", "json"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    assert lite.returncode == 0, lite.stdout
    assert changed.returncode == 0, changed.stdout
    lite_report = json.loads(lite.stdout)
    changed_report = json.loads(changed.stdout)
    assert lite_report["budget"] == "short"
    assert len(section(lite_report, "recommended_read_order")) <= 12
    assert changed_report["mode"] == "changed"


def test_subfolder_git_state_is_target_scoped(tmp: Path) -> None:
    write(tmp / "AGENTS.md", "# Instructions\n")
    write(tmp / "service" / "README.md", "# Service\n")
    write(tmp / "other" / "README.md", "# Other\n")
    subprocess.run(["git", "init"], cwd=tmp, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "tests@example.invalid"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=tmp, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp, check=True, stdout=subprocess.DEVNULL)
    write(tmp / "service" / "README.md", "# Service\n\nChanged.\n")
    write(tmp / "other" / "README.md", "# Other\n\nChanged outside target.\n")

    report = brief_repo.build_report(tmp / "service", mode="changed")

    active_paths = {item.get("path") for item in section(report, "active_git_state") if item.get("path")}
    assert active_paths == {"README.md"}
    read_order = {item.get("path") for item in section(report, "recommended_read_order")}
    assert "README.md" in read_order
    assert "other/README.md" not in read_order


def test_brief_clamps_file_scan_and_uses_bounded_reads(tmp: Path) -> None:
    write(tmp / "README.md", "abcdef\n")

    report = brief_repo.build_report(tmp, mode="brief", max_files=1_000_000, budget="deep")

    assert (
        f"file scan limit clamped from 1000000 to {brief_repo.MAX_SCAN_FILES} files"
        in report["skipped"]
    )
    stream = mock.MagicMock()
    stream.read.side_effect = lambda limit: b"abcdef"[:limit]
    opened = mock.MagicMock()
    opened.__enter__.return_value = stream
    fake_path = mock.MagicMock()
    fake_path.open.return_value = opened

    assert brief_repo.read_text(fake_path, limit=4) == "abcd"
    fake_path.open.assert_called_once_with("rb")
    stream.read.assert_called_once_with(4)

    binary_stream = mock.MagicMock()
    binary_stream.read.return_value = b"text"
    binary_opened = mock.MagicMock()
    binary_opened.__enter__.return_value = binary_stream
    binary_path = mock.MagicMock()
    binary_path.open.return_value = binary_opened

    assert brief_repo.looks_binary(binary_path) is False
    binary_path.open.assert_called_once_with("rb")
    binary_stream.read.assert_called_once_with(4096)


def run_tests() -> None:
    tests = [
        test_briefing_uses_complete_canonical_project_policy,
        test_brief_mode_maps_repository,
        test_changed_mode_prioritizes_git_changes,
        test_cli_writes_json_report,
        test_new_repo_and_resume_modes_with_budget_and_risks,
        test_short_budget_caps_sections_and_surfaces_agent_guidance,
        test_dispatcher_supports_lite_and_changed_aliases,
        test_subfolder_git_state_is_target_scoped,
        test_brief_clamps_file_scan_and_uses_bounded_reads,
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        for test in tests:
            test_root = base / test.__name__
            test_root.mkdir()
            test(test_root)
            print(f"PASS {test.__name__}")


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="write/temp: run repo-navigation briefing self-tests using temporary fixture projects")


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    brief_repo.require_supported_python()
    run_tests()
    print("repo-navigation self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
