"""Deterministic routing explanations for skills and workflows."""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path

import module_contract_v3
from repo_support import repo_common as repo


TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.-]*")
ALIAS_GROUPS = (
    {"a11y", "accessibility", "accessible"},
    {"ado", "azure-devops", "azure", "ticket", "work-item", "workitem"},
    {"ci", "cd", "pipeline", "pipelines", "github-actions", "actions", "build"},
    {"deck", "ppt", "pptx", "powerpoint", "presentation", "presentations", "slides"},
    {"doc", "docx", "document", "documents", "word"},
    {"embedding", "embeddings", "vector", "vectors"},
    {"exe", "executable", "binary", "portable"},
    {"pdf", "attachment", "attachments"},
)
ROUTE_OWNER_KEYWORDS = {
    "local-ai-helper": (
        "local ai llama model models vector vectors embedding embeddings "
        "vision broker cache"
    ),
    "skill-manager": (
        "skill skills route routing which-skill command commands token budget regression portable "
        "executable exe binary manifest claude adapter adapters"
    ),
    "workflow-manager": "automation automations workflow workflows plan run checkpoint module contract",
    "disciplined-change-workflow": (
        "disciplined change larger repo lifecycle dogfood smoke smoke-test workflow run runs "
        "checkpoint handoff context finish evidence validation"
    ),
    "candidate-import-workflow": (
        "candidate candidates import imports temporary temp staged folder folders source sources "
        "classify classification take rewrite reject cleanup clean proof promote accepted owner owners "
        "skill skills workflow workflows scripts generated external provenance license"
    ),
    "feedback-improvement-workflow": (
        "feedback failure failures local ledger action plan action-plan improvement improvements "
        "validation validate regression prevention processed truncate clear summary export candidates "
        "candidate repeated recurring not-actionable follow-up workflow"
    ),
    "user-story-workflow": (
        "story user-story feature feature-request enhancement implement implementation add build create "
        "endpoint acceptance criteria new behavior functionality azure-devops work-item ticket"
    ),
    "bug-ticket-workflow": (
        "bug defect regression crash failure reproduce reproduction observed expected root-cause "
        "fix repair issue azure-devops work-item ticket"
    ),
    "local-ai-benchmark-workflow": (
        "local ai model runtime gguf llama llama.cpp llama-server server speculative decoding "
        "mtp ngram n-gram qwen glm nemotron code-generation embedding retrieval "
        "vision benchmark benchmarks"
    ),
    "dotnet-legacy": (
        "dotnet net .net framework legacy classic asp.net mvc web forms wcf old csproj "
        "non-sdk packages.config binding redirect redirects app.config web.config com gac "
        "iis iis-hosted visual studio msbuild vstest winforms wpf windows service "
        "maintain maintenance maintain-in-place modernization migration"
    ),
    "dotnet-framework-migration": (
        "dotnet net .net framework migration migrate migrating legacy app application solution "
        "binding redirects app.config web.config packages.config compatibility baseline inventory "
        "rollback validation evidence modernize modernization target framework"
    ),
    "dotnet-upgrade": (
        "dotnet net .net modern upgrade upgrades upgraded app application solution target version "
        "target-framework targetframework tfm sdk runtime package packages dependency dependencies "
        "nuget feed feeds source sources package-resolution dependency-resolution microsoft notes "
        "changelog compatibility baseline validation evidence rollback"
    ),
    "repo-navigation": (
        "navigation map maps handoff handoffs capsule capsules project-context project context "
        "draft staleness stale refresh generated deterministic repository read-order search retrieval "
        "repository-search ripgrep rg grep"
    ),
    "reference-refresh": (
        "reference references reference-refresh external-reference-manager external git repository repositories manifest manifests "
        "pin pins pinned commit commits card cards compact refresh no-fetch fetch clone mirror mirrors "
        "local offline network evidence"
    ),
}
ROUTE_OWNER_TERM_PAIRS = {
    "workflow-manager": ({"workflow", "plan"},),
    "disciplined-change-workflow": (
        {"lifecycle", "dogfood"},
        {"lifecycle", "smoke"},
        {"workflow", "dogfood"},
    ),
    "candidate-import-workflow": (
        {"candidate", "import"},
        {"candidate", "rewrite"},
        {"candidate", "cleanup"},
        {"take", "rewrite"},
        {"rewrite", "reject"},
    ),
    "feedback-improvement-workflow": (
        {"failure", "feedback"},
        {"feedback", "improvement"},
        {"action", "plan"},
        {"feedback", "clear"},
        {"feedback", "export"},
    ),
    "local-ai-benchmark-workflow": (
        {"local", "ai"},
        {"llama", "server"},
        {"code", "generation"},
        {"speculative", "benchmark"},
    ),
    "dotnet-legacy": (
        {"net", "framework"},
        {"dotnet", "framework"},
        {"classic", "asp.net"},
        {"wcf", "packages.config"},
        {"old", "csproj"},
        {"binding", "redirects"},
        {"com", "gac"},
    ),
    "diagram-review-workflow": ({"diagram", "workflow"},),
    "dotnet-framework-migration": (
        {"framework", "migration"},
        {"framework", "migrate"},
        {"dotnet", "framework"},
        {"net", "framework"},
        {"binding", "redirect"},
        {"binding", "redirects"},
    ),
    "dotnet-upgrade": (
        {"dotnet", "upgrade"},
        {"net", "upgrade"},
        {"package", "resolution"},
        {"dependency", "resolution"},
        {"microsoft", "notes"},
        {"target", "version"},
    ),
    "navigation": (
        {"navigation", "maps"},
        {"navigation", "map"},
        {"stale", "maps"},
        {"staleness", "maps"},
        {"refresh", "maps"},
        {"handoff", "navigation"},
    ),
    "reference-refresh": (
        {"reference", "refresh"},
        {"external", "manager"},
        {"external", "reference"},
        {"pinned", "manifest"},
        {"pin", "commit"},
        {"git", "reference"},
        {"network", "fetch"},
    ),
}
STORY_INTENT_TERMS = {
    "acceptance",
    "criteria",
    "endpoint",
    "enhancement",
    "feature",
    "functionality",
    "implement",
    "implementation",
    "story",
    "user-story",
}
BUG_INTENT_TERMS = {
    "bug",
    "crash",
    "defect",
    "failure",
    "fix",
    "regression",
    "repro",
    "reproduce",
    "reproduction",
}
BUG_ACTION_TERMS = BUG_INTENT_TERMS - {"regression"}
BUG_SUBJECT_TERMS = BUG_INTENT_TERMS - {"fix"}
STORY_ACTION_TERMS = STORY_INTENT_TERMS - {"acceptance", "criteria", "feature"}
INTAKE_INTENT_TERMS = {"attachment", "attachments", "comment", "comments", "download", "fetch", "import", "ingest", "intake"}
IMPLEMENTATION_INTENT_TERMS = {"add", "build", "change", "create", "develop", "fix", "implement", "implementation"}
DISCIPLINED_CHANGE_READ_ONLY_INTENT_TERMS = {"disciplined", "discipline", "dogfood", "lifecycle", "larger", "smoke", "smoke-test"}
CANDIDATE_IMPORT_CONTEXT_TERMS = {
    "accepted",
    "candidate",
    "classify",
    "classification",
    "clean",
    "cleanup",
    "external",
    "folder",
    "generated",
    "harnes",
    "harness",
    "license",
    "owner",
    "promote",
    "proof",
    "provenance",
    "reject",
    "rewrite",
    "script",
    "source",
    "staged",
    "take",
    "temp",
    "temporary",
}
CANDIDATE_IMPORT_TRIGGER_TERMS = {
    "candidate",
    "classify",
    "classification",
    "clean",
    "cleanup",
    "folder",
    "promote",
    "reject",
    "rewrite",
    "script",
    "staged",
    "take",
    "temp",
    "temporary",
}
FEEDBACK_IMPROVEMENT_CONTEXT_TERMS = {
    "action",
    "action-plan",
    "candidate",
    "candidates",
    "clear",
    "export",
    "failure",
    "failures",
    "feedback",
    "follow-up",
    "improvement",
    "improvements",
    "ledger",
    "local",
    "plan",
    "prevention",
    "processed",
    "recurring",
    "regression",
    "repeated",
    "summary",
    "truncate",
    "validation",
    "workflow",
}
FEEDBACK_IMPROVEMENT_TRIGGER_TERMS = {
    "action-plan",
    "clear",
    "export",
    "feedback",
    "ledger",
    "summary",
    "truncate",
}
FEEDBACK_IMPROVEMENT_ANCHOR_TERMS = {
    "action-plan",
    "clear",
    "export",
    "feedback",
    "ledger",
    "summary",
    "truncate",
}
DIAGRAM_REVIEW_CONTEXT_TERMS = {
    "azure",
    "diagram",
    "diagrams",
    "devops",
    "documentation",
    "evidence",
    "markdown",
    "materialization",
    "mermaid",
    "mmd",
    "render",
    "svg",
    "validation",
    "wiki",
}
DIAGRAM_REVIEW_TRIGGER_TERMS = {
    "dogfood",
    "evidence",
    "materialization",
    "validation",
    "workflow",
}
DOTNET_FRAMEWORK_MIGRATION_CONTEXT_TERMS = {
    "app",
    "application",
    "baseline",
    "binding",
    "compatibility",
    "config",
    "dotnet",
    "evidence",
    "framework",
    "legacy",
    "migrate",
    "migration",
    "modernization",
    "net",
    "package",
    "packages",
    "redirect",
    "redirects",
    "rollback",
    "solution",
    "target",
    "validation",
}
DOTNET_FRAMEWORK_MIGRATION_TRIGGER_TERMS = {
    "binding",
    "compatibility",
    "framework",
    "legacy",
    "migrate",
    "migration",
    "modernization",
    "redirect",
    "redirects",
}
DOTNET_UPGRADE_CONTEXT_TERMS = {
    "app",
    "application",
    "baseline",
    "changelog",
    "compatibility",
    "dependency",
    "dependencies",
    "dotnet",
    "evidence",
    "feed",
    "feeds",
    "microsoft",
    "modern",
    "net",
    "notes",
    "nuget",
    "package",
    "packages",
    "resolution",
    "rollback",
    "runtime",
    "sdk",
    "solution",
    "source",
    "sources",
    "target",
    "tfm",
    "upgrade",
    "upgraded",
    "upgrades",
    "validation",
    "version",
}
DOTNET_UPGRADE_TRIGGER_TERMS = {
    "changelog",
    "dependency",
    "dependencies",
    "notes",
    "nuget",
    "package",
    "packages",
    "resolution",
    "target",
    "upgrade",
    "upgraded",
    "upgrades",
    "version",
}
DOTNET_UPGRADE_FRAMEWORK_HANDOFF_TERMS = {
    "binding",
    "framework",
    "legacy",
    "migrate",
    "migration",
    "modernization",
    "redirect",
    "redirects",
}
NAVIGATION_CONTEXT_TERMS = {
    "capsule",
    "capsules",
    "context",
    "deterministic",
    "draft",
    "generated",
    "handoff",
    "handoffs",
    "map",
    "maps",
    "navigation",
    "project",
    "project-context",
    "read-order",
    "refresh",
    "repository",
    "stale",
    "staleness",
}
NAVIGATION_TRIGGER_TERMS = {
    "handoff",
    "handoffs",
    "map",
    "maps",
    "navigation",
    "project-context",
    "refresh",
    "stale",
    "staleness",
}
REFERENCE_REFRESH_CONTEXT_TERMS = set(
    "card cards clone commit commits compact evidence external external-reference-manager fetch git local manager "
    "manifest manifests mirror mirrors network offline pin pinned pins reference reference-refresh references "
    "refresh repository repositories".split()
)
REFERENCE_REFRESH_TRIGGER_TERMS = set(
    "card cards commit commits external external-reference-manager fetch git manager manifest manifests mirror mirrors "
    "network offline pin pinned pins reference reference-refresh references refresh".split()
)
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "check",
    "for",
    "from",
    "how",
    "in",
    "inspect",
    "is",
    "it",
    "of",
    "on",
    "only",
    "or",
    "review",
    "the",
    "to",
    "use",
    "when",
    "with",
}
GENERIC_MATCH_TERMS = {
    "architecture",
    "automation",
    "automations",
    "behavior",
    "change",
    "changes",
    "command",
    "commands",
    "compact",
    "context",
    "deterministic",
    "diagram",
    "diagrams",
    "evidence",
    "evidence.",
    "fresh",
    "generated",
    "idea",
    "ideas",
    "improvement",
    "improvements",
    "maintenance",
    "offline",
    "packet",
    "plan",
    "project",
    "rank",
    "ranked",
    "read",
    "reference",
    "references",
    "repo",
    "search",
    "skill",
    "skills",
    "status",
    "test",
    "tests",
    "workflow",
    "workflows",
}
ACTIVATION_GENERIC_TERMS = {
    "automation",
    "automations",
    "harnes",
    "harness",
    "skill",
    "skills",
    "workflow",
    "workflows",
}
READ_ONLY_REQUEST_PHRASES = (
    "read-only",
    "read only",
    "do not run",
    "do not start",
    "don't run",
    "don't start",
    "dont run",
    "dont start",
    "do not edit",
    "don't edit",
    "dont edit",
    "do not change",
    "don't change",
    "dont change",
    "no start",
    "no-start",
    "no changes",
    "no edits",
    "without starting",
    "report only",
    "report ranked ideas",
    "ranked ideas only",
    "rank ideas only",
    "next best feature",
    "next best features",
    "next best improvement",
    "next best improvements",
    "what would the next",
    "what are the next",
    "what should",
    "should we",
    "worth pursuing",
    "recommend",
    "prioritize",
    "just tell me",
    "only tell me",
    "report the route",
    "tell me the route",
)
CROSS_CUTTING_AUDIT_INTENT_TERMS = {"assess", "assessment", "audit"}
CROSS_CUTTING_AUDIT_SUBJECT_TERMS = {"harness", "repo", "repository", "roadmap"}
CROSS_CUTTING_AUDIT_AREA_TERMS = {
    "context",
    "navigation",
    "onboarding",
    "project-context",
    "readiness",
    "registry",
    "routing",
    "skill",
    "skills",
    "sync",
    "synchronization",
    "workflow",
    "workflows",
}
LOCAL_AI_AVOIDANCE_PHRASES = (
    "avoid local ai",
    "avoid local-ai",
    "without local ai",
    "without local-ai",
    "no local ai",
    "no local-ai",
    "do not use local ai",
    "do not use local-ai",
    "don't use local ai",
    "don't use local-ai",
    "dont use local ai",
    "dont use local-ai",
    "avoid model setup",
    "without model setup",
    "no model setup",
    "avoid embeddings",
    "without embeddings",
    "no embeddings",
)
WORKFLOW_SUBJECT_TERMS = {
    "automation",
    "automations",
    "lifecycle",
    "run",
    "runs",
    "smoke",
    "smoke-test",
    "workflow",
    "workflows",
}
STRICT_READ_ONLY_REQUEST_PHRASES = (
    "strict read-only",
    "strict read only",
    "no-temp",
    "no temp",
    "no-write",
    "no write",
    "no-network",
    "no network",
    "no-profile",
    "no profile",
)
STRICT_READ_ONLY_REQUEST_TERMS = {
    "strict",
    "offline",
    "no-temp",
    "no-write",
    "no-network",
    "no-profile",
}


