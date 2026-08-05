#!/usr/bin/env python3

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "markdown_tools.py"
SPEC = importlib.util.spec_from_file_location("markdown_tools", SCRIPT)
markdown_tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(markdown_tools)


def test_clean_markdown():
    report = markdown_tools.scan_markdown("# Safe\n\n[Docs](https://example.test)\n")
    assert report["status"] == "passed"
    assert report["score"] == 1.0
    assert report["detected_issue_count"] == 0
    print("PASS test_clean_markdown")


def test_security_findings_are_located_and_redacted():
    sensitive_sample = "sk" + "_" + ("x" * 16)
    markdown = f"# Risk\n[x](javascript:alert(1))\n{sensitive_sample}\n\x01"
    report = markdown_tools.scan_markdown(markdown)
    assert [item["id"] for item in report["issues"]] == [
        "JAVASCRIPT_LINK",
        "SECRET_LIKE_TOKEN",
        "CONTROL_CHARACTER",
    ]
    assert report["issues"][0]["line"] == 2
    assert report["issues"][1]["line"] == 3
    assert report["issues"][2]["line"] == 4
    assert report["score"] == 0.45
    assert sensitive_sample not in json.dumps(report)
    print("PASS test_security_findings_are_located_and_redacted")


def test_angle_html_and_modern_secret_forms_are_detected():
    modern_secret = "sk" + "-proj-" + ("z" * 20)
    markdown = (
        "[angle](<javascript:alert(1)>)\n"
        "<a href='javascript:alert(2)'>html</a>\n"
        f"{modern_secret}\n"
    )
    report = markdown_tools.scan_markdown(markdown)
    assert [item["id"] for item in report["issues"]] == [
        "JAVASCRIPT_LINK",
        "JAVASCRIPT_LINK",
        "SECRET_LIKE_TOKEN",
    ]
    assert [item["line"] for item in report["issues"]] == [1, 2, 3]
    assert report["ok"] is False
    assert modern_secret not in json.dumps(report)
    print("PASS test_angle_html_and_modern_secret_forms_are_detected")


def test_reference_definitions_and_hidden_format_characters_are_detected():
    markdown = (
        "[first][unsafe]\n"
        "\n"
        "[unsafe]: javascript:alert(1)\n"
        "   [angle]: <javascript:alert(2)>\n"
        "[split-plain]:\n"
        "  javascript:alert(3)\n"
        "[split-angle]:  \n"
        "   <javascript:alert(4)>\n"
        "[safe]: https://example.test/path\n"
        "[safe-split]:\n"
        "  <https://example.test/split>\n"
        "visible\u202ehidden\n"
        "\u2066isolated\u2069\n"
    )
    report = markdown_tools.scan_markdown(markdown)
    assert [item["id"] for item in report["issues"]] == [
        "JAVASCRIPT_LINK",
        "JAVASCRIPT_LINK",
        "JAVASCRIPT_LINK",
        "JAVASCRIPT_LINK",
        "HIDDEN_FORMAT_CHARACTER",
        "HIDDEN_FORMAT_CHARACTER",
        "HIDDEN_FORMAT_CHARACTER",
    ]
    assert [item["line"] for item in report["issues"]] == [3, 4, 5, 7, 12, 13, 13]
    hidden_offsets = [item["offset"] for item in report["issues"] if item["id"] == "HIDDEN_FORMAT_CHARACTER"]
    assert hidden_offsets == [markdown.index("\u202e"), markdown.index("\u2066"), markdown.index("\u2069")]
    serialized = json.dumps(report)
    assert "javascript:alert" not in serialized
    assert "visible\u202ehidden" not in serialized
    print("PASS test_reference_definitions_and_hidden_format_characters_are_detected")


