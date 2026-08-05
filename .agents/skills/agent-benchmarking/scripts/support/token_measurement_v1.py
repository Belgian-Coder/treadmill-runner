"""Provider-neutral TokenMeasurementV1 normalization and gate eligibility."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import provider_evidence_adapters


SCHEMA_VERSION = 1
PROVENANCES = {
    "heuristic_estimate",
    "tokenizer_artifact",
    "provider_telemetry",
    "provider_invoice",
}
SCOPES = {"artifact", "full_run"}
HOST_SURFACES = {
    "codex",
    "github-copilot",
    "claude-code",
    "openai-responses-api",
    "anthropic-messages-api",
    "local-ai",
    "unknown",
}
MODEL_PROVIDERS = {"openai", "anthropic", "local", "other", "unknown"}
AVAILABILITY = {"reported", "derived", "estimated", "unavailable"}
AVAILABILITY_ORDER = ("unavailable", "estimated", "derived", "reported")
ACCOUNTING_UNITS = {"provider_tokens", "tokenizer_tokens", "estimated_tokens"}
PROVENANCE_ACCOUNTING_UNITS = {
    "provider_telemetry": "provider_tokens",
    "provider_invoice": "provider_tokens",
    "tokenizer_artifact": "tokenizer_tokens",
    "heuristic_estimate": "estimated_tokens",
}
TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens")
USAGE_TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
DETAIL_FIELDS = (
    "cache_read_input_tokens",
    "cache_write_input_tokens",
    "reasoning_output_tokens",
)
CLAIM_FIELDS = ("token-total", "cache-economics", "reasoning-detail")
REQUIRED_FIELDS = {
    "schema_version",
    "provenance",
    "scope",
    "accounting_unit",
    "tokenizer_or_estimator",
    "host_surface",
    "model_provider",
    *TOKEN_FIELDS,
    "details",
    "completeness",
    "evidence",
}
COMPLETENESS_FIELDS = {"complete", "missing", "claims"}


def _is_token_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _detail(value: int | None, availability: str) -> dict[str, Any]:
    return {"value": value, "availability": availability}


def _inferred_availability(provenance: str, value: int | None) -> str:
    if value is None:
        return "unavailable"
    if provenance in {"provider_telemetry", "provider_invoice"}:
        return "reported"
    if provenance == "tokenizer_artifact":
        return "derived"
    return "estimated"


def _inferred_accounting_unit(provenance: str) -> str:
    if provenance in {"provider_telemetry", "provider_invoice"}:
        return "provider_tokens"
    if provenance == "tokenizer_artifact":
        return "tokenizer_tokens"
    return "estimated_tokens"


def build_measurement(
    *,
    provenance: str,
    scope: str,
    tokenizer_or_estimator: str,
    accounting_unit: str | None = None,
    input_tokens: int,
    output_tokens: int = 0,
    total_tokens: int | None = None,
    host_surface: str = "unknown",
    model_provider: str = "unknown",
    cached_input_tokens: int | None = None,
    cache_write_input_tokens: int | None = None,
    reasoning_output_tokens: int | None = None,
    cache_read_availability: str | None = None,
    cache_write_availability: str | None = None,
    reasoning_availability: str | None = None,
    complete: bool,
    missing: list[str] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a canonical measurement while preserving unavailable detail as null."""

    provenance_text = str(provenance)
    computed_total = input_tokens + output_tokens if total_tokens is None else total_tokens
    cache_read_state = cache_read_availability or _inferred_availability(
        provenance_text, cached_input_tokens
    )
    cache_write_state = cache_write_availability or _inferred_availability(
        provenance_text, cache_write_input_tokens
    )
    reasoning_state = reasoning_availability or _inferred_availability(
        provenance_text, reasoning_output_tokens
    )
    normalized_missing = sorted(
        {str(item) for item in (missing or []) if str(item).strip()}
    )
    expected_complete = not normalized_missing
    if bool(complete) is not expected_complete:
        raise ValueError("token_measurement complete must be true exactly when missing is empty")
    return {
        "schema_version": SCHEMA_VERSION,
        "provenance": provenance_text,
        "scope": str(scope),
        "accounting_unit": str(accounting_unit or _inferred_accounting_unit(provenance_text)),
        "tokenizer_or_estimator": str(tokenizer_or_estimator),
        "host_surface": str(host_surface),
        "model_provider": str(model_provider),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": computed_total,
        "details": {
            "cache_read_input_tokens": _detail(cached_input_tokens, cache_read_state),
            "cache_write_input_tokens": _detail(cache_write_input_tokens, cache_write_state),
            "reasoning_output_tokens": _detail(reasoning_output_tokens, reasoning_state),
        },
        "completeness": {
            "complete": expected_complete,
            "missing": normalized_missing,
            "claims": {
                "token-total": True,
                "cache-economics": (
                    cache_read_state != "unavailable" and cache_write_state != "unavailable"
                ),
                "reasoning-detail": reasoning_state != "unavailable",
            },
        },
        "evidence": dict(evidence or provider_evidence_adapters.unavailable_evidence()),
    }


