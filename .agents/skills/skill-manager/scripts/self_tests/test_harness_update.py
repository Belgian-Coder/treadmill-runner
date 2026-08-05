"""Transactional, archive-safety, and portable-lock harness update tests."""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import zipfile
from argparse import Namespace
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import repo_manager
from repo_support import repo_harness_install
from repo_support import repo_harness_update
from repo_support import repo_cost_policy
from repo_support import repo_policy


REPOSITORY = "https://github.com/Belgian-Coder/skills"


class TtyBuffer(StringIO):
    def isatty(self) -> bool:
        return True


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, value: object) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def payload_manifest() -> dict[str, object]:
    return {
        "schema_version": 2,
        "tool": "install-harness-payload",
        "owner": "skill-manager",
        "include_roots": ["AGENTS.md", ".agents", "docs"],
        "exclude_globs": list(repo_harness_install.REQUIRED_GENERAL_EXCLUDES),
        "state_exclude_globs": list(repo_harness_install.REQUIRED_STATE_EXCLUDES),
        "required_features": ["core"],
        "feature_bundles": {
            "core": {"include_globs": ["AGENTS.md", ".agents/**", "docs/**"], "requires": []},
        },
        "profiles": {"minimal": {"features": ["core"], "exclude_features": []}},
    }


def source_tree(root: Path, *, agents: str, include_old: bool, include_new: bool) -> Path:
    source = root
    write_text(source / "AGENTS.md", agents)
    write_json(source / ".agents/harness-payload.json", payload_manifest())
    write_text(source / ".agents/manage.py", "print('fixture manager')\n")
    if include_old:
        write_text(source / "docs/old.md", "old\n")
    if include_new:
        write_text(source / "docs/new.md", "new\n")
    return source


def set_release_lock(target: Path, source: Path, *, tag: str, commit: str) -> tuple[str, list[dict[str, object]]]:
    lock = repo_harness_install.read_install_manifest(target)
    _files, selected, rows, digest = repo_harness_update.selected_payload(source, lock)
    release_lock = repo_harness_update.make_lock(
        repository=REPOSITORY,
        tag=tag,
        commit=commit,
        selected_profile=selected,
        digest=digest,
        rows=rows,
    )
    write_json(target / repo_harness_update.LOCK_REL, release_lock)
    return digest, rows


def archive_for(tmp: Path, source: Path, *, tag: str, commit: str, digest: str) -> tuple[Path, Path]:
    archive = tmp / f"{tag}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                output.write(path, f"skills-{commit}/{path.relative_to(source).as_posix()}")
    metadata = tmp / f"{tag}.metadata.json"
    write_json(metadata, {"repository": REPOSITORY, "tag": tag, "commit": commit, "payload_digest": digest})
    return archive, metadata


def installed_fixture(tmp: Path) -> tuple[Path, Path, Path, Path, Path]:
    source1 = source_tree(tmp / "source-v1", agents="v1\n", include_old=True, include_new=False)
    target = tmp / "consumer"
    report = repo_harness_install.install_harness_report(source1, target, profile="minimal")
    assert report["ok"], report
    commit1 = "1" * 40
    set_release_lock(target, source1, tag="v1.0.0", commit=commit1)

    source2 = source_tree(tmp / "source-v2", agents="v2\n", include_old=False, include_new=True)
    lock = repo_harness_install.read_install_manifest(target)
    _files, _selected, _rows, digest2 = repo_harness_update.selected_payload(source2, lock)
    archive2, metadata2 = archive_for(tmp, source2, tag="v1.1.0", commit="2" * 40, digest=digest2)
    return source1, source2, target, archive2, metadata2


def with_passing_setup_verification():
    previous = repo_harness_update.setup_verification
    repo_harness_update.setup_verification = lambda _root: {"ok": True, "status": "passed"}
    return previous


