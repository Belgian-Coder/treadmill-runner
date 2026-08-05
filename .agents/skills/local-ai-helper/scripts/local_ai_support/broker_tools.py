"""Brokered read-only repository tools for local AI routing."""

from __future__ import annotations

import os
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_TOOLS_CONFIG = {
    "mode": "brokered-read-only",
    "allow": ["repo.search", "repo.read", "repo.tree", "repo.generated-status"],
    "max_read_bytes": 20000,
    "max_search_results": 50,
    "max_tree_entries": 200,
    "timeout_seconds": 5,
    "exclude_paths": [
        ".git",
        ".agents/local-ai/cache",
        ".agents/local-ai/bundle",
        ".agents/local-ai/downloads",
        ".agents/tools/cache",
        ".agents/.deps",
        ".agents/registry.json",
        ".aider.conf.yml",
        ".claude",
        ".continue",
        ".github/copilot-instructions.md",
        "GEMINI.md",
        "automations/registry.json",
    ],
}
PORTABLE_RG_MANIFEST_REL = ".agents/skills/skill-manager/assets/tools/ripgrep/manifest.json"
PORTABLE_RG_CACHE_REL = ".agents/tools/cache/ripgrep"


def relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def normalized_repo_rel(value: str) -> str:
    return str(value).replace("\\", "/").strip().strip("/")


def _strong_file_identity(stat) -> tuple[int, int] | None:
    identity = (
        int(getattr(stat, "st_dev", 0) or 0),
        int(getattr(stat, "st_ino", 0) or 0),
    )
    return identity if all(identity) else None


def _same_file_identity(left, right, *, require_strong: bool = False) -> bool:
    left_identity = _strong_file_identity(left)
    right_identity = _strong_file_identity(right)
    if require_strong:
        if left_identity is None or right_identity is None:
            return False
        if left_identity != right_identity:
            return False
    elif (
        left_identity is not None
        and right_identity is not None
        and left_identity != right_identity
    ):
        return False
    return (
        int(left.st_mode) == int(right.st_mode)
        and int(left.st_size) == int(right.st_size)
        and int(getattr(left, "st_mtime_ns", int(left.st_mtime * 1_000_000_000)))
        == int(getattr(right, "st_mtime_ns", int(right.st_mtime * 1_000_000_000)))
    )


def _contained_path_stat(
    path: Path,
    *,
    contained_root: Path | None,
    containment_check,
) -> os.stat_result:
    try:
        if containment_check is not None:
            if not bool(containment_check(path)):
                raise OSError(f"file is not safely contained: {path}")
        elif contained_root is not None:
            path.resolve().relative_to(contained_root)
        return path.stat(follow_symlinks=False)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise OSError(f"file containment check failed: {path}") from exc


def sha256_file(
    path: Path,
    *,
    contained_root: Path | None = None,
    containment_check=None,
    return_stat: bool = False,
) -> str | tuple[str, os.stat_result]:
    boundary_sensitive = contained_root is not None or containment_check is not None
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if boundary_sensitive:
            current = _contained_path_stat(
                path,
                contained_root=contained_root,
                containment_check=containment_check,
            )
            if not _same_file_identity(before, current, require_strong=True):
                raise OSError(f"file identity changed before hashing: {path}")
        digest = hashlib.file_digest(handle, "sha256")
        after = os.fstat(handle.fileno())
    if not _same_file_identity(before, after):
        raise OSError(f"file changed while hashing: {path}")
    current_after_close = None
    if boundary_sensitive:
        current_after_close = _contained_path_stat(
            path,
            contained_root=contained_root,
            containment_check=containment_check,
        )
        if not _same_file_identity(
            before,
            current_after_close,
            require_strong=True,
        ):
            raise OSError(f"file identity changed after hashing: {path}")
    value = digest.hexdigest()
    return (value, current_after_close or after) if return_stat else value


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def platform_key() -> str:
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64", "x64"}:
        arch = "x64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        arch = machine
    if sys.platform.startswith("win"):
        system = "windows"
    elif sys.platform == "darwin":
        system = "macos"
    elif sys.platform.startswith("linux"):
        system = "linux"
    else:
        system = sys.platform
    return f"{system}-{arch}"


