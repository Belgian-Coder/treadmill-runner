#!/usr/bin/env python3
"""Self-tests for the Mermaid diagrams skill scripts."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

import materialize_diagrams
import setup_vscode_mermaid_preview
import validate_mermaid


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_mmdc_render_flags(command: list[str]) -> None:
    expected = validate_mermaid.MMDC_RENDER_FLAGS
    assert_true(command[-len(expected) :] == expected, "expected dark transparent SVG render flags")


def write_azure_block(path: Path, body: str) -> None:
    indented = "\n".join(f"    {line}" if line else "" for line in body.strip("\n").splitlines())
    path.write_text(f"::: mermaid\n{indented}\n:::\n", encoding="utf-8", newline="\n")


def validate_draft_paths(paths: list[Path], **kwargs: object) -> dict[str, object]:
    return validate_mermaid.validate_paths(paths, allow_markdown_blocks=True, **kwargs)


def test_azure_extraction_and_validation(temp_root: Path) -> None:
    path = temp_root / "azure.md"
    path.write_text(
        "Intro\n\n::: mermaid\n    graph TD;\n      A[\"Start\"] --> B[\"Done\"];\n:::\n",
        encoding="utf-8",
        newline="\n",
    )
    report = validate_draft_paths([path])
    assert_true(report["block_count"] == 1, "expected one Azure Mermaid block")
    assert_true(report["valid"], f"expected Azure block to validate: {report['errors']}")


def test_compact_azure_wrapper_accepted(temp_root: Path) -> None:
    path = temp_root / "compact-wrapper.md"
    path.write_text(
        ":::mermaid\n    graph TD;\n      A[\"Start\"] --> B[\"Done\"];\n:::\n",
        encoding="utf-8",
        newline="\n",
    )
    report = validate_draft_paths([path])
    assert_true(report["block_count"] == 1, "compact wrapper should still be extracted")
    assert_true(report["valid"], f"compact Azure wrapper should validate: {report['errors']}")
    assert_true(
        any("Preferred Azure Mermaid wrapper style" in item["message"] for item in report["warnings"]),
        "expected wrapper style warning",
    )


def test_supported_azure_diagram_types(temp_root: Path) -> None:
    fixtures = {
        "graph": 'graph TD;\n  A["Start"] --> B["Done"];',
        "sequence": "sequenceDiagram\n  participant User\n  participant Agent\n  User->>Agent: Request",
        "gantt": "gantt\n  title Plan\n  dateFormat YYYY-MM-DD\n  section Work\n  Build :a1, 2026-01-01, 2d",
        "class": "classDiagram\n  class Skill\n  Skill <|-- ManagerSkill",
        "state": "stateDiagram\n  [*] --> Draft\n  Draft --> Done",
        "state-v2": "stateDiagram-v2\n  [*] --> Draft\n  Draft --> Done",
        "journey": "journey\n  title Review\n  section Intake\n    Inspect: 5: Agent",
        "pie": 'pie title Split\n  "Build" : 60\n  "Test" : 40',
        "requirement": "requirementDiagram\n  requirement local_validation {\n    id: \"REQ-001\"\n    text: Validate locally\n    risk: medium\n    verifymethod: test\n  }",
        "gitgraph": 'gitGraph\n  commit id: "Initial"\n  branch feature\n  checkout feature\n  commit id: "Change"',
        "erd": "erDiagram\n  CUSTOMER ||--o{ ORDER : places\n  ORDER {\n    string order_id\n  }",
        "timeline": "timeline\n  title Release\n  2026-01 : Start\n  2026-02 : Finish",
    }
    for name, body in fixtures.items():
        path = temp_root / f"{name}.md"
        write_azure_block(path, body)
        report = validate_draft_paths([path])
        assert_true(report["valid"], f"{name} should validate: {report['errors']}")


def test_requirement_diagram_rejects_unquoted_hyphenated_id(temp_root: Path) -> None:
    path = temp_root / "requirement-id.md"
    write_azure_block(
        path,
        "requirementDiagram\n"
        "  requirement local_validation {\n"
        "    id: REQ-001\n"
        "    text: Validate locally\n"
        "    risk: medium\n"
        "    verifymethod: test\n"
        "  }",
    )
    report = validate_draft_paths([path])
    assert_true(not report["valid"], "unquoted hyphenated requirement ids should fail static validation")
    assert_true(
        any("requirement id" in item["message"].lower() for item in report["errors"]),
        "expected requirement id error",
    )


def test_mermaid_source_file_validation(temp_root: Path) -> None:
    path = temp_root / "diagram.mmd"
    path.write_text('graph TD;\n  A["Start"] --> B["Done"];\n', encoding="utf-8", newline="\n")
    report = validate_draft_paths([path])
    assert_true(report["block_count"] == 1, "expected one source Mermaid block")
    assert_true(report["blocks"][0]["wrapper"] == "source", "expected source wrapper")
    assert_true(report["valid"], f"expected .mmd source to validate: {report['errors']}")


def test_markdown_mermaid_blocks_require_materialization(temp_root: Path) -> None:
    path = temp_root / "inline.md"
    write_azure_block(path, 'graph TD;\n  A["Start"] --> B["Done"];')
    report = validate_mermaid.validate_paths([path])
    assert_true(not report["valid"], "default validation should reject durable Markdown Mermaid blocks")
    assert_true(any("materialize_diagrams.py" in item["message"] for item in report["errors"]), "expected materialization guidance")


def test_linked_svg_artifacts_require_dark_transparent_intrinsic_canvas(temp_root: Path) -> None:
    root = temp_root / "bad-linked-svg"
    diagrams = root / "diagrams"
    diagrams.mkdir(parents=True)
    path = root / "guide.md"
    path.write_text(
        "[![Flow diagram](diagrams/flow.svg)](diagrams/flow.svg)\n\n"
        "Source: [Mermaid](diagrams/flow.mmd)\n",
        encoding="utf-8",
        newline="\n",
    )
    (diagrams / "flow.mmd").write_text('graph TD;\n  A["Start"] --> B["Done"];\n', encoding="utf-8", newline="\n")
    (diagrams / "flow.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="200" viewBox="0 0 800 200">'
        '<rect width="800" height="200" fill="#ffffff"/></svg>',
        encoding="utf-8",
        newline="\n",
    )

    report = validate_mermaid.validate_paths([path])

    assert_true(not report["valid"], "stale linked SVG should fail validation")
    messages = "\n".join(item["message"] for item in report["errors"])
    assert_true("intrinsic numeric width" in messages, "expected intrinsic width error")
    assert_true("transparent background" in messages, "expected transparent background error")
    assert_true("white background" in messages, "expected white background error")


def test_linked_svg_artifacts_accept_normalized_dark_canvas(temp_root: Path) -> None:
    root = temp_root / "good-linked-svg"
    diagrams = root / "diagrams"
    diagrams.mkdir(parents=True)
    path = root / "guide.md"
    path.write_text(
        "[![Flow diagram](diagrams/flow.svg)](diagrams/flow.svg)\n\n"
        "Source: [Mermaid](diagrams/flow.mmd)\n",
        encoding="utf-8",
        newline="\n",
    )
    (diagrams / "flow.mmd").write_text('graph TD;\n  A["Start"] --> B["Done"];\n', encoding="utf-8", newline="\n")
    (diagrams / "flow.svg").write_text(
        '<svg id="my-svg" width="100" height="148" viewBox="0 -24 100 148" '
        'style="max-width: 100px; background-color: transparent;" '
        'preserveAspectRatio="xMidYMid meet" data-mermaid-vertical-padding="24">'
        '<style>#my-svg{fill:#ccc;}</style><g></g></svg>',
        encoding="utf-8",
        newline="\n",
    )

    report = validate_mermaid.validate_paths([path])

    assert_true(report["valid"], f"normalized linked SVG should pass: {report['errors']}")
    assert_true(report["artifact_count"] == 1, "expected linked artifact evidence")


def test_unlinked_materialized_mermaid_artifacts_fail_validation(temp_root: Path) -> None:
    root = temp_root / "orphaned-artifact"
    diagrams = root / "diagrams"
    diagrams.mkdir(parents=True)
    (diagrams / "orphan.mmd").write_text('graph TD;\n  A["Start"] --> B["Done"];\n', encoding="utf-8", newline="\n")
    (diagrams / "orphan.svg").write_text(
        '<svg id="my-svg" width="100" height="148" viewBox="0 -24 100 148" '
        'style="max-width: 100px; background-color: transparent;" '
        'preserveAspectRatio="xMidYMid meet" data-mermaid-vertical-padding="24">'
        '<style>#my-svg{fill:#ccc;}</style><g></g></svg>',
        encoding="utf-8",
        newline="\n",
    )

    report = validate_mermaid.validate_paths([root])

    assert_true(not report["valid"], "orphaned rendered Mermaid assets should fail")
    messages = "\n".join(item["message"] for item in report["errors"])
    assert_true("not linked from Markdown" in messages, "expected orphaned asset error")


def test_mermaid_template_assets_allow_unlinked_blocks(temp_root: Path) -> None:
    templates = temp_root / "assets" / "mermaid-templates"
    templates.mkdir(parents=True)
    (templates / "graph.md").write_text("::: mermaid\n    graph TD;\n      A --> B;\n:::\n", encoding="utf-8", newline="\n")

    report = validate_mermaid.validate_paths([templates])

    assert_true(report["valid"], f"template Mermaid blocks should be allowed: {report['errors']}")


def test_selection_guide_recommends_erd() -> None:
    skill_root = Path(__file__).resolve().parents[1]
    guide = (skill_root / "docs" / "diagram-selection-guide.md").read_text(encoding="utf-8")
    assert_true("tables" in guide.lower() and "erDiagram" in guide, "selection guide should prefer ERD for tables")


def test_fenced_mermaid_rejected(temp_root: Path) -> None:
    path = temp_root / "fenced.md"
    path.write_text(
        "```mermaid\ngraph LR;\n  A[\"One\"] --> B[\"Two\"];\n```\n",
        encoding="utf-8",
        newline="\n",
    )
    blocks, _files = validate_mermaid.extract_blocks([path])
    assert_true(len(blocks) == 1 and blocks[0].wrapper == "fenced", "expected fenced Mermaid block")
    report = validate_draft_paths([path])
    assert_true(not report["valid"], "fenced Mermaid blocks should be out of scope")
    assert_true(any("Azure DevOps skill scope" in item["message"] for item in report["errors"]), "expected fenced wrapper error")


def test_flowchart_rejected(temp_root: Path) -> None:
    path = temp_root / "flowchart.md"
    path.write_text(
        "::: mermaid\n    flowchart TD;\n      A --> B;\n:::\n",
        encoding="utf-8",
        newline="\n",
    )
    report = validate_mermaid.validate_paths([path])
    assert_true(not report["valid"], "flowchart syntax should fail portable validation")
    assert_true(any("flowchart" in item["message"] for item in report["errors"]), "expected flowchart error")


def test_azure_incompatible_syntax_rejected(temp_root: Path) -> None:
    cases = {
        "longarrow": 'graph TD;\n  A["Start"] ----> B["Done"];',
        "fontawesome": 'graph TD;\n  A["fa:fa-user"] --> B["Done"];',
        "init": '%%{init: {"theme": "base"}}%%\ngraph TD;\n  A["Start"] --> B["Done"];',
        "html": 'graph TD;\n  A["<b>Start</b>"] --> B["Done"];',
        "style": 'graph TD;\n  A["Start"] --> B["Done"];\n  classDef hot fill:#fff;',
    }
    for name, body in cases.items():
        path = temp_root / f"{name}.md"
        write_azure_block(path, body)
        report = validate_draft_paths([path])
        assert_true(not report["valid"], f"{name} should fail validation")


def test_subgraph_grouping_allowed(temp_root: Path) -> None:
    path = temp_root / "subgraph-ok.md"
    write_azure_block(
        path,
        'graph TD;\n  subgraph groupA["Group A"]\n    A["Start"] --> B["Inside"];\n  end\n  B --> C["Done"];',
    )
    report = validate_draft_paths([path])
    assert_true(report["valid"], f"node-to-node subgraph links should validate: {report['errors']}")


def test_subgraph_id_edges_rejected(temp_root: Path) -> None:
    path = temp_root / "subgraph-edge.md"
    write_azure_block(
        path,
        'graph TD;\n  subgraph groupA["Group A"]\n    A["Start"];\n  end\n  groupA --> B["Done"];',
    )
    report = validate_draft_paths([path])
    assert_true(not report["valid"], "links to subgraph ids should fail")
    assert_true(any("subgraph ids" in item["message"] for item in report["errors"]), "expected subgraph edge error")


def test_graph_label_hygiene_warnings(temp_root: Path) -> None:
    path = temp_root / "labels.md"
    write_azure_block(
        path,
        'graph TD;\n  A[Path /] --> B[Array [0]];\n  C["end"] --> D["This label is intentionally long enough to trigger the graph label warning"];',
    )
    report = validate_draft_paths([path])
    assert_true(report["valid"], f"label hygiene findings should be warnings: {report['errors']}")
    messages = "\n".join(item["message"] for item in report["warnings"])
    assert_true("ends with `/`" in messages, "expected trailing slash warning")
    assert_true("special characters" in messages, "expected special character warning")
    assert_true("Lowercase `end`" in messages, "expected lowercase end warning")
    assert_true("Graph label is long" in messages, "expected long label warning")


def test_require_render_missing(temp_root: Path) -> None:
    path = temp_root / "render.md"
    path.write_text(
        "::: mermaid\n    graph TD;\n      A[\"Start\"] --> B[\"Done\"];\n:::\n",
        encoding="utf-8",
        newline="\n",
    )
    report = validate_draft_paths(
        [path],
        render=True,
        require_render=True,
        mmdc="definitely-missing-" + "mmdc",
    )
    assert_true(not report["valid"], "required render should fail when mmdc is missing")
    assert_true("not found" in report["errors"][0]["message"], "expected clear missing renderer message")
    assert_true(
        not report["render"]["auto_install_requested"],
        "custom render commands should not trigger automatic mmdc setup",
    )


def test_render_success_with_stub(temp_root: Path) -> None:
    path = temp_root / "render-ok.md"
    path.write_text(
        "::: mermaid\n    graph TD;\n      A[\"Start\"] --> B[\"Done\"];\n:::\n",
        encoding="utf-8",
        newline="\n",
    )

    original_which = shutil.which
    original_run = subprocess.run

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_which(_command: str) -> str:
        return str(temp_root / "fake-mmdc")

    def fake_run(command: list[str], **_kwargs: object) -> Completed:
        assert_mmdc_render_flags(command)
        output = Path(command[command.index("-o") + 1])
        output.write_text("<svg></svg>", encoding="utf-8")
        return Completed()

    try:
        shutil.which = fake_which
        subprocess.run = fake_run
        report = validate_draft_paths([path], render=True, require_render=True)
    finally:
        shutil.which = original_which
        subprocess.run = original_run

    assert_true(report["valid"], f"fake render should pass: {report['errors']}")


def test_auto_install_success_with_compatible_node(temp_root: Path) -> None:
    path = temp_root / "auto-install.md"
    path.write_text(
        "::: mermaid\n    graph TD;\n      A[\"Start\"] --> B[\"Done\"];\n:::\n",
        encoding="utf-8",
        newline="\n",
    )

    original_which = shutil.which
    original_run = subprocess.run
    installed = {"mmdc": False}

    class Completed:
        def __init__(self, returncode: int = 0, stdout: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def fake_which(command: str) -> str | None:
        if command == "mmdc":
            return str(temp_root / "fake-mmdc") if installed["mmdc"] else None
        if command in {"node", "npm"}:
            return str(temp_root / command)
        return None

    def fake_run(command: list[str], **_kwargs: object) -> Completed:
        if "--version" in command:
            return Completed(stdout="v20.11.1")
        if "install" in command and "@mermaid-js/mermaid-cli" in command:
            installed["mmdc"] = True
            return Completed()
        if "-o" in command:
            assert_mmdc_render_flags(command)
            output = Path(command[command.index("-o") + 1])
            output.write_text("<svg></svg>", encoding="utf-8")
            return Completed()
        return Completed(returncode=1, stdout="unexpected command")

    try:
        shutil.which = fake_which
        subprocess.run = fake_run
        report = validate_draft_paths(
            [path],
            render=True,
            require_render=True,
        )
    finally:
        shutil.which = original_which
        subprocess.run = original_run

    assert_true(report["valid"], f"auto install render should pass: {report['errors']}")
    assert_true(report["render"]["install_attempted"], "expected npm install attempt")
    assert_true(report["render"]["install_performed"], "expected successful install marker")


def test_auto_install_rejects_incompatible_node(temp_root: Path) -> None:
    path = temp_root / "bad-node.md"
    path.write_text(
        "::: mermaid\n    graph TD;\n      A[\"Start\"] --> B[\"Done\"];\n:::\n",
        encoding="utf-8",
        newline="\n",
    )

    original_which = shutil.which
    original_run = subprocess.run

    class Completed:
        returncode = 0
        stdout = "v18.18.0"
        stderr = ""

    def fake_which(command: str) -> str | None:
        if command in {"node", "npm"}:
            return str(temp_root / command)
        return None

    def fake_run(_command: list[str], **_kwargs: object) -> Completed:
        return Completed()

    try:
        shutil.which = fake_which
        subprocess.run = fake_run
        report = validate_draft_paths(
            [path],
            render=True,
            require_render=True,
        )
    finally:
        shutil.which = original_which
        subprocess.run = original_run

    assert_true(not report["valid"], "incompatible Node should block auto install")
    assert_true(any("Node.js ^18.19 or >=20.0" in item["message"] for item in report["errors"]), "expected Node compatibility error")


def test_auto_install_missing_node_is_optional_warning(temp_root: Path) -> None:
    path = temp_root / "optional-render.md"
    path.write_text(
        "::: mermaid\n    graph TD;\n      A[\"Start\"] --> B[\"Done\"];\n:::\n",
        encoding="utf-8",
        newline="\n",
    )

    original_which = shutil.which

    def fake_which(_command: str) -> str | None:
        return None

    try:
        shutil.which = fake_which
        report = validate_draft_paths([path], render=True, require_render=False)
    finally:
        shutil.which = original_which

    assert_true(report["valid"], f"optional render setup failures should be warnings: {report['errors']}")
    assert_true(report["render"]["auto_install_requested"], "default mmdc render should allow automatic setup")
    assert_true(report["warnings"], "expected optional render warning when node/npm are unavailable")


def test_non_blocking_cli_returns_success(temp_root: Path) -> None:
    path = temp_root / "bad.md"
    path.write_text(
        "::: mermaid\n    flowchart TD;\n      A --> B;\n:::\n",
        encoding="utf-8",
        newline="\n",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parent / "validate_mermaid.py"),
            str(path),
            "--non-blocking",
            "--format",
            "json",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert_true(completed.returncode == 0, "non-blocking validation should exit 0")
    assert_true('"valid": false' in completed.stdout, "non-blocking validation should still report invalid")


def test_non_blocking_required_render_returns_success(temp_root: Path) -> None:
    path = temp_root / "render-missing.md"
    path.write_text(
        "::: mermaid\n    graph TD;\n      A[\"Start\"] --> B[\"Done\"];\n:::\n",
        encoding="utf-8",
        newline="\n",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parent / "validate_mermaid.py"),
            str(path),
            "--render",
            "--require-render",
            "--mmdc",
            "definitely-missing-mmdc",
            "--non-blocking",
            "--format",
            "json",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert_true(completed.returncode == 0, "non-blocking render validation should exit 0")
    assert_true('"valid": false' in completed.stdout, "non-blocking render should still report invalid")


def test_static_only_skips_render_even_when_required(temp_root: Path) -> None:
    path = temp_root / "static.md"
    write_azure_block(path, 'graph TD;\n  A["Start"] --> B["Done"];')
    report = validate_draft_paths([path], render=False, require_render=False)
    assert_true(report["valid"], f"static validation should pass: {report['errors']}")
    assert_true(report["render"] is None, "static-only validation should not render")
    assert_true("warning_groups" in report, "expected grouped warnings in report")


def test_changed_only_uses_git_diff_markdown_files(temp_root: Path) -> None:
    changed = temp_root / "docs" / "changed.md"
    ignored = temp_root / "docs" / "notes.txt"
    changed.parent.mkdir(parents=True)
    changed.write_text("::: mermaid\n    graph TD;\n      A[\"Start\"] --> B[\"Done\"];\n:::\n", encoding="utf-8", newline="\n")
    ignored.write_text("not markdown\n", encoding="utf-8", newline="\n")
    original_run = validate_mermaid.subprocess.run

    class Completed:
        returncode = 0
        stdout = "docs/changed.md\ndocs/notes.txt\n"
        stderr = ""

    def fake_run(command: list[str], **_kwargs: object) -> Completed:
        assert command[:3] == ["git", "diff", "--name-only"]
        return Completed()

    try:
        validate_mermaid.subprocess.run = fake_run
        files = validate_mermaid.changed_markdown_files(temp_root)
    finally:
        validate_mermaid.subprocess.run = original_run
    assert_true(files == [changed.resolve()], f"expected only changed markdown file, got {files}")


def test_autofix_converts_common_azure_incompatibilities(temp_root: Path) -> None:
    path = temp_root / "fix.md"
    path.write_text("```mermaid\nflowchart TD;\nA[Start] --> B[Done]\n```\n", encoding="utf-8", newline="\n")
    result = validate_mermaid.apply_autofix([path])
    text = path.read_text(encoding="utf-8")
    assert_true(result["changed"], "expected auto-fix to write file")
    assert_true("::: mermaid" in text, "expected Azure wrapper")
    assert_true("graph TD;" in text, "expected graph keyword")
    assert_true("    A[Start]" in text, "expected indented body")


def test_autofix_does_not_touch_files_without_mermaid(temp_root: Path) -> None:
    path = temp_root / "plain.md"
    original = "# Plain\n\nNo diagrams here.\n"
    path.write_text(original, encoding="utf-8", newline="\n")
    result = validate_mermaid.apply_autofix([path])
    assert_true(not result["changed"], "expected no auto-fix writes")
    assert_true(path.read_text(encoding="utf-8") == original, "plain markdown should remain unchanged")


def test_materialize_diagrams_writes_source_svg_and_embed(temp_root: Path) -> None:
    path = temp_root / "guide.md"
    path.write_text(
        "# Guide\n\n## Flow\n\n::: mermaid\n    graph TD;\n      A[\"Start\"] --> B[\"Done\"];\n:::\n",
        encoding="utf-8",
        newline="\n",
    )
    original_run = materialize_diagrams.subprocess.run

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command: list[str], **_kwargs: object) -> Completed:
        assert_mmdc_render_flags(command)
        output = Path(command[command.index("-o") + 1])
        output.write_text("<svg></svg>", encoding="utf-8")
        return Completed()

    try:
        materialize_diagrams.subprocess.run = fake_run
        report = materialize_diagrams.materialize_paths(
            [path],
            mmdc="python",
            auto_install_mmdc=False,
            dry_run=False,
        )
    finally:
        materialize_diagrams.subprocess.run = original_run

    source = temp_root / "diagrams" / "guide-flow.mmd"
    image = temp_root / "diagrams" / "guide-flow.svg"
    text = path.read_text(encoding="utf-8")
    assert_true(report["diagram_count"] == 1, "expected one materialized diagram")
    assert_true(source.exists(), "expected Mermaid source file")
    assert_true(image.exists(), "expected rendered SVG")
    assert_true("[![Flow diagram](diagrams/guide-flow.svg)](diagrams/guide-flow.svg)" in text, "expected linked SVG embed")
    assert_true("Source: [Mermaid](diagrams/guide-flow.mmd)" in text, "expected source link")


def test_materialize_diagrams_refuses_existing_unlinked_targets(temp_root: Path) -> None:
    root = temp_root / "collision"
    root.mkdir()
    path = root / "collision.md"
    path.write_text(
        "# Guide\n\n## Flow\n\n::: mermaid\n    graph TD;\n      A[\"Start\"] --> B[\"Done\"];\n:::\n",
        encoding="utf-8",
        newline="\n",
    )
    diagrams = root / "diagrams"
    diagrams.mkdir()
    existing = diagrams / "collision-flow.mmd"
    existing.write_text("graph TD;\n  Existing --> Asset;\n", encoding="utf-8", newline="\n")

    dry_run = materialize_diagrams.materialize_paths(
        [path],
        mmdc="definitely-missing-mmdc",
        auto_install_mmdc=False,
        dry_run=True,
    )
    assert_true(dry_run["diagram_count"] == 1, "dry-run should still report planned materialization")

    try:
        materialize_diagrams.materialize_paths(
            [path],
            mmdc="definitely-missing-mmdc",
            auto_install_mmdc=False,
            dry_run=False,
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected materialization collision to fail before renderer setup")

    assert_true("generated target files already exist" in message, "expected collision explanation")
    assert_true("collision-flow.mmd" in message, "expected colliding target path")
    assert_true("Existing --> Asset" in existing.read_text(encoding="utf-8"), "existing target must not be overwritten")


def test_normalize_svg_canvas_adds_intrinsic_size_and_vertical_padding(temp_root: Path) -> None:
    image = temp_root / "diagram.svg"
    image.write_text(
        '<svg id="my-svg" width="100%" style="max-width: 100px; background-color: transparent;" '
        'viewBox="0 0 100 200"><g></g></svg>',
        encoding="utf-8",
        newline="\n",
    )

    materialize_diagrams.normalize_svg_canvas(image)
    materialize_diagrams.normalize_svg_canvas(image)

    text = image.read_text(encoding="utf-8")
    assert_true('width="100"' in text, "expected intrinsic SVG width")
    assert_true('height="248"' in text, "expected vertical padding in SVG height")
    assert_true('viewBox="0 -24 100 248"' in text, "expected top and bottom viewBox padding")
    assert_true(
        'data-mermaid-vertical-padding="24"' in text,
        "expected idempotent SVG padding marker",
    )


def test_materialize_workflow_diagrams_use_diagrams_dir(temp_root: Path) -> None:
    workflow = temp_root / "automations" / "demo"
    workflow.mkdir(parents=True)
    (workflow / "module.json").write_text('{"id":"demo"}\n', encoding="utf-8", newline="\n")
    path = workflow / "WORKFLOW.md"
    path.write_text(
        "# Demo\n\n## Process Diagram\n\n::: mermaid\n    graph TD;\n      A[\"Start\"] --> B[\"Done\"];\n:::\n",
        encoding="utf-8",
        newline="\n",
    )
    original_run = materialize_diagrams.subprocess.run

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command: list[str], **_kwargs: object) -> Completed:
        assert_mmdc_render_flags(command)
        output = Path(command[command.index("-o") + 1])
        output.write_text("<svg></svg>", encoding="utf-8")
        return Completed()

    try:
        materialize_diagrams.subprocess.run = fake_run
        materialize_diagrams.materialize_paths([path], mmdc="python", auto_install_mmdc=False, dry_run=False)
    finally:
        materialize_diagrams.subprocess.run = original_run

    assert_true((workflow / "diagrams" / "workflow-process-diagram.mmd").exists(), "expected workflow source under diagrams")
    text = path.read_text(encoding="utf-8")
    assert_true("diagrams/workflow-process-diagram.svg" in text, "expected workflow diagram link")


def test_refresh_existing_diagrams_rerenders_linked_sources(temp_root: Path) -> None:
    root = temp_root / "refresh-existing"
    diagrams = root / "diagrams"
    diagrams.mkdir(parents=True)
    path = root / "guide.md"
    path.write_text(
        "[![Flow diagram](diagrams/flow.svg)](diagrams/flow.svg)\n\n"
        "Source: [Mermaid](diagrams/flow.mmd)\n",
        encoding="utf-8",
        newline="\n",
    )
    (diagrams / "flow.mmd").write_text('graph TD;\n  A["Start"] --> B["Done"];\n', encoding="utf-8", newline="\n")
    original_run = materialize_diagrams.subprocess.run

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command: list[str], **_kwargs: object) -> Completed:
        assert_mmdc_render_flags(command)
        output = Path(command[command.index("-o") + 1])
        output.write_text(
            '<svg id="my-svg" width="100%" height="100" '
            'style="max-width: 100px; background-color: transparent;" viewBox="0 0 100 100">'
            '<g></g></svg>',
            encoding="utf-8",
        )
        return Completed()

    try:
        materialize_diagrams.subprocess.run = fake_run
        report = materialize_diagrams.materialize_paths(
            [path],
            mmdc="python",
            auto_install_mmdc=False,
            dry_run=False,
            refresh_existing=True,
        )
    finally:
        materialize_diagrams.subprocess.run = original_run

    text = (diagrams / "flow.svg").read_text(encoding="utf-8")
    assert_true(report["diagram_count"] == 1, "expected one refreshed diagram")
    assert_true('width="100"' in text, "expected refreshed intrinsic width")
    assert_true('data-mermaid-vertical-padding="24"' in text, "expected normalized padding metadata")


def test_diagram_inventory_counts_mixed_markdown(temp_root: Path) -> None:
    path = temp_root / "mixed.md"
    path.write_text(
        "::: mermaid\n    graph TD;\n      A[\"Start\"] --> B[\"Done\"];\n:::\n\n"
        "```mermaid\nsequenceDiagram\n  A->>B: Hi\n```\n",
        encoding="utf-8",
        newline="\n",
    )
    inventory = validate_mermaid.diagram_inventory([path])
    assert_true(inventory["diagram_count"] == 2, "expected two diagrams")
    assert_true(inventory["by_wrapper"]["azure"] == 1, "expected one Azure block")
    assert_true(inventory["by_wrapper"]["fenced"] == 1, "expected one fenced block")


def test_doctor_packet_is_read_only_and_stable(temp_root: Path) -> None:
    path = temp_root / "doctor.mmd"
    path.write_text('graph TD;\n  A["Start"] --> B["Done"];\n', encoding="utf-8", newline="\n")

    original_validator_which = validate_mermaid.shutil.which
    original_setup_which = setup_vscode_mermaid_preview.shutil.which

    def fake_which(_command: str) -> str | None:
        return None

    try:
        validate_mermaid.shutil.which = fake_which
        setup_vscode_mermaid_preview.shutil.which = fake_which
        report = validate_mermaid.build_doctor_report([path], mmdc="definitely-missing-mmdc")
    finally:
        validate_mermaid.shutil.which = original_validator_which
        setup_vscode_mermaid_preview.shutil.which = original_setup_which

    assert_true(report["tool"] == "mermaid-diagrams-azure-devops.doctor", "expected doctor tool id")
    assert_true(report["write_policy"]["writes_allowed"] is False, "doctor must be read-only")
    assert_true(report["write_policy"]["auto_install_mmdc"] is False, "doctor must disable mmdc install")
    assert_true(report["write_policy"]["vscode_auto_install"] is False, "doctor must disable VS Code install")
    assert_true(report["status"]["parser"] == "pass", f"expected parser pass: {report}")
    assert_true(report["status"]["render"] == "warn", "missing optional renderer should be a warning")
    assert_true(report["status"]["setup"] == "skipped", "missing VS Code CLI should skip setup")
    assert_true(report["render"]["auto_install_requested"] is False, "render setup must not be requested")
    assert_true(report["setup"]["install_attempted"] is False, "VS Code setup must not install")
    assert_true(report["wrappers"]["source"] == 1, "expected source wrapper evidence")
    assert_true("graph" in report["diagram_types"], "expected diagram type evidence")


def test_doctor_cli_returns_json(temp_root: Path) -> None:
    path = temp_root / "doctor-cli.mmd"
    path.write_text('graph TD;\n  A["Start"] --> B["Done"];\n', encoding="utf-8", newline="\n")
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parent / "validate_mermaid.py"),
            str(path),
            "--doctor",
            "--mmdc",
            "definitely-missing-mmdc",
            "--non-blocking",
            "--format",
            "json",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert_true(completed.returncode == 0, completed.stderr or completed.stdout)
    data = json.loads(completed.stdout)
    assert_true(data["tool"] == "mermaid-diagrams-azure-devops.doctor", "expected doctor JSON")
    assert_true(data["write_policy"]["writes_allowed"] is False, "doctor JSON should declare read-only mode")


def test_cli_help_classifies_read_only_write_and_install_modes(_temp_root: Path | None = None) -> None:
    validate_help = validate_mermaid.build_parser().format_help()
    materialize_help = materialize_diagrams.build_parser().format_help()
    setup_help = setup_vscode_mermaid_preview.parse_args

    assert_true("read-only: run static checks only" in validate_help, "static-only help must classify read-only mode")
    assert_true("write:" in validate_help and "compatibility" in validate_help and "fixes" in validate_help, "fix help must classify write mode")
    assert_true("read-only/no-install evidence packet" in validate_help, "doctor help must classify no-install mode")
    assert_true("may auto-install default mmdc" in validate_help, "render help must warn about auto-install")
    assert_true("use when installs are forbidden" in validate_help, "no-auto-install help must name install boundary")

    assert_true("writes unless --dry-run is used" in materialize_help, "materialize help must name default writes")
    assert_true("read-only: report planned writes" in materialize_help, "dry-run help must classify read-only mode")
    assert_true("write/render: rerender" in materialize_help, "refresh help must classify write/render mode")

    setup_stdout = io.StringIO()
    with contextlib.redirect_stdout(setup_stdout):
        try:
            setup_help(["--help"])
        except SystemExit:
            pass
    setup_text = setup_stdout.getvalue()
    setup_flat = " ".join(setup_text.split())
    assert_true("Without --auto-install this is read-only inspection" in setup_text, "setup help must classify read-only mode")
    assert_true("install the recommended VS Code extension" in setup_text, "setup help must classify auto-install mode")
    assert_true("setup always reports evidence and exits 0" in setup_flat, "setup help must classify non-blocking exit behavior")


def patch_vscode_cli(
    temp_root: Path,
    installed_extensions: set[str],
    *,
    available: dict[str, bool] | None = None,
    failing_commands: set[str] | None = None,
    install_success: bool = True,
) -> tuple[object, object, list[list[str]]]:
    original_which = setup_vscode_mermaid_preview.shutil.which
    original_run = setup_vscode_mermaid_preview.subprocess.run
    commands: list[list[str]] = []
    if available is None:
        available = {"code.cmd": True}
    if failing_commands is None:
        failing_commands = set()

    class Completed:
        def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_which(command: str) -> str | None:
        if available.get(command):
            return str(temp_root / command)
        return None

    def fake_run(command: list[str], **_kwargs: object) -> Completed:
        commands.append(command)
        executable = Path(command[0]).name
        if executable in failing_commands:
            return Completed(returncode=1, stderr=f"{executable} failed")
        if "--version" in command:
            return Completed(stdout="1.99.0\nabc123\nx64")
        if "--list-extensions" in command:
            return Completed(stdout="\n".join(sorted(installed_extensions)))
        if "--install-extension" in command:
            if install_success:
                installed_extensions.add(
                    f"{setup_vscode_mermaid_preview.RECOMMENDED_EXTENSION}@1.27.0"
                )
                return Completed(stdout="installed")
            return Completed(returncode=1, stderr="marketplace unavailable")
        return Completed(returncode=1, stderr="unexpected command")

    setup_vscode_mermaid_preview.shutil.which = fake_which
    setup_vscode_mermaid_preview.subprocess.run = fake_run
    return original_which, original_run, commands


def restore_vscode_cli(original_which: object, original_run: object) -> None:
    setup_vscode_mermaid_preview.shutil.which = original_which
    setup_vscode_mermaid_preview.subprocess.run = original_run


def test_vscode_preview_recommended_installed(temp_root: Path) -> None:
    original_which, original_run, commands = patch_vscode_cli(
        temp_root,
        {f"{setup_vscode_mermaid_preview.RECOMMENDED_EXTENSION}@1.27.0"},
    )
    try:
        report = setup_vscode_mermaid_preview.setup_vscode_preview(auto_install=True)
    finally:
        restore_vscode_cli(original_which, original_run)

    assert_true(report["valid"], f"installed extension should pass: {report['errors']}")
    assert_true(report["recommended_installed"], "recommended extension should be detected")
    assert_true(not report["install_attempted"], "install should not run when extension exists")
    assert_true(
        not any("--install-extension" in command for command in commands),
        "setup should not reinstall existing extension",
    )


def test_vscode_preview_auto_install_success(temp_root: Path) -> None:
    original_which, original_run, _commands = patch_vscode_cli(temp_root, set())
    try:
        report = setup_vscode_mermaid_preview.setup_vscode_preview(auto_install=True)
    finally:
        restore_vscode_cli(original_which, original_run)

    assert_true(report["valid"], f"auto-install should pass: {report['errors']}")
    assert_true(report["install_attempted"], "install should be attempted")
    assert_true(report["install_succeeded"], "install should succeed")
    assert_true(report["install_verified"], "install should be verified")


def test_vscode_preview_auto_install_failure_continues(temp_root: Path) -> None:
    original_which, original_run, _commands = patch_vscode_cli(
        temp_root,
        set(),
        install_success=False,
    )
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            code = setup_vscode_mermaid_preview.main(
                ["--auto-install", "--format", "json"]
            )
    finally:
        restore_vscode_cli(original_which, original_run)

    assert_true(code == 0, "setup should exit 0 after install failure")
    assert_true('"valid": false' in stdout.getvalue(), "setup should still report failed setup status")


def test_vscode_preview_fallback_cli(temp_root: Path) -> None:
    original_which, original_run, _commands = patch_vscode_cli(
        temp_root,
        {f"{setup_vscode_mermaid_preview.RECOMMENDED_EXTENSION}@1.27.0"},
        available={"code": True, "code-insiders.cmd": True},
        failing_commands={"code"},
    )
    try:
        report = setup_vscode_mermaid_preview.setup_vscode_preview(auto_install=False)
    finally:
        restore_vscode_cli(original_which, original_run)

    assert_true(report["valid"], f"fallback CLI should pass: {report['errors']}")
    assert_true(
        report["cli"]["command"] == "code-insiders.cmd",
        "setup should reject unusable code CLI and accept later working CLI",
    )


def test_vscode_preview_no_cli_skips(temp_root: Path) -> None:
    original_which, original_run, _commands = patch_vscode_cli(temp_root, set(), available={})
    try:
        report = setup_vscode_mermaid_preview.setup_vscode_preview(auto_install=True)
    finally:
        restore_vscode_cli(original_which, original_run)

    assert_true(report["valid"], "missing VS Code CLI should skip without failing")
    assert_true(report["skipped"], "missing VS Code CLI should mark setup skipped")
    assert_true("No usable VS Code CLI" in report["skip_reason"], "expected missing CLI skip message")


def test_vscode_preview_visual_studio_skips(temp_root: Path) -> None:
    original_which, original_run, _commands = patch_vscode_cli(
        temp_root,
        set(),
        available={"devenv.exe": True},
    )
    try:
        report = setup_vscode_mermaid_preview.setup_vscode_preview(auto_install=True)
    finally:
        restore_vscode_cli(original_which, original_run)

    assert_true(report["valid"], "Visual Studio-only environments should skip without failing")
    assert_true(report["skipped"], "Visual Studio-only environment should mark setup skipped")
    assert_true(report["visual_studio_detected"], "Visual Studio should be detected")
    assert_true("not applicable" in report["skip_reason"], "expected Visual Studio skip reason")


def test_vscode_preview_rider_skips(temp_root: Path) -> None:
    original_which, original_run, _commands = patch_vscode_cli(
        temp_root,
        set(),
        available={"rider": True},
    )
    try:
        report = setup_vscode_mermaid_preview.setup_vscode_preview(auto_install=True)
    finally:
        restore_vscode_cli(original_which, original_run)

    assert_true(report["valid"], "Rider-only environments should skip without failing")
    assert_true(report["skipped"], "Rider-only environment should mark setup skipped")
    assert_true(report["rider_detected"], "Rider should be detected")
    assert_true("not applicable" in report["skip_reason"], "expected Rider skip reason")


def test_vscode_preview_conflict_detection(temp_root: Path) -> None:
    original_which, original_run, _commands = patch_vscode_cli(
        temp_root,
        {
            f"{setup_vscode_mermaid_preview.RECOMMENDED_EXTENSION}@1.27.0",
            "mermaidchart.vscode-mermaid-chart@1.0.0",
            "sample.markdown-preview-plus@2.0.0",
        },
    )
    try:
        report = setup_vscode_mermaid_preview.setup_vscode_preview(auto_install=False)
    finally:
        restore_vscode_cli(original_which, original_run)

    assert_true(report["valid"], f"conflicts are report-only warnings: {report['errors']}")
    assert_true(report["conflicts"], "expected Mermaid Chart conflict")
    assert_true(
        any("markdown-preview-plus" in warning for warning in report["warnings"]),
        "expected preview-like extension warning",
    )


def test_vscode_preview_no_uninstall_disable_or_settings(temp_root: Path) -> None:
    original_which, original_run, commands = patch_vscode_cli(temp_root, set())
    try:
        setup_vscode_mermaid_preview.setup_vscode_preview(auto_install=True)
    finally:
        restore_vscode_cli(original_which, original_run)

    flattened = "\n".join(" ".join(command) for command in commands)
    assert_true("--uninstall-extension" not in flattened, "setup must never uninstall extensions")
    assert_true("--disable-extension" not in flattened, "setup must never disable extensions")
    assert_true("settings.json" not in flattened, "setup must never modify VS Code settings")
    assert_true(
        not (temp_root / ".vscode" / "settings.json").exists(),
        "setup must not create VS Code settings",
    )


def test_vscode_preview_no_fixed_ide_version_requirements() -> None:
    script = (Path(__file__).resolve().parent / "setup_vscode_mermaid_preview.py").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "visual studio 202",
        "vs code 1.",
        "vscode 1.",
        "rider 20",
        "code >= ",
        "rider >= ",
    )
    lowered = script.lower()
    for fragment in forbidden:
        assert_true(fragment not in lowered, f"fixed IDE version requirement found: {fragment}")


def test_no_uncontrolled_remote_or_setup_behavior() -> None:
    skill_root = Path(__file__).resolve().parents[1]
    forbidden = [
        "pip " + "install",
        "cur" + "l ",
        "wg" + "et ",
        "urllib" + ".request",
        "requests" + ".",
        ".vscode" + "/settings",
        ".mcp" + ".json",
    ]
    for path in skill_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".md", ".py", ".json"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for fragment in forbidden:
            assert_true(fragment not in text, f"forbidden behavior marker found in {path}: {fragment}")
    validator = (skill_root / "scripts" / "validate_mermaid.py").read_text(encoding="utf-8")
    assert_true("--no-auto-install-mmdc" in validator, "default mmdc setup needs an explicit opt-out")


def run_all() -> None:
    tests = [
        test_azure_extraction_and_validation,
        test_compact_azure_wrapper_accepted,
        test_supported_azure_diagram_types,
        test_requirement_diagram_rejects_unquoted_hyphenated_id,
        test_mermaid_source_file_validation,
        test_markdown_mermaid_blocks_require_materialization,
        test_linked_svg_artifacts_require_dark_transparent_intrinsic_canvas,
        test_linked_svg_artifacts_accept_normalized_dark_canvas,
        test_unlinked_materialized_mermaid_artifacts_fail_validation,
        test_mermaid_template_assets_allow_unlinked_blocks,
        test_fenced_mermaid_rejected,
        test_flowchart_rejected,
        test_azure_incompatible_syntax_rejected,
        test_subgraph_grouping_allowed,
        test_subgraph_id_edges_rejected,
        test_graph_label_hygiene_warnings,
        test_require_render_missing,
        test_render_success_with_stub,
        test_auto_install_success_with_compatible_node,
        test_auto_install_rejects_incompatible_node,
        test_auto_install_missing_node_is_optional_warning,
        test_non_blocking_cli_returns_success,
        test_non_blocking_required_render_returns_success,
        test_static_only_skips_render_even_when_required,
        test_changed_only_uses_git_diff_markdown_files,
        test_autofix_converts_common_azure_incompatibilities,
        test_autofix_does_not_touch_files_without_mermaid,
        test_materialize_diagrams_writes_source_svg_and_embed,
        test_materialize_diagrams_refuses_existing_unlinked_targets,
        test_normalize_svg_canvas_adds_intrinsic_size_and_vertical_padding,
        test_materialize_workflow_diagrams_use_diagrams_dir,
        test_refresh_existing_diagrams_rerenders_linked_sources,
        test_diagram_inventory_counts_mixed_markdown,
        test_doctor_packet_is_read_only_and_stable,
        test_doctor_cli_returns_json,
        test_cli_help_classifies_read_only_write_and_install_modes,
        test_vscode_preview_recommended_installed,
        test_vscode_preview_auto_install_success,
        test_vscode_preview_auto_install_failure_continues,
        test_vscode_preview_fallback_cli,
        test_vscode_preview_no_cli_skips,
        test_vscode_preview_visual_studio_skips,
        test_vscode_preview_rider_skips,
        test_vscode_preview_conflict_detection,
        test_vscode_preview_no_uninstall_disable_or_settings,
    ]
    with tempfile.TemporaryDirectory(prefix="mermaid-tests-") as temp_name:
        temp_root = Path(temp_name)
        for test in tests:
            test(temp_root)
    test_selection_guide_recommends_erd()
    test_vscode_preview_no_fixed_ide_version_requirements()
    test_no_uncontrolled_remote_or_setup_behavior()


def main() -> int:
    validate_mermaid.require_supported_python()
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            run_all()
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("Mermaid diagrams self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