def workflow_strict_read_only_commands(root: Path, owner: str) -> list[dict[str, object]]:
    manifest_path = root / "automations" / owner / "module.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    values = data.get("strict_read_only_commands") if isinstance(data, dict) else None
    if not isinstance(values, list):
        return []
    if data.get("schema_version") != 3:
        return []
    command_specs = data.get("commands")
    if not isinstance(command_specs, list):
        return []
    by_id: dict[str, dict[str, object]] = {}
    duplicates: set[str] = set()
    for command in command_specs:
        if not isinstance(command, dict):
            continue
        command_id = command.get("id")
        argv = command.get("argv")
        if not (
            isinstance(command_id, str)
            and isinstance(argv, list)
            and argv
            and all(isinstance(item, str) and item for item in argv)
        ):
            continue
        if command_id in by_id:
            duplicates.add(command_id)
        by_id[command_id] = copy.deepcopy(command)
    resolved: list[dict[str, object]] = []
    for item in values:
        if not isinstance(item, str) or item in duplicates:
            continue
        command = by_id.get(item)
        if command:
            resolved.append(command)
    return resolved


def strict_read_only_request(text: str, query_terms: set[str]) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in STRICT_READ_ONLY_REQUEST_PHRASES) or bool(
        query_terms & STRICT_READ_ONLY_REQUEST_TERMS
    )


