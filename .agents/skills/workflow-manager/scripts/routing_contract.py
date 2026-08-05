"""Shared validation helpers for workflow routing metadata."""

from __future__ import annotations

import re


TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.-]*")
SEPARATOR_RE = re.compile(r"[-_.]+")
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
GENERIC_ACTIVATION_TERMS = {
    "automation",
    "automations",
    "harnes",
    "harness",
    "skill",
    "skills",
    "workflow",
    "workflows",
}
GENERIC_SCORE_TERMS = {
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
ALIAS_GROUPS = (
    {"a11y", "accessibility", "accessible"},
    {"ado", "azure-devops", "azure", "ticket", "work-item", "workitem"},
    {"ci", "cd", "pipeline", "pipelines", "github-actions", "actions", "build"},
    {"deck", "ppt", "pptx", "powerpoint", "presentation", "presentations", "slides"},
    {"doc", "docx", "document", "documents", "word"},
    {"embedding", "embeddings", "context_evidence", "retrieval", "vector", "vectors"},
    {"exe", "executable", "binary", "portable"},
    {"pdf", "attachment", "attachments"},
)


def term_components(value: str) -> set[str]:
    """Return meaningful components using the same separators as the route scorer."""

    components: set[str] = set()
    for token in TOKEN_RE.findall(value.strip().lower()):
        for component in SEPARATOR_RE.split(token):
            if len(component) > 2 and component not in STOP_WORDS:
                components.add(component)
    return components


def has_specific_concept(values: list[str]) -> bool:
    return any(term_components(value) - GENERIC_SCORE_TERMS for value in values)


def has_non_generic_activation(values: list[str]) -> bool:
    return any(term_components(value) - GENERIC_ACTIVATION_TERMS for value in values)


def normalized_terms(values: list[str], *, label: str) -> tuple[list[str], list[str]]:
    normalized: list[str] = []
    issues: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            issues.append(f"{label} must contain non-empty strings")
            continue
        term = value.strip().lower()
        if term not in normalized:
            normalized.append(term)
    if not normalized:
        issues.append(f"at least one {label} is required")
    elif not has_specific_concept(normalized):
        issues.append(f"{label} must include a specific routing concept")
    return normalized, issues


def canonical_concept(value: str) -> str:
    return SEPARATOR_RE.sub("-", value.strip().lower())


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


def semantic_key(concept: str) -> str:
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


def routing_score_capacity(values: list[str]) -> int:
    """Return the maximum independent score slots exposed by routing terms."""

    if not has_specific_concept(values):
        return 0

    concepts = {
        canonical_concept(item)
        for value in values
        for item in TOKEN_RE.findall(value.lower())
        if concept_variants(item)
    }
    standalone = {concept for concept in concepts if "-" not in concept}
    keys: set[str] = set()
    for concept in concepts:
        parts = [part for part in concept.split("-") if concept_variants(part)]
        if len(parts) > 1 and all(part in standalone for part in parts):
            continue
        keys.add(semantic_key(concept))
    return len(keys)


def routing_reachability_issues(
    terms: list[str],
    *,
    threshold: int,
    winner_margin: int,
) -> list[str]:
    capacity = routing_score_capacity(terms)
    issues: list[str] = []
    if isinstance(threshold, int) and not isinstance(threshold, bool) and threshold > capacity:
        issues.append(
            f"routing terms can score at most {capacity} independent concept(s) and cannot reach threshold {threshold}"
        )
    if isinstance(winner_margin, int) and not isinstance(winner_margin, bool) and winner_margin > capacity:
        issues.append(
            f"routing terms can score at most {capacity} independent concept(s) and cannot reach winner margin {winner_margin}"
        )
    return issues
