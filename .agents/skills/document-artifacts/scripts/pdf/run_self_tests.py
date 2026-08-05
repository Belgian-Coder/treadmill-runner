#!/usr/bin/env python3

import json
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pdf_tools


def assert_contains(items, text):
    assert any(text in str(item) for item in items), items


def make_pdf(path, text="Hello PDF text"):
    payload = text.encode("latin-1", errors="ignore")
    path.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj\n"
        + b"4 0 obj << /Length 44 >> stream\nBT /F1 12 Tf 72 720 Td (" + payload + b") Tj ET\nendstream endobj\n"
        + b"trailer << /Root 1 0 R >>\n%%EOF\n"
    )


def test_parser_help():
    parser = pdf_tools.build_parser()
    help_text = parser.format_help()
    assert "render-pages" in help_text
    assert "forms" in help_text
    assert "doctor" in help_text
    assert "compare" in help_text
    assert "to-markdown" in help_text
    assert "metadata" in help_text
    assert "extract-assets" in help_text
    assert "bundle-evidence" in help_text
    assert "batch" in help_text


def test_evidence_schema_file():
    schema = json.loads((SCRIPT_DIR.parents[1] / "docs" / "pdf-evidence-schema.json").read_text(encoding="utf-8"))
    report = pdf_tools.inspect_pdf(Path(__file__))
    for field in schema["required_fields"]:
        assert field in report


def test_inspect_json_shape(tmp):
    pdf = tmp / "sample.pdf"
    make_pdf(pdf)
    report = pdf_tools.inspect_pdf(pdf)
    assert report["schema_version"] == 1
    assert report["tool"] == "document-artifacts"
    assert report["ok"] is True
    assert report["input_sha256"]
    assert report["format"] == "pdf"
    assert "issues" in report
    assert report["checks"]


def test_doctor_json_shape():
    report = pdf_tools.doctor_report()
    assert report["command"] == "doctor"
    assert report["ok"] is True
    assert report["capabilities"]["python"] is True
    assert report["commands"]


def test_doctor_install_python_deps_is_explicit(tmp):
    calls = []
    original_run = pdf_tools.subprocess.run
    original_deps = pdf_tools.LOCAL_DEPS

    def fake_run(command, **kwargs):
        calls.append(command)

        class Result:
            returncode = 0
            stdout = "ok"

        return Result()

    pdf_tools.subprocess.run = fake_run
    pdf_tools.LOCAL_DEPS = tmp / "deps"
    try:
        report = pdf_tools.doctor_report(install_deps=True)
    finally:
        pdf_tools.subprocess.run = original_run
        pdf_tools.LOCAL_DEPS = original_deps
    assert report["ok"] is True
    assert calls
    assert "--target" in calls[0]


def test_extract_text_fallback(tmp):
    pdf = tmp / "sample.pdf"
    make_pdf(pdf)
    report = pdf_tools.extract_text(pdf)
    assert report["status"] in {"passed", "skipped"}
    assert "evidence" in report
    if report["ok"]:
        assert "Hello PDF text" in report["evidence"][-1]["excerpt"]


def test_malformed_pdf_blocked(tmp):
    pdf = tmp / "bad.pdf"
    pdf.write_text("not a pdf\n", encoding="utf-8", newline="\n")
    report = pdf_tools.inspect_pdf(pdf)
    assert report["ok"] is False
    assert report["status"] == "blocked"
    assert report["blocked"]


def test_protected_marker_warning(tmp):
    pdf = tmp / "protected.pdf"
    make_pdf(pdf)
    pdf.write_bytes(pdf.read_bytes() + b"\n/Encrypt /JavaScript /EmbeddedFile /Sig /Perms\n")
    report = pdf_tools.inspect_pdf(pdf)
    assert report["warnings"]
    assert_contains(report["evidence"], "pdf-markers")


def test_render_missing_tool_is_skipped(tmp):
    pdf = tmp / "sample.pdf"
    make_pdf(pdf)
    original_which = pdf_tools.shutil.which
    pdf_tools.shutil.which = lambda name: None
    try:
        report = pdf_tools.render_pages(pdf, tmp / "pages", write=True)
    finally:
        pdf_tools.shutil.which = original_which
    assert report["status"] == "skipped"
    assert report["writes"] == []
    strict = pdf_tools.apply_strict(report, True)
    assert strict["status"] == "failed"