def setup_args(**overrides: object) -> Namespace:
    values = {
        "check": False,
        "dry_run": False,
        "offline": False,
        "output_format": "markdown",
        "doctor": False,
    }
    values.update(overrides)
    return Namespace(**values)


def minimal_setup_report() -> dict[str, object]:
    return {
        "schema_version": 1,
        "tool": "setup",
        "ok": True,
        "status": "ready",
        "actions": {},
        "skipped": [],
        "failures": [],
    }


def test_install_into_existing_git_project_leaves_git_tree_untouched(tmp: Path) -> None:
    source = source_tree(tmp / "source", agents="v1\n", include_old=True, include_new=False)
    write_text(source / ".git/objects/source-only", "must not copy\n")
    target = tmp / "consumer"
    write_text(target / ".git/HEAD", "ref: refs/heads/feature/app\n")
    write_text(target / ".git/config", "[core]\n\trepositoryformatversion = 0\n")
    before = {
        path.relative_to(target / ".git").as_posix(): path.read_bytes()
        for path in (target / ".git").rglob("*")
        if path.is_file()
    }

    report = repo_harness_install.install_harness_report(source, target, profile="minimal")

    after = {
        path.relative_to(target / ".git").as_posix(): path.read_bytes()
        for path in (target / ".git").rglob("*")
        if path.is_file()
    }
    assert report["ok"], report
    assert before == after
    assert "objects/source-only" not in after
    assert (target / repo_harness_update.LOCK_REL).is_file()


def test_interactive_setup_previews_then_defaults_to_declining_update(tmp: Path) -> None:
    write_text(tmp / repo_harness_update.LOCK_REL, "{}\n")
    calls: list[bool] = []

    def fake_update(_root: Path, *, requested: str, apply: bool) -> dict[str, object]:
        assert requested == "latest"
        calls.append(apply)
        return {
            "ok": True,
            "status": "preview",
            "updates": [],
            "additions": [],
            "deletions": [],
            "preserved": [],
            "collisions": [],
            "summary": {},
        }

    with (
        patch.object(repo_manager.sys, "stdin", TtyBuffer()),
        patch.object(repo_manager.sys, "stdout", TtyBuffer()),
        patch("builtins.input", return_value=""),
        patch.object(
            repo_manager.repo_harness_update,
            "status_report",
            return_value={"ok": True, "update_available": True, "status": "update-available"},
        ),
        patch.object(repo_manager.repo_harness_update, "update_report", side_effect=fake_update),
        patch.object(repo_manager.repo_harness_update, "print_report"),
        patch.object(repo_manager.repo_setup, "build_setup_report", return_value=minimal_setup_report()),
    ):
        report = repo_manager.build_setup_report(setup_args(), tmp)

    assert calls == [False]
    assert report["actions"]["harness_update"]["status"] == "declined"
    assert any("default No" in row for row in report["skipped"])


def test_interactive_setup_apply_is_bound_to_the_previewed_tag(tmp: Path) -> None:
    write_text(tmp / repo_harness_update.LOCK_REL, "{}\n")
    calls: list[tuple[str, bool]] = []

    def fake_update(
        _root: Path,
        *,
        requested: str,
        apply: bool,
        expected_commit: str | None = None,
        expected_payload_digest: str | None = None,
    ) -> dict[str, object]:
        calls.append((requested, apply, expected_commit, expected_payload_digest))
        if not apply:
            return {
                "ok": True,
                "status": "preview",
                "target": {"tag": "v1.1.0", "commit": "2" * 40, "payload_digest": "3" * 64},
                "updates": [],
                "additions": [],
                "deletions": [],
                "preserved": [],
                "collisions": [],
                "summary": {},
            }
        return {"ok": True, "status": "applied", "target": {"tag": requested}}

    with (
        patch.object(repo_manager.sys, "stdin", TtyBuffer()),
        patch.object(repo_manager.sys, "stdout", TtyBuffer()),
        patch("builtins.input", return_value="yes"),
        patch.object(
            repo_manager.repo_harness_update,
            "status_report",
            return_value={"ok": True, "update_available": True, "status": "update-available"},
        ),
        patch.object(repo_manager.repo_harness_update, "update_report", side_effect=fake_update),
        patch.object(repo_manager.repo_harness_update, "print_report"),
    ):
        report = repo_manager.build_setup_report(setup_args(), tmp)

    assert calls == [
        ("latest", False, None, None),
        ("v1.1.0", True, "2" * 40, "3" * 64),
    ]
    assert report["ok"] and report["status"] == "self-updated"


