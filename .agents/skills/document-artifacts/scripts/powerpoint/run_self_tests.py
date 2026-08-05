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

import powerpoint_tools


def assert_contains(items, text):
    assert any(text in str(item) for item in items), items


def make_pptx(path, unsafe=False, title="Title One"):
    presentation = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<p:sldSz cx="12192000" cy="6858000" type="screen16x9"/>'
        '<p:sldIdLst><p:sldId id="256" r:id="rId1"/><p:sldId id="257" r:id="rId2" show="0"/></p:sldIdLst>'
        "</p:presentation>"
    )
    slide1 = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        f'<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/></p:nvGrpSpPr>'
        f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title Shape"/></p:nvSpPr><p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p></p:txBody></p:sp>'
        '<p:pic><p:nvPicPr><p:cNvPr id="3" name="Picture 1"/></p:nvPicPr></p:pic>'
        "</p:spTree></p:cSld></p:sld>"
    )
    slide2 = slide1.replace(title, "Title Two")
    notes = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>Speaker note</a:t></p:notes>'
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
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Fixture Deck</dc:title></cp:coreProperties>'
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("docProps/core.xml", core)
        archive.writestr("docProps/app.xml", "<Properties><Application>Tests</Application></Properties>")
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/slides/slide1.xml", slide1)
        archive.writestr("ppt/slides/slide2.xml", slide2)
        archive.writestr("ppt/slides/_rels/slide1.xml.rels", rels)
        archive.writestr("ppt/notesSlides/notesSlide1.xml", notes)
        archive.writestr("ppt/media/image1.png", b"png")
        archive.writestr("ppt/charts/chart1.xml", "<chart/>")
        if unsafe:
            archive.writestr("../evil.xml", "bad")


def test_parser_help():
    help_text = powerpoint_tools.build_parser().format_help()
    assert "rearrange" in help_text
    assert "replace-text" in help_text
    assert "doctor" in help_text
    assert "compare" in help_text
    assert "to-markdown" in help_text
    assert "metadata" in help_text
    assert "extract-assets" in help_text
    assert "bundle-evidence" in help_text
    assert "batch" in help_text


def test_evidence_schema_file():
    schema = json.loads((SCRIPT_DIR.parents[1] / "docs" / "powerpoint-evidence-schema.json").read_text(encoding="utf-8"))
    report = powerpoint_tools.inspect_pptx(Path(__file__))
    for field in schema["required_fields"]:
        assert field in report


def test_inspect_json_shape(tmp):
    pptx = tmp / "sample.pptx"
    make_pptx(pptx)
    report = powerpoint_tools.inspect_pptx(pptx)
    assert report["schema_version"] == 1
    assert report["tool"] == "document-artifacts"
    assert report["ok"] is True
    assert report["input_sha256"]
    assert report["format"] == "pptx"
    assert "issues" in report
    assert report["evidence"][0]["slides"] == 2
    assert report["evidence"][0]["hidden_slides"] == 1


def test_doctor_json_shape():
    report = powerpoint_tools.doctor_report()
    assert report["command"] == "doctor"
    assert report["ok"] is True
    assert report["capabilities"]["python"] is True
    assert report["commands"]


def test_doctor_install_python_deps_is_explicit(tmp):
    report = powerpoint_tools.doctor_report(install_deps=True)
    assert report["ok"] is True
    assert "no optional Python packages" in " ".join(report["skipped"])


def test_extract_text_and_inventory(tmp):
    pptx = tmp / "sample.pptx"
    make_pptx(pptx)
    text = powerpoint_tools.extract_text(pptx)
    inventory = powerpoint_tools.inventory(pptx)
    assert "Title One" in str(text["evidence"])
    assert inventory["evidence"][0]["slides"][0]["title"] == "Title One"


def test_replace_dry_run_writes_nothing(tmp):
    pptx = tmp / "sample.pptx"
    make_pptx(pptx)
    report = powerpoint_tools.replace_text(pptx, "Title", "Heading", None, write=False)
    assert report["status"] == "planned"
    assert report["writes"] == []
    assert report["evidence"][0]["replacement_count"] >= 2


def test_rearrange_dry_run(tmp):
    pptx = tmp / "sample.pptx"
    make_pptx(pptx)
    report = powerpoint_tools.rearrange(pptx, "2,1", None, write=False)
    assert report["status"] == "planned"
    assert report["evidence"][0]["requested_order"] == [2, 1]