def test_encoded_and_escaped_javascript_schemes_are_detected():
    unsafe_samples = [
        "[x](javascript&#58;alert(1))",
        "[x](javascript\\:alert(1))",
        "[x](&#106;avascript&colon;alert(1))",
        "[x]: javascript&#58;alert(1)",
        "[x]: javascript\\:alert(1)",
        "<a href=javascript&#58;alert(1)>x</a>",
        "<a href='&#106;avascript&colon;alert(1)'>x</a>",
    ]
    for markdown in unsafe_samples:
        report = markdown_tools.scan_markdown(markdown)
        assert report["detected_issue_count"] == 1, markdown
        assert report["issues"][0]["id"] == "JAVASCRIPT_LINK"
        serialized = json.dumps(report)
        assert "alert(1)" not in serialized
        assert "&#58;" not in serialized

    safe_samples = [
        "[x](https&#58;//example.test)",
        "[x]: <https&#58;//example.test>",
        "<a href='https&#58;//example.test'>x</a>",
    ]
    for markdown in safe_samples:
        assert markdown_tools.scan_markdown(markdown)["detected_issue_count"] == 0
    print("PASS test_encoded_and_escaped_javascript_schemes_are_detected")


def test_reference_definitions_support_cr_and_escaped_labels():
    markdown = (
        "[plain]:\r javascript:alert(1)\r"
        "[angle]:\r <javascript&#58;alert(2)>\r"
        "[escaped\\]]: javascript\\:alert(3)\r"
        "[safe\\]]:\r <https&#58;//example.test>\r"
    )
    report = markdown_tools.scan_markdown(markdown)
    assert [item["id"] for item in report["issues"]] == [
        "JAVASCRIPT_LINK",
        "JAVASCRIPT_LINK",
        "JAVASCRIPT_LINK",
    ]
    assert [item["line"] for item in report["issues"]] == [1, 3, 5]
    assert "alert(" not in json.dumps(report)
    print("PASS test_reference_definitions_support_cr_and_escaped_labels")


def test_commonmark_containers_multiline_labels_and_autolinks_are_detected():
    samples = [
        ("[Foo\n  bar]: javascript:alert(1)", 1),
        ("[Foo\r\n  bar]: javascript:alert(1)", 1),
        ("> [foo]: javascript:alert(1)", 1),
        ("> [Foo\n>   bar]: javascript:alert(1)", 1),
        ("- [foo]: javascript:alert(1)", 1),
        ("<javascript:alert(1)>", 1),
        ("<javascript&#58;alert(1)>", 1),
    ]
    for markdown, expected_line in samples:
        report = markdown_tools.scan_markdown(markdown)
        assert report["detected_issue_count"] == 1, markdown
        assert report["issues"][0]["id"] == "JAVASCRIPT_LINK"
        assert report["issues"][0]["line"] == expected_line
    print("PASS test_commonmark_containers_multiline_labels_and_autolinks_are_detected")


def test_balanced_empty_and_multiline_inline_links_are_detected():
    unsafe_samples = [
        "[](javascript:alert(1))",
        "[outer [inner]](javascript:alert(1))",
        "[![alt](https://safe.test)](javascript:alert(1))",
        "[outer\ninner](javascript:alert(1))",
        "[`]`](javascript:alert(1))",
    ]
    for markdown in unsafe_samples:
        report = markdown_tools.scan_markdown(markdown)
        assert report["detected_issue_count"] == 1, markdown
        assert report["issues"][0]["id"] == "JAVASCRIPT_LINK"

    nested_image = "[![alt](javascript:alert(1))](https://safe.test)"
    assert markdown_tools.scan_markdown(nested_image)["detected_issue_count"] == 1
    both_unsafe = "[![alt](javascript:alert(1))](javascript:alert(2))"
    assert markdown_tools.scan_markdown(both_unsafe)["detected_issue_count"] == 2
    print("PASS test_balanced_empty_and_multiline_inline_links_are_detected")


def test_inline_titles_and_escaped_code_delimiters_preserve_detection():
    unsafe_samples = [
        "[x](javascript:alert(1) (title))",
        "[x](javascript:alert(1)\n \"title\")",
        "[x](javascript:alert(1)\r\n \"title\")",
        "[x](javascript:alert(1)\r \"title\")",
        "\\`[x](javascript:alert(1))`",
    ]
    for markdown in unsafe_samples:
        report = markdown_tools.scan_markdown(markdown)
        assert report["detected_issue_count"] == 1, markdown
    print("PASS test_inline_titles_and_escaped_code_delimiters_preserve_detection")


