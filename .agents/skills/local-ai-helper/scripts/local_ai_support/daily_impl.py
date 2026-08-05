#!/usr/bin/env python3
"""Daily text-task helpers for the repo-local AI setup command."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import local_ai_routing
from local_ai_support import setup_impl as support
from local_ai_support import model_lease


LOG_LINE_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+\s+[A-Z](?:\s+|$)")


def daily_task_prompt(task: str, docs: list[dict[str, Any]]) -> str:
    task_instructions = {
        "validation-triage": "Summarize validation failures, likely causes, and next checks.",
        "code-review": "Review the supplied evidence for bugs, regressions, risks, and missing tests.",
        "implementation-planning": "Create a compact implementation plan from the supplied evidence.",
        "patch-draft": "Draft a patch suggestion in prose only. Do not claim to edit files.",
        "inventory-summary": "Summarize inventory facts, notable gaps, and follow-up checks.",
        "changelog-draft": "Draft concise changelog notes from the supplied evidence.",
        "changed-files-summary": "Summarize changed files, likely ownership boundaries, and review focus from the supplied evidence.",
        "failure-cluster": "Cluster validation failures by first failing fact, likely cause, and next deterministic check.",
        "test-gap-summary": "Summarize likely test gaps and candidate regression areas without writing tests.",
        "handoff-draft": "Draft concise handoff notes from supplied evidence without claiming completion or validation.",
        "duplicate-overlap-detection": "Identify duplicate or overlapping skill, workflow, or reference behavior from supplied evidence.",
    }
    payload = {
        "task": task,
        "instruction": task_instructions.get(task, "Summarize the supplied evidence."),
        "documents": docs,
    }
    return (
        "You are a repo-local assistant. Use only the supplied local evidence.\n"
        "Return one compact JSON object with keys summary, findings, suggestions, evidence, confidence. "
        "summary is under 220 characters. findings and suggestions are arrays of at most 5 short strings. "
        "evidence is an array of at most 3 objects with source and excerpt. "
        "Do not include markdown, code fences, destructive commands, network actions, credentials, or claims that files were edited.\n"
        f"Input: {json.dumps(payload, ensure_ascii=False, sort_keys=True)}\n"
    )


def daily_report_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["summary", "findings", "suggestions", "evidence", "confidence"],
        "properties": {
            "summary": {"type": "string", "maxLength": 240},
            "findings": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 180}},
            "suggestions": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 180}},
            "evidence": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "required": ["source", "excerpt"],
                    "properties": {
                        "source": {"type": "string", "maxLength": 80},
                        "excerpt": {"type": "string", "maxLength": 220},
                    },
                },
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }


def bounded_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def cleaned_plain_model_text(output: str) -> str:
    skipped_prefixes = (
        ">",
        "llama_",
        "load_backend:",
        "main:",
        "build ",
        "build:",
        "model ",
        "model:",
        "modalities ",
        "modalities:",
        "available commands:",
        "loading model",
        "repeat_last_n",
        "dry_multiplier",
        "top_k",
        "mirostat",
        "--no-conversation is not supported",
        "please use llama-completion instead",
        "/exit",
        "/regen",
        "/clear",
        "/read",
        "/glob",
    )
    rows: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered.startswith(skipped_prefixes):
            continue
        if LOG_LINE_RE.match(stripped):
            continue
        if "�" in stripped or "â" in stripped:
            continue
        rows.append(stripped)
    return "\n".join(rows)


def report_from_model_output(output: str, *, task: str) -> dict[str, Any]:
    for candidate in local_ai_routing.iter_json_objects(output):
        parsed = json.loads(candidate)
        if not isinstance(parsed, dict):
            continue
        summary = " ".join(str(parsed.get("summary", "")).split())
        if not summary:
            continue
        return {
            "ok": True,
            "summary": summary,
            "findings": support.as_text_list(parsed.get("findings", [])),
            "suggestions": support.as_text_list(parsed.get("suggestions", [])),
            "evidence": support.normalize_evidence(parsed.get("evidence", [])),
            "confidence": bounded_confidence(parsed.get("confidence", 0.0)),
            "issues": [],
            "structured_json": True,
        }
    cleaned = cleaned_plain_model_text(output)
    if not cleaned:
        return {
            "ok": False,
            "summary": "Local AI returned no usable task output.",
            "findings": [],
            "suggestions": [],
            "evidence": [],
            "confidence": 0.0,
            "issues": ["model output was empty or runtime noise only"],
            "structured_json": False,
        }
    return {
        "ok": True,
        "summary": cleaned[:900],
        "findings": [],
        "suggestions": [],
        "evidence": [{"source": "model", "excerpt": cleaned[:500]}],
        "confidence": 0.0,
        "issues": ["model output was plain text, not structured JSON"],
        "structured_json": False,
    }


def failure_class(reason: str) -> str:
    text = str(reason or "").strip().lower()
    if not text:
        return "unknown"
    if "memory-limit" in text or "memory limit" in text:
        return "memory-limit"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "unsupported" in text:
        return "unsupported-task"
    if "plain text" in text:
        return "plain-text"
    if "confidence" in text and "below" in text:
        return "low-confidence"
    if "json" in text or "schema" in text or "missing files array" in text:
        return "schema"
    if "missing required file" in text:
        return "missing-required-file"
    if "extra file" in text or "production file" in text:
        return "extra-file"
    if "path" in text and ("required" in text or "file" in text):
        return "path"
    if "targetframework" in text or "net10.0" in text or "framework" in text:
        return "wrong-framework"
    if "package" in text or "dependency" in text or "system.commandline" in text:
        return "wrong-package"
    if "mutation" in text:
        return "mutation"
    if "dotnet test failed" in text or "test failed" in text:
        return "test"
    if "compile" in text or "build failed" in text or "restore failed" in text:
        return "compile"
    if "model failed to start" in text or "model exited" in text:
        return "runtime"
    return "unknown"


def retry_decision(reason: str, policy: dict[str, Any]) -> dict[str, Any]:
    klass = failure_class(reason)
    retry_classes = {str(item) for item in policy.get("retry_failure_classes", [])}
    handoff_classes = {str(item) for item in policy.get("handoff_failure_classes", [])}
    retryable = klass in retry_classes
    handoff_now = klass in handoff_classes
    return {
        "failure_class": klass,
        "retryable": retryable,
        "handoff_now": handoff_now,
    }


def _run_text_completion_unleased(
    root: Path,
    *,
    task: str,
    profile: str,
    prompt: str,
    json_schema: dict[str, Any] | None = None,
) -> tuple[bool, str, dict[str, Any], list[str]]:
    model, runtime, config, issues = support.resolve_model_and_runtime(root, task=task, profile=profile)
    if model is None or runtime is None:
        return False, "", config, issues
    limits = dict(config.get("limits", local_ai_routing.DEFAULT_LIMITS))
    limits["output_tokens"] = max(int(limits.get("output_tokens", 192)), 512)
    config["limits"] = limits
    with tempfile.TemporaryDirectory() as temp_dir:
        prompt_path = Path(temp_dir) / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8", newline="\n")
        command = local_ai_routing.llama_command(runtime, model, config, prompt_path)
        if json_schema is not None:
            schema_path = Path(temp_dir) / "schema.json"
            schema_path.write_text(
                json.dumps(json_schema, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            command.extend(["--no-jinja", "--json-schema-file", str(schema_path)])
        try:
            completed = support.subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=int(limits.get("timeout_seconds", local_ai_routing.DEFAULT_LIMITS["timeout_seconds"])),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, "", config, ["model timed out"]
        except OSError as exc:
            return False, "", config, [f"model failed to start: {exc}"]
    if completed.returncode != 0:
        return False, completed.stdout, config, [f"model exited with {completed.returncode}"]
    return True, completed.stdout, config, []


def run_text_completion(
    root: Path,
    *,
    task: str,
    profile: str,
    prompt: str,
    json_schema: dict[str, Any] | None = None,
) -> tuple[bool, str, dict[str, Any], list[str]]:
    priority = "validation" if task == "validation-triage" else "interactive"
    with model_lease.exclusive_lease(
        root,
        profile=profile,
        role="text",
        priority=priority,
        command_kind="task",
        timeout_ms=0,
    ) as lease:
        if not lease.acquired:
            return False, "", {"lease": lease.report()}, ["local-ai-busy; deterministic fallback required"]
        started = time.perf_counter()
        ok, output, config, issues = _run_text_completion_unleased(
            root,
            task=task,
            profile=profile,
            prompt=prompt,
            json_schema=json_schema,
        )
        lease.inference_ms = int(max(0.0, time.perf_counter() - started) * 1000)
        config = dict(config)
        config["lease"] = lease.report()
        return ok, output, config, issues


def acceptance_issue(report: dict[str, Any], config: dict[str, Any], policy: dict[str, Any]) -> str:
    if not bool(report.get("ok")):
        issues = [str(issue) for issue in report.get("issues", [])]
        return issues[0] if issues else "model report was not accepted"
    if bool(policy.get("retry_on_plain_text", True)) and not bool(report.get("structured_json", False)):
        return "model output was not structured JSON"
    limits = config.get("limits", local_ai_routing.DEFAULT_LIMITS)
    try:
        threshold = float(limits.get("confidence_threshold", local_ai_routing.DEFAULT_LIMITS["confidence_threshold"]))
    except (TypeError, ValueError):
        threshold = float(local_ai_routing.DEFAULT_LIMITS["confidence_threshold"])
    confidence = bounded_confidence(report.get("confidence", 0.0))
    if bool(policy.get("retry_on_low_confidence", True)) and confidence < threshold:
        return f"model confidence {confidence:.2f} below threshold {threshold:.2f}"
    return ""


def attempt_record(
    *,
    profile: str,
    attempt: int,
    started: bool,
    accepted: bool,
    report: dict[str, Any] | None = None,
    issues: list[str] | None = None,
    failure_class_name: str = "",
    retryable: bool | None = None,
) -> dict[str, Any]:
    model_report = report or {}
    merged_issues = [str(issue) for issue in (issues or [])]
    merged_issues.extend(str(issue) for issue in model_report.get("issues", []))
    record = {
        "profile": profile,
        "attempt": attempt,
        "started": bool(started),
        "ok": bool(model_report.get("ok", False)),
        "accepted": bool(accepted),
        "confidence": bounded_confidence(model_report.get("confidence", 0.0)),
        "structured_json": bool(model_report.get("structured_json", False)),
        "issues": list(dict.fromkeys(issue for issue in merged_issues if issue)),
    }
    if failure_class_name:
        record["failure_class"] = failure_class_name
    if retryable is not None:
        record["retryable"] = bool(retryable)
    summary = str(model_report.get("summary", "")).strip()
    if summary:
        record["summary"] = " ".join(summary.split())[:240]
    return record


def run_daily_text_model(root: Path, task: str, prompt: str) -> dict[str, Any]:
    initial_config = local_ai_routing.load_config(root, task)
    if not initial_config.get("enabled"):
        reason = str(initial_config.get("reason", "Local AI is disabled."))
        return {
            "ok": False,
            "summary": "Local AI is unavailable for this task; use deterministic evidence.",
            "findings": [],
            "suggestions": ["Use deterministic evidence and skip the local model."],
            "evidence": [],
            "confidence": 0.0,
            "issues": [reason],
            "required": bool(initial_config.get("required", False)),
            "profile": "",
            "profile_order": [],
            "attempt_count": 0,
            "attempts": [],
            "handoff_required": False,
            "fallback": "deterministic",
            "structured_json": True,
            "lease": {},
            "model_invoked": False,
            "decision": str(initial_config.get("status", "disabled")),
        }
    policy = local_ai_routing.normalize_task_attempt_policy(initial_config.get("task_attempt_policy", {}))
    profile_order = initial_config.get("profile_order", [support.TEXT_TASK_PROFILE])
    if not isinstance(profile_order, list) or not profile_order:
        profile_order = [support.TEXT_TASK_PROFILE]
    profile_order = [str(profile).strip() for profile in profile_order if str(profile).strip()]
    profile_order = list(dict.fromkeys(profile_order)) or [support.TEXT_TASK_PROFILE]
    max_attempts = int(policy.get("max_attempts_per_profile", 2))
    attempts: list[dict[str, Any]] = []
    last_config = initial_config
    collected_issues: list[str] = []
    stop_local_attempts = False

    for profile in profile_order:
        if stop_local_attempts:
            break
        for attempt in range(1, max_attempts + 1):
            ok, output, config, issues = support.run_text_completion(
                root,
                task=task,
                profile=profile,
                prompt=prompt,
                json_schema=support.daily_report_json_schema(),
            )
            last_config = config
            if not ok:
                first_issue = str(issues[0]) if issues else "model completion failed"
                decision = retry_decision(first_issue, policy)
                attempts.append(
                    attempt_record(
                        profile=profile,
                        attempt=attempt,
                        started=False,
                        accepted=False,
                        issues=issues,
                        failure_class_name=str(decision["failure_class"]),
                        retryable=bool(decision["retryable"]),
                    )
                )
                collected_issues.extend(str(issue) for issue in issues)
                if not bool(decision["retryable"]):
                    stop_local_attempts = bool(decision["handoff_now"])
                    break
                continue
            model_report = support.report_from_model_output(output, task=task)
            rejection = acceptance_issue(model_report, config, policy)
            accepted = not rejection
            attempt_issues = [rejection] if rejection else []
            decision = retry_decision(rejection, policy) if rejection else {
                "failure_class": "",
                "retryable": False,
                "handoff_now": False,
            }
            attempts.append(
                attempt_record(
                    profile=profile,
                    attempt=attempt,
                    started=True,
                    accepted=accepted,
                    report=model_report,
                    issues=attempt_issues,
                    failure_class_name=str(decision["failure_class"]),
                    retryable=bool(decision["retryable"]) if rejection else None,
                )
            )
            if accepted:
                model_report["profile"] = profile
                model_report["profile_order"] = profile_order
                model_report["attempt_count"] = len(attempts)
                model_report["attempts"] = attempts
                model_report["handoff_required"] = False
                model_report["fallback"] = ""
                model_report["required"] = bool(config.get("required", False))
                model_report["lease"] = dict(config.get("lease", {}))
                return model_report
            collected_issues.append(rejection)
            if not bool(decision["retryable"]):
                stop_local_attempts = bool(decision["handoff_now"])
                break

    fallback = str(policy.get("fallback", "orchestrator-handoff"))
    issue_list = list(dict.fromkeys(issue for issue in collected_issues if issue))
    if not issue_list:
        issue_list = ["local AI attempts produced no accepted output"]
    issue_list.append(f"local AI attempts exhausted; hand off to {fallback}")
    if not attempts:
        attempts.append(
            attempt_record(
                profile=profile_order[0],
                attempt=1,
                started=False,
                accepted=False,
                issues=issue_list,
            )
        )
    return {
        "ok": False,
        "summary": "Local AI did not produce accepted structured output; orchestrator handoff is required.",
        "findings": [],
        "suggestions": ["Use deterministic evidence and let the orchestrator handle this task."],
        "evidence": [],
        "confidence": 0.0,
        "issues": issue_list,
        "required": bool(last_config.get("required", initial_config.get("required", False))),
        "profile": str(attempts[-1].get("profile", profile_order[0])),
        "profile_order": profile_order,
        "attempt_count": len(attempts),
        "attempts": attempts,
        "handoff_required": True,
        "fallback": fallback,
        "structured_json": False,
        "lease": dict(last_config.get("lease", {})),
    }


def read_daily_report_cache(root: Path, rel_cache_path: str, *, task: str, input_paths: list[str]) -> dict[str, Any] | None:
    path = (root / rel_cache_path).resolve()
    cache_root = (root / ".agents" / "local-ai" / "cache").resolve()
    try:
        path.relative_to(cache_root)
    except ValueError:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("task") != task:
        return None
    if data.get("input_paths") != input_paths:
        return None
    data = dict(data)
    data["cache_hit"] = True
    data["status"] = "cache"
    return data


def deterministic_small_changed_files_report(docs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Keep bounded, structured changed-file packets out of the model path."""

    text = "\n".join(str(doc.get("text", "")) for doc in docs)
    if len(text) > 32_000:
        return None
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    if not lines or len(lines) > 200:
        return None
    small_input = len(text) <= 800 and len(lines) <= 12
    path_pattern = re.compile(
        r"(?:[A-Za-z]:[\\/])?[^\s:]+[\\/][^\s:]+|[^\s:]+\.[A-Za-z0-9]{1,10}"
    )
    path_rows = [(line, path_pattern.search(line)) for line in lines]
    matched_paths = [match.group(0).rstrip(";,") for _, match in path_rows if match]
    if not small_input and len(matched_paths) < max(1, len(lines) - 3):
        return None
    evidence: list[dict[str, str]] = []
    findings: list[str] = []
    for line in lines[:8]:
        match = path_pattern.search(line)
        source = match.group(0).rstrip(";,") if match else "<input>"
        excerpt = line[match.end() :].lstrip(" :;-") if match else line
        evidence.append({"source": source, "excerpt": excerpt[:500]})
        findings.append(line[:240])
    if small_input:
        summary = "; ".join(findings[:3])[:240]
        decision = "deterministic-small-input"
    else:
        sample = ", ".join(matched_paths[:3])
        summary = f"{len(lines)} changed-file entries; sample: {sample}"[:240]
        decision = "deterministic-structured-input"
    return {
        "ok": True,
        "summary": summary,
        "findings": findings,
        "suggestions": [line[:240] for line in lines if line.lower().startswith(("risk:", "review focus:"))][:3],
        "evidence": evidence,
        "confidence": 1.0,
        "issues": [],
        "profile": decision,
        "profile_order": [],
        "attempt_count": 0,
        "attempts": [],
        "handoff_required": False,
        "fallback": "deterministic",
        "structured_json": True,
        "model_invoked": False,
        "decision": decision,
        "input_line_count": len(lines),
        "omitted_finding_count": max(0, len(lines) - len(findings)),
        "lease": {},
    }


