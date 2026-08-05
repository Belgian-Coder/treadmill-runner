#!/usr/bin/env python3

import sys
import tempfile
import zipfile
import json
from pathlib import Path

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import word_tools


def assert_contains(items, text):
    assert any(text in str(item) for item in items), items


def make_docx(path, unsafe=False, text="Hello Word"):
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        "<w:body>"
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Heading One</w:t></w:r></w:p>'
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        '<w:p><w:hyperlink r:id="rIdHyperlink"><w:r><w:t>Example link</w:t></w:r></w:hyperlink></w:p>'
        '<w:p><w:r><w:drawing><wp:inline><wp:docPr id="1" name="Picture 1"/></wp:inline></w:drawing></w:r></w:p>'
        '<w:p><w:ins><w:r><w:t>Inserted</w:t></w:r></w:ins></w:p>'
        '<w:p><w:del><w:r><w:t>Deleted</w:t></w:r></w:del></w:p>'
        "</w:body></w:document>"
    )
    comments = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:comment><w:p><w:r><w:t>Review comment</w:t></w:r></w:p></w:comment>"
        "</w:comments>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdHyperlink" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.test" TargetMode="External"/>'
        "</Relationships>"
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Fixture Document</dc:title></cp:coreProperties>'
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("docProps/core.xml", core)
        archive.writestr("docProps/app.xml", "<Properties><Application>Tests</Application></Properties>")
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", rels)
        archive.writestr("word/comments.xml", comments)
        archive.writestr("word/media/image1.png", b"png")
        if unsafe:
            archive.writestr("../evil.xml", "bad")


def test_parser_help():
    help_text = word_tools.build_parser().format_help()
    assert "replace-text" in help_text
    assert "tracked-changes" in help_text
    assert "doctor" in help_text
    assert "compare" in help_text
    assert "to-markdown" in help_text
    assert "metadata" in help_text
    assert "extract-assets" in help_text
    assert "bundle-evidence" in help_text
    assert "batch" in help_text


def test_evidence_schema_file():
    schema = json.loads((SCRIPT_DIR.parents[1] / "docs" / "word-evidence-schema.json").read_text(encoding="utf-8"))
    report = word_tools.inspect_docx(Path(__file__))
    for field in schema["required_fields"]:
        assert field in report


def test_inspect_json_shape(tmp):
    docx = tmp / "sample.docx"
    make_docx(docx)
    report = word_tools.inspect_docx(docx)
    assert report["schema_version"] == 1
    assert report["tool"] == "document-artifacts"
    assert report["ok"] is True
    assert report["input_sha256"]
    assert report["format"] == "docx"
    assert "issues" in report
    assert report["evidence"][0]["paragraphs"] >= 1


def test_doctor_json_shape():
    report = word_tools.doctor_report()
    assert report["command"] == "doctor"
    assert report["ok"] is True
    assert report["capabilities"]["python"] is True
    assert report["commands"]


def test_doctor_install_python_deps_is_explicit(tmp):
    calls = []
    original_run = word_tools.subprocess.run
    original_deps = word_tools.LOCAL_DEPS

    def fake_run(command, **kwargs):
        calls.append(command)

        class Result:
            returncode = 0
            stdout = "ok"

        return Result()

    word_tools.subprocess.run = fake_run
    word_tools.LOCAL_DEPS = tmp / "deps"
    try:
        report = word_tools.doctor_report(install_deps=True)
    finally:
        word_tools.subprocess.run = original_run
        word_tools.LOCAL_DEPS = original_deps
    assert report["ok"] is True
    assert calls
    assert "--target" in calls[0]


def test_extract_markdown(tmp):
    docx = tmp / "sample.docx"
    make_docx(docx)
    report = word_tools.extract_markdown(docx)
    assert report["ok"] is True
    assert "Hello Word" in report["evidence"][0]["excerpt"]


def test_comments_and_tracked_changes(tmp):
    docx = tmp / "sample.docx"
    make_docx(docx)
    comments = word_tools.comments_report(docx)
    tracked = word_tools.tracked_changes_report(docx)
    assert comments["evidence"][0]["count"] == 1
    assert tracked["evidence"][0]["insertions"] == 1
    assert tracked["evidence"][0]["deletions"] == 1


