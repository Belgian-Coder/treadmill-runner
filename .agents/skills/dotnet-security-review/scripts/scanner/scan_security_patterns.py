#!/usr/bin/env python3
"""Scan files for common risky security patterns."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import sarif_support


RULES = [
    ("SEC001", "high", r"\[AllowAnonymous\]", "AllowAnonymous attribute requires explicit review."),
    ("SEC002", "high", r"TrustServerCertificate\s*=\s*true", "TrustServerCertificate=true weakens TLS validation."),
    ("SEC003", "high", r"ServerCertificateCustomValidationCallback\s*=\s*.*true", "Certificate validation callback appears to allow all certificates."),
    ("SEC004", "medium", r"Password\s*=\s*[^;\n]+", "Hard-coded password-like connection string value."),
    ("SEC005", "medium", r"(ApiKey|ClientSecret|AccessToken)\s*[:=]\s*['\"][^'\"]{8,}", "Hard-coded secret-like assignment."),
    ("SEC006", "medium", r"dangerouslySetInnerHTML", "Raw HTML injection sink requires sanitization evidence."),
    ("SEC007", "medium", r"\beval\s*\(", "Dynamic code execution requires explicit review."),
    ("SEC008", "low", r"TODO[: ]+security", "Security TODO should be tracked before handoff."),
    ("SEC009", "high", r"\b(pickle\.loads|BinaryFormatter\b|JsonConvert\.DeserializeObject\s*<\s*object\s*>)", "Unsafe deserialization pattern requires explicit validation."),
    ("SEC010", "high", r"\b(os\.system|subprocess\.(Popen|run|call)|child_process\.(exec|spawn))\s*\(", "Command execution with dynamic input requires review."),
    ("SEC011", "medium", r"(\.\./|\.\.\\|Path\.Combine\s*\([^)]*request|send_file\s*\()", "Path traversal-prone file access requires containment checks."),
    ("SEC012", "medium", r"\b(?:requests|httpx)\s*\.\s*(?:get|post|put|patch|delete|request)\s*\([^)]*(url|uri|endpoint|request)|\b(?:await\s+)?(?-i:fetch)\s*\([^)]*(url|uri|endpoint|request)", "Potential SSRF sink requires allowlist or trusted input evidence."),
    ("SEC013", "medium", r"innerHTML\s*=", "Direct innerHTML assignment requires sanitization evidence."),
    ("SEC014", "medium", r"\bRedirect\s*\([^)]*(returnUrl|url|uri|request)", "Redirect target should be allowlisted to avoid open redirect."),
    ("SEC015", "high", r"\bAddMcpServer|\.mcp\.json|allowedTools|dangerously-skip-permissions", "Agent/tool configuration requires explicit trust-boundary review."),
    ("SEC016", "medium", r"prompt\s*[:=].*(system|developer|instruction)", "Prompt or instruction construction should be reviewed for injection boundaries."),
    ("SEC017", "high", r"\b(zipfile\.ZipFile|ZipArchive)\b.*\.(extractall|ExtractToDirectory)\s*\(", "Archive extraction requires zip-slip path containment checks before handling OOXML or uploaded archives."),
    ("SEC018", "medium", r"\b(docx|xlsx|pptx|ooxml|OfficeOpenXml|OpenXmlPackage)\b.*\b(extract|unzip|ZipArchive|ZipFile)\b", "Office document archive handling should reject zip-slip paths, symlinks, and oversized entries."),
    ("SEC019", "high", r"\bFromSqlRaw\s*\([^;\n]*(?:\+|\$\")", "FromSqlRaw with string concatenation or interpolation can bypass SQL parameterization."),
    ("SEC020", "medium", r"\b\w+\s*\.\s*(?:Get|Post|Put|Delete|Patch)(?:String|ByteArray|Stream)?Async\s*\([^;\n]*(?:request\.)?(?:url|uri|endpoint)\b", "HttpClient call with URL-like input may be an SSRF sink without allowlist evidence."),
    ("SEC021", "high", r"\b(?:MD5|SHA1|TripleDES|DES|RC2)(?:CryptoServiceProvider|Managed)?\s*(?:\.Create\s*\(|\()|\bHashAlgorithmName\.(?:MD5|SHA1)\b|\bRNGCryptoServiceProvider\s*\(", "Deprecated cryptographic API requires migration to a modern API."),
    ("SEC022", "medium", r"\b(?:AllowAnyOrigin\s*\(\s*\)\s*\.\s*AllowCredentials\s*\(\s*\)|AllowCredentials\s*\(\s*\)\s*\.\s*AllowAnyOrigin\s*\(\s*\)|SetIsOriginAllowed\s*\([^)]*=>\s*true\s*\))", "CORS policy appears to allow all origins and requires explicit review."),
    ("SEC023", "medium", r"\bCookieSecurePolicy\.(?:SameAsRequest|None)\b", "Cookie secure policy may allow cookies over HTTP and requires explicit review."),
    ("SEC024", "high", r"\b(?:RequireHttpsMetadata|ValidateIssuer|ValidateAudience|ValidateLifetime|ValidateIssuerSigningKey)\s*=\s*false\b", "JWT bearer validation or HTTPS metadata checks appear disabled and require explicit review."),
    ("SEC025", "medium", r"['\"]?(?:ApiKey|ClientSecret|SigningKey|ConnectionString|Password|Secret)['\"]?\s*:\s*['\"](?!REPLACE|CHANGE_ME|TODO|<|\$\{)[^'\"]{12,}['\"]", "Secret-like configuration value appears hard-coded."),
    ("SEC026", "medium", r"\b\w+\.Log(?:Trace|Debug|Information|Warning|Error|Critical)\s*\((?![^;\n]*(?:configured|isconfigured))[^;\n]*(?:api\s*key|apikey|connection\s*string|password|secret|signing\s*key)[^;\n]*,\s*[^;\n]*(?:apiKey|connectionString|password|secret|signingKey|clientSecret|accessToken)\b", "Log call appears to write a secret-bearing value."),
    ("SEC027", "medium", r"^\s*ENV\s+\S*(?:ConnectionStrings|ApiKey|ClientSecret|SigningKey|Password|Secret|AccessToken)\S*\s*=\s*(?!\$\{|REPLACE|CHANGE_ME|TODO|<)[^\s#]{8,}", "Docker image ENV appears to bake a secret-like value into the image."),
    ("SEC028", "medium", r"<\s*NuGetAudit\s*>\s*false\s*<\s*/\s*NuGetAudit\s*>|<\s*NuGetAuditMode\s*>\s*(?!all\s*<\s*/\s*NuGetAuditMode\s*>)[^<]+<\s*/\s*NuGetAuditMode\s*>", "NuGet audit appears disabled or limited to non-transitive dependency checks."),
    ("SEC029", "high", r"<\s*EnableUnsafeBinaryFormatterSerialization\s*>\s*true\s*<\s*/\s*EnableUnsafeBinaryFormatterSerialization\s*>", "Unsafe BinaryFormatter serialization switch appears enabled."),
    ("SEC030", "medium", r"<\s*add\b[^>]*\bvalue\s*=\s*['\"]http://(?!(?:localhost|127\.0\.0\.1|\[::1\])(?::|/|['\"]))[^'\"]+['\"][^>]*>|<\s*add\b[^>]*\ballowInsecureConnections\s*=\s*['\"]true['\"][^>]*>", "NuGet package source appears to allow insecure package transport."),
    ("SEC031", "medium", r"\busing\s+System\.Security\.Permissions\s*;|\[(?:assembly:\s*)?(?:AllowPartiallyTrustedCallers|SecurityPermission|PermissionSet|SecurityCritical|SecuritySafeCritical)\b|\busing\s+System\.Runtime\.Remoting\s*;|\bRemoting(?:Configuration|Services)\s*\.|\b(?:Tcp|Http|Ipc)Channel\s*\(|\busing\s+System\.EnterpriseServices\s*;|\bServicedComponent\b", "Deprecated .NET security, remoting, or EnterpriseServices surface requires migration review."),
    ("SEC032", "medium", r"\bHttpLoggingFields\.(?:All|RequestBody|ResponseBody)\b", "HTTP logging appears to include raw request or response bodies that may contain credentials, tokens, or PII."),
    ("SEC035", "medium", r"^(?!\s*#)[^\r\n]*?\b(?:NUGET_API_KEY|NUGET_AUTH_TOKEN|NUGET_TOKEN|NuGetApiKey)\b\s*[:=]\s*['\"]?(?!\s*(?:\$\{\{|\$\(|\$[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z_][A-Za-z0-9_]*%|REPLACE|CHANGE_ME|TODO|<))[^'\"\s#][^#\r\n]{7,}", "CI or pipeline NuGet credential appears hard-coded instead of using a secret or variable reference."),
]
TEXT_SUFFIXES = {".cs", ".cshtml", ".razor", ".config", ".json", ".xml", ".csproj", ".fsproj", ".vbproj", ".props", ".targets", ".js", ".jsx", ".ts", ".tsx", ".py", ".yml", ".yaml", ".md"}
DOCKERFILE_NAMES = {"dockerfile", "containerfile"}
SKIP_DIRS = {
    ".agents",
    ".claude",
    ".git",
    "automations",
    "bin",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "obj",
    "__pycache__",
}
SEVERITY_ORDER = {"informational": 0, "low": 1, "medium": 2, "high": 3}
CONFIDENCE_BY_SEVERITY = {"high": "high", "medium": "medium", "low": "low", "informational": "informational"}
DOCKER_FROM_RE = re.compile(r"^\s*FROM\s+(?P<image>\S+)", re.IGNORECASE)
DOTNET_PATCH_TAG_RE = re.compile(
    r"^mcr\.microsoft\.com/dotnet/(?:sdk|aspnet|runtime|runtime-deps):\d+\.\d+\.\d+(?:[-@]|$)",
    re.IGNORECASE,
)
DOTNET_CSHARP_SUFFIXES = {".cs", ".cshtml", ".razor"}
DOTNET_ECB_RE = re.compile(r"(?:=\s*|[(,]\s*)CipherMode\s*\.\s*ECB\b", re.IGNORECASE)
DOTNET_SMALL_RSA_RE = re.compile(
    r"\bRSA\s*\.\s*Create\s*\(\s*(?:512|1024)\s*\)"
    r"|\bRSACryptoServiceProvider\s*\(\s*(?:512|1024)\s*\)"
    r"|\brsa\w*\s*\.\s*KeySize\s*=\s*(?:512|1024)\b",
    re.IGNORECASE,
)
SUPPRESSION_RE = re.compile(
    r"dotnet-security-review:\s*ignore\s+(?P<rule>SEC\d+)\s+(?:because|rationale:)\s*(?P<reason>.+)",
    re.IGNORECASE,
)
GENERATED_PATH_RE = re.compile(r"(\.generated\.|/generated/|\\generated\\|\.min\.js$)", re.IGNORECASE)


def is_supported_text_path(path: Path) -> bool:
    name = path.name.lower()
    return (
        path.suffix.lower() in TEXT_SUFFIXES
        or name in DOCKERFILE_NAMES
        or name.startswith("dockerfile.")
        or name.startswith("containerfile.")
    )


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def git_changed_files(root: Path) -> list[Path]:
    probe = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if probe.returncode != 0:
        raise RuntimeError(probe.stderr.strip() or "not a Git repository")
    root = Path(probe.stdout.strip())
    completed = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMRT", "HEAD"],
        cwd=str(root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git diff failed")
    return [root / line.strip() for line in completed.stdout.splitlines() if line.strip()]


def path_matches_target(path: Path, target: Path) -> bool:
    try:
        resolved_path = path.resolve()
        resolved_target = target.resolve()
    except OSError:
        resolved_path = path.absolute()
        resolved_target = target.absolute()
    if resolved_target.is_file():
        return resolved_path == resolved_target
    try:
        resolved_path.relative_to(resolved_target)
        return True
    except ValueError:
        return False


def iter_target_files(targets: list[Path], changed_only: bool) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    skipped: list[str] = []
    if changed_only:
        for target in targets:
            root = target if target.is_dir() else target.parent
            files.extend(path for path in git_changed_files(root.resolve()) if path_matches_target(path, target))
    else:
        for target in targets:
            if target.is_file():
                files.append(target)
            else:
                for path in target.rglob("*"):
                    if path.is_file():
                        files.append(path)
    filtered: list[Path] = []
    seen: set[Path] = set()
    for path in files:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        if any(part in SKIP_DIRS for part in path.parts):
            skipped.append(f"{path}: skipped directory")
            continue
        if GENERATED_PATH_RE.search(str(path)):
            skipped.append(f"{path}: skipped generated file")
            continue
        if not is_supported_text_path(path):
            skipped.append(f"{path}: unsupported suffix")
            continue
        try:
            if b"\0" in path.read_bytes()[:4096]:
                skipped.append(f"{path}: skipped binary file")
                continue
        except OSError:
            skipped.append(f"{path}: unreadable")
            continue
        filtered.append(path)
    return filtered, skipped


def suppression_for_line(line: str, rule_id: str) -> str:
    match = SUPPRESSION_RE.search(line)
    if match and match.group("rule").upper() == rule_id:
        reason = match.group("reason").strip()
        return reason
    return ""


def finding_row(path: Path, line_number: int, line: str, rule_id: str, severity: str, message: str) -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "confidence": CONFIDENCE_BY_SEVERITY.get(severity, "low"),
        "path": str(path),
        "line": line_number,
        "message": message,
        "match": line.strip()[:240],
    }


def dockerfile_final_stage_findings(path: Path, lines: list[str]) -> list[dict[str, object]]:
    if not is_supported_text_path(path) or not (path.name.lower() == "containerfile" or "dockerfile" in path.name.lower()):
        return []
    from_rows: list[tuple[int, str, str]] = []
    for line_number, line in enumerate(lines, start=1):
        match = DOCKER_FROM_RE.search(line)
        if match:
            from_rows.append((line_number, line, match.group("image").lower()))
    if not from_rows:
        return []
    line_number, line, image = from_rows[-1]
    if "mcr.microsoft.com/dotnet/sdk" not in image:
        return []
    return [
        finding_row(
            path,
            line_number,
            line,
            "SEC033",
            "medium",
            "Final .NET container stage uses the SDK image instead of aspnet, runtime, or runtime-deps.",
        )
    ]


def dockerfile_patch_tag_findings(path: Path, lines: list[str]) -> list[dict[str, object]]:
    if not is_supported_text_path(path) or not (path.name.lower() == "containerfile" or "dockerfile" in path.name.lower()):
        return []
    findings: list[dict[str, object]] = []
    for line_number, line in enumerate(lines, start=1):
        match = DOCKER_FROM_RE.search(line)
        if not match:
            continue
        if not DOTNET_PATCH_TAG_RE.search(match.group("image")):
            continue
        findings.append(
            finding_row(
                path,
                line_number,
                line,
                "SEC034",
                "medium",
                "Official .NET container image is pinned to a patch tag instead of a floating major.minor servicing tag.",
            )
        )
    return findings


def dotnet_ecb_cipher_findings(path: Path, lines: list[str]) -> list[dict[str, object]]:
    if path.suffix.lower() not in DOTNET_CSHARP_SUFFIXES:
        return []
    findings: list[dict[str, object]] = []
    for line_number, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith(("//", "/*", "*")):
            continue
        if not DOTNET_ECB_RE.search(line):
            continue
        findings.append(
            finding_row(
                path,
                line_number,
                line,
                "SEC036",
                "high",
                "CipherMode.ECB leaks plaintext patterns and should be replaced with authenticated encryption such as AES-GCM.",
            )
        )
    return findings


def dotnet_small_rsa_key_findings(path: Path, lines: list[str]) -> list[dict[str, object]]:
    if path.suffix.lower() not in DOTNET_CSHARP_SUFFIXES:
        return []
    findings: list[dict[str, object]] = []
    for line_number, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith(("//", "/*", "*")):
            continue
        if not DOTNET_SMALL_RSA_RE.search(line):
            continue
        findings.append(
            finding_row(
                path,
                line_number,
                line,
                "SEC037",
                "high",
                "RSA key size below 2048 bits is no longer acceptable for security-sensitive use.",
            )
        )
    return findings


def scan_file(path: Path, skipped: list[str]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    findings: list[dict[str, object]] = []
    suppressed: list[dict[str, object]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        skipped.append(f"{path}: not utf-8 text")
        return findings, suppressed
    lines = text.splitlines()
    for line_number, line in enumerate(lines, start=1):
        for rule_id, severity, pattern, message in RULES:
            if re.search(pattern, line, flags=re.IGNORECASE):
                row = finding_row(path, line_number, line, rule_id, severity, message)
                rationale = suppression_for_line(line, rule_id)
                if rationale:
                    row["suppression_rationale"] = rationale
                    suppressed.append(row)
                else:
                    findings.append(row)
    for row in [
        *dockerfile_final_stage_findings(path, lines),
        *dockerfile_patch_tag_findings(path, lines),
        *dotnet_ecb_cipher_findings(path, lines),
        *dotnet_small_rsa_key_findings(path, lines),
    ]:
        line = lines[int(row["line"]) - 1] if 1 <= int(row["line"]) <= len(lines) else ""
        rationale = suppression_for_line(line, str(row["rule_id"]))
        if rationale:
            row["suppression_rationale"] = rationale
            suppressed.append(row)
        else:
            findings.append(row)
    return findings, suppressed


def summarize(findings: list[dict[str, object]]) -> dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0, "informational": 0}
    for finding in findings:
        counts[str(finding["severity"])] += 1
    return counts


def write_markdown(path: Path, payload: dict[str, object]) -> None:
    lines = [
        "# Security Pattern Scan",
        "",
        f"- Files scanned: {payload['files_scanned']}",
        f"- Findings: {len(payload['findings'])}",
        f"- High: {payload['summary']['high']}",
        f"- Medium: {payload['summary']['medium']}",
        f"- Low: {payload['summary']['low']}",
        "",
        "## Findings",
        "",
    ]
    if not payload["findings"]:
        lines.append("- None")
    for finding in payload["findings"]:
        lines.append(
            f"- `{finding['severity']}` `{finding['rule_id']}` {finding['path']}:{finding['line']} - {finding['message']}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def normalize_sarif_findings(paths: list[str]) -> tuple[list[dict[str, object]], dict[str, object]]:
    sarif_rows: list[dict[str, object]] = []
    summary: dict[str, object] = {"files": 0, "findings": 0, "levels": {}}
    if not paths:
        return sarif_rows, summary
    report = sarif_support.summarize_sarif([Path(path) for path in paths])
    levels = report.get("levels", {})
    for row in report.get("findings", []):
        if not isinstance(row, dict):
            continue
        severity = "high" if str(row.get("severity")) == "error" else "medium"
        sarif_rows.append(
            {
                "rule_id": str(row.get("rule_id") or "SARIF"),
                "severity": severity,
                "confidence": "medium",
                "path": str(row.get("path", "")),
                "line": row.get("line"),
                "message": str(row.get("message", "")),
                "match": f"SARIF:{row.get('tool', 'unknown')}",
                "source": "sarif",
            }
        )
    summary = {"files": len(paths), "findings": len(sarif_rows), "levels": levels}
    return sarif_rows, summary


def scan(args: argparse.Namespace) -> dict[str, object]:
    started_at = utc_now()
    targets = [Path(target).resolve() for target in args.target]
    files, skipped = iter_target_files(targets, args.changed_only)
    findings: list[dict[str, object]] = []
    suppressed: list[dict[str, object]] = []
    for path in files:
        file_findings, file_suppressed = scan_file(path, skipped)
        findings.extend(file_findings)
        suppressed.extend(file_suppressed)
    sarif_findings, sarif_summary = normalize_sarif_findings(getattr(args, "input_sarif", None) or [])
    findings.extend(sarif_findings)
    summary = summarize(findings)
    threshold = SEVERITY_ORDER.get(args.fail_on or "", 99)
    ok = not any(SEVERITY_ORDER[str(finding["severity"])] >= threshold for finding in findings)
    return {
        "schema_version": 1,
        "tool": "dotnet-security-review.scan_security_patterns",
        "ok": ok,
        "status": "passed" if ok else "failed",
        "started_at": started_at,
        "finished_at": utc_now(),
        "targets": [str(target) for target in targets],
        "changed_only": args.changed_only,
        "files_scanned": len(files),
        "boundary": "Pattern review only; this is not a full security audit.",
        "summary": summary,
        "sarif_summary": sarif_summary,
        "findings": findings,
        "suppressed_findings": suppressed if args.include_suppressed else [],
        "checks": [
            {
                "name": "security-patterns",
                "kind": "analysis",
                "ok": ok,
                "status": "passed" if ok else "failed",
                "summary": summary,
            }
        ],
        "skipped": skipped[:200],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Read-only when no --output-* flags are set. "
            "--changed-only uses local Git diff discovery for tracked changes under the requested target. "
            "--input-sarif reads local SARIF only and does not upload or mutate data. "
            "Output flags are write-capable caller-owned review evidence and may create parent directories."
        ),
    )
    parser.add_argument("--target", action="append", required=True, help="file or directory to scan; repeatable")
    parser.add_argument("--changed-only", action="store_true", help="scan tracked changed files under the requested target")
    parser.add_argument("--fail-on", choices=["low", "medium", "high"], help="return nonzero at or above this severity")
    parser.add_argument("--include-suppressed", action="store_true", help="include locally rationalized suppressions")
    parser.add_argument("--input-sarif", action="append", help="read local SARIF findings into the report")
    parser.add_argument("--output-json", help="write JSON evidence to this caller-owned path")
    parser.add_argument("--output-md", help="write Markdown evidence to this caller-owned path")
    parser.add_argument("--output-sarif", help="write SARIF evidence to this caller-owned path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = scan(args)
    except Exception as exc:
        payload = {
            "schema_version": 1,
            "tool": "dotnet-security-review.scan_security_patterns",
            "ok": False,
            "status": "failed",
            "started_at": utc_now(),
            "finished_at": utc_now(),
            "summary": {"error": str(exc)},
            "checks": [
                {
                    "name": "security-patterns",
                    "kind": "analysis",
                    "ok": False,
                    "status": "failed",
                    "summary": {"error": str(exc)},
                }
            ],
            "skipped": [],
            "findings": [],
        }
        if args.output_json:
            path = Path(args.output_json)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.output_json:
        path = Path(args.output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if args.output_md:
        write_markdown(Path(args.output_md), payload)
    if args.output_sarif:
        path = Path(args.output_sarif)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sarif_support.sarif_from_findings(payload["findings"], "dotnet-security-review"), indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": payload["ok"], "files_scanned": payload["files_scanned"], "summary": payload["summary"]}, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