def daily_task_report(root: Path, *, task: str, inputs: list[str], stdin_text: str) -> dict[str, Any]:
    if task not in support.DAILY_TEXT_TASKS:
        raise RuntimeError(f"unsupported daily local AI task: {task}")
    docs, input_paths = support.read_daily_inputs(root, inputs, stdin_text)
    prompt = support.daily_task_prompt(task, docs)
    name_hash = hashlib.sha256(json.dumps({"task": task, "inputs": docs}, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    _cache_path, rel_cache_path = support.cache_file(root, task, name_hash)
    cached = read_daily_report_cache(root, rel_cache_path, task=task, input_paths=input_paths)
    if cached is not None:
        return cached
    model_report = (
        deterministic_small_changed_files_report(docs)
        if task == "changed-files-summary"
        else None
    ) or support.run_daily_text_model(root, task, prompt)
    profile = str(model_report.get("profile", support.TEXT_TASK_PROFILE))
    lease = model_report.get("lease") if isinstance(model_report.get("lease"), dict) else {}
    report = support.stable_report(
        ok=bool(model_report.get("ok")),
        task=task,
        profile=profile,
        input_paths=input_paths,
        summary=str(model_report.get("summary", "")),
        findings=support.as_text_list(model_report.get("findings", [])),
        suggestions=support.as_text_list(model_report.get("suggestions", [])),
        evidence=support.normalize_evidence(model_report.get("evidence", [])),
        cache_path=rel_cache_path,
        issues=[str(issue) for issue in model_report.get("issues", [])],
        confidence=bounded_confidence(model_report.get("confidence", 0.0)),
        required=bool(model_report.get("required", False)),
        structured_json=bool(model_report.get("structured_json", False)),
        profile_order=model_report.get("profile_order", [profile]),
        attempt_count=int(model_report.get("attempt_count", 0) or 0),
        attempts=model_report.get("attempts", []),
        handoff_required=bool(model_report.get("handoff_required", False)),
        fallback=str(model_report.get("fallback", "")),
        model_invoked=bool(model_report.get("model_invoked", True)),
        decision=str(model_report.get("decision", "local-model")),
        input_line_count=int(model_report.get("input_line_count", 0) or 0),
        omitted_finding_count=int(model_report.get("omitted_finding_count", 0) or 0),
        lease=lease,
        **support.lease_report_fields(lease),
    )
    support.write_report_cache(root, report)
    return report


def print_daily_task(root: Path, *, task: str, inputs: list[str], as_json: bool) -> int:
    stdin_text = support.sys.stdin.read() if "-" in inputs else ""
    return support.print_generated_report(
        lambda: daily_task_report(root, task=task, inputs=inputs, stdin_text=stdin_text),
        as_json=as_json,
    )