def test_replace_dry_run_writes_nothing(tmp):
    docx = tmp / "sample.docx"
    make_docx(docx)
    report = word_tools.replace_text(docx, "Hello", "Goodbye", None, write=False)
    assert report["status"] == "planned"
    assert report["writes"] == []
    assert report["evidence"][0]["replacement_count"] >= 1


def test_unsafe_zip_blocked(tmp):
    docx = tmp / "unsafe.docx"
    make_docx(docx, unsafe=True)
    report = word_tools.inspect_docx(docx)
    assert report["status"] == "blocked"
    assert report["blocked"]


def test_render_missing_tool_is_skipped(tmp):
    docx = tmp / "sample.docx"
    make_docx(docx)
    original_which = word_tools.shutil.which
    word_tools.shutil.which = lambda name: None
    try:
        report = word_tools.render_docx(docx, tmp / "pages", write=True)
    finally:
        word_tools.shutil.which = original_which
    assert report["status"] == "skipped"
    assert report["writes"] == []
    strict = word_tools.apply_strict(report, True)
    assert strict["status"] == "failed"


def test_output_files_and_no_input_mutation(tmp):
    docx = tmp / "sample.docx"
    out_json = tmp / "report.json"
    out_md = tmp / "report.md"
    make_docx(docx)
    before_hash = word_tools.file_sha256(docx)
    status = word_tools.main(["inspect", "--file", str(docx), "--output-json", str(out_json), "--output-md", str(out_md), "--json"])
    assert status == 0
    assert out_json.exists()
    assert out_md.exists()
    saved = json.loads(out_json.read_text(encoding="utf-8"))
    assert saved["artifacts"]
    assert word_tools.file_sha256(docx) == before_hash


def test_compare_json_shape(tmp):
    before = tmp / "before.docx"
    after = tmp / "after.docx"
    make_docx(before, text="Hello Word")
    make_docx(after, text="Hello Word changed")
    report = word_tools.compare_docx(before, after)
    assert report["ok"] is True
    compare = report["evidence"][-1]
    assert compare["kind"] == "docx-compare"
    assert compare["differences"]["sha256_changed"] is True


def test_to_markdown_writes_output(tmp):
    docx = tmp / "sample.docx"
    output = tmp / "sample.md"
    make_docx(docx, text="Hello Markdown Word")
    before_hash = word_tools.file_sha256(docx)
    report = word_tools.to_markdown(docx, output)
    assert report["ok"] is True
    assert output.exists()
    assert "Hello Markdown Word" in output.read_text(encoding="utf-8")
    assert word_tools.file_sha256(docx) == before_hash


def test_to_markdown_include_content_is_opt_in(tmp):
    docx = tmp / "sample.docx"
    make_docx(docx, text="W" * 900 + "WORD_CONTENT_TAIL")
    before_hash = word_tools.file_sha256(docx)
    compact = word_tools.to_markdown(docx, None)
    compact_evidence = next(item for item in compact["evidence"] if item["kind"] == "markdown")
    assert "content" not in compact_evidence
    assert_contains(compact["skipped"], "--include-content")
    full = word_tools.to_markdown(docx, None, include_content=True)
    full_evidence = next(item for item in full["evidence"] if item["kind"] == "markdown")
    assert len(full_evidence["content"]) == full_evidence["characters"]
    assert "WORD_CONTENT_TAIL" in full_evidence["content"][800:]
    assert full["writes"] == []
    assert full["artifacts"] == []
    assert not any("no --output path" in item for item in full["skipped"])
    assert word_tools.file_sha256(docx) == before_hash


def test_to_markdown_options_and_overwrite_guard(tmp):
    docx = tmp / "sample.docx"
    output = tmp / "sample.md"
    make_docx(docx, text="Hello Markdown Word")
    output.write_text("existing", encoding="utf-8", newline="\n")
    blocked = word_tools.to_markdown(docx, output, include_metadata=True, include_links=True, include_outline=True, include_assets=True)
    assert blocked["status"] == "blocked"
    forced = word_tools.to_markdown(docx, output, force=True, include_metadata=True, include_links=True, include_outline=True, include_assets=True)
    assert forced["ok"] is True
    text = output.read_text(encoding="utf-8")
    assert "Metadata Evidence" in text
    assert "Asset Evidence" in text
    child_blocked = word_tools.to_markdown(docx, docx / "content.md", force=True)
    assert child_blocked["status"] == "blocked"
    assert "input file" in " ".join(child_blocked["blocked"])