def test_invalid_rearrange_blocked(tmp):
    pptx = tmp / "sample.pptx"
    make_pptx(pptx)
    report = powerpoint_tools.rearrange(pptx, "1,1", None, write=False)
    assert report["status"] == "blocked"
    assert report["blocked"]


def test_unsafe_zip_blocked(tmp):
    pptx = tmp / "unsafe.pptx"
    make_pptx(pptx, unsafe=True)
    report = powerpoint_tools.inspect_pptx(pptx)
    assert report["status"] == "blocked"
    assert report["blocked"]


def test_render_missing_tool_is_skipped(tmp):
    pptx = tmp / "sample.pptx"
    make_pptx(pptx)
    original_which = powerpoint_tools.shutil.which
    powerpoint_tools.shutil.which = lambda name: None
    try:
        report = powerpoint_tools.render_deck(pptx, tmp / "pages", write=True)
    finally:
        powerpoint_tools.shutil.which = original_which
    assert report["status"] == "skipped"
    assert report["writes"] == []
    strict = powerpoint_tools.apply_strict(report, True)
    assert strict["status"] == "failed"


def test_cli_json(tmp):
    pptx = tmp / "sample.pptx"
    make_pptx(pptx)
    assert powerpoint_tools.main(["inspect", "--file", str(pptx), "--json"]) == 0


def test_output_files_and_no_input_mutation(tmp):
    pptx = tmp / "sample.pptx"
    out_json = tmp / "report.json"
    out_md = tmp / "report.md"
    make_pptx(pptx)
    before_hash = powerpoint_tools.file_sha256(pptx)
    status = powerpoint_tools.main(["inspect", "--file", str(pptx), "--output-json", str(out_json), "--output-md", str(out_md), "--json"])
    assert status == 0
    assert out_json.exists()
    assert out_md.exists()
    saved = json.loads(out_json.read_text(encoding="utf-8"))
    assert saved["artifacts"]
    assert powerpoint_tools.file_sha256(pptx) == before_hash


def test_compare_json_shape(tmp):
    before = tmp / "before.pptx"
    after = tmp / "after.pptx"
    make_pptx(before, title="Title One")
    make_pptx(after, title="Changed Title")
    report = powerpoint_tools.compare_pptx(before, after)
    assert report["ok"] is True
    compare = report["evidence"][-1]
    assert compare["kind"] == "pptx-compare"
    assert compare["differences"]["sha256_changed"] is True


def test_to_markdown_writes_output(tmp):
    pptx = tmp / "sample.pptx"
    output = tmp / "sample.md"
    make_pptx(pptx, title="Hello Markdown Deck")
    before_hash = powerpoint_tools.file_sha256(pptx)
    report = powerpoint_tools.to_markdown(pptx, output)
    assert report["ok"] is True
    assert output.exists()
    assert "Hello Markdown Deck" in output.read_text(encoding="utf-8")
    assert powerpoint_tools.file_sha256(pptx) == before_hash


def test_to_markdown_include_content_is_opt_in(tmp):
    pptx = tmp / "sample.pptx"
    make_pptx(pptx, title="P" * 900 + "POWERPOINT_CONTENT_TAIL")
    before_hash = powerpoint_tools.file_sha256(pptx)
    compact = powerpoint_tools.to_markdown(pptx, None)
    compact_evidence = next(item for item in compact["evidence"] if item["kind"] == "markdown")
    assert "content" not in compact_evidence
    assert_contains(compact["skipped"], "--include-content")
    full = powerpoint_tools.to_markdown(pptx, None, include_content=True)
    full_evidence = next(item for item in full["evidence"] if item["kind"] == "markdown")
    assert len(full_evidence["content"]) == full_evidence["characters"]
    assert "POWERPOINT_CONTENT_TAIL" in full_evidence["content"][800:]
    assert full["writes"] == []
    assert full["artifacts"] == []
    assert not any("no --output path" in item for item in full["skipped"])
    assert powerpoint_tools.file_sha256(pptx) == before_hash


def test_to_markdown_options_and_overwrite_guard(tmp):
    pptx = tmp / "sample.pptx"
    output = tmp / "sample.md"
    make_pptx(pptx, title="Hello Markdown Deck")
    output.write_text("existing", encoding="utf-8", newline="\n")
    blocked = powerpoint_tools.to_markdown(pptx, output, include_metadata=True, include_links=True, include_outline=True, include_assets=True)
    assert blocked["status"] == "blocked"
    forced = powerpoint_tools.to_markdown(pptx, output, force=True, include_metadata=True, include_links=True, include_outline=True, include_assets=True)
    assert forced["ok"] is True
    text = output.read_text(encoding="utf-8")
    assert "Metadata Evidence" in text
    assert "Asset Evidence" in text
    child_blocked = powerpoint_tools.to_markdown(pptx, pptx / "content.md", force=True)
    assert child_blocked["status"] == "blocked"
    assert "input file" in " ".join(child_blocked["blocked"])


