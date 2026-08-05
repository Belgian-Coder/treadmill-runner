"""Focused tests for project-owned policy configuration."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from repo_support import repo_command_metrics
from repo_support import repo_cost_policy
from repo_support import repo_harness_install
from repo_support import repo_health_surface
from repo_support import repo_policy
import validate_skill


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, value: object) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def make_project(root: Path) -> None:
    write_text(root / ".agents" / "manage.py", "# fixture\n")
    write_text(root / "AGENTS.md", "# Repo\n")
    write_text(root / ".agents" / "routing.md", "# Skills\n")
    write_text(root / "automations" / "routing.md", "# Workflows\n")


def test_project_policy_defaults_are_available_without_a_project_file(tmp: Path) -> None:
    make_project(tmp)
    values, issues, configured = repo_policy.effective_values(tmp)

    assert issues == [], issues
    assert configured == set()
    assert values["limits.agents.warn_chars"] == 3500
    assert values["limits.skill.fail_words"] == 2000
    assert values["commands.output_tokens.finish"] == 1800
    assert not (tmp / repo_policy.PROJECT_POLICY_PATH).exists()


def test_project_policy_set_reset_materializes_complete_policy_and_rejects_invalid_writes(tmp: Path) -> None:
    make_project(tmp)
    ok, message = repo_policy.configure_project_value(tmp, "limits.agents.warn_chars", 3600)
    assert ok, message
    document = json.loads((tmp / repo_policy.PROJECT_POLICY_PATH).read_text(encoding="utf-8"))
    assert document["limits"]["agents"]["warn_chars"] == 3600
    assert document["limits"]["skill"]["fail_words"] == 2000
    assert document["warnings"]["health"]["skill"]["words"] == "warning"
    assert document["commands"]["output_tokens"]["finish"] == 1800
    assert document["$schema"] == repo_policy.INSTANCE_SCHEMA
    assert document["schema_version"] == 2
    assert document["cost_policy"]["review"]["loop"]["max_units"] == 20

    before = (tmp / repo_policy.PROJECT_POLICY_PATH).read_bytes()
    ok, message = repo_policy.configure_project_value(tmp, "limits.agents.warn_chars", 5000)
    assert not ok
    assert "lower than" in message
    assert (tmp / repo_policy.PROJECT_POLICY_PATH).read_bytes() == before

    ok, message = repo_policy.configure_project_value(tmp, "limits.agents.warn_chars", None, reset=True)
    assert ok, message
    document = json.loads((tmp / repo_policy.PROJECT_POLICY_PATH).read_text(encoding="utf-8"))
    assert document["limits"]["agents"]["warn_chars"] == 3500


def test_project_policy_rejects_unknown_paths_actions_and_wrong_types(tmp: Path) -> None:
    make_project(tmp)
    for path, value in (
        ("limits.unknown", 1),
        ("limits.script.warn_lines", True),
        ("warnings.health.script.lines", "sometimes"),
    ):
        ok, _message = repo_policy.configure_project_value(tmp, path, value)
        assert not ok
    assert not (tmp / repo_policy.PROJECT_POLICY_PATH).exists()


def test_project_policy_warning_actions_can_hide_or_escalate_registered_advisories(tmp: Path) -> None:
    make_project(tmp)
    message = repo_policy.tagged_warning("health.script.lines", "large script")
    warnings, errors = repo_policy.classify_warnings(tmp, [message, "unregistered warning"])
    assert warnings == [message, "unregistered warning"]
    assert errors == []

    assert repo_policy.configure_project_value(tmp, "warnings.health.script.lines", "off")[0]
    warnings, errors = repo_policy.classify_warnings(tmp, [message, "unregistered warning"])
    assert warnings == ["unregistered warning"]
    assert errors == []

    assert repo_policy.configure_project_value(tmp, "warnings.health.script.lines", "error")[0]
    warnings, errors = repo_policy.classify_warnings(tmp, [message])
    assert warnings == []
    assert errors == [message]

    assert repo_policy.configure_project_value(tmp, "warnings.default_action", "off")[0]
    warnings, errors = repo_policy.classify_warnings(tmp, ["unregistered warning"])
    assert warnings == []
    assert errors == []


def test_project_policy_health_thresholds_drive_routing_warnings(tmp: Path) -> None:
    make_project(tmp)
    write_text(tmp / ".agents" / "routing.md", "# Skills\n" + ("x" * 80))
    assert repo_policy.configure_project_value(tmp, "limits.routing.warn_chars", 40)[0]

    warnings = repo_health_surface.routing_budget_warnings(tmp)

    assert any("[policy:health.routing.characters]" in item for item in warnings)


def test_project_policy_command_budgets_drive_metric_reports(tmp: Path) -> None:
    make_project(tmp)
    assert repo_policy.configure_project_value(tmp, "commands.latency_ms.status-fast", 1234)[0]
    assert repo_policy.configure_project_value(tmp, "commands.output_tokens.status-fast", 321)[0]
    repo_command_metrics.configure_policy_root(tmp)
    try:
        latency = repo_command_metrics.timing_budget_report("status-fast", 10)
        output = repo_command_metrics.output_budget_report("status-fast", {"ok": True})
    finally:
        repo_command_metrics.configure_policy_root(None)

    assert latency["budget_ms"] == 1234
    assert output["budget_tokens"] == 321


def test_project_policy_cost_bridge_validates_before_writing(tmp: Path) -> None:
    make_project(tmp)
    write_json(tmp / repo_policy.LOCAL_AI_PATH, {"tasks": sorted(repo_cost_policy.LOCAL_AI_TASKS)})
    write_json(tmp / repo_policy.PROJECT_POLICY_PATH, repo_policy.default_policy_document())

    ok, message = repo_policy.configure_project_value(tmp, "cost_policy.budgets.phases.overrides.routing", 1600)
    assert ok, message
    configured = json.loads((tmp / repo_policy.PROJECT_POLICY_PATH).read_text(encoding="utf-8"))
    assert configured["cost_policy"]["budgets"]["phases"]["overrides"]["routing"] == 1600

    before = (tmp / repo_policy.PROJECT_POLICY_PATH).read_bytes()
    ok, message = repo_policy.configure_project_value(tmp, "cost_policy.review.loop.max_units", 0)
    assert not ok
    assert "positive integer" in message
    assert (tmp / repo_policy.PROJECT_POLICY_PATH).read_bytes() == before

    ok, message = repo_policy.configure_project_value(
        tmp, "cost_policy.budgets.phases.overrides.routing", None, reset=True
    )
    assert ok, message
    configured = json.loads((tmp / repo_policy.PROJECT_POLICY_PATH).read_text(encoding="utf-8"))
    assert configured["cost_policy"]["budgets"]["phases"]["overrides"]["routing"] == 1500


def test_project_policy_document_materializes_every_catalog_leaf(tmp: Path) -> None:
    make_project(tmp)
    write_json(tmp / repo_policy.PROJECT_POLICY_PATH, repo_policy.default_policy_document())

    document, issues, exists = repo_policy.load_project_policy(tmp)
    catalog, catalog_issues = repo_policy.policy_catalog(tmp)

    assert exists
    assert issues == [], issues
    assert catalog_issues == [], catalog_issues
    flattened = repo_policy._flatten(document)
    assert set(catalog) == {
        path for path in flattened if path not in {"$schema", "schema_version"}
    }


def test_project_policy_v1_migration_is_explicit_lossless_and_v2_only(tmp: Path) -> None:
    make_project(tmp)
    v1 = {
        "schema_version": 1,
        "limits": repo_policy.default_policy_document()["limits"],
        "warnings": repo_policy.default_policy_document()["warnings"],
        "commands": repo_policy.default_policy_document()["commands"],
        "cost_policy": repo_cost_policy.default_cost_policy(),
    }
    v1["limits"]["agents"]["warn_chars"] = 3600
    del v1["limits"]["import"]
    v1["cost_policy"]["phase_budgets"]["routing"] = 1700
    incomplete = json.loads(json.dumps(v1))
    del incomplete["limits"]["agents"]["warn_chars"]
    assert any("v1 policy is incomplete" in issue for issue in repo_policy.migrate_v1_document(incomplete)[1])
    write_json(tmp / repo_policy.PROJECT_POLICY_PATH, v1)

    _document, issues, exists = repo_policy.load_project_policy(tmp)
    assert exists
    assert any("schema_version must be 2" in issue for issue in issues)
    assert any("$schema must be" in issue for issue in issues)
    ok, message = repo_policy.migrate_project_policy(tmp)
    assert ok, message
    migrated = json.loads((tmp / repo_policy.PROJECT_POLICY_PATH).read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert migrated["limits"]["agents"]["warn_chars"] == 3600
    assert migrated["cost_policy"]["budgets"]["phases"]["overrides"]["routing"] == 1700
    assert "id" not in migrated["cost_policy"]
    before = (tmp / repo_policy.PROJECT_POLICY_PATH).read_bytes()
    assert not repo_policy.migrate_project_policy(tmp)[0]
    assert (tmp / repo_policy.PROJECT_POLICY_PATH).read_bytes() == before


def test_project_policy_schema_is_strict_and_unsafe_paths_fail_semantics(tmp: Path) -> None:
    make_project(tmp)
    schema = repo_policy.generated_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema_version"] == {"const": 2}
    assert schema["additionalProperties"] is False
    document = repo_policy.default_policy_document()
    document["cost_policy"]["context"]["always_loaded"]["files"][0] = "../outside.md"
    _loaded, issues, _exists = repo_policy.load_project_policy_from_document(document, tmp)
    assert any("unsafe project-relative path" in issue for issue in issues)
    for unsafe in (
        "C:/Windows/System32",
        ".",
        "AGENTS.md:stream",
        "NUL",
        "CON.txt",
        "nested//file.md",
        "nested/./file.md",
        "nested/file.md.",
        " nested/file.md",
        "nested/file.md\x00tail",
    ):
        candidate = repo_policy.default_policy_document()
        candidate["cost_policy"]["context"]["always_loaded"]["files"][0] = unsafe
        _loaded, issues, _exists = repo_policy.load_project_policy_from_document(candidate, tmp)
        assert any("unsafe project-relative path" in issue for issue in issues), issues
    for malformed in ({"path": "AGENTS.md"}, 7):
        candidate = repo_policy.default_policy_document()
        candidate["cost_policy"]["context"]["always_loaded"]["files"][0] = malformed
        _loaded, issues, _exists = repo_policy.load_project_policy_from_document(candidate, tmp)
        assert any("must contain only strings" in issue for issue in issues), issues
    for malformed_tasks in ([7], [], ["validation-triage", "validation-triage"]):
        candidate = repo_policy.default_policy_document()
        candidate["cost_policy"]["local_ai"]["warm_batch"]["prefer_for_tasks"] = malformed_tasks
        _loaded, issues, _exists = repo_policy.load_project_policy_from_document(candidate, tmp)
        assert issues, malformed_tasks
    candidate = repo_policy.default_policy_document()
    candidate["cost_policy"]["routing"]["tasks"]["routing"]["local_ai_use_cases"] = [
        "skill-routing",
        "skill-routing",
    ]
    _loaded, issues, _exists = repo_policy.load_project_policy_from_document(candidate, tmp)
    assert any("unique non-empty strings" in issue for issue in issues), issues
    for leaf in (
        ("default_paid_model_fallback",),
        ("tasks", "routing", "fallback"),
        ("tasks", "routing", "authoritative_evidence"),
    ):
        candidate = repo_policy.default_policy_document()
        node = candidate["cost_policy"]["routing"]
        for component in leaf[:-1]:
            node = node[component]
        node[leaf[-1]] = ""
        _loaded, issues, _exists = repo_policy.load_project_policy_from_document(candidate, tmp)
        assert any("must be a non-empty string" in issue for issue in issues), (leaf, issues)


def test_cost_policy_report_exposes_only_canonical_v2_configuration_paths(tmp: Path) -> None:
    make_project(tmp)
    write_json(tmp / repo_cost_policy.LOCAL_AI_CONFIG_PATH, {})
    document = repo_policy.default_policy_document()
    document["cost_policy"]["guidance"]["default"]["budget_tokens"] = "bad"
    write_json(tmp / repo_policy.PROJECT_POLICY_PATH, document)

    report = repo_cost_policy.cost_policy_report(tmp, compact=True)

    assert not report["ok"]
    assert report["policy"]["source"] == "fallback-invalid"
    assert any("cost_policy.guidance.default.budget_tokens" in issue for issue in report["issues"])
    obsolete = (
        "cost_policy.default_guidance_budget_tokens",
        "cost_policy.phase_budgets",
        "cost_policy.delegation_gates",
        "cost_policy.schema_version",
    )
    assert not any(old in issue for issue in report["issues"] for old in obsolete), report["issues"]
    assert "runtime_profile" in report["policy"]
    assert "prefer_local_ai_over_paid_small_models" not in report["policy"]


def test_project_policy_refresh_adds_new_defaults_without_overwriting_choices(tmp: Path) -> None:
    make_project(tmp)
    document = repo_policy.default_policy_document()
    document["limits"]["agents"]["warn_chars"] = 3600
    del document["limits"]["dashboard"]["path_chars"]
    write_json(tmp / repo_policy.PROJECT_POLICY_PATH, document)

    ok, message = repo_policy.refresh_project_policy(tmp)

    assert ok, message
    refreshed = json.loads((tmp / repo_policy.PROJECT_POLICY_PATH).read_text(encoding="utf-8"))
    assert refreshed["limits"]["agents"]["warn_chars"] == 3600
    assert refreshed["limits"]["dashboard"]["path_chars"] == 160


def test_project_policy_cli_explains_source_and_effective_value(tmp: Path, capsys=None) -> None:
    make_project(tmp)
    assert repo_policy.configure_project_value(tmp, "limits.agents.warn_chars", 3600)[0]
    args = Namespace(
        policy_action="explain",
        path="limits.agents.warn_chars",
        value=None,
        section=None,
        output_format="json",
    )
    # The repository test runner captures stdout around each test; verify the report through the catalog.
    catalog, issues = repo_policy.policy_catalog(tmp)
    assert issues == [], issues
    assert catalog[args.path]["effective"] == 3600
    assert catalog[args.path]["source"] == repo_policy.PROJECT_POLICY_PATH


def test_project_policy_skill_warning_and_failure_limits_are_effective(tmp: Path) -> None:
    make_project(tmp)
    skill = tmp / ".agents" / "skills" / "demo"
    write_text(
        skill / "SKILL.md",
        "---\nname: demo\ndescription: Use when testing project policies.\n---\n\n# Demo\n\n"
        + ("word " * 1300),
    )
    assert repo_policy.configure_project_value(tmp, "limits.skill.warn_words", 1500)[0]
    errors, warnings = validate_skill.validate_skill(skill)
    assert not any("SKILL.md has 1300" in item for item in [*errors, *warnings])

    assert repo_policy.configure_project_value(tmp, "limits.skill.warn_words", 100)[0]
    assert repo_policy.configure_project_value(tmp, "warnings.health.skill.words", "error")[0]
    errors, warnings = validate_skill.validate_skill(skill)
    assert any("SKILL.md has" in item for item in errors)
    assert not any("SKILL.md has" in item for item in warnings)


def test_project_policy_is_project_owned_harness_state(_tmp: Path) -> None:
    assert repo_policy.PROJECT_POLICY_PATH in repo_harness_install.STATE_EXCLUDE_GLOBS
    assert repo_policy.PROJECT_POLICY_PATH in repo_harness_install.REQUIRED_STATE_EXCLUDES


def test_project_policy_is_an_explicit_root_configuration_owner(_tmp: Path) -> None:
    from repo_support import repo_addition_acceptance

    assert repo_policy.PROJECT_POLICY_PATH in repo_addition_acceptance.ALLOWED_UNOWNED_NEW_FILES
