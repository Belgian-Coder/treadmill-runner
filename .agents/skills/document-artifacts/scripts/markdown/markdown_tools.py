#!/usr/bin/env python3

import argparse
import bisect
import html
import json
import re
import string
import sys
import unicodedata
from array import array
from pathlib import Path


REFERENCE_LINK_DESTINATION = re.compile(
    r"(?:^|(?<=\r))"
    r"(?P<container>[ \t]{0,3}(?:(?:>[ \t]?)|(?:[-+*][ \t]+)|(?:\d{1,9}[.)][ \t]+))*)"
    r"\[(?P<label>(?:\\[^\r\n]|[^\]\\\r\n]|(?:\r\n|\n|\r)(?:(?:>[ \t]?)+)?[ \t]{1,3}){1,999}?)\]:[ \t]*"
    r"(?:(?:\r\n|\n|\r)[ \t]{0,3})?"
    r"(?:<(?P<angle>[^>\r\n]*)>|(?P<plain>[^\s\r\n]+))",
    re.MULTILINE,
)
# The broad form remains a security candidate so encoded schemes such as
# ``javascript&#58;`` are still reported. The exact CommonMark form is used for
# inline precedence and therefore follows the normative URI/email grammar.
AUTOLINK_DESTINATION = re.compile(
    r"<(?P<angle>[^\x00-\x20\x7f<>]+)>"
)
COMMONMARK_AUTOLINK_DESTINATION = re.compile(
    r"<(?P<angle>(?:"
    r"[A-Za-z][A-Za-z0-9+.-]{1,31}:[^\x00-\x20\x7f<>]*"
    r"|"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
    r"))>"
)
FENCE_START = re.compile(
    r"^(?P<container>[ \t]{0,3}(?:(?:>[ \t]?)|(?:[-+*][ \t]+)|(?:\d{1,9}[.)][ \t]+))*)"
    r"[ \t]{0,3}(?P<fence>`{3,}|~{3,})"
)
COMMONMARK_CHARACTER_REFERENCE = re.compile(
    r"&(?:#[xX][0-9A-Fa-f]{1,8}|#[0-9]{1,8}|[A-Za-z][A-Za-z0-9]{1,31});"
)
MARKDOWN_ESCAPABLE = frozenset(string.punctuation)
REDACTION_CANDIDATE_PATTERN = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|(?:sk|api|token|key)_[A-Za-z0-9_-]{16,})\b",
    re.IGNORECASE,
)
PENALTIES = {"warning": 0.10, "error": 0.35}
MAX_INPUT_CHARACTERS = 2_000_000
MAX_INPUT_BYTES = 8_000_000
MAX_PROCESSED_ISSUES = 5_000
MAX_LINK_NESTING = 32
LINK_SCAN_WORK_MULTIPLIER = 8
MIN_LINK_SCAN_WORK = 4_096
HIDDEN_FORMAT_CHARACTERS = {
    "\u00ad": "soft hyphen",
    "\u061c": "arabic letter mark",
    "\u180e": "mongolian vowel separator",
    "\u200b": "zero-width space",
    "\u200c": "zero-width non-joiner",
    "\u200d": "zero-width joiner",
    "\ufeff": "byte-order mark",
    "\u200e": "left-to-right mark",
    "\u200f": "right-to-left mark",
    "\u202a": "left-to-right embedding",
    "\u202b": "right-to-left embedding",
    "\u202c": "pop directional formatting",
    "\u202d": "left-to-right override",
    "\u202e": "right-to-left override",
    "\u2060": "word joiner",
    "\u2066": "left-to-right isolate",
    "\u2067": "right-to-left isolate",
    "\u2068": "first strong isolate",
    "\u2069": "pop directional isolate",
}


def line_starts(text):
    starts = array("I", [0])
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\r":
            if index + 1 < len(text) and text[index + 1] == "\n":
                index += 1
            starts.append(index + 1)
        elif character == "\n":
            starts.append(index + 1)
        index += 1
    return starts


def location(starts, offset):
    line_index = max(0, bisect.bisect_right(starts, offset) - 1)
    return line_index + 1, offset - starts[line_index] + 1


def issue(rule_id, severity, message, starts, offset):
    line, column = location(starts, offset)
    return {
        "id": rule_id,
        "severity": severity,
        "message": message,
        "line": line,
        "column": column,
        "offset": offset,
    }


def captured_destination(match):
    for group_name in ("angle", "plain", "double", "single", "bare"):
        value = match.groupdict().get(group_name)
        if value is not None:
            return value
    return ""


