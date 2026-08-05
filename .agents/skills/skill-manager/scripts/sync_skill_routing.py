#!/usr/bin/env python3
"""Generate accepted-skill routing and registry artifacts for this repository."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

LOCAL_AI_HELPER_SCRIPTS = Path(__file__).resolve().parents[2] / "local-ai-helper" / "scripts"
if str(LOCAL_AI_HELPER_SCRIPTS) not in sys.path:
    sys.path.append(str(LOCAL_AI_HELPER_SCRIPTS))

import skill_manager_common as common
import local_ai_routing
from repo_support import repo_policy

ROUTING_VERSION = 4
CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Documentation And Diagrams",
        (
            "mermaid",
            "diagram",
            "project context",
            "context files",
            "repo brief",
        ),
    ),
    (
        "Security",
        (
            "security",
            "threat",
            "vulnerab",
            "pentest",
            "penetration",
            "auth",
        ),
    ),
    (
        "Architecture And Engineering",
        (
            "architecture",
            "backend",
            "frontend",
            "refactor",
            "clean code",
            "dotnet",
            "aspnet",
        ),
    ),
    (
        "Skill Maintenance",
        (
            "skill-manager",
            "candidate",
            "promot",
            "rewrite",
            "skill design",
            "skill folder",
            "version",
            "upgrade",
        ),
    ),
    (
        "Workflow Management",
        (
            "workflow-manager",
            "workflow module",
            "automation workflow",
            "automations/",
            "WORKFLOW.md",
            "module.json",
        ),
    ),
    (
        "Ticket And Intake",
        (
            "azure-devops-ticket",
            "work item",
            "ticket intake",
            "ticket folder",
            "ticket-info",
            "attachment manifest",
            "attachments downloaded",
        ),
    ),
    (
        "Web And Browser Automation",
        (
            "playwright",
            "browser",
            "screenshot",
            "webapp",
            "e2e",
            "ui test",
        ),
    ),
    (
        "Documents And Office",
        (
            "pdf",
            "docx",
            "pptx",
            "xlsx",
            "spreadsheet",
            "slide",
            "document",
        ),
    ),
    (
        "Data And Research",
        (
            "data",
            "analytics",
            "scientific",
            "sql",
            "infographic",
            "research",
        ),
    ),
    (
        "AI Agents",
        (
            "agent",
            "mcp",
            "prompt",
            "embedding",
            "llm",
            "orchestration",
        ),
    ),
    (
        "Design And Media",
        (
            "design",
            "brand",
            "canvas",
            "asset",
        ),
    ),
)

PRIMARY_OWNER_CATEGORIES = {
    "document-artifacts": "Documents And Office",
    "project-context-generator": "Documentation And Diagrams",
    "repo-navigation": "Documentation And Diagrams",
}


def default_root() -> Path:
    return Path(__file__).resolve().parents[4]


def discover_skill_dirs(root: Path) -> list[Path]:
    skills_root = root / ".agents" / "skills"
    if not skills_root.exists():
        return []
    return [
        child
        for child in sorted(skills_root.iterdir(), key=lambda item: item.name.lower())
        if child.is_dir() and (child / "SKILL.md").exists()
    ]


def infer_category(name: str, description: str, summary: str) -> str:
    normalized_name = name.lower()
    if normalized_name in PRIMARY_OWNER_CATEGORIES:
        return PRIMARY_OWNER_CATEGORIES[normalized_name]
    if normalized_name == "azure-devops-ticket-intake":
        return "Ticket And Intake"
    if normalized_name == "dotnet-security-review":
        return "Security"
    if normalized_name.startswith("dotnet-"):
        return "Architecture And Engineering"
    haystack = f"{name} {description} {summary}".lower()
    for category, terms in CATEGORY_RULES:
        if any(term in haystack for term in terms):
            return category
    return "General"


def allowed_categories() -> list[str]:
    categories = ["General"]
    for category, _terms in CATEGORY_RULES:
        if category not in categories:
            categories.append(category)
    return categories


def should_accept_local_ai_category(skill: dict[str, Any], category: str) -> bool:
    """Keep deterministic owner categories when risk words distort the purpose."""
    current = str(skill.get("category", ""))
    name = str(skill.get("name", "")).lower()
    description = str(skill.get("description", "")).lower()
    summary = str(skill.get("summary", "")).lower()
    haystack = f"{name} {description} {summary}"
    if category == "Security" and current == "Ticket And Intake":
        return False
    if category == "Security" and any(
        term in haystack
        for term in (
            "azure-devops-ticket",
            "ticket intake",
            "work item",
            "attachment manifest",
        )
    ):
        return False
    return True


def as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def script_names(skill_dir: Path) -> list[str]:
    scripts = skill_dir / "scripts"
    if not scripts.exists():
        return []
    return [
        path.name
        for path in sorted(scripts.glob("*.py"), key=lambda item: item.name.lower())
        if path.is_file()
    ]


def has_files(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    return any(path.is_file() for path in folder.rglob("*"))


def analyze_skill(
    skill_dir: Path, max_files: int, max_text_files: int
) -> dict[str, Any]:
    import analyze_location

    return analyze_location.analyze_target(
        str(skill_dir),
        skill_dir.resolve(),
        max_files=max_files,
        max_text_files=max_text_files,
    )


def build_skill_entry(
    root: Path,
    skill_dir: Path,
    max_files: int,
    max_text_files: int,
    deep: bool = False,
) -> dict[str, Any]:
    name, description = common.extract_frontmatter_description(skill_dir / "SKILL.md")
    if not name:
        raise ValueError(f"{common.relative(root, skill_dir / 'SKILL.md')} is missing a name.")
    if name != skill_dir.name:
        raise ValueError(
            f"{common.relative(root, skill_dir / 'SKILL.md')} name '{name}' "
            f"must match folder name '{skill_dir.name}'."
        )
    if not description:
        raise ValueError(
            f"{common.relative(root, skill_dir / 'SKILL.md')} is missing a description."
        )

    manifest, manifest_path, manifest_error = common.load_skill_manifest_with_path(skill_dir)
    if manifest_error or manifest is None:
        raise ValueError(
            f"{common.relative(root, manifest_path)} is required: {manifest_error}"
        )

    analysis = analyze_skill(skill_dir, max_files, max_text_files) if deep else {}
    manifest_dependencies = common.manifest_dependency_labels(manifest)
    dependencies = manifest_dependencies or as_string_list(analysis.get("dependencies"))
    scripts = as_string_list(analysis.get("scripts")) if deep else script_names(skill_dir)
    detected_risks: list[str] = []
    detected_risks.extend(as_string_list(analysis.get("network_signals")))
    detected_risks.extend(as_string_list(analysis.get("credential_signals")))
    for path in as_string_list(analysis.get("disallowed_scripts")):
        detected_risks.append(f"Disallowed script: `{path}`")

    declared_risks = common.manifest_risk_flags(manifest)
    risk_profile = common.manifest_risk_profile(manifest)
    quality = common.routing_example_counts(manifest)
    compatibility = manifest.get("compatibility") if isinstance(manifest, dict) else {}
    if not isinstance(compatibility, dict):
        compatibility = {}

    summary = str(manifest.get("summary") or description)
    skill_words = common.word_count(common.read_text(skill_dir / "SKILL.md"))
    return {
        "name": name,
        "folder": common.relative(root, skill_dir),
        "kind": str(manifest.get("kind") or "skill"),
        "manifest_path": manifest_path.name,
        "category": infer_category(name, description, summary),
        "description": description,
        "summary": summary,
        "version": str(manifest.get("version", "")),
        "manifest_schema_version": manifest.get("schema_version"),
        "status": str(manifest.get("status", "accepted")),
        "compatibility": compatibility,
        "dependencies": dependencies,
        "has_scripts": bool(scripts),
        "has_docs": has_files(skill_dir / "docs"),
        "has_assets": has_files(skill_dir / "assets"),
        "declared_risk_flags": declared_risks,
        "risk_profile": risk_profile,
        "detected_risk_signals": sorted(set(detected_risks)),
        "local_ai": common.local_ai_use_case_summary(manifest),
        "quality": quality,
        "scan_mode": "deep" if deep else "fast",
        "budget": {
            "skill_md_words": skill_words,
            "skill_md_status": repo_policy.skill_word_status(root, skill_words),
        },
    }


def build_registry_data(
    root: Path,
    max_files: int,
    max_text_files: int,
    deep: bool = False,
    use_local_ai: bool = True,
    check_local_ai: bool = False,
) -> dict[str, Any]:
    skills = [
        build_skill_entry(root, skill_dir, max_files, max_text_files, deep=deep)
        for skill_dir in discover_skill_dirs(root)
    ]
    skills.sort(key=lambda item: item["name"])
    if use_local_ai:
        apply_local_ai_routes(root, skills, check=check_local_ai)
    return {
        "version": ROUTING_VERSION,
        "source_root": ".agents/skills",
        "scan_mode": "deep" if deep else "fast",
        "skills": skills,
    }


def local_ai_item_for_skill(skill: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": skill["name"],
        "name": skill["name"],
        "task": "skill-routing",
        "category": skill["category"],
        "description": skill["description"],
        "summary": skill["summary"],
        "source_paths": [
            f"{skill['folder']}/SKILL.md",
            f"{skill['folder']}/{skill.get('manifest_path', 'module.json')}",
        ],
    }


def apply_local_ai_routes(
    root: Path, skills: list[dict[str, Any]], *, check: bool
) -> dict[str, Any]:
    if not skills:
        return {"status": "disabled", "check_failed": False, "issues": [], "items": {}}
    items = [local_ai_item_for_skill(skill) for skill in skills]
    result = local_ai_routing.route_items(
        root,
        "skill-routing",
        items,
        allowed_categories=allowed_categories(),
        check=check,
    )
    routed_items = result.get("items", {})
    if isinstance(routed_items, dict):
        for skill in skills:
            routed = routed_items.get(skill["name"])
            if not isinstance(routed, dict) or not routed.get("accepted"):
                continue
            fields = routed.get("fields", {})
            if not isinstance(fields, dict):
                continue
            category = fields.get("category")
            if (
                isinstance(category, str)
                and category in allowed_categories()
                and should_accept_local_ai_category(skill, category)
            ):
                skill["category"] = category
    return result


def escape_markdown_cell(value: object) -> str:
    text = str(value).replace("\n", " ").strip()
    return text.replace("|", "\\|")


def compact_routing_text(value: object, limit: int) -> str:
    text = " ".join(str(value).replace("\n", " ").split()).strip()
    if text.lower().startswith("use when "):
        text = text[9:].strip()
    lower = text.lower()
    for marker in (" including ", "; "):
        index = lower.find(marker)
        if index > 0:
            text = text[:index].strip(" ,.;")
            lower = text.lower()
    if text.endswith("/"):
        text = text[:-1].rstrip()
    if text:
        text = text[0].upper() + text[1:]
    if len(text) <= limit:
        if text.endswith((".", "!", "?")) or len(text) == limit:
            return text
        return f"{text}."
    truncated = text[: limit - 3].rsplit(" ", 1)[0].strip(" ,.;")
    return f"{truncated}..."


def render_markdown(root: Path, data: dict[str, Any]) -> str:
    lines = [
        "<!-- Generated by skill-manager sync_skill_routing.py. Do not edit by hand. -->",
        "",
        "# Skill Routing Index",
        "",
        "Use this file only to choose which skill to open. Open one matching `SKILL.md`; do not load all skills.",
        "",
        "The tool-only `registry.json` is generated from module.json contracts and contains full metadata for scripts and checks.",
        "",
        f"- Source root: `{data['source_root']}`",
        f"- Index schema version: `{data['version']}`",
    ]

    if not data["skills"]:
        lines.extend(["", "No accepted skills found."])
        return "\n".join(lines)

    lines.extend(
        [
            "",
            "| Category | Skill | Use When | Open |",
            "|---|---|---|---|",
        ]
    )
    for skill in sorted(data["skills"], key=lambda item: (item["category"], item["name"])):
        summary_chars = repo_policy.int_value(root, "limits.routing.entry_summary_chars")
        lines.append(
            "| "
            + " | ".join(
                [
                    escape_markdown_cell(skill["category"]),
                    f"`{escape_markdown_cell(skill['name'])}`",
                    escape_markdown_cell(compact_routing_text(skill["description"], summary_chars)),
                    f"`{escape_markdown_cell(skill['folder'])}/SKILL.md`",
                ]
            )
            + " |"
        )

    return "\n".join(lines)


def expected_outputs(root: Path, data: dict[str, Any]) -> dict[Path, str]:
    output_root = root / ".agents"
    return {
        output_root / "routing.md": render_markdown(root, data) + "\n",
        output_root / "registry.json": json.dumps(data, indent=2, sort_keys=True) + "\n",
    }


def sync_skill_routing(
    root: Path,
    check: bool,
    max_files: int,
    max_text_files: int,
    deep: bool = False,
) -> int:
    if check and deep:
        build_registry_data(
            root,
            max_files=max_files,
            max_text_files=max_text_files,
            deep=True,
            use_local_ai=False,
        )
    data = build_registry_data(
        root,
        max_files=max_files,
        max_text_files=max_text_files,
        deep=False if check else deep,
        use_local_ai=False,
    )
    outputs = expected_outputs(root, data)

    if check:
        stale: list[Path] = []
        for path, expected in outputs.items():
            if not path.exists():
                stale.append(path)
                continue
            actual = path.read_text(encoding="utf-8").replace("\r\n", "\n")
            if actual != expected:
                stale.append(path)
        if stale:
            for path in stale:
                print(f"ERROR: {common.relative(root, path)} is missing or stale.", file=sys.stderr)
            print(
                "Strict read-only: report stale generated skill routing/registry; do not run write-mode sync.",
                file=sys.stderr,
            )
            print(
                "Write-mode fix: python -B .agents/manage.py sync-skill-routing",
                file=sys.stderr,
            )
            return 1
        print("Accepted-skill routing and registry are in sync.")
        return 0

    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"Generated {common.relative(root, path)}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="repository root; defaults to the script parent repository")
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if generated routing Markdown or registry JSON is stale",
    )
    parser.add_argument("--max-files", type=int, default=2500)
    parser.add_argument("--max-text-files", type=int, default=400)
    parser.add_argument(
        "--deep",
        action="store_true",
        help="include analyzer-derived risk/dependency signals; slower for large skill sets",
    )
    return parser


def main() -> int:
    common.require_supported_python()
    args = build_parser().parse_args()
    root = Path(args.root).expanduser().resolve() if args.root else default_root()
    return sync_skill_routing(
        root,
        check=args.check,
        max_files=args.max_files,
        max_text_files=args.max_text_files,
        deep=args.deep,
    )


if __name__ == "__main__":
    raise SystemExit(main())
