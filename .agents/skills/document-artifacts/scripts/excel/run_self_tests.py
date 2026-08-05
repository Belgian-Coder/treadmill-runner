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

import excel_tools


def assert_contains(items, text):
    assert any(text in str(item) for item in items), items


def make_xlsx(path, unsafe=False, formula="SUM(1,2)"):
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<workbookProtection lockStructure="1"/>'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/>'
        '<sheet name="Hidden" sheetId="2" state="hidden" r:id="rId2"/></sheets>'
        '<definedNames><definedName name="Print_Area">Sheet1!$A$1:$C$1</definedName></definedNames>'
        '<calcPr calcMode="manual"/></workbook>'
    )
    shared = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<si><t>Hello Excel</t></si></sst>"
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheetProtection sheet="1"/>'
        '<sheetData><row r="1">'
        '<c r="A1" t="s"><v>0</v></c>'
        f'<c r="B1"><f>{formula}</f><v>3</v></c>'
        '<c r="C1"><v>42</v></c>'
        '</row></sheetData><mergeCells><mergeCell ref="A2:B2"/></mergeCells>'
        '<hyperlinks><hyperlink ref="A1" r:id="rIdHyperlink"/></hyperlinks></worksheet>'
    )
    sheet_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdHyperlink" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.test" TargetMode="External"/>'
        "</Relationships>"
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Fixture Workbook</dc:title></cp:coreProperties>'
    )
    drawing = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing">'
        '<xdr:twoCellAnchor><xdr:pic><xdr:nvPicPr><xdr:cNvPr id="1" name="Picture 1"/></xdr:nvPicPr></xdr:pic></xdr:twoCellAnchor></xdr:wsDr>'
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("docProps/core.xml", core)
        archive.writestr("docProps/app.xml", "<Properties><Application>Tests</Application></Properties>")
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr("xl/worksheets/_rels/sheet1.xml.rels", sheet_rels)
        archive.writestr("xl/drawings/drawing1.xml", drawing)
        archive.writestr("xl/media/image1.png", b"png")
        archive.writestr("xl/charts/chart1.xml", "<chart/>")
        archive.writestr("xl/tables/table1.xml", "<table/>")
        archive.writestr("xl/externalLinks/externalLink1.xml", "<externalLink/>")
        if unsafe:
            archive.writestr("../evil.xml", "bad")


def test_parser_help():
    help_text = excel_tools.build_parser().format_help()
    assert "write-cells" in help_text
    assert "recalc-check" in help_text
    assert "doctor" in help_text
    assert "compare" in help_text
    assert "to-markdown" in help_text
    assert "metadata" in help_text
    assert "extract-assets" in help_text
    assert "bundle-evidence" in help_text
    assert "batch" in help_text


def test_evidence_schema_file():
    schema = json.loads((SCRIPT_DIR.parents[1] / "docs" / "excel-evidence-schema.json").read_text(encoding="utf-8"))
    report = excel_tools.inspect_xlsx(Path(__file__))
    for field in schema["required_fields"]:
        assert field in report


def test_inspect_json_shape(tmp):
    xlsx = tmp / "sample.xlsx"
    make_xlsx(xlsx)
    report = excel_tools.inspect_xlsx(xlsx)
    assert report["schema_version"] == 1
    assert report["tool"] == "document-artifacts"
    assert report["ok"] is True
    assert report["input_sha256"]
    assert report["format"] == "xlsx"
    assert "issues" in report
    assert report["evidence"][0]["formula_count"] == 1
    assert report["evidence"][0]["hidden_sheets"]


def test_doctor_json_shape():
    report = excel_tools.doctor_report()
    assert report["command"] == "doctor"
    assert report["ok"] is True
    assert report["capabilities"]["python"] is True
    assert report["commands"]


def test_doctor_install_python_deps_is_explicit(tmp):
    calls = []
    original_run = excel_tools.subprocess.run
    original_deps = excel_tools.LOCAL_DEPS

    def fake_run(command, **kwargs):
        calls.append(command)

        class Result:
            returncode = 0
            stdout = "ok"

        return Result()

    excel_tools.subprocess.run = fake_run
    excel_tools.LOCAL_DEPS = tmp / "deps"
    try:
        report = excel_tools.doctor_report(install_deps=True)
    finally:
        excel_tools.subprocess.run = original_run
        excel_tools.LOCAL_DEPS = original_deps
    assert report["ok"] is True
    assert calls
    assert "--target" in calls[0]


def test_extract_tables_and_formulas(tmp):
    xlsx = tmp / "sample.xlsx"
    make_xlsx(xlsx)
    tables = excel_tools.extract_tables(xlsx)
    formulas = excel_tools.formulas_report(xlsx)
    assert tables["ok"] is True
    assert "Hello Excel" in str(tables["evidence"])
    assert formulas["evidence"][0]["count"] == 1


