"""Temporary-fixture tests for isolated determinism replay capture."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

from repo_support import repo_determinism


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def init_repo(root: Path) -> None:
    commands = (
        ["git", "init", "--quiet"],
        ["git", "config", "user.name", "Determinism Fixture"],
        ["git", "config", "user.email", "fixture@example.invalid"],
        ["git", "add", "-A"],
        ["git", "commit", "--quiet", "-m", "fixture"],
    )
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr


def command(script: str, *, timeout: int = 10, working_directory: str = "repository") -> dict[str, object]:
    return {
        "id": Path(script).stem,
        "argv": ["python", "-B", script],
        "timeout_seconds": timeout,
        "working_directory": working_directory,
        "effects": [],
    }


def create_source(root: Path) -> None:
    write_text(root / ".gitignore", "*.ignored\n")
    write_text(root / "tracked.txt", "before\n")
    write_text(root / "scripts/success.py", 'print("success")\n')
    write_text(root / "scripts/fail.py", 'print("failed")\nraise SystemExit(7)\n')
    write_text(root / "scripts/timeout.py", "import time\ntime.sleep(30)\n")
    write_text(
        root / "scripts/child_timeout.py",
        (
            "import os, subprocess, sys, time\n"
            'marker = os.path.join(os.environ["HOME"], "child-survived.txt")\n'
            "subprocess.Popen([sys.executable, '-B', '-c', "
            '"import pathlib,sys,time;time.sleep(2);pathlib.Path(sys.argv[1]).write_text(\\\"alive\\\")", marker])\n'
            "time.sleep(30)\n"
        ),
    )
    write_text(
        root / "scripts/snapshot.py",
        (
            "import os\n"
            "from pathlib import Path\n"
            'Path("tracked.txt").write_text("after\\n", encoding="utf-8")\n'
            'Path("new.txt").write_text("new\\n", encoding="utf-8")\n'
            'Path("cache.ignored").write_text("ignored\\n", encoding="utf-8")\n'
            'Path("empty-dir").mkdir(exist_ok=True)\n'
            'Path(os.environ["TMP"], "temp.txt").write_text("temp\\n", encoding="utf-8")\n'
            'Path(os.environ["HOME"], "home.txt").write_text("home\\n", encoding="utf-8")\n'
        ),
    )
    write_text(root / "automations/demo/runs/protected/run.json", "protected\n")
    write_text(root / ".superpowers/sdd/review.diff", "scratch\n")
    write_text(root / ".agents/local-ai/cache/index.json", "cache\n")
    write_text(root / ".agents/local-ai/secrets.local.json", "secret\n")
    write_text(root / ".agents/harness-install.json", "install evidence\n")
    write_text(root / ".agents/harness.lock.json", "lock evidence\n")
    init_repo(root)


def test_determinism_isolation_seed_excludes_state_and_uses_private_git(tmp):
    source = tmp / "source"
    seed = tmp / "seed"
    create_source(source)

    report = repo_determinism.build_isolated_seed(source, seed)

    assert report["ok"] is True, report
    assert (seed / ".git").is_dir()
    assert not (seed / "automations/demo/runs/protected/run.json").exists()
    assert not (seed / ".superpowers/sdd/review.diff").exists()
    assert not (seed / ".agents/local-ai/cache/index.json").exists()
    assert not (seed / ".agents/local-ai/secrets.local.json").exists()
    assert not (seed / ".agents/harness-install.json").exists()
    assert not (seed / ".agents/harness.lock.json").exists()
    assert report["excluded_count"] >= 5


def test_determinism_isolation_source_mutation_cannot_change_seed_replay(tmp):
    source = tmp / "source"
    seed = tmp / "seed"
    replay = tmp / "replay"
    create_source(source)
    write_text(source / "scripts/value.py", 'print("seed-value")\n')
    subprocess.run(["git", "add", "-A"], cwd=source, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "value"], cwd=source, check=True)
    assert repo_determinism.build_isolated_seed(source, seed)["ok"] is True
    write_text(source / "scripts/value.py", 'print("live-value")\n')

    capture = repo_determinism.capture_replay(
        seed,
        ".agents/skills/demo/module.json",
        command("scripts/value.py"),
        replay,
    )

    assert capture["capture_ok"] is True, capture
    assert capture["stdout_text"].strip() == "seed-value"
    assert "live-value" not in capture["stdout_text"]


def test_determinism_isolation_pair_has_independent_clones_and_cleans(tmp):
    source = tmp / "source"
    work = tmp / "work"
    create_source(source)

    report = repo_determinism.capture_replay_pair(
        source,
        ".agents/skills/demo/module.json",
        command("scripts/success.py"),
        work,
    )

    assert report["ok"] is True, report
    assert report["fixture_isolation"]["independent_git_directories"] is True
    assert report["fixture_isolation"]["git_directories"] == [
        "run-a/repository/.git",
        "run-b/repository/.git",
    ]
    assert report["runs"][0]["returncode"] == 0
    assert report["runs"][1]["returncode"] == 0
    assert report["cleanup"]["ok"] is True
    assert not work.exists()

    failure_work = tmp / "failure-work"
    failure_report = repo_determinism.capture_replay_pair(
        source,
        ".agents/skills/demo/module.json",
        command("scripts/fail.py"),
        failure_work,
    )
    assert [row["returncode"] for row in failure_report["runs"]] == [7, 7]
    assert failure_report["cleanup"]["ok"] is True
    assert not failure_work.exists()


def test_determinism_pair_rejects_mismatched_prepared_seed_report(tmp):
    source = tmp / "source"
    prepared_seed = tmp / "shared-seed"
    (prepared_seed / ".git").mkdir(parents=True)
    source.mkdir()

    with patch.object(
        repo_determinism,
        "capture_replay",
        side_effect=AssertionError("invalid prepared seed must not be cloned"),
    ):
        report = repo_determinism.capture_replay_pair(
            source,
            ".agents/skills/demo/module.json",
            command("scripts/success.py"),
            tmp / "work",
            prepared_seed=prepared_seed,
            prepared_seed_report={
                "ok": True,
                "status": "ready",
                "source_root": str(source.resolve()),
                "seed_root": str((tmp / "different-seed").resolve()),
                "private_git_directory": True,
            },
        )

    assert report["ok"] is False, report
    assert report["status"] == "seed-failed", report
    assert any("recorded seed_root" in issue for issue in report["seed"]["issues"]), report
    assert report["runs"] == []
    assert report["cleanup"]["ok"] is True


def test_determinism_replay_clone_enables_windows_longpaths(tmp):
    seed = tmp / "seed"
    seed.mkdir()
    captured: list[list[str]] = []

    def fake_git(_cwd, arguments, *, timeout_seconds=120, environment=None):
        _ = timeout_seconds, environment
        captured.append(list(arguments))
        return {"ok": False, "returncode": 128, "stdout": b"", "stderr": b"fixture stop"}

    with patch.object(repo_determinism, "_run_git", side_effect=fake_git):
        report = repo_determinism.capture_replay(
            seed,
            ".agents/skills/demo/module.json",
            command("scripts/success.py"),
            tmp / "replay",
        )

    assert report["status"] == "clone-failed", report
    assert captured == [
        [
            "-c",
            "core.longpaths=true",
            "clone",
            "--no-local",
            "--quiet",
            str(seed.resolve()),
            str((tmp / "replay" / "repository").resolve()),
        ]
    ]


def test_determinism_capture_summary_normalizes_clone_failure_without_paths(tmp):
    _ = tmp
    summary = repo_determinism._command_capture_summary(
        {
            "capture_ok": False,
            "status": "clone-failed",
            "returncode": None,
            "stderr_text": (
                "error: unable to create file C:/private/workspace/secret-name.md: Filename too long\n"
                "fatal: unable to checkout working tree\n"
            ),
        }
    )

    assert summary["failure_reason"] == "git-clone-filename-too-long", summary
    assert "private" not in json.dumps(summary).lower(), summary
    assert "secret-name" not in json.dumps(summary).lower(), summary


def test_determinism_isolation_captures_nonzero_timeout_and_kills_child(tmp):
    source = tmp / "source"
    seed = tmp / "seed"
    create_source(source)
    assert repo_determinism.build_isolated_seed(source, seed)["ok"] is True

    failed = repo_determinism.capture_replay(
        seed,
        ".agents/skills/demo/module.json",
        command("scripts/fail.py"),
        tmp / "failed",
    )
    timed_out = repo_determinism.capture_replay(
        seed,
        ".agents/skills/demo/module.json",
        command("scripts/child_timeout.py", timeout=1),
        tmp / "timed-out",
    )
    marker = tmp / "timed-out" / "home" / "child-survived.txt"
    time.sleep(2.5)

    assert failed["capture_ok"] is True
    assert failed["returncode"] == 7
    assert failed["stdout_text"].strip() == "failed"
    assert timed_out["capture_ok"] is False
    assert timed_out["timed_out"] is True
    assert timed_out["process_cleanup"]["ok"] is True
    assert marker.exists() is False


def test_determinism_isolation_snapshots_git_classes_empty_dirs_temp_and_home(tmp):
    source = tmp / "source"
    seed = tmp / "seed"
    create_source(source)
    assert repo_determinism.build_isolated_seed(source, seed)["ok"] is True

    capture = repo_determinism.capture_replay(
        seed,
        ".agents/skills/demo/module.json",
        command("scripts/snapshot.py"),
        tmp / "snapshot",
    )
    post_repo = {row["path"]: row for row in capture["snapshots"]["after"]["repository"]["files"]}
    post_temp = {row["path"]: row for row in capture["snapshots"]["after"]["temporary"]["files"]}
    post_home = {row["path"]: row for row in capture["snapshots"]["after"]["home"]["files"]}

    assert post_repo["tracked.txt"]["git_state"] == "tracked"
    assert post_repo["new.txt"]["git_state"] == "untracked"
    assert post_repo["cache.ignored"]["git_state"] == "ignored"
    assert "empty-dir/" in capture["snapshots"]["after"]["repository"]["empty_directories"]
    assert post_temp["temp.txt"]["sha256"]
    assert post_home["home.txt"]["bytes"] >= 5


def test_determinism_isolation_working_directory_and_credential_environment(tmp):
    source = tmp / "source"
    seed = tmp / "seed"
    credential_name = "DEMO_" + "SECRET_" + "TOKEN"
    ambient_names = (
        "PYTHONPATH",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_ASKPASS",
        "SSH_AUTH_SOCK",
        "SYSTEM_ACCESS" + "TOKEN",
        "AZURE_DEVOPS_EXT_PAT",
        "ARM_CLIENT_" + "SECRET",
    )
    ambient_name_literals = ",".join(repr(name) for name in ambient_names)
    create_source(source)
    write_text(
        source / "scripts/environment.py",
        (
            "import json, os\n"
            "from pathlib import Path\n"
            "print(json.dumps({"
            "'cwd': Path.cwd().as_posix(), "
            "'home': os.environ['HOME'], "
            "'tmp': os.environ['TMP'], "
            f"'has_credential': {credential_name!r} in os.environ, "
            "'hashseed': os.environ.get('PYTHONHASHSEED')}))\n"
        ),
    )
    subprocess.run(["git", "add", "-A"], cwd=source, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "environment"], cwd=source, check=True)
    assert repo_determinism.build_isolated_seed(source, seed)["ok"] is True

    environment_command = command("scripts/environment.py", working_directory="temporary")
    environment_command["argv"] = [
        "python",
        "-B",
        "-c",
        (
            "import json,os;from pathlib import Path;"
            "print(json.dumps({'cwd':Path.cwd().as_posix(),'home':os.environ['HOME'],"
            f"'tmp':os.environ['TMP'],'has_credential':{credential_name!r} in os.environ,"
            "'ambient_names':sorted(name for name in os.environ if name in {"
            f"{ambient_name_literals}"
            "}),"
            "'hashseed':os.environ.get('PYTHONHASHSEED')}))"
        ),
    ]
    injected = {
        name: f"dummy-{index}-must-not-leak"
        for index, name in enumerate((credential_name, *ambient_names), start=1)
    }
    with patch.dict(os.environ, injected):
        capture = repo_determinism.capture_replay(
            seed,
            ".agents/skills/demo/module.json",
            environment_command,
            tmp / "environment",
        )
    assert capture["returncode"] == 0, {
        "status": capture["status"],
        "stderr": capture["stderr_text"],
    }
    payload = json.loads(capture["stdout_text"])

    assert payload["cwd"].endswith("/temporary/workspace")
    assert payload["home"].endswith("/environment/home")
    assert payload["tmp"].endswith("/environment/temporary")
    assert payload["has_credential"] is False
    assert payload["ambient_names"] == []
    assert payload["hashseed"] == "0"
    assert credential_name in capture["environment"]["removed_credential_names"]
    assert set(injected).issubset(capture["environment"]["removed_environment_names"])
    serialized = json.dumps(capture)
    assert all(value not in serialized for value in injected.values())

    module_command = command("scripts/environment.py", working_directory="module")
    module_command["argv"] = [
        "python",
        "-B",
        "-c",
        "from pathlib import Path;print(Path.cwd().as_posix())",
    ]
    module_capture = repo_determinism.capture_replay(
        seed,
        "scripts/module.json",
        module_command,
        tmp / "module-environment",
    )
    assert module_capture["stdout_text"].strip().endswith("/repository/scripts")


def test_determinism_isolation_timeout_parent_kill_fallback_never_confirms_tree_cleanup(tmp):
    marker = tmp / "child-survived.txt"
    ready = tmp / "child-started.txt"
    child_code = "import pathlib,sys,time;time.sleep(1);pathlib.Path(sys.argv[1]).write_text('alive')"
    parent_code = (
        "import pathlib,subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-B','-c',{child_code!r},sys.argv[1]]);"
        "pathlib.Path(sys.argv[2]).write_text('ready');"
        "time.sleep(30)"
    )
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", parent_code, str(marker), str(ready)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **repo_determinism._process_group_kwargs(),
    )
    try:
        ready_deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < ready_deadline:
            time.sleep(0.05)
        assert ready.exists(), "fixture parent must spawn its child before forced cleanup"
        if os.name == "nt":
            forced = subprocess.CompletedProcess(
                ["taskkill"],
                returncode=1,
                stdout=b"",
                stderr=b"forced taskkill failure",
            )
            group_failure = patch.object(repo_determinism.subprocess, "run", return_value=forced)
        else:
            group_failure = patch.object(
                repo_determinism.os,
                "killpg",
                side_effect=OSError("forced process-group failure"),
            )
        with group_failure:
            cleanup = repo_determinism._terminate_process_group(process)
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)

        assert marker.exists(), "fixture child must survive a parent-only fallback"
        assert cleanup["ok"] is False, cleanup
        assert cleanup["process_tree_termination_confirmed"] is False, cleanup
        assert cleanup["method"] == "process-kill-fallback", cleanup
        assert cleanup.get("issue"), cleanup
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_determinism_isolation_timeout_orphan_pipe_drain_is_bounded(tmp):
    source = tmp / "source"
    seed = tmp / "seed"
    replay = tmp / "held-pipes"
    create_source(source)
    child_code = (
        "import os,time\n"
        "from pathlib import Path\n"
        "stop = Path(os.environ['HOME']) / 'stop-orphan.txt'\n"
        "exited = Path(os.environ['HOME']) / 'orphan-exited.txt'\n"
        "deadline = time.monotonic() + 8\n"
        "while not stop.exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.05)\n"
        "exited.write_text('exited', encoding='utf-8')\n"
    )
    write_text(
        source / "scripts/held_pipes.py",
        (
            "import subprocess,sys,time\n"
            f"child_code = {child_code!r}\n"
            "subprocess.Popen(\n"
            "    [sys.executable, '-B', '-c', child_code],\n"
            "    stdin=subprocess.DEVNULL,\n"
            "    stdout=sys.stdout.buffer,\n"
            "    stderr=sys.stderr.buffer,\n"
            "    close_fds=False,\n"
            ")\n"
            "time.sleep(30)\n"
        ),
    )
    subprocess.run(["git", "add", "-A"], cwd=source, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "held pipes"], cwd=source, check=True)
    assert repo_determinism.build_isolated_seed(source, seed)["ok"] is True

    def parent_only_cleanup(process):
        process.kill()
        return {
            "attempted": True,
            "ok": False,
            "method": "test-parent-only-fallback",
            "process_tree_termination_confirmed": False,
            "parent_termination_confirmed": True,
            "issue": "forced process-tree cleanup failure",
        }

    started = time.monotonic()
    with patch.object(
        repo_determinism,
        "_terminate_process_group",
        side_effect=parent_only_cleanup,
    ):
        capture = repo_determinism.capture_replay(
            seed,
            ".agents/skills/demo/module.json",
            command("scripts/held_pipes.py", timeout=1),
            replay,
        )
    elapsed = time.monotonic() - started
    write_text(replay / "home/stop-orphan.txt", "stop\n")
    orphan_exit_deadline = time.monotonic() + 2
    while (
        not (replay / "home/orphan-exited.txt").exists()
        and time.monotonic() < orphan_exit_deadline
    ):
        time.sleep(0.05)

    assert capture["timed_out"] is True, capture
    assert capture["process_cleanup"]["ok"] is False, capture["process_cleanup"]
    assert (replay / "home/orphan-exited.txt").exists()
    assert elapsed < 4, f"post-timeout pipe drain was not bounded: {elapsed:.2f}s"


def test_determinism_isolation_reparse_source_blocks_and_cleanup_failure_reports(tmp):
    source = tmp / "source"
    seed = tmp / "seed"
    source.mkdir(parents=True)
    actual = source / "actual"
    write_text(actual / "payload.txt", "payload\n")
    linked = source / "linked"
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(linked), str(actual)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
    else:
        linked.symlink_to(actual, target_is_directory=True)
    try:
        blocked = repo_determinism.build_isolated_seed(
            source,
            seed,
            source_paths=["linked/payload.txt"],
        )
        assert blocked["ok"] is False
        assert blocked["unsafe_paths"]
    finally:
        if os.name == "nt" and linked.exists():
            os.rmdir(linked)

    clean_source = tmp / "clean-source"
    create_source(clean_source)
    work = tmp / "cleanup-work"
    real_rmtree = shutil.rmtree
    with patch.object(repo_determinism.shutil, "rmtree", side_effect=OSError("cleanup denied")):
        cleanup_report = repo_determinism.capture_replay_pair(
            clean_source,
            ".agents/skills/demo/module.json",
            command("scripts/fail.py"),
            work,
        )
    assert cleanup_report["ok"] is False
    assert cleanup_report["cleanup"]["ok"] is False
    assert "cleanup denied" in cleanup_report["cleanup"]["issue"]
    def make_writable_and_retry(function, path, _error):
        os.chmod(path, 0o700)
        function(path)

    real_rmtree(work, onexc=make_writable_and_retry)