def verified_portable_rg(root: Path) -> str | None:
    manifest = read_json_object(root / PORTABLE_RG_MANIFEST_REL)
    assets = manifest.get("assets") if isinstance(manifest.get("assets"), dict) else {}
    key = platform_key()
    asset = assets.get(key) if isinstance(assets, dict) else None
    if not isinstance(asset, dict):
        return None
    executable = str(asset.get("executable", "rg.exe" if key.startswith("windows-") else "rg"))
    binary = root / PORTABLE_RG_CACHE_REL / key / executable
    record = read_json_object(root / PORTABLE_RG_CACHE_REL / key / "install.json")
    expected_hash = str(record.get("binary_sha256", "")).lower()
    if not binary.is_file() or not expected_hash:
        return None
    if str(record.get("version", "")) != str(manifest.get("version", "")):
        return None
    try:
        if sha256_file(binary).lower() != expected_hash:
            return None
    except OSError:
        return None
    return str(binary)


def path_matches_prefix(rel_path: str, prefix: str) -> bool:
    rel = normalized_repo_rel(rel_path).lower()
    item = normalized_repo_rel(prefix).lower()
    return bool(item) and (rel == item or rel.startswith(item + "/"))


def _normalize_string_list(value: Any, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        value = default
    return [str(item).strip() for item in value if str(item).strip()]


def _tools_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("tools", DEFAULT_TOOLS_CONFIG)
    return raw if isinstance(raw, dict) else DEFAULT_TOOLS_CONFIG


def _int_limit(value: Any, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if maximum is not None:
        number = min(number, maximum)
    return max(minimum, number)


def broker_exclude_paths(config: dict[str, Any]) -> list[str]:
    tools = _tools_config(config)
    defaults = list(DEFAULT_TOOLS_CONFIG["exclude_paths"])
    excludes = _normalize_string_list(tools.get("exclude_paths"), defaults)
    if ".git" not in excludes:
        excludes.insert(0, ".git")
    return excludes


def broker_exclude_globs(config: dict[str, Any]) -> list[str]:
    globs: list[str] = []
    for excluded in broker_exclude_paths(config):
        suffix = Path(excluded).suffix
        glob = f"!{excluded}" if suffix else f"!{excluded}/**"
        if glob not in globs:
            globs.append(glob)
    return globs


def broker_path_is_excluded(root: Path, path: Path, config: dict[str, Any]) -> bool:
    rel_path = relative_to_root(root, path)
    return any(path_matches_prefix(rel_path, excluded) for excluded in broker_exclude_paths(config))


def broker_search_file_candidates(root: Path, base_path: Path, config: dict[str, Any]) -> list[Path]:
    if base_path.is_file():
        return [] if broker_path_is_excluded(root, base_path, config) else [base_path]
    candidates: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base_path):
        current = Path(dirpath)
        kept_dirs: list[str] = []
        for dirname in sorted(dirnames, key=str.lower):
            path = current / dirname
            if not broker_path_is_excluded(root, path, config):
                kept_dirs.append(dirname)
        dirnames[:] = kept_dirs
        for filename in sorted(filenames, key=str.lower):
            path = current / filename
            if not broker_path_is_excluded(root, path, config):
                candidates.append(path)
    return candidates


def resolve_repo_request_path(root: Path, requested_path: str) -> Path:
    root_resolved = root.resolve()
    candidate = Path(requested_path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (root_resolved / requested_path).resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"requested path escapes repository root: {requested_path}") from exc
    parts = set(resolved.relative_to(root_resolved).parts)
    if ".git" in parts:
        raise ValueError("access to .git is not allowed")
    if "bundle" in parts and ".agents" in parts and "local-ai" in parts:
        raise ValueError("access to local AI bundle files is not allowed")
    return resolved


def broker_read(root: Path, config: dict[str, Any], requested_path: str) -> dict[str, Any]:
    tools = _tools_config(config)
    max_bytes = int(tools.get("max_read_bytes", DEFAULT_TOOLS_CONFIG["max_read_bytes"]))
    path = resolve_repo_request_path(root, requested_path)
    if not path.is_file():
        return {"ok": False, "error": f"path is not a file: {requested_path}"}
    data = path.read_bytes()[: max_bytes + 1]
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": f"path is not valid UTF-8 text: {requested_path}"}
    return {"ok": True, "path": relative_to_root(root, path), "content": content, "truncated": truncated}


def broker_tree(root: Path, config: dict[str, Any], requested_path: str, max_entries: int | None = None) -> dict[str, Any]:
    tools = _tools_config(config)
    configured_limit = int(tools.get("max_tree_entries", DEFAULT_TOOLS_CONFIG["max_tree_entries"]))
    entry_limit = _int_limit(max_entries or configured_limit, configured_limit, maximum=configured_limit)
    path = resolve_repo_request_path(root, requested_path or ".")
    if not path.exists():
        return {"ok": False, "error": f"path does not exist: {requested_path}"}
    candidates = [path] if path.is_file() else sorted(path.rglob("*"), key=lambda item: item.as_posix().lower())
    entries: list[str] = []
    for candidate in candidates:
        if len(entries) >= entry_limit:
            break
        if not candidate.is_file():
            continue
        try:
            resolve_repo_request_path(root, relative_to_root(root, candidate))
        except ValueError:
            continue
        entries.append(relative_to_root(root, candidate))
    return {"ok": True, "path": relative_to_root(root, path), "entries": entries, "truncated": len(entries) >= entry_limit}


def broker_search(root: Path, config: dict[str, Any], pattern: str, requested_path: str = ".") -> dict[str, Any]:
    tools = _tools_config(config)
    max_results = int(tools.get("max_search_results", DEFAULT_TOOLS_CONFIG["max_search_results"]))
    timeout = int(tools.get("timeout_seconds", DEFAULT_TOOLS_CONFIG["timeout_seconds"]))
    if not pattern or len(pattern) > 200:
        return {"ok": False, "error": "search pattern must be 1-200 characters"}
    base_path = resolve_repo_request_path(root, requested_path or ".")
    results: list[dict[str, Any]] = []
    rg = verified_portable_rg(root) or shutil.which("rg")
    if rg:
        command = [rg, "--line-number", "--no-heading", "--color", "never", "--hidden"]
        for glob in broker_exclude_globs(config):
            command.extend(["--glob", glob])
        command.extend([pattern, str(base_path)])
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "search timed out"}
        if completed.returncode not in {0, 1}:
            return {"ok": False, "error": completed.stderr.strip() or "search failed"}
        for line in completed.stdout.splitlines():
            if len(results) >= max_results:
                break
            match = re.match(r"^(.*?):(\d+):(.*)$", line)
            if not match:
                continue
            path_text, line_number, text = match.groups()
            try:
                rel_path = relative_to_root(root, resolve_repo_request_path(root, path_text))
            except ValueError:
                continue
            results.append({"path": rel_path, "line": int(line_number), "text": text[:500]})
        return {"ok": True, "search_backend": "ripgrep", "results": results, "truncated": len(results) >= max_results}

    for path in broker_search_file_candidates(root, base_path, config):
        if len(results) >= max_results:
            break
        if not path.is_file():
            continue
        if broker_path_is_excluded(root, path, config):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for index, line in enumerate(content.splitlines(), start=1):
            if pattern in line:
                results.append({"path": relative_to_root(root, path), "line": index, "text": line[:500]})
                if len(results) >= max_results:
                    break
    return {"ok": True, "search_backend": "stdlib", "results": results, "truncated": len(results) >= max_results}