def test_container_fence_closing_and_invalid_info_strings_do_not_hide_links():
    blockquote_fence = (
        "> ```markdown\n"
        "> [inside](javascript:alert(1))\n"
        "> ```\n"
        "[outside](javascript:alert(2))\n"
    )
    report = markdown_tools.scan_markdown(blockquote_fence)
    assert report["detected_issue_count"] == 1
    assert report["issues"][0]["line"] == 4

    invalid_info = "``` bad`info\n[x](javascript:alert(1))\n```"
    assert markdown_tools.scan_markdown(invalid_info)["detected_issue_count"] == 1
    print("PASS test_container_fence_closing_and_invalid_info_strings_do_not_hide_links")


def test_non_link_code_and_escape_contexts_remain_clean():
    safe_samples = [
        "`[x](javascript:alert(1))`",
        "`[x](javascript:alert(1))\\`",
        "```markdown\n[x](javascript:alert(1))\n```",
        "~~~\n<a href='javascript:alert(1)'>x</a>\n~~~",
        "\\[x](javascript:alert(1))",
        "text ](javascript:alert(1))",
        "paragraph text\n[x]: javascript:alert(1)",
        "> paragraph text\n> [x]: javascript:alert(1)",
        "- ```markdown\n  [x](javascript:alert(1))\n  ```",
        "\\<javascript:alert(1)>",
        "<a href='javascript\\:alert(1)'>x</a>",
        "[x](javascript\\&colon;alert(1))",
        "[x](javascript&#58alert(1))",
    ]
    for markdown in safe_samples:
        report = markdown_tools.scan_markdown(markdown)
        assert report["detected_issue_count"] == 0, markdown

    escaped_backslash_link = "\\\\[x](javascript:alert(1))"
    assert markdown_tools.scan_markdown(escaped_backslash_link)["detected_issue_count"] == 1
    print("PASS test_non_link_code_and_escape_contexts_remain_clean")


def test_hidden_format_character_policy_is_table_driven():
    policy_samples = {
        "\u00ad": "soft hyphen",
        "\u061c": "arabic letter mark",
        "\u180e": "mongolian vowel separator",
        "\u2060": "word joiner",
    }
    for character, label in policy_samples.items():
        report = markdown_tools.scan_markdown(f"before{character}after")
        assert report["detected_issue_count"] == 1
        finding = report["issues"][0]
        assert finding["id"] == "HIDDEN_FORMAT_CHARACTER"
        assert label in finding["message"]
        assert finding["offset"] == 6
    print("PASS test_hidden_format_character_policy_is_table_driven")


def test_issue_cap_and_strict_exit():
    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / "unsafe.md"
        path.write_text("\n".join("[x](javascript:alert(1))" for _ in range(3)), encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPT),
                "scan",
                "--file",
                str(path),
                "--max-issues",
                "2",
                "--strict",
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    report = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert report["detected_issue_count"] == 3
    assert report["emitted_issue_count"] == 2
    assert report["truncated"] is True
    print("PASS test_issue_cap_and_strict_exit")


def test_resource_bounds_are_explicit():
    original_limit = markdown_tools.MAX_PROCESSED_ISSUES
    markdown_tools.MAX_PROCESSED_ISSUES = 2
    try:
        report = markdown_tools.scan_markdown("\x01\x02\x03")
    finally:
        markdown_tools.MAX_PROCESSED_ISSUES = original_limit
    assert report["processing_truncated"] is True
    assert report["detected_issue_count_is_lower_bound"] is True
    assert report["truncated"] is True

    try:
        markdown_tools.scan_markdown("x" * (markdown_tools.MAX_INPUT_CHARACTERS + 1))
    except ValueError as exc:
        assert "scan limit" in str(exc)
    else:
        raise AssertionError("oversized Markdown should be rejected")
    print("PASS test_resource_bounds_are_explicit")


