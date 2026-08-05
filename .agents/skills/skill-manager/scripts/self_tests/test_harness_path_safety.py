"""Containment, contraction, and export-reuse regressions for harness profiles."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import struct
import tempfile
from pathlib import Path

from repo_support import repo_harness_install
from repo_support import repo_harness_paths
from repo_support import repo_harness_promote
from repo_support import repo_onboarding


FULL_ONLY_PATH = "docs/full-only.md"
BENCHMARK_SKILL_PATH = ".agents/skills/agent-benchmarking/SKILL.md"
BENCHMARK_WORKFLOW_PATH = "automations/agent-benchmarking/WORKFLOW.md"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, payload: object) -> None:
    write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def harness_payload() -> dict[str, object]:
    return {
        "schema_version": 2,
        "tool": "install-harness-payload",
        "owner": "skill-manager",
        "include_roots": ["AGENTS.md", ".agents", "automations", "docs"],
        "exclude_globs": list(repo_harness_install.REQUIRED_GENERAL_EXCLUDES),
        "state_exclude_globs": list(repo_harness_install.REQUIRED_STATE_EXCLUDES),
        "required_features": ["core"],
        "feature_bundles": {
            "core": {
                "include_globs": ["AGENTS.md"],
                "requires": [],
            },
            "benchmarking": {
                "include_globs": [
                    ".agents/skills/agent-benchmarking/**",
                    "automations/agent-benchmarking/**",
                ],
                "requires": ["core"],
            },
            "full-extra": {
                "include_globs": ["docs/**"],
                "requires": ["core"],
            },
        },
        "profiles": {
            "minimal": {
                "features": ["core"],
                "exclude_features": [],
            },
            "full": {
                "extends": "minimal",
                "features": ["benchmarking", "full-extra"],
                "exclude_features": [],
            },
        },
    }


def harness_source(tmp: Path) -> Path:
    source = tmp / "source"
    files = {
        "AGENTS.md": "# Harness\n",
        BENCHMARK_SKILL_PATH: "# Benchmark skill\n",
        BENCHMARK_WORKFLOW_PATH: "# Benchmark workflow\n",
        FULL_ONLY_PATH: "# Full-only guide\n",
    }
    for relative, text in files.items():
        write_text(source / relative, text)
    write_json(source / ".agents/harness-payload.json", harness_payload())
    return source


def tree_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    rows: dict[str, bytes] = {}
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        directory_path = Path(directory)
        for filename in filenames:
            path = directory_path / filename
            rows[path.relative_to(root).as_posix()] = path.read_bytes()
    return rows


def create_directory_reparse(link: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        link.mkdir()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        create_file.restype = ctypes.c_void_p
        device_io_control = kernel32.DeviceIoControl
        device_io_control.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        device_io_control.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int

        handle = create_file(
            str(link),
            0x40000000,
            0,
            None,
            3,
            0x00200000 | 0x02000000,
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle in (None, invalid_handle):
            error_code = ctypes.get_last_error()
            os.rmdir(link)
            raise AssertionError(error_code)
        absolute_target = str(target.resolve(strict=True))
        substitute_name = ("\\??\\" + absolute_target).encode("utf-16-le")
        print_name = absolute_target.encode("utf-16-le")
        path_buffer = substitute_name + b"\x00\x00" + print_name + b"\x00\x00"
        reparse_data_length = 8 + len(path_buffer)
        raw_buffer = struct.pack(
            "<LHHHHHH",
            0xA0000003,
            reparse_data_length,
            0,
            0,
            len(substitute_name),
            len(substitute_name) + 2,
            len(print_name),
        ) + path_buffer
        input_buffer = ctypes.create_string_buffer(raw_buffer)
        bytes_returned = ctypes.c_uint32()
        try:
            succeeded = device_io_control(
                handle,
                0x000900A4,
                input_buffer,
                len(raw_buffer),
                None,
                0,
                ctypes.byref(bytes_returned),
                None,
            )
            error_code = ctypes.get_last_error() if not succeeded else 0
        finally:
            kernel32.CloseHandle(handle)
        if not succeeded:
            os.rmdir(link)
            raise AssertionError(error_code)
    else:
        os.symlink(target, link, target_is_directory=True)
    assert link.exists(), link


def remove_directory_reparse(link: Path) -> None:
    try:
        os.lstat(link)
    except FileNotFoundError:
        return
    os.rmdir(link) if os.name == "nt" else os.unlink(link)


def extended_path_alias(path: Path) -> Path:
    assert os.name == "nt"
    return Path("\\\\?\\" + str(path.resolve(strict=False)))


def administrative_share_alias(path: Path) -> Path:
    assert os.name == "nt"
    resolved = path.resolve(strict=False)
    drive = resolved.drive.rstrip(":")
    assert len(drive) == 1, resolved
    relative = resolved.relative_to(Path(resolved.anchor))
    return Path(f"\\\\localhost\\{drive}$").joinpath(relative)


def unsafe_paths(report: dict[str, object]) -> set[str]:
    return {
        str(row.get("path"))
        for row in report.get("unsafe_paths", [])
        if isinstance(row, dict) and row.get("path")
    }


def test_shared_guard_rejects_cross_platform_absolute_traversal_and_ads_paths(tmp: Path) -> None:
    guard = repo_harness_paths.HarnessPathGuard(tmp / "root", label="fixture")
    unsafe = [
        "../outside.txt",
        "/absolute.txt",
        r"C:\absolute.txt",
        r"\\server\share\file.txt",
        r"\\?\C:\device-path.txt",
        "file.txt:stream",
        "nested//file.txt",
        "./file.txt",
        "CON",
        "con.txt",
        "nested/AUX.json",
        "COM1.log",
        "lpt9",
        "trailing.",
        "trailing ",
        "nested/component /file.txt",
    ]

    for raw_path in unsafe:
        try:
            guard.check(raw_path, operation="lexical-regression")
        except repo_harness_paths.UnsafeHarnessPathError as exc:
            assert exc.path == raw_path, (raw_path, exc)
        else:
            raise AssertionError(f"unsafe path was accepted: {raw_path}")


def test_windows_extended_namespace_root_aliases_fail_closed_across_harness_surfaces(tmp: Path) -> None:
    if os.name != "nt":
        return
    source = harness_source(tmp)
    same_alias = extended_path_alias(source)
    child_alias = extended_path_alias(source / "nested-target")
    reports = {
        "install-same": repo_harness_install.install_harness_report(
            source,
            same_alias,
            profile="minimal",
            dry_run=True,
        ),
        "install-ancestor": repo_harness_install.install_harness_report(
            source,
            child_alias,
            profile="minimal",
            dry_run=True,
        ),
        "export-same": repo_harness_install.public_export_report(
            source,
            same_alias,
            profile="minimal",
            dry_run=True,
        ),
        "export-ancestor": repo_harness_install.public_export_report(
            source,
            child_alias,
            profile="minimal",
            dry_run=True,
        ),
        "promote-same": repo_harness_promote.harness_promote_report(
            source,
            same_alias,
            profile="minimal",
            dry_run=True,
        ),
        "promote-ancestor": repo_harness_promote.harness_promote_report(
            source,
            child_alias,
            profile="minimal",
            dry_run=True,
        ),
    }

    for label, report in reports.items():
        assert report["ok"] is False, (label, report)
        assert report["status"] == "unsafe-path-blocked", (label, report)
        assert "." in unsafe_paths(report), (label, report)


def test_windows_extended_namespace_source_alias_is_rejected_before_install_read(tmp: Path) -> None:
    if os.name != "nt":
        return
    source = harness_source(tmp)
    target = tmp / "target"

    report = repo_harness_install.install_harness_report(
        extended_path_alias(source),
        target,
        profile="minimal",
        dry_run=True,
    )

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert "." in unsafe_paths(report), report
    assert report["resolved_file_manifest"] == [], report


def test_windows_local_and_administrative_share_root_relationships_use_filesystem_identity(tmp: Path) -> None:
    _ = tmp
    if os.name != "nt":
        return
    fixture_parent = Path(__file__).resolve().parents[6]
    with tempfile.TemporaryDirectory(prefix="harness-root-identity-", dir=fixture_parent) as raw_root:
        root = Path(raw_root)
        source = root / "source"
        child = source / "existing-child"
        sibling = root / "sibling"
        child.mkdir(parents=True)
        sibling.mkdir()
        same_alias = administrative_share_alias(source)
        child_alias = administrative_share_alias(child)
        missing_alias = administrative_share_alias(source / "missing" / "child")
        sibling_alias = administrative_share_alias(sibling)
        assert os.path.samefile(source, same_alias)
        assert os.path.samefile(child, child_alias)

        same = repo_harness_paths.root_relationship(source, same_alias, operation="identity-test")
        descendant = repo_harness_paths.root_relationship(source, child_alias, operation="identity-test")
        ancestor = repo_harness_paths.root_relationship(child_alias, source, operation="identity-test")
        missing = repo_harness_paths.root_relationship(source, missing_alias, operation="identity-test")
        distinct = repo_harness_paths.root_relationship(source, sibling_alias, operation="identity-test")

        assert same.kind == "same", same
        assert descendant.kind == "first-ancestor-of-second", descendant
        assert descendant.relative_path == "existing-child", descendant
        assert ancestor.kind == "second-ancestor-of-first", ancestor
        assert missing.kind == "first-ancestor-of-second", missing
        assert missing.relative_path == "missing/child", missing
        assert distinct.kind == "distinct", distinct


def test_windows_root_relationship_identity_errors_fail_closed(tmp: Path) -> None:
    if os.name != "nt":
        return
    first = tmp / "first"
    second = tmp / "second"
    first.mkdir()
    second.mkdir()
    original_samefile = repo_harness_paths.os.path.samefile

    def denied(_first: object, _second: object) -> bool:
        raise PermissionError("identity denied")

    repo_harness_paths.os.path.samefile = denied
    try:
        try:
            repo_harness_paths.root_relationship(first, second, operation="identity-error-test")
        except repo_harness_paths.UnsafeHarnessPathError as exc:
            assert "identity denied" in exc.reason, exc
        else:
            raise AssertionError("identity comparison error did not fail closed")
    finally:
        repo_harness_paths.os.path.samefile = original_samefile


def test_windows_administrative_share_aliases_block_all_harness_root_overlaps_without_writes(tmp: Path) -> None:
    _ = tmp
    if os.name != "nt":
        return
    fixture_parent = Path(__file__).resolve().parents[6]
    with tempfile.TemporaryDirectory(prefix="harness-root-surfaces-", dir=fixture_parent) as raw_root:
        root = Path(raw_root)
        for surface in ("install", "export", "promote"):
            for relation in ("same", "existing-descendant", "missing-descendant"):
                case_root = root / surface / relation
                source = harness_source(case_root)
                if relation == "same":
                    target = administrative_share_alias(source)
                elif relation == "existing-descendant":
                    local_target = source / "consumer-existing"
                    local_target.mkdir()
                    target = administrative_share_alias(local_target)
                else:
                    target = administrative_share_alias(source / "consumer-missing" / "child")
                assert os.path.samefile(source, administrative_share_alias(source))
                before = tree_snapshot(source)

                if surface == "install":
                    report = repo_harness_install.install_harness_report(
                        source,
                        target,
                        profile="minimal",
                        dry_run=False,
                    )
                elif surface == "export":
                    report = repo_harness_install.public_export_report(
                        source,
                        target,
                        profile="minimal",
                        dry_run=False,
                    )
                else:
                    report = repo_harness_promote.harness_promote_report(
                        source,
                        target,
                        profile="minimal",
                        dry_run=True,
                    )

                assert report["ok"] is False, (surface, relation, report)
                assert any("target must be outside the source" in issue for issue in report["issues"]), (
                    surface,
                    relation,
                    report,
                )
                assert report.get("copied", report.get("exported", [])) == [], (surface, relation, report)
                assert tree_snapshot(source) == before, (surface, relation, report)


def test_install_rejects_target_root_reparse_before_any_write(tmp: Path) -> None:
    source = harness_source(tmp)
    outside = tmp / "outside-target-root"
    write_text(outside / "sentinel.txt", "unchanged\n")
    target = tmp / "target"
    create_directory_reparse(target, outside)
    try:
        report = repo_harness_install.install_harness_report(source, target, profile="minimal", dry_run=False)
    finally:
        remove_directory_reparse(target)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert "." in unsafe_paths(report), report
    assert report["copied"] == [], report
    assert tree_snapshot(outside) == {"sentinel.txt": b"unchanged\n"}


def test_source_manifest_junction_is_rejected_before_read_or_hash(tmp: Path) -> None:
    source = tmp / "source"
    source.mkdir(parents=True)
    write_text(source / "AGENTS.md", "# Harness\n")
    outside_agents = tmp / "outside-agents"
    write_json(outside_agents / "harness-payload.json", harness_payload())
    link = source / ".agents"
    create_directory_reparse(link, outside_agents)
    try:
        report = repo_harness_install.install_harness_report(
            source,
            tmp / "target",
            profile="minimal",
            dry_run=True,
        )
    finally:
        remove_directory_reparse(link)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert ".agents/harness-payload.json" in unsafe_paths(report), report
    assert report["resolved_file_manifest"] == [], report


def test_source_include_root_junction_is_rejected_without_enumerating_outside(tmp: Path) -> None:
    source = harness_source(tmp)
    payload_path = source / ".agents/harness-payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["include_roots"].append("linked-root")
    payload["feature_bundles"]["full-extra"]["include_globs"].append("linked-root/**")
    write_json(payload_path, payload)
    outside = tmp / "outside-source"
    write_text(outside / "secret.txt", "must-not-be-read\n")
    link = source / "linked-root"
    create_directory_reparse(link, outside)
    try:
        report = repo_harness_install.install_harness_report(
            source,
            tmp / "target",
            profile="full",
            dry_run=True,
        )
    finally:
        remove_directory_reparse(link)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert "linked-root" in unsafe_paths(report), report
    assert not any(row.get("path") == "linked-root/secret.txt" for row in report["resolved_file_manifest"])


def test_install_rejects_target_junction_before_candidate_write(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    target.mkdir()
    outside = tmp / "outside-target"
    write_text(outside / "sentinel.txt", "unchanged\n")
    link = target / "docs"
    create_directory_reparse(link, outside)
    try:
        report = repo_harness_install.install_harness_report(source, target, profile="full", dry_run=False)
    finally:
        remove_directory_reparse(link)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert FULL_ONLY_PATH in unsafe_paths(report), report
    assert report["copied"] == [], report
    assert not (outside / "full-only.md").exists()
    assert (outside / "sentinel.txt").read_text(encoding="utf-8") == "unchanged\n"


def test_install_preflights_manifest_and_plan_evidence_before_any_write(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    target.mkdir()
    outside = tmp / "outside-evidence"
    write_text(outside / "sentinel.txt", "unchanged\n")
    link = target / ".agents"
    create_directory_reparse(link, outside)
    try:
        report = repo_harness_install.install_harness_report(source, target, profile="minimal", dry_run=False)
    finally:
        remove_directory_reparse(link)

    expected_evidence = {
        repo_harness_install.INSTALL_MANIFEST_REL,
        repo_harness_install.INSTALL_PLAN_JSON_REL,
        repo_harness_install.INSTALL_PLAN_MARKDOWN_REL,
    }
    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert expected_evidence <= unsafe_paths(report), report
    assert report["copied"] == [], report
    assert not (target / "AGENTS.md").exists()
    assert tree_snapshot(outside) == {"sentinel.txt": b"unchanged\n"}


def test_install_initialization_rejects_unselected_navigation_junction_before_any_write(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    outside = tmp / "outside-navigation"
    write_text(outside / "sentinel.txt", "unchanged\n")
    link = target / "automations/navigation/artifacts/maps"
    create_directory_reparse(link, outside)
    calls: list[list[str]] = []

    def runner(_cwd: Path, args: list[str], _timeout: int) -> dict[str, object]:
        calls.append(args)
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}

    try:
        report = repo_harness_install.install_harness_report(
            source,
            target,
            profile="minimal",
            dry_run=False,
            run_setup_check=True,
            command_runner=runner,
        )
    finally:
        remove_directory_reparse(link)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert "automations/navigation/artifacts/maps" in unsafe_paths(report), report
    assert report["copied"] == [], report
    assert report["post_install"] == [], report
    assert calls == []
    assert not (target / "AGENTS.md").exists()
    assert tree_snapshot(target) == {}
    assert tree_snapshot(outside) == {"sentinel.txt": b"unchanged\n"}


def test_project_kickoff_apply_rejects_navigation_junction_before_any_write(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    outside = tmp / "outside-kickoff-navigation"
    write_text(outside / "sentinel.txt", "unchanged\n")
    link = target / "automations/navigation/artifacts/maps"
    create_directory_reparse(link, outside)
    calls: list[list[str]] = []

    def runner(_cwd: Path, args: list[str], _timeout: int) -> dict[str, object]:
        calls.append(args)
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}

    try:
        report = repo_onboarding.project_kickoff_report(
            source,
            target=target,
            profile="minimal",
            apply=True,
            command_runner=runner,
        )
    finally:
        remove_directory_reparse(link)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert "automations/navigation/artifacts/maps" in unsafe_paths(report), report
    assert report["post_apply"] == [], report
    assert calls == []
    assert not (target / "AGENTS.md").exists()
    assert tree_snapshot(target) == {}
    assert tree_snapshot(outside) == {"sentinel.txt": b"unchanged\n"}


def test_install_wizard_apply_rejects_navigation_junction_before_any_write(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    outside = tmp / "outside-wizard-navigation"
    write_text(outside / "sentinel.txt", "unchanged\n")
    link = target / "automations/navigation/artifacts/maps"
    create_directory_reparse(link, outside)
    calls: list[list[str]] = []

    def runner(_cwd: Path, args: list[str], _timeout: int) -> dict[str, object]:
        calls.append(args)
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}

    try:
        report = repo_onboarding.install_wizard_report(
            source,
            target=target,
            profile="minimal",
            setup_check=False,
            install_rg_portable=False,
            bootstrap_local_ai=False,
            download_ai_models=False,
            apply=True,
            command_runner=runner,
        )
    finally:
        remove_directory_reparse(link)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert "automations/navigation/artifacts/maps" in unsafe_paths(report), report
    assert report["post_apply"] == [], report
    assert calls == []
    assert not (target / "AGENTS.md").exists()
    assert tree_snapshot(target) == {}
    assert tree_snapshot(outside) == {"sentinel.txt": b"unchanged\n"}


def test_install_initialization_allows_regular_target_and_converges(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    write_text(target / "consumer.txt", "preserved\n")
    calls: list[list[str]] = []

    def runner(_cwd: Path, args: list[str], _timeout: int) -> dict[str, object]:
        calls.append(args)
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}

    report = repo_harness_install.install_harness_report(
        source,
        target,
        profile="minimal",
        dry_run=False,
        run_setup_check=True,
        command_runner=runner,
    )

    assert report["ok"] is True, report
    assert report["status"] == "installed", report
    assert calls == [
        ["setup", "--no-link-skills"],
        ["setup", "--check", "--no-link-skills"],
    ]
    assert (target / "AGENTS.md").is_file()
    assert (target / "consumer.txt").read_text(encoding="utf-8") == "preserved\n"


def test_install_rechecks_containment_before_each_initialization_runner(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    outside = tmp / "outside-between-runners"
    write_text(outside / "sentinel.txt", "unchanged\n")
    link = target / "automations/navigation/artifacts/maps"
    calls: list[list[str]] = []

    def runner(cwd: Path, args: list[str], _timeout: int) -> dict[str, object]:
        calls.append(args)
        if len(calls) == 1:
            create_directory_reparse(link, outside)
        else:
            write_text(cwd / "automations/navigation/artifacts/maps/escaped.txt", "must-not-write\n")
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}

    try:
        report = repo_harness_install.install_harness_report(
            source,
            target,
            profile="minimal",
            dry_run=False,
            run_setup_check=True,
            command_runner=runner,
        )
    finally:
        remove_directory_reparse(link)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert "automations/navigation/artifacts/maps" in unsafe_paths(report), report
    assert calls == [["setup", "--no-link-skills"]], calls
    assert len(report["post_install"]) == 1, report
    assert tree_snapshot(outside) == {"sentinel.txt": b"unchanged\n"}


def test_install_rejects_lexically_unsafe_target_controlled_manifest_path(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    outside = tmp / "outside.txt"
    write_text(outside, "outside\n")
    write_json(
        target / repo_harness_install.INSTALL_MANIFEST_REL,
        {
            "schema_version": 1,
            "files": [{"path": "../outside.txt", "sha256": "deadbeef"}],
        },
    )
    before = tree_snapshot(target)

    report = repo_harness_install.install_harness_report(source, target, profile="minimal", dry_run=False)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert "../outside.txt" in unsafe_paths(report), report
    assert report["copied"] == [], report
    assert tree_snapshot(target) == before
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_install_rejects_reparse_target_controlled_retained_path(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    write_json(
        target / repo_harness_install.INSTALL_MANIFEST_REL,
        {
            "schema_version": 1,
            "files": [{"path": "retained/file.txt", "sha256": "deadbeef"}],
        },
    )
    manifest_before = (target / repo_harness_install.INSTALL_MANIFEST_REL).read_bytes()
    outside = tmp / "outside-retained"
    write_text(outside / "file.txt", "outside\n")
    link = target / "retained"
    create_directory_reparse(link, outside)
    try:
        report = repo_harness_install.install_harness_report(source, target, profile="minimal", dry_run=False)
    finally:
        remove_directory_reparse(link)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert "retained/file.txt" in unsafe_paths(report), report
    assert report["copied"] == [], report
    assert (target / repo_harness_install.INSTALL_MANIFEST_REL).read_bytes() == manifest_before
    assert (outside / "file.txt").read_text(encoding="utf-8") == "outside\n"


def test_install_rejects_malformed_target_controlled_manifest_row_before_force_write(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    installed = repo_harness_install.install_harness_report(source, target, profile="minimal", dry_run=False)
    assert installed["ok"] is True, installed
    write_text(target / "AGENTS.md", "# Consumer edit\n")
    manifest_path = target / repo_harness_install.INSTALL_MANIFEST_REL
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = "not-a-sha256"
    manifest["files"][0]["bytes"] = -1
    write_json(manifest_path, manifest)
    before = tree_snapshot(target)

    report = repo_harness_install.install_harness_report(
        source,
        target,
        profile="minimal",
        dry_run=False,
        force=True,
    )

    assert report["ok"] is False, report
    assert report["status"] == "invalid-install-manifest", report
    assert any("sha256 must be 64 lowercase hexadecimal characters" in issue for issue in report["install_manifest_issues"])
    assert any("bytes must be a non-negative integer" in issue for issue in report["install_manifest_issues"])
    assert report["copied"] == [], report
    assert tree_snapshot(target) == before


def test_install_rejects_duplicate_target_controlled_manifest_rows_before_write(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    installed = repo_harness_install.install_harness_report(source, target, profile="minimal", dry_run=False)
    assert installed["ok"] is True, installed
    manifest_path = target / repo_harness_install.INSTALL_MANIFEST_REL
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append(dict(manifest["files"][0]))
    write_json(manifest_path, manifest)
    before = tree_snapshot(target)

    report = repo_harness_install.install_harness_report(source, target, profile="minimal", dry_run=False)

    assert report["ok"] is False, report
    assert report["status"] == "invalid-install-manifest", report
    assert report["install_manifest_issues"] == ["AGENTS.md: duplicate path in install manifest"], report
    assert report["copied"] == [], report
    assert tree_snapshot(target) == before


def test_install_rejects_present_unreadable_or_malformed_manifest_shapes_before_force_write(tmp: Path) -> None:
    cases = {
        "invalid-utf8": b"\xff\xfe\x00",
        "invalid-json": b"{\n",
        "non-object": b"[]\n",
        "missing-files": b"{}\n",
        "non-list-files": b'{"files": {}}\n',
        "non-object-row": b'{"files": [42]}\n',
    }
    for case_name, manifest_bytes in cases.items():
        case_root = tmp / case_name
        source = harness_source(case_root)
        target = case_root / "target"
        installed = repo_harness_install.install_harness_report(source, target, profile="minimal", dry_run=False)
        assert installed["ok"] is True, (case_name, installed)
        write_text(target / "AGENTS.md", "# Consumer edit\n")
        manifest_path = target / repo_harness_install.INSTALL_MANIFEST_REL
        manifest_path.write_bytes(manifest_bytes)
        before = tree_snapshot(target)

        report = repo_harness_install.install_harness_report(
            source,
            target,
            profile="minimal",
            dry_run=False,
            force=True,
        )

        assert report["ok"] is False, (case_name, report)
        assert report["status"] == "invalid-install-manifest", (case_name, report)
        assert report["install_manifest_issues"], (case_name, report)
        assert report["copied"] == [], (case_name, report)
        assert tree_snapshot(target) == before, case_name


def test_install_rejects_regular_file_evidence_ancestor_before_payload_write(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    target.mkdir()
    write_text(target / ".agents", "blocks evidence directory\n")
    before = tree_snapshot(target)

    report = repo_harness_install.install_harness_report(source, target, profile="minimal", dry_run=False, force=True)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert repo_harness_install.INSTALL_MANIFEST_REL in unsafe_paths(report), report
    assert report["copied"] == [], report
    assert tree_snapshot(target) == before


def test_install_rejects_directory_payload_destination_before_any_write(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    write_text(target / FULL_ONLY_PATH / "sentinel.txt", "unchanged\n")
    before = tree_snapshot(target)

    report = repo_harness_install.install_harness_report(source, target, profile="full", dry_run=False, force=True)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert FULL_ONLY_PATH in unsafe_paths(report), report
    assert report["copied"] == [], report
    assert tree_snapshot(target) == before


def test_install_rejects_directory_evidence_destination_before_payload_write(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    evidence_directory = target / repo_harness_install.INSTALL_MANIFEST_REL
    evidence_directory.mkdir(parents=True)
    before = tree_snapshot(target)

    report = repo_harness_install.install_harness_report(source, target, profile="minimal", dry_run=False)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert repo_harness_install.INSTALL_MANIFEST_REL in unsafe_paths(report), report
    assert report["copied"] == [], report
    assert tree_snapshot(target) == before


def test_public_export_rejects_target_junction_before_write(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "export"
    target.mkdir()
    outside = tmp / "outside-export"
    write_text(outside / "sentinel.txt", "unchanged\n")
    link = target / "docs"
    create_directory_reparse(link, outside)
    try:
        report = repo_harness_install.public_export_report(source, target, profile="full", dry_run=False)
    finally:
        remove_directory_reparse(link)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert FULL_ONLY_PATH in unsafe_paths(report), report
    assert report["exported"] == [], report
    assert not (outside / "full-only.md").exists()


def test_harness_promote_rejects_target_junction_read(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    installed = repo_harness_install.install_harness_report(source, target, profile="full", dry_run=False)
    assert installed["ok"] is True, installed
    source_before = (source / FULL_ONLY_PATH).read_bytes()
    shutil.rmtree(target / "docs")
    outside = tmp / "outside-promote-read"
    write_text(outside / "full-only.md", "# Consumer edit outside\n")
    link = target / "docs"
    create_directory_reparse(link, outside)
    try:
        report = repo_harness_promote.harness_promote_report(
            source,
            target,
            profile="full",
            apply=True,
            paths=[FULL_ONLY_PATH],
        )
    finally:
        remove_directory_reparse(link)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert FULL_ONLY_PATH in unsafe_paths(report), report
    assert report["copied"] == [], report
    assert (source / FULL_ONLY_PATH).read_bytes() == source_before


def test_harness_promote_rejects_source_destination_junction_write(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    installed = repo_harness_install.install_harness_report(source, target, profile="full", dry_run=False)
    assert installed["ok"] is True, installed
    write_text(target / FULL_ONLY_PATH, "# Consumer edit\n")
    shutil.rmtree(source / "docs")
    outside = tmp / "outside-promote-write"
    write_text(outside / "full-only.md", "# Original outside\n")
    outside_before = tree_snapshot(outside)
    link = source / "docs"
    create_directory_reparse(link, outside)
    try:
        report = repo_harness_promote.harness_promote_report(
            source,
            target,
            profile="full",
            apply=True,
            paths=[FULL_ONLY_PATH],
        )
    finally:
        remove_directory_reparse(link)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert FULL_ONLY_PATH in unsafe_paths(report), report
    assert report["copied"] == [], report
    assert tree_snapshot(outside) == outside_before


def test_harness_promote_dry_run_blocks_source_include_root_reparse(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    installed = repo_harness_install.install_harness_report(source, target, profile="full", dry_run=False)
    assert installed["ok"] is True, installed
    shutil.rmtree(source / "docs")
    outside = tmp / "outside-promote-dry-run"
    write_text(outside / "full-only.md", "# Outside\n")
    link = source / "docs"
    create_directory_reparse(link, outside)
    try:
        report = repo_harness_promote.harness_promote_report(source, target, profile="full", dry_run=True)
    finally:
        remove_directory_reparse(link)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert "docs" in unsafe_paths(report), report
    assert report["copied"] == [], report


def test_harness_promote_dry_run_blocks_target_file_reparse(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    installed = repo_harness_install.install_harness_report(source, target, profile="full", dry_run=False)
    assert installed["ok"] is True, installed
    shutil.rmtree(target / "docs")
    outside = tmp / "outside-promote-target-dry-run"
    write_text(outside / "full-only.md", "# Outside\n")
    link = target / "docs"
    create_directory_reparse(link, outside)
    try:
        report = repo_harness_promote.harness_promote_report(source, target, profile="full", dry_run=True)
    finally:
        remove_directory_reparse(link)

    assert report["ok"] is False, report
    assert report["status"] == "unsafe-path-blocked", report
    assert FULL_ONLY_PATH in unsafe_paths(report), report
    assert report["copied"] == [], report


def test_harness_promote_apply_blocks_diverged_selected_file(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    installed = repo_harness_install.install_harness_report(source, target, profile="minimal", dry_run=False)
    assert installed["ok"] is True, installed
    write_text(source / "AGENTS.md", "# Source edit\n")
    write_text(target / "AGENTS.md", "# Consumer edit\n")
    source_before = tree_snapshot(source)

    report = repo_harness_promote.harness_promote_report(
        source,
        target,
        profile="minimal",
        apply=True,
        paths=["AGENTS.md"],
    )

    assert report["ok"] is False, report
    assert report["status"] == "blocked", report
    assert report["copied"] == [], report
    assert any("consumer-changed-only" in issue for issue in report["issues"]), report
    assert tree_snapshot(source) == source_before


def test_harness_promote_apply_stably_deduplicates_selected_paths(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    installed = repo_harness_install.install_harness_report(source, target, profile="minimal", dry_run=False)
    assert installed["ok"] is True, installed
    write_text(target / "AGENTS.md", "# Consumer edit\n")

    report = repo_harness_promote.harness_promote_report(
        source,
        target,
        profile="minimal",
        apply=True,
        paths=["AGENTS.md", "AGENTS.md"],
    )

    assert report["ok"] is True, report
    assert report["status"] == "applied", report
    assert report["selected_paths"] == ["AGENTS.md"], report
    assert report["copied"] == ["AGENTS.md"], report
    assert (source / "AGENTS.md").read_bytes() == (target / "AGENTS.md").read_bytes()


def test_profile_contraction_full_to_minimal_blocks_without_writes(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    full = repo_harness_install.install_harness_report(source, target, profile="full", dry_run=False)
    assert full["ok"] is True, full
    before = tree_snapshot(target)

    report = repo_harness_install.install_harness_report(
        source,
        target,
        profile="minimal",
        dry_run=False,
        force=True,
    )

    expected = sorted([BENCHMARK_SKILL_PATH, BENCHMARK_WORKFLOW_PATH, FULL_ONLY_PATH])
    assert report["ok"] is False, report
    assert report["status"] == "profile-contraction-blocked", report
    assert report["retained_previous_profile_files"] == expected, report
    assert any("profile-contraction-blocked" in issue for issue in report["issues"]), report
    assert report["copied"] == [], report
    assert all(row["status"] == "blocked" for row in report["install_plan_artifacts"].values())
    assert tree_snapshot(target) == before


def test_profile_contraction_blocks_existing_owned_directory_without_writes(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    full = repo_harness_install.install_harness_report(source, target, profile="full", dry_run=False)
    assert full["ok"] is True, full
    (target / BENCHMARK_SKILL_PATH).unlink()
    (target / BENCHMARK_WORKFLOW_PATH).unlink()
    (target / FULL_ONLY_PATH).unlink()
    write_text(target / FULL_ONLY_PATH / "sentinel.txt", "consumer directory\n")
    before = tree_snapshot(target)

    report = repo_harness_install.install_harness_report(source, target, profile="minimal", dry_run=False, force=True)

    assert report["ok"] is False, report
    assert report["status"] == "profile-contraction-blocked", report
    assert report["retained_previous_profile_files"] == [FULL_ONLY_PATH], report
    assert report["copied"] == [], report
    assert tree_snapshot(target) == before


def test_profile_contraction_without_benchmarking_blocks_without_writes(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    full = repo_harness_install.install_harness_report(source, target, profile="full", dry_run=False)
    assert full["ok"] is True, full
    before = tree_snapshot(target)

    report = repo_harness_install.install_harness_report(
        source,
        target,
        profile="full",
        without_features=["benchmarking"],
        dry_run=False,
    )

    expected = sorted([BENCHMARK_SKILL_PATH, BENCHMARK_WORKFLOW_PATH])
    assert report["ok"] is False, report
    assert report["status"] == "profile-contraction-blocked", report
    assert report["retained_previous_profile_files"] == expected, report
    assert report["copied"] == [], report
    assert tree_snapshot(target) == before


def test_profile_expansion_minimal_to_full_remains_allowed(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "target"
    minimal = repo_harness_install.install_harness_report(source, target, profile="minimal", dry_run=False)
    expanded = repo_harness_install.install_harness_report(source, target, profile="full", dry_run=False)

    assert minimal["ok"] is True, minimal
    assert expanded["ok"] is True, expanded
    assert expanded["status"] == "updated", expanded
    assert (target / BENCHMARK_SKILL_PATH).is_file()
    assert (target / BENCHMARK_WORKFLOW_PATH).is_file()
    assert (target / FULL_ONLY_PATH).is_file()


def assert_reused_export_blocked(
    report: dict[str, object],
    *,
    expected_out_of_selection: list[str],
    before: dict[str, bytes],
    target: Path,
) -> None:
    assert report["ok"] is False, report
    assert report["status"] == "export-target-not-empty", report
    assert report["existing_target_paths"] == sorted(before), report
    assert report["out_of_selection_existing_paths"] == sorted(expected_out_of_selection), report
    assert report["exported"] == [], report
    assert tree_snapshot(target) == before


def test_public_export_reuse_full_to_minimal_blocks_even_with_force_and_dry_run(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "export"
    full = repo_harness_install.public_export_report(source, target, profile="full", dry_run=False)
    assert full["ok"] is True, full
    before = tree_snapshot(target)
    expected = [BENCHMARK_SKILL_PATH, BENCHMARK_WORKFLOW_PATH, FULL_ONLY_PATH]

    write_report = repo_harness_install.public_export_report(
        source,
        target,
        profile="minimal",
        dry_run=False,
        force=True,
    )
    assert_reused_export_blocked(write_report, expected_out_of_selection=expected, before=before, target=target)

    dry_report = repo_harness_install.public_export_report(
        source,
        target,
        profile="minimal",
        dry_run=True,
    )
    assert dry_report["dry_run"] is True
    assert_reused_export_blocked(dry_report, expected_out_of_selection=expected, before=before, target=target)


def test_public_export_reuse_without_benchmarking_blocks_without_writes(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "export"
    full = repo_harness_install.public_export_report(source, target, profile="full", dry_run=False)
    assert full["ok"] is True, full
    before = tree_snapshot(target)

    report = repo_harness_install.public_export_report(
        source,
        target,
        profile="full",
        without_features=["benchmarking"],
        dry_run=False,
    )

    assert_reused_export_blocked(
        report,
        expected_out_of_selection=[BENCHMARK_SKILL_PATH, BENCHMARK_WORKFLOW_PATH],
        before=before,
        target=target,
    )


def test_public_export_to_existing_empty_target_remains_allowed(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "empty-export"
    target.mkdir()

    report = repo_harness_install.public_export_report(source, target, profile="minimal", dry_run=False)

    assert report["ok"] is True, report
    assert report["status"] == "exported", report
    assert (target / "AGENTS.md").is_file()


def test_public_export_allows_empty_directory_that_is_parent_of_selected_file(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "empty-parent-export"
    (target / "docs").mkdir(parents=True)

    report = repo_harness_install.public_export_report(source, target, profile="full", dry_run=False)

    assert report["ok"] is True, report
    assert report["status"] == "exported", report
    assert report["out_of_selection_existing_paths"] == [], report
    assert (target / FULL_ONLY_PATH).is_file()


def test_public_export_same_profile_unchanged_files_are_reusable(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "export"
    first = repo_harness_install.public_export_report(source, target, profile="minimal", dry_run=False)
    assert first["ok"] is True, first
    before = tree_snapshot(target)

    report = repo_harness_install.public_export_report(source, target, profile="minimal", dry_run=False)

    assert report["ok"] is True, report
    assert report["status"] == "exported", report
    assert report["already_present"] == ["AGENTS.md"], report
    assert report["planned"] == [], report
    assert report["exported"] == [], report
    assert report["collisions"] == [], report
    assert tree_snapshot(target) == before


def test_public_export_changed_selected_file_collides_without_force_and_writes_nothing(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "export"
    first = repo_harness_install.public_export_report(source, target, profile="minimal", dry_run=False)
    assert first["ok"] is True, first
    write_text(target / "AGENTS.md", "# Consumer edit\n")
    before = tree_snapshot(target)

    report = repo_harness_install.public_export_report(
        source,
        target,
        profile="minimal",
        dry_run=False,
        force=False,
    )

    assert report["ok"] is False, report
    assert report["status"] == "blocked", report
    assert report["collisions"] == [{"path": "AGENTS.md", "reason": "target file differs from source"}], report
    assert report["exported"] == [], report
    assert tree_snapshot(target) == before


def test_public_export_changed_selected_file_is_updated_with_force(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "export"
    first = repo_harness_install.public_export_report(source, target, profile="minimal", dry_run=False)
    assert first["ok"] is True, first
    write_text(target / "AGENTS.md", "# Consumer edit\n")

    report = repo_harness_install.public_export_report(
        source,
        target,
        profile="minimal",
        dry_run=False,
        force=True,
    )

    assert report["ok"] is True, report
    assert report["status"] == "exported", report
    assert report["planned"] == [{"path": "AGENTS.md", "bytes": 10, "reason": "forced-update"}], report
    assert report["exported"] == ["AGENTS.md"], report
    assert (target / "AGENTS.md").read_bytes() == (source / "AGENTS.md").read_bytes()


def test_public_export_out_of_selection_path_still_blocks_atomically(tmp: Path) -> None:
    source = harness_source(tmp)
    target = tmp / "export"
    write_text(target / "consumer.txt", "preserved\n")
    before = tree_snapshot(target)

    report = repo_harness_install.public_export_report(source, target, profile="minimal", dry_run=False, force=True)

    assert report["ok"] is False, report
    assert report["status"] == "export-target-not-empty", report
    assert report["out_of_selection_existing_paths"] == ["consumer.txt"], report
    assert report["exported"] == [], report
    assert tree_snapshot(target) == before