def test_external_links(tmp):
    xlsx = tmp / "sample.xlsx"
    make_xlsx(xlsx)
    report = excel_tools.external_links_report(xlsx)
    assert len(report["evidence"][0]["parts"]) == 1


def test_write_cells_dry_run_writes_nothing(tmp):
    xlsx = tmp / "sample.xlsx"
    make_xlsx(xlsx)
    report = excel_tools.write_cells(xlsx, ["Sheet1!A1=Done"], None, write=False)
    assert report["status"] == "planned"
    assert report["writes"] == []


def test_write_cells_output_guard(tmp):
    xlsx = tmp / "sample.xlsx"
    output = tmp / "changed.xlsx"
    make_xlsx(xlsx)
    output.write_text("existing", encoding="utf-8", newline="\n")
    report = excel_tools.write_cells(xlsx, ["Sheet1!A1=Done"], output, write=True)
    assert report["status"] == "blocked"
    assert "already exists" in " ".join(report["blocked"])


def test_recalc_skips_when_no_renderer(tmp):
    xlsx = tmp / "sample.xlsx"
    make_xlsx(xlsx)
    original_which = excel_tools.shutil.which
    excel_tools.shutil.which = lambda name: None
    try:
        report = excel_tools.recalc_check(xlsx)
    finally:
        excel_tools.shutil.which = original_which
    assert report["status"] == "skipped"
    assert report["skipped"]
    strict = excel_tools.apply_strict(report, True)
    assert strict["status"] == "failed"


def test_unsafe_zip_blocked(tmp):
    xlsx = tmp / "unsafe.xlsx"
    make_xlsx(xlsx, unsafe=True)
    report = excel_tools.inspect_xlsx(xlsx)
    assert report["status"] == "blocked"
    assert report["blocked"]


def test_render_missing_tool_is_skipped(tmp):
    xlsx = tmp / "sample.xlsx"
    make_xlsx(xlsx)
    original_which = excel_tools.shutil.which
    excel_tools.shutil.which = lambda name: None
    try:
        report = excel_tools.render_workbook(xlsx, tmp / "pages", write=True)
    finally:
        excel_tools.shutil.which = original_which
    assert report["status"] == "skipped"
    assert report["writes"] == []


def test_cli_json(tmp):
    xlsx = tmp / "sample.xlsx"
    make_xlsx(xlsx)
    assert excel_tools.main(["inspect", "--file", str(xlsx), "--json"]) == 0


def test_output_files_and_no_input_mutation(tmp):
    xlsx = tmp / "sample.xlsx"
    out_json = tmp / "report.json"
    out_md = tmp / "report.md"
    make_xlsx(xlsx)
    before_hash = excel_tools.file_sha256(xlsx)
    status = excel_tools.main(["inspect", "--file", str(xlsx), "--output-json", str(out_json), "--output-md", str(out_md), "--json"])
    assert status == 0
    assert out_json.exists()
    assert out_md.exists()
    saved = json.loads(out_json.read_text(encoding="utf-8"))
    assert saved["artifacts"]
    assert excel_tools.file_sha256(xlsx) == before_hash


def test_compare_json_shape(tmp):
    before = tmp / "before.xlsx"
    after = tmp / "after.xlsx"
    make_xlsx(before, formula="SUM(1,2)")
    make_xlsx(after, formula="SUM(1,2,3)")
    report = excel_tools.compare_xlsx(before, after)
    assert report["ok"] is True
    compare = report["evidence"][-1]
    assert compare["kind"] == "xlsx-compare"
    assert compare["differences"]["sha256_changed"] is True


def test_to_markdown_writes_output(tmp):
    xlsx = tmp / "sample.xlsx"
    output = tmp / "sample.md"
    make_xlsx(xlsx)
    before_hash = excel_tools.file_sha256(xlsx)
    report = excel_tools.to_markdown(xlsx, output)
    assert report["ok"] is True
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert "Hello Excel" in text
    assert "SUM(1,2)" in text
    assert excel_tools.file_sha256(xlsx) == before_hash


