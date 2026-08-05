"""Focused self-tests for the managed failure feedback ledger."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from repo_support import repo_local_ai
from repo_support import repo_qol


FEEDBACK_LOG = Path(".agents/local-ai/cache/feedback/failure-feedback.jsonl")


def read_feedback(root: Path) -> list[dict[str, object]]:
    path = root / FEEDBACK_LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_feedback_record_appends_jsonl_and_normalizes_context_paths(tmp: Path) -> None:
    from repo_support import repo_feedback

    repo_feedback.record_feedback(
        tmp,
        target_kind="workflow",
        target="story-flow",
        summary="finish failed",
        bad="required proof was missing",
        good="finish found the issue",
        context_paths=["automations\\story-flow\\runs\\run-a\\run.json"],
        caller="agent-a",
        trigger_command="workflow finish",
        failure_type="missing-proof",
        first_failing_fact="required-proof.md missing",
        raw_output_path=".agents/local-ai/cache/command-output/raw.txt",
        output_digest="abc123",
        suggested_next_command="python -B .agents/manage.py workflow resume --name story-flow --run-id run-a",
        source_tool="workflow-manager.finish-run",
    )
    repo_feedback.record_feedback(
        tmp,
        target_kind="workflow",
        target="story-flow",
        summary="second failure",
        bad="another issue",
    )

    entries = read_feedback(tmp)

    assert len(entries) == 2
    assert entries[0]["schema_version"] == 1
    assert entries[0]["target_kind"] == "workflow"
    assert entries[0]["target"] == "story-flow"
    assert entries[0]["caller"] == "agent-a"
    assert entries[0]["context_paths"] == ["automations/story-flow/runs/run-a/run.json"]
    assert entries[0]["raw_output_path"] == ".agents/local-ai/cache/command-output/raw.txt"
    assert entries[1]["summary"] == "second failure"


def test_feedback_summary_groups_repeated_failures_by_fingerprint(tmp: Path) -> None:
    from repo_support import repo_feedback

    for summary in ("first", "second"):
        repo_feedback.record_feedback(
            tmp,
            target_kind="skill",
            target="skill-manager",
            summary=summary,
            bad="generated routing was stale",
            failure_type="stale-generated-or-cache",
            first_failing_fact="generated routing is stale",
            suggested_next_command="python -B .agents/manage.py sync",
        )
    repo_feedback.record_feedback(
        tmp,
        target_kind="workflow",
        target="story-flow",
        summary="workflow failure",
        bad="proof missing",
        failure_type="missing-proof",
        first_failing_fact="required-proof.md missing",
    )

    report = repo_feedback.summary_report(tmp, target="skill-manager")

    assert report["summary"]["entry_count"] == 2
    assert report["summary"]["group_count"] == 1
    group = report["groups"][0]
    assert group["target_kind"] == "skill"
    assert group["target"] == "skill-manager"
    assert group["count"] == 2
    assert group["failure_type"] == "stale-generated-or-cache"
    assert group["suggested_next_command"] == "python -B .agents/manage.py sync"


def test_feedback_command_records_summarizes_and_exports(tmp: Path) -> None:
    from repo_support import repo_feedback

    record_status = repo_feedback.feedback_group(
        [
            "record",
            "--target-kind",
            "repo",
            "--target",
            "harness",
            "--summary",
            "check failed",
            "--bad",
            "validation failed",
            "--context",
            ".agents\\local-ai\\cache\\last-validation.txt",
            "--caller",
            "agent-a",
            "--format",
            "json",
        ],
        tmp,
    )
    summary_status = repo_feedback.feedback_group(
        ["summary", "--target", "harness", "--summary", "--compact", "--format", "json"],
        tmp,
    )
    export_status = repo_feedback.feedback_group(
        ["export", "--target", "harness", "--min-count", "1", "--output", "evidence/feedback", "--format", "json"],
        tmp,
    )

    assert record_status == 0
    assert summary_status == 0
    assert export_status == 0
    assert (tmp / "evidence/feedback/feedback-candidates.json").exists()
    assert (tmp / "evidence/feedback/feedback-candidates.md").exists()
    try:
        repo_feedback.feedback_group(
            ["export", "--target", "harness", "--min-count", "1", "--output", "evidence/feedback", "--format", "json"],
            tmp,
        )
    except SystemExit as exc:
        assert "never overwrites" in str(exc)
    else:
        raise AssertionError("feedback export overwrote existing candidate files")
    try:
        repo_feedback.feedback_group(
            ["export", "--target", "harness", "--min-count", "1", "--output", "docs/feedback", "--format", "json"],
            tmp,
        )
    except SystemExit as exc:
        assert "evidence directory or workflow run artifact" in str(exc)
    else:
        raise AssertionError("feedback export accepted an arbitrary repository directory")


def test_feedback_eval_packet_requires_review_and_preserves_surface_model_axes(tmp: Path) -> None:
    from repo_support import repo_feedback

    corrections = tmp / "evidence/corrections.json"
    corrections.parent.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "tool": "skill-manager.corrections",
        "review_state": "reviewed",
        "reviewed": True,
        "reviewed_by": "owner-a",
        "events": [
            {
                "id": "copilot-unknown-model",
                "target_kind": "repo",
                "target": "harness",
                "task_class": "bounded-implementation",
                "host_surface": "github-copilot",
                "model_provider": "unknown",
                "model": "",
                "semantic_profile": "implementation-medium",
                "prompt": "Resolve a route without model attestation.",
                "incorrect_behavior": "Assume OpenAI-specific behavior.",
                "correct_behavior": "Use the generic overlay and only attested Copilot capabilities.",
                "acceptance_criteria": ["The overlay is generic-v1."],
                "source_refs": ["docs/reference/model-compatibility-and-routing.md"],
            }
        ],
    }
    payload["reviewed_events_sha256"] = repo_feedback.correction_review_sha256(
        payload["reviewed_by"], payload["events"]
    )
    corrections.write_text(json.dumps(payload), encoding="utf-8", newline="\n")

    status = repo_feedback.feedback_group(
        [
            "eval-packet",
            "--corrections",
            "evidence/corrections.json",
            "--output",
            "evidence/correction-evals.json",
            "--format",
            "json",
        ],
        tmp,
    )

    assert status == 0
    report = json.loads((tmp / "evidence/correction-evals.json").read_text(encoding="utf-8"))
    assert report["summary"]["case_count"] == 1
    case = report["cases"][0]
    assert case["host_surface"] == "github-copilot"
    assert case["model_provider"] == "unknown"
    assert case["semantic_profile"] == "implementation-medium"
    assert case["status"] == "candidate"
    assert case["source_review_status"] == "reviewed"
    assert case["source_reviewed_by"] == "owner-a"

    payload["reviewed"] = False
    corrections.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    try:
        repo_feedback.build_eval_packet(tmp, "evidence/corrections.json")
    except SystemExit as exc:
        assert "reviewed must be true" in str(exc)
    else:
        raise AssertionError("unreviewed correction packet was accepted")


def test_feedback_eval_packet_binds_review_and_never_overwrites_source(tmp: Path) -> None:
    from repo_support import repo_feedback

    corrections = tmp / "evidence/corrections.json"
    corrections.parent.mkdir(parents=True)
    event = {
        "id": "portable-candidate",
        "target_kind": "repo",
        "target": "harness",
        "task_class": "validation",
        "host_surface": "unknown",
        "model_provider": "unknown",
        "semantic_profile": "evidence-mini",
        "prompt": "Validate a portable correction.",
        "correct_behavior": "Keep it provider-neutral.",
        "acceptance_criteria": ["The case remains a candidate."],
        "source_refs": ["docs/reference/model-compatibility-and-routing.md"],
    }
    payload = {
        "schema_version": 1,
        "tool": "skill-manager.corrections",
        "review_state": "reviewed",
        "reviewed": True,
        "reviewed_by": "owner-a",
        "events": [event],
    }
    payload["reviewed_events_sha256"] = repo_feedback.correction_review_sha256(
        payload["reviewed_by"], payload["events"]
    )
    corrections.write_text(json.dumps(payload), encoding="utf-8", newline="\n")

    report = repo_feedback.build_eval_packet(tmp, "evidence/corrections.json")
    try:
        repo_feedback.write_eval_packet(tmp, report, "evidence/corrections.json")
    except SystemExit as exc:
        assert "must not alias" in str(exc)
    else:
        raise AssertionError("eval-packet overwrote its reviewed source")
    assert json.loads(corrections.read_text(encoding="utf-8"))["tool"] == "skill-manager.corrections"

    existing = tmp / "evidence/existing.json"
    existing.write_text("{}\n", encoding="utf-8", newline="\n")
    try:
        repo_feedback.write_eval_packet(tmp, report, "evidence/existing.json")
    except SystemExit as exc:
        assert "never overwrites" in str(exc)
    else:
        raise AssertionError("eval-packet overwrote an existing evidence file")
    try:
        repo_feedback.write_eval_packet(tmp, report, ".agents/skills/demo/suites/candidates.json")
    except SystemExit as exc:
        assert "never an active suite" in str(exc)
    else:
        raise AssertionError("eval-packet wrote directly into an active suite")
    alias_parent = tmp / "alias" / "evidence"
    alias_parent.parent.mkdir(parents=True)
    try:
        alias_parent.symlink_to(corrections.parent, target_is_directory=True)
    except OSError:
        alias_parent = None
    if alias_parent is not None:
        try:
            repo_feedback.write_eval_packet(tmp, report, "alias/evidence/candidate.json")
        except SystemExit as exc:
            assert "symlink or reparse alias" in str(exc)
        else:
            raise AssertionError("eval-packet accepted a symlinked output parent")

    corrections.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    try:
        repo_feedback.write_eval_packet(tmp, report, "evidence/stale-source.json")
    except SystemExit as exc:
        assert "source changed" in str(exc)
    else:
        raise AssertionError("eval-packet wrote cases built from stale source bytes")

    corrections.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    atomic_output = tmp / "evidence/atomic-failure.json"
    with patch.object(repo_feedback.os, "link", side_effect=OSError("injected publication failure")):
        try:
            repo_feedback.write_eval_packet(tmp, report, "evidence/atomic-failure.json")
        except SystemExit as exc:
            assert "failed safely" in str(exc)
        else:
            raise AssertionError("eval-packet ignored an atomic publication failure")
    assert not atomic_output.exists()
    assert not list(atomic_output.parent.glob(f".{atomic_output.name}.*.pending"))

    payload["events"].append({**event, "id": "added-after-review"})
    corrections.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    try:
        repo_feedback.build_eval_packet(tmp, "evidence/corrections.json")
    except SystemExit as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("events changed after review were accepted")


def test_feedback_review_digest_is_public_deterministic_and_unicode_safe(tmp: Path) -> None:
    from repo_support import repo_feedback

    event = {
        "id": "unicode-behavior",
        "target_kind": "repo",
        "target": "harness",
        "task_class": "validation",
        "host_surface": "claude-code",
        "model_provider": "anthropic",
        "semantic_profile": "evidence-mini",
        "prompt": "Préserve Unicode.",
        "correct_behavior": "Keep café and naïve intact.",
        "acceptance_criteria": ["The digest is deterministic."],
        "source_refs": ["docs/reference/model-compatibility-and-routing.md"],
    }
    other = {**event, "id": "alpha-event", "prompt": "First after sorting."}
    packet = {
        "schema_version": 1,
        "tool": "skill-manager.corrections",
        "review_state": "review-input",
        "reviewed": True,
        "reviewed_by": "owner-a",
        "events": [event, other],
    }
    evidence = tmp / "evidence"
    evidence.mkdir(parents=True)
    first = evidence / "first.json"
    second = evidence / "second.json"
    first.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8", newline="\n")
    packet["events"] = list(reversed(packet["events"]))
    second.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8", newline="\n")

    first_report = repo_feedback.build_review_digest_report(tmp, "evidence/first.json")
    second_report = repo_feedback.build_review_digest_report(tmp, "evidence/second.json")

    assert first_report["reviewed_events_sha256"] == second_report["reviewed_events_sha256"]
    assert first_report["canonicalization"]["encoding"] == "UTF-8"
    permuted = [dict(reversed(list(item.items()))) for item in packet["events"]]
    assert repo_feedback.correction_review_sha256("owner-a", permuted) == first_report["reviewed_events_sha256"]
    assert first_report["reviewed_events_sha256"] == "c89aa98060bde2cce05b06fdf7aeacf76d9bb287bfbc5692c760529af425c8d6", first_report[
        "reviewed_events_sha256"
    ]
    assert repo_feedback.correction_review_sha256("different-owner", permuted) != first_report[
        "reviewed_events_sha256"
    ]
    reviewed_packet = {
        **packet,
        "review_state": "reviewed",
        "reviewed_by": "different-owner",
        "reviewed_events_sha256": first_report["reviewed_events_sha256"],
    }
    assert any(
        "reviewer-bound" in issue
        for issue in repo_feedback.correction_packet_issues(reviewed_packet)
    )


def test_feedback_eval_packet_bounds_event_count_and_input_size(tmp: Path) -> None:
    from repo_support import repo_feedback

    packet = {
        "schema_version": 1,
        "tool": "skill-manager.corrections",
        "review_state": "reviewed",
        "reviewed": True,
        "reviewed_by": "owner-a",
        "reviewed_events_sha256": "0" * 64,
        "events": [{}] * (repo_feedback.MAX_CORRECTION_EVENTS + 1),
    }
    issues = repo_feedback.correction_packet_issues(packet)
    assert issues == ["corrections.events must contain at most 200 events"]

    packet["schema_version"] = True
    packet["events"] = [{}]
    issues = repo_feedback.correction_packet_issues(packet)
    assert "corrections.schema_version must be 1" in issues

    packet["schema_version"] = 1
    packet["events"] = [
        {
            "id": "blank-value",
            "target_kind": "repo",
            "target": "   ",
            "task_class": "validation",
            "host_surface": "unknown",
            "model_provider": "unknown",
            "semantic_profile": "evidence-mini",
            "prompt": "Validate.",
            "correct_behavior": "Reject whitespace.",
            "acceptance_criteria": ["Reject it."],
            "source_refs": ["docs/reference/model-compatibility-and-routing.md"],
        }
    ]
    packet["reviewed_events_sha256"] = repo_feedback.correction_review_sha256(
        packet["reviewed_by"], packet["events"]
    )
    assert any("target must be a non-empty" in issue for issue in repo_feedback.correction_packet_issues(packet))
    schema_path = Path(__file__).resolve().parents[2] / "assets/schemas/correction-events-v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["properties"]["events"]["items"]["properties"]["target"]["pattern"] == ".*\\S.*"

    oversized = tmp / "evidence/oversized.json"
    oversized.parent.mkdir(parents=True)
    oversized.write_bytes(b" " * (repo_feedback.MAX_CORRECTION_PACKET_BYTES + 1))
    try:
        repo_feedback.build_eval_packet(tmp, "evidence/oversized.json")
    except SystemExit as exc:
        assert "input limit" in str(exc)
    else:
        raise AssertionError("oversized corrections packet was accepted")


def test_feedback_clear_dry_run_reports_counts_without_mutation(tmp: Path) -> None:
    from repo_support import repo_feedback

    repo_feedback.record_feedback(
        tmp,
        target_kind="skill",
        target="skill-manager",
        summary="check failed",
        bad="generated routing was stale",
    )
    action_plan = tmp / "automations/feedback-improvement-workflow/runs/run-a/action-plan.md"
    action_plan.parent.mkdir(parents=True)
    action_plan.write_text("# Action Plan\n", encoding="utf-8", newline="\n")
    before = (tmp / FEEDBACK_LOG).read_text(encoding="utf-8")

    report = repo_feedback.clear_report(
        tmp,
        all_targets=True,
        confirm_truncate=True,
        reason="processed into action plan",
        action_plan=str(action_plan),
        dry_run=True,
    )

    assert report["ok"] is True
    assert report["dry_run"] is True
    assert report["entry_count_before"] == 1
    assert report["bytes_before"] > 0
    assert (tmp / FEEDBACK_LOG).read_text(encoding="utf-8") == before


def test_feedback_clear_truncates_with_confirmation_and_action_plan(tmp: Path) -> None:
    from repo_support import repo_feedback

    repo_feedback.record_feedback(
        tmp,
        target_kind="workflow",
        target="story-flow",
        summary="finish failed",
        bad="proof missing",
    )
    action_plan = tmp / "automations/feedback-improvement-workflow/runs/run-a/action-plan.md"
    action_plan.parent.mkdir(parents=True)
    action_plan.write_text("# Action Plan\n", encoding="utf-8", newline="\n")

    report = repo_feedback.clear_report(
        tmp,
        all_targets=True,
        confirm_truncate=True,
        reason="processed into action plan",
        action_plan=str(action_plan),
        dry_run=False,
    )

    assert report["ok"] is True
    assert report["status"] == "cleared"
    assert report["entry_count_before"] == 1
    assert report["cleared_path"] == ".agents/local-ai/cache/feedback/failure-feedback.jsonl"
    assert report["action_plan_path"] == "automations/feedback-improvement-workflow/runs/run-a/action-plan.md"
    assert (tmp / FEEDBACK_LOG).read_text(encoding="utf-8") == ""


def test_feedback_clear_rejects_unsafe_or_incomplete_requests_without_mutation(tmp: Path) -> None:
    from repo_support import repo_feedback

    repo_feedback.record_feedback(
        tmp,
        target_kind="repo",
        target="harness",
        summary="check failed",
        bad="validation failed",
    )
    action_plan = tmp / "automations/feedback-improvement-workflow/runs/run-a/action-plan.md"
    action_plan.parent.mkdir(parents=True)
    action_plan.write_text("# Action Plan\n", encoding="utf-8", newline="\n")
    outside = tmp.parent / f"{tmp.name}-outside-action-plan.md"
    before = (tmp / FEEDBACK_LOG).read_text(encoding="utf-8")

    bad_requests = [
        {"all_targets": False, "confirm_truncate": True, "action_plan": str(action_plan)},
        {"all_targets": True, "confirm_truncate": False, "action_plan": str(action_plan)},
        {"all_targets": True, "confirm_truncate": True, "action_plan": "missing.md"},
        {"all_targets": True, "confirm_truncate": True, "action_plan": str(outside)},
    ]

    for request in bad_requests:
        try:
            repo_feedback.clear_report(
                tmp,
                reason="processed into action plan",
                dry_run=False,
                **request,
            )
        except SystemExit:
            pass
        else:
            raise AssertionError(f"expected clear_report to reject {request}")
        assert (tmp / FEEDBACK_LOG).read_text(encoding="utf-8") == before


def test_what_now_from_command_failure_appends_feedback_and_success_does_not(tmp: Path) -> None:
    def failing_capture(_root: Path, command: str, *, timeout: int = 600) -> dict[str, object]:
        return {
            "ok": False,
            "status": 1,
            "command": command,
            "output_tail": "ERROR: generated routing is stale",
            "distilled_output": "Notable lines:\n- ERROR: generated routing is stale",
            "raw_output_path": ".agents/local-ai/cache/command-output/raw.txt",
            "output_summary": {"bytes": 200, "lines": 3, "digest": "abc123"},
        }

    old_capture = repo_qol.run_capture_shell
    try:
        repo_qol.run_capture_shell = failing_capture
        report = repo_qol.what_now_report(tmp, from_command="python -B .agents/manage.py check")
    finally:
        repo_qol.run_capture_shell = old_capture

    entries = read_feedback(tmp)
    assert report["failure_type"] == "stale-generated-or-cache"
    assert len(entries) == 1
    assert entries[0]["target_kind"] == "skill"
    assert entries[0]["target"] == "skill-manager"
    assert entries[0]["trigger_command"] == "python -B .agents/manage.py check"
    assert entries[0]["output_digest"] == "abc123"

    def passing_capture(_root: Path, command: str, *, timeout: int = 600) -> dict[str, object]:
        return {"ok": True, "status": 0, "command": command, "output_tail": "ok"}

    old_capture = repo_qol.run_capture_shell
    try:
        repo_qol.run_capture_shell = passing_capture
        repo_qol.what_now_report(tmp, from_command="python -B .agents/manage.py check")
    finally:
        repo_qol.run_capture_shell = old_capture

    assert len(read_feedback(tmp)) == 1


def test_what_now_pycache_failure_points_to_syntax_check_guard(tmp: Path) -> None:
    def pycache_capture(_root: Path, command: str, *, timeout: int = 600) -> dict[str, object]:
        return {
            "ok": False,
            "status": 1,
            "command": command,
            "output_tail": "ERROR: __pycache__ directory found under .agents/skills/local-ai-helper/scripts",
            "distilled_output": "Notable lines:\n- ERROR: __pycache__ directory found under .agents/skills/local-ai-helper/scripts",
            "raw_output_path": ".agents/local-ai/cache/command-output/check.txt",
            "output_summary": {"bytes": 120, "lines": 2, "digest": "pycache123"},
        }

    old_capture = repo_qol.run_capture_shell
    try:
        repo_qol.run_capture_shell = pycache_capture
        report = repo_qol.what_now_report(tmp, from_command="python -B .agents/manage.py check")
    finally:
        repo_qol.run_capture_shell = old_capture

    entries = read_feedback(tmp)
    assert report["failure_type"] == "stale-generated-or-cache"
    assert "syntax-check --paths .agents/skills automations --format json" in report["next_command"]
    assert len(entries) == 1
    assert entries[0]["failure_type"] == "stale-generated-or-cache"
    assert "__pycache__" in entries[0]["first_failing_fact"]
    assert "syntax-check --paths .agents/skills automations --format json" in entries[0]["suggested_next_command"]


def test_finish_work_failure_appends_feedback_for_failed_checks(tmp: Path) -> None:
    def fake_capture(_root: Path, command: list[str], *, timeout: int = 90) -> dict[str, object]:
        command_text = " ".join(command)
        failed = "check-changed" in command_text
        return {
            "ok": not failed,
            "status": 1 if failed else 0,
            "command": command_text,
            "output_tail": "ERROR: changed scope failed" if failed else "ok",
            "distilled_output": "Notable lines:\n- ERROR: changed scope failed" if failed else "",
            "raw_output_path": ".agents/local-ai/cache/command-output/check-changed.txt" if failed else "",
            "output_summary": {"bytes": 120, "lines": 4, "digest": "def456"},
            "elapsed_seconds": 0.01,
        }

    old_capture = repo_qol.run_capture
    try:
        repo_qol.run_capture = fake_capture
        report = repo_qol.finish_work_report(tmp, deep=False, skip_benchmark=True)
    finally:
        repo_qol.run_capture = old_capture

    entries = read_feedback(tmp)
    assert report["ok"] is False
    assert len(entries) == 1
    assert entries[0]["target_kind"] == "skill"
    assert entries[0]["target"] == "skill-manager"
    assert ".agents/manage.py check-changed" in entries[0]["trigger_command"]
    assert entries[0]["raw_output_path"] == ".agents/local-ai/cache/command-output/check-changed.txt"


def test_run_with_failure_triage_appends_feedback_for_failed_validation(tmp: Path) -> None:
    def runner() -> int:
        print("ERROR: generated routing is stale")
        return 1

    status = repo_local_ai.run_with_failure_triage(
        tmp,
        "check-additions",
        runner,
        ready_func=lambda _root: False,
        policy_func=lambda _root: (False, "test policy disabled"),
    )

    entries = read_feedback(tmp)
    assert status == 1
    assert len(entries) == 1
    assert entries[0]["target_kind"] == "skill"
    assert entries[0]["target"] == "skill-manager"
    assert entries[0]["trigger_command"] == "check-additions"
    assert entries[0]["failure_type"] == "stale-generated-or-cache"
    assert entries[0]["context_paths"] == [".agents/local-ai/cache/last-validation.txt"]
