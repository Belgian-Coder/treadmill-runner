"""Lossless helpers for typed module command argv and human display."""

from __future__ import annotations

import json


def command_argv(command: object) -> list[str]:
    """Return a detached argv array without parsing display or shell text."""

    if isinstance(command, list) and all(isinstance(part, str) for part in command):
        return list(command)
    if isinstance(command, dict):
        argv = command.get("argv")
        if isinstance(argv, list) and all(isinstance(part, str) for part in argv):
            return list(argv)
    return []


def command_display(command: object) -> str:
    """Render argv as canonical JSON for humans, never as executable shell text."""

    return json.dumps(command_argv(command), ensure_ascii=False, separators=(",", ":"))