def commonmark_entity_decode(value):
    return COMMONMARK_CHARACTER_REFERENCE.sub(
        lambda match: html.unescape(match.group(0)),
        value,
    )


def normalized_link_destination(destination, *, markdown_escapes=True):
    if not markdown_escapes:
        return html.unescape(destination).strip()
    pieces = []
    pending = []

    def flush_pending():
        if pending:
            pieces.append(commonmark_entity_decode("".join(pending)))
            pending.clear()

    index = 0
    while index < len(destination):
        character = destination[index]
        if (
            character == "\\"
            and index + 1 < len(destination)
            and destination[index + 1] in MARKDOWN_ESCAPABLE
        ):
            flush_pending()
            pieces.append(destination[index + 1])
            index += 2
            continue
        pending.append(character)
        index += 1
    flush_pending()
    return "".join(pieces).strip()


def is_javascript_destination(destination, *, markdown_escapes=True):
    normalized = normalized_link_destination(
        destination,
        markdown_escapes=markdown_escapes,
    )
    normalized = re.sub(r"[\t\n\r\f]+", "", normalized)
    return re.match(r"^javascript\s*:", normalized, re.IGNORECASE) is not None


def is_backslash_escaped(text, offset):
    count = 0
    index = offset - 1
    while index >= 0 and text[index] == "\\":
        count += 1
        index -= 1
    return count % 2 == 1


def link_spacing_end(text, offset):
    while offset < len(text) and text[offset] in " \t":
        offset += 1
    if offset < len(text) and text[offset] in "\r\n":
        if text.startswith("\r\n", offset):
            offset += 2
        else:
            offset += 1
        while offset < len(text) and text[offset] in " \t":
            offset += 1
        if offset < len(text) and text[offset] in "\r\n":
            return None
    return offset