def test_forms_fill_dry_run_writes_nothing(tmp):
    pdf = tmp / "sample.pdf"
    values = tmp / "values.json"
    make_pdf(pdf)
    values.write_text(json.dumps({"Name": "Example"}), encoding="utf-8", newline="\n")
    report = pdf_tools.fill_forms(pdf, values, None, write=False)
    assert report["status"] == "planned"
    assert report["writes"] == []


def test_cli_json(tmp):
    pdf = tmp / "sample.pdf"
    make_pdf(pdf)
    status = pdf_tools.main(["inspect", "--file", str(pdf), "--json"])
    assert status == 0


def test_output_files_and_no_input_mutation(tmp):
    pdf = tmp / "sample.pdf"
    out_json = tmp / "report.json"
    out_md = tmp / "report.md"
    make_pdf(pdf)
    before_hash = pdf_tools.file_sha256(pdf)
    status = pdf_tools.main(["inspect", "--file", str(pdf), "--output-json", str(out_json), "--output-md", str(out_md), "--json"])
    assert status == 0
    assert out_json.exists()
    assert out_md.exists()
    saved = json.loads(out_json.read_text(encoding="utf-8"))
    assert saved["artifacts"]
    assert pdf_tools.file_sha256(pdf) == before_hash


def test_compare_json_shape(tmp):
    before = tmp / "before.pdf"
    after = tmp / "after.pdf"
    make_pdf(before, "Hello PDF text")
    make_pdf(after, "Hello PDF text with more words")
    report = pdf_tools.compare_pdfs(before, after)
    assert report["ok"] is True
    compare = report["evidence"][-1]
    assert compare["kind"] == "pdf-compare"
    assert compare["differences"]["sha256_changed"] is True


def test_to_markdown_writes_output(tmp):
    pdf = tmp / "sample.pdf"
    output = tmp / "sample.md"
    make_pdf(pdf, "Hello Markdown PDF")
    before_hash = pdf_tools.file_sha256(pdf)
    report = pdf_tools.to_markdown(pdf, output)
    assert report["ok"] is True
    assert output.exists()
    assert "Hello Markdown PDF" in output.read_text(encoding="utf-8")
    assert pdf_tools.file_sha256(pdf) == before_hash


def test_to_markdown_include_content_is_opt_in(tmp):
    pdf = tmp / "sample.pdf"
    long_text = ") Tj\n(".join(["D" * 200] * 5 + ["PDF_CONTENT_TAIL"])
    make_pdf(pdf, long_text)
    before_hash = pdf_tools.file_sha256(pdf)
    compact = pdf_tools.to_markdown(pdf, None)
    compact_evidence = next(item for item in compact["evidence"] if item["kind"] == "markdown")
    assert "content" not in compact_evidence
    assert_contains(compact["skipped"], "--include-content")
    full = pdf_tools.to_markdown(pdf, None, include_content=True)
    full_evidence = next(item for item in full["evidence"] if item["kind"] == "markdown")
    assert len(full_evidence["content"]) == full_evidence["characters"]
    assert "PDF_CONTENT_TAIL" in full_evidence["content"][800:]
    assert full["writes"] == []
    assert full["artifacts"] == []
    assert not any("no --output path" in item for item in full["skipped"])
    assert pdf_tools.file_sha256(pdf) == before_hash


def test_markdown_reads_pdf_pages_incrementally():
    accessed = []

    class Page:
        def __init__(self, number):
            self.number = number

        def extract_text(self):
            return f"Page {self.number}"

    class Pages:
        def __len__(self):
            return 3

        def __getitem__(self, index):
            assert isinstance(index, int), "page collection must not be sliced or materialized"
            accessed.append(index)
            return Page(index + 1)

    class Reader:
        pages = Pages()

    original_read = pdf_tools.read_pdf_bytes
    original_reader = pdf_tools.pypdf_reader
    pdf_tools.read_pdf_bytes = lambda _path: b"%PDF-1.4\n"
    pdf_tools.pypdf_reader = lambda _path: Reader()
    try:
        markdown, skipped, warnings = pdf_tools.markdown_from_pdf(Path("large.pdf"), max_pages=2)
    finally:
        pdf_tools.read_pdf_bytes = original_read
        pdf_tools.pypdf_reader = original_reader
    assert accessed == [0, 1]
    assert "Page 1" in markdown and "Page 2" in markdown and "Page 3" not in markdown
    assert_contains(skipped, "truncated after 2 page")
    assert warnings == []