def read_only_workflow_next_commands(
    root: Path,
    owner: str,
    query_terms: set[str],
    query: str,
) -> list[dict[str, object]]:
    if strict_read_only_request(query, query_terms):
        strict_commands = workflow_strict_read_only_commands(root, owner)
        if strict_commands:
            return strict_commands
    if query_terms & {"dogfood", "lifecycle", "smoke", "smoke-test"}:
        argv = [
            "python",
            "-B",
            ".agents/manage.py",
            "workflow",
            "smoke",
            "--name",
            owner,
            "--dry-run",
            "--summary",
            "--compact",
            "--format",
            "json",
        ]
        return [
            {
                "id": module_contract_v3.command_id_for_argv(argv, prefix="read-only"),
                "argv": argv,
                "timeout_seconds": 300,
                "working_directory": "repository",
                "effects": [],
            }
        ]
    return []


def read_only_workflow_next_command(
    root: Path,
    owner: str,
    query_terms: set[str],
    query: str,
) -> tuple[str, list[dict[str, object]]]:
    next_commands = read_only_workflow_next_commands(root, owner, query_terms, query)
    if next_commands:
        return module_contract_v3.command_display(next_commands[0]), next_commands
    return f"Report selected workflow `{owner}`; do not start, resume, or finish a retained workflow run.", []


def normalized_tokens(text: str, *, expand_aliases: bool) -> set[str]:
    normalized: set[str] = set()
    for raw_item in TOKEN_RE.findall(text.lower()):
        candidates = [raw_item]
        if any(separator in raw_item for separator in ("-", "_", ".")):
            candidates.extend(part for part in re.split(r"[-_.]+", raw_item) if part)
        for item in candidates:
            if item in STOP_WORDS or len(item) <= 2:
                continue
            normalized.add(item)
            if item.endswith("s") and len(item) > 4 and item != "devops" and item[:-1] not in STOP_WORDS:
                normalized.add(item[:-1])
            if item.endswith("ing") and len(item) > 6 and item[:-3] not in STOP_WORDS:
                normalized.add(item[:-3])
    if expand_aliases:
        for group in ALIAS_GROUPS:
            if normalized & group:
                normalized.update(group)
    return normalized


def read_only_request(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in READ_ONLY_REQUEST_PHRASES)


def local_ai_avoidance_request(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in LOCAL_AI_AVOIDANCE_PHRASES)


def tokens(text: str) -> set[str]:
    return normalized_tokens(text, expand_aliases=True)


def display_tokens(text: str) -> set[str]:
    return normalized_tokens(text, expand_aliases=False)