def html_link_destinations(text):
    candidates = []
    tag_ranges = []
    missing_terminators = set()
    work = 0

    def ascii_alpha(character):
        return "A" <= character <= "Z" or "a" <= character <= "z"

    def ascii_alnum(character):
        return ascii_alpha(character) or "0" <= character <= "9"

    def html_space(character):
        return character in " \t\r\n"

    def consume_tag_space(cursor, end):
        """Consume spaces/tabs and at most one CommonMark line ending."""
        nonlocal work
        saw_line_ending = False
        while cursor < end:
            character = text[cursor]
            if character in " \t":
                work += 1
                cursor += 1
                continue
            if character not in "\r\n":
                break
            if saw_line_ending:
                return cursor, False
            saw_line_ending = True
            width = 2 if text.startswith("\r\n", cursor) else 1
            work += width
            cursor += width
        return cursor, True

    def tag_name_start(tag_start):
        cursor = tag_start + 1
        if cursor < len(text) and text[cursor] == "/":
            cursor += 1
        if cursor >= len(text) or not ascii_alpha(text[cursor]):
            return None
        return cursor

    def collect_attributes(tag_start, tag_end):
        nonlocal work
        attribute_index = tag_name_start(tag_start)
        if attribute_index is None:
            return False, []
        closing_tag = text[tag_start + 1] == "/"
        attribute_index += 1
        while attribute_index < tag_end and (
            ascii_alnum(text[attribute_index])
            or text[attribute_index] == "-"
        ):
            work += 1
            attribute_index += 1
        if closing_tag:
            attribute_index, valid_space = consume_tag_space(
                attribute_index,
                tag_end,
            )
            return valid_space and attribute_index == tag_end, []

        href_destinations = []
        while attribute_index < tag_end:
            whitespace_start = attribute_index
            attribute_index, valid_space = consume_tag_space(
                attribute_index,
                tag_end,
            )
            if not valid_space:
                return False, []
            if attribute_index >= tag_end:
                break
            if text[attribute_index] == "/":
                work += 1
                return attribute_index + 1 == tag_end, href_destinations
            if attribute_index == whitespace_start:
                return False, []
            if not (
                ascii_alpha(text[attribute_index])
                or text[attribute_index] in "_:"
            ):
                return False, []
            name_start = attribute_index
            attribute_index += 1
            while attribute_index < tag_end and (
                ascii_alnum(text[attribute_index])
                or text[attribute_index] in "_.:-"
            ):
                work += 1
                attribute_index += 1
            attribute_name = text[name_start:attribute_index]
            after_name = attribute_index
            attribute_index, valid_space = consume_tag_space(
                attribute_index,
                tag_end,
            )
            if not valid_space:
                return False, []
            if attribute_index >= tag_end or text[attribute_index] != "=":
                attribute_index = after_name
                continue
            work += 1
            attribute_index += 1
            attribute_index, valid_space = consume_tag_space(
                attribute_index,
                tag_end,
            )
            if not valid_space:
                return False, []
            if attribute_index >= tag_end:
                return False, []
            destination_start = attribute_index
            if text[attribute_index] in "\"'":
                delimiter = text[attribute_index]
                destination_start = attribute_index + 1
                attribute_index += 1
                while (
                    attribute_index < tag_end
                    and text[attribute_index] != delimiter
                ):
                    work += 1
                    attribute_index += 1
                if attribute_index >= tag_end:
                    return False, []
                destination_end = attribute_index
                work += 1
                attribute_index += 1
            else:
                if text[attribute_index] in "\"'=<>`":
                    return False, []
                while (
                    attribute_index < tag_end
                    and not html_space(text[attribute_index])
                ):
                    if text[attribute_index] in "\"'=<>`":
                        return False, []
                    work += 1
                    attribute_index += 1
                destination_end = attribute_index
                if destination_end == destination_start:
                    return False, []
            if attribute_name.lower() == "href":
                href_destinations.append(text[destination_start:destination_end])
        return True, href_destinations

    def terminated_construct_end(start, prefix, terminator):
        """Return the exclusive end of a raw HTML construct in one bounded scan."""
        nonlocal work
        if terminator in missing_terminators:
            return None
        content_start = start + len(prefix)
        terminator_start = text.find(terminator, content_start)
        if terminator_start < 0:
            work += len(text) - content_start
            missing_terminators.add(terminator)
            return None
        construct_end = terminator_start + len(terminator)
        work += construct_end - content_start
        return construct_end

    def declaration_end(start):
        """Return the exclusive end of a CommonMark HTML declaration."""
        nonlocal work
        cursor = start + 2
        if cursor >= len(text) or not ascii_alpha(text[cursor]):
            return None
        if ">" in missing_terminators:
            return None
        declaration_close = text.find(">", cursor + 1)
        if declaration_close < 0:
            work += len(text) - cursor
            missing_terminators.add(">")
            return None
        work += declaration_close + 1 - cursor
        return declaration_close + 1

    index = 0
    while index < len(text):
        tag_start = text.find("<", index)
        if tag_start < 0:
            work += len(text) - index
            break
        work += tag_start - index + 1
        if is_backslash_escaped(text, tag_start):
            index = tag_start + 1
            continue
        if text.startswith("<!--", tag_start):
            construct_end = terminated_construct_end(tag_start, "<!--", "-->")
            if construct_end is not None:
                tag_ranges.append((tag_start, construct_end))
                index = construct_end
            else:
                index = tag_start + 1
            continue
        if text.startswith("<![CDATA[", tag_start):
            construct_end = terminated_construct_end(
                tag_start,
                "<![CDATA[",
                "]]>",
            )
            if construct_end is not None:
                tag_ranges.append((tag_start, construct_end))
                index = construct_end
            else:
                index = tag_start + 1
            continue
        if text.startswith("<?", tag_start):
            construct_end = terminated_construct_end(tag_start, "<?", "?>")
            if construct_end is not None:
                tag_ranges.append((tag_start, construct_end))
                index = construct_end
            else:
                index = tag_start + 1
            continue
        if text.startswith("<!", tag_start):
            construct_end = declaration_end(tag_start)
            if construct_end is not None:
                tag_ranges.append((tag_start, construct_end))
                index = construct_end
                continue
        name_start = tag_name_start(tag_start)
        if name_start is None:
            index = tag_start + 1
            continue
        name_end = name_start + 1
        while (
            name_end < len(text)
            and (ascii_alnum(text[name_end]) or text[name_end] == "-")
        ):
            work += 1
            name_end += 1
        closing_tag = text[tag_start + 1] == "/"
        if (
            name_end >= len(text)
            or (
                not html_space(text[name_end])
                and text[name_end] not in (">" if closing_tag else "/>")
            )
        ):
            index = tag_start + 1
            continue
        cursor = name_end
        delimiter = None
        nested_fallback = None
        while cursor < len(text):
            work += 1
            character = text[cursor]
            if delimiter is not None:
                if character == delimiter:
                    delimiter = None
                elif (
                    character == "<"
                    and nested_fallback is None
                    and tag_name_start(cursor) is not None
                ):
                    nested_fallback = cursor
            elif character in "\"'":
                delimiter = character
            elif character == ">":
                break
            elif (
                text[cursor] == "<"
                and tag_name_start(cursor) is not None
            ):
                tag_start = cursor
                nested_fallback = None
            cursor += 1
        if cursor >= len(text) or text[cursor] != ">":
            if nested_fallback is not None:
                index = nested_fallback
                continue
            break
        valid_tag, href_destinations = collect_attributes(tag_start, cursor)
        if valid_tag:
            tag_ranges.append((tag_start, cursor + 1))
            candidates.extend(
                (tag_start, cursor + 1, destination)
                for destination in href_destinations
            )
        if not valid_tag and nested_fallback is not None:
            index = nested_fallback
        else:
            index = cursor + 1
    return {"candidates": candidates, "tag_ranges": tag_ranges, "work": work}