def broker_generated_status(root: Path) -> dict[str, Any]:
    generated_paths = [
        ".agents/routing.md",
        ".agents/registry.json",
        "automations/routing.md",
        "automations/registry.json",
        ".aider.conf.yml",
        ".claude/CLAUDE.md",
        ".continue/rules/repository-instructions.md",
        ".github/copilot-instructions.md",
        "GEMINI.md",
    ]
    files: list[dict[str, Any]] = []
    for rel_path in generated_paths:
        path = root / rel_path
        files.append({"path": rel_path, "exists": path.exists(), "size": path.stat().st_size if path.exists() else 0})
    return {"ok": True, "files": files}


def broker_tool_request(root: Path, config: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, dict):
        return {"ok": False, "error": "tool request must be a JSON object"}
    tool = str(request.get("tool", request.get("name", ""))).strip()
    tools = _tools_config(config)
    allowed_tools = set(tools.get("allow", DEFAULT_TOOLS_CONFIG["allow"]))
    if tool not in allowed_tools:
        return {"ok": False, "error": f"tool {tool!r} is not allowed"}
    try:
        if tool == "repo.read":
            return broker_read(root, config, str(request.get("path", "")))
        if tool == "repo.tree":
            return broker_tree(root, config, str(request.get("path", ".")), _int_limit(request.get("max_entries"), int(tools.get("max_tree_entries", 200))))
        if tool == "repo.search":
            return broker_search(root, config, str(request.get("pattern", "")), str(request.get("path", ".")))
        if tool == "repo.generated-status":
            return broker_generated_status(root)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": f"tool {tool!r} is not implemented"}