def query_concepts(text: str) -> set[str]:
    return {
        item
        for item in TOKEN_RE.findall(text.lower())
        if item not in STOP_WORDS and len(item) > 2
    }


def parse_markdown_table(path: Path, kind: str) -> list[dict[str, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, str]] = []
    for line in lines:
        if not line.startswith("|") or "---" in line:
            continue
        cells = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        if kind == "skill" and len(cells) >= 4 and cells[0] != "Category":
            rows.append(
                {
                    "kind": "skill",
                    "name": cells[1],
                    "category": cells[0],
                    "use_when": cells[2],
                    "open": cells[3].strip("`"),
                }
            )
        if kind == "workflow" and len(cells) >= 4 and cells[0] != "Workflow":
            rows.append(
                {
                    "kind": "workflow",
                    "name": cells[0],
                    "category": "Workflow",
                    "use_when": cells[1],
                    "open": cells[2].strip("`"),
                    "contract": cells[3].strip("`"),
                }
            )
    return rows


def routing_metadata_values(row: dict[str, object], keys: tuple[str, ...]) -> list[str]:
    metadata = row.get("routing")
    if not isinstance(metadata, dict):
        return []
    values: list[str] = []
    for key in keys:
        raw = metadata.get(key)
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, list):
            values.extend(item for item in raw if isinstance(item, str) and item.strip())
    return values


def routing_metadata_terms(row: dict[str, object]) -> set[str]:
    terms: set[str] = set()
    for value in routing_metadata_values(row, ("terms", "keywords", "anchors", "phrases")):
        terms.update(concept_variants(value))
    return terms


def canonical_concept(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value.strip().lower())


def concept_variants(value: str) -> set[str]:
    variants: set[str] = set()
    for raw_item in TOKEN_RE.findall(value.lower()):
        item = canonical_concept(raw_item)
        if item in STOP_WORDS or len(item) <= 2:
            continue
        variants.add(item)
        if item.endswith("s") and len(item) > 4 and item != "devops" and item[:-1] not in STOP_WORDS:
            variants.add(item[:-1])
        if item.endswith("ing") and len(item) > 6 and item[:-3] not in STOP_WORDS:
            variants.add(item[:-3])
    for raw_group in ALIAS_GROUPS:
        group = {canonical_concept(item) for item in raw_group}
        if variants & group:
            variants.update(group)
    return variants


def metadata_semantic_key(concept: str) -> str:
    variants = concept_variants(concept)
    for index, raw_group in enumerate(ALIAS_GROUPS):
        group = {canonical_concept(item) for item in raw_group}
        if variants & group:
            return f"alias:{index}"
    canonical = canonical_concept(concept)
    if canonical.endswith("s") and len(canonical) > 4 and canonical != "devops":
        return canonical[:-1]
    if canonical.endswith("ing") and len(canonical) > 6:
        return canonical[:-3]
    return canonical


def metadata_concept_matches(values: list[str], query_concepts: set[str]) -> list[str]:
    metadata_concepts = {
        canonical_concept(item)
        for value in values
        for item in TOKEN_RE.findall(value.lower())
        if concept_variants(item)
    }
    standalone_metadata = {concept for concept in metadata_concepts if "-" not in concept}
    entries: dict[str, dict[str, object]] = {}
    for concept in metadata_concepts:
        parts = [part for part in concept.split("-") if concept_variants(part)]
        if len(parts) > 1 and all(part in standalone_metadata for part in parts):
            continue
        key = metadata_semantic_key(concept)
        entry = entries.setdefault(key, {"concepts": set(), "variants": set()})
        entry["concepts"].add(concept)
        entry["variants"].update(concept_variants(concept))

    query_rows = [
        (canonical_concept(concept), concept_variants(concept))
        for concept in query_concepts
        if concept_variants(concept)
    ]
    simple_query_terms = {
        variant
        for concept, variants in query_rows
        if "-" not in concept
        for variant in variants
    }
    compound_query_terms = {
        variant
        for concept, _variants in query_rows
        if "-" in concept
        for part in concept.split("-")
        for variant in concept_variants(part)
    }
    matches: dict[str, str] = {}
    for key, entry in entries.items():
        concepts = entry["concepts"]
        variants = entry["variants"]
        direct_labels = [concept for concept, query_variants in query_rows if query_variants & variants]
        if direct_labels:
            matches[key] = min(direct_labels, key=lambda item: (len(item), item))
            continue
        compound_metadata = [concept for concept in concepts if "-" in concept]
        if any(
            all(concept_variants(part) & simple_query_terms for part in concept.split("-"))
            for concept in compound_metadata
        ):
            matches[key] = min(compound_metadata, key=lambda item: (len(item), item))
            continue
        simple_metadata = [concept for concept in concepts if "-" not in concept]
        if simple_metadata and variants & compound_query_terms:
            matches[key] = min(simple_metadata, key=lambda item: (len(item), item))
    return sorted(matches.values())


def routing_int(
    row: dict[str, object],
    names: tuple[str, ...],
    default: int,
    *,
    minimum: int = 1,
) -> int:
    metadata = row.get("routing")
    if not isinstance(metadata, dict):
        return default
    for name in names:
        value = metadata.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= minimum:
            return value
    return default