def markdown_link_destinations(text, code_ranges=None, max_work=None):
    candidates = []
    excluded = code_ranges or []
    excluded_starts = [start for start, _ in excluded]
    budget = (
        max(MIN_LINK_SCAN_WORK, len(text) * LINK_SCAN_WORK_MULTIPLIER)
        if max_work is None
        else max_work
    )
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
        raise ValueError("Markdown link scan work limit must be a positive integer.")
    work = 0
    truncated_at = None

    def spend(amount, offset):
        nonlocal work, truncated_at
        amount = max(0, amount)
        if amount > budget - work:
            truncated_at = min(max(0, offset), len(text))
            return False
        work += amount
        return True

    def escaped_at(offset):
        slash_count = 0
        escape_cursor = offset - 1
        while escape_cursor >= 0 and text[escape_cursor] == "\\":
            if not spend(1, offset):
                return None
            slash_count += 1
            escape_cursor -= 1
        return slash_count % 2 == 1

    def spacing_end(offset):
        while offset < len(text) and text[offset] in " \t":
            if not spend(1, offset):
                return None, True
            offset += 1
        if offset < len(text) and text[offset] in "\r\n":
            newline_width = 2 if text.startswith("\r\n", offset) else 1
            if not spend(newline_width, offset):
                return None, True
            offset += newline_width
            while offset < len(text) and text[offset] in " \t":
                if not spend(1, offset):
                    return None, True
                offset += 1
            if offset < len(text) and text[offset] in "\r\n":
                return None, False
        return offset, False

    index = 0
    while index < len(text):
        start = text.find("[", index)
        if start < 0:
            if not spend(len(text) - index, index):
                break
            break
        if not spend(start - index + 1, start):
            break
        containing = range_containing(excluded, excluded_starts, start)
        if containing:
            index = containing[1]
            continue
        escaped = escaped_at(start)
        if escaped is None:
            break
        if escaped:
            index = start + 1
            continue
        depth = 1
        too_deep = False
        cursor = start + 1
        while cursor < len(text) and depth:
            if not spend(1, cursor):
                break
            containing = range_containing(excluded, excluded_starts, cursor)
            if containing:
                cursor = containing[1]
                continue
            if text[cursor] == "\\" and cursor + 1 < len(text):
                cursor += 2
                continue
            if text[cursor] == "[":
                depth += 1
                if depth > MAX_LINK_NESTING:
                    too_deep = True
                    break
            elif text[cursor] == "]":
                depth -= 1
            cursor += 1
        if truncated_at is not None:
            break
        if depth or too_deep:
            index = start + 1
            continue
        label_end = cursor - 1
        label_length = label_end - start - 1
        if not spend(max(1, label_length * 3), start):
            break
        normalized_label = text[start + 1 : label_end].replace("\r\n", "\n").replace("\r", "\n")
        if re.search(r"\n[ \t]*\n", normalized_label):
            index = start + 1
            continue
        if cursor >= len(text) or text[cursor] != "(":
            index = start + 1
            continue
        cursor += 1
        while cursor < len(text) and text[cursor] in " \t":
            if not spend(1, cursor):
                break
            cursor += 1
        if truncated_at is not None:
            break
        if cursor < len(text) and text[cursor] == "<":
            destination_start = cursor + 1
            cursor += 1
            while cursor < len(text) and text[cursor] not in ">\r\n":
                if not spend(1, cursor):
                    break
                if text[cursor] == "\\" and cursor + 1 < len(text):
                    cursor += 2
                else:
                    cursor += 1
            if truncated_at is not None:
                break
            if cursor >= len(text) or text[cursor] != ">":
                index = start + 1
                continue
            destination = text[destination_start:cursor]
            if not spend(max(1, len(destination)), destination_start):
                break
            cursor += 1
        else:
            destination_start = cursor
            parenthesis_depth = 0
            while cursor < len(text):
                if not spend(1, cursor):
                    break
                character = text[cursor]
                if character == "\\" and cursor + 1 < len(text):
                    cursor += 2
                    continue
                if character in "\r\n\t " and parenthesis_depth == 0:
                    break
                if character == "(":
                    parenthesis_depth += 1
                elif character == ")":
                    if parenthesis_depth == 0:
                        break
                    parenthesis_depth -= 1
                cursor += 1
            if truncated_at is not None:
                break
            destination = text[destination_start:cursor]
            if not spend(max(1, len(destination)), destination_start):
                break
        cursor, spacing_truncated = spacing_end(cursor)
        if spacing_truncated:
            break
        if cursor is None:
            index = start + 1
            continue
        if cursor < len(text) and text[cursor] in "\"'(":
            opening_delimiter = text[cursor]
            delimiter = ")" if opening_delimiter == "(" else opening_delimiter
            cursor += 1
            while cursor < len(text) and text[cursor] != delimiter:
                if not spend(1, cursor):
                    break
                if text[cursor] == "\\" and cursor + 1 < len(text):
                    cursor += 2
                else:
                    cursor += 1
            if truncated_at is not None:
                break
            if cursor >= len(text):
                index = start + 1
                continue
            cursor += 1
            cursor, spacing_truncated = spacing_end(cursor)
            if spacing_truncated:
                break
            if cursor is None:
                index = start + 1
                continue
        if cursor >= len(text) or text[cursor] != ")":
            index = start + 1
            continue
        candidates.append((start, cursor + 1, destination))
        index = start + 1
    return {
        "candidates": candidates,
        "truncated": truncated_at is not None,
        "truncated_at": truncated_at,
        "work": work,
        "budget": budget,
    }


