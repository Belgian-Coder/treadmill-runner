"""Small changed-file proof hygiene checks."""

from __future__ import annotations

import dataclasses
import fnmatch
import re
import subprocess
from pathlib import Path
from typing import Any

from repo_support import repo_policy


UNFINISHED_WORDS = ("TO" + "DO", "FIX" + "ME", "X" * 3, "HA" + "CK")
UNFINISHED_PHRASES = ("not " + "implemented", "coming " + "soon", "st" + "ub")
UNFINISHED_RE = (
    r"\b(" + "|".join(UNFINISHED_WORDS) + r")\b|"
    + "|".join(re.escape(item) for item in UNFINISHED_PHRASES)
)

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("unfinished_marker", re.compile(UNFINISHED_RE, re.I)),
    (
        "python_silent_failure",
        re.compile(
            r"except\b[^\n:]*:[ \t]*(?:\r?\n[ \t]*)?(pass\b|return[ \t]+None(?![ \t]*,))",
            re.I,
        ),
    ),
    ("js_silent_failure", re.compile(r"catch\s*\([^)]*\)\s*{\s*(?:/\*.*?\*/\s*)?}", re.I | re.S)),
    ("unsafe_eval", re.compile(r"\b(eval|exec)\s*\(", re.I)),
    (
        "secret_literal",
        re.compile(
            r"(?i)['\"]?\b(api[_-]?key|secret|token|password)\b['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{8,}['\"]?"
        ),
    ),
)

DEFAULT_EXCLUDES = (
    ".git/*",
    ".venv/*",
    "venv/*",
    "node_modules/*",
    "dist/*",
    "build/*",
    "__pycache__/*",
    "*.lock",
    ".agents/registry.json",
    ".agents/routing.md",
    ".claude/CLAUDE.md",
    ".claude/skills/*",
    ".continue/rules/repository-instructions.md",
    ".github/copilot-instructions.md",
    "GEMINI.md",
    "automations/registry.json",
    "automations/routing.md",
    "automations/navigation/artifacts/maps/*",
)
PROOF_FILE_RE = re.compile(r"(donecheck|proof|verification|test[-_]?results)", re.I)
PROOF_FILE_NAMES = {"report.md", "execution-log.md"}
FINAL_ARTIFACT_NAMES = {
    "execution-log.md",
    "pr-description.md",
    "pr_description.md",
    "pull-request.md",
    "pull_request.md",
    "report.md",
    "summary.md",
}
PROOF_EXTENSIONS = {".md", ".markdown", ".txt"}
THIN_PROOF_RE = re.compile(r"\b(all\s+)?tests?\s+pass(?:ed|es)?\b", re.I)
UNCHECKED_CLOSEOUT_RE = re.compile(r"^\s*[-*]\s+\[\s\]\s+\S")
PLACEHOLDER_WORDS = ("run-id", "title", "to" + "do", "tbd", r"fill[^\]]*")
ANGLE_PLACEHOLDER_WORDS = ("fill[^>]*", "placeholder", "to" + "do", "tbd")
PLACEHOLDER_RESIDUE_RE = re.compile(
    r"\[(?:" + "|".join(PLACEHOLDER_WORDS) + r")\]|<\s*(?:"
    + "|".join(ANGLE_PLACEHOLDER_WORDS)
    + r")[^>]*>|\breplace me\b",
    re.I,
)
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
MAX_TEXT_BYTES = 1_000_000


@dataclasses.dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    line: int
    text: str

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def excluded(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in DEFAULT_EXCLUDES)


def strip_markdown_fences(text: str) -> str:
    lines: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            lines.append("")
        elif fenced:
            lines.append("")
        else:
            lines.append(line)
    return "\n".join(lines)


def read_text(path: Path) -> tuple[str | None, str]:
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return (None, "file is larger than proof hygiene text limit")
        return (path.read_text(encoding="utf-8"), "")
    except (OSError, UnicodeDecodeError) as exc:
        return (None, f"could not read text: {exc.__class__.__name__}")