def test_noninteractive_check_offline_and_json_setup_never_check_upstream(tmp: Path) -> None:
    write_text(tmp / repo_harness_update.LOCK_REL, "{}\n")
    cases = (
        (setup_args(check=True), TtyBuffer(), TtyBuffer()),
        (setup_args(offline=True), TtyBuffer(), TtyBuffer()),
        (setup_args(output_format="json"), TtyBuffer(), TtyBuffer()),
        (setup_args(), StringIO(), TtyBuffer()),
    )
    for args, stdin, stdout in cases:
        with (
            patch.object(repo_manager.sys, "stdin", stdin),
            patch.object(repo_manager.sys, "stdout", stdout),
            patch.object(
                repo_manager.repo_harness_update,
                "status_report",
                side_effect=AssertionError("setup unexpectedly accessed upstream tags"),
            ),
            patch.object(repo_manager.repo_setup, "build_setup_report", return_value=minimal_setup_report()),
        ):
            report = repo_manager.build_setup_report(args, tmp)
        assert report["ok"]


def test_update_preview_apply_and_rollback_preserve_consumer_git(tmp: Path) -> None:
    _source1, _source2, target, archive, metadata = installed_fixture(tmp)
    write_text(target / ".git/HEAD", "ref: refs/heads/main\n")
    project_policy = repo_policy.default_policy_document()
    project_policy["limits"]["agents"]["warn_chars"] = 3600
    write_json(target / ".agents/project-policy.json", project_policy)
    write_json(target / ".agents/local-ai/project.settings.json", {"task_model_profiles": {}})
    write_json(target / ".agents/local-ai/local.settings.json", {"gpu": {"mode": "off"}})
    before_lock = (target / repo_harness_update.LOCK_REL).read_bytes()

    preview = repo_harness_update.update_report(
        target, requested="v1.1.0", apply=False, archive=str(archive), archive_metadata=str(metadata)
    )
    assert preview["ok"] and preview["status"] == "preview", preview
    assert preview["summary"] == {"updated": 1, "added": 1, "deleted": 1, "unchanged": 2, "preserved": 0, "collisions": 0}
    assert (target / "AGENTS.md").read_text(encoding="utf-8") == "v1\n"
    assert (target / repo_harness_update.LOCK_REL).read_bytes() == before_lock
    output = StringIO()
    with contextlib.redirect_stdout(output):
        repo_harness_update.print_report(preview, "markdown")
    rendered = output.getvalue()
    assert "Updated:" in rendered and "AGENTS.md" in rendered
    assert "Added:" in rendered and "docs/new.md" in rendered
    assert "Deleted:" in rendered and "docs/old.md" in rendered

    previous = with_passing_setup_verification()
    try:
        applied = repo_harness_update.update_report(
            target, requested="v1.1.0", apply=True, archive=str(archive), archive_metadata=str(metadata)
        )
    finally:
        repo_harness_update.setup_verification = previous
    assert applied["ok"] and applied["status"] == "applied", applied
    assert (target / "AGENTS.md").read_text(encoding="utf-8") == "v2\n"
    assert (target / "docs/new.md").is_file()
    assert not (target / "docs/old.md").exists()
    assert (target / ".git/HEAD").read_text(encoding="utf-8").endswith("main\n")
    assert json.loads((target / ".agents/project-policy.json").read_text(encoding="utf-8"))["limits"]["agents"]["warn_chars"] == 3600
    assert (target / ".agents/local-ai/project.settings.json").is_file()
    assert (target / ".agents/local-ai/local.settings.json").is_file()

    rolled_back = repo_harness_update.rollback_report(target, transaction=str(applied["transaction"]))
    assert rolled_back["ok"], rolled_back
    assert (target / "AGENTS.md").read_text(encoding="utf-8") == "v1\n"
    assert (target / "docs/old.md").is_file()
    assert not (target / "docs/new.md").exists()
    assert (target / repo_harness_update.LOCK_REL).read_bytes() == before_lock