def test_to_markdown_include_content_is_opt_in(tmp):
    xlsx = tmp / "sample.xlsx"
    make_xlsx(xlsx, formula="A" * 900 + "EXCEL_CONTENT_TAIL")
    before_hash = excel_tools.file_sha256(xlsx)
    compact = excel_tools.to_markdown(xlsx, None)
    compact_evidence = next(item for item in compact["evidence"] if item["kind"] == "markdown")
    assert "content" not in compact_evidence
    assert_contains(compact["skipped"], "--include-content")
    full = excel_tools.to_markdown(xlsx, None, include_content=True)
    full_evidence = next(item for item in full["evidence"] if item["kind"] == "markdown")
    assert len(full_evidence["content"]) == full_evidence["characters"]
    assert "EXCEL_CONTENT_TAIL" in full_evidence["content"][800:]
    assert full["writes"] == []
    assert full["artifacts"] == []
    assert not any("no --output path" in item for item in full["skipped"])
    assert excel_tools.file_sha256(xlsx) == before_hash


def test_to_markdown_options_and_overwrite_guard(tmp):
    xlsx = tmp / "sample.xlsx"
    output = tmp / "sample.md"
    make_xlsx(xlsx)
    output.write_text("existing", encoding="utf-8", newline="\n")
    blocked = excel_tools.to_markdown(xlsx, output, include_metadata=True, include_links=True, include_outline=True, include_assets=True)
    assert blocked["status"] == "blocked"
    forced = excel_tools.to_markdown(xlsx, output, force=True, include_metadata=True, include_links=True, include_outline=True, include_assets=True)
    assert forced["ok"] is True
    text = output.read_text(encoding="utf-8")
    assert "Metadata Evidence" in text
    assert "Asset Evidence" in text
    child_blocked = excel_tools.to_markdown(xlsx, xlsx / "content.md", force=True)
    assert child_blocked["status"] == "blocked"
    assert "input file" in " ".join(child_blocked["blocked"])


def test_active_content_warning(tmp):
    xlsx = tmp / "macro.xlsm"
    make_xlsx(xlsx)
    with zipfile.ZipFile(xlsx, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/vbaProject.bin", b"macro")
    report = excel_tools.inspect_xlsx(xlsx)
    assert_contains(report["warnings"], "Active content")


def test_format_depth_reports_and_assets(tmp):
    xlsx = tmp / "sample.xlsx"
    assets = tmp / "assets"
    make_xlsx(xlsx, formula="NOW()")
    metadata = excel_tools.metadata_report(xlsx)
    links = excel_tools.links_report(xlsx)
    outline = excel_tools.outline_report(xlsx)
    accessibility = excel_tools.accessibility_report(xlsx)
    dry_run = excel_tools.extract_assets(xlsx, assets, write=False)
    written = excel_tools.extract_assets(xlsx, assets, write=True)
    assert metadata["evidence"][0]["core"]["title"] == "Fixture Workbook"
    assert metadata["evidence"][0]["workbook_protection"] is True
    assert links["evidence"][0]["count"] >= 1
    assert outline["evidence"][0]["formula_count"] == 1
    assert accessibility["warnings"]
    assert dry_run["status"] == "planned"
    assert dry_run["writes"] == []
    assert written["ok"] is True
    assert (assets / "xl__media__image1.png").exists()
    assert (assets / "asset-manifest.json").exists()


def test_bundle_and_batch_evidence(tmp):
    first = tmp / "first.xlsx"
    second = tmp / "second.xlsx"
    make_xlsx(first, formula="SUM(1,2)")
    make_xlsx(second, formula="SUM(1,2,3)")
    bundle_dir = tmp / "bundle"
    batch_dir = tmp / "batch"
    planned = excel_tools.bundle_evidence(first, bundle_dir, write=False)
    written = excel_tools.bundle_evidence(first, bundle_dir, write=True)
    batch = excel_tools.batch_evidence([first, second], batch_dir, write=True)
    assert planned["status"] == "planned"
    assert_contains(planned["evidence"], "next-safe-commands")
    assert_contains(planned["findings"], "extract-assets")
    assert written["ok"] is True
    assert (bundle_dir / "evidence-bundle.json").exists()
    assert batch["ok"] is True
    assert (batch_dir / "batch-index.json").exists()


def test_evidence_output_dir_cannot_be_input_file(tmp):
    xlsx = tmp / "sample.xlsx"
    make_xlsx(xlsx)
    bundle = excel_tools.bundle_evidence(xlsx, xlsx, write=True)
    batch = excel_tools.batch_evidence([xlsx], xlsx, write=True)
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
        test_extract_tables_and_formulas,
        test_external_links,
        test_write_cells_dry_run_writes_nothing,
        test_write_cells_output_guard,
        test_recalc_skips_when_no_renderer,
        test_unsafe_zip_blocked,
        test_render_missing_tool_is_skipped,
        test_cli_json,
        test_output_files_and_no_input_mutation,
        test_compare_json_shape,
        test_to_markdown_writes_output,
        test_to_markdown_include_content_is_opt_in,
        test_to_markdown_options_and_overwrite_guard,
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