def proof_like(path: Path) -> bool:
    if path.suffix.lower() not in PROOF_EXTENSIONS:
        return False
    if path.name.lower() in PROOF_FILE_NAMES:
        return True
    return bool(PROOF_FILE_RE.search(path.name))


def closeout_like(path: str) -> bool:
    value = path.replace("\\", "/").lower()
    source = Path(value)
    if source.suffix not in PROOF_EXTENSIONS:
        return False
    if "/templates/" in value or value.startswith("templates/"):
        return False
    if source.name in FINAL_ARTIFACT_NAMES:
        return True
    return proof_like(source)


def run_git_lines(root: Path, args: list[str]) -> tuple[int, list[str]]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError:
        return 1, []
    return proc.returncode, (proc.stdout or "").splitlines()


def parse_added_line_map(diff_lines: list[str]) -> dict[str, set[int]]:
    added: dict[str, set[int]] = {}
    current_path = ""
    current_line: int | None = None
    for line in diff_lines:
        if line.startswith("+++ "):
            raw_path = line[4:].strip()
            current_path = ""
            if raw_path.startswith("b/"):
                current_path = raw_path[2:].replace("\\", "/")
            elif raw_path != "/dev/null":
                current_path = raw_path.replace("\\", "/")
            current_line = None
            continue
        match = HUNK_RE.match(line)
        if match:
            current_line = int(match.group(1))
            continue
        if current_line is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            if current_path:
                added.setdefault(current_path, set()).add(current_line)
            current_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif line.startswith(" "):
            current_line += 1
    return added


def all_line_numbers(text: str) -> set[int]:
    return set(range(1, len(text.splitlines()) + 1))


def git_available(root: Path) -> bool:
    status, _lines = run_git_lines(root, ["rev-parse", "--is-inside-work-tree"])
    return status == 0


def untracked_paths(root: Path, paths: list[str]) -> set[str]:
    if not paths:
        return set()
    status, lines = run_git_lines(root, ["ls-files", "--others", "--exclude-standard", "--", *paths])
    if status != 0:
        return set()
    return {line.replace("\\", "/") for line in lines}


def diff_added_line_map(root: Path, paths: list[str], *, staged: bool = False) -> dict[str, set[int]]:
    if not paths:
        return {}
    args = ["diff"]
    if staged:
        args.append("--cached")
    args.extend(["--unified=0", "--no-ext-diff", "--", *paths])
    status, lines = run_git_lines(root, args)
    if status != 0:
        return {}
    return parse_added_line_map(lines)


def added_line_numbers(
    path: str,
    text: str,
    *,
    has_git: bool,
    untracked: set[str],
    diff_maps: tuple[dict[str, set[int]], ...],
) -> set[int]:
    if path in untracked:
        return all_line_numbers(text)

    lines: set[int] = set()
    for diff_map in diff_maps:
        lines.update(diff_map.get(path, set()))

    if lines or has_git:
        return lines
    return all_line_numbers(text)


def line_range_for_match(text: str, start: int, end: int) -> range:
    start_line = text.count("\n", 0, start) + 1
    end_line = text.count("\n", 0, max(start, end - 1)) + 1
    return range(start_line, end_line + 1)


def overlaps_added_line(text: str, start: int, end: int, added_lines: set[int]) -> bool:
    if not added_lines:
        return False
    return any(line in added_lines for line in line_range_for_match(text, start, end))


def scan_text(path: str, text: str, added_lines: set[int]) -> list[Finding]:
    if Path(path).suffix.lower() in {".md", ".markdown"}:
        text = strip_markdown_fences(text)
    findings: list[Finding] = []
    snippet_chars = repo_policy.int_value(
        repo_policy.project_root(Path(path)), "limits.output.finding_snippet_chars"
    )
    lines = text.splitlines()
    for rule, pattern in RULES:
        for match in pattern.finditer(text):
            if not overlaps_added_line(text, match.start(), match.end(), added_lines):
                continue
            line = text.count("\n", 0, match.start()) + 1
            snippet = lines[line - 1].strip() if line <= len(lines) else match.group(0).strip()
            findings.append(Finding(rule, path, line, snippet[:snippet_chars]))
    return findings


