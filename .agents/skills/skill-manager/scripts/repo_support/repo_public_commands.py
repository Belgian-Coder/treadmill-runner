"""Route the compact public command surface to its internal owners."""

from __future__ import annotations


def normalize_public_commands(raw_args: list[str]) -> list[str]:
    """Map compact public commands onto internal owner commands."""
    if not raw_args:
        return raw_args
    command, *rest = raw_args
    simple_aliases = {
        "status": "dashboard",
        "route": "explain-route",
        "check": "validate",
    }
    if command in simple_aliases:
        return [simple_aliases[command], *rest]
    if command == "review":
        if "--skill" in rest:
            return ["review-skill", *rest]
        if "--workflow" in rest:
            rewritten = list(rest)
            rewritten[rewritten.index("--workflow")] = "--name"
            return ["review-workflow", *rewritten]
        if rest and not rest[0].startswith("-"):
            target = rest[0]
            remaining = rest[1:]
            if target.replace("\\", "/").startswith(".agents/skills/"):
                return ["review-skill", "--skill", target, *remaining]
            return ["review-workflow", "--name", target, *remaining]
        return ["review-workflow", *rest]
    if command == "new":
        help_requested = any(item in {"-h", "--help"} for item in rest)
        if not rest or (help_requested and "--kind" not in rest):
            return raw_args
        rewritten = list(rest)
        kind = ""
        if "--kind" in rewritten:
            index = rewritten.index("--kind")
            if index + 1 >= len(rewritten):
                raise SystemExit("--kind requires a value: skill or workflow")
            kind = rewritten[index + 1]
            del rewritten[index : index + 2]
        if kind == "workflow" and ("--from-request" in rewritten or "--request" in rewritten):
            return ["create-workflow-from-request", *rewritten]
        if kind == "workflow" or (not kind and "--summary" in rewritten):
            return ["create-workflow", *rewritten]
        if kind and kind != "skill":
            raise SystemExit("--kind must be skill or workflow")
        return ["new-skill-checklist", *rewritten]
    return raw_args