def test_update_policy_migration_verifies_v2_postcondition_and_restores_noop_success(tmp: Path) -> None:
    root = tmp / "consumer"
    write_text(root / ".agents/manage.py", "raise SystemExit(0)\n")
    v1 = {
        "schema_version": 1,
        "limits": repo_policy.default_policy_document()["limits"],
        "warnings": repo_policy.default_policy_document()["warnings"],
        "commands": repo_policy.default_policy_document()["commands"],
        "cost_policy": repo_cost_policy.default_cost_policy(),
    }
    policy_path = root / repo_policy.PROJECT_POLICY_PATH
    write_json(policy_path, v1)
    identifier = "noop-migration"
    transaction_dir = root / repo_harness_update.TRANSACTIONS_REL / identifier
    transaction_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(policy_path, transaction_dir / "project-policy.json")
    write_json(
        transaction_dir / "transaction.json",
        {
            "schema_version": 1,
            "tool": "harness-update-transaction",
            "id": identifier,
            "status": "applied",
            "operations": [],
            "project_policy": {
                "path": repo_policy.PROJECT_POLICY_PATH,
                "pre_sha256": repo_harness_update.sha256_file(policy_path),
                "migrated": False,
            },
        },
    )

    report = repo_harness_update.migrate_project_policy_after_update(root, identifier)

    assert not report["ok"]
    assert report["status"] == "auto-restored"
    assert "Postcondition failed" in report["output"]
    assert json.loads(policy_path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_update_blocks_direct_edits_and_unknown_destinations_without_partial_apply(tmp: Path) -> None:
    _source1, _source2, target, archive, metadata = installed_fixture(tmp)
    write_text(target / "AGENTS.md", "consumer edit\n")
    write_text(target / "docs/new.md", "consumer-owned unknown destination\n")
    before_lock = (target / repo_harness_update.LOCK_REL).read_bytes()

    report = repo_harness_update.update_report(
        target, requested="v1.1.0", apply=True, archive=str(archive), archive_metadata=str(metadata)
    )

    assert not report["ok"] and report["status"] == "blocked", report
    assert {row["path"] for row in report["collisions"]} == {"AGENTS.md", "docs/new.md"}
    assert (target / "docs/old.md").is_file()
    assert (target / repo_harness_update.LOCK_REL).read_bytes() == before_lock


def test_project_overlay_transfers_a_managed_path_to_project_ownership(tmp: Path) -> None:
    _source1, _source2, target, archive, metadata = installed_fixture(tmp)
    write_text(target / "AGENTS.md", "project-owned overlay\n")
    write_json(
        target / repo_harness_update.PROJECT_OVERLAY_REL,
        {"schema_version": 1, "tool": "harness-project-overlay", "paths": ["AGENTS.md"]},
    )
    previous = with_passing_setup_verification()
    try:
        report = repo_harness_update.update_report(
            target, requested="v1.1.0", apply=True, archive=str(archive), archive_metadata=str(metadata)
        )
    finally:
        repo_harness_update.setup_verification = previous

    assert report["ok"] and report["status"] == "applied", report
    assert report["project_overlay"] == ["AGENTS.md"]
    assert report["preserved"] == ["AGENTS.md"]
    assert (target / "AGENTS.md").read_text(encoding="utf-8") == "project-owned overlay\n"
    lock = repo_harness_update.read_lock(target)
    assert "AGENTS.md" not in {str(row["path"]) for row in lock["files"]}


def test_project_overlay_rejects_unknown_missing_and_state_paths(tmp: Path) -> None:
    _source1, _source2, target, archive, metadata = installed_fixture(tmp)
    for declared_path, expected in (
        ("docs/not-managed.md", "not managed"),
        (repo_harness_update.LOCK_REL, "cannot claim harness state"),
    ):
        write_json(
            target / repo_harness_update.PROJECT_OVERLAY_REL,
            {"schema_version": 1, "tool": "harness-project-overlay", "paths": [declared_path]},
        )
        try:
            report = repo_harness_update.update_report(
                target, requested="v1.1.0", apply=False, archive=str(archive), archive_metadata=str(metadata)
            )
        except RuntimeError as exc:
            assert expected in str(exc)
        else:
            assert not report["ok"] and expected in str(report["collisions"]), report


def test_update_lock_is_portable_to_a_second_consumer_clone(tmp: Path) -> None:
    _source1, _source2, target, archive, metadata = installed_fixture(tmp)
    clone = tmp / "consumer-clone"
    shutil.copytree(target, clone)
    previous = with_passing_setup_verification()
    try:
        report = repo_harness_update.update_report(
            clone, requested="v1.1.0", apply=True, archive=str(archive), archive_metadata=str(metadata)
        )
    finally:
        repo_harness_update.setup_verification = previous
    assert report["ok"], report
    assert (clone / "AGENTS.md").read_text(encoding="utf-8") == "v2\n"


def test_rollback_blocks_files_changed_after_update(tmp: Path) -> None:
    _source1, _source2, target, archive, metadata = installed_fixture(tmp)
    previous = with_passing_setup_verification()
    try:
        report = repo_harness_update.update_report(
            target, requested="v1.1.0", apply=True, archive=str(archive), archive_metadata=str(metadata)
        )
    finally:
        repo_harness_update.setup_verification = previous
    write_text(target / "AGENTS.md", "post-update project edit\n")
    rollback = repo_harness_update.rollback_report(target, transaction=str(report["transaction"]))
    assert not rollback["ok"] and rollback["status"] == "blocked", rollback
    assert any("AGENTS.md" in row for row in rollback["collisions"])


def test_rollback_rejects_tampered_transaction_paths_without_outside_writes(tmp: Path) -> None:
    _source1, _source2, target, archive, metadata = installed_fixture(tmp)
    previous = with_passing_setup_verification()
    try:
        report = repo_harness_update.update_report(
            target, requested="v1.1.0", apply=True, archive=str(archive), archive_metadata=str(metadata)
        )
    finally:
        repo_harness_update.setup_verification = previous
    directory = repo_harness_update.transaction_path(target, str(report["transaction"]))
    transaction = json.loads((directory / "transaction.json").read_text(encoding="utf-8"))
    transaction["operations"][0]["path"] = "../outside.txt"
    write_json(directory / "transaction.json", transaction)
    outside = target.parent / "outside.txt"
    write_text(outside, "sentinel\n")

    try:
        repo_harness_update.rollback_report(target, transaction=str(report["transaction"]))
    except RuntimeError as exc:
        assert "unsafe transaction operation path" in str(exc)
    else:
        raise AssertionError("tampered rollback transaction was accepted")

    assert outside.read_text(encoding="utf-8") == "sentinel\n"
    assert (target / "AGENTS.md").read_text(encoding="utf-8") == "v2\n"


def test_interrupted_update_automatically_restores_payload_and_lock(tmp: Path) -> None:
    _source1, _source2, target, archive, metadata = installed_fixture(tmp)
    before_lock = (target / repo_harness_update.LOCK_REL).read_bytes()
    real_atomic_copy = repo_harness_update.atomic_copy
    calls = 0

    def fail_second_copy(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("fixture interruption")
        real_atomic_copy(source, destination)

    repo_harness_update.atomic_copy = fail_second_copy
    try:
        try:
            repo_harness_update.update_report(
                target, requested="v1.1.0", apply=True, archive=str(archive), archive_metadata=str(metadata)
            )
        except RuntimeError as exc:
            assert "automatically restored" in str(exc)
        else:
            raise AssertionError("interrupted update unexpectedly succeeded")
    finally:
        repo_harness_update.atomic_copy = real_atomic_copy
    assert (target / "AGENTS.md").read_text(encoding="utf-8") == "v1\n"
    assert (target / "docs/old.md").is_file()
    assert not (target / "docs/new.md").exists()
    assert (target / repo_harness_update.LOCK_REL).read_bytes() == before_lock


def test_local_archive_requires_matching_payload_digest(tmp: Path) -> None:
    _source1, _source2, target, archive, metadata = installed_fixture(tmp)
    metadata_payload = json.loads(metadata.read_text(encoding="utf-8"))
    metadata_payload["payload_digest"] = "0" * 64
    write_json(metadata, metadata_payload)
    try:
        repo_harness_update.update_report(
            target, requested="v1.1.0", apply=False, archive=str(archive), archive_metadata=str(metadata)
        )
    except RuntimeError as exc:
        assert "payload digest" in str(exc)
    else:
        raise AssertionError("mismatched local archive digest was accepted")


def test_local_archive_root_must_identify_metadata_commit(tmp: Path) -> None:
    _source1, _source2, target, archive, metadata = installed_fixture(tmp)
    metadata_payload = json.loads(metadata.read_text(encoding="utf-8"))
    metadata_payload["commit"] = "8" * 40
    write_json(metadata, metadata_payload)

    try:
        repo_harness_update.update_report(
            target, requested="v1.1.0", apply=False, archive=str(archive), archive_metadata=str(metadata)
        )
    except RuntimeError as exc:
        assert "does not identify resolved commit" in str(exc)
    else:
        raise AssertionError("archive whose root disagreed with metadata commit was accepted")


def test_apply_rejects_commit_or_payload_changed_after_preview(tmp: Path) -> None:
    _source1, _source2, target, archive, metadata = installed_fixture(tmp)
    metadata_payload = json.loads(metadata.read_text(encoding="utf-8"))
    cases = (
        {"expected_commit": "9" * 40},
        {"expected_payload_digest": "9" * 64},
    )
    for expectations in cases:
        try:
            repo_harness_update.update_report(
                target,
                requested="v1.1.0",
                apply=True,
                archive=str(archive),
                archive_metadata=str(metadata),
                **expectations,
            )
        except RuntimeError as exc:
            assert "preview" in str(exc)
        else:
            raise AssertionError("update accepted a target that changed after preview")
    assert (target / "AGENTS.md").read_text(encoding="utf-8") == "v1\n"
    assert metadata_payload["commit"] == "2" * 40


def test_upstream_status_blocks_a_moved_current_tag(tmp: Path) -> None:
    _source1, _source2, target, _archive, _metadata = installed_fixture(tmp)
    real_fetch = repo_harness_update.fetch_stable_tags
    real_resolve = repo_harness_update.resolve_annotated_tag
    repo_harness_update.fetch_stable_tags = lambda _repository: ["v1.1.0", "v1.0.0"]
    repo_harness_update.resolve_annotated_tag = lambda _repository, _tag: "9" * 40
    try:
        report = repo_harness_update.status_report(target, check_upstream=True)
    finally:
        repo_harness_update.fetch_stable_tags = real_fetch
        repo_harness_update.resolve_annotated_tag = real_resolve
    assert not report["ok"] and report["status"] == "moved-tag-blocked", report
    assert report["moved_tag"] is True


def test_safe_extract_rejects_traversal_and_symlink_members(tmp: Path) -> None:
    traversal = tmp / "traversal.zip"
    with zipfile.ZipFile(traversal, "w") as output:
        output.writestr("skills/../outside.txt", "bad")
    try:
        repo_harness_update.safe_extract_archive(traversal, tmp / "extract-traversal")
    except RuntimeError as exc:
        assert "unsafe archive path" in str(exc)
    else:
        raise AssertionError("traversal archive was accepted")

    symlink = tmp / "symlink.zip"
    info = zipfile.ZipInfo("skills/link")
    info.create_system = 3
    info.external_attr = (0o120777 << 16)
    with zipfile.ZipFile(symlink, "w") as output:
        output.writestr(info, "target")
    try:
        repo_harness_update.safe_extract_archive(symlink, tmp / "extract-symlink")
    except RuntimeError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("symlink archive was accepted")


def test_update_rejects_reparse_update_state_before_cache_writes(tmp: Path) -> None:
    _source1, _source2, target, archive, metadata = installed_fixture(tmp)
    outside = tmp / "outside-update-state"
    outside.mkdir()
    state = target / repo_harness_update.UPDATE_STATE_REL
    try:
        state.symlink_to(outside, target_is_directory=True)
    except OSError:
        return

    try:
        repo_harness_update.update_report(
            target, requested="v1.1.0", apply=False, archive=str(archive), archive_metadata=str(metadata)
        )
    except RuntimeError as exc:
        assert "safe harness state directory" in str(exc)
    else:
        raise AssertionError("reparse update-state directory was accepted")

    assert not list(outside.iterdir())


def test_adopt_converts_and_backs_up_matching_legacy_manifest(tmp: Path) -> None:
    source1 = source_tree(tmp / "source-v1", agents="v1\n", include_old=True, include_new=False)
    target = tmp / "consumer"
    installed = repo_harness_install.install_harness_report(source1, target, profile="minimal")
    assert installed["ok"], installed
    lock = repo_harness_install.read_install_manifest(target)
    _files, selected, rows, digest = repo_harness_update.selected_payload(source1, lock)
    legacy = {
        "schema_version": 1,
        "tool": "install-harness",
        "repository": REPOSITORY,
        "profile": selected,
        "resolved_manifest_digest": digest,
        "files": rows,
    }
    (target / repo_harness_update.LOCK_REL).unlink()
    write_json(target / repo_harness_update.LEGACY_LOCK_REL, legacy)
    archive, metadata = archive_for(tmp, source1, tag="v1.0.0", commit="1" * 40, digest=digest)

    report = repo_harness_update.adopt_report(
        target, tag="v1.0.0", archive=str(archive), archive_metadata=str(metadata)
    )

    assert report["ok"] and report["status"] == "adopted", report
    assert (target / repo_harness_update.LOCK_REL).is_file()
    assert not (target / repo_harness_update.LEGACY_LOCK_REL).exists()
    assert (target / report["legacy_backup"]).is_file()


def test_release_check_requires_annotated_stable_tag_at_clean_head(tmp: Path) -> None:
    source = source_tree(tmp / "release", agents="release\n", include_old=True, include_new=False)
    commands = [
        ["git", "init"],
        ["git", "config", "user.email", "fixture@example.invalid"],
        ["git", "config", "user.name", "Fixture"],
        ["git", "add", "."],
        ["git", "commit", "-m", "fixture"],
        ["git", "tag", "-a", "v1.0.0", "-m", "v1.0.0"],
        ["git", "tag", "v1.0.1"],
    ]
    for command in commands:
        subprocess.run(command, cwd=source, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    annotated = repo_harness_update.release_tag_report(source, tag="v1.0.0")
    lightweight = repo_harness_update.release_tag_report(source, tag="v1.0.1")

    assert annotated["ok"] and annotated["annotated"] is True, annotated
    assert not lightweight["ok"] and lightweight["annotated"] is False, lightweight
    assert any("annotated" in issue for issue in lightweight["issues"])
