"""Focused orchestration and public CLI tests for determinism-check."""

from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from repo_support import repo_cli_parser
from repo_support import repo_determinism


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def command(command_id: str, *, effects: list[str] | None = None) -> dict[str, object]:
    return {
        "id": command_id,
        "argv": ["python", "-B", f"scripts/{command_id}.py"],
        "timeout_seconds": 30,
        "working_directory": "repository",
        "effects": effects or [],
    }


def manifest(
    module_id: str,
    commands: list[dict[str, object]],
    *,
    minimum_python: str = "3.12",
    executables: list[str] | None = None,
    platforms: list[str] | None = None,
) -> dict[str, object]:
    ids = [str(row["id"]) for row in commands]
    return {
        "schema_version": 3,
        "kind": "skill",
        "id": module_id,
        "name": module_id,
        "version": "1.0.0",
        "status": "accepted",
        "summary": "Determinism orchestration fixture.",
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
        "strict_read_only_commands": ids,
        "determinism": {
            "replay_commands": ids,
            "allowed_temporary_effects": [],
            "volatile_json_pointers": [],
            "environment_requirements": {
                "minimum_python": minimum_python,
                "executables": executables or [],
                "platforms": platforms or ["windows", "linux", "macos"],
            },
        },
        "extensions": {},
    }


def capture(returncode: int = 0) -> dict[str, object]:
    empty = base64.b64encode(b"").decode("ascii")
    return {
        "capture_ok": True,
        "status": "captured" if returncode == 0 else "captured-nonzero",
        "returncode": returncode,
        "timed_out": False,
        "stdout_base64": empty,
        "stderr_base64": empty,
        "changes": {"repository": [], "temporary": [], "home": []},
        "artifacts": {},
        "snapshots": {"before": {}, "after": {}},
    }


def pair(returncode: int = 0, *, cleanup_ok: bool = True) -> dict[str, object]:
    return {
        "ok": cleanup_ok,
        "status": "captured" if cleanup_ok else "cleanup-failed",
        "runs": [capture(returncode), capture(returncode)],
        "cleanup": {
            "attempted": True,
            "ok": cleanup_ok,
            "issue": "" if cleanup_ok else "cleanup denied",
        },
        "fixture_isolation": {"independent_git_directories": True},
    }


def module_path(root: Path, module_id: str) -> Path:
    return root / ".agents/skills" / module_id / "module.json"