def merge_ranges(ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def range_containing(ranges, starts, offset):
    index = bisect.bisect_right(starts, offset) - 1
    if index >= 0 and offset < ranges[index][1]:
        return ranges[index]
    return None


def fenced_code_ranges(text, ignored_ranges=None):
    ranges = []
    ignored = ignored_ranges or []
    ignored_starts = [start for start, _ in ignored]
    active = None
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        if active is None:
            if range_containing(ignored, ignored_starts, offset):
                offset += len(line)
                continue
            match = FENCE_START.match(content)
            if match:
                fence = match.group("fence")
                if fence[0] == "`" and "`" in content[match.end() :]:
                    offset += len(line)
                    continue
                active = (
                    offset,
                    fence[0],
                    len(fence),
                    match.group("container").count(">"),
                )
        else:
            start, character, minimum, quote_count = active
            closing = re.match(
                rf"^(?:[ \t]{{0,3}}>[ \t]?){{{quote_count}}}[ \t]{{0,3}}"
                rf"{re.escape(character)}{{{minimum},}}[ \t]*$",
                content,
            )
            if closing:
                ranges.append((start, offset + len(line)))
                active = None
        offset += len(line)
    if active is not None:
        ranges.append((active[0], len(text)))
    return ranges


def excluded_code_ranges(
    text,
    ignored_ranges=None,
    autolink_ranges=None,
    work_report=None,
):
    raw_ranges = merge_ranges(ignored_ranges or [])
    autolinks = merge_ranges(autolink_ranges or [])
    fences = fenced_code_ranges(text, ignored_ranges=raw_ranges)
    runs = []
    spans = []
    active_raw_ranges = []
    active_autolink_ranges = []
    work = len(text)

    # Discover every backtick run outside fenced code. Runs inside apparent raw
    # HTML remain candidates: an earlier code-span opener takes precedence over
    # a later raw-HTML opener, so a backtick in that apparent tag may close it.
    index = 0
    fence_index = 0
    while index < len(text):
        work += 1
        while fence_index < len(fences) and fences[fence_index][1] <= index:
            work += 1
            fence_index += 1
        if (
            fence_index < len(fences)
            and fences[fence_index][0] <= index < fences[fence_index][1]
        ):
            index = fences[fence_index][1]
            continue
        if text[index] != "`":
            index += 1
            continue
        end = index + 1
        while end < len(text) and text[end] == "`":
            work += 1
            end += 1
        length = end - index
        slash_count = 0
        escape_cursor = index - 1
        while escape_cursor >= 0 and text[escape_cursor] == "\\":
            work += 1
            slash_count += 1
            escape_cursor -= 1
        runs.append((index, end, length, slash_count % 2 == 1))
        index = end

    # Map each run to the next run of the same length. A run that is escaped in
    # normal Markdown cannot open a span, but it can still close an already-open
    # code span because backslash escapes are literal inside code spans.
    next_by_start = {}
    next_by_length = {}
    for start, end, length, _ in reversed(runs):
        work += 1
        next_by_start[start] = next_by_length.get(length)
        next_by_length[length] = (start, end)

    # Resolve equal-precedence inline constructs from left to right. Whichever
    # valid construct opens first wins. If a code span crosses the start of an
    # apparent raw tag, that tag is discarded rather than activated midway.
    index = 0
    fence_index = 0
    raw_index = 0
    autolink_index = 0
    run_index = 0
    while index < len(text):
        work += 1
        while fence_index < len(fences) and fences[fence_index][1] <= index:
            work += 1
            fence_index += 1
        while raw_index < len(raw_ranges) and raw_ranges[raw_index][0] < index:
            work += 1
            raw_index += 1
        while (
            autolink_index < len(autolinks)
            and autolinks[autolink_index][0] < index
        ):
            work += 1
            autolink_index += 1
        while run_index < len(runs) and runs[run_index][0] < index:
            work += 1
            run_index += 1

        if (
            fence_index < len(fences)
            and fences[fence_index][0] == index
        ):
            index = fences[fence_index][1]
            continue
        if raw_index < len(raw_ranges) and raw_ranges[raw_index][0] == index:
            active_raw_ranges.append(raw_ranges[raw_index])
            index = raw_ranges[raw_index][1]
            continue
        if (
            autolink_index < len(autolinks)
            and autolinks[autolink_index][0] == index
        ):
            active_autolink_ranges.append(autolinks[autolink_index])
            index = autolinks[autolink_index][1]
            continue
        if run_index < len(runs) and runs[run_index][0] == index:
            start, end, _, escaped = runs[run_index]
            closing = next_by_start[start]
            if not escaped and closing is not None:
                spans.append((start, closing[1]))
                index = closing[1]
            else:
                index = end
            continue
        index += 1

    if work_report is not None:
        work_report["work"] = work
        work_report["active_raw_ranges"] = active_raw_ranges
        work_report["active_autolink_ranges"] = active_autolink_ranges
    return merge_ranges(fences + spans)


def previous_line_before(markdown, offset):
    line_end = offset
    if line_end >= 2 and markdown[line_end - 2 : line_end] == "\r\n":
        line_end -= 2
    elif line_end and markdown[line_end - 1] in "\r\n":
        line_end -= 1
    previous_start = line_end
    examined = offset - line_end
    while previous_start > 0:
        examined += 1
        if markdown[previous_start - 1] in "\r\n":
            break
        previous_start -= 1
    return markdown[previous_start:line_end], examined


def reference_definition_allowed(match, previous_line, continues_reference_block):
    label = match.group("label")
    normalized_label = label.replace("\r\n", "\n").replace("\r", "\n")
    if re.search(r"\n[ \t>]*\n", normalized_label):
        return False
    if match.start() == 0:
        return True
    if continues_reference_block:
        return True
    container = match.group("container")
    if not container.strip():
        return not previous_line.strip()
    if re.search(r"(?:[-+*]|\d{1,9}[.)])[ \t]+$", container):
        return True
    remaining = previous_line
    for _ in range(container.count(">")):
        quote = re.match(r"^[ \t]{0,3}>[ \t]?", remaining)
        if quote is None:
            return True
        remaining = remaining[quote.end() :]
    return not remaining.strip()


def line_end_after(text, offset):
    cursor = offset
    while cursor < len(text) and text[cursor] not in "\r\n":
        cursor += 1
    examined = cursor - offset + (1 if cursor < len(text) else 0)
    return cursor, examined


def scan_markdown(markdown, source="stdin", max_issues=50):
    if len(markdown) > MAX_INPUT_CHARACTERS:
        raise ValueError(
            f"Markdown input exceeds the {MAX_INPUT_CHARACTERS}-character scan limit."
        )
    starts = line_starts(markdown)
    detected = []
    processing_truncated = False
    html_scan = html_link_destinations(markdown)
    commonmark_autolink_matches = [
        match
        for match in COMMONMARK_AUTOLINK_DESTINATION.finditer(markdown)
        if not is_backslash_escaped(markdown, match.start())
    ]
    code_scan = {}
    code_ranges = excluded_code_ranges(
        markdown,
        ignored_ranges=html_scan["tag_ranges"],
        autolink_ranges=[
            (match.start(), match.end())
            for match in commonmark_autolink_matches
        ],
        work_report=code_scan,
    )
    code_starts = [start for start, _ in code_ranges]
    active_html_ranges = code_scan["active_raw_ranges"]
    active_autolink_ranges = code_scan["active_autolink_ranges"]
    markdown_excluded_ranges = merge_ranges(
        code_ranges + active_html_ranges + active_autolink_ranges
    )
    markdown_excluded_starts = [
        start for start, _ in markdown_excluded_ranges
    ]
    inline_link_ranges = []

    def append_finding(rule_id, severity, message, offset):
        nonlocal processing_truncated
        if len(detected) >= MAX_PROCESSED_ISSUES:
            processing_truncated = True
            return False
        detected.append(issue(rule_id, severity, message, starts, offset))
        return True

    link_scan = markdown_link_destinations(
        markdown,
        code_ranges=markdown_excluded_ranges,
    )
    if link_scan["truncated"]:
        processing_truncated = True
        append_finding(
            "MARKDOWN_LINK_SCAN_LIMIT",
            "error",
            "Markdown link scan work limit reached; results are incomplete.",
            link_scan["truncated_at"],
        )
    for link_start, link_end, destination in link_scan["candidates"]:
        if range_containing(
            markdown_excluded_ranges,
            markdown_excluded_starts,
            link_start,
        ):
            continue
        inline_link_ranges.append((link_start, link_end))
        if not is_javascript_destination(destination):
            continue
        if not append_finding(
            "JAVASCRIPT_LINK",
            "error",
            "JavaScript URL detected in a Markdown link or image destination.",
            link_start,
        ):
            break
    inline_link_ranges = merge_ranges(inline_link_ranges)
    inline_link_starts = [start for start, _ in inline_link_ranges]
    reported_html_tags = set()
    for tag_start, tag_end, destination in html_scan["candidates"]:
        if range_containing(code_ranges, code_starts, tag_start):
            continue
        tag_key = (tag_start, tag_end)
        if tag_key in reported_html_tags:
            continue
        if not is_javascript_destination(destination, markdown_escapes=False):
            continue
        if not append_finding(
            "JAVASCRIPT_LINK",
            "error",
            "JavaScript URL detected in an HTML link.",
            tag_start,
        ):
            break
        reported_html_tags.add(tag_key)
    reference_block_open = False
    reference_gap_cursor = 0
    reference_context_work = 0
    reference_ranges = []
    for match in REFERENCE_LINK_DESTINATION.finditer(markdown):
        continues_reference_block = False
        if reference_block_open:
            cursor = reference_gap_cursor
            while cursor < match.start():
                reference_context_work += 1
                if not markdown[cursor].isspace():
                    reference_block_open = False
                    break
                cursor += 1
            reference_gap_cursor = match.start()
            continues_reference_block = reference_block_open
        if range_containing(
            markdown_excluded_ranges,
            markdown_excluded_starts,
            match.start(),
        ):
            continue
        previous_line = ""
        if match.start() != 0 and not continues_reference_block:
            previous_line, examined = previous_line_before(markdown, match.start())
            reference_context_work += examined
        if not reference_definition_allowed(
            match,
            previous_line,
            continues_reference_block,
        ):
            continue
        reference_gap_cursor, examined = line_end_after(markdown, match.end())
        reference_context_work += examined
        reference_block_open = True
        reference_ranges.append((match.start(), match.end()))
        if not is_javascript_destination(captured_destination(match)):
            continue
        if not append_finding(
            "JAVASCRIPT_LINK",
            "error",
            "JavaScript URL detected in a Markdown reference definition.",
            match.start(),
        ):
            break
    reference_ranges = merge_ranges(reference_ranges)
    reference_starts = [start for start, _ in reference_ranges]
    non_autolink_excluded_ranges = merge_ranges(
        code_ranges + active_html_ranges
    )
    non_autolink_excluded_starts = [
        start for start, _ in non_autolink_excluded_ranges
    ]
    for match in AUTOLINK_DESTINATION.finditer(markdown):
        if range_containing(
            non_autolink_excluded_ranges,
            non_autolink_excluded_starts,
            match.start(),
        ):
            continue
        if is_backslash_escaped(markdown, match.start()):
            continue
        if range_containing(inline_link_ranges, inline_link_starts, match.start()):
            continue
        if range_containing(reference_ranges, reference_starts, match.start()):
            continue
        if not is_javascript_destination(captured_destination(match)):
            continue
        if not append_finding(
            "JAVASCRIPT_LINK",
            "error",
            "JavaScript URL detected in a Markdown autolink.",
            match.start(),
        ):
            break
    for match in REDACTION_CANDIDATE_PATTERN.finditer(markdown):
        if not append_finding(
            "SECRET_LIKE_TOKEN",
            "warning",
            "Potential secret-like token detected; review before sharing.",
            match.start(),
        ):
            break
    for offset, character in enumerate(markdown):
        if unicodedata.category(character) == "Cc" and character not in "\n\r\t":
            if not append_finding(
                "CONTROL_CHARACTER",
                "warning",
                f"Unexpected control character U+{ord(character):04X} detected.",
                offset,
            ):
                break
        hidden_label = HIDDEN_FORMAT_CHARACTERS.get(character)
        if hidden_label:
            if not append_finding(
                "HIDDEN_FORMAT_CHARACTER",
                "warning",
                f"Unexpected hidden format character U+{ord(character):04X} ({hidden_label}) detected.",
                offset,
            ):
                break
    detected.sort(key=lambda item: (item["offset"], item["id"]))
    emitted = detected[:max_issues]
    score = max(
        0.0,
        1.0 - sum(PENALTIES[item["severity"]] for item in detected),
    )
    has_error = any(item["severity"] == "error" for item in detected)
    return {
        "schema_version": 1,
        "tool": "document-artifacts.markdown-security",
        "operation": "scan",
        "source": source,
        "status": "findings" if detected else "passed",
        "ok": not has_error,
        "score": round(score, 2),
        "scanned_characters": len(markdown),
        "detected_issue_count": len(detected),
        "emitted_issue_count": len(emitted),
        "truncated": len(detected) > len(emitted) or processing_truncated,
        "processing_truncated": processing_truncated,
        "detected_issue_count_is_lower_bound": processing_truncated,
        "link_scan_work": link_scan["work"],
        "link_scan_budget": link_scan["budget"],
        "html_scan_work": html_scan["work"],
        "code_scan_work": code_scan["work"],
        "reference_context_work": reference_context_work,
        "issues": emitted,
        "redaction": "Matched secret-like values are never included in the report.",
    }


def read_markdown(source):
    if source == "-":
        text = sys.stdin.read(MAX_INPUT_CHARACTERS + 1)
        if len(text) > MAX_INPUT_CHARACTERS:
            raise ValueError(
                f"Markdown input exceeds the {MAX_INPUT_CHARACTERS}-character scan limit."
            )
        return text, "stdin"
    path = Path(source)
    if not path.is_file():
        raise ValueError(f"Markdown input is not a file: {path}")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"Markdown input exceeds the {MAX_INPUT_BYTES}-byte scan limit.")
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    if len(text) > MAX_INPUT_CHARACTERS:
        raise ValueError(
            f"Markdown input exceeds the {MAX_INPUT_CHARACTERS}-character scan limit."
        )
    return text, str(path)