def test_link_scan_work_budget_fails_closed():
    malformed = "[x](((((" * 500
    bounded = markdown_tools.markdown_link_destinations(
        malformed,
        max_work=64,
    )
    assert bounded["truncated"] is True
    assert bounded["truncated_at"] is not None
    assert bounded["work"] <= bounded["budget"] == 64

    report = markdown_tools.scan_markdown(malformed)
    finding_ids = {finding["id"] for finding in report["issues"]}
    assert report["ok"] is False
    assert report["processing_truncated"] is True
    assert report["detected_issue_count_is_lower_bound"] is True
    assert report["link_scan_work"] <= report["link_scan_budget"]
    assert "MARKDOWN_LINK_SCAN_LIMIT" in finding_ids
    print("PASS test_link_scan_work_budget_fails_closed")


def test_reference_context_scanning_is_incremental():
    repeated = "[root]: https://safe.test\n" + (
        "paragraph\n[x]: https://safe.test\n" * 5_000
    )
    report = markdown_tools.scan_markdown(repeated)
    assert report["ok"] is True
    assert report["processing_truncated"] is False
    assert report["detected_issue_count"] == 0
    assert report["reference_context_work"] <= len(repeated) * 3
    print("PASS test_reference_context_scanning_is_incremental")


def test_raw_html_scanning_is_single_pass():
    malformed = "<a" * 10_000
    report = markdown_tools.scan_markdown(malformed)
    assert report["ok"] is True
    assert report["detected_issue_count"] == 0
    assert report["html_scan_work"] <= len(malformed) * 8

    duplicate_attributes = (
        '<a href="https://safe.test" href="javascript:alert(1)">x</a>'
    )
    report = markdown_tools.scan_markdown(duplicate_attributes)
    javascript_findings = [
        finding
        for finding in report["issues"]
        if finding["id"] == "JAVASCRIPT_LINK"
    ]
    assert len(javascript_findings) == 1

    for newline in ("\n", "\r\n", "\r"):
        multiline = f'<a{newline} href="javascript&#58;alert(1)">x</a>'
        report = markdown_tools.scan_markdown(multiline)
        finding_ids = {finding["id"] for finding in report["issues"]}
        assert "JAVASCRIPT_LINK" in finding_ids

    quoted_text = (
        '<span title="ordinary href=javascript:alert(1) text">safe</span>'
    )
    report = markdown_tools.scan_markdown(quoted_text)
    assert report["detected_issue_count"] == 0

    quoted_angle = '<a title=">" href="javascript:alert(1)">x</a>'
    report = markdown_tools.scan_markdown(quoted_angle)
    finding_ids = {finding["id"] for finding in report["issues"]}
    assert "JAVASCRIPT_LINK" in finding_ids
    print("PASS test_raw_html_scanning_is_single_pass")


def test_large_inline_code_is_not_charged_as_link_work():
    code_only = "`[x](javascript:alert(1))`\n" * 20_000
    report = markdown_tools.scan_markdown(code_only)
    assert report["ok"] is True
    assert report["processing_truncated"] is False
    assert report["detected_issue_count"] == 0
    assert report["link_scan_work"] < report["link_scan_budget"]
    print("PASS test_large_inline_code_is_not_charged_as_link_work")


def test_code_delimiter_state_is_linear_and_ignores_raw_tag_attributes():
    unique_runs = "".join("`" * length + "x" for length in range(2, 302))
    paired_runs = "".join(("`" * length + "x") * 2 for length in range(2, 302))
    report = markdown_tools.scan_markdown(unique_runs + paired_runs)
    assert report["code_scan_work"] <= report["scanned_characters"] * 5

    raw_tag_delimiters = '<span title="`"> {payload} <span title="`">'
    payloads = (
        '<a href="javascript:alert(1)">x</a>',
        '[x](javascript:alert(1))',
        '<javascript:alert(1)>',
    )
    for payload in payloads:
        report = markdown_tools.scan_markdown(
            raw_tag_delimiters.format(payload=payload)
        )
        finding_ids = {finding["id"] for finding in report["issues"]}
        assert "JAVASCRIPT_LINK" in finding_ids

    code_wrapped_html = '`<a href="javascript:alert(1)">x</a>`'
    report = markdown_tools.scan_markdown(code_wrapped_html)
    assert report["detected_issue_count"] == 0
    print("PASS test_code_delimiter_state_is_linear_and_ignores_raw_tag_attributes")


