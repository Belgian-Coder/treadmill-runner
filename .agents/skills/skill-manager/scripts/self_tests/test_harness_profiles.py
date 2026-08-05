"""Feature/profile contract tests for harness install and export surfaces."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from repo_support import repo_cli_parser
from repo_support import repo_doctor_clone
from repo_support import repo_harness_install
from repo_support import repo_harness_promote
from repo_support import repo_harness_render
from repo_support import repo_onboarding


GENERAL_EXCLUDES = [
    ".git/**",
    ".git",
    ".cache/**",
    "**/.cache/**",
    ".vscode/**",
    "**/.vscode/**",
    ".idea/**",
    "**/.idea/**",
    ".mypy_cache/**",
    "**/.mypy_cache/**",
    ".pytest_cache/**",
    "**/.pytest_cache/**",
    ".ruff_cache/**",
    "**/.ruff_cache/**",
    ".venv/**",
    "**/.venv/**",
    "venv/**",
    "**/venv/**",
    "node_modules/**",
    "**/node_modules/**",
    "tmp/**",
    "**/tmp/**",
    "temp/**",
    "**/temp/**",
    "dist/**",
    "**/dist/**",
    "build/**",
    "**/build/**",
    "coverage/**",
    "**/coverage/**",
    "benchmark/**",
    "**/benchmark/**",
    "**/bin/**",
    "**/obj/**",
    "__pycache__/**",
    "**/__pycache__/**",
    "*.pyc",
    "*.pyo",
    "*.log",
    ".DS_Store",
    "docs/project/project-context.md",
    "docs/project/project-context.generated.md",
    "docs/project/project-context.json",
    "docs/project/diagrams/**",
    "docs/project/review/**",
    "docs/project/validation/**",
]

STATE_EXCLUDES = [
    ".agents/harness.lock.json",
    ".agents/harness-install.json",
    ".agents/harness.overlay.json",
    ".agents/harness-install-plan.json",
    ".agents/harness-install-plan.md",
    ".agents/harness-smoke-target.json",
    ".agents/project-policy.json",
    ".agents/local-ai/cache/**",
    ".agents/local-ai/bundle/**",
    ".agents/local-ai/downloads/**",
    ".agents/local-ai/runtime/**",
    ".agents/tools/cache/**",
    ".agents/local-ai/secrets.json",
    ".agents/local-ai/secrets.local.json",
    ".agents/local-ai/local.settings.json",
    ".agents/local-ai/project.settings.json",
    ".agents/.deps/**",
    ".claude/settings.local.json",
    ".github/copilot/settings.local.json",
    "*.local",
    "*.local.*",
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "automations/**/Scripts/output/**",
    "automations/reference-refresh/References/repositories/**",
    "automations/*/runs/**",
    "automations/*/runs",
]
VSCODE_SETTINGS_PATH = ".vscode/" + "settings.json"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, payload: object) -> None:
    write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def feature_payload() -> dict[str, object]:
    return {
        "schema_version": 2,
        "tool": "install-harness-payload",
        "owner": "skill-manager",
        "include_roots": ["AGENTS.md", ".agents", "automations", "docs", ".claude", ".github"],
        "exclude_globs": GENERAL_EXCLUDES,
        "state_exclude_globs": STATE_EXCLUDES,
        "required_features": ["core"],
        "feature_bundles": {
            "core": {
                "description": "Core CLI and first-use guidance.",
                "include_globs": [
                    "AGENTS.md",
                    ".agents/manage.py",
                    ".agents/harness-payload.json",
                    ".agents/skills/skill-manager/**",
                    "docs/start-here.md",
                ],
                "requires": [],
            },
            "accepted-skills": {
                "include_globs": [".agents/skills/**"],
                "requires": ["core"],
            },
            "standard-workflows": {
                "include_globs": [
                    "automations/user-story-workflow/**",
                    "automations/bug-ticket-workflow/**",
                    "automations/disciplined-change-workflow/**",
                ],
                "requires": ["core"],
            },
            "addon": {
                "include_globs": ["docs/addon.md"],
                "requires": ["core"],
            },
            "full-surface": {
                "include_globs": ["**"],
                "requires": ["core"],
            },
        },
        "profiles": {
            "minimal": {
                "description": "Minimal.",
                "features": ["core"],
                "exclude_features": [],
            },
            "standard": {
                "description": "Standard.",
                "extends": "minimal",
                "features": ["accepted-skills", "standard-workflows"],
                "exclude_features": [],
            },
            "full": {
                "description": "Full.",
                "extends": ["standard"],
                "features": ["full-surface"],
                "exclude_features": [],
            },
            "lean": {"description": "Alias.", "alias_of": "minimal"},
        },
    }


def feature_source(tmp: Path) -> Path:
    source = tmp / "source"
    files = {
        "AGENTS.md": "# Instructions\n",
        ".agents/manage.py": "print('manage')\n",
        ".agents/skills/skill-manager/SKILL.md": "# Skill manager\n",
        ".agents/skills/demo/SKILL.md": "# Demo\n",
        "automations/user-story-workflow/WORKFLOW.md": "# Story\n",
        "automations/bug-ticket-workflow/WORKFLOW.md": "# Bug\n",
        "automations/disciplined-change-workflow/WORKFLOW.md": "# Change\n",
        "automations/extra/WORKFLOW.md": "# Extra\n",
        "docs/start-here.md": "# Start\n",
        "docs/addon.md": "# Addon\n",
        "docs/reference.md": "# Reference\n",
        ".agents/local-ai/cache/index.json": "{}\n",
        ".agents/local-ai/downloads/model.gguf": "payload\n",
        ".agents/local-ai/runtime/bin.exe": "payload\n",
        ".agents/local-ai/secrets.local.json": "{}\n",
        ".agents/skills/demo/.cache/index.json": "{}\n",
        ".agents/skills/demo/.pytest_cache/state.json": "{}\n",
        ".agents/skills/demo/.idea/workspace.xml": "<xml />\n",
        f".agents/skills/demo/{VSCODE_SETTINGS_PATH}": "{}\n",
        ".agents/skills/demo/.env.production": "TOKEN=secret\n",
        ".agents/skills/demo/tmp/scratch.txt": "runtime\n",
        ".agents/skills/demo/dist/bundle.js": "runtime\n",
        ".agents/skills/demo/build/output.bin": "runtime\n",
        ".agents/skills/demo/coverage/lcov.info": "runtime\n",
        ".agents/skills/demo/bin/tool.exe": "runtime\n",
        ".agents/skills/demo/obj/state.json": "{}\n",
        ".claude/settings.local.json": "{\"token\": \"secret\"}\n",
        ".github/copilot/settings.local.json": "{\"token\": \"secret\"}\n",
        "automations/extra/Scripts/output/result.json": "{}\n",
        "automations/extra/runs/run-a/run.json": "{}\n",
        "automations/reference-refresh/References/repositories/demo/config.json": "{}\n",
    }
    for relative, text in files.items():
        write_text(source / relative, text)
    write_json(source / ".agents/harness-payload.json", feature_payload())
    return source


def paths(report: dict[str, object]) -> set[str]:
    return {
        str(row["path"])
        for row in report.get("resolved_file_manifest", [])
        if isinstance(row, dict) and row.get("path")
    }


def test_feature_profiles_are_strict_subsets_with_stable_source_digests(tmp: Path) -> None:
    source = feature_source(tmp)
    reports = {
        profile: repo_harness_install.install_harness_report(
            source,
            tmp / f"target-{profile}",
            profile=profile,
            dry_run=True,
        )
        for profile in ("minimal", "standard", "full")
    }

    assert all(report["ok"] is True for report in reports.values()), reports
    assert paths(reports["minimal"]) < paths(reports["standard"]) < paths(reports["full"])
    repeated = repo_harness_install.install_harness_report(
        source,
        tmp / "target-minimal-repeat",
        profile="minimal",
        dry_run=True,
    )
    assert reports["minimal"]["resolved_manifest_digest"] == repeated["resolved_manifest_digest"]
    assert [row["path"] for row in reports["full"]["resolved_file_manifest"]] == sorted(paths(reports["full"]))
    assert len(str(reports["full"]["resolved_manifest_digest"])) == 64


def test_profile_alias_features_dependencies_and_core_protection(tmp: Path) -> None:
    source = feature_source(tmp)
    lean = repo_harness_install.install_harness_report(source, tmp / "lean", profile="lean", dry_run=True)
    customized = repo_harness_install.install_harness_report(
        source,
        tmp / "customized",
        profile="standard",
        with_features=["addon"],
        without_features=["standard-workflows"],
        dry_run=True,
    )
    blocked = repo_harness_install.install_harness_report(
        source,
        tmp / "blocked",
        profile="minimal",
        without_features=["core"],
        dry_run=True,
    )

    assert lean["ok"] is True, lean
    assert lean["profile"]["name"] == "lean"
    assert lean["profile"]["resolved_profile"] == "minimal"
    assert paths(lean) == paths(
        repo_harness_install.install_harness_report(source, tmp / "minimal", profile="minimal", dry_run=True)
    )
    assert customized["resolved_features"] == ["accepted-skills", "addon", "core"]
    assert "docs/addon.md" in paths(customized)
    assert not any(path.startswith("automations/") for path in paths(customized))
    assert blocked["ok"] is False
    assert any("required core feature" in issue for issue in blocked["issues"])


def test_unknown_profiles_and_features_fail_without_fallback(tmp: Path) -> None:
    source = feature_source(tmp)

    unavailable = repo_harness_install.install_harness_report(
        source,
        tmp / "unknown-profile",
        profile="missing",
        dry_run=True,
    )
    unknown_feature = repo_harness_install.install_harness_report(
        source,
        tmp / "unknown-feature",
        profile="minimal",
        with_features=["missing-feature"],
        dry_run=True,
    )

    assert unavailable["ok"] is False
    assert unavailable["profile"]["name"] == "missing"
    assert any("profile-unavailable" in issue for issue in unavailable["issues"])
    assert unknown_feature["ok"] is False
    assert any("unknown feature" in issue for issue in unknown_feature["issues"])

    wizard = repo_onboarding.install_wizard_report(
        source,
        target=tmp / "wizard",
        profile="missing",
        setup_check=False,
        install_rg_portable=False,
        bootstrap_local_ai=False,
        download_ai_models=False,
        apply=False,
    )
    assert wizard["ok"] is False, wizard
    assert wizard["status"] == "blocked"
    assert any("profile-unavailable" in issue for issue in wizard["issues"])


def test_v2_payload_requires_a_non_empty_profiles_object(tmp: Path) -> None:
    for label, replacement in (("missing", "missing"), ("null", None), ("empty", {})):
        source = feature_source(tmp / label)
        payload_path = source / ".agents/harness-payload.json"
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        if replacement == "missing":
            payload.pop("profiles")
        else:
            payload["profiles"] = replacement
        write_json(payload_path, payload)

        report = repo_harness_install.install_harness_report(
            source,
            tmp / f"target-{label}",
            profile="minimal",
            dry_run=True,
        )

        assert report["ok"] is False, report
        assert paths(report) == set(), report
        assert any("profiles must be a non-empty object for schema_version 2" in issue for issue in report["issues"])


def test_profile_resolver_rejects_cycles_alias_mixing_and_conflicting_overrides(tmp: Path) -> None:
    source = feature_source(tmp)
    payload = feature_payload()
    profiles = payload["profiles"]
    bundles = payload["feature_bundles"]
    assert isinstance(profiles, dict)
    assert isinstance(bundles, dict)

    profiles["cycle-a"] = {"extends": "cycle-b", "features": []}
    profiles["cycle-b"] = {"extends": "cycle-a", "features": []}
    profiles["invalid-alias"] = {"alias_of": "minimal", "extends": "standard", "features": ["addon"]}
    profiles["invalid-alias-globs"] = {"alias_of": "minimal", "exclude_globs": ["docs/**"]}
    bundles["dependency-a"] = {"include_globs": ["docs/addon.md"], "requires": ["dependency-b"]}
    bundles["dependency-b"] = {"include_globs": ["docs/reference.md"], "requires": ["dependency-a"]}
    profiles["feature-cycle"] = {"features": ["dependency-a"]}
    write_json(source / ".agents/harness-payload.json", payload)

    cycle = repo_harness_install.install_harness_report(source, tmp / "cycle", profile="cycle-a", dry_run=True)
    alias = repo_harness_install.install_harness_report(source, tmp / "alias", profile="invalid-alias", dry_run=True)
    alias_globs = repo_harness_install.install_harness_report(
        source,
        tmp / "alias-globs",
        profile="invalid-alias-globs",
        dry_run=True,
    )
    feature_cycle = repo_harness_install.install_harness_report(
        source,
        tmp / "feature-cycle",
        profile="feature-cycle",
        dry_run=True,
    )
    contradictory = repo_harness_install.install_harness_report(
        source,
        tmp / "contradictory",
        profile="minimal",
        with_features=["addon", "addon"],
        without_features=["addon"],
        dry_run=True,
    )
    dependency_excluded = repo_harness_install.install_harness_report(
        source,
        tmp / "dependency-excluded",
        profile="standard",
        without_features=["core"],
        dry_run=True,
    )

    assert cycle["ok"] is False and any("profile inheritance cycle" in issue for issue in cycle["issues"]), cycle
    assert alias["ok"] is False and any("alias_of cannot be combined" in issue for issue in alias["issues"]), alias
    assert alias_globs["ok"] is False and any(
        "profile invalid-alias-globs alias_of cannot be combined" in issue for issue in alias_globs["issues"]
    ), alias_globs
    assert feature_cycle["ok"] is False and any("feature dependency cycle" in issue for issue in feature_cycle["issues"]), feature_cycle
    assert contradictory["ok"] is False and any("both included and excluded" in issue for issue in contradictory["issues"]), contradictory
    assert dependency_excluded["ok"] is False and any("required core feature" in issue for issue in dependency_excluded["issues"]), dependency_excluded


def test_payload_preflight_rejects_unselected_profile_and_feature_cycles(tmp: Path) -> None:
    source = feature_source(tmp)
    payload = feature_payload()
    profiles = payload["profiles"]
    bundles = payload["feature_bundles"]
    assert isinstance(profiles, dict)
    assert isinstance(bundles, dict)
    profiles["unused-a"] = {"extends": "unused-b", "features": []}
    profiles["unused-b"] = {"extends": "unused-a", "features": []}
    bundles["unused-feature-a"] = {"include_globs": ["docs/addon.md"], "requires": ["unused-feature-b"]}
    bundles["unused-feature-b"] = {"include_globs": ["docs/reference.md"], "requires": ["unused-feature-a"]}
    write_json(source / ".agents/harness-payload.json", payload)

    report = repo_harness_install.install_harness_report(source, tmp / "target", profile="minimal", dry_run=True)

    assert report["ok"] is False, report
    assert any("profile inheritance cycle" in issue for issue in report["issues"])
    assert any("feature dependency cycle" in issue for issue in report["issues"])


def test_feature_dependency_exclusion_and_order_are_deterministic(tmp: Path) -> None:
    source = feature_source(tmp)
    payload = feature_payload()
    bundles = payload["feature_bundles"]
    profiles = payload["profiles"]
    assert isinstance(bundles, dict)
    assert isinstance(profiles, dict)
    bundles["dependent"] = {"include_globs": ["docs/reference.md"], "requires": ["addon", "core", "addon"]}
    profiles["dependent"] = {"features": ["dependent", "core", "dependent"]}
    write_json(source / ".agents/harness-payload.json", payload)

    first = repo_harness_install.install_harness_report(
        source,
        tmp / "first",
        profile="dependent",
        with_features=["accepted-skills", "addon", "accepted-skills"],
        dry_run=True,
    )
    second = repo_harness_install.install_harness_report(
        source,
        tmp / "second",
        profile="dependent",
        with_features=["addon", "accepted-skills"],
        dry_run=True,
    )
    excluded = repo_harness_install.install_harness_report(
        source,
        tmp / "excluded",
        profile="dependent",
        without_features=["addon"],
        dry_run=True,
    )

    assert first["ok"] is True and second["ok"] is True, (first, second)
    assert first["resolved_features"] == ["accepted-skills", "addon", "core", "dependent"]
    assert first["resolved_features"] == second["resolved_features"]
    assert first["resolved_manifest_digest"] == second["resolved_manifest_digest"]
    assert excluded["ok"] is False
    assert any("required by selected feature" in issue for issue in excluded["issues"]), excluded


def test_cli_with_feature_can_reinclude_a_profile_exclusion(tmp: Path) -> None:
    source = feature_source(tmp)
    payload = feature_payload()
    profiles = payload["profiles"]
    assert isinstance(profiles, dict)
    profiles["standard-without-workflows"] = {
        "extends": "standard",
        "features": [],
        "exclude_features": ["standard-workflows"],
    }
    write_json(source / ".agents/harness-payload.json", payload)

    excluded = repo_harness_install.install_harness_report(
        source,
        tmp / "excluded",
        profile="standard-without-workflows",
        dry_run=True,
    )
    restored = repo_harness_install.install_harness_report(
        source,
        tmp / "restored",
        profile="standard-without-workflows",
        with_features=["standard-workflows"],
        dry_run=True,
    )

    assert excluded["ok"] is True, excluded
    assert "standard-workflows" not in excluded["resolved_features"]
    assert restored["ok"] is True, restored
    assert "standard-workflows" in restored["resolved_features"]
    assert any(path.startswith("automations/user-story-workflow/") for path in paths(restored))


def test_every_profile_preserves_state_secret_and_runtime_exclusions(tmp: Path) -> None:
    source = feature_source(tmp)
    forbidden = {
        ".agents/local-ai/cache/index.json",
        ".agents/local-ai/downloads/model.gguf",
        ".agents/local-ai/runtime/bin.exe",
        ".agents/local-ai/secrets.local.json",
        ".agents/skills/demo/.cache/index.json",
        ".agents/skills/demo/.pytest_cache/state.json",
        ".agents/skills/demo/.idea/workspace.xml",
        f".agents/skills/demo/{VSCODE_SETTINGS_PATH}",
        ".agents/skills/demo/.env.production",
        ".agents/skills/demo/tmp/scratch.txt",
        ".agents/skills/demo/dist/bundle.js",
        ".agents/skills/demo/build/output.bin",
        ".agents/skills/demo/coverage/lcov.info",
        ".agents/skills/demo/bin/tool.exe",
        ".agents/skills/demo/obj/state.json",
        ".claude/settings.local.json",
        ".github/copilot/settings.local.json",
        "automations/extra/Scripts/output/result.json",
        "automations/extra/runs/run-a/run.json",
        "automations/reference-refresh/References/repositories/demo/config.json",
    }

    for profile in ("minimal", "standard", "full", "lean"):
        report = repo_harness_install.install_harness_report(
            source,
            tmp / f"target-{profile}",
            profile=profile,
            dry_run=True,
        )
        assert report["ok"] is True, report
        assert forbidden.isdisjoint(paths(report)), (profile, paths(report) & forbidden)
        assert forbidden <= set(report["excluded"]), (profile, set(report["excluded"]))


def test_install_and_export_enforce_required_excludes_when_payload_omits_them(tmp: Path) -> None:
    source = feature_source(tmp)
    payload_path = source / ".agents/harness-payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["exclude_globs"] = []
    payload["state_exclude_globs"] = []
    write_json(payload_path, payload)
    forbidden = {
        ".agents/local-ai/cache/index.json",
        ".agents/local-ai/downloads/model.gguf",
        ".agents/local-ai/runtime/bin.exe",
        ".agents/local-ai/secrets.local.json",
        ".agents/skills/demo/.cache/index.json",
        ".agents/skills/demo/.pytest_cache/state.json",
        ".agents/skills/demo/.idea/workspace.xml",
        f".agents/skills/demo/{VSCODE_SETTINGS_PATH}",
        ".agents/skills/demo/.env.production",
        ".agents/skills/demo/tmp/scratch.txt",
        ".agents/skills/demo/dist/bundle.js",
        ".agents/skills/demo/build/output.bin",
        ".agents/skills/demo/coverage/lcov.info",
        ".agents/skills/demo/bin/tool.exe",
        ".agents/skills/demo/obj/state.json",
        ".claude/settings.local.json",
        ".github/copilot/settings.local.json",
        "automations/extra/Scripts/output/result.json",
        "automations/extra/runs/run-a/run.json",
        "automations/reference-refresh/References/repositories/demo/config.json",
    }

    install = repo_harness_install.install_harness_report(
        source,
        tmp / "install-target",
        profile="full",
        dry_run=True,
    )
    export = repo_harness_install.public_export_report(
        source,
        tmp / "export-target",
        profile="full",
        dry_run=True,
    )
    copy_contract = repo_harness_install.copy_contract_report(source, profile="full")

    assert install["ok"] is True, install
    assert export["ok"] is True, export
    assert forbidden.isdisjoint(paths(install)), forbidden & paths(install)
    assert forbidden.isdisjoint(paths(export)), forbidden & paths(export)
    assert forbidden <= set(install["excluded"]), forbidden - set(install["excluded"])
    assert forbidden <= set(export["excluded"]), forbidden - set(export["excluded"])
    assert copy_contract["ok"] is False, copy_contract
    assert copy_contract["missing_general_excludes"], copy_contract
    assert copy_contract["missing_required_excludes"], copy_contract


def test_install_evidence_persists_profile_features_manifest_and_digest(tmp: Path) -> None:
    source = feature_source(tmp)
    target = tmp / "installed"

    report = repo_harness_install.install_harness_report(
        source,
        target,
        profile="minimal",
        with_features=["addon"],
        dry_run=False,
    )
    evidence = json.loads((target / ".agents/harness.lock.json").read_text(encoding="utf-8"))

    assert report["ok"] is True, report
    assert evidence["tool"] == "harness-lock"
    assert evidence["install"]["profile"] == "minimal"
    assert evidence["install"]["features"] == ["addon", "core"]
    assert evidence["files"] == report["resolved_file_manifest"]
    assert evidence["payload_digest"] == report["resolved_manifest_digest"]
    assert report["install_plan"]["resolved_manifest_digest"] == report["resolved_manifest_digest"]


def test_profile_downgrade_blocks_and_reports_previously_managed_files(tmp: Path) -> None:
    source = feature_source(tmp)
    target = tmp / "installed"
    full = repo_harness_install.install_harness_report(source, target, profile="full", dry_run=False)
    optional = target / "automations/extra/WORKFLOW.md"

    assert full["ok"] is True, full
    assert optional.is_file()
    evidence_before = (target / ".agents/harness.lock.json").read_bytes()
    minimal = repo_harness_install.install_harness_report(source, target, profile="minimal", dry_run=False)

    assert minimal["ok"] is False, minimal
    assert minimal["status"] == "profile-contraction-blocked", minimal
    assert optional.is_file()
    assert "automations/extra/WORKFLOW.md" in minimal["retained_previous_profile_files"]
    assert (target / ".agents/harness.lock.json").read_bytes() == evidence_before


def test_harness_promote_apply_rejects_paths_outside_resolved_profile(tmp: Path) -> None:
    source = feature_source(tmp)
    target = tmp / "installed"
    full_only_path = "automations/extra/WORKFLOW.md"
    source_file = source / full_only_path
    target_file = target / full_only_path
    source_before = source_file.read_bytes()

    full = repo_harness_install.install_harness_report(source, target, profile="full", dry_run=False)
    assert full["ok"] is True, full
    write_text(target_file, "# Consumer full-only edit\n")
    minimal = repo_harness_install.install_harness_report(source, target, profile="minimal", dry_run=False)
    assert minimal["ok"] is False, minimal
    assert minimal["status"] == "profile-contraction-blocked", minimal
    assert full_only_path in minimal["retained_previous_profile_files"]

    blocked = repo_harness_promote.harness_promote_report(
        source,
        target,
        profile="minimal",
        apply=True,
        paths=[full_only_path],
    )

    assert blocked["ok"] is False, blocked
    assert blocked["status"] == "blocked"
    assert blocked["copied"] == []
    assert source_file.read_bytes() == source_before
    assert any("outside the resolved profile/feature selection" in issue for issue in blocked["issues"])
    row = next(item for item in blocked["files"] if item.get("path") == full_only_path)
    assert row["classification"] == "outside-selected-profile", row


def test_profile_feature_flags_parse_for_every_public_harness_surface(tmp: Path) -> None:
    _ = tmp
    parser = repo_cli_parser.build_parser()
    commands = [
        ["start-here", "--profile", "lean"],
        ["project-kickoff", "--target", "D:/Project", "--profile", "lean"],
        ["install-wizard", "--target", "D:/Project", "--no-input", "--profile", "lean"],
        ["install-harness", "--target", "D:/Project", "--profile", "lean"],
        ["validate-copy-contract", "--profile", "lean"],
        ["harness-promote", "--target", "D:/Project", "--profile", "lean"],
        ["public-export", "--target", "D:/Export", "--profile", "lean"],
    ]
    for command in commands:
        args = parser.parse_args(
            [*command, "--with-feature", "addon", "--with-feature", "accepted-skills", "--without-feature", "standard-workflows"]
        )
        assert args.profile == "lean", vars(args)
        assert args.with_feature == ["addon", "accepted-skills"], vars(args)
        assert args.without_feature == ["standard-workflows"], vars(args)


def test_profile_feature_flags_propagate_in_handoff_commands_and_reports(tmp: Path) -> None:
    source = feature_source(tmp)
    target = tmp / "target"
    kwargs = {
        "profile": "minimal",
        "with_features": ["addon"],
        "without_features": [],
    }

    install = repo_harness_install.install_harness_report(source, target, dry_run=True, **kwargs)
    copy_contract = repo_harness_install.copy_contract_report(source, **kwargs)
    exported = repo_harness_install.public_export_report(source, tmp / "export", dry_run=True, **kwargs)
    wizard = repo_onboarding.install_wizard_report(
        source,
        target=target,
        setup_check=False,
        install_rg_portable=False,
        bootstrap_local_ai=False,
        download_ai_models=False,
        apply=False,
        **kwargs,
    )
    kickoff = repo_onboarding.project_kickoff_report(source, target=target, apply=False, **kwargs)
    start = repo_onboarding.start_here_report(source, target=target, simple=True, **kwargs)

    for command in (
        install["next_commands"][0],
        copy_contract["next_command"],
        exported["next_command"],
        wizard["recommended_command"],
        kickoff["primary_next_action"]["command"],
        start["primary_next_action"]["command"],
    ):
        assert "--profile minimal" in command, command
        assert "--with-feature addon" in command, command
    for report in (install, copy_contract, exported, wizard, kickoff, start):
        assert report["resolved_features"] == ["addon", "core"], report

    installed = repo_harness_install.install_harness_report(source, target, dry_run=False, **kwargs)
    assert installed["ok"] is True, installed
    promoted = repo_harness_promote.harness_promote_report(source, target, dry_run=True, **kwargs)
    assert promoted["ok"] is True, promoted
    assert promoted["resolved_features"] == ["addon", "core"]


def test_schema_v1_payload_is_rejected_without_copy_all_fallback(tmp: Path) -> None:
    source = feature_source(tmp)
    payload = feature_payload()
    payload["schema_version"] = 1
    payload.pop("required_features")
    payload.pop("feature_bundles")
    payload["profiles"] = {
        name: {"description": name, "exclude_globs": [], "state_exclude_globs": []}
        for name in ("minimal", "standard", "full")
    }
    write_json(source / ".agents/harness-payload.json", payload)

    report = repo_harness_install.install_harness_report(
        source,
        tmp / "target",
        profile="minimal",
        dry_run=True,
    )

    assert report["ok"] is False, report
    assert paths(report) == set(), report
    assert report["resolved_features"] == [], report
    assert any("payload manifest schema_version must be 2" in issue for issue in report["issues"]), report


def test_schema_v2_payload_manifest_is_required_without_built_in_fallback(tmp: Path) -> None:
    source = feature_source(tmp)
    (source / ".agents/harness-payload.json").unlink()

    report = repo_harness_install.install_harness_report(
        source,
        tmp / "target",
        profile="minimal",
        dry_run=True,
    )

    assert report["ok"] is False, report
    assert paths(report) == set(), report
    assert report["payload_manifest"]["schema_version"] == 2, report
    assert report["payload_manifest"]["source"] == "missing-file", report
    assert any("is required and must use schema_version 2" in issue for issue in report["issues"]), report


def test_repository_payload_profiles_are_real_subsets_and_partial_omit_full_generated_indexes(tmp: Path) -> None:
    source = Path(__file__).resolve().parents[5]
    reports = {
        profile: repo_harness_install.install_harness_report(
            source,
            tmp / profile,
            profile=profile,
            dry_run=True,
        )
        for profile in ("minimal", "standard", "full")
    }

    assert all(report["ok"] is True for report in reports.values()), reports
    assert paths(reports["minimal"]) < paths(reports["standard"]) < paths(reports["full"])
    full_paths = paths(reports["full"])
    for skill_dir in sorted((source / ".agents/skills").iterdir()):
        if skill_dir.is_dir():
            assert f".agents/skills/{skill_dir.name}/SKILL.md" in full_paths
            assert f".agents/skills/{skill_dir.name}/module.json" in full_paths
    for workflow_dir in sorted((source / "automations").iterdir()):
        if workflow_dir.is_dir() and (workflow_dir / "module.json").is_file():
            assert f"automations/{workflow_dir.name}/module.json" in full_paths
            assert f"automations/{workflow_dir.name}/WORKFLOW.md" in full_paths
    for descriptor in sorted((source / ".agents/integrations").glob("*/integration.json")):
        assert descriptor.relative_to(source).as_posix() in full_paths
    for guide in sorted((source / "docs/reference").glob("*.md")):
        assert guide.relative_to(source).as_posix() in full_paths
    assert ".agents/skills/agent-benchmarking/SKILL.md" in full_paths
    assert "automations/agent-benchmarking/WORKFLOW.md" in full_paths
    for profile in ("minimal", "standard"):
        selected = paths(reports[profile])
        assert ".agents/routing.md" not in selected
        assert ".agents/registry.json" not in selected
        assert "automations/routing.md" not in selected
        assert "automations/registry.json" not in selected
    assert "reference-guides" in reports["standard"]["resolved_features"]
    assert "docs/reference/commands.md" in paths(reports["standard"])


def test_repository_full_profile_exclusion_removes_bundle_paths_but_preserves_core(tmp: Path) -> None:
    source = Path(__file__).resolve().parents[5]
    full = repo_harness_install.install_harness_report(source, tmp / "full", profile="full", dry_run=True)
    without_benchmarking = repo_harness_install.install_harness_report(
        source,
        tmp / "without-benchmarking",
        profile="full",
        without_features=["benchmarking"],
        dry_run=True,
    )

    assert full["ok"] is True and without_benchmarking["ok"] is True, (full, without_benchmarking)
    assert "benchmarking" not in without_benchmarking["resolved_features"]
    assert len(paths(without_benchmarking)) < len(paths(full))
    assert not any(path.startswith(".agents/skills/agent-benchmarking/") for path in paths(without_benchmarking))
    assert not any(path.startswith("automations/agent-benchmarking/") for path in paths(without_benchmarking))
    assert ".agents/skills/skill-manager/SKILL.md" in paths(without_benchmarking)

    without_integrations = repo_harness_install.install_harness_report(
        source,
        tmp / "without-integrations",
        profile="full",
        without_features=["integrations"],
        dry_run=True,
    )
    assert without_integrations["ok"] is True, without_integrations
    assert len(paths(without_integrations)) < len(paths(full))
    assert not any(path.startswith(".github/") for path in paths(without_integrations))
    assert not any(path.startswith(".claude/") for path in paths(without_integrations))
    assert ".agents/integrations/pr-system/integration.json" in paths(without_integrations)
    assert ".agents/skills/skill-manager/SKILL.md" in paths(without_integrations)


def test_fresh_standard_profile_converges_setup_and_generated_sync(tmp: Path) -> None:
    source = Path(__file__).resolve().parents[5]
    target = tmp / "standard-target"
    installed = repo_harness_install.install_harness_report(source, target, profile="standard", dry_run=False)
    assert installed["ok"] is True, installed
    assert not (target / ".agents/routing.md").exists()

    commands = [
        [sys.executable, "-B", ".agents/manage.py", "setup", "--no-link-skills"],
        [sys.executable, "-B", ".agents/manage.py", "setup", "--check", "--no-link-skills"],
        [sys.executable, "-B", ".agents/manage.py", "sync", "--check"],
        [
            sys.executable,
            "-B",
            ".agents/manage.py",
            "startup-context",
            "--summary",
            "--compact",
            "--format",
            "json",
        ],
    ]
    results = [
        subprocess.run(
            command,
            cwd=target,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=180,
        )
        for command in commands
    ]
    assert all(result.returncode == 0 for result in results), [result.stdout[-4000:] for result in results]
    assert (target / ".agents/routing.md").is_file()
    assert (target / "automations/routing.md").is_file()


def test_partial_profile_install_smoke_initializes_before_checking_generated_outputs(tmp: Path) -> None:
    source = feature_source(tmp)
    post_install_calls: list[list[str]] = []

    def command_runner(_root: Path, args: list[str], _timeout: int) -> dict[str, object]:
        post_install_calls.append(list(args))
        return {"ok": True, "status": "passed", "command": " ".join(args), "returncode": 0, "output_tail": ""}

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        stdout = "{}\n"
        if "startup-context" in command:
            stdout = json.dumps(
                {
                    "navigation": {
                        "status": "fresh",
                        "read_first": "automations/navigation/artifacts/maps/HANDOFF.md",
                        "next_command": "none, navigation maps are fresh",
                    }
                }
            ) + "\n"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    report = repo_doctor_clone.install_harness_smoke_report(
        source,
        work_dir=tmp / "smoke",
        fast=True,
        runner=runner,
        command_runner=command_runner,
    )

    assert report["ok"] is True, report
    assert post_install_calls == [], post_install_calls
    setup_check = next(row for row in report["checks"] if row.get("name") == "project-initialization-setup")
    assert setup_check["ok"] is True


def test_explicit_setup_check_option_initializes_partial_profile_first(tmp: Path) -> None:
    source = feature_source(tmp)
    target = tmp / "target"
    calls: list[list[str]] = []

    def command_runner(_root: Path, args: list[str], _timeout: int) -> dict[str, object]:
        calls.append(list(args))
        return {"ok": True, "status": "passed", "command": " ".join(args), "returncode": 0, "output_tail": ""}

    report = repo_harness_install.install_harness_report(
        source,
        target,
        profile="minimal",
        run_setup_check=True,
        command_runner=command_runner,
    )

    assert report["ok"] is True, report
    assert calls == [
        ["setup", "--no-link-skills"],
        ["setup", "--check", "--no-link-skills"],
    ]
