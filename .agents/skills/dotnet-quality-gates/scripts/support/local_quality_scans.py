"""Local quality scan helpers for validate_local_quality."""
from __future__ import annotations

import re
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from support.csharp_concurrency_scans import csharp_concurrency_findings
from support.csharp_linq_scans import csharp_linq_findings
from support.csharp_suppression_scans import csharp_static_scan_findings
from support.project_file_scans import project_file_findings
SKIP_DIRS = {".git", "bin", "obj", "node_modules", "dist", "build", "coverage", "__pycache__", ".venv", "venv"}
SLOP_FILE_SUFFIXES = {".cs", ".csproj", ".fs", ".fsproj", ".props", ".targets", ".vb", ".vbproj"}
SLOP_SEVERITY_RANK = {"warning": 1, "error": 2}
SNAPSHOT_RECEIVED_MARKER = ".received."
SNAPSHOT_VERIFIED_MARKER = ".verified."
SLOP_RULES = [
    {
        "rule_id": "SW001",
        "severity": "error",
        "message": "disabled test detected",
        "pattern": re.compile(r"\[(?:Fact|Theory|Test|TestCase)\s*\([^\]]*\bSkip\s*=|\[(?:Ignore|Explicit)\b", re.IGNORECASE),
        "test_only": False,
    },
    {
        "rule_id": "SW002",
        "severity": "warning",
        "message": "warning suppression detected",
        "pattern": re.compile(r"#pragma\s+warning\s+disable", re.IGNORECASE),
        "test_only": False,
    },
    {
        "rule_id": "SW003",
        "severity": "error",
        "message": "empty catch block detected",
        "pattern": re.compile(r"catch\s*(?:\([^)]*\))?\s*\{\s*\}", re.IGNORECASE | re.DOTALL),
        "test_only": False,
    },
    {
        "rule_id": "SW004",
        "severity": "warning",
        "message": "arbitrary delay in test code detected",
        "pattern": re.compile(r"\b(?:Task\.Delay|Thread\.Sleep)\s*\(", re.IGNORECASE),
        "test_only": True,
    },
    {
        "rule_id": "SW005",
        "severity": "warning",
        "message": "project warning policy weakening detected",
        "pattern": re.compile(r"<TreatWarningsAsErrors>\s*false\s*</TreatWarningsAsErrors>|<NoWarn>|NoWarn\s*=", re.IGNORECASE),
        "test_only": False,
    },
    {
        "rule_id": "SW006",
        "severity": "warning",
        "message": "central package version bypass detected",
        "pattern": re.compile(r"\bVersionOverride\s*=", re.IGNORECASE),
        "test_only": False,
    },
    {
        "rule_id": "SW007",
        "severity": "error",
        "message": "async void xUnit test detected",
        "pattern": re.compile(r"\[(?:Fact|Theory)\b[^\]]*\][\s\S]{0,600}?\basync\s+void\s+\w+\s*\(", re.IGNORECASE),
        "test_only": True,
    },
    {
        "rule_id": "SW008",
        "severity": "error",
        "message": "method mixes xUnit Fact and Theory attributes",
        "pattern": re.compile(r"\[Fact\b[^\]]*\]\s*(?:\r?\n\s*)?\[Theory\b|\[Theory\b[^\]]*\]\s*(?:\r?\n\s*)?\[Fact\b", re.IGNORECASE),
        "test_only": True,
    },
    {
        "rule_id": "SW009",
        "severity": "warning",
        "message": "framework or infrastructure type mocked in test code",
        "pattern": re.compile(r"(?:Substitute\.For|new\s+Mock)\s*<\s*(?:HttpClient|\w*DbContext)\s*>", re.IGNORECASE),
        "test_only": True,
    },
    {
        "rule_id": "SW010",
        "severity": "warning",
        "message": "hardcoded local infrastructure connection string detected in test code",
        "pattern": re.compile(r'"[^"]*\b(?:Host|Server|Data Source)\s*=\s*(?:localhost|127\.0\.0\.1)\b[^"]*"', re.IGNORECASE),
        "test_only": True,
    },
    {
        "rule_id": "SW011",
        "severity": "error",
        "message": "direct sync-over-async call detected",
        "pattern": re.compile(
            r"\b\w+Async\s*\([^;\n{}]*\)\s*\.\s*(?:Result\b|Wait\s*\(|GetAwaiter\s*\(\s*\)\s*\.\s*GetResult\s*\()",
            re.IGNORECASE,
        ),
        "test_only": False,
    },
    {
        "rule_id": "SW012",
        "severity": "warning",
        "message": "Task.Run wraps already-async work",
        "pattern": re.compile(
            r"\bTask\.Run\s*\(\s*async\s*(?:\([^)]*\)|\w+)?\s*=>\s*await\s+\w+Async\s*\(",
            re.IGNORECASE,
        ),
        "test_only": False,
    },
    {
        "rule_id": "SW013",
        "severity": "warning",
        "message": "EF-style async call without CancellationToken detected",
        "pattern": re.compile(
            r"\b(?:ToList|ToArray|First|FirstOrDefault|Single|SingleOrDefault|Any|All|Count|LongCount|SaveChanges)Async\s*\(\s*\)",
            re.IGNORECASE,
        ),
        "test_only": False,
    },
    {
        "rule_id": "SW014",
        "severity": "warning",
        "message": "direct legacy HTTP client construction detected",
        "pattern": re.compile(
            r"\b(?:new\s+(?:HttpClient|WebClient)\s*\(\s*\)|(?:HttpClient|WebClient)\s+\w+\s*=\s*new\s*\(\s*\))",
            re.IGNORECASE,
        ),
        "test_only": False,
    },
    {
        "rule_id": "SW015",
        "severity": "warning",
        "message": "superseded Microsoft.Extensions.Http.Polly package detected",
        "pattern": re.compile(
            r"<(?:PackageReference|PackageVersion)\b[^>]*(?:Include|Update)\s*=\s*[\"']Microsoft\.Extensions\.Http\.Polly[\"']",
            re.IGNORECASE,
        ),
        "test_only": False,
    },
    {
        "rule_id": "SW016",
        "severity": "warning",
        "message": "unstructured ILogger message template detected",
        "pattern": re.compile(
            r"\b\w+\.Log(?:Trace|Debug|Information|Warning|Error|Critical)\s*\([^;\n]*(?:\$\s*\"|\"[^\"]*\"\s*\+)",
            re.IGNORECASE,
        ),
        "test_only": False,
    },
    {
        "rule_id": "SW017",
        "severity": "warning",
        "message": "secret-bearing options property uses init accessor",
        "pattern": re.compile(
            r"\b(?:public|internal)\s+(?:required\s+)?(?:string|SecureString)\??\s+"
            r"(?:\w*(?:ApiKey|ClientSecret|SigningKey|ConnectionString|Password|AccessToken)\w*|Secret)"
            r"\s*\{\s*get\s*;\s*init\s*;",
            re.IGNORECASE,
        ),
        "test_only": False,
    },
    {
        "rule_id": "SW018",
        "severity": "warning",
        "message": "Minimal API Results factory detected; prefer TypedResults for OpenAPI metadata",
        "pattern": re.compile(
            r"(?<!Typed)\bResults\.(?:Ok|Created|Accepted|NoContent|NotFound|BadRequest|Problem|ValidationProblem|Conflict|Unauthorized|Forbid|StatusCode|Json|Text|File|Bytes|Stream|Redirect|LocalRedirect)\s*\(",
            re.IGNORECASE,
        ),
        "test_only": False,
    },
    {
        "rule_id": "SW023",
        "severity": "warning",
        "message": "legacy ASP.NET API versioning package detected",
        "pattern": re.compile(
            r"<(?:PackageReference|PackageVersion)\b[^>]*(?:Include|Update)\s*=\s*[\"']Microsoft\.AspNetCore\.Mvc\.Versioning(?:\.ApiExplorer)?[\"']",
            re.IGNORECASE,
        ),
        "test_only": False,
    },
    {"rule_id": "SW051", "severity": "warning", "message": "hallucinated EntityFrameworkCore package ID detected; use Microsoft.EntityFrameworkCore", "pattern": re.compile(r"<(?:PackageReference|PackageVersion)\b[^>]*(?:Include|Update)\s*=\s*[\"']EntityFrameworkCore[\"']", re.IGNORECASE), "test_only": False},
]
MIDDLEWARE_CALL_PATTERNS = {
    "routing": re.compile(r"\.\s*UseRouting\s*\(", re.IGNORECASE),
    "cors": re.compile(r"\.\s*UseCors\s*\(", re.IGNORECASE),
    "authentication": re.compile(r"\.\s*UseAuthentication\s*\(", re.IGNORECASE),
    "authorization": re.compile(r"\.\s*UseAuthorization\s*\(", re.IGNORECASE),
    "output_cache": re.compile(r"\.\s*UseOutputCache\s*\(", re.IGNORECASE),
    "rate_limiter": re.compile(r"\.\s*UseRateLimiter\s*\(", re.IGNORECASE),
    "endpoint_mapping": re.compile(
        r"\.\s*(?:MapControllers|MapControllerRoute|MapDefaultControllerRoute|MapRazorPages|MapGet|MapPost|MapPut|MapDelete|MapPatch|MapMethods|MapFallback|MapHealthChecks|MapHub)\s*\(",
        re.IGNORECASE,
    ),
    "reverse_proxy_mapping": re.compile(r"\.\s*MapReverseProxy\s*\(", re.IGNORECASE),
}
BACKGROUND_SERVICE_PATTERN = re.compile(r":\s*(?:[\w.]+\.)?BackgroundService\b", re.IGNORECASE)
EXECUTE_ASYNC_PATTERN = re.compile(r"\bExecuteAsync\s*\(", re.IGNORECASE)
THREAD_SLEEP_PATTERN = re.compile(r"\bThread\.Sleep\s*\(", re.IGNORECASE)
TASK_DELAY_PATTERN = re.compile(r"\bTask\.Delay\s*\(", re.IGNORECASE)
EFCORE_DATABASE_MIGRATE_PATTERN = re.compile(r"\.\s*Database\s*\.\s*Migrate(?:Async)?\s*\(", re.IGNORECASE)
EFCORE_DATABASE_ENSURE_CREATED_PATTERN = re.compile(r"\.\s*Database\s*\.\s*EnsureCreated(?:Async)?\s*\(", re.IGNORECASE)
HTTPCLIENT_BASE_ADDRESS_URI_PATTERN = re.compile(
    r"\.\s*BaseAddress\s*=\s*new(?:\s+Uri)?\s*\(\s*([\"'])(?P<uri>https?://[^\"']+)\1\s*\)",
    re.IGNORECASE,
)
STANDARD_RESILIENCE_HANDLER_PATTERN = re.compile(r"\.\s*AddStandardResilienceHandler\s*\(", re.IGNORECASE)
LEGACY_POLLY_HTTP_POLICY_PATTERNS = [
    re.compile(r"\.\s*AddTransientHttpErrorPolicy\s*\(", re.IGNORECASE),
    re.compile(r"\bIAsyncPolicy\s*<\s*HttpResponseMessage\s*>", re.IGNORECASE),
    re.compile(r"\bHttpPolicyExtensions\s*\.\s*HandleTransientHttpError\s*\(", re.IGNORECASE),
]
SEMANTIC_KERNEL_CHAT_PROVIDER_PATTERNS = [
    ("azure", re.compile(r"\.\s*AddAzureOpenAIChatCompletion\s*\(", re.IGNORECASE)),
    ("openai", re.compile(r"\.\s*AddOpenAIChatCompletion\s*\(", re.IGNORECASE)),
]
SEMANTIC_KERNEL_SERVICE_ID_PATTERN = re.compile(r"\bserviceId\s*:", re.IGNORECASE)
KERNEL_FUNCTION_ATTRIBUTE_PATTERN = re.compile(r"\[KernelFunction(?:Attribute)?(?:\s*\([^\]]*\))?\]", re.IGNORECASE)
KERNEL_FUNCTION_ASYNC_SIGNATURE_PATTERN = re.compile(
    r"(?:\[[^\]]+\]\s*)*"
    r"(?:(?:public|private|protected|internal|static|virtual|override|sealed|async|partial|extern|new)\s+)*"
    r"(?:[\w.]+\.)?(?:Task|ValueTask|IAsyncEnumerable)\s*(?:<[^(\n;{]+>)?\s+"
    r"\w+\s*\((?P<params>[^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)
HYBRID_CACHE_ENTRY_OPTIONS_PATTERN = re.compile(r"new\s+HybridCacheEntryOptions\s*\{", re.IGNORECASE)
HYBRID_CACHE_TIMESPAN_PATTERN = re.compile(
    r"\b(?P<name>Expiration|LocalCacheExpiration)\s*=\s*TimeSpan\.From(?P<unit>Seconds|Minutes|Hours|Days)\s*\(\s*(?P<value>\d+(?:\.\d+)?)\s*\)",
    re.IGNORECASE,
)
PROTOBUF_ITEM_PATTERN = re.compile(r"<\s*Protobuf\b[^>]*>", re.IGNORECASE)
GRPC_SERVICES_ATTRIBUTE_PATTERN = re.compile(r"\bGrpcServices\s*=", re.IGNORECASE)
OPENAPI_PACKAGE_PATTERN = re.compile(
    r"<(?:PackageReference|PackageVersion)\b[^>]*(?:Include|Update)\s*=\s*[\"']Microsoft\.AspNetCore\.OpenApi[\"'][^>]*",
    re.IGNORECASE,
)
SWASHBUCKLE_PACKAGE_PATTERN = re.compile(
    r"<(?:PackageReference|PackageVersion)\b[^>]*(?:Include|Update)\s*=\s*[\"']Swashbuckle\.AspNetCore[\"'][^>]*",
    re.IGNORECASE,
)
TARGET_FRAMEWORK_PATTERN = re.compile(r"<TargetFrameworks?>\s*([^<]+)</TargetFrameworks?>", re.IGNORECASE)
PACKAGE_VERSION_ATTR_PATTERN = re.compile(r"\bVersion\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
PUBLIC_API_ANALYZERS_PATTERN = re.compile(
    r"<(?:PackageReference|PackageVersion)\b[^>]*(?:Include|Update)\s*=\s*[\"']Microsoft\.CodeAnalysis\.PublicApiAnalyzers[\"']",
    re.IGNORECASE,
)
PUBLIC_API_TRACKING_FILES = ("PublicAPI.Shipped.txt", "PublicAPI.Unshipped.txt")
PROPERTY_GROUP_PATTERN = re.compile(r"<PropertyGroup\b[^>]*>[\s\S]*?</PropertyGroup>", re.IGNORECASE)
APICOMPAT_SUPPRESSION_PROPERTY_PATTERN = re.compile(r"<ApiCompatSuppressionFile\b", re.IGNORECASE)
PACKAGE_LICENSE_EXPRESSION_PATTERN = re.compile(r"<PackageLicenseExpression\b", re.IGNORECASE)
PACKAGE_LICENSE_FILE_PATTERN = re.compile(r"<PackageLicenseFile\b", re.IGNORECASE)
AOT_OR_TRIM_PROPERTY_PATTERN = re.compile(
    r"<(?P<name>PublishAot|IsAotCompatible|PublishTrimmed)>\s*true\s*</(?P=name)>",
    re.IGNORECASE,
)
JSON_SERIALIZER_SINGLE_ARGUMENT_PATTERN = re.compile(
    r"\b(?:System\.Text\.Json\.)?JsonSerializer\s*\.\s*(?:Serialize|Deserialize)(?:\s*<[^>\r\n;]+>)?\s*\(\s*[^,;()]+\s*\)",
    re.IGNORECASE,
)
JSON_SERIALIZER_CONTEXT_CLASS_PATTERN = re.compile(
    r"\b(?P<modifiers>(?:(?:public|private|protected|internal|sealed|abstract|static|unsafe|new|partial|file)\s+)*)"
    r"class\s+\w+(?:\s*<[^>{;]+>)?\s*:\s*(?:global::)?(?:[\w.]+\.)?JsonSerializerContext\b",
    re.IGNORECASE,
)
LOGGER_MESSAGE_STRUCT_PATTERN = re.compile(
    r"\[LoggerMessage(?:Attribute)?(?:\s*\([^\]]*\))?\]\s*"
    r"(?:(?:public|private|protected|internal|static|partial|readonly|ref|unsafe|new)\s+)*"
    r"struct\s+\w+\b",
    re.IGNORECASE | re.DOTALL,
)
GENERATED_REGEX_ATTRIBUTE_PATTERN = re.compile(r"\[\s*GeneratedRegex(?:Attribute)?\b", re.IGNORECASE)
GENERATED_REGEX_SIGNATURE_PATTERN = re.compile(
    r"\s*(?P<modifiers>(?:(?:public|private|protected|internal|static|partial|extern|new|unsafe)\s+)*)"
    r"(?P<return_type>(?:global::)?(?:[\w.]+\.)?\w+\??)\s+"
    r"\w+\s*\(",
    re.IGNORECASE,
)
TEST_PROJECT_PATH_MARKER_PATTERN = re.compile(
    r"(?:^|[._-])(?:tests?|unittests|integrationtests|functionaltests|e2etests|acceptancetests)(?:$|[._-])",
    re.IGNORECASE,
)
TEST_ATTRIBUTE_PATTERN = re.compile(r"\[(?:Fact|Theory|Test|TestCase|TestMethod)\b", re.IGNORECASE)
def status(ok: bool) -> str: return "passed" if ok else "failed"


def iter_snapshot_artifacts(targets: list[str]) -> tuple[list[Path], list[Path], set[Path]]:
    received: list[Path] = []
    verified: list[Path] = []
    roots: set[Path] = set()
    for raw in targets:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"snapshot target not found: {path}")
        root = path.parent if path.is_file() else path
        roots.add(root)
        candidates = [path] if path.is_file() else sorted(path.rglob("*"), key=lambda item: item.as_posix().lower())
        for item in candidates:
            if not item.is_file() or any(part in SKIP_DIRS for part in item.parts):
                continue
            name = item.name.lower()
            if SNAPSHOT_RECEIVED_MARKER in name:
                received.append(item)
            elif SNAPSHOT_VERIFIED_MARKER in name:
                verified.append(item)
    return received, verified, roots


def file_has_live_pattern(path: Path, marker: str) -> bool:
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and marker in stripped:
            return True
    return False


def any_root_file_has_pattern(roots: set[Path], filename: str, marker: str) -> bool:
    return any(file_has_live_pattern(root / filename, marker) for root in roots)


def snapshot_artifact_check(targets: list[str], require_gitignore: bool = False) -> dict[str, Any]:
    started = time.perf_counter()
    received, verified, roots = iter_snapshot_artifacts(targets)
    gitignore_received = any_root_file_has_pattern(roots, ".gitignore", SNAPSHOT_RECEIVED_MARKER)
    gitattributes_verified = any_root_file_has_pattern(roots, ".gitattributes", SNAPSHOT_VERIFIED_MARKER)
    failures: list[str] = []
    warnings: list[str] = []
    if received:
        failures.append(f"unapproved snapshot received files found: {len(received)}")
    if require_gitignore and (received or verified) and not gitignore_received:
        failures.append("missing .gitignore pattern for *.received.*")
    if verified and not gitattributes_verified:
        warnings.append("verified snapshots found without .gitattributes pattern for clean diffs")
    ok = not failures
    return {
        "name": "snapshot-artifact-check",
        "kind": "analysis",
        "ok": ok,
        "status": status(ok),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "summary": {
            "targets": len(targets),
            "verified_files": len(verified),
            "received_files": len(received),
            "gitignore_received_pattern": gitignore_received,
            "gitattributes_verified_pattern": gitattributes_verified,
            "require_gitignore": require_gitignore,
        },
        "format": "snapshot-artifacts",
        "evidence_paths": [str(path) for path in (received + verified)[:100]],
        "received_files": [str(path) for path in received[:100]],
        "verified_files": [str(path) for path in verified[:100]],
        "failures": failures,
        "warnings": warnings,
    }


def iter_slop_files(targets: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in targets:
        path = Path(raw).expanduser().resolve()
        if path.is_file() and path.suffix.lower() in SLOP_FILE_SUFFIXES:
            files.append(path)
        elif path.is_dir():
            for item in sorted(path.rglob("*"), key=lambda candidate: candidate.as_posix().lower()):
                if item.is_file() and item.suffix.lower() in SLOP_FILE_SUFFIXES and not any(part in SKIP_DIRS for part in item.parts):
                    files.append(item)
        elif not path.exists():
            raise FileNotFoundError(f"slop scan target not found: {path}")
    return files


def is_test_file(path: Path) -> bool:
    normalized = path.as_posix().lower()
    return "test" in path.stem.lower() or "/test/" in normalized or "/tests/" in normalized or normalized.endswith(".tests.cs")


def line_for_offset(text: str, offset: int) -> int: return text.count("\n", 0, offset) + 1


def line_snippet(text: str, line_number: int) -> str:
    lines = text.splitlines()
    return lines[line_number - 1].strip()[:180] if 1 <= line_number <= len(lines) else ""


def strip_csharp_comments_preserve_offsets(text: str) -> str:
    def replace_comment(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group(0))

    return re.sub(r"//[^\r\n]*|/\*[\s\S]*?\*/", replace_comment, text)


def strip_xml_comments_preserve_offsets(text: str) -> str:
    def replace_comment(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group(0))

    return re.sub(r"<!--[\s\S]*?-->", replace_comment, text)


def first_middleware_call_offset(text: str, call_name: str) -> int | None:
    return match.start() if (match := MIDDLEWARE_CALL_PATTERNS[call_name].search(text)) else None


def middleware_order_findings(path: Path, text: str) -> list[dict[str, Any]]:
    if path.suffix.lower() != ".cs":
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    routing = first_middleware_call_offset(code, "routing")
    cors = first_middleware_call_offset(code, "cors")
    authentication = first_middleware_call_offset(code, "authentication")
    authorization = first_middleware_call_offset(code, "authorization")
    output_cache = first_middleware_call_offset(code, "output_cache")
    rate_limiter = first_middleware_call_offset(code, "rate_limiter")
    endpoint_mapping = first_middleware_call_offset(code, "endpoint_mapping")
    reverse_proxy_mapping = first_middleware_call_offset(code, "reverse_proxy_mapping")
    findings: list[dict[str, Any]] = []
    if authorization is not None and routing is not None and authorization < routing:
        line_number = line_for_offset(text, authorization)
        findings.append(
            {
                "rule_id": "SW019",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "ASP.NET UseAuthorization is registered before UseRouting",
                "snippet": line_snippet(text, line_number),
            }
        )
    if cors is not None and authorization is not None and cors > authorization:
        line_number = line_for_offset(text, cors)
        findings.append(
            {
                "rule_id": "SW020",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "ASP.NET UseCors is registered after UseAuthorization",
                "snippet": line_snippet(text, line_number),
            }
        )
    if authorization is not None and authentication is not None and authorization < authentication:
        line_number = line_for_offset(text, authorization)
        findings.append(
            {
                "rule_id": "SW031",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "ASP.NET UseAuthorization is registered before UseAuthentication",
                "snippet": line_snippet(text, line_number),
            }
        )
    if output_cache is not None and routing is not None and output_cache < routing:
        line_number = line_for_offset(text, output_cache)
        findings.append(
            {
                "rule_id": "SW024",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "ASP.NET UseOutputCache is registered before UseRouting",
                "snippet": line_snippet(text, line_number),
            }
        )
    if output_cache is not None and cors is not None and output_cache < cors:
        line_number = line_for_offset(text, output_cache)
        findings.append(
            {
                "rule_id": "SW025",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "ASP.NET UseOutputCache is registered before UseCors",
                "snippet": line_snippet(text, line_number),
            }
        )
    if rate_limiter is not None and routing is not None and rate_limiter < routing:
        line_number = line_for_offset(text, rate_limiter)
        findings.append(
            {
                "rule_id": "SW033",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "ASP.NET UseRateLimiter is registered before UseRouting",
                "snippet": line_snippet(text, line_number),
            }
        )
    if rate_limiter is not None and authorization is not None and rate_limiter > authorization:
        line_number = line_for_offset(text, rate_limiter)
        findings.append(
            {
                "rule_id": "SW034",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "ASP.NET UseRateLimiter is registered after UseAuthorization",
                "snippet": line_snippet(text, line_number),
            }
        )
    if rate_limiter is not None and endpoint_mapping is not None and endpoint_mapping < rate_limiter:
        line_number = line_for_offset(text, endpoint_mapping)
        findings.append(
            {
                "rule_id": "SW035",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "ASP.NET endpoint mapping is registered before UseRateLimiter",
                "snippet": line_snippet(text, line_number),
            }
        )
    auth_offsets = [offset for offset in (authentication, authorization) if offset is not None]
    if reverse_proxy_mapping is not None and auth_offsets and reverse_proxy_mapping < min(auth_offsets):
        line_number = line_for_offset(text, reverse_proxy_mapping)
        findings.append(
            {
                "rule_id": "SW045",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "YARP MapReverseProxy is registered before authentication or authorization middleware",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def statement_segment(text: str, offset: int, max_chars: int = 500) -> str:
    end = text.find(";", offset)
    if end == -1:
        end = min(len(text), offset + max_chars)
    else:
        end = min(end + 1, offset + max_chars)
    return text[offset:end]


def background_service_delay_findings(path: Path, text: str, test_file: bool) -> list[dict[str, Any]]:
    if test_file or path.suffix.lower() != ".cs":
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for match in THREAD_SLEEP_PATTERN.finditer(code):
        line_number = line_for_offset(text, match.start())
        findings.append(
            {
                "rule_id": "SW021",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "Thread.Sleep blocks production .NET code; prefer an async delay with cancellation",
                "snippet": line_snippet(text, line_number),
            }
        )
    if not (BACKGROUND_SERVICE_PATTERN.search(code) and EXECUTE_ASYNC_PATTERN.search(code)):
        return findings
    for match in TASK_DELAY_PATTERN.finditer(code):
        segment = statement_segment(code, match.start())
        if "," in segment:
            continue
        line_number = line_for_offset(text, match.start())
        findings.append(
            {
                "rule_id": "SW022",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "BackgroundService Task.Delay is missing the stopping token",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def efcore_startup_migration_findings(path: Path, text: str, test_file: bool) -> list[dict[str, Any]]:
    if test_file or path.suffix.lower() != ".cs" or path.stem.lower() not in {"program", "startup"}:
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for match in EFCORE_DATABASE_MIGRATE_PATTERN.finditer(code):
        line_number = line_for_offset(text, match.start())
        findings.append(
            {
                "rule_id": "SW036",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "EF Core Database.Migrate is called from startup code; prefer migration bundles or idempotent deployment scripts",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def efcore_startup_schema_creation_findings(path: Path, text: str, test_file: bool) -> list[dict[str, Any]]:
    if test_file or path.suffix.lower() != ".cs" or path.stem.lower() not in {"program", "startup"}:
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for match in EFCORE_DATABASE_ENSURE_CREATED_PATTERN.finditer(code):
        line_number = line_for_offset(text, match.start())
        findings.append(
            {
                "rule_id": "SW037",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "EF Core Database.EnsureCreated is called from startup code; reserve schema creation for tests or explicit provisioning tools",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def httpclient_base_address_findings(path: Path, text: str, test_file: bool) -> list[dict[str, Any]]:
    if test_file or path.suffix.lower() != ".cs":
        return []
    findings: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith(("//", "/*", "*")):
            continue
        match = HTTPCLIENT_BASE_ADDRESS_URI_PATTERN.search(line)
        if not match:
            continue
        uri = match.group("uri")
        parsed = urlsplit(uri)
        if not parsed.path or parsed.path == "/" or parsed.path.endswith("/"):
            continue
        findings.append(
            {
                "rule_id": "SW038",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "HttpClient BaseAddress includes a path without a trailing slash; relative URIs can drop the final path segment",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def duplicate_standard_resilience_handler_findings(path: Path, text: str, test_file: bool) -> list[dict[str, Any]]:
    if test_file or path.suffix.lower() != ".cs":
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for match in STANDARD_RESILIENCE_HANDLER_PATTERN.finditer(code):
        segment = statement_segment(code, match.start(), max_chars=1200)
        calls = list(STANDARD_RESILIENCE_HANDLER_PATTERN.finditer(segment))
        if len(calls) < 2:
            continue
        duplicate_offset = match.start() + calls[1].start()
        line_number = line_for_offset(text, duplicate_offset)
        findings.append(
            {
                "rule_id": "SW039",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "AddStandardResilienceHandler is added more than once in the same HTTP client chain",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def legacy_polly_http_policy_findings(path: Path, text: str, test_file: bool) -> list[dict[str, Any]]:
    if test_file or path.suffix.lower() != ".cs":
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for pattern in LEGACY_POLLY_HTTP_POLICY_PATTERNS:
        for match in pattern.finditer(code):
            line_number = line_for_offset(text, match.start())
            findings.append(
                {
                    "rule_id": "SW040",
                    "severity": "warning",
                    "path": str(path),
                    "line": line_number,
                    "message": "legacy Polly v7 HTTP policy API detected; prefer Microsoft.Extensions.Http.Resilience standard handlers or Polly v8 pipelines",
                    "snippet": line_snippet(text, line_number),
                }
            )
    return findings


def semantic_kernel_chat_service_id_findings(path: Path, text: str, test_file: bool) -> list[dict[str, Any]]:
    if test_file or path.suffix.lower() != ".cs":
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    calls: list[dict[str, Any]] = []
    for provider, pattern in SEMANTIC_KERNEL_CHAT_PROVIDER_PATTERNS:
        for match in pattern.finditer(code):
            segment = statement_segment(code, match.start(), max_chars=1600)
            calls.append(
                {
                    "provider": provider,
                    "offset": match.start(),
                    "has_service_id": bool(SEMANTIC_KERNEL_SERVICE_ID_PATTERN.search(segment)),
                }
            )
    providers = {str(call["provider"]) for call in calls}
    if not {"azure", "openai"}.issubset(providers):
        return []
    findings: list[dict[str, Any]] = []
    for call in calls:
        if call["has_service_id"]:
            continue
        line_number = line_for_offset(text, int(call["offset"]))
        findings.append(
            {
                "rule_id": "SW041",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "mixed Semantic Kernel Azure OpenAI and OpenAI chat registrations should use explicit serviceId values",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def semantic_kernel_plugin_cancellation_token_findings(path: Path, text: str, test_file: bool) -> list[dict[str, Any]]:
    if test_file or path.suffix.lower() != ".cs":
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for attribute in KERNEL_FUNCTION_ATTRIBUTE_PATTERN.finditer(code):
        segment = code[attribute.end() : attribute.end() + 1200]
        signature = KERNEL_FUNCTION_ASYNC_SIGNATURE_PATTERN.search(segment)
        if not signature:
            continue
        if re.search(r"\bCancellationToken\b", signature.group("params"), re.IGNORECASE):
            continue
        offset = attribute.end() + signature.start()
        line_number = line_for_offset(text, offset)
        findings.append(
            {
                "rule_id": "SW042",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "Semantic Kernel async plugin function is missing a CancellationToken parameter",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def timespan_seconds(unit: str, value: str) -> float:
    multiplier = {
        "seconds": 1.0,
        "minutes": 60.0,
        "hours": 3600.0,
        "days": 86400.0,
    }[unit.lower()]
    return float(value) * multiplier


def hybrid_cache_expiration_findings(path: Path, text: str, test_file: bool) -> list[dict[str, Any]]:
    if test_file or path.suffix.lower() != ".cs":
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for initializer in HYBRID_CACHE_ENTRY_OPTIONS_PATTERN.finditer(code):
        segment = statement_segment(code, initializer.start(), max_chars=1000)
        values: dict[str, tuple[float, int]] = {}
        for match in HYBRID_CACHE_TIMESPAN_PATTERN.finditer(segment):
            values[match.group("name").lower()] = (
                timespan_seconds(match.group("unit"), match.group("value")),
                initializer.start() + match.start(),
            )
        expiration = values.get("expiration")
        local_expiration = values.get("localcacheexpiration")
        if expiration is None or local_expiration is None or local_expiration[0] <= expiration[0]:
            continue
        line_number = line_for_offset(text, local_expiration[1])
        findings.append(
            {
                "rule_id": "SW043",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "HybridCache LocalCacheExpiration is longer than Expiration; keep the L1 TTL shorter than the L2 TTL",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def protobuf_grpcservices_findings(path: Path, text: str, test_file: bool) -> list[dict[str, Any]]:
    if test_file or path.suffix.lower() not in {".csproj", ".props", ".targets"}:
        return []
    code = strip_xml_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for match in PROTOBUF_ITEM_PATTERN.finditer(code):
        item_text = match.group(0)
        if GRPC_SERVICES_ATTRIBUTE_PATTERN.search(item_text):
            continue
        line_number = line_for_offset(text, match.start())
        findings.append(
            {
                "rule_id": "SW044",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "gRPC Protobuf item is missing explicit GrpcServices; default Both can generate unused client or server code",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def target_framework_majors(text: str) -> set[int]:
    majors: set[int] = set()
    for match in TARGET_FRAMEWORK_PATTERN.finditer(text):
        for target in re.split(r"[;\s]+", match.group(1).strip()):
            version = re.match(r"net(\d+)\.0\b", target, re.IGNORECASE)
            if version:
                majors.add(int(version.group(1)))
    return majors


def package_major(package_text: str) -> int | None:
    match = PACKAGE_VERSION_ATTR_PATTERN.search(package_text)
    if not match:
        return None
    major = re.match(r"(\d+)", match.group(1).strip())
    return int(major.group(1)) if major else None


def openapi_package_findings(path: Path, text: str) -> list[dict[str, Any]]:
    if path.suffix.lower() != ".csproj":
        return []
    openapi_matches = list(OPENAPI_PACKAGE_PATTERN.finditer(text))
    swashbuckle_matches = list(SWASHBUCKLE_PACKAGE_PATTERN.finditer(text))
    findings: list[dict[str, Any]] = []
    if openapi_matches and swashbuckle_matches:
        line_number = line_for_offset(text, swashbuckle_matches[0].start())
        findings.append(
            {
                "rule_id": "SW026",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "project mixes Swashbuckle and Microsoft.AspNetCore.OpenApi packages",
                "snippet": line_snippet(text, line_number),
            }
        )
    target_majors = target_framework_majors(text)
    if len(target_majors) != 1:
        return findings
    expected_major = next(iter(target_majors))
    for match in openapi_matches:
        actual_major = package_major(match.group(0))
        if actual_major is None or actual_major == expected_major:
            continue
        line_number = line_for_offset(text, match.start())
        findings.append(
            {
                "rule_id": "SW027",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "Microsoft.AspNetCore.OpenApi package major does not match target framework major",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def public_api_tracking_findings(path: Path, text: str) -> list[dict[str, Any]]:
    if path.suffix.lower() != ".csproj" or not PUBLIC_API_ANALYZERS_PATTERN.search(text):
        return []
    findings: list[dict[str, Any]] = []
    project_line = 1
    package_match = PUBLIC_API_ANALYZERS_PATTERN.search(text)
    if package_match:
        project_line = line_for_offset(text, package_match.start())
    for filename in PUBLIC_API_TRACKING_FILES:
        tracking_file = path.parent / filename
        if not tracking_file.exists():
            findings.append(
                {
                    "rule_id": "SW028",
                    "severity": "warning",
                    "path": str(path),
                    "line": project_line,
                    "message": f"PublicApiAnalyzers project is missing {filename}",
                    "snippet": line_snippet(text, project_line),
                }
            )
            continue
        first_nonblank = ""
        for line in tracking_file.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            if line.strip():
                first_nonblank = line.strip()
                break
        if first_nonblank != "#nullable enable":
            findings.append(
                {
                    "rule_id": "SW029",
                    "severity": "warning",
                    "path": str(tracking_file),
                    "line": 1,
                    "message": f"{filename} is missing #nullable enable header",
                    "snippet": line_snippet(tracking_file.read_text(encoding="utf-8-sig", errors="replace"), 1),
                }
            )
    return findings


def apicompat_suppression_findings(path: Path, text: str) -> list[dict[str, Any]]:
    if path.suffix.lower() not in {".csproj", ".props", ".targets"}:
        return []
    code = strip_xml_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for group in PROPERTY_GROUP_PATTERN.finditer(code):
        match = APICOMPAT_SUPPRESSION_PROPERTY_PATTERN.search(group.group(0))
        if not match:
            continue
        offset = group.start() + match.start()
        line_number = line_for_offset(text, offset)
        findings.append(
            {
                "rule_id": "SW030",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "ApiCompatSuppressionFile must be an ItemGroup item, not a PropertyGroup property",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def nuget_packaging_findings(path: Path, text: str) -> list[dict[str, Any]]:
    if path.suffix.lower() not in {".csproj", ".props", ".targets"}:
        return []
    code = strip_xml_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for group in PROPERTY_GROUP_PATTERN.finditer(code):
        group_text = group.group(0)
        expression = PACKAGE_LICENSE_EXPRESSION_PATTERN.search(group_text)
        license_file = PACKAGE_LICENSE_FILE_PATTERN.search(group_text)
        if expression is None or license_file is None:
            continue
        offset = group.start() + license_file.start()
        line_number = line_for_offset(text, offset)
        findings.append(
            {
                "rule_id": "SW032",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "NuGet package metadata sets both PackageLicenseExpression and PackageLicenseFile",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def aot_or_trim_enabled_roots(files: list[Path]) -> list[Path]:
    roots: list[Path] = []
    for path in files:
        if path.suffix.lower() not in {".csproj", ".props", ".targets"}:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        code = strip_xml_comments_preserve_offsets(text)
        if AOT_OR_TRIM_PROPERTY_PATTERN.search(code):
            roots.append(path.parent)
    return roots


def path_is_under_any(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def reflection_json_serialization_findings(
    path: Path,
    text: str,
    test_file: bool,
    aot_roots: list[Path],
) -> list[dict[str, Any]]:
    if test_file or path.suffix.lower() != ".cs" or not path_is_under_any(path, aot_roots):
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for match in JSON_SERIALIZER_SINGLE_ARGUMENT_PATTERN.finditer(code):
        line_number = line_for_offset(text, match.start())
        findings.append(
            {
                "rule_id": "SW046",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "System.Text.Json call in an AOT or trimming-enabled project omits source-generated type info",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def json_serializer_context_partial_findings(path: Path, text: str, test_file: bool) -> list[dict[str, Any]]:
    if test_file or path.suffix.lower() != ".cs":
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for match in JSON_SERIALIZER_CONTEXT_CLASS_PATTERN.finditer(code):
        if re.search(r"\bpartial\b", match.group("modifiers"), re.IGNORECASE):
            continue
        line_number = line_for_offset(text, match.start())
        findings.append(
            {
                "rule_id": "SW047",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "JsonSerializerContext source-generation class is missing the partial modifier",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def logger_message_struct_findings(path: Path, text: str, test_file: bool) -> list[dict[str, Any]]:
    if test_file or path.suffix.lower() != ".cs":
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for match in LOGGER_MESSAGE_STRUCT_PATTERN.finditer(code):
        line_number = line_for_offset(text, match.start())
        findings.append(
            {
                "rule_id": "SW048",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "LoggerMessage source-generation container is declared as a struct; use a partial class",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def csharp_attribute_end(text: str, start: int) -> int | None:
    in_string = False
    string_quote = ""
    verbatim = False
    index = start + 1
    while index < len(text):
        char = text[index]
        if in_string:
            if verbatim and char == '"' and index + 1 < len(text) and text[index + 1] == '"':
                index += 2
                continue
            if char == string_quote:
                in_string = False
                verbatim = False
            elif not verbatim and char == "\\":
                index += 2
                continue
            index += 1
            continue
        if char in {'"', "'"}:
            string_quote = char
            verbatim = char == '"' and index > 0 and text[index - 1] == "@"
            in_string = True
        elif char == "]":
            return index + 1
        index += 1
    return None


def generated_regex_method_shape_findings(path: Path, text: str, test_file: bool) -> list[dict[str, Any]]:
    if test_file or path.suffix.lower() != ".cs":
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    findings: list[dict[str, Any]] = []
    for attribute in GENERATED_REGEX_ATTRIBUTE_PATTERN.finditer(code):
        attribute_end = csharp_attribute_end(code, attribute.start())
        if attribute_end is None:
            continue
        signature = GENERATED_REGEX_SIGNATURE_PATTERN.match(code[attribute_end : attribute_end + 400])
        if signature is None:
            continue
        modifiers = {part.lower() for part in signature.group("modifiers").split()}
        return_type = signature.group("return_type").removeprefix("global::").split(".")[-1].rstrip("?")
        if {"static", "partial"}.issubset(modifiers) and return_type == "Regex":
            continue
        line_number = line_for_offset(text, attribute.start())
        findings.append(
            {
                "rule_id": "SW049",
                "severity": "warning",
                "path": str(path),
                "line": line_number,
                "message": "GeneratedRegex source-generation method must be static partial and return Regex",
                "snippet": line_snippet(text, line_number),
            }
        )
    return findings


def production_test_attribute_findings(path: Path, text: str) -> list[dict[str, Any]]:
    if path.suffix.lower() != ".cs" or any(TEST_PROJECT_PATH_MARKER_PATTERN.search(part) for part in path.parts[:-1]):
        return []
    code = strip_csharp_comments_preserve_offsets(text)
    return [
        {
            "rule_id": "SW050",
            "severity": "warning",
            "path": str(path),
            "line": line_for_offset(text, match.start()),
            "message": "Test framework attribute appears in production-shaped source; move tests to a test project",
            "snippet": line_snippet(text, line_for_offset(text, match.start())),
        }
        for match in TEST_ATTRIBUTE_PATTERN.finditer(code)
    ]

def slop_scan_check(targets: list[str], fail_on: str = "error") -> dict[str, Any]:
    started = time.perf_counter()
    fail_rank = SLOP_SEVERITY_RANK.get(fail_on, SLOP_SEVERITY_RANK["error"])
    files = iter_slop_files(targets)
    aot_roots = aot_or_trim_enabled_roots(files)
    findings: list[dict[str, Any]] = []
    for path in files:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        test_file = is_test_file(path)
        for rule in SLOP_RULES:
            if rule.get("test_only") and not test_file:
                continue
            pattern = rule["pattern"]
            assert isinstance(pattern, re.Pattern)
            for match in pattern.finditer(text):
                line_number = line_for_offset(text, match.start())
                findings.append(
                    {
                        "rule_id": rule["rule_id"],
                        "severity": rule["severity"],
                        "path": str(path),
                        "line": line_number,
                        "message": rule["message"],
                        "snippet": line_snippet(text, line_number),
                    }
                )
        findings.extend(middleware_order_findings(path, text))
        findings.extend(background_service_delay_findings(path, text, test_file))
        findings.extend(efcore_startup_migration_findings(path, text, test_file))
        findings.extend(efcore_startup_schema_creation_findings(path, text, test_file))
        findings.extend(httpclient_base_address_findings(path, text, test_file))
        findings.extend(duplicate_standard_resilience_handler_findings(path, text, test_file))
        findings.extend(legacy_polly_http_policy_findings(path, text, test_file))
        findings.extend(semantic_kernel_chat_service_id_findings(path, text, test_file))
        findings.extend(semantic_kernel_plugin_cancellation_token_findings(path, text, test_file))
        findings.extend(hybrid_cache_expiration_findings(path, text, test_file))
        findings.extend(protobuf_grpcservices_findings(path, text, test_file))
        findings.extend(csharp_linq_findings(path, text, test_file))
        findings.extend(project_file_findings(path, text))
        findings.extend(openapi_package_findings(path, text))
        findings.extend(public_api_tracking_findings(path, text))
        findings.extend(apicompat_suppression_findings(path, text))
        findings.extend(nuget_packaging_findings(path, text))
        findings.extend(reflection_json_serialization_findings(path, text, test_file, aot_roots))
        findings.extend(json_serializer_context_partial_findings(path, text, test_file))
        findings.extend(logger_message_struct_findings(path, text, test_file))
        findings.extend(generated_regex_method_shape_findings(path, text, test_file))
        findings.extend(production_test_attribute_findings(path, text))
        findings.extend(csharp_concurrency_findings(path, text))
        findings.extend(csharp_static_scan_findings(path, text, test_file))
    blocking = [item for item in findings if SLOP_SEVERITY_RANK.get(str(item["severity"]), 0) >= fail_rank]
    ok = not blocking
    return {
        "name": "slop-scan",
        "kind": "analysis",
        "ok": ok,
        "status": status(ok),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "summary": {
            "files": len(files),
            "findings": len(findings),
            "blocking_findings": len(blocking),
            "fail_on": fail_on,
            "rules": dict(sorted(Counter(str(item["rule_id"]) for item in findings).items())),
            "severities": dict(sorted(Counter(str(item["severity"]) for item in findings).items())),
        },
        "format": "slop-patterns",
        "evidence_paths": [str(path) for path in files[:100]],
        "findings": findings[:100],
    }