def test_earlier_code_spans_take_precedence_over_later_raw_html():
    active_after_tag = '`<a title="`">[x](javascript:alert(1))`'
    report = markdown_tools.scan_markdown(active_after_tag)
    javascript_findings = [
        finding
        for finding in report["issues"]
        if finding["id"] == "JAVASCRIPT_LINK"
    ]
    assert len(javascript_findings) == 1
    assert javascript_findings[0]["offset"] == 14
    assert javascript_findings[0]["line"] == 1
    assert javascript_findings[0]["column"] == 15

    active_inside_apparent_tag = '`<a title="`[x](javascript:alert(1))">'
    report = markdown_tools.scan_markdown(active_inside_apparent_tag)
    finding_ids = {finding["id"] for finding in report["issues"]}
    assert "JAVASCRIPT_LINK" in finding_ids

    unmatched_code_opener = '`<a href="javascript:alert(1)">safe</a>'
    report = markdown_tools.scan_markdown(unmatched_code_opener)
    html_findings = [
        finding
        for finding in report["issues"]
        if finding["id"] == "JAVASCRIPT_LINK"
        and "HTML link" in finding["message"]
    ]
    assert len(html_findings) == 1
    print("PASS test_earlier_code_spans_take_precedence_over_later_raw_html")


def test_earlier_autolinks_take_precedence_over_later_code_spans():
    earlier_uri = '<https://example.test/`>[x](javascript:alert(1))`'
    report = markdown_tools.scan_markdown(earlier_uri)
    javascript_findings = [
        finding
        for finding in report["issues"]
        if finding["id"] == "JAVASCRIPT_LINK"
    ]
    assert len(javascript_findings) == 1
    assert javascript_findings[0]["offset"] == 24
    assert javascript_findings[0]["line"] == 1
    assert javascript_findings[0]["column"] == 25

    earlier_email = '<foo`@bar.example>[x](javascript:alert(1))`'
    report = markdown_tools.scan_markdown(earlier_email)
    finding_ids = {finding["id"] for finding in report["issues"]}
    assert "JAVASCRIPT_LINK" in finding_ids

    unicode_space_uri = '<javascript:\u00a0alert(1)>'
    report = markdown_tools.scan_markdown(unicode_space_uri)
    finding_ids = {finding["id"] for finding in report["issues"]}
    assert "JAVASCRIPT_LINK" in finding_ids

    inert_inside_uri = '<https:[x](javascript:alert(1))>'
    report = markdown_tools.scan_markdown(inert_inside_uri)
    assert report["detected_issue_count"] == 0

    escaped_uri = '\\<https://example.test/`>[x](javascript:alert(1))`'
    report = markdown_tools.scan_markdown(escaped_uri)
    assert report["detected_issue_count"] == 0

    code_wins_when_it_opens_first = '`<https://example.test/`[x](javascript:alert(1))>'
    report = markdown_tools.scan_markdown(code_wins_when_it_opens_first)
    finding_ids = {finding["id"] for finding in report["issues"]}
    assert "JAVASCRIPT_LINK" in finding_ids
    print("PASS test_earlier_autolinks_take_precedence_over_later_code_spans")