def _validate_detail(label: str, value: object) -> list[str]:
    issues: list[str] = []
    if not isinstance(value, dict):
        return [f"token_measurement.details.{label} must be an object"]
    unknown = sorted(set(value) - {"value", "availability"})
    missing = sorted({"value", "availability"} - set(value))
    issues.extend(f"token_measurement.details.{label}.{field} is required" for field in missing)
    issues.extend(f"token_measurement.details.{label}.{field} is not allowed" for field in unknown)
    availability = value.get("availability")
    detail_value = value.get("value")
    if availability not in AVAILABILITY:
        issues.append(
            f"token_measurement.details.{label}.availability must be one of: "
            + ", ".join(sorted(AVAILABILITY))
        )
    if availability == "unavailable":
        if detail_value is not None:
            issues.append(
                f"token_measurement.details.{label}.value must be null when unavailable"
            )
    elif not _is_token_count(detail_value):
        issues.append(
            f"token_measurement.details.{label}.value must be a non-negative integer when available"
        )
    return issues


def validate_measurement(value: object) -> list[str]:
    issues: list[str] = []
    if not isinstance(value, dict):
        return ["token_measurement must be an object"]
    missing_fields = sorted(REQUIRED_FIELDS - set(value))
    unknown_fields = sorted(set(value) - REQUIRED_FIELDS)
    issues.extend(f"token_measurement.{field} is required" for field in missing_fields)
    issues.extend(f"token_measurement.{field} is not allowed" for field in unknown_fields)
    if type(value.get("schema_version")) is not int or value.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"token_measurement.schema_version must be {SCHEMA_VERSION}")
    provenance = value.get("provenance")
    if provenance not in PROVENANCES:
        issues.append("token_measurement.provenance must be one of: " + ", ".join(sorted(PROVENANCES)))
    if value.get("scope") not in SCOPES:
        issues.append("token_measurement.scope must be one of: " + ", ".join(sorted(SCOPES)))
    estimator = value.get("tokenizer_or_estimator")
    if not isinstance(estimator, str) or not estimator.strip():
        issues.append("token_measurement.tokenizer_or_estimator must be a non-empty string")
    if value.get("accounting_unit") not in ACCOUNTING_UNITS:
        issues.append(
            "token_measurement.accounting_unit must be one of: "
            + ", ".join(sorted(ACCOUNTING_UNITS))
        )
    elif provenance in PROVENANCE_ACCOUNTING_UNITS and value.get("accounting_unit") != PROVENANCE_ACCOUNTING_UNITS[provenance]:
        issues.append(
            "token_measurement.accounting_unit must be "
            f"{PROVENANCE_ACCOUNTING_UNITS[provenance]} for provenance {provenance}"
        )
    if value.get("host_surface") not in HOST_SURFACES:
        issues.append("token_measurement.host_surface must be one of: " + ", ".join(sorted(HOST_SURFACES)))
    if value.get("model_provider") not in MODEL_PROVIDERS:
        issues.append("token_measurement.model_provider must be one of: " + ", ".join(sorted(MODEL_PROVIDERS)))
    for field in TOKEN_FIELDS:
        if not _is_token_count(value.get(field)):
            issues.append(f"token_measurement.{field} must be a non-negative integer")
    if all(_is_token_count(value.get(field)) for field in TOKEN_FIELDS):
        if int(value["total_tokens"]) != int(value["input_tokens"]) + int(value["output_tokens"]):
            issues.append("token_measurement.total_tokens must equal input_tokens + output_tokens")
    details = value.get("details")
    if not isinstance(details, dict):
        issues.append("token_measurement.details must be an object")
        details = {}
    else:
        issues.extend(
            f"token_measurement.details.{field} is required"
            for field in sorted(set(DETAIL_FIELDS) - set(details))
        )
        issues.extend(
            f"token_measurement.details.{field} is not allowed"
            for field in sorted(set(details) - set(DETAIL_FIELDS))
        )
    for field in DETAIL_FIELDS:
        issues.extend(_validate_detail(field, details.get(field)))
    cache_read = detail_value(value, "cache_read_input_tokens")
    cache_write = detail_value(value, "cache_write_input_tokens")
    reasoning = detail_value(value, "reasoning_output_tokens")
    if cache_read is not None and _is_token_count(value.get("input_tokens")) and cache_read > int(value["input_tokens"]):
        issues.append("token_measurement cache-read input detail must not exceed input_tokens")
    if cache_write is not None and _is_token_count(value.get("input_tokens")) and cache_write > int(value["input_tokens"]):
        issues.append("token_measurement cache-write input detail must not exceed input_tokens")
    if (
        cache_read is not None
        and cache_write is not None
        and _is_token_count(value.get("input_tokens"))
        and cache_read + cache_write > int(value["input_tokens"])
    ):
        issues.append("token_measurement cache-read plus cache-write input detail must not exceed input_tokens")
    if reasoning is not None and _is_token_count(value.get("output_tokens")) and reasoning > int(value["output_tokens"]):
        issues.append("token_measurement reasoning output detail must not exceed output_tokens")
    completeness = value.get("completeness")
    if not isinstance(completeness, dict):
        issues.append("token_measurement.completeness must be an object")
    else:
        issues.extend(
            f"token_measurement.completeness.{field} is required"
            for field in sorted(COMPLETENESS_FIELDS - set(completeness))
        )
        issues.extend(
            f"token_measurement.completeness.{field} is not allowed"
            for field in sorted(set(completeness) - COMPLETENESS_FIELDS)
        )
        if not isinstance(completeness.get("complete"), bool):
            issues.append("token_measurement.completeness.complete must be boolean")
        missing = completeness.get("missing")
        if not isinstance(missing, list) or not all(isinstance(item, str) and item.strip() for item in missing):
            issues.append("token_measurement.completeness.missing must be a list of non-empty strings")
        elif completeness.get("complete") is not (not missing):
            issues.append(
                "token_measurement.completeness.complete must be true exactly when missing is empty"
            )
        claims = completeness.get("claims")
        if not isinstance(claims, dict):
            issues.append("token_measurement.completeness.claims must be an object")
        else:
            issues.extend(
                f"token_measurement.completeness.claims.{field} is required"
                for field in sorted(set(CLAIM_FIELDS) - set(claims))
            )
            issues.extend(
                f"token_measurement.completeness.claims.{field} is not allowed"
                for field in sorted(set(claims) - set(CLAIM_FIELDS))
            )
            for field in CLAIM_FIELDS:
                if not isinstance(claims.get(field), bool):
                    issues.append(f"token_measurement.completeness.claims.{field} must be boolean")
            if claims.get("token-total") is not True:
                issues.append("token_measurement.completeness.claims.token-total must be true")
            cache_available = all(
                detail_value(value, field) is not None
                for field in ("cache_read_input_tokens", "cache_write_input_tokens")
            )
            if claims.get("cache-economics") is not cache_available:
                issues.append(
                    "token_measurement cache-economics claim must be true exactly when cache-read and cache-write details are available"
                )
            if claims.get("reasoning-detail") is not (reasoning is not None):
                issues.append(
                    "token_measurement reasoning-detail claim must be true exactly when reasoning detail is available"
                )
    issues.extend(provider_evidence_adapters.validate_evidence_shape(value.get("evidence")))
    return issues