def test_to_markdown_options_and_overwrite_guard(tmp):
    pdf = tmp / "sample.pdf"
    output = tmp / "sample.md"
    make_pdf(pdf, "Hello Markdown PDF")
    output.write_text("existing", encoding="utf-8", newline="\n")
    blocked = pdf_tools.to_markdown(pdf, output, include_metadata=True, include_links=True, include_outline=True, include_assets=True)
    assert blocked["status"] == "blocked"
    forced = pdf_tools.to_markdown(pdf, output, force=True, include_metadata=True, include_links=True, include_outline=True, include_assets=True)
    assert forced["ok"] is True
    text = output.read_text(encoding="utf-8")
    assert "Metadata Evidence" in text
    assert "Asset Evidence" in text
    child_blocked = pdf_tools.to_markdown(pdf, pdf / "content.md", force=True)
    assert child_blocked["status"] == "blocked"
    assert "input file" in " ".join(child_blocked["blocked"])


def test_format_depth_reports_and_assets(tmp):
    pdf = tmp / "sample.pdf"
    assets = tmp / "assets"
    make_pdf(pdf)
    pdf.write_bytes(pdf.read_bytes() + b"\n/URI (https://example.test) /StructTreeRoot /Lang /Alt /Subtype /Image /EmbeddedFile\n")
    metadata = pdf_tools.pdf_metadata_report(pdf)
    links = pdf_tools.pdf_links_report(pdf)
    outline = pdf_tools.pdf_outline_report(pdf)
    accessibility = pdf_tools.pdf_accessibility_report(pdf)
    dry_run = pdf_tools.extract_assets(pdf, assets, write=False)
    written = pdf_tools.extract_assets(pdf, assets, write=True)
    assert metadata["ok"] is True
    assert links["evidence"][0]["count"] >= 1
    assert outline["ok"] is True
    assert accessibility["evidence"][0]["tagged_structure"] is True
    assert dry_run["status"] == "planned"
    assert dry_run["writes"] == []
    assert written["ok"] is True
    assert (assets / "asset-manifest.json").exists()


def test_bundle_and_batch_evidence(tmp):
    first = tmp / "first.pdf"
    second = tmp / "second.pdf"
    make_pdf(first, "First PDF")
    make_pdf(second, "Second PDF")
    bundle_dir = tmp / "bundle"
    batch_dir = tmp / "batch"
    planned = pdf_tools.bundle_evidence(first, bundle_dir, write=False)
    written = pdf_tools.bundle_evidence(first, bundle_dir, write=True)
    batch = pdf_tools.batch_evidence([first, second], batch_dir, write=True)
    assert planned["status"] == "planned"
    assert_contains(planned["evidence"], "next-safe-commands")
    assert_contains(planned["findings"], "render-pages")
    assert written["ok"] is True
    assert (bundle_dir / "evidence-bundle.json").exists()
    assert batch["ok"] is True
    assert (batch_dir / "batch-index.json").exists()


def test_evidence_output_dir_cannot_be_input_file(tmp):
    pdf = tmp / "sample.pdf"
    make_pdf(pdf)
    bundle = pdf_tools.bundle_evidence(pdf, pdf, write=True)
    batch = pdf_tools.batch_evidence([pdf], pdf, write=True)
    assert bundle["status"] == "blocked"
    assert batch["status"] == "blocked"
    assert "input file" in " ".join(bundle["blocked"])


def run_tests():
    tests = [
        test_parser_help,
        test_evidence_schema_file,
        test_inspect_json_shape,
        test_doctor_json_shape,
        test_doctor_install_python_deps_is_explicit,
        test_extract_text_fallback,
        test_malformed_pdf_blocked,
        test_protected_marker_warning,
        test_render_missing_tool_is_skipped,
        test_forms_fill_dry_run_writes_nothing,
        test_cli_json,
        test_output_files_and_no_input_mutation,
        test_compare_json_shape,
        test_to_markdown_writes_output,
        test_to_markdown_include_content_is_opt_in,
        test_markdown_reads_pdf_pages_incrementally,
        test_to_markdown_options_and_overwrite_guard,
        test_format_depth_reports_and_assets,
        test_bundle_and_batch_evidence,
        test_evidence_output_dir_cannot_be_input_file,
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        for test in tests:
            root = base / test.__name__
            root.mkdir()
            if test.__code__.co_argcount:
                test(root)
            else:
                test()
            print(f"PASS {test.__name__}")


def main():
    run_tests()
    print("document-artifacts self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