def test_raw_html_construct_backticks_do_not_hide_active_links():
    backtick = "`"
    payload = "[x](javascript:alert(1))"
    wrappers = (
        ("<!-- ", " -->"),
        ("<?audit ", " ?>"),
        ("<!DOCTYPE ", " >"),
        ("<![CDATA[", "]]>"),
    )
    for prefix, suffix in wrappers:
        markdown = (
            prefix
            + backtick
            + suffix
            + payload
            + prefix
            + backtick
            + suffix
        )
        report = markdown_tools.scan_markdown(markdown)
        finding_ids = {finding["id"] for finding in report["issues"]}
        assert "JAVASCRIPT_LINK" in finding_ids
        assert report["html_scan_work"] <= len(markdown) * 8
    print("PASS test_raw_html_construct_backticks_do_not_hide_active_links")


def test_unterminated_raw_html_constructs_do_not_hide_active_links():
    payload = "[x](javascript:alert(1))"
    prefixes = ("<!-- ", "<?audit ", "<!DOCTYPE ", "<![CDATA[")
    for prefix in prefixes:
        markdown = "prefix " + prefix + payload
        report = markdown_tools.scan_markdown(markdown)
        finding_ids = {finding["id"] for finding in report["issues"]}
        assert "JAVASCRIPT_LINK" in finding_ids
        assert report["html_scan_work"] <= len(markdown) * 8

    repeated = ("<!-- " * 20_000) + payload
    report = markdown_tools.scan_markdown(repeated)
    finding_ids = {finding["id"] for finding in report["issues"]}
    assert "JAVASCRIPT_LINK" in finding_ids
    assert report["html_scan_work"] <= len(repeated) * 8
    print("PASS test_unterminated_raw_html_constructs_do_not_hide_active_links")


def test_raw_html_text_is_not_parsed_as_markdown():
    inert_html = (
        '<span title="[x](javascript:alert(1))">safe</span>'
        '<!-- [x](javascript:alert(1)) -->'
        '<?audit [x](javascript:alert(1)) ?>'
        '<!DOCTYPE "[x](javascript:alert(1))">'
        '<![CDATA[[x](javascript:alert(1))]]>'
    )
    report = markdown_tools.scan_markdown(inert_html)
    assert report["ok"] is True
    assert report["detected_issue_count"] == 0

    active_autolink = markdown_tools.scan_markdown("<javascript:alert(1)>")
    finding_ids = {finding["id"] for finding in active_autolink["issues"]}
    assert "JAVASCRIPT_LINK" in finding_ids

    invalid_raw_tags = (
        '<é title="[x](javascript:alert(1))">',
        '<aé title="[x](javascript:alert(1))">',
        '<a é="[x](javascript:alert(1))">',
        '<a\u00a0title="[x](javascript:alert(1))">',
        '<!é "[x](javascript:alert(1))">',
    )
    for invalid_raw_tag in invalid_raw_tags:
        report = markdown_tools.scan_markdown(invalid_raw_tag)
        finding_ids = {finding["id"] for finding in report["issues"]}
        assert "JAVASCRIPT_LINK" in finding_ids

    malformed_outer_tags = (
        '<x "<a href="javascript:alert(1)">',
        '<x "junk<a href="javascript:alert(1)">tail',
        '<x "<a href="javascript:alert(1)">">',
    )
    for malformed_outer_tag in malformed_outer_tags:
        report = markdown_tools.scan_markdown(malformed_outer_tag)
        finding_ids = {finding["id"] for finding in report["issues"]}
        assert "JAVASCRIPT_LINK" in finding_ids

    valid_quoted_text = '<x title="<a href=\'javascript:alert(1)\'>">'
    report = markdown_tools.scan_markdown(valid_quoted_text)
    assert report["ok"] is True
    assert report["detected_issue_count"] == 0

    current_declarations = (
        '<!x [x](javascript:alert(1))>',
        '<!ELEMENT[x](javascript:alert(1))>',
    )
    for declaration in current_declarations:
        report = markdown_tools.scan_markdown(declaration)
        assert report["ok"] is True
        assert report["detected_issue_count"] == 0

    invalid_tag_whitespace = (
        '<a\n\n title="[x](javascript:alert(1))">',
        '<a title\r\r="[x](javascript:alert(1))">',
        '<a title=\r\n\n"[x](javascript:alert(1))">',
        '</a\n\n [x](javascript:alert(1))>',
    )
    for malformed_tag in invalid_tag_whitespace:
        report = markdown_tools.scan_markdown(malformed_tag)
        finding_ids = {finding["id"] for finding in report["issues"]}
        assert "JAVASCRIPT_LINK" in finding_ids

    valid_tag_whitespace = '<a \t\r\n title="[x](javascript:alert(1))">'
    report = markdown_tools.scan_markdown(valid_tag_whitespace)
    assert report["ok"] is True
    assert report["detected_issue_count"] == 0

    escaped_raw_html = (
        '\\<a href="javascript:alert(1)">',
        '\\<!-- [x](javascript:alert(1)) -->',
    )
    for escaped_html in escaped_raw_html:
        report = markdown_tools.scan_markdown(escaped_html)
        html_findings = [
            finding
            for finding in report["issues"]
            if finding["id"] == "JAVASCRIPT_LINK"
            and "HTML link" in finding["message"]
        ]
        assert html_findings == []
    escaped_comment_ids = {
        finding["id"]
        for finding in markdown_tools.scan_markdown(escaped_raw_html[1])["issues"]
    }
    assert "JAVASCRIPT_LINK" in escaped_comment_ids

    escaped_tag_with_active_markdown = (
        '\\<span title="[x](javascript:alert(1))">'
    )
    report = markdown_tools.scan_markdown(escaped_tag_with_active_markdown)
    finding_ids = {finding["id"] for finding in report["issues"]}
    assert "JAVASCRIPT_LINK" in finding_ids

    even_backslashes = '\\\\<a href="javascript:alert(1)">'
    report = markdown_tools.scan_markdown(even_backslashes)
    finding_ids = {finding["id"] for finding in report["issues"]}
    assert "JAVASCRIPT_LINK" in finding_ids
    print("PASS test_raw_html_text_is_not_parsed_as_markdown")


