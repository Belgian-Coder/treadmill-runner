"""Focused A/B comparison and observed-effect tests."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import subprocess

from repo_support import repo_determinism


def encoded(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def capture(
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
    timed_out: bool = False,
    capture_ok: bool = True,
    changes: dict[str, list[dict[str, object]]] | None = None,
    artifacts: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "capture_ok": capture_ok,
        "status": "captured" if capture_ok else "timeout" if timed_out else "capture-failed",
        "returncode": returncode,
        "timed_out": timed_out,
        "stdout_base64": encoded(stdout),
        "stderr_base64": encoded(stderr),
        "changes": changes or {"repository": [], "temporary": [], "home": []},
        "artifacts": artifacts or {},
        "snapshots": {"before": {}, "after": {}},
    }


def spec(
    *,
    pointers: list[str] | None = None,
    allowed: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "replay_commands": ["replay"],
        "allowed_temporary_effects": allowed or [],
        "volatile_json_pointers": pointers or [],
        "environment_requirements": {
            "minimum_python": "3.12",
            "executables": [],
            "platforms": ["windows", "linux", "macos"],
        },
    }


def change(
    root: str,
    path: str,
    operation: str,
    digest: str,
    *,
    entry_type: str = "file",
    git_state: str = "external",
    executable: bool = False,
) -> dict[str, object]:
    return {
        "root": root,
        "path": path,
        "operation": operation,
        "entry_type": entry_type,
        "git_state": git_state,
        "executable": executable,
        "sha256": digest,
        "bytes": 1,
    }


def artifact(label: str, content: bytes) -> tuple[str, dict[str, object]]:
    return label, {
        "label": label,
        "content_base64": encoded(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def test_determinism_comparison_rfc6901_exact_redaction_and_order(tmp):
    first = b'{"a/b":{"~key":1},"timing":1,"z":0}'
    second = b'{"a/b":{"~key":9},"timing":2,"z":0}'
    canonical_first = repo_determinism.canonicalize_json_bytes(first, ["/a~1b/~0key", "/timing"])
    canonical_second = repo_determinism.canonicalize_json_bytes(second, ["/a~1b/~0key", "/timing"])

    assert canonical_first["ok"] is True
    assert canonical_first["content"] == canonical_second["content"]
    assert canonical_first["applications"] == ["/a~1b/~0key", "/timing"]

    verdict = repo_determinism.compare_replay_captures(
        capture(stdout=first),
        capture(stdout=second),
        spec(pointers=["/a~1b/~0key", "/timing"]),
    )
    reordered = repo_determinism.compare_replay_captures(
        capture(stdout=first),
        capture(stdout=b'{"z":0,"a/b":{"~key":9},"timing":2}'),
        spec(pointers=["/a~1b/~0key", "/timing"]),
    )

    assert verdict["ok"] is True, verdict
    assert reordered["ok"] is False
    assert "stdout" in reordered["mismatches"]


def test_determinism_comparison_pointer_missing_one_sided_and_unapplied_fail(tmp):
    missing = repo_determinism.compare_replay_captures(
        capture(stdout=b'{"value":1}'),
        capture(stdout=b'{"value":1}'),
        spec(pointers=["/missing"]),
    )
    one_sided = repo_determinism.compare_replay_captures(
        capture(stdout=b'{"volatile":1,"value":1}'),
        capture(stdout=b'{"value":1}'),
        spec(pointers=["/volatile"]),
    )

    assert missing["ok"] is False
    assert "volatile-pointer-unapplied:/missing" in missing["mismatches"]
    assert one_sided["ok"] is False
    assert "volatile-pointer-application:/volatile" in one_sided["mismatches"]


def test_determinism_comparison_raw_stream_exit_timeout_random_and_paths_fail(tmp):
    cases = [
        (capture(stdout=b"random-a"), capture(stdout=b"random-b"), "stdout"),
        (capture(stderr=b"error-a"), capture(stderr=b"error-b"), "stderr"),
        (capture(returncode=0), capture(returncode=2), "exit-code"),
        (capture(stdout=b"C:/fixture/a"), capture(stdout=b"C:/fixture/b"), "stdout"),
    ]
    for first, second, mismatch in cases:
        verdict = repo_determinism.compare_replay_captures(first, second, spec())
        assert verdict["ok"] is False
        assert mismatch in verdict["mismatches"]

    timeout = repo_determinism.compare_replay_captures(
        capture(timed_out=True, capture_ok=False, returncode=-9),
        capture(timed_out=True, capture_ok=False, returncode=-9),
        spec(),
    )
    assert timeout["ok"] is False
    assert timeout["status"] == "timeout"


def test_determinism_comparison_equal_nonzero_is_repeatable_but_fails(tmp):
    verdict = repo_determinism.compare_replay_captures(
        capture(stdout=b"same", returncode=7),
        capture(stdout=b"same", returncode=7),
        spec(),
    )

    assert verdict["repeatable"] is True
    assert verdict["command_succeeded"] is False
    assert verdict["ok"] is False
    assert verdict["status"] == "deterministic-command-failure"


def test_determinism_comparison_repository_changes_fail_even_when_identical(tmp):
    rows = {
        "repository": [
            change("repository", "tracked.txt", "modify", "a" * 64, git_state="tracked"),
            change("repository", "new.txt", "create", "b" * 64, git_state="untracked"),
            change("repository", "cache.ignored", "create", "c" * 64, git_state="ignored"),
            change("repository", "deleted.txt", "delete", "d" * 64, git_state="tracked"),
            change("repository", "empty/", "create", "", entry_type="directory"),
        ],
        "temporary": [],
        "home": [],
    }
    verdict = repo_determinism.compare_replay_captures(
        capture(changes=rows),
        capture(changes=rows),
        spec(),
    )

    assert verdict["repeatable"] is True
    assert verdict["ok"] is False
    assert "repository_write" in verdict["observed_effects"]
    assert "repository_write" in verdict["undeclared_effects"]
    assert verdict["status"] == "undeclared-effects"


def test_determinism_comparison_manifest_and_artifact_hash_mismatches_fail(tmp):
    first_change = change("repository", "result.json", "create", "a" * 64, git_state="untracked")
    second_change = change("repository", "result.json", "create", "b" * 64, git_state="untracked")
    first_artifacts = dict([artifact("repository:result.json", b'{"value":1}')])
    second_artifacts = dict([artifact("repository:result.json", b'{"value":2}')])
    first = capture(
        changes={"repository": [first_change], "temporary": [], "home": []},
        artifacts=first_artifacts,
    )
    second = capture(
        changes={"repository": [second_change], "temporary": [], "home": []},
        artifacts=second_artifacts,
    )

    verdict = repo_determinism.compare_replay_captures(first, second, spec())

    assert verdict["repeatable"] is False
    assert "filesystem:repository" in verdict["mismatches"]
    assert "artifact:repository:result.json" in verdict["mismatches"]


def test_determinism_comparison_allowed_temp_identical_passes_random_fails(tmp):
    allowed = [
        {
            "path": "scratch/result.json",
            "recursive": False,
            "operations": ["create"],
        }
    ]
    row = change("temporary", "scratch/result.json", "create", "a" * 64)
    artifacts = dict([artifact("temporary:scratch/result.json", b'{"ok":true}')])
    identical = repo_determinism.compare_replay_captures(
        capture(changes={"repository": [], "temporary": [row], "home": []}, artifacts=artifacts),
        capture(changes={"repository": [], "temporary": [row], "home": []}, artifacts=artifacts),
        spec(allowed=allowed),
    )
    random = repo_determinism.compare_replay_captures(
        capture(changes={"repository": [], "temporary": [row], "home": []}, artifacts=artifacts),
        capture(
            changes={
                "repository": [],
                "temporary": [change("temporary", "scratch/random.json", "create", "a" * 64)],
                "home": [],
            },
            artifacts=dict([artifact("temporary:scratch/random.json", b'{"ok":true}')]),
        ),
        spec(allowed=allowed),
    )

    assert identical["ok"] is True, identical
    assert identical["observed_effects"] == ["temporary_write"]
    assert identical["undeclared_effects"] == []
    assert random["ok"] is False
    assert "filesystem:temporary" in random["mismatches"]


def test_determinism_comparison_json_artifact_pointer_and_home_effect(tmp):
    allowed = [
        {
            "path": "result.json",
            "recursive": False,
            "operations": ["create"],
        }
    ]
    row = change("temporary", "result.json", "create", "a" * 64)
    first_artifact = dict([artifact("temporary:result.json", b'{"timestamp":"a","ok":true}')])
    second_artifact = dict([artifact("temporary:result.json", b'{"timestamp":"b","ok":true}')])
    canonical = repo_determinism.compare_replay_captures(
        capture(changes={"repository": [], "temporary": [row], "home": []}, artifacts=first_artifact),
        capture(changes={"repository": [], "temporary": [row], "home": []}, artifacts=second_artifact),
        spec(pointers=["/timestamp"], allowed=allowed),
    )
    undeclared_timestamp = repo_determinism.compare_replay_captures(
        capture(changes={"repository": [], "temporary": [row], "home": []}, artifacts=first_artifact),
        capture(changes={"repository": [], "temporary": [row], "home": []}, artifacts=second_artifact),
        spec(allowed=allowed),
    )
    home_row = change("home", "profile.json", "create", "f" * 64)
    home = repo_determinism.compare_replay_captures(
        capture(changes={"repository": [], "temporary": [], "home": [home_row]}),
        capture(changes={"repository": [], "temporary": [], "home": [home_row]}),
        spec(),
    )

    assert canonical["ok"] is True, canonical
    assert undeclared_timestamp["ok"] is False
    assert "artifact:temporary:result.json" in undeclared_timestamp["mismatches"]
    assert home["repeatable"] is True
    assert home["ok"] is False
    assert "external_write" in home["undeclared_effects"]


def test_determinism_comparison_special_or_reparse_capture_fails(tmp):
    unsafe = capture(capture_ok=False)
    unsafe["status"] = "capture-failed"
    unsafe["snapshots"] = {
        "before": {},
        "after": {
            "repository": {
                "ok": False,
                "unsafe_paths": [{"path": "link", "reason": "reparse"}],
            }
        },
    }
    verdict = repo_determinism.compare_replay_captures(unsafe, unsafe, spec())

    assert verdict["ok"] is False
    assert verdict["status"] == "capture-failed"


def test_determinism_comparison_captured_json_artifact_integration(tmp):
    source = tmp / "source"
    script = source / "scripts/write_temp.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        (
            "import json, os\n"
            "from pathlib import Path\n"
            'target = Path(os.environ["TMP"]) / "result.json"\n'
            "target.write_text(json.dumps({'timestamp': os.environ['HOME'], 'ok': True}), encoding='utf-8')\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    for git_args in (
        ["init", "--quiet"],
        ["config", "user.name", "Fixture"],
        ["config", "user.email", "fixture@example.invalid"],
        ["add", "-A"],
        ["commit", "--quiet", "-m", "fixture"],
    ):
        completed = subprocess.run(
            ["git", *git_args],
            cwd=source,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
    pair = repo_determinism.capture_replay_pair(
        source,
        ".agents/skills/demo/module.json",
        {
            "id": "write-temp",
            "argv": ["python", "-B", "scripts/write_temp.py"],
            "timeout_seconds": 10,
            "working_directory": "repository",
            "effects": [],
        },
        tmp / "work",
    )
    verdict = repo_determinism.compare_replay_captures(
        pair["runs"][0],
        pair["runs"][1],
        spec(
            pointers=["/timestamp"],
            allowed=[
                {
                    "path": "result.json",
                    "recursive": False,
                    "operations": ["create"],
                }
            ],
        ),
    )

    assert pair["cleanup"]["ok"] is True
    assert verdict["ok"] is True, verdict
    assert verdict["generated_artifact_hashes"][0]["match"] is True