def score_route(
    query_terms: set[str],
    row: dict[str, object],
    *,
    display_query_terms: set[str] | None = None,
    display_query_concepts: set[str] | None = None,
    analysis_only: bool = False,
    avoid_local_ai: bool = False,
) -> tuple[int, list[str]]:
    metadata_terms = routing_metadata_terms(row)
    if metadata_terms:
        concepts = display_query_concepts or display_query_terms or query_terms
        activation_values = routing_metadata_values(row, ("activation_terms",))
        activation_matches = metadata_concept_matches(activation_values, concepts)
        if activation_values and not activation_matches:
            return 0, []
        display_matches = metadata_concept_matches(
            routing_metadata_values(row, ("terms", "keywords", "anchors", "phrases")),
            concepts,
        )
        score = len(display_matches) + (1 if activation_matches else 0)
        if avoid_local_ai and str(row.get("name", "")) in {"local-ai-helper", "local-ai-benchmark-workflow"}:
            score -= 50
        return score, display_matches
    searchable = " ".join(
        [
            str(row.get("name", "")),
            str(row.get("category", "")),
            str(row.get("use_when", "")),
            ROUTE_OWNER_KEYWORDS.get(str(row.get("name", "")), ""),
        ]
    )
    row_terms = tokens(searchable)
    matches = sorted(query_terms & row_terms)
    display_matches = matches
    if display_query_terms is not None:
        display_matches = sorted(display_query_terms & row_terms) or matches
    score = len(matches)
    name_terms = tokens(str(row.get("name", "")))
    name_matches = (query_terms & name_terms) - GENERIC_MATCH_TERMS
    score += 2 * len(name_matches)
    owner_terms = tokens(ROUTE_OWNER_KEYWORDS.get(str(row.get("name", "")), ""))
    owner_specific_matches = (query_terms & owner_terms) - GENERIC_MATCH_TERMS
    score += len(owner_specific_matches)
    for term_pair in ROUTE_OWNER_TERM_PAIRS.get(str(row.get("name", "")), ()):
        if term_pair <= query_terms:
            score += 1
    if len((query_terms & name_terms) - GENERIC_MATCH_TERMS) >= 2 or (
        not analysis_only and len(query_terms & name_terms) >= 2
    ):
        score += 1
    row_name = str(row.get("name", ""))
    if avoid_local_ai and row_name in {"local-ai-helper", "local-ai-benchmark-workflow"}:
        score -= 50
    if (
        row.get("kind") == "workflow"
        and analysis_only
        and "skill" in query_terms
        and not (query_terms & WORKFLOW_SUBJECT_TERMS)
    ):
        score -= 50
    story_intent = query_terms & STORY_INTENT_TERMS
    bug_intent = query_terms & BUG_INTENT_TERMS
    intake_intent = query_terms & INTAKE_INTENT_TERMS
    candidate_import_context = query_terms & CANDIDATE_IMPORT_CONTEXT_TERMS
    feedback_improvement_context = query_terms & FEEDBACK_IMPROVEMENT_CONTEXT_TERMS
    diagram_review_context = query_terms & DIAGRAM_REVIEW_CONTEXT_TERMS
    dotnet_framework_migration_context = query_terms & DOTNET_FRAMEWORK_MIGRATION_CONTEXT_TERMS
    dotnet_upgrade_context = query_terms & DOTNET_UPGRADE_CONTEXT_TERMS
    if row_name == "user-story-workflow":
        score += 8 * len(story_intent)
        if analysis_only and not (story_intent & STORY_ACTION_TERMS):
            score -= 20
        if bug_intent:
            score -= 6 * len(bug_intent)
        if intake_intent and not story_intent:
            score -= 20 * len(intake_intent)
    elif row_name == "bug-ticket-workflow":
        score += 8 * len(bug_intent)
        if analysis_only and not (bug_intent & BUG_ACTION_TERMS):
            score -= 20
        strict_workflow_dogfood = bool(
            (query_terms & (STRICT_READ_ONLY_REQUEST_TERMS | {"dogfood", "lifecycle", "smoke", "smoke-test"}))
            and (query_terms & WORKFLOW_SUBJECT_TERMS)
        )
        if strict_workflow_dogfood and not (bug_intent & BUG_SUBJECT_TERMS):
            score -= 20
        if story_intent:
            score -= 6 * len(story_intent)
        if intake_intent and not bug_intent:
            score -= 20 * len(intake_intent)
        if feedback_improvement_context and not (bug_intent & {"bug", "crash", "defect", "fix", "repro", "reproduce", "reproduction"}):
            score -= 12 * len(feedback_improvement_context)
    elif row_name == "azure-devops-ticket-intake":
        score += 4 * len(intake_intent)
        if (query_terms & IMPLEMENTATION_INTENT_TERMS) and not (query_terms & INTAKE_INTENT_TERMS):
            score -= 6
    elif row_name == "disciplined-change-workflow" and analysis_only:
        if not (query_terms & DISCIPLINED_CHANGE_READ_ONLY_INTENT_TERMS):
            score -= 20
    elif row_name == "candidate-import-workflow":
        if candidate_import_context and (query_terms & CANDIDATE_IMPORT_TRIGGER_TERMS):
            score += 5 * len(candidate_import_context)
            if intake_intent:
                score += 4 * len(intake_intent)
        elif (query_terms & {"external", "source", "sources"}) and not (query_terms & CANDIDATE_IMPORT_TRIGGER_TERMS):
            score -= 20
        elif intake_intent:
            score -= 20 * len(intake_intent)
    elif row_name == "feedback-improvement-workflow":
        feedback_anchor_terms = query_terms & FEEDBACK_IMPROVEMENT_ANCHOR_TERMS
        lone_feedback_request = feedback_anchor_terms == {"feedback"} and len(feedback_improvement_context) == 1
        if (
            feedback_improvement_context
            and feedback_anchor_terms
            and not lone_feedback_request
            and (query_terms & FEEDBACK_IMPROVEMENT_TRIGGER_TERMS)
        ):
            score += 5 * len(feedback_improvement_context)
        elif not feedback_anchor_terms:
            score -= 20
        elif bug_intent and not feedback_improvement_context:
            score -= 10 * len(bug_intent)
    elif row_name == "diagram-review-workflow":
        diagram_subject = query_terms & {"diagram", "diagrams", "mermaid", "mmd", "svg"}
        if diagram_subject and (query_terms & DIAGRAM_REVIEW_TRIGGER_TERMS):
            score += 4 * len(diagram_review_context)
        elif intake_intent and not diagram_subject:
            score -= 20 * len(intake_intent)
    elif row_name == "dotnet-framework-migration":
        if query_terms & DOTNET_FRAMEWORK_MIGRATION_TRIGGER_TERMS:
            score += 4 * len(dotnet_framework_migration_context)
        else:
            score -= 20
    elif row_name == "dotnet-upgrade":
        if (query_terms & {"dotnet", "net"}) and (query_terms & DOTNET_UPGRADE_TRIGGER_TERMS):
            score += 4 * len(dotnet_upgrade_context)
        else:
            score -= 20
        framework_handoff_terms = query_terms & DOTNET_UPGRADE_FRAMEWORK_HANDOFF_TERMS
        if framework_handoff_terms:
            score -= 8 * len(framework_handoff_terms)
    elif row_name == "navigation":
        navigation_context = query_terms & NAVIGATION_CONTEXT_TERMS
        navigation_subject = bool(query_terms & {"navigation", "project-context"})
        if navigation_subject and (query_terms & NAVIGATION_TRIGGER_TERMS):
            score += 4 * len(navigation_context)
        elif query_terms & NAVIGATION_TRIGGER_TERMS:
            score -= 30
    elif row_name == "reference-refresh":
        reference_context = query_terms & REFERENCE_REFRESH_CONTEXT_TERMS
        reference_subject = bool(query_terms & {"reference", "references", "reference-refresh", "external-reference-manager"})
        refresh_subject = bool(query_terms & {"refresh", "external-reference-manager", "manager", "pin", "pinned", "pins", "manifest", "manifests", "fetch", "card", "cards"})
        if reference_subject and refresh_subject and (query_terms & REFERENCE_REFRESH_TRIGGER_TERMS):
            score += 4 * len(reference_context)
        elif query_terms & {"reference", "references", "external", "manifest", "manifests"}:
            score -= 20
    elif row.get("kind") == "workflow" and intake_intent and not (story_intent or bug_intent):
        score -= 20 * len(intake_intent)
    return score, display_matches