def thin_proof_findings(path: str, text: str, added_lines: set[int]) -> list[Finding]:
    source = Path(path)
    if not proof_like(source):
        return []
    has_exit = re.search(r"\bexit code\b", text, re.I)
    has_output = re.search(r"\boutput\b|```", text, re.I)
    has_time = re.search(r"\bgenerated\b|\btimestamp\b|\d{4}-\d{2}-\d{2}", text, re.I)
    match = THIN_PROOF_RE.search(text)
    if match and overlaps_added_line(text, match.start(), match.end(), added_lines) and not (has_exit and has_output and has_time):
        line = text.count("\n", 0, match.start()) + 1
        return [
            Finding(
                "thin_proof_file",
                path,
                line,
                "proof says tests passed without command output, exit code, and timestamp",
            )
        ]
    return []


def closeout_residue_findings(path: str, text: str, added_lines: set[int]) -> list[Finding]:
    if not closeout_like(path):
        return []
    findings: list[Finding] = []
    snippet_chars = repo_policy.int_value(
        repo_policy.project_root(Path(path)), "limits.output.finding_snippet_chars"
    )
    lines = text.splitlines()
    for index, line in enumerate(lines, start=1):
        if index in added_lines and UNCHECKED_CLOSEOUT_RE.search(line):
            findings.append(
                Finding(
                    "unchecked_closeout_item",
                    path,
                    index,
                    line.strip()[:snippet_chars],
                )
            )
    for match in PLACEHOLDER_RESIDUE_RE.finditer(text):
        if not overlaps_added_line(text, match.start(), match.end(), added_lines):
            continue
        line = text.count("\n", 0, match.start()) + 1
        snippet = lines[line - 1].strip() if line <= len(lines) else match.group(0).strip()
        findings.append(
            Finding(
                "closeout_placeholder",
                path,
                line,
                snippet[:snippet_chars],
            )
        )
    return findings


def scan_paths(root: Path, paths: list[str]) -> tuple[list[Finding], list[dict[str, str]]]:
    findings: list[Finding] = []
    skipped: list[dict[str, str]] = []
    normalized = [path.replace("\\", "/") for path in paths]
    has_git = git_available(root)
    untracked = untracked_paths(root, normalized) if has_git else set()
    diff_maps = (
        diff_added_line_map(root, normalized, staged=False) if has_git else {},
        diff_added_line_map(root, normalized, staged=True) if has_git else {},
    )
    for rel_path in paths:
        path = rel_path.replace("\\", "/")
        if excluded(path):
            continue
        source = root / path
        if not source.is_file():
            continue
        text, skip_reason = read_text(source)
        if text is None:
            skipped.append({"path": path, "reason": skip_reason})
            continue
        added_lines = added_line_numbers(path, text, has_git=has_git, untracked=untracked, diff_maps=diff_maps)
        if not added_lines:
            continue
        findings.extend(scan_text(path, text, added_lines))
        findings.extend(thin_proof_findings(path, text, added_lines))
        findings.extend(closeout_residue_findings(path, text, added_lines))
    return findings, skipped


def proof_hygiene_report(root: Path, paths: list[str]) -> dict[str, Any]:
    findings, skipped = scan_paths(root, paths)
    return {
        "schema_version": 1,
        "tool": "skill-manager.proof-hygiene",
        "ok": not findings,
        "status": "passed" if not findings else "failed",
        "summary": {"files_checked": len(paths), "finding_count": len(findings), "skipped_count": len(skipped)},
        "findings": [finding.as_dict() for finding in findings],
        "skipped": skipped,
    }


def render_proof_hygiene(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        f"status={report.get('status', '')}",
        (
            f"files={summary.get('files_checked', 0)} "
            f"findings={summary.get('finding_count', 0)} "
            f"skipped={summary.get('skipped_count', 0)}"
        ),
    ]
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    for item in findings[:30]:
        if isinstance(item, dict):
            lines.append(f"- {item.get('rule')} {item.get('path')}:{item.get('line')} {item.get('text')}")
    return "\n".join(lines)