def test_accepted_reference_line_end_scanning_is_incremental():
    for newline in ("\n", "\r"):
        references = (f"[x]: https://safe.test{newline}" * 20_000)
        report = markdown_tools.scan_markdown(references)
        assert report["ok"] is True
        assert report["processing_truncated"] is False
        assert report["reference_context_work"] <= len(references) * 4
    print("PASS test_accepted_reference_line_end_scanning_is_incremental")


def main():
    test_clean_markdown()
    test_security_findings_are_located_and_redacted()
    test_angle_html_and_modern_secret_forms_are_detected()
    test_reference_definitions_and_hidden_format_characters_are_detected()
    test_encoded_and_escaped_javascript_schemes_are_detected()
    test_reference_definitions_support_cr_and_escaped_labels()
    test_commonmark_containers_multiline_labels_and_autolinks_are_detected()
    test_balanced_empty_and_multiline_inline_links_are_detected()
    test_inline_titles_and_escaped_code_delimiters_preserve_detection()
    test_container_fence_closing_and_invalid_info_strings_do_not_hide_links()
    test_non_link_code_and_escape_contexts_remain_clean()
    test_hidden_format_character_policy_is_table_driven()
    test_issue_cap_and_strict_exit()
    test_resource_bounds_are_explicit()
    test_link_scan_work_budget_fails_closed()
    test_reference_context_scanning_is_incremental()
    test_raw_html_scanning_is_single_pass()
    test_large_inline_code_is_not_charged_as_link_work()
    test_code_delimiter_state_is_linear_and_ignores_raw_tag_attributes()
    test_earlier_code_spans_take_precedence_over_later_raw_html()
    test_earlier_autolinks_take_precedence_over_later_code_spans()
    test_raw_html_construct_backticks_do_not_hide_active_links()
    test_unterminated_raw_html_constructs_do_not_hide_active_links()
    test_raw_html_text_is_not_parsed_as_markdown()
    test_accepted_reference_line_end_scanning_is_incremental()
    print("markdown self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