def detail_value(value: object, field: str) -> int | None:
    if not isinstance(value, dict):
        return None
    details = value.get("details")
    if not isinstance(details, dict):
        return None
    detail = details.get(field)
    if not isinstance(detail, dict) or detail.get("availability") == "unavailable":
        return None
    raw = detail.get("value")
    return int(raw) if _is_token_count(raw) else None


def usage_counts(value: object) -> dict[str, int | None]:
    measurement = value if isinstance(value, dict) else {}
    return {
        "input_tokens": measurement.get("input_tokens") if _is_token_count(measurement.get("input_tokens")) else None,
        "cached_input_tokens": detail_value(measurement, "cache_read_input_tokens"),
        "cache_write_input_tokens": detail_value(measurement, "cache_write_input_tokens"),
        "output_tokens": measurement.get("output_tokens") if _is_token_count(measurement.get("output_tokens")) else None,
        "reasoning_output_tokens": detail_value(measurement, "reasoning_output_tokens"),
        "total_tokens": measurement.get("total_tokens") if _is_token_count(measurement.get("total_tokens")) else None,
    }


def normalize_measurement(value: object) -> dict[str, Any]:
    issues = validate_measurement(value)
    if issues:
        raise ValueError("invalid explicit token_measurement: " + "; ".join(issues))
    measurement = dict(value)
    measurement["details"] = {key: dict(item) for key, item in value["details"].items()}
    measurement["completeness"] = dict(value["completeness"])
    measurement["completeness"]["claims"] = dict(value["completeness"]["claims"])
    measurement["evidence"] = dict(value["evidence"])
    return measurement


