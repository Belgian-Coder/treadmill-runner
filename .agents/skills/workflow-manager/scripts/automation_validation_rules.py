#!/usr/bin/env python3
"""Static validation rules for current workflow modules."""

from __future__ import annotations

import re
import sys

sys.dont_write_bytecode = True

ALLOWED_AUTOMATIONS_ROOT_FILES = {
    "LICENSE.txt",
    "NOTICE.txt",
    "hooks.json",
    "registry.json",
    "routing.md",
}
DISALLOWED_AUTOMATIONS_ROOT_DIRS = {
    "runs": "Use workflow-local automations/<workflow-name>/runs/.",
}
WORKFLOW_FOLDER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
PHASE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
TEXT_EXTENSIONS = {
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
NEGATION_TERMS = ("no ", "none", "not ", "without", "never")
SIGNAL_RULES = (
    ("network", "http-url", re.compile(r"https?://", re.IGNORECASE)),
    ("network", "clone-or-fetch", re.compile(r"\b(git\s+clone|fetch|pull|api)\b", re.IGNORECASE)),
    ("credentials", "credential", re.compile(r"\b(token|secret|password|pat|api key)\b", re.IGNORECASE)),
    ("uploads", "upload", re.compile(r"\b(upload|publish|push)\b", re.IGNORECASE)),
    ("attachments", "attachment", re.compile(r"\battachment\b", re.IGNORECASE)),
)
STATIC_SCRIPT_PATTERN = re.compile(
    r"(?P<path>(?:\.agents|automations|\.github|\.claude|scripts)"
    r"[^`\s]*?\.py)\b"
)
MANAGE_COMMAND_PATTERN = re.compile(
    r"(?:python\s+-B\s+)?\.agents[\\/]manage\.py\s+(?P<command>[a-z0-9-]+)\b"
)
START_MANAGE_COMMAND_PATTERN = re.compile(
    r"(?:python\s+-B\s+)?\.agents[\\/]manage\.py\s+[a-z0-9-]+\b"
)
KNOWN_MANAGE_COMMANDS = {
    "analyze-location",
    "attest-skill",
    "check",
    "check-changed",
    "check-repo-health",
    "compare-skill",
    "eval-skill",
    "eval-workflow",
    "finish",
    "index-workflow-runs",
    "inspect-skill",
    "link-skills",
    "measure-skill-budget",
    "new",
    "new-skill-checklist",
    "review",
    "review-skill",
    "route",
    "workflow",
    "skill-inventory",
    "status",
    "sync",
    "sync-automation-routing",
    "sync-claude-skills",
    "sync-instructions",
    "sync-skill-routing",
    "triage-candidates",
    "upgrade-skill",
    "validate",
    "validate-agent-compatibility",
    "validate-automations",
}
