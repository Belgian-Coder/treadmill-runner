"""Final-claim evidence projected directly from the authoritative finish run."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


EXPECTED_PHASE_ARGV_TAILS: dict[str, list[str]] = {
    "workflow-hooks": ["-B", ".agents/manage.py", "workflow", "hooks", "--all", "--check", "--format", "json"],
    "clean-context-proof": ["-B", ".agents/manage.py", "clean-context-proof", "--summary", "--compact", "--format", "json"],
    "install-harness-smoke-fast": ["-B", ".agents/manage.py", "install-harness-smoke", "--fast", "--format", "json"],
    "user-story-workflow-smoke": [
        "-B", ".agents/manage.py", "workflow", "smoke", "--name", "user-story-workflow",
        "--summary", "--compact", "--format", "json",
    ],
    "workflow-evals": [
        "-B", ".agents/manage.py", "workflow", "eval", "--all", "--summary", "--compact", "--format", "json",
    ],
    "repo-check": ["-B", ".agents/manage.py", "check"],
    "benchmark-doctor": ["-B", ".agents/manage.py", "benchmark", "doctor"],
}


def _python_command(argv: object) -> list[str]:
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        return []
    try:
        executable_matches = os.path.normcase(str(Path(argv[0]).resolve())) == os.path.normcase(str(Path(sys.executable).resolve()))
    except OSError:
        return []
    return argv if executable_matches else []


def _phase_command_matches(check: dict[str, Any], *, deep_validation: bool) -> bool:
    phase = str(check.get("phase") or "")
    argv = _python_command(check.get("command_argv"))
    if not argv:
        return False
    tail = argv[1:]
    if phase == "changed-scope":
        expected = [
            "-B", ".agents/manage.py", "check-changed",
            *(["--deep"] if deep_validation else []),
            "--record-progress", "--summary", "--compact", "--format", "json",
        ]
        return tail == expected
    if phase == "workflow-run-index":
        return (
            len(tail) == 8
            and tail[:4] == ["-B", ".agents/manage.py", "index-workflow-runs", "--name"]
            and bool(tail[4].strip())
            and tail[5:] == ["--check", "--format", "json"]
        )
    expected = EXPECTED_PHASE_ARGV_TAILS.get(phase)
    return expected is not None and tail == expected


def _changed_scope_receipt_matches(
    check: dict[str, Any],
    finish_report: dict[str, Any],
    *,
    expected_profile: str,
) -> bool:
    execution_mode = str(check.get("execution_mode") or "subprocess")
    if execution_mode == "subprocess":
        return True
    if execution_mode != "validation-progress-receipt":
        return False
    receipt = (
        check.get("validation_receipt")
        if isinstance(check.get("validation_receipt"), dict)
        else {}
    )
    fingerprint = (
        finish_report.get("input_fingerprint")
        if isinstance(finish_report.get("input_fingerprint"), dict)
        else {}
    )
    digest = str(fingerprint.get("digest") or "")
    required_values = receipt.get("required_check_ids")
    passed_values = receipt.get("passed_check_ids")
    if (
        not isinstance(required_values, list)
        or not isinstance(passed_values, list)
        or not all(isinstance(item, str) and item for item in required_values)
        or not all(isinstance(item, str) and item for item in passed_values)
    ):
        return False
    required = set(required_values)
    passed = set(passed_values)
    failed_check_count = receipt.get("failed_check_count")
    try:
        age_value = receipt["age_seconds"]
        max_age_value = receipt["max_age_seconds"]
        if isinstance(age_value, bool) or isinstance(max_age_value, bool):
            return False
        age_seconds = float(age_value)
        max_age_seconds = float(max_age_value)
    except (KeyError, TypeError, ValueError):
        return False
    return (
        receipt.get("schema_version") == 1
        and receipt.get("verified") is True
        and receipt.get("source_schema_version") == 1
        and receipt.get("source_tool") == "skill-manager.validation-progress"
        and receipt.get("command_argv") == check.get("command_argv")
        and bool(digest)
        and str(receipt.get("input_fingerprint_digest") or "") == digest
        and receipt.get("environment_fingerprint") == fingerprint.get("runtime")
        and str(receipt.get("post_input_fingerprint_digest") or "") == digest
        and receipt.get("input_stable") is True
        and str(receipt.get("profile") or "") == expected_profile
        and str(receipt.get("side_effect_boundary") or "")
        == "repository-read-only-and-temporary-restored"
        and type(failed_check_count) is int
        and failed_check_count == 0
        and bool(str(receipt.get("recorded_at") or "").strip())
        and 0 <= age_seconds <= max_age_seconds
        and max_age_seconds > 0
        and bool(required)
        and len(required) == len(required_values)
        and len(passed) == len(passed_values)
        and required == passed
    )


def _receipt_item(
    item_id: str,
    *,
    status: str,
    required: bool,
    evidence: str,
    next_command: str = "",
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": item_id,
        "status": status,
        "required": required,
        "evidence": evidence,
    }
    if next_command:
        item["next_command"] = next_command
    return item


def _receipt_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    required = [item for item in items if item.get("required")]
    return {
        "item_count": len(items),
        "required_count": len(required),
        "required_passed_count": sum(1 for item in required if item.get("status") == "passed"),
        "required_missing_count": sum(1 for item in required if item.get("status") != "passed"),
        "not_proven_count": sum(1 for item in items if item.get("status") == "not-proven"),
    }


def finish_claim_report(
    finish_report: dict[str, Any],
    *,
    deep: bool = False,
    release_full: bool = False,
) -> dict[str, Any]:
    """Build claim evidence without launching another validation command graph."""
    readiness = (
        finish_report.get("finish_readiness")
        if isinstance(finish_report.get("finish_readiness"), dict)
        else {}
    )
    navigation = finish_report.get("navigation") if isinstance(finish_report.get("navigation"), dict) else {}
    coverage = readiness.get("review_coverage") if isinstance(readiness.get("review_coverage"), dict) else {}
    review_packet = (
        finish_report.get("review_packet")
        if isinstance(finish_report.get("review_packet"), dict)
        else {}
    )
    checks = finish_report.get("checks") if isinstance(finish_report.get("checks"), list) else []
    failed = [item for item in checks if isinstance(item, dict) and not bool(item.get("ok"))]
    phases = {str(item.get("phase") or "") for item in checks if isinstance(item, dict)}
    next_command = str(
        readiness.get("next_command")
        or finish_report.get("next_command")
        or "python -B .agents/manage.py finish --summary --compact --format json"
    )
    profile = "release-full" if release_full else "deep" if deep else "changed"
    navigation_ok = navigation.get("status") == "fresh"
    navigation_next_command = str(
        navigation.get("next_command")
        or "python -B .agents/skills/repo-navigation/scripts/repo_navigation.py update --target . --write --format json"
    )
    review_status = str(review_packet.get("status") or "")
    review_required = review_status == "over-budget"
    review_ok = review_status == "within-budget" or (
        review_required
        and coverage.get("status") in {"complete", "no-review-units"}
        and int(coverage.get("pending_review_unit_count", 0) or 0) == 0
    )
    changed_checks = [
        item for item in checks if isinstance(item, dict) and item.get("phase") == "changed-scope"
    ]
    required_phases = {"changed-scope"}
    if release_full:
        required_phases.update(
            {
                "workflow-hooks",
                "clean-context-proof",
                "install-harness-smoke-fast",
                "user-story-workflow-smoke",
                "workflow-evals",
                "repo-check",
                "benchmark-doctor",
            }
        )
    expected_validation_profile = "deep" if deep or release_full else "changed"
    selected_phase_ids = (
        finish_report.get("selected_phase_ids")
        if isinstance(finish_report.get("selected_phase_ids"), list)
        else []
    )
    selected_phase_set = {str(item) for item in selected_phase_ids if str(item)}
    check_phase_ids = [
        str(item.get("phase") or "") for item in checks if isinstance(item, dict) and str(item.get("phase") or "")
    ]
    changed_profile_ok = (
        len(changed_checks) == 1
        and _phase_command_matches(
            changed_checks[0],
            deep_validation=expected_validation_profile == "deep",
        )
        and _changed_scope_receipt_matches(
            changed_checks[0],
            finish_report,
            expected_profile=expected_validation_profile,
        )
    )
    phase_commands_ok = bool(checks) and all(
        _phase_command_matches(item, deep_validation=expected_validation_profile == "deep")
        and (
            str(item.get("phase") or "") != "changed-scope"
            or _changed_scope_receipt_matches(
                item,
                finish_report,
                expected_profile=expected_validation_profile,
            )
        )
        for item in checks
        if isinstance(item, dict)
    )
    changed_validation_ok = (
        changed_profile_ok
        and "changed-scope" in phases
        and not any(str(item.get("phase") or "") == "changed-scope" for item in failed)
    )
    contract_ok = (
        finish_report.get("tool") == "repo-finish"
        and finish_report.get("profile") == profile
        and bool(readiness)
        and bool(review_packet)
        and required_phases.issubset(phases)
        and finish_report.get("selected_validation_profile") == expected_validation_profile
        and selected_phase_ids == check_phase_ids
        and changed_profile_ok
        and phase_commands_ok
    )
    finish_ok = (
        bool(finish_report.get("ok"))
        and readiness.get("ok") is True
        and contract_ok
        and not failed
    )
    items = [
        _receipt_item(
            "navigation",
            status="passed" if navigation_ok else "missing",
            required=True,
            evidence=f"navigation_status={navigation.get('status', 'unknown')}",
            next_command=navigation_next_command if not navigation_ok else "",
        ),
        _receipt_item(
            "review-coverage",
            status="passed" if review_ok else "missing",
            required=True,
            evidence=(
                f"review_required={review_required}; "
                f"review_coverage_status={coverage.get('status', 'unknown')}; "
                f"pending_review_unit_count={coverage.get('pending_review_unit_count', 0)}"
            ),
            next_command=next_command if not review_ok else "",
        ),
        _receipt_item(
            "changed-validation",
            status="passed" if changed_validation_ok else "failed",
            required=True,
            evidence=(
                f"changed_scope_present={'changed-scope' in phases}; "
                f"execution_mode={str(changed_checks[0].get('execution_mode') or 'subprocess') if changed_checks else 'missing'}; "
                f"changed_profile_ok={changed_profile_ok}; failed_check_count={len(failed)}"
            ),
            next_command=next_command if not changed_validation_ok else "",
        ),
        _receipt_item(
            "selected-finish-checks",
            status="passed" if finish_ok else "failed",
            required=True,
            evidence=(
                f"tool={finish_report.get('tool', 'missing')}; expected_profile={profile}; "
                f"report_profile={finish_report.get('profile', 'missing')}; "
                f"selected_validation_profile={finish_report.get('selected_validation_profile', 'missing')}; "
                f"selected_phases={','.join(sorted(phases))}; "
                f"declared_selected_phases={','.join(str(item) for item in selected_phase_ids)}; "
                f"missing_required_phases={','.join(sorted(required_phases - phases))}; "
                f"changed_profile_ok={changed_profile_ok}; "
                f"phase_commands_ok={phase_commands_ok}; "
                f"readiness_present={bool(readiness)}; review_packet_present={bool(review_packet)}; "
                f"failed_check_count={len(failed)}"
            ),
            next_command=next_command if not finish_ok else "",
        ),
        _receipt_item(
            "external-ci",
            status="not-proven",
            required=False,
            evidence="External CI is outside local finish gates.",
        ),
    ]
    supported = all(item.get("status") == "passed" for item in items if item.get("required"))
    missing = [str(item.get("id")) for item in items if item.get("required") and item.get("status") != "passed"]
    return {
        "schema_version": 1,
        "tool": "skill-manager.finish-claim",
        "ok": supported,
        "status": "supported" if supported else "blocked",
        "completion_supported": supported,
        "profile": profile,
        "missing_evidence": missing,
        "next_command": (
            "python -B .agents/manage.py commit-readiness"
            if supported
            else navigation_next_command
            if not navigation_ok
            else next_command
        ),
        "claim_receipt": {
            "schema_version": 1,
            "status": "supported" if supported else "blocked",
            "summary": _receipt_summary(items),
            "items": items,
            "boundary": (
                "This receipt projects checks executed by the current finish invocation plus any exact current "
                "deep validation receipt verified against the same input, environment, profile, and required set. "
                "It does not rerun validation itself, push, merge, or prove external CI."
            ),
        },
    }


def summarize_claim_receipt(receipt: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    if not compact:
        return dict(receipt)
    items = receipt.get("items") if isinstance(receipt.get("items"), list) else []
    return {
        "schema_version": receipt.get("schema_version", 1),
        "status": receipt.get("status", "unknown"),
        "summary": receipt.get("summary", {}),
        "items": [
            {
                "id": item.get("id", ""),
                "status": item.get("status", "unknown"),
                "required": bool(item.get("required", False)),
                **({"next_command": item.get("next_command")} if item.get("next_command") else {}),
            }
            for item in items
            if isinstance(item, dict)
        ],
    }