def read_object(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def merge_candidate(rows: dict[tuple[str, str], dict[str, object]], candidate: dict[str, object]) -> None:
    kind = str(candidate.get("kind") or "")
    name = str(candidate.get("name") or "")
    if not kind or not name:
        return
    key = (kind, name)
    existing = rows.get(key, {})
    merged = {**candidate, **existing}
    existing_routing = existing.get("routing") if isinstance(existing.get("routing"), dict) else {}
    candidate_routing = candidate.get("routing") if isinstance(candidate.get("routing"), dict) else {}
    if candidate_routing:
        merged["routing"] = {**existing_routing, **candidate_routing}
    elif existing_routing:
        merged["routing"] = existing_routing
    rows[key] = merged


def module_route_candidates(root: Path, kind: str) -> list[dict[str, object]]:
    base = root / ("automations" if kind == "workflow" else ".agents/skills")
    candidates: list[dict[str, object]] = []
    if not base.is_dir():
        return candidates
    for manifest_path in sorted(base.glob("*/module.json")):
        manifest = read_object(manifest_path)
        module_kind = str(manifest.get("kind") or kind)
        if module_kind != kind:
            continue
        name = str(manifest.get("id") or manifest_path.parent.name)
        routing = manifest.get("routing") if isinstance(manifest.get("routing"), dict) else {}
        summary = str(routing.get("use_when") or manifest.get("summary") or "")
        relative = manifest_path.parent.relative_to(root).as_posix()
        candidates.append(
            {
                "kind": kind,
                "name": name,
                "category": "Workflow" if kind == "workflow" else str(manifest.get("category") or "Skill"),
                "use_when": summary,
                "open": f"{relative}/WORKFLOW.md" if kind == "workflow" else f"{relative}/SKILL.md",
                "contract": f"{relative}/module.json" if kind == "workflow" else "",
                "routing": routing,
            }
        )
    return candidates


def registry_route_candidates(root: Path, kind: str) -> list[dict[str, object]]:
    path = root / ("automations/registry.json" if kind == "workflow" else ".agents/registry.json")
    data = read_object(path)
    values = data.get("automations" if kind == "workflow" else "skills")
    if not isinstance(values, list):
        return []
    candidates: list[dict[str, object]] = []
    for entry in values:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("id") or entry.get("name") or "")
        folder = str(entry.get("folder") or (f"automations/{name}" if kind == "workflow" else f".agents/skills/{name}"))
        candidates.append(
            {
                "kind": kind,
                "name": name,
                "category": "Workflow" if kind == "workflow" else str(entry.get("category") or "Skill"),
                "use_when": str(entry.get("summary") or entry.get("description") or ""),
                "open": f"{folder}/{entry.get('start_file', 'WORKFLOW.md' if kind == 'workflow' else 'SKILL.md')}",
                "contract": f"{folder}/{entry.get('contract_file', 'module.json')}" if kind == "workflow" else "",
                "routing": entry.get("routing") if isinstance(entry.get("routing"), dict) else {},
            }
        )
    return candidates


def route_candidates(root: Path, *, kinds: set[str] | None = None) -> list[dict[str, object]]:
    selected = kinds or {"skill", "workflow"}
    rows: dict[tuple[str, str], dict[str, object]] = {}
    if "skill" in selected:
        for candidate in registry_route_candidates(root, "skill"):
            merge_candidate(rows, candidate)
        for candidate in module_route_candidates(root, "skill"):
            merge_candidate(rows, candidate)
        for candidate in parse_markdown_table(root / ".agents" / "routing.md", "skill"):
            merge_candidate(rows, candidate)
    if "workflow" in selected:
        for candidate in registry_route_candidates(root, "workflow"):
            merge_candidate(rows, candidate)
        for candidate in module_route_candidates(root, "workflow"):
            merge_candidate(rows, candidate)
        for candidate in parse_markdown_table(root / "automations" / "routing.md", "workflow"):
            merge_candidate(rows, candidate)
    return list(rows.values())