def test_replace_write_verify_and_output_guard(tmp):
    docx = tmp / "sample.docx"
    output = tmp / "changed.docx"
    make_docx(docx, text="Hello Word")
    output.write_text("existing", encoding="utf-8", newline="\n")
    blocked = word_tools.replace_text(docx, "Hello", "Goodbye", output, write=True)
    assert blocked["status"] == "blocked"
    output.unlink()
    report = word_tools.replace_text(docx, "Hello", "Goodbye", output, write=True, verify_output=True)
    assert report["ok"] is True
    assert output.exists()
    assert_contains(report["evidence"], "output-verification")


def test_active_content_warning(tmp):
    docx = tmp / "macro.docm"
    make_docx(docx)
    with zipfile.ZipFile(docx, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/vbaProject.bin", b"macro")
    report = word_tools.inspect_docx(docx)
    assert_contains(report["warnings"], "Active content")


def test_cli_json(tmp):
    docx = tmp / "sample.docx"
    make_docx(docx)
    assert word_tools.main(["inspect", "--file", str(docx), "--json"]) == 0


def test_format_depth_reports_and_assets(tmp):
    docx = tmp / "sample.docx"
    assets = tmp / "assets"
    make_docx(docx)
    metadata = word_tools.metadata_report(docx)
    links = word_tools.links_report(docx)
    outline = word_tools.outline_report(docx)
    accessibility = word_tools.accessibility_report(docx)
    dry_run = word_tools.extract_assets(docx, assets, write=False)
    written = word_tools.extract_assets(docx, assets, write=True)
    assert metadata["evidence"][0]["core"]["title"] == "Fixture Document"
    assert links["evidence"][0]["count"] == 1
    assert outline["evidence"][0]["headings"][0]["text"] == "Heading One"
    assert accessibility["warnings"]
    assert dry_run["status"] == "planned"
    assert dry_run["writes"] == []
    assert written["ok"] is True
    assert (assets / "word__media__image1.png").exists()
    assert (assets / "asset-manifest.json").exists()


def test_bundle_and_batch_evidence(tmp):
    first = tmp / "first.docx"
    second = tmp / "second.docx"
    make_docx(first, text="First Word")
    make_docx(second, text="Second Word")
    bundle_dir = tmp / "bundle"
    batch_dir = tmp / "batch"
    planned = word_tools.bundle_evidence(first, bundle_dir, write=False)
    written = word_tools.bundle_evidence(first, bundle_dir, write=True)
    batch = word_tools.batch_evidence([first, second], batch_dir, write=True)
    assert planned["status"] == "planned"
    assert_contains(planned["evidence"], "next-safe-commands")
    assert_contains(planned["findings"], "to-markdown")
    assert written["ok"] is True
    assert (bundle_dir / "evidence-bundle.json").exists()
    assert batch["ok"] is True
    assert (batch_dir / "batch-index.json").exists()


def test_evidence_output_dir_cannot_be_input_file(tmp):
    docx = tmp / "sample.docx"
    make_docx(docx)
    bundle = word_tools.bundle_evidence(docx, docx, write=True)
    batch = word_tools.batch_evidence([docx], docx, write=True)
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
        test_extract_markdown,
        test_comments_and_tracked_changes,
        test_replace_dry_run_writes_nothing,
        test_unsafe_zip_blocked,
        test_render_missing_tool_is_skipped,
        test_output_files_and_no_input_mutation,
        test_compare_json_shape,
        test_to_markdown_writes_output,
        test_to_markdown_include_content_is_opt_in,
        test_to_markdown_options_and_overwrite_guard,
        test_replace_write_verify_and_output_guard,
        test_active_content_warning,
        test_cli_json,
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