def test_replace_and_rearrange_write_verify_and_output_guard(tmp):
    pptx = tmp / "sample.pptx"
    replaced = tmp / "replaced.pptx"
    rearranged = tmp / "rearranged.pptx"
    make_pptx(pptx)
    replaced.write_text("existing", encoding="utf-8", newline="\n")
    blocked = powerpoint_tools.replace_text(pptx, "Title", "Heading", replaced, write=True)
    assert blocked["status"] == "blocked"
    replaced.unlink()
    report = powerpoint_tools.replace_text(pptx, "Title", "Heading", replaced, write=True, verify_output=True)
    assert report["ok"] is True
    assert_contains(report["evidence"], "output-verification")
    rearranged_report = powerpoint_tools.rearrange(pptx, "2,1", rearranged, write=True, verify_output=True)
    assert rearranged_report["ok"] is True
    assert_contains(rearranged_report["evidence"], "output-verification")


def test_active_content_warning(tmp):
    pptx = tmp / "macro.pptm"
    make_pptx(pptx)
    with zipfile.ZipFile(pptx, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ppt/vbaProject.bin", b"macro")
    report = powerpoint_tools.inspect_pptx(pptx)
    assert_contains(report["warnings"], "Active content")


def test_format_depth_reports_and_assets(tmp):
    pptx = tmp / "sample.pptx"
    assets = tmp / "assets"
    make_pptx(pptx)
    metadata = powerpoint_tools.metadata_report(pptx)
    links = powerpoint_tools.links_report(pptx)
    outline = powerpoint_tools.outline_report(pptx)
    accessibility = powerpoint_tools.accessibility_report(pptx)
    dry_run = powerpoint_tools.extract_assets(pptx, assets, write=False)
    written = powerpoint_tools.extract_assets(pptx, assets, write=True)
    assert metadata["evidence"][0]["core"]["title"] == "Fixture Deck"
    assert metadata["evidence"][0]["slide_size"]["type"] == "screen16x9"
    assert links["evidence"][0]["count"] == 1
    assert outline["evidence"][0]["slides"][0]["title"] == "Title One"
    assert accessibility["warnings"]
    assert dry_run["status"] == "planned"
    assert dry_run["writes"] == []
    assert written["ok"] is True
    assert (assets / "ppt__media__image1.png").exists()
    assert (assets / "asset-manifest.json").exists()


def test_bundle_and_batch_evidence(tmp):
    first = tmp / "first.pptx"
    second = tmp / "second.pptx"
    make_pptx(first, title="First Deck")
    make_pptx(second, title="Second Deck")
    bundle_dir = tmp / "bundle"
    batch_dir = tmp / "batch"
    planned = powerpoint_tools.bundle_evidence(first, bundle_dir, write=False)
    written = powerpoint_tools.bundle_evidence(first, bundle_dir, write=True)
    batch = powerpoint_tools.batch_evidence([first, second], batch_dir, write=True)
    assert planned["status"] == "planned"
    assert_contains(planned["evidence"], "next-safe-commands")
    assert_contains(planned["findings"], "compare")
    assert written["ok"] is True
    assert (bundle_dir / "evidence-bundle.json").exists()
    assert batch["ok"] is True
    assert (batch_dir / "batch-index.json").exists()


def test_evidence_output_dir_cannot_be_input_file(tmp):
    pptx = tmp / "sample.pptx"
    make_pptx(pptx)
    bundle = powerpoint_tools.bundle_evidence(pptx, pptx, write=True)
    batch = powerpoint_tools.batch_evidence([pptx], pptx, write=True)
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
        test_extract_text_and_inventory,
        test_replace_dry_run_writes_nothing,
        test_rearrange_dry_run,
        test_invalid_rearrange_blocked,
        test_unsafe_zip_blocked,
        test_render_missing_tool_is_skipped,
        test_cli_json,
        test_output_files_and_no_input_mutation,
        test_compare_json_shape,
        test_to_markdown_writes_output,
        test_to_markdown_include_content_is_opt_in,
        test_to_markdown_options_and_overwrite_guard,
        test_replace_and_rearrange_write_verify_and_output_guard,
        test_active_content_warning,
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
