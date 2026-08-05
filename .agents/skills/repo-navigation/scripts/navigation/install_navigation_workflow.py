#!/usr/bin/env python3
"""Install a generated navigation workflow into a target project."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import navigation_core

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_MANAGER_SCRIPTS = SCRIPT_DIR.parents[2] / "skill-manager" / "scripts"
if str(SKILL_MANAGER_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_MANAGER_SCRIPTS))

import module_contract_v3


NAVIGATION_WORKFLOW_UPDATED = "2026-07-25"


def require_supported_python() -> None:
    navigation_core.require_supported_python()


def workflow_files() -> dict[str, str]:
    primary_commands = [
        "python -B automations/navigation/scripts/update_navigation.py --target . --check",
        "python -B automations/navigation/scripts/update_navigation.py --target . --write",
        "python -B automations/navigation/scripts/project_context.py --target . --check",
        "python -B automations/navigation/scripts/project_context.py --target . --write",
    ]
    strict_commands = [
        'python -B .agents/manage.py which-workflow "<request>" --summary --compact --format json',
        "python -B .agents/manage.py validate-automations --name navigation --summary --compact --format json",
        "python -B .agents/manage.py workflow metadata inspect --name navigation --summary --compact --format json",
        "python -B automations/navigation/scripts/update_navigation.py --target . --check --format json",
        "python -B automations/navigation/scripts/project_context.py --target . --check --format json",
    ]
    command_specs: list[dict[str, Any]] = []
    ids_by_text: dict[str, str] = {}
    for command_text in [*primary_commands, *strict_commands]:
        if command_text in ids_by_text:
            continue
        argv = module_contract_v3.lexical_argv_from_text(command_text)
        command_id = module_contract_v3.command_id_for_argv(argv)
        ids_by_text[command_text] = command_id
        command = {
            "id": command_id,
            "argv": argv,
            "timeout_seconds": 300,
            "working_directory": "repository",
            "effects": [],
        }
        command["effects"] = module_contract_v3.infer_command_effects(
            command,
            {"id": "navigation"},
        )
        command_specs.append(command)
    template_layers = module_contract_v3.conventional_template_layers("navigation")
    template_layers["profiles"] = {}
    manifest = {
        "schema_version": 3,
        "kind": "workflow",
        "id": "navigation",
        "version": "1.0.0",
        "summary": "Generate deterministic project navigation maps, technical context, conventions, and freshness evidence.",
        "owners": ["engineering"],
        "phases": [
            {"id": "scan", "summary": "Scan project files, manifests, commands, and symbols."},
            {"id": "project-context", "summary": "Prepare or check the human-owned project context profile."},
            {"id": "write", "summary": "Write navigation maps and staleness evidence."},
            {"id": "check", "summary": "Compare generated maps with committed outputs."},
        ],
        "inputs": ["WORKFLOW.md", "module.json", "instructions.md", "project source files"],
        "outputs": [
            "artifacts/maps/NAVIGATION.md",
            "artifacts/maps/HANDOFF.md",
            "artifacts/maps/handoff.json",
            "artifacts/maps/TECHNICAL_CONTEXT.md",
            "artifacts/maps/CONVENTIONS.md",
            "artifacts/maps/PROJECT_CONTEXT_DRAFT.md",
            "artifacts/maps/staleness.json",
            "docs/project/project-context.md",
        ],
        "commands": command_specs,
        "strict_read_only_commands": [ids_by_text[text] for text in strict_commands],
        "template_layers": template_layers,
        "context": module_contract_v3.conventional_context("navigation"),
        "related_modules": ["repo-navigation"],
        "validation": ["python -B automations/navigation/scripts/update_navigation.py --target . --check"],
        "context_evidence": {
            "required": True,
            "start_queries": [
                {
                    "id": "navigation-start",
                    "question": "What project context and navigation maps are available for this repository?",
                    "scope": "repo",
                    "fallback_paths": [
                        "docs/project/project-context.md",
                        "automations/navigation/artifacts/maps/HANDOFF.md",
                    ],
                }
            ],
            "resume_queries": [
                {
                    "id": "navigation-resume",
                    "question": "What navigation map files changed or may be stale for this repository?",
                    "scope": "repo",
                    "fallback_paths": [
                        "automations/navigation/artifacts/maps/staleness.json",
                        "automations/navigation/artifacts/maps/NAVIGATION.md",
                    ],
                }
            ],
            "finish_queries": [
                {
                    "id": "navigation-finish",
                    "question": "What navigation outputs and project context files should be validated before handoff?",
                    "scope": "repo",
                    "fallback_paths": [
                        "automations/navigation/artifacts/maps/HANDOFF.md",
                        "docs/project/project-context.md",
                    ],
                }
            ],
        },
        "external_access": {
            "source_systems": [],
            "credential_expectations": "none",
            "data_copied_locally": ["project file names", "hashes", "deterministic summaries"],
            "attachments_retrieved": False,
        },
        "local_ai": {"use_cases": []},
        "metadata_path": "metadata/workflow-metadata.json",
        "risk": {
            "credentials": False,
            "destructive": False,
            "generated_settings": False,
            "installs": False,
            "network": False,
            "production_writes": False,
            "uploads": False,
            "profile": "local-write",
        },
        "routing": {
            "activation_terms": [
                "navigation",
                "project-context",
                "staleness",
            ],
            "terms": [
                "navigation",
                "map",
                "maps",
                "handoff",
                "capsule",
                "project-context",
                "staleness",
                "stale",
                "refresh",
                "read-order",
            ],
            "threshold": 2,
            "winner_margin": 1,
        },
        "extensions": {},
    }
    process_mmd = "\n".join(
        [
            "graph TD;",
            '  start["Read project guidance"] --> scan["Scan files and manifests"];',
            '  scan["Scan files and manifests"] --> context["Check project context"];',
            '  context["Check project context"] --> maps["Write navigation maps"];',
            '  maps["Write navigation maps"] --> check["Compare freshness evidence"];',
            '  check["Compare freshness evidence"] --> handoff["Report map status"];',
            "",
        ]
    )
    connection_mmd = "\n".join(
        [
            "graph LR;",
            '  project["Project files"] --> scanner["Navigation scanner"];',
            '  scanner["Navigation scanner"] --> maps["artifacts/maps"];',
            '  scanner["Navigation scanner"] --> context["docs/project/project-context.md"];',
            '  maps["artifacts/maps"] --> workflows["Story and bug workflows"];',
            '  context["docs/project/project-context.md"] --> workflows["Story and bug workflows"];',
            "",
        ]
    )
    process_svg = "\n".join(
        [
            '<svg id="navigation-process" xmlns="http://www.w3.org/2000/svg" width="720" height="228" viewBox="0 -24 720 228" style="max-width: 720px; background-color: transparent;" preserveAspectRatio="xMidYMid meet" data-mermaid-vertical-padding="24" role="img" aria-labelledby="navigation-process-title navigation-process-desc">',
            '<title id="navigation-process-title">Navigation workflow process</title>',
            '<desc id="navigation-process-desc">Process from project guidance through scan, context, map writing, freshness check, and handoff.</desc>',
            '<defs><marker id="navigation-process-arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="#94a3b8"/></marker></defs>',
            '<g fill="#111827" stroke="#64748b" stroke-width="1.5">',
            '<rect x="18" y="20" width="132" height="44" rx="6"/>',
            '<rect x="176" y="20" width="132" height="44" rx="6"/>',
            '<rect x="334" y="20" width="132" height="44" rx="6"/>',
            '<rect x="492" y="20" width="132" height="44" rx="6"/>',
            '<rect x="176" y="126" width="132" height="44" rx="6"/>',
            '<rect x="334" y="126" width="132" height="44" rx="6"/>',
            '</g>',
            '<g stroke="#94a3b8" stroke-width="1.8" fill="none" marker-end="url(#navigation-process-arrow)">',
            '<path d="M150,42 L176,42"/>',
            '<path d="M308,42 L334,42"/>',
            '<path d="M466,42 L492,42"/>',
            '<path d="M558,64 L558,98 L242,98 L242,126"/>',
            '<path d="M308,148 L334,148"/>',
            '</g>',
            '<g font-family="Arial, sans-serif" font-size="13" fill="#e5e7eb" text-anchor="middle">',
            '<text x="84" y="39">Read project</text><text x="84" y="55">guidance</text>',
            '<text x="242" y="39">Scan files</text><text x="242" y="55">and manifests</text>',
            '<text x="400" y="39">Check project</text><text x="400" y="55">context</text>',
            '<text x="558" y="39">Write navigation</text><text x="558" y="55">maps</text>',
            '<text x="242" y="145">Compare freshness</text><text x="242" y="161">evidence</text>',
            '<text x="400" y="145">Report map</text><text x="400" y="161">status</text>',
            '</g>',
            "</svg>",
            "",
        ]
    )
    connection_svg = "\n".join(
        [
            '<svg id="navigation-connection" xmlns="http://www.w3.org/2000/svg" width="720" height="204" viewBox="0 -24 720 204" style="max-width: 720px; background-color: transparent;" preserveAspectRatio="xMidYMid meet" data-mermaid-vertical-padding="24" role="img" aria-labelledby="navigation-connection-title navigation-connection-desc">',
            '<title id="navigation-connection-title">Navigation workflow connections</title>',
            '<desc id="navigation-connection-desc">Project files feed the navigation scanner, which writes maps and project context used by implementation workflows.</desc>',
            '<defs><marker id="navigation-connection-arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="#94a3b8"/></marker></defs>',
            '<g fill="#111827" stroke="#64748b" stroke-width="1.5">',
            '<rect x="24" y="54" width="124" height="44" rx="6"/>',
            '<rect x="194" y="54" width="142" height="44" rx="6"/>',
            '<rect x="390" y="18" width="144" height="44" rx="6"/>',
            '<rect x="390" y="108" width="186" height="44" rx="6"/>',
            '<rect x="602" y="64" width="94" height="58" rx="6"/>',
            '</g>',
            '<g stroke="#94a3b8" stroke-width="1.8" fill="none" marker-end="url(#navigation-connection-arrow)">',
            '<path d="M148,76 L194,76"/>',
            '<path d="M336,76 C360,76 360,40 390,40"/>',
            '<path d="M336,76 C360,76 360,130 390,130"/>',
            '<path d="M534,40 C582,40 582,82 602,82"/>',
            '<path d="M576,130 C594,130 594,104 602,104"/>',
            '</g>',
            '<g font-family="Arial, sans-serif" font-size="13" fill="#e5e7eb" text-anchor="middle">',
            '<text x="86" y="73">Project</text><text x="86" y="89">files</text>',
            '<text x="265" y="73">Navigation</text><text x="265" y="89">scanner</text>',
            '<text x="462" y="37">artifacts/maps</text><text x="462" y="53">outputs</text>',
            '<text x="483" y="127">docs/project</text><text x="483" y="143">project context</text>',
            '<text x="649" y="84">Workflows</text><text x="649" y="100">consume</text>',
            '</g>',
            "</svg>",
            "",
        ]
    )
    eval_suite = {
        "schema_version": 1,
        "workflow_name": "navigation",
        "evals": [
            {
                "id": "navigation-generated-workflow-validates",
                "name": "Generated navigation workflow validates",
                "assertions": [
                    {"type": "validation_ok"},
                    {"type": "contract_declares_related_module", "module": "repo-navigation"},
                    {
                        "type": "contract_declares_command",
                        "command": "update_navigation.py --target . --check",
                    },
                    {"type": "contract_contains", "text": "strict_read_only_commands"},
                    {
                        "type": "contract_contains",
                        "text": "project_context.py --target . --check --format json",
                    },
                    {"type": "contract_declares_output", "path": "artifacts/maps/HANDOFF.md"},
                    {"type": "contract_declares_output", "path": "docs/project/project-context.md"},
                    {"type": "file_exists", "path": "diagrams/navigation-process.mmd"},
                    {"type": "file_exists", "path": "diagrams/navigation-process.svg"},
                    {"type": "file_exists", "path": "diagrams/navigation-connection.mmd"},
                    {"type": "file_exists", "path": "diagrams/navigation-connection.svg"},
                    {"type": "file_exists", "path": "artifacts/maps/HANDOFF.md"},
                    {"type": "file_exists", "path": "scripts/update_navigation.py"},
                    {"type": "start_contains", "text": "## Read-Only Dogfood"},
                    {"type": "start_contains", "text": "module.json.strict_read_only_commands"},
                    {
                        "type": "start_contains",
                        "text": "do not follow a `workflow start` next command",
                    },
                    {
                        "type": "instructions_contains",
                        "text": "Strict read-only/offline/no-profile/no-temp/no-write dogfood does not write",
                    },
                    {
                        "type": "file_contains",
                        "path": "scripts/project_context.py",
                        "text": "python -B automations/navigation/scripts/project_context.py --target . --write",
                    },
                    {
                        "type": "file_contains",
                        "path": "scripts/project_context.py",
                        "text": "python -B automations/navigation/scripts/update_navigation.py --target . --check",
                    },
                ],
            }
        ],
    }
    return {
        "automations/navigation/module.json": json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        "automations/navigation/metadata/workflow-metadata.json": json.dumps(
            {"updated": NAVIGATION_WORKFLOW_UPDATED}, indent=2, sort_keys=True
        )
        + "\n",
        "automations/navigation/WORKFLOW.md": "\n".join(
            [
                "# Navigation Workflow",
                "",
                "Start by reading `module.json`, this `WORKFLOW.md`, `instructions.md`, then `artifacts/maps/HANDOFF.md` and `artifacts/maps/NAVIGATION.md` when present.",
                "",
                "## Read-Only Dogfood",
                "",
                "This section overrides lifecycle/context-evidence/write steps. Route with `python -B .agents/manage.py which-workflow \"<request>\" --summary --compact --format json`; use the result only to confirm `selected_owner`, and do not follow a `workflow start` next command. Run only commands listed in `module.json.strict_read_only_commands`, plus exact `rg`/file reads. The canonical freshness command is `python -B automations/navigation/scripts/update_navigation.py --target . --check --format json`; use `--write` only when intentionally refreshing generated map outputs.",
                "Lifecycle smoke commands are validation commands only; do not list or run them in strict no-temp dogfood.",
                "",
                "## Diagrams",
                "",
                "[![Navigation process](diagrams/navigation-process.svg)](diagrams/navigation-process.svg)",
                "",
                "Source: [Mermaid](diagrams/navigation-process.mmd)",
                "",
                "[![Navigation connections](diagrams/navigation-connection.svg)](diagrams/navigation-connection.svg)",
                "",
                "Source: [Mermaid](diagrams/navigation-connection.mmd)",
                "",
                "## Example Prompts",
                "",
                '- Start: "Initialize or refresh navigation maps for this project."',
                '- Resume: "Check whether navigation maps are stale and report the next refresh action."',
                '- Handoff: "Summarize the current navigation handoff and project-context status."',
                '- Finish: "Validate navigation maps and project context, then report written files and skipped checks."',
                "",
            ]
        ),
        "automations/navigation/diagrams/navigation-process.mmd": process_mmd,
        "automations/navigation/diagrams/navigation-process.svg": process_svg,
        "automations/navigation/diagrams/navigation-connection.mmd": connection_mmd,
        "automations/navigation/diagrams/navigation-connection.svg": connection_svg,
        "automations/navigation/suites/workflow-evals.json": json.dumps(eval_suite, indent=2, sort_keys=True) + "\n",
        "automations/navigation/instructions.md": "\n".join(
            [
                "# Navigation Instructions",
                "",
                "## Always Load",
                "",
                "- `module.json`",
                "- `WORKFLOW.md`",
                "- `artifacts/maps/HANDOFF.md` when present",
                "- `artifacts/maps/NAVIGATION.md` when present",
                "",
                "Strict read-only/offline/no-profile/no-temp/no-write dogfood does not write generated maps, project-context drafts, lifecycle evidence, context evidence, run state, raw JSON maps, caches, profiles, or temporary fixtures; report skipped write steps instead.",
                "",
                "## Phase: scan",
                "",
                "- [ ] Read: `WORKFLOW.md`, `module.json`, and project guidance files.",
                "  Do: collect deterministic file, manifest, command, symbol, and import facts.",
                "  Write: no files in check mode; generated map payloads in write mode.",
                "  Done when: scan facts are available.",
                "  If blocked: report unreadable roots or files.",
                "",
                "## Phase: project-context",
                "",
                "- [ ] Read: compact navigation Markdown and `docs/project/project-context.md` when present; keep raw navigation JSON such as `handoff.json` and `staleness.json` inside deterministic commands.",
                "  Do: check that project purpose, technologies, run commands, validation commands, folder structure, generated files, external-service boundaries, and Mermaid diagrams are confirmed.",
                "  Write: `docs/project/project-context.md` when missing, or `artifacts/maps/PROJECT_CONTEXT_DRAFT.md` when an existing context must not be overwritten.",
                "  Done when: the project context is reviewed or unresolved facts are explicit.",
                "  If blocked: stop implementation work and record the missing project facts.",
                "",
                "## Phase: write",
                "",
                "- [ ] Read: scan output.",
                "  Do: write map outputs under `artifacts/maps/`.",
                "  Write: `NAVIGATION.md`, `HANDOFF.md`, `TECHNICAL_CONTEXT.md`, `CONVENTIONS.md`, plus tool-only raw JSON indexes `handoff.json` and `staleness.json`.",
                "  Done when: all declared outputs are present.",
                "  If blocked: keep the failing path and command output.",
                "",
                "## Phase: check",
                "",
                "- [ ] Read: check command status; compare committed raw navigation JSON and freshly generated outputs inside the tool.",
                "  Do: compare expected outputs with committed files.",
                "  Write: status only unless `--write` is provided.",
                "  Done when: stale outputs and source changes are explicit.",
                "  If blocked: report the failing path and stop before trusting stale maps.",
                "",
                "## Stop Rules",
                "",
                "- Stop before reading suspected sensitive local values.",
                "- Stop before claiming maps are fresh when `check` reports stale outputs.",
                "",
                "## Completion Contract",
                "",
                "Report target root, mode, generated or checked map paths, project-context status, stale source changes, skipped files, failed commands, and remaining navigation risk.",
                "",
            ]
        ),
    }


def copy_updater_files(target: Path) -> list[str]:
    written: list[str] = []
    script_target = target / "automations" / "navigation" / "scripts"
    script_target.mkdir(parents=True, exist_ok=True)
    sources = {
        "navigation_core.py": SCRIPT_DIR / "navigation_core.py",
        "update_navigation.py": SCRIPT_DIR / "update_navigation.py",
        "project_context.py": SCRIPT_DIR / "project_context.py",
        "module_command.py": (
            SCRIPT_DIR.parents[2]
            / "skill-manager"
            / "scripts"
            / "module_command.py"
        ),
    }
    for name, source in sources.items():
        destination = script_target / name
        if write_text_if_changed(destination, source.read_text(encoding="utf-8")):
            written.append(f"automations/navigation/scripts/{name}")
    return written


def write_text_if_changed(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8").replace("\r\n", "\n") == text:
        return False
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def install_navigation_workflow(target: Path, *, write: bool = False, max_files: int = 5000) -> dict[str, Any]:
    target = target.expanduser().resolve()
    files = workflow_files()
    map_outputs, scan = navigation_core.build_outputs(target, max_files=max_files)
    written: list[str] = []
    if write:
        for relative, text in files.items():
            path = target / relative
            if write_text_if_changed(path, text):
                written.append(relative)
        written.extend(copy_updater_files(target))
        map_outputs, scan = navigation_core.build_outputs(target, max_files=max_files)
        written.extend(navigation_core.write_outputs(target, map_outputs))
    return {
        "schema_version": navigation_core.SCHEMA_VERSION,
        "tool": navigation_core.TOOL_NAME,
        "ok": bool(scan.get("ok")),
        "status": "installed" if write else "dry-run",
        "target": str(target),
        "written": written,
        "checks": [
            "navigation workflow files prepared",
            "self-contained updater prepared",
            "dry-run builds generated payloads in memory unless --write is set",
        ],
        "skipped": scan.get("skipped", []),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="read target project root")
    parser.add_argument("--write", action="store_true", help="write the navigation workflow and generated maps")
    parser.add_argument("--max-files", type=int, default=5000)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", dest="output_format")
    return parser


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Navigation Workflow Install",
        "",
        f"- Target: `{report['target']}`",
        f"- Status: {report['status']}",
        "",
        "## Written",
        "",
    ]
    written = report.get("written", [])
    lines.extend(f"- `{item}`" for item in written) if written else lines.append("- None; dry run built generated payloads in memory.")
    return "\n".join(lines)


def main() -> int:
    navigation_core.require_supported_python()
    args = build_parser().parse_args()
    report = install_navigation_workflow(
        Path(args.target),
        write=args.write,
        max_files=args.max_files,
    )
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