def explain_routes(
    root: Path,
    query: str,
    *,
    limit: int = 6,
    kinds: set[str] | None = None,
    tool_name: str = "skill-manager.route-explainer",
) -> dict[str, object]:
    query_terms = tokens(query)
    query_display_terms = display_tokens(query)
    query_display_concepts = query_concepts(query)
    avoid_local_ai = local_ai_avoidance_request(query)
    strong_action_terms = (
        (query_terms & (IMPLEMENTATION_INTENT_TERMS | BUG_ACTION_TERMS | STORY_ACTION_TERMS | INTAKE_INTENT_TERMS))
        - {"change"}
    )
    audit_read_only = bool(query_terms & CROSS_CUTTING_AUDIT_INTENT_TERMS) and not strong_action_terms
    read_only = read_only_request(query) or audit_read_only
    analysis_only = read_only and not strong_action_terms
    cross_cutting_audit = bool(
        audit_read_only
        and query_terms & CROSS_CUTTING_AUDIT_SUBJECT_TERMS
        and len(query_terms & CROSS_CUTTING_AUDIT_AREA_TERMS) >= 3
    )
    candidates = [] if cross_cutting_audit and kinds == {"workflow"} else route_candidates(root, kinds=kinds)
    scored: list[dict[str, object]] = []
    for row in candidates:
        score, matches = score_route(
            query_terms,
            row,
            display_query_terms=query_display_terms,
            display_query_concepts=query_display_concepts,
            analysis_only=analysis_only,
            avoid_local_ai=avoid_local_ai,
        )
        if score <= 0:
            continue
        if kinds == {"workflow"} and routing_metadata_terms(row) and score < 2:
            continue
        if matches and set(matches) <= ACTIVATION_GENERIC_TERMS:
            continue
        if matches and set(matches) <= GENERIC_MATCH_TERMS and score <= len(matches):
            continue
        scored.append(
            {
                **row,
                "score": score,
                "matched_terms": matches,
                "reason": (
                    f"Matches {row['kind']} `{row['name']}` through "
                    f"{', '.join(matches[:6]) or 'routing text'}."
                ),
            }
        )
    scored.sort(key=lambda item: (-int(item["score"]), str(item["kind"]), str(item["name"])))
    selected = scored[0] if scored else {}
    rejected = scored[1:limit] if scored else []
    returned = scored[:limit]
    top_score = int(selected.get("score", 0)) if isinstance(selected, dict) else 0
    runner_up_score = int(scored[1].get("score", 0)) if len(scored) > 1 else 0
    workflow_only = kinds == {"workflow"}
    threshold = routing_int(
        selected,
        ("threshold", "high_confidence_threshold", "minimum_score"),
        max(5, len(query_terms) + 1),
        minimum=2 if workflow_only and routing_metadata_terms(selected) else 1,
    ) if isinstance(selected, dict) and selected else max(5, len(query_terms) + 1)
    winner_margin = routing_int(
        selected,
        ("winner_margin", "minimum_margin"),
        1,
    ) if isinstance(selected, dict) and selected else 1
    score_margin = top_score - runner_up_score
    if isinstance(selected, dict) and selected:
        selected["threshold"] = threshold
        selected["winner_margin"] = winner_margin
        selected["score_margin"] = score_margin
    confidence = "none"
    if top_score >= threshold and score_margin >= winner_margin:
        confidence = "high"
    elif top_score >= 2:
        confidence = "medium"
    elif top_score == 1:
        confidence = "low"
    next_command = "Open automations/routing.md." if workflow_only else "Open .agents/routing.md and automations/routing.md."
    next_commands: list[dict[str, object]] = []
    start_command_if_confirmed = ""
    start_ready = False
    confirmation_required = False
    if isinstance(selected, dict) and selected:
        open_path = str(selected.get("open") or "")
        if workflow_only and selected.get("kind") == "workflow" and selected.get("name"):
            if read_only:
                next_command, next_commands = read_only_workflow_next_command(root, str(selected["name"]), query_terms, query)
            else:
                start_command_if_confirmed = (
                    f"python -B .agents/manage.py workflow start --name {selected['name']} "
                    "--summary --compact --format json"
                )
                if confidence == "high":
                    start_ready = True
                    next_command = start_command_if_confirmed
                else:
                    confirmation_required = True
                    next_command = (
                        "Workflow route confidence is not high; rerun which-workflow with a more specific request "
                        "or confirm the selected workflow before starting a run."
                    )
        else:
            next_command = f"Open {open_path}." if open_path else "Use the selected route's owner command."
    return {
        "schema_version": 1,
        "tool": tool_name,
        "ok": True,
        "status": "matched" if scored else "no-match",
        "query": query,
        "query_terms": sorted(query_display_terms),
        "selected_route": selected,
        "selected_owner": selected.get("name", "") if isinstance(selected, dict) else "",
        "confidence": confidence,
        "start_ready": start_ready,
        "confirmation_required": confirmation_required,
        "start_command_if_confirmed": start_command_if_confirmed,
        "read_only_request": read_only,
        "local_ai_avoidance_request": avoid_local_ai,
        "matched_route_count": len(scored),
        "returned_route_count": len(returned),
        "rejected_route_count": len(rejected),
        "rejected_routes": rejected,
        "routes": returned,
        "next_command": next_command,
        "next_commands": next_commands,
        "fallback": (
            "Open .agents/routing.md when no skill route is clear."
            if kinds == {"skill"}
            else "Open automations/routing.md when no workflow route is clear."
            if workflow_only
            else "Open .agents/routing.md for skills and automations/routing.md for workflows when no route is clear."
        ),
    }


def route_summary_row(route: object, *, compact: bool = False) -> dict[str, object]:
    if not isinstance(route, dict) or not route:
        return {}
    row: dict[str, object] = {
        "kind": route.get("kind", ""),
        "name": route.get("name", ""),
        "score": route.get("score", 0),
        "matched_terms": route.get("matched_terms", []),
        "open": route.get("open", ""),
    }
    for key in ("threshold", "winner_margin", "score_margin"):
        if key in route:
            row[key] = route.get(key)
    if route.get("contract"):
        row["contract"] = route.get("contract", "")
    if not compact:
        row["category"] = route.get("category", "")
        row["reason"] = route.get("reason", "")
    return row


def summarize_route_report(report: dict[str, object], *, compact: bool = False) -> dict[str, object]:
    routes = report.get("routes") if isinstance(report.get("routes"), list) else []
    rejected = report.get("rejected_routes") if isinstance(report.get("rejected_routes"), list) else []
    selected = route_summary_row(report.get("selected_route"), compact=compact)
    output: dict[str, object] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.route-explainer"),
        "ok": bool(report.get("ok", False)),
        "status": report.get("status", ""),
        "query": report.get("query", ""),
        "selected_owner": report.get("selected_owner", ""),
        "selected_route": selected,
        "confidence": report.get("confidence", "none"),
        "start_ready": bool(report.get("start_ready", False)),
        "confirmation_required": bool(report.get("confirmation_required", False)),
        "summary": {
            "query_term_count": len(
                report.get("query_terms", []) if isinstance(report.get("query_terms"), list) else []
            ),
            "matched_route_count": report.get("matched_route_count", len(routes)),
            "returned_route_count": report.get("returned_route_count", len(routes)),
            "rejected_route_count": report.get("rejected_route_count", len(rejected)),
            "top_score": selected.get("score", 0) if selected else 0,
        },
        "next_command": report.get("next_command", ""),
    }
    if report.get("next_commands"):
        output["next_commands"] = report.get("next_commands", [])
    if report.get("start_command_if_confirmed"):
        output["start_command_if_confirmed"] = report.get("start_command_if_confirmed", "")
    if compact:
        if not selected:
            output["fallback"] = report.get("fallback", "")
        return output
    output["query_terms"] = report.get("query_terms", [])
    output["routes"] = [route_summary_row(route) for route in routes if isinstance(route, dict)]
    output["rejected_routes"] = [route_summary_row(route) for route in rejected if isinstance(route, dict)]
    output["fallback"] = report.get("fallback", "")
    return output