def test_determinism_orchestration_environment_blocks_before_runner(tmp):
    root = tmp / "repo"
    current = repo_determinism.current_platform_id()
    wrong_platform = next(value for value in ("windows", "linux", "macos") if value != current)
    write_json(module_path(root, "python"), manifest("python", [command("check")], minimum_python="999.0"))
    write_json(
        module_path(root, "executable"),
        manifest("executable", [command("check")], executables=["definitely-missing-tool"]),
    )
    write_json(
        module_path(root, "platform"),
        manifest("platform", [command("check")], platforms=[wrong_platform]),
    )
    calls: list[object] = []

    report = repo_determinism.run_determinism_check(
        root,
        all_modules=True,
        work_dir=tmp / "work",
        pair_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert report["ok"] is False
    assert report["summary"]["blocked_environment_count"] == 3
    assert report["summary"]["executed_replay_count"] == 0
    assert calls == []
    assert not (tmp / "work").exists()


def test_determinism_orchestration_unsafe_effects_and_placeholders_never_run(tmp):
    root = tmp / "repo"
    root.mkdir(parents=True)
    unsafe_plan = {
        "schema_version": 1,
        "tool": "skill-manager.determinism-check",
        "mode": "all",
        "deep": False,
        "ok": False,
        "status": "blocked",
        "summary": {},
        "modules": [],
        "warnings": [],
        "commands": [
            {
                "module_path": ".agents/skills/demo/module.json",
                "module_id": "demo",
                "kind": "skill",
                "command_id": "unsafe",
                "argv": ["python", "-B", "unsafe.py"],
                "timeout_seconds": 30,
                "working_directory": "repository",
                "declared_effects": ["network"],
                "selection_source": "fixture",
                "status": "planned",
                "ok": True,
                "issues": [],
                "determinism": {
                    "allowed_temporary_effects": [],
                    "volatile_json_pointers": [],
                    "environment_requirements": {
                        "minimum_python": "3.12",
                        "executables": [],
                        "platforms": ["windows", "linux", "macos"],
                    },
                },
            },
            {
                "module_path": ".agents/skills/demo/module.json",
                "module_id": "demo",
                "kind": "skill",
                "command_id": "placeholder",
                "argv": ["python", "tool.py", "<request>"],
                "timeout_seconds": 30,
                "working_directory": "repository",
                "declared_effects": [],
                "selection_source": "fixture",
                "status": "blocked-placeholder",
                "ok": False,
                "issues": ["argv contains an unresolved placeholder"],
                "determinism": {},
            },
        ],
    }
    calls: list[object] = []
    with patch.object(repo_determinism, "build_plan", return_value=unsafe_plan):
        report = repo_determinism.run_determinism_check(
            root,
            all_modules=True,
            work_dir=tmp / "work",
            pair_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

    assert report["summary"]["blocked_effect_count"] == 1
    assert report["summary"]["blocked_placeholder_count"] == 1
    assert report["summary"]["executed_replay_count"] == 0
    assert calls == []


def test_determinism_orchestration_blocked_only_selection_never_builds_seed(tmp):
    root = tmp / "repo"
    write_json(
        module_path(root, "blocked"),
        manifest("blocked", [command("networked", effects=["network"])]),
    )

    with patch.object(
        repo_determinism,
        "build_isolated_seed",
        side_effect=AssertionError("blocked selection must not build a seed"),
    ):
        report = repo_determinism.run_determinism_check(
            root,
            all_modules=True,
            work_dir=tmp / "work",
        )

    assert report["summary"]["blocked_module_count"] == 1, report
    assert report["summary"]["executed_replay_count"] == 0, report
    assert not (tmp / "work").exists()


def test_determinism_orchestration_executes_each_eligible_command_twice(tmp):
    root = tmp / "repo"
    write_json(module_path(root, "demo"), manifest("demo", [command("first"), command("second")]))
    calls: list[str] = []

    def fake_pair(_root, _module_path, command_spec, _work_dir):
        calls.append(str(command_spec["id"]))
        return pair()

    report = repo_determinism.run_determinism_check(
        root,
        all_modules=True,
        work_dir=tmp / "work",
        pair_runner=fake_pair,
    )

    assert report["ok"] is True, report
    assert calls == ["first", "second"]
    assert report["summary"]["executed_command_count"] == 2
    assert report["summary"]["executed_replay_count"] == 4
    assert report["summary"]["passed_count"] == 2
    assert not (tmp / "work").exists()


def test_determinism_orchestration_builds_one_shared_seed_for_multiple_commands(tmp):
    root = tmp / "repo"
    write_json(module_path(root, "demo"), manifest("demo", [command("first"), command("second")]))
    seed_builds: list[tuple[Path, Path]] = []
    pair_seeds: list[Path] = []

    def fake_seed(source_root, seed_root):
        seed_builds.append((source_root, seed_root))
        seed_root.mkdir(parents=True)
        return {"ok": True, "status": "ready", "seed_root": str(seed_root)}

    def fake_pair(
        _source_root,
        _module_path,
        _command_spec,
        _work_dir,
        *,
        prepared_seed=None,
        prepared_seed_report=None,
    ):
        assert prepared_seed_report["ok"] is True
        pair_seeds.append(prepared_seed)
        return pair()

    with (
        patch.object(repo_determinism, "build_isolated_seed", side_effect=fake_seed),
        patch.object(repo_determinism, "capture_replay_pair", side_effect=fake_pair),
    ):
        report = repo_determinism.run_determinism_check(
            root,
            all_modules=True,
            work_dir=tmp / "work",
        )

    assert report["ok"] is True, report
    assert len(seed_builds) == 1, seed_builds
    assert len(pair_seeds) == 2, pair_seeds
    assert pair_seeds[0] == pair_seeds[1] == seed_builds[0][1]
    assert not (tmp / "work").exists()


def test_determinism_orchestration_cleanup_and_equal_nonzero_fail_gate(tmp):
    root = tmp / "repo"
    write_json(module_path(root, "demo"), manifest("demo", [command("check")]))
    cleanup = repo_determinism.run_determinism_check(
        root,
        all_modules=True,
        work_dir=tmp / "cleanup-work",
        pair_runner=lambda *_args, **_kwargs: pair(cleanup_ok=False),
    )
    nonzero = repo_determinism.run_determinism_check(
        root,
        all_modules=True,
        work_dir=tmp / "nonzero-work",
        pair_runner=lambda *_args, **_kwargs: pair(returncode=7),
    )

    assert cleanup["ok"] is False
    assert cleanup["summary"]["cleanup_failed_count"] == 1
    assert nonzero["ok"] is False
    assert nonzero["commands"][0]["repeatable"] is True
    assert nonzero["commands"][0]["status"] == "deterministic-command-failure"


def test_determinism_orchestration_changed_selection_and_empty_success(tmp):
    root = tmp / "repo"
    write_json(module_path(root, "demo"), manifest("demo", [command("check")]))
    calls: list[str] = []
    changed = repo_determinism.run_determinism_check(
        root,
        changed=True,
        changed_paths=[".agents/skills/demo/scripts/check.py"],
        work_dir=tmp / "changed-work",
        pair_runner=lambda *_args, **_kwargs: (calls.append("called") or pair()),
    )
    empty = repo_determinism.run_determinism_check(
        root,
        changed=True,
        changed_paths=["docs/readme.md"],
        work_dir=tmp / "empty-work",
        pair_runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    assert changed["ok"] is True
    assert calls == ["called"]
    assert empty["ok"] is True
    assert empty["status"] == "empty-selection"
    assert empty["summary"]["executed_replay_count"] == 0


def test_determinism_orchestration_cli_xor_render_and_exit(tmp):
    parser = repo_cli_parser.build_parser()
    parsed_changed = parser.parse_args(["determinism-check", "--changed", "--summary", "--compact", "--format", "json"])
    parsed_all = parser.parse_args(["determinism-check", "--all", "--deep"])
    assert parsed_changed.changed is True and parsed_changed.all is False
    assert parsed_all.all is True and parsed_all.deep is True
    for invalid in (["determinism-check"], ["determinism-check", "--changed", "--all"]):
        try:
            parser.parse_args(invalid)
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("mutually exclusive target must be required")

    report = {
        "schema_version": 1,
        "tool": "skill-manager.determinism-check",
        "ok": True,
        "status": "passed",
        "mode": "all",
        "deep": False,
        "summary": {"command_count": 1, "passed_count": 1},
        "commands": [{"module_id": "demo", "command_id": "check", "status": "passed", "ok": True}],
        "warnings": [],
        "observation_boundary": {"network": "declared network effects are blocked"},
        "next_command": "none",
    }
    markdown = repo_determinism.render_markdown(report)
    compact = repo_determinism.summarize_report(report, compact=True)
    assert "# Determinism Check" in markdown
    assert compact["status"] == "passed"
    assert compact.get("commands", []) == []

    args = argparse.Namespace(
        changed=False,
        all=True,
        deep=False,
        summary=True,
        compact=True,
        output_format="json",
    )
    with patch.object(repo_determinism, "run_determinism_check", return_value=report):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = repo_determinism.determinism_check_command(args, tmp)
    assert exit_code == 0
    assert json.loads(output.getvalue())["ok"] is True


def test_determinism_public_cli_smoke_runs_only_against_temp_fixture(tmp):
    root = tmp / "fixture-repo"
    write_json(module_path(root, "demo"), manifest("demo", [command("stable")]))
    script = root / "scripts" / "stable.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text('import json\nprint(json.dumps({"ok": True}, sort_keys=True))\n', encoding="utf-8", newline="\n")
    for argv in (
        ["git", "init", "--quiet"],
        ["git", "config", "user.name", "Determinism CLI Fixture"],
        ["git", "config", "user.email", "fixture@example.invalid"],
        ["git", "add", "-A"],
        ["git", "commit", "--quiet", "-m", "fixture"],
    ):
        completed = subprocess.run(
            argv,
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr

    repo_root = Path(__file__).resolve().parents[5]
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(repo_root / ".agents" / "manage.py"),
            "determinism-check",
            "--root",
            str(root),
            "--all",
            "--summary",
            "--compact",
            "--format",
            "json",
        ],
        cwd=repo_root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["tool"] == "skill-manager.determinism-check"
    assert report["status"] == "passed", report
    assert report["summary"]["executed_command_count"] == 1
    assert report["summary"]["executed_replay_count"] == 2
    assert report["summary"]["passed_count"] == 1
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert status.returncode == 0, status.stderr
    assert status.stdout == ""
