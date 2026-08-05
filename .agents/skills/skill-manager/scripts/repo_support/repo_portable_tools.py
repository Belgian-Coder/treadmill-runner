#!/usr/bin/env python3
"""Pinned portable tool downloads owned by skill-manager."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

MANIFEST_REL = ".agents/skills/skill-manager/assets/tools/ripgrep/manifest.json"
CACHE_REL = ".agents/tools/cache/ripgrep"
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def ripgrep_manifest(root: Path) -> dict[str, Any]:
    return read_json(root / MANIFEST_REL)


def tool_manifest_paths(root: Path) -> list[Path]:
    tools_root = root / ".agents" / "skills" / "skill-manager" / "assets" / "tools"
    if not tools_root.exists():
        return []
    return sorted(tools_root.glob("*/manifest.json"), key=lambda item: item.as_posix().lower())


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


def ripgrep_asset(root: Path, key: str | None = None) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
    manifest = ripgrep_manifest(root)
    current = key or platform_key()
    assets = manifest.get("assets", {})
    asset = assets.get(current) if isinstance(assets, dict) else None
    return current, asset if isinstance(asset, dict) else None, manifest


def portable_ripgrep_binary(root: Path, key: str | None = None) -> Path:
    current, asset, _manifest = ripgrep_asset(root, key)
    executable = str(asset.get("executable", "rg.exe" if current.startswith("windows-") else "rg")) if asset else "rg"
    return root / CACHE_REL / current / executable


def portable_ripgrep_record(root: Path, key: str | None = None) -> Path:
    current = key or platform_key()
    return root / CACHE_REL / current / "install.json"


def run_ripgrep_version(path: Path) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    first = completed.stdout.splitlines()[0] if completed.stdout else ""
    return completed.returncode == 0, first


def verified_portable_ripgrep(root: Path) -> dict[str, Any]:
    key, asset, manifest = ripgrep_asset(root)
    if not manifest:
        return {"ok": False, "status": "manifest-missing", "platform": key, "path": str(root / MANIFEST_REL)}
    if asset is None:
        return {"ok": False, "status": "unsupported-platform", "platform": key}

    binary = portable_ripgrep_binary(root, key)
    record_path = portable_ripgrep_record(root, key)
    if not binary.exists():
        return {"ok": False, "status": "missing", "platform": key, "path": str(binary)}

    record = read_json(record_path)
    expected_binary_hash = str(record.get("binary_sha256", "")).lower()
    if not expected_binary_hash:
        return {"ok": False, "status": "unverified", "platform": key, "path": str(binary)}
    actual_binary_hash = sha256_file(binary).lower()
    if actual_binary_hash != expected_binary_hash:
        return {
            "ok": False,
            "status": "hash-mismatch",
            "platform": key,
            "path": str(binary),
            "expected_sha256": expected_binary_hash,
            "actual_sha256": actual_binary_hash,
        }
    if str(record.get("version", "")) != str(manifest.get("version", "")):
        return {
            "ok": False,
            "status": "version-mismatch",
            "platform": key,
            "path": str(binary),
            "expected_version": manifest.get("version", ""),
            "actual_version": record.get("version", ""),
        }
    ok, version_text = run_ripgrep_version(binary)
    expected_version = str(manifest.get("version", ""))
    if not ok or expected_version not in version_text:
        return {
            "ok": False,
            "status": "version-check-failed",
            "platform": key,
            "path": str(binary),
            "version": version_text,
        }
    return {
        "ok": True,
        "status": "present",
        "source": "portable",
        "platform": key,
        "path": str(binary),
        "version": version_text,
        "binary_sha256": actual_binary_hash,
    }


def validate_tool_manifest(path: Path, key: str | None = None) -> dict[str, Any]:
    manifest = read_json(path)
    issues: list[str] = []
    tool = str(manifest.get("tool") or path.parent.name)
    assets = manifest.get("assets") if isinstance(manifest.get("assets"), dict) else {}
    current = key or platform_key()
    if not manifest:
        issues.append("manifest is missing or invalid JSON")
    for field in ("schema_version", "tool", "version", "source", "license", "assets"):
        if field not in manifest:
            issues.append(f"missing required field: {field}")
    for platform_name, asset in sorted(assets.items()):
        if not isinstance(asset, dict):
            issues.append(f"{platform_name}: asset must be an object")
            continue
        for field in ("url", "sha256", "size", "archive_type", "executable", "name"):
            if field not in asset:
                issues.append(f"{platform_name}: missing asset field {field}")
        sha = str(asset.get("sha256", "")).lower()
        if sha and not SHA256_RE.match(sha):
            issues.append(f"{platform_name}: sha256 must be 64 lowercase hex characters")
        archive_type = str(asset.get("archive_type", ""))
        if archive_type and archive_type not in {"zip", "tar.gz"}:
            issues.append(f"{platform_name}: unsupported archive_type {archive_type}")
        try:
            size = int(asset.get("size", 0) or 0)
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            issues.append(f"{platform_name}: size must be positive")
    if current not in assets:
        issues.append(f"current platform {current} has no asset")
    return {
        "tool": tool,
        "path": str(path),
        "ok": not issues,
        "status": "valid" if not issues else "invalid",
        "version": manifest.get("version", ""),
        "source": manifest.get("source", ""),
        "license": manifest.get("license", ""),
        "platform": current,
        "asset_count": len(assets),
        "issues": issues,
    }


def portable_install_issue(item: dict[str, Any]) -> str:
    tool = str(item.get("tool") or item.get("source") or "tool")
    status = str(item.get("status") or "unknown")
    details: list[str] = []
    if item.get("platform"):
        details.append(f"platform={item.get('platform')}")
    if item.get("path"):
        details.append(f"path={item.get('path')}")
    if item.get("reason"):
        details.append(f"reason={item.get('reason')}")
    suffix = f" ({'; '.join(details)})" if details else ""
    return f"{tool}: {status}{suffix}"


def portable_tools_report(root: Path, *, require_installed: bool = False) -> dict[str, Any]:
    manifests = [validate_tool_manifest(path) for path in tool_manifest_paths(root)]
    installs: list[dict[str, Any]] = []
    for manifest in manifests:
        if manifest.get("tool") == "ripgrep":
            install = verified_portable_ripgrep(root)
            install.setdefault("tool", "ripgrep")
            installs.append(install)
        else:
            installs.append(
                {
                    "ok": True,
                    "status": "not-checked",
                    "tool": manifest.get("tool", ""),
                    "reason": "no verifier registered for this tool",
                }
            )
    manifest_issues = [
        f"{item.get('tool')}: {issue}"
        for item in manifests
        for issue in item.get("issues", [])
    ]
    install_issues = [
        portable_install_issue(item)
        for item in installs
        if require_installed and not item.get("ok")
    ]
    issues = [*manifest_issues, *install_issues]
    return {
        "schema_version": 1,
        "tool": "skill-manager.portable-tools",
        "ok": not issues,
        "status": "ready" if not issues else "issues",
        "summary": {
            "manifest_count": len(manifests),
            "valid_manifest_count": sum(1 for item in manifests if item.get("ok")),
            "installed_count": sum(1 for item in installs if item.get("ok")),
            "issue_count": len(issues),
            "require_installed": require_installed,
            "platform": platform_key(),
        },
        "manifests": manifests,
        "installed": installs,
        "issues": issues,
        "next_command": (
            "python -B .agents/manage.py setup --install-rg-portable"
            if any(item.get("status") == "missing" for item in installs)
            else ""
        ),
    }


def summarize_portable_tools_report(report: dict[str, Any], *, compact: bool = False) -> dict[str, Any]:
    output: dict[str, Any] = {
        "schema_version": report.get("schema_version", 1),
        "tool": report.get("tool", "skill-manager.portable-tools"),
        "ok": bool(report.get("ok", False)),
        "status": report.get("status", ""),
        "summary": report.get("summary", {}),
        "issues": report.get("issues", []),
        "next_command": report.get("next_command", ""),
    }
    if not compact:
        output["manifests"] = report.get("manifests", [])
        output["installed"] = report.get("installed", [])
    return output


def render_portable_tools_report(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Portable Tools",
        "",
        f"- Status: {report.get('status')}",
        f"- Platform: `{summary.get('platform', platform_key())}`",
        f"- Manifests: {summary.get('valid_manifest_count', 0)}/{summary.get('manifest_count', 0)} valid",
        f"- Installed/verifiable: {summary.get('installed_count', 0)}",
    ]
    installs = report.get("installed") if isinstance(report.get("installed"), list) else []
    if installs:
        lines.extend(["", "## Current Platform", ""])
        for item in installs:
            if isinstance(item, dict):
                lines.append(
                    f"- {item.get('source', item.get('tool', 'tool'))}: {item.get('status')} "
                    f"`{item.get('path', '')}`".rstrip()
                )
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if issues:
        lines.extend(["", "## Issues", ""])
        lines.extend(f"- {item}" for item in issues)
    if report.get("next_command"):
        lines.extend(["", f"Next command: `{report.get('next_command')}`"])
    return "\n".join(lines) + "\n"


def download_file(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "skills-harness-setup"})
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def zip_member_bytes(archive: Path, executable: str) -> bytes:
    with zipfile.ZipFile(archive) as zip_handle:
        matches = [
            name
            for name in zip_handle.namelist()
            if not name.endswith("/") and Path(name).name.lower() == executable.lower()
        ]
        if len(matches) != 1:
            raise RuntimeError(f"expected exactly one {executable} in archive, found {len(matches)}")
        return zip_handle.read(matches[0])


def tar_member_bytes(archive: Path, executable: str) -> bytes:
    with tarfile.open(archive, "r:gz") as tar_handle:
        matches = [
            member
            for member in tar_handle.getmembers()
            if member.isfile() and Path(member.name).name.lower() == executable.lower()
        ]
        if len(matches) != 1:
            raise RuntimeError(f"expected exactly one {executable} in archive, found {len(matches)}")
        extracted = tar_handle.extractfile(matches[0])
        if extracted is None:
            raise RuntimeError(f"could not read {executable} from archive")
        return extracted.read()


def extract_ripgrep_binary(archive: Path, archive_type: str, executable: str, target: Path) -> None:
    if archive_type == "zip":
        payload = zip_member_bytes(archive, executable)
    elif archive_type == "tar.gz":
        payload = tar_member_bytes(archive, executable)
    else:
        raise RuntimeError(f"unsupported archive type: {archive_type}")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_target = target.with_suffix(target.suffix + ".tmp")
    tmp_target.write_bytes(payload)
    if not sys.platform.startswith("win"):
        tmp_target.chmod(tmp_target.stat().st_mode | 0o755)
    tmp_target.replace(target)


def install_portable_ripgrep(root: Path) -> dict[str, Any]:
    key, asset, manifest = ripgrep_asset(root)
    if not manifest:
        return {"ok": False, "status": "manifest-missing", "platform": key, "path": str(root / MANIFEST_REL)}
    if asset is None:
        return {"ok": False, "status": "unsupported-platform", "platform": key}

    expected_sha = str(asset.get("sha256", "")).lower()
    url = str(asset.get("url", ""))
    archive_name = str(asset.get("name", "ripgrep-archive"))
    archive_type = str(asset.get("archive_type", ""))
    executable = str(asset.get("executable", "rg.exe" if key.startswith("windows-") else "rg"))
    target = portable_ripgrep_binary(root, key)

    with tempfile.TemporaryDirectory(prefix="portable-rg-") as temp_dir:
        archive = Path(temp_dir) / archive_name
        try:
            download_file(url, archive)
        except (OSError, urllib.error.URLError) as exc:
            return {"ok": False, "status": "download-failed", "platform": key, "url": url, "error": str(exc)}
        actual_sha = sha256_file(archive).lower()
        if actual_sha != expected_sha:
            return {
                "ok": False,
                "status": "archive-hash-mismatch",
                "platform": key,
                "url": url,
                "expected_sha256": expected_sha,
                "actual_sha256": actual_sha,
            }
        try:
            extract_ripgrep_binary(archive, archive_type, executable, target)
        except (OSError, RuntimeError, tarfile.TarError, zipfile.BadZipFile) as exc:
            return {"ok": False, "status": "extract-failed", "platform": key, "error": str(exc)}

    binary_sha = sha256_file(target).lower()
    ok, version_text = run_ripgrep_version(target)
    expected_version = str(manifest.get("version", ""))
    if not ok or expected_version not in version_text:
        return {
            "ok": False,
            "status": "version-check-failed",
            "platform": key,
            "path": str(target),
            "version": version_text,
        }

    record = {
        "archive_sha256": expected_sha,
        "binary_sha256": binary_sha,
        "installed_at": now_utc(),
        "license": manifest.get("license", ""),
        "platform": key,
        "source": manifest.get("source", ""),
        "source_url": url,
        "tool": "ripgrep",
        "version": expected_version,
    }
    record_path = portable_ripgrep_record(root, key)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return {
        "ok": True,
        "status": "installed",
        "source": "portable",
        "platform": key,
        "path": str(target),
        "version": version_text,
        "binary_sha256": binary_sha,
        "archive_sha256": expected_sha,
    }