def workflow_routing_regression_suite_report(root: Path, suite_path: Path) -> dict[str, object]:
    resolved_suite = suite_path if suite_path.is_absolute() else root / suite_path
    try:
        payload = json.loads(resolved_suite.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise SystemExit(f"workflow routing suite not found: {suite_path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"workflow routing suite has invalid JSON: {exc}") from exc
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list):
        raise SystemExit("workflow routing suite must contain a cases array")
    rows: list[dict[str, object]] = []
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            rows.append(
                {
                    "id": f"case-{index}",
                    "ok": False,
                    "issue": "case must be an object",
                }
            )
            continue
        query = str(case.get("query") or "").strip()
        expected_owner = str(case.get("expected_owner") or "").strip()
        expected_confidence = str(case.get("expected_confidence") or "").strip()
        expected_next_command = str(case.get("expected_next_command") or "").strip()
        report = explain_routes(
            root,
            query,
            kinds={"workflow"},
            tool_name="skill-manager.which-workflow",
        )
        selected_owner = str(report.get("selected_owner") or "")
        confidence = str(report.get("confidence") or "")
        next_command = str(report.get("next_command") or "")
        issues: list[str] = []
        if selected_owner != expected_owner:
            issues.append(f"expected owner {expected_owner or '<none>'}, got {selected_owner or '<none>'}")
        if expected_confidence and confidence != expected_confidence:
            issues.append(f"expected confidence {expected_confidence}, got {confidence}")
        if expected_next_command and next_command != expected_next_command:
            issues.append(f"expected next command {expected_next_command}, got {next_command}")
        rows.append(
            {
                "id": str(case.get("id") or f"case-{index}"),
                "ok": not issues,
                "query": query,
                "expected_owner": expected_owner,
                "selected_owner": selected_owner,
                "expected_confidence": expected_confidence,
                "confidence": confidence,
                "expected_next_command": expected_next_command,
                "next_command": next_command,
                "issues": issues,
                "route": route_summary_row(report.get("selected_route"), compact=True),
            }
        )
    failed = [row for row in rows if row.get("ok") is not True]
    return {
        "schema_version": 1,
        "tool": "skill-manager.workflow-routing-suite",
        "ok": not failed,
        "status": "passed" if not failed else "failed",
        "suite_path": repo.relative(root, resolved_suite),
        "summary": {
            "case_count": len(rows),
            "failed_count": len(failed),
        },
        "cases": rows,
        "next_command": "python -B .agents/manage.py which-workflow --suite <path> --check-suite --format json",
    }


def render_markdown(report: dict[str, object]) -> str:
    lines = ["# Route Explanation", ""]
    lines.append(f"- Query: {report['query']}")
    lines.append(f"- Status: {report['status']}")
    lines.append(f"- Confidence: {report.get('confidence', 'none')}")
    if report.get("selected_owner"):
        lines.append(f"- Selected owner: `{report.get('selected_owner')}`")
    lines.append(f"- Next command: {report.get('next_command')}")
    lines.append("")
    routes = report.get("routes", [])
    if isinstance(routes, list) and routes:
        lines.extend(["| Kind | Name | Score | Why | Open |", "|---|---|---:|---|---|"])
        for route in routes:
            if not isinstance(route, dict):
                continue
            lines.append(
                f"| {route.get('kind')} | `{route.get('name')}` | {route.get('score')} | "
                f"{route.get('reason')} | `{route.get('open')}` |"
            )
        rejected = report.get("rejected_routes", [])
        if isinstance(rejected, list) and rejected:
            lines.extend(["", "## Rejected Routes", ""])
            for route in rejected[:3]:
                if isinstance(route, dict):
                    lines.append(f"- `{route.get('name')}`: lower score ({route.get('score')}) than selected route.")
    else:
        lines.append(str(report.get("fallback")))
    lines.append("")
    return "\n".join(lines)


def render_workflow_routing_suite(report: dict[str, object]) -> str:
    lines = ["# Workflow Routing Regression Suite", ""]
    lines.append(f"- Status: {report.get('status')}")
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines.append(f"- Cases: {summary.get('case_count', 0)}")
    lines.append(f"- Failed: {summary.get('failed_count', 0)}")
    rows = report.get("cases") if isinstance(report.get("cases"), list) else []
    if rows:
        lines.extend(["", "| ID | OK | Expected | Selected | Confidence |", "|---|---:|---|---|---|"])
        for row in rows:
            if isinstance(row, dict):
                lines.append(
                    f"| {row.get('id')} | {str(row.get('ok')).lower()} | "
                    f"`{row.get('expected_owner')}` | `{row.get('selected_owner')}` | {row.get('confidence')} |"
                )
    failed = [row for row in rows if isinstance(row, dict) and row.get("ok") is not True]
    if failed:
        lines.extend(["", "## Failures", ""])
        for row in failed:
            lines.append(f"- `{row.get('id')}`: {'; '.join(str(item) for item in row.get('issues', []))}")
    lines.append("")
    return "\n".join(lines)


def explain_route_command(args: argparse.Namespace, root: Path) -> int:
    report = explain_routes(root, args.query, limit=args.limit)
    if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
        report = summarize_route_report(report, compact=bool(getattr(args, "compact", False)))
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0 if report["ok"] else 1


def which_skill_command(args: argparse.Namespace, root: Path) -> int:
    report = explain_routes(
        root,
        args.query,
        limit=args.limit,
        kinds={"skill"},
        tool_name="skill-manager.which-skill",
    )
    if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
        report = summarize_route_report(report, compact=bool(getattr(args, "compact", False)))
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0 if report["ok"] else 1


def which_workflow_command(args: argparse.Namespace, root: Path) -> int:
    if getattr(args, "suite", None):
        report = workflow_routing_regression_suite_report(root, Path(args.suite))
        if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
            report = {
                "schema_version": report.get("schema_version", 1),
                "tool": report.get("tool", "skill-manager.workflow-routing-suite"),
                "ok": bool(report.get("ok")),
                "status": report.get("status", ""),
                "suite_path": report.get("suite_path", ""),
                "summary": report.get("summary", {}),
                "failures": [
                    row
                    for row in report.get("cases", [])
                    if isinstance(row, dict) and row.get("ok") is not True
                ],
                "next_command": report.get("next_command", ""),
            }
            if bool(getattr(args, "compact", False)) and not report.get("failures"):
                report.pop("failures", None)
        if args.output_format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_workflow_routing_suite(report), end="")
        return 0 if report.get("ok") else 1
    if not getattr(args, "query", ""):
        raise SystemExit("which-workflow requires a query unless --suite is provided")
    report = explain_routes(
        root,
        args.query,
        limit=args.limit,
        kinds={"workflow"},
        tool_name="skill-manager.which-workflow",
    )
    if bool(getattr(args, "summary", False)) or bool(getattr(args, "compact", False)):
        report = summarize_route_report(report, compact=bool(getattr(args, "compact", False)))
    if args.output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0 if report["ok"] else 1
