"""Local AI benchmark timing helpers."""

from __future__ import annotations

import math
import re
from typing import Any


def _float_or_none(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_llama_timing_output(output: str) -> dict[str, Any]:
    """Parse llama.cpp timing rows into standards-aligned fields."""
    metrics: dict[str, Any] = {}
    patterns = {
        "model_load_ms": r"load time\s*=\s*([0-9.]+)\s*ms",
        "prompt_eval": r"prompt eval time\s*=\s*([0-9.]+)\s*ms\s*/\s*([0-9]+)\s*tokens?.*?([0-9.]+)\s*tokens per second",
        "decode": r"(?:^|\n)\s*(?:llama_perf_context_print:\s+)?eval time\s*=\s*([0-9.]+)\s*ms\s*/\s*([0-9]+)\s*(?:runs|tokens?).*?([0-9.]+)\s*tokens per second",
        "total_ms": r"total time\s*=\s*([0-9.]+)\s*ms",
    }
    load = re.search(patterns["model_load_ms"], output, re.IGNORECASE)
    if load:
        metrics["model_load_ms"] = _float_or_none(load.group(1))
    prompt = re.search(patterns["prompt_eval"], output, re.IGNORECASE)
    if prompt:
        metrics["prompt_eval_ms"] = _float_or_none(prompt.group(1))
        metrics["prompt_tokens"] = int(prompt.group(2))
        metrics["prompt_throughput_tps"] = _float_or_none(prompt.group(3))
    decode = re.search(patterns["decode"], output, re.IGNORECASE)
    if decode:
        metrics["decode_ms"] = _float_or_none(decode.group(1))
        metrics["generated_tokens"] = int(decode.group(2))
        metrics["output_throughput_tps"] = _float_or_none(decode.group(3))
    total = re.search(patterns["total_ms"], output, re.IGNORECASE)
    if total:
        metrics["e2e_latency_ms"] = _float_or_none(total.group(1))
    generated = int(metrics.get("generated_tokens") or 0)
    decode_ms = metrics.get("decode_ms")
    if generated and isinstance(decode_ms, (int, float)):
        metrics["tpot_ms"] = round(float(decode_ms) / generated, 4)
        metrics["itl_ms"] = metrics["tpot_ms"]
    ttft_parts = [
        float(value)
        for value in (metrics.get("model_load_ms"), metrics.get("prompt_eval_ms"))
        if isinstance(value, (int, float))
    ]
    if ttft_parts:
        metrics["ttft_ms"] = round(sum(ttft_parts), 4)
    return metrics


def metrics_from_elapsed(
    elapsed_seconds: float,
    *,
    cold_start: bool,
    warm_cache: bool,
    repetitions: int,
) -> dict[str, Any]:
    e2e_ms = max(0.0, elapsed_seconds * 1000)
    return {
        "e2e_latency_ms": round(e2e_ms, 4),
        "request_throughput_rps": round(1000 / e2e_ms, 4) if e2e_ms > 0 else None,
        "cold_start": cold_start,
        "warm_cache": warm_cache,
        "repetitions": max(1, repetitions),
    }


def percentile(values: list[float], percent: float) -> float | None:
    clean = sorted(value for value in values if not math.isnan(value))
    if not clean:
        return None
    if len(clean) == 1:
        return round(clean[0], 4)
    rank = (len(clean) - 1) * percent
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(clean[int(rank)], 4)
    weight = rank - lower
    return round(clean[lower] * (1 - weight) + clean[upper] * weight, 4)


def aggregate_metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate: dict[str, Any] = {"repetitions": len(samples)}
    numeric_keys = {
        "ttft_ms",
        "tpot_ms",
        "itl_ms",
        "e2e_latency_ms",
        "model_load_ms",
        "prompt_eval_ms",
        "decode_ms",
        "request_throughput_rps",
        "output_throughput_tps",
        "prompt_throughput_tps",
        "peak_memory_mib",
        "cpu_utilization_percent",
    }
    for key in sorted(numeric_keys):
        values = [float(sample[key]) for sample in samples if isinstance(sample.get(key), (int, float))]
        if not values:
            continue
        aggregate[key] = round(sum(values) / len(values), 4)
        aggregate.setdefault("p50", {})[key] = percentile(values, 0.50)
        aggregate.setdefault("p95", {})[key] = percentile(values, 0.95)
    aggregate["cold_start"] = any(sample.get("cold_start") is True for sample in samples)
    aggregate["warm_cache"] = any(sample.get("warm_cache") is True for sample in samples)
    return aggregate