def aggregate_availability(states: list[str]) -> str:
    """Return the weakest availability in a conservative aggregation lattice."""

    if not states or any(state not in AVAILABILITY for state in states):
        return "unavailable"
    return min(states, key=AVAILABILITY_ORDER.index)


def aggregate_detail(measurements: list[object], field: str) -> dict[str, Any]:
    """Aggregate one subset detail without turning missing evidence into zero."""

    details: list[dict[str, Any]] = []
    for measurement in measurements:
        if not isinstance(measurement, dict):
            return _detail(None, "unavailable")
        measurement_details = measurement.get("details")
        if not isinstance(measurement_details, dict):
            return _detail(None, "unavailable")
        detail = measurement_details.get(field)
        if not isinstance(detail, dict):
            return _detail(None, "unavailable")
        details.append(detail)
    state = aggregate_availability([str(detail.get("availability", "")) for detail in details])
    if state == "unavailable":
        return _detail(None, state)
    values = [detail.get("value") for detail in details]
    if not all(_is_token_count(value) for value in values):
        return _detail(None, "unavailable")
    return _detail(sum(int(value) for value in values), state)


def gate_eligibility(
    value: object,
    *,
    gate_scope: str,
    evidence_root: Path | None = None,
    trusted_host_capture_root: Path | None = None,
    expected_run_id: str | None = None,
    expected_model_label: str | None = None,
    evidence_already_verified: bool = False,
) -> dict[str, Any]:
    reasons = validate_measurement(value)
    measurement = value if isinstance(value, dict) else {}
    normalized_scope = str(gate_scope).replace("-", "_")
    if normalized_scope not in SCOPES:
        reasons.append(f"unsupported gate scope: {gate_scope}")
    completeness = measurement.get("completeness") if isinstance(measurement.get("completeness"), dict) else {}
    claims = completeness.get("claims") if isinstance(completeness.get("claims"), dict) else {}
    if completeness.get("complete") is not True:
        reasons.append("token measurement is incomplete")
    if claims.get("token-total") is not True:
        reasons.append("token-total claim is unavailable")
    provenance = str(measurement.get("provenance", ""))
    scope = str(measurement.get("scope", ""))
    if scope != normalized_scope:
        reasons.append(f"token measurement scope {scope or 'missing'} does not match gate scope {normalized_scope}")
    if normalized_scope == "artifact" and provenance != "tokenizer_artifact":
        reasons.append("artifact gate requires tokenizer_artifact provenance")
    if (
        normalized_scope == "artifact"
        and provenance == "tokenizer_artifact"
        and not evidence_already_verified
    ):
        reasons.extend(
            provider_evidence_adapters.verify_artifact_tokenizer_binding(
                measurement,
                evidence_root=evidence_root,
            )
        )
    if normalized_scope == "full_run" and provenance not in {"provider_telemetry", "provider_invoice"}:
        reasons.append("full-run gate requires provider telemetry or invoice provenance")
    host_surface = str(measurement.get("host_surface", ""))
    model_provider = str(measurement.get("model_provider", ""))
    if normalized_scope == "full_run":
        if host_surface == "unknown":
            reasons.append("full-run gate requires an observed non-unknown host_surface")
        if model_provider == "unknown":
            reasons.append("full-run gate requires an observed non-unknown model_provider")
        reasons.extend(
            provider_evidence_adapters.eligibility_issues(
                measurement.get("evidence"),
                host_surface=host_surface,
                model_provider=model_provider,
                provenance=provenance,
            )
        )
        if not evidence_already_verified:
            reasons.extend(
                provider_evidence_adapters.verify_measurement_binding(
                    measurement,
                    evidence_root=evidence_root,
                    trusted_host_capture_root=trusted_host_capture_root,
                    expected_run_id=expected_run_id,
                    expected_model_label=expected_model_label,
                )
            )
    return {
        "eligible": not reasons,
        "gate_scope": normalized_scope,
        "provenance": provenance,
        "scope": scope,
        "host_surface": host_surface,
        "model_provider": model_provider,
        "claims": claims,
        "reasons": sorted(set(reasons)),
    }