def print_human(report):
    print(f"# Markdown Security Scan\n\n- Status: {report['status']}")
    print(f"- Score: {report['score']:.2f}")
    print(f"- Issues: {report['detected_issue_count']}")
    for finding in report["issues"]:
        print(
            f"- {finding['severity'].upper()} {finding['id']} "
            f"at {finding['line']}:{finding['column']}: {finding['message']}"
        )


def build_parser():
    parser = argparse.ArgumentParser(description="Portable Markdown evidence tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan = subparsers.add_parser("scan", help="Scan Markdown without emitting matched secret values.")
    scan.add_argument("--file", required=True, help="UTF-8 Markdown path, or - for stdin.")
    scan.add_argument("--max-issues", type=int, default=50)
    scan.add_argument("--strict", action="store_true", help="Return nonzero when any issue is detected.")
    scan.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.max_issues < 1:
        print("--max-issues must be at least 1", file=sys.stderr)
        return 2
    try:
        markdown, source = read_markdown(args.file)
        report = scan_markdown(markdown, source=source, max_issues=args.max_issues)
    except (OSError, UnicodeError, ValueError) as exc:
        report = {
            "schema_version": 1,
            "tool": "document-artifacts.markdown-security",
            "operation": "scan",
            "status": "failed",
            "ok": False,
            "error": str(exc),
        }
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"Markdown security scan failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    return 1 if args.strict and report["detected_issue_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
