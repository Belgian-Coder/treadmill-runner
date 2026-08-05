#!/usr/bin/env python3
"""Image and rendered-PDF vision helpers for repo-local AI."""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from local_ai_support import setup_impl as support
from local_ai_support import model_lease


LOG_LINE_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+\s+[A-Z]\s+")


def parse_pages(value: str) -> list[int]:
    pages: list[int] = []
    for part in str(value or "1").split(","):
        text = part.strip()
        if not text:
            continue
        if "-" in text:
            start_text, end_text = text.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if end < start:
                raise RuntimeError(f"invalid page range: {text}")
            pages.extend(range(start, end + 1))
        else:
            pages.append(int(text))
    pages = list(dict.fromkeys(pages))
    if not pages or any(page < 1 for page in pages):
        raise RuntimeError("pages must be positive 1-based page numbers")
    if len(pages) > 12:
        raise RuntimeError("vision pdf accepts at most 12 pages per run")
    return pages


def vision_model_paths(root: Path) -> tuple[Path, Path, Path, dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    model, runtime, config, issues = support.resolve_model_and_runtime(
        root,
        task="inventory-summary",
        profile=support.VISION_PROFILE,
    )
    if model is None or runtime is None:
        return Path(), Path(), Path(), config, model or {}, runtime or {}, issues
    runtime_path = Path(str(runtime.get("resolved_path", "")))
    mtmd_path = runtime_path.with_name("llama-mtmd-cli.exe")
    sidecars = model.get("sidecar_files", [])
    mmproj_path = Path()
    if isinstance(sidecars, list):
        for sidecar in sidecars:
            if isinstance(sidecar, dict) and sidecar.get("kind") == "mmproj":
                manifest_path = root / str(config.get("bundle_manifest", support.local_ai_routing.DEFAULT_MANIFEST_PATH))
                try:
                    mmproj_path = support.local_ai_routing.resolve_asset(manifest_path.parent, str(sidecar.get("path", "")))
                except ValueError:
                    mmproj_path = Path()
                break
    if not mtmd_path.exists():
        issues.append(f"llama-mtmd-cli.exe is missing: {mtmd_path}")
    if not mmproj_path.exists():
        issues.append("vision mmproj sidecar is missing")
    return mtmd_path, Path(str(model.get("resolved_path", ""))), mmproj_path, config, model, runtime, issues


def clean_model_text(output: str) -> str:
    def strip_template_echo(text: str) -> str:
        if "<|im_end|>" in text:
            return text.rsplit("<|im_end|>", 1)[-1].strip()
        return text.strip()

    lines = output.splitlines()
    decoded_indexes = [index for index, line in enumerate(lines) if "image decoded" in line.lower()]
    if decoded_indexes:
        answer_lines: list[str] = []
        for candidate in lines[decoded_indexes[-1] + 1 :]:
            stripped = candidate.strip()
            if stripped.startswith("llama_perf_context_print:"):
                break
            if stripped:
                answer_lines.append(stripped)
        if answer_lines:
            return strip_template_echo("\n".join(answer_lines))
    lines = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(
            (
                "alloc_",
                "clip_",
                "common_",
                "decoding image",
                "encoding image",
                "ggml_",
                "image decoded",
                "image slice encoded",
                "llama_",
                "load_",
                "main:",
                "mtmd_",
                "print_info:",
                "sched_",
                "warmup:",
                "WARN:",
                "---",
                "<|",
            )
        ):
            continue
        if LOG_LINE_RE.match(stripped):
            continue
        lines.append(stripped)
    return strip_template_echo("\n".join(lines))


def _run_vision_model_unleased(root: Path, image_path: Path, prompt: str) -> tuple[bool, str, list[str]]:
    mtmd_path, model_path, mmproj_path, config, _model, _runtime, issues = support.vision_model_paths(root)
    if issues:
        return False, "", issues
    limits = dict(config.get("limits", support.local_ai_routing.DEFAULT_LIMITS))
    command = [
        str(mtmd_path),
        "-m",
        str(model_path),
        "--mmproj",
        str(mmproj_path),
        "--image",
        str(image_path),
        "-p",
        prompt,
        "-t",
        str(int(limits.get("threads", 8))),
        "-tb",
        str(int(limits.get("threads_batch", 8))),
        "-c",
        str(int(limits.get("context_tokens", 2048))),
        "-b",
        str(int(limits.get("batch_size", 512))),
        "-ub",
        str(int(limits.get("ubatch_size", 256))),
        "-n",
        str(max(int(limits.get("output_tokens", 160)), 180)),
        "--temp",
        "0",
        "--top-k",
        "1",
        "--seed",
        "42",
        "--image-max-tokens",
        "1536",
        "--image-min-tokens",
        "1024",
        "--no-mmproj-offload",
        "--no-warmup",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=int(limits.get("timeout_seconds", 300)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "", ["vision model timed out"]
    except OSError as exc:
        return False, "", [f"vision model failed to start: {exc}"]
    if completed.returncode != 0:
        return False, support.clean_model_text(completed.stdout), [f"vision model exited with {completed.returncode}"]
    return True, support.clean_model_text(completed.stdout), []


def run_vision_model(
    root: Path,
    image_path: Path,
    prompt: str,
    *,
    lease_report: dict[str, Any] | None = None,
) -> tuple[bool, str, list[str]]:
    with model_lease.exclusive_lease(
        root,
        profile=support.VISION_PROFILE,
        role="vision",
        priority="interactive",
        command_kind="vision",
        timeout_ms=0,
    ) as lease:
        if not lease.acquired:
            if lease_report is not None:
                lease_report.update(lease.report())
            return False, "", ["local-ai-busy; deterministic fallback required"]
        started = time.perf_counter()
        result = _run_vision_model_unleased(root, image_path, prompt)
        lease.inference_ms = int(max(0.0, time.perf_counter() - started) * 1000)
        if lease_report is not None:
            lease_report.update(lease.report())
        return result


def vision_describe_report(root: Path, *, image: str, run_model: bool = True) -> dict[str, Any]:
    image_path = support.resolve_repo_file(root, image)
    if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise RuntimeError("vision describe accepts only JPEG or PNG images")
    rel_image = support.relative(root, image_path)
    _cache_path, rel_cache_path = support.cache_file(
        root,
        "vision",
        "image-" + hashlib.sha256(rel_image.encode("utf-8")).hexdigest()[:16],
    )
    summary = "Image is ready for local vision analysis."
    issues: list[str] = []
    lease: dict[str, Any] = {}
    ok = True
    if run_model:
        ok, output, issues = support.run_vision_model(
            root,
            image_path,
            "Describe what is visibly in this image. Use only pixel evidence. Keep the answer concise.",
            lease_report=lease,
        )
        summary = output[:1200] if output else "Local vision model is not ready; deterministic fallback should be used."
    report = support.stable_report(
        ok=ok,
        task="vision-describe",
        profile=support.VISION_PROFILE,
        input_paths=[rel_image],
        summary=summary,
        findings=[],
        suggestions=[],
        evidence=[{"path": rel_image, "kind": "image"}],
        cache_path=rel_cache_path,
        issues=issues,
        lease=lease,
        **support.lease_report_fields(lease),
    )
    support.write_report_cache(root, report)
    return report


def render_pdf_pages(root: Path, pdf_path: Path, pages: list[int], target_dir: Path) -> list[Path]:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError("pypdfium2 is required to render PDF pages for local vision analysis") from exc
    target_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page_count = len(document)
        for page_number in pages:
            if page_number > page_count:
                raise RuntimeError(f"PDF has only {page_count} page(s); requested page {page_number}")
            page = document[page_number - 1]
            bitmap = page.render(scale=2)
            image = bitmap.to_pil()
            target = target_dir / f"page-{page_number:03d}.png"
            image.save(target)
            rendered.append(target)
    finally:
        document.close()
    return rendered


def vision_pdf_report(root: Path, *, pdf: str, pages: str, run_model: bool = True) -> dict[str, Any]:
    pdf_path = support.resolve_repo_file(root, pdf, allowed_suffixes={".pdf"})
    page_numbers = support.parse_pages(pages)
    rel_pdf = support.relative(root, pdf_path)
    cache_key = hashlib.sha256(f"{rel_pdf}|{','.join(str(page) for page in page_numbers)}".encode("utf-8")).hexdigest()[:16]
    cache_path, rel_cache_path = support.cache_file(root, "vision", f"pdf-{cache_key}")
    render_dir = cache_path.with_suffix("")
    evidence: list[dict[str, Any]] = [{"page": page} for page in page_numbers]
    summaries: list[str] = []
    issues: list[str] = []
    ok = True
    lease_reports: list[dict[str, Any]] = []
    if run_model:
        try:
            rendered = support.render_pdf_pages(root, pdf_path, page_numbers, render_dir)
            evidence = []
            for page_number, image_path in zip(page_numbers, rendered):
                page_lease: dict[str, Any] = {}
                model_ok, output, model_issues = support.run_vision_model(
                    root,
                    image_path,
                    "Describe this rendered PDF page. Read visible raster text, layout, tables, charts, and inline images. Keep it concise.",
                    lease_report=page_lease,
                )
                lease_reports.append(page_lease)
                ok = ok and model_ok
                issues.extend(model_issues)
                summaries.append(f"Page {page_number}: {output[:500]}")
                evidence.append({"page": page_number, "path": support.relative(root, image_path), "excerpt": output[:500]})
        except RuntimeError as exc:
            ok = False
            issues.append(str(exc))
    summary = " ".join(summaries) if summaries else f"PDF pages selected for local vision analysis: {', '.join(str(page) for page in page_numbers)}."
    lease = support.aggregate_lease_reports(lease_reports)
    report = support.stable_report(
        ok=ok,
        task="vision-pdf",
        profile=support.VISION_PROFILE,
        input_paths=[rel_pdf],
        summary=summary,
        findings=[],
        suggestions=[],
        evidence=evidence,
        cache_path=rel_cache_path,
        issues=issues,
        pages=page_numbers,
        lease=lease,
        **support.lease_report_fields(lease),
    )
    support.write_report_cache(root, report)
    return report


def print_vision_describe(root: Path, *, image: str, as_json: bool) -> int:
    return support.print_generated_report(lambda: vision_describe_report(root, image=image, run_model=True), as_json=as_json)


def print_vision_pdf(root: Path, *, pdf: str, pages: str, as_json: bool) -> int:
    return support.print_generated_report(lambda: vision_pdf_report(root, pdf=pdf, pages=pages, run_model=True), as_json=as_json)
